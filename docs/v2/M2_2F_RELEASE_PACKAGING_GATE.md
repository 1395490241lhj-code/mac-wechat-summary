# M2.2f — Release Packaging / Signing / Notarization Gate

**Sealed:** 2026-09-07 · **Branch:** `v2/rewrite` · **Builds on:** `753d2bd` (M2.2e)
**Scope:** packaging and release only. No memory semantics, no tool changes,
`SKILL.md` untouched, no entitlements added, hardened runtime kept, no real
WeChat data, nothing pushed.

## Verdict

```
RELEASE SIGNING   (Release configuration, hardened runtime, nested helper) : PASS
DISTRIBUTION SIGNING (Developer ID Application)                            : NOT RUN
NOTARIZATION                                                               : NOT RUN
STAPLING                                                                   : NOT RUN
GATEKEEPER                                                                 : NOT RUN
```

The three NOT RUN layers share **one** cause: this machine has no **Developer
ID Application** identity and no notarization credential. That is an operator
prerequisite, not a product failure, and nothing was changed to work around it.

---

## 1. Preflight (presence and capability only)

| | |
|---|---|
| Codesigning identities | **1** — `Apple Development` (Team `5M5KT5ZG74`) |
| Developer ID Application identities | **0** |
| "Developer ID" certificates found | 2 — both `Developer ID Certification Authority`, i.e. Apple's **intermediate CA**, not a leaf signing certificate |
| notarytool credential profile | **none** (no `com.apple.gke.notary.tool` keychain item) |
| `xcrun notarytool` | present |
| Host bundle id | `com.lianghongjing.WeChatCompanion` |
| Hardened runtime | `ENABLE_HARDENED_RUNTIME = YES` in **both** Debug and Release |
| Entitlements | **none** — no `CODE_SIGN_ENTITLEMENTS`, no `.entitlements` file |

No certificate material, password, Apple ID, or keychain secret was read or
printed.

## 2. A real finding: the nested bundle identifier

The nested helper's `CFBundleIdentifier` was **`MemoryWorker`** — PyInstaller's
default, which is the bare product name. It is neither reverse-DNS nor
plausibly unique, and it also becomes the *code-signing* identifier. A
notarization service is entitled to reject that, so it would have been a
blocker discovered at the worst moment.

Fixed in the pinned build: `--osx-bundle-identifier
com.lianghongjing.WeChatCompanion.MemoryWorker`, and the build **verifies** the
resulting plist rather than trusting the flag. A regression test asserts both.
This is the only substantive change in the phase.

## 3. Release bundle structure

```
WeChat Companion.app/Contents/
  Info.plist  MacOS/  PkgInfo  _CodeSignature/
  Helpers/MemoryWorker.app/Contents/
    Info.plist  MacOS/  Frameworks/  Resources/  _CodeSignature/
```

| Check | Result |
|---|---|
| nested bundle identifier | `com.lianghongjing.WeChatCompanion.MemoryWorker` — unique, reverse-DNS |
| nested `CFBundleExecutable` | `MemoryWorker`, present at `Contents/MacOS/MemoryWorker` |
| `LSBackgroundOnly` | `true` — a helper, not a second app in the Dock |
| signing order | nested Mach-O → `Python.framework` → helper bundle → (Xcode) host |
| Team identifiers | `5M5KT5ZG74` on host **and** helper |
| hardened runtime | `flags=0x10000(runtime)` on host **and** helper |

## 4. Deterministic Release build

The embed phase is Debug-lenient and Release-strict, and that was tested both
ways:

- worker removed → `xcodebuild -configuration Release` **fails**:
  `error: Release expects a bundled memory worker. Run scripts/build-memory-worker.sh first.`
- worker present → build **succeeds**, and the phase logs that it signed the
  helper with the app's own identity (not ad-hoc).

Pins are unchanged and enforced: PyInstaller **6.11.1**, CPython **3.12**
(another minor is refused), `--onedir --windowed`, and an `env -i` probe that
fails the build if the result is not self-contained.

## 5. Code-sign verification (Release artifact)

| Check | Result |
|---|---|
| `codesign --verify --deep --strict` (host) | **valid on disk**, **satisfies its Designated Requirement**; nested helper explicitly `--validated` |
| same on the nested helper alone | valid, satisfies its DR |
| explicit requirement `anchor apple generic` | satisfied |
| Mach-O files scanned inside the app | **52** |
| unsigned | **0** |
| ad-hoc | **0** |
| outside a signed nested bundle or the host's `MacOS/` | **0** |

Authority chain: `Apple Development: … (XM589WPY95)` → `Apple Worldwide
Developer Relations Certification Authority` → `Apple Root CA`.

## 6. Runtime smoke after Release signing

