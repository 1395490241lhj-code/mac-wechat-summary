import Foundation

/// App-owned Memory sync and freshness state (M2.2c).
///
/// Memory is the local, source-neutral store that the read-only memory MCP
/// serves to an agent. Putting messages into it is an explicit, foreground,
/// user-owned action -- never a scheduler, never an agent, never a tool. This
/// file holds the state machine the app owns for that action, the seam through
/// which the action is executed, and the freshness summary the app shows.
///
/// **Packaging boundary, stated plainly.** The memory layer is Python, frozen
/// into the signed `MemoryWorker.app` helper shipped inside the macOS app.
/// Swift talks to that helper through a one-request JSON protocol; it never
/// imports Python modules or opens the Memory SQLite store itself. Tests inject
/// inert/fake runners so a test host can never read or mutate the user's store.

/// The message source Memory can read. The app can explicitly offer its own
/// visual-capture and imported-archive stores; an external database reader is
/// operator-side only and is never selected silently by the app.
enum MemorySource: String, Equatable, Sendable {
    case visual
    case archive
    case database

    static let appSelectable: [MemorySource] = [.visual, .archive]

    var label: String {
        switch self {
        case .visual: return "Visual capture store"
        case .archive: return "Imported WeChat archives"
        case .database: return "External database reader"
        }
    }
}

/// Freshness as the memory layer reports it. Three different questions, kept
/// apart: when memory last synced, through what moment the source was
/// observed, and when the newest stored message is from. There is no
/// "is fresh" flag and no staleness threshold on purpose.
struct MemoryFreshnessSummary: Equatable, Sendable {
    var source: MemorySource
    var lastSuccessfulSync: Date?
    var observedThrough: Date?
    var completeThrough: Date?
    var latestMessageAt: Date?
    /// Fixed token from the memory layer: `succeeded`, `failed`, or `never`.
    var lastRunState: String
    /// Fixed failure token when the last run failed; never prose.
    var lastRunFailure: String?
    /// Fixed coverage token summarising the source's newest complete window:
    /// `complete`, `partial`, `unavailable`, or `none`.
    var coverageSummary: String
}

struct MemorySyncCounts: Equatable, Sendable {
    var conversationsSeen: Int
    var messagesSeen: Int
    var messagesInserted: Int
    var messagesUpdated: Int
}

/// Why a sync did not happen or did not finish. Fixed cases; the associated
/// values are the memory layer's fixed state tokens, never message content,
/// a path or an exception.
enum MemorySyncFailure: Error, Equatable, Sendable {
    case consentWithheld
    case runnerUnavailable
    case sourceUnavailable(state: String)
    case ingestionFailed(state: String)

    var message: String {
        switch self {
        case .consentWithheld:
            return "Local message storage is off, so Memory cannot be written."
        case .runnerUnavailable:
            return "Memory sync runs from the operator command line in this build; "
                + "the app cannot execute the memory layer."
        case .sourceUnavailable(let state):
            return "The selected message source is not available (\(state)). "
                + "No other source was used instead."
        case .ingestionFailed(let state):
            return "Memory sync failed (\(state)). The last successful state is kept."
        }
    }
}

enum MemorySyncOutcome: Equatable, Sendable {
    case succeeded(MemorySyncCounts, MemoryFreshnessSummary?)
    case failed(MemorySyncFailure)
}

enum MemorySyncPhase: Equatable, Sendable {
    case idle
    case running
    case succeeded(MemorySyncCounts)
    case failed(MemorySyncFailure)

    var isRunning: Bool { self == .running }
}

/// The seam through which the app executes a sync and reads freshness.
/// Injected; the default is the honest "unavailable" runner.
protocol MemorySyncRunning: Sendable {
    func sync(source: MemorySource) async -> MemorySyncOutcome
    func freshness(source: MemorySource) async -> MemoryFreshnessSummary?
}

