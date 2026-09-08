import CoreGraphics
import Foundation
import Testing
@testable import WeChatCompanion

/// Local persistence consent, deletion and retention.
///
/// These tests need real files: WAL/SHM lifecycle and "off keeps history"
/// cannot be observed in an in-memory database. Every test therefore uses a
/// unique throwaway directory under the system temporary directory and removes
/// it afterwards. All fixture content remains synthetic.
private final class TemporaryDatabase {
    let directory: URL
    var url: URL { directory.appendingPathComponent("messages.sqlite") }

    init() throws {
        directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("WeChatCompanionTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    func exists(_ suffix: String = "") -> Bool {
        FileManager.default.fileExists(atPath: url.path + suffix)
    }

    deinit { try? FileManager.default.removeItem(at: directory) }
}

private func message(_ text: String) -> ExtractedVisibleMessage {
    ExtractedVisibleMessage(ownership: .other, text: text, kind: .text, confidence: 0.9)
}

private func frame(
    chat: String,
    _ texts: [String],
    // Defaults to now, so the default 30-day retention never expires a
    // fixture out from under a test that is not about retention.
    capturedAt: Date = Date()
) -> ExtractedConversationFrame {
    ExtractedConversationFrame(
        capturedAt: capturedAt,
        chat: ExtractedChatIdentity(title: chat, confidence: 0.9),
        messages: texts.map(message)
    )
}

private func storedTexts(_ history: LocalMessageHistory, _ title: String) async throws -> [String?] {
    guard let store = await history.openStore(),
          let conversation = try await store.conversation(titled: title) else { return [] }
    return try await store.messages(inConversation: conversation.id).map(\.text)
}

/// UserDefaults isolated per test, so no test can read or write the real
/// preferences domain.
private func makeDefaults() -> UserDefaults {
    let suite = "WeChatCompanionTests-\(UUID().uuidString)"
    let defaults = UserDefaults(suiteName: suite)!
    defaults.removePersistentDomain(forName: suite)
    return defaults
}

struct LocalPersistenceConsentTests {
    // MARK: - Default off

    @Test @MainActor
    func localPersistenceIsOffOnAFreshInstall() {
        let model = AppModel(messageHistory: makeTestMessageHistory(), consentDefaults: makeDefaults())
        #expect(model.allowsLocalPersistence == false)
        // Independent of the remote consent, which is also off by default.
        #expect(model.allowsRemoteProcessing == false)
    }

    @Test @MainActor
    func retentionDefaultsToThirtyDays() {
        let model = AppModel(messageHistory: makeTestMessageHistory(), consentDefaults: makeDefaults())
        #expect(model.retentionPolicy == .thirtyDays)
    }

    @Test
    func aDisabledHistoryOpensNoStoreAndCreatesNoFile() async throws {
        let database = try TemporaryDatabase()
        let history = LocalMessageHistory(url: database.url)

        #expect(await history.ingestor() == nil)
        #expect(await history.hasOpenStore == false)
        #expect(database.exists() == false)
    }

    // MARK: - Off writes nothing

    @Test
    func withConsentOffExtractionStillRunsButNothingIsWritten() async throws {
        let database = try TemporaryDatabase()
        let history = LocalMessageHistory(url: database.url)
        let coordinator = ExtractionCoordinator(
            extractor: FixedExtractor(result: frame(chat: "Chat A", ["m1"]))
        )
        await coordinator.updateConfiguration(
            extractor: FixedExtractor(result: frame(chat: "Chat A", ["m1"])),
            capability: .onDeviceOnly,
            ingestor: await history.ingestor()
        )

        await coordinator.submit(.synthetic(secondsFromNow: 0))
        await coordinator.waitUntilIdle()

        // Extraction succeeded and the result is available in memory...
        #expect(await coordinator.snapshot().extractionsSucceeded == 1)
        #expect(await coordinator.latest() != nil)
        // ...but nothing was persisted, and no database file exists.
        #expect(await coordinator.isPersisting == false)
        #expect(database.exists() == false)
    }

    // MARK: - On writes

