"""The composition rule for one window covered by several sources (M1.1).

Internal only; nothing exposes it yet. What is pinned here is the rule M2 will
have to keep: per-source evidence is never erased, the aggregate is the most
cautious reading, and an empty result is only ever "no messages" when every
source consulted covered the window completely.
"""

from __future__ import annotations

import memory_identity as identity
from conftest import database_message, visual_message
from memory_ingest import MemoryIngestor
from memory_retrieval import MemoryQuery, MemoryRetriever
from memory_store import (
    COVERAGE_COMPLETE,
    COVERAGE_NOT_OBSERVED,
    COVERAGE_PARTIAL,
    COVERAGE_UNAVAILABLE,
    CoverageRecord,
    CoverageVerdict,
    compose_coverage,
)
from message_source import SOURCE_DATABASE, SOURCE_VISUAL


def verdict(status, source, *reasons):
    return CoverageVerdict(status=status, source=source, reasons=tuple(reasons))


# --- the pure rule -----------------------------------------------------------


def test_no_sources_is_not_observed():
    composed = compose_coverage({})
    assert composed.status == COVERAGE_NOT_OBSERVED
    assert not composed.trustworthy_empty_possible


def test_one_complete_source_is_complete():
    composed = compose_coverage({"visual": verdict(COVERAGE_COMPLETE, "visual")})
    assert composed.status == COVERAGE_COMPLETE
    assert composed.trustworthy_empty_possible
    assert composed.as_verdict().source == "visual"


def test_all_complete_is_complete():
    composed = compose_coverage({
        "visual": verdict(COVERAGE_COMPLETE, "visual"),
        "database": verdict(COVERAGE_COMPLETE, "database"),
    })
    assert composed.status == COVERAGE_COMPLETE
    assert composed.trustworthy_empty_possible
    # An aggregate over two sources has no single provenance.
    assert composed.as_verdict().source is None


def test_a_partial_optional_source_does_not_erase_a_complete_one():
    composed = compose_coverage({
        "visual": verdict(COVERAGE_COMPLETE, "visual"),
        "database": verdict(COVERAGE_PARTIAL, "database", "limit_reached"),
    })
    assert composed.complete_sources == ("visual",)
    assert composed.partial_sources == ("database",)
    assert composed.per_source["visual"].status == COVERAGE_COMPLETE
    # ...but the aggregate is still the cautious reading.
    assert composed.status == COVERAGE_PARTIAL
    assert not composed.trustworthy_empty_possible
    assert composed.as_verdict().reasons == ("database:limit_reached",)


def test_an_unavailable_optional_source_does_not_erase_a_complete_one():
    composed = compose_coverage({
        "visual": verdict(COVERAGE_COMPLETE, "visual"),
        "database": verdict(COVERAGE_UNAVAILABLE, "database", "source_error"),
    })
    assert composed.complete_sources == ("visual",)
    assert composed.unavailable_sources == ("database",)
    assert composed.status == COVERAGE_PARTIAL
    assert not composed.trustworthy_empty_possible


def test_two_partial_sources_do_not_add_up_to_complete():
    composed = compose_coverage({
        "visual": verdict(COVERAGE_PARTIAL, "visual", "limit_reached"),
        "database": verdict(COVERAGE_PARTIAL, "database", "limit_reached"),
    })
    assert composed.status == COVERAGE_PARTIAL
    assert composed.complete_sources == ()
    assert not composed.trustworthy_empty_possible


def test_no_complete_source_never_permits_a_trustworthy_empty():
    for statuses in (
        (COVERAGE_PARTIAL, COVERAGE_PARTIAL),
        (COVERAGE_PARTIAL, COVERAGE_UNAVAILABLE),
        (COVERAGE_UNAVAILABLE, COVERAGE_UNAVAILABLE),
        (COVERAGE_NOT_OBSERVED, COVERAGE_PARTIAL),
        (COVERAGE_NOT_OBSERVED, COVERAGE_NOT_OBSERVED),
    ):
        composed = compose_coverage({
            "visual": verdict(statuses[0], "visual"),
            "database": verdict(statuses[1], "database"),
        })
        assert not composed.trustworthy_empty_possible, statuses


def test_all_unavailable_is_unavailable():
    composed = compose_coverage({
        "visual": verdict(COVERAGE_UNAVAILABLE, "visual", "source_error"),
        "database": verdict(COVERAGE_UNAVAILABLE, "database", "source_error"),
    })
    assert composed.status == COVERAGE_UNAVAILABLE


def test_a_not_observed_source_beside_a_complete_one_is_partial():
    composed = compose_coverage({
        "visual": verdict(COVERAGE_COMPLETE, "visual"),
        "database": verdict(COVERAGE_NOT_OBSERVED, "database"),
    })
    assert composed.status == COVERAGE_PARTIAL
    assert composed.not_observed_sources == ("database",)
    assert composed.as_verdict().reasons == ("database:not_observed",)


