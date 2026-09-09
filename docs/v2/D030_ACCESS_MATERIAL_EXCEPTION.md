# D-030 — ACTIVE (recorded 2026-09-08)

**This file is the working record. The Decision itself is recorded in the
vault's `Decisions.md`.**

D-030 was approved by the operator on 2026-09-08 with a final amendment: the
dangerous conditions become **explicit preflight refusal gates**, checked and
required to pass *before* execution, in addition to — not instead of — the
post-hoc verification already specified.

**Activating D-030 authorizes no operational action.** It records that a
category of action *may* be permitted, under stated gates, once separately
consented to. It is not an instruction, a schedule, or a trigger. Nothing may be
installed, built, executed, or prepared on the strength of this Decision alone.

**Status of execution: nothing has been done.** wxkey is not installed, not
built, not run. No WeChat process, memory, database, key, Keychain item, or
application bundle has been accessed. Nothing has been re-signed. Approval of
D-030 is not consent to execute acquisition; that consent is a separate,
per-occasion act and has not been given.

### Traceability — the seven agreed amendments

Each amendment the operator required, and where it landed. The A/B/C structure
is used because three of the seven are not preflight conditions: two are
standing requirements about artifacts, and one is about the meaning of approval
itself. Filing them as "gates" would have misdescribed when they apply.

| # | Agreed amendment | Recorded as |
|---|---|---|
| 1 | `WXKEY_BOOTSTRAP_ORIGINAL_WECHAT` absent/false, else abort before execution | **A1** |
| 2 | Disposable shadow-copy route only | **A2** (+ **B2** absence check) |
| 3 | Original app identity/signature/hash/mtime recorded before, compared after | **A3** (baseline) + **B1** (comparison) |
| 4 | No-persisted-password route; abort if `r266.wx-mcp.sudo` would be created or used | **A4** (+ **B3** absence check) |
| 5 | `~/.config/wxcli/config.json` is temporary credential state, deleted on success **and** on failure | **C3** |
| 6 | No acquisition artifact may remain except the final approved file | **C2** (+ **C4** sweep) |
| 7 | Approval is still not consent to execute | **C6** (+ the statement above) |

---

## The Decision, as recorded

### D-030 — Operator-performed access-material acquisition, one-shot local pilot only

**Status.** **Active**, recorded 2026-09-08. Scope: **one acquisition, on the
operator's own Mac, for their own WeChat account, for the local H5 pilot.** A
one-shot operator-only local exception. Not a product capability, not a shipping
decision, not a standing permission.

**Why.** D-017 made access-material generation "an opaque external
prerequisite" and gated the real-database path on "real access material
available", but never said by what means it could become available. H5A's
provider research answered that: every provider that exists reads the live
WeChat process's memory as root and ad-hoc re-signs some copy of WeChat. There
is no non-invasive provider to wait for. The prerequisite is therefore either
satisfied by the operator acting outside this project, or the database path does
not proceed. D-030 chooses the first, narrowly, and writes down what it costs.

### A. Preflight refusal gates — checked before execution, all must pass

The run **aborts before any acquisition tool is invoked** if any gate fails. A
gate that cannot be evaluated counts as failed.

- **A1 — `WXKEY_BOOTSTRAP_ORIGINAL_WECHAT` must be absent or false.** If it is
  set to any truthy value in the environment the tool would inherit, abort
  before execution.
- **A2 — Shadow-copy route only.** The invocation must be the one whose branch
  prepares a disposable shadow copy. The `resign-wechat` subcommand is
  forbidden. Any invocation that would reach the original-app route aborts.
- **A3 — Original-app identity recorded first.** The code signature, file hash
  and mtime of `/Applications/WeChat.app` are recorded **before** execution, as
  the baseline for the post-hoc comparison in B1. If the baseline cannot be
  recorded, abort.
- **A4 — No-persisted-password route only.** The tool must run such that its
  password-storage path is unreachable. If the run would create or use the
  `r266.wx-mcp.sudo` Keychain credential — including if that item already
  exists — abort.

### B. Post-hoc verification — checked after execution, all must pass

- **B1 — `/Applications/WeChat.app` unchanged.** Signature, hash and mtime
  compared against the A3 baseline. Any difference is a breach.
