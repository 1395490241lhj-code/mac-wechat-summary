#!/usr/bin/env python3
"""Read-only WeChat shadow-mode digest runner — command-line entry point.

Produces one digest from messages WeChat Companion has already stored, then
removes every local trace of the run. It is deliberately narrow:

* **Read-only.** The only tools reachable are the read-only MCP tools of the
  ``wechat_companion`` bridge. The effective surface is asserted per backend.
* **Fail closed.** The effective tool list is asserted **before any message is
  read**. Anything unexpected aborts the run.
* **Self-cleaning.** Whatever the chosen runtime persists locally is purged in
  a ``finally`` block, so ordinary errors and Ctrl+C still clean up, and the
  next preflight recovers from a kill that bypassed it.
* **Review-only.** The digest goes to stdout. It is derived real data and is
  not written to a file unless explicitly asked for.

The orchestration lives in ``agent_runner.py``; each runtime lives in
``runners/``. Select one with ``--agent-backend`` (or the
``WECHAT_DIGEST_AGENT_BACKEND`` environment variable):

* ``hermes`` (default) — the original Hermes Agent CLI backend. Its flags and
  behaviour are unchanged from before H6.
* ``claude`` — Claude Code headless. Synthetic-only until its gates are sealed.

Not enabled, by construction: cron, unattended execution, messaging sends,
terminal, filesystem, browser, computer use, memory, and skills runtime tools.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_runner import DEFAULT_TIMEOUT, run_shadow  # noqa: E402
from runners import BACKENDS  # noqa: E402

# Re-exported for the Hermes gate documentation and older call sites.
from runners.hermes import HERMES_EXPECTED_TOOLS as EXPECTED_TOOLS  # noqa: E402,F401


def _parse_env(items: list[str]) -> dict:
    extra = {}
    for item in items:
        k, _, v = item.partition("=")
        extra[k] = v
    return extra


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Read-only WeChat shadow digest run")
    ap.add_argument("--agent-backend", choices=BACKENDS,
                    default=os.environ.get("WECHAT_DIGEST_AGENT_BACKEND", "hermes"))
    ap.add_argument("--isolated-home", required=True, type=Path,
                    help="throwaway HOME for the child process")
    ap.add_argument("--env", action="append", default=[],
                    help="extra child env as KEY=VALUE (repeatable)")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                    help="wall-clock bound for the digest turn, seconds")

    hermes = ap.add_argument_group("hermes backend")
    hermes.add_argument("--hermes-entry", type=Path)
    hermes.add_argument("--python", type=Path,
                        help="hermes: interpreter with Hermes's dependencies; "
                             "claude: interpreter with the bridge's `mcp` dependency")
    hermes.add_argument("--hermes-home", type=Path)
    hermes.add_argument("--project-dir", type=Path)

    claude = ap.add_argument_group("claude backend")
    claude.add_argument("--claude-bin", type=Path)
    claude.add_argument("--bridge", type=Path)
    claude.add_argument("--db-path", type=Path)
    claude.add_argument("--skill", type=Path)
    claude.add_argument("--model")
    claude.add_argument("--proxy-port", type=int, default=8823)
    return ap


def _require(ap: argparse.ArgumentParser, args: argparse.Namespace, names: list[str]) -> None:
    missing = [n for n in names if getattr(args, n.replace("-", "_")) is None]
    if missing:
        ap.error(f"--agent-backend {args.agent_backend} requires: "
                 + ", ".join(f"--{n}" for n in missing))


def make_runner(args: argparse.Namespace, ap: argparse.ArgumentParser):
    extra = _parse_env(args.env)
    if args.agent_backend == "hermes":
        from runners.hermes import HermesConfig, HermesRunner
        _require(ap, args, ["hermes-entry", "python", "hermes-home", "project-dir"])
        cfg = HermesConfig(
            hermes_entry=args.hermes_entry, python=args.python,
            hermes_home=args.hermes_home, project_dir=args.project_dir,
            isolated_home=args.isolated_home, extra_env=extra, timeout=args.timeout,
        )
        cfg.isolated_home.mkdir(parents=True, exist_ok=True)
        cfg.hermes_home.mkdir(parents=True, exist_ok=True)
        return HermesRunner(cfg)

    from runners.claude import DEFAULT_MODEL, DEFAULT_SKILL, ClaudeConfig, ClaudeRunner
    _require(ap, args, ["claude-bin", "python", "bridge", "db-path"])
    skill = args.skill or (args.project_dir or Path.cwd()) / DEFAULT_SKILL
    cfg = ClaudeConfig(
        claude_bin=args.claude_bin, python=args.python, bridge=args.bridge,
        db_path=args.db_path, skill=skill, isolated_home=args.isolated_home,
        model=args.model or DEFAULT_MODEL, proxy_port=args.proxy_port,
        extra_env=extra, timeout=args.timeout,
    )
    return ClaudeRunner(cfg)


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    runner = make_runner(args, ap)
    return run_shadow(runner)


if __name__ == "__main__":
    raise SystemExit(main())
