"""The read-only memory MCP server (M2.1): tools, schemas, envelope, refusals,
and a real process-boundary composition test over a synthetic store.

Nothing here reads the real preference domain: the in-process tests inject the
consent reader, and the subprocess test points HOME at a temporary directory
and gives the child a PATH with no `defaults` tool, so the server's own plist
fallback reads a fixture file. All data is invented.
"""

from __future__ import annotations

import asyncio
import json
import os
import plistlib
import sqlite3
import sys
from pathlib import Path

import pytest

import memory_consent as consent
import memory_identity as identity
import wechat_memory_mcp as server
from conftest import app_state, conversation, database_message, granted, visual_message
from memory_ingest import MemoryIngestor
from memory_store import (
    COVERAGE_COMPLETE,
    COVERAGE_UNAVAILABLE,
    LINK_KIND_MESSAGE,
    LINK_OPERATOR,
    CoverageRecord,
    MemoryStore,
)
from message_source import SOURCE_DATABASE, SOURCE_VISUAL

BASE = 1_700_000_000.0
CONV = identity.conversation_canonical_id(SOURCE_VISUAL, "7")


def vid(n):
    return identity.message_canonical_id_from_source(SOURCE_VISUAL, str(n))


def call(tool, **kwargs):
    return getattr(tool, "fn", tool)(**kwargs)


def populate(tmp_path):
    """A synthetic store: ten visual messages, one database message, complete coverage."""
    with MemoryStore.open(granted(tmp_path)) as store:
        ingestor = MemoryIngestor(store)
        ingestor.ingest(
            SOURCE_VISUAL,
            [visual_message(n, 7, f"第{n}条 消息", observed_at=BASE + 10 * n, sequence=n) for n in range(1, 11)],
            conversations=[conversation(7, "项目组")],
            coverage=[CoverageRecord(source=SOURCE_VISUAL, status=COVERAGE_COMPLETE,
                                     window_start=BASE, window_end=BASE + 1_000)],
            now=1_000.0,
        )
    return tmp_path / "memory.sqlite"


@pytest.fixture()
def served(tmp_path, monkeypatch):
    path = populate(tmp_path)
    monkeypatch.setenv(consent.MEMORY_ENABLED_ENV, "1")
    monkeypatch.setenv(consent.MEMORY_DB_PATH_ENV, str(path))
    monkeypatch.setattr(consent, "read_app_consent_state_macos", lambda: app_state(True, generation=2))
    monkeypatch.setattr(server, "resolve_consent", lambda: consent.resolve_consent(None, lambda: app_state(True, generation=2)))
    return path


# --- surface -------------------------------------------------------------------


def test_exactly_five_read_only_tools_are_declared():
    names = {t if isinstance(t, str) else getattr(t, "name", None)
             for t in _declared_tool_names()}
    assert names == set(server.TOOL_NAMES)
    assert names == {"memory_search", "memory_timeline", "memory_context", "memory_recent",
                     "memory_conversations"}


def _declared_tool_names():
    mcp = server.mcp
    for attr in ("_tool_manager", "tool_manager"):
        manager = getattr(mcp, attr, None)
        if manager is not None:
            tools = manager.list_tools() if hasattr(manager, "list_tools") else manager._tools.values()
            return [t.name for t in tools]
    return [t.name for t in asyncio.run(mcp.list_tools())]


def test_no_write_sync_or_link_tool_exists():
    for name in _declared_tool_names():
        assert not any(word in name for word in ("sync", "ingest", "link", "delete", "update", "write", "set"))


def test_the_server_module_does_not_import_the_ingestor_or_sync():
    import ast
    tree = ast.parse(Path(server.__file__).read_text(encoding="utf-8"))
    imported = {
        (n.module or "") if isinstance(n, ast.ImportFrom) else n.names[0].name
        for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
    }
    assert "memory_ingest" not in imported and "memory_sync" not in imported


# --- envelope and citation -------------------------------------------------------


