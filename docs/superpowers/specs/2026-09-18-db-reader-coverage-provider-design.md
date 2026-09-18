# Coverage-aware database reader provider — design

**Date:** 2026-09-18
**Branch:** `feature/hermes-validation-isolation` (unmerged; vault `head_commit` stays `a928976`)
**Status:** Design only. Nothing here is implemented, wired, routed, enabled, or shipped.
**Governing decisions:** D-002, D-005, D-011, D-017 (*Amended: provider isolation*),
D-019, D-020, D-022, D-023, D-030 (**lapsed**), R-003.
**Governing evidence:** E-018, E-022, F-016, F-036, F-037,
`docs/v2/DB_READER_INTERFACE_GATE.md`.
**Records decision:** D-031 — *source-authored coverage belongs in the Reader
boundary; memory composes it rather than inferring it.*

---

## 0. Summary

`wechatdb/` can parse one plaintext WeChat 4.1+ message database into
`MessageRecord`s. A real container is not one database, and today nothing in
this repository can state which parts of a source it read, which it could not
characterise, which refused, or whether the answer is complete for the window
that was asked for. Worse, the one place that *does* publish a coverage claim —
`memory/memory_ingest.py` — derives it from `len(messages) < message_limit`,
which is a guess about the source made by a layer that cannot see inside it.

This spec moves coverage to where the evidence is. The generic Reader boundary
gains four frozen, stdlib-only types — `ReadWindow`, `ReadFreshness`,
`ReadCoverage`, `ReadResult[T]` — and becomes the single owner of the four
`COVERAGE_*` tokens that `memory/memory_store.py` defines today. Each source
authors its own coverage; memory composes what sources said instead of inferring
it from a length. A clean-room, provider-internal orchestration package
(discovery, routing, identity) is designed around the existing `wechatdb` parser
so a multi-part source can state honest coverage, with no shard or schema
vocabulary reaching any generic type.

Nothing is promoted. Visual capture remains the production path.

---

## 1. Problem

### 1.1 What is true today

- **The generic boundary carries no coverage at all.**
  `bridge/message_source.py` publishes `MessageSource` (line ~199),
  `NormalizedMessage` (~144), `NormalizedConversation` (~119), `SourceStatus`
  (~92) and `MessageSourceError` (~76). Its three collection methods return bare
  `list[...]`. An empty list means "the source answered and found nothing" —
  a claim the source is frequently not entitled to make.
- **The module is deliberately dependency-free.**
  `bridge/tests/test_reader_boundary.py::test_the_protocol_depends_on_no_reader_technology`
  restricts its imports to `{__future__, dataclasses, typing}` and fails on the
  identifiers `rion`, `subprocess`, `sqlcipher`, `wechat`, `json`, `argv`,
  `zstd`, `sqlite`.
- **Coverage vocabulary already exists, one layer too high.**
  `memory/memory_store.py` owns `COVERAGE_COMPLETE="observed_complete"`,
  `COVERAGE_PARTIAL="observed_partial"`, `COVERAGE_UNAVAILABLE="unavailable"`,
  `COVERAGE_NOT_OBSERVED="not_observed"`, plus `CoverageRecord`,
  `CoverageVerdict`, `ComposedCoverage` and `compose_coverage`.
  `memory/memory_retrieval.py` carries `coverage` / `truncated` /
  `coverage_by_source`; `memory/memory_freshness.py` carries
  `observed_through` / `complete_through`. None of it is reachable from a reader.
- **The one coverage claim in the system is inferred, and can be wrong.**
  `MemoryIngestor.ingest_from_source` sets
  `complete = len(messages) < message_limit`. A source that truncated
  *internally* — below the caller's limit — is recorded as
  `observed_complete`. This is not hypothetical:
  `RionReaderAdapter.get_recent_messages` runs a bounded sweep capped at
  `RECENT_CONVERSATION_SCAN_LIMIT = 50` conversations, and that bound never
  reaches a caller. A 3-message answer to a 200-message request is currently
  recorded as complete.
- **A real page signal is discarded.** `RionReaderAdapter` parses the reader's
  `query` object and drops `has_more` and `next_offset` on the floor. The
  fixtures in `bridge/tests/test_reader_boundary.py` carry
  `"query": {"has_more": False, "next_offset": 3}` — the evidence arrives and is
  thrown away.
- **Dependency direction is settled.** `memory → message_source`, never the
  reverse. `memory/tests/test_layering.py::test_the_memory_layer_imports_nothing_new`
  already lists `message_source` in `ALLOWED_IMPORTS`;
  `memory/memory_ingest.py` already imports it, with a `sys.path` fallback for
  the packaged worker.
- **Isolation is enforced, not intended.**
  `test_no_product_module_imports_the_candidate_schema_provider` fails if
  `bridge`, `memory`, `shadow`, `ai`, `core`, `app.py` or `mcp_server.py`
  imports `wechatdb`.
- **The production read path is visual capture / OCR.**
  `selected_source_name()` defaults to `visual`; a default build never
  constructs a database reader.

### 1.2 The gap

Four failures follow, and they are the same failure wearing four hats — *a
partial read that looks like a complete one*, which the `message_source` module
docstring already names as the one thing this boundary exists to prevent.

1. **Omission by length.** Coverage inferred from item count is wrong whenever a
   source truncates below the caller's limit.
2. **Omission by part.** A multi-part source with one unreadable part returns a
   shorter answer indistinguishable from a complete short answer.
3. **Omission by traversal.** A bounded traversal must stop somewhere. Nothing
   distinguishes a stop whose remainder is provably irrelevant from a guess.
4. **Silent staleness.** A source that records its own newest moment can be
   compared against the newest message actually read. When they disagree, that
   is a fact the caller must see — and it must degrade *freshness*, never
   silently alter the data or masquerade as a structural gap.

### 1.3 Why now

F-037 closed the schema-feasibility question for the tested current `message_0`.
[[Next Actions]] states the remaining question plainly: *"Should `wechatdb`
remain an evidence-only candidate, or be promoted into a real provider behind
the existing Reader contract?"* A component that cannot state its own coverage
cannot be evaluated against that question. This design supplies what the
promotion gate would measure. It does not pass that gate, and §12 keeps it shut.

---

## 2. Goals and non-goals

### 2.1 Goals

1. One owner for the coverage vocabulary, at the boundary where reads happen.
2. A generic, source-neutral read envelope in which an incomplete answer is
   structurally unable to present itself as a complete one.
3. Freshness as a first-class, orthogonal fact with a closed token vocabulary —
   no boolean, no threshold.
4. Source-authored coverage from **every** source, including the two that exist
   today, so memory composes evidence instead of inventing it.
5. A provider-internal design for multi-part discovery, routing and identity
   that leaks no shard or schema vocabulary into any generic type.
6. A synthetic test matrix sufficient to gate a future promotion decision.

### 2.2 Non-goals

Each is a boundary this design must not cross, not deferred work with a hidden
plan.

1. **No acquisition capability.** No key, salt, passphrase, cipher parameter,
   `PRAGMA key`, SQLCipher, decryption, process-memory read, LLDB or debugger
   attach, `task_for_pid`, code-signature operation, container discovery,
   plaintext cache, shadow copy, or temporary database. D-005 and R-003 are
   untouched.
2. **No production wiring in this phase.** `selected_source_name()` keeps
   `visual`. The MCP surface stays exactly four tools; the memory surface stays
   exactly five. Nothing in product core imports a provider.
