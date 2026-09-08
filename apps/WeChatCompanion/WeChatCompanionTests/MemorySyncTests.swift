import Foundation
import Testing
@testable import WeChatCompanion

/// App-owned Memory sync (M2.2c): consent-gated, foreground, explicit; the
/// runner is a fake so no memory layer, Python, or file is touched.
private final class FakeMemorySyncRunner: MemorySyncRunning, @unchecked Sendable {
    var outcomes: [MemorySyncOutcome]
    var freshnessToReport: MemoryFreshnessSummary?
    private(set) var syncCalls: [MemorySource] = []
    private(set) var freshnessCalls = 0

    init(outcomes: [MemorySyncOutcome], freshness: MemoryFreshnessSummary? = nil) {
        self.outcomes = outcomes
        self.freshnessToReport = freshness
    }

    func sync(source: MemorySource) async -> MemorySyncOutcome {
        syncCalls.append(source)
        return outcomes.isEmpty ? .failed(.runnerUnavailable) : outcomes.removeFirst()
    }

    func freshness(source: MemorySource) async -> MemoryFreshnessSummary? {
        freshnessCalls += 1
        return freshnessToReport
    }
}

private func makeDefaults() -> UserDefaults {
    let suite = "WeChatCompanionTests-\(UUID().uuidString)"
    let defaults = UserDefaults(suiteName: suite)!
    defaults.removePersistentDomain(forName: suite)
    return defaults
}

private let counts = MemorySyncCounts(conversationsSeen: 2, messagesSeen: 13, messagesInserted: 3, messagesUpdated: 10)

private func freshness(state: String = "succeeded", failure: String? = nil, coverage: String = "complete") -> MemoryFreshnessSummary {
    MemoryFreshnessSummary(
        source: .visual,
        lastSuccessfulSync: Date(timeIntervalSince1970: 1_700_000_000),
        observedThrough: Date(timeIntervalSince1970: 1_699_999_880),   // 09:58
        completeThrough: Date(timeIntervalSince1970: 1_699_999_880),
        latestMessageAt: Date(timeIntervalSince1970: 1_699_998_920),   // 09:42
        lastRunState: state, lastRunFailure: failure, coverageSummary: coverage
    )
}

struct MemorySyncTests {
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
        #expect(runner.freshnessCalls == 1)
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
        #expect(runner.freshnessCalls == 0)
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
