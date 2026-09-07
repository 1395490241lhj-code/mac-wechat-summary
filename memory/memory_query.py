"""M2 — the deterministic retrieval contract over the memory store.

Four questions, one envelope. ``search``, ``timeline``, ``context_around`` and
``recent_context`` all return a :class:`MemoryQueryResult`, and there is no
call in this module that returns items without it. That is the point of the
envelope: coverage, truncation and scope are not decorations on a list, they
are what makes the list mean anything, and a caller that drops them is making
claims the store did not make.

Everything here is local, deterministic and lexical -- the same store and the
same query give the same items in the same order on any machine, with no
network, no model and no embedding. This is the stable *internal* contract; a
later gate decides which of it, if any, becomes a public tool.

Source policy
-------------

A query is answered from one or more sources, and not every source counts the
same. A **required** source is one whose coverage the answer depends on; a
**supplemental** source is consulted and reported but cannot, by being
incomplete, take away what a required source established. The rules:

* An empty result is *trustworthy* -- may be read as "no such message" -- only
  when every required source covered the window completely.
* A supplemental source that is partial or unavailable is *reported*, always,
  and never hidden; it just does not veto.
* Partial sources are never merged into a complete one, and no source ever
  stands in for another. Composition is over verdicts, not over data.

The default policy, used when a caller gives none, is the conservative one:
**every source the store knows about is required**, and none is supplemental.
Naming a source in the query makes that source the single required one. To
treat a source as supplemental a caller must say so in a
:class:`SourcePolicy`; there is no way to reach that state by omission.

Logical messages
----------------

When several observations have been *explicitly* linked to one logical
message (M1.1), a query returns one item for the group, carrying every
observation and every citation. Unlinked observations stay separate items
however alike they look: there is no heuristic grouping here, and there will
not be. The representative observation for a group is chosen by a fixed rule
(see :func:`representative_of`) so the same store answers the same way twice.
"""

from __future__ import annotations

import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

from memory_freshness import MemoryFreshness, memory_freshness

from memory_store import (
    COVERAGE_COMPLETE,
    COVERAGE_NOT_OBSERVED,
    COVERAGE_PARTIAL,
    COVERAGE_UNAVAILABLE,
    TIME_SOURCE_CREATED,
    CoverageVerdict,
    MemoryStore,
    MemoryStoreError,
    compose_coverage,
    match_expression,
)

MAX_LIMIT: int = 500
DEFAULT_LIMIT: int = 50

Order = Literal["relevance", "recent", "oldest"]

#: The stable timeline ordering, as SQL. Time first, then the source's own
#: sequence, then the canonical id as a total tiebreak. A cursor compares on
#: exactly these three expressions, which is what makes paging honest: it can
#: only be built from an item this ordering already placed.
_TIMELINE_PARTS: tuple[str, ...] = ("m.timestamp", "COALESCE(m.sequence, 0)", "m.canonical_id")
_TIMELINE_KEY = ", ".join(_TIMELINE_PARTS)


# --- Citation ------------------------------------------------------------------


@dataclass(frozen=True)
class MessageCitation:
    """A stable pointer at one source observation. Never carries text.

    This is what a summary, a memory or a digest line cites. It identifies the
    observation that was actually read (``canonical_message_id``), the logical
    message it has been linked to if any (``logical_message_id``, ``None``
    meaning *unknown*, not *none*), and enough provenance to say what the
    timestamp means.
    """

    canonical_message_id: str
    canonical_conversation_id: str
    source: str
    identity_mode: str
    timestamp: float
    timestamp_kind: str
    logical_message_id: str | None = None
    source_message_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "canonical_message_id": self.canonical_message_id,
            "logical_message_id": self.logical_message_id,
            "canonical_conversation_id": self.canonical_conversation_id,
            "source": self.source,
            "source_message_id": self.source_message_id,
            "identity_mode": self.identity_mode,
            "timestamp": self.timestamp,
            "timestamp_kind": self.timestamp_kind,
        }


# --- Scope and policy ----------------------------------------------------------


