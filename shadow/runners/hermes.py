#!/usr/bin/env python3
"""Hermes Agent backend for the shadow digest — the original runner, unchanged.

Everything Hermes-specific from the pre-H6 ``wechat_shadow_run.py`` lives here
and nowhere else:

* **Launch.** ``<python> <hermes_entry> chat -Q -q <prompt> -s wechat-digest
  -t wechat_companion`` under a dedicated ``HERMES_HOME`` and an isolated
  ``HOME``. The digest skill is *preloaded* rather than exposed through the
  ``skills`` toolset, because Hermes v0.20.5 cannot offer ``skill_view``
  without also offering the mutating ``skill_manage`` (H4.5).
* **Tool boundary.** Hermes prints its final tool selection during agent init,
  before the first API call. The probe is terminated as soon as that line is
  seen, so the boundary is confirmed without reading a single message.
* **Response parsing.** The digest is stdout; ``session_id:`` is on stderr.
* **Persistence cleanup.** Raw message content reaches the Hermes session store
  on every run and a ``request_dump_*.json`` on provider failure (H4.6). All
  three cleanup steps are required — delete, optimize (VACUUM), sweep orphans.

Argument shapes, environment construction, timeouts and the purge sequence are
byte-for-byte those of the pre-H6 runner; the unit tests pin them.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from agent_runner import DigestResult, PROMPT, ShadowError, check_tool_boundary

# The exact tool surface this backend permits. Anything else is a hard stop.
# The four *_resource / *_prompt entries are MCP protocol primitives Hermes
# adds automatically; they are read-only by spec and inert against this bridge,
# which declares no resources and no prompts.
HERMES_EXPECTED_TOOLS = frozenset({
    "mcp__wechat_companion__status",
    "mcp__wechat_companion__list_conversations",
    "mcp__wechat_companion__get_messages",
    "mcp__wechat_companion__get_recent_messages",
    "mcp__wechat_companion__list_resources",
    "mcp__wechat_companion__read_resource",
    "mcp__wechat_companion__list_prompts",
    "mcp__wechat_companion__get_prompt",
})

SKILL_NAME = "wechat-digest"
TOOLSETS = "wechat_companion"
BOUNDARY_MARKER = "Final tool selection"


@dataclass
class HermesConfig:
    hermes_entry: Path      # the `hermes` entry script of the runtime to use
    python: Path            # interpreter that has Hermes's dependencies
    hermes_home: Path       # dedicated profile home for shadow runs
    project_dir: Path       # repo whose .hermes/skills holds the digest skill
    isolated_home: Path     # HOME for the child, so ~/.claude etc. stay unseen
    extra_env: dict = field(default_factory=dict)
    timeout: int = 900

    def child_env(self) -> dict:
        """A minimal, explicit environment.

        Built from scratch rather than inherited so nothing from the operator's
        shell — credentials, base-URL overrides, Claude Code state — can change
        how the run resolves.
        """
        env = {
            "HOME": str(self.isolated_home),
            "HERMES_HOME": str(self.hermes_home),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "LANG": "en_US.UTF-8",
            "LC_ALL": "en_US.UTF-8",
            "TERM": "dumb",
        }
        env.update(self.extra_env)
        return env


class HermesRunner:
    """``AgentRunner`` over the Hermes Agent CLI.

    ``run`` and ``popen`` default to ``subprocess`` and are injectable so the
    tests can pin argv, environment, timeouts and ordering without a runtime.
    """

    backend = "hermes"
    expected_tools = HERMES_EXPECTED_TOOLS

    def __init__(self, cfg: HermesConfig, *, run: Callable = subprocess.run,
                 popen: Callable = subprocess.Popen, err=sys.stderr) -> None:
        self.cfg = cfg
        self._run = run
        self._popen = popen
        self._err = err

    # --- process launch ------------------------------------------------------

    def _hermes(self, args: list[str], timeout: int) -> subprocess.CompletedProcess:
        cfg = self.cfg
        return self._run(
            [str(cfg.python), str(cfg.hermes_entry), *args],
            cwd=str(cfg.project_dir), env=cfg.child_env(),
            capture_output=True, text=True, timeout=timeout,
        )

    # --- session / dump housekeeping ----------------------------------------

    def list_sessions(self) -> list[str]:
        out = self._hermes(["sessions", "list"], timeout=120)
        ids = []
        for line in (out.stdout + out.stderr).splitlines():
            for token in line.split():
                # Session ids look like 20260825_105319_5be2f1
                parts = token.strip().split("_")
                if (len(parts) == 3 and len(parts[0]) == 8 and len(parts[1]) == 6
                        and parts[0].isdigit() and parts[1].isdigit()):
                    ids.append(token.strip())
        return sorted(set(ids))

    def dump_files(self) -> list[Path]:
        d = self.cfg.hermes_home / "sessions"
        return sorted(d.glob("request_dump_*.json")) if d.is_dir() else []

    def purge(self, session_ids: list[str]) -> None:
        """Delete sessions, reclaim freed pages, then sweep leftover dumps.

        All three steps are required. Deletion alone left content in freed
        database pages in H4.5, and it only removes the dump belonging to the
        session being deleted — an orphaned dump outlives its session and is
        reclaimed by nothing else (H4.6).
        """
        for sid in session_ids:
            self._hermes(["sessions", "delete", sid, "--yes"], timeout=120)
        if session_ids:
            self._hermes(["sessions", "optimize"], timeout=300)
        for path in self.dump_files():
            path.unlink(missing_ok=True)

    def assert_clean(self, stage: str) -> None:
        sessions, dumps = self.list_sessions(), self.dump_files()
        if sessions or dumps:
            raise ShadowError(
                f"{stage}: state is not clean — "
                f"{len(sessions)} session(s), {len(dumps)} request dump(s) remain"
            )

    # --- AgentRunner ---------------------------------------------------------

    def preflight(self) -> None:
        """Recover from anything a previous run could not clean up itself.

        A SIGKILL or power loss bypasses the finally block, so residue from the
        previous run is detected and removed *here*, before this run reads
        anything new.
        """
        stale_sessions, stale_dumps = self.list_sessions(), self.dump_files()
        if stale_sessions or stale_dumps:
            print(f"preflight: recovering {len(stale_sessions)} stale session(s), "
                  f"{len(stale_dumps)} orphan dump(s)", file=self._err)
            self.purge(stale_sessions)
        self.assert_clean("preflight")
        print("preflight: clean state verified", file=self._err)

    def assert_tool_boundary(self) -> set[str]:
        """Read the effective tool list and fail closed on anything unexpected."""
        cfg = self.cfg
        proc = self._popen(
            [str(cfg.python), str(cfg.hermes_entry), "chat", "-Q", "-v",
             "-q", "boundary-probe", "-s", SKILL_NAME, "-t", TOOLSETS],
            cwd=str(cfg.project_dir), env=cfg.child_env(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        line = None
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                if BOUNDARY_MARKER in raw:
                    line = raw
                    break
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()

        if line is None:
            raise ShadowError("tool boundary: Hermes never reported a tool selection")

        tools = {t.strip() for t in line.split(":", 1)[1].split(",") if t.strip()}
        check_tool_boundary(tools, self.expected_tools)
        return tools

    def digest(self) -> DigestResult:
        out = self._hermes(["chat", "-Q", "-q", PROMPT, "-s", SKILL_NAME, "-t", TOOLSETS],
                           timeout=self.cfg.timeout)
        sid = None
        for line in out.stderr.splitlines():
            if line.strip().startswith("session_id:"):
                sid = line.split(":", 1)[1].strip()
                break
        return DigestResult(text=out.stdout.strip(), run_id=sid,
                            returncode=out.returncode)

    def cleanup(self, run_id: str | None) -> None:
        known = self.list_sessions()
        if run_id and run_id not in known:
            known.append(run_id)
        self.purge(known)
        self.assert_clean("post-run")
