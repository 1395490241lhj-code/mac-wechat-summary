"""Logical identity above source observations (M1.1, schema v2).

Every row the ingestor writes is a *source observation*. Whether two of them
are the same WeChat object is a separate fact, recorded only when someone with
grounds asserts it, and otherwise left unknown. These tests pin the three
things that matter: nothing is merged by default, nothing can be merged on
content alone, and an explicit link is the only way two readers' rows meet.
"""

from __future__ import annotations

import sqlite3

import pytest

import memory_identity as identity
import memory_store as ms
from conftest import conversation, database_message, granted, visual_message
from memory_ingest import MemoryIngestor
from memory_retrieval import MemoryQuery, MemoryRetriever
from memory_store import (
    LINK_KIND_CONVERSATION,
    LINK_KIND_MESSAGE,
    LINK_OPERATOR,
    LINK_SOURCE_PROVIDED,
    MemoryStore,
    MemoryStoreError,
)
from message_source import SOURCE_DATABASE, SOURCE_VISUAL

VISUAL_1 = identity.message_canonical_id_from_source(SOURCE_VISUAL, "1")
DATABASE_1 = identity.message_canonical_id_from_source(SOURCE_DATABASE, "1")
VISUAL_CONV = identity.conversation_canonical_id(SOURCE_VISUAL, "7")
DATABASE_CONV = identity.conversation_canonical_id(SOURCE_DATABASE, "7")


@pytest.fixture()
def two_readers(store):
    """The same invented message, as each reader would describe it."""
    ingestor = MemoryIngestor(store)
    ingestor.ingest(
        SOURCE_VISUAL, [visual_message(1, 7, "明天开会")],
        conversations=[conversation(7, "项目组")], now=1_000.0,
    )
    ingestor.ingest(
        SOURCE_DATABASE, [database_message(1, 7, "明天开会")],
        conversations=[conversation(7, "项目组", source=SOURCE_DATABASE)], now=1_000.0,
    )
    return store


# --- v1 -> v2 migration ------------------------------------------------------


def build_v1_store(path):
    """A store exactly as M1 wrote it: the v1 DDL, user_version 1, some rows."""
    connection = sqlite3.connect(path)
    for statement in ms._SCHEMA_V1:
        connection.execute(statement)
    connection.execute("PRAGMA user_version = 1;")
    connection.execute(
        "INSERT INTO ingestion_runs (run_id, source, started_at, state)"
        " VALUES ('r1', 'visual', 1.0, 'succeeded');"
    )
    connection.execute(
        "INSERT INTO conversations (canonical_id, source, source_conversation_id,"
        " display_name, first_ingested_at, last_observed_at)"
        " VALUES (?, 'visual', '7', '项目组', 1.0, 1.0);",
        (VISUAL_CONV,),
    )
    connection.execute(
        "INSERT INTO messages (canonical_id, source, source_message_id,"
        " conversation_canonical_id, identity_mode, content_fingerprint, ownership,"
        " timestamp, timestamp_kind, kind, text, first_ingested_at, last_observed_at,"
        " observation_count, first_run_id, last_run_id)"
        " VALUES (?, 'visual', '1', ?, 'source', 'fp:x', 'other', 5.0,"
        " 'first_observed', 'text', '明天开会', 1.0, 1.0, 1, 'r1', 'r1');",
        (VISUAL_1, VISUAL_CONV),
    )
    connection.commit()
    connection.close()


def test_a_v1_store_migrates_to_v2_and_keeps_its_rows(tmp_path):
    build_v1_store(tmp_path / "memory.sqlite")
    with MemoryStore.open(granted(tmp_path)) as store:
        assert store.schema_version == 2
        assert store.message(VISUAL_1)["text"] == "明天开会"
        assert store.message(VISUAL_1)["logical_message_id"] is None
        assert store.conversation(VISUAL_CONV)["logical_conversation_id"] is None
        counts = store.counts()
        assert (counts["messages"], counts["conversations"], counts["runs"]) == (1, 1, 1)
        assert (counts["logical_messages"], counts["logical_conversations"]) == (0, 0)


def test_migration_is_deterministic_and_repeatable(tmp_path):
    build_v1_store(tmp_path / "memory.sqlite")
    with MemoryStore.open(granted(tmp_path)) as first:
        tables_first = sorted(
            row[0] for row in first.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
            )
        )
    with MemoryStore.open(granted(tmp_path)) as second:
        assert second.schema_version == 2
        tables_second = sorted(
            row[0] for row in second.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
            )
        )
    assert tables_first == tables_second
    assert {"logical_conversations", "logical_messages", "equivalence_links"} <= set(tables_first)