Run directly from inside the signed Release bundle, with `env -i` and an
isolated `HOME`:

| Check | Result |
|---|---|
| launches under hardened runtime | yes — no library-validation failure |
| structured protocol | `{"op":"paths"}` answers correctly |
| system Python dependency | **none** — 0 links to a system `Python.framework`/`libpython` |
| network | none required; the worker imports no network module |
| private path emitted | none — only the home-relative form |
| synthetic sync (both paths derived) | **1 inserted**, then **0 inserted / 1 updated** — idempotent |
| store permissions | `0600` |

*(A first attempt reported `database_missing`; that was a fixture-ordering
mistake in the harness shell, confirmed by re-running with the fixture
verified. It is recorded because the difference between a product defect and a
test defect is exactly what a gate is for.)*

## 7. Notarization, stapling, Gatekeeper

**NOTARIZATION: NOT RUN.** Submission requires a Developer ID Application
identity and a notarytool credential; neither exists here. Nothing was
submitted, so nothing is claimed. The credential-free half was still
exercised: the distributable artifact was produced with
`ditto -c -k --keepParent` (**9.4 MB** zip).

**STAPLING: NOT RUN.** `xcrun stapler validate` on the Release app:
*"does not have a ticket stapled to it"* — correct, there is no ticket.

**GATEKEEPER: NOT RUN** as a meaningful distribution check. `spctl --assess
--type execute` was run and returned **rejected**, which is the *expected*
answer for an app signed with an Apple Development certificate and not
notarized. It does not evaluate the nested-helper architecture, so it is
recorded as observed and not counted either way.

### Operator prerequisites to finish these layers

1. A **Developer ID Application** certificate for Team `5M5KT5ZG74` in the
   login keychain.
2. A notarization credential profile:
   `xcrun notarytool store-credentials --apple-id … --team-id 5M5KT5ZG74`
   (run by the operator; this session neither asks for nor stores secrets).

Then: build Release, zip, `notarytool submit --wait`, `stapler staple`,
`spctl --assess`.

## 8. Is the nested-helper architecture release-viable?

Every structural property notarization is known to check now holds under
Release signing: a valid unique reverse-DNS nested bundle identifier, hardened
runtime on host and helper, one Team ID throughout, no unsigned or ad-hoc code
anywhere in 52 Mach-O files, no loose code outside a signed nested bundle, and
no entitlement weakening library validation.

**That is a strong indication, not proof.** Apple's service can reject for
reasons only it evaluates, and no submission was made. The honest statement is:
*nothing known to block notarization remains, and the remaining risk is
unmeasured.*

## 9. Security posture — unchanged

| | |
|---|---|
| hardened runtime | kept, host and helper |
| `disable-library-validation` | **not** added (asserted by test) |
| any entitlement | **none** exist (asserted by test) |
| unsigned nested executable | none |
| ad-hoc signature in the release artifact | none |
| system-Python dependency | none |
| runtime download | none |
| `PATH` lookup for the worker | none — fixed bundle-relative path |

## 10. Product regression — none

Canonical store path, Sync Now through the bundled worker, consent behaviour,
reader fail-closed rules and the read-only memory MCP are untouched. Default
tool set **exactly 4**; memory-enabled **exactly 9**
(`test_memory_activation.py`: 36 passed). No live Claude run was needed: no
runner or tool-boundary code changed.

## 11. Sizes

| | Debug (M2.2d) | Release (M2.2f) |
|---|---|---|
| app total | 27.8 MB | **25.3 MB** |
| base app | 6.2 MB | **3.7 MB** |
| bundled worker | 21.6 MB | 21.6 MB |
| distributable zip | — | **9.4 MB** |

The worker dominates and is unchanged; the Release base is smaller than Debug.
No size optimisation was attempted — packaging is not blocked by size.

## 12. Test safety

The M2.2e guard is intact: every Python test runs with an isolated `HOME`, and
the session fixture asserts the real store is untouched. All packaging work
used scratch directories. The user's
`~/Library/Application Support/WeChatCompanion/memory.sqlite` **remained absent
throughout**, matching its pre-test state.

## 13. Gates

| Suite | M2.2e | M2.2f |
|---|---|---|
| `memory/` | 330 | **337** (+7 packaging contract) |
| `bridge/` | 72 | **72** unchanged |
| `shadow/` | 138 | **138** unchanged |
| Swift | 206 / 16 | **206 / 16** unchanged |

## 14. Does this unblock M2.3?

**Yes for skill integration.** M2.3 concerns what the skill teaches an agent
about memory; it does not depend on distribution signing. The remaining
packaging work is a distribution prerequisite for *shipping*, not for the skill
work, and is now a single well-defined operator step rather than an unknown.
