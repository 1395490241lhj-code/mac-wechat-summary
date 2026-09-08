# H5A — access-material provider research and Rion licensing record

**Status: research only.** No WeChat database was opened, no key was extracted,
derived or requested, nothing was built, installed or executed, and the WeChat
container was not modified. Every claim below cites local mirror source or the
provider's own documentation at a named revision.

## Part 1 — who is supposed to produce the access material

Rion's own compatibility document
(`projects/rion-wechat-reader/docs/access-bundle-compatibility.md` @ `3afe33e0`)
names the formats it will import, and disclaims producing any of them: *"It does
not obtain, scan, recover, or derive database keys."* Three candidate providers
follow from it.

| # | Provider | Format | License | Local mirror |
|---|---|---|---|---|
| 1 | `huohuoer/wechat-cli` 0.2.4 → fork `maomao3334/wechat-cli-plus` | path map | Apache-2.0 | yes |
| 2 | `r266-tech/wechat-cli` v1.6.21 | schema-2 salt map | MIT | **no — DMCA'd** |
| 3 | `r266-tech/wxkey` @ `9b70eecd…` (via Rion's experimental helper) | writes `~/.config/wxcli/config.json` | MIT | yes (archived during this review) |

### 1. wechat-cli-plus — path map

- **Format, verbatim from source.** `wechat_cli/bin/find_all_keys_macos.c:292`:
  `/* Save JSON: { "rel/path.db": { "enc_key": "hex" }, ... } */`, emitted at
  line 309. Note it writes **`enc_key` only** — the salt is read from each
  database's own 16-byte header (`read_db_salt`, line 94) and used to *match*
  keys to files, not stored. `DB_READER_INTERFACE_GATE.md` describes the shape
  as `enc_key`/`salt`/`size_mb`; the extra fields are optional metadata Rion
  tolerates, not something this tool emits.
- **macOS 4.1.13.** Partial and indirect. The fork's 4.1.x commit `c3cbe6b`
  touches only `wechat_cli/keys/common.py` (HMAC-SHA1 verification for 3.9+/4.1.x).
  The macOS scanner binary is **inherited unchanged** from upstream
  (`8fc59408…`, identical blob). No macOS 4.1.13-specific test exists.
- **Local?** Yes, entirely; `grep` for socket/connect/curl/http/NSURL in the
  scanner returns **0**. Nothing leaves the machine.
- **Mechanism — invasive.** `task_for_pid` (line 127), `mach_vm_region` (187),
  `mach_vm_read` (203) against the live WeChat process. Header, verbatim:
  *"Prerequisites: WeChat must be ad-hoc signed (or SIP disabled); Must run as
  root (sudo)."*

### 2. r266-tech/wechat-cli — gone

The schema-2 salt-map source is **unavailable due to a GitHub DMCA takedown**
(verified 2026-09-08). This is the *second* DMCA-removed upstream in this chain:
R-003 already recorded `ylytdeng/wechat-decrypt`, credited as wechat-cli's
decryption basis, as removed the same way.

### 3. wxkey — the least invasive, and still not clean

Rion's helper (`skills/wechat-cli/references/experimental-access.md`) pins
`9b70eecd…` and claims it neutralises the worst behaviour: run as root through
macOS system authorization, set `WXKEY_NO_ELEVATE=1`, never pass the
original-app re-sign switch, never read the Keychain. It also states its own
stop condition: *"发现需要关闭 SIP、修改原微信或持久化密码的构建时停止本路线"* —
if a build requires SIP off, modifying the original WeChat, **or persisting a
password**, abandon the route.

At that exact pinned commit, `cmd/wxkey/main.go` lines 69–76 say, in the tool's
own usage text:

- *"SIP stays enabled"* — genuinely better than provider 1.
- *"First-time key extraction uses one route only: ad-hoc-signed WeChat + sudo
  privileges."*
- *"wxkey asks for the admin password once and **stores it in the user's macOS
  Keychain** for later unattended refreshes."*
- *"wxkey will re-launch itself through `sudo -S` when direct attach fails (set
  `WXKEY_NO_ELEVATE=1` to disable that auto-relaunch)."*
