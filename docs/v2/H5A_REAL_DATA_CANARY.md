# H5a — First real-data ClaudeRunner canary (sanitised evidence)

**Result: PASS.** 2026-09-06 04:00 UTC. One `ClaudeRunner` execution against
the real WeChat Companion store, read-only, review-only. This file contains
counts, tool names, statuses, shapes and booleans only — **no conversation
title, sender or message text**, and not the digest.

## Runtime

| | |
|---|---|
| Runner | `shadow/runners/claude.py` via `shadow/h5_real_data_canary.py`, `v2/rewrite` @ `f104019` + this harness |
| Agent runtime | Claude Code 2.1.238 headless; `--tools ""`, `--strict-mcp-config`, `--setting-sources ""`, `autoMemoryEnabled:false`, `--disable-slash-commands`, `--permission-mode dontAsk`, `--no-session-persistence`; isolated `HOME`/`CLAUDE_CONFIG_DIR`/cwd |
| Model | `claude-sonnet-5` (pinned) |
| Auth | subscription OAuth token read at runtime from the fixed keychain item `wechat-shadow-claude-oauth`; never argv, disk, logs or reports. **Deleted after this run.** |
| Skill | `.hermes/skills/wechat-digest/SKILL.md` @ `5b1e7a8`, unmodified, verbatim system prompt |
| Bridge | `bridge/wechat_companion_mcp.py`, stdio, `mode=ro` + `PRAGMA query_only`, unmodified |
| Store | `~/Library/Application Support/WeChatCompanion/messages.sqlite`, schema `user_version = 1`, created by the dev app after the operator enabled both consents and picked one WeChat window |
| Duration | 22.3 s wall clock for the digest turn |

## Preconditions verified before the run

- HEAD `f104019`, clean tree, vault reconciled.
- Both app consents on (`extraction.allowsRemoteProcessing = 1`,
  `persistence.allowsLocalMessageStorage = 1`), set by the operator in the UI.
- Bridge `status`: `ok`, `ready`, schema 1, **2 conversations, 13 messages**,
  all first observed within the previous hour. Deliberately small.
- Bridge tool list: exactly `status`, `list_conversations`, `get_messages`,
  `get_recent_messages`.

## Tool boundary (wire-observed)

Probe request recorded and never forwarded; observed `tools[]` equal to the
four bridge tools; digest turn enforced on every request.

| | |
|---|---|
| Observed names | `mcp__wechat_companion__status`, `mcp__wechat_companion__list_conversations`, `mcp__wechat_companion__get_messages`, `mcp__wechat_companion__get_recent_messages` |
| Model requests in the digest turn | 3, all tools-bearing |
| Violations | 0 |
| Upstream HTTP statuses recorded | 200, 200 (third response was still streaming when the verdict file was last written) |
| Forwarding errors | none |

Three model requests imply two tool calls before the final answer. The bridge's
per-call count lines were not present in the CLI's MCP log (only the two
"bridge starting" lines were), so the exact call sequence is inferred, not
logged.

## Digest (shape only)

| Property | Value |
|---|---|
| Produced | yes |
| Non-empty lines | 5 |
| Characters | 225 |
| Starts with `微信摘要` | yes |
| Sections present | `💬 其他讨论` only |
| Bullets | 2 |
| Coverage line present and literal last line | yes / yes |
| Empty-state form | no |
| Quotes a stored title/sender/text of ≥ 6 chars verbatim | no |

The operator did not read the digest during this run (it was measured, not
displayed, so it would not enter this transcript). Whether the classification
was *correct* for these 13 messages is therefore **not graded** here.

## Persistence audit

Inventory of the isolated home before purge (seven files, same set as H6):
CLI config JSON and backup, a cleanup timestamp, two MCP stderr logs, and the
two runner-owned files (`mcp.json`, `tool_boundary.json`).

| Check | Result |
|---|---|
| Residue files containing any stored title/sender/text (10 strings ≥ 6 chars) | **0** |
| Real `~/.claude` / `~/.claude.json` containing any of them | **0** |
| Files under the isolated home after purge | **0** |
| Leak hits anywhere after purge | **0** |
| Store mutated | no — opened `mode=ro`; the app kept writing its WAL normally |

## Verdict against the H5a checklist

1. Real store opens — yes. 2. `status` succeeds — yes. 3. Only the four MCP
tools reachable — yes, on the wire. 4. A digest is produced — yes; usefulness
not graded. 5. Coverage line last — yes. 6. No instruction-shaped message
executed — structurally impossible (no other tool existed); no injection
fixture was present in real data. 7. Cleanup proves clean — yes. 8. No raw
content in persistent artifacts — yes.

## Is H5 complete?

**No — H5b recommended, small.** H5a proves the path end to end on real data
but its data was 2 chats / 13 messages that produced a single `其他讨论`
section, and the digest was not read. H5b should be one run where the
operator reads the stdout digest and grades it against the H3 expectations
with at least one 需要处理 or 时间与安排 item present in the captured window.
Nothing in H5b needs new code.
