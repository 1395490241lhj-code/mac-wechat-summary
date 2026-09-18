# Coverage-aware database reader provider — design

**Date:** 2026-09-18
**Branch:** `feature/hermes-validation-isolation` (unmerged; `head_commit` stays `a928976`)
**Status:** Design only. Nothing here is implemented, wired, routed, or enabled.
**Governing decisions:** D-017 (*amended: provider isolation*), D-002, D-005,
D-011, D-022, D-023, D-030 (**lapsed**), R-003.
**Governing evidence:** E-022, F-036, F-037, `docs/v2/DB_READER_INTERFACE_GATE.md`,
`docs/v2/READER_BOUNDARY_INTEGRATION.md`.

---

## 0. One-paragraph summary

`wechatdb/` can read one plaintext WeChat 4.1+ message database and turn its
conversation tables into `MessageRecord`s. A real WeChat container is not one
database; it is several, and a single conversation's history is spread across
them. Today nothing in this repository can say *which* of those files it looked
at, which it could not characterise, which it could not open, or whether the
answer it produced is complete for the window that was asked about. This spec
designs the missing layer: a **provider-internal** orchestration package that
discovers the parts of a source, routes a time-windowed query across them,
resolves identities, and emits a **generic, source-neutral** result envelope
whose coverage semantics make an incomplete read structurally impossible to
mistake for a complete one. The existing `wechatdb` parser is unchanged. Product
core still depends on nothing but the Reader contract. Promotion remains
unapproved, and this document does not promote anything.

---

## 1. Problem

### 1.1 What is true today

- `wechatdb/parser.py` is a schema layer over **one already-open
  `sqlite3.Connection`**. It enumerates `Msg_<32hex>` tables in that one
  connection, resolves senders through that one connection's `Name2Id`, and
  yields `MessageRecord`. It opens nothing and locates nothing, deliberately.
- E-022 opened exactly **one** real current message database. It held 90 tables:
  84 `Msg_<32hex>` conversation tables plus `Name2Id`, 379,458 rows, one shared
  17-column layout. Bounded samples parsed with zero schema or parse failures
  (F-037).
- The generic Reader contract in `bridge/message_source.py` — `MessageSource`,
  `SourceStatus`, `NormalizedConversation`, `NormalizedMessage`,
  `MessageSourceError` — answers four questions and carries **no** coverage
  statement at all. An empty list means "the source answered and found nothing".
- `memory/` already owns a mature, well-tested coverage and freshness
  vocabulary (`observed_complete` / `observed_partial` / `unavailable` /
  `not_observed`; `observed_through` / `complete_through` / `latest_message_at`;
  no `is_fresh`, no threshold). That vocabulary lives **above** the reader, in
  the memory store, and is populated by the ingestor — not by the reader itself.
- The production read path is **visual capture / OCR**.
  `selected_source_name()` in `bridge/store_access.py` defaults to `visual` when
  `WECHAT_COMPANION_MESSAGE_SOURCE` is unset, so a default build never
  constructs an external reader and never depends on one being importable.

### 1.2 The gap

Three distinct failures follow from having a one-database parser and a
coverage-free contract:

1. **Silent omission by file.** A container whose parts are partly unreadable —
   a locked file, a file whose schema is not recognised, a file the locator
   never listed — produces a shorter answer that looks exactly like a complete
   short answer. Nothing in the current types can express "there were five
   parts and I read three."
2. **Silent omission by traversal.** A `limit`-bounded read across several
   parts must stop somewhere. Stopping is safe only when the parts not visited
   provably cannot contain a message in the requested window. Nothing today can
   distinguish a stop that is provable from a stop that is a guess.
3. **Silent staleness.** A source that records its own "latest message" per
   session can be compared against the newest message actually read. When the
   source says there is something newer than what came back, that is a fact the
   caller must see. Nothing today carries it.

Each of these is the same failure the project already names as the one thing
this boundary exists to prevent: *a quiet fallback would make a partial read
look like a complete one* (`bridge/message_source.py` module docstring).

### 1.3 Why now

F-037's conclusion is explicit: the open question is no longer whether the
parser understands a real schema, it is *"whether to promote this candidate
behind the Reader contract without weakening D-017 or importing acquisition
capability into the product."* A promotion decision cannot be made against a
component that cannot state its own coverage. This design supplies what the
promotion gate would have to be measured against. It does not pass that gate.

---

## 2. Non-goals

Explicitly out of scope. None of these is deferred work with a hidden plan;
each is a boundary this design must not cross.

1. **No acquisition capability of any kind.** No key derivation, no salt, no
   `PRAGMA key`, no SQLCipher, no decryption, no process-memory access, no
   LLDB, no debugger attach, no `task_for_pid`, no code-signature manipulation,
   no WeChat container discovery, no plaintext cache, no shadow copy, no temp
   database. D-005 and R-003 are untouched.
2. **No production routing change.** `selected_source_name()` keeps `visual` as
   its default. The MCP surface stays **exactly four** tools. Nothing in
   `bridge/`, `memory/`, `shadow/`, `ai/`, `core/`, or the app imports the
   provider.
3. **No FTS implementation and no cache implementation.** §9 states only how a
   future search or timeline surface would *reuse* the envelope described here.
   No index, no materialised view, no memoisation, no warm store.
4. **No replacement of `wechatdb`.** Its scope is fixed by §5.3 and its source
   is not edited by this design.
5. **No replacement of Rion.** D-017's external-reader architecture stands. This
   is a second candidate, not a deletion of the first.
6. **No adoption of `wx-cli-again`.** See §3, alternative B, and §11.
7. **No promotion.** No wiring, no default change, no environment variable, no
   settings toggle, no operator-visible surface.
8. **No real-data work.** D-030 has lapsed, no access material is retained, and
   nothing in this design attempts, requires, or justifies obtaining any. Every
   fixture in §12 is synthetic and built in code.
9. **No new dependency.** Python standard library only (`sqlite3`,
   `dataclasses`, `hashlib`, `re`, `typing`), matching what `wechatdb` already
   uses.
10. **No cross-source deduplication, promotion, or equivalence.** D-019's rule
    stands: equivalence is never inferred. This design produces one source's
    answer about one source.

---

## 3. Alternatives and trade-offs

### Alternative A — keep the current single-database candidate unchanged

Leave `wechatdb` exactly as it is and make no orchestration layer at all.

| | |
|---|---|
| **For** | Zero new code. Zero new surface. The isolation boundary is already proven by `test_no_product_module_imports_the_candidate_schema_provider`. Nothing can regress. |
| **Against** | It cannot answer the promotion question. A single-database parser handed a multi-part container answers about one part and cannot say so. Every one of the three failures in §1.2 remains structurally unexpressible. |
| **Cost of choosing it** | The promotion decision stays blocked indefinitely, because there is nothing to gate. |

**This is the status quo and it remains in force until the gates in §11 are
met.** Rejecting A as the permanent end state is not the same as leaving it
today; today it is exactly what is true.

### Alternative B — adopt `wx-cli-again` wholesale

Take the external project's implementation as the reader.

| | |
|---|---|
| **For** | It reportedly already handles multi-file containers, so the routing problem would be someone else's. |
| **Against** | **(a)** Licence and provenance are unreviewed; the H5A provider and licensing review process exists precisely because this cannot be assumed. **(b)** Such a tool's value is concentrated in acquisition, which non-goal 1 forbids in the shipped dependency graph — adopting it wholesale imports exactly the capability D-005 excludes. **(c)** It would put WeChat schema knowledge somewhere product core depends on, which is what D-017 forbids. **(d)** R-003 already records the rule about not invoking or vendoring a stock `wechat-cli`; adopting a successor wholesale is the same route under a new name. **(e)** It drags an entire foreign runtime into the graph for a capability we need a thin slice of. |
| **Verdict** | **Rejected.** |

**`wx-cli-again` is a STUDY-only, clean-room behavioural reference.** It may be
read to understand *what behaviour a correct multi-part reader exhibits* —
that a container is partitioned, that a conversation spans partitions, that a
session table carries its own latest-message timestamp. It supplies
**questions**, not answers. **No code, no SQL text, no query shape copied
verbatim, no test fixture, no fixture data, no comment, and no identifier
naming scheme may be copied from it into this repository.** Everything in §6–§8
is expressed in this project's own vocabulary and derived from this project's
own `wechatdb` and `memory/` conventions. This mirrors how F-036's schema
knowledge was handled: public schema references were read as a source of schema
*facts*, and **no code was copied**.

### Alternative C — clean-room provider orchestration around our `wechatdb` — **RECOMMENDED**

Add one new isolated package that owns discovery, routing, identity and
coverage assembly, uses the existing `wechatdb` parser unchanged for the
per-table work, and emits a generic result envelope.

| | |
|---|---|
| **For** | **(a)** D-017 is satisfied by construction: all shard and schema vocabulary stays inside the provider package, and the generic envelope is source-neutral. **(b)** `wechatdb` keeps its single, provable responsibility. **(c)** No acquisition capability is introduced — the provider is *handed* its inputs by an injected locator and never searches a filesystem. **(d)** Coverage honesty becomes a **type invariant**, not a convention: a `ReaderCoverage` claiming completeness while holding a downgrade reason cannot be constructed. **(e)** The whole thing is testable without any real data, which is the only kind of testing currently available. **(f)** It produces exactly the artefact the promotion gate needs. |
| **Against** | New code that is not wired, and therefore carries a maintenance cost with no immediate product benefit. Some routing behaviour depends on a structural assumption about the container that only one real database has ever been examined against (§5.5). |
| **Mitigation for the second point** | The design is built so that the assumption being *false* degrades coverage rather than corrupting an answer: a part whose time bounds cannot be established is always visited and never permits an early stop. Correctness does not depend on the assumption; only efficiency does. |

