# DB Reader P4 — complete-container readiness (evidence gate)

> **SUPERSEDED IN PART — reconciliation enacted.** This document's
> `P4 definition requires reconciliation` verdict and its §8 "proposal only"
> were resolved by **D-040**, recorded in the vault `Decisions.md` on
> 2026-10-02. The authoritative current P4 definition now lives in the original
> design spec
> `docs/superpowers/specs/2026-09-18-db-reader-coverage-provider-design.md`
> §15 P4-A/P4-B, and the machine-checkable acceptance checklist is §13 of this
> document. §§1–12 retain the historical read-only assessment at the baseline
> below; their statements that container accounting is absent or the real gate
> is unexecuted are superseded by the later accounting/correction and fresh
> rerun. Current scored evidence is in §13 and
> `DB_READER_P4_A10_REAL_CONTAINER_CLASSIFICATION.md` §14. §8 is no longer a
> proposal, §12's original result is historical, and §6's matrix is read against
> D-040's role table. Where this document and D-040 could be read as disagreeing,
> D-040 and the spec §15 text win.

## Scope

This is the P4 evidence/design gate. It adds no Reader capability. It answers
one question: **what provable facts still separate the current code, the
existing synthetic gates and the historical bounded real evidence from
"complete-container compatibility"?**

Baseline commit: `71b83ac9f477ae73cc4136f74257c665e4558ec6` (`v2/rewrite`),
`origin/v2/rewrite` and the live remote at the same SHA, ahead/behind `0/0`,
clean working tree.

**No new real WeChat evidence was gathered in this capsule.** No real database
was opened, no real container was read, no database/WAL/SHM file was copied, no
snapshot was started, no real-data test was run, no real Keychain or credential
was read, and no LLDB, Frida, re-signing, process attach or WeChat launch
occurred. Only already-existing aggregate evidence, repository source and
synthetic fixtures were read.

`stash@{0}` (`wip capture experiments before system-window architecture`) was
verified present and left untouched.

This document is **not** a P4 verdict of MET or NOT MET, and it is not a
Database Mode promotion.

## Verdict

**P4 definition requires reconciliation.**

P4 has never received machine-checkable acceptance criteria, and its one
authoritative sentence in this repository bundles two different claims — one
that current code and existing evidence can support, and one that is not
provable in principle before future WeChat versions exist. §8 proposes the
reconciliation; it does not enact it.

## 1. The authoritative P4 definition

P4 appeared exactly once as a definition, in the approved design. **This is the
superseded wording, quoted for provenance; it is no longer an acceptance
criterion** (D-040):

> **P4 — Complete-container coverage and future-WeChat-version compatibility
> remain unproven.** E-022's evidence is scoped to one operator's current
> `message_0` and must not be generalised. No claim to the contrary may be made
> on the strength of this document.

`docs/superpowers/specs/2026-09-18-db-reader-coverage-provider-design.md:1365`

The same design's §2.2 non-goals forbid the claim the sentence asks for:

> 5. **No complete-container claim and no future-version claim.** E-022's
> evidence is scoped to one operator's current `message_0` and is not
> generalised here.

`…design.md:161`

That document also disclaims any promotion on its own strength: *"There is no
implicit promotion"* (`…design.md:1355`). Everything after it is a deferral,
not a specification.

P4 is tracked as **UNMET** in `docs/v2/DB_READER_STANDING_ACQUISITION_DESIGN.md:417`
and `docs/v2/DB_READER_REAL_MULTIPART_GATE.md:203`, as *P4 remains unmet* in
`docs/v2/DB_READER_PRODUCT_PROMOTION_DECISION.md:34`, and in the vault as the
largest untouched named P4 item — in a section that also called its evidence gap
*the standing blocker* `database-production-path-no-retained-access-material`.
§9 shows that coupling is a vault conflation, not a property of P4, and the
vault sections that carried it are now marked historical.

### Why the definition is unusable as an acceptance criterion

1. **It has no acceptance criteria.** There is no per-item checklist, no
   required/optional split, and no machine-checkable form anywhere in the repo
   or the vault. Every later P4 gate document names P4 as its boundary and then
   declares itself explicitly *not* P4.
2. **It conflates two independent claims.** "Complete-container coverage" is a
   statement about a specific container and is evidenced. "Future-WeChat-version
   compatibility" is a statement about containers that do not exist and cannot be
   evidenced by any test, because no suite can run against an unshipped version.
   §5 separates them.
3. **"Container" is never defined.** The implementation's container
   understanding is narrower than the phrase suggests: §4 shows that accounting
   is total inside one selected `message/` directory, not across the assembled
   container.
4. **A P4 required-role set was never fixed.** The code's read contract fixes one
   (`REQUIRED_MESSAGE_ROLES` is `{ordinary_message}`), but whether
   `biz_message_*.db` is inside or outside **P4** scope has never been decided by
   any decision record, gate or product document. §4.C. Without that, no honest
   pass/fail exists.

## 2. Existing synthetic evidence

All of the following are committed behavioural contracts over synthetic
fixtures. None of them observed a real schema.

| Contract | Where | Gate document |
|---|---|---|
| Closed 7-role inventory, exactly-once accounting, only `ordinary_message` admitted as a message source | `acquisition/database_inventory.py` | `docs/v2/DB_READER_P4_INVENTORY_COMPATIBILITY.md` |
| Role / schema compatibility / coverage stated as three separate claims, closed 6-state outcome vocabulary | `wechatdb/provider/compatibility.py` | `docs/v2/DB_READER_P4_SCHEMA_DRIFT_ACCOUNTING.md` |
| Typed, bounded, closed query surface (4 operations, 3 served + 1 refused) | `wechatdb/` query surface | `docs/v2/DB_READER_TYPED_BOUNDED_QUERY_SURFACE.md` |
| Coverage envelope, shard-local id paging, WAL/SHM-bounded replay | `wechatdb/`, `bridge/acquired_database_source.py` | `docs/v2/DB_READER_COVERAGE_GATE.md`, `docs/v2/DB_READER_ACQUISITION_FAST_LANE.md` |
| Identity catalog built from explicit session + contact + message evidence | `wechatdb/provider/identity_catalog.py` | `docs/v2/DB_READER_IDENTITY_CATALOG_GATE.md` |
| Optional native FTS index that never becomes a second truth | provider search spike | `docs/v2/DB_READER_NATIVE_FTS_SPIKE.md` |
| Acquisition contract, key descriptors, per-role fail-closed validation | `bridge/database_bootstrap.py` | `docs/v2/DB_READER_STANDING_ACQUISITION_DESIGN.md` |

