# Reader boundary — a runtime-neutral message source in the MCP bridge

**Status: sealed phase report, 2026-09-06. Architecture and integration only.**
No real WeChat data, no WeChat container, no credential or access helper, no
`sudo`, no macOS security setting, no change to WeChat. No external reader is
installed, downloaded, located, or executed by this repository. Every test in
this phase runs against a stub written into a temporary directory.

## What changed

The four MCP tools no longer read SQLite directly. They ask a `MessageSource`,
and the store the macOS app fills from the visual capture path is one such
source. A second source can now exist without any tool, the agent runner, or
the digest skill changing.

```
MessageSource
├── StoreMessageSource   the visual capture path, behaviour unchanged
└── RionReaderAdapter    an external reader process, optional
```

Both produce `NormalizedMessage` and `NormalizedConversation`, which are the
store's existing field set rather than a parallel model. A parallel model would
have pushed the difference between readers all the way out to the skill.

## The protocol

`bridge/message_source.py` defines four questions and nothing else: `status`,
`list_conversations`, `get_messages`, `get_recent_messages`. It imports only
`dataclasses`, `typing` and `__future__`. It names no vendor, no transport, no
codec and no WeChat schema, and a test enforces that against the module's
imports and identifiers rather than its prose.

There is deliberately no query method, no path argument, and no way for a
source to widen the tool surface.

## Provenance and coverage

`NormalizedMessage` carries a `source` field. `NormalizedMessage.payload()`
deliberately omits it, so the per-message wire shape an agent sees is
byte-identical to the one the bridge has always returned. Provenance reaches a
client on the **response envelope** instead, where it describes the whole
answer rather than each row, and where it cannot be mistaken for a per-message
attribute the store never had.

Two fields mean different things per source, and that is the reason the
envelope names the source at all:

| Field | `visual` | `database` |
|---|---|---|
| `first_observed_at` | when the message was first seen on screen | the message's own stored creation time |
| `visible_time` | the string WeChat drew on screen | `None` — a formatted time here would look like something the user saw |
| `confidence` | the extractor's estimate | `1.0` — a decoded row is exact |
| `first_seen_at` (conversation) | when this machine first saw the chat | `None` — not known by a database read |

## Failing closed

There is no fallback between sources in either direction. A quiet substitution
would present one reader's coverage as the other's, which is the single failure
this boundary exists to prevent.

| Condition | Result |
|---|---|
| No source configured | The visual store, exactly as before |
| Unrecognised source name | `source_unknown`, no read |
| Database source without the agent opt-in | `agent_read_disabled` |
| Database source without an injected executable path | `reader_not_configured` |
| Executable absent or not executable | `reader_unavailable`; the visual path is unaffected |
| Reply is not JSON, or is JSON in an unexpected shape | `reader_malformed_response`, nothing ingested |
| One row in an otherwise valid reply is malformed | the whole reply is refused |
| Reader reports its own failure | its error code, but only if it matches a strict safe-token pattern |
| Reader exits non-zero while claiming success | `reader_error` |
| Backwards paging by sequence | `unsupported_paging` — refused, not approximated |

The last row is a real coverage gap kept visible on purpose. The external
reader pages on its own message identifiers, which are not the ordering key
published as `sequence`; paging on a key that merely resembles the right one
would return a plausible, wrong window.

`get_recent_messages` on the database source sweeps a bounded set of recent
conversations because the reader exposes no single cross-conversation query
this adapter can express faithfully. The bound is a documented limit, not a
total.

## The external-process boundary

`bridge/rion_reader_adapter.py` vendors no reader source, links no reader
library, imports nothing from a reader, and copies no reader implementation. It
contains no key handling, no cipher parameter, no WeChat schema, no
process-memory access, and nothing that could modify an installed application;
a test asserts the absence of those tokens in the module. No shell is used: the
argument vector is passed directly.

The executable path is **injected** and never searched for. Reader stdout and
stderr may contain chat content or filesystem paths, so neither is ever placed
in an exception, a log line, or a return value.

**Licensing.** The reader is an optional program the operator installs
themselves, under its own licence. Running it in a separate process with a
documented data contract *reduces* coupling risk compared with vendoring or
linking it. That is an engineering statement, not a legal conclusion, and it
does not by itself resolve any licensing obligation.

## Selection

| Variable | Meaning |
|---|---|
| `WECHAT_COMPANION_MESSAGE_SOURCE` | absent or `visual` (default), or `database` |
| `WECHAT_COMPANION_READER_BIN` | the reader executable path; never discovered |
| `WECHAT_COMPANION_READER_CONFIG` | optional reader configuration path |
| `WECHAT_COMPANION_READER_TIMEOUT` | optional seconds, default 30 |

The agent-read opt-in gates every source, not just the store.

`ClaudeRunner` builds an explicit minimal environment containing only
`WECHAT_COMPANION_ALLOW_AGENT_READ` and `WECHAT_COMPANION_DB_PATH`. The
database source is therefore **unreachable from the agent path** unless someone
deliberately adds a variable to that allowlist. The runner needed no change and
received none.

## Gates at this commit

| Suite | Result |
|---|---|
| Bridge, pre-existing | 34 passed, unchanged and unmodified |
| Bridge, new boundary and adapter tests | 33 passed |
| Bridge total | 67 passed |
| Shadow (agent runner, Claude runner, Hermes runner, tool boundary, credentials) | 75 passed, unchanged |

The tool surface is asserted to be exactly `status`, `list_conversations`,
`get_messages`, `get_recent_messages`. The message payload key set is asserted
identical for both sources.

## Scope of the evidence

The adapter is tested against **recorded reply shapes**, including the
WAL-resident, zstd-compressed row shape observed in the sealed synthetic
interface gate. That proves this adapter normalises what that reader emits. It
does **not** prove anything new about real WeChat databases: macOS 4.1.13
cipher compatibility and real access-material availability remain pending, and
Windows remains unknown. VisualReader remains the shipped read path.
