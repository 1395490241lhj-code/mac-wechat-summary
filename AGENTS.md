# mac-wechat-summary — agent bridge

Applies to **Claude Code and Codex alike**. Read this before doing anything else
in this repository. It is the one shared instruction source; there is no
agent-specific second copy.

---

## 1. Source of truth

1. **Repo + source + tests + runtime / experiment evidence** — implementation
   and experimental truth. If code and memory disagree, code wins.
2. **Obsidian project memory** — canonical memory for current state,
   architecture, experiments, findings, constraints, rejected/superseded
   approaches, decisions, and next actions.
3. **Memorix / model memory** — session handoff and scratch **only**.

**For this project, this repo-level rule overrides generic user-level
"Memorix-first" / retrieval-boundary instructions.** A Memorix brief is not the
retrieval boundary here and never outranks the vault; it is known to lag this
project's real state. Read the vault. (No global Claude or Codex configuration
is modified by this file — the override is scoped to this repository.)

### Vault location

```
${MAC_WECHAT_SUMMARY_VAULT:-$HOME/Documents/Obsidian Vault/10 Projects/mac-wechat-summary}
```

The vault counts as **available** only when all of these hold:

- the directory exists
- `Project.md` exists
- `Current Status.md` exists
- the project identity matches this repo (`mac-wechat-summary`)
- `Current Status.md` contains a `head_commit:` field

Any failure → **degraded mode** (§10).

---

## 2. Always read first, then route

Before meaningful work, read in this order:

1. `Project.md`
2. `Current Status.md`
3. `Next Actions.md`

Then read only what the task needs. **Do not read all ten notes by default.**

| Task | Also read |
|---|---|
| Capture / app / permissions / architecture | `Architecture.md`, `Constraints.md`, cited Decisions and Findings |
| Experiment / R&D | `Experiments.md`, `Findings.md`, `Rejected Approaches.md`, `Constraints.md`, cited Decisions |
| Hermes / MCP / agent safety | `Architecture.md`, `Findings.md`, `Constraints.md`, relevant Experiments and Decisions |
| Historical / provenance | `Sources.md` **as an index**, plus the specific `E-*` and commits it names |

### Reopening a previously closed route — mandatory reading

Before proposing, retrying, or arguing about any route the project has already
closed, you **must** read:

- the matching `E-*` in `Experiments.md`
- the matching `R-*` in `Rejected Approaches.md`, **including its reopening
  criteria**
- the governing `D-*` in `Decisions.md`

**Never reopen a closed route from memory, intuition, or a summary alone.**

---

## 3. Freshness — check both dimensions, every session

```
git log --oneline <head_commit>..HEAD              # committed drift
git status --porcelain=v1 --untracked-files=all    # working-tree drift
```

`head_commit` in `Current Status.md` is the **only** vault-wide reconciliation
anchor. It means: *the committed repository state through which canonical
project memory has been reconciled.*

**`head_commit == HEAD` does NOT mean memory is fully current.** The working
tree is a second, independent dimension.

Classify every working-tree difference before drawing conclusions:

| Class | What it covers | Consequence |
|---|---|---|
| **Product-relevant** | shipped behaviour, defaults, UI, persistence, permissions, provider calls, safety/security boundary | disclose; inspect; canonical memory is **stale for that uncommitted work** |
| **Tooling / local-only** | editor settings, local permissions or configuration, formatting, other non-product local state | disclose; does not automatically invalidate product state |
| **Experimental / scratch** | disposable probes | disclose; does **not** automatically become project memory — create or update an `E-*` only if it answered a material question |
| **Uncertain** | anything you have not actually opened | inspect before classifying |

**Never classify by filename alone.**

Hard rules:

- Never report the tree as clean when it is not.
- Never advance `head_commit` to uncommitted work.
- Never stage, restore, normalise, or overwrite pre-existing dirty work in order
  to make the memory state look tidy. **Disclosure is the remedy, not cleanup.**

---

## 4. Branch awareness

This project has material work spread across more than one branch, including a
historical generation on `main`. Treat topology as **live data**, not as
something memorised from a summary.

1. Run `git branch --show-current` before any cross-branch reasoning.
2. Read the branch topology recorded in `Project.md` / `Current Status.md`.
3. **Verify that topology against Git before acting on it** — commit counts and
   merge bases move.
4. The digest skill lives at `.hermes/skills/wechat-digest` on `v2/rewrite`
   since merge `fcbacbd` (provenance: `h3/wechat-digest` @ `5b1e7a8`). Read it
   from HEAD; do not look to the `h3` branch for it.
5. To inspect a file on another branch, prefer `git show <branch>:<path>`. Do
   not casually switch branches just to look at something.
6. **Neither branch is automatically the merge winner.**
7. Integration direction is a product/technical decision, not an implicit
   default. It is not made by this file.
8. Before claiming an end-to-end workflow exists, state which branch — or which
   reconciled state — supplies each stage.