Focused runs for this capsule: inventory `23 passed`; schema compatibility
`58 passed`; typed query surface `174 passed`;
`bridge/tests/test_database_bootstrap.py` `29 passed`, of which the
`-k generation` refusal subset is `2 passed, 27 deselected`;
`bridge/tests/test_acquired_database_source.py` `36 passed`; acquisition suite
`173 passed`; wechatdb suite `514 passed`; bridge excluding two MCP-dependent
collections `134 passed` (those two require the absent `mcp` package). No
real-data test was run.

## 3. Existing real evidence, and its exact scope

All real results below come from **one operator account, one container, WeChat
4.1.15**, gathered in earlier capsules and read here only as existing aggregate
reports. They are not re-verified in this capsule and they extend to no other
build, account or layout.

| Real observation | Value | Source |
|---|---|---|
| readable message parts | 7, plus 3 valid empty parts | `DB_READER_REAL_MULTIPART_GATE.md:49`, `DB_READER_IDENTITY_CATALOG_GATE.md` §4 |
| message rows in the WAL-aware snapshot | 532,860 | `DB_READER_REAL_MULTIPART_GATE.md:108` |
| conversations listed | 86 | `DB_READER_IDENTITY_CATALOG_GATE.md` §4 |
| conversations present in more than one part | 3 | `DB_READER_REAL_MULTIPART_GATE.md:79` |
| cross-part paging of one overlapping conversation | 1,015 / 1,015 rows over 3 pages | `DB_READER_REAL_MULTIPART_GATE.md:93-95` |
| public message-id collisions | 0 | `DB_READER_REAL_MULTIPART_GATE.md:114` |
| within-conversation sequence collisions | 0 | `DB_READER_REAL_MULTIPART_GATE.md:129` |
| WAL main-file-only read | missed 2 rows, lagged 62 seconds | `DB_READER_REAL_MULTIPART_GATE.md:154-155`, `GREENBUBBLES_ASSIMILATION_AUDIT.md:76` |
| identity, full catalog | 86 / 86 conversations | `DB_READER_IDENTITY_CATALOG_GATE.md` §4 |
| identity, session source alone | 85 / 86 | `DB_READER_IDENTITY_CATALOG_GATE.md:48` |
| identity, contact source alone | 85 / 86 | `DB_READER_IDENTITY_CATALOG_GATE.md:49` |
| identity, message `Name2Id` alone | 86 / 86 | `DB_READER_IDENTITY_CATALOG_GATE.md:50` |
| sender display values changed by catalog evidence | 990 / 1,000 | `DB_READER_IDENTITY_CATALOG_GATE.md:97` |

**Nothing here is a real schema-drift observation.** No real database has ever
been classified `compatible`; `compatible` is a synthetic contract, exactly as
the FTS spike's was.

## 4. Complete-container must be decomposed

### A. Message container completeness

For ordinary message shards the real evidence is strong: seven readable parts,
532,860 rows, full identity, no collisions, restart-safe paging across a part
boundary, and a WAL finding that produced a correct fix rather than a hopeful
one.

Its scope is **the seven message parts probed, not the `message/` directory's
contents and not that directory's layout.** `DB_READER_REAL_MULTIPART_GATE.md:16`
scopes it to "one operator-owned current Mac WeChat 4.1.15 multi-part message
**snapshot**"; line 42 records "the seven actual message parts were probed".
No real gate establishes what else lives in that directory, because no real
inventory of its non-`message_*.db` children exists. So the area is evidenced
for the ordinary shards that were opened; its residual weakness is scope
wording, not behaviour.

### B. Identity roles (session, contact)

Both roles are load-bearing in the real evidence: session and contact are the two
identity anchors `candidate_source_set()` requires, and each on its own
resolved 85 of 86 conversations.

The *structural* treatment is asymmetric with message parts, and the asymmetry
is narrower than "no envelope":

- **Session and contact do fail closed.** `identity_catalog._require_columns`
  (`wechatdb/provider/identity_catalog.py:35-47`) requires `SessionTable` to
  carry `username` and `contact` to carry `username, remark, nick_name`,
  raising the fixed token `identity catalog schema unsupported` otherwise; any
  `sqlite3.Error` during the read becomes the same token
  (`identity_catalog.py:98`). That token is one of three in
  `_SURFACE_REFUSALS` (`bridge/database_bootstrap.py:139-143`), so it maps to
  `BOOTSTRAP_UNSUPPORTED_GENERATION`. Synthetic-proven at
  `wechatdb/tests/provider/test_identity_catalog.py:251,268`.
- **What they do not have** is the *accounting* envelope message parts get. A
  message part passes through `acquisition/database_inventory.py`'s closed
  seven-role vocabulary and then `wechatdb/provider/compatibility.py`'s closed
  six-state outcome vocabulary, so a changed one becomes a named role with a
  named gap. `candidate_source_set`
  (`bridge/database_bootstrap.py:238`) names exactly three things —
  `message/`, `session/session.db`, `contact/contact.db`
  (`database_bootstrap.py:145-149`) — and session and contact skip the
  inventory entirely.

The real gap is **accounting and reporting parity, not fail-closed safety**: a
changed session or contact schema refuses correctly but stays invisible to the
role/gap vocabulary, so it cannot be accounted, counted or reported the way a
changed message part is. That is a genuine Reader-correctness gap, and a
narrower one than "no envelope" would be.

### C. Business messages — DECIDED under D-040 (this subsection is the pre-decision assessment)

> **Decided 2026-10-02.** D-040 kept the committed read contract: `business_message` is
> **not required**, is message-bearing, explicitly unsupported, and retains the standing
> `business_message_unread` gap. The reasoning below is the pre-decision analysis that
> D-040 resolved; read the decision, not this opening question.

`biz_message_<n>.db` is a recognised `business_message` role that is never
admitted as a message source and raises the standing gap
`business_message_unread`. That is honest accounting.

Two things are true, and keeping them apart is the point:

- **The read contract has already decided.** `REQUIRED_MESSAGE_ROLES`
  (`wechatdb/provider/compatibility.py:79`) is `{ordinary_message}`, with
  the committed comment *"Every other role is optional: it can be incompatible
  all day without weakening a message claim."*
  `acquisition/database_inventory.py:58` mirrors it as
  `SUPPORTED_MESSAGE_ROLE`. At the reader level, `business_message` is
  **optional** — already, and by a committed contract.
- **No P4-scope record has decided it.** Neither P4's one-sentence definition,
  any gate document, nor any `Decisions.md` entry states whether business
  messages fall inside P4. `biz_message` appears nowhere in the vault's
  Decisions, Experiments or Rejected Approaches records.

And it has **never been observed in a real container**. The only real-context
mention of `biz_message_*.db` is `GREENBUBBLES_ASSIMILATION_AUDIT.md:94`,
reporting what GreenBubbles recognises *by name* in a STUDY section — not a scan
of the operator's container. No gate performs a real inventory of the
`message/` directory's non-shard children.

