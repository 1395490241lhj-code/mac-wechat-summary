# DB Reader — typed bounded query surface (sub-gate)

## Scope

One internal Reader contract, synthetic fixtures only, one sub-gate. It is not
P4 complete, not a Database Mode promotion, and not product wiring.

Baseline commit: `ad1f2ab777740f8d17359de7431b721606ad8607` (`v2/rewrite`),
after the role-inventory sub-gate at
`docs/v2/DB_READER_P4_INVENTORY_COMPATIBILITY.md`, the schema-drift sub-gate at
`docs/v2/DB_READER_P4_SCHEMA_DRIFT_ACCOUNTING.md` and the native FTS spike at
`docs/v2/DB_READER_NATIVE_FTS_SPIKE.md`.

No real WeChat process, container, database, `message_fts`, media, session or
contact database, account passphrase, credential, Keychain record, LLDB, Frida,
process-memory capture, re-signing or clone was touched. No UI, Agents, MCP tool
or Search product wiring was changed. No source default was changed. Every
database in the tests is synthetic and built inside the test file under pytest's
`tmp_path`.

> no arbitrary SQL · no live source · no product wiring

## The gap this closes

The reader had accumulated conversation listing, per-conversation messages,
recent messages, inventory and compatibility diagnostics, an optional native FTS
candidate index, an identity catalog and `ReadCoverage`. Nothing stopped a
caller from composing provider methods freely, interpreting a limit or a cursor
itself, deciding whether diagnostics should affect a result, or reaching for
SQL. This capsule adds the boundary those callers should use instead: a closed
operation vocabulary, typed requests, a hard bound on every collection, one
result envelope, and a fixed refusal token for everything else.

## Operation vocabulary

Closed and total in `wechatdb/provider/query.py`. Four names; a caller picks one
and fills in the one request class that operation accepts.

| Operation | Request | Bounds | Served |
|---|---|---|---|
| `list_conversations` | `ListConversationsQuery(limit)` | `limit` in `1..500` | yes |
| `conversation_messages` | `ConversationMessagesQuery(conversation_id, limit, cursor=None)` | `limit` in `1..500`; `conversation_id` a positive int; `cursor` bound to source + conversation | yes |
| `recent_messages` | `RecentMessagesQuery(since_observed_at, limit)` | `limit` in `1..500`; `since_observed_at` a finite real | yes |
| `native_search` | — | — | declared, refused `unsupported_capability` |

`native_search` is named but not wired, because the FTS spike is deliberately
not product-wired. There is deliberately **no** exact-message-get: the identity
contract that would make one sound is not sealed, and an unsealed identity is not
something to expose at a boundary.

## No arbitrary reader power

No operation accepts SQL, a table name, a column, a WHERE clause, an ORDER BY,
an expression or a filesystem path. A request object is a frozen dataclass, so
an unrecognised field raises at construction and cannot be ignored, and the
dispatcher matches the request by **exact type** — a subclass cannot widen the
accepted surface. A field belonging to another operation has nowhere to go.

## Bounds, and refused rather than clamped

`MAX_READ_LIMIT = 500` is one definition that `provider.py` imports, so a
consumer that bypasses this module and calls the provider directly finds no
wider door. Zero, negative, `True`, `"5"`, a float, `501` and `10 ** 400` are
all refused. Refused, not clamped: a caller is never handed fewer results than
it asked for while believing it asked for more. The provider keeps raising
`ValueError`, because that is what its own already-sealed callers handle; the
surface turns the same condition into a typed refusal.

## Result envelope

`QueryResult` is tagged, and the tag is enforced at construction:

| Kind | Carries | Never carries |
|---|---|---|
| `items` | the provider's own `items` and `ReadCoverage`, forwarded unchanged; a `continuation` for the conversation operation | a `state` |
| `refusal` | one `state` from the closed set and one fixed `detail` | items, coverage, or a continuation |

So every query ends in exactly one explicit outcome. There is no shape in which
a caller holds items beside a hidden error, and none in which a refusal could be
read as an empty answer.

## Ownership: nothing below is recomputed

