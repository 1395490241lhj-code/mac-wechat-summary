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
    COVERAGE_NOT_OBSERVED,
    REASON_CALLER_LIMIT,
    REASON_EMPTY_WINDOW,
    REASON_FULL_WINDOW_OBSERVED,
    REASON_NO_OBSERVATION,
    REASON_SOURCE_LIMIT,
    REASON_TIMESTAMP_MISMATCH,
    REASON_UPSTREAM_MORE,
    SOURCE_DATABASE,
    SOURCE_VISUAL,
    MessageSourceError,
    ReadCoverage,
    ReadFreshness,
    ReadResult,
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


#: Reasons that mean the read was cut short, so ``truncated`` defaults on.
_CUT_SHORT = {REASON_CALLER_LIMIT, REASON_SOURCE_LIMIT, REASON_UPSTREAM_MORE}


def coverage_for(items, *, status=COVERAGE_COMPLETE, reason=None,
                 truncated=None, freshness=ReadFreshness.UNKNOWN,
                 requested_start=None, observed_through=None,
                 complete_through=None):
    """A lawful ``ReadCoverage`` a fake source authors for ``items``.

    Only the fields a test wants to steer are named; the rest fall to the
    quietest lawful values. Memory never sees this helper -- it is the
    *source's* statement, and the point of every test below is that Memory
    records that statement rather than working one out for itself.
    """
    if reason is None:
        reason = {
            COVERAGE_COMPLETE: REASON_FULL_WINDOW_OBSERVED if items else REASON_EMPTY_WINDOW,
            COVERAGE_NOT_OBSERVED: REASON_NO_OBSERVATION,
        }.get(status, REASON_SOURCE_LIMIT)
    if truncated is None:
        truncated = reason in _CUT_SHORT
    if status == COVERAGE_COMPLETE and complete_through is None:
        complete_through = observed_through
    return ReadCoverage(
        status=status, reason=reason,
        requested_start=requested_start, requested_end=None,
        observed_through=observed_through, complete_through=complete_through,
        freshness=freshness, truncated=truncated, item_count=len(items),
    )


class FakeSource:
    """A ``MessageSource`` that answers from memory. Contacts nothing.

    ``coverage`` maps a conversation id to keyword overrides for
    :func:`coverage_for`, so a test can have the source author exactly the
    verdict it is asserting on -- partial with a source limit, not observed,
    a through-moment later than any item -- without Memory knowing why.
    A conversation with no entry gets lawful complete coverage.
    """

    def __init__(self, name, conversations, messages, *, fail=None, coverage=None):
        self.name = name
        self._conversations = conversations
        self._messages = messages
        self._fail = fail
        self._coverage = coverage or {}

    def status(self):
        return SourceStatus(source=self.name, ready=True, state="ready")

    def list_conversations(self, limit):
        if self._fail:
            raise MessageSourceError(self._fail, "The source cannot answer.")
        items = tuple(self._conversations[:limit])
        return ReadResult(items=items, coverage=coverage_for(items))

    def get_messages(self, conversation_id, limit, before_sequence=None):
        items = tuple(self._messages.get(conversation_id, [])[:limit])
        overrides = self._coverage.get(conversation_id, {})
        return ReadResult(items=items, coverage=coverage_for(items, **overrides))

    def get_recent_messages(self, since_observed_at, limit):
        raise MessageSourceError("unsupported", "Not used by this test.")


def stored_coverage(store, source, conversation_id):
    """The persisted row itself, not a verdict derived from it."""
    rows = store.connection.execute(
        "SELECT status, reason, window_start, window_end, message_count"
        " FROM coverage WHERE source = ? AND conversation_canonical_id = ?"
        " ORDER BY id DESC;",
        (source, identity.conversation_canonical_id(source, str(conversation_id))),
    ).fetchall()
    return [dict(row) for row in rows]


def test_reading_a_whole_source_records_complete_coverage(store):
    source = FakeSource(
        SOURCE_VISUAL,
        [conversation(7, "项目组")],
        {7: [visual_message(1, 7, "明天开会"), visual_message(2, 7, "好的")]},
        # The source says so; Memory does not count its way there.
        coverage={7: dict(status=COVERAGE_COMPLETE,
                          observed_through=1_700_000_000.0)},
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
        coverage={7: dict(status=COVERAGE_PARTIAL, reason=REASON_CALLER_LIMIT)},
    )
    MemoryIngestor(store).ingest_from_source(source, message_limit=2, now=1_000.0)
    verdict = store.assess_coverage(
        source=SOURCE_VISUAL,
        conversation_canonical_id=identity.conversation_canonical_id(
            SOURCE_VISUAL, "7"
        ),
    )
    assert verdict.status == COVERAGE_PARTIAL
    # The source's own token, not the memory-local one. The constant stays
    # exported for rows already stored under it; the ingestor no longer
    # produces it.
    assert verdict.reasons == (REASON_CALLER_LIMIT,)
    assert REASON_LIMIT_REACHED == "limit_reached"



