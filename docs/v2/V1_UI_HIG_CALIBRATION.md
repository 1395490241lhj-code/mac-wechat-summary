# V-1 Chats — UI/HIG calibration and implementation spec

- **Status:** `Proposed / implementation pending`
- **Kind:** design calibration + implementation spec. **Not an architectural
  Decision.** This document records no `D-*`, overturns none, and creates no new
  safety or architectural boundary. It refines the presentation of already-shipped
  V-1 behaviour.
- **Subject:** `apps/WeChatCompanion/WeChatCompanion/ContentView.swift`,
  `Ingestion/CaptureLedger.swift`
- **Baseline:** `d2e30b2` — *feat(app): V-1 Capture Ledger*
- **Branch:** `feature/hermes-validation-isolation` (unmerged)
- **Date:** 2026-09-09

This supersedes the first-pass HIG audit, which over-claimed in four places. Each
finding below is restated at its evidence-supported grade.

---

## 0. How to read this document

Two kinds of claim appear here and they are **kept separate on purpose**:

- **HIG evidence** — what Apple's Human Interface Guidelines and the platform
  actually support, plus verifiable facts about this codebase (build settings,
  line references, test constraints).
- **Product-design judgment** — this project's decision for this product and this
  task, which the HIG permits but does not compel.

A judgment must never be reported as an HIG mandate. Most of the first-pass
audit's errors were exactly that substitution.

Evidence grades follow the project vocabulary: **Verified · Probable ·
Hypothesis · Inconclusive · Rejected/Superseded**.

---

## 1. Build-configuration facts

Established by reading
`apps/WeChatCompanion/WeChatCompanion.xcodeproj/project.pbxproj`:

| Setting | Value |
|---|---|
| `MACOSX_DEPLOYMENT_TARGET` | `14.0` |
| `SWIFT_VERSION` | `6.0` |

Grade: **Verified**. This bounds which platform affordances are even reachable
and is decisive for §5.

---

## 2. Finding 1 — List vs Table

### HIG evidence

Apple's guidance on tables is **conditional**: sorting and resizable columns
should be offered *when they provide value*. The HIG does not mandate `Table`
for any collection of records, and offers no rule that a list of items with more
than one attribute must become a multi-column table.

`Table` is available at the deployment target. Availability is therefore not the
deciding factor and must not be used as one.

Grade: **Verified** (as a statement about what the HIG does and does not require).

### Product-design judgment

**`Table` is a rejected candidate for the Captured conversations ledger. Use a
native compact `List`.**

The product task is *"what conversations were captured recently?"*. Reasoning:

1. **Sorting adds nothing.** `CaptureLedger.conversations` is documented and
   tested as newest-`lastCapturedAt`-first (`CaptureLedgerTests:75`). The one
   ordering the task needs is already the default. Sorting by first-captured or
   by retained count answers questions a capture-ledger user is not asking.
2. **Columns fight the content.** The chat name is the widest and least
   predictable field and is frequently a long Chinese title. A fixed column
   truncates the field that identifies the row. The present two-line row lets the
   title take full width and demotes the timestamps to `.caption`.
3. **Two timestamps are one fact.** "First captured · last captured" is a span.
   Splitting it into two independently sortable columns presents it as two
   comparable dimensions — the database-browser framing this product is avoiding.
4. **Register.** `Table` reads as a data grid. Chats is a status surface, not a
   query surface. Chats must not become an engineering dashboard *or* a database
   browser.

The row carries exactly four fields
(`CaptureLedgerPresentation.ConversationRow`: `title`, `retainedCount`,
`firstCaptured`, `lastCaptured`).

### The actual defect

Not "this is not a `Table`" — it is that **it is not a `List` either**.
`CaptureLedgerSection` (`ContentView.swift:562`) hand-rolls list behaviour: a
`ForEach` over an `enumerated()` array inside a `GroupBox`, with manually
interleaved `Divider()`s. That reimplements separators and forfeits the row
semantics, keyboard traversal and separator-inset behaviour `List` provides —
which is also the substrate the VoiceOver gate in §7 would be testing.

