# D-005 — project-owned Frida Bootstrap secret-acquisition helper (design)

**Status: design pass only. Nothing was run, captured or published.**

This records a **newly designed route**, not a recovered one. No previously
proven project Frida path exists: the only real acquisition in this project's
history was an operator-run third-party tool under D-030, which lapsed and is
separate from this decision. No access material was captured or persisted during
this pass, and no Bootstrap production code changed.

The helper's purpose is to implement the already sealed boundary:

    BootstrapSecretProvider
        ├── OperatorSuppliedSecret        # sealed at 13cd4c2
        └── FridaBootstrapSecretProvider  # this design

## 1. Evidence recovered from this machine (2026-09-20)

| Fact | Value |
|---|---|
| Installed app | /Applications/WeChat.app, present |
| Signing | Developer ID Application: Tencent Mobile International Limited (5A4RE8SF68) |
| Hardened runtime | enabled (CodeDirectory flags 0x10000(runtime), Runtime Version 15.2.0) |
| Entitlements | app-sandbox true, application-groups, allow-jit, audio/camera |
| get-task-allow | **absent** |
| SIP | enabled |
| Frida | not installed system-wide; **frida 17.18.0 installs via uv run --with frida** |
| Main executable | Contents/MacOS/WeChat, **182 KB launcher** |
| Crypto symbols in launcher | **none** — no CCKeyDerivationPBKDF, sqlite3_key, sqlcipher, EVP_, HMAC |
| Real code | Contents/Frameworks/{andromeda,ilink2,MultiMediaDyn,ProtobufLite,Sparkle,ilink_*} |

## 2. Answers to the D-005 questions

**1. Instrumentation target.** A temporary copy, launched under a helper-owned
PID. The installed app is never modified. This is forced, not preferred: no
get-task-allow plus hardened runtime means the installed binary cannot be
attached to at all.

**2. Code-signing requirements.** The installed app carries hardened runtime and
no get-task-allow, so an instrumentable copy must be re-signed, and re-signing a
copy requires the signature to be replaced (ad-hoc or a local identity) together
with the nested frameworks and helpers. The exact minimum — which components
must be re-signed and whether the sandbox entitlement must be stripped for the
copy to be debuggable — is **not yet established** and is the first thing the
spike must settle. SIP stays enabled; no system-wide security control is
changed; no permanent signing change is made.

**3. Frida feasibility.** frida 17.18.0 is installable through the project's
existing uv pattern, so no new package manager is introduced. Local spawn of a
re-signed copy under SIP is expected to work and must be proven. **The proposed
interception point is disproven**: the launcher imports neither
CCKeyDerivationPBKDF nor any sqlite/sqlcipher/openssl symbol. The key derivation
happens inside a framework, so the real interception point must be identified
there before any capture design is written.

**4. SQLCipher correlation model.** The helper never validates a database. It
yields a bounded candidate, and the existing sealed chain decides:
candidate SecretBytes → Bootstrap candidate seam → AcquisitionCoordinator → all
required roles → IdentityCatalog + ShardedMessageProvider → provider envelope.
Only a candidate that chain accepts may be published. One account secret must
validate every role; a subset match is a stop-and-report, not a new contract.

**5. Candidate filtering.** Filter on non-secret structural properties only:
algorithm, iteration count, derived length, and the SQLCipher profile the Fast
Lane already uses. Bounded explicitly: a maximum candidate count and a finite
timeout. Candidate values are never printed, logged, or serialised.

**6. Secret transport.** In-process only where possible: the provider returns
SecretBytes directly to Bootstrap. If a helper process is required, the narrowest
local channel available, carrying raw bytes and nothing else — never argv, the
environment, JSON, a temp file, plaintext stdout, or a log. Crossing a process
boundary means the bytes exist in two address spaces; that is a real
confidentiality limitation and is stated rather than hidden.

**7. Process ownership.** Launch the copy, receive its exact PID from the launch,
instrument that PID, and clean up only that PID. Never select by the name
WeChat. The user's ordinary WeChat is never attached to or terminated.

