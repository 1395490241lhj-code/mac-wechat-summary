import XCTest
import Observation
import SQLite3
@testable import WeChatCompanion

final class ConsumerConversationPreviewTests: XCTestCase {
    func testArchiveTailBeyondReaderLimitAndUnattributedEvidenceStaySeparate() async throws {
        let store = try MessageStore(url: nil)
        let date = Date(timeIntervalSince1970: 1_700_000_000)
        let transcript = WeChatNativeTranscript.attributed(WeChatAttributedTranscript(
            messages: (0...600).map {
                WeChatAttributedArchiveMessage(sequence: $0, sender: "Ana", sentAt: date,
                    sentAtText: "source time", text: "record \($0)")
            }, timeZoneIdentifier: "UTC"))
        let first = try await store.persistArchiveEvidence(transcript: transcript,
            conversationKey: ArchiveConversationKey("preview-A"), importedAt: date)
        let second = try await store.persistArchiveEvidence(transcript: .unattributed(
            WeChatUnattributedTranscript(records: [
                WeChatUnattributedArchiveRecord(sequence: 0, recordText: "·first"),
                WeChatUnattributedArchiveRecord(sequence: 1, recordText: "·unattributed tail")
            ])), conversationKey: ArchiveConversationKey("preview-B"), importedAt: date)
        guard case .inserted(let a, _) = first, case .inserted(let b, _) = second else {
            return XCTFail("expected distinct imports")
        }
        let previews = try await store.consumerConversationPreviews(ids: [.archiveImport(a), .archiveImport(b)])
        XCTAssertEqual(previews[.archiveImport(a)]?.line(), "Ana: record 600")
        XCTAssertEqual(previews[.archiveImport(b)]?.line(), "·unattributed tail")
        let reader = try await store.archiveRecords(importID: a)
        XCTAssertEqual(reader.last?.sequence, 499)
    }

    func testVisualSequenceTailWinsOverLaterBackfillAndOlderText() async throws {
        let store = try MessageStore(url: nil)
        let date = Date(timeIntervalSince1970: 1_700_000_000)
        let id = try await store.conversationID(forTitle: "Fixture", seenAt: date)
        try await store.append([ExtractedVisibleMessage(sender: "Ana", text: "older text", kind: .text),
            ExtractedVisibleMessage(kind: .image)], conversationID: id, observedAt: date)
        try await store.prepend([ExtractedVisibleMessage(sender: "Bo", text: "backfilled older", kind: .text)],
            conversationID: id, observedAt: date.addingTimeInterval(500))
        let previews = try await store.consumerConversationPreviews(ids: [.visualConversation(id)])
        XCTAssertEqual(previews[.visualConversation(id)]?.line(), "Image observed")
        try await store.append([ExtractedVisibleMessage(kind: .file)], conversationID: id, observedAt: date)
        let filePreview = try await store.consumerConversationPreviews(ids: [.visualConversation(id)])
        XCTAssertEqual(filePreview[.visualConversation(id)]?.line(), "File observed")
        try await store.append([ExtractedVisibleMessage(sender: "Ana", kind: .unknown)],
            conversationID: id, observedAt: date)
        let noText = try await store.consumerConversationPreviews(ids: [.visualConversation(id)])
        XCTAssertEqual(noText[.visualConversation(id)]?.line(), "No text preview available")
    }

    func testBatchScopesCollidingIDsAndDoesNotCoalesceSameDisplayNames() async throws {
        let store = try MessageStore(url: nil)
        let date = Date(timeIntervalSince1970: 1_700_000_000)
        let id = try await store.conversationID(forTitle: "Same name", seenAt: date)
        try await store.append([ExtractedVisibleMessage(text: "visual tail", kind: .text)],
            conversationID: id, observedAt: date)
        for index in 0..<205 {
            _ = try await store.persistArchiveEvidence(transcript: .unattributed(
                WeChatUnattributedTranscript(records: [
                    WeChatUnattributedArchiveRecord(sequence: 0, recordText: "archive \(index)")
                ])), conversationKey: ArchiveConversationKey("scope-\(index)"), importedAt: date)
        }
        let imports = try await store.archiveImportSummaries()
        for item in imports { _ = try await store.setArchiveImportDisplayName(importID: item.id, displayName: "Same name") }
        let ids = imports.map { ConsumerConversationID.archiveImport($0.id) } + [.visualConversation(id)]
        let previews = try await store.consumerConversationPreviews(ids: ids + [.archiveImport(9999), .visualConversation(9999)])
        XCTAssertEqual(previews.count, 206)
        XCTAssertEqual(previews[.archiveImport(id)]?.line(), "archive 0")
        XCTAssertEqual(previews[.visualConversation(id)]?.line(), "visual tail")
        let rows = ConsumerConversationRow.rows(archive: ArchiveEvidenceSnapshot(storeState: .ready,
            imports: try await store.archiveImportSummaries()), visual: CaptureLedger(storeState: .ready,
            conversations: try await store.conversationSummaries()), previews: previews)
        XCTAssertEqual(Set(rows.map(\.id)).count, 206)
        XCTAssertEqual(rows.first(where: { $0.id == .archiveImport(id) })?.preview, "archive 0")
    }

    func testDisplayNormalizationDoesNotAlterCanonicalTextOrInventSender() {
        let body = " 你好👩🏽‍💻\r\n第二行\u{2028}café "
        let value = ConsumerConversationPreview(sender: " Ana\n ", text: body, kind: nil)
        XCTAssertEqual(value.line(), "Ana: 你好👩🏽‍💻 第二行 café")
        XCTAssertEqual(value.text, body)
        XCTAssertEqual(ConsumerConversationPreview(sender: " \n", text: "body", kind: nil).line(), "body")
    }