---

## 3. Finding 2 — Accessibility

### HIG evidence

`ContentView.swift` contains **zero** `accessibility*` modifiers. Grade:
**Verified**.

"Therefore the app has no accessibility support" does **not** follow, and the
first-pass audit's inference was invalid. The file is built from
`LabeledContent`, `GroupBox`, `Toggle`, `Picker`, `Button`, `ProgressView`,
`ContentUnavailableView` and `Label`, all of which ship platform semantics
without any explicit modifier. Absence of modifiers is absence of *override*,
not absence of semantics.

### Restated finding

**`Unverified VoiceOver experience for composite/custom rows.`** Grade:
**Inconclusive**.

The rows built by hand from bare `Text` in `VStack`/`HStack` are the genuinely
unknown surface: `CapturedConversationRow`, `StatusRow`, `PermissionRow`,
`DiagnosticMetric`, `ExtractionMetric`, `MemoryRow`, `FailureDetail`.

Open questions, none of them yet answered:

- Does each composite row read as **one** element, or fragment into three?
- Is the `.caption` line `"First captured … · Last captured …"` announced
  coherently?
- Do the 57 `Divider()` instances add navigation noise?

### Product-design judgment

**No accessibility modifier is prescribed until Accessibility Inspector /
VoiceOver evidence exists.** Prescribing modifiers against an unmeasured baseline
risks overriding correct system semantics with worse hand-written ones. §7 D5
defines the gate. A clean audit is a **result** ("verified, no modifiers needed"),
not a failure.

### ⚠ Repo-specific hazard — do not conflate two different "accessibility"

SwiftUI accessibility modifiers on **our own** UI are unrelated to **Semantic AX
capture of WeChat**, which is deferred under D-002 and AGENTS.md §8. The two must
never be conflated in a commit message, a test name, or a vault note.

Separately, `PassiveCaptureTests.compiledSourceContainsNoActiveControlAPIs`
(`WeChatCompanionTests/PassiveCaptureTests.swift:75`) string-scans **every**
Swift file under `WeChatCompanion/` for `CGEvent`, `AXUIElement` and
`.activate(`. `.accessibilityLabel` and friends do not trip it; any
`NSAccessibility`-adjacent API must be checked against that gate before it is
written.

---

## 4. Finding 3 — Toolbar

### HIG evidence

`grep -c "\.toolbar" ContentView.swift` → `0`. Grade: **Verified**.

**The absence of a toolbar is not itself an HIG violation.** The HIG describes
the toolbar as the place for frequently used, view-level commands. A view with no
such command is correct without one.

### Product-design judgment

**No toolbar defect. Do not add a toolbar to Chats.**

- The app currently has exactly **one** frequent view-level command:
  `Test Passive Capture` (`ContentView.swift:245`), already a prominent button in
  the Diagnostics header, mirrored on Overview (`:163`).
- Retention (`Keep messages for`, `:831`) and `Delete Local Message History…`
  (`:855`, guarded by a `confirmationDialog`) live in Settings. That is the
  correct home for a persistent policy control and an irreversible history
  action.
- **Neither is to be moved to a toolbar merely to create a toolbar.** Promoting a
  destructive history action into a toolbar would place it one stray click away
  in a view whose entire purpose is passive reporting.

Revisit only if a later phase produces a genuine frequent view-level command.

---

## 5. Finding 4 — Liquid Glass / scroll-edge

### HIG evidence

Liquid Glass and the macOS 26 scroll-edge effect **do not exist at
`MACOSX_DEPLOYMENT_TARGET = 14.0`**. The shipped app can exhibit neither the
adoption nor the defect. On macOS 14–15 the standard `NavigationSplitView` +
`ScrollView` composition already receives the system's own sidebar material and
scroll behaviour without custom code.

Grade: **Verified**, from the build configuration in §1.

### Restated finding

