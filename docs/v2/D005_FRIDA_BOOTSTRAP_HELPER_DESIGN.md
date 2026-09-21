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

---

# Capsule 5 — clone transform and first launch (2026-09-20)

**Result: CLONE COMPONENT ISOLATION UNPROVEN. The clone was not built, signed or
launched.**

The capsule's component gate must be cleared before any transform or launch, and
it cannot be. No clone was created, nothing was launched, no Frida, no attach, no
memory read, no PBKDF traffic, no database access, and no Bootstrap production
code changed.

## Observed fact — nested identity audit

The installed bundle contains **48 nested bundles** carrying a `CFBundleIdentifier`.
**41 of them carry a production Tencent identity**, including:

| Component | Bundle identifier |
|---|---|
| main app | `com.tencent.xinWeChat` |
| Chromium helper app | `com.tencent.flue.WeChatAppEx` |
| Sparkle XPC service | `com.tencent.xinWeChat.InstallerLauncher` |
| framework resource bundles | `com.tencent.ConfSDK`, `com.tencent.MultiMedia`, `com.tencent.ProtobufLite`, `com.tencent.wc.mp.andromeda-dylib`, `com.tencent.ilink2`, `com.tencent.ilinkstream`, `com.tencent.xwechat.wcdywrapper`, `com.tencent.owl`, `com.tencent.wechat.roam.migration`, `com.tencent.wechat.roam.server`, `com.tencent.usb` |

`Contents/XPCServices` also exists and holds one entry. The launcher loads
`libwxld.dylib` through `rpath`, so the clone's own dependency graph is internal to
the bundle rather than system-provided.

## Inference — why the gate fails

Capsule 4 proved the identity model on a probe with **one** identity. The real
clone has **41**, and several are not decorative:

- the XPC service identifier is how the parent addresses that service, so
  rewriting it requires rewriting the parent's reference too;
- the Chromium helper's identifier is how the Chromium framework launches and
  addresses its child processes;
- the framework resource-bundle identifiers are referenced from the frameworks'
  own code.

The capsule explicitly forbids rewriting nested identifiers blindly when doing so
could break internal XPC addressing. Proving that all 41 can be rewritten safely,
and rewriting their cross-references, is a design task in its own right — not a
mechanical transform — and it cannot be shown correct by inspection at this
level.

A second, separate concern: the installed app carries
`temporary-exception.mach-lookup.global-name`, which names global Mach services. If any
launchable component registers or looks those up, container isolation alone would
not isolate it. That was not established either.

## Unresolved question

Whether the 41 nested identities can be rewritten while preserving XPC and
Chromium child-process addressing — and whether any component structurally
requires its production identity. Both need a dedicated component-graph design.

## Stop

Per the capsule: **CLONE COMPONENT ISOLATION UNPROVEN**, failing component set
= the 41 nested bundles above, with the exact dependencies named for the XPC
service and the Chromium helper. Isolation was not weakened to force progress,
and no transform was attempted.

## Smallest possible design change

A component-graph capsule that, for each of the 41: records its identifier,
entitlements and designated requirement; classifies it launchable or resource;
identifies every cross-reference to its identifier from other components; and
proposes a unique non-production replacement plus the matching reference edits —
or, where an identifier is structurally required, records that explicitly and
scopes the clone to a subset that can be isolated. Only then is a transform and a
network-isolation probe meaningful.

---

# Capsule 5A — minimal executable identity graph (2026-09-20)

**Result: MINIMAL CLONE GRAPH ISOLATABLE.**

Static analysis only. No clone was created, copied, signed or launched; no Frida,
no attach, no memory, DB or key access; no Bootstrap production code changed.

## Observed fact — the identity surface is much smaller than 41

Of **48 nested bundles**, only **14 are launchable**; the other **34 are
resource-only** and grant nothing, so a `com.tencent.*` identifier on one of them is
not an isolation boundary.

Of the 14 launchable: **13 carry production Tencent identities**, **11 are
sandboxed**, **only 1 carries `com.apple.application-identifier`** (the main app),
**3 carry `application-groups`**, and **3 carry `network.client`**.

