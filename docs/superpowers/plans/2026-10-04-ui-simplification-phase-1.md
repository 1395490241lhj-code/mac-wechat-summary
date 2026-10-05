# UI Simplification Phase 1 Implementation Plan

**Goal:** Open the app, find a chat, read or search it without learning the ingestion pipeline.

**Architecture:** Reuse AppModel and every sealed route. Restrict the macOS sidebar to Home, Chats and Settings; internal destinations remain accessible as child pages. Present independent observations in a native conversation list and preserve canonical transcript/reveal anchors. Use existing source-wide Summary/Reminders without implying conversation-scoped summaries.

**Tech stack:** SwiftUI/AppKit, existing local reader and explicit Memory preparation.

**Spec:** User-supplied UI Simplification / Consumerization Phase request, 2026-10-04.

## Constraints and review focus

- No acquisition, reader, schema, identity, coverage, privacy or fail-closed change.
- Separate imports remain separate even when names match. Dates must describe saved/observed time honestly.
- No fabricated message previews, Today data, outgoing Safe Share or Export.
- Memory settings requests must expand the moved panel before scrolling; sync remains explicit.
- Exact search reveal must retain row IDs, warning states, bounded windows and announcement.
- GUI/keyboard/VoiceOver acceptance must remain unverified if native UI tooling fails.

## Tasks

- [x] A: Validate consumer shell regression RED; restrict sidebar while preserving internal destinations and return navigation.
- [x] B: Add Home search and existing Today/follow-up routes; add recent conversations using existing aggregate metadata only.
- [x] C: Split Chats list/reader; move low-frequency actions to menu; keep legacy source controls in Advanced. Preserve exact reveal.
- [x] D: Move provider, Memory, capture and diagnostics to expandable Advanced; retain local storage consent/retention/deletion in normal Settings.
- [x] E: Run Debug build and full Swift suite, independent final review, actual UI inspection where available. Record unresolved gates and current worktree; no push or implicit commit. Runtime interaction acceptance remains Not Established.

## Execution evidence

Initial HEAD f894a31d7181e65b5d6fc6c0ed8a23643e5a5e5d, v2/rewrite, clean. Vault anchor equals HEAD. Historical digest branch contained in HEAD (185/0). Native Computer Use failed before returning UI: Sky Computer Use native pipe closed before response.


Implementation validation: Debug build succeeded; final Swift Testing 599 tests / 67 suites passed and XCTest 26 tests / 0 failures; realArchiveAcceptance skipped because DUKOU_FIXTURE_PATH absent. Full-suite in-process canonical-store fingerprint isolation test passed, but no external pre-host fingerprint capsule was established; do not claim that broader guarantee. No acquisition/real message inspection was performed for this UI phase. git diff --check passed.

Independent review found two Important presentation defects: nested Settings viewport handoff and import feedback disappearing with Visual selection. Corrected with one Settings scroll owner and reader-independent ImportAttentionView. Added source-wiring regression checks and incomplete attachment-status coverage. Final independent re-review found no remaining Critical or Important finding. Reviewer independently opened Home/Chats/Settings screenshots and ran StatusModelTests 7/7 plus git diff --check; viewport interaction/keyboard/VoiceOver remain unverified.

Xcode RenderPreview successfully rendered and the main agent opened production Home (empty and populated), Chats (synthetic transcript), and Settings (Advanced collapsed), all isolated synthetic state. Screenshots are local ignored artifacts in .build/ui-simplification-phase1/. RenderPreview's previewIndex parameter did not select another declaration, so declaration order was changed for each inspected preview. These are production-view previews, not installed-app interaction acceptance. Native UI automation failed before returning a view. Keyboard, VoiceOver, exact-hit visual scrolling and constrained-window interaction remain Not Established.

Product verdict remains NOT YET PRODUCT MET pending interaction/first-launch acceptance and further consumer copy in Summary/Follow-ups. No commit, push, install replacement, acquisition-policy change or Vault write-back. Canonical head_commit remains f894a31; this working tree contains uncommitted product-relevant divergence.


