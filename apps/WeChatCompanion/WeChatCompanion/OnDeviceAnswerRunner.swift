import Foundation
#if canImport(FoundationModels)
import FoundationModels
#endif

// On-Device Archive Answer v1.
//
// One question, one bounded time window, one answer, and citations that are
// only ever the sealed Archive anchors the host already held. The model never
// sees Archive identity and never names its own provenance: it returns
// indices into a prompt the host wrote, and the host maps those back.
//
// Nothing here persists. The question, the answer and the citations live in
// memory for as long as the process does.

/// Why the on-device model cannot answer right now.
///
/// The three reasons are kept apart on purpose: "this Mac cannot run it",
/// "Apple Intelligence is switched off", and "the model is not downloaded yet"
/// are three different things a person can act on, and collapsing them into one
/// "unsupported Mac" message would tell someone to look in the wrong place.
enum AnswerRuntimeUnavailableReason: Equatable, Sendable {
    /// The framework is not present in this build, or this OS predates it.
    case frameworkUnavailable
    case deviceNotEligible
    case appleIntelligenceNotEnabled
    case modelNotReady

    var message: String {
        switch self {
        case .frameworkUnavailable:
            "This build cannot run the on-device model."
        case .deviceNotEligible:
            "This Mac is not eligible for the on-device model."
        case .appleIntelligenceNotEnabled:
            "Apple Intelligence is turned off for this Mac."
        case .modelNotReady:
            "The on-device model is not ready yet."
        }
    }
}

enum AnswerRuntimeAvailability: Equatable, Sendable {
    case available(contextSize: Int)
    case unavailable(AnswerRuntimeUnavailableReason)

    var contextSize: Int? {
        if case .available(let size) = self { return size }
        return nil
    }

    var isAvailable: Bool { contextSize != nil }

    var reason: AnswerRuntimeUnavailableReason? {
        if case .unavailable(let reason) = self { return reason }
        return nil
    }
}

/// One run-local presentation token -- [1], [2], and so on -- plus the row it
/// stands for.
///
/// The anchor is host-only. It is what a validated citation is revealed
/// through, and it is never formatted into the prompt: the prompt is built
/// from `token`, `sender`, `timestamp` and `text`, and nothing else.
struct AnswerModelRow: Equatable, Sendable, Identifiable {
    let token: Int
    let sender: String?
    let timestamp: Date
    let text: String
    let archiveEvidence: ArchiveEvidenceAnchor

    var id: Int { token }
}

/// The exact model-visible input for one run, and what it cost.
///
/// rowsOmitted and rowsTextShortened are this slice's own trimming, kept
/// strictly apart from the sealed window's own text_truncated: that flag means
/// the worker shortened a row; these mean the host did, to fit a context window
/// that has nothing to do with the worker.
struct AnswerModelInput: Equatable, Sendable {
    static let maxRows = 40
    static let maxCharactersPerRow = 150
    /// Held back from the context for instructions, the question, the row
    /// labels and the structured answer. Deliberately generous: it costs a few
    /// rows of evidence and cannot cause a context overflow.
    static let reservedTokens = 1_500

    let question: String
    let rows: [AnswerModelRow]
    let prompt: String
    let rowsOmitted: Int
    let rowsTextShortened: Int

    var modelInputTruncated: Bool { rowsOmitted > 0 || rowsTextShortened > 0 }

    /// Whether a question of this length still leaves safe capacity for a
    /// prompt at all.
    ///
    /// Rows shrink before the question does, and a question is never shortened,
    /// so the hard boundary is the question itself: past it there is no prompt
    /// to build and the run is refused rather than overflowed. contextSize is
    /// only known at runtime, which is why this is asked where the runtime is
    /// asked, not at the text field.
    static func fitsQuestion(_ question: String, contextSize: Int) -> Bool {
        question.count + answerInstructions.count + reservedTokens <= contextSize
    }

