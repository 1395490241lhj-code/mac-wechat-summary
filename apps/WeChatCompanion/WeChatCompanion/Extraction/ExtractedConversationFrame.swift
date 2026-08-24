import CoreGraphics
import Foundation

/// Whether a visible bubble belongs to the local user or the other party.
/// `unknown` is a first-class outcome: ownership is a visual inference and the
/// extractor must never guess.
enum MessageOwnership: String, Sendable, Equatable {
    case own
    case other
    case unknown
}

/// What a visible bubble appears to be. `unknown` is representable so an
/// extractor can report "there is a bubble here" without classifying it.
enum VisibleMessageKind: String, Sendable, Equatable {
    case text
    case image
    case file
    case link
    case voice
    case system
    case unknown
}

/// The chat a frame appears to show. Optional throughout: a frame may not
/// contain a legible chat title at all.
struct ExtractedChatIdentity: Sendable, Equatable {
    let title: String
    let confidence: Double
}

/// One visible bubble. Every field an extractor cannot actually see is
/// optional, so absence is recorded as absence rather than invented.
struct ExtractedVisibleMessage: Sendable, Equatable {
    let sender: String?
    let ownership: MessageOwnership
    let visibleTime: String?
    let text: String?
    let kind: VisibleMessageKind
    let confidence: Double
    /// Bubble position in unit coordinates, so bounds carry no pixel data.
    let normalizedBounds: CGRect?

    init(
        sender: String? = nil,
        ownership: MessageOwnership = .unknown,
        visibleTime: String? = nil,
        text: String? = nil,
        kind: VisibleMessageKind = .unknown,
        confidence: Double = 0,
        normalizedBounds: CGRect? = nil
    ) {
        self.sender = sender
        self.ownership = ownership
        self.visibleTime = visibleTime
        self.text = text
        self.kind = kind
        self.confidence = confidence
        self.normalizedBounds = normalizedBounds
    }
}

/// Provider-independent result of extracting one observed frame.
///
/// Deliberately NOT Codable in this phase: extracted conversation content stays
/// in memory only, and the absence of a Codable conformance makes accidental
/// persistence a compile error rather than a review question. It also carries
/// no image and no provider identifiers.
struct ExtractedConversationFrame: Sendable, Equatable {
    let capturedAt: Date
    let chat: ExtractedChatIdentity?
    let messages: [ExtractedVisibleMessage]

    init(
        capturedAt: Date,
        chat: ExtractedChatIdentity? = nil,
        messages: [ExtractedVisibleMessage] = []
    ) {
        self.capturedAt = capturedAt
        self.chat = chat
        self.messages = messages
    }

    /// A frame the extractor could read nothing usable from. Distinct from a
    /// failure: the extractor ran and honestly reported no visible content.
    static func empty(capturedAt: Date) -> Self {
        Self(capturedAt: capturedAt)
    }
}
