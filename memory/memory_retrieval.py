"""Deterministic local retrieval over the memory store.

This is the internal API for M1. It is not exposed as an MCP tool and nothing
in ``bridge/`` or ``shadow/`` imports it: the public memory surface is an M2
decision, and shipping one here would settle that question by accident.

Everything in this module is local, deterministic and lexical. The same store
and the same query return the same rows in the same order, on any machine, with
no network call, no model, and no embedding. Semantic retrieval is M3 and is
deliberately absent.

Two rules make a result trustworthy.

**Every hit cites its source message.** A hit carries the canonical id, the
source, and the source's own identifier, so anything built on top -- a summary,
a memory, a digest line -- can point at the exact message it came from. A
result that cannot be traced back is not a retrieval result; it is a claim.

**Every result carries a coverage verdict.** An empty ``hits`` list is not an
answer on its own. It means "no stored message matched", which is only the same
as "no such message exists" when coverage says the window was actually
observed. The verdict travels with the result so a caller cannot use one
without the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from memory_store import (
    COVERAGE_COMPLETE,
    ComposedCoverage,
    CoverageVerdict,
    MemoryStore,
    MemoryStoreError,
    match_expression,
)

#: How results are ordered. ``relevance`` requires a text query; the other two
#: are the ones that make sense for "what happened recently".
Order = Literal["relevance", "recent", "oldest"]

MAX_LIMIT: int = 500
DEFAULT_LIMIT: int = 50


@dataclass(frozen=True)
class MemoryQuery:
    """One retrieval question.

    Every field is optional except the limit, and an all-empty query is a
    legitimate "what do you have" over the whole store. There is no field here
    that accepts SQL, a filter expression, or a path -- the same rule the MCP
    bridge follows, applied one layer down so it still holds if this API is
    ever exposed.
    """

    text: str | None = None
    conversation_canonical_id: str | None = None
    source: str | None = None
    sender: str | None = None
    ownership: str | None = None
    start: float | None = None
    end: float | None = None
    limit: int = DEFAULT_LIMIT
    order: Order = "recent"


@dataclass(frozen=True)
class MemoryHit:
    """One remembered message, with the reference that makes it citable."""

    canonical_id: str
    conversation_canonical_id: str
    source: str
    source_message_id: str | None
    identity_mode: str
    sender: str | None
    ownership: str
    timestamp: float
    timestamp_kind: str
    kind: str
    text: str | None
    sequence: int | None
    confidence: float | None
    first_ingested_at: float
    last_observed_at: float
    observation_count: int
    rank: float | None = None
    #: The logical object this observation was explicitly linked to, or
    #: ``None`` -- which means "equivalence unknown", not "unique". Kept off
    #: ``citation()``: a citation points at the observation, and the
    #: observation is what was actually read.
    logical_message_id: str | None = None

    def citation(self) -> dict[str, Any]:
        """The minimum needed to point back at the original message.

        Identifiers and provenance only -- no text. A caller that wants to
        quote the message has it in ``text``; a caller that wants to say where
        something came from uses this.
        """
        return {
            "canonical_id": self.canonical_id,
            "conversation_canonical_id": self.conversation_canonical_id,
            "source": self.source,
            "source_message_id": self.source_message_id,
            "identity_mode": self.identity_mode,
            "timestamp": self.timestamp,
            "timestamp_kind": self.timestamp_kind,
        }


@dataclass(frozen=True)
class MemoryResult:
    """Hits plus what the store is entitled to claim about them."""

    hits: tuple[MemoryHit, ...]
    #: The aggregate verdict. When the query named no source this is composed
    #: from every source the store knows, and ``source`` is then ``None``.
    coverage: CoverageVerdict
    truncated: bool = False
    query: MemoryQuery | None = field(default=None)
    #: The per-source evidence behind ``coverage``. Never collapsed: a source
    #: that covered the window completely is listed as such here even when the
    #: aggregate is partial because another source was not.
    coverage_by_source: ComposedCoverage | None = field(default=None)

    @property
    def is_empty_and_trustworthy(self) -> bool:
        """True only when "nothing matched" also means "nothing exists".

        The one question a caller must ask before writing "no messages". Any
        coverage state other than complete makes an empty result a statement
        about *us*, not about the conversation -- and when several sources
        were consulted, every one of them must have covered the window.
        """
        if self.hits:
            return False
        if self.coverage_by_source is not None:
            return self.coverage_by_source.trustworthy_empty_possible
        return self.coverage.status == COVERAGE_COMPLETE


class MemoryRetriever:
    """Reads the memory store. Never writes to it."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def search(self, query: MemoryQuery) -> MemoryResult:
        limit = max(1, min(int(query.limit), MAX_LIMIT))
        if query.order == "relevance" and not (query.text or "").strip():
            raise MemoryStoreError(
                "order_requires_text",
                "Relevance ordering needs a text query to rank against.",
            )
        clauses: list[str] = []
        parameters: list[Any] = []
        selection = "SELECT m.*, NULL AS rank FROM messages m"
        if (query.text or "").strip():
            selection = (
                "SELECT m.*, bm25(messages_fts) AS rank FROM messages_fts"
                " JOIN messages_fts_map map ON map.rowid = messages_fts.rowid"
                " JOIN messages m ON m.canonical_id = map.canonical_id"
            )
            clauses.append("messages_fts MATCH ?")
            parameters.append(match_expression(query.text or ""))
        if query.conversation_canonical_id is not None:
            clauses.append("m.conversation_canonical_id = ?")
            parameters.append(query.conversation_canonical_id)
        if query.source is not None:
            clauses.append("m.source = ?")
            parameters.append(query.source)
        if query.sender is not None:
            clauses.append("m.sender = ?")
            parameters.append(query.sender)
        if query.ownership is not None:
            clauses.append("m.ownership = ?")
            parameters.append(query.ownership)
        if query.start is not None:
            clauses.append("m.timestamp >= ?")
            parameters.append(float(query.start))
        if query.end is not None:
            clauses.append("m.timestamp <= ?")
            parameters.append(float(query.end))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        if query.order == "relevance":
            ordering = " ORDER BY rank ASC, m.timestamp DESC, m.canonical_id ASC"
        elif query.order == "oldest":
            ordering = " ORDER BY m.timestamp ASC, m.canonical_id ASC"
        else:
            ordering = " ORDER BY m.timestamp DESC, m.canonical_id ASC"
        # One row over the limit, so "there is more" is measured rather than
        # assumed from a full page.
        rows = self._store.connection.execute(
            f"{selection}{where}{ordering} LIMIT ?;", (*parameters, limit + 1)
        ).fetchall()
        truncated = len(rows) > limit
        hits = tuple(_hit(row) for row in rows[:limit])
        composed = self._store.assess_coverage_composed(
            source=query.source,
            conversation_canonical_id=query.conversation_canonical_id,
            start=query.start,
            end=query.end,
        )
        return MemoryResult(
            hits=hits,
            coverage=composed.as_verdict(),
            truncated=truncated,
            query=query,
            coverage_by_source=composed,
        )

    def conversation_messages(
        self,
        conversation_canonical_id: str,
        *,
        limit: int = DEFAULT_LIMIT,
        start: float | None = None,
        end: float | None = None,
    ) -> MemoryResult:
        """One conversation in stored time order, oldest first."""
        return self.search(
            MemoryQuery(
                conversation_canonical_id=conversation_canonical_id,
                start=start,
                end=end,
                limit=limit,
                order="oldest",
            )
        )

    def recent(
        self, *, since: float | None = None, limit: int = DEFAULT_LIMIT,
        source: str | None = None,
    ) -> MemoryResult:
        """The most recent messages, newest first."""
        return self.search(
            MemoryQuery(start=since, source=source, limit=limit, order="recent")
        )

    def conversations(self, *, limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
        """Known conversations, most recently observed first."""
        rows = self._store.connection.execute(
            "SELECT canonical_id, source, source_conversation_id, display_name,"
            " kind, first_seen_at, last_seen_at, first_ingested_at, last_observed_at"
            " FROM conversations"
            " ORDER BY COALESCE(last_seen_at, last_observed_at) DESC, canonical_id ASC"
            " LIMIT ?;",
            (max(1, min(int(limit), MAX_LIMIT)),),
        ).fetchall()
        return [dict(row) for row in rows]


def _hit(row: Any) -> MemoryHit:
    return MemoryHit(
        canonical_id=row["canonical_id"],
        conversation_canonical_id=row["conversation_canonical_id"],
        source=row["source"],
        source_message_id=row["source_message_id"],
        identity_mode=row["identity_mode"],
        sender=row["sender"],
        ownership=row["ownership"],
        timestamp=row["timestamp"],
        timestamp_kind=row["timestamp_kind"],
        kind=row["kind"],
        text=row["text"],
        sequence=row["sequence"],
        confidence=row["confidence"],
        first_ingested_at=row["first_ingested_at"],
        last_observed_at=row["last_observed_at"],
        observation_count=row["observation_count"],
        rank=row["rank"],
        logical_message_id=row["logical_message_id"],
    )


__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "MemoryHit",
    "MemoryQuery",
    "MemoryResult",
    "MemoryRetriever",
]