**8. Temporary artifacts.** The copy, its re-signed products, the Frida script,
any channel endpoint, and any log. All live under one private per-attempt
workspace; none may contain secret material; all are removed on success,
failure, cancellation and crash. No secret-bearing artifact may persist.

**9. Explicit invocation only.** Reachable only from an explicit Bootstrap
lifecycle action. Ordinary runtime never imports Frida, spawns WeChat, inspects
processes or acquires secrets. A needs_bootstrap result may inform the caller but
never triggers instrumentation.

**10. Permissions.** Expected: Developer Tools (debugging) for the re-signed
copy, and whatever the sandbox requires once the copy is launched. Accessibility,
Automation and Full Disk Access are **not** assumed. The spike must report the
actual prompt set.

## 3. Threat model

Assets: the database secret; the selected WeChat source; the user's normal
WeChat installation and session; the KeyStore; the SourceLocator record.

| Risk | Control |
|---|---|
| Capturing unrelated crypto | filter on the proven structural profile; bound count and time |
| Candidate bytes via Frida console | never print; redact at the capture boundary |
| Attaching to the wrong process | instrument only the PID the helper launched |
| Modifying installed WeChat | copy-only; installed bundle never written |
| Signed copy left behind | one private workspace, removed on every exit path |
| Crash leaving artifacts | workspace under a private root, swept on next attempt |
| Helper reachable from normal runtime | explicit lifecycle only; import guard |
| Dependency supply chain | one declared dependency (frida) through the existing uv pattern |
| Secret copies in Python/JS runtimes | minimise copies, clear mutable buffers; **no claim of complete erasure** |

Honest limitation: Python, Frida and JavaScript runtimes give no guarantee of
memory zeroisation. The achievable property is bounded lifetime and no
persistent copy, not erasure.

## 4. Decision

**B. FEASIBLE WITH CHANGES.**

The isolation model holds — copy, re-sign, launch, instrument, clean up — and the
dependency story is clean. Two things must change before implementation:

1. **The interception point is wrong.** CCKeyDerivationPBKDF is not reachable
   from the launcher; the real boundary must be located inside the framework
   that performs the derivation.
2. **The re-signing minimum is unproven.** Which components must be re-signed,
   and whether the sandbox entitlement must be dropped for the copy, is unknown.

A third change is a consequence: the spike below cannot be run until (1) and (2)
are settled, because there is nothing to hook and nothing proven to launch.

## 5. Bounded feasibility spike — not run

The spike (create a copy, prove the installed app untouched, launch, instrument,
observe the crypto boundary, clean up) was **not run** in this pass, and that is
a deliberate stop rather than an omission: the evidence above disproves the
proposed hook, so a spike would have nothing to attach to; and launching a
re-signed second WeChat instance has session side effects that should not be
incurred for a design pass.

## 6. Smallest next implementation capsule

1. Locate the derivation boundary in Contents/Frameworks (static analysis of the
   frameworks, no launch) and name the exact symbol.
2. Establish the re-signing minimum on a throwaway copy and prove the installed
   app is byte-identical afterwards.
3. Run the bounded spike against the helper-owned PID with capture redacted to a
   boolean and a count.
4. Only then design the FridaBootstrapSecretProvider implementation.

Until (1)–(3) are done, no production helper is written and the sealed
OperatorSuppliedSecret path remains the only supported mechanism.

---

# Capsule 1 — static boundary analysis (2026-09-20)

Static inspection only: no launch, no copy, no re-sign, no Frida, no attach, no
process memory, no key. Nothing was mutated.

## Observed fact — bundle layout

The bundle is 1.4 GB. `Contents/Frameworks/` holds **thin shims** (83 KB–301 KB)
and imports no crypto and no `sqlite3_*` at all. The real implementation is under
`Contents/Resources/`:

| Binary | Size |
|---|---|
| `Contents/Resources/wechat.dylib` | **337 MB** |
| `Contents/MacOS/WeChatAppEx.app/.../WeChatAppEx Framework` | 391 MB (Chromium shell) |
| `Contents/Resources/MultiMediaDyn.framework/...` | 52 MB |
| `Contents/Resources/ilink2.framework/...` | 17 MB |
| `Contents/Resources/andromeda.framework/...` | 8.7 MB |

