# DB Reader P4-A A10 — bounded real container-wide structural classification gate

> **Current verdict: A10 UNMET — P4-A remains 9/10.** The fresh real run
> under the reconciled committed `a9b93d9` single predicate completed accounting;
> the method returned FAIL with three fixed blocking tokens. Source pre/post
> unchanged: YES. P4-B remains MET 4/4.
>
> Five distinct evidence phases: §§1–10 first real run (instrument defect,
> UNMET); §11 synthetic instrument correction (PASS); §12 pre-reconciliation
> fresh real run (UNMET); §13 synthetic boundary/gap reconciliation (PASS);
> §14 fresh real run under the reconciled single predicate (current UNMET);
> §15 provenance-based container-domain reconciliation (PASS — policy sealed,
> A10 still UNMET pending a fresh real rerun).

### Historical first real-run overview

> **A10 UNMET.**
>
> The gate **was executed against the operator-designated real container**, and
> it **failed on a defect in the sealed primitive** rather than on the container.
> The defect is recorded below with sanitized aggregate evidence. Per the gate's
> own execution-integrity rule, the code was **not** fixed and the same real
> source was **not** re-read to chase a green result. **P4-A remains 9 MET / 1
> UNMET.**

## 1. Gate identity

| | |
|---|---|
| Gate | A10 — bounded real container-wide structural classification |
| Baseline commit | `853d4813db9e1cf4f1626ed2b6be4e893aa5e0cc` (`v2/rewrite`) |
| Authoritative scope | D-040 P4-A; §13 A10 of `DB_READER_P4_COMPLETE_CONTAINER_READINESS.md` |
| Governing design | §10 of the same document; §15 P4-A of the design spec |
| Operator-designated source class | operator-designated current WeChat `db_storage` root |
| Persistent source enrollment | **NO** — designation was execution-local only |
| Real gate executed | **YES** |
| Classification scope | `read_only_structural_classification` — `lstat` + bounded directory listing, no file opened |
| Content policy | structural/aggregate only; no name, path, identifier, digest or content recorded |

## 2. Operator designation and how it was used

The operator explicitly designated one exact source root. Only that root was
validated, listed and fingerprinted. No `$HOME` scan, no sibling-account
inspection, no mtime or size comparison, no newest/largest selection, no v1
`auto_detect_db_dir()`, no process or log inference, and no walk upward from the
designated root to a parent. Because the designated root validated (exists, is a
directory, is not a symlink), no substitution question arose.

The exact path is deliberately absent from this document, from the Vault and
from every committed file, along with the account-directory identifier. It is
referred to only as the operator-designated current WeChat `db_storage` root.

Nothing was enrolled: `database_source.json` was not written, no SourceLocator
state was created or updated, no Keychain entry was made, no bootstrap ran, no
product preference or Database Mode state changed. The designation confers no
standing access and is not P5.

## 3. Result: the sealed primitive raised on the real container

`acquisition/container_accounting.py` was used exactly as sealed. On the
designated root, `account_container()` raised:

```
ValueError: container accounting invalid
```

The cause is in `ContainerAccounting.__post_init__`, which validates that
`examined_directories` contains **no duplicate entry**, while `account_container()`
appends a **location token** (`other_directory`, `message_directory`, …) once per
directory examined. The vocabulary has one token for the *class* of directory,
so any root holding two or more directories that map to the same token produces a
duplicate and fails the uniqueness check. The real root holds 15 direct
directories — the three named ones and **12 that map to `other_directory`** — so
the check cannot pass. This is a per-directory identity bug in a field that is
documented as a tuple of *locations*, exercised for the first time by a real
root; the synthetic fixtures contain at most one non-named directory, so no
test reached it.

This is a genuine defect in the accounting primitive's traversal/accounting
identity, not a refusal token and not a property of the container. Accounting of
the container therefore did not complete.

### Execution-integrity handling

The gate's rule is explicit: a real-evidence run that exposes a defect in
container accounting, role classification, traversal semantics or required-role
logic **stops**, keeps sanitized aggregate evidence, marks A10 UNMET, and does
not fix production code or re-consume the same real source in the same run. That
is what happened. No corrective code was written, no production file was
modified, and the designated root was not re-read to obtain a passing result.

## 4. Sanitized aggregate evidence

Because the sealed entry point refuses to return, the aggregates below were
obtained by re-running **the same sealed classification functions**
(`classify_database_name()`, the sealed location-to-role anchor rule, the sealed
candidate/regular-file predicates and the sealed fixed-token vocabulary) over
the same listing, in a command-local throwaway harness that was not committed and
introduced **no new role, gap or token vocabulary**. It answers what the sealed
classifier makes of this container; it is not a substitute for a completed
container accounting, and it does not make A10 pass.

| Role | Count | Class | Status |
|---|---|---|---|
| `ordinary_message` | 7 | required | accounted |
| `session_identity` | 1 | required | accounted (container level, anchor rule) |
| `contact_identity` | 1 | required | accounted (container level, anchor rule) |
| `business_message` | 1 | excluded capability | explicit unsupported gap, `business_message_unread`, not required |
| `search_index` | 1 | optional | accounted, no gap raised |
| `media` | 1 | optional | accounted, no gap raised |
| `auxiliary` | 1 | accounting only | counted, never supported |
| `unknown` | 13 | gap only | visible `unknown_database` gap |
| `unsupported_message_candidate` | 1 | gap only | visible `unsupported_message_candidate` gap |

- Total databases accounted: **27** (27 distinct keys, 0 duplicate keys).
- Direct directories in the root: **15**. Rejections: **2** `nested_directory`
  — directories inside the boundary holding a subdirectory, which the sealed
  contract deliberately does not recurse into, so they fail closed.
- Because of the two nested directories, a completed accounting would also have
  raised `directory_unexamined`, which alone fails the predicate at §13 A10.

### Required-role result

