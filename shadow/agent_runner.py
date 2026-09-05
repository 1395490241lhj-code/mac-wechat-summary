#!/usr/bin/env python3
"""Runtime-neutral orchestration for the read-only WeChat shadow digest.

This module owns everything that is *not* specific to one agent runtime:

* the run sequence — preflight → tool-boundary assertion → digest → cleanup;
* fail-closed semantics — a ``ShadowError`` aborts before any message is read;
* signal handling — Ctrl+C and SIGTERM unwind through the ``finally`` block;
* timeout handling — a runtime that overruns is reported as a failed run and
  still cleaned up;
* review-only output — the digest goes to stdout and is never written to a file;
* exit codes — ``0`` digest produced · ``1`` run failed · ``2`` boundary or
  preflight abort · ``3`` cleanup failed · ``130`` interrupted.

Everything that *is* runtime-specific — how the agent process is launched, how
its effective tool surface is read, how its output is parsed, and where it
persists raw content that must be purged — lives behind the ``AgentRunner``
protocol. ``shadow/runners/`` holds one implementation per backend.

The digest policy itself is never here. It is ``.hermes/skills/wechat-digest/
SKILL.md``, the single source every backend consumes unmodified.
"""

from __future__ import annotations

import signal
import subprocess
import sys
from dataclasses import dataclass
from typing import Protocol, TextIO

#: The one user turn every backend sends. The policy is in the skill, not here.
PROMPT = "请使用 wechat-digest skill 生成我的微信摘要。"

#: Default wall-clock bound for the digest turn, in seconds.
DEFAULT_TIMEOUT = 900

#: Substrings that mark a tool as mutating. Named purely so a failure message
#: can say *why* something is forbidden; the boundary itself is the exact set.
MUTATING_MARKERS = (
    "skill_manage", "terminal", "process", "write_file", "patch",
    "execute_code", "computer_use", "browser_", "delegate_task", "cronjob",
    "memory", "send", "kanban", "image_gen", "tts", "text_to_speech",
    "bash", "edit", "write", "webfetch", "websearch",
)


class ShadowError(RuntimeError):
    """Aborts the run. The message is safe to print — never message content."""


@dataclass(frozen=True)
class DigestResult:
    """What one digest turn produced. ``text`` is review-only."""

    text: str
    run_id: str | None
    returncode: int


class AgentRunner(Protocol):
    """One agent runtime, seen only through the operations the driver needs.

    Implementations must never print message content, must build the child
    environment from scratch, and must treat any unexpected tool as a hard
    stop. The driver guarantees ``cleanup`` runs whatever else happens.
    """

    backend: str
    expected_tools: frozenset[str]

    def preflight(self) -> None:
        """Recover residue from a previous run, then prove a clean state."""

    def assert_tool_boundary(self) -> set[str]:
        """Return the effective tool set, or raise ``ShadowError``.

        Must complete before any message is read, and must fail closed.
        """

    def digest(self) -> DigestResult:
        """Run one digest turn. May raise ``subprocess.TimeoutExpired``."""

    def cleanup(self, run_id: str | None) -> None:
        """Purge every local trace of the run, then prove the state is clean."""


def check_tool_boundary(tools: set[str], expected: frozenset[str]) -> None:
    """Fail closed on any deviation from the expected tool set.

    Shared by every backend so the rule is stated exactly once: the effective
    surface must equal the expected surface — no extra tool, no missing tool.
    """
    unexpected = tools - expected
    missing = expected - tools
    if unexpected:
        mutating = sorted(t for t in unexpected
                          if any(m in t.lower() for m in MUTATING_MARKERS))
        raise ShadowError(
            f"tool boundary: refusing to run — unexpected tool(s): "
            f"{sorted(unexpected)}"
            + (f"; MUTATING: {mutating}" if mutating else "")
        )
    if missing:
        raise ShadowError(f"tool boundary: expected tool(s) absent: {sorted(missing)}")


def run_shadow(runner: AgentRunner, *, out: TextIO = sys.stdout,
               err: TextIO = sys.stderr, install_signals: bool = True) -> int:
    """Drive one shadow run to completion and return the process exit code.

    ``install_signals`` is False only under test, where the handlers cannot be
    installed from a non-main thread and are not what is being exercised.
    """
    if install_signals:
        # Ctrl+C and SIGTERM must unwind through the finally block, not kill us.
        def _raise(signum, _frame):
            raise KeyboardInterrupt(f"signal {signum}")
        signal.signal(signal.SIGINT, _raise)
        signal.signal(signal.SIGTERM, _raise)

    run_id: str | None = None
    status = 0
    try:
        runner.preflight()
        tools = runner.assert_tool_boundary()
        print(f"tool boundary: verified {len(tools)} read-only tools "
              f"({runner.backend})", file=err)
        result = runner.digest()
        run_id = result.run_id
        if result.returncode != 0 or not result.text:
            status = 1
            print("run failed — no digest produced", file=err)
        else:
            # Review-only: stdout, never a file.
            print(result.text, file=out)
    except subprocess.TimeoutExpired:
        status = 1
        print("run failed — agent runtime timed out", file=err)
    except ShadowError as exc:
        print(f"ABORTED: {exc}", file=err)
        status = 2
    except KeyboardInterrupt as exc:
        print(f"INTERRUPTED: {exc}", file=err)
        status = 130
    finally:
        # Runs on success, ordinary failure, abort, timeout and Ctrl+C alike.
        # A SIGKILL or power loss skips this; the next preflight recovers it.
        try:
            runner.cleanup(run_id)
            print("cleanup: verified clean", file=err)
        except Exception as exc:  # never mask the primary outcome
            print(f"CLEANUP FAILED: {exc}", file=err)
            status = status or 3
    return status
