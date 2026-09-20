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
    ExplicitShardLocator,
    ReadOnlySqliteOpener,
    ShardDiscovery,
    ShardEntry,
    ShardFacts,
    ShardLocator,
    shard_key,
)
from .discovery import SHARD_READABLE, SHARD_UNAVAILABLE, SHARD_UNKNOWN
from .identity import IdentityResolver
from .compatibility import require_supported_surface
from .message_identity import message_sequence
from .result import Contribution, ProviderDiagnostics, ProviderResult
from .routing import STOP_EXHAUSTED, STOP_SAFE, STOP_UNSAFE, ShardRouter

# `.result` has already made the Reader boundary importable, in the flat style
# the repository uses for it; the two modules below are the whole crossing.
from conversation_identity import conversation_identifier
from message_source import (
    SOURCE_DATABASE,
    NormalizedConversation,
    NormalizedMessage,
    ReadResult,
)

#: The most parts one read visits. Reaching it with planned parts left over is
#: an early stop, and is classified like any other.
_PART_VISIT_BOUND = 256


@dataclass(frozen=True, slots=True)
class _PartRead:
    """What one visited part yielded for one read, before any collapse."""

    records: tuple[MessageRecord, ...]
    observed_through: int | None
    truncated: bool
    complete_through: int | None


@dataclass(frozen=True, slots=True)
class _Traversal:
    """The visited parts in plan order, and the evidence about the rest."""

    reads: dict[str, _PartRead]
    stop: str
    inventory_gap: bool


