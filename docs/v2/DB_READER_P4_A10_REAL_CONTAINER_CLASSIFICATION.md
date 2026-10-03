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
category has **no established member in this capsule**. *(Superseded by §19: a
proven domain exempts nothing by itself; only an independently proven exact store
is exempt, and never nested structure.)* We do not manufacture a
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
| Independently proven physical-only nested domain | would remain visible | would not by presence alone *(hypothetical here; §19: nested blocks in every domain)* | no such parent-domain exemption currently exists |

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
| known_physical_only | message-shaped candidate | yes, role count + gap | ~~no~~ **yes (§19)** | no — stays unsupported |
| known_physical_only | nested directory | yes, rejection | ~~no~~ **yes (§19)** | no |
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
ambiguous *(superseded by §19: also inside a proven physical-only domain)*.

`NESTED_DIRECTORY` was **not** removed globally. Its blocking effect was first
refined by parent boundary class *(superseded by §19: it now blocks in every
domain, including a proven physical-only one)*. No recursion is implied anywhere: traversal is
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
different claims, and only the first still blocked inside a physical-only domain
*(superseded by §19: both now block everywhere)*.
The optional/excluded axis stays on `role_counts`, so business/FTS/media remain
visible where they already were.

### 15.9 Synthetic verification

RED before the first production edit: **20 failed / 75 passed** — missing domain
policy symbols; required/ambiguous/physical-only behaviour; physical-only nested
semantics *(historical; §19 reversed the nested exemption)*; no-basename-inference; no-domain-spoofing; aggregate evidence shape.

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
unknown roles stay visible; nested logic relaxed only for the one proven domain *(historical; reversed by §19)*;
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
## 16. Fresh real structural rerun under the sealed provenance-backed domain policy (2026-10-02)

**Verdict: UNMET — P4-A remains 9/10; P4-B remains MET 4/4.** This is the
seventh evidence phase and the first real structural rerun executed against the
provenance-backed domain policy sealed in §15. The structural result comes
exclusively from `accounting.meets_requirements()`: **FALSE / FAIL**. No
production code, classifier, policy or test changed in this capsule, and no
retry followed the failed predicate. D-040 is unchanged. §§1–15 retain their
original scope and results; nothing earlier is retroactively rescored.

### 16.1 Designation, sealed implementation and one execution

**Exact root explicitly designated by operator.** The one-run, exact-root-only,
read-only authorization was used for the operator-designated current WeChat
`db_storage` root. Root validation used `lstat` and required a present,
non-symlink directory. The source path and account identifier existed only in
command-local execution memory: no discovery, sibling-account listing, upward
walk, mtime-based account selection, process-open-file inspection, alternate
root or substitution. **Persistent enrollment: NO** — no SourceLocator record,
no application configuration, no Keychain item, no bootstrap, no Database Mode,
no standing source access, no P5.

Before any source inspection, local HEAD, `origin/v2/rewrite` and live
`refs/heads/v2/rewrite` all matched
`265ed38426b734c9e7317dcbdaee7cc845f8eeef`, ahead/behind `0/0`, clean tree, and
the pre-existing `stash@{0}` was present. The loaded
`acquisition/container_accounting.py` and `acquisition/database_inventory.py`
source bytes were the committed files at that revision; nothing was patched in
memory. **Fresh real gate executed: YES; `account_container()` invocations:
exactly 1; accounting completed: YES; execution defect observed: NO.** An
exclusive start marker refuses a second invocation of the real root in the same
execution environment.

The harness validated the designation, fingerprinted bounded metadata, called
the sealed primitive once, compared the same bounded set afterwards, and
verified that `evidence()["unmet_requirements"]` renders exactly what
`unmet_requirements(accounting)` returns. It supplies no second classifier, no
role override, no boundary-class rule of its own, no corrective retry, and no
prose verdict. It emitted fixed tokens, closed-vocabulary role names and
integer counts only.

### 16.2 Authoritative acceptance result

One predicate, one truth:

```
directory_unexamined
unknown_database
unsupported_message_candidate
```

`meets_requirements()` returned `FALSE`; `unmet_requirements(accounting)` and
`evidence()["unmet_requirements"]` returned the same three tokens, and the
harness would have aborted with *acceptance truth divergent* had they disagreed.
No `required_role_missing` or `required_role_unclassified` condition was
returned: all three required roles are present and correctly anchored. Required
schema, WAL, parsing and identity compatibility remain scoped to the existing
sealed evidence in §7; this structural run opened no database and re-proved none
of them.

### 16.3 Fresh aggregate role accounting

| Role | Count | Class | Status |
|---|---:|---|---|
| `ordinary_message` | 7 | required | structurally accounted in the defined message domain |
| `session_identity` | 1 | required | sealed location/anchor semantics; structurally accounted |
| `contact_identity` | 1 | required | sealed location/anchor semantics; structurally accounted |
| `business_message` | 1 | excluded/non-required | explicit unsupported `business_message_unread`; non-blocking by itself (D-040) |
| `search_index` | 1 | optional | accounted; no search or content access |
| `media` | 1 | optional | accounted; no content access |
| `auxiliary` | 1 | accounting-only | nonrequired; adds no message truth |
| `unknown` | 13 | visible gap; blocking only in required/ambiguous domains | `unknown_database` reported; role never hidden or relabelled |
| `unsupported_message_candidate` | 1 | message-bearing risk/blocking | fixed candidate gap; no promotion or whitelist |

| Total accounting | Fresh result |
|---|---:|
| DB rows / unique DB keys | 27 / 27 |
| DB + rejection entries / unique accounting keys | 29 / 29 |
| Duplicate accounting keys | 0 |
| Direct directories / examined directories | 15 / 15 |
| Rejections | 2 |
| Nested/unexamined entries | 2 |
| Visible gaps | `business_message_unread`, `unknown_database`, `unsupported_message_candidate` |

Accounting keys and directory identities were compared mechanically against the
pre-run bounded entry set and matched; they remain operation-local and are not
persisted. This audit adds no classifier and no gap rule.

### 16.4 Aggregate boundary-class summary

This is the reporting axis §15.8 specified, rendered from
`evidence()["domain_summary"]`. No directory, file, identity or path appears.

| Boundary class | DB count | Unknown count | Candidate count | Nested/unexamined count | Unreadable count |
|---|---:|---:|---:|---:|---:|
| `required_message` | 12 | 1 | 1 | 0 | 0 |
| `required_identity` | 3 | 1 | 0 | 1 | 0 |
| `known_physical_only` | 1 | 1 | 0 | 0 | 0 |
| `ambiguous` | 11 | 10 | 0 | 1 | 0 |

The columns reconcile exactly against §16.3: 27 databases, 13 unknown,
1 candidate, 2 nested/unexamined, 0 unreadable.

**Effect of the sealed physical-only class, in aggregate only.** Exactly **one**
unknown database observation fell inside `known_physical_only`, and it is
non-blocking for Reader completeness while remaining fully visible as
`unknown_database` with `role = unknown`. **Twelve** unknown observations
remained in `required_message`, `required_identity` and `ambiguous` domains and
therefore remained blocking. Both nested/unexamined entries fell outside
`known_physical_only` — one under a required-identity domain, one under an
ambiguous domain — so both remained blocking; no unreadable directory was
observed. Which physical directory contributed which count is deliberately not
recoverable from this report, and no domain membership was added to make the
verdict greener.

The role axis was not collapsed into the domain axis: the physical-only unknown
is reported as `role = unknown` + `domain_class = known_physical_only`, and
`accounting.gaps` still contains `unknown_database` for it. Only *acceptance*
is boundary-aware, exactly as §15.4 sealed.

### 16.5 Independent agreement with history, and privacy

The aggregate totals — 27 DB rows, 13 unknown, 1 candidate, 2 nested/unexamined,
15/15 direct directories — **independently match** the earlier §14 and §12
real-rerun aggregates. They were derived from this run's own execution, not
copied from a historical receipt; no previous result, harness output or table
was reused as input. What is new in this phase is the **split by boundary
class**, which did not exist before the provenance reconciliation.

No residual blocker was investigated. No unknown filename, directory name,
message/contact identity, username/wxid, chat title, conversation id, table
name or digest, content, key, salt or passphrase was printed, inspected,
persisted or searched for, and no nested directory was entered. No public or
upstream repository was searched using private names. The blockers are recorded
as aggregate boundary-class counts and fixed tokens only.

### 16.6 Integrity, boundaries and stop

**Source pre/post unchanged: YES.** The guard compared existence, entry set,
entry type, size, inode and `mtime_ns` for the root, its direct entries and the
children of visible non-symlink direct directories, using the same bounded
traversal on both passes; nested directory entries were statted, never entered.
No full-container hashing, no database opening, no SQL, schema or row access.
This claim is explicitly limited to that bounded metadata guard.

