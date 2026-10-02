# DB Reader P4 — within-role schema-drift accounting (sub-gate)

## Scope

P4 compatibility work only, synthetic fixtures only, one sub-gate. It is not
P4 complete and it is not a Database Mode promotion.

Baseline commit: `12a195a2eaf82fe087cabee009e8dac08671580c` (`v2/rewrite`),
after the role-inventory sub-gate at
`docs/v2/DB_READER_P4_INVENTORY_COMPATIBILITY.md` and the native FTS spike at
`docs/v2/DB_READER_NATIVE_FTS_SPIKE.md`.

No real WeChat process, container, database, `message_fts`, media, session or
contact database, account passphrase, credential, Keychain record, LLDB, Frida,
process-memory capture, re-signing or clone was touched. No UI, Agents, MCP or
FTS product wiring was changed. No source default was changed.

Every database in the tests is synthetic and built inside the test file, under
pytest's own `tmp_path`: invented names, invented tables, invented text. The
message-bearing relation treated as drift is `Msg_Archive_0`, a name pattern
invented for this contract. No claim is made about any real generation's table
names.

## The gap this closes

The role inventory settled `filename -> role`. It did not settle
`role -> is the schema inside still the generation this reader understands`.
A recognized role was therefore never evidence of compatibility, but nothing in
the provider said so either, and nothing accounted for a part whose schema had
drifted.

## Three claims kept apart

| Claim | Question | Owner |
|---|---|---|
| Role | What does this name shape earn? | `classify_shard_name` |
| Compatibility | Is the schema inside the generation we understand? | `assess_schema` |
| Coverage | How much did this request observe? | `_collapse`, unchanged |

A recognized name is never a compatible one. `message_7.db` earns
`ordinary_message` and is *then* probed, and the two answers are allowed to
disagree.

## Vocabulary

Closed and total, one outcome per listed entry, in
`wechatdb/provider/compatibility.py`:

| Outcome | Meaning |
|---|---|
| `compatible` | Every conversation table carries the parser's whole read surface, and no message-bearing relation is present that this reader does not cover. |
| `empty` | No conversation table at all, but the shape Discovery already seals as a valid readable empty part. Not a different generation: it is this generation with no conversations in it right now. |
| `unsupported` | A different generation. Either a table lacks a column only this envelope requires, or the part opened and holds nothing this reader reads. |
| `malformed` | A conversation table lacks a column the parser itself refuses on. |
| `incomplete` | Otherwise compatible, but the part also holds a message-bearing relation outside the read surface. |
| `unassessed` | Never structurally read. Accounted for, and deliberately *not* a compatibility claim. |

`unassessed` exists so that total accounting does not require reconstructing a
fact from an absence. It is not compatibility and is not incompatible: a
`biz_message_0.db` or a `media.db` is never opened by Discovery, so it is
honestly `unassessed` rather than guessed.

## Parser parity

One structural truth. `SUPPORTED_CONVERSATION_COLUMNS` is derived as
`frozenset(WANTED_COLUMNS)`, and `WANTED_COLUMNS` is now public in
`wechatdb/parser.py` rather than a private `_WANTED_COLUMNS`. The probe
therefore cannot disagree with the parser about which columns are optional:
there is no second copy to drift.

Two rules, kept apart on purpose:

- absent `MANDATORY_COLUMNS` -> `malformed`, because the parser itself refuses
  on that shape;
- absent any other column the parser reads -> `unsupported`, because the parser
  still reads it and the provider's strict envelope still refuses it.

An *optional* parser column going missing therefore stays `unsupported` and
never becomes `malformed`. `test_provider_compatibility.py` seals the strict
reading (a dropped `source` column is refused); the test here seals the other
half, that the parser's own optional field is not promoted to mandatory.

## Data flow

```text
locator entries
    -> ShardDiscovery.catalogue()   name -> role, outcome := unassessed
    -> ShardDiscovery.probe()       opens message parts, assess_schema()
    -> SchemaCompatibilityReport    roles + outcomes, one per entry
    -> provider._traverse()         inventory_gap = .. or report.required_gap
    -> _collapse()                  unchanged, single coverage truth
```

The probe takes the structural verdict *before* any early return, so a part
Discovery then calls unreadable still carries the verdict its own contents
support. Reading the structure is never what makes a part fail.

