import Foundation

/// Runs the memory sync through the worker bundled inside the app (M2.2d).
///
/// The memory layer is Python and this app ships no interpreter for a user to
/// install: the worker is frozen into a self-contained executable and copied
/// into the app bundle at build time. This runner starts it as a one-shot
/// child process, writes one JSON request, reads one JSON reply, and returns.
///
/// The narrow parts are deliberate:
///
/// * **Fixed path.** The executable is resolved relative to the app bundle and
///   nowhere else. There is no `PATH` lookup, so nothing a user has installed
///   can be run in its place, and no shell, so there is no command string to
///   get wrong.
/// * **Environment built from scratch.** `HOME` (so the worker can read the
///   app's own consent state), a fixed `PATH` for `defaults`, and a locale.
///   Nothing is inherited: the parent's variables cannot select a different
///   source or a different store behind the app's back.
/// * **Bounded.** The child is given a deadline and terminated if it passes
///   it, so a wedged worker cannot hold the action open.
/// * **Sanitized.** Failures are the worker's fixed state tokens or a fixed
///   local one. A path, a chat, or an interpreter traceback never becomes a
///   user-visible string.
/// One mutable bit shared with the watchdog queue.
final class Expiry: @unchecked Sendable {
    var fired = false
}

// MARK: - The evidence window

/// The seam through which the sealed evidence window is read.
///
/// Extracted from `PackagedMemorySyncRunner` so a caller that only needs an
/// answer's evidence is not also handed the sync, summary and follow-up seams.
/// The implementation is unchanged: the same packaged worker answers it.
protocol AnswerEvidenceRunning: Sendable {
    func answerEvidence(
        start: Date, end: Date, messageLimit: Int
    ) async -> AnswerEvidenceOutcome
}

/// The honest evidence window for a build without the packaged worker: it
/// reports the packaging gap rather than faking a window.
struct UnavailableAnswerEvidenceRunner: AnswerEvidenceRunning {
    func answerEvidence(
        start: Date, end: Date, messageLimit: Int
    ) async -> AnswerEvidenceOutcome {
        .failed(.workerFailed(state: "worker_unavailable"))
    }
}

/// One anchored Archive row. There is no anchorless form: a row that cannot
/// be named is not returned, because the window exists so a later answer can
/// cite a row that can be revealed back to exactly where it came from.
struct AnswerEvidenceRow: Equatable, Sendable {
    let canonicalMessageID: String
    let canonicalConversationID: String
    let timestamp: Date
    let timestampKind: String
    let sender: String?
    let text: String
    let textTruncated: Bool
    let archiveEvidence: ArchiveEvidenceAnchor
}

struct AnswerEvidenceSnapshot: Equatable, Sendable {
    let start: Date
    let end: Date
    let scannedMessages: Int
    let returnedEvidence: Int
    let excludedUnanchored: Int
    let textTruncatedCount: Int
    let truncated: Bool
    let coverage: FollowUpCoverage
    let freshness: MemoryFreshnessSummary?
    let rows: [AnswerEvidenceRow]
}

enum AnswerEvidenceFailure: Error, Equatable, Sendable {
    case consentWithheld
    case memoryUnavailable(state: String)
    case workerFailed(state: String)

    var message: String {
        switch self {
        case .consentWithheld:
            "Local message storage is off. Evidence cannot be read."
        case .memoryUnavailable(let state):
            "Memory is not available for an evidence window (\(state)). Sync the Archive first."
        case .workerFailed(let state):
            "The evidence window could not be prepared (\(state))."
        }
    }
}

enum AnswerEvidenceOutcome: Equatable, Sendable {
    case ready(AnswerEvidenceSnapshot)
    case failed(AnswerEvidenceFailure)
}

