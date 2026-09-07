"""Freshness (M2.2c): exact timestamp semantics, per source and aggregate,
kept separate from coverage and from the newest stored message."""

from __future__ import annotations

import pytest

from conftest import database_message, granted, visual_message
from memory_freshness import memory_freshness
from memory_ingest import MemoryIngestor
from memory_query import MemoryQueryService
from memory_store import COVERAGE_COMPLETE, COVERAGE_PARTIAL, COVERAGE_UNAVAILABLE, CoverageRecord, MemoryStore
from message_source import SOURCE_DATABASE, SOURCE_VISUAL, MessageSourceError, SourceStatus

T = 1_700_000_000.0  # synthetic "10:00"


def fresh(store, at=T + 9_999):
    return memory_freshness(store, generated_at=at)


def by_source(store, name, at=T + 9_999):
    return {s.source: s for s in fresh(store, at).sources}[name]


def test_no_ingestion_history(store):
    f = fresh(store)
    assert f.sources == () and f.participating_sources == ()
    assert f.last_successful_sync is None
    assert f.observed_through_all_sources is None and f.complete_through_all_sources is None
    assert f.caveats == ("no_ingestion_history",)


def test_first_successful_sync_sets_every_boundary(store):
    MemoryIngestor(store).ingest(
        SOURCE_VISUAL, [visual_message(1, 7, "x", observed_at=T - 18 * 60)],  # 09:42
        coverage=[CoverageRecord(source=SOURCE_VISUAL, status=COVERAGE_COMPLETE, window_end=T - 120)],  # 09:58
        now=T,  # sync finished 10:00
    )
    s = by_source(store, SOURCE_VISUAL)
    assert (s.last_attempted_at, s.last_succeeded_at) == (T, T)
    assert s.last_attempt_state == "succeeded" and s.last_attempt_failure_state is None
    assert s.observed_through == T - 120
    assert s.complete_through == T - 120
    assert s.latest_message_at == T - 18 * 60
    assert s.latest_message_timestamp_kind == "first_observed"
    assert s.stored_messages == 1 and s.runs_total == 1


def test_the_three_timestamps_stay_distinct(store):
    """Example 1: sync 10:00, observed through 09:58, latest message 09:42."""
    MemoryIngestor(store).ingest(
        SOURCE_VISUAL, [visual_message(1, 7, "x", observed_at=T - 18 * 60)],
        coverage=[CoverageRecord(source=SOURCE_VISUAL, status=COVERAGE_COMPLETE, window_end=T - 120)], now=T,
    )
    s = by_source(store, SOURCE_VISUAL)
    assert s.latest_message_at < s.observed_through < s.last_succeeded_at
    assert len({s.latest_message_at, s.observed_through, s.last_succeeded_at}) == 3


def test_an_unbounded_record_is_observed_through_its_run(store):
    MemoryIngestor(store).ingest(SOURCE_VISUAL, [visual_message(1, 7, "x", observed_at=T - 500)],
                                 coverage=[CoverageRecord(source=SOURCE_VISUAL, status=COVERAGE_COMPLETE)], now=T)
    s = by_source(store, SOURCE_VISUAL)
    assert s.observed_through == T and s.complete_through == T
    assert s.latest_message_at == T - 500  # not moved forward by the read


def test_repeated_success_advances_the_boundaries(store):
    ingestor = MemoryIngestor(store)
    ingestor.ingest(SOURCE_VISUAL, [], coverage=[CoverageRecord(source=SOURCE_VISUAL, status=COVERAGE_COMPLETE, window_end=T)], now=T)
    ingestor.ingest(SOURCE_VISUAL, [], coverage=[CoverageRecord(source=SOURCE_VISUAL, status=COVERAGE_COMPLETE, window_end=T + 600)], now=T + 700)
    s = by_source(store, SOURCE_VISUAL)
    assert (s.last_succeeded_at, s.observed_through, s.complete_through, s.runs_total) == (T + 700, T + 600, T + 600, 2)


def test_a_later_failure_keeps_the_last_known_good_state(store):
    """Example 3: both the failure and the last success are visible."""
    ingestor = MemoryIngestor(store)
    ingestor.ingest(SOURCE_VISUAL, [visual_message(1, 7, "x", observed_at=T - 10)],
                    coverage=[CoverageRecord(source=SOURCE_VISUAL, status=COVERAGE_COMPLETE, window_end=T)], now=T)
    broken = visual_message(2, 7, "y")
    object.__setattr__(broken, "ownership", "")
    with pytest.raises(Exception):
        ingestor.ingest(SOURCE_VISUAL, [broken], now=T + 900)
    s = by_source(store, SOURCE_VISUAL)
    assert (s.last_attempted_at, s.last_attempt_state, s.last_attempt_failure_state) == (T + 900, "failed", "record_malformed")
    assert (s.last_succeeded_at, s.observed_through, s.complete_through) == (T, T, T)
    assert f"{SOURCE_VISUAL}:last_attempt_failed:record_malformed" in fresh(store).caveats


def test_a_source_refusal_is_recorded_without_moving_observed_through(store):
    class Refusing:
        name = SOURCE_DATABASE
        def status(self): return SourceStatus(source=self.name, ready=False, state="x")
        def list_conversations(self, limit): raise MessageSourceError("reader_unavailable", "no")
        def get_messages(self, *a, **k): return []
        def get_recent_messages(self, *a, **k): return []
    MemoryIngestor(store).ingest_from_source(Refusing(), now=T)
    s = by_source(store, SOURCE_DATABASE)
    assert s.last_attempt_state == "failed" and s.last_attempt_failure_state == "reader_unavailable"
    assert s.last_succeeded_at is None and s.observed_through is None
    assert f"{SOURCE_DATABASE}:never_succeeded" in fresh(store).caveats


