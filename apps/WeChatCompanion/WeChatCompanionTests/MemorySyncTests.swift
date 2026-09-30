import Foundation
import Testing
@testable import WeChatCompanion

/// App-owned Memory sync (M2.2c): consent-gated, foreground, explicit; the
/// runner is a fake so no memory layer, Python, or file is touched.
private final class FakeMemorySyncRunner: MemorySyncRunning, @unchecked Sendable {
    var outcomes: [MemorySyncOutcome]
    var freshnessToReport: MemoryFreshnessSummary?
    private(set) var syncCalls: [MemorySource] = []
    private(set) var freshnessCalls: [MemorySource] = []

    init(outcomes: [MemorySyncOutcome], freshness: MemoryFreshnessSummary? = nil) {
        self.outcomes = outcomes
        self.freshnessToReport = freshness
    }

    func sync(source: MemorySource) async -> MemorySyncOutcome {
        syncCalls.append(source)
        return outcomes.isEmpty ? .failed(.runnerUnavailable) : outcomes.removeFirst()
    }

    func freshness(source: MemorySource) async -> MemoryFreshnessSummary? {
        freshnessCalls.append(source)
        return freshnessToReport
    }
}



private final class FakeDailySummaryRunner: DailySummaryRunning, @unchecked Sendable {
    struct Call: Equatable {
        let source: MemorySource
        let start: Date
        let end: Date
        let messageLimit: Int
    }

    var outcomes: [DailySummaryOutcome]
    private(set) var calls: [Call] = []

    init(outcomes: [DailySummaryOutcome]) {
        self.outcomes = outcomes
    }

    func prepare(
        source: MemorySource,
        start: Date,
        end: Date,
        messageLimit: Int
    ) async -> DailySummaryOutcome {
        calls.append(Call(source: source, start: start, end: end, messageLimit: messageLimit))
        return outcomes.isEmpty ? .failed(.runnerUnavailable) : outcomes.removeFirst()
    }
}

private actor HeldMemorySyncRunner: MemorySyncRunning {
    private(set) var calls: [MemorySource] = []
    private var completion: CheckedContinuation<MemorySyncOutcome, Never>?
    private var started: CheckedContinuation<Void, Never>?

    func sync(source: MemorySource) async -> MemorySyncOutcome {
        calls.append(source)
        return await withCheckedContinuation { continuation in
            completion = continuation
            started?.resume()
            started = nil
        }
    }
    func waitUntilStarted() async {
        if completion == nil {
            await withCheckedContinuation { started = $0 }
        }
    }
    func finish() {
        completion?.resume(returning: .failed(.runnerUnavailable))
        completion = nil
    }
    func freshness(source: MemorySource) async -> MemoryFreshnessSummary? { nil }
}

private func dailySnapshot(source: MemorySource = .archive) -> DailySummarySnapshot {
    let start = Date(timeIntervalSince1970: 1_700_000_000)
    let end = Date(timeIntervalSince1970: 1_700_003_600)
    return DailySummarySnapshot(
        source: source,
        start: start,
        end: end,
        returnedMessages: 1,
        returnedConversations: 1,
        returnedSenders: 1,
        textTruncatedCount: 0,
        truncated: false,
        coverage: DailySummaryCoverage(status: "complete", trustworthyEmpty: true, caveats: []),
        freshness: freshness(source: source),
        conversations: [
            DailySummaryConversation(
                id: 0,
                label: source == .archive ? "Imported archive export" : "Captured chat",
                messageCount: 1,
                senderCount: 1,
                firstAt: start,
                lastAt: end
            ),
        ],
        senders: [DailySummarySenderCount(sender: "A", count: 1)],
        messages: [
            DailySummaryMessage(
                id: 0,
                conversationIndex: 0,
                source: source,
                timestamp: start,
                timestampKind: source == .archive ? "source_created" : "first_observed",
                sender: "A",
                kind: "text",
                text: "hello",
                textTruncated: false
            ),
        ]
    )
}



private final class FakeFollowUpRunner: FollowUpCandidateRunning, @unchecked Sendable {
    struct Call: Equatable {
        let source: MemorySource
        let start: Date
        let end: Date
        let messageLimit: Int
        let candidateLimit: Int
    }

    var outcomes: [FollowUpOutcome]
    private(set) var calls: [Call] = []

