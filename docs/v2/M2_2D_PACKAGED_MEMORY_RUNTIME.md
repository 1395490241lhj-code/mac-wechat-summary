# M2.2d — Packaged Memory Runtime + App-Owned Store

**Sealed:** 2026-09-07 · **Branch:** `v2/rewrite` · **Builds on:** `2bd3b7b` (M2.2c)
**Scope:** make the existing Sync Now perform a real local foreground sync in a
normal packaged build, and give the memory store one app-owned location. No
scheduler, no polling, no agent-triggered sync, no memory write/sync MCP tool,
no tenth tool, no `SourcePolicy` exposure, `SKILL.md` untouched, consent
unchanged, no real WeChat data consumed.

---

## 1. Packaging strategy, chosen by measurement

The app is Swift; the memory layer is Python. Four candidate shapes were tried
against the app's **existing** hardened runtime, and three were rejected on
evidence rather than taste:

| Shape | Result |
|---|---|
| `/usr/bin/python3` or a user-installed interpreter | Excluded by the brief, and by the product: a normal install must not depend on one. |
| PyInstaller **onefile** | **Fails.** It unpacks CPython to a temp directory at run time; library validation refuses the mapping — *"code signature … not valid for use in process: mapping process and mapped file (non-platform) have different Team IDs"*. |
| PyInstaller **onedir**, loose directory in `Contents/Helpers` | **Fails at build time.** Xcode's signing walks into it: *"code object is not signed at all … In subcomponent: …/_internal/base_library.zip"*. |
| PyInstaller **onedir `--windowed`** → a nested `MemoryWorker.app` | **Works.** Own seal, own Team ID, hardened runtime intact. |

The rejected fix worth naming is the one **not** taken: a
`com.apple.security.cs.disable-library-validation` entitlement would have made
onefile work. The app currently has **no `.entitlements` file at all**; adding
one to weaken library validation so the build could be simpler is the wrong way
round, and would have quietly changed the app's security posture. The nested
bundle keeps hardening exactly as it was.

**Chosen:** a self-contained helper bundle, not an interpreter embedded in the
Swift process — the app never links or hosts Python, it runs a child and reads
its reply.

## 2. Worker protocol

`memory/memory_worker.py`, frozen into the helper. One JSON object in on
stdin, one out on stdout, then exit:

```
{"op": "sync",   "store_path": "…", "message_store_path": "…", "message_source": "visual",
                 "conversation_limit": 50, "message_limit": 200}
{"op": "status", "store_path": "…"}
{"op": "paths"}
```

Exit `0` success, `1` structured refusal/failure, `2` unusable request.
`OPERATIONS` is exactly `{sync, status, paths}` — there is no write, delete,
link or ingest verb to name. No shell, no command string, no second request,
request capped at 64 KB, limits clamped. Errors are fixed tokens; an unexpected
exception becomes one fixed sentence.

**It never emits an absolute path.** `paths` answers
`Library/Application Support/WeChatCompanion/memory.sqlite` — where the store
is, not whose home it is in. A test asserts no reply carries the temp path, the
hostname or the username.

**One ingestion engine.** The worker calls `memory_sync` → `MemoryIngestor`.
There is no second implementation.

## 3. Making the worker importable without the MCP SDK

The sync path was pure standard library except for one edge: `memory_sync`
reached `active_source()` through `wechat_companion_mcp`, which imports the
MCP SDK. Store access and source selection therefore moved, **unchanged**, into
`bridge/store_access.py`; the MCP bridge imports every name back and
re-exports it, so its tools, wire shape and tests are exactly what they were.
The bridge's source-level invariants (no writes, nothing on stdout, no
hard-coded home) now span **both** files rather than narrowing to whichever half
kept them. Bridge suite: 72, unchanged.

Consequence: the worker's entire dependency set is the standard library, so
nothing is downloaded into the bundle beyond CPython itself.

## 4. Build integration