    func testNoTextFallbacksKeepMessageKindAndImportAttachmentScope() {
        XCTAssertEqual(ConsumerConversationPreview(sender: nil, text: nil, kind: .file).line(), "File observed")
        XCTAssertEqual(ConsumerConversationPreview(sender: nil, text: nil, kind: .voice).line(), "Voice message observed")
        XCTAssertEqual(ConsumerConversationPreview(sender: "Ana", text: "\n", kind: nil)
            .line(attachmentCount: 3), "3 attachments in saved copy")
        XCTAssertEqual(ConsumerConversationPreview(sender: "Ana", text: "\n", kind: nil).line(),
            "No text preview available")
        XCTAssertEqual(ConsumerConversationPreview(sender: "Ana", text: "body", kind: nil)
            .line(attachmentCount: 3), "Ana: body")
    }
}

private actor ConsumerPreviewReadGate {
    private var requests: [CheckedContinuation<[ConsumerConversationID: ConsumerConversationPreview], Never>] = []
    func read(_ ids: [ConsumerConversationID]) async -> [ConsumerConversationID: ConsumerConversationPreview] {
        await withCheckedContinuation { requests.append($0) }
    }
    func waitForRequests(_ count: Int) async throws {
        let deadline = ContinuousClock.now.advanced(by: .seconds(5))
        while requests.count < count {
            guard ContinuousClock.now < deadline else { throw NSError(domain: "ConsumerPreviewReadGate", code: 1) }
            await Task.yield()
        }
    }
    func finishEmpty(_ index: Int) { requests[index].resume(returning: [:]) }
    func finish(_ index: Int, text: String) {
        requests[index].resume(returning: [.visualConversation(1): ConsumerConversationPreview(sender: nil, text: text, kind: .text)])
    }
}

@MainActor
final class ConsumerConversationPreviewLifecycleTests: XCTestCase {
    private func fixture(url: URL? = nil,
        reader: (@Sendable ([ConsumerConversationID]) async -> [ConsumerConversationID: ConsumerConversationPreview])? = nil)
        async throws -> (AppModel, LocalMessageHistory) {
        let history = LocalMessageHistory(url: url)
        await history.setEnabled(true)
        let openStore = await history.openStore()
        let store = try XCTUnwrap(openStore)
        let id = try await store.conversationID(forTitle: "Fixture", seenAt: Date())
        try await store.append([ExtractedVisibleMessage(text: "old search needle", kind: .text)], conversationID: id, observedAt: Date())
        try await store.append((1...120).map { ExtractedVisibleMessage(text: "tail \($0)", kind: .text) },
            conversationID: id, observedAt: Date())
        let defaults = UserDefaults(suiteName: "consumer-preview-test-\(UUID().uuidString)")!
        defaults.set(true, forKey: AppModel.localPersistenceConsentKey)
        let app = AppModel(messageHistory: history, shareInbox: nil, consentDefaults: defaults,
            consumerPreviewReader: reader)
        return (app, history)
    }

    func testLedgerPreviewCadenceUsesOwnersAndCountsNotActivity() async throws {
        let counter = ConsumerPreviewReadCounter()
        let (_, history) = try await fixture()
        let defaults = UserDefaults(suiteName: "preview-cadence-\(UUID().uuidString)")!
        defaults.set(true, forKey: AppModel.localPersistenceConsentKey)
        let app = AppModel(messageHistory: history, shareInbox: nil, consentDefaults: defaults,
            consumerPreviewReader: { ids in
                await counter.record()
                return await history.consumerConversationPreviews(ids: ids)
            })
        let openStore = await history.openStore()
        let store = try XCTUnwrap(openStore)
        await app.refreshCaptureLedger()
        let initial = app.consumerConversationPreviews
        let changes = DispatchSemaphore(value: 0)
        withObservationTracking { _ = app.consumerConversationPreviews } onChange: { changes.signal() }
        var reads = await counter.reads
        XCTAssertEqual(reads, 1)
        for _ in 0..<5 { await app.refreshCaptureLedger() }
        reads = await counter.reads
        XCTAssertEqual(reads, 1)
        XCTAssertEqual(app.consumerConversationPreviews, initial)
        let previousActivity = app.captureLedger.conversations.first?.lastCapturedAt
        _ = try await store.conversationID(forTitle: "Fixture", seenAt: Date().addingTimeInterval(100))
        await app.refreshCaptureLedger()
        XCTAssertNotEqual(app.captureLedger.conversations.first?.lastCapturedAt, previousActivity)
        reads = await counter.reads
        XCTAssertEqual(reads, 1)
        XCTAssertEqual(app.consumerConversationPreviews, initial)
        XCTAssertEqual(changes.wait(timeout: .now()), .timedOut, "unchanged polling must not publish preview churn")
        try await store.append([ExtractedVisibleMessage(text: "new tail", kind: .text)],
            conversationID: 1, observedAt: Date())
        await app.refreshCaptureLedger()
        reads = await counter.reads
        XCTAssertEqual(reads, 2)
        XCTAssertEqual(app.consumerConversationPreviews[.visualConversation(1)]?.line(), "new tail")
        let second = try await store.conversationID(forTitle: "Second", seenAt: Date())
        try await store.append([ExtractedVisibleMessage(text: "second tail", kind: .text)],
            conversationID: second, observedAt: Date())
        await app.refreshCaptureLedger()
        reads = await counter.reads
        XCTAssertEqual(reads, 3)
        XCTAssertEqual(app.consumerConversationPreviews[.visualConversation(second)]?.line(), "second tail")
        try await store.deleteAllHistory()
        await app.refreshCaptureLedger()
        XCTAssertTrue(app.consumerConversationPreviews.isEmpty)
        reads = await counter.reads
        XCTAssertEqual(reads, 3, "no owners means no content query")
        for _ in 0..<3 { await app.refreshCaptureLedger() }
        XCTAssertTrue(app.consumerConversationPreviews.isEmpty)
    }

