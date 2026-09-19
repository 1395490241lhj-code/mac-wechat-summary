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
                name: str = "reader", record: Path | None = None) -> Path:
    """Writes a stub that answers one subcommand with a fixed string.

    It parses no arguments beyond finding which subcommand it was asked for,
    opens no file, and reads no input.

    With ``record`` it also appends its own argument vector to that path, one
    invocation per line, so a test can assert what the adapter *asked for*
    rather than only what it did with the answer. The recorded arguments are
    the synthetic ones this suite passes; no real reader is ever run.
    """
    path = tmp_path / name
    path.write_text(
        "#!" + sys.executable + "\n"
        "import sys\n"
        f"REPLIES = {replies!r}\n"
        f"EXIT = {exit_code}\n"
        f"RECORD = {str(record) if record else None!r}\n"
        "if RECORD:\n"
        "    with open(RECORD, 'a', encoding='utf-8') as handle:\n"
        "        handle.write(' '.join(sys.argv[1:]) + '\\n')\n"
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
    # ``enum`` is here for the boundary's own closed token sets and is the last
    # name this set gains; the forbidden list below is never relaxed.
    assert imported <= {"__future__", "dataclasses", "enum", "typing"}
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


def test_the_protocol_promises_a_result_envelope_not_a_bare_collection():
    """The contract's own syntax, not its prose, is what callers compile against.

    Read out of the syntax tree so that a docstring describing an envelope
    cannot pass for a signature declaring one. ``status`` is checked in the
    same pass precisely because it must *not* have moved: readiness and
    per-read coverage are different questions with different lifetimes, and a
    source that is ready can still answer partially.
    """
    import ast

    tree = ast.parse(Path(ms.__file__).read_text(encoding="utf-8"))
    protocol = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "MessageSource"
    )
    returns = {
        node.name: ast.unparse(node.returns)
        for node in protocol.body
        if isinstance(node, ast.FunctionDef) and node.returns is not None
    }

    assert returns == {
        "status": "SourceStatus",
        "list_conversations": "ReadResult[NormalizedConversation]",
        "get_messages": "ReadResult[NormalizedMessage]",
        "get_recent_messages": "ReadResult[NormalizedMessage]",
    }

    for method in ("list_conversations", "get_messages", "get_recent_messages"):
        assert not returns[method].startswith("list["), method


def test_the_protocol_signatures_are_otherwise_untouched():
    """Only the return annotations move; every call site keeps its arguments.

    A renamed parameter or a dropped default would break callers silently at
    the moment the shape changed, and would be indistinguishable in review from
    the return-type migration it travelled with.
    """
    import ast

    tree = ast.parse(Path(ms.__file__).read_text(encoding="utf-8"))
    protocol = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "MessageSource"
    )
    signatures = {
        node.name: (
            [argument.arg for argument in node.args.args],
            [ast.unparse(default) for default in node.args.defaults],
        )
        for node in protocol.body
        if isinstance(node, ast.FunctionDef)
    }

    assert signatures == {
        "status": (["self"], []),
        "list_conversations": (["self", "limit"], []),
        "get_messages": (
            ["self", "conversation_id", "limit", "before_sequence"], ["None"]),
        "get_recent_messages": (["self", "since_observed_at", "limit"], []),
    }


# --- Coverage vocabulary -----------------------------------------------------

def test_the_boundary_owns_the_four_coverage_tokens():
    """Coverage is a Reader-layer vocabulary, so the boundary owns the words.

    The values are load-bearing: they are already persisted in the memory
    store's coverage rows, so a move that changed one would silently re-key
    every stored row.
    """
    assert ms.COVERAGE_COMPLETE == "observed_complete"
    assert ms.COVERAGE_PARTIAL == "observed_partial"
    assert ms.COVERAGE_UNAVAILABLE == "unavailable"
    assert ms.COVERAGE_NOT_OBSERVED == "not_observed"

    assert isinstance(ms.COVERAGE_STATUSES, frozenset)
    assert ms.COVERAGE_STATUSES == {
        "observed_complete", "observed_partial", "unavailable", "not_observed",
    }


# --- Freshness vocabulary ----------------------------------------------------

def test_freshness_is_three_tokens_and_never_a_boolean():
    """Freshness is a comparison of two moments the source supplied.

    A boolean would have to be computed against something, and the only
    something available is a clock -- which would make the boundary assert an
    age policy it has no standing to hold. Three tokens say what was compared,
    including the case where one of the two moments was simply absent.
    """
    assert issubclass(ms.ReadFreshness, str)

    assert [member.name for member in ms.ReadFreshness] == [
        "EVIDENCE_CONSISTENT", "POTENTIALLY_STALE", "UNKNOWN",
    ]
    assert ms.ReadFreshness.EVIDENCE_CONSISTENT.value == "evidence_consistent"
    assert ms.ReadFreshness.POTENTIALLY_STALE.value == "potentially_stale"
    assert ms.ReadFreshness.UNKNOWN.value == "unknown"

    # A str enum so a payload carries the token a human reads, with no
    # translation table and no second spelling.
    assert ms.ReadFreshness.POTENTIALLY_STALE == "potentially_stale"

    _, identifiers, _ = module_identifiers(Path(ms.__file__))
    assert "fresh" not in identifiers
    assert "is_fresh" not in identifiers


# --- Reason vocabulary -------------------------------------------------------

#: The twelve tokens spec section 6.5 closes the vocabulary at, written out
#: here rather than derived from the module, so that this test disagrees with
#: the module if either one changes.
APPROVED_REASONS = {
    "full_window_observed", "empty_window", "caller_limit", "source_limit",
    "window_bound", "upstream_more", "partial_inventory", "unsafe_early_stop",
    "timestamp_mismatch", "scope_unsupported", "scope_not_read",
    "no_observation",
}

#: Which statuses each reason is a valid explanation for. The spec's table,
#: transcribed independently of the module's own mapping.
APPROVED_REASON_STATUSES = {
    "full_window_observed": {"observed_complete"},
    "empty_window": {"observed_complete"},
    "window_bound": {"observed_complete"},
    "caller_limit": {"observed_partial"},
    "source_limit": {"observed_partial"},
    "upstream_more": {"observed_partial"},
    "unsafe_early_stop": {"observed_partial"},
    "timestamp_mismatch": {"observed_partial"},
    "partial_inventory": {"observed_partial", "unavailable"},
    "scope_unsupported": {"unavailable"},
    "scope_not_read": {"not_observed"},
    "no_observation": {"not_observed"},
}


