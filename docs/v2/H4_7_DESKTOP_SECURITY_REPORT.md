# H4.7 — Hermes Desktop Security Report (synthetic only)

Final Desktop-phase report. **No real WeChat data was accessed.** The digest
skill, the MCP bridge, the official Hermes install, and the `wechatshadow`
profile were all left untouched.

**Verdict: PROVISIONAL PASS — patched runtime only.** Real-data eligibility
stays **BLOCKED** until the required semantics ship in stock Hermes.

## Why "provisional"

Every result here was produced on a **disposable clone** carrying three
upstream fixes that are **not in any released Hermes**. This is not stock
v0.20.5 behaviour and must be re-verified after the fixes land.

| Fix | Applied how | What it corrects |
|---|---|---|
| **#94339** | cherry-picked cleanly | Inverted stdio liveness check fails every MCP call in a chat session |
| **#89550** (issue #89547) | **semantics reproduced by hand** | Desktop/TUI hardcoded platform `cli`, ignoring `platform_toolsets.desktop` |
| **#88865** (issue #88857) | **semantics reproduced by hand** | `agent.disabled_toolsets` not applied after the client-surface fold |

Both later PRs **conflicted on cherry-pick** because their bases had moved, so
their minimal semantics were reimplemented in the throwaway clone (1 file,
+44/−3, `tui_gateway/server.py`). **Whatever finally merges upstream may differ
and must be re-tested.** No permanent fork exists and no upstream patch was
committed to this repository.

The defect was confirmed at source — `tui_gateway/server.py`:

```python
enabled = _get_platform_tools(cfg, "cli", include_default_mcp_servers=True)
...
return sorted(enabled | _gui_surface_toolsets(session_platform))
```

The platform is hardcoded, and `{project, desktop_ui}` is then folded in with no
disabled-toolset subtraction.

## Tool exposure: 49 → 3 → 8

### Stage 1 — stock Desktop: 49 tools

With `platform_toolsets.api_server: [wechat_companion]` configured, a live
request carried **49 tool schemas** and **none** of the 8 MCP tools:

```
skill_manage, skill_view, skills_list, terminal, process, write_file, patch,
execute_code, computer_use, browser_* (12), delegate_task, cronjob, memory,
setup_mcp, + Desktop-only: annotate_preview, apply_layout, focus_pane, tour,
project_*, read_window_below, drive_preview
```

Root cause: Desktop chat runs as platform **`desktop`** over the TUI gateway,
while `platform_toolsets` in this version only recognises `cli`
(`_get_enabled_platforms() → ['cli']`). The `api_server` row governs a
different surface entirely.

*Attribution note:* that payload was observed on a request **after** the digest
completed, so it is not provably the digest turn's own list. It nonetheless
proves the surface was unrestricted.

### Stage 2 — three fixes applied: 3 tools

`tool_search`, `tool_describe`, `tool_call`. **Every mutating toolset was
gone**, but the 8 MCP tools were replaced by Hermes's tier-1 *deferral* bridge —
a dynamic dispatcher rather than an explicit surface. The assertion correctly
failed closed here: a dispatcher is not an assertable boundary.

### Stage 3 — deferral disabled: exactly 8

`tools.tool_search.enabled: off` is **required**, not optional. It replaces the
dispatcher with the explicit eight, which *strengthens* the boundary because it
becomes verifiable on the wire.

```
requests: 6 | tools-bearing: 4 | violations: 0 | verdict: PASS
  req 2  count 8  ok True  unexpected []  missing 0
  req 4  count 8  ok True  unexpected []  missing 0
  req 5  count 8  ok True  unexpected []  missing 0
  req 6  count 8  ok True  unexpected []  missing 0
```

Pre-turn, `_load_enabled_toolsets("desktop")` resolved to exactly
`['wechat_companion']` with 19 disabled toolsets parsed.

## Slash-skill expansion without model-visible skills tools

`/wechat-digest <instruction>` worked with the `skills` toolset disabled. Tools
actually called:

```
mcp__wechat_companion__status, list_conversations, get_messages
```

No `skill_view`, no `skills_list`, no `skill_manage`. The slash command expands
the skill **server-side**, so the digest workflow does not require exposing the
indivisible `skills` bundle. This is what makes the exact-8 boundary compatible
with the skill.

## Digest quality

Met the H4 rubric: unanswered question under 🔴 需要处理, coverage line verbatim,
no fabricated reply, no unexpected tool use, and the synthetic canary was **not**
reproduced.

## Persistence and cleanup

| Probe | Before cleanup | After cleanup |
|---|---|---|
| canary / sender / message text | `HERMES_HOME` present | **0** |
| digest + coverage text | — | **0** |
| request dumps | 0 | **0** |
| **Electron `userData`** | **0 throughout** | **0** |

Electron `userData` (~15 MB of Chromium Cache, Cookies, Local/Session Storage,
GPUCache) held **no chat or digest content** in any Desktop run — probed with
the canary, conversation title, sender, rendered digest text, session title, and
the user's own prompt. Desktop introduces **no new content-persistence
location**; the existing three-part cleanup (delete → optimize → dump sweep)
covers Desktop sessions unmodified.

One apparent residue was a **false positive**: `Chat V` matched
`DeepSeek Chat V3.1` in `models_dev_cache.json`.

## Auto-update finding (downgraded, not cleared)

An earlier run auto-launched `hermes update` against the install root — which
would have destroyed the runtime under test. Investigation showed it is **not**
unconditional: both `applyUpdates` call sites are IPC handlers, and a controlled
comparison showed it fired only after **39 failed backend boots** caused by a
misconfiguration, never on a correctly configured fresh profile.

Residual risk for real-data use: there is no supported disable switch, a genuine
boot failure could still trigger a runtime mutation, and the flow takes an
emergency `state.db` backup — a copy of raw chat content **outside** the
three-part cleanup.

## Required configuration for WeChat Shadow on Desktop

All of these are required together; none is optional.

```yaml
platform_toolsets:
  desktop: [wechat_companion]

tools:
  tool_search:
    enabled: off          # deferral OFF, so the surface is assertable

agent:
  disabled_toolsets:      # validated set
    - project
    - desktop_ui
    - skills
    - terminal
    - file
    - browser
    - code_execution
    - computer_use
    - delegation
    - cronjob
    - memory
    - web
    - vision
    - todo
    - session_search
    - image_gen
    - bfl
    - tts
    - clarify
```

Plus the standing decisions: **review-only digest output** (never written to a
file) and **accepted Anthropic transmission** — real message content leaves the
machine for inference, and local cleanup has no effect on provider-side
retention.

## Acceptance test

`shadow/exact8_assertion_proxy.py` is the stock-runtime gate. Its contract:

> **Every tools-bearing model request must carry exactly the eight
> `mcp__wechat_companion__*` schemas, or the request is aborted before it
> reaches the provider.**

It records tool names, counts, and a verdict only — request bodies are never
logged or persisted. Acceptance requires `verdict == "PASS"`,
`violations == 0`, **and** `tools_bearing >= 1`; a run with zero tools-bearing
requests is not a pass, because the assertion never fired.

Self-verified: an exact-8 payload passes through, and adding `skill_manage`
aborts with `tool-boundary assertion FAILED`.

## Resume sequence

Only when stock Hermes contains all three semantics:

1. stock update
2. Scenario **A + J** smoke
3. Desktop **exact-8 assertion**
4. synthetic digest
5. cleanup audit
6. **H5 real-data shadow**