def test_every_tool_returns_the_envelope(served):
    responses = [
        call(server.memory_search, text="消息"),
        call(server.memory_timeline, conversation_id=CONV),
        call(server.memory_context, message_id=vid(5)),
        call(server.memory_recent),
    ]
    for response in responses:
        assert response["ok"] is True
        assert {"items", "coverage", "truncated", "query_scope", "focal_canonical_id"} <= set(response)
        for item in response["items"]:
            assert set(item["citation"]) == {
                "canonical_message_id", "logical_message_id", "canonical_conversation_id",
                "source", "source_message_id", "identity_mode", "timestamp", "timestamp_kind",
            }


def test_coverage_on_wire_carries_what_a_reader_needs(served):
    coverage = call(server.memory_search, text="消息", start=BASE, end=BASE + 200)["coverage"]
    assert set(coverage) == {"status", "trustworthy_empty", "required_sources",
                             "supplemental_sources", "complete_sources", "per_source", "caveats"}
    assert coverage["per_source"] == {SOURCE_VISUAL: {"status": COVERAGE_COMPLETE, "reasons": []}}
    assert coverage["required_sources"] == [SOURCE_VISUAL]


def test_trustworthy_empty_vs_incomplete_empty_on_wire(served):
    inside = call(server.memory_search, text="不存在", start=BASE, end=BASE + 100)
    outside = call(server.memory_search, text="不存在", start=BASE + 5_000, end=BASE + 6_000)
    assert inside["items"] == [] and inside["coverage"]["trustworthy_empty"] is True
    assert outside["items"] == [] and outside["coverage"]["trustworthy_empty"] is False
    assert outside["coverage"]["status"] == "not_observed"


def test_the_policy_is_reported_but_not_accepted(served):
    scope = call(server.memory_search)["query_scope"]
    assert scope["policy"] == {"required_sources": [SOURCE_VISUAL], "supplemental_sources": []}
    import inspect
    for tool in (server.memory_search, server.memory_timeline, server.memory_context, server.memory_recent,
                 server.memory_conversations):
        parameters = inspect.signature(getattr(tool, "fn", tool)).parameters
        assert "policy" not in parameters and "source" not in parameters
        assert not any("path" in p or "sql" in p for p in parameters)


def test_logical_message_id_is_optional_metadata(served, tmp_path):
    with MemoryStore.open(granted(tmp_path)) as store:
        MemoryIngestor(store).ingest(SOURCE_DATABASE, [database_message(5, 7, "第5条 消息", created_at=BASE + 50)],
                                     coverage=[CoverageRecord(source=SOURCE_DATABASE, status=COVERAGE_COMPLETE)], now=2.0)
        logical = store.link_observation(kind=LINK_KIND_MESSAGE, observation_canonical_id=vid(5), logical_id=None,
                                         basis=LINK_OPERATOR, asserted_by="op", now=3.0)
        store.link_observation(kind=LINK_KIND_MESSAGE, observation_canonical_id=identity.message_canonical_id_from_source(SOURCE_DATABASE, "5"),
                               logical_id=logical, basis=LINK_OPERATOR, asserted_by="op", now=3.0)
    items = call(server.memory_search, text="第5条")["items"]
    assert len(items) == 1
    assert items[0]["logical_message_id"] == logical
    assert {o["citation"]["source"] for o in items[0]["observations"]} == {SOURCE_VISUAL, SOURCE_DATABASE}


# --- inputs ---------------------------------------------------------------------


@pytest.mark.parametrize("tool,kwargs", [
    ("memory_search", {"limit": "ten"}),
    ("memory_search", {"start": "yesterday"}),
    ("memory_search", {"order": "random"}),
    ("memory_search", {"order": "relevance"}),
    ("memory_search", {"start": 10.0, "end": 5.0}),
    ("memory_search", {"text": "x" * 501}),
    ("memory_timeline", {"conversation_id": ""}),
    ("memory_timeline", {"conversation_id": CONV, "after_cursor": "abc"}),
    ("memory_timeline", {"conversation_id": CONV, "after_cursor": {"timestamp": 1.0}}),
    ("memory_context", {"message_id": vid(1), "before": True}),
    ("memory_recent", {"order": "relevance"}),
    ("memory_recent", {"since": 9.0, "until": 1.0}),
])
def test_malformed_input_is_refused_with_a_fixed_state(served, tool, kwargs):
    response = call(getattr(server, tool), **kwargs)
    assert response["ok"] is False
    assert response["state"] == "invalid_argument"
    assert "Traceback" not in response["detail"]


