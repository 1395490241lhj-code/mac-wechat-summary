# `memory/` — M1 Memory Foundation

A source-neutral local memory layer between normalised messages and any future
retrieval or intelligence feature. Local, deterministic, lexical. No embedding,
no vector index, no network call. Its only public surface is the separate,
explicitly enabled, read-only memory MCP server (M2.1).

```
MessageSource  (bridge/message_source.py)
    ↓  NormalizedMessage / NormalizedConversation
MemoryIngestor          memory_ingest.py
    ↓
MemoryStore             memory_store.py
    ├─ canonical conversations / messages
    ├─ ingestion runs
    ├─ coverage / provenance
    └─ FTS5 index
    ↓
MemoryQueryService      memory_query.py       (M2 internal contract, not an MCP tool)
    ├─ search · timeline · context_around · recent_context
    └─ MemoryQueryResult { items, coverage, truncated, query_scope }
wechat_memory_mcp.py    separate read-only MCP server (M2.1, +discovery M2.2b):
                        memory_conversations · memory_search · memory_timeline ·
                        memory_context · memory_recent — off by default, opened mode=ro
memory_sync.py          explicit foreground sync (operator-run, consent-gated, no scheduler)
memory_retrieval.py     M1 retrieval, kept intact, superseded by memory_query
```

`memory_consent.py` is the gate every entry point passes through: it reads the
app-owned `consent.state` from the app's own preference domain and refuses when
it is missing, malformed, or says no. `memory_identity.py` is the deterministic
identity and fingerprint derivation for **source observations**; logical
identity above them lives in the store's `logical_*` tables (schema v2) and is
written only by an explicit `link_observation` with an accepted basis.

Coverage from several sources is composed by `compose_coverage` in
`memory_store.py`: per-source verdicts are never erased, the aggregate is the
most cautious reading, and an empty result is "no messages" only when every
consulted source covered the window.

Nothing in `bridge/` imports this package and the memory server never imports
the bridge; `shadow/runners/claude.py` loads the consent gate by path only to
refuse a memory request before a run. See `docs/v2/M1_MEMORY_FOUNDATION.md` for the schema, the identity
limitations, the coverage model, the consent gap, and the M1 → M4 layering.

Intended agent flow: `memory_conversations("产品群")` → a `canonical_conversation_id`
(or several candidates, never a guess) → `memory_search` / `memory_timeline` /
`memory_recent`. Matching is exact-then-substring after NFC/trim/casefold; no
fuzzy matching, no merge by name.

Every result also carries **freshness** (`memory_freshness.py`): when memory
last synced and through what point each source was observed — separate from
coverage (what portion of the *requested* data was observed) and from the
newest stored message. No `is_fresh`, no staleness threshold.

Sync (operator, foreground; honours `WECHAT_COMPANION_MESSAGE_SOURCE`, never
substitutes a source): `python3 memory/memory_sync.py`.

The app runs the same ingestion through `memory_worker.py`, frozen into a
self-contained helper bundle (`scripts/build-memory-worker.sh`) that ships
inside the app — so a normal install needs no Python. The store's one
canonical location lives in `memory_paths.py`, matched by
`MemoryStoreLocation.swift`.
Tests: `cd memory && python -m pytest`.