def test_the_reason_vocabulary_is_closed():
    """A reason is chosen from a fixed set, never composed.

    The point of the closure is that a reason can never carry message text, a
    sender, a path, a table or a line of provider output: there is nothing to
    put them in. A token built at runtime -- an f-string, a concatenation, a
    call -- would reopen exactly that, so the constants are checked in the
    syntax tree as well as by value.
    """
    import ast

    assert isinstance(ms.COVERAGE_REASONS, frozenset)
    assert len(ms.COVERAGE_REASONS) == 12
    assert set(ms.COVERAGE_REASONS) == APPROVED_REASONS

    for token in ms.COVERAGE_REASONS:
        assert token.isascii(), token
        assert token == token.lower(), token
        assert token.split() == [token], token
        assert token.strip() == token, token

    tree = ast.parse(Path(ms.__file__).read_text(encoding="utf-8"))
    literals: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if not target.id.startswith("REASON_") or target.id == "REASON_STATUSES":
                continue
            assert isinstance(node.value, ast.Constant), target.id
            assert isinstance(node.value.value, str), target.id
            literals[target.id] = node.value.value

    assert len(literals) == 12
    assert set(literals.values()) == APPROVED_REASONS

    assert set(ms.REASON_STATUSES) == set(ms.COVERAGE_REASONS)


def test_the_reason_status_mapping_is_total_in_both_directions():
    """Every reason explains a status, and every status has an explanation.

    A status nothing can explain would be a claim with no account of itself,
    and a reason valid for no status would be a word the vocabulary cannot use.
    """
    explained: set[str] = set()
    for reason, statuses in ms.REASON_STATUSES.items():
        assert isinstance(statuses, frozenset), reason
        assert statuses, reason
        assert statuses <= ms.COVERAGE_STATUSES, reason
        explained |= statuses

    assert explained == set(ms.COVERAGE_STATUSES)

    assert {reason: set(statuses)
            for reason, statuses in ms.REASON_STATUSES.items()} == \
        APPROVED_REASON_STATUSES


# --- Adapter success ---------------------------------------------------------

def test_status_reports_ready(tmp_path):
    report = build(make_reader(tmp_path, READY_REPLIES)).status()

    assert report.ready is True
    assert report.source == ms.SOURCE_DATABASE
    # Counting would mean a scan; unknown is reported as unknown.
    assert report.conversation_count is None
    assert report.message_count is None


def test_conversations_are_normalized(tmp_path):
    result = build(make_reader(tmp_path, READY_REPLIES)).list_conversations(10)
    conversations = result.items

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
        CONVERSATION_A, 50).items

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
        CONVERSATION_A, 50).items
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

    result = build(reader).get_messages(CONVERSATION_A, 50)

    assert result.items == ()
    # Empty is an answer here, not an absence: the reader said there is no
    # further page, so the window was actually accounted for.
    assert result.coverage.status == "observed_complete"
    assert result.coverage.reason == "empty_window"


def test_recent_messages_filter_and_sort(tmp_path):
    messages = build(make_reader(tmp_path, READY_REPLIES)).get_recent_messages(
        200.0, 50).items

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

    conversations = source.list_conversations(limit=50).items
    rendered = json.dumps(
        [c.__dict__ for c in conversations], default=str, ensure_ascii=False
    )
    assert "ARCHIVE-KEY" not in rendered

    result = source.get_messages(conversations[0].id, limit=100)
    rendered = json.dumps([m.__dict__ for m in result.items], default=str,
                          ensure_ascii=False)
    assert "VISUAL-TEXT" in rendered
    for leaked in ("ARCHIVE-TEXT", "ARCHIVE-SENDER", "ARCHIVE-KEY"):
        assert leaked not in rendered, f"{leaked} reached the visual read model"

    # `imported_at` of 9999 is newer than the visual message's observation time;
    # a recent sweep must still see only the visual row.
    recent = source.get_recent_messages(since_observed_at=0.0, limit=100)
    rendered = json.dumps([m.__dict__ for m in recent.items], default=str,
                          ensure_ascii=False)
    assert "VISUAL-TEXT" in rendered
    assert "ARCHIVE-TEXT" not in rendered

    # Nor may an archive moment advance the visual source's own coverage. The
    # archive carries `imported_at` 9999 and `sent_at` 5000, both newer than
    # the visual row at 1000; the newest-moment proof must see only 1000, so
    # this short read is complete *at 1000* rather than stale against 9999.
    for read in (result, recent):
        assert read.coverage.status == "observed_complete"
        assert read.coverage.observed_through == 1000.0
        assert read.coverage.complete_through == 1000.0


# --- P10: the visual store states conservative coverage ----------------------
#
# Visual capture observes what was on screen, so the absence of a row is not
# evidence that a message does not exist. A read may claim completeness only
# when the caller's limit was not filled AND an independent query for the
# store's newest recorded moment in the *exact* requested scope shows the read
# reached it. A short answer on its own proves nothing.


def visual_store(tmp_path: Path, *, conversations=(), messages=(),
                 name: str = "visual.sqlite") -> Path:
    """A synthetic v1 store with exactly the rows a test names.

    ``conversations`` are ``(title, first_seen_at, last_seen_at)``;
    ``messages`` are ``(conversation_id, sequence, first_observed_at)``.
    """
    path = tmp_path / name
    connection = sqlite3.connect(path)
    connection.executescript(_V1_TABLES)
    for title, first, last in conversations:
        connection.execute(
            "INSERT INTO conversations(title, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?);", (title, first, last))
    for conversation_id, sequence, observed in messages:
        connection.execute(
            "INSERT INTO messages(conversation_id, sequence, sender, ownership, "
            "visible_time, text, kind, confidence, first_observed_at) "
            "VALUES (?, ?, 'fixture', 'other', NULL, 'fixture text', 'text', "
            "0.9, ?);", (conversation_id, sequence, observed))
    connection.execute("PRAGMA user_version = 1;")
    connection.commit()
    connection.close()
    return path