| Launchable component | Identity bits |
|---|---|
| `com.tencent.xinWeChat` (main) | sandbox, appid, groups, net, mach |
| `com.tencent.flue.WeChatAppEx` (Chromium shell) | sandbox |
| `com.tencent.flue.WeApp`, `.helper` x2, `.helper.plugin`, `.helper.renderer` | sandbox |
| `com.tencent.xinWeChat.WeChatHelper` | sandbox |
| `com.tencent.xinWeChat.xplayer` | sandbox |
| `com.tencent.xinWeChat.WeChatMacShare` | sandbox, groups, net |
| `com.tencent.xinWeChat.WeChatFileProviderExtension` | sandbox, groups, net |
| `com.tencent.xWechat.DebugHelper` (XPC) | none |
| `com.tencent.xinWeChat.InstallerLauncher` (XPC) | none |
| `org.sparkle-project.Sparkle.Updater` | none -- third party, not Tencent |

## Observed fact — which components can actually reach production containers

Production containers exist for **three** of these identities:

- `~/Library/Containers/com.tencent.xinWeChat`
- `~/Library/Containers/com.tencent.xinWeChat.WeChatMacShare`
- `~/Library/Containers/com.tencent.xinWeChat.WeChatFileProviderExtension`

plus **one** production group container under `5A4RE8SF68.*`. The other ten
launchable components have no existing container, so keeping their identifiers
would create clone-owned containers rather than reach production ones.

## Inference — answers to the critical questions

1. **How many of the 41 are launchable?** 13 of 14 launchable bundles; 34 of the
   41 are resource-only.
2. **How many possess sandbox/container entitlements?** 11 are sandboxed; only the
   main app holds `application-identifier`; 3 hold `application-groups`.
3. **Which identities are runtime-addressed by another executable?** The Chromium
   helper identifiers are addressed by the Chromium framework, and the two XPC
   service identifiers by their parent; both need their references carried in the
   transform. Resource-bundle identifiers are not runtime-addressed.
4. **Mach services:** the installed app carries
   `temporary-exception.mach-lookup.global-name` (lookup, not registration) on the main
   executable only. It must be dropped, and any lookup the clone performs will
   then fail closed, which is the desired direction.
5. **Is any production identity inherently required?** Not for container
   isolation. The three container-bearing components plus the group entitlement
   are the whole production-access surface; nothing in the graph requires
   reaching the installed WeChat environment to launch.
6. **Can the minimal graph be isolated by coordinated renaming?** Yes, without
   touching the 34 resource-only identifiers.

## Minimal transform set

| Component | Old identity | Proposed new identity | References to change | Entitlements |
|---|---|---|---|---|
| main app | com.tencent.xinWeChat | org.mac-wechat-summary.bootstrap-clone.NONCE | Info.plist only | drop appid, groups, mach, net; keep sandbox |
| Chromium shell | com.tencent.flue.WeChatAppEx | clone.NONCE.appex | Chromium framework helper ids | keep sandbox; drop net |
| Chromium helpers x5 | com.tencent.flue.* | clone.NONCE.helper* | referenced by the shell | keep sandbox |
| WeChatHelper | com.tencent.xinWeChat.WeChatHelper | clone.NONCE.helper | main app launch reference | keep sandbox |
| xplayer | com.tencent.xinWeChat.xplayer | clone.NONCE.xplayer | main app launch reference | keep sandbox |
| WeChatMacShare | com.tencent.xinWeChat.WeChatMacShare | clone.NONCE.share | extension registration | drop groups, net |
| FileProviderExtension | com.tencent.xinWeChat.WeChatFileProviderExtension | clone.NONCE.fileprovider | extension registration | drop groups, net |
| DebugHelper XPC | com.tencent.xWechat.DebugHelper | clone.NONCE.debughelper | parent XPC reference | none |
| InstallerLauncher XPC | com.tencent.xinWeChat.InstallerLauncher | clone.NONCE.installer | parent XPC reference | none |
| Sparkle Updater | org.sparkle-project.Sparkle.Updater | unchanged | -- | third party; not production WeChat |

## Unresolved question

Whether the Chromium framework tolerates renamed helper bundle identifiers is not
provable by inspection at this level; it is the one reference in the set that
needs the transform to carry it, and the first launch is what confirms it. The
network gate from Capsule 4 still applies to that launch.

---

# Capsule 5B — minimal-graph transform, no launch (2026-09-20)

**Result: gate 11 FAILS. TRANSFORMED CLONE STATICALLY VERIFIED is not claimed.**

Nothing was launched, and the disposable clone was removed after evidence
collection, as the capsule requires on failure. No Frida, no attach, no memory,
DB or key access, no Bootstrap production code changed.

