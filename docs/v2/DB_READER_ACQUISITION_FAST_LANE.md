# DB Reader Acquisition Fast Lane

## Decision

After P2-B and its bounded live Keychain gate, the remaining non-bootstrap
acquisition implementation is compressed into one bounded workstream.

The workstream combines the previously staged P2-C, P2-D and P2-F work while
preserving their internal invariants:

1. private stable encrypted snapshot;
2. committed encrypted WAL replay;
3. ephemeral decrypt workspace;
4. acquisition coordinator producing `PreparedSource`.

This is an implementation-scheduling change, not a weakening of D-034.

## Preconditions already met

- P1 product role: MET.
- P2 standing acquisition decision: MET.
- P3 real multi-part Reader verification: MET.
- P2-A contracts/readiness: sealed.
- P2-B KeyStore: implemented at `0737b32`.
- bounded live Keychain gate: MET at `afdf1ca` using disposable random material.

## In scope

### Explicit source inputs only

The Fast Lane receives an explicit source set from its caller. It does not walk
or search the filesystem and does not choose an account/root.

A source item may identify:

- encrypted main database;
- optional WAL;
- optional SHM;
- logical source role;
- KeyDescriptor.

Paths/handles are acquisition-internal. `PreparedSource` remains opaque.

### Stable encrypted snapshot

The live source is never modified, checkpointed or decrypted in place.

For each source role, build a product-owned encrypted copy using a bounded
copy/verify/retry protocol. Pre/post structural signatures must prove that the
accepted main/WAL/SHM generation did not change while being copied.

If a stable generation cannot be obtained within the bound, return
`snapshot_unstable`/`source_busy`; never pass a torn source to decryption.

### Encrypted WAL replay

WAL replay happens only against the private encrypted main copy and only through
the last committed frame proven to belong to the accepted WAL generation.

Validate at least:

- WAL magic / page size;
- frame structure;
- frame salts against the WAL header;
- non-zero page numbers;
- commit boundary / database-size semantics;
- truncation of the private encrypted main copy when a committed frame declares
  a smaller database size.

Do not replay uncommitted trailing frames.

### Ephemeral decrypt workspace

Decrypt only the accepted private encrypted snapshot.

The plaintext workspace is app-owned/private, uses restrictive permissions, has
opaque names, and is always cleaned in success/failure paths. A startup janitor
may remove stale product-owned workspaces from earlier crashes.

Do not claim secure SSD/APFS erasure; minimize lifetime and unlink.

### Clean decryptor

Do not import or reuse `core/decryptor.py` or `core/wechat_db.py` in the new
acquisition path. They predate the new snapshot correctness contract.

Implement the minimum decryption behavior behind an injected acquisition-local
Decryptor contract. It may use existing project crypto dependencies, but no
legacy cache, source discovery or product-core behavior.

### Coordinator

The coordinator composes:

```text
explicit source set
    + KeyStore
        ↓
stable encrypted snapshots
        ↓
committed encrypted WAL replay
        ↓
ephemeral decryption + structural validation
        ↓
AcquisitionOutcome(READY, PreparedSource(...))
```

All normal failure states return the existing P2-A readiness vocabulary rather
than partial handles.

The coordinator does not register the provider or change source selection.

## Not in scope

- OCR or visual-capture implementation work;
- source/account discovery;
- Frida or process inspection;
- bootstrap/key extraction;
- application re-signing;
- product routing / P5 wiring;
- changing the visual default;
- broad future-version compatibility claims;
- attachments/media extraction.

Visual/OCR remains only the existing fallback product path; this workstream is
entirely the database path.

## Real gate after implementation

After synthetic/mutation validation and independent review, run one bounded
current-version real end-to-end acquisition gate:

```text
explicit current source
    → stored/validated key material
    → stable private encrypted snapshot
    → committed WAL replay
    → decrypt workspace
    → PreparedSource
    → IdentityCatalog
    → ShardedMessageProvider
```

The gate must verify structural counts/coverage only and must not commit real
keys, usernames, paths or message content.

## Bootstrap remains separate

P2-E is deliberately not part of the Fast Lane. A valid stored key exercises
the normal database path without bootstrap.

Bootstrap is required only when the KeyStore has no usable material or when a
compatibility/key validation step invalidates it. Its helper/security boundary
will be designed after the normal stored-key path is proven end to end.

## Gate state

P1/P2/P3 remain MET. P4/P5 remain UNMET. The database provider remains unwired
and the runtime default remains visual until a later explicit integration gate.
