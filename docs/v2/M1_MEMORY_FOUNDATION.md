# M1 — Memory Foundation

**Sealed:** 2026-09-06 · **Branch:** `v2/rewrite` · **Scope:** local only,
deterministic only, synthetic fixtures only.

A source-neutral local memory and index layer between normalised messages and
any future retrieval or intelligence feature. It adds a package, `memory/`, and
changes nothing that already ships.

> **Not a real-data phase.** Nothing here reads a WeChat database, a WeChat
> window, or any external service. Every fixture in the suite is invented. No
> reader activation, no capture behaviour, and no MCP surface changed.

---

## 1. What was built

```
MessageSource                     bridge/message_source.py   (unchanged)
    ↓  NormalizedMessage / NormalizedConversation
MemoryIngestor                    memory/memory_ingest.py
    ↓
MemoryStore                       memory/memory_store.py
    ├─ canonical conversations / messages
    ├─ ingestion runs
    ├─ coverage / provenance
    └─ FTS5 index
    ↓
MemoryRetriever                   memory/memory_retrieval.py  (internal API)
```

`memory/memory_consent.py` is the gate every entry point passes through.
`memory/memory_identity.py` derives canonical identity and content
fingerprints. Both are pure and injectable; neither reads a clock or a network.

**Why SQLite.** The project already publishes a schema contract through
SQLite's `user_version` in two places (the app's store, the MCP bridge's
verification of it), already ships a read-only-at-the-engine-level discipline,
and already needs the store to be a single file a purge can delete along with
its `-wal` and `-shm` sidecars. FTS5 comes with it. Nothing weaker would carry
coverage; nothing heavier is justified by a local, single-writer store.

**A separate file, deliberately.** The memory store is *not* the app's message
store. It is never opened by the app, never migrated into it, and never
repurposes it. Coupling memory to the visual reader's own database would make
memory a property of one reader, which is the exact failure this layer exists
to avoid.

---

## 2. Schema (`user_version = 1`)

| Table | Purpose |
|---|---|
| `conversations` | canonical id, source, source id, display name, kind, first/last seen, first ingested, last observed |
| `messages` | canonical id, source, source id, conversation, identity mode, fingerprint, sender, ownership, sequence, timestamp + its meaning, visible time, kind, text, confidence, reply reference, first ingested, last observed, observation count, first/last run |
| `ingestion_runs` | run id, source, started/completed, state, failure state, counts |
| `coverage` | run, source, conversation (or NULL for source-wide), window, status, reason, count, recorded at |
| `messages_fts` + `messages_fts_map` | contentless FTS5 index and its canonical-id map |

Two structural absences are as load-bearing as the columns:

- **No column for an image, frame, screenshot, path, bubble geometry, provider
  response, or credential.** Asserted by a test, as in the app's own store. Raw
  capture data cannot be persisted here even by mistake.
- **No filesystem path is stored at all.** The store's own location comes from
  the operator's environment and is never written into the database. Run ids
  carry no hostname, username, or pid, so they are safe to paste into evidence.

The file is created `0600` inside a `0700` directory. An unrecognised
`user_version` fails closed rather than inferring shape from tables — the same
rule the MCP bridge applies to the app's store.

---

## 3. Identity, provenance and duplicates

**Canonical id** is a namespaced BLAKE2b digest, deterministic across processes
and runs, in one of two modes recorded per message:

- `source` — digest of `(source, source_message_id)`. Two sources may reuse the
  same local id without colliding.
- `derived` — digest of `(source, conversation, sender, ownership, kind,
  NFC-normalised text, whole-second timestamp)`, for a source with no stable
  identifier.

Current defaults: **both** shipped sources declare `source`. The app's store
assigns rowids it does not reuse, and the reader surfaces WeChat's own local id.
A source that is not listed defaults to `derived`, because trusting an unstable
id silently mixes records while deriving one merely costs a re-identification.

### Limitations of derived ids — stated, and asserted by tests

1. Two identical messages from the same sender in the same conversation at the
   same recorded second **collapse into one record**. That is a real loss of
   content, not a deduplication.
2. Any change to the text — a re-extraction that fixes one character — produces
   a **different** canonical id, so the correction is a new record rather than
   an update.
3. A derived id and a source-provided id for the same underlying message are
   not comparable. Changing a source's mode re-identifies everything it has
   ever contributed.

### Fingerprints are not identity

Every message also carries a `content_fingerprint` over its content and
position, **not** namespaced by source. It exists so that a store which was
deleted and re-read — renumbering every source id — is recognisable rather than
silently duplicated. Such a match is **counted and reported**
(`duplicates_detected`), never merged: merging would destroy the evidence that
two records came from different reads.

