# DB Reader P4-A A10 — bounded real container-wide structural classification gate

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

## 10. Next step (not taken here)

The gate stops. A **separate synthetic corrective capsule** is required to fix
the accounting identity defect and cover a root with multiple directories mapping
to the same location token, plus the nested-directory condition this container
exposed. Only after that fix is sealed on synthetic fixtures should container
accounting be re-attempted, under a fresh designation and a fresh real gate.
P5 / Database-route product-integration planning remains a separate decision
and was not started.