    /// Deterministic, oldest-first, no chunking and no second pass.
    ///
    /// Rows are taken in the order the sealed window returned them (oldest
    /// first) and each is capped at 150 characters. If the whole set still
    /// would not fit the actual context, rows are dropped from the end.
    static func build(
        question: String, rows: [AnswerEvidenceRow], contextSize: Int
    ) -> AnswerModelInput {
        // One *visible character* is budgeted as one token. Chinese evidence
        // is close to one token per character, so a four-characters-per-token
        // guess would overrun the context by roughly four times, and an
        // overrun surfaces as a model error -- the one outcome this budgeting
        // exists to prevent. Undercounting only costs a few rows.
        let characterBudget = max(0, contextSize - reservedTokens)
        var kept: [AnswerModelRow] = []
        var shortened = 0
        var used = question.count + answerInstructions.count
        for row in rows.prefix(maxRows) {
            let label = label(token: kept.count + 1, sender: row.sender, at: row.timestamp)
            let available = min(maxCharactersPerRow, characterBudget - used - label.count)
            guard available > 0 else { break }
            let text = row.text.count > available ? String(row.text.prefix(available)) : row.text
            if text.count < row.text.count { shortened += 1 }
            used += label.count + text.count
            kept.append(AnswerModelRow(
                token: kept.count + 1,
                sender: row.sender,
                timestamp: row.timestamp,
                text: text,
                archiveEvidence: row.archiveEvidence
            ))
        }
        return AnswerModelInput(
            question: question,
            rows: kept,
            prompt: Self.promptText(question: question, rows: kept),
            rowsOmitted: rows.count - kept.count,
            rowsTextShortened: shortened
        )
    }

    private static func label(token: Int, sender: String?, at timestamp: Date) -> String {
        "[\(token)] " + (sender ?? "Unknown sender") + " · "
            + timestamp.formatted(date: .abbreviated, time: .shortened) + "\n"
    }

    private static func promptText(question: String, rows: [AnswerModelRow]) -> String {
        var evidence = ""
        for row in rows {
            evidence += label(token: row.token, sender: row.sender, at: row.timestamp)
                + row.text + "\n\n"
        }
        return "Evidence:\n\(evidence)\nQuestion: \(question)"
    }
}

/// Instruction semantics for one answer: same language as the question,
/// evidence only, and citations restricted to the tokens actually supplied.
let answerInstructions = """
    Answer only from the evidence supplied below.

    - Do not invent a fact, a time, or a name that the evidence does not contain.
    - If the evidence does not answer the question, say so plainly.
    - citations may contain only the row numbers listed in this prompt.
    - Answer in the language of the question.
    """

/// One validated citation: a run-local token plus the sealed anchor it maps to.
struct AnswerCitation: Identifiable, Equatable, Sendable {
    let token: Int
    let sender: String?
    let timestamp: Date
    let text: String
    let archiveEvidence: ArchiveEvidenceAnchor

    var id: Int { token }

    /// The exact-reveal request a click turns into.
    ///
    /// This is the *only* way a citation reaches the Archive: it builds the
    /// same `LocalSearchResult` shape Search and a saved follow-up already
    /// reveal through, so there is one reveal path rather than a second one
    /// written for Agents. `AnswerEvidenceRow` carries no conversation label
    /// and none is inferred here -- the fallback is the truthful label the
    /// reveal surface already accepts for a nameless import.
    var searchResult: LocalSearchResult {
        LocalSearchResult(
            id: "archive-answer:\(archiveEvidence.importID):\(archiveEvidence.sequence)",
            target: .archiveRecord(
                importID: archiveEvidence.importID,
                sequence: archiveEvidence.sequence,
                provenance: .archiveAttributed
            ),
            source: .archiveAttributed,
            provenance: .archiveAttributed,
            conversationLabel: "Imported archive export",
            sender: sender,
            timestamp: timestamp,
            excerpt: text,
            linkState: nil
        )
    }
}

/// The answer, its citations, and the input that produced it.
///
    /// `validating` is the only way a result is built: there is no constructor
    /// that accepts model-supplied indices without passing them through the
    /// host's own bounds check, de-duplication and token map.
struct AnswerRunResult: Equatable, Sendable {
    let answer: String
    let citations: [AnswerCitation]
    let input: AnswerModelInput

