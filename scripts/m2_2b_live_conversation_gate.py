#!/usr/bin/env python3
"""M2.2b live conversation-discovery gate — real Claude Code, synthetic memory.

Reuses the M2.2a harness (runner subclass, isolation, probes, refusal) with a
corpus built for discovery: one uniquely named conversation, two distinct
conversations sharing a display name, one explicitly linked logical
conversation, and searchable messages in each. Two live tasks: resolve a
unique title and answer from memory; meet an ambiguous title and refuse to
guess. Probes assert exactly four (default) and exactly nine (memory).

Evidence recorded: tool names, counts, whether ``memory_conversations`` was
used, unique/ambiguous outcome, whether a later retrieval used the selected
canonical id, citation presence, coverage, residue. Never a credential, a
private path, or an environment dump. SKILL.md is not used.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

spec = importlib.util.spec_from_file_location("m2_2a_gate", HERE / "m2_2a_live_memory_gate.py")
m22a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m22a)

from agent_runner import DigestResult, ShadowError  # noqa: E402
from runners.claude import (CLAUDE_EXPECTED_TOOLS, CLAUDE_EXPECTED_TOOLS_WITH_MEMORY,  # noqa: E402
                            DEFAULT_MODEL, MEMORY_TOOLS, ClaudeRunner)
from scenarios import SCENARIOS_BY_KEY, build_database  # noqa: E402

import memory_consent as consent  # noqa: E402
import memory_identity as identity  # noqa: E402
from memory_ingest import MemoryIngestor  # noqa: E402
from memory_query import MemoryQueryService  # noqa: E402
from memory_store import (COVERAGE_COMPLETE, LINK_KIND_CONVERSATION, LINK_OPERATOR,  # noqa: E402
                          CoverageRecord, MemoryStore)
from message_source import SOURCE_DATABASE, SOURCE_VISUAL, NormalizedConversation  # noqa: E402

BASE = m22a.BASE
UNIQUE, AMBIG_A, AMBIG_B, LINKED = 21, 22, 23, 24

CORPUS = [
    (301, UNIQUE, "周晨", "other", "设计评审定在周四上午十点，A 栋 302。", 100),
    (302, UNIQUE, "李默", "other", "收到，我把原型带过去。", 130),
    (401, AMBIG_A, "赵婷", "other", "发布日期定在 11 月 3 日。", 200),
    (402, AMBIG_A, "周晨", "other", "那营销物料 10 月底前要交。", 230),
    (501, AMBIG_B, "孙磊", "other", "发布日期还没定，等供应商回复。", 300),
    (502, AMBIG_B, "赵婷", "other", "先按 12 月准备。", 330),
    (601, LINKED, "钱蓉", "other", "报销单周五前交给我。", 400),
]


def build_memory_store(path: Path, home: Path) -> dict:
    m22a.write_consent(home, allowed=True)
    decision = consent.resolve_consent(
        {consent.MEMORY_ENABLED_ENV: "1", consent.MEMORY_DB_PATH_ENV: str(path)},
        lambda: m22a.read_consent(home))
    with MemoryStore.open(decision) as store:
        ingestor = MemoryIngestor(store)
        ingestor.ingest(
            SOURCE_VISUAL, [m22a._message(*row) for row in CORPUS],
            conversations=[
                NormalizedConversation(UNIQUE, "设计评审群", BASE, BASE + 130, SOURCE_VISUAL),
                NormalizedConversation(AMBIG_A, "产品群", BASE, BASE + 230, SOURCE_VISUAL),
                NormalizedConversation(AMBIG_B, "产品群", BASE, BASE + 330, SOURCE_VISUAL),
                NormalizedConversation(LINKED, "财务对接", BASE, BASE + 400, SOURCE_VISUAL),
            ],
            coverage=[CoverageRecord(source=SOURCE_VISUAL, status=COVERAGE_COMPLETE)], now=BASE + 2000)
        ingestor.ingest(
            SOURCE_DATABASE,
            [m22a._message(9601, LINKED, "钱蓉", "other", "报销单周五前交给我。", 400, source=SOURCE_DATABASE)],
            conversations=[NormalizedConversation(LINKED, "财务对接", None, BASE + 401, SOURCE_DATABASE)],
            coverage=[CoverageRecord(source=SOURCE_DATABASE, status=COVERAGE_COMPLETE)], now=BASE + 2001)
        logical = store.link_observation(
            kind=LINK_KIND_CONVERSATION, observation_canonical_id=identity.conversation_canonical_id(SOURCE_VISUAL, str(LINKED)),
            logical_id=None, basis=LINK_OPERATOR, asserted_by="gate", now=BASE + 2002)
        store.link_observation(
            kind=LINK_KIND_CONVERSATION, observation_canonical_id=identity.conversation_canonical_id(SOURCE_DATABASE, str(LINKED)),
            logical_id=logical, basis=LINK_OPERATOR, asserted_by="gate", now=BASE + 2002)
        service = MemoryQueryService(store)
        return {
            "messages": store.counts()["messages"],
            "conversations": store.counts()["conversations"],
            "unique_candidates": len(service.conversations(name="设计评审群").items),
            "ambiguous_candidates": len(service.conversations(name="产品群").items),
            "linked_candidate_observations": len(service.conversations(name="财务对接").items[0].observations),
            "linked_candidates": len(service.conversations(name="财务对接").items),
        }


TEST_SYSTEM_PROMPT = """你是一个只读的记忆查询助手，用于一次合成数据测试。
你可以使用五个只读记忆工具：memory_conversations、memory_search、memory_timeline、memory_context、memory_recent。
规则：
1. 用户用会话名称（标题）提问时，先用 memory_conversations(name=...) 找到候选会话，
   再用候选的 canonical_conversation_id 作为 conversation_id 去查 memory_search / memory_timeline / memory_recent。
