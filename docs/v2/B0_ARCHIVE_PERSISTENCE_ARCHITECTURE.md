# B0 — Archive persistence architecture (design only)

**Status.** Proposal. No production schema, no persistence code, no MCP or
Memory change is made by this document. Phase A is sealed at `b41ab2e`;
persistence is **not implemented**.

**Question.** Where does a parsed native WeChat archive go on disk, without
changing the meaning of anything already stored there?

---

## 0. Review defects — all four confirmed, by execution

The first draft of this document was reviewed and four design defects were
raised. Every one reproduces against the draft's own DDL, and one is worse than
the review predicted.

### D1 — the canonical-link columns break deletion. **Confirmed, severity raised.**

The draft paired a nullable FK with `linkage_basis` under
`CHECK ((canonical_conversation_id IS NULL) = (linkage_basis IS NULL))`, expecting
`ON DELETE SET NULL` to leave an invalid row. What SQLite actually does is
evaluate the CHECK during the cascade and **abort the delete**:

```
DELETE FROM conversations WHERE id = 1;
  -> IntegrityError: CHECK constraint failed:
     (canonical_conversation_id IS NULL) = (linkage_basis IS NULL)
```

Run against the **real deletion statements** in the shipped app, with one linked
archive conversation present:

| Path | Statements | Result |
|---|---|---|
| **Delete All History** | `DELETE FROM messages;` then `DELETE FROM conversations;` | **FAILS** — `conversations` still holds 1 row afterwards |
| **Visual retention** | orphan cleanup `DELETE FROM conversations WHERE id NOT IN (...)` | **FAILS** |

So a single archive link would have made a visual conversation undeletable and
broken the one action this project cannot get wrong. **Correction:**
`canonical_conversation_id` and `linkage_basis` are removed from schema v2
entirely. Canonical linkage is B4's, and B4 should use a separate link relation
whose deletion semantics can be designed on their own (§4).

### D2 — sensitive conversation identity outlives its last import. **Confirmed.**

With retention deleting only expired `archive_imports` and letting records
cascade:

```
after retention: imports=0  records=0  conversations=[('SENSITIVE-CHAT-KEY',)]
```

The chat identity survives with nothing left to justify it. **Correction:**
retention deletes conversations left holding zero imports (§5), and
`first_imported_at` / `last_imported_at` are dropped — derivable from
`MIN/MAX(imported_at)`, stale after retention, and unnecessary persisted
metadata about a private conversation.

### D3 — the migration state machine was underspecified. **Confirmed as a gap.**

The shipped downgrade-restamp defect (F-031) stands. The draft's own state
machine, however, treated `user_version = 0` as "fresh" without looking at the
file. A database with tables and no stamp is unknown provenance, not a new file.
**Correction:** `0` splits into *0 with no application tables* (create) and *0
with tables present* (**fail closed**, `unversioned_existing_schema`), plus
explicit rollback semantics (§7).

### D4 — `{1, 2}` alone accepts a v2 file with no v2 objects. **Confirmed.**

Against the real `bridge/store_access.verify_schema` with only
`SUPPORTED_SCHEMA_VERSIONS` widened:

```
user_version = 2, conversations + messages present, archive tables MISSING
  -> ACCEPTED (version 2)
```

The gate would assert a shape the file does not have. **Correction:** the
required-table set becomes a function of the version (§10).

### D5 — delimiter concatenation is ambiguous. **Confirmed.**

The draft illustrated `field ‖ field`. Executed:

```
("a‖b", "c") -> 3efca701835282e1
("a",  "b‖c") -> 3efca701835282e1        # identical
```

Message text can contain any UTF-8, including whichever delimiter is picked.
**Correction:** a versioned, domain-separated, **length-delimited** canonical
encoding (§6); the same two tuples then hash differently, as do the newline
boundary cases.

### D6/D7 — `record_count`. **Both confirmed.**