def test_limits_are_capped_on_the_public_surface(served):
    assert len(call(server.memory_search, limit=10_000)["items"]) == 10
    assert call(server.memory_search, limit=10_000)["query_scope"]["limit"] == server.MAX_LIMIT
    context = call(server.memory_context, message_id=vid(5), before=10_000, after=10_000)
    assert context["query_scope"]["limit"] == 2 * server.MAX_CONTEXT_SIDE + 1


def test_context_around_an_unknown_message_is_a_fixed_refusal(served):
    response = call(server.memory_context, message_id="msg:nope")
    assert (response["ok"], response["state"]) == (False, "message_unknown")


def test_timeline_pages_with_wire_cursors(served):
    page1 = call(server.memory_timeline, conversation_id=CONV, limit=4)
    page2 = call(server.memory_timeline, conversation_id=CONV, limit=4, after_cursor=page1["items"][-1]["cursor"])
    ids = [i["citation"]["canonical_message_id"] for i in page1["items"] + page2["items"]]
    assert ids == [vid(n) for n in range(1, 9)]
    assert page1["truncated"] is True


# --- refusals: activation, consent, store ---------------------------------------


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv(consent.MEMORY_ENABLED_ENV, raising=False)
    monkeypatch.delenv(consent.MEMORY_DB_PATH_ENV, raising=False)
    response = call(server.memory_recent)
    assert (response["ok"], response["state"]) == (False, "memory_disabled")


def test_missing_db_path(monkeypatch):
    monkeypatch.setenv(consent.MEMORY_ENABLED_ENV, "1")
    monkeypatch.delenv(consent.MEMORY_DB_PATH_ENV, raising=False)
    assert call(server.memory_recent)["state"] == "memory_not_configured"


@pytest.mark.parametrize("state,expected", [
    (None, "consent_state_missing"),
    ({"version": 1}, "consent_state_malformed"),
    (app_state(False), "consent_withheld"),
])
def test_consent_refusals(tmp_path, monkeypatch, state, expected):
    path = populate(tmp_path)
    monkeypatch.setenv(consent.MEMORY_ENABLED_ENV, "1")
    monkeypatch.setenv(consent.MEMORY_DB_PATH_ENV, str(path))
    monkeypatch.setattr(server, "resolve_consent", lambda: consent.resolve_consent(None, lambda: state))
    assert call(server.memory_search, text="消息")["state"] == expected


def test_missing_store(tmp_path, monkeypatch):
    monkeypatch.setenv(consent.MEMORY_ENABLED_ENV, "1")
    monkeypatch.setenv(consent.MEMORY_DB_PATH_ENV, str(tmp_path / "absent.sqlite"))
    monkeypatch.setattr(server, "resolve_consent", lambda: consent.resolve_consent(None, lambda: app_state(True)))
    response = call(server.memory_recent)
    assert response["state"] == "memory_store_missing"
    assert not (tmp_path / "absent.sqlite").exists()  # a reader creates nothing


def test_corrupt_store(tmp_path, monkeypatch):
    path = tmp_path / "memory.sqlite"
    path.write_bytes(b"not a database at all" * 100)
    monkeypatch.setenv(consent.MEMORY_ENABLED_ENV, "1")
    monkeypatch.setenv(consent.MEMORY_DB_PATH_ENV, str(path))
    monkeypatch.setattr(server, "resolve_consent", lambda: consent.resolve_consent(None, lambda: app_state(True)))
    assert call(server.memory_recent)["state"] == "memory_store_unreadable"


