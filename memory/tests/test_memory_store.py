"""Schema, migration, the text index, and the coverage vocabulary."""

from __future__ import annotations

import sqlite3

import pytest

import memory_store as ms
from conftest import granted
from memory_store import (
    COVERAGE_COMPLETE,
    COVERAGE_NOT_OBSERVED,
    COVERAGE_PARTIAL,
    COVERAGE_UNAVAILABLE,
    CoverageRecord,
    MemoryStore,
    MemoryStoreError,
)

# --- schema ------------------------------------------------------------------


def test_a_fresh_store_is_created_at_the_current_version(tmp_path):
    with MemoryStore.open(granted(tmp_path)) as store:
        assert store.schema_version == ms.MEMORY_SCHEMA_VERSION
        tables = {
            row["name"]
            for row in store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view');"
            )
        }
    assert {"conversations", "messages", "ingestion_runs", "coverage"} <= tables


def test_reopening_an_existing_store_migrates_nothing(tmp_path):
    with MemoryStore.open(granted(tmp_path)) as first:
        first.connection.execute(
            "INSERT INTO ingestion_runs (run_id, source, started_at, state)"
            " VALUES ('r1', 'visual', 1.0, 'succeeded');"
        )
    with MemoryStore.open(granted(tmp_path)) as second:
        assert second.migrate() == ms.MEMORY_SCHEMA_VERSION
        assert second.counts()["runs"] == 1


def test_an_unknown_schema_version_fails_closed(tmp_path):
    path = tmp_path / "memory.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version = 99;")
    connection.commit()
    connection.close()
    with pytest.raises(MemoryStoreError) as raised:
        MemoryStore.open(granted(tmp_path))
    assert raised.value.state == "schema_unsupported"


def test_the_store_has_no_column_for_raw_capture_data(tmp_path):
    """The same structural guarantee the app's own store makes."""
    with MemoryStore.open(granted(tmp_path)) as store:
        columns = {
            row["name"]
            for row in store.connection.execute("PRAGMA table_info(messages);")
        } | {
            row["name"]
            for row in store.connection.execute("PRAGMA table_info(conversations);")
        }
    forbidden = {
        "image", "frame", "screenshot", "screenshot_path", "path", "file_path",
        "bounds", "normalized_bounds", "geometry", "provider_response", "api_key",
    }
    assert columns & forbidden == set()


def test_the_store_file_is_owner_only(tmp_path):
    with MemoryStore.open(granted(tmp_path)) as store:
        mode = (tmp_path / "memory.sqlite").stat().st_mode & 0o777
        assert mode == 0o600
        assert store.path.endswith("memory.sqlite")


# --- segmentation and the match expression ----------------------------------


def test_chinese_is_segmented_one_token_per_character():
    assert ms.segment("明天开会") == "明 天 开 会"


def test_latin_words_are_left_alone():
    assert ms.segment("meeting at 3pm") == "meeting at 3pm"


def test_mixed_text_keeps_both_conventions():
    assert ms.segment("明天 meeting 开会") == "明 天 meeting 开 会"


def test_a_query_becomes_quoted_phrases():
    assert ms.match_expression("开会") == '"开 会"'
    assert ms.match_expression("开会 tomorrow") == '"开 会" "tomorrow"'


def test_fts_syntax_in_a_query_is_neutralised():
    """Nothing a user types may be read as index syntax."""
    expression = ms.match_expression('AND OR NOT * "x" (y)')
    assert expression.count('"') % 2 == 0
    assert ms.match_expression('"') == '""""'


def test_an_empty_query_is_refused_rather_than_matching_everything():
    with pytest.raises(MemoryStoreError) as raised:
        ms.match_expression("   ")
    assert raised.value.state == "query_empty"


# --- run identifiers ---------------------------------------------------------


def test_a_run_id_carries_no_machine_identity():
    run = ms.new_run_id(1_700_000_000.0)
    assert run.startswith("run-1700000000000-")
    assert ms.new_run_id(1_700_000_000.0) != run  # not predictable either