    @Test
    func turningConsentOnCreatesTheStoreAndPersists() async throws {
        let database = try TemporaryDatabase()
        let history = LocalMessageHistory(url: database.url)
        await history.setEnabled(true)

        #expect(database.exists())
        let ingestor = try #require(await history.ingestor())
        await ingestor.ingest(frame(chat: "Chat A", ["m1", "m2"]))

        #expect(try await storedTexts(history, "Chat A") == ["m1", "m2"])
    }

    @Test
    func theCoordinatorPersistsOnlyWhileAnIngestorIsAttached() async throws {
        let database = try TemporaryDatabase()
        let history = LocalMessageHistory(url: database.url)
        await history.setEnabled(true)
        let coordinator = ExtractionCoordinator(
            extractor: FixedExtractor(result: frame(chat: "Chat A", ["m1"]))
        )
        await coordinator.updateConfiguration(
            extractor: FixedExtractor(result: frame(chat: "Chat A", ["m1"])),
            capability: .onDeviceOnly,
            ingestor: await history.ingestor()
        )
        #expect(await coordinator.isPersisting)

        await coordinator.submit(.synthetic(secondsFromNow: 0))
        await coordinator.waitUntilIdle()
        #expect(try await storedTexts(history, "Chat A") == ["m1"])
    }

    // MARK: - On to off

    @Test
    func turningConsentOffStopsFutureWritesButKeepsStoredHistory() async throws {
        let database = try TemporaryDatabase()
        let history = LocalMessageHistory(url: database.url)
        await history.setEnabled(true)
        let ingestor = try #require(await history.ingestor())
        await ingestor.ingest(frame(chat: "Chat A", ["m1"]))

        await history.setEnabled(false)
        #expect(await history.ingestor() == nil)
        #expect(await history.hasOpenStore == false)
        // Withdrawing consent is not a delete: the file is still there.
        #expect(database.exists())

        // Turning it back on finds the same history, not an empty database.
        await history.setEnabled(true)
        #expect(try await storedTexts(history, "Chat A") == ["m1"])
    }

    // MARK: - Deletion

    @Test
    func deletingHistoryEmptiesEveryConversation() async throws {
        let database = try TemporaryDatabase()
        let history = LocalMessageHistory(url: database.url)
        await history.setEnabled(true)
        let ingestor = try #require(await history.ingestor())
        await ingestor.ingest(frame(chat: "Chat A", ["m1", "m2"]))
        await ingestor.ingest(
            frame(chat: "Chat B", ["b1"], capturedAt: Date().addingTimeInterval(1))
        )

        await history.deleteAllHistory()

        let store = try #require(await history.openStore())
        #expect(try await store.conversations().isEmpty)
        #expect(try await store.totalMessageCount() == 0)
    }

    @Test
    func deletingHistoryRemovesTheWriteAheadSidecarFilesToo() async throws {
        let database = try TemporaryDatabase()
        let history = LocalMessageHistory(url: database.url)
        await history.setEnabled(true)
        let ingestor = try #require(await history.ingestor())
        await ingestor.ingest(frame(chat: "Chat A", ["m1"]))

        await history.setEnabled(false)
        await history.deleteAllHistory()

        // Deleting only the .sqlite would leave committed rows in the -wal.
        #expect(database.exists() == false)
        #expect(database.exists("-wal") == false)
        #expect(database.exists("-shm") == false)
    }

    @Test
    func deletingHistoryWhileConsentIsOnLeavesAUsableEmptyStore() async throws {
        let database = try TemporaryDatabase()
        let history = LocalMessageHistory(url: database.url)
        await history.setEnabled(true)
        let first = try #require(await history.ingestor())
        await first.ingest(frame(chat: "Chat A", ["m1"]))

        await history.deleteAllHistory()

        let reopened = try #require(await history.ingestor())
        await reopened.ingest(frame(chat: "Chat A", ["m9"]))
        #expect(try await storedTexts(history, "Chat A") == ["m9"])
    }