    func testRetentionSweepClearsArchivePreviewWithUnchangedVisualCounts() async throws {
        let (app, history) = try await fixture()
        let date = Date()
        _ = try await history.persistArchiveEvidence(transcript: .unattributed(
            WeChatUnattributedTranscript(records: [WeChatUnattributedArchiveRecord(sequence: 0, recordText: "expired archive")])),
            conversationKey: ArchiveConversationKey("expiry"), importedAt: date.addingTimeInterval(-40 * 86_400))
        await app.refreshCaptureLedger()
        await app.refreshArchiveEvidence()
        XCTAssertNotNil(app.consumerConversationPreviews[.archiveImport(1)])
        let counts = app.captureLedger.conversations.map(\.retainedMessageCount)
        await history.setRetention(.thirtyDays)
        await app.refreshCaptureLedger()
        XCTAssertEqual(app.captureLedger.conversations.map(\.retainedMessageCount), counts)
        XCTAssertNil(app.consumerConversationPreviews[.archiveImport(1)])
        XCTAssertEqual(app.consumerConversationPreviews[.visualConversation(1)]?.line(), "tail 120")
    }

    func testRetentionSweepThenAppendRefreshesTailWithBalancedVisualCount() async throws {
        let (app, history) = try await fixture()
        await history.setRetention(.thirtyDays)
        let date = Date()
        let openStore = await history.openStore()
        let store = try XCTUnwrap(openStore)
        try await store.append([ExtractedVisibleMessage(text: "surviving tail", kind: .text)],
            conversationID: 1, observedAt: date.addingTimeInterval(60 * 86_400))
        await app.refreshCaptureLedger()
        let counts = app.captureLedger.conversations.map(\.retainedMessageCount)
        let ingestor = await history.ingestor()
        await ingestor?.sweepNow(date.addingTimeInterval(31 * 86_400))
        try await store.append((0..<121).map { _ in ExtractedVisibleMessage(text: "replacement tail", kind: .text) },
            conversationID: 1, observedAt: date.addingTimeInterval(60 * 86_400))
        await app.refreshCaptureLedger()
        XCTAssertEqual(app.captureLedger.conversations.map(\.retainedMessageCount), counts)
        XCTAssertEqual(app.consumerConversationPreviews[.visualConversation(1)]?.line(), "replacement tail")
    }

    func testBalancedExpiryAndAppendDuringPreviewReadRefreshesCanonicalTail() async throws {
        let (_, history) = try await fixture()
        let openStore = await history.openStore()
        let store = try XCTUnwrap(openStore)
        let date = Date()
        let future = date.addingTimeInterval(60 * 86_400)
        let survivor = ExtractedVisibleMessage(text: "survivor", kind: .text)
        try await store.append([survivor], conversationID: 1, observedAt: future)
        let gate = ConsumerPreviewReadGate()
        let defaults = UserDefaults(suiteName: "preview-overlap-\(UUID().uuidString)")!
        defaults.set(true, forKey: AppModel.localPersistenceConsentKey)
        let app = AppModel(messageHistory: history, shareInbox: nil, consentDefaults: defaults,
            consumerPreviewReader: { ids in
                let result = await history.consumerConversationPreviews(ids: ids)
                _ = await gate.read(ids)
                return result
            })
        let pending = Task { await app.refreshCaptureLedger() }
        try await gate.waitForRequests(1)
        let counts = app.captureLedger.conversations.map(\.retainedMessageCount)
        // Model the mixed-snapshot boundary: expiry restores the same final count,
        // while a canonical tail read is already suspended before publication.
        _ = try await store.applyRetention(.thirtyDays, now: date.addingTimeInterval(31 * 86_400))
        let ingestor = await history.ingestor()
        await ingestor?.ingest(ExtractedConversationFrame(capturedAt: future,
            chat: ExtractedChatIdentity(title: "Fixture", confidence: 1),
            messages: [survivor] + (0..<121).map { ExtractedVisibleMessage(text: "new \($0)", kind: .text) }))
        await gate.finishEmpty(0)
        await pending.value
        XCTAssertEqual(app.consumerConversationPreviews[.visualConversation(1)]?.line(), "survivor")
        let next = Task { await app.refreshCaptureLedger() }
        try await gate.waitForRequests(2)
        await gate.finishEmpty(1)
        await next.value
        XCTAssertEqual(app.captureLedger.conversations.map(\.retainedMessageCount), counts)
        XCTAssertEqual(app.consumerConversationPreviews[.visualConversation(1)]?.line(), "new 120")
    }

    func testReplacedStoreWithSameOwnersAndCountsRefreshesPreview() async throws {
        let (app, history) = try await fixture()
        await app.refreshCaptureLedger()
        let originalCounts = app.captureLedger.conversations.map(\.retainedMessageCount)
        await history.setEnabled(false)
        await history.setEnabled(true)
        let openStore = await history.openStore()
        let store = try XCTUnwrap(openStore)
        let id = try await store.conversationID(forTitle: "Replacement", seenAt: Date())
        XCTAssertEqual(id, 1)
        try await store.append((0..<121).map { _ in ExtractedVisibleMessage(text: "replacement", kind: .text) },
            conversationID: id, observedAt: Date())
        await app.refreshCaptureLedger()
        XCTAssertEqual(app.captureLedger.conversations.map(\.retainedMessageCount), originalCounts)
        XCTAssertEqual(app.consumerConversationPreviews[.visualConversation(1)]?.line(), "replacement")
    }

    func testReplacedStoreRejectsDelayedPreviewEvenWhenOwnerIDIsReused() async throws {
        let gate = ConsumerPreviewReadGate()
        let (app, history) = try await fixture(reader: { await gate.read($0) })
        let pending = Task { await app.refreshCaptureLedger() }
        try await gate.waitForRequests(1)
        await history.setEnabled(false)
        await history.setEnabled(true)
        let openStore = await history.openStore()
        let store = try XCTUnwrap(openStore)
        let id = try await store.conversationID(forTitle: "Replacement", seenAt: Date())
        try await store.append([ExtractedVisibleMessage(text: "replacement", kind: .text)],
            conversationID: id, observedAt: Date())
        await gate.finish(0, text: "old store")
        await pending.value
        XCTAssertTrue(app.consumerConversationPreviews.isEmpty)
    }

