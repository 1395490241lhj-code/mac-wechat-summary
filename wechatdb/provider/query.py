"""The closed, bounded, typed surface every Database-reader consumer uses.

Four questions are the whole vocabulary, every one of them bounded, and every
answer is either the provider's own envelope forwarded unchanged or one fixed
refusal token. What a conversation is, how far a read actually got, and whether
a schema is a generation this reader understands stay owned by the layers
below; nothing here recomputes any of it.

**No arbitrary reader power.** No operation takes SQL, a table name, a column,
a WHERE clause, an ORDER BY, an expression or a filesystem path. A caller picks
one of four names and fills in the one typed request that operation accepts, so
a field belonging to another operation cannot be supplied at all, and a
mapping is refused rather than interpreted.

**Every answer is bounded.** Each collection operation takes a limit in the
closed range ``1..MAX_READ_LIMIT``. Zero, a negative number, a bool, a
non-integer, a value above the maximum and an absent limit are all refused, and
refused rather than clamped: a caller is never handed fewer results than it
asked for while believing it asked for more. The bound is one definition the
provider itself imports, so a consumer that bypasses this module and calls the
provider directly finds no wider door.

**The cursor is the provider's, and it is bound to its scope.**
``before_sequence`` is the existing authoritative pagination value and is
forwarded as given; no second cursor format and no raw row identifier is
invented. What this module adds is the binding the raw value cannot carry: a
continuation names the conversation it came from, so replaying one
conversation's cursor against another is refused rather than silently narrowing
or widening the read. Only the conversation operation has a field to hold a
cursor, so a cursor cannot reach a listing or a recent read at all.

**One error vocabulary, and nothing else escapes.** Every outcome is one of the
fixed tokens below. No token is assembled from the value that was refused, and
no SQLite message, path, table name, schema, chat content, sender or exception
repr can reach a caller, because each failure becomes a token and the original
survives only as a suppressed cause.

**Search is declared and refused, not wired.** The native index is a sealed
spike that is deliberately not product-wired, so the honest outcome is to name
the operation and refuse it with a capability token -- never to return an
index's candidate rows, and never to let an absent index read as an empty
conversation.

**What this module deliberately does not do.** It finds no database: no
locator, no directory listing, no search, no path. It classifies no schema and
constructs no coverage. It changes no source default, wires no MCP tool and
touches no UI. It is an internal contract over components that already exist,
exercised against synthetic fixtures only.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from .compatibility import UnsupportedGeneration

try:
    from message_source import SOURCE_DATABASE, ReadCoverage
except ImportError:  # pragma: no cover - imported from another cwd
    import os
    import sys

    sys.path.insert(
        0,
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "bridge",
        ),
    )
    from message_source import SOURCE_DATABASE, ReadCoverage


# -- Operations, closed --------------------------------------------------------


class QueryOperation(str, Enum):
    """The whole vocabulary. Four names, and a caller picks one.

    A ``str`` enum, so the token a log reads is the token the code compares,
    with no translation table and no second spelling. Nothing outside this enum
    is a query. There is deliberately no "exact message get": the identity
    contract that would make one sound is not yet sealed, and an unsealed
    identity is not something to expose at a boundary.
    """

    #: Which conversations exist, newest first.
    LIST_CONVERSATIONS = "list_conversations"
    #: What is in one conversation, paginated by the provider's own sequence.
    CONVERSATION_MESSAGES = "conversation_messages"
    #: What is new since a moment the caller names.
    RECENT_MESSAGES = "recent_messages"
    #: Declared, deliberately not wired. Refused with a capability token.
    NATIVE_SEARCH = "native_search"


#: The one operation with no implementation, named once so the dispatcher's
#: reason for refusing it is one comparison rather than a branch per request.
UNWIRED_OPERATION: QueryOperation = QueryOperation.NATIVE_SEARCH


# -- Bounds, closed ------------------------------------------------------------

#: The largest number of items any one query may return. One number for every
#: collection operation, so "how far may a read reach" has a single answer that
#: cannot drift per operation, and a caller cannot discover a larger ceiling on
#: whichever operation it happens to be holding. The provider imports this, so
#: the bound holds on both sides of this module.
MAX_READ_LIMIT: int = 500


# -- The refusal vocabulary, closed -------------------------------------------

#: The operation is not one of the four names above. Covers a statement, a
#: relation name, an expression, a path, and anything else offered where an
#: operation name belongs.
ERROR_INVALID_QUERY: str = "invalid_query"

#: The operation is real and the arguments are not: a limit out of range or of
#: the wrong type, a window bound that is not a finite number, a conversation
#: reference of the wrong shape, a cursor from another conversation, or a
#: request that is not the one class this operation accepts.
ERROR_INVALID_ARGUMENT: str = "invalid_argument"

#: The source was asked and could not be read: a SQLite failure or a part that
#: will not open. One token for both, because each is a database this reader
#: cannot read and each must reach a caller as a fixed word.
ERROR_SOURCE_UNAVAILABLE: str = "source_unavailable"

#: A generation this reader will not read, refused as unverified rather than
#: parsed on a best-effort basis, and publishing nothing.
ERROR_UNSUPPORTED_GENERATION: str = "unsupported_generation"

#: The operation is declared and deliberately not wired. Never a substitute for
#: "there was nothing to find": an unsupported capability and an empty read are
#: different claims, and collapsing them is the failure this boundary exists to
#: prevent.
ERROR_UNSUPPORTED_CAPABILITY: str = "unsupported_capability"

#: A failure this layer could not classify. Content-free by construction: it is
#: a token, not a message.
ERROR_INTERNAL: str = "internal_error"


ERROR_STATES: frozenset[str] = frozenset({
    ERROR_INVALID_QUERY,
    ERROR_INVALID_ARGUMENT,
    ERROR_SOURCE_UNAVAILABLE,
    ERROR_UNSUPPORTED_GENERATION,
    ERROR_UNSUPPORTED_CAPABILITY,
    ERROR_INTERNAL,
})


#: One fixed, content-free sentence per token. Safe to return and safe to log,
#: and never built from the value that was refused.
ERROR_DETAILS: Mapping[str, str] = MappingProxyType({
    ERROR_INVALID_QUERY: "This query is not one of the supported operations.",
    ERROR_INVALID_ARGUMENT: "This query has arguments outside their bounds.",
    ERROR_SOURCE_UNAVAILABLE: "The database source is not safely readable.",
    ERROR_UNSUPPORTED_GENERATION: "This database generation is not verified.",
    ERROR_UNSUPPORTED_CAPABILITY: "This reader does not support that query.",
    ERROR_INTERNAL: "This query could not be completed.",
})


# -- What an answer is ---------------------------------------------------------

#: Items plus the provider's own coverage, forwarded unchanged.
KIND_ITEMS: str = "items"

#: A typed refusal: no items, no coverage, one fixed state. A complete answer in
#: its own right, so no consumer can mistake it for a read that found nothing.
KIND_REFUSAL: str = "refusal"

RESULT_KINDS: frozenset[str] = frozenset({KIND_ITEMS, KIND_REFUSAL})


# -- Requests, one class per served operation ---------------------------------


@dataclass(frozen=True, slots=True)
class ConversationCursor:
    """A continuation, carrying the scope that makes it meaningful.

    ``before_sequence`` alone is a bare integer: nothing in it says which
    conversation produced it, so on its own it could narrow any conversation to
    any other, and nothing in it says which source minted it either. Naming both
    here is what binds it, and the dispatcher refuses a cursor whose source or
    conversation is not the one being read. The operation needs no name in it:
    only ``ConversationMessagesQuery`` has a field to hold a cursor, so a
    cursor cannot be offered to a listing or a recent read at all. No token is
    signed and no format is invented; the provider still receives the same
    integer it has always received.
    """

    source: str
    conversation_id: int
    before_sequence: int


@dataclass(frozen=True, slots=True)
class ListConversationsQuery:
    """How many conversations, and nothing else.

    ``limit`` has no default, so there is no way to ask for the listing without
    naming a bound. A caller that wants everything is refused rather than
    served a listing whose length the source decided.
    """

    limit: int


@dataclass(frozen=True, slots=True)
class ConversationMessagesQuery:
    """One conversation, one window of it, optionally continuing from a cursor.

    ``conversation_id`` is the provider's own identifier for the conversation,
    exactly as :mod:`wechatdb.provider.result` derives it. This module mints no
    second identifier and unwraps none: the bridge already owns how a public
    reference maps onto this value, and a second mapping here would be a second
    source of truth about identity.
    """

    conversation_id: int
    limit: int
    cursor: ConversationCursor | None = None


@dataclass(frozen=True, slots=True)
class RecentMessagesQuery:
    """Everything new since a moment the caller names.

    ``since_observed_at`` bounds a window, it is not an identity, so it is
    validated as a real finite number rather than as a reference. A bool, a
    string, a NaN and an infinity are all refused: the first two are not
    numbers, and the last two would silently match everything or nothing.

    There is no cursor field, and there is none on purpose: a recent read is
    bounded by a moment, not by a sequence, so a conversation cursor has nowhere
    legal to go here.
    """

    since_observed_at: float
    limit: int


#: The one request class each served operation accepts. A caller that hands the
#: dispatcher a different class is refused, which makes "arguments cannot be
#: smuggled between operations" a property of the construction rather than a
#: rule someone remembers to apply. Matched by exact type, so a subclass cannot
#: widen the accepted surface either.
QUERY_CLASSES: dict[QueryOperation, type] = {
    QueryOperation.LIST_CONVERSATIONS: ListConversationsQuery,
    QueryOperation.CONVERSATION_MESSAGES: ConversationMessagesQuery,
    QueryOperation.RECENT_MESSAGES: RecentMessagesQuery,
}


# -- Results -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QueryResult:
    """One query's answer, tagged with what kind of answer it is.

    ``items`` and ``coverage`` are the provider's own values, forwarded
    unchanged. A query layer that rebuilt the envelope would be a second place
    coverage could be got wrong, so it holds no way to build one at all.

    The invariant below is what makes "every query ends in exactly one explicit
    outcome" structural rather than a convention: a served read always carries
    coverage and no state, a refusal always carries a state and never coverage
    or items. There is no shape in which a caller holds items beside a hidden
    error, and none in which a refusal could be read as an empty answer.
    """

    kind: str
    source: str
    items: tuple = ()
    coverage: ReadCoverage | None = None
    continuation: ConversationCursor | None = None
    state: str | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in RESULT_KINDS:
            raise ValueError("result kind is not in the closed vocabulary")
        if self.kind == KIND_ITEMS:
            if self.coverage is None or self.state is not None:
                raise ValueError("a served read carries coverage and no state")
        else:
            if self.state not in ERROR_STATES:
                raise ValueError("a refusal carries one closed state")
            if self.coverage is not None or self.items or self.continuation:
                raise ValueError("a refusal carries no read evidence")

    def ok(self) -> bool:
        """Whether this answer served a read. One predicate, no guessing."""
        return self.kind == KIND_ITEMS


def _refusal(state: str) -> QueryResult:
    return QueryResult(
        kind=KIND_REFUSAL, source=SOURCE_DATABASE, state=state,
        detail=ERROR_DETAILS[state],
    )


# -- Argument validation -------------------------------------------------------


def _bounded_limit(limit: object) -> int | None:
    """The limit, or None. A bool is not an int here, and 10**400 is not a limit."""
    if isinstance(limit, bool) or not isinstance(limit, int):
        return None
    return limit if 1 <= limit <= MAX_READ_LIMIT else None


def _conversation_id(value: object) -> int | None:
    """A conversation identifier this source minted, or None.

    The provider's identifiers are positive integers derived by the canonical
    owner in :mod:`conversation_identity`. Anything else -- a bool, a float, a
    string, zero, a negative reference belonging to another namespace -- is not
    one, and is refused here rather than reaching a query.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _window_start(value: object) -> float | None:
    """A finite window bound, or None. A bool, a string, a NaN and an infinity
    are all refused: the first two are not numbers, and the last two would
    silently match everything or nothing.

    An ``int`` is accepted here, so the finiteness test is reached through a
    conversion that can itself fail: :func:`math.isfinite` raises
    ``OverflowError`` on a value like ``10 ** 400`` rather than returning
    ``False``, and letting that raise would report a caller's bad argument as
    ``internal_error`` -- a different claim about whose fault the read was. The
    conversion is therefore bounded, and a value that cannot be a moment at all
    is refused on the same grounds as an infinity: a window past every
    representable instant would match nothing at all, and a read that matched
    nothing would be indistinguishable from a genuinely empty one.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        as_moment = float(value)
    except OverflowError:
        return None
    return value if math.isfinite(as_moment) else None


def _before_sequence(request: object) -> int | None | str:
    """The sequence to forward, None, or one refusal token.

    A cursor is only a continuation of the source and conversation it names.
    Replaying one scope's cursor against another is refused, because a bare
    sequence number cannot prove where it belongs and silently narrowing an
    unrelated conversation is exactly the widening-by-accident this prevents.
    """
    cursor = request.cursor
    if cursor is None:
        return None
    if (type(cursor) is not ConversationCursor
            or cursor.source != SOURCE_DATABASE
            or cursor.conversation_id != request.conversation_id):
        return ERROR_INVALID_ARGUMENT
    sequence = cursor.before_sequence
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        return ERROR_INVALID_ARGUMENT
    return sequence


# -- The surface ---------------------------------------------------------------


class DatabaseQuery:
    """Asks one source the four questions above, and answers in one of two shapes.

    Constructed over the provider that already exists. It holds no locator, finds
    no database, opens no part, and remembers nothing between queries: a query
    establishes no state that could change the next one.
    """

    def __init__(self, provider: object) -> None:
        self._provider = provider

    @property
    def source(self) -> str:
        """The source every answer from this surface is attributed to."""
        return SOURCE_DATABASE

    def run(self, operation: object, request: object = None) -> QueryResult:
        """One query, one explicit outcome. No exception escapes this method."""
        try:
            # Resolving the operation name is inside the guard, not in front of
            # it. Membership in the vocabulary is a dict lookup, and a dict
            # lookup hashes its key, so a str subclass with a raising __hash__
            # would otherwise raise from a line the guard does not cover and
            # defeat "no exception escapes this method" for a value that is not
            # even a name this surface accepts.
            chosen = self._operation(operation)
            if chosen is None:
                return _refusal(ERROR_INVALID_QUERY)
            if chosen is UNWIRED_OPERATION:
                return _refusal(ERROR_UNSUPPORTED_CAPABILITY)

            if type(request) is not QUERY_CLASSES[chosen]:
                return _refusal(ERROR_INVALID_ARGUMENT)

            if self._provider is None:
                # There is no other source to fall back to, and a query over no
                # source is a source that cannot be read, not an internal fault.
                return _refusal(ERROR_SOURCE_UNAVAILABLE)

            return self._serve(chosen, request)
        except UnsupportedGeneration:
            # A changed generation is refused as unverified and publishes
            # nothing, rather than being parsed on a best-effort basis.
            return _refusal(ERROR_UNSUPPORTED_GENERATION)
        except sqlite3.Error:
            return _refusal(ERROR_SOURCE_UNAVAILABLE)
        except Exception:
            return _refusal(ERROR_INTERNAL)

    @staticmethod
    def _operation(operation: object) -> QueryOperation | None:
        """The named operation, or None. No prefix, no case folding, no coercion."""
        if isinstance(operation, QueryOperation):
            return operation
        if not isinstance(operation, str):
            return None
        try:
            return QueryOperation(operation)
        except ValueError:
            return None

    def _serve(self, operation: QueryOperation, request: object) -> QueryResult:
        """Validate the arguments, forward once, and hand back what came."""
        limit = _bounded_limit(request.limit)
        if limit is None:
            return _refusal(ERROR_INVALID_ARGUMENT)

        if operation is QueryOperation.LIST_CONVERSATIONS:
            result = self._provider.list_conversations(limit)
            # A listing is a newest-first snapshot, not a page of a traversal,
            # so it offers no continuation: handing one back would imply a
            # pagination this provider does not perform.
            return QueryResult(
                kind=KIND_ITEMS, source=SOURCE_DATABASE,
                items=result.items, coverage=result.coverage)

        if operation is QueryOperation.RECENT_MESSAGES:
            start = _window_start(request.since_observed_at)
            if start is None:
                return _refusal(ERROR_INVALID_ARGUMENT)
            result = self._provider.get_recent_messages(start, limit)
            return QueryResult(
                kind=KIND_ITEMS, source=SOURCE_DATABASE,
                items=result.items, coverage=result.coverage)

        conversation = _conversation_id(request.conversation_id)
        if conversation is None:
            return _refusal(ERROR_INVALID_ARGUMENT)
        before = _before_sequence(request)
        if isinstance(before, str):
            return _refusal(before)
        result = self._provider.get_messages(conversation, limit, before)
        # The continuation is the value the provider would accept back, bound to
        # the scope it came from. Offered only when the provider itself reports
        # the answer was cut short, so a caller is never handed a cursor that
        # would continue nothing.
        # A page is the newest N in ascending order, so items[0] is the OLDEST
        # item in it and that is where a continuation starts: the next page is
        # everything strictly older. The name says which, so this cannot later be
        # "corrected" into a cursor that continues into the page it came from.
        oldest = result.items[0].sequence if result.items else None
        continuation = (ConversationCursor(SOURCE_DATABASE, conversation, oldest)
                        if result.coverage.truncated and oldest is not None
                        else None)
        return QueryResult(
            kind=KIND_ITEMS, source=SOURCE_DATABASE,
            items=result.items, coverage=result.coverage,
            continuation=continuation)
