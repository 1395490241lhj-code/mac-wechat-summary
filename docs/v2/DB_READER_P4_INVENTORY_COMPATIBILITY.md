# DB Reader P4 — database-role inventory compatibility (sub-gate)

## Scope

P4 compatibility groundwork only, synthetic fixtures only, one sub-gate. It is
not P4 complete and it is not a Database Mode promotion.

Baseline commit: `ad4a6e40c93e0b4ff2e107686b3568650aad64c1` (`v2/rewrite`), after
the GreenBubbles assimilation audit at `docs/v2/GREENBUBBLES_ASSIMILATION_AUDIT.md`.

No real WeChat process, container, database, account passphrase, credential
file, protected store, LLDB, Frida, process-memory capture, re-signing,
injection, UI or Agents surface was touched. No product source selection,
polling or background refresh was changed. The work is authorized by the
audit's *Recommended next capsule* section.

## Why it exists

Both the bounded refresher and the bootstrap candidate builder enumerated every
direct `*.db` child of the already-selected `message/` directory and bound each
one to the message-role key descriptor. `bridge/acquired_database_source.py`
then relabels handles as `message_{i}.db`, so the mistake could not be caught
downstream by provider name classification.

A synthetic probe of the pre-change refresher on an 11-child directory
produced 9 message-role sources that should never have been message shards: ordinary
shards plus `biz_message_0.db`, `message_fts.db`, `media.db`, an arbitrary
`zzz_unknown.db`, and a symlink.

## Role vocabulary

Closed and total, in `acquisition/database_inventory.py`:

| Role | Shape | Message source? | Gap |
|---|---|---|---|
| `ordinary_message` | `message_<n>.db` | yes | — |
| `business_message` | `biz_message_<n>.db` | no | `business_message_unread` |
| `search_index` | `message_fts[_<n>].db` | no | — |
| `media` | `media[_<n>].db` | no | — |
| `auxiliary` | `session.db`, `contact.db`, `hardlink_<n>.db`, `chatbot.db`, `sns.db` | no | — |
| `unknown` | any other visible `.db` child | no | `unknown_database` |
| `unsupported_message_candidate` | message-shaped name that is not the documented shard shape, e.g. `message_shard.db`, `biz_message_x.db` | no | `unsupported_message_candidate` |

One fixed, content-free rejection reason: `not_a_regular_file`, for a symlink,
directory or other non-regular child matching `*.db`. A refused child is still
accounted for: its name is classified on the same vocabulary, so a refused
`message_<n>.db` becomes `unsupported_message_candidate` rather than an ordinary
shard, a refused unknown name becomes `unknown_database`, and a refused media or
auxiliary name raises nothing.

Classification is **structural by necessity**, not an oversight. A source is
still encrypted at inventory time, so a name shape is the only evidence
available. No role is ever inferred from anything the module cannot see, and
no file is opened.

## Invariants

1. **Total accounting.** Every direct child of the selected directory lands in
   exactly one role, or is explicitly unknown, explicitly an unsupported message
   candidate, or rejected with the one fixed reason. Nothing is silently
   ignored, and a directory that does not exist yields an empty inventory rather
   than an error.
2. **Only `ordinary_message` is ever a message source.** Both discovery
   entry points — `BoundedSourceRefresher.refresh` and
   `bridge.database_bootstrap.candidate_source_set` — now bind message parts
   from `DatabaseInventory.message_shards` only.
3. **Auxiliary presence never strengthens message coverage.** `search_index`,
   `media` and `auxiliary` raise no gap and never become a shard. They stop
   looking anomalous; they contribute nothing to what a read can claim.
4. **Unknown and unsupported candidates stay visible.** Neither is parsed as a
   shard, neither disappears from accounting, and both appear as explicit gaps.
5. **The discovery boundary does not widen.** One direct directory listing, no
   recursion, no search above or beside the selected directory, no account
   choice, no newest-account rule, no `$HOME` scan. Hidden children and non-`.db`
   children (including `-wal` / `-shm` sidecars) are not candidates.
6. **Split-directory source sets are left alone.** The refresher inspects
   neither end when recorded sources span two parents, and reports an empty
   inventory rather than choosing one.
7. **Determinism.** Entries and rejections are name-ordered, so the result
   depends only on which children exist, never on filesystem listing order.

## The coverage consequence, and two review findings

Excluding a database is only honest if the read says so. The provider can only
report a gap for a part it was given, and an excluded part is invisible to it, so
an inventory gap had to be carried across the boundary explicitly.

