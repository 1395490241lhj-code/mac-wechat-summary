"""P17b: everything that is not a clean success, and what the provider says."""

from __future__ import annotations

import message_source as ms
from conversation_identity import conversation_identifier
from wechatdb.provider import (
    NAME_ROOM_MEMBER,
    NameCandidate,
    ProviderDiagnostics,
    ShardEntry,
)
from wechatdb.provider import provider as provider_module
from wechatdb.provider import result as result_module

from . import fixtures
from .fixtures import ALPHA, BETA, ROOM, SyntheticMessage as M
from .test_provider_reads import (
    empty_window_source,
    inject_partial_read,
    provider_over,
    six,
)

CID = conversation_identifier(ROOM)


def read(provider, *args, **kwargs):
    return provider.get_messages(*args, **kwargs)


def count_opens(provider):
    """How often each part was opened, by entry name. Probing opens once."""
    opens: dict[str, int] = {}
    real = provider._opener

    class Counting:
        def open(self, entry: ShardEntry):
            opens[entry.name] = opens.get(entry.name, 0) + 1
            return real.open(entry)

    provider._opener = Counting()
    return opens


def two_parts(tmp_path):
    return fixtures.split_conversation(tmp_path, ROOM, six(), stem="message")


# -- T-2 -------------------------------------------------------------------------

def test_an_unknown_intersecting_part_changes_the_claim_not_the_content(tmp_path):
    parts = two_parts(tmp_path)
    clean = read(provider_over(parts), CID, 50)
    unknown = fixtures.unknown_name_part(tmp_path, ROOM, [M(99, 350, ALPHA, "unseen")])
    p = provider_over(parts + [unknown])
    got = read(p, CID, 50)
    assert got.items == clean.items and len(got.items) == 6     # nothing disappears
    c = got.coverage
    assert (c.status, c.reason) == (ms.COVERAGE_PARTIAL, ms.REASON_PARTIAL_INVENTORY)
    assert c.truncated is False
    assert c.observed_through == 600 and c.complete_through is None
    assert p.diagnostics == ProviderDiagnostics(
        readable=2, unknown=1, unavailable=0, unresolved_identities=0)


# -- T-3 -------------------------------------------------------------------------

def test_an_unavailable_part_still_returns_the_readable_parts(tmp_path):
    parts = two_parts(tmp_path)
    variants = {
        "will not open": fixtures.unopenable_part("message_9.db"),
        "unrecognised schema": fixtures.unrecognised_schema_part(tmp_path, "message_8.db"),
    }
    for label, broken in variants.items():
        p = provider_over(parts + [broken])
        got = read(p, CID, 50)
        assert [m.id for m in got.items] == [1, 2, 3, 4, 5, 6], label
        assert got.coverage.status == ms.COVERAGE_PARTIAL, label
        assert got.coverage.reason == ms.REASON_PARTIAL_INVENTORY, label
        assert p.diagnostics.unavailable == 1 and p.diagnostics.readable == 2, label
        recent = p.get_recent_messages(0.0, 50)
        assert len(recent.items) == 6 and recent.coverage.reason == ms.REASON_PARTIAL_INVENTORY
        listed = p.list_conversations(5)
        assert len(listed.items) == 1 and listed.coverage.reason == ms.REASON_PARTIAL_INVENTORY


# -- T-5 -------------------------------------------------------------------------

def descending(tmp_path):
    newer = fixtures.readable_part(tmp_path, "message_0.db", ROOM, [
        M(4, 400, ALPHA, "d"), M(5, 500, BETA, "e"), M(6, 600, ALPHA, "f")])
    older = fixtures.readable_part(tmp_path, "message_1.db", ROOM, [
        M(1, 100, ALPHA, "a"), M(2, 200, BETA, "b")])
    return [newer, older]