    func testSearchWindowDoesNotReplaceCanonicalListPreview() async throws {
        let (app, history) = try await fixture()
        await app.refreshCaptureLedger()
        XCTAssertEqual(app.consumerConversationPreviews[.visualConversation(1)]?.line(), "tail 120")
        let snapshot = await history.searchLocalMessages("old search needle")
        let hit = try XCTUnwrap(snapshot.results.first)
        await app.openSearchResult(hit)
        XCTAssertTrue(app.selectedVisualIsHitWindow)
        XCTAssertFalse(app.selectedVisualMessages.contains(where: { $0.text == "tail 120" }))
        XCTAssertEqual(app.consumerConversationPreviews[.visualConversation(1)]?.line(), "tail 120")
    }

    func testModelConsentOffRefusesNewPreviewWhileHistoryIsStillReady() async throws {
        let (_, history) = try await fixture()
        let defaults = UserDefaults(suiteName: "consumer-preview-off-\(UUID().uuidString)")!
        let app = AppModel(messageHistory: history, shareInbox: nil, consentDefaults: defaults)
        XCTAssertFalse(app.allowsLocalPersistence)
        await app.refreshCaptureLedger()
        XCTAssertTrue(app.consumerConversationPreviews.isEmpty)
    }

    func testConsentWithdrawalAndDeleteAllClearSavedPreviews() async throws {
        let (app, _) = try await fixture()
        await app.refreshCaptureLedger()
        XCTAssertFalse(app.consumerConversationPreviews.isEmpty)
        await app.setAllowsLocalPersistence(false)
        XCTAssertTrue(app.consumerConversationPreviews.isEmpty)
        let (other, _) = try await fixture()
        await other.refreshCaptureLedger()
        XCTAssertFalse(other.consumerConversationPreviews.isEmpty)
        await other.deleteLocalMessageHistory()
        XCTAssertTrue(other.consumerConversationPreviews.isEmpty)
    }

    func testDelayedOldRefreshCannotReplaceNewerPreview() async throws {
        let gate = ConsumerPreviewReadGate()
        let (app, _) = try await fixture(reader: { await gate.read($0) })
        let first = Task { await app.refreshCaptureLedger() }
        try await gate.waitForRequests(1)
        let second = Task { await app.refreshConsumerConversationPreviews() }
        try await gate.waitForRequests(2)
        await gate.finish(1, text: "new")
        await second.value
        await gate.finish(0, text: "stale")
        await first.value
        XCTAssertEqual(app.consumerConversationPreviews[.visualConversation(1)]?.line(), "new")
    }

    func testDisabledStoreRejectsDelayedResultWithoutAnotherModelRefresh() async throws {
        let gate = ConsumerPreviewReadGate()
        let (app, history) = try await fixture(reader: { await gate.read($0) })
        let refresh = Task { await app.refreshCaptureLedger() }
        try await gate.waitForRequests(1)
        await history.setEnabled(false)
        await gate.finish(0, text: "stale")
        await refresh.value
        XCTAssertTrue(app.consumerConversationPreviews.isEmpty)
    }

    func testConsentDeleteAndDisappearanceInvalidateInFlightRead() async throws {
        for action in ["consent", "delete", "disappearance"] {
            let gate = ConsumerPreviewReadGate()
            let (app, history) = try await fixture(reader: { await gate.read($0) })
            let refresh = Task { await app.refreshCaptureLedger() }
            try await gate.waitForRequests(1)
            switch action {
            case "consent": await app.setAllowsLocalPersistence(false)
            case "delete": await app.deleteLocalMessageHistory()
            default:
                let openStore = await history.openStore()
                try await XCTUnwrap(openStore).deleteAllHistory()
                await app.refreshCaptureLedger()
            }
            await gate.finish(0, text: "stale")
            await refresh.value
            XCTAssertTrue(app.consumerConversationPreviews.isEmpty, action)
        }
    }

    func testCanonicalDisappearanceRejectsDelayedReadWithoutModelRefresh() async throws {
        let gate = ConsumerPreviewReadGate()
        let (app, history) = try await fixture(reader: { await gate.read($0) })
        let refresh = Task { await app.refreshCaptureLedger() }
        try await gate.waitForRequests(1)
        let openStore = await history.openStore()
        try await XCTUnwrap(openStore).deleteAllHistory()
        await gate.finish(0, text: "stale")
        await refresh.value
        XCTAssertTrue(app.consumerConversationPreviews.isEmpty)
    }

    func testOldArchivePreviewRefreshCannotReplaceNewCanonicalReaderSelection() async throws {
        let gate = ConsumerPreviewReadGate()
        let (app, history) = try await fixture(reader: { await gate.read($0) })
        func save(_ key: String, _ date: Date) async throws -> Int64 {
            let result = try await history.persistArchiveEvidence(transcript: .unattributed(
                WeChatUnattributedTranscript(records: [
                    WeChatUnattributedArchiveRecord(sequence: 0, recordText: key)
                ])), conversationKey: ArchiveConversationKey(key), importedAt: date)
            guard case .inserted(let id, _) = result else { throw NSError(domain: "fixture", code: 1) }
            return id
        }
        let date = Date()
        _ = try await save("first", date)
        let older = Task { await app.refreshArchiveEvidence() }
        try await gate.waitForRequests(1)
        let newerID = try await save("second", date.addingTimeInterval(1))
        let newer = Task { await app.refreshArchiveEvidence() }
        try await gate.waitForRequests(2)
        await gate.finishEmpty(1)
        await newer.value
        await app.selectArchiveImport(newerID)
        XCTAssertEqual(app.selectedArchiveImportID, newerID)
        await gate.finishEmpty(0)
        await older.value
        XCTAssertEqual(app.selectedArchiveImportID, newerID)
        XCTAssertEqual(app.selectedArchiveRecords.first?.text, "second")
        XCTAssertFalse(app.archiveContextUnavailable)
    }

