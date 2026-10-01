# GreenBubbles assimilation audit — 2026-10-01

## Scope

Read-only comparison of `mac-wechat-summary` `v2/rewrite` at
`4019d1761d76dab4c9360727b1e5463238483688` against
`bojieli/greenbubbles` `0.10.0` at
`69f19c7089d7ef011e9ba43d44c6fb4e6e5be98b`.

No production WeChat process, container, credential, database or protected store
was touched. No LLDB, Frida, process-memory capture, re-signing, injection,
network capture or product-source wiring was performed.

This audit does not authorize a new acquisition route. D-005 and D-036 remain
unchanged.

## Executive result

**KEEP our snapshot/coverage/provider architecture. STUDY selected GreenBubbles
reader behaviours. DO NOT adopt its credential acquisition path.**

The comparison changes the initial hypothesis that GreenBubbles could replace
our database reader. Our reader is already stronger in the areas that matter
most to the product boundary: private WAL-aware snapshot construction, explicit
coverage semantics, source/provider isolation, and real WeChat 4.1.15
multi-part verification.

GreenBubbles is most useful as fresh public evidence for schema drift handling,
native WeChat FTS use, additional database-role inventory, lazy media lookup and
a bounded typed query surface.
## What GreenBubbles independently confirms

Both projects converge on the same WeChat 4.1.x credential model:

- one 32-byte account passphrase;
- each database has its own 16-byte salt;
- database key derivation is PBKDF2-HMAC-SHA512, 256,000 iterations, 32 bytes;
- SQLCipher page authentication uses the separate SQLCipher-4 HMAC derivation.

That independently supports the credential-model correction already present in
our `acquisition/deriver.py` and standing design. It does not create a supported
secret source.

GreenBubbles' public acquisition path re-signs production WeChat and attaches
LLDB to capture the 32-byte passphrase during PBKDF2. Its guide says that path
has been tested on WeChat 4.1.12 and 4.1.13. That mechanism conflicts directly
with D-005 and is **NOT ADOPT**.

Our bootstrap already has the correct seam: `BootstrapSecretProvider`.
The shipped provider is operator-local hidden input of a 64-hex account secret.
Therefore the current blocker is not a missing bootstrap interface; it is the
absence of a supported source for the account secret.

A future file/import provider would be a separate security/product decision.
It must not be presented as GreenBubbles support while GreenBubbles' ordinary
credential source depends on the prohibited acquisition mechanism.
## KEEP — existing implementation

Keep the current acquisition/read split:

- explicit selected source root;
- Keychain-backed account-secret records;
- per-database key derivation;
- bounded private encrypted snapshot;
- encrypted WAL replay before decryption;
- ephemeral plaintext lease;
- isolated `ShardedMessageProvider`;
- non-droppable `ReadCoverage` contract.

Our G2 evidence is materially stronger than GreenBubbles' current published
live-read timing evidence for the concurrency question: one WeChat 4.1.15
snapshot, seven readable message parts, 532,860 message rows, 86 conversations,
three cross-part conversations, collision-free public identity/sequence, and a
1,015-row conversation recovered exactly across three pages.

The WAL gate also proved that a main-file-only copy missed two rows and lagged
the latest observed moment by 62 seconds. GreenBubbles explicitly states that
its normal live path is statement-consistent per database and not
cross-database atomic. That is an acceptable CLI tradeoff for GreenBubbles but
not a reason to weaken our snapshot boundary.

Do not replace our pagination merely because GreenBubbles uses a richer opaque
compound cursor. Our provider's public sequence/cursor path already passed the
real multi-part exhaustion and collision gates. Any cursor change requires a
specific failing case, not aesthetic convergence.
## STUDY / possible later adoption

1. **Schema drift accounting.** GreenBubbles inventories tables and columns,
   distinguishes known auxiliary tables from unsupported message-like tables,
   and makes gaps explicit. Our parser already treats optional columns
   conservatively, but complete-container role accounting remains P4 work.

2. **Database-role inventory.** GreenBubbles explicitly recognizes
   `message_*.db`, `biz_message_*.db`, message-side/top-level media DBs,
   native FTS, hardlink, chatbot, SNS and other auxiliary stores. Our current
   bounded refresher admits every direct `*.db` in the selected message
   directory under one message-role descriptor. Before P4, make the inventory
   role-aware rather than broadening it further.

3. **Native FTS first.** GreenBubbles queries WeChat's compatible
   `message_fts.db` when available and falls back to a bounded decoded scan.
   Our Memory FTS is a different, post-ingestion index; Database source search
   has no equivalent native path today. This is useful only after the generic
   reader/search boundary is explicitly extended.

4. **Lazy media resolution.** GreenBubbles keeps ordinary message reads light
   and resolves/materializes selected media later. Our parser already exposes a
   media digest while Archive has its own attachment model. A future Database
   attachment bridge should reuse that lazy principle rather than eagerly
   restoring all media.

5. **Typed bounded query surface.** GreenBubbles exposes named operations, hard
   limits and opaque source-bound cursors instead of arbitrary SQL. This aligns
   with our existing generic reader/MCP principles; adopt the invariant, not
   necessarily its wire format.
## Provenance / code-reuse boundary

GreenBubbles itself is MIT licensed, but its Rust core pins multiple
`pandorafuture/wx-cli` crates and its acquisition code declares derivation from
`wcdb-key-tool`. Our existing project constraint treats the wx-cli family as
study-only clean-room behavioural evidence and forbids copying code, SQL/query
shapes, fixtures or identifier schemes into this repository.

This audit therefore authorizes **behavioural comparison only**. No GreenBubbles
or wx-cli implementation should be copied into production until the existing
provenance/layering decision is explicitly reconsidered with file-level
attribution and dependency review.

## Recommended next capsule

Do **not** start Agents/UI work and do **not** start credential integration.

The smallest justified engineering capsule is a **P4 database inventory
compatibility spike** against synthetic fixtures only:

- define a closed database-role inventory for the already-selected message
  directory;
- distinguish ordinary message, business-message, native-FTS/media/auxiliary,
  unknown and unsupported-message-candidate roles;
- preserve every inventory member in diagnostics/coverage accounting;
- prove that adding recognized auxiliary DBs cannot silently strengthen message
  coverage;
- keep SourceLocator explicit and bounded;
- touch no live source, credential path, product source selector or UI.

Only after that spike should we decide whether native FTS or lazy media deserves
its own implementation capsule.

## Decision impact

No existing decision is overturned. D-005 remains active. D-036 remains active.
P0 remains Safe Share. Database remains later P1/P2 and blocked for production
by `database-production-path-no-retained-access-material`.

The current `4019d176` product/research state is therefore a valid pause
baseline while this compatibility work proceeds independently.
