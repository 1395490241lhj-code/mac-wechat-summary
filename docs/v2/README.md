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
8. `H6_AGENT_RUNNER_DECOUPLING.md` — pluggable agent runtime
9. `H5A_REAL_DATA_CANARY.md` — first real-data canary
10. `DB_READER_INTERFACE_GATE.md` — synthetic database-reader interface gate (counts only)
11. `READER_BOUNDARY_INTEGRATION.md` — the runtime-neutral message-source boundary in the bridge
12. `READER_SOURCE_ACTIVATION.md` — explicit source selection from the agent path
13. `M1_MEMORY_FOUNDATION.md` — source-neutral local memory store, coverage model and FTS index (adds no MCP tool)
14. `M1_1_MEMORY_HARDENING.md` — logical identity over source observations (schema v2), the app-owned consent state, and the multi-source coverage composition rule
15. `M2_MEMORY_RETRIEVAL.md` — the internal query service, non-droppable result envelope, stable citation, required/supplemental coverage, and the explicit foreground sync (no MCP tool)
16. `M2_1_MEMORY_MCP_GATE.md` — the separate read-only memory MCP server, explicit activation from ClaudeRunner, and the 4-tool / 8-tool wire gate
17. `M2_2A_LIVE_MEMORY_AGENT_GATE.md` — live Claude Code memory gate: harness, corpus, isolation and preflight refusal executed; **live half not run (no credential)**
18. `M2_2A_LIVE_MEMORY_AGENT_GATE_COMPLETION.md` — the live half, run: exactly 4 / exactly 8 on a real Claude Code 2.1.238 process, four graded synthetic turns, **LIVE MEMORY AGENT GATE PASS**

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
- **The runner's source reachability changed.** `READER_BOUNDARY_INTEGRATION.md`
  says the runner "needed no change and received none", which was correct at
  `8c4138e`. `READER_SOURCE_ACTIVATION.md` records the change it then received:
  the agent path can select a source, but only on an explicit, validated request.
- **The M1 consent gap is closed by M1.1.** `M1_MEMORY_FOUNDATION.md` §7
  records that a Python process could only see a bare preference flag and
  refused when it was unobservable. `M1_1_MEMORY_HARDENING.md` replaces that
  with an app-written, versioned consent state; the refusal on absence stands,
  but absence is now "no", not "unknown".
- **"No MCP tool" in the M1–M2 memory reports is superseded by M2.1.**
  `M2_1_MEMORY_MCP_GATE.md` adds a separate read-only memory server; it is
  off by default, so the four-tool default those reports describe still holds.
- **`M2_2A` is a "not run", not a failure — and is completed by
  `M2_2A_…_COMPLETION.md`.** The first report's verdict line reads FAIL by
  the gate's binary rule because the live half never executed for want of a
  credential; the completion report records the same harness run for real
  with a PASS. The first report is preserved unchanged as sealed evidence.
- **The CLI audit is research, not approval.** Its header states *"architecture
  research only; no implementation decision or production approval"*.

For the current blocker set, read `Current Status.md` in the vault — not this
directory.
