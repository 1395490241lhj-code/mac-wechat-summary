# M2.2c — Explicit Memory Sync + Freshness

**Sealed:** 2026-09-06 · **Branch:** `v2/rewrite` · **Builds on:** `138dea3` (M2.2b)
**Scope:** a deterministic freshness model on every memory result, the sync
CLI made to honour the selected source, an app-owned Sync Memory state
machine and panel with its packaging gap stated rather than papered over, and
a live freshness gate on a real Claude Code process. No scheduler, no
background polling, no agent-triggered sync, no new tool, no `SourcePolicy`
exposure, `SKILL.md` untouched, consent unchanged, no real WeChat data.

## Verdict

```
LIVE FRESHNESS GATE PASS
```

Claude Code **2.1.238**, `claude-sonnet-5`, gate exit **0**; default probe
**exactly 4**, memory probe **exactly 9**, **0 violations**, **0 residue**.

---

## 1. Three questions, kept apart

| Concept | Question it answers | Where it lives |
|---|---|---|
| **Coverage** | What portion of the *requested* data was observed? | `assess_coverage`, per query (M1/M1.1/M2) |
| **Freshness** | When was memory last updated, and through what point was the source observed? | `memory_freshness.py`, per store (M2.2c) |
| **Latest message** | What is the newest *stored* message timestamp? | reported inside freshness, labelled distinctly |

They are not interchangeable, and the wire carries all three side by side.

## 2. Freshness model — `memory/memory_freshness.py`

Per source (`SourceFreshness`): `runs_total`, `last_attempted_at`,
`last_attempt_state`, `last_attempt_failure_state`, `last_succeeded_at`,
`observed_through`, `complete_through`, `latest_message_at`,
`latest_message_timestamp_kind`, `stored_messages`.

Aggregate (`MemoryFreshness`): `generated_at` (the one clock read, injectable),
`participating_sources`, `last_successful_sync {source, at}`,
`observed_through_all_sources`, `complete_through_all_sources` (each the
**minimum** over sources — the point through which *every* source is known —
and `null` if any source has none), `caveats`, and a `semantics` block that
restates the field meanings on the wire.

**No `is_fresh`. No staleness threshold.** A sync from yesterday with complete
coverage through yesterday is exactly that; whether it is too old is a product
or agent decision made with the timestamps in view.

### Exact timestamp semantics

- `last_attempted_at` — `started_at` of the newest run, whatever its state.
- `last_succeeded_at` — `completed_at` of the newest **succeeded** run; a later
  failure does not move it.
- `observed_through` — over coverage records of succeeded runs with status
  `observed_complete` or `observed_partial`: the record's `window_end`, or the
  run's `completed_at` when the record is unbounded (an unbounded read covers
  everything that existed when it ran). `unavailable` records contribute
  nothing — a refusal observed nothing.
- `complete_through` — the same over `observed_complete` records only.
- `latest_message_at` — `MAX(timestamp)` of stored messages, with its
  `timestamp_kind`.

Asserted distinguishable (tests): sync 10:00 / observed through 09:58 / latest
message 09:42 are three different values; a source observed recently with no
messages has `observed_through` set and `latest_message_at = null`; a later
failed run keeps `last_succeeded_at`, `observed_through` and `complete_through`
from the last success beside `last_attempt_state = failed` and its token;
partial coverage advances `observed_through` but not `complete_through`
(caveat `complete_before_observed`); yesterday's complete sync queried today
yields timestamps and an empty caveat list; values survive a store reopen;
freshness on a result does not depend on the question asked.

## 3. Wire — freshness on all five envelopes, nothing else changed

Every response of `memory_search`, `memory_timeline`, `memory_context`,
`memory_recent` and `memory_conversations` now carries `freshness` beside
`items`, `coverage`, `truncated`, `query_scope` (and citations). It is a
**required** field of both result dataclasses, so a result without it cannot
be constructed. Coverage and citation key sets are asserted unchanged;
discovery's coverage remains `store_inventory` and gains no claim about the
source. No path, SQL, host or user identifier, and no boolean verdict in the
freshness dict (asserted). **No tool was added: still exactly 4 / exactly 9**
(runner tests, proxy, and the live probes below).

## 4. Sync — the selected source, never another

`memory_sync.py` now builds its source through the bridge's own
`active_source()`: absent selection → the visual store; `database` without an
injected reader → `reader_not_configured`, exit 1, **no store created, no
visual fallback**; an unknown name → `source_unknown`. A successful sync
reports freshness in its summary. Still operator-run, foreground, idempotent
through the M1 ingestor; a structural test asserts no scheduler, thread,
event loop, signal or subprocess module is imported.

## 5. App — Sync Memory, and the packaging boundary