    init(outcomes: [FollowUpOutcome]) {
        self.outcomes = outcomes
    }

    func scan(
        source: MemorySource,
        start: Date,
        end: Date,
        messageLimit: Int,
        candidateLimit: Int
    ) async -> FollowUpOutcome {
        calls.append(.init(
            source: source,
            start: start,
            end: end,
            messageLimit: messageLimit,
            candidateLimit: candidateLimit
        ))
        return outcomes.isEmpty ? .failed(.runnerUnavailable) : outcomes.removeFirst()
    }
}

private func followUpSnapshot(
    source: MemorySource = .archive,
    textTruncated: Bool = false,
    coverageStatus: String = "partial"
) -> FollowUpCandidateSnapshot {
    let start = Date(timeIntervalSince1970: 1_700_000_000)
    let end = Date(timeIntervalSince1970: 1_700_003_600)
    return FollowUpCandidateSnapshot(
        source: source,
        start: start,
        end: end,
        scannedMessages: 4,
        returnedCandidates: 1,
        textTruncatedCount: textTruncated ? 1 : 0,
        truncated: false,
        coverage: FollowUpCoverage(
            status: coverageStatus,
            trustworthyEmpty: coverageStatus == "complete",
            caveats: coverageStatus == "complete" ? [] : ["archive:partial"]
        ),
        freshness: freshness(source: source, coverage: coverageStatus),
        conversations: [
            FollowUpConversation(
                id: 0,
                label: source == .archive ? "Imported archive export" : "Captured chat"
            ),
        ],
        candidates: [
            FollowUpCandidate(
                id: 0,
                conversationIndex: 0,
                source: source,
                timestamp: Date(timeIntervalSince1970: 1_700_001_200),
                timestampKind: source == .archive ? "source_created" : "first_observed",
                sender: "A",
                text: "麻烦明天确认一下报价",
                textTruncated: textTruncated,
                reasons: ["explicit_request", "explicit_follow_up", "time_reference"]
            ),
        ]
    )
}

private func makeDefaults() -> UserDefaults {
    let suite = "WeChatCompanionTests-\(UUID().uuidString)"
    let defaults = UserDefaults(suiteName: suite)!
    defaults.removePersistentDomain(forName: suite)
    return defaults
}

private let counts = MemorySyncCounts(conversationsSeen: 2, messagesSeen: 13, messagesInserted: 3, messagesUpdated: 10)

private func freshness(
    source: MemorySource = .visual,
    state: String = "succeeded",
    failure: String? = nil,
    coverage: String = "complete"
) -> MemoryFreshnessSummary {
    MemoryFreshnessSummary(
        source: source,
        lastSuccessfulSync: Date(timeIntervalSince1970: 1_700_000_000),
        observedThrough: Date(timeIntervalSince1970: 1_699_999_880),   // 09:58
        completeThrough: Date(timeIntervalSince1970: 1_699_999_880),
        latestMessageAt: Date(timeIntervalSince1970: 1_699_998_920),   // 09:42
        lastRunState: state, lastRunFailure: failure, coverageSummary: coverage
    )
}

struct MemorySyncTests {
    @Test @MainActor
    func archiveSummaryNavigationSelectsArchiveAndNeverRunsWork() async {
        let sync = FakeMemorySyncRunner(outcomes: [])
        let summary = FakeDailySummaryRunner(outcomes: [])
        let model = AppModel(messageHistory: makeTestMessageHistory(),
                             consentDefaults: makeDefaults(), memorySync: sync,
                             dailySummary: summary)
        await model.setAllowsLocalPersistence(true)
        model.setDailySummarySource(.visual)
        model.setDailySummaryWindow(.yesterday)
        model.openArchiveDailySummary()
        #expect(model.selectedDestination == .dailySummary)
        #expect(model.dailySummarySource == .archive)
        #expect(model.dailySummaryWindow == .yesterday)
        #expect(model.memorySource == .visual)
        #expect(model.dailySummaryPhase == .idle)
        #expect(sync.syncCalls.isEmpty)
        #expect(summary.calls.isEmpty)
    }

