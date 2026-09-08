# Proposed Decision D-030 — awaiting operator approval (amended)

**This is a proposal, not a Decision.** Nothing in the vault has been amended.
No tool has been installed, built, or run. No WeChat process, memory, database,
key, Keychain item, or application bundle has been accessed. Nothing has been
re-signed.

It relaxes an **Active** safety boundary (D-005). AGENTS.md §7 forbids doing
that silently, which is why it is written as a proposal to accept or refuse
rather than recorded and reported.

**Amendment note.** This revision adds nine fail-closed requirements and a
feasibility assessment. It also **corrects one claim** in the previous draft:
that draft said wxkey's bootstrap prefers re-signing the installed WeChat and
falls back to a shadow copy only when the installed app cannot be modified. A
closer read of the same archived source shows the opposite — the original-app
route is **opt-in behind an environment variable**, and the shadow copy is the
default branch. The correction is favourable to the safe route and is evidenced
in the assessment below.

---

## D-030 — Operator-performed access-material acquisition, one-shot local pilot only

**Status.** Proposed 2026-09-08. Scope: **one acquisition, on the operator's own
Mac, for their own WeChat account, for the local H5 pilot.** Not a product
capability, not a shipping decision, not a standing permission.

**Why.** D-017 made access-material generation "an opaque external
prerequisite" and gated the real-database path on "real access material
available", but never said by what means it could become available. H5A's
provider research answered that: every provider that exists reads the live
WeChat process's memory as root and ad-hoc re-signs some copy of WeChat. There
is no non-invasive provider to wait for. So the prerequisite is either satisfied
by the operator acting outside this project, or the database path does not
proceed. This Decision chooses the first, narrowly, and writes down what it
costs.

### The nine fail-closed requirements

Each is a **refusal condition**. If any cannot be satisfied, or cannot be
verified after the fact, the route is abandoned rather than downgraded, and any
material already produced is destroyed.

1. **The shipped `/Applications/WeChat.app` is never modified or re-signed.**
   Not by wxkey, not by any helper, not "temporarily", not as a diagnostic. The
   `resign-wechat` subcommand and the `WXKEY_BOOTSTRAP_ORIGINAL_WECHAT`
   environment variable are forbidden. Its code signature and mtime are
   recorded before and compared after; any difference is a breach.
2. **Only a disposable shadow copy may be used**, created under the invoking
   user's own home, ad-hoc signed, and deleted when the run ends. Its absence
   is verified afterwards; a shadow copy surviving the run is a breach, not
   untidiness.
3. **No admin password or acquisition credential is persisted** — not to the
   macOS Keychain, not to a file, not to an environment. The tool must be
   invoked such that its password-storage path is not reached, and the Keychain
   is checked afterwards for the service name it would have used.
4. **No network egress from the acquisition tool.** The provider must contain
   no network capability, and the run must produce no outbound connection.
5. **One-shot, local, this pilot only.** One acquisition under one consent. A
   repeat, a refresh, an "unattended" mode, or a second account is a new
   decision, not a continuation of this one.
6. **The only artifact that may be retained is the access-material file itself,
   mode `0600`**, at its single approved location. Every other output of the
   acquisition — including the provider's own config or key file elsewhere on
   disk — is destroyed once the material has been imported.
7. **Nothing else may remain.** No process-memory dump, no plaintext or
   decrypted database, no credential, no helper or provider state directory, no
   chat content, no log containing any of the above. Verified by an explicit
   sweep, not assumed from the tool's own cleanup.
8. **D-030 does not weaken D-005 for the shipped product.** The repository
   continues to contain, ship, depend on, and require **no** acquisition code:
   no key extraction, no process-memory access, no re-signing, no vendored
   provider. D-005 remains Active as a statement about the product and its
   dependency graph; D-030 narrows it to that scope and does not touch it there.
9. **Approving D-030 is not consent to perform acquisition.** It authorises the
   *category* of action. A separate, explicit, per-occasion consent naming the
   side effects at that moment is required before anything runs, and consent for
   one acquisition is never consent for a later one.

### What is otherwise unchanged

D-002 (no driving WeChat), D-011 (nothing unattended), R-003 (no stock
`wechat-cli` as a backend) are untouched. Project.md's v2 non-goals remain true
of the product: the app decrypts nothing, reads no process memory, re-signs
nothing.

### The honest part — how requirements 1–4 are enforced

They hold **at invocation, not at the source level**: by *not* setting
`WXKEY_BOOTSTRAP_ORIGINAL_WECHAT`, *not* invoking `resign-wechat`, running under
an already-root context so the password-storage path returns early, and setting
`WXKEY_NO_ELEVATE=1`. The pinned build *contains* password persistence and an
original-app re-signing path; it is invoked so as not to reach them.

This is a **conscious departure from D-005's stated standard**, which is *"The
safety boundary must be verifiable at the source and package level, not behind a
runtime flag."* D-030 does not pretend to meet that standard. It accepts a
weaker one for a one-off local pilot, and records that R-003 rejected
`wechat-cli` for the structurally identical reason — capability present and
reachable, mitigated only at runtime. The difference D-030 rests on is scope:
R-003 concerned a **shipped dependency of the product**; this concerns a tool the
operator runs once, outside the product, that the product never invokes and
never requires.