    func testOppositeSourceRefreshCannotSkipDisappearedReaderCleanup() async throws {
        for removeVisual in [true, false] {
            let gate = ConsumerPreviewReadGate()
            let (app, history) = try await fixture(reader: { await gate.read($0) })
            let date = Date()
            _ = try await history.persistArchiveEvidence(transcript: .unattributed(
                WeChatUnattributedTranscript(records: [WeChatUnattributedArchiveRecord(sequence: 0, recordText: "archive")])),
                conversationKey: ArchiveConversationKey("other-source"),
                importedAt: date.addingTimeInterval((removeVisual ? 60 : -40) * 86_400))
            let initialVisual = Task { await app.refreshCaptureLedger() }
            try await gate.waitForRequests(1)
            await gate.finishEmpty(0)
            await initialVisual.value
            let initialArchive = Task { await app.refreshArchiveEvidence() }
            try await gate.waitForRequests(2)
            await gate.finishEmpty(1)
            await initialArchive.value
            if removeVisual { await app.selectVisualConversation(1) }
            else { await app.selectArchiveImport(1) }
            let nextRequest = 2
            let openStore = await history.openStore()
            _ = try await XCTUnwrap(openStore).applyRetention(.thirtyDays,
                now: removeVisual ? date.addingTimeInterval(31 * 86_400) : date)
            let removed = Task {
                if removeVisual { await app.refreshCaptureLedger() }
                else { await app.refreshArchiveEvidence() }
            }
            try await gate.waitForRequests(nextRequest + 1)
            if !removeVisual {
                // A real count change makes the opposite Visual refresh supersede the pending preview.
                try await XCTUnwrap(openStore).append([ExtractedVisibleMessage(text: "new tail", kind: .text)],
                    conversationID: 1, observedAt: date)
            }
            let opposite = Task {
                if removeVisual { await app.refreshArchiveEvidence() }
                else { await app.refreshCaptureLedger() }
            }
            try await gate.waitForRequests(nextRequest + 2)
            await gate.finishEmpty(nextRequest + 1)
            await opposite.value
            await gate.finishEmpty(nextRequest)
            await removed.value
            if removeVisual {
                XCTAssertNil(app.selectedVisualConversationID)
                XCTAssertTrue(app.selectedVisualMessages.isEmpty)
            } else {
                XCTAssertNil(app.selectedArchiveImportID)
                XCTAssertTrue(app.selectedArchiveRecords.isEmpty)
            }
            XCTAssertNil(app.contextRevealRequest)
        }
    }

    func testUnavailableStoreAndQueryFailureClearOldPreview() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("preview-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let url = root.appendingPathComponent("fixture.sqlite")
        let (app, history) = try await fixture(url: url)
        await app.refreshCaptureLedger()
        XCTAssertFalse(app.consumerConversationPreviews.isEmpty)
        var database: OpaquePointer?
        XCTAssertEqual(sqlite3_open(url.path, &database), SQLITE_OK)
        defer { sqlite3_close(database) }
        // Read failure while the actor still owns an open store.
        XCTAssertEqual(sqlite3_exec(database, "DROP TABLE messages;", nil, nil, nil), SQLITE_OK)
        await app.refreshConsumerConversationPreviews()
        XCTAssertTrue(app.consumerConversationPreviews.isEmpty)
        await history.setEnabled(false)
        XCTAssertEqual(sqlite3_exec(database, "PRAGMA user_version = 99;", nil, nil, nil), SQLITE_OK)
        await history.setEnabled(true)
        let state = await history.storeState
        XCTAssertEqual(state, .unavailable)
        await app.refreshCaptureLedger()
        XCTAssertTrue(app.consumerConversationPreviews.isEmpty)
    }

    func testPreviewRefreshDoesNotTriggerIngestionOrDerivedWork() async throws {
        let (app, history) = try await fixture()
        let work = ConsumerPreviewUnexpectedWork()
        let defaults = UserDefaults(suiteName: "consumer-preview-work-\(UUID().uuidString)")!
        defaults.set(true, forKey: AppModel.localPersistenceConsentKey)
        let isolated = AppModel(messageHistory: history, shareInbox: nil,
            consentDefaults: defaults, memorySync: work, dailySummary: work, followUpCandidates: work)
        let ingestor = await history.ingestor()
        let before = await ingestor?.snapshot()
        await isolated.refreshCaptureLedger()
        await isolated.refreshArchiveEvidence()
        await isolated.refreshConsumerConversationPreviews()
        let after = await ingestor?.snapshot()
        XCTAssertEqual(before, after)
        let calls = await work.calls
        XCTAssertEqual(calls, 0)
        XCTAssertFalse(isolated.consumerConversationPreviews.isEmpty)
        XCTAssertNil(app.selectedVisualConversationID)
    }

    func testFailedReadDoesNotKeepOldPreview() async throws {
        let gate = ConsumerPreviewReadGate()
        let (app, _) = try await fixture(reader: { await gate.read($0) })
        let refresh = Task { await app.refreshCaptureLedger() }
        try await gate.waitForRequests(1)
        await gate.finish(0, text: "old")
        await refresh.value
        let next = Task { await app.refreshConsumerConversationPreviews() }
        try await gate.waitForRequests(2)
        XCTAssertTrue(app.consumerConversationPreviews.isEmpty, "pending read must not keep stale content")
        await gate.finishEmpty(1)
        await next.value
        XCTAssertTrue(app.consumerConversationPreviews.isEmpty)
    }
}

private actor ConsumerPreviewReadCounter {
    private(set) var reads = 0
    func record() { reads += 1 }
}