3. **No standing database input.** D-030 lapsed; this design neither requires
   nor justifies obtaining access material. Every fixture is synthetic.
4. **No replacement of `wechatdb`.** Its source is not edited by this design.
5. **No replacement of Rion.** D-017's external-reader architecture stands.
6. **No adoption of `wx-cli-again`.** STUDY only — §14.
7. **No FTS, cache, or search optimisation.** Deferred entirely; §7.6 states
   only the constraint any future one must satisfy.
8. **No cross-source deduplication, promotion or equivalence.** D-019 stands:
   equivalence is never inferred.
9. **No new dependency.** Standard library only.

---

## 3. Architecture

```
  product core                   generic boundary                isolated provider
 ┌────────────────┐         ┌───────────────────────┐        ┌────────────────────┐
 │ bridge/  MCP   │         │ bridge/message_source │        │ wechatprovider/    │
 │ memory/        │──uses──▶│                       │◀─uses──│   ShardDiscovery   │
 │ shadow/ ai/    │         │  MessageSource        │        │   ShardRouter      │
 │ core/   app    │         │  Normalized{Msg,Conv} │        │   IdentityResolver │
 └────────────────┘         │  SourceStatus         │        │   ProviderResult   │
         ▲                  │  MessageSourceError   │        └─────────┬──────────┘
         │                  │  COVERAGE_* (owner)   │                  │ uses
         │                  │  ReadWindow           │                  ▼
         │                  │  ReadFreshness        │        ┌────────────────────┐
         │                  │  ReadCoverage         │        │ wechatdb/ parser   │
         │                  │  ReadResult[T]        │        │   (UNCHANGED)      │
         │                  └───────────────────────┘        └────────────────────┘
         │                              ▲
         │                              │ imports COVERAGE_* and the read types
         │                  ┌───────────┴───────────┐
         └── NEVER imports  │ memory/memory_store   │
             wechatdb or    │ memory/memory_ingest  │
             wechatprovider │ memory/memory_query   │
                            └───────────────────────┘

  existing sources, both of which will author their own coverage:
      bridge/store_access.StoreMessageSource        (visual — production path)
      bridge/rion_reader_adapter.RionReaderAdapter  (external reader)
```

Four rules, and the absent arrow is the point:

- **Provider → generic** is required: the provider constructs the read types.
- **Provider → `wechatdb`** is required and is the only consumer of the parser.
- **memory → generic** is required and already exists.
- **Generic → provider**, **generic → memory**, and **product core → provider**
  are forbidden, enforced by test (§10, T-11) rather than by intention.

`wechatprovider/` would use the repository's established flat cross-tree import
style — `from message_source import ...` with `bridge/` supplied on `sys.path`
by the caller, identical to `memory/memory_ingest.py`. No new import mechanism.

---

## 4. Data flow

### 4.1 A windowed read through a multi-part provider

```
1. ShardLocator.entries()          injected; lists exactly what it was given
                                   no glob, no walk, no default root

2. ShardDiscovery.catalogue()      classify by name shape alone; open nothing
   → ShardInventory                every entry becomes exactly one descriptor
                                   nothing is ever dropped

3. ShardDiscovery.probe()          open read-only; recognise schema; take
   → ShardInventory                time bounds and conversation digests
                                   key set is identical to step 2's

4. ShardRouter.plan(window, limit) every key lands in steps ∪ excluded,
   → RoutingPlan                   exactly once, each with a reason token

5. traversal                       per planned part, per table:
                                     wechatdb.parse_conversation(...)
                                   collect limit + 1 matching records
   → ProviderReadOutcome           so "there is more" is measured, not inferred

6. ShardRouter.classify_stop()     exhausted | safe | unsafe
   → TraversalOutcome

7. IdentityResolver.mappings()     (session_names, display_names) — the exact
                                   pair wechatdb.parse_conversation accepts

8. ProviderResult → ReadResult     the single translation point; all shard and
   assemble ReadCoverage           schema vocabulary stops here
   assemble ReadFreshness

9. ReadResult[NormalizedMessage]   items + coverage. Nothing else.
```

### 4.2 The same shape for a source that is not composite

A non-composite source skips steps 1–7 entirely and assembles a `ReadCoverage`
from what it does know. `StoreMessageSource` knows whether it filled the
caller's limit and knows `MAX(first_observed_at)` over the requested scope from
one indexed query. `RionReaderAdapter` knows the reader's own `has_more`, and
knows whether its internal conversation sweep hit
`RECENT_CONVERSATION_SCAN_LIMIT`. Both are enough to author honest coverage;
neither requires the shard machinery.

### 4.3 What no source ever does

Falls back to another source; substitutes a different answer for the one asked;
writes, creates, checkpoints, truncates or copies anything; constructs a path;
takes a path from a client request; or puts coverage inside a message.

---

## 5. The generic boundary — exact type shapes

Everything in this section lands in `bridge/message_source.py`. It stays
stdlib-only: `TypeVar` and `Generic` come from `typing`, which the existing
guard already permits, so
`test_the_protocol_depends_on_no_reader_technology` keeps passing **unmodified**.
No identifier here contains a forbidden substring, and no value names a vendor,
a schema, a transport, a path or a file.

### 5.1 Coverage tokens — moved, not duplicated

The four tokens move to `bridge/message_source.py`, which becomes their **single
owner**. `memory/memory_store.py` imports them from there and re-exports them
through its existing `__all__`, so every current caller — `memory_ingest`,
`memory_retrieval`, `memory_query`, the tests — keeps working with no edit.

```python
#: The source accounted for the whole requested window.
COVERAGE_COMPLETE: str = "observed_complete"

#: The source was read, and the window was not covered in full, or the source
#: cannot say that it was.
COVERAGE_PARTIAL: str = "observed_partial"

#: The source was asked and could not answer for the requested scope.
COVERAGE_UNAVAILABLE: str = "unavailable"

#: Nothing has been observed for the requested scope. Distinct from a complete
#: empty read, and never stored as a coverage row (§6.8).
COVERAGE_NOT_OBSERVED: str = "not_observed"

#: Every status a read may carry, including NOT_OBSERVED. Deliberately a
#: different set from ``memory_store.COVERAGE_STATES``, which is the smaller
#: set a stored row may carry.
READ_COVERAGE_STATES: frozenset[str] = frozenset({
    COVERAGE_COMPLETE, COVERAGE_PARTIAL,
    COVERAGE_UNAVAILABLE, COVERAGE_NOT_OBSERVED,
})
```

**Why moved rather than mirrored.** Two copies of a token pinned by an equality
test is still two copies: such a test proves they are equal today and does
nothing about the day someone adds a fifth state to one side. The direction of
the move is the one the repository already uses — `memory` imports
`message_source`, and `message_source` imports nothing — so the move creates no
cycle and needs no new import inside the guarded module, because a string
constant has no import.

`memory_store.COVERAGE_STATES` — the storable subset — stays where it is. It is
a statement about the store's schema, not about the vocabulary.

### 5.2 `ReadWindow`