## Observed fact — what the transform achieved

The clone was copied into a disposable workspace and the 13 launchable Tencent
identities were rewritten under `org.mac-wechat-summary.bootstrap-clone.*`:

| Old identity | New identity |
|---|---|
| com.tencent.xinWeChat | …clone.main |
| com.tencent.flue.WeChatAppEx | …clone.shell |
| com.tencent.flue.WeApp | …clone.shell.WeApp |
| com.tencent.flue.helper (x2) | …clone.shell.helper |
| com.tencent.flue.helper.plugin | …clone.shell.helper.plugin |
| com.tencent.flue.helper.renderer | …clone.shell.helper.renderer |
| com.tencent.xinWeChat.WeChatHelper | …clone.main.wechathelper |
| com.tencent.xinWeChat.xplayer | …clone.main.xplayer |
| com.tencent.xinWeChat.WeChatMacShare | …clone.main.wechatmacshare |
| com.tencent.xinWeChat.WeChatFileProviderExtension | …clone.main.wechatfileproviderextension |
| com.tencent.xWechat.DebugHelper | …clone.main.debughelper |
| com.tencent.xinWeChat.InstallerLauncher | …clone.main.installerlauncher |

Entitlements were reduced per component by dropping
`com.apple.application-identifier`, `application-groups`, `keychain-access-groups`,
`network.client`, `network.server` and both `temporary-exception.*` entries, keeping
`app-sandbox` and the non-container entitlements.

The 34 resource-only bundle identifiers were left untouched, as Capsule 5A
directed.

## Observed fact — the independent residual search is clean

Re-searching the transformed bundle after signing, independently of the transform
manifest: **0 residual findings**. No launchable component retains a
`com.tencent.*` or `5A4RE8SF68.*` identifier, and none retains any dropped
entitlement.

## Observed fact — recursive verification fails

`codesign --verify --deep --strict` on the transformed clone returns **exit 1**:

    WeChat.app: nested code is modified or invalid
    In subcomponent: …/WeChat.app/Contents/MacOS/WeChatAppEx.app/Contents/Frameworks/WeChatAppEx Framework.framework

The whole executable graph was re-signed deepest-first (15 nested code objects,
all successful) and then the outer bundle (exit 0), so the failing object is the
Chromium framework's own seal over the helper applications it contains.

## Inference — why, and what it means

Renaming the Chromium helpers necessarily changes their code hashes, so the
enclosing framework's seal must be recomputed after them; that ordering was
applied and the framework re-signed successfully, yet the deep verification still
rejects it. The likely cause is that the Chromium framework seals its nested
`Frameworks` directory in a way this signing pass does not reproduce exactly —
for example additional nested code beneath the helper applications that was not
enumerated as a bundle, or `codesign`'s seal semantics for a framework that
contains applications.

This is the transform step the capsule anticipated as the risky one. It is a
signing-mechanics problem, not an isolation problem: the identity and entitlement
work is complete and the residual search confirms it.

## Stop

Per the capsule: the failing gate is **11 — recursive code-sign verification**,
at subcomponent `Contents/MacOS/WeChatAppEx.app/Contents/Frameworks/WeChatAppEx Framework.framework`.
Isolation was not weakened to make it pass, and the clone was removed.

## Installed app

`Contents/MacOS/WeChat` and `Contents/Resources/wechat.dylib` were hashed before and
after and are byte-identical.

## Smallest next step

Enumerate *every* nested Mach-O under the Chromium framework — including code
that carries no bundle identifier — and re-sign in strict depth order, or accept
that the Chromium shell cannot be re-sealed in place and scope the clone to the
non-Chromium components, which Capsule 5A showed carry none of the three
production containers.

---

# Capsule 5B.1 — recursive code-sign seal, retry (2026-09-20)

**Result: TRANSFORMED CLONE STATICALLY VERIFIED.**

The earlier 5B.1 attempt was invalid and is discarded, not repaired: its
enumerator pruned traversal at the first bundle it met and reported
`signable_bundles 1`. The code-signing graph was rebuilt independently of the
bundle-identity graph.

Nothing was launched. No clone, helper, XPC service or extension was started; no
Frida was installed, run or attached; no process was attached; no process memory,
key material or database was read; no Bootstrap production code changed. The
approved 13-component identity mapping and the approved entitlement reduction are
the Capsule 5B design, unchanged.

