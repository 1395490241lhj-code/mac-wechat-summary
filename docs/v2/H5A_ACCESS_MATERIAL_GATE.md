# H5A — access-material gate (historical block; one-shot pilot later completed)

**Historical status (2026-09-08): blocked before any database access.** At
that point no readable WeChat database or authorized access file existed, so
the phase stopped at its first gate.

**Reconciliation (2026-09-16).** A separately consented, one-shot local pilot
later produced and validated one readable `message_0` copy long enough to run a
bounded real-database compatibility test. The technical smoke therefore did
happen after this original report. Because the D-030 A3 pre-execution baseline
could not later be evidenced, that one-shot decision lapsed and all temporary
pilot artifacts plus the retained access file were destroyed. The current
standing state again has **no retained access material**, but the historical
claim that no real-database smoke was ever attempted is no longer current.

## Where `database_access_material_required` actually comes from

Earlier sessions could not find this token in the repository or the vault, and
treated it as a project-side paraphrase. It is neither: it is **the Rion
reader's own error code**, recorded on 2026-09-05 by the operator's phase-0 run
in `~/Projects/wechat-reader-spike/state/setup.json`:

```
ok = false
error.code = database_access_material_required
error.message = 发现 27 个无法由 SQLite 直接打开的数据库文件。
                当前 CLI 缺少这些数据库的授权访问材料，不能完成完整历史读取。
```

So the blocked state is a real, previously-recorded reader verdict against the
real container: 27 database files that SQLite cannot open directly, and no
authorized access material for them. `setup` failed, which is why no
`config.json` was ever written beside it.

## Inventory — what exists, and what does not

Candidates were enumerated by path, name, size and mode. Contents were read
only where necessary to decide whether a file *is* access material, and only
structural or path fields were extracted.

| Location | Result |
|---|---|
| `~/.config/rion-wechat-reader/` (the reader's default config + keys home) | **absent** |
| `~/Projects/wechat-reader-spike/state/config.json` (the spike's configured path) | **absent** — `setup` failed, so it was never written |
| `~/.wechat-summary/config.json` (v1-generation config, Aug 23) | present, but the `keys_file` path it names **does not exist**, and `decrypted_dir` does not exist |
| Structural sweep of every `keys.json` / `all_keys.json` / `config.json` under `$HOME` | **no file in the wechat-cli-plus path-map shape** (`path → {enc_key, salt}`) |
| `Library/Application Support/*/TrustTokenKeyCommitments/keys.json` | Chromium infrastructure, unrelated |

The Sep 5 content-bearing state files (`digest.json`, `status.json`,
`doctor.json`, `discover.json`, `layout.json`) were **not opened**. Only
`setup.json` was read, and only its path and readiness fields were extracted —
it carries a diagnostic error, not chat content.

**No access material exists on this machine.**

## What was deliberately not done

- No key was derived, extracted, reconstructed, or obtained from process
  memory, from WeChat, or from the v1 generation's memory-scanning route.
  D-017 keeps access-material generation an opaque external prerequisite, and
  the v1 route is exactly what v2 excludes.
- The reader was never started. `RionReaderAdapter.status()` was not called:
  without material it could only return `reader_not_ready`, which would be a
  restatement of the inventory rather than evidence about the real database.
- The WeChat container was read only for directory existence.

The pinned reader remains available for the real smoke, unchanged and
verifiable offline: revision `3afe33e0742ef4e92b4babe399bf471fdcd86a7b`,
`rion_wechat_reader.py` sha256
`ad0ec2066fc259070780b1c858ee0b2f7112d5287b7bd0b6fe6963cd79158d7e` — the
B1-accepted hash, re-derived from the local mirror during this phase. (The
installed `wechat-reader-spike` copy is a *different*, older revision and must
not be substituted.)

## Cleanup

| Check | Result |
|---|---|
| Plaintext database copies | none (`~/.wechat-summary/decrypted` does not exist) |
| Temporary key / access-material copies | none — the synthetic B1 fixture and its random keys were removed |
| Reader processes | 0 |
| Open ports | 0 |
| Request dumps / session databases | 0 / 0 |
| WeChat container mutations | none |
| `~/.hermes` / `wechatshadow` mutations | none; `SOUL.md` still `29993087…` |

## Also closed in this phase — H4.1 item 3 (test-only)

The K3 regression debt from `H4_SYNTHETIC_DIGEST_EVALUATION.md`: one of 17 H4
runs attempted `skill_manage`, which errored and changed nothing, and the model
then narrated the misfire in its reply.

Closed without touching runtime behaviour or SKILL.md:

- `evaluation/test_digest_skill.py` pins the written rule that already forbids
  it ("Do not reach for any other tool to work around that", "out of scope for
  this skill") and asserts no mutating skill tool is named anywhere in the
  skill.
- `shadow/hermes_online.py` adds `skill_manage` / `skills_list` / `skill_view`
  to the digest output-hygiene list, so a digest that names a runtime tool —
  especially to explain a failed call — fails the same gate as a leaked field
  name.

Suites after the change: **54** skill, **158** harness.

## Current standing verdict

**NO RETAINED ACCESS MATERIAL; D-030 LAPSED.**

The one-shot pilot established the real database root, one working
`message_0` access mapping, a successful plaintext SQLite open, and the real
message schema family. It did **not** establish complete multi-database
coverage or production ReaderAdapter readiness. Because the pilot's access
file was destroyed under the D-030 cleanup/revocation rule, there is no
standing database input to route today. Any repeat acquisition requires a new
explicit decision and per-occasion consent; this document does not authorize
one.
