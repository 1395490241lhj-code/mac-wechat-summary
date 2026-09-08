#!/usr/bin/env python3
"""What the WeChat Companion bridge actually advertises over stdio.

The exact-8 boundary is four bridge tools plus four MCP resource/prompt utility
tools the runtime generates per server. Current upstream Hermes only generates
those four when the server *advertises* the matching capability families, so
"8" is a measured property of this bridge, not a constant. This probe measures
it against a synthetic store and prints the result as JSON.

``SYNTH_DB`` selects the store. Nothing here reads a real one.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BRIDGE = REPO / "bridge" / "wechat_companion_mcp.py"


async def probe() -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(BRIDGE)],
        env={**os.environ,
             "WECHAT_COMPANION_ALLOW_AGENT_READ": "1",
             "WECHAT_COMPANION_DB_PATH": os.environ["SYNTH_DB"]},
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            initialized = await session.initialize()
            capabilities = initialized.capabilities
            tools = await session.list_tools()
            return {
                "advertises_resources": getattr(capabilities, "resources", None) is not None,
                "advertises_prompts": getattr(capabilities, "prompts", None) is not None,
                "tools": sorted(tool.name for tool in tools.tools),
            }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(probe()), indent=1, sort_keys=True))
