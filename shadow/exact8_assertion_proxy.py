#!/usr/bin/env python3
"""Fail-closed tool-boundary proxy — the stock-runtime acceptance test.

Sits between a Hermes runtime and the Anthropic API and enforces one rule:

    EVERY tools-bearing model request must carry EXACTLY the eight read-only
    ``mcp__wechat_companion__*`` tool schemas. Anything else aborts the
    request before it reaches the provider.

This exists because neither the CLI nor Desktop can prove their own tool
boundary from the inside. Configuration is what *should* be sent; this is
what *is* sent. It caught a 49-tool payload (including ``skill_manage``,
``terminal``, ``write_file``, ``execute_code``, ``computer_use``) on a
Desktop profile whose config asked for one toolset.

Privacy: request bodies are never logged or persisted. Only tool NAMES,
counts, and a pass/fail verdict are recorded. The proxy is a test harness —
it is not part of the shadow runner and must not be used in production.

Usage
-----
    python3 shadow/exact8_assertion_proxy.py --port 8823 --state <path>

Point the runtime at it with ``ANTHROPIC_BASE_URL=http://127.0.0.1:<port>``
and inspect the state file afterwards. Exit criteria for acceptance:

    verdict == "PASS" and violations == 0 and tools_bearing >= 1

A run with zero tools-bearing requests is NOT a pass — it means the
assertion never got the chance to fire.
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = "https://api.anthropic.com"

#: The only tool surface a read-only WeChat digest may present to the model.
#: The four resource/prompt entries are MCP protocol primitives Hermes adds
#: automatically; they are read-only by spec and inert against a bridge that
#: declares no resources and no prompts.
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


class Assertion:
    """Verdict state. Holds names and counts only — never a request body."""

    def __init__(self, state_path: str) -> None:
        self.path = state_path
        self.requests = 0
        self.tools_bearing = 0
        self.violations = 0
        self.checks: list[dict] = []

    @property
    def verdict(self) -> str:
        if self.violations:
            return "FAIL"
        return "PASS" if self.tools_bearing else "no tools-bearing request yet"

    def save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({
                "requests": self.requests,
                "tools_bearing": self.tools_bearing,
                "violations": self.violations,
                "verdict": self.verdict,
                "expected": sorted(EXPECTED_TOOLS),
                "checks": self.checks,
            }, handle, indent=1)

    def check(self, names: set[str]) -> bool:
        """Record one tools-bearing request. Returns True when it may proceed."""
        self.tools_bearing += 1
        ok = names == EXPECTED_TOOLS
        if not ok:
            self.violations += 1
        self.checks.append({
            "request": self.requests,
            "tool_count": len(names),
            "ok": ok,
            "unexpected": sorted(names - EXPECTED_TOOLS),
            "missing": sorted(EXPECTED_TOOLS - names),
        })
        return ok


def make_handler(state: Assertion):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # no request logging
            pass

        def _send(self, status: int, body: bytes, headers=None):
            self.send_response(status)
            for key, value in (headers or {"Content-Type": "application/json"}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            state.requests += 1

            try:
                names = {t.get("name") for t in (json.loads(body).get("tools") or [])}
            except Exception:
                names = set()

            if names and not state.check(names):
                state.save()
                self._send(500, json.dumps({
                    "type": "error",
                    "error": {
                        "type": "api_error",
                        "message": "tool-boundary assertion FAILED — request aborted",
                    },
                }).encode())
                return
            state.save()

            request = urllib.request.Request(
                UPSTREAM + self.path, data=body, method="POST",
                headers={k: v for k, v in self.headers.items()
                         if k.lower() not in ("host", "content-length")},
            )
            try:
                with urllib.request.urlopen(
                    request, context=ssl.create_default_context()
                ) as response:
                    data = response.read()
                    passthrough = {
                        k: v for k, v in response.headers.items()
                        if k.lower() not in
                        ("transfer-encoding", "content-length", "connection")
                    }
                    self._send(response.status, data, passthrough)
            except urllib.error.HTTPError as error:
                self._send(error.code, error.read())

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--state", required=True,
                        help="path to write the verdict JSON")
    args = parser.parse_args(argv)

    state = Assertion(args.state)
    state.save()
    print(f"exact-8 assertion proxy on 127.0.0.1:{args.port}", file=sys.stderr)
    ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(state)).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