The open question is therefore narrower than "required or optional": **must P4
override the read contract and make `business_message` a blocker?** This
gate does not answer that; §8.3 states the decision. Guessing either way would
be the completeness fantasy the capsule instruction warns against: guessing
*required* contradicts a committed contract, guessing *optional* silently
narrows a phrase that says "complete container".

### D. Native FTS

`search_index` is an **optional capability**. The inventory raises no gap for
it, and it can neither strengthen nor weaken coverage. No P4 record makes it
required, and its presence in the GreenBubbles audit implies no P4 obligation.
Not a P4 blocker.

### E. Media

`media` is likewise an **optional capability**, with the same accounting
treatment as `search_index`. Not a P4 blocker.

### F. Auxiliary / unknown databases

The defensible reading of "complete container" is:

> every relevant component is accounted for, required roles are supported,
> optional and unknown roles are explicitly bounded.

It is **not** "every database must be fully parsed". The committed inventory
already implements the accounting half — total, order-independent,
exactly-once classification, counts-only rendering, unknown databases surfaced as
gaps, and an inventory gap that caps an otherwise-complete read at
`observed_partial` / `partial_inventory`.

**The limit of that accounting is its scope.** `inventory_message_directory()`
(`acquisition/database_inventory.py:250`) enumerates one directory via
`directory.iterdir()` (line 261). Combined with `candidate_source_set()`,
container-wide coverage does not exist today. Precisely:

- `session/session.db` and `contact/contact.db` **are** located and opened —
  `database_bootstrap.py:248-249` builds those paths by name and
  `source_fingerprint()` reads their salt evidence — but neither ever passes
  through the inventory.
- Every other database in the container is **never examined at all**: any other
  `.db` in `session/` or `contact/`, any other directory of the
  container, and any nested shard subdirectory inside `message/` (not a direct
  child).

This is the concrete sense in which "complete container" is currently "complete
`message/` directory, plus two named identity files".

## 5. Future-version safety vs future-version compatibility

These are different properties. Only one of them is achievable now.

**Safety — an unknown future generation fails closed.** Implemented and
synthetic-proven. `wechatdb/provider/compatibility.py` returns `unsupported` for
a schema that parses but is missing a table this reader requires, and
`malformed` for a broken one; both are `INCOMPATIBLE_OUTCOMES`, both fold into
the existing `inventory_gap`, and neither can be overridden by coverage. An
unknown database becomes `unknown_database`. An unsupported message candidate
becomes `unsupported_message_candidate`. A refused read carries a fixed token and
no read evidence. A future WeChat that renames or drops a required table is
therefore reported honestly instead of being read as if understood.

**Compatibility — every future version is compatible.** No test can establish
this and no amount of synthetic work can. It requires a future version to exist
and be observed. The current single-build real evidence proves nothing about it,
and the WAL main-file-only defect found in 4.1.15 is itself the counter-example:
a property that was only discoverable by meeting a real build.

Any future real gate on an unknown future generation would measure compatibility
for that generation, one build at a time. The P4 sentence cannot be satisfied as
written without being reinterpreted as the safety property.

## 6. Complete-container matrix

| Area | Required for P4? | Synthetic | Existing real | Current verdict | Remaining action |
|---|---|---|---|---|---|
| ordinary messages | yes | yes | yes | strong for the 7 probed 4.1.15 parts; the directory's other children were never inventoried | none beyond the §10 real observation |
| multi-shard | yes | yes | yes | proven: 7 parts, 3 overlapping conversations, cross-part paging | none |
| session identity | yes — **decided required by D-040**; still absent from the committed `REQUIRED_MESSAGE_ROLES`, which covers message parts only | refusal proven | yes | fail-closed on real and synthetic; absent only from the role/gap accounting vocabulary | documentation-only under D-040 (declaration + existing refusal); no subsystem |
| contact identity | yes — **decided required by D-040**; still absent from the committed `REQUIRED_MESSAGE_ROLES`, as above | refusal proven | yes | fail-closed on real and synthetic; absent only from the role/gap accounting vocabulary | documentation-only under D-040; no subsystem |
| WAL coherence | yes | yes | yes | proven for this build: main-only misses 2 rows, lags 62 s | none |
| business message | **no — decided (D-040)** | yes (gap raised) | none — never observed in a real container | explicit and unsupported; message-bearing but outside required P4-A | none — D-040 seals it; standing `business_message_unread` gap |
| native FTS | no — optional | yes | not applicable | not a P4 blocker | none |
| media | no — optional | yes (no gap) | not applicable | not a P4 blocker | none |
| unknown / auxiliary | accounted, not supported | yes | n/a | total **inside `message/` only**, not container-wide | container-wide accounting (Category 2) + one real observation (Category 3) |
| schema drift | yes | yes | **none** | synthetic contract only; no real schema ever classified `compatible` | none possible without real evidence |
| future-generation fail closed | yes, as safety | yes | n/a | synthetic safety proven; never compatibility | none — P4-B sealed by D-040 |
| typed bounded surface | yes | yes | n/a | synthetic contract proven, nothing product-wired | none |

## 7. Evidence classification

### Category 1 — Already proven, no new evidence needed

- Ordinary multi-shard message reading for the seven 4.1.15 message parts
  actually probed: 532,860 rows, 86 conversations, restart-safe paging, no
  message-id or sequence collisions. *Not* the directory's full contents, and
  not evidence about the directory's layout.
- WAL coherence for the current build: main-file-only is demonstrably wrong
  (2 rows, 62 s) and the SHM-bounded replay contract fixes it.
- Identity evidence reaching the existing parser/provider flow: 86/86
  conversations, 990/1,000 sender display values changed by catalog evidence.
- Unknown-generation fail-closed safety (synthetic contract).
- The typed bounded query surface's refusal discipline (synthetic contract).
- Role accounting being total inside the selected `message/` directory
  (synthetic contract).

### Category 2 — Provable from current code plus synthetic tests

- A required-role matrix naming which roles are required, which are optional
  capabilities, and which are accounted-but-unsupported.
- An explicit unsupported-optional-role assertion: search index and media must be
  able to raise no gap and must not weaken coverage.
- **Container-wide accounting coverage** — proving that every `.db`-bearing
  directory of a prepared source was inventoried. `inventory_message_directory()`
  takes one directory and `candidate_source_set()` takes one root, and
  `session.db` / `contact.db` are opened directly by name
  (`database_bootstrap.py:248-249`) rather than inventoried, so this is a
  focused *code* capsule, not a document exercise. Small, but production code,
  so it is deliberately left to a later capsule rather than smuggled in here.
- **Session/contact accounting parity.** They already fail closed; what is
  missing is the role/gap vocabulary, so a changed session or contact schema can
  be accounted, counted and reported the way a changed message part already is.
  Same scoping caveat: real code, later capsule.