def test_composition_never_substitutes_one_source_for_another():
    """No fallback: each per-source verdict is returned as its own."""
    inputs = {
        "visual": verdict(COVERAGE_COMPLETE, "visual"),
        "database": verdict(COVERAGE_UNAVAILABLE, "database", "source_error"),
    }
    composed = compose_coverage(inputs)
    assert composed.per_source == inputs


# --- through the store and retrieval ----------------------------------------


def test_a_query_without_a_source_composes_every_known_source(store):
    ingestor = MemoryIngestor(store)
    ingestor.ingest(
        SOURCE_VISUAL, [visual_message(1, 7, "明天开会", observed_at=100.0)],
        coverage=[CoverageRecord(source=SOURCE_VISUAL, status=COVERAGE_COMPLETE)],
        now=1_000.0,
    )
    ingestor.ingest(
        SOURCE_DATABASE, [],
        coverage=[CoverageRecord(source=SOURCE_DATABASE, status=COVERAGE_UNAVAILABLE,
                                 reason="source_error")],
        now=1_000.0,
    )
    result = MemoryRetriever(store).search(MemoryQuery(text="取消"))
    assert result.hits == ()
    assert result.coverage.status == COVERAGE_PARTIAL
    assert result.coverage.source is None
    assert result.coverage_by_source.complete_sources == (SOURCE_VISUAL,)
    assert result.coverage_by_source.unavailable_sources == (SOURCE_DATABASE,)
    # The shipped reader's complete word is right there, unerased...
    assert result.coverage_by_source.per_source[SOURCE_VISUAL].status == COVERAGE_COMPLETE
    # ...and still the empty result may not be read as "no messages".
    assert not result.is_empty_and_trustworthy


def test_a_query_naming_a_source_is_that_source_alone(store):
    ingestor = MemoryIngestor(store)
    ingestor.ingest(
        SOURCE_VISUAL, [],
        coverage=[CoverageRecord(source=SOURCE_VISUAL, status=COVERAGE_COMPLETE)],
        now=1_000.0,
    )
    ingestor.ingest(
        SOURCE_DATABASE, [],
        coverage=[CoverageRecord(source=SOURCE_DATABASE, status=COVERAGE_UNAVAILABLE,
                                 reason="source_error")],
        now=1_000.0,
    )
    result = MemoryRetriever(store).search(MemoryQuery(text="取消", source=SOURCE_VISUAL))
    assert result.coverage.status == COVERAGE_COMPLETE
    assert result.coverage.source == SOURCE_VISUAL
    assert result.is_empty_and_trustworthy
    assert tuple(result.coverage_by_source.per_source) == (SOURCE_VISUAL,)


def test_a_source_the_store_never_heard_of_is_not_in_the_aggregate(store):
    MemoryIngestor(store).ingest(
        SOURCE_VISUAL, [],
        coverage=[CoverageRecord(source=SOURCE_VISUAL, status=COVERAGE_COMPLETE)],
        now=1_000.0,
    )
    result = MemoryRetriever(store).search(MemoryQuery(text="取消"))
    assert tuple(result.coverage_by_source.per_source) == (SOURCE_VISUAL,)
    assert result.coverage.status == COVERAGE_COMPLETE
    assert result.is_empty_and_trustworthy


def test_an_empty_store_composes_to_not_observed(store):
    result = MemoryRetriever(store).search(MemoryQuery(text="取消"))
    assert result.coverage.status == COVERAGE_NOT_OBSERVED
    assert result.coverage_by_source.per_source == {}
    assert not result.is_empty_and_trustworthy


def test_per_source_windows_are_assessed_independently(store):
    ingestor = MemoryIngestor(store)
    ingestor.ingest(
        SOURCE_VISUAL, [],
        coverage=[CoverageRecord(source=SOURCE_VISUAL, status=COVERAGE_COMPLETE,
                                 window_start=0.0, window_end=100.0)],
        now=1_000.0,
    )
    ingestor.ingest(
        SOURCE_DATABASE, [],
        coverage=[CoverageRecord(source=SOURCE_DATABASE, status=COVERAGE_COMPLETE,
                                 window_start=0.0, window_end=50.0)],
        now=1_000.0,
    )
    result = MemoryRetriever(store).search(MemoryQuery(start=60.0, end=90.0))
    per = result.coverage_by_source
    assert per.per_source[SOURCE_VISUAL].status == COVERAGE_COMPLETE
    assert per.per_source[SOURCE_DATABASE].status == COVERAGE_NOT_OBSERVED
    assert result.coverage.status == COVERAGE_PARTIAL
    assert not result.is_empty_and_trustworthy
    conv = identity.conversation_canonical_id(SOURCE_VISUAL, "7")
    assert MemoryRetriever(store).search(
        MemoryQuery(start=60.0, end=90.0, source=SOURCE_VISUAL, conversation_canonical_id=conv)
    ).is_empty_and_trustworthy