## Observed fact — the crypto boundary

`Contents/Resources/wechat.dylib` reports **compatibility version 4.1.15**
(current 4.31.14) and imports from CommonCrypto:

- **`CCKeyDerivationPBKDF`** — the proposed hook, present;
- `CCCrypt`, `CCCryptorCreate`, `CCCryptorUpdate`, `CCCryptorFinal`,
  `CCCryptorRelease` — AES-CBC, matching the sealed profile;
- `CCHmacInit`, `CCHmacUpdate`, `CCHmacFinal` — HMAC, matching the sealed
  profile's page authentication.

`CCKeyDerivationPBKDF` also appears among the dylib's own defined symbols, which
is consistent with an internal alias or PLT stub. **No `sqlite3_*` symbol is
imported by any image**, so SQLite/SQLCipher is statically linked — consistent
with the 337 MB size. Other frameworks import only `CC_SHA1`/`CC_SHA256`
(`WCDYWrapper`) and `CCCrypt` (`libwxld`), and the Sparkle updater uses
`CC_SHA1` for its own signatures; none of those is the database path.

## Why the earlier assumption was wrong

The symbol was right; the **binary** was wrong. The earlier pass inspected
`Contents/MacOS/WeChat`, a 182 KB launcher, and concluded the hook was absent.
The implementation lives in `Contents/Resources/wechat.dylib`, which the launcher
loads at runtime.

## Inference — which invocation matters

The sealed profile derives a 32-byte key and then a MAC key with
PBKDF2-HMAC-SHA512, 2 iterations, `salt ^ 0x3a`. A `CCKeyDerivationPBKDF` call
with **2 iterations** is therefore the HMAC-subkey derivation, which yields the
MAC key and *not* the database secret. The database secret must come from the
differently-parameterised invocation (the account KDF). Which call carries which
value cannot be settled from symbols alone.

## Unresolved question

The exact call-site discrimination — algorithm, iteration count and derived
length that identify the account-secret derivation — needs bounded runtime
observation. The symbol is identified; the filter parameters are not.

## Conclusion

**STATIC BOUNDARY FOUND.**

- Framework/binary: `Contents/Resources/wechat.dylib`
- Symbol: `CCKeyDerivationPBKDF` (imported from CommonCrypto; the AES and HMAC
  primitives alongside it confirm the SQLCipher-shaped path)
- Why preferred: it is the key-derivation boundary itself, so a hook there sees
  derived key material rather than bulk ciphertext, and it is far narrower than
  `CCCrypt`/`CCHmac`, which carry unrelated traffic.
- Expected semantics: `CCKeyDerivationPBKDF(algorithm, password, passwordLen,
  salt, saltLen, prf, rounds, derivedKey, derivedKeyLen)`; the candidate is the
  output buffer.
- Remaining dynamic proof: which invocation carries the account secret, and
  therefore the filter (algorithm, rounds, derived length), plus confirmation
  that the boundary is stable across the startup path.
- Capsule 2 (re-signing) is now meaningful, because there is something to
  instrument: the temporary copy must allow a hook on `wechat.dylib`'s
  CommonCrypto imports.

---

# Capsule 2 — throwaway-copy re-sign feasibility (2026-09-20)

A private temporary copy was made outside the installed app and re-signed. The
installed app was never modified, nothing was launched, no Frida was installed,
no process was attached, and no cryptographic material was observed.

## Observed fact — installed baseline

- `Contents/MacOS/WeChat` sha256 `b21aeae5c3e4d570…`
- `Contents/Resources/wechat.dylib` sha256 `2af9442379888ee4…`
- Identifier `com.tencent.xinWeChat`, Team `5A4RE8SF68`, Developer ID signed,
  CodeDirectory `flags=0x10000(runtime)`
- 17 entitlements, including `app-sandbox`; **no** `get-task-allow` and **no**
  `disable-library-validation`
- 46 nested bundles/frameworks and 32 dylibs participate in the seal

## Observed fact — minimum re-sign

