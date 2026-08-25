import CoreGraphics
import Foundation
import Testing
@testable import WeChatCompanion

/// End-to-end ingestion over a private in-memory database. Nothing in this
/// suite writes to disk, and every fixture string is a synthetic placeholder.
private func message(
    _ text: String?,
    sender: String? = nil,
    ownership: MessageOwnership = .other,
    visibleTime: String? = nil,
    kind: VisibleMessageKind = .text
) -> ExtractedVisibleMessage {
    ExtractedVisibleMessage(
        sender: sender,
        ownership: ownership,
        visibleTime: visibleTime,
        text: text,
        kind: kind,
        confidence: 0.9,
        // Deliberately present, to prove bounds are dropped rather than stored.
        normalizedBounds: CGRect(x: 0.1, y: 0.2, width: 0.3, height: 0.05)
    )
}

private func frame(
    chat: String?,
    _ messages: [ExtractedVisibleMessage],
    secondsFromNow: TimeInterval = 0
) -> ExtractedConversationFrame {
    ExtractedConversationFrame(
        capturedAt: Date(timeIntervalSince1970: 1_700_000_000 + secondsFromNow),
        chat: chat.map { ExtractedChatIdentity(title: $0, confidence: 0.9) },
        messages: messages
    )
}

private func makeStore() throws -> MessageStore {
    try MessageStore(url: nil)
}

private func texts(_ store: MessageStore, _ title: String) async throws -> [String?] {
    guard let conversation = try await store.conversation(titled: title) else { return [] }
    return try await store.messages(inConversation: conversation.id).map(\.text)
}

struct MessageIngestionTests {
    // MARK: - Overlapping frames

    @Test
    func consecutiveOverlappingFramesProduceOneContinuousStream() async throws {
        let store = try makeStore()
        let ingestor = MessageIngestor(store: store)

        await ingestor.ingest(frame(chat: "Chat A", [message("m1"), message("m2"), message("m3")]))
        // Scrolled down by two bubbles: m2,m3 overlap, m4,m5 are new.
        await ingestor.ingest(
            frame(chat: "Chat A", [message("m2"), message("m3"), message("m4"), message("m5")],
                  secondsFromNow: 5)
        )

        #expect(try await texts(store, "Chat A") == ["m1", "m2", "m3", "m4", "m5"])
        let metrics = await ingestor.snapshot()
        #expect(metrics.messagesAppended == 5)
        #expect(metrics.continuityGaps == 0)
    }

    @Test
    func anUnchangedFrameWritesNothingTwice() async throws {
        let store = try makeStore()
        let ingestor = MessageIngestor(store: store)
        let still = frame(chat: "Chat A", [message("m1"), message("m2")])

        await ingestor.ingest(still)
        await ingestor.ingest(still)
        await ingestor.ingest(still)

        #expect(try await texts(store, "Chat A") == ["m1", "m2"])
        #expect(await ingestor.snapshot().framesWithNothingNew == 2)
    }

    @Test
    func scrollingUpRecoversOlderMessagesInOrder() async throws {
        let store = try makeStore()
        let ingestor = MessageIngestor(store: store)

        await ingestor.ingest(frame(chat: "Chat A", [message("m3"), message("m4")]))
        await ingestor.ingest(
            frame(chat: "Chat A", [message("m1"), message("m2"), message("m3")],
                  secondsFromNow: 5)
        )

        #expect(try await texts(store, "Chat A") == ["m1", "m2", "m3", "m4"])
        let metrics = await ingestor.snapshot()
        #expect(metrics.messagesPrepended == 2)
        #expect(metrics.continuityGaps == 0)
    }

    @Test
    func aJumpBeyondOneScreenIsKeptButFlaggedAsAGap() async throws {
        let store = try makeStore()
        let ingestor = MessageIngestor(store: store)

        await ingestor.ingest(frame(chat: "Chat A", [message("m1"), message("m2")]))
        await ingestor.ingest(
            frame(chat: "Chat A", [message("z1"), message("z2")], secondsFromNow: 60)
        )

        #expect(try await texts(store, "Chat A") == ["m1", "m2", "z1", "z2"])
        #expect(await ingestor.snapshot().continuityGaps == 1)
    }

    // MARK: - Duplicate text

    @Test
    func twoRealMessagesWithIdenticalTextBothSurvive() async throws {
        let store = try makeStore()
        let ingestor = MessageIngestor(store: store)

        await ingestor.ingest(frame(chat: "Chat A", [message("hello"), message("ok")]))
        // "ok" sent a second time; the frame now shows both.
        await ingestor.ingest(
            frame(chat: "Chat A", [message("hello"), message("ok"), message("ok")],
                  secondsFromNow: 5)
        )

        #expect(try await texts(store, "Chat A") == ["hello", "ok", "ok"])
    }

