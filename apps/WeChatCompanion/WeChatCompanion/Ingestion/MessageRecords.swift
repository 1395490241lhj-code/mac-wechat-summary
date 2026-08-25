import Foundation

/// A chat we have persisted at least one message for.
///
/// Identified by its visible title. A frame whose chat title was not legible is
/// never attributed to a conversation -- see `MessageIngestor` -- so there is no
/// "unknown" conversation that unrelated chats could silently merge into.
struct ConversationRecord: Sendable, Equatable, Identifiable {
    let id: Int64
    let title: String
    let firstSeenAt: Date
    let lastSeenAt: Date
}

/// One message-level row, reconciled from one or more overlapping frames.
///
/// This is the ONLY shape of extracted content that reaches disk. It carries no
/// image, no frame, and no bubble geometry: `normalizedBounds` is deliberately
/// dropped during ingestion because it describes a pixel layout that moves with
/// every scroll and means nothing once the frame is gone.
struct PersistedMessage: Sendable, Equatable, Identifiable {
    let id: Int64
    let conversationID: Int64
    /// Signed, monotonic within a conversation. Appends take `max + 1` and
    /// backfilled history takes `min - 1`, so revealing older messages never
    /// rewrites the rows already stored.
    let sequence: Int64
    let sender: String?
    let ownership: MessageOwnership
    /// The time string as WeChat displayed it, or nil. Never parsed into a
    /// Date: "昨天 14:30" is not a timestamp and guessing one would be a lie.
    let visibleTime: String?
    let text: String?
    let kind: VisibleMessageKind
    let confidence: Double
    /// When we first saw this message on screen. Capture metadata, NOT the time
    /// the message was actually sent.
    let firstObservedAt: Date
}

/// The fields that decide whether two observed bubbles are the same message.
///
/// Confidence and bounds are excluded on purpose: the same bubble seen in two
/// frames gets different geometry and may get a different confidence, and
/// including either would break every overlap match.
///
/// Note this is an *identity* key, never a uniqueness key. Two genuinely
/// distinct messages with the same text produce the same key, and the
/// reconciler distinguishes them by position in the sequence, not by hashing.
struct MessageIdentityKey: Sendable, Equatable, Hashable {
    let sender: String?
    let ownership: MessageOwnership
    let visibleTime: String?
    let text: String?
    let kind: VisibleMessageKind

    init(sender: String?, ownership: MessageOwnership, visibleTime: String?, text: String?, kind: VisibleMessageKind) {
        self.sender = sender
        self.ownership = ownership
        self.visibleTime = visibleTime
        self.text = text
        self.kind = kind
    }

    init(_ message: ExtractedVisibleMessage) {
        self.init(
            sender: message.sender,
            ownership: message.ownership,
            visibleTime: message.visibleTime,
            text: message.text,
            kind: message.kind
        )
    }

    init(_ message: PersistedMessage) {
        self.init(
            sender: message.sender,
            ownership: message.ownership,
            visibleTime: message.visibleTime,
            text: message.text,
            kind: message.kind
        )
    }
}