All three required roles were observed and classified under the sealed rules:
ordinary message, session identity and contact identity. This is **presence and
accounting only** — required-role *compatibility* continues to rest on the
previously sealed gates cited in section 7 and was not re-tested here.

### Optional / excluded roles

One business message database was observed. Per D-040 it is message-bearing,
unsupported and non-required, and it raises the standing `business_message_unread`
gap; it is not absorbed into ordinary-message coverage and is not a P4-A failure
by itself. This is a statement about this tested container only. Native FTS and
media roles were present and are optional; neither was queried or opened, and
neither weakens nor is required by an ordinary-message claim. One auxiliary
database was counted.

### Unknown / message-bearing gaps

**YES.** 13 databases classified `unknown` (`unknown_database` gap) and one
classified `unsupported_message_candidate` (that gap). Per the gate's own
fail-honest rule these are the output, not silent omissions — and in combination
with the crash and the two unexamined nested directories they mean the predicate
cannot be satisfied on this observation.

## 5. Historical acceptance contract at the first real run

At that run A10 PASS required **all** conditions below. The later synthetic
audit (§13) established that `unmet_requirements()` did not encode the
unknown/candidate blockers: the wrapper added them separately. This historical
table records the run contract; §13 supplies the current single code predicate.

| # | Condition | Encoded as | Met this run |
|---|---|---|---|
| 1 | container source identity explicit / previously selected | caller-supplied root; no selection logic exists | **YES** — operator-designated |
| 2 | session required role accounted | `required_role_missing` / `required_role_unclassified` | observed |
| 3 | contact required role accounted | `required_role_missing` / `required_role_unclassified` | observed |
| 4 | at least one ordinary message role accounted | `required_role_missing` | observed (7) |
| 5 | no required role missing | `required_role_missing` | unevaluable — accounting did not complete |
| 6 | no required role classified incompatible/unknown | `required_role_unclassified` | unevaluable |
| 7 | no unaccounted message-bearing role | `unknown_database`, `unsupported_message_candidate`, `directory_unexamined` | **NO** — 13 unknown, 1 candidate, 2 unexamined nested directories |
| 8 | every optional/excluded role accounted explicitly | role counts; no role silently dropped | observed |
| 9 | business message, if present, stays explicit unsupported | `business_message` role + `business_message_unread` gap | **YES** |
| 10 | no source mutation | read-only by construction | **YES** — pre/post unchanged |
| 11 | evidence output structural/aggregate only | `evidence()` shape | **YES** |
| 12 | no unexamined directory required by container accounting | `directory_unexamined` | **NO** |

**A10 UNMET.** Conditions 7 and 12 fail outright, and 5 and 6 could not be
evaluated because the accounting never returned. Optional and excluded roles
never appear in the predicate.

## 6. Source integrity

**source pre/post unchanged: YES**

A bounded structural fingerprint — existence, type, size, inode, `mtime_ns` —
was taken before and after inspection over every root entry and every child of
every root directory (160 entries), and compared. No hashing of database
contents was performed and none is required by the sealed convention. Nothing was
opened, and nothing was written inside the source root; no SQLite write, WAL
checkpoint, VACUUM, PRAGMA change, journal or schema change, sidecar creation,
rename, chmod, chown or deletion occurred, and no plaintext database copy was
made.

## 7. Existing evidence reused (not re-run)

No previously sealed real correctness gate was re-executed. A10 cites, rather
than re-proves:

- G2 real multi-part message gate — `docs/v2/DB_READER_REAL_MULTIPART_GATE.md`, D-032
- WAL coherence evidence — same gate
- identity catalog real gate and its fixed `identity catalog schema unsupported`
  refusal (`wechatdb/provider/identity_catalog.py`)
- acquisition evidence — `acquisition/`
- schema-drift and typed-query gates — synthetic only
- D-040 reconciliation — `docs/v2/DB_READER_P4_COMPLETE_CONTAINER_READINESS.md` section 13

Required-role **compatibility** is supplied by those existing results. This run
added only the container-wide structural **classification/accounting**
observation, and it did not complete it.

## 8. Privacy and boundary statement

- Only structural metadata was read: directory listings, file types, sizes,
  inodes and mtimes. No database file was opened, so no schema, no row, no
  content was ever available to this gate.
- No message text, chat title, contact name, sender, username/wxid,
  conversation id, table digest, `Msg_<digest>` name, file path, account path,
  key, salt, passphrase, raw SQL error or row content was read, written or
  recorded — including in this document and in the Vault.
- No arbitrary SQL was run and no `sqlite_master` dump was taken.
- No new credential, no passphrase request, no Keychain, no credential
  derivation, no bootstrap, no key recovery, no snapshot, no decryption, no
  plaintext copy.
- No LLDB, no Frida, no process attach, no process memory, no injection, no
  hooks, no re-sign, no clone launch, no production WeChat modification.
- WeChat was not launched, quit, activated, navigated, exported from, shared
  from, or switched; no account state changed.
- `stash@{0}` (`wip capture experiments before system-window architecture`) was
  verified present. **No stash operation was performed during this capsule.**

## 9. Resulting P4 status

| Item | Status |
|---|---|
| P4-A | **9 MET / 1 UNMET** — unchanged; A10 still UNMET |
| P4-B | **MET** — future-generation fail-closed safety, unchanged |

No statement here generalises to future WeChat versions, all WeChat data, all
WeChat databases, business-message support, media, native FTS, or Database Mode
readiness. D-040 is unchanged: the run exposed a defect in an implementation of
D-040, not an error in D-040's definition.

## 10. Next step at the original real run (historical; not taken in that run)

The gate stops. A **separate synthetic corrective capsule** is required to fix
the accounting identity defect and cover a root with multiple directories mapping
to the same location token, plus the nested-directory condition this container
exposed. Only after that fix is sealed on synthetic fixtures should container
accounting be re-attempted, under a fresh designation and a fresh real gate.
P5 / Database-route product-integration planning remains a separate decision
and was not started.


## 11. Synthetic instrument corrective capsule (2026-10-02)

