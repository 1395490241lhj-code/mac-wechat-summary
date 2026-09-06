"""Identity, deduplication, incremental ingestion, atomicity, neutrality."""

from __future__ import annotations

import pytest

import memory_identity as identity
from conftest import conversation, database_message, visual_message
from memory_ingest import (
    REASON_LIMIT_REACHED,
    REASON_SOURCE_ERROR,
    MemoryIngestor,
    MemoryRecord,
)
from memory_store import (
    COVERAGE_COMPLETE,
    COVERAGE_PARTIAL,
    COVERAGE_UNAVAILABLE,
    RUN_FAILED,
    RUN_SUCCEEDED,
    TIME_FIRST_OBSERVED,
    TIME_SOURCE_CREATED,
    CoverageRecord,
    MemoryStoreError,
)
from message_source import (
    SOURCE_DATABASE,
    SOURCE_VISUAL,
    MessageSourceError,
    SourceStatus,
)

# --- identity ----------------------------------------------------------------


def test_a_canonical_id_is_deterministic_across_processes():
    first = identity.message_canonical_id_from_source("visual", "17")
    assert first == identity.message_canonical_id_from_source("visual", "17")
    assert first.startswith("msg:")


def test_the_same_source_id_in_two_sources_does_not_collide():
    assert identity.message_canonical_id_from_source(
        "visual", "17"
    ) != identity.message_canonical_id_from_source("database", "17")


def test_a_derived_id_ignores_sub_second_jitter():
    """A re-read differing only in fractions of a second is the same message."""
    arguments = ("visual", "conv:1", "同事A", "other", "text", "明天开会")
    assert identity.message_canonical_id_derived(
        *arguments, 1_700_000_000.4
    ) == identity.message_canonical_id_derived(*arguments, 1_700_000_000.9)


def test_a_derived_id_changes_when_the_text_changes():
    """The documented limitation: a corrected message is a new record."""
    arguments = ("visual", "conv:1", "同事A", "other", "text")
    assert identity.message_canonical_id_derived(
        *arguments, "明天开会", 1_700_000_000.0
    ) != identity.message_canonical_id_derived(
        *arguments, "明天开會", 1_700_000_000.0
    )


def test_a_missing_field_is_not_the_same_as_an_empty_one():
    arguments = ("visual", "conv:1")
    assert identity.message_canonical_id_derived(
        *arguments, None, "other", "text", "x", 1.0
    ) != identity.message_canonical_id_derived(
        *arguments, "", "other", "text", "x", 1.0
    )


def test_a_fingerprint_is_not_namespaced_by_source():
    """So the same message seen by two readers is recognisable as one."""
    arguments = ("conv:1", "同事A", "other", "text", "明天开会", 1_700_000_000.0)
    assert identity.content_fingerprint(*arguments) == identity.content_fingerprint(
        *arguments
    )


def test_text_is_normalised_only_for_comparison():
    assert identity.normalize_text("  明天开会  ") == "明天开会"
    assert identity.normalize_text(None) is None


# --- first ingestion ---------------------------------------------------------


def test_first_ingestion_writes_conversations_messages_and_a_run(store):
    ingestor = MemoryIngestor(store)
    report = ingestor.ingest(
        SOURCE_VISUAL,
        [visual_message(1, 7, "明天开会"), visual_message(2, 7, "好的")],
        conversations=[conversation(7, "项目组")],
        coverage=[CoverageRecord(source=SOURCE_VISUAL, status=COVERAGE_COMPLETE)],
        now=1_000.0,
    )
    assert report.state == RUN_SUCCEEDED
    assert (report.messages_inserted, report.messages_updated) == (2, 0)
    counts = store.counts()
    assert (counts["conversations"], counts["messages"], counts["runs"],
            counts["coverage_records"]) == (1, 2, 1, 1)
    # Nothing links observations to logical objects on ingestion.
    assert (counts["logical_conversations"], counts["logical_messages"]) == (0, 0)
    row = store.conversation(
        identity.conversation_canonical_id(SOURCE_VISUAL, "7")
    )
    assert row["display_name"] == "项目组"
    assert row["source"] == SOURCE_VISUAL
    assert row["first_ingested_at"] == 1_000.0


