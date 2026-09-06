#!/usr/bin/env python3
"""M2.2a live memory agent gate — real Claude Code, synthetic memory only.

Everything this script reads is built in a scratch directory from invented
content: a synthetic WeChat store for the bridge, a synthetic v2 memory store
for the memory server, and a synthetic app consent state in an isolated HOME.
No real WeChat data, no production store, no real preference domain.

Runs, in order:

1. **default wire probe** — ``ClaudeRunner`` in default mode, probe turn only
   (rejected by the proxy before any provider traffic): exactly the four
   bridge tools observed on the wire.
2. **memory wire probe + live tasks** — memory mode: exactly eight tools
   observed, then four short real turns against the synthetic memory:
   search+evidence, context recovery, trustworthy empty, incomplete empty.
3. **preflight refusal** — memory explicitly requested with an invalid
   configuration: Claude Code must never launch and nothing may remain.

What is recorded (``--report``): tool names on the wire, call counts, which
memory tools were used, coverage states returned, whether citations were
present, exit codes, residue counts, and the final answers (synthetic, so
they may be quoted). Never a credential, a private path, or an environment
dump. SKILL.md is not used and not modified: the system prompt is a one-off
test prompt defined here.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import plistlib
import stat
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for sub in ("shadow", "memory", "bridge", ".hermes/skills/wechat-digest/evaluation"):
    sys.path.insert(0, str(REPO / sub))

from agent_runner import (DigestResult, ShadowError, collect_pass_env,  # noqa: E402
                          read_keychain_token, reject_secret_env_args)
from runners.claude import (CLAUDE_EXPECTED_TOOLS, CLAUDE_EXPECTED_TOOLS_WITH_MEMORY,  # noqa: E402
                            DEFAULT_MODEL, DEFAULT_SKILL, ClaudeConfig, ClaudeRunner,
                            MEMORY_TOOLS)
from scenarios import SCENARIOS_BY_KEY, build_database  # noqa: E402

import memory_consent as consent  # noqa: E402
import memory_identity as identity  # noqa: E402
from memory_ingest import MemoryIngestor  # noqa: E402
from memory_query import MemoryQueryService  # noqa: E402
from memory_store import (COVERAGE_COMPLETE, COVERAGE_PARTIAL, LINK_KIND_MESSAGE,  # noqa: E402
                          LINK_OPERATOR, CoverageRecord, MemoryStore)
from message_source import (SOURCE_DATABASE, SOURCE_VISUAL, NormalizedConversation,  # noqa: E402
                            NormalizedMessage)

MEMORY_SERVER = REPO / "memory" / "wechat_memory_mcp.py"
BASE = 1_757_000_000.0  # synthetic epoch; nothing real happened then

# --- Synthetic corpus (all names and content invented) ------------------------

PRODUCT = 7   # 「产品组」 — fully covered
SUPPLIER = 8  # 「供应商群」 — partially observed

CORPUS = [
    # (source id, conversation, sender, ownership, text, offset seconds)
    (101, PRODUCT, "林晓", "other", "季度评审改到 10 月 14 日下午三点，会议室 B204。", 100),
    (102, PRODUCT, "陈伟", "other", "收到。", 130),
    (103, PRODUCT, "陈伟", "other", "那份合同要不要今天签？", 400),
    (104, PRODUCT, "林晓", "other", "先别签，法务说第七条的违约条款有问题，等他们改完再说。", 430),
    (105, PRODUCT, "陈伟", "other", "好的，那我先压着。", 460),
    (106, PRODUCT, "林晓", "other", "预算表已经发到你邮箱了。", 700),   # linked with db 9106
    (107, PRODUCT, "林晓", "other", "下午同步一下进度。", 800),           # unlinked lookalike A
    (108, PRODUCT, "林晓", "other", "下午同步一下进度。", 1500),          # unlinked lookalike B
    (201, SUPPLIER, "王芳", "other", "样品下周才能寄出。", 900),
    (202, SUPPLIER, "王芳", "other", "运费到时候再确认。", 950),
]

def _message(sid, conv, sender, ownership, text, offset, *, source=SOURCE_VISUAL):
    visual = source == SOURCE_VISUAL
    return NormalizedMessage(
        id=sid, conversation_id=conv, sequence=sid, sender=sender, ownership=ownership,
        visible_time="今天" if visual else None, text=text, kind="text",
        confidence=0.93 if visual else 1.0, first_observed_at=BASE + offset, source=source,
    )


def build_memory_store(path: Path, home: Path) -> dict:
    """The synthetic v2 memory store, and its facts as sanitized evidence."""
    write_consent(home, allowed=True)
    decision = consent.resolve_consent(
        {consent.MEMORY_ENABLED_ENV: "1", consent.MEMORY_DB_PATH_ENV: str(path)},
        lambda: read_consent(home),
    )
    with MemoryStore.open(decision) as store:
        ingestor = MemoryIngestor(store)
        ingestor.ingest(
            SOURCE_VISUAL,
            [_message(*row) for row in CORPUS],
            conversations=[
                NormalizedConversation(PRODUCT, "产品组", BASE, BASE + 1500, SOURCE_VISUAL),
                NormalizedConversation(SUPPLIER, "供应商群", BASE, BASE + 950, SOURCE_VISUAL),
            ],
            # Source-wide complete first; the supplier-group partial is recorded
            # later so it is what answers for that conversation and for any
            # question that does not scope itself to one conversation.
            coverage=[CoverageRecord(source=SOURCE_VISUAL, status=COVERAGE_COMPLETE)],
            now=BASE + 2000,
        )
        ingestor.ingest(
            SOURCE_VISUAL, [],
            coverage=[CoverageRecord(
                source=SOURCE_VISUAL, status=COVERAGE_PARTIAL,
                conversation_canonical_id=identity.conversation_canonical_id(SOURCE_VISUAL, str(SUPPLIER)),
                reason="limit_reached")],
            now=BASE + 2001,
        )
        # A second reader's observation of the budget message, explicitly linked.
        ingestor.ingest(
            SOURCE_DATABASE,
            [_message(9106, PRODUCT, "林晓", "other", "预算表已经发到你邮箱了。", 700, source=SOURCE_DATABASE)],
            coverage=[CoverageRecord(source=SOURCE_DATABASE, status=COVERAGE_COMPLETE)],
            now=BASE + 2002,
        )
        visual_106 = identity.message_canonical_id_from_source(SOURCE_VISUAL, "106")
        db_9106 = identity.message_canonical_id_from_source(SOURCE_DATABASE, "9106")
        logical = store.link_observation(kind=LINK_KIND_MESSAGE, observation_canonical_id=visual_106,
                                         logical_id=None, basis=LINK_OPERATOR, asserted_by="gate", now=BASE + 2003)
        store.link_observation(kind=LINK_KIND_MESSAGE, observation_canonical_id=db_9106,
                               logical_id=logical, basis=LINK_OPERATOR, asserted_by="gate", now=BASE + 2003)
        service = MemoryQueryService(store)
        product = identity.conversation_canonical_id(SOURCE_VISUAL, str(PRODUCT))
        supplier = identity.conversation_canonical_id(SOURCE_VISUAL, str(SUPPLIER))
        facts = {
            "messages": store.counts()["messages"],
            "logical_messages": store.counts()["logical_messages"],
            "product_conversation_coverage": service.search(text="发票", conversation_canonical_id=product).coverage.status,
            "product_empty_trustworthy": service.search(text="发票", conversation_canonical_id=product).is_empty_and_trustworthy,
            "supplier_conversation_coverage": service.search(text="报销", conversation_canonical_id=supplier).coverage.status,
            "supplier_empty_trustworthy": service.search(text="报销", conversation_canonical_id=supplier).is_empty_and_trustworthy,
            "global_coverage": service.search(text="报销").coverage.status,
            "linked_item_observations": len(service.search(text="预算表").items[0].observations),
            "unlinked_lookalike_items": len(service.search(text="同步一下进度").items),
        }
    return facts


# --- Consent in an isolated HOME ----------------------------------------------

def write_consent(home: Path, *, allowed: bool, generation: int = 1) -> None:
    prefs = home / "Library" / "Preferences"
    prefs.mkdir(parents=True, exist_ok=True)
    (prefs / f"{consent.APP_PREFERENCE_DOMAIN}.plist").write_bytes(plistlib.dumps({
        consent.CONSENT_STATE_KEY: {
            "version": 1, "allowsLocalMessageStorage": allowed, "allowsMemoryStorage": allowed,
            "generation": generation, "updatedAt": BASE,
        }}))


def read_consent(home: Path):
    path = home / "Library" / "Preferences" / f"{consent.APP_PREFERENCE_DOMAIN}.plist"
    if not path.exists():
        return None
    with open(path, "rb") as handle:
        return plistlib.load(handle).get(consent.CONSENT_STATE_KEY)


# --- One-off test prompt (SKILL.md is neither used nor modified) --------------

TEST_SYSTEM_PROMPT = """你是一个只读的记忆查询助手，用于一次合成数据测试。
你可以使用四个只读记忆工具：memory_search、memory_timeline、memory_context、memory_recent。
它们返回的每条结果都带有 citation（引用）和 coverage（覆盖度）。
规则：
1. 回答必须基于工具返回的证据；不要编造。
2. 如果需要把查询限定在某个会话，conversation_id 请使用某条结果 citation 里的 canonical_conversation_id。
3. 当没有找到匹配结果时，必须区分两种情况：
   - coverage.trustworthy_empty 为 true：可以说“记录里没有提到”；
   - coverage.trustworthy_empty 为 false（status 为 observed_partial / not_observed / unavailable）：
     只能说“在已观察到的记录里没有找到”，并明确说明覆盖不完整，不能断言“从未发生”。