**Instrument defect corrected synthetically; the previous real run remains
A10 UNMET and is not retroactively reinterpreted. P4-A remains 9/10; P4-B
remains MET 4/4.** Baseline: `d78e19d41d2c36da36703cf2b836ef2ecf0f59c4`.
E-030/F-045 remain historical evidence, not fixtures. D-040 is unchanged.

`examined_directories` now holds separate `ExaminedDirectory` records:
operation-local root-relative direct-entry identity and fixed classification.
Uniqueness and ordering apply to identity; multiple identities may share
`other_directory`. Identity is omitted from repr and `evidence()`; the latter's
aggregate-only keys are unchanged. Required-role checks read classification.
No source/product identity, content hash, database read or wider traversal is
introduced. Concrete directory identity also keys databases and rejection rows,
so the same basename in two same-class directories is two entries, while a
repeated concrete entry (including a database/rejection overlap) is invalid.
`accounts_for()` uses these internal `(directory_identity, basename)` keys;
the root identity is the empty string. Role and location tokens remain classes.
The database inventory role rules and the sealed A10 acceptance predicate are
unchanged.

Focused RED, before production edits: **9 failed, 23 passed**. The three-entry
same-class fixture reproduced `ValueError: container accounting invalid`;
separate contract tests failed on missing identity/class fields and acceptance
of a bare class token. Fixtures use neutral synthetic names only. Regression
checks cover zero/one/three same-class directories, named plus other entries,
required roles, root-level databases, duplicate identities (including a changed
class), repeated enumeration, reversed enumeration, nested-directory refusal,
exactly-once database checks, and sanitized repr/evidence/errors.

The new independent read-only review identified five Important surviving
instrument defects: database identity still collapsed by class/basename;
rejection-key collisions/overlap; `.db`-shaped nested directories failing to
record `directory_unexamined`; names exposed by rejection/database repr; and
root-listing errors exposing an absolute path. Corrective RED before these
production edits: **7 failed, 32 passed**. The correction propagates the same
operation-local directory identity into all accounting keys, hides names from
repr, and converts root-listing errors to a fixed exception with suppressed
context. For a `.db`-shaped nested directory, the existing
`not_a_regular_file` reason and name-classification gap are preserved; an
internal structural flag records the existing unexamined-directory condition.
No nested directory is entered and no role, rejection or gap token is added.

Independent re-review: **APPROVED**. All five Important findings were
independently verified closed with in-memory synthetic inputs; no new actionable
regressions. The reviewer confirmed identity/class separation, duplicate and
overlap protection, stable enumeration, unchanged hidden-entry/symlink/depth
bounds, required roles and gaps, sanitized repr/traceback, and unchanged evidence
keys. Suite totals below were run by the parent, not independently re-run by the
reviewer. Both `git diff --check` and `git diff --cached --check` passed.

Final synthetic validation after the corrective pass:

| Suite / check | Result |
|---|---|
| `acquisition/tests/test_container_accounting.py` | 39 passed |
| `acquisition/tests` | 212 passed |
| `wechatdb/tests` | 514 passed |
| `bridge/tests` (synthetic-home isolation) | 270 passed |
| Fixed role/location/gap/rejection constants vs baseline | unchanged |
| `acquisition/database_inventory.py` vs baseline | unchanged |

No real source was accessed, including the previous operator-designated root.
No real A10 rerun, credentials, Keychain, production bootstrap, process attach,
LLDB, Frida, re-signing or production WeChat interaction occurred. Bridge tests
run with `Path.home()` redirected to a disposable synthetic home, including the
two pre-existing path-existence assertions; no real-data tests ran. Memory and
shadow do not consume this primitive, so their suites are not needed here.

A **fresh A10 real structural classification using the already
operator-designated root, under a new explicit authorization**, is still
required. This synthetic correction grants no real-source access and does not
turn any unknown, unsupported-message or nested/unexamined gap into success.


## 12. Fresh real structural classification rerun (2026-10-02)

**Verdict: `A10 UNMET — P4-A remains 9/10`.** This is a new evidence event after the synthetic
instrument correction, not a reinterpretation or replay of the first real run.

### Authorization, execution and integrity

The **exact root was explicitly designated by the operator** for this one
read-only A10 execution: operator-designated current WeChat `db_storage` root.
The actual path and account identifier were held only in command-local memory,
never persisted in evidence. Root validation used `lstat`: present, directory,
not a symlink. No source discovery, sibling-account listing, upward walk,
mtime-based account selection or alternate root.

The unchanged committed `acquisition/container_accounting.py` and
`acquisition/database_inventory.py` at
`906a4d8e21e2764a13dd532cee0e77cab5d06677` were verified against Git before
inspection. **Fresh real gate executed: YES; real `account_container()`
invocations: 1; accounting completed: YES; execution defect observed: NO.**
Every role/classification value below comes from that returned sealed primitive.
The wrapper only validates the designation, fingerprints bounded metadata,
checks returned aggregate accounting uniqueness and emits sanitized evidence;
it supplies no second classifier, role override or corrective retry.

A pre/post guard compared existence, type, size, inode and `mtime_ns` for the
root, its direct entries and the children of visible non-symlink direct
directories. Both passes used the same bounded rules; nested directories were
statted but never entered. The comparison retained entry identities only in
command-local memory, detecting both metadata and bounded-entry-set changes.
No database-content hashing or file opening.

**source pre/post unchanged: YES**

### Fresh aggregate role accounting

| Role | Count | Class | Status |
|---|---:|---|---|
| `ordinary_message` | 7 | required | structurally accounted; prior compatibility evidence reused |
| `session_identity` | 1 | required | structurally accounted; prior compatibility evidence reused |
| `contact_identity` | 1 | required | structurally accounted; prior compatibility evidence reused |
| `business_message` | 1 | excluded/non-required | explicit unsupported `business_message_unread`; not ordinary coverage |
| `search_index` | 1 | optional | structurally accounted; not queried |
| `media` | 1 | optional | structurally accounted; no contents inspected |
| `auxiliary` | 1 | accounting-only | counted; no capability promoted |
| `unknown` | 13 | gap | explicit `unknown_database` |
| `unsupported_message_candidate` | 1 | gap | explicit `unsupported_message_candidate` |

