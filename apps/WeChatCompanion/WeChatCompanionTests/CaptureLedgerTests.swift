import Foundation
import Testing
@testable import WeChatCompanion

/// V-1 Capture Ledger.
///
/// Every fixture here is synthetic. No real WeChat content is used anywhere in
/// this suite, and the assertions about "no message text" are written against
/// strings that could only contain text if the ledger had leaked it.

private func date(_ offset: TimeInterval) -> Date {
    Date(timeIntervalSince1970: 1_757_000_000 + offset)
}

private func conversation(
    id: Int64,
    title: String,
    count: Int,
    first: TimeInterval,
    last: TimeInterval
) -> CapturedConversationSummary {
    CapturedConversationSummary(
        id: id,
        title: title,
        retainedMessageCount: count,
        firstCapturedAt: date(first),
        lastCapturedAt: date(last)
    )
}

/// Everything the user can actually read in this section, flattened.
private func userFacingStrings(_ shown: CaptureLedgerPresentation) -> [String] {
    var strings: [String] = []
    if let message = shown.emptyMessage { strings.append(message) }
    if let note = shown.retentionNote { strings.append(note) }
    strings += shown.guidance
    for row in shown.conversations {
        strings += [row.title, row.retainedCount, row.firstCaptured, row.lastCaptured]
    }
    for row in shown.healthRows {
        strings += [row.label, row.value]
    }
    return strings
}

struct CaptureLedgerTests {
    // MARK: - Conversation listing