def test_partial_coverage_moves_observed_but_not_complete(store):
    MemoryIngestor(store).ingest(
        SOURCE_VISUAL, [],
        coverage=[CoverageRecord(source=SOURCE_VISUAL, status=COVERAGE_COMPLETE, window_end=T),
                  CoverageRecord(source=SOURCE_VISUAL, status=COVERAGE_PARTIAL, window_end=T + 300, reason="limit_reached")],
        now=T + 400,
    )
    s = by_source(store, SOURCE_VISUAL)
    assert (s.observed_through, s.complete_through) == (T + 300, T)
    assert f"{SOURCE_VISUAL}:complete_before_observed" in fresh(store).caveats


def test_unavailable_records_contribute_nothing(store):
    MemoryIngestor(store).ingest(
        SOURCE_VISUAL, [], coverage=[CoverageRecord(source=SOURCE_VISUAL, status=COVERAGE_UNAVAILABLE, reason="source_error")], now=T)
    s = by_source(store, SOURCE_VISUAL)
    assert s.last_succeeded_at == T and s.observed_through is None
    assert f"{SOURCE_VISUAL}:no_observed_window" in fresh(store).caveats


def test_a_source_with_no_messages_but_a_valid_window(store):
    MemoryIngestor(store).ingest(SOURCE_VISUAL, [],
                                 coverage=[CoverageRecord(source=SOURCE_VISUAL, status=COVERAGE_COMPLETE, window_end=T)], now=T)
    s = by_source(store, SOURCE_VISUAL)
    assert s.stored_messages == 0 and s.latest_message_at is None
    assert s.observed_through == T  # observed recently, with nothing in it: not the same as unobserved


def test_no_stale_threshold_and_no_boolean(store):
    """Example 2: yesterday's complete sync, queried today, is just timestamps."""
    MemoryIngestor(store).ingest(SOURCE_VISUAL, [],
                                 coverage=[CoverageRecord(source=SOURCE_VISUAL, status=COVERAGE_COMPLETE, window_end=T)], now=T)
    f = fresh(store, at=T + 86_400)
    d = f.as_dict()
    assert d["generated_at"] == T + 86_400 and d["last_successful_sync"] == {"source": SOURCE_VISUAL, "at": T}
    assert not any("fresh" in k or "stale" in k for k in d if k != "generated_at")
    assert f.caveats == ()


def test_per_source_and_aggregate(store):
    ingestor = MemoryIngestor(store)
    ingestor.ingest(SOURCE_VISUAL, [visual_message(1, 7, "x", observed_at=T)],
                    coverage=[CoverageRecord(source=SOURCE_VISUAL, status=COVERAGE_COMPLETE, window_end=T + 100)], now=T + 200)
    ingestor.ingest(SOURCE_DATABASE, [database_message(9, 7, "x", created_at=T)],
                    coverage=[CoverageRecord(source=SOURCE_DATABASE, status=COVERAGE_PARTIAL, window_end=T + 50, reason="limit_reached")], now=T + 300)
    f = fresh(store)
    assert f.participating_sources == (SOURCE_DATABASE, SOURCE_VISUAL)
    assert f.last_successful_sync == (SOURCE_DATABASE, T + 300)
    assert f.observed_through_all_sources == T + 50   # the minimum: through which every source is known
    assert f.complete_through_all_sources is None     # database has no complete window
    assert f"{SOURCE_DATABASE}:no_complete_window" in f.caveats


def test_timestamps_survive_a_reopen(tmp_path):
    with MemoryStore.open(granted(tmp_path)) as store:
        MemoryIngestor(store).ingest(SOURCE_VISUAL, [visual_message(1, 7, "x", observed_at=T - 5)],
                                     coverage=[CoverageRecord(source=SOURCE_VISUAL, status=COVERAGE_COMPLETE, window_end=T)], now=T + 1)
        before = by_source(store, SOURCE_VISUAL).as_dict()
    with MemoryStore.open(granted(tmp_path)) as store:
        assert by_source(store, SOURCE_VISUAL).as_dict() == before


def test_freshness_is_separate_from_coverage_on_a_result(store):
    MemoryIngestor(store).ingest(SOURCE_VISUAL, [visual_message(1, 7, "x", observed_at=T - 5)],
                                 coverage=[CoverageRecord(source=SOURCE_VISUAL, status=COVERAGE_COMPLETE, window_end=T)], now=T + 1)
    service = MemoryQueryService(store, clock=lambda: T + 50)
    inside = service.search(text="none", start=T - 100, end=T)
    outside = service.search(text="none", start=T + 10, end=T + 40)
    assert inside.coverage.trustworthy_empty and not outside.coverage.trustworthy_empty
    assert inside.freshness == outside.freshness  # freshness does not depend on the question
    assert inside.freshness.generated_at == T + 50
    assert inside.freshness.sources[0].observed_through == T


def test_every_query_kind_carries_freshness(store):
    MemoryIngestor(store).ingest(SOURCE_VISUAL, [visual_message(1, 7, "x", observed_at=T)],
                                 coverage=[CoverageRecord(source=SOURCE_VISUAL, status=COVERAGE_COMPLETE)], now=T)
    service = MemoryQueryService(store)
    from memory_identity import conversation_canonical_id, message_canonical_id_from_source
    conv = conversation_canonical_id(SOURCE_VISUAL, "7")
    for result in (service.search(), service.timeline(conv), service.context_around(message_canonical_id_from_source(SOURCE_VISUAL, "1")),
                   service.recent_context(), service.conversations()):
        assert result.freshness.participating_sources == (SOURCE_VISUAL,)