def serve_store(monkeypatch, path: Path) -> store_access.StoreMessageSource:
    monkeypatch.setenv(store_access.ALLOW_READ_ENV, "1")
    monkeypatch.setenv(store_access.DB_PATH_ENV, str(path))
    return store_access.StoreMessageSource()


class AdvancingConnection:
    """A read-only connection under which the live store moves.

    Delegates everything to the real connection, but runs ``advance`` against
    the store file just before the *second* statement the source issues --
    which is after the item query and before the newest-moment query. This
    is the race the design names, made deterministic: it does not depend on
    the SQL text, only on the order of the two reads, so it fails just as
    loudly if the second read is deleted and completeness is guessed from a
    count instead.
    """

    def __init__(self, real, path: Path, advance):
        self._real = real
        self._path = path
        self._advance = advance
        self._statements = 0

    def execute(self, sql, parameters=()):
        self._statements += 1
        if self._statements == 2:
            self._advance(self._path)
        return self._real.execute(sql, parameters)

    def close(self):
        self._real.close()


def advancing(monkeypatch, path: Path, advance):
    """Serve ``path`` through a connection that advances between its reads."""
    real_open = store_access.open_verified

    def open_advancing():
        connection, version = real_open()
        return AdvancingConnection(connection, path, advance), version

    monkeypatch.setattr(store_access, "open_verified", open_advancing)


def write(path: Path, sql: str, parameters=()):
    connection = sqlite3.connect(path)
    connection.execute(sql, parameters)
    connection.commit()
    connection.close()


def newer_conversation(path: Path):
    write(path, "INSERT INTO conversations(title, first_seen_at, last_seen_at) "
                "VALUES ('appeared later', 1.0, 900.0);")


def newer_message(path: Path):
    write(path, "INSERT INTO messages(conversation_id, sequence, sender, "
                "ownership, visible_time, text, kind, confidence, "
                "first_observed_at) VALUES (1, 99, 'fixture', 'other', NULL, "
                "'appeared later', 'text', 0.9, 900.0);")


STORE_READS = [
    ("list_conversations", lambda s: s.list_conversations(10), newer_conversation),
    ("get_messages", lambda s: s.get_messages(1, 10), newer_message),
    ("get_recent_messages", lambda s: s.get_recent_messages(0.0, 10), newer_message),
]


@pytest.mark.parametrize("name,read,advance", STORE_READS,
                         ids=[r[0] for r in STORE_READS])
def test_the_visual_store_never_reports_complete_from_item_count_alone(
        tmp_path, monkeypatch, name, read, advance):
    """One row for a limit of ten, and the store moved between the two reads.

    Under `len(items) < limit` this is complete. It is not: a newer
    observation in the very same scope appeared after the item query, and
    the independent newest-moment query is the only thing that can see it.
    Delete that query and this test fails.
    """
    path = visual_store(tmp_path, conversations=[("chat", 1.0, 100.0)],
                        messages=[(1, 1, 100.0)])
    source = serve_store(monkeypatch, path)
    advancing(monkeypatch, path, advance)

    result = read(source)

    assert len(result.items) == 1
    assert result.coverage.status == "observed_partial"
    assert result.coverage.reason == "timestamp_mismatch"
    assert result.coverage.freshness is ms.ReadFreshness.POTENTIALLY_STALE
    assert result.coverage.truncated is False
    assert result.coverage.observed_through == 100.0
    assert result.coverage.complete_through is None
    # The newer row is reported as evidence, never fetched, filtered or
    # reordered into the answer.
    assert all(getattr(item, "first_observed_at", getattr(item, "last_seen_at",
               None)) == 100.0 for item in result.items)


@pytest.mark.parametrize("name,read,_", STORE_READS,
                         ids=[r[0] for r in STORE_READS])
def test_the_visual_store_reports_complete_only_at_its_newest_moment(
        tmp_path, monkeypatch, name, read, _):
    """The stable case: the newest moment read equals the store's newest.

    Equality, not a threshold. The independent query and the item read agree
    on one moment, so the read reached everything the store itself claims to
    hold for the scope.
    """
    path = visual_store(tmp_path, conversations=[("chat", 1.0, 100.0)],
                        messages=[(1, 1, 50.0), (1, 2, 100.0)])
    result = read(serve_store(monkeypatch, path))

    assert 0 < len(result.items) < 10
    assert result.coverage.status == "observed_complete"
    assert result.coverage.reason == "full_window_observed"
    assert result.coverage.truncated is False
    assert result.coverage.freshness is ms.ReadFreshness.EVIDENCE_CONSISTENT
    assert result.coverage.observed_through == 100.0
    assert result.coverage.complete_through == 100.0


def test_the_visual_store_can_produce_a_trustworthy_empty(tmp_path, monkeypatch):
    """Zero items is knowledge only when the scope is independently empty.

    Each scope here is empty for its own reason -- no conversations at all, a
    conversation with no messages, a lower bound past every observation --
    and each is accepted as empty only after the newest-moment query for that
    exact scope also came back empty.
    """
    path = visual_store(tmp_path, conversations=[("quiet", 1.0, 2.0),
                                                  ("chat", 1.0, 3.0)],
                        messages=[(2, 1, 50.0)])
    source = serve_store(monkeypatch, path)

    for result in (source.get_messages(1, 10),
                   source.get_recent_messages(60.0, 10)):
        assert result.items == ()
        assert result.coverage.status == "observed_complete"
        assert result.coverage.reason == "empty_window"
        assert result.coverage.truncated is False
        assert result.coverage.freshness is ms.ReadFreshness.UNKNOWN
        assert result.coverage.observed_through is None
        assert result.coverage.complete_through is None

    empty = serve_store(monkeypatch, visual_store(tmp_path, name="none.sqlite"))
    result = empty.list_conversations(10)
    assert result.items == ()
    assert result.coverage.reason == "empty_window"

    # And the negative half: an empty item read whose scope is *not*
    # independently empty is not a trustworthy empty. The store gained a row
    # in scope between the two reads; that is the mismatch case, not the
    # empty one. (Re-served: the empty store above re-pointed the path.)
    source = serve_store(monkeypatch, path)
    advancing(monkeypatch, path, newer_message)
    raced = source.get_messages(1, 10)
    assert raced.items == ()
    assert raced.coverage.status == "observed_partial"
    assert raced.coverage.reason == "timestamp_mismatch"
    assert raced.coverage.freshness is ms.ReadFreshness.POTENTIALLY_STALE