@dataclass(frozen=True)
class SourcePolicy:
    """Which sources a query's answer depends on, and which merely inform it."""

    required: tuple[str, ...]
    supplemental: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        overlap = set(self.required) & set(self.supplemental)
        if overlap:
            raise MemoryStoreError(
                "source_policy_invalid",
                "A source cannot be both required and supplemental.",
            )


@dataclass(frozen=True)
class QueryScope:
    """What was asked, restated. Travels with every result.

    ``kind`` names the query; ``window`` is the time range the coverage verdict
    is about, ``None`` on either side meaning unbounded; ``policy`` is the
    resolved source policy, never the caller's omission.
    """

    kind: str
    policy: SourcePolicy
    conversation_canonical_id: str | None = None
    window: tuple[float | None, float | None] = (None, None)
    limit: int = DEFAULT_LIMIT
    order: str = "recent"
    text: str | None = None
    sender: str | None = None
    ownership: str | None = None
    anchor: str | None = None


# --- Coverage ------------------------------------------------------------------


@dataclass(frozen=True)
class CoverageReport:
    """Per-source verdicts and the aggregate they justify, under a policy.

    ``status`` is the aggregate over the *required* sources -- the most
    cautious reading of them. Supplemental verdicts are carried in
    ``per_source`` beside the required ones and summarised in ``caveats``;
    they inform the reader, they do not decide.
    """

    status: str
    per_source: dict[str, CoverageVerdict]
    required_sources: tuple[str, ...]
    supplemental_sources: tuple[str, ...]
    complete_sources: tuple[str, ...]
    caveats: tuple[str, ...] = ()

    @property
    def trustworthy_empty(self) -> bool:
        """May an empty item list be read as "no such message"?

        Only when there is at least one required source and every required
        source covered the window completely. Supplemental sources cannot
        make this true and cannot make it false.
        """
        return bool(self.required_sources) and all(
            self.per_source[name].status == COVERAGE_COMPLETE
            for name in self.required_sources
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "trustworthy_empty": self.trustworthy_empty,
            "required_sources": list(self.required_sources),
            "supplemental_sources": list(self.supplemental_sources),
            "complete_sources": list(self.complete_sources),
            "per_source": {
                name: {"status": v.status, "reasons": list(v.reasons)}
                for name, v in self.per_source.items()
            },
            "caveats": list(self.caveats),
        }


def report_coverage(
    per_source: dict[str, CoverageVerdict], policy: SourcePolicy
) -> CoverageReport:
    """Applies a source policy to per-source verdicts.

    The aggregate is composed over required sources only, by the M1.1 rule
    (nothing partial becomes complete). Every supplemental verdict that is not
    complete becomes a caveat, so an incomplete optional reader is visible
    without being allowed to erase a required reader's complete evidence.
    """
    # A policy source with no verdict is a source nobody assessed: not observed.
    per_source = {
        **{n: CoverageVerdict(status=COVERAGE_NOT_OBSERVED, source=n)
           for n in policy.required + policy.supplemental},
        **per_source,
    }
    required = compose_coverage({n: per_source[n] for n in policy.required})
    caveats: list[str] = []
    for name in policy.supplemental:
        verdict = per_source.get(name)
        if verdict.status != COVERAGE_COMPLETE:
            caveats.append(f"{name}:{verdict.status}")
            caveats.extend(f"{name}:{reason}" for reason in verdict.reasons)
    for name in policy.required:
        verdict = per_source.get(name)
        if verdict is not None and verdict.status != COVERAGE_COMPLETE:
            caveats.append(f"{name}:{verdict.status}")
            caveats.extend(f"{name}:{reason}" for reason in verdict.reasons)
    return CoverageReport(
        status=required.status,
        per_source=dict(per_source),
        required_sources=tuple(policy.required),
        supplemental_sources=tuple(policy.supplemental),
        complete_sources=tuple(
            n for n, v in per_source.items() if v.status == COVERAGE_COMPLETE
        ),
        caveats=tuple(dict.fromkeys(caveats)),
    )


# --- Items ---------------------------------------------------------------------


