# M2 — Memory Retrieval + Citation

**Sealed:** 2026-09-06 · **Branch:** `v2/rewrite` · **Builds on:** `7ae6963` (M1.1)
**Scope:** local, deterministic, lexical. No MCP tool, no AI, no embedding, no
scheduler, no reader change, no real data. This is the **stable internal
contract**; a later M2.1 gate decides what, if anything, becomes public.

```
MemoryStore
    ↓
MemoryQueryService          memory/memory_query.py
    ├─ search
    ├─ timeline
    ├─ context_around
    └─ recent_context
    ↓
MemoryQueryResult  { items, coverage, truncated, query_scope, focal_canonical_id }
```

---

## 1. The envelope — non-droppable by construction

Every method returns a frozen `MemoryQueryResult`. There is no call that
returns items alone, and a test asserts the service exposes none.

| Field | Meaning |
|---|---|
| `items` | `MemoryItem`s — one observation each, or one linked logical message each |
| `coverage` | `CoverageReport`: per-source verdicts, aggregate status, required vs supplemental sources, complete sources, caveats, `trustworthy_empty` |
| `truncated` | measured (one row past the limit is fetched), never assumed |
| `query_scope` | `QueryScope`: kind, resolved `SourcePolicy`, conversation, window, limit, order, filters, anchor |
| `focal_canonical_id` | for `context_around`, the focal observation |

`is_empty_and_trustworthy` is true only when `items` is empty **and**
`coverage.trustworthy_empty`.

## 2. Coverage — required vs supplemental

`SourcePolicy(required, supplemental)`; a source cannot be both.

- **Aggregate status** is composed over **required** sources only, by the
  M1.1 rule (`compose_coverage`: nothing partial becomes complete).
- **`trustworthy_empty`** ⇔ at least one required source **and** every
  required source complete for the window. Supplemental sources can neither
  make it true nor make it false.
- **Nothing is hidden.** Every consulted source's verdict is in `per_source`;
  every non-complete source (required or supplemental) becomes a caveat
  (`database:unavailable`, `database:source_error`, …).
- **No substitution.** Composition is over verdicts; no source's data or
  coverage ever stands in for another's.

**Default policy (no policy given):** every source the store has ever recorded
coverage for is **required**; none is supplemental. Naming a source in the
query makes that source the single required one. Supplemental status is
reachable **only** by passing an explicit `SourcePolicy`. A required source
with no coverage record is `not_observed`.

## 3. Citation — `MessageCitation`

Frozen, text-free, identical across all four query kinds (asserted):

```
canonical_message_id · logical_message_id (None = unknown, not none)
canonical_conversation_id · source · source_message_id (None if derived)
identity_mode · timestamp · timestamp_kind
```

Every `MemoryItem` exposes `citation` (its representative's) and `citations`
(one per contributing observation); `MemoryQueryResult.citations` flattens
them. `as_dict()` yields exactly these eight keys.

## 4. Capabilities

**search** — text (FTS, every term quoted, Han segmented as in M1),
conversation, sender, source, ownership, start/end, `relevance|recent|oldest`,
bounded limit (1…500). `relevance` requires text.

**timeline** — one conversation, independent of FTS, ordered by
`(timestamp, COALESCE(sequence,0), canonical_id)` ascending. Window by
start/end. Paged by `TimelineCursor(timestamp, sequence, canonical_id)` —
built only from an item, compared as a row value on exactly the ordering key.
`after` pages forward; `before` returns the newest `limit` items before the
cursor, still delivered oldest-first, so order never flips. A bare canonical
id is not accepted as an anchor: ids are digests and carry no time order.
Passing both anchors is refused.

**context_around(canonical_message_id, before, after)** — resolves the focal
observation (unknown id → `message_unknown`, never an empty context), returns
the `before` immediately earlier and `after` immediately later observations
**in the same conversation observation** (never across sources — a neighbour
from another reader's view of "the same" chat would be a guess), marks the
focal (`is_focal`, `focal_canonical_id`), clips at conversation edges, and
reports `truncated` when context exists beyond either side. The coverage
window is the span actually returned; the focal's source is the required
source unless a policy says otherwise.

**recent_context** — newest messages in scope (optional conversation,
since/until, source), bounded; `recent` (default) newest-first, `oldest`
delivers the same newest-`limit` set chronologically.

## 5. Logical-message retrieval

Grouping happens on **explicit links only** (`messages.logical_message_id`,
M1.1). Linked observations become one item with every observation and every
citation; the representative is chosen by a fixed rule — `source_created`
timestamp beats `first_observed`, then higher confidence, then smallest
canonical id — with no preference for a reader by name. Unlinked lookalikes
remain separate items; conflicting provenance (different timestamps, meanings,
visible-time strings) is preserved on each observation. Grouping is applied to
a page's own rows, so a logical message straddling a page boundary appears on
both pages with the observations each page saw — a visible seam, preferred to
silently widening a page. **No heuristic cross-source deduplication exists.**

## 6. Explicit sync — `memory/memory_sync.py`

`sync_from_source(source, environment, read_app_consent_state)` and a thin
CLI `python3 memory/memory_sync.py [--conversation-limit N] [--message-limit N]`.
Operator-initiated, foreground, returns. Consent-gated through
`resolve_consent` (every refusal that gate can make is a refusal here; a
refusal touches the source **zero** times and creates no file). Ingestion is
the M1 ingestor, so it is idempotent and atomic. The visual reader is imported
lazily from the bridge and still applies the bridge's own two opt-ins. Nothing
in `bridge/` or `shadow/` imports this module; no MCP write/action tool exists;
a structural test asserts no timing, threading or event-loop module is
imported. **No app/UI action was added** — a Settings-level "sync now" is app
work and is left for a later decision rather than expanding M2.

## 7. Public boundaries unchanged

The four MCP tools, `ClaudeRunner`, the readers and `memory_retrieval.py` (the
M1 contract, kept intact and marked superseded) are untouched. The import
allowlist and "nothing in bridge/shadow references memory" checks still hold,
now covering `memory_query` and `memory_sync`.

## 8. Gates

| Suite | M1.1 | M2 |
|---|---|---|
| `memory/` | 154 | **211** (+49 query, +8 sync) |
| `bridge/` | 72 | **72** unchanged |
| `shadow/` | 92 | **92** unchanged |
| Swift | 177 / 14 | **177 / 14** (no Swift change in M2; M1.1 run stands) |

Total 552.

## 9. Recommended M2.1 — public memory-tool surface

Read-only, all returning the envelope verbatim (coverage and scope cannot be
dropped on the wire), gated exactly as the bridge's four tools are and
verified on the wire by the shadow runner's exact-N proxy:

1. `memory_search` — `search` with the same filters; no SQL, no path.
2. `memory_timeline` — `timeline` with cursor paging.
3. `memory_context` — `context_around`.
4. `memory_recent` — `recent_context`.

Decisions that gate it: whether per-source coverage and `logical_message_id`
are user-visible; whether `SourcePolicy` is caller-settable over the wire (the
conservative default should be the only option at first); and that no sync,
link or write tool is exposed — ever, through this surface.