4. 回答用中文，简短，最后一行写出你依据的 canonical_message_id（若有）。
不要使用其它工具。"""

TASKS = {
    "search_evidence": "季度评审改到什么时候、在哪里？",
    "context_recovery": "那份合同后来签了吗？最后是怎么决定的？",
    "trustworthy_empty": "在「产品组」这个会话里，有没有人提到过发票？请先找到该会话的 conversation_id 再查。",
    "incomplete_empty": "在「供应商群」这个会话里，有没有人提到过报销？请先找到该会话的 conversation_id 再查。",
}


class LiveMemoryRunner(ClaudeRunner):
    """Gate-only: one-off system prompt, stream output for sanitized evidence.

    The wire boundary, proxy, isolation and purge are the unchanged runner's.
    """

    def __init__(self, cfg, prompt: str, **kw) -> None:
        super().__init__(cfg, **kw)
        self.prompt = prompt

    def skill_text(self) -> str:
        return TEST_SYSTEM_PROMPT

    def argv(self, prompt: str) -> list[str]:
        base = super().argv(prompt)
        i = base.index("--output-format")
        base[i + 1] = "stream-json"
        base.insert(i + 2, "--verbose")
        return base

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
                        calls.append({"name": block.get("name"),
                                      "arguments": sorted((block.get("input") or {}).keys())})
            elif event.get("type") == "user":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "tool_result":
                        results.append(_summarise_result(block))
            elif event.get("type") == "result":
                final = (event.get("result") or "").strip()
                run_id = event.get("session_id")
                is_error = bool(event.get("is_error"))
        assert self._proxy is not None
        state = self._proxy.state
        diag = {
            "returncode": out.returncode, "is_error": is_error,
            "tool_calls": calls, "tool_results": results,
            "proxy": {"requests": state.requests, "tools_bearing": state.tools_bearing,
                      "violations": state.violations},
        }
        if state.violations:
            raise ShadowError(f"tool boundary: {state.violations} violating request(s)")
        rc = out.returncode or (1 if is_error else 0)
        return DigestResult(text=final, run_id=run_id, returncode=rc, diagnostics=diag)


def _summarise_result(block: dict) -> dict:
    """Coverage state and citation presence from a tool result. Never the text."""
    content = block.get("content")
    text = ""
    if isinstance(content, list):
        text = "".join(c.get("text", "") for c in content if isinstance(c, dict))
    elif isinstance(content, str):
        text = content
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {"parsed": False, "is_error": bool(block.get("is_error"))}
    if not isinstance(payload, dict):
        return {"parsed": False}
    summary = {"parsed": True, "ok": payload.get("ok")}
    if payload.get("ok"):
        coverage = payload.get("coverage") or {}
        items = payload.get("items") or []
        summary.update({
            "kind": (payload.get("query_scope") or {}).get("kind"),
            "items": len(items),
            "citations_present": all("citation" in i and i["citation"].get("canonical_message_id") for i in items),
            "coverage_status": coverage.get("status"),
            "trustworthy_empty": coverage.get("trustworthy_empty"),
            "required_sources": coverage.get("required_sources"),
        })
    else:
        summary["state"] = payload.get("state")
    return summary


# --- Runs ---------------------------------------------------------------------

def make_config(args, home: Path, db: Path, *, memory: Path | None, consent_home: Path | None) -> ClaudeConfig:
    cfg = ClaudeConfig(
        claude_bin=args.claude_bin, python=args.python,
        bridge=REPO / "bridge" / "wechat_companion_mcp.py", db_path=db,
        skill=REPO / DEFAULT_SKILL, isolated_home=home, model=args.model,
        proxy_port=args.proxy_port, extra_env=dict(args.credentials), timeout=args.timeout,
        memory_enabled=memory is not None, memory_db_path=memory,
        memory_server=MEMORY_SERVER if memory is not None else None,
        memory_consent_reader=(lambda: read_consent(consent_home)) if consent_home else None,
    )
    if memory is not None:
        # The memory server must read the *isolated* consent state: give its
        # process the isolated HOME and a PATH with no `defaults` tool, so the
        # plist fallback answers. Gate-only; the runner itself adds nothing.
        nopath = home.parent / "nopath"
        nopath.mkdir(exist_ok=True)
        original = cfg.mcp_config_document

        def document():
            doc = original()
            doc["mcpServers"]["wechat_memory"]["env"].update({"HOME": str(consent_home), "PATH": str(nopath)})
            return doc
        cfg.mcp_config_document = document  # type: ignore[method-assign]
    return cfg


def wire_probe(runner: ClaudeRunner, expected: frozenset[str]) -> dict:
    """preflight → probe → cleanup. No provider traffic, no digest turn."""
    err = io.StringIO()
    runner._err = err
    record: dict = {"expected": sorted(expected), "expected_count": len(expected)}
    try:
        runner.preflight()
        observed = runner.assert_tool_boundary()
        record.update({"observed": sorted(observed), "observed_count": len(observed),
                       "exact": observed == set(expected), "violations": runner._proxy.state.violations})
        status = 0
    except ShadowError as exc:
        record["aborted"] = str(exc)
        status = 2
    finally:
        runner.cleanup(None)
        record["residue_after_cleanup"] = len(runner.residue())
    record["status"] = status
    return record


def live_task(args, work: Path, key: str, memory_db: Path, wechat_db: Path, consent_home: Path) -> dict:
    home = work / f"home_{key}"
    cfg = make_config(args, home, wechat_db, memory=memory_db, consent_home=consent_home)
    runner = LiveMemoryRunner(cfg, TASKS[key], err=io.StringIO())
    record: dict = {"question": TASKS[key]}
    status = 0
    try:
        runner.preflight()
        observed = runner.assert_tool_boundary()
        record["observed_count"] = len(observed)
        record["exact_eight"] = observed == set(CLAUDE_EXPECTED_TOOLS_WITH_MEMORY)
        result = runner.digest()
        record.update({
            "returncode": result.returncode,
            "answer": result.text,
            "tool_calls": result.diagnostics["tool_calls"],
            "memory_tools_used": sorted({c["name"] for c in result.diagnostics["tool_calls"]
                                         if any(t in (c["name"] or "") for t in MEMORY_TOOLS)}),
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


def preflight_refusal(args, work: Path, wechat_db: Path, consent_home: Path) -> dict:
    """Memory explicitly requested, store missing: Claude Code must never start."""
    home = work / "home_refusal"
    tripwire = work / "tripwire"
    marker = work / "claude_was_launched"
    tripwire.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 0\n")
    tripwire.chmod(tripwire.stat().st_mode | stat.S_IXUSR)
    cfg = make_config(args, home, wechat_db, memory=work / "absent_memory.sqlite", consent_home=consent_home)
    cfg.claude_bin = tripwire
    runner = ClaudeRunner(cfg, err=io.StringIO())
    record: dict = {}
    try:
        runner.preflight()
        runner.assert_tool_boundary()
        record["aborted"] = None
    except ShadowError as exc:
        record["aborted"] = str(exc)
    finally:
        runner.cleanup(None)
    record.update({
        "claude_launched": marker.exists(),
        "mcp_config_written": cfg.mcp_config.exists(),
        "proxy_state_written": cfg.proxy_state.exists(),
        "residue_after_cleanup": len(runner.residue()),
        "path_in_message": str(work) in (record["aborted"] or ""),
    })
    return record


def isolation_checks(cfg: ClaudeConfig, memory_db: Path) -> dict:
    doc = cfg.mcp_config_document()
    child = cfg.child_env("http://127.0.0.1:0")
    return {
        "servers": sorted(doc["mcpServers"]),
        "memory_path_in_memory_server_env": str(memory_db) in json.dumps(doc["mcpServers"]["wechat_memory"]["env"]),
        "memory_path_in_bridge_env": str(memory_db) in json.dumps(doc["mcpServers"]["wechat_companion"]["env"]),
        "memory_path_in_claude_env": str(memory_db) in json.dumps(child),
        "memory_vars_in_claude_env": sorted(k for k in child if "MEMORY" in k),
    }


def credentials(args) -> dict:
    reject_secret_env_args(args.env)
    extra = dict(item.partition("=")[::2] for item in args.env)
    extra.update(collect_pass_env(args.pass_env, os.environ))
    if args.claude_oauth_from_keychain:
        extra.update(read_keychain_token())
    return extra


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--claude-bin", required=True, type=Path)
    ap.add_argument("--python", required=True, type=Path, help="interpreter with `mcp`")
    ap.add_argument("--work", required=True, type=Path)
    ap.add_argument("--report", required=True, type=Path)
    ap.add_argument("--env", action="append", default=[])
    ap.add_argument("--pass-env", action="append", default=[], metavar="NAME")
    ap.add_argument("--claude-oauth-from-keychain", action="store_true")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--proxy-port", type=int, default=8823)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--only", action="append",
                    choices=("default", "memory", "refusal", *TASKS))
    args = ap.parse_args(argv)
    args.work.mkdir(parents=True, exist_ok=True)
    args.credentials = credentials(args)
    want = lambda k: not args.only or k in args.only  # noqa: E731

    wechat_db = build_database(args.work / "wechat.sqlite", SCENARIOS_BY_KEY["A_unanswered_question"])
    consent_home = args.work / "consent_home"
    memory_db = args.work / "memory.sqlite"
    report: dict = {"model": args.model, "corpus": build_memory_store(memory_db, consent_home), "runs": {}}
    report["isolation"] = isolation_checks(
        make_config(args, args.work / "home_iso", wechat_db, memory=memory_db, consent_home=consent_home), memory_db)
    statuses = []
    try:
        if want("default"):
            runner = ClaudeRunner(make_config(args, args.work / "home_default", wechat_db, memory=None, consent_home=None))
            report["runs"]["default_probe"] = wire_probe(runner, CLAUDE_EXPECTED_TOOLS)
            statuses.append(report["runs"]["default_probe"]["status"])
        if want("memory"):
            runner = ClaudeRunner(make_config(args, args.work / "home_memory", wechat_db, memory=memory_db, consent_home=consent_home))
            report["runs"]["memory_probe"] = wire_probe(runner, CLAUDE_EXPECTED_TOOLS_WITH_MEMORY)
            statuses.append(report["runs"]["memory_probe"]["status"])
        for key in TASKS:
            if want(key):
                report["runs"][key] = live_task(args, args.work, key, memory_db, wechat_db, consent_home)
                statuses.append(report["runs"][key]["status"])
        if want("refusal"):
            report["runs"]["preflight_refusal"] = preflight_refusal(args, args.work, wechat_db, consent_home)
            r = report["runs"]["preflight_refusal"]
            statuses.append(0 if (r["aborted"] and not r["claude_launched"] and not r["mcp_config_written"]
                                  and r["residue_after_cleanup"] == 0) else 1)
    finally:
        args.report.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    worst = max(statuses) if statuses else 1
    print(f"gate: worst exit {worst}; report at {args.report}", file=sys.stderr)
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
