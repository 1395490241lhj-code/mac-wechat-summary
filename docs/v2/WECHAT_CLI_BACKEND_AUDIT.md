# WeChat CLI Backend Audit

Status: architecture research only; no implementation decision or production approval

Upstream reviewed: [`huohuoer/wechat-cli`](https://github.com/huohuoer/wechat-cli) at commit [`a3789232d4f79bf0b30634d9dadbce71e4acd601`](https://github.com/huohuoer/wechat-cli/tree/a3789232d4f79bf0b30634d9dadbce71e4acd601) (version 0.2.4)

Scope: post-key-extraction data access only

## Executive conclusion

The query layer can operate from an existing `all_keys.json` and WeChat data directory without reading the WeChat process, activating WeChat, sending input, or modifying `WeChat.app`. The prohibited behavior is concentrated in `init` and the `keys` package, but the stock CLI still ships and imports that code. It is therefore not an acceptable production subprocess dependency in its current form.

For a native SwiftUI v2, the recommended production direction is **C: a native, read-only reimplementation**, using the upstream project only as a behavioral/schema reference and only after license/provenance review. A narrowly stripped Python core is a possible disposable validation bridge, not the preferred shipped architecture. Invoking the unmodified CLI is not recommended.

The most important engineering gaps to address are:

- The upstream decrypted cache contains persistent plaintext database copies in a shared temporary directory.
- Raw database and WAL files are read independently while WeChat may be writing; the result is not guaranteed to be a coherent SQLite snapshot.
- `new-messages` stores only one timestamp per chat in a global, non-account-scoped state file and returns session summaries rather than every message since the previous call.
- The code is coupled to specific table/column names, SQLCipher page parameters, zstd markers, and WeChat message-type encodings.
- Upstream declares Apache-2.0 but attributes its core decryption and parsing capabilities to `ylytdeng/wechat-decrypt`. That repository is currently unavailable following a GitHub DMCA takedown notice. License compliance and legal permission to distribute a derivative therefore require separate review; this document is not legal advice.

## Safety boundary

The safe adapter must exclude these files and artifacts entirely, not merely hide them behind a runtime option:

- `wechat_cli/commands/init.py`
- `wechat_cli/keys/**`
- `wechat_cli/bin/**`, including memory-scanning executables
- any subprocess invocation of `codesign`, `sudo`, or a scanner
- any process-discovery, process-memory, `task_for_pid`, AX action, application activation, or UI-input code

The reason is concrete: [`scanner_macos.py`](https://github.com/huohuoer/wechat-cli/blob/a3789232d4f79bf0b30634d9dadbce71e4acd601/wechat_cli/keys/scanner_macos.py) invokes a bundled process-memory scanner and, after a `task_for_pid` failure, can automatically run `codesign --force` against `/Applications/WeChat.app` with `com.apple.security.get-task-allow`. [`init.py`](https://github.com/huohuoer/wechat-cli/blob/a3789232d4f79bf0b30634d9dadbce71e4acd601/wechat_cli/commands/init.py) is the route that invokes key extraction.

No scanner, initialization command, database, or private WeChat data was executed or opened for this audit.

## Reviewed architecture

The stock CLI has four relevant layers:

1. `main.py` registers commands and constructs `AppContext` for every command except `init` and `version`.
2. `AppContext` loads one configuration, reads the existing keys JSON, creates `DBCache`, and discovers message shard keys.
3. `DBCache` decrypts encrypted databases and WAL pages into plaintext SQLite cache files.
4. Command modules query the plaintext SQLite files through `sqlite3`, using `messages.py` and `contacts.py` for decoding and name resolution.

Although normal commands do not execute key extraction, [`main.py`](https://github.com/huohuoer/wechat-cli/blob/a3789232d4f79bf0b30634d9dadbce71e4acd601/wechat_cli/main.py) imports and registers `commands.init`, which imports the platform key-extraction package. This has no observed import-time mutation, but it means the dangerous implementation remains present and reachable in the stock package.

## Findings by audit question

### 1. Files required after `all_keys.json` exists

The smallest reusable data/query subset is:

| Responsibility | Required upstream modules |
| --- | --- |
| Configuration and keys loading | `core/config.py`, `core/context.py`, `core/key_utils.py` |
| Page/WAL decryption and cache | `core/crypto.py`, `core/db_cache.py` |
| Contact, chat-room, and sender resolution | `core/contacts.py` |
| Message shard discovery, query, and content decoding | `core/messages.py` |
| Session/unread queries | logic from `commands/sessions.py` and `commands/unread.py` |
| Incremental state, if retained | redesigned logic based on `commands/new_messages.py` |
| Group members and statistics | logic from `commands/members.py` and `commands/stats.py` |
| CLI-only formatting | `output/formatter.py`; not needed by a typed adapter |

Python dependencies are Python 3.10+, PyCryptodome, and zstandard. Click is needed only for the CLI shell, not for the core. These declarations are in [`pyproject.toml`](https://github.com/huohuoer/wechat-cli/blob/a3789232d4f79bf0b30634d9dadbce71e4acd601/pyproject.toml).

The following are not required after keys exist and must not be included in the safe product: `commands/init.py`, all of `keys`, bundled `bin` scanners, and the stock `main.py` command registry. `export.py`, favorites, media-file lookup, and contact-detail output are also unnecessary for the requested adapter API.

### 2. Can normal queries run without the live WeChat process?

Yes, provided that:

- an existing valid keys file is supplied;
- the configured encrypted database files remain available; and
- the current schema and encryption parameters are compatible.

The query flow reads files and uses SQLite after decryption. It does not locate or attach to a running WeChat process. WeChat need not be running, although querying while it is running introduces the consistency issue in question 9.

### 3. Do normal query paths invoke prohibited process or app mutation APIs?

No normal query function inspected calls `sudo`, `codesign`, `task_for_pid`, a memory API, or a WeChat mutation API. Those operations are in initialization/key extraction.

There are two qualifications:

- The stock CLI imports the `init` command, so prohibited code is packaged and reachable even when a query command is selected.
- The normal query path does write plaintext cache files and incremental cursor metadata. It does not mutate WeChat or its encrypted databases, but it is not globally side-effect-free.

A safe adapter should make the absence of prohibited capabilities verifiable at the source and package level.

### 4. Encrypted page decryption and caching

[`crypto.py`](https://github.com/huohuoer/wechat-cli/blob/a3789232d4f79bf0b30634d9dadbce71e4acd601/wechat_cli/core/crypto.py) assumes a SQLCipher-4-style layout:

- 4,096-byte pages;
- 32-byte AES key;
- 16-byte first-page salt;
- 80 reserved bytes per page;
- AES-256-CBC page encryption; and
- a 16-byte IV plus a 64-byte HMAC-SHA512 area in the reserve.

For the first page it restores the standard SQLite header, decrypts the encrypted body, and clears reserved bytes. Other pages are decrypted in place by page number. WAL frames are read and decrypted, then their page images are patched into the plaintext database at the corresponding page offsets.

[`db_cache.py`](https://github.com/huohuoer/wechat-cli/blob/a3789232d4f79bf0b30634d9dadbce71e4acd601/wechat_cli/core/db_cache.py) stores decrypted SQLite databases in a persistent process-shared temporary cache. It keys filenames by a truncated MD5 of the relative encrypted path and records database/WAL mtimes in JSON. Matching mtimes reuse the existing plaintext file; otherwise it performs a full decrypt and applies the current WAL.

Security and correctness concerns for production:

- plaintext cache files survive command exit;
- there is no per-account namespace strong enough to avoid all cross-configuration collision risk;
- cache writes and metadata updates are not transactional or coordinated across processes;
- source files may change during decryption; and
- the WAL replay logic is not a substitute for SQLite's own reader locking/snapshot semantics.

### 5. Message shard discovery

[`messages.py`](https://github.com/huohuoer/wechat-cli/blob/a3789232d4f79bf0b30634d9dadbce71e4acd601/wechat_cli/core/messages.py) discovers shards from keys-file entries, not by independently scanning the directory. A normalized key path must begin with `message/` and match `message_<number>.db`. Each chat is expected in a table named `Msg_<MD5(username)>`.

For a chat query, the code checks every known message shard for that table, reads its maximum `create_time`, and processes matching shards newest first. Consequences:

- a newly created shard absent from `all_keys.json` is invisible;
- a renamed path or changed naming convention is invisible; and
- every chat lookup may touch many shards unless an index is added.

### 6. Message BLOB and content decoding

The message query expects columns including `local_id`, `local_type`, `create_time`, `real_sender_id`, `message_content`, and `WCDB_CT_message_content`.

When `message_content` is bytes and the content marker is `4`, it is zstd-decompressed and decoded as UTF-8 with replacement for invalid sequences. Other byte content is decoded directly as UTF-8 with replacement. `local_type` can contain a base type in the low 32 bits and subtype in the high 32 bits.

XML app-message payloads are parsed with the standard XML parser after rejecting document type/entity declarations and applying a size bound. The code extracts summaries for links, files, mini-programs, quoted messages, and calls. Group messages can also be interpreted from a `sender:\ncontent` prefix.

The current SQL search performs `LIKE` against stored `message_content` before Python-side decompression. It can therefore miss compressed BLOB content. A safe adapter must define search completeness explicitly and test it against supported schemas.

### 7. Group sender-name resolution

Resolution has two stages:

1. `real_sender_id` is mapped through the shard's `Name2Id(rowid, user_name)` table.
2. The resulting username is mapped through contact/chat-room data, preferring remark, then nickname, then username.

For group rows where `Name2Id` is insufficient, the code falls back to the sender prefix embedded in message content. Group metadata comes from `contact`, `chat_room`, and `chatroom_member`; the inferred local account is displayed as “me.”

This is recoverable for the reviewed schema but not guaranteed for every message type: missing `Name2Id` rows, stale contacts, system messages, or schema changes can leave only an internal identifier.

### 8. `new-messages` cursor and state

[`new_messages.py`](https://github.com/huohuoer/wechat-cli/blob/a3789232d4f79bf0b30634d9dadbce71e4acd601/wechat_cli/commands/new_messages.py) stores a JSON dictionary from session username to the latest session timestamp in a fixed state file. On first use it records all current timestamps and returns unread session summaries. On later calls it returns sessions whose latest timestamp increased, then replaces the saved timestamp map.

This is not a complete message cursor:

- it returns at most a latest session summary, not every new message;
- multiple messages with the same or intermediate timestamps can be lost;
- state is not scoped to account or configuration;
- the state write is not atomic or locked; and
- advancing the timestamp can make omissions permanent.

The safe adapter should use an account-scoped, per-shard cursor such as `(create_time, local_id, shard_id)`, with deterministic tie-breaking, and update it only after results are successfully delivered/persisted by the caller.

### 9. Concurrent WeChat writes

The encrypted database and WAL are inspected as ordinary files without acquiring a SQLite read transaction on the encrypted source. The database is decrypted first and the WAL is read/applied separately. If WeChat appends, checkpoints, replaces, or truncates the WAL during those steps, the output may combine states from different moments.

The code uses mtimes as a cache freshness heuristic, but it does not establish an atomic snapshot, lock source files, verify that pre/post file identity and size stayed stable, or atomically publish a fully validated cache entry. Querying while WeChat writes can therefore yield stale data, transient failures, or an inconsistent plaintext image.

A safe implementation should:

- stage database and WAL bytes into an app-private location without mutating the sources;
- compare source identity, size, and high-resolution timestamps before and after staging;
- retry a bounded number of times if they changed;
- decrypt into a new temporary output, validate the SQLite header/schema and run a bounded integrity check; and
- atomically publish the validated cache generation.

This reduces races but does not create a perfect cross-file snapshot. If supported-WeChat testing shows that retries cannot reliably obtain a stable pair, production use while WeChat is writing should be declared unsupported rather than silently returning uncertain results.

### 10. Multi-account and data-directory selection

[`config.py`](https://github.com/huohuoer/wechat-cli/blob/a3789232d4f79bf0b30634d9dadbce71e4acd601/wechat_cli/core/config.py) searches known platform locations. On macOS it looks below the WeChat container for account directories containing `db_storage`. If multiple directories exist, initialization prompts on a TTY; noninteractive selection can fall back to the first candidate.

The persisted configuration represents one selected account/data directory and one keys file. Query commands do not expose an account selector, and `new-messages` state is global. Thus upstream does not provide robust simultaneous multi-account operation.

The safe adapter should represent each account explicitly as `(opaque account ID, approved data root, keys file, cache namespace, cursor namespace)`. It must never silently choose the first account. UI-visible account labels should come from user assignment or an opaque local identifier, not private contact data.

### 11. WeChat-version and schema assumptions

The upstream README advertises compatibility with WeChat for Mac up to 4.1.8.100 and warns that newer versions may not work. The query layer additionally assumes:

- the SQLCipher page format and constants listed in question 4;
- database key paths such as `contact/contact.db`, `session/session.db`, and `message/message_<n>.db`;
- `SessionTable` and its current columns;
- `contact`, `chat_room`, and `chatroom_member` and their current columns;
- `Msg_<MD5(username)>`, its message columns, and `Name2Id`;
- current zstd content markers, packed message types, XML layouts, and timestamp units.

Some of the published compatibility limit may be scanner-specific, but the code has no schema-version negotiation or migration layer, so the data core must still be treated as version-coupled. A production adapter needs a read-only capability probe that reports recognized database format/schema before any content query.

### 12. Reusing `all_keys.json` after a WeChat upgrade

An app-binary upgrade alone does not invalidate a database key. Existing entries should continue to work for the same encrypted database files if their key, salt/layout, path mapping, and schema remain compatible.

That conclusion is conditional:

- database migration or key rotation invalidates affected entries;
- newly created shards require new key entries and will otherwise be undiscoverable;
- moved/renamed databases may no longer match keys-file paths; and
- schema or encoding changes can break queries even when decryption still succeeds.

Therefore “the key file still decrypts one old page” is not enough. Compatibility should be reported per database entry and per schema capability, without attempting extraction when coverage is missing.

### 13. Apache-2.0 reuse and notices

At the audited commit, `wechat-cli` contains an Apache License 2.0 [`LICENSE`](https://github.com/huohuoer/wechat-cli/blob/a3789232d4f79bf0b30634d9dadbce71e4acd601/LICENSE) and no repository `NOTICE` file was found. Subject to provenance and legal review, Apache-2.0 generally allows reuse and modification while requiring distribution of the license, preservation of applicable copyright/patent/trademark/attribution notices, prominent notices on modified upstream files, and reproduction of a `NOTICE` if one exists. Dependency licenses for PyCryptodome, zstandard, Click, or native replacements must be audited separately.

There is a material unresolved provenance issue. The upstream [`README`](https://github.com/huohuoer/wechat-cli/blob/a3789232d4f79bf0b30634d9dadbce71e4acd601/README.md#acknowledgements) says the project is built on `ylytdeng/wechat-decrypt` for core database decryption and parsing. That source and its license could not be independently reviewed because the repository is unavailable. GitHub has published a [DMCA takedown notice concerning that repository and its fork network](https://github.com/github/dmca/blob/master/2026/07/2026-07-13-wechat-3.md). A notice contains allegations, not a judicial finding, but it creates a significant distribution and provenance risk.

Required release gates:

- obtain legal review for anti-circumvention, contract/terms, privacy, and jurisdiction-specific issues;
- establish the provenance and license of every reused code fragment;
- do not assume the top-level Apache license cures an incompatible or unlicensed upstream contribution;
- include a third-party notices inventory and Apache-2.0 license if any audited code is distributed; and
- keep prohibited key-extraction/re-signing code and binaries out of source, build inputs, and artifacts.

### 14. Backend strategy recommendation

| Option | Advantages | Disadvantages | Recommendation |
| --- | --- | --- | --- |
| A. Invoke stock `wechat-cli` | Fastest experiment; command behavior already exists; JSON output | Ships/imports reachable `init` and scanner code; Python/runtime packaging; subprocess cancellation and error handling; plaintext cache; coarse cursor; schema errors exposed as CLI behavior; harder sandboxing and App Store review | Reject for production and do not invoke the unmodified package |
| B. Vendor a stripped read-only Python core | Fastest way to preserve proven parsing behavior; prohibited packages can be physically removed; easiest short-term differential testing | Still ships Python and crypto dependencies; provenance/license issue; bridge complexity in a native app; same cache/concurrency/schema debt unless rewritten | Possible disposable, local feasibility harness after legal approval; do not make it the product architecture |
| C. Reimplement the read-only core natively | Smallest trusted capability set; typed Swift API; native cancellation/concurrency; explicit sandbox paths; controllable cache and cursor security; no scanner code or Python runtime | Highest initial effort; crypto/WAL correctness burden; ongoing schema compatibility testing; clean-room/provenance discipline needed | Recommended production direction, contingent on legal review and fixture-based validation |

Native reimplementation does not by itself settle legal or terms-of-service questions. It does, however, make the safety boundary enforceable and avoids distributing known process-memory and re-signing code.

## Safe design: `WeChatDataAdapter`

### Contract

`WeChatDataAdapter` accepts only user-approved existing inputs:

- one or more explicit WeChat data-directory roots; and
- an existing keys file for each account.

It never discovers keys, opens a process, inspects memory, invokes a command, modifies an app bundle, or writes to encrypted source databases. Missing or stale keys produce a typed “key unavailable/incompatible” result with no extraction fallback.

### API surface

| API | Read-only behavior |
| --- | --- |
| `listAccounts` | Enumerate validated account roots and return opaque account IDs plus capability state; never silently select one |
| `listSessions` | Read `SessionTable`, returning session IDs, timestamps, unread counts, and summaries through the caller's privacy boundary |
| `recentMessages` | Query a resolved chat across known shards using deterministic `(create_time, local_id, shard_id)` ordering |
| `messagesSince` | Return all supported messages after an account/chat-scoped cursor; do not mutate cursor state internally before caller acknowledgement |
| `searchMessages` | Search decoded content with documented bounds; do not rely solely on SQL `LIKE` for compressed BLOBs |
| `unreadSessions` | Read sessions whose unread count is positive; never mark read or activate WeChat |
| `groupMembers` | Resolve chat-room membership through contact tables; return unresolved IDs explicitly when names are unavailable |
| `stats` | Aggregate supported messages over an explicit account/chat/time range without exporting raw transcripts |

### Required invariants

1. **Compile/package-time exclusion:** no `init`, scanner, memory API, app activation, input synthesis, signing, or database-write implementation is present.
2. **Explicit paths:** no implicit first-account selection. Canonicalized database paths must remain under the approved account root; keys-file relative paths must reject traversal and unsafe links.
3. **Strict key validation:** accept only the documented metadata plus relative database mappings and exact-length hexadecimal keys. Never log keys or copy them into diagnostics.
4. **Read-only sources:** open encrypted databases and WALs read-only. All writes are confined to app-owned cache/state directories.
5. **Private cache:** use per-account generations, owner-only permissions, backup exclusion, atomic publication, bounded retention, and explicit purge. Cache filenames and errors must not disclose account names or source paths.
6. **Stable-read validation:** use pre/post source metadata checks, bounded retries, and SQLite/schema validation before exposing a cache generation.
7. **Account-scoped state:** cursors include account, chat, shard, timestamp, and local ID. Persist atomically only after caller acknowledgement.
8. **Schema capability probe:** identify compatible databases/tables/columns and return unsupported-version errors rather than guessing.
9. **Privacy-safe observability:** logs contain operation IDs, counts, durations, and redacted error categories only—never keys, paths, account IDs from WeChat, contact names, chat names, or message content.
10. **No export endpoint:** transcript export is outside this adapter. Any future export must be a separate, explicit user action with its own privacy review.

### Suggested internal flow

1. Validate the approved account root and keys file without scanning unrelated directories.
2. Build a per-account manifest of known encrypted databases from keys-file mappings.
3. Probe format/schema compatibility without returning content.
4. Materialize a validated, private plaintext cache generation from stable staged source bytes.
5. Open cached SQLite files read-only and execute parameterized queries.
6. Decode message content and resolve senders in memory.
7. Return typed results; keep persistence and presentation outside the core query engine.

### Verification gates before implementation is considered production-ready

- Static build check proving prohibited symbols, scanner binaries, `codesign`, and process-memory APIs are absent.
- Synthetic/consented fixtures for every supported schema and message encoding; no real private content in the repository.
- Tests for key/path validation, compressed-content search, same-timestamp pagination, shard rollover, account isolation, concurrent cache readers, interrupted cache writes, and WAL churn.
- Differential tests against known fixtures only, never against committed real WeChat content.
- License/provenance and legal approval before copying or translating upstream implementation details.

## Final recommendation

Proceed only with a narrowly scoped native `WeChatDataAdapter` feasibility design after legal/provenance review. Treat the upstream Python code as evidence that post-key query access is technically separable from process-memory extraction, not as a production dependency. Do not ship or invoke the stock CLI, and do not include its `init`, `keys`, or `bin` trees in any v2 target.