A first pass wired that in too broadly, and the independent review caught it:
**RED-first**. With two messages in one shard and a caller limit of one, the
unfixed bridge replaced the read's `caller_limit` / `truncated=True` with
`partial_inventory` / `truncated=False`. That discarded evidence the caller
needs: a read cut short by the caller stayed partial, but stopped saying so.

The correction makes the inventory a ceiling on a *complete* claim only. A read
already partial for a stronger reason -- a measured caller cut, a source limit,
an unsafe stop -- keeps that reason and its truncation.

The independent review then found a fail-open hole in the refusal rule itself,
also **RED-first**. A child refused as `not_a_regular_file` produced no gap at
all, so a directory containing a symlinked `message_1.db` let the read report
`observed_complete` over history it had never accounted for -- the exact failure
this sub-gate exists to prevent, reached through the one path that looked like
a safe refusal. Two tests failed (`assert () == ('unsupported_message_candidate',)`
and the same assertion in the boundary test).

The correction maps a refused name through its own shape, with one correction:
a supported-shaped name that was refused is a message *candidate*, never a
shard, because this reader did not and may not open it. A refused media or
auxiliary name has no message-bearing claim to lose and raises nothing.

## RED evidence

`acquisition/tests/test_database_inventory.py` was added before any production
edit. The first run failed at collection:

```
ModuleNotFoundError: No module named 'acquisition.database_inventory'
```

proving the inventory contract did not exist before the production code was
written.

## Final test evidence

```
acquisition/tests/test_database_inventory.py    23 passed
acquisition/tests + wechatdb/tests             396 passed
bridge/tests                                  270 passed
memory/tests                                   438 passed
shadow/tests                                   158 passed
```

The root `tests/` directory is empty (v1 generation) and collects nothing.
`git diff --check` is clean.

All synthetic. No test requiring real data, a real container, or the canonical
operator store was run. No GUI acceptance applies: no UI changed.

## Not proven by this gate

- No live WeChat evidence was gathered. No container, database or encrypted
  source was read. Every classification here is name-shape reasoning over
  synthetic directories.
- No credential source was established. The account-secret blocker is
  unchanged, and the production blocker
  `database-production-path-no-retained-access-material` still stands.
- A refused database is refused *by name shape only*. The inventory never opens
  a file, so it cannot distinguish a symlink that points at a real shard from
  one that points at nothing, and `not_a_regular_file` deliberately carries no
  claim about content. The gap it raises is therefore conservative: a refused
  `message_<n>.db` is reported as an unclaimed message candidate whether or not
  the underlying database is real. That is the fail-honest direction.
- The role list is not a claim about what a specific WeChat build actually
  writes. A database this list does not know surfaces as `unknown_database`,
  which is the intended fail-honest behaviour, not a regression.
- Schema drift accounting, native FTS reading, lazy media resolution and the
  typed bounded query surface remain P4 work this sub-gate does not touch.
- Native FTS and lazy media are **not** product-wired. `search_index` and `media`
  exist only as inventory roles.
- Compatibility state and message coverage are kept separate.
  `DatabaseInventory.gaps` is a compatibility signal about database roles. It is
  not merged into `ReadCoverage`; it reaches the envelope only as a ceiling on
  an otherwise-complete claim, reusing the existing `partial_inventory` reason.
  No second identity or coverage truth was introduced, and no existing coverage
  rule was changed.
- The role list is not a claim about what a specific WeChat build actually
  writes. `hardlink_<n>.db`, `chatbot.db` and `sns.db` are named because this
  project's own `docs/v2/GREENBUBBLES_ASSIMILATION_AUDIT.md` records them as
  stores a comparable reader recognises; no code, SQL, query shape, fixture or
  identifier scheme was copied, and they are recognised only so they stop
  looking anomalous.

## Decisions and boundaries unchanged

- D-005 unchanged. No new credential provider, no `passphrase.txt` discovery,
  no acquisition path.
- D-036 unchanged.
- P0 remains Safe Share. Database remains later P1/P2 and blocked for
  production by `database-production-path-no-retained-access-material`.
- Current WAL-aware snapshot, encrypted WAL replay, ephemeral plaintext
  workspace, `ShardedMessageProvider`, public message identity, sequence and
  cross-shard pagination all unchanged.
- GreenBubbles / wx-cli material was used as behavioural evidence only. No code,
  SQL, query shape, fixture, fixture data or identifier scheme was copied.
