# Coverage-Aware Database Reader Provider Design

**Date:** 2026-09-18
**Branch:** `feature/hermes-validation-isolation` (unmerged)
**Status:** Design only. Nothing here is implemented, wired, routed, enabled, or shipped.
**Governing decisions:** D-002, D-005, D-011, D-017 (*amended: provider isolation*),
D-019, D-020, D-022, D-023, D-030 (**lapsed**), R-003.
**Governing evidence:** E-018, E-022, F-016, F-036, F-037,
`docs/v2/DB_READER_INTERFACE_GATE.md`, `docs/v2/READER_BOUNDARY_INTEGRATION.md`.
**Decision this document records:** D-031 — *source-authored coverage belongs at
the generic Reader boundary; memory composes what sources said rather than
inferring it from a length.*

---

## 0. Summary

`wechatdb/` can parse one plaintext WeChat 4.1+ message database into
`MessageRecord`s. A real container is not one database, and today nothing in this
repository can state which parts of a source it read, which it could not
characterise, which refused, or whether the answer is complete for the window
that was actually asked for. Worse, the one place that *does* publish a coverage
claim — `memory/memory_ingest.py` — derives it from
`len(messages) < message_limit`, which is a guess about the source made by a
layer that cannot see inside it.

This design moves coverage to where the evidence is. The generic Reader boundary
in `bridge/message_source.py` becomes the **single owner** of the four existing
`COVERAGE_*` tokens and gains three stdlib-only types — `ReadFreshness`,
`ReadCoverage`, `ReadResult[T]`. Every source authors its own coverage, including
the two that ship today. Memory consumes that coverage instead of inferring it.
A clean-room, provider-internal orchestration design (`ShardDiscovery`,
`ShardRouter`, `IdentityResolver`, `ProviderResult`) wraps the existing
`wechatdb` parser so a multi-part source can state honest coverage, with no
shard, table, path or schema vocabulary reaching any generic type.

Nothing is promoted. Visual capture remains the production read path and the
default.

---

## 1. Problem

### 1.1 What is true today

- **The generic boundary carries no coverage at all.** `bridge/message_source.py`
  publishes `MessageSource`, `NormalizedMessage`, `NormalizedConversation`,
  `SourceStatus` and `MessageSourceError`. Its three collection methods return
  bare `list[...]`. An empty list means "the source answered and found nothing" —
  a claim the source is frequently not entitled to make.
- **The module is deliberately dependency-free.**
  `bridge/tests/test_reader_boundary.py::test_the_protocol_depends_on_no_reader_technology`
  restricts its imports to `{__future__, dataclasses, typing}` and fails on the
  identifiers `rion`, `subprocess`, `sqlcipher`, `wechat`, `json`, `argv`,
  `zstd`, `sqlite`.
- **Coverage vocabulary already exists, one layer too high.**
  `memory/memory_store.py` owns `COVERAGE_COMPLETE`, `COVERAGE_PARTIAL`,
  `COVERAGE_UNAVAILABLE` and `COVERAGE_NOT_OBSERVED`, plus `CoverageRecord`,
  `CoverageVerdict`, `ComposedCoverage` and `compose_coverage`. None of it is
  reachable from a reader, so no reader can use the vocabulary the system already
  agreed on.
- **The one coverage claim in the system is inferred, and can be wrong.**
  `MemoryIngestor.ingest_from_source` sets `complete = len(messages) <
  message_limit`. A source that truncated *internally* — below the caller's
  limit — is therefore recorded as `observed_complete`. This is not
  hypothetical: `RionReaderAdapter.get_recent_messages` runs a bounded sweep
  capped at `RECENT_CONVERSATION_SCAN_LIMIT = 50` conversations, and that bound
  never reaches a caller. A three-message answer to a two-hundred-message request
  is currently recorded as complete.
- **A real page signal is discarded.** `RionReaderAdapter` parses the reader's
  `query` object and drops `has_more` and `next_offset` on the floor. The
  fixtures in `bridge/tests/test_reader_boundary.py` carry
  `"query": {"has_more": false, "next_offset": 3}` — the evidence arrives and is
  thrown away.
- **The candidate database reader is single-database.** `wechatdb/` opens one
  connection and parses one table at a time. Handed a multi-part container it
  answers about the part it was given, and cannot say that it did.
- **Dependency direction is settled.** `memory → message_source`, never the
  reverse. `memory/tests/test_layering.py::test_the_memory_layer_imports_nothing_new`
  already lists `message_source` in `ALLOWED_IMPORTS`.
- **Isolation is enforced, not intended.**
  `test_no_product_module_imports_the_candidate_schema_provider` fails if
  `bridge`, `memory`, `shadow`, `ai`, `core`, `app.py` or `mcp_server.py` imports
  `wechatdb`.
- **The production read path is visual capture / OCR.** `selected_source_name()`
  defaults to `visual`; a default build never constructs a database reader.

### 1.2 The gap

Four failures follow, and they are one failure wearing four hats — *a partial
read that presents itself as a complete one*, which the `message_source` module
docstring already names as the single thing this boundary exists to prevent.

1. **Omission by length.** Coverage inferred from item count is wrong whenever a
   source truncates below the caller's limit.
2. **Omission by part.** A multi-part source with one unreadable part returns a
   shorter answer that is indistinguishable from a complete short answer.
3. **Omission by traversal.** A bounded traversal must stop somewhere. Nothing
   today distinguishes a stop whose remainder is provably irrelevant from a
   guess.
4. **Silent staleness.** A source that records its own newest moment can be
   compared against the newest item actually read. When the two disagree, that is
   a fact the caller must see — and it must move *freshness*, never silently
   alter the data and never masquerade as a structural gap.

Without a fix, extending the candidate reader to multiple databases makes
failure 2 systemic: a container shard that will not open would silently subtract
history from every answer, and no layer in the system would be able to say so.

### 1.3 Why now

F-037 closed the schema-feasibility question for the tested current `message_0`.
The remaining open question is whether `wechatdb` stays an evidence-only
candidate or is promoted into a real provider behind the existing Reader
contract. A component that cannot state its own coverage cannot be evaluated
against that question at all. This design supplies exactly what such a promotion
gate would have to measure. It does not pass that gate, and §15 keeps it shut.

---

## 2. Goals and non-goals

### 2.1 Goals

1. **Source-authored coverage at the generic Reader boundary.** The layer holding
   the evidence makes the claim.
2. **Single ownership of the existing coverage vocabulary.** One definition of
   the four `COVERAGE_*` tokens, in `bridge/message_source.py`.
3. **Freshness orthogonal to coverage**, with a closed token vocabulary — no
   `fresh` boolean, no age threshold, no clock comparison.
4. **A trustworthy empty answer distinguishable from an incomplete empty one.**
5. **Preservation of the pagination and truncation evidence sources already
   produce** — specifically Rion's `has_more` / `next_offset`, and every internal
   bound a source applies to itself.
6. **Memory consumes coverage rather than inferring it.** The
   `len(messages) < message_limit` inference is deleted, not demoted to a
   fallback.
7. **Provider-internal multi-shard routing and identity resolution**, designed
   clean-room, so honest coverage is possible for a multi-part source.
8. **A stdlib-only generic boundary**, so the existing technology-neutrality
   guard keeps passing with its allowed-import set extended by `enum` alone.
9. **A synthetic implementation gate, and a separate promotion gate.** Being
   correct and being shipped are different decisions.

### 2.2 Non-goals

Each is a boundary this design must not cross, not deferred work with a hidden
plan.

1. **No production wiring.** `selected_source_name()` keeps `visual`. The MCP
   surface stays exactly four tools. No product module imports a provider.
2. **No provider promotion.** This document supplies the gate; it does not pass
   it and does not argue for passing it.
3. **No acquisition, cryptography or key handling.** No key, salt, passphrase,
   cipher parameter, `PRAGMA key`, SQLCipher call, decryption, process-memory
   read, debugger attach, `task_for_pid`, code-signature operation, container
   discovery, plaintext cache, shadow copy or temporary database. D-005 and R-003
   are untouched.
4. **No FTS, search or cache layer.** Deferred entirely; §8.6 records only the
   one constraint any future one must satisfy.
5. **No complete-container claim and no future-version claim.** E-022's evidence
   is scoped to one operator's current `message_0` and is not generalised here.
6. **`SourceStatus` is unchanged.** Readiness is not per-read coverage.
7. **No `wx-cli-again` code, SQL, test fixtures or comments enter this
   repository.** STUDY-only; §16.
8. **No boolean freshness.** There is no `fresh` field anywhere in this design.
9. **No new dependency.** Standard library only.

---

## 3. Alternatives considered

### Alternative 1 — Leave the single-database candidate unchanged

Build no orchestration layer; leave the length inference in `memory_ingest`.

**For.** Zero new code and zero new surface; isolation is already proven; nothing
can regress.
**Against.** It answers none of §1.2. A single-database parser handed a
multi-part container answers about one part and cannot say so, and the only
coverage claim the system publishes stays a guess made by the layer furthest from
the evidence. The promotion question stays permanently unanswerable, because
there is nothing to gate.
**Verdict.** **Rejected** as the end state. It remains what is true *today*, and
stays true until §15 is met.

### Alternative 2 — Adopt `wx-cli-again` wholesale

**For.** It reportedly already handles multi-file containers, so routing would be
someone else's problem.
**Against.** (a) Its value is concentrated in **acquisition** — key handling and
decryption — which non-goal 3 forbids in the shipped dependency graph; adopting
it wholesale imports precisely the capability D-005 excludes. (b) Licence and
provenance are unreviewed, and the review process exists because this cannot be
assumed. (c) It would place WeChat schema knowledge where product core depends on
it, which is the **D-017 leakage** the Reader boundary exists to prevent. (d)
R-003 already rejected invoking or vendoring a stock `wechat-cli`; adopting a
successor wholesale is the same route under a new name. (e) It drags a foreign
runtime into the graph for a thin slice of behaviour.
**Verdict.** **Rejected.**

