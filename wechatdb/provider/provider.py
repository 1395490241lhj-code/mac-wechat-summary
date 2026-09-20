"""The sharded provider: finished components, composed, and wired to nothing.

Not a registered source, not constructed by any product code, not imported by
product core. It is handed a locator and an identity resolver and searches for
nothing: the locator lists the parts, discovery classifies and probes them
through a read-only opener, the router plans the visit, the leaf parser reads
each conversation table, and one translation point turns the evidence into the
generic answer.

The order of evidence is load-bearing. Candidates are collected without
trimming, so a read that holds more than the caller asked for has *measured*
that and not inferred it. The collapse sees that untrimmed population, and the
count it states is already the public count. Only then is the public tuple cut
to the limit. Nothing repairs the coverage afterwards.

Each conversion has one owner. The router's table name goes to the parser
unchanged; the parser alone turns it into a conversation key; the canonical
identity is computed from that key and from nothing else, for a listed
conversation exactly as for its messages. Records are merged across parts and
never deduplicated by content, fingerprint or name.
"""

from __future__ import annotations

from dataclasses import dataclass

import wechatdb
from wechatdb.parser import MessageRecord

from .discovery import (
    ReadOnlySqliteOpener,
    ShardDiscovery,
    ShardEntry,
    ShardFacts,
    ShardLocator,
    shard_key,
)
from .identity import IdentityResolver
from .result import Contribution, ProviderResult
from .routing import STOP_EXHAUSTED, ShardRouter

# `.result` has already made the Reader boundary importable, in the flat style
# the repository uses for it; the two modules below are the whole crossing.
from conversation_identity import conversation_identifier
from message_source import (
    SOURCE_DATABASE,
    NormalizedConversation,
    NormalizedMessage,
    ReadResult,
)

#: The most records one conversation table of one part contributes to a read.
#: An internal bound of this source: a table holding more is cut, and says so.
_PART_RECORD_BOUND = 10_000


@dataclass(frozen=True, slots=True)
class _PartRead:
    """What one visited part yielded for one read, before any collapse."""

    records: tuple[MessageRecord, ...]
    observed_through: int | None
    truncated: bool
    complete_through: int | None


def _order(record: MessageRecord) -> tuple[int, int, str]:
    return (record.timestamp, record.local_id, record.session_id)