```python
@dataclass(frozen=True)
class ReadWindow:
    """The scope a read was asked for, in Unix seconds. ``None`` is unbounded.

    Carried on every coverage, so completeness is never unqualified:
    ``observed_complete`` means complete *for this window* and never complete
    for all of history.
    """

    start: float | None = None
    end: float | None = None

    def __post_init__(self) -> None:
        """Rejects ``start > end``: an inverted window is a caller defect, not
        an empty answer."""

    @property
    def is_bounded(self) -> bool:
        return self.start is not None or self.end is not None

    @property
    def is_open_ended(self) -> bool:
        return self.end is None

    def contains(self, moment: float) -> bool: ...
```

### 5.3 `ReadFreshness`

```python
#: The read and the source's own newest moment agree: nothing the source knows
#: about is newer than what came back.
FRESHNESS_CONSISTENT: str = "evidence_consistent"

#: The source's own records name something newer than the newest item read.
#: A statement about two timestamps, not a verdict about age.
FRESHNESS_POTENTIALLY_STALE: str = "evidence_potentially_stale"

#: One of the two values needed for the comparison is absent, so no comparison
#: was made. Not a synonym for consistent.
FRESHNESS_UNKNOWN: str = "freshness_unknown"

READ_FRESHNESS_STATES: frozenset[str] = frozenset({
    FRESHNESS_CONSISTENT, FRESHNESS_POTENTIALLY_STALE, FRESHNESS_UNKNOWN,
})


@dataclass(frozen=True)
class ReadFreshness:
    """Whether a read reached what the source itself claims to hold.

    There is no ``fresh`` boolean and no threshold, deliberately and in the
    same spirit as D-023: this layer reports the two moments and their
    relation. Whether a gap matters is a product or agent decision made with
    both numbers in view, never a verdict handed down here.

    ``read_through`` is the newest item this read actually returned.
    ``source_reported_at`` is the newest moment the source states it holds for
    the requested scope, from the source's own records.
    """

    status: str
    read_through: float | None = None
    source_reported_at: float | None = None

    def __post_init__(self) -> None:
        """Enforces, raising :class:`ReadContractError`:

        1. ``status in READ_FRESHNESS_STATES``
        2. ``status == FRESHNESS_UNKNOWN`` **iff** either moment is ``None``
        3. ``status == FRESHNESS_POTENTIALLY_STALE`` **iff**
           ``source_reported_at > read_through``
        """

    @classmethod
    def compare(
        cls, *, read_through: float | None, source_reported_at: float | None
    ) -> "ReadFreshness":
        """The single place the comparison is made.

        Exact, with no tolerance, no rounding beyond the second-normalisation
        every timestamp already receives, and no constant other than the
        comparison itself.
        """
```

### 5.4 `ReadCoverage`

```python
#: The caller's limit cut the answer short.
READ_REASON_LIMIT_REACHED: str = "limit_reached"

#: Part of the read refused. Spelled identically to the token
#: ``memory_ingest`` already uses, so no translation table exists.
READ_REASON_SOURCE_ERROR: str = "source_error"

#: The source holds a part it could not characterise at all.
READ_REASON_SEGMENT_UNKNOWN: str = "segment_unknown"

#: The source holds a part it understood but could not read.
READ_REASON_SEGMENT_UNAVAILABLE: str = "segment_unavailable"

#: The read stopped without accounting for what it had not visited.
READ_REASON_TRAVERSAL_INCOMPLETE: str = "traversal_incomplete"

#: The source cannot say whether more exists beyond what it returned.
READ_REASON_COMPLETENESS_UNSTATED: str = "completeness_unstated"

#: The source names material newer than this read, inside the requested window.
READ_REASON_SOURCE_AHEAD: str = "source_ahead_of_read"

READ_REASONS: frozenset[str] = frozenset({
    READ_REASON_LIMIT_REACHED,
    READ_REASON_SOURCE_ERROR,
    READ_REASON_SEGMENT_UNKNOWN,
    READ_REASON_SEGMENT_UNAVAILABLE,
    READ_REASON_TRAVERSAL_INCOMPLETE,
    READ_REASON_COMPLETENESS_UNSTATED,
    READ_REASON_SOURCE_AHEAD,
})


@dataclass(frozen=True)
class ReadCoverage:
    """What one read is entitled to claim about the window it was asked for.

    Structure, truncation and freshness are three different facts on three
    different fields. Collapsing them into one verdict is what produces "it
    said complete and it wasn't".
    """

    status: str
    window: ReadWindow
    freshness: ReadFreshness
    reasons: tuple[str, ...] = ()
    observed_through: float | None = None
    complete_through: float | None = None
    truncated: bool = False
    item_count: int = 0

    def __post_init__(self) -> None:
        """Enforces §6.3, raising :class:`ReadContractError`."""

    @property
    def is_complete(self) -> bool:
        return self.status == COVERAGE_COMPLETE

    @property
    def windowed(self) -> bool:
        """Whether the caller constrained the scope.

        Derived from ``window`` rather than stored, because a stored flag can
        contradict the window sitting beside it. Windowed and truncated are
        different facts: a windowed read can be perfectly complete for its
        window, and a truncated read is incomplete whatever its window.
        """
        return self.window.is_bounded

    def payload(self) -> dict[str, Any]:
        """Tokens, counts and timestamps only. Never a name, path, or text."""


class ReadContractError(Exception):
    """A read claimed more than its evidence supports.

    Raised at construction and never caught to produce a softer answer: this
    is a defect in a source, not a runtime condition a caller handles.
    """
```

### 5.5 `ReadResult[T]`

```python
T = TypeVar("T")


@dataclass(frozen=True)
class ReadResult(Generic[T]):
    """Items plus what the source is entitled to claim about them.

    An empty ``items`` is not an answer on its own. It equals "there is
    nothing" only when ``coverage`` says the window was actually covered, and
    :attr:`trustworthy_empty` is the one property a caller must read before
    writing "no messages".
    """

    items: tuple[T, ...]
    coverage: ReadCoverage

    def __post_init__(self) -> None:
        """Rejects ``coverage.item_count != len(items)``, and rejects items
        carried alongside ``unavailable`` or ``not_observed`` coverage."""

    @property
    def trustworthy_empty(self) -> bool:
        return not self.items and self.coverage.status == COVERAGE_COMPLETE
```

`ReadResult` carries **items and coverage, and nothing else**. Provenance stays
where it already is: on the bridge's response envelope, which names the source
for the whole answer, exactly as it does today.

### 5.6 What `MessageSource` becomes

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

Three decisions are load-bearing here.

1. **Collections return `ReadResult[...]`, not bare lists.** A list cannot
   express a partial answer, so a source holding one is forced to choose between
   discarding its coverage and raising on every imperfect read. Both are the
   failure this design exists to remove.
2. **`status()` keeps returning `SourceStatus`.** Readiness is not per-read
   coverage. "Can you answer at all" and "how much of this window did you cover"
   are different questions with different lifetimes, and a source that is ready
   can still answer partially.
3. **Coverage is envelope metadata and never enters a message.**
   `NormalizedMessage.payload()` keeps its exact ten keys. A per-message
   coverage field would push the difference between sources out to the skill,
   which is what the payload projection exists to prevent.

The change to the Protocol is staged, not flag-day; §11 sequences it so no
caller is ever broken and the four MCP tools' wire shape never moves.

---

## 6. Coverage semantics

### 6.1 What each status means