@dataclass(frozen=True)
class Observation:
    """One source observation, with its own citation. Text included."""

    citation: MessageCitation
    sender: str | None
    ownership: str
    kind: str
    text: str | None
    sequence: int | None
    confidence: float | None
    visible_time: str | None
    first_ingested_at: float
    last_observed_at: float
    observation_count: int

    @property
    def canonical_id(self) -> str:
        return self.citation.canonical_message_id


@dataclass(frozen=True)
class MemoryItem:
    """One result: a single observation, or one logical message's group.

    ``citation`` and the message fields are those of the representative
    observation. ``observations`` holds every contributing observation -- one
    for an unlinked message, several for a linked logical message -- each with
    its own citation, so nothing about where a fact came from is lost by
    grouping. ``logical_message_id`` is ``None`` for an unlinked item.
    """

    citation: MessageCitation
    sender: str | None
    ownership: str
    kind: str
    text: str | None
    timestamp: float
    observations: tuple[Observation, ...]
    logical_message_id: str | None = None
    rank: float | None = None
    is_focal: bool = False

    @property
    def canonical_id(self) -> str:
        return self.citation.canonical_message_id

    @property
    def citations(self) -> tuple[MessageCitation, ...]:
        return tuple(o.citation for o in self.observations)

    @property
    def cursor(self) -> "TimelineCursor":
        rep = self.observations[0]
        return TimelineCursor(
            timestamp=self.timestamp,
            sequence=rep.sequence if rep.sequence is not None else 0,
            canonical_id=rep.canonical_id,
        )


@dataclass(frozen=True)
class TimelineCursor:
    """A position in the timeline ordering, built only from an item.

    Carries the three keys the ordering sorts on. A bare canonical id would
    not do: ids are digests and their order means nothing about time.
    """

    timestamp: float
    sequence: int
    canonical_id: str


@dataclass(frozen=True)
class MemoryQueryResult:
    """The one envelope. Items are never returned without the rest."""

    items: tuple[MemoryItem, ...]
    coverage: CoverageReport
    truncated: bool
    query_scope: QueryScope
    #: When memory was last synced and through what point each source was
    #: observed (M2.2c). Required: a result without it cannot be built.
    freshness: MemoryFreshness
    #: For ``context_around``: the canonical id of the focal observation.
    focal_canonical_id: str | None = None

    @property
    def is_empty_and_trustworthy(self) -> bool:
        return not self.items and self.coverage.trustworthy_empty

    @property
    def citations(self) -> tuple[MessageCitation, ...]:
        return tuple(c for item in self.items for c in item.citations)


# --- Conversation discovery (M2.2b) --------------------------------------------
#
# The one question the message queries cannot answer: "which conversation is
# 「产品群」?". Discovery is deliberately dumb. A name matches a stored display
# name *exactly* after normalisation, or as a *substring* of it, and that is
# the whole rule: no fuzzy matching, no ranking by similarity, no model. Two
# conversations with the same display name are two candidates and stay two
# candidates; choosing between them is the caller's decision, never this
# layer's. Equivalence across sources is only ever what an explicit link says.

MATCH_EXACT: str = "exact"
MATCH_CONTAINS: str = "contains"
MATCH_ALL: str = "all"


def normalize_name(name: str | None) -> str:
    """NFC, trimmed, case-folded. Comparison only; stored names are untouched."""
    if name is None:
        return ""
    return unicodedata.normalize("NFC", name).strip().casefold()


@dataclass(frozen=True)
class ConversationObservation:
    """One reader's account of one conversation. Provenance, no messages."""

    canonical_conversation_id: str
    source: str
    source_conversation_id: str
    display_name: str | None
    kind: str | None
    first_seen_at: float | None
    last_seen_at: float | None
    last_observed_at: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "canonical_conversation_id": self.canonical_conversation_id,
            "source": self.source,
            "source_conversation_id": self.source_conversation_id,
            "display_name": self.display_name,
            "kind": self.kind,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
        }


