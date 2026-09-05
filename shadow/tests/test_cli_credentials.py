"""Credential transport: ``--pass-env`` by name only; never on argv or in output."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

import wechat_shadow_run as cli
import subprocess

from agent_runner import (KEYCHAIN_ENV, KEYCHAIN_SERVICE, PASS_ENV_ALLOWED, ShadowError,
                          collect_pass_env, read_keychain_token, reject_secret_env_args,
                          run_shadow)

TOKEN = "sk-ant-oat01-FAKE-not-a-real-token-0123456789"


@pytest.fixture
def claude_args(tmp_path):
    skill = tmp_path / "SKILL.md"
    skill.write_text("# policy\n")
    return ["--agent-backend", "claude", "--isolated-home", str(tmp_path / "iso"),
            "--claude-bin", "/x/claude", "--python", "/x/python",
            "--bridge", "/x/bridge.py", "--db-path", str(tmp_path / "f.sqlite"),
            "--skill", str(skill)]


def test_pass_env_copies_allowlisted_names_from_the_parent_environment_only():
    env = {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN, "ANTHROPIC_BASE_URL": "http://attacker"}
    assert collect_pass_env(["CLAUDE_CODE_OAUTH_TOKEN"], env) == {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN}
    with pytest.raises(ShadowError, match="not an allowlisted"):
        collect_pass_env(["ANTHROPIC_BASE_URL"], env)
    with pytest.raises(ShadowError, match="not set in the parent"):
        collect_pass_env(["ANTHROPIC_API_KEY"], env)
    assert PASS_ENV_ALLOWED == {"CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"}


def test_env_flag_refuses_credentials_on_the_command_line():
    with pytest.raises(ShadowError, match="use --pass-env CLAUDE_CODE_OAUTH_TOKEN"):
        reject_secret_env_args(["CLAUDE_CODE_OAUTH_TOKEN=x"])
    with pytest.raises(ShadowError, match="ANTHROPIC_API_KEY"):
        reject_secret_env_args(["LANG=C", "ANTHROPIC_API_KEY=x"])
    reject_secret_env_args(["LANG=C"])  # harmless names pass


def test_cli_pass_env_reaches_the_child_env_but_never_argv(claude_args):
    ap = cli.build_parser()
    args = ap.parse_args(claude_args + ["--pass-env", "CLAUDE_CODE_OAUTH_TOKEN"])
    runner = cli.make_runner(args, ap, environ={"CLAUDE_CODE_OAUTH_TOKEN": TOKEN})
    assert runner.cfg.extra_env == {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN}
    assert runner.cfg.child_env("http://127.0.0.1:1")["CLAUDE_CODE_OAUTH_TOKEN"] == TOKEN
    assert TOKEN not in " ".join(runner.argv("boundary-probe"))
    assert TOKEN not in " ".join(claude_args)


def test_cli_env_with_a_credential_is_a_usage_error(claude_args, capsys):
    ap = cli.build_parser()
    args = ap.parse_args(claude_args + ["--env", "CLAUDE_CODE_OAUTH_TOKEN=" + TOKEN])
    with pytest.raises(SystemExit):
        cli.make_runner(args, ap, environ={})
    assert "use --pass-env" in capsys.readouterr().err


def test_cli_missing_credential_fails_closed(claude_args, capsys):
    ap = cli.build_parser()
    args = ap.parse_args(claude_args + ["--pass-env", "ANTHROPIC_API_KEY"])
    with pytest.raises(SystemExit):
        cli.make_runner(args, ap, environ={})
    assert "not set in the parent" in capsys.readouterr().err


def test_no_output_stream_or_state_file_ever_carries_the_token(claude_args, tmp_path):
    """Drive a full fake run with the token in the child env; grep everything."""
    from test_claude_runner import FakeProxy, FakeRun

    ap = cli.build_parser()
    args = ap.parse_args(claude_args + ["--pass-env", "CLAUDE_CODE_OAUTH_TOKEN"])
    runner = cli.make_runner(args, ap, environ={"CLAUDE_CODE_OAUTH_TOKEN": TOKEN})
    run = FakeRun()

    def factory(expected, **kw):
        proxy = FakeProxy(expected, **kw)
        run.proxy = proxy
        proxy.state.path = str(tmp_path / "state.json")
        return proxy

    runner._run, runner._proxy_factory = run, factory
    out, err = io.StringIO(), io.StringIO()
    # keep residue for inspection: replace cleanup with a no-op, then inspect
    runner.cleanup = lambda run_id: None
    status = run_shadow(runner, out=out, err=err, install_signals=False)
    assert status == 0
    run.proxy.state.save()
    blobs = [out.getvalue(), err.getvalue()]
    blobs += [p.read_text(errors="ignore") for p in Path(args.isolated_home).rglob("*") if p.is_file()]
    blobs.append((tmp_path / "state.json").read_text())
    assert not any(TOKEN in b for b in blobs)
    assert TOKEN not in json.dumps(run.proxy.state.snapshot())


# --- fixed keychain source ---------------------------------------------------

def fake_security(rc=0, stdout=TOKEN + "\n"):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, rc, stdout, "")
    run.calls = calls
    return run


def test_keychain_source_is_fixed_service_account_and_destination():
    run = fake_security()
    got = read_keychain_token(run=run, user="alice")
    assert got == {KEYCHAIN_ENV: TOKEN} and KEYCHAIN_ENV == "CLAUDE_CODE_OAUTH_TOKEN"
    assert run.calls == [["/usr/bin/security", "find-generic-password", "-a", "alice",
                          "-s", "wechat-shadow-claude-oauth", "-w"]]
    assert KEYCHAIN_SERVICE == "wechat-shadow-claude-oauth"


def test_keychain_failures_are_fixed_text_never_tool_output():
    with pytest.raises(ShadowError) as info:
        read_keychain_token(run=fake_security(rc=44, stdout="SECRET-LEAK"), user="a")
    assert "absent or access was denied" in str(info.value) and "SECRET-LEAK" not in str(info.value)
    with pytest.raises(ShadowError, match="is empty"):
        read_keychain_token(run=fake_security(stdout="\n"), user="a")

    def boom(argv, **kw):
        raise OSError("no such file: " + TOKEN)
    with pytest.raises(ShadowError) as info:
        read_keychain_token(run=boom, user="a")
    assert TOKEN not in str(info.value)


def test_cli_keychain_flag_is_claude_only_and_reaches_the_child_env(claude_args, monkeypatch, capsys):
    monkeypatch.setattr(cli, "read_keychain_token", lambda: {KEYCHAIN_ENV: TOKEN})
    ap = cli.build_parser()
    args = ap.parse_args(claude_args + ["--claude-oauth-from-keychain"])
    runner = cli.make_runner(args, ap, environ={})
    assert runner.cfg.extra_env == {KEYCHAIN_ENV: TOKEN}
    assert TOKEN not in " ".join(runner.argv("boundary-probe"))
    hermes = ["--agent-backend", "hermes", "--isolated-home", "/tmp/i", "--hermes-entry", "/x",
              "--python", "/x", "--hermes-home", "/tmp/h", "--project-dir", "/tmp/p",
              "--claude-oauth-from-keychain"]
    with pytest.raises(SystemExit):
        cli.make_runner(ap.parse_args(hermes), ap, environ={})
    assert "claude backend only" in capsys.readouterr().err


def test_credential_reference_is_dropped_after_the_digest_spawn(claude_args):
    from test_claude_runner import FakeProxy, FakeRun
    ap = cli.build_parser()
    args = ap.parse_args(claude_args + ["--pass-env", "CLAUDE_CODE_OAUTH_TOKEN"])
    runner = cli.make_runner(args, ap, environ={"CLAUDE_CODE_OAUTH_TOKEN": TOKEN})
    run = FakeRun()

    def factory(expected, **kw):
        run.proxy = FakeProxy(expected, **kw)
        return run.proxy
    runner._run, runner._proxy_factory = run, factory
    runner.preflight()
    runner.assert_tool_boundary()
    assert run.calls[0][1]["env"][KEYCHAIN_ENV] == TOKEN, "probe spawn carries it"
    runner.digest()
    assert run.calls[1][1]["env"][KEYCHAIN_ENV] == TOKEN, "digest spawn carries it"
    assert KEYCHAIN_ENV not in runner.cfg.extra_env, "dropped once the last spawn returned"
    assert KEYCHAIN_ENV not in runner.cfg.child_env("http://127.0.0.1:1")
