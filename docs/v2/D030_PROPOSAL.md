# Proposed Decision D-030 — awaiting operator approval

**This is a proposal, not a Decision.** Nothing in the vault has been amended.
No tool has been installed and nothing has been run. If approved, the text below
goes into `Decisions.md` and the D-005 reconciliation into that entry; if
rejected, this file is deleted and H5A stays blocked.

It relaxes an **Active** safety boundary (D-005). AGENTS.md §7 forbids doing
that silently, which is why it is written as a proposal for you to accept or
refuse rather than recorded and reported.

---

## D-030 — Operator-performed access-material acquisition is permitted for the local H5 pilot, outside this repository

**Status.** Proposed 2026-09-08. Scope: **the local H5 pilot on the operator's
own Mac and own WeChat account only.** Not a product capability, not a shipping
decision, not a general permission.

**Why.** D-017 made access-material generation "an opaque external
prerequisite" and gated the real-database path on "real access material
available", but never said by what means it could become available. H5A's
provider research answered that: every provider that exists reads the live
WeChat process's memory as root and ad-hoc re-signs WeChat. There is no
non-invasive provider to wait for. So the prerequisite is either satisfied by
the operator acting outside this project, or the database path does not proceed.
This Decision chooses the first, narrowly, and writes down what that costs.

**What is permitted.**

1. The operator may run a separately obtained external tool, on their own
   machine and their own account, to produce an access-material file.
2. The agent may install and pin such a tool **outside** this repository, and
   may invoke it **only** after an explicit, per-occasion operator consent that
   names the side effects. Consent for one acquisition is never consent for a
   later one.

**What is unchanged, and must stay verifiable.**

- This repository contains, ships, depends on, and requires **no** acquisition
  code — no key extraction, no process-memory access, no re-signing, no
  vendored provider. `bridge/rion_reader_adapter.py` continues to execute only
  an injected path and to contain no key handling. D-005 remains Active **as a
  statement about the product and its dependency graph**; D-030 narrows it to
  that scope and does not weaken it there.
- D-002 (no driving WeChat), D-011 (nothing unattended), R-003 (no stock
  `wechat-cli` as a backend) are untouched.
- Project.md's v2 non-goals are unchanged and remain true of the product: the
  app decrypts nothing, reads no process memory, re-signs nothing.

**Mechanism limits the operator accepts.** An acquisition that requires any of
the following is refused, and the route is abandoned rather than downgraded:

- disabling SIP;
- modifying or re-signing the **installed** `/Applications/WeChat.app` — a
  managed shadow copy only;
- persisting an admin password to the Keychain or anywhere else;
- any network egress from the acquisition tool;
- acquisition while another user's WeChat session is running.

**The honest part — how those limits are enforced.** They hold **at invocation,
not at the source level**: an environment variable (`WXKEY_NO_ELEVATE=1`), a
choice of subcommand (never `resign-wechat`), and running under an already-root
context so the password-storage path returns early. The pinned wxkey build
*contains* password persistence and an original-app re-signing path; it is
invoked so as not to reach them.

This is a **conscious departure from D-005's stated standard**, which is *"The
safety boundary must be verifiable at the source and package level, not behind a
runtime flag."* D-030 does not pretend to meet that standard. It accepts a
weaker one for a one-off local pilot, and records that R-003 rejected
`wechat-cli` as a backend for the structurally identical reason — capability
present and reachable, mitigated only at runtime. The difference D-030 relies on
is scope: R-003 concerned a **shipped dependency of the product**; this concerns
a tool the operator runs once, outside the product, that the product never
invokes and never requires.

**Accepted risks, named rather than mitigated.**

- Rion's helper labels its own reasoning *source inference, not a guarantee
  about any released binary*, and its acquisition path *never tested end to end
  on a fresh machine*.
- A verified SHA-256 proves file identity, not safety, and not correspondence
  to reviewed source.
- The provider ecosystem has a DMCA pattern: `ylytdeng/wechat-decrypt` (R-003)
  and now `r266-tech/wechat-cli` are both removed. Availability of any provider
  is not durable, and provenance is correspondingly weak.
- Acquisition quits and restarts WeChat and touches a live process; an
  interrupted run may leave WeChat closed or a shadow copy behind.

**Reconciliation with D-005's "option C" clause.** D-005 says that if a database
path is revisited *"it must be option C — a native read-only
`WeChatDataAdapter`"*. D-017 already chose a different option (external reader
behind a project-owned protocol) while asserting D-005 was unchanged; that was
an unnoticed inconsistency. D-030 resolves it explicitly: **D-005's option-C
clause is narrowed to the shipping product.** A native `WeChatDataAdapter`
remains the only form in which database reading may ever ship. The external
Rion reader is permitted **for the pilot only**, and a pilot result is not
permission to ship it.

**What this does not decide.** Whether database reading becomes a product
feature; whether Rion may ever be redistributed or bundled (the licensing record
leaves that explicitly open); whether H5B or any real-content read follows. Each
needs its own decision.

**Revocation.** D-030 lapses automatically when the H5 pilot ends, and
immediately if any limit above turns out to have been breached — in which case
the acquired material is destroyed and the finding recorded before anything
else.

**Evidence.** `docs/v2/H5A_PROVIDER_AND_LICENSING_REVIEW.md`;
`docs/v2/H5A_ACCESS_MATERIAL_GATE.md`; `~/Projects/wechat-source-archive/MANIFEST.md`
entries 2, 5 and 6.

**Consequence if approved.**

- H5A's "real access material available" gate becomes reachable.
- A second, separate operator consent is still required before any acquisition
  runs, naming the side effects at that moment.
- The licensing review is recorded as **local-pilot permitted, redistribution
  and product licensing unresolved** — the two are separable and only the first
  is answered.
- `Constraints.md` gains the five mechanism limits as standing constraints.