private actor ConsumerPreviewUnexpectedWork: MemorySyncRunning, DailySummaryRunning, FollowUpCandidateRunning {
    private(set) var calls = 0
    func sync(source: MemorySource) async -> MemorySyncOutcome { calls += 1; return .failed(.runnerUnavailable) }
    func freshness(source: MemorySource) async -> MemoryFreshnessSummary? { calls += 1; return nil }
    func prepare(source: MemorySource, start: Date, end: Date, messageLimit: Int) async -> DailySummaryOutcome {
        calls += 1; return .failed(.runnerUnavailable)
    }
    func scan(source: MemorySource, start: Date, end: Date, messageLimit: Int, candidateLimit: Int) async -> FollowUpOutcome {
        calls += 1; return .failed(.runnerUnavailable)
    }
}

/// B7.1 -- the transcript presentation layer.
///
/// The contract these tests protect is honesty plus identity: a row is filed
/// under the time the source really has, an unattributed record never reads
/// like a senderless attributed one, and grouping never changes which row a
/// search hit scrolls to.
final class TranscriptPresentationTests: XCTestCase {
    func testSpokenConversationDescriptionsDistinguishSameTimeOnDifferentDates() {
        let first = ConsumerConversationRow(id: .archiveImport(1), title: "Fixture chat",
            date: Date(timeIntervalSince1970: 1_700_000_000), note: "Saved at 18:00")
        let second = ConsumerConversationRow(id: .archiveImport(2), title: "Fixture chat",
            date: first.date.addingTimeInterval(86_400), note: first.note)
        XCTAssertNotEqual(first.accessibilityDescription, second.accessibilityDescription)
        XCTAssertTrue(first.accessibilityDescription.contains(first.title))
        XCTAssertTrue(first.accessibilityDescription.contains(first.note))
    }
    func testConsumerListDoesNotMergeSameNamesOrCollidingSourceIDs() {
        let date = Date(timeIntervalSince1970: 1_700_000_000)
        func saved(_ id: Int64) -> ArchiveEvidenceImportSummary {
            ArchiveEvidenceImportSummary(id: id, displayName: "Same name", shape: .unattributed,
                importedAt: date, recordCount: 2, firstSentAt: nil, lastSentAt: nil, isAnonymous: true,
                link: nil, attachmentBatchCount: 0, attachmentCount: 0, materializedAttachmentCount: 0)
        }
        let archive = ArchiveEvidenceSnapshot(storeState: .ready, imports: [saved(2), saved(1)])
        let visual = CaptureLedger(storeState: .ready, conversations: [
            CapturedConversationSummary(id: 1, title: "Same name", retainedMessageCount: 2,
                firstCapturedAt: date.addingTimeInterval(-10), lastCapturedAt: date)
        ])
        let rows = ConsumerConversationRow.rows(archive: archive, visual: visual)
        XCTAssertEqual(rows.map(\.id), [.archiveImport(1), .archiveImport(2), .visualConversation(1)])
        XCTAssertEqual(Set(rows.map(\.id)).count, 3)
        XCTAssertTrue(rows[0].note.hasPrefix("Saved "))
        XCTAssertTrue(rows[2].note.hasPrefix("Last seen "))
        XCTAssertTrue(rows.allSatisfy { $0.date == date })
        XCTAssertTrue(ConsumerConversationRow.rows(archive: .unavailable(.disabled), visual: .empty).isEmpty)
    }

    // MARK: - Grouping

    func testConsecutiveSameSenderOnSameDayIsOneGroup() {
        let groups = TranscriptGrouping.groups(
            [
                visual(id: 1, sender: "Ana", at: at(12, 1)),
                visual(id: 2, sender: "Ana", at: at(12, 2)),
            ],
            calendar: utc
        )
        XCTAssertEqual(groups.count, 1)
        XCTAssertEqual(groups[0].rows.map(\.id), [.visualMessage(1), .visualMessage(2)])
    }

    func testSenderChangeStartsNewGroup() {
        let groups = TranscriptGrouping.groups(
            [
                visual(id: 1, sender: "Ana", at: at(12, 1)),
                visual(id: 2, sender: "Bo", at: at(12, 2)),
                visual(id: 3, sender: "Ana", at: at(12, 3)),
            ],
            calendar: utc
        )
        XCTAssertEqual(groups.count, 3)
    }

    func testReliableDayBoundaryStartsNewGroup() {
        let groups = TranscriptGrouping.groups(
            [
                visual(id: 1, sender: "Ana", at: at(23, 50)),
                visual(id: 2, sender: "Ana", at: at(0, 10, dayOffset: 1)),
            ],
            calendar: utc
        )
        XCTAssertEqual(groups.count, 2)
    }

    /// An invented "five minutes later is a new turn" rule would make the
    /// reader's hierarchy depend on a number nobody can check.
    func testNoTimeGapHeuristicSplitsASenderRun() {
        let groups = TranscriptGrouping.groups(
            [
                visual(id: 1, sender: "Ana", at: at(0, 0)),
                visual(id: 2, sender: "Ana", at: at(23, 0)),
            ],
            calendar: utc
        )
        XCTAssertEqual(groups.count, 1)
    }

    func testMissingSenderSaysSoAndKeepsItsOwnGroup() {
        let rows = [
            visual(id: 1, sender: nil, at: at(9, 0)),
            visual(id: 2, sender: nil, at: at(9, 1)),
        ]
        XCTAssertEqual(Set(rows.map(\.sender)), ["Sender unknown"])
        let groups = TranscriptGrouping.groups(rows, calendar: utc)
        XCTAssertEqual(groups.count, 1)
    }

    /// An unattributed record is not an attributed record that lost a sender.
    func testUnattributedArchiveNeverMergesWithUnknownSender() {
        let rows = [
            archive(sequence: 1, shape: .attributed, sender: nil, sentAt: at(10, 0)),
            archive(sequence: 2, shape: .unattributed, sender: nil, sentAt: at(10, 1)),
        ]
        XCTAssertEqual(rows[0].sender, "Unknown sender")
        XCTAssertEqual(rows[1].sender, "Unattributed record")
        XCTAssertEqual(TranscriptGrouping.groups(rows, calendar: utc).count, 2)
    }

