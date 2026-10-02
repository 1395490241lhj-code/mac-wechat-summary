# DB Reader — native FTS compatibility / query spike (synthetic sub-gate)

## Scope

Synthetic-only compatibility and query sub-gate for one question: can a native
`message_fts[_<n>].db` be used as an **optional candidate index** above the
ordinary message shards, without ever becoming a second message truth,
identity, or coverage system?

Baseline commit: `a5bbd0d01ba708ba88711a3130488c459b6a1a42` (`v2/rewrite`,
`feat(v2): classify database inventory roles safely`), which is where
`message_fts[_<n>].db` was first classified as the `search_index` role in
`acquisition/database_inventory.py` (P4, see
`docs/v2/DB_READER_P4_INVENTORY_COMPATIBILITY.md`).

No real WeChat process, container, database, `message_fts.db`, account
passphrase, keychain item, LLDB, Frida, process memory, re-signing, UI, Agents,
MCP product tool, Memory FTS, Archive Search, Database Mode, source default,
polling, or network was touched. Nothing was wired into the product.

Verdict: **synthetic compatibility spike / sub-gate PASS.** Not product-wired.
Not "Native FTS PRODUCT MET".

## Architecture

```
bounded one-term query
  -> native FTS (a supplied, read-only entry)
  -> candidate coordinates (part name, conversation table, local_id)
  -> ordinary read-only re-read of that exact part + table
  -> existing parser / support envelope -> ProviderResult.message
  -> dedup by the provider's own canonical message id
  -> bounded NativeSearchResult (capability, messages, truncated, diagnostics)
```

The index never reaches the answer. It nominates a row; the ordinary shard
decides whether the row exists and what it says.

## Truth ownership

Ordinary message shards own message identity, content, sender, chronology,
coverage and completeness. The native index owns none of them, and the module
has no field through which it could claim any: `NativeSearchResult` is a
separate type with no `ReadCoverage`, and `NativeSearchDiagnostics` has no
coverage field (`test_a_search_result_carries_no_message_coverage`). Absent,
unsupported, malformed, query-failed and empty are five distinct outcomes and
none of them says a message does not exist.

## Schema decision

The recognised source is this spike's own design, derived from the behavioural
question -- what is the least an index must expose so a hit maps back to an
authoritative message -- not from any external reader's SQL, schema, or
identifier scheme. A compatible source is an FTS5 virtual content table
carrying all three coordinates (`shard_name`, `conversation_table`,
`local_id`); missing any one of them is `unsupported`, never a guess. The
synthetic fixtures in `wechatdb/tests/provider/fixtures_fts.py` encode the
compatible, unsupported, corrupt and malformed shapes separately.

The real WeChat native FTS schema is **unproven**. This spike deliberately does
not guess at it. `conversation_table` is spelled as the parser's own `Msg_<32hex>`
form, which is the only conversation spelling the ordinary reader parses; an
index that addresses conversations some other way is `unsupported` here, not a
future fuzzer.

## RED evidence

1. With `native_search.py` withheld, collection failed outright:
   `ModuleNotFoundError: No module named 'wechatdb.provider.native_search'`.
2. Independent read-only review, first pass: an ordinary part that could not be
   opened escaped `search()` with `sqlite3.OperationalError: unable to open
   database file` -- SQLite's own path-bearing text at a public boundary.
   RED: `2 failed, 55 deselected`.
3. Independent read-only review, second pass: the native path parsed with
   `session_names` only, so a resolvable sender came back as `wxid_fixture_alpha`
   while the ordinary provider returned `Fixture Alpha` -- a second, weaker
   projection of one authoritative row. RED: `1 failed, 57 deselected`.
4. Self-review, third pass: `CAST("local_id" AS INTEGER)` turned a coordinate
   the index could not state into `0`, which is a real row's id, and that row was
   returned. RED: `1 failed, 58 deselected`.

All three were fixed inside `_read` / the candidate SQL (guard the open, reuse
the resolver's `room=` path, read `local_id` as stored) and re-verified by
targeted rerun.

## Final evidence

| Suite | Result |
|---|---|
| `wechatdb/tests/provider/test_native_fts.py` | 59 passed |
| `wechatdb/tests` | 282 passed |
| `acquisition/tests` | 173 passed |
| `bridge/tests` | 270 passed |
| `memory/tests` | 438 passed |
| `shadow/tests` | 158 passed |
| `git diff --check` | clean |

The 30+ contract cases cover the required classes: capability (absent /
compatible / unsupported / corrupt / malformed), candidate-to-authoritative
resolution (valid hit, orphan, stale, wrong part, wrong table, unreadable
coordinate, part that will not open, part that is not a database, unsupported
generation), identity (provider-owned id, rowid never becomes an id, no invented
conversation, display-name parity with the ordinary path), coverage (no
coverage on the search result, five index states leave ordinary coverage
identical, orphan candidates move it in neither direction), boundaries (one
bounded term, bounded limit, measured truncation, every statement a literal with
bound parameters read from the module's own syntax tree, no `sqlite3.connect`,
no directory listing, no product-layer import), and ordering (deterministic,
newest-`limit`).

## Not done

- Real WeChat native FTS schema compatibility: **not proven**.
- No live query against any real database, index, or container.
- No product Search: no SearchView, no MCP tool, no Archive Search, no Memory
  FTS, no Agents wiring, no source default change, no Database Mode promotion.
- No fallback decoded search. An index that is unavailable yields
  `capability=absent` and nothing else; no enumeration-and-`contains` path was
  added.
- No media compatibility, no credential handling, no real-data opt-in test run,
  no canonical-store read.

## Next

Real-schema compatibility remains the open question this spike deliberately
left open, and it is answerable only from public provenance, not by opening an
operator database. See `Next Actions.md`.