- `resign-wechat` — *"quit WeChat, ad-hoc re-sign `/Applications/WeChat.app`,
  then reopen it"*; bootstrap prefers a managed shadow copy only *"when the
  installed WeChat cannot be modified."*

So the build Rion pins **contains** two of the three conditions Rion's own doc
says should end the route: password persistence and original-app re-signing.
Rion's mitigation is real but is *invocation-shaped* — a runtime environment
variable and a choice of subcommand — and Rion labels its own reasoning
*"源码推断，不是所有发行二进制的保证"* (source inference, not a guarantee about
any released binary). Its maturity note is *"尚无新机器端到端实测"*: never tested
end to end on a fresh machine.

### Answers to the seven questions

| # | Question | Answer |
|---|---|---|
| 1 | Which tool produces the file | wechat-cli-plus (path map) or wxkey (via Rion's helper). Rion produces nothing |
| 2 | Upstream + revision | `maomao3334/wechat-cli-plus` `75a322dd…`; `r266-tech/wxkey` `9b70eecd…`; `r266-tech/wechat-cli` v1.6.21 **unavailable** |
| 3 | macOS WeChat 4.1.13 | Not established for either. Verification updated for 4.1.x; the macOS scanner is unchanged and untested at 4.1.13 |
| 4 | Exact mapping | wechat-cli-plus emits `path → {enc_key}`; salt is derived from each DB header, not stored. Rion accepts this |
| 5 | Local acquisition | Yes, both, entirely on this Mac |
| 6 | Invasive? | **Yes, unavoidably.** `task_for_pid` process-memory reads, root, and ad-hoc re-signing of WeChat (original or shadow copy) in every available route |
| 7 | Data egress | None found in either tool's acquisition path |

## Part 2 — what the Decisions actually permit

**D-005** (Active) — *"No key extraction, no process memory, no re-signing
WeChat"*: *"Reading another app's process memory and force-re-signing its bundle
are not things **this product** will do, and shipping a dependency that merely
*contains* that code is not acceptable either."* Consequence: *"The safety
boundary must be verifiable at the source and package level, **not behind a
runtime flag**."*

**D-017** (Active): *"Access-material generation stays an opaque external
prerequisite… no key extraction, process memory, or re-signing **enters this
project**."* Gate: *"real access material available"*.

Both are scoped to **the product and its dependency graph**. Neither says
anything about what the operator may do with a separately installed tool on the
same machine, and D-017's "opaque external prerequisite" presupposes the
material can arrive by means the project does not implement. **The Decisions
therefore neither permit nor forbid operator-run external acquisition — they are
silent, and this is genuine ambiguity, not an implied yes.**

Two things sharpen it rather than resolve it:

- D-005's standard — *source and package level, not behind a runtime flag* — is
  exactly the standard the wxkey route fails. Its safety depends on
  `WXKEY_NO_ELEVATE=1` and on not invoking `resign-wechat`. R-003 rejected
  `wechat-cli` for the structurally identical reason: the scanner *"ship[s] and
  remain[s] reachable even when a query command is selected."*
- Project.md lists as v2 **non-goals**: *"Decrypting the WeChat database or
  extracting keys from process memory"* and *"Modifying or re-signing
  WeChat.app"*. Both are product-scoped, but an operator performing them on the
  same Mac to feed the same product is at minimum in tension with the spirit.

### Smallest amendment required

A new Decision — call it **D-030** — is needed before any acquisition. It must
state, at minimum:

1. Operator-performed, out-of-repository acquisition with a separately installed
   external tool is permitted **for the local H5 pilot only**.
2. The repository continues to contain, ship, depend on, and require **no**
   acquisition code. D-005 stays intact as a statement about the product;
   D-030 explicitly narrows it to the product rather than the operator.
3. Named mechanism limits the operator accepts: SIP stays enabled; no
   modification or re-signing of the installed `/Applications/WeChat.app`
   (shadow copy only); no persisted admin password; no data egress.
4. An explicit acknowledgement that limits 3 are enforced **at invocation, not
   at the source level**, and that this is a conscious departure from D-005's
   stated standard — because the pinned wxkey build contains the capabilities
   it is being invoked so as to avoid.
5. Reconciliation with D-005's *"must be option C"* clause, which currently
   points at a native `WeChatDataAdapter` rather than an external reader.

I am not writing D-030. Recording a Decision that relaxes an active safety
boundary is the operator's call, and AGENTS.md forbids silently overturning one.

## Part 3 — Rion licensing risk record (engineering, not legal advice)

**Pinned revision** `3afe33e0…`; root `LICENSE` is **AGPL-3.0-only**, with
`NOTICE.md` and `COMMERCIAL-LICENSE.md` alongside. No per-project license
override exists under `projects/rion-wechat-reader` — the root license governs.
No AGPL §7 additional permissions are granted.

Upstream's own summary (`README.md:199`): you may study, run, modify, and use
commercially; obligations attach when you **distribute a modified version, or
provide a modified version to users over a network**. `COMMERCIAL-LICENSE.md`
scopes its commercial offer to closed-source integration, proprietary or
modified distribution, OEM/white-label.

Against the architecture actually used:

| Trigger | Our pilot | Assessment |
|---|---|---|
| Distribution / conveying | Not vendored, bundled, downloaded by us, or shipped; operator installs it themselves | Not triggered |
| Modification | Verbatim `git show` export at a pinned hash; the only authored file is a two-line `exec` wrapper, a separate file | Not triggered |
| Linking / derivative | Subprocess over a documented JSON CLI contract; `rion_reader_adapter.py` imports only stdlib + the project's own `message_source` | No linkage |
| AGPL §13 network provision | Rion runs as a local subprocess of a local bridge; nothing exposes it over a network | Not triggered **today** |

**Conclusion for the pilot:** nothing in the local-only architecture as
exercised requires redistributing or modifying Rion, so the AGPL obligations
that attach to those acts have no occasion to arise. This is an engineering
observation about what the code does.

**Explicitly unresolved, and it must stay that way:** if the bridge or an MCP
surface over it is ever exposed remotely, §13 needs re-analysis; any product
that ships, bundles, or distributes Rion needs a separate analysis; and none of
this is a legal conclusion or a substitute for review by someone qualified to
give one. A Decision may mark the **local-only pilot** as permitted while
leaving redistribution and product licensing open — the two questions are
genuinely separable, and only the first is answered here.

## Part 4 — preparation done, and what is still required

Prepared without touching WeChat:

- `r266-tech/wxkey` archived as a bare mirror + verified bundle beside the
  existing five repositories, with the Rion-pinned commit confirmed present.
  **Not built, not installed, not executed.**
- The DMCA unavailability of `r266-tech/wechat-cli` recorded in the manifest.
- The pinned Rion reader re-verified offline: `3afe33e0…`,
  `rion_wechat_reader.py` sha256 `ad0ec2066f…`, the B1-accepted hash.

**Still required, and it is not only consent.** Two things, in order:

1. **A Decision (D-030) permitting operator-run external acquisition**, with the
   mechanism limits and the explicit acknowledgement that they are enforced at
   invocation rather than at source level. This relaxes an active D-005
   boundary and is the operator's decision to record.
2. **Consent to run the chosen provider**, with its real side effects: an admin
   authorization prompt, reading the live WeChat process's memory, quitting and
   restarting WeChat, and ad-hoc re-signing a copy of the app.

Neither the licensing review nor the provider research is what blocks H5A now.
The block is that every available provider requires precisely the mechanism the
project's active Decisions were written to exclude.
