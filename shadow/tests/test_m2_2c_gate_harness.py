"""Offline checks of the M2.2c freshness gate harness. No Claude Code process."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def gate():
    spec = importlib.util.spec_from_file_location("m2_2c_gate", ROOT / "scripts" / "m2_2c_live_freshness_gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    for name in ("memory_consent", "memory_identity", "memory_ingest", "memory_query", "memory_store",
                 "memory_freshness", "message_source", "scenarios", "m2_2a_gate"):
        sys.modules.pop(name, None)


def test_the_corpus_separates_sync_observed_and_latest(gate, tmp_path):
    facts = gate.build_memory_store(tmp_path / "memory.sqlite", tmp_path / "home")
    assert facts["latest_message_at"] < facts["observed_through"] == facts["complete_through"] < facts["last_succeeded_at"] < facts["synthetic_now"]
    assert facts["synthetic_now"] - facts["last_succeeded_at"] > 6 * gate.DAY


def test_the_prompt_states_the_synthetic_now_and_the_semantics(gate):
    assert str(int(gate.NOW)) in gate.TEST_SYSTEM_PROMPT
    for field in ("observed_through", "last_succeeded_at", "latest_message_at", "trustworthy_empty"):
        assert field in gate.TEST_SYSTEM_PROMPT
    assert "memory_conversations" in gate.TEST_SYSTEM_PROMPT


def test_freshness_summaries_carry_timestamps_not_text(gate):
    block = {"type": "tool_result", "content": [{"type": "text", "text":
             '{"ok": true, "items": [{"citation": {"canonical_message_id": "msg:1"}, "text": "秘密"}],'
             ' "coverage": {"status": "observed_complete", "trustworthy_empty": false, "required_sources": ["visual"]},'
             ' "truncated": false, "query_scope": {"kind": "search"},'
             ' "freshness": {"sources": {"visual": {"observed_through": 5.0, "last_succeeded_at": 6.0, "latest_message_at": 4.0}}}}'}]}
    summary = gate._with_freshness(gate.m22a._summarise_result(block), block)
    assert (summary["freshness_present"], summary["observed_through"], summary["last_succeeded_at"], summary["latest_message_at"]) == (True, 5.0, 6.0, 4.0)
    assert "秘密" not in str(summary)
