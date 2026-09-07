"""Tests for the read-only WeChat Companion MCP bridge.

Every test builds its own synthetic SQLite database in a pytest temporary
directory. No test reads, creates, or touches the real Application Support
store, and no fixture contains real chat content.
"""

from __future__ import annotations

import importlib
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import store_access  # noqa: E402
import wechat_companion_mcp as bridge  # noqa: E402

SCHEMA_VERSION = 1


def call(tool, **kwargs):
    """Invoke a tool's underlying function across MCP SDK versions.

    SDK 1.x wraps the function in a FunctionTool exposing `.fn`; 2.x returns
    the plain function.
    """
    return getattr(tool, "fn", tool)(**kwargs)

# Mirrors the Swift MessageStore schema, including the user_version stamp.
SCHEMA = """
CREATE TABLE conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL UNIQUE,
    first_seen_at REAL NOT NULL,
    last_seen_at REAL NOT NULL
);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    sender TEXT,
    ownership TEXT NOT NULL,
    visible_time TEXT,
    text TEXT,
    kind TEXT NOT NULL,
    confidence REAL NOT NULL,
    first_observed_at REAL NOT NULL
);
CREATE INDEX messages_by_position ON messages(conversation_id, sequence);
"""


def make_database(path: Path, *, schema_version: int = SCHEMA_VERSION,
                  with_tables: bool = True) -> Path:
    connection = sqlite3.connect(path)
    if with_tables:
        connection.executescript(SCHEMA)
    connection.execute(f"PRAGMA user_version = {schema_version};")
    connection.commit()
    connection.close()
    return path


def add_conversation(path: Path, title: str, first: float, last: float) -> int:
    connection = sqlite3.connect(path)
    cursor = connection.execute(
        "INSERT INTO conversations (title, first_seen_at, last_seen_at) VALUES (?, ?, ?);",
        (title, first, last),
    )
    connection.commit()
    conversation_id = cursor.lastrowid
    connection.close()
    return conversation_id


def add_message(path: Path, conversation_id: int, sequence: int, text: str,
                observed_at: float, *, sender: str | None = None,
                visible_time: str | None = None, ownership: str = "other",
                kind: str = "text", confidence: float = 0.9) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        """
        INSERT INTO messages (conversation_id, sequence, sender, ownership,
                              visible_time, text, kind, confidence, first_observed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (conversation_id, sequence, sender, ownership, visible_time, text,
         kind, confidence, observed_at),
    )
    connection.commit()
    connection.close()


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    """Never inherit a real configuration from the developer's shell."""
    monkeypatch.delenv(bridge.ALLOW_READ_ENV, raising=False)
    monkeypatch.delenv(bridge.DB_PATH_ENV, raising=False)


@pytest.fixture
def enabled(tmp_path, monkeypatch):
    """A populated synthetic database with both opt-ins granted."""
    path = make_database(tmp_path / "messages.sqlite")
    monkeypatch.setenv(bridge.ALLOW_READ_ENV, "1")
    monkeypatch.setenv(bridge.DB_PATH_ENV, str(path))
    return path


# --- Read gate ---------------------------------------------------------------

def test_agent_read_is_denied_by_default(tmp_path, monkeypatch):
    path = make_database(tmp_path / "messages.sqlite")
    # The path alone is not enough: the allow flag is absent.
    monkeypatch.setenv(bridge.DB_PATH_ENV, str(path))

    result = call(bridge.list_conversations)

    assert result["ok"] is False
    assert result["state"] == "agent_read_disabled"
    assert "conversations" not in result


def test_allow_flag_without_a_database_path_is_not_configured(monkeypatch):
    monkeypatch.setenv(bridge.ALLOW_READ_ENV, "1")

    result = call(bridge.list_conversations)

    assert result["state"] == "database_not_configured"


def test_there_is_no_default_database_path(monkeypatch):
    """The production store must be unreachable without an explicit path."""
    monkeypatch.setenv(bridge.ALLOW_READ_ENV, "1")
    reloaded = importlib.reload(bridge)
    with pytest.raises(reloaded.BridgeUnavailable) as raised:
        reloaded.resolve_access()
    assert raised.value.state == "database_not_configured"


