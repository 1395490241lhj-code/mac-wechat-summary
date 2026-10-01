"""The internal logical-conversation scope: one explicit source, one group.

A logical conversation is an additive grouping relation over snapshot
canonical identities that stay permanent row-level provenance. This file pins
the smallest honest query primitive over it:

* the logical id is a *selector* and always requires one explicit source, so
  no cross-source coverage is ever synthesized;
* membership resolves through the existing ``observations_of`` link layer,
  is intersected with the requested source, drops dangling links, and the
  *same* surviving list is what evidence and coverage are computed from;
* coverage is SEMANTIC-1 / ANY-COMPLETE **within that one source**: complete
  iff at least one surviving member's own verdict for that source is complete
  over the whole window. Two partial members never combine;
* every observation survives. There is no deduplication, no identity
  inference, and no collapsing across Archive snapshots;
* nothing here is exposed: the worker and the MCP server are unchanged, and
  canonical conversation ids never appear under a ``*_sources`` field.
"""

from __future__ import annotations

import dataclasses

import pytest

import memory_identity as identity
import memory_worker as worker
from archive_message_source import SOURCE_ARCHIVE
from conftest import conversation, visual_message
from memory_ingest import MemoryIngestor
from memory_query import MemoryQueryService
from memory_store import (
    COVERAGE_COMPLETE,
    COVERAGE_NOT_OBSERVED,
    COVERAGE_PARTIAL,
    COVERAGE_UNAVAILABLE,
    LINK_KIND_CONVERSATION,
    LINK_OPERATOR,
    CoverageRecord,
    MemoryStoreError,
)
from message_source import SOURCE_VISUAL, NormalizedMessage

BASE = 1_700_000_000.0
WINDOW = (BASE, BASE + 1_000)

#: A well-formed logical conversation id that no store has ever asserted.
UNKNOWN_LOGICAL = "logc:" + "0" * 32


def archive_message(identifier, conversation_id, text, *, at, sequence):
    """The shape an Archive import writes: a source message id, nothing inferred."""
    return NormalizedMessage(
        id=identifier,
        conversation_id=conversation_id,
        sequence=sequence,
        sender="林晓",
        ownership="other",
        visible_time=None,
        text=text,
        kind="text",
        confidence=1.0,
        first_observed_at=at,
        source=SOURCE_ARCHIVE,
    )


def conv(source, identifier):
    return identity.conversation_canonical_id(source, str(identifier))


ARCHIVE_A = conv(SOURCE_ARCHIVE, 1)
ARCHIVE_B = conv(SOURCE_ARCHIVE, 2)
ARCHIVE_C = conv(SOURCE_ARCHIVE, 3)
VISUAL_V = conv(SOURCE_VISUAL, 7)


def archive_snapshot(store, *, canonical, number, at, coverage=()):
    """One imported Archive snapshot: its own conversation, messages and claim."""
    MemoryIngestor(store).ingest(
        SOURCE_ARCHIVE,
        [archive_message(number, number, f"第{number}条", at=at, sequence=number)],
        conversations=[conversation(number, "项目组", source=SOURCE_ARCHIVE)],
        coverage=list(coverage),
        now=1_000.0,
    )
    assert store.conversation(canonical) is not None, "fixture drifted from canonical identity"


def visual_snapshot(store, *, canonical, number, at, coverage=()):
    MemoryIngestor(store).ingest(
        SOURCE_VISUAL,
        [visual_message(number, 7, f"第{number}条", observed_at=at, sequence=number)],
        conversations=[conversation(7, "项目组", source=SOURCE_VISUAL)],
        coverage=list(coverage),
        now=1_000.0,
    )
    assert store.conversation(canonical) is not None, "fixture drifted from canonical identity"


def complete(canonical, *, source=SOURCE_ARCHIVE, window=WINDOW):
    return CoverageRecord(
        source=source, status=COVERAGE_COMPLETE,
        conversation_canonical_id=canonical,
        window_start=window[0], window_end=window[1],
        message_count=1,
    )


def partial(canonical, window, *, source=SOURCE_ARCHIVE, reason="caller_limit"):
    return CoverageRecord(
        source=source, status=COVERAGE_PARTIAL,
        conversation_canonical_id=canonical,
        window_start=window[0], window_end=window[1], reason=reason,
        message_count=1,
    )