**Not applicable at the current deployment target.** This is a downgrade *past*
`visual verification pending` — the first-pass audit graded it as an
HIG-supported defect, and the calibration pass proposed `verification pending`;
the build configuration removes it from scope entirely.

### Product-design judgment

**No custom material and no custom scroll-edge treatment is proposed in any
phase of this spec.** It becomes a legitimate open question only if the
deployment target is raised to 26 *and* the app is built against that SDK — a
separate decision with its own cost, out of scope here.

If the rendered review (§7 D1) surfaces an actual visual artifact, **that
artifact is the finding**, described on its own terms and graded on its own
evidence — not retro-fitted to this heading.

---

## 6. Implementation phases

### Invariants held across every phase

Non-negotiable, and each is a review gate:

- No change to capture, extraction, ingestion or reconciliation behaviour.
- No new permission. **Screen Recording only.**
- No provider call added or altered.
- No navigation into a conversation.
- **No message text reaches the ledger.** `CapturedConversationSummary` keeps no
  text field; a leak stays a compile error, not a review question.
- No schema, table, column or `user_version` change.
- Retention semantics and the destructive-delete confirmation stay exactly as
  they are, **in Settings**.
- No `CGEvent`, `AXUIElement` or `.activate(`.
- Database path stays at `H5A BLOCKED — ACCESS MATERIAL REQUIRED`.
- All 346 tests in 36 suites keep passing.

### Test-surface constraint that shapes the phases

`CaptureLedgerTests` asserts against `CaptureLedgerPresentation` strings only
(via `userFacingStrings`, `CaptureLedgerTests:32`) — **not** against view chrome.
Therefore:

- GroupBox labels and section titles in `ContentView.swift` are **test-neutral**;
  sentence-casing them (Phase A4) breaks nothing.
- Any change to a *presentation* string — including any newly added one — must
  clear the forbidden-internal-name assertion at `CaptureLedgerTests:221`.
- Phase C is the only phase that touches tested strings.

---

## Phase A — Information architecture only

No visual styling, no new controls, no `CaptureLedgerPresentation` wording change.

### A1 — Chats retains user-facing operational state only

**Principle: Chats carries operational state a user can act on. Implementation
and provider state belongs in Diagnostics. Persistent privacy/consent settings
belong in Settings.**

**Keep in Chats**

1. A **compact capture `Status`** — one line answering "is capture working right
   now".
2. **User-facing timing** — `Last captured` / last extraction time.
3. **Contextual warnings, only when something currently needs action.** Nothing
   in this group is a permanent section; each appears only on its triggering
   condition:
   - withheld-for-consent, when `framesWithheldPendingConsent > 0` (a consent
     misconfiguration the user can fix, not a throughput statistic);
   - the Capture health anomalies of Phase C;
   - Remote Processing **only** when it is blocking or materially affecting
     capture (see A1.3).

**Move to Diagnostics**

1. **All throughput counters** — `Meaningful Frames Received`, `Successful
   Extractions`, `Dropped While Busy`, `Failures`, `Cancelled`, and the raw
   `Withheld Pending Consent` count (`ContentView.swift:429–466`). The *warning*
   stays in Chats; the *counter* moves.
2. **`Last Failure Diagnosis`** (`:485–520`) — twelve raw protocol fields: HTTP
   status, URL error code, Keychain status, finish/block reason, output
   characters, four token counts, occurred-at. Engineer-facing telemetry. Moves
   **whole and unchanged**, and its privacy caption travels with it verbatim.
3. **`Latest Extraction`** (`:536`) — labelled in-source as a development-stage,
   memory-only preview. Not a shipped feature; belongs in Diagnostics.
4. **Detailed Remote Processing / provider state** — the `Remote Processing`
   status row (`:415`) and `Selected Gemini Model` (`:422`).

**A1.3 — Remote Processing is a persistent privacy/consent setting**

Its canonical control already exists in **Settings** (`ContentView.swift:786`):
the toggle, the "Off by default. Saving an API key does not enable this."
explanation, and an `Extraction Status` row. The Chats row is a redundant mirror
of a setting that is not a Chats concern.