    func testUndatedRowsGetNoDateSeparator() {
        let groups = TranscriptGrouping.groups(
            [archive(sequence: 1, shape: .attributed, sender: "Ana", sentAtText: "yesterday 14:30")],
            calendar: utc
        )
        XCTAssertEqual(groups.count, 1)
        XCTAssertNil(groups[0].date)
        XCTAssertNil(groups[0].dateLabel(reference: at(9, 0), calendar: utc))
    }

    // MARK: - Date separators

    /// A date separator marks a day transition, not a speaker change: several
    /// senders on one day get one "Yesterday", not one per group.
    func testSenderChangesOnTheSameDayGetOneDateSeparator() {
        let groups = TranscriptGrouping.groups(
            [
                visual(id: 1, sender: "Ana", at: at(12, 1)),
                visual(id: 2, sender: "Bo", at: at(12, 2)),
                visual(id: 3, sender: "Cy", at: at(12, 3)),
            ],
            calendar: utc
        )
        XCTAssertEqual(groups.count, 3)
        XCTAssertEqual(TranscriptGrouping.dateSeparatorIndices(in: groups), [0])
    }

    func testSameSenderOnANewDayGetsASecondSeparator() {
        let groups = TranscriptGrouping.groups(
            [
                visual(id: 1, sender: "Ana", at: at(23, 50)),
                visual(id: 2, sender: "Ana", at: at(0, 10, dayOffset: 1)),
            ],
            calendar: utc
        )
        XCTAssertEqual(TranscriptGrouping.dateSeparatorIndices(in: groups), [0, 1])
    }

    func testSenderAndDayBothChangingGetsASecondSeparator() {
        let groups = TranscriptGrouping.groups(
            [
                visual(id: 1, sender: "Ana", at: at(23, 50)),
                visual(id: 2, sender: "Bo", at: at(0, 10, dayOffset: 1)),
            ],
            calendar: utc
        )
        XCTAssertEqual(TranscriptGrouping.dateSeparatorIndices(in: groups), [0, 1])
    }

    /// A raw display string is not a date, so an undated group neither emits a
    /// separator of its own nor hides the next real day.
    func testUndatedGroupsNeitherAddNorSuppressSeparators() {
        let groups = TranscriptGrouping.groups(
            [
                visual(id: 1, sender: "Ana", at: at(12, 1)),
                archive(sequence: 1, shape: .attributed, sender: "Ana", sentAtText: "yesterday 14:30"),
                visual(id: 2, sender: "Bo", at: at(9, 0, dayOffset: 1)),
            ],
            calendar: utc
        )
        XCTAssertEqual(groups.map(\.date), [utc.startOfDay(for: at(12, 1)), nil, utc.startOfDay(for: at(9, 0, dayOffset: 1))])
        XCTAssertEqual(TranscriptGrouping.dateSeparatorIndices(in: groups), [0, 2])
    }

    // MARK: - Reveal anchors

    /// The B6.2 contract: grouping is a presentation wrapper, so the set of
    /// scrollable anchors must be byte-for-byte the same before and after.
    func testGroupingPreservesEveryCanonicalRevealAnchor() {
        let rows = [
            visual(id: 1, sender: "Ana", at: at(12, 1)),
            visual(id: 2, sender: "Ana", at: at(12, 2)),
            visual(id: 3, sender: "Bo", at: at(12, 3)),
        ]
        let grouped = TranscriptGrouping.groups(rows, calendar: utc).flatMap(\.rows)
        XCTAssertEqual(grouped.map(\.id), rows.map(\.id))
        XCTAssertEqual(Set(grouped.map(\.id)), [.visualMessage(1), .visualMessage(2), .visualMessage(3)])
    }

    /// Choosing which groups carry a separator must not touch the rows or their
    /// anchors: the B6.2 reveal target is identical with and without any date
    /// separator at all.
    func testDateSeparatorsDoNotDisturbCanonicalAnchors() {
        let rows = [
            visual(id: 1, sender: "Ana", at: at(12, 1)),
            visual(id: 2, sender: "Bo", at: at(12, 2)),
            archive(sequence: 7, shape: .unattributed, sender: nil, sentAtText: "yesterday 14:30"),
            visual(id: 3, sender: "Ana", at: at(9, 0, dayOffset: 1)),
        ]
        let groups = TranscriptGrouping.groups(rows, calendar: utc)
        XCTAssertEqual(groups.flatMap(\.rows).map(\.id), rows.map(\.id))
        XCTAssertEqual(
            groups.map(\.id),
            [.visualMessage(1), .visualMessage(2), .archiveRecord(importID: 42, sequence: 7), .visualMessage(3)]
        )
    }

    func testArchiveAnchorsStayImportAndSequence() {
        let row = archive(sequence: 7, shape: .attributed, sender: "Ana")
        XCTAssertEqual(row.id, .archiveRecord(importID: 42, sequence: 7))
    }

    // MARK: - Retained count wording

    /// The count row used to read "7 messages kept messages": the word lived in
    /// both the presentation string and the view. It belongs to the string.
    func testRetainedCountWordingIsSingularAndPlural() {
        XCTAssertEqual(ledger(kept: 1).conversations[0].retainedCount, "1 message kept")
        XCTAssertEqual(ledger(kept: 7).conversations[0].retainedCount, "7 messages kept")
    }

    /// A group is identified by its first row, so nothing can ever scroll to a
    /// container that has no canonical row behind it.
    func testGroupIdentityIsItsFirstRow() {
        let groups = TranscriptGrouping.groups(
            [
                visual(id: 1, sender: "Ana", at: at(12, 1)),
                visual(id: 2, sender: "Ana", at: at(12, 2)),
            ],
            calendar: utc
        )
        XCTAssertEqual(groups[0].id, .visualMessage(1))
    }