    @Test @MainActor
    func deletingHistoryLeavesCredentialsConsentAndRetentionAlone() async throws {
        let database = try TemporaryDatabase()
        let defaults = makeDefaults()
        let credentials = InMemoryCredentials()
        try credentials.save("test-key", account: GeminiFrameExtractor.credentialAccount)
        let model = AppModel(
            messageHistory: LocalMessageHistory(url: database.url),
            credentials: credentials,
            consentDefaults: defaults
        )
        await model.setAllowsRemoteProcessing(true)
        await model.setAllowsLocalPersistence(true)
        await model.setRetentionPolicy(.ninetyDays)

        await model.deleteLocalMessageHistory()

        #expect(model.hasProviderCredential)
        #expect(credentials.hasSecret(account: GeminiFrameExtractor.credentialAccount))
        #expect(model.allowsRemoteProcessing)
        #expect(model.allowsLocalPersistence)
        #expect(model.retentionPolicy == .ninetyDays)
        #expect(defaults.bool(forKey: AppModel.remoteConsentKey))
        #expect(defaults.bool(forKey: AppModel.localPersistenceConsentKey))
    }

    @Test @MainActor
    func turningConsentOffDoesNotDeleteHistory() async throws {
        let database = try TemporaryDatabase()
        let history = LocalMessageHistory(url: database.url)
        let model = AppModel(messageHistory: history, consentDefaults: makeDefaults())
        await model.setAllowsLocalPersistence(true)
        let ingestor = try #require(await history.ingestor())
        await ingestor.ingest(frame(chat: "Chat A", ["m1"]))

        await model.setAllowsLocalPersistence(false)

        #expect(database.exists())
        await model.setAllowsLocalPersistence(true)
        #expect(try await storedTexts(history, "Chat A") == ["m1"])
    }

    // MARK: - Retention

    @Test(arguments: [
        (RetentionPolicy.sevenDays, 7.0),
        (RetentionPolicy.thirtyDays, 30.0),
        (RetentionPolicy.ninetyDays, 90.0)
    ])
    func eachPolicyExpiresMessagesOlderThanItsWindow(
        policy: RetentionPolicy, days: Double
    ) async throws {
        let store = try MessageStore(url: nil)
        let ingestor = MessageIngestor(store: store, retention: .untilDeleted)
        let now = Date(timeIntervalSince1970: 1_800_000_000)

        // One message just inside the window, one just outside it.
        await ingestor.ingest(
            frame(chat: "Chat A", ["old"], capturedAt: now.addingTimeInterval(-(days + 1) * 86_400))
        )
        await ingestor.ingest(
            frame(chat: "Chat A", ["recent"], capturedAt: now.addingTimeInterval(-(days - 1) * 86_400))
        )
        #expect(try await store.totalMessageCount() == 2)

        let removed = try await store.applyRetention(policy, now: now)

        #expect(removed == 1)
        let conversation = try #require(try await store.conversation(titled: "Chat A"))
        #expect(try await store.messages(inConversation: conversation.id).map(\.text) == ["recent"])
    }

    @Test
    func untilIDeleteItNeverExpiresAnything() async throws {
        let store = try MessageStore(url: nil)
        let ingestor = MessageIngestor(store: store, retention: .untilDeleted)
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        await ingestor.ingest(
            frame(chat: "Chat A", ["ancient"], capturedAt: now.addingTimeInterval(-3_650 * 86_400))
        )

        let removed = try await store.applyRetention(.untilDeleted, now: now)

        #expect(removed == 0)
        #expect(try await store.totalMessageCount() == 1)
    }

    @Test
    func cleanupUsesFirstObservedAtNotVisibleTime() async throws {
        let store = try MessageStore(url: nil)
        let ingestor = MessageIngestor(store: store, retention: .untilDeleted)
        let now = Date(timeIntervalSince1970: 1_800_000_000)

        // Observed long ago, but its visible time string says "today". The
        // string must not rescue it.
        await ingestor.ingest(
            ExtractedConversationFrame(
                capturedAt: now.addingTimeInterval(-60 * 86_400),
                chat: ExtractedChatIdentity(title: "Chat A", confidence: 0.9),
                messages: [ExtractedVisibleMessage(
                    ownership: .other, visibleTime: "今天 09:00", text: "stale", kind: .text,
                    confidence: 0.9
                )]
            )
        )
        // Observed recently, but its visible time string says a year ago. The
        // string must not condemn it.
        await ingestor.ingest(
            ExtractedConversationFrame(
                capturedAt: now.addingTimeInterval(-1 * 86_400),
                chat: ExtractedChatIdentity(title: "Chat A", confidence: 0.9),
                messages: [ExtractedVisibleMessage(
                    ownership: .other, visibleTime: "2019-01-01", text: "fresh", kind: .text,
                    confidence: 0.9
                )]
            )
        )

        try await store.applyRetention(.thirtyDays, now: now)

        let conversation = try #require(try await store.conversation(titled: "Chat A"))
        #expect(try await store.messages(inConversation: conversation.id).map(\.text) == ["fresh"])
    }