One strategy was sufficient, and no deeper one was needed:

    ditto /Applications/WeChat.app <workspace>/WeChat.app
    codesign --force --sign - --options runtime \
        --entitlements <original + 2 debug keys> <workspace>/WeChat.app

- `codesign --verify --strict` → exit 0
- `codesign --verify --deep --strict` → exit 0, so **no nested component
  required re-signing**; the outer re-sign re-sealed the existing nested
  signatures, which stayed valid
- resulting CodeDirectory `flags=0x10002(adhoc,runtime)` — ad-hoc **and** hardened
  runtime preserved
- `TeamIdentifier=not set`, which is what ad-hoc signing implies
- the two added entitlements were present afterwards, and `app-sandbox` was
  **preserved**, not removed

## Inference

The minimal transformation is therefore a single outer-bundle ad-hoc re-sign
with a two-key entitlement delta: `get-task-allow` (so the helper-owned
process can be attached to) and `disable-library-validation` (so a Frida
dylib signed by a different identity is not rejected by hardened runtime).
Keeping the sandbox costs nothing at signing time and was not shown to be an
obstacle.

## Unresolved question

Whether the copy actually launches, whether the sandbox permits it with those
entitlements, and whether the injected dylib loads — all runtime facts. Signing
success is not launch success.

## Conclusion

**RE-SIGN PATH ESTABLISHED.**

- Components requiring signing: the outer bundle only.
- Order: copy, then one outer re-sign; no nested pass.
- Entitlement delta: `get-task-allow` and `disable-library-validation`, on top of the
  original 17, sandbox retained.
- Hardened runtime: preserved (`0x10002(adhoc,runtime)`).
- Sandbox: can remain intact.
- Unproven until Capsule 3: launch under the sandbox, and dylib injection.
- Cleanup: the workspace was removed and verified absent; the installed bundle's
  two critical hashes were re-checked and are byte-identical.

---

# Capsule 3 — launch isolation gate (2026-09-20)

**Result: LAUNCH ISOLATION UNPROVEN. Nothing was launched.**

The capsule's hard gate asks whether the re-signed temporary copy could reach the
user's live production WeChat data. Static evidence says it could, so the launch
was refused rather than attempted.

## Observed fact

- `CFBundleIdentifier` is `com.tencent.xinWeChat`. The copy keeps it: nothing
  in the proven re-sign path changes the bundle identity.
- The signature carries `com.apple.application-identifier` =
  `5A4RE8SF68.com.tencent.xinWeChat`, `com.apple.security.application-groups` for the
  same production group, `com.apple.security.app-sandbox = true`, and broad
  exceptions (`temporary-exception.sbpl`, `temporary-exception.mach-lookup.global-name`).
  The Capsule 2 strategy preserves all of them.
- The production containers exist: `~/Library/Containers/com.tencent.xinWeChat` and
  `~/Library/Group Containers/5A4RE8SF68.com.tencent.xinWeChat`. Three containers
  resolve to this bundle identifier.

## Inference — why the gate fails

A sandboxed app's container is resolved from its bundle identifier, and its group
container from the application-group entitlement. The copy would carry both
unchanged, so the ordinary resolution is the **production** containers -- the
live session and its data.

It is plausible that an ad-hoc signature (no TeamIdentifier) causes macOS to
reject or ignore `com.apple.application-identifier`, which could deny the launch or
redirect the container. That is precisely the kind of guess this gate forbids:
the failure modes span "refused" and "reached the real container", and only the
second is catastrophic.

## Unresolved question

Which of those outcomes actually occurs is a runtime fact that cannot be settled
without launching -- and launching is the action the gate exists to prevent.

## Smallest next design for an isolated runtime target

Give the copy a **distinct identity** so macOS resolves a different container,
and make the entitlement set consistent with that identity:

1. Rewrite `CFBundleIdentifier` in the copy to a unique per-attempt value.
2. Remove `com.apple.application-identifier` and
   `com.apple.security.application-groups` from the copy, so it cannot claim the
   production container or group.
3. Decide explicitly whether the copy runs sandboxed under its new identifier or
   unsandboxed. This is a real entitlement change beyond Capsule 2's two debug
   keys and needs its own evidence; it is not implemented here.