def test_unknown_user_version(tmp_path, monkeypatch):
    path = tmp_path / "memory.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version = 7;")
    connection.commit(); connection.close()
    monkeypatch.setenv(consent.MEMORY_ENABLED_ENV, "1")
    monkeypatch.setenv(consent.MEMORY_DB_PATH_ENV, str(path))
    monkeypatch.setattr(server, "resolve_consent", lambda: consent.resolve_consent(None, lambda: app_state(True)))
    assert call(server.memory_recent)["state"] == "schema_unsupported"


def test_a_v1_store_is_not_migrated_by_the_reader(tmp_path, monkeypatch):
    """The reader refuses an older store rather than upgrading it."""
    path = tmp_path / "memory.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version = 1;")
    connection.commit(); connection.close()
    monkeypatch.setenv(consent.MEMORY_ENABLED_ENV, "1")
    monkeypatch.setenv(consent.MEMORY_DB_PATH_ENV, str(path))
    monkeypatch.setattr(server, "resolve_consent", lambda: consent.resolve_consent(None, lambda: app_state(True)))
    assert call(server.memory_recent)["state"] == "schema_unsupported"
    assert sqlite3.connect(path).execute("PRAGMA user_version;").fetchone()[0] == 1


def test_the_reader_cannot_write(served):
    store = MemoryStore.open_read_only(consent.resolve_consent(None, lambda: app_state(True)))
    with pytest.raises(sqlite3.OperationalError):
        store.connection.execute("DELETE FROM messages;")
    store.close()


def test_internal_failures_leak_nothing(served, monkeypatch, tmp_path):
    def explode(self, **kwargs):
        raise RuntimeError(f"secret path {tmp_path} and SELECT * FROM messages")
    monkeypatch.setattr(server.MemoryQueryService, "search", explode)
    response = call(server.memory_search, text="x")
    assert response == {"ok": False, "state": "internal_error",
                        "detail": "The memory server could not answer this request."}


def test_no_response_carries_a_path_or_host_identity(served, tmp_path):
    import getpass, socket
    for response in (call(server.memory_search, text="消息"), call(server.memory_recent),
                     call(server.memory_context, message_id="msg:nope")):
        blob = json.dumps(response, ensure_ascii=False)
        assert str(tmp_path) not in blob
        assert "sqlite" not in blob
        assert socket.gethostname() not in blob
        assert getpass.getuser() not in blob


# --- real process-boundary composition ---------------------------------------------


def _mcp_env(tmp_path, db_path, *, allow=True, with_state=True):
    """The child environment ClaudeRunner would give the memory server, plus a
    HOME whose preference plist is the app's consent state, and a PATH with no
    `defaults` tool so the server's plist fallback is what answers."""
    home = tmp_path / "home"
    prefs = home / "Library" / "Preferences"
    prefs.mkdir(parents=True, exist_ok=True)
    if with_state:
        (prefs / f"{consent.APP_PREFERENCE_DOMAIN}.plist").write_bytes(
            plistlib.dumps({consent.CONSENT_STATE_KEY: app_state(allow, generation=5)}))
    return {
        "HOME": str(home), "PATH": str(tmp_path / "nopath"), "LANG": "en_US.UTF-8",
        consent.MEMORY_ENABLED_ENV: "1", consent.MEMORY_DB_PATH_ENV: str(db_path),
        "PYTHONPATH": os.pathsep.join(sys.path),
    }


async def _session(env):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=sys.executable, args=[str(Path(server.__file__))], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            async def call_tool(name, **arguments):
                result = await session.call_tool(name, arguments)
                text = "".join(getattr(c, "text", "") for c in result.content)
                try:
                    payload = json.loads(text) if text else {}
                except json.JSONDecodeError:
                    # The SDK refused the call at schema validation, before
                    # the tool ran. Still a refusal; just not our envelope.
                    return {"ok": False, "state": "sdk_rejected", "is_error": bool(getattr(result, "is_error", getattr(result, "isError", False))), "text": text}
                if isinstance(payload, dict) and "ok" in payload:
                    return payload
                structured = getattr(result, "structuredContent", None)
                return structured.get("result", structured) if structured else payload
            return [t.name for t in tools.tools], await _exercise(call_tool)


