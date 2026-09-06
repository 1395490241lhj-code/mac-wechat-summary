#!/usr/bin/env python3
"""H5a — first real-data ClaudeRunner canary. One run, review-only, sanitised.

Runs a single ``ClaudeRunner`` cycle against the **real** WeChat Companion
store (read-only through the bridge) and records evidence that contains no
chat content: counts, tool names, HTTP statuses, structural properties of the
digest, file paths and byte counts, booleans.

What this script deliberately does **not** do:

* print, save or return the digest text — the digest is derived real data
  (D-007). Only its *shape* is measured: line count, which section headings
  appear, whether the coverage line is the literal last line, whether it is
  the empty form;
* log any conversation title, sender or message text. The leak check loads
  those strings into memory solely to test residue files for containment and
  reports a count of hits;
* keep anything: the isolated home is purged and proved empty at the end.

Usage
-----
    python3 shadow/h5_real_data_canary.py --claude-bin ~/.local/bin/claude \\
        --python <interpreter with mcp> --isolated-home <throwaway dir> \\
        --report <path.json> --claude-oauth-from-keychain [--db-path <store>]
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

from agent_runner import ShadowError, collect_pass_env, read_keychain_token, run_shadow  # noqa: E402
from runners.claude import DEFAULT_MODEL, DEFAULT_SKILL, ClaudeConfig, ClaudeRunner  # noqa: E402

DEFAULT_STORE = Path.home() / "Library/Application Support/WeChatCompanion/messages.sqlite"
REAL_CLAUDE_LOCATIONS = (Path.home() / ".claude", Path.home() / ".claude.json")

HEADINGS = ("🔴 需要处理", "📅 时间与安排", "✅ 待办", "🟡 值得关注", "💬 其他讨论")
COVERAGE_LINE = "基于 WeChat Companion 已采集到的消息生成，可能不包含未被采集的聊天。"
EMPTY_FORM = "暂无已采集到的消息。"

#: The bridge's own fixed-format log lines (counts and states only, by design).
BRIDGE_LOG_PATTERNS = re.compile(
    r"(status: ready, \d+ conversations, \d+ messages"
    r"|status: not ready \([a-z_]+\)"
    r"|request refused: [a-z_]+"
    r"|list_conversations: returned \d+ rows \(limit \d+\)"
    r"|get_messages: returned \d+ rows \(limit \d+\)"
    r"|get_recent_messages: returned \d+ rows \(limit \d+\)"
    r"|wechat-companion MCP bridge starting \(read-only\))"
)


def store_counts(db: Path) -> dict:
    """Aggregate counts from the store, read-only. No content."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        now = time.time()
        return {
            "schema_version": conn.execute("PRAGMA user_version;").fetchone()[0],
            "conversations": conn.execute("SELECT COUNT(*) FROM conversations;").fetchone()[0],
            "messages": conn.execute("SELECT COUNT(*) FROM messages;").fetchone()[0],
            "messages_last_24h": conn.execute(
                "SELECT COUNT(*) FROM messages WHERE first_observed_at >= ?;", (now - 86400,)
            ).fetchone()[0],
        }
    finally:
        conn.close()