**The boundary.** The memory layer is Python; the app ships no Python runtime
and none of the bridge's dependencies. Executing the sync from the app would
introduce an unsupported runtime dependency, so the app does not fake it.

**The honest subset, implemented.** `Ingestion/MemorySync.swift` defines the
state (`MemorySyncPhase`: idle / running / succeeded(counts) / failed(fixed
token)), the freshness summary the app shows (three separate dates, last run
state and failure token, coverage token — no boolean), and the seam
`MemorySyncRunning`. The production `UnavailableMemorySyncRunner` runs
nothing and reports `runnerUnavailable` ("runs from the operator command line
in this build"). `AppModel` owns `syncMemoryNow()`: it checks the
local-storage consent **before** reaching the runner, refuses to overlap, asks
the runner for the app's own visual source only, and refreshes freshness on
success; withdrawing consent resets the phase immediately, disables the
action, and keeps the last freshness (withdrawal is not a delete request).
The Settings panel **Memory** shows Source · Last successful sync · Observed
through · Complete through · Latest stored message · Coverage · Last sync
state, and a **Sync Now** button disabled without consent, with running
state, success counts, and failure messages that carry fixed tokens only.

Swift: **187 tests / 15 suites** (177 + 10 `MemorySyncTests`: fresh install
refuses without reaching the runner; active source visible; consented sync
runs and refreshes; freshness fetched when not inline; repeat sync;
sync failure distinct from partial coverage with last-good state kept;
selected source unavailable without substitution; revoke takes effect
immediately and deletes nothing; production runner reports the gap; failure
messages carry no path or content).

## 6. Live freshness gate

Corpus: 「项目组」, 3 messages, complete coverage window
[`BASE`, `BASE+1000`], last successful sync at `BASE+2000`, latest message
`BASE+400` (< observed-through), synthetic "now" = `BASE + 7 days` stated in a
one-off prompt that explains the fields. `SKILL.md` unused.

| Task | Tools | Coverage returned | Freshness on results | Behaviour | Grade |
|---|---|---|---|---|---|
| after the boundary: last three days, 合同变更? | `memory_conversations`, `memory_search` | `not_observed`, `trustworthy_empty=false` | yes (observed_through 1757001000, last_succeeded_at 1757002000, latest 1757000400) | *…观察截止到 observed_through=…，早于你询问的时间段…不是「没有」，而是记忆没有覆盖到那之后。* | **PASS** |
| covered window, 发票? | `memory_conversations`, `memory_search` | `observed_complete`, `trustworthy_empty=true` | yes | *…记录里没有人提到「发票」（coverage 为 observed_complete，trustworthy_empty=true…）* | **PASS** |
| latest vs observed-through | `memory_conversations`, `memory_recent` | `observed_partial` (unbounded recent) | yes | *…latest_message_at = 1757000400 … observed_through = 1757001000 … 两者之间没有存储的消息——这段时间确实被观察过，只是没有新消息产生，而非记忆缺失。* cited `msg:3c1a…` | **PASS** |

The model did not turn the old snapshot into a claim about newer events, did
claim "not mentioned" only under `trustworthy_empty = true`, and read the
latest-vs-observed gap as a fact. Each turn: exactly nine on its probe, 3
tools-bearing requests, 0 violations, 0 residue. Memory path in the memory
server env only. Report: 0 credential-shaped strings, no private path.

## 7. Gates

| Suite | M2.2b | M2.2c |
|---|---|---|
| `memory/` | 268 | **291** (+15 freshness, +3 wire, +5 sync) |
| `bridge/` | 72 | **72** unchanged |
| `shadow/` | 131 | **134** (+3 M2.2c harness; 122 pre-existing unmodified) |
| Swift | 177 / 14 | **187 / 15** |

## 8. Product direction, recorded

```
User explicit action  (operator CLI today; app "Sync Now" once packaging allows)
    ↓
MessageSource  (the selected one; never substituted)
    ↓
Memory ingestion  (M1 ingestor; idempotent; atomic)
    ↓
MemoryStore
    ↓
Freshness + Coverage  (separate facts, both on every result)
    ↓
Read-only Memory MCP  (exactly 5 tools; 9 on the wire with the bridge)
    ↓
Claude
```

No background scheduler. No agent sync. No memory write tools.

## 9. Remaining before M2.3 (skill integration)

1. **Close the packaging gap or decide not to** — either bundle a runtime for
   the memory layer or keep sync operator-side and say so in product copy.
   The app's state machine and panel are ready for a real runner.
2. Whether the app should own the memory store *location* (today an
   operator-side explicit path) so the panel can read freshness directly.
3. Skill design: how a digest cites memory and states freshness beside
   coverage — every live gate so far used a one-off prompt.
