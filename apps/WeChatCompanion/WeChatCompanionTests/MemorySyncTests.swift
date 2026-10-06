import Foundation
import Testing
@testable import WeChatCompanion

private actor OverviewTransport: GeminiTransporting {
    private(set) var calls = 0
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        calls += 1
        throw URLError(.cancelled)
    }
}

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
                reasons: ["explicit_request", "explicit_follow_up", "time_reference"],
                archiveEvidence: source == .archive
                    ? ArchiveEvidenceAnchor(importID: 1, sequence: 0) : nil
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

private actor RecordingReminderStore: ReminderStoring {
    private(set) var calls: [String] = []

    func load() async throws -> [SavedFollowUp] { calls.append("load"); return [] }
    func add(_ reminder: SavedFollowUp) async throws -> [SavedFollowUp] { calls.append("add"); return [] }
    func setStatus(id: UUID, status: SavedFollowUpStatus) async throws -> [SavedFollowUp] {
        calls.append("setStatus"); return []
    }
    func delete(id: UUID) async throws -> [SavedFollowUp] { calls.append("delete"); return [] }
}

private actor HeldFollowUpRunner: FollowUpCandidateRunning {
    private var completion: CheckedContinuation<FollowUpOutcome, Never>?
    private var started: CheckedContinuation<Void, Never>?

    func scan(source: MemorySource, start: Date, end: Date,
              messageLimit: Int, candidateLimit: Int) async -> FollowUpOutcome {
        await withCheckedContinuation { continuation in
            completion = continuation
            started?.resume()
            started = nil
        }
    }
    func waitUntilStarted() async {
        if completion == nil { await withCheckedContinuation { started = $0 } }
    }
    func finish() {
        completion?.resume(returning: .failed(.runnerUnavailable))
        completion = nil
    }
}