    /// An answer with no surviving citation is still shown, but never as
    /// grounded: hasVerifiableSource is what the surface reads.
    var hasVerifiableSource: Bool { !citations.isEmpty }

    /// Indices are resolved against `input.rows` -- the rows actually supplied
    /// to the model -- and never against the sealed window as a whole. A row
    /// the host dropped for context was never shown, so a citation naming it
    /// is dropped too rather than resolved against a row the model could not
    /// have read.
    static func validating(
        answer: String, returnedIndices: [Int], input: AnswerModelInput
    ) -> AnswerRunResult {
        var seen: Set<Int> = []
        var citations: [AnswerCitation] = []
        for index in returnedIndices {
            // Non-positive, and anything not supplied in this run, is dropped.
            // There is no nearest-row fallback and no fuzzy match: an
            // unnameable citation is not softened into a plausible one.
            guard index >= 1, index <= input.rows.count, !seen.contains(index) else { continue }
            seen.insert(index)
            let row = input.rows[index - 1]
            citations.append(AnswerCitation(
                token: index,
                sender: row.sender,
                timestamp: row.timestamp,
                text: row.text,
                archiveEvidence: row.archiveEvidence
            ))
        }
        return AnswerRunResult(answer: answer, citations: citations, input: input)
    }
}

/// What the answer surface discloses, keeping the two incompleteness dimensions
/// apart rather than merging them into one number.
struct AnswerEvidenceDisclosure: Equatable, Sendable {
    let evidenceTruncated: Bool
    let excludedUnanchored: Int
    let workerTextTruncated: Int
    let modelInputTruncated: Bool
    let rowsOmittedForContext: Int
    let rowsTextShortenedForContext: Int

    init(snapshot: AnswerEvidenceSnapshot, modelInput: AnswerModelInput) {
        evidenceTruncated = snapshot.truncated
        excludedUnanchored = snapshot.excludedUnanchored
        workerTextTruncated = snapshot.textTruncatedCount
        modelInputTruncated = modelInput.modelInputTruncated
        rowsOmittedForContext = modelInput.rowsOmitted
        rowsTextShortenedForContext = modelInput.rowsTextShortened
    }

    var lines: [String] {
        var lines: [String] = []
        if evidenceTruncated {
            lines.append("The Archive window itself was truncated upstream.")
        }
        if excludedUnanchored > 0 {
            lines.append(
                "\(excludedUnanchored) scanned "
                    + "\(excludedUnanchored == 1 ? "row was" : "rows were")"
                    + " excluded because it could not be named exactly."
            )
        }
        if workerTextTruncated > 0 {
            lines.append(
                "\(workerTextTruncated) "
                    + "\(workerTextTruncated == 1 ? "row had" : "rows had")"
                    + " text shortened by the worker."
            )
        }
        if rowsOmittedForContext > 0 {
            lines.append(
                "\(rowsOmittedForContext) more "
                    + "\(rowsOmittedForContext == 1 ? "row was" : "rows were")"
                    + " left out of this answer to fit the model context."
            )
        }
        if rowsTextShortenedForContext > 0 {
            lines.append(
                "\(rowsTextShortenedForContext) supplied "
                    + "\(rowsTextShortenedForContext == 1 ? "row had" : "rows had")"
                    + " text shortened to fit the model context."
            )
        }
        return lines
    }
}

enum AnswerFailure: Error, Equatable, Sendable {
    /// Reuses the sealed window's own failure vocabulary rather than inventing
    /// a second one: consent withheld and an unavailable Memory store mean the
    /// same thing here as they do in Daily Summary and Reminders.
    case evidence(AnswerEvidenceFailure)
    case runtimeUnavailable(AnswerRuntimeUnavailableReason)
    case generationFailed
    case timedOut
    case cancelled
    /// The question on its own does not fit the model's context. Distinct from
    /// a generation failure because nothing failed: the run was refused before
    /// it started, and the honest thing to say is that the question is too
    /// long to ask here.
    case questionTooLong

