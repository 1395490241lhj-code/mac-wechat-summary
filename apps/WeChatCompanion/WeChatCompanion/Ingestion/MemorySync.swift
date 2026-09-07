import Foundation

/// App-owned Memory sync and freshness state (M2.2c).
///
/// Memory is the local, source-neutral store that the read-only memory MCP
/// serves to an agent. Putting messages into it is an explicit, foreground,
/// user-owned action -- never a scheduler, never an agent, never a tool. This
/// file holds the state machine the app owns for that action, the seam through
/// which the action is executed, and the freshness summary the app shows.
///
/// **Packaging boundary, stated plainly.** The memory layer is Python
/// (`memory/memory_sync.py`) and this app ships no Python runtime and no
/// bridge dependencies. Running the sync from inside the app would introduce
/// an unsupported runtime dependency, so the production runner does not
/// pretend to: `UnavailableMemorySyncRunner` reports exactly that, the UI
/// shows it as a refusal, and the state machine, consent gate and freshness
/// presentation are real and tested through the seam. Closing the gap is a
/// packaging decision, not something to fake here.

/// The message source the sync would read. The app can only offer the visual
/// store it fills itself; a database reader is selected and configured on the
/// operator side and is never chosen silently.
enum MemorySource: String, Equatable, Sendable {
    case visual
    case database

    var label: String {
        switch self {
        case .visual: return "Visual capture store"
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
enum MemorySyncFailure: Equatable, Sendable {
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
