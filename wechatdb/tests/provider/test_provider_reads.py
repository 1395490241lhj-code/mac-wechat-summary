"""P17a: the smallest successful orchestration. Every part here is readable.

The degradation matrix lives in ``test_provider_degradation.py``; its T-10 is
the pair-partner of T-9 below and shares :func:`empty_window_source`.
"""

from __future__ import annotations

import ast
import inspect

import pytest

import message_source as ms
from conversation_identity import conversation_identifier
from wechatdb.provider import ExplicitShardLocator, IdentityResolver, ShardEntry
from wechatdb.provider import result as result_module

from . import fixtures
from .fixtures import ALPHA, BETA, ROOM, SyntheticMessage as M

from wechatdb.provider import provider as provider_module  # noqa: E402
from wechatdb.provider import ShardedMessageProvider  # noqa: E402


def entry(part):
    return ShardEntry(name=part.name, handle=part.path)


def digest_of(conversation):
    # Test-side only: the session-name mapping arrives parser-shaped.
    return fixtures.conversation_table(conversation)[len("Msg_"):]


def provider_over(parts, *, conversations=(ROOM,), candidates=(), source_newest=None):
    identities = IdentityResolver(
        candidates, session_names={digest_of(c): c for c in conversations})
    return ShardedMessageProvider(
        ExplicitShardLocator([entry(p) for p in parts]),
        identities=identities, source_newest=source_newest)


def inject_partial_read(monkeypatch, part_name, *, keep, observed_through,
                        complete_through):
    """Synthetic evidence at the provider-local read boundary, test-only.

    The real part read runs; for the chosen part its answer is replaced by a
    real ``_PartRead`` that says it was cut short. Everything downstream --
    the real Contribution, the real collapse -- is production.
    """
    real = ShardedMessageProvider._read_part

    def patched(self, part_entry, *args, **kwargs):
        whole = real(self, part_entry, *args, **kwargs)
        if part_entry.name != part_name:
            return whole
        return provider_module._PartRead(
            records=whole.records[:keep], observed_through=observed_through,
            truncated=True, complete_through=complete_through)

    monkeypatch.setattr(ShardedMessageProvider, "_read_part", patched)


def six():
    return [M(i, 100 * i, ALPHA if i % 2 else BETA, f"fixture text {i}", server_id=i)
            for i in range(1, 7)]


def empty_window_source(tmp_path):
    """Shared by T-9 here and T-10 in test_provider_degradation: the readable
    parts, and the requested bounds that contain none of their messages."""
    parts = fixtures.split_conversation(tmp_path, ROOM, six(), stem="message")
    return parts, {"requested_start": 250.5, "requested_end": 290.5}


# -- construction ---------------------------------------------------------------

def test_the_provider_constructs_from_an_injected_locator_and_reads_nothing_else(tmp_path):
    class Counting:
        calls = 0

        def entries(self):
            Counting.calls += 1
            return ()

    p = ShardedMessageProvider(Counting(), identities=IdentityResolver(()))
    assert Counting.calls == 0          # construction touches nothing
    assert p.name == ms.SOURCE_DATABASE
    got = p.get_recent_messages(0.0, 5)
    assert Counting.calls >= 1 and got.items == ()
    signature = inspect.signature(ShardedMessageProvider.__init__)
    assert list(signature.parameters) == ["self", "locator", "identities", "source_newest"]
    public = {n for n in vars(ShardedMessageProvider) if not n.startswith("_")}
    assert public == {"name", "list_conversations", "get_messages", "get_recent_messages"}
    tree = ast.parse(inspect.getsource(provider_module))
    roots = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)
             for a in n.names}
    roots |= {(n.module or "").split(".")[0] for n in ast.walk(tree)
              if isinstance(n, ast.ImportFrom) and n.level == 0}
    assert not roots & {"os", "pathlib", "glob", "subprocess", "rion_reader_adapter",
                        "store_access", "hashlib", "time", "datetime"}


