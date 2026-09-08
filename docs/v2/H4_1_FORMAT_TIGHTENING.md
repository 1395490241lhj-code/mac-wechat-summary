# H4.1 — format tightening and the invocation contract (synthetic only)

**Status: phase report. Synthetic only.** No real WeChat data, no WeChat
container, no database, no access material. Every store in this report was
built from the H3 scenario fixtures in
`.hermes/skills/wechat-digest/evaluation/scenarios.py`. The official Hermes
install and the `wechatshadow` profile were not written to.

H4.1 was deferred by design so the artifact under evidence would not change
underneath the stock re-validation. That re-validation has now run, and it
reproduced two of the three items it was waiting on. This closes them.

## What the credentialed run found

Two deviations, both deterministic, both on current upstream Hermes with the
`#89550` + `#88865` heads composed onto `main`:

1. **A note after the coverage line.** The Desktop-surface digest was otherwise
   correct — `微信摘要` header, right section, right classification, coverage
   line present — and then appended
   `（提示：当前仅采集到 1 个会话、2 条消息，覆盖范围非常有限。）`.
2. **Internal field names in user-visible output.** A seeded
   `hermes chat -q "/wechat-digest …"` produced
   `conversation_count=1, message_count=2, reader_configured=false` and
   `first_observed_at` in prose aimed at the user.

## Decision 1 — the invocation contract is the preloaded skill

The supported H5 invocation is the **preloaded skill**: `-s wechat-digest` for
the CLI runner, and SKILL.md's bytes prepended to the prompt for the Desktop
turn. That is what `shadow/runners/hermes.py` has always run.

`hermes chat -q "/wechat-digest …"` is **not** an acceptance path. Current
upstream reaches slash expansion only under `if not is_seeded_query` in
`cli.py`, and `-q` *is* a seeded query, so the text reaches the model verbatim
with no skill applied. Finding 2 above is a symptom of that: no skill was in
play, so no skill rule could be broken.

Scope of the earlier evidence, stated precisely: H4.7 observed
`/wechat-digest` expanding server-side in an **interactive Desktop chat on
stock Hermes v0.20.5**. That finding is true for that runtime and that
invocation and is not restated here as anything broader. **No claim is made
anywhere in this project that a seeded `-q` performs server-side slash
expansion.** The seeded-slash probe has been removed from the harness rather
than left recording a path outside the contract, and upstream's CLI behaviour
is not patched.

## Decision 2 — SKILL.md v1.2.0

Two rules added; every other digest semantic and the whole output structure are
unchanged.

**The coverage line must be the literal final line.** Not "near the end", not
"followed by a short note". Nothing may come after it: no note, caveat,
parenthetical, footer, summary of the summary, capture count, remark about the
data being sparse, or offer to do more, in either language.

The position is a rule rather than a preference for a specific reason, and the
rule says so: a reader who stops at the last line must land on the coverage
qualification. A trailing note displaces it — and a note that itself describes
coverage ("only 2 messages captured") reads as a *more precise* claim than the
qualification it displaced, which is exactly the completeness claim the
coverage section exists to prevent.

**No internal name may appear in a digest.** Tool names, response fields
(`message_count`, `conversation_count`, `reader_configured`, `schema_version`,
`first_observed_at`, `sequence`, `logical_message_id`, `coverage`, `freshness`),
and MCP/API or store vocabulary are how the answer was *obtained*, not part of
it: "共 2 条消息", never "message_count=2". The one exception is the fixed
`依据：msg:…` citation line, whose form was already specified.

This rule is **new**, not a restatement. No output-hygiene rule about internal
names existed in SKILL.md before v1.2.0; the H4.1 item named it as owed and
this is where it lands.

## Gates

Both rules are pinned twice: statically against the skill text, and
behaviourally against real digests.

- `evaluation/test_digest_skill.py` asserts the written rules, including the
  two new ones, and each new test names the real output that motivated it.
- `shadow/hermes_online.py` asserts, for **every** digest a run produces —
  gates A/B/C/J, the canary, and the Desktop turn — that the last line is the
  mandated coverage line verbatim and that `INTERNAL_NAMES` appears nowhere in
  it. A failure of either fails the run.

The third H4.1 item, a regression test for the stray `skill_manage` deviation,
is unaffected by this change and stays open.

## Results

**PASS.** All six digests the supported invocation produced — gates A/B/C/J,
the canary, and the Desktop turn — end on the literal coverage line and carry
no internal name. The Desktop surface, which previously appended a note, now
ends exactly on it. Static suites: 52 skill, 158 harness. Residue and
containment clean. Full evidence in `H4_1_VALIDATION.md`.