| Fact | Owner | What this layer does |
|---|---|---|
| Coverage | `ProviderResult.collapse`, unchanged | forwards `result.coverage` untouched; reads no coverage field except `truncated`, and only to decide whether to offer a continuation |
| Compatibility | `assess_schema` / `SchemaCompatibilityReport`, unchanged | imports only the `UnsupportedGeneration` **exception**; no classifier, no outcome vocabulary, no gap mapping |
| Message identity | `conversation_identity` / `message_identity` | mints no second identifier and unwraps none |
| Discovery | `ShardDiscovery` | holds no locator, lists no directory, opens nothing |

The surface therefore cannot be a second coverage implementation: it has no way
to construct a `ReadCoverage` at all, which a test enforces by checking that no
sensitive import is ever *called* — the surface legitimately names the type for
its annotation, so the import is not the offence, calling the bound name is.

## Cursor and scope

`before_sequence` is the provider's existing authoritative pagination value and
is forwarded as given. No second cursor format and no raw row identifier is
invented, and nothing is signed — the binding catches accidental cross-scope
reuse, not a deliberately forged one, which is what its own docstring says.

`ConversationCursor` names the source and conversation it came from. A cursor
whose source or conversation is not the one being read is refused, because a
bare sequence number cannot prove where it belongs and silently narrowing an
unrelated conversation is a widening by accident. Operation binding is
structural: only `ConversationMessagesQuery` has a cursor field, so a cursor
cannot reach a listing or a recent read at all.

A page is the newest N in **ascending** order, so the continuation is the
**oldest** item in the page and the next page is everything strictly older. It is
offered only when the provider itself reports `coverage.truncated`, so a caller
is never handed a cursor that would continue nothing. A listing is a
newest-first snapshot rather than a page of a traversal, so it offers no
continuation.

## Error vocabulary

Six fixed, content-free tokens. No token is assembled from the value that was
refused, and each `detail` is one fixed sentence.

| Token | Meaning |
|---|---|
| `invalid_query` | not one of the four operation names |
| `invalid_argument` | a real operation with an out-of-range, wrongly typed, cross-scope or wrong-class argument |
| `source_unavailable` | a SQLite failure, a part that will not open, or no provider at all |
| `unsupported_generation` | a changed generation, refused as unverified and publishing nothing |
| `unsupported_capability` | declared and deliberately unwired — never a substitute for "nothing to find" |
| `internal_error` | a failure this layer could not classify; a token, not a message |

No SQLite message, path, table name, schema, chat content, sender or exception
repr can reach a caller: each failure becomes a token and the original survives
only as a suppressed cause. `ERROR_DETAILS` is a `MappingProxyType`, so the
exported surface cannot be edited to inject content into a later refusal.

## Search is declared and refused

The refusal happens before request validation, so no FTS candidate row can be
returned by any route, and no FTS internal coordinate reaches a caller. A
refused search and a genuinely empty read are structurally different answers:
the refusal carries no coverage, the empty read carries `observed_complete` over
an empty window. An absent index can never read as an empty conversation.

## Independent review and the corrective pass

A fresh read-only review of the first green state raised two Important findings,
four Minors and one non-finding. Both Important findings were real and both are
fixed, RED-first. The reviewer could not execute pytest — it is not installed for
any interpreter on the machine — and verified everything by driving the
production code directly; its static count of 58 test functions / 172 collected
cases matched the suite exactly.

**Finding 1 — a caller's bad window bound was reported as an internal fault.**
`_window_start` called `math.isfinite(value)` on a value only checked with
`isinstance(..., (int, float))`. `math.isfinite` raises `OverflowError` on an
unbounded `int` such as `10 ** 400` rather than returning `False`; the raise
escaped the validator, was swallowed by the blanket handler, and told the caller
`internal_error` — a different claim about whose fault the read was. The suite's
window-bound parametrization listed every float-shaped hostile value and no large
`int`, which is why it survived. RED, before the fix:

```text
test_a_window_bound_that_is_not_a_finite_number_is_refused[10**400]   failed
test_a_window_bound_that_is_not_a_finite_number_is_refused[-10**400]  failed
2 failed, 172 passed
AssertionError: ('internal_error', 'This query could not be completed.')
```

Fixed by bounding the conversion and refusing a value that cannot be a moment at
all. The first attempt compared against the float infinities, which does not
raise but *accepts* `10 ** 400` as an instant matching nothing — a read
indistinguishable from a genuinely empty one, the exact failure the docstring
names for infinity. The committed version converts and refuses on
`OverflowError`.