**Recommendation: C.**

---

## 4. Architecture

### 4.1 Dependency direction — the load-bearing rule

```
  product core                generic contract              isolated provider
 ┌──────────────┐            ┌──────────────────┐          ┌──────────────────┐
 │ bridge/      │            │ bridge/          │          │ wechatprovider/  │
 │   MCP tools  │──imports──▶│   message_source │◀─imports─│   discovery      │
 │ memory/      │            │                  │          │   routing        │
 │ shadow/      │            │  MessageSource   │          │   identity       │
 │ ai/  core/   │            │  NormalizedMsg   │          │   coverage       │
 │ app          │            │  ReaderResult    │          │   provider       │
 └──────────────┘            │  ReaderCoverage  │          └────────┬─────────┘
        ▲                    │  CoverageAware-  │                   │ imports
        │                    │        Reader    │                   ▼
        └── NEVER imports ───┴──────────────────┘          ┌──────────────────┐
            wechatprovider or wechatdb                     │ wechatdb/        │
                                                           │   parser (as-is) │
                                                           └──────────────────┘
```

Three arrows, and the absent one is the point:

- **Provider → generic** is allowed and required. The provider must construct
  `NormalizedMessage` and `ReaderResult`.
- **Provider → `wechatdb`** is allowed and required.
- **Generic → provider** and **product core → provider** are **forbidden**,
  enforced by test, not by intention (§11, G2).

`wechatprovider` uses the repository's established flat cross-tree import style,
identical to `memory/memory_sync.py`'s `from message_source import ...`: it
requires `bridge/` on `sys.path`, supplied by the caller. No new import
mechanism is introduced.

### 4.2 New package layout

```
wechatprovider/
  __init__.py          exports; the package docstring states the boundary
  shards.py            ShardEntry, ShardDescriptor, ShardInventory,
                       ShardTimeBounds, ShardLocator, ShardOpener,
                       ExplicitPathLocator, ReadOnlySqliteOpener, ShardError
  discovery.py         ShardDiscovery
  routing.py           ShardRouter, RoutingStep, RoutingExclusion,
                       RoutingPlan, TraversalOutcome
  identity.py          IdentityResolver, ResolvedIdentity
  coverage.py          assemble_coverage  (provider → generic projection)
  provider.py          WeChatDatabaseProvider
  pytest.ini           testpaths = tests   (matching wechatdb/)
  tests/
    __init__.py
    fixtures.py        synthetic multi-part containers, built in code
    test_discovery.py
    test_routing.py
    test_identity.py
    test_coverage.py
    test_provider.py
    test_isolation.py
```

### 4.3 Generic additions

`bridge/message_source.py` gains the reader result envelope (§7). It gains
**nothing else**, and its existing guard
`test_the_protocol_depends_on_no_reader_technology` — which restricts its
imports to `{__future__, dataclasses, typing}` and forbids the identifiers
`rion`, `subprocess`, `sqlcipher`, `wechat`, `json`, `argv`, `zstd`, `sqlite` —
**must keep passing unmodified**. That test is the interface-leakage guard and
is not to be relaxed.

`MessageSource` itself is **not changed**. Adding coverage to its four methods
would change a published wire shape and a contract three live callers depend
on. The new `CoverageAwareReader` Protocol sits beside it.

---

## 5. Data flow

### 5.1 `read_recent(window, limit)`

```
1. locator.entries()
      → tuple[ShardEntry]              opaque name + opaque handle; no path in
                                       any provider-owned vocabulary

2. ShardDiscovery.catalogue()
      → ShardInventory                 every entry classified KNOWN or UNKNOWN
                                       by name shape alone. Nothing is opened.
                                       Nothing is dropped.

3. ShardDiscovery.probe(inventory)
      → ShardInventory                 each KNOWN message part is opened
                                       read-only and schema-checked:
                                         opens + recognised → READABLE + bounds
                                         otherwise          → UNAVAILABLE + token
                                       key set is identical to step 2's.

4. ShardRouter.plan(inventory, window=..., limit=...)
      → RoutingPlan                    every inventory key appears exactly once
                                       in steps ∪ excluded, each with a reason.

5. for step in plan.steps:                         (newest-first by max bound)
       connection = opener.open(entry)
       session_names, display_names = resolver.mappings(descriptor)
       for table in wechatdb.conversation_tables(connection):
           records = wechatdb.parse_conversation(
               connection, table,
               name2id=…, session_names=…, display_names=…)
           collect records inside `window`
       if collected > limit: break        # one over the limit — see below
   outcome = router.classify_stop(plan, visited=…, collected=…, …)
   truncated = collected > limit
   records = records[:limit]

   # `limit + 1` records are collected so that "there is more" is *measured*
   # rather than inferred from a full page. This is the same technique
   # `memory_retrieval.MemoryRetriever.search` already uses, for the same
   # reason: a traversal that returns exactly `limit` records and assumes
   # truncation reports a gap that may not exist, and one that assumes
   # completeness hides a gap that does.

6. records → NormalizedMessage         provider-internal projection (§6.6)

7. assemble_coverage(inventory, plan, outcome, window, truncated,
                     latest_read_at, source_latest_at)
      → ReaderCoverage

8. ReaderResult(messages=…, coverage=…, source=SOURCE_DATABASE,
                identity_resolution=resolver.state)
```

### 5.2 `read_conversation(conversation_id, window, limit)`

Identical, with two differences: step 4 additionally excludes any part that does
not contain the conversation's table (reason `conversation_absent`), and step 5
parses only that one table per part. The union across parts is what makes
routing necessary: the same conversation table name may exist in more than one
part of the container.

### 5.3 `wechatdb`'s scope, restated and unchanged

`wechatdb` remains: **one open connection, one table → provider records.** It
gains no knowledge of parts, of other files, of routing, of coverage, or of
windows. The provider calls the functions it already exports —
`conversation_tables`, `load_name2id`, `parse_conversation`,
`normalise_timestamp`, `MessageRecord`, `MessageSchemaError` — and injects the
`session_names` and `display_names` mappings that `parse_conversation` already
accepts. **The `IdentityResolver` exists to produce exactly those two mappings**,
which is why identity resolution requires no change to the parser at all.

`parse_database` (whole-connection) stays exported and stays tested; the
provider does not use it, because it needs per-table control for routing.

### 5.4 What the provider never does

It never writes. It never creates a file. It never checkpoints or truncates a
WAL. It never copies a database. It never opens anything the locator did not
hand it. It never constructs a path. It never takes a path from a client
request.

### 5.5 The one structural assumption, and why correctness does not rest on it

**Assumption (graded Hypothesis).** A multi-part WeChat container is roughly
time-partitioned: newer parts hold newer messages, so a newest-first traversal
can stop early once the remaining parts are provably older than the window.

**Evidence.** None from this project. E-022 opened one part and characterised
its interior; it established nothing about how parts relate to each other.
Treat any statement to the contrary as unsupported.

**Why the design is safe anyway.** Early stop is gated on *measured* bounds, not
on the assumption. A part whose `bounds_source` is `absent` can never be
excluded and can never be skipped past — it is always visited. If the
assumption is false in some container, the router simply never finds a safe
stop, visits everything, and the answer is still complete. The assumption buys
efficiency, never correctness.

---

## 6. Provider-internal interfaces (`wechatprovider/`)

Everything in this section is **provider vocabulary** and must never appear in a
generic module.

### 6.1 Parts — `wechatprovider/shards.py`