def test_a_safe_early_stop_preserves_a_correct_caller_limited_answer(tmp_path, monkeypatch):
    parts = descending(tmp_path)
    stops = []
    real = result_module.ProviderResult.collapse

    def spy(contributions, **kwargs):
        stops.append((kwargs["stop"], sum(len(c.records) for c in contributions)))
        return real(contributions, **kwargs)

    monkeypatch.setattr(provider_module.ProviderResult, "collapse", staticmethod(spy))
    p = provider_over(parts)
    opens = count_opens(p)
    got = read(p, CID, 2)
    assert opens == {"message_0.db": 2, "message_1.db": 1}     # probed, never read
    assert stops == [("safe", 3)]                              # limit + 1, measured
    c = got.coverage
    assert (c.status, c.reason) == (ms.COVERAGE_PARTIAL, ms.REASON_CALLER_LIMIT)
    assert c.truncated is True and c.observed_through == 600
    assert c.reason != ms.REASON_WINDOW_BOUND
    assert len(got.items) == c.item_count == 2
    # Exactly the top-N a full traversal produces.
    monkeypatch.setattr(provider_module.ShardRouter, "classify_stop",
                        lambda self, *a, **k: "unsafe")
    full = provider_over(parts)
    full_opens = count_opens(full)
    everything = full.get_messages(CID, 50)
    assert full_opens["message_1.db"] == 2
    assert got.items == everything.items[-2:]


# -- T-6 -------------------------------------------------------------------------

def test_an_unsafe_early_stop_is_partial(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_module, "_PART_VISIT_BOUND", 1)
    unbounded = [fixtures.unbounded_part(tmp_path, ROOM, name=f"message_{i}.db")
                 for i in (1, 2)]
    overlapping = [
        fixtures.readable_part(tmp_path, "message_5.db", ROOM, [
            M(4, 300, ALPHA, "d"), M(5, 350, BETA, "e"), M(6, 400, ALPHA, "f")]),
        fixtures.readable_part(tmp_path, "message_6.db", ROOM, [
            M(7, 350, ALPHA, "g"), M(8, 380, BETA, "h")]),
    ]
    variants = {
        "unvisited part has no established bounds": (unbounded + overlapping[:1], 50),
        "unvisited maximum at or after the boundary": (overlapping, 1),
    }
    for label, (parts, limit) in variants.items():
        got = read(provider_over(parts), CID, limit)
        c = got.coverage
        assert (c.status, c.reason) == (ms.COVERAGE_PARTIAL, ms.REASON_UNSAFE_EARLY_STOP), label
        assert c.truncated is True, label
        assert c.observed_through is None and c.complete_through is None, label
        assert c.item_count == len(got.items), label


# -- T-7 -------------------------------------------------------------------------

def test_a_source_newer_than_the_read_downgrades_freshness(tmp_path):
    parts = two_parts(tmp_path)
    inside = read(provider_over(parts, source_newest=650.5), CID, 50).coverage
    assert (inside.status, inside.reason, inside.freshness) == (
        ms.COVERAGE_PARTIAL, ms.REASON_TIMESTAMP_MISMATCH,
        ms.ReadFreshness.POTENTIALLY_STALE)
    assert inside.truncated is False
    outside = read(provider_over(parts, source_newest=650.5), CID, 50,
                   requested_end=620.25).coverage
    assert (outside.status, outside.freshness) == (
        ms.COVERAGE_COMPLETE, ms.ReadFreshness.POTENTIALLY_STALE)
    consistent = read(provider_over(parts, source_newest=600.0), CID, 50).coverage
    assert (consistent.status, consistent.freshness) == (
        ms.COVERAGE_COMPLETE, ms.ReadFreshness.EVIDENCE_CONSISTENT)
    absent = read(provider_over(parts), CID, 50).coverage
    assert (absent.status, absent.freshness) == (
        ms.COVERAGE_COMPLETE, ms.ReadFreshness.UNKNOWN)


# -- T-10 ------------------------------------------------------------------------

def test_zero_messages_with_an_unavailable_part_is_not_trustworthy(tmp_path):
    # Pair-partner: test_provider_reads.py ::
    # test_zero_messages_with_every_part_readable_is_a_trustworthy_empty (T-9).
    # Same fixture, same bounds, same items; the opposite conclusion.
    parts, window = empty_window_source(tmp_path)
    trusted = provider_over(parts).get_messages(CID, 50, **window)
    got = read(provider_over(parts + [fixtures.unopenable_part("message_9.db")]),
               CID, 50, **window)
    assert got.items == trusted.items == ()
    c, t = got.coverage, trusted.coverage
    assert (c.requested_start, c.requested_end) == (t.requested_start, t.requested_end)
    assert (t.status, t.reason) == (ms.COVERAGE_COMPLETE, ms.REASON_EMPTY_WINDOW)
    assert (c.status, c.reason) == (ms.COVERAGE_PARTIAL, ms.REASON_PARTIAL_INVENTORY)
    assert c.item_count == 0


# -- T-11 ------------------------------------------------------------------------