def test_a_fresh_store_and_a_migrated_store_have_the_same_shape(tmp_path):
    build_v1_store(tmp_path / "memory.sqlite")
    with MemoryStore.open(granted(tmp_path)) as migrated:
        migrated_columns = {
            table: [r[1] for r in migrated.connection.execute(f"PRAGMA table_info({table});")]
            for table in ("conversations", "messages", "logical_messages", "equivalence_links")
        }
    fresh_dir = tmp_path / "fresh"
    fresh_dir.mkdir()
    with MemoryStore.open(granted(fresh_dir)) as fresh:
        fresh_columns = {
            table: [r[1] for r in fresh.connection.execute(f"PRAGMA table_info({table});")]
            for table in ("conversations", "messages", "logical_messages", "equivalence_links")
        }
    assert migrated_columns == fresh_columns


def test_a_newer_schema_version_fails_closed(tmp_path):
    path = tmp_path / "memory.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version = 3;")
    connection.commit()
    connection.close()
    with pytest.raises(MemoryStoreError) as raised:
        MemoryStore.open(granted(tmp_path))
    assert raised.value.state == "schema_unsupported"


def test_migrated_rows_are_searchable_after_migration(tmp_path):
    """FTS state written under v1 is untouched by the v2 step."""
    build_v1_store(tmp_path / "memory.sqlite")
    with MemoryStore.open(granted(tmp_path)) as store:
        # v1 rows were inserted directly, so index them the way the store does.
        store._index_message(1, VISUAL_1, "明天开会", None)
        hits = MemoryRetriever(store).search(MemoryQuery(text="开会")).hits
        assert [hit.canonical_id for hit in hits] == [VISUAL_1]


# --- default: equivalence unknown -------------------------------------------


def test_identical_messages_from_two_readers_remain_distinct(two_readers):
    """The headline: same text, same second, same sender -- still two rows."""
    store = two_readers
    assert store.counts()["messages"] == 2
    assert store.message(VISUAL_1)["logical_message_id"] is None
    assert store.message(DATABASE_1)["logical_message_id"] is None
    assert store.counts()["logical_messages"] == 0


def test_identical_conversations_from_two_readers_remain_distinct(two_readers):
    store = two_readers
    assert store.counts()["conversations"] == 2
    assert store.conversation(VISUAL_CONV)["logical_conversation_id"] is None
    assert store.conversation(DATABASE_CONV)["logical_conversation_id"] is None


def test_a_hit_reports_unknown_equivalence_as_none(two_readers):
    hits = MemoryRetriever(two_readers).search(MemoryQuery(text="开会")).hits
    assert len(hits) == 2
    assert {hit.logical_message_id for hit in hits} == {None}
    # The citation contract is unchanged: it points at the observation.
    assert "logical_message_id" not in hits[0].citation()


def test_re_ingestion_never_touches_a_logical_link(two_readers):
    store = two_readers
    logical = store.link_observation(
        kind=LINK_KIND_MESSAGE, observation_canonical_id=VISUAL_1, logical_id=None,
        basis=LINK_OPERATOR, asserted_by="test", now=2_000.0,
    )
    MemoryIngestor(store).ingest(SOURCE_VISUAL, [visual_message(1, 7, "明天开会")], now=3_000.0)
    assert store.message(VISUAL_1)["logical_message_id"] == logical


# --- explicit equivalence ----------------------------------------------------


def test_an_explicit_link_joins_two_observations_under_one_logical_message(two_readers):
    store = two_readers
    logical = store.link_observation(
        kind=LINK_KIND_MESSAGE, observation_canonical_id=VISUAL_1, logical_id=None,
        basis=LINK_OPERATOR, asserted_by="operator", now=2_000.0,
    )
    joined = store.link_observation(
        kind=LINK_KIND_MESSAGE, observation_canonical_id=DATABASE_1, logical_id=logical,
        basis=LINK_OPERATOR, asserted_by="operator", now=2_001.0,
    )
    assert joined == logical
    observations = store.observations_of(LINK_KIND_MESSAGE, logical)
    assert {row["source"] for row in observations} == {SOURCE_VISUAL, SOURCE_DATABASE}
    # Both observation rows survive, unchanged, with their own provenance.
    assert store.counts()["messages"] == 2
    assert store.counts()["logical_messages"] == 1
    hits = MemoryRetriever(store).search(MemoryQuery(text="开会")).hits
    assert {hit.logical_message_id for hit in hits} == {logical}