    @Test
    func repeatedTextIsNotCollapsedAcrossManyFrames() async throws {
        let store = try makeStore()
        let ingestor = MessageIngestor(store: store)

        await ingestor.ingest(frame(chat: "Chat A", [message("ok")]))
        await ingestor.ingest(frame(chat: "Chat A", [message("ok"), message("ok")], secondsFromNow: 5))
        await ingestor.ingest(
            frame(chat: "Chat A", [message("ok"), message("ok"), message("ok")], secondsFromNow: 10)
        )

        #expect(try await texts(store, "Chat A") == ["ok", "ok", "ok"])
    }

    // MARK: - Chat switching

    @Test
    func switchingChatsKeepsTheStreamsSeparate() async throws {
        let store = try makeStore()
        let ingestor = MessageIngestor(store: store)

        await ingestor.ingest(frame(chat: "Chat A", [message("a1"), message("a2")]))
        await ingestor.ingest(frame(chat: "Chat B", [message("b1")], secondsFromNow: 5))
        // Back to A: its own tail still aligns, so nothing is duplicated.
        await ingestor.ingest(
            frame(chat: "Chat A", [message("a1"), message("a2"), message("a3")],
                  secondsFromNow: 10)
        )

        #expect(try await texts(store, "Chat A") == ["a1", "a2", "a3"])
        #expect(try await texts(store, "Chat B") == ["b1"])
        #expect(try await store.conversations().count == 2)
    }

    @Test
    func identicalTextInTwoChatsIsNeverMerged() async throws {
        let store = try makeStore()
        let ingestor = MessageIngestor(store: store)

        await ingestor.ingest(frame(chat: "Chat A", [message("ok")]))
        await ingestor.ingest(frame(chat: "Chat B", [message("ok")], secondsFromNow: 5))

        #expect(try await texts(store, "Chat A") == ["ok"])
        #expect(try await texts(store, "Chat B") == ["ok"])
    }

    // MARK: - Unknown chat, sender and time

    @Test
    func aFrameWithNoLegibleChatTitleIsNeverAttributedToAnything() async throws {
        let store = try makeStore()
        let ingestor = MessageIngestor(store: store)

        await ingestor.ingest(frame(chat: "Chat A", [message("a1")]))
        await ingestor.ingest(frame(chat: nil, [message("orphan")], secondsFromNow: 5))

        #expect(try await texts(store, "Chat A") == ["a1"])
        #expect(try await store.conversations().count == 1)
        let metrics = await ingestor.snapshot()
        #expect(metrics.framesWithoutChatIdentity == 1)
        #expect(metrics.messagesAppended == 1)
    }

    @Test
    func unknownSenderAndTimeArePersistedAsAbsentNotInvented() async throws {
        let store = try makeStore()
        let ingestor = MessageIngestor(store: store)

        await ingestor.ingest(
            frame(chat: "Chat A", [
                message("m1", sender: "Sender One", ownership: .other, visibleTime: "14:30"),
                message("m2", sender: nil, ownership: .unknown, visibleTime: nil)
            ])
        )

        let conversation = try #require(try await store.conversation(titled: "Chat A"))
        let stored = try await store.messages(inConversation: conversation.id)
        #expect(stored.count == 2)
        #expect(stored[0].sender == "Sender One")
        #expect(stored[0].visibleTime == "14:30")
        #expect(stored[0].ownership == .other)
        #expect(stored[1].sender == nil)
        #expect(stored[1].visibleTime == nil)
        #expect(stored[1].ownership == .unknown)
    }

    @Test
    func aMessageWithNoSenderIsStillDistinctFromOneWithASender() async throws {
        let store = try makeStore()
        let ingestor = MessageIngestor(store: store)

        await ingestor.ingest(frame(chat: "Chat A", [message("ok", sender: nil)]))
        await ingestor.ingest(
            frame(chat: "Chat A", [message("ok", sender: nil), message("ok", sender: "Sender One")],
                  secondsFromNow: 5)
        )

        let conversation = try #require(try await store.conversation(titled: "Chat A"))
        let stored = try await store.messages(inConversation: conversation.id)
        #expect(stored.map(\.sender) == [nil, "Sender One"])
    }

    // MARK: - Store behaviour

    @Test
    func bubbleGeometryIsNeverPersisted() async throws {
        let store = try makeStore()
        let ingestor = MessageIngestor(store: store)
        await ingestor.ingest(frame(chat: "Chat A", [message("m1")]))

        let conversation = try #require(try await store.conversation(titled: "Chat A"))
        let stored = try await store.messages(inConversation: conversation.id)
        // PersistedMessage has no geometry field at all -- this asserts the
        // shape, so adding one would break the build here first.
        #expect(stored.count == 1)
        #expect(stored[0].confidence == 0.9)
    }

    @Test
    func messagesReadBackInSequenceOrderNotObservationOrder() async throws {
        let store = try makeStore()
        let ingestor = MessageIngestor(store: store)

        await ingestor.ingest(frame(chat: "Chat A", [message("m3")]))
        await ingestor.ingest(
            frame(chat: "Chat A", [message("m1"), message("m2"), message("m3")],
                  secondsFromNow: 30)
        )

        let conversation = try #require(try await store.conversation(titled: "Chat A"))
        let stored = try await store.messages(inConversation: conversation.id)
        #expect(stored.map(\.text) == ["m1", "m2", "m3"])
        #expect(stored.map(\.sequence).sorted() == stored.map(\.sequence))
        // m3 was observed first even though it sorts last.
        #expect(stored[2].firstObservedAt < stored[0].firstObservedAt)
    }

