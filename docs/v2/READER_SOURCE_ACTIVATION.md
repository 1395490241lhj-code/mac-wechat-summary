# Reader source activation — explicit selection from the agent path

**Status: sealed phase report, 2026-09-06. Application plumbing only.** No real
WeChat data, no WeChat container, no external database, no credential or access
helper, no `sudo`, no macOS security setting, no change to WeChat. No external
reader is installed, downloaded, located, or executed by this repository. Every
test runs against a stub written into a temporary directory.

This report follows `READER_BOUNDARY_INTEGRATION.md` (`8c4138e`), which
introduced the `MessageSource` boundary. That report is sealed and unedited;
one statement in it is superseded here, noted at the end.

## What this adds

The boundary existed but only the bridge could be told which source to use. The
agent path could not ask, which meant the database source was unreachable from
a run. It is now reachable, and only ever on request.

`ClaudeConfig` gained four optional fields, all defaulting to `None`:

| Field | Meaning |
|---|---|
| `message_source` | `visual`, `database`, or unset |
| `reader_bin` | the external reader executable; injected, never searched for |
| `reader_config` | optional configuration file for the reader |
| `reader_timeout` | optional per-call timeout in seconds |

and `shadow/wechat_shadow_run.py` gained the matching `--message-source`,
`--reader-bin`, `--reader-config` and `--reader-timeout`, grouped in the help
output under a heading that states the default in words.

## Default behaviour: unchanged, by absence

A run that asks for nothing carries nothing. With no selection, the MCP server
environment is exactly the two variables it has always had:

```
WECHAT_COMPANION_ALLOW_AGENT_READ=1
WECHAT_COMPANION_DB_PATH=<the store>
```

and the generated configuration document does not mention a reader variable
anywhere. The visual store remains the default not because anything falls back
to it, but because no request was made of anything else.

## Explicit opt-in

`ClaudeConfig.source_env()` returns the activation variables for a deliberate
selection, and `mcp_config_document()` merges them into the bridge server's
environment. Selection is trimmed and lower-cased before it is matched.

Naming `visual` explicitly is allowed and is **not** the same as saying nothing:
it emits `WECHAT_COMPANION_MESSAGE_SOURCE=visual`, pinning the source against a
future change of default.

Activation variables go into the **bridge server's** environment only. They are
never placed in Claude Code's own environment, which is still built from scratch
with nothing from the operator's shell.

## Failing closed

The failure this design is built against is not a crash. It is a run that reads
the visual store while the operator believes they selected another source. Every
refusal below exists to prevent that specific outcome.

| Request | Result |
|---|---|
| `database` without a reader path | refused before the run starts |
| A reader path without a selection | refused; it would have read the visual store |
| An unrecognised source name | refused by the parser and again by the config |
| Either on the Hermes backend | refused |

The CLI validates by calling `source_env()` once while building the runner, so
an impossible request exits with a usage error rather than launching.

Omitting the variables on a failed request — the "safe-looking" alternative —
would have been the silent substitution itself.

## Variable names have one definition

The names live in `bridge/message_source.py`, which imports only the standard
library. The bridge reads them from there. The runner keeps its own copy, so it
has no import-time dependency on the bridge it launches as a subprocess, and a
test loads that module from its file and compares every name. Two copies of a
string that must agree are a drift risk; the guard is automated rather than
remembered.

## Status output

`status` reports the selected source name and a `reader_configured` boolean.
Availability is a name and a boolean: no configured location is ever disclosed,
and a test asserts the reader path and its directory appear nowhere in the
response.

## Gates at this commit

| Suite | Result |
|---|---|
| Bridge, pre-existing before the boundary | 34 passed, still unmodified |
| Bridge total | 72 passed |
| Shadow, pre-existing | 75 passed, unmodified |
| Shadow, new source-activation tests | 17 passed |
| Shadow total | 92 passed |

The load-bearing test configures **both** sources fully, with content in the
store, selects the database source, and breaks it three ways: absent executable,
malformed replies, and reader-reported failure. All three must fail, and no
stored row may appear in any response. A silent fallback would pass every other
test in the suite and fail only this one.

Composition across the process boundary was checked once by hand outside the
suites: the environment `ClaudeConfig` emits was handed to the real bridge in a
child process, which selected the database source and read through a stub
reader. The automated guard against the two halves drifting is the name
comparison test.

## Supersession

`READER_BOUNDARY_INTEGRATION.md` states that the database source is
"unreachable from the agent path unless someone deliberately adds a variable to
that allowlist. The runner needed no change and received none." That was correct
at `8c4138e`. The runner has now received exactly that change. The property it
described is preserved in a stronger form: the variables are absent unless
requested, and a request that cannot be honoured refuses to start.

## Scope

Unchanged by this work: no real database has been opened, macOS WeChat 4.1.13
cipher compatibility and real access-material availability remain pending,
Windows remains unknown, the licensing review of an external-process reader is
still owed, and there is no user interface for any of this. VisualReader remains
the shipped read path.