async def _exercise(call_tool):
    out = {}
    out["search"] = await call_tool("memory_search", text="第5条")
    hit = out["search"]["items"][0]["citation"]["canonical_message_id"]
    out["context"] = await call_tool("memory_context", message_id=hit, before=1, after=1)
    out["timeline"] = await call_tool("memory_timeline", conversation_id=CONV, limit=3)
    out["recent"] = await call_tool("memory_recent", limit=2)
    out["empty_inside"] = await call_tool("memory_search", text="不存在", start=BASE, end=BASE + 100)
    out["empty_outside"] = await call_tool("memory_search", text="不存在", start=BASE + 5_000, end=BASE + 6_000)
    out["bad_type"] = await call_tool("memory_search", limit="ten")
    out["bad_value"] = await call_tool("memory_search", order="random")
    return out


def test_composition_over_a_real_memory_server_process(tmp_path):
    """ClaudeRunner-shaped env → real memory MCP process → synthetic store → M2."""
    db_path = populate(tmp_path)
    names, out = asyncio.run(_session(_mcp_env(tmp_path, db_path)))
    assert set(names) == set(server.TOOL_NAMES)
    assert [i["citation"]["canonical_message_id"] for i in out["search"]["items"]] == [vid(5)]
    assert [i["citation"]["canonical_message_id"] for i in out["context"]["items"]] == [vid(4), vid(5), vid(6)]
    assert out["context"]["focal_canonical_id"] == vid(5)
    assert [i["is_focal"] for i in out["context"]["items"]] == [False, True, False]
    assert [i["citation"]["canonical_message_id"] for i in out["timeline"]["items"]] == [vid(1), vid(2), vid(3)]
    assert out["timeline"]["truncated"] is True
    assert [i["citation"]["canonical_message_id"] for i in out["recent"]["items"]] == [vid(10), vid(9)]
    for key in ("search", "context", "timeline", "recent"):
        assert {"items", "coverage", "truncated", "query_scope"} <= set(out[key])
        assert out[key]["coverage"]["required_sources"] == [SOURCE_VISUAL]
    assert out["empty_inside"]["coverage"]["trustworthy_empty"] is True
    assert out["empty_outside"]["coverage"]["trustworthy_empty"] is False
    # A wrong type is stopped by the SDK's schema before the tool runs; a wrong
    # value reaches the tool and gets the fixed refusal. Neither is an answer.
    assert out["bad_type"]["ok"] is False and out["bad_type"]["is_error"] is True
    assert out["bad_value"] == {"ok": False, "state": "invalid_argument",
                                "detail": "order must be one of: oldest, recent, relevance."}
    blob = json.dumps(out, ensure_ascii=False)
    assert str(tmp_path) not in blob


def test_composition_refuses_without_the_app_state(tmp_path):
    """The tools are declared -- the surface is what the gate asserts -- and
    every one of them refuses, because the app has recorded no decision."""
    db_path = populate(tmp_path)
    _, out = asyncio.run(_session_first_call_only(_mcp_env(tmp_path, db_path, with_state=False)))
    assert (out["ok"], out["state"]) == (False, "consent_state_missing")


def test_composition_refuses_when_consent_is_withheld(tmp_path):
    db_path = populate(tmp_path)
    _, out = asyncio.run(_session_first_call_only(_mcp_env(tmp_path, db_path, allow=False)))
    assert out == {"ok": False, "state": "consent_withheld",
                   "detail": "Local persistence is off in WeChat Companion, so no message text may be written down."}


async def _session_first_call_only(env):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=sys.executable, args=[str(Path(server.__file__))], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("memory_search", {"text": "x"})
            text = "".join(getattr(c, "text", "") for c in result.content)
            return None, json.loads(text)