def test_a_stored_message_keeps_its_provenance_and_timestamp_meaning(store):
    MemoryIngestor(store).ingest(
        SOURCE_VISUAL, [visual_message(1, 7, "明天开会")], now=1_000.0
    )
    row = store.message(identity.message_canonical_id_from_source(SOURCE_VISUAL, "1"))
    assert row["source"] == SOURCE_VISUAL
    assert row["source_message_id"] == "1"
    assert row["identity_mode"] == identity.IDENTITY_SOURCE
    assert row["timestamp_kind"] == TIME_FIRST_OBSERVED
    assert row["visible_time"] == "昨天 14:30"
    assert row["observation_count"] == 1


def test_a_database_message_records_the_other_timestamp_meaning(store):
    MemoryIngestor(store).ingest(
        SOURCE_DATABASE, [database_message(9001, 7, "明天开会")], now=1_000.0
    )
    row = store.message(
        identity.message_canonical_id_from_source(SOURCE_DATABASE, "9001")
    )
    assert row["timestamp_kind"] == TIME_SOURCE_CREATED
    assert row["visible_time"] is None


# --- idempotence and incremental ingestion ----------------------------------


def test_an_identical_second_ingestion_adds_nothing(store):
    ingestor = MemoryIngestor(store)
    batch = [visual_message(1, 7, "明天开会"), visual_message(2, 7, "好的")]
    ingestor.ingest(SOURCE_VISUAL, batch, now=1_000.0)
    second = ingestor.ingest(SOURCE_VISUAL, batch, now=2_000.0)
    assert (second.messages_inserted, second.messages_updated) == (0, 2)
    assert store.counts()["messages"] == 2
    row = store.message(identity.message_canonical_id_from_source(SOURCE_VISUAL, "1"))
    assert row["first_ingested_at"] == 1_000.0
    assert row["last_observed_at"] == 2_000.0
    assert row["observation_count"] == 2


def test_an_overlapping_window_re_ingests_only_what_is_new(store):
    ingestor = MemoryIngestor(store)
    ingestor.ingest(
        SOURCE_VISUAL,
        [visual_message(index, 7, f"消息{index}") for index in (1, 2, 3)],
        now=1_000.0,
    )
    overlapping = ingestor.ingest(
        SOURCE_VISUAL,
        [visual_message(index, 7, f"消息{index}") for index in (2, 3, 4)],
        now=2_000.0,
    )
    assert (overlapping.messages_inserted, overlapping.messages_updated) == (1, 2)
    assert store.counts()["messages"] == 4


def test_one_new_message_is_one_new_record(store):
    ingestor = MemoryIngestor(store)
    ingestor.ingest(SOURCE_VISUAL, [visual_message(1, 7, "明天开会")], now=1_000.0)
    report = ingestor.ingest(SOURCE_VISUAL, [visual_message(2, 7, "好的")], now=2_000.0)
    assert (report.messages_inserted, report.messages_updated) == (1, 0)
    assert store.counts()["messages"] == 2


def test_a_re_observation_fills_in_what_was_unknown_without_erasing(store):
    """Updated observation metadata, and no field is lost to a later NULL."""
    ingestor = MemoryIngestor(store)
    ingestor.ingest(
        SOURCE_VISUAL,
        [visual_message(1, 7, "明天开会", sender="同事A")],
        now=1_000.0,
    )
    thinner = visual_message(1, 7, None, sender=None, visible_time=None)
    ingestor.ingest(SOURCE_VISUAL, [thinner], now=2_000.0)
    row = store.message(identity.message_canonical_id_from_source(SOURCE_VISUAL, "1"))
    assert row["text"] == "明天开会"
    assert row["sender"] == "同事A"
    assert row["visible_time"] == "昨天 14:30"
    assert row["observation_count"] == 2


def test_a_corrected_text_replaces_the_old_one_under_source_identity(store):
    ingestor = MemoryIngestor(store)
    ingestor.ingest(SOURCE_VISUAL, [visual_message(1, 7, "明天开會")], now=1_000.0)
    ingestor.ingest(SOURCE_VISUAL, [visual_message(1, 7, "明天开会")], now=2_000.0)
    row = store.message(identity.message_canonical_id_from_source(SOURCE_VISUAL, "1"))
    assert row["text"] == "明天开会"
    assert store.counts()["messages"] == 1