## Enumeration — the code-signing graph, built independently of the identity graph

The whole clone tree was walked with nested bundle subtrees never pruned.

| | Count |
|---|---|
| signable objects | **132** |
| bundles (root app + 47 nested) | **48** |
| raw Mach-O (executables, dylibs, versioned framework binaries, `.node`) | **85** |
| bundle types | 34 framework, 9 app, 2 xpc, 2 appex, plus the root app |
| maximum object depth | 12 |

- Symlinks are canonicalised, so each physical object is signed once: 168 alias
  paths collapse onto 132 objects, and 36 objects are reachable by more than one
  path (`Versions/Current`, and framework-root binary aliases such as
  `…/ConfSDKdyn.framework/ConfSDKdyn` → `…/Versions/A/ConfSDKdyn`).
- The nested objects beneath the two named subtrees are explicitly included.
  `WeChatAppEx.app` contributes **43** objects (16 bundles, 27 Mach-O);
  `WeChatAppEx Framework.framework` contributes **41** objects (15 bundles,
  26 Mach-O), to depth 12.

Two independent completeness checks:

1. `codesign --verify --deep --strict --verbose=4` on the installed app walks 55
   distinct paths. Every one is either an enumerated object or the same framework
   seen through its `Versions/Current` versioned-directory alias. The single
   exception is `XPlayer.app/Contents/Frameworks/vk_swiftshader_icd.json`, a JSON
   file that `codesign` classifies as nested code by directory rule but which
   carries no code and no signature.
2. `file(1)` over all 406 real files reports exactly **85** Mach-O, with a
   symmetric difference of **0** against the enumeration and no `file` errors.

## Phase 1 — diagnosis before repair

On the installed app all 132 objects pass `codesign --verify --strict`
individually, so the enumeration contains no unsigned object.

After the identity and entitlement transform and **before** any re-sign, 29 of
132 objects fail. The deepest initial failures are at **depth 12**:

| object | depth | type | result | diagnostic |
|---|---|---|---|---|
| `…/WeChatAppEx Framework.framework/Versions/C/Helpers/WeChatAppEx Helper.app/Contents/MacOS/WeChatAppEx Helper` | 12 | Mach-O | FAIL | `invalid Info.plist (plist or signature have been modified)` |
| the same for `WeChatAppEx Helper (GPU)`, `(Plugin)`, `(Renderer)` and `WeApp` | 12 | Mach-O | FAIL | same |
| `…/Sparkle.framework/Versions/B/XPCServices/Installer.xpc/Contents/MacOS/Installer` | 9 | Mach-O | FAIL | same |

The Chromium framework's own failure at that moment is a **consequence** of those
children, not an independent fault, so diagnosis did not stop at the framework.

## Proven root cause of the Capsule 5B failure

Reproduced exactly. Signing the transform's own object set — the 13
identity-rewritten bundles plus the outer app — **without re-sealing the enclosing
bundles that contain them** reproduces Capsule 5B's error byte for byte:

```
WeChat.app: nested code is modified or invalid
In subcomponent: …/WeChat.app/Contents/MacOS/WeChatAppEx.app/Contents/Frameworks/WeChatAppEx Framework.framework
exit 1
```

The signing set came from the **bundle-identity** graph. That graph contains the
five Chromium helper apps but not `WeChatAppEx Framework.framework`, which is a
framework and not a launchable bundle, so the framework's seal over its children
was never recomputed after they changed. `Sparkle.framework` and its
`Installer.xpc` fail the same way. The correction is not a different signing
recipe but deriving the order from **containment**.

## Corrected signing order — containment, deepest first

Sign set = every changed object plus every ancestor of it. 15 objects, all
`exit 0`:

1. the five Chromium helper apps (`Helpers/*.app`, depth 9)
2. `Sparkle.framework/…/Installer.xpc` (depth 6)
3. `WeChatAppEx Framework.framework` (depth 5)
4. `WeChatAppEx.app`, `WeChatHelper.app`, `XPlayer.app`, `WeChatMacShare.appex`,
   `DebugHelper.xpc` (depth 2)
5. `Sparkle.framework` (depth 2)
6. `WeChatFileProviderExtension.appex` (depth 2)
7. outer `WeChat.app`

No object inside an already-signed parent was mutated afterwards.