    // MARK: - Time semantics

    func testVisualDatesAreObservedNotSent() {
        let row = visual(id: 1, sender: "Ana", at: at(12, 0))
        XCTAssertEqual(row.timeKind, .observed)
        XCTAssertEqual(
            TranscriptGrouping.groups([row], calendar: utc)[0].dateLabel(
                reference: at(12, 0), calendar: utc
            ),
            "Observed today"
        )
    }

    func testVisibleTimeIsShownLiterallyAndPrefixed() {
        let message = PersistedMessage(
            id: 1, conversationID: 1, sequence: 1, sender: "Ana", ownership: .unknown,
            visibleTime: "\u{6628}\u{5929} 14:30", text: "body", kind: .text,
            confidence: 1, firstObservedAt: at(12, 0)
        )
        XCTAssertEqual(TranscriptRow(visual: message).timeLabel, "WeChat showed: \u{6628}\u{5929} 14:30")
    }

    func testStructuredArchiveSentAtFormatsAsAnArchiveDate() {
        let row = archive(sequence: 1, shape: .attributed, sender: "Ana", sentAt: at(9, 0))
        XCTAssertEqual(row.timeKind, .archiveSent)
        XCTAssertEqual(
            TranscriptGrouping.groups([row], calendar: utc)[0].dateLabel(
                reference: at(9, 0), calendar: utc
            ),
            "Today"
        )
    }

    /// A display string like "yesterday 14:30" is not a date. Parsing it would
    /// invent one.
    func testSentAtTextOnlyStaysLiteralAndInfersNoDate() {
        let row = archive(sequence: 1, shape: .attributed, sender: "Ana", sentAtText: "yesterday 14:30")
        XCTAssertNil(row.date)
        XCTAssertNil(row.timeKind)
        XCTAssertEqual(row.timeLabel, "yesterday 14:30")
    }

    func testNoTimestampAtAllShowsNothingExtra() {
        let row = archive(sequence: 1, shape: .attributed, sender: "Ana")
        XCTAssertNil(row.timeLabel)
    }

    func testDateLabelIsDeterministicViaInjectedReference() {
        let date = at(9, 0, dayOffset: -2)
        for (kind, expected) in [(TranscriptTimeKind.observed, "Observed yesterday"), (TranscriptTimeKind.archiveSent, "Yesterday")] {
            XCTAssertEqual(
                TranscriptDateLabel.label(for: at(9, 0, dayOffset: -1), kind: kind, reference: at(9, 0), calendar: utc),
                expected
            )
        }
        // Older days fall back to an absolute date, which must not depend on
        // the machine's clock.
        XCTAssertTrue(TranscriptDateLabel.label(for: date, kind: .archiveSent, reference: at(9, 0), calendar: utc).contains("2026"))
    }

    // MARK: - Content honesty

    func testMissingTextIsShownAsAPlaceholder() {
        let message = PersistedMessage(
            id: 1, conversationID: 1, sequence: 1, sender: "Ana", ownership: .unknown,
            visibleTime: nil, text: nil, kind: .text, confidence: 1, firstObservedAt: at(12, 0)
        )
        XCTAssertEqual(TranscriptRow(visual: message).body, "[No text retained]")
    }

    func testSourcesCarryDistinctRowProvenance() {
        XCTAssertEqual(TranscriptSource.visual.rowProvenance, "Visual capture")
        XCTAssertEqual(TranscriptSource.archiveAttributed.rowProvenance, "Attributed archive record")
        XCTAssertEqual(TranscriptSource.archiveUnattributed.rowProvenance, "Unattributed archive record")
    }

    /// These are different problems with different fixes, so the empty states
    /// must not collapse into one "no data" line.
    func testEmptyStatesReadDifferently() {
        let states: [TranscriptState] = [
            .storageOff, .storeUnavailable, .neverCaptured, .emptyImport,
            .noRetainedRows, .contextVanished, .searchHitVanished,
        ]
        let texts = states.map(\.text)
        XCTAssertEqual(Set(texts).count, texts.count)
        XCTAssertNotEqual(TranscriptState.noRetainedRows.text, TranscriptState.contextVanished.text)
    }

    // MARK: - Fixtures

    private var utc: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        return calendar
    }

    /// 2026-09-29 at the given UTC time, optionally on a later day.
    private func at(_ hour: Int, _ minute: Int, dayOffset: Int = 0) -> Date {
        utc.date(
            from: DateComponents(
                timeZone: TimeZone(identifier: "UTC"),
                year: 2026, month: 9, day: 29 + dayOffset, hour: hour, minute: minute
            )
        )!
    }

    private func visual(id: Int64, sender: String?, at observed: Date) -> TranscriptRow {
        TranscriptRow(visual: PersistedMessage(
            id: id, conversationID: 1, sequence: id, sender: sender, ownership: .unknown,
            visibleTime: nil, text: "body \(id)", kind: .text, confidence: 1,
            firstObservedAt: observed
        ))
    }

    private func archive(
        sequence: Int,
        shape: ArchiveEvidenceShape,
        sender: String?,
        sentAt: Date? = nil,
        sentAtText: String? = nil
    ) -> TranscriptRow {
        TranscriptRow(archive: ArchiveEvidenceRecord(
            importID: 42, importedAt: at(8, 0), shape: shape, sequence: sequence,
            sender: sender, sentAt: sentAt, sentAtText: sentAtText, text: "body \(sequence)"
        ))
    }

    private func ledger(kept: Int) -> CaptureLedgerPresentation {
        CaptureLedgerPresentation(ledger: CaptureLedger(
            storeState: .ready,
            conversations: [CapturedConversationSummary(
                id: 1, title: "Chat", retainedMessageCount: kept,
                firstCapturedAt: at(8, 0), lastCapturedAt: at(9, 0)
            )]
        ))
    }
}
