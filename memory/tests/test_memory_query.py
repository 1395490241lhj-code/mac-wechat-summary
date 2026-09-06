"""M2: the query service, its envelope, citations and coverage policy."""

from __future__ import annotations

import dataclasses

import pytest

import memory_identity as identity
from conftest import conversation, database_message, visual_message
from memory_ingest import MemoryIngestor
from memory_query import (
    CoverageReport,
    MemoryQueryResult,
    MemoryQueryService,
    MessageCitation,
    SourcePolicy,
    TimelineCursor,
    report_coverage,
    representative_of,
)
from memory_store import (
    COVERAGE_COMPLETE,
    COVERAGE_NOT_OBSERVED,
    COVERAGE_PARTIAL,
    COVERAGE_UNAVAILABLE,
    LINK_KIND_MESSAGE,
    LINK_OPERATOR,
    CoverageRecord,
    CoverageVerdict,
    MemoryStoreError,
)
from message_source import SOURCE_DATABASE, SOURCE_VISUAL

BASE = 1_700_000_000.0
CONV = identity.conversation_canonical_id(SOURCE_VISUAL, "7")
OTHER = identity.conversation_canonical_id(SOURCE_VISUAL, "8")
DB_CONV = identity.conversation_canonical_id(SOURCE_DATABASE, "7")


def vid(n):
    return identity.message_canonical_id_from_source(SOURCE_VISUAL, str(n))


def did(n):
    return identity.message_canonical_id_from_source(SOURCE_DATABASE, str(n))


@pytest.fixture()
def service(store):
    """Ten invented visual messages in conversation 7 and two in 8, complete coverage."""
    ingestor = MemoryIngestor(store)
    ingestor.ingest(
        SOURCE_VISUAL,
        [visual_message(n, 7, f"第{n}条 消息", observed_at=BASE + 10 * n, sequence=n) for n in range(1, 11)]
        + [visual_message(21, 8, "lunch?", sender="Sam", observed_at=BASE + 15, sequence=1),
           visual_message(22, 8, "明天 lunch", sender="Sam", observed_at=BASE + 25, sequence=2)],
        conversations=[conversation(7, "项目组"), conversation(8, "Sam")],
        coverage=[CoverageRecord(source=SOURCE_VISUAL, status=COVERAGE_COMPLETE,
                                 window_start=BASE, window_end=BASE + 1_000)],
        now=1_000.0,
    )
    return MemoryQueryService(store)


# --- envelope ------------------------------------------------------------------


def test_every_query_returns_the_same_envelope(service):
    results = [
        service.search(text="消息"),
        service.timeline(CONV),
        service.context_around(vid(5)),
        service.recent_context(),
    ]
    for result in results:
        assert isinstance(result, MemoryQueryResult)
        assert isinstance(result.coverage, CoverageReport)
        assert isinstance(result.truncated, bool)
        assert result.query_scope.kind in {"search", "timeline", "context", "recent"}


def test_there_is_no_items_only_api(service):
    """Every public method of the service returns the envelope."""
    for name in ("search", "timeline", "context_around", "recent_context"):
        assert callable(getattr(service, name))
    assert not any(
        name.endswith("_items") or name == "items" for name in dir(service) if not name.startswith("_")
    )


def test_the_envelope_is_frozen_so_coverage_cannot_be_dropped(service):
    result = service.search(text="消息")
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.coverage = None  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.items = ()  # type: ignore[misc]


def test_the_scope_restates_the_question(service):
    result = service.search(text="消息", sender="同事A", start=BASE, end=BASE + 50, limit=3, order="oldest")
    scope = result.query_scope
    assert (scope.kind, scope.text, scope.sender, scope.window, scope.limit, scope.order) == (
        "search", "消息", "同事A", (BASE, BASE + 50), 3, "oldest")
    assert scope.policy.required == (SOURCE_VISUAL,)
    assert scope.policy.supplemental == ()


# --- citation ------------------------------------------------------------------


