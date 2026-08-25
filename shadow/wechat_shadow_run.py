#!/usr/bin/env python3
"""Read-only WeChat shadow-mode digest runner.

Produces one digest from messages WeChat Companion has already stored, then
removes every local trace of the run. It is deliberately narrow:

* **Read-only.** The only tools reachable are the eight read-only MCP tools of
  the ``wechat_companion`` bridge. The digest skill is *preloaded* rather than
  exposed through the ``skills`` toolset, because Hermes cannot offer
  ``skill_view`` without also offering the mutating ``skill_manage`` (H4.5).
* **Fail closed.** The effective tool list is asserted **before any message is
  read**. Anything unexpected aborts the run.
* **Self-cleaning.** Raw message content reaches two local places: the Hermes
  session store and, on provider failure, a ``request_dump_*.json`` (H4.6).
  Cleanup runs in a ``finally`` block so ordinary errors and Ctrl+C still
  clean up, and the next preflight recovers from a kill that bypassed it.
* **Review-only.** The digest goes to stdout. It is derived real data and is
  not written to a file unless explicitly asked for.

Not enabled, by construction: cron, unattended execution, messaging sends,
terminal, filesystem, browser, computer use, memory, and skills runtime tools.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# The exact tool surface this runner permits. Anything else is a hard stop.
# The four *_resource / *_prompt entries are MCP protocol primitives Hermes
# adds automatically; they are read-only by spec and inert against this bridge,
# which declares no resources and no prompts.
EXPECTED_TOOLS = frozenset({
    "mcp__wechat_companion__status",
    "mcp__wechat_companion__list_conversations",
    "mcp__wechat_companion__get_messages",
    "mcp__wechat_companion__get_recent_messages",
    "mcp__wechat_companion__list_resources",
    "mcp__wechat_companion__read_resource",
    "mcp__wechat_companion__list_prompts",
    "mcp__wechat_companion__get_prompt",
})

# Named purely so a failure message can say *why* something is forbidden.
MUTATING_MARKERS = (
    "skill_manage", "terminal", "process", "write_file", "patch",
    "execute_code", "computer_use", "browser_", "delegate_task", "cronjob",
    "memory", "send", "kanban", "image_gen", "tts", "text_to_speech",
)

SKILL_NAME = "wechat-digest"
TOOLSETS = "wechat_companion"
PROMPT = "请使用 wechat-digest skill 生成我的微信摘要。"


class ShadowError(RuntimeError):
    """Aborts the run. The message is safe to print — never message content."""


@dataclass
class Config:
    hermes_entry: Path      # the `hermes` entry script of the runtime to use
    python: Path            # interpreter that has Hermes's dependencies
    hermes_home: Path       # dedicated profile home for shadow runs
    project_dir: Path       # repo whose .hermes/skills holds the digest skill
    isolated_home: Path     # HOME for the child, so ~/.claude etc. stay unseen
    extra_env: dict = field(default_factory=dict)

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


def _run(cfg: Config, args: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(cfg.python), str(cfg.hermes_entry), *args],
        cwd=str(cfg.project_dir), env=cfg.child_env(),
        capture_output=True, text=True, timeout=timeout,
    )


# --- session / dump housekeeping --------------------------------------------

def list_sessions(cfg: Config) -> list[str]:
    out = _run(cfg, ["sessions", "list"], timeout=120)
    ids = []
    for line in (out.stdout + out.stderr).splitlines():
        for token in line.split():
            # Session ids look like 20260825_105319_5be2f1
            parts = token.strip().split("_")
            if (len(parts) == 3 and len(parts[0]) == 8 and len(parts[1]) == 6
                    and parts[0].isdigit() and parts[1].isdigit()):
                ids.append(token.strip())
    return sorted(set(ids))


def dump_files(cfg: Config) -> list[Path]:
    d = cfg.hermes_home / "sessions"
    return sorted(d.glob("request_dump_*.json")) if d.is_dir() else []


def purge(cfg: Config, session_ids: list[str]) -> None:
    """Delete sessions, reclaim freed pages, then sweep leftover dumps.

    All three steps are required. Deletion alone left content in freed database
    pages in H4.5, and it only removes the dump belonging to the session being
    deleted — an orphaned dump outlives its session and is reclaimed by nothing
    else (H4.6).
    """
    for sid in session_ids:
        _run(cfg, ["sessions", "delete", sid, "--yes"], timeout=120)
    if session_ids:
        _run(cfg, ["sessions", "optimize"], timeout=300)
    for path in dump_files(cfg):
        path.unlink(missing_ok=True)


def assert_clean(cfg: Config, stage: str) -> None:
    sessions, dumps = list_sessions(cfg), dump_files(cfg)
    if sessions or dumps:
        raise ShadowError(
            f"{stage}: state is not clean — "
            f"{len(sessions)} session(s), {len(dumps)} request dump(s) remain"
        )


def preflight(cfg: Config) -> None:
    """Recover from anything a previous run could not clean up itself.

    A SIGKILL or power loss bypasses the finally block, so residue from the
    previous run is detected and removed *here*, before this run reads
    anything new.
    """
    stale_sessions, stale_dumps = list_sessions(cfg), dump_files(cfg)
    if stale_sessions or stale_dumps:
        print(f"preflight: recovering {len(stale_sessions)} stale session(s), "
              f"{len(stale_dumps)} orphan dump(s)", file=sys.stderr)
        purge(cfg, stale_sessions)
    assert_clean(cfg, "preflight")
    print("preflight: clean state verified", file=sys.stderr)


# --- tool boundary -----------------------------------------------------------

def assert_tool_boundary(cfg: Config) -> set[str]:
    """Read the effective tool list and fail closed on anything unexpected.

    Hermes prints its final selection during agent init, before the first API
    call, so the probe is terminated as soon as the line is seen: the boundary
    is confirmed without inference and without reading a single message.
    """
    proc = subprocess.Popen(
        [str(cfg.python), str(cfg.hermes_entry), "chat", "-Q", "-v",
         "-q", "boundary-probe", "-s", SKILL_NAME, "-t", TOOLSETS],
        cwd=str(cfg.project_dir), env=cfg.child_env(),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    marker, line = "Final tool selection", None
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            if marker in raw:
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
    unexpected = tools - EXPECTED_TOOLS
    missing = EXPECTED_TOOLS - tools
    if unexpected:
        mutating = sorted(t for t in unexpected
                          if any(m in t for m in MUTATING_MARKERS))
        raise ShadowError(
            f"tool boundary: refusing to run — unexpected tool(s): "
            f"{sorted(unexpected)}"
            + (f"; MUTATING: {mutating}" if mutating else "")
        )
    if missing:
        raise ShadowError(f"tool boundary: expected tool(s) absent: {sorted(missing)}")
    print(f"tool boundary: verified {len(tools)} read-only tools", file=sys.stderr)
    return tools


# --- the run -----------------------------------------------------------------

def digest(cfg: Config) -> tuple[str, str | None, int]:
    out = _run(cfg, ["chat", "-Q", "-q", PROMPT, "-s", SKILL_NAME, "-t", TOOLSETS])
    sid = None
    for line in out.stderr.splitlines():
        if line.strip().startswith("session_id:"):
            sid = line.split(":", 1)[1].strip()
            break
    return out.stdout.strip(), sid, out.returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only WeChat shadow digest run")
    ap.add_argument("--hermes-entry", required=True, type=Path)
    ap.add_argument("--python", required=True, type=Path)
    ap.add_argument("--hermes-home", required=True, type=Path)
    ap.add_argument("--project-dir", required=True, type=Path)
    ap.add_argument("--isolated-home", required=True, type=Path)
    ap.add_argument("--env", action="append", default=[],
                    help="extra child env as KEY=VALUE (repeatable)")
    args = ap.parse_args(argv)

    extra = {}
    for item in args.env:
        k, _, v = item.partition("=")
        extra[k] = v

    cfg = Config(
        hermes_entry=args.hermes_entry, python=args.python,
        hermes_home=args.hermes_home, project_dir=args.project_dir,
        isolated_home=args.isolated_home, extra_env=extra,
    )
    cfg.isolated_home.mkdir(parents=True, exist_ok=True)
    cfg.hermes_home.mkdir(parents=True, exist_ok=True)

    # Ctrl+C and SIGTERM must unwind through the finally block, not kill us.
    def _raise(signum, _frame):
        raise KeyboardInterrupt(f"signal {signum}")
    signal.signal(signal.SIGINT, _raise)
    signal.signal(signal.SIGTERM, _raise)

    session_id = None
    status = 0
    try:
        preflight(cfg)
        assert_tool_boundary(cfg)
        text, session_id, rc = digest(cfg)
        if rc != 0 or not text:
            status = 1
            print("run failed — no digest produced", file=sys.stderr)
        else:
            # Review-only: stdout, never a file.
            print(text)
    except ShadowError as exc:
        print(f"ABORTED: {exc}", file=sys.stderr)
        status = 2
    except KeyboardInterrupt as exc:
        print(f"INTERRUPTED: {exc}", file=sys.stderr)
        status = 130
    finally:
        # Runs on success, ordinary failure, abort, and Ctrl+C alike. A SIGKILL
        # or power loss skips this; the next preflight recovers that case.
        try:
            known = list_sessions(cfg)
            if session_id and session_id not in known:
                known.append(session_id)
            purge(cfg, known)
            assert_clean(cfg, "post-run")
            print("cleanup: verified clean", file=sys.stderr)
        except Exception as exc:  # never mask the primary outcome
            print(f"CLEANUP FAILED: {exc}", file=sys.stderr)
            status = status or 3
    return status


if __name__ == "__main__":
    raise SystemExit(main())