@dataclass(frozen=True)
class ConversationItem:
    """One candidate: a single observation, or one linked logical conversation.

    ``canonical_conversation_id`` is the representative observation's id and is
    what a caller passes to ``search`` / ``timeline`` / ``recent_context``.
    Those queries are per observation, so a linked logical conversation lists
    every observation and a caller who wants all of them queries each.
    """

    canonical_conversation_id: str
    display_name: str | None
    kind: str | None
    match: str
    observations: tuple[ConversationObservation, ...]
    logical_conversation_id: str | None = None

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(o.source for o in self.observations))

    def as_dict(self) -> dict[str, Any]:
        return {
            "canonical_conversation_id": self.canonical_conversation_id,
            "logical_conversation_id": self.logical_conversation_id,
            "display_name": self.display_name,
            "kind": self.kind,
            "match": self.match,
            "sources": list(self.sources),
            "observations": [o.as_dict() for o in self.observations],
        }


@dataclass(frozen=True)
class ConversationCoverage:
    """What discovery can honestly claim about its own result.

    Discovery enumerates the *store*. It can say whether it returned every
    stored candidate (it did unless truncated); it can never say the store
    holds every conversation WeChat has, and it does not pretend to.
    """

    exhaustive_of_store: bool
    status: str = "store_inventory"
    note: str = (
        "Candidates are the conversations this memory store holds. Nothing is "
        "claimed about conversations the store has never observed."
    )

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "exhaustive_of_store": self.exhaustive_of_store,
                "note": self.note}


@dataclass(frozen=True)
class ConversationDiscoveryResult:
    """The discovery envelope: candidates, truncation, scope, honest coverage."""

    items: tuple[ConversationItem, ...]
    truncated: bool
    query_scope: QueryScope
    coverage: ConversationCoverage
    #: Store freshness. Says when memory last synced, never that the store
    #: knows every conversation the source has.
    freshness: MemoryFreshness

    @property
    def is_unique(self) -> bool:
        return len(self.items) == 1

    @property
    def is_ambiguous(self) -> bool:
        return len(self.items) > 1


def _match_kind(query: str, display_name: str | None) -> str | None:
    if not query:
        return MATCH_ALL
    stored = normalize_name(display_name)
    if not stored:
        return None
    if stored == query:
        return MATCH_EXACT
    if query in stored:
        return MATCH_CONTAINS
    return None


_MATCH_RANK = {MATCH_EXACT: 0, MATCH_CONTAINS: 1, MATCH_ALL: 2}


# --- Representative selection --------------------------------------------------


def representative_of(observations: Sequence[Observation]) -> Observation:
    """The observation that stands for a logical message. Fixed rule.

    1. an observation whose timestamp is the source's own creation time beats
       one whose timestamp is when a screen was first seen -- it is the
       stronger ordering fact;
    2. then higher confidence;
    3. then the lexically smallest canonical id, as a total tiebreak.

    No preference for a particular reader by name: the rule is about the
    quality of the observation, not its origin.
    """
    return sorted(
        observations,
        key=lambda o: (
            0 if o.citation.timestamp_kind == TIME_SOURCE_CREATED else 1,
            -(o.confidence if o.confidence is not None else 0.0),
            o.canonical_id,
        ),
    )[0]


# --- The service ---------------------------------------------------------------