```python
# --- status: the four states a part can be in --------------------------------

SHARD_KNOWN: str = "known"              # catalogued by name; not opened
SHARD_READABLE: str = "readable"        # opened, schema recognised, bounds taken
SHARD_UNKNOWN: str = "unknown"          # listed, but cannot be characterised
SHARD_UNAVAILABLE: str = "unavailable"  # recognised, but cannot be read

SHARD_STATUSES: frozenset[str] = frozenset({
    SHARD_KNOWN, SHARD_READABLE, SHARD_UNKNOWN, SHARD_UNAVAILABLE,
})

# --- role --------------------------------------------------------------------

SHARD_ROLE_MESSAGE: str = "message"          # holds Msg_<32hex> tables
SHARD_ROLE_IDENTITY: str = "identity"        # holds contact / session / room data
SHARD_ROLE_UNRECOGNISED: str = "unrecognised"

# --- how a part's time bounds were established -------------------------------

BOUNDS_SCANNED: str = "scanned"    # MIN/MAX(create_time) over its own tables
BOUNDS_DECLARED: str = "declared"  # a metadata table in the part declared them
BOUNDS_ABSENT: str = "absent"      # not establishable; forces full traversal


class ShardError(Exception):
    """A part could not be characterised or read.

    ``state`` is a fixed lowercase token from :data:`SHARD_ERROR_STATES`.
    ``detail`` is a fixed sentence. Neither ever carries a path, a filename, a
    sender, message text, SQL, or output captured from anything else.
    """

    def __init__(self, state: str, detail: str) -> None: ...
    state: str
    detail: str


SHARD_ERROR_STATES: frozenset[str] = frozenset({
    "shard_absent",                # the handle no longer resolves
    "shard_open_refused",          # the opener could not open it read-only
    "shard_schema_unrecognised",   # opened, but not a shape this provider reads
    "shard_read_refused",          # opened and recognised, but a read failed
    "locator_unavailable",         # the locator itself cannot list
})


@dataclass(frozen=True)
class ShardEntry:
    """One thing the locator listed. ``handle`` is opaque to everything but the
    opener, which is what keeps filesystem vocabulary out of this package."""

    name: str
    handle: object


@dataclass(frozen=True)
class ShardTimeBounds:
    """When the messages in one part were created, in Unix **seconds**.

    Every value passes through :func:`wechatdb.normalise_timestamp` before it
    lands here. A part mixing second- and millisecond-valued creation times
    would otherwise report a maximum in the year 5138 and dominate every
    routing decision.
    """

    min_timestamp: int | None
    max_timestamp: int | None
    row_count: int | None
    bounds_source: str

    @property
    def is_established(self) -> bool:
        """True only when both ends are known and were not guessed."""

    def overlaps(self, start: float | None, end: float | None) -> bool:
        """Whether this part can contain a message in ``[start, end]``.

        Returns ``True`` whenever the bounds are not established: an unknown
        part may contain anything, and saying otherwise is the omission this
        design exists to prevent.
        """

    def strictly_older_than(self, moment: float) -> bool:
        """``max_timestamp < moment`` and the bounds are established.

        ``False`` for unestablished bounds, so an unknown part never satisfies
        an early-stop condition.
        """


@dataclass(frozen=True)
class ShardDescriptor:
    """One part of a source, and everything known about it.

    ``key`` is a stable opaque identifier derived from the entry name
    (blake2b, 48 bits, hex). It is **not** a path and **not** a filename: it is
    safe to log, safe to return, and stable across runs.
    """

    key: str
    role: str
    status: str
    ordinal: int | None          # the part's own sequence number, when declared
    bounds: ShardTimeBounds | None
    reason: str | None           # a SHARD_ERROR_STATES token when not readable
    #: Which conversations this part holds, as the table digests
    #: ``wechatdb.conversation_tables`` already yields. Captured during the
    #: probe that opened the part, so conversation routing needs no second
    #: open and no callback. ``None`` for any part that is not READABLE.
    #: Held in memory only; never emitted, logged or returned.
    conversation_digests: frozenset[str] | None

    def __post_init__(self) -> None:
        """Rejects: an unrecognised status or role; ``READABLE`` with a reason;
        ``UNKNOWN``/``UNAVAILABLE`` without one; bounds or conversation digests
        on a non-readable part."""


@dataclass(frozen=True)
class ShardInventory:
    """Every part the locator listed. Nothing is ever dropped from here."""

    shards: tuple[ShardDescriptor, ...]

    def __post_init__(self) -> None:
        """Rejects duplicate keys. Duplicate keys would silently collapse two
        parts into one, which is omission by another name."""

    def by_status(self, status: str) -> tuple[ShardDescriptor, ...]: ...
    def by_role(self, role: str) -> tuple[ShardDescriptor, ...]: ...
    def get(self, key: str) -> ShardDescriptor | None: ...

    @property
    def keys(self) -> frozenset[str]: ...

    def census(self, *, role: str = SHARD_ROLE_MESSAGE) -> ReaderSegmentCensus:
        """The generic projection consumed by coverage assembly. Counts only.

        Counts parts whose role is ``role`` — **plus every part whose role is**
        ``SHARD_ROLE_UNRECOGNISED``, which always counts toward ``unknown``,
        for every role. This is the whole point of the ``UNKNOWN`` state: if
        nobody can say what an entry is, nobody can say it is not a message
        part, and filtering it out by role would make it invisible to message
        coverage — silent omission produced by a filter.

        A part that *is* characterised, and belongs to a different role, is
        correctly excluded: an unreadable identity part does not make messages
        unobserved (§13 Q3).
        """


@runtime_checkable
class ShardLocator(Protocol):
    """Supplies the parts. Injected, never discovered.

    There is no default implementation that searches anything. The shipped
    implementation is :class:`ExplicitPathLocator`, which lists exactly the
    entries it was constructed with.
    """

    def entries(self) -> tuple[ShardEntry, ...]: ...


@runtime_checkable
class ShardOpener(Protocol):
    """Turns one entry into a read-only connection, or raises ShardError."""

    def open(self, entry: ShardEntry) -> sqlite3.Connection: ...


@dataclass(frozen=True)
class ExplicitPathLocator:
    """Lists exactly what it was given. No glob, no walk, no default root."""

    paths: tuple[str, ...]

    def entries(self) -> tuple[ShardEntry, ...]: ...


class ReadOnlySqliteOpener:
    """Opens ``file:<path>?mode=ro`` and nothing else.

    ``immutable=1`` is **forbidden here and asserted against by test**. It would
    make SQLite ignore the write-ahead log, silently dropping every
    not-yet-checkpointed message — a silent omission produced by an
    optimisation flag. When the log cannot be read, the correct outcome is
    ``shard_open_refused`` and an ``UNAVAILABLE`` part, which downgrades
    coverage where a caller can see it.
    """

    def open(self, entry: ShardEntry) -> sqlite3.Connection: ...
```

### 6.2 `ShardDiscovery` — `wechatprovider/discovery.py`

```python
class ShardDiscovery:
    """Finds the parts of a source and says what it could and could not learn.

    Two passes, deliberately separate, because they cost different amounts.
    ``catalogue`` reads names. ``probe`` opens files. Readiness reporting uses
    the first; reading uses both. Neither ever drops an entry.
    """

    def __init__(self, locator: ShardLocator, opener: ShardOpener) -> None: ...

    def catalogue(self) -> ShardInventory:
        """Name-shape classification only. Opens nothing.

        Every entry becomes exactly one descriptor:

        * name matches this provider's message-part shape  → ``KNOWN`` /
          ``SHARD_ROLE_MESSAGE``, with ``ordinal`` taken from the name
        * name matches this provider's identity-part shape → ``KNOWN`` /
          ``SHARD_ROLE_IDENTITY``
        * anything else → ``UNKNOWN`` / ``SHARD_ROLE_UNRECOGNISED``,
          ``reason='shard_schema_unrecognised'``

        Raises :class:`ShardError` ``locator_unavailable`` only when the
        locator itself cannot list. An empty listing is an empty inventory,
        which is an answer, not an error.
        """

    def probe(
        self,
        inventory: ShardInventory,
        *,
        roles: tuple[str, ...] = (SHARD_ROLE_MESSAGE,),
    ) -> ShardInventory:
        """Opens each ``KNOWN`` part in ``roles`` and re-classifies it.

        * opens, and at least one conversation table satisfies
          ``wechatdb.MANDATORY_COLUMNS`` (or the part is legitimately empty of
          conversation tables) → ``READABLE``, with bounds and with the part's
          conversation digests, both taken in that one open
        * opens, but no recognised shape → ``UNAVAILABLE``,
          ``shard_schema_unrecognised``
        * will not open → ``UNAVAILABLE``, ``shard_open_refused``
        * handle does not resolve → ``UNAVAILABLE``, ``shard_absent``
        * ``wechatdb.MessageSchemaError`` while establishing bounds →
          ``UNAVAILABLE``, ``shard_read_refused``

        A part outside ``roles`` is returned untouched, still ``KNOWN``.

        **Invariant, asserted:** the returned inventory's ``keys`` equal the
        input's. Probing may change what is known about a part; it may never
        change how many parts there are.
        """

    def bounds(self, connection: sqlite3.Connection) -> ShardTimeBounds:
        """``MIN``/``MAX`` creation time across the part's conversation tables,
        normalised to seconds. ``BOUNDS_ABSENT`` when the part has no
        conversation table, or when any table refuses the read."""
```

### 6.3 `ShardRouter` — `wechatprovider/routing.py`