def test_a_truthy_but_wrong_allow_value_still_denies(tmp_path, monkeypatch):
    path = make_database(tmp_path / "messages.sqlite")
    monkeypatch.setenv(bridge.ALLOW_READ_ENV, "true")
    monkeypatch.setenv(bridge.DB_PATH_ENV, str(path))

    assert call(bridge.status)["state"] == "agent_read_disabled"


def test_missing_database_file_is_reported_without_the_path(tmp_path, monkeypatch):
    monkeypatch.setenv(bridge.ALLOW_READ_ENV, "1")
    missing = tmp_path / "absent.sqlite"
    monkeypatch.setenv(bridge.DB_PATH_ENV, str(missing))

    result = call(bridge.status)

    assert result["ok"] is False
    assert result["state"] == "database_missing"
    # The filesystem path is not echoed back.
    assert str(missing) not in result["detail"]
    # A read-only bridge must not create the file it failed to find.
    assert not missing.exists()


# --- Schema compatibility ----------------------------------------------------

def test_compatible_schema_is_accepted(enabled):
    result = call(bridge.status)

    assert result["ok"] is True
    assert result["state"] == "ready"
    assert result["schema_version"] == SCHEMA_VERSION


def test_incompatible_schema_fails_closed(tmp_path, monkeypatch):
    path = make_database(tmp_path / "future.sqlite", schema_version=99)
    monkeypatch.setenv(bridge.ALLOW_READ_ENV, "1")
    monkeypatch.setenv(bridge.DB_PATH_ENV, str(path))

    status = call(bridge.status)
    assert status["ok"] is False
    assert status["state"] == "schema_unsupported"

    # Every content tool refuses too, not just status.
    for result in (
        call(bridge.list_conversations),
        call(bridge.get_messages, conversation_id=1),
        call(bridge.get_recent_messages, since_observed_at=0),
    ):
        assert result["ok"] is False
        assert result["state"] == "schema_unsupported"


def test_unstamped_schema_fails_closed(tmp_path, monkeypatch):
    """A version-0 database predates the contract and is not guessed at."""
    path = make_database(tmp_path / "unstamped.sqlite", schema_version=0)
    monkeypatch.setenv(bridge.ALLOW_READ_ENV, "1")
    monkeypatch.setenv(bridge.DB_PATH_ENV, str(path))

    assert call(bridge.status)["state"] == "schema_unsupported"


def test_missing_tables_fail_closed(tmp_path, monkeypatch):
    path = make_database(tmp_path / "empty.sqlite", with_tables=False)
    monkeypatch.setenv(bridge.ALLOW_READ_ENV, "1")
    monkeypatch.setenv(bridge.DB_PATH_ENV, str(path))

    assert call(bridge.status)["state"] == "schema_incomplete"


# --- status ------------------------------------------------------------------

def test_status_reports_counts_but_no_chat_content(enabled):
    conversation = add_conversation(enabled, "Chat A", 100.0, 200.0)
    add_message(enabled, conversation, 1, "hello there", 150.0)

    result = call(bridge.status)

    assert result["conversation_count"] == 1
    assert result["message_count"] == 1
    assert result["read_only"] is True
    serialized = repr(result)
    assert "Chat A" not in serialized
    assert "hello there" not in serialized


# --- list_conversations ------------------------------------------------------

def test_list_conversations_orders_by_last_seen(enabled):
    add_conversation(enabled, "Older", 10.0, 20.0)
    add_conversation(enabled, "Newer", 30.0, 40.0)

    result = call(bridge.list_conversations)

    assert [c["title"] for c in result["conversations"]] == ["Newer", "Older"]
    assert result["conversations"][0]["last_seen_at"] == 40.0


def test_list_conversations_clamps_to_the_hard_cap(enabled):
    for index in range(5):
        add_conversation(enabled, f"Chat {index}", float(index), float(index))

    assert call(bridge.list_conversations, limit=10_000)["limit"] == bridge.MAX_CONVERSATIONS
    assert call(bridge.list_conversations, limit=0)["limit"] == 1
    assert call(bridge.list_conversations, limit=-5)["limit"] == 1
    assert len(call(bridge.list_conversations, limit=2)["conversations"]) == 2


# --- get_messages ------------------------------------------------------------