## Kitchen Manager reference supplement (2026-10-04)

Read-only reference audit: Kitchen Manager main @ c772a36830396505cc04f4799b49410a069bcaeb, clean. Canonical anchor 79e55c6 is one Planner-start consolidation commit behind; current reviewed design primitives are unchanged by that commit. Read AGENTS, km-project-memory/km-ui, canonical UI Design System, D-043 and relevant committed design/source implementation. No Kitchen Manager file or Vault changed.

| Primitive | Current Kitchen use | Companion application | Adoption |
| --- | --- | --- | --- |
| Open Row | Inventory/Shopping scan rows, native navigation | Chats native List selection; recent rows; adaptive name/metadata | Project-local CompanionConversationRow |
| Module Surface | Related task or expandable content; no mandatory nested card | Existing Today/Summary hierarchy | Pattern only; no new surface component |
| Utility row | Quiet Inventory navigation and native Settings controls | Existing storage/privacy and Advanced disclosures | Pattern only, macOS native controls |
| Quiet secondary state | Healthy expiry metadata suppressed; urgent/unknown retained | Dates secondary; successful imports quiet; partial history stays visible | Existing local status predicates/presentation |
| Contextual menu/confirmation | Native More, contextual rows, confirm destructive action | Follow-up More menu; delete confirmation; Chats search/menu | Native SwiftUI pattern |
| Empty state | ContentUnavailableView with clear next action | Empty chats/search/follow-ups | Native SwiftUI pattern |
| AI wrapper | Kitchen-owned phase wrapper around ThinkingOrbs; no invented reasoning | CompanionAIActivityIndicator for running on-device answer only; prep/search/deterministic summary ordinary progress | Local native wrapper, no ThinkingOrbs package |
| Motion grammar | quick .snappy(.2), standard .smooth(.28), emphasis .smooth(.32, extraBounce:.05) unused; native transitions preserved | Only confirmed follow-up regroup uses local standard; no custom selection/menu motion or emphasis | Local CompanionMotion.standard; Reduce Motion -> nil |
| Completion grouping | Pending execution rows + collapsed purchased region; reopen possible | Pending follow-ups + default-collapsed Completed; existing persisted status owner | Pattern reimplemented locally, no optimistic completion |
| Accessible layout | Semantic fonts, vertical AX layout, decorative icons yield space | ViewThatFits row, wrapping secondary text, avatar hidden at accessibility sizes; native sidebar/List + Cmd-F | macOS adaptation |

Opened fresh synthetic production Chats/Follow-ups previews and standalone long-text row at 260pt with accessibility3 environment and Light appearance. Screenshots in .build/ui-simplification-phase1/{chats-km-primitives,follow-ups,open-row-large-text}.png. Preview layout does not establish real macOS text-size scaling or keyboard/VoiceOver/Reduce Motion interaction. No cross-repo source dependency or third-party UI framework added.

Final supplemental review found a P2 asynchronous Visual -> Archive selection race: the suspended Visual ledger read could publish an obsolete selection after a newer Archive intent. A synthetic regression reproduced five wrong-reader assertions RED. The correction snapshots the existing revealGeneration and guards publication after both ledger and message awaits, including the unavailable path. Test-only actor blocking lives in the test target; final synchronization waits for both selection intents rather than relying on an already-populated Archive ID.

Final validation: /tmp/mws-ui-selection-final-tests.log reports TEST SUCCEEDED, 600 Swift Testing tests / 67 suites and XCTest 26 / zero failures; realArchiveAcceptance skipped without DUKOU_FIXTURE_PATH. The tightened final regression's 10-test suite also passed (/tmp/mws-ui-selection-focused.log). Separate Debug build succeeded (/tmp/mws-ui-selection-debug-build.log). Independent final review found no remaining Critical or Important finding in its bounded scope. git diff --check passed. PRODUCT MET remains Not Established for the runtime interaction and consumer-copy gaps above. HEAD unchanged; seven modified tracked files plus this untracked plan. No commit, push or canonical memory update.