`assess_schema` precedence is fixed, so several disagreeing tables still yield
one answer: `malformed` > `unsupported` > `incomplete` > `compatible`, and a
part with no conversation table at all is `empty` when it is a valid empty
message part and `unsupported` otherwise.

## Two definitions that had to become one

`is_valid_empty_message_part` is the one rule for "a message part that is
valid but currently holds nothing". It lives in `compatibility.py` because the
probe needs it, and Discovery imports it rather than keeping its own copy, so
the two layers cannot disagree about the same file. Before this consolidation
they were separate functions and a real part could be readable to one and
unsupported to the other.

## How each case reaches coverage

| Case | Outcome | Reaches coverage? |
|---|---|---|
| Ordinary message shard, compatible | `compatible` | no gap |
| Ordinary message shard, valid but empty | `empty` | no gap |
| Ordinary message shard, unsupported/malformed | `unsupported` / `malformed` | `required_shard_unread` |
| Ordinary shard never opened | `unassessed` | `message_shard_absent` |
| Ordinary shard with extra message relation | `incomplete` | `message_relation_unread` |
| Search index / media / auxiliary, any schema | role only; `unassessed` | **no** |
| Business message | `unassessed` | `business_message_unread`, never absorbed |
| Unknown / unsupported candidate | `unassessed` | existing unknown/candidate gap |

`required_gap` is the only compatibility boolean composed into coverage, and it
is added to the two gaps Discovery already produced. No second completeness
notion is created.

## A conflict with the task brief, and how it was resolved

The brief's case 13 asked that one compatible plus one incompatible ordinary
shard yield partial coverage. Implementing that literally meant skipping the
incompatible shard's parse and publishing the compatible one. Three
pre-existing, sealed bridge tests rejected it:

- `test_a_changed_but_parseable_generation_is_refused_as_unverified`
- `test_a_generation_the_provider_refuses_publishes_nothing`
- `test_one_changed_message_part_is_enough_to_refuse_the_generation`

The sealed boundary is stronger than the brief's wording and is the project's
real safety posture, so it was kept and the bridge tests were **not** weakened:
a changed ordinary generation still raises `UnsupportedGeneration` and
publishes nothing. The accounting is recorded *before* that refusal, so the gap
is still reported. The mixed case therefore refuses the generation rather than
publishing a partial read, and this document records that deviation
deliberately rather than leaving the brief's wording silently unimplemented.

## Independent review and the corrective pass

A read-only review of the first green state raised two Important findings.
Both were real and both are fixed; RED was reproduced before each fix.

**Finding 1 — a valid empty message part was called a different generation.**
`assess_schema` returned `unsupported` for any part with no conversation table,
but committed Discovery already seals the `TimeStamp` +
`wcdb_builtin_compression_record` + `timestamp` shape as readable. The two
layers therefore disagreed about the same file, and a real shard that happened
to be empty lost `observed_complete` permanently. Measured against HEAD:

```text
HEAD:    valid empty part alone -> observed_complete
first:   valid empty part alone -> observed_partial
first:   good shard + valid empty -> observed_partial
```

Fixed by the `empty` outcome. `is_valid_empty_message_part` was consolidated
into `compatibility.py` and Discovery now calls it, so there is one rule rather
than two that can drift.

**Finding 2 — the foreign-relation check misfired on FTS5 companion tables.**
`table.startswith("Msg")` treated an index's internal `type='table'`
companions (`Msg_<digest>_data`, `_idx`, `_docsize`, `_config`) as unread
message relations, so a harmless embedded index cost a shard a completeness
claim that an identically harmless non-`Msg` table did not. Fixed by exempting
those five suffixes by name, which keeps every other `Msg`-prefixed table —
including a name this project has never seen — counted as a relation.

RED for both, before the fix:

```text
6 failed, 51 passed
```

Two Minor observations were recorded and deliberately not changed: the
ordinary-shard regex is duplicated between `discovery` and `compatibility`
(identical today, unpinned by a test), and `assess_schema` runs inside
`_probe_one` rather than at the role boundary, so the "optional parts are
never probed" invariant rests on the Discovery regex rather than being stated
where the role is decided. Both are latent, not live, and neither is worth a
production change inside this capsule.