### Alternative 3 — Clean-room provider orchestration around the existing `wechatdb` — **RECOMMENDED**

**For.** (a) D-017 is satisfied by construction: shard and schema vocabulary
stays inside the provider and the envelope stays source-neutral. (b) `wechatdb`
keeps its single provable responsibility and is not edited. (c) No acquisition
capability is introduced — the provider is *handed* its inputs and never
searches. (d) Coverage honesty becomes a construction invariant rather than a
convention someone remembers. (e) It is fully testable without real data, which
is the only testing available. (f) It fixes a live defect in the shipped visual
path's recorded coverage before any provider exists.
**Against.** New code that is not wired, carrying maintenance cost with no
immediate product benefit; and routing efficiency rests on an unverified
structural assumption about how containers partition.
**Mitigation.** The assumption being false degrades efficiency, never correctness
(§8.2). The unwired cost is bounded by §15, which keeps the provider out of the
dependency graph until an explicit decision admits it.

**Recommendation: Alternative 3.**

### Why the parser is not replaced

`wechatdb` is the only component in this area with `Verified` synthetic evidence
and `Verified (scoped)` real evidence behind it (F-036, F-037), and its two
review defects were each reproduced as a failing test before being fixed
(`7919e8f`, `cc89e31`). The gap this design addresses is **not parsing** — it is
orchestration, coverage and honest incompleteness reporting. Replacing a proven
parser to obtain an unproven one, to solve a problem the parser does not have,
would discard the project's best evidence for no gain.

---

## 4. Architecture

```
   product core                  generic boundary                isolated provider
 ┌────────────────┐        ┌────────────────────────┐        ┌────────────────────┐
 │ bridge/  MCP   │        │ bridge/message_source  │        │ wechatprovider/    │
 │ memory/        │──uses─▶│                        │◀─uses──│   ShardDiscovery   │
 │ shadow/  ai/   │        │  MessageSource         │        │   ShardRouter      │
 │ core/    app   │        │  Normalized{Msg,Conv}  │        │   IdentityResolver │
 └────────────────┘        │  SourceStatus          │        │   ProviderResult   │
         ▲                 │  MessageSourceError    │        └─────────┬──────────┘
         │                 │                        │                  │ uses
         │                 │  COVERAGE_* (owner)    │                  ▼
         │                 │  COVERAGE_STATUSES     │        ┌────────────────────┐
         │                 │  ReadFreshness         │        │ wechatdb/ parser   │
         │                 │  ReadCoverage          │        │   (UNCHANGED)      │
         │                 │  ReadResult[T]         │        └────────────────────┘
         │                 └────────────────────────┘
         │                             ▲
         │                             │ imports COVERAGE_* and the read types
         │                 ┌───────────┴────────────┐
         └── NEVER imports │ memory/memory_store    │
             wechatdb or   │ memory/memory_ingest   │
             wechatprovider│ memory/memory_query    │
                           └────────────────────────┘

  the two sources that ship today, both of which will author their own coverage:
      bridge/store_access.StoreMessageSource        (visual — production path)
      bridge/rion_reader_adapter.RionReaderAdapter  (external reader)

  wx-cli-again:  STUDY-only reference.  Not a node in this diagram.
```

Four rules govern the diagram, and the absent arrow is the point:

- **provider → generic** is required: the provider constructs the read types.
- **provider → `wechatdb`** is required, and the provider is the only consumer of
  the parser.
- **memory → generic** is required and already exists.
- **generic → provider**, **generic → memory**, and **product core → provider**
  are all forbidden, enforced by test (T-16) rather than by intention.

The existing Memory types still own cross-source composition: `CoverageRecord`,
`CoverageVerdict`, `ComposedCoverage` and `compose_coverage` stay in
`memory/memory_store.py` and are not moved or duplicated. What moves is the
*vocabulary*, not the composition.

`wechatprovider/` would use the repository's established flat cross-tree import
style — `from message_source import ...` with `bridge/` supplied on `sys.path` by
the caller, identical to `memory/memory_ingest.py`. No new import mechanism is
introduced.

---

## 5. Data flow

### 5.1 A windowed read through a multi-part provider

```
 caller
   │  get_messages(conversation_id, limit, window)
   ▼
 ShardDiscovery ──▶ inventory: each part is readable | unknown | unavailable
   │                (provider-internal vocabulary; opens nothing in pass 1)
   ▼
 ShardRouter ────▶ plan: every inventory entry is either a visited step or an
   │                exclusion with a fixed cause — nothing leaves unaccounted
   ▼
 traversal ──────▶ wechatdb.parse_conversation(...) per planned part/table
   │                collects limit + 1, so "there is more" is measured
   ▼
 stop classify ──▶ exhausted | safe   = every unvisited planned shard is
   │                                   strictly older than oldest_collected_at
   │                         | unsafe → observed_partial / unsafe_early_stop
   ▼
 IdentityResolver ▶ (session_names, display_names); unresolved or ambiguous
   │                names stay unnamed → aggregate diagnostic count only
   │                → no coverage effect
   ▼
 ProviderResult ──▶ pessimistic collapse into ONE ReadCoverage
   │                complete_through = min over required coverage
   │                observed_through = max observed point
   │                truncated        = any contributing read cut short
   ▼                ◀── PROVIDER VOCABULARY STOPS HERE ───────────────────────
 ReadResult[NormalizedMessage]  =  items + coverage.  Nothing else.
```

### 5.2 The same shape for a source that is not composite

A non-composite source skips discovery, routing and stop classification entirely
and assembles a `ReadCoverage` from what it does know. `StoreMessageSource` knows
whether it filled the caller's limit and what the newest stored moment in the
requested scope is. `RionReaderAdapter` knows the upstream reader's `has_more`,
and knows whether its internal conversation sweep hit
`RECENT_CONVERSATION_SCAN_LIMIT`. Both are enough to author honest coverage;
neither requires any shard machinery.

### 5.3 What no source ever does

Falls back to another source; substitutes a different answer for the one asked
for; writes, creates, checkpoints, truncates or copies anything; constructs a
path; takes a path from a client request; or puts coverage inside a message.

---

## 6. The generic boundary — exact design

Everything in this section lands in `bridge/message_source.py`. It stays
stdlib-only: `Enum` comes from `enum`, `TypeVar` and `Generic` from `typing`,
`dataclass` from `dataclasses`. No identifier or value here names a vendor, a
schema, a transport, a path or a file.

### 6.1 Coverage tokens — moved, not duplicated

These string definitions **move** out of `memory/memory_store.py` into
`bridge/message_source.py`, which becomes their one owner. `memory_store` imports
them from there and re-exports them through its existing `__all__`, so every
current caller — `memory_ingest`, `memory_retrieval`, `memory_query`, the tests —
keeps working with no edit.

```python
#: The source accounted for the whole requested window.
COVERAGE_COMPLETE = "observed_complete"

#: The source was read, and either the window was not covered in full or the
#: source cannot state that it was.
COVERAGE_PARTIAL = "observed_partial"

#: The source was asked and could not serve the requested scope.
COVERAGE_UNAVAILABLE = "unavailable"

#: The source has no observation for the requested scope. Distinct from a
#: complete read that found nothing.
COVERAGE_NOT_OBSERVED = "not_observed"

COVERAGE_STATUSES = frozenset({
    COVERAGE_COMPLETE,
    COVERAGE_PARTIAL,
    COVERAGE_UNAVAILABLE,
    COVERAGE_NOT_OBSERVED,
})
```

**Why moved rather than mirrored.** Two copies pinned by an equality test are
still two copies: such a test proves they are equal today and does nothing about
the day a fifth state is added to one side. The direction of the move is the one
the repository already uses — `memory` imports `message_source`, and
`message_source` imports nothing — so the move creates no cycle, and it needs no
new import inside the guarded module, because a string constant has no import.

`memory_store.COVERAGE_STATES`, the smaller subset a *stored* row may carry,
stays where it is. It is a statement about the store's schema, not about the
vocabulary.

### 6.2 `ReadFreshness`

```python
class ReadFreshness(str, Enum):
    #: The read reached everything the source itself claims to hold for the
    #: requested scope.
    EVIDENCE_CONSISTENT = "evidence_consistent"

    #: The source's own records name something newer than the newest item read.
    #: A statement about two moments, not a verdict about age.
    POTENTIALLY_STALE = "potentially_stale"

    #: No comparison was made, because one of the two moments is absent.
    #: Not a synonym for consistent.
    UNKNOWN = "unknown"
```

A `str` enum so that a coverage payload serialises to the same token a human
reads, with no translation table and no second spelling.

There is no `fresh` boolean, no age threshold, no "recent enough" check and no
clock comparison anywhere in this design. The only comparison is between two
moments the source itself supplied.

### 6.3 `ReadCoverage`