def test_derived_identity_collapses_a_repeat_of_the_same_message(store):
    """The documented cost of derived ids, asserted rather than hoped for."""
    ingestor = MemoryIngestor(store)
    report = ingestor.ingest(
        SOURCE_VISUAL,
        [visual_message(1, 7, "好的"), visual_message(2, 7, "好的")],
        identity_mode=identity.IDENTITY_DERIVED,
        now=1_000.0,
    )
    assert report.messages_seen == 2
    assert store.counts()["messages"] == 1
    assert report.messages_updated == 1


def test_derived_identity_survives_a_source_that_renumbers(store):
    """A store that was reset and re-read produces the same canonical records."""
    ingestor = MemoryIngestor(store)
    ingestor.ingest(
        SOURCE_VISUAL,
        [visual_message(11, 7, "明天开会"), visual_message(12, 7, "收到")],
        identity_mode=identity.IDENTITY_DERIVED,
        now=1_000.0,
    )
    renumbered = ingestor.ingest(
        SOURCE_VISUAL,
        [visual_message(1, 7, "明天开会"), visual_message(2, 7, "收到")],
        identity_mode=identity.IDENTITY_DERIVED,
        now=2_000.0,
    )
    assert renumbered.messages_inserted == 0
    assert store.counts()["messages"] == 2


def test_a_renumbered_source_under_source_identity_is_reported_as_duplicate(store):
    """Not merged, not hidden: counted, so the ambiguity stays visible."""
    ingestor = MemoryIngestor(store)
    ingestor.ingest(SOURCE_VISUAL, [visual_message(11, 7, "明天开会")], now=1_000.0)
    report = ingestor.ingest(SOURCE_VISUAL, [visual_message(1, 7, "明天开会")], now=2_000.0)
    assert report.duplicates_detected == 1
    assert store.counts()["messages"] == 2


def test_duplicate_source_records_in_one_batch_are_written_once(store):
    ingestor = MemoryIngestor(store)
    report = ingestor.ingest(
        SOURCE_VISUAL,
        [visual_message(1, 7, "明天开会"), visual_message(1, 7, "明天开会")],
        now=1_000.0,
    )
    assert report.messages_seen == 2
    assert (report.messages_inserted, report.messages_updated) == (1, 1)
    assert store.counts()["messages"] == 1


# --- atomicity ---------------------------------------------------------------


def test_a_malformed_record_rejects_the_whole_batch(store):
    ingestor = MemoryIngestor(store)
    broken = visual_message(2, 7, "好的")
    object.__setattr__(broken, "ownership", "")
    with pytest.raises(MemoryStoreError) as raised:
        ingestor.ingest(
            SOURCE_VISUAL, [visual_message(1, 7, "明天开会"), broken], now=1_000.0
        )
    assert raised.value.state == "record_malformed"
    assert store.counts()["messages"] == 0
    assert store.counts()["conversations"] == 0


def test_a_failed_run_is_still_recorded_as_having_happened(store):
    ingestor = MemoryIngestor(store)
    broken = visual_message(1, 7, "好的")
    object.__setattr__(broken, "first_observed_at", "yesterday")
    with pytest.raises(MemoryStoreError):
        ingestor.ingest(SOURCE_VISUAL, [broken], now=1_000.0)
    runs = store.runs()
    assert len(runs) == 1
    assert runs[0]["state"] == RUN_FAILED
    assert runs[0]["failure_state"] == "record_malformed"


def test_a_record_claiming_another_source_is_refused(store):
    with pytest.raises(MemoryStoreError) as raised:
        MemoryIngestor(store).ingest(
            SOURCE_VISUAL, [database_message(1, 7, "明天开会")], now=1_000.0
        )
    assert raised.value.state == "record_source_mismatch"


def test_an_unknown_identity_mode_is_refused(store):
    with pytest.raises(MemoryStoreError) as raised:
        MemoryIngestor(store).ingest(
            SOURCE_VISUAL, [visual_message(1, 7, "x")], identity_mode="vibes"
        )
    assert raised.value.state == "identity_mode_unknown"


# --- optional fields ---------------------------------------------------------