def test_a_filled_limit_on_the_visual_store_is_partial(tmp_path, monkeypatch):
    """A filled limit never proves exhaustion, even when the scope holds
    exactly that many rows.

    Three rows, a limit of three: the answer is full, and the store could
    hold a fourth for all this read can tell. The visual path stays
    conservative and does not spend a newest-moment query to find out.
    """
    path = visual_store(
        tmp_path,
        conversations=[("a", 1.0, 10.0), ("b", 1.0, 20.0), ("c", 1.0, 30.0)],
        messages=[(1, 1, 10.0), (1, 2, 20.0), (1, 3, 30.0)])
    source = serve_store(monkeypatch, path)

    for result in (source.list_conversations(3),
                   source.get_messages(1, 3),
                   source.get_recent_messages(0.0, 3)):
        assert len(result.items) == 3
        assert result.coverage.status == "observed_partial"
        assert result.coverage.reason == "caller_limit"
        assert result.coverage.truncated is True
        assert result.coverage.freshness is ms.ReadFreshness.UNKNOWN
        assert result.coverage.observed_through == 30.0
        assert result.coverage.complete_through is None
        assert result.coverage.item_count == 3


def test_conversation_coverage_is_judged_by_last_seen_at_not_message_time(
        tmp_path, monkeypatch):
    """The two observation clocks are not the same clock.

    The app updates `conversations.last_seen_at` whenever a conversation is
    observed, whether or not a message was stored, so it can run ahead of
    every `messages.first_observed_at` -- or behind it. Conversation-list
    coverage must be measured against the conversations table's own moment
    in both directions.
    """
    # Conversation observed later than any message it holds.
    ahead = visual_store(tmp_path, name="ahead.sqlite",
                         conversations=[("chat", 1.0, 500.0)],
                         messages=[(1, 1, 100.0)])
    result = serve_store(monkeypatch, ahead).list_conversations(10)
    assert result.coverage.status == "observed_complete"
    assert result.coverage.observed_through == 500.0
    assert result.coverage.complete_through == 500.0

    # Conversation observed earlier than a message it holds. A newest-moment
    # query aimed at `messages` would see 500 > 100 and call this stale; the
    # conversations table says 100 == 100 and the list is complete.
    behind = visual_store(tmp_path, name="behind.sqlite",
                          conversations=[("chat", 1.0, 100.0)],
                          messages=[(1, 1, 500.0)])
    result = serve_store(monkeypatch, behind).list_conversations(10)
    assert result.coverage.status == "observed_complete"
    assert result.coverage.reason == "full_window_observed"
    assert result.coverage.observed_through == 100.0


def test_a_historical_page_is_judged_within_its_own_cursor(tmp_path, monkeypatch):
    """`before_sequence` is a cursor, and the newest-moment proof honours it.

    Newer messages exist past the cursor, and in another conversation. Neither
    is in this page's scope, so neither may make the page stale or partial.
    """
    path = visual_store(tmp_path, conversations=[("chat", 1.0, 1.0),
                                                  ("other", 1.0, 1.0)],
                        messages=[(1, 1, 10.0), (1, 2, 20.0), (1, 3, 30.0),
                                  (1, 4, 40.0), (1, 5, 50.0),
                                  (2, 1, 999.0)])
    source = serve_store(monkeypatch, path)

    page = source.get_messages(1, 10, before_sequence=3)
    assert [m.sequence for m in page.items] == [1, 2]
    assert page.coverage.status == "observed_complete"
    assert page.coverage.reason == "full_window_observed"
    assert page.coverage.observed_through == 20.0
    assert page.coverage.complete_through == 20.0
    # A cursor is not a time window.
    assert page.coverage.requested_start is None
    assert page.coverage.requested_end is None

    # The other conversation's 999 is out of scope for this one as well.
    whole = source.get_messages(1, 10)
    assert whole.coverage.status == "observed_complete"
    assert whole.coverage.observed_through == 50.0


def test_the_recent_lower_bound_scopes_the_proof_and_keeps_its_precision(
        tmp_path, monkeypatch):
    """Rows older than the bound are outside the scope entirely.

    A newest-moment query that ignored the bound would see the 50.0 row and
    turn an honestly empty window into a mismatch. It must not.
    """
    path = visual_store(tmp_path, conversations=[("chat", 1.0, 1.0)],
                        messages=[(1, 1, 50.0), (1, 2, 100.25), (1, 3, 150.0)])
    source = serve_store(monkeypatch, path)

    result = source.get_recent_messages(100.25, 10)
    assert [m.first_observed_at for m in result.items] == [100.25, 150.0]
    assert result.coverage.requested_start == 100.25
    assert result.coverage.requested_end is None
    assert result.coverage.status == "observed_complete"
    assert result.coverage.observed_through == 150.0

    # Past every observation: empty, and provably so despite the older rows.
    beyond = source.get_recent_messages(200.5, 10)
    assert beyond.items == ()
    assert beyond.coverage.reason == "empty_window"
    assert beyond.coverage.requested_start == 200.5


def test_every_visual_answer_is_a_result_whose_count_matches(tmp_path, monkeypatch):
    source = serve_store(monkeypatch, make_store(tmp_path))
    for result in (source.list_conversations(10),
                   source.get_messages(1, 10),
                   source.get_recent_messages(0.0, 10)):
        assert isinstance(result, ms.ReadResult)
        assert isinstance(result.items, tuple)
        assert result.coverage.item_count == len(result.items)