```python
@dataclass(frozen=True, slots=True)
class ReadCoverage:
    """What one read is entitled to claim about the window it was asked for.

    Structure (``status``), cause (``reason``), truncation and currency are four
    different facts on four different fields. Collapsing them into one verdict
    is what produces "it said complete and it wasn't".
    """

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

Fields, exactly:

| Field | Meaning |
|---|---|
| `status` | one of the four `COVERAGE_*` tokens |
| `reason` | one token from the closed set in §6.5, always present |
| `requested_start` | inclusive start of the requested window, Unix seconds; `None` is unbounded |
| `requested_end` | inclusive end of the requested window, Unix seconds; `None` is unbounded |
| `observed_through` | newest moment this read is known to have looked at |
| `complete_through` | newest moment up to which the source can account for the window with no gap |
| `freshness` | a `ReadFreshness` member, orthogonal to `status` |
| `truncated` | whether the answer was cut short rather than exhausted |
| `item_count` | number of items in the accompanying `ReadResult` |

Windowed evidence is represented **only** by `requested_start` /
`requested_end` + `truncated` + `status` / `reason`. There is no separate window
object and no `windowed` flag: a stored flag can contradict the bounds sitting
beside it.

**Correction, 2026-09-18 — the four moment fields are `float | None`.** This
revision of §6.3 declared them `int | None`. That was a drafting regression
introduced when the former `ReadWindow` was flattened into `ReadCoverage`, not a
timestamp policy: at spec revision `35e81f9` the same moments were
`ReadWindow.start` / `.end: float | None` and `observed_through` /
`complete_through: float | None`. Four points settle it.

- **Unix seconds may carry fractional precision.** Every moment a shipped source
  can supply is already a float: `NormalizedMessage.first_observed_at`,
  `NormalizedConversation.first_seen_at` / `.last_seen_at`,
  `MessageSource.get_recent_messages`'s own `since_observed_at`, and the visual
  store's SQLite `REAL` columns. `RionReaderAdapter._number()` returns
  `float | None` deliberately.
- **The Reader boundary preserves source precision.** It does not round, floor
  or cast a moment. §7.3 states that mapping a `ReadCoverage` onto a
  `CoverageRecord` is a field copy with no translation table, and
  `memory_freshness.SourceFreshness.observed_through` / `.complete_through` and
  `CoverageRecord.window_start` / `.window_end` are all `float | None`. Under
  `int` that copy would be a rounding step, and a rounding step on a window
  bound moves the window.
- **An integer-valued source stays ordinary evidence** and needs no conversion
  policy. This widens what may be carried; it requires nothing of a source that
  has only whole seconds.
- **Provider-internal integer timestamps are untouched.** `wechatdb`'s
  `normalise_timestamp()` returns `int` and `MessageRecord.timestamp` is `int`;
  shard bounds and `Contribution` moments in §8 legitimately stay integers. The
  correction applies to this generic contract, not to every timestamp that
  eventually flows into it.

**No `ReadWindow` returns.** There are still exactly three new generic Reader
types — `ReadFreshness`, `ReadCoverage`, `ReadResult[T]` — and no status,
reason, invariant or field meaning changes.

### 6.4 `ReadResult[T]`

```python
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ReadResult(Generic[T]):
    """Items plus what the source is entitled to claim about them.

    An empty ``items`` is not an answer on its own. It means "there is nothing"
    only when ``coverage`` says the window was actually accounted for.
    """

    items: tuple[T, ...]
    coverage: ReadCoverage

    def __iter__(self):
        """Migration compatibility only. Durable consumers read ``.coverage``."""
        return iter(self.items)

    def __len__(self) -> int:
        """Migration compatibility only. Durable consumers read ``.coverage``."""
        return len(self.items)
```

`__iter__` and `__len__` exist so that an existing call site written against a
list keeps working while the protocol migrates. They are a **migration
affordance, not an interface**: any durable consumer must read `.coverage`, and
T-14 pins that memory does. A consumer that only iterates is exactly the
consumer this design exists to correct.

`ReadResult` carries **items and coverage, and nothing else**. Provenance stays
where it already is, on the bridge's response envelope, which names the source
for the whole answer as it does today.

*Implementation note:* `slots=True` on a `Generic` dataclass is supported from
Python 3.11; the boundary is written against that floor.

### 6.5 The reason token set — closed

`reason` is drawn from this set and nothing else. It is never free text, never
message content, never a sender, never a path, and never a string produced by a
provider, a database or an external process.

```python
REASON_FULL_WINDOW_OBSERVED = "full_window_observed"
REASON_EMPTY_WINDOW         = "empty_window"
REASON_CALLER_LIMIT         = "caller_limit"
REASON_SOURCE_LIMIT         = "source_limit"
REASON_WINDOW_BOUND         = "window_bound"
REASON_UPSTREAM_MORE        = "upstream_more"
REASON_PARTIAL_INVENTORY    = "partial_inventory"
REASON_UNSAFE_EARLY_STOP    = "unsafe_early_stop"
REASON_TIMESTAMP_MISMATCH   = "timestamp_mismatch"
REASON_SCOPE_UNSUPPORTED    = "scope_unsupported"
REASON_SCOPE_NOT_READ       = "scope_not_read"
REASON_NO_OBSERVATION       = "no_observation"

COVERAGE_REASONS = frozenset({
    REASON_FULL_WINDOW_OBSERVED, REASON_EMPTY_WINDOW,
    REASON_CALLER_LIMIT, REASON_SOURCE_LIMIT, REASON_WINDOW_BOUND,
    REASON_UPSTREAM_MORE, REASON_PARTIAL_INVENTORY, REASON_UNSAFE_EARLY_STOP,
    REASON_TIMESTAMP_MISMATCH, REASON_SCOPE_UNSUPPORTED,
    REASON_SCOPE_NOT_READ, REASON_NO_OBSERVATION,
})
```

| Token | Valid with status | Means |
|---|---|---|
| `full_window_observed` | `observed_complete` | every part that could hold matching items was read, and nothing was cut short |
| `empty_window` | `observed_complete` | the window was fully accounted for and held no matching item — the trustworthy empty |
| `caller_limit` | `observed_partial` | the caller's `limit` cut the answer short |
| `source_limit` | `observed_partial` | an internal bound of the source cut the answer short, independently of the caller's limit |
| `window_bound` | `observed_complete` | the traversal stopped early, and everything not visited is provably outside the requested window — the safe early stop |
| `upstream_more` | `observed_partial` | an upstream reader stated that more results exist for this request |
| `partial_inventory` | `observed_partial`, `unavailable` | at least one part the request required was unknown or unreadable |
| `unsafe_early_stop` | `observed_partial` | the traversal stopped without being able to prove the remainder irrelevant |
| `timestamp_mismatch` | `observed_partial` | the source names material newer than this read *inside* the requested window |
| `scope_unsupported` | `unavailable` | the scope is representable, and this source cannot serve it |
| `scope_not_read` | `not_observed` | this read did not look at the requested scope |
| `no_observation` | `not_observed` | the source holds no observation of the requested scope at all |

Every token appears in the table, and no status is without at least one token, so
the mapping is total in both directions.

### 6.6 What `MessageSource` becomes

```python
@runtime_checkable
class MessageSource(Protocol):
    name: str

    def status(self) -> SourceStatus: ...

    def list_conversations(self, limit: int) -> ReadResult[NormalizedConversation]: ...

    def get_messages(
        self, conversation_id: int, limit: int, before_sequence: int | None = None
    ) -> ReadResult[NormalizedMessage]: ...

    def get_recent_messages(
        self, since_observed_at: float, limit: int
    ) -> ReadResult[NormalizedMessage]: ...