Strictly read-only throughout: no write, create, delete, rename, chmod/chown,
SQLite write, checkpoint, VACUUM, schema change, sidecar, temp file inside the
source or plaintext copy. No credential request, Keychain, bootstrap, key
derivation, old-key recovery, decryption, plaintext copy or snapshot. No LLDB,
Frida, attach, process memory, injection, hooks, re-signing or clone launch. No
WeChat launch, quit, activation, navigation, account switch, export or share.
The only v2 permission model implied by this phase is Screen Recording; none
was exercised.

The phase stopped after the failed predicate and the post-execution integrity
guard. No production fix, policy change, domain addition, whitelist, deeper
traversal, second invocation, P5 or Database Mode work followed. **A10 remains
UNMET and P4-A remains 9/10.**

### 16.7 Independent review and tests

Independent read-only review, performed by a separate reviewer with no access to
the real source, of the harness, the sealed implementation and this record:
**APPROVED — no Critical and no Important execution defect.** The reviewer
confirmed, from committed code, that `meets_requirements()` is a one-line
delegation to `unmet_requirements(self) == ()` and that no second or wrapper
verdict exists anywhere in the repository; that the exported constructor rejects
a misplaced ordinary *or* identity role, so required truth is unreachable from
the ambiguous root or a physical-only domain by any construction path; that the
physical-only set is exact-set membership with no `re`, `fnmatch`,
case-folding or distance primitive anywhere in either module; that unreadable
directories are tested *before* the physical-only exemption and therefore block
everywhere; that business/FTS/media raise no requirement condition while staying
visible; that the harness assigns the machine verdict once with no override and
prints only fixed tokens and counts; that traversal is exactly one root listing
plus one listing per direct directory with no `glob`/`walk`/`resolve`/open/
sqlite primitive; and that `domain_summary` is keyed only by the four fixed
boundary tokens. The reviewer also independently re-derived every claimed count
from the sealed code's own routing constraints and reproduced all of them,
including the single most informative confirmation: the exempted row is one
whose role stayed `unknown`, which is precisely the honest representation §15.4
requires. No real source was re-accessed during the review.

Two minor, reporting-only observations were recorded and do not affect the
verdict: the harness returns a zero exit status even when the predicate is false
(the verdict is carried in the printed JSON and was read from there), and it
binds the two modules by working-tree path rather than re-verifying their bytes
against the sealed commit in-process — byte-identity with `265ed384` was
confirmed separately, by the reviewer and before execution. Neither creates a
verdict path, and the single-invocation marker remains consumed, so no second
real access is possible without deliberately deleting it.

Synthetic tests, with no production code change: focused container-accounting
and domain-policy suite **103 passed**; full acquisition suite **276 passed**;
`git diff --check` and the populated staged `git diff --cached --check` passed.
All tests synthetic; the Keychain adapter checks use injected FakeSecurity. No
unrelated real-data gate ran.

### 16.8 What this phase did not do

No production-code change, no classifier or policy change, no whitelist, no
domain addition, no recursion, no traversal widening, no Database Mode, no P5,
no credential/decryption work, no product wiring, no decision change. The
13 / 1 / 2 aggregate figures are now split by boundary class, and the residual
blockers are located **in aggregate**: the required-message and required-identity
domains are not themselves the problem — the ambiguous class and the two
unentered nested structures are.

Next step only: **provenance-only / synthetic reconciliation of the remaining
ambiguous blocker classes**, with no additional real-source access until a new
independent domain policy is sealed. Not started here, and not automatically
authorized.

---

## 17. Residual blocker reconciliation — identity anchor scope and public layout provenance (2026-10-02)

### 17.1 Designation and evidence class

This phase is **strictly synthetic and public-provenance-only**. No real source
was accessed: the WeChat container was not listed, stat'ed, searched, hashed or
opened, the previously operator-designated root was not touched, and no name,
directory or count from the §16 real run was used to derive any rule here. The
13 / 1 / 2 aggregate figures of §16 are historical input only. Nothing in this
phase was written to make them green.

Two independent questions were answered. **Workstream A:** is required identity
truth the whole `session/`/`contact/` directory, or only the exact anchors?
**Workstream B:** do additional root domains and `message/` shapes have enough
*independent public* provenance to be pre-classified?

### 17.2 Identity anchor scope (Workstream A)

Recovered from committed production routing, not from physical layout:

- `bridge/acquired_database_source.py` passes explicitly prepared handles under
  `"session.db"` and `"contact.db"`.
- `acquisition/source_refresher.py` refreshes only message shards and states that
  identity anchors are never discovered or changed.
- `wechatdb/provider/identity_catalog.py` `IdentityCatalog.build()` consumes
  explicitly supplied `ShardEntry` objects for the two roles and infers nothing
  from siblings.
- Repository search finds no production enumeration of `session/`/`contact/`
  siblings.

So production requires the **parent location** and the **exact anchor**. It
never claims that every other database in that directory is identity truth.
D-040 requires the two identity *roles*, and its operative wording keeps a
message-bearing unsupported or unknown structure an explicit gap; it does not
claim the entire directory. The two claims are now separated:

| Observation | Visible | Blocking | Can satisfy required identity |
|---|---|---|---|
| exact valid anchor | yes | no | its own role only |
| missing anchor | yes | **yes** (`required_role_missing`, `required_role_unclassified`) | no |
| refused anchor (symlink, directory, non-regular) | yes | **yes** | no |
| unreadable parent | yes | **yes** (`directory_unexamined`, `required_role_missing`) | no |
| unknown regular-file sibling beside a proven anchor | yes | no — outside the anchor claim | no |
| refused unknown sibling (symlink) beside a proven anchor | yes | **yes** (`unknown_database`) | no |
| message-bearing candidate sibling | yes | **yes** (`unsupported_message_candidate`) | no |
| nested sibling inside an identity parent | yes | **yes** (`directory_unexamined`) | no |
| misplaced ordinary shard (`session/message_0.db`) | yes | **yes** (candidate) | no |
| misplaced identity basename (`session/contact.db`) | yes | no | no — auxiliary, never identity |

`message/` keeps its whole-directory claim: an unknown beside the shards is
still a completeness blocker. Ambiguous and root-level behaviour is unchanged.

Anchor scope reaches only what the listing already named. A *directory* beside
the anchor is different from a regular-file sibling: nothing inside it was
entered, so this accounting cannot show that it holds no message-bearing risk,
and exempting it would let `session/archive/message_0.db` pass unexamined. Nested
structures inside identity parents therefore stay `directory_unexamined`
blockers, exactly as in every other non-exempt domain — including a dot-prefixed
directory such as `session/.archive/`, which is recorded like any other. This
narrowing came out of an independent read-only review of this capsule (see
§17.10).

### 17.3 Root-domain provenance ledger (Workstream B)

Tier 1–3 were re-read from committed material. Tier 4 added two independent
public repositories, consulted only for layout and behavioural facts — no code,
SQL, fixture, credential method or identifier-derivation algorithm was copied.

| Source | Revision | License |
|---|---|---|
| `https://github.com/bojieli/greenbubbles` | `69f19c7089d7ef011e9ba43d44c6fb4e6e5be98b` | MIT (repo `LICENSE`; an earlier draft of this row said GPL-3.0, which was wrong and is corrected in §18.1) |
| `https://github.com/raclen/wechat-suite` | `68235fb4308d71b1462e78e533850162849562a1` | MIT |
| `https://github.com/fanyuantaier/wechatauto-replica` | `204f1296e5cae158b0fbc1a3f97819fa1b77d487` | Apache-2.0 |

Corroboration actually established:

| Domain | Exact root layout proven? | Purpose | Source(s) | Confidence | Boundary class |
|---|---|---|---|---|---|
| `emoticon/` | yes | sticker md5 to CDN mapping | Tier 2 committed `core/wechat_db.py` | high | `known_physical_only` (unchanged) |
| `sns/` | yes | Moments feature data, not chat rows | GreenBubbles `docs/WECHAT_DATABASE_FORMAT.md` (`sns/sns.db` in the observed `db_storage` tree) + raclen `wechat-decrypt/export_sns.py` (`os.path.join(DECRYPTED_DIR, "sns", "sns.db")`) | high | `known_physical_only` (**new**) |
| `bizchat/`, `chatbot/`, `favorite/`, `general/`, `hardlink/`, `head_image/` | yes, but **single-source** | feature data (business chat, chatbot, favourites, system events, attachment link indexes, avatars) | GreenBubbles only | **one source — insufficient** | `ambiguous` (unchanged, fail-closed) |
| `message/`, `session/`, `contact/` | yes | required roles | Tier 1 production routing | high | `required_message` / `required_identity` |
| container root | no | — | — | — | `ambiguous` (unchanged) |