/// The production runner for this build. It runs nothing and reads nothing:
/// the app has no runtime for the memory layer, and says so.
struct UnavailableMemorySyncRunner: MemorySyncRunning {
    func sync(source: MemorySource) async -> MemorySyncOutcome {
        .failed(.runnerUnavailable)
    }

    func freshness(source: MemorySource) async -> MemoryFreshnessSummary? {
        nil
    }
}


// MARK: - Daily Summary read model

enum DailySummaryWindow: String, CaseIterable, Identifiable, Equatable, Sendable {
    case today
    case yesterday
    case last24Hours

    var id: Self { self }

    var label: String {
        switch self {
        case .today: "Today"
        case .yesterday: "Yesterday"
        case .last24Hours: "Last 24 Hours"
        }
    }

    func bounds(now: Date, calendar: Calendar = .current) -> (start: Date, end: Date) {
        switch self {
        case .today:
            return (calendar.startOfDay(for: now), now)
        case .yesterday:
            let today = calendar.startOfDay(for: now)
            return (calendar.date(byAdding: .day, value: -1, to: today) ?? today, today)
        case .last24Hours:
            return (now.addingTimeInterval(-24 * 60 * 60), now)
        }
    }
}

struct DailySummaryCoverage: Equatable, Sendable {
    let status: String
    let trustworthyEmpty: Bool
    let caveats: [String]
}

struct DailySummaryConversation: Identifiable, Equatable, Sendable {
    let id: Int
    let label: String
    let messageCount: Int
    let senderCount: Int
    let firstAt: Date
    let lastAt: Date
}

struct DailySummarySenderCount: Identifiable, Equatable, Sendable {
    var id: String { sender }
    let sender: String
    let count: Int
}

struct DailySummaryMessage: Identifiable, Equatable, Sendable {
    let id: Int
    let conversationIndex: Int
    let source: MemorySource
    let timestamp: Date
    let timestampKind: String
    let sender: String?
    let kind: String
    let text: String
    let textTruncated: Bool
}

struct DailySummarySnapshot: Equatable, Sendable {
    let source: MemorySource
    let start: Date
    let end: Date
    let returnedMessages: Int
    let returnedConversations: Int
    let returnedSenders: Int
    let textTruncatedCount: Int
    let truncated: Bool
    let coverage: DailySummaryCoverage
    let freshness: MemoryFreshnessSummary?
    let conversations: [DailySummaryConversation]
    let senders: [DailySummarySenderCount]
    let messages: [DailySummaryMessage]
}

enum DailySummaryFailure: Error, Equatable, Sendable {
    case consentWithheld
    case runnerUnavailable
    case memoryUnavailable(state: String)
    case workerFailed(state: String)

    var message: String {
        switch self {
        case .consentWithheld:
            "Local message storage is off. Daily Summary cannot read Memory."
        case .runnerUnavailable:
            "The bundled Memory worker is unavailable in this build."
        case .memoryUnavailable(let state):
            "Memory is not available for this summary (\(state)). Sync the selected source first."
        case .workerFailed(let state):
            "Daily Summary could not prepare its local evidence (\(state))."
        }
    }
}

enum DailySummaryOutcome: Equatable, Sendable {
    case ready(DailySummarySnapshot)
    case failed(DailySummaryFailure)
}

enum DailySummaryPhase: Equatable, Sendable {
    case idle
    case running
    case ready
    case failed(DailySummaryFailure)

    var isRunning: Bool { self == .running }
}

protocol DailySummaryRunning: Sendable {
    func prepare(
        source: MemorySource,
        start: Date,
        end: Date,
        messageLimit: Int
    ) async -> DailySummaryOutcome
}

struct UnavailableDailySummaryRunner: DailySummaryRunning {
    func prepare(
        source: MemorySource,
        start: Date,
        end: Date,
        messageLimit: Int
    ) async -> DailySummaryOutcome {
        .failed(.runnerUnavailable)
    }
}
