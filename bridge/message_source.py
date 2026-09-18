"""The reader-neutral boundary the MCP bridge serves its tools from.

The bridge historically read one thing: the SQLite store the macOS app fills
from the visual capture path. This module names that assumption instead of
hiding it, so a second reader can exist without the tools, the agent runner, or
the skill learning anything about it.

Two rules shape everything here.

**Nothing in this file is specific to any one reader.** No process, no JSON
dialect, no WeChat schema, no vendor name. A source is anything that can answer
four questions: are you ready, which conversations do you have, what is in one
conversation, and what is recent. An implementation lives in its own module.

**Provenance is carried, not projected.** :class:`NormalizedMessage` knows which
source produced it, but :meth:`NormalizedMessage.payload` deliberately omits
that field. The wire shape an agent sees is therefore byte-identical to the one
the bridge has always returned, while the process still knows where a row came
from. Provenance reaches a client on the response envelope instead, where it
describes the whole answer rather than each row. When the reader layer is ready
to be user-visible, that is a projection change here and nowhere else.

Coverage differences between sources are real and must stay visible. A source
that cannot answer a question raises :class:`MessageSourceError`; it never
substitutes a different answer, and no caller silently falls back to another
source. A quiet fallback would make a partial read look like a complete one,
which is the one failure this boundary exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

#: The visual capture path: frames extracted, reconciled, and stored by the
#: macOS app under the user's local-persistence consent.
SOURCE_VISUAL: str = "visual"

#: A local WeChat database read through an external reader implementation.
SOURCE_DATABASE: str = "database"

SOURCE_NAMES: frozenset[str] = frozenset({SOURCE_VISUAL, SOURCE_DATABASE})

# --- Coverage vocabulary ------------------------------------------------------
#
# What a source covered of what was asked of it is a statement only that source
# can make, so the words for it belong to the boundary every source speaks
# through rather than to any one layer above it. These four tokens have one
# owner here; a consumer imports them and never restates them, because two
# copies pinned by an equality test are still two copies on the day a fifth
# state is added to one of them. A string constant needs no import, so owning
# them costs this module nothing of its neutrality.

#: The source accounted for the whole requested window.
COVERAGE_COMPLETE: str = "observed_complete"

#: The source was read, and either the window was not covered in full or the
#: source cannot state that it was.
COVERAGE_PARTIAL: str = "observed_partial"

#: The source was asked and could not serve the requested scope.
COVERAGE_UNAVAILABLE: str = "unavailable"

#: The source has no observation for the requested scope. Distinct from a
#: complete read that found nothing.
COVERAGE_NOT_OBSERVED: str = "not_observed"

#: The whole vocabulary. A narrower subset may be all that a given consumer
#: will store or accept; that subset is that consumer's own statement to make.
COVERAGE_STATUSES: frozenset[str] = frozenset({
    COVERAGE_COMPLETE,
    COVERAGE_PARTIAL,
    COVERAGE_UNAVAILABLE,
    COVERAGE_NOT_OBSERVED,
})

# --- Reason vocabulary --------------------------------------------------------
#
# Why a source made the coverage claim it made, drawn from this closed set and
# from nothing else. A reason is never free text, never message content, never
# a sender, never a path, never a table or a shard, and never a string produced
# by a provider, a database or another process: it is a fixed token chosen from
# twelve, which is what makes it safe to return to a client and safe to log.
# Each one names something about the read, not about the data read.

#: Every part that could hold a matching item was read, and nothing was cut
#: short.
REASON_FULL_WINDOW_OBSERVED: str = "full_window_observed"

#: The window was fully accounted for and held no matching item -- the
#: trustworthy empty, as opposed to an empty nobody looked for.
REASON_EMPTY_WINDOW: str = "empty_window"

#: The caller's own ``limit`` cut the answer short.
REASON_CALLER_LIMIT: str = "caller_limit"

#: An internal bound of the source cut the answer short, independently of the
#: caller's limit. The case a caller cannot detect from the outside.
REASON_SOURCE_LIMIT: str = "source_limit"

#: The traversal stopped early, and everything not visited is provably outside
#: the requested window -- the safe early stop, which costs no completeness.
REASON_WINDOW_BOUND: str = "window_bound"

#: An upstream reader stated that more results exist for this request.
REASON_UPSTREAM_MORE: str = "upstream_more"

#: At least one part the request required was unknown or unreadable. Naming it
#: is the alternative to dropping it and still claiming completeness.
REASON_PARTIAL_INVENTORY: str = "partial_inventory"

#: The traversal stopped without being able to prove the remainder irrelevant.
REASON_UNSAFE_EARLY_STOP: str = "unsafe_early_stop"

#: The source names material newer than this read *inside* the requested
#: window. Outside it, that is a freshness statement and not a coverage one.
REASON_TIMESTAMP_MISMATCH: str = "timestamp_mismatch"

#: The scope is representable, and this source cannot serve it.
REASON_SCOPE_UNSUPPORTED: str = "scope_unsupported"

#: This read did not look at the requested scope.
REASON_SCOPE_NOT_READ: str = "scope_not_read"

#: The source holds no observation of the requested scope at all.
REASON_NO_OBSERVATION: str = "no_observation"

#: The whole vocabulary, closed. A reason outside this set is a defect, not a
#: new case.
COVERAGE_REASONS: frozenset[str] = frozenset({
    REASON_FULL_WINDOW_OBSERVED,
    REASON_EMPTY_WINDOW,
    REASON_CALLER_LIMIT,
    REASON_SOURCE_LIMIT,
    REASON_WINDOW_BOUND,
    REASON_UPSTREAM_MORE,
    REASON_PARTIAL_INVENTORY,
    REASON_UNSAFE_EARLY_STOP,
    REASON_TIMESTAMP_MISMATCH,
    REASON_SCOPE_UNSUPPORTED,
    REASON_SCOPE_NOT_READ,
    REASON_NO_OBSERVATION,
})

#: Which statuses each reason is a valid explanation for. Kept as data rather
#: than as prose so the pairing can be checked rather than remembered: every
#: reason explains at least one status and every status has at least one
#: reason, so the mapping is total in both directions.
REASON_STATUSES: dict[str, frozenset[str]] = {
    REASON_FULL_WINDOW_OBSERVED: frozenset({COVERAGE_COMPLETE}),
    REASON_EMPTY_WINDOW:         frozenset({COVERAGE_COMPLETE}),
    REASON_WINDOW_BOUND:         frozenset({COVERAGE_COMPLETE}),
    REASON_CALLER_LIMIT:         frozenset({COVERAGE_PARTIAL}),
    REASON_SOURCE_LIMIT:         frozenset({COVERAGE_PARTIAL}),
    REASON_UPSTREAM_MORE:        frozenset({COVERAGE_PARTIAL}),
    REASON_UNSAFE_EARLY_STOP:    frozenset({COVERAGE_PARTIAL}),
    REASON_TIMESTAMP_MISMATCH:   frozenset({COVERAGE_PARTIAL}),
    REASON_PARTIAL_INVENTORY:    frozenset({COVERAGE_PARTIAL,
                                            COVERAGE_UNAVAILABLE}),
    REASON_SCOPE_UNSUPPORTED:    frozenset({COVERAGE_UNAVAILABLE}),
    REASON_SCOPE_NOT_READ:       frozenset({COVERAGE_NOT_OBSERVED}),
    REASON_NO_OBSERVATION:       frozenset({COVERAGE_NOT_OBSERVED}),
}

# --- Freshness ----------------------------------------------------------------
#
# Freshness is orthogonal to coverage, and deliberately so: a source can cover
# the whole window it was asked about and still know that its own records name
# something newer than anything it returned. Coverage answers "how much of what
# I asked for did you account for"; freshness answers "did what you hold move
# under you". Collapsing the two loses the case that matters.


class ReadFreshness(str, Enum):
    """Whether the source's own records outran what this read returned.

    A ``str`` enum so a payload carries the same token a human reads, with no
    translation table and no second spelling.

    There is no ``fresh`` boolean, no age threshold, no "recent enough" test
    and no clock anywhere in this. A boolean would have to be computed against
    something, and the only something available is the current time -- which
    would make this module assert an age policy it has no standing to hold.
    The only comparison is between two moments the source itself supplied.
    """

    #: The read reached everything the source itself claims to hold for the
    #: requested scope.
    EVIDENCE_CONSISTENT = "evidence_consistent"

    #: The source's own records name something newer than the newest item read.
    #: A statement about two moments, not a verdict about age.
    POTENTIALLY_STALE = "potentially_stale"

    #: No comparison was made, because one of the two moments is absent.
    #: Not a synonym for consistent.
    UNKNOWN = "unknown"


# --- The coverage claim -------------------------------------------------------

#: The only reasons that mean the read was cut short. Truncation paired with
#: anything else is incoherent rather than merely unusual.
_TRUNCATING_REASONS: frozenset[str] = frozenset({
    REASON_CALLER_LIMIT,
    REASON_SOURCE_LIMIT,
    REASON_UPSTREAM_MORE,
    REASON_UNSAFE_EARLY_STOP,
})

#: The statuses under which nothing was served, and which therefore carry no
#: evidence about what a read reached.
_SERVED_NOTHING: frozenset[str] = frozenset({
    COVERAGE_UNAVAILABLE,
    COVERAGE_NOT_OBSERVED,
})


@dataclass(frozen=True, slots=True)
class ReadCoverage:
    """What one read is entitled to claim about the window it was asked for.

    Structure (``status``), cause (``reason``), truncation and currency are
    four different facts on four different fields. Collapsing them into one
    verdict is what produces "it said complete and it wasn't".

    Every rule below is enforced at construction, so a source that hit a
    cut-short condition **cannot build** a complete coverage. The downgrade is
    not a rule someone remembers to apply; it is a rule the object must satisfy
    in order to exist. A violation is a defect in a source, never a runtime
    condition a caller handles, so it raises :class:`ValueError` and is never
    caught to produce a softer answer.

    Refusal messages are fixed lowercase tokens. None is assembled from the
    value it rejected: a message built from a field is how chat content, a
    filesystem path or another program's error text reaches a log through a
    validation error.
    """

    #: One of the four ``COVERAGE_*`` tokens.
    status: str

    #: One token from the closed reason set. Always present: a claim without an
    #: account of itself is what this design removes.
    reason: str

    #: Inclusive start of the requested window, Unix seconds. ``None`` is
    #: unbounded.
    requested_start: int | None

    #: Inclusive end of the requested window, Unix seconds. ``None`` is
    #: unbounded.
    requested_end: int | None

    #: Newest moment this read is known to have looked at.
    observed_through: int | None

    #: Newest moment up to which the source can account for the window with no
    #: gap.
    complete_through: int | None

    #: Orthogonal to ``status``: a read can cover its whole window and still
    #: know the source moved underneath it.
    freshness: ReadFreshness

    #: Whether the answer was cut short rather than exhausted.
    truncated: bool

    #: Number of items in the accompanying result.
    item_count: int

    def __post_init__(self) -> None:
        if self.status not in COVERAGE_STATUSES:
            raise ValueError("coverage status is not in the closed vocabulary")

        if self.reason not in COVERAGE_REASONS:
            raise ValueError("coverage reason is not in the closed vocabulary")
        if self.status not in REASON_STATUSES[self.reason]:
            raise ValueError("coverage reason does not explain this status")

        if self.status == COVERAGE_COMPLETE and self.truncated:
            raise ValueError("a complete read cannot be truncated")

        if self.item_count < 0:
            raise ValueError("item count cannot be negative")

        if self.reason == REASON_EMPTY_WINDOW and self.item_count != 0:
            raise ValueError("an empty window reason requires no items")

        if self.truncated and self.reason not in _TRUNCATING_REASONS:
            raise ValueError("truncation requires a cut short reason")

        if self.status in _SERVED_NOTHING:
            if self.freshness is not ReadFreshness.UNKNOWN:
                raise ValueError(
                    "a status that served nothing has unknown freshness")
            if self.item_count != 0:
                raise ValueError("a status that served nothing has no items")
            if self.observed_through is not None:
                raise ValueError(
                    "a status that served nothing has no observed point")
            if self.complete_through is not None:
                raise ValueError(
                    "a status that served nothing has no complete point")

        if self.complete_through is not None:
            if self.observed_through is None:
                raise ValueError("a complete point requires an observed point")
            if self.complete_through > self.observed_through:
                raise ValueError(
                    "a complete point cannot exceed the observed point")

        if self.status == COVERAGE_COMPLETE:
            if (self.observed_through is not None
                    and self.complete_through != self.observed_through):
                raise ValueError(
                    "a complete read completes through what it observed")

        if self.requested_start is not None and self.requested_end is not None:
            if self.requested_start > self.requested_end:
                raise ValueError("the requested window is inverted")



#: The item type a result carries. The boundary never inspects an item, which
#: is exactly why it can be generic over one.
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ReadResult(Generic[T]):
    """Items plus what the source is entitled to claim about them.

    An empty ``items`` is not an answer on its own. It means "there is nothing"
    only when ``coverage`` says the window was actually accounted for, and
    means "nobody looked" otherwise. Keeping the two together is what stops a
    caller reading the first as the second.

    Carries items and coverage and nothing else. Provenance stays where it
    already is, on the response envelope that names the source for the whole
    answer.
    """

    items: tuple[T, ...]
    coverage: ReadCoverage

    def __post_init__(self) -> None:
        # Checked before the count, so that anything without a length is
        # refused as the wrong shape rather than raising from ``len``.
        if not isinstance(self.items, tuple):
            raise ValueError("result items must be a tuple")
        if self.coverage.item_count != len(self.items):
            raise ValueError("coverage item count disagrees with the items")

    def __iter__(self):
        """Migration compatibility only; durable consumers read ``.coverage``.

        Deliberately the whole of what a list-shaped call site gets: there is
        no indexing, no slicing, no concatenation and no equality with a list,
        because a consumer that only iterates is the one this design exists to
        correct. It has to be migrated rather than accommodated.
        """
        return iter(self.items)

    def __len__(self) -> int:
        """Migration compatibility only; durable consumers use ``.coverage``."""
        return len(self.items)


# --- Activation ---------------------------------------------------------------
#
# The names of the variables that select and configure a source live here, in
# the one module that depends on nothing, so that the bridge which reads them
# and any launcher which writes them agree by construction rather than by two
# copies of a string. A launcher that cannot import this module keeps its own
# copy and a test compares the two.

#: Selects the source. Absent or ``visual`` is the default and is the only
#: value that needs no further configuration.
MESSAGE_SOURCE_ENV: str = "WECHAT_COMPANION_MESSAGE_SOURCE"

#: The external reader executable. Always injected, never discovered: there is
#: no search path, no default location, and no candidate list anywhere.
READER_BIN_ENV: str = "WECHAT_COMPANION_READER_BIN"

#: Optional configuration file handed to the external reader.
READER_CONFIG_ENV: str = "WECHAT_COMPANION_READER_CONFIG"

#: Optional per-call timeout in seconds for the external reader.
READER_TIMEOUT_ENV: str = "WECHAT_COMPANION_READER_TIMEOUT"

#: Every variable that selects or configures a non-default source. A launcher
#: that passes none of these gets the visual store, which is the point.
ACTIVATION_ENV_NAMES: frozenset[str] = frozenset({
    MESSAGE_SOURCE_ENV,
    READER_BIN_ENV,
    READER_CONFIG_ENV,
    READER_TIMEOUT_ENV,
})


class MessageSourceError(Exception):
    """A source cannot answer, and will not guess.

    ``state`` is a fixed, lowercase token describing the category of refusal.
    ``detail`` is a fixed human-readable sentence. Neither may ever carry chat
    content, a sender, a filesystem path, or output captured from another
    program: both are returned to a client and both are safe to log.
    """

    def __init__(self, state: str, detail: str) -> None:
        super().__init__(detail)
        self.state = state
        self.detail = detail


@dataclass(frozen=True)
class SourceStatus:
    """Readiness of one source. Never carries chat content.

    ``conversation_count`` and ``message_count`` are optional because they are
    not universally cheap: a source that would have to scan to answer reports
    ``None`` rather than an estimate or a capped total that reads like a total.
    """

    source: str
    ready: bool
    state: str
    detail: str = ""
    schema_version: int | None = None
    conversation_count: int | None = None
    message_count: int | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "state": self.state,
            "schema_version": self.schema_version,
            "conversation_count": self.conversation_count,
            "message_count": self.message_count,
        }


@dataclass(frozen=True)
class NormalizedConversation:
    """One conversation, in the representation the bridge already publishes.

    ``first_seen_at`` and ``last_seen_at`` are optional. The visual path knows
    both because it watched them arrive; a database read may know only when the
    last message was written. ``None`` says "not known by this source" and is
    not the same claim as a zero.
    """

    id: int
    title: str | None
    first_seen_at: float | None
    last_seen_at: float | None
    source: str

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
        }


@dataclass(frozen=True)
class NormalizedMessage:
    """One message, in the representation the bridge already publishes.

    The field set is the store's, not a new model: adding a parallel shape
    would push the difference between readers all the way out to the skill.

    Two fields mean subtly different things per source, and that difference is
    the reason the envelope names the source.

    ``first_observed_at`` is when the visual path first saw the message on
    screen. A database source has no such moment worth reporting and supplies
    the message's own stored creation time instead, which is strictly better
    ordering information. Both are Unix timestamps and both are safe to filter
    on; only their meaning differs.

    ``visible_time`` is the string WeChat itself displayed, such as a relative
    day label. It is never a timestamp and is never parsed into one. A database
    source leaves it ``None`` rather than inventing a formatted time, because a
    value there would look like something a user saw when it is not.
    """

    id: int
    conversation_id: int
    sequence: int
    sender: str | None
    ownership: str
    visible_time: str | None
    text: str | None
    kind: str
    confidence: float
    first_observed_at: float
    source: str

    def payload(self) -> dict[str, Any]:
        """The wire shape. Provenance is intentionally not included.

        The envelope carries the source for the whole response; per-message
        provenance stays inside the process until there is a decision to make
        it user-visible.
        """
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "sequence": self.sequence,
            "sender": self.sender,
            "ownership": self.ownership,
            "visible_time": self.visible_time,
            "text": self.text,
            "kind": self.kind,
            "confidence": self.confidence,
            "first_observed_at": self.first_observed_at,
        }


@runtime_checkable
class MessageSource(Protocol):
    """What the bridge requires of any reader.

    Four questions, no more. There is deliberately no query method, no path
    argument, and no way to ask a source for arbitrary data: the tools are a
    fixed surface and a source cannot widen it.

    Every method raises :class:`MessageSourceError` when it cannot answer.
    Returning an empty list means the source answered and found nothing, which
    is a different fact from being unable to answer, and callers rely on the
    distinction.
    """

    #: One of :data:`SOURCE_NAMES`.
    name: str

    def status(self) -> SourceStatus:
        """Readiness only. Never raises for an unready source: it reports."""
        ...

    def list_conversations(self, limit: int) -> list[NormalizedConversation]:
        ...

    def get_messages(
        self,
        conversation_id: int,
        limit: int,
        before_sequence: int | None = None,
    ) -> list[NormalizedMessage]:
        ...

    def get_recent_messages(
        self, since_observed_at: float, limit: int
    ) -> list[NormalizedMessage]:
        ...
