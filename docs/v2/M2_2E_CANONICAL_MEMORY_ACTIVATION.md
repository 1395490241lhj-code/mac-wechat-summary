# M2.2e — Canonical Memory Activation + End-to-End Product Gate

**Sealed:** 2026-09-07 · **Branch:** `v2/rewrite` · **Builds on:** `08fd99d` (M2.2d)
**Scope:** finish the normal app-owned memory path so no one has to know or
type a store location, and prove the whole flow on a real Claude Code process
with synthetic data. No tenth tool, no sync/write MCP tool, `SKILL.md`
untouched, no scheduler, no agent-triggered sync, no `SourcePolicy` exposure,
consent unchanged, reader fallback rules unchanged, no real WeChat data.

## Verdict

```
PRODUCT PATH GATE PASS
```

Claude Code **2.1.238**, `claude-sonnet-5`, gate exit **0**.

---

## 1. Canonical activation

`ClaudeConfig.memory_env()` now resolves the store itself when no override is
given:

```python
resolved = self.memory_db_path or self._canonical_store_path()
```

`_canonical_store_path()` loads `memory_paths` **by path** from beside the
injected server and calls `canonical_store_path()` — the same definition the
app and the worker use. The runner holds no path literal of its own; a test
asserts that `shadow/runners/claude.py` contains neither `Application Support`
nor `memory.sqlite`, and another asserts the resolved value equals
`memory_paths.canonical_store_path()`.

- `--memory` remains the explicit opt-in; nothing is resolved without it.
- Default (no `--memory`) is byte-identical: no server, no variable, no
  expected tool — the M2.1 assertions are untouched.
- **No PATH search, no filesystem scan** for an alternative store.
- The path reaches the memory server's environment only, never Claude Code's
  (asserted in the gate: `path_in_claude_environment: false`).

## 2. Fail closed

An explicitly requested memory run refuses **before Claude Code launches** when
the canonical store is absent, unreadable, at an unsupported schema, corrupt,
or unconsented — `memory_env()` raises inside `_prepare`, so no MCP config is
written and no proxy starts. It never silently disables memory, never
continues in four-tool mode, never falls back to another path, and **never
creates a store**: the check opens read-only, and a test asserts neither the
file nor its directory exists afterwards.

## 3. Availability is the precondition; age is not

`_check_memory_available` validates presence, readability, schema and consent.
It does not look at time. A store whose file is 400 days old still activates
(asserted), and a docstring-stripped scan of both activation methods asserts
they contain no `stale`, `max_age`, `is_fresh`, `freshness`, `time` or `mtime`.
Freshness stays what M2.2c made it: information on every query envelope for the
model to weigh, not a gate with a threshold nobody chose.

## 4. Product flow vs override

| | |
|---|---|
| **Product default** | `wechat_shadow_run.py --agent-backend claude --memory --memory-server memory/wechat_memory_mcp.py …` — the app-owned canonical store is found. **No path.** |
| **Override** | `--memory-db-path <file>` — isolated tests and debugging only. Its help text now says so. |

The override still wins when given, and the canonical location is not
consulted in that case (asserted).

## 5. Real-user-data guard — the M2.2d incident class, structurally closed

M2.2d's bug was not specific to one test, so the guard is not either.

**Python (`memory/tests/conftest.py`, `shadow/tests/conftest.py`).** One
autouse fixture gives **every** test an isolated `HOME`, so any code that
derives the canonical location reaches a temporary directory. A test that
genuinely needs the real one marks itself `@pytest.mark.real_data` and says so.
Behind that, a session-scoped fixture captures the real store's existence and
mtime *before* redirection and asserts at the end that the suite neither
created, removed, nor wrote to it.

**Swift.** The guard moved into `PackagedMemorySyncRunner.bundled()` — the one
factory that pairs the bundled worker with the canonical store — which returns
nil under a test host. `AppModel.defaultMemorySyncRunner()` keeps its own check
as a second, independent stop.

**Regression test for the exact incident:** `aTestHostNeverPairsARealWorkerWithTheRealStore`
runs in a test host whose bundle *does* contain a real packaged worker (the
suite was re-run after building it) and asserts `bundled()` is nil and the
default runner is inert. `theGuardRefusesEvenWhenAWorkerIsPresent` builds a
bundle that definitely contains an executable helper and still gets nil, so the
refusal is the test host rather than a missing file. Neither reads the user's
store — only its absence is observed.