| Total accounting | Fresh result |
|---|---:|
| Database rows / unique database keys | 27 / 27 |
| All emitted entries (database + rejection) / unique accounting keys | 29 / 29 |
| Duplicate accounting entries | 0 |
| Direct directories / examined identities | 15 / 15 |
| Unexamined directory entries | 2 |
| `nested_directory` rejections | 2 |

Returned accounting keys are unique: **PASS**. The sealed primitive completed
its bounded root/direct-directory walk and returned all 15 examined-directory
identities without the former class/identity collision. The wrapper mechanically
compared operation-local accounting keys with the pre-run bounded entry set;
it added no second classifier and persisted no real-name list. The two nested
rejections remain unexamined, so completed direct accounting does not imply recursive or
complete-container coverage.

### Predicate outcome — unchanged

All three required roles were observed and structurally accounted. No
`required_role_missing` or `required_role_unclassified` token was returned.
Required-role compatibility, message parsing, WAL coherence and identity
semantics rely on the previously sealed evidence in §7 and were not re-proved.

The fresh returned gaps are **13 `unknown_database`
database rows**, **1
`unsupported_message_candidate` database row**, and **2
`nested_directory` rejection entries → `directory_unexamined`**. The primitive's
`unmet_requirements()` returns `directory_unexamined`; unknown/candidate gaps
are additionally blockers under §5 conditions 7 and 12 / authoritative §13 A10.
Thus accounting now completes, but the A10 acceptance predicate does not pass.
No gap was suppressed and no directory was manually called harmless.

The **1** business-message row also retains
`business_message_unread`. It stays excluded/non-required under D-040 and is not
the cause of this UNMET verdict. FTS/media remain optional and were not opened.
No required-role schema incompatibility was tested or newly claimed.

These counts were freshly measured. They happen to match the historical
aggregate role/gap counts; that coincidence is not reuse of the old harness,
and the first real run remains UNMET on its own instrument-defect evidence.

### Independent review and tests

A new independent read-only review checked the execution wrapper, aggregate
receipt, sealed source and documentation diff without accessing the source or
re-running the gate. It found no execution defect. One Important documentation
finding was corrected: the readiness document's historical analysis was still
labelled current, and its residual paragraph confused the narrower
message-directory auxiliary classifier with the now-required container identity
roles. The opening scope note and §13 residual explanation now distinguish
those contracts and point to this fresh event. This was reporting-only; no real
rerun or production fix. A subsequent re-review caught a reporting-only denial
of the wrapper's entry-key comparison; that sentence was corrected to state the
actual mechanical audit and its lack of a second classifier or persisted names.
No execution defect or new real invocation resulted from either correction.
Final independent re-review: **APPROVED**; both reporting findings closed,
no remaining actionable reporting issue or execution defect identified. Tests
remain parent-run evidence. The staged reporting correction passed
`git diff --cached --check` before its normal follow-up commit.

| Synthetic test / check | Result |
|---|---|
| `acquisition/tests/test_container_accounting.py` | 39 passed |
| Full `acquisition/tests` | 212 passed |
| Production source/test diff | none |
| `git diff --check` | PASS |

The focused module is the A10 container-accounting contract suite. No unrelated
real-data gate was re-run. `git diff --cached --check` is run after staging and
before the evidence commit.

### Boundaries and next step

**Persistent enrollment: NO.** No `database_source.json`, SourceLocator, app
configuration, product preference, Keychain or Database Mode state was written.
No new credential, passphrase, derivation, bootstrap, decryption, key recovery,
snapshot or plaintext copy. No database, schema, row, FTS or media content was
opened/read. No LLDB, Frida, process attach/memory, injection, hook, re-sign,
clone launch, WeChat launch/quit/activation/navigation/export/share/account
switch or application-state change. No source write, creation, deletion,
rename, permission change, sidecar, checkpoint or migration. No raw source path,
account identifier, filename, directory name, contact/chat identity, content,
digest, key, salt, passphrase or raw exception entered durable evidence.

No production code or tests changed. No post-fix real retry and no investigation
of the unknown filenames or nested contents occurred. **A10 stays UNMET; P4-A
stays 9/10; P4-B stays MET 4/4.** D-040 and the production blocker
`database-production-path-no-retained-access-material` are unchanged.

Next step only: **a separate synthetic corrective/reconciliation capsule using
sanitized aggregate gap categories**. It is not started here; this rerun grants
no standing source access, classifier changes, Reader feature work or P5.


## 13. Synthetic Reader-boundary / gap-semantics reconciliation (2026-10-02)

**Verdict: A10 boundary/gap-semantics reconciliation PASS — A10 itself remains
UNMET pending fresh real rerun. P4-A remains 9/10; P4-B remains MET 4/4.**
D-040 is unchanged. §§1–12 and Vault E-030/F-045, E-031/F-046 remain historical
UNMET evidence; no old real observation is retroactively rescored.

### Boundary recovered from production, not physical layout

The required Reader paths are the ordinary shards directly in `message/`,
`session/session.db`, and `contact/contact.db`. Production provenance:
`bridge/database_bootstrap.py:candidate_source_set` builds precisely those paths;
`acquisition/source_refresher.py:BoundedSourceRefresher` refreshes ordinary direct
message children and never discovers anchors; `bridge/acquired_database_source.py`
feeds the prepared message handles and two identity handles to the provider and
`IdentityCatalog`; `wechatdb/provider/identity_catalog.py` consumes the two
identity schemas. `wechatdb/provider/compatibility.py` requires ordinary message
parts and refuses unsupported required schemas. These are the current
`v2/rewrite` paths; historical v1 `core/wechat_db.py` is name-shape provenance
only, not a v2 access route.

