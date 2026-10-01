"""The bundled memory worker (M2.2d): protocol, consent, source, isolation.

Every test drives the worker the way the app does -- one JSON object in, one
out -- either in-process or as a real subprocess. No real WeChat data, no real
preference domain: consent is injected in-process and, for subprocess tests,
read from a fixture plist under an isolated HOME.
"""

from __future__ import annotations

import io
import json
import os
import plistlib
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import memory_consent as consent
import memory_paths as paths
import memory_worker as worker
from archive_message_source import SOURCE_ARCHIVE
from conftest import app_state, conversation, visual_message
from message_source import (
    COVERAGE_COMPLETE,
    REASON_EMPTY_WINDOW,
    REASON_FULL_WINDOW_OBSERVED,
    SOURCE_VISUAL,
    MessageSourceError,
    ReadCoverage,
    ReadFreshness,
    ReadResult,
    SourceStatus,
)


def complete_result(items):
    """``items`` under lawful complete coverage the stub itself authors."""
    items = tuple(items)
    return ReadResult(items=items, coverage=ReadCoverage(
        status=COVERAGE_COMPLETE,
        reason=REASON_FULL_WINDOW_OBSERVED if items else REASON_EMPTY_WINDOW,
        requested_start=None, requested_end=None,
        observed_through=None, complete_through=None,
        freshness=ReadFreshness.UNKNOWN, truncated=False,
        item_count=len(items)))

ROOT = Path(__file__).resolve().parents[2]
FROZEN = ROOT / ".build/memory-worker/dist/MemoryWorker.app/Contents/MacOS/MemoryWorker"


#: Distinct from ``None``, which is itself a meaningful consent state: the app
#: has recorded no decision. Without this, "consent absent" and "test said
#: nothing" would be the same argument.
CONSENTED = object()


def run(request: dict, *, state=CONSENTED) -> tuple[dict, int]:
    """One in-process request/response cycle with injected app consent."""
    resolved = app_state(True) if state is CONSENTED else state
    out = io.StringIO()
    code = worker.main(
        io.StringIO(json.dumps(request)), out,
        read_app_consent_state=lambda: resolved,
    )
    return json.loads(out.getvalue()), code


class FakeSource:
    name = SOURCE_VISUAL

    def __init__(self, messages, *, fail=None):
        self._messages, self._fail = messages, fail
        self.calls = 0

    def status(self): return SourceStatus(source=self.name, ready=True, state="ready")

    def list_conversations(self, limit):
        self.calls += 1
        if self._fail:
            raise MessageSourceError(self._fail, "The source cannot answer.")
        return complete_result([conversation(7, "项目组")])

    def get_messages(self, conversation_id, limit, before_sequence=None):
        return complete_result(self._messages[:limit])

    def get_recent_messages(self, since, limit): raise MessageSourceError("unsupported", "unused")


@pytest.fixture()
def synthetic(monkeypatch):
    """The worker's source is a fake; nothing reads a real store."""
    source = FakeSource([visual_message(1, 7, "明天开会"), visual_message(2, 7, "好的")])
    monkeypatch.setattr(worker, "build_selected_source", lambda: source)
    return source


# --- protocol -------------------------------------------------------------------


def test_paths_answers_without_consent_and_without_an_absolute_path():
    reply, code = run({"op": "paths"}, state=None)
    assert (reply["ok"], code) == (True, 0)
    assert reply["relative_store_path"] == paths.relative_store_path()
    assert "/Users/" not in json.dumps(reply)


@pytest.mark.parametrize("request_body,state", [
    ({"op": "delete"}, "invalid_request"),
    ({"op": 7}, "invalid_request"),
    ({}, "invalid_request"),
    ({"op": "sync", "conversation_limit": "many"}, "invalid_request"),
    ({"op": "sync", "message_source": "carrier-pigeon"}, "invalid_request"),
    ({"op": "sync", "store_path": ""}, "invalid_request"),
    ({"op": "summary_input", "message_source": "visual"}, "invalid_request"),
    ({"op": "summary_input", "message_source": "visual", "start": 2, "end": 1}, "invalid_request"),
    ({"op": "summary_input", "message_source": "carrier-pigeon", "start": 1, "end": 2}, "invalid_request"),
    ({"op": "reminder_candidates", "message_source": "visual"}, "invalid_request"),
    ({"op": "reminder_candidates", "message_source": "visual", "start": 2, "end": 1}, "invalid_request"),
    ({"op": "reminder_candidates", "message_source": "carrier-pigeon", "start": 1, "end": 2}, "invalid_request"),
])
def test_an_unusable_request_is_refused_with_exit_two(request_body, state):
    reply, code = run(request_body)
    assert (reply["state"], code) == (state, worker.EXIT_BAD_REQUEST)


def test_malformed_json_is_refused():
    out = io.StringIO()
    code = worker.main(io.StringIO("{not json"), out)
    assert (json.loads(out.getvalue())["state"], code) == ("malformed_request", 2)


def test_an_oversized_request_is_refused_before_being_parsed():
    out = io.StringIO()
    code = worker.main(io.StringIO(" " * (worker.MAX_REQUEST_BYTES + 5)), out)
    assert (json.loads(out.getvalue())["state"], code) == ("request_too_large", 2)


def test_there_is_no_operation_that_deletes_or_runs_arbitrary_commands():
    # answer_evidence is a read: it names no command, writes nothing, and
    # exposes no delete. What it may not do is grow into anything else.
    assert worker.OPERATIONS <= {
        "sync", "status", "paths", "summary_input", "reminder_candidates",
        "answer_evidence",
    }
    assert worker.OPERATIONS == {
        "sync", "status", "paths", "summary_input", "reminder_candidates",
        "answer_evidence",
    }


# --- consent --------------------------------------------------------------------


@pytest.mark.parametrize("state,expected", [
    (None, "consent_state_missing"),
    ({"version": 1}, "consent_state_malformed"),
    (app_state(False), "consent_withheld"),
])
def test_consent_refusals_create_nothing_and_touch_no_source(tmp_path, synthetic, state, expected):
    store = tmp_path / "Library/Application Support/WeChatCompanion/memory.sqlite"
    reply, code = run({"op": "sync", "store_path": str(store)}, state=state)
    assert (reply["ok"], reply["state"], code) == (False, expected, 1)
    assert synthetic.calls == 0
    assert not store.exists() and not store.parent.exists()


