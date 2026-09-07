# M2.2b — Memory Conversation Discovery + live 4/9 gate

**Sealed:** 2026-09-06 · **Branch:** `v2/rewrite` · **Builds on:** `c957b39` (M2.2a completion)
**Scope:** one read-only discovery tool, the boundary moved from exactly eight
to exactly nine, verified on a real Claude Code process with synthetic data.
No sync/ingestion/link/write tool, no `SourcePolicy` or reader control
exposed, `SKILL.md` untouched, no real WeChat data, nothing pushed.

## Verdict

```
LIVE CONVERSATION DISCOVERY GATE PASS
```

Claude Code **2.1.238**, model **`claude-sonnet-5`**, gate exit **0**.

---

## 1. Why

The M2.2a live run showed the model reaching for the bridge's
`list_conversations` to turn 「产品组」 into a canonical id, because memory
had no way to do it. Memory is now self-contained for that step.

## 2. Internal API — `MemoryQueryService.conversations(name=None, limit=50)`

Returns `ConversationDiscoveryResult { items, truncated, query_scope, coverage }`.

**Matching rule, exactly.** Both sides are normalised (NFC, trimmed,
case-folded). A stored display name that *equals* the query is `exact`; one
that *contains* it as a substring is `contains`; nothing else matches. No
tokenising ("评 审" matches nothing), no character-bag matching ("群产品"
matches nothing), no edit distance, no model. With no name every conversation
is returned as `all`. **This is candidate lookup, not identity resolution.**

**Ordering.** `exact` before `contains`, then most recently seen, then
canonical id — deterministic.

**Ambiguity.** Two conversations with one display name are two items.
`is_unique` / `is_ambiguous` are exposed; nothing here ever picks one.

**Logical conversations.** Mirrors the M2 message model: observations
explicitly linked (`equivalence_links`, basis `operator` or
`source_provided`) group into one item carrying every observation with its
provenance; the representative (`canonical_conversation_id`) is the most
recently seen observation. Same display name across sources without a link
stays two items with `logical_conversation_id = null`. Nothing is inferred
from names, timestamps, or source similarity.

**Coverage, honestly.** `ConversationCoverage { status: "store_inventory",
exhaustive_of_store: bool, note }` — true unless truncated. It says the store
was enumerated; it never claims the store holds every conversation WeChat has,
and there is deliberately no `exhaustive_of_source` field to misread.

Each item is sufficient to continue: its `canonical_conversation_id` is
accepted by `search` / `timeline` / `recent_context`. Those queries are per
observation, so a logical item lists every observation for a caller who wants
all of them.

## 3. Public tool — `memory_conversations(name?, limit?)`

`name` ≤ 200 chars, `limit` 1…200. Wire envelope:

```
{ ok, items[], truncated, query_scope{ kind, name, limit, order, match_rule },
  coverage{ status, exhaustive_of_store, note }, candidates, unique, ambiguous }
```

Item: `canonical_conversation_id`, `logical_conversation_id`, `display_name`,
`kind`, `match`, `sources[]`, `observations[{ canonical_conversation_id,
source, source_conversation_id, display_name, kind, first_seen_at,
last_seen_at }]`. No path, SQL, credential, host or user identifier (asserted).
The four message tools' schemas are unchanged and gained no `name` parameter
(asserted). Refusals are the same fixed memory-layer states.

## 4. Boundary — exactly 4 / exactly 9

`MEMORY_TOOLS` gains `memory_conversations`; `expected_tools`,
`--allowedTools`, and the probe/enforce proxy follow. Tests refuse any tenth
tool (Bash, Read, WebFetch, ListMcpResourcesTool, ReadMcpResourceTool, Task, a
hypothetical `memory_sync` / `memory_link` / `memory_delete`) and any missing
one; the default four-tool assertions are untouched.

| Mode | Expected | Observed on a real Claude Code process | Exact | Violations | Residue |
|---|---|---|---|---|---|
| default | 4 | **4** — `mcp__wechat_companion__{status, list_conversations, get_messages, get_recent_messages}` | yes | 0 | 0 |
| `--memory` | 9 | **9** — the four + `mcp__wechat_memory__{memory_conversations, memory_search, memory_timeline, memory_context, memory_recent}` | yes | 0 | 0 |

## 5. Live tasks (synthetic corpus: 5 conversations, 8 messages; one unique
title, two 「产品群」, one linked 「财务对接」 with 2 observations)

Each task: preflight → probe (exactly nine observed) → turn under the
enforce-mode proxy → purge to 0 files.

### 5.1 Unique title
Q: 「设计评审群」里评审定在什么时候、在哪里？
Calls: `memory_conversations(name)` → 1 candidate, `exact`, unique →
`memory_search(conversation_id, text)` → `memory_timeline(conversation_id)`;
**both later calls used the candidate id** (2 of 2). Results
`observed_complete`, citations present.
A: *设计评审定在周四上午十点，地点在A栋302。… 依据：msg:1cee…*
**PASS** — discovery, then retrieval on the discovered id, answered from the
returned evidence with its citation.

### 5.2 Ambiguous title
Q: 「产品群」里发布日期定了吗？
Calls: `memory_conversations(name)` → **2 candidates, both `exact`,
`ambiguous = true`** → `memory_search(conversation_id, text)` ×2, one per
candidate; **all later calls used candidate ids** (2 of 2).
A: *存在两个名为「产品群」的会话，结果不同，请确认具体是哪个：… 来源ID 23 …尚未确定 … 来源ID 22 …已确定为 11 月 3 日 … 请告诉我您指的是哪一个群 … 依据：msg:097f…；msg:5b5d…*
**PASS** — did not pick one; reported each candidate's evidence separately
with its citation and asked for clarification. No identity guess.

## 6. Citation, coverage, isolation, residue

Citations present on every message result (4 of 4). Coverage reached the
model (`observed_complete` throughout; no empty result arose). Memory DB path
in the memory server env only — absent from the bridge env and from Claude
Code's environment; no `*MEMORY*` variable there. The memory server read the
isolated consent state. Residue after cleanup: **0 files in all 4 runs**.
Report contains 0 credential-shaped strings and no scratch or home path. The
proxy saw 3 tools-bearing requests per task, 0 violations.

## 7. Gates

| Suite | M2.2a | M2.2b |
|---|---|---|
| `memory/` | 250 | **268** (+18 discovery: matching, ambiguity, logical grouping, tool, live server) |
| `bridge/` | 72 | **72** unchanged |
| `shadow/` | 126 | **131** (+2 tenth-tool cases, +3 M2.2b harness; 122 pre-existing unmodified) |
| Swift | 177 / 14 | not re-run (no Swift change) |

## 8. Surface, stated

**Default:** 4 raw WeChat read tools. **Memory enabled:** those 4 + 5
read-only memory tools — `memory_conversations`, `memory_search`,
`memory_timeline`, `memory_context`, `memory_recent` — **9 total**. No memory
write, sync or link tool is public. Source policy is host-controlled.
`SKILL.md` still knows nothing of memory.

Sealed history preserved unchanged: `M2_2A_LIVE_MEMORY_AGENT_GATE.md`,
`M2_2A_LIVE_MEMORY_AGENT_GATE_COMPLETION.md`, `M2_1_MEMORY_MCP_GATE.md`
(their "eight" is correct as of their runs).