None of these need real data, new architecture, or a new dependency.

### Category 3 — Requires new bounded real evidence

**Yes, exactly one thing:** container-wide role classification — every
`.db`-bearing directory of an assembled container, not only the selected
`message/` directory — observed on a real container.

- Why synthetic cannot prove it: the claim is about *this* container's directory
  set. A synthetic fixture asserts the reader's classification logic; it cannot
  reveal that a real container holds a directory nobody enumerated.
- Minimum observation: the set of relevant children of the selected `message/`,
  `session/` and `contact/` directories plus the container root —
  **role classification, counts and status only.** No message text, no names, no
  ids, no table digests, no account paths, no keys, no salts.
- Message content needed: **no.**
- Credential needed: only what the already-approved prepared-source path
  already requires. No new secret acquisition.
- D-005 involvement: none. No process memory, no LLDB, no Frida, no
  re-signing, no WeChat launch.
- Existing prepared-source path reusable: **yes**, if one is still safely
  prepared. Otherwise this gate must not run.

### Category 4 — Not required for current P4

- Native FTS search behaviour — optional capability, and the typed surface
  refuses `native_search` by design.
- Media support — optional capability.
- Full parsing of every database in the container — contradicted by the
  accounting-versus-support distinction in §4.F.
- Production reacquisition ability — see §9.
- Any claim about a future WeChat version — see §5.
- Any new large subsystem.

## 8. Reconciliation proposal (SUPERSEDED — enacted as D-040)

**This section is historical.** The reconciliation proposed here was **approved
and sealed** as **D-040** in the project vault `Decisions.md` on 2026-10-02, in
the same terms: the
P4-A/P4-B split, dropping future-version *compatibility* while keeping
fail-closed *safety*, deciding business messages explicitly, extending
accounting beyond one directory in a later focused code capsule, and keeping
native FTS and media outside required P4. Note the labels below are **P4a/P4b**
(accounting / parity) and are **not** the operative D-040 labels, which are
**P4-A** (current-reader container compatibility) and **P4-B**
(future-generation fail-closed safety). The operative definition is in the
approved design spec §15; the operative checklist is §13 below. Read §13, not
this section.

1. **Split P4.**
   - **P4a — container-complete accounting.** Every database in an assembled
     container is examined and classified exactly once; optional and unknown
     roles are explicitly bounded rather than silently included or excluded.
   - **P4b — role accounting parity for required roles.** `ordinary_message`,
     session and contact each get one role, one outcome and the gaps that follow
     from the pair, so a changed schema in any of them is accounted and
     reported rather than only refused. Fail-closed refusal with a fixed token is
     already sealed for all three and is *not* part of what P4b adds; what P4b
     adds is the accounting vocabulary the refusal currently bypasses.
2. **Remove future-version compatibility from P4.** Retain unknown-generation
   fail-closed *safety* as the standing requirement. Compatibility with any
   specific future version is a per-build real gate, never a sealed predicate.
3. **Decide business messages explicitly.** The committed read contract already
   makes `business_message` **optional** (`REQUIRED_MESSAGE_ROLES` is
   `{ordinary_message}`). P4 therefore has two honest options: leave that
   contract standing and record `business_message_unread` as an accepted gap
   inside P4, or explicitly override it and promote `business_message` to the
   required set, making its gap a P4b blocker. Either way the choice must be
   written down rather than left to inference — overriding an existing contract
   and inheriting an existing gap are both silent-narrowing failures otherwise.
   This is a product decision, not an inference from a file name.
4. **Extend accounting beyond one selected directory** in a later focused code
   capsule, keeping the existing counts-only rendering and fail-closed
   behaviour.
5. **Keep native FTS and media outside required P4 compatibility.**

With (1)–(3) fixed, most of Category 2 becomes machine-checkable and the single
Category 3 real gate becomes a confirmation rather than an open question.

## 9. The standing blocker is a P5/acquisition blocker, not a P4 blocker

Every repo document that names
`database-production-path-no-retained-access-material` scopes it to
**production acquisition**, never to reader correctness. The four citations:

- `docs/v2/GREENBUBBLES_ASSIMILATION_AUDIT.md:153` — Database "remains later
  P1/P2 and blocked **for production**" by it.
- `docs/v2/DB_READER_P4_INVENTORY_COMPATIBILITY.md:179` — the same wording.
- `docs/v2/DB_READER_TYPED_BOUNDED_QUERY_SURFACE.md:257` — listed as an
  unchanged blocker alongside the sub-gate's own limits.
- `docs/v2/DB_READER_P4_INVENTORY_COMPATIBILITY.md:146` — "the account-secret
  blocker is unchanged", stated separately from any P4 claim. This one is a P4
  sub-gate, so it cannot by itself prove the point; it is listed as the
  sub-gate's own acknowledgement, not as independent corroboration.

P4 itself is tracked as UNMET in
`docs/v2/DB_READER_STANDING_ACQUISITION_DESIGN.md:417` and
`docs/v2/DB_READER_REAL_MULTIPART_GATE.md:203`, and as "remains unmet" in
`docs/v2/DB_READER_PRODUCT_PROMOTION_DECISION.md:34` — **without** naming the
access-material blocker as its cause.

The vault is where the two got conflated. Across `Next Actions.md` and this
note's own earlier bullet there were **four** such statements: two calls of the
P4 evidence gap "not a design question but the standing blocker below" (the
readiness-assessment section, and the typed-query-surface section), and two
conclusions that deciding how real-schema evidence will ever arrive "is the
same blocker (`database-production-path-no-retained-access-material`) that
already gates the production path" (the readiness-assessment section, and the
schema-drift section). Each is now inside a section carrying an explicit
correction. Note that one of the four sat under a heading literally labelled
"Current direction", which is why a correction applied where an earlier review
pointed would not have found it — this count comes from grepping the token, not
from re-reading only the places a previous review named.

The question P4 actually asks is: *given a safely prepared source, does the
Reader work correctly and report honestly?* That question is answerable without
a retained production access path — and it has been answered, repeatedly, in the
existing real gates. The blocker concerns **establishing** a prepared source in
production without retaining access material: a P5 / product acquisition problem
under D-002 and D-036.

So: **the standing blocker does not block P4.** It blocks production
reacquisition. The vault lines that coupled them are corrected above and now
sit in marked-historical sections, so P4 is no longer unfinishable for a reason
that has nothing to do with the reader. Note this does not weaken P4: P4-A
still has real open items (§4.B, §4.C, §4.F, and A10), it is simply not
blocked by *this*.

## 10. Proposed next bounded real gate — design only, NOT executed

This gate was **not** run. It exists so the remaining Category 3 gap is
actionable without anyone improvising real-data behaviour later.