def test_every_item_exposes_a_stable_citation(service):
    item = service.search(text="第5条").items[0]
    citation = item.citation
    assert citation == MessageCitation(
        canonical_message_id=vid(5), logical_message_id=None,
        canonical_conversation_id=CONV, source=SOURCE_VISUAL, source_message_id="5",
        identity_mode="source", timestamp=BASE + 50, timestamp_kind="first_observed",
    )
    assert set(citation.as_dict()) == {
        "canonical_message_id", "logical_message_id", "canonical_conversation_id",
        "source", "source_message_id", "identity_mode", "timestamp", "timestamp_kind",
    }


def test_a_citation_carries_no_text(service):
    item = service.search(text="第5条").items[0]
    assert "消息" not in repr(item.citation)
    assert "text" not in item.citation.as_dict()


def test_citations_are_identical_across_query_kinds(service):
    by_search = service.search(text="第5条").items[0].citation
    by_timeline = next(i for i in service.timeline(CONV).items if i.canonical_id == vid(5)).citation
    by_context = next(i for i in service.context_around(vid(5)).items if i.is_focal).citation
    by_recent = next(i for i in service.recent_context(conversation_canonical_id=CONV).items
                     if i.canonical_id == vid(5)).citation
    assert by_search == by_timeline == by_context == by_recent


def test_result_citations_collect_every_observation(service):
    result = service.timeline(CONV, limit=3)
    assert [c.canonical_message_id for c in result.citations] == [vid(1), vid(2), vid(3)]


# --- coverage policy -----------------------------------------------------------


def verdict(status, source, *reasons):
    return CoverageVerdict(status=status, source=source, reasons=tuple(reasons))


def test_default_policy_requires_every_known_source(store):
    ingestor = MemoryIngestor(store)
    for source in (SOURCE_VISUAL, SOURCE_DATABASE):
        ingestor.ingest(source, [], coverage=[CoverageRecord(source=source, status=COVERAGE_COMPLETE)], now=1.0)
    result = MemoryQueryService(store).search(text="x")
    assert set(result.query_scope.policy.required) == {SOURCE_VISUAL, SOURCE_DATABASE}
    assert result.query_scope.policy.supplemental == ()
    assert result.is_empty_and_trustworthy


def test_naming_a_source_makes_it_the_single_required_source(store):
    ingestor = MemoryIngestor(store)
    ingestor.ingest(SOURCE_VISUAL, [], coverage=[CoverageRecord(source=SOURCE_VISUAL, status=COVERAGE_COMPLETE)], now=1.0)
    ingestor.ingest(SOURCE_DATABASE, [], coverage=[CoverageRecord(source=SOURCE_DATABASE, status=COVERAGE_UNAVAILABLE, reason="source_error")], now=1.0)
    result = MemoryQueryService(store).search(text="x", source=SOURCE_VISUAL)
    assert result.query_scope.policy == SourcePolicy(required=(SOURCE_VISUAL,))
    assert result.is_empty_and_trustworthy


def test_a_supplemental_source_cannot_veto_a_required_complete_one():
    report = report_coverage(
        {SOURCE_VISUAL: verdict(COVERAGE_COMPLETE, SOURCE_VISUAL),
         SOURCE_DATABASE: verdict(COVERAGE_UNAVAILABLE, SOURCE_DATABASE, "source_error")},
        SourcePolicy(required=(SOURCE_VISUAL,), supplemental=(SOURCE_DATABASE,)),
    )
    assert report.status == COVERAGE_COMPLETE
    assert report.trustworthy_empty
    # ...and it is not hidden.
    assert report.per_source[SOURCE_DATABASE].status == COVERAGE_UNAVAILABLE
    assert report.caveats == (f"{SOURCE_DATABASE}:unavailable", f"{SOURCE_DATABASE}:source_error")