Requirements 1–3 are written as *post-hoc verifiable* — signature and mtime
comparison, shadow-copy absence, Keychain absence — precisely because the
pre-hoc guarantee is only a flag. Verification after the fact is what makes them
fail-closed rather than aspirational.

### Accepted risks, named rather than mitigated

- Rion's helper labels its own reasoning *source inference, not a guarantee
  about any released binary*, and its acquisition path *never tested end to end
  on a fresh machine*.
- A verified SHA-256 proves file identity, not safety, and not correspondence to
  reviewed source.
- The provider ecosystem has a DMCA pattern: `ylytdeng/wechat-decrypt` (R-003)
  and `r266-tech/wechat-cli` are both removed. Provenance is correspondingly
  weak and availability is not durable.
- Acquisition quits and restarts WeChat and reads a live process. An interrupted
  run may leave WeChat closed, or a shadow copy behind — which requirement 2
  turns into a checked condition rather than a hope.
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
fails, and immediately if any of the nine requirements turns out to have been
breached — in which case the acquired material is destroyed and the finding
recorded before anything else proceeds.

**Evidence.** `docs/v2/H5A_PROVIDER_AND_LICENSING_REVIEW.md`;
`docs/v2/H5A_ACCESS_MATERIAL_GATE.md`;
`~/Projects/wechat-source-archive/MANIFEST.md` entries 2, 5, 6.

### Consequence if approved

- H5A's "real access material available" gate becomes reachable.
- A second, separate operator consent is still required before any acquisition
  runs (requirement 9).
- The licensing review is recorded as **local-pilot permitted; redistribution
  and product licensing unresolved** — separable questions, only the first
  answered.
- `Constraints.md` gains requirements 1–7 as standing constraints.

---

## Feasibility assessment — source review only, nothing executed

Whether the safe route is *possible in principle*, judged against the archived
source at the Rion-pinned commit `9b70eecd…`. This is source inference. Nothing
was built, installed, or run, and no runtime behaviour was observed.

| Requirement | Source evidence | Feasible in principle |
|---|---|---|
| 1. Installed app never re-signed | `cmd/wxkey/main.go:596` — the original-app route runs only `if envTrue("WXKEY_BOOTSTRAP_ORIGINAL_WECHAT")`; the `else` branch at 604 uses the shadow copy. `resign-wechat` is a separate explicit subcommand | **Yes** — and importantly the dangerous path is **opt-in**, not opt-out |
| 2. Disposable shadow copy only | `shadowWeChatPath()` (997) → `~/Library/Application Support/wx-mcp/WeChat-shadow.app`; `prepareShadowWeChat()` returns a `cleanup` closure that is deferred (607–621); `openManagedShadowRoot` rejects symlinks and paths escaping the user's home (1019, 1036, 1059) | **Yes**, with the caveat that cleanup is `defer`-based and would not run on SIGKILL — hence the post-hoc absence check |
| 3. No persisted password | `ensureStoredSudoPassword()` (2276) begins `if os.Geteuid() == 0 { return nil }` — under root it prompts nothing and writes nothing. Keychain service would be `r266.wx-mcp.sudo` (2256) | **Yes, conditional** on genuinely running as root at that point |
| 4. No network egress | No `net`, `net/http`, `net/url`, or `golang.org/x/net` import anywhere in the tree at the pinned commit. The only `net` matches are filesystem paths (WeChat's `net/kvcomm`) | **Yes** |
| 5. One-shot local pilot | Policy, not tool behaviour | **Yes** |
| 6. Only the access-material file retained, `0600` | Rion's `import-access` writes its destination `0600` and never returns key values. But wxkey writes its own `~/.config/wxcli/config.json` — a second credential-bearing artifact outside the approved location | **Yes only if** that file is explicitly destroyed after import; it is not covered by the tool's own cleanup |
| 7. Nothing else remains | Rion verifies against private snapshots and commits atomically; wxkey's shadow copy is cleaned on the normal path | **Plausible, not proven.** Requires the explicit sweep requirement 7 mandates |
| 8. D-005 unweakened for the product | Textual; the repository already contains no acquisition code | **Yes** |
| 9. Approval ≠ consent | Textual | **Yes** |

**Assessment: the safe route appears feasible in principle.** The three
requirements that depend on tool behaviour rather than policy — no original-app
re-signing, no persisted password, no egress — each have a concrete basis in the
archived source, and the most dangerous behaviour is opt-in rather than default.

Three qualifications belong with that conclusion:

- **Feasible ≠ safe, and feasible ≠ effective.** Requirements 1–3 are satisfied
  by *how the tool is invoked*, so they are only as strong as the invocation and
  the post-hoc checks. And even a flawless run may not yield keys: macOS WeChat
  **4.1.13 extraction is unestablished for every provider examined.**
- **Requirement 6 needs an explicit step nobody's tooling performs.** wxkey's
  own `~/.config/wxcli/config.json` is a retained credential artifact outside the
  approved location; it must be destroyed deliberately.
- **This is source inference on one archived revision.** It says nothing about
  any released binary, and Rion's own helper states its acquisition path has
  never been tested end to end on a fresh machine.

Nothing here should be read as a recommendation to proceed. It answers only the
question asked: whether the route, as constrained, is coherent on paper.