def test_a_reply_reference_is_resolved_to_a_canonical_id(store):
    ingestor = MemoryIngestor(store)
    ingestor.ingest(SOURCE_VISUAL, [visual_message(1, 7, "明天开会")], now=1_000.0)
    ingestor.ingest(
        SOURCE_VISUAL,
        [MemoryRecord(message=visual_message(2, 7, "收到"), reply_to_source_id="1")],
        now=2_000.0,
    )
    row = store.message(identity.message_canonical_id_from_source(SOURCE_VISUAL, "2"))
    assert row["reply_to_canonical_id"] == identity.message_canonical_id_from_source(
        SOURCE_VISUAL, "1"
    )


def test_an_unknown_reply_and_conversation_kind_stay_null(store):
    MemoryIngestor(store).ingest(
        SOURCE_VISUAL, [visual_message(1, 7, "明天开会")], now=1_000.0
    )
    assert (
        store.message(
            identity.message_canonical_id_from_source(SOURCE_VISUAL, "1")
        )["reply_to_canonical_id"]
        is None
    )
    assert (
        store.conversation(
            identity.conversation_canonical_id(SOURCE_VISUAL, "7")
        )["kind"]
        is None
    )


def test_a_known_conversation_kind_is_stored(store):
    MemoryIngestor(store).ingest(
        SOURCE_VISUAL,
        [MemoryRecord(message=visual_message(1, 7, "x"), conversation_kind="group")],
        now=1_000.0,
    )
    assert (
        store.conversation(
            identity.conversation_canonical_id(SOURCE_VISUAL, "7")
        )["kind"]
        == "group"
    )


# --- reader-driven ingestion -------------------------------------------------


class FakeSource:
    """A ``MessageSource`` that answers from memory. Contacts nothing."""

    def __init__(self, name, conversations, messages, *, fail=None):
        self.name = name
        self._conversations = conversations
        self._messages = messages
        self._fail = fail

    def status(self):
        return SourceStatus(source=self.name, ready=True, state="ready")

    def list_conversations(self, limit):
        if self._fail:
            raise MessageSourceError(self._fail, "The source cannot answer.")
        return self._conversations[:limit]

    def get_messages(self, conversation_id, limit, before_sequence=None):
        return self._messages.get(conversation_id, [])[:limit]

    def get_recent_messages(self, since_observed_at, limit):
        raise MessageSourceError("unsupported", "Not used by this test.")


def test_reading_a_whole_source_records_complete_coverage(store):
    source = FakeSource(
        SOURCE_VISUAL,
        [conversation(7, "项目组")],
        {7: [visual_message(1, 7, "明天开会"), visual_message(2, 7, "好的")]},
    )
    report = MemoryIngestor(store).ingest_from_source(
        source, message_limit=10, now=1_000.0
    )
    assert report.state == RUN_SUCCEEDED
    assert report.messages_inserted == 2
    assert (
        store.assess_coverage(
            source=SOURCE_VISUAL,
            conversation_canonical_id=identity.conversation_canonical_id(
                SOURCE_VISUAL, "7"
            ),
            start=1_700_000_000.0,
            end=1_700_000_000.0,
        ).status
        == COVERAGE_COMPLETE
    )


def test_a_filled_limit_is_recorded_as_partial_not_complete(store):
    source = FakeSource(
        SOURCE_VISUAL,
        [conversation(7, "项目组")],
        {7: [visual_message(index, 7, f"消息{index}") for index in range(1, 6)]},
    )
    MemoryIngestor(store).ingest_from_source(source, message_limit=2, now=1_000.0)
    verdict = store.assess_coverage(
        source=SOURCE_VISUAL,
        conversation_canonical_id=identity.conversation_canonical_id(
            SOURCE_VISUAL, "7"
        ),
    )
    assert verdict.status == COVERAGE_PARTIAL
    assert verdict.reasons == (REASON_LIMIT_REACHED,)


def test_a_refusing_source_writes_nothing_and_records_unavailable(store):
    source = FakeSource(SOURCE_DATABASE, [], {}, fail="reader_unavailable")
    report = MemoryIngestor(store).ingest_from_source(source, now=1_000.0)
    assert report.state == RUN_FAILED
    assert report.failure_state == "reader_unavailable"
    assert store.counts()["messages"] == 0
    verdict = store.assess_coverage(source=SOURCE_DATABASE)
    assert verdict.status == COVERAGE_UNAVAILABLE
    assert verdict.reasons == (REASON_SOURCE_ERROR,)


