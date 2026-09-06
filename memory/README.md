# `memory/` — M1 Memory Foundation

A source-neutral local memory layer between normalised messages and any future
retrieval or intelligence feature. Local, deterministic, lexical. No embedding,
no vector index, no network call, no new MCP tool.

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
MemoryRetriever         memory_retrieval.py   (internal API, not an MCP tool)
```

`memory_consent.py` is the gate every entry point passes through, and
`memory_identity.py` is the deterministic identity and fingerprint derivation.

Nothing in `bridge/` or `shadow/` imports this package; the dependency runs one
way only. See `docs/v2/M1_MEMORY_FOUNDATION.md` for the schema, the identity
limitations, the coverage model, the consent gap, and the M1 → M4 layering.

Tests: `cd memory && python -m pytest`.
