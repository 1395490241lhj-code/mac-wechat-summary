#!/usr/bin/env python3
"""M2.2e live product-path gate — the whole normal flow, synthetic data only.

    packaged MemoryWorker sync  (what Sync Now runs)
        ↓
    app-owned canonical MemoryStore   (derived, never typed)
        ↓
    ClaudeRunner --memory             (no --memory-db-path)
        ↓
    real Memory MCP → real Claude Code → 9 read-only tools

Everything happens under an isolated home, so the user's own
``~/Library/Application Support/WeChatCompanion`` is never read or written:
the worker derives the canonical location from ``HOME``, and this process sets
``HOME`` to a scratch directory before resolving anything. The synthetic
message store is built there too.

Records tool names, counts, violations, which memory tools were used, whether
coverage/freshness/citations reached the model, and residue. Never a
credential, a private path, or an environment dump. ``SKILL.md`` is not used.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
spec = importlib.util.spec_from_file_location("m2_2a_gate", HERE / "m2_2a_live_memory_gate.py")
m22a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m22a)

from agent_runner import DigestResult, ShadowError  # noqa: E402
from runners.claude import (CLAUDE_EXPECTED_TOOLS, CLAUDE_EXPECTED_TOOLS_WITH_MEMORY,  # noqa: E402
                            DEFAULT_MODEL, MEMORY_TOOLS, ClaudeConfig, ClaudeRunner)
from scenarios import SCENARIOS_BY_KEY, build_database  # noqa: E402

WORKER = REPO / ".build/memory-worker/dist/MemoryWorker.app/Contents/MacOS/MemoryWorker"

#: Invented. A conversation the model must find by title, and a fact in it.
CONVERSATION = "研发周会"
MESSAGES = [
    (1, 1, "周敏", "other", "本周的发布窗口定在 11 月 6 日晚上十点。", 1_757_000_100.0),
    (2, 2, "郭凯", "other", "灰度先做 10%，没问题再全量。", 1_757_000_200.0),
    (3, 3, "周敏", "other", "回滚脚本我已经准备好了。", 1_757_000_300.0),
]


def build_message_store(home: Path) -> Path:
    """The app's own store, where the app would have written it."""
    path = home / "Library/Application Support/WeChatCompanion/messages.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(
        "PRAGMA user_version = 1;"
        "CREATE TABLE conversations (id INTEGER PRIMARY KEY, title TEXT,"
        " first_seen_at REAL, last_seen_at REAL);"
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, conversation_id INTEGER,"
        " sequence INTEGER, sender TEXT, ownership TEXT, visible_time TEXT, text TEXT,"
        " kind TEXT, confidence REAL, first_observed_at REAL);")
    connection.execute("INSERT INTO conversations VALUES (1, ?, ?, ?);",
                       (CONVERSATION, MESSAGES[0][5], MESSAGES[-1][5]))
    for identifier, sequence, sender, ownership, text, observed in MESSAGES:
        connection.execute(
            "INSERT INTO messages VALUES (?, 1, ?, ?, ?, '今天', ?, 'text', 0.9, ?);",
            (identifier, sequence, sender, ownership, text, observed))
    connection.commit()
    connection.close()
    return path


def sync_through_the_packaged_worker(home: Path) -> dict:
    """What Sync Now runs: the bundled worker, naming no store location."""
    environment = {"HOME": str(home), "PATH": str(home / "nopath"), "LANG": "en_US.UTF-8"}
    done = subprocess.run(
        [str(WORKER)], input=json.dumps({"op": "sync"}),   # no store_path: derived
        capture_output=True, text=True, env=environment, timeout=180)
    return json.loads(done.stdout or "{}")