def test_the_visual_store_still_refuses_hard_and_reads_no_clock(
        tmp_path, monkeypatch):
    """A refusal is a refusal; there is no envelope for it, and no clock."""
    import ast

    monkeypatch.setenv(store_access.ALLOW_READ_ENV, "1")
    monkeypatch.setenv(store_access.DB_PATH_ENV, str(tmp_path / "absent.sqlite"))
    with pytest.raises(store_access.BridgeUnavailable) as refusal:
        store_access.StoreMessageSource().list_conversations(10)
    assert refusal.value.state == "database_missing"

    tree = ast.parse(Path(store_access.__file__).read_text(encoding="utf-8"))
    imported = {
        (alias.name if isinstance(node, ast.Import) else (node.module or ""))
        .split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (node.names if isinstance(node, ast.Import) else [None])
    }
    for forbidden in ("time", "datetime", "calendar"):
        assert forbidden not in imported, forbidden
#
# Pagination evidence differs by command in the gated Rion revision
# (3afe33e0..., see spec section 9.1): `history` emits a `query` object,
# `sessions` emits none. So conversation truncation is *measured* by asking for
# one row more than the caller wanted, and the absence of that row is an answer
# the source gave rather than a short count the adapter read meaning into.


def session_row(index: int) -> dict:
    return {"username": f"wxid_fixture_{index}", "type": 1, "unread_count": 0,
            "summary": "fixture summary", "last_timestamp": 100 + index,
            "display_name": f"Fixture {index}", "chat_type": "private"}


def sessions_reply(count: int) -> str:
    return envelope("sessions", {"sessions": [session_row(i) for i in range(count)]})


#: One more conversation than the internal sweep bound, so the sentinel lands
#: exactly at RECENT_CONVERSATION_SCAN_LIMIT + 1.
SESSIONS_OVER_SWEEP = sessions_reply(adapter.RECENT_CONVERSATION_SCAN_LIMIT + 1)

#: A next offset no legitimate coverage field could coincidentally hold. The
#: real reader would send a small number here, which is exactly what makes a
#: small number useless for proving the value did not leak: it would match an
#: item count or a sequence by accident.
LEAKY_OFFSET = 987_654

#: The same three rows as HISTORY, but the reader says a page remains.
HISTORY_MORE = envelope("history", {
    "query": {"has_more": True, "next_offset": LEAKY_OFFSET},
    "messages": json.loads(HISTORY)["data"]["messages"],
})

ONE_SESSION = sessions_reply(1)


def recorded(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line]


# --- The sessions contract ---------------------------------------------------

def test_the_sessions_fixture_deliberately_carries_no_query():
    """Evidence about the real reader, not an oversight in the fixture.

    The gated revision emits `{"sessions": [...]}` and nothing else, while
    `history` emits a `query` object. Completing this fixture with an invented
    `query` would make every sessions coverage test prove something about a
    reader that does not exist.
    """
    assert "query" not in json.loads(SESSIONS)["data"]
    assert "query" in json.loads(HISTORY)["data"]


def test_the_adapter_asks_sessions_for_one_row_more_than_the_caller_wanted(
        tmp_path):
    """Asserted from what was asked, not from what came back.

    Reading this off the answer would pass just as well if the adapter had
    asked for the caller's limit and trimmed nothing -- the overfetch is the
    whole mechanism, so the argument vector is what the test pins.
    """
    log = tmp_path / "argv.log"
    source = build(make_reader(tmp_path, READY_REPLIES, record=log))

    source.list_conversations(10)
    assert "sessions --limit 11" in recorded(log)

    # The identifier cache is cold, so `_chat_for` refreshes over the internal
    # sweep bound -- which overfetches by one as well.
    source = build(make_reader(tmp_path, READY_REPLIES, record=log,
                               name="reader2"))
    source.get_messages(CONVERSATION_A, 10)
    assert f"sessions --limit {adapter.RECENT_CONVERSATION_SCAN_LIMIT + 1}" in \
        recorded(log)


def test_a_returned_sentinel_row_proves_the_caller_limit_truncated(tmp_path):
    """The extra row came back, so a conversation the caller cannot see exists."""
    reader = make_reader(tmp_path, READY_REPLIES)
    result = build(reader).list_conversations(1)

    assert len(result.items) == 1
    assert result.coverage.status == "observed_partial"
    assert result.coverage.reason == "caller_limit"
    assert result.coverage.truncated is True
    assert result.coverage.item_count == 1
    assert result.coverage.freshness is ms.ReadFreshness.UNKNOWN
    # A caller-limited read names no gap-free point.
    assert result.coverage.complete_through is None


def test_an_absent_sentinel_row_proves_the_session_list_exhausted(tmp_path):
    """Asked for eleven, given two: the source answered that there are two."""
    result = build(make_reader(tmp_path, READY_REPLIES)).list_conversations(10)

    assert len(result.items) == 2
    assert result.coverage.status == "observed_complete"
    assert result.coverage.reason == "full_window_observed"
    assert result.coverage.truncated is False
    assert result.coverage.observed_through == 300.0
    assert result.coverage.complete_through == 300.0


def test_an_empty_session_list_is_a_trustworthy_empty(tmp_path):
    reader = make_reader(tmp_path, {"doctor": DOCTOR_READY,
                                    "sessions": sessions_reply(0),
                                    "history": EMPTY_HISTORY})
    result = build(reader).list_conversations(10)

    assert result.items == ()
    assert result.coverage.status == "observed_complete"
    assert result.coverage.reason == "empty_window"
    assert result.coverage.truncated is False
    assert result.coverage.observed_through is None
    assert result.coverage.complete_through is None


def test_the_sentinel_row_has_no_public_side_effect(tmp_path):
    """Evidence only: it is never normalised, cached, or made resolvable.

    A sentinel that reached `_chat_by_id` would silently widen the adapter's
    reachable set by one conversation beyond the bound every caller was told
    about, which is the opposite of what measuring the bound is for.
    """
    reader = make_reader(tmp_path, {"doctor": DOCTOR_READY,
                                    "sessions": SESSIONS_OVER_SWEEP,
                                    "history": EMPTY_HISTORY})
    source = build(reader)

    sweep = adapter.RECENT_CONVERSATION_SCAN_LIMIT
    result = source.list_conversations(sweep)

    assert len(result.items) == sweep
    beyond = adapter.conversation_identifier(f"wxid_fixture_{sweep}")
    assert beyond not in {item.id for item in result.items}
    assert beyond not in source._chat_by_id

    # And it stays unknown across the refresh `_chat_for` performs, which is
    # itself bounded at the sweep limit.
    with pytest.raises(adapter.MessageSourceError) as refusal:
        source.get_messages(beyond, 10)
    assert refusal.value.state == "conversation_unknown"

    # The sentinel's own moment never leaks into a boundary either.
    assert result.coverage.observed_through == float(100 + sweep - 1)