**Inputs.** An existing approved, safely prepared source path. No new
acquisition, no new snapshot, no new secret.

**Privacy.** Structural metadata only: role classification, counts, status. Never
recorded: message text, names, ids, table digests, account paths, keys, salts.

**Scope.** All relevant children of the selected `message/`, `session/` and
`contact/` directories plus the container root — what Category 3 needs, and no
more. Required roles only; optional roles are observed and bounded, not
supported.

**Success conditions** — machine-checkable, all three required:

1. Every observed database is classified exactly once.
2. No `.db`-bearing directory of the prepared source remains unexamined.
3. No role is claimed that was not observed.

**Failure policy.** Fail closed. Report unreadable or unclassifiable directories
explicitly and issue no compatibility claim.

**Cleanup.** No retained plaintext, copy, snapshot or workspace artefact.

**D-005.** Requires no process memory, no LLDB, no Frida, no re-signing, no
WeChat launch. If any of those would be required, the gate must not run.

**What this gate could not establish, even on success.** Future-version
compatibility (§5), business-message support (§4.C), and session/contact
accounting parity (§4.B — that is Category 2 code work, not an observation).
Their fail-closed refusal is not on this list; it already exists.

## 11. Review findings (read-only, after the matrix was written)

Four independent read-only reviews ran against this document, the repository and
the vault; the third and fourth re-ran the synthetic suites themselves. No
Critical finding stands. Every Important finding below was a real misstatement
in an earlier draft and is now corrected. They are recorded rather than
smoothed away, because two rounds of them were genuine overstatements about
evidence that does not exist.

| # | Finding | Severity | Disposition |
|---|---|---|---|
| 1 | §6 claimed business messages were "role observed, never read" — no gate ever observes `biz_message_*.db` in a real container | Important | corrected — §3, §4.C and §6 now say *never observed in a real container*, and name `GREENBUBBLES_ASSIMILATION_AUDIT.md:94` as a name-recognition STUDY note rather than an observation |
| 2 | §6 claimed session/contact had "no refusal envelope" and only a "synthetic partial" — both fail closed with a fixed token, synthetic-proven | Important | corrected — §4.B and §6 now say synthetic refusal proven, real exercised, and that the remaining gap is accounting/reporting parity |
| 3 | §2 reported "focused bridge inventory/version checks `2 passed`", mislabelling a `-k generation` subset of a 29-test file as the whole file | Important | corrected — both numbers are now given, with the deselection count |
| 4 | §3 cited five real-evidence rows at the wrong lines in `DB_READER_REAL_MULTIPART_GATE.md` | Minor | corrected — 79, 93-95, 114, 129, 154-155, each re-read from the file |
| 5 | §8.3 framed business-message scope as a free choice between required and optional, ignoring that the committed read contract already made it optional | Important | corrected — §8.3 now states the contract first, and names the two honest options: inherit the accepted gap, or explicitly override it |
| 6 | §9 said *every* repo/vault document scopes the blocker away from P4, but the vault coupled real-schema evidence to the blocker in more than one place | Important | corrected — §9 names the vault coupling sites and scopes its claim to repo documents. Later review passes found two more sites (three, then four); §9 was widened each time, and the final count comes from grepping the token rather than from re-reading only the places a review had pointed. |
| 7 | §1 called P4 the "largest untouched" item without noting that the same vault sentence also calls it the standing blocker | Minor | corrected — §1 states the coupling and points to §9 |
| 8 | §9's lead sentence claimed *every* document scopes the blocker away from reader correctness, but `DB_READER_P4_INVENTORY_COMPATIBILITY.md:146` is itself a P4 sub-gate, which makes it circular as proof | Important | corrected — §9 now scopes the claim to the four named repo citations and states plainly that the vault, not the repo, is where the two get conflated |
| 9 | §4.A, §6 and §7 described the real message evidence as covering the 4.1.15 `message/` *layout*, which no gate observed | Minor | corrected — the scope is now "the seven parts probed", and the absence of any real inventory of the directory's other children is stated |
| 10 | Finding 2's correction reached §4.B and §6 but not §7, §8.1 or §12, which still claimed session/contact lack a "structural compatibility envelope" and must "fail closed instead of degrading silently" — a defect that does not exist, with a remedy already implemented | Important | corrected — §7 Category 2, §8.1's P4b, §10's boundary and §12's *not MET* reasons now all say accounting/reporting parity, with fail-closed refusal explicitly excluded as already sealed |
| 11 | §8.1's P4b was unfalsifiable: its fail-closed clause is already true for all three roles, so the requirement could not fail and could not gate anything | Important | corrected — P4b now asks only for role accounting parity and states that refusal is already sealed and is not what it adds |
| 12 | §6 marked session/contact "Required for P4? yes" without noting that the only committed required-role set covers message parts, so "yes" is P4's undecided position rather than a contract fact | Minor | corrected — both rows now say so inline |

The reviewer also checked, and found absent, each of the failure modes the gate
brief named: optional capability written as required; single-build real evidence
written as future-version proof; role accounting written as role support;
fail-closed safety written as compatibility; synthetic schema written as real
WeChat evidence; and widened existing-evidence scope. The business-message
question is present and deliberately unresolved; the "complete container means
one `message/` directory" limit is surfaced in §4.F, §6 and Category 2.

The reviewer withdrew three of its own candidate findings after checking them:
the `candidate_source_set` and `inventory_message_directory` line citations are
correct, and §5's safety/compatibility separation verified clean against
`compatibility.py` and `database_bootstrap.py`.

The fourth review confirmed findings 1-9 resolved against the corrected text,
re-ran every synthetic suite and re-verified every §2 and §3 citation, and
raised 10-12. Those were corrected in the same round. A fifth confirmation pass
was requested and the reviewer did not return within three waits, so the
verification that findings 10-12 are fully closed — and that no further
internal contradiction of the same class survives elsewhere in the document —
was done by the author, not by an independent reviewer. **No Important or
Critical finding is known to remain open**; that is the honest limit of the
claim.

## 12. Result

**SUPERSEDED — see §13.** This was the pre-decision result. D-040 reconciled the
definition and scored it: **P4-B is MET; P4-A is 9 MET / 1 UNMET** (A10). The
result below is retained as the assessment that produced that decision and is
not the current verdict.

**P4 definition requires reconciliation.** (historical verdict)

- Not `P4 MET`: the definition has no acceptance criteria, business-message
  scope is undecided, container-wide accounting does not exist yet, and
  session/contact are refused fail-closed but stay absent from the role/gap
  accounting vocabulary that message parts already use.
- Not `P4 NOT MET`: the honest failure is definitional. Issuing a failing
  verdict would require deciding, silently, the two questions §8 raises —
  business-message scope, and directory-versus-container boundary — and would
  also misattribute a P5 acquisition blocker to P4 (§9).

