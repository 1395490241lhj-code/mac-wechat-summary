"""Memory MCP activation from the agent path (M2.1).

Off by default and byte-for-byte the four-tool run when off; exactly eight
tools when on; refuses to start on any memory request it cannot honour. No
test here reads the real preference domain or launches a real CLI.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from agent_runner import ShadowError, check_tool_boundary
from runners.claude import (
    CLAUDE_EXPECTED_TOOLS,
    CLAUDE_EXPECTED_TOOLS_WITH_MEMORY,
    CLAUDE_MEMORY_TOOLS,
    MEMORY_DB_PATH_ENV,
    MEMORY_ENABLED_ENV,
    MEMORY_SERVER,
    MEMORY_TOOLS,
    ClaudeConfig,
    ClaudeRunner,
    claude_wire_name,
)
from wechat_shadow_run import build_parser, make_runner

ROOT = Path(__file__).resolve().parents[2]
MEMORY_DIR = ROOT / "memory"
SERVER = MEMORY_DIR / "wechat_memory_mcp.py"
SKILL_BYTES = b"# skill\n"


def app_state(allowed=True):
    return {"version": 1, "allowsLocalMessageStorage": allowed, "allowsMemoryStorage": allowed,
            "generation": 1, "updatedAt": 1.0}


def memory_store(tmp_path, *, version=2, name="memory.sqlite"):
    """A minimal file the read-only gate accepts: v2 tables, no rows."""
    sys.path.insert(0, str(MEMORY_DIR)); sys.path.insert(0, str(ROOT / "bridge"))
    try:
        import memory_consent
        from memory_store import MemoryStore
        decision = memory_consent.resolve_consent(
            {MEMORY_ENABLED_ENV: "1", MEMORY_DB_PATH_ENV: str(tmp_path / name)}, lambda: app_state(True))
        MemoryStore.open(decision).close()
    finally:
        for key in ("memory_consent", "memory_store", "memory_identity"):
            sys.modules.pop(key, None)
        sys.path.remove(str(MEMORY_DIR)); sys.path.remove(str(ROOT / "bridge"))
    if version != 2:
        c = sqlite3.connect(tmp_path / name); c.execute(f"PRAGMA user_version = {version};"); c.commit(); c.close()
    return tmp_path / name


def cfg(tmp_path, **memory):
    skill = tmp_path / "repo" / ".hermes" / "skills" / "wechat-digest" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_bytes(SKILL_BYTES)
    return ClaudeConfig(
        claude_bin=tmp_path / "bin" / "claude", python=tmp_path / "venv" / "python",
        bridge=tmp_path / "repo" / "bridge" / "wechat_companion_mcp.py",
        db_path=tmp_path / "fixture.sqlite", skill=skill,
        isolated_home=tmp_path / "isolated", proxy_port=18823,
        extra_env={"ANTHROPIC_API_KEY": "k"},
        memory_consent_reader=lambda: app_state(True),
        **memory,
    )


# --- default: unchanged ----------------------------------------------------------


def test_default_is_exactly_the_four_tool_run(tmp_path):
    c = cfg(tmp_path)
    assert c.memory_enabled is False
    assert c.expected_tools == CLAUDE_EXPECTED_TOOLS
    assert len(c.expected_tools) == 4
    document = c.mcp_config_document()
    assert list(document["mcpServers"]) == ["wechat_companion"]
    assert not any(k.startswith("WECHAT_COMPANION_MEMORY") for k in document["mcpServers"]["wechat_companion"]["env"])
    runner = ClaudeRunner(c)
    assert runner.expected_tools == CLAUDE_EXPECTED_TOOLS
    assert set(runner.argv("p")[runner.argv("p").index("--allowedTools") + 1].split(",")) == set(CLAUDE_EXPECTED_TOOLS)


def test_default_argv_and_env_are_unchanged_by_the_memory_feature(tmp_path):
    """The whole child contract is identical to the pre-M2.1 one."""
    c = cfg(tmp_path)
    runner = ClaudeRunner(c)
    argv = runner.argv("prompt")
    assert "--memory" not in " ".join(argv)
    env = c.child_env("http://127.0.0.1:1")
    assert not any("MEMORY" in k for k in env)
    assert json.dumps(c.mcp_config_document()) == json.dumps({"mcpServers": {"wechat_companion": {
        "type": "stdio", "command": str(c.python), "args": [str(c.bridge)],
        "env": {"WECHAT_COMPANION_ALLOW_AGENT_READ": "1", "WECHAT_COMPANION_DB_PATH": str(c.db_path)}}}})


def test_memory_tools_on_the_wire_in_default_mode_are_refused():
    with pytest.raises(ShadowError, match="unexpected"):
        check_tool_boundary(set(CLAUDE_EXPECTED_TOOLS_WITH_MEMORY), CLAUDE_EXPECTED_TOOLS)


# --- enabled: exactly eight --------------------------------------------------------


def test_enabled_is_exactly_eight_tools(tmp_path):
    store = memory_store(tmp_path)
    c = cfg(tmp_path, memory_enabled=True, memory_db_path=store, memory_server=SERVER)
    assert c.expected_tools == CLAUDE_EXPECTED_TOOLS_WITH_MEMORY
    assert len(c.expected_tools) == 8
    assert CLAUDE_EXPECTED_TOOLS_WITH_MEMORY == CLAUDE_EXPECTED_TOOLS | CLAUDE_MEMORY_TOOLS
    assert CLAUDE_MEMORY_TOOLS == {claude_wire_name(MEMORY_SERVER, t) for t in MEMORY_TOOLS}
    assert MEMORY_TOOLS == ("memory_search", "memory_timeline", "memory_context", "memory_recent")


def test_enabled_config_names_both_servers_and_isolates_the_memory_path(tmp_path):
    store = memory_store(tmp_path)
    c = cfg(tmp_path, memory_enabled=True, memory_db_path=store, memory_server=SERVER)
    document = c.mcp_config_document()
    assert set(document["mcpServers"]) == {"wechat_companion", MEMORY_SERVER}
    memory = document["mcpServers"][MEMORY_SERVER]
    assert memory["args"] == [str(SERVER)]
    assert memory["env"] == {MEMORY_ENABLED_ENV: "1", MEMORY_DB_PATH_ENV: str(store)}
    bridge = document["mcpServers"]["wechat_companion"]["env"]
    assert MEMORY_DB_PATH_ENV not in bridge and MEMORY_ENABLED_ENV not in bridge
    child = c.child_env("http://127.0.0.1:1")
    assert str(store) not in json.dumps(child)
    assert not any("MEMORY" in k for k in child)


def test_enabled_allowed_tools_are_exactly_eight(tmp_path):
    store = memory_store(tmp_path)
    runner = ClaudeRunner(cfg(tmp_path, memory_enabled=True, memory_db_path=store, memory_server=SERVER))
    argv = runner.argv("p")
    allowed = set(argv[argv.index("--allowedTools") + 1].split(","))
    assert allowed == CLAUDE_EXPECTED_TOOLS_WITH_MEMORY and len(allowed) == 8
    assert argv[argv.index("--tools") + 1] == ""


@pytest.mark.parametrize("extra", [{"Bash"}, {"Read"}, {"WebFetch"}, {"ListMcpResourcesTool"}, {"Task"},
                                   {"mcp__wechat_memory__memory_sync"}, {"mcp__wechat_memory__memory_link"}])
def test_enabled_gate_refuses_any_ninth_tool(extra):
    with pytest.raises(ShadowError, match="unexpected"):
        check_tool_boundary(set(CLAUDE_EXPECTED_TOOLS_WITH_MEMORY) | extra, CLAUDE_EXPECTED_TOOLS_WITH_MEMORY)


def test_enabled_gate_refuses_a_missing_memory_tool():
    with pytest.raises(ShadowError, match="absent"):
        check_tool_boundary(set(CLAUDE_EXPECTED_TOOLS), CLAUDE_EXPECTED_TOOLS_WITH_MEMORY)


# --- fail closed before the run ----------------------------------------------------


def test_memory_flags_without_opt_in_refuse(tmp_path):
    store = memory_store(tmp_path)
    with pytest.raises(ShadowError, match="require --memory"):
        cfg(tmp_path, memory_db_path=store).mcp_config_document()
    with pytest.raises(ShadowError, match="require --memory"):
        cfg(tmp_path, memory_server=SERVER).mcp_config_document()


def test_missing_db_path_refuses(tmp_path):
    with pytest.raises(ShadowError, match="explicit memory database path"):
        cfg(tmp_path, memory_enabled=True, memory_server=SERVER).memory_env()


def test_missing_server_refuses(tmp_path):
    store = memory_store(tmp_path)
    with pytest.raises(ShadowError, match="server script"):
        cfg(tmp_path, memory_enabled=True, memory_db_path=store).memory_env()
    with pytest.raises(ShadowError, match="server script"):
        cfg(tmp_path, memory_enabled=True, memory_db_path=store, memory_server=tmp_path / "nope.py").memory_env()


@pytest.mark.parametrize("state,expected", [
    (None, "consent_state_missing"), ({"version": 1}, "consent_state_malformed"), (app_state(False), "consent_withheld"),
])
def test_consent_refusals_stop_the_run(tmp_path, state, expected):
    store = memory_store(tmp_path)
    c = cfg(tmp_path, memory_enabled=True, memory_db_path=store, memory_server=SERVER)
    c.memory_consent_reader = lambda: state
    with pytest.raises(ShadowError, match=expected):
        c.memory_env()


def test_missing_store_refuses(tmp_path):
    with pytest.raises(ShadowError, match="memory_store_missing"):
        cfg(tmp_path, memory_enabled=True, memory_db_path=tmp_path / "absent.sqlite", memory_server=SERVER).memory_env()


def test_unknown_user_version_refuses(tmp_path):
    store = memory_store(tmp_path, version=9)
    with pytest.raises(ShadowError, match="schema_unsupported"):
        cfg(tmp_path, memory_enabled=True, memory_db_path=store, memory_server=SERVER).memory_env()


def test_corrupt_store_refuses(tmp_path):
    store = tmp_path / "memory.sqlite"
    store.write_bytes(b"garbage" * 200)
    with pytest.raises(ShadowError, match="memory_store_unreadable"):
        cfg(tmp_path, memory_enabled=True, memory_db_path=store, memory_server=SERVER).memory_env()


def test_a_refusal_never_falls_back_to_four_tools(tmp_path):
    """The boundary probe never runs: _prepare raises before any spawn."""
    c = cfg(tmp_path, memory_enabled=True, memory_db_path=tmp_path / "absent.sqlite", memory_server=SERVER)
    runner = ClaudeRunner(c, run=lambda *a, **k: pytest.fail("the CLI must not be spawned"),
                          proxy_factory=lambda *a, **k: pytest.fail("the proxy must not start"))
    with pytest.raises(ShadowError, match="memory"):
        runner.assert_tool_boundary()
    assert not c.mcp_config.exists()


def test_refusal_messages_carry_no_path(tmp_path):
    for build in (
        lambda: cfg(tmp_path, memory_enabled=True, memory_db_path=tmp_path / "absent.sqlite", memory_server=SERVER),
        lambda: cfg(tmp_path, memory_enabled=True, memory_db_path=memory_store(tmp_path), memory_server=tmp_path / "nope.py"),
    ):
        with pytest.raises(ShadowError) as raised:
            build().memory_env()
        assert str(tmp_path) not in str(raised.value)


# --- CLI ---------------------------------------------------------------------------


def base_args(tmp_path):
    return ["--agent-backend", "claude", "--isolated-home", str(tmp_path / "iso"),
            "--claude-bin", "/bin/echo", "--python", sys.executable,
            "--bridge", str(tmp_path / "bridge.py"), "--db-path", str(tmp_path / "db.sqlite"),
            "--skill", str(tmp_path / "SKILL.md")]


def test_cli_default_has_no_memory(tmp_path):
    (tmp_path / "SKILL.md").write_bytes(SKILL_BYTES)
    ap = build_parser()
    runner = make_runner(ap.parse_args(base_args(tmp_path)), ap)
    assert runner.cfg.memory_enabled is False and runner.expected_tools == CLAUDE_EXPECTED_TOOLS


def test_cli_memory_flags_require_the_opt_in(tmp_path, capsys):
    (tmp_path / "SKILL.md").write_bytes(SKILL_BYTES)
    ap = build_parser()
    with pytest.raises(SystemExit):
        make_runner(ap.parse_args(base_args(tmp_path) + ["--memory-db-path", str(tmp_path / "m.sqlite")]), ap)
    assert "require --memory" in capsys.readouterr().err


def test_cli_memory_on_hermes_is_refused(tmp_path, capsys):
    ap = build_parser()
    args = ap.parse_args(["--agent-backend", "hermes", "--isolated-home", str(tmp_path), "--memory"])
    with pytest.raises(SystemExit):
        make_runner(args, ap)
    assert "claude backend only" in capsys.readouterr().err


def test_cli_memory_refuses_before_the_run_when_unconsented(tmp_path, capsys, monkeypatch):
    """The CLI has no injected reader, so the real gate runs; with no store the
    store check cannot even be reached, and the refusal is a fixed token."""
    (tmp_path / "SKILL.md").write_bytes(SKILL_BYTES)
    ap = build_parser()
    args = ap.parse_args(base_args(tmp_path) + ["--memory", "--memory-db-path", str(tmp_path / "absent.sqlite"),
                                                "--memory-server", str(SERVER)])
    monkeypatch.setenv("HOME", str(tmp_path / "home")); monkeypatch.setenv("PATH", str(tmp_path / "nopath"))
    with pytest.raises(SystemExit):
        make_runner(args, ap)
    err = capsys.readouterr().err
    assert "memory: refused before the run (consent_state_missing)" in err
    assert str(tmp_path) not in err


# --- drift ---------------------------------------------------------------------------


def load_by_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


def test_runner_constants_match_the_memory_layer():
    consent = load_by_path("memory_consent", MEMORY_DIR / "memory_consent.py")
    assert (consent.MEMORY_ENABLED_ENV, consent.MEMORY_DB_PATH_ENV) == (MEMORY_ENABLED_ENV, MEMORY_DB_PATH_ENV)
    text = SERVER.read_text(encoding="utf-8")
    assert f'SERVER_NAME: Final = "{MEMORY_SERVER}"' in text
    for tool in MEMORY_TOOLS:
        assert f"def {tool}(" in text
    assert text.count("@mcp.tool(") == 4