def link(store, canonical, logical=None):
    return store.link_observation(
        kind=LINK_KIND_CONVERSATION, observation_canonical_id=canonical,
        logical_id=logical, basis=LINK_OPERATOR, asserted_by="test", now=2_000.0,
    )


def service(store):
    return MemoryQueryService(store)


@pytest.fixture()
def group(store):
    """Two Archive snapshots and one Visual snapshot, explicitly one logical chat."""
    archive_snapshot(store, canonical=ARCHIVE_A, number=1, at=BASE + 10,
                     coverage=[complete(ARCHIVE_A)])
    archive_snapshot(store, canonical=ARCHIVE_B, number=2, at=BASE + 20)
    visual_snapshot(store, canonical=VISUAL_V, number=7, at=BASE + 30,
                    coverage=[complete(VISUAL_V, source=SOURCE_VISUAL)])
    logical = link(store, ARCHIVE_A)
    link(store, ARCHIVE_B, logical)
    link(store, VISUAL_V, logical)
    return store, logical


# --- the contract boundary ----------------------------------------------------


def test_logical_scope_with_one_explicit_source_is_accepted(group):
    store, logical = group
    result = service(store).recent_context(
        logical_conversation_id=logical, source=SOURCE_ARCHIVE, since=BASE, until=WINDOW[1])
    assert {i.citation.canonical_conversation_id for i in result.items} == {ARCHIVE_A, ARCHIVE_B}
    assert result.query_scope.logical_conversation_id == logical
    assert result.query_scope.conversation_canonical_id is None
    assert result.logical_coverage.status == COVERAGE_COMPLETE
    assert result.logical_coverage.trustworthy_empty


def test_logical_scope_without_a_source_refuses(group):
    store, logical = group
    with pytest.raises(MemoryStoreError) as refusal:
        service(store).recent_context(logical_conversation_id=logical, since=BASE)
    assert refusal.value.state == "logical_scope_requires_source"


def test_logical_scope_and_conversation_scope_together_refuse(group):
    store, logical = group
    with pytest.raises(MemoryStoreError) as refusal:
        service(store).recent_context(
            logical_conversation_id=logical, source=SOURCE_ARCHIVE,
            conversation_canonical_id=ARCHIVE_A, since=BASE)
    assert refusal.value.state == "logical_scope_conflicts_conversation_scope"


@pytest.mark.parametrize("absent_source", [None, ""], ids=["omitted", "empty"])
def test_logical_scope_without_a_source_refuses_however_it_is_absent(group, absent_source):
    store, logical = group
    with pytest.raises(MemoryStoreError) as refusal:
        service(store).recent_context(
            logical_conversation_id=logical, source=absent_source, since=BASE)
    assert refusal.value.state == "logical_scope_requires_source"


@pytest.mark.parametrize("malformed", [
    "logc:" + "0" * 31,          # too short
    "logc:" + "0" * 33,          # too long
    "logc:" + "A" * 32,          # not lowercase
    "logc:" + "g" * 32,          # not hexadecimal
    "logc:zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",
    "conv:" + "0" * 32,          # not a logical id at all
    "",
])
def test_a_malformed_logical_id_refuses(group, malformed):
    store, _ = group
    with pytest.raises(MemoryStoreError) as refusal:
        service(store).recent_context(
            logical_conversation_id=malformed, source=SOURCE_ARCHIVE, since=BASE)
    assert refusal.value.state == "logical_conversation_id_malformed"


def test_a_well_shaped_unknown_logical_id_is_empty_and_never_global(group):
    store, _ = group
    result = service(store).recent_context(
        logical_conversation_id=UNKNOWN_LOGICAL, source=SOURCE_ARCHIVE,
        since=BASE, until=WINDOW[1])
    assert result.items == ()
    assert result.logical_coverage.status == COVERAGE_NOT_OBSERVED
    assert not result.logical_coverage.trustworthy_empty
    assert result.logical_coverage.members == ()
    assert any("logical_conversation_unknown" in c for c in result.logical_coverage.caveats)
    # Archive really did cover this window, and the source-level report says so.
    # The point is that this must not leak into the logical answer: an unknown
    # selector reports nothing it knows and falls back to nothing.
    assert result.coverage.trustworthy_empty is True
    assert not result.logical_coverage.trustworthy_empty
    # Both truth flags on the same envelope cannot disagree about whether an
    # empty logical answer may be read as "nothing happened". The source-level
    # report never saw the selector, so it does not govern here.
    assert result.is_empty_and_trustworthy is False


# --- membership ----------------------------------------------------------------


