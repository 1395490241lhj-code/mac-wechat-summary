# H4.6 — Privacy Failure-Path Gate (synthetic only)

H4.5 proved the **happy path**. This gate tests what happens when the provider
call **fails after** raw message content has already entered the request path.

**No real WeChat data was accessed.** Synthetic fixtures only. The digest skill,
the MCP bridge, the official Hermes install, and the H4/H4.5 reports were left
unmodified.

Runtime: **Hermes Agent v0.20.5 + upstream unmerged PR #94339**, Anthropic
native provider, `claude-sonnet-5`, isolated `HOME` + `HERMES_HOME`, and the
exact proposed H5 tool configuration (`-s wechat-digest -t wechat_companion`,
plus the inert canary during testing).

## Method

A unique token was embedded in one synthetic message, phrased as a leaked
credential. A local pass-through proxy forwarded every request to the real API
**except** the one whose body contained the token — that request returned
HTTP 500. This guarantees the failure lands *after* the content is in the
conversation history, not before.

The proxy confirmed the precondition on both runs:

```
{"calls": 18, "failed_on": 18, "saw_canary_in_body": true}
```

Hermes retried, exhausted retries, and aborted with exit 1.

Note: `model.base_url` is **ignored** by the native Anthropic provider; routing
requires the `ANTHROPIC_BASE_URL` environment variable.

## 1. Failure-path persistence matrix

| Location | Raw content on failure? |
|---|---|
| `$HERMES_HOME/sessions/request_dump_<sid>_*.json` | **YES — new on this path** |
| `$HERMES_HOME/state.db` (+`-wal`) | **YES** (same as happy path) |
| `logs/agent.log` | no |
| `logs/mcp-stderr.log` | no |
| `logs/errors.log` | no |
| `cache/` | no |
| Isolated `HOME` | no |
| Real `~/.hermes` | no |
| `/tmp`, `$TMPDIR` | no |
| Agent stdout | no (aborted before a digest was produced) |

Reproduced identically on two independent failure runs.

**The failure path is strictly worse than the happy path**: it adds a second
persistent location that H4.5's cleanup was never exercised against.

## 2. Can API-error request dumps contain raw content?

**Yes — verbatim.** The dump holds the full outgoing request body, including the
MCP tool result carrying message text:

```
reason: max_retries_exhausted
messages in dump: 7
  -> canary in messages[6] role=user
     context: ..."visible_time": "09:00", "text": "服务器口令 [CANARY] 千万别发群里"...
```

### Can it be disabled?

**No.** There are two error-path dump sites in `agent/conversation_loop.py`, and
**neither is gated**:

| Site | Reason | Gated? |
|---|---|---|
| `:3095` | `preflight` | yes — `env_var_enabled("HERMES_DUMP_REQUESTS")` |
| `:6162` | `non_retryable_client_error` | **no** |
| `:6503` | `max_retries_exhausted` | **no** |

`HERMES_DUMP_REQUESTS` only controls the opt-in preflight dump. The error dumps
are unconditional, and there is no config key or environment switch for them.
Disabling is therefore **not available**; cleanup is mandatory.

## 3. Cleanup mechanism (verified)

The aborted session **is** persisted (`_persist_session` runs on the failure
path), so it is addressable by session ID and can be cleaned:

| Step | Result |
|---|---|
| `hermes sessions delete <sid> --yes` | removes DB rows **and** that session's `request_dump_*.json` |
| `hermes sessions optimize` (FTS merge + VACUUM) | reclaims freed pages |

Verified on both failure runs: token fully absent from `$HERMES_HOME` afterwards,
with the dump file gone from `sessions/`.

**Both steps remain mandatory.** In H4.6 the token happened to disappear after
`delete` alone, but in H4.5 it survived `delete` in freed pages. That difference
is page-reuse timing, not a guarantee — so `optimize` stays required.

### Additional finding: orphaned dumps

Three dumps from earlier phases were still on disk with chat-content references,
while their sessions were **no longer in the store**:

```
20260824_233441_0954b3  in-store=0
20260824_234008_cdf3be  in-store=0
20260824_234011_65afae  in-store=0
```

`sessions delete` only removes the dump belonging to the session being deleted.
A dump that outlives its session is never reclaimed by any session command.

**Required policy for H5 must therefore be three parts, run immediately after
every run — success or failure:**

1. `hermes sessions delete <sid> --yes`
2. `hermes sessions optimize`
3. an explicit sweep of any remaining `$HERMES_HOME/sessions/request_dump_*.json`

Step 3 is not optional: it is the only thing that reclaims orphans, and an
aborted run is exactly the case most likely to produce one.

## 4. Deletion semantics — logical purge only

What was verified is an **application-level / logical purge**: the token is no
longer retrievable from the database, its indexes, or the dump files, as
observed by content search over `$HERMES_HOME`.

This is explicitly **not** a claim of forensic or physical secure erasure. Not
covered: filesystem free-space remnants after `unlink`, SSD wear-levelling and
TRIM behaviour, APFS snapshots or local Time Machine copies, Spotlight indexes,
swap/page files, and any backup that ran before cleanup. Treat the guarantee as
"purged from the application's own storage", nothing stronger.

## 5. Provider transmission boundary — explicit H5 assumption

Inference runs on **Anthropic native / `claude-sonnet-5`**. Producing a digest
therefore **requires transmitting real WeChat message content to Anthropic**.
Both canary runs confirm this directly: the token appeared in the outgoing
request body.

**This is not local-only processing.** Under H5 the boundary is:

- Raw message text leaves the machine on every digest.
- It is covered by the provider's API terms and retention policy, not by our
  local consent gate or cleanup policy.
- Local cleanup removes local copies **only** — it has no effect on anything
  retained provider-side.
- The prompt-caching observed in H4 (94% cache reads) implies provider-side
  retention of prompt content for the cache TTL.

This requires **explicit user acceptance before H5**, recorded as a decision
rather than assumed. No provider research or change was performed.

## 6. Digest output is derived real data

A digest built from real messages is itself real data — the H4.6 run showed the
model summarising a credential-like string even while declining to repeat it
verbatim, which is discretion, not a guarantee.

Recommendation for H5: **do not persist digest output to a normal file by
default.** Prefer review-only output (terminal/stdout, consumed and discarded).
Any on-disk retention needs an explicitly approved policy covering location,
lifetime, and deletion — the same standard applied to the session store.

## Status

**BLOCKED for real-data H5**, now on **two** conditions rather than one:

1. **Upstream** (unchanged): PR #94339 still open; stock-runtime A + J smoke
   remains the prerequisite.
2. **New from H4.6**: the three-part cleanup policy above must be wired into the
   H5 runner as a mandatory post-step for **both** success and failure paths,
   and the provider-transmission assumption (§5) plus the digest-retention
   decision (§6) must be explicitly accepted.

The failure path itself is **understood and containable** — raw content lands in
exactly two known locations, both verifiably purgeable — but it is not yet
*contained*, because nothing enforces the cleanup automatically today.