The corroboration bar was deliberately not lowered. **This table is the first
pass and is partly superseded by §18**: it consulted Tier 4 incompletely (the
pinned Tier 3B reader and two further public layout listings were not read), so
its "single-source" row and its note that Replica names `sns.db` by basename only
were wrong. §18 holds the corrected ledger; the six rows above are re-decided
there under an explicit two-part admission rule.

### 17.4 Message-directory provenance ledger (Workstream B)

| Shape | Role | Purpose | Public provenance | Reader effect |
|---|---|---|---|---|
| `message_<n>.db` | `ordinary_message` | chat shards | Tier 2 committed reader + GreenBubbles + raclen | the only message truth |
| `biz_message_<n>.db` | `business_message` | business-account/chat shards | Tier 2 + GreenBubbles | visible gap, excluded by D-040 |
| `message_fts.db` | `search_index` | full-text index | Tier 2 + GreenBubbles | optional, no coverage |
| `media.db`, `media_<n>.db` | `media` | voice/attachment metadata | Tier 2 + GreenBubbles + raclen + Replica | optional, no coverage |
| `message/message_resource.db` | `media` | rows connecting a message to media metadata, ids, hashes, packed info | GreenBubbles database reference + raclen `decode_image.py` / `export_messages.py` reading the same `packed_info` blob | optional, no coverage (**new**) |
| `weclaw.db` | `unknown` | "WeChat internal state"; no usable content tables on the inspected account | GreenBubbles only, semantics uncertain | stays a visible blocker |

No new role was created. `message_resource.db` joins the existing `media` role
because that vocabulary already truthfully covers attachment/resource metadata,
and it must be matched before the message-like pattern or it would be misread as
an unread message-shaped candidate. The mapping is **location-scoped to
`message/`**, because that is the exact location the sources name: it is applied
in the container layer's routing, not in the location-free basename classifier,
so the same basename at the container root or in an identity parent is still an
explicit message-shaped candidate and still blocks. `weclaw.db` was deliberately
**not** mapped: calling it auxiliary merely because it is not a message store is
exactly the inference this policy refuses. `message_resource_<n>.db` stays a
candidate — the proven shape carries no index suffix, and an indexed variant is
unproven either way.

### 17.5 Remaining ambiguity — still fail-closed

`required_message` unknowns and candidates, every ambiguous domain (including
the container root and every domain §18 leaves out), every message-shaped name
outside `message/`, and every nested structure remain blocking. **(§19 narrowed
this:** a physical-only domain exempts only an independently proven exact store
(§19.3), not an unknown name, a candidate or nested structure.) Physical-only domains never relabel; their
contents stay visible, keep their honest role, cannot satisfy any required role and cannot strengthen coverage. Exact-match only: no
prefix, suffix, substring, case-folding or fuzzy matching. Each exemption is
exactly as wide as its evidence: each proven domain by exact directory name (§18.4), the resource
store by exact `message/` location, and anchor scope by exact parent location.

### 17.6 One acceptance truth

Unchanged: `ContainerAccounting.meets_requirements()` delegates to
`unmet_requirements()`, and `evidence()["unmet_requirements"]` renders the same
tuple. The anchor-scope rule was added **inside** that single predicate, as two
conditions on rows that were already being evaluated — there is no wrapper, no
second verdict and no second policy engine. `domain_summary` needed no schema
change; the existing boundary-class aggregates already report these rows.

### 17.7 RED-first evidence

- **RED-A (identity scope)** — 4 failed / 118 passed. The failures were exactly
  the two anchor-scope groups: unknown sibling beside a proven anchor, and nested
  sibling inside an identity parent.
- **RED-B (root-domain provenance)** — 4 failed / 125 passed: the `sns`
  physical-only set assertion and the three `sns` non-blocking cases.
- **RED-C (message shape)** — 2 failed / 125 passed: `message_resource.db` role
  and its no-coverage effect.

Two pre-existing tests encoded the old whole-directory identity claim and were
corrected rather than worked around; their identity-parent coverage is now
explicit in the anchor-scope group.

- **RED-D (review correction)** — 4 failed / 131 passed, after review found the
  nested-directory exemption too broad. The failures were the nested-sibling
  group, parametrized over both identity parents and both a benign and a
  message-bearing fixture. A refused (symlink) unknown sibling was pinned at
  the same time and was already green.
- **RED-E (second review correction)** — 6 failed / 137 passed, after the
  re-review found two exemptions wider than their evidence: the resource-store
  mapping applied by basename outside `message/`, and a dot-prefixed directory
  bypassed the nested-directory rejection entirely. The failures were three
  location cases for the resource shape and three domains for the hidden
  directory. The hidden-anchor case was pinned at the same time and was already
  green.
- **RED-F (third review correction)** — 6 failed / 144 passed, after the
  review found two more silent skips: a dot-prefixed *directory at the root*
  (`_is_directory` matched it, but nothing in the root loop treated it as a
  domain, so it was never entered and never recorded), and a non-dot symlink
  pointing at a directory, which is neither a real directory nor a plain file
  and therefore fell through both branches. Three root domains and three
  locations were pinned; the hidden-anchor case was re-pinned and was green.
- **RED-G (fourth review correction)** — 3 failed / 150 passed, after the
  re-review found the same fall-through reached back into dot-prefixed names:
  `session/.link -> ../message` and `root/.link` were skipped by the hidden-name
  exemption, because that exemption tested the *name* rather than the *type*.
  The exemption now tests only `S_ISREG`, so hiding a symlink buys it nothing
  while `.DS_Store` and other hidden regular files stay ignored as sidecar
  noise. Three locations were pinned, and the existing hidden-sidecar test
  (root and `session/`) was the counter-check that still passes.

### 17.8 Synthetic verification

Focused container accounting **153 passed**; full acquisition **326 passed**;
wechatdb **514 passed**; bridge **270 passed**. `git diff --check` and the
populated `git diff --cached --check` passed. All tests synthetic.

### 17.9 What this phase did not do

No real rerun, no source access of any kind, no credential/keychain/decryption/
attach/re-sign work, no Database Mode, no P5, no UI/MCP/Swift/crypto/parser/query
change, no source-selector or standing-enrollment change, no recursion, no
traversal widening, and **no change to D-040**. Real rerun evidence is required
before any of this is claimed to hold against a real container.

### 17.10 Independent review of this capsule

A separate read-only reviewer worked only from committed code and synthetic
fixtures in `/tmp`. It confirmed the anchor-scope premise (production exact-opens
both anchors and enumerates no identity sibling), confirmed every failure mode
still fails closed, found no provenance finding and no acceptance-policy
finding. Its first Important finding was the nested-directory exemption described
in §17.2, corrected under RED-D; its re-review confirmed that fix and no other
path became permissive. That re-review raised two further Minors, both real and
both fail-open, both corrected under RED-E: the resource-store mapping applied by
basename outside `message/` (now location-scoped), and a dot-prefixed directory
bypassing the nested-directory rejection (now recorded like any other
directory). It also corrected an imprecise note: a dot-prefixed `.session.db` is
not proven as the anchor, but it *is* reported missing — `required_role_missing`
and `required_role_unclassified` both fire from the directory being examined,
independently of whether a candidate was named. Two earlier Minor notes were
adopted as documentation or test hygiene: the sibling row is qualified as a
*regular-file* sibling, and the duplicated physical-only set literal was
dropped. `message_resource_<n>.db` remains a candidate, because the proven shape
carries no index suffix.

Round 3 produced two more Important findings, both corrected under RED-F: a
dot-prefixed directory at the root was skipped entirely (root domains are now
accounted like any other directory), and a non-dot symlink to a directory was
silently ignored (it now produces `REJECTED_NOT_REGULAR_FILE` and blocks as
`unknown_database`). Symlinks are never followed. Round 4 produced one more
Important finding on the same code path, corrected under RED-G: the hidden-name
exemption was a name test where it had to be a type test, so a dot-prefixed
symlink to a directory still passed. The precise invariant now sealed is: only a
dot-prefixed **plain file** is ignored; any non-regular entry is recorded
regardless of its name, in the root and in every direct directory.

---

## 18. Second public-provenance pass — corrected census and aggregate blocking evidence (2026-10-02)

This section supersedes §17.3's single-source conclusion and extends §17.4. It is
**still strictly synthetic and public-provenance-only: no real source was
accessed**, no name, directory or count from §16 was used, and every candidate
below came from public documentation and public code. The search was seeded only
by public WeChat database-layout material, never by any local layout.

### 18.1 Why a second pass, and what it corrected

The first pass did not read the pinned Tier 3B reader (`wechat-cli`) and stopped
at three repositories. A bounded public search then found further independent
layout listings. Re-fetching every cited source at its pinned revision also
corrected three statements in §17:

- **GreenBubbles is MIT-licensed**, not GPL-3.0 (its `LICENSE` at the pinned
  revision begins "MIT License"; the project's own
  `GREENBUBBLES_ASSIMILATION_AUDIT.md` already said MIT).
- **WeChat Auto Replica does spell root-relative paths** — its Chinese README's
  `db_storage` tree lists `sns\sns.db`, `message\message_resource.db`,
  `message\media_0.db`, `session\session.db` and `contact\contact.db`. §17.3 said
  it named `sns.db` by basename only, which was incorrect.
- **`favorite/` was not single-source**: the pinned `wechat-cli` reader opens
  `os.path.join("favorite", "favorite.db")` relative to the `db_storage`
  directory.

### 18.2 Sources (behavioural and layout facts only)

No implementation, SQL, fixture, credential method or identifier derivation was
copied; nothing is imported. All are public GitHub repositories, fetched at the
exact commit shown.

| ID | Source | Revision | Commit date | License | Evidence used |
|---|---|---|---|---|---|
| S1 | `https://github.com/bojieli/greenbubbles` | `69f19c7089d7ef011e9ba43d44c6fb4e6e5be98b` | 2026-10-01 | MIT | `docs/WECHAT_DATABASE_FORMAT.md`: `db_storage` tree and per-database purpose table (macOS-oriented) |
| S2 | `https://github.com/raclen/wechat-suite` | `68235fb4308d71b1462e78e533850162849562a1` | 2026-07-11 | MIT | `wechat-decrypt/`: `export_sns.py` (`sns`/`sns.db`), `emoticons.py` (`emoticon`/`emoticon.db`), `decode_image.py` and `export_messages.py` (`message/message_resource.db`), `session`/`contact` anchors |
| S3 | `https://github.com/fanyuantaier/wechatauto-replica` | `204f1296e5cae158b0fbc1a3f97819fa1b77d487` | 2026-10-01 | Apache-2.0 | `README.zh-CN.md` §二 `db_storage` tree (Windows 4.x) |
| S4 | `https://github.com/huohuoer/wechat-cli` (pinned in `WECHAT_CLI_BACKEND_AUDIT.md`) | `a3789232d4f79bf0b30634d9dadbce71e4acd601` | 2026-04-06 | Apache-2.0 | `commands/favorites.py` (`favorite`/`favorite.db`, `fav_db_item`), `session`/`contact` anchors; `DBCache` keys relative to `db_dir`, documented in `core/messages.py` as the `db_storage` directory |
| S5 | `https://github.com/CatchLee/wechat_wish_agent` | `069fdd9448695ed604a789a9921b85614cea2b10` | 2026-02-23 | **no license file** — facts only, nothing reusable | `src/core/weixin_db_arch.md`: the author's own `db_storage` listing and notes |
| S6 | `https://github.com/Glory0707/wx-qq-decrypt` | `f9b3c7f9e69205d45d6ea8bdabe4444fd1f9c4a5` | 2026-09-07 | MIT | `docs/database_inventory.md`: WeChat 4.1.13.12 on Windows 11, 27 databases, "directory structure consistent with the client's original layout" |

These are different authors and repositories whose documents each describe their
own inspection; that is the independence this ledger relies on. It is weaker than
it looks: independent review found a shared lineage — S4's README says it is built
on `ylytdeng/wechat-decrypt`, S2's `wechat-decrypt/` has the same file set, S6
vendors a copy of the same lineage, and S3 and S5 cite shared community material.
Admission still holds because every admitted domain has an independent pair (S1 is
not part of that lineage, and S3 and S5 are separate), but it is not a claim of
fully independent observation, and it is not a claim about any real container.
Layout agrees across a macOS-oriented source (S1) and Windows 4.x sources (S3,
S6), which is corroboration of the `db_storage` layout rather than a platform
claim.

### 18.3 Admission rule (both parts required)

1. **Layout** — the exact root-relative directory `<domain>/<db>` is spelled in at
   least two independent sources. A basename alone never counts.
2. **Semantics** — at least two sources *describe what the rows are* (metadata,
   index, resource, saved items, icons, avatars) as non-message feature data. A
   bare feature name is not a description. No source may describe chat or
   message rows, or message-event records (recalled-message content, per-message
   system events), in the domain.

A domain failing either part stays `ambiguous` and fail-closed.

### 18.4 Root-domain ledger

| Domain | Layout sources | Semantics (sources) | Decision |
|---|---|---|---|
| `emoticon/` | Tier 2 `core/wechat_db.py`; S1, S2 (`emoticons.py`), S5, S6 | stickers: md5 to CDN mapping (Tier 2, S2), sticker packages (S1) | `known_physical_only` (unchanged) |
| `sns/` | S1, S2, S3, S5, S6 | Moments timeline (S1, S3, S6) | `known_physical_only` |
| `favorite/` | S1, S4, S5, S6 | saved items, "not chat history" (S1); S4 lists saved items with their sender and source chat; 收藏 (S6); S5 guesses saved messages. A saved copy, not the chat store | `known_physical_only` (**new**, judgment call §18.9) |
| `head_image/` | S1, S5, S6 | avatar blobs keyed by username and MD5 (S1, S5); 头像 (S6) | `known_physical_only` (**new**) |
| `hardlink/` | S1, S5, S6 | MD5-to-file attachment index (S1); 文件硬链接索引, "file hardlink index" (S6). S5 admits it does not understand it. S6 describes the rows, so it counts | `known_physical_only` (**new**) |
| `bizchat/` | S1, S5, S6 | business-chat group and user metadata (S1); 企业微信的基本信息 (S5); S6 gives only a label, so S1 and S5 carry the semantics | `known_physical_only` (**new**, judgment call §18.9) |
| `third_app_icon/` | S1 (table; not in its tree), S6 | third-party app icon images (S1); 第三方应用图标, "third-party app icons" (S6) | `known_physical_only` (**new**) |
| `general/` | S1, S5, S6 | S5 and S6 call it miscellaneous settings, but S1 documents message-event records in it: a recall table with a message `content` column, red-envelope and transfer tables keyed by a message id, friend-request content (found by independent review) | `ambiguous` — fails part 2 (first admitted, then **withdrawn** before commit) |
| `chatbot/` | S1, S6 | S1: "chatbot sessions and chatbot messages" — **message-bearing** | `ambiguous` — fails part 2 |
| `solitaire/` | S1, S6 | only S1 characterises the contents (接龙 content, folds, validity); S6 gives a feature name only | `ambiguous` — fails part 2 |
| `message/`, `session/`, `contact/` | Tier 1; S1–S6 | required roles | required classes (unchanged) |
| container root | no | — | `ambiguous` (unchanged) |

**Superseded by §19.** This paragraph first said a physical-only domain's unknown
or candidate databases and unentered nested directories stop being
Reader-completeness blockers. That was too broad: only a direct regular-file
unknown is exempt; candidates, nested directories and refused/non-regular entries
still block. Exact match only.

### 18.5 Message-directory provenance ledger (additions)

| Shape | Role | Public provenance | Reader effect |
|---|---|---|---|
| `message/message_resource.db` | `media` | S1 (tree + table), S2 (`cache.get("message/message_resource.db")`, `os.path.join(..., "message", "message_resource.db")`), S3 (`message\message_resource.db`), S6 | optional, no coverage — four sources, location `message/` only |
| `message/media_<n>.db` | `media` | S1, S3 (`message\media_0.db`), S5, S6 | unchanged |
| `message/biz_message_<n>.db` | `business_message` | S1, S5, S6 | unchanged: visible gap, excluded by D-040 |
| `message/message_fts.db` | `search_index` | S1, S5, S6 | unchanged |
| `message/weclaw.db` | `unknown` | S1: "internal state, no usable content tables"; S6: "resource/auxiliary" — the two characterisations differ | stays a visible blocker; no role is justified |
| `message/*.kvdb` | none | S5 | not a `.db` candidate, already outside the inventory |
| `contact/contact_fts.db` | none needed | S1, S5, S6: contact search index | beside a proven anchor it is visible, unsupported and non-blocking by anchor scope (§17.2); it can never satisfy a role |
| `favorite/favorite_fts.db` | none needed | S1, S5, S6 | covered by the `favorite/` domain |

No new role was created and `_MESSAGE_LIKE` was not loosened.
`message_resource_<n>.db` stays a candidate: no source documents an indexed
variant.

### 18.6 One definition of the resource store

The message-directory inventory (`inventory_message_directory`, consumed by the
refresher and by `acquired_database_source` for the Reader's compatibility
gaps) still classified `message/message_resource.db` as an unsupported message
candidate, while container accounting called it `media`. One physical file had
two answers. `classify_message_directory_name` in `database_inventory.py` is now
the single definition of the exact-name rule and both layers use it; the
location-free `classify_database_name` is unchanged, so the same basename
anywhere else is still a candidate. A refused (symlink) `message_resource.db` in
`message/` carries no message claim in the inventory, like any refused media
name; container accounting still records it as a refusal.