`CHECK (record_count >= 0)` accepted an import with `record_count = 0`, and an
import claiming `999` records with zero child rows inserted without complaint —
SQLite cannot express `record_count == COUNT(children)`. Claiming that invariant
was database-enforced would have been false. **Correction:** `record_count` is
not persisted at all; it is derived (§3). The count still appears inside the
fingerprint, where it is computed once from the same in-memory transcript that
is inserted.

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

## 3. Proposed schema (v2, additive) — revised after review

Shape A and Shape B stay **different rows in different tables**. Phase A used a
sum type precisely so that "no attribution" is unrepresentable as an empty
string; flattening it into one nullable table would undo that at the storage
layer.

Four columns from the first draft are **gone**: `canonical_conversation_id`,
`linkage_basis`, `record_count` and `reader_version`. §0 says why, with the
executed evidence.

```sql
-- Source-side conversation identity, and nothing else. Whether this is the
-- same WeChat conversation as a visual one is a B4 question; schema v2 does
-- not pre-build half of the answer.
CREATE TABLE archive_conversations (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Identity as the source expresses it. Sensitive: it carries chat identity,
    -- so it is stored under the same consent as `conversations.title` already
    -- is, never reported through MCP or diagnostics, and deleted as soon as its
    -- last import expires.
    source_conversation_key TEXT NOT NULL UNIQUE
);

-- One accepted import: the unit the user consented to, and the unit retention
-- and idempotency both operate on.
CREATE TABLE archive_imports (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    archive_conversation_id    INTEGER NOT NULL
                                 REFERENCES archive_conversations(id) ON DELETE CASCADE,
    -- Deterministic over the PARSED transcript, never over the file bytes.
    import_fingerprint         TEXT    NOT NULL UNIQUE,
    -- Which canonical encoding produced that fingerprint. Without it, changing
    -- the encoding would silently turn UNIQUE into a no-op and re-imports
    -- would start duplicating instead of being refused.
    fingerprint_format_version INTEGER NOT NULL CHECK (fingerprint_format_version > 0),
    source_type                TEXT    NOT NULL CHECK (source_type = 'wechat_native_archive'),
    transcript_shape           TEXT    NOT NULL
                                 CHECK (transcript_shape IN ('attributed', 'unattributed')),
    -- When this local copy entered our history. The retention clock.
    imported_at                REAL    NOT NULL,
    -- Increments only when persisted interpretation semantics change, so a
    -- later grammar fix can find what an older one wrote. NOT the app version.
    archive_parser_version     INTEGER NOT NULL CHECK (archive_parser_version > 0),
    -- The timezone the wall-clock readings were interpreted in. Attributed
    -- only: an unattributed transcript has no time to interpret.
    time_zone_identifier       TEXT,
    CHECK (
        (transcript_shape = 'attributed'   AND time_zone_identifier IS NOT NULL) OR
        (transcript_shape = 'unattributed' AND time_zone_identifier IS NULL)
    ),
    UNIQUE (id, transcript_shape)
);

CREATE TABLE archive_attributed_records (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id        INTEGER NOT NULL,
    transcript_shape TEXT    NOT NULL DEFAULT 'attributed'
                       CHECK (transcript_shape = 'attributed'),
    sequence         INTEGER NOT NULL CHECK (sequence >= 0),
    sender           TEXT    NOT NULL,
    sent_at          REAL    NOT NULL,
    sent_at_text     TEXT    NOT NULL,
    text             TEXT    NOT NULL,
    UNIQUE (import_id, sequence),
    FOREIGN KEY (import_id, transcript_shape)
        REFERENCES archive_imports(id, transcript_shape) ON DELETE CASCADE
);

CREATE TABLE archive_unattributed_records (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id        INTEGER NOT NULL,
    transcript_shape TEXT    NOT NULL DEFAULT 'unattributed'
                       CHECK (transcript_shape = 'unattributed'),
    sequence         INTEGER NOT NULL CHECK (sequence >= 0),
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

**No `messages` or `conversations` column is added, altered or dropped, and no
archive table references `conversations` at all.**

Forbidden by construction, not by review: a fake sender, a fake timestamp, an
ownership placeholder, a synthetic confidence, a fake kind. None of those
columns exists in the unattributed table.

### Why each column exists

| Column | Reason it is persisted |
|---|---|
| `source_conversation_key` | the only identity the source expresses; needed to group imports of one export lineage |
| `import_fingerprint` | idempotency: re-importing one archive must be a no-op |
| `fingerprint_format_version` | if the canonical encoding ever changes, old fingerprints stop being comparable and `UNIQUE` silently degrades into "never matches". Recording the format is what makes that detectable and migratable — a concrete future migration reason, which is the bar for adding a column at all |
| `source_type` | the store will outlive one importer; a row must say what produced it |
| `transcript_shape` | the discriminator, and the parent side of the composite FK that keeps records on the right kind of import |
| `imported_at` | the retention clock (§5) |
| `archive_parser_version` | so a later grammar fix can find rows an older parser wrote. **Semantic, not the app build**: it increments only when persisted interpretation changes |
| `time_zone_identifier` | the assumption under which `sent_at` was derived, so a corrected reading stays derivable. NULL for Shape B, enforced |

### What is *not* persisted, deliberately

- **`record_count`.** SQLite cannot enforce `record_count == COUNT(children)`,
  so storing it would create an invariant the database does not actually hold —
  and §16 would then be claiming enforcement it cannot deliver. It is derived
  with a `COUNT(*)` on an indexed foreign key, and the shape column says which
  table to count. (The count still appears **inside the fingerprint**, where it
  pins cardinality in a value that is computed once, in one place, from the same
  in-memory transcript that is then inserted.)
- **Canonical conversation linkage.** Deferred to B4 — see §4.

---

## 4. Conversation identity

Two different questions, deliberately kept apart:

| Question | Answered by |
|---|---|
| "Did these imports come from the same **export source**?" | `archive_conversations.source_conversation_key` |
| "Is that the same **WeChat conversation** as this visual one?" | **nothing in schema v2** — deferred to B4 |

The v2 invariant is **`source identity ≠ canonical identity`**, and the way to
hold it is to have no canonical-identity column at all. The first draft tried to
express "unknown equivalence" with a nullable FK plus a paired `linkage_basis`
CHECK; §0 shows that construction blocking Delete All History. Half of a B4
design, frozen into a shipped schema, bought nothing and cost the delete path.

When B4 arrives, evaluate a **separate link relation** whose deletion semantics
can be designed on their own terms, roughly:

```sql
-- B4, NOT part of schema v2. Shape illustrative, deletion semantics undecided.
archive_conversation_links (
    archive_conversation_id,
    canonical_conversation_id,
    linkage_basis,          -- 'operator' | 'source_provided', never inferred
    linked_at
)
```

A separate relation makes "the link is gone" a row deletion rather than a
constraint violation, so removing a visual conversation can never be blocked by
an archive row. **Do not freeze that DDL now.** What stays true either way:
D-019 and D-022 forbid resolving identity by title similarity, so no automatic
linking, ever.

**Open (B2):** an export with no attachments may have no top-level directory, so
`source_conversation_key` has no derivation. The import service must take an
explicit key from the caller or refuse. Not decidable here — all three observed
archives had attachments.

---

## 5. Retention

**Recommendation: `imported_at`, for both shapes, symmetrically.**

`sent_at` is available for Shape A, and using it would be wrong:

> Import a 2022 conversation under a 30-day policy and every row is eligible for
> deletion the moment the import commits.

That is not what a retention setting means. The existing clock,
`first_observed_at`, means *when this local copy entered our history* — D-009
chose it because "the only trustworthy clock is when **we** saw a message".
`imported_at` is the same idea for a different intake. It is also the only clock
Shape B has, so the two shapes stay symmetric and the asymmetry the review
warned about never arises.

**Retention deletes whole imports.** A partially expired transcript is a
misleading fragment, and the import is the unit the user consented to.

### Exact deletion order

```sql
-- 1. expired imports
DELETE FROM archive_imports WHERE imported_at < :cutoff;