```python
# --- why a part is in the plan ----------------------------------------------

ROUTE_WINDOW_OVERLAP: str = "window_overlap"
ROUTE_BOUNDS_UNESTABLISHED: str = "bounds_unestablished"
ROUTE_CONVERSATION_PRESENT: str = "conversation_present"

# --- why a part is not ------------------------------------------------------

EXCLUDE_WINDOW_DISJOINT: str = "window_disjoint"
EXCLUDE_NOT_READABLE: str = "not_readable"
EXCLUDE_ROLE_MISMATCH: str = "role_mismatch"
EXCLUDE_CONVERSATION_ABSENT: str = "conversation_absent"

# --- how a traversal ended --------------------------------------------------

STOP_EXHAUSTED: str = "exhausted"   # every planned part was visited
STOP_SAFE: str = "safe"             # stopped; the remainder provably cannot match
STOP_UNSAFE: str = "unsafe"         # stopped; the remainder is not accounted for


@dataclass(frozen=True)
class RoutingStep:
    shard_key: str
    order: int          # 0 first; newest-first by established max bound
    reason: str


@dataclass(frozen=True)
class RoutingExclusion:
    shard_key: str
    reason: str


@dataclass(frozen=True)
class RoutingPlan:
    steps: tuple[RoutingStep, ...]
    excluded: tuple[RoutingExclusion, ...]
    window: ReaderWindow
    limit: int

    def __post_init__(self) -> None:
        """Rejects a plan in which any key appears twice, or in both lists."""

    @property
    def accounted_keys(self) -> frozenset[str]: ...


@dataclass(frozen=True)
class TraversalOutcome:
    visited: tuple[str, ...]
    unvisited: tuple[str, ...]
    early_stop: str
    stop_reasons: tuple[str, ...]   # ShardDescriptor keys' reasons, deduplicated


class ShardRouter:
    """Decides which parts a windowed query must touch, and in what order.

    Ordering is newest-first by established ``max_timestamp``. A part whose
    bounds are not established sorts **first**, before every part with bounds:
    it must be visited, and visiting it first means a later early stop is
    evaluated against a remainder that is entirely bounded.
    """

    def plan(
        self,
        inventory: ShardInventory,
        *,
        window: ReaderWindow,
        limit: int,
        conversation_digest: str | None = None,
    ) -> RoutingPlan:
        """Every part accounted for, exactly once, with a reason.

        Included when all hold:

        1. ``status is SHARD_READABLE`` — anything else is excluded as
           ``not_readable`` and is separately reflected in coverage;
        2. ``role is SHARD_ROLE_MESSAGE`` — else ``role_mismatch``;
        3. ``bounds.overlaps(window.start, window.end)`` — unestablished bounds
           always overlap, so such a part is always included with reason
           ``bounds_unestablished``;
        4. when ``conversation_digest`` is given, it is in the part's
           ``conversation_digests`` — else ``conversation_absent``. The router
           opens nothing: the digests were captured by the probe that already
           opened the part.

        **Invariant, asserted:** ``plan.accounted_keys == inventory.keys``.
        This is the structural form of "no silent omission": a part cannot
        leave the inventory without a recorded reason.
        """

    def classify_stop(
        self,
        plan: RoutingPlan,
        *,
        visited: tuple[str, ...],
        inventory: ShardInventory,
        oldest_collected_at: float | None,
        collected: int,
    ) -> TraversalOutcome:
        """Whether stopping here leaves the answer explainable.

        ``STOP_EXHAUSTED`` when every planned part was visited. Otherwise the
        stop is ``STOP_SAFE`` only when **every** unvisited planned part
        satisfies ``bounds.strictly_older_than(boundary)``, where ``boundary``
        is ``oldest_collected_at`` if ``collected`` is non-zero and
        ``window.start`` otherwise. Any other stop is ``STOP_UNSAFE``.

        Because ``strictly_older_than`` is ``False`` for unestablished bounds,
        a part with unknown bounds can never be stepped over.

        A traversal only ends early because ``limit + 1`` records were
        collected — the router already excluded window-disjoint parts when it
        planned — so a stop is always accompanied by truncation. Safety and
        truncation nonetheless stay separate facts: truncation says the
        *returned* set was cut short, and safety says whether the *unvisited*
        remainder is accounted for. Only the second decides whether
        ``observed_through`` can be stated (§8.2 step 6): an unvisited part
        with unestablished bounds might hold something newer than anything
        collected, so after an unsafe stop the read cannot claim how far
        forward it looked.

        ``UNKNOWN`` and ``UNAVAILABLE`` parts do **not** make a stop unsafe:
        they were never visitable, so the traversal did not skip them. They are
        accounted for separately, and always, by coverage assembly (§8). The
        two mechanisms are kept apart so that neither can mask the other.
        """
```

### 6.4 `IdentityResolver` — `wechatprovider/identity.py`

```python
IDENTITY_SCOPE_SESSION: str = "session"
IDENTITY_SCOPE_GROUP_MEMBER: str = "group_member"
IDENTITY_SCOPE_SELF: str = "self"
IDENTITY_SCOPE_UNKNOWN: str = "unknown"

NAME_SOURCE_REMARK: str = "contact_remark"
NAME_SOURCE_CHATROOM_NICKNAME: str = "chatroom_member_nickname"
NAME_SOURCE_NICKNAME: str = "contact_nickname"
NAME_SOURCE_UNRESOLVED: str = "unresolved"


@dataclass(frozen=True)
class ResolvedIdentity:
    """One identifier, and the best name this provider can honestly give it."""

    identifier: str
    display_name: str | None
    name_source: str
    scope: str

    def __post_init__(self) -> None:
        """Rejects a ``display_name`` paired with ``NAME_SOURCE_UNRESOLVED``,
        and an absent name paired with any other source. The two always agree."""


class IdentityResolver:
    """Contact, session and group-member names, from the source's own tables.

    Resolution is a lookup, never an inference. There is no fuzzy match, no
    edit distance, no tokenisation, no model, and no heuristic — the same rule
    D-022 already fixed for conversation discovery, applied one layer down.

    **Precedence, deterministic:** a per-room member nickname beats a contact
    remark beats a contact nickname. Precedence is a fixed order over distinct
    *kinds* of name, so it is never a choice between two equally good answers.

    **Ambiguity is refused, never resolved.** When one identifier yields two
    different names from the *same* kind of source, the answer is
    ``display_name=None`` / ``NAME_SOURCE_UNRESOLVED``, and ``state`` degrades
    to ``IDENTITY_RESOLVED_PARTIAL``. Picking one would be inventing a fact.

    Identity resolution failure is **not** a coverage failure. A missing name
    does not make a message unobserved, and this class never touches coverage.
    Its ``state`` travels on its own field of the result envelope.
    """

    def __init__(self, inventory: ShardInventory, opener: ShardOpener) -> None: ...

    @property
    def state(self) -> str:
        """``IDENTITY_RESOLVED_COMPLETE`` when every identity part in the
        inventory was readable and no ambiguity was hit;
        ``IDENTITY_RESOLVED_PARTIAL`` when some names were refused as
        ambiguous, or some identity part was unreadable while another was
        readable; ``IDENTITY_SOURCE_UNAVAILABLE`` when no identity part was
        readable, **including when the locator listed none at all**."""

    def session_names(self) -> dict[str, str]:
        """``md5(username) -> username``, the mapping
        :func:`wechatdb.parse_conversation` already accepts as ``session_names``.

        The digest is computed with ``hashlib.md5(..., usedforsecurity=False)``
        and is used purely as a lookup index into table names. Empty when no
        identity part was readable — in which case the parser's own documented
        fallback applies and a conversation identifies itself by digest rather
        than by an invented name.
        """

    def display_names(
        self, *, session_identifier: str | None = None
    ) -> dict[str, str]:
        """``sender identifier -> display name``, the mapping
        :func:`wechatdb.parse_conversation` already accepts as ``display_names``.

        Scoped to one session when ``session_identifier`` is given, so a
        per-room nickname applies only in that room. Identifiers that resolve
        ambiguously are **absent from the mapping**, which makes the parser's
        existing ``sender_name`` fallback to ``sender_id`` the correct outcome
        with no parser change.
        """

    def resolve(
        self, identifier: str, *, session_identifier: str | None = None
    ) -> ResolvedIdentity:
        """One identifier, for callers that want the provenance of the name."""

    def mappings(
        self, descriptor: ShardDescriptor
    ) -> tuple[dict[str, str], dict[str, str]]:
        """``(session_names, display_names)`` for one message part, in the
        exact shape the parser takes. This is the whole integration surface
        between identity resolution and ``wechatdb``."""
```

### 6.5 `WeChatDatabaseProvider` — `wechatprovider/provider.py`

```python
class WeChatDatabaseProvider:
    """Orchestrates discovery, routing, parsing and identity into one envelope.

    The only class in this package that constructs a generic type, and
    therefore the only place the provider's vocabulary is translated away.

    Implements :class:`CoverageAwareReader`. It deliberately does **not**
    implement :class:`MessageSource`: that Protocol's list-returning methods
    cannot express a partial answer, and satisfying it would mean either
    dropping coverage or raising on every imperfect read. Which contract the
    bridge consumes is the promotion decision (§11), not this design.
    """

    name: str = SOURCE_DATABASE

    def __init__(
        self,
        *,
        discovery: ShardDiscovery,
        router: ShardRouter,
        opener: ShardOpener,
        resolver_factory: Callable[[ShardInventory], IdentityResolver],
    ) -> None: ...

    def status(self) -> SourceStatus:
        """Readiness from ``catalogue()`` alone. Opens nothing, counts nothing.

        ``conversation_count`` and ``message_count`` stay ``None``: counting
        would mean a scan, and the existing contract says an unknown count is
        reported as unknown rather than estimated.

        States: ``ready`` (at least one known message part, no unrecognised
        entry); ``ready_with_unrecognised_segments`` (ready, but the listing
        held entries this provider cannot characterise — reported rather than
        hidden, and ``ready`` is still ``True``); ``source_unconfigured`` (the
        locator listed nothing); ``source_unrecognised`` (entries exist, none
        is a known part). Never raises.
        """

    def list_conversations(self, limit: int) -> list[NormalizedConversation]:
        """Conversations across every readable message part, deduplicated by
        table digest, ``last_seen_at`` being the newest across parts.

        ``first_seen_at`` is ``None``, per the existing contract: a database
        read does not know when this Mac first saw a chat.
        """

    def read_conversation(
        self, conversation_id: int, *, window: ReaderWindow, limit: int
    ) -> ReaderResult:
        """One conversation across every part that holds it.

        Raises :class:`MessageSourceError` ``conversation_unknown`` for an id
        this provider has never listed — matching the existing adapter's rule
        that an unknown conversation is refused, never fabricated.
        """

    def read_recent(self, *, window: ReaderWindow, limit: int) -> ReaderResult:
        """Every conversation, newest first, within the window.

        Both read methods collect up to ``limit + 1`` matching records and
        return the first ``limit``, so ``ReaderCoverage.truncated`` is measured
        rather than inferred from a full page (§5.1).
        """
```

### 6.6 Record → message projection

Provider-internal, in `provider.py`. `MessageRecord` → `NormalizedMessage`:

| `NormalizedMessage` field | Value | Why |
|---|---|---|
| `id` | `conversation_identifier(session_id) ^ local_id`, masked to 48 bits | stable, positive, JSON-safe |
| `conversation_id` | `conversation_identifier(session_id)` | blake2b-48 of the session identifier |
| `sequence` | `record.local_id` | the source's own ordering within a conversation |
| `sender` | `record.sender_name` | already falls back to `sender_id`; `None` when neither resolves |
| `ownership` | `"own"` when the sender equals the resolved self identity, else `"other"` | when self identity is unresolved, **every** message is `"other"` and `identity_resolution` is not `COMPLETE` — never guessed |
| `visible_time` | `None` | a database read never saw a rendered time; inventing one would claim the user saw something |
| `text` | `record.content` | `None` for a purely binary payload, per the parser's documented rule |
| `kind` | `record.message_type` | the parser's kind vocabulary is already the bridge's |
| `confidence` | `1.0` | a decoded row is exact; there is no estimator on this path |
| `first_observed_at` | `float(record.timestamp)` | the contract already documents that a database source supplies the message's own creation time here |
| `source` | `SOURCE_DATABASE` | carried in the record, omitted from `payload()` |

`conversation_identifier` is defined in `wechatprovider/provider.py` with the
same construction the bridge adapter already uses — blake2b, 48 bits, big-endian
— and a test asserts the two agree for the same input, so two readers can never
disagree about what a conversation's id is.

---

## 7. Generic types (`bridge/message_source.py`)

Source-neutral. **No `Msg_*`, no `message_N.db`, no `Name2Id`, no
`real_sender_id`, no `local_type`, no "shard", no "WeChat", no SQL, no path.**

### 7.1 Coverage vocabulary — reused verbatim

```python
#: The source was read and the whole requested window was covered.
COVERAGE_COMPLETE: str = "observed_complete"

#: The source was read, but the window was not covered in full.
COVERAGE_PARTIAL: str = "observed_partial"

#: The source was asked and could not answer at all.
COVERAGE_UNAVAILABLE: str = "unavailable"

#: Nothing was observed for this window.
COVERAGE_NOT_OBSERVED: str = "not_observed"

READER_COVERAGE_STATES: frozenset[str] = frozenset({
    COVERAGE_COMPLETE, COVERAGE_PARTIAL,
    COVERAGE_UNAVAILABLE, COVERAGE_NOT_OBSERVED,
})
```

**Why the tokens are redeclared rather than imported — the reuse decision.**
The existing type is `memory_store.CoverageVerdict`, and it fits the *concept*
cleanly, but it cannot be imported here for two independent reasons:

1. **Dependency direction.** `memory/memory_sync.py` imports
   `message_source`. Importing `memory_store` from `message_source` would
   invert that and create a cycle between the two trees.
2. **The interface-leakage guard.** `test_the_protocol_depends_on_no_reader_technology`
   restricts this module's imports to `{__future__, dataclasses, typing}`. Any
   cross-tree import fails it. Relaxing that test to permit one import would
   remove the guard that keeps this module source-neutral, which is a much
   worse trade than four duplicated string constants.

Additionally, `CoverageVerdict` is shaped for a *store's* question (what has
ever been ingested) rather than a *read's* question (what this call covered):
it has no window, no truncation, no segment census, and no source-boundary
comparison.

**The minimal extension is therefore: reuse the vocabulary exactly, define a
reader-side type, and pin the two together by test.** G5 (§11) asserts
constant-by-constant equality with `memory_store`'s four tokens, so the two
layers can never drift. A future ingestor mapping `ReaderCoverage` onto a
`CoverageRecord` is then a field copy with no translation table.

### 7.2 Downgrade reasons

```python
REASON_SEGMENT_UNKNOWN: str = "segment_unknown"
REASON_SEGMENT_UNPROBED: str = "segment_unprobed"
REASON_SEGMENT_UNAVAILABLE: str = "segment_unavailable"
REASON_RESULT_LIMIT: str = "result_limit_reached"
REASON_UNEXPLAINED_REMAINDER: str = "traversal_stopped_with_unexplained_remainder"
REASON_SOURCE_AHEAD: str = "source_reports_newer_than_read"
REASON_SOURCE_BOUNDARY_UNKNOWN: str = "source_boundary_unknown"

READER_COVERAGE_REASONS: frozenset[str] = frozenset({
    REASON_SEGMENT_UNKNOWN,
    REASON_SEGMENT_UNPROBED,
    REASON_SEGMENT_UNAVAILABLE,
    REASON_RESULT_LIMIT,
    REASON_UNEXPLAINED_REMAINDER,
    REASON_SOURCE_AHEAD,
    REASON_SOURCE_BOUNDARY_UNKNOWN,
})
```

Seven fixed tokens. A reason outside the set is rejected at construction. No
reason ever carries a count, a name, a path, or free text.

### 7.3 Window, census, staleness

```python
@dataclass(frozen=True)
class ReaderWindow:
    """The requested time bounds, in Unix seconds. ``None`` means unbounded.

    Always carried on the result, so completeness is never unqualified:
    ``observed_complete`` means complete **for this window**, never complete
    for all of history.
    """

    start: float | None = None
    end: float | None = None

    def __post_init__(self) -> None:
        """Rejects ``start > end``. An inverted window is a caller defect, not
        an empty result."""

    @property
    def is_open_ended(self) -> bool:
        return self.end is None


@dataclass(frozen=True)
class ReaderSegmentCensus:
    """How many parts of a composite source were in each state.

    Source-neutral: a "segment" is any independently readable part a source is
    composed of. A source that is not composite reports ``None`` instead of a
    census, which is a different claim from a census of zeroes.
    """

    known: int          # catalogued, not opened for this read
    readable: int       # opened and understood
    unknown: int        # present, not characterisable
    unavailable: int    # understood, not readable

    def __post_init__(self) -> None:
        """Rejects a negative count."""

    @property
    def total(self) -> int: ...

    @property
    def fully_accounted(self) -> bool:
        """True only when every part was opened and understood."""
        return self.total > 0 and self.readable == self.total


#: The read reached the newest thing the source claims to have.
STALENESS_AT_SOURCE_BOUNDARY: str = "read_reached_source_boundary"

#: The source's own records name something newer than the newest message read.
STALENESS_SOURCE_AHEAD: str = "source_reports_newer_than_read"

#: One of the two values needed for the comparison is absent.
STALENESS_NOT_COMPARABLE: str = "source_boundary_unknown"

READER_STALENESS_STATES: frozenset[str] = frozenset({
    STALENESS_AT_SOURCE_BOUNDARY, STALENESS_SOURCE_AHEAD,
    STALENESS_NOT_COMPARABLE,
})
```

Two token strings appear in both §7.2 and §7.3 —
`source_reports_newer_than_read` and `source_boundary_unknown`. That is
deliberate: the same fact is named the same way wherever it appears, so a
reader does not have to learn two words for it. The two *sets* stay separate
and are validated separately, because a staleness state is not a downgrade
reason: §8.2 step 4 shows the case where the staleness state is
`SOURCE_AHEAD` and the reason is correctly absent.

### 7.4 `ReaderCoverage`

```python
@dataclass(frozen=True)
class ReaderCoverage:
    """What one read is entitled to claim about the window it was asked for.

    Coverage, truncation and source-boundary comparison are three different
    facts and are kept on three different fields, the same discipline the
    memory layer already applies to coverage, freshness and latest-message.
    Collapsing them into one verdict is what produces "it said complete and it
    wasn't".

    There is no ``is_fresh`` and no staleness threshold. ``staleness`` is the
    literal outcome of comparing two timestamps the source itself supplied;
    whether the gap matters is a product or agent decision made with both
    numbers in view, not a verdict this layer hands down.
    """

    status: str
    window: ReaderWindow
    reasons: tuple[str, ...] = ()
    truncated: bool = False
    observed_through: float | None = None
    complete_through: float | None = None
    staleness: str = STALENESS_NOT_COMPARABLE
    source_latest_at: float | None = None
    latest_read_at: float | None = None
    segments: ReaderSegmentCensus | None = None

    def __post_init__(self) -> None:
        """Enforces the coverage invariants (§8.3). Raises ReaderContractError."""

    @property
    def is_complete(self) -> bool:
        return self.status == COVERAGE_COMPLETE

    @property
    def source_ahead_of_read(self) -> bool:
        """The source names something newer than the newest message read.

        Not a staleness verdict and not a threshold: it is exactly
        ``staleness == STALENESS_SOURCE_AHEAD``, which is exactly
        ``source_latest_at > latest_read_at`` on two values the source gave.
        Whether that matters depends on the window, and §8.2 says how.
        """
        return self.staleness == STALENESS_SOURCE_AHEAD

    def payload(self) -> dict[str, Any]:
        """Tokens, counts and timestamps only. Never a name, path or text."""


class ReaderContractError(Exception):
    """A result was assembled that claims more than its evidence supports.

    Raised at construction, never caught to produce a softer answer. This is a
    programming defect in a provider, not a runtime condition a caller handles.
    """
```

### 7.5 `ReaderResult` and `CoverageAwareReader`

