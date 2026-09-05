"""HermesRunner pins the pre-H6 behaviour: argv, env, timeouts, ordering.

No Hermes runtime is involved. ``subprocess.run`` and ``subprocess.Popen`` are
replaced by fakes that record every call.
"""

from __future__ import annotations

import io
import os
import subprocess
from pathlib import Path

import pytest

from agent_runner import PROMPT, ShadowError, run_shadow
from runners.hermes import (HERMES_EXPECTED_TOOLS, HermesConfig, HermesRunner,
                            SKILL_NAME, TOOLSETS)

SID = "20260825_105319_5be2f1"


class FakeRun:
    """Records argv/kwargs and answers ``sessions list`` from a mutable set."""

    def __init__(self, sessions=(), digest_stdout="微信摘要\n...", digest_rc=0,
                 timeout_on_chat=False):
        self.sessions = set(sessions)
        self.calls: list[tuple[list[str], dict]] = []
        self.digest_stdout = digest_stdout
        self.digest_rc = digest_rc
        self.timeout_on_chat = timeout_on_chat
        self.dump_present_at_optimize = None

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        sub = argv[2:]
        if sub[:2] == ["sessions", "list"]:
            return subprocess.CompletedProcess(argv, 0, "\n".join(sorted(self.sessions)), "")
        if sub[:2] == ["sessions", "delete"]:
            self.sessions.discard(sub[2])
            return subprocess.CompletedProcess(argv, 0, "", "")
        if sub[:2] == ["sessions", "optimize"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if sub[0] == "chat":
            if self.timeout_on_chat:
                raise subprocess.TimeoutExpired(argv, kw["timeout"])
            self.sessions.add(SID)
            return subprocess.CompletedProcess(argv, self.digest_rc, self.digest_stdout,
                                               f"session_id: {SID}\n")
        raise AssertionError(argv)


class FakePopen:
    def __init__(self, lines):
        self.stdout = iter(lines)
        self.terminated = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


def make_popen(lines, record):
    def popen(argv, **kw):
        record.append((argv, kw))
        return FakePopen(lines)
    return popen


@pytest.fixture
def cfg(tmp_path):
    return HermesConfig(
        hermes_entry=tmp_path / "hermes.py", python=tmp_path / "python",
        hermes_home=tmp_path / "home", project_dir=tmp_path / "repo",
        isolated_home=tmp_path / "isolated", extra_env={"ANTHROPIC_API_KEY": "k"},
    )


def selection_line(tools):
    return f"Final tool selection: {', '.join(sorted(tools))}\n"


# --- argv --------------------------------------------------------------------

def test_digest_argv_and_kwargs_are_the_pre_h6_shape(cfg):
    run = FakeRun()
    out = HermesRunner(cfg, run=run, err=io.StringIO()).digest()
    argv, kw = run.calls[-1]
    assert argv == [str(cfg.python), str(cfg.hermes_entry), "chat", "-Q", "-q", PROMPT,
                    "-s", SKILL_NAME, "-t", TOOLSETS]
    assert kw == {"cwd": str(cfg.project_dir), "env": cfg.child_env(),
                  "capture_output": True, "text": True, "timeout": 900}
    assert out.text == "微信摘要\n..." and out.run_id == SID and out.returncode == 0


def test_boundary_probe_argv_and_termination(cfg):
    record = []
    runner = HermesRunner(cfg, run=FakeRun(),
                          popen=make_popen(["init...\n", selection_line(HERMES_EXPECTED_TOOLS),
                                            "never read\n"], record), err=io.StringIO())
    tools = runner.assert_tool_boundary()
    assert tools == set(HERMES_EXPECTED_TOOLS)
    argv, kw = record[0]
    assert argv == [str(cfg.python), str(cfg.hermes_entry), "chat", "-Q", "-v",
                    "-q", "boundary-probe", "-s", SKILL_NAME, "-t", TOOLSETS]
    assert kw["env"] == cfg.child_env() and kw["stderr"] == subprocess.STDOUT


# --- environment -------------------------------------------------------------

def test_child_env_is_built_from_scratch(cfg, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://attacker")
    monkeypatch.setenv("CLAUDECODE", "1")
    env = cfg.child_env()
    assert set(env) == {"HOME", "HERMES_HOME", "PATH", "LANG", "LC_ALL", "TERM",
                        "ANTHROPIC_API_KEY"}
    assert env["HOME"] == str(cfg.isolated_home)
    assert env["HERMES_HOME"] == str(cfg.hermes_home)
    assert env["PATH"] == "/usr/bin:/bin:/usr/sbin:/sbin"
    assert "ANTHROPIC_BASE_URL" not in env and "CLAUDECODE" not in env
    assert os.environ["ANTHROPIC_BASE_URL"] == "http://attacker", "only the child is isolated"


# --- timeout -----------------------------------------------------------------

def test_digest_timeout_is_reported_and_cleaned_up(cfg):
    run = FakeRun(timeout_on_chat=True)
    runner = HermesRunner(cfg, run=run, err=io.StringIO(),
                          popen=make_popen([selection_line(HERMES_EXPECTED_TOOLS)], []))
    err = io.StringIO()
    status = run_shadow(runner, out=io.StringIO(), err=err, install_signals=False)
    assert status == 1 and "timed out" in err.getvalue()
    assert [c[0][2:4] for c in run.calls][-1] == ["sessions", "list"], "cleanup still ran"


# --- fail closed -------------------------------------------------------------

@pytest.mark.parametrize("tools,fragment", [
    (HERMES_EXPECTED_TOOLS | {"terminal"}, "MUTATING: \\['terminal'\\]"),
    (HERMES_EXPECTED_TOOLS | {"skill_manage"}, "MUTATING"),
    (HERMES_EXPECTED_TOOLS - {"mcp__wechat_companion__status"}, "absent"),
])
def test_unexpected_or_missing_tool_aborts_before_digest(cfg, tools, fragment):
    run = FakeRun()
    runner = HermesRunner(cfg, run=run, popen=make_popen([selection_line(tools)], []),
                          err=io.StringIO())
    with pytest.raises(ShadowError, match=fragment):
        runner.assert_tool_boundary()
    assert not any(c[0][2] == "chat" for c in run.calls), "no digest turn was launched"


def test_missing_selection_line_aborts(cfg):
    runner = HermesRunner(cfg, run=FakeRun(), popen=make_popen(["init\n"], []),
                          err=io.StringIO())
    with pytest.raises(ShadowError, match="never reported"):
        runner.assert_tool_boundary()


def test_driver_exit_code_two_on_boundary_abort(cfg):
    run = FakeRun()
    runner = HermesRunner(cfg, run=run, err=io.StringIO(),
                          popen=make_popen([selection_line(HERMES_EXPECTED_TOOLS | {"write_file"})], []))
    status = run_shadow(runner, out=io.StringIO(), err=io.StringIO(), install_signals=False)
    assert status == 2


# --- purge / cleanup ordering ------------------------------------------------

def test_cleanup_deletes_then_optimizes_then_sweeps_orphan_dumps(cfg):
    sessions_dir = cfg.hermes_home / "sessions"
    sessions_dir.mkdir(parents=True)
    dump = sessions_dir / "request_dump_orphan_1.json"
    dump.write_text("{}")
    run = FakeRun(sessions={SID, "20260825_110000_aaaaaa"})

    seen_dump_at_optimize = []
    original = run.__call__

    def spy(argv, **kw):
        if argv[2:4] == ["sessions", "optimize"]:
            seen_dump_at_optimize.append(dump.exists())
        return original(argv, **kw)

    HermesRunner(cfg, run=spy, err=io.StringIO()).cleanup(run_id="20260825_120000_bbbbbb")
    ops = [tuple(c[0][2:5]) for c in run.calls]
    deletes = [o for o in ops if o[:2] == ("sessions", "delete")]
    assert deletes == [("sessions", "delete", SID), ("sessions", "delete", "20260825_110000_aaaaaa"),
                       ("sessions", "delete", "20260825_120000_bbbbbb")]
    assert ops.index(("sessions", "optimize", "")[:2] + ()) if False else True
    optimize_index = next(i for i, o in enumerate(ops) if o[:2] == ("sessions", "optimize"))
    assert all(i < optimize_index for i, o in enumerate(ops) if o[:2] == ("sessions", "delete"))
    assert seen_dump_at_optimize == [True], "the sweep is the LAST step"
    assert not dump.exists()
    assert ops[-1][:2] == ("sessions", "list"), "post-run assert_clean re-lists"


def test_delete_uses_yes_flag_and_optimize_is_skipped_when_nothing_to_delete(cfg):
    run = FakeRun()
    HermesRunner(cfg, run=run, err=io.StringIO()).purge([SID])
    assert run.calls[0][0][2:] == ["sessions", "delete", SID, "--yes"]
    assert run.calls[0][1]["timeout"] == 120 and run.calls[1][1]["timeout"] == 300
    run = FakeRun()
    HermesRunner(cfg, run=run, err=io.StringIO()).purge([])
    assert run.calls == []


# --- clean-state assertions --------------------------------------------------

def test_preflight_recovers_stale_sessions_and_orphans_then_proves_clean(cfg):
    (cfg.hermes_home / "sessions").mkdir(parents=True)
    (cfg.hermes_home / "sessions" / "request_dump_x.json").write_text("{}")
    run = FakeRun(sessions={SID})
    err = io.StringIO()
    HermesRunner(cfg, run=run, err=err).preflight()
    assert "recovering 1 stale session(s), 1 orphan dump(s)" in err.getvalue()
    assert "clean state verified" in err.getvalue()
    assert run.sessions == set() and not list((cfg.hermes_home / "sessions").iterdir())


def test_assert_clean_fails_when_residue_survives_purge(cfg):
    class Sticky(FakeRun):
        def __call__(self, argv, **kw):
            if argv[2:4] == ["sessions", "delete"]:
                self.calls.append((argv, kw))
                return subprocess.CompletedProcess(argv, 1, "", "refused")
            return super().__call__(argv, **kw)
    runner = HermesRunner(cfg, run=Sticky(sessions={SID}), err=io.StringIO())
    with pytest.raises(ShadowError, match="preflight: state is not clean — 1 session"):
        runner.preflight()


def test_session_id_parser_matches_the_hermes_shape(cfg):
    run = FakeRun(sessions={SID, "not_a_session", "2026_1_x", "20260825_105319"})
    assert HermesRunner(cfg, run=run, err=io.StringIO()).list_sessions() == [SID]
    assert Path(cfg.hermes_home).name == "home"