One bounded real gate remains necessary (Category 3). Its design is in §10. It
was not executed and must not be executed without its own explicit
authorization and its own gate document.

## 13. Approved acceptance checklist (D-040, 2026-10-02)

This is the **one authoritative machine-checkable acceptance contract** for P4.
It resolves §8; the proposal text in §8 is historical and is not a competing
definition. Every item is scored `MET` or `UNMET` against committed evidence.
At the D-040 reconciliation, no real data was re-run to produce the checklist.
A10's real evidence remains historical UNMET. Its synthetic interpretation and
single machine predicate are reconciled in the A10 gate document §13; no
checklist score or D-040 decision changes.

### P4-A — current-reader container compatibility (per tested generation)

| # | Acceptance item | Status | Evidence |
|---|---|---|---|
| A1 | Required-role inventory and accounting is defined | **MET** | `REQUIRED_MESSAGE_ROLES = {ordinary_message}` (`wechatdb/provider/compatibility.py:79`), mirrored as `SUPPORTED_MESSAGE_ROLE` (`acquisition/database_inventory.py:58`); closed 7-role vocabulary `acquisition/database_inventory.py:38-54`; D-040 role table |
| A2 | Ordinary-message synthetic support | **MET** | `wechatdb/tests/provider/test_schema_compatibility.py` (`test_a_supported_schema_is_compatible`, `test_a_whole_set_of_compatible_shards_is_complete`); inventory `a5bbd0d` |
| A3 | Real multi-part message gate for the tested generation | **MET (scoped)** | 4.1.15 real gate: 7 readable parts + 3 valid empty, 532,860 rows, 86 conversations, 3 multi-part, 1,015/1,015 paged across a part boundary — `docs/v2/DB_READER_REAL_MULTIPART_GATE.md`, D-032. **Scope is the seven probed parts, not the `message/` directory's contents.** |
| A4 | Session/contact required-role contract | **MET (documentation-level declaration)** | Declared required by D-040. Existing behaviour: `_require_columns` raises the fixed token `identity catalog schema unsupported` (`wechatdb/provider/identity_catalog.py:35-47,98`), in `_SURFACE_REFUSALS` (`bridge/database_bootstrap.py:139-143`) → `BOOTSTRAP_UNSUPPORTED_GENERATION`; synthetic-proven by `wechatdb/tests/provider/test_identity_catalog.py` (line-referenced assertions at `:244,261`). **Accounting/reporting parity is not part of A4** — see A10 and the residual note below. |
| A5 | WAL coherence | **MET (scoped)** | Real 4.1.15: main-file-only read misses 2 rows and lags 62 s; WAL-inclusive read is coherent — `docs/v2/DB_READER_REAL_MULTIPART_GATE.md`; identity-catalog gate. Synthetic WAL fixtures also green. |
| A6 | Schema-drift fail-closed | **MET** | `ad1f2ab`; `unsupported` and `malformed` are both `INCOMPATIBLE_OUTCOMES`, both fold into `inventory_gap`, neither overridable by coverage. Evidence: `test_a_required_ordinary_shard_that_is_incompatible_is_never_complete` (`:428`, the `unsupported`/dropped-column case), `test_a_refused_generation_is_refused_and_not_parsed_on_best_effort` (`:444`), `test_a_compatibility_gap_never_overwrites_a_stronger_reason` (`:508`); the `malformed` case is the parametrized case at `:175` driving `test_a_parser_mandatory_column_missing_is_malformed_and_also_refused` (`:548-553`), and `gaps_for()` treats both outcomes identically (`wechatdb/provider/compatibility.py:279-282`). **Synthetic contract only — no real schema has ever been classified `compatible`.** |
| A7 | Unsupported/unknown message-bearing structures remain explicit gaps | **MET** | `business_message_unread`, `unknown_database`, `unsupported_message_candidate` (`acquisition/database_inventory.py:62-70`); `test_a_business_message_shard_is_never_absorbed_by_the_ordinary_reader`, `test_a_new_message_bearing_relation_keeps_the_shard_from_claiming_complete`; `test_unknown_and_candidate_roles_keep_their_existing_gap` |
| A8 | Typed bounded query contract | **MET** | `71b83ac`; `docs/v2/DB_READER_TYPED_BOUNDED_QUERY_SURFACE.md`; 58 test functions in `wechatdb/tests/provider/test_typed_query_surface.py`, incl. the post-review `math.isfinite` refusal fix (`wechatdb/provider/query.py:358`) and four AST guards (`test_typed_query_surface.py:626,806,926,969`). Nothing product-wired. |
| A9 | Optional FTS/media incompatibility does not weaken ordinary coverage | **MET** | `test_an_optional_role_is_accounted_and_never_probed`, `test_optional_role_states_can_never_strengthen_a_message_claim`, `test_optional_drift_does_not_move_the_message_coverage_verdict`; FTS spike `12a195a` — `native_search` is refused by design. |
| A10 | Bounded real container-wide classification confirms no unaccounted required/message-bearing role | **UNMET** | **The single remaining P4-A gap.** Fresh real rerun under the committed `265ed384` provenance-backed domain policy: `meets_requirements()` returned FALSE; machine unmet = `directory_unexamined`, `unknown_database`, `unsupported_message_candidate`. Accounting completed: 27 DB rows, 29 unique accounting keys, 0 duplicates, 15/15 direct directories; 13 unknown, 1 candidate, 2 nested/unexamined. Boundary split: `required_message` 12 DB / 1 unknown / 1 candidate; `required_identity` 3 DB / 1 unknown / 1 nested; `known_physical_only` 1 DB / 1 unknown (non-blocking); `ambiguous` 11 DB / 10 unknown / 1 nested. Source pre/post unchanged. See `DB_READER_P4_A10_REAL_CONTAINER_CLASSIFICATION.md` §16. All six earlier phases remain historical evidence; no production changes or retry. |

**P4-A is not yet `MET`.** It is `9 MET / 1 UNMET`, and the one `UNMET` item
is a real-evidence item, not a design question. Two consequences are recorded
honestly rather than folded into the verdict:

- **Container structural accounting now exists; complete-container acceptance
  remains UNMET.** The bounded primitive was introduced at `853d481` and its
  per-directory identity correction sealed at `906a4d8`, with single-predicate
  reconciliation at `a9b93d9`. The fresh reconciled A10 rerun
  accounts the root and every direct-directory identity exactly once, including
  required identity roles, but retains unknown/candidate gaps and explicit
  unexamined nested entries. This is structural accounting, not new parsing,
  recursive coverage, schema compatibility or a P4-A pass. See the A10 gate
  document §14; no further production work is authorized by that evidence.