```python
IDENTITY_RESOLVED_COMPLETE: str = "resolved_complete"
IDENTITY_RESOLVED_PARTIAL: str = "resolved_partial"
IDENTITY_SOURCE_UNAVAILABLE: str = "identity_source_unavailable"

READER_IDENTITY_STATES: frozenset[str] = frozenset({
    IDENTITY_RESOLVED_COMPLETE, IDENTITY_RESOLVED_PARTIAL,
    IDENTITY_SOURCE_UNAVAILABLE,
})


@dataclass(frozen=True)
class ReaderResult:
    """Messages plus what the reader is entitled to claim about them.

    An empty ``messages`` is not an answer on its own: it means "nothing
    matched", which equals "nothing exists" only when ``coverage`` says the
    window was actually covered. ``trustworthy_empty`` is the one property a
    caller must read before writing "no messages".

    ``identity_resolution`` is deliberately **not** part of coverage. An
    unresolvable display name does not make a message unobserved; it makes it
    unnamed. Keeping the two apart stops a missing contact table from
    reporting the messages as incomplete, and stops a complete message read
    from implying every name in it is real.
    """

    messages: tuple[NormalizedMessage, ...]
    coverage: ReaderCoverage
    source: str
    identity_resolution: str = IDENTITY_RESOLVED_COMPLETE

    def __post_init__(self) -> None:
        """Rejects an unknown ``source`` or ``identity_resolution``; rejects
        messages carried alongside ``unavailable`` or ``not_observed``
        coverage; rejects a message whose ``source`` differs from the
        envelope's."""

    @property
    def trustworthy_empty(self) -> bool:
        return not self.messages and self.coverage.status == COVERAGE_COMPLETE

    def payload(self) -> dict[str, Any]:
        """``{"messages": [...], "coverage": {...}, "source": ...,
        "identity_resolution": ...}``.

        Each message uses the unchanged ``NormalizedMessage.payload()``, so the
        per-message wire shape is byte-identical to the one the bridge has
        always returned and per-message provenance stays inside the process.
        """


@runtime_checkable
class CoverageAwareReader(Protocol):
    """A source that states its own coverage. ``MessageSource`` is unchanged
    and this Protocol does not replace it; nothing consumes this Protocol
    today. Which of the two the bridge consumes is the promotion decision."""

    name: str

    def status(self) -> SourceStatus: ...
    def list_conversations(self, limit: int) -> list[NormalizedConversation]: ...
    def read_conversation(
        self, conversation_id: int, *, window: ReaderWindow, limit: int
    ) -> ReaderResult: ...
    def read_recent(
        self, *, window: ReaderWindow, limit: int
    ) -> ReaderResult: ...
```

---

## 8. Coverage semantics

### 8.1 The five distinctions, and where each one lives

| Required distinction | Expressed by |
|---|---|
| **complete for the window** | `status == COVERAGE_COMPLETE` **and** `window` carried beside it, so the claim is always qualified |
| **partial / unknown** | `status == COVERAGE_PARTIAL` with at least one reason; `segment_unknown` / `segment_unprobed` name the unknown case specifically; `status == COVERAGE_NOT_OBSERVED` is the "nothing was looked at" case |
| **potentially stale** | `staleness == STALENESS_SOURCE_AHEAD`, with both compared values carried (`source_latest_at`, `latest_read_at`); when the newer message falls inside the requested window it also adds `source_reports_newer_than_read` and downgrades `status` |
| **windowed / truncated** | `window` (the requested scope, always present) and `truncated` + `result_limit_reached` (the answer was cut short) — two different facts on two different fields |
| **source unavailable** | `status == COVERAGE_UNAVAILABLE`, carrying whichever of `segment_unavailable` / `segment_unknown` / `segment_unprobed` says *why* nothing could answer; a **partly** unavailable source is `COVERAGE_PARTIAL` with the same reasons and a census that says how many |

No boolean named `fresh` exists anywhere. No threshold, no clock comparison, no
age constant, and no "recent enough" check appears in any of these rules — G4
(§11) asserts the assembled module names no threshold.

### 8.2 Assembly rules

`wechatprovider/coverage.py`:

```python
def assemble_coverage(
    *,
    inventory: ShardInventory,
    plan: RoutingPlan,
    outcome: TraversalOutcome,
    window: ReaderWindow,
    truncated: bool,
    latest_read_at: float | None,
    source_latest_at: float | None,
) -> ReaderCoverage:
```

Applied in order.

**Step 0 — cross-check.** Assert `plan.accounted_keys == inventory.keys`. The
router already guarantees this; re-checking it at the one place coverage is
decided means a future router change cannot quietly drop a part on the way to
a verdict. A mismatch is a `ReaderContractError`, not a downgrade.

**Step 1 — census.** `census = inventory.census(role=SHARD_ROLE_MESSAGE)`.

**Step 2 — the degenerate statuses.** Both return immediately, with
`messages == ()`, `observed_through is None` and `complete_through is None`.

- `census.total == 0` → `COVERAGE_NOT_OBSERVED`, reasons `()`. Nothing was
  listed, so nothing was looked at, and there is nothing to explain.
- `census.total > 0` and `census.readable == 0` → `COVERAGE_UNAVAILABLE`, with
  every applicable member of `{segment_unavailable, segment_unknown,
  segment_unprobed}` as a reason. Parts exist and not one of them could
  answer; at least one of the three always applies, because a part that is
  neither readable nor unavailable nor unknown nor unprobed does not exist.

**Step 3 — staleness, computed before any downgrade.**

- both of `source_latest_at` and `latest_read_at` present and
  `source_latest_at > latest_read_at` → `STALENESS_SOURCE_AHEAD`
- both present and `source_latest_at <= latest_read_at` →
  `STALENESS_AT_SOURCE_BOUNDARY`
- either absent → `STALENESS_NOT_COMPARABLE`

The comparison is exact. There is no tolerance, no rounding beyond the
second-normalisation every timestamp already receives, and no threshold.

**Step 4 — collect downgrade reasons.** Start empty; add each that applies:

| Condition | Reason |
|---|---|
| `census.unknown > 0` | `segment_unknown` |
| `census.known > 0` | `segment_unprobed` |
| `census.unavailable > 0` | `segment_unavailable` |
| `truncated` | `result_limit_reached` |
| `outcome.early_stop == STOP_UNSAFE` | `traversal_stopped_with_unexplained_remainder` |
| `staleness == STALENESS_SOURCE_AHEAD` **and** (`window.is_open_ended` or `source_latest_at <= window.end`) | `source_reports_newer_than_read` |
| `staleness == STALENESS_NOT_COMPARABLE` **and** `window.is_open_ended` | `source_boundary_unknown` |

Two of these deserve their justification stated, because the tempting simpler
rule is wrong in each case:

- **Source-ahead does not always downgrade.** If the source's newest message
  is *later than the requested window's end*, then it is outside what was
  asked for, and the read can still be complete for that window. `staleness`
  still reports `SOURCE_AHEAD` — it is a true fact about the source — but no
  reason is added and `status` is untouched. Downgrading here would make every
  bounded historical query permanently partial, which is both false and
  useless.
- **Boundary-unknown only matters for an open-ended window.** If the caller
  asked for `[T0, T1]` and the traversal covered `[T0, T1]`, not knowing what
  the source holds after `T1` is irrelevant to the claim being made.

**Step 5 — status.** `COVERAGE_COMPLETE` if no reasons were collected,
otherwise `COVERAGE_PARTIAL`.

**Step 6 — boundaries.**

- `observed_through` = `window.end` when the window is bounded and
  `outcome.early_stop in (STOP_EXHAUSTED, STOP_SAFE)`; otherwise
  `latest_read_at` when `outcome.early_stop in (STOP_EXHAUSTED, STOP_SAFE)`;
  otherwise `None`. An unsafe stop knows nothing about how far it looked.
- `complete_through` = `observed_through` when `status == COVERAGE_COMPLETE`,
  else `None`. A partial read cannot honestly name a sub-window it covered
  completely, because the gap could be anywhere in it.

These match the memory layer's definitions: *the latest moment the source is
known to have been looked at*, and *the latest moment through which coverage
was complete*.

### 8.3 Invariants — enforced at construction

`ReaderCoverage.__post_init__` raises `ReaderContractError` unless all hold:

1. `status in READER_COVERAGE_STATES`.
2. `staleness in READER_STALENESS_STATES`.
3. every reason is in `READER_COVERAGE_REASONS`; reasons are deduplicated and
   lexicographically sorted, so two coverages describing the same situation
   compare equal.
4. `status == COVERAGE_COMPLETE` ⟹ `reasons == ()` **and** `truncated is False`
   **and** (`segments is None` or `segments.fully_accounted`).
5. `status == COVERAGE_PARTIAL` ⟹ `reasons != ()`.
6. `status == COVERAGE_UNAVAILABLE` ⟹ at least one of
   `REASON_SEGMENT_UNAVAILABLE`, `REASON_SEGMENT_UNKNOWN`,
   `REASON_SEGMENT_UNPROBED` is in `reasons`. "Could not answer" must always
   say which kind of could-not.
7. `status == COVERAGE_NOT_OBSERVED` ⟹ `reasons == ()` and
   `complete_through is None` and `observed_through is None`.
8. `complete_through is not None` ⟹ `status == COVERAGE_COMPLETE`.
9. `truncated is True` ⟹ `REASON_RESULT_LIMIT in reasons`.
10. `staleness == STALENESS_NOT_COMPARABLE` ⟺ `source_latest_at is None or
    latest_read_at is None`.
11. `REASON_SOURCE_AHEAD in reasons` ⟹ `staleness == STALENESS_SOURCE_AHEAD`.

Invariant 4 is the structural form of requirement 7: an inventory holding an
unknown or unavailable part produces a census that is not `fully_accounted`,
which makes `COVERAGE_COMPLETE` **unconstructable**. The downgrade is not a rule
someone remembers to apply; it is a rule that must be applied for the object to
exist.

