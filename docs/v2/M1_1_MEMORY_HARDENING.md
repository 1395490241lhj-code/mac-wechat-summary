# M1.1 — Memory Foundation Hardening

**Sealed:** 2026-09-06 · **Branch:** `v2/rewrite` · **Builds on:** `a6d2c58` (M1)
**Scope:** local only, deterministic only, synthetic fixtures only. No MCP
tool, no AI, no embedding, no RAG, no scheduler, no reader change, no real data.

Three things were hardened before M2 gets to decide a public surface: what a
canonical id *is*, who owns consent, and how several sources' coverage adds up.

---

## 1. Identity — source observations, and a logical layer above them

### What an M1 canonical id turned out to mean

Every row in `conversations` and `messages` is a **source observation**: one
reader's account of one WeChat object, stable *within that reader*. M1's ids
are namespaced by source, so the visual store's row 17 and the database's local
id 17 never collide — but that difference was only ever the absence of a claim.
It was not proof of two messages, and M1.1 stops letting it read as one.

### The model

```
LogicalConversation  (logical_conversations)         LogicalMessage  (logical_messages)
        ↑ 0..n explicit links                                ↑ 0..n explicit links
SourceConversationObservation  (conversations)       SourceMessageObservation  (messages)
        logical_conversation_id: TEXT NULL                   logical_message_id: TEXT NULL
```

- **`NULL` means equivalence unknown.** It is the default for every observation
  and the state of every observation nobody has explicitly linked. It is not
  "unique" and it is not "distinct from everything else".
- **A link is an explicit assertion**, one row in `equivalence_links` with a
  `basis`, an `asserted_by` and a time. Only two bases exist: `operator` and
  `source_provided` (a reader that itself exposes a cross-source identity).
  There is deliberately **no** `text_similarity`, `fingerprint`, or
  `timestamp_sender_text` basis, and `link_observation` refuses any basis not
  in the set — the rule is enforced, not documented and hoped for.
- **Observations are never rewritten.** Linking sets a pointer; both rows keep
  their provenance, text, timestamps and citations. `observations_of()` returns
  every observation behind a logical object.
- **A contradiction is refused, not resolved.** An observation already linked
  to one logical object cannot be linked to another; the store will not pick a
  side between two explicit assertions.
- **Ingestion cannot link.** `MemoryIngestor` has no path to
  `link_observation`, asserted by test. Reading a source produces observations
  only.
- **Logical ids are deterministic**, seeded by the founding observation, so a
  rebuilt store re-running the same assertions produces the same ids.

### Headline test

Two identical synthetic messages — same text, same sender, same second — from
the two reader shapes remain **two rows with `NULL` logical ids** and no logical
object exists, until an operator-basis link joins them; after the link both
rows survive under one logical id and the citation contract is unchanged.

### Migration `user_version` 1 → 2

Deterministic and additive: three new tables, two nullable columns, two indexes.
A fresh store runs the v1 DDL then the migration, so there is exactly one path
to the current shape and it is the path an existing store takes. Tests build a
real v1 file with the v1 DDL, migrate it, and check rows survive, nothing is
populated, a second open is a no-op, the migrated shape equals a fresh one, and
a *newer* version still fails closed.

### Reconciliation boundary

Deciding *that* two observations are one object is not the store's job and
not M2's either: it needs either a reader that carries a cross-source identity
or a human. The store gives it somewhere to land and forbids it from landing on
a guess. Nothing in the repository produces links today.

---

## 2. Consent bridge — closing the M1 gap without weakening it

M1 could only see the app's bare preference boolean and refused when it could
not observe it. That refusal was right; the gap was that "never ran", "ran and
said no" and "state from a build that never wrote one" were indistinguishable.

### The state, owned by the app

`LocalPersistenceConsentState` (`apps/…/Ingestion/LocalPersistenceConsentState.swift`)
is written by `AppModel` under the key `consent.state` in the **same preference
domain that already holds the consent flag** — one settings system, one owner.
It is written on every `setAllowsLocalPersistence` and once at `AppModel.init`,
so a fresh install records an explicit "no" and a state left by an older build
is brought up to date from the flag it mirrors.

```
consent.state = {
  version: 1,                          // shape; a reader refuses any other
  allowsLocalMessageStorage: Bool,     // the existing consent
  allowsMemoryStorage: Bool,           // distinct field, same decision today
  generation: Int (>= 0, +1 per write),// newer-than without trusting a clock
  updatedAt: Double (seconds since 1970)
}
```