- **Session/contact accounting parity** (the §6 matrix "remaining action") is
  **documentation-only under D-040**: a required-role declaration plus the
  existing fixed-token refusal is the whole contract. No subsystem is created
  for symmetry, and A4 is scored on that declaration, not on new code.
- **The container-domain boundary policy is sealed and has now been tested
  against the real container** (A10 gate document
  §15, synthetic/provenance-only). `required_message` for `message/`,
  `required_identity` for `session/` and `contact/`, `known_physical_only` for
  the one domain with independent root-layout provenance in this repository's
  own committed historical reader, and `ambiguous` — fail-closed — for
  everything else. Acceptance stays one predicate: `unmet_requirements()` now
  decides on role **and** domain boundary class, `evidence()` renders sanitized
  `domain_summary` aggregates by boundary class, and traversal width is
  unchanged. **This is policy, not real evidence: A10 stays `UNMET` and P4-A
  stays 9/10.** It removes no checklist item and requires no score change.

  The separately authorized fresh real rerun under that policy has now run
  (§16, real evidence). It confirms the policy behaves as sealed on real
  layout — exactly one unknown observation is exempted inside
  `known_physical_only` — while **12 unknown observations and both
  nested/unexamined structures remain blocking** in the required and ambiguous
  domains. **The score does not change: A10 stays `UNMET`, P4-A stays 9/10.**
  No domain was added, no blocker investigated and no retry performed. The
  residual blockers are now localized *in aggregate* to the ambiguous class
  and the two unentered nested structures; resolving them needs a new
  independent domain policy from provenance, not another look at the source.

### P4-B — future-generation fail-closed safety

| # | Acceptance item | Status | Evidence |
|---|---|---|---|
| B1 | Changed required schema fails closed | **MET** | `ad1f2ab`; `test_a_changed_generation_is_named_by_its_own_verdict`, `test_the_same_name_with_a_changed_schema_changes_the_outcome` |
| B2 | Malformed required schema fails closed | **MET** | `test_bytes_that_are_not_a_database_stay_unassessed_without_driver_text`, `test_a_refused_generation_is_refused_and_not_parsed_on_best_effort`; session/contact refusal at A4 |
| B3 | Unknown message-like role remains an explicit gap | **MET** | `test_unknown_and_candidate_roles_keep_their_existing_gap`; `test_a_new_message_bearing_relation_keeps_the_shard_from_claiming_complete` |
| B4 | No filename/role recognition implies compatibility | **MET** | `test_a_recognised_role_is_not_yet_a_compatible_schema` — recognition and compatibility are two separate claims |
| B5 | No future-generation compatibility claim without a new real gate | **RULE (GR-1) — not scored** | D-040 makes compatibility a per-build real gate, never a sealed predicate; spec §15 P4-B states it. Being unfalsifiable by a test, it is recorded as a governing rule below rather than counted as evidence. |

**P4-B is `MET`** — 4 of 4 scored items, with B5 recorded as governing rule
GR-1 below rather than counted as evidence. It is a safety claim, and safety is
provable without meeting the future version. The 4.1.15 WAL main-file-only
defect is the standing proof that some properties are discoverable *only* by
meeting a real build — which is why compatibility stays a per-build gate and is
never asserted here.

### Governing rules (outside the scored checklist)

These are **rules D-040 sets**, not checklist items. They are not scored `MET`
precisely because they cannot be falsified by a test: they constrain what any
future capsule or report is allowed to claim. Keeping them out of the scored
list avoids counting a definition as if it were an observation.

- **GR-1.** Compatibility with a specific WeChat build is a **per-build real
  gate**, never a sealed predicate and never a standing claim. Only the tested
  generation (4.1.15) carries the A3/A5 scoped results.
- **GR-2.** No future-version claim is made here or anywhere in this document,
  and no suite may be cited as evidence for one.

### Classifier scopes after container-level accounting reconciliation

D-040 makes session identity and contact identity **required** roles. This is
now reflected in `REQUIRED_CONTAINER_ROLES` and the named-anchor rules in
`acquisition/container_accounting.py`, introduced at `853d481` and corrected at
`906a4d8`. The fresh real rerun observes and structurally accounts both roles.

The message-directory contracts deliberately retain their narrower scope:

- `classify_database_name()` still maps the generic session/contact anchor
  basenames to `ROLE_AUXILIARY` in `acquisition/database_inventory.py`.
- `REQUIRED_MESSAGE_ROLES` remains `{ordinary_message}` in
  `wechatdb/provider/compatibility.py`; it governs message parts, not the
  container-level identity-role requirement.

These unchanged message-directory rules do not mean identity roles are absent
from the machine-required container contract. A4 still rests on D-040's required
role declaration plus the existing fixed-token refusal; container structural
accounting is now supplied by A10's primitive. A10 remains UNMET because the
fresh returned unknown/candidate/unexamined gaps fail its acceptance predicate,
not because a future accounting capsule must still reconcile identity roles.
The historical reporting capsule changed no production code. The subsequent
synthetic reconciliation in the A10 gate document §13 makes
`ContainerAccounting.meets_requirements()` (via `unmet_requirements`) the sole
structural acceptance predicate, including unknown/candidate blockers. Ordinary
shard shapes outside production `message/` routing remain unsupported candidates,
not required-role evidence. Physical container presence alone does not require
implementing every store; unknown/nested domains have no independently proven
exclusion and remain fail-closed. Business stays excluded with a visible gap;
FTS/media stay optional. The synthetic reconciliation widened no traversal and
accessed no real source. The separately authorized §14 real structural run used
that implementation unchanged. A10 remained UNMET after the fresh real rerun
under this predicate; P4-A stayed 9/10 and P4-B stayed MET 4/4. See the A10
gate document §14 for that real evidence, §§1–13 for prior phases, and §13 for
the boundary/gap semantics. §15 sealed the container-domain boundary policy
from independent provenance only — no source re-access, and no real rerun. The
separately authorized §16 fresh real structural rerun has now executed that
sealed policy against the operator-designated root and reported aggregate
counts by boundary class: the one sealed physical-only domain exempted exactly
one unknown observation, while 12 unknown observations and both
nested/unexamined structures remained blocking. **A10 remains UNMET; P4-A
remains 9/10; P4-B remains MET 4/4.** See the A10 gate document §16 for the
current real evidence. Next only: provenance-only / synthetic reconciliation of
the remaining ambiguous blocker classes, with no additional real-source access
until a new independent domain policy is sealed.

## 14. Residual reconciliation under the sealed residual policy (2026-10-02)

A **synthetic, public-provenance-only** capsule has now answered the two
questions §13 left open. **No real source was accessed**, no §16 count was used to
derive a rule, and no A10 evidence was re-run. Full ledger, sources, revisions and
licenses: A10 gate document §17.