Recognized business messages are excluded/non-required with a standing explicit
gap. Native FTS and media are optional, never ordinary message truth. Recognized
auxiliary stores are accounting-only. Their committed basename vocabulary has
independent provenance in `acquisition/database_inventory.py` and the accepted
`GREENBUBBLES_ASSIMILATION_AUDIT.md` additional-role section; no new basename or
directory mapping is introduced here.

**Physical container is not identical to the Reader boundary. P4-A MET never
means every physical WeChat database is understood.** Recognized nonrequired
stores need no implementation. But absence from the current routing is not
proof that an arbitrary parent domain cannot contain required/message-bearing
truth. No committed architecture or accepted audit establishes an entire
unnamed directory as physical-only. Consequently no directory-level exemption
is added: root/other-domain unknowns remain **ambiguous**, not proven
message-bearing and not proven irrelevant. A genuinely proven physical-only
unknown or nested domain could be visible/nonblocking under D-040, but that
category has **no established member in this capsule**. We do not manufacture a
passing fixture for an unproven exclusion.

An ordinary shard shape outside `message/` is location-incompatible with current
routing. Container accounting reports it as `unsupported_message_candidate`;
it cannot satisfy the required ordinary role. This is container policy only:
the context-free message-directory classifier remains unchanged. The exported
`ContainerDatabase` constructor also rejects an ordinary role outside
`message_directory`, so direct construction or `dataclasses.replace` cannot
bypass this location invariant.

### Visible observations versus blockers

| Condition | Visible? | Blocks structural acceptance? | Reason |
|---|---|---|---|
| Unknown in Reader message domain | yes, `unknown_database` | yes | unclassified message truth risk |
| Unknown in root/other domain | yes, `unknown_database` | yes | exclusion is unestablished; ambiguity stays fail-closed |
| Unknown in independently proven physical-only domain | would remain visible | would not by presence alone | no such domain is currently established or exempted |
| Unsupported message candidate, any current location | yes, `unsupported_message_candidate` | yes | no location proves exclusion; ordinary-shaped misplaced stores also belong here |
| Business message | yes, `business_message_unread` | no by itself | D-040 explicit unsupported excluded capability |
| FTS / media | yes, role counts or rejection | no by themselves | optional, no contribution to ordinary truth |
| Recognized auxiliary DB | yes, role count or rejection | no by itself | accounting-only; no new whitelist |
| Required role absent/misplaced | yes, required-role tokens | yes | current access path has no required input |
| In-boundary unexamined directory | yes, rejection + `directory_unexamined` | yes | required/message truth could be hidden |
| Other-domain nested directory | yes, rejection + `directory_unexamined` | yes | parent exclusion is unestablished |
| Independently proven physical-only nested domain | would remain visible | would not by presence alone | no such parent-domain exemption currently exists |

Rejected unknown/candidate DB-shaped entries retain the same risk blockers.
A `.db`-shaped directory additionally retains `directory_unexamined`. Recognized
optional DB-shaped directories also block on their unexamined contents: an
optional basename cannot prove a directory contains only optional data.
Business exclusion from A10 does not change the production per-read inventory
coverage ceiling (`_accounted`); visible unread message evidence still prevents
an overclaim about all messages.

### One machine truth

`ContainerAccounting.meets_requirements()` delegates exclusively to
`unmet_requirements(accounting) == ()`. `evidence()["unmet_requirements"]` renders
that same function. It owns missing/unclassified required roles, unexamined
risk, unknown risk and unsupported candidates. Conditions are sorted fixed
tokens; business stays visible in `.gaps` but is not a blocking requirement.
There is no wrapper-level post-processing of `.gaps` in the current contract.
A future gate must call this predicate, not replay the obsolete historical
wrapper's extra blocker logic. Caller designation, source pre/post integrity
and structural-only reporting remain execution prerequisites, rather than a
second interpretation of role gaps. Required schema compatibility continues to
rest on A2–A6; encrypted structural classification cannot prove a schema.

Traversal remains one root listing plus one listing of each visible non-symlink
direct directory. No recursion, discovery, source selection or content opening
was added. All unknown observations remain visible; no data outside required
routing can strengthen required message evidence.

### Synthetic verification

Before production edits: **20 failed / 40 passed**. Exact RED cases:

- `test_an_unknown_root_database_is_a_visible_gap_and_blocker`
- `test_the_evidence_summary_carries_no_name_or_path`
- `test_multiple_other_directories_preserve_required_roles_and_database_accounting`
- `test_ambiguous_database_blocks_in_every_unexcluded_location`: unknown and
  candidate × root/message/session/contact/neutral extra domain (10 cases)
- `test_ordinary_shape_outside_message_domain_cannot_supply_required_truth`:
  root/session/contact/neutral extra domain (4 cases)
- `test_refused_database_shape_keeps_its_acceptance_blocker`: unknown,
  unsupported candidate and refused ordinary shape (3 cases)

Initial independent review found one Important API bypass: enumeration enforced
location semantics, but a caller could reconstruct a misplaced row as ordinary
through the exported dataclass and obtain false PASS. Corrective RED before
production edits: **4 failed / 60 passed** —
`test_exported_database_api_cannot_relabel_misplaced_shard_as_required` at
root/session/contact/neutral extra domain. The existing constructor now rejects
that inconsistent ordinary-role/location pair for both direct construction and
`dataclasses.replace`. No new API or traversal was added.

Final synthetic validation: focused container **64 passed**, full acquisition
**237 passed**, wechatdb **514 passed**, bridge **270 passed**. Bridge used a
fresh disposable synthetic `Path.home()` for all tests; acquisition Keychain
checks use injected FakeSecurity. No real-data tests ran. Memory/shadow have no
callers of this primitive and were not run. `database_inventory.py` and provider,
bootstrap, identity, Reader queries and app/MCP product code remain unchanged.
Final independent read-only re-review: **APPROVED**, no remaining Critical or
Important findings. The reviewer independently verified the API bypass closure;
full-suite totals above are parent-run evidence. `git diff --check` and the
populated staged `git diff --cached --check` passed.
**Real source access: NO.** The previous operator-designated root was not read,
resolved or inspected. No real rerun, real names, credentials, Keychain,
bootstrap, process access, WeChat interaction, P5 or product work.