## 6. App / agent consistency

| Claim | Evidence |
|---|---|
| Sync Now writes the canonical store | packaged-worker sync with **no** `store_path` created it (gate §7); Swift `MemorySyncTests` cover the app's own path |
| `--memory` with no path resolves that same store | `resolved_is_canonical: true` in the gate; `test_no_db_path_resolves_the_app_owned_canonical_store` |
| Memory MCP opens it read-only | `MemoryStore.open_read_only` (`mode=ro` + `query_only`); creates and migrates nothing |
| The path never reaches Claude Code | `path_in_claude_environment: false` |
| An explicit override still works | `test_an_explicit_override_still_wins` |
| Swift and Python derivations cannot drift | `test_the_swift_side_derives_the_same_location` parses the Swift constants |

## 7. End-to-end product gate (synthetic, isolated home)

```
packaged MemoryWorker sync → canonical store → ClaudeRunner --memory
   → real Memory MCP → real Claude Code → 9 tools
```

| Step | Result |
|---|---|
| packaged worker sync, no `store_path` | `ok`, 1 conversation, 3 messages, **3 inserted** |
| canonical store | created, mode **0600**, 3 stored messages |
| activation | `memory_db_path_supplied: false`, `resolved_is_canonical: true` |
| default probe | **exactly 4**, 0 violations, 0 residue |
| memory probe | **exactly 9**, 0 violations, 0 residue |
| live turn | `memory_conversations` → `memory_search` → `memory_timeline`; 3 tools-bearing requests, **0 violations**, **0 residue** |
| answer | *发布窗口定在 11 月 6 日晚上十点。灰度…先放量 10%…并已提前准备好回滚脚本* — with three `canonical_message_id`s |
| coverage / freshness / citations | present on **every** result (citations on every message result) |
| report hygiene | 0 credential-shaped strings, no scratch or real-home path |
| the user's real store | **untouched** — still absent |

The `observed_partial` coverage on the unbounded search and timeline is
expected, not a defect: `ingest_from_source` bounds a conversation's window at
its newest message, so a query reaching past `observed_through` is partial by
the M1.1 rule.

## 8. Tool boundary

Default **exactly 4**; memory-enabled **exactly 9**. No tenth tool, no
`memory_sync` / `memory_status` / write / update / delete / link tool.
Freshness still travels inside the existing read envelopes.
`shadow/tests/test_memory_activation.py`: 36 passed.

## 9. Gates

| Suite | M2.2d | M2.2e |
|---|---|---|
| `memory/` | 326 | **330** (+4: canonical worker default, guard self-tests) |
| `bridge/` | 72 | **72** unchanged |
| `shadow/` | 134 | **138** (+4 canonical activation, −1 replaced) |
| Swift | 204 / 16 | **206 / 16** (+2 incident-class regression) |

## 10. Product architecture

```
macOS App
    ↓ Sync Now (explicit, foreground)
Bundled MemoryWorker            Contents/Helpers/MemoryWorker.app
    ↓
App-owned canonical MemoryStore ~/Library/Application Support/WeChatCompanion/memory.sqlite
    ↓
ClaudeRunner --memory           (no path)
    ↓
Read-only Memory MCP
    ↓
9-tool Claude surface           4 bridge + 5 memory
```

**Normal users do not configure a MemoryStore path.** No scheduler. No agent
sync. No memory write tools. Consent stays app-owned and is enforced in the
app, in the worker, and again at activation.

## 11. Release packaging — deliberately not this phase

The nested `MemoryWorker.app` packaging from M2.2d is preserved unchanged.
**Release signing, notarization and stapling of a bundle containing a nested
helper remain untested and unclaimed**, and are recorded as a separate future
packaging gate.

## 12. Remaining before M2.3 (skill integration)

1. The release/notarization packaging gate above.
2. Skill design: how a digest cites memory and states freshness beside
   coverage. Every live gate so far has used a one-off prompt, and `SKILL.md`
   still knows nothing of memory.
3. Whether a logical conversation should be queryable as one unit, and who may
   create equivalence links — nothing produces `operator` / `source_provided`
   links yet.
