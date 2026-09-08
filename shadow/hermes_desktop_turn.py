#!/usr/bin/env python3
"""One real agent turn on the Desktop surface, without the Electron app.

The two fixes under validation (#89550, #88865) govern
``tui_gateway.server._load_enabled_toolsets``, which the ``hermes chat`` CLI
path never reaches -- only the TUI gateway does. So the exact-8 *Desktop*
assertion cannot be made through the CLI runner: it has to go through the
gateway's own agent factory, ``_make_agent``, which is what a Desktop chat
session uses. ``HERMES_DESKTOP=1`` makes that factory resolve the session
platform as ``desktop``; no packaged app, no Electron, no gateway socket.

The turn's provider request travels to whatever ``ANTHROPIC_BASE_URL`` names,
so pointing that at the exact-8 proxy puts the assertion on the real wire.

This is the second and last module in the repository that imports a Hermes
tree; like ``hermes_probe``, the isolation guard runs before ``sys.path`` is
touched. The report and the response text each go to their own file: the
runtime writes its own warnings *and* JSON-RPC event lines to stdout, so stdout
is not machine-readable and a digest must never be mixed into it either.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hermes_isolation import IsolationRefused, enforce_disposable_home  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tree", required=True, type=Path)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--skill", type=Path, help="SKILL.md preloaded ahead of the prompt")
    ap.add_argument("--response", required=True, type=Path, help="where the reply is written")
    ap.add_argument("--session-id", default="h5pre-desktop")
    ap.add_argument("--out", required=True, type=Path, help="where the JSON report is written")
    args = ap.parse_args(argv)

    try:
        resolved = enforce_disposable_home()
    except IsolationRefused as refusal:
        print(json.dumps({"refused": str(refusal)}), file=sys.stderr)
        return 3

    sys.path.insert(0, str(args.tree.resolve()))
    report: dict = {"isolation": {k: str(v) for k, v in resolved.items()}}
    try:
        import tui_gateway.server as server

        report["session_platform"] = server._resolve_session_platform()
        report["enabled_toolsets"] = server._load_enabled_toolsets()

        # A Desktop session's gateway starts MCP discovery before it builds an
        # agent; importing the server module does not. Without it the
        # `wechat_companion` toolset name is unregistered when toolsets are
        # validated, the agent is built with no tools, and every request
        # reaches the provider carrying no `tools[]` at all -- which the wire
        # verifier correctly reports as "not tools-bearing", i.e. an assertion
        # that never fired rather than a boundary that held.
        import hermes_cli.mcp_startup as mcp_startup
        import logging
        mcp_startup.set_mcp_server_filter(report["enabled_toolsets"])
        mcp_startup.start_background_mcp_discovery(
            logger=logging.getLogger("h5pre"), thread_name="h5pre-mcp-discovery")
        mcp_startup.wait_for_mcp_discovery(timeout=120)
        report["mcp_discovery_in_flight"] = mcp_startup.mcp_discovery_in_flight()
        agent = server._make_agent(args.session_id, args.session_id,
                                   session_id=args.session_id)
        report["agent_platform"] = getattr(agent, "platform", None)
        report["agent_toolsets"] = list(getattr(agent, "enabled_toolsets", None) or [])
        report["agent_tool_names"] = sorted(_tool_names(agent))

        message = args.prompt
        if args.skill and args.skill.is_file():
            message = args.skill.read_text(encoding="utf-8") + "\n\n" + args.prompt
        result = agent.run_conversation(message)
        text = result if isinstance(result, str) else (
            getattr(result, "content", None) or getattr(result, "text", None) or str(result))
        args.response.write_text(text or "", encoding="utf-8")
        report["response_bytes"] = len(text or "")
        report["ok"] = bool(text)
    except Exception as exc:                                 # reported, not raised
        report["ok"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()[-2000:]

    args.out.write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    return 0 if report.get("ok") else 5


def _tool_names(agent) -> set[str]:
    """Whatever the agent will actually present, however it stores it."""
    for attribute in ("tool_definitions", "tools", "_tools", "tool_schemas"):
        value = getattr(agent, attribute, None)
        if not value:
            continue
        names = set()
        for item in value:
            if isinstance(item, dict):
                name = item.get("name") or (item.get("function") or {}).get("name")
                if name:
                    names.add(name)
            elif isinstance(item, str):
                names.add(item)
        if names:
            return names
    return set()


if __name__ == "__main__":
    raise SystemExit(main())