def test_conversations_link_the_same_way(two_readers):
    store = two_readers
    logical = store.link_observation(
        kind=LINK_KIND_CONVERSATION, observation_canonical_id=VISUAL_CONV, logical_id=None,
        basis=LINK_SOURCE_PROVIDED, asserted_by="reader", now=2_000.0,
    )
    store.link_observation(
        kind=LINK_KIND_CONVERSATION, observation_canonical_id=DATABASE_CONV,
        logical_id=logical, basis=LINK_SOURCE_PROVIDED, asserted_by="reader", now=2_001.0,
    )
    assert len(store.observations_of(LINK_KIND_CONVERSATION, logical)) == 2


def test_a_logical_id_is_deterministic_from_its_founding_observation(two_readers):
    store = two_readers
    first = store.link_observation(
        kind=LINK_KIND_MESSAGE, observation_canonical_id=VISUAL_1, logical_id=None,
        basis=LINK_OPERATOR, asserted_by="operator", now=2_000.0,
    )
    assert first == ms._logical_id(LINK_KIND_MESSAGE, VISUAL_1)
    assert first.startswith("logm:")


def test_linking_the_same_observation_twice_is_idempotent(two_readers):
    store = two_readers
    first = store.link_observation(
        kind=LINK_KIND_MESSAGE, observation_canonical_id=VISUAL_1, logical_id=None,
        basis=LINK_OPERATOR, asserted_by="operator", now=2_000.0,
    )
    again = store.link_observation(
        kind=LINK_KIND_MESSAGE, observation_canonical_id=VISUAL_1, logical_id=None,
        basis=LINK_OPERATOR, asserted_by="operator", now=2_001.0,
    )
    assert again == first
    links = store.connection.execute("SELECT COUNT(*) FROM equivalence_links;").fetchone()[0]
    assert links == 1


def test_every_link_records_its_basis_and_asserter(two_readers):
    store = two_readers
    store.link_observation(
        kind=LINK_KIND_MESSAGE, observation_canonical_id=VISUAL_1, logical_id=None,
        basis=LINK_OPERATOR, asserted_by="operator", now=2_000.0,
    )
    row = store.connection.execute("SELECT * FROM equivalence_links;").fetchone()
    assert (row["basis"], row["asserted_by"], row["asserted_at"]) == (LINK_OPERATOR, "operator", 2_000.0)


# --- refusals ----------------------------------------------------------------


@pytest.mark.parametrize("basis", ["text_similarity", "fingerprint", "timestamp_sender_text", "", "vibes"])
def test_content_based_bases_are_refused(two_readers, basis):
    """No merge on text alone, and none on second + sender + content."""
    with pytest.raises(MemoryStoreError) as raised:
        two_readers.link_observation(
            kind=LINK_KIND_MESSAGE, observation_canonical_id=VISUAL_1, logical_id=None,
            basis=basis, asserted_by="anyone", now=2_000.0,
        )
    assert raised.value.state == "link_basis_refused"
    assert two_readers.counts()["logical_messages"] == 0


def test_a_contradicting_link_is_refused_not_moved(two_readers):
    store = two_readers
    first = store.link_observation(
        kind=LINK_KIND_MESSAGE, observation_canonical_id=VISUAL_1, logical_id=None,
        basis=LINK_OPERATOR, asserted_by="operator", now=2_000.0,
    )
    other = store.link_observation(
        kind=LINK_KIND_MESSAGE, observation_canonical_id=DATABASE_1, logical_id=None,
        basis=LINK_OPERATOR, asserted_by="operator", now=2_001.0,
    )
    assert first != other
    with pytest.raises(MemoryStoreError) as raised:
        store.link_observation(
            kind=LINK_KIND_MESSAGE, observation_canonical_id=VISUAL_1, logical_id=other,
            basis=LINK_OPERATOR, asserted_by="operator", now=2_002.0,
        )
    assert raised.value.state == "link_conflict"
    assert store.message(VISUAL_1)["logical_message_id"] == first


def test_an_unknown_observation_or_logical_object_is_refused(two_readers):
    with pytest.raises(MemoryStoreError) as raised:
        two_readers.link_observation(
            kind=LINK_KIND_MESSAGE, observation_canonical_id="msg:nope", logical_id=None,
            basis=LINK_OPERATOR, asserted_by="operator", now=2_000.0,
        )
    assert raised.value.state == "observation_unknown"
    with pytest.raises(MemoryStoreError) as raised:
        two_readers.link_observation(
            kind=LINK_KIND_MESSAGE, observation_canonical_id=VISUAL_1, logical_id="logm:nope",
            basis=LINK_OPERATOR, asserted_by="operator", now=2_000.0,
        )
    assert raised.value.state == "logical_unknown"


def test_the_ingestor_has_no_way_to_link(store):
    """Ingestion produces observations only; equivalence is not its job."""
    assert not hasattr(MemoryIngestor(store), "link_observation")
    import inspect
    import memory_ingest
    assert "link_observation" not in inspect.getsource(memory_ingest)