def test_membership_resolves_through_observations_of_and_is_one_list(group, monkeypatch):
    store, logical = group
    seen = []
    original = store.observations_of
    assessed = []
    original_coverage = store.assess_coverage

    def spy(kind, logical_id):
        seen.append((kind, logical_id))
        return original(kind, logical_id)

    def coverage_spy(**kwargs):
        assessed.append(kwargs.get("conversation_canonical_id"))
        return original_coverage(**kwargs)

    monkeypatch.setattr(store, "observations_of", spy)
    monkeypatch.setattr(store, "assess_coverage", coverage_spy)
    result = service(store).recent_context(
        logical_conversation_id=logical, source=SOURCE_ARCHIVE, since=BASE)
    assert seen == [(LINK_KIND_CONVERSATION, logical)]
    members = result.logical_coverage.members
    assert members == (ARCHIVE_A, ARCHIVE_B)
    # One list, and it is the same one: every per-member coverage assessment
    # was asked about a surviving member and about nothing else. (The one
    # ``None`` assessment is the source-level policy, which has no identity.)
    assert [m for m in assessed if m is not None] == [ARCHIVE_A, ARCHIVE_B]
    # Evidence and coverage come from that same list: every returned row belongs
    # to a surviving member, and every assessed member is in it.
    assert {i.citation.canonical_conversation_id for i in result.items} <= set(members)
    assert set(result.logical_coverage.complete_members) <= set(members)


def test_a_mixed_source_group_queried_as_archive_is_archive_only(group):
    store, logical = group
    result = service(store).recent_context(
        logical_conversation_id=logical, source=SOURCE_ARCHIVE, since=BASE)
    assert {i.citation.source for i in result.items} == {SOURCE_ARCHIVE}
    assert result.logical_coverage.members == (ARCHIVE_A, ARCHIVE_B)
    # The Visual member is not missing from this query; it was never in it.
    assert not any(VISUAL_V in c for c in result.logical_coverage.caveats)
    assert not any(SOURCE_VISUAL in c for c in result.logical_coverage.caveats)


def test_a_mixed_source_group_queried_as_visual_is_visual_only(group):
    store, logical = group
    result = service(store).recent_context(
        logical_conversation_id=logical, source=SOURCE_VISUAL,
        since=BASE, until=WINDOW[1])
    assert {i.citation.source for i in result.items} == {SOURCE_VISUAL}
    assert result.logical_coverage.members == (VISUAL_V,)
    assert result.logical_coverage.status == COVERAGE_COMPLETE
    assert not any(ARCHIVE_A in c or ARCHIVE_B in c for c in result.logical_coverage.caveats)


def test_a_group_with_no_member_in_the_requested_source_is_empty(group):
    store, logical = group
    result = service(store).recent_context(
        logical_conversation_id=logical, source="database", since=BASE)
    assert result.items == ()
    assert result.logical_coverage.status == COVERAGE_NOT_OBSERVED
    assert not result.logical_coverage.trustworthy_empty
    assert any("no_database_member" in c for c in result.logical_coverage.caveats)
    assert result.coverage.trustworthy_empty is False


def test_a_dangling_link_is_dropped_but_kept_visible(store):
    archive_snapshot(store, canonical=ARCHIVE_A, number=1, at=BASE + 10,
                     coverage=[partial(ARCHIVE_A, (BASE, BASE + 500))])
    archive_snapshot(store, canonical=ARCHIVE_B, number=2, at=BASE + 20)
    # A conversation observation with no messages, so deleting it leaves the
    # store consistent: the dangling case is a vanished observation, never a
    # dangling foreign key. Its coverage claim is complete, so a store that
    # kept counting deleted members would wrongly promote the group.
    MemoryIngestor(store).ingest(
        SOURCE_ARCHIVE, [],
        conversations=[conversation(3, "项目组", source=SOURCE_ARCHIVE)],
        coverage=[complete(ARCHIVE_C)], now=1_000.0,
    )
    logical = link(store, ARCHIVE_A)
    link(store, ARCHIVE_B, logical)
    link(store, ARCHIVE_C, logical)
    assert store.conversation(ARCHIVE_C) is not None
    # The observation is gone; the assertion it was part of is not.
    store.connection.execute("DELETE FROM conversations WHERE canonical_id = ?;", (ARCHIVE_C,))

    result = service(store).recent_context(
        logical_conversation_id=logical, source=SOURCE_ARCHIVE, since=BASE)
    assert result.logical_coverage.members == (ARCHIVE_A, ARCHIVE_B)
    assert ARCHIVE_C not in result.logical_coverage.complete_members
    assert result.logical_coverage.complete_members == ()
    assert result.logical_coverage.status == COVERAGE_PARTIAL
    assert not result.logical_coverage.trustworthy_empty
    assert any("dangling_member" in c for c in result.logical_coverage.caveats)
    assert {i.citation.canonical_conversation_id for i in result.items} == {ARCHIVE_A, ARCHIVE_B}