# -- T-1 -------------------------------------------------------------------------

def test_all_parts_readable_over_the_full_window(tmp_path):
    parts = fixtures.split_conversation(tmp_path, ROOM, six(), parts=3, stem="message")
    p = provider_over(parts)
    got = p.get_messages(conversation_identifier(ROOM), 50)
    assert isinstance(got, ms.ReadResult)
    assert [m.id for m in got.items] == [1, 2, 3, 4, 5, 6]     # each exactly once
    c = got.coverage
    assert (c.status, c.reason) == (ms.COVERAGE_COMPLETE, ms.REASON_FULL_WINDOW_OBSERVED)
    assert c.truncated is False and c.item_count == 6
    assert c.complete_through == c.observed_through == 600
    assert all(m.source == ms.SOURCE_DATABASE and m.ownership == "unknown"
               for m in got.items)
    recent = p.get_recent_messages(250.5, 50)
    assert [m.id for m in recent.items] == [3, 4, 5, 6]
    assert recent.coverage.requested_start == 250.5
    assert recent.coverage.status == ms.COVERAGE_COMPLETE


# -- T-4a ------------------------------------------------------------------------

def test_a_conversation_spanning_parts_merges_in_order(tmp_path):
    parts = fixtures.split_conversation(tmp_path, ROOM, six(), stem="message")
    # No part holds the whole sequence.
    assert all(len(fixtures.parse_part(x, ROOM)) == 3 for x in parts)
    p = provider_over(parts)
    cid = conversation_identifier(ROOM)
    got = p.get_messages(cid, 50)
    assert [m.first_observed_at for m in got.items] == [100, 200, 300, 400, 500, 600]
    assert {m.conversation_id for m in got.items} == {cid}
    c = got.coverage
    assert c.status == ms.COVERAGE_COMPLETE
    assert c.complete_through == c.observed_through == 600
    # before_sequence narrows by the public timestamp/local-id sequence, and
    # the limit keeps the newest, oldest first.
    older = p.get_messages(cid, 2, before_sequence=(500 << 31) | 5)
    assert [m.id for m in older.items] == [3, 4]
    assert older.coverage.reason == ms.REASON_CALLER_LIMIT
    assert older.coverage.item_count == 2 == len(older.items)


def test_paging_uses_public_sequence_when_local_ids_restart_in_each_part(tmp_path):
    older = fixtures.readable_part(tmp_path, "message_0.db", ROOM, [
        M(1, 100, ALPHA, "older one", server_id=101),
        M(2, 200, BETA, "older two", server_id=102),
    ])
    newer = fixtures.readable_part(tmp_path, "message_1.db", ROOM, [
        M(1, 300, ALPHA, "newer one", server_id=103),
        M(2, 400, BETA, "newer two", server_id=104),
    ])
    provider = provider_over([older, newer])
    conversation = conversation_identifier(ROOM)

    first = provider.get_messages(conversation, 2)
    cursor = first.items[0].sequence
    second = provider.get_messages(conversation, 2, before_sequence=cursor)

    assert [message.id for message in first.items] == [103, 104]
    assert [message.id for message in second.items] == [101, 102]
    assert {message.id for message in first.items}.isdisjoint(
        message.id for message in second.items)
    assert all(message.sequence < cursor for message in second.items)


