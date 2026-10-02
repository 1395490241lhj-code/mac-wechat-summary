# DB Reader P4 — complete-container readiness (evidence gate)

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

P4 appears exactly once as a definition, in the approved design:

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

P4 is tracked as **UNMET** in `docs/v2/DB_READER_STANDING_ACQUISITION_DESIGN.md:415`
and `docs/v2/DB_READER_REAL_MULTIPART_GATE.md:203`, as *P4 remains unmet* in
`docs/v2/DB_READER_PRODUCT_PROMOTION_DECISION.md:34`, and in the vault as the
largest untouched named P4 item (`Next Actions.md:24`) — a line that also calls
its evidence gap *the standing blocker* `database-production-path-no-retained-access-material`.
§9 shows that coupling is a vault conflation, not a property of P4.

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

### C. Business messages — UNRESOLVED, deliberately

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
| session identity | yes — P4's position, not a committed fact (`REQUIRED_MESSAGE_ROLES` covers message parts only) | refusal proven | yes | fail-closed on real and synthetic; absent only from the role/gap accounting vocabulary | accounting/reporting parity (Category 2) |
| contact identity | yes — P4's position, not a committed fact (as above) | refusal proven | yes | fail-closed on real and synthetic; absent only from the role/gap accounting vocabulary | accounting/reporting parity (Category 2) |
| WAL coherence | yes | yes | yes | proven for this build: main-only misses 2 rows, lags 62 s | none |
| business message | **UNRESOLVED** | yes (gap raised) | none — never observed in a real container | honest refusal; scope undecided | decision required (§8.3) |
| native FTS | no — optional | yes | not applicable | not a P4 blocker | none |
| media | no — optional | yes (no gap) | not applicable | not a P4 blocker | none |
| unknown / auxiliary | accounted, not supported | yes | n/a | total **inside `message/` only**, not container-wide | container-wide accounting (Category 2) + one real observation (Category 3) |
| schema drift | yes | yes | **none** | synthetic contract only; no real schema ever classified `compatible` | none possible without real evidence |
| future-generation fail closed | yes, as safety | yes | n/a | synthetic safety proven; never compatibility | wording fix (§8.2) |
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

## 8. Reconciliation proposal (proposal only — not enacted)

No `Decisions.md` entry accompanies this document, because no decision has been
approved. If this reconciliation is accepted, seal it as a decision first,
implement second.

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
`docs/v2/DB_READER_STANDING_ACQUISITION_DESIGN.md:415` and
`docs/v2/DB_READER_REAL_MULTIPART_GATE.md:203`, and as "remains unmet" in
`docs/v2/DB_READER_PRODUCT_PROMOTION_DECISION.md:34` — **without** naming the
access-material blocker as its cause.

The vault is where the two get conflated, and it does so in two places.
`Next Actions.md:24` calls the remaining P4 evidence gap "not a design question
but the standing blocker below", while the same note and every
acquisition-related vault record keep P0 as Safe Share and Database as later
under D-036. `Next Actions.md:42` closes the loop from the other direction: it
concludes that deciding how real-schema evidence will ever arrive "is the same
blocker (`database-production-path-no-retained-access-material`) that already
gates the production path". Those two lines are the only place in canonical
memory that couples P4 / real-schema evidence to the access-material blocker.

The question P4 actually asks is: *given a safely prepared source, does the
Reader work correctly and report honestly?* That question is answerable without
a retained production access path — and it has been answered, repeatedly, in the
existing real gates. The blocker concerns **establishing** a prepared source in
production without retaining access material: a P5 / product acquisition problem
under D-002 and D-036.

So: **the standing blocker does not block P4.** It blocks production
reacquisition. The two vault lines that couple them — `Next Actions.md:24` and
`:42` — should be re-read, or P4 stays permanently unfinishable for a reason
that has nothing to do with the reader. Note this does not weaken P4: P4 still
has real open items (§4.B, §4.C, §4.F), it is simply not blocked by *this*.

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
| 6 | §9 said *every* repo/vault document scopes the blocker away from P4, but `Next Actions.md:42` couples real-schema evidence to it as well as `:24` | Important | corrected — §9 now names both vault lines and scopes its claim to repo documents |
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

**P4 definition requires reconciliation.**

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