def test_a_supplemental_source_cannot_make_an_empty_result_trustworthy():
    report = report_coverage(
        {SOURCE_VISUAL: verdict(COVERAGE_PARTIAL, SOURCE_VISUAL, "limit_reached"),
         SOURCE_DATABASE: verdict(COVERAGE_COMPLETE, SOURCE_DATABASE)},
        SourcePolicy(required=(SOURCE_VISUAL,), supplemental=(SOURCE_DATABASE,)),
    )
    assert not report.trustworthy_empty
    assert report.status == COVERAGE_PARTIAL
    assert SOURCE_DATABASE in report.complete_sources


def test_two_required_sources_must_both_be_complete():
    report = report_coverage(
        {SOURCE_VISUAL: verdict(COVERAGE_COMPLETE, SOURCE_VISUAL),
         SOURCE_DATABASE: verdict(COVERAGE_PARTIAL, SOURCE_DATABASE, "limit_reached")},
        SourcePolicy(required=(SOURCE_VISUAL, SOURCE_DATABASE)),
    )
    assert not report.trustworthy_empty
    assert report.status == COVERAGE_PARTIAL
    assert report.complete_sources == (SOURCE_VISUAL,)


def test_partial_sources_never_compose_into_complete():
    report = report_coverage(
        {SOURCE_VISUAL: verdict(COVERAGE_PARTIAL, SOURCE_VISUAL),
         SOURCE_DATABASE: verdict(COVERAGE_PARTIAL, SOURCE_DATABASE)},
        SourcePolicy(required=(SOURCE_VISUAL, SOURCE_DATABASE)),
    )
    assert report.status == COVERAGE_PARTIAL and not report.trustworthy_empty


def test_no_required_source_is_never_trustworthy():
    assert not report_coverage({}, SourcePolicy(required=())).trustworthy_empty
    assert report_coverage({}, SourcePolicy(required=())).status == COVERAGE_NOT_OBSERVED


def test_a_required_source_with_no_record_is_not_observed():
    report = report_coverage({}, SourcePolicy(required=(SOURCE_VISUAL,)))
    assert report.status == COVERAGE_NOT_OBSERVED
    assert not report.trustworthy_empty


def test_a_source_cannot_be_both_required_and_supplemental():
    with pytest.raises(MemoryStoreError) as raised:
        SourcePolicy(required=(SOURCE_VISUAL,), supplemental=(SOURCE_VISUAL,))
    assert raised.value.state == "source_policy_invalid"


def test_an_explicit_policy_is_honoured_by_the_service(store):
    ingestor = MemoryIngestor(store)
    ingestor.ingest(SOURCE_VISUAL, [], coverage=[CoverageRecord(source=SOURCE_VISUAL, status=COVERAGE_COMPLETE)], now=1.0)
    ingestor.ingest(SOURCE_DATABASE, [], coverage=[CoverageRecord(source=SOURCE_DATABASE, status=COVERAGE_PARTIAL, reason="limit_reached")], now=1.0)
    service = MemoryQueryService(store)
    conservative = service.search(text="x")
    assert not conservative.is_empty_and_trustworthy
    explicit = service.search(text="x", policy=SourcePolicy(required=(SOURCE_VISUAL,), supplemental=(SOURCE_DATABASE,)))
    assert explicit.is_empty_and_trustworthy
    assert explicit.coverage.caveats[0] == f"{SOURCE_DATABASE}:observed_partial"


def test_an_empty_result_outside_the_window_is_not_trustworthy(service):
    result = service.search(text="不存在", start=BASE + 5_000, end=BASE + 6_000)
    assert result.items == ()
    assert result.coverage.status == COVERAGE_NOT_OBSERVED
    assert not result.is_empty_and_trustworthy


def test_an_empty_result_inside_the_window_is_trustworthy(service):
    result = service.search(text="不存在", start=BASE, end=BASE + 100)
    assert result.items == () and result.is_empty_and_trustworthy


# --- search --------------------------------------------------------------------


