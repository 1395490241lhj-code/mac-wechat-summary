# WeChat shadow-mode runner

Produces one read-only digest from messages WeChat Companion has already
stored, then removes every local trace of the run.

**Status: synthetic-only; the Claude backend has passed its H6 synthetic gates
and is eligible for H5.** No backend has been pointed at real WeChat data yet.
See *Remaining gates* below.

## Layout (since H6)

| File | Role |
|---|---|
| `wechat_shadow_run.py` | CLI entry point. `--agent-backend hermes\|claude` |
| `agent_runner.py` | Runtime-neutral orchestration: preflight → boundary → digest → cleanup, signals, timeout, exit codes, review-only stdout |
| `runners/hermes.py` | `HermesRunner` — the original Hermes Agent CLI backend, behaviour-identical to the pre-H6 runner |
| `runners/claude.py` | `ClaudeRunner` — Claude Code headless (`claude -p`); H6 gates passed, eligible for H5 |
| `tool_boundary_proxy.py` | Exact-N wire verifier: every tools-bearing model request must carry exactly the expected tool names |
| `exact8_assertion_proxy.py` | The Hermes instance of that verifier (the eight names Hermes v0.20.5 puts on the wire). Unchanged contract |
| `h6_synthetic_gate.py` | H6 gate: Scenario A + J on the Claude backend, then a canary persistence audit |
| `tests/` | Subprocess-faked unit tests for the driver, both runners and the proxy |

The digest policy is **not** here. It is `.hermes/skills/wechat-digest/SKILL.md`,
which every backend consumes unmodified — Hermes by preloading it (`-s`),
Claude by passing its bytes verbatim as the system prompt.

## Why this exists as code

H4.5 and H4.6 established two boundaries that cannot be maintained by
instruction alone:

- **Tool boundary.** A runtime's configuration is not evidence of what it sends.
  The effective tool surface is asserted before any message is read — from the
  init log for Hermes, on the wire for Claude.
- **Privacy boundary.** Raw message content lands wherever the runtime persists
  it. For Hermes that is the session store on every run plus an undeletable
  `request_dump_*.json` on provider failure. Cleanup is therefore mandatory,
  and a manual instruction is not good enough.

Both are enforced here, in the runner.

## Guarantees (all backends)

| Property | How it is enforced |
|---|---|
| Read-only tools | Effective tool set asserted against an exact allowlist **before any message is read**; any unexpected or missing tool aborts the run |
| Clean start | Preflight detects residue from a previous run, purges it, and **refuses to proceed** unless a clean state is demonstrated |
| Clean finish | `finally` block purges on success, ordinary failure, timeout, abort, and Ctrl+C |
| Kill recovery | A `SIGKILL`/power loss skips the finally block; the **next preflight** detects and recovers that residue before reading anything new |
| Review-only output | The digest goes to stdout; it is never written to a file |
| No ambient config | The child environment is built from scratch, so shell credentials, `ANTHROPIC_BASE_URL` or a parent Claude Code session cannot alter the run |

Exit codes: `0` digest produced · `1` run failed (including timeout) · `2`
boundary/preflight abort · `3` cleanup failed · `130` interrupted. Cleanup is
attempted in every case.

Not enabled, by construction: cron, unattended execution, messaging sends,
terminal, filesystem, browser, computer use, memory, skills runtime tools.

## Backend: hermes (original, optional)

Exactly eight tools on the wire, all read-only:

```
mcp__wechat_companion__status
mcp__wechat_companion__list_conversations
mcp__wechat_companion__get_messages
mcp__wechat_companion__get_recent_messages
mcp__wechat_companion__list_resources     (MCP primitive; inert — bridge declares none)
mcp__wechat_companion__read_resource      (MCP primitive; inert)
mcp__wechat_companion__list_prompts       (MCP primitive; inert)
mcp__wechat_companion__get_prompt         (MCP primitive; inert)
```

The skill is preloaded with `-s` and no `skills` tool is exposed, because the
Hermes `skills` toolset is indivisible (H4.5). Cleanup is three parts, all
required: `sessions delete <sid> --yes` → `sessions optimize` (VACUUM) → sweep
leftover `request_dump_*.json`.

```bash
python3 shadow/wechat_shadow_run.py --agent-backend hermes \
  --hermes-entry  <path to the hermes entry script> \
  --python        <interpreter with Hermes's dependencies> \
  --hermes-home   <dedicated shadow profile home> \
  --project-dir   <repo containing .hermes/skills/wechat-digest> \
  --isolated-home <throwaway HOME for the child process>
```

A dedicated profile keeps shadow runs away from `default`:
`hermes profile create wechatshadow --no-alias` (no `--clone`).

**Stock Hermes v0.20.5 cannot run this backend** (PR #94339, inverted stdio
liveness check). Since H6 that no longer blocks the project: Hermes is an
optional backend, re-validated if and when a fixed stock release ships.

## Backend: claude (H6 gates passed; eligible for H5)

`claude -p` with the narrowest configuration the installed CLI (2.1.x) supports:
`--tools ""` (no built-in tool), `--strict-mcp-config --mcp-config <isolated
json>` (the bridge and nothing else), `--allowedTools <bridge tools>`,
`--permission-mode dontAsk`, `--no-session-persistence`, `--setting-sources ""`,
an empty working directory (no `CLAUDE.md`), and `--system-prompt <SKILL.md
bytes>`.