---

## 9. What later reuses this envelope

Stated so the shape is not re-litigated, and so nothing here is built now.

- **History and timeline.** A timeline read is a windowed read with a different
  ordering. It returns `ReaderResult` unchanged; `window` already carries the
  span and `coverage` already says whether the span is fully accounted for.
- **Search.** A future search returns the same envelope with a narrowed
  `messages` tuple. Coverage still describes the *window scanned*, not the
  number of hits: "I searched all of March and found nothing" and "I searched
  part of March and found nothing" are different answers, and
  `trustworthy_empty` is already the property that separates them.
- **FTS.** Not designed, not specified, not implemented here. If an index is
  ever added, it is a provider-internal accelerator and must produce a
  `ReaderCoverage` no stronger than an unindexed read of the same window would.
- **Cache.** Not designed, not specified, not implemented here. A cached answer
  would need its own `observed_through`, and inventing one is exactly the class
  of error this envelope exists to prevent.

No code, type, field or flag for search, FTS, timeline or caching appears in
§6 or §7.

---

## 10. Errors, privacy and security

### 10.1 Error model

Every token is fixed, lowercase, and content-free. Nothing in this design ever
returns a path, a filename, a chat title, a sender, message text, SQL, or output
captured from another program — the rule `MessageSourceError` already states.

| Token | Raised by | Effect | Raises or reports |
|---|---|---|---|
| `locator_unavailable` | `ShardDiscovery.catalogue` | `status()` reports `source_unconfigured` | raises `ShardError`; the provider catches it in `status()` |
| `shard_absent` | `ShardDiscovery.probe` | that part → `UNAVAILABLE` | caught; becomes coverage |
| `shard_open_refused` | `ReadOnlySqliteOpener.open` | that part → `UNAVAILABLE` | caught; becomes coverage |
| `shard_schema_unrecognised` | `catalogue` / `probe` | `UNKNOWN` (catalogue) or `UNAVAILABLE` (probe) | caught; becomes coverage |
| `shard_read_refused` | `probe` / traversal | that part → `UNAVAILABLE` | caught; becomes coverage |
| `conversation_unknown` | `read_conversation` | refusal | raises `MessageSourceError` |
| `source_unconfigured` | `status` | reported readiness | reported, never raised |
| `source_unrecognised` | `status` | reported readiness | reported, never raised |
| `ready_with_unrecognised_segments` | `status` | reported readiness, `ready=True` | reported, never raised |
| — | `ReaderCoverage` / `ReaderResult` | `ReaderContractError` | raises; never caught to soften an answer |

**The governing rule:** a per-part failure is never fatal and is never silent.
It becomes a coverage downgrade with a reason, every time. A failure that would
make the whole answer meaningless — an unknown conversation, a contract
violation — raises. There is **no fallback to another source**, at any level.

### 10.2 Privacy and security

1. **No acquisition surface.** The provider derives, requests, reconstructs and
   stores no key, salt, passphrase or cipher parameter. It contains no
   SQLCipher call, no `PRAGMA key`, no decryption, no process-memory read, no
   debugger interaction, no code-signature operation. G3 asserts this by source
   scan over the whole package.
2. **No location knowledge.** The provider contains no WeChat container path,
   bundle identifier, directory name or search root. Every input arrives
   through an injected `ShardLocator`, and the shipped locator lists exactly
   what it was constructed with. The provider cannot find a WeChat database; it
   can only be handed one.
3. **Read-only, and no silent optimisation.** `mode=ro` only. `immutable=1` is
   forbidden by test because it would silently drop write-ahead-log-resident
   messages — the interface gate already recorded a WAL-resident row as a real
   shape, so this is a known live case, not a hypothetical.
4. **Nothing is written.** No file created, no WAL checkpointed, no temporary
   copy, no plaintext cache, no export. The source is left byte-identical.
5. **Identifiers, not names, in diagnostics.** `ShardDescriptor.key` is a
   blake2b digest of an entry name, never the name. No coverage token, error
   token or census field can carry a name.
6. **No real data anywhere.** Every fixture is synthetic and built in code, in
   the style of `wechatdb/tests/fixtures.py`. No real chat content, no real
   `wxid`, no real path, no real digest is committed.
7. **D-002, D-005, D-011 and R-003 are untouched.** Nothing here drives,
   activates, scrolls or writes to WeChat; nothing runs unattended; nothing
   invokes or vendors a `wechat-cli`.
8. **Nothing ships.** The package is not imported by product core, is not in
   any build phase, and is not in any packaged runtime. It is not in the
   shipped dependency graph at all.

---

## 11. Promotion gates

**Parser success is not sufficient, and neither is passing every test below.**
The gates are a precondition for *considering* promotion, not a grant of it.

### Implementation gates — required before any wiring

- **G1 — Behaviour.** All ten synthetic fixture classes of §12 pass. Each test
  is verified to fail when the behaviour it pins is reverted; a coverage test
  that passes against a provider that always reports `observed_complete` is
  worthless.
- **G2 — Isolation.**
  `bridge/tests/test_reader_boundary.py::test_no_product_module_imports_the_candidate_schema_provider`
  is generalised from one `CANDIDATE_PROVIDER` string to
  `CANDIDATE_PROVIDERS = ("wechatdb", "wechatprovider")` and stays green across
  `bridge`, `memory`, `shadow`, `ai`, `core`, `app.py`, `mcp_server.py`.
- **G3 — Vocabulary.** Both scans parse with `ast` and inspect **identifiers
  and non-docstring string constants**, never raw file text — the technique
  `test_the_protocol_depends_on_no_reader_technology` already establishes, so
  that prose describing what a module deliberately avoids is not mistaken for
  a dependency on it. Scanning raw text would fail on these very docstrings.
  - `bridge/message_source.py` contains none of `Msg_`, `message_0`,
    `Name2Id`, `real_sender_id`, `local_type`, `shard`, and
    `test_the_protocol_depends_on_no_reader_technology` still passes
    **unmodified** — that existing test is what continues to exclude
    transport, codec and vendor vocabulary.
  - `wechatprovider/` contains none of `Containers`, `xwechat_files`,
    `db_storage`, `com.tencent`, `/Users/`, `PRAGMA key`, `enc_key`, `salt`,
    `sqlcipher`, `task_for_pid`, `lldb`, `codesign`, `sudo`, `shell=True`,
    `immutable`.
- **G4 — Invariants.** Every invariant in §8.3 has a test constructing the
  violating object and asserting `ReaderContractError`. An `ast` scan over
  identifiers in `wechatprovider/coverage.py` and `bridge/message_source.py`
  asserts none matches `fresh`, `stale_after`, `max_age` or `threshold`, and
  that `coverage.py` binds no numeric constant other than `0`.
- **G5 — Vocabulary parity.** A test imports both `message_source` and
  `memory_store` and asserts the four coverage tokens are equal
  constant-by-constant, so the reader and the store can never drift apart.
- **G6 — No silent omission.** A property-style test over generated
  inventories asserts `plan.accounted_keys == inventory.keys` for every
  inventory the router is given, and that `probe` preserves the key set.
- **G7 — Identity honesty.** A test asserts that an ambiguous identifier
  produces `NAME_SOURCE_UNRESOLVED` with `display_name is None`, that the name
  never appears in the result, and that `identity_resolution` degrades while
  `coverage.status` does not.

### Decision gates — required before promotion is even proposed

- **P1 — An explicit operator decision** recorded in `Decisions.md`, amending
  or extending D-017, that names `wechatprovider` as a promotable provider and
  states what it is promoted *to*. An import statement is not a promotion
  decision.
- **P2 — A real multi-part container**, read under a fresh explicit decision and
  per-occasion consent. **This is not satisfiable today:** D-030 lapsed, no
  access material is retained, and obtaining more is not an ordinary next
  action. §5.5's assumption is unverified and stays unverified until P2.
- **P3 — The licensing review** D-017 still records as owed, covering whatever
  arrangement promotion would create.
- **P4 — A contract decision:** whether the bridge consumes `MessageSource` or
  `CoverageAwareReader`, and what the four MCP tools do with a partial answer.
  A tool that drops `coverage` on the floor would undo this entire design.
- **P5 — Default unchanged.** Promotion does not alter
  `selected_source_name()`'s `visual` default. Visual capture / OCR remains the
  production path, and any database source stays explicitly selected,
  off by default, and fail-closed when unselected.

**Until every gate above is met, `wechatprovider` is what `wechatdb` is today:
an isolated candidate that nothing imports.**

---

## 12. Testing

All synthetic. Fixtures are built in code by `wechatprovider/tests/fixtures.py`:
in-memory or `tmp_path` SQLite databases holding `Msg_<32hex>` tables whose
column layout matches what `wechatdb` reads, populated with invented text,
invented `wxid_fixture_*` identifiers, and chosen timestamps. No real data,
ever.