| Status | Means | Never means |
|---|---|---|
| `observed_complete` | the source can **account for the full requested window** — every part that could hold matching items was read, nothing was cut short, and the source can say there is no more | "fewer items came back than the limit" |
| `observed_partial` | a **successful** read with known incompleteness *or* unresolved uncertainty: a limit was hit, a part refused, a traversal stopped unexplained, or the source cannot state whether more exists | a failure; partial answers carry real items and real evidence |
| `unavailable` | the source was asked and **could not answer for the requested scope**, having enumerated something to try | "the source does not exist" |
| `not_observed` | the source has **no observation for the requested scope** — nothing was looked at, and nothing refused either | "we looked and found nothing" |

The `observed_complete` rule is the whole point of D-031: completeness is a
claim about the *window*, made by the layer that can see the parts, the page
signals and the stop condition. Item count is not evidence for it.

### 6.2 `unavailable` versus `MessageSourceError` — the refusal split

Existing refusal semantics are preserved exactly, and the rule that decides
between them is *whether an envelope carrying evidence can be built at all*.

**Raise `MessageSourceError`** — unchanged from today — when the source cannot
produce any envelope: the reader is not configured, the executable is missing,
a reply is malformed, a timeout occurs, an argument is invalid, the requested
conversation is unknown, paging is unsupported, or a contract invariant is
violated. These already raise, `bridge/tests/test_reader_boundary.py` pins them,
and no caller falls back to another source.

**Return `ReadResult` with `COVERAGE_UNAVAILABLE`** when the source *did*
enumerate what it would have had to read, and none of it could be read for the
requested scope. A raise would destroy that evidence — "there are parts and not
one answered" is a stronger, more useful statement than an exception.

Both paths converge downstream: `memory_ingest` already turns a caught
`MessageSourceError` into `CoverageRecord(status=COVERAGE_UNAVAILABLE,
reason=REASON_SOURCE_ERROR)`, and a returned `unavailable` coverage records the
same status with the reasons the source supplied. Nothing about the
no-silent-fallback guarantee changes, in either path.

### 6.3 Invariants — enforced at construction

`ReadCoverage.__post_init__` raises `ReadContractError` unless all hold:

1. `status in READ_COVERAGE_STATES`.
2. every reason is in `READ_REASONS`; reasons are deduplicated and sorted, so
   two coverages describing the same situation compare equal.
3. `status == COVERAGE_COMPLETE` ⟹ `reasons == ()` **and** `truncated is False`.
4. `status == COVERAGE_PARTIAL` ⟹ `reasons != ()`.
5. `status == COVERAGE_UNAVAILABLE` ⟹ `reasons != ()`, `item_count == 0`,
   `observed_through is None`, `complete_through is None`. "Could not answer"
   must always say which kind of could-not.
6. `status == COVERAGE_NOT_OBSERVED` ⟹ `reasons == ()`, `item_count == 0`,
   both boundaries `None`, and `freshness.status == FRESHNESS_UNKNOWN`.
   Nothing was looked at, so there is nothing to explain and nothing to compare.
7. `truncated is True` ⟹ `READ_REASON_LIMIT_REACHED in reasons`.
8. `complete_through is not None` ⟹ `status == COVERAGE_COMPLETE`. A partial
   read cannot name a sub-window it covered completely, because the gap could
   be anywhere in it.
9. `observed_through is not None` ⟹
   `status in (COVERAGE_COMPLETE, COVERAGE_PARTIAL)`.
10. `READ_REASON_SOURCE_AHEAD in reasons` ⟹
    `freshness.status == FRESHNESS_POTENTIALLY_STALE`.
11. `item_count` is non-negative.

Invariant 3 is the structural form of the whole design: a source that collected
any downgrade reason **cannot construct** a complete coverage. The downgrade is
not a rule someone remembers to apply; it is a rule the object must satisfy to
exist.

### 6.4 Freshness is orthogonal, and so is truncation

- **Freshness never changes structure by itself.** A source whose own records
  name something newer than the read gets `evidence_potentially_stale`. That
  alone does not make the read partial.
- **It downgrades structure only when it bites the window.** Add
  `source_ahead_of_read` — and therefore `observed_partial` — only when the
  window is open-ended, or when `source_reported_at <= window.end`. If the
  newer material lies *outside* the requested window, the read is still
  complete for that window and freshness reports the fact on its own field.
  Downgrading unconditionally would make every bounded historical query
  permanently partial, which is both false and useless.
- **Unknown freshness does not downgrade by itself either.** Completeness comes
  from structural accounting — every part read, nothing cut short — and a
  source that can prove that is complete whether or not it can also declare its
  own newest moment. What a source may **not** do is treat an absent boundary as
  evidence of completeness: a source whose only completeness evidence *is* its
  declared boundary reports `completeness_unstated` when that boundary is
  missing, which is a structural reason and downgrades on its own terms.
- **A timestamp mismatch never alters data.** It is recorded, never repaired,
  never used to filter, and never used to re-order.
- **Truncated and windowed are orthogonal to both.** `windowed` says the caller
  narrowed the scope; `truncated` says the answer was cut short by the limit;
  `freshness` says whether the read reached the source's own boundary. A read
  can be windowed, untruncated, complete and potentially stale all at once.

### 6.5 Boundaries

- `observed_through` — the latest moment the source is known to have looked at:
  `window.end` for a bounded window fully traversed, otherwise the newest item
  read, and `None` after an unexplained stop, which knows nothing about how far
  forward it looked.
- `complete_through` — equal to `observed_through` when the status is complete,
  `None` otherwise.

These are the same definitions `memory/memory_freshness.py` already documents,
so a future ingestor maps `ReadCoverage` onto a `CoverageRecord` by copying
fields, with no translation table.

### 6.6 No hidden thresholds

No age constant, no clock comparison, no "recent enough" check, and no `fresh`
boolean exists anywhere in §5 or §6. The only numeric comparison is
`source_reported_at > read_through` on two values the source itself supplied.
T-12 (§10) asserts this by AST scan.

### 6.7 The truth table

`TE` = `trustworthy_empty`.

| # | items | status | freshness | truncated | TE | What a caller may say |
|---|---|---|---|---|---|---|
| 1 | n > 0 | `observed_complete` | `evidence_consistent` | no | — | "these are all the messages in the window" |
| 2 | 0 | `observed_complete` | `evidence_consistent` | no | **yes** | "there are no messages in this window" |
| 3 | n ≥ 0 | `observed_complete` | `evidence_potentially_stale` | no | as #1/#2 | complete *for this window*; must also state that the source names newer material **outside** it |
| 4 | n ≥ 0 | `observed_complete` | `freshness_unknown` | no | as #1/#2 | complete on structural accounting alone; unknown freshness never downgrades by itself. A source whose only completeness evidence is its own declared boundary reports row 8 instead when that boundary is absent (§6.4) |
| 5 | n > 0 | `observed_partial` + `limit_reached` | any | yes | no | "at least these; more exist above the limit" |
| 6 | n > 0 | `observed_partial` + `segment_unavailable` / `segment_unknown` | any | no | no | "part of this source could not be read; what came back is real, the absence is not" |
| 7 | n > 0 | `observed_partial` + `traversal_incomplete` | any | yes | no | "the read stopped without accounting for the rest" |
| 8 | n > 0 | `observed_partial` + `completeness_unstated` | any | no | no | "the source cannot say whether more exists" |
| 9 | n ≥ 0 | `observed_partial` + `source_ahead_of_read` | `evidence_potentially_stale` | any | no | "the source names newer material inside the window that this read did not return" |
| 10 | 0 | `observed_partial` (any reason) | any | any | **no** | "nothing matched, and the window was not fully covered" — **never** "there are no messages" |
| 11 | 0 | `unavailable` + at least one reason | `freshness_unknown` | no | **no** | "the source could not answer for this scope", naming which kind of could-not |
| 12 | 0 | `not_observed` | `freshness_unknown` | no | **no** | "nothing has been looked at for this scope" |
| 13 | n > 0 | `unavailable` or `not_observed` | — | — | — | **impossible** — `ReadContractError` at construction |