# --- P11: Memory records the coverage a source stated ------------------------

CANON_7 = identity.conversation_canonical_id(SOURCE_VISUAL, "7")


def test_a_short_answer_is_not_recorded_complete_when_the_source_says_partial(store):
    """The live defect. Three messages for a limit of two hundred.

    Under ``len(messages) < message_limit`` this is complete. The source has
    just said it is not -- its own sweep was bounded -- and that statement is
    the only thing Memory may record.
    """
    source = FakeSource(
        SOURCE_VISUAL,
        [conversation(7, "项目组")],
        {7: [visual_message(i, 7, f"消息{i}") for i in range(1, 4)]},
        coverage={7: dict(status=COVERAGE_PARTIAL, reason=REASON_SOURCE_LIMIT,
                          truncated=True)},
    )
    MemoryIngestor(store).ingest_from_source(source, message_limit=200, now=1_000.0)

    # Asked *inside* the observed window, so a falsely complete row would
    # contain the question and answer complete; the defect fails on status.
    verdict = store.assess_coverage(source=SOURCE_VISUAL,
                                    conversation_canonical_id=CANON_7,
                                    start=1_700_000_000.0, end=1_700_000_000.0)
    assert verdict.status == COVERAGE_PARTIAL
    assert verdict.reasons == (REASON_SOURCE_LIMIT,)


def test_memory_records_the_sources_status_and_reason(store):
    """No translation table. A token Memory never coined survives intact."""
    source = FakeSource(
        SOURCE_VISUAL,
        [conversation(7, "项目组")],
        {7: [visual_message(1, 7, "一条")]},
        coverage={7: dict(status=COVERAGE_PARTIAL, reason=REASON_TIMESTAMP_MISMATCH,
                          truncated=False,
                          freshness=ReadFreshness.POTENTIALLY_STALE)},
    )
    MemoryIngestor(store).ingest_from_source(source, message_limit=200, now=1_000.0)

    [row] = stored_coverage(store, SOURCE_VISUAL, 7)
    assert row["status"] == COVERAGE_PARTIAL
    assert row["reason"] == REASON_TIMESTAMP_MISMATCH
    assert row["message_count"] == 1


def test_a_not_observed_read_writes_no_coverage_row(store):
    """Absence of a row is how "this read did not look" is recorded.

    ``COVERAGE_STATES`` excludes ``not_observed`` on purpose: a stored row with
    that status would be a record of nothing, and ``assess_coverage`` already
    answers ``not_observed`` for a scope no row speaks to.
    """
    before = store.connection.execute("SELECT COUNT(*) FROM coverage;").fetchone()[0]
    source = FakeSource(
        SOURCE_VISUAL,
        [conversation(7, "项目组")],
        {7: []},
        coverage={7: dict(status=COVERAGE_NOT_OBSERVED, reason=REASON_NO_OBSERVATION)},
    )
    report = MemoryIngestor(store).ingest_from_source(source, message_limit=200,
                                                      now=1_000.0)

    assert report.state == RUN_SUCCEEDED
    after = store.connection.execute("SELECT COUNT(*) FROM coverage;").fetchone()[0]
    assert after == before
    assert stored_coverage(store, SOURCE_VISUAL, 7) == []
    verdict = store.assess_coverage(source=SOURCE_VISUAL,
                                    conversation_canonical_id=CANON_7)
    assert verdict.status == COVERAGE_NOT_OBSERVED


def test_the_boundaries_are_copied_from_coverage_not_derived_from_items(store):
    """A source that looked past its newest returned item may say so.

    The newest item is at 200; the source states it observed through 250.
    Memory's freshness must carry 250 -- the statement about the read -- not
    200, the statement about the data.
    """
    from memory_freshness import source_freshness

    source = FakeSource(
        SOURCE_VISUAL,
        [conversation(7, "项目组")],
        {7: [visual_message(1, 7, "一", observed_at=100.0),
             visual_message(2, 7, "二", observed_at=200.0)]},
        coverage={7: dict(status=COVERAGE_PARTIAL, reason=REASON_SOURCE_LIMIT,
                          truncated=True, observed_through=250.0)},
    )
    MemoryIngestor(store).ingest_from_source(source, message_limit=200, now=1_000.0)

    assert source_freshness(store, SOURCE_VISUAL).observed_through == 250.0
    [row] = stored_coverage(store, SOURCE_VISUAL, 7)
    assert row["window_end"] == 250.0