# --- source neutrality -------------------------------------------------------

NEUTRAL_FIELDS = (
    "sender",
    "ownership",
    "sequence",
    "timestamp",
    "kind",
    "text",
    "identity_mode",
)


def canonical_shape(store, source):
    rows = store.connection.execute(
        "SELECT * FROM messages WHERE source = ? ORDER BY timestamp, sequence;",
        (source,),
    ).fetchall()
    return [{field: row[field] for field in NEUTRAL_FIELDS} for row in rows]


def test_two_reader_shapes_produce_one_canonical_representation(store):
    """The same three messages, described by each reader in its own shape."""
    texts = ["明天开会", "收到", "我下午回复你"]
    ingestor = MemoryIngestor(store)
    ingestor.ingest(
        SOURCE_VISUAL,
        [
            visual_message(
                index + 1,
                7,
                text,
                observed_at=1_700_000_000.0 + index,
                sequence=index + 1,
            )
            for index, text in enumerate(texts)
        ],
        conversations=[conversation(7, "项目组")],
        now=1_000.0,
    )
    ingestor.ingest(
        SOURCE_DATABASE,
        [
            database_message(
                9000 + index,
                7,
                text,
                created_at=1_700_000_000.0 + index,
                sequence=index + 1,
            )
            for index, text in enumerate(texts)
        ],
        conversations=[conversation(7, "项目组", source=SOURCE_DATABASE)],
        now=1_000.0,
    )
    assert canonical_shape(store, SOURCE_VISUAL) == canonical_shape(
        store, SOURCE_DATABASE
    )


def test_the_legitimate_differences_are_exactly_provenance_and_meaning(store):
    ingestor = MemoryIngestor(store)
    ingestor.ingest(SOURCE_VISUAL, [visual_message(1, 7, "明天开会")], now=1_000.0)
    ingestor.ingest(SOURCE_DATABASE, [database_message(1, 7, "明天开会")], now=1_000.0)
    visual = dict(
        store.message(identity.message_canonical_id_from_source(SOURCE_VISUAL, "1"))
    )
    database = dict(
        store.message(identity.message_canonical_id_from_source(SOURCE_DATABASE, "1"))
    )
    differing = {key for key in visual if visual[key] != database[key]}
    assert differing == {
        # Identity and provenance.
        "canonical_id",
        "source",
        "conversation_canonical_id",
        "content_fingerprint",
        "first_run_id",
        "last_run_id",
        # Facts that genuinely differ between a screen and a database row.
        "timestamp_kind",
        "visible_time",
        "confidence",
    }


def test_a_re_read_after_a_reset_is_recognised_by_its_fingerprint(store):
    """The case fingerprints exist for: renumbered ids, same message."""
    ingestor = MemoryIngestor(store)
    ingestor.ingest(SOURCE_VISUAL, [visual_message(11, 7, "明天开会")], now=1_000.0)
    ingestor.ingest(SOURCE_VISUAL, [visual_message(1, 7, "明天开会")], now=2_000.0)
    fingerprints = {
        row["content_fingerprint"]
        for row in store.connection.execute(
            "SELECT content_fingerprint FROM messages;"
        )
    }
    assert len(fingerprints) == 1
    assert store.counts()["messages"] == 2


def test_cross_source_duplicates_are_not_detected_and_that_is_documented(store):
    """A known M1 limit: conversation identity is still per-source.

    Two readers describing one chat produce different conversation canonical
    ids, so their fingerprints differ. Equating them needs a conversation
    mapping layer that M1 does not have, and guessing would merge chats that
    merely look alike.
    """
    ingestor = MemoryIngestor(store)
    ingestor.ingest(SOURCE_VISUAL, [visual_message(1, 7, "明天开会")], now=1_000.0)
    report = ingestor.ingest(
        SOURCE_DATABASE, [database_message(1, 7, "明天开会")], now=1_000.0
    )
    assert report.duplicates_detected == 0
    assert store.counts()["messages"] == 2