Next step only: **fresh A10 structural rerun under the reconciled single
acceptance predicate**, under a separate explicit real-source authorization.


## 14. Fresh real structural rerun under the reconciled single predicate (2026-10-02)

**Verdict: A10 UNMET — P4-A remains 9/10. P4-B remains MET 4/4.**
This is the fifth independent evidence event. Historical §§1–13 and Vault
E-030/F-045, E-031/F-046, E-032/F-047 retain their original scope and results.

### Designation, sealed implementation and one execution

**Exact root explicitly designated by operator.** The one-run, exact-root-only,
read-only authorization was used for the operator-designated current WeChat
`db_storage` root. Root validation used `lstat` and required a non-symlink
directory. The source path/account identifier existed only in execution-local
memory; no discovery, substitution, sibling/upward walk or alternate account.
**Persistent enrollment: NO.** No SourceLocator record, app configuration,
Keychain item, bootstrap or Database Mode activation.

Before any source inspection, local/tracking/live remote all matched
`a9b93d9dacdef69f58736d58cc6c2fd9e6f0061f`, ahead/behind 0/0, clean tree.
The loaded container-accounting and generic inventory source bytes were checked
against that committed implementation. **Fresh reconciled run: YES;
`account_container()` invocations: exactly 1; accounting completed: YES.**
An exclusive start marker prevents accidental repetition of the execution.
No production code or test changed in this real-run capsule.

The structural result comes exclusively from
`accounting.meets_requirements()`: **FALSE / FAIL**. Component evidence from
`unmet_requirements(accounting)` and `evidence()["unmet_requirements"]` agrees:

```
directory_unexamined
unknown_database
unsupported_message_candidate
```

**One authoritative predicate; no wrapper override.** The wrapper renders these
results and performs mechanical key/directory-set and source-integrity audits;
it does not combine `.gaps` with unmet conditions to invent another verdict.
No required-role missing/unclassified condition was returned. Required schema,
WAL, parsing and identity compatibility remain scoped to the existing sealed
evidence in §7; this structural run did not open a database or re-prove them.

### Fresh aggregate accounting

| Role | Count | Class | Status |
|---|---:|---|---|
| ordinary_message | 7 | required | structurally accounted in the defined message domain |
| session_identity | 1 | required | sealed location/anchor semantics; structurally accounted |
| contact_identity | 1 | required | sealed location/anchor semantics; structurally accounted |
| business_message | 1 | excluded/non-required | explicit unsupported `business_message_unread`; not a blocker by itself |
| search_index | 1 | optional | accounted; no search or content access |
| media | 1 | optional | accounted; no content access |
| auxiliary | 1 | accounting-only | nonrequired; adds no message truth |
| unknown | 13 | ambiguous/blocking | visible `unknown_database`; no exemption |
| unsupported_message_candidate | 1 | message-bearing risk/blocking | visible fixed candidate gap; no promotion or whitelist |

| Total accounting | Fresh result |
|---|---:|
| DB rows / unique DB keys | 27 / 27 |
| DB + rejection entries / unique accounting keys | 29 / 29 |
| Duplicate entries | 0 |
| Direct directories / examined directories | 15 / 15 |
| Rejections | 2 |
| Nested/unexamined entries | 2 |

The returned keys and direct-directory identities mechanically match the
pre-run bounded entry set. Keys/names remain operation-local and are not
persisted. This audit adds no classifier or PASS/FAIL gap rule. The **13 unknown,
1 candidate and 2 nested/unexamined** counts are freshly measured; they
independently match the earlier aggregate counts. They were not copied from a
historical receipt. No unknown name or nested content was investigated. No
ordinary-shaped store outside the message domain was manually promoted.

### Integrity, privacy and stop boundary

**Source pre/post unchanged: YES.** The guard compared existence/entry-set,
entry type, size, inode and `mtime_ns` for the root, its direct entries and
children of visible non-symlink direct directories. Both passes use the same
bounded traversal. Nested directory entries are statted, never entered. No
content hashes, database opening, SQL/schema/rows, credential/decryption or
snapshot activity. Only aggregate counts, fixed tokens and booleans survive.

No real source path, account directory, real filename/directory name,
message/contact identity, username/wxid, table name/digest, content, key, salt,
passphrase or raw exception is persisted by this capsule. No source mutation,
sidecar/temp file, permission change, checkpoint or migration. No credential,
Keychain, bootstrap, decryption, plaintext copy, encrypted snapshot, LLDB,
Frida, attach/process memory, injection/hooks, re-sign, clone launch or WeChat
launch/quit/activation/navigation/export/share/account change.

The real-evidence phase stopped after the failed predicate and post-integrity
guard. No production fix, mapping change, deeper traversal, second invocation,
P5 or Database Mode work followed. D-040 is unchanged.

### Review and synthetic tests

Focused container-accounting suite, including acceptance/boundary regressions:
**64 passed**. Full acquisition suite: **237 passed**. All tests synthetic; the
Keychain adapter checks use injected FakeSecurity. No unrelated real-data gate
was run. `git diff --check` and populated staged `git diff --cached --check`
passed before the evidence commit.

Independent read-only review: **APPROVED**, no execution defect or Important
reporting defect. The reviewer checked the wrapper, sanitized receipt, sealed
source and both reports, and used in-memory synthetic checks to verify the guard
can detect bounded entry-set/type/size/inode/mtime changes. Source unchanged YES
is explicitly limited to that metadata guard. No source re-access, wrapper
execution, real rerun or edits occurred during review. Suite totals above are
parent-run evidence; the reviewer did not repeat those suites.

Next step only: **a provenance-based synthetic reconciliation of the remaining
sanitized blocker categories, without re-accessing the real source**. Not started
here. The production acquisition blocker remains unchanged and orthogonal.