**Judgment:** the canonical control and status stay in Settings. Chats surfaces
Remote Processing **only** when it blocks or materially affects capture — for
example extraction status `notConfigured`, or remote processing off while frames
are being withheld — and then as a contextual warning under A1.3, never as a
permanent section.

**Resulting hierarchy**

| Destination | Answers | Carries |
|---|---|---|
| **Chats** | *What was captured, and does anything need me?* | compact capture status; last captured / last extraction time; captured-conversations ledger; contextual warnings only when actionable |
| **Diagnostics** | *What did the machinery do?* | throughput counters; last failure diagnosis; latest-extraction dev preview; provider/model detail; passive-capture test |
| **Settings** | *What am I allowing, and for how long?* | remote-processing consent; local storage consent; retention; destructive delete |
| **Overview** | *Is the system set up and healthy?* | WeChat/permission/window status; capture preview; diagnostics summary |

### A2 — Remove redundant in-content titles

Four views print a `.largeTitle` duplicating `.navigationTitle`:
`ContentView.swift:39`/`:179`, `:240`/`:327`, `:403`/`:543`, `:727`/`:891`. Drop
the in-content `Text`; keep `.navigationTitle`. In `DiagnosticsView` the title
sits in an `HStack` with the `Test Passive Capture` button and the
"Only privacy-safe aggregate metadata is stored." subtitle — keep both, drop only
the title `Text`.

### A3 — Reduce full-width boxing

Fifteen `GroupBox`es, every one at `maxWidth: .infinity`, separated by 57
`Divider()`s. Replace the vertical stack of boxes with native sectioning
(`Form`-style `Section`s, or the `List` sections of Phase B) so grouping comes
from the platform rather than from nested borders; row separators then replace
the manual dividers.

Also unify the reading measure: `DiagnosticsView` uses
`.frame(maxWidth: 760)`, `OverviewView` uses `720`, and `ChatsView` and
`SettingsView` use `.infinity`. One value across all four.

### A4 — Sentence-case visible section titles

`Captured Conversations` → `Captured conversations`; `Capture Health` →
`Capture health`; `Message Extraction` → `Message extraction`;
`Last Failure Diagnosis` → `Last failure diagnosis`; `Latest Extraction` →
`Latest extraction`; `Remote Processing` → `Remote processing`;
`Local Message Storage` → `Local message storage`;
`Gemini Extraction Provider` → `Gemini extraction provider`;
`Passive Capture` → `Passive capture`; `Capture Preview` → `Capture preview`;
`Delete Local Message History…` → `Delete local message history…`. Row labels in
`StatusRow` / `LabeledContent` usages follow the same rule.

Sidebar `Destination.rawValue` entries stay title-case: they are navigation
destination names, not section titles.

---

## Phase B — Captured conversations presentation

**Native compact `List`. `Table` rejected** — §2, on the product task, not on
feature availability.

- **B1.** Convert `ChatsView` from `ScrollView { VStack }` to a top-level `List`
  with `Section`s, `.listStyle(.inset)`. This must happen at the view root: a
  `List` nested inside a `ScrollView` requires a fixed height and behaves badly.
  This is why Phase B follows Phase A's sectioning.
- **B2.** `CapturedConversationRow` keeps its shape — title with `.lineLimit(1)`
  / `.truncationMode(.tail)` and retained count on the first baseline, span on a
  `.caption` second line — but becomes a real `List` row. Drop the manual
  `Divider()` interleaving and the `enumerated()` wrapper; `ForEach` over the
  already-`Identifiable` `ConversationRow` suffices.
- **B3.** Rows stay **non-selectable and non-navigable**. No disclosure, no
  detail. Navigation into a conversation is out of scope for V-1 and would
  require message text.
- **B4.** No wording change. `CaptureLedgerPresentation` is untouched, so
  `CaptureLedgerTests` is untouched.

---

## Phase C — Capture health by progressive disclosure

Today `healthRows` is a flat, always-visible list of four counters whenever
storage is not disabled — three of them normally zero. **Normal operation should
be quiet.**