**Finding 2 — three AST guards asserted more than they checked.** All three
asserted structural absence **by name**, so an alias defeated them; the
filesystem guard exempted the entire body of every `ExceptHandler`, which is
where `run()` handles SQLite error text; and the coverage guard's forbidden set
named fields the module never reads while missing the two it does. Mutation
probes the reviewer ran against the old sets:

```text
'from .compatibility import assess_schema as _a'              EVADED
'from message_source import ReadCoverage as _C; return _C()'  EVADED
'return r.coverage.status == ms.OBSERVED_COMPLETE'            EVADED
'return len(r.items) == r.coverage.item_count'                caught
```

Fixed by checking what an import *brings in* rather than what it is named, and
by exempting only the `ImportError` path-resolution fallback instead of every
handler body. `open` was added to the forbidden calls — it had never been in the
set, so a handler that opened a path passed. Re-probing the hardened guards:

```text
alias-classifier    caught by the schema guard
alias-coverage      caught by the coverage guard
status-equality     caught by the coverage guard
count-inference     caught by the coverage guard
path-in-handler     caught by the filesystem guard
scandir-in-handler  caught by the filesystem guard
```

Two holes in the corrected guards were found by re-probing rather than assumed
away: the compatibility import check missed a relative `from .compatibility`
because the module name has no leading dot, and the first coverage fix flagged
the production module's own legitimate `ReadCoverage` annotation import. The
final rule tracks what each sensitive import binds, alias included, and fails on
the **call**.

**Minors, all fixed.** The continuation variable was named `newest` while holding
the *oldest* item of an ascending page — correct behaviour, but a name that
invites a future editor to "fix" it into a real bug; renamed with the ordering
spelled out. `ERROR_DETAILS` was an exported mutable `dict`, so any consumer
could rewrite a detail and have every later refusal carry it; now a
`MappingProxyType`. One assertion compared an error token against a coverage
reason — two disjoint vocabularies, so it could not fail for any input; replaced
with the structural distinction it was reaching for. A `str` subclass with a
raising `__hash__` escaped `run()` from the operation lookup, which sits *in
front of* the handler; the lookup moved inside the guard.

**Recorded, not changed.** The auxiliary-incompatibility test is honest in its
own docstring about being a no-op by construction: the optional part is
`SHARD_UNKNOWN` on both branches and already contributes the same inventory gap,
so it proves a no-op is a no-op. It is not evidence for the property its name
claims, and the coverage-neutrality claim rests on the committed schema-drift
gate rather than on it. `ProviderResult.collapse` still validates only
`caller_limit > 0` and accepts `10 ** 9`; this is pre-existing, is not an
unbounded *read* path, and its sole non-test caller sits behind `_bounded`.
`BaseException` (`KeyboardInterrupt`, `SystemExit`) still propagates, which is
deliberate: those are not read failures.

The review also confirmed all ten contract points satisfied, and specifically
that the suite transcribes the vocabulary and the `500` bound rather than
importing them, so the tests cannot agree with the implementation by
construction.

## Final counts

Run with the external interpreter that has `pycryptodome` and the MCP
requirements installed. No repository dependency file was changed.

| Suite | Result |
|---|---|
| `wechatdb/tests/provider/test_typed_query_surface.py` | 174 passed |
| `wechatdb/tests` + `acquisition/tests` + `bridge/tests` | 957 passed |
| baseline at `ad1f2ab` (same suites, no new file) | 783 passed |
| `git diff --check` | clean |

The baseline was measured in a throwaway worktree at the same commit, so the
difference is exactly the new contract suite.

## Not proven

- No real source was read. Every claim here is a synthetic behavioural contract.
- Real / complete-container compatibility is **unproven**; the standing blocker
  `database-production-path-no-retained-access-material` is unchanged.
- `native_search` is refused, so no search behaviour of any kind is proven.
- The cursor binding catches accidental cross-scope reuse, not a forged cursor.
- Nothing here is product-wired: no MCP tool, no Swift surface, no Search, no
  Archive, no Memory, no source default.
- `MAX_READ_LIMIT = 500` is a contract choice, not a measurement of any real
  source's size.

## This does not complete P4

> Typed bounded query surface sub-gate PASS; P4 remains incomplete.

Remaining P4 surface, in rough dependency order:

1. complete-container compatibility (the assembled container, not one shard)
2. a decoded-search fallback for the search capability
3. lazy media compatibility

Each is a separate capsule and none is started here.