Rows 2 and 10 are the pair that matters: identical `items`, identical `window`,
opposite conclusions, distinguishable only through `coverage`. Any caller
reading `items` alone gets row 10 wrong, every time.

### 6.8 How memory consumes it

`memory_ingest` stops inferring and starts recording what the source said:

- `observed_complete` / `observed_partial` / `unavailable` → a `CoverageRecord`
  carrying that status, the source's reasons, and `window_start` /
  `window_end` from `ReadCoverage.window` — not from `min`/`max` of the items
  returned, which is a statement about the data rather than about the request.
- `not_observed` → **no row is written.** `memory_store.COVERAGE_STATES`
  excludes it by design: "never stored as a row — it is what the absence of a
  row means". Writing one would raise `coverage_status_unknown` from
  `CoverageRecord.__post_init__`. This is the single most easily missed
  consequence of the move, and T-10 pins it.
- `len(messages) < message_limit` is **deleted**, not kept as a fallback. A
  fallback that is wrong in exactly the cases this design exists to catch is
  worse than no fallback; §11 sequences the deletion so it happens only once
  both shipped sources author coverage.

`compose_coverage`, `ComposedCoverage` and `CoverageVerdict` are unchanged. They
already implement the composition rule this design needs — evidence is never
erased, the aggregate is the most cautious reading, and no complete source means
no trustworthy empty.

---

## 7. Provider-internal orchestration

Everything in this section is **provider vocabulary** and must never appear in a
generic module. These are responsibilities and contracts, not implementations.

### 7.1 `ShardDiscovery` — what the source is made of

**Owns:** the inventory of a composite source, and the evidence for each part's
state.

- Two passes, kept separate because they cost different amounts: `catalogue()`
  classifies by name shape and **opens nothing**; `probe()` opens read-only and
  re-classifies.
- Four part states: **known** (catalogued, not opened), **readable** (opened,
  schema recognised, bounds taken), **unknown** (listed, not characterisable),
  **unavailable** (characterised, not readable).
- **Nothing is ever dropped.** The invariant is that `probe()` returns an
  inventory whose key set equals its input's: probing changes what is known
  about a part, never how many parts there are. An entry nobody can characterise
  counts toward the unknown tally for **every** role — if nobody can say what it
  is, nobody can say it is not a message part, and filtering it out by role
  would produce silent omission by way of a filter.
- Parts are identified by a stable opaque digest of the entry name, never by the
  name and never by a path. Safe to log, safe to count, meaningless outside the
  process.
- Time bounds are taken per part and normalised through
  `wechatdb.normalise_timestamp`, so a part mixing second- and
  millisecond-valued creation times cannot report a maximum in the year 5138 and
  dominate every routing decision. Bounds are either **established** or
  **absent**; absent bounds are never guessed.
- The locator is **injected**. There is no implementation that searches a
  filesystem, and the shipped one lists exactly the entries it was constructed
  with.

### 7.2 `ShardRouter` — which parts a windowed query must touch

**Owns:** routing, ordering, and the explanation for stopping.

- **Total accounting.** Every inventory key appears exactly once across the
  plan's included steps and its exclusions, each with a fixed reason token.
  `plan.accounted_keys == inventory.keys` is the structural form of "no silent
  omission": a part cannot leave the inventory without a recorded reason.
- **Inclusion** requires: the part is readable, its role is message, its bounds
  overlap the window, and — for a conversation-scoped read — it holds that
  conversation. A part whose bounds are **not established always overlaps**, and
  is therefore always visited.
- **Ordering** is newest-first by established maximum bound, with
  unestablished-bounds parts sorted **first**, so they are visited rather than
  skipped and any later stop is evaluated against a remainder that is entirely
  bounded.
- **Explainable stop.** `exhausted` when every planned part was visited; `safe`
  when every unvisited planned part is provably older than the boundary already
  reached; `unsafe` otherwise. Because "provably older" is false for
  unestablished bounds, a part with unknown bounds can never be stepped past.
- **Unknown and unavailable parts do not make a stop unsafe.** They were never
  visitable, so the traversal did not skip them; they downgrade coverage
  separately and always. Keeping the two mechanisms apart means neither can mask
  the other.
- The traversal collects `limit + 1` matching records so truncation is
  **measured** rather than inferred from a full page — the same technique
  `memory_retrieval` already uses, for the same reason.

**One structural assumption, graded Hypothesis.** A multi-part container is
roughly time-partitioned. **Evidence from this project: none.** E-022 opened one
part and characterised its interior; it established nothing about how parts
relate. The design is safe regardless: early stop is gated on measured bounds,
so if the assumption is false the router simply never finds a safe stop, visits
everything, and the answer is still correct. The assumption buys efficiency,
never correctness.

### 7.3 `wechatdb` — one connection, one table

**Unchanged.** Its scope stays: one open connection, one table → provider
records. It learns nothing about parts, routing, coverage, or windows. The
provider calls what it already exports — `conversation_tables`, `load_name2id`,
`parse_conversation`, `normalise_timestamp`, `MessageRecord`,
`MessageSchemaError` — and injects the `session_names` and `display_names`
mappings `parse_conversation` already accepts. No edit to the parser is required
by anything in this document.

### 7.4 `IdentityResolver` — names, never guesses

**Owns:** contact, session and group-member display resolution, from the
source's own tables.

- Resolution is a **lookup, never an inference**: no fuzzy match, no edit
  distance, no tokenisation, no model, no heuristic. This is D-022's rule for
  conversation discovery, applied one layer down.
- Precedence is a fixed order over distinct *kinds* of name (a per-room member
  nickname beats a contact remark beats a contact nickname), so it is never a
  choice between two equally good answers.
- **Ambiguity is refused, never resolved.** One identifier yielding two
  different names of the same kind resolves to no name; the identifier is simply
  absent from the mapping handed to the parser, whose existing fallback to the
  sender identifier is then the correct outcome with no parser change.
- **Identity failure is not coverage failure.** A missing name does not make a
  message unobserved; it makes it unnamed. Identity state travels on the
  provider's own diagnostics, never on `ReadCoverage`. Merging them would let a
  missing contact table report the messages as incomplete, which is false.
- Its entire integration surface with the parser is the
  `(session_names, display_names)` pair.

### 7.5 `ProviderResult` → `ReadResult`

**Owns:** the single translation point, and the only place in the provider that
constructs a generic type.

- Maps records to `NormalizedMessage` using the field meanings the contract
  already documents: a database source puts the message's own creation time in
  `first_observed_at`, leaves `visible_time` `None` because it never saw a
  rendered time, and reports `confidence = 1.0` because a decoded row is exact
  and there is no estimator on this path.
- Derives conversation identifiers with the same construction
  `rion_reader_adapter.conversation_identifier` uses, so two readers can never
  disagree about what a conversation's id is.
- Assembles `ReadCoverage` and `ReadFreshness` from the inventory census, the
  plan, the stop outcome, the truncation measurement and the two boundary
  timestamps.