4. Re-sign with that reduced set plus the two debug entitlements, then verify the
   copy resolves a fresh container and cannot see the production one.

Separately worth settling before any launch: a second WeChat process shares the
account's *network* session regardless of local container isolation, so local
container isolation addresses data mutation but not session interaction. That is
a product decision, not a signing one.

---

# Capsule 4 — clone isolation probe (2026-09-20)

**Result: CLONE ISOLATION PROVEN.**

The WeChat clone was **not** launched, and was not even copied. A minimal probe
was built first, signed with the proposed identity model, and used to establish
what macOS actually does. No Frida, no attach, no process memory, no PBKDF
traffic, no database access.

## Proposed clone identity

- Bundle identifier: a unique non-production value of the form
  `org.mac-wechat-summary.bootstrap-clone.<nonce>` (the probe used
  `org.mac-wechat-summary.bootstrap-probe.v1`).
- Entitlements: `com.apple.security.app-sandbox = true`, plus the two debug keys
  from Capsule 2 (`get-task-allow`, `disable-library-validation`) when the clone is
  eventually instrumented. Sandbox retained.

## Entitlement diff (production → clone)

| Entitlement | Installed app | Clone |
|---|---|---|
| `com.apple.application-identifier` | 5A4RE8SF68.com.tencent.xinWeChat | **removed** |
| `com.apple.security.application-groups` | 5A4RE8SF68.com.tencent.xinWeChat | **removed** |
| `com.apple.security.app-sandbox` | true | **kept** |
| `temporary-exception.sbpl`, `…mach-lookup.global-name` | present | **removed** |
| keychain-access-groups | none present | none |
| debug keys | absent | added only when instrumenting |

Production identities are removed rather than replaced with production-like
guesses.

## Observed fact — probe evidence

Build, ad-hoc sign and verify:

- `clang` build exit 0; `codesign --force --sign - --options runtime` exit 0;
  `codesign --verify --strict` exit 0
- signed entitlements after signing: `['com.apple.security.app-sandbox']` only

Probe output, launched directly as its own helper-owned process:

    bundle_id=org.mac-wechat-summary.bootstrap-probe.v1
    home=/Users/…/Library/Containers/org.mac-wechat-summary.bootstrap-probe.v1/Data
    prod_app_access=0
    prod_group_access=0
    own_container_writable=1

Production-container access booleans: `~/Library/Containers/com.tencent.xinWeChat` →
**0 (denied)**; `~/Library/Group Containers/5A4RE8SF68.com.tencent.xinWeChat` →
**0 (denied)**.

## Inference

The sandbox **does** engage for an ad-hoc signed bundle, and the container is
keyed by the bundle identifier, so a non-production identifier resolves a fresh
container. This also retires the Capsule 3 uncertainty in the safe direction: an
ad-hoc signature does not have to be trusted with a production identity, because
the identity can simply be replaced. Removing the Tencent application-identifier
and application-groups entitlements is what denies access to the production
containers.

## PASS criteria

| # | Criterion | Result |
|---|---|---|
| 1 | unique non-production bundle identifier | PASS |
| 2 | no Tencent production application identifier | PASS |
| 3 | no Tencent production app-group entitlement | PASS |
| 4 | no production keychain-access-group entitlement | PASS (none present) |
| 5 | resolves to a fresh non-production container | PASS |
| 6 | cannot access either production container | PASS (both denied) |
| 7 | installed app untouched | PASS (never copied or modified) |
| 8 | no production WeChat process attached | PASS (none touched) |

## Unresolved question

The probe proves the **identity model**, not the WeChat clone itself. Applying
the same transformation to the clone — rewriting `CFBundleIdentifier` and dropping
the Tencent entitlements — still needs its own verification before any launch,
because the clone carries far more nested code and its own startup behaviour.

## Network gate for the first clone launch

The first WeChat-clone launch must be offline until local-container behaviour is
independently confirmed: the clone must not be assumed to lack a valid account
session. The smallest mechanism is to launch it with no outbound path — a
sandbox profile denying network, or an equivalent per-process restriction —
rather than relying on the entitlement set alone. Not implemented here; it is a
prerequisite of the launch capsule.