def test_search_supports_every_m1_filter(service):
    assert [i.canonical_id for i in service.search(text="第5条").items] == [vid(5)]
    assert len(service.search(conversation_canonical_id=OTHER).items) == 2
    assert len(service.search(sender="Sam").items) == 2
    assert len(service.search(ownership="other").items) == 12
    assert len(service.search(source=SOURCE_VISUAL).items) == 12
    assert [i.timestamp for i in service.search(start=BASE + 20, end=BASE + 30, order="oldest").items] == [BASE + 20, BASE + 25, BASE + 30]
    assert [i.canonical_id for i in service.search(text="lunch", order="relevance").items] == \
           [i.canonical_id for i in service.search(text="lunch", order="relevance").items]


def test_search_orders_recent_and_oldest_as_mirrors(service):
    recent = [i.canonical_id for i in service.search(order="recent").items]
    oldest = [i.canonical_id for i in service.search(order="oldest").items]
    assert recent == list(reversed(oldest))


def test_search_keeps_fts_syntax_quoted(service):
    assert service.search(text='"( OR NOT *').items == ()
    assert [i.canonical_id for i in service.search(text="明天 lunch").items] == [vid(22)]


def test_relevance_requires_text(service):
    with pytest.raises(MemoryStoreError) as raised:
        service.search(order="relevance")
    assert raised.value.state == "order_requires_text"


# --- timeline ------------------------------------------------------------------


def test_timeline_is_chronological_and_deterministic(service):
    first = [i.canonical_id for i in service.timeline(CONV).items]
    assert first == [vid(n) for n in range(1, 11)]
    assert first == [i.canonical_id for i in service.timeline(CONV).items]


def test_timeline_respects_a_window(service):
    items = service.timeline(CONV, start=BASE + 30, end=BASE + 50).items
    assert [i.canonical_id for i in items] == [vid(3), vid(4), vid(5)]


def test_timeline_pages_forward_with_a_cursor(service):
    page1 = service.timeline(CONV, limit=4)
    assert page1.truncated
    page2 = service.timeline(CONV, limit=4, after=page1.items[-1].cursor)
    page3 = service.timeline(CONV, limit=4, after=page2.items[-1].cursor)
    ids = [i.canonical_id for p in (page1, page2, page3) for i in p.items]
    assert ids == [vid(n) for n in range(1, 11)]
    assert not page3.truncated


def test_timeline_pages_backward_without_flipping_order(service):
    last = service.timeline(CONV, limit=3, before=TimelineCursor(BASE + 1_000, 0, "zzz"))
    assert [i.canonical_id for i in last.items] == [vid(8), vid(9), vid(10)]
    previous = service.timeline(CONV, limit=3, before=last.items[0].cursor)
    assert [i.canonical_id for i in previous.items] == [vid(5), vid(6), vid(7)]


def test_timeline_cursor_is_not_a_bare_identifier(service):
    """Two rows in the same second are ordered by sequence, then id -- and paged the same way."""
    store = service._store
    MemoryIngestor(store).ingest(
        SOURCE_VISUAL,
        [visual_message(31, 9, "a", observed_at=BASE, sequence=2),
         visual_message(32, 9, "b", observed_at=BASE, sequence=1),
         visual_message(33, 9, "c", observed_at=BASE, sequence=3)],
        now=2.0,
    )
    conv9 = identity.conversation_canonical_id(SOURCE_VISUAL, "9")
    all_items = service.timeline(conv9).items
    assert [i.canonical_id for i in all_items] == [vid(32), vid(31), vid(33)]
    after_first = service.timeline(conv9, after=all_items[0].cursor).items
    assert [i.canonical_id for i in after_first] == [vid(31), vid(33)]
    assert all_items[0].cursor == TimelineCursor(BASE, 1, vid(32))


def test_timeline_refuses_two_anchors(service):
    cursor = service.timeline(CONV, limit=1).items[0].cursor
    with pytest.raises(MemoryStoreError) as raised:
        service.timeline(CONV, after=cursor, before=cursor)
    assert raised.value.state == "cursor_conflict"


def test_timeline_scope_records_the_anchor(service):
    cursor = service.timeline(CONV, limit=1).items[0].cursor
    assert service.timeline(CONV, after=cursor).query_scope.anchor == vid(1)