    var message: String {
        switch self {
        case .evidence(let failure):
            failure.message
        case .runtimeUnavailable(let reason):
            reason.message
        case .generationFailed:
            "The on-device model did not return an answer."
        case .timedOut:
            "The on-device model took too long and was stopped."
        case .cancelled:
            "The answer run was cancelled."
        case .questionTooLong:
            "This question is too long to ask about this window. Shorten it and ask again."
        }
    }
}

/// One-shot state. answered keeps its result for as long as the process lives;
/// a relaunch starts at idle because nothing was written.
enum AnswerPhase: Equatable, Sendable {
    case idle
    case running
    case answered
    case failed(AnswerFailure)
    case cancelled
    case timedOut

    var isRunning: Bool { self == .running }
}

/// The seam through which the answer runtime is reached. Injected; the default
/// is the on-device model when this OS has one, and an honest unavailable stub
/// when it does not.
protocol AnswerRunning: Sendable {
    func currentAvailability() -> AnswerRuntimeAvailability
    func answer(
        question: String, snapshot: AnswerEvidenceSnapshot
    ) async throws -> AnswerRunResult
}

/// The production runner. It reads no keychain item, opens no socket and has no
/// fallback: when the on-device model is unavailable this reports that and
/// nothing else.
struct SystemLanguageModelAnswerRunner: AnswerRunning {
    func currentAvailability() -> AnswerRuntimeAvailability {
        #if canImport(FoundationModels)
        if #available(macOS 26.0, *) {
            switch SystemLanguageModel.default.availability {
            case .available:
                return .available(contextSize: SystemLanguageModel.default.contextSize)
            case .unavailable(let reason):
                switch reason {
                case .deviceNotEligible: return .unavailable(.deviceNotEligible)
                case .appleIntelligenceNotEnabled: return .unavailable(.appleIntelligenceNotEnabled)
                case .modelNotReady: return .unavailable(.modelNotReady)
                @unknown default: return .unavailable(.modelNotReady)
                }
            }
        }
        return .unavailable(.frameworkUnavailable)
        #else
        return .unavailable(.frameworkUnavailable)
        #endif
    }

    func answer(
        question: String, snapshot: AnswerEvidenceSnapshot
    ) async throws -> AnswerRunResult {
        #if canImport(FoundationModels)
        if #available(macOS 26.0, *) {
            let input = AnswerModelInput.build(
                question: question,
                rows: snapshot.rows,
                contextSize: SystemLanguageModel.default.contextSize
            )
            let session = LanguageModelSession(instructions: answerInstructions)
            do {
                let generated = try await session.respond(
                    to: input.prompt, generating: GeneratedArchiveAnswer.self
                ).content
                return AnswerRunResult.validating(
                    answer: generated.answer,
                    returnedIndices: generated.citations,
                    input: input
                )
            } catch let error where error is CancellationError {
                // Cancellation stays a cancellation. Turning it into a domain
                // failure here made a user pressing Cancel read a generic
                // "no answer" notice, because the caller has no way to tell
                // `.failed(.cancelled)` from any other failure. Nothing in
                // this file catches it back into a domain case.
                throw error
            } catch {
                throw AnswerFailure.generationFailed
            }
        }
        throw AnswerFailure.runtimeUnavailable(.frameworkUnavailable)
        #else
        throw AnswerFailure.runtimeUnavailable(.frameworkUnavailable)
        #endif
    }
}

#if canImport(FoundationModels)
@available(macOS 26.0, *)
@Generable
struct GeneratedArchiveAnswer {
    @Guide(description: "The answer, written in the language of the question.")
    var answer: String
    @Guide(
        description: """
            The numbers of the evidence rows that support the answer. Use only
            numbers listed in the prompt, and none at all when the evidence
            does not answer the question.
            """
    )
    var citations: [Int]
}
#endif

/// The honest runner for a build without the on-device model.
struct UnavailableAnswerRunner: AnswerRunning {
    func currentAvailability() -> AnswerRuntimeAvailability {
        .unavailable(.frameworkUnavailable)
    }
    func answer(
        question: String, snapshot: AnswerEvidenceSnapshot
    ) async throws -> AnswerRunResult {
        throw AnswerFailure.runtimeUnavailable(.frameworkUnavailable)
    }
}
