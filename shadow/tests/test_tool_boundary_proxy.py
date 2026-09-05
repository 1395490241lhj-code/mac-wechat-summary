"""The exact-N proxy: name extraction, verdicts, and live enforce/probe modes.

The live tests run the proxy against a stub upstream on localhost. No real
provider is contacted.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import exact8_assertion_proxy
from tool_boundary_proxy import Assertion, ToolBoundaryProxy, extract_tool_names

FOUR = frozenset({"mcp__s__a", "mcp__s__b", "mcp__s__c", "mcp__s__d"})


# --- pure functions ----------------------------------------------------------

def test_extract_tool_names_reads_the_tools_array_only():
    body = json.dumps({"model": "m", "tools": [
        {"name": "mcp__s__a", "input_schema": {}},
        {"type": "function", "name": "mcp__s__b"},
        {"type": "web_search_20250305"},  # no name → ignored
        "garbage",
    ]}).encode()
    assert extract_tool_names(body) == {"mcp__s__a", "mcp__s__b"}


@pytest.mark.parametrize("body", [b"", b"{}", b'{"tools": null}', b"not json", b'{"tools": "x"}'])
def test_non_tools_bearing_bodies_yield_empty(body):
    assert extract_tool_names(body) == set()


def test_assertion_verdict_and_acceptance_contract(tmp_path):
    a = Assertion(FOUR, str(tmp_path / "s.json"))
    assert a.verdict == "no tools-bearing request yet" and not a.accepted
    assert a.check(set(FOUR)) and a.accepted and a.verdict == "PASS"
    assert not a.check(set(FOUR) | {"Bash"}) and a.verdict == "FAIL" and not a.accepted
    a.save()
    saved = json.loads((tmp_path / "s.json").read_text())
    assert saved["violations"] == 1 and saved["tools_bearing"] == 2
    assert saved["checks"][1] == {"request": 0, "tool_count": 5, "ok": False,
                                  "unexpected": ["Bash"], "missing": []}
    assert "input_schema" not in json.dumps(saved), "names and counts only"
    a.reset()
    assert a.tools_bearing == 0 and a.expected == FOUR


def test_exact8_wrapper_keeps_the_hermes_eight():
    assert exact8_assertion_proxy.EXPECTED_TOOLS == {
        "mcp__wechat_companion__status", "mcp__wechat_companion__list_conversations",
        "mcp__wechat_companion__get_messages", "mcp__wechat_companion__get_recent_messages",
        "mcp__wechat_companion__list_resources", "mcp__wechat_companion__read_resource",
        "mcp__wechat_companion__list_prompts", "mcp__wechat_companion__get_prompt",
    }


# --- live ------------------------------------------------------------------

class Upstream:
    """Records what reached it and streams a canned SSE-ish body back."""

    def __init__(self):
        self.hits: list[tuple[str, str, bytes]] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                outer.hits.append(("POST", self.path, body))
                payload = b"event: a\ndata: 1\n\nevent: b\ndata: 2\n\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                outer.hits.append(("GET", self.path, b""))
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def upstream():
    u = Upstream()
    yield u
    u.stop()


def post(url, tools):
    body = json.dumps({"model": "m", "tools": [{"name": t} for t in tools],
                       "messages": [{"role": "user", "content": "x"}]}).encode()
    req = urllib.request.Request(url + "/v1/messages", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "x-api-key": "k"})
    return urllib.request.urlopen(req)


def test_enforce_forwards_exact_match_and_streams_the_response(upstream, tmp_path):
    proxy = ToolBoundaryProxy(FOUR, port=0, state_path=str(tmp_path / "s.json"),
                              upstream=upstream.url).start()
    try:
        with post(proxy.url, FOUR) as r:
            assert r.status == 200 and r.read() == b"event: a\ndata: 1\n\nevent: b\ndata: 2\n\n"
        assert len(upstream.hits) == 1 and upstream.hits[0][1] == "/v1/messages"
        assert proxy.state.accepted and proxy.state.observed == set(FOUR)
        assert json.loads((tmp_path / "s.json").read_text())["verdict"] == "PASS"
    finally:
        proxy.stop()


def test_enforce_aborts_a_mismatch_before_it_reaches_upstream(upstream, tmp_path):
    proxy = ToolBoundaryProxy(FOUR, port=0, state_path=str(tmp_path / "s.json"),
                              upstream=upstream.url).start()
    try:
        with pytest.raises(urllib.error.HTTPError) as info:
            post(proxy.url, set(FOUR) | {"Bash"})
        assert info.value.code == 400 and b"assertion FAILED" in info.value.read()
        assert upstream.hits == []
        assert proxy.state.violations == 1 and proxy.state.verdict == "FAIL"
    finally:
        proxy.stop()


def test_probe_records_but_never_forwards_even_on_a_match(upstream, tmp_path):
    proxy = ToolBoundaryProxy(FOUR, port=0, mode="probe", upstream=upstream.url).start()
    try:
        with pytest.raises(urllib.error.HTTPError) as info:
            post(proxy.url, FOUR)
        assert info.value.code == 400 and b"probe" in info.value.read()
        assert upstream.hits == [] and proxy.state.tools_bearing == 1
        assert proxy.state.observed == set(FOUR) and proxy.state.violations == 0
        proxy.mode = "enforce"
        with post(proxy.url, FOUR) as r:
            assert r.status == 200
        assert len(upstream.hits) == 1
    finally:
        proxy.stop()


def test_requests_without_tools_pass_through_and_get_is_forwarded(upstream, tmp_path):
    proxy = ToolBoundaryProxy(FOUR, port=0, upstream=upstream.url).start()
    try:
        with post(proxy.url, set()) as r:
            assert r.status == 200
        with urllib.request.urlopen(proxy.url + "/v1/models") as r:
            assert r.read() == b"ok"
        assert [h[0] for h in upstream.hits] == ["POST", "GET"]
        assert proxy.state.tools_bearing == 0 and proxy.state.requests == 2
    finally:
        proxy.stop()


def test_invalid_mode_is_rejected():
    with pytest.raises(ValueError):
        ToolBoundaryProxy(FOUR, port=0, mode="observe")


def test_upstream_status_is_recorded_and_network_failure_becomes_502(upstream, tmp_path):
    proxy = ToolBoundaryProxy(FOUR, port=0, upstream=upstream.url).start()
    try:
        with post(proxy.url, FOUR):
            pass
        assert proxy.state.upstream_status == [200]
    finally:
        proxy.stop()
    dead = ToolBoundaryProxy(FOUR, port=0, upstream="http://127.0.0.1:9").start()
    try:
        with pytest.raises(urllib.error.HTTPError) as info:
            post(dead.url, FOUR)
        assert info.value.code == 502
        assert dead.state.forward_errors == ["URLError"]
        assert json.loads(dead.state.snapshot() and json.dumps(dead.state.snapshot()))["forward_errors"] == ["URLError"]
    finally:
        dead.stop()