    @Test @MainActor
    func summaryMemoryShortcutSelectsArchiveWithoutSyncOrPreparation() async {
        let sync = FakeMemorySyncRunner(outcomes: [], freshness: freshness(source: .archive))
        let summary = FakeDailySummaryRunner(outcomes: [])
        let model = AppModel(messageHistory: makeTestMessageHistory(),
                             consentDefaults: makeDefaults(), memorySync: sync,
                             dailySummary: summary)
        await model.setAllowsLocalPersistence(true)
        model.setDailySummarySource(.archive)
        await model.openDailySummaryMemorySettings()
        #expect(model.selectedDestination == .settings)
        #expect(model.memorySource == .archive)
        #expect(model.memoryFreshness?.source == .archive)
        #expect(model.consumeMemorySettingsRequest())
        #expect(!model.consumeMemorySettingsRequest())
        #expect(sync.syncCalls.isEmpty)
        #expect(summary.calls.isEmpty)
        model.setDailySummarySource(.visual)
        await model.openDailySummaryMemorySettings()
        #expect(model.memorySource == .visual)
        #expect(sync.syncCalls.isEmpty)
    }

    @Test @MainActor
    func incompatibleMemorySyncBlocksSummaryShortcutWithoutChangingSource() async {
        let sync = HeldMemorySyncRunner()
        let summary = FakeDailySummaryRunner(outcomes: [])
        let model = AppModel(messageHistory: makeTestMessageHistory(),
                             consentDefaults: makeDefaults(), memorySync: sync,
                             dailySummary: summary)
        await model.setAllowsLocalPersistence(true)
        model.setDailySummarySource(.archive)
        model.selectedDestination = .dailySummary
        let operation = Task { await model.syncMemoryNow() }
        await sync.waitUntilStarted()
        #expect(!model.canOpenDailySummaryMemorySettings)
        await model.openDailySummaryMemorySettings()
        #expect(model.selectedDestination == .dailySummary)
        #expect(model.memorySource == .visual)
        #expect(model.memorySyncPhase == .running)
        #expect(!model.consumeMemorySettingsRequest())
        #expect(await sync.calls == [.visual])
        #expect(summary.calls.isEmpty)
        await sync.finish()
        await operation.value
        #expect(model.canOpenDailySummaryMemorySettings)
    }

    @Test @MainActor
    func storageOffNavigationDoesNotReadSyncOrPrepareMemory() async {
        let sync = FakeMemorySyncRunner(outcomes: [])
        let summary = FakeDailySummaryRunner(outcomes: [])
        let model = AppModel(messageHistory: makeTestMessageHistory(),
                             consentDefaults: makeDefaults(), memorySync: sync,
                             dailySummary: summary)
        model.openArchiveDailySummary()
        await model.openDailySummaryMemorySettings()
        #expect(model.memorySource == .archive)
        #expect(!model.isMemoryAvailable)
        #expect(!model.canPrepareDailySummary)
        #expect(sync.freshnessCalls.isEmpty)
        #expect(sync.syncCalls.isEmpty)
        #expect(summary.calls.isEmpty)
    }

    @Test @MainActor
    func dailySummaryDefaultsToArchiveWithoutSyncingOrPreparing() {
        let sync = FakeMemorySyncRunner(outcomes: [])
        let summary = FakeDailySummaryRunner(outcomes: [])
        let model = AppModel(messageHistory: makeTestMessageHistory(),
                             consentDefaults: makeDefaults(), memorySync: sync,
                             dailySummary: summary)
        #expect(model.dailySummarySource == .archive)
        #expect(model.dailySummaryPhase == .idle)
        #expect(sync.syncCalls.isEmpty)
        #expect(summary.calls.isEmpty)
    }

    @Test @MainActor
    func explicitVisualSummarySelectionSurvivesOrdinaryNavigation() {
        let model = AppModel(messageHistory: makeTestMessageHistory(),
                             consentDefaults: makeDefaults())
        model.setDailySummarySource(.visual)
        model.selectedDestination = .chats
        model.selectedDestination = .dailySummary
        #expect(model.dailySummarySource == .visual)
        model.setDailySummarySource(.database)
        #expect(model.dailySummarySource == .visual)
    }

