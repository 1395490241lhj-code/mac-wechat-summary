# docs/v2 — phase evidence index

These are **sealed, point-in-time phase reports**. Each was correct as of its
own date and is deliberately not edited afterwards, so that later work does not
rewrite the evidence it was built on.

**They supersede one another. None of them individually defines current state.**

Canonical current state comes from the Obsidian project memory, reached via
[`AGENTS.md`](../../AGENTS.md). If the vault is unavailable, use this index plus
Git history and the reports together — never one old phase report in isolation.

## Read order

1. `WECHAT_CLI_BACKEND_AUDIT.md` — third-party CLI backend audit
2. `H4_SYNTHETIC_DIGEST_EVALUATION.md` — synthetic digest evaluation
3. `H4_5_SAFETY_PRECONDITION_GATE.md` — tool and privacy preconditions
4. `H4_6_PRIVACY_FAILURE_PATH_GATE.md` — provider-failure privacy path
5. `H4_7_DESKTOP_SECURITY_REPORT.md` — Desktop security report
6. `H5_UI_GATES_PENDING.md` — pending Desktop UI gates
7. `H5_PRIVACY_DECISIONS.md` — accepted privacy decisions

## Known supersessions

Verified from the documents themselves:

- **The real-data blocker list changed twice.** `H4_5` concludes *"BLOCKED for
  real-data H5 — on the upstream gate only"* (one condition). `H4_6` concludes
  *"BLOCKED for real-data H5, now on **two** conditions rather than one"*.
- **Two former gates were then resolved.** `H5_PRIVACY_DECISIONS.md` records that
  provider transmission and digest retention are *"no longer gates — they are
  decided"*. A reader of `H4_6` alone would still treat them as open.
- **`H4_7` is explicitly provisional.** Its own verdict is *"PROVISIONAL PASS —
  patched runtime only"*, produced on a disposable clone; it is not
  stock-runtime acceptance.
- **The CLI audit is research, not approval.** Its header states *"architecture
  research only; no implementation decision or production approval"*.

For the current blocker set, read `Current Status.md` in the vault — not this
directory.
