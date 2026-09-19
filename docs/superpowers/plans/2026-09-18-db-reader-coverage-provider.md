# Implementation Plan — Coverage-Aware Database Reader Provider

**Date:** 2026-09-18
**Branch:** `feature/hermes-validation-isolation` (unmerged)
**Design spec (authoritative):**
[`docs/superpowers/specs/2026-09-18-db-reader-coverage-provider-design.md`](../specs/2026-09-18-db-reader-coverage-provider-design.md)
**Design baseline commit:** `006a80b`
**Governing decisions:** D-031 (Active), D-017 (Active, amended — provider
isolation), D-002, D-005, D-011, D-019, D-020, D-022, D-023, R-003.
D-030 is **lapsed** and is not reopened by anything in this plan.
**Status:** Plan only. Nothing here is implemented. This document authorises no
production change, no route, no promotion and no acquisition.
**Revision:** amended twice on 2026-09-18 after two independent architecture
reviews — see §0.4.

---

## 0. What this plan is

A TDD execution plan for the design approved in the spec above. It decomposes
the spec's seven-stage rollout (§14 M1…M7) into **20 independently reviewable
tasks** — one pre-provider task plus the seven stages — each with its own RED
test, minimum GREEN implementation, validation command and commit boundary.

The spec is the authority. Where this plan and the spec differ, the spec wins
and the executor stops and reports (§4 below).

**Read the spec in full before executing task P0.** This plan names files and
commands; it does not restate the design's reasoning, its truth table, or its
privacy analysis.

### 0.1 Ordering principle

The lowest-risk order is *generic boundary first, consumers second, provider
last*, because every provider-side test depends on boundary types that must
already be proven. Inside that order, each task is sized so that rejecting it
invalidates no other task's work:

- P0 gives conversation identity one generic owner, so no later task is tempted
  to depend on a sibling source or to copy an algorithm.
- P1–P2 move a vocabulary and change no behaviour.
- P3–P6 add types nothing consumes yet.
- P7–P8 widen a contract and close every row of the consumer audit in §6,
  **before** any source changes shape.
- P9–P10 change one source each.
- P11 fixes the live defect.
- P12–P16 build orchestration components inside the already-isolated
  `wechatdb`, which nothing imports.
- P17a proves the orchestration is correct; P17b proves it is honest.
- P18 seals the synthetic gate.

**The repository is green at every commit boundary.** No task ends red.

### 0.2 Baseline, verified at planning time

| Suite | Command directory | Tests | State |
|---|---|---|---|
| `bridge` | `bridge/` | 79 | passing |
| `memory` | `memory/` | 337 | passing |
| `wechatdb` | `wechatdb/` | 42 | passing |
| `shadow` | `shadow/` | 158 | passing |

These four counts were measured at planning time and are recorded as a
**baseline**, not as a target. **No task in this plan states an expected test
count**: a count written months before the code is a brittle assertion that
invites an executor to make the number match rather than make the behaviour
right. Every task's validation is *"every suite passes"*, plus the named tests
that must have been seen failing first.

### 0.3 The canonical test command

`pytest` is not installed against any interpreter on this machine's `PATH`; `uv`
is. Every command in this plan uses this form, verified working at planning
time:

```bash
uv run --python 3.12 --with pytest --with pytest-asyncio --with "mcp[cli]" --with zstandard python -m pytest
```

Referred to below as **`PYTEST`**. Each suite is run from its own package
directory, because each package ships its own `pytest.ini` with
`testpaths = tests`. If the executor has a project virtualenv with these
packages, plain `python -m pytest` is an acceptable substitute; the *arguments*
after `pytest` in each task are what matters.

Full-repository validation, used as the closing check of most tasks:

```bash
cd bridge && PYTEST -q && cd ../memory && PYTEST -q && cd ../wechatdb && PYTEST -q && cd ../shadow && PYTEST -q
```

**Four suites, and `shadow` is not optional.**
`shadow/tests/test_source_activation.py::test_the_runner_and_the_bridge_name_the_same_variables`
loads `bridge/message_source.py` **from its file path** with
`importlib.util.spec_from_file_location`, registers it in `sys.modules` and
**executes it**, then compares the activation constants against the runner's own
copies. Any change to the Reader boundary — a new import, a new `Enum`, a
`Generic` dataclass whose annotations must resolve, a renamed constant — is
exercised there and nowhere else in that form. `shadow` is therefore
**load-bearing for every Reader-boundary task**, and it runs in P0 and in every
task from P1 to P18 that touches `bridge/`.

The chain stays at four: the provider tests added in Stage 6 land under
`wechatdb/tests/provider/` and are collected by the **existing** `wechatdb`
run. No task adds a fifth test package.

### 0.4 What the two 2026-09-18 architecture reviews changed

**Second review — four blocking corrections and three plan fixes.** All are
resolved in place; nothing is carried forward as an open question.

| # | Finding | Resolution |
|---|---|---|
| 1 | P0 must not move `conversation_identifier` into `bridge/message_source.py`, and D-031 authorised no `hashlib` exception | **P0 rewritten.** New stdlib-only sibling module `bridge/conversation_identity.py`. The boundary's import set stays at the final spec's `{__future__, dataclasses, enum, typing}` and T-15 is modified by P3 alone. Every claim of a D-031-sanctioned `hashlib` exception is withdrawn. P0 now owes five named proofs. |
| 2 | The validation chain is four suites; `shadow` is load-bearing | **Restored everywhere.** `shadow/tests/test_source_activation.py` loads and executes `bridge/message_source.py` from its path. `shadow` runs in P0 and in every later Reader-boundary task, and in P18's final gate. All invented expected test counts are removed in favour of pass/fail expectations; §0.2 keeps four measured baseline counts, labelled as a baseline. |
| 3 | The vault still carried stale references and an already-answered operator ruling | **Reconciled.** `Current Status.md` and `Next Actions.md` corrected; the operator's 2026-09-18 approval of clean-room orchestration is recorded as approving **synthetic implementation**, explicitly not production promotion. |
| 4 | P11's `window_start ← requested_start` mapping was a semantic regression | **Rewritten.** Source-authored coverage replaces the completeness inference and supplies `observed_through` / `complete_through` where stated; existing observation-window evidence is kept when the source received no explicit bound. Three RED tests pin it, each to be seen failing against the naive mapping first. |
| 5 | P12's guard could collide with `test_layering.py`'s raw-substring scan of `bridge/**` | **Designed around.** The provider-isolation guard moves to `wechatdb/tests/provider/test_isolation.py`, matches by AST segment prefix, and weakens no existing architecture test. P12's acceptance includes seeing `test_layering.py` pass. |
| 6 | P17 was oversized | **Split** into P17a (correct) and P17b (honest), with the T-mapping updated. The gate keeps the number P18; the task count becomes twenty. |
| 7 | Stale source line numbers | Corrected or replaced with instructions to confirm at execution time. They are navigational, never semantic. |

**First review — seven findings, all previously resolved and still standing.**

