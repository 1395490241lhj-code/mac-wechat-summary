#!/usr/bin/env python3
"""M2.2c live freshness gate — real Claude Code, synthetic memory only.

Reuses the M2.2a harness. Corpus: one conversation with complete historical
coverage over a bounded window, a last successful sync well before the
synthetic "now" the prompt states, and a latest message earlier than the
observed-through boundary. Three live turns check that the model separates
"nothing matched inside a completely covered window" from "memory cannot
speak to what happened after its observed-through boundary", and reads the
newest-message vs observed-through distinction as a fact. Probes assert
exactly four and exactly nine. SKILL.md is not used.
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
from memory_freshness import memory_freshness  # noqa: E402
from memory_ingest import MemoryIngestor  # noqa: E402
from memory_store import COVERAGE_COMPLETE, CoverageRecord, MemoryStore  # noqa: E402
from message_source import SOURCE_VISUAL, NormalizedConversation  # noqa: E402

DAY = 86_400.0
BASE = 1_757_000_000.0            # synthetic epoch
WINDOW_END = BASE + 1_000         # observed / complete through
LAST_SYNC = BASE + 2_000          # last successful sync finished
NOW = BASE + 7 * DAY              # the "current" moment the prompt states
CONV = 31

CORPUS = [
    (701, CONV, "林晓", "other", "合同第七条改完了，明天签。", 100),
    (702, CONV, "陈伟", "other", "好，明天上午签。", 130),
    (703, CONV, "林晓", "other", "预算表已发。", 400),   # latest message: BASE+400 < observed-through BASE+1000
]


def build_memory_store(path: Path, home: Path) -> dict:
    m22a.write_consent(home, allowed=True)
    decision = consent.resolve_consent(
        {consent.MEMORY_ENABLED_ENV: "1", consent.MEMORY_DB_PATH_ENV: str(path)}, lambda: m22a.read_consent(home))
    with MemoryStore.open(decision) as store:
        MemoryIngestor(store).ingest(
            SOURCE_VISUAL, [m22a._message(*row) for row in CORPUS],
            conversations=[NormalizedConversation(CONV, "项目组", BASE, BASE + 400, SOURCE_VISUAL)],
            coverage=[CoverageRecord(source=SOURCE_VISUAL, status=COVERAGE_COMPLETE, window_start=BASE, window_end=WINDOW_END)],
            now=LAST_SYNC)
        f = memory_freshness(store, generated_at=NOW).sources[0]
        return {"messages": store.counts()["messages"], "last_succeeded_at": f.last_succeeded_at,
                "observed_through": f.observed_through, "complete_through": f.complete_through,
                "latest_message_at": f.latest_message_at, "synthetic_now": NOW}


TEST_SYSTEM_PROMPT = f"""你是一个只读的记忆查询助手，用于一次合成数据测试。
当前时间（Unix 秒）：{int(NOW)}。
你可以使用五个只读记忆工具：memory_conversations、memory_search、memory_timeline、memory_context、memory_recent。
每个结果都带有 coverage（覆盖度）和 freshness（新鲜度）。二者含义不同：
- coverage 回答“所请求的那段数据被观察到了多少”；trustworthy_empty 为 true 时，可以说“记录里没有”。
- freshness 回答“记忆上次同步是什么时候、来源被观察到哪个时刻为止”：
  · sources.<来源>.observed_through：来源被观察到的最晚时刻；此后发生的事记忆无法知道；
  · sources.<来源>.last_succeeded_at：上次成功同步结束的时刻；
  · sources.<来源>.latest_message_at：已存储的最新一条消息的时间；它不是观察边界。
规则：
1. 只依据工具证据回答；不要编造。
2. 关于 observed_through 之后的时间，只能说“记忆没有覆盖到那之后”，不能断言没发生。
3. 关于 observed_through 之前且 trustworthy_empty 为 true 的窗口，可以说记录里没有提到。
4. latest_message_at 与 observed_through 之间没有存储消息，是一个事实（那段时间被观察了但没有消息），不是“不知道”。
5. 回答用中文，简短；给出你依据的时间戳字段值；最后一行写出依据的 canonical_message_id（若有）。
不要使用其它工具。"""

TASKS = {
    "after_boundary": f"最近三天（Unix {int(NOW - 3 * DAY)} 到 {int(NOW)}）「项目组」里有没有人提到合同变更？",
    "covered_empty": f"在 Unix {int(BASE)} 到 {int(WINDOW_END)} 这段时间里，「项目组」里有没有人提到发票？",
    "latest_vs_observed": "「项目组」里最新一条消息是什么时候？记忆对这个来源观察到什么时候为止？这两个时刻之间有没有存储的消息？",
}


class LiveFreshnessRunner(m22a.LiveMemoryRunner):
    def skill_text(self) -> str:
        return TEST_SYSTEM_PROMPT

    def digest(self) -> DigestResult:
        result = super().digest()
        result.diagnostics["tool_results"] = [_with_freshness(r, raw) for r, raw in
                                              zip(result.diagnostics["tool_results"], self._raw_results)]
        return result

    def _claude(self, prompt):
        out = super()._claude(prompt)
        self._raw_results = []
        for line in (out.stdout or "").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "user":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "tool_result":
                        self._raw_results.append(block)
        return out


def _with_freshness(summary: dict, block: dict) -> dict:
    content = block.get("content")
    text = "".join(c.get("text", "") for c in content if isinstance(c, dict)) if isinstance(content, list) else (content or "")
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return summary
    if not isinstance(payload, dict) or not payload.get("ok"):
        return summary
    fresh = payload.get("freshness") or {}
    source = (fresh.get("sources") or {}).get(SOURCE_VISUAL) or {}
    return {**summary, "freshness_present": bool(fresh),
            "observed_through": source.get("observed_through"),
            "last_succeeded_at": source.get("last_succeeded_at"),
            "latest_message_at": source.get("latest_message_at")}


def live_task(args, work: Path, key: str, memory_db: Path, wechat_db: Path, consent_home: Path) -> dict:
    home = work / f"home_{key}"
    cfg = m22a.make_config(args, home, wechat_db, memory=memory_db, consent_home=consent_home)
    runner = LiveFreshnessRunner(cfg, TASKS[key], err=io.StringIO())
    record: dict = {"question": TASKS[key]}
    status = 0
    try:
        runner.preflight()
        observed = runner.assert_tool_boundary()
        record["observed_count"] = len(observed)
        record["exact_nine"] = observed == set(CLAUDE_EXPECTED_TOOLS_WITH_MEMORY)
        result = runner.digest()
        results = result.diagnostics["tool_results"]
        record.update({
            "returncode": result.returncode, "answer": result.text,
            "tool_calls": result.diagnostics["tool_calls"],
            "memory_tools_used": sorted({c["name"].replace("mcp__wechat_memory__", "") for c in result.diagnostics["tool_calls"]
                                         if any(t in (c["name"] or "") for t in MEMORY_TOOLS)}),
            "tool_results": results,
            "freshness_present_on_all_ok_results": all(r.get("freshness_present") for r in results if r.get("ok")),
            "coverage_states": [r.get("coverage_status") for r in results if r.get("ok") and r.get("coverage_status")],
            "trustworthy_empty_states": [r.get("trustworthy_empty") for r in results if r.get("ok") and "trustworthy_empty" in r],
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