def test_messages_are_ordered_by_sequence_not_insertion(enabled):
    conversation = add_conversation(enabled, "Chat A", 0.0, 0.0)
    # Inserted newest-first, and backfilled history uses negative sequences.
    add_message(enabled, conversation, 3, "third", 300.0)
    add_message(enabled, conversation, -1, "first", 900.0)
    add_message(enabled, conversation, 2, "second", 200.0)

    result = call(bridge.get_messages, conversation_id=conversation)

    assert [m["text"] for m in result["messages"]] == ["first", "second", "third"]
    assert [m["sequence"] for m in result["messages"]] == [-1, 2, 3]


def test_get_messages_returns_the_newest_window_when_limited(enabled):
    conversation = add_conversation(enabled, "Chat A", 0.0, 0.0)
    for sequence in range(1, 6):
        add_message(enabled, conversation, sequence, f"m{sequence}", float(sequence))

    result = call(bridge.get_messages, conversation_id=conversation, limit=2)

    assert [m["text"] for m in result["messages"]] == ["m4", "m5"]


def test_before_sequence_pages_backwards(enabled):
    conversation = add_conversation(enabled, "Chat A", 0.0, 0.0)
    for sequence in range(1, 6):
        add_message(enabled, conversation, sequence, f"m{sequence}", float(sequence))

    first = call(bridge.get_messages, conversation_id=conversation, limit=2)
    assert [m["text"] for m in first["messages"]] == ["m4", "m5"]

    second = call(bridge.get_messages, 
        conversation_id=conversation, limit=2,
        before_sequence=first["next_before_sequence"],
    )
    assert [m["text"] for m in second["messages"]] == ["m2", "m3"]

    third = call(bridge.get_messages, 
        conversation_id=conversation, limit=2,
        before_sequence=second["next_before_sequence"],
    )
    assert [m["text"] for m in third["messages"]] == ["m1"]
    assert third["next_before_sequence"] == 1

    exhausted = call(bridge.get_messages, 
        conversation_id=conversation, limit=2, before_sequence=1
    )
    assert exhausted["messages"] == []
    assert exhausted["next_before_sequence"] is None


def test_get_messages_clamps_to_the_hard_cap(enabled):
    conversation = add_conversation(enabled, "Chat A", 0.0, 0.0)
    add_message(enabled, conversation, 1, "m1", 1.0)

    assert call(bridge.get_messages, 
        conversation_id=conversation, limit=10_000
    )["limit"] == bridge.MAX_MESSAGES


def test_get_messages_scopes_to_one_conversation(enabled):
    a = add_conversation(enabled, "Chat A", 0.0, 0.0)
    b = add_conversation(enabled, "Chat B", 0.0, 0.0)
    add_message(enabled, a, 1, "a1", 1.0)
    add_message(enabled, b, 1, "b1", 1.0)

    result = call(bridge.get_messages, conversation_id=a)
    assert [m["text"] for m in result["messages"]] == ["a1"]


# --- get_recent_messages -----------------------------------------------------

def test_recent_messages_filter_on_first_observed_at_not_visible_time(enabled):
    conversation = add_conversation(enabled, "Chat A", 0.0, 0.0)
    # Observed long ago, but its display string claims today.
    add_message(enabled, conversation, 1, "stale", 100.0, visible_time="今天 09:00")
    # Observed recently, but its display string claims years ago.
    add_message(enabled, conversation, 2, "fresh", 900.0, visible_time="2019-01-01")

    result = call(bridge.get_recent_messages, since_observed_at=500.0)

    assert [m["text"] for m in result["messages"]] == ["fresh"]
    # The display string is still returned as data, just never used to filter.
    assert result["messages"][0]["visible_time"] == "2019-01-01"


def test_recent_messages_clamp_and_order(enabled):
    conversation = add_conversation(enabled, "Chat A", 0.0, 0.0)
    for sequence in range(1, 4):
        add_message(enabled, conversation, sequence, f"m{sequence}", float(sequence * 10))

    result = call(bridge.get_recent_messages, since_observed_at=0, limit=10_000)

    assert result["limit"] == bridge.MAX_MESSAGES
    assert [m["text"] for m in result["messages"]] == ["m1", "m2", "m3"]


