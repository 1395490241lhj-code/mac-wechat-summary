# DB Reader P4-A A10 — bounded real container-wide structural classification gate

> **Current verdict: A10 UNMET — P4-A remains 9/10.** The fresh real rerun
> after instrument correction completed accounting, with unchanged pre/post
> source metadata, but the sealed predicate still fails on explicit gaps.
>
> Three separate evidence phases: **§§1–10 first real run (historical UNMET on
> instrument defect); §11 synthetic correction (PASS, no real source); §12 fresh
> real rerun (current UNMET on returned gaps).** P4-B remains MET 4/4.

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

## 5. Acceptance predicate (unchanged, sealed)

A10 PASS iff **all** hold. `unmet_requirements()` returns the fixed tokens of
those that do not; each is a closed-vocabulary item assembled from no name and
no path.

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
identities without the former class/identity collision. Totality is the existing
primitive contract reviewed in §11; this rerun adds no second filename-level
comparison or persisted real-name list. The two nested rejections remain
unexamined, so completed direct accounting does not imply recursive or
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
rerun or production fix.

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
