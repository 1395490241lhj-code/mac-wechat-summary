# B0 — Archive persistence architecture (design only)

**Status.** Proposal. No production schema, no persistence code, no MCP or
Memory change is made by this document. Phase A is sealed at `b41ab2e`;
persistence is **not implemented**.

**Question.** Where does a parsed native WeChat archive go on disk, without
changing the meaning of anything already stored there?

---

## 1. The contracts that already exist

Read from the source at `b41ab2e`, not from memory.

### `messages` / `conversations` (`MessageStore`, `user_version = 1`)

| Column | Means | Depended on by |
|---|---|---|
| `sequence` | signed, monotonic **within a conversation**; appends take `max+1`, backfill takes `min-1` | `append`, `prepend`, `messages_by_position`, the reconciler's ordering |
| `visible_time` | the string WeChat *displayed* (「昨天」). **Never a date** | F-005; quoted verbatim, never parsed |
| `first_observed_at` | when **we saw it on screen**. Not a send time | `applyRetention`, `get_recent_messages` |
| `sender`, `ownership`, `kind`, `confidence` | visual extraction facts | `MessageIdentityKey` |

`MessageIdentityKey` = (sender, ownership, visibleTime, text, kind). It is an
**identity** key, never a uniqueness key: two genuinely distinct messages with
the same text produce the same key, and `FrameReconciler` separates them by
*position*, not by hashing.

`reconciliationTail` is `recentMessages(limit: 120)` ordered by `sequence`;
`headKeys` is the oldest 120. Both feed run-alignment. **Any row in `messages`
is a candidate for frame alignment.**

### Consent, retention, delete — verified behaviour

- **Consent off ⇒ no database file at all.** `LocalMessageHistory.open()` is the
  only thing that constructs a `MessageStore`, and it runs only while enabled.
- **Retention** deletes `WHERE first_observed_at < cutoff`, then orphaned
  conversations, then `wal_checkpoint(TRUNCATE)`.
- **Delete All History** deletes the rows, **then removes the file plus its
  `-wal` and `-shm`**, then reopens empty if consent is still on.

That last fact matters more than it looks: *anything stored in this file is
already covered by Delete All History, by construction.*

### Bridge / MCP (`bridge/store_access.py`)

- `SUPPORTED_SCHEMA_VERSIONS = {1}` — an unknown `user_version` **fails closed**.
- `REQUIRED_TABLES = {conversations, messages}` checked with
  **`issubset(present)`** — additive tables do **not** break it.
- `mode=ro` + `PRAGMA query_only = ON`; every query is a fixed statement.
- `wechat_companion_mcp.py` publishes `supported_schema_versions` in `status`.

### Memory

`memory_sync` reads **through** `MessageSource`, never the raw tables, so it
sees exactly what the bridge exposes. The memory store is its own file with its
own `user_version` — and, unlike `MessageStore`, it already has the migration
state machine this document recommends adopting.

### The data flow, and where an archive would attach

```
visual capture → ExtractionCoordinator → FrameReconciler → MessageIngestor
                                                              ↓ [consent]
                                        conversations / messages  (user_version 1)
                                                              ↓ mode=ro
                                        MessageSource → 4 read-only MCP tools
                                                              ↓ explicit sync
                                        MemoryStore (own file) → 5 memory tools

native ZIP → [Phase A, sealed] ZIPArchiveReader → discriminated transcript
                                                              ↓
                                        ▲ THIS DOCUMENT: where does it land?
```

---

## 2. Option comparison

| | **A** extend `messages` | **B** same DB, separate tables | **C** separate DB |
|---|---|---|---|
| Semantic safety | **Unsafe** — see below | Safe: visual tables untouched | Safe |
| Migration | rewrite/backfill columns | additive only, `1 → 2` | none for v1 |
| Consent | inherits | **inherits structurally** | needs its own gate |
| Delete All | inherits | **inherits via file removal** | must be extended by hand |
| MCP compat | breaks the read model | additive; `{1,2}` | second gate, second path |
| Memory | archive silently becomes "messages" | archive invisible until a source is added | same, plus a second reader |
| Cross-source dedup later | already conflated — unfixable | possible, explicit | possible |
| Attachments later | no room | room | room |
| Ops complexity | low | low | **highest** |

### Why A is rejected — not on taste, on the reconciler

Archive rows have no `ownership`, no `kind`, no `confidence`, no `visible_time`,
and Shape B has no `sender`. Inserting them means NULLs or invented values, and
then:

1. `reconciliationTail`/`headKeys` return the newest/oldest 120 rows of
   `messages`. An import of 83 archive rows **evicts the visual tail** and
   feeds archive keys into `FrameReconciler.longestOverlap`. Frame alignment
   would start matching screen content against imported history.
2. `sequence` is the visual append/prepend cursor. Archive rows must occupy
   sequences, which either collide with or displace the visual cursor.
3. `first_observed_at` would have to be invented for rows we never observed —
   and it drives both retention and `get_recent_messages`, so an import would
   surface as "recent" or vanish on the next sweep.

The instruction was to prove every reconciler/query semantic stays valid. It
does not. **A is rejected.**

### Why C is rejected

The precedent for a separate file is the memory store, and D-018 gives the
reason: memory is *below* the reader boundary and must not become a property of
one reader. Archive evidence is not that — it is the same user's local chat
history, under the same consent, the same retention choice and the same delete
action. Splitting it buys isolation we already have and costs:

- **Delete All History would have to be extended by hand** to a second file plus
  its `-wal`/`-shm`. Forgetting it leaves a complete chat copy on disk after the
  user asked for deletion. That is the one failure mode this project cannot
  ship.
- A second file can be created while local-persistence consent is off unless
  separately gated — i.e. a second consent model, which §8 forbids.
- A second retention sweeper, a second version gate, a second thing to back up.

### Recommendation: **Option B**

Same `messages.sqlite`, separate archive tables. It inherits consent, retention
scope and Delete All **structurally** rather than by remembering to. The
bridge's `issubset` check means additive tables are already tolerated. The cost
is a real one and is accounted for in §7: `user_version` 1 → 2, which touches
`store_access.py` and one Swift test.

---

## 3. Proposed schema (v2, additive)

Shape A and Shape B stay **different rows in different tables**. Phase A used a
sum type precisely so that "no attribution" is unrepresentable as an empty
string; flattening it into one nullable table would undo that at the storage
layer.

```sql
-- Source-side conversation identity. NOT the same thing as a visual
-- conversation, and never linked to one by title similarity (D-019).
CREATE TABLE archive_conversations (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Identity as the *source* expresses it. Sensitive (it carries chat
    -- identity), stored under the same consent as `conversations.title`
    -- already is, and never reported through MCP or diagnostics.
    source_conversation_key   TEXT    NOT NULL UNIQUE,
    first_imported_at         REAL    NOT NULL,
    last_imported_at          REAL    NOT NULL,
    -- Equivalence to a visual conversation is UNKNOWN until an explicit basis
    -- says otherwise. NULL is the honest default, not a missing value.
    canonical_conversation_id INTEGER REFERENCES conversations(id) ON DELETE SET NULL,
    linkage_basis             TEXT    CHECK (linkage_basis IN ('operator', 'source_provided')),
    CHECK ((canonical_conversation_id IS NULL) = (linkage_basis IS NULL))
);

-- One accepted import. The unit the user consented to and the unit retention
-- and idempotency both operate on.
CREATE TABLE archive_imports (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    archive_conversation_id INTEGER NOT NULL
                              REFERENCES archive_conversations(id) ON DELETE CASCADE,
    -- Deterministic over the PARSED transcript, never over the file bytes (§6).
    import_fingerprint      TEXT    NOT NULL UNIQUE,
    source_type             TEXT    NOT NULL CHECK (source_type = 'wechat_native_archive'),
    transcript_shape        TEXT    NOT NULL CHECK (transcript_shape IN ('attributed', 'unattributed')),
    record_count            INTEGER NOT NULL CHECK (record_count >= 0),
    -- When this local copy entered our history. The retention clock (§5).
    imported_at             REAL    NOT NULL,
    -- Which parser produced these rows, so a later grammar fix can find and
    -- re-derive what an older one wrote.
    reader_version          INTEGER NOT NULL,
    -- The timezone the wall-clock readings were interpreted in. Attributed
    -- only: an unattributed transcript has no time to interpret.
    time_zone_identifier    TEXT,
    CHECK (
        (transcript_shape = 'attributed'   AND time_zone_identifier IS NOT NULL) OR
        (transcript_shape = 'unattributed' AND time_zone_identifier IS NULL)
    ),
    -- Enables the composite foreign keys below.
    UNIQUE (id, transcript_shape)
);

-- Shape A. Lossless: every field Phase A parses, plus the interpretation.
CREATE TABLE archive_attributed_records (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id        INTEGER NOT NULL,
    transcript_shape TEXT    NOT NULL DEFAULT 'attributed'
                       CHECK (transcript_shape = 'attributed'),
    sequence         INTEGER NOT NULL CHECK (sequence >= 0),
    sender           TEXT    NOT NULL,   -- verbatim, never trimmed
    sent_at          REAL    NOT NULL,   -- minute precision; seconds always 0
    sent_at_text     TEXT    NOT NULL,   -- verbatim, so a tz re-reading is derivable
    text             TEXT    NOT NULL,   -- verbatim
    UNIQUE (import_id, sequence),
    -- An attributed record can only belong to an attributed import. A database
    -- invariant, not a convention someone has to remember.
    FOREIGN KEY (import_id, transcript_shape)
        REFERENCES archive_imports(id, transcript_shape) ON DELETE CASCADE
);

-- Shape B. Exactly what the artifact carries, and nothing else. There is no
-- sender column, no time column, no ownership and no kind -- so no query can
-- read an attribution that was never in the archive.
CREATE TABLE archive_unattributed_records (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id        INTEGER NOT NULL,
    transcript_shape TEXT    NOT NULL DEFAULT 'unattributed'
                       CHECK (transcript_shape = 'unattributed'),
    sequence         INTEGER NOT NULL CHECK (sequence >= 0),
    -- The record line verbatim, including the leading marker. Named
    -- record_text, not message_text: that one record is one message is an
    -- assumption the artifact does not establish.
    record_text      TEXT    NOT NULL,
    UNIQUE (import_id, sequence),
    FOREIGN KEY (import_id, transcript_shape)
        REFERENCES archive_imports(id, transcript_shape) ON DELETE CASCADE
);

CREATE INDEX archive_imports_by_conversation
    ON archive_imports(archive_conversation_id, imported_at);
CREATE INDEX archive_attributed_by_time
    ON archive_attributed_records(import_id, sent_at);
```

