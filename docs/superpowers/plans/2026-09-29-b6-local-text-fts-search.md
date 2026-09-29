# B6 Local Text FTS / Search MVP — Implementation Plan

**Date:** 2026-09-29 · **Branch:** `v2/rewrite` · **Baseline commit:** `2935a31`
**Status:** Plan of record for this session. Authorises no schema bump, no
Memory/Bridge change, and no push.

## 0. Goal

Add a genuinely useful *local* full-text search over the message evidence this
app currently stores, while keeping `MessageStore` the only source of truth and
treating the index as rebuildable derived state.

## 1. Locked architecture

- New actor `LocalMessageSearchIndex` in
  `apps/WeChatCompanion/WeChatCompanion/Ingestion/LocalMessageSearch.swift`.
- Its database is `sqlite3_open(":memory:")` with
  `CREATE VIRTUAL TABLE … USING fts5(…, tokenize='trigram')`.
- Provenance lives in UNINDEXED columns: `source`
  (`visual` / `archive_attributed` / `archive_unattributed`), plus the visual
  message id or the archive `import_id` + `sequence`.
- No persistence. The index is rebuilt on first use after a relaunch and is
  destroyed when the process ends or local persistence is withdrawn.
- **No schema bump.** Message schema stays at v5. Memory and Bridge source are
  not touched.
- FTS is a *candidate finder only*. Every hit is re-read from the canonical
  store; a row that no longer exists is dropped.
- The query compiler turns user text into a quoted FTS phrase. Raw user input
  never reaches `MATCH` as grammar, so `OR`, `NEAR`, `*`, `"`, `%`, `_` and
  emoji are literal.
- Queries shorter than the trigram minimum (1–2 characters) use a bounded,
  parameterized canonical `instr()` scan instead, so `车` / `我` / `L` are still
  findable.
- Result cap 100 everywhere; excerpt bounded.

## 2. Files

| File | Change |
|---|---|
| `Ingestion/LocalMessageSearch.swift` | NEW — index actor, compiler, read models, canonical read-only accessors |
| `Ingestion/MessageStore.swift` | MODIFIED — chunked read-only enumeration accessors for the build |
| `Ingestion/LocalMessageHistory.swift` | MODIFIED — owns the index, invalidation wiring, consent gate |
| `AppModel.swift` | MODIFIED — `Destination.search` + search UI state |
| `ContentView.swift` | MODIFIED — `SearchView` |
| `WeChatCompanionTests/LocalMessageSearchTests.swift` | NEW |
| `WeChatCompanion.xcodeproj/project.pbxproj` | MODIFIED — explicit file references |

## 3. Indexed / excluded

**Indexed:** visual message text and sender; archive attributed record text and
sender; archive unattributed record text.

**Excluded:** attachment bytes, filenames, extensions, paths, hashes, OCR, PDF
text, Memory, Daily Summary, Reminders, AI responses, display names, search
history.

## 4. Invalidation

Every canonical write that changes searchable text invalidates the index:
visual persistence (append/prepend), archive import, retention sweep, Delete All
History, consent withdrawal, and a failed/cancelled rebuild. Display-name and
attachment-only changes deliberately do not invalidate it.

## 5. Test plan (RED first, per behaviour)

Tokenizer runtime; Chinese/English/mixed substrings; every literal-grammar
case; empty and overlong query rejection; 1- and 2-char fallback; each source;
attachment metadata and Memory content never matching; display name as label
only; unlinked stays unlinked; visual and archive remain distinct rows;
vanished canonical row dropped; the four invalidation paths; consent off and
withdrawal; no canonical write during search; 100-result cap; chunked build of a
large corpus; cancellation leaving no poisoned index; read model exposing no
internal path/hash; bounded excerpt; source filter.

## 6. Execution order

1. Write the failing test, run it red, implement minimally, run green.
2. Focused Search suite, then the full Swift suite between real-store
   fingerprints (app quit).
3. `git diff --check`, review the staged diff, one B6 commit, no push.
4. Re-freeze MemoryWorker, clean signed build from the final commit, install to
   `~/Applications/WeChat Companion.app`, `codesign --verify --deep --strict`,
   compare build vs installed SHA-256 for the three executables, reduce
   PlugInKit to exactly one installed registration, run the installed
   `paths/status` smoke check.
5. Real acceptance: aggregate counts and a random-UUID zero-result query only.
   Never read or print real result content.
6. Vault: `Current Status.md`, `Next Actions.md`, `Architecture.md`.

## 7. Explicitly not in this phase

Exact-row navigation, OCR, attachment search, cross-source dedup, native
`WeChatDataAdapter`, AI/semantic search, persisted search history.
