#!/usr/bin/env python3
"""M2.4 — the real product path, end to end, operator-graded.

Runs the **production SKILL.md** against the **real canonical MemoryStore**
the app's own Sync Now filled, through normal canonical activation
(``--memory`` with no database path). The questions are written so that this
process never needs to read the user's messages: the model discovers the
conversations itself, and the operator -- who owns the data -- grades whether
the answers are right.

What is recorded is counts, booleans, tool names, coverage/freshness states,
citation-set membership and durations. **No message text, sender, chat name,
citation id, or path is written to the report.** The answers are printed to
stdout for the operator to read and grade, and are not persisted.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
spec = importlib.util.spec_from_file_location("m2_3_gate", HERE / "m2_3_skill_gate.py")
m23 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m23)
m22a = m23.m22a

import re as _re
import unicodedata

from agent_runner import ShadowError  # noqa: E402
from runners.claude import (CLAUDE_EXPECTED_TOOLS_WITH_MEMORY, DEFAULT_MODEL,  # noqa: E402
                            MEMORY_TOOLS, ClaudeConfig)

SKILL = REPO / ".hermes/skills/wechat-digest/SKILL.md"
REAL_HOME = Path.home()
STORE_DIR = REAL_HOME / "Library/Application Support/WeChatCompanion"

#: Deliberately self-locating: each question tells the model how to find its
#: own subject, so this process needs no knowledge of the content.
SCENARIOS = {
    "direct_recall": "记忆里现在有哪些会话？请挑其中一个，说明大家主要在讨论什么，并给出依据。",
    "contextual_recall": "在消息最多的那个会话里，讨论最后的结论或当前状态是什么？请结合上下文回答，并给出依据。",
    "coverage_honesty": "9 月 1 日到 9 月 4 日这几天，这些会话里聊了些什么？",
    "freshness_awareness": "今天是 2026 年 9 月 7 日。今天这些会话里有什么新消息？",
}


def _normalise(text: str) -> str:
    return unicodedata.normalize("NFC", text).replace(" ", "").replace("\u3000", "")


def check_grounding(answer: str) -> dict:
    """Compare the answer against the store **inside this process**.

    Only counts leave this function. The point is to test the claims without a
    human having to re-read their own chat history, and without the message
    text passing through anything that records it.

    Two checks:

    * **Quoted fidelity** — every span the answer puts in quotation marks
      should occur in some stored message. A quote that matches nothing is
      either a paraphrase presented as a quote, or an invention.
    * **Date claims** — any ``M月D日``/ISO date the answer asserts about when
      messages were *observed* is compared with the store's real span. The
      skill forbids deriving dates from ``first_observed_at`` at all, so a
      date outside the real span is a grounding error either way.
    """
    import sqlite3

    store = STORE_DIR / "memory.sqlite"
    if not store.exists():
        return {"checked": False}
    connection = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    corpus = _normalise(" \u241f ".join(
        row[0] or "" for row in connection.execute("SELECT text FROM messages")))
    span = connection.execute("SELECT MIN(timestamp), MAX(timestamp) FROM messages").fetchone()
    connection.close()

    quotes = [q for q in _re.findall(r"[「\"“']([^「」\"“”'\n]{4,60})[」\"”']", answer)]
    matched = sum(1 for q in quotes if _normalise(q) in corpus)

    import datetime
    real_days = {datetime.date.fromtimestamp(t).isoformat() for t in span if t}
    claimed = set()
    for month, day in _re.findall(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", answer):
        claimed.add(f"2026-{int(month):02d}-{int(day):02d}")
    for iso in _re.findall(r"20\d\d-\d\d-\d\d", answer):
        claimed.add(iso)
    # Dates the answer states that are not days the store actually holds.
    outside = sorted(claimed - real_days)
    return {
        "checked": True,
        "quoted_spans": len(quotes),
        "quotes_found_in_store": matched,
        "quotes_unmatched": len(quotes) - matched,
        "observation_days_in_store": len(real_days),
        "dates_asserted": len(claimed),
        "dates_not_in_store": len(outside),
    }


def make_config(args, isolated: Path) -> ClaudeConfig:
    """Canonical activation: memory enabled, **no** database path supplied."""
    cfg = ClaudeConfig(
        claude_bin=args.claude_bin, python=args.python,
        bridge=REPO / "bridge" / "wechat_companion_mcp.py",
        db_path=STORE_DIR / "messages.sqlite",
        skill=SKILL, isolated_home=isolated, model=args.model,
        proxy_port=args.proxy_port, extra_env=dict(args.credentials), timeout=args.timeout,
        memory_enabled=True, memory_db_path=None,
        memory_server=REPO / "memory" / "wechat_memory_mcp.py",
    )
    original = cfg.mcp_config_document

    def document():
        doc = original()
        # The memory server must read the *real* consent state and store.
        doc["mcpServers"]["wechat_memory"]["env"].update(
            {"HOME": str(REAL_HOME), "PATH": "/usr/bin:/bin"})
        return doc
    cfg.mcp_config_document = document  # type: ignore[method-assign]
    return cfg


def run(args, key: str, prompt: str, work: Path) -> tuple[dict, str]:
    cfg = make_config(args, work / f"home_{key}")
    runner = m23.SkillRunner(cfg, prompt, err=io.StringIO())
    record: dict = {"scenario": key}
    started = time.monotonic()
    answer = ""
    status = 0
    try:
        runner.preflight()
        observed = runner.assert_tool_boundary()
        record["observed_count"] = len(observed)
        record["exact_nine"] = observed == set(CLAUDE_EXPECTED_TOOLS_WITH_MEMORY)
        record["unexpected_tools"] = sorted(observed - set(CLAUDE_EXPECTED_TOOLS_WITH_MEMORY))
        result = runner.digest()
        answer = result.text
        calls = result.diagnostics["tool_calls"]
        citations = m23.grade_citations(
            answer, result.diagnostics.get("returned_message_ids", []),
            result.diagnostics.get("discovery_only_conversation_ids", []))
        # Counts only: never the ids themselves.
        citations.pop("fabricated_ids", None)
        record.update({
            "returncode": result.returncode,
            "answer_chars": len(answer),
            "total_tool_calls": len(calls),
            "memory_tool_calls": sum(
                1 for c in calls if any(t in (c["name"] or "") for t in MEMORY_TOOLS)),
            "memory_tools_used": sorted({
                c["name"].replace("mcp__wechat_memory__", "") for c in calls
                if any(t in (c["name"] or "") for t in MEMORY_TOOLS)}),
            "raw_tool_calls": sum(1 for c in calls if "wechat_companion" in (c["name"] or "")),
            "citations": {k: v for k, v in citations.items() if k != "fabricated"},
            "fabricated_count": len(citations.get("fabricated", [])),
            "coverage_states": sorted({r.get("coverage_status") for r in result.diagnostics["tool_results"]
                                       if r.get("ok") and r.get("coverage_status")}),
            "trustworthy_empty_seen": sorted({str(r.get("trustworthy_empty"))
                                              for r in result.diagnostics["tool_results"]
                                              if r.get("ok") and "trustworthy_empty" in r}),
            "proxy": result.diagnostics["proxy"],
            "grounding": check_grounding(answer),
        })
        if result.returncode != 0 or not answer:
            status = 1
    except ShadowError as exc:
        record["aborted"] = str(exc)
        status = 2
    finally:
        runner.cleanup(None)
        record["residue_after_cleanup"] = len(runner.residue())
        record["duration_seconds"] = round(time.monotonic() - started, 1)
    record["status"] = status
    return record, answer


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--claude-bin", required=True, type=Path)
    ap.add_argument("--python", required=True, type=Path)
    ap.add_argument("--work", required=True, type=Path)
    ap.add_argument("--report", required=True, type=Path)
    ap.add_argument("--env", action="append", default=[])
    ap.add_argument("--pass-env", action="append", default=[], metavar="NAME")
    ap.add_argument("--claude-oauth-from-keychain", action="store_true")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--proxy-port", type=int, default=8823)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--only", action="append", choices=tuple(SCENARIOS))
    args = ap.parse_args(argv)
    args.work.mkdir(parents=True, exist_ok=True)
    args.credentials = m22a.credentials(args)

    report: dict = {"model": args.model, "runs": {},
                    "skill_sha256": __import__("hashlib").sha256(SKILL.read_bytes()).hexdigest()}
    statuses = []
    try:
        for key, prompt in SCENARIOS.items():
            if args.only and key not in args.only:
                continue
            record, answer = run(args, key, prompt, args.work)
            report["runs"][key] = record
            statuses.append(record["status"])
            print("=" * 78)
            print(f"SCENARIO {key}\nQ: {prompt}\n\nA: {answer or '<none>'}\n")
    finally:
        args.report.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    worst = max(statuses) if statuses else 1
    print("=" * 78)
    print(f"gate: worst exit {worst}; sanitized report at {args.report}", file=sys.stderr)
    print("Operator: please grade the four answers above against your own data.", file=sys.stderr)
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
