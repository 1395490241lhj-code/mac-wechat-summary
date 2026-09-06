"""The internal retrieval API: FTS, filters, citations, coverage-aware results.

Deterministic and local throughout. No test here contacts a network, loads a
model, or computes an embedding, and none is expected to: semantic retrieval is
M3.
"""

from __future__ import annotations

import pytest

import memory_identity as identity
from conftest import conversation, database_message, visual_message
from memory_ingest import MemoryIngestor
from memory_retrieval import MemoryQuery, MemoryRetriever
from memory_store import (
    COVERAGE_COMPLETE,
    COVERAGE_NOT_OBSERVED,
    COVERAGE_PARTIAL,
    CoverageRecord,
    MemoryStoreError,
)
from message_source import SOURCE_DATABASE, SOURCE_VISUAL

BASE = 1_700_000_000.0
CONVERSATION = identity.conversation_canonical_id(SOURCE_VISUAL, "7")
OTHER_CONVERSATION = identity.conversation_canonical_id(SOURCE_VISUAL, "8")


@pytest.fixture()
def populated(store):
    """A small, entirely invented corpus with both Chinese and English."""
    ingestor = MemoryIngestor(store)
    ingestor.ingest(
        SOURCE_VISUAL,
        [
            visual_message(1, 7, "明天下午三点开会", observed_at=BASE + 10),
            visual_message(2, 7, "好的，我会准时到", sender="我", ownership="own",
                           observed_at=BASE + 20),
            visual_message(3, 7, "记得带上季度报告", observed_at=BASE + 30),
            visual_message(4, 8, "lunch at the new place?", sender="Sam",
                           observed_at=BASE + 40),
            visual_message(5, 8, "明天 lunch 也可以", sender="Sam",
                           observed_at=BASE + 50),
        ],
        conversations=[conversation(7, "项目组"), conversation(8, "Sam")],
        coverage=[
            CoverageRecord(
                source=SOURCE_VISUAL,
                status=COVERAGE_COMPLETE,
                window_start=BASE,
                window_end=BASE + 100,
            )
        ],
        now=1_000.0,
    )
    return MemoryRetriever(store)


# --- text search -------------------------------------------------------------


def test_a_chinese_word_matches_inside_a_sentence(populated):
    hits = populated.search(MemoryQuery(text="开会")).hits
    assert [hit.text for hit in hits] == ["明天下午三点开会"]


def test_a_chinese_word_that_is_not_present_matches_nothing(populated):
    assert populated.search(MemoryQuery(text="取消")).hits == ()


def test_adjacency_matters_so_a_scrambled_pair_does_not_match(populated):
    """Segmented indexing must behave as a phrase, not as loose characters."""
    assert populated.search(MemoryQuery(text="会开")).hits == ()


def test_an_english_word_matches(populated):
    assert {hit.text for hit in populated.search(MemoryQuery(text="lunch")).hits} == {
        "lunch at the new place?",
        "明天 lunch 也可以",
    }


def test_two_terms_are_combined_with_and(populated):
    hits = populated.search(MemoryQuery(text="明天 lunch")).hits
    assert [hit.text for hit in hits] == ["明天 lunch 也可以"]


def test_a_sender_is_searchable(populated):
    hits = populated.search(MemoryQuery(text="Sam")).hits
    assert len(hits) == 2


def test_relevance_ordering_requires_a_text_query(populated):
    with pytest.raises(MemoryStoreError) as raised:
        populated.search(MemoryQuery(order="relevance"))
    assert raised.value.state == "order_requires_text"


def test_relevance_ordering_is_deterministic(populated):
    first = [hit.canonical_id for hit in populated.search(
        MemoryQuery(text="明天", order="relevance")
    ).hits]
    second = [hit.canonical_id for hit in populated.search(
        MemoryQuery(text="明天", order="relevance")
    ).hits]
    assert first == second and first


def test_punctuation_a_user_types_is_not_index_syntax(populated):
    assert populated.search(MemoryQuery(text='"( OR')).hits == ()


# --- filters -----------------------------------------------------------------


def test_filtering_by_conversation(populated):
    hits = populated.search(
        MemoryQuery(conversation_canonical_id=CONVERSATION)
    ).hits
    assert {hit.conversation_canonical_id for hit in hits} == {CONVERSATION}
    assert len(hits) == 3


def test_filtering_by_sender(populated):
    hits = populated.search(MemoryQuery(sender="Sam")).hits
    assert len(hits) == 2


def test_filtering_by_ownership(populated):
    hits = populated.search(MemoryQuery(ownership="own")).hits
    assert [hit.text for hit in hits] == ["好的，我会准时到"]


def test_filtering_by_a_time_range(populated):
    hits = populated.search(MemoryQuery(start=BASE + 20, end=BASE + 40)).hits
    assert [hit.timestamp for hit in hits] == [BASE + 40, BASE + 30, BASE + 20]


def test_recency_and_oldest_orderings_are_mirror_images(populated):
    newest = [hit.canonical_id for hit in populated.search(
        MemoryQuery(order="recent")
    ).hits]
    oldest = [hit.canonical_id for hit in populated.search(
        MemoryQuery(order="oldest")
    ).hits]
    assert newest == list(reversed(oldest))


def test_a_limit_is_applied_and_truncation_is_reported(populated):
    result = populated.search(MemoryQuery(limit=2))
    assert len(result.hits) == 2
    assert result.truncated is True
    assert populated.search(MemoryQuery(limit=50)).truncated is False


def test_a_limit_cannot_exceed_the_cap(populated):
    assert len(populated.search(MemoryQuery(limit=10_000)).hits) == 5