The nine nested frameworks under the Chromium framework's `Frameworks/` and the
dylibs under its `Libraries/` were not modified by the transform and needed no
re-sign: the nested frameworks are sealed by the framework as nested code, and the
`Libraries/` dylibs are sealed by **content hash** — `codesign`'s own `rules2` for
this framework does not classify `Libraries/` as nested (`Frameworks/`, `Helpers/`,
`MacOS/`, `XPCServices/` and `PlugIns/` are; `Libraries/` is not). They were
enumerated and verified regardless.

## Phase 3 — verification

| # | Check | Result |
|---|---|---|
| 1 | every discovered signable object, `codesign --verify --strict` | **132 / 132 PASS** |
| 2 | `WeChatAppEx Framework.framework` strict | exit 0 |
| 3 | `WeChatAppEx Framework.framework` deep+strict | exit 0 |
| 4 | `WeChatAppEx.app` strict and deep+strict | exit 0 |
| 5 | outer clone strict | exit 0 |
| 6 | outer clone deep+strict | exit 0 |
| 7 | residual production identities on clone-owned components | **0** |
| 8 | residual dropped entitlements on clone-owned components | **0** |
| 9 | installed production app | unchanged (below) |

The framework's seal shape is preserved: `Sealed Resources version=2 rules=13
files=45`, identical to the installed framework, with the same 45 sealed entries
and the same rule set.

## Residual isolation

The residual search was re-run independently of the transform manifest, over all
48 bundles. No clone-owned component retains a `com.tencent.*` or `5A4RE8SF68.*`
identifier, and none retains `application-identifier`, `application-groups`,
`keychain-access-groups`, `network.client`, `network.server` or either
`temporary-exception.*` entitlement. The 34 resource-only identifiers are
untouched, as Capsule 5A directed.

**New observation, reported not changed.** Three `Info.plist` files still carry
the literal production team id, and one names the production app group:

| file | key | value |
|---|---|---|
| outer app, `WeChatMacShare.appex`, `WeChatFileProviderExtension.appex` | `TeamIdentifier` | `5A4RE8SF68.` |
| `WeChatFileProviderExtension.appex` | `NSExtension.NSExtensionFileProviderDocumentGroup` | `5A4RE8SF68.com.tencent.xinWeChat` |

Both are present identically in the installed app, so neither is a transform
regression; neither is an identifier or an entitlement, so both are outside the
approved 13-component transform. The extension no longer carries
`application-groups`, so the group binding cannot resolve — Capsule 4's probe
established that removing that entitlement is what denies the production group
container. Flagged for the launch capsule: whether
`NSExtensionFileProviderDocumentGroup` should be neutralised before any launch.

## Signature fidelity — for the launch capsule

Local ad-hoc signing differs from Tencent's Developer ID signature in ways that do
not affect verification: ad-hoc with `TeamIdentifier=not set`, no CMS blob and no
timestamp, a sha256-only CodeDirectory where the original carries sha1 and sha256,
and `codesign` chose a 16 KB hash page size for the x86_64 slice where the original
used 4 KB (the arm64 slice is 4 KB in both). Hardened runtime is preserved:
`flags=0x10002(adhoc,runtime)`.

## Installed app

`Contents/MacOS/WeChat` sha256 `b21aeae5c3e4d570…` and
`Contents/Resources/wechat.dylib` sha256 `2af9442379888ee4…` are byte-identical
before and after, and `codesign --verify --deep --strict /Applications/WeChat.app`
is still exit 0.

Two live-environment notes: the operator's ordinary WeChat session was already
running for the whole pass and was never touched, and WeChat's own Sparkle updater
was running concurrently (not ours). Both production hashes were re-checked after
all work, and the app bundle's mtime is still `Sep 15 04:24:37`.

## Disposable artifacts

The clone trees — the transformed working clone and the reproduction copy — were
removed after evidence collection; nothing from them was committed. The disposable
tooling and the raw evidence JSONs remain outside the repository under
`/tmp/mws-d005-5b1-retry`: the enumerator, the per-object verifier, the transform
manifest, the signing log and the Phase 1 diagnostics. The only repository change
is this document.

---

# Capsule 5B.2 — File Provider document-group neutralisation (2026-09-20)

**Result: CLONE PRE-LAUNCH METADATA VERIFIED.**

Starting checkpoint `25b99ec`, pushed to `origin/feature/hermes-validation-isolation`
before this work (0/0). The Capsule 5B.1 signing architecture and the approved
13-component identity design are unchanged. Nothing was launched; no Frida was
installed or run; no process was attached; no DB or key material was touched; no
Bootstrap production code changed.

## The one runtime reference, and its treatment

`WeChatFileProviderExtension.appex` declared
`NSExtension.NSExtensionFileProviderDocumentGroup = 5A4RE8SF68.com.tencent.xinWeChat`.
The value is now:

    org.mac-wechat-summary.bootstrap-clone.5b1retry.main.wechatfileproviderextension.docgroup

The rest of the `NSExtension` block is unchanged:
`NSExtensionPointIdentifier = com.apple.fileprovider-nonui`,
`NSExtensionPrincipalClass = FileProviderExtension`,
`NSExtensionFileProviderSupportsEnumeration = true`. No `application-groups`
entitlement was added; the extension's entitlements remain exactly
`com.apple.security.app-sandbox` and
`com.apple.security.files.user-selected.read-write`.

## Classification of the three `TeamIdentifier` values

| file | value | classification | treatment |
|---|---|---|---|
| outer app | `5A4RE8SF68.` | informational / application-owned metadata | unchanged |
| `WeChatMacShare.appex` | `5A4RE8SF68.` | informational / application-owned metadata | unchanged |
| `WeChatFileProviderExtension.appex` | `5A4RE8SF68.` | informational / application-owned metadata | unchanged |

`TeamIdentifier` is not an Apple `Info.plist` key: team identity is read from the
code signature, or from `com.apple.developer.team-identifier` in signed
entitlements. Nothing in container, app-group, keychain-group or sandbox resolution
consults an `Info.plist` key of that name, and all three values are identical to the
installed app, so there is no evidence any of them grants or selects production
access. Left unchanged, as directed; no broad string replacement was performed.

## Scope of the transform

Every `Info.plist` in the clone was compared against its installed counterpart: 35
of 48 are byte-identical, and the 13 that differ contain exactly **14** changed
keys — the 13 approved `CFBundleIdentifier` values plus the single document-group
value above. Nothing else moved.

## Rebuild and seal

The disposable clone was recreated from `/Applications/WeChat.app`; the approved
identity map and entitlement reduction were applied unchanged, plus the single
metadata edit. The no-pruning enumeration still resolves to **132** signable
objects (48 bundles — root app plus 47 nested — and 85 raw Mach-O), so the metadata
edit does not change the object graph. The proven deepest-first containment closure
re-signed the same **15** objects, all `exit 0`.

## Gates

| # | Gate | Result |
|---|---|---|
| 1 | 132-object enumeration complete | PASS (132: 47 nested bundles + 85 raw Mach-O) |
| 2 | every signable object strict | PASS 132/132 |
| 3 | Chromium framework deep+strict | PASS exit 0 |
| 4 | `WeChatAppEx.app` deep+strict | PASS exit 0 |
| 5 | outer clone deep+strict | PASS exit 0 |
| 6 | zero production application identifiers | PASS (0 `application-identifier` entitlements; 0 production ids on identity-graph components) |
| 7 | zero app-group / keychain-group entitlements | PASS (0) |
| 8 | zero production File Provider document-group references | PASS (0) |
| 9 | production app unchanged | PASS |
| 10 | nothing launched | PASS |

## Independent residual search

Re-run after signing, independently of the transform manifest. The production group
string `5A4RE8SF68.com.tencent.xinWeChat` now appears in **0 files** anywhere in the
clone. In the installed app it appeared in four places — three binaries, inside
their code-signature entitlement blobs, and the extension `Info.plist` — and all
four are gone: re-signing with the reduced entitlement set removes the
signature-blob occurrences, and the metadata edit removes the plist occurrence.

## Production app

`Contents/MacOS/WeChat` sha256 `b21aeae5c3e4d570…` and
`Contents/Resources/wechat.dylib` sha256 `2af9442379888ee4…` are byte-identical
before and after; `codesign --verify --deep --strict /Applications/WeChat.app` is
still exit 0. The operator's ordinary WeChat session remained the only WeChat
process set running, all of it launched from `/Applications/WeChat.app`; no clone
or component process exists.

## Disposable artifacts

Clone trees removed after evidence collection; the tooling and evidence JSONs remain
under `/tmp/mws-d005-5b1-retry` outside the repository. The only repository change
is this document.