    @Test
    func listsEveryConversationWithItsRetainedCountAndTimes() {
        let ledger = CaptureLedger(
            storeState: .ready,
            conversations: [
                conversation(id: 2, title: "Team Group", count: 42, first: 0, last: 900),
                conversation(id: 1, title: "Ada", count: 1, first: 100, last: 300),
            ]
        )
        let shown = CaptureLedgerPresentation(ledger: ledger)

        #expect(shown.emptyMessage == nil)
        #expect(shown.conversations.count == 2)
        #expect(shown.conversations[0].title == "Team Group")
        #expect(shown.conversations[0].retainedCount == "42 messages kept")
        // Singular is not "1 messages kept".
        #expect(shown.conversations[1].retainedCount == "1 message kept")
        #expect(shown.conversations[0].firstCaptured == date(0).formatted(
            date: .abbreviated, time: .shortened
        ))
        #expect(shown.conversations[0].lastCaptured == date(900).formatted(
            date: .abbreviated, time: .shortened
        ))
    }

    @Test
    func conversationOrderIsPreservedNewestLastCapturedFirst() {
        // The store orders by last_seen_at DESC; the presentation must not
        // re-sort it into, say, alphabetical order.
        let ledger = CaptureLedger(
            storeState: .ready,
            conversations: [
                conversation(id: 3, title: "Zoe", count: 5, first: 0, last: 900),
                conversation(id: 2, title: "Ada", count: 5, first: 0, last: 500),
                conversation(id: 1, title: "Ben", count: 5, first: 0, last: 100),
            ]
        )
        let shown = CaptureLedgerPresentation(ledger: ledger)
        #expect(shown.conversations.map(\.title) == ["Zoe", "Ada", "Ben"])
    }

    // MARK: - Empty and off states

    @Test
    func persistenceOffIsDistinctFromNothingCapturedYet() {
        let off = CaptureLedgerPresentation(ledger: CaptureLedger(storeState: .disabled))
        let onButEmpty = CaptureLedgerPresentation(ledger: CaptureLedger(storeState: .ready))

        #expect(off.emptyMessage != nil)
        #expect(onButEmpty.emptyMessage != nil)
        #expect(off.emptyMessage != onButEmpty.emptyMessage)
        // The off state names its own fix.
        #expect(off.emptyMessage?.contains("Settings") == true)
        #expect(onButEmpty.emptyMessage?.contains("No conversations captured yet") == true)
    }

    @Test
    func anUnreadableStoreIsItsOwnState() {
        let shown = CaptureLedgerPresentation(ledger: CaptureLedger(storeState: .unavailable))
        #expect(shown.emptyMessage?.contains("could not") == true)
        #expect(shown.conversations.isEmpty)
    }

    @Test
    func healthIsHiddenEntirelyWhilePersistenceIsOff() {
        // Nothing is being kept, so counters about keeping it are noise.
        let shown = CaptureLedgerPresentation(ledger: CaptureLedger(storeState: .disabled))
        #expect(shown.healthRows.isEmpty)
        #expect(shown.guidance.isEmpty)
    }

    // MARK: - Capture health

    @Test
    func titleLessFramesBecomeVisibleWithGuidance() {
        var health = IngestionMetrics()
        health.framesWithoutChatIdentity = 3
        let shown = CaptureLedgerPresentation(
            ledger: CaptureLedger(storeState: .ready, health: health)
        )

        let row = shown.healthRows.first { $0.label.contains("chat name not readable") }
        #expect(row?.value == "3")
        #expect(shown.guidance.contains { $0.contains("chat name") })
    }

    @Test
    func continuityGapsBecomeVisibleWithScrollGuidance() {
        var health = IngestionMetrics()
        health.continuityGaps = 2
        let shown = CaptureLedgerPresentation(
            ledger: CaptureLedger(storeState: .ready, health: health)
        )

        let row = shown.healthRows.first { $0.label.contains("Gaps in captured history") }
        #expect(row?.value == "2")
        let advice = shown.guidance.first { $0.contains("Scrolling upward") }
        #expect(advice != nil)
        // The guidance must offer no automation, and must say so.
        #expect(advice?.contains("never scrolls for you") == true)
    }

    @Test
    func guidanceAppearsOnlyWhenTheMatchingProblemOccurred() {
        let clean = CaptureLedgerPresentation(ledger: CaptureLedger(storeState: .ready))
        #expect(clean.guidance.isEmpty)
    }

    @Test
    func savingFailuresAreShownOnlyWhenTheyHappened() {
        let clean = CaptureLedgerPresentation(ledger: CaptureLedger(storeState: .ready))
        #expect(!clean.healthRows.contains { $0.label.contains("saving failed") })

        var health = IngestionMetrics()
        health.persistenceFailures = 1
        let failing = CaptureLedgerPresentation(
            ledger: CaptureLedger(storeState: .ready, health: health)
        )
        #expect(failing.healthRows.contains { $0.label.contains("saving failed") })
    }

    @Test
    func capturedAndRecoveredMessagesAreBothReported() {
        var health = IngestionMetrics()
        health.messagesAppended = 12
        health.messagesPrepended = 7
        let shown = CaptureLedgerPresentation(
            ledger: CaptureLedger(storeState: .ready, health: health)
        )
        #expect(shown.healthRows.first { $0.label == "Messages captured" }?.value == "12")
        #expect(
            shown.healthRows.first { $0.label.contains("recovered while scrolling") }?.value == "7"
        )
    }

    // MARK: - Retention wording

    @Test
    func aPopulatedLedgerSaysTheCountsAreWhatIsCurrentlyKept() {
        let ledger = CaptureLedger(
            storeState: .ready,
            conversations: [conversation(id: 1, title: "Ada", count: 3, first: 0, last: 10)]
        )
        let note = CaptureLedgerPresentation(ledger: ledger).retentionNote
        #expect(note?.contains("currently kept") == true)
        #expect(note?.contains("not everything ever observed") == true)
    }

    // MARK: - Privacy and vocabulary

    @Test
    func noMessageTextCanReachTheLedger() {
        // The summary type has no text field at all, so the only strings the
        // ledger can show for a conversation are its title, a count and two
        // timestamps. This asserts the rendered strings against a body that
        // would be unmistakable if it leaked.
        let ledger = CaptureLedger(
            storeState: .ready,
            conversations: [
                conversation(id: 1, title: "Ada", count: 2, first: 0, last: 10),
            ]
        )
        let shown = CaptureLedgerPresentation(ledger: ledger)
        let rendered = userFacingStrings(shown).joined(separator: "\n")
        #expect(!rendered.contains("SECRET-MESSAGE-BODY"))
        for row in shown.conversations {
            #expect(row.title == "Ada")
            #expect(row.retainedCount == "2 messages kept")
        }
    }

    @Test
    func userFacingStringsNameNoInternalFieldOrProperty() {
        var health = IngestionMetrics()
        health.messagesAppended = 4
        health.messagesPrepended = 2
        health.framesWithoutChatIdentity = 1
        health.continuityGaps = 1
        health.persistenceFailures = 1
        let states: [LocalHistoryStoreState] = [.disabled, .ready, .unavailable]
        let forbidden = [
            "framesWithoutChatIdentity", "continuityGaps", "messagesAppended",
            "messagesPrepended", "persistenceFailures", "framesIngested",
            "framesWithNothingNew", "messagesExpired", "lastIngestedAt",
            "retainedMessageCount", "lastCapturedAt", "firstCapturedAt",
            "storeState", "IngestionMetrics", "CaptureLedger",
            "last_seen_at", "first_seen_at", "conversation_id", "first_observed_at",
            "message_count", "conversation_count", "logical_message_id",
            "schema_version", "user_version", "sqlite", "SELECT",
        ]

        for state in states {
            let ledger = CaptureLedger(
                storeState: state,
                conversations: state == .ready
                    ? [conversation(id: 1, title: "Ada", count: 2, first: 0, last: 10)]
                    : [],
                health: health
            )
            let rendered = userFacingStrings(CaptureLedgerPresentation(ledger: ledger))
                .joined(separator: "\n")
                .lowercased()
            for name in forbidden {
                #expect(
                    !rendered.contains(name.lowercased()),
                    "user-facing text leaked the internal name \(name) in state \(state)"
                )
            }
        }
    }
}