2. 如果 memory_conversations 返回多个候选（ambiguous 为 true），不要自行挑选一个：
   要么向用户说明有哪些候选并请其指定，要么分别说明每个候选的情况；绝不能把一个候选当成唯一答案。
3. 回答必须基于工具返回的证据；不要编造。注意 coverage：trustworthy_empty 为 false 时不能断言“从未发生”。
4. 回答用中文，简短，最后一行写出你依据的 canonical_message_id（若有）。
不要使用其它工具。"""

TASKS = {
    "unique_title": "「设计评审群」里评审定在什么时候、在哪里？",
    "ambiguous_title": "「产品群」里发布日期定了吗？",
}


class LiveConversationRunner(m22a.LiveMemoryRunner):
    """M2.2a runner plus: records the conversation_id values later calls used.

    Ids are digests, never content, so recording them is sanitized evidence.
    """

    def skill_text(self) -> str:
        return TEST_SYSTEM_PROMPT

    def digest(self) -> DigestResult:
        try:
            out = self._claude(self.prompt)
        finally:
            for name in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"):
                self.cfg.extra_env.pop(name, None)
        calls, results, final, run_id, is_error = [], [], "", None, False
        for line in (out.stdout or "").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "assistant":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        inp = block.get("input") or {}
                        calls.append({"name": block.get("name"), "arguments": sorted(inp.keys()),
                                      "conversation_id": inp.get("conversation_id"),
                                      "name_query": inp.get("name")})
            elif event.get("type") == "user":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "tool_result":
                        results.append(_summarise(block))
            elif event.get("type") == "result":
                final = (event.get("result") or "").strip()
                run_id = event.get("session_id")
                is_error = bool(event.get("is_error"))
        assert self._proxy is not None
        state = self._proxy.state
        diag = {"returncode": out.returncode, "is_error": is_error, "tool_calls": calls,
                "tool_results": results,
                "proxy": {"requests": state.requests, "tools_bearing": state.tools_bearing,
                          "violations": state.violations}}
        if state.violations:
            raise ShadowError(f"tool boundary: {state.violations} violating request(s)")
        rc = out.returncode or (1 if is_error else 0)
        return DigestResult(text=final, run_id=run_id, returncode=rc, diagnostics=diag)


def _summarise(block: dict) -> dict:
    base = m22a._summarise_result(block)
    content = block.get("content")
    text = "".join(c.get("text", "") for c in content if isinstance(c, dict)) if isinstance(content, list) else (content or "")
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return base
    if isinstance(payload, dict) and payload.get("ok") and (payload.get("query_scope") or {}).get("kind") == "conversations":
        return {"parsed": True, "ok": True, "kind": "conversations", "candidates": payload.get("candidates"),
                "unique": payload.get("unique"), "ambiguous": payload.get("ambiguous"),
                "candidate_ids": [i.get("canonical_conversation_id") for i in payload.get("items", [])],
                "matches": [i.get("match") for i in payload.get("items", [])],
                "coverage_status": (payload.get("coverage") or {}).get("status")}
    return base


def live_task(args, work: Path, key: str, memory_db: Path, wechat_db: Path, consent_home: Path) -> dict:
    home = work / f"home_{key}"
    cfg = m22a.make_config(args, home, wechat_db, memory=memory_db, consent_home=consent_home)
    runner = LiveConversationRunner(cfg, TASKS[key], err=io.StringIO())
    record: dict = {"question": TASKS[key]}
    status = 0
    try:
        runner.preflight()
        observed = runner.assert_tool_boundary()
        record["observed_count"] = len(observed)
        record["exact_nine"] = observed == set(CLAUDE_EXPECTED_TOOLS_WITH_MEMORY)
        result = runner.digest()
        calls = result.diagnostics["tool_calls"]
        discovery = [r for r in result.diagnostics["tool_results"] if r.get("kind") == "conversations"]
        candidate_ids = {i for r in discovery for i in r.get("candidate_ids", [])}
        later_ids = [c["conversation_id"] for c in calls if c.get("conversation_id")]
        record.update({
            "returncode": result.returncode, "answer": result.text,
            "tool_calls": [{"name": c["name"], "arguments": c["arguments"]} for c in calls],
            "memory_tools_used": sorted({c["name"].replace("mcp__wechat_memory__", "") for c in calls
                                         if any(t in (c["name"] or "") for t in MEMORY_TOOLS)}),
            "used_memory_conversations": any((c["name"] or "").endswith("memory_conversations") for c in calls),
            "discovery_results": discovery,
            "later_calls_used_candidate_id": bool(later_ids) and all(i in candidate_ids for i in later_ids),
            "later_conversation_ids_count": len(later_ids),
            "tool_results": result.diagnostics["tool_results"],
            "proxy": result.diagnostics["proxy"],
        })
        if result.returncode != 0 or not result.text:
            status = 1
    except ShadowError as exc:
        record["aborted"] = str(exc)
        status = 2
    finally:
        runner.cleanup(None)
        record["residue_after_cleanup"] = len(runner.residue())
    record["status"] = status
    print(f"=== {key} ===\nQ: {TASKS[key]}\nA: {record.get('answer', '<none>')}\n")
    return record


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
    ap.add_argument("--only", action="append", choices=("default", "memory", *TASKS))
    args = ap.parse_args(argv)
    args.work.mkdir(parents=True, exist_ok=True)
    args.credentials = m22a.credentials(args)
    want = lambda k: not args.only or k in args.only  # noqa: E731

    wechat_db = build_database(args.work / "wechat.sqlite", SCENARIOS_BY_KEY["A_unanswered_question"])
    consent_home = args.work / "consent_home"
    memory_db = args.work / "memory.sqlite"
    report: dict = {"model": args.model, "corpus": build_memory_store(memory_db, consent_home), "runs": {}}
    report["isolation"] = m22a.isolation_checks(
        m22a.make_config(args, args.work / "home_iso", wechat_db, memory=memory_db, consent_home=consent_home), memory_db)
    statuses = []
    try:
        if want("default"):
            runner = ClaudeRunner(m22a.make_config(args, args.work / "home_default", wechat_db, memory=None, consent_home=None))
            report["runs"]["default_probe"] = m22a.wire_probe(runner, CLAUDE_EXPECTED_TOOLS)
            statuses.append(report["runs"]["default_probe"]["status"])
        if want("memory"):
            runner = ClaudeRunner(m22a.make_config(args, args.work / "home_memory", wechat_db, memory=memory_db, consent_home=consent_home))
            report["runs"]["memory_probe"] = m22a.wire_probe(runner, CLAUDE_EXPECTED_TOOLS_WITH_MEMORY)
            statuses.append(report["runs"]["memory_probe"]["status"])
        for key in TASKS:
            if want(key):
                report["runs"][key] = live_task(args, args.work, key, memory_db, wechat_db, consent_home)
                statuses.append(report["runs"][key]["status"])
    finally:
        args.report.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    worst = max(statuses) if statuses else 1
    print(f"gate: worst exit {worst}; report at {args.report}", file=sys.stderr)
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
