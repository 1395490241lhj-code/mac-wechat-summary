# M2.1 — Read-only Memory MCP + wire gate

**Sealed:** 2026-09-06 · **Branch:** `v2/rewrite` · **Builds on:** `e694048` (M2)
**Scope:** a second, separate, explicitly enabled, read-only MCP server over the
memory store, reachable from `ClaudeRunner` only on request, with the wire
gate extended to assert it. No AI, no embedding, no sync/link/write tool, no
reader change, no real data, no change to the WeChat bridge.

```
ClaudeRunner
  ├─ wechat_companion  (bridge/wechat_companion_mcp.py)   4 tools — unchanged
  └─ wechat_memory     (memory/wechat_memory_mcp.py)      4 tools — off by default
```

---

## 1. Process / layer boundary

- **Two servers, two processes, no shared code.** `bridge/` never references
  the memory layer (asserted). `memory/wechat_memory_mcp.py` never imports
  `wechat_companion_mcp`, `rion_reader_adapter` or `message_source`
  (asserted). The MCP SDK is confined to the one server module in `memory/`.
- `shadow/runners/claude.py` keeps **no import-time dependency** on memory: it
  loads `memory_consent` and `memory_store` by path from the injected server
  location, only for the pre-run gate, and a test pins it as the only loader.
- The memory store is opened **`mode=ro` + `PRAGMA query_only`** by the
  server (`MemoryStore.open_read_only`) — a write is refused by the engine. A
  reader creates nothing and migrates nothing: a missing file is
  `memory_store_missing`, a corrupt one `memory_store_unreadable`, any
  `user_version` other than 2 (including 1) `schema_unsupported`.

## 2. Activation — explicit, fail closed before the run

| Runner field | CLI flag | Requirement |
|---|---|---|
| `memory_enabled` | `--memory` | opt-in; off by default |
| `memory_db_path` | `--memory-db-path` | explicit; reaches the memory server env **only** |
| `memory_server` | `--memory-server` | `memory/wechat_memory_mcp.py`, injected, never searched for |

`ClaudeConfig.memory_env()` validates before anything is spawned: server file
present, path given, the app's `consent.state` allowing (read through the
memory layer's own gate; the reader is injectable for tests and real for the
CLI), and the store openable read-only at a supported version. Any failure is a
`ShadowError` before `_prepare`, so **no MCP config is written, no proxy
starts, no CLI runs** — the run never silently continues with four tools. Memory
flags without `--memory`, or with the Hermes backend, are refused. Refusal
messages carry a fixed state token and **no path**.

**Default is byte-identical to pre-M2.1:** same argv, same child env, same
single-server MCP document (asserted by serialising the document).

## 3. Exact tool sets

| Mode | Expected on the wire | Count |
|---|---|---|
| default | `mcp__wechat_companion__{status, list_conversations, get_messages, get_recent_messages}` | **4** |
| `--memory` | the four above + `mcp__wechat_memory__{memory_search, memory_timeline, memory_context, memory_recent}` | **8** |

`ClaudeRunner.expected_tools` is now derived from the config; `--allowedTools`
is exactly that set; the probe/enforce proxy asserts equality. Tests pin: any
ninth tool (Bash, Read, WebFetch, ListMcpResourcesTool, Task, a hypothetical
`memory_sync` or `memory_link`) refused in enabled mode; a missing memory tool
refused; memory tools appearing in default mode refused. The pre-existing
four-tool assertions are untouched.

## 4. Public schemas (narrower than M2)

| Tool | Parameters |
|---|---|
| `memory_search` | `text?`, `conversation_id?`, `sender?`, `start?`, `end?`, `limit?`, `order? ∈ {recent, oldest, relevance}` |
| `memory_timeline` | `conversation_id`, `start?`, `end?`, `after_cursor?`, `before_cursor?`, `limit?` |
| `memory_context` | `message_id`, `before?`, `after?` |
| `memory_recent` | `conversation_id?`, `since?`, `until?`, `limit?`, `order? ∈ {recent, oldest}` |

Caps: `limit` 1…200, `before`/`after` 0…50, text ≤ 500 chars, `start ≤ end`,
cursors must be the object returned on an item. **Not exposed:** `SourcePolicy`,
source filtering, SQL, paths. Wrong *types* are stopped by the SDK's schema
before the tool runs; wrong *values* get the fixed `invalid_argument` refusal.

## 5. Wire envelope

`{ ok, items[], coverage, truncated, query_scope, focal_canonical_id }` —
the M2 envelope verbatim. Each item: `citation` (the eight M2 fields, no text),
`logical_message_id` (optional metadata, `null` = unknown), `sender`,
`ownership`, `kind`, `text`, `timestamp`, `is_focal`, `cursor`, and
`observations[]` each with its own citation and provenance.
`coverage`: `status`, `trustworthy_empty`, `required_sources`,
`supplemental_sources`, `complete_sources`, `per_source{status, reasons}`,
`caveats` — enough to tell trustworthy-empty from incomplete-empty, unavailable
from partial, and which required sources are complete. `query_scope.policy` is
**reported, never accepted**: the host's conservative default (every known
source required) is the only policy.

## 6. Privacy / redaction

No path, database location, consent-state location, SQL, credential, host or
user identifier in any response or on stderr (asserted against the temp path,
`gethostname()` and `getuser()`). Consent and store refusals are the fixed
memory-layer tokens; any other exception collapses to
`{"ok": false, "state": "internal_error", "detail": "The memory server could not answer this request."}`.
stderr carries counts and states only.

## 7. Composition test (real process boundary)

`test_composition_over_a_real_memory_server_process`: a ClaudeRunner-shaped
environment (the two activation variables, `HOME` pointing at a temporary
preference plist holding a synthetic `consent.state`, a `PATH` with no
`defaults` so the server's plist fallback answers) → **a real
`wechat_memory_mcp.py` subprocess over stdio** → a synthetic v2 store → M2.
Verified: exactly the four tools listed; search; context around the hit with
the focal marked; timeline with truncation; recent; the envelope on all four;
required-source coverage; trustworthy-empty inside the observed window vs
not-observed outside it; SDK-level type refusal and tool-level value refusal;
no path in any reply. Two more runs: no app state → every tool refuses
`consent_state_missing`; consent withheld → `consent_withheld`.

## 8. Ingestion unchanged

`memory_sync.py` remains operator-run and foreground. No scheduler, polling,
app sync UI, or agent-triggered refresh was added; the memory server has no
write path to trigger one. M2.2 decides freshness UX.

## 9. Gates

| Suite | M2 | M2.1 |
|---|---|---|
| `memory/` | 211 | **250** (+39: server, schemas, refusals, composition) |
| `bridge/` | 72 | **72** unchanged |
| `shadow/` | 92 | **122** (+30 activation, gates, CLI, drift); 92 pre-existing unmodified |
| Swift | 177 / 14 | **177 / 14** (no Swift change) |

Total 621.

## 10. State of the surface

**Current:** default agent surface = 4 raw WeChat tools · memory is explicit
opt-in · enabled surface = exactly 8 read-only tools · coverage and citation are
mandatory on the wire · source policy is host-controlled · the memory server
cannot mutate the store.

**Future, not implemented:** M2.2 explicit app-owned sync / freshness UX;
M3 intelligence extraction.