// MARK: - Against a real store

private func message(_ text: String) -> ExtractedVisibleMessage {
    ExtractedVisibleMessage(text: text, kind: .text, confidence: 0.9)
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

/// The counts and ordering the ledger shows come from SQL, so they are worth
/// verifying against a real (in-memory) database rather than a hand-built
/// fixture. Nothing here writes to disk.
struct CaptureLedgerStoreTests {
    @Test
    func summariesCarryPerConversationCountsNewestActivityFirst() async throws {
        let store = try MessageStore(url: nil)
        let ingestor = MessageIngestor(store: store)

        await ingestor.ingest(frame(chat: "Ada", [message("a1")], secondsFromNow: 0))
        await ingestor.ingest(
            frame(chat: "Team Group", [message("g1"), message("g2")], secondsFromNow: 60)
        )

        let summaries = try await store.conversationSummaries()
        #expect(summaries.count == 2)
        // Newest last-captured first.
        #expect(summaries[0].title == "Team Group")
        #expect(summaries[0].retainedMessageCount == 2)
        #expect(summaries[1].title == "Ada")
        #expect(summaries[1].retainedMessageCount == 1)
        #expect(summaries[0].firstCapturedAt <= summaries[0].lastCapturedAt)
    }

    @Test
    func aTitlelessFrameIsCountedAndCreatesNoConversation() async throws {
        let store = try MessageStore(url: nil)
        let ingestor = MessageIngestor(store: store)

        await ingestor.ingest(frame(chat: nil, [message("orphan")]))

        #expect(try await store.conversationSummaries().isEmpty)
        #expect(await ingestor.snapshot().framesWithoutChatIdentity == 1)
    }

    @Test
    func theLedgerReportsDisabledWithoutOpeningAStore() async {
        let history = LocalMessageHistory(url: nil)
        let ledger = await history.captureLedger()
        #expect(ledger.storeState == .disabled)
        #expect(ledger.conversations.isEmpty)
    }

    @Test
    func theLedgerReportsCapturedConversationsOnceConsentIsOn() async throws {
        let history = LocalMessageHistory(url: nil)
        await history.setEnabled(true)
        let ingestor = await history.ingestor()
        #expect(ingestor != nil)
        await ingestor?.ingest(frame(chat: "Ada", [message("a1"), message("a2")]))

        let ledger = await history.captureLedger()
        #expect(ledger.storeState == .ready)
        #expect(ledger.conversations.count == 1)
        #expect(ledger.conversations.first?.title == "Ada")
        #expect(ledger.conversations.first?.retainedMessageCount == 2)
        #expect(ledger.health.messagesAppended == 2)
    }
}