    @Test
    func aConversationLeftWithNoMessagesIsRemoved() async throws {
        let store = try MessageStore(url: nil)
        let ingestor = MessageIngestor(store: store, retention: .untilDeleted)
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        await ingestor.ingest(
            frame(chat: "Chat A", ["old"], capturedAt: now.addingTimeInterval(-60 * 86_400))
        )
        await ingestor.ingest(
            frame(chat: "Chat B", ["new"], capturedAt: now.addingTimeInterval(-1 * 86_400))
        )

        try await store.applyRetention(.thirtyDays, now: now)

        #expect(try await store.conversations().map(\.title) == ["Chat B"])
    }

    @Test
    func tighteningThePolicySweepsImmediately() async throws {
        let store = try MessageStore(url: nil)
        let ingestor = MessageIngestor(store: store, retention: .untilDeleted)
        await ingestor.ingest(
            frame(chat: "Chat A", ["old"], capturedAt: Date().addingTimeInterval(-60 * 86_400))
        )
        #expect(try await store.totalMessageCount() == 1)

        await ingestor.setRetention(.sevenDays)

        #expect(try await store.totalMessageCount() == 0)
        #expect(await ingestor.snapshot().messagesExpired == 1)
    }

    @Test
    func enablingPersistenceSweepsBeforeAnythingNewIsWritten() async throws {
        let database = try TemporaryDatabase()
        let history = LocalMessageHistory(url: database.url, retention: .untilDeleted)
        await history.setEnabled(true)
        let first = try #require(await history.ingestor())
        await first.ingest(
            frame(chat: "Chat A", ["old"], capturedAt: Date().addingTimeInterval(-60 * 86_400))
        )
        await history.setEnabled(false)

        // Reopening under a tighter policy, as a relaunch would.
        let reopened = LocalMessageHistory(url: database.url, retention: .sevenDays)
        await reopened.setEnabled(true)

        let store = try #require(await reopened.openStore())
        #expect(try await store.totalMessageCount() == 0)
    }

    // MARK: - Restart

    @Test @MainActor
    func consentAndRetentionSurviveARestart() async throws {
        let database = try TemporaryDatabase()
        let defaults = makeDefaults()
        let first = AppModel(
            messageHistory: LocalMessageHistory(url: database.url),
            consentDefaults: defaults
        )
        await first.setAllowsLocalPersistence(true)
        await first.setRetentionPolicy(.ninetyDays)

        // A new AppModel over the same preferences is what a relaunch looks like.
        let relaunched = AppModel(
            messageHistory: LocalMessageHistory(url: database.url),
            consentDefaults: defaults
        )
        #expect(relaunched.allowsLocalPersistence)
        #expect(relaunched.retentionPolicy == .ninetyDays)
    }

    @Test @MainActor
    func consentOffSurvivesARestartAndStillOpensNothing() async throws {
        let database = try TemporaryDatabase()
        let defaults = makeDefaults()
        let history = LocalMessageHistory(url: database.url)
        let first = AppModel(messageHistory: history, consentDefaults: defaults)
        await first.setAllowsLocalPersistence(true)
        await first.setAllowsLocalPersistence(false)

        let relaunchHistory = LocalMessageHistory(url: database.url)
        let relaunched = AppModel(messageHistory: relaunchHistory, consentDefaults: defaults)
        #expect(relaunched.allowsLocalPersistence == false)
        #expect(await relaunchHistory.hasOpenStore == false)
    }

