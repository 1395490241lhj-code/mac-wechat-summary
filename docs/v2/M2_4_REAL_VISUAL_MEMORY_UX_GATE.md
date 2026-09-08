# M2.4 — Real Visual → Memory Product UX Gate

**Sealed:** 2026-09-07 · **Branch:** `v2/rewrite` · **Builds on:** `226a133` (M2.3)
**Scope:** the first validation on **real, consented user data**. No tool added,
no schema change, no reader work, no background sync, no M3 intelligence.
`SKILL.md` is unchanged (see §8). No message text, chat name, sender, citation
id or filesystem path appears anywhere in this report.

## Verdict

```
REAL VISUAL → MEMORY UX GATE: FAIL — not established
```

**The product path worked.** What could not be established is the thing the
gate exists to establish: that the production skill produces *grounded Memory
answers on real data*. Three of four scenarios never used Memory at all, and
the two mechanical checks intended to grade the fourth turned out to measure
artifacts. **No defect was proven and none was excluded.** Recording this as a
pass would claim evidence that does not exist.

---

## 1. Preflight (read-only)

| | |
|---|---|
| HEAD / tree / vault | `226a133`, clean, reconciled |
| app local-message consent | on |
| memory consent | on (`allowsMemoryStorage`, generation 8) |
| active MessageSource | visual (default; nothing selected) |
| canonical MemoryStore before | **absent** |
| OAuth keychain item | present (presence only; never read or printed) |
| installed app | present but **predating M2.2d — no bundled worker** |
| real-user-data test guard | active |

The installed app could not have synced anything, so it was rebuilt and
reinstalled with `scripts/build-dev-app.sh`; the installed helper then verified
`--deep --strict`, carried the expected Team ID and hardened-runtime flags, and
answered its protocol probe under `env -i`.

## 2. Capture scope

No new capture was performed. The local store already held **13 real messages
across 2 conversations**, captured earlier through the production VisualReader
path, spanning **about one minute**. That is the smallest representative real
scope, and a fresh capture would have required a new Screen Recording session
and another round of frames leaving the Mac for extraction (D-006) for data
that already existed. The operator chose this explicitly.

**Consequence, and it matters for the result:** a one-minute, 13-message store
contains nothing that the four raw tools cannot read directly. See §6.

## 3. Real App Sync Now — PASS

Performed through the app's own Settings → Memory → **Sync Now** button, not the
operator CLI.

| Check | Result |
|---|---|
| first sync | `Done: 13 new, 0 re-observed, 2 conversation(s)` |
| second sync (idempotence) | `Done: 0 new, 13 re-observed, 2 conversation(s)` |
| canonical store | created at the app-owned location, mode **0600** |
| store contents | `user_version` 2 · 13 messages · 2 conversations · 2 runs, both succeeded · 4 coverage rows, all `observed_complete` |
| provenance | source `visual` only — **no substitution** |
| identity / timestamps | `identity_mode: source`, `timestamp_kind: first_observed` |
| freshness after sync | last successful sync advanced (19:09 → 19:10) while the three data boundaries stayed put |
| residue | none |

The M2.2c semantics held in the real product: **last successful sync** moved on
the second click while **observed through**, **complete through** and **latest
stored message** did not.

## 4. Freshness UX review

The panel, before any sync, showed every value as `—`, which reads correctly as
"nothing has been synced". After syncing it showed source, last successful sync,
observed through, complete through, latest stored message, coverage and last
sync state, with the button's own line reading *"Runs once, in the foreground,
when you ask."*

| Item | Judgment |
|---|---|
| which source Memory came from | **clear** |
| when it was last synced | **clear** |
| whether the last sync succeeded | **clear** |
| whether coverage is complete/partial | **clear** |
| how to refresh manually | **clear** |
| through what point data was observed | **understandable but improvable** |

The one improvable finding: on this data **observed through**, **complete
through** and **latest stored message** all showed the *same* value, because a
per-conversation read bounds its window at the newest message it saw. Three
identical dates in three rows invites "why is this here three times?" — the
distinction the M2.2c model is built on is invisible precisely when the three
coincide. Nothing is wrong; the display simply cannot show *why* three separate
facts agree. **Not blocking, so not fixed here** (§8).

Also visible and useful: last successful sync (Sep 7) sits two days after
observed through (Sep 5), which is exactly the "Memory is current, the data is
not" situation the freshness model exists to express.

## 5. Agent runs — mechanics PASS

Production `SKILL.md`, canonical activation (`--memory`, **no** database path),
four questions written so that this process never needed to read the messages:
the model discovers the conversations itself.

| Check | Result |
|---|---|
| tools on the wire | **exactly 9**, every run |
| unexpected tools | none |
| boundary violations | **0**, every run |
| residue after cleanup | **0**, every run |
| citations returned in-run | all of them |
| fabricated citation ids | **0** |
| duration per scenario | 22–47 s |
| report hygiene | no credential, no id, no path, no chat content |

