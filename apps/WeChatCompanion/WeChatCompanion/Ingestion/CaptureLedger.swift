import Foundation

/// One captured conversation, as the ledger shows it to the user.
///
/// There is deliberately **no message-text field on this type**. The ledger
/// answers "which chats were captured, and how much of them is still kept",
/// and a type that cannot carry a message body makes leaking one into that
/// answer a compile error rather than a review question.
struct CapturedConversationSummary: Sendable, Equatable, Identifiable {
    let id: Int64
    let title: String
    /// Messages the store still holds for this chat -- what retention has
    /// *kept*, never a running total of everything ever observed.
    let retainedMessageCount: Int
    let firstCapturedAt: Date
    let lastCapturedAt: Date
}

/// What the Chats tab knows about captured history at one moment.
struct CaptureLedger: Sendable, Equatable {
    var storeState: LocalHistoryStoreState = .disabled
    /// Newest `lastCapturedAt` first.
    var conversations: [CapturedConversationSummary] = []
    var health = IngestionMetrics()

    static let empty = CaptureLedger()
}

/// Turns a ledger into the exact strings the Chats tab renders.
///
/// Kept separate from the view so the user-facing wording is testable without
/// rendering SwiftUI: the rules that matter here -- no message text, no
/// internal field names, guidance only when it applies -- are assertions about
/// these strings, not about pixels.
struct CaptureLedgerPresentation: Equatable {
    struct ConversationRow: Equatable, Identifiable {
        let id: Int64
        let title: String
        let retainedCount: String
        let firstCaptured: String
        let lastCaptured: String
    }

    struct HealthRow: Equatable, Identifiable {
        let label: String
        let value: String
        var id: String { label }
    }

    /// Set when there is nothing to list, and says *why* -- consent off and
    /// "nothing captured yet" are different situations with different fixes.
    let emptyMessage: String?
    let conversations: [ConversationRow]
    let healthRows: [HealthRow]
    /// Shown only when the matching problem actually occurred.
    let guidance: [String]
    /// Present whenever a list is shown, so the counts are never read as a
    /// complete history.
    let retentionNote: String?

    init(ledger: CaptureLedger) {
        let health = ledger.health

        switch ledger.storeState {
        case .disabled:
            emptyMessage = "Local message storage is off, so no conversation history is "
                + "being kept. Capture and extraction still run, but nothing is written "
                + "down. Turn on Local Message Storage in Settings to start a ledger."
        case .unavailable:
            emptyMessage = "Local message storage is on, but the message store could not "
                + "be opened, so nothing is being kept right now."
        case .ready:
            emptyMessage = ledger.conversations.isEmpty
                ? "No conversations captured yet. Share a WeChat window and keep the chat "
                    + "name at the top of the window visible -- captured conversations "
                    + "appear here."
                : nil
        }

        conversations = ledger.conversations.map { conversation in
            ConversationRow(
                id: conversation.id,
                title: conversation.title,
                retainedCount: Self.messageCountText(conversation.retainedMessageCount),
                firstCaptured: Self.timestamp(conversation.firstCapturedAt),
                lastCaptured: Self.timestamp(conversation.lastCapturedAt)
            )
        }

        // Only the numbers a user can act on. Frame-level throughput already
        // has its own section above and does not belong in a health summary.
        var rows: [HealthRow] = [
            HealthRow(label: "Messages captured", value: "\(health.messagesAppended)"),
            HealthRow(
                label: "Older messages recovered while scrolling",
                value: "\(health.messagesPrepended)"
            ),
            HealthRow(
                label: "Frames skipped — chat name not readable",
                value: "\(health.framesWithoutChatIdentity)"
            ),
            HealthRow(label: "Gaps in captured history", value: "\(health.continuityGaps)"),
        ]
        // A zero here is not news; a non-zero is.
        if health.persistenceFailures > 0 {
            rows.append(
                HealthRow(label: "Times saving failed", value: "\(health.persistenceFailures)")
            )
        }
        healthRows = ledger.storeState == .disabled ? [] : rows

        var advice: [String] = []
        if ledger.storeState != .disabled, health.framesWithoutChatIdentity > 0 {
            advice.append(
                "Some frames were skipped because the chat name at the top of the window "
                    + "was not readable. They are never guessed into another chat. Keep the "
                    + "chat name visible and unobstructed while capturing."
            )
        }
        if ledger.storeState != .disabled, health.continuityGaps > 0 {
            advice.append(
                "Some captured messages do not join up with what was already kept, so "
                    + "older history is missing in between. Scrolling upward in WeChat "
                    + "yourself can recover it. WeChat Companion never scrolls for you."
            )
        }
        guidance = advice

        retentionNote = ledger.conversations.isEmpty
            ? nil
            : "Counts show what is currently kept under your retention setting, not "
                + "everything ever observed. Older messages are removed automatically."
    }

    private static func messageCountText(_ count: Int) -> String {
        count == 1 ? "1 message kept" : "\(count) messages kept"
    }

    private static func timestamp(_ date: Date) -> String {
        date.formatted(date: .abbreviated, time: .shortened)
    }
}