# --- Payload shape -----------------------------------------------------------

def test_payload_exposes_only_stored_structured_fields(enabled):
    conversation = add_conversation(enabled, "Chat A", 0.0, 0.0)
    add_message(enabled, conversation, 1, "m1", 1.0, sender="Sender One",
                visible_time="14:30")

    message = call(bridge.get_messages, conversation_id=conversation)["messages"][0]

    assert set(message) == {
        "id", "conversation_id", "sequence", "sender", "ownership",
        "visible_time", "text", "kind", "confidence", "first_observed_at",
    }
    # Nothing from the capture or provider layers, and no SQLite internals.
    for forbidden in ("bounds", "normalizedBounds", "image", "frame",
                      "api_key", "rowid", "sqlite"):
        assert forbidden not in message


def test_unknown_sender_and_time_round_trip_as_null(enabled):
    conversation = add_conversation(enabled, "Chat A", 0.0, 0.0)
    add_message(enabled, conversation, 1, "m1", 1.0, sender=None,
                visible_time=None, ownership="unknown")

    message = call(bridge.get_messages, conversation_id=conversation)["messages"][0]

    assert message["sender"] is None
    assert message["visible_time"] is None
    assert message["ownership"] == "unknown"


def test_unicode_round_trips_unchanged(enabled):
    conversation = add_conversation(enabled, "群聊", 0.0, 0.0)
    add_message(enabled, conversation, 1, "你好 🙂", 1.0, sender="发送者")

    assert call(bridge.list_conversations)["conversations"][0]["title"] == "群聊"
    message = call(bridge.get_messages, conversation_id=conversation)["messages"][0]
    assert message["text"] == "你好 🙂"
    assert message["sender"] == "发送者"


# --- Read-only enforcement ---------------------------------------------------

def test_the_bridge_connection_cannot_write(enabled):
    conversation = add_conversation(enabled, "Chat A", 0.0, 0.0)
    connection = bridge.connect(bridge.resolve_access())
    try:
        for statement in (
            "INSERT INTO messages (conversation_id, sequence, ownership, kind, "
            "confidence, first_observed_at) VALUES (1, 1, 'other', 'text', 1.0, 1.0);",
            "UPDATE conversations SET title = 'x';",
            "DELETE FROM messages;",
            "DROP TABLE messages;",
            "CREATE TABLE injected (id INTEGER);",
            "VACUUM;",
        ):
            with pytest.raises(sqlite3.OperationalError):
                connection.execute(statement)
    finally:
        connection.close()

    # And the data is untouched.
    connection = sqlite3.connect(enabled)
    assert connection.execute("SELECT COUNT(*) FROM conversations;").fetchone()[0] == 1
    assert connection.execute(
        "SELECT title FROM conversations WHERE id = ?;", (conversation,)
    ).fetchone()[0] == "Chat A"
    connection.close()


def test_query_only_pragma_is_set(enabled):
    connection = bridge.connect(bridge.resolve_access())
    try:
        assert connection.execute("PRAGMA query_only;").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout;").fetchone()[0] == \
            bridge.BUSY_TIMEOUT_MS
    finally:
        connection.close()


def test_read_only_uri_refuses_to_create_a_missing_database(tmp_path):
    absent = tmp_path / "nope.sqlite"
    access = bridge.Access(path=str(absent))
    with pytest.raises(sqlite3.OperationalError):
        sqlite3.connect(access.uri, uri=True).execute("SELECT 1;")
    assert not absent.exists()


# --- Exposed surface ---------------------------------------------------------

@pytest.mark.asyncio
async def test_only_the_four_read_only_tools_are_exposed():
    tools = {tool.name for tool in await bridge.mcp.list_tools()}

    assert tools == {
        "status", "list_conversations", "get_messages", "get_recent_messages",
    }
    # No arbitrary SQL and no arbitrary filesystem path tool.
    for forbidden in ("query", "sql", "execute", "read_file", "open_path", "write"):
        assert forbidden not in tools


# --- Source guards -----------------------------------------------------------

def bridge_sources() -> list[Path]:
    """Every file the bridge process is made of.

    Store access and source selection moved into ``store_access`` so a process
    without the MCP SDK can use the same code (M2.2d). These invariants are
    about the bridge as a whole, so they follow the code into both files
    rather than narrowing to whichever half kept them.
    """
    return [Path(bridge.__file__), Path(store_access.__file__)]