- **No shard, table, column, file, path, digest or schema name escapes.** The
  provider's vocabulary ends here.
- **Diagnostics may carry aggregate counts and category tallies** — how many
  parts were readable, unknown, unavailable — and never a raw identifier, a
  name, a path, or message content. They are a provider-side surface, not part
  of `ReadResult`.

### 7.6 Deferred: FTS, cache, search optimisation

Not designed, not specified, not implemented. One constraint is recorded so the
question is not re-litigated later: **an indexed or cached answer must never
claim coverage stronger than an unindexed read of the same window would.** A
cached answer needs its own `observed_through`, and inventing one is exactly the
class of error this envelope exists to prevent.

---

## 8. Error handling

| Condition | Outcome | Raises or reports |
|---|---|---|
| reader not configured / executable absent / timeout / malformed reply | unchanged from today | raises `MessageSourceError` |
| unknown conversation, unsupported paging, invalid argument | unchanged from today | raises `MessageSourceError` |
| one part cannot be characterised | that part is `unknown`; coverage gains `segment_unknown` | caught, becomes coverage |
| one part will not open, has an unrecognised schema, or a read refuses | that part is `unavailable`; coverage gains `segment_unavailable` | caught, becomes coverage |
| every part refuses, after enumeration | `ReadResult` with `unavailable` | returned, not raised (§6.2) |
| nothing enumerable for the scope | `ReadResult` with `not_observed` | returned, not raised |
| the caller's limit is hit | `truncated` + `limit_reached` | returned |
| the traversal stops with an unexplained remainder | `traversal_incomplete` | returned |
| a source cannot state whether more exists | `completeness_unstated` | returned |
| a coverage or result claims more than its evidence | `ReadContractError` | raises; never caught to soften an answer |

**The governing rule:** a per-part failure is never fatal and never silent — it
becomes a coverage downgrade with a fixed reason, every time. A failure that
would make the whole answer meaningless raises. **There is no fallback to
another source, at any level.** Every token stays fixed, lowercase and
content-free; no path, filename, chat title, sender, message text, SQL, or
captured output ever reaches a caller, a log line, or an exception.

---

## 9. Privacy and security

1. **No acquisition surface.** No key, salt, passphrase, cipher parameter,
   SQLCipher call, `PRAGMA key`, decryption, process-memory read, debugger
   interaction or code-signature operation exists anywhere in this design.
   Asserted by source scan (§10, T-13).
2. **No location knowledge.** No WeChat container path, bundle identifier,
   directory name or search root. Every input arrives through an injected
   locator. The provider cannot find a database; it can only be handed one.
3. **Read-only, with no silent optimisation.** Read-only open only. The
   `immutable` optimisation is **forbidden and asserted against**: it makes
   SQLite ignore the write-ahead log, silently dropping not-yet-checkpointed
   messages. The interface gate already recorded a WAL-resident row as a real
   shape, so this is a live case. When the log cannot be read, the honest
   outcome is an unavailable part and a visible coverage downgrade.
4. **Nothing is written.** No file created, no WAL checkpointed, no copy, no
   plaintext cache, no export. The source is left byte-identical.
5. **Digests, not names, in diagnostics.** No coverage token, reason, count or
   status field can carry a name, and `ReadCoverage.payload()` emits tokens,
   counts and timestamps only.
6. **No real data anywhere.** Every fixture is synthetic and built in code, in
   the style of `wechatdb/tests/fixtures.py`. No real chat content, `wxid`, path
   or digest is committed.
7. **D-002, D-005, D-011 and R-003 are untouched.** Nothing drives, activates,
   scrolls or writes to WeChat; nothing runs unattended; nothing invokes or
   vendors a `wechat-cli`.
8. **Nothing ships.** No product module imports the provider, it is in no build
   phase and no packaged runtime, and it is not in the shipped dependency graph.

---

## 10. Test matrix

All synthetic. Multi-part fixtures are built in code as `tmp_path` SQLite
databases whose column layout matches what `wechatdb` reads, populated with
invented text, `wxid_fixture_*` identifiers and chosen timestamps.

Every test must be verified to **fail when the behaviour it pins is reverted**.
A coverage test that passes against a source which always reports
`observed_complete` is worthless.

| # | Case | Asserted |
|---|---|---|
| **T-1** | **All parts readable** | 3 parts, disjoint ranges, all open and recognised; `observed_complete`; `reasons == ()`; `truncated is False`; `complete_through == observed_through`; every expected message present exactly once |
| **T-2** | **One unknown part** | T-1 plus a listed entry matching no known shape; it appears in the plan's exclusions with a reason; `observed_partial`; `segment_unknown in reasons`; **messages identical to T-1** — an unknown part changes the claim, not the content; constructing a complete coverage with that reason raises `ReadContractError` |
| **T-3** | **One unavailable part** | variants for "will not open" and "opens with unrecognised schema"; `observed_partial`; `segment_unavailable in reasons`; the other parts' messages **are still returned** — a per-part failure is not fatal. Third variant: every part refuses → `unavailable`, `items == ()`, `trustworthy_empty is False` |
| **T-4** | **Query spanning parts** | one conversation present in all 3 parts; all 3 planned; messages correctly ordered across part boundaries; `observed_complete`. Variant: a fourth part lacks that conversation → excluded **for cause**, coverage still complete — excluded-for-cause is accounted for, not omitted |
| **T-5** | **Safe early stop** | descending established ranges, `limit + 1` collected in the newest part, every unvisited part provably older; stop is `safe`; `truncated is True` with `limit_reached`; `traversal_incomplete` **not** present; `observed_through` is stated. Control variant with a larger limit → `exhausted`, `truncated is False`, `observed_complete`, proving the partial verdict came from truncation alone |
| **T-6** | **Unsafe early stop** | variant (a) an unvisited part has no established bounds; variant (b) an unvisited part's maximum lies inside the window | both `unsafe`; reasons contain **both** `limit_reached` and `traversal_incomplete`; `observed_partial`; `observed_through is None` — an unexplained stop may not claim how far forward it looked. Variant (c): the router orders the unbounded part first, so with a larger limit it is visited and the stop becomes safe |
| **T-7** | **Timestamp freshness mismatch** | source declares `T_src`; newest readable message is `T_read < T_src`. (a) open-ended window and (b) `window.end` between them → `evidence_potentially_stale`, `source_ahead_of_read in reasons`, `observed_partial`, **both timestamps carried**. (c) `T_src` outside the window → `evidence_potentially_stale` **and** `observed_complete`. (d) `T_src` absent from a source whose only completeness evidence is that boundary → `freshness_unknown` **and** `completeness_unstated`, so `observed_partial` — the downgrade comes from the structural reason, never from the freshness token. (e) `T_src` absent, but the traversal accounted for every part → `freshness_unknown` **and** `observed_complete`. No test anywhere compares against a clock or a constant |
| **T-8** | **Identity resolution** | remark beats nickname; a room nickname applies inside that room and not outside it; an identifier with two conflicting same-kind names resolves to **no name**, is absent from the mapping, and **neither candidate name appears anywhere in the result**; identity state degrades while `coverage.status` is **unaffected**. Variant with no readable identity part: conversations identify by digest per the parser's documented fallback, and **coverage is still complete** |
| **T-9** | **Zero messages: complete vs incomplete** | (a) all parts readable, window genuinely empty → `items == ()`, `observed_complete`, `trustworthy_empty is True`. (b) identical window, one part unavailable → `items == ()`, `observed_partial`, `segment_unavailable`, `trustworthy_empty is False`. The two results carry an **identical `items` tuple and an identical `window`** and differ only in coverage. **The single most important test in the suite** |
| **T-10** | **Source-internal truncation below the caller's limit** | a source returns 3 items for a 200-item request while having truncated internally → `observed_partial`, never complete. Run against the visual store, the Rion adapter's bounded sweep, and the provider. Additionally: `memory_ingest` records the **source's** status, not `len(messages) < message_limit`; a `not_observed` read writes **no coverage row** and does not raise `coverage_status_unknown`; and the length-inference branch is gone from the ingestor |
| **T-11** | **Architecture guard** | AST scan, never raw text, so prose describing what a module avoids is not mistaken for a dependency. No module under `bridge/`, `memory/`, `shadow/`, `ai/`, `core/`, nor `app.py` / `mcp_server.py` imports `wechatdb` or the provider package at any depth (the existing guard generalised from one candidate name to a tuple); `bridge/message_source.py`'s import set is still `⊆ {__future__, dataclasses, typing}`; the provider imports nothing from `memory/`, `shadow/`, `ai/` or `core/` |
| **T-12** | **No hidden thresholds, no leaked vocabulary** | AST scan over identifiers and non-docstring string constants: `bridge/message_source.py` contains none of `Msg_`, `Name2Id`, `real_sender_id`, `local_type`, `shard`, `message_0`; no identifier in the coverage or freshness assembly matches `fresh` as a standalone flag, `stale_after`, `max_age` or `threshold`; the assembly binds no numeric constant other than `0` |
| **T-13** | **Provider source scan** | the provider package contains none of `Containers`, `xwechat_files`, `db_storage`, `com.tencent`, `/Users/`, `PRAGMA key`, `enc_key`, `salt`, `sqlcipher`, `task_for_pid`, `lldb`, `codesign`, `sudo`, `shell=True`, `immutable` |
| **T-14** | **Existing sources author conservative coverage** | `StoreMessageSource`: fewer items than the limit **and** the scope's newest stored moment reached → complete; limit filled → `observed_partial` + `limit_reached` + `truncated`. `RionReaderAdapter`: the reader's `has_more` is **consumed** — true → partial with `limit_reached`; absent → partial with `completeness_unstated`, never complete; the conversation sweep hitting its internal scan bound → partial with `traversal_incomplete`. Neither source ever reports complete on evidence it does not have |
| **T-15** | **Envelope shape is unchanged** | `NormalizedMessage.payload()` still has exactly its ten keys, with no coverage field; the four MCP tools' response shape is byte-identical to today's; `ReadCoverage.payload()` contains only tokens, counts and timestamps |
| **T-16** | **Invariants** | every invariant in §6.3 and §5.3 has a test constructing the violating object and asserting `ReadContractError`, including the impossible row 13 of §6.7 |