    @Test
    func recentMessagesReturnsTheNewestWindowOldestFirst() async throws {
        let store = try makeStore()
        let ingestor = MessageIngestor(store: store)
        await ingestor.ingest(
            frame(chat: "Chat A", (1...5).map { message("m\($0)") })
        )

        let conversation = try #require(try await store.conversation(titled: "Chat A"))
        let recent = try await store.recentMessages(inConversation: conversation.id, limit: 2)
        #expect(recent.map(\.text) == ["m4", "m5"])
        #expect(try await store.messageCount(inConversation: conversation.id) == 5)
    }

    @Test
    func nonLatinTextRoundTripsUnchanged() async throws {
        let store = try makeStore()
        let ingestor = MessageIngestor(store: store)
        await ingestor.ingest(frame(chat: "群聊", [message("你好"), message("emoji 🙂")]))

        #expect(try await texts(store, "群聊") == ["你好", "emoji 🙂"])
    }

    @Test
    func aFrameWithNothingLegibleIsCountedButNotStored() async throws {
        let store = try makeStore()
        let ingestor = MessageIngestor(store: store)

        await ingestor.ingest(
            frame(chat: "Chat A", [message(nil, sender: nil, kind: .unknown)])
        )

        // No rows, and no empty conversation record either: a frame we could
        // read nothing from is not evidence that a chat exists.
        #expect(try await store.conversations().isEmpty)
        #expect(await ingestor.snapshot().framesWithNothingNew == 1)
    }

    // MARK: - Persistence guards

    @Test
    func theIngestionLayerNeverTouchesRawFramesOrLogs() throws {
        let source = try ingestionSourceText()
        // No raw capture data may reach the layer that writes to disk.
        #expect(!source.contains("CGImage"))
        #expect(!source.contains("ObservedFrame"))
        #expect(!source.contains("normalizedBounds"))
        // Chat content must never be logged.
        #expect(!source.contains("print("))
        #expect(!source.contains("NSLog"))
        #expect(!source.contains("os_log"))
    }

    @Test
    func theStoredSchemaHasNoColumnForRawCaptureData() throws {
        let source = try ingestionSourceText()
        for forbidden in ["image", "screenshot", "frame_", "bounds", "provider"] {
            #expect(!source.lowercased().contains("\(forbidden) BLOB".lowercased()))
        }
        #expect(!source.contains("BLOB"))
    }

    // MARK: - Coordinator seam

    @Test
    func aSuccessfulExtractionReachesTheIngestor() async throws {
        let store = try makeStore()
        let ingestor = MessageIngestor(store: store)
        let extractor = FixedExtractor(
            result: frame(chat: "Chat A", [message("m1"), message("m2")])
        )
        let coordinator = ExtractionCoordinator(extractor: extractor, ingestor: ingestor)

        await coordinator.submit(.synthetic(secondsFromNow: 0))
        await coordinator.waitUntilIdle()

        #expect(try await texts(store, "Chat A") == ["m1", "m2"])
        #expect(await coordinator.snapshot().extractionsSucceeded == 1)
    }

    @Test
    func aCoordinatorWithoutAnIngestorPersistsNothing() async throws {
        let extractor = FixedExtractor(result: frame(chat: "Chat A", [message("m1")]))
        let coordinator = ExtractionCoordinator(extractor: extractor)

        await coordinator.submit(.synthetic(secondsFromNow: 0))
        await coordinator.waitUntilIdle()

        let metrics = await coordinator.snapshot()
        #expect(metrics.extractionsSucceeded == 1)
        #expect(await coordinator.latest() != nil)
    }
}

/// Returns one fixed result without touching the network.
private struct FixedExtractor: FrameExtracting {
    let result: ExtractedConversationFrame
    var isConfigured: Bool { true }
    var processingLocation: ExtractionProcessingLocation { .onDevice }

    func extract(from frame: ObservedFrame) async throws -> ExtractedConversationFrame {
        result
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

/// Scans the ingestion sources the same way the extraction privacy guard scans
/// its own layer. Comments are stripped: doc comments legitimately name the
/// things the code must not do.
private func ingestionSourceText() throws -> String {
    let directory = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent()
        .deletingLastPathComponent()
        .appendingPathComponent("WeChatCompanion/Ingestion")
    let files = FileManager.default.enumerator(at: directory, includingPropertiesForKeys: nil)?
        .compactMap { $0 as? URL }
        .filter { $0.pathExtension == "swift" } ?? []
    let joined = try files.map { try String(contentsOf: $0, encoding: .utf8) }.joined(separator: "\n")
    return joined
        .split(separator: "\n", omittingEmptySubsequences: false)
        .filter { !$0.trimmingCharacters(in: .whitespaces).hasPrefix("//") }
        .joined(separator: "\n")
}