### 18.7 Aggregate blocking evidence

Under anchor scope `required_identity.unknown_count` can no longer say whether an
unknown observation blocked. `domain_summary` now carries four further counts per
boundary class — `blocking_unknown_count`, `blocking_candidate_count`,
`blocking_nested_count`, `blocking_unreadable_count` — and they are **read from
the same pass that decides acceptance**: acceptance logic moved into one
`_blocking_observations` generator that both `unmet_requirements` and
`domain_summary` consume. There is still one predicate,
`ContainerAccounting.meets_requirements()` delegating to `unmet_requirements()`;
the report cannot disagree with it because it is not a second implementation.
A parametrized test pins that the summary's blocking totals imply exactly the
row-level tokens of `unmet_requirements`. Two reading notes from review:
`blocking_unknown_count` can exceed `unknown_count`, because the plain count covers
database rows only while the blocking count also includes refused (for example
symlinked) unknowns; and zero blocking counts do not imply the predicate is met,
because the required-role tokens (`required_role_missing`,
`required_role_unclassified`) are not row observations.

### 18.8 RED-first evidence for this pass

- **RED-H (inventory consistency)** — 2 failed / 32 passed
  (`test_database_inventory.py`): the message-directory inventory classified the
  resource store as a candidate, and a refused one left a candidate gap. The five
  pinning tests around it were green.
- **RED-I (root-domain provenance)** — 25 failed / 201 passed: the ledger set
  assertion, and each of the seven domains then admitted (`general` was withdrawn later, RED-K) for unknown, candidate and
  ordinary-shaped names plus a nested directory. One earlier test used
  `hardlink/` as an arbitrary directory name and was moved to a neutral name; the
  old "single-source stays ambiguous" test, which encoded the superseded
  conclusion, was replaced by tests for the domains that still fail the rule.
- **RED-J (blocking aggregates)** — 16 failed / 226 passed: the new keys and the
  agreement property.

- **RED-K (review correction)** — 2 failed / 232 passed, after independent review
  found that `general/` documents message-event records: the set assertion and
  the still-ambiguous case. `general` was withdrawn from the proven set.
- Two bridge tests pin the live effect of §18.6 (a complete read stays complete
  beside `message_resource.db`; an indexed variant still caps it). They are
  characterisation tests of an already-implemented inventory change, covered
  RED-first at the inventory level by RED-H.

Final synthetic counts: `acquisition/tests` 418 passed, `wechatdb` 514 passed,
`bridge` 272 passed. `git diff --check` and the populated staged check passed.
No real source, credential, Keychain, decryption, process, WeChat or Database
Mode action was taken.

### 18.9 Judgment calls and accepted risks for the operator

- **Withdrawn: `general/`.** It was admitted by the first draft of this pass and
  withdrawn after independent review showed that S1 documents message-event
  records in it. It stays ambiguous.
- **`favorite/`** is admitted as a saved copy of items (some saved from chats,
  with sender and source chat), not the chat store; S1 says "not chat history".
  A reasonable reader could keep it ambiguous; revert by deleting its name from
  `_PHYSICAL_ONLY_DOMAIN_NAMES`.
- **`bizchat/`** is business-chat group/user metadata; business messages live in
  `biz_message_<n>.db`, handled separately and unchanged.
- **Evidence is per file, so the exemption is per store (§19).**
  The sources describe specific databases (`favorite/favorite.db`, ...). S1 itself
  warns WeChat may add a numeric suffix, create a new feature database or move a
  feature into another store. The first version of this pass exempted everything
  below a proven directory; the controller rejected that as too broad and §19
  first narrowed it to a direct regular-file unknown, which was still too broad.
  §19.1 Decision C now limits the exemption to the exact proven stores in the
  §19.3 ledger. An unrecognised, non-message-shaped regular file in a proven
  domain blocks again. The residual accepted risk is a proven store that WeChat
  later changes the meaning of, which only a fresh real rerun can detect.
- **Anchor scope relies on a narrow name regex.** An unknown regular-file sibling
  beside a proven anchor is non-blocking, while a name matching the message-like
  pattern (`message*.db`, `biz_message*.db`) still blocks as a candidate. A
  message-bearing store with another name (`msg_0.db`, `chat_0.db`, a different
  case) beside an anchor stays visible as `unknown` but passes. Production never
  reads it, so this cannot cause a misread; it does mean the claim is "no known
  message-shaped structure escapes", not "no message-bearing structure escapes".
- **Hidden directories now block.** A hidden directory at the root (a stray
  `.Trash`-like entry) is accounted as an ambiguous domain and blocks, where
  before it was silently skipped. That is a liveness cost, not a safety one;
  hidden regular files (`.DS_Store`) stay ignored.
- **Case-insensitive filesystems.** `Session.db` is listed under its stored name
  and is reported missing, while production might open it. That fails closed.
- **Live Reader completeness changes (needs explicit operator sign-off).** The
  message-directory inventory now treats `message/message_resource.db` as media.
  `bridge/acquired_database_source.py::_accounted` capped every complete read to
  `partial_inventory` when the inventory held an unsupported-candidate gap, so a
  `message/` containing that file no longer carries that cap. The file is
  attachment metadata in four public sources, but this is a change to a live
  coverage claim, not only to A10 accounting.

### 18.10 Verification performed

This pass re-fetched every cited path at its pinned revision through the public
GitHub API, and re-read the production routing it depends on
(`bridge/acquired_database_source.py`, `acquisition/source_refresher.py`,
`wechatdb/provider/identity_catalog.py`). A separate read-only reviewer then ran
about fifty hand-built synthetic cases and 400 random fixtures with no real-source
access, re-fetched all six sources, and re-checked the acceptance refactor:
**APPROVED WITH MINORS**, no Critical finding, no fail-open path. Its three
Important findings (the `general/` message-event records, the per-file versus
per-directory evidence scope, and the live-completeness change) and its minors are
recorded in §18.9 or corrected in §18.3–18.7; `general/` was withdrawn under RED-K.
The aggregate-versus-verdict check found 0 mismatches over the random sweep.
The reviewer could not run the bridge suite from its working directory; it was run
here (272 passed).

### 18.11 Status

A10 remains **UNMET**, P4-A **9/10**, P4-B **MET 4/4**. This is policy and
public provenance, not real evidence: a fresh structural rerun under this sealed
policy, with separate explicit operator authorization, is the only way to learn
whether it holds against a real container.

---

## 19. Physical-only exemption scoped to proven stores (2026-10-02)

Still synthetic and public-policy only: **no real source was accessed**, no real
name or count shaped any rule, and A10 was not run. Sections 19.1-19.6 below
supersede the earlier controller-review draft of this section, which had accepted
a blanket per-directory exemption; that draft's wording is retained verbatim at
the end as historical record.

### 19.1 Operator decisions

**Decision A - APPROVED.** Exact `message/message_resource.db` keeps the `media`
role, scoped only to the exact `message/` location. Multiple independent public
sources document the exact path, and its semantics are attachment/resource
metadata rather than ordinary chat history. This is an **intentional live
coverage-contract change**. `message_resource_<n>.db` stays an unsupported
candidate and blocks; the same basename anywhere other than `message/` stays an
unsupported candidate.

**Decision B - APPROVED.** `favorite/` (saved/favourite feature domain) and
`bizchat/` (business-chat group/user metadata domain) keep their independently
proven domain semantics - neither is ordinary chat-history truth. This approves
the *domain* admission only; it authorises no blanket trust of every descendant.

