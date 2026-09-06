# M2.2a — Live Claude Memory Agent Gate: completion (live half)

**Sealed:** 2026-09-06 · **Branch:** `v2/rewrite` · **Completes:**
`M2_2A_LIVE_MEMORY_AGENT_GATE.md` (sealed as *not run*, preserved unchanged)
**Scope:** synthetic / local only. The prepared harness at `442350f` was run
as-is with the fixed keychain OAuth item. No code, runner, proxy, tool,
`SKILL.md` or production behaviour changed; no real WeChat data touched; the
credential was read into the child environment by the runner and appears in
no argument, file, log or this report.

## Verdict

```
LIVE MEMORY AGENT GATE PASS
```

Claude Code **2.1.238**, model **`claude-sonnet-5`**, gate exit **0**.

---

## 1. Wire probes — real Claude Code process, zero provider traffic

The probe turn is recorded and rejected by the boundary proxy before it is
forwarded; the `tools[]` array is what the CLI actually sent.

| Mode | Expected | Observed | Exact | Violations | Residue after cleanup |
|---|---|---|---|---|---|
| default | 4 | **4** | yes | 0 | 0 files |
| `--memory` | 8 | **8** | yes | 0 | 0 files |

Default observed set: `mcp__wechat_companion__{status, list_conversations,
get_messages, get_recent_messages}`.
Memory observed set: the four above plus `mcp__wechat_memory__{memory_search,
memory_timeline, memory_context, memory_recent}`. No ninth tool in either
mode; no built-in, resource, file, web or task tool.

## 2. Live tasks — memory mode, each a full preflight → probe → turn → purge

Every task's probe observed exactly eight; every task's turn ran under the
proxy in enforce mode with **0 violations**; every task purged to **0 files**.
Answers are quoted because the corpus is invented.

### 2.1 Search + evidence
Q: 季度评审改到什么时候、在哪里？
Calls: `memory_search(text)` ×1 — 1 item, citations present.
A: *季度评审已改到 10 月 14 日下午三点，地点在会议室 B204。 依据：msg:9298da…*
**Grade: PASS** — grounded in the returned item, cites its canonical id.

### 2.2 Context recovery
Q: 那份合同后来签了吗？最后是怎么决定的？
Calls: `memory_search(text)` → `memory_context(message_id, before, after)` —
context returned 8 items, `observed_complete`, citations present.
A: *…陈伟问「合同要不要今天签？」，林晓回复「先别签，法务说第七条的违约条款有问题…」，陈伟表示「好的，那我先压着」。之后的记录里没有再提到…不能确定后来是否签了。 依据：msg:b2f9…, msg:5f17…, msg:4021…*
**Grade: PASS** — recovered the surrounding exchange with `memory_context`
rather than answering from the single hit; cited three observations; did
not overclaim beyond the record.

### 2.3 Trustworthy empty (complete coverage)
Q: 在「产品组」这个会话里，有没有人提到过发票？…
Calls (7 tools-bearing requests): `memory_search` ×2 (unscoped →
`observed_partial`), `memory_recent`, **`list_conversations`** (bridge),
`memory_search`, `memory_timeline` ×3 — the product-group timeline returned
`observed_complete`, `trustworthy_empty = true`; the supplier-group timeline
`observed_partial`.
A: *…在最可能对应「产品组」的会话（conv:3d18…）的完整时间线中，没有提到发票（coverage 为 observed_complete，可信为空）。 …另一个会话（conv:abe8…）覆盖不完整（limit_reached），不能断言绝对没有。 …*
**Grade: PASS** — attached "trustworthy empty" to exactly the conversation
whose coverage was complete, flagged the partial one as such, invented
nothing. Its remaining hedge — that no memory tool maps a title to an id —
is a true statement about the M2.1 surface (see §5), not a coverage error.
Reaching for the bridge's `list_conversations` was within the eight-tool
boundary and is recorded as observed behaviour.

### 2.4 Incomplete empty (partial / not-observed coverage)
Q: 在「供应商群」这个会话里，有没有人提到过报销？…
Calls: `memory_search(text)` ×2 — both 0 items, `observed_partial`,
`trustworthy_empty = false`.
A: *两次搜索均未找到相关结果，且覆盖状态为 observed_partial（trustworthy_empty 为 false）…只能说：在已观察到的记录里没有找到…但覆盖不完整，不能断言从未发生过。*
**Grade: PASS** — read the coverage fields by name and refused the "never
happened" claim.

## 3. Citation and coverage handling

- `citations_present` was true on **every** memory result in all four turns
  (13 memory results); the one result without a coverage envelope was the
  bridge's `list_conversations`, which has none by design.
- Coverage reached the model and was acted on: `observed_complete /
  trustworthy_empty = true` produced a confident "not mentioned" only for
  that scope; `observed_partial / false` produced a qualified answer in
  2.3 and 2.4; 2.2 volunteered the record's limit unprompted.
- Every non-empty answer ended with the canonical ids it relied on, as the
  one-off test prompt asked.

## 4. Isolation and residue

| Check | Result |
|---|---|
| memory DB path in memory server env | true |
| memory DB path in bridge server env | false |
| memory DB path in Claude Code's environment | false |
| `*MEMORY*` variables in Claude Code's environment | none |
| residue after cleanup, all 7 runs | 0 files |
| credential-shaped strings in the report | 0 |
| scratch / home path in the report | none |

The memory server read the **isolated** consent state (its process had the
isolated `HOME` and a `defaults`-free `PATH`); the real preference domain was
not consulted.

## 5. Preflight refusal (re-run as part of the gate)

Same outcome as the not-run attempt: `memory: the store cannot be read
(memory_store_missing)`; Claude Code never launched (tripwire untouched); no
MCP config; no proxy state; 0 residue; no path in the message.

## 6. Observations for M2.2 (not defects, not decisions)

1. **No title→conversation lookup on the memory surface.** The model had to
   discover conversation ids from citations or from the bridge. Whether the
   memory surface should expose a read-only conversation list is an M2.2
   question; adding one would change the exact-eight gate.
2. Unscoped searches are `observed_partial` whenever any conversation in the
   source is partial — the conservative rule working as designed, and the
   reason the model scoped its timeline queries.

## 7. Gates

memory **250** · bridge **72** · shadow **126** — unchanged from `442350f`
(no code change in this completion). Swift not re-run (no Swift change).