# --- context around ------------------------------------------------------------


def test_context_around_returns_neighbours_and_marks_the_focal(service):
    result = service.context_around(vid(5), before=2, after=2)
    assert [i.canonical_id for i in result.items] == [vid(3), vid(4), vid(5), vid(6), vid(7)]
    assert [i.is_focal for i in result.items] == [False, False, True, False, False]
    assert result.focal_canonical_id == vid(5)
    assert result.truncated  # more exists on both sides


def test_context_is_clipped_at_conversation_boundaries(service):
    start = service.context_around(vid(1), before=3, after=1)
    assert [i.canonical_id for i in start.items] == [vid(1), vid(2)]
    end = service.context_around(vid(10), before=1, after=3)
    assert [i.canonical_id for i in end.items] == [vid(9), vid(10)]
    # Truncation means context exists beyond what was asked for, on either side.
    assert service.context_around(vid(10), before=0, after=0).truncated
    assert not service.context_around(vid(21), before=5, after=5).truncated


def test_context_never_crosses_conversations(service):
    result = service.context_around(vid(21), before=5, after=5)
    assert {i.citation.canonical_conversation_id for i in result.items} == {OTHER}


def test_context_coverage_is_for_the_returned_window(service):
    result = service.context_around(vid(5), before=1, after=1)
    assert result.query_scope.window == (BASE + 40, BASE + 60)
    assert result.query_scope.conversation_canonical_id == CONV
    assert result.query_scope.policy.required == (SOURCE_VISUAL,)
    assert result.coverage.status == COVERAGE_COMPLETE


def test_context_around_an_unknown_message_fails_clearly(service):
    with pytest.raises(MemoryStoreError) as raised:
        service.context_around("msg:nope")
    assert raised.value.state == "message_unknown"


# --- recent context ------------------------------------------------------------


def test_recent_context_is_newest_first_and_bounded(service):
    result = service.recent_context(limit=3)
    assert [i.canonical_id for i in result.items] == [vid(10), vid(9), vid(8)]
    assert result.truncated


def test_recent_context_can_be_read_chronologically(service):
    result = service.recent_context(limit=3, order="oldest")
    assert [i.canonical_id for i in result.items] == [vid(8), vid(9), vid(10)]


def test_recent_context_scopes_to_conversation_and_time(service):
    scoped = service.recent_context(conversation_canonical_id=OTHER)
    assert [i.canonical_id for i in scoped.items] == [vid(22), vid(21)]
    timed = service.recent_context(since=BASE + 85)
    assert [i.canonical_id for i in timed.items] == [vid(10), vid(9)]
    assert timed.query_scope.window == (BASE + 85, None)


# --- logical messages ----------------------------------------------------------


@pytest.fixture()
def two_readers(store):
    ingestor = MemoryIngestor(store)
    ingestor.ingest(SOURCE_VISUAL, [visual_message(1, 7, "明天开会", observed_at=BASE + 3)],
                    coverage=[CoverageRecord(source=SOURCE_VISUAL, status=COVERAGE_COMPLETE)], now=1.0)
    ingestor.ingest(SOURCE_DATABASE, [database_message(1, 7, "明天开会", created_at=BASE)],
                    coverage=[CoverageRecord(source=SOURCE_DATABASE, status=COVERAGE_COMPLETE)], now=1.0)
    return store


def test_unlinked_lookalikes_stay_two_items(two_readers):
    result = MemoryQueryService(two_readers).search(text="开会")
    assert len(result.items) == 2
    assert {i.logical_message_id for i in result.items} == {None}
    assert all(len(i.observations) == 1 for i in result.items)


