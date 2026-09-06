# Database-reader interface gate — synthetic Rion path-map interoperability

**Status: sealed phase report, 2026-09-06. Synthetic only.** No real WeChat
database, no WeChat container access, no access-material tooling, no `sudo`,
no Keychain, no WeChat process, no production code change. Read-only
architecture evidence; not a production approval.

## Question

Can an access file in the archived `maomao3334/wechat-cli-plus` `all_keys.json`
shape (relative database path → `enc_key` / `salt` / `size_mb`) be consumed by
the archived Rion reader unchanged, and then drive a read of an encrypted
WeChat-shaped SQLCipher fixture through Rion's normal public CLI path, without
persistent plaintext and without touching the source files?

## Environment

| Item | Value |
|---|---|
| Rion reader revision | `3afe33e0742ef4e92b4babe399bf471fdcd86a7b` (local archive mirror, read-only export) |
| Python | 3.12.14, disposable venv in the session scratchpad |
| `sqlcipher3` | 0.6.2 (Rion's own pinned version; SQLCipher 4.12.0 community) |
| `zstandard` | 0.25.0 |
| HOME / TMPDIR | redirected into the scratchpad; real `~/.config` confirmed untouched |

## Method

1. Built a plaintext fixture with random, meaningless records, then encrypted it
   with a randomly generated 64-hex raw key using the same `sqlcipher_export`
   mechanism Rion's own test suite uses.
2. Placed one additional zstd-compressed message (`WCDB_CT_message_content = 4`)
   **only in the WAL**, so the main file never contained it.
3. Wrote the access file **independently** (own serializer, not Rion's helper)
   in the wechat-cli-plus shape, mode `0600`.
4. Ran Rion `import-access` with the explicit synthetic root, then `setup`,
   `sessions`, `contacts`, `resolve-chat`, and `history`.
5. Compared SHA-256 of every fixture file before and after; inspected residue.

All CLI output was redacted for 64-hex strings before capture. The key never
appeared in any captured output or evidence file.

## Results (counts only)

| Check | Result |
|---|---|
| Synthetic fixture | 3 encrypted databases (contact, session, one message shard); 1 session, 2 contacts, 3 messages |
| Access-file format | wechat-cli-plus-shaped path map **accepted directly** as `path_key_map` |
| Keys matched on import | 3 / 3, 0 unresolved |
| Missing databases | 0 |
| `setup` / `doctor` | `ready`; 3 encrypted databases; session, contact, message schemas compatible; 1 WCDB-compression-capable table |
| Session row | 1 read, display name resolved |
| Contacts | 2 listed; `resolve-chat` returned 1 correct candidate |
| Messages | 3 read in local_id order; sender resolved for contact and self |
| WAL-backed message | **readable** (snapshot copied `-wal`/`-shm`) |
| zstd-compressed message | **readable** (decoded via compression-type column) |
| Source encrypted files | **byte-identical** before/after; none carries a plaintext SQLite header |
| Persistent plaintext databases created | **0** |
| Temporary Rion snapshot directories remaining | **0** |
| Files created outside the fixture | 2 (`keys.json`, `config.json`), both `0600` |

## Verdict

**INTERFACE GATE PASS.** The access-file interface and the Rion read path are
compatible for the tested shape. The operator-side adapter is two preconditions,
not code: the source file must be `0600`, and the database root must be passed
explicitly because the path-map format carries no root.

## What this does not prove — remaining unknowns

- **macOS WeChat 4.1.13 real-database cipher compatibility: pending.** The
  fixture used SQLCipher 4 defaults. Source reading suggests the macOS layout
  matches, but no real database was opened.
- **Real access-material availability: pending.** Treated as an opaque external
  prerequisite; nothing was acquired or attempted.
- **Windows real-database cipher compatibility: unknown**, not incompatible. The
  path-map format carries no cipher parameters.
- **Licensing.** Running Rion as a separately installed external process
  *reduces AGPL coupling risk*; it is **not a legal conclusion** and does not by
  itself resolve licensing obligations.

## Resulting architecture (recorded, not implemented)

```
External AccessMaterialProvider   (opaque; outside this project)
        ↓
Generic ReaderAdapter             (owned by mac-wechat-summary; protocol only)
        ↓
Rion external implementation      (independently installed, separate process)
        ↓
NormalizedMessage
        ↓
existing MCP bridge / AgentRunner
```

VisualReader (the current capture path) remains the fallback and the shipped
product. D-002, D-005 and R-003 are unchanged by this gate.