def test_a_dangling_member_of_another_source_is_never_named(group):
    store, logical = group
    assert store.conversation(VISUAL_V) is not None
    store.connection.execute(
        "DELETE FROM messages WHERE conversation_canonical_id = ?;", (VISUAL_V,)
    )
    store.connection.execute(
        "DELETE FROM conversations WHERE canonical_id = ?;", (VISUAL_V,)
    )
    result = service(store).recent_context(
        logical_conversation_id=logical, source=SOURCE_ARCHIVE, since=BASE
    )
    assert result.logical_coverage.members == (ARCHIVE_A, ARCHIVE_B)
    # ARCHIVE_B never claimed this window, so the group is partial -- and the
    # deleted Visual member changed neither the membership nor the verdict.
    assert result.logical_coverage.status == COVERAGE_PARTIAL
    assert not any(VISUAL_V in c for c in result.logical_coverage.caveats)
    assert any("dangling_member" in c for c in result.logical_coverage.caveats)


def test_every_member_dangling_is_its_own_case(store):
    # Message-free conversations, so deleting one leaves the store consistent.
    MemoryIngestor(store).ingest(
        SOURCE_ARCHIVE, [], conversations=[conversation(1, "项目组", source=SOURCE_ARCHIVE)],
        coverage=[complete(ARCHIVE_A)], now=1_000.0,
    )
    MemoryIngestor(store).ingest(
        SOURCE_VISUAL, [], conversations=[conversation(7, "项目组", source=SOURCE_VISUAL)],
        coverage=[complete(VISUAL_V, source=SOURCE_VISUAL)], now=1_000.0,
    )
    logical = link(store, ARCHIVE_A)
    link(store, VISUAL_V, logical)
    for canonical in (ARCHIVE_A, VISUAL_V):
        assert store.conversation(canonical) is not None
        store.connection.execute(
            "DELETE FROM conversations WHERE canonical_id = ?;", (canonical,)
        )
    result = service(store).recent_context(
        logical_conversation_id=logical, source=SOURCE_ARCHIVE, since=BASE
    )
    assert result.items == ()
    assert result.logical_coverage.members == ()
    assert result.logical_coverage.status == COVERAGE_NOT_OBSERVED
    assert not result.logical_coverage.trustworthy_empty
    assert not result.is_empty_and_trustworthy
    assert any("logical_conversation_dangling" in c for c in result.logical_coverage.caveats)
    assert not any(ARCHIVE_A in c for c in result.logical_coverage.caveats)


# --- SEMANTIC-1, inside one source -------------------------------------------


def two_archive_snapshots(store, *, a_coverage, b_coverage):
    archive_snapshot(store, canonical=ARCHIVE_A, number=1, at=BASE + 10,
                     coverage=list(a_coverage))
    archive_snapshot(store, canonical=ARCHIVE_B, number=2, at=BASE + 20,
                     coverage=list(b_coverage))


def link_both(store, first, second):
    logical = link(store, first)
    link(store, second, logical)
    return logical


@pytest.mark.parametrize("reverse", [False, True], ids=["a_first", "b_first"])
def test_case_a_one_complete_member_with_a_partial_sibling(store, reverse):
    two_archive_snapshots(
        store,
        a_coverage=[complete(ARCHIVE_A)],
        b_coverage=[partial(ARCHIVE_B, (BASE, BASE + 500))],
    )
    pair = (ARCHIVE_A, ARCHIVE_B) if reverse else (ARCHIVE_B, ARCHIVE_A)
    logical = link_both(store, *pair)

    result = service(store).recent_context(
        logical_conversation_id=logical, source=SOURCE_ARCHIVE, since=BASE, until=WINDOW[1])
    assert result.logical_coverage.status == COVERAGE_COMPLETE
    assert result.logical_coverage.trustworthy_empty
    assert result.logical_coverage.complete_members == (ARCHIVE_A,)
    # The weaker sibling is reported, not erased and not allowed to veto.
    assert any(ARCHIVE_B in c and COVERAGE_PARTIAL in c for c in result.logical_coverage.caveats)