def test_activation_alone_does_not_authorise(tmp_path, synthetic, monkeypatch):
    """The worker sets its own activation; the app's consent still decides."""
    monkeypatch.setenv(consent.MEMORY_ENABLED_ENV, "1")
    monkeypatch.setenv(consent.MEMORY_DB_PATH_ENV, str(tmp_path / "memory.sqlite"))
    reply, _ = run({"op": "sync", "store_path": str(tmp_path / "memory.sqlite")}, state=None)
    assert reply["state"] == "consent_state_missing"


# --- sync ------------------------------------------------------------------------


def test_a_first_sync_inserts_and_reports_counts_and_freshness(tmp_path, synthetic):
    store = paths.canonical_store_path(tmp_path)
    reply, code = run({"op": "sync", "store_path": str(store)})
    assert (reply["ok"], code) == (True, 0)
    assert reply["counts"]["messages_inserted"] == 2
    assert reply["counts"]["messages_updated"] == 0
    assert reply["freshness"]["participating_sources"] == [SOURCE_VISUAL]
    assert store.exists()
    assert stat.S_IMODE(store.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.parent.stat().st_mode) == 0o700


def test_a_second_identical_sync_is_idempotent(tmp_path, synthetic):
    store = paths.canonical_store_path(tmp_path)
    run({"op": "sync", "store_path": str(store)})
    reply, _ = run({"op": "sync", "store_path": str(store)})
    assert reply["counts"]["messages_inserted"] == 0
    assert reply["counts"]["messages_updated"] == 2


def test_one_new_message_appears_on_the_next_sync(tmp_path, monkeypatch):
    store = paths.canonical_store_path(tmp_path)
    first = FakeSource([visual_message(1, 7, "明天开会")])
    monkeypatch.setattr(worker, "build_selected_source", lambda: first)
    run({"op": "sync", "store_path": str(store)})
    second = FakeSource([visual_message(1, 7, "明天开会"), visual_message(2, 7, "好的")])
    monkeypatch.setattr(worker, "build_selected_source", lambda: second)
    reply, _ = run({"op": "sync", "store_path": str(store)})
    assert reply["counts"]["messages_inserted"] == 1



def test_summary_input_reads_memory_only_and_returns_bounded_provenance(tmp_path, synthetic):
    store = paths.canonical_store_path(tmp_path)
    synced, sync_code = run({"op": "sync", "store_path": str(store)})
    assert (synced["ok"], sync_code) == (True, 0)

    reply, code = run({
        "op": "summary_input",
        "store_path": str(store),
        "message_source": SOURCE_VISUAL,
        "start": 1_699_999_000.0,
        "end": 1_700_001_000.0,
        "message_limit": 1,
    })

    assert (reply["ok"], code) == (True, 0)
    assert reply["state"] == "ready"
    assert reply["source"] == SOURCE_VISUAL
    assert reply["counts"]["returned_messages"] == 1
    assert reply["counts"]["returned_conversations"] == 1
    assert reply["truncated"] is True
    assert reply["conversations"][0]["label"] == "项目组"
    assert reply["messages"][0]["source"] == SOURCE_VISUAL
    assert reply["messages"][0]["timestamp_kind"] == "first_observed"
    assert "coverage" in reply and "trustworthy_empty" in reply["coverage"]
    assert "freshness" in reply
    blob = json.dumps(reply, ensure_ascii=False)
    assert "canonical_message_id" not in blob
    assert "canonical_conversation_id" not in blob
    assert str(tmp_path) not in blob


def test_summary_input_does_not_turn_not_observed_into_empty(tmp_path, synthetic):
    store = paths.canonical_store_path(tmp_path)
    run({"op": "sync", "store_path": str(store)})

    reply, code = run({
        "op": "summary_input",
        "store_path": str(store),
        "message_source": SOURCE_VISUAL,
        "start": 1_800_000_000.0,
        "end": 1_800_003_600.0,
    })

    assert (reply["ok"], code) == (True, 0)
    assert reply["messages"] == []
    assert reply["coverage"]["trustworthy_empty"] is False
    assert reply["coverage"]["status"] != "complete"




def test_reminder_candidates_require_explicit_action_language(tmp_path, monkeypatch):
    store = paths.canonical_store_path(tmp_path)
    source = FakeSource([
        visual_message(1, 7, "明天开会"),
        visual_message(2, 7, "麻烦明天确认一下报价"),
        visual_message(3, 7, "好的"),
        visual_message(4, 7, "我会明天跟进这个事情"),
    ])
    monkeypatch.setattr(worker, "build_selected_source", lambda: source)
    run({"op": "sync", "store_path": str(store)})

    reply, code = run({
        "op": "reminder_candidates",
        "store_path": str(store),
        "message_source": SOURCE_VISUAL,
        "start": 1_699_999_000.0,
        "end": 1_700_001_000.0,
        "message_limit": 200,
        "candidate_limit": 50,
    })

    assert (reply["ok"], code) == (True, 0)
    assert reply["source"] == SOURCE_VISUAL
    assert reply["counts"]["scanned_messages"] == 4
    assert reply["counts"]["returned_candidates"] == 2
    texts = [item["text"] for item in reply["candidates"]]
    assert "明天开会" not in texts
    assert "麻烦明天确认一下报价" in texts
    assert "我会明天跟进这个事情" in texts

    request_candidate = next(
        item for item in reply["candidates"]
        if item["text"] == "麻烦明天确认一下报价"
    )
    assert "explicit_request" in request_candidate["reasons"]
    assert "explicit_follow_up" in request_candidate["reasons"]
    assert "time_reference" in request_candidate["reasons"]

    commitment = next(
        item for item in reply["candidates"]
        if item["text"] == "我会明天跟进这个事情"
    )
    assert "explicit_commitment" in commitment["reasons"]
    assert "time_reference" in commitment["reasons"]

    blob = json.dumps(reply, ensure_ascii=False)
    assert "canonical_message_id" not in blob
    assert "canonical_conversation_id" not in blob
    assert str(tmp_path) not in blob


def test_reminder_candidates_are_bounded_and_keep_coverage(tmp_path, monkeypatch):
    store = paths.canonical_store_path(tmp_path)
    source = FakeSource([
        visual_message(1, 7, "请确认A"),
        visual_message(2, 7, "请确认B"),
        visual_message(3, 7, "请确认C"),
    ])
    monkeypatch.setattr(worker, "build_selected_source", lambda: source)
    run({"op": "sync", "store_path": str(store)})

    reply, code = run({
        "op": "reminder_candidates",
        "store_path": str(store),
        "message_source": SOURCE_VISUAL,
        "start": 1_699_999_000.0,
        "end": 1_700_001_000.0,
        "candidate_limit": 1,
    })

    assert (reply["ok"], code) == (True, 0)
    assert reply["counts"]["returned_candidates"] == 1
    assert reply["candidates"][0]["text"] == "请确认C"
    assert reply["truncated"] is True
    assert "coverage" in reply
    assert "trustworthy_empty" in reply["coverage"]