**No `messages` or `conversations` column is added, altered or dropped.**

Forbidden by construction, not by review: a fake sender, a fake timestamp, an
ownership placeholder, a synthetic confidence, a fake kind. None of those
columns exists in the unattributed table.

---

## 4. Conversation identity

Two different questions, deliberately kept apart:

| Question | Answered by |
|---|---|
| "Did these two imports come from the same **export source**?" | `archive_conversations.source_conversation_key` |
| "Is that the same **WeChat conversation** as this visual one?" | `canonical_conversation_id` + `linkage_basis`, and **only** with an explicit basis |

The visual side keys conversations on `title UNIQUE`. A real archive's
top-level directory name has been shown to carry conversation identity, but
**nothing has shown it is a stable one-to-one match for the visible title** —
and D-022 already forbids resolving identity by name, while D-019 forbids
inferring equivalence from content. So: **no automatic linking, ever, on title
equality.** `NULL` means *equivalence unknown*, which is the truth.

**Open (B2):** an export with no attachments may have no top-level directory,
so `source_conversation_key` has no derivation. The import service must then
either take an explicit key from the caller or refuse. Not decidable here —
all three observed archives had attachments.

---

## 5. Retention

**Recommendation: `imported_at`, for both shapes, symmetrically.**

`sent_at` is available for Shape A, and using it would be wrong:

> Import a 2022 conversation under a 30-day policy and every row is eligible for
> deletion the moment the import commits.

That is not what a retention setting means. The existing clock,
`first_observed_at`, means *when this local copy entered our history* — D-009
chose it because "the only trustworthy clock is when **we** saw a message".
`imported_at` is the same idea for a different intake: how long we keep **our
copy**, not how old the content is. It is also the only choice available to
Shape B, so it keeps the two shapes symmetric — the asymmetry the brief warned
against never arises.

**Retention deletes whole imports**, cascading to their records. A partially
expired transcript is a misleading fragment, and the import is the unit the user
consented to.

---

## 6. Idempotency — what B1 solves, and what it must not

**Solved: the same archive imported twice.** `import_fingerprint` is
`UNIQUE`; a second attempt is a no-op that reports "already imported".

**The fingerprint is computed over the parsed transcript, never the file.**
Hashing the bytes and parsing afterwards reintroduces the hash/parse TOCTOU that
PR #1 had to fix. Proposed input, canonically serialised:

```
source_type ‖ transcript_shape ‖ source_conversation_key ‖ record_count
  attributed:   for each record: sequence ‖ sender ‖ sent_at_text ‖ text
  unattributed: for each record: sequence ‖ record_text
```