## 15. Provenance-based container-domain reconciliation (2026-10-02)

**Verdict: PASS — the domain policy is sealed. A10 itself remains UNMET pending
a fresh real rerun. P4-A remains 9/10; P4-B remains MET 4/4.** This is a
strictly synthetic / provenance-only phase. **Real source access: NO** — the
previous operator-designated root was not read, listed, resolved or statted.
D-040 is unchanged. §§1–14 and Vault E-030/F-045 … E-033/F-048 retain their
original scope and results; no old real observation is retroactively rescored.

### 15.1 The question this phase answers

The contract established by §13 correctly refused to equate *physical
`db_storage`* with *required Reader boundary*. It also established **no**
independently proven physical-only domain, so every unnamed parent domain stayed
ambiguous and blocking. This phase asks whether independent provenance — not a
fresh look at the real container — can establish any domain as required,
optional/excluded, known physical-container-only, or ambiguous.

The purpose was never to explain the count 13. The 13 unknown, 1 candidate and
2 nested figures stay aggregated historical facts; this phase does not recover,
inspect or guess any identity behind them.

### 15.2 Provenance hierarchy

Evidence priority, strongest first:

| Tier | Source | What it can establish | What it cannot |
|---|---|---|---|
| 1 | current production architecture (bootstrap, SourceSet, source refresher, provider, identity catalog, Reader operations) | a path/domain the current v2 Reader consumes is inside the Reader boundary | — |
| 2 | existing project code/history (committed v1 `core/` reader, sealed design/gate documents, committed compatibility research) | a domain exists in WeChat layout, and its broad feature class | that the domain is *required* by the current Reader |
| 3 | already-approved public-source behavioural evidence (GreenBubbles audit, wx-cli audit, their pinned upstream revisions) | behavioural and layout facts, at the recorded revision | source code, SQL, query shapes, fixtures, private identifier schemes |

No new upstream repository was consulted. Tier 3 was audited only as already
recorded; no revision was re-fetched. **No real-derived whitelist exists**: not
one entry below came from a screenshot, a previous real run, or a desire to make
A10 pass.

### 15.3 Provenance ledger

| Domain / shape | Boundary class | Provenance | Evidence strength | Reader claim | Allowed effect |
|---|---|---|---|---|---|
| `message/` | `required_message` | Tier 1: bootstrap/source-refresh/provider routing | production architecture | ordinary message truth | unknown/candidate block; shard satisfies the ordinary role |
| `session/` | `required_identity` | Tier 1: `session.db` opened as the identity anchor | production architecture | session identity truth | anchor satisfies the role; identity cannot move |
| `contact/` | `required_identity` | Tier 1: `contact.db` opened as the identity anchor | production architecture | contact identity truth | anchor satisfies the role; identity cannot move |
| `emoticon/` | `known_physical_only` | Tier 2: committed `core/wechat_db.py` reads `os.path.join("emoticon", "emoticon.db")` from a directory its own docstring documents as the WeChat `db_storage` root, for an md5 → CDN sticker mapping | project-owned historical reader | none in v2 | existence accounted; contents add no coverage and support no role |
| `hardlink_*.db`, `chatbot.db`, `sns.db` | **no domain entry** | Tier 3 recognises these as database *basenames*; no accepted evidence establishes a parent directory | insufficient for layout | none | stay database-role knowledge; never a directory-domain entry |
| every other domain, and the container root | `ambiguous` | none | insufficient | unknown | visible and fail-closed |

`emoticon` is the **only** domain with defensible root-layout provenance, and it
is the only member of the `known_physical_only` class. `db_dir` is documented as
the WeChat `db_storage` root, so `emoticon/emoticon.db` is a first path component
relative to the container root — a *root-domain* fact, not a basename fact. Its
purpose (a sticker md5 → CDN lookup table) is not message or identity truth, and
no v2 production path references it. Both required conditions of §8 are
therefore met, and only for this one domain.

### 15.4 Role axis and domain axis are independent

A *domain* is a root-relative directory; a *role* is what a database basename is
understood to be. Neither collapses into the other:

- A recognized auxiliary **basename** (`sns.db`) proves nothing about its parent
  directory. It stays `role = auxiliary` inside an `ambiguous` domain.
- A **physical-only parent** does not relabel its children. An unknown database
  inside `emoticon/` stays `role = unknown`; a message-shaped one stays
  `role = unsupported_message_candidate`. Neither becomes auxiliary, and neither
  becomes support.
- `ROLE_UNKNOWN` is never hidden or converted into a fake auxiliary role to get
  green. `accounting.gaps` still reports it; only *acceptance* is boundary-aware.

The honest representation of a proven physical-only unknown is
`role = unknown` + `domain_class = known_physical_only`, and that is what the
code produces.

### 15.5 Acceptance semantics

One pass over every row that could block, so acceptance is still decided in
exactly one place:

| Domain class | Role / shape | Visible? | Blocking? | Can satisfy required truth? |
|---|---|---|---|---|
| required_message | unknown database | yes, `unknown_database` | yes | no |
| required_message | unsupported message candidate | yes, fixed candidate gap | yes | no |
| required_message | ordinary shard | yes, role count | no | yes |
| required_identity | own anchor (`session.db` / `contact.db`) | yes, role count | no | yes, its own role only |
| required_identity | misplaced ordinary shard | yes, candidate gap | yes | no |
| known_physical_only | unknown database | yes, role count + gap | no, for Reader completeness only | no |
| known_physical_only | message-shaped candidate | yes, role count + gap | no, for Reader completeness only | no — stays unsupported |
| known_physical_only | nested directory | yes, rejection | no — no recursion is implied | no |
| known_physical_only | **unreadable** directory | yes, rejection | **yes** | no |
| known_physical_only | identity role row | impossible — constructor rejects it | — | no |
| ambiguous | unknown database | yes | yes | no |
| ambiguous | message-shaped candidate | yes | yes | no |
| ambiguous | nested directory | yes, `directory_unexamined` | yes | no |
| any | business message | yes, `business_message_unread` | no by itself (D-040) | no |
| any | FTS / media / auxiliary | yes, role counts | no by themselves | no |
| any | refused `.db` name | yes, rejection + role-derived gap | yes when unknown/candidate | no |

