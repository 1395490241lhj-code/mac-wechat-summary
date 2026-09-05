#!/usr/bin/env python3
"""Claude Code headless backend for the shadow digest — synthetic-only.

Runs ``claude -p`` (Claude Code 2.1.x) with the narrowest configuration the
installed CLI supports:

* ``--tools ""`` — no built-in tool (no Bash, no file read/write/edit, no web).
* ``--strict-mcp-config --mcp-config <isolated json>`` — exactly one MCP
  server, the read-only ``wechat_companion`` bridge; nothing from any user or
  project MCP configuration.
* ``--permission-mode dontAsk`` — no interactive prompt can ever appear; a tool
  that is not pre-allowed is denied, never asked about.
* ``--allowedTools`` — only the bridge's tools are pre-allowed.
* ``--no-session-persistence`` — the CLI is told not to save the session.
* ``--setting-sources ""`` and an empty working directory — no user or project
  settings, no ``CLAUDE.md``, no hooks, no plugins are discovered.
* ``--system-prompt <SKILL.md bytes>`` — the digest policy is the skill file,
  read verbatim at launch and passed as the whole system prompt. This build
  has no ``--system-prompt-file`` flag, so the bytes travel on argv; nothing is
  copied to disk, reformatted, or rewritten.

**None of that is trusted.** Claude Code prints no tool selection, so the
boundary is verified where it is real: on the wire. The runner starts a
``ToolBoundaryProxy`` in-process and points the CLI at it through
``ANTHROPIC_BASE_URL``. Before the digest, a *probe* turn is sent with the
proxy in ``probe`` mode: the first tools-bearing request is recorded and
rejected without being forwarded, so the actual ``tools[]`` array is observed
with zero provider traffic and no message read. Only if it equals the expected
set does the digest turn run, with the proxy in ``enforce`` mode for every
subsequent request.

**Persistence.** Everything the CLI may write is confined to one dedicated
directory: ``HOME``, ``CLAUDE_CONFIG_DIR`` and the working directory all
resolve inside ``isolated_home``. "Clean" for this backend means that
directory contains no files at all; preflight and post-run cleanup remove
its contents and prove that. Whether Claude Code writes chat content anywhere
inside it despite ``--no-session-persistence`` is the subject of the H6
canary audit, not an assumption made here.

Not enabled, by construction: any built-in tool, cron, unattended execution,
session resume, plugins, hooks, memory, skills discovery.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from agent_runner import DigestResult, PROMPT, ShadowError, check_tool_boundary
from tool_boundary_proxy import ToolBoundaryProxy

#: The bridge's own tool names, as it declares them. The server key and these
#: names are the only inputs to the expected wire set; the join convention is
#: Claude Code's documented ``mcp__<server>__<tool>`` and is *verified*, not
#: assumed — the proxy compares against what is actually sent.
BRIDGE_SERVER = "wechat_companion"
BRIDGE_TOOLS = ("status", "list_conversations", "get_messages", "get_recent_messages")

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_SKILL = Path(".hermes/skills/wechat-digest/SKILL.md")
PROBE_PROMPT = "boundary-probe"


def claude_wire_name(server: str, tool: str) -> str:
    return f"mcp__{server}__{tool}"


CLAUDE_EXPECTED_TOOLS = frozenset(claude_wire_name(BRIDGE_SERVER, t) for t in BRIDGE_TOOLS)


@dataclass
class ClaudeConfig:
    claude_bin: Path        # the `claude` executable to run
    python: Path            # interpreter that has the bridge's `mcp` dependency
    bridge: Path            # bridge/wechat_companion_mcp.py
    db_path: Path           # the SQLite store the bridge may read (synthetic only, for now)
    skill: Path             # SKILL.md — the single source of digest policy
    isolated_home: Path     # HOME + CLAUDE_CONFIG_DIR + cwd for the child; owned by the run
    model: str = DEFAULT_MODEL
    proxy_port: int = 8823
    extra_env: dict = field(default_factory=dict)
    timeout: int = 900

    @property
    def config_dir(self) -> Path:
        return self.isolated_home / ".claude"

    @property
    def workdir(self) -> Path:
        return self.isolated_home / "cwd"

    @property
    def mcp_config(self) -> Path:
        return self.isolated_home / "mcp.json"

    @property
    def proxy_state(self) -> Path:
        return self.isolated_home / "tool_boundary.json"

    def child_env(self, proxy_url: str) -> dict:
        """A minimal, explicit environment, built from scratch.

        Nothing from the operator's shell — credentials, base-URL overrides,
        Claude Code state, the parent Claude Code session — can reach the child.
        """
        env = {
            "HOME": str(self.isolated_home),
            "CLAUDE_CONFIG_DIR": str(self.config_dir),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "LANG": "en_US.UTF-8",
            "LC_ALL": "en_US.UTF-8",
            "TERM": "dumb",
            "ANTHROPIC_BASE_URL": proxy_url,
            "DISABLE_TELEMETRY": "1",
            "DISABLE_ERROR_REPORTING": "1",
            "DISABLE_AUTOUPDATER": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }
        env.update(self.extra_env)
        return env

    def mcp_config_document(self) -> dict:
        """Exactly one server. Its env carries the bridge's double opt-in."""
        return {"mcpServers": {BRIDGE_SERVER: {
            "type": "stdio",
            "command": str(self.python),
            "args": [str(self.bridge)],
            "env": {
                "WECHAT_COMPANION_ALLOW_AGENT_READ": "1",
                "WECHAT_COMPANION_DB_PATH": str(self.db_path),
            },
        }}}