None of that is trusted. The runner starts `tool_boundary_proxy.py` in-process
and routes the CLI through it via `ANTHROPIC_BASE_URL`. A **probe** turn runs
first with the proxy refusing to forward anything: the actual `tools[]` array
is recorded and compared with the four bridge tools, with zero provider
traffic and no message read. Only on an exact match does the digest turn run,
with every subsequent request enforced.

Expected on the wire (verified, not assumed):

```
mcp__wechat_companion__status
mcp__wechat_companion__list_conversations
mcp__wechat_companion__get_messages
mcp__wechat_companion__get_recent_messages
```

`HOME`, `CLAUDE_CONFIG_DIR` and the working directory all live inside
`--isolated-home`, which the run owns outright. "Clean" means that directory
holds no files; preflight and cleanup remove its contents and prove it.

```bash
python3 shadow/wechat_shadow_run.py --agent-backend claude \
  --claude-bin    <path to claude> \
  --python        <interpreter that can import mcp, for the bridge> \
  --bridge        bridge/wechat_companion_mcp.py \
  --db-path       <SYNTHETIC fixture .sqlite> \
  --isolated-home <throwaway directory owned by the run> \
  [--skill .hermes/skills/wechat-digest/SKILL.md] [--model claude-sonnet-5] \
  [--env ANTHROPIC_API_KEY=...]
```

The child environment is built from scratch, so the CLI's keychain login is
**not** visible to it (Claude Code keys keychain credentials per config
directory). Credentials enter by **name only**, through an allowlist:

```bash
export CLAUDE_CODE_OAUTH_TOKEN="$(claude setup-token)"   # in your own shell, never pasted anywhere
python3 shadow/wechat_shadow_run.py --agent-backend claude ... --pass-env CLAUDE_CODE_OAUTH_TOKEN
```

Or, for unattended-by-the-agent runs, store the token once in the macOS
keychain with an interactive prompt (`-w` last, so it never enters argv or
history) and let the runner read it at runtime:

```bash
security add-generic-password -a "$USER" -s wechat-shadow-claude-oauth -U -w
python3 shadow/wechat_shadow_run.py --agent-backend claude ... --claude-oauth-from-keychain
```

The keychain source is fixed to that one service, the current user, and the
`CLAUDE_CODE_OAUTH_TOKEN` destination; nothing else can be requested.
`--pass-env` copies the named variable from the parent environment into the
child environment and nowhere else. `--env NAME=value` refuses credential
names, because argv and shell history are not private. The runner never logs,
prints, persists or reports the value. `--bare` is deliberately **not** used:
it would restrict auth to `ANTHROPIC_API_KEY`; the same isolation is enforced
flag by flag (`--setting-sources ""`, `--settings '{"autoMemoryEnabled":false}'`,
`--disable-slash-commands`, `--strict-mcp-config`, `--tools ""`, `--no-session-persistence`,
`CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`, `CLAUDE_CODE_DISABLE_BUNDLED_SKILLS=1`,
`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`, `CLAUDE_CODE_DISABLE_WORKFLOWS=1`).

### H6 gate

```bash
python3 shadow/h6_synthetic_gate.py --claude-bin ~/.local/bin/claude \
  --python <interpreter with mcp> --work <empty dir> --report <path.json> \
  --pass-env CLAUDE_CODE_OAUTH_TOKEN
```

Runs Scenario A and J from the skill's synthetic fixtures, then a canary run
that inventories what Claude Code left in the isolated home, searches it and
the real `~/.claude` for the token (read only), and finally purges and proves
clean. The report holds names, paths, byte counts and verdicts — never content.

## Remaining gates before real data

1. **Claude backend, live synthetic gates — passed** (Scenario A + J, exact
   four-tool boundary on the wire, canary audit clean). See
   `docs/v2/H6_AGENT_RUNNER_DECOUPLING.md`. Next is H5 on this backend.
2. **Deletion semantics** — cleanup is an application-level purge, verified by
   content search. No forensic or physical erasure is claimed.
3. Hermes: stock-release re-validation is now **optional**, not a gate.

## Accepted privacy boundaries

Decided, not open questions — see `docs/v2/H5_PRIVACY_DECISIONS.md`:

- **Provider transmission (accepted).** Real WeChat message content is
  transmitted to Anthropic / Claude for inference. Both current backends use
  Anthropic; a backend on another provider is a new decision.
- **Digest retention: review-only.** Output is derived real data and is
  printed to stdout, never written to a file.

## Memory MCP (M2.1) — off by default

`--agent-backend claude --memory --memory-server memory/wechat_memory_mcp.py`
adds a second, separate, read-only MCP server over the **app-owned canonical
store** — no path is required (`--memory-db-path` remains a test/debug
override) — (five tools:
`memory_conversations`, `memory_search`, `memory_timeline`, `memory_context`,
`memory_recent`) and changes the expected wire set from exactly four tools to
exactly nine. The request is validated before
the run (server present, path given, app consent state allowing, store readable
at a supported version) and refuses to start otherwise — never a silent
four-tool run. See `docs/v2/M2_1_MEMORY_MCP_GATE.md`.