@pytest.mark.parametrize("parts", [
    lambda tmp_path: [
        fixtures.readable_part(tmp_path, "message_0.db", ROOM,
                               [M(1, 100, ALPHA, "id collision a", server_id=99)]),
        fixtures.readable_part(tmp_path, "message_1.db", ROOM,
                               [M(2, 200, BETA, "id collision b", server_id=99)]),
    ],
    lambda tmp_path: [
        fixtures.readable_part(tmp_path, "message_0.db", ROOM,
                               [M(1, 100, ALPHA, "sequence collision a", server_id=101)]),
        fixtures.readable_part(tmp_path, "message_1.db", ROOM,
                               [M(1, 100, BETA, "sequence collision b", server_id=102)]),
    ],
])
def test_public_message_identity_collisions_fail_closed_with_fixed_copy(tmp_path, parts):
    with pytest.raises(ValueError, match="^message identity collision$") as refusal:
        provider_over(parts(tmp_path)).get_messages(conversation_identifier(ROOM), 50)
    assert "collision a" not in str(refusal.value)


def test_the_sentinel_reaches_collapse_before_the_public_trim(tmp_path, monkeypatch):
    parts = fixtures.split_conversation(tmp_path, ROOM, six(), stem="message")
    seen = {}
    real = result_module.ProviderResult.collapse

    def spy(contributions, **kwargs):
        parts_ = tuple(contributions)
        assert all(type(x) is result_module.Contribution for x in parts_)
        seen["count"] = sum(len(x.records) for x in parts_)
        seen["limit"] = kwargs["caller_limit"]
        return real(parts_, **kwargs)

    monkeypatch.setattr(provider_module.ProviderResult, "collapse", staticmethod(spy))
    got = provider_over(parts).get_messages(conversation_identifier(ROOM), 2)
    assert seen["count"] > seen["limit"] == 2
    assert len(got.items) == got.coverage.item_count == 2
    assert [m.id for m in got.items] == [5, 6]
    assert got.coverage.truncated is True


def test_names_reach_the_parser_and_the_table_name_is_passed_unchanged(tmp_path, monkeypatch):
    from wechatdb.provider import NAME_ROOM_MEMBER, NameCandidate
    parts = fixtures.split_conversation(tmp_path, ROOM, six(), stem="message")
    tables = []
    real = provider_module.wechatdb.parse_conversation

    def spy(connection, table, **kwargs):
        tables.append(table)
        return real(connection, table, **kwargs)

    monkeypatch.setattr(provider_module.wechatdb, "parse_conversation", spy)
    p = provider_over(parts, candidates=[
        NameCandidate(ALPHA, NAME_ROOM_MEMBER, "Fixture Alpha", room=ROOM)])
    got = p.get_messages(conversation_identifier(ROOM), 50)
    assert set(tables) == {fixtures.conversation_table(ROOM)}
    assert {m.sender for m in got.items} == {"Fixture Alpha", BETA}


# -- T-4b ------------------------------------------------------------------------

def test_a_conversation_spanning_parts_caps_at_the_weakest_complete_point(tmp_path, monkeypatch):
    whole = fixtures.readable_part(tmp_path, "message_0.db", ROOM,
                                   [M(1, 380, ALPHA, "a", server_id=1),
                                    M(2, 400, BETA, "b", server_id=2)])
    capped = fixtures.readable_part(tmp_path, "message_1.db", ROOM, [
        M(11, 100, ALPHA, "c", server_id=11),
        M(12, 200, BETA, "d", server_id=12),
        M(13, 300, ALPHA, "e", server_id=13),
        M(14, 350, BETA, "f", server_id=14)])
    inject_partial_read(monkeypatch, "message_1.db", keep=2,
                        observed_through=350, complete_through=200)
    got = provider_over([whole, capped]).get_messages(conversation_identifier(ROOM), 50)
    c = got.coverage
    assert (c.status, c.reason) == (ms.COVERAGE_PARTIAL, ms.REASON_SOURCE_LIMIT)
    assert c.truncated is True
    assert c.observed_through == 400        # the maximum is still reported
    assert c.complete_through == 200        # the weakest contribution caps it
    assert [m.id for m in got.items] == [11, 12, 1, 2]
    assert c.item_count == 4


# -- T-9 -------------------------------------------------------------------------