def sensitive_strings(db: Path) -> list[bytes]:
    """Titles, senders and message texts — held in memory for containment
    tests only. Never written, printed or returned to the report."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        values: set[str] = set()
        for (t,) in conn.execute("SELECT title FROM conversations;"):
            if t:
                values.add(t)
        for s, x in conn.execute("SELECT sender, text FROM messages;"):
            for v in (s, x):
                if v:
                    values.add(v)
        return [v.encode("utf-8") for v in values if len(v) >= 6]
    finally:
        conn.close()


def containment_hits(paths, needles) -> int:
    hits = 0
    for p in paths:
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if any(n in data for n in needles):
            hits += 1
    return hits


def files_under(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return [p for p in root.rglob("*") if p.is_file()] if root.is_dir() else []


def digest_shape(text: str) -> dict:
    lines = [l for l in text.splitlines() if l.strip()]
    return {
        "produced": bool(text.strip()),
        "line_count": len(lines),
        "char_count": len(text),
        "starts_with_title": bool(lines) and lines[0].strip() == "微信摘要",
        "headings_present": [h for h in HEADINGS if h in text],
        "coverage_line_present": COVERAGE_LINE in text,
        "coverage_line_is_last": bool(lines) and lines[-1].strip() == COVERAGE_LINE,
        "empty_form": EMPTY_FORM in text,
        "bullet_count": sum(1 for l in lines if l.lstrip().startswith("- ")),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--claude-bin", required=True, type=Path)
    ap.add_argument("--python", required=True, type=Path)
    ap.add_argument("--isolated-home", required=True, type=Path)
    ap.add_argument("--report", required=True, type=Path)
    ap.add_argument("--db-path", type=Path, default=DEFAULT_STORE)
    ap.add_argument("--pass-env", action="append", default=[], metavar="NAME")
    ap.add_argument("--claude-oauth-from-keychain", action="store_true")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--proxy-port", type=int, default=8823)
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args(argv)

    report: dict = {"phase": "H5a", "model": args.model, "store": {}, "run": {}}
    if not args.db_path.is_file():
        report["aborted"] = "store not found"
        args.report.write_text(json.dumps(report, indent=1, ensure_ascii=False))
        print("ABORTED: store not found", file=sys.stderr)
        return 2
    report["store"] = store_counts(args.db_path)
    needles = sensitive_strings(args.db_path)
    report["store"]["leak_check_strings"] = len(needles)

    extra = collect_pass_env(args.pass_env, os.environ)
    if args.claude_oauth_from_keychain:
        extra.update(read_keychain_token())
    cfg = ClaudeConfig(
        claude_bin=args.claude_bin, python=args.python,
        bridge=REPO / "bridge" / "wechat_companion_mcp.py", db_path=args.db_path,
        skill=REPO / DEFAULT_SKILL, isolated_home=args.isolated_home, model=args.model,
        proxy_port=args.proxy_port, extra_env=extra, timeout=args.timeout,
    )
    runner = ClaudeRunner(cfg)
    home = args.isolated_home

    residue: dict = {}
    real_cleanup = runner.cleanup

    def inventory_then_keep(run_id):
        files = sorted(files_under(home))
        residue["files"] = [{"path": str(p.relative_to(home)), "bytes": p.stat().st_size}
                            for p in files]
        residue["leak_hits_isolated"] = containment_hits(files, needles)
        residue["leak_hits_real_claude"] = containment_hits(
            [p for r in REAL_CLAUDE_LOCATIONS for p in files_under(r)], needles)
        bridge_lines: list[str] = []
        for p in files:
            if "mcp-logs" in str(p):
                for m in BRIDGE_LOG_PATTERNS.finditer(p.read_text(errors="ignore")):
                    bridge_lines.append(m.group(0))
        residue["bridge_log_lines"] = bridge_lines
        state_file = cfg.proxy_state
        if state_file.is_file():
            st = json.loads(state_file.read_text())
            residue["proxy"] = {k: st.get(k) for k in
                                ("requests", "tools_bearing", "violations", "verdict",
                                 "observed", "upstream_status", "forward_errors")}
        if runner._proxy is not None:
            runner._proxy.stop()
            runner._proxy = None

    runner.cleanup = inventory_then_keep  # type: ignore[method-assign]
    out, err = io.StringIO(), io.StringIO()
    started = time.time()
    status = run_shadow(runner, out=out, err=err)
    duration = round(time.time() - started, 1)
    digest = out.getvalue()
    del out  # the text lives only in `digest` from here on

    stderr_lines = [l for l in err.getvalue().splitlines()
                    if l.startswith(("tool boundary", "preflight", "cleanup", "ABORTED",
                                     "run failed", "CLEANUP", "INTERRUPTED"))]
    report["run"] = {
        "status": status, "duration_s": duration,
        "expected_tools": sorted(runner.expected_tools),
        "stderr_lines": stderr_lines,
        "digest": digest_shape(digest),
        "digest_contains_store_strings": any(n.decode("utf-8", "ignore") in digest for n in needles),
        **residue,
    }
    del digest

    real_cleanup(None)
    post = files_under(home)
    report["run"]["post_purge_files"] = [str(p) for p in post]
    report["run"]["post_purge_leak_hits"] = containment_hits(
        post + [p for r in REAL_CLAUDE_LOCATIONS for p in files_under(r)], needles)
    args.report.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    for line in stderr_lines:
        print(line, file=sys.stderr)
    verdict = "PASS" if (status == 0 and report["run"]["digest"]["produced"]
                         and not post) else "FAIL"
    print(f"H5a: {verdict} (exit {status}, {duration}s); report at {args.report}", file=sys.stderr)
    return 0 if verdict == "PASS" else (status or 1)


if __name__ == "__main__":
    raise SystemExit(main())