**Known M1 limit.** The fingerprint *is* scoped to a conversation, and
conversation identity is still per-source, so the same message arriving from two
different readers is **not** detected as a duplicate. Equating one reader's
conversation with another's needs a conversation-mapping layer that M1
deliberately does not have; guessing would merge chats that merely look alike.
A test asserts this limit rather than leaving it implied.

### One timestamp field, two meanings

`NormalizedMessage.first_observed_at` means *when this Mac first saw it on
screen* for the visual path and *when the source says it was created* for a
database read. The store records the value **and** which meaning it has
(`first_observed` / `source_created` / `source_reported`), so no later reader
has to infer it from a source name.

---

## 4. Coverage is first-class

Four states, and the difference between them is the point:

| State | Meaning |
|---|---|
| `observed_complete` | the source was read and the whole window was covered |
| `observed_partial` | read, but a limit was reached or paging was refused |
| `unavailable` | the source was asked and could not answer |
| `not_observed` | **no record covers this window** — never stored as a row; it is what the absence of a row means |

`assess_coverage()` reads records newest-first and lets the first record that
speaks to the window decide, so a later `unavailable` correctly overrides an
earlier success. A window reaching beyond an observed one is `partial`, never
`complete`. A source-wide record answers for every conversation in that source.

Every retrieval result carries a verdict, and
`MemoryResult.is_empty_and_trustworthy` is true **only** when nothing matched
*and* coverage is complete. Reporting "no messages" without consulting it is
making a claim the store did not make.

---

## 5. Ingestion — incremental, idempotent, atomic

```python
MemoryIngestor(store).ingest(source, records, conversations=..., coverage=...,
                             identity_mode=None, run_id=None, now=None)
MemoryIngestor(store).ingest_from_source(message_source, conversation_limit=50,
                                         message_limit=200)
```

- **Idempotent.** A repeat observation updates `last_observed_at`,
  `observation_count` and `last_run_id`, and inserts nothing. Overlapping
  windows are therefore free — which matters, because overlapping is the normal
  case for both readers.
- **Never destructive on re-read.** A thinner second observation cannot erase a
  field that is already known, and `first_ingested_at` never moves: when we
  first wrote a message down is a fact about us.
- **Atomic.** Every record is validated *before* anything is written, so one
  malformed record writes nothing at all. The run is still recorded, as
  `failed` with a fixed failure token — losing it would make a failure
  indistinguishable from never having run.
- **Coverage travels with the batch.** `ingest_from_source` records `complete`
  per conversation when the read came back under the limit and `partial` with
  `limit_reached` when it filled it; a refusing source produces `unavailable`,
  a failed run, and **nothing written**.
- `ingest_from_source` calls only the four questions in the `MessageSource`
  protocol. It cannot widen a reader's surface and never falls back to another
  source.

---

## 6. Retrieval — local, deterministic, lexical

`MemoryQuery(text, conversation_canonical_id, source, sender, ownership, start,
end, limit, order)` where `order` is `relevance` (requires text), `recent`, or
`oldest`. Limits are capped; one row past the limit is fetched so `truncated`
is measured rather than guessed. No field accepts SQL, a filter expression, or
a path.

**Chinese text and FTS5.** `unicode61` would turn a whole Han sentence into one
token, so "会议" could never match inside it. The index therefore stores a
*segmented* copy in which every ideograph is its own token, and a query is
segmented identically and issued as a **phrase** — exact substring matching for
Chinese, ordinary word matching for Latin. A scrambled pair ("会开") correctly
does not match. Every user term is quoted, so nothing a user types is read as
index syntax. The `trigram` tokeniser was rejected: it cannot match a query
shorter than three characters, and many Chinese words are two.

The index is **contentless** (`contentless_delete=1`, SQLite 3.43+): it holds
terms, not a second readable copy of the message, and a corrected message stops
matching its old text. A build without it fails closed with `fts_unsupported`
rather than creating an index that cannot forget.

**Every hit cites its message.** `MemoryHit.citation()` returns canonical id,
conversation, source, source id, identity mode, timestamp and timestamp meaning
— identifiers and provenance, no text. Anything built on top can therefore
point at the exact message it came from.

**No semantic retrieval.** No embedding, no vector index, no RAG. A test reads
the syntax tree of every implementation file and asserts the import set is a
subset of an allowlist (standard library, the reader boundary, itself).

---

## 7. Consent — and the gap, stated rather than bypassed

Writing normalised message text into a second local database is a second act of
local persistence, and the project already owns that question. Three conditions,
**all** required:

1. `WECHAT_COMPANION_MEMORY_ENABLED=1` — the operator asked, in this process.
2. `WECHAT_COMPANION_MEMORY_DB_PATH` — an explicit path. There is no default
   location, so no store is created anywhere nobody chose.
3. The app's own local-persistence consent, read from
   `com.lianghongjing.WeChatCompanion` / `persistence.allowsLocalMessageStorage`,
   observed to be **on**. Read, never written, never mirrored.

**The gap.** A Python process has no first-class channel to the app's consent —
only its preferences. When the flag cannot be observed at all (the app has never
run, the domain is absent, the read failed), the gate returns
`consent_unobservable` and **refuses**. It does not fall back to the operator's
assertion, because an operator asserting the user's consent is not the user's
consent. The consequence is deliberate: a memory store cannot be opened on a
machine where the app has never stored anything. Closing that properly is app
work — a first-class consent channel — and is not something this package may
paper over.

Withdrawal behaves as the product does: an existing store is not deleted when
consent goes off (withdrawal is not a delete request), but nothing further can
be written or read through this package while it is off, because every entry
point goes through the gate. A refusal carries no path and no content.

---

## 8. Existing behaviour is unchanged

Nothing under `bridge/`, `shadow/`, or `apps/` was modified in this phase; the
diff is entirely new files. Asserted structurally as well as by the diff: a test
reads every Python file in `bridge/` and `shadow/` and fails if any references
the memory modules. The dependency runs one way only — memory imports the reader
boundary, and never the reverse.

The four MCP tools, their arguments and their wire shape are untouched, the
agent runner is untouched, VisualReader capture is untouched, and the external
reader adapter is untouched. **M1 adds no MCP tool**; a test asserts no memory
module imports an MCP server or declares a tool decorator. The public memory
surface is an M2 decision and must not be settled here by accident.

---

## 9. Gates

| Suite | Before | After |
|---|---|---|
| `bridge/` | 72 | **72** (unmodified, all passing) |
| `shadow/` | 92 | **92** (unmodified, all passing) |
| `memory/` | — | **102 new, all passing** |

Total 266. Swift was not touched in this phase and was not re-run; the last
recorded Swift baseline is 169 tests in 13 suites and this phase changes no
Swift file.

Memory suite coverage: schema creation and re-open, unsupported-version fail
closed, structural absence of raw-capture columns, file mode, identity
determinism and cross-source non-collision, derived-id limits (both the
collapse and the re-identification), fingerprint behaviour including the
documented cross-source limit, first ingestion, identical re-ingestion,
overlapping windows, one new message, updated observation metadata, duplicate
source records in one batch, atomic rejection of a malformed batch with the run
still recorded, source mismatch, unknown identity mode, reply and
conversation-kind resolution, reader-driven ingestion with complete / partial /
unavailable coverage, all four coverage states and their precedence, Chinese
and English FTS including adjacency and syntax neutralisation, every filter and
ordering, limits and truncation, citations, coverage-aware empty results,
index re-sync after a correction, source-neutral canonical shape from both
reader shapes, the full consent matrix including fail-closed unobservable, and
the layering assertions.

---

## 10. Layering — what is and is not implemented

| Phase | Status |
|---|---|
| **M1 Memory Foundation** | **implemented, this document** — store, identity, coverage, ingestion, FTS, internal retrieval API |
| **M2 Memory Retrieval API** | **not implemented** — the public surface, and whether any of it becomes an MCP tool |
| **M3 Memory Intelligence** | **not implemented** — summaries, entities, semantic retrieval |
| **M4 Agent-facing memory tools** | **not implemented** — what an agent may ask memory, under what boundary |

Nothing above M1 exists in the repository. No embedding, vector store, RAG
path, task creation, entity graph, AI summary, reminder write, MCP tool, UI, or
change to real database access was added, and none is implied by this document.

### Recommended M2 scope

1. Decide the **public** query surface and whether it is an MCP tool at all —
   the same "verify on the wire" discipline applies, and every new tool widens
   the boundary the shadow runner asserts.
2. Decide how coverage reaches a caller. The internal API refuses to answer
   without a verdict; a public surface must not be allowed to drop it, or "no
   messages" becomes a lie the moment it leaves this process.
3. Decide whether per-message provenance becomes visible. The bridge withholds
   it today; memory records it. That is a product decision, not a refactor.
4. Close, or formally accept, the consent gap in §7.
5. Decide who runs ingestion, and when. M1 has an API and no scheduler, which
   is correct — an unattended ingest would collide with "no unattended
   execution" (D-011) and needs an explicit decision, not a default.