def test_zero_messages_with_every_part_readable_is_a_trustworthy_empty(tmp_path):
    # Pair-partner: test_provider_degradation.py ::
    # test_zero_messages_with_an_unavailable_part_is_not_trustworthy (T-10).
    parts, window = empty_window_source(tmp_path)
    got = provider_over(parts).get_messages(conversation_identifier(ROOM), 50, **window)
    assert got.items == ()
    c = got.coverage
    assert (c.status, c.reason) == (ms.COVERAGE_COMPLETE, ms.REASON_EMPTY_WINDOW)
    assert (c.requested_start, c.requested_end) == (250.5, 290.5)
    assert c.truncated is False and c.item_count == 0


# -- list_conversations ---------------------------------------------------------

def test_list_conversations_answers_from_the_same_orchestration(tmp_path):
    other = "wxid_fixture_gamma"
    parts = fixtures.split_conversation(tmp_path, ROOM, six(), stem="message")
    parts.append(fixtures.readable_part(tmp_path, "message_7.db", other,
                                        [M(1, 900, ALPHA, "later")]))
    p = provider_over(parts, conversations=(ROOM, other))
    listed = p.list_conversations(10)
    assert [(c.id, c.title, c.last_seen_at) for c in listed.items] == [
        (conversation_identifier(other), other, 900),
        (conversation_identifier(ROOM), ROOM, 600)]
    assert all(c.source == ms.SOURCE_DATABASE for c in listed.items)
    assert listed.coverage.status == ms.COVERAGE_COMPLETE
    assert listed.coverage.item_count == 2
    # A listed conversation and its messages cannot disagree.
    for conversation in listed.items:
        messages = p.get_messages(conversation.id, 50).items
        assert messages and {m.conversation_id for m in messages} == {conversation.id}
    # An unmapped table still lists, under the parser's own fallback key.
    bare = provider_over(parts, conversations=()).list_conversations(10)
    assert all(c.title.startswith("msg_") for c in bare.items)
    assert {c.id for c in bare.items} == {conversation_identifier(c.title) for c in bare.items}
    cut = p.list_conversations(1)
    assert len(cut.items) == cut.coverage.item_count == 1
    assert cut.coverage.reason == ms.REASON_CALLER_LIMIT


# -- sealing regressions -----------------------------------------------------------

def test_one_read_takes_the_locator_exactly_once(tmp_path):
    parts = fixtures.split_conversation(tmp_path, ROOM, six(), stem="message")

    class OnceOnly:
        calls = 0

        def entries(self):
            OnceOnly.calls += 1
            if OnceOnly.calls > 1:
                raise AssertionError("a read took the locator twice")
            return tuple(entry(x) for x in parts)

    p = ShardedMessageProvider(OnceOnly(), identities=IdentityResolver(
        (), session_names={digest_of(ROOM): ROOM}))
    opened = []
    real = p._opener

    class Recording:
        def open(self, e):
            opened.append(e)
            return real.open(e)

    p._opener = Recording()
    got = p.get_messages(conversation_identifier(ROOM), 50)
    assert OnceOnly.calls == 1
    assert [m.id for m in got.items] == [1, 2, 3, 4, 5, 6]
    # Probing and reading opened the very entries of that one snapshot.
    assert {e.name for e in opened} == {x.name for x in parts} and len(opened) == 4


def test_a_limited_read_returns_the_newest_window(tmp_path, monkeypatch):
    # No provider post-read record cap exists that could turn [5, 6] into [2, 3].
    monkeypatch.setattr(provider_module, "_PART_RECORD_BOUND", 3, raising=False)
    part = fixtures.readable_part(tmp_path, "message_0.db", ROOM, six())
    got = provider_over([part]).get_messages(conversation_identifier(ROOM), 2)
    assert [m.id for m in got.items] == [5, 6]
    assert got.coverage.reason == ms.REASON_CALLER_LIMIT
    assert "_PART_RECORD" not in inspect.getsource(provider_module)