class MemoryQueryService:
    """Reads the memory store. Never writes to it."""

    def __init__(self, store: MemoryStore, *, clock=time.time) -> None:
        self._store = store
        self._clock = clock

    def freshness(self) -> MemoryFreshness:
        """When memory was last updated, per source and aggregated."""
        return memory_freshness(self._store, generated_at=float(self._clock()))

    # -- policy resolution --------------------------------------------------

    def resolve_policy(
        self, policy: SourcePolicy | None, source: str | None
    ) -> SourcePolicy:
        """The most conservative policy consistent with what was asked."""
        if policy is not None:
            return policy
        if source is not None:
            return SourcePolicy(required=(source,))
        return SourcePolicy(required=tuple(self._store.coverage_sources()))

    def _coverage(
        self,
        policy: SourcePolicy,
        *,
        conversation_canonical_id: str | None,
        start: float | None,
        end: float | None,
    ) -> CoverageReport:
        names = tuple(dict.fromkeys(policy.required + policy.supplemental))
        per_source = {
            name: self._store.assess_coverage(
                source=name,
                conversation_canonical_id=conversation_canonical_id,
                start=start,
                end=end,
            )
            for name in names
        }
        return report_coverage(per_source, policy)

    # -- search --------------------------------------------------------------

    def search(
        self,
        *,
        text: str | None = None,
        conversation_canonical_id: str | None = None,
        source: str | None = None,
        sender: str | None = None,
        ownership: str | None = None,
        start: float | None = None,
        end: float | None = None,
        limit: int = DEFAULT_LIMIT,
        order: Order = "recent",
        policy: SourcePolicy | None = None,
    ) -> MemoryQueryResult:
        """Filtered, optionally full-text, retrieval. Same filters as M1."""
        limit = _clamp(limit)
        has_text = bool((text or "").strip())
        if order == "relevance" and not has_text:
            raise MemoryStoreError(
                "order_requires_text", "Relevance ordering needs a text query to rank against."
            )
        resolved = self.resolve_policy(policy, source)
        clauses: list[str] = []
        parameters: list[Any] = []
        if has_text:
            selection = (
                "SELECT m.*, bm25(messages_fts) AS rank FROM messages_fts"
                " JOIN messages_fts_map map ON map.rowid = messages_fts.rowid"
                " JOIN messages m ON m.canonical_id = map.canonical_id"
            )
            clauses.append("messages_fts MATCH ?")
            parameters.append(match_expression(text or ""))
        else:
            selection = "SELECT m.*, NULL AS rank FROM messages m"
        _add_filters(
            clauses, parameters,
            conversation_canonical_id=conversation_canonical_id, source=source,
            sender=sender, ownership=ownership, start=start, end=end,
        )
        if order == "relevance":
            ordering = f"rank ASC, {_TIMELINE_KEY}"
        elif order == "oldest":
            ordering = _TIMELINE_KEY
        else:
            ordering = _desc(_TIMELINE_KEY)
        rows = self._rows(selection, clauses, parameters, ordering, limit)
        items, truncated = self._items(rows, limit)
        scope = QueryScope(
            kind="search", policy=resolved,
            conversation_canonical_id=conversation_canonical_id,
            window=(start, end), limit=limit, order=order, text=text,
            sender=sender, ownership=ownership,
        )
        return MemoryQueryResult(
            items=items, truncated=truncated, query_scope=scope, freshness=self.freshness(),
            coverage=self._coverage(
                resolved, conversation_canonical_id=conversation_canonical_id,
                start=start, end=end,
            ),
        )

    # -- timeline ------------------------------------------------------------

    def timeline(
        self,
        conversation_canonical_id: str,
        *,
        start: float | None = None,
        end: float | None = None,
        after: TimelineCursor | None = None,
        before: TimelineCursor | None = None,
        limit: int = DEFAULT_LIMIT,
        source: str | None = None,
        policy: SourcePolicy | None = None,
    ) -> MemoryQueryResult:
        """One conversation in stable chronological order, independent of FTS.

        Ordering is ``(timestamp, sequence, canonical_id)`` ascending, and a
        page is bounded by cursors on exactly that key. ``after`` returns the
        items strictly later than the cursor, oldest first; ``before`` returns
        the items strictly earlier, still delivered oldest first (the newest
        ``limit`` of them), so the reader pages backwards without the order
        flipping underneath them.
        """
        limit = _clamp(limit)
        if after is not None and before is not None:
            raise MemoryStoreError(
                "cursor_conflict", "Pass either an after cursor or a before cursor, not both."
            )
        resolved = self.resolve_policy(policy, source)
        clauses: list[str] = []
        parameters: list[Any] = []
        _add_filters(
            clauses, parameters,
            conversation_canonical_id=conversation_canonical_id, source=source,
            start=start, end=end,
        )
        if after is not None:
            clauses.append(f"({_TIMELINE_KEY}) > (?, ?, ?)")
            parameters.extend((after.timestamp, after.sequence, after.canonical_id))
        if before is not None:
            clauses.append(f"({_TIMELINE_KEY}) < (?, ?, ?)")
            parameters.extend((before.timestamp, before.sequence, before.canonical_id))
        ordering = _desc(_TIMELINE_KEY) if before is not None else _TIMELINE_KEY
        rows = self._rows("SELECT m.*, NULL AS rank FROM messages m", clauses, parameters, ordering, limit)
        truncated = len(rows) > limit
        rows = rows[:limit]
        if before is not None:
            rows = list(reversed(rows))
        items, _ = self._items(rows, None)
        scope = QueryScope(
            kind="timeline", policy=resolved,
            conversation_canonical_id=conversation_canonical_id,
            window=(start, end), limit=limit, order="oldest",
            anchor=(after or before).canonical_id if (after or before) else None,
        )
        return MemoryQueryResult(
            items=items, truncated=truncated, query_scope=scope, freshness=self.freshness(),
            coverage=self._coverage(
                resolved, conversation_canonical_id=conversation_canonical_id,
                start=start, end=end,
            ),
        )

    # -- context around ------------------------------------------------------

    def context_around(
        self,
        canonical_message_id: str,
        *,
        before: int = 5,
        after: int = 5,
        policy: SourcePolicy | None = None,
    ) -> MemoryQueryResult:
        """The focal observation with its neighbours in the same conversation.

        Neighbours are the ``before`` observations immediately earlier and the
        ``after`` observations immediately later in timeline order, from the
        focal's own conversation observation (not across sources: a neighbour
        from another reader's view of "the same" chat would be a guess). The
        coverage window is the span actually returned, for that conversation,
        under the focal's source as the required source unless a policy says
        otherwise. An unknown id fails, it does not return an empty context.
        """
        focal = self._store.connection.execute(
            "SELECT m.*, NULL AS rank FROM messages m WHERE m.canonical_id = ?;",
            (canonical_message_id,),
        ).fetchone()
        if focal is None:
            raise MemoryStoreError(
                "message_unknown", "No observation with that canonical id exists."
            )
        before = max(0, min(int(before), MAX_LIMIT))
        after = max(0, min(int(after), MAX_LIMIT))
        conversation = focal["conversation_canonical_id"]
        key = (focal["timestamp"], focal["sequence"] if focal["sequence"] is not None else 0, focal["canonical_id"])
        base = "SELECT m.*, NULL AS rank FROM messages m"
        earlier = self._rows(
            base, ["m.conversation_canonical_id = ?", f"({_TIMELINE_KEY}) < (?, ?, ?)"],
            [conversation, *key], _desc(_TIMELINE_KEY), before,
        )
        later = self._rows(
            base, ["m.conversation_canonical_id = ?", f"({_TIMELINE_KEY}) > (?, ?, ?)"],
            [conversation, *key], _TIMELINE_KEY, after,
        )
        truncated = len(earlier) > before or len(later) > after
        rows = list(reversed(earlier[:before])) + [focal] + later[:after]
        items, _ = self._items(rows, None, focal_id=canonical_message_id)
        resolved = self.resolve_policy(policy, focal["source"])
        window = (rows[0]["timestamp"], rows[-1]["timestamp"])
        scope = QueryScope(
            kind="context", policy=resolved, conversation_canonical_id=conversation,
            window=window, limit=before + after + 1, order="oldest",
            anchor=canonical_message_id,
        )
        return MemoryQueryResult(
            items=items, truncated=truncated, query_scope=scope, freshness=self.freshness(),
            focal_canonical_id=canonical_message_id,
            coverage=self._coverage(
                resolved, conversation_canonical_id=conversation,
                start=window[0], end=window[1],
            ),
        )

    # -- recent context ------------------------------------------------------

    def recent_context(
        self,
        *,
        conversation_canonical_id: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = DEFAULT_LIMIT,
        order: Literal["recent", "oldest"] = "recent",
        source: str | None = None,
        policy: SourcePolicy | None = None,
    ) -> MemoryQueryResult:
        """The most recent messages, optionally in one conversation or window.

        ``recent`` (default) delivers newest first; ``oldest`` delivers the
        same newest-``limit`` set in chronological order, for callers that want
        to read a context top to bottom. Either way the *selection* is the
        newest messages in scope.
        """
        limit = _clamp(limit)
        resolved = self.resolve_policy(policy, source)
        clauses: list[str] = []
        parameters: list[Any] = []
        _add_filters(
            clauses, parameters,
            conversation_canonical_id=conversation_canonical_id, source=source,
            start=since, end=until,
        )
        rows = self._rows(
            "SELECT m.*, NULL AS rank FROM messages m", clauses, parameters,
            _desc(_TIMELINE_KEY), limit,
        )
        truncated = len(rows) > limit
        rows = rows[:limit]
        if order == "oldest":
            rows = list(reversed(rows))
        items, _ = self._items(rows, None)
        scope = QueryScope(
            kind="recent", policy=resolved,
            conversation_canonical_id=conversation_canonical_id,
            window=(since, until), limit=limit, order=order,
        )
        return MemoryQueryResult(
            items=items, truncated=truncated, query_scope=scope, freshness=self.freshness(),
            coverage=self._coverage(
                resolved, conversation_canonical_id=conversation_canonical_id,
                start=since, end=until,
            ),
        )

    # -- conversation discovery (M2.2b) -------------------------------------

    def conversations(
        self, *, name: str | None = None, limit: int = DEFAULT_LIMIT
    ) -> ConversationDiscoveryResult:
        """Candidates for a display name, or every conversation the store holds.

        Rule, exactly: after NFC + trim + casefold on both sides, a stored
        display name that *equals* the query is an ``exact`` match and one
        that *contains* it is a ``contains`` match; nothing else matches. With
        no name every conversation is returned as ``all``. Ordering is exact
        before contains, then most recently seen first, then canonical id.
        Observations explicitly linked to one logical conversation are grouped
        into one item; nothing else is grouped, however alike it looks.

        The conversations table is read whole and filtered here, because the
        normalisation cannot be expressed in SQL and the table is small by
        construction (one row per observed conversation).
        """
        limit = _clamp(limit)
        query = normalize_name(name)
        rows = self._store.connection.execute(
            "SELECT canonical_id, source, source_conversation_id, display_name, kind,"
            " first_seen_at, last_seen_at, last_observed_at, logical_conversation_id"
            " FROM conversations;"
        ).fetchall()
        groups: dict[str, list[Any]] = {}
        for row in rows:
            key = row["logical_conversation_id"] or row["canonical_id"]
            groups.setdefault(key, []).append(row)
        items: list[ConversationItem] = []
        for key, members in groups.items():
            observations = tuple(
                sorted(
                    (ConversationObservation(
                        canonical_conversation_id=r["canonical_id"], source=r["source"],
                        source_conversation_id=r["source_conversation_id"],
                        display_name=r["display_name"], kind=r["kind"],
                        first_seen_at=r["first_seen_at"], last_seen_at=r["last_seen_at"],
                        last_observed_at=r["last_observed_at"],
                    ) for r in members),
                    key=lambda o: (-(o.last_seen_at if o.last_seen_at is not None else o.last_observed_at),
                                   o.canonical_conversation_id),
                )
            )
            matches = [m for m in (_match_kind(query, o.display_name) for o in observations) if m]
            if not matches:
                continue
            best = min(matches, key=_MATCH_RANK.__getitem__)
            representative = observations[0]
            logical = members[0]["logical_conversation_id"]
            items.append(ConversationItem(
                canonical_conversation_id=representative.canonical_conversation_id,
                display_name=representative.display_name,
                kind=next((o.kind for o in observations if o.kind), None),
                match=best, observations=observations, logical_conversation_id=logical,
            ))
        items.sort(key=lambda i: (
            _MATCH_RANK[i.match],
            -max((o.last_seen_at if o.last_seen_at is not None else o.last_observed_at)
                 for o in i.observations),
            i.canonical_conversation_id,
        ))
        truncated = len(items) > limit
        scope = QueryScope(kind="conversations", policy=self.resolve_policy(None, None),
                           limit=limit, order="match,recent", text=name)
        return ConversationDiscoveryResult(
            items=tuple(items[:limit]), truncated=truncated, query_scope=scope,
            coverage=ConversationCoverage(exhaustive_of_store=not truncated),
            freshness=self.freshness(),
        )

    # -- plumbing ------------------------------------------------------------

    def _rows(self, selection: str, clauses: list[str], parameters: list[Any],
              ordering: str, limit: int) -> list[Any]:
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        # One past the limit, so truncation is measured, never assumed.
        return self._store.connection.execute(
            f"{selection}{where} ORDER BY {ordering} LIMIT ?;", (*parameters, limit + 1)
        ).fetchall()

    def _items(
        self, rows: list[Any], limit: int | None, *, focal_id: str | None = None
    ) -> tuple[tuple[MemoryItem, ...], bool]:
        """Rows to items, grouping only on an explicit logical link.

        Grouping happens *after* the page is cut, on the page's own rows, so a
        logical message whose observations straddle a page boundary appears
        on both pages with the observations each page saw. That is a visible
        seam, preferred to widening a page silently to close it.
        """
        truncated = limit is not None and len(rows) > limit
        if limit is not None:
            rows = rows[:limit]
        groups: dict[str, list[Any]] = {}
        order: list[str] = []
        for row in rows:
            key = row["logical_message_id"] or row["canonical_id"]
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(row)
        items: list[MemoryItem] = []
        for key in order:
            observations = tuple(_observation(r) for r in groups[key])
            rep = representative_of(observations)
            rep_row = next(r for r in groups[key] if r["canonical_id"] == rep.canonical_id)
            items.append(
                MemoryItem(
                    citation=rep.citation, sender=rep.sender, ownership=rep.ownership,
                    kind=rep.kind, text=rep.text, timestamp=rep.citation.timestamp,
                    observations=observations,
                    logical_message_id=rep_row["logical_message_id"],
                    rank=rep_row["rank"],
                    is_focal=any(r["canonical_id"] == focal_id for r in groups[key]),
                )
            )
        return tuple(items), truncated


