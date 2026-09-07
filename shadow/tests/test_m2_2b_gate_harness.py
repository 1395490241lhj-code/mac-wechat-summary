"""Offline checks of the M2.2b discovery gate harness. No Claude Code process."""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def gate():
    spec = importlib.util.spec_from_file_location("m2_2b_gate", ROOT / "scripts" / "m2_2b_live_conversation_gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    for name in ("memory_consent", "memory_identity", "memory_ingest", "memory_query", "memory_store",
                 "message_source", "scenarios", "m2_2a_gate"):
        sys.modules.pop(name, None)


def test_the_discovery_corpus_has_the_shapes_the_tasks_need(gate, tmp_path):
    facts = gate.build_memory_store(tmp_path / "memory.sqlite", tmp_path / "home")
    assert facts["unique_candidates"] == 1
    assert facts["ambiguous_candidates"] == 2
    assert facts["linked_candidates"] == 1 and facts["linked_candidate_observations"] == 2
    assert facts["conversations"] == 5 and facts["messages"] == 8


def test_the_gate_runner_is_the_nine_tool_runner_with_a_discovery_prompt(gate, tmp_path):
    gate.build_memory_store(tmp_path / "memory.sqlite", tmp_path / "home")

    class Args:
        claude_bin = tmp_path / "claude"; python = sys.executable; model = "m"; proxy_port = 1
        timeout = 1; credentials = {}
    cfg = gate.m22a.make_config(Args, tmp_path / "iso", tmp_path / "wechat.sqlite",
                                memory=tmp_path / "memory.sqlite", consent_home=tmp_path / "home")
    runner = gate.LiveConversationRunner(cfg, "q", err=io.StringIO())
    assert len(runner.expected_tools) == 9
    assert "mcp__wechat_memory__memory_conversations" in runner.expected_tools
    argv = runner.argv("q")
    assert argv[argv.index("--system-prompt") + 1] == gate.TEST_SYSTEM_PROMPT
    assert "memory_conversations" in gate.TEST_SYSTEM_PROMPT
    assert (ROOT / gate.m22a.DEFAULT_SKILL).read_bytes() not in b"".join(a.encode() for a in argv)


def test_discovery_result_summaries_carry_ids_not_names(gate):
    block = {"type": "tool_result", "content": [{"type": "text", "text":
             '{"ok": true, "items": [{"canonical_conversation_id": "conv:a", "display_name": "秘密群", "match": "exact"},'
             ' {"canonical_conversation_id": "conv:b", "display_name": "秘密群", "match": "exact"}],'
             ' "truncated": false, "query_scope": {"kind": "conversations"}, "coverage": {"status": "store_inventory"},'
             ' "candidates": 2, "unique": false, "ambiguous": true}'}]}
    summary = gate._summarise(block)
    assert summary["kind"] == "conversations" and summary["ambiguous"] is True
    assert summary["candidate_ids"] == ["conv:a", "conv:b"] and summary["matches"] == ["exact", "exact"]
    assert "秘密群" not in str(summary)