---

## 11. Migration sequence

Staged so that no caller is ever broken, the wire shape never moves, and each
step is independently revertible. **No production wiring occurs in this phase:**
M1–M4 change internal types and honesty; M5 and M6 are gated.

**M1 — Move the coverage tokens.** Define the four `COVERAGE_*` tokens in
`bridge/message_source.py`. `memory/memory_store.py` imports them (with the
`sys.path` fallback `memory_ingest` already uses) and re-exports them through
its existing `__all__`. No caller changes. `memory/tests/test_layering.py` needs
no edit — `message_source` is already an allowed import. The `message_source`
guard needs no edit — constants require no import.

**M2 — Add the read types.** `ReadWindow`, `ReadFreshness`, `ReadCoverage`,
`ReadResult[T]`, `ReadContractError`, the reason tokens and the state sets, with
their invariants and tests. `READ_REASON_LIMIT_REACHED` and
`READ_REASON_SOURCE_ERROR` are spelled identically to the tokens
`memory_ingest` defines today; `memory_ingest` imports them from
`message_source` rather than keeping a second copy. Nothing consumes the new
types yet.

**M3 — Sources author their coverage.** `StoreMessageSource` and
`RionReaderAdapter` gain coverage-returning read methods **beside** their
existing four, so both shapes exist at once and every current caller keeps
working. `RionReaderAdapter` stops discarding `has_more`, and its bounded
conversation sweep reports its own limit.

**M4 — Memory consumes rather than infers.** `MemoryIngestor.ingest_from_source`
reads the source's `ReadCoverage`; `not_observed` writes no row; the
`len(messages) < message_limit` branch is **deleted** once both shipped sources
author coverage — not kept as a fallback, because it is wrong in precisely the
cases this design exists to catch.

**M5 — The Protocol migrates (gated).** `MessageSource`'s three collection
methods become `ReadResult`-returning and the transitional list methods are
removed, once every caller — the four MCP tools, `memory_sync`, the shadow
runner path — reads through the envelope. `status()` is untouched throughout.
This step requires an explicit decision on what the four tools do with a partial
answer; a tool that drops coverage on the floor would undo the whole design.

**M6 — The provider package (gated).** Created only after §12's gates are met.
Until then this design is a document, and `wechatdb` remains exactly what it is
today: an isolated candidate that nothing imports.

---

## 12. Promotion gates

**No production wiring in this stage.** Passing every gate below is a
precondition for *considering* promotion, never a grant of it.

**Implementation gates.**

- **G1 — Behaviour.** T-1 … T-16 green, each verified to fail when reverted.
- **G2 — Isolation.** The existing product-import guard, generalised to cover
  the provider package as well as `wechatdb`, green across `bridge`, `memory`,
  `shadow`, `ai`, `core`, `app.py` and `mcp_server.py`.
- **G3 — Vocabulary.** T-12 and T-13 green;
  `test_the_protocol_depends_on_no_reader_technology` still passing
  **unmodified**. That test is the leakage guard and is not to be relaxed.
- **G4 — Honesty.** Every §6.3 and §5.3 invariant pinned by a construction test.

**Decision gates.**

- **P1 — An explicit product decision to promote,** recorded in `Decisions.md`,
  naming what the provider is promoted *to*. An import statement is not a
  promotion decision, and D-017's provider-isolation amendment stays intact:
  product core still may not import a provider, and promotion may happen only
  through the Reader contract.
- **P2 — Real multi-part verification.** **Not satisfiable today.** D-030
  lapsed, no access material is retained, and a repeat acquisition requires a
  new explicit decision and fresh per-occasion consent. **This design does not
  reopen D-030 and supplies no argument for reopening it.** §7.2's partitioning
  assumption stays unverified until P2, and correctness does not depend on it.
- **P3 — The licensing review** D-017 still records as owed.
- **P4 — Contract decision (M5).** What the four MCP tools do with a partial
  answer, decided before the Protocol migrates.
- **P5 — Visual remains the default production source.** Promotion does not
  change `selected_source_name()`'s `visual` default. A database source stays
  explicitly selected, off by default, and fail-closed when unselected.

**Standing limits, unchanged by this design.** Complete-container coverage and
future-WeChat-version compatibility remain **unproven**; E-022's evidence is
`Verified (scoped)` to one operator's current `message_0` and must not be
generalised. Standing acquisition or input is a separate decision.

---

## 13. Alternatives

### Alternative 1 — Leave the single-DB candidate unchanged

Make no orchestration layer; leave the length-inference in `memory_ingest`.