# --- History pagination ------------------------------------------------------

def test_upstream_has_more_is_preserved_as_partial_coverage(tmp_path):
    """The reader said a page remains; that is not the caller's limit."""
    reader = make_reader(tmp_path, {"doctor": DOCTOR_READY, "sessions": SESSIONS,
                                    "history": HISTORY_MORE})
    result = build(reader).get_messages(CONVERSATION_A, 50)

    assert len(result.items) == 3
    assert result.coverage.status == "observed_partial"
    assert result.coverage.reason == "upstream_more"
    assert result.coverage.truncated is True
    assert result.coverage.complete_through is None


def test_next_offset_never_appears_in_coverage(tmp_path):
    """Paging state is the adapter's own business, not the envelope's."""
    import dataclasses

    reader = make_reader(tmp_path, {"doctor": DOCTOR_READY, "sessions": SESSIONS,
                                    "history": HISTORY_MORE})
    source = build(reader)
    result = source.get_messages(CONVERSATION_A, 50)

    assert source._next_offset == LEAKY_OFFSET

    names = {field.name for field in dataclasses.fields(ms.ReadCoverage)}
    assert "next_offset" not in names
    for name in names:
        assert getattr(result.coverage, name) != LEAKY_OFFSET, name

    rendered = repr(result.coverage) + repr([i.payload() for i in result.items])
    assert "next_offset" not in rendered
    assert str(LEAKY_OFFSET) not in rendered


def test_an_exhausted_window_is_complete(tmp_path):
    result = build(make_reader(tmp_path, READY_REPLIES)).get_messages(
        CONVERSATION_A, 50)

    assert result.coverage.status == "observed_complete"
    assert result.coverage.reason == "full_window_observed"
    assert result.coverage.truncated is False
    assert result.coverage.observed_through == 300.0
    assert result.coverage.complete_through == 300.0


def test_an_empty_exhausted_window_is_a_trustworthy_empty(tmp_path):
    reader = make_reader(tmp_path, {"doctor": DOCTOR_READY, "sessions": SESSIONS,
                                    "history": EMPTY_HISTORY})
    result = build(reader).get_messages(CONVERSATION_A, 50)

    assert result.items == ()
    assert result.coverage.status == "observed_complete"
    assert result.coverage.reason == "empty_window"
    assert result.coverage.truncated is False
    # The fixture omits next_offset; absence is None, not a guess.
    assert build(reader)._next_offset is None


@pytest.mark.parametrize("query", [
    None,
    "not an object",
    {},
    {"has_more": "false"},
    {"has_more": 0},
    {"has_more": False, "next_offset": "3"},
    {"has_more": False, "next_offset": True},
])
def test_malformed_history_pagination_fails_closed(tmp_path, query):
    """Bad evidence is refused, never replaced with an assumption.

    Guessing here would manufacture exactly the false completeness this whole
    design exists to remove, from a reply the adapter could not read.
    """
    data = {"messages": []}
    if query is not None:
        data["query"] = query
    reader = make_reader(tmp_path, {"doctor": DOCTOR_READY, "sessions": SESSIONS,
                                    "history": envelope("history", data)})

    with pytest.raises(adapter.MessageSourceError) as refusal:
        build(reader).get_messages(CONVERSATION_A, 50)
    assert refusal.value.state == "reader_malformed_response"


# --- The recent sweep --------------------------------------------------------

def test_the_bounded_conversation_sweep_reports_its_own_limit(tmp_path):
    """The internal 50-conversation bound stops being invisible.

    The caller asked for messages, not for fifty conversations, so this is the
    source's own limit and not the caller's -- naming it `caller_limit` would
    describe a bound the caller never set.
    """
    reader = make_reader(tmp_path, {"doctor": DOCTOR_READY,
                                    "sessions": SESSIONS_OVER_SWEEP,
                                    "history": EMPTY_HISTORY})
    result = build(reader).get_recent_messages(0.0, 50)

    assert result.coverage.status == "observed_partial"
    assert result.coverage.reason == "source_limit"
    assert result.coverage.truncated is True


def test_a_short_answer_to_a_large_request_is_not_complete(tmp_path):
    """T-11, the defect this design exists to remove.

    Three messages for a two-hundred-message request, with a child history the
    reader itself said was incomplete. Under the old length inference this was
    recorded as complete; the item count decides nothing here.
    """
    reader = make_reader(tmp_path, {"doctor": DOCTOR_READY,
                                    "sessions": ONE_SESSION,
                                    "history": HISTORY_MORE})
    result = build(reader).get_recent_messages(0.0, 200)

    assert len(result.items) == 3
    assert result.coverage.status != "observed_complete"
    assert result.coverage.status == "observed_partial"
    assert result.coverage.reason == "source_limit"
    assert result.coverage.truncated is True
    assert result.coverage.item_count == 3


def test_an_exhausted_sweep_over_exhausted_children_is_complete(tmp_path):
    """Nothing was bounded anywhere, so the answer is worth its completeness."""
    reader = make_reader(tmp_path, {"doctor": DOCTOR_READY,
                                    "sessions": ONE_SESSION,
                                    "history": HISTORY})
    result = build(reader).get_recent_messages(0.0, 50)

    assert len(result.items) == 3
    assert result.coverage.status == "observed_complete"
    assert result.coverage.reason == "full_window_observed"
    assert result.coverage.truncated is False
    assert result.coverage.observed_through == 300.0
    assert result.coverage.complete_through == 300.0


def test_an_internal_bound_outranks_the_caller_limit_explanation(tmp_path):
    """Precedence: a source-internal truncation is never dressed as the caller's.

    Here both could be said -- the sweep was bounded and nothing matched -- and
    the source's own bound is the one that must survive, because it is the one
    the caller could not have known about.
    """
    reader = make_reader(tmp_path, {"doctor": DOCTOR_READY,
                                    "sessions": SESSIONS_OVER_SWEEP,
                                    "history": EMPTY_HISTORY})
    result = build(reader).get_recent_messages(0.0, 1)

    assert result.coverage.reason == "source_limit"
    assert result.coverage.reason != "caller_limit"