## Consumer Pass 1.5 (user review follow-up)

Goal: reduce the remaining presentation complexity in the existing dirty tree. Keep the three-item sidebar, canonical IDs/reveal, explicit sync and fail-closed rules; no backend/schema/acquisition change or ContentView decomposition. No commit/push.

- [x] Home/rows: remove permanent preparation explanations, counts and duplicate dates; preserve honest Saved/Seen wording without fabricated message preview.
- [x] Reader: body-first typography, existing canonical sender/day grouping, spacing instead of per-message dividers; quiet visible incomplete-history warning and lower-weight details. Never infer self identity.
- [x] Follow-ups: saved task rows first, Completed collapsed, Find follow-ups as sole discovery entry. Reveal source/window/preparation only in search context or when required; keep failures and partial/truncated results honest, original details reachable.
- [x] Search: preserve actual entry parent for sidebar and Back action, retain all/archive request and exact-reveal behavior. Pin navigation behavior with focused regression before implementation.
- [x] Verification: full relevant test target and separate Debug build, re-render four production previews using isolated synthetic fixtures, independent code/UI review and Before/After findings. Runtime accessibility remains a separate unestablished gate.

Before previews preserved at .build/ui-consumer-pass15/*-before.png. Existing tracked product/test edits inspected and preserved; canonical Vault anchor unchanged at f894a31. ContentView size is recorded tech debt, deliberately not split in this pass.


Consumer Pass 1.5 final evidence:

- Home: permanent preparation/background explanation removed; Saved/Seen time appears once beside one date; no record/message count or fabricated last-message preview.
- Reader: canonical sender/day groups kept, sender displayed once per consecutive run, body primary and per-message full-width dividers removed. Details lower-weight; incomplete saved-copy warning visible. No self-identity inference or bubble alignment. Exact row IDs/highlight/announcement remain.
- Follow-ups: title/route naming consistent; saved task rows first, partial history in secondary consumer wording, Completed collapsed with Reopen. Find follow-ups starts only the existing explicit scan; Options & details appear in finding context; preparation failure reveals the existing explicit Settings route. Source and period still selectable, sync never automatic. Saved provenance/caveats reachable via More -> Details sheet, scrollable with Done outside scrolling content.
- Search: session-local parent captured on entry, sidebar and Back use it; all/archive handoff unchanged. Focused navigation regression passed. Isolated Search-from-Chats preview visibly highlights Chats.
- Existing MemorySyncTests source-wiring test targeted the obsolete RemindersView name and engineering copy, causing three failures after UI rename. Fixed required bounded extraction to FollowUpsView and removed two obsolete wording assertions; retained existing preparation/no-auto-sync/explicit-scan checks. Behavioral source/preparation/consent tests unchanged.
- Independent review opened four Before/After pairs and identified two Important issues: spoken date omission and incorrect complete+truncated warning. Fixed shared full-date accessibility description with date-distinction regression, and warning covering either message or suggestion bound. Bounded final re-review: no remaining Critical/Important issue.
- Final full test log /tmp/mws-pass15-full-tests.log: TEST SUCCEEDED, 601 Swift Testing tests / 67 suites; XCTest27 / zero failures. realArchiveAcceptance skipped without DUKOU_FIXTURE_PATH. Separate final Debug build in /tmp/mws-pass15-debug-build.log. git diff --check passed.
- Production previews rendered and opened: .build/ui-consumer-pass15/{home,chats,follow-ups,settings}-after.png, plus search-from-chats.png and follow-ups-preparation.png. Original Phase1 images preserved as *-before.png. All fixtures isolated/synthetic, no normal bootstrap or real-chat inspection. Preview evidence is not runtime keyboard, VoiceOver, Reduce Motion, exact scrolling or preparation-handoff acceptance.
- Current state: v2/rewrite @ f894a31d7181e65b5d6fc6c0ed8a23643e5a5e5d; eight modified tracked files plus this untracked plan. No commit/push/rollback/reset/stash or Vault write-back. Presentation pass complete for next user review; broader PRODUCT MET remains Not Established.

## Runtime Acceptance & Seal — 2026-10-04

User accepted Consumer Pass 1.5 and authorized bounded acceptance only. This section supersedes earlier provisional verdicts; historical test counts above describe earlier source states.

**Verdict: UI SIMPLIFICATION PHASE 1 — IMPLEMENTATION MET / RUNTIME ACCEPTANCE PARTIAL**

Preflight/final state: v2/rewrite @ f894a31d7181e65b5d6fc6c0ed8a23643e5a5e5d; exactly eight inherited modified tracked files and this untracked plan. All eight Swift file SHA-256 hashes match the preflight manifest in ignored .build/ui-runtime-seal/inherited-scope.json. No product/test fix was needed or made during acceptance. Canonical Vault is available with the same committed anchor; its clean-tree description is stale for the inherited uncommitted UI.

### Runtime method and isolation

Initial Computer Use attempts failed resolving Xcode or timed out; Xcode DeviceInteractionStartWorkspaceSession rejected macOS. Later native Computer Use successfully connected. A blanket GUI-unavailable claim would therefore be inaccurate.

An ignored, separate SwiftUI AcceptanceHost links the unchanged current production Debug dylib and instantiates its actual ContentView/AppModel. It uses in-memory LocalMessageHistory(url:nil), volatile reminders, empty credentials, unique defaults, nil share inbox, unavailable worker runners, and a fake preparation-required follow-up outcome. It omits normal application bootstrap/onOpenURL. Synthetic fixture: one archive import, 120 messages, one unique sequence-100 reveal marker, one pending and one completed follow-up. No real messages inspected; no existing user Companion instance bound or operated; no acquisition, sync, worker or network acceptance claimed. This establishes hosted production-view interaction, not untouched-app normal first-launch acceptance.

### Evidence matrix

| Gate | Established evidence | Still Not Established |
| --- | --- | --- |
| Launch/empty Home | Isolated native host opens actual production ContentView; empty Home purpose and understandable share/storage next action; only Home/Chats/Settings sidebar; no database/source/capture/provenance/coverage vocabulary in that normal empty view | Untouched production app normal first-time bootstrap |
| Keyboard/navigation | Sidebar click plus Down/Return navigates Home to Chats; Cmd-F from Chats opens Search with Chats still selected; Back to Chats returns; native Follow-up menu Escape dismisses; Details sheet Return activates Done | Conversation row-to-row keyboard selection (fixture has one import); broader focus traversal/sheet Escape |
| Search/reveal | Native local search for synthetic reveal marker returns one result; opening it reaches Chats and correct saved conversation; screenshot visibly shows sequence-100 marker scrolled into view and highlighted, between messages99/101; canonical exact-anchor tests pass | Actual VoiceOver reveal announcement/focus |
| Follow-ups ready | Find follow-ups is primary action; pending task and partial-history wording visible; Completed initially collapsed; Mark Done removes pending and increments collapsed Completed1 to2; expand/Reopen restores pending; More/Details and default Done usable | Real worker ready-success path and destructive confirmation navigation |
| Preparation required | Fake runner failure reveals one Prepare conversations next action and explicit return instructions; Options & details initially collapsed | Clicking preparation hit native pipe closure; Settings disclosure expansion/scroll landing and actual return-without-implicit-work are unestablished. Source/model tests cover explicit semantics, but are not GUI evidence |
| Accessibility | Runtime AX inspection exposes full-date conversation row, named search/Follow-up controls and partial-history wording | Actual VoiceOver labels/order, spoken dates, announcements and destructive-action traversal |
| Reduce Motion | Source disables custom regroup animation and provides static AI indicator plus adjacent status text | Native environment toggle and rendered/announced transition acceptance |
| Visual sanity | Reopened six final production previews; actual hosted empty Home, Search-parent, Chats reveal, ready/preparation Follow-ups visually checked | Runtime Settings landing, constrained-window focus/clipping and confirmation acceptance |

Native pipe closed while clicking Prepare conversations; two observations and one exact-host rebind failed with the same transport error. Exact AcceptanceHost process remained alive, so this is not evidence of a process crash. Its Settings state was not observed; no handoff PASS inferred. Stop bounded runtime attempts here rather than redesign or add alternate input injection. No OS accessibility preference changed.

Runtime screenshots: .build/ui-runtime-seal/{home-empty-runtime,search-parent-runtime,search-reveal-runtime,follow-ups-ready-runtime,follow-ups-preparation-runtime}.png. Final production preview set: .build/ui-consumer-pass15/{home-after,chats-after,search-from-chats,follow-ups-after,follow-ups-preparation,settings-after}.png. Preview artifacts remain separately graded.

Copy decision: retain Prepared conversations / Prepare conversations… . Existing behavior checks source-wide prepared content for a time window and opens explicit preparation Settings. Selected/Choose would imply a conversation selector this implementation does not provide. No copy/behavior change necessary.

### Validation and review

Completed in this acceptance session on the same unchanged source, macOS arm64 Debug and /tmp/mws-ui-phase1-build DerivedData:

- Focused xcodebuild test: StatusModelTests, LocalSearchHitRevealTests, LocalMessageSearchConsentTests, LocalMessageSearchLiteralQueryTests, MemorySyncTests, TranscriptPresentationTests — 79 Swift Testing tests / five suites; XCTest27 / zero failures; /tmp/mws-ui-seal-focused-tests.log, TEST SUCCEEDED.
- Full xcodebuild test — 601 Swift Testing tests /67 suites; XCTest27 / zero failures; /tmp/mws-ui-seal-full-tests.log, TEST SUCCEEDED. realArchiveAcceptance skipped because DUKOU_FIXTURE_PATH absent. In-process canonical-store isolation test passed; no external pre-host fingerprint capsule established.
- Separate Debug build — BUILD SUCCEEDED; /tmp/mws-ui-seal-debug-build.log.
- Final hash verification: eight inherited Swift files unchanged. git diff --check passed. Tests/build not repeated after scratch-host activity because product/test source did not change.

Independent complete-phase review by Parfit found no Critical/Important finding, reviewed full tracked diff, plan, user contract and six production screenshots, and recommended this partial verdict. A supplemental review of newly obtained scratch-host evidence was requested but could not complete because the reviewer hit its usage limit. Host/evidence supplemental review is therefore not independently accepted; main-agent inspection only.

### Seal package

Tracked diff: eight files, +1,076/-401:

- Product: AppModel.swift (navigation parent and selection-intent guard), ContentView.swift (consumer shell, Home, reader, Follow-ups, Settings, project-local primitives), TranscriptPresentation.swift (grouping/accessibility presentation).
- Tests: LocalMessageSearchLiteralQueryTests.swift, LocalMessageSearchTests.swift, MemorySyncTests.swift, StatusModelTests.swift, TranscriptPresentationTests.swift.
- Untracked: this finalized plan, which belongs in a future seal commit as rationale, scope and acceptance record.

Do not include ignored host, screenshots, manifests or temporary logs in the seal commit. Proposed subject: feat(v2): simplify navigation and conversation workflows.

Isolated AcceptanceHost was terminated after the native connection failed; exact process absence verified. Existing user Companion instances were not terminated. No commit/push, staging, reset/stash/rollback or Vault write-back. Canonical head_commit remains unchanged. Stop here for the user's final seal-package review; PRODUCT MET is not claimed.