struct PackagedMemorySyncRunner: MemorySyncRunning, DailySummaryRunning, FollowUpCandidateRunning,
    AnswerEvidenceRunning
{
    /// Where the worker sits inside the bundle. It is a nested *bundle*, not a
    /// loose directory: codesign refuses to seal an app that contains an
    /// unsigned tree of plain files, and a helper .app carries its own seal.
    static let bundleSubpath = "Helpers/MemoryWorker.app/Contents/MacOS/MemoryWorker"

    let workerURL: URL
    let storeURL: URL
    let messageStoreURL: URL
    var timeout: TimeInterval = 120

    /// The kill-switch an answer's evidence read gets, which is deliberately
    /// *later* than the answer's own user-visible deadline.
    ///
    /// The two are not the same timer and must not share an instant. The
    /// answer deadline times the whole run -- evidence read and model call --
    /// and has to win, or a slow read would surface as a worker failure rather
    /// than as the timeout the person is waiting through. This one only exists
    /// to stop a wedged child, and the margin is wide enough that it is never
    /// the thing that decides. It is scoped to `answer_evidence` alone: the
    /// sealed sync, summary and follow-up reads keep `timeout` unchanged.
    static let answerEvidenceTimeoutMargin: TimeInterval = 90
    var answerEvidenceTimeout: TimeInterval { timeout + Self.answerEvidenceTimeoutMargin }

    /// True when this process is a test host rather than the shipped app.
    ///
    /// A test host is an app bundle too, and since M2.2d it carries a real
    /// worker. This is the one place that pairs that worker with the *real*
    /// store, so it is the right place to refuse: without this, a test that
    /// asked for the production runner would sync the user's own messages.
    /// It happened once; the guard and its reason are kept together so that
    /// neither is removed without the other.
    /// One definition, shared with the app's launch path. Two copies of this
    /// predicate would eventually disagree, and the disagreement would be about
    /// whether to touch the user's data.
    static var isUnderTestHost: Bool { RuntimeEnvironment.isUnderTestHost }

    /// The runner for this build, or nil when no worker was bundled -- or when
    /// this is a test host, whatever it happens to contain.
    ///
    /// Returning nil rather than a runner that always fails keeps the honest
    /// M2.2c behaviour available: a build without the worker still reports the
    /// packaging gap instead of pretending a sync was attempted.
    static func bundled(
        in bundle: Bundle = .main,
        store: URL = MemoryStoreLocation.canonical,
        messageStore: URL = MemoryStoreLocation.messageStore
    ) -> PackagedMemorySyncRunner? {
        guard !isUnderTestHost else { return nil }
        let executable = URL(fileURLWithPath: bundle.bundlePath)
            .appendingPathComponent("Contents")
            .appendingPathComponent(bundleSubpath)
        guard FileManager.default.isExecutableFile(atPath: executable.path) else { return nil }
        return PackagedMemorySyncRunner(
            workerURL: executable, storeURL: store, messageStoreURL: messageStore
        )
    }

    // MARK: - MemorySyncRunning

    func sync(source: MemorySource) async -> MemorySyncOutcome {
        do {
            try MemoryStoreLocation.prepareDirectory(for: storeURL)
        } catch {
            return .failed(.ingestionFailed(state: "store_directory_unavailable"))
        }
        var request = baseRequest(op: "sync")
        // The app can only offer the store it fills itself. Naming the source
        // explicitly pins it: the worker refuses a selection it cannot honour
        // rather than reading a different one.
        request["message_source"] = source.rawValue
        switch invoke(request) {
        case .failure(let failure):
            return .failed(failure)
        case .success(let reply):
            guard reply["ok"] as? Bool == true else {
                return .failed(failureFrom(reply))
            }
            let counts = reply["counts"] as? [String: Any] ?? [:]
            return .succeeded(
                MemorySyncCounts(
                    conversationsSeen: counts["conversations_seen"] as? Int ?? 0,
                    messagesSeen: counts["messages_seen"] as? Int ?? 0,
                    messagesInserted: counts["messages_inserted"] as? Int ?? 0,
                    messagesUpdated: counts["messages_updated"] as? Int ?? 0
                ),
                Self.summary(from: reply["freshness"], source: source)
            )
        }
    }

    func freshness(source: MemorySource) async -> MemoryFreshnessSummary? {
        guard case .success(let reply) = invoke(baseRequest(op: "status")),
              reply["ok"] as? Bool == true else { return nil }
        return Self.summary(from: reply["freshness"], source: source)
    }


    // MARK: - Daily Summary

    func prepare(
        source: MemorySource,
        start: Date,
        end: Date,
        messageLimit: Int
    ) async -> DailySummaryOutcome {
        var request = baseRequest(op: "summary_input")
        request["message_source"] = source.rawValue
        request["start"] = start.timeIntervalSince1970
        request["end"] = end.timeIntervalSince1970
        request["message_limit"] = messageLimit

        switch invoke(request) {
        case .failure(let failure):
            return .failed(dailySummaryFailure(fromLocal: failure))
        case .success(let reply):
            guard reply["ok"] as? Bool == true else {
                return .failed(dailySummaryFailure(from: reply))
            }
            guard let replySourceRaw = reply["source"] as? String,
                  let replySource = MemorySource(rawValue: replySourceRaw),
                  replySource == source,
                  let window = reply["window"] as? [String: Any],
                  let startSeconds = window["start"] as? Double,
                  let endSeconds = window["end"] as? Double,
                  abs(startSeconds - start.timeIntervalSince1970) < 0.001,
                  abs(endSeconds - end.timeIntervalSince1970) < 0.001,
                  let counts = reply["counts"] as? [String: Any],
                  let coverageRoot = reply["coverage"] as? [String: Any],
                  let coverageStatus = coverageRoot["status"] as? String,
                  let trustworthyEmpty = coverageRoot["trustworthy_empty"] as? Bool,
                  let caveats = coverageRoot["caveats"] as? [String],
                  let conversationRows = reply["conversations"] as? [[String: Any]],
                  let senderRows = reply["senders"] as? [[String: Any]],
                  let messageRows = reply["messages"] as? [[String: Any]]
            else {
                return .failed(.workerFailed(state: "worker_response_malformed"))
            }

            let conversations = conversationRows.compactMap { row -> DailySummaryConversation? in
                guard let index = row["index"] as? Int,
                      let label = row["label"] as? String,
                      let messageCount = row["message_count"] as? Int,
                      let senderCount = row["sender_count"] as? Int,
                      let firstAt = row["first_at"] as? Double,
                      let lastAt = row["last_at"] as? Double
                else { return nil }
                return DailySummaryConversation(
                    id: index,
                    label: label,
                    messageCount: messageCount,
                    senderCount: senderCount,
                    firstAt: Date(timeIntervalSince1970: firstAt),
                    lastAt: Date(timeIntervalSince1970: lastAt)
                )
            }
            guard conversations.count == conversationRows.count else {
                return .failed(.workerFailed(state: "worker_response_malformed"))
            }

            let senders = senderRows.compactMap { row -> DailySummarySenderCount? in
                guard let sender = row["sender"] as? String,
                      let count = row["count"] as? Int else { return nil }
                return DailySummarySenderCount(sender: sender, count: count)
            }
            guard senders.count == senderRows.count else {
                return .failed(.workerFailed(state: "worker_response_malformed"))
            }

            let messages = messageRows.compactMap { row -> DailySummaryMessage? in
                guard let ordinal = row["ordinal"] as? Int,
                      let conversationIndex = row["conversation_index"] as? Int,
                      let sourceRaw = row["source"] as? String,
                      let messageSource = MemorySource(rawValue: sourceRaw),
                      let timestamp = row["timestamp"] as? Double,
                      let timestampKind = row["timestamp_kind"] as? String,
                      let kind = row["kind"] as? String,
                      let text = row["text"] as? String,
                      let textTruncated = row["text_truncated"] as? Bool
                else { return nil }
                return DailySummaryMessage(
                    id: ordinal,
                    conversationIndex: conversationIndex,
                    source: messageSource,
                    timestamp: Date(timeIntervalSince1970: timestamp),
                    timestampKind: timestampKind,
                    sender: row["sender"] as? String,
                    kind: kind,
                    text: text,
                    textTruncated: textTruncated
                )
            }
            guard messages.count == messageRows.count else {
                return .failed(.workerFailed(state: "worker_response_malformed"))
            }

            return .ready(DailySummarySnapshot(
                source: source,
                start: Date(timeIntervalSince1970: startSeconds),
                end: Date(timeIntervalSince1970: endSeconds),
                returnedMessages: counts["returned_messages"] as? Int ?? messages.count,
                returnedConversations: counts["returned_conversations"] as? Int ?? conversations.count,
                returnedSenders: counts["returned_senders"] as? Int ?? senders.count,
                textTruncatedCount: counts["text_truncated"] as? Int ?? 0,
                truncated: reply["truncated"] as? Bool ?? false,
                coverage: DailySummaryCoverage(
                    status: coverageStatus,
                    trustworthyEmpty: trustworthyEmpty,
                    caveats: caveats
                ),
                freshness: Self.summary(from: reply["freshness"], source: source),
                conversations: conversations,
                senders: senders,
                messages: messages
            ))
        }
    }


    // MARK: - Follow-up candidates

    func scan(
        source: MemorySource,
        start: Date,
        end: Date,
        messageLimit: Int,
        candidateLimit: Int
    ) async -> FollowUpOutcome {
        var request = baseRequest(op: "reminder_candidates")
        request["message_source"] = source.rawValue
        request["start"] = start.timeIntervalSince1970
        request["end"] = end.timeIntervalSince1970
        request["message_limit"] = messageLimit
        request["candidate_limit"] = candidateLimit

        switch invoke(request) {
        case .failure(let failure):
            return .failed(followUpFailure(fromLocal: failure))
        case .success(let reply):
            guard reply["ok"] as? Bool == true else {
                return .failed(followUpFailure(from: reply))
            }
            guard let replySourceRaw = reply["source"] as? String,
                  let replySource = MemorySource(rawValue: replySourceRaw),
                  replySource == source,
                  let window = reply["window"] as? [String: Any],
                  let startSeconds = window["start"] as? Double,
                  let endSeconds = window["end"] as? Double,
                  abs(startSeconds - start.timeIntervalSince1970) < 0.001,
                  abs(endSeconds - end.timeIntervalSince1970) < 0.001,
                  let counts = reply["counts"] as? [String: Any],
                  let coverageRoot = reply["coverage"] as? [String: Any],
                  let coverageStatus = coverageRoot["status"] as? String,
                  let trustworthyEmpty = coverageRoot["trustworthy_empty"] as? Bool,
                  let caveats = coverageRoot["caveats"] as? [String],
                  let conversationRows = reply["conversations"] as? [[String: Any]],
                  let candidateRows = reply["candidates"] as? [[String: Any]]
            else {
                return .failed(.workerFailed(state: "worker_response_malformed"))
            }

            let conversations = conversationRows.compactMap { row -> FollowUpConversation? in
                guard let index = row["index"] as? Int,
                      let label = row["label"] as? String
                else { return nil }
                return FollowUpConversation(id: index, label: label)
            }
            guard conversations.count == conversationRows.count else {
                return .failed(.workerFailed(state: "worker_response_malformed"))
            }

            let candidates = candidateRows.compactMap { row -> FollowUpCandidate? in
                guard let ordinal = row["ordinal"] as? Int,
                      let conversationIndex = row["conversation_index"] as? Int,
                      let sourceRaw = row["source"] as? String,
                      let candidateSource = MemorySource(rawValue: sourceRaw),
                      candidateSource == source,
                      let timestamp = row["timestamp"] as? Double,
                      let timestampKind = row["timestamp_kind"] as? String,
                      let text = row["text"] as? String,
                      let textTruncated = row["text_truncated"] as? Bool,
                      let reasons = row["reasons"] as? [String]
                else { return nil }
                guard let anchor = Self.archiveAnchor(
                    from: row["archive_evidence"], source: candidateSource
                ) else { return nil }
                return FollowUpCandidate(
                    id: ordinal,
                    conversationIndex: conversationIndex,
                    source: candidateSource,
                    timestamp: Date(timeIntervalSince1970: timestamp),
                    timestampKind: timestampKind,
                    sender: row["sender"] as? String,
                    text: text,
                    textTruncated: textTruncated,
                    reasons: reasons,
                    archiveEvidence: anchor
                )
            }
            guard candidates.count == candidateRows.count else {
                return .failed(.workerFailed(state: "worker_response_malformed"))
            }

            return .ready(FollowUpCandidateSnapshot(
                source: source,
                start: Date(timeIntervalSince1970: startSeconds),
                end: Date(timeIntervalSince1970: endSeconds),
                scannedMessages: counts["scanned_messages"] as? Int ?? 0,
                returnedCandidates: counts["returned_candidates"] as? Int ?? candidates.count,
                textTruncatedCount: counts["text_truncated"] as? Int ?? 0,
                truncated: reply["truncated"] as? Bool ?? false,
                coverage: FollowUpCoverage(
                    status: coverageStatus,
                    trustworthyEmpty: trustworthyEmpty,
                    caveats: caveats
                ),
                freshness: Self.summary(from: reply["freshness"], source: source),
                conversations: conversations,
                candidates: candidates
            ))
        }
    }

    // MARK: - Answer evidence window

    /// A bounded, exactly anchored Archive evidence window.
    ///
    /// There is no source parameter because there is only one source this can
    /// be: only an Archive-attributed row carries the canonical identity a
    /// citation is revealed through, so the window pins Archive here rather
    /// than trusting a caller to ask for it. The same shape is returned or
    /// refused whole -- a row that cannot be named fails the reply, it is
    /// never defaulted into an anchorless row that could be mistaken for
    /// citeable.
    func answerEvidence(
        start: Date,
        end: Date,
        messageLimit: Int
    ) async -> AnswerEvidenceOutcome {
        var request = baseRequest(op: "answer_evidence")
        request["message_source"] = MemorySource.archive.rawValue
        request["start"] = start.timeIntervalSince1970
        request["end"] = end.timeIntervalSince1970
        request["message_limit"] = messageLimit

        switch invoke(request, timeout: answerEvidenceTimeout) {
        case .failure(let failure):
            return .failed(answerEvidenceFailure(fromLocal: failure))
        case .success(let reply):
            guard reply["ok"] as? Bool == true else {
                return .failed(answerEvidenceFailure(from: reply))
            }
            guard let replySourceRaw = reply["source"] as? String,
                  let replySource = MemorySource(rawValue: replySourceRaw),
                  replySource == .archive,
                  let window = reply["window"] as? [String: Any],
                  let startSeconds = window["start"] as? Double,
                  let endSeconds = window["end"] as? Double,
                  abs(startSeconds - start.timeIntervalSince1970) < 0.001,
                  abs(endSeconds - end.timeIntervalSince1970) < 0.001,
                  let counts = reply["counts"] as? [String: Any],
                  let coverageRoot = reply["coverage"] as? [String: Any],
                  let coverageStatus = coverageRoot["status"] as? String,
                  let trustworthyEmpty = coverageRoot["trustworthy_empty"] as? Bool,
                  let caveats = coverageRoot["caveats"] as? [String],
                  let evidenceRows = reply["evidence"] as? [[String: Any]],
                  Self.echoesRequestedWindow(reply["query_scope"],
                                            start: startSeconds, end: endSeconds),
                  let scannedMessages = counts["scanned_messages"] as? Int,
                  let returnedEvidence = counts["returned_evidence"] as? Int,
                  let excludedUnanchored = counts["excluded_unanchored"] as? Int,
                  let textTruncatedCount = counts["text_truncated"] as? Int,
                  let truncated = reply["truncated"] as? Bool,
                  scannedMessages >= returnedEvidence + excludedUnanchored,
                  // A window that lost a row to the bound, or to an
                  // unnameable identity, has to say so.
                  truncated || (returnedEvidence + excludedUnanchored == scannedMessages)
            else {
                return .failed(.workerFailed(state: "worker_response_malformed"))
            }

            let rows = evidenceRows.compactMap { row -> AnswerEvidenceRow? in
                guard let messageID = row["canonical_message_id"] as? String,
                      let conversationID = row["canonical_conversation_id"] as? String,
                      let rowSourceRaw = row["source"] as? String,
                      let rowSource = MemorySource(rawValue: rowSourceRaw),
                      rowSource == .archive,
                      let timestamp = row["timestamp"] as? Double,
                      let timestampKind = row["timestamp_kind"] as? String,
                      let text = row["text"] as? String,
                      let textTruncated = row["text_truncated"] as? Bool,
                      case let .some(anchor?) = Self.archiveAnchor(
                        from: row["archive_evidence"], source: rowSource
                      )
                else { return nil }
                return AnswerEvidenceRow(
                    canonicalMessageID: messageID,
                    canonicalConversationID: conversationID,
                    timestamp: Date(timeIntervalSince1970: timestamp),
                    timestampKind: timestampKind,
                    sender: row["sender"] as? String,
                    text: text,
                    textTruncated: textTruncated,
                    archiveEvidence: anchor
                )
            }
            guard rows.count == evidenceRows.count,
                  returnedEvidence == rows.count else {
                return .failed(.workerFailed(state: "worker_response_malformed"))
            }

            return .ready(AnswerEvidenceSnapshot(
                start: Date(timeIntervalSince1970: startSeconds),
                end: Date(timeIntervalSince1970: endSeconds),
                scannedMessages: scannedMessages,
                returnedEvidence: returnedEvidence,
                excludedUnanchored: excludedUnanchored,
                textTruncatedCount: textTruncatedCount,
                truncated: truncated,
                coverage: FollowUpCoverage(
                    status: coverageStatus,
                    trustworthyEmpty: trustworthyEmpty,
                    caveats: caveats
                ),
                freshness: Self.summary(from: reply["freshness"], source: .archive),
                rows: rows
            ))
        }
    }

    // MARK: - Protocol

    private func baseRequest(op: String) -> [String: Any] {
        ["op": op, "store_path": storeURL.path, "message_store_path": messageStoreURL.path]
    }

    /// Decodes the worker's canonical Archive anchor, or rejects the row.
    ///
    /// The worker packs no identity of its own: it unpacks the one Memory
    /// already stored, so this only has to refuse an anchor that is malformed,
    /// non-positive, or attached to something that is not Archive evidence. A
    /// candidate that cannot be named is never offered as one that can be
    /// revealed, and no anchor is ever defaulted from a missing field.
    private static func archiveAnchor(
        from value: Any?, source: MemorySource
    ) -> ArchiveEvidenceAnchor?? {
        // Archive evidence is only ever named by the worker's own canonical
        // identity, so an Archive row without one is malformed, not anchorless.
        // Only a non-Archive candidate may legitimately carry no anchor.
        guard source == .archive else { return value == nil ? .some(nil) : nil }
        guard let row = value as? [String: Any],
              let importID = row["import_id"] as? Int64, importID > 0,
              let sequence = row["sequence"] as? Int, sequence >= 0
        else { return nil }
        return .some(ArchiveEvidenceAnchor(importID: importID, sequence: sequence))
    }

    /// The worker restates the query it ran as ``query_scope``. This window's
    /// value is that the restatement is the window the app asked for: a scope
    /// that is absent, or that describes a different range, would let a
    /// caller believe it was reading one window while being handed another.
    private static func echoesRequestedWindow(
        _ value: Any?, start: Double, end: Double
    ) -> Bool {
        guard let scope = value as? [String: Any],
              let scopeWindow = scope["window"] as? [Any],
              scopeWindow.count == 2,
              let scopeStart = scopeWindow[0] as? Double,
              let scopeEnd = scopeWindow[1] as? Double,
              let limit = scope["limit"] as? Int, limit > 0
        else { return false }
        return abs(scopeStart - start) < 0.001 && abs(scopeEnd - end) < 0.001
    }

    private func failureFrom(_ reply: [String: Any]) -> MemorySyncFailure {
        let state = reply["state"] as? String ?? "unknown"
        // A source that could not be built is reported as such, so the user
        // sees "the selected source is unavailable" and never a bare failure
        // that might be mistaken for incomplete coverage.
        if state.contains(":") { return .sourceUnavailable(state: state) }
        if state == "consent_withheld" || state == "consent_state_missing"
            || state == "consent_state_malformed" || state == "consent_unobservable" {
            return .consentWithheld
        }
        return .ingestionFailed(state: state)
    }


    private func dailySummaryFailure(from reply: [String: Any]) -> DailySummaryFailure {
        let state = reply["state"] as? String ?? "unknown"
        if state == "consent_withheld" || state == "consent_state_missing"
            || state == "consent_state_malformed" || state == "consent_unobservable" {
            return .consentWithheld
        }
        if state.hasPrefix("memory_") {
            return .memoryUnavailable(state: state)
        }
        return .workerFailed(state: state)
    }

    private func dailySummaryFailure(fromLocal failure: MemorySyncFailure) -> DailySummaryFailure {
        switch failure {
        case .consentWithheld:
            return .consentWithheld
        case .runnerUnavailable:
            return .runnerUnavailable
        case .sourceUnavailable(let state):
            return .memoryUnavailable(state: state)
        case .ingestionFailed(let state):
            return .workerFailed(state: state)
        }
    }


    private func followUpFailure(from reply: [String: Any]) -> FollowUpFailure {
        let state = reply["state"] as? String ?? "unknown"
        if state == "consent_withheld" || state == "consent_state_missing"
            || state == "consent_state_malformed" || state == "consent_unobservable" {
            return .consentWithheld
        }
        if state.hasPrefix("memory_") {
            return .memoryUnavailable(state: state)
        }
        return .workerFailed(state: state)
    }

    private func followUpFailure(fromLocal failure: MemorySyncFailure) -> FollowUpFailure {
        switch failure {
        case .consentWithheld:
            return .consentWithheld
        case .runnerUnavailable:
            return .runnerUnavailable
        case .sourceUnavailable(let state):
            return .memoryUnavailable(state: state)
        case .ingestionFailed(let state):
            return .workerFailed(state: state)
        }
    }

    private func answerEvidenceFailure(from reply: [String: Any]) -> AnswerEvidenceFailure {
        let state = reply["state"] as? String ?? "unknown"
        if state == "consent_withheld" || state == "consent_state_missing"
            || state == "consent_state_malformed" || state == "consent_unobservable" {
            return .consentWithheld
        }
        if state.hasPrefix("memory_") || state == "archive_evidence_malformed" {
            return .memoryUnavailable(state: state)
        }
        return .workerFailed(state: state)
    }

    private func answerEvidenceFailure(fromLocal failure: MemorySyncFailure) -> AnswerEvidenceFailure {
        switch failure {
        case .consentWithheld:
            return .consentWithheld
        case .runnerUnavailable, .sourceUnavailable:
            return .workerFailed(state: "worker_unavailable")
        case .ingestionFailed(let state):
            return .workerFailed(state: state)
        }
    }

    private func invoke(
        _ request: [String: Any],
        timeout overrideTimeout: TimeInterval? = nil
    ) -> Result<[String: Any], MemorySyncFailure> {
        let deadline = overrideTimeout ?? timeout
        guard let payload = try? JSONSerialization.data(withJSONObject: request) else {
            return .failure(.ingestionFailed(state: "request_not_encodable"))
        }
        let process = Process()
        process.executableURL = workerURL          // fixed path; never a PATH lookup
        process.arguments = []
        process.environment = Self.childEnvironment
        let input = Pipe(), output = Pipe(), errors = Pipe()
        process.standardInput = input
        process.standardOutput = output
        process.standardError = errors
        do {
            try process.run()
        } catch {
            return .failure(.ingestionFailed(state: "worker_not_runnable"))
        }
        input.fileHandleForWriting.write(payload)
        try? input.fileHandleForWriting.close()

        // The deadline has to cover the read, not just the wait. Draining the
        // pipe blocks until the child closes it, so a worker that hangs while
        // holding stdout open would otherwise sit here past any timeout: the
        // watchdog terminates it, which is what ends the read.
        let expiry = Expiry()
        let watchdog = DispatchWorkItem {
            if process.isRunning {
                expiry.fired = true
                process.terminate()
            }
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + deadline, execute: watchdog)
        defer { watchdog.cancel() }

        // Read before waiting: a reply larger than the pipe buffer would
        // otherwise deadlock the child against a parent that is not draining.
        let data = output.fileHandleForReading.readDataToEndOfFile()
        _ = errors.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        if expiry.fired {
            return .failure(.ingestionFailed(state: "worker_timed_out"))
        }
        guard let object = try? JSONSerialization.jsonObject(with: data),
              let reply = object as? [String: Any] else {
            // Includes the case where the worker died without a reply.
            return .failure(.ingestionFailed(state: "worker_response_malformed"))
        }
        return .success(reply)
    }

    /// Built from scratch, never inherited.
    private static var childEnvironment: [String: String] {
        [
            "HOME": NSHomeDirectory(),
            "PATH": "/usr/bin:/bin",
            "LANG": "en_US.UTF-8",
        ]
    }

    // MARK: - Freshness mapping

    /// Maps the worker's freshness for one source into the app's summary.
    ///
    /// `coverageSummary` is *derived*, and only from what the boundaries
    /// actually say: a complete-through boundary means complete, an
    /// observed-through boundary without one means partial, a successful run
    /// that observed no window at all means unavailable, and no successful run
    /// means none. It is never a restatement of the sync's own success.
    static func summary(from freshness: Any?, source: MemorySource) -> MemoryFreshnessSummary? {
        guard let root = freshness as? [String: Any],
              let sources = root["sources"] as? [String: Any],
              let entry = sources[source.rawValue] as? [String: Any] else { return nil }
        let succeeded = entry["last_succeeded_at"] as? Double
        let observed = entry["observed_through"] as? Double
        let complete = entry["complete_through"] as? Double
        let coverage: String
        if complete != nil { coverage = "complete" }
        else if observed != nil { coverage = "partial" }
        else if succeeded != nil { coverage = "unavailable" }
        else { coverage = "none" }
        return MemoryFreshnessSummary(
            source: source,
            lastSuccessfulSync: succeeded.map(Date.init(timeIntervalSince1970:)),
            observedThrough: observed.map(Date.init(timeIntervalSince1970:)),
            completeThrough: complete.map(Date.init(timeIntervalSince1970:)),
            latestMessageAt: (entry["latest_message_at"] as? Double).map(Date.init(timeIntervalSince1970:)),
            lastRunState: entry["last_attempt_state"] as? String ?? "never",
            lastRunFailure: entry["last_attempt_failure_state"] as? String,
            coverageSummary: coverage
        )
    }
}
