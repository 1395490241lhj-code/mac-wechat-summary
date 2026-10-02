"""P4 typed bounded query surface: a closed set of questions, and nothing else.

The claim under test is narrow and structural. A Database-reader consumer can
ask four questions, each with a typed request and a closed limit range, and can
receive nothing else: no SQL, no table, no column, no path, no unbounded read,
and no argument belonging to a different operation. Everything the surface
returns about coverage and about schema comes from the layer that already owned
it, forwarded rather than recomputed.

Every database here is synthetic and built under pytest's own ``tmp_path`` from
the shared fixture module: invented names, invented tables, invented text. No
real WeChat process, container, index, media store, session or contact database
is opened, and no credential exists anywhere in this file.

The vocabulary below is transcribed from the boundary contract rather than
imported, so a test that built its expectations out of the implementation's own
constants would prove only that the module agrees with itself. The maximum and
the error tokens are pinned here too, so a change to either reads as a change
to the contract rather than a silent one.
"""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

import message_source as ms
from conversation_identity import conversation_identifier
from wechatdb.provider import (
    ExplicitShardLocator,
    IdentityResolver,
    ShardEntry,
    ShardedMessageProvider,
)

from wechatdb.provider import compatibility as compatibility_module
from wechatdb.provider import discovery as discovery_module
from wechatdb.provider import query as query_module
from wechatdb.provider import provider as provider_module
from wechatdb.provider.query import (
    ConversationCursor,
    ConversationMessagesQuery,
    DatabaseQuery,
    ListConversationsQuery,
    MAX_READ_LIMIT,
    QueryOperation,
    RecentMessagesQuery,
)

from . import fixtures
from .fixtures import ALPHA, ROOM, SyntheticMessage as M


# --- the contract, transcribed -----------------------------------------------

LIST = "list_conversations"
CONVERSATION = "conversation_messages"
RECENT = "recent_messages"
SEARCH = "native_search"

SERVED_OPERATIONS = (LIST, CONVERSATION, RECENT)
DECLARED_OPERATIONS = SERVED_OPERATIONS + (SEARCH,)

ITEMS = "items"
REFUSAL = "refusal"

INVALID_QUERY = "invalid_query"
INVALID_ARGUMENT = "invalid_argument"
SOURCE_UNAVAILABLE = "source_unavailable"
UNSUPPORTED_GENERATION = "unsupported_generation"
UNSUPPORTED_CAPABILITY = "unsupported_capability"
INTERNAL_ERROR = "internal_error"

ERROR_TOKENS = (
    INVALID_QUERY,
    INVALID_ARGUMENT,
    SOURCE_UNAVAILABLE,
    UNSUPPORTED_GENERATION,
    UNSUPPORTED_CAPABILITY,
    INTERNAL_ERROR,
)

#: The bound this surface publishes for one read, and the one the provider
#: enforces on a caller that bypasses the surface entirely.
EXPECTED_MAXIMUM = 500

QUERY_SOURCE = Path(query_module.__file__)

OTHER_ROOM = "fixture_room_0002@chatroom"


def entry(part):
    return ShardEntry(name=part.name, handle=part.path)


def provider_over(parts, *, conversations=(ROOM,)):
    identities = IdentityResolver(
        (),
        session_names={
            fixtures.conversation_table(conversation)[len("Msg_"):]: conversation
            for conversation in conversations
        },
    )
    return ShardedMessageProvider(
        ExplicitShardLocator([entry(part) for part in parts]),
        identities=identities,
    )


def six():
    return [
        M(index, 100 * index, ALPHA, "fixture text %d" % index, server_id=index)
        for index in range(1, 7)
    ]


def conversation(conversation_name: str = ROOM) -> int:
    """The provider's own identifier for one conversation."""
    return conversation_identifier(conversation_name)


def request_for(operation, limit=5, **fields):
    """The one request class this operation accepts, filled in for the test."""
    builder = {
        LIST: lambda: ListConversationsQuery(limit=limit),
        CONVERSATION: lambda: ConversationMessagesQuery(
            limit=limit, conversation_id=fields.get("conversation", conversation()),
            cursor=cursor_for(fields.get("cursor"))),
        RECENT: lambda: RecentMessagesQuery(
            since_observed_at=fields.get("since", 0.0), limit=limit),
    }[operation]()
    return builder


def cursor_for(value):
    """A cursor the surface itself would mint, unless the test names one."""
    if isinstance(value, dict):
        return ConversationCursor(
            value.get("source", ms.SOURCE_DATABASE),
            value["conversation_id"],
            value["before_sequence"])
    if isinstance(value, tuple):
        return ConversationCursor(
            value[2] if len(value) == 3 else ms.SOURCE_DATABASE, value[0], value[1])
    return value


@pytest.fixture
def readable(tmp_path):
    """Two synthetic parts holding one conversation's six messages."""
    return fixtures.split_conversation(tmp_path, ROOM, six(), stem="message")


@pytest.fixture
def served(readable):
    return DatabaseQuery(provider_over(readable))


def assert_refused(result, state: str) -> None:
    assert result.kind == REFUSAL, result.kind
    assert result.state == state, (result.state, result.detail)
    # A refusal is a complete answer: no items, no coverage, no continuation, and
    # nothing a consumer could mistake for a read that happened and found nothing.
    assert result.items == ()
    assert result.coverage is None
    assert result.continuation is None
    assert result.detail == query_module.ERROR_DETAILS[state]


