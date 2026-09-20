# DB Reader Standing Acquisition Design — P2

## 1. Decision

**P2 = MET as a product decision.**

The product is approved to implement a standing, local-only database acquisition
capability for the explicitly enabled Database Mode defined by D-033/P1.

This approval is conditional on the architecture in this document. It is not a
runtime wiring change and does not make acquisition available today.

The standing capability exists only while the user has explicitly enabled
Database Mode. When that mode is off, no database snapshot, decrypt, key refresh
or background database read may run.

P4 and P5 remain unmet.

---

## 2. Product objective

The target path is:

```text
explicit user enablement
        ↓
AcquisitionReadiness
        ↓
KeyStore ── needs key ──→ explicit Bootstrap Assistant
   │                         ↓
   └──────── valid key ←─────┘
        ↓
Snapshot Manager
        ↓
main DB + WAL consistent private encrypted snapshot
        ↓
Decrypt Workspace
        ↓
ephemeral plaintext SQLite snapshot
        ↓
IdentityCatalog + ShardedMessageProvider
        ↓
Reader boundary
```

If any prerequisite cannot be established defensibly, Database Mode fails
closed and the product may use the visual fallback. Acquisition failure never
weakens Reader coverage claims.

---

## 3. Standing consent

Database acquisition is opt-in.

### 3.1 Enablement

The product must require an explicit Database Mode enable action before it may:

- read the WeChat database container for acquisition purposes;
- use stored database keys;
- create encrypted or plaintext private snapshots;
- request a bootstrap/key refresh;
- run background database refreshes.

No green test, successful bootstrap, stored key, prior session or application
upgrade counts as consent.

### 3.2 Disablement

Turning Database Mode off immediately stops standing acquisition and database
reads. Stored keys may remain in Keychain so setup can be resumed later, but
possession of a stored key does not itself authorize use while the mode is off.

The product must also expose a distinct **Forget Database Access** operation
which deletes stored database keys and acquisition metadata and removes any
retained private acquisition state.

### 3.3 Bootstrap consent

Bootstrap is never automatic. A missing, invalid or stale key produces a
`needs_bootstrap` readiness state. The user must explicitly start the bootstrap
flow each time it is required.

---

## 4. KeyStore

Raw database keys must never be persisted in JSON, preferences, logs, command
output, analytics, crash metadata or repository files.

The production KeyStore is macOS Keychain.

### 4.1 Stored material

Each Keychain record contains only the minimum material needed to reuse a proven
key:

- raw key bytes;
- an opaque source fingerprint;
- logical database role/scope;
- key-record format version;
- compatibility metadata needed to decide whether the record may be tried.

The source fingerprint must not contain a username, display name or filesystem
path. A digest over structural database evidence and the logical role is
acceptable.

No Keychain item is synchronizable across devices.

### 4.2 Lookup and replacement

An existing key is tried only against a private snapshot. A failed validation
never causes the product to mutate the source or repeatedly retry the same key.
It moves readiness to `needs_bootstrap` or `version_unverified` as appropriate.

A newly obtained key replaces an older record only after the new key has passed
snapshot decryption and structural validation. Replacement is atomic from the
product's perspective.

### 4.3 Logging

Key bytes, salts, PBKDF material and derived candidates are never logged.
Operational logs may record only fixed state/reason tokens.

---

## 5. Bootstrap boundary

Bootstrap exists only to establish or refresh KeyStore material when no valid
stored key is available.

It is not part of the normal Reader path.

### 5.1 D-005 remains intact

The normal shipped product dependency graph continues to contain no process
memory key extraction, Frida dependency, application re-signing or injected
runtime hook.

A bootstrap mechanism that needs such techniques must live behind a separate,
explicitly invoked helper boundary. The application runtime may request the
bootstrap flow and consume only its validated KeyStore result; it does not
import, link or call extraction primitives as ordinary Reader dependencies.

Distribution and signing of that helper are a later implementation/security
review. P2 does not silently amend D-005.

### 5.2 Bootstrap output

Bootstrap output may enter only the KeyStore validation path. It must not be
written to a plaintext transfer file or printed to stdout/stderr.

The bootstrap flow succeeds only when candidate material validates against the
expected private snapshot and structural source identity.

---

## 6. Source scope and discovery

Standing acquisition does not authorize broad filesystem search.

A future SourceLocator may inspect only the expected official WeChat data
container and product-owned acquisition state. It may not scan the user's home,
other application containers, removable media or arbitrary databases looking
for compatible files.

If multiple WeChat account roots are present, the product must not silently
merge them. Account/root selection must be explicit or established by a later
separately tested source-selection rule.

The first implementation stage should continue to use explicitly supplied
source handles/paths so acquisition correctness can be built before locator
policy.

---

## 7. WAL-aware Snapshot Manager

The product never decrypts or modifies the live WeChat database in place.

Every Reader refresh operates on a product-owned private snapshot.

### 7.1 Snapshot set

For each required database role the snapshot captures, where present:

- main database;
- WAL;
- SHM metadata needed to identify the committed WAL frame boundary.

Sidecar files may be added only by a separately documented compatibility rule.

### 7.2 Stable-copy rule

A snapshot is accepted only when the source set can be shown stable enough to
represent one committed state.

The implementation must use a bounded copy/verify/retry protocol. At minimum it
must compare structural source signatures before and after copying and verify
that the private WAL/SHM pair describes a coherent committed frame range.