then SHA-256. `sent_at_text` rather than `sent_at`, so re-interpreting a
timezone does not change an import's identity.

**Tradeoff.** Two exports with byte-identical parsed content and the same source
key collapse into one — which is exactly the case we want collapsed. A
re-export with one extra message is a *different* fingerprint and becomes a
second import holding overlapping content.

**Explicitly NOT solved: overlapping-export dedup.** PR #1 established that
repeated identical messages inside overlapping windows are
information-theoretically ambiguous, and that `hash(sender+time+text)` as a
uniqueness key silently deletes real messages. B1 stores overlapping imports as
separate imports and lets the duplication be visible. Deferred to B4.

---

## 7. Migration, and a defect to fix first

**`MessageStore.migrate` never reads `user_version` before writing it.** There
is exactly one write (`PRAGMA user_version = 1`) and no guard; `CREATE TABLE IF
NOT EXISTS` runs unconditionally. Consequences:

- A **newer** database opened by an older binary is silently **re-stamped
  downwards** while its newer tables remain. The stamp then lies, and the
  bridge's fail-closed version gate — the whole point of publishing a version —
  is defeated by the writer.
- The memory store already does this correctly (stepped, transactional,
  refuses a newer version). The app store should adopt the same shape.

**Proposed state machine, to ship in B1 *before* any v2 data exists:**

| Stored version | Action |
|---|---|
| `0` (new/empty) | create v1 tables, stamp 1, then step 1 → 2 |
| `1` | create archive tables in **one transaction**, stamp 2 |
| `2` | no-op |
| `> 2` | **fail closed** — do not migrate, do not stamp, do not open |
| unknown gap | fail closed |

Each step in its own transaction, so an interrupted migration leaves a file at a
known version rather than between two.

---

## 8. Consent

**Archive import may parse transiently; persistence requires local-persistence
consent ON.** Phase A already guarantees the transient half — the reader has no
persistence API and a structural test enforces it.

This needs no new policy, because it is already structural: with consent off
there is no database file and no `MessageStore`, so there is nowhere for an
import to go. An import attempted while consent is off must **fail with a clear
reason**, never create the store as a side effect. Choosing a ZIP is a choice
about *what* to import, not consent to start writing chat text to disk.

One consent model, unchanged.

---

## 9. Delete All History

Under Option B this is inherited: `deleteAllHistory()` removes the rows and then
the file plus `-wal`/`-shm`. B1 must still:

- add `DELETE FROM archive_unattributed_records / archive_attributed_records /
  archive_imports / archive_conversations` to the row-deletion path (cascades
  make the last two sufficient, but explicit is testable);
- add a test asserting that after Delete All, **no archive table contains a
  row**, and that no chat text survives in the `-wal`.

**Scope wording (B2, UI):** "Deletes WeChat Companion's local history." It must
not claim to delete the ZIP that Dukou holds — that is another app's data, and
this app neither owns nor should silently touch it.

---

## 10. MCP compatibility

| Database | Old visual tools | Archive evidence |
|---|---|---|
| `user_version = 1` | work unchanged | absent |
| `user_version = 2` | **work unchanged** | present, **not exposed** |

- `SUPPORTED_SCHEMA_VERSIONS` → `{1, 2}`. Required because `memory_sync` also
  reads through `store_access`; leaving it at `{1}` would break memory sync the
  moment the app migrates.
- `REQUIRED_TABLES` already uses `issubset`, so no change.
- `list_conversations` / `get_messages` / `get_recent_messages` keep **visual
  semantics only**. Shape B must never be squeezed into `NormalizedMessage`:
  that type's `sender` and `first_observed_at` would have to be invented.
- Wire-visible but tool-count-neutral: `status` reports
  `supported_schema_versions: [1, 2]`. The exact-N boundary is unchanged.

---

## 11. Memory

Not implemented here. The shape it must take:

```
HistoricalEvidence
├── attributedMessage(sender, sentAt, sentAtText, text, sequence)
└── unattributedRecord(recordText, sequence)
```

- **Shape A → canonical memory** only after the B4 promotion gate. Until then it
  is evidence from an archive, carrying its own provenance.
- **Shape B → semantic search only.** The discriminated type is what stops it
  answering "who said this, and when": there is no case that carries a speaker,
  so an attribution query cannot be served from it — the same discipline as the
  storage layer.
- **Coverage/freshness** must distinguish `historical_backfill` from `visual`
  observation. An import is not an observation: it must not advance
  `observed_through`, and a freshness line must be able to say "this window is
  covered by an import, not by anything we watched".