def prose_constants(tree):
    """Ids of the docstring constants, so prose about absence is not scanned."""
    ids = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", [])
        if (isinstance(body, list) and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            ids.add(id(body[0].value))
    return ids


# --- operation closure --------------------------------------------------------

def test_the_vocabulary_is_exactly_four_names_and_three_of_them_are_served():
    assert {operation.value for operation in QueryOperation} == set(
        DECLARED_OPERATIONS)
    assert {operation.value
            for operation in query_module.QUERY_CLASSES} == set(SERVED_OPERATIONS)
    assert query_module.UNWIRED_OPERATION is QueryOperation.NATIVE_SEARCH


@pytest.mark.parametrize("operation", SERVED_OPERATIONS)
def test_each_served_operation_answers_with_items_and_coverage(
        served, operation):
    result = served.run(operation, request_for(operation))
    assert result.kind == ITEMS
    assert result.state is None
    assert result.items
    assert result.coverage is not None
    assert result.coverage.item_count == len(result.items)
    assert result.source == ms.SOURCE_DATABASE


def test_the_enum_and_its_token_name_the_same_operation(readable):
    served = DatabaseQuery(provider_over(readable))
    by_token = served.run(LIST, request_for(LIST))
    by_member = served.run(QueryOperation.LIST_CONVERSATIONS, request_for(LIST))
    assert by_token.items == by_member.items
    assert by_token.coverage == by_member.coverage


@pytest.mark.parametrize("operation", [
    "messages",
    "LIST_CONVERSATIONS",
    "list conversations",
    "list_conversations ",
    " list_conversations",
    "list_conversations; DROP TABLE messages",
    "",
    None,
    7,
    [LIST],
    (LIST,),
    {"operation": LIST},
    object(),
])
def test_an_operation_outside_the_vocabulary_is_refused(served, operation):
    assert_refused(served.run(operation, request_for(LIST)), INVALID_QUERY)


@pytest.mark.parametrize("operation", [
    "SELECT * FROM Msg_0",
    "DROP TABLE messages",
    "PRAGMA table_info('Msg_0')",
    "Msg_0.local_id = 1 ORDER BY create_time",
    "/Users/fixture/secret.db",
    "message_0.db",
    "sqlite_master",
    "Name2Id",
])
def test_a_statement_a_relation_or_a_path_is_never_an_operation(
        served, operation):
    """No prefix match, no normalisation, no case folding reaches an operation."""
    assert_refused(served.run(operation, request_for(LIST)), INVALID_QUERY)


@pytest.mark.parametrize("extra", [
    {"sql": "SELECT 1"},
    {"table": "Msg_0"},
    {"column": "local_id"},
    {"path": "/tmp/x.db"},
    {"order_by": "create_time"},
    {"conversation_id": 1},
    {"since_observed_at": 0.0},
])
def test_a_request_cannot_be_built_with_a_field_it_does_not_have(extra):
    """Extra fields are refused at construction, so none can be ignored."""
    with pytest.raises(TypeError):
        ListConversationsQuery(limit=5, **extra)


@pytest.mark.parametrize("supplied", [
    {"limit": 5},
    {"operation": LIST, "limit": 5},
    {"limit": 5, "sql": "SELECT 1"},
    [5],
    (5,),
    5,
    "list_conversations",
    None,
    object(),
])
def test_a_mapping_or_anything_else_is_refused_not_interpreted(served, supplied):
    """The dispatcher takes typed requests only; a mapping is not a query."""
    assert_refused(served.run(LIST, supplied), INVALID_ARGUMENT)


def test_a_request_for_another_operation_cannot_smuggle_its_arguments(served):
    """Each operation has one request class, so a field cannot migrate."""
    assert_refused(
        served.run(LIST, ConversationMessagesQuery(limit=5, conversation_id=1)),
        INVALID_ARGUMENT)
    assert_refused(
        served.run(RECENT, ListConversationsQuery(limit=5)), INVALID_ARGUMENT)
    assert_refused(
        served.run(CONVERSATION,
                   RecentMessagesQuery(since_observed_at=0.0, limit=5)),
        INVALID_ARGUMENT)


def test_a_subclass_of_a_request_is_refused_rather_than_read(served):
    class Widened(ListConversationsQuery):
        pass

    assert_refused(served.run(LIST, Widened(limit=5)), INVALID_ARGUMENT)


@pytest.mark.parametrize("operation", SERVED_OPERATIONS)
def test_an_absent_request_is_not_a_request_with_defaults(served, operation):
    """Asking for a listing without naming a bound asks for everything."""
    assert_refused(served.run(operation, None), INVALID_ARGUMENT)


# --- bounds -------------------------------------------------------------------

def test_the_maximum_is_pinned_so_the_bound_cannot_drift():
    assert MAX_READ_LIMIT == EXPECTED_MAXIMUM


@pytest.mark.parametrize("operation", SERVED_OPERATIONS)
@pytest.mark.parametrize("limit", [
    0,
    -1,
    -MAX_READ_LIMIT,
    True,
    False,
    1.0,
    5.5,
    "5",
    None,
    MAX_READ_LIMIT + 1,
    10 ** 9,
    10 ** 400,
    [5],
    (5,),
    object(),
])
def test_every_list_query_has_a_closed_range_at_both_ends(
        served, operation, limit):
    request = request_for(operation, limit=limit)
    assert_refused(served.run(operation, request), INVALID_ARGUMENT)


@pytest.mark.parametrize("limit", [1, 2, 5, MAX_READ_LIMIT - 1, MAX_READ_LIMIT])
def test_the_boundaries_themselves_are_served(served, limit):
    result = served.run(LIST, ListConversationsQuery(limit=limit))
    assert result.kind == ITEMS
    assert len(result.items) <= limit


def test_a_limit_is_refused_and_never_silently_clamped(served):
    """A caller asking for more than the maximum is told, not shortened."""
    assert_refused(
        served.run(LIST, ListConversationsQuery(limit=MAX_READ_LIMIT + 1)),
        INVALID_ARGUMENT)
    assert served.run(LIST, ListConversationsQuery(limit=MAX_READ_LIMIT)).items


def test_there_is_no_path_to_an_unbounded_read(readable):
    """The provider itself is bounded, not only the layer above it.

    A consumer that bypasses the query surface and calls the provider directly
    must not find a wider door: one bound, one definition, applied at the reader
    every caller routes through.
    """
    provider = provider_over(readable)
    calls = (
        provider.list_conversations,
        lambda bound: provider.get_messages(conversation(), bound),
        lambda bound: provider.get_recent_messages(0.0, bound),
    )
    for call in calls:
        assert call(MAX_READ_LIMIT) is not None
        for refused in (MAX_READ_LIMIT + 1, 10 ** 400, 0, -1, True, "5"):
            with pytest.raises(ValueError):
                call(refused)


def test_the_provider_and_the_surface_publish_the_same_bound():
    """One number, not two that could drift apart."""
    assert provider_module.MAX_READ_LIMIT is MAX_READ_LIMIT


@pytest.mark.parametrize("since", [
    True, False, "0", None, [0], object(),
    float("nan"), float("inf"), float("-inf"),
    10 ** 400, -10 ** 400,
])
def test_a_window_bound_that_is_not_a_finite_number_is_refused(served, since):
    assert_refused(
        served.run(RECENT, RecentMessagesQuery(since_observed_at=since, limit=5)),
        INVALID_ARGUMENT)


@pytest.mark.parametrize("since", [0, 0.0, -1, -1.5, 10 ** 12])
def test_an_integer_window_bound_is_ordinary_evidence(served, since):
    assert served.run(
        RECENT, RecentMessagesQuery(since_observed_at=since, limit=5)).kind == ITEMS


# --- scope and cursor ---------------------------------------------------------

def test_a_conversation_scoped_query_stays_exact(served):
    result = served.run(CONVERSATION, request_for(CONVERSATION, MAX_READ_LIMIT))
    assert result.kind == ITEMS
    assert len(result.items) == 6
    assert {item.conversation_id for item in result.items} == {conversation()}


@pytest.mark.parametrize("bad", [
    0, -1, True, False, 1.0, "-1", None, object(), -conversation(),
])
def test_a_reference_that_is_not_an_identifier_is_refused(served, bad):
    """Zero, a negative reference from the other namespace, a bool and a
    non-integer are not conversation references at all.

    A well-formed positive integer is not tested here and cannot be: the
    identifiers this reader mints are opaque 63-bit values with no reserved
    range, so no number can be proven absent without reading the source. A
    reference this source does not hold is an empty scope, covered separately.
    """
    assert_refused(
        served.run(CONVERSATION,
                   ConversationMessagesQuery(limit=5, conversation_id=bad)),
        INVALID_ARGUMENT)


def test_a_reference_no_conversation_holds_reads_as_an_empty_scope(served):
    """A well-formed reference for a conversation this source does not hold is
    not an error: the source cannot serve that scope, and it says so in coverage
    rather than by raising or by widening the question."""
    absent = conversation_identifier("fixture_absent_room@chatroom")
    result = served.run(
        CONVERSATION,
        ConversationMessagesQuery(limit=5, conversation_id=absent))
    assert result.kind == ITEMS
    assert result.items == ()
    assert result.coverage.item_count == 0


def test_a_cursor_continues_the_providers_own_pagination(served):
    everything = served.run(CONVERSATION, request_for(CONVERSATION, MAX_READ_LIMIT))
    assert len(everything.items) == 6

    # A page is the newest N in ascending order, so continuing from the page's
    # own oldest item yields the next-newest page below it, with no overlap.
    page = served.run(CONVERSATION, request_for(
        CONVERSATION, 2,
        cursor=ConversationCursor(ms.SOURCE_DATABASE, conversation(),
                                  everything.items[4].sequence)))
    assert [item.sequence for item in page.items] == [
        item.sequence for item in everything.items[2:4]]


def test_the_continuation_is_a_value_the_surface_accepts_back(served):
    """No second cursor format, no opaque token, no raw row identifier."""
    first = served.run(CONVERSATION, request_for(CONVERSATION, 2))
    assert first.coverage.truncated is True
    assert first.continuation is not None
    assert first.continuation.source == ms.SOURCE_DATABASE
    assert first.continuation.conversation_id == conversation()
    # A page is the newest N, ascending; the cursor is the oldest item in it, so
    # the next page is everything strictly older. The provider's own
    # before_sequence, forwarded unchanged.
    assert first.continuation.before_sequence == first.items[0].sequence

    second = served.run(
        CONVERSATION, request_for(CONVERSATION, 2, cursor=first.continuation))
    assert second.kind == ITEMS
    assert not ({item.sequence for item in second.items}
                & {item.sequence for item in first.items})


def test_an_untruncated_read_offers_no_continuation(served):
    """A cursor that would continue nothing is never handed out."""
    whole = served.run(CONVERSATION, request_for(CONVERSATION, MAX_READ_LIMIT))
    assert whole.coverage.truncated is False
    assert whole.continuation is None


def test_a_listing_offers_no_continuation(served):
    """A listing is a snapshot, not a page of a traversal."""
    assert served.run(LIST, request_for(LIST, 1)).continuation is None


def test_a_recent_read_offers_no_continuation(served):
    """A recent read is bounded by a moment, not by a sequence."""
    result = served.run(RECENT, request_for(RECENT, 2, since=0.0))
    assert result.kind == ITEMS
    assert result.continuation is None


def test_a_conversation_cursor_cannot_widen_into_another_operation(served):
    """Only the conversation request has a field to hold a cursor, and a
    mapping that tries to smuggle one is refused rather than interpreted."""
    page = served.run(CONVERSATION, request_for(CONVERSATION, 2))
    assert page.continuation is not None
    assert_refused(
        served.run(RECENT, {"limit": 5, "before_sequence": page.continuation}),
        INVALID_ARGUMENT)
    assert_refused(
        served.run(RECENT, RecentMessagesQuery(
            since_observed_at=0.0, limit=page.continuation)),
        INVALID_ARGUMENT)
    assert_refused(
        served.run(LIST, {"limit": 5, "cursor": page.continuation}),
        INVALID_ARGUMENT)


def test_a_cursor_belonging_to_one_conversation_is_refused_against_another(
        tmp_path):
    """The cross-scope case that a bare sequence number cannot catch.

    Conversation A is read to the maximum and yields a truncated page, so it
    hands back a continuation. That continuation names A. Replayed against B it
    is refused rather than narrowing B to A's sequence, because ``before_sequence``
    alone cannot prove which conversation minted it.
    """
    other_room = OTHER_ROOM
    other_messages = [M(50 + i, 1000 * i, ALPHA, "other %d" % i, server_id=50 + i)
                      for i in range(1, 5)]
    parts = fixtures.split_conversation(tmp_path, ROOM, six(), stem="message")
    parts.append(fixtures.readable_part(
        tmp_path, "message_9.db", other_room, other_messages))
    served = DatabaseQuery(
        provider_over(parts, conversations=(ROOM, other_room)))

    page = served.run(CONVERSATION, request_for(CONVERSATION, 2))
    assert page.coverage.truncated is True
    assert page.continuation.conversation_id == conversation()

    assert_refused(
        served.run(CONVERSATION, request_for(
            CONVERSATION, MAX_READ_LIMIT,
            conversation=conversation(other_room),
            cursor=page.continuation)),
        INVALID_ARGUMENT)


def test_a_cursor_with_the_right_conversation_still_serves_it(tmp_path):
    """The binding refuses a replayed scope, not a legitimate continuation."""
    other_messages = [M(50 + i, 1000 * i, ALPHA, "other %d" % i, server_id=50 + i)
                      for i in range(1, 5)]
    parts = fixtures.split_conversation(tmp_path, ROOM, six(), stem="message")
    parts.append(fixtures.readable_part(
        tmp_path, "message_9.db", OTHER_ROOM, other_messages))
    served = DatabaseQuery(
        provider_over(parts, conversations=(ROOM, OTHER_ROOM)))

    first = served.run(CONVERSATION, request_for(
        CONVERSATION, 2, conversation=conversation(OTHER_ROOM)))
    assert first.coverage.truncated is True
    second = served.run(CONVERSATION, request_for(
        CONVERSATION, 2, conversation=conversation(OTHER_ROOM),
        cursor=first.continuation))
    assert second.kind == ITEMS
    assert {item.conversation_id for item in second.items} == {
        conversation(OTHER_ROOM)}
    assert not ({item.sequence for item in second.items}
                & {item.sequence for item in first.items})


@pytest.mark.parametrize("sequence", [True, False, -1, 1.0, "3", [3], object()])
def test_a_malformed_cursor_fails_closed(served, sequence):
    assert_refused(
        served.run(CONVERSATION, request_for(
            CONVERSATION, 5,
            cursor=ConversationCursor(ms.SOURCE_DATABASE, conversation(),
                                      sequence))),
        INVALID_ARGUMENT)


def test_a_mapping_pretending_to_be_a_cursor_is_refused(served):
    """Only the typed cursor is accepted; its fields cannot be supplied loose."""
    page = served.run(CONVERSATION, request_for(CONVERSATION, 2))
    assert page.continuation is not None
    assert_refused(
        served.run(CONVERSATION, ConversationMessagesQuery(
            limit=5, conversation_id=conversation(),
            cursor={"conversation_id": conversation(), "before_sequence": 3})),
        INVALID_ARGUMENT)


def test_a_missing_source_answers_rather_than_falling_back():
    """There is no second source to fall back to, and no branch that reaches one."""
    query = DatabaseQuery(None)
    for operation in SERVED_OPERATIONS:
        assert_refused(query.run(operation, request_for(operation)), SOURCE_UNAVAILABLE)


def test_a_cursor_minted_by_another_source_is_refused(served):
    """The binding names the source, so a continuation cannot cross into one."""
    assert_refused(
        served.run(CONVERSATION, ConversationMessagesQuery(
            limit=5, conversation_id=conversation(),
            cursor=ConversationCursor("visual", conversation(), 500))),
        INVALID_ARGUMENT)


def test_every_answer_is_attributed_to_this_source():
    assert DatabaseQuery(None).source == ms.SOURCE_DATABASE


# --- coverage -----------------------------------------------------------------

def test_the_envelope_forwards_the_providers_own_coverage_unchanged(readable):
    provider = provider_over(readable)
    direct = provider.list_conversations(3)
    through = DatabaseQuery(provider).run(LIST, ListConversationsQuery(limit=3))
    assert through.items == direct.items
    # Equal field for field, and rebuilt on neither side: a query layer that
    # reconstructed the envelope would be a second place coverage could be got
    # wrong. ReadCoverage is frozen, so equality is the whole comparison.
    assert through.coverage == direct.coverage


def test_a_truncated_read_reports_the_callers_own_cut(served):
    result = served.run(CONVERSATION, request_for(CONVERSATION, 2))
    assert result.coverage.status == ms.COVERAGE_PARTIAL
    assert result.coverage.reason == ms.REASON_CALLER_LIMIT
    assert result.coverage.truncated is True


def test_an_empty_window_is_a_complete_read_not_inferred_from_a_count(readable):
    served = DatabaseQuery(provider_over(readable))
    result = served.run(
        RECENT, RecentMessagesQuery(since_observed_at=10 ** 9, limit=5))
    assert result.items == ()
    assert result.coverage.status == ms.COVERAGE_COMPLETE
    assert result.coverage.reason == ms.REASON_EMPTY_WINDOW


def test_the_query_layer_constructs_no_coverage_and_infers_no_completeness():
    """One coverage implementation: the surface cannot build a coverage object,
    so it cannot introduce a second completeness notion even by accident."""
    tree = ast.parse(QUERY_SOURCE.read_text(encoding="utf-8"))
    prose = prose_constants(tree)
    # A constructor can only be reached through a name, so a coverage is built
    # here exactly when a sensitive name is *called*. Tracking what each import
    # binds -- including an alias -- is what makes that checkable: the surface
    # legitimately names ReadCoverage for its own type annotation, so the import
    # is not the offence, calling the bound name is.
    sensitive = {"ReadCoverage", "ReadResult", "Contribution", "collapse"}
    constructed: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in sensitive:
                    constructed.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname and alias.asname in sensitive:
                    constructed.add(alias.asname)
    offenders = []
    for node in ast.walk(tree):
        if id(node) in prose:
            continue
        # The facts the module is allowed to touch a coverage for are named
        # explicitly, because the earlier set named fields the module never
        # reads and missed the two it does. ``truncated`` is the only one it
        # reads, and it is the field a continuation legitimately needs: it is
        # the provider saying the answer was cut short, not the surface deciding
        # how much of the source it saw. Every other coverage field, and the
        # status vocabulary that would let a count become a completeness claim,
        # stays forbidden -- so a guard that evaded through an alias still fails
        # here, because these are attribute and identifier names rather than a
        # value comparison.
        if isinstance(node, ast.Call) and (getattr(node.func, "id", None)
                                           or getattr(node.func, "attr", None)) in {
                "ReadCoverage", "ReadResult", "collapse", "Contribution",
                "read_coverage", "replace", "observed_through", "complete_through",
                "coverage_for"} | constructed:
            offenders.append(ast.unparse(node)[:60])
        if isinstance(node, ast.Name) and node.id in {
                "COVERAGE_COMPLETE", "COVERAGE_PARTIAL", "COVERAGE_UNAVAILABLE",
                "COVERAGE_NOT_OBSERVED", "COVERAGE_REASONS", "REASON_STATUSES",
                "COVERAGE_EMPTY_WINDOW", "REASON_EMPTY_WINDOW", "observed_complete",
                "observed_partial", "observed_unavailable"}:
            offenders.append(node.id)
        if isinstance(node, ast.Attribute) and node.attr in {
                "item_count", "observed_through", "complete_through", "status",
                "reason", "requested_start", "requested_end", "freshness"}:
            offenders.append("." + node.attr)
    assert offenders == [], offenders


def test_an_auxiliary_incompatibility_does_not_alter_unrelated_coverage(
        tmp_path, monkeypatch):
    """A non-message part being wrong is not a message-read fact.

    A real optional-role part -- an ``session`` database, which this reader
    never opens -- is added and then probed as ``unsupported``. The ordinary
    message parts are untouched, the real probe still runs, and only that
    part's structural outcome is replaced, so the coverage that comes out is
    production's own collapse over production's own accounting.

    The optional part is ``SHARD_UNKNOWN`` to this reader whatever role its
    name earns, so its presence alone already accounts for an inventory gap on
    every branch. What the test establishes is that replacing its outcome with
    ``unsupported`` moves nothing: the same gap, the same reason, the same
    items. The latent question of whether discovery's part regex and
    compatibility's role regex should agree is not this test's to settle.
    """
    parts = fixtures.split_conversation(tmp_path, ROOM, six(), stem="message")
    optional = fixtures.readable_part(
        tmp_path, "session.db", OTHER_ROOM, [M(1, 100, ALPHA, "synthetic row")])
    roles = discovery_module.classify_shard_name(optional.name)
    assert roles == compatibility_module.ROLE_AUXILIARY

    real = discovery_module.ShardDiscovery.compatibility

    def with_broken_optional(self):
        outcomes = dict(real(self))
        outcomes[optional.name] = compatibility_module.UNSUPPORTED
        return outcomes

    monkeypatch.setattr(discovery_module.ShardDiscovery, "compatibility",
                        with_broken_optional)
    # Built after the patch, so the accounting under test is production's own
    # and only this one outcome differs between the two branches.
    patched_provider = provider_over(parts + [optional])
    patched = DatabaseQuery(patched_provider).run(
        CONVERSATION, request_for(CONVERSATION, MAX_READ_LIMIT))

    monkeypatch.undo()
    clean_provider = provider_over(parts + [optional])
    clean = DatabaseQuery(clean_provider).run(
        CONVERSATION, request_for(CONVERSATION, MAX_READ_LIMIT))

    assert clean.coverage.reason == patched.coverage.reason
    assert patched.items == clean.items
    assert patched.coverage == clean.coverage
    assert patched_provider.compatibility.outcome_of(
        optional.name) == compatibility_module.UNSUPPORTED


def test_an_unsupported_search_does_not_alter_ordinary_coverage(served):
    """A refused capability is not a read, so it cannot move a coverage claim."""
    before = served.run(CONVERSATION, request_for(CONVERSATION, MAX_READ_LIMIT))
    assert_refused(served.run(SEARCH, None), UNSUPPORTED_CAPABILITY)
    after = served.run(CONVERSATION, request_for(CONVERSATION, MAX_READ_LIMIT))
    assert after.items == before.items
    assert after.coverage == before.coverage


def test_a_search_query_is_refused_and_never_returns_an_index_candidate(served):
    for request in (None, {"term": "fixture", "limit": 5}):
        result = served.run(SEARCH, request)
        assert_refused(result, UNSUPPORTED_CAPABILITY)
        assert result.items == ()
        assert result.coverage is None
        assert result.continuation is None


def test_search_is_a_capability_refusal_and_never_a_message_absence(served):
    """The two answers are distinguished by their SHAPE, not by comparing a
    token to a reason. A refusal carries no coverage and no items; an empty read
    carries both. A consumer therefore cannot read one as the other, whatever
    the values happen to be."""
    refused = served.run(SEARCH, None)
    empty = served.run(RECENT, request_for(RECENT, 5, since=10 ** 9))
    assert refused.kind == REFUSAL
    assert refused.state == UNSUPPORTED_CAPABILITY
    assert refused.coverage is None
    assert refused.items == ()
    assert empty.kind == ITEMS
    assert empty.items == ()
    assert empty.coverage.status == ms.COVERAGE_COMPLETE


# --- compatibility ------------------------------------------------------------

def test_a_changed_generation_is_refused_and_publishes_nothing(tmp_path):
    """The sealed whole-source refusal, unchanged by anything above it."""
    part = fixtures.readable_part(tmp_path, "message_0.db", ROOM, six())
    connection = sqlite3.connect(str(part.path))
    try:
        connection.execute(
            'ALTER TABLE "%s" DROP COLUMN "source"'
            % fixtures.conversation_table(ROOM))
        connection.commit()
    finally:
        connection.close()

    served = DatabaseQuery(provider_over([part]))
    for operation in SERVED_OPERATIONS:
        result = served.run(operation, request_for(operation))
        assert_refused(result, UNSUPPORTED_GENERATION)
        # Not one row of the changed generation escapes, even though the parser
        # could still find most of it.
        assert result.items == ()


def test_a_part_that_will_not_open_is_accounted_not_forgotten(tmp_path):
    parts = [fixtures.readable_part(tmp_path, "message_0.db", ROOM, six()),
             fixtures.unopenable_part("message_1.db")]
    result = DatabaseQuery(provider_over(parts)).run(
        CONVERSATION, request_for(CONVERSATION, MAX_READ_LIMIT))
    # A part the request required could not be read: the source reports that
    # through the existing inventory reason rather than dropping it silently.
    assert result.kind == ITEMS
    assert result.coverage.reason == ms.REASON_PARTIAL_INVENTORY


def test_an_unknown_part_is_accounted_rather_than_forgotten(tmp_path):
    parts = [fixtures.readable_part(tmp_path, "message_0.db", ROOM, six()),
             fixtures.unknown_name_part(tmp_path, ROOM, six())]
    result = DatabaseQuery(provider_over(parts)).run(
        CONVERSATION, request_for(CONVERSATION, MAX_READ_LIMIT))
    assert result.coverage.reason == ms.REASON_PARTIAL_INVENTORY


def test_the_query_layer_never_classifies_a_schema():
    """Compatibility is consumed, never recomputed.

    The one compatibility name the surface may touch is the exception a refused
    generation raises; no classifier, no outcome vocabulary and no gap mapping
    appears here, so there is no second schema judgement to disagree with the
    one below.
    """
    tree = ast.parse(QUERY_SOURCE.read_text(encoding="utf-8"))
    prose = prose_constants(tree)
    forbidden = {
        "assess_schema", "classify_shard_name", "gaps_for", "is_valid_empty_message_part",
        "table_columns", "conversation_tables", "SchemaCompatibilityReport",
        "COMPATIBLE", "MALFORMED", "INCOMPLETE", "EMPTY", "UNASSESSED",
        "UNSUPPORTED", "COMPATIBILITY_STATES", "REQUIRED_MESSAGE_ROLES",
        "gaps", "counts", "required_gap", "role_of", "outcome_of",
        "PRAGMA", "sqlite_master", "SUPPORTED_CONVERSATION_COLUMNS",
        "require_supported_surface",
    }
    offenders = []
    for node in ast.walk(tree):
        if id(node) in prose:
            continue
        # An import is checked before the name walk, and separately from it,
        # because a name guard alone can be defeated by an alias: binding a
        # classifier to a private name puts no forbidden identifier in the
        # tree while still giving the surface a schema judgement to call. The
        # module name an import brings in cannot be renamed away, so the whole
        # compatibility module is admitted once -- for the single exception the
        # guard below then names -- and no part of it may enter by any name.
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith(
                "compatibility"):
            imported = [alias.name for alias in node.names]
            if imported != ["UnsupportedGeneration"]:
                offenders.append("from " + node.module + " " + ",".join(imported))
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[-1] in forbidden:
                    offenders.append("import " + alias.name)
        if isinstance(node, ast.Name) and node.id in forbidden:
            offenders.append(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in forbidden:
            offenders.append(node.attr)
    assert offenders == [], offenders


# --- error and privacy boundary ----------------------------------------------

@pytest.mark.parametrize("operation", SERVED_OPERATIONS)
def test_every_query_lands_in_exactly_one_kind(served, operation):
    """No None, no exception escaping, no object beside a hidden error."""
    result = served.run(operation, request_for(operation))
    assert result.kind in {ITEMS, REFUSAL}
    assert result.ok() is (result.kind == ITEMS)
    if result.ok():
        assert result.state is None
        assert result.coverage is not None
    else:
        assert result.state in ERROR_TOKENS
        assert result.coverage is None


@pytest.mark.parametrize("boom", [
    sqlite3.OperationalError(
        "unable to open database file: /Users/fixture/Containers/x.db"),
    sqlite3.DatabaseError("file is not a database: Msg_0 (Name2Id)"),
])
def test_a_sqlite_failure_becomes_a_fixed_token_and_leaks_nothing(
        readable, monkeypatch, boom):
    """SQLite's own text, and the path inside it, stop at this boundary."""
    provider = provider_over(readable)

    def explode(*args, **kwargs):
        raise boom

    monkeypatch.setattr(discovery_module.ShardDiscovery, "_probe_one",
                        lambda self, key, entry: explode())
    result = DatabaseQuery(provider).run(LIST, ListConversationsQuery(limit=5))
    assert_refused(result, SOURCE_UNAVAILABLE)
    rendered = repr(result)
    for leak in ("unable to open", "/Users/", "Containers", "x.db",
                 "OperationalError", "DatabaseError", "sqlite3", "database file",
                 "Msg_0", "Name2Id"):
        assert leak not in rendered, leak


def test_an_unclassifiable_failure_becomes_one_token_and_nothing_else(
        readable, monkeypatch):
    provider = provider_over(readable)

    def explode(*args, **kwargs):
        raise RuntimeError("Msg_0 local_id 42 for wxid_fixture_alpha in /tmp")

    monkeypatch.setattr(provider, "_locator",
                        type("L", (), {"entries": lambda self: explode()})())
    result = DatabaseQuery(provider).run(LIST, ListConversationsQuery(limit=5))
    assert_refused(result, INTERNAL_ERROR)
    rendered = repr(result)
    for leak in ("Msg_0", "local_id", "wxid", "/tmp", "RuntimeError"):
        assert leak not in rendered, leak


def test_the_error_vocabulary_is_closed_and_each_token_has_its_own_sentence():
    assert set(query_module.ERROR_STATES) == set(ERROR_TOKENS)
    assert set(query_module.ERROR_DETAILS) == set(ERROR_TOKENS)
    sentences = {query_module.ERROR_DETAILS[token] for token in ERROR_TOKENS}
    assert len(sentences) == len(ERROR_TOKENS)
    for sentence in sentences:
        for leak in ("/", "SELECT", "sqlite", "Msg_", ".db", "{"):
            assert leak not in sentence, (sentence, leak)


def test_a_refusal_state_is_the_only_thing_a_caller_can_read_from_it(served):
    """No exception escapes, and the detail is one fixed sentence per token."""
    result = served.run("SELECT 1", None)
    assert result.state == INVALID_QUERY
    assert result.detail == query_module.ERROR_DETAILS[INVALID_QUERY]


def test_the_surface_finds_no_database_and_lists_no_directory():
    """No locator, no listing, no search, no path anywhere in the surface."""
    tree = ast.parse(QUERY_SOURCE.read_text(encoding="utf-8"))
    prose = prose_constants(tree)
    forbidden_calls = {"listdir", "scandir", "iterdir", "glob", "iglob", "rglob",
                       "walk", "system", "Popen", "connect", "resolve", "isfile",
                       "exists", "open", "opendir", "listdirs"}
    # sqlite3 is imported for one thing: naming the exception class that becomes
    # a token. Everything the module could do with a database -- connect,
    # execute, a path, a directory -- is still forbidden by name above.
    forbidden_modules = {"subprocess", "glob", "shutil", "ctypes", "pathlib"}
    # The import guard that finds the bridge tree is the one place os and sys
    # appear, and it runs only when the flat import fails. It is excluded so the
    # check is about what the surface can do, not about how it was located.
    #
    # Only the path-resolution half of that fallback is exempt. The bodies of
    # the handlers in run() are NOT exempt: exempting whole ExceptHandler bodies
    # left the one place SQLite error text is handled outside the check, so a
    # handler that opened a path or printed a table name would have passed. What
    # is skipped here is the statements that compute the bridge directory, and
    # nothing else.
    fallbacks = set()
    for handler in ast.walk(tree):
        if isinstance(handler, ast.ExceptHandler) and handler.name == "ImportError":
            fallbacks.update(id(statement) for statement in ast.walk(handler))
    offenders = []
    for node in ast.walk(tree):
        if id(node) in prose or id(node) in fallbacks:
            continue
        if isinstance(node, ast.Import):
            offenders.extend("import " + alias.name for alias in node.names
                             if alias.name.split(".")[0] in forbidden_modules)
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in forbidden_modules:
                offenders.append("from " + (node.module or ""))
        elif isinstance(node, ast.Attribute) and node.attr in forbidden_calls:
            offenders.append("." + node.attr)
        elif isinstance(node, ast.Name) and node.id in forbidden_calls:
            offenders.append(node.id)
    assert offenders == [], offenders


def test_the_surface_names_no_sql_even_in_a_literal():
    tree = ast.parse(QUERY_SOURCE.read_text(encoding="utf-8"))
    prose = prose_constants(tree)
    markers = ("SELECT ", "FROM ", "WHERE", "ORDER BY", "PRAGMA", "INSERT",
               "DELETE", "UPDATE", "sqlite3", ".db", "MATCH", "JOIN")
    offenders = []
    for node in ast.walk(tree):
        if id(node) in prose:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for marker in markers:
                if marker in node.value:
                    offenders.append((marker, node.value))
    assert offenders == [], offenders


def test_the_surface_takes_no_path_argument_anywhere_in_its_signature():
    """A path is not a field on any request, so it cannot be forwarded."""
    for request_class in query_module.QUERY_CLASSES.values():
        fields = {
            name for name in request_class.__dataclass_fields__
        }
        assert not fields & {"path", "handle", "file", "directory", "root",
                             "sql", "table", "column", "where", "order_by"}


# --- determinism and identity -------------------------------------------------

def test_the_same_query_against_the_same_source_is_stable(readable):
    query = DatabaseQuery(provider_over(readable))
    request = request_for(CONVERSATION, MAX_READ_LIMIT)
    first = query.run(CONVERSATION, request)
    second = query.run(CONVERSATION, request)
    assert [item.id for item in first.items] == [item.id for item in second.items]
    assert [item.sequence for item in first.items] == [
        item.sequence for item in second.items]
    assert first.coverage == second.coverage


def test_inventory_ordering_does_not_change_the_answer(tmp_path):
    """The same two parts, handed over in either order, answer identically."""
    messages = six()
    parts = fixtures.split_conversation(tmp_path, ROOM, messages, stem="message")
    request = request_for(CONVERSATION, MAX_READ_LIMIT)
    assert [item.id for item in DatabaseQuery(provider_over(parts)).run(
        CONVERSATION, request).items] == [
        item.id for item in DatabaseQuery(provider_over(list(reversed(parts)))).run(
        CONVERSATION, request).items]


def test_a_conversation_split_across_parts_keeps_identity_semantics(readable):
    """One conversation over two parts is one ordered sequence with no repeat."""
    result = DatabaseQuery(provider_over(readable)).run(
        CONVERSATION, request_for(CONVERSATION, MAX_READ_LIMIT))
    ids = [item.id for item in result.items]
    sequences = [item.sequence for item in result.items]
    assert len(set(ids)) == len(ids)
    assert len(set(sequences)) == len(sequences)
    assert sequences == sorted(sequences)


def test_one_query_leaves_nothing_behind_for_the_next(served):
    """A cursor or a scope established by one query accumulates nothing."""
    page = served.run(CONVERSATION, request_for(CONVERSATION, 2))
    assert page.coverage.truncated is True
    whole = served.run(CONVERSATION, request_for(CONVERSATION, MAX_READ_LIMIT))
    assert len(whole.items) == 6
    assert whole.coverage.truncated is False