TEST_SYSTEM_PROMPT = """你是一个只读的记忆查询助手，用于一次合成数据测试。
你可以使用五个只读记忆工具：memory_conversations、memory_search、memory_timeline、memory_context、memory_recent。
规则：
1. 用户用会话名称提问时，先用 memory_conversations(name=...) 找到候选会话，
   再用候选的 canonical_conversation_id 去查 memory_search / memory_timeline / memory_recent。
2. 只依据工具返回的证据回答，不要编造。
3. 结果里带有 coverage（覆盖度）和 freshness（新鲜度）：
   trustworthy_empty 为 false 时不能断言"从未发生"；observed_through 之后的时间记忆无法知道。
4. 回答用中文，简短，最后一行写出依据的 canonical_message_id。
不要使用其它工具。"""

TASK = f"「{CONVERSATION}」里这次发布窗口定在什么时候？灰度怎么安排？"


class ProductRunner(m22a.LiveMemoryRunner):
    def skill_text(self) -> str:
        return TEST_SYSTEM_PROMPT

    def digest(self) -> DigestResult:
        result = super().digest()
        raw = getattr(self, "_raw_results", [])
        result.diagnostics["tool_results"] = [
            _detail(summary, block)
            for summary, block in zip(result.diagnostics["tool_results"], raw)
        ] or result.diagnostics["tool_results"]
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


def _detail(summary: dict, block: dict) -> dict:
    content = block.get("content")
    text = "".join(c.get("text", "") for c in content if isinstance(c, dict)) \
        if isinstance(content, list) else (content or "")
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return summary
    if not isinstance(payload, dict) or not payload.get("ok"):
        return summary
    kind = (payload.get("query_scope") or {}).get("kind")
    detail = {**summary, "kind": kind,
              "freshness_present": bool(payload.get("freshness")),
              "coverage_present": bool(payload.get("coverage"))}
    if kind == "conversations":
        detail.update({"candidates": payload.get("candidates"),
                       "unique": payload.get("unique")})
    fresh = (payload.get("freshness") or {}).get("sources", {}).get("visual", {})
    if fresh:
        detail["observed_through"] = fresh.get("observed_through")
        detail["last_succeeded_at"] = fresh.get("last_succeeded_at")
    return detail


