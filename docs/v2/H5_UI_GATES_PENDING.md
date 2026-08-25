# H5 UI Gates — Pending (Hermes Desktop)

Hermes Desktop validation is **deferred until upstream PR #94339 lands in a
stock Hermes release**. No Desktop build was performed and no Desktop
validation is claimed.

## Why deferred

Desktop is an Electron app with no build present (`apps/desktop/dist` and
`release` absent, patched clone has no `node_modules`, Electron binary not
downloaded). Building costs roughly 1.5 GB plus a network download.

That cost buys nothing durable right now:

- Desktop inherits the #94339 defect — every stdio MCP call in a chat session
  fast-fails on stock v0.20.5 — so a meaningful test today would have to run
  against the patched clone.
- Any patched-runtime result would have to be repeated on stock Hermes anyway
  once the fix ships.
- `apps/desktop/dist` and `release` are **not** gitignored, so building inside
  the official install would dirty a checkout deliberately kept clean since
  H2.1.

## Findings recorded (established by source inspection, not by running Desktop)

### 1. Electron `userData` is a persistence boundary outside `HERMES_HOME`

Desktop uses Electron `getPath('userData')` and `app.setPath('userData', …)`.
That directory holds Chromium/WebView storage — Cache, Local Storage, IndexedDB,
Cookies, Session Storage, GPUCache.

The H5 runner cleans `HERMES_HOME` only: sessions, `state.db` (+WAL/FTS), and
`request_dump_*.json`. **Electron `userData` is not covered by it.**

Whether anything sensitive actually lands there is **unverified** and needs an
empirical canary run.

**Gate:** future Desktop validation must launch with an isolated
`HERMES_DESKTOP_USER_DATA_DIR` and audit that directory with a synthetic
privacy canary, exactly as H4.5/H4.6 did for `HERMES_HOME`. Desktop is not
eligible for real data until that audit exists and any leak found is covered by
cleanup or per-run sandboxing.

### 2. Tool-selection parity is not established

The H5 runner enforces the read-only boundary structurally: it asserts the
effective tool list against an exact eight-tool allowlist **before any message
is read**, and fails closed on anything unexpected or mutating.

Desktop has its own tool-selection UI and **no equivalent assertion**. Nothing
currently demonstrates that a Desktop session cannot enable the `skills`
toolset — which is indivisible and therefore carries the mutating
`skill_manage` (H4.5) — or any other toolset.

**Gate:** the real-data Desktop path must not be approved unless the same exact
eight-tool read-only boundary can be **structurally enforced, or asserted before
any WeChat message is read**. A GUI checkbox state alone is not sufficient
evidence.

### 3. Isolation and credential posture

`wechatshadow` stays **clean and credential-free**: no `state.db`, 0 sessions,
0 logs, 0 `.env` assignments. No synthetic state, canary artifact, session,
log, or request dump has been migrated into it.

Credential options were surveyed but **nothing was migrated**. Preference order
preserving the rule that real `~/.hermes` should not hold an unnecessary
plaintext credential:

1. External secret manager (`hermes secrets bitwarden|onepassword`) — pulls at
   startup instead of storing in `~/.hermes/.env`.
2. Shell-environment injection at launch (e.g. read from the macOS Keychain at
   launch time) — never written to disk.
3. Pooled credentials (`hermes auth add` → `auth.json`) — managed, still on disk.
4. Profile `.env` plaintext — avoid.

macOS note: `HERMES_DESKTOP_PASSWORD_STORE` is Linux-only (a Chromium
keychain-backend switch). On macOS, Electron `safeStorage` uses the OS Keychain
natively, but the code path applies to remote gateway tokens, not the provider
API key — so it is not an automatic answer for the Anthropic credential.

### 4. Published installer is not a substitute

The currently published Desktop installer must **not** be installed or tested
as a stand-in for current-source validation. It is a different artifact from the
runtime under evaluation.

## Sequence when #94339 is merged and available in stock Hermes

1. Update stock Hermes.
2. Run the Scenario **A + J** smoke on the stock runtime (the standing gate).
3. Perform **one** Desktop build/validation against the **stock** runtime.
4. Audit both `HERMES_HOME` **and** Electron `userData` with a synthetic canary.
5. Verify Skill + MCP + the exact eight-tool boundary on synthetic data.
6. Only then consider Desktop eligible for H5 real-data shadow use.

Until the upstream gate changes, no further Desktop work is scheduled. The CLI
shadow runner remains the only validated path, and it is itself still blocked on
the same upstream fix plus the provider-transmission and digest-retention
decisions recorded in H4.6.