private actor HeldFreshnessRunner: MemorySyncRunning {
    private var completion: CheckedContinuation<MemoryFreshnessSummary?, Never>?
    private var started: CheckedContinuation<Void, Never>?

    func sync(source: MemorySource) async -> MemorySyncOutcome { .failed(.runnerUnavailable) }
    func freshness(source: MemorySource) async -> MemoryFreshnessSummary? {
        await withCheckedContinuation { continuation in
            completion = continuation
            started?.resume()
            started = nil
        }
    }
    func waitUntilStarted() async {
        if completion == nil { await withCheckedContinuation { started = $0 } }
    }
    func finish() {
        completion?.resume(returning: nil)
        completion = nil
    }
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
    func genericArchiveEntryClearsExpiredExactContextWithoutWork() async throws {
        let history = makeTestMessageHistory()
        let transport = OverviewTransport()
        let sync = FakeMemorySyncRunner(outcomes: [])
        let summary = FakeDailySummaryRunner(outcomes: [])
        let scan = FakeFollowUpRunner(outcomes: [])
        let defaults = makeDefaults()
        let model = AppModel(messageHistory: history, shareInbox: nil,
                             geminiTransport: transport, consentDefaults: defaults,
                             memorySync: sync, dailySummary: summary, followUpCandidates: scan)
        await model.setAllowsLocalPersistence(true)
        let transcript = try WeChatNativeTranscriptParser.parse(
            "·Fixture sender\n2026年9月7日 20:35\nstale-overview-target\n\n",
            timeZone: TimeZone(identifier: "Asia/Shanghai")!
        )
        _ = try await history.persistArchiveEvidence(
            transcript: transcript, conversationKey: ArchiveConversationKey("stale-overview"),
            importedAt: Date()
        )
        let hit = try #require(await history.searchLocalMessages("stale-overview-target", filter: .archive).results.first)
        await history.deleteAllHistory()
        await model.openSearchResult(hit)
        // A genuine failed exact reveal must still report its missing context.
        #expect(model.archiveContextUnavailable)
        #expect(model.archiveEvidence.storeState == .ready)
        #expect(model.archiveEvidence.imports.isEmpty)
        #expect(model.contextRevealRequest == nil)
        let syncCalls = sync.syncCalls
        let freshnessCalls = sync.freshnessCalls
        let consent = defaults.dictionaryRepresentation()
        model.selectedDestination = .overview
        await model.openArchiveBrowser()
        #expect(model.selectedDestination == .chats)
        #expect(model.contextNavigationTarget == .archiveBrowser)
        #expect(!model.archiveContextUnavailable)
        #expect(model.selectedArchiveImportID == nil)
        #expect(model.archiveEvidence.imports.isEmpty)
        #expect(model.archiveImportStatus == .idle)
        #expect(sync.syncCalls == syncCalls && sync.freshnessCalls == freshnessCalls)
        #expect(summary.calls.isEmpty && scan.calls.isEmpty)
        #expect(await transport.calls == 0)
        #expect(model.allowsLocalPersistence && !model.allowsRemoteProcessing)
        #expect(NSDictionary(dictionary: defaults.dictionaryRepresentation()).isEqual(to: consent))
        // Superseding the warning must not suppress a later real reveal failure.
        await model.openSearchResult(hit)
        #expect(model.archiveContextUnavailable)
        #expect(model.contextRevealRequest == nil)
    }

    @Test @MainActor
    func overviewArchiveEntryTargetsArchiveWithoutConsentOrWork() async {
        let history = makeTestMessageHistory()
        let transport = OverviewTransport()
        let sync = FakeMemorySyncRunner(outcomes: [])
        let summary = FakeDailySummaryRunner(outcomes: [])
        let scan = FakeFollowUpRunner(outcomes: [])
        let model = AppModel(messageHistory: history, shareInbox: nil,
                             geminiTransport: transport,
                             consentDefaults: makeDefaults(), memorySync: sync,
                             dailySummary: summary, followUpCandidates: scan)
        await model.openArchiveBrowser()
        #expect(model.selectedDestination == .chats)
        #expect(model.contextNavigationTarget == .archiveBrowser)
        #expect(!model.allowsLocalPersistence)
        #expect(!model.allowsRemoteProcessing)
        #expect(await history.hasOpenStore == false)
        #expect(model.archiveEvidence.storeState == .disabled)
        #expect(model.archiveImportStatus == .idle)
        #expect(sync.syncCalls.isEmpty && sync.freshnessCalls.isEmpty)
        #expect(summary.calls.isEmpty && scan.calls.isEmpty)
        #expect(model.dailySummaryPhase == .idle && model.followUpPhase == .idle)
        #expect(await transport.calls == 0)
    }

    @Test @MainActor
    func overviewArchiveEntryKeepsUnavailableStorageTruthful() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("overview-unavailable-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let database = root.appendingPathComponent("not-a-database")
        try Data("synthetic invalid database".utf8).write(to: database)
        let before = try Data(contentsOf: database)
        let history = LocalMessageHistory(url: database)
        let sync = FakeMemorySyncRunner(outcomes: [])
        let summary = FakeDailySummaryRunner(outcomes: [])
        let scan = FakeFollowUpRunner(outcomes: [])
        let model = AppModel(messageHistory: history, shareInbox: nil,
                             consentDefaults: makeDefaults(), memorySync: sync,
                             dailySummary: summary, followUpCandidates: scan)
        await model.setAllowsLocalPersistence(true)
        #expect(model.archiveEvidence.storeState == .unavailable)
        await model.openArchiveBrowser()
        #expect(model.selectedDestination == .chats)
        #expect(model.contextNavigationTarget == .archiveBrowser)
        #expect(model.allowsLocalPersistence)
        #expect(model.archiveEvidence.storeState == .unavailable)
        #expect(await history.hasOpenStore == false)
        #expect(try Data(contentsOf: database) == before)
        #expect(sync.syncCalls.isEmpty && summary.calls.isEmpty && scan.calls.isEmpty)
        #expect(model.archiveImportStatus == .idle)
    }

    @Test(arguments: [MemorySource.archive, .visual]) @MainActor
    func reminderMemoryShortcutMatchesCurrentSourceWithoutWork(source: MemorySource) async {
        let sync = FakeMemorySyncRunner(outcomes: [], freshness: freshness(source: source))
        let scan = FakeFollowUpRunner(outcomes: [])
        let summary = FakeDailySummaryRunner(outcomes: [])
        let store = RecordingReminderStore()
        let model = AppModel(messageHistory: makeTestMessageHistory(),
                             consentDefaults: makeDefaults(), memorySync: sync,
                             dailySummary: summary, followUpCandidates: scan, reminderStore: store)
        await model.setAllowsLocalPersistence(true)
        await model.setMemorySource(source == .archive ? .visual : .archive)
        model.setFollowUpSource(source)
        model.selectedDestination = .reminders
        let storeCalls = await store.calls
        await model.openFollowUpMemorySettings()
        #expect(model.selectedDestination == .settings)
        #expect(model.memorySource == source)
        #expect(model.followUpSource == source)
        #expect(model.memoryFreshness?.source == source)
        #expect(model.consumeMemorySettingsRequest())
        #expect(!model.consumeMemorySettingsRequest())
        #expect(sync.syncCalls.isEmpty)
        #expect(scan.calls.isEmpty)
        #expect(summary.calls.isEmpty)
        #expect(await store.calls == storeCalls)
    }

    @Test @MainActor
    func storageOffReminderShortcutDoesNotReadOrWriteMemory() async {
        let sync = FakeMemorySyncRunner(outcomes: [])
        let scan = FakeFollowUpRunner(outcomes: [])
        let summary = FakeDailySummaryRunner(outcomes: [])
        let store = RecordingReminderStore()
        let model = AppModel(messageHistory: makeTestMessageHistory(),
                             consentDefaults: makeDefaults(), memorySync: sync,
                             dailySummary: summary, followUpCandidates: scan, reminderStore: store)
        // Storage is off, so this preparation-section handoff is refused; the
        // reminder shortcut no longer implies the store is readable.
        await model.openFollowUpMemorySettings()
        #expect(!model.canOpenFollowUpMemorySettings)
        #expect(model.memorySource == .visual)
        #expect(model.preparationOrigin == nil)
        #expect(!model.consumeMemorySettingsRequest())
        #expect(!model.isMemoryAvailable)
        #expect(!model.canScanFollowUps)
        #expect(sync.freshnessCalls.isEmpty)
        #expect(sync.syncCalls.isEmpty)
        #expect(scan.calls.isEmpty)
        #expect(summary.calls.isEmpty)
        #expect(await store.calls.isEmpty)
    }

    @Test(arguments: [PreparationOrigin.dailySummary, .followUps]) @MainActor
    func aPreparationSectionHandoffIsRefusedWhileStorageIsOff(origin: PreparationOrigin) async {
        let sync = FakeMemorySyncRunner(outcomes: [])
        let summary = FakeDailySummaryRunner(outcomes: [])
        let scan = FakeFollowUpRunner(outcomes: [])
        let model = AppModel(messageHistory: makeTestMessageHistory(), consentDefaults: makeDefaults(),
                             memorySync: sync, dailySummary: summary, followUpCandidates: scan,
                             reminderStore: RecordingReminderStore())
        model.setDailySummarySource(.archive)
        model.setFollowUpSource(.archive)
        model.selectedDestination = (origin == .dailySummary ? .dailySummary : .reminders)

        #expect(!model.canOpenDailySummaryMemorySettings)
        #expect(!model.canOpenFollowUpMemorySettings)
        await model.openDailySummaryMemorySettings()
        await model.openFollowUpMemorySettings()

        // Refused: no navigation, no scroll request, no origin, no work.
        #expect(model.selectedDestination == (origin == .dailySummary ? .dailySummary : .reminders))
        #expect(model.preparationOrigin == nil)
        #expect(!model.hasMemorySettingsRequest)
        #expect(sync.syncCalls.isEmpty && sync.freshnessCalls.isEmpty)
        #expect(summary.calls.isEmpty)
        #expect(scan.calls.isEmpty)
    }

    @Test @MainActor
    func runningCandidateScanBlocksReminderMemoryHandoff() async {
        let sync = FakeMemorySyncRunner(outcomes: [])
        let scan = HeldFollowUpRunner()
        let model = AppModel(messageHistory: makeTestMessageHistory(),
                             consentDefaults: makeDefaults(), memorySync: sync, followUpCandidates: scan)
        await model.setAllowsLocalPersistence(true)
        model.selectedDestination = .reminders
        let operation = Task { await model.scanFollowUps() }
        await scan.waitUntilStarted()
        #expect(!model.canOpenFollowUpMemorySettings)
        await model.openFollowUpMemorySettings()
        #expect(model.selectedDestination == .reminders)
        #expect(model.memorySource == .visual)
        #expect(!model.consumeMemorySettingsRequest())
        #expect(sync.freshnessCalls.isEmpty)
        #expect(sync.syncCalls.isEmpty)
        model.setFollowUpSource(.visual)
        #expect(model.followUpSource == .archive)
        await scan.finish()
        await operation.value
        #expect(model.canOpenFollowUpMemorySettings)
    }

    @Test(arguments: [false, true]) @MainActor
    func reminderHandoffDuringMemorySyncRequiresMatchingSource(compatible: Bool) async {
        let sync = HeldMemorySyncRunner()
        let scan = FakeFollowUpRunner(outcomes: [])
        let model = AppModel(messageHistory: makeTestMessageHistory(),
                             consentDefaults: makeDefaults(), memorySync: sync, followUpCandidates: scan)
        await model.setAllowsLocalPersistence(true)
        if compatible { await model.setMemorySource(.archive) }
        model.selectedDestination = .reminders
        let operation = Task { await model.syncMemoryNow() }
        await sync.waitUntilStarted()
        #expect(model.canOpenFollowUpMemorySettings == compatible)
        await model.openFollowUpMemorySettings()
        #expect(model.selectedDestination == (compatible ? .settings : .reminders))
        #expect(model.memorySource == (compatible ? .archive : .visual))
        #expect(model.consumeMemorySettingsRequest() == compatible)
        #expect(await sync.calls == [compatible ? .archive : .visual])
        #expect(scan.calls.isEmpty)
        await sync.finish()
        await operation.value
    }

    @Test(arguments: [false, true]) @MainActor
    func reminderHandoffRevalidatesAfterAwaitedFreshness(startScan: Bool) async {
        let sync = HeldFreshnessRunner()
        let scan = HeldFollowUpRunner()
        let model = AppModel(messageHistory: makeTestMessageHistory(),
                             consentDefaults: makeDefaults(), memorySync: sync, followUpCandidates: scan)
        await model.setAllowsLocalPersistence(true)
        model.selectedDestination = .reminders
        let handoff = Task { await model.openFollowUpMemorySettings() }
        await sync.waitUntilStarted()
        var scanOperation: Task<Void, Never>?
        if startScan {
            scanOperation = Task { await model.scanFollowUps() }
            await scan.waitUntilStarted()
        } else {
            model.setFollowUpSource(.visual)
        }
        await sync.finish()
        await handoff.value
        #expect(model.selectedDestination == .reminders)
        #expect(!model.consumeMemorySettingsRequest())
        #expect(model.followUpSource == (startScan ? .archive : .visual))
        if let scanOperation {
            await scan.finish()
            await scanOperation.value
        }
    }

    @Test @MainActor
    func freshRemindersDefaultsToArchiveWithoutWork() async {
        let sync = FakeMemorySyncRunner(outcomes: [])
        let scan = FakeFollowUpRunner(outcomes: [])
        let summary = FakeDailySummaryRunner(outcomes: [])
        let store = RecordingReminderStore()
        let model = AppModel(messageHistory: makeTestMessageHistory(),
                             consentDefaults: makeDefaults(), memorySync: sync,
                             dailySummary: summary, followUpCandidates: scan, reminderStore: store)
        #expect(model.followUpSource == .archive)
        #expect(model.followUpPhase == .idle)
        #expect(sync.syncCalls.isEmpty)
        #expect(sync.freshnessCalls.isEmpty)
        #expect(scan.calls.isEmpty)
        #expect(summary.calls.isEmpty)
        #expect(await store.calls.isEmpty)
    }

    @Test @MainActor
    func explicitVisualReminderSelectionSurvivesOrdinaryNavigation() {
        let model = AppModel(messageHistory: makeTestMessageHistory(), consentDefaults: makeDefaults())
        model.setFollowUpSource(.visual)
        model.selectedDestination = .chats
        model.selectedDestination = .dailySummary
        model.selectedDestination = .reminders
        #expect(model.followUpSource == .visual)
        model.setFollowUpSource(.database)
        #expect(model.followUpSource == .visual)
    }

    @Test
    func reminderPreparationUsesTheExistingMemoryPanelAndExplicitScan() throws {
        let file = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .deletingLastPathComponent().appendingPathComponent("WeChatCompanion/ContentView.swift")
        let source = try String(contentsOf: file, encoding: .utf8)
        let start = try #require(source.range(of: "private struct FollowUpsView: View {"))
        let end = try #require(source.range(of: "private struct SavedFollowUpRow:"))
        let reminders = String(source[start.upperBound..<end.lowerBound])
        #expect(reminders.contains("model.openFollowUpMemorySettings()"))
        #expect(reminders.contains("model.canOpenFollowUpMemorySettings"))
        // Consumer wording is reviewed in previews. This wiring check keeps
        // the explicit preparation/scan boundary, rather than pinning copy.
        #expect(reminders.contains("Task { await model.scanFollowUps() }"))
        #expect(!reminders.contains("MemorySection("))
        #expect(!reminders.contains("syncMemoryNow()"))
    }

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
        // Storage is off, so the preparation-section handoff is refused outright
        // rather than quietly opening Advanced. The storage control is the route.
        #expect(!model.canOpenDailySummaryMemorySettings)
        await model.openDailySummaryMemorySettings()
        #expect(model.selectedDestination == .dailySummary)
        #expect(model.preparationOrigin == nil)
        #expect(!model.hasMemorySettingsRequest)
        #expect(model.memorySource == .visual)
        #expect(!model.isMemoryAvailable)
        #expect(!model.canPrepareDailySummary)
        #expect(sync.freshnessCalls.isEmpty)
        #expect(sync.syncCalls.isEmpty)
        #expect(summary.calls.isEmpty)
        // The refused preparation handoff leaves the storage route as the only
        // way out, and turning storage on still does no work on its own.
        model.openStorageSettings(from: .dailySummary)
        #expect(model.selectedDestination == .settings)
        #expect(model.preparationOrigin == .dailySummary)
        #expect(sync.freshnessCalls.isEmpty)
        #expect(sync.syncCalls.isEmpty)
        #expect(summary.calls.isEmpty)
        await model.setAllowsLocalPersistence(true)
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
        model.setFollowUpSource(.visual)
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
        #expect(object?["version"] as? Int == 2)

        let completed = try await store.setStatus(id: reminder.id, status: .completed)
        #expect(completed.first?.status == .completed)
        #expect(try await store.delete(id: reminder.id).isEmpty)
    }

    // MARK: - Canonical evidence reveal (B8)

    private func anchoredReminder(
        anchor: ArchiveEvidenceAnchor,
        text: String,
        source: MemorySource = .archive,
        evidenceTimestamp: TimeInterval = 1_700_001_200,
        savedAt: TimeInterval = 1_800_000_000
    ) -> SavedFollowUp {
        SavedFollowUp(
            id: UUID(),
            source: source,
            conversationLabel: "Imported archive export",
            sender: "林晓",
            evidenceTimestamp: Date(timeIntervalSince1970: evidenceTimestamp),
            evidenceTimestampKind: "source_created",
            scanWindowStart: Date(timeIntervalSince1970: 1_700_000_000),
            scanWindowEnd: Date(timeIntervalSince1970: 1_700_003_600),
            coverageStatus: "partial",
            coverageCaveats: ["archive:partial"],
            savedAt: Date(timeIntervalSince1970: savedAt),
            text: text,
            reasons: ["explicit_request"],
            status: .pending,
            archiveEvidence: anchor
        )
    }

    /// A model that already holds `reminders`, through the same store the app
    /// writes, so the reveal is exercised against real persisted state rather
    /// than a value poked into the model.
    private func modelWithSavedFollowUps(
        _ reminders: [SavedFollowUp],
        history: LocalMessageHistory,
        scan: FakeFollowUpRunner = FakeFollowUpRunner(outcomes: []),
        transport: OverviewTransport = OverviewTransport(),
        sync: FakeMemorySyncRunner = FakeMemorySyncRunner(outcomes: []),
        summary: FakeDailySummaryRunner = FakeDailySummaryRunner(outcomes: [])
    ) async throws -> AppModel {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("ReminderStoreReveal-\(UUID().uuidString)", isDirectory: true)
        let store = LocalReminderStore(url: root.appendingPathComponent("reminders.json"))
        for reminder in reminders { _ = try await store.add(reminder) }
        let model = await MainActor.run { AppModel(
            messageHistory: history, shareInbox: nil, geminiTransport: transport,
            consentDefaults: makeDefaults(), memorySync: sync,
            dailySummary: summary, followUpCandidates: scan, reminderStore: store
        ) }
        await model.setAllowsLocalPersistence(true)
        return model
    }

    @Test @MainActor
    func savedFollowUpRevealsItsExactArchiveRowAndRepeatsCleanly() async throws {
        let history = makeTestMessageHistory()
        await history.setEnabled(true)
        let transcript = try WeChatNativeTranscriptParser.parse(
            "·林晓\n2026年9月7日 20:35\nreveal-target-row\n\n",
            timeZone: TimeZone(identifier: "Asia/Shanghai")!
        )
        let imported = try await history.persistArchiveEvidence(
            transcript: transcript,
            conversationKey: ArchiveConversationKey("b8-reveal"),
            importedAt: Date()
        )
        guard case .inserted(let importID, _) = imported else {
            Issue.record("expected an archive import")
            return
        }
        let hit = try #require(
            await history.searchLocalMessages("reveal-target-row").results.first
        )
        guard case .archiveRecord(let hitImport, let sequence, let provenance) = hit.target else {
            Issue.record("expected an exact archive target")
            return
        }
        #expect(hitImport == importID && provenance == .archiveAttributed)

        let transport = OverviewTransport()
        let sync = FakeMemorySyncRunner(outcomes: [])
        let summary = FakeDailySummaryRunner(outcomes: [])
        let scan = FakeFollowUpRunner(outcomes: [])
        let reminder = anchoredReminder(
            anchor: ArchiveEvidenceAnchor(importID: hitImport, sequence: sequence),
            text: "reveal-target-row"
        )
        let model = try await modelWithSavedFollowUps(
            [reminder], history: history, scan: scan, transport: transport,
            sync: sync, summary: summary
        )
        #expect(model.savedFollowUps.map(\.id) == [reminder.id])

        // The reveal uses only the anchor, and reaches the exact row.
        await model.openSavedFollowUpEvidence(reminder.id)
        #expect(model.selectedDestination == .chats)
        #expect(model.selectedArchiveImportID == hitImport)
        #expect(model.selectedArchiveIsHitWindow)
        #expect(model.selectedArchiveRecords.contains(where: { $0.sequence == sequence }))
        #expect(model.contextRevealRequest?.anchor
                == .archiveRecord(importID: hitImport, sequence: sequence))
        #expect(!model.archiveContextUnavailable)
        #expect(!model.searchHitUnavailable)

        // Repeating the same request repeats the same reveal.
        await model.consumeContextReveal(generation: model.contextRevealRequest!.generation)
        await model.openSavedFollowUpEvidence(reminder.id)
        #expect(model.selectedArchiveImportID == hitImport)
        #expect(model.selectedArchiveRecords.contains(where: { $0.sequence == sequence }))
        #expect(model.contextRevealRequest?.anchor
                == .archiveRecord(importID: hitImport, sequence: sequence))

        // Revealing is navigation only: no sync, scan, summary, or network.
        #expect(scan.calls.isEmpty)
        #expect(await transport.calls == 0)
    }

    @Test @MainActor
    func savedFollowUpRevealTargetsItsOwnRowAndNeverANeighbouringOne() async throws {
        let history = makeTestMessageHistory()
        await history.setEnabled(true)
        let transcript = try WeChatNativeTranscriptParser.parse(
            "·林晓\n2026年9月7日 20:35\nfirst-anchor-row\n\n"
                + "·林晓\n2026年9月7日 20:36\nsecond-anchor-row\n\n",
            timeZone: TimeZone(identifier: "Asia/Shanghai")!
        )
        let imported = try await history.persistArchiveEvidence(
            transcript: transcript,
            conversationKey: ArchiveConversationKey("b8-two-rows"),
            importedAt: Date()
        )
        guard case .inserted(let importID, _) = imported else {
            Issue.record("expected an archive import")
            return
        }
        let firstHit = try #require(
            await history.searchLocalMessages("first-anchor-row").results.first
        )
        let secondHit = try #require(
            await history.searchLocalMessages("second-anchor-row").results.first
        )
        guard case .archiveRecord(_, let firstSequence, _) = firstHit.target,
              case .archiveRecord(_, let secondSequence, _) = secondHit.target
        else {
            Issue.record("expected exact archive targets")
            return
        }
        #expect(firstSequence != secondSequence)

        let first = anchoredReminder(
            anchor: ArchiveEvidenceAnchor(importID: importID, sequence: firstSequence),
            text: "first-anchor-row", evidenceTimestamp: 1_700_001_200,
            savedAt: 1_800_000_000
        )
        let second = anchoredReminder(
            anchor: ArchiveEvidenceAnchor(importID: importID, sequence: secondSequence),
            text: "second-anchor-row", evidenceTimestamp: 1_700_001_260,
            savedAt: 1_800_000_100
        )
        let model = try await modelWithSavedFollowUps([first, second], history: history)

        await model.openSavedFollowUpEvidence(first.id)
        #expect(model.contextRevealRequest?.anchor
                == .archiveRecord(importID: importID, sequence: firstSequence))
        await model.consumeContextReveal(generation: model.contextRevealRequest!.generation)

        // A different follow-up reveals its own row, not the previous one.
        await model.openSavedFollowUpEvidence(second.id)
        #expect(model.contextRevealRequest?.anchor
                == .archiveRecord(importID: importID, sequence: secondSequence))
    }

    @Test @MainActor
    func savedFollowUpRevealReportsMissingEvidenceHonestly() async throws {
        let history = makeTestMessageHistory()
        await history.setEnabled(true)
        let transcript = try WeChatNativeTranscriptParser.parse(
            "·林晓\n2026年9月7日 20:35\nexpiring-anchor-row\n\n",
            timeZone: TimeZone(identifier: "Asia/Shanghai")!
        )
        let imported = try await history.persistArchiveEvidence(
            transcript: transcript,
            conversationKey: ArchiveConversationKey("b8-expiring"),
            importedAt: Date()
        )
        guard case .inserted(let importID, _) = imported else {
            Issue.record("expected an archive import")
            return
        }
        let hit = try #require(
            await history.searchLocalMessages("expiring-anchor-row").results.first
        )
        guard case .archiveRecord(let hitImport, let sequence, _) = hit.target else {
            Issue.record("expected an exact archive target")
            return
        }
        let reminder = anchoredReminder(
            anchor: ArchiveEvidenceAnchor(importID: hitImport, sequence: sequence),
            text: "expiring-anchor-row"
        )
        let model = try await modelWithSavedFollowUps([reminder], history: history)

        // The whole import is gone: the context is gone, and no row is shown.
        await history.deleteAllHistory()
        await model.openSavedFollowUpEvidence(reminder.id)
        #expect(model.archiveContextUnavailable)
        #expect(model.contextRevealRequest == nil)
        #expect(model.selectedArchiveRecords.isEmpty)
        #expect(!model.searchHitUnavailable)

        // The import exists but the anchored row does not: a sequence that was
        // never written. This is the hit-unavailable state with the context
        // still present, and it must not substitute the row that is there.
        let survivor = try WeChatNativeTranscriptParser.parse(
            "·林晓\n2026年9月7日 20:35\ndifferent-row-only\n\n",
            timeZone: TimeZone(identifier: "Asia/Shanghai")!
        )
        _ = try await history.persistArchiveEvidence(
            transcript: survivor,
            conversationKey: ArchiveConversationKey("b8-expiring"),
            importedAt: Date()
        )
        let survivorHit = try #require(
            await history.searchLocalMessages("different-row-only").results.first
        )
        guard case .archiveRecord(let survivorImport, _, _) = survivorHit.target else {
            Issue.record("expected an exact archive target")
            return
        }
        let stale = anchoredReminder(
            anchor: ArchiveEvidenceAnchor(importID: survivorImport, sequence: sequence + 99),
            text: "expiring-anchor-row"
        )
        let model3 = try await modelWithSavedFollowUps([stale], history: history)
        await model3.openSavedFollowUpEvidence(stale.id)
        #expect(model3.selectedArchiveImportID == survivorImport)
        #expect(model3.searchHitUnavailable)
        #expect(model3.contextRevealRequest == nil)
        #expect(!model3.selectedArchiveRecords.contains(where: { $0.sequence == sequence + 99 }))
        #expect(!model3.archiveContextUnavailable)
    }

    @Test @MainActor
    func anAnchorlessSavedFollowUpNeverRevealsAnything() async throws {
        let history = makeTestMessageHistory()
        await history.setEnabled(true)
        let transport = OverviewTransport()
        let scan = FakeFollowUpRunner(outcomes: [])
        let anchorless = SavedFollowUp(
            id: UUID(),
            source: .archive,
            conversationLabel: "Imported archive export",
            sender: "林晓",
            evidenceTimestamp: Date(timeIntervalSince1970: 1_700_001_200),
            evidenceTimestampKind: "source_created",
            scanWindowStart: Date(timeIntervalSince1970: 1_700_000_000),
            scanWindowEnd: Date(timeIntervalSince1970: 1_700_003_600),
            coverageStatus: "partial",
            coverageCaveats: ["archive:partial"],
            savedAt: Date(timeIntervalSince1970: 1_800_000_000),
            text: "legacy row",
            reasons: ["explicit_request"],
            status: .pending
        )
        #expect(anchorless.archiveEvidence == nil)
        let model = try await modelWithSavedFollowUps(
            [anchorless], history: history, scan: scan, transport: transport
        )

        // Nothing to reveal means nothing happens, including navigation.
        await model.openSavedFollowUpEvidence(anchorless.id)
        #expect(model.selectedDestination == .overview)
        #expect(model.contextRevealRequest == nil)
        #expect(model.contextNavigationTarget == nil)
        #expect(!model.archiveContextUnavailable)
        #expect(!model.searchHitUnavailable)
        #expect(model.selectedArchiveRecords.isEmpty)
        #expect(scan.calls.isEmpty)
        #expect(await transport.calls == 0)
    }

    @Test @MainActor
    func aNonArchiveSavedFollowUpCannotRevealArchiveEvidence() async throws {
        // A record that pairs a Visual source with an Archive anchor is not
        // something this app writes, and a tampered file must not be able to
        // turn it into an Archive reveal.
        let history = makeTestMessageHistory()
        await history.setEnabled(true)
        let transcript = try WeChatNativeTranscriptParser.parse(
            "·林晓\n2026年9月7日 20:35\nvisual-source-row\n\n",
            timeZone: TimeZone(identifier: "Asia/Shanghai")!
        )
        let imported = try await history.persistArchiveEvidence(
            transcript: transcript,
            conversationKey: ArchiveConversationKey("b8-visual-source"),
            importedAt: Date()
        )
        guard case .inserted(let importID, _) = imported else {
            Issue.record("expected an archive import")
            return
        }
        let hit = try #require(
            await history.searchLocalMessages("visual-source-row").results.first
        )
        guard case .archiveRecord(let hitImport, let sequence, _) = hit.target else {
            Issue.record("expected an exact archive target")
            return
        }
        let tampered = anchoredReminder(
            anchor: ArchiveEvidenceAnchor(importID: hitImport, sequence: sequence),
            text: "visual-source-row",
            source: .visual
        )
        let model = try await modelWithSavedFollowUps([tampered], history: history)
        // Whatever the import view happens to be showing, the reveal itself
        // must do nothing: no navigation, no reveal request, no archive
        // context loss, and no hit-unavailable state.

        await model.openSavedFollowUpEvidence(tampered.id)
        #expect(model.contextRevealRequest == nil)
        #expect(model.contextNavigationTarget == nil)
        #expect(!model.archiveContextUnavailable)
        #expect(!model.searchHitUnavailable)
    }

    @Test
    func exactDuplicateEvidenceWithDifferentAnchorsStaysIndependentlySaveable() async throws {
        // Two records can agree on every displayed field and still name two
        // different exact rows. Deduplication must not collapse them.
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("ReminderStoreAnchors-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let store = LocalReminderStore(url: root.appendingPathComponent("reminders.json"))
        let first = anchoredReminder(
            anchor: ArchiveEvidenceAnchor(importID: 1, sequence: 0), text: "same-row"
        )
        let second = anchoredReminder(
            anchor: ArchiveEvidenceAnchor(importID: 1, sequence: 1), text: "same-row"
        )
        #expect(!first.isSameEvidence(as: second))

        var loaded = try await store.add(first)
        #expect(loaded.count == 1)
        loaded = try await store.add(second)
        #expect(loaded.count == 2)
        #expect(Set(loaded.compactMap(\.archiveEvidence?.sequence)) == [0, 1])
    }

    @Test
    func localReminderStoreKeepsAVersionOneFileReadableAsAnchorless() async throws {
        // The compatibility contract. A v1 file predates the evidence anchor,
        // so its records load as anchorless and stay fully manageable. They
        // are never guessed at, and reading one rewrites nothing.
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("ReminderStoreV1-\(UUID().uuidString)", isDirectory: true)
        let url = root.appendingPathComponent("reminders.json")
        defer { try? FileManager.default.removeItem(at: root) }
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)

        // Dates are seconds since the 2001 reference date, which is what the
        // store's default encoder has always written -- a real version 1 file
        // is in exactly this form.
        let legacy = """
        {"version":1,"reminders":[{"id":"11111111-1111-1111-1111-111111111111",
        "source":"archive","conversationLabel":"Imported archive export","sender":"A",
        "evidenceTimestamp":721694000,"evidenceTimestampKind":"source_created",
        "scanWindowStart":720692800,"scanWindowEnd":721696800,
        "coverageStatus":"partial","coverageCaveats":["archive:partial"],
        "savedAt":821692800,"text":"麻烦明天确认一下报价",
        "reasons":["explicit_request"],"status":"pending"}]}
        """
        let original = Data(legacy.utf8)
        try original.write(to: url)

        let store = LocalReminderStore(url: url)
        let loaded = try await store.load()
        let record = try #require(loaded.first)
        #expect(loaded.count == 1)
        #expect(record.id == UUID(uuidString: "11111111-1111-1111-1111-111111111111"))
        #expect(record.source == .archive)
        #expect(record.conversationLabel == "Imported archive export")
        #expect(record.text == "麻烦明天确认一下报价")
        #expect(abs(record.evidenceTimestamp.timeIntervalSince1970 - 1_700_001_200) < 1)
        #expect(abs(record.savedAt.timeIntervalSince1970 - 1_800_000_000) < 1)
        #expect(record.status == .pending)
        #expect(record.archiveEvidence == nil)
        // Reading is not a migration: the file on disk is untouched.
        #expect(try Data(contentsOf: url) == original)

        // Lifecycle still works on an anchorless legacy record.
        let completed = try await store.setStatus(id: record.id, status: .completed)
        #expect(completed.first?.status == .completed)
        #expect(try await store.setStatus(id: record.id, status: .pending).first?.status == .pending)
        #expect(try await store.delete(id: record.id).isEmpty)
    }

    @Test
    func localReminderStoreRoundTripsAnchoredAndAnchorlessRecordsInTheCurrentVersion()
    async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("ReminderStoreV2-\(UUID().uuidString)", isDirectory: true)
        let url = root.appendingPathComponent("reminders.json")
        defer { try? FileManager.default.removeItem(at: root) }

        let store = LocalReminderStore(url: url)
        let anchored = SavedFollowUp(
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
            reasons: ["explicit_request"],
            status: .pending,
            archiveEvidence: ArchiveEvidenceAnchor(importID: 1, sequence: 0)
        )
        let anchorless = SavedFollowUp(
            id: UUID(),
            source: .visual,
            conversationLabel: "Captured conversation",
            sender: "B",
            evidenceTimestamp: Date(timeIntervalSince1970: 1_700_001_400),
            evidenceTimestampKind: "first_observed",
            scanWindowStart: Date(timeIntervalSince1970: 1_700_000_000),
            scanWindowEnd: Date(timeIntervalSince1970: 1_700_003_600),
            coverageStatus: "partial",
            coverageCaveats: [],
            savedAt: Date(timeIntervalSince1970: 1_800_000_100),
            text: "我会明天跟进这个事情",
            reasons: ["explicit_commitment"],
            status: .completed
        )

        _ = try await store.add(anchored)
        _ = try await store.add(anchorless)
        #expect(try await store.load() == [anchorless, anchored])
        #expect(try await store.load().first?.archiveEvidence == nil)
        #expect(try await store.load().last?.archiveEvidence
                == ArchiveEvidenceAnchor(importID: 1, sequence: 0))
    }

    @Test
    func savedFollowUpCarriesTheCandidateAnchorIntoTheStore() async throws {
        let anchor = ArchiveEvidenceAnchor(importID: 12, sequence: 34)
        let candidate = FollowUpCandidate(
            id: 0,
            conversationIndex: 0,
            source: .archive,
            timestamp: Date(timeIntervalSince1970: 1_700_001_200),
            timestampKind: "source_created",
            sender: "A",
            text: "麻烦明天确认一下报价",
            textTruncated: false,
            reasons: ["explicit_request"],
            archiveEvidence: anchor
        )
        let runner = FakeFollowUpRunner(outcomes: [.ready(FollowUpCandidateSnapshot(
            source: .archive,
            start: Date(timeIntervalSince1970: 1_700_000_000),
            end: Date(timeIntervalSince1970: 1_700_003_600),
            scannedMessages: 4,
            returnedCandidates: 1,
            textTruncatedCount: 0,
            truncated: false,
            coverage: FollowUpCoverage(
                status: "partial", trustworthyEmpty: false, caveats: ["archive:partial"]
            ),
            freshness: nil,
            conversations: [FollowUpConversation(id: 0, label: "Imported archive export")],
            candidates: [candidate]
        ))])
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("ReminderStoreSave-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let reminderStore = LocalReminderStore(
            url: root.appendingPathComponent("reminders.json")
        )
        let model = await MainActor.run { AppModel(
            messageHistory: makeTestMessageHistory(), shareInbox: nil,
            consentDefaults: makeDefaults(), followUpCandidates: runner,
            reminderStore: reminderStore
        ) }
        await model.setAllowsLocalPersistence(true)
        await MainActor.run { model.setFollowUpSource(.archive) }
        await model.scanFollowUps(now: Date(timeIntervalSince1970: 1_800_000_000))
        await model.saveFollowUpCandidate(0)

        let saved = try #require(await MainActor.run { model.savedFollowUps.first })
        #expect(saved.archiveEvidence == anchor)
        #expect(saved.source == .archive)
        // The anchor is persisted, not just held in the model.
        #expect(try await reminderStore.load().first?.archiveEvidence == anchor)
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

/// Preparation UX: one consumer verb ("prepare conversations") and one
/// remembered reason for being in Settings. The return is navigation only --
/// nothing here may run preparation, a summary, or a scan.
struct PreparationReturnTests {
    @Test @MainActor
    func aFreshModelHasNoPreparationOrigin() {
        let model = AppModel(messageHistory: makeTestMessageHistory(), consentDefaults: makeDefaults())
        #expect(model.preparationOrigin == nil)
    }

    @Test @MainActor
    func summaryHandoffRecordsSummaryOrigin() async {
        let sync = FakeMemorySyncRunner(outcomes: [], freshness: freshness(source: .archive))
        let model = AppModel(messageHistory: makeTestMessageHistory(), consentDefaults: makeDefaults(),
                             memorySync: sync, dailySummary: FakeDailySummaryRunner(outcomes: []))
        await model.setAllowsLocalPersistence(true)
        model.setDailySummarySource(.archive)
        await model.openDailySummaryMemorySettings()
        #expect(model.selectedDestination == .settings)
        #expect(model.preparationOrigin == .dailySummary)
        #expect(sync.syncCalls.isEmpty)
    }

    @Test @MainActor
    func followUpHandoffRecordsFollowUpOrigin() async {
        let sync = FakeMemorySyncRunner(outcomes: [], freshness: freshness(source: .archive))
        let model = AppModel(messageHistory: makeTestMessageHistory(), consentDefaults: makeDefaults(),
                             memorySync: sync, followUpCandidates: FakeFollowUpRunner(outcomes: []))
        await model.setAllowsLocalPersistence(true)
        model.setFollowUpSource(.archive)
        model.selectedDestination = .reminders
        await model.openFollowUpMemorySettings()
        #expect(model.selectedDestination == .settings)
        #expect(model.preparationOrigin == .followUps)
        #expect(sync.syncCalls.isEmpty)
    }

    @Test @MainActor
    func anIncompatibleHandoffRecordsNeitherRequestNorOrigin() async {
        let sync = HeldMemorySyncRunner()
        let model = AppModel(messageHistory: makeTestMessageHistory(), consentDefaults: makeDefaults(),
                             memorySync: sync, dailySummary: FakeDailySummaryRunner(outcomes: []))
        await model.setAllowsLocalPersistence(true)
        model.setDailySummarySource(.archive)
        model.selectedDestination = .dailySummary
        let operation = Task { await model.syncMemoryNow() }
        await sync.waitUntilStarted()
        await model.openDailySummaryMemorySettings()
        #expect(model.selectedDestination == .dailySummary)
        #expect(model.preparationOrigin == nil)
        #expect(!model.consumeMemorySettingsRequest())
        await sync.finish()
        await operation.value
    }

    @Test @MainActor
    func consumingTheScrollRequestKeepsTheOrigin() async {
        let model = AppModel(messageHistory: makeTestMessageHistory(), consentDefaults: makeDefaults(),
                             memorySync: FakeMemorySyncRunner(outcomes: [], freshness: freshness(source: .archive)),
                             dailySummary: FakeDailySummaryRunner(outcomes: []))
        await model.setAllowsLocalPersistence(true)
        model.setDailySummarySource(.archive)
        await model.openDailySummaryMemorySettings()
        #expect(model.consumeMemorySettingsRequest())
        #expect(!model.consumeMemorySettingsRequest())
        #expect(model.preparationOrigin == .dailySummary)
    }

    @Test(arguments: [PreparationOrigin.dailySummary, .followUps]) @MainActor
    func explicitReturnGoesBackToTheOriginatingTaskAndRunsNothing(origin: PreparationOrigin) async {
        let sync = FakeMemorySyncRunner(outcomes: [], freshness: freshness(source: .archive))
        let summary = FakeDailySummaryRunner(outcomes: [])
        let scan = FakeFollowUpRunner(outcomes: [])
        let store = RecordingReminderStore()
        let model = AppModel(messageHistory: makeTestMessageHistory(), consentDefaults: makeDefaults(),
                             memorySync: sync, dailySummary: summary, followUpCandidates: scan,
                             reminderStore: store)
        await model.setAllowsLocalPersistence(true)
        model.setDailySummarySource(.archive)
        model.setFollowUpSource(.archive)
        model.openStorageSettings(from: origin)
        #expect(model.selectedDestination == .settings)
        #expect(model.preparationOrigin == origin)
        let storeCallsBeforeReturn = await store.calls
        model.returnToPreparationOrigin()
        #expect(model.selectedDestination == (origin == .dailySummary ? .dailySummary : .reminders))
        #expect(model.preparationOrigin == nil)
        #expect(sync.syncCalls.isEmpty && sync.freshnessCalls.isEmpty)
        #expect(summary.calls.isEmpty)
        #expect(scan.calls.isEmpty)
        #expect(await store.calls == storeCallsBeforeReturn)
    }

    @Test @MainActor
    func leavingSettingsAnotherWayClearsAStaleOrigin() async {
        let model = AppModel(messageHistory: makeTestMessageHistory(), consentDefaults: makeDefaults())
        model.openStorageSettings(from: .dailySummary)
        #expect(model.preparationOrigin == .dailySummary)
        model.selectedDestination = .chats
        #expect(model.preparationOrigin == nil)
    }

    @Test @MainActor
    func aLaterOrdinarySettingsVisitHasNoStaleReturn() async {
        let model = AppModel(messageHistory: makeTestMessageHistory(), consentDefaults: makeDefaults())
        model.openStorageSettings(from: .followUps)
        // The real path: leave Settings by an unrelated destination, then come
        // back on an ordinary visit. Nothing about that may restore the origin.
        model.selectedDestination = .chats
        model.selectedDestination = .settings
        #expect(model.preparationOrigin == nil)
    }

    @Test(arguments: [PreparationOrigin.dailySummary, .followUps]) @MainActor
    func storageOffHandoffRecordsOriginWithoutAskingForThePreparationSection(origin: PreparationOrigin) async {
        let sync = FakeMemorySyncRunner(outcomes: [])
        let summary = FakeDailySummaryRunner(outcomes: [])
        let scan = FakeFollowUpRunner(outcomes: [])
        let store = RecordingReminderStore()
        let model = AppModel(messageHistory: makeTestMessageHistory(), consentDefaults: makeDefaults(),
                             memorySync: sync, dailySummary: summary, followUpCandidates: scan,
                             reminderStore: store)
        model.openStorageSettings(from: origin)
        #expect(model.selectedDestination == .settings)
        #expect(model.preparationOrigin == origin)
        // Storage is off, so the user needs the top-level control, not the
        // preparation section further down.
        #expect(!model.hasMemorySettingsRequest)
        #expect(sync.syncCalls.isEmpty && sync.freshnessCalls.isEmpty)
        #expect(summary.calls.isEmpty)
        #expect(scan.calls.isEmpty)
        #expect(await store.calls.isEmpty)
    }

    @Test @MainActor
    func enablingStoragePreparesNothingOnItsOwn() async {
        let sync = FakeMemorySyncRunner(outcomes: [])
        let summary = FakeDailySummaryRunner(outcomes: [])
        let scan = FakeFollowUpRunner(outcomes: [])
        let model = AppModel(messageHistory: makeTestMessageHistory(), consentDefaults: makeDefaults(),
                             memorySync: sync, dailySummary: summary, followUpCandidates: scan,
                             reminderStore: RecordingReminderStore())
        model.openStorageSettings(from: .dailySummary)
        await model.setAllowsLocalPersistence(true)
        #expect(sync.syncCalls.isEmpty && sync.freshnessCalls.isEmpty)
        #expect(summary.calls.isEmpty)
        #expect(scan.calls.isEmpty)
        #expect(model.dailySummaryPhase == .idle)
        #expect(model.followUpPhase == .idle)
        // The reason for being here survives, so the return action still works.
        #expect(model.preparationOrigin == .dailySummary)
    }

    @Test
    func consumerPreparationSurfacesNeverRenderInternalState() throws {
        let file = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .deletingLastPathComponent().appendingPathComponent("WeChatCompanion/ContentView.swift")
        let source = try String(contentsOf: file, encoding: .utf8)
        func view(_ start: String, _ end: String) throws -> String {
            let from = try #require(source.range(of: start))
            let to = try #require(source.range(of: end))
            return String(source[from.upperBound..<to.lowerBound])
        }
        let summary = try view("private struct DailySummaryView: View {", "private struct DailySummarySnapshotView:")
        let followUps = try view("private struct FollowUpsView: View {", "private struct SavedFollowUpRow:")
        for surface in [summary, followUps] {
            // 'failure.message' carries raw worker state; the safe consumer
            // mapping is the only thing these surfaces may render.
            #expect(!surface.contains("failure.message"))
            #expect(!surface.contains("syncMemoryNow()"))
        }
        // Worker caveats reach these surfaces as `archive:partial` tokens.
        // They must go through the consumer mapping, never render raw.
        // Caveat rows sit in the snapshot/row views, not the two task views,
        // so assert on the mapping itself plus the absence of a raw render.
        #expect(!source.contains("Label(caveat, systemImage:"))
        #expect(!source.contains("Text(caveat).font(.callout)"))

        // Exactly one storage-off route. The model guard is what actually
        // refuses the handoff (covered behaviourally above), so this only
        // pins that the view has no second, unguarded copy of the action and
        // that the storage-off state offers the top-level control.
        #expect(summary.components(separatedBy: "openDailySummaryMemorySettings()").count == 2)
        #expect(summary.contains("model.openStorageSettings(from: .dailySummary)"))
    }

    @Test
    func structuredCaveatsUseConsumerVocabularyAndNeverRevealInternalTokens() {
        // Public coverage states keep their existing consumer wording.
        #expect(FollowUpCoveragePresentation.caveat("archive:partial") == "Imported WeChat archives: Partial")
        #expect(FollowUpCoveragePresentation.caveat("archive:unavailable")
            == "Imported WeChat archives: Unavailable")
        #expect(FollowUpCoveragePresentation.caveat("visual:not_observed")
            == "Visual capture store: Not observed")

        // The worker spells these `observed_*` on the wire (see
        // bridge/message_source.py). A real caveat must not degrade into the
        // neutral fallback just because the token is the longer form.
        #expect(FollowUpCoveragePresentation.caveat("archive:observed_partial")
            == "Imported WeChat archives: Partial")
        #expect(FollowUpCoveragePresentation.caveat("archive:observed_complete")
            == "Imported WeChat archives: Complete")
        #expect(FollowUpCoveragePresentation.caveat("visual:observed_partial")
            == "Visual capture store: Partial")

        // Internal reason tokens fail closed and are never prettified into copy.
        for token in ["memory_store_missing", "archive_store_missing", "schema_unsupported"] {
            let rendered = FollowUpCoveragePresentation.caveat("archive:\(token)")
            #expect(!rendered.contains(token))
            #expect(!rendered.contains(token.replacingOccurrences(of: "_", with: " ").capitalized))
            #expect(rendered == "Imported WeChat archives: Coverage could not be fully confirmed.")
        }

        // An unknown underscore token from a source we do not know stays neutral.
        let unknownSource = FollowUpCoveragePresentation.caveat("some_new_reader:internal_thing")
        #expect(!unknownSource.contains("internal_thing"))
        #expect(!unknownSource.contains("Internal Thing"))

        // Free-text caveats stay byte-for-byte; they are already consumer prose.
        for prose in ["Some days are unindexed.", "Archive import is still running.",
                      "Note: older imports are not indexed."] {
            #expect(FollowUpCoveragePresentation.caveat(prose) == prose)
        }
    }

    @Test
    func oneCoverageLabelOwnsAllFourMeanings() throws {
        for (status, expected) in [
            ("complete", "Complete"),
            ("partial", "Partial"),
            ("unavailable", "Unavailable"),
            ("not_observed", "Not observed"),
            ("observed_complete", "Complete"),
            ("observed_partial", "Partial"),
        ] {
            #expect(FollowUpCoveragePresentation.label(for: status) == expected)
        }
        // Unknown stays fail-closed: readable, and never "Complete".
        let unknown = FollowUpCoveragePresentation.label(for: "schema_unsupported")
        #expect(unknown == "Schema Unsupported")
        #expect(unknown != "Complete")

        // Exactly one implementation exists: the shared owner, which is this
        // file's own. No other file may re-declare the four labels.
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("WeChatCompanion")
        for name in ["ContentView.swift", "OnDeviceAnswerRunner.swift"] {
            let text = try String(contentsOf: root.appendingPathComponent(name), encoding: .utf8)
            #expect(!text.contains("case \"partial\": \"Partial\""), "\(name) re-declares a coverage label")
        }
    }

    @Test
    func coverageMeaningIsOneDecisionForBothSpellings() {
        // The real wire vocabulary (bridge/message_source.py) and the legacy
        // short forms must read identically to the consumer.
        for status in ["complete", "observed_complete"] {
            #expect(FollowUpCoveragePresentation.isComplete(status))
            #expect(FollowUpCoveragePresentation.message(for: status) == nil)
        }
        for status in ["partial", "observed_partial"] {
            #expect(!FollowUpCoveragePresentation.isComplete(status))
            #expect(FollowUpCoveragePresentation.message(for: status)
                == "Based on part of this conversation.")
        }
        #expect(FollowUpCoveragePresentation.message(for: "unavailable")
            == "Conversation history is unavailable.")
        #expect(FollowUpCoveragePresentation.message(for: "not_observed")
            == "Conversation history has not been checked.")

        // Unknown is never complete, and says so.
        for unknown in ["schema_unsupported", "", "Observed_Complete"] {
            #expect(!FollowUpCoveragePresentation.isComplete(unknown))
            #expect(FollowUpCoveragePresentation.message(for: unknown)
                == "Full conversation history could not be confirmed.")
        }
    }

    @Test
    func followUpSnapshotWarnsFromOneCoverageMeaning() {
        // The view's two decisions - is coverage complete, and what note do we
        // show - must both come from the shared helper, so a real
        // observed_complete result cannot be presented as incomplete.
        func note(status: String, truncated: Bool) -> String? {
            guard !FollowUpCoveragePresentation.isComplete(status) || truncated else { return nil }
            return FollowUpCoveragePresentation.isComplete(status)
                ? "Some messages or suggestions may be missing from this search."
                : FollowUpCoveragePresentation.message(for: status)
        }
        // Complete coverage, nothing truncated: no warning at all.
        #expect(note(status: "observed_complete", truncated: false) == nil)
        #expect(note(status: "complete", truncated: false) == nil)
        // Complete coverage but truncated: only the truncation note.
        #expect(note(status: "observed_complete", truncated: true)
            == "Some messages or suggestions may be missing from this search.")
        // Partial coverage: the partial-history note, never the truncation one.
        #expect(note(status: "observed_partial", truncated: false)
            == "Based on part of this conversation.")
        #expect(note(status: "partial", truncated: false)
            == "Based on part of this conversation.")
        // Unknown stays fail-closed.
        #expect(note(status: "schema_unsupported", truncated: false)
            == "Full conversation history could not be confirmed.")
    }

    @Test
    func savedFollowUpsCarryTheSameCoverageMeaning() {
        // A saved follow-up records the worker's status verbatim, so the row
        // note has to come from the same shared helper.
        let model = SavedFollowUp(
            id: UUID(), source: .archive, conversationLabel: "Weekend plans",
            sender: "Sam",
            evidenceTimestamp: Date(timeIntervalSince1970: 1_791_151_200),
            evidenceTimestampKind: "archive_sent",
            scanWindowStart: Date(timeIntervalSince1970: 1_791_151_200),
            scanWindowEnd: Date(timeIntervalSince1970: 1_791_151_560),
            coverageStatus: "observed_complete", coverageCaveats: [],
            savedAt: Date(timeIntervalSince1970: 1_791_151_560),
            text: "Reply to Sam", reasons: [], status: .pending
        )
        // No false incompleteness note for a genuinely complete read.
        #expect(FollowUpCoveragePresentation.message(for: model.coverageStatus) == nil)
        #expect(FollowUpCoveragePresentation.label(for: model.coverageStatus) == "Complete")
        // And a partial one still warns.
        let partial = SavedFollowUp(
            id: model.id, source: model.source, conversationLabel: model.conversationLabel,
            sender: model.sender, evidenceTimestamp: model.evidenceTimestamp,
            evidenceTimestampKind: model.evidenceTimestampKind,
            scanWindowStart: model.scanWindowStart, scanWindowEnd: model.scanWindowEnd,
            coverageStatus: "observed_partial", coverageCaveats: ["archive:observed_partial"],
            savedAt: model.savedAt, text: model.text, reasons: model.reasons, status: model.status
        )
        #expect(FollowUpCoveragePresentation.message(for: partial.coverageStatus) != nil)
        #expect(FollowUpCoveragePresentation.label(for: partial.coverageStatus) == "Partial")
        // The stored caveat is a real wire token, and it reads as Partial.
        #expect(partial.coverageCaveats.map(FollowUpCoveragePresentation.caveat(_:))
            == ["Imported WeChat archives: Partial"])
    }

    @Test
    func noConsumerCodeComparesCoverageAgainstALiteralComplete() throws {
        // One meaning, one owner. A direct literal comparison in a view is how a
        // real observed_complete result became a false incompleteness warning.
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("WeChatCompanion")
        for name in ["ContentView.swift", "OnDeviceAnswerRunner.swift"] {
            let text = try String(contentsOf: root.appendingPathComponent(name), encoding: .utf8)
            #expect(!text.contains("\"complete\""), "\(name) compares coverage against the literal complete")
        }
    }
}