def test_the_provider_truncating_internally_is_never_complete(tmp_path, monkeypatch):
    inject_partial_read(monkeypatch, "message_0.db", keep=3,
                        observed_through=600, complete_through=300)
    part = fixtures.readable_part(tmp_path, "message_0.db", ROOM, six())
    p = provider_over([part, fixtures.unopenable_part("message_9.db")])
    got = read(p, CID, 200)
    assert len(got.items) == 3                  # far below the caller's limit
    c = got.coverage
    assert (c.status, c.reason) == (ms.COVERAGE_PARTIAL, ms.REASON_SOURCE_LIMIT)
    assert c.truncated is True
    # The source cut wins the reason; the gap is still counted, and still caps.
    assert c.complete_through is None
    assert p.diagnostics == ProviderDiagnostics(
        readable=1, unknown=0, unavailable=1, unresolved_identities=0)


# -- identity diagnostics --------------------------------------------------------

def test_ambiguity_events_are_summed_across_the_rooms_a_read_resolves(tmp_path):
    other = "fixture_room_0002@chatroom"
    other_messages = [
        M(i, 1_000 + 100 * i, ALPHA if i % 2 else BETA, f"other fixture {i}",
          server_id=1_000 + i)
        for i in range(1, 7)
    ]
    parts = [fixtures.readable_part(tmp_path, "message_0.db", ROOM, six()),
             fixtures.readable_part(tmp_path, "message_1.db", other, other_messages)]
    candidates = [NameCandidate(ALPHA, NAME_ROOM_MEMBER, name, room=room)
                  for room in (ROOM, other) for name in ("One", "Another")]
    p = provider_over(parts, conversations=(ROOM, other), candidates=candidates)
    got = p.get_recent_messages(0.0, 50)
    assert p.diagnostics.unresolved_identities == 2      # one identifier, two events
    assert got.coverage.status == ms.COVERAGE_COMPLETE   # identity is not coverage
    assert {m.sender for m in got.items} == {ALPHA, BETA}
    p.get_messages(CID, 50)
    assert p.diagnostics.unresolved_identities == 1      # per read, never carried


def test_one_ambiguous_room_in_two_parts_is_two_events(tmp_path):
    # Events, not distinct identifiers: the room is resolved once per part read.
    parts = two_parts(tmp_path)
    candidates = [NameCandidate(ALPHA, NAME_ROOM_MEMBER, name, room=ROOM)
                  for name in ("One", "Another")]
    p = provider_over(parts, candidates=candidates)
    calls = []
    real = p._names

    def spy(**kwargs):
        resolved = real(**kwargs)
        if kwargs.get("room") is not None:
            calls.append(resolved.unresolved)
        return resolved

    p._names = spy
    p.get_messages(CID, 50)
    assert calls == [1, 1]
    assert p.diagnostics.unresolved_identities == 2


# -- T-18 (standalone: builds every scenario itself) ------------------------------

def test_every_reason_token_this_provider_emits_is_in_the_closed_set(tmp_path, monkeypatch):
    parts, window = empty_window_source(tmp_path)
    gap = parts + [fixtures.unopenable_part("message_9.db")]
    emitted = {
        read(provider_over(parts), CID, 50).coverage.reason,
        read(provider_over(parts), CID, 50, **window).coverage.reason,
        read(provider_over(parts), CID, 2).coverage.reason,
        read(provider_over(gap), CID, 50).coverage.reason,
        read(provider_over(parts, source_newest=650.5), CID, 50).coverage.reason,
    }
    with monkeypatch.context() as cut:
        inject_partial_read(cut, "message_0.db", keep=1,
                            observed_through=500, complete_through=100)
        emitted.add(read(provider_over(parts), CID, 50).coverage.reason)
    with monkeypatch.context() as bound:
        bound.setattr(provider_module, "_PART_VISIT_BOUND", 1)
        emitted.add(read(provider_over(parts), CID, 50).coverage.reason)
    assert emitted <= ms.COVERAGE_REASONS
    assert emitted == {
        ms.REASON_FULL_WINDOW_OBSERVED, ms.REASON_EMPTY_WINDOW, ms.REASON_CALLER_LIMIT,
        ms.REASON_SOURCE_LIMIT, ms.REASON_PARTIAL_INVENTORY,
        ms.REASON_UNSAFE_EARLY_STOP, ms.REASON_TIMESTAMP_MISMATCH}
    for reason in emitted:
        assert ms.REASON_STATUSES[reason]