- **B2 — No shadow copy survives.** Its absence is verified; a surviving shadow
  copy is a breach, not untidiness, because the tool's cleanup is `defer`-based
  and would not run on an abnormal termination.
- **B3 — No credential persisted.** The Keychain is checked for
  `r266.wx-mcp.sudo`; its presence is a breach.
- **B4 — No egress occurred.**

### C. Standing requirements

- **C1 — One-shot, local, this pilot only.** One acquisition under one consent.
  A repeat, a refresh, an "unattended" mode, or a second account is a new
  decision, not a continuation of this one.
- **C2 — Only the final approved access-material file may be retained**, mode
  `0600`, at its single approved location. **No other acquisition artifact may
  remain.**
- **C3 — `~/.config/wxcli/config.json` is temporary credential-bearing state.**
  It is deleted after a successful import **and** on any failure or abort. It is
  not covered by any tool's own cleanup, so its deletion is an explicit step.
- **C4 — Nothing else may remain.** No process-memory dump, no plaintext or
  decrypted database, no credential, no helper or provider state directory, no
  chat content, no log containing any of the above. Verified by an explicit
  sweep, not assumed from the tool's own cleanup.
- **C5 — D-030 does not weaken D-005 for the shipped product.** The repository
  continues to contain, ship, depend on, and require **no** acquisition code: no
  key extraction, no process-memory access, no re-signing, no vendored provider.
  D-005 remains Active as a statement about the product and its dependency
  graph; D-030 narrows it to that scope and does not touch it there.
- **C6 — Approval of D-030 is not consent to perform acquisition.** It
  authorises the *category* of action. A separate, explicit, per-occasion
  consent naming the side effects at that moment is required before anything
  runs, and consent for one acquisition is never consent for a later one.

### What is otherwise unchanged

D-002 (no driving WeChat), D-011 (nothing unattended), R-003 (no stock
`wechat-cli` as a backend) are untouched. Project.md's v2 non-goals remain true
of the product: the app decrypts nothing, reads no process memory, re-signs
nothing.

### The honest part — what the gates rest on

A1, A2 and A4 are *invocation-shaped*: an environment variable must be absent, a
subcommand must not be used, and the process must genuinely be root so the
password-storage path returns early. The pinned build **contains** password
persistence and an original-app re-signing path; it is invoked so as not to
reach them.

This is a **conscious departure from D-005's stated standard** — *"The safety
boundary must be verifiable at the source and package level, not behind a
runtime flag."* D-030 does not pretend to meet it. It accepts a weaker standard
for a one-off local pilot, and records that R-003 rejected `wechat-cli` for the
structurally identical reason: capability present and reachable, mitigated only
at runtime. The difference D-030 rests on is scope — R-003 concerned a **shipped
dependency of the product**; this concerns a tool the operator runs once,
outside the product, that the product never invokes and never requires.

The preflight/post-hoc pairing is the compensation for that weakness, and the
reason the amendment matters: A1–A4 stop a bad invocation before it touches
anything, and B1–B4 catch a breach that happened anyway. Neither alone would be
enough, because a flag can be wrong and a `defer` can be skipped.

### Accepted risks, named rather than mitigated

- Rion's helper labels its own reasoning *source inference, not a guarantee
  about any released binary*, and its acquisition path *never tested end to end
  on a fresh machine*.
- A verified SHA-256 proves file identity, not safety, and not correspondence to
  reviewed source.
- The provider ecosystem has a DMCA pattern: `ylytdeng/wechat-decrypt` (R-003)
  and `r266-tech/wechat-cli` are both removed. Provenance is weak and
  availability is not durable.
- Acquisition quits and restarts WeChat and reads a live process. An interrupted
  run may leave WeChat closed, or a shadow copy behind — which B2 turns into a
  checked condition rather than a hope.
- **macOS WeChat 4.1.13 key extraction is not established for any provider.** A
  permitted, correctly-executed acquisition may still simply fail.

### Reconciliation with D-005's "option C" clause