    @Test
    func archiveSummaryAndMemoryPreparationControlsAreWired() throws {
        let file = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .deletingLastPathComponent().appendingPathComponent("WeChatCompanion/ContentView.swift")
        let source = try String(contentsOf: file, encoding: .utf8)
        let archive = try #require(source.components(separatedBy: "private struct ArchiveEvidenceBrowser: View {").last?
            .components(separatedBy: "private struct ArchiveLinkControls:").first)
        let summary = try #require(source.components(separatedBy: "private struct DailySummaryView: View {").last?
            .components(separatedBy: "private struct DailySummarySnapshotView:").first)
        #expect(archive.contains("model.openArchiveDailySummary()"))
        #expect(summary.contains("model.openDailySummaryMemorySettings()"))
    }

    @Test @MainActor
    func aFreshInstallCannotSyncAndNeverReachesTheRunner() async {
        let runner = FakeMemorySyncRunner(outcomes: [.succeeded(counts, freshness())])
        let model = AppModel(messageHistory: makeTestMessageHistory(), consentDefaults: makeDefaults(), memorySync: runner)
        #expect(model.isMemoryAvailable == false)
        #expect(model.canSyncMemory == false)
        await model.syncMemoryNow()
        #expect(model.memorySyncPhase == .failed(.consentWithheld))
        #expect(runner.syncCalls.isEmpty)
        #expect(model.memoryFreshness == nil)
    }

    @Test @MainActor
    func theActiveSourceIsVisibleAndIsTheVisualStore() {
        let model = AppModel(messageHistory: makeTestMessageHistory(), consentDefaults: makeDefaults(), memorySync: FakeMemorySyncRunner(outcomes: []))
        #expect(model.memorySource == .visual)
        #expect(model.memorySource.label.contains("Visual"))
    }

    @Test @MainActor
    func archiveIsAppSelectableButDatabaseIsNot() async {
        let runner = FakeMemorySyncRunner(
            outcomes: [.succeeded(counts, freshness(source: .archive))],
            freshness: freshness(source: .archive)
        )
        let model = AppModel(
            messageHistory: makeTestMessageHistory(),
            consentDefaults: makeDefaults(),
            memorySync: runner
        )
        await model.setAllowsLocalPersistence(true)

        await model.setMemorySource(.archive)
        #expect(model.memorySource == .archive)
        #expect(runner.freshnessCalls.last == .archive)

        await model.syncMemoryNow()
        #expect(runner.syncCalls == [.archive])
        #expect(model.memoryFreshness?.source == .archive)

        await model.setMemorySource(.database)
        #expect(model.memorySource == .archive)
        #expect(MemorySource.appSelectable == [.visual, .archive])
    }

    @Test @MainActor
    func aConsentedSyncRunsInTheForegroundAndRefreshesFreshness() async {
        let runner = FakeMemorySyncRunner(outcomes: [.succeeded(counts, freshness())])
        let model = AppModel(messageHistory: makeTestMessageHistory(), consentDefaults: makeDefaults(), memorySync: runner)
        await model.setAllowsLocalPersistence(true)
        #expect(model.canSyncMemory)
        await model.syncMemoryNow()
        #expect(model.memorySyncPhase == .succeeded(counts))
        #expect(runner.syncCalls == [.visual])
        #expect(model.memoryFreshness == freshness())
        // The three timestamps stay three different facts.
        let f = model.memoryFreshness!
        #expect(f.latestMessageAt! < f.observedThrough!)
        #expect(f.observedThrough! < f.lastSuccessfulSync!)
    }

    @Test @MainActor
    func freshnessIsFetchedWhenTheRunnerDoesNotReturnItInline() async {
        let runner = FakeMemorySyncRunner(outcomes: [.succeeded(counts, nil)], freshness: freshness())
        let model = AppModel(messageHistory: makeTestMessageHistory(), consentDefaults: makeDefaults(), memorySync: runner)
        await model.setAllowsLocalPersistence(true)
        await model.syncMemoryNow()
        #expect(runner.freshnessCalls == [.visual])
        #expect(model.memoryFreshness == freshness())
    }

    @Test @MainActor
    func aRepeatedSyncIsJustAnotherForegroundRun() async {
        let runner = FakeMemorySyncRunner(outcomes: [
            .succeeded(counts, freshness()),
            .succeeded(MemorySyncCounts(conversationsSeen: 2, messagesSeen: 13, messagesInserted: 0, messagesUpdated: 13), freshness()),
        ])
        let model = AppModel(messageHistory: makeTestMessageHistory(), consentDefaults: makeDefaults(), memorySync: runner)
        await model.setAllowsLocalPersistence(true)
        await model.syncMemoryNow()
        await model.syncMemoryNow()
        #expect(runner.syncCalls.count == 2)
        if case .succeeded(let second) = model.memorySyncPhase {
            #expect(second.messagesInserted == 0 && second.messagesUpdated == 13)
        } else {
            Issue.record("expected a second success")
        }
    }

    @Test @MainActor
    func aSyncFailureIsDistinctFromIncompleteCoverage() async {
        // Earlier good state with partial coverage, then a failed run.
        let runner = FakeMemorySyncRunner(outcomes: [
            .succeeded(counts, freshness(coverage: "partial")),
            .failed(.ingestionFailed(state: "reader_unavailable")),
        ])
        let model = AppModel(messageHistory: makeTestMessageHistory(), consentDefaults: makeDefaults(), memorySync: runner)
        await model.setAllowsLocalPersistence(true)
        await model.syncMemoryNow()
        #expect(model.memoryFreshness?.coverageSummary == "partial")
        await model.syncMemoryNow()
        #expect(model.memorySyncPhase == .failed(.ingestionFailed(state: "reader_unavailable")))
        // The last known-good freshness is not erased by the failure.
        #expect(model.memoryFreshness?.coverageSummary == "partial")
        #expect(model.memoryFreshness?.lastSuccessfulSync != nil)
        #expect(MemorySyncFailure.ingestionFailed(state: "reader_unavailable").message.contains("last successful state is kept"))
    }

    @Test @MainActor
    func aSelectedSourceThatIsUnavailableFailsWithoutSubstitution() async {
        let runner = FakeMemorySyncRunner(outcomes: [.failed(.sourceUnavailable(state: "reader_not_configured"))])
        let model = AppModel(messageHistory: makeTestMessageHistory(), consentDefaults: makeDefaults(), memorySync: runner)
        await model.setAllowsLocalPersistence(true)
        await model.syncMemoryNow()
        #expect(model.memorySyncPhase == .failed(.sourceUnavailable(state: "reader_not_configured")))
        #expect(MemorySyncFailure.sourceUnavailable(state: "x").message.contains("No other source was used"))
        #expect(runner.syncCalls == [.visual])
    }

    @Test @MainActor
    func revokingConsentTakesEffectImmediatelyAndDeletesNothing() async {
        let runner = FakeMemorySyncRunner(outcomes: [.succeeded(counts, freshness())])
        let model = AppModel(messageHistory: makeTestMessageHistory(), consentDefaults: makeDefaults(), memorySync: runner)
        await model.setAllowsLocalPersistence(true)
        await model.syncMemoryNow()
        await model.setAllowsLocalPersistence(false)
        #expect(model.isMemoryAvailable == false)
        #expect(model.canSyncMemory == false)
        #expect(model.memorySyncPhase == .idle)
        // Retained, not deleted: withdrawing consent is not a delete request.
        #expect(model.memoryFreshness == freshness())
        await model.syncMemoryNow()
        #expect(model.memorySyncPhase == .failed(.consentWithheld))
        #expect(runner.syncCalls.count == 1)
        await model.refreshMemoryFreshness()
        #expect(runner.freshnessCalls.isEmpty)
    }



    @Test @MainActor
    func dailySummaryRequiresConsentAndNeverReachesTheRunner() async {
        let summaryRunner = FakeDailySummaryRunner(outcomes: [.ready(dailySnapshot())])
        let model = AppModel(
            messageHistory: makeTestMessageHistory(),
            consentDefaults: makeDefaults(),
            memorySync: FakeMemorySyncRunner(outcomes: []),
            dailySummary: summaryRunner
        )

        await model.prepareDailySummary()

        #expect(model.dailySummaryPhase == .failed(.consentWithheld))
        #expect(model.dailySummarySnapshot == nil)
        #expect(summaryRunner.calls.isEmpty)
    }

    @Test @MainActor
    func dailySummaryReadsAnIndependentSourceAndBoundedWindowWithoutSyncing() async {
        let syncRunner = FakeMemorySyncRunner(outcomes: [])
        let summaryRunner = FakeDailySummaryRunner(outcomes: [.ready(dailySnapshot(source: .archive))])
        let model = AppModel(
            messageHistory: makeTestMessageHistory(),
            consentDefaults: makeDefaults(),
            memorySync: syncRunner,
            dailySummary: summaryRunner
        )
        await model.setAllowsLocalPersistence(true)
        model.setDailySummarySource(.archive)
        model.setDailySummaryWindow(.last24Hours)
        let now = Date(timeIntervalSince1970: 1_800_000_000)

        await model.prepareDailySummary(now: now)

        #expect(model.dailySummaryPhase == .ready)
        #expect(model.dailySummarySnapshot == dailySnapshot(source: .archive))
        #expect(syncRunner.syncCalls.isEmpty)
        #expect(summaryRunner.calls == [
            .init(
                source: .archive,
                start: now.addingTimeInterval(-24 * 60 * 60),
                end: now,
                messageLimit: 200
            ),
        ])
        model.setDailySummarySource(.database)
        #expect(model.dailySummarySource == .archive)
    }

    @Test @MainActor
    func changingDailySummaryScopeAndRevokingConsentClearTheInMemorySnapshot() async {
        let summaryRunner = FakeDailySummaryRunner(outcomes: [
            .ready(dailySnapshot(source: .visual)),
            .ready(dailySnapshot(source: .visual)),
        ])
        let model = AppModel(
            messageHistory: makeTestMessageHistory(),
            consentDefaults: makeDefaults(),
            memorySync: FakeMemorySyncRunner(outcomes: []),
            dailySummary: summaryRunner
        )
        await model.setAllowsLocalPersistence(true)
        await model.prepareDailySummary(now: Date(timeIntervalSince1970: 1_800_000_000))
        #expect(model.dailySummarySnapshot != nil)

        model.setDailySummaryWindow(.yesterday)
        #expect(model.dailySummarySnapshot == nil)
        #expect(model.dailySummaryPhase == .idle)

        await model.prepareDailySummary(now: Date(timeIntervalSince1970: 1_800_000_000))
        #expect(model.dailySummarySnapshot != nil)
        await model.setAllowsLocalPersistence(false)
        #expect(model.dailySummarySnapshot == nil)
        #expect(model.dailySummaryPhase == .idle)
    }



    @Test @MainActor
    func followUpScanningRequiresConsentAndNeverReachesTheRunner() async {
        let runner = FakeFollowUpRunner(outcomes: [.ready(followUpSnapshot())])
        let model = AppModel(
            messageHistory: makeTestMessageHistory(),
            consentDefaults: makeDefaults(),
            memorySync: FakeMemorySyncRunner(outcomes: []),
            followUpCandidates: runner,
            reminderStore: VolatileReminderStore()
        )

        await model.scanFollowUps()

        #expect(model.followUpPhase == .failed(.consentWithheld))
        #expect(model.followUpSnapshot == nil)
        #expect(runner.calls.isEmpty)
    }

    @Test @MainActor
    func followUpScanningIsBoundedIndependentAndNeverSyncsMemory() async {
        let syncRunner = FakeMemorySyncRunner(outcomes: [])
        let runner = FakeFollowUpRunner(outcomes: [.ready(followUpSnapshot(source: .archive))])
        let model = AppModel(
            messageHistory: makeTestMessageHistory(),
            consentDefaults: makeDefaults(),
            memorySync: syncRunner,
            followUpCandidates: runner,
            reminderStore: VolatileReminderStore()
        )
        await model.setAllowsLocalPersistence(true)
        model.setFollowUpSource(.archive)
        model.setFollowUpWindow(.last24Hours)
        let now = Date(timeIntervalSince1970: 1_800_000_000)

        await model.scanFollowUps(now: now)

        #expect(model.followUpPhase == .ready)
        #expect(model.followUpSnapshot == followUpSnapshot(source: .archive))
        #expect(syncRunner.syncCalls.isEmpty)
        #expect(runner.calls == [
            .init(
                source: .archive,
                start: now.addingTimeInterval(-24 * 60 * 60),
                end: now,
                messageLimit: 200,
                candidateLimit: 50
            ),
        ])
        model.setFollowUpSource(.database)
        #expect(model.followUpSource == .archive)
    }

    @Test @MainActor
    func savingAFollowUpPersistsEvidenceCoverageAndRequiresUnclippedEvidence() async {
        let reminderStore = VolatileReminderStore()
        let runner = FakeFollowUpRunner(outcomes: [
            .ready(followUpSnapshot(source: .archive, coverageStatus: "partial")),
            .ready(followUpSnapshot(source: .archive, textTruncated: true)),
        ])
        let model = AppModel(
            messageHistory: makeTestMessageHistory(),
            consentDefaults: makeDefaults(),
            memorySync: FakeMemorySyncRunner(outcomes: []),
            followUpCandidates: runner,
            reminderStore: reminderStore
        )
        await model.setAllowsLocalPersistence(true)
        model.setFollowUpSource(.archive)

        await model.scanFollowUps(now: Date(timeIntervalSince1970: 1_800_000_000))
        await model.saveFollowUpCandidate(0, now: Date(timeIntervalSince1970: 1_800_000_100))

        #expect(model.savedFollowUps.count == 1)
        let saved = try! #require(model.savedFollowUps.first)
        #expect(saved.source == .archive)
        #expect(saved.conversationLabel == "Imported archive export")
        #expect(saved.evidenceTimestampKind == "source_created")
        #expect(saved.scanWindowStart == Date(timeIntervalSince1970: 1_700_000_000))
        #expect(saved.scanWindowEnd == Date(timeIntervalSince1970: 1_700_003_600))
        #expect(saved.coverageStatus == "partial")
        #expect(saved.coverageCaveats == ["archive:partial"])
        #expect(saved.reasons.contains("time_reference"))
        #expect(saved.status == .pending)

        await model.scanFollowUps(now: Date(timeIntervalSince1970: 1_800_000_000))
        await model.saveFollowUpCandidate(0)
        #expect(model.savedFollowUps.count == 1)
    }

    @Test @MainActor
    func savedFollowUpsCanBeCompletedReopenedDeletedAndSurviveConsentWithdrawal() async {
        let reminderStore = VolatileReminderStore()
        let runner = FakeFollowUpRunner(outcomes: [.ready(followUpSnapshot(source: .visual))])
        let model = AppModel(
            messageHistory: makeTestMessageHistory(),
            consentDefaults: makeDefaults(),
            memorySync: FakeMemorySyncRunner(outcomes: []),
            followUpCandidates: runner,
            reminderStore: reminderStore
        )
        await model.setAllowsLocalPersistence(true)
        await model.scanFollowUps(now: Date(timeIntervalSince1970: 1_800_000_000))
        await model.saveFollowUpCandidate(0)
        let id = try! #require(model.savedFollowUps.first?.id)

        await model.setSavedFollowUpStatus(id, status: .completed)
        #expect(model.savedFollowUps.first?.status == .completed)
        await model.setSavedFollowUpStatus(id, status: .pending)
        #expect(model.savedFollowUps.first?.status == .pending)

        await model.setAllowsLocalPersistence(false)
        #expect(model.savedFollowUps.isEmpty)
        await model.setAllowsLocalPersistence(true)
        #expect(model.savedFollowUps.count == 1)

        await model.deleteSavedFollowUp(id)
        #expect(model.savedFollowUps.isEmpty)
    }

    @Test
    func localReminderStoreIsAtomicPrivateVersionedAndIdempotent() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("ReminderStoreTests-\(UUID().uuidString)", isDirectory: true)
        let url = root.appendingPathComponent("reminders.json")
        defer { try? FileManager.default.removeItem(at: root) }

        let store = LocalReminderStore(url: url)
        let reminder = SavedFollowUp(
            id: UUID(),
            source: .archive,
            conversationLabel: "Imported archive export",
            sender: "A",
            evidenceTimestamp: Date(timeIntervalSince1970: 1_700_001_200),
            evidenceTimestampKind: "source_created",
            scanWindowStart: Date(timeIntervalSince1970: 1_700_000_000),
            scanWindowEnd: Date(timeIntervalSince1970: 1_700_003_600),
            coverageStatus: "partial",
            coverageCaveats: ["archive:partial"],
            savedAt: Date(timeIntervalSince1970: 1_800_000_000),
            text: "麻烦明天确认一下报价",
            reasons: ["explicit_request", "time_reference"],
            status: .pending
        )

        #expect(try await store.load().isEmpty)
        #expect(try await store.add(reminder).count == 1)
        #expect(try await store.add(reminder).count == 1)
        #expect(try await store.load() == [reminder])

        let directoryMode = try FileManager.default.attributesOfItem(atPath: root.path)[.posixPermissions] as? NSNumber
        let fileMode = try FileManager.default.attributesOfItem(atPath: url.path)[.posixPermissions] as? NSNumber
        #expect(directoryMode?.intValue == 0o700)
        #expect(fileMode?.intValue == 0o600)

        let raw = try Data(contentsOf: url)
        let object = try JSONSerialization.jsonObject(with: raw) as? [String: Any]
        #expect(object?["version"] as? Int == 1)

        let completed = try await store.setStatus(id: reminder.id, status: .completed)
        #expect(completed.first?.status == .completed)
        #expect(try await store.delete(id: reminder.id).isEmpty)
    }

    @Test
    func localReminderStoreRefusesUnsupportedOrMalformedFilesWithoutRewritingThem() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("ReminderStoreRefusal-\(UUID().uuidString)", isDirectory: true)
        let url = root.appendingPathComponent("reminders.json")
        defer { try? FileManager.default.removeItem(at: root) }
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)

        let unsupported = Data(#"{"version":99,"reminders":[]}"#.utf8)
        try unsupported.write(to: url)
        let store = LocalReminderStore(url: url)
        await #expect(throws: ReminderStoreError.unsupportedVersion) {
            _ = try await store.load()
        }
        #expect(try Data(contentsOf: url) == unsupported)

        let malformed = Data("not json".utf8)
        try malformed.write(to: url)
        await #expect(throws: ReminderStoreError.malformed) {
            _ = try await store.load()
        }
        #expect(try Data(contentsOf: url) == malformed)
    }

    @Test @MainActor
    func aBuildWithoutAWorkerReportsThePackagingGapInsteadOfFakingASync() async {
        // The M2.2c contract, still exact: with no worker to run, the app says
        // so rather than pretending a sync happened.
        let model = AppModel(messageHistory: makeTestMessageHistory(), consentDefaults: makeDefaults(),
                             memorySync: UnavailableMemorySyncRunner())
        await model.setAllowsLocalPersistence(true)
        await model.syncMemoryNow()
        #expect(model.memorySyncPhase == .failed(.runnerUnavailable))
        #expect(MemorySyncFailure.runnerUnavailable.message.contains("operator command line"))
        #expect(model.memoryFreshness == nil)
    }

    @Test @MainActor
    func aTestHostNeverResolvesALiveRunnerAgainstTheRealStore() async {
        // A test host is an app bundle carrying a real worker. Resolving it
        // here would sync the user's own messages as a side effect of running
        // the suite; the default must be the inert runner instead.
        //
        // The contract is **non-mutation**, not absence. This test used to
        // assert the canonical memory store did not exist, which is false the
        // moment an operator legitimately uses Sync Now -- "the operator has
        // used the product" and "the suite touched the product's data" are
        // different claims, and only the second is a defect. Asserting the
        // first made the guard fail on exactly the machines it was written to
        // protect, which is how a guard gets deleted instead of fixed.
        let before = canonicalMemoryStoreFingerprints()

        #expect(AppModel.defaultMemorySyncRunner() is UnavailableMemorySyncRunner)
        let model = AppModel(messageHistory: makeTestMessageHistory(), consentDefaults: makeDefaults())
        await model.setAllowsLocalPersistence(true)
        await model.syncMemoryNow()
        #expect(model.memorySyncPhase == .failed(.runnerUnavailable))

        let after = canonicalMemoryStoreFingerprints()
        for key in ["db", "wal", "shm"] {
            #expect(after[key] == before[key],
                    "the canonical memory store's \(key) changed: \(before[key]!) -> \(after[key]!)")
        }
    }

    @Test
    func failureMessagesCarryTokensNotContentOrPaths() {
        for failure in [MemorySyncFailure.consentWithheld, .runnerUnavailable,
                        .sourceUnavailable(state: "reader_unavailable"), .ingestionFailed(state: "record_malformed")] {
            #expect(!failure.message.contains("/"))
            #expect(!failure.message.contains("sqlite"))
        }
    }
}