Five plist scalars. No chat, no sender, no path, no credential, no reader data
— a test pins the key set. `allowsMemoryStorage` is a separate field because
memory is a second copy of the text and may one day deserve its own toggle; it
is written from the same decision today because both are the one question
"may extracted text be written down", and a second toggle for one question is a
second place for the answer to drift.

### The gate, in Python

`resolve_consent` reads the state through `defaults export` (cfprefsd-accurate),
falling back to the plist only when the tool is unavailable, then validates it
**strictly** — unknown version, missing field, wrong type (a plist `1` is not
`true`), negative generation all reject the whole value. Outcomes:

| State | Meaning |
|---|---|
| `memory_disabled` / `memory_not_configured` | operator activation absent — **not consent**, only "this process was not asked" |
| `consent_state_missing` | no state: fresh install, or an app older than the format. **Denied.** |
| `consent_state_malformed` | a state that fails validation. **Denied wholesale.** |
| `consent_withheld` | a well-formed state that says no |
| `consent_unobservable` | the domain could not be read at all. **Denied.** |
| `consented` | the only state that yields a path |

`MEMORY_ENABLED=1` plus a path with no app state is denied. An existing
`memory.sqlite` with no app state is denied. Revocation is honoured on the next
decision. The app is the only writer.

### Gates

Swift **177 tests / 14 suites** (169 baseline + 8 new in
`LocalPersistenceConsentStateTests`): absence is denial; fresh install records
an explicit no at generation 1; grant and revoke advance the generation and the
booleans; an older build's flag-only state is upgraded on launch; four
malformed shapes are rejected; a malformed stored state is replaced by the next
write; the key set is exactly the contract.

---

## 3. Coverage composition — the M2 rule, pinned now

`compose_coverage(per_source) -> ComposedCoverage`, and every retrieval result
now carries `coverage_by_source` beside the aggregate `coverage`. Internal only.

Three principles, in priority order:

1. **Evidence is never erased.** A source that covered the window completely is
   listed in `complete_sources` whatever any other source did. An optional
   reader being partial or unavailable does not make the shipped reader's
   complete read less complete.
2. **The aggregate is the most cautious reading.** `complete` only when every
   consulted source is complete; `partial` when at least one observed and at
   least one did not fully; `unavailable` when every consulted source refused;
   `not_observed` when nothing observed anything.
3. **No complete source, no trustworthy empty.** `trustworthy_empty_possible`
   is true only when *every* consulted source is complete. Two partial sources
   do not add up to one complete one. `MemoryResult.is_empty_and_trustworthy`
   now consults this.

Which sources are "consulted": a named source alone, otherwise every source
that has ever recorded coverage. A source the store has never heard of is not
in the answer — the store cannot report on what it does not know exists.

**No fallback.** Composition never substitutes one source's verdict for
another's; `per_source` returns each verdict as its own, and an aggregate over
more than one source has `source = None` because it has no single provenance.

---

## 4. M1 preserved

Unchanged by construction or by the existing tests, all still passing: FTS
segmentation and matching, every retrieval filter and ordering, the citation
contract (`citation()` returns the same keys; `logical_message_id` is a
separate hit field), no MCP memory tool, no network or model import, no
scheduler, no change under `bridge/` or `shadow/`, no reader change. The
import allowlist and the "no existing module imports memory" checks still hold.

## 5. Gates

| Suite | M1 | M1.1 |
|---|---|---|
| `memory/` | 102 | **154** (+22 consent, +18 identity model, +15 composition, adjusted 1) |
| `bridge/` | 72 | **72** unchanged |
| `shadow/` | 92 | **92** unchanged |
| Swift | 169 / 13 | **177 / 14** |

Total 495.

## 6. Recommended M2 scope

1. The public query surface, and whether any of it is an MCP tool — a new tool
   widens the boundary the shadow runner asserts and needs a wire-verified gate.
2. How `coverage_by_source` reaches a caller without being droppable.
3. Whether per-message provenance and `logical_message_id` become visible.
4. Who may create equivalence links, through what, and audited how — the store
   accepts only `operator` and `source_provided`; nothing produces either yet.
5. Who runs ingestion and when (D-011 still forbids unattended runs).