D-005 says that if a database path is revisited *"it must be option C — a native
read-only `WeChatDataAdapter`"*. D-017 already chose a different option (an
external reader behind a project-owned protocol) while asserting D-005 was
unchanged; that was an unnoticed inconsistency. D-030 resolves it explicitly:
**D-005's option-C clause is narrowed to the shipping product.** A native
`WeChatDataAdapter` remains the only form in which database reading may ever
ship. The external Rion reader is permitted **for the pilot only**, and a pilot
result is not permission to ship it.

### What this does not decide

Whether database reading becomes a product feature; whether Rion may ever be
redistributed or bundled (the licensing record leaves that explicitly open);
whether H5B or any real-content read follows. Each needs its own decision.

### Revocation

D-030 lapses automatically when the one permitted acquisition completes or
fails, and immediately if any gate or requirement turns out to have been
breached — in which case the acquired material is destroyed and the finding
recorded before anything else proceeds.

**Evidence.** `docs/v2/H5A_PROVIDER_AND_LICENSING_REVIEW.md`;
`docs/v2/H5A_ACCESS_MATERIAL_GATE.md`;
`~/Projects/wechat-source-archive/MANIFEST.md` entries 2, 5, 6.

---

## Feasibility assessment — source review only, nothing executed

Judged against the archived source at the Rion-pinned commit `9b70eecd…`. Source
inference; nothing was built, installed, or run, and no runtime behaviour was
observed.

| Gate / requirement | Source evidence | Feasible in principle |
|---|---|---|
| A1 / A2 / B1 — installed app never re-signed | `cmd/wxkey/main.go:596` — the original-app route runs only `if envTrue("WXKEY_BOOTSTRAP_ORIGINAL_WECHAT")`; the `else` at 604 uses the shadow copy. `resign-wechat` is a separate explicit subcommand | **Yes** — the dangerous path is **opt-in**, not opt-out |
| A2 / B2 — disposable shadow copy only | `shadowWeChatPath()` (997) → `~/Library/Application Support/wx-mcp/WeChat-shadow.app`; `prepareShadowWeChat()` returns a deferred `cleanup` (607–621); `openManagedShadowRoot` rejects symlinks and escapes from the user's home (1019, 1036, 1059) | **Yes**, with the caveat that cleanup is `defer`-based and would not run on SIGKILL — hence B2 |
| A4 / B3 — no persisted password | `ensureStoredSudoPassword()` (2276) begins `if os.Geteuid() == 0 { return nil }` — under root it prompts nothing and writes nothing. The Keychain service would be `r266.wx-mcp.sudo` (2256) | **Yes, conditional** on genuinely running as root at that point |
| B4 — no egress | No `net`, `net/http`, `net/url` or `golang.org/x/net` import anywhere in the tree at the pinned commit; the only `net` matches are filesystem paths (WeChat's `net/kvcomm`) | **Yes** |
| C2 / C3 — only the approved file retained | Rion's `import-access` writes its destination `0600` and never returns key values. wxkey separately writes `~/.config/wxcli/config.json`, a second credential-bearing artifact outside the approved location | **Yes only with C3's explicit deletion** — no tool's own cleanup covers it |
| C4 — nothing else remains | Rion verifies against private snapshots and commits atomically; wxkey cleans the shadow copy on the normal path | **Plausible, not proven** — requires C4's explicit sweep |
| C1 / C5 / C6 | Policy and text | **Yes** |

**Assessment: the safe route appears feasible in principle.** The gates that
depend on tool behaviour rather than policy each have a concrete basis in the
archived source, and the most dangerous behaviour is opt-in rather than default.

Three qualifications stand with that conclusion:

- **Feasible ≠ safe, and feasible ≠ effective.** A1/A2/A4 are satisfied by *how
  the tool is invoked*, so they are only as strong as the invocation and the B
  checks. And even a flawless run may yield nothing: macOS WeChat **4.1.13
  extraction is unestablished for every provider examined.**
- **C3 exists because no tooling performs it.** wxkey's own
  `~/.config/wxcli/config.json` is a retained credential artifact outside the
  approved location and must be destroyed deliberately.
- **This is source inference on one archived revision.** It says nothing about
  any released binary, and Rion's own helper states its acquisition path has
  never been tested end to end on a fresh machine.