Two refusals keep blocking unchanged: an unreadable directory fails closed
**everywhere**, because a directory that could not be listed is not the same
claim as one that was listed and held nothing needed; and a refused `.db`-shaped
entry keeps its role-derived blocker when its parent domain is required or
ambiguous.

`NESTED_DIRECTORY` was **not** removed globally. Its blocking effect is refined
by parent boundary class only. No recursion is implied anywhere: traversal is
still one root listing plus one listing of each visible non-symlink direct
directory.

### 15.6 No spoofing, no substring inference, no widening

`domain_boundary_class()` is exact-set membership over a closed policy. There is
deliberately no inference helper — no prefix, substring, case-folding or fuzzy
match — because a look-alike name is not the proven domain. `emoticon2`,
`emoticon_`, `emoticons`, `Emoticon`, `xemoticon` and `emot` all stay ambiguous and
blocking. Unknown inputs default to ambiguous; an invalid location token raises.

### 15.7 One acceptance truth

`ContainerAccounting.meets_requirements()` remains `unmet_requirements(self) == ()`,
and `evidence()["unmet_requirements"]` renders that same function. There is no
second predicate, no wrapper override, and no combining `.gaps` with unmet
conditions to invent a verdict. The boundary-class exemption lives inside that
one predicate, so it cannot be bypassed by calling the wrong function.

One further containment: `_REQUIREMENT_ROLE_GAPS` is `_ROLE_GAPS` intersected
with `CONTAINER_REQUIREMENTS`, so a classified refusal can only ever add a
condition from the closed requirement vocabulary. Independent review found that
without it the rejection branch could emit `business_message_unread` — visible in
`.gaps`, correctly excluded from required truth, but wrongly placed in
`unmet_requirements` and therefore able to fail a container that D-040 says it
does not block. Corrective RED-first, then fixed; see §15.9.

### 15.8 Aggregate evidence shape for the next real gate

`evidence()["domain_summary"]` reports counts **by boundary class only** — no
name, no path, no directory identity, no filename:

```
boundary class        database_count  unknown_count  candidate_count
                      nested_unexamined_count  unreadable_count  role_counts
required_message
required_identity
known_physical_only
ambiguous
```

This is what lets the next real rerun answer *how many remaining blockers are
actually inside Reader-relevant domains?* without publishing a single private
layout name. `unreadable_count` is kept separate from
`nested_unexamined_count` because an unlisted directory and an unentered one are
different claims, and only the first still blocks inside a physical-only domain.
The optional/excluded axis stays on `role_counts`, so business/FTS/media remain
visible where they already were.

### 15.9 Synthetic verification

RED before the first production edit: **20 failed / 75 passed** — missing domain
policy symbols; required/ambiguous/physical-only behaviour; physical-only nested
semantics; no-basename-inference; no-domain-spoofing; aggregate evidence shape.

A second RED appeared during implementation, from the §14 review: the exported
`ContainerDatabase` constructor allowed a caller to build an identity row
outside its anchored domain. **6 failed.** The constructor now rejects a
misplaced identity role as well as a misplaced ordinary role, so no construction
path can satisfy a required identity from the ambiguous root or from a proven
physical-only domain.

The independent read-only review after GREEN found two further defects, both
fixed RED-first:

1. **Important — closed-vocabulary escape.** A refused business-shaped name
   (a symlink is never opened) flowed through the new rejection branch and could
   appear in `unmet_requirements()`, even though `business_message_unread` is
   deliberately *not* in `CONTAINER_REQUIREMENTS` and D-040 keeps business
   message non-blocking. Corrective RED: **1 failed / 102 passed**
   (`test_a_refused_name_can_never_add_a_condition_outside_the_closed_vocabulary`).
   Fixed by intersecting with the closed requirement vocabulary.
2. **Minor — merged evidence counters.** The new aggregate counted an unreadable
   directory as `nested_unexamined_count`, which would have reported an
   always-blocking condition inside a non-blocking physical-only bucket. Split
   into `unreadable_count`, asserted by
   `test_an_unreadable_physical_only_directory_still_fails_closed`.

Final synthetic validation: focused container accounting **103 passed**, full
acquisition **276 passed**, wechatdb **514 passed**, bridge **270 passed**. All
tests synthetic. Bridge used its disposable synthetic `HOME` fixture; acquisition
Keychain checks use injected FakeSecurity. No real-data test ran. Memory/shadow
have no caller of this primitive and were not run. `database_inventory.py`,
parser, provider query semantics, crypto, credential path, bootstrap acquisition,
source discovery, UI, MCP, Agents and Memory are unchanged. `git diff --check`
and the populated staged `git diff --cached --check` passed.

The review also confirmed, against the diff: no domain entry derived from
real-source knowledge; `emoticon` provenance valid; basename evidence never
promoted into layout evidence; no physical-only row can satisfy a required
role; no message-bearing candidate escapes from a required or ambiguous domain;
unknown roles stay visible; nested logic relaxed only for the one proven domain;
traversal width unchanged; no fuzzy/prefix matching; physical-only rows cannot
strengthen coverage; still exactly one acceptance predicate; D-040, P4-B and
business-message semantics unchanged.

### 15.10 What this phase did not do

No real source access, no fresh A10 rerun, no recursion, no new traversal, no
Database Mode, no P5, no product wiring, no decision change. The 13 / 1 / 2
aggregate figures remain unreconciled until an authorized fresh real rerun reads
the sealed policy against the real container.

Next step only: **one fresh A10 structural rerun using the sealed
provenance-backed domain policy, reporting aggregate counts by boundary class**.
Not executed here.