    @Test
    func anUnknownStoredRetentionValueFallsBackToTheDefault() {
        #expect(RetentionPolicy.resolved(fromStoredID: nil) == .thirtyDays)
        #expect(RetentionPolicy.resolved(fromStoredID: "nonsense") == .thirtyDays)
        #expect(RetentionPolicy.resolved(fromStoredID: "sevenDays") == .sevenDays)
    }

    // MARK: - Privacy guards

    @Test @MainActor
    func consentAndRetentionPreferencesHoldNoChatContent() async throws {
        let database = try TemporaryDatabase()
        let defaults = makeDefaults()
        let model = AppModel(
            messageHistory: LocalMessageHistory(url: database.url),
            consentDefaults: defaults
        )
        await model.setAllowsLocalPersistence(true)
        await model.setRetentionPolicy(.sevenDays)

        // Only a boolean and a policy ID. No title, no message, no date.
        #expect(defaults.object(forKey: AppModel.localPersistenceConsentKey) as? Bool == true)
        #expect(defaults.string(forKey: AppModel.retentionPolicyKey) == "sevenDays")
    }

    @Test
    func retentionSQLNeverFiltersOnTheDisplayTimeString() throws {
        let source = try String(
            contentsOf: URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .appendingPathComponent("WeChatCompanion/Ingestion/MessageStore.swift"),
            encoding: .utf8
        )
        let retention = try #require(source.range(of: "func applyRetention"))
        let body = String(source[retention.lowerBound...].prefix(1_200))
        #expect(body.contains("first_observed_at <"))
        #expect(!body.contains("visible_time"))
    }

    @Test
    func ingestionMetricsCannotCarryChatContent() async throws {
        let store = try MessageStore(url: nil)
        let ingestor = MessageIngestor(store: store)
        await ingestor.ingest(frame(chat: "Secret Chat Title", ["secret message body"]))

        let metrics = await ingestor.snapshot()
        let json = String(decoding: try JSONEncoder().encode(metrics), as: UTF8.self)
        #expect(!json.contains("Secret Chat Title"))
        #expect(!json.contains("secret message body"))
    }
}

private struct FixedExtractor: FrameExtracting {
    let result: ExtractedConversationFrame
    var isConfigured: Bool { true }
    var processingLocation: ExtractionProcessingLocation { .onDevice }

    func extract(from frame: ObservedFrame) async throws -> ExtractedConversationFrame {
        result
    }
}

private final class InMemoryCredentials: CredentialStoring, @unchecked Sendable {
    private let lock = NSLock()
    private var storage: [String: String] = [:]

    func save(_ secret: String, account: String) throws {
        lock.withLock { storage[account] = secret }
    }

    func secret(account: String) throws -> String? {
        lock.withLock { storage[account] }
    }

    func remove(account: String) throws {
        lock.withLock { storage[account] = nil }
    }
}

private extension ObservedFrame {
    /// Synthetic frame built in code. No real WeChat imagery is used in tests.
    static func synthetic(secondsFromNow: Double) -> ObservedFrame {
        let side = 8
        let context = CGContext(
            data: nil,
            width: side,
            height: side,
            bitsPerComponent: 8,
            bytesPerRow: side,
            space: CGColorSpaceCreateDeviceGray(),
            bitmapInfo: CGImageAlphaInfo.none.rawValue
        )!
        context.setFillColor(gray: 0.5, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: side, height: side))
        let image = context.makeImage()!
        return ObservedFrame(
            image: image,
            capturedAt: Date(timeIntervalSince1970: 1_700_000_000 + secondsFromNow),
            captureMode: .visibleDisplayRegion,
            fingerprint: FrameFingerprint(image: image)!
        )
    }
}

/// The app-owned consent state another local process reads (M1.1).
///
/// The state is the app's assertion of the consent, so these tests check that
/// it is always present once the app has run, always well-formed, always in
/// step with the flag, and never anything but denial when nothing has been
/// written. Every defaults suite here is isolated; nothing touches the real
/// preference domain.
struct LocalPersistenceConsentStateTests {
    @Test
    func absenceIsDenialNotUnknown() {
        #expect(LocalPersistenceConsentState.load(from: makeDefaults()) == nil)
    }