class ClaudeRunner:
    """``AgentRunner`` over Claude Code headless.

    ``run`` defaults to ``subprocess.run`` and ``proxy_factory`` to
    ``ToolBoundaryProxy``; both are injectable so the tests can pin argv,
    environment, ordering and fail-closed behaviour without a runtime.
    """

    backend = "claude"
    expected_tools = CLAUDE_EXPECTED_TOOLS

    def __init__(self, cfg: ClaudeConfig, *, run: Callable = subprocess.run,
                 proxy_factory: Callable = ToolBoundaryProxy, err=sys.stderr) -> None:
        self.cfg = cfg
        self._run = run
        self._proxy_factory = proxy_factory
        self._err = err
        self._proxy: ToolBoundaryProxy | None = None

    # --- launch --------------------------------------------------------------

    def skill_text(self) -> str:
        """SKILL.md, verbatim. Never modified, never copied to disk."""
        return self.cfg.skill.read_text(encoding="utf-8")

    def argv(self, prompt: str) -> list[str]:
        cfg = self.cfg
        allowed = ",".join(sorted(self.expected_tools))
        return [
            str(cfg.claude_bin), "-p",
            "--tools", "",
            "--strict-mcp-config", "--mcp-config", str(cfg.mcp_config),
            "--allowedTools", allowed,
            "--permission-mode", "dontAsk",
            "--no-session-persistence",
            "--setting-sources", "",
            "--output-format", "json",
            "--model", cfg.model,
            "--system-prompt", self.skill_text(),
            prompt,
        ]

    def _prepare(self) -> None:
        cfg = self.cfg
        cfg.config_dir.mkdir(parents=True, exist_ok=True)
        cfg.workdir.mkdir(parents=True, exist_ok=True)
        cfg.mcp_config.write_text(json.dumps(cfg.mcp_config_document(), indent=1),
                                  encoding="utf-8")

    def _claude(self, prompt: str) -> subprocess.CompletedProcess:
        assert self._proxy is not None, "proxy must be running"
        cfg = self.cfg
        return self._run(
            self.argv(prompt), cwd=str(cfg.workdir),
            env=cfg.child_env(self._proxy.url),
            capture_output=True, text=True, timeout=cfg.timeout,
        )

    # --- persistence ---------------------------------------------------------

    def residue(self) -> list[Path]:
        """Every file under the isolated home. Names only; contents never read."""
        home = self.cfg.isolated_home
        return sorted(p for p in home.rglob("*") if p.is_file()) if home.is_dir() else []

    def purge(self) -> None:
        """Remove everything the run owns. The directory itself stays."""
        if self._proxy is not None:
            self._proxy.stop()
            self._proxy = None
        home = self.cfg.isolated_home
        if home.is_dir():
            for child in home.iterdir():
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink(missing_ok=True)

    def assert_clean(self, stage: str) -> None:
        left = self.residue()
        if left:
            raise ShadowError(f"{stage}: state is not clean — {len(left)} file(s) remain "
                              f"under the isolated home")

    # --- AgentRunner ---------------------------------------------------------

    def preflight(self) -> None:
        self.cfg.isolated_home.mkdir(parents=True, exist_ok=True)
        stale = self.residue()
        if stale:
            print(f"preflight: recovering {len(stale)} stale file(s)", file=self._err)
            self.purge()
        self.assert_clean("preflight")
        if not self.cfg.skill.is_file():
            raise ShadowError("preflight: SKILL.md not found at the configured path")
        print("preflight: clean state verified", file=self._err)

    def assert_tool_boundary(self) -> set[str]:
        """Observe the actual ``tools[]`` on the wire, with nothing forwarded."""
        cfg = self.cfg
        self._prepare()
        try:
            self._proxy = self._proxy_factory(
                self.expected_tools, port=cfg.proxy_port,
                state_path=str(cfg.proxy_state), mode="probe",
            ).start()
        except OSError as exc:
            raise ShadowError(f"tool boundary: proxy could not listen on port "
                              f"{cfg.proxy_port} ({exc.__class__.__name__})") from exc
        # The probe turn never reaches the provider: the proxy records the
        # request's tool names and rejects it. The CLI exits non-zero, which is
        # the expected outcome here.
        self._claude(PROBE_PROMPT)
        state = self._proxy.state
        if state.tools_bearing < 1:
            raise ShadowError("tool boundary: Claude never sent a tools-bearing request")
        tools = set(state.observed)
        check_tool_boundary(tools, self.expected_tools)
        # Reset the tally so the digest turn is judged on its own requests,
        # then let matching requests through.
        self._proxy.state.reset()
        self._proxy.mode = "enforce"
        return tools

    def digest(self) -> DigestResult:
        out = self._claude(PROMPT)
        text, run_id, rc = "", None, out.returncode
        try:
            payload = json.loads(out.stdout or "{}")
            run_id = payload.get("session_id")
            if payload.get("is_error"):
                rc = rc or 1
            elif rc == 0:
                text = (payload.get("result") or "").strip()
        except json.JSONDecodeError:
            rc = rc or 1
        assert self._proxy is not None
        state = self._proxy.state
        if state.violations:
            raise ShadowError(f"tool boundary: {state.violations} violating request(s) "
                              f"aborted on the wire during the digest")
        if state.tools_bearing < 1:
            raise ShadowError("tool boundary: the digest turn carried no tools")
        return DigestResult(text=text, run_id=run_id, returncode=rc)

    def cleanup(self, run_id: str | None) -> None:
        self.purge()
        self.assert_clean("post-run")