def source_text() -> str:
    """The bridge sources with comment-only lines stripped.

    Doc comments legitimately name the things the code must not do.
    """
    lines: list[str] = []
    for path in bridge_sources():
        lines += [line for line in path.read_text(encoding="utf-8").splitlines()
                  if not line.strip().startswith("#")]
    return "\n".join(lines)


def test_the_bridge_contains_no_write_statements():
    upper = source_text().upper()
    for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP ",
                      "CREATE TABLE", "VACUUM", "ALTER TABLE"):
        assert forbidden not in upper


def test_the_bridge_never_prints_content_to_stdout():
    source = source_text()
    # The single print() is the stderr logger; nothing else writes anywhere.
    assert source.count("print(") == 1
    assert "file=sys.stderr" in source
    assert "sys.stdout" not in source
    assert "logging.basicConfig" not in source


def test_log_calls_never_interpolate_chat_content():
    """Every log(...) argument must be a fixed string or a count."""
    source = "\n".join(p.read_text(encoding="utf-8") for p in bridge_sources())
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped.startswith("log("):
            continue
        for forbidden in ("title", "text", "sender", "row[", "payload",
                          "message[", "conversation["):
            assert forbidden not in stripped, stripped


def test_no_home_directory_is_hardcoded():
    sources = [p.read_text(encoding="utf-8") for p in bridge_sources()]
    example = (Path(bridge.__file__).parents[1] / "hermes" / "config.example.yaml"
               ).read_text(encoding="utf-8")
    for text in (*sources, example):
        assert "/Users/" not in text
        assert "/home/" not in text
        assert "Application Support/WeChatCompanion" not in text
        assert os.path.expanduser("~") not in text


def test_the_example_config_carries_only_placeholders():
    example = (Path(bridge.__file__).parents[1] / "hermes" / "config.example.yaml"
               ).read_text(encoding="utf-8")
    assert "<python>" in example
    assert "<path-to-messages.sqlite>" in example
    assert "WECHAT_COMPANION_ALLOW_AGENT_READ" in example


def test_the_example_documents_the_hermes_tool_name_prefix():
    """Regression guard for the `mcp__<server>__<tool>` naming convention.

    Hermes v0.20.5 registers MCP tools with a DOUBLE-underscore delimiter
    (`mcp_prefixed_tool_name` in tools/mcp_tool.py), not the single-underscore
    form. The example must document the names an agent actually sees, so a
    reader does not write `mcp_wechat_companion_status` and wonder why nothing
    resolves.
    """
    example = (Path(bridge.__file__).parents[1] / "hermes" / "config.example.yaml"
               ).read_text(encoding="utf-8")

    server = "wechat_companion"
    assert f"\n  {server}:" in example, "server key drives every tool name"
    for tool in ("status", "list_conversations", "get_messages",
                 "get_recent_messages"):
        assert f"mcp__{server}__{tool}" in example
    # The single-underscore form is wrong for this Hermes version.
    assert "mcp_wechat_companion_status" not in example


def test_example_tool_names_match_the_servers_real_tools():
    """The documented names must be derived from the tools we actually expose."""
    import asyncio

    example = (Path(bridge.__file__).parents[1] / "hermes" / "config.example.yaml"
               ).read_text(encoding="utf-8")
    names = {tool.name for tool in asyncio.run(bridge.mcp.list_tools())}

    for name in names:
        assert f"mcp__wechat_companion__{name}" in example, name


def test_no_real_application_support_database_is_touched(enabled):
    """The whole suite must stay inside its temporary directories."""
    real = Path.home() / "Library/Application Support/WeChatCompanion/messages.sqlite"
    before = real.exists()

    call(bridge.status)
    call(bridge.list_conversations)
    call(bridge.get_recent_messages, since_observed_at=0)

    assert real.exists() == before
    # And the configured path really is the synthetic one.
    assert str(enabled).startswith(str(Path(os.environ[bridge.DB_PATH_ENV]).parent))
    assert "Application Support" not in os.environ[bridge.DB_PATH_ENV]
