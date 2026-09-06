# M2.2a — Live Claude Memory Agent Gate

**Sealed:** 2026-09-06 · **Branch:** `v2/rewrite` · **Builds on:** `059dc81` (M2.1)
**Scope:** synthetic / local only. No real WeChat data, no reader change, no
ingestion scheduling, `SKILL.md` untouched, no tool added or removed, no
`SourcePolicy` exposure, nothing pushed.

## Verdict

```
LIVE MEMORY AGENT GATE FAIL — not run: no credential
```

Nothing below is a measured failure of the memory boundary. The live half of
the gate — a real Claude Code process on the wire in both modes and four
model turns — **did not execute**, because the fixed OAuth keychain item
`wechat-shadow-claude-oauth` (deleted after H5a, per that report) does not
exist on this machine and the installed CLI reports `loggedIn: false`. Under
D-016 the Claude backend takes its credential from that item and nowhere
else; the other `Claude Code-credentials*` keychain entries belong to the
Claude Code application's own store and were deliberately not used. Two
operator confirmations that the item had been created were followed by
searches (sandboxed and unsandboxed, by account and by service name alone)
that found nothing, so the run was sealed rather than guessed at. **This is a
"not run" verdict, not a "failed" one**, and the evidence discipline of this
project (§6 of `AGENTS.md`) forbids recording it as either a pass or a
technical failure.

Everything that does not require a model was executed against the real
runner code paths and is recorded below. When the keychain item exists, the
whole gate is one command (§7).

---

## 1. Harness (committed, offline-verified)

`scripts/m2_2a_live_memory_gate.py`:

- builds a synthetic WeChat store for the bridge (H3 scenario A) and a
  synthetic **v2 memory store** from an invented corpus (§2), with the app's
  `consent.state` written to an **isolated HOME** plist;
- `LiveMemoryRunner(ClaudeRunner)` — gate-only: swaps the system prompt for a
  one-off test prompt (so `SKILL.md` is neither used nor changed) and switches
  the CLI to `stream-json` so tool calls and returned coverage can be
  summarised as sanitized evidence. The boundary proxy, argv isolation, purge
  and expected sets are the unchanged runner's (asserted: `--tools ""`,
  expected set exactly the eight names, `SKILL.md` bytes absent from argv);
- gives the memory server's process the isolated `HOME` and a `PATH` with no
  `defaults` tool, so the server's plist fallback reads the synthetic consent
  state and never the real preference domain;
- records only: tool names, call counts, memory-tool classes, coverage
  states, citation presence, exit codes, residue counts, and the (synthetic)
  answers. Never a credential, a private path, or an environment dump.

`shadow/tests/test_m2_2a_gate_harness.py` (4 tests) pins the corpus
semantics, the eight-tool boundary of the gate runner, the isolated-consent
env, the preflight refusal, and that result summaries carry no message text.

## 2. Synthetic corpus (all names and content invented)

| # | Requirement | Fixture | Verified by the query service |
|---|---|---|---|
| 1 | directly searchable fact | 林晓: 季度评审改到 10 月 14 日下午三点，会议室 B204 | — |
| 2 | fact needing context | 陈伟 asks whether to sign the contract; 林晓's reply two messages later says not yet (法务, 第七条) | — |
| 3 | linked logical message | 预算表 message observed by the visual reader **and** a database reader, linked with basis `operator` | one item, **2 observations** |
| 4 | duplicate-looking, unlinked | two visual "下午同步一下进度" observations, 700 s apart | **2 items** |
| 5 | covered window, no result | 「产品组」: source-wide complete record; "发票" never appears | `observed_complete`, `trustworthy_empty = true` |
| 6 | partially observed, no result | 「供应商群」: later conversation-scoped `observed_partial` (`limit_reached`); "报销" never appears | `observed_partial`, `trustworthy_empty = false`; an unscoped query is also `observed_partial` |

11 messages, 1 logical message. Consent: isolated plist, generation 1,
allowed.

## 3. Isolation (config-level, real runner code)

| Check | Result |
|---|---|
| MCP servers named with memory on | `wechat_companion`, `wechat_memory` |
| memory DB path in memory server env | **true** |
| memory DB path in bridge server env | **false** |
| memory DB path in Claude Code's environment | **false** |
| `*MEMORY*` variables in Claude Code's environment | **none** |

## 4. Preflight refusal — executed, real code path

Memory explicitly requested with `--memory-db-path` pointing at a store that
does not exist; `claude_bin` replaced by a tripwire script that would create a
marker file if ever executed.

| | |
|---|---|
| aborted | `memory: the store cannot be read (memory_store_missing)` |
| Claude Code launched | **false** (tripwire marker absent) |
| MCP config written | **false** |
| proxy state written | **false** |
| residue after cleanup | **0 files** |
| path in the refusal message | **false** |

The run did not continue in four-tool mode: `assert_tool_boundary` raised
inside `_prepare`, before the config was written and before the proxy
started.

## 5. Wire probes and live tasks — NOT RUN

| Item | Planned | Result |
|---|---|---|
| default probe | exactly 4 on the wire, no provider traffic | not run |
| memory probe | exactly 8 on the wire, no provider traffic | not run |
| search + evidence | 季度评审 question | not run |
| context recovery | contract decision question | not run |
| trustworthy empty | 发票 in 「产品组」 | not run |
| incomplete empty | 报销 in 「供应商群」 | not run |

Claude Code version on this machine: **2.1.238**; model that would have been
used: `claude-sonnet-5` (runner default). Neither was exercised.

## 6. Gates

| Suite | M2.1 | M2.2a |
|---|---|---|
| `memory/` | 250 | **250** unchanged |
| `bridge/` | 72 | **72** unchanged |
| `shadow/` | 122 | **126** (+4 harness; 122 pre-existing unmodified) |
| Swift | 177 / 14 | not re-run (no Swift change) |

## 7. To run the live half

Recreate the item in an operator terminal, then:

```
claude setup-token
security add-generic-password -a "$USER" -s wechat-shadow-claude-oauth -U -w
python3 scripts/m2_2a_live_memory_gate.py --claude-bin ~/.local/bin/claude \
  --python <interpreter with mcp> --work <empty dir> --report <path.json> \
  --claude-oauth-from-keychain
security delete-generic-password -a "$USER" -s wechat-shadow-claude-oauth
```

Grade the four answers against §5's intent (grounded; context recovered;
"not mentioned" only with `trustworthy_empty = true`; no "never happened"
claim under partial coverage) and re-seal this report with the observed tool
sets, counts, coverage states and residue. Until then the eight-tool surface
rests on M2.1's runner tests, boundary-proxy tests and the live
*memory-server* composition test — not on a live Claude Code run.