| # | Fixture | Construction | Asserted |
|---|---|---|---|
| **F-1** | **All parts readable** | 3 message parts, disjoint time ranges, all open, all recognised; 1 identity part | `census(known=0, readable=3, unknown=0, unavailable=0)`; `status == observed_complete`; `reasons == ()`; `truncated is False`; `complete_through == observed_through`; every expected message present exactly once |
| **F-2** | **Unknown part** | F-1 plus a fourth listed entry whose name matches nothing | catalogue marks it `UNKNOWN`; it appears in `plan.excluded` with `not_readable`; `census.unknown == 1`; `status == observed_partial`; `segment_unknown in reasons`; **messages identical to F-1** — the unknown part changes the claim, not the content; constructing `observed_complete` with this census raises `ReaderContractError` |
| **F-3** | **Unavailable part** | F-1 with one part's opener raising `shard_open_refused`; and a second variant where it opens but has an unrecognised schema | `UNAVAILABLE` with the right token in each variant; `census.unavailable == 1`; `status == observed_partial`; `segment_unavailable in reasons`; messages from the other two parts are still returned — a failure is not fatal; a third variant where **every** part refuses gives `status == unavailable` and `messages == ()` |
| **F-4** | **Query spanning parts** | one conversation whose table exists in all 3 parts, 4 messages per part, window covering all 12 | all 3 parts planned with `conversation_present`; 12 messages, correctly ordered across the part boundary; `status == observed_complete`. Variant: a fourth part exists but lacks that table → `plan.excluded` names it `conversation_absent`, and coverage is **still complete** — an excluded-for-cause part is accounted for, not omitted |
| **F-5** | **Safe early stop** | 3 parts with established, disjoint, descending ranges; `limit + 1` records collected inside the newest part; every unvisited part strictly older than the oldest collected message | `outcome.early_stop == STOP_SAFE`; `unvisited` non-empty; `truncated is True` and `result_limit_reached in reasons`; `traversal_stopped_with_unexplained_remainder` **not** in reasons; `observed_through` is stated. Status is partial for the *limit* and for nothing else — a control variant with `limit` raised above the total returns `STOP_EXHAUSTED`, `truncated is False` and `observed_complete`, proving the partial verdict came from truncation and not from stopping |
| **F-6** | **Unsafe early stop** | same as F-5 but one unvisited part has `bounds_source == BOUNDS_ABSENT`; and a second variant where an unvisited part's `max_timestamp` is inside the window | `STOP_UNSAFE` in both; reasons contain **both** `result_limit_reached` and `traversal_stopped_with_unexplained_remainder`; `status == observed_partial`; `observed_through is None` — an unsafe stop may not claim how far forward it looked. A third variant proves the router **orders** an unestablished-bounds part first, so with a large enough `limit` it is visited rather than skipped and the stop becomes safe |
| **F-7** | **Source timestamp mismatch** | identity part declares a session latest timestamp `T_src`; the newest readable message is at `T_read < T_src`. **(a)** open-ended window; **(b)** `window.end` between `T_read` and `T_src`; **(c)** `window.end < T_read`, i.e. `T_src` outside the window | (a) and (b): `staleness == STALENESS_SOURCE_AHEAD`, `source_reports_newer_than_read in reasons`, `status == observed_partial`, both timestamps carried. (c): `staleness == STALENESS_SOURCE_AHEAD` **and** `status == observed_complete` — the newer message is outside what was asked for. (d) `T_src` absent, open-ended window: `STALENESS_NOT_COMPARABLE` and `source_boundary_unknown in reasons`. (e) `T_src` absent, bounded window: `NOT_COMPARABLE`, no reason, `observed_complete` |
| **F-8** | **Identity resolution** | identity part with: a contact with a remark and a nickname; a room member with a room-specific nickname; an identifier with two conflicting same-kind names; an identifier absent entirely | remark beats nickname; room nickname beats remark inside that room and not outside it; the conflicting identifier is **absent from `display_names`**, so the parser falls back to `sender_id` unchanged, `resolve()` returns `NAME_SOURCE_UNRESOLVED` / `display_name is None`, and neither candidate name appears anywhere in the result; `identity_resolution == resolved_partial` while `coverage.status` is unaffected. Variant with no readable identity part: `identity_source_unavailable`, `session_names() == {}`, conversations identify by digest per the parser's documented fallback, every `ownership == "other"`, and **coverage is still complete** |
| **F-9** | **Zero messages, complete vs incomplete** | **(a)** all parts readable, window genuinely empty. **(b)** identical window, one part unavailable | (a) `messages == ()`, `status == observed_complete`, `trustworthy_empty is True`. (b) `messages == ()`, `status == observed_partial`, `segment_unavailable in reasons`, `trustworthy_empty is False`. The two results carry an **identical `messages` tuple and an identical `window`**, and differ only in `coverage` — which is exactly the case where a caller reading `messages` alone would write "no messages" and be wrong. The single most important test in the suite |
| **F-10** | **Architecture guard** | AST scan, not text matching, so prose describing what a module avoids cannot be mistaken for a dependency on it | no module under `bridge/`, `memory/`, `shadow/`, `ai/`, `core/`, nor `app.py` / `mcp_server.py`, imports `wechatdb` or `wechatprovider` at any depth; `bridge/message_source.py`'s import set is still `⊆ {__future__, dataclasses, typing}`; the forbidden-vocabulary scans of G3 pass; `wechatprovider` imports no module from `memory/`, `shadow/`, `ai/`, or `core/` |

Two supporting tests that are not fixture classes but are required by G1:

- **Timestamp normalisation in routing.** A part whose `create_time` column
  mixes seconds and milliseconds yields bounds in seconds. Without this, one
  millisecond row gives a `max_timestamp` in the year 5138 and that part is
  routed first for every query, forever.
- **Conversation-id agreement.** `wechatprovider.conversation_identifier` and
  `rion_reader_adapter.conversation_identifier` return the same value for the
  same input. This test lives under `wechatprovider/tests/` — never under
  `bridge/`, which G2 scans.

---

## 13. Resolved questions

Every question this design raised, and its answer. None is left open.

1. **Should `ReaderCoverage` reuse `memory_store.CoverageVerdict`?**
   No — it reuses the *vocabulary* verbatim and defines a reader-side type.
   Importing it would invert the dependency direction (`memory_sync` already
   imports `message_source`) and would break the guard that keeps
   `message_source.py` standard-library-only. §7.1; pinned by G5.

2. **Should `MessageSource` gain coverage?**
   No. Three live callers depend on its four-method shape and its published
   wire payload. `CoverageAwareReader` is added beside it and consumed by
   nothing today.

3. **Does an unavailable identity part downgrade message coverage?**
   No. Identity and coverage are different facts;
   `ReaderResult.identity_resolution` carries the former. Merging them would
   make a missing contact table report the messages as incomplete, which is
   false. F-8 pins it.

4. **Do unknown or unavailable parts make an early stop unsafe?**
   No — they were never visitable, so the traversal did not skip them. They
   downgrade coverage independently and always. Keeping the two mechanisms
   separate means neither can mask the other. §6.3, F-2/F-3/F-6.

5. **Does "source reports newer" always downgrade?**
   No. Only when the newer message falls inside the requested window, or the
   window is open-ended. Otherwise `staleness` reports the fact and `status`
   stays complete. Downgrading unconditionally would make every bounded
   historical query permanently partial. §8.2 step 4, F-7(c).

6. **Where does the "known but unprobed" state show up?**
   As `segment_unprobed`. `status()` catalogues without probing, so `known`
   parts are normal there; a *read* probes every message part first, so a
   remaining `known` part at read time means a probe was refused, and that is a
   downgrade.

7. **How does identity reach the parser without changing it?**
   `IdentityResolver.mappings()` returns exactly the `(session_names,
   display_names)` pair `wechatdb.parse_conversation` already accepts. That is
   the entire integration surface, and `wechatdb` is not edited. §5.3.

8. **What happens when identity is ambiguous?**
   Nothing is picked. The identifier is omitted from `display_names`, the
   parser's existing `sender_name → sender_id` fallback applies, `resolve()`
   reports `NAME_SOURCE_UNRESOLVED`, and `identity_resolution` degrades. Same
   rule D-022 fixed for conversation discovery.

9. **What if the time-partitioning assumption is wrong?**
   Nothing breaks. Unestablished bounds always overlap and never satisfy
   `strictly_older_than`, so such a part is always visited and never stepped
   past. The assumption buys efficiency, not correctness. §5.5, F-6.

10. **Does the provider implement `MessageSource` so it could be dropped in?**
    No, deliberately. Its list-returning methods cannot express a partial
    answer; satisfying them would mean either discarding coverage or raising on
    every imperfect read. Which contract the bridge consumes is gate P4.

11. **Why `mode=ro` and not `immutable=1`?**
    `immutable=1` makes SQLite ignore the write-ahead log, silently dropping
    not-yet-checkpointed messages. The interface gate already recorded a
    WAL-resident row as a real shape. When the log cannot be read, the honest
    outcome is an `UNAVAILABLE` part and a visible coverage downgrade. Asserted
    by G3.

12. **Is `ReaderSegmentCensus` leakage of a provider concept into generic core?**
    No. "Segment" names a generic property — a source composed of independently
    readable parts — and carries no WeChat vocabulary. A non-composite source
    reports `None`, which is a different claim from a census of zeroes. G3's
    scan is what keeps the distinction honest.

13. **Where do FTS and caching go?**
    Nowhere, now. §9 records only that the envelope would be reused unchanged,
    and that a cached or indexed answer must never claim coverage stronger than
    an unindexed read of the same window.

14. **Does anything here justify obtaining a WeChat database?**
    No. D-030 lapsed, no access material is retained, and every test in §12 is
    synthetic. P2 records that real multi-part verification is a decision
    gate, not an implementation task, and that it is **not satisfiable today**.

---

## 14. What this document changes

Nothing executable. It adds one design document. It does not create, edit or
delete any module, test, dependency, build phase, environment variable, default
or route. `wechatdb` is untouched. The MCP surface stays exactly four tools.
`selected_source_name()` still defaults to `visual`, and visual capture / OCR
remains the production read path.