def test_a_partial_source_stays_partial_below_the_caller_limit(store):
    """The defect again, read straight off the persisted row."""
    source = FakeSource(
        SOURCE_VISUAL,
        [conversation(7, "项目组")],
        {7: [visual_message(i, 7, f"消息{i}") for i in range(1, 4)]},
        coverage={7: dict(status=COVERAGE_PARTIAL, reason=REASON_SOURCE_LIMIT,
                          truncated=True)},
    )
    MemoryIngestor(store).ingest_from_source(source, message_limit=200, now=1_000.0)

    [row] = stored_coverage(store, SOURCE_VISUAL, 7)
    assert row["status"] == COVERAGE_PARTIAL
    assert row["reason"] == REASON_SOURCE_LIMIT
    assert row["message_count"] == 3


def test_an_unbounded_read_keeps_its_observed_window_start(store):
    """An unbounded *request* is not an unbounded *observation*.

    ``_window_contains`` reads a ``None`` lower bound as "covers all of
    time". The source asked for no lower bound, but it observed messages
    from 1_700_000_100 onward, and that is the earliest moment this row may
    claim to speak for.
    """
    source = FakeSource(
        SOURCE_VISUAL,
        [conversation(7, "项目组")],
        {7: [visual_message(1, 7, "一", observed_at=1_700_000_100.0),
             visual_message(2, 7, "二", observed_at=1_700_000_200.0)]},
        coverage={7: dict(status=COVERAGE_COMPLETE, requested_start=None,
                          observed_through=1_700_000_200.0)},
    )
    MemoryIngestor(store).ingest_from_source(source, message_limit=200, now=1_000.0)

    [row] = stored_coverage(store, SOURCE_VISUAL, 7)
    assert row["window_start"] is not None
    assert row["window_start"] == 1_700_000_100.0
    assert row["window_end"] == 1_700_000_200.0


def test_an_unbounded_read_does_not_claim_coverage_it_never_looked_at(store):
    """The consequence of the bound, asserted where it bites.

    A question about a time well before the earliest observed message must
    not come back complete. This is the check that survives even if the
    direct ``window_start`` assertion above is ever loosened.
    """
    source = FakeSource(
        SOURCE_VISUAL,
        [conversation(7, "项目组")],
        {7: [visual_message(1, 7, "一", observed_at=1_700_000_100.0),
             visual_message(2, 7, "二", observed_at=1_700_000_200.0)]},
        coverage={7: dict(status=COVERAGE_COMPLETE, requested_start=None,
                          observed_through=1_700_000_200.0)},
    )
    MemoryIngestor(store).ingest_from_source(source, message_limit=200, now=1_000.0)

    long_before = store.assess_coverage(
        source=SOURCE_VISUAL, conversation_canonical_id=CANON_7,
        start=1_600_000_000.0, end=1_600_000_000.0)
    assert long_before.status != COVERAGE_COMPLETE

    inside = store.assess_coverage(
        source=SOURCE_VISUAL, conversation_canonical_id=CANON_7,
        start=1_700_000_150.0, end=1_700_000_150.0)
    assert inside.status == COVERAGE_COMPLETE


def test_an_explicit_source_lower_bound_wins_over_the_observed_one(store):
    """When the source *was* given a lower bound, that bound is the window."""
    source = FakeSource(
        SOURCE_VISUAL,
        [conversation(7, "项目组")],
        {7: [visual_message(1, 7, "一", observed_at=1_700_000_100.0)]},
        coverage={7: dict(status=COVERAGE_COMPLETE, requested_start=1_700_000_000.5,
                          observed_through=1_700_000_100.0)},
    )
    MemoryIngestor(store).ingest_from_source(source, message_limit=200, now=1_000.0)

    [row] = stored_coverage(store, SOURCE_VISUAL, 7)
    assert row["window_start"] == 1_700_000_000.5


def test_the_length_inference_is_gone_from_the_ingestor():
    """Structural: no ``len(...)`` is ever compared against a ``*limit*`` name.

    Deliberately narrow. Other ``len`` calls are fine; the one shape that
    reintroduces the defect -- as a fallback, behind a flag, as a default --
    is the comparison, and it is the comparison that is forbidden.
    """
    import ast
    import inspect

    import memory_ingest

    tree = ast.parse(inspect.getsource(memory_ingest))

    def is_len(node):
        return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "len")

    def names_limit(node):
        return any(isinstance(n, ast.Name) and "limit" in n.id.lower()
                   for n in ast.walk(node))

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        sides = [node.left, *node.comparators]
        if any(is_len(s) for s in sides) and any(names_limit(s) for s in sides):
            offenders.append(ast.unparse(node))

    assert offenders == [], offenders


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
