"""Tests for the reader boundary and the external-reader adapter.

Nothing here contacts a real reader, a real WeChat database, the WeChat
container, or any credential. The external reader is a stub script written into
a pytest temporary directory that prints canned JSON and reads nothing.

The canned shapes are the ones recorded in the sealed synthetic interface gate
(`docs/v2/DB_READER_INTERFACE_GATE.md`), including the row shape produced by a
WAL-resident, zstd-compressed message. Every value in them is fixture data.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import message_source as ms  # noqa: E402
import rion_reader_adapter as adapter  # noqa: E402
import store_access  # noqa: E402
import wechat_companion_mcp as bridge  # noqa: E402


def call(tool, **kwargs):
    return getattr(tool, "fn", tool)(**kwargs)


# --- The stub reader ---------------------------------------------------------

def make_reader(tmp_path: Path, replies: dict[str, str], *, exit_code: int = 0,
                name: str = "reader") -> Path:
    """Writes a stub that answers one subcommand with a fixed string.

    It parses no arguments beyond finding which subcommand it was asked for,
    opens no file, and reads no input.
    """
    path = tmp_path / name
    path.write_text(
        "#!" + sys.executable + "\n"
        "import sys\n"
        f"REPLIES = {replies!r}\n"
        f"EXIT = {exit_code}\n"
        "chosen = ''\n"
        "for argument in sys.argv[1:]:\n"
        "    if argument in REPLIES:\n"
        "        chosen = REPLIES[argument]\n"
        "        break\n"
        "sys.stdout.write(chosen)\n"
        "sys.exit(EXIT)\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def envelope(command: str, data: dict) -> str:
    return json.dumps({"ok": True, "tool": command, "command": command, "data": data})


DOCTOR_READY = envelope("doctor", {
    "summary": "ready",
    "status": {"status": {"readiness": "ready", "sqlcipher_driver_ready": True}},
})

SESSIONS = envelope("sessions", {"sessions": [
    {"username": "wxid_fixture_a", "type": 1, "unread_count": 1,
     "summary": "fixture summary", "last_timestamp": 300,
     "display_name": "Fixture Contact A", "chat_type": "private"},
    {"username": "fixture@chatroom", "type": 2, "unread_count": 0,
     "summary": "fixture summary", "last_timestamp": 100,
     "display_name": "Fixture Room", "chat_type": "group"},
]})

# Rows 1 and 2 are ordinary. Row 3 is the shape the gate recorded for a
# WAL-resident, zstd-compressed message: no plain content, a base64 compressed
# blob, compression type 4, and text the reader has already decoded.
HISTORY = envelope("history", {
    "query": {"has_more": False, "next_offset": 3},
    "messages": [
        {"local_id": 1, "server_id": 1001, "local_type": 1, "sort_seq": 1,
         "real_sender_id": 1, "create_time": 100, "status": 0,
         "message_content": "fixture one", "compress_content": "",
         "WCDB_CT_message_content": 0, "talker": "wxid_fixture_a",
         "sender": "Fixture Contact A", "sender_wxid": "wxid_fixture_a",
         "from_me": False, "text": "fixture one", "content": "fixture one",
         "kind_name": "text"},
        {"local_id": 2, "server_id": 1002, "local_type": 1, "sort_seq": 2,
         "real_sender_id": 2, "create_time": 200, "status": 0,
         "message_content": "fixture two", "compress_content": "",
         "WCDB_CT_message_content": 0, "talker": "wxid_fixture_a",
         "sender": "Fixture Me", "sender_wxid": "wxid_fixture_me",
         "from_me": True, "text": "fixture two", "content": "fixture two",
         "kind_name": "text"},
        {"local_id": 3, "server_id": 1003, "local_type": 1, "sort_seq": 3,
         "real_sender_id": 1, "create_time": 300, "status": 0,
         "message_content": None,
         "compress_content": {"encoding": "base64", "data": "KLUv_SAcFIXTURE"},
         "WCDB_CT_message_content": 4, "talker": "wxid_fixture_a",
         "sender": "Fixture Contact A", "sender_wxid": "wxid_fixture_a",
         "from_me": False, "text": "fixture three from the write ahead log",
         "content": "fixture three from the write ahead log",
         "kind_name": "text"},
    ],
})

EMPTY_HISTORY = envelope("history", {"query": {"has_more": False}, "messages": []})

READY_REPLIES = {"doctor": DOCTOR_READY, "sessions": SESSIONS, "history": HISTORY}

CONVERSATION_A = adapter.conversation_identifier("wxid_fixture_a")


def build(path: Path, **kwargs) -> adapter.RionReaderAdapter:
    return adapter.RionReaderAdapter(
        adapter.RionReaderConfig(executable=str(path), timeout_seconds=20.0, **kwargs)
    )


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in (bridge.ALLOW_READ_ENV, bridge.DB_PATH_ENV,
                 bridge.MESSAGE_SOURCE_ENV, bridge.READER_BIN_ENV,
                 bridge.READER_CONFIG_ENV, bridge.READER_TIMEOUT_ENV):
        monkeypatch.delenv(name, raising=False)


# --- The protocol ------------------------------------------------------------

def test_both_sources_satisfy_one_protocol(tmp_path):
    assert isinstance(bridge.StoreMessageSource(), ms.MessageSource)
    assert isinstance(build(make_reader(tmp_path, READY_REPLIES)), ms.MessageSource)


def test_the_protocol_depends_on_no_reader_technology():
    """The boundary must not learn a vendor, a transport, or a schema.

    Checked against imports and identifiers rather than raw text, so that
    prose describing what the module deliberately avoids cannot be mistaken
    for a dependency on it.
    """
    import ast

    tree = ast.parse(Path(ms.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    identifiers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
        elif isinstance(node, (ast.Name, ast.arg)):
            identifiers.add(getattr(node, "id", None) or node.arg)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            identifiers.add(node.name)

    # Only the standard library's typing vocabulary. No transport, no codec.
    assert imported <= {"__future__", "dataclasses", "typing"}
    joined = " ".join(identifiers).lower()
    for forbidden in ("rion", "subprocess", "sqlcipher", "wechat", "json",
                      "argv", "zstd", "sqlite"):
        assert forbidden not in joined, forbidden


def test_message_payload_omits_provenance():
    """Provenance is carried in the record and withheld from the wire."""
    message = ms.NormalizedMessage(
        id=1, conversation_id=2, sequence=3, sender=None, ownership="other",
        visible_time=None, text="t", kind="text", confidence=1.0,
        first_observed_at=1.0, source=ms.SOURCE_DATABASE,
    )
    assert message.source == ms.SOURCE_DATABASE
    assert set(message.payload()) == {
        "id", "conversation_id", "sequence", "sender", "ownership",
        "visible_time", "text", "kind", "confidence", "first_observed_at",
    }


# --- Adapter success ---------------------------------------------------------

def test_status_reports_ready(tmp_path):
    report = build(make_reader(tmp_path, READY_REPLIES)).status()

    assert report.ready is True
    assert report.source == ms.SOURCE_DATABASE
    # Counting would mean a scan; unknown is reported as unknown.
    assert report.conversation_count is None
    assert report.message_count is None


def test_conversations_are_normalized(tmp_path):
    conversations = build(make_reader(tmp_path, READY_REPLIES)).list_conversations(10)

    assert [item.title for item in conversations] == [
        "Fixture Contact A", "Fixture Room"]
    assert conversations[0].id == CONVERSATION_A
    assert conversations[0].last_seen_at == 300.0
    # A database read does not know when this machine first saw the chat.
    assert conversations[0].first_seen_at is None
    assert all(item.source == ms.SOURCE_DATABASE for item in conversations)


def test_conversation_identifiers_are_stable_and_json_safe():
    first = adapter.conversation_identifier("wxid_fixture_a")
    assert first == adapter.conversation_identifier("wxid_fixture_a")
    assert first != adapter.conversation_identifier("fixture@chatroom")
    assert 0 < first < 2 ** 53


def test_messages_are_normalized_in_order(tmp_path):
    messages = build(make_reader(tmp_path, READY_REPLIES)).get_messages(
        CONVERSATION_A, 50)

    assert [item.sequence for item in messages] == [1, 2, 3]
    assert [item.ownership for item in messages] == ["other", "own", "other"]
    assert all(item.kind == "text" for item in messages)
    # A decoded row is exact: there is no estimator in this path.
    assert all(item.confidence == 1.0 for item in messages)
    # The reader reports a real timestamp, not a label the user saw.
    assert all(item.visible_time is None for item in messages)
    assert messages[0].first_observed_at == 100.0


def test_a_wal_resident_zstd_message_normalizes_to_its_decoded_text(tmp_path):
    """The third row is the WAL-only, zstd-compressed shape from the gate."""
    messages = build(make_reader(tmp_path, READY_REPLIES)).get_messages(
        CONVERSATION_A, 50)
    third = messages[-1]

    assert third.text == "fixture three from the write ahead log"
    assert third.sequence == 3
    # The compressed blob is a transport detail and never reaches the payload.
    payload = repr(third.payload())
    assert "base64" not in payload
    assert "KLUv" not in payload
    assert "WCDB" not in payload


def test_a_conversation_with_no_messages_returns_an_empty_answer(tmp_path):
    reader = make_reader(tmp_path, {"doctor": DOCTOR_READY, "sessions": SESSIONS,
                                    "history": EMPTY_HISTORY})

    assert build(reader).get_messages(CONVERSATION_A, 50) == []


def test_recent_messages_filter_and_sort(tmp_path):
    messages = build(make_reader(tmp_path, READY_REPLIES)).get_recent_messages(
        200.0, 50)

    assert [item.first_observed_at for item in messages] == [200.0, 200.0,
                                                             300.0, 300.0]


def test_the_executable_path_is_injected_never_searched(tmp_path):
    """Nothing is executed except the path the caller supplied."""
    reader = make_reader(tmp_path, READY_REPLIES, name="custom-reader-name")
    built = build(reader)

    assert built._argv(["doctor"])[0] == str(reader)
    assert built.status().ready is True


def test_a_configuration_path_is_passed_through(tmp_path):
    built = build(make_reader(tmp_path, READY_REPLIES),
                  config_path="/injected/config.json")

    assert built._argv(["doctor"])[:3] == [
        built._config.executable, "--config", "/injected/config.json"]


# --- Failing closed ----------------------------------------------------------

def test_a_missing_executable_is_unavailable_not_a_crash(tmp_path):
    built = build(tmp_path / "does-not-exist")

    with pytest.raises(ms.MessageSourceError) as raised:
        built.list_conversations(10)
    assert raised.value.state == "reader_unavailable"

    # status reports rather than raising, so a caller can see why.
    report = built.status()
    assert report.ready is False
    assert report.state == "reader_unavailable"


def test_malformed_json_ingests_nothing(tmp_path):
    reader = make_reader(tmp_path, {"sessions": "this is not json {"})

    with pytest.raises(ms.MessageSourceError) as raised:
        build(reader).list_conversations(10)
    assert raised.value.state == "reader_malformed_response"


def test_an_empty_reply_ingests_nothing(tmp_path):
    with pytest.raises(ms.MessageSourceError) as raised:
        build(make_reader(tmp_path, {})).list_conversations(10)
    assert raised.value.state == "reader_malformed_response"


def test_a_reply_without_the_expected_envelope_is_refused(tmp_path):
    reader = make_reader(tmp_path, {"sessions": json.dumps({"sessions": []})})

    with pytest.raises(ms.MessageSourceError) as raised:
        build(reader).list_conversations(10)
    assert raised.value.state == "reader_malformed_response"


def test_a_partially_valid_reply_is_refused_whole(tmp_path):
    """Half a row is not half an answer."""
    reader = make_reader(tmp_path, {"sessions": envelope(
        "sessions", {"sessions": [{"display_name": "no identifier"}]})})

    with pytest.raises(ms.MessageSourceError) as raised:
        build(reader).list_conversations(10)
    assert raised.value.state == "reader_malformed_response"


def test_a_message_without_a_creation_time_is_refused(tmp_path):
    reader = make_reader(tmp_path, {
        "sessions": SESSIONS,
        "history": envelope("history", {"messages": [
            {"local_id": 1, "sort_seq": 1, "text": "t"}]}),
    })

    with pytest.raises(ms.MessageSourceError) as raised:
        build(reader).get_messages(CONVERSATION_A, 10)
    assert raised.value.state == "reader_malformed_response"


def test_a_reader_reported_failure_keeps_only_a_safe_code(tmp_path):
    reader = make_reader(tmp_path, {"sessions": json.dumps({
        "ok": False, "command": "sessions",
        "error": {"code": "sqlcipher_driver_required",
                  "message": "a sentence that may name a path"}})}, exit_code=1)

    with pytest.raises(ms.MessageSourceError) as raised:
        build(reader).list_conversations(10)
    assert raised.value.state == "reader_sqlcipher_driver_required"
    # The reader's own prose never reaches the caller.
    assert "path" not in raised.value.detail


def test_an_unsafe_error_code_is_discarded(tmp_path):
    """A code that is really a path must not become part of a state token."""
    reader = make_reader(tmp_path, {"sessions": json.dumps({
        "ok": False, "error": {"code": "/injected/path/to/a/file.db"}})},
        exit_code=1)

    with pytest.raises(ms.MessageSourceError) as raised:
        build(reader).list_conversations(10)
    assert raised.value.state == "reader_error"
    assert "/" not in raised.value.state


def test_success_with_a_failing_exit_code_is_refused(tmp_path):
    reader = make_reader(tmp_path, READY_REPLIES, exit_code=3)

    with pytest.raises(ms.MessageSourceError) as raised:
        build(reader).list_conversations(10)
    assert raised.value.state == "reader_error"


def test_paging_backwards_is_refused_rather_than_approximated(tmp_path):
    with pytest.raises(ms.MessageSourceError) as raised:
        build(make_reader(tmp_path, READY_REPLIES)).get_messages(
            CONVERSATION_A, 10, before_sequence=3)
    assert raised.value.state == "unsupported_paging"


def test_an_unknown_conversation_is_not_fabricated(tmp_path):
    with pytest.raises(ms.MessageSourceError) as raised:
        build(make_reader(tmp_path, READY_REPLIES)).get_messages(999_999, 10)
    assert raised.value.state == "conversation_unknown"


def test_the_adapter_never_reads_a_wechat_location():
    """Nothing here knows where WeChat keeps anything."""
    source = Path(adapter.__file__).read_text(encoding="utf-8")
    for forbidden in ("Containers", "xwechat_files", "db_storage",
                      "com.tencent", "/Users/", "codesign", "sudo",
                      "task_for_pid", "PRAGMA key", "enc_key", "salt"):
        assert forbidden not in source, forbidden
    assert "shell=True" not in source


# --- Selection through the bridge --------------------------------------------

def test_the_default_source_is_the_visual_store(monkeypatch):
    assert bridge.selected_source_name() == ms.SOURCE_VISUAL
    assert isinstance(bridge.active_source(), bridge.StoreMessageSource)


def test_selecting_the_database_source_requires_the_agent_opt_in(monkeypatch, tmp_path):
    monkeypatch.setenv(bridge.MESSAGE_SOURCE_ENV, ms.SOURCE_DATABASE)
    monkeypatch.setenv(bridge.READER_BIN_ENV,
                       str(make_reader(tmp_path, READY_REPLIES)))

    with pytest.raises(bridge.BridgeUnavailable) as raised:
        bridge.active_source()
    assert raised.value.state == "agent_read_disabled"


def test_the_database_source_without_an_executable_is_unavailable(monkeypatch):
    monkeypatch.setenv(bridge.ALLOW_READ_ENV, "1")
    monkeypatch.setenv(bridge.MESSAGE_SOURCE_ENV, ms.SOURCE_DATABASE)

    result = call(bridge.list_conversations)

    assert result["ok"] is False
    assert result["state"] == "reader_not_configured"
    assert result["source"] == ms.SOURCE_DATABASE
    assert "conversations" not in result


def test_an_unrecognised_source_fails_closed(monkeypatch):
    monkeypatch.setenv(bridge.MESSAGE_SOURCE_ENV, "something-else")

    result = call(bridge.list_conversations)

    assert result["ok"] is False
    assert result["state"] == "source_unknown"


def make_store(tmp_path: Path) -> Path:
    """A populated synthetic store, so a fallback would be visible if it happened."""
    import sqlite3

    path = tmp_path / "messages.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "title TEXT NOT NULL UNIQUE, first_seen_at REAL NOT NULL, "
        "last_seen_at REAL NOT NULL);"
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "conversation_id INTEGER NOT NULL, sequence INTEGER NOT NULL, "
        "sender TEXT, ownership TEXT NOT NULL, visible_time TEXT, text TEXT, "
        "kind TEXT NOT NULL, confidence REAL NOT NULL, "
        "first_observed_at REAL NOT NULL);"
        "INSERT INTO conversations (title, first_seen_at, last_seen_at) "
        "VALUES ('store fixture chat', 1.0, 2.0);"
        "INSERT INTO messages (conversation_id, sequence, sender, ownership, "
        "visible_time, text, kind, confidence, first_observed_at) "
        "VALUES (1, 1, 'store fixture sender', 'other', '09:00', "
        "'store fixture message', 'text', 0.9, 1.0);"
    )
    connection.execute("PRAGMA user_version = 1;")
    connection.commit()
    connection.close()
    return path


@pytest.mark.parametrize("broken", ["absent", "malformed", "failing"])
def test_a_failing_database_source_is_never_served_by_the_store(
    tmp_path, monkeypatch, broken
):
    """The load-bearing test: no silent fallback, even with a working store.

    Both sources are fully configured and the store has content. If a failure
    on the selected source were ever answered by the other one, these reads
    would succeed and quietly carry visual rows under a database selection.
    """
    if broken == "absent":
        reader = tmp_path / "not-installed"
    elif broken == "malformed":
        reader = make_reader(tmp_path, {"doctor": "{", "sessions": "{",
                                        "history": "{"})
    else:
        reader = make_reader(tmp_path, {
            "doctor": json.dumps({"ok": False, "error": {"code": "not_ready"}}),
            "sessions": json.dumps({"ok": False, "error": {"code": "not_ready"}}),
            "history": json.dumps({"ok": False, "error": {"code": "not_ready"}}),
        }, exit_code=1)

    monkeypatch.setenv(bridge.ALLOW_READ_ENV, "1")
    monkeypatch.setenv(bridge.DB_PATH_ENV, str(make_store(tmp_path)))
    monkeypatch.setenv(bridge.MESSAGE_SOURCE_ENV, ms.SOURCE_DATABASE)
    monkeypatch.setenv(bridge.READER_BIN_ENV, str(reader))

    conversations = call(bridge.list_conversations)
    recent = call(bridge.get_recent_messages, since_observed_at=0)
    reported = call(bridge.status)

    for result in (conversations, recent):
        assert result["ok"] is False
        assert result["source"] == ms.SOURCE_DATABASE
        assert "conversations" not in result and "messages" not in result
        assert "store fixture" not in repr(result)
    # status reports the failure against the selected source, not the store's.
    assert reported["ok"] is False
    assert reported["source"] == ms.SOURCE_DATABASE
    assert "conversation_count" not in reported
    assert "store fixture" not in repr(reported)


def test_status_names_the_active_source_without_exposing_a_path(tmp_path, monkeypatch):
    reader = make_reader(tmp_path, READY_REPLIES)
    monkeypatch.setenv(bridge.ALLOW_READ_ENV, "1")
    monkeypatch.setenv(bridge.MESSAGE_SOURCE_ENV, ms.SOURCE_DATABASE)
    monkeypatch.setenv(bridge.READER_BIN_ENV, str(reader))

    reported = call(bridge.status)

    assert reported["ok"] is True
    assert reported["source"] == ms.SOURCE_DATABASE
    assert reported["reader_configured"] is True
    # Availability is a boolean and a name. No location is ever disclosed.
    serialized = repr(reported)
    assert str(reader) not in serialized
    assert str(tmp_path) not in serialized


def test_status_on_the_default_path_reports_no_reader_configured(monkeypatch):
    monkeypatch.setenv(bridge.ALLOW_READ_ENV, "1")

    reported = call(bridge.status)

    assert reported["source"] == ms.SOURCE_VISUAL
    assert reported["reader_configured"] is False


def test_a_missing_reader_does_not_disturb_the_visual_path(tmp_path, monkeypatch):
    """The database source being broken must not degrade the store path."""
    monkeypatch.setenv(bridge.ALLOW_READ_ENV, "1")
    monkeypatch.setenv(bridge.READER_BIN_ENV, str(tmp_path / "absent"))
    store = tmp_path / "messages.sqlite"
    import sqlite3

    connection = sqlite3.connect(store)
    connection.executescript(
        "CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "title TEXT NOT NULL UNIQUE, first_seen_at REAL NOT NULL, "
        "last_seen_at REAL NOT NULL);"
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "conversation_id INTEGER NOT NULL, sequence INTEGER NOT NULL, "
        "sender TEXT, ownership TEXT NOT NULL, visible_time TEXT, text TEXT, "
        "kind TEXT NOT NULL, confidence REAL NOT NULL, "
        "first_observed_at REAL NOT NULL);"
    )
    connection.execute("PRAGMA user_version = 1;")
    connection.commit()
    connection.close()
    monkeypatch.setenv(bridge.DB_PATH_ENV, str(store))

    result = call(bridge.list_conversations)

    assert result["ok"] is True
    assert result["source"] == ms.SOURCE_VISUAL


# --- The MCP contract is unchanged -------------------------------------------

def test_the_tool_surface_is_exactly_the_four_tools():
    import asyncio

    names = {tool.name for tool in asyncio.run(bridge.mcp.list_tools())}
    assert names == {"status", "list_conversations", "get_messages",
                     "get_recent_messages"}


def test_the_database_source_serves_the_unchanged_tool_surface(tmp_path, monkeypatch):
    monkeypatch.setenv(bridge.ALLOW_READ_ENV, "1")
    monkeypatch.setenv(bridge.MESSAGE_SOURCE_ENV, ms.SOURCE_DATABASE)
    monkeypatch.setenv(bridge.READER_BIN_ENV,
                       str(make_reader(tmp_path, READY_REPLIES)))

    status = call(bridge.status)
    conversations = call(bridge.list_conversations)
    messages = call(bridge.get_messages, conversation_id=CONVERSATION_A)

    assert status["ok"] is True and status["source"] == ms.SOURCE_DATABASE
    assert status["read_only"] is True
    assert conversations["ok"] is True
    assert len(conversations["conversations"]) == 2
    assert set(conversations["conversations"][0]) == {
        "id", "title", "first_seen_at", "last_seen_at"}
    # The message shape is identical to the one the store has always returned.
    assert set(messages["messages"][0]) == {
        "id", "conversation_id", "sequence", "sender", "ownership",
        "visible_time", "text", "kind", "confidence", "first_observed_at"}
    assert messages["source"] == ms.SOURCE_DATABASE


def test_no_reader_output_reaches_stdout(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(bridge.ALLOW_READ_ENV, "1")
    monkeypatch.setenv(bridge.MESSAGE_SOURCE_ENV, ms.SOURCE_DATABASE)
    monkeypatch.setenv(bridge.READER_BIN_ENV,
                       str(make_reader(tmp_path, READY_REPLIES)))

    call(bridge.get_messages, conversation_id=CONVERSATION_A)

    captured = capsys.readouterr()
    assert captured.out == ""
    for forbidden in ("fixture one", "Fixture Contact A", "write ahead log"):
        assert forbidden not in captured.err


def test_no_real_wechat_location_is_touched_by_this_suite(tmp_path, monkeypatch):
    container = Path.home() / "Library/Containers/com.tencent.xinWeChat"
    before = container.exists()
    monkeypatch.setenv(bridge.ALLOW_READ_ENV, "1")
    monkeypatch.setenv(bridge.MESSAGE_SOURCE_ENV, ms.SOURCE_DATABASE)
    monkeypatch.setenv(bridge.READER_BIN_ENV,
                       str(make_reader(tmp_path, READY_REPLIES)))

    call(bridge.list_conversations)

    assert container.exists() == before
    assert str(tmp_path).startswith(os.environ.get("TMPDIR", "/") .rstrip("/")
                                    ) or "pytest" in str(tmp_path)


# --- Schema v2: version-specific required tables -----------------------------
#
# Widening SUPPORTED_SCHEMA_VERSIONS alone would accept a database stamped 2
# whose archive tables do not exist -- the stamp asserting a shape the file does
# not have, which is the failure the version gate exists to prevent.

_V1_TABLES = """
CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL UNIQUE,
  first_seen_at REAL NOT NULL, last_seen_at REAL NOT NULL);
CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER NOT NULL
  REFERENCES conversations(id) ON DELETE CASCADE, sequence INTEGER NOT NULL, sender TEXT,
  ownership TEXT NOT NULL, visible_time TEXT, text TEXT, kind TEXT NOT NULL,
  confidence REAL NOT NULL, first_observed_at REAL NOT NULL);
"""

_V2_ARCHIVE_STATEMENTS = {
    "archive_conversations": """
        CREATE TABLE archive_conversations (id INTEGER PRIMARY KEY AUTOINCREMENT,
          source_conversation_key TEXT NOT NULL UNIQUE);
    """,
    "archive_imports": """
        CREATE TABLE archive_imports (id INTEGER PRIMARY KEY AUTOINCREMENT,
          archive_conversation_id INTEGER NOT NULL, import_fingerprint TEXT NOT NULL UNIQUE,
          fingerprint_format_version INTEGER NOT NULL, source_type TEXT NOT NULL,
          transcript_shape TEXT NOT NULL, imported_at REAL NOT NULL,
          archive_parser_version INTEGER NOT NULL, time_zone_identifier TEXT,
          UNIQUE (id, transcript_shape));
    """,
    "archive_attributed_records": """
        CREATE TABLE archive_attributed_records (id INTEGER PRIMARY KEY AUTOINCREMENT,
          import_id INTEGER NOT NULL, transcript_shape TEXT NOT NULL DEFAULT 'attributed',
          sequence INTEGER NOT NULL, sender TEXT NOT NULL, sent_at REAL NOT NULL,
          sent_at_text TEXT NOT NULL, text TEXT NOT NULL, UNIQUE (import_id, sequence));
    """,
    "archive_unattributed_records": """
        CREATE TABLE archive_unattributed_records (id INTEGER PRIMARY KEY AUTOINCREMENT,
          import_id INTEGER NOT NULL, transcript_shape TEXT NOT NULL DEFAULT 'unattributed',
          sequence INTEGER NOT NULL, record_text TEXT NOT NULL, UNIQUE (import_id, sequence));
    """,
}

_V2_ARCHIVE_TABLES = "".join(_V2_ARCHIVE_STATEMENTS.values())


def _schema_db(tmp_path, *, version, extra=""):
    path = tmp_path / f"schema-{version}.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript(_V1_TABLES + extra)
    connection.execute(f"PRAGMA user_version = {version};")
    connection.commit()
    connection.close()
    return path


def _verify(path):
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return store_access.verify_schema(connection)
    finally:
        connection.close()


def test_complete_schema_v1_is_accepted(tmp_path):
    assert _verify(_schema_db(tmp_path, version=1)) == 1


def test_complete_schema_v2_is_accepted(tmp_path):
    assert _verify(_schema_db(tmp_path, version=2, extra=_V2_ARCHIVE_TABLES)) == 2


def test_schema_v2_missing_an_archive_table_is_incomplete(tmp_path):
    for dropped in _V2_ARCHIVE_STATEMENTS:
        partial = "".join(
            statement
            for name, statement in _V2_ARCHIVE_STATEMENTS.items()
            if name != dropped
        )
        path = tmp_path / f"missing-{dropped}.sqlite"
        connection = sqlite3.connect(path)
        connection.executescript(_V1_TABLES + partial)
        connection.execute("PRAGMA user_version = 2;")
        connection.commit()
        connection.close()

        with pytest.raises(store_access.BridgeUnavailable) as error:
            _verify(path)
        assert error.value.state == "schema_incomplete", dropped


def test_schema_v1_tolerates_an_unrelated_additive_table(tmp_path):
    extra = "CREATE TABLE something_additive (id INTEGER PRIMARY KEY);"
    assert _verify(_schema_db(tmp_path, version=1, extra=extra)) == 1


def test_a_future_schema_version_is_unsupported(tmp_path):
    with pytest.raises(store_access.BridgeUnavailable) as error:
        _verify(_schema_db(tmp_path, version=3, extra=_V2_ARCHIVE_TABLES))
    assert error.value.state == "schema_unsupported"


def test_archive_evidence_is_invisible_to_the_visual_read_path(tmp_path, monkeypatch):
    """Schema v2 archive rows must not reach anything Memory or MCP reads.

    Memory sync consumes ``StoreMessageSource``, so proving the source cannot
    see archive rows proves an import cannot masquerade as a visual message,
    advance ``observed_through``, or show up in a recent-message sweep.
    """
    path = tmp_path / "v2.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript(_V1_TABLES + _V2_ARCHIVE_TABLES)
    connection.executescript(
        """
        INSERT INTO conversations(title, first_seen_at, last_seen_at) VALUES('chat', 1, 2);
        INSERT INTO messages(conversation_id, sequence, sender, ownership, visible_time,
                             text, kind, confidence, first_observed_at)
          VALUES(1, 0, 'someone', 'other', '昨天', 'VISUAL-TEXT', 'text', 0.9, 1000);

        INSERT INTO archive_conversations(source_conversation_key) VALUES('ARCHIVE-KEY');
        INSERT INTO archive_imports(archive_conversation_id, import_fingerprint,
            fingerprint_format_version, source_type, transcript_shape, imported_at,
            archive_parser_version, time_zone_identifier)
          VALUES(1, 'fp', 1, 'wechat_native_archive', 'attributed', 9999, 1, 'Asia/Shanghai');
        INSERT INTO archive_attributed_records(import_id, sequence, sender, sent_at,
            sent_at_text, text)
          VALUES(1, 0, 'ARCHIVE-SENDER', 5000, '2026年9月7日 20:35', 'ARCHIVE-TEXT');
        """
    )
    connection.execute("PRAGMA user_version = 2;")
    connection.commit()
    connection.close()

    monkeypatch.setenv(store_access.ALLOW_READ_ENV, "1")
    monkeypatch.setenv(store_access.DB_PATH_ENV, str(path))
    source = store_access.StoreMessageSource()

    status = source.status()
    assert status.ready is True
    # One visual conversation and one visual message -- the archive rows are
    # simply not part of this count.
    assert status.detail is None or "ARCHIVE" not in str(status.detail)

    conversations = source.list_conversations(limit=50)
    rendered = json.dumps(
        [c.__dict__ for c in conversations], default=str, ensure_ascii=False
    )
    assert "ARCHIVE-KEY" not in rendered

    messages = source.get_messages(conversations[0].id, limit=100)
    rendered = json.dumps([m.__dict__ for m in messages], default=str, ensure_ascii=False)
    assert "VISUAL-TEXT" in rendered
    for leaked in ("ARCHIVE-TEXT", "ARCHIVE-SENDER", "ARCHIVE-KEY"):
        assert leaked not in rendered, f"{leaked} reached the visual read model"

    # `imported_at` of 9999 is newer than the visual message's observation time;
    # a recent sweep must still see only the visual row.
    recent = source.get_recent_messages(since_observed_at=0.0, limit=100)
    rendered = json.dumps([m.__dict__ for m in recent], default=str, ensure_ascii=False)
    assert "VISUAL-TEXT" in rendered
    assert "ARCHIVE-TEXT" not in rendered