`scripts/build-memory-worker.sh` — pinned **PyInstaller 6.11.1**, pinned
**CPython 3.12** (refuses another minor), builds `MemoryWorker.app`, sets
`LSBackgroundOnly`, and **probes the result with `env -i`**: if it cannot
answer `{"op":"paths"}` with an empty environment it is not self-contained and
the build fails.

`scripts/embed-memory-worker.sh` — an Xcode run-script phase. Copies the
helper to `Contents/Helpers/MemoryWorker.app`, then signs every nested Mach-O,
the Python framework, and the helper bundle with the *app's* identity
(`EXPANDED_CODE_SIGN_IDENTITY`, else `CODE_SIGN_IDENTITY`, else resolved from
`DEVELOPMENT_TEAM`) so library validation sees one Team ID throughout. **Debug
without a worker warns and continues** (the test suite and ordinary development
builds do not need a 21 MB PyInstaller run); **any other configuration fails the
build**.

`scripts/build-dev-app.sh` gained `verify_memory_worker`: the helper must be
present, `codesign --verify --strict` clean, and must answer its paths probe —
checked on the built app and again on the installed one.

**Measured on a real signed build** (`DEVELOPMENT_TEAM=5M5KT5ZG74`, automatic
signing): build succeeds; `codesign --verify --deep --strict` on the app —
*valid on disk*, *satisfies its Designated Requirement*; helper
`TeamIdentifier=5M5KT5ZG74`, `flags=0x10000(runtime)`; helper runs under
`env -i`.

**No release or notarization claim is made.** Only Debug + Apple Development
signing was exercised.

### Bundle size

| | |
|---|---|
| app without the worker | **6.2 MB** |
| bundled worker | **21.6 MB** |
| **app total** | **27.8 MB** |

## 5. App-owned store location

`memory/memory_paths.py` and `Ingestion/MemoryStoreLocation.swift` derive the
same canonical location beside the app's existing message store:

```
~/Library/Application Support/WeChatCompanion/memory.sqlite
```

Deriving creates nothing; only `prepare_store_directory` /
`prepareDirectory` does, and only after the consent gate. Directory `0700`,
store `0600`. It is **not a user setting** — no picker, no defaults key. Tests
and the operator CLI still inject an explicit path through the existing
`WECHAT_COMPANION_MEMORY_DB_PATH` seam. A test parses the Swift constants and
compares them with the Python ones, so the two cannot drift.

## 6. Sync Now, in production

`PackagedMemorySyncRunner` replaces `UnavailableMemorySyncRunner` **when a
worker is bundled**; without one the M2.2c behaviour stands and the app still
reports the packaging gap rather than faking a sync.

Fixed bundle-relative path
(`Helpers/MemoryWorker.app/Contents/MacOS/MemoryWorker`) — never `PATH`, never
a shell (asserted by reading the source). Child environment built from scratch:
`HOME`, a fixed `PATH`, `LANG` — a parent variable cannot select a different
source behind the app (asserted). Bounded by a watchdog that terminates the
child; the deadline covers the **read**, not just the wait, because draining
the pipe blocks until the child closes it. One sync at a time via the existing
phase machine; completion refreshes every displayed freshness field; failures
map to fixed states.

**A sync failure and incomplete coverage stay different things:** a worker
state containing `:` (e.g. `database:reader_not_configured`) becomes
`sourceUnavailable`, consent states become `consentWithheld`, anything else
`ingestionFailed`; coverage is shown from freshness and is derived only from
the boundaries that exist.

## 7. Consent

Unchanged and enforced **twice**. The app checks its own local-persistence
consent before spawning anything; the worker independently resolves the
app-owned `consent.state` through `memory_consent.resolve_consent`, so a
worker started by anything else still refuses. Activation variables say *which*
store, never *whether* — a test sets them and still gets
`consent_state_missing`. Fresh install: no store, sync refused, worker never
launched (asserted: the fake source records zero calls and no directory
exists). Revocation takes effect immediately, deletes nothing, and leaves the
last freshness visible as history.