def _positive(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    return limit


class ShardedMessageProvider:
    """Reads a composite source through the finished provider components."""

    name: str = SOURCE_DATABASE

    def __init__(
        self,
        locator: ShardLocator,
        *,
        identities: IdentityResolver,
        source_newest: float | None = None,
    ) -> None:
        self._locator = locator
        # The resolver's one method shares its name with a path operation that
        # an architecture guard forbids every provider module from spelling as
        # an attribute. The guard is right and stays as it is; the method is
        # bound once, here, by name.
        self._names = getattr(identities, "resolve")
        self._source_newest = source_newest
        self._opener = ReadOnlySqliteOpener()
        self._router = ShardRouter()

    # -- the three reads -----------------------------------------------------

    def list_conversations(self, limit: int) -> ReadResult[NormalizedConversation]:
        limit = _positive(limit)
        reads = self._traverse(None, None, None, None)
        newest: dict[str, tuple[MessageRecord, str]] = {}
        for key, read in reads.items():
            for record in read.records:
                held = newest.get(record.session_id)
                if held is None or _order(record) > _order(held[0]):
                    newest[record.session_id] = (record, key)
        # One representative record per conversation, credited to the part
        # that holds it, so the collapse counts conversations.
        contributions = self._contributions({
            key: _PartRead(
                records=tuple(sorted(
                    (rec for rec, owner in newest.values() if owner == key), key=_order)),
                observed_through=read.observed_through,
                truncated=read.truncated,
                complete_through=read.complete_through)
            for key, read in reads.items()})
        coverage = self._collapse(contributions, None, None, limit)
        ranked = sorted((rec for rec, _ in newest.values()), key=_order, reverse=True)
        items = tuple(
            NormalizedConversation(
                id=conversation_identifier(record.session_id),
                title=record.session_id,
                first_seen_at=None,
                last_seen_at=record.timestamp,
                source=SOURCE_DATABASE)
            for record in ranked[:limit])
        return ReadResult(items=items, coverage=coverage)

    def get_messages(
        self,
        conversation_id: int,
        limit: int,
        before_sequence: int | None = None,
        *,
        requested_start: float | None = None,
        requested_end: float | None = None,
    ) -> ReadResult[NormalizedMessage]:
        limit = _positive(limit)
        reads = self._traverse(requested_start, requested_end, conversation_id,
                               before_sequence)
        return self._answer(reads, requested_start, requested_end, limit)

    def get_recent_messages(
        self, since_observed_at: float, limit: int
    ) -> ReadResult[NormalizedMessage]:
        limit = _positive(limit)
        reads = self._traverse(since_observed_at, None, None, None)
        return self._answer(reads, since_observed_at, None, limit)

    # -- orchestration -------------------------------------------------------

    def _traverse(
        self,
        start: float | None,
        end: float | None,
        conversation_id: int | None,
        before_sequence: int | None,
    ) -> dict[str, _PartRead]:
        """Discover, plan, and read every planned part, in the plan's order."""
        discovery = ShardDiscovery(self._locator, self._opener)
        inventory = discovery.probe(discovery.catalogue())
        entries = {shard_key(entry.name): entry for entry in self._locator.entries()}
        plan = self._router.plan(inventory, requested_start=start, requested_end=end)
        reads: dict[str, _PartRead] = {}
        for key in plan.visit:
            reads[key] = self._read_part(entries[key], inventory[key], start, end,
                                         conversation_id, before_sequence)
        return reads

    def _read_part(
        self,
        entry: ShardEntry,
        facts: ShardFacts,
        start: float | None,
        end: float | None,
        conversation_id: int | None,
        before_sequence: int | None,
    ) -> _PartRead:
        session_names = self._names().session_names
        kept: list[MessageRecord] = []
        observed: list[int] = []
        points: list[int | None] = []
        connection = self._opener.open(entry)
        try:
            name2id = wechatdb.load_name2id(connection)
            for table in facts.tables:
                # The table name goes to the parser unchanged; the parser alone
                # turns it into the conversation key every record then carries.
                records = list(wechatdb.parse_conversation(
                    connection, table, name2id=name2id, session_names=session_names))
                if not records:
                    continue
                session_id = records[0].session_id
                if (conversation_id is not None
                        and conversation_identifier(session_id) != conversation_id):
                    continue
                resolved = self._names(room=session_id)
                if resolved.display_names:
                    records = list(wechatdb.parse_conversation(
                        connection, table, name2id=name2id,
                        session_names=resolved.session_names,
                        display_names=resolved.display_names))
                records = [r for r in records
                           if (start is None or r.timestamp >= start)
                           and (end is None or r.timestamp <= end)
                           and (before_sequence is None or r.local_id < before_sequence)]
                observed.extend(r.timestamp for r in records)
                if len(records) > _PART_RECORD_BOUND:
                    # Oldest first, as the parser yields: what is kept is
                    # accountable only up to the last moment wholly kept.
                    cut_at = records[_PART_RECORD_BOUND].timestamp
                    records = records[:_PART_RECORD_BOUND]
                    points.append(max((r.timestamp for r in records
                                       if r.timestamp < cut_at), default=None))
                kept.extend(records)
        finally:
            connection.close()
        kept.sort(key=_order)
        return _PartRead(
            records=tuple(kept),
            observed_through=max(observed, default=None),
            truncated=bool(points),
            complete_through=(None if not points or None in points else min(points)))

    def _contributions(self, reads: dict[str, _PartRead]) -> tuple[Contribution, ...]:
        built = []
        for read in reads.values():
            complete = read.complete_through if read.truncated else read.observed_through
            built.append(Contribution(
                records=read.records, observed_through=read.observed_through,
                complete_through=complete, truncated=read.truncated))
        return tuple(built)

    def _collapse(self, contributions, start, end, limit):
        return ProviderResult.collapse(
            contributions, requested_start=start, requested_end=end,
            caller_limit=limit, stop=STOP_EXHAUSTED, inventory_gap=False,
            source_newest=None)

    def _answer(self, reads, start, end, limit) -> ReadResult[NormalizedMessage]:
        merged = sorted((r for read in reads.values() for r in read.records), key=_order)
        coverage = self._collapse(self._contributions(reads), start, end, limit)
        items = tuple(ProviderResult.message(record) for record in merged[-limit:])
        return ReadResult(items=items, coverage=coverage)
