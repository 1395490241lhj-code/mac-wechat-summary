# WeChat shadow-mode runner

Produces one read-only digest from messages WeChat Companion has already
stored, then removes every local trace of the run.

**Status: synthetic-only.** This has not been pointed at real WeChat data. See
*Remaining gates* below.

## Why this exists as code

H4.5 and H4.6 established two boundaries that cannot be maintained by
instruction alone:

- **Tool boundary.** Hermes cannot expose `skill_view` without the mutating
  `skill_manage` — the `skills` toolset is indivisible. So the digest skill is
  *preloaded* with `-s` and no `skills` tool is exposed at runtime.
- **Privacy boundary.** Raw message content lands in the Hermes session store
  on every run, and additionally in a `request_dump_*.json` whenever the
  provider call fails. The error dump cannot be disabled. Cleanup is therefore
  mandatory, and a manual instruction is not good enough.

Both are enforced here, in the runner.

## Guarantees

| Property | How it is enforced |
|---|---|
| Read-only tools | Effective tool list asserted against an exact allowlist **before any message is read**; any unexpected or mutating tool aborts the run |
| Clean start | Preflight detects stale sessions and orphan dumps, purges them, and **refuses to proceed** unless a clean state is demonstrated |
| Clean finish | `finally` block purges on success, ordinary failure, abort, and Ctrl+C |
| Kill recovery | A `SIGKILL`/power loss skips the finally block; the **next preflight** detects and recovers that residue before reading anything new |
| Review-only output | The digest goes to stdout; it is never written to a file |
| No ambient config | The child environment is built from scratch, so shell credentials or `ANTHROPIC_BASE_URL` cannot alter the run |

Not enabled, by construction: cron, unattended execution, messaging sends,
terminal, filesystem, browser, computer use, memory, skills runtime tools.

## Effective tool surface

Exactly eight, all read-only:

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

## Cleanup, in three parts

All three are required:

1. `sessions delete <sid> --yes` — removes DB rows and that session's dump
2. `sessions optimize` — VACUUM; deletion alone left content in freed pages
3. sweep leftover `request_dump_*.json` — the only thing that reclaims orphans,
   which a session delete never touches

## Usage

```bash
python3 shadow/wechat_shadow_run.py \
  --hermes-entry  <path to the hermes entry script> \
  --python        <interpreter with Hermes's dependencies> \
  --hermes-home   <dedicated shadow profile home> \
  --project-dir   <repo containing .hermes/skills/wechat-digest> \
  --isolated-home <throwaway HOME for the child process>
```

Exit codes: `0` digest produced · `1` run failed · `2` boundary/preflight abort
· `3` cleanup failed · `130` interrupted. Cleanup is verified in every case.

## Profile separation

A dedicated Hermes profile keeps shadow runs away from `default` and away from
the synthetic evaluation environment:

```bash
hermes profile create wechatshadow --no-alias
```

Created without `--clone`, so no synthetic `state.db`, sessions, logs, request
dumps, or canary artifacts are inherited. Hermes Desktop lists it alongside
`default`, so the two can be selected independently.

Note Hermes warns that a profile with no keys "will inherit keys from your
shell environment". The runner does not rely on that: it constructs the child
environment explicitly.

## Remaining gates before real data

1. **Upstream** — PR #94339 is still open. Stock Hermes fails every stdio MCP
   call in a chat session, so the stock-runtime Scenario A + J smoke remains
   the prerequisite.
2. **Provider transmission** — inference is Anthropic native, so real message
   content necessarily leaves the machine on every digest. This is an explicit
   privacy assumption requiring acceptance; local cleanup has no effect on
   provider-side retention.
3. **Digest retention decision** — output is derived real data. A synthetic run
   showed the model reproducing a credential-like string verbatim in its
   digest, so redaction cannot be assumed. Review-only is the default; any
   on-disk retention needs an explicitly approved policy.
4. **Deletion semantics** — cleanup is an application-level purge, verified by
   content search. No forensic or physical erasure is claimed.