def test_linked_observations_become_one_item_with_every_citation(two_readers):
    logical = two_readers.link_observation(kind=LINK_KIND_MESSAGE, observation_canonical_id=vid(1),
                                           logical_id=None, basis=LINK_OPERATOR, asserted_by="op", now=2.0)
    two_readers.link_observation(kind=LINK_KIND_MESSAGE, observation_canonical_id=did(1),
                                 logical_id=logical, basis=LINK_OPERATOR, asserted_by="op", now=2.0)
    result = MemoryQueryService(two_readers).search(text="开会")
    assert len(result.items) == 1
    item = result.items[0]
    assert item.logical_message_id == logical
    assert {o.citation.source for o in item.observations} == {SOURCE_VISUAL, SOURCE_DATABASE}
    assert len(item.citations) == 2
    assert all(c.logical_message_id == logical for c in item.citations)


def test_the_representative_is_chosen_by_a_fixed_rule(two_readers):
    logical = two_readers.link_observation(kind=LINK_KIND_MESSAGE, observation_canonical_id=vid(1),
                                           logical_id=None, basis=LINK_OPERATOR, asserted_by="op", now=2.0)
    two_readers.link_observation(kind=LINK_KIND_MESSAGE, observation_canonical_id=did(1),
                                 logical_id=logical, basis=LINK_OPERATOR, asserted_by="op", now=2.0)
    service = MemoryQueryService(two_readers)
    for _ in range(3):
        item = service.search(text="开会").items[0]
        # The database observation carries the source's own creation time.
        assert item.citation.source == SOURCE_DATABASE
        assert item.citation.timestamp_kind == "source_created"
        assert item.timestamp == BASE


def test_conflicting_provenance_is_kept_on_each_observation(two_readers):
    logical = two_readers.link_observation(kind=LINK_KIND_MESSAGE, observation_canonical_id=vid(1),
                                           logical_id=None, basis=LINK_OPERATOR, asserted_by="op", now=2.0)
    two_readers.link_observation(kind=LINK_KIND_MESSAGE, observation_canonical_id=did(1),
                                 logical_id=logical, basis=LINK_OPERATOR, asserted_by="op", now=2.0)
    item = MemoryQueryService(two_readers).search(text="开会").items[0]
    by_source = {o.citation.source: o for o in item.observations}
    assert by_source[SOURCE_VISUAL].citation.timestamp == BASE + 3
    assert by_source[SOURCE_DATABASE].citation.timestamp == BASE
    assert by_source[SOURCE_VISUAL].visible_time == "昨天 14:30"
    assert by_source[SOURCE_DATABASE].visible_time is None
    assert by_source[SOURCE_VISUAL].citation.timestamp_kind != by_source[SOURCE_DATABASE].citation.timestamp_kind


def test_representative_rule_falls_back_to_confidence_then_id():
    from memory_query import Observation
    def obs(cid, kind, conf):
        return Observation(
            citation=MessageCitation(canonical_message_id=cid, canonical_conversation_id="c",
                                     source="x", identity_mode="source", timestamp=1.0, timestamp_kind=kind),
            sender=None, ownership="other", kind="text", text=None, sequence=None,
            confidence=conf, visible_time=None, first_ingested_at=1.0, last_observed_at=1.0, observation_count=1,
        )
    assert representative_of([obs("b", "first_observed", 0.5), obs("a", "first_observed", 0.9)]).canonical_id == "a"
    assert representative_of([obs("b", "first_observed", 0.9), obs("a", "first_observed", 0.9)]).canonical_id == "a"
    assert representative_of([obs("z", "source_created", 0.1), obs("a", "first_observed", 0.9)]).canonical_id == "z"


# --- truncation ----------------------------------------------------------------


def test_truncation_is_measured_not_assumed(service):
    assert service.search(limit=12).truncated is False
    assert service.search(limit=11).truncated is True
    assert service.timeline(CONV, limit=10).truncated is False
    assert service.recent_context(limit=12).truncated is False


def test_limits_are_clamped(service):
    assert len(service.search(limit=10_000).items) == 12
    assert len(service.search(limit=0).items) == 1


def test_the_service_never_writes(store, service):
    before = store.counts()
    service.search(text="消息"); service.timeline(CONV); service.context_around(vid(2)); service.recent_context()
    assert store.counts() == before