-- 2. records cascade via the composite FK (ON DELETE CASCADE) -- no statement

-- 3. conversations left holding no imports. `source_conversation_key` is
--    chat identity: it must not outlive the last import that justified it.
DELETE FROM archive_conversations
 WHERE id NOT IN (SELECT DISTINCT archive_conversation_id FROM archive_imports);

-- 4. if anything was removed:
PRAGMA wal_checkpoint(TRUNCATE);
```

Step 3 is the correction from review. Without it a sensitive conversation key
survives every one of its imports expiring — verified in §0.

**Acceptance tests (B1):**

- a conversation with two imports, one expired and one retained → conversation
  **remains**;
- a conversation whose **final** import expires → imports 0, records 0,
  conversations 0, and `source_conversation_key` no longer stored;
- checkpoint runs only when something was removed.

`first_imported_at` / `last_imported_at` were dropped from the draft: they
duplicate `MIN/MAX(archive_imports.imported_at)`, go stale the moment retention
runs, and no B1 query needs them. Less persisted metadata about a private
conversation is the better default.

---

## 6. Idempotency — what B1 solves, and what it must not

**Solved: the same archive imported twice.** `import_fingerprint` is `UNIQUE`;
a second attempt is a no-op that reports "already imported".

### Canonical encoding (normative)

The first draft illustrated `field ‖ field ‖ field`. **Delimiter concatenation
is forbidden**: §0 shows `("a‖b", "c")` and `("a", "b‖c")` hashing identically.
Message text can contain any UTF-8, including whatever delimiter is chosen.

```
domain                     = "wechat-native-archive-import-fingerprint"
fingerprint_format_version = 1