def test_case_b_one_complete_member_with_an_unobserved_sibling(store):
    two_archive_snapshots(store, a_coverage=[complete(ARCHIVE_A)], b_coverage=[])
    logical = link_both(store, ARCHIVE_A, ARCHIVE_B)

    result = service(store).recent_context(
        logical_conversation_id=logical, source=SOURCE_ARCHIVE, since=BASE, until=WINDOW[1])
    assert result.logical_coverage.status == COVERAGE_COMPLETE
    assert result.logical_coverage.trustworthy_empty
    assert any(ARCHIVE_B in c and COVERAGE_NOT_OBSERVED in c
               for c in result.logical_coverage.caveats)


def test_case_c_two_complete_members_report_no_weaker_member(store):
    two_archive_snapshots(store, a_coverage=[complete(ARCHIVE_A)],
                          b_coverage=[complete(ARCHIVE_B)])
    logical = link_both(store, ARCHIVE_A, ARCHIVE_B)

    result = service(store).recent_context(
        logical_conversation_id=logical, source=SOURCE_ARCHIVE, since=BASE, until=WINDOW[1])
    assert result.logical_coverage.status == COVERAGE_COMPLETE
    assert result.logical_coverage.trustworthy_empty
    assert set(result.logical_coverage.complete_members) == {ARCHIVE_A, ARCHIVE_B}
    assert result.logical_coverage.caveats == ()


@pytest.mark.parametrize("reverse", [False, True], ids=["partial_first", "complete_first"])
def test_case_d_order_does_not_change_the_aggregate(store, reverse):
    two_archive_snapshots(
        store,
        a_coverage=[partial(ARCHIVE_A, (BASE, BASE + 500))],
        b_coverage=[complete(ARCHIVE_B)],
    )
    pair = (ARCHIVE_A, ARCHIVE_B) if reverse else (ARCHIVE_B, ARCHIVE_A)
    logical = link_both(store, *pair)

    result = service(store).recent_context(
        logical_conversation_id=logical, source=SOURCE_ARCHIVE, since=BASE, until=WINDOW[1])
    assert result.logical_coverage.status == COVERAGE_COMPLETE
    assert result.logical_coverage.trustworthy_empty
    assert result.logical_coverage.complete_members == (ARCHIVE_B,)
    assert any(ARCHIVE_A in c and COVERAGE_PARTIAL in c for c in result.logical_coverage.caveats)


@pytest.mark.parametrize("a_window,b_window,label", [
    ((BASE, BASE + 500), (BASE + 500, WINDOW[1]), "joined_at_an_endpoint"),
    ((BASE, BASE + 400), (BASE + 600, WINDOW[1]), "leaving_a_gap"),
])
def test_two_partial_members_never_combine_into_complete_coverage(store, a_window, b_window, label):
    two_archive_snapshots(
        store,
        a_coverage=[partial(ARCHIVE_A, a_window)],
        b_coverage=[partial(ARCHIVE_B, b_window)],
    )
    logical = link_both(store, ARCHIVE_A, ARCHIVE_B)

    result = service(store).recent_context(
        logical_conversation_id=logical, source=SOURCE_ARCHIVE, since=BASE, until=WINDOW[1])
    assert result.logical_coverage.status == COVERAGE_PARTIAL, label
    assert not result.logical_coverage.trustworthy_empty, label
    assert result.logical_coverage.complete_members == (), label


def test_one_member_unavailable_leaves_the_group_unavailable(store):
    archive_snapshot(store, canonical=ARCHIVE_A, number=1, at=BASE + 10,
                     coverage=[CoverageRecord(
                         source=SOURCE_ARCHIVE, status=COVERAGE_UNAVAILABLE,
                         conversation_canonical_id=ARCHIVE_A,
                         window_start=BASE, window_end=WINDOW[1],
                         reason="permission_revoked", message_count=0,
                     )])
    archive_snapshot(store, canonical=ARCHIVE_B, number=2, at=BASE + 20)
    logical = link_both(store, ARCHIVE_A, ARCHIVE_B)

    result = service(store).recent_context(
        logical_conversation_id=logical, source=SOURCE_ARCHIVE,
        since=BASE, until=WINDOW[1])
    # Neither member observed the window, so nothing may claim it was empty.
    assert result.logical_coverage.status == COVERAGE_UNAVAILABLE
    assert not result.logical_coverage.trustworthy_empty
    assert any(ARCHIVE_A in c and COVERAGE_UNAVAILABLE in c
               for c in result.logical_coverage.caveats)


