"""ClaudeRunner: argv, env-from-scratch, wire-observed boundary, cleanup.

No Claude Code runtime, no network. ``subprocess.run`` and the proxy are
replaced by fakes; the fake proxy is fed whatever "tools on the wire" a test
wants the runner to observe.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
from pathlib import Path

import pytest

from agent_runner import PROMPT, ShadowError, run_shadow
from runners.claude import (BRIDGE_TOOLS, CLAUDE_EXPECTED_TOOLS, PROBE_PROMPT,
                            ClaudeConfig, ClaudeRunner, claude_wire_name)
from tool_boundary_proxy import Assertion

SKILL_BYTES = "---\nname: wechat-digest\n---\n# WeChat Digest\n规则…\n".encode("utf-8")


class FakeProxy:
    """Stands in for ToolBoundaryProxy; the test decides what the wire shows."""

    instances: list["FakeProxy"] = []

    def __init__(self, expected, *, port, state_path=None, mode="enforce", **_):
        self.state = Assertion(expected, None)
        self.mode = mode
        self.port = port
        self.started = self.stopped = False
        self.modes_seen: list[str] = []
        FakeProxy.instances.append(self)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def start(self):
        self.started = True
        return self

    def stop(self):
        self.stopped = True


class FakeRun:
    """Answers the probe by 'showing' tools on the proxy, and the digest with JSON."""

    def __init__(self, wire_tools=None, result="微信摘要\n…", is_error=False, rc=0,
                 timeout_on_digest=False, raw_stdout=None, side_effect=None):
        self.wire_tools = set(CLAUDE_EXPECTED_TOOLS if wire_tools is None else wire_tools)
        self.result, self.is_error, self.rc = result, is_error, rc
        self.timeout_on_digest, self.raw_stdout = timeout_on_digest, raw_stdout
        self.side_effect = side_effect
        self.calls: list[tuple[list[str], dict]] = []
        self.proxy: FakeProxy | None = None

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        assert self.proxy is not None
        self.proxy.modes_seen.append(self.proxy.mode)
        if self.side_effect:
            self.side_effect(argv, kw)
        if argv[-1] == PROBE_PROMPT:
            if self.wire_tools:
                self.proxy.state.check(self.wire_tools)
            return subprocess.CompletedProcess(argv, 1, json.dumps({"is_error": True}), "")
        if self.timeout_on_digest:
            raise subprocess.TimeoutExpired(argv, kw["timeout"])
        # the digest turn carries tools too
        self.proxy.state.check(self.wire_tools)
        stdout = self.raw_stdout if self.raw_stdout is not None else json.dumps({
            "result": self.result, "session_id": "sess-1", "is_error": self.is_error})
        return subprocess.CompletedProcess(argv, self.rc, stdout, "")


@pytest.fixture
def cfg(tmp_path):
    skill = tmp_path / "repo" / ".hermes" / "skills" / "wechat-digest" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_bytes(SKILL_BYTES)
    return ClaudeConfig(
        claude_bin=tmp_path / "bin" / "claude", python=tmp_path / "venv" / "python",
        bridge=tmp_path / "repo" / "bridge" / "wechat_companion_mcp.py",
        db_path=tmp_path / "fixture.sqlite", skill=skill,
        isolated_home=tmp_path / "isolated", proxy_port=18823,
        extra_env={"ANTHROPIC_API_KEY": "k"},
    )


@pytest.fixture
def runner_with(cfg):
    FakeProxy.instances.clear()

    def make(run: FakeRun):
        def factory(expected, **kw):
            proxy = FakeProxy(expected, **kw)
            run.proxy = proxy
            return proxy
        return ClaudeRunner(cfg, run=run, proxy_factory=factory, err=io.StringIO())
    return make


# --- argv --------------------------------------------------------------------

def test_argv_is_the_narrowest_supported_configuration(cfg, runner_with):
    run = FakeRun()
    runner = runner_with(run)
    runner.assert_tool_boundary()
    argv, kw = run.calls[0]

    def opt(name):
        return argv[argv.index(name) + 1]

    assert argv[:2] == [str(cfg.claude_bin), "-p"]
    assert opt("--tools") == "", "no built-in tool at all"
    assert "--strict-mcp-config" in argv and opt("--mcp-config") == str(cfg.mcp_config)
    assert opt("--permission-mode") == "dontAsk"
    assert "--no-session-persistence" in argv
    assert opt("--setting-sources") == ""
    assert json.loads(opt("--settings")) == {"autoMemoryEnabled": False}
    assert "--disable-slash-commands" in argv
    assert "--bare" not in argv, "bare mode would exclude subscription OAuth"
    assert opt("--output-format") == "json"
    assert opt("--model") == "claude-sonnet-5"
    assert set(opt("--allowedTools").split(",")) == set(CLAUDE_EXPECTED_TOOLS)
    assert argv[-1] == PROBE_PROMPT
    assert kw["cwd"] == str(cfg.workdir) and kw["timeout"] == 900
    assert kw["capture_output"] is True and kw["text"] is True
    assert kw["stdin"] == subprocess.DEVNULL
    for forbidden in ("--dangerously-skip-permissions", "--allow-dangerously-skip-permissions",
                      "--add-dir", "--resume", "--continue", "bypassPermissions"):
        assert forbidden not in argv


def test_system_prompt_is_skill_md_verbatim_and_skill_is_never_rewritten(cfg, runner_with):
    before = hashlib.sha256(cfg.skill.read_bytes()).hexdigest()
    run = FakeRun()
    runner = runner_with(run)
    runner.assert_tool_boundary()
    runner.digest()
    for argv, _ in run.calls:
        assert argv[argv.index("--system-prompt") + 1].encode("utf-8") == SKILL_BYTES
    assert hashlib.sha256(cfg.skill.read_bytes()).hexdigest() == before
    # nothing under the isolated home is a copy of the skill
    for path in cfg.isolated_home.rglob("*"):
        if path.is_file():
            assert path.read_bytes() != SKILL_BYTES


def test_digest_prompt_is_the_shared_prompt(cfg, runner_with):
    run = FakeRun()
    runner = runner_with(run)
    runner.assert_tool_boundary()
    runner.digest()
    assert run.calls[-1][0][-1] == PROMPT


# --- environment -------------------------------------------------------------

def test_child_env_is_built_from_scratch_and_routes_through_the_proxy(cfg, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://attacker")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/real/.claude")
    env = cfg.child_env("http://127.0.0.1:18823")
    assert env["HOME"] == str(cfg.isolated_home)
    assert env["CLAUDE_CONFIG_DIR"] == str(cfg.isolated_home / ".claude")
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:18823"
    assert env["PATH"] == "/usr/bin:/bin:/usr/sbin:/sbin"
    assert env["DISABLE_TELEMETRY"] == "1" and env["DISABLE_AUTOUPDATER"] == "1"
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    for knob in ("CLAUDE_CODE_DISABLE_CLAUDE_MDS", "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS",
                 "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", "CLAUDE_CODE_DISABLE_WORKFLOWS"):
        assert env[knob] == "1"
    assert env["ANTHROPIC_API_KEY"] == "k"
    assert "CLAUDECODE" not in env
    assert os.environ["CLAUDE_CONFIG_DIR"] == "/real/.claude"


def test_mcp_config_names_exactly_one_server_with_both_opt_ins(cfg, runner_with):
    runner = runner_with(FakeRun())
    runner.assert_tool_boundary()
    doc = json.loads(cfg.mcp_config.read_text())
    assert list(doc) == ["mcpServers"] and list(doc["mcpServers"]) == ["wechat_companion"]
    server = doc["mcpServers"]["wechat_companion"]
    assert server["command"] == str(cfg.python) and server["args"] == [str(cfg.bridge)]
    assert server["env"] == {"WECHAT_COMPANION_ALLOW_AGENT_READ": "1",
                             "WECHAT_COMPANION_DB_PATH": str(cfg.db_path)}


# --- tool boundary, observed on the wire -------------------------------------

def test_expected_set_is_derived_from_the_bridges_tools_only():
    assert CLAUDE_EXPECTED_TOOLS == {claude_wire_name("wechat_companion", t) for t in BRIDGE_TOOLS}
    assert len(CLAUDE_EXPECTED_TOOLS) == 4


def test_probe_runs_in_probe_mode_then_digest_in_enforce_mode(cfg, runner_with):
    run = FakeRun()
    runner = runner_with(run)
    tools = runner.assert_tool_boundary()
    assert tools == set(CLAUDE_EXPECTED_TOOLS)
    runner.digest()
    assert run.proxy.modes_seen == ["probe", "enforce"]
    assert run.proxy.started and run.proxy.state.tools_bearing == 1, "tally reset after probe"
    assert run.calls[0][0][-1] == PROBE_PROMPT and run.calls[1][0][-1] == PROMPT


@pytest.mark.parametrize("wire,fragment", [
    (CLAUDE_EXPECTED_TOOLS | {"Bash"}, "MUTATING: \\['Bash'\\]"),
    (CLAUDE_EXPECTED_TOOLS | {"ListMcpResourcesTool"}, "unexpected"),
    (CLAUDE_EXPECTED_TOOLS - {"mcp__wechat_companion__status"}, "absent"),
    ({"wechat_companion__status"}, "unexpected"),
])
def test_wire_mismatch_fails_closed_before_any_digest_turn(cfg, runner_with, wire, fragment):
    run = FakeRun(wire_tools=wire)
    runner = runner_with(run)
    with pytest.raises(ShadowError, match=fragment):
        runner.assert_tool_boundary()
    assert len(run.calls) == 1 and run.calls[0][0][-1] == PROBE_PROMPT


def test_no_tools_bearing_request_is_not_a_pass(cfg, runner_with):
    runner = runner_with(FakeRun(wire_tools=set()))
    with pytest.raises(ShadowError, match="never sent a tools-bearing request"):
        runner.assert_tool_boundary()


def test_violation_during_digest_aborts(cfg, runner_with):
    run = FakeRun()
    runner = runner_with(run)
    runner.assert_tool_boundary()
    run.wire_tools = CLAUDE_EXPECTED_TOOLS | {"WebFetch"}
    with pytest.raises(ShadowError, match="violating request"):
        runner.digest()


def test_proxy_port_in_use_is_a_boundary_abort(cfg):
    def factory(expected, **kw):
        raise OSError(48, "Address already in use")
    runner = ClaudeRunner(cfg, run=FakeRun(), proxy_factory=factory, err=io.StringIO())
    with pytest.raises(ShadowError, match="proxy could not listen"):
        runner.assert_tool_boundary()


# --- response parsing --------------------------------------------------------

def test_digest_parses_result_and_session_id(cfg, runner_with):
    runner = runner_with(FakeRun(result="微信摘要\n\n🔴 需要处理\n- x"))
    runner.assert_tool_boundary()
    out = runner.digest()
    assert out.text.startswith("微信摘要") and out.run_id == "sess-1" and out.returncode == 0


@pytest.mark.parametrize("run", [
    FakeRun(is_error=True), FakeRun(rc=1), FakeRun(raw_stdout="not json"),
])
def test_error_shapes_are_failed_runs_not_digests(cfg, runner_with, run):
    runner = runner_with(run)
    runner.assert_tool_boundary()
    out = runner.digest()
    assert out.returncode != 0 and out.text == ""


# --- timeout, persistence, cleanup ------------------------------------------

def test_timeout_is_reported_and_isolated_home_is_purged(cfg, runner_with):
    run = FakeRun(timeout_on_digest=True)
    runner = runner_with(run)
    err = io.StringIO()
    status = run_shadow(runner, out=io.StringIO(), err=err, install_signals=False)
    assert status == 1 and "timed out" in err.getvalue()
    assert run.proxy.stopped and runner.residue() == []


def test_cleanup_removes_everything_the_run_wrote_and_proves_it(cfg, runner_with):
    def write_like_claude(argv, kw):
        home = Path(kw["env"]["HOME"])
        (home / ".claude" / "debug").mkdir(parents=True, exist_ok=True)
        (home / ".claude" / "debug" / "x.log").write_text("names only")
        (home / ".claude" / ".claude.json").write_text("{}")
    run = FakeRun(side_effect=write_like_claude)
    runner = runner_with(run)
    status = run_shadow(runner, out=io.StringIO(), err=io.StringIO(), install_signals=False)
    assert status == 0
    assert cfg.isolated_home.is_dir() and runner.residue() == []
    assert not cfg.mcp_config.exists() and not cfg.proxy_state.exists()
    assert run.proxy.stopped


def test_preflight_recovers_residue_then_proves_clean(cfg, runner_with):
    (cfg.isolated_home / ".claude" / "projects").mkdir(parents=True)
    (cfg.isolated_home / ".claude" / "projects" / "left.jsonl").write_text("x")
    runner = runner_with(FakeRun())
    runner._err = io.StringIO()
    runner.preflight()
    assert "recovering 1 stale file(s)" in runner._err.getvalue()
    assert runner.residue() == []


def test_preflight_refuses_without_skill_file(cfg, runner_with):
    cfg.skill.unlink()
    with pytest.raises(ShadowError, match="SKILL.md not found"):
        runner_with(FakeRun()).preflight()


def test_full_run_exit_zero_prints_digest_only(cfg, runner_with):
    run = FakeRun(result="微信摘要\n…")
    out, err = io.StringIO(), io.StringIO()
    status = run_shadow(runner_with(run), out=out, err=err, install_signals=False)
    assert status == 0 and out.getvalue().strip() == "微信摘要\n…"
    assert "verified 4 read-only tools (claude)" in err.getvalue()


# --- diagnostics (sanitised) ------------------------------------------------

OBSERVED_ERROR_ENVELOPE = {  # shape emitted by Claude Code 2.1.238 on an API error
    "type": "result", "subtype": "success", "is_error": True, "duration_api_ms": 0,
    "num_turns": 1, "stop_reason": "stop_sequence", "session_id": "s-err",
    "total_cost_usd": 0, "terminal_reason": "api_error", "api_error_status": 401,
    "permission_denials": [], "result": "Not logged in · Please run /login",
}


def test_api_error_envelope_yields_sanitised_diagnostics(cfg, runner_with):
    runner = runner_with(FakeRun(raw_stdout=json.dumps(OBSERVED_ERROR_ENVELOPE), rc=1))
    runner.assert_tool_boundary()
    out = runner.digest()
    assert out.returncode == 1 and out.text == ""
    d = out.diagnostics
    assert d["is_error"] is True and d["terminal_reason"] == "api_error"
    assert d["api_error_status"] == 401 and d["num_turns"] == 1
    assert d["error_text"].startswith("Not logged in") and d["stdout_json"] is True
    assert d["proxy"]["tools_bearing"] == 1 and "upstream_status" in d["proxy"]
    assert "session_id" not in d


def test_success_diagnostics_never_carry_the_digest(cfg, runner_with):
    runner = runner_with(FakeRun(result="微信摘要\n🔴 需要处理\n- 秘密"))
    runner.assert_tool_boundary()
    out = runner.digest()
    assert out.text.startswith("微信摘要")
    assert "秘密" not in json.dumps(out.diagnostics, ensure_ascii=False)
    assert "error_text" not in out.diagnostics and "stderr_head" not in out.diagnostics


def test_driver_prints_diagnostics_on_failure(cfg, runner_with):
    runner = runner_with(FakeRun(raw_stdout=json.dumps(OBSERVED_ERROR_ENVELOPE), rc=1))
    err = io.StringIO()
    status = run_shadow(runner, out=io.StringIO(), err=err, install_signals=False)
    assert status == 1
    line = next(l for l in err.getvalue().splitlines() if l.startswith("run failed"))
    assert '"api_error_status": 401' in line and '"terminal_reason": "api_error"' in line
