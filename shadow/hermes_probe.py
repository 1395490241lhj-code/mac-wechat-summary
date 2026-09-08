#!/usr/bin/env python3
"""The only module in this repository that imports an upstream Hermes tree.

Concentrating the import here is the point. Importing ``hermes_cli.config`` --
directly or through anything that reaches it, such as ``tui_gateway.server`` --
seeds and upgrades ``SOUL.md`` inside the resolved ``HERMES_HOME`` at import
time, falling back to ``$HOME/.hermes`` when the variable is unset. So the
isolation guard runs **before** the tree reaches ``sys.path``, and a refusal
exits non-zero having imported nothing.

Reports, as JSON on stdout:

* the resolved session platform (``HERMES_DESKTOP`` selects ``desktop``);
* the per-turn toolset row the runtime would hand the agent;
* the parsed ``agent.disabled_toolsets`` set.

Usage
-----
    HOME=<disposable> HERMES_HOME=<disposable> HERMES_DESKTOP=1 \\
        python3 shadow/hermes_probe.py --tree <hermes checkout>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hermes_isolation import IsolationRefused, enforce_disposable_home  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tree", required=True, type=Path, help="Hermes checkout to import")
    ap.add_argument("--platform", action="append", default=[],
                    help="resolve this platform explicitly, in addition to the session's")
    ap.add_argument("--out", required=True, type=Path,
                    help="where the JSON report is written; stdout carries the runtime's "
                         "own warnings and is not machine-readable")
    args = ap.parse_args(argv)

    # --- the guard, before anything Hermes-shaped exists on sys.path --------
    try:
        resolved = enforce_disposable_home()
    except IsolationRefused as refusal:
        print(json.dumps({"refused": str(refusal)}), file=sys.stderr)
        return 3

    tree = args.tree.resolve()
    if not (tree / "tui_gateway" / "server.py").is_file():
        print(json.dumps({"error": f"not a Hermes checkout: {tree}"}), file=sys.stderr)
        return 4
    sys.path.insert(0, str(tree))

    import agent.coding_context as coding_context
    import tui_gateway.server as server

    # No coding posture: the posture branch resolves from the working directory
    # and would make the result depend on where the probe happened to run.
    coding_context.coding_selection = lambda **_: None

    report = {
        "isolation": {name: str(path) for name, path in resolved.items()},
        "server_module": server.__file__,
        "session_platform": server._resolve_session_platform(),
        "disabled_toolsets": sorted(server._disabled_agent_toolsets())
        if hasattr(server, "_disabled_agent_toolsets") else None,
        "toolsets": {},
    }
    report["toolsets"]["<session>"] = server._load_enabled_toolsets()
    for platform in args.platform:
        report["toolsets"][platform] = server._load_enabled_toolsets(platform)

    args.out.write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