# --- coverage ----------------------------------------------------------------


def test_no_record_means_not_observed_not_empty(store):
    verdict = store.assess_coverage(source="visual")
    assert verdict.status == COVERAGE_NOT_OBSERVED


def test_an_unbounded_complete_record_covers_any_window(store):
    store.begin_run("r1", "visual", 1.0)
    store.record_coverage(
        "r1", CoverageRecord(source="visual", status=COVERAGE_COMPLETE), 10.0
    )
    verdict = store.assess_coverage(source="visual", start=5.0, end=99.0)
    assert verdict.status == COVERAGE_COMPLETE
    assert verdict.is_complete


def test_a_bounded_record_does_not_cover_outside_itself(store):
    store.begin_run("r1", "visual", 1.0)
    store.record_coverage(
        "r1",
        CoverageRecord(
            source="visual",
            status=COVERAGE_COMPLETE,
            window_start=100.0,
            window_end=200.0,
        ),
        10.0,
    )
    assert (
        store.assess_coverage(source="visual", start=120.0, end=180.0).status
        == COVERAGE_COMPLETE
    )
    # Asking about a window that reaches beyond what was observed is partial,
    # never complete, and never "no messages".
    assert (
        store.assess_coverage(source="visual", start=120.0, end=500.0).status
        == COVERAGE_PARTIAL
    )
    assert (
        store.assess_coverage(source="visual", start=900.0, end=1000.0).status
        == COVERAGE_NOT_OBSERVED
    )


def test_a_partial_record_is_reported_with_its_reason(store):
    store.begin_run("r1", "visual", 1.0)
    store.record_coverage(
        "r1",
        CoverageRecord(
            source="visual", status=COVERAGE_PARTIAL, reason="limit_reached"
        ),
        10.0,
    )
    verdict = store.assess_coverage(source="visual")
    assert verdict.status == COVERAGE_PARTIAL
    assert verdict.reasons == ("limit_reached",)


def test_an_unavailable_source_is_not_an_empty_source(store):
    store.begin_run("r1", "database", 1.0)
    store.record_coverage(
        "r1",
        CoverageRecord(
            source="database", status=COVERAGE_UNAVAILABLE, reason="source_error"
        ),
        10.0,
    )
    verdict = store.assess_coverage(source="database")
    assert verdict.status == COVERAGE_UNAVAILABLE
    assert not verdict.is_complete


def test_a_later_failure_overrides_an_earlier_success(store):
    store.begin_run("r1", "database", 1.0)
    store.record_coverage(
        "r1", CoverageRecord(source="database", status=COVERAGE_COMPLETE), 10.0
    )
    store.begin_run("r2", "database", 2.0)
    store.record_coverage(
        "r2",
        CoverageRecord(
            source="database", status=COVERAGE_UNAVAILABLE, reason="source_error"
        ),
        20.0,
    )
    assert store.assess_coverage(source="database").status == COVERAGE_UNAVAILABLE


def test_coverage_is_scoped_per_source(store):
    store.begin_run("r1", "visual", 1.0)
    store.record_coverage(
        "r1", CoverageRecord(source="visual", status=COVERAGE_COMPLETE), 10.0
    )
    assert store.assess_coverage(source="visual").status == COVERAGE_COMPLETE
    assert store.assess_coverage(source="database").status == COVERAGE_NOT_OBSERVED


def test_a_source_wide_record_answers_for_a_conversation(store):
    store.begin_run("r1", "visual", 1.0)
    store.record_coverage(
        "r1", CoverageRecord(source="visual", status=COVERAGE_COMPLETE), 10.0
    )
    verdict = store.assess_coverage(
        source="visual", conversation_canonical_id="conv:abc"
    )
    assert verdict.status == COVERAGE_COMPLETE


def test_an_unrecognised_coverage_status_is_refused():
    with pytest.raises(MemoryStoreError) as raised:
        CoverageRecord(source="visual", status="probably_fine")
    assert raised.value.state == "coverage_status_unknown"