**For.** Zero new code and zero new surface; isolation is already proven;
nothing can regress.
**Against.** It answers none of §1.2. A single-database parser handed a
multi-part container answers about one part and cannot say so, and the coverage
claim the system publishes today stays a guess made by the layer furthest from
the evidence. The promotion question stays permanently unanswerable because
there is nothing to gate.
**Verdict.** This is what is true *today* and it stays true until §12 is met.
Rejecting it as the permanent end state is not the same as abandoning it now.

### Alternative 2 — Adopt `wx-cli-again` wholesale

**For.** It reportedly already handles multi-file containers, so routing would
be someone else's problem.
**Against.** (a) Licence and provenance are unreviewed — the H5A review process
exists precisely because this cannot be assumed. (b) Such a tool's value is
concentrated in **acquisition**, which non-goal 1 forbids in the shipped
dependency graph; adopting it wholesale imports exactly the capability D-005
excludes. (c) It would place WeChat schema knowledge where product core depends
on it, which D-017 forbids. (d) R-003 already rejected invoking or vendoring a
stock `wechat-cli`; adopting a successor wholesale is the same route under a new
name. (e) It drags a foreign runtime into the graph for a thin slice of
behaviour.
**Verdict.** **Rejected.**

### Alternative 3 — Clean-room provider orchestration around `wechatdb` — **RECOMMENDED**

**For.** (a) D-017 is satisfied by construction: shard and schema vocabulary
stays inside the provider, and the envelope is source-neutral. (b) `wechatdb`
keeps its single provable responsibility and is not edited. (c) No acquisition
capability is introduced — the provider is *handed* its inputs and never
searches. (d) Coverage honesty becomes a **type invariant**: a complete coverage
holding a downgrade reason cannot be constructed. (e) It is fully testable
without real data, which is the only testing available. (f) It benefits the two
shipped sources immediately, before any provider exists — M3 and M4 fix a real
defect in the visual path's recorded coverage.
**Against.** New code that is not wired, carrying maintenance cost with no
immediate product benefit; and routing efficiency rests on an unverified
structural assumption.
**Mitigation.** The assumption being false degrades efficiency, never
correctness (§7.2). The unwired cost is bounded by §12, which keeps the provider
out of the graph until a decision admits it.

**Recommendation: Alternative 3.**

### Why no parser replacement

`wechatdb` is the only component in this area with `Verified` synthetic evidence
and `Verified (scoped)` real evidence behind it (F-036, F-037), and its two
review defects were each reproduced as a failing test before being fixed
(`7919e8f`, `cc89e31`). The gap this design addresses is **not parsing** — it is
orchestration, coverage and honest incompleteness reporting. Replacing a proven
parser to obtain an unproven one, in order to solve a problem the parser does
not have, would discard the project's best evidence for no gain. The operator
direction is explicit: keep `wechatdb`; do not replace it.

---

## 14. Clean-room note

`wx-cli-again` is a **STUDY-only** reference. It may be read to understand *what
behaviour a correct multi-part reader exhibits* — that a container is
partitioned, that a conversation can span partitions, that a session record may
carry its own latest-message moment. It supplies **questions, not answers**.

**No source code, SQL text, query shape, test fixture, fixture datum, comment,
or identifier-naming scheme may be copied from it into this repository.**
Everything in §5–§10 is expressed in this project's own vocabulary and derived
from this project's own `wechatdb`, `bridge/` and `memory/` conventions. This
mirrors how F-036's schema knowledge was handled: public references were read as
a source of *facts*, and no code was copied.

This is **STUDY**, not **ADOPT** and not **REPLACE**.

---

## 15. Resolved questions

1. **Who owns the coverage tokens?** `bridge/message_source.py`, exclusively.
   `memory_store` imports and re-exports them. Two copies pinned by an equality
   test is still two copies. §5.1.
2. **Does `message_source` importing nothing survive the move?** Yes — a string
   constant requires no import, and the existing guard passes unmodified. The
   dependency that *does* appear runs `memory → message_source`, which is the
   direction the repository already uses.
3. **Why not reuse `memory_store.CoverageVerdict` directly?** It answers a
   store's question (what has ever been ingested), not a read's question (what
   this call covered): it carries no window, no truncation, no freshness, and no
   item count. The vocabulary is reused verbatim; the type is not.
4. **Does `MessageSource` change?** Yes — its three collection methods return
   `ReadResult[...]`, because a list cannot express a partial answer. `status()`
   stays `SourceStatus`, because readiness is not per-read coverage. The change
   is staged through M1–M5 so no caller breaks and the wire shape never moves.
5. **Does coverage go on a message?** Never. `NormalizedMessage.payload()` keeps
   its ten keys. Coverage describes the whole answer, which is why it lives on
   the envelope. T-15.
6. **Returned `unavailable` or raised `MessageSourceError`?** Raise when no
   envelope carrying evidence can be built; return `unavailable` when the source
   enumerated what it would have read and none of it could be read. Both
   converge on the same recorded status downstream, and the no-fallback
   guarantee is untouched. §6.2.
7. **What is `not_observed` for, and can it be stored?** It distinguishes
   "nothing has been looked at" from "we looked and found nothing". It is
   **returned** by a read and **never written** as a coverage row —
   `memory_store.COVERAGE_STATES` excludes it by design, and absence of a row is
   how it is recorded. T-10.
8. **Does potentially-stale make a read partial?** Only when the newer material
   lies inside the requested window, or the window is open-ended. Otherwise
   freshness reports the fact on its own field and the read stays complete for
   the window it was asked about. §6.4, T-7(c).
9. **Is there a staleness threshold anywhere?** No. One exact comparison of two
   timestamps the source itself supplied, no clock, no constant, no `fresh`
   boolean. T-12.
10. **Do unknown or unavailable parts make an early stop unsafe?** No — they
    were never visitable, so the traversal did not skip them. They downgrade
    coverage independently and always, and keeping the mechanisms separate means
    neither masks the other. §7.2.
11. **Does an unresolvable name make a message unobserved?** No. It makes it
    unnamed. Identity state never touches `ReadCoverage`. §7.4, T-8.
12. **Does the parser change?** No. `IdentityResolver` produces exactly the
    `(session_names, display_names)` pair `parse_conversation` already accepts;
    that is the entire integration surface. §7.3.
13. **What if the time-partitioning assumption is wrong?** Nothing breaks.
    Unestablished bounds always overlap and never satisfy the early-stop test,
    so such a part is always visited. The assumption buys efficiency, not
    correctness. §7.2.
14. **Where do FTS and caching go?** Nowhere, now. Only one constraint is
    recorded: an indexed or cached answer may never claim coverage stronger than
    an unindexed read of the same window. §7.6.
15. **Does any of this justify obtaining a WeChat database?** No. D-030 lapsed,
    no access material is retained, every test is synthetic, and P2 records real
    multi-part verification as a decision gate that is **not satisfiable today**.
16. **Does this change what ships?** No. Visual capture remains the production
    path and the default, the MCP surface stays at four tools, and nothing in
    product core imports a provider.

---

## 16. What this document changes

Nothing executable. It adds one design document and records one decision
(D-031). It creates, edits or deletes no module, test, dependency, build phase,
environment variable, default or route. `wechatdb` is untouched. The MCP surface
stays exactly four tools, `selected_source_name()` still defaults to `visual`,
and visual capture / OCR remains the production read path.