If the source changes during every attempt, snapshot creation returns a fixed
`source_busy`/`snapshot_unstable` state. It never guesses and never asks the
Reader to interpret a torn snapshot.

### 7.3 Encrypted WAL replay

Committed WAL frames are replayed only into the product-owned **encrypted** main
copy. The live source is never checkpointed or mutated.

Replay must validate WAL header/page-size/salt/frame structure and refuse
malformed or contradictory evidence.

Only committed frames belonging to the accepted snapshot generation may be
applied.

### 7.4 Real evidence

The G2 evidence established that main-file-only acquisition missed two message
rows and lagged the latest observed moment by 62 seconds, while private encrypted
WAL replay followed by decryption produced `integrity_check = ok`.

Therefore WAL awareness is a correctness requirement, not an optimization.

---

## 8. Decrypt Workspace

Decryption occurs only after a stable private encrypted snapshot exists.

### 8.1 Location and permissions

Plaintext databases live only in an app-owned private runtime/cache directory,
not a shared global `/tmp` location. The directory and files use restrictive
current-user permissions.

Plaintext paths are opaque and contain no account/user identifiers.

### 8.2 Lifecycle

Plaintext snapshots are ephemeral:

1. create private workspace;
2. decrypt required snapshots;
3. run SQLite structural/integrity validation;
4. hand explicit handles to IdentityCatalog / ShardedMessageProvider;
5. close readers;
6. unlink the plaintext workspace on success or failure.

A startup janitor removes stale workspaces left by a previous crash.

The product must not claim cryptographic secure deletion on APFS/SSD storage;
unlinking and lifecycle minimization are the guarantees.

### 8.3 Validation

A decrypted file is not usable merely because decryption returned bytes.
Required validation includes SQLite readability/integrity and the expected
role-specific structural probe.

Validation failure does not expose partial plaintext to the Reader.

---

## 9. AcquisitionReadiness

Acquisition state is a product-owned state machine separate from Reader
coverage.

The closed initial state vocabulary is:

```text
disabled
needs_bootstrap
ready
source_busy
snapshot_unstable
decrypt_failed
schema_unsupported
version_unverified
internal_error
```

These states describe whether Database Mode can construct a Reader source. They
must never be translated into stronger `ReadCoverage` claims.

`ready` means, for the current source generation:

- Database Mode is enabled;
- a candidate key is available and validated;
- a coherent private snapshot can be built;
- required decrypt/structural validation succeeds;
- the resulting explicit handles are suitable for the proven Reader/provider.

---

## 10. Failure and fallback

Database acquisition is fail-closed.

A failure never:

- mutates the live WeChat database;
- weakens schema validation;
- reuses unverifiable plaintext;
- silently broadens filesystem search;
- auto-runs bootstrap;
- changes Reader coverage to appear complete.

At the product-selection layer, an unavailable Database Mode may fall back to
visual capture according to the later P5 integration design. P2 defines the
readiness signal only; it does not wire that fallback today.

---

## 11. Version compatibility

The bounded real evidence currently proves one current WeChat build/container
shape, not future compatibility.

Unknown or changed source versions fail closed as `version_unverified` until the
compatibility probe establishes that the required structural contracts still
hold.

An application update does not automatically delete existing Keychain records;
it makes them candidates that must revalidate against a private snapshot before
use.

No filename, build number or key success alone proves compatibility.

P4 remains a separate gate.

---

## 12. Privacy and retention

Acquisition remains local-only.

The acquisition subsystem must not upload keys, encrypted database pages,
plaintext SQLite content, usernames, contact names or message content.

Operational telemetry, if any is later added, may contain only coarse fixed
state/reason tokens and non-identifying timing/count metrics after a separate
privacy review.

No database key or plaintext snapshot is part of backup/sync behavior.

---

## 13. Implementation boundaries

P2 is implemented in stages. Passing one stage does not authorize jumping to a
later stage.

### A — contracts and readiness

Define source-neutral acquisition result/readiness contracts and architecture
guards. No Keychain access, file copying, decryption or bootstrap yet.

### B — KeyStore

Implement the Keychain-backed store against synthetic/random key material and
mock source fingerprints. No real WeChat key acquisition.

### C — Snapshot Manager

Implement stable-copy and encrypted-WAL replay using fabricated encrypted-like
page fixtures and mutation tests. Never touch a live WeChat source in this
stage.

### D — Decrypt Workspace

Implement private workspace lifecycle and decryption behind an injected
`Decryptor` contract. Production decrypt compatibility requires its own bounded
real gate.

### E — Bootstrap boundary

Specify and implement the explicit helper/IPC boundary. D-005 review is required
before any extraction mechanism is distributed or invoked.

### F — Acquisition coordinator

Compose A-E into explicit prepared handles for IdentityCatalog and
ShardedMessageProvider. Still no product source-selection wiring.

### G — P4 compatibility, then P5 wiring

Only after P4 passes may product integration decide the final enabled/default /
fallback behavior under P5.

---

## 14. Promotion-gate state after this decision

| Gate | State |
|---|---|
| P1 — explicit product role | **MET** |
| P2 — standing acquisition decision | **MET** |
| P3 — real multi-part verification | **MET under D-032** |
| P4 — complete-container / future-version compatibility | **UNMET** |
| P5 — runtime/default integration | **UNMET** |

**No runtime source selection changes under P2.**