def test_a_source_wide_record_completes_each_member_it_speaks_for(store):
    """A source-wide (NULL conversation) record speaks for every member.

    That is inherited store semantics and is honest here -- the record says
    Archive covered the window. What it must not do is read as two members
    combining, so this pins the per-member verdict rather than the count.
    """
    two_archive_snapshots(store, a_coverage=[], b_coverage=[])
    logical = link_both(store, ARCHIVE_A, ARCHIVE_B)
    MemoryIngestor(store).ingest(
        SOURCE_ARCHIVE, [], conversations=[], now=1_000.0,
        coverage=[CoverageRecord(
            source=SOURCE_ARCHIVE, status=COVERAGE_COMPLETE,
            conversation_canonical_id=None,
            window_start=WINDOW[0], window_end=WINDOW[1], message_count=2,
        )],
    )
    result = service(store).recent_context(
        logical_conversation_id=logical, source=SOURCE_ARCHIVE,
        since=BASE, until=WINDOW[1])
    assert result.logical_coverage.status == COVERAGE_COMPLETE
    assert result.logical_coverage.trustworthy_empty
    assert result.logical_coverage.members == (ARCHIVE_A, ARCHIVE_B)
    assert result.logical_coverage.member_status == (
        (ARCHIVE_A, COVERAGE_COMPLETE), (ARCHIVE_B, COVERAGE_COMPLETE),
    )


# --- truthfulness and the sealed boundary -------------------------------------


def test_query_scope_and_row_provenance_stay_snapshot_shaped(group):
    store, logical = group
    result = service(store).recent_context(
        logical_conversation_id=logical, source=SOURCE_ARCHIVE, since=BASE)
    scope = result.query_scope
    assert scope.conversation_canonical_id is None
    assert scope.logical_conversation_id == logical
    assert scope.window == (BASE, None)
    assert scope.policy.required == (SOURCE_ARCHIVE,)
    # Every row keeps the canonical identity of the snapshot it came from.
    for item in result.items:
        for observation in item.observations:
            assert observation.citation.canonical_conversation_id in (ARCHIVE_A, ARCHIVE_B)
            assert observation.citation.logical_message_id is None


def test_snapshot_scoped_results_are_unchanged(group):
    store, _ = group
    before = service(store).recent_context(conversation_canonical_id=ARCHIVE_A, since=BASE)
    serialized = {
        "coverage": before.coverage.as_dict(),
        "scope": dataclasses.asdict(before.query_scope),
    }
    assert before.logical_coverage is None
    assert before.query_scope.logical_conversation_id is None
    after = service(store).recent_context(conversation_canonical_id=ARCHIVE_A, since=BASE)
    assert after.coverage.as_dict() == serialized["coverage"]
    assert dataclasses.asdict(after.query_scope) == serialized["scope"]
    assert after.logical_coverage is None
    assert list(after.coverage.as_dict()) == [
        "status", "trustworthy_empty", "required_sources", "supplemental_sources",
        "complete_sources", "per_source", "caveats",
    ]


def test_no_conversation_id_appears_under_any_sources_field(group):
    store, logical = group
    result = service(store).recent_context(
        logical_conversation_id=logical, source=SOURCE_ARCHIVE, since=BASE)
    wire = result.coverage.as_dict()
    for canonical in (ARCHIVE_A, ARCHIVE_B, VISUAL_V):
        assert canonical not in wire["required_sources"]
        assert canonical not in wire["supplemental_sources"]
        assert canonical not in wire["complete_sources"]
        assert canonical not in wire["per_source"]
        assert canonical not in result.query_scope.policy.required
        assert canonical not in result.query_scope.policy.supplemental


def test_the_worker_still_refuses_a_logical_conversation_id():
    assert "logical_conversation_id" not in worker.ANSWER_REQUEST_FIELDS
    with pytest.raises(worker.BadRequest):
        worker.handle({
            "op": "answer_evidence",
            "store_path": "/nonexistent/memory.sqlite",
            "message_source": SOURCE_ARCHIVE,
            "start": BASE,
            "end": WINDOW[1],
            "logical_conversation_id": UNKNOWN_LOGICAL,
        })