| # | Finding | Resolution |
|---|---|---|
| 1 | `head_commit` was wrongly described as stale | §5 rewritten: `a928976` is the canonical **merged** anchor and correctly unchanged while the branch is unmerged. No task advances it. |
| 2 | D-031 still named a fourth type, `ReadWindow` | D-031 **amended in place** in the vault to three types matching `006a80b`, with the reason recorded; `Current Status.md`'s echo corrected. No D-032. |
| 3 | A second top-level `wechatprovider` package was proposed | Removed. Orchestration is `wechatdb/provider/`, tests are `wechatdb/tests/provider/`, and the leaf parser is unchanged. Stage 6 rewritten. |
| 4 | P16 left provider → Rion as an open review question | Closed as **NO**. New task **P0** gives `conversation_identifier` one generic owner; the provider imports that, never the adapter, and never a copy. *(The owner's location was corrected by the second review — see item 1 above.)* |
| 5 | Bare-collection consumers were not audited before P7/P8 | New §6 enumerates every consumer, its assumptions and the task that closes it. P8 retitled and rescoped to the whole audit. |
| 6 | P11 must remain the correctness milestone | Held; strengthened again by the second review's item 4. |
| 7 | The DB-provider gate must stay synthetic-only | Held; §1's scope guard carries the prohibitions. |

Across both revisions the task count moved 18 → 19 → 20. P1–P16 and P18 keep
their numbers throughout, so review comments on either earlier revision still
address the same tasks.

### 0.5 What P9 preflight changed — one contract correction, applied before P9

P9 is the first task that builds a `ReadCoverage` out of moments a real source
supplies, and its preflight exposed a type regression that P0–P8 could not have
surfaced: §6.3 and this plan's P5 snippet declared the four moment fields
`int | None`, while every moment a shipped source can produce is a `float`
(`NormalizedMessage.first_observed_at`, `NormalizedConversation`'s boundaries,
`get_recent_messages(since_observed_at: float)`, `RionReaderAdapter._number()`)
and both memory-side records they are documented to copy into without a
translation table are `float | None`. At spec revision `35e81f9` these same
moments were `float | None`; the `int` arrived when the former `ReadWindow` was
flattened into `ReadCoverage`.

The four generic moment fields are therefore **`float | None`**, corrected in
`bridge/message_source.py`, in §6.3 of the spec, and in P5's snippet above,
**before** P9 runs. No invariant, status, reason or field meaning changes, no
`ReadWindow` returns, and the design still adds exactly three generic types. The
correction is pinned by two tests in `bridge/tests/test_read_coverage.py`, one
on the evaluated annotations and one on a fractional value surviving
construction unchanged.

**This is not a global retype.** Provider-internal integer timestamps in Stage 6
stay integers: `ShardFacts.min_timestamp` / `.max_timestamp`, `ShardRouter`'s
`requested_start` / `requested_end` parameters, `Contribution.observed_through`
/ `.complete_through`, `ProviderResult.collapse`'s parameters and
`ShardedMessageProvider`'s keyword bounds are all fed by `wechatdb`'s
`normalise_timestamp() -> int` and `MessageRecord.timestamp: int`. A value
widening as it crosses into the generic contract is not a reason to widen the
type it came from. `before_sequence: int | None` is a sequence, not a moment,
and is untouched everywhere.

### 0.6 What P9's second preflight changed — Rion pagination is per-command

P9's contract gate asked whether the `sessions` command promises the same
pagination metadata as `history`. It does not, and the repository could not
answer it: the only `query` object anywhere in this tree is on the two
`history` fixtures, the interface gate recorded counts rather than
envelopes, and the design spec never mentioned `sessions` at all. Execution
stopped there rather than inventing a signal.

The exact Rion revision the sealed gate exercised has now been inspected —
`Rion-Wu-tech/wechat-intelligence-hub` @
`3afe33e0742ef4e92b4babe399bf471fdcd86a7b`,
`projects/rion-wechat-reader/rion_wechat_reader.py` — and settles it:
`history` emits `{"query": {"has_more": len(rows) == args.limit,
"next_offset": args.offset + len(rows)}, ...}`, while `sessions` emits only
`{"sessions": [...]}`. The design's assumption of a uniform pagination
envelope was wrong.

**Resolution, recorded in spec §9.1 and as D-031 amendment 4:**
`list_conversations` requests `caller_limit + 1` sessions and measures
truncation from whether the sentinel row comes back, dropping it before the
public `ReadResult`. Conversation listing is therefore **not** permanently
partial, and no count-only completeness heuristic returns — the forbidden
rule reads a coincidence as evidence, whereas an absent row that was
explicitly asked for is an answer. It is the same `limit + 1` measurement
§8.2 already applies to the provider's traversal. No generic type, status or
reason is added.

### 0.7 What P13 preflight changed — two discovery boundaries restored

P13's preflight compared the final §8.1 and this task's interface against the
two earlier design revisions and found two regressions introduced by the final
simplification, each of which removed a boundary while keeping the behaviour
that depends on it.

1. **The pre-probe `KNOWN` state.** Revision `35e81f9` had four part states,
   with **known** = “catalogued, not opened”. The final revision kept the rule
   that `catalogue()` opens nothing but dropped `known`, leaving no honest state
   for an entry whose name is recognised and whose readability is untested. It
   is restored: the catalogue now produces only `KNOWN` or `UNKNOWN`, and only
   `probe()` can produce `READABLE`.
2. **The `ShardOpener` boundary.** Revision `b34836f` had `ShardOpener`, a
   `ReadOnlySqliteOpener` (“opens `file:<path>?mode=ro` and nothing else …
   `immutable=1` is forbidden here and asserted against by test”) and
   `ShardDiscovery(locator, opener)`. The final revision replaced the opener with
   an `open_connection` callable on `ShardEntry` while still requiring Discovery
   to open read-only with `mode=ro` and never `immutable=1` — which Discovery
   cannot enforce on a connection an injected callable has already made. It is
   restored: `ShardEntry(name, handle)` with an opaque handle, an injected
   `ShardOpener`, and a small concrete read-only opener that is pinned
   independently.

This restores **only** those two load-bearing boundaries. The older role
taxonomy, `ShardDescriptor`, `ShardInventory` and the `ShardError` hierarchy are
not restored; the current simplified `ShardFacts` stays; the four states remain
provider-internal and never enter the generic boundary; no filesystem search,
default root, glob, walk or path discovery is introduced anywhere — opening an
explicitly injected handle read-only is not acquisition. Recorded as D-031
amendment 5. P13 remains unstarted; no Python changed.

---

## 1. Scope guard — what this plan must not produce

None of the following is in scope for **any** task here, and an executor that
finds itself writing one must stop and report instead:

- production database routing; `selected_source_name()` keeps returning `visual`
- provider promotion, or any import of `wechatdb` — at any depth, including
  `wechatdb.provider` — from `bridge/`, `memory/`, `shadow/`, `ai/`, `core/`,
  `app.py` or `mcp_server.py`
- a new top-level provider package: orchestration lives inside `wechatdb/`, and
  nothing is layered on top of it
- any dependency from the database provider on `bridge/rion_reader_adapter.py`,
  or any second copy of the `conversation_identifier` construction
- any widening of `bridge/message_source.py`'s import set beyond the final
  spec's `{__future__, dataclasses, enum, typing}`
- real WeChat data, a real container, a real path, or a real identifier in any
  fixture
- acquisition, key handling, salts, passphrases, `PRAGMA key`, SQLCipher,
  decryption, process-memory reads, LLDB or any debugger interaction
- FTS, search or cache implementation
- any change to `wechatdb/parser.py` or `wechatdb/msg_types.py`
- any change to the MCP tool surface, which stays exactly four tools, or to the
  wire shape of any tool payload
- any change to `NormalizedMessage.payload()`'s ten keys
- unrelated refactoring

`wx-cli-again` is **STUDY-only**. No source code, SQL text, query shape, test
fixture, fixture datum, comment or identifier-naming scheme may be copied from
it into this repository.

---

## 2. Names this plan uses

Every type name below comes from the spec except where marked.

| Name | Where | Spec |
|---|---|---|
| `COVERAGE_COMPLETE` / `_PARTIAL` / `_UNAVAILABLE` / `_NOT_OBSERVED`, `COVERAGE_STATUSES` | `bridge/message_source.py` | §6.1 |
| `ReadFreshness` (`EVIDENCE_CONSISTENT`, `POTENTIALLY_STALE`, `UNKNOWN`) | `bridge/message_source.py` | §6.2 |
| `ReadCoverage` | `bridge/message_source.py` | §6.3 |
| `ReadResult[T]` | `bridge/message_source.py` | §6.4 |
| the twelve `REASON_*` tokens, `COVERAGE_REASONS` | `bridge/message_source.py` | §6.5 |
| `conversation_identifier` | `bridge/conversation_identity.py` (**moved** by P0) | §8.5 |
| `ShardDiscovery`, `ShardOpener`, `ShardRouter`, `IdentityResolver`, `ProviderResult` | `wechatdb/provider/` | §8 |
| `ShardedMessageProvider` | `wechatdb/provider/provider.py` | **plan-introduced**, see below |

**`ShardedMessageProvider` is the one name this plan introduces.** The spec's
§5.1 data flow begins at a caller invoking `get_messages(conversation_id, limit,
window)` and ends at `ProviderResult` producing a `ReadResult`, but it never
names the object that owns that call. This plan names it. It is
provider-internal, unwired, constructed only by its own tests, never registered
in `build_database_source()`, and never imported by product core. If review
rejects the name, only P17 is affected.

**There is no top-level `wechatprovider` package.** `wechatdb` *is* the isolated
candidate provider; orchestration belongs inside it, with `wechatdb/parser.py`
remaining its leaf parser. The architectural model is three sibling
implementations behind one generic boundary —

```
                 product core
                      │  uses
                      ▼
        MessageSource / ReadResult  (bridge/message_source.py)
                      ▲
        ┌─────────────┼──────────────────────────┐
        │             │                          │
 RionReaderAdapter  StoreMessageSource   wechatdb/provider/  (isolated)
                                                 │ uses
                                                 ▼
                                         wechatdb/parser.py (leaf, unchanged)
```

— and **not** a second provider package layered on top of `wechatdb`. The three
siblings never import one another (P0 and P16).

There is **no `ReadWindow` type.** The final spec at `006a80b` represents the
requested window through `ReadCoverage`'s `requested_start` and `requested_end`
fields together with `truncated` and the coverage `status` / `reason` tokens
(§6.3: *"There is no separate window object and no `windowed` flag"* — a stored
flag can contradict the bounds sitting beside it). An earlier draft of the
vault's D-031 entry named a fourth type; **D-031 has been amended in place** to
match the final spec, and no D-032 was created. See §5.

---

## 3. Task index

| # | Task | Stage | Touches |
|---|---|---|---|
| **P0** | Canonical conversation identity moves to the generic boundary | 0 (pre-provider) | `bridge/message_source.py`, `bridge/rion_reader_adapter.py`, `bridge/tests/` |
| **P1** | Coverage tokens land in the Reader boundary | 1 / M1 | `bridge/message_source.py`, `bridge/tests/` |
| **P2** | `memory_store` imports the tokens instead of defining them | 1 / M1 | `memory/memory_store.py`, `memory/tests/` |
| **P3** | `ReadFreshness`, and the neutrality guard widened by `enum` | 2 / M2 | `bridge/message_source.py`, `bridge/tests/` |
| **P4** | The closed reason-token set and its status map | 2 / M2 | `bridge/message_source.py`, `bridge/tests/` |
| **P5** | `ReadCoverage` and its ten construction invariants | 2 / M2 | `bridge/message_source.py`, `bridge/tests/` |
| **P6** | `ReadResult[T]` and its two invariants | 2 / M2 | `bridge/message_source.py`, `bridge/tests/` |
| **P7** | `MessageSource` declares `ReadResult` return types | 3 / M3 | `bridge/message_source.py`, `bridge/tests/` |
| **P8** | Every collection consumer survives the envelope | 3 / M3 | `bridge/wechat_companion_mcp.py`, `bridge/tests/` |
| **P9** | `RionReaderAdapter` authors coverage; `has_more` is preserved | 4 / M4 | `bridge/rion_reader_adapter.py`, `bridge/tests/` |
| **P10** | `StoreMessageSource` authors conservative coverage | 4 / M4 | `bridge/store_access.py`, `bridge/tests/` |
| **P11** | **`memory_ingest` consumes source coverage; the length inference is deleted** | 5 / M5 | `memory/memory_ingest.py`, four `memory/tests/` files |
| **P12** | `wechatdb/provider/` skeleton, synthetic fixtures, isolation guards | 6 / M6 | `wechatdb/provider/`, `wechatdb/tests/provider/`, `bridge/tests/` |
| **P13** | `ShardDiscovery` — an inventory that never drops an entry | 6 / M6 | `wechatdb/provider/discovery.py` |
| **P14** | `ShardRouter` — total accounting, ordering, stop classification | 6 / M6 | `wechatdb/provider/routing.py` |
| **P15** | `IdentityResolver` — lookup, precedence, refused ambiguity | 6 / M6 | `wechatdb/provider/identity.py` |
| **P16** | `ProviderResult` — the pessimistic collapse into one `ReadCoverage` | 6 / M6 | `wechatdb/provider/result.py` |
| **P17a** | `ShardedMessageProvider` — the smallest successful orchestration (T-1, T-4, T-9) | 6 / M6 | `wechatdb/provider/provider.py`, `wechatdb/tests/provider/test_provider_reads.py` |
| **P17b** | The degradation and uncertainty matrix (T-2, T-3, T-5, T-6, T-7, T-10, T-11, T-18) | 6 / M6 | `wechatdb/provider/provider.py`, `wechatdb/tests/provider/test_provider_degradation.py` |
| **P18** | The synthetic gate: T-1…T-18 mapped, revert-verified, recorded | 7 / M7 | `docs/v2/DB_READER_COVERAGE_GATE.md`, test trees |

**Twenty tasks.** (P17 split into P17a and P17b; the gate keeps the number
P18, so every other task's number is unchanged from the previous revision.)
**P11 fixes the live Memory false-complete defect.**
**P12 is the first task whose code imports `wechatdb`'s parser**; it is also the
first to add anything under `wechatdb/provider/`. No task edits
`wechatdb/parser.py` or `wechatdb/msg_types.py`.

## 4. Stop-and-report protocol

If implementation evidence contradicts the approved architecture, the executor
**stops** and reports exactly four things:

1. the concrete evidence (a failing test, a file and line, an observed value)
2. the affected design decision (D-nnn, or the spec section)
3. the minimum affected scope (which tasks, which files)
4. the smallest proposed resolution

It does **not** perform broad repository archaeology, does not redesign the
architecture, does not widen scope to "fix it properly", and does not silently
adjust a test to make a contradiction disappear.

Two contradictions are already anticipated and are **not** grounds to stop:

- Existing `bridge` tests that index (`conversations[0]`) or compare against a
  list (`== []`) will need updating at P8–P10. That is planned migration, not
  contradiction.
- Existing `memory` tests whose `FakeSource` returns bare lists will need
  updating at P11. Same.

---

## 5. Canonical memory — reconciled, not stale

Recorded so a reviewer does not re-derive it. An earlier revision of this plan
described the vault as stale; **that was this plan's error, and it is
corrected here.**

1. **`head_commit` is `a928976`, and that is correct.** It is the canonical
   **merged** anchor: *the committed repository state through which canonical
   project memory has been reconciled*. It stays `a928976` while
   `feature/hermes-validation-isolation` is unmerged, however many commits that
   branch accumulates. An unmerged branch never advances the anchor. `Current
   Status.md` already says so explicitly, and this plan does not change it.
   **No task in this plan advances `head_commit`.**
2. **The branch reconciliation, not the anchor, carries the branch state.**
   `Current Status.md` records twenty-plus unmerged commits on the branch
   through the current local HEAD, with the design spec final at `006a80b` and
   the newest commits being this plan document and its correction. The working
   tree is clean, the branch has no upstream and was not pushed.
3. **D-031 has been amended in place** (2026-09-18) so its type list matches
   the final spec at `006a80b`: three generic types — `ReadFreshness`,
   `ReadCoverage`, `ReadResult[T]` — and no `ReadWindow`. The amendment records
   why the window is carried as fields rather than as an object. **No D-032 was
   created.** `Current Status.md`'s echo of the same list was corrected with it.
   D-017 stays Active/amended, D-030 stays lapsed, database-provider promotion
   stays undecided, visual/OCR stays the production default, and no acquisition
   permission is created or implied.

---

## 6. Collection-consumer audit (prerequisite for P7 and P8)

`ReadResult` provides `__iter__` and `__len__` and **nothing else**. It does
**not** provide `__getitem__`, slicing, list equality, list concatenation or a
custom `__bool__`. Truthiness works only because Python falls back to `__len__`
when `__bool__` is absent. Every caller of `list_conversations`,
`get_messages` and `get_recent_messages` was therefore enumerated before P7 was
finalised, and every row below is covered by a named test in the task listed.

### 6.1 Production consumers — the complete set

Line numbers are as of `d2030f4` and are **navigational aids, not semantic
requirements**: confirm each site at execution time and correct the number here
rather than treating a moved line as a contradiction.

| # | Site | Assumptions used | Survives `ReadResult`? | Handled by |
|---|---|---|---|---|
| 1 | `bridge/wechat_companion_mcp.py:211` `list_conversations` | `len(...)` (l.214), iteration in a payload comprehension (l.219) | yes | P8 (materialised anyway, for one uniform shape) |
| 2 | `bridge/wechat_companion_mcp.py:244` `get_messages` | `len(...)` (l.247), **truthiness** and **indexing** `ordered[0].sequence if ordered else None` (l.254), iteration (l.255) | **NO — indexing raises `TypeError`** | **P8** |
| 3 | `bridge/wechat_companion_mcp.py:277` `get_recent_messages` | `len(...)` (l.280), iteration (l.286) | yes | P8 |
| 4 | `bridge/rion_reader_adapter.py:262` `_chat_for` self-call | result discarded; the call is made for its side effect on `_chat_by_id` | yes | P9 |
| 5 | `bridge/rion_reader_adapter.py:359` sweep self-call | iteration | yes, but the sweep must now also **read `.coverage`** of each inner read to author its own | **P9** |
| 6 | `bridge/rion_reader_adapter.py:360` inner `get_messages` | iteration | as above | **P9** |
| 7 | `memory/memory_ingest.py:417` `list_conversations` | iteration in a comprehension | yes, but must now read `.coverage` | **P11** |
| 8 | `memory/memory_ingest.py:419` `list(source.get_messages(...))` | iteration via `list()`; the result is then a real `list` and every later operation is on that list | yes, but must now read `.coverage` | **P11** |

**No production consumer slices, concatenates, compares against a list, or
serialises a protocol result directly.** Payloads are always built per item
(`[item.payload() for item in ...]`), so no serializer sees the envelope.
Site 2 is the only production breakage, and P8 removes it **before** any source
changes shape.

### 6.2 Test consumers and test stubs

Not production, listed so no task is surprised. All are updated inside the task
that changes the shape they observe — planned migration, not contradiction (§4).

| Site | Assumption | Updated by |
|---|---|---|
| `bridge/tests/test_reader_boundary.py:200-210` | indexing `conversations[0]` | P9 |
| `bridge/tests/test_reader_boundary.py:219-245` | indexing `messages[0]`, `messages[-1]` | P9 |
| `bridge/tests/test_reader_boundary.py:251` | **list equality** `== []` | P9 |
| `bridge/tests/test_reader_boundary.py:255-259` | iteration | P9 (no edit needed; listed for completeness) |
| `bridge/tests/test_reader_boundary.py:762-778` | indexing `conversations[0].id`, iteration into `json.dumps` | P10 |
| `bridge/tests/test_wechat_companion_mcp.py` (all) | consumes **tool dict payloads**, never a protocol result | no edit — and that is the proof the wire shape did not move |
| `memory/tests/test_memory_ingest.py` `FakeSource` | **produces** bare lists | P11 |
| `memory/tests/test_memory_freshness.py` `Refusing` | produces bare lists | P11 |
| `memory/tests/test_memory_sync.py` stub source | produces bare lists | P11 |
| `memory/tests/test_memory_worker.py` stub source | produces bare lists | P11 |

`shadow/` references (`exact8_assertion_proxy.py`, `h5_real_data_canary.py`,
`runners/claude.py`, `runners/hermes.py`) are **tool-name strings on the MCP
wire**, not protocol calls. The tool surface does not change, so none of them is
touched by any task. `core/wechat_db.py`, `mcp_server.py` and `app.py` have
their own unrelated `get_messages` on the **v1** historical generation and are
out of scope entirely.

---

# Tasks

Each task states: **files**, **interface**, **depends on**, **RED test**,
**prove RED**, **minimum GREEN**, **validate**, **commit**.

---

## Stage 0 — One canonical conversation identity (pre-provider)

### P0 — Canonical conversation identity moves to its own generic module

**Why this exists, and why it is first.** Spec §8.5 requires the database
provider to derive conversation identifiers *"with the same construction
`rion_reader_adapter.conversation_identifier` uses, so two readers can never
disagree about what a conversation's identifier is."* The provider must **not**
obtain that by importing the Rion adapter: the two are sibling implementations
behind the same generic boundary, and a sibling dependency would make the
database provider inherit an external-reader transport it has nothing to do
with. Copying the function is equally rejected, for the reason spec §6.1 gives
about the coverage tokens — two copies pinned by an equality test are still two
copies. The remaining correct move is to give the construction **one generic
owner**, before anything needs two consumers of it.

**Classification of the existing function — Case A, genuinely source-neutral.**
`bridge/rion_reader_adapter.py` (the `conversation_identifier` definition, just
below `RECENT_CONVERSATION_SCAN_LIMIT`; confirm the line number at execution
time rather than trusting this plan) reads, in full:

```python
def conversation_identifier(chat: str) -> int:
    digest = hashlib.blake2b(chat.encode("utf-8"), digest_size=6).digest()
    return int.from_bytes(digest, "big")
```

It is a total function from an arbitrary string to a stable, positive, 48-bit,
JSON-safe integer. It contains **no** Rion vocabulary, no transport or process
semantics, no schema, no column, no table, no path, and no branch on any reader
behaviour. Its only tie to the adapter is that the adapter happened to be the
first source whose native conversation key was a string. The property it
defines — *how a string conversation key becomes the `int` that
`NormalizedConversation.id` and `NormalizedMessage.conversation_id` carry* — is
a property of the Reader **layer**, which is precisely why two sources
disagreeing about it would be a defect. Case A holds.

**Where it goes, and where it does not.** It goes into a **new stdlib-only
generic sibling module**, `bridge/conversation_identity.py`. It does **not** go
into `bridge/message_source.py`.

```
bridge/
├── message_source.py          generic boundary TYPES and the protocol only
│                              imports: {__future__, dataclasses, enum, typing}
│                              — the final spec's set, sealed by T-15, unchanged
│                              by this task and by every later task
└── conversation_identity.py   the canonical conversation_identifier algorithm
                               imports: {__future__, hashlib}
```

Both consumers import the one canonical function from there:

```
        bridge/rion_reader_adapter.py ──┐
                                        ├──▶ bridge/conversation_identity.py
        wechatdb/provider/result.py  ───┘         conversation_identifier()
```

and `wechatdb/provider/**` never imports `bridge/rion_reader_adapter.py`.

**Correcting the previous revision of this plan.** An earlier revision proposed
moving the function into `bridge/message_source.py` and widening T-15's
allowed-import set by `hashlib`, and claimed the amended D-031 sanctioned that.
**Both are withdrawn.** D-031 authorised no `hashlib` exception and no change to
the boundary's import set; the final spec at `006a80b` (§12.7, §17.2) keeps that
set at `{__future__, dataclasses, enum, typing}`, and T-15 is the seal on a
technology-neutral surface that this plan does not get to loosen for
convenience. A separate module costs one file and keeps the sealed surface
exactly as the spec left it.

**Files**
- new `bridge/conversation_identity.py`
- modify `bridge/rion_reader_adapter.py`
- modify `bridge/tests/test_reader_boundary.py`

**Interface produced**

```python
# bridge/conversation_identity.py
from __future__ import annotations

import hashlib


def conversation_identifier(chat: str) -> int:
    # The one construction every source uses to turn a string conversation key
    # into the integer NormalizedConversation.id carries. Technology-neutral:
    # it names no reader, transport, schema, table, column or path. It lives in
    # its own module rather than beside the boundary types so that
    # message_source.py keeps the import set the Reader contract is sealed at.
```

`bridge/rion_reader_adapter.py` **deletes its definition** and imports the name
from `conversation_identity`, re-exporting it at module level so
`rion_reader_adapter.conversation_identifier` keeps working for every existing
caller and test. Its `import hashlib` goes with the function if nothing else in
the module needs it.

**Depends on** nothing. It precedes P1 so every later task sees a single owner,
and so the provider in P16 has something correct to import the day it is
written.

**RED tests** — in `bridge/tests/test_reader_boundary.py`. The five proofs this
task owes:

1. `test_conversation_identity_has_its_own_generic_module` — asserts
   `conversation_identity.conversation_identifier` exists, is stable across
   calls, differs for different inputs, and returns `0 < value < 2 ** 53`.
2. `test_the_identifier_is_unchanged_for_every_existing_fixture` — for every
   chat identifier used anywhere in the existing fixtures (`wxid_fixture_a`,
   the fixture room, and the module-level `CONVERSATION_A` constant's input),
   the value produced by the new module **equals** the value the adapter
   produced before the move, as integers, and equals
   `rion_reader_adapter.conversation_identifier` after it. Nothing already
   derived, stored or asserted shifts by one bit.
3. `test_only_one_implementation_of_conversation_identity_exists` — `ast` scan
   of every module under `bridge/` and `wechatdb/` other than
   `conversation_identity.py`, asserting none contains a `FunctionDef` named
   `conversation_identifier` and none contains a `blake2b` call. This is what
   stops a second copy reappearing, here or in the provider.
4. `test_the_boundary_module_never_gains_a_digest_dependency` — `ast` scan of
   `bridge/message_source.py` asserting `hashlib` is **not** among its imports
   and no identifier in it is `conversation_identifier` or `blake2b`.
5. The existing `test_the_protocol_depends_on_no_reader_technology` (T-15) is
   **left exactly as it is**: `imported <= {"__future__", "dataclasses",
   "typing"}` now, gaining `enum` at P3 and nothing else ever. This task must
   not touch it, and proof 4 is what makes that safe. *(P3 is the only task in
   this plan that modifies T-15.)*

Plus a sixth, cheap and worth having:

6. `test_the_identity_module_is_technology_neutral` — the same shape of scan
   T-15 applies to the boundary, applied to `conversation_identity.py`: its
   imports are `⊆ {__future__, hashlib}`, and it contains none of `rion`,
   `subprocess`, `sqlcipher`, `wechat`, `json`, `argv`, `zstd`, `sqlite`,
   `Msg_`, `Name2Id`, or any path separator.

**Prove RED**

```bash
cd bridge && PYTEST -q tests/test_reader_boundary.py -k "conversation_identity_has_its_own or only_one_implementation or never_gains_a_digest"
```

Expected failure: `ModuleNotFoundError: conversation_identity`. Then, with the
module created but before the adapter's definition is deleted, run the same
command again: proof 3 must now fail on `bridge/rion_reader_adapter.py`. Seeing
that second failure separately proves the guard catches a duplicate rather than
merely tolerating the new location.

**Minimum GREEN** — create `bridge/conversation_identity.py` with the function;
delete the adapter's definition and import the name, re-exporting it. Do not
touch `message_source.py`. Do not touch T-15.

**Validate**

```bash
cd bridge && PYTEST -q && cd ../memory && PYTEST -q && cd ../shadow && PYTEST -q
```

Every suite passes. Three specific things must hold:

- the existing `test_conversation_identifiers_are_stable_and_json_safe` passes
  **unchanged** — it calls through the adapter's re-export, which is exactly the
  compatibility this task promises;
- T-15 passes **unmodified**, which is proof 5;
- `shadow` passes, because `test_source_activation.py` executes
  `bridge/message_source.py` from its path and would notice any accidental
  change to that module — and this task makes none.

**Commit** — `refactor(bridge): one canonical conversation identity, in its own generic module`

---

## Stage 1 — Generic coverage vocabulary (spec §6.1, M1)

### P1 — Coverage tokens land in the Reader boundary

**Files**
- modify `bridge/message_source.py`
- modify `bridge/tests/test_reader_boundary.py`

**Interface produced** (in `bridge/message_source.py`, module level, verbatim
values from spec §6.1)

```python
COVERAGE_COMPLETE: str = "observed_complete"
COVERAGE_PARTIAL: str = "observed_partial"
COVERAGE_UNAVAILABLE: str = "unavailable"
COVERAGE_NOT_OBSERVED: str = "not_observed"

COVERAGE_STATUSES: frozenset[str] = frozenset({
    COVERAGE_COMPLETE, COVERAGE_PARTIAL,
    COVERAGE_UNAVAILABLE, COVERAGE_NOT_OBSERVED,
})
```

**Depends on** nothing.

**RED test** — new, in `bridge/tests/test_reader_boundary.py`, in a new section
`# --- Coverage vocabulary ---`:

- `test_the_boundary_owns_the_four_coverage_tokens` — asserts the four values
  are exactly `"observed_complete"`, `"observed_partial"`, `"unavailable"`,
  `"not_observed"`, and that `COVERAGE_STATUSES` is a `frozenset` containing
  exactly those four.

**Prove RED**

```bash
cd bridge && PYTEST -q tests/test_reader_boundary.py::test_the_boundary_owns_the_four_coverage_tokens
```

Expected failure: `AttributeError: module 'message_source' has no attribute
'COVERAGE_COMPLETE'`.

**Minimum GREEN** — add the five names above with their docstring comments. Add
no imports: a string constant requires none, which is why the neutrality guard
is untouched by this task.

**Validate**

```bash
cd bridge && PYTEST -q && cd ../memory && PYTEST -q && cd ../shadow && PYTEST -q
```

Every suite passes. `memory` is run because it still
owns its own copies; this task must not disturb it.

**Commit** — `feat(bridge): the reader boundary owns the coverage vocabulary`

---

### P2 — `memory_store` imports the tokens instead of defining them

**Files**
- modify `memory/memory_store.py`
- modify `memory/tests/test_layering.py`

**Interface produced** — `memory/memory_store.py` no longer assigns the four
string literals. It imports them from `message_source` through the *existing*
`try: from message_source import ... / except ImportError:` pattern already used
by `memory/memory_ingest.py` (lines 58–86), and keeps all four names in its
existing `__all__`, so `memory_ingest`, `memory_retrieval`, `memory_query` and
every current test keep working with no edit. `COVERAGE_STATES` — the smaller
subset a *stored* row may carry — stays defined in `memory_store`, because it is
a statement about the store's schema, not about the vocabulary (spec §6.1).

**Depends on** P1.

**RED test** — new, in `memory/tests/test_layering.py`:

- `test_the_memory_layer_does_not_redefine_the_coverage_vocabulary` — parses
  `memory/memory_store.py` with `ast` and asserts no module-level assignment
  binds `COVERAGE_COMPLETE`, `COVERAGE_PARTIAL`, `COVERAGE_UNAVAILABLE` or
  `COVERAGE_NOT_OBSERVED` to a string literal; then asserts
  `memory_store.COVERAGE_COMPLETE is message_source.COVERAGE_COMPLETE` for all
  four (identity, not equality — two equal copies are still two copies).

**Prove RED**

```bash
cd memory && PYTEST -q tests/test_layering.py::test_the_memory_layer_does_not_redefine_the_coverage_vocabulary
```

Expected failure: the AST assertion finds four literal assignments.

**Minimum GREEN** — delete the four literal assignments from `memory_store.py`,
import the names, keep the explanatory comment block rewritten to say where the
vocabulary now lives, keep `COVERAGE_STATES` local, keep `__all__` unchanged.

**Validate**

```bash
cd memory && PYTEST -q && cd ../bridge && PYTEST -q && cd ../shadow && PYTEST -q
```

Every suite passes.

**Commit** — `refactor(memory): import the coverage vocabulary, never redefine it`

---

## Stage 2 — Generic Reader result types (spec §6.2–§6.5, §6.7, M2)

Nothing consumes these types until Stage 3. Each of P3–P6 is independently
revertible.

### P3 — `ReadFreshness`, and the neutrality guard widened by `enum`

**Files**
- modify `bridge/message_source.py`
- modify `bridge/tests/test_reader_boundary.py`

**Interface produced**

```python
class ReadFreshness(str, Enum):
    EVIDENCE_CONSISTENT = "evidence_consistent"
    POTENTIALLY_STALE = "potentially_stale"
    UNKNOWN = "unknown"
```

`from enum import Enum` joins the module's imports. **No other import is
added, in this task or any later one.**

**Depends on** P1.

**RED tests** — in `bridge/tests/test_reader_boundary.py`:

- `test_freshness_is_three_tokens_and_never_a_boolean` — asserts the three
  member values, that the type is a `str` subclass so a member serialises to the
  token a human reads, that there are exactly three members, and that no
  identifier anywhere in `message_source.py` is named `fresh` or `is_fresh`.
- modify the existing `test_the_protocol_depends_on_no_reader_technology` (T-15)
  so its assertion reads `imported <= {"__future__", "dataclasses", "enum",
  "typing"}`. Its forbidden-identifier list is **not** relaxed.

**Prove RED**

```bash
cd bridge && PYTEST -q tests/test_reader_boundary.py::test_freshness_is_three_tokens_and_never_a_boolean
```

Expected failure: `AttributeError: ... 'ReadFreshness'`.

Then, having added the enum but before widening the guard, confirm the guard
genuinely constrains the module:

```bash
cd bridge && PYTEST -q tests/test_reader_boundary.py::test_the_protocol_depends_on_no_reader_technology
```

Expected failure: `assert {'enum', ...} <= {'__future__', 'dataclasses',
'typing'}`. This ordering is deliberate — it proves the guard is load-bearing
rather than decorative before the allowed set is widened.

**Minimum GREEN** — add the import, the enum and its docstrings; widen the
allowed-import set by `enum` and nothing else.

**Validate**

```bash
cd bridge && PYTEST -q && cd ../shadow && PYTEST -q
```

Every suite passes.

**Commit** — `feat(bridge): freshness is three tokens, orthogonal to coverage`

---

### P4 — The closed reason-token set and its status map

**Files**
- modify `bridge/message_source.py`
- modify `bridge/tests/test_reader_boundary.py`

**Interface produced** — the twelve `REASON_*` constants and `COVERAGE_REASONS`
exactly as spec §6.5 lists them, plus the mapping that makes §6.5's table
executable rather than prose:

```python
#: Which statuses each reason is a valid explanation for (spec §6.5).
REASON_STATUSES: dict[str, frozenset[str]] = {
    REASON_FULL_WINDOW_OBSERVED: frozenset({COVERAGE_COMPLETE}),
    REASON_EMPTY_WINDOW:         frozenset({COVERAGE_COMPLETE}),
    REASON_WINDOW_BOUND:         frozenset({COVERAGE_COMPLETE}),
    REASON_CALLER_LIMIT:         frozenset({COVERAGE_PARTIAL}),
    REASON_SOURCE_LIMIT:         frozenset({COVERAGE_PARTIAL}),
    REASON_UPSTREAM_MORE:        frozenset({COVERAGE_PARTIAL}),
    REASON_UNSAFE_EARLY_STOP:    frozenset({COVERAGE_PARTIAL}),
    REASON_TIMESTAMP_MISMATCH:   frozenset({COVERAGE_PARTIAL}),
    REASON_PARTIAL_INVENTORY:    frozenset({COVERAGE_PARTIAL, COVERAGE_UNAVAILABLE}),
    REASON_SCOPE_UNSUPPORTED:    frozenset({COVERAGE_UNAVAILABLE}),
    REASON_SCOPE_NOT_READ:       frozenset({COVERAGE_NOT_OBSERVED}),
    REASON_NO_OBSERVATION:       frozenset({COVERAGE_NOT_OBSERVED}),
}
```

**Depends on** P1.

**RED tests** — the static half of T-18:

- `test_the_reason_vocabulary_is_closed` — `COVERAGE_REASONS` holds exactly
  twelve tokens; every token is lowercase ASCII with no whitespace; the set of
  `REASON_STATUSES` keys equals `COVERAGE_REASONS`.
- `test_the_reason_status_mapping_is_total_in_both_directions` — every reason
  maps to at least one status, every status in `COVERAGE_STATUSES` is named by
  at least one reason, and every value set is a subset of `COVERAGE_STATUSES`.

**Prove RED**

```bash
cd bridge && PYTEST -q tests/test_reader_boundary.py -k "reason_vocabulary or reason_status_mapping"
```

Expected failure: `AttributeError: ... 'COVERAGE_REASONS'`.

**Minimum GREEN** — add the constants, the frozenset and the mapping.

**Validate**

```bash
cd bridge && PYTEST -q && cd ../shadow && PYTEST -q
```

Every suite passes.

**Commit** — `feat(bridge): a closed reason vocabulary for coverage claims`

---

### P5 — `ReadCoverage` and its ten construction invariants

**Files**
- modify `bridge/message_source.py`
- new `bridge/tests/test_read_coverage.py`

A new test module, because T-17 is a dense table of invariant cases and folding
it into the 850-line boundary suite would bury it. `bridge/pytest.ini` needs no
change: `testpaths = tests` already collects it.

**Interface produced** — `ReadCoverage` exactly as spec §6.3, frozen, slotted,
with `__post_init__` enforcing invariants 1–10 of §6.7 by raising `ValueError`.
Field order and types are the spec's, unchanged:

```python
@dataclass(frozen=True, slots=True)
class ReadCoverage:
    status: str
    reason: str
    requested_start: float | None
    requested_end: float | None
    observed_through: float | None
    complete_through: float | None
    freshness: ReadFreshness
    truncated: bool
    item_count: int
```

**Depends on** P1, P3, P4.

**RED tests** — `bridge/tests/test_read_coverage.py`, one test per invariant,
each constructing the violating object and asserting `ValueError`:

| Test | Invariant |
|---|---|
| `test_an_unknown_status_is_refused` | 1 |
| `test_an_unknown_reason_is_refused` | 2a |
| `test_a_reason_invalid_for_its_status_is_refused` | 2b |
| `test_a_complete_read_can_never_be_truncated` | 3 |
| `test_a_negative_item_count_is_refused` | 4 |
| `test_an_empty_window_reason_with_items_is_refused` | 5 |
| `test_truncation_requires_a_cut_short_reason` | 6 |
| `test_unavailable_and_not_observed_carry_nothing` | 7 (four assertions: freshness, item_count, both boundaries) |
| `test_a_complete_point_requires_an_observed_point` | 8a |
| `test_a_complete_point_may_not_exceed_the_observed_point` | 8b |
| `test_a_complete_read_completes_through_what_it_observed` | 9 |
| `test_an_inverted_requested_window_is_a_caller_defect` | 10 |
| `test_a_lawful_coverage_constructs` | the positive control — one instance per status |

**Prove RED**

```bash
cd bridge && PYTEST -q tests/test_read_coverage.py
```

Expected failure: collection error, `ImportError: cannot import name
'ReadCoverage'`.

**Minimum GREEN** — the dataclass and a `__post_init__` that checks the ten
invariants in the spec's order and raises `ValueError` with a fixed, lowercase,
content-free message. **No invariant is caught anywhere to produce a softer
answer** (spec §6.7).

**Validate**

```bash
cd bridge && PYTEST -q && cd ../shadow && PYTEST -q
```

Every suite passes.

**Commit** — `feat(bridge): ReadCoverage, with claims it cannot overstate`

---

### P6 — `ReadResult[T]` and its two invariants

**Files**
- modify `bridge/message_source.py`
- modify `bridge/tests/test_read_coverage.py`

**Interface produced** — `ReadResult` exactly as spec §6.4: `Generic[T]`,
frozen, slotted, `items: tuple[T, ...]`, `coverage: ReadCoverage`, `__iter__`
and `__len__` documented as migration affordances only, plus `__post_init__`
enforcing invariants 11 and 12.

`Generic`, `TypeVar` come from `typing`, already imported. `slots=True` on a
generic dataclass needs Python ≥ 3.11 (spec §6.4); the repository runs 3.12+.

**Depends on** P5.

**RED tests** — in `bridge/tests/test_read_coverage.py`:

- `test_a_result_whose_count_disagrees_with_its_items_is_refused` (invariant 11)
- `test_items_must_be_a_tuple_so_a_result_cannot_be_mutated` (invariant 12 —
  passing a `list` raises)
- `test_iteration_and_length_are_migration_affordances` — `list(result)` and
  `len(result)` work and agree with `result.items`; `result.coverage` is the
  durable accessor.

**Prove RED**

```bash
cd bridge && PYTEST -q tests/test_read_coverage.py -k "result"
```

Expected failure: `ImportError: cannot import name 'ReadResult'`.

**Minimum GREEN** — the dataclass, the two dunders, the two invariant checks.
**No `__getitem__`, no `__eq__` against a list, no `.get()`, no `__bool__`.**
The absent affordances are what force P8 to be an explicit migration rather than
an accident.

**Validate**

```bash
cd bridge && PYTEST -q && cd ../memory && PYTEST -q && cd ../shadow && PYTEST -q
```

Every suite passes.

**Commit** — `feat(bridge): ReadResult carries items and what they may claim`

---

## Stage 3 — MessageSource protocol migration (spec §6.6, M3)

### P7 — `MessageSource` declares `ReadResult` return types

**Files**
- modify `bridge/message_source.py`
- modify `bridge/tests/test_reader_boundary.py`

**Interface produced** — the three collection methods on the `MessageSource`
Protocol are re-declared as returning `ReadResult[NormalizedConversation]` and
`ReadResult[NormalizedMessage]` (spec §6.6). `status()` is **not touched** and
keeps returning `SourceStatus`. The Protocol's class docstring is updated: an
empty `ReadResult` is an answer only when its coverage says the window was
accounted for.

This task is **typing and documentation only**. `@runtime_checkable` checks
method *presence*, not signatures, so both shipped sources still satisfy the
Protocol at runtime and nothing breaks. That is exactly why this is a separate,
trivially revertible commit.

**Depends on** P6.

**Prerequisite, already discharged:** §6 of this plan enumerates every caller of
the three methods and classifies what each assumes. That audit is what makes
this task safe to perform as a typing-only change and what determines the
content of P8. It is not a flag-day: exactly one production site breaks, and P8
fixes it before any source changes shape.

**RED test** — in `bridge/tests/test_reader_boundary.py`:

- `test_the_protocol_promises_a_result_envelope_not_a_bare_collection` — parses
  `message_source.py` with `ast`, locates the `MessageSource` class, and asserts
  each of the three collection methods' return annotation renders as
  `ReadResult[...]`, while `status`'s renders as `SourceStatus`. Reading the
  annotation from the syntax tree rather than from prose keeps a docstring from
  passing the test.

**Prove RED**

```bash
cd bridge && PYTEST -q tests/test_reader_boundary.py::test_the_protocol_promises_a_result_envelope_not_a_bare_collection
```

Expected failure: the annotations still render as `list[NormalizedConversation]`
and `list[NormalizedMessage]`.

**Minimum GREEN** — change the three annotations and the docstring.

**Validate**

```bash
cd bridge && PYTEST -q && cd ../memory && PYTEST -q && cd ../shadow && PYTEST -q
```

Every suite passes. Both shipped sources still return
lists at this point and both still satisfy the runtime protocol check; this is
intended and temporary.

**Commit** — `feat(bridge): the reader protocol promises a result envelope`

---

### P8 — Every collection consumer survives the envelope

**Files**
- modify `bridge/wechat_companion_mcp.py`
- modify `bridge/tests/test_reader_boundary.py`

**Why this comes before the sources change.** §6.1 row 2:
`wechat_companion_mcp.get_messages` reads `ordered[0].sequence` (line 254) to
compute `next_before_sequence`, and `ReadResult` deliberately has no
`__getitem__`. That is **the only production site in the repository that would
break**, and if a source changed shape first this line would raise and the suite
would go red mid-stage. Making the three tool bodies shape-tolerant **first**
keeps every commit green: the same code works against a `list` today and a
`ReadResult` after P9/P10.

**Audit rows closed by this task.** §6.1 rows 1, 2 and 3 — the three MCP tool
bodies — every one of which is exercised by the RED tests below against a
`ReadResult`-returning stub. Rows 4–6 close at P9, rows 7–8 at P11. **No row is
left to a flag day**; every row names the task that closes it and the test that
proves it.

**Interface produced** — no interface change. Inside each of the three tool
bodies, the value returned by the source is materialised once,

```python
rows = tuple(source.list_conversations(capped))      # and the two others
```

and every later use — `len(rows)`, the payload comprehension, and
`rows[0].sequence` — reads that tuple. **No tool signature, no tool count, no
payload key and no log line changes.**

**Depends on** P6, P7.

**RED tests** — in `bridge/tests/test_reader_boundary.py`, a small stub source
that returns `ReadResult` values built from the boundary types:

- `test_the_tools_serve_a_result_envelope_unchanged` — for each of the three
  tools, the payload produced from a `ReadResult`-returning source is **equal**
  to the payload produced from an equivalent list-returning source, key for key.
- `test_paging_state_survives_a_result_envelope` — `get_messages` returns the
  correct `next_before_sequence` from a `ReadResult`, which is the line that
  would otherwise raise.

**Prove RED**

```bash
cd bridge && PYTEST -q tests/test_reader_boundary.py -k "result_envelope_unchanged or paging_state_survives"
```

Expected failure: `TypeError: 'ReadResult' object is not subscriptable`.

**Minimum GREEN** — the three one-line materialisations and the `rows[0]` fix.
Nothing else in the module is touched: `clamp`, `unavailable`, `log` and the
four tool registrations are unchanged.

**Validate**

```bash
cd bridge && PYTEST -q && cd ../shadow && PYTEST -q
```

Every suite passes, including
`test_the_tool_surface_is_exactly_the_four_tools` and
`test_no_reader_output_reaches_stdout`, both unchanged.

**Commit** — `refactor(bridge): the tools read a result envelope or a list`

*(Title changed from "the MCP bridge stops indexing the read result": the task's
scope is the whole consumer audit of §6, of which the indexing site is the one
production breakage.)*

---

## Stage 4 — Existing sources author coverage (spec §9, M4)

### P9 — `RionReaderAdapter` authors coverage; `has_more` is preserved

**Files**
- modify `bridge/rion_reader_adapter.py`
- modify `bridge/tests/test_reader_boundary.py`

**Interface produced** — the adapter's three collection methods return
`ReadResult[...]`. Behaviour, per spec §9.1:

- **`history` only:** the `query` object the reader returns is parsed rather
  than discarded. `has_more` true ⟹ `COVERAGE_PARTIAL`,
  `REASON_UPSTREAM_MORE`, `truncated=True`, and it is never rewritten as
  `REASON_CALLER_LIMIT`.
- `next_offset` is retained as the adapter's own paging state (a private
  attribute) and **never** placed in `ReadCoverage`.
- **`sessions` carries no `query` at all** (see §0.6). `list_conversations`
  therefore asks for `caller_limit + 1` and measures truncation from the
  sentinel: present ⟹ `COVERAGE_PARTIAL` + `REASON_CALLER_LIMIT` +
  `truncated=True`, with the sentinel dropped before the public
  `ReadResult`; absent ⟹ the oversized unfiltered request exhausted, and
  the read may be `COVERAGE_COMPLETE`.
- `get_recent_messages`'s bounded sweep — capped at
  `RECENT_CONVERSATION_SCAN_LIMIT = 50` — reports `COVERAGE_PARTIAL`,
  `REASON_SOURCE_LIMIT`, `truncated=True` whenever the cap was reached or any
  visited conversation was itself truncated. This is the defect from spec §1.1:
  a three-message answer to a two-hundred-message request must never be
  complete.
- The caller's `limit` being filled ⟹ `REASON_CALLER_LIMIT`, `truncated=True`.
- `COVERAGE_COMPLETE` **only** when the upstream result set is exhausted within
  the requested window **and** no internal bound was hit; with items, reason
  `REASON_FULL_WINDOW_OBSERVED`; with none, reason `REASON_EMPTY_WINDOW`.
- Freshness is `ReadFreshness.UNKNOWN` unless the reader supplied a newest
  moment to compare against; there is no clock comparison anywhere.
- Every existing `MessageSourceError` path is unchanged — a hard refusal still
  raises and produces no envelope (spec §11).

**Depends on** P6, P7, P8.

**RED tests** — T-13 and half of T-11, in `bridge/tests/test_reader_boundary.py`:

- `test_upstream_has_more_is_preserved_as_partial_coverage`
- `test_next_offset_never_appears_in_coverage` — asserts `next_offset` is absent
  from every `ReadCoverage` field, by scanning the dataclass's values.
- `test_the_bounded_conversation_sweep_reports_its_own_limit` — a stub reader
  with more than `RECENT_CONVERSATION_SCAN_LIMIT` conversations; coverage is
  partial with `source_limit`.
- `test_a_short_answer_to_a_large_request_is_not_complete` (T-11) — three items
  returned for a limit of 200, with an internal bound hit; asserts **not**
  `observed_complete`.
- `test_an_exhausted_window_is_complete` — `has_more` false, no internal bound,
  reason `full_window_observed`.
- `test_an_empty_exhausted_window_is_a_trustworthy_empty` — reason
  `empty_window`, zero items, complete.

Six more that P9 owes because `sessions` has no pagination metadata (§0.6),
all against the **query-less** `SESSIONS` fixture:

- the `SESSIONS` fixture **still carries no `query` object**, asserted
  directly. It is load-bearing evidence about the real reader, not an
  oversight, and P9 must not complete it into something the gated revision
  never emits.
- the adapter **asks `sessions` for one more row than the caller wanted**,
  asserted from the stub's recorded argument vector rather than from the
  answer.
- **sentinel present** ⟹ `observed_partial` + `caller_limit` + `truncated`,
  and the public `ReadResult` still carries exactly `caller_limit` items,
  with `coverage.item_count == len(items)`.
- **sentinel absent** ⟹ `observed_complete` + `full_window_observed`,
  `truncated=False`.
- **empty sessions** ⟹ `observed_complete` + `empty_window`, `items == ()`.
- the recent sweep's sentinel at `RECENT_CONVERSATION_SCAN_LIMIT + 1` maps
  to aggregate `source_limit`, while enumeration that exhausts below that
  bound leaves the aggregate free to be complete once child history
  coverage agrees.

And one negative guard: **no generic `len(rows) < limit` completeness rule
exists**. A short answer is evidence only because an extra row was
deliberately requested and did not come back; the same short answer without
the overfetch proves nothing, and no code path may treat it as though it did.

Existing tests in this file that index or compare adapter results against lists
(`test_conversations_are_normalized`, `test_messages_are_normalized_in_order`,
`test_a_wal_resident_zstd_message_normalizes_to_its_decoded_text`,
`test_a_conversation_with_no_messages_returns_an_empty_answer`,
`test_recent_messages_filter_and_sort`, and the adapter refusal tests) are
updated in this same commit to read `.items` — planned migration, named in §4.

**Prove RED**

```bash
cd bridge && PYTEST -q tests/test_reader_boundary.py -k "has_more or next_offset or bounded_conversation_sweep or short_answer_to_a_large_request or exhausted_window"
```

Expected failure: `AttributeError: 'list' object has no attribute 'coverage'`.

**Minimum GREEN** — thread the `query` object out of `_invoke`, track the sweep
bound, build one `ReadCoverage` per method, wrap in `ReadResult`.

**Validate**

```bash
cd bridge && PYTEST -q && cd ../shadow && PYTEST -q
```

Every suite passes.

**Commit** — `feat(bridge): the reader adapter states its own coverage`

---

### P10 — `StoreMessageSource` authors conservative coverage

**Files**
- modify `bridge/store_access.py`
- modify `bridge/tests/test_reader_boundary.py`

**Interface produced** — the visual store's three collection methods return
`ReadResult[...]`. Behaviour, per spec §9.2 (T-12):

- The **default** for any scope OCR cannot prove complete is `COVERAGE_PARTIAL`
  with `ReadFreshness.UNKNOWN`. Absence of a message in the store is not
  evidence the message does not exist.
- `COVERAGE_COMPLETE` **only** where the caller's limit was not filled **and**
  the store's own newest recorded moment for the scope was reached — established
  by one additional bounded query against the store's `first_observed_at`, not
  by item count.
- A genuinely empty, accounted-for window is `COVERAGE_COMPLETE` with
  `REASON_EMPTY_WINDOW` — the visual path can and must be able to produce a
  trustworthy empty.
- A filled limit is `REASON_CALLER_LIMIT`, `truncated=True`.
- `BridgeUnavailable` / `MessageSourceError` paths are unchanged.

**Depends on** P9 (so the two sources migrate in a fixed order and a bisect
lands on one source at a time).

**RED tests** — T-12, in `bridge/tests/test_reader_boundary.py`:

- `test_the_visual_store_never_reports_complete_from_item_count_alone`
- `test_the_visual_store_reports_complete_only_at_its_newest_moment`
- `test_the_visual_store_can_produce_a_trustworthy_empty`
- `test_a_filled_limit_on_the_visual_store_is_partial`

Existing store-backed tests in this file are updated to `.items` in the same
commit.

**Prove RED**

```bash
cd bridge && PYTEST -q tests/test_reader_boundary.py -k "visual_store or filled_limit_on_the_visual_store"
```

Expected failure: `AttributeError: 'list' object has no attribute 'coverage'`.

**Minimum GREEN** — the newest-moment query, one `ReadCoverage` per method, the
`ReadResult` wrap.

**Validate**

```bash
cd bridge && PYTEST -q && cd ../memory && PYTEST -q && cd ../shadow && PYTEST -q
```

Every suite passes. **`memory` passing here is expected, not incidental**: its
ingestor consumes the result by iteration (`list(source.get_messages(...))`) and
still infers coverage from length. That inference is now demonstrably wrong and
is deleted in P11.

**Commit** — `feat(bridge): the visual store states conservative coverage`

---

## Stage 5 — Memory consumes source-authored coverage (spec §10, M5)

### P11 — `memory_ingest` consumes source coverage; the length inference is deleted

**This is the task that fixes the live Memory false-complete defect.**

**Files**
- modify `memory/memory_ingest.py`
- modify `memory/tests/test_memory_ingest.py`
- modify `memory/tests/test_memory_freshness.py`
- modify `memory/tests/test_memory_sync.py`
- modify `memory/tests/test_memory_worker.py`
- modify `memory/tests/test_layering.py`

The four stub sources enumerated in §6.2 — `test_memory_ingest.FakeSource`,
`test_memory_freshness.Refusing`, and the stubs in `test_memory_sync.py` and
`test_memory_worker.py` — all **produce** bare lists today and must all author
`ReadResult` in this commit, because from here the ingestor requires
`.coverage`. Missing one is the most likely way this task goes red; §6.2 is the
checklist.

**Interface produced** — `MemoryIngestor.ingest_from_source` keeps its exact
signature. Inside it:

- `complete = len(messages) < message_limit` is **deleted**. Not demoted to a
  fallback, not kept behind a flag (spec §10, D-031 item 6).
- The source's `ReadResult.coverage` supplies **completeness and its
  explanation**, and nothing else is inferred:

  | `CoverageRecord` field | Source | Rule |
  |---|---|---|
  | `status` | `coverage.status` | source-authored, always |
  | `reason` | `coverage.reason` | source-authored, always |
  | `message_count` | `coverage.item_count` | source-authored, always |
  | `window_end` | `coverage.complete_through` when the status is complete, else `coverage.observed_through`; **when the source states neither, the existing `max(stamps)`** | source-authored **where available**, existing observation evidence otherwise |
  | `window_start` | `coverage.requested_start` when the source was given an explicit lower bound; **otherwise the existing `min(stamps)`** | see below |
  | `conversation_canonical_id` | unchanged | — |

- **`window_start` must not regress to `None`.** This is the correction the
  architecture review caught, and it matters because of what the store does with
  the field. `memory_store._window_contains` treats `None` as *unbounded*: a
  record with `window_start=None` and `window_end=None` **covers every question
  ever asked**, including windows the read never looked at. Today's ingestor
  derives `window_start = min(stamps)` / `window_end = max(stamps)` from the
  returned messages, and those bounds are genuine observation evidence that
  *constrains* the claim. Neither shipped source is handed an explicit requested
  window by `ingest_from_source` today, so mapping `window_start ←
  requested_start` unconditionally would replace a real lower bound with `None`
  and **widen every stored coverage claim to all of time** — turning a
  false-complete defect into a false-*scope* defect, which is worse. The rule is
  therefore: **take the source's bound when the source has one; keep the
  existing observed bound when it does not; never widen.**
- `observed_through` and `complete_through` *do* become source-authored where
  the source states them, which is the point of T-14 and is a genuine
  improvement: a source that looked through a moment later than its newest
  returned item may say so, and a trustworthy empty read — which has no stamps
  at all — can now carry real bounds where today it carries none.
- A read whose status is `COVERAGE_NOT_OBSERVED` writes **no coverage row** —
  the absence of a row is how "nothing was observed" is recorded, and
  `memory_store.COVERAGE_STATES` excludes that status by design (spec §10).
- `memory/memory_freshness.py` needs **no change**: `source_freshness` already
  derives `observed_through` / `complete_through` from each row's `window_end`
  plus its status, and its existing fall-back to the run's `completed_at` for a
  `None` `window_end` is untouched.
- `REASON_LIMIT_REACHED` stays defined and exported for the store's existing
  rows and callers; the ingestor no longer produces it, because the source's own
  `caller_limit` / `source_limit` token is the more precise statement.
- The `MessageSourceError` path — failed run, `unavailable` row,
  `REASON_SOURCE_ERROR`, nothing written — is **unchanged** (spec §11).

**What this task does and does not replace, in one line.** Source-authored
coverage replaces the **completeness inference** — status, reason, truncation —
and supplies `observed_through` / `complete_through` where the source knows
them. It does **not** replace legitimate observation-window evidence the
ingestor already derives. No new type is introduced; there is still no
`ReadWindow`.

**Depends on** P9 and P10 (both shipped sources must author coverage before the
consumer requires it).

**RED tests** — T-14. `FakeSource` in `memory/tests/test_memory_ingest.py` is
changed to return `ReadResult` values, then:

- `test_a_short_answer_is_not_recorded_complete_when_the_source_says_partial` —
  the defect, pinned: a source returns 3 messages for `message_limit=200` with
  coverage `observed_partial` / `source_limit`; the recorded verdict is
  **partial**, and its reason is `source_limit`. *Under today's code this test
  records `observed_complete`.*
- `test_memory_records_the_sources_status_and_reason` — the stored row carries
  the source's own tokens, not one invented by memory.
- `test_a_not_observed_read_writes_no_coverage_row` — the store's coverage row
  count is unchanged and `assess_coverage` answers `not_observed`.
- `test_the_boundaries_are_copied_from_coverage_not_derived_from_items` — a
  source states `observed_through` later than its newest returned item;
  `source_freshness(...).observed_through` equals the source's value.
- `test_the_length_inference_is_gone_from_the_ingestor` — `ast` scan of
  `memory/memory_ingest.py` asserting no comparison of a `len(...)` call against
  a name containing `limit`. A structural assertion, so the branch cannot come
  back as a fallback.

Three further RED tests exist solely to pin the `window_start` correction, and
each must be seen failing against a naive `window_start ← requested_start`
mapping before that mapping is written:

- `test_a_partial_source_stays_partial_below_the_caller_limit` — a source
  returns 3 items for `message_limit=200` and authors `observed_partial` /
  `source_limit` / `truncated=True`; the stored row is partial, with the
  source's reason. *(The same property as the defect test above, asserted
  directly against the stored row rather than through `assess_coverage`.)*
- `test_an_unbounded_read_keeps_its_observed_window_start` — a source authors
  coverage with `requested_start is None` and `requested_end is None` while
  returning messages with real timestamps; the stored row's `window_start`
  equals `min` of those observed timestamps and is **not** `None`. Against a
  naive mapping this fails with `None`.
- `test_an_unbounded_read_does_not_claim_coverage_it_never_looked_at` — with
  that same row stored, `assess_coverage(start=<well before the earliest
  observed message>, end=<the same>)` does **not** return `observed_complete`
  for that conversation, while a query inside the observed window does return
  the source's status. This is the assertion that would catch a silent widening
  even if the `window_start` test above were later weakened.

The existing `test_a_filled_limit_is_recorded_as_partial_not_complete` and
`test_reading_a_whole_source_records_complete_coverage` are updated to have
`FakeSource` author the coverage they are asserting on. The `Refusing` stub in
`memory/tests/test_memory_freshness.py` and the stub sources in
`test_memory_sync.py` and `test_memory_worker.py` are updated for the new return
shape; their assertions are unchanged, because neither the refusal path nor the
sync/worker behaviour changes.

**The acceptance this task must prove, restated so it cannot drift:** a source
returns **fewer items than the caller's limit** while declaring
`observed_partial` / `source_limit` / `truncated = True`, and Memory records
**that partial coverage**, unchanged, with the source's own reason token. No
fallback equivalent to `len(messages) < message_limit` may determine source
completeness anywhere — not in the ingestor, not behind a flag, not as a
last-resort default. The AST guard stays as long as it is the smallest robust
enforcement of that.

**Prove RED**

```bash
cd memory && PYTEST -q tests/test_memory_ingest.py -k "short_answer_is_not_recorded_complete or records_the_sources_status or not_observed_read_writes_no_coverage_row or copied_from_coverage or length_inference_is_gone"
```

Expected failures: the first records `observed_complete` where `observed_partial`
is asserted — **this is the defect reproduced as a failing test, and it must be
seen failing before the fix** — and the AST test finds the `len(messages) <
message_limit` comparison.

Then, for the window correction:

```bash
cd memory && PYTEST -q tests/test_memory_ingest.py -k "keeps_its_observed_window_start or does_not_claim_coverage_it_never_looked_at"
```

These two must be run **twice**: once against a deliberate naive
`window_start ← coverage.requested_start` mapping, where they fail with a `None`
lower bound and a widened claim, and once against the rule in the table above,
where they pass. Seeing the naive mapping fail is what proves the tests pin the
regression rather than merely describing it.

**Minimum GREEN** — delete the inference; map `ReadCoverage` onto
`CoverageRecord` per the table above, including both "source bound when stated,
existing observed bound otherwise" fall-backs; skip the row for `not_observed`.
Before writing any of it, re-read `memory/memory_ingest.py`
(`ingest_from_source`), `memory/memory_store.py` (`CoverageRecord`,
`assess_coverage`, `_window_contains`, `_windows_overlap`) and
`memory/tests/test_coverage_composition.py`, and confirm the coverage-row
semantics have not moved since this plan was written.

**Validate**

```bash
cd memory && PYTEST -q && cd ../bridge && PYTEST -q && cd ../wechatdb && PYTEST -q && cd ../shadow && PYTEST -q
```

Every suite passes.

**Commit** — `fix(memory): record the coverage a source stated, never a length`

---

## Stage 6 — Orchestration inside the isolated provider (spec §8, M6)

Everything from here is **clean-room, provider-internal, unwired and untouched
by product core**. No task in this stage edits `wechatdb/parser.py` or
`wechatdb/msg_types.py`, and none is imported by anything under `bridge/`,
`memory/`, `shadow/`, `ai/` or `core/`.

**Where the code lives.** `wechatdb` *is* the isolated candidate provider.
Orchestration is a subpackage of it, with the existing parser as its leaf:

```
wechatdb/
├── __init__.py          # leaf-parser exports only; does NOT import provider/
├── parser.py            # UNCHANGED
├── msg_types.py         # UNCHANGED
├── pytest.ini
├── provider/
│   ├── __init__.py
│   ├── discovery.py     # ShardDiscovery          (P13)
│   ├── routing.py       # ShardRouter             (P14)
│   ├── identity.py      # IdentityResolver        (P15)
│   ├── result.py        # ProviderResult          (P16)
│   └── provider.py      # ShardedMessageProvider  (P17a, extended by P17b)
└── tests/
    ├── conftest.py      # UNCHANGED
    ├── fixtures.py      # UNCHANGED
    ├── test_parser.py   # UNCHANGED
    └── provider/
        ├── __init__.py
        ├── conftest.py
        ├── fixtures.py          # multi-part synthetic fixtures (P12)
        ├── test_fixtures.py     # (P12)
        ├── test_discovery.py    # (P13)
        ├── test_routing.py      # (P14)
        ├── test_identity.py     # (P15)
        ├── test_result.py       # (P16)
        ├── test_provider_reads.py       # (P17a)
        └── test_provider_degradation.py # (P17b)
```

**No new top-level package is created.** Three consequences follow, and all
three are simplifications:

1. The existing isolation guard
   `test_no_product_module_imports_the_candidate_schema_provider` already fails
   on any product import of `wechatdb` **at any depth**, so it covers
   `wechatdb.provider` on the day that package appears, with no generalisation
   and no second package name to keep in step.
2. `wechatdb/pytest.ini` already carries `testpaths = tests`, so the provider
   suite is collected by the existing `wechatdb` suite. No fifth test package
   and no provider-specific validation command is introduced; the full
   validation chain stays exactly the four suites of §0.3 — `bridge`, `memory`,
   `wechatdb`, `shadow`.
3. The dependency direction is *product core → generic boundary ← isolated
   wechatdb provider → wechatdb parser*, not *product core → wechatprovider →
   wechatdb*. Nothing sits on top of `wechatdb`.

**The one rule this layout must not lose.** `wechatdb/parser.py` is
dependency-free today and must stay importable without dragging in orchestration
— the provider imports `message_source` from `bridge/`, and the leaf parser must
not inherit that. `wechatdb/__init__.py` therefore **does not import
`wechatdb.provider`**, and P12 adds the guard that keeps it that way.

---

### P12 — `wechatdb/provider/` skeleton, synthetic fixtures, isolation guards

**This is the first task whose code imports the `wechatdb` parser, and the first
to add anything under `wechatdb/provider/`.**

**Files**
- new `wechatdb/provider/__init__.py`
- new `wechatdb/tests/provider/__init__.py`
- new `wechatdb/tests/provider/conftest.py`
- new `wechatdb/tests/provider/fixtures.py`
- new `wechatdb/tests/provider/test_fixtures.py`
- new `wechatdb/tests/provider/test_isolation.py`

**No file under `bridge/` is modified by this task.** See the guard-placement
rule below — this is a deliberate design decision, not an omission.

**Interface produced**

- `wechatdb/provider/__init__.py` — a docstring stating what the subpackage is
  and is not: orchestration around this package's own leaf parser, wired to
  nothing, promoted by nothing, containing no acquisition capability. It
  re-exports the four component names as P13–P16 add them. **`wechatdb/__init__.py`
  is not modified by this task or any later one.**
- `wechatdb/tests/provider/conftest.py` — puts the repository root and `bridge/`
  on `sys.path`, the same flat cross-tree style `memory/memory_ingest.py`
  already uses and `wechatdb/tests/conftest.py` already uses for the root. No
  new import mechanism.
- `wechatdb/tests/provider/fixtures.py` — a **multi-part** synthetic fixture
  builder: several temporary SQLite databases, each with `Msg_<32 hex>` tables
  in the layout `wechatdb.parser` reads (`local_id` and `create_time` mandatory,
  the optional columns where a case needs them) plus an optional `Name2Id`,
  populated with invented text, fixture identifiers and chosen timestamps. It
  supports every shape T-1…T-10 needs: a readable part, a part matching no known
  name shape, a part that will not open, a part that opens with an unrecognised
  schema, a part with no establishable time bounds, and one conversation
  spanning several parts. It is written from the parser's documented column
  contract. **Nothing is copied from `wx-cli-again.`** It is a separate module
  from `wechatdb/tests/fixtures.py` — the single-database parser fixtures stay
  exactly as they are, and neither suite's helpers constrain the other.

**Depends on** P6 (the boundary types the provider will construct).

**RED tests**

- `wechatdb/tests/provider/test_fixtures.py::test_a_synthetic_part_is_parseable_by_the_leaf_parser`
  — builds a fixture database and asserts `wechatdb.parse_conversation` returns
  the expected `MessageRecord`s. The smoke test that the builder speaks the
  parser's actual dialect.
- `wechatdb/tests/provider/test_fixtures.py::test_a_multi_part_fixture_really_has_several_parts`
  — a conversation's messages are split across parts, and no part alone holds
  them all. Without this, every later multi-part test could pass vacuously.
- `wechatdb/tests/provider/test_fixtures.py::test_no_fixture_carries_a_real_path_or_identifier`
  — scans the fixture module's source for `Library`, `Containers`, any absolute
  path outside `tmp_path`, and any `wxid_` not carrying an obviously synthetic
  fixture prefix.
- `wechatdb/tests/provider/test_isolation.py::test_importing_the_leaf_parser_does_not_import_the_provider`
  — imports `wechatdb` in a subprocess with a clean `sys.modules` and asserts
  `wechatdb.provider` is **not** in `sys.modules` afterwards. This is what keeps
  the leaf parser dependency-free.
- `wechatdb/tests/provider/test_isolation.py::test_the_isolated_provider_imports_no_product_layer`
  — `ast` scan asserting no module under `wechatdb/` imports the memory layer,
  `shadow`, `ai` or `core`. **This test lives in `wechatdb/tests/`, not in
  `bridge/tests/`, and that placement is load-bearing** — see below.

**Guard placement: the raw-substring collision, designed around rather than
discovered.** `memory/tests/test_layering.py::test_the_bridge_never_references_the_memory_layer`
scans **every** `*.py` under `bridge/` — *including `bridge/tests/`* — as **raw
text**, and fails if any of `memory_store`, `memory_ingest`, `memory_retrieval`,
`memory_query`, `memory_sync`, `memory_consent`, `memory_freshness` or
`wechat_memory_mcp` appears anywhere in it. Writing a provider-isolation guard
into `bridge/tests/test_reader_boundary.py` that spells those module names as
forbidden-dependency literals would therefore **fail the Memory layering test on
the strength of the guard's own source text** — a false positive produced by the
new test, not by any real dependency. Three rules follow, and all three are part
of this task's acceptance:

1. **The provider→product-layer guard lives in `wechatdb/tests/provider/`.**
   `test_layering.py` scans `bridge` and `shadow`, never `wechatdb`, so the
   collision cannot arise there. This also puts the guard beside the thing it
   guards.
2. **It tests the import direction, not a word list.** It reads each module's
   top-level imported names from the syntax tree — the same `ast` technique
   `test_layering.py` itself uses for `shadow` — and rejects any whose first
   dotted segment is the memory layer, `shadow`, `ai` or `core`. Matching is by
   **prefix on the segment** (`name.split(".")[0].startswith("memory")`), so the
   guard never needs to spell a full module name even in its own tree.
3. **No existing architecture test is weakened.**
   `test_the_bridge_never_references_the_memory_layer` keeps its raw-text scan
   and its full name list;
   `test_no_product_module_imports_the_candidate_schema_provider` is **not
   changed at all** — `wechatdb` is already its constant and already matches at
   any depth, so it covers `wechatdb.provider` on the day that package appears.
   That is one of the reasons the provider lives inside `wechatdb`.

T-16's third clause — *"`message_source` imports neither"* — is already sealed
by T-15's allowed-import set and needs no new test.

**Prove RED**

```bash
cd wechatdb && PYTEST -q tests/provider
```

Expected failure: the directory does not exist.

```bash
cd wechatdb && PYTEST -q tests/provider/test_isolation.py
```

Expected failure: the test module does not exist. Then, with it added and a
deliberate temporary import of a memory-layer module placed in
`wechatdb/provider/__init__.py`, it must fail — proving the guard bites — before
that line is removed.

And the collision itself must be proven absent rather than assumed:

```bash
cd memory && PYTEST -q tests/test_layering.py
```

This must pass **after** the new guard exists. If it does not, the guard has
been written in the wrong place or with literal module names in its source, and
rule 1 or rule 2 above has been broken.

**Minimum GREEN** — create the subpackage, the test package, the conftest, the
fixture builder and the five tests. Add **no** bridge test and modify **no**
bridge file.

**Validate**

```bash
cd bridge && PYTEST -q && cd ../wechatdb && PYTEST -q && cd ../memory && PYTEST -q && cd ../shadow && PYTEST -q
```

Every suite passes. Two of them matter specifically here: `memory`, because
`test_layering.py` is the test the guard placement exists to keep green, and
`wechatdb`, because the provider tests are collected by its **existing** run —
this task adds a test *directory*, never a fifth test package.

**Commit** — `feat(wechatdb): a provider subpackage, synthetic multi-part fixtures, and the guards that keep it isolated`

---

### P13 — `ShardDiscovery` — an inventory that never drops an entry

**Files**
- new `wechatdb/provider/discovery.py`
- new `wechatdb/tests/provider/test_discovery.py`
- modify `wechatdb/provider/__init__.py`

**Interface produced** (provider-internal vocabulary; none of it crosses into a
generic type)

```python
SHARD_KNOWN       = "known"
SHARD_READABLE    = "readable"
SHARD_UNKNOWN     = "unknown"
SHARD_UNAVAILABLE = "unavailable"
SHARD_STATES = frozenset({
    SHARD_KNOWN, SHARD_READABLE, SHARD_UNKNOWN, SHARD_UNAVAILABLE,
})


@dataclass(frozen=True, slots=True)
class ShardEntry:
    # One part of a composite source, as handed to the provider. `handle` is
    # opaque to Discovery: only the opener knows what to do with it, and it is
    # never copied into ShardFacts.
    name: str
    handle: object


@runtime_checkable
class ShardLocator(Protocol):
    def entries(self) -> tuple[ShardEntry, ...]: ...


class ExplicitShardLocator:
    # Lists exactly the entries it was constructed with. Searches nothing.
    def __init__(self, entries: Sequence[ShardEntry]) -> None: ...
    def entries(self) -> tuple[ShardEntry, ...]: ...


@runtime_checkable
class ShardOpener(Protocol):
    # The only thing that turns an entry into a connection.
    def open(self, entry: ShardEntry) -> sqlite3.Connection: ...


class ReadOnlySqliteOpener:
    # explicitly supplied handle -> SQLite URI -> mode=ro, uri=True.
    # Never immutable=1. No search, no default root, no path discovery, no
    # copy, no checkpoint, no decryption, no SQLCipher, no process access.
    def open(self, entry: ShardEntry) -> sqlite3.Connection: ...


def shard_key(name: str) -> str:
    # A stable opaque digest of an entry name. Never the name, never a path.


@dataclass(frozen=True, slots=True)
class ShardFacts:
    key: str
    state: str                    # one of SHARD_STATES
    bounds_established: bool
    min_timestamp: int | None
    max_timestamp: int | None
    tables: tuple[str, ...] = ()  # provider-internal only


class ShardDiscovery:
    def __init__(self, locator: ShardLocator, opener: ShardOpener) -> None: ...
    def catalogue(self) -> dict[str, ShardFacts]:
        # Pass one: classifies by name shape. Opens nothing. Produces only
        # KNOWN or UNKNOWN -- never READABLE.
    def probe(self, inventory: dict[str, ShardFacts]) -> dict[str, ShardFacts]:
        # Pass two: asks the opener for each KNOWN entry, recognises schema,
        # takes time bounds. UNKNOWN is left alone. The returned key set
        # equals the input's.
```

**The two-pass state transition**, which is the contract every test below pins:

```
locator.entries()
        │
        ▼
catalogue()          recognised name shape  → KNOWN
                     unrecognised name      → UNKNOWN
                     opens nothing
        │
        ▼
probe()              UNKNOWN                              → stays UNKNOWN, not opened
                     KNOWN + read-only open + recognised schema → READABLE
                     KNOWN + open refusal                 → UNAVAILABLE
                     KNOWN + opened, schema unrecognised  → UNAVAILABLE
                     key set identical to its input
```

**`ShardFacts` invariants by state.** KNOWN, UNKNOWN and UNAVAILABLE all carry
`bounds_established=False`, `min_timestamp=None`, `max_timestamp=None`,
`tables=()` — no positive schema or bounds claim exists for any of them, for
three different reasons (not opened yet; not characterisable; probed and not
readable). READABLE carries the recognised conversation-table set in `tables`,
and bounds that are either **established** (`True`, both endpoints non-`None`)
or **absent** (`False`, both `None`) for a legitimately readable but empty part.
One endpoint is never guessed from the other.

Behaviour, per spec §8.1: the locator is injected and there is **no**
implementation that searches a filesystem; the opener is injected and is the
only thing that opens; the shipped opener is `mode=ro` and never `immutable`
(spec §12.5); time bounds are normalised through `wechatdb.normalise_timestamp`;
bounds are either established or absent and are never guessed; an entry nobody
can characterise counts toward the unknown tally for **every** role. `shard_key`
is stable, opaque, derived from the entry name, and never the raw name or a path;
`ShardFacts` holds no raw name, no handle and no path.

**Name-shape scope.** The project has verified one real `message_0` instance
and nothing more. The synthetic multi-part tests may exercise the documented
message-part naming convention, but that is synthetic orchestration evidence,
not a claim that every real installation has those parts. P13 is
message-shard discovery only: no contact or session role classification.

**Depends on** P12.

**RED tests** — `wechatdb/tests/provider/test_discovery.py`:

*Catalogue:*

- `test_a_recognised_name_is_catalogued_as_known_not_readable` — a readable
  synthetic part catalogues as `KNOWN`; **no** catalogue result is `READABLE`.
- `test_an_uncharacterisable_entry_stays_in_the_inventory_as_unknown`.
- `test_the_catalogue_pass_opens_nothing` — the injected opener fails the test
  if invoked, and the entry's handle is never touched.
- `test_nothing_is_claimed_before_probing` — every catalogue result has
  `bounds_established=False`, both timestamps `None`, `tables=()`.

*Probe:*

- `test_a_known_readable_part_becomes_readable` — `KNOWN` + valid part →
  `READABLE` with the conversation-table set in `tables`.
- `test_a_part_that_will_not_open_is_unavailable_not_absent` — `KNOWN` + an
  opener that raises → `UNAVAILABLE`, key still present.
- `test_a_part_with_an_unrecognised_schema_is_unavailable`.
- `test_an_unknown_entry_is_not_probed` — the opener is never asked for it and
  it remains `UNKNOWN`.
- `test_probing_never_changes_how_many_parts_there_are` — `probe(...).keys() ==
  catalogue().keys()`, including for entries that will not open.
- `test_a_readable_empty_part_has_absent_bounds` — `READABLE`,
  `bounds_established=False`, both `None`.
- `test_mixed_second_and_millisecond_times_normalise_before_bounding` — a part
  whose rows mix the two does not report a maximum in the far future.
- `test_bounds_are_established_or_absent_never_guessed`.
- `test_a_part_is_identified_by_an_opaque_digest_never_by_its_name` —
  `shard_key` is stable across calls; the entry name, handle and any path appear
  in no `ShardFacts` field.

*Read-only boundary (replaces the former requirement that Discovery itself
contain a SQLite URI, which an injected connection made unenforceable):*

- `test_discovery_opens_only_through_the_injected_opener` — `ast` scan of
  `discovery.py`: `ShardDiscovery` never calls `sqlite3.connect`; a counting
  opener records exactly one `open` per `KNOWN` entry.
- `test_the_read_only_opener_is_mode_ro_and_never_immutable` — the concrete
  opener is independently pinned: `uri=True`, the URI carries `mode=ro`, and
  `immutable` appears nowhere in the module.
- `test_a_write_through_the_opener_is_refused` — functional: an `INSERT` on a
  connection the opener returned raises `sqlite3.OperationalError`, and the
  synthetic file is byte-identical afterwards.
- `test_no_provider_component_searches_for_a_path` — `ast` scan of every
  module under `wechatdb/provider/` for `glob`, `rglob`, `iterdir`, `listdir`,
  `walk`, `Path.home`, `expanduser`, and any string literal containing a path
  separator. P12's acquisition guard is not weakened; this is the same
  property asserted from the discovery side.

A test that needs an unopenable fixture injects a raising opener through
`ShardOpener`; it never weakens the production opener to get one.

**Prove RED**

```bash
cd wechatdb && PYTEST -q tests/provider/test_discovery.py
```

Expected failure: collection error, `ModuleNotFoundError: wechatdb.provider.discovery`.

**Minimum GREEN** — the four constants, the dataclasses and protocols, the
small read-only opener, the two passes. `probe` asks the injected opener for each
`KNOWN` entry inside `try/except (sqlite3.Error, <the opener's refusal>)` and
downgrades to `SHARD_UNAVAILABLE`; it never opens anything itself, never touches
`UNKNOWN`, and never lets an exception escape as a dropped key.

**Validate**

```bash
cd wechatdb && PYTEST -q && cd ../bridge && PYTEST -q && cd ../shadow && PYTEST -q
```

**Commit** — `feat(wechatdb): shard discovery that never drops a part`

---

### P14 — `ShardRouter` — total accounting, ordering, stop classification

**Files**
- new `wechatdb/provider/routing.py`
- new `wechatdb/tests/provider/test_routing.py`
- modify `wechatdb/provider/__init__.py`

**Interface produced**

```python
EXCLUDED_OUT_OF_WINDOW       = "out_of_window"
EXCLUDED_NOT_READABLE        = "not_readable"
EXCLUDED_CONVERSATION_ABSENT = "conversation_absent"
EXCLUSION_CAUSES = frozenset({
    EXCLUDED_OUT_OF_WINDOW, EXCLUDED_NOT_READABLE, EXCLUDED_CONVERSATION_ABSENT,
})

STOP_EXHAUSTED = "exhausted"
STOP_SAFE      = "safe"
STOP_UNSAFE    = "unsafe"
STOP_KINDS = frozenset({STOP_EXHAUSTED, STOP_SAFE, STOP_UNSAFE})


@dataclass(frozen=True, slots=True)
class RoutePlan:
    visit: tuple[str, ...]                     # shard keys, in visit order
    exclusions: tuple[tuple[str, str], ...]    # (shard key, cause)

    def accounts_for(self, inventory: Mapping[str, ShardFacts]) -> bool:
        # Every inventory key appears exactly once across visit + exclusions.


class ShardRouter:
    def plan(
        self,
        inventory: Mapping[str, ShardFacts],
        *,
        requested_start: int | None,
        requested_end: int | None,
        conversation_key: str | None = None,
    ) -> RoutePlan: ...

    def classify_stop(
        self,
        plan: RoutePlan,
        inventory: Mapping[str, ShardFacts],
        *,
        visited: Sequence[str],
        requested_start: int | None,
        requested_end: int | None,
    ) -> str:  # one of STOP_KINDS
        ...
```

Behaviour, per spec §8.2: total accounting; a part whose bounds are **not
established always overlaps** and is therefore always visited; ordering is
newest-first by established maximum bound with unestablished-bounds parts sorted
**first**; a safe early stop requires that every unscanned shard is provably
outside the requested window; any other early stop is unsafe; unknown and
unavailable parts do **not** make a stop unsafe — they downgrade coverage
separately through `partial_inventory`, always.

The time-partitioning assumption stays graded **Hypothesis** with no project
evidence; if it is false the router simply never finds a safe stop, visits
everything, and the answer is still correct.

**Depends on** P13.

**RED tests** — `wechatdb/tests/provider/test_routing.py`:

- `test_every_part_is_either_visited_or_excluded_with_a_cause`
- `test_no_part_appears_twice_across_the_plan`
- `test_a_part_without_established_bounds_is_always_visited`
- `test_unestablished_bounds_are_visited_first`
- `test_visit_order_is_newest_first_by_established_maximum`
- `test_a_stop_is_safe_only_when_every_unscanned_part_is_outside_the_window`
- `test_a_stop_with_an_unbounded_unvisited_part_is_unsafe` (T-6a)
- `test_a_stop_with_an_unvisited_maximum_inside_the_window_is_unsafe` (T-6b)
- `test_unknown_and_unavailable_parts_do_not_make_a_stop_unsafe`
- `test_a_conversation_scoped_read_excludes_parts_that_do_not_hold_it`

**Prove RED**

```bash
cd wechatdb && PYTEST -q tests/provider/test_routing.py
```

Expected failure: `ModuleNotFoundError: wechatdb.provider.routing`.

**Minimum GREEN** — the constants, `RoutePlan`, `plan`, `classify_stop`.

**Validate**

```bash
cd wechatdb && PYTEST -q
```

**Commit** — `feat(wechatdb): routing that accounts for every part`

---

### P15 — `IdentityResolver` — lookup, precedence, refused ambiguity

**Files**
- new `wechatdb/provider/identity.py`
- new `wechatdb/tests/provider/test_identity.py`
- modify `wechatdb/provider/__init__.py`

**Interface produced**

```python
NAME_ROOM_MEMBER      = "room_member"
NAME_CONTACT_REMARK   = "contact_remark"
NAME_CONTACT_NICKNAME = "contact_nickname"

# Fixed precedence over distinct KINDS of name, strongest first (spec §8.4).
NAME_PRECEDENCE = (NAME_ROOM_MEMBER, NAME_CONTACT_REMARK, NAME_CONTACT_NICKNAME)


@dataclass(frozen=True, slots=True)
class NameCandidate:
    identifier: str
    kind: str                 # one of NAME_PRECEDENCE
    name: str
    room: str | None = None   # a room-member name applies only inside its room


@dataclass(frozen=True, slots=True)
class ResolvedIdentities:
    session_names: Mapping[str, str]   # table digest -> conversation username
    display_names: Mapping[str, str]   # sender identifier -> display name
    unresolved: int                    # aggregate count only


class IdentityResolver:
    def __init__(
        self,
        candidates: Sequence[NameCandidate],
        *,
        session_names: Mapping[str, str] | None = None,
    ) -> None: ...

    def resolve(self, *, room: str | None = None) -> ResolvedIdentities: ...
```

Behaviour, per spec §8.4: resolution is a **lookup, never an inference** — no
fuzzy match, no edit distance, no tokenisation, no model, no heuristic;
precedence is over distinct kinds so it is never a choice between two equally
good answers; **ambiguity is refused, never resolved** — one identifier with two
different names of the same kind resolves to no name and is simply absent from
the mapping, leaving the parser's existing fallback to the sender identifier as
the correct outcome; an unresolved identity **never raises**, never drops a
message and never invents a name — it increments `unresolved`.

`ResolvedIdentities` is exactly the `(session_names, display_names)` pair
`wechatdb.parse_conversation` already accepts. That is the entire integration
surface; **the parser is not edited** (spec §8.3, §17.14).

**Depends on** P12.

**RED tests** — T-8, in `wechatdb/tests/provider/test_identity.py`:

- `test_a_remark_beats_a_nickname`
- `test_a_room_nickname_applies_inside_that_room_and_not_outside_it`
- `test_two_conflicting_same_kind_names_resolve_to_no_name` — and neither
  candidate string appears anywhere in the returned mapping.
- `test_an_unresolved_identity_raises_nothing_and_is_counted`
- `test_resolution_is_never_a_guess` — `ast`/string scan of `identity.py` for
  `difflib`, `SequenceMatcher`, prefix matching, substring containment over
  names, case-normalised matching, and any import beyond the standard library.
- `test_the_resolver_produces_exactly_what_the_parser_accepts` — the two
  mappings are passed to `wechatdb.parse_conversation` and it accepts them.

**Prove RED**

```bash
cd wechatdb && PYTEST -q tests/provider/test_identity.py
```

Expected failure: `ModuleNotFoundError: wechatdb.provider.identity`.

**Minimum GREEN** — the constants, the three dataclasses, `resolve`.

**Validate**

```bash
cd wechatdb && PYTEST -q
```

The whole `wechatdb` suite is run, which re-proves `test_parser.py` unchanged
and therefore that the parser was not edited.

**Commit** — `feat(wechatdb): identity resolution that refuses to guess`

---

### P16 — `ProviderResult` — the pessimistic collapse into one `ReadCoverage`

**Files**
- new `wechatdb/provider/result.py`
- new `wechatdb/tests/provider/test_result.py`
- modify `wechatdb/provider/__init__.py`

**Interface produced** — the **only** place in the provider that constructs a
generic type (spec §8.5).

```python
@dataclass(frozen=True, slots=True)
class Contribution:
    # What one planned part contributed to one read.
    records: tuple[MessageRecord, ...]
    observed_through: int | None
    complete_through: int | None
    truncated: bool


@dataclass(frozen=True, slots=True)
class ProviderDiagnostics:
    # Aggregate counts and category tallies only. Never an identifier, a name,
    # a path or message content. Not part of ReadResult.
    readable: int
    unknown: int
    unavailable: int
    unresolved_identities: int


class ProviderResult:
    @staticmethod
    def message(record: MessageRecord, *, conversation_id: int) -> NormalizedMessage:
        # One record in the shape the contract already documents: the message's
        # own creation time in first_observed_at, visible_time None because no
        # rendered time was ever seen, confidence 1.0 because a decoded row is
        # exact and there is no estimator on this path.

    @staticmethod
    def collapse(
        contributions: Sequence[Contribution],
        *,
        requested_start: int | None,
        requested_end: int | None,
        caller_limit: int,
        stop: str,                     # one of routing.STOP_KINDS
        inventory_gap: bool,           # any required part unknown or unavailable
        source_newest: int | None,     # the source's own newest declared moment
    ) -> ReadCoverage: ...
```

Collapse rules, per spec §8.5 and §7.2–§7.3:

- any unknown or unavailable **required** part ⟹ `COVERAGE_PARTIAL` with
  `REASON_PARTIAL_INVENTORY`;
- `complete_through` = the **minimum** complete point across required
  contributions; when none is capped, it equals `observed_through`, which is
  what invariant 9 requires of a complete read;
- `observed_through` = the **maximum** observed point across contributing reads;
- `truncated` = `True` if **any** contributing read was cut short or the
  caller's limit was hit;
- `STOP_UNSAFE` ⟹ `COVERAGE_PARTIAL`, `REASON_UNSAFE_EARLY_STOP`,
  `truncated=True`, `observed_through=None`;
- `STOP_SAFE` ⟹ `COVERAGE_COMPLETE`, `REASON_WINDOW_BOUND`, `truncated=False`;
- `source_newest` later than the newest item read ⟹ freshness
  `POTENTIALLY_STALE`; when that newer material falls **inside** the requested
  window, also `COVERAGE_PARTIAL` with `REASON_TIMESTAMP_MISMATCH`; when either
  moment is absent, freshness `UNKNOWN` and **no** structural downgrade from
  freshness alone. A mismatch never alters, filters or re-orders data.

**Conversation identity — resolved, not deferred.** `ProviderResult.message`
imports `conversation_identifier` from `bridge/conversation_identity.py`, the
canonical generic owner established by **P0**. It does **not** import
`bridge/rion_reader_adapter.py`: the Rion adapter and this provider are sibling
implementations behind the same boundary, and neither may depend on the other.
It also does **not** carry a second copy of the construction, because a
duplicate pinned by an equality test is still a duplicate. An earlier revision
of this plan left this as an open review question and then answered it in the
wrong module; both are settled here. Two guards keep it settled: P0 proof 3
(`test_only_one_implementation_of_conversation_identity_exists`, which scans
`wechatdb/` as well as `bridge/`) and P12's provider-isolation test.

**No shard name, path, table name, column name, digest or schema identifier
escapes this module.** The provider's vocabulary ends here.

**Depends on** P0, P5, P6, P13, P14, P15.

**RED tests** — `wechatdb/tests/provider/test_result.py`:

- `test_one_capped_contribution_caps_the_whole_read` (T-4b) — the minimum
  complete point caps the aggregate while `observed_through` reports the
  maximum, so the two differ and the read is partial.
- `test_every_contribution_accountable_yields_a_complete_read` (T-4a)
- `test_any_inventory_gap_makes_the_read_partial` (T-2, T-3)
- `test_any_truncated_contribution_truncates_the_whole_read`
- `test_an_unsafe_stop_states_no_observed_point` (T-6)
- `test_a_safe_stop_is_complete_with_a_window_bound` (T-5)
- `test_a_mismatch_inside_the_window_is_partial_and_stale` (T-7a)
- `test_a_mismatch_outside_the_window_is_complete_and_stale` (T-7b)
- `test_an_absent_moment_is_unknown_with_no_structural_downgrade` (T-7c)
- `test_freshness_is_never_compared_against_a_clock` — `ast` scan of `result.py`
  for `time.`, `datetime`, `now`, and any numeric literal used as a threshold.
- `test_no_provider_vocabulary_reaches_the_envelope` — every `ReadCoverage`
  field value is a token from `COVERAGE_STATUSES` / `COVERAGE_REASONS`, a
  `ReadFreshness` member, a bool, an int or `None`; and no field value contains
  a table name, a shard key, a path separator or any fixture name.
- `test_the_provider_derives_identity_from_the_generic_owner` — asserts, by
  `ast`, that `result.py` imports `conversation_identifier` from
  `conversation_identity`, that it defines no function of that name and calls no
  `blake2b`, and that no module under `wechatdb/` imports `rion_reader_adapter`
  or `message_source`'s absent equivalent.
- `test_diagnostics_carry_counts_only`

**Prove RED**

```bash
cd wechatdb && PYTEST -q tests/provider/test_result.py
```

Expected failure: `ModuleNotFoundError: wechatdb.provider.result`.

**Minimum GREEN** — `Contribution`, `ProviderDiagnostics`, `message`, `collapse`.

**Validate**

```bash
cd wechatdb && PYTEST -q && cd ../bridge && PYTEST -q && cd ../shadow && PYTEST -q
```

**Commit** — `feat(wechatdb): one pessimistic coverage for a multi-part read`

---

### P17a — `ShardedMessageProvider` — the smallest successful orchestration

**Files**
- new `wechatdb/provider/provider.py`
- new `wechatdb/tests/provider/test_provider_reads.py`
- modify `wechatdb/provider/__init__.py`

**Interface produced**

```python
class ShardedMessageProvider:
    # Orchestration around this package's own leaf parser.
    # Not a registered source. Not constructed by build_database_source().
    # Not imported by product core. Handed its inputs; it searches for nothing.

    name: str = SOURCE_DATABASE

    def __init__(
        self,
        locator: ShardLocator,
        *,
        identities: IdentityResolver,
        source_newest: int | None = None,
    ) -> None: ...

    def list_conversations(self, limit: int) -> ReadResult[NormalizedConversation]: ...

    def get_messages(
        self,
        conversation_id: int,
        limit: int,
        before_sequence: int | None = None,
        *,
        requested_start: int | None = None,
        requested_end: int | None = None,
    ) -> ReadResult[NormalizedMessage]: ...

    def get_recent_messages(
        self, since_observed_at: float, limit: int
    ) -> ReadResult[NormalizedMessage]: ...
```

**Scope of this task: the happy path only.** Construction; the
discovery → routing → leaf parse → identity → `ProviderResult` chain; multi-part
merge and ordering; a complete read; and a trustworthy zero-message complete
read. Every part in every fixture here is **readable**, every stop is
`STOP_EXHAUSTED` or a plain full traversal, and no freshness mismatch is
introduced. The traversal already collects `limit + 1` matching records so that
truncation is **measured** rather than inferred (spec §8.2) — P17b is what
exercises the measurement.

**Explicitly not done here:** no registration in `bridge/store_access.py`, no
`MESSAGE_SOURCE_ENV` value, no addition to `SOURCE_NAMES` beyond the existing
`SOURCE_DATABASE`, no environment variable, no change to
`selected_source_name()`, and no change to `wechatdb/__init__.py`. Constructing
this class is a test's act, never the product's.

**Depends on** P13, P14, P15, P16.

**RED tests** — `wechatdb/tests/provider/test_provider_reads.py`:

| Test | Spec |
|---|---|
| `test_the_provider_constructs_from_an_injected_locator_and_reads_nothing_else` | §8.1, §12.4 |
| `test_all_parts_readable_over_the_full_window` | **T-1** |
| `test_a_conversation_spanning_parts_merges_in_order` (variant a: every contribution accountable ⟹ complete, `complete_through == observed_through`) | **T-4a** |
| `test_a_conversation_spanning_parts_caps_at_the_weakest_complete_point` (variant b: one contribution capped ⟹ minimum caps the aggregate, maximum still reported, read is partial) | **T-4b** |
| `test_zero_messages_with_every_part_readable_is_a_trustworthy_empty` | **T-9** |
| `test_list_conversations_answers_from_the_same_orchestration` | §5.1 |

T-4b sits here rather than in P17b because it is a property of a *successful*
multi-part merge — the aggregation arithmetic, not a degradation — and because
it is the pair-partner of T-4a that makes T-4a non-vacuous.

**Prove RED**

```bash
cd wechatdb && PYTEST -q tests/provider/test_provider_reads.py
```

Expected failure: `ModuleNotFoundError: wechatdb.provider.provider`.

**Minimum GREEN** — the class, wiring discovery → routing → traversal → identity
→ `ProviderResult.collapse`, with no degradation handling beyond what
`ProviderResult` already does on its own.

**Validate**

```bash
cd wechatdb && PYTEST -q && cd ../bridge && PYTEST -q && cd ../memory && PYTEST -q && cd ../shadow && PYTEST -q
```

Every suite passes. The `bridge` run is what proves the provider is still
isolated: T-16 fails if anything in product core learned about it.

**Commit** — `feat(wechatdb): an orchestrated multi-part read, wired to nothing`

---

### P17b — the degradation and uncertainty matrix

**Files**
- modify `wechatdb/provider/provider.py`
- new `wechatdb/tests/provider/test_provider_degradation.py`
- modify `wechatdb/tests/provider/fixtures.py` (the degraded shapes, if P12 left
  any unbuilt)

**Scope of this task: everything that is not a clean success.** An unknown
relevant part; an unavailable relevant part; a safe early stop; an unsafe early
stop; a freshness mismatch; a zero-message *incomplete* read; source-internal
truncation below the caller's limit; and the closed-reason emission check. No
new public interface: `ShardedMessageProvider`'s signature is unchanged, and
what changes is which evidence it threads into `ProviderResult.collapse`.

Splitting here is deliberate. P17a can be reviewed on whether the orchestration
is *correct*; P17b can be reviewed on whether it is *honest*. A reviewer who
rejects one does not invalidate the other, and the degradation matrix is where
the design's whole claim lives.

**Depends on** P17a.

**RED tests** — `wechatdb/tests/provider/test_provider_degradation.py`:

| Test | Spec |
|---|---|
| `test_an_unknown_intersecting_part_changes_the_claim_not_the_content` | **T-2** |
| `test_an_unavailable_part_still_returns_the_readable_parts` — two variants: will not open, opens with unrecognised schema | **T-3** |
| `test_a_safe_early_stop_is_complete` | **T-5** |
| `test_an_unsafe_early_stop_is_partial` — variants (a) unvisited part with no established bounds, (b) unvisited maximum inside the window | **T-6** |
| `test_a_source_newer_than_the_read_downgrades_freshness` — variants (a) mismatch inside the window, (b) outside it, (c) one moment absent | **T-7** |
| `test_zero_messages_with_an_unavailable_part_is_not_trustworthy` | **T-10** |
| `test_the_provider_truncating_internally_is_never_complete` | **T-11** |
| `test_every_reason_token_this_provider_emits_is_in_the_closed_set` | **T-18** |

**The T-9 / T-10 pair spans the split, and must still read as a pair.** T-9 lives
in P17a and T-10 here; both assert **identical `items` and identical requested
bounds with opposite conclusions**, and the spec calls this the single most
important pair in the suite (§13). P17b's T-10 test therefore carries a comment
naming its partner's module and test name, and a shared fixture builds the two
windows so they cannot drift apart. If review prefers them physically adjacent,
moving T-9 into P17b is a one-test change and nothing else moves.

**Prove RED**

```bash
cd wechatdb && PYTEST -q tests/provider/test_provider_degradation.py
```

Expected failure: the module does not exist. Once it does, each test must be
seen failing against P17a's happy-path-only provider before the degradation
handling is written — that is the whole reason this is a second commit rather
than a larger first one.

**Minimum GREEN** — thread inventory gaps, stop classification, internal
truncation and the source's declared newest moment into
`ProviderResult.collapse`. No new type, no new public method.

**Validate**

```bash
cd wechatdb && PYTEST -q && cd ../bridge && PYTEST -q && cd ../memory && PYTEST -q && cd ../shadow && PYTEST -q
```

Every suite passes, including P17a's, unchanged.

**Commit** — `feat(wechatdb): a multi-part read that states its own incompleteness`

---

## Stage 7 — The synthetic gate (spec §13, §15 G1, M7)

### P18 — T-1…T-18 mapped, revert-verified, recorded

**Files**
- new `docs/v2/DB_READER_COVERAGE_GATE.md`
- modify any test file whose revert-verification exposes a weak assertion

**What this task produces**

1. **A complete T-number → test mapping.** Every one of T-1…T-18 is mapped to
   the file, test name and task that produced it. Coverage after P0–P17b:

   | T | Task |
   |---|---|
   | T-1, T-4, T-9 | P17a (scenarios), P16 (collapse arithmetic) |
   | T-2, T-3, T-5, T-6, T-7, T-10 | P17b (scenarios), P16 (collapse rules) |
   | T-8 | P15 |
   | T-11 | P9 (adapter sweep) **and** P17b (provider) — the spec requires both |
   | T-12 | P10 |
   | T-13 | P9 |
   | T-14 | P11 |
   | T-15 | P3 (allowed set gains `enum`, and nothing else, ever); P0 adds proof 4, that the boundary never gains a digest dependency; forbidden-identifier list never relaxed |
   | T-16 | P12 (the existing `wechatdb` guard already covers `wechatdb.provider`; P12 adds the outward-direction half) |
   | T-17 | P5, P6 |
   | T-18 | P4 (static closure), P17b (every token emitted by at least one test) |

   Any T-number left unmapped at this point is a **defect in this plan**, and
   the executor stops and reports per §4 rather than inventing a test to close
   the gap silently.

2. **Revert verification.** Spec §13: *"Every test must be verified to fail when
   the behaviour it pins is reverted."* For each of T-1…T-18 the executor
   performs one temporary, uncommitted mutation, records the command and the
   observed failure, and reverts it. The minimum mutation set:

   | Mutation | Must break |
   |---|---|
   | make every source author `COVERAGE_COMPLETE` unconditionally | T-1…T-3, T-6, T-10…T-14 |
   | make `ShardRouter.classify_stop` always return `STOP_SAFE` | T-5, T-6 |
   | make `ShardDiscovery.probe` drop unreadable keys | T-2, T-3, T-10 |
   | make `collapse` take the maximum complete point instead of the minimum | T-4b |
   | make `IdentityResolver` pick the first of two conflicting names | T-8 |
   | restore `complete = len(messages) < message_limit` in `memory_ingest` | T-14 |
   | add `import json` to `bridge/message_source.py` | T-15 |
   | add `import wechatdb` to `bridge/store_access.py` | T-16 |
   | add `from wechatdb import provider` to `wechatdb/__init__.py` | P12's leaf-parser isolation test |
   | add a second `conversation_identifier` to `wechatdb/provider/result.py` | P0 proof 3 |
   | add `import hashlib` to `bridge/message_source.py` | P0 proof 4, and T-15 |
   | delete invariant 3 from `ReadCoverage.__post_init__` | T-17 |
   | add a thirteenth reason token used by a source but absent from `COVERAGE_REASONS` | T-18 |

   A mutation that does **not** turn a test red means the test is decorative.
   Strengthen that test in this commit and record which one it was.

3. **The gate record** — `docs/v2/DB_READER_COVERAGE_GATE.md`, stating: the
   mapping table, the revert-verification evidence, the final suite counts, and
   — prominently — that **G1 being green is not P1…P5**. The promotion gates in
   spec §15 are separate and all still unmet: no product decision exists,
   acquisition remains a separate decision, D-030 is lapsed so real multi-part
   verification is **not satisfiable today**, complete-container and
   future-version compatibility remain unproven, and visual capture stays the
   default.

**Depends on** P0–P17b.

**RED** — this task's RED is the revert verification itself: each mutation must
be **seen** turning a named test red before the gate may be recorded. A gate
written from a green run alone is worthless, which is the spec's own point.

**Prove RED** (example, one of ten)

```bash
cd memory && PYTEST -q tests/test_memory_ingest.py -k short_answer_is_not_recorded_complete
```

run once with the inference restored by hand (expect failure), then once after
`git checkout -- memory/memory_ingest.py` (expect pass).

**Validate**

```bash
cd bridge && PYTEST -q && cd ../memory && PYTEST -q && cd ../wechatdb && PYTEST -q && cd ../shadow && PYTEST -q && cd .. && git status --porcelain=v1 --untracked-files=all
```

All **four** suites green — `bridge`, `memory`, `wechatdb`, `shadow` — and the
working tree clean apart from the gate document, proving every mutation was
reverted.

**Commit** — `docs(v2): seal the synthetic coverage gate (G1)`

---

## 7. What is still true when every task is done

- `selected_source_name()` returns `visual`. The MCP surface is exactly four
  tools. `NormalizedMessage.payload()` has the same ten keys.
- No product module imports `wechatdb` at any depth, enforced by T-16. No
  top-level provider package exists. The database provider depends on no sibling
  source.
- `bridge/message_source.py` imports exactly `{__future__, dataclasses, enum,
  typing}` — the final spec's set, gaining `enum` at P3 and nothing else ever.
  `conversation_identifier` has one implementation, in
  `bridge/conversation_identity.py`, and both sources consume it.
- `wechatdb/parser.py`, `wechatdb/msg_types.py` and `wechatdb/__init__.py` are
  byte-identical to `006a80b`. Importing `wechatdb` still does not import
  `wechatdb.provider`.
- Memory records the coverage a source stated, and no stored coverage row claims
  a wider window than the evidence behind it.
- No acquisition capability exists anywhere in the dependency graph. D-005 and
  R-003 are untouched. D-030 is still lapsed, so real multi-part verification
  remains not satisfiable, and the existing real `message_0` evidence stays
  historical, scoped evidence that is not generalised.
- `head_commit` is still `a928976`. No task advances it.
- G1 is met. The five promotion gates are not, and this plan supplies no
  argument for meeting them. **Implementation approval is not promotion
  approval.**
- The one user-visible correctness change is P11: coverage the system records is
  now what the source said, not what a length implied.

## 8. Plan self-review, after the second architecture review

Re-run against the fifteen review items, before this revision was committed.

| # | Check | Result |
|---|---|---|
| 1 | Exact compliance with `006a80b` | Every §6.1–§6.7, §7, §8.1–§8.5, §9, §10, §11, §12, §13 (T-1…T-18), §14 (M1…M7) and §15 (G1) requirement maps to a task. **No deviation remains.** The previous revision's `hashlib` exception is withdrawn: §12.7 and §17.2 keep the boundary's import set at `{__future__, dataclasses, enum, typing}`, P0 now uses a separate module, and P0 proof 4 asserts the boundary never gains a digest dependency. §8.6 (FTS/cache) stays deferred by the spec and sits in this plan's scope guard, not in a task. |
| 2 | No `ReadWindow` | The type appears in no task, no interface, no test name. §2, §5 and P11 mention it only to record that it does not exist and that D-031 was amended in place to say so. P11 explicitly does not introduce one. |
| 3 | `message_source.py` does not require `hashlib` | P0 creates `bridge/conversation_identity.py` instead. P0 proof 4 (`test_the_boundary_module_never_gains_a_digest_dependency`) and P18's revert mutation "add `import hashlib` to `bridge/message_source.py`" both fail if it ever does. T-15 is modified by **P3 only**, and only to add `enum`. |
| 4 | One generic `conversation_identifier` | P0 **moves** it and deletes the original, leaving a re-export for compatibility. Proof 3 scans `bridge/` **and** `wechatdb/` for a second `FunctionDef` of that name or a stray `blake2b`. Proof 2 pins that every existing fixture's identifier is integer-identical before and after. |
| 5 | No provider → Rion dependency | P16 imports from `conversation_identity`, never from `rion_reader_adapter`. Guards: P16's `test_the_provider_derives_identity_from_the_generic_owner`, P0 proof 3, and P12's provider-isolation test. |
| 6 | No top-level `wechatprovider` | The name survives only in §1, §2 and the Stage 6 preamble, each time as an explicit prohibition. Provider code is `wechatdb/provider/*.py`; provider tests are `wechatdb/tests/provider/*.py`. |
| 7 | Four-suite validation includes `shadow` | §0.3 states why: `shadow/tests/test_source_activation.py` loads and **executes** `bridge/message_source.py` from its path, so it is load-bearing for every Reader-boundary change. Every task that touches `bridge/` runs it, including P0, and P18's final gate runs all four. No task states an expected test count; §0.2's four counts are a measured baseline only. |
| 8 | P11 preserves window evidence | The mapping table now reads *source bound when the source has one, existing observed bound when it does not, never widen*. The rationale is in the task: `_window_contains` treats `None` as unbounded, so a naive `window_start ← requested_start` would widen every stored claim to all of time. Three RED tests pin it, each required to be seen failing against the naive mapping. Completeness-by-length is still deleted outright. |
| 9 | Provider guard does not collide with Memory's raw-substring tests | P12's guard lives in `wechatdb/tests/provider/test_isolation.py`, outside the `bridge/` tree `test_layering.py` scans as raw text; it matches by AST segment prefix rather than by spelling module names; and it changes no existing architecture test. P12's acceptance includes running `memory/tests/test_layering.py` and seeing it pass. |
| 10 | P17 split into independently reviewable units | P17a is the happy path (T-1, T-4a, T-4b, T-9) and can be reviewed on correctness; P17b is the degradation matrix (T-2, T-3, T-5, T-6, T-7, T-10, T-11, T-18) and can be reviewed on honesty. P17b depends on P17a and adds no interface. The T-9/T-10 pair spans the split and is kept legible by a shared fixture and a naming comment. |
| 11 | T-1…T-18 have producing tasks | T-1, T-4, T-9 → P17a + P16; T-2, T-3, T-5, T-6, T-7, T-10 → P17b + P16; T-8 → P15; T-11 → P9 **and** P17b; T-12 → P10; T-13 → P9; T-14 → P11; T-15 → P3 (+ P0 proof 4); T-16 → P12; T-17 → P5 + P6; T-18 → P4 + P17b. Nothing unmapped; an unmapped T-number at execution time is a plan defect and triggers §4. |
| 12 | No production routing or promotion | No task modifies `selected_source_name()`, `build_database_source()`, `active_source()`, `SOURCE_NAMES`, `ACTIVATION_ENV_NAMES`, `wechatdb/__init__.py`, or any launcher. P17a states the exclusion explicitly; P17b adds no interface. P8 touches `wechat_companion_mcp.py` for shape tolerance only, with a byte-identical payload pinned by its own test. |
| 13 | D-030 remains lapsed | Untouched by every task. §1 forbids acquisition, keys, SQLCipher, process memory, LLDB and any real database; every fixture is synthetic and built in code, and P12 scans the fixture module for real paths and identifiers. P18's gate record restates that promotion gate P3 is **not satisfiable today**. |
| 14 | Vault records option C as approved for synthetic implementation only | `Next Actions.md` rewritten: the operator ruling of 2026-09-18 approved clean-room orchestration around the unchanged `wechatdb`, sealed at `006a80b` and D-031; P0–P18 synthetic implementation is authorised by it; production routing and promotion remain a later explicit gate; acquisition stays separately prohibited with D-030 lapsed. `Current Status.md` corrected to `wechatdb/provider/`, to one implementation gate plus five promotion gates, and to the T-1…T-18 matrix. |
| 15 | `head_commit` remains `a928976` | Unchanged in the vault front matter and in every statement about it. §5 records that it is the canonical **merged** anchor and correctly does not move while the branch is unmerged. No task advances it. |
| — | Dependency order is valid | P0 → (P1 → P2), (P1, P3, P4 → P5 → P6) → P7 → P8 → P9 → P10 → P11; P6 → P12 → {P13, P15}; P13 → P14; {P0, P5, P6, P13, P14, P15} → P16 → P17a → P17b; P0…P17b → P18. No cycle, no forward reference, and every task's stated dependencies precede it in the ordering. Each task's validation names the suites, and no commit is red. |
| — | Placeholders / TODOs | None. No interface is left to the executor's judgement. Two reviewable choices are stated with their blast radius: P17b's note on moving T-9 for physical adjacency (one test), and nothing else. |
| — | Stale line numbers | P0 no longer cites a line number for `conversation_identifier` and tells the executor to confirm the location at execution time. §6.1's line numbers are labelled as of `d2030f4` and are navigational aids, not semantic requirements. |
| — | Oversized task boundaries | Largest are now P17b (eight scenario groups) and P18 (twelve revert mutations). Both are single-concern and independently revertible. Every other task touches at most three production files. |
