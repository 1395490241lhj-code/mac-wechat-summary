#!/usr/bin/env python3
"""Fail-closed tool-boundary proxy — the Hermes stock-runtime acceptance test.

This is the Hermes-specific instance of ``tool_boundary_proxy.py``: the
expected set is the exact eight read-only ``mcp__wechat_companion__*`` names
Hermes v0.20.5 puts on the wire (four bridge tools plus the four MCP protocol
primitives Hermes adds automatically, which are inert against a bridge that
declares no resources and no prompts).

The rule, the privacy posture and the acceptance contract are unchanged from
the H4.7 gate and are documented in ``tool_boundary_proxy.py``:

    verdict == "PASS" and violations == 0 and tools_bearing >= 1

Usage
-----
    python3 shadow/exact8_assertion_proxy.py --port 8823 --state <path>

Point the runtime at it with ``ANTHROPIC_BASE_URL=http://127.0.0.1:<port>``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tool_boundary_proxy import main as _main  # noqa: E402

#: The only tool surface a read-only WeChat digest may present to the model
#: through Hermes v0.20.5.
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


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    for name in sorted(EXPECTED_TOOLS):
        args += ["--expect", name]
    return _main(args)


if __name__ == "__main__":
    raise SystemExit(main())