- **C1 — Normal state.** When `framesWithoutChatIdentity == 0`,
  `continuityGaps == 0` and `persistenceFailures == 0`, collapse to a single
  reassuring line reporting messages captured (and recovered, when non-zero). No
  counter grid.
- **C2 — Anomaly state.** Any non-zero problem counter surfaces that counter with
  its existing guidance string, which already says what the user can do and
  already states that the app never scrolls for you. `persistenceFailures` keeps
  its current non-zero-only rule; `framesWithoutChatIdentity` and
  `continuityGaps` adopt the same rule.
- **C3 — Detail stays reachable.** A disclosure control reveals the full counter
  set, so nothing currently visible becomes unreachable.
- **C4 — Constraint.** This is the one phase that changes tested strings.
  `guidanceAppearsOnlyWhenTheMatchingProblemOccurred`,
  `healthIsHiddenEntirelyWhilePersistenceIsOff`,
  `savingFailuresAreShownOnlyWhenTheyHappened`,
  `capturedAndRecoveredMessagesAreBothReported` and the three empty-state tests
  must all still hold; the forbidden-internal-name assertion
  (`CaptureLedgerTests:221`) must cover every new string. Expect to **add** cases,
  not to relax existing ones. The three distinct empty states — storage off,
  storage on but unreadable, storage on with nothing captured yet — are unchanged.

---

## 7. Phase D — Verification

**No finding in Phases A–C is closed by reasoning.** Each gate produces evidence,
or the item stays open at its current grade.

- **D1 — Rendered visual review.** Build and run; screenshot Overview, Chats,
  Diagnostics, Settings; compare against the pre-change build. Any Liquid Glass or
  scroll-edge claim is admissible **only** as an artifact observed here — and per
  §5 no macOS 26 effect is expected to appear at target 14.0.
- **D2 — Narrow-window review.** Resize to the
  `navigationSplitViewColumnWidth(min: 190)` floor plus a minimum detail width.
  Confirm the reading measure holds, no row clips, and the ledger's title/count
  baseline row degrades by truncating the **title**, never by dropping the count.
- **D3 — Light / Dark / Increase Contrast.** All four destinations in each.
  Particular attention to A3's reduced boxing — grouping must survive without the
  borders — and to `.foregroundStyle(.secondary)` guidance and caption text under
  Increase Contrast.
- **D4 — Long names, English and Chinese.** Fixture ledgers with long English and
  long Chinese chat names, plus a single-message conversation to exercise
  `"1 message kept"`. Confirm CJK truncation, the two-line row, and that the count
  is never pushed off.
- **D5 — Accessibility Inspector / VoiceOver.** The gate for §3. Run the
  Inspector audit on all four destinations, then traverse Chats with VoiceOver and
  record, per composite row type, whether it reads as one coherent element,
  whether the span line is announced sensibly, and whether separators add noise.
  **Only findings this pass produces justify adding accessibility modifiers.**
- **D6 — Test gate.** Full suite green — 346 tests, 36 suites — including
  `DiagnosticPrivacyTests`, `CaptureLedgerTests` and
  `compiledSourceContainsNoActiveControlAPIs`.

**Sequencing:** A → B → C, each independently committable. D1–D4 run after each
phase. D5 runs after B — the `List` conversion changes the semantic substrate it
tests — and again after C.

---

## 8. Vault reconciliation

**This document is not duplicated into the vault.** The vault should carry, at
most, a one-line pointer to this path with its `Proposed / implementation
pending` status, recorded when a phase is actually implemented and verified.

No `D-*` is created, amended or superseded. No `Rejected Approaches` entry is
created: `Table` is a **rejected candidate within this spec**, not a closed
project route.

At the time of writing, `Current Status.md` reconciles through `57294b9`
(eleven commits); `d2e30b2` — the V-1 Capture Ledger this document calibrates —
is a twelfth, unreconciled product-relevant commit. `head_commit` stays
`a928976`.