---

## 12. Out of scope, stated so it is not drifted into

FTS/search index (**B3** — a derived index is a second copy of chat text and
needs its own retention, delete and rebuild story before it exists); attachment
blobs, copies or association (**B5** — the exact-basename evidence is recorded
in F-029/F-030 and nothing more); cross-source dedup or promotion (**B4**); raw
ZIP retention (**never, by default** — §13).

## 13. Raw ZIP lifecycle

**Recommendation: do not copy the raw ZIP into WeChat Companion.**

```
Dukou-owned / user-selected ZIP → validate → parse → persist normalized evidence → release
```

Keeping a raw archive would create a second complete chat copy with a *different*
lifecycle from every rule above: it is opaque to retention (which works on rows),
it would survive row deletion unless separately handled, and "we can always
re-derive" is not a reason to keep a permanent copy of someone's conversation.
The normalized evidence is lossless for everything Phase A parses; what a raw
copy would additionally preserve is attachments, and that is B5's problem to
argue on its own merits.

If a future phase does need one, it must answer: where, under which consent,
under which retention clock, deleted by which action, and why normalized
evidence is insufficient.

---

## 14. Staging

| Stage | Scope | Acceptance gate |
|---|---|---|
| **B1** | version-aware migration; v2 archive tables; persist a parsed transcript; consent/retention/delete semantics | migration state machine tested at 0/1/2/>2; Delete All leaves no archive row and no `-wal` residue; retention expires whole imports by `imported_at`; re-import is a no-op; **bridge `{1,2}` and existing MCP tests green on both v1 and v2 databases** |
| **B2** | import service + user-selected file entry point; source key acquisition; UI copy | import refused with a clear reason when consent is off; no raw ZIP retained; scope wording accurate about Dukou; real-archive run on R1/R2/R3 |
| **B3** | historical evidence read model + search | Shape B cannot answer attribution; index has a tested delete/retention/rebuild story |
| **B4** | cross-source reconciliation / promotion to canonical message | positional alignment, provenance, explicit conversation linkage; no content-inferred equivalence |
| **B5** | attachment mapping | association evidence-backed, no blobs copied |

Each stage independently testable, privacy-safe, and semantically honest.

---

## 15. Risks and unresolved

1. **Downgrade re-stamping** (real, in shipped code). An older binary opening a
   v2 file stamps it back to 1. Mitigated by shipping the version-aware
   migration in B1 before v2 data exists; not fully removable for already-shipped
   builds.
2. **`source_conversation_key` derivation** when an export has no attachments —
   unknown, all three observed archives had one. B2 must handle or refuse.
3. **Variant selector still unresolved** (F-030). It does not affect the schema —
   both shapes are representable — but it does affect what B2's UI can promise.
4. **Overlapping imports duplicate content** by design in B1. Visible, not
   silent, and deferred to B4.

---

## 16. Design verification (this document, not a future implementation)

The DDL in §3 was executed, not just written. Against an in-memory database
already holding the v1 `conversations`/`messages` tables:

| Claim | Result |
|---|---|
| DDL applies on top of a v1 database | **OK** |
| attributed record on an unattributed import | **refused** |
| unattributed record on an attributed import | **refused** |
| attributed import with NULL timezone | **refused** |
| unattributed import carrying a timezone | **refused** |
| duplicate `import_fingerprint` (idempotency) | **refused** |
| canonical link without `linkage_basis` | **refused** |
| duplicate `(import_id, sequence)` | **refused** |
| deleting an archive conversation cascades | **OK** — 0 archive rows remain |
| visual tables affected | **none** |

The shape invariants are database invariants, enforced by composite foreign
keys and CHECK constraints, not by application discipline.

Against the real `bridge/store_access.verify_schema` at `b41ab2e`:

| Database | Result |
|---|---|
| v1, visual tables only | accepted |
| v1 **with the archive tables present** | **accepted** — additive tables are tolerated |
| v2 with archive tables, today's gate | refused, `state=schema_unsupported` |

The third row is the point: a v2 database fails on the **version**, never on
`schema_incomplete`. The whole compatibility cost of Option B is
`SUPPORTED_SCHEMA_VERSIONS = {1, 2}`.

---

## Verdict

**PHASE B ARCHITECTURE: READY FOR IMPLEMENTATION** — Option B, schema v2,
retention on `imported_at`, fingerprint over the parsed transcript,
version-aware fail-closed migration, no raw ZIP retained, no MCP semantic change.