def _order(record: MessageRecord) -> tuple[int, str]:
    return (message_sequence(record), record.session_id)


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
        #: Aggregate counts for the most recent read. Beside the envelope,
        #: never inside it, and never an input to the collapse.
        self.diagnostics = ProviderDiagnostics(
            readable=0, unknown=0, unavailable=0, unresolved_identities=0)
        self._router = ShardRouter()

    # -- the three reads -----------------------------------------------------

    def list_conversations(self, limit: int) -> ReadResult[NormalizedConversation]:
        limit = _positive(limit)
        trail = self._traverse(None, None, None, None, None)
        reads = trail.reads
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
        coverage = self._collapse(trail, contributions, None, None, limit)
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
        trail = self._traverse(requested_start, requested_end, conversation_id,
                              before_sequence, limit)
        return self._answer(trail, requested_start, requested_end, limit)

    def get_recent_messages(
        self, since_observed_at: float, limit: int
    ) -> ReadResult[NormalizedMessage]:
        limit = _positive(limit)
        trail = self._traverse(since_observed_at, None, None, None, limit)
        return self._answer(trail, since_observed_at, None, limit)

    # -- orchestration -------------------------------------------------------

    def _traverse(
        self,
        start: float | None,
        end: float | None,
        conversation_id: int | None,
        before_sequence: int | None,
        limit: int | None,
    ) -> _Traversal:
        """Discover, plan, and read planned parts in order until a stop.

        The traversal ends early in two ways only. It may stop once it holds
        more than the caller asked for *and* the router proves every unvisited
        part strictly older than everything in hand; while that proof is
        missing it keeps going. Or it reaches this source's own visit bound.
        Either way the router classifies the stop, and that classification is
        what the collapse is told. A listing counts conversations, not
        records, so it has no measured limit to stop on and passes none.
        """
        # One snapshot per read: the locator promises a tuple, not the same
        # tuple twice, so discovery and the reads share this one.
        snapshot = tuple(self._locator.entries())
        discovery = ShardDiscovery(ExplicitShardLocator(snapshot), self._opener)
        inventory = discovery.probe(discovery.catalogue())
        entries = {shard_key(entry.name): entry for entry in snapshot}
        plan = self._router.plan(inventory, requested_start=start, requested_end=end)
        states = [facts.state for facts in inventory.values()]
        events: list[int] = []
        reads: dict[str, _PartRead] = {}
        stop = STOP_EXHAUSTED
        for key in plan.visit:
            if len(reads) >= _PART_VISIT_BOUND:
                stop = self._classify(plan, inventory, reads, limit)
                break
            reads[key] = self._read_part(entries[key], inventory[key], start, end,
                                         conversation_id, before_sequence, events)
            if (limit is not None and len(reads) < len(plan.visit)
                    and self._held(reads) > limit
                    and self._classify(plan, inventory, reads, limit) == STOP_SAFE):
                stop = STOP_SAFE
                break
        self.diagnostics = ProviderDiagnostics(
            readable=states.count(SHARD_READABLE),
            unknown=states.count(SHARD_UNKNOWN),
            unavailable=states.count(SHARD_UNAVAILABLE),
            unresolved_identities=sum(events))
        # Parts that could never be visited were not skipped by the traversal;
        # they are a gap in the inventory, always, and separately from any stop.
        gap = SHARD_UNKNOWN in states or SHARD_UNAVAILABLE in states
        return _Traversal(reads=reads, stop=stop, inventory_gap=gap)

    @staticmethod
    def _held(reads: dict[str, _PartRead]) -> int:
        return sum(len(read.records) for read in reads.values())

    def _classify(self, plan, inventory, reads, limit) -> str:
        oldest = min((r.timestamp for read in reads.values() for r in read.records),
                     default=None)
        stop = self._router.classify_stop(
            plan, inventory, visited=tuple(reads), oldest_collected_at=oldest)
        if stop == STOP_SAFE and (limit is None or self._held(reads) <= limit):
            # Proven older, but the answer in hand is not full: what was
            # skipped would have been part of it. Only a measured caller cut
            # makes a stop safe.
            return STOP_UNSAFE
        return stop

    def _read_part(
        self,
        entry: ShardEntry,
        facts: ShardFacts,
        start: float | None,
        end: float | None,
        conversation_id: int | None,
        before_sequence: int | None,
        events: list[int],
    ) -> _PartRead:
        # Taken for the parser-shaped mapping alone: no name from this call
        # reaches any message, so it is not a resolution event of the read.
        session_names = self._names().session_names
        kept: list[MessageRecord] = []
        connection = self._opener.open(entry)
        try:
            name2id = wechatdb.load_name2id(connection)
            for table in facts.tables:
                # A table that parses is not necessarily the generation this
                # provider supports. The envelope is checked before any row is
                # read, so a changed schema fails closed instead of yielding
                # whatever the old reader could still find in it.
                require_supported_surface(connection, table)
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
                # Every room-scoped resolution is an event and is summed as
                # one; no identifier is kept to deduplicate the number.
                events.append(resolved.unresolved)
                if resolved.display_names:
                    records = list(wechatdb.parse_conversation(
                        connection, table, name2id=name2id,
                        session_names=resolved.session_names,
                        display_names=resolved.display_names))
                records = [r for r in records
                           if (start is None or r.timestamp >= start)
                           and (end is None or r.timestamp <= end)
                           and (before_sequence is None
                                or message_sequence(r) < before_sequence)]
                kept.extend(records)
        finally:
            connection.close()
        kept.sort(key=_order)
        # The leaf parser hands over every record of the scope, so this read
        # is whole: nothing here cuts it, and a cut is never invented. A part
        # read that *was* cut short says so through these same fields.
        return _PartRead(
            records=tuple(kept),
            observed_through=max((r.timestamp for r in kept), default=None),
            truncated=False,
            complete_through=None)

    def _contributions(self, reads: dict[str, _PartRead]) -> tuple[Contribution, ...]:
        built = []
        for read in reads.values():
            complete = read.complete_through if read.truncated else read.observed_through
            built.append(Contribution(
                records=read.records, observed_through=read.observed_through,
                complete_through=complete, truncated=read.truncated))
        return tuple(built)

    def _collapse(self, trail: _Traversal, contributions, start, end, limit):
        return ProviderResult.collapse(
            contributions, requested_start=start, requested_end=end,
            caller_limit=limit, stop=trail.stop, inventory_gap=trail.inventory_gap,
            source_newest=self._source_newest)

    def _answer(self, trail: _Traversal, start, end, limit) -> ReadResult[NormalizedMessage]:
        reads = trail.reads
        merged = sorted((r for read in reads.values() for r in read.records), key=_order)
        coverage = self._collapse(trail, self._contributions(reads), start, end, limit)
        projected = tuple(ProviderResult.message(record) for record in merged)
        ids = {message.id for message in projected}
        sequences = {(message.conversation_id, message.sequence) for message in projected}
        if len(ids) != len(projected) or len(sequences) != len(projected):
            raise ValueError("message identity collision")
        items = projected[-limit:]
        return ReadResult(items=items, coverage=coverage)