digest = SHA-256 over, in order:
    domain            as UTF-8, then 0x00
    format version    as ASCII decimal, then 0x00
    for each field, in fixed order:
        field tag     as ASCII, then 0x00
        byte length   of the UTF-8 encoding, as ASCII decimal, then 0x00
        raw UTF-8 bytes
```

Every field is length-prefixed, so no value can impersonate a boundary. Rules
this must satisfy, and which the encoding gives by construction: unambiguous;
deterministic; independent of any dictionary/set iteration order (the field
order is fixed by the spec, never by a `Dictionary`); no locale dependence; no
timezone-dependent `Date` formatting; exact UTF-8 preserved.

Fields, in this order:

| Tag | Value |
|---|---|
| `source_type` | `wechat_native_archive` |
| `transcript_shape` | `attributed` / `unattributed` |
| `source_conversation_key` | see below |
| `record_count` | ASCII decimal — pins cardinality |
| per record, attributed | `sequence`, `sender`, `sent_at_text`, `text` |
| per record, unattributed | `sequence`, `record_text` |

**`sent_at_text`, never the interpreted `Date`.** Re-interpreting a timezone
must not change an import's identity, and a `Date` has no canonical textual form
that is free of locale and calendar.

**`source_conversation_key` is part of identity.** The same transcript exported
from two different conversations is two different imports: identity is "this
content, from this source lineage", and collapsing across sources would let one
conversation's import silently satisfy another's. The cost is that a key
correction (B2's open question) changes the fingerprint — acceptable, because a
key correction genuinely is a different claim about provenance.

**One parse, one object.** The fingerprint is computed from the already-parsed
`WeChatNativeTranscript` value that is then inserted, inside the same import
flow. Never hash the file and reparse afterwards — that is the hash/parse TOCTOU
PR #1 had to fix — and never parse twice.

**Tradeoff.** Two exports whose parsed content and source key are identical
collapse into one, which is exactly the case we want collapsed. A re-export with
one extra message is a different fingerprint and becomes a second import holding
overlapping content.

**Explicitly NOT solved: overlapping-export dedup.** PR #1 established that
repeated identical messages inside overlapping windows are
information-theoretically ambiguous, and that `hash(sender+time+text)` as a
uniqueness key silently deletes real messages. B1 stores overlapping imports
separately and lets the duplication be visible. Deferred to B4.

---

## 7. Migration, and a defect to fix first

**`MessageStore.migrate` never reads `user_version` before writing it.** There
is exactly one write (`PRAGMA user_version = 1`) and no guard; `CREATE TABLE IF
NOT EXISTS` runs unconditionally. A **newer** database opened by an older binary
is silently **re-stamped downwards** while its newer tables remain, so the stamp
lies and the bridge's fail-closed gate is defeated by the writer. F-031.

### State machine (revised)

| Stored version | Precondition | Action |
|---|---|---|
| `0` | **no application tables present** | create the complete current schema in one transaction, stamp 2 |
| `0` | **any application table present** | **FAIL CLOSED** — `unversioned_existing_schema` |
| `1` | required v1 objects verified | migrate 1 → 2 in one transaction, stamp 2 **only after every v2 object exists** |
| `2` | complete v2 schema verified | no migration |
| `> 2` | — | **FAIL CLOSED** — never stamp downward, never modify schema |
| gap / unknown | — | **FAIL CLOSED** |

`user_version = 0` must **not** be read as "fresh". A file with tables and no
stamp is an unknown provenance, and guessing a legacy layout without evidence
that one ever shipped is how a migration destroys data. There is no evidence of
a legitimate unversioned layout, so the honest action is to refuse and say so.

**Rollback semantics.** Each version step runs in its own transaction and stamps
its version inside that transaction, so an interruption leaves the file at
exactly one known version — never between two. A failed step rolls back its own
objects and the stamp together; the file remains at the prior version and the
next launch retries the same step. There is no partial-v2 state to detect.

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

## 10. MCP compatibility — version-specific, not a widened set

**`SUPPORTED_SCHEMA_VERSIONS = {1, 2}` alone is not sufficient**, and §0 proves
it: with only that change the bridge accepts a database stamped `2` whose
archive tables do not exist, because `REQUIRED_TABLES` only ever described the
visual subset. The stamp would assert a shape the file does not have — the exact
failure the version gate exists to prevent, arriving through the reader instead
of the writer.

The required set must be **a function of the version**:

| version | required tables |
|---|---|
| 1 | `conversations`, `messages` |
| 2 | `conversations`, `messages`, `archive_conversations`, `archive_imports`, `archive_attributed_records`, `archive_unattributed_records` |

Still a **subset** check, so an unknown additive table stays tolerated — that
property is what makes future additive versions cheap and must be kept.

**Schema version is never inferred from tables.** The stamp is the claim and the
tables are checked against it; deducing a version from what happens to be
present would reintroduce exactly the "infer the shape from the tables it
happens to find" behaviour the bridge was written to avoid.

**Indexes and columns are deliberately *not* part of the contract.** The check
answers "does this file have the objects this version's queries name", and every
bridge query is a fixed statement over those tables. Adding column-level
verification would couple the reader to details it never reads and would fail on
a benign additive column. Recorded as a decision, not an oversight.

| Database | Old visual tools | Archive evidence |
|---|---|---|
| `user_version = 1`, complete | work unchanged | absent |
| `user_version = 2`, complete | **work unchanged** | present, **not exposed** |

- `list_conversations` / `get_messages` / `get_recent_messages` keep **visual
  semantics only**, on both versions. Shape B must never be squeezed into
  `NormalizedMessage`: that type's `sender` and `first_observed_at` would have
  to be invented.
- `{1, 2}` is required regardless, because `memory_sync` reads through the same
  gate and would break the moment the app migrates.
- Wire-visible but tool-count-neutral: `status` reports
  `supported_schema_versions: [1, 2]`. The exact-N boundary is unchanged.

**Planned bridge tests (B1):**

| Case | Expected |
|---|---|
| v1 complete | accepted |
| v2 complete | accepted |
| v2 missing **any** archive table | `schema_incomplete` |
| v1 with an unknown additive table | accepted |
| unknown future version (3) | `schema_unsupported` |


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
| **B1** | version-aware migration; v2 archive tables; persist a parsed transcript; consent/retention/delete semantics | migration tested at **0-empty / 0-non-empty / 1 / 2 / >2**; Delete All leaves no archive row and no `-wal` residue, and **is never blocked by an archive row**; retention expires whole imports by `imported_at` **and removes conversations left with zero imports**; re-import is a no-op; fingerprint is canonical length-delimited and versioned, with the ambiguity cases from §0 as tests; **version-specific** bridge schema check green on v1, v2, and refusing a v2 file missing an archive table |
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
   unknown, all three observed archives had one. B2 must handle or refuse. Note
   this interacts with §6: the key is part of the fingerprint, so a later key
   correction changes an import's identity.
3. **Variant selector still unresolved** (F-030). It does not affect the schema —
   both shapes are representable — but it does affect what B2's UI can promise.
4. **Overlapping imports duplicate content** by design in B1. Visible, not
   silent, and deferred to B4.
5. **`record_count` is derived, not stored**, so a caller wanting it pays a
   `COUNT(*)`. Cheap on an indexed foreign key, and the alternative was an
   invariant SQLite cannot enforce.

---

## 16. Design verification (this document, not a future implementation)

The **revised** DDL was executed against an in-memory database already holding
the v1 `conversations`/`messages` tables.

| Claim | Result |
|---|---|
| DDL applies on top of a v1 database | **OK** |
| attributed + unattributed imports coexist | **OK** |
| Shape A row on a Shape B import | **refused** |
| Shape B row on a Shape A import | **refused** |
| attributed import with NULL timezone | **refused** |
| unattributed import carrying a timezone | **refused** |
| duplicate `import_fingerprint` | **refused** |
| `fingerprint_format_version = 0` | **refused** |
| duplicate `(import_id, sequence)` | **refused** |
| `archive_imports` has a `record_count` column | **no** — derived, as designed |

**The case that exposed D1, re-run against the revised schema:**

| Path, with archive rows present | Result |
|---|---|
| Delete All History (`DELETE FROM messages;` `DELETE FROM conversations;`) | **OK** |
| Visual retention orphan cleanup | **OK** |

No archive table references `conversations`, so a visual delete cannot be
blocked by archive data — the property D1 violated is now structural.

**Retention, executed in the §5 order:**

| Scenario | Result |
|---|---|
| conversation with one expired + one retained import | conversation **kept** |
| conversation whose **final** import expires | imports 0, records 0, conversations 0, **key no longer stored** |

**Fingerprint encoding:**

| Input | Delimiter draft | Canonical (§6) |
|---|---|---|
| `("a‖b","c")` vs `("a","b‖c")` | **identical hashes** | **distinct** |
| `("x\ny","z")` vs `("x","y\nz")` | distinct under a `‖` delimiter, identical under a newline one | **distinct** |

**Bridge, against the real `store_access.verify_schema` at `b41ab2e`:**

| Database | Result |
|---|---|
| v1, visual tables only | accepted |
| v1 **with the archive tables present** | **accepted** — additive tables tolerated |
| v2 with archive tables, today's `{1}` gate | refused, `schema_unsupported` |
| **v2 stamped, archive tables missing, `{1,2}` gate** | **accepted — the D4 defect** |

The last row is why §10 makes the required set version-specific rather than
merely widening the accepted versions.


## Verdict

**PHASE B ARCHITECTURE: READY FOR IMPLEMENTATION** — Option B, schema v2 with
no canonical-link columns, retention on `imported_at` including orphaned
conversation cleanup, a versioned length-delimited fingerprint over the parsed
transcript, a fail-closed migration that does not assume `user_version = 0`
means "fresh", version-specific bridge schema verification, no raw ZIP retained,
and no MCP semantic change.

Revised after review: all four reported defects reproduced and corrected, plus
two more found while reproducing them (`record_count`). B1 is not implemented.