**Second independent review — one Critical, fixed red-first.** The review of the
corrected state found that the new `empty` outcome had reintroduced the first
finding in narrower form: `assess_schema` returned `EMPTY` *before* the
`foreign` check, so a part with no `Msg_<32hex>` table but with an unread
message-shaped relation beside it was certified `observed_complete` over an
empty window. The same `Msg_Archive_0` relation produced `incomplete` in a
populated part and `empty` in an empty one, purely on whether an unrelated
conversation table happened to exist. The sealed "readable conversations are
not the whole shard" test only covered the populated path, which is how it
survived. RED, before the fix:

```text
test_a_new_message_relation_in_an_empty_part_also_blocks_completion
AssertionError: assert 'empty' == 'incomplete'
```

Fixed by giving the `EMPTY` branch the same precedence the conversation branch
already had — `INCOMPLETE if foreign else EMPTY`. `gaps_for` was already correct:
`EMPTY` stays out of `INCOMPATIBLE_OUTCOMES` and out of the required-role gap, so
a genuinely empty part still certifies completeness and only the unread
relation withholds it.

The same review confirmed the empty-part helper is genuinely consolidated (one
definition, one call site per layer, no import cycle), that the FTS companion
exemption is neither too broad nor too narrow for the shapes tested —
`Msg_Archive_0` is still counted, `_DATA` is still counted, `_datax` is still
counted — and that all five suites were green.

## RED evidence

The focused suite was run against the production tree with only the probe wiring
removed (the `WANTED_COLUMNS` export kept, so the file collects and fails on
behaviour rather than on an import error):

```text
40 failed, 11 passed
```

Restoring the wiring: `51 passed`.

After the corrective pass the focused suite was `57 passed`, and after the
second review's fix `58 passed`.

An earlier attempt that reverted the whole production diff produced a collection
`ImportError` on `WANTED_COLUMNS` instead, which is not evidence of anything
behavioural and was not counted as RED.

## Final counts

Run separately; a single combined invocation of `memory/tests` and
`shadow/tests` collides on duplicate module basenames and errors during
collection, which is pre-existing and unrelated to this capsule.

| Suite | Result |
|---|---|
| `wechatdb/tests/provider/test_schema_compatibility.py` | 58 passed |
| `wechatdb/tests` | 340 passed |
| `acquisition/tests` | 173 passed |
| `bridge/tests` | 270 passed |
| `memory/tests` | 438 passed |
| `shadow/tests` | 158 passed |
| `git diff --check` | clean |

## Not proven

- No real schema was ever validated. `compatible` here is a synthetic
  behavioural contract, exactly as the FTS spike's is.
- Real schema compatibility for `search_index` and `media` is **unproven** and
  remains so; those roles are accounted, not assessed.
- No media decoding, attachment materialization or media API.
- Role classification is still by name shape only, because a source is still
  encrypted at inventory time. A file renamed to `message_3.db` is a real
  limitation this sub-gate does not address.
- The `Msg`-prefix relation check classifies by table-name prefix structure
  only. It cannot know what an unrecognized relation actually contains.
- The FTS5 companion exemption is a fixed list of five suffixes, matched against
  a `Msg_<32hex>_` name this project has never observed on a real database. It
  is a projection of the format, not a measurement: this repository's only FTS
  implementation names its content table `search_index_content`, not `Msg_*`.
  The list does not inspect relations structurally, so a companion under a
  different shape would still be counted as unread.
- Optional-role coverage neutrality is proven as a *difference* against a
  readable optional part, because a non-message name in the inventory already
  opens the sealed inventory gap on its own (committed Discovery classifies every
  name that is not `message_<n>.db` as unknown). That pre-existing gap is not
  schema evidence and was not changed here.

## This does not complete P4

**This does not complete P4-A unless all remaining P4-A requirements are
independently met** (D-040; checklist in
`docs/v2/DB_READER_P4_COMPLETE_CONTAINER_READINESS.md` §13).

Remaining P4 surface, in rough dependency order:

1. P4-A current-reader container compatibility (the assembled container, not one shard)
2. a decoded-search fallback for the search capability
3. lazy media compatibility

Each is a separate capsule and none is started here.
