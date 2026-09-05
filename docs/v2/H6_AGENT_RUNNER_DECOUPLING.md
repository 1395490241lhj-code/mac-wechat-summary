# H6 — Decouple the digest pipeline from Hermes (H6.1–H6.3)

Status at time of writing: **code landed, synthetic live gates pending a
credential.** No real WeChat data was accessed. No Hermes chat was run. No
byte of `.hermes/skills/wechat-digest/SKILL.md` changed.

## H6.1 — Decision

- The agent runtime is **pluggable**. Orchestration (`shadow/agent_runner.py`)
  is runtime-neutral; each runtime is an `AgentRunner` under `shadow/runners/`.
- **Hermes is an optional backend, not the critical path.** The wait for a
  stock Hermes release past v0.20.5 (PR #94339) no longer blocks the project.
- **H5 stays the first real-data validation stage and is backend-neutral.** It
  runs on whichever backend has passed its synthetic gates.
- **`ClaudeRunner` is the first candidate backend.** Both Anthropic-backed
  runtimes fall under the existing provider-transmission acceptance
  (`H5_PRIVACY_DECISIONS.md`, Decision 1).
- **Codex remains deferred** pending two decisions the assessment identified:
  whether its shell tool can be made structurally unreachable, and an explicit
  acceptance of OpenAI as a second provider egress.

## H6.2 — AgentRunner extraction

`shadow/wechat_shadow_run.py` was a Hermes-only monolith. It is now:

| Module | Owns |
|---|---|
| `agent_runner.py` | run sequence, `ShadowError`, timeout → failed run, signals → `finally`, exit codes `0/1/2/3/130`, review-only stdout, the shared `check_tool_boundary` rule |
| `runners/hermes.py` | launch argv, `HERMES_HOME`, env-from-scratch, init-log boundary probe, `session_id:` parsing, delete → optimize → sweep purge, the Hermes-specific expected set of eight |
| `wechat_shadow_run.py` | CLI; `--agent-backend hermes` is the default and takes the same four Hermes flags as before |

**Behaviour equivalence.** The Hermes argv, environment keys and values,
per-call timeouts (120 s list/delete, 300 s optimize, 900 s digest), the
boundary marker (`Final tool selection`), the session-id shape, and the purge
order are pinned by `shadow/tests/test_hermes_runner.py` against a fake
`subprocess`. One deliberate difference: a digest turn that exceeds its timeout
is now reported as `run failed — agent runtime timed out` with exit `1` after
cleanup, where the old code would have raised a traceback (also exit `1`) after
cleanup.

## H6.3 — ClaudeRunner (synthetic-only)

Installed CLI: Claude Code **2.1.238** (`~/.local/bin/claude`). The flags used
were verified present in `claude --help` on this build. `--system-prompt-file`
is **not** available on this build, so `SKILL.md` is read verbatim and passed
through `--system-prompt`; nothing is copied to disk or rewritten.

Configuration: `--tools ""`, `--strict-mcp-config --mcp-config <isolated>`,
`--allowedTools <four bridge tools>`, `--permission-mode dontAsk`,
`--no-session-persistence`, `--setting-sources ""`, empty working directory,
`--output-format json`, `--model claude-sonnet-5` (pinned).

Auth design (2026-09-05 follow-up): `--bare` is **not** used, because bare
mode restricts auth to `ANTHROPIC_API_KEY` and ignores `CLAUDE_CODE_OAUTH_TOKEN`.
Isolation is enforced flag by flag instead: `--setting-sources ""`,
`--settings '{"autoMemoryEnabled":false}'`, `--disable-slash-commands`,
`--strict-mcp-config`, `--tools ""`, `--no-session-persistence`, plus
`CLAUDE_CODE_DISABLE_CLAUDE_MDS`, `_BUNDLED_SKILLS`, `_BACKGROUND_TASKS`,
`_WORKFLOWS`. Credentials enter only via `--pass-env NAME` (allowlist:
`CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY`), copied from the parent
environment into the child by name; `--env` refuses credential names. The
keychain login of the operator's `claude` is keyed per config directory and is
therefore intentionally invisible to the isolated runner.

Environment built from scratch: `HOME`, `CLAUDE_CONFIG_DIR` and cwd inside
`--isolated-home`; `PATH=/usr/bin:/bin:/usr/sbin:/sbin`; `ANTHROPIC_BASE_URL`
pointing at the in-process proxy; telemetry, error reporting, auto-update and
non-essential traffic disabled. Credentials only via `--pass-env NAME`.

**Wire-verified tool boundary.** `shadow/tool_boundary_proxy.py` is the
runtime-neutral exact-N verifier. `exact8_assertion_proxy.py` is now a thin
wrapper over it with the Hermes eight, same CLI, same state JSON, same
acceptance contract. For Claude the runner first sends a *probe* turn with the
proxy in `probe` mode (records `tools[]`, forwards nothing), compares the
observed names with the four bridge tools, and only then runs the digest with
the proxy enforcing every request. Prefixes and counts are not assumed: a
mismatch in either direction aborts before any message is read.

## Gate results

| Gate | Result |
|---|---|
| Swift baseline | **169 tests / 13 suites passed** (Swift Testing, `xcodebuild test`, sources untouched by H6) |
| `bridge/tests` | **34 passed** |
| `.hermes/skills/wechat-digest/evaluation` | **39 passed**, including `test_skill_passes_the_hermes_security_scanner` |
| Hermes `skills_guard.scan_skill` (direct) | verdict **safe**, 0 findings |
| `SKILL.md` bytes | blob `81d276ea…` at HEAD before and after — **unchanged** |
| `shadow/tests` (new) | **67 passed** — driver 12, HermesRunner 15, ClaudeRunner 24, proxy 10, credential transport 6 |
| Synthetic Scenario A + J on Claude | **NOT RUN — blocked** (see below) |
| Exact tool boundary on the wire (Claude) | **NOT VERIFIED — blocked** |
| Canary persistence audit (Claude) | **NOT RUN — blocked** |

### Why the live gates are blocked

The installed Claude Code CLI has **no usable credential**: `claude auth
status` reports `loggedIn: false, authMethod: none` in a clean environment, no
`ANTHROPIC_API_KEY` exists in the shell, and no credentials file exists. A
keychain item named for Claude Code is present but the CLI does not accept it.
Logging in or supplying a key is an operator action; this task did not perform
it.

What *was* exercised against the real binary: the full `h6_synthetic_gate.py`
path for Scenario A. Claude Code never sent a tools-bearing request, the runner
failed closed (`ABORTED: tool boundary: Claude never sent a tools-bearing
request`, exit 2), the proxy was stopped, and the isolated home was purged and
proved empty. That is the intended fail-closed behaviour, not a pass.

To run the gates once a credential exists:

```bash
python3 shadow/h6_synthetic_gate.py --claude-bin ~/.local/bin/claude \
  --python <interpreter with mcp> --work <empty dir> --report <path.json> \
  --pass-env CLAUDE_CODE_OAUTH_TOKEN
```

Acceptance: both scenario runs exit `0` with digests matching their
`expectation` strings; `tool_boundary` observed set equals the four bridge
tools with `tools_bearing >= 1` and `violations == 0`; the canary report shows
`canary_hits_real_claude == []`, an explained inventory of the isolated home,
and `post_purge_files == []` with `post_purge_canary_hits == []`.

**ClaudeRunner is therefore not yet eligible for H5.**

## Hermes-install discrepancy (resolved, read-only)

`hermes --version` reports `local a08dfab3 (+1 carried commit)`, while the
vault says the official install was never modified. Inspection of
`~/.hermes/hermes-agent`: HEAD is `a08dfab3` (upstream merge of PR #94351),
the reflog holds exactly one entry (the clone, 2026-08-24), the working tree is
clean, and the #94339 fix commit `c1b39a86…` does not exist in the object
store. The "carried commit" is the banner's arithmetic: it compares HEAD with
the current upstream tip (`1bbb6e5b`), which does not contain `a08dfab3`, so
one commit appears local-ahead. **The install is the unmodified H4 base;
nothing was patched in.** No runner or test assumption is affected.

## Files

`shadow/agent_runner.py`, `shadow/runners/{__init__,hermes,claude}.py`,
`shadow/tool_boundary_proxy.py`, `shadow/exact8_assertion_proxy.py` (wrapper),
`shadow/wechat_shadow_run.py` (CLI), `shadow/h6_synthetic_gate.py`,
`shadow/tests/*`, `shadow/pytest.ini`, `shadow/README.md`, `AGENTS.md` (route
table, open items), this report.
