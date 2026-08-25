import Foundation

/// Aggregate-only ingestion telemetry. As with `ExtractionMetrics`, there is
/// deliberately no field that could hold a chat title or message text.
struct IngestionMetrics: Codable, Equatable, Sendable {
    var framesIngested = 0
    /// Frames whose chat title was not legible. These are never persisted --
    /// attributing them to the last known chat would be a guess, and giving
    /// them a shared "unknown" conversation would merge unrelated chats.
    var framesWithoutChatIdentity = 0
    /// Frames that showed nothing beyond what was already stored. The steady
    /// state for a chat sitting still, and the main thing overlap dedup buys.
    var framesWithNothingNew = 0
    var messagesAppended = 0
    /// Older messages recovered by scrolling up.
    var messagesPrepended = 0
    /// Frames that shared no run with stored history at either end. The
    /// messages were kept, but they do not continue the stored tail.
    var continuityGaps = 0
    /// Store write failures. Aggregate count only; no error is retained.
    var persistenceFailures = 0
    var lastIngestedAt: Date?
    /// Messages removed by the retention policy. Aggregate count only.
    var messagesExpired = 0
    var lastRetentionSweepAt: Date?
}

/// The seam the extraction coordinator hands successful frames to.
///
/// A protocol so the coordinator has no idea a database exists, and so tests
/// can observe ingestion without one.
protocol MessageIngesting: Sendable {
    func ingest(_ frame: ExtractedConversationFrame) async
}

/// Turns the stream of overlapping `ExtractedConversationFrame`s into
/// message-level rows.
///
/// The frames themselves stay transient: this type reads one, writes the
/// messages it did not already have, and lets it go. Nothing here retains a
/// frame, an image, or a provider response.
actor MessageIngestor: MessageIngesting {
    /// How often an ingesting app re-checks for expired messages. Retention is
    /// a housekeeping bound, not a real-time guarantee, so sweeping on every
    /// frame would be pure waste.
    static let sweepInterval: TimeInterval = 3_600

    private let store: MessageStore
    private var retention: RetentionPolicy
    private var lastSweepAt: Date?
    private var metrics = IngestionMetrics()

    init(store: MessageStore, retention: RetentionPolicy = .defaultPolicy) {
        self.store = store
        self.retention = retention
    }

    func snapshot() -> IngestionMetrics { metrics }

    /// Applies a new policy and sweeps immediately, so tightening retention
    /// takes effect when the user chooses it rather than up to an hour later.
    func setRetention(_ policy: RetentionPolicy) async {
        retention = policy
        await sweep(now: Date())
    }

    /// Run at enable time so a policy that tightened while the app was closed
    /// is applied before anything new is written.
    func sweepNow(_ now: Date = Date()) async {
        await sweep(now: now)
    }

    private func sweepIfDue(now: Date) async {
        guard retention.maximumAge != nil else { return }
        if let lastSweepAt, now.timeIntervalSince(lastSweepAt) < Self.sweepInterval {
            return
        }
        await sweep(now: now)
    }

    private func sweep(now: Date) async {
        lastSweepAt = now
        metrics.lastRetentionSweepAt = now
        do {
            metrics.messagesExpired += try await store.applyRetention(retention, now: now)
        } catch {
            // Aggregate only; the error can reference the statement.
            metrics.persistenceFailures += 1
        }
    }

    func ingest(_ frame: ExtractedConversationFrame) async {
        metrics.framesIngested += 1
        await sweepIfDue(now: Date())

        // No legible chat title means we do not know which conversation this
        // belongs to, and there is no honest way to find out. Counted, dropped.
        guard let title = frame.chat?.title else {
            metrics.framesWithoutChatIdentity += 1
            return
        }

        // Bubbles carrying nothing at all -- no text, no classified kind, no
        // sender -- are not messages, and they would make every frame's key
        // sequence align with every other's.
        let visible = frame.messages.filter(Self.isMeaningful)
        guard !visible.isEmpty else {
            metrics.framesWithNothingNew += 1
            return
        }

        do {
            let conversationID = try await store.conversationID(
                forTitle: title, seenAt: frame.capturedAt
            )
            let tail = try await store.reconciliationTail(conversationID: conversationID)
            let head = try await store.headKeys(
                conversationID: conversationID, limit: MessageStore.reconciliationWindow
            )
            let reconciliation = FrameReconciler.reconcile(
                incoming: visible.map(MessageIdentityKey.init),
                storedTail: tail,
                storedHead: head
            )

            switch reconciliation {
            case .nothingNew:
                metrics.framesWithNothingNew += 1
            case let .appended(_, range):
                try await store.append(
                    Array(visible[range]),
                    conversationID: conversationID,
                    observedAt: frame.capturedAt
                )
                metrics.messagesAppended += range.count
            case let .gap(range):
                try await store.append(
                    Array(visible[range]),
                    conversationID: conversationID,
                    observedAt: frame.capturedAt
                )
                metrics.messagesAppended += range.count
                metrics.continuityGaps += 1
            case let .prepended(_, range):
                try await store.prepend(
                    Array(visible[range]),
                    conversationID: conversationID,
                    observedAt: frame.capturedAt
                )
                metrics.messagesPrepended += range.count
            }
            metrics.lastIngestedAt = Date()
        } catch {
            // Aggregate only. The error can reference the statement, so it is
            // never stored or logged.
            metrics.persistenceFailures += 1
        }
    }

    /// A bubble is worth storing if the extractor read *something* from it.
    private static func isMeaningful(_ message: ExtractedVisibleMessage) -> Bool {
        if message.text?.isEmpty == false { return true }
        if message.sender?.isEmpty == false { return true }
        return message.kind != .unknown
    }
}
