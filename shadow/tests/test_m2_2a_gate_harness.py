"""Offline checks of the M2.2a gate harness: the synthetic corpus has the
coverage semantics the live tasks depend on, the gate-only runner keeps the
unchanged eight-tool boundary, and the preflight refusal never launches.

No Claude Code process, no credential, no real preference domain."""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def gate():
    spec = importlib.util.spec_from_file_location("m2_2a_gate", ROOT / "scripts" / "m2_2a_live_memory_gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    for name in ("memory_consent", "memory_identity", "memory_ingest", "memory_query", "memory_store",
                 "message_source", "scenarios"):
        sys.modules.pop(name, None)


def test_the_corpus_has_the_semantics_the_live_tasks_need(gate, tmp_path):
    facts = gate.build_memory_store(tmp_path / "memory.sqlite", tmp_path / "home")
    assert facts["product_conversation_coverage"] == "observed_complete"
    assert facts["product_empty_trustworthy"] is True
    assert facts["supplier_conversation_coverage"] == "observed_partial"
    assert facts["supplier_empty_trustworthy"] is False
    assert facts["global_coverage"] == "observed_partial"
    assert facts["linked_item_observations"] == 2
    assert facts["unlinked_lookalike_items"] == 2
    assert facts["logical_messages"] == 1


def test_the_gate_runner_keeps_the_eight_tool_boundary_and_swaps_only_the_prompt(gate, tmp_path):
    gate.build_memory_store(tmp_path / "memory.sqlite", tmp_path / "home")

    class Args:
        claude_bin = tmp_path / "claude"; python = sys.executable; model = "m"; proxy_port = 1
        timeout = 1; credentials = {}
    cfg = gate.make_config(Args, tmp_path / "iso", tmp_path / "wechat.sqlite",
                           memory=tmp_path / "memory.sqlite", consent_home=tmp_path / "home")
    runner = gate.LiveMemoryRunner(cfg, "q", err=io.StringIO())
    assert runner.expected_tools == gate.CLAUDE_EXPECTED_TOOLS_WITH_MEMORY
    assert len(runner.expected_tools) == 8
    argv = runner.argv("q")
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert argv[argv.index("--system-prompt") + 1] == gate.TEST_SYSTEM_PROMPT
    assert (ROOT / gate.DEFAULT_SKILL).read_bytes() not in b"".join(a.encode() for a in argv)
    assert argv[argv.index("--tools") + 1] == ""
    memory_env = cfg.mcp_config_document()["mcpServers"]["wechat_memory"]["env"]
    assert memory_env["HOME"] == str(tmp_path / "home")
    assert not Path(memory_env["PATH"], "defaults").exists()
    assert "MEMORY" not in " ".join(cfg.child_env("http://x"))


def test_the_preflight_refusal_never_launches_claude(gate, tmp_path):
    gate.build_memory_store(tmp_path / "memory.sqlite", tmp_path / "home")

    class Args:
        claude_bin = tmp_path / "claude"; python = sys.executable; model = "m"; proxy_port = 1
        timeout = 1; credentials = {}
    record = gate.preflight_refusal(Args, tmp_path, tmp_path / "wechat.sqlite", tmp_path / "home")
    assert record["aborted"] and "memory_store_missing" in record["aborted"]
    assert record["claude_launched"] is False
    assert record["mcp_config_written"] is False
    assert record["proxy_state_written"] is False
    assert record["residue_after_cleanup"] == 0
    assert record["path_in_message"] is False


def test_result_summaries_carry_no_message_text(gate):
    block = {"type": "tool_result", "content": [{"type": "text", "text":
             '{"ok": true, "items": [{"citation": {"canonical_message_id": "msg:1"}, "text": "秘密"}],'
             ' "coverage": {"status": "observed_complete", "trustworthy_empty": false, "required_sources": ["visual"]},'
             ' "truncated": false, "query_scope": {"kind": "search"}}'}]}
    summary = gate._summarise_result(block)
    assert summary == {"parsed": True, "ok": True, "kind": "search", "items": 1, "citations_present": True,
                       "coverage_status": "observed_complete", "trustworthy_empty": False,
                       "required_sources": ["visual"]}
    assert "秘密" not in str(summary)