**Identity truth is anchor-scoped.** Production opens `session/session.db` and
`contact/contact.db` by exact name, supplies them explicitly to the identity
catalog, refreshes only message shards, and never enumerates identity siblings.
Required identity is therefore the anchor, not the parent directory. An unknown
regular-file sibling beside a proven anchor stays visible and unsupported but no
longer blocks. Every way of failing to *prove* the anchor — missing, symlinked, a
directory, or an unlistable parent — still blocks, as does a message-bearing
candidate sibling, a refused (symlink) unknown sibling and a misplaced ordinary
shard. `message/` retains its whole-directory claim.

An unentered nested directory inside an identity parent still blocks. Anchor
scope covers what the listing already named, not structures this accounting never
opened; exempting them would let `session/archive/message_0.db` pass unexamined.
An independent read-only review of the capsule found that over-broad exemption,
and it was narrowed under a fourth RED group (4 failed / 131 passed). A dot
prefix does not exempt a directory either: `session/.archive/` is recorded as an
unentered structure like any other.

The same rule applies to symlinks. A symlink is neither a real directory nor a
plain file, so it aliases content this pass never reads under a name it never
classifies; symlinks are never followed and every one is recorded as
`REJECTED_NOT_REGULAR_FILE` and blocks as `unknown_database`, in the root and in
every direct directory. A dot prefix does not exempt one. What a dot prefix does
exempt is a plain file: only a dot-prefixed **regular file** is sidecar noise and
is ignored. Two further review rounds found three more silent skips on that
path — a dot-prefixed directory at the root, a non-dot symlink to a directory,
and a dot-prefixed symlink to a directory. They are recorded in the gate
document §17.7 as RED-F (6 failed / 144 passed) and RED-G (3 failed / 150
passed).

**Six new root domains.** `sns/`, `favorite/`, `head_image/`, `hardlink/`,
`bizchat/` and `third_app_icon/` are now `known_physical_only`, under a
two-part admission rule: the exact root-relative directory is spelled in at least
two independent public sources, and at least two sources characterise it as
non-message feature data with none describing chat rows. A second pass (A10 gate
document §18) re-fetched every source at its pinned revision, read the pinned
`wechat-cli` reader the first pass had skipped, and corrected three first-pass
statements (GreenBubbles is MIT, not GPL; WeChat Auto Replica does spell
root-relative paths; `favorite/` is not single-source). `chatbot/` stays
`ambiguous` because its sources describe chatbot *messages*, `general/` because
it documents message-event records (admitted by a first draft, withdrawn after
independent review), and `solitaire/` because only one source characterises it.
The container root is unchanged and still ambiguous. `favorite/` rests on a
reading of the semantics, the exemption was first per directory while the evidence is per
file *(narrowed twice in §15; the evidence is per file, so the exemption is now per exact proven store)*, and a change to the live `message_resource.db` completeness claim needs
explicit operator sign-off (A10 §18.9).

**One new message shape.** `message/message_resource.db` is classified `media` —
independently documented as attachment/resource metadata in at least three
sources, and already truthfully inside the existing role vocabulary. The mapping is scoped to
that exact location, because that is what both sources name: elsewhere the same
basename is still an explicit message-shaped candidate and still blocks. No new
role was created.
`weclaw.db` stays `unknown`: its semantics are uncertain, and mapping it to
auxiliary merely because it is not a message store is the inference this policy
refuses.

Acceptance remains a single predicate, `unmet_requirements()`; the new rules were
added inside it rather than beside it. `domain_summary` now also reports
`blocking_unknown/candidate/nested/unreadable` counts per boundary class, read from
the same `_blocking_observations` pass that decides acceptance, so the next real
rerun can tell an observation outside the identity claim from a blocking one
without naming anything. The message-directory inventory and container accounting
now share one definition of the resource-store rule (A10 §18.6).

Synthetic verification: `acquisition/tests` 418 passed, `wechatdb` 514 passed,
`bridge` 272 passed. RED-first groups A–G are recorded in A10 §17.7, and H (2
failed / 32 passed), I (25 failed / 201 passed) and J (16 failed / 226 passed) in
§18.8, then K (2 failed / 232 passed) after a fifth independent review. Five review
rounds found eight over-broad exemptions, all corrected RED-first; each exemption is exactly as wide as the evidence behind it.

**This is policy, not real evidence.** A10 remains **UNMET**, P4-A remains
**9/10**, and P4-B remains **MET 4/4**. A fresh real structural rerun under this
sealed policy is required before any of it is claimed to hold against a real
container, and that rerun needs separate explicit operator authorization.

## 15. Physical-only exemption scoped to proven stores (2026-10-02)

Synthetic and public-policy only; **no real source access**; A10 not run.

Operator decisions:

- **A - APPROVED.** Exact `message/message_resource.db` keeps the `media` role,
  scoped only to the exact `message/` location. This is an intentional live
  coverage-contract change. `message_resource_<n>.db` and the same basename
  elsewhere stay unsupported candidates and block.
- **B - APPROVED.** `favorite/` and `bizchat/` keep their proven domain
  semantics. Domain admission only; no blanket trust of descendants.
- **C - REJECTED.** The per-directory blanket exemption is rejected. Both the
  section 14 directory-wide form and the first section 15 narrowing (any direct
  regular-file unknown) were broader than the evidence. **Accepted:**
  provenance-backed store exemption, keyed on the exact `(domain, basename)`
  pair.

A `known_physical_only` parent therefore grants **no exemption at all**. Domain
knowledge and store knowledge are separate facts: knowing `favorite/` is a
saved-items domain says nothing about a file that appears in it later. Only the
exact proven stores in the A10 section 19.3 ledger - `emoticon.db`, `sns.db`,
`favorite.db`, `favorite_fts.db`, `head_image.db`, `hardlink.db`, `bizchat.db`,
`third_app_icon.db`, each in its own domain - are exempt, and only as direct
regular files. An unproven or future name such as `random_future.db`, and any
look-alike (`favorite2.db`, `Favorite.db`, `favorite_1.db`), keeps `ROLE_UNKNOWN`,
stays visible and blocks. No pattern, prefix, suffix, case-folding or inferred
numeric variant is admitted. Candidates, unentered nested directories,
refused/non-regular entries (symlinks are never followed and never receive a
store exemption) whose condition is unknown or candidate risk, and unreadable
directories block in every domain. A proven store is non-required and can never
satisfy `ordinary_message`, `session_identity` or `contact_identity`. Domain
class and row role stay separate axes.

The change is one exemption predicate inside `_blocking_observations`, so the
single predicate and the `blocking_*` aggregates still come from the same pass.
Full table, proven store ledger, RED-M (9 failed / 310 passed) and verification in
A10 gate document section 19.

A10 remains **UNMET**, P4-A **9/10**, P4-B **MET 4/4**; no historical E-030...E-036
evidence was rescored.