def test_text_and_filters_combine(populated):
    hits = populated.search(
        MemoryQuery(text="明天", conversation_canonical_id=OTHER_CONVERSATION)
    ).hits
    assert [hit.text for hit in hits] == ["明天 lunch 也可以"]


def test_the_conversation_helper_reads_in_time_order(populated):
    result = populated.conversation_messages(CONVERSATION)
    assert [hit.timestamp for hit in result.hits] == [BASE + 10, BASE + 20, BASE + 30]


def test_the_recent_helper_reads_newest_first(populated):
    result = populated.recent(since=BASE + 30)
    assert [hit.timestamp for hit in result.hits] == [BASE + 50, BASE + 40, BASE + 30]


def test_conversations_are_listable(populated):
    listed = populated.conversations()
    assert {row["display_name"] for row in listed} == {"项目组", "Sam"}


# --- citation ----------------------------------------------------------------


def test_every_hit_can_be_traced_back_to_its_source_message(populated):
    hit = populated.search(MemoryQuery(text="开会")).hits[0]
    citation = hit.citation()
    assert citation["source"] == SOURCE_VISUAL
    assert citation["source_message_id"] == "1"
    assert citation["canonical_id"] == identity.message_canonical_id_from_source(
        SOURCE_VISUAL, "1"
    )
    assert citation["identity_mode"] == identity.IDENTITY_SOURCE
    assert citation["timestamp_kind"] == "first_observed"


def test_a_citation_carries_no_message_text(populated):
    hit = populated.search(MemoryQuery(text="开会")).hits[0]
    assert "开会" not in repr(hit.citation())


def test_a_hit_carries_the_observation_metadata(populated):
    hit = populated.search(MemoryQuery(text="开会")).hits[0]
    assert hit.observation_count == 1
    assert hit.first_ingested_at == 1_000.0


# --- coverage travels with every result -------------------------------------


def test_a_result_inside_the_observed_window_is_complete(populated):
    result = populated.search(
        MemoryQuery(source=SOURCE_VISUAL, start=BASE + 10, end=BASE + 20)
    )
    assert result.coverage.status == COVERAGE_COMPLETE


def test_an_empty_result_inside_the_observed_window_is_trustworthy(populated):
    result = populated.search(
        MemoryQuery(text="取消", source=SOURCE_VISUAL, start=BASE, end=BASE + 100)
    )
    assert result.hits == ()
    assert result.is_empty_and_trustworthy


def test_an_empty_result_outside_the_observed_window_is_not(populated):
    """The whole point: nothing found is not the same as nothing there."""
    result = populated.search(
        MemoryQuery(
            text="取消", source=SOURCE_VISUAL, start=BASE + 5_000, end=BASE + 6_000
        )
    )
    assert result.hits == ()
    assert result.coverage.status == COVERAGE_NOT_OBSERVED
    assert not result.is_empty_and_trustworthy


def test_a_window_reaching_past_what_was_read_is_partial(populated):
    result = populated.search(
        MemoryQuery(source=SOURCE_VISUAL, start=BASE + 50, end=BASE + 5_000)
    )
    assert result.coverage.status == COVERAGE_PARTIAL
    assert not result.is_empty_and_trustworthy


def test_an_unread_source_reports_not_observed(populated):
    result = populated.search(MemoryQuery(source=SOURCE_DATABASE))
    assert result.hits == ()
    assert result.coverage.status == COVERAGE_NOT_OBSERVED


# --- retrieval never writes --------------------------------------------------


def test_retrieval_leaves_the_store_unchanged(store, populated):
    before = store.counts()
    populated.search(MemoryQuery(text="明天"))
    populated.recent()
    populated.conversations()
    assert store.counts() == before


def test_a_database_sourced_message_is_retrievable_identically(store):
    ingestor = MemoryIngestor(store)
    ingestor.ingest(
        SOURCE_DATABASE,
        [database_message(9001, 7, "明天下午三点开会", created_at=BASE)],
        coverage=[
            CoverageRecord(source=SOURCE_DATABASE, status=COVERAGE_COMPLETE)
        ],
        now=1_000.0,
    )
    result = MemoryRetriever(store).search(MemoryQuery(text="开会"))
    assert [hit.text for hit in result.hits] == ["明天下午三点开会"]
    assert result.hits[0].citation()["source"] == SOURCE_DATABASE
    assert result.hits[0].timestamp_kind == "source_created"


def test_a_corrected_message_stops_matching_its_old_text(store):
    """The index must forget, or a fixed typo stays searchable forever."""
    ingestor = MemoryIngestor(store)
    ingestor.ingest(SOURCE_VISUAL, [visual_message(1, 7, "明天开會")], now=1_000.0)
    retriever = MemoryRetriever(store)
    assert len(retriever.search(MemoryQuery(text="开會")).hits) == 1
    ingestor.ingest(SOURCE_VISUAL, [visual_message(1, 7, "明天开会")], now=2_000.0)
    assert retriever.search(MemoryQuery(text="开會")).hits == ()
    assert len(retriever.search(MemoryQuery(text="开会")).hits) == 1


def test_the_index_holds_one_row_per_message(store):
    ingestor = MemoryIngestor(store)
    batch = [visual_message(1, 7, "明天开会"), visual_message(2, 7, "好的")]
    ingestor.ingest(SOURCE_VISUAL, batch, now=1_000.0)
    ingestor.ingest(SOURCE_VISUAL, batch, now=2_000.0)
    indexed = store.connection.execute(
        "SELECT COUNT(*) AS n FROM messages_fts_map;"
    ).fetchone()["n"]
    assert indexed == 2
    assert len(MemoryRetriever(store).search(MemoryQuery(text="明天")).hits) == 1
