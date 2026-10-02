# DB Reader P4-A A10 — bounded real container-wide structural classification gate

> **A10 BLOCKED — approved input unavailable.**
>
> The gate was **not executed against any real container**, because no
> approved, already-selected WeChat source input exists for it. This document
> records that honestly and contains **no fabricated aggregate evidence**.
> Checklist item **A10 remains UNMET** and **P4-A remains 9 MET / 1 UNMET**.

## 1. Gate identity

| | |
|---|---|
| Gate | A10 — bounded real container-wide structural classification |
| Baseline commit | `6355ebf739726ceea5832cde87c865e504c1ddde` (`v2/rewrite`) |
| Authoritative scope | D-040 P4-A; §13 A10 of `DB_READER_P4_COMPLETE_CONTAINER_READINESS.md` |
| Governing design | §10 of the same document; §15 P4-A of the design spec |
| Evidence scope | **none — no real container was read** |
| Content policy | structural/aggregate only; nothing recorded, because nothing was observed |

## 2. Why the gate did not run

A10 requires one *explicitly selected* current WeChat source root that this
project is already permitted to inspect read-only. That input does not exist
here, and obtaining it is exactly what this capsule is **not** authorized to do.

The check performed was narrow and project-owned: this repository defines
exactly one app-owned path for a recorded source decision —
`recorded_manifest_path()` =
`~/Library/Application Support/WeChatCompanion/database_source.json`
(`bridge/acquired_database_source.py:93-115`) — together with its acquisition
workspace sibling. Both are **absent**. Reading that one known path is the
project's own record lookup, not a discovery step.

Nothing was done to work around the absence. Specifically, and per §5 of the
capsule and D-005:

- no `$HOME` scan, no account-root guessing, no newest-account heuristic;
- no passphrase requested or read, no Keychain access, no bootstrap run;
- no snapshot, no re-acquisition, no key recovery;
- no LLDB, no Frida, no process attach, no re-sign, no WeChat launch;
- no real container, database, WAL or sidecar opened or listed.

Absence of the project-owned record is **not** by itself proof that no WeChat
source exists on the machine. It is proof that the project has **no recorded
selection** — and an inferred, discovered or operator-guessed root is precisely
what the capsule forbids substituting for one. So the correct outcome is
BLOCKED, not a probe.

## 3. What was built instead (synthetic only)

A container-level accounting primitive, `acquisition/container_accounting.py`,
was added so that the future real gate is a bounded structural read rather than
an improvised script.

- `acquisition/database_inventory.py` is unchanged. The message-directory
  classifier still maps `session.db`/`contact.db` to `auxiliary`; the recorded
  D-040 divergence is preserved, not edited away.
- Identity anchors are accounted **as required roles at container level**,
  which is the only place D-040's required-role set and the directory-shard
  classifier meet. No message parser, source default, discovery boundary or
  acquisition semantic was touched.
- Accounting is total over the boundary: every directory in the boundary is
  examined: every directory directly inside the selected root is listed,
  including ones outside the three named ones. This matters because real
  container layout holds
  databases those three do not contain (`message/message_fts.db`, and
  `emoticon/emoticon.db` in this repository's own v1 reader). Counting such a
  directory without listing it would have made §10's success condition — *no
  `.db`-bearing directory of the prepared source remains unexamined* — false
  while still reporting success.
- A directory that cannot be listed, or that contains a nested subdirectory
  (not walked by design), raises `directory_unexamined` and fails the predicate
  closed. A database sitting directly in the root is accounted; an earlier
  draft silently dropped root-level `.db` children, which a real gate would
  have misreported as every database classified exactly once.
- Accounting identity is keyed by `(location, name)`, so the same name in two
  directories is two databases rather than one double-counted name.
- `evidence()` emits counts and closed tokens only: no name, path, identifier,
  digest, or database content.
- Reading is `read_only_structural_classification`: `lstat` plus directory
  listing, no file opened, nothing written.

Synthetic fixtures only. No real data was used to build or test any of it.

## 4. Acceptance predicate (in code, synthetic)

A10 PASS iff **all** hold. `unmet_requirements()` returns the fixed tokens of
those that do not; each is a closed vocabulary item, assembled from no name and
no path.

| # | Condition | Encoded as |
|---|---|---|
| 1 | container source identity explicit / previously selected | caller-supplied root; no selection logic exists |
| 2 | session required role accounted | `required_role_missing` / `required_role_unclassified` |
| 3 | contact required role accounted | `required_role_missing` / `required_role_unclassified` |
| 4 | ≥1 ordinary message role accounted | `required_role_missing` |
| 5 | no required role missing | `required_role_missing` |
| 6 | no required role classified incompatible/unknown | `required_role_unclassified` |
| 7 | no unaccounted message-bearing role | `unknown_database`, `unsupported_message_candidate`, `directory_unexamined` |
| 8 | every optional/excluded role accounted explicitly | role counts; no role is silently dropped |
| 9 | business message, if present, stays explicit unsupported | `business_message` role + `business_message_unread` gap; not a required role |
| 10 | no source mutation | read-only by construction; no write path exists |
| 11 | evidence output structural/aggregate only | `evidence()` shape |

Condition 1 is satisfied by construction of the API but is **not** satisfied in
this capsule: no such root was available, which is precisely the blocker.

Optional and excluded roles never appear in the predicate. A present business
message, a search index, a media store or an unreadable optional directory is
accounted and visible, and weakens no ordinary-message claim.

## 5. Result

**A10 BLOCKED — approved input unavailable**

- Real gate executed: **NO**
- Real container observed: **none**
- Aggregate role counts recorded: **none** (nothing was observed; inventing any
  would be fabrication)
- Unknown / message-bearing gaps observed: **none observed — none examined**

### P4 status after this capsule

| Item | Status |
|---|---|
| P4-A | **9 MET / 1 UNMET** — unchanged; A10 still UNMET |
| P4-B | **MET** — future-generation fail-closed safety, unchanged |

No statement here generalises to future WeChat versions, all WeChat data, all
WeChat databases, business messages, media, native FTS, or Database Mode
readiness.

## 6. Privacy and boundary statement

- No message text, chat title, contact name, sender, username/wxid,
  conversation id, table digest, `Msg_<digest>` name, file path, account path,
  key, salt, passphrase, raw SQL error or row content was read, written or
  recorded.
- No internal inspection of a real source took place at all, so no sanitisation
  of real structural metadata was even required.
- No new credential, no bootstrap, no LLDB, no Frida, no re-sign, no process
  attach, no WeChat launch.
- No source mutation: nothing was opened, and nothing was written.
- `stash@{0}` (`wip capture experiments before system-window architecture`) was
  verified present. **No stash operation was performed during this capsule.**

## 7. Existing evidence reused (not re-run)

No real correctness gate was re-executed. A10 cites, rather than re-proves:

- G2 real multi-part message gate — `docs/v2/DB_READER_REAL_MULTIPART_GATE.md`, D-032
- WAL coherence evidence — same gate
- identity catalog real gate and its fixed `identity catalog schema unsupported`
  refusal (`wechatdb/provider/identity_catalog.py`)
- acquisition evidence — `acquisition/`
- schema-drift and typed-query gates — synthetic only

Required-role **compatibility** is supplied by those existing results; this
capsule adds only the container-wide **classification/accounting** observation
primitive, and did not perform that observation.

## 8. Next step (not taken here)

The capsule stops. A future gate needs an explicitly selected source root that
the project is already permitted to inspect read-only. Per the capsule, P5 /
Database-route product-integration planning is a **separate decision** and was
not started.