def _observation(row: Any) -> Observation:
    return Observation(
        citation=MessageCitation(
            canonical_message_id=row["canonical_id"],
            logical_message_id=row["logical_message_id"],
            canonical_conversation_id=row["conversation_canonical_id"],
            source=row["source"],
            source_message_id=row["source_message_id"],
            identity_mode=row["identity_mode"],
            timestamp=row["timestamp"],
            timestamp_kind=row["timestamp_kind"],
        ),
        sender=row["sender"], ownership=row["ownership"], kind=row["kind"],
        text=row["text"], sequence=row["sequence"], confidence=row["confidence"],
        visible_time=row["visible_time"], first_ingested_at=row["first_ingested_at"],
        last_observed_at=row["last_observed_at"], observation_count=row["observation_count"],
    )


def _add_filters(
    clauses: list[str], parameters: list[Any], *,
    conversation_canonical_id: str | None = None, source: str | None = None,
    sender: str | None = None, ownership: str | None = None,
    start: float | None = None, end: float | None = None,
) -> None:
    """Fixed statements with bound parameters. No field accepts SQL."""
    for column, value in (
        ("m.conversation_canonical_id", conversation_canonical_id),
        ("m.source", source), ("m.sender", sender), ("m.ownership", ownership),
    ):
        if value is not None:
            clauses.append(f"{column} = ?")
            parameters.append(value)
    if start is not None:
        clauses.append("m.timestamp >= ?")
        parameters.append(float(start))
    if end is not None:
        clauses.append("m.timestamp <= ?")
        parameters.append(float(end))


def _desc(key: str) -> str:
    assert key == _TIMELINE_KEY
    return ", ".join(f"{part} DESC" for part in _TIMELINE_PARTS)


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), MAX_LIMIT))


__all__ = [
    "DEFAULT_LIMIT", "MAX_LIMIT", "MATCH_ALL", "MATCH_CONTAINS", "MATCH_EXACT",
    "ConversationCoverage", "ConversationDiscoveryResult", "ConversationItem",
    "ConversationObservation", "CoverageReport", "MemoryItem", "normalize_name",
    "MemoryQueryResult", "MemoryQueryService", "MessageCitation", "Observation",
    "QueryScope", "SourcePolicy", "TimelineCursor", "report_coverage",
    "representative_of",
]