    @Test @MainActor
    func aFreshInstallRecordsAnExplicitNo() {
        let defaults = makeDefaults()
        _ = AppModel(messageHistory: makeTestMessageHistory(), consentDefaults: defaults)
        let state = LocalPersistenceConsentState.load(from: defaults)
        #expect(state?.allowsLocalMessageStorage == false)
        #expect(state?.allowsMemoryStorage == false)
        #expect(state?.generation == 1)
    }

    @Test @MainActor
    func grantingWritesTheStateAndAdvancesTheGeneration() async {
        let defaults = makeDefaults()
        let model = AppModel(messageHistory: makeTestMessageHistory(), consentDefaults: defaults)
        await model.setAllowsLocalPersistence(true)
        let state = LocalPersistenceConsentState.load(from: defaults)
        #expect(state?.allowsLocalMessageStorage == true)
        #expect(state?.allowsMemoryStorage == true)
        #expect(state?.generation == 2)
    }

    @Test @MainActor
    func revokingTakesEffectInTheState() async {
        let defaults = makeDefaults()
        let model = AppModel(messageHistory: makeTestMessageHistory(), consentDefaults: defaults)
        await model.setAllowsLocalPersistence(true)
        await model.setAllowsLocalPersistence(false)
        let state = LocalPersistenceConsentState.load(from: defaults)
        #expect(state?.allowsLocalMessageStorage == false)
        #expect(state?.allowsMemoryStorage == false)
        #expect(state?.generation == 3)
        // The flag the app itself reads agrees.
        #expect(defaults.bool(forKey: AppModel.localPersistenceConsentKey) == false)
    }

    @Test @MainActor
    func aStateWrittenByAnOlderBuildIsBroughtUpToDateFromTheFlag() {
        let defaults = makeDefaults()
        // An older build stored only the flag.
        defaults.set(true, forKey: AppModel.localPersistenceConsentKey)
        _ = AppModel(messageHistory: makeTestMessageHistory(), consentDefaults: defaults)
        let state = LocalPersistenceConsentState.load(from: defaults)
        #expect(state?.allowsLocalMessageStorage == true)
        #expect(state?.generation == 1)
    }

    @Test
    func aMalformedStateIsRejectedWholesale() {
        let missingField: [String: Any] = [
            "version": 1, "allowsLocalMessageStorage": true, "generation": 1, "updatedAt": 1.0,
        ]
        #expect(LocalPersistenceConsentState(dictionary: missingField) == nil)
        let wrongType: [String: Any] = [
            "version": 1, "allowsLocalMessageStorage": "yes", "allowsMemoryStorage": true,
            "generation": 1, "updatedAt": 1.0,
        ]
        #expect(LocalPersistenceConsentState(dictionary: wrongType) == nil)
        let unknownVersion: [String: Any] = [
            "version": 2, "allowsLocalMessageStorage": true, "allowsMemoryStorage": true,
            "generation": 1, "updatedAt": 1.0,
        ]
        #expect(LocalPersistenceConsentState(dictionary: unknownVersion) == nil)
        let negativeGeneration: [String: Any] = [
            "version": 1, "allowsLocalMessageStorage": true, "allowsMemoryStorage": true,
            "generation": -1, "updatedAt": 1.0,
        ]
        #expect(LocalPersistenceConsentState(dictionary: negativeGeneration) == nil)
    }

    @Test
    func aMalformedStoredStateIsReplacedByTheNextWrite() {
        let defaults = makeDefaults()
        defaults.set(["version": 1, "garbage": true], forKey: LocalPersistenceConsentState.key)
        let written = LocalPersistenceConsentState.record(allowsLocalMessageStorage: false, in: defaults)
        #expect(written.generation == 1)
        #expect(LocalPersistenceConsentState.load(from: defaults) == written)
    }

    @Test
    func theStateCarriesNothingButTheContract() {
        let state = LocalPersistenceConsentState.record(allowsLocalMessageStorage: true, in: makeDefaults())
        #expect(Set(state.dictionary.keys) == [
            "version", "allowsLocalMessageStorage", "allowsMemoryStorage", "generation", "updatedAt",
        ])
    }
}
