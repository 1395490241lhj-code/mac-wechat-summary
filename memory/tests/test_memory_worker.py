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
from conftest import app_state, conversation, visual_message
from message_source import SOURCE_VISUAL, MessageSourceError, SourceStatus

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
        return [conversation(7, "项目组")]

    def get_messages(self, conversation_id, limit, before_sequence=None):
        return self._messages[:limit]

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


def test_there_is_no_operation_that_writes_or_deletes():
    assert worker.OPERATIONS == {"sync", "status", "paths"}


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
        "INSERT INTO messages VALUES (1, 1, 1, '林晓', 'other', '今天', '明天开会',"
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
