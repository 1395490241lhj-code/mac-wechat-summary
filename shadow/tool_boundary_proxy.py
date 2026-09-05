#!/usr/bin/env python3
"""Fail-closed tool-boundary proxy — runtime-neutral exact-N verifier.

Sits between an agent runtime and its model provider and enforces one rule:

    EVERY tools-bearing model request must carry EXACTLY the expected tool
    names. Anything else aborts the request before it reaches the provider.

This exists because no runtime can prove its own tool boundary from the
inside. Configuration is what *should* be sent; this is what *is* sent. The
Hermes-era instance of this rule (``exact8_assertion_proxy.py``) caught a
49-tool payload on a Desktop profile whose config asked for one toolset.

The expected set is supplied by the caller — it is a property of the backend,
not of this proxy. Tool names are read from the ``tools[]`` array of the
request body, so nothing about prefixes or counts is assumed.

Two modes:

* ``enforce`` — a matching request is forwarded upstream; a mismatch is
  aborted with an error the runtime will surface.
* ``probe`` — every tools-bearing request is recorded and rejected without
  being forwarded. This lets a runner observe the wire surface *before any
  message is read and before any content leaves the machine*: the model
  never receives the request, so it can never call a tool.

Privacy: request bodies are never logged or persisted. Only tool NAMES,
counts, and a pass/fail verdict are recorded. The proxy is a test harness —
it is not part of any production path.

Usage
-----
    python3 shadow/tool_boundary_proxy.py --port 8823 --state <path> \\
        --expect mcp__wechat_companion__status --expect ...

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
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = "https://api.anthropic.com"

#: Hop-by-hop or length-bearing headers that must not be copied verbatim.
_SKIP_RESPONSE_HEADERS = ("transfer-encoding", "content-length", "connection")
_SKIP_REQUEST_HEADERS = ("host", "content-length")


def extract_tool_names(body: bytes) -> set[str]:
    """Names in the request's ``tools[]`` array; empty when there is none.

    Works for any provider whose request carries ``tools: [{"name": ...}]``
    (Anthropic Messages; OpenAI Responses function tools use the same key).
    Malformed bodies yield an empty set, which counts as *not* tools-bearing.
    """
    try:
        tools = json.loads(body).get("tools") or []
    except Exception:
        return set()
    names = set()
    for tool in tools:
        if isinstance(tool, dict) and isinstance(tool.get("name"), str):
            names.add(tool["name"])
    return names


class Assertion:
    """Verdict state. Holds names and counts only — never a request body."""

    def __init__(self, expected: frozenset[str], state_path: str | None) -> None:
        self.expected = frozenset(expected)
        self.path = state_path
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        """Start a fresh tally (same expected set, same state file)."""
        self.requests = 0
        self.tools_bearing = 0
        self.violations = 0
        self.observed: set[str] = set()
        self.checks: list[dict] = []

    @property
    def verdict(self) -> str:
        if self.violations:
            return "FAIL"
        return "PASS" if self.tools_bearing else "no tools-bearing request yet"

    @property
    def accepted(self) -> bool:
        """The acceptance contract, stated once."""
        return self.verdict == "PASS" and self.violations == 0 and self.tools_bearing >= 1

    def snapshot(self) -> dict:
        return {
            "requests": self.requests,
            "tools_bearing": self.tools_bearing,
            "violations": self.violations,
            "verdict": self.verdict,
            "expected": sorted(self.expected),
            "observed": sorted(self.observed),
            "checks": self.checks,
        }

    def save(self) -> None:
        if not self.path:
            return
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(self.snapshot(), handle, indent=1)

    def check(self, names: set[str]) -> bool:
        """Record one tools-bearing request. Returns True when it may proceed."""
        with self._lock:
            self.tools_bearing += 1
            self.observed = set(names)
            ok = names == self.expected
            if not ok:
                self.violations += 1
            self.checks.append({
                "request": self.requests,
                "tool_count": len(names),
                "ok": ok,
                "unexpected": sorted(names - self.expected),
                "missing": sorted(self.expected - names),
            })
            return ok


def make_handler(state: Assertion, upstream: str, mode_ref: dict):
    """``mode_ref["mode"]`` is read per request so a runner can flip modes."""

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

        def _abort(self, status: int, message: str) -> None:
            self._send(status, json.dumps({
                "type": "error",
                "error": {"type": "invalid_request_error", "message": message},
            }).encode())

        def _forward(self, method: str, body: bytes | None) -> None:
            request = urllib.request.Request(
                upstream + self.path, data=body, method=method,
                headers={k: v for k, v in self.headers.items()
                         if k.lower() not in _SKIP_REQUEST_HEADERS},
            )
            try:
                with urllib.request.urlopen(
                    request, context=ssl.create_default_context()
                ) as response:
                    self.send_response(response.status)
                    for key, value in response.headers.items():
                        if key.lower() not in _SKIP_RESPONSE_HEADERS:
                            self.send_header(key, value)
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    # Stream through so an SSE response reaches the client as
                    # it is produced rather than after it has completed.
                    while True:
                        chunk = response.read(8192)
                        if not chunk:
                            break
                        self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                        self.wfile.flush()
                    self.wfile.write(b"0\r\n\r\n")
            except urllib.error.HTTPError as error:
                self._send(error.code, error.read())

        def do_GET(self):  # noqa: N802
            state.requests += 1
            self._forward("GET", None)

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            state.requests += 1
            names = extract_tool_names(body)

            if names:
                ok = state.check(names)
                state.save()
                if mode_ref["mode"] == "probe":
                    # Observe only: never forward, whatever the verdict.
                    self._abort(400, "tool-boundary probe — request recorded, not forwarded")
                    return
                if not ok:
                    self._abort(400, "tool-boundary assertion FAILED — request aborted")
                    return
            state.save()
            self._forward("POST", body)

    return Handler


class ToolBoundaryProxy:
    """In-process proxy a runner can start, flip between modes, and stop."""

    def __init__(self, expected: frozenset[str], *, port: int,
                 state_path: str | None = None, upstream: str = UPSTREAM,
                 mode: str = "enforce", host: str = "127.0.0.1") -> None:
        if mode not in ("enforce", "probe"):
            raise ValueError(mode)
        self.state = Assertion(expected, state_path)
        self._mode = {"mode": mode}
        self.host, self.port = host, port
        self._server = ThreadingHTTPServer((host, port), make_handler(self.state, upstream, self._mode))
        self._thread: threading.Thread | None = None

    @property
    def mode(self) -> str:
        return self._mode["mode"]

    @mode.setter
    def mode(self, value: str) -> None:
        if value not in ("enforce", "probe"):
            raise ValueError(value)
        self._mode["mode"] = value

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self._server.server_address[1]}"

    def start(self) -> "ToolBoundaryProxy":
        self.state.save()
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread:
            self._thread.join(timeout=5)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--state", required=True, help="path to write the verdict JSON")
    parser.add_argument("--expect", action="append", default=[],
                        help="an expected tool name (repeatable)")
    parser.add_argument("--expect-file", type=argparse.FileType("r"),
                        help="file with one expected tool name per line")
    parser.add_argument("--mode", choices=("enforce", "probe"), default="enforce")
    parser.add_argument("--upstream", default=UPSTREAM)
    args = parser.parse_args(argv)

    expected = set(args.expect)
    if args.expect_file:
        expected |= {line.strip() for line in args.expect_file if line.strip()}
    if not expected:
        parser.error("at least one --expect (or --expect-file) is required")

    proxy = ToolBoundaryProxy(frozenset(expected), port=args.port, state_path=args.state,
                              upstream=args.upstream, mode=args.mode)
    proxy.state.save()
    print(f"exact-{len(expected)} tool-boundary proxy ({args.mode}) on "
          f"127.0.0.1:{args.port}", file=sys.stderr)
    proxy._server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