---

## 5. Generation boundary — v1 vs v2

**v1** — historical Python generation (`main`; `app.py`, `core/`, `ai/`,
`mcp_server.py`, `c_src/`). Decrypts the WeChat database, extracts keys from
process memory, and **actively sends messages**. The top-level `README.md`
describes this generation only.

**v2** — the current direction (`apps/WeChatCompanion`). Native SwiftUI,
read-only, Screen Recording only, system window sharing, hosted extraction,
consented local persistence, read-only MCP, review-only digest path.

**Never transfer a v1 capability, permission, threat model, or limitation into
v2 without explicitly marking it historical.** Detailed architecture facts live
in the vault, not here.

---

## 6. Evidence grading

Use the project vocabulary: **Verified · Probable · Hypothesis · Inconclusive ·
Rejected/Superseded.**

**A decision to stop using a technique is not evidence that the technique
technically failed.** A commit saying "abandon X" proves abandonment, not
empirical failure.

When citing a closed route, name the closure type:

- measured technical failure
- privacy rejection
- product / ethical prohibition
- architectural supersession
- version-specific runtime defect
- inconclusive experiment

### Version and provider scoping is mandatory

Write `stock Hermes v0.20.5`, never bare "Hermes".

Write *"Hermes v0.20.5 Desktop under the tested configuration exposed 49 tools
on the wire"*, never *"Desktop exposes 49 tools"*.

Patched results stay labelled: **disposable patched clone**, patched, with
hand-reimplemented fixes noted where relevant, and **not stock-runtime
acceptance**.

Provider and cache behaviour must not be restated as a provider retention
guarantee without provider evidence.

---

## 7. Stable rules

These are settled procedures and evidence-backed boundaries, not immutable laws:

- Structural reachability is the safety boundary. Do not rely on telling a model
  not to write.
- Do not invoke or vendor the stock `wechat-cli`.
- Under the current D-002, do not add `CGEvent`, `.activate(`, or other
  WeChat-driving behaviour without explicit reconsideration.
- Never parse a visible WeChat time as an authoritative date — use
  `firstObservedAt`.
- Local cleanup is an application-level purge, not forensic erasure.
- Do **not** claim local cleanup affects or guarantees deletion of any
  provider-side copy or cache.
- The digest is **not** local-only.
- Verify tool surfaces on the wire, not from configuration alone.
- Do not build durable acceptance evidence on a patched Hermes clone.
- The current v2 permission model is **Screen Recording only**.
- Do not silently reintroduce Accessibility or active control.

**Do not silently overturn an Active Decision or an evidence-backed safety
boundary. If a task requires changing one, name the conflict and require an
explicit new technical or product reason.**

---

## 8. Still open — canonical memory must not freeze R&D

- **Semantic AX** — currently not used. The original technical measurement was
  **Inconclusive**. Do not say AX is impossible. Note the conflict: the current
  enforcement test also bans `AXUIElement`, so a read-only AX reopening requires
  explicit reconsideration of that test and decision boundary.
- **Hermes Desktop** — deferred, not rejected. Patched-clone evidence is
  provisional.
- **Native `WeChatDataAdapter`** — option C remains open behind legal and
  provenance review.
- **Coverage / history above the fold** — a major open R&D question. Do not
  solve it by silently violating D-002.
- **Minimised / other-Space capture** — untested. Claim neither support nor
  failure.

---

## 9. Write-back, after verified work only

Always consider `Current Status.md` and `Next Actions.md`. Then update only the
task-specific notes that actually changed.

For R&D:

- `Experiments.md` — for a material experiment
- `Findings.md` — only when durable knowledge resulted
- `Rejected Approaches.md` — only if a route was genuinely rejected or superseded
- `Decisions.md` — only if a real decision occurred
- `Sources.md` — only for durable evidence

**A failed run is not automatically a Rejected Approach.** Do not update every
note merely because a session happened.

Advance `head_commit` only to a real commit, only after verified work is
committed, and only after canonical memory has been reconciled through it. If
product-relevant work is uncommitted, leave the anchor unchanged and disclose
the working-tree divergence separately.

---

## 10. Degraded mode

If the vault cannot be verified per §1, begin your first message with:

```
Project memory unavailable at <path> — running in degraded mode.
```

Then work from this file, the repo source and tests, Git branch and history, and
the relevant docs. Use Memorix only as clearly stale-prone scratch, if present.

Do not invent another vault. Do not create project memory inside the repo. Do
not treat the historical README or a sealed phase report as automatically
current. Do not proceed silently as though canonical memory were loaded.

Normal development and tests may continue where appropriate. After meaningful
verified work, end with a block titled:

```
VAULT UPDATE
```

containing which canonical notes should change, the concise facts and evidence
to add, and whether `head_commit` may advance and to which real commit.

There is no sync script, hook, or CI mirror. Reconciliation is deliberate.