## 8. Source selection

The worker sets its activation from the validated request and clears anything
inherited, then applies the bridge's rule verbatim. A `database` selection
without a reader returns `database:reader_not_configured` and **no store is
created** — it does not sync the visual store instead. An unknown name is an
invalid request. Reader activation semantics and bridge behaviour are unchanged.

## 9. Freshness after a packaged sync

M2.2c semantics hold across the process boundary: `last_attempted_at`,
`last_succeeded_at`, `observed_through`, `complete_through` and
`latest_message_at` remain distinct fields, and a later failed attempt keeps
the last successful boundaries beside `last_attempt_state = failed` and its
token (asserted in Python and in the Swift mapping). No `isFresh`, no
threshold.

## 10. End-to-end, through the real helper inside the signed app

Isolated `HOME`, synthetic consent plist, synthetic message store, no
`defaults` on `PATH`:

| Step | Result |
|---|---|
| first sync | `ok`, 1 conversation, 2 messages, **2 inserted** |
| second identical sync | **0 inserted, 2 updated** — idempotent |
| status | `runs_total: 2`, freshness complete, no path in the reply |
| store | created `0600` in the isolated tree |

The Python suite additionally runs the frozen helper as a real subprocess
(`test_the_frozen_worker_is_self_contained_and_syncs_end_to_end`), skipped
when the worker has not been built.

## 11. A defect this phase found and fixed

Wiring the packaged runner as `AppModel`'s default made **the test host**
resolve it: a test host is an app bundle, and it now carried a real worker
pointed at the real store. One Swift run created a real
`~/Library/Application Support/WeChatCompanion/memory.sqlite` from the user's
real `messages.sqlite`. The file was removed, and
`AppModel.defaultMemorySyncRunner()` now returns the inert runner under a test
host, with a test asserting the canonical store stays absent after a default
`AppModel` sync. Recorded because the guard only makes sense if the reason is
written down.

## 12. MCP surface unchanged

Default **exactly 4**; memory-enabled **exactly 9**
(`memory_conversations`, `memory_search`, `memory_timeline`,
`memory_context`, `memory_recent`). No `memory_sync`, `memory_status`,
write, update, delete or link tool. Freshness still travels inside the existing
read envelopes. `shadow/tests/test_memory_activation.py`: 32 passed.

## 13. Gates

| Suite | M2.2c | M2.2d |
|---|---|---|
| `memory/` | 291 | **326** (+21 worker, +7 paths, +7 adjusted) |
| `bridge/` | 72 | **72** unchanged (invariants now span both files) |
| `shadow/` | 134 | **134** unchanged |
| Swift | 187 / 15 | **204 / 16** (+17: packaged runner, location, test-host guard) |

## 14. Production architecture

```
macOS App
    ↓ explicit Sync Now (foreground, user-initiated)
bundled local Memory worker   Contents/Helpers/MemoryWorker.app
    ↓ selected MessageSource  (never substituted)
MemoryIngestor
    ↓
app-owned MemoryStore  ~/Library/Application Support/WeChatCompanion/memory.sqlite
    ↓
Coverage + Freshness
    ↓
read-only Memory MCP  (5 memory tools; 9 on the wire with the bridge)
    ↓
Claude
```

**No system Python dependency for the normal app path. No scheduler. No agent
sync. No memory write MCP tools. Consent remains app-owned. Reader/source
fallback remains forbidden.**

## 15. Remaining before M2.3 (skill integration)

1. Release/notarization of a bundle containing a nested helper is **untested**;
   only Debug + Apple Development signing was exercised.
2. The app does not yet point the memory MCP at the canonical store for an
   agent run; the operator still passes `--memory-db-path` explicitly.
3. Skill design: how a digest cites memory and states freshness beside
   coverage. Every live gate so far used a one-off prompt.