**Decision C - REJECTED as too broad.** The per-directory blanket exemption
("once a root directory is `known_physical_only`, every direct regular-file
unknown inside it is automatically non-blocking") is rejected. Public evidence is
primarily about *specific stores and file shapes*, not about every file a domain
may ever contain. **Accepted replacement:** provenance-backed store exemption - a
store is exempt only when its exact `(domain, basename)` pair is independently
proven. Unknown future/unproven names remain visible and fail-closed.

The two concepts are now separate and are never collapsed:

- **Domain knowledge** - `favorite` is a known non-required feature domain.
- **Store knowledge** - `favorite/favorite.db` is a provenance-backed store.

Knowing the domain must never imply that all future files in it are safe.

### 19.2 The invariant

A `known_physical_only` parent grants **no exemption of its own**. The exemption
is keyed on `(domain, exact basename)` membership in `PROVEN_STORE_NAMES`, and
only for a direct *regular-file* `ROLE_UNKNOWN` row. Domain class and row role
stay separate axes; the exemption is a verdict decision and never relabels a row.

| Observation inside a proven physical-only domain | Visible | Blocking | Token |
|---|---|---|---|
| proven exact store (`favorite/favorite.db`) | yes | **no** | - |
| unproven / future regular-file unknown (`random_future.db`) | yes, `ROLE_UNKNOWN` | **yes** | `unknown_database` |
| look-alike of a proven store (`favorite2.db`, `Favorite.db`, `favorite_1.db`) | yes, `ROLE_UNKNOWN` | **yes** | `unknown_database` |
| direct regular-file message-shaped candidate (`message_future.db`, `message_0.db`, `biz_message_x.db`) | yes | **yes** | `unsupported_message_candidate` |
| nested directory (including hidden) | yes | **yes** | `directory_unexamined` |
| symlink, neutral name | yes | **yes** | `unknown_database` |
| symlink, message-shaped name | yes | **yes** | `unsupported_message_candidate` |
| symlink to a directory | yes | **yes** | `unknown_database` |
| symlink with a recognised non-message name (`media.db`) | yes | **yes** in a proven domain (unproven store); no elsewhere | `unknown_database` |
| unproven basename the generic ledger recognises elsewhere (`sns.db`, `chatbot.db`, `media.db`, `message_fts.db`, `session.db`) inside a proven domain | yes, `ROLE_UNKNOWN` | **yes** | `unknown_database` |
| non-`.db` regular file (`favorite.db.bak`, `favorite.db.wal`) inside a proven domain | yes, `ROLE_UNKNOWN` | **yes** | `unknown_database` |
| proven-store sidecar (`favorite.db-wal`, `favorite.db-shm`, `favorite.db-journal`) inside a proven domain | yes, companion row | **no** (see 20) | - |
| unreadable directory | yes | **yes** (unchanged) | `directory_unexamined` |

Symlinks are never followed and never receive a store exemption - a store
exemption names a regular file. No recursion is authorised. A proven store is
non-required and can never satisfy `ordinary_message`, `session_identity` or
`contact_identity`; a `favorite.db` or `head_image.db` row keeps its truthfully
unknown or auxiliary role rather than being faked into a passing role.
Identity-anchor scope (17.2) is unchanged and follows the same shape: only a
regular-file unknown beside a proven anchor is exempt.

### 19.3 Proven store ledger

Every entry is spelled root-relative by at least two independent public sources
(section 17.3 and 18 revisions). No pattern, prefix, suffix, substring,
case-folding or inferred numeric variant is admitted, because no source proved
one - S1 explicitly warns that WeChat may add a numeric suffix or introduce a new
feature database, which is exactly the guess this table refuses to become.

| Domain | Exact store basename | Provenance sources | Semantics | Acceptance effect |
|---|---|---|---|---|
| `emoticon` | `emoticon.db` | Tier 2 `core/wechat_db.py`; S1, S6 | sticker/emoji CDN md5 index | visible, non-required, non-blocking |
| `sns` | `sns.db` | S1, S5, S6 | Moments feature data | visible, non-required, non-blocking |
| `favorite` | `favorite.db` | S1, S5, S6 | saved/favourite items | visible, non-required, non-blocking |
| `favorite` | `favorite_fts.db` | S1, S5, S6 | full-text index over saved items | visible, non-required, non-blocking |
| `head_image` | `head_image.db` | S1, S6 | avatar/head-image cache | visible, non-required, non-blocking |
| `hardlink` | `hardlink.db` | S1, S6 | attachment link index | visible, non-required, non-blocking |
| `bizchat` | `bizchat.db` | S1, S6 | business-chat group/user metadata | visible, non-required, non-blocking |
| `third_app_icon` | `third_app_icon.db` | S1, S6 | third-party app icon cache | visible, non-required, non-blocking |

Revisions, pinned: S1 GreenBubbles `69f19c7089d7ef011e9ba43d44c6fb4e6e5be98b`;
S5 wechat_wish_agent `069fdd9448695ed604a789a9921b85614cea2b10`;
S6 wx-qq-decrypt `f9b3c7f9e69205d45d6ea8bdabe4444fd1f9c4a5`. S1 and S6
independently spell all seven exact domain/store paths; S5 names most. The
`hardlink_<n>.db` shape is Tier-2-reader auxiliary, never proven layout, so it is
**not** an exempt store. Deliberately absent: `chatbot` (sources describe
chatbot *messages*), `general` (documented tables hold message-event records such
as recalled message content), `solitaire` (single-source only), `weclaw.db`
(semantics uncertain).

### 19.4 What changed in code

In `acquisition/container_accounting.py`: `PROVEN_STORE_NAMES` is the new
`(domain -> exact basenames)` table, and `is_proven_physical_store()` is the only
exemption predicate. `_blocking_observations` now exempts an unknown row only via
that predicate instead of via domain membership, and the now-unused
`_is_outside_reader_boundary()` helper was deleted. `classify_database_name`,
`_MESSAGE_LIKE`, the message-resource classification, the domain set, traversal
and the bridge are untouched. The single predicate is unchanged:
`meets_requirements()` -> `unmet_requirements()` -> `_blocking_observations` ->
`domain_summary` `blocking_*` counts, so a blocking physical-only unknown
increments `blocking_unknown_count`, while an exempt proven store increments only
the plain `unknown_count`.

Section 19.7 corrects two ways that predicate still failed to reach, both found by
independent adversarial review of this committed state rather than by a test:
`_container_role` gained a `directory_identity` argument so classification applies
the proven-domain rule itself, and `_account_directory` now accounts a
non-hidden regular file that is not a `.db` candidate inside a proven domain.
`classify_database_name` itself is still untouched and still location-free.

### 19.5 RED-first evidence

- **RED-L** (earlier section 19 narrowing, now historical) - 33 failed / 414 passed
  before that production edit.
- **RED-M** (Decision C) - with the new tests in place and the production
  exemption still keyed on domain membership, 9 failed / 310 passed: the
  per-domain narrowing sweep across all seven proven domains, the aggregate
  blocking-split expectation, and the refused non-regular entry inside a proven
  domain. **Correction to the earlier wording of this bullet:** these were not a
  clean RED against unmodified `HEAD`. The first attempt failed at *collection*,
  because `PROVEN_STORE_NAMES` did not exist in the committed module at all; the
  9-failure run happened after the predicate was added but while the old blanket
  blocking condition was still in place, and some of those failures were stale
  tests written for the blanket model. It is corrective RED evidence that the old
  blocking condition contradicted the new tests, not a historical proof that
  every one of them failed first against shipped code.
- Tests that encoded the broad exemption were rewritten, not worked around: the
  accounts-and-keeps-roles test, the proven-domain acceptance parametrizations,
  the aggregate blocking-count expectations, and the refused symlink case - the
  last of which now asserts blocking, because a symlink is never a regular-file
  store.

### 19.6 Status

A10 remains **UNMET**, P4-A **9/10**, P4-B **MET 4/4**. E-030...E-036 real evidence
is not rescored. A fresh real structural rerun under this policy, with separate
explicit authorization, is still the only way to learn how it behaves on a real
container.

### 19.7 Second-pass correction: two escapes the store predicate could not reach

The `(domain, basename)` predicate in 19.4 is exact, but it was only ever asked
about rows that were already `ROLE_UNKNOWN` and already selected as candidates.
Two classes of unproven file inside a proven domain therefore escaped it:

1. **A basename the generic ledger recognises for another reason.**
   `favorite/sns.db` was classified `ROLE_AUXILIARY` (the location-free
   `classify_database_name` recognises `sns.db`, `chatbot.db`, `media.db`,
   `message_fts.db`), so it was visible, correctly non-blocking in the sense that
   no requirement was unmet, and silently outside the exemption decision - even
   though `sns.db` is not a proven `favorite` store. The predicate returned the
   right answer and was never consulted.
2. **A file that is not a candidate at all.** `_is_candidate()` accepts only
   names ending in `.db`, so `favorite/favorite.db.bak` and a `-wal`/`-journal`
   companion of a proven store were dropped before classification. Inside a
   domain whose exemption rests on accounting for everything it holds, a
   silently dropped regular file is a hole in the claim, not sidecar noise.

The correction is two rules in this module, not a new exemption:

- `_container_role` classifies inside a proven physical-only domain *before*
  consulting the generic ledger. A message-shaped name still becomes an
  unsupported candidate and blocks. Any other name that is not that domain's own
  proven store becomes `ROLE_UNKNOWN`, so it is visible and blocking. Outside a
  proven domain nothing changes: `sns.db` in `some_domain/` is still auxiliary
  and still non-blocking, exactly as before.
- `_account_directory` accounts a non-hidden regular file that is not a `.db`
  candidate when its parent is a proven physical-only domain, as
  `ROLE_UNKNOWN`. Hidden files stay ignorable everywhere, and directories,
  symlinks and unreadable entries keep their existing refusal paths unchanged.

Truthful roles inside a *proven* store are preserved: `favorite/favorite.db` is
still classified by the generic ledger as auxiliary and still exempt, because it
is that domain's own proven store. No proven store is relabelled to unknown; the
correction only denies an unproven store a role borrowed from elsewhere.

The identity-anchor semantics of 17.2 are untouched: the anchor exemption still
applies to its own directory class and only to a regular-file unknown.

### 19.8 RED-first evidence for the second-pass correction

- **RED-N** (section 19.7) - 15 failed / 2 passed, against the *already-committed*
  19.4 predicate, before any production edit for this pass. The failures were
  exactly the two escapes: six unproven-but-generically-recognised basenames in
  `favorite/` (`sns.db`, `chatbot.db`, `media.db`, `message_fts.db`, `session.db`
  and the remainder of the parameterisation), three non-`.db` look-alikes
  (`favorite.db.bak`, `favorite.db-wal`, `favorite.db-journal`), and one proven
  store's `-wal` sidecar in each of the seven proven domains.
- The two passing cases in that run were the pre-existing hidden-sidecar tests,
  which the correction deliberately does not change.
- One pre-existing test encoded the escape rather than the invariant:
  `test_a_recognised_non_message_symlink_name_adds_no_blocker_in_a_physical_only_domain`
  asserted that a refused `media.db` symlink inside a proven domain adds no
  blocker. That is exactly the laundering 19.7 removes, so it now asserts
  blocking, and a sibling test was added pinning the unchanged outside-a-proven-
  domain behaviour.

Final synthetic counts for this second pass: `acquisition/tests/test_container_accounting.py`
336 passed, `test_database_inventory.py` 34 passed, `acquisition` 520 passed,
`wechatdb` 514 passed, `bridge` 272 passed. `git diff --check` and the populated
staged check passed. Counts recorded in earlier sections are historical and are
left as they stood. No real source, credential, Keychain, decryption, process,
WeChat or Database Mode action was taken.

The row for proven-store sidecars in the 19.2 table and the `-wal`/`-journal`
examples in 19.7 and 19.8 are **superseded by section 20**. The RED-N failures
they describe were real and are kept as history; the conclusion drawn from them
-- that a standard SQLite companion of a proven store is an unproven store -- is
withdrawn.

---

### 19.H Superseded controller-review draft (historical record)

The earlier draft of this section concluded:

> **Rejected as too broad:** the blanket directory-wide exemption. In `9edbbbe`
> membership in a physical-only domain made message-shaped candidates, nested
> directories and some refused/non-regular entries non-blocking.
>
> **Accepted replacement:** only a direct regular-file unknown is exempt inside a
> proven physical-only domain. Candidate, nested, unreadable and refused/non-regular
> unknown-or-candidate risk remain fail-closed.

That replacement was itself too broad and is superseded by 19.1 Decision C: a
proven domain exempts nothing by itself, and only an independently proven exact
store is exempt. Its A10 / P4-A / P4-B status lines are unchanged and still
accurate.

## 20. SQLite companion semantics reconciled (2026-10-02)

Still synthetic and policy-only: **no real source was accessed, listed, statted
or searched**, no real name or count shaped any rule, and A10 was not run. This
section supersedes only the sidecar rows of 19.2 / 19.7 / 19.8. Every historical
real result, count and verdict in this document stands unchanged.

### 20.1 The defect in the 9507592 policy

`9507592` was over-conservative for exactly one class of name. It made every
non-hidden, non-`.db` regular file inside a proven physical-only domain a
blocking `ROLE_UNKNOWN`, and its own RED-N bullet lists
`favorite.db-wal` and `favorite.db-journal` among the names it expected to block.

That contradicts committed architecture this same repository already relies on:

- `database_inventory._is_candidate()` accepts only a name ending in `.db`,
  documented as the choice that "excludes the -wal and -shm sidecars, which
  belong to a database rather than standing for one".
- `BoundedSourceRefresher` attaches `name + "-wal"` and `name + "-shm"` to the
  main database's `EncryptedSource`.
- `EncryptedSource` / `EncryptedSnapshot` carry wal/shm as companion paths on
  **one** source rather than as independent stores.
- `bridge/database_bootstrap.py` forms the same two names.

So the reader already knows a sidecar belongs to a database. Physical-only
accounting contradicted it and made `favorite.db-wal` read as an independent
unproven store inside a domain the same table proves. Fail-closed governs
*uncertain* Reader truth; an artifact already known to belong to a known
database is not uncertain. Conservatism that blocks a fact the project has
already established is a false blocker, not safety.

### 20.2 The corrected invariant

A standard SQLite companion of an exact provenance-backed store is a companion
of that database, not an independent store.

Recognition is exact on all three axes, with a closed three-suffix set
(`PROVEN_STORE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")`):

    <exact proven store basename> + "-wal"
    <exact proven store basename> + "-shm"
    <exact proven store basename> + "-journal"

The suffix is appended to the **complete** database basename. The companion of
`favorite.db` is `favorite.db-wal`, never `favorite-wal`.

Such a companion is:

- structurally accounted, with the same exactly-once key as any other row;
- given **no** database role -- not `ROLE_AUXILIARY`, not `ROLE_MEDIA`, not
  `ROLE_UNKNOWN`, because all three are false claims about a file that exists
  only to serve another database;
- never entering `_blocking_observations`, so it satisfies no required role and
  cannot produce `unknown_database`;
- strengthening no coverage and adding no `domain_summary` database or role
  count.

No file is opened and no content is read; this stays a name-shape decision.

| Observation | DB role? | Visible / accounted? | Blocking? |
|---|---|---|---|
| `favorite/favorite.db` (proven store) | truthful existing role, unchanged | yes | no |
| `favorite/favorite.db-wal` | no role | yes, companion row | **no** |
| `favorite/favorite.db-shm` | no role | yes, companion row | **no** |
| `favorite/favorite.db-journal` | no role | yes, companion row | **no** |
| `favorite/favorite.db.bak` | `ROLE_UNKNOWN` | yes | **yes** |
| `favorite/favorite.db.wal` (dot variant) | `ROLE_UNKNOWN` | yes | **yes** |
| `favorite/future.db-wal` (unproven store) | `ROLE_UNKNOWN` | yes | **yes** |

### 20.3 What the correction explicitly refuses

Matching is exact. No fuzzy, prefix, case-folding, recursive-stripping or
inferred-variant matching, and no suffix beyond the three SQLite itself
appends. All of these remain unproven and blocking inside a proven domain:

- `favorite.db.wal`, `favorite.db.shm` -- dot variants, not SQLite sidecars;
- `favorite-wal`, `favorite-shm`, `favorite-journal` -- suffix on a truncated
  basename;
- `favorite.db-wal-wal` -- a sidecar of a sidecar;
- `favorite.db-wal.bak`, `favorite.db.backup` -- a backup, not a companion;
- `Favorite.db-wal`, `favorite_1.db-wal` -- look-alikes, no case-folding and no
  inferred numeric variant;
- `future.db-wal` -- see below.

**A sidecar of an unproven store inherits no exemption.** `future.db-wal` is
formed from `future.db`, which is not in `PROVEN_STORE_NAMES["favorite"]`, so
both the store and its companion remain visible blocking unknowns. Companion
naming must never become a way to bypass store provenance: exempting the
companion of an unproven store would silently drop the main database from
accounting while the bytes it names still sit there.

`ContainerSidecar.__post_init__` re-validates every row against
`is_proven_store_sidecar`, so a caller cannot hand the accounting a row that
merely claims to be a companion. Verified directly: `ContainerSidecar(...,
"future.db-wal", "favorite")`, `"favorite-wal"` and `"sns.db-wal"` are all
refused with `ValueError`.

Symlink and non-regular behaviour is unchanged and verified independently of the
name: a `favorite.db-wal` **symlink**, a `favorite.db-shm` symlink and a real
`favorite.db-journal` **directory** all produce `not_a_regular_file` /
`nested_directory` rejections, no companion row, and still block. Only a plain
regular file can be a companion, exactly as only a plain regular file can be a
proven store. Hidden files remain ignorable everywhere.

Outside a proven physical-only domain nothing changes at all: message-source
semantics are preserved untouched, so `message/message_0.db-wal` and
`message/message_0.db-shm` remain non-independent, out of the database
inventory, and are not represented as companions here. This section
reconciles physical-only accounting with the already-sealed global sidecar
model; it does not redesign message acquisition.

### 20.4 Representation and evidence

The minimal representation is a dedicated row type, `ContainerSidecar`, plus a
`ContainerAccounting.sidecars` tuple. A companion is deliberately *not* a role,
so it gets its own type instead of being squeezed into `ContainerDatabase` with
a misleading role token.

`accounts_for()` counts companions in the exactly-once key set, so total
accounting stays true in both directions -- dropping them would leave real bytes
unaccounted inside a domain whose whole exemption rests on accounting for
everything it holds. Verified: `accounts_for` over the complete row set is
`True`; omitting sidecar keys makes it `False`.

Aggregate evidence stays name-free and path-free. `evidence()` adds a
top-level `companion_count` and each `domain_summary` boundary class gains a
`companion_count`; the count is deliberately absent from `database_count` and
`role_counts`, since a companion adds no coverage and no role. A fresh real A10
can therefore say "known physical-only companions: N" without identifying any
of them. Verified: neither `favorite`, `db-wal`, `db-shm`, `db-journal` nor the
root path appears anywhere in `evidence()` or `domain_summary`.

The single acceptance truth is preserved with no wrapper override:
`meets_requirements()` -> `unmet_requirements()` -> `_blocking_observations()`.
Sidecars never enter `_blocking_observations` and never satisfy a required
role, so `favorite/favorite.db` + `favorite/favorite.db-wal` alone yields only
`required_role_missing` and never `unknown_database`, while
`favorite/future.db` + `favorite/future.db-wal` yields both
`unknown_database` and `required_role_missing`.

### 20.5 Test correction and RED-first evidence

The pre-existing test `test_a_proven_store_sidecar_stays_a_blocking_unknown`
built its name with the equivalent of `"favorite.db".replace(".db", "-wal")`,
which yields `favorite-wal` -- **not** `favorite.db-wal`. It therefore never
tested the SQLite WAL naming convention at all, and its passing result was
evidence about a name SQLite never produces. The defect was corrected
RED-first: the wrong-naming test was removed and replaced with explicit
companion tests for all three suffixes, including a direct assertion that
documents the `favorite-wal` / `favorite.db-wal` distinction. The production
edit came after the behavioural RED, not before it.

- **RED-O** - against unmodified `9507592` production: **52 failed / 339
  passed**. The failures were behavioural, caused by the missing structural
  companion handling (`ContainerAccounting.sidecars` did not exist), not a
  collection failure. No production code had been edited when that RED was
  recorded. (An earlier attempt that failed at collection -- because the tests
  imported a not-yet-defined production constant -- was discarded and is not
  counted as RED evidence.)
- Several RED expectations were corrected during GREEN because they
  contradicted the sealed architecture rather than the code under test: a
  proven store keeps its truthful generic role (`sns.db` is auxiliary,
  `favorite.db` is unknown-but-exempt) and the verdict, not the role count,
  proves non-blocking; `future.db-wal` belongs to an unproven store so it must
  remain a second visible blocking unknown; `accounts_for()` expects the
  complete accounted key set; and message-side sidecars stay ignored rather
  than becoming `ContainerSidecar` rows.

Final synthetic counts: `acquisition/tests/test_container_accounting.py` 391
passed, `test_database_inventory.py` 34 passed, `acquisition` 575 passed,
`wechatdb` 514 passed, `bridge` 272 passed. No real source, credential,
Keychain, decryption, process, WeChat or Database Mode action was taken.

### 20.6 Status

A10 remains **UNMET**, P4-A **9/10**, P4-B **MET 4/4**. All historical real A10
evidence is unchanged and is not rescored by this correction. Whether the
companion rule behaves correctly against a real container is untested: only one
fresh real structural rerun, under separate explicit operator authorization,
can answer that.

## 21. Final fresh real structural rerun under the sealed companion policy (2026-10-02)

This is a **new distinct evidence event after
`4d6f8a8081d3751495e205295b0a93c93af335da`**. No prior real result is
rewritten, rescored or retroactively claimed to have run under this policy.
Sections 3, 12, 14, 16, 18 and 20 remain exactly as recorded.

### 21.1 Designation and execution

The operator explicitly designated the exact container root to be used. The
designation was a single explicit path; it was not discovered, globbed,
searched or substituted, and no alternative root was tried. The designated
path is deliberately not reproduced here.

- Real invocation count of `account_container()`: **exactly 1**.
- A second attempt was refused by an execution-local marker **before** the
  container was invoked, so the one-invocation guard is behavioural evidence,
  not a promise.
- The single acceptance predicate was the sealed one; there was no wrapper
  override and no second truth source.

### 21.2 Source integrity

The source was hashed immediately before and immediately after the run.

- source pre/post unchanged: **YES**
- no mutation, restore, normalisation, staging or write of any kind

### 21.3 Acquisition boundary

Confirmed **NO** for all of: discovery or substitution of the designated root,
recursion, content access, credential retrieval, Keychain access, bootstrap,
decryption, snapshot, LLDB, Frida, process attach, re-signing, any WeChat
interaction, any mutation of the source, and persistent enrollment.

### 21.4 Machine acceptance

- `meets_requirements()`: **False**
- Evidence tokens and direct predicate tokens were identical.
- Exact unmet token list (fixed vocabulary, aggregate):
  `directory_unexamined`, `unknown_database`
- No wrapper override. No required-role failure. No
  unsupported-message-candidate token.

**A10 remains UNMET.**

### 21.5 Accounting

| Quantity | Value |
|---|---:|
| DB rows / unique DB keys | 27 / 27 |
| Total accounting keys / unique | 45 / 45 |
| — DB | 27 |
| — nested rejections | 2 |
| — companions | 16 |
| Duplicates | 0 |
| Examined directories | 15 |
| `accounts_for(complete_set)` | True |

Required roles:

| Role | Status | Count |
|---|---|---:|
| ordinary message | present | 7 |
| session identity | present | 1 |
| contact identity | present | 1 |

### 21.6 Boundary summary

| Boundary | DB | Unknown | Candidate | Nested | Unreadable | Blocking unknown | Blocking candidate | Blocking nested | Blocking unreadable | Companions |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| required_message | 12 | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| required_identity | 3 | 1 | 0 | 1 | 0 | 0 | 0 | 1 | 0 | 0 |
| known_physical_only | 8 | 7 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 16 |
| ambiguous | 4 | 4 | 0 | 1 | 0 | 4 | 0 | 1 | 0 | 0 |

- Blocking totals: **5** unknown, **2** nested, **0** candidate.
- Companion total: **16**, all inside `known_physical_only`.
- Unknown balance: **13** = 5 blocking + 8 exempt.
- The candidate blocker present under earlier policies **disappeared** under
  the sealed companion policy.
- All 16 companions were correctly excluded from DB rows and from role
  assignment. Under the superseded `9507592` policy the same 16 artifacts
  would instead have contributed 16 additional false unknown blockers.

### 21.7 Role summary (aggregate)

| Role | Count |
|---|---:|
| ordinary_message | 7 |
| unknown | 13 |
| media | 2 |
| auxiliary | 1 |
| business_message | 1 |
| search_index | 1 |
| session_identity | 1 |
| contact_identity | 1 |

### 21.8 Companion summary

- total `companion_count`: **16**
- by boundary class: `required_message` 0, `required_identity` 0,
  `known_physical_only` 16, `ambiguous` 0

### 21.9 Independent review

A read-only review of the sealed implementation and the sanitized receipt was
performed after the run. The reviewer did **not** invoke the real container a
second time.

- Result: **no Critical and no Important finding.**
- Confirmed: exact-source authorization honoured; no discovery or
  substitution; exactly one real invocation; source-unchanged guard valid;
  store-level exemption rather than a domain blanket exemption; unproven
  stores still block; the candidate class still blocks; nested rejections
  still block; anchor scope preserved; the exact-resource rule preserved;
  companions isolated from DB rows and roles; one acceptance predicate only;
  `blocking_*` totals agree with the machine unmet tokens; no source mutation.
- Privacy scan of the receipt found no real path, filename, account identifier
  or digest. Only fixed vocabulary tokens such as `message_directory` and
  `business_message_unread` matched.
- No execution defect was found, so the production-fix path was not triggered.

### 21.10 Residual blockers (aggregate only)

Blocking classes remaining: **5 unknown** and **2 nested**, spread across the
`required_message`, `required_identity` and `ambiguous` boundary classes. No
candidate blocker and no unreadable blocker remain. No file, store or domain is
identified here; resolving these requires independent provenance work, not
another run under the current policy.

### 21.11 Tests

No production code changed in this phase, so only the synthetic suites were
run:

- focused container accounting + database inventory: **425 passed**
- full `acquisition`: **575 passed**
- `git diff --check`: clean
- repository was production-clean immediately afterwards

### 21.12 Status

- **A10 UNMET**
- **P4-A 9/10**
- **P4-B MET 4/4**

This is the **final real rerun under the current sealed policy**. Further real
A10 access is not authorized and should not be attempted until independent
provenance or synthetic work produces a materially new, sealed policy. Do not
schedule another real rerun on the strength of this result.