def test_the_recent_lower_bound_keeps_its_fractional_precision(tmp_path):
    """The caller's window is recorded as asked, not as rounded."""
    result = build(make_reader(tmp_path, READY_REPLIES)).get_recent_messages(
        100.25, 50)

    assert result.coverage.requested_start == 100.25
    assert result.coverage.requested_end is None


def test_the_adapter_reads_no_clock(tmp_path):
    """Freshness is UNKNOWN because the reader supplies no newest moment.

    Not because a threshold was chosen: there is no clock in this path at all,
    so there is no age to compare and nothing to tune.
    """
    import ast

    source = build(make_reader(tmp_path, READY_REPLIES))
    for result in (source.list_conversations(10),
                   source.get_messages(CONVERSATION_A, 50),
                   source.get_recent_messages(0.0, 50)):
        assert result.coverage.freshness is ms.ReadFreshness.UNKNOWN

    tree = ast.parse(Path(adapter.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    identifiers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
        elif isinstance(node, ast.Name):
            identifiers.add(node.id)

    for forbidden in ("time", "datetime", "calendar"):
        assert forbidden not in imported, forbidden
    for forbidden in ("now", "monotonic", "perf_counter", "utcnow", "today"):
        assert forbidden not in identifiers, forbidden


def test_every_collection_answer_is_a_result_whose_count_matches(tmp_path):
    source = build(make_reader(tmp_path, READY_REPLIES))
    for result in (source.list_conversations(10),
                   source.get_messages(CONVERSATION_A, 50),
                   source.get_recent_messages(0.0, 50)):
        assert isinstance(result, ms.ReadResult)
        assert isinstance(result.items, tuple)
        assert result.coverage.item_count == len(result.items)


# --- The tools tolerate either shape -----------------------------------------
#
# During the migration a source may hand back a bare list or a ReadResult, and
# the three collection tools have to serve the same answer from either. The
# stub below is the only place in this suite that returns a ReadResult, and it
# contacts nothing: no store, no reader, no database, no agent-read opt-in.

ENVELOPE_CONVERSATIONS = [
    ms.NormalizedConversation(
        id=11, title="Fixture Contact A", first_seen_at=None,
        last_seen_at=300.0, source=ms.SOURCE_DATABASE),
    ms.NormalizedConversation(
        id=22, title="Fixture Room", first_seen_at=None,
        last_seen_at=100.0, source=ms.SOURCE_DATABASE),
]

ENVELOPE_MESSAGES = [
    ms.NormalizedMessage(
        id=1, conversation_id=11, sequence=7, sender="Fixture Contact A",
        ownership="other", visible_time=None, text="fixture one", kind="text",
        confidence=1.0, first_observed_at=100.0, source=ms.SOURCE_DATABASE),
    ms.NormalizedMessage(
        id=2, conversation_id=11, sequence=8, sender="Fixture Me",
        ownership="mine", visible_time=None, text="fixture two", kind="text",
        confidence=1.0, first_observed_at=200.0, source=ms.SOURCE_DATABASE),
]


def lawful_coverage(count):
    """A complete, unremarkable claim. P8 consumes none of it; it exists so the
    stub can build a ReadResult at all."""
    return ms.ReadCoverage(
        status=ms.COVERAGE_COMPLETE, reason=ms.REASON_FULL_WINDOW_OBSERVED,
        requested_start=None, requested_end=None,
        observed_through=None, complete_through=None,
        freshness=ms.ReadFreshness.EVIDENCE_CONSISTENT,
        truncated=False, item_count=count,
    )


class ShapedSource:
    """Serves the same fixtures as a list or as a ReadResult, on request."""

    name = ms.SOURCE_DATABASE

    def __init__(self, *, envelope: bool):
        self.envelope = envelope

    def _serve(self, items):
        if not self.envelope:
            return list(items)
        return ms.ReadResult(items=tuple(items),
                             coverage=lawful_coverage(len(items)))

    def status(self):
        return ms.SourceStatus(
            ready=True, source=self.name, conversation_count=None,
            message_count=None, detail=None)

    def list_conversations(self, limit):
        return self._serve(ENVELOPE_CONVERSATIONS[:limit])

    def get_messages(self, conversation_id, limit, before_sequence=None):
        return self._serve(ENVELOPE_MESSAGES[:limit])

    def get_recent_messages(self, since_observed_at, limit):
        return self._serve(ENVELOPE_MESSAGES[:limit])


def serve_with(monkeypatch, *, envelope: bool):
    source = ShapedSource(envelope=envelope)
    monkeypatch.setattr(bridge, "active_source", lambda: source)
    return source


def test_the_tools_serve_a_result_envelope_unchanged(monkeypatch):
    """The wire answer must not depend on how a source packaged its rows.

    Compared payload against payload rather than field by field, so a key that
    appeared or vanished with the shape would fail here rather than survive to
    a client.
    """
    calls = [
        (bridge.list_conversations, {"limit": 5}),
        (bridge.get_messages, {"conversation_id": 11, "limit": 5}),
        (bridge.get_recent_messages, {"since_observed_at": 0.0, "limit": 5}),
    ]

    for tool, kwargs in calls:
        serve_with(monkeypatch, envelope=False)
        from_list = call(tool, **kwargs)
        serve_with(monkeypatch, envelope=True)
        from_envelope = call(tool, **kwargs)

        assert from_envelope == from_list, tool
        assert from_envelope["ok"] is True
        assert "coverage" not in from_envelope

    # And the keys themselves are the ones already published, per tool.
    serve_with(monkeypatch, envelope=True)
    assert set(call(bridge.list_conversations, limit=5)) == {
        "ok", "source", "limit", "conversations"}
    assert set(call(bridge.get_messages, conversation_id=11, limit=5)) == {
        "ok", "source", "conversation_id", "limit", "next_before_sequence",
        "messages"}
    assert set(call(bridge.get_recent_messages,
                    since_observed_at=0.0, limit=5)) == {
        "ok", "source", "since_observed_at", "limit", "messages"}


def test_paging_state_survives_a_result_envelope(monkeypatch):
    """The one production site that indexes a source's return.

    ``next_before_sequence`` is the first row's sequence, and a ReadResult has
    no ``__getitem__`` on purpose -- a consumer that reaches past iteration is
    the consumer this design exists to correct. Reading it from a materialised
    tuple is what keeps both shapes working.
    """
    serve_with(monkeypatch, envelope=False)
    from_list = call(bridge.get_messages, conversation_id=11, limit=5)

    serve_with(monkeypatch, envelope=True)
    from_envelope = call(bridge.get_messages, conversation_id=11, limit=5)

    assert from_list["next_before_sequence"] == 7
    assert from_envelope["next_before_sequence"] == 7
    assert from_envelope["messages"] == from_list["messages"]

    # An empty answer still reports no paging cursor rather than raising.
    monkeypatch.setattr(ShapedSource, "get_messages",
                        lambda self, c, limit, before_sequence=None:
                        self._serve([]))
    serve_with(monkeypatch, envelope=True)
    assert call(bridge.get_messages, conversation_id=11,
                limit=5)["next_before_sequence"] is None


# --- D-017: an isolated schema provider stays isolated ------------------------

#: Every Python tree that is product core or a generic abstraction. A
#: WeChat-specific schema provider may exist in this repository, but nothing
#: here may depend on one: the Reader contract is the only data boundary
#: product core is allowed to know.
PRODUCT_TREES = ("bridge", "memory", "shadow", "ai", "core")

#: The candidate provider. Isolated by construction today; this test is what
#: keeps it isolated when someone is in a hurry.
CANDIDATE_PROVIDER = "wechatdb"


def test_no_product_module_imports_the_candidate_schema_provider():
    """`wechatdb` is a candidate provider, not a wired one.

    It parses a plaintext WeChat 4.1+ schema and is exercised against synthetic
    fixtures only. Until it has been proven against a real database and adopted
    through the Reader contract, an import of it from product core would be a
    production routing decision made by an import statement.
    """
    import ast

    root = Path(__file__).resolve().parents[2]
    candidates = [root / "app.py", root / "mcp_server.py"]
    for tree_name in PRODUCT_TREES:
        candidates.extend(sorted((root / tree_name).rglob("*.py")))

    offenders: list[str] = []
    for path in candidates:
        if not path.is_file():
            continue
        parsed = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(parsed):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name.split(".")[0] == CANDIDATE_PROVIDER for name in names):
                offenders.append(str(path.relative_to(root)))

    assert offenders == [], offenders


# --- P0: one canonical conversation identity ---------------------------------

#: The trees that must never hold a second copy of the construction. `memory/`
#: is deliberately absent: it hashes for its own unrelated purposes, and this
#: guard is about the Reader layer's conversation identity only.
IDENTITY_TREES = ("bridge", "wechatdb")

#: The identifiers these fixtures already derive, recorded as literals before
#: the function moved. A move that changes any of them by one bit would
#: silently re-key everything already stored against them.
IDENTIFIERS_BEFORE_THE_MOVE = {
    "wxid_fixture_a": 64790855742931,
    "fixture@chatroom": 132956166894205,
}


def module_identifiers(path: Path) -> tuple[set[str], set[str], list[str]]:
    """Imports, identifiers and string constants of one module, via `ast`.

    The same shape T-15 uses: checked against what the module *names*, so that
    prose describing what it avoids cannot be mistaken for a dependency on it.
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    identifiers: set[str] = set()
    constants: list[str] = []
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
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            constants.append(node.value)
    return imported, identifiers, constants


def test_conversation_identity_has_its_own_generic_module():
    """The construction has one owner, and it is not a source adapter."""
    import conversation_identity

    first = conversation_identity.conversation_identifier("wxid_fixture_a")
    assert first == conversation_identity.conversation_identifier("wxid_fixture_a")
    assert first != conversation_identity.conversation_identifier("fixture@chatroom")
    assert 0 < first < 2 ** 53


def test_the_identifier_is_unchanged_for_every_existing_fixture():
    """Nothing already derived, stored or asserted shifts by one bit."""
    import conversation_identity

    for chat, expected in IDENTIFIERS_BEFORE_THE_MOVE.items():
        assert conversation_identity.conversation_identifier(chat) == expected, chat
        assert adapter.conversation_identifier(chat) == expected, chat

    assert CONVERSATION_A == IDENTIFIERS_BEFORE_THE_MOVE["wxid_fixture_a"]


def test_only_one_implementation_of_conversation_identity_exists():
    """A second copy pinned by an equality test would still be a second copy.

    This is what stops one reappearing — here, or in a future provider.
    """
    import ast

    root = Path(__file__).resolve().parents[2]
    offenders: list[str] = []
    for tree_name in IDENTITY_TREES:
        for path in sorted((root / tree_name).rglob("*.py")):
            if "__pycache__" in path.parts or path.name == "conversation_identity.py":
                continue
            parsed = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(parsed):
                defines = (isinstance(node, ast.FunctionDef)
                           and node.name == "conversation_identifier")
                digests = ((isinstance(node, ast.Attribute) and node.attr == "blake2b")
                           or (isinstance(node, ast.Name) and node.id == "blake2b"))
                if defines or digests:
                    offenders.append(path.relative_to(root).as_posix())
                    break

    assert offenders == [], offenders


def test_the_boundary_module_never_gains_a_digest_dependency():
    """The sealed Reader boundary stays free of the identity construction."""
    imported, identifiers, _ = module_identifiers(Path(ms.__file__))

    assert "hashlib" not in imported
    assert "conversation_identifier" not in identifiers
    assert "blake2b" not in identifiers


def test_the_identity_module_is_technology_neutral():
    """The owner of the construction names no reader, schema, codec or path."""
    import conversation_identity

    imported, identifiers, constants = module_identifiers(
        Path(conversation_identity.__file__))

    assert imported <= {"__future__", "hashlib"}
    joined = " ".join(identifiers).lower()
    for forbidden in ("rion", "subprocess", "sqlcipher", "wechat", "json",
                      "argv", "zstd", "sqlite", "msg_", "name2id"):
        assert forbidden not in joined, forbidden
    for text in constants:
        assert "/" not in text and "\\" not in text, text