def test_reminder_candidates_do_not_turn_not_observed_into_nothing_to_do(tmp_path, synthetic):
    store = paths.canonical_store_path(tmp_path)
    run({"op": "sync", "store_path": str(store)})

    reply, code = run({
        "op": "reminder_candidates",
        "store_path": str(store),
        "message_source": SOURCE_VISUAL,
        "start": 1_800_000_000.0,
        "end": 1_800_003_600.0,
    })

    assert (reply["ok"], code) == (True, 0)
    assert reply["candidates"] == []
    assert reply["coverage"]["trustworthy_empty"] is False
    assert reply["coverage"]["status"] != "complete"


# --- canonical Archive evidence anchors ------------------------------------


def archive_message_store(path: Path, *, attributed: int = 2) -> Path:
    """A minimal supported-schema Archive store, synthetic throughout.

    Schema version 2 is used on purpose: it is the smallest supported version
    and therefore needs the fewest tables, none of which involve attachments
    or labels. Every string here is invented for this test file.
    """
    import sqlite3

    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        PRAGMA user_version = 2;
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY, title TEXT,
            first_seen_at REAL, last_seen_at REAL
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY, conversation_id INTEGER, sequence INTEGER,
            sender TEXT, ownership TEXT, visible_time TEXT, text TEXT,
            kind TEXT, confidence REAL, first_observed_at REAL
        );
        CREATE TABLE archive_conversations (
            id INTEGER PRIMARY KEY, source_conversation_key TEXT NOT NULL
        );
        CREATE TABLE archive_imports (
            id INTEGER PRIMARY KEY, archive_conversation_id INTEGER NOT NULL,
            transcript_shape TEXT NOT NULL, imported_at REAL NOT NULL
        );
        CREATE TABLE archive_attributed_records (
            import_id INTEGER NOT NULL, sequence INTEGER NOT NULL,
            sender TEXT NOT NULL, sent_at REAL NOT NULL,
            sent_at_text TEXT NOT NULL, text TEXT NOT NULL
        );
        CREATE TABLE archive_unattributed_records (
            import_id INTEGER NOT NULL, sequence INTEGER NOT NULL,
            record_text TEXT NOT NULL
        );
        INSERT INTO archive_conversations VALUES (1, 'archive-a');
        INSERT INTO archive_imports VALUES (1, 1, 'attributed', 1000.0);
        INSERT INTO archive_imports VALUES (2, 1, 'unattributed', 2000.0);
        INSERT INTO archive_unattributed_records VALUES (2, 0, '不应进入 Memory');
        """
    )
    texts = ["麻烦明天确认一下报价", "我会明天跟进这个事情"]
    for sequence in range(attributed):
        connection.execute(
            """INSERT INTO archive_attributed_records
               VALUES (1, ?, '林晓', ?, '昨天 10:00', ?);""",
            (sequence, 100.0 + sequence, texts[sequence % len(texts)]),
        )
    connection.commit()
    connection.close()
    return path


def scan_archive_candidates(tmp_path, *, count: int = 2) -> dict:
    """Sync a synthetic Archive store and scan it for follow-up candidates."""
    store = paths.canonical_store_path(tmp_path)
    messages = archive_message_store(
        paths.canonical_message_store_path(tmp_path), attributed=count
    )
    run({
        "op": "sync",
        "store_path": str(store),
        "message_store_path": str(messages),
        "message_source": SOURCE_ARCHIVE,
        "conversation_limit": 10,
        "message_limit": 10,
    })
    reply, code = run({
        "op": "reminder_candidates",
        "store_path": str(store),
        "message_source": SOURCE_ARCHIVE,
        "start": 50.0,
        "end": 150.0,
    })
    assert (reply["ok"], code) == (True, 0), reply
    return reply


def test_archive_candidates_carry_their_exact_canonical_anchor(tmp_path):
    reply = scan_archive_candidates(tmp_path, count=1)

    assert reply["counts"]["returned_candidates"] == 1
    anchor = reply["candidates"][0]["archive_evidence"]
    assert anchor == {"import_id": 1, "sequence": 0}


def test_each_archive_candidate_keeps_its_own_anchor(tmp_path):
    reply = scan_archive_candidates(tmp_path, count=2)

    assert reply["counts"]["returned_candidates"] == 2
    anchors = [item["archive_evidence"] for item in reply["candidates"]]
    assert anchors == [
        {"import_id": 1, "sequence": 0},
        {"import_id": 1, "sequence": 1},
    ]


def test_unattributed_and_visual_candidates_carry_no_archive_anchor(
    tmp_path, monkeypatch
):
    store = paths.canonical_store_path(tmp_path)
    messages = archive_message_store(
        paths.canonical_message_store_path(tmp_path), attributed=1
    )
    run({
        "op": "sync",
        "store_path": str(store),
        "message_store_path": str(messages),
        "message_source": SOURCE_ARCHIVE,
        "conversation_limit": 10,
        "message_limit": 10,
    })
    archive_reply, _ = run({
        "op": "reminder_candidates",
        "store_path": str(store),
        "message_source": SOURCE_ARCHIVE,
        "start": 50.0,
        "end": 150.0,
    })
    # Unattributed evidence never reaches Memory, so it cannot be anchored.
    assert "不应进入 Memory" not in json.dumps(archive_reply, ensure_ascii=False)
    for candidate in archive_reply["candidates"]:
        assert candidate["archive_evidence"]["import_id"] == 1

    visual = FakeSource([visual_message(1, 7, "麻烦明天确认一下报价")])
    monkeypatch.setattr(worker, "build_selected_source", lambda: visual)
    run({"op": "sync", "store_path": str(store)})
    visual_reply, _ = run({
        "op": "reminder_candidates",
        "store_path": str(store),
        "message_source": SOURCE_VISUAL,
        "start": 1_699_999_000.0,
        "end": 1_700_001_000.0,
    })
    assert visual_reply["counts"]["returned_candidates"] == 1
    assert "archive_evidence" not in visual_reply["candidates"][0]


# --- answer_evidence: the deterministic evidence window ----------------------


def evidence_window(tmp_path, **overrides) -> dict:
    """Sync a synthetic Archive store, then ask it for an evidence window."""
    import sqlite3

    attributed = overrides.pop("attributed", 2)
    store = paths.canonical_store_path(tmp_path)
    messages = paths.canonical_message_store_path(tmp_path)
    if not messages.exists():
        archive_message_store(messages, attributed=0)
    connection = sqlite3.connect(messages)
    try:
        connection.execute("DELETE FROM archive_attributed_records")
        for sequence in range(attributed):
            connection.execute(
                """INSERT INTO archive_attributed_records
                   VALUES (1, ?, '林晓', ?, '昨天 10:00', '麻烦明天确认一下报价');""",
                (sequence, 100.0 + sequence),
            )
        connection.commit()
    finally:
        connection.close()
    run({
        "op": "sync",
        "store_path": str(store),
        "message_store_path": str(messages),
        "message_source": SOURCE_ARCHIVE,
        "conversation_limit": 10,
        "message_limit": 10,
    })
    request = {
        "op": "answer_evidence",
        "store_path": str(store),
        "message_source": SOURCE_ARCHIVE,
        "start": 50.0,
        "end": 150.0,
    }
    request.update(overrides)
    return run(request)[0]


def test_answer_evidence_is_a_recognized_packaged_operation(tmp_path):
    assert "answer_evidence" in worker.OPERATIONS
    reply = evidence_window(tmp_path)
    assert (reply["ok"], reply["op"], reply["state"]) == (
        True, "answer_evidence", "ready")


@pytest.mark.parametrize("source", [SOURCE_VISUAL, "database", None, "nonsense", 7])
def test_answer_evidence_refuses_every_non_archive_source(tmp_path, source):
    # Recognized first: an unrecognized op refuses everything, so the refusal
    # below has to be this operation's own.
    assert "answer_evidence" in worker.OPERATIONS
    store = paths.canonical_store_path(tmp_path)
    request = {"op": "answer_evidence", "store_path": str(store),
               "start": 50.0, "end": 150.0}
    if source is not None:
        request["message_source"] = source
    reply, code = run(request)
    assert (reply["ok"], code) == (False, 2)
    assert reply["state"] == "invalid_request"


@pytest.mark.parametrize("window", [{"start": 150.0, "end": 50.0},
                                    {"end": 150.0}, {"start": 50.0}])
def test_answer_evidence_refuses_a_reversed_or_missing_window(tmp_path, window):
    assert "answer_evidence" in worker.OPERATIONS
    store = paths.canonical_store_path(tmp_path)
    reply, code = run({"op": "answer_evidence", "store_path": str(store),
                       "message_source": SOURCE_ARCHIVE, **window})
    assert (reply["ok"], code) == (False, 2)


@pytest.mark.parametrize("field", ["question", "prompt", "model", "provider",
                                    "sql", "candidate_limit", "whatever"])
def test_answer_evidence_accepts_only_its_own_fields(tmp_path, field):
    assert "answer_evidence" in worker.OPERATIONS
    store = paths.canonical_store_path(tmp_path)
    reply, code = run({"op": "answer_evidence", "store_path": str(store),
                       "message_source": SOURCE_ARCHIVE, "start": 50.0, "end": 150.0,
                       field: "anything"})
    # A silently ignored `sql` is the one that would eventually be trusted.
    assert (reply["ok"], code) == (False, 2), field
    assert reply["state"] == "invalid_request"


def test_evidence_rows_are_oldest_first_and_carry_their_exact_anchor(tmp_path):
    reply = evidence_window(tmp_path, attributed=3)

    assert reply["counts"]["returned_evidence"] == 3
    assert [row["archive_evidence"] for row in reply["evidence"]] == [
        {"import_id": 1, "sequence": 0},
        {"import_id": 1, "sequence": 1},
        {"import_id": 1, "sequence": 2},
    ]
    assert [row["timestamp"] for row in reply["evidence"]] == [100.0, 101.0, 102.0]
    for row in reply["evidence"]:
        assert row["source"] == SOURCE_ARCHIVE
        assert row["canonical_message_id"] and row["canonical_conversation_id"]
        assert row["timestamp_kind"]
        assert row["sender"] == "林晓"


def test_identical_displayed_evidence_keeps_its_own_anchor(tmp_path):
    # Same text, same sender, same timestamp. Only the canonical anchor tells
    # these two rows apart, so each must keep its own.
    import sqlite3

    messages = archive_message_store(
        paths.canonical_message_store_path(tmp_path), attributed=0
    )
    connection = sqlite3.connect(messages)
    for sequence in (0, 1):
        connection.execute(
            """INSERT INTO archive_attributed_records
               VALUES (1, ?, '林晓', 100.0, '昨天 10:00', '麻烦明天确认一下报价');""",
            (sequence,),
        )
    connection.commit()
    connection.close()
    store = paths.canonical_store_path(tmp_path)
    run({"op": "sync", "store_path": str(store), "message_store_path": str(messages),
         "message_source": SOURCE_ARCHIVE, "conversation_limit": 10, "message_limit": 10})
    reply, code = run({"op": "answer_evidence", "store_path": str(store),
                       "message_source": SOURCE_ARCHIVE, "start": 50.0, "end": 150.0})

    assert (reply["ok"], code) == (True, 0), reply
    rows = reply["evidence"]
    assert len(rows) == 2
    assert {(r["text"], r["sender"], r["timestamp"]) for r in rows} == {
        ("麻烦明天确认一下报价", "林晓", 100.0)}
    assert {r["canonical_message_id"] for r in rows} and len(
        {r["canonical_message_id"] for r in rows}) == 2
    assert sorted(r["archive_evidence"]["sequence"] for r in rows) == [0, 1]


def test_unattributed_and_visual_evidence_never_enter_the_window(tmp_path, monkeypatch):
    reply = evidence_window(tmp_path, attributed=1)
    store = paths.canonical_store_path(tmp_path)
    visual = FakeSource([visual_message(1, 7, "这是视觉证据")])
    monkeypatch.setattr(worker, "build_selected_source", lambda: visual)
    run({"op": "sync", "store_path": str(store)})
    later, code = run({"op": "answer_evidence", "store_path": str(store),
                       "message_source": SOURCE_ARCHIVE, "start": 50.0, "end": 150.0})

    assert (reply["ok"], code) == (True, 0), later
    blob = json.dumps(later, ensure_ascii=False)
    assert "不应进入 Memory" not in blob
    assert "这是视觉证据" not in blob
    assert all(row["archive_evidence"]["import_id"] == 1
               for row in later["evidence"])


@pytest.mark.parametrize("identity", [None, "not-a-number"])
def test_a_row_without_a_usable_identity_is_never_approximated(tmp_path, identity):
    import sqlite3

    reply = evidence_window(tmp_path, attributed=1)
    assert reply["counts"]["returned_evidence"] == 1
    store = paths.canonical_store_path(tmp_path)
    connection = sqlite3.connect(store)
    connection.execute(
        "UPDATE messages SET source_message_id = ? WHERE canonical_id = "
        "(SELECT canonical_id FROM messages ORDER BY timestamp LIMIT 1)",
        (identity,),
    )
    connection.commit()
    connection.close()

    after, code = run({"op": "answer_evidence", "store_path": str(store),
                       "message_source": SOURCE_ARCHIVE, "start": 50.0, "end": 150.0})

    if identity is None:
        # No identity at all is not a malformed identity, only an
        # unrevealable row: it is excluded, and the window says so.
        assert (after["ok"], code) == (True, 0), after
        assert after["evidence"] == []
        assert after["counts"]["returned_evidence"] == 0
        assert after["counts"]["excluded_unanchored"] == 1
        assert after["truncated"] is True
    else:
        # An identity that exists but cannot be read is corruption, and is
        # refused rather than skipped: the store is not what it claims.
        assert (after["ok"], code) == (False, 1), after
        assert after["state"] == "archive_evidence_malformed"
        assert "evidence" not in after


def test_coverage_freshness_and_scope_come_from_the_existing_envelope(tmp_path):
    store = paths.canonical_store_path(tmp_path)
    messages = archive_message_store(
        paths.canonical_message_store_path(tmp_path), attributed=2
    )
    run({"op": "sync", "store_path": str(store), "message_store_path": str(messages),
         "message_source": SOURCE_ARCHIVE, "conversation_limit": 10, "message_limit": 10})
    summary, _ = run({"op": "summary_input", "store_path": str(store),
                      "message_source": SOURCE_ARCHIVE, "start": 50.0, "end": 150.0})
    reply, code = run({"op": "answer_evidence", "store_path": str(store),
                       "message_source": SOURCE_ARCHIVE, "start": 50.0, "end": 150.0})

    assert (reply["ok"], code) == (True, 0), reply
    assert reply["coverage"] == summary["coverage"]
    # Freshness is the query's own, carried through unchanged: only the
    # envelope's generation stamp may differ between two reads.
    reply_freshness = {k: v for k, v in reply["freshness"].items()
                       if k != "generated_at"}
    summary_freshness = {k: v for k, v in summary["freshness"].items()
                         if k != "generated_at"}
    assert reply_freshness == summary_freshness
    assert reply["source"] == SOURCE_ARCHIVE
    assert reply["window"] == {"start": 50.0, "end": 150.0}
    scope = reply["query_scope"]
    assert scope["kind"] == "recent"
    assert scope["window"] == [50.0, 150.0]
    assert scope["order"] == "oldest"
    assert scope["limit"] == worker.MAX_SUMMARY_MESSAGES
    assert scope["policy"]["required_sources"] == [SOURCE_ARCHIVE]


def test_the_bound_makes_truncated_truthful(tmp_path):
    bounded = evidence_window(tmp_path, attributed=3, message_limit=1)
    assert bounded["truncated"] is True
    assert bounded["counts"]["returned_evidence"] == 1
    assert bounded["evidence"][0]["archive_evidence"]["sequence"] == 2
    assert bounded["query_scope"]["limit"] == 1

    # A separate store: re-syncing would leave the previous rows in Memory, so
    # this half is about an unclipped window, not about stale rows.
    unclipped = evidence_window(tmp_path / "unclipped", attributed=2, message_limit=2)
    assert unclipped["truncated"] is False


def test_long_text_is_clipped_by_the_existing_policy(tmp_path):
    import sqlite3

    store = paths.canonical_store_path(tmp_path)
    messages = archive_message_store(
        paths.canonical_message_store_path(tmp_path), attributed=1
    )
    long_text = "长" * (worker.MAX_SUMMARY_TEXT_CHARS + 50)
    connection = sqlite3.connect(messages)
    connection.execute("UPDATE archive_attributed_records SET text = ?", (long_text,))
    connection.commit()
    connection.close()
    run({"op": "sync", "store_path": str(store), "message_store_path": str(messages),
         "message_source": SOURCE_ARCHIVE, "conversation_limit": 10, "message_limit": 10})
    reply, code = run({"op": "answer_evidence", "store_path": str(store),
                       "message_source": SOURCE_ARCHIVE, "start": 50.0, "end": 150.0})

    assert (reply["ok"], code) == (True, 0), reply
    row = reply["evidence"][0]
    assert row["text_truncated"] is True
    assert len(row["text"]) == worker.MAX_SUMMARY_TEXT_CHARS
    assert reply["counts"]["text_truncated"] == 1


def test_an_empty_but_valid_window_is_an_empty_success_not_an_error(tmp_path):
    reply = evidence_window(tmp_path, attributed=2, start=10_000.0, end=20_000.0)

    assert reply["ok"] is True
    assert reply["evidence"] == []
    assert reply["counts"]["returned_evidence"] == 0
    assert "coverage" in reply and "freshness" in reply


def test_the_operation_writes_nothing_to_either_store(tmp_path):
    store = paths.canonical_store_path(tmp_path)
    messages = archive_message_store(
        paths.canonical_message_store_path(tmp_path), attributed=2
    )
    run({"op": "sync", "store_path": str(store), "message_store_path": str(messages),
         "message_source": SOURCE_ARCHIVE, "conversation_limit": 10, "message_limit": 10})

    def snapshot(root: Path) -> dict:
        return {path.name: path.read_bytes() for path in sorted(root.iterdir())
                if path.is_file() and not path.name.endswith(("-shm", "-wal"))}

    memory_before = snapshot(store.parent)
    messages_before = messages.read_bytes()
    reply, code = run({"op": "answer_evidence", "store_path": str(store),
                       "message_source": SOURCE_ARCHIVE, "start": 50.0, "end": 150.0})

    assert (reply["ok"], code) == (True, 0), reply
    assert snapshot(store.parent) == memory_before
    assert messages.read_bytes() == messages_before


def test_the_window_neither_syncs_nor_scans_candidates(tmp_path, monkeypatch):
    # The evidence window is a read, not a second ingestion path. A stale
    # freshness entry is the observable proof: ingestion would have moved it.
    store = paths.canonical_store_path(tmp_path)
    messages = archive_message_store(
        paths.canonical_message_store_path(tmp_path), attributed=2
    )
    run({"op": "sync", "store_path": str(store), "message_store_path": str(messages),
         "message_source": SOURCE_ARCHIVE, "conversation_limit": 10, "message_limit": 10})
    status, _ = run({"op": "status", "store_path": str(store)})
    before = status["freshness"]

    def poisoned(*args, **kwargs):
        raise AssertionError("the evidence window must not ingest or scan")

    monkeypatch.setattr(worker, "sync_from_source", poisoned)
    monkeypatch.setattr(worker, "build_selected_source", poisoned)
    monkeypatch.setattr(worker, "prepare_store_directory", poisoned)
    reply, code = run({"op": "answer_evidence", "store_path": str(store),
                       "message_source": SOURCE_ARCHIVE, "start": 50.0, "end": 150.0})

    assert (reply["ok"], code) == (True, 0), reply
    assert reply["counts"]["returned_evidence"] == 2
    after, _ = run({"op": "status", "store_path": str(store)})
    assert {k: v for k, v in after["freshness"].items() if k != "generated_at"} == {
        k: v for k, v in before.items() if k != "generated_at"}


def test_freshness_advances_and_keeps_its_parts_distinct(tmp_path, synthetic):
    store = paths.canonical_store_path(tmp_path)
    first, _ = run({"op": "sync", "store_path": str(store)})
    later, _ = run({"op": "sync", "store_path": str(store)})
    a = first["freshness"]["sources"][SOURCE_VISUAL]
    b = later["freshness"]["sources"][SOURCE_VISUAL]
    assert b["last_succeeded_at"] >= a["last_succeeded_at"]
    assert b["runs_total"] == a["runs_total"] + 1
    # Distinct fields, and distinct meanings. A per-conversation read bounds
    # its coverage window at the newest message it saw, so on this path the two
    # coincide; they are never the same *fact*, and `test_memory_freshness`
    # covers the case where observation continued past the last message.
    assert b["observed_through"] >= b["latest_message_at"]
    assert "observed_through" in b and "latest_message_at" in b
    assert b["last_succeeded_at"] >= b["observed_through"]
    assert "is_fresh" not in json.dumps(b)


def test_a_source_failure_is_reported_and_keeps_the_last_good_state(tmp_path, monkeypatch):
    store = paths.canonical_store_path(tmp_path)
    good = FakeSource([visual_message(1, 7, "明天开会")])
    monkeypatch.setattr(worker, "build_selected_source", lambda: good)
    ok, _ = run({"op": "sync", "store_path": str(store)})
    succeeded_at = ok["freshness"]["sources"][SOURCE_VISUAL]["last_succeeded_at"]

    broken = FakeSource([], fail="reader_unavailable")
    monkeypatch.setattr(worker, "build_selected_source", lambda: broken)
    bad, code = run({"op": "sync", "store_path": str(store)})
    assert (bad["ok"], bad["state"], code) == (False, "reader_unavailable", 1)

    status, _ = run({"op": "status", "store_path": str(store)})
    entry = status["freshness"]["sources"][SOURCE_VISUAL]
    assert entry["last_succeeded_at"] == succeeded_at      # not erased
    assert entry["last_attempt_state"] == "failed"
    assert entry["last_attempt_failure_state"] == "reader_unavailable"


# --- source selection -------------------------------------------------------------


def test_a_database_selection_without_a_reader_refuses_and_never_uses_visual(tmp_path, monkeypatch):
    monkeypatch.delenv("WECHAT_COMPANION_READER_BIN", raising=False)
    store = paths.canonical_store_path(tmp_path)
    reply, code = run({"op": "sync", "store_path": str(store), "message_source": "database"})
    assert (reply["ok"], code) == (False, 1)
    assert reply["state"] == "database:reader_not_configured"
    assert not store.exists()


def test_the_activation_is_built_from_the_request_not_inherited(tmp_path, monkeypatch):
    monkeypatch.setenv("WECHAT_COMPANION_MESSAGE_SOURCE", "database")
    monkeypatch.setenv("WECHAT_COMPANION_READER_BIN", "/nowhere/reader")
    source = FakeSource([visual_message(1, 7, "x")])
    monkeypatch.setattr(worker, "build_selected_source", lambda: source)
    # The request names no source, so the worker clears the inherited selection.
    reply, _ = run({"op": "sync", "store_path": str(paths.canonical_store_path(tmp_path))})
    assert reply["ok"] is True
    assert os.environ.get("WECHAT_COMPANION_MESSAGE_SOURCE") is None
    assert os.environ.get("WECHAT_COMPANION_READER_BIN") is None


# --- status -----------------------------------------------------------------------


def test_status_on_a_missing_store_refuses_without_creating_it(tmp_path):
    store = paths.canonical_store_path(tmp_path)
    reply, code = run({"op": "status", "store_path": str(store)})
    assert (reply["state"], code) == ("memory_store_missing", 1)
    assert not store.exists()


def test_status_reports_freshness_without_a_path(tmp_path, synthetic):
    store = paths.canonical_store_path(tmp_path)
    run({"op": "sync", "store_path": str(store)})
    reply, code = run({"op": "status", "store_path": str(store)})
    assert (reply["ok"], code) == (True, 0)
    assert reply["freshness"]["sources"][SOURCE_VISUAL]["stored_messages"] == 2
    assert str(tmp_path) not in json.dumps(reply)


# --- isolation --------------------------------------------------------------------


def test_the_worker_imports_no_network_or_provider_module():
    import ast
    tree = ast.parse(Path(worker.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    for forbidden in ("socket", "http", "urllib", "requests", "ssl", "anthropic", "mcp", "openai"):
        assert forbidden not in imported, forbidden
    assert "subprocess" not in imported          # no shell, no child of its own


def test_no_reply_carries_a_path_or_host_identity(tmp_path, synthetic):
    import getpass, socket
    store = paths.canonical_store_path(tmp_path)
    for request in ({"op": "paths"}, {"op": "sync", "store_path": str(store)},
                    {"op": "status", "store_path": str(store)}):
        blob = json.dumps(run(request)[0], ensure_ascii=False)
        assert str(tmp_path) not in blob
        assert socket.gethostname() not in blob and getpass.getuser() not in blob


# --- a real process, through the real contract -------------------------------------


def isolated_home(tmp_path, *, allowed=True, with_state=True) -> dict:
    home = tmp_path / "home"
    prefs = home / "Library" / "Preferences"
    prefs.mkdir(parents=True, exist_ok=True)
    if with_state:
        (prefs / f"{consent.APP_PREFERENCE_DOMAIN}.plist").write_bytes(
            plistlib.dumps({consent.CONSENT_STATE_KEY: app_state(allowed)}))
    # No `defaults` on PATH, so the worker's plist fallback reads the fixture.
    return {"HOME": str(home), "PATH": str(tmp_path / "nopath"), "LANG": "en_US.UTF-8"}


def invoke(argv: list[str], request: dict, env: dict) -> tuple[dict, int]:
    done = subprocess.run(argv, input=json.dumps(request), capture_output=True,
                          text=True, env=env, timeout=120)
    return json.loads(done.stdout), done.returncode


WORKER_ARGV = ([str(FROZEN)] if FROZEN.exists()
               else [sys.executable, str(ROOT / "memory" / "memory_worker.py")])


def test_a_real_worker_process_answers_the_paths_probe(tmp_path):
    reply, code = invoke(WORKER_ARGV, {"op": "paths"}, isolated_home(tmp_path))
    assert (reply["ok"], code) == (True, 0)
    assert reply["relative_store_path"] == paths.relative_store_path()


def test_a_real_worker_process_refuses_without_the_app_state(tmp_path):
    env = isolated_home(tmp_path, with_state=False)
    store = paths.canonical_store_path(tmp_path)
    reply, code = invoke(WORKER_ARGV, {"op": "sync", "store_path": str(store)}, env)
    assert (reply["state"], code) == ("consent_state_missing", 1)
    assert not store.exists()


@pytest.mark.skipif(not FROZEN.exists(),
                    reason="frozen worker not built; run scripts/build-memory-worker.sh")
def test_the_frozen_worker_is_self_contained_and_syncs_end_to_end(tmp_path):
    """The real bundled artifact, with nothing of this venv in its environment."""
    env = isolated_home(tmp_path)
    store = paths.canonical_store_path(tmp_path)
    messages = paths.canonical_message_store_path(tmp_path)
    # An empty, schema-correct visual store, written the way the app writes it.
    import sqlite3
    messages.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(messages)
    connection.executescript(
        "PRAGMA user_version = 1;"
        "CREATE TABLE conversations (id INTEGER PRIMARY KEY, title TEXT,"
        " first_seen_at REAL, last_seen_at REAL);"
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, conversation_id INTEGER,"
        " sequence INTEGER, sender TEXT, ownership TEXT, visible_time TEXT, text TEXT,"
        " kind TEXT, confidence REAL, first_observed_at REAL);"
        "INSERT INTO conversations VALUES (1, '项目组', 1.0, 2.0);"
        "INSERT INTO messages VALUES (1, 1, 1, '林晓', 'other', '今天', '麻烦明天确认一下报价',"
        " 'text', 0.9, 2.0);")
    connection.commit(); connection.close()

    reply, code = invoke(
        [str(FROZEN)],
        {"op": "sync", "store_path": str(store), "message_store_path": str(messages)}, env)
    assert (reply["ok"], code) == (True, 0), reply
    assert reply["counts"]["messages_inserted"] == 1
    assert store.exists() and stat.S_IMODE(store.stat().st_mode) == 0o600

    again, _ = invoke(
        [str(FROZEN)],
        {"op": "sync", "store_path": str(store), "message_store_path": str(messages)}, env)
    assert again["counts"]["messages_inserted"] == 0
    assert again["counts"]["messages_updated"] == 1

    status, _ = invoke([str(FROZEN)], {"op": "status", "store_path": str(store)}, env)
    assert status["freshness"]["sources"][SOURCE_VISUAL]["stored_messages"] == 1
    assert str(tmp_path) not in json.dumps(status)

    summary, summary_code = invoke(
        [str(FROZEN)],
        {
            "op": "summary_input",
            "store_path": str(store),
            "message_source": SOURCE_VISUAL,
            "start": 1.0,
            "end": 3.0,
            "message_limit": 200,
        },
        env,
    )
    assert (summary["ok"], summary_code) == (True, 0), summary
    assert summary["counts"]["returned_messages"] == 1
    assert summary["conversations"][0]["label"] == "项目组"
    assert summary["messages"][0]["timestamp_kind"] == "first_observed"
    assert str(tmp_path) not in json.dumps(summary)

    reminders, reminder_code = invoke(
        [str(FROZEN)],
        {
            "op": "reminder_candidates",
            "store_path": str(store),
            "message_source": SOURCE_VISUAL,
            "start": 1.0,
            "end": 3.0,
            "message_limit": 200,
            "candidate_limit": 50,
        },
        env,
    )
    assert (reminders["ok"], reminder_code) == (True, 0), reminders
    assert reminders["counts"]["returned_candidates"] == 1
    assert reminders["conversations"][0]["label"] == "项目组"
    assert "explicit_request" in reminders["candidates"][0]["reasons"]
    assert reminders["candidates"][0]["timestamp_kind"] == "first_observed"
    assert str(tmp_path) not in json.dumps(reminders)


@pytest.mark.skipif(not FROZEN.exists(),
                    reason="frozen worker not built; run scripts/build-memory-worker.sh")
def test_the_frozen_worker_syncs_archive_source_end_to_end(tmp_path):
    """The frozen artifact contains the archive source adapter and keeps Shape B out."""
    import sqlite3

    env = isolated_home(tmp_path)
    store = paths.canonical_store_path(tmp_path)
    messages = paths.canonical_message_store_path(tmp_path)
    messages.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(messages)
    connection.executescript(
        """
        PRAGMA user_version = 5;
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY, title TEXT, first_seen_at REAL, last_seen_at REAL
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY, conversation_id INTEGER, sequence INTEGER,
            sender TEXT, ownership TEXT, visible_time TEXT, text TEXT,
            kind TEXT, confidence REAL, first_observed_at REAL
        );
        CREATE TABLE archive_conversations (
            id INTEGER PRIMARY KEY, source_conversation_key TEXT NOT NULL
        );
        CREATE TABLE archive_imports (
            id INTEGER PRIMARY KEY, archive_conversation_id INTEGER NOT NULL,
            transcript_shape TEXT NOT NULL, imported_at REAL NOT NULL
        );
        CREATE TABLE archive_attributed_records (
            import_id INTEGER NOT NULL, sequence INTEGER NOT NULL,
            sender TEXT NOT NULL, sent_at REAL NOT NULL,
            sent_at_text TEXT NOT NULL, text TEXT NOT NULL
        );
        CREATE TABLE archive_unattributed_records (
            import_id INTEGER NOT NULL, sequence INTEGER NOT NULL,
            record_text TEXT NOT NULL
        );
        CREATE TABLE archive_conversation_links (
            archive_conversation_id INTEGER PRIMARY KEY,
            visual_conversation_id INTEGER NOT NULL,
            basis TEXT NOT NULL, asserted_at REAL NOT NULL
        );
        CREATE TABLE archive_attachment_batches (
            id INTEGER PRIMARY KEY,
            import_id INTEGER NOT NULL,
            batch_fingerprint TEXT NOT NULL,
            observed_at REAL NOT NULL,
            attachment_count INTEGER NOT NULL,
            materialized_count INTEGER NOT NULL
        );
        CREATE TABLE archive_attachments (
            id INTEGER PRIMARY KEY,
            batch_id INTEGER NOT NULL,
            source_entry_index INTEGER NOT NULL,
            path_extension TEXT NOT NULL,
            byte_count INTEGER NOT NULL,
            crc32 INTEGER NOT NULL,
            media_kind TEXT,
            storage_state TEXT NOT NULL,
            content_sha256 TEXT,
            stored_relative_path TEXT,
            relation_scope TEXT NOT NULL
        );
        CREATE TABLE archive_conversation_labels (
            archive_conversation_id INTEGER PRIMARY KEY,
            display_name TEXT NOT NULL,
            basis TEXT NOT NULL,
            updated_at REAL NOT NULL
        );
        INSERT INTO archive_conversations VALUES (1, 'anonymous-a');
        INSERT INTO archive_conversations VALUES (2, 'anonymous-b');
        INSERT INTO archive_imports VALUES (1, 1, 'attributed', 1000.0);
        INSERT INTO archive_imports VALUES (2, 2, 'unattributed', 2000.0);
        INSERT INTO archive_attributed_records
            VALUES (1, 0, '林晓', 100.0, '昨天 10:00', '麻烦确认归档报价');
        INSERT INTO archive_unattributed_records
            VALUES (2, 0, '不应进入 Memory');
        INSERT INTO archive_conversation_labels
            VALUES (1, '用户确认群名', 'operator', 4000.0);
        INSERT INTO archive_attachment_batches
            VALUES (1, 1, 'batch', 3000.0, 1, 1);
        INSERT INTO archive_attachments
            VALUES (
                1, 1, 7, 'jpg', 12, 123, 'image', 'materialized',
                'ATTACHMENT-SHA-SENTINEL', 'private/path', 'import_only'
            );
        """
    )
    connection.commit()
    connection.close()

    reply, code = invoke(
        [str(FROZEN)],
        {
            "op": "sync",
            "store_path": str(store),
            "message_store_path": str(messages),
            "message_source": SOURCE_ARCHIVE,
            "conversation_limit": 10,
            "message_limit": 10,
        },
        env,
    )

    assert (reply["ok"], code) == (True, 0), reply
    assert reply["source"] == SOURCE_ARCHIVE
    assert reply["counts"]["conversations_seen"] == 1
    assert reply["counts"]["messages_seen"] == 1
    assert reply["counts"]["messages_inserted"] == 1
    reply_blob = json.dumps(reply, ensure_ascii=False)
    assert "ATTACHMENT-SHA-SENTINEL" not in reply_blob
    assert "private/path" not in reply_blob

    status, status_code = invoke(
        [str(FROZEN)], {"op": "status", "store_path": str(store)}, env
    )
    assert (status["ok"], status_code) == (True, 0)
    assert status["freshness"]["sources"][SOURCE_ARCHIVE]["stored_messages"] == 1
    status_blob = json.dumps(status, ensure_ascii=False)
    assert "不应进入 Memory" not in status_blob
    assert "ATTACHMENT-SHA-SENTINEL" not in status_blob
    assert "private/path" not in status_blob

    summary, summary_code = invoke(
        [str(FROZEN)],
        {
            "op": "summary_input",
            "store_path": str(store),
            "message_source": SOURCE_ARCHIVE,
            "start": 50.0,
            "end": 150.0,
        },
        env,
    )
    assert (summary["ok"], summary_code) == (True, 0), summary
    assert summary["source"] == SOURCE_ARCHIVE
    assert summary["counts"]["returned_messages"] == 1
    assert summary["conversations"][0]["label"] == "用户确认群名"
    assert summary["messages"][0]["timestamp_kind"] == "source_created"
    summary_blob = json.dumps(summary, ensure_ascii=False)
    assert "不应进入 Memory" not in summary_blob
    assert "ATTACHMENT-SHA-SENTINEL" not in summary_blob
    assert "private/path" not in summary_blob

    reminders, reminder_code = invoke(
        [str(FROZEN)],
        {
            "op": "reminder_candidates",
            "store_path": str(store),
            "message_source": SOURCE_ARCHIVE,
            "start": 50.0,
            "end": 150.0,
        },
        env,
    )
    assert (reminders["ok"], reminder_code) == (True, 0), reminders
    assert reminders["source"] == SOURCE_ARCHIVE
    assert reminders["counts"]["returned_candidates"] == 1
    assert reminders["conversations"][0]["label"] == "用户确认群名"
    assert reminders["candidates"][0]["timestamp_kind"] == "source_created"
    reminders_blob = json.dumps(reminders, ensure_ascii=False)
    assert "不应进入 Memory" not in reminders_blob
    assert "ATTACHMENT-SHA-SENTINEL" not in reminders_blob
    assert "private/path" not in reminders_blob


# --- canonical activation (M2.2e) --------------------------------------------


def test_a_sync_with_no_store_path_writes_the_canonical_store(synthetic):
    """The product path: the app names no location, the worker derives it."""
    reply, code = run({"op": "sync"})          # no store_path at all
    assert (reply["ok"], code) == (True, 0)
    canonical = paths.canonical_store_path()   # isolated HOME, per the guard
    assert canonical.exists()
    assert reply["counts"]["messages_inserted"] == 2
    assert str(canonical) not in json.dumps(reply)


def test_the_worker_and_the_runner_agree_on_the_canonical_store(synthetic):
    """The same file the ClaudeRunner will point the memory MCP at."""
    run({"op": "sync"})
    reply, _ = run({"op": "status"})           # also no path
    assert reply["ok"] is True
    assert reply["freshness"]["sources"][SOURCE_VISUAL]["stored_messages"] == 2
    # The runner resolves it through the same module, not a literal of its own.
    runner_source = (ROOT / "shadow/runners/claude.py").read_text(encoding="utf-8")
    assert "memory_paths" in runner_source
    assert "Application Support" not in runner_source
    assert "memory.sqlite" not in runner_source