```

Three decisions are load-bearing.

1. **Collection-returning methods eventually return `ReadResult[...]`, not bare
   lists.** A list cannot express a partial answer, so a source holding coverage
   would have to choose between discarding it and raising on every imperfect
   read. Both are the failure this design removes.
2. **`status()` keeps returning `SourceStatus`, never `ReadResult`.** "Can you
   answer at all" and "how much of this window did you cover" are different
   questions with different lifetimes. A source that is ready can still answer
   partially.
3. **Coverage never enters `NormalizedMessage.payload()`.** Coverage describes
   the whole answer; putting it on an item would push per-source differences out
   to the skill, which is exactly what the payload projection exists to prevent.

### 6.7 Construction invariants

Violations raise `ValueError` at construction. They are defects in a source, not
runtime conditions a caller handles, and they are never caught to produce a
softer answer.

`ReadCoverage`:

1. `status in COVERAGE_STATUSES`.
2. `reason in COVERAGE_REASONS`, and `reason` is valid for `status` per §6.5.
3. `status == COVERAGE_COMPLETE` ⟹ `truncated is False`. **A complete read can
   never be truncated.**
4. `item_count >= 0`.
5. `reason == REASON_EMPTY_WINDOW` ⟹ `item_count == 0`.
6. `truncated is True` ⟹ `reason in {caller_limit, source_limit, upstream_more,
   unsafe_early_stop}`.
7. `status in {COVERAGE_UNAVAILABLE, COVERAGE_NOT_OBSERVED}` ⟹
   `freshness is ReadFreshness.UNKNOWN`, `item_count == 0`,
   `observed_through is None`, `complete_through is None`.
8. `complete_through is not None` ⟹ `observed_through is not None` and
   `complete_through <= observed_through`.
9. `status == COVERAGE_COMPLETE` and `observed_through is not None` ⟹
   `complete_through == observed_through`.
10. `requested_start is not None and requested_end is not None` ⟹
    `requested_start <= requested_end`. An inverted window is a caller defect,
    not an empty answer.

`ReadResult`:

11. `coverage.item_count == len(items)`.
12. `items` is a tuple, so a result cannot be mutated after its coverage was
    asserted.

Invariant 3 is the structural form of the whole design: a source that hit any
cut-short condition **cannot construct** a complete coverage. The downgrade is
not a rule someone remembers to apply; it is a rule the object must satisfy in
order to exist.

---

## 7. Coverage semantics

### 7.1 What each status means

| Status | Means | Never means |
|---|---|---|
| `observed_complete` | the source can **account for the full requested window**: every part that could hold matching items was read, nothing was cut short, `truncated` is `False`. Zero items under this status is a **trustworthy empty**. | "fewer items came back than the limit" — completeness is never inferred from item count |
| `observed_partial` | a **successful** read with known incompleteness or unresolved uncertainty. Zero items under this status is **not** trustworthy. | a failure — partial answers carry real items and real evidence |
| `unavailable` | the source was asked and **cannot serve an explicitly representable requested scope**. No exception is hidden behind it. | "the source does not exist", and never a disguised crash |
| `not_observed` | the source **has no observation for the requested scope**. | "we looked and found nothing" — that is `observed_complete` + `empty_window` |

Hard failures still raise `MessageSourceError` (§11). `unavailable` is for the
case where an envelope carrying real evidence *can* be built and is more useful
than an exception.

### 7.2 Freshness is orthogonal

- Freshness takes one of `evidence_consistent`, `potentially_stale`, `unknown`,
  and says only whether the read reached what the source itself claims to hold.
- **`unavailable` and `not_observed` make sense only with `unknown`.** Nothing
  was read, so there are no two moments to compare (invariant 7).
- **`potentially_stale` can coexist with `observed_complete`.** The full
  requested historical window is accounted for, and the source may still hold
  newer material beyond `observed_through`. Downgrading structure for that would
  make every bounded historical query permanently partial, which is both false
  and useless.
- Freshness moves `status` only when the newer material lies **inside** the
  requested window, in which case the read carries `observed_partial` with
  `timestamp_mismatch`.
- A timestamp mismatch never alters data. It is recorded, never repaired, never
  used to filter, and never used to re-order.

### 7.3 Boundaries

- `observed_through` — the newest moment this read is known to have looked at:
  `requested_end` for a bounded window fully traversed, otherwise the newest item
  read, and `None` after an unsafe early stop, which knows nothing about how far
  forward it looked.
- `complete_through` — the newest moment up to which the window is accounted for
  with no gap. Equal to `observed_through` on a complete read; on a partial read
  it is the weakest point that still holds, or `None` when no such point can be
  named.

These are the same definitions `memory/memory_freshness.py` already documents, so
mapping a `ReadCoverage` onto a `CoverageRecord` is a field copy with no
translation table.

### 7.4 The truth table

`TE` = trustworthy empty.

| status | freshness | What the caller may say | TE when `items` is empty |
|---|---|---|---|
| `observed_complete` | `evidence_consistent` | **strongest available claim**: the window is fully accounted for and the read reached the source's own newest moment | **yes** |
| `observed_complete` | `potentially_stale` | the requested window is fully accounted for, and newer unseen data may exist beyond `observed_through` | **yes** |
| `observed_complete` | `unknown` | the requested window is accounted for; the source's currency is unevidenced | **yes** |
| `observed_partial` | `evidence_consistent` | the portion read is current, and the window is incomplete | no |
| `observed_partial` | `potentially_stale` | incomplete, and possibly behind the source's own newest moment | no |
| `observed_partial` | `unknown` | incomplete, and currency unevidenced | no |
| `unavailable` | `unknown` only | the source could not serve this scope, with `reason` naming which kind | no |
| `not_observed` | `unknown` only | nothing has been observed for this scope | no |

The rule that settles every empty answer:

- **Zero items + `observed_complete` + `empty_window` ⟹ trustworthy empty.**
  The caller may write "there are no messages in this window".
- **Zero items + any other status ⟹ not trustworthy.** The caller may write only
  "nothing matched, and the window was not fully covered", and must never write
  "there are no messages".

These two lines are the pair that matters: identical `items`, identical requested
bounds, opposite conclusions, distinguishable only through `coverage`. Any caller
reading `items` alone gets the second one wrong, every time.

---

## 8. Provider-internal orchestration

Everything in this section is **provider vocabulary** and must never appear in a
generic module. These are responsibilities and contracts, not implementations.

### 8.1 `ShardDiscovery` — what the source is made of

**Owns:** the inventory of a composite source, and the evidence for each part's
state.

- Inventories the provider's shards in **four** states — **known**,
  **readable**, **unknown**, **unavailable** — **internally only**. None of
  those four words, and no shard identity, ever crosses into a generic type.
  - **known** — the name shape is recognised and the entry has **not** been
    opened. No schema, readability or bounds claim exists yet.
  - **readable** — opened, schema recognised, usable as a message part; bounds
    are established from source rows or honestly absent.
  - **unknown** — listed, but not characterisable by any known name shape.
    Retained in the inventory and never silently discarded.
  - **unavailable** — characterised as a candidate, but probing could not make
    it readable: the open was refused, or it opened with an unrecognised schema.
- Two passes, kept separate because they cost different amounts: a catalogue pass
  classifies by name shape and **opens nothing** — so it can only ever produce
  **known** or **unknown**, never readable; a probe pass asks the injected opener
  for a read-only connection to each **known** entry, recognises schema, and
  takes time bounds. An **unknown** entry is not probed: opening it merely to
  guess what it might be is exactly the characterisation the catalogue could not
  honestly make.
- **Nothing is ever dropped.** The probe pass returns an inventory whose key set
  equals its input's: probing changes what is known about a part, never how many
  parts there are. An entry nobody can characterise counts toward the unknown
  tally for **every** role — if nobody can say what it is, nobody can say it is
  not a message part, and filtering it out by role would produce silent omission
  by way of a filter.
- Parts are identified internally by a stable opaque digest of the entry name,
  never by the name and never by a path.
- Time bounds are normalised through `wechatdb.normalise_timestamp`, so a part
  mixing second- and millisecond-valued times cannot report a maximum in the far
  future and dominate every routing decision. Bounds are either **established**
  or **absent**; absent bounds are never guessed.
- The locator is **injected**. There is no implementation that searches a
  filesystem; the shipped one lists exactly the entries it was constructed with.
- The opener is **injected too**, and is the only thing that turns an entry into
  a connection. `ShardDiscovery(locator, opener)`: Discovery never opens
  anything itself, so the read-only requirement is enforceable where the
  connection is made rather than wished for after an injected callable has
  already made it. An entry is `ShardEntry(name, handle)`, `handle` opaque to
  Discovery and never copied into `ShardFacts`. The shipped opener turns an
  explicitly supplied handle into a SQLite URI with `mode=ro`, `uri=True`, and
  never `immutable=1`; it searches nothing, knows no default root, copies,
  checkpoints and decrypts nothing. Opening an explicitly injected handle
  read-only is not acquisition: the provider still cannot find a database.

**Correction, 2026-09-19 — two boundaries restored.** The final simplification
of this section dropped two things while keeping the behaviour that depends on
them. Revision `35e81f9` carried the pre-probe **known** state (“catalogued,
not opened”); without it, a catalogue that opens nothing has no honest state
for an entry whose name is recognised but whose readability is untested, and
the only alternatives are to call it readable before opening it or to open it
during the catalogue. Revision `b34836f` carried the **`ShardOpener`** /
read-only-opener separation and `ShardDiscovery(locator, opener)`; the
simplification replaced it with an `open_connection` callable on the entry,
after which Discovery could no longer enforce `mode=ro` or forbid `immutable=1`
on a connection it did not make. This correction restores **only those two
load-bearing boundaries**. It does not restore the older role taxonomy,
`ShardDescriptor`, `ShardInventory`, the `ShardError` hierarchy or any other
discarded complexity; the current simplified `ShardFacts` stays as it is, and
these states remain provider-internal.

### 8.2 `ShardRouter` — which parts a windowed query must touch

**Owns:** shard selection for the requested window, ordering, and the explanation
for stopping.

- **Total accounting.** Every inventory key appears exactly once across the plan's
  visited steps and its exclusions, each with a fixed cause. A part cannot leave
  the inventory without a recorded reason.
- **Inclusion** requires that the part is readable, its bounds overlap the
  requested window, and — for a conversation-scoped read — that it holds that
  conversation. A **readable** part whose bounds are **not established always
  overlaps**, and is therefore always visited. Known, unknown and unavailable
  parts are `not_readable` exclusions; absent bounds do not turn them into
  visits. For every inventory key exactly one thing happens — a visit, or an
  exclusion with exactly one cause — and the cause is chosen by a fixed
  precedence so no part ever gets an arbitrary one: **not readable**
  `not_readable`; else a conversation-scoped request whose table the part
  lacks `conversation_absent`; else established bounds disjoint from the window
  `out_of_window`; else visit.
- **Conversation scope** is expressed as `conversation_table`: `None` for no
  filter, otherwise the **exact parser table name** `Msg_<32 hex>`, tested by
  membership in `ShardFacts.tables`. It is never hashed, never stripped, never
  converted, and never the generic conversation identifier, which is a
  different digest of a different thing. The same table legitimately occurs in
  several parts, so routing is many-parts-to-one-conversation and never a
  one-table-to-one-shard map.
- **Ordering** is newest-first by established maximum bound, with
  unestablished-bounds parts sorted **first**, so they are visited rather than
  skipped and any later stop is evaluated against a remainder that is entirely
  bounded. Ties break on the opaque shard key; no raw name or path
  participates.
- **Stops are classified against the traversal boundary, not the window.**
  The planner has already excluded every part whose established bounds miss the
  window, so a stop rule phrased as “every unscanned shard is provably outside
  the requested window” can never be satisfied for a planned part. The
  boundary is `oldest_collected_at`, the oldest candidate the newest-first
  traversal has collected so far. `exhausted` when every planned part was
  visited. **`safe`** when the traversal stopped early *and* `oldest_collected_at`
  is known *and* every unvisited planned part has established bounds with
  `max_timestamp < oldest_collected_at` — strictly, so nothing skipped could
  contribute a record newer than or equal to what is already in hand. **Any
  other early stop is `unsafe`**: an unvisited readable part with unestablished
  bounds, an unvisited maximum at or after the boundary, or no collected
  boundary at all. There is no fall-back to `requested_start` for an empty
  collection; the planner already did that work, and blurring the two would
  make the stop rule mean two things.
- **A safe stop is traversal safety, not completeness.** It means only that
  skipping the remaining planned parts cannot alter the limited answer already
  collected. Because the traversal collects `limit + 1` and a safe stop happens
  *after* that, the answer is caller-limited and stays `observed_partial` with
  reason `caller_limit`, `truncated = True`; `observed_through` **may** be
  stated, because the remainder was proven strictly older. It is never upgraded
  to `observed_complete`, and this provider's path does **not** emit
  `window_bound` for it. `window_bound` stays in the generic vocabulary for a
  source or traversal whose unvisited remainder genuinely lies outside its
  requested window.
- **An unsafe stop yields `observed_partial` with reason `unsafe_early_stop`**,
  `truncated = True`, and `observed_through = None`, and takes precedence over
  the caller-limit explanation: the returned top-N cannot be proven valid
  against what was skipped, so it may not claim how far forward it looked.
- Unknown and unavailable parts do **not** make a stop unsafe. They were never
  visitable, so the traversal did not skip them; they downgrade coverage
  separately, through `partial_inventory`, and always. Keeping the two mechanisms
  apart means neither can mask the other.
- The traversal collects `limit + 1` matching records so truncation is
  **measured** rather than inferred from a full page.

**One structural assumption, graded Hypothesis.** A multi-part container is
roughly time-partitioned. **Evidence from this project: none.** E-022 opened one
part and characterised its interior; it established nothing about how parts
relate. The design is safe regardless: early stop is gated on measured bounds, so
if the assumption is false the router simply never finds a safe stop, visits
everything, and the answer is still correct. The assumption buys efficiency,
never correctness.

**Correction, 2026-09-19 — the reachable stop and the honest stop.** Revision
`35e81f9` classified a stop as safe when “every unvisited planned part is
provably older than the boundary already reached”; revision `b34836f` made that
boundary explicit as `classify_stop(…, oldest_collected_at)`, and both
revisions’ T-5 already asserted `truncated is True` with a limit reason for the
safe stop. The final simplification deleted the boundary parameter and
rewrote safety as “outside the requested window” even though `plan()` had
already excluded every such part — leaving `STOP_SAFE` unreachable — and then
mapped that unreachable branch to `observed_complete` / `window_bound`, which
the current generic contract (caller truncation `⟹` partial, complete `⟹` not
truncated) could not have honoured even if it were reached. This correction
restores the traversal boundary and records that a safe stop is traversal
safety, not completeness. It restores none of the older `RoutingStep`,
`RoutingExclusion`, `TraversalOutcome`, `ShardInventory` or role machinery.
It also renames the conversation-scope parameter to `conversation_table`, the
exact parser table name, because P13 sealed `ShardFacts.tables` as
parser-recognised names that must never be compared to the generic
conversation identifier.

### 8.3 `wechatdb` — one readable database, one table

**Unchanged.** The existing parser remains a one-readable-database, one-table
component that produces the provider's record leaf. It learns nothing about
shards, routing, windows or coverage. The provider calls what it already exports
— `conversation_tables`, `load_name2id`, `parse_conversation`,
`normalise_timestamp`, `MessageRecord`, `MessageSchemaError` — and injects the
`session_names` and `display_names` mappings `parse_conversation` already
accepts. No edit to the parser is required by anything in this document.

### 8.4 `IdentityResolver` — names, never guesses

**Owns:** contact, session and group-member sender and nickname resolution, from
the source's own tables.

- Resolution is a **lookup, never an inference**: no fuzzy match, no edit
  distance, no tokenisation, no model, no heuristic. This is D-022's rule for
  conversation discovery applied one layer down.
- Precedence is a fixed order over distinct *kinds* of name — a per-room member
  nickname beats a contact remark beats a contact nickname — so it is never a
  choice between two equally good answers.
- **Ambiguity is refused, never resolved.** One identifier yielding two different
  names of the same kind resolves to no name; the identifier is simply absent
  from the mapping handed to the parser, whose existing fallback to the sender
  identifier is then the correct outcome with no parser change.
- **Unresolved identity does not crash and does not weaken coverage.** A
  missing or ambiguous display name means only that the message remains
  unnamed; it never makes an observed message unobserved. Resolution failure
  never raises merely because a name is absent or ambiguous, never drops a
  message, never invents a name, increments
  `ProviderDiagnostics.unresolved_identities`, and never changes `ReadCoverage`
  status, reason, requested bounds, `observed_through`, `complete_through`,
  freshness or `truncated`. Identity diagnostics are provider-local; no
  identity state enters `ReadCoverage`.
- **Scope and precedence, exactly.** A `room_member` candidate is applicable
  only when a room was requested and the candidate’s room is that room; a
  member name from another room is out of scope for this call and is not
  itself an unresolved identity. Contact remark and contact nickname are
  global. For one identifier the applicable kinds are considered in the order
  room member, contact remark, contact nickname, and the **first kind with any
  applicable candidate decides**: one distinct name resolves; repeated copies
  of the same name are still that one name; two or more different names are
  ambiguity and resolve to no name. An ambiguous stronger kind does **not**
  fall through to a weaker one, because that would silently bypass an
  unresolved stronger source; the identifier is absent from `display_names`
  and counted unresolved once. An identifier with no applicable candidate at
  all is simply out of scope and is not counted. Exact strings are evidence:
  nothing is trimmed, case-folded or normalised.
- **`session_names` is already the parser’s shape.** The resolver is handed a
  mapping from bare table digest to conversation username and preserves it as
  a defensive copy. It does not derive it from `ShardFacts.tables`, does not
  strip `Msg_`, does not hash usernames and does not invent missing names.
  `ResolvedIdentities` carries `session_names`, `display_names` and the
  `unresolved` count, and nothing else: no coverage field, no status token, no
  raw candidate list.

### 8.5 `ProviderResult` — the single translation point

**Owns:** the only place in the provider that constructs a generic type.

- Maps records to `NormalizedMessage` using the field meanings the contract
  already documents: a database source puts the message's own creation time in
  `first_observed_at`, leaves `visible_time` as `None` because it never saw a
  rendered time, and reports full confidence because a decoded row is exact and
  there is no estimator on this path.
- Derives conversation identifiers through the **one canonical owner**,
  `conversation_identifier` in `bridge/conversation_identity.py`, which every
  source imports — so two readers can never disagree about what a
  conversation’s identifier is. It imports that module and never the sibling
  reader adapter, and carries no second copy of the construction. The input is
  `MessageRecord.session_id`, the source’s already-resolved string conversation
  key: the supplied username when `session_names` had one, otherwise the
  parser’s own stable `msg_<digest>` fallback. Each conversion has one owner:
  full `Msg_<digest>` table name → `parse_conversation` →
  `MessageRecord.session_id` → `conversation_identifier` →
  `NormalizedMessage.conversation_id`. The identifier is never computed from
  the table name, a bare digest or a display name, and is never supplied from
  outside the projection.
- **Pessimistically collapses provider-local evidence into exactly one
  `ReadCoverage`:**
  - any **unknown or unavailable required shard** ⟹ `observed_partial` with
    reason `partial_inventory`;
  - `complete_through` = the **weakest, i.e. minimum**, complete point across all
    required coverage: the earliest point at which any required contribution
    stops being accountable caps the whole read. When no required contribution is
    capped — every one of them accounted for its own range — nothing caps the
    aggregate and it equals `observed_through`, which is what invariant 9
    requires of a complete read;
  - `observed_through` = the **furthest, i.e. maximum**, observed point across
    contributing reads;
  - `truncated` = `True` if **any** contributing read was cut short or the
    caller's limit was hit;
  - **the sentinel is evidence, never a public count.** The traversal
    collects `limit + 1` and the sentinel survives until collapse, so
    `candidate_count = sum(len(c.records) for c in contributions)` and
    `caller_limit_hit = candidate_count > caller_limit` — strictly, never
    inferred from a count equal to the limit. But invariant 11 requires
    `coverage.item_count == len(items)` and the public items are trimmed to the
    limit *after* collapse, so `ReadCoverage.item_count` is
    `min(candidate_count, caller_limit)` — the eventual public count. The
    sentinel decides truncation and is never counted. No provider-internal
    deduplication is specified or invented: the public candidates are the
    records actually collected and merged;
  - **a contribution cut short by a provider-internal bound is `source_limit`**
    (T-11): `source_limit_hit = any(c.truncated for c in contributions)`. It is
    never called `caller_limit`;
  - **a safe stop without a measured sentinel is not a lawful state.** A safe
    stop is by definition an early stop taken after `limit + 1` candidates were
    collected, so `stop == safe` with `caller_limit_hit` false is contradictory
    provider evidence. It is refused with a fixed `ValueError` *before* any
    reason is chosen — never converted into complete, partial, a source limit
    or an unsafe stop. The precedence below is unchanged by this: every lawful
    safe stop has `caller_limit_hit` true and so reaches rule 2 or rule 3;
  - **one reason, by fixed precedence.** (1) `unsafe` stop ⟹
    `observed_partial` + `unsafe_early_stop` + `truncated`, `observed_through =
    None`, `complete_through = None` — outranks everything, because the
    returned top-N cannot be proven against what was skipped. (2)
    `source_limit_hit` ⟹ `observed_partial` + `source_limit` + `truncated` —
    the provider’s own hidden cut is the evidence a caller cannot otherwise
    see, and `truncated` needs a truncating reason. (3) `caller_limit_hit` ⟹
    `observed_partial` + `caller_limit` + `truncated`, for a `safe` and an
    `exhausted` stop alike; safety prevents `unsafe_early_stop`, it does not
    grant completeness. (4) a required inventory gap ⟹ `observed_partial` +
    `partial_inventory`, `truncated = False`, `complete_through = None` —
    structural uncertainty, not truncation. (5) the source declares a moment
    newer than `observed_through` that lies inside the requested upper bound
    (`requested_end is None or source_newest <= requested_end`) ⟹
    `observed_partial` + `timestamp_mismatch`, `truncated = False`. (6)
    otherwise `observed_complete`, `truncated = False`, reason `empty_window`
    when the public count is zero and `full_window_observed` when it is not —
    the trustworthy-empty distinction. This path emits no `window_bound`;
  - **freshness is computed independently of the reason.** Either moment
    absent ⟹ `unknown`; `source_newest <= observed_through` ⟹
    `evidence_consistent`; `source_newest > observed_through` ⟹
    `potentially_stale` — including when the newer moment lies past a bounded
    `requested_end`, where it is stale and still structurally complete. No
    clock, no tolerance, no age constant;
  - **boundaries.** `observed_through` is the maximum non-`None` contribution
    point, or `None`; an unsafe stop overrides it to `None`. On a partial read
    `complete_through` is the minimum non-`None` contribution complete point,
    or `None`, and is `None` outright for an inventory gap or an unsafe stop.
    On a complete read `complete_through = observed_through`, as invariant 9
    requires; a minimum of provider-local values may never produce a complete
    coverage whose two points differ;
  - **requested bounds and `source_newest` are the caller’s and the source’s
    generic float moments** and are carried exactly — never rounded, floored or
    cast. `Contribution` moments, `MessageRecord.timestamp` and shard bounds
    remain integer provider evidence.
- **The message projection is exact, and guesses nothing.** `id` and
  `sequence` are the parser’s `local_id`; `conversation_id` is
  `conversation_identifier(record.session_id)`, through the canonical owner
  and with no argument supplied from outside; `sender` is `sender_name`, which the parser has already
  fallen back to the identifier for; `visible_time` is `None`; `text` is
  `content`; `kind` is `message_type`; `confidence` is `1.0`;
  `first_observed_at` is `timestamp`; `source` is the database source name.
  **`ownership` is `"unknown"`**: the provider holds no source-authored self
  identity, and ownership is never inferred from a name, an identifier’s
  shape, room membership or conversation identity. No new hash or
  message-identity construction is introduced; `local_id` is not claimed
  globally unique, and `conversation_id` already travels separately.
- **No shard name, path, table name, column name, digest or schema identifier
  escapes.** The provider's vocabulary ends at this boundary.
- **`unresolved_identities` counts ambiguity events, not distinct
  identifiers.** Identity resolution is per call; a read that resolves several
  rooms sums the per-call counts, so one identifier ambiguous in two rooms
  contributes two. No identifier is retained merely to deduplicate a
  diagnostic, which is what keeps the count-only privacy contract. Every
  diagnostic field is a non-negative `int`.
- **Diagnostics carry aggregate counts and category tallies only** — how many
  parts were readable, unknown, unavailable; how many identities went unresolved
  — and never a raw identifier, a name, a path, or message content. Diagnostics
  are a provider-side surface and are not part of `ReadResult`.

**Correction, 2026-09-19 — the collapse contract, sealed before it is
written.** Four things were incomplete or stale. Requested bounds and
`source_newest` still carried integer annotations although the generic moment
fields have been `float | None` since before P9; they are the caller’s, not the
database’s, and keep their precision. Sentinel evidence exists before the
public trim, so the internal candidate count and the public `item_count`
cannot be one field. `Contribution.truncated` had no reason assigned, and
`truncated = True` requires a truncating one: it is the existing
`source_limit`. And this is the single provider→generic projection point, so
the message mapping and `unknown` ownership are written down rather than left
to be inferred. No generic type, status or reason changes; `window_bound` is
untouched.

**Correction, 2026-09-19 — the identity handoff, and the unlawful safe stop.**
P0 settled one canonical owner for conversation identity and named
`wechatdb/provider/result.py` as its second consumer. The previous correction
gave `ProviderResult.message` a caller-supplied `conversation_id`, which made
that owner bypassable — an arbitrary integer could disagree with it, and the
module would have had no reason to import it at all. The parameter is removed:
the projection derives the identifier from `record.session_id` through the
canonical owner. Message `id` and `sequence` remain `local_id`; no historical
hash is restored. Separately, amendment 6 already makes every lawful safe stop
a measured caller-limited stop, so a safe stop without the sentinel is
rejected as contradictory evidence rather than falling through to complete.
Where a source cut and an inventory gap coexist the reason is `source_limit`
by the unchanged precedence, and the gap remains visible in the provider’s
diagnostic counts, which are never passed into the collapse.

### 8.6 Deferred: FTS, cache, search optimisation

Not designed, not specified, not implemented. One constraint is recorded so the
question is not re-litigated later: **an indexed or cached answer must never
claim coverage stronger than an unindexed read of the same window would.** A
cached answer needs its own `observed_through`, and inventing one is exactly the
class of error this envelope exists to prevent.

---

## 9. The two sources that ship today

### 9.1 `RionReaderAdapter` — stop discarding the evidence

The adapter already receives the upstream reader's `query` object and throws its
contents away. It must preserve them:

- `has_more` true ⟹ `observed_partial`, reason `upstream_more`,
  `truncated = True`. `next_offset` is preserved as the adapter's own paging
  state and is not smuggled into `ReadCoverage`.
- An **internal source bound** — including the bounded conversation sweep capped
  at `RECENT_CONVERSATION_SCAN_LIMIT` — ⟹ `observed_partial`, reason
  `source_limit`, `truncated = True`. The sweep is currently invisible to the
  caller; after this change it is not.
- `observed_complete` **only** when the upstream result set is exhausted within
  the requested window **and** no internal bound was hit.

**Correction, 2026-09-19 — pagination evidence differs by command, and
`sessions` has none.** An earlier revision of this section wrote as though every
reader reply carried a `query` object. It does not. The exact Rion revision the
sealed interface gate exercised —
`Rion-Wu-tech/wechat-intelligence-hub` @ `3afe33e0742ef4e92b4babe399bf471fdcd86a7b`,
`projects/rion-wechat-reader/rion_wechat_reader.py` — was inspected directly and
settles it.

1. **Evidence differs by command.** There is no uniform pagination envelope.
2. **`history` supplies it.** That revision emits
   `{"query": {"has_more": len(rows) == args.limit, "next_offset":
   args.offset + len(rows)}, "messages": rows}`. Note the reader computes
   `has_more` itself, as a full-page equality against the limit *it* was given.
3. **`sessions` supplies neither.** It emits only
   `{"sessions": sessions(db, args.limit, args.type_filter, args.keyword)}` —
   no `query`, no `has_more`, no `next_offset`. The underlying `sessions()`
   sorts newest-first and returns at most the caller-supplied limit; this
   adapter calls it with no type filter and no keyword.

`list_conversations` therefore **measures** truncation rather than inferring it,
and is **not** permanently partial:

4. It asks the reader for **`caller_limit + 1`** sessions.
5. The extra row is **sentinel evidence only**. It is removed before the public
   `ReadResult`, so a caller that asked for `L` still receives at most `L`
   items and `coverage.item_count` still equals `len(items)`.
6. **Sentinel present** (`limit + 1` rows came back) ⟹ a row exists that the
   caller's own limit excluded ⟹ `observed_partial`, reason `caller_limit`,
   `truncated = True`.
7. **Sentinel absent** (at most `limit` rows) ⟹ this deliberately oversized,
   unfiltered request exhausted the session list ⟹ `observed_complete`, reason
   `full_window_observed` with items and `empty_window` with none,
   `truncated = False`.
8. A sentinel at **`RECENT_CONVERSATION_SCAN_LIMIT + 1`** proves the recent
   sweep's own internal bound was reached, and becomes aggregate
   `observed_partial` + `source_limit` + `truncated = True` in
   `get_recent_messages`. Enumeration that genuinely exhausts *below* that bound
   does not by itself prevent the aggregate read from being complete; child
   history coverage is still inspected either way.
9. **This is measured truncation, not short-count completeness inference.** The
   forbidden rule is `len(rows) < caller_limit ⟹ complete`, which reads a
   coincidence as evidence. Here the source was deliberately asked for one row
   *beyond* what the caller wanted, so its absence is an answer the source gave
   rather than a gap the adapter guessed at. It is the same "collect `limit + 1`
   so truncation is measured rather than inferred" principle §8.2 already
   applies to the provider's traversal.

No generic vocabulary, type, status or reason changes: `caller_limit`,
`source_limit`, `upstream_more`, `full_window_observed` and `empty_window` are
the existing tokens, used as §6.5 already defines them. `history`'s explicit
`upstream_more` is never rewritten as `caller_limit`.

### 9.2 `StoreMessageSource` — conservative by default

The visual store authors conservative coverage:

- The default for any scope OCR cannot prove complete is `observed_partial` with
  freshness `unknown`. Visual capture observes what was on screen; absence of a
  message in the store is not evidence that the message does not exist.
- `observed_complete` only where the scope is genuinely account-for-able: the
  caller's limit was not filled **and** the store's own newest recorded moment
  for the scope was reached. Where the window is genuinely empty and accounted
  for, that is `observed_complete` with reason `empty_window` — a real
  trustworthy empty, which the visual path can produce.
- Under no circumstance does it report complete on the basis of having returned
  fewer items than the limit alone.

---

## 10. Memory

- `memory/memory_store.py` **imports** the coverage tokens from
  `bridge/message_source.py` rather than defining duplicates, and re-exports them
  through its existing `__all__`. `CoverageRecord`, `CoverageVerdict`,
  `ComposedCoverage` and `compose_coverage` stay exactly where they are: they
  answer the store's cross-source composition question, which is not a read's
  question.
- `memory/memory_ingest.py` **consumes `ReadResult.coverage`** and stops inferring
  from `len(messages) < message_limit`. That branch is deleted, not kept as a
  fallback: a fallback that is wrong in precisely the cases this design exists to
  catch is worse than no fallback.
- `not_observed` writes **no coverage row**. `memory_store.COVERAGE_STATES`
  excludes it by design — the absence of a row is how "nothing was observed" is
  recorded — so writing one would be rejected by `CoverageRecord`'s own
  validation.
- `MemoryResult` and `SourceFreshness` keep their shapes and stay conceptually
  where they are. What changes is the provenance of two fields:
  `observed_through` and `complete_through` become **source-authored**, copied
  from `ReadCoverage` rather than derived from the returned items, which is a
  statement about the data rather than about the request.

---

## 11. Error handling

| Condition | Outcome |
|---|---|
| hard refusal: reader not configured, executable missing, timeout | raises `MessageSourceError` |
| request malformed: invalid argument, unknown conversation, unsupported paging shape | raises `MessageSourceError` |
| store error, or any failure leaving no defensible statement to make | raises `MessageSourceError` |
| a representable scope this source simply does not serve | returns `ReadResult` with `unavailable` and a naming `reason` |
| a multi-shard read where some parts were unknown or unavailable and the cause is attributable | returns `ReadResult` with `observed_partial`, reason `partial_inventory`, **carrying the readable items** |
| the caller's limit was hit | `observed_partial`, `caller_limit`, `truncated = True` |
| an internal source bound was hit | `observed_partial`, `source_limit`, `truncated = True` |
| an early stop that cannot be proven safe | `observed_partial`, `unsafe_early_stop`, `truncated = True` |
| a coverage or result claims more than its evidence supports | raises `ValueError` at construction |
| ordinary readiness questions | answered by `status()` → `SourceStatus`, unchanged |

**The governing rules.** A hard refusal, a malformed request, a store error, or
any condition under which no defensible statement can be made raises
`MessageSourceError` and produces **no envelope**. A successful but unserved
representable scope may return `unavailable` — and `unavailable` never hides an
exception. A partial multi-shard read returns partial **with its readable items**
whenever the cause is attributable; a per-part failure is never fatal and never
silent. **There is no fallback to another source, at any level.** Every token
stays fixed, lowercase and content-free: no path, filename, chat title, sender,
message text, SQL or captured output ever reaches a caller, a log line, or an
exception message.

---

## 12. Privacy and security

1. **The generic types are technology-neutral.** No identifier or token in
   `bridge/message_source.py` carries `Msg_`, `Name2Id`, `real_sender_id`,
   `local_type`, a shard name, a table name, a column name or a file name. T-15
   asserts this by scan.
2. **No content-bearing coverage fields.** Every `ReadCoverage` field is a token,
   a count, a boolean or a Unix-seconds number — a float, which a whole-second
   source satisfies without conversion. There is no field into which a
   name, a path or a message body could be placed.
3. **No acquisition, cryptography or key material.** No key, salt, passphrase,
   cipher parameter, `PRAGMA key`, SQLCipher call, decryption, process-memory
   read, debugger interaction or code-signature operation exists anywhere in this
   design.
4. **No location knowledge.** No container path, bundle identifier, directory
   name or search root. Every input arrives through an injected locator. The
   provider cannot find a database; it can only be handed one.
5. **Read-only, with no silent optimisation.** Read-only open only. The
   `immutable` optimisation is **forbidden**: it makes SQLite ignore the
   write-ahead log, silently dropping not-yet-checkpointed messages. When the log
   cannot be read, the honest outcome is an unavailable part and a visible
   coverage downgrade.
6. **Nothing is written.** No file created, no WAL checkpointed, no copy, no
   plaintext cache, no export. The source is left byte-identical.
7. **The boundary is stdlib-only.** The existing technology-neutrality guard
   keeps passing, with its allowed-import set extended by `enum` and nothing
   else; its list of forbidden reader-technology identifiers is not relaxed.
8. **No real data anywhere.** Every fixture is synthetic and built in code. No
   real chat content, identifier, path or digest is committed.
9. **`wx-cli-again` is a clean-room behavioural reference only** — §16.
10. **D-002, D-005, D-011 and R-003 are untouched.** Nothing drives, activates,
    scrolls or writes to WeChat; nothing runs unattended; nothing invokes or
    vendors a `wechat-cli`.

---

## 13. Synthetic test matrix

All fixtures are synthetic, built in code as temporary SQLite databases whose
column layout matches what `wechatdb` reads, populated with invented text,
fixture identifiers and chosen timestamps. **Every test must be verified to fail
when the behaviour it pins is reverted** — a coverage test that passes against a
source which always reports `observed_complete` is worthless.

| # | Case | Asserted |
|---|---|---|
| **T-1** | All shards readable, full window | every part opens and is recognised, every expected message present exactly once; `observed_complete`; reason `full_window_observed`; `truncated is False`; `complete_through == observed_through` |
| **T-2** | Unknown intersecting shard | T-1 plus one entry matching no known shape and overlapping the window; `observed_partial`; reason `partial_inventory`; **messages identical to T-1** — an unknown part changes the claim, not the content |
| **T-3** | Unavailable intersecting shard | variants for "will not open" and "opens with unrecognised schema"; `observed_partial`; reason `partial_inventory`; **the readable parts' messages are still returned** — a per-part failure is not fatal |
| **T-4** | Read spanning shards | one conversation present in several parts; all are planned; messages correctly merge-ordered across part boundaries. (a) every contribution accountable ⟹ `observed_complete` and `complete_through == observed_through`. (b) one contribution capped ⟹ the **minimum** complete point caps the aggregate while `observed_through` still reports the **maximum** observed point, so the two differ and the read is partial |
| **T-5** | Safe limited traversal | descending established ranges; the traversal has measured `caller_limit + 1` matching records; `oldest_collected_at` is known; every unvisited planned shard has `max_timestamp < oldest_collected_at` (strict); stop is `safe`; `observed_partial`, reason `caller_limit`, `truncated is True`, `observed_through` is stated. Proves the early stop did **not** become `unsafe_early_stop`; claims nothing about the whole window being enumerated |
| **T-6** | Unsafe early stop | variant (a) an unvisited readable part has no established bounds; variant (b) an unvisited part’s `max_timestamp >= oldest_collected_at` — the comparison is against the traversal boundary, not the window edge; both ⟹ `observed_partial`, reason `unsafe_early_stop`, `truncated is True`, `observed_through is None` |
| **T-7** | Timestamp mismatch, freshness downgrade | the source declares a newest moment later than the newest item read. (a) mismatch inside the window ⟹ `observed_partial` + `timestamp_mismatch` + `potentially_stale`. (b) mismatch outside the window ⟹ `observed_complete` + `potentially_stale`. (c) one moment absent ⟹ `unknown`, with no structural downgrade caused by freshness alone. No test anywhere compares against a clock or a constant |
| **T-8** | Identity resolved and unresolved | remark beats nickname; a room nickname applies inside that room and not outside it, and another room’s member name is out of scope rather than unresolved; an identifier with two conflicting same-kind names resolves to **no name**, neither candidate appears anywhere in the result, and an ambiguous stronger kind does not fall through to a weaker one; repeated copies of one name are not ambiguity. Ambiguous or unresolved identity ⟹ the message remains present ⟹ its sender name falls back through the parser’s existing behaviour ⟹ the unresolved diagnostic count increments ⟹ **`ReadCoverage` is unchanged** in every field. It raises nothing and never places a name in `ReadCoverage` |
| **T-9** | Zero messages, complete | every part readable, window genuinely empty ⟹ `items == ()`, `observed_complete`, reason `empty_window`; the caller may state "there are no messages" |
| **T-10** | Zero messages, incomplete | identical window, one part unavailable ⟹ `items == ()`, `observed_partial`, reason `partial_inventory`; **identical `items` and identical requested bounds to T-9, opposite conclusion.** The single most important pair in the suite |
| **T-11** | Source-internal truncation below the caller's limit | a source returns three items for a two-hundred-item request while having truncated internally ⟹ `observed_partial`, reason `source_limit`, `truncated is True`, never complete. Run against the Rion adapter's bounded sweep and the provider |
| **T-12** | Visual source conservative coverage | `StoreMessageSource` reports `observed_partial` + `unknown` for scopes OCR cannot prove complete; reports `observed_complete` only where the limit was not filled **and** the store's newest recorded moment for the scope was reached; never reports complete from item count alone |
| **T-13** | Rion pagination preservation | `has_more` true ⟹ `observed_partial` + `upstream_more` + `truncated`; `next_offset` is preserved as adapter paging state and never appears in `ReadCoverage`; `observed_complete` only when upstream is exhausted in the requested window with no internal bound . `has_more` / `next_offset` are **`history`-only** — `sessions` carries no `query`, and its exhaustion is measured by the `limit + 1` sentinel of §9.1 |
| **T-14** | Memory consumes source coverage | `memory_ingest` records the **source's** status and reason; the `len(messages) < message_limit` branch is gone from the ingestor; a `not_observed` read writes **no** coverage row; `observed_through` / `complete_through` are copied from `ReadCoverage`, not derived from the items |
| **T-15** | Technology-neutrality guard | AST and string scan: `bridge/message_source.py` contains none of `Msg_`, `Name2Id`, `real_sender_id`, `local_type`, `shard`, `message_0`, nor any table, column, path or vendor name; its import set is still `⊆ {__future__, dataclasses, enum, typing}` |
| **T-16** | Import-direction guard | AST scan, never raw text. No module under `bridge/`, `memory/`, `shadow/`, `ai/`, `core/`, nor `app.py` / `mcp_server.py` imports `wechatdb` or the provider package at any depth; the provider imports nothing from `memory/`, `shadow/`, `ai/` or `core/`; `message_source` imports neither |
| **T-17** | Invariant enforcement | every invariant in §6.7 has a test constructing the violating object and asserting `ValueError`, including a complete-and-truncated coverage, an `item_count` that disagrees with `len(items)`, items carried alongside `unavailable` or `not_observed`, a non-`unknown` freshness on `unavailable`, and an inverted requested window |
| **T-18** | Reason-token closure | every `reason` a source can emit is in `COVERAGE_REASONS`; every token in `COVERAGE_REASONS` is valid for at least one status and is emitted by at least one test; constructing a coverage with a reason invalid for its status raises `ValueError`; no reason is ever free text, content, a sender, a path or provider output |

This matrix is a floor, not a ceiling: T-1 … T-18 are the minimum enumeration,
and additional cases may be added without a further decision.

---

## 14. Migration stages

Staged so that no caller is ever broken, the wire shape never moves, and each
step is independently revertible. **No production wiring occurs during any
stage.**

**M1 — Relocate the vocabulary.** Define `COVERAGE_COMPLETE`,
`COVERAGE_PARTIAL`, `COVERAGE_UNAVAILABLE`, `COVERAGE_NOT_OBSERVED` and
`COVERAGE_STATUSES` in `bridge/message_source.py`. `memory/memory_store.py`
imports and re-exports them. No caller changes; `message_source` still imports
nothing, because a string constant requires no import.

**M2 — Add the boundary types.** `ReadFreshness`, `ReadCoverage`,
`ReadResult[T]`, the twelve reason tokens and `COVERAGE_REASONS`, with their
construction invariants and T-17 / T-18. Nothing consumes the new types yet.

**M3 — Widen the protocol with compatibility.** Collection-returning methods
begin returning `ReadResult`, whose `__iter__` and `__len__` keep every existing
list-shaped call site working unchanged. `status()` is untouched.

**M4 — Source-authored coverage.** `StoreMessageSource` and `RionReaderAdapter`
author their own coverage. Rion stops discarding `has_more` and reports its
internal sweep bound. The visual store adopts its conservative default.

**M5 — Memory consumer switchover.** `memory_ingest` reads
`ReadResult.coverage`; the `len(messages) < message_limit` branch is **deleted**.
This stage **fixes the live defect** identified in §1.1 and is the first stage
with a user-visible correctness effect on recorded coverage.

**M6 — Provider orchestration, clean-room.** `ShardDiscovery`, `ShardRouter`,
`IdentityResolver` and `ProviderResult` are built against synthetic fixtures
only, importing `wechatdb` and `message_source` and nothing from product core.

**M7 — Full synthetic gate.** T-1 … T-18 green, each verified to fail when its
behaviour is reverted.

---

## 15. Promotion gates

Passing every gate below is a precondition for *considering* promotion, never a
grant of it.

**Implementation gate.**

- **G1 — The full synthetic matrix is green.** T-1 … T-18, each verified to fail
  on revert.

**Promotion gates, separate and all required.**

- **P1 — An explicit product decision to promote,** recorded in `Decisions.md`,
  naming what the provider is promoted *to*. **There is no implicit promotion:**
  an import statement, a green test run and a merged branch are none of them a
  promotion decision.
- **P2 — Standing acquisition remains a separate decision.** This design neither
  requires nor justifies obtaining access material, and supplies no argument for
  doing so.
- **P3 — D-030 has lapsed** and is not reopened here. At design approval, real
  multi-part verification was therefore **not satisfiable**. D-032 records the
  post-G1 structural evidence that superseded that operational state; P3 remains
  unmet until the corrected provider passes a later real-evidence gate.
- **P4 — Complete-container coverage and future-WeChat-version compatibility
  remain unproven.** E-022's evidence is scoped to one operator's current
  `message_0` and must not be generalised. No claim to the contrary may be made
  on the strength of this document.
- **P5 — Visual capture stays the default.** Promotion does not change
  `selected_source_name()`'s `visual` default. A database source stays explicitly
  selected, off by default, and fail-closed when unselected.

---

## 16. Clean-room note

`wx-cli-again` is a **STUDY-only** reference. It may be read to understand *what
behaviour a correct multi-part reader exhibits* — that a container may be
partitioned, that a conversation can span partitions, that a session record may
carry its own latest-message moment. It supplies **questions, not answers**.

**No source code, SQL text, query shape, test fixture, fixture datum, comment, or
identifier-naming scheme may be copied from it into this repository.** Every
design idea above is expressed in this project's own terms and derived from this
project's own `wechatdb`, `bridge/` and `memory/` conventions.

This is **STUDY**, not **ADOPT** and not **REPLACE**.

---

## 17. Resolved questions

Every choice below is settled by this document. There are no open questions.

1. **Who owns the coverage vocabulary?** `bridge/message_source.py`, exclusively.
   `memory_store` imports and re-exports. Two copies pinned by an equality test
   are still two copies. §6.1.
2. **Does `message_source` importing nothing survive the move?** Yes — a string
   constant requires no import, and `enum` joins `dataclasses` and `typing` as
   stdlib-only. The existing guard passes with its allowed set extended by
   `enum` and nothing else. T-15.
3. **Is freshness a boolean?** No, and it never will be. Three tokens, orthogonal
   to coverage, no threshold, no clock, no `fresh` field. §6.2, §7.2.
4. **Can a complete read be stale?** Yes. The requested window is fully accounted
   for and the source may hold newer material beyond `observed_through`. §7.2.
5. **Can a complete read be truncated?** Never. Invariant 3 makes it
   unconstructable. §6.7.
6. **How is a trustworthy empty recognised?** Zero items, `observed_complete`,
   reason `empty_window`. Any other status with zero items is not trustworthy.
   §7.4, T-9 and T-10.
7. **Returned `unavailable` or raised `MessageSourceError`?** Raise when no
   defensible statement can be made and no envelope can be built; return
   `unavailable` when the scope is representable and the source simply does not
   serve it. `unavailable` never hides an exception. §11.
8. **Can a partial read carry items?** Yes, and it must, whenever the cause is
   attributable. A per-part failure is never fatal. §11, T-3.
9. **Does coverage go on a message?** Never. Coverage describes the whole answer,
   so it lives on the envelope and never in `NormalizedMessage.payload()`. §6.6.
10. **Does `status()` change?** No. It returns `SourceStatus`, because readiness
    is not per-read coverage. §6.6.
11. **Do unknown or unavailable shards make an early stop unsafe?** No — they
    were never visitable, so the traversal did not skip them. They downgrade
    coverage independently through `partial_inventory`, and keeping the two
    mechanisms separate means neither masks the other. §8.2.
12. **Does an unresolvable name make a message unobserved?** No. It makes it
    unnamed: the message stays present under the parser’s own fallback, an
    aggregate diagnostic count is incremented, nothing crashes, and
    `ReadCoverage` is unchanged in every field. Identity state never touches
    coverage. §8.4, T-8.
13. **How does a multi-shard read collapse to one coverage?** Pessimistically:
    any unknown or unavailable required shard makes it partial, `complete_through`
    takes the minimum, `observed_through` the maximum, and `truncated` is true if
    any contributing read was cut short. §8.5.
14. **Does the parser change?** No. `IdentityResolver` produces exactly the
    `(session_names, display_names)` pair `parse_conversation` already accepts;
    that is the entire integration surface. §8.3.
15. **What if the time-partitioning assumption is wrong?** Nothing breaks.
    A readable part with unestablished bounds always overlaps and never
    satisfies the safe-stop test, so it is always visited. The assumption buys efficiency, not
    correctness. §8.2.
16. **Where do FTS and caching go?** Nowhere, now. One constraint is recorded: an
    indexed or cached answer may never claim coverage stronger than an unindexed
    read of the same window. §8.6.
17. **Does `__iter__` on `ReadResult` mean callers may ignore coverage?** No. It
    is a migration affordance that exists so M3 breaks no caller. Durable
    consumers read `.coverage`, and T-14 pins that memory does. §6.4.
18. **Does any of this justify obtaining a WeChat database?** No. D-030 lapsed,
    every fixture is synthetic, and P2 and P3 keep acquisition a separate
    decision that this document does not make and does not argue for.

---

## 18. Compatibility with standing decisions

### 18.1 D-017 — the Reader boundary

`ReadFreshness`, `ReadCoverage` and `ReadResult[T]` **extend the existing Reader
boundary**. They do not create a second data boundary in product core: they live
in the module that already defines the contract, alongside `MessageSource`,
`NormalizedMessage`, `NormalizedConversation`, `SourceStatus` and
`MessageSourceError`, and they are the only new types product core can see.
Provider internals — `ShardDiscovery`, `ShardRouter`, `IdentityResolver`,
`ProviderResult`, and every shard, table and schema name they handle — remain
isolated behind `ProviderResult`, and that isolation is **enforced by the
architecture guards** (T-15, T-16) rather than by intention. D-017's
provider-isolation amendment therefore stands unmodified: product core still may
not import a provider, and any promotion may happen only through the Reader
contract.

### 18.2 D-030 — unaffected and lapsed

D-030 is **lapsed and unaffected**. This design creates no access material, takes
no access material as input, and performs no acquisition of any kind. Every
fixture in §13 is synthetic and built in code. Nothing here reopens D-030, and
nothing here supplies an argument for reopening it.

---

## 19. What this document changes

Nothing executable. It adds one design document and records one decision (D-031).
It creates, edits or deletes no module, test, dependency, build phase,
environment variable, default or route. `wechatdb` is untouched. The MCP surface
stays exactly four tools, `selected_source_name()` still defaults to `visual`,
and visual capture / OCR remains the production read path.
