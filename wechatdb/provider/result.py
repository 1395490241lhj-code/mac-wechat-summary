"""ProviderResult: the single point where provider evidence becomes generic.

Two projections live here and nothing else. A parser record becomes a
``NormalizedMessage``; provider-local read evidence becomes exactly one
``ReadCoverage``. This is the only provider module that reaches the generic
Reader layer, and it reaches exactly two of its modules: the canonical owner
of conversation identity, and the boundary types. It discovers nothing, routes
nothing, resolves no name, opens no database, trims no public tuple, builds no
result envelope, and deduplicates nothing.

**Conversation identity has one owner.** The generic identifier is
``conversation_identifier(record.session_id)`` and is never supplied from
outside, never computed from a table name, a bare digest or a display name,
and never hashed here. The parser authored ``session_id`` -- the supplied
username, or its own stable fallback -- so every step of the chain from table
name to generic identifier has exactly one owner.

**Ownership is unknown.** The provider holds no source-authored evidence of
which sender is the account itself, and infers none from a name, an
identifier's shape, room membership or conversation identity.

**The sentinel is evidence, never a public count.** A traversal collects one
record beyond the caller's limit so truncation is measured; the public tuple
is trimmed afterwards, by the orchestrator. So the caller cut is measured
strictly from more candidates than the limit, while ``item_count`` is already
the number the eventual public tuple will hold.

**One reason, by fixed precedence.** Unsafe stop, then a provider-internal
cut, then the measured caller cut, then a required inventory gap, then a source
moment newer than the read inside the requested upper bound, otherwise
complete. Freshness is computed independently of that choice, from the two
moments the source supplied and from nothing else: no clock, no tolerance, no
age. A safe stop without a measured sentinel is contradictory evidence and is
refused before any reason is chosen.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

try:
    from conversation_identity import conversation_identifier
    from message_source import (
        COVERAGE_COMPLETE,
        COVERAGE_PARTIAL,
        REASON_CALLER_LIMIT,
        REASON_EMPTY_WINDOW,
        REASON_FULL_WINDOW_OBSERVED,
        REASON_PARTIAL_INVENTORY,
        REASON_SOURCE_LIMIT,
        REASON_TIMESTAMP_MISMATCH,
        REASON_UNSAFE_EARLY_STOP,
        SOURCE_DATABASE,
        NormalizedMessage,
        ReadCoverage,
        ReadFreshness,
    )
except ImportError:  # pragma: no cover - imported from another cwd
    # The Reader boundary lives in the bridge tree as flat modules and is
    # imported, never copied -- the same crossing the memory layer makes.
    import os
    import sys

    sys.path.insert(
        0,
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "bridge",
        ),
    )
    from conversation_identity import conversation_identifier
    from message_source import (
        COVERAGE_COMPLETE,
        COVERAGE_PARTIAL,
        REASON_CALLER_LIMIT,
        REASON_EMPTY_WINDOW,
        REASON_FULL_WINDOW_OBSERVED,
        REASON_PARTIAL_INVENTORY,
        REASON_SOURCE_LIMIT,
        REASON_TIMESTAMP_MISMATCH,
        REASON_UNSAFE_EARLY_STOP,
        SOURCE_DATABASE,
        NormalizedMessage,
        ReadCoverage,
        ReadFreshness,
    )

from ..parser import MessageRecord
from .message_identity import message_id, message_sequence
from .routing import STOP_KINDS, STOP_SAFE, STOP_UNSAFE


@dataclass(frozen=True, slots=True)
class Contribution:
    """What one planned part contributed to one read. Integer provider evidence.

    ``truncated`` means this part was cut short by a provider-internal bound.
    A complete point needs an observed point and may not exceed it. The moments
    stay whole seconds; nothing casts them because the generic contract happens
    to accept more.
    """

    records: tuple[MessageRecord, ...]
    observed_through: int | None
    complete_through: int | None
    truncated: bool

    def __post_init__(self) -> None:
        if not isinstance(self.records, tuple):
            raise ValueError("contribution records must be a tuple")
        if not isinstance(self.truncated, bool):
            raise ValueError("contribution truncation must be a boolean")
        if self.complete_through is not None:
            if self.observed_through is None:
                raise ValueError("a complete point requires an observed point")
            if self.complete_through > self.observed_through:
                raise ValueError("a complete point cannot exceed the observed point")


@dataclass(frozen=True, slots=True)
class ProviderDiagnostics:
    """Aggregate counts, and nothing that could identify anything.

    ``unresolved_identities`` counts ambiguity events met during this read, not
    distinct identifiers: identity resolution is per call, a read that resolves
    several rooms sums the per-call counts, and one identifier ambiguous in two
    rooms therefore contributes two. No identifier is kept to deduplicate the
    number. Never part of a result envelope, and never an input to the collapse.
    """

    readable: int
    unknown: int
    unavailable: int
    unresolved_identities: int

    def __post_init__(self) -> None:
        for value in (self.readable, self.unknown, self.unavailable,
                      self.unresolved_identities):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("a diagnostic count must be a non-negative integer")


class ProviderResult:
    """The two projections. Static, stateless, and the provider's last word."""

    @staticmethod
    def message(record: MessageRecord) -> NormalizedMessage:
        """One parser record in the shape the Reader contract documents.

        The record alone: there is no way to supply a conversation identifier,
        because the canonical owner derives it from the record's own key.
        """
        return NormalizedMessage(
            id=message_id(record),
            conversation_id=conversation_identifier(record.session_id),
            sequence=message_sequence(record),
            # The parser already fell back to the sender identifier when no
            # display name was mapped.
            sender=record.sender_name,
            ownership="unknown",
            # No rendered time was ever seen on this path.
            visible_time=None,
            text=record.content,
            kind=record.message_type,
            # A decoded row is exact; there is no estimator here.
            confidence=1.0,
            first_observed_at=record.timestamp,
            source=SOURCE_DATABASE,
        )

    @staticmethod
    def collapse(
        contributions: Sequence[Contribution],
        *,
        requested_start: float | None,
        requested_end: float | None,
        caller_limit: int,
        stop: str,
        inventory_gap: bool,
        source_newest: float | None,
    ) -> ReadCoverage:
        """Pessimistically collapse one read's evidence into one coverage.

        The requested bounds and the source's newest moment pass through
        exactly as given. Nothing is trimmed, counted twice or deduplicated.
        """
        if (isinstance(caller_limit, bool) or not isinstance(caller_limit, int)
                or caller_limit <= 0):
            raise ValueError("caller limit must be a positive integer")
        if stop not in STOP_KINDS:
            raise ValueError("stop is not in the closed vocabulary")
        if not isinstance(inventory_gap, bool):
            raise ValueError("inventory gap must be a boolean")

        parts = tuple(contributions)
        candidate_count = sum(len(part.records) for part in parts)
        caller_limit_hit = candidate_count > caller_limit      # strictly; equality proves nothing
        source_limit_hit = any(part.truncated for part in parts)
        public_item_count = min(candidate_count, caller_limit)

        if stop == STOP_SAFE and not caller_limit_hit:
            raise ValueError("a safe stop requires measured caller truncation")

        unsafe = stop == STOP_UNSAFE
        raw_observed = max(
            (part.observed_through for part in parts if part.observed_through is not None),
            default=None,
        )
        weakest_complete = min(
            (part.complete_through for part in parts if part.complete_through is not None),
            default=None,
        )
        observed_through = None if unsafe else raw_observed
        # A required part that is missing, or a remainder that was skipped
        # unproven, leaves no gap-free point to claim -- whichever reason wins.
        partial_complete = None if (unsafe or inventory_gap) else weakest_complete

        if source_newest is None or observed_through is None:
            freshness = ReadFreshness.UNKNOWN
        elif source_newest <= observed_through:
            freshness = ReadFreshness.EVIDENCE_CONSISTENT
        else:
            freshness = ReadFreshness.POTENTIALLY_STALE
        mismatch_in_window = freshness is ReadFreshness.POTENTIALLY_STALE and (
            requested_end is None or source_newest <= requested_end)

        if unsafe:
            status, reason, truncated = COVERAGE_PARTIAL, REASON_UNSAFE_EARLY_STOP, True
        elif source_limit_hit:
            status, reason, truncated = COVERAGE_PARTIAL, REASON_SOURCE_LIMIT, True
        elif caller_limit_hit:
            status, reason, truncated = COVERAGE_PARTIAL, REASON_CALLER_LIMIT, True
        elif inventory_gap:
            status, reason, truncated = COVERAGE_PARTIAL, REASON_PARTIAL_INVENTORY, False
        elif mismatch_in_window:
            status, reason, truncated = COVERAGE_PARTIAL, REASON_TIMESTAMP_MISMATCH, False
        else:
            status, truncated = COVERAGE_COMPLETE, False
            reason = REASON_EMPTY_WINDOW if public_item_count == 0 else REASON_FULL_WINDOW_OBSERVED

        return ReadCoverage(
            status=status,
            reason=reason,
            requested_start=requested_start,
            requested_end=requested_end,
            observed_through=observed_through,
            # A complete read's two points always coincide.
            complete_through=observed_through if status == COVERAGE_COMPLETE else partial_complete,
            freshness=freshness,
            truncated=truncated,
            item_count=public_item_count,
        )