def make_config(args, home: Path, wechat_db: Path, isolated: Path) -> ClaudeConfig:
    """Memory enabled with **no** database path: the canonical store is found."""
    cfg = ClaudeConfig(
        claude_bin=args.claude_bin, python=args.python,
        bridge=REPO / "bridge" / "wechat_companion_mcp.py", db_path=wechat_db,
        skill=REPO / m22a.DEFAULT_SKILL, isolated_home=isolated, model=args.model,
        proxy_port=args.proxy_port, extra_env=dict(args.credentials), timeout=args.timeout,
        memory_enabled=True, memory_db_path=None,          # <- the product path
        memory_server=REPO / "memory" / "wechat_memory_mcp.py",
        memory_consent_reader=lambda: m22a.read_consent(home),
    )
    nopath = home / "nopath"
    nopath.mkdir(exist_ok=True)
    original = cfg.mcp_config_document

    def document():
        doc = original()
        doc["mcpServers"]["wechat_memory"]["env"].update(
            {"HOME": str(home), "PATH": str(nopath)})
        return doc
    cfg.mcp_config_document = document  # type: ignore[method-assign]
    return cfg


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
    args = ap.parse_args(argv)
    args.work.mkdir(parents=True, exist_ok=True)
    args.credentials = m22a.credentials(args)

    home = args.work / "home"
    home.mkdir(parents=True, exist_ok=True)
    m22a.write_consent(home, allowed=True)
    build_message_store(home)
    report: dict = {"model": args.model, "runs": {}}

    if not WORKER.exists():
        report["aborted"] = "the packaged worker has not been built"
        args.report.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
        print("gate: no packaged worker; run scripts/build-memory-worker.sh", file=sys.stderr)
        return 1

    # 1. Sync Now, through the packaged worker, naming no location.
    sync = sync_through_the_packaged_worker(home)
    canonical = home / "Library/Application Support/WeChatCompanion/memory.sqlite"
    report["sync"] = {"ok": sync.get("ok"), "state": sync.get("state"),
                      "counts": sync.get("counts"),
                      "canonical_store_created": canonical.exists(),
                      "canonical_store_mode": oct(canonical.stat().st_mode & 0o777) if canonical.exists() else None}
    if not sync.get("ok"):
        report["aborted"] = f"packaged sync failed: {sync.get('state')}"
        args.report.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
        return 1
    with sqlite3.connect(canonical) as connection:
        report["sync"]["stored_messages"] = connection.execute(
            "SELECT COUNT(*) FROM messages;").fetchone()[0]

    # 2. The runner must resolve that same store from HOME alone.
    os.environ["HOME"] = str(home)
    wechat_db = build_database(args.work / "wechat.sqlite", SCENARIOS_BY_KEY["A_unanswered_question"])
    statuses = []
    try:
        default_runner = ClaudeRunner(m22a.make_config(
            args, args.work / "home_default", wechat_db, memory=None, consent_home=None))
        report["runs"]["default_probe"] = m22a.wire_probe(default_runner, CLAUDE_EXPECTED_TOOLS)
        statuses.append(report["runs"]["default_probe"]["status"])

        cfg = make_config(args, home, wechat_db, args.work / "home_memory")
        resolved = cfg.memory_env()["WECHAT_COMPANION_MEMORY_DB_PATH"]
        report["activation"] = {
            "memory_db_path_supplied": False,
            "resolved_is_canonical": resolved == str(canonical),
            "path_in_claude_environment":
                str(canonical) in json.dumps(cfg.child_env("http://127.0.0.1:0")),
            "servers": sorted(cfg.mcp_config_document()["mcpServers"]),
        }
        report["runs"]["memory_probe"] = m22a.wire_probe(
            ClaudeRunner(cfg), CLAUDE_EXPECTED_TOOLS_WITH_MEMORY)
        statuses.append(report["runs"]["memory_probe"]["status"])

        # 3. One live turn over the product path.
        runner = ProductRunner(make_config(args, home, wechat_db, args.work / "home_task"),
                               TASK, err=io.StringIO())
        record: dict = {"question": TASK}
        try:
            runner.preflight()
            observed = runner.assert_tool_boundary()
            record["observed_count"] = len(observed)
            record["exact_nine"] = observed == set(CLAUDE_EXPECTED_TOOLS_WITH_MEMORY)
            result = runner.digest()
            results = result.diagnostics["tool_results"]
            record.update({
                "returncode": result.returncode, "answer": result.text,
                "tool_calls": [{"name": c["name"], "arguments": c["arguments"]}
                               for c in result.diagnostics["tool_calls"]],
                "memory_tools_used": sorted({
                    c["name"].replace("mcp__wechat_memory__", "")
                    for c in result.diagnostics["tool_calls"]
                    if any(t in (c["name"] or "") for t in MEMORY_TOOLS)}),
                "used_memory_conversations": any(
                    (c["name"] or "").endswith("memory_conversations")
                    for c in result.diagnostics["tool_calls"]),
                "tool_results": results,
                "coverage_on_all_ok_results": all(
                    r.get("coverage_present") for r in results if r.get("ok")),
                "freshness_on_all_ok_results": all(
                    r.get("freshness_present") for r in results if r.get("ok")),
                "citations_on_all_message_results": all(
                    r.get("citations_present") for r in results
                    if r.get("ok") and r.get("kind") not in (None, "conversations")),
                "proxy": result.diagnostics["proxy"],
            })
            status = 0 if (result.returncode == 0 and result.text) else 1
        except ShadowError as exc:
            record["aborted"] = str(exc)
            status = 2
        finally:
            runner.cleanup(None)
            record["residue_after_cleanup"] = len(runner.residue())
        record["status"] = status
        statuses.append(status)
        report["runs"]["product_task"] = record
        print(f"=== product task ===\nQ: {TASK}\nA: {record.get('answer', '<none>')}\n")
    finally:
        args.report.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    worst = max(statuses) if statuses else 1
    print(f"gate: worst exit {worst}; report at {args.report}", file=sys.stderr)
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