## 6. Memory was barely exercised — the central finding

Tool use per scenario, across two runs:

| Scenario | Memory calls | Raw-tool calls |
|---|---|---|
| direct recall | 3 / 2 | 0 / 0 |
| contextual recall | 0 / 0 | 4 / 4 |
| coverage honesty | 1 / 0 | 4 / 4 |
| freshness awareness | 0 / 0 | 5 / 3 |

Only the first question was answered from Memory. The others were answered from
the four raw tools — and on this data **that is defensible rather than wrong**:
with 13 messages captured inside one minute, the raw store *is* the whole
history, so Memory offers nothing extra and the skill explicitly says not every
question needs it.

The consequence is that **the gate did not test what it was built to test.**
A real store whose history exceeds the capture window is required for Memory to
have anything the raw tools lack. That is a property of the dataset, not a
defect in the skill or the plumbing.

## 7. Why answer quality could not be graded

Two attempts, both inconclusive, recorded because the failure of a method is
evidence about the method:

**Operator grading.** The operator initially marked one of four answers correct,
then said plainly that they could not actually make that judgment — it would
mean re-reading their own messages from a one-minute window two days earlier.
That is a fair objection, so the grade was **withdrawn and not used**. Asking a
person to verify an assistant's summary of their own chat history is not a
usable validation method for a small, old window.

**Mechanical proxies.** Two content-free checks were then built, and both
turned out to measure artifacts:

- *Quoted-span fidelity* — spans in quotation marks were compared against
  stored message text. The first version counted **8–9 unmatched**; once
  conversation titles and sender names were added to the comparison corpus the
  true figure was **4 of 20**, unchanged before and after an intervention. Most
  "unmatched quotes" were the model quoting a **chat name**, which is not a
  message.
- *Date assertions* — dates the answer stated were compared against the days
  the store holds. But the questions themselves named dates ("Sep 1–4",
  "Sep 7"), so restating them counted as a violation. The signal was the
  question, not the answer.

Neither check, corrected, shows a grounding defect; neither is strong enough to
show its absence. The residual 4-of-20 unmatched spans are an upper bound that
includes partial quotes and paraphrase, not a count of inventions.

## 8. A change made and reverted

Acting on the *first*, uncorrected reading of those metrics, `SKILL.md` was
hardened with two rules (quotations must be copied verbatim; messages may not
be placed on a calendar day) and two contract tests were added. Re-running the
four scenarios showed **no improvement** — unmatched spans 4 → 4.

The metric was then corrected and the finding evaporated. The change was
**reverted**: the skill is byte-identical to M2.3 (`7c57580…`), and the
evaluation suite is back to 49 passed / 1 skipped. Editing a sealed production
skill on the strength of a measurement artifact is the mistake here, and the
revert is the fix.

Worth keeping regardless: adding a rule to the skill did not move the metric,
which is consistent with **F-008 — instructions are not a boundary**.

**Bugs found: none proven. Bugs fixed: none. Changes surviving this phase:
the new gate script only.**

## 9. Real-store safety

| | |
|---|---|
| store before the gate | absent |
| store after the gate | present — written **only** by the operator's two Sync Now clicks |
| written by any automated test | **no** — byte-identical (size and mtime) before and after the full suite run |
| M2.2e guard | intact |

## 10. Recommended default digest policy

**Option A — keep the default digest capture-focused; use Memory for explicit
historical or contextual questions.**

The evidence: on a small, fresh store the model already ignores Memory without
being told to, and answering from the raw tools was faster and sufficient.
Automatically augmenting every digest with Memory (option B) would add latency
and retrieval for no gain on exactly this shape of data, and would invite
coverage confusion between two sources describing the same messages. Revisit
when a store exists whose history genuinely exceeds the capture window — that
is the condition under which B or a conditional hybrid could pay for itself,
and it is not testable today.

## 11. First-run / revocation

Not re-tested here, deliberately: it is covered synthetically by
`MemorySyncTests` (fresh install refuses without reaching the runner; revocation
takes effect immediately and deletes nothing) and by the worker's consent tests.
Toggling the operator's real consent to re-prove it would disturb their settings
for no new information. If a real-store revocation check is wanted, it belongs
in a separate **M2.4b**.

## 12. Gates

No production code changed, so the suites were run as integrity checks only:
skill evaluation **49 + 1 skipped**, `memory/` **337**, `bridge/` **72**,
`shadow/` **138** — all unchanged. Swift not re-run (no Swift change).

## 13. What M2.4b should be

1. **A store with real history.** Several captures across several days, so
   Memory holds something the capture window does not. Without that, no gate
   can distinguish Memory working from Memory being unnecessary.
2. **A grading method that does not require the user to re-read their chats** —
   e.g. questions whose answers are checkable against store *structure*
   (counts, ordering, which conversation, which sender) rather than meaning.
3. Only then: coverage-honesty and freshness scenarios that Memory alone can
   answer, and a real revocation check.
