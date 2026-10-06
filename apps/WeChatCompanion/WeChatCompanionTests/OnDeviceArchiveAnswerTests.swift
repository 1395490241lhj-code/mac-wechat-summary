import Foundation
import Testing
@testable import WeChatCompanion

// On-Device Archive Answer v1. The model itself is exercised separately by the
// FoundationModels boundary probe; everything here runs against a stub runner,
// because model prose is nondeterministic and must never be asserted on.

private func evidenceRow(
    _ index: Int,
    text: String,
    sender: String? = "张伟",
    seconds: TimeInterval = 1_700_000_000
) -> AnswerEvidenceRow {
    AnswerEvidenceRow(
        canonicalMessageID: "msg-\(index)",
        canonicalConversationID: "conv-1",
        timestamp: Date(timeIntervalSince1970: seconds + Double(index)),
        timestampKind: "source_created",
        sender: sender,
        text: text,
        textTruncated: false,
        archiveEvidence: ArchiveEvidenceAnchor(importID: 7, sequence: index)
    )
}

private func evidenceSnapshot(
    rows: [AnswerEvidenceRow],
    truncated: Bool = false,
    excludedUnanchored: Int = 0,
    textTruncatedCount: Int = 0,
    coverage: FollowUpCoverage = FollowUpCoverage(
        status: "complete", trustworthyEmpty: true, caveats: []
    )
) -> AnswerEvidenceSnapshot {
    AnswerEvidenceSnapshot(
        start: Date(timeIntervalSince1970: 1_700_000_000),
        end: Date(timeIntervalSince1970: 1_700_003_600),
        scannedMessages: rows.count + excludedUnanchored,
        returnedEvidence: rows.count,
        excludedUnanchored: excludedUnanchored,
        textTruncatedCount: textTruncatedCount,
        truncated: truncated,
        coverage: coverage,
        freshness: nil,
        rows: rows
    )
}

private final class RecordingEvidenceRunner: AnswerEvidenceRunning, @unchecked Sendable {
    struct Call: Equatable {
        let start: Date
        let end: Date
        let messageLimit: Int
        let canonicalID: String?
    }

    var outcomes: [AnswerEvidenceOutcome]
    var discoveryOutcomes: [ArchiveConversationDiscoveryOutcome]
    private(set) var calls: [Call] = []

    init(
        outcomes: [AnswerEvidenceOutcome] = [],
        discoveryOutcomes: [ArchiveConversationDiscoveryOutcome] = []
    ) {
        self.outcomes = outcomes
        self.discoveryOutcomes = discoveryOutcomes
    }

    func answerEvidence(
        start: Date, end: Date, messageLimit: Int
    ) async -> AnswerEvidenceOutcome {
        calls.append(Call(start: start, end: end, messageLimit: messageLimit, canonicalID: nil))
        return outcomes.isEmpty
            ? .failed(.workerFailed(state: "no_outcome"))
            : outcomes.removeFirst()
    }

    func answerEvidenceScoped(
        start: Date, end: Date, messageLimit: Int, conversationCanonicalID: String?
    ) async -> AnswerEvidenceOutcome {
        calls.append(Call(start: start, end: end, messageLimit: messageLimit, canonicalID: conversationCanonicalID))
        return outcomes.isEmpty
            ? .failed(.workerFailed(state: "no_outcome"))
            : outcomes.removeFirst()
    }

    func archiveConversations() async -> ArchiveConversationDiscoveryOutcome {
        discoveryOutcomes.isEmpty ? .ready([]) : discoveryOutcomes.removeFirst()
    }
}

private actor HeldEvidenceRunner: AnswerEvidenceRunning {
    private(set) var calls = 0
    private var completion: CheckedContinuation<AnswerEvidenceOutcome, Never>?
    private var started: CheckedContinuation<Void, Never>?

    func answerEvidence(
        start: Date, end: Date, messageLimit: Int
    ) async -> AnswerEvidenceOutcome {
        calls += 1
        return await withCheckedContinuation { continuation in
            completion = continuation
            started?.resume()
            started = nil
        }
    }
    func waitUntilStarted() async {
        if completion == nil {
            await withCheckedContinuation { started = $0 }
        }
    }
    func finish(_ outcome: AnswerEvidenceOutcome) {
        completion?.resume(returning: outcome)
        completion = nil
    }
}

/// An actor, not a mutable struct: a stub that hangs inside its answer call
/// would otherwise mutate recorded state from two places at once.
private actor StubAnswerRunner: AnswerRunning {
    var returnedIndices: [Int] = [1]
    private let hangs: Bool
    private(set) var callCount = 0
    private(set) var lastSnapshot: AnswerEvidenceSnapshot?
    private(set) var lastQuestion: String?

    init(returnedIndices: [Int] = [1], hangs: Bool = false) {
        self.returnedIndices = returnedIndices
        self.hangs = hangs
    }

    nonisolated func currentAvailability() -> AnswerRuntimeAvailability {
        .available(contextSize: 8_192)
    }

    func answer(
        question: String, snapshot: AnswerEvidenceSnapshot
    ) async throws -> AnswerRunResult {
        callCount += 1
        lastQuestion = question
        lastSnapshot = snapshot
        if hangs {
            try await Task.sleep(for: .seconds(600))
        }
        return try AnswerRunResult.validating(
            answer: "回答",
            disposition: AnswerDisposition.answered.rawValue,
            returnedIndices: returnedIndices,
            input: AnswerModelInput.build(
                question: question, rows: snapshot.rows, contextSize: 8_192
            )
        )
    }
}

/// Mirrors the production runner's real cancellation boundary: the production
/// code catches a cancellation coming out of the model and rethrows it as a
/// domain failure, so a stub that throws a *raw* CancellationError never
/// reaches the same code path. This one throws what production actually
/// throws, which is the whole point of defect A.
private actor ProductionShapedCancellingRunner: AnswerRunning {
    nonisolated func currentAvailability() -> AnswerRuntimeAvailability {
        .available(contextSize: 8_192)
    }

    func answer(
        question: String, snapshot: AnswerEvidenceSnapshot
    ) async throws -> AnswerRunResult {
        // The production mapping, verbatim: the model call is cancelled, and
        // production converts that into a *domain* failure. A stub that threw a
        // raw CancellationError never reached this boundary, which is how the
        // defect survived the earlier cancel test.
        do {
            try await Task.sleep(for: .seconds(600))
        } catch is CancellationError {
            throw AnswerFailure.cancelled
        }
        return try AnswerRunResult.validating(
            answer: "回答",
            disposition: AnswerDisposition.answered.rawValue,
            returnedIndices: [],
            input: AnswerModelInput.build(question: question, rows: [], contextSize: 8_192)
        )
    }
}

/// A runner that can be released *by hand* at a moment the test chooses, so a
/// cancelled run can be observed still being alive after Cancel returns. This
/// is what defect B needs and what the old cancel test could not express.
private actor ControllableAnswerRunner: AnswerRunning {
    private(set) var callCount = 0
    private(set) var finishedCalls = 0
    /// One gate per call, oldest first. A single slot would let a second,
    /// overlapping run overwrite the first run's continuation and strand it
    /// forever -- exactly the overlap this runner exists to observe, so the
    /// harness must not reproduce it as an artefact of its own shape.
    private var gates: [CheckedContinuation<Void, Never>] = []
    private var arrivalWaiters: [CheckedContinuation<Void, Never>] = []
    private var nextIndex = 0

    nonisolated func currentAvailability() -> AnswerRuntimeAvailability {
        .available(contextSize: 8_192)
    }

    func answer(
        question: String, snapshot: AnswerEvidenceSnapshot
    ) async throws -> AnswerRunResult {
        callCount += 1
        nextIndex += 1
        let index = nextIndex
        await withCheckedContinuation { (c: CheckedContinuation<Void, Never>) in
            gates.append(c)
            for waiter in arrivalWaiters { waiter.resume() }
            arrivalWaiters.removeAll()
        }
        finishedCalls += 1
        return try AnswerRunResult.validating(
            answer: "回答-\(index)",
            disposition: AnswerDisposition.answered.rawValue,
            returnedIndices: [],
            input: AnswerModelInput.build(question: question, rows: [], contextSize: 8_192)
        )
    }

    func waitForCallCount(_ count: Int) async {
        while callCount < count {
            await withCheckedContinuation { arrivalWaiters.append($0) }
        }
    }

    /// Let every parked call return, as if the runtime had finished unwinding.
    func releaseAll() {
        for gate in gates { gate.resume() }
        gates.removeAll()
    }
}

@MainActor
private func answerTestDefaults(_ consent: Bool = true) -> UserDefaults {
    let defaults = UserDefaults(suiteName: "OnDeviceAnswerTests-\(UUID().uuidString)")!
    defaults.set(consent, forKey: AppModel.localPersistenceConsentKey)
    defaults.removeObject(forKey: AppModel.remoteConsentKey)
    return defaults
}

/// The exact input a run would have built for these rows.
private func modelInput(
    _ rows: [AnswerEvidenceRow], question: String = "问题", contextSize: Int = 8_192
) -> AnswerModelInput {
    AnswerModelInput.build(question: question, rows: rows, contextSize: contextSize)
}

@Test("Archive snapshot selection is ephemeral and defaults to all snapshots")
@MainActor
func archiveSnapshotSelectionClearsOnAllSnapshotsAndHasNoPersistenceState() {
    let model = makeAnswerModel(
        evidence: RecordingEvidenceRunner(outcomes: []),
        answer: StubAnswerRunner()
    )
    #expect(model.selectedArchiveConversationID == nil)

    let snapshot = ArchiveSnapshot(
        id: "conv:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        importID: 1,
        label: "Imported Archive snapshot",
        firstSeenAt: Date(timeIntervalSince1970: 100),
        lastSeenAt: Date(timeIntervalSince1970: 100)
    )
    model.setAnswerConversation(snapshot)
    #expect(model.selectedArchiveConversationID == snapshot.id)
    model.setAnswerConversation(nil)
    #expect(model.selectedArchiveConversationID == nil)
    #expect(model.answerPhase == .idle)
    #expect(model.answerResult == nil)
    #expect(model.answerSnapshot == nil)
}

@MainActor
private func makeAnswerModel(
    evidence: any AnswerEvidenceRunning,
    answer: any AnswerRunning,
    consent: Bool = true,
    history: LocalMessageHistory = makeTestMessageHistory()
) -> AppModel {
    AppModel(
        messageHistory: history,
        shareInbox: nil,
        consentDefaults: answerTestDefaults(consent),
        memorySync: UnavailableMemorySyncRunner(),
        dailySummary: UnavailableDailySummaryRunner(),
        followUpCandidates: UnavailableFollowUpRunner(),
        answerEvidence: evidence,
        answerRunner: answer
    )
}

/// Availability that can change mid-session, which is the whole point: the
/// sealed stubs above pin one value forever, and a runner whose availability is
/// decided once at construction cannot express modelNotReady -> available.
/// Unchecked Sendable rather than an actor because currentAvailability() is
/// nonisolated and must stay the cheap synchronous read production makes; every
/// mutation here happens on the main actor in these tests.
private final class MutableAvailabilityRunner: AnswerRunning, @unchecked Sendable {
    private var availability: AnswerRuntimeAvailability
    private(set) var availabilityQueries = 0
    private(set) var answerCalls = 0

    init(_ availability: AnswerRuntimeAvailability) {
        self.availability = availability
    }

    func setAvailability(_ next: AnswerRuntimeAvailability) {
        availability = next
    }

    func currentAvailability() -> AnswerRuntimeAvailability {
        availabilityQueries += 1
        return availability
    }

    func answer(
        question: String, snapshot: AnswerEvidenceSnapshot
    ) async throws -> AnswerRunResult {
        answerCalls += 1
        return try AnswerRunResult.validating(
            answer: "回答",
            disposition: AnswerDisposition.answered.rawValue,
            returnedIndices: [1],
            input: AnswerModelInput.build(
                question: question, rows: snapshot.rows, contextSize: 8_192
            )
        )
    }
}

// MARK: - Runtime input shaping

@Suite("On-device answer runtime input")
struct OnDeviceAnswerRuntimeInputTests {
    @Test("Model-visible text is capped at 150 characters per row")
    func capsRowText() {
        let long = String(repeating: "字", count: 400)
        let input = AnswerModelInput.build(
            question: "问题",
            rows: [evidenceRow(1, text: long)],
            contextSize: 8_192
        )
        #expect(input.rows.count == 1)
        #expect(input.rows[0].text.count == 150)
        #expect(input.rowsTextShortened == 1)
    }

    @Test("At most 40 rows reach the model, oldest first")
    func capsRowCount() {
        let rows = (1...70).map { evidenceRow($0, text: "消息\($0)") }
        let input = AnswerModelInput.build(
            question: "问题",
            rows: rows,
            contextSize: 8_192
        )
        #expect(input.rows.count == 40)
        #expect(input.rowsOmitted == 30)
        #expect(input.rows.first?.token == 1)
        #expect(input.rows.last?.token == 40)
    }

    @Test("A smaller context trims rows further than the 40-row ceiling")
    func respectsContextSize() {
        let rows = (1...40).map {
            evidenceRow($0, text: String(repeating: "字", count: 150))
        }
        let roomy = AnswerModelInput.build(
            question: String(repeating: "问", count: 100), rows: rows, contextSize: 16_000
        )
        #expect(roomy.rows.count == 40)

        let cramped = AnswerModelInput.build(
            question: "问题", rows: rows, contextSize: 1_200
        )
        #expect(cramped.rows.count < 40)
        #expect(cramped.rowsOmitted > 0)
    }

    @Test("The 150-character row cap is what stops a 40-row set from fitting")
    func rowCapHoldsEvenInARoomyContext() {
        // 40 rows of 150 characters plus their labels is ~6.8k characters,
        // which does not fit an 8k-token context once the reserve is held
        // back. The answer is fewer rows, never a longer row: the per-row cap
        // is a hard ceiling, not a suggestion the budget may raise.
        let rows = (1...40).map {
            evidenceRow($0, text: String(repeating: "字", count: 150))
        }
        let input = AnswerModelInput.build(question: "问题", rows: rows, contextSize: 8_192)
        #expect(input.rows.count < 40)
        #expect(input.rows.allSatisfy { $0.text.count <= 150 })
        #expect(input.rowsOmitted == 40 - input.rows.count)
    }

    @Test("Prompt carries host tokens and never canonical identity")
    func promptCarriesTokensOnly() {
        let input = AnswerModelInput.build(
            question: "会议改到什么时候了？",
            rows: [evidenceRow(1, text: "改到周四上午十点了")],
            contextSize: 8_192
        )
        #expect(input.prompt.contains("[1]"))
        #expect(input.prompt.contains("改到周四上午十点了"))
        for secret in ["msg-1", "conv-1", "import_id", "canonical", "sequence"] {
            #expect(!input.prompt.contains(secret))
        }
    }

    @Test("The runtime-input disclosure stays separate from B9 truncation")
    func disclosureIsSeparate() {
        let snapshot = evidenceSnapshot(
            rows: (1...70).map { evidenceRow($0, text: "消息\($0)") },
            truncated: true,
            excludedUnanchored: 4,
            textTruncatedCount: 2
        )
        let input = AnswerModelInput.build(
            question: "问题", rows: snapshot.rows, contextSize: 8_192
        )
        let disclosure = AnswerEvidenceDisclosure(snapshot: snapshot, modelInput: input)
        #expect(disclosure.evidenceTruncated)
        #expect(disclosure.excludedUnanchored == 4)
        #expect(disclosure.workerTextTruncated == 2)
        #expect(disclosure.modelInputTruncated)
        #expect(disclosure.rowsOmittedForContext == 30)
        #expect(disclosure.rowsTextShortenedForContext == 0)
    }
}

// MARK: - Citation validation

@Suite("On-device answer citations")
struct OnDeviceAnswerCitationTests {
    @Test("Valid indices map to the sealed anchors in order, de-duplicated")
    func mapsValidIndices() throws {
        let rows = (1...3).map { evidenceRow($0, text: "消息\($0)") }
        let result = try AnswerRunResult.validating(
            answer: "回答",
            disposition: AnswerDisposition.answered.rawValue,
            returnedIndices: [2, 2, 3],
            input: modelInput(rows)
        )
        #expect(result.citations.map(\.archiveEvidence) == [
            ArchiveEvidenceAnchor(importID: 7, sequence: 2),
            ArchiveEvidenceAnchor(importID: 7, sequence: 3),
        ])
        #expect(result.hasVerifiableSource)
    }

    @Test("Out-of-range indices are dropped")
    func dropsOutOfRange() throws {
        let rows = (1...2).map { evidenceRow($0, text: "消息\($0)") }
        let result = try AnswerRunResult.validating(
            answer: "回答",
            disposition: AnswerDisposition.answered.rawValue,
            returnedIndices: [0, -1, 3, 99, 1],
            input: modelInput(rows)
        )
        #expect(result.citations.map(\.token) == [1])
    }

    @Test("All-invalid citations leave the answer explicitly unverified")
    func allInvalidIsUnverified() throws {
        let rows = (1...2).map { evidenceRow($0, text: "消息\($0)") }
        let result = try AnswerRunResult.validating(
            answer: "回答",
            disposition: AnswerDisposition.answered.rawValue,
            returnedIndices: [7, 9],
            input: modelInput(rows)
        )
        #expect(result.citations.isEmpty)
        #expect(!result.hasVerifiableSource)
    }

    @Test("A citation exposes its own row's sender, time and text")
    func citationCarriesRow() throws {
        let rows = [evidenceRow(4, text: "原文", sender: "王经理")]
        let result = try AnswerRunResult.validating(
            answer: "回答",
            disposition: AnswerDisposition.answered.rawValue,
            returnedIndices: [1],
            input: modelInput(rows)
        )
        let citation = result.citations[0]
        #expect(citation.sender == "王经理")
        #expect(citation.text == "原文")
        #expect(citation.timestamp == rows[0].timestamp)
    }

    @Test("A citation naming a row dropped for context is dropped, not resolved")
    func omittedRowIsNotCitable() throws {
        // 70 rows, 40 supplied. Token 55 exists in the sealed window and is
        // the most tempting wrong answer: resolving it would reveal a row the
        // model was never shown.
        let rows = (1...70).map { evidenceRow($0, text: "消息\($0)") }
        let input = modelInput(rows)
        #expect(input.rows.count == 40)
        let result = try AnswerRunResult.validating(
            answer: "回答",
            disposition: AnswerDisposition.answered.rawValue,
            returnedIndices: [55, 1],
            input: input
        )
        #expect(result.citations.map(\.token) == [1])
        #expect(result.citations[0].archiveEvidence == ArchiveEvidenceAnchor(importID: 7, sequence: 1))
    }

    @Test("Distinct citations carry distinct anchors and distinct reveal requests")
    func distinctCitationsRevealDistinctRows() throws {
        let rows = (1...3).map { evidenceRow($0, text: "消息\($0)") }
        let result = try AnswerRunResult.validating(
            answer: "回答",
            disposition: AnswerDisposition.answered.rawValue,
            returnedIndices: [1, 3],
            input: modelInput(rows)
        )
        #expect(result.citations.count == 2)
        let targets: [ArchiveEvidenceAnchor?] = result.citations.map {
            if case let .archiveRecord(importID, sequence, _) = $0.searchResult.target {
                ArchiveEvidenceAnchor(importID: importID, sequence: sequence)
            } else {
                nil
            }
        }
        #expect(targets == [
            ArchiveEvidenceAnchor(importID: 7, sequence: 1),
            ArchiveEvidenceAnchor(importID: 7, sequence: 3),
        ])
        #expect(Set(result.citations.map(\.id)).count == 2)
    }

    @Test("A citation reveals through the one existing search-result path")
    func citationUsesCanonicalRevealShape() throws {
        let rows = [evidenceRow(4, text: "原文")]
        let citation = try AnswerRunResult.validating(
            answer: "回答",
            disposition: AnswerDisposition.answered.rawValue,
            returnedIndices: [1],
            input: modelInput(rows)
        ).citations[0]
        #expect(citation.searchResult.target == .archiveRecord(
            importID: 7, sequence: 4, provenance: .archiveAttributed
        ))
        #expect(citation.searchResult.provenance == .archiveAttributed)
        #expect(citation.searchResult.conversationLabel == "Imported archive export")
    }
}

// MARK: - Grounding disposition

/// The v1 contract had no way to tell an honest refusal from an unsupported
/// claim: both were `citations.isEmpty`. The disposition token is the model's
/// own machine-readable statement about whether the supplied evidence answered
/// the question, and the host validates it exactly -- never inferred from the
/// answer prose, and never inferred from the citation array alone.
@Suite("On-device answer grounding disposition")
struct OnDeviceAnswerDispositionTests {
    private func validated(
        _ disposition: String,
        indices: [Int],
        rows: [AnswerEvidenceRow] = [evidenceRow(1, text: "消息1"), evidenceRow(2, text: "消息2")]
    ) throws -> AnswerRunResult {
        try AnswerRunResult.validating(
            answer: "回答",
            disposition: disposition,
            returnedIndices: indices,
            input: modelInput(rows)
        )
    }

    @Test("Answered with a valid citation is grounded")
    func answeredIsGrounded() throws {
        let result = try validated(AnswerDisposition.answered.rawValue, indices: [2])
        #expect(result.disposition == .answered)
        #expect(result.hasVerifiableSource)
        #expect(result.citations.map(\.token) == [2])
    }

    @Test("Answered with no citation is answered but unverified")
    func answeredWithoutCitation() throws {
        let result = try validated(AnswerDisposition.answered.rawValue, indices: [])
        #expect(result.disposition == .answered)
        #expect(!result.hasVerifiableSource)
        // Grounding is a fact about citations, never a fact about disposition.
        #expect(result.hasVerifiableSource == !result.citations.isEmpty)
    }

    @Test("Answered with only invalid indices keeps the disposition and loses the citations")
    func answeredWithAllIndicesInvalid() throws {
        let result = try validated(AnswerDisposition.answered.rawValue, indices: [0, -1, 7, 99])
        #expect(result.disposition == .answered)
        #expect(result.citations.isEmpty)
        #expect(!result.hasVerifiableSource)
    }

    @Test("An honest refusal is accepted with no citations and no integrity warning")
    func refusalWithEmptyCitations() throws {
        let result = try validated(AnswerDisposition.insufficientEvidence.rawValue, indices: [])
        #expect(result.disposition == .insufficientEvidence)
        #expect(result.citations.isEmpty)
        #expect(!result.hasVerifiableSource)
    }

    @Test(
        "A refusal that also cites is refused before the indices are dropped",
        arguments: [[1], [99], [0], [1, 99], [-1, 2]]
    )
    func refusalWithAnyCitationFailsClosed(indices: [Int]) {
        // Dropping invalid indices first would turn `insufficientEvidence` plus
        // `[999]` into a clean refusal, silently repairing a model that broke
        // the contract in the one way the user cannot see.
        #expect(throws: AnswerFailure.self) {
            try AnswerRunResult.validating(
                answer: "回答",
                disposition: AnswerDisposition.insufficientEvidence.rawValue,
                returnedIndices: indices,
                input: modelInput([evidenceRow(1, text: "消息1")])
            )
        }
    }

    @Test("An unknown disposition token fails closed with no default")
    func unknownDispositionFailsClosed() throws {
        #expect(throws: AnswerFailure.self) {
            try AnswerRunResult.validating(
                answer: "回答",
                disposition: "probably_fine",
                returnedIndices: [],
                input: modelInput([evidenceRow(1, text: "消息1")])
            )
        }
    }

    @Test("The instruction budget covers the disposition clause")
    func dispositionInstructionIsBudgeted() {
        #expect(answerInstructions.contains("insufficientEvidence"))
        // The question is the hard boundary, and the instruction text is part
        // of what it is measured against.
        #expect(!AnswerModelInput.fitsQuestion(String(repeating: "问", count: 9_000), contextSize: 8_192))
        #expect(AnswerModelInput.fitsQuestion("问题", contextSize: 8_192))
    }

    @Test("The prompt still carries tokens only, never canonical identity")
    func promptStaysIdentityBlind() {
        let input = modelInput([evidenceRow(1, text: "原文")])
        for secret in ["msg-1", "conv-1", "importID", "import_id", "sequence", "canonical"] {
            #expect(!input.prompt.contains(secret))
        }
    }
}

/// Coverage is host-known, so it is presented from data the snapshot already
/// carries. Nothing here asks the model anything, and nothing here weakens the
/// narrower claim Memory coverage actually supports.
@Suite("On-device answer coverage disclosure")
struct OnDeviceAnswerCoverageDisclosureTests {
    private func lines(
        rows: [AnswerEvidenceRow],
        coverage: FollowUpCoverage
    ) -> [String] {
        AnswerCoverageDisclosure(
            coverage: coverage,
            rowCount: rows.count
        ).lines
    }

    @Test("A raw coverage status never reaches the surface")
    func humanReadableStatus() {
        for (status, expected) in [
            ("complete", "Complete"),
            ("partial", "Partial"),
            ("unavailable", "Unavailable"),
            ("not_observed", "Not observed"),
        ] {
            // The label is what the Coverage row renders, so it is asserted on
            // the label itself rather than on the caveat lines beside it.
            let label = AnswerCoverageDisclosure(
                coverage: FollowUpCoverage(
                    status: status, trustworthyEmpty: true, caveats: []
                ),
                rowCount: 1
            ).statusLabel
            #expect(label == expected)
            if status != "complete" {
                #expect(!label.contains(status))
                #expect(!label.contains("_"))
            }
            #expect(!label.isEmpty)
            let rendered = lines(
                rows: [evidenceRow(1, text: "消息")],
                coverage: FollowUpCoverage(
                    status: status, trustworthyEmpty: true, caveats: []
                )
            )
            #expect(!rendered.contains { $0.contains("_") })
        }
    }

    @Test("An untrustworthy empty window does not read as no messages happened")
    func untrustworthyEmptyIsNotAbsence() {
        let rendered = lines(
            rows: [],
            coverage: FollowUpCoverage(
                status: "partial", trustworthyEmpty: false, caveats: []
            )
        )
        #expect(rendered.contains { $0.contains("no messages happened") })
        #expect(rendered.contains { $0.contains("Coverage is not complete") })
    }

    @Test("A caveat reason token never reaches the answer panel")
    func caveatTokensUseTheConsumerMapping() {
        let rendered = lines(
            rows: [evidenceRow(1, text: "消息")],
            coverage: FollowUpCoverage(
                status: "partial",
                trustworthyEmpty: true,
                caveats: ["archive:memory_store_missing", "archive:partial", "Some days are unindexed."]
            )
        )
        #expect(!rendered.contains { $0.contains("memory_store_missing") })
        #expect(!rendered.contains { $0.contains("Memory Store Missing") })
        #expect(rendered.contains("Imported WeChat archives: Coverage could not be fully confirmed."))
        #expect(rendered.contains("Imported WeChat archives: Partial"))
        // Free-text caveats are already prose and pass through unchanged.
        #expect(rendered.contains("Some days are unindexed."))
    }

    @Test("A covered empty window says the window holds nothing")
    func trustworthyEmptyIsAbsence() {
        let rendered = lines(
            rows: [],
            coverage: FollowUpCoverage(
                status: "complete", trustworthyEmpty: true, caveats: []
            )
        )
        #expect(rendered.contains { $0.contains("no stored messages") })
        #expect(!rendered.contains { $0.contains("no messages happened") })
    }

    @Test("Caveats are surfaced individually and stay readable")
    func caveatsAreSurfaced() {
        let rendered = lines(
            rows: [evidenceRow(1, text: "消息")],
            coverage: FollowUpCoverage(
                status: "partial",
                trustworthyEmpty: false,
                caveats: ["Archive import is still running.", "Some days are unindexed."]
            )
        )
        #expect(rendered.contains("Archive import is still running."))
        #expect(rendered.contains("Some days are unindexed."))
    }

    @Test("A covered window with rows adds no absence claim")
    func populatedWindowStaysQuiet() {
        let rendered = lines(
            rows: [evidenceRow(1, text: "消息")],
            coverage: FollowUpCoverage(
                status: "complete", trustworthyEmpty: true, caveats: []
            )
        )
        #expect(!rendered.contains { $0.contains("no messages happened") })
        #expect(!rendered.contains { $0.contains("no stored messages") })
    }

    @Test("Coverage never claims more than Memory observed")
    func coverageDoesNotOverclaim() {
        let rendered = lines(
            rows: [],
            coverage: FollowUpCoverage(
                status: "not_observed", trustworthyEmpty: false, caveats: []
            )
        )
        for line in rendered {
            #expect(!line.lowercased().contains("wechat was fully"))
            #expect(!line.lowercased().contains("complete coverage of all"))
        }
    }
}

#if canImport(FoundationModels)
import FoundationModels

/// The generated contract itself, checked without a model: a payload that
/// omits `disposition` must not decode into a usable answer, because the host
/// has no second source to fall back on.
@Suite(
    "On-device generated answer contract",
    .enabled(
        if: ProcessInfo.processInfo.isOperatingSystemAtLeast(
            OperatingSystemVersion(majorVersion: 26, minorVersion: 0, patchVersion: 0)
        )
    )
)
struct GeneratedArchiveAnswerContractTests {
    @available(macOS 26.0, *)
    static func decode(_ json: String) throws -> GeneratedArchiveAnswer {
        try GeneratedArchiveAnswer(GeneratedContent(json: json))
    }

    @Test("A payload without disposition fails to decode")
    func missingDispositionFailsDecode() throws {
        guard #available(macOS 26.0, *) else { return }
        do {
            _ = try Self.decode(#"{"answer": "回答", "citations": [1]}"#)
            Issue.record("a payload without disposition must not decode")
        } catch {}
    }

    @Test("A payload with a closed disposition decodes")
    func closedDispositionDecodes() throws {
        guard #available(macOS 26.0, *) else { return }
        let generated = try Self.decode(
            #"{"answer": "回答", "citations": [1], "disposition": "answered"}"#
        )
        #expect(generated.disposition == AnswerDisposition.answered.rawValue)
    }
}
#endif

// MARK: - Architectural boundaries

/// The negative conditions are cheaper to state as source facts than to prove
/// by injecting a network transport that must never be reachable.
@Suite("On-device answer architectural boundary")
struct OnDeviceAnswerBoundaryTests {
    private static var sourceRoot: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()          // WeChatCompanionTests
            .deletingLastPathComponent()          // WeChatCompanion
            .appendingPathComponent("WeChatCompanion")
    }

    private func source(_ name: String) -> String {
        (try? String(contentsOf: Self.sourceRoot.appendingPathComponent(name), encoding: .utf8))
            ?? ""
    }

    /// The Agents section of AppModel.swift, bounded by the next MARK so a
    /// later section cannot silently satisfy (or break) these assertions.
    private var agentsSection: String {
        let appModel = source("AppModel.swift")
        let start = "MARK: - Agents: on-device archive answer"
        let end = "MARK: - Local persistence settings"
        guard let from = appModel.range(of: start),
              let to = appModel.range(of: end, range: from.upperBound..<appModel.endIndex)
        else { return "" }
        return String(appModel[from.upperBound..<to.lowerBound])
    }

    @Test("The answer path names no credential, network or remote provider")
    func noProviderOrNetworkInAnswerPath() {
        let runner = source("OnDeviceAnswerRunner.swift")
        for forbidden in [
            "URLSession", "Gemini", "Anthropic", "Keychain", "CredentialStoring",
            "PrivateCloudComputeLanguageModel", "URLRequest",
        ] {
            #expect(!runner.contains(forbidden))
        }
    }

    @Test("The app asks only the sealed evidence window and reveals only by search result")
    func answerPathUsesSealedSeamsOnly() {
        let body = agentsSection
        guard !body.isEmpty else {
            Issue.record("The Agents section marker is missing from AppModel.swift")
            return
        }
        #expect(body.contains("answerEvidence.answerEvidenceScoped("))
        #expect(body.contains("openSearchResult("))
        // The answer path must not reach the Memory store, search, sync,
        // summary, follow-up or reminder seams directly.
        for forbidden in [
            "messageHistory.", "localSearch", "memorySync.", "dailySummary.",
            "followUpCandidates.", "reminderStore.",
        ] {
            #expect(!body.contains(forbidden))
        }
    }

    @Test("The model runner never writes the question, answer or citations down")
    func nothingPersisted() {
        for text in [source("OnDeviceAnswerRunner.swift"), agentsSection, agentsView] {
            for forbidden in ["UserDefaults", "FileManager", "write(", "JSONEncoder"] {
                #expect(!text.contains(forbidden))
            }
        }
    }

    @Test("The prompt builder reads only presentation fields, never an anchor")
    func promptNeverCarriesCanonicalIdentity() {
        let runner = source("OnDeviceAnswerRunner.swift")
        // The evidence window, whose rows do carry canonical identity, is not
        // constructed anywhere in the prompt path: labels are rebuilt from
        // the run-local row instead.
        #expect(!runner.contains("row: AnswerEvidenceRow) -> String"))
        #expect(runner.contains("label(token: Int, sender: String?, at timestamp: Date)"))
        // The Agents view must not render an anchor as an identifier either.
        #expect(!agentsView.contains("importID"))
        #expect(!agentsView.contains("sequence"))
    }

    private var agentsView: String {
        let content = source("ContentView.swift")
        let start = "private struct AgentsView: View"
        let end = "private struct PlaceholderView: View"
        guard let from = content.range(of: start),
              let to = content.range(of: end, range: from.upperBound..<content.endIndex)
        else { return "" }
        return String(content[from.upperBound..<to.lowerBound])
    }
}

// MARK: - AppModel state

@MainActor
@Suite("On-device answer AppModel state")
struct OnDeviceAnswerAppModelTests {
    @Test("Ask reads evidence through the sealed runner with a real window")
    func asksThroughSealedRunner() async {
        let evidence = RecordingEvidenceRunner(outcomes: [
            .ready(evidenceSnapshot(rows: [evidenceRow(1, text: "原文")]))
        ])
        let model = makeAnswerModel(evidence: evidence, answer: StubAnswerRunner())
        await model.askArchiveQuestion(
            "问题？", now: Date(timeIntervalSince1970: 1_700_003_600)
        )
        #expect(evidence.calls.count == 1)
        #expect(evidence.calls[0].messageLimit > 0)
        #expect(evidence.calls[0].end > evidence.calls[0].start)
    }

    @Test("An empty question never reads evidence")
    func emptyQuestionNoRun() async {
        let evidence = RecordingEvidenceRunner(outcomes: [])
        let model = makeAnswerModel(evidence: evidence, answer: StubAnswerRunner())
        await model.askArchiveQuestion("   \n ", now: Date())
        #expect(evidence.calls.isEmpty)
        #expect(model.answerPhase == .idle)
    }

    @Test("Asking while a run is in flight starts no second run")
    func singleFlight() async {
        let held = HeldEvidenceRunner()
        let model = makeAnswerModel(evidence: held, answer: StubAnswerRunner())
        let now = Date(timeIntervalSince1970: 1_700_003_600)
        let first = Task { await model.askArchiveQuestion("第一个？", now: now) }
        await held.waitUntilStarted()
        await model.askArchiveQuestion("第二个？", now: now)
        #expect(await held.calls == 1)
        await held.finish(.failed(.workerFailed(state: "worker_unavailable")))
        await first.value
    }

    @Test("An unavailable runtime never reads evidence")
    func unavailableSkipsEvidence() async {
        let evidence = RecordingEvidenceRunner(outcomes: [
            .ready(evidenceSnapshot(rows: [evidenceRow(1, text: "原文")]))
        ])
        // The stub now reports unavailable for real, rather than being told so
        // through the cached copy: production re-reads the runtime before a run,
        // so a value injected behind the runtime's back no longer describes it.
        let runner = MutableAvailabilityRunner(.unavailable(.appleIntelligenceNotEnabled))
        let model = makeAnswerModel(evidence: evidence, answer: runner)
        await model.askArchiveQuestion("问题？", now: Date())
        #expect(evidence.calls.isEmpty)
        #expect(runner.answerCalls == 0)
        #expect(model.answerPhase == .failed(.runtimeUnavailable(.appleIntelligenceNotEnabled)))
    }

    @Test("A cancel leaves a cancelled phase and no partial answer")
    func cancelIsNotFailure() async {
        let held = HeldEvidenceRunner()
        let model = makeAnswerModel(evidence: held, answer: StubAnswerRunner())
        let run = Task { await model.askArchiveQuestion("问题？", now: Date()) }
        await held.waitUntilStarted()
        model.cancelArchiveAnswer()
        // The evidence is released *after* the cancel, on purpose: a parked
        // continuation cannot be woken by cancellation, so this is also what
        // proves the late answer cannot overwrite the cancelled phase.
        await held.finish(.ready(evidenceSnapshot(rows: [evidenceRow(1, text: "原文")])))
        await run.value
        #expect(model.answerPhase == .cancelled)
        #expect(model.answerResult == nil)
    }

    @Test("A run that outlives the watchdog times out rather than hanging")
    func timesOut() async {
        let evidence = RecordingEvidenceRunner(outcomes: [
            .ready(evidenceSnapshot(rows: [evidenceRow(1, text: "原文")]))
        ])
        let model = makeAnswerModel(evidence: evidence, answer: StubAnswerRunner(hangs: true))
        model.answerTimeout = 0.15
        await model.askArchiveQuestion("问题？", now: Date())
        #expect(model.answerPhase == .timedOut)
        #expect(model.answerResult == nil)
    }

    @Test("A withheld consent reports the sealed window's own failure, not a new one")
    func consentFailureReusesSealedVocabulary() async {
        let evidence = RecordingEvidenceRunner(outcomes: [.failed(.consentWithheld)])
        let model = makeAnswerModel(evidence: evidence, answer: StubAnswerRunner())
        await model.askArchiveQuestion("问题？", now: Date())
        #expect(model.answerPhase == .failed(.evidence(.consentWithheld)))
    }

    @Test("Asking starts no sync, summary or follow-up scan")
    func asksNothingElse() async {
        let evidence = RecordingEvidenceRunner(outcomes: [
            .ready(evidenceSnapshot(rows: [evidenceRow(1, text: "原文")]))
        ])
        let model = makeAnswerModel(evidence: evidence, answer: StubAnswerRunner())
        await model.askArchiveQuestion("问题？", now: Date())
        #expect(model.memorySyncPhase == .idle)
        #expect(model.dailySummaryPhase == .idle)
        #expect(model.followUpPhase == .idle)
        #expect(model.dailySummarySnapshot == nil)
        #expect(model.followUpSnapshot == nil)
    }

    @Test("A completed answer survives leaving and returning to the tab")
    func resultSurvivesNavigation() async {
        let evidence = RecordingEvidenceRunner(outcomes: [
            .ready(evidenceSnapshot(rows: [evidenceRow(1, text: "原文")]))
        ])
        let model = makeAnswerModel(evidence: evidence, answer: StubAnswerRunner())
        await model.askArchiveQuestion("问题？", now: Date())
        model.selectedDestination = .chats
        model.selectedDestination = .agents
        #expect(model.answerPhase == .answered)
        #expect(model.answerResult?.answer == "回答")
    }

    @Test("Two citations reveal their own exact rows through the same path")
    func twoCitationsRevealOwnRows() async {
        let rows = (1...3).map { evidenceRow($0, text: "消息\($0)") }
        let evidence = RecordingEvidenceRunner(outcomes: [.ready(evidenceSnapshot(rows: rows))])
        let model = makeAnswerModel(
            evidence: evidence, answer: StubAnswerRunner(returnedIndices: [1, 3])
        )
        await model.askArchiveQuestion("问题？", now: Date())
        let citations = try! #require(model.answerResult?.citations)
        #expect(citations.count == 2)
        await model.openArchiveAnswerCitation(citations[0])
        #expect(model.answerRevealTarget == ArchiveEvidenceAnchor(importID: 7, sequence: 1))
        await model.openArchiveAnswerCitation(citations[1])
        #expect(model.answerRevealTarget == ArchiveEvidenceAnchor(importID: 7, sequence: 3))
    }

    @Test("Each runtime reason reads as itself, never as one blanket message")
    func reasonsStayDistinct() {
        let reasons: [AnswerRuntimeUnavailableReason] = [
            .frameworkUnavailable, .deviceNotEligible,
            .appleIntelligenceNotEnabled, .modelNotReady,
        ]
        #expect(Set(reasons.map(\.message)).count == reasons.count)
        #expect(!reasons.map(\.message).contains {
            $0.localizedCaseInsensitiveContains("api key")
                || $0.localizedCaseInsensitiveContains("remote")
        })
    }

    @Test("A successful run keeps its answer and citation anchors in memory only")
    func answeredKeepsResult() async {
        let rows = (1...2).map { evidenceRow($0, text: "消息\($0)") }
        let evidence = RecordingEvidenceRunner(outcomes: [.ready(evidenceSnapshot(rows: rows))])
        let model = makeAnswerModel(evidence: evidence, answer: StubAnswerRunner(returnedIndices: [1]))
        await model.askArchiveQuestion(
            "问题？", now: Date(timeIntervalSince1970: 1_700_003_600)
        )
        #expect(model.answerPhase == .answered)
        let result = try! #require(model.answerResult)
        #expect(result.citations.map(\.archiveEvidence) == [
            ArchiveEvidenceAnchor(importID: 7, sequence: 1)
        ])
    }

    @Test("A citation click reveals the exact Archive row through the existing path")
    func citationClickReveals() async {
        // Real reveal, real store: the citation has to be able to name a row
        // that actually exists, or this would only be proving that the anchor
        // survives a dictionary lookup. Mirrors the B8 exact-reveal fixture.
        let history = makeTestMessageHistory()
        await history.setEnabled(true)
        let transcript = try! WeChatNativeTranscriptParser.parse(
            "·林晓\n2026年9月7日 20:35\nanswer-reveal-row\n\n",
            timeZone: TimeZone(identifier: "Asia/Shanghai")!
        )
        let imported = try! await history.persistArchiveEvidence(
            transcript: transcript,
            conversationKey: ArchiveConversationKey("answer-reveal"),
            importedAt: Date()
        )
        guard case .inserted(let importID, _) = imported else {
            Issue.record("expected an archive import")
            return
        }
        let hit = try! #require(
            await history.searchLocalMessages("answer-reveal-row").results.first
        )
        guard case .archiveRecord(let hitImport, let sequence, _) = hit.target else {
            Issue.record("expected an exact archive target")
            return
        }
        let rows = (1...2).map {
            AnswerEvidenceRow(
                canonicalMessageID: "msg-\($0)",
                canonicalConversationID: "conv-1",
                timestamp: Date(timeIntervalSince1970: 1_700_000_000 + Double($0)),
                timestampKind: "source_created",
                sender: "林晓",
                text: "消息\($0)",
                textTruncated: false,
                archiveEvidence: ArchiveEvidenceAnchor(importID: hitImport, sequence: sequence)
            )
        }
        let evidence = RecordingEvidenceRunner(outcomes: [.ready(evidenceSnapshot(rows: rows))])
        let model = makeAnswerModel(
            evidence: evidence,
            answer: StubAnswerRunner(returnedIndices: [2]),
            history: history
        )
        await model.askArchiveQuestion(
            "问题？", now: Date(timeIntervalSince1970: 1_700_003_600)
        )
        let result = try! #require(model.answerResult)
        let citation = try! #require(result.citations.first)
        await model.openArchiveAnswerCitation(citation)
        #expect(model.selectedDestination == .chats)
        #expect(model.answerRevealTarget == ArchiveEvidenceAnchor(importID: hitImport, sequence: sequence))
        #expect(model.selectedArchiveImportID == hitImport)
        #expect(model.selectedArchiveIsHitWindow)
        #expect(model.contextRevealRequest?.anchor
                == .archiveRecord(importID: hitImport, sequence: sequence))
        // Reveal is navigation only: no sync, scan or summary was started.
        #expect(model.memorySyncPhase == .idle)
        #expect(model.dailySummaryPhase == .idle)
        #expect(model.followUpPhase == .idle)
    }

    @Test("Nothing in the answer path writes the question or the answer")
    func persistsNothing() async {
        let defaults = answerTestDefaults()
        let model = AppModel(
            messageHistory: makeTestMessageHistory(),
            shareInbox: nil,
            consentDefaults: defaults,
            memorySync: UnavailableMemorySyncRunner(),
            dailySummary: UnavailableDailySummaryRunner(),
            followUpCandidates: UnavailableFollowUpRunner(),
            answerEvidence: RecordingEvidenceRunner(outcomes: [
                .ready(evidenceSnapshot(rows: [evidenceRow(1, text: "原文")]))
            ]),
            answerRunner: StubAnswerRunner()
        )
        await model.askArchiveQuestion("私密问题", now: Date())
        for (_, value) in defaults.dictionaryRepresentation() {
            let text = String(describing: value)
            #expect(!text.contains("私密问题"))
            #expect(!text.contains("回答"))
        }
    }
}

// MARK: - Run lifecycle

@MainActor
@Suite("On-device answer run lifecycle")
struct OnDeviceAnswerRunLifecycleTests {
    @Test("Production-shaped cancellation ends cancelled, never failed")
    func productionCancellationIsCancelled() async throws {
        let evidence = RecordingEvidenceRunner(outcomes: [
            .ready(evidenceSnapshot(rows: [evidenceRow(1, text: "原文")]))
        ])
        let model = makeAnswerModel(
            evidence: evidence, answer: ProductionShapedCancellingRunner()
        )
        model.answerTimeout = 30
        let run = Task { await model.askArchiveQuestion("问题？", now: Date()) }
        try await Task.sleep(for: .milliseconds(300))
        model.cancelArchiveAnswer()
        await run.value
        #expect(model.answerPhase == .cancelled)
        #expect(model.answerResult == nil)
    }

    @Test("A new Ask cannot start while a cancelled run is still unwinding")
    func leaseBelongsToTheLiveTask() async throws {
        let runner = ControllableAnswerRunner()
        let evidence = RecordingEvidenceRunner(outcomes: [
            .ready(evidenceSnapshot(rows: [evidenceRow(1, text: "原文")])),
            .ready(evidenceSnapshot(rows: [evidenceRow(2, text: "另一条")])),
        ])
        let model = makeAnswerModel(evidence: evidence, answer: runner)
        model.answerTimeout = 30
        let now = Date(timeIntervalSince1970: 1_700_003_600)

        let runA = Task { await model.askArchiveQuestion("A？", now: now) }
        await runner.waitForCallCount(1)
        model.cancelArchiveAnswer()

        let runB = Task { await model.askArchiveQuestion("B？", now: now) }
        try await Task.sleep(for: .milliseconds(300))
        #expect(await runner.callCount == 1)
        #expect(await evidence.calls.count == 1)

        await runner.releaseAll()
        await runA.value
        await runB.value
        #expect(model.answerPhase == .cancelled)
        #expect(model.answerResult == nil)

        let runC = Task { await model.askArchiveQuestion("C？", now: now) }
        await runner.waitForCallCount(2)
        await runner.releaseAll()
        await runC.value
        #expect(model.answerPhase == .answered)
        #expect(model.answerResult?.answer == "回答-2")
    }

    /// One user-visible deadline. The packaged worker's watchdog bounds a
    /// child process and cannot time a run that is inside the model, so the two
    /// are not the same timer: the answer deadline comes first and the worker
    /// kill-switch is a deliberately later ceiling that must never win the
    /// race and report a timeout as an evidence failure.
    @Test("The packaged worker kill-switch sits strictly later than the answer deadline")
    func workerKillSwitchIsLater() {
        let model = makeAnswerModel(
            evidence: UnavailableAnswerEvidenceRunner(), answer: UnavailableAnswerRunner()
        )
        let worker = PackagedMemorySyncRunner(
            workerURL: URL(fileURLWithPath: "/dev/null"),
            storeURL: URL(fileURLWithPath: "/dev/null"),
            messageStoreURL: URL(fileURLWithPath: "/dev/null")
        )
        #expect(worker.answerEvidenceTimeout > model.answerTimeout)
        // The sealed consumers keep the untouched value: a shared timer moving
        // is how a later change would quietly re-time Summary or Reminders.
        #expect(worker.timeout == 120)
    }

    @Test("A run that overruns the deadline is timed out every time, not sometimes failed")
    func nearDeadlineIsDeterministic() async throws {
        for _ in 0..<5 {
            let evidence = RecordingEvidenceRunner(outcomes: [
                .ready(evidenceSnapshot(rows: [evidenceRow(1, text: "原文")]))
            ])
            let model = makeAnswerModel(evidence: evidence, answer: StubAnswerRunner(hangs: true))
            model.answerTimeout = 0.15
            await model.askArchiveQuestion("问题？", now: Date())
            #expect(model.answerPhase == .timedOut)
            #expect(model.answerResult == nil)
        }
    }

    /// The question is part of the hard budget. A question that cannot fit is
    /// refused before anything is read or generated: it is never shortened to
    /// make room, and it never costs a worker round trip.
    @Test("A question too large for the context is refused before any work starts")
    func oversizedQuestionIsRefused() async {
        let evidence = RecordingEvidenceRunner(outcomes: [
            .ready(evidenceSnapshot(rows: [evidenceRow(1, text: "原文")]))
        ])
        let runner = StubAnswerRunner()
        let model = makeAnswerModel(evidence: evidence, answer: runner)
        await model.askArchiveQuestion(
            String(repeating: "问", count: 400_000), now: Date()
        )
        #expect(evidence.calls.isEmpty)
        #expect(await runner.callCount == 0)
        #expect(model.answerResult == nil)
        guard case .failed(let failure) = model.answerPhase else {
            Issue.record("expected a dedicated failure, got \(model.answerPhase)")
            return
        }
    }

    /// The Agents state machine owns its own single deadline and must not
    /// share the worker's unsynchronized flag across execution contexts.
    @Test("The answer state machine shares no watchdog flag with the packaged worker")
    func noSharedExpiryFlag() {
        let appModel = (try? String(
            contentsOf: Self.sourceRoot.appendingPathComponent("AppModel.swift"), encoding: .utf8
        )) ?? ""
        guard !appModel.isEmpty else {
            Issue.record("AppModel.swift could not be read")
            return
        }
        let start = "MARK: - Agents: on-device archive answer"
        guard let from = appModel.range(of: start) else {
            Issue.record("the Agents section marker is missing")
            return
        }
        #expect(!appModel[from.lowerBound...].contains("Expiry("))
    }

    private static var sourceRoot: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("WeChatCompanion")
    }
}

// MARK: - Exact conversation entry & lifecycle

@MainActor
@Suite("On-device answer exact conversation entry and lifecycle")
struct OnDeviceAnswerExactEntryLifecycleTests {
    private func makeAttributedModel(
        importID: Int64,
        shape: ArchiveEvidenceShape = .attributed,
        discoveryOutcomes: [ArchiveConversationDiscoveryOutcome] = []
    ) -> AppModel {
        let model = makeAnswerModel(
            evidence: RecordingEvidenceRunner(discoveryOutcomes: discoveryOutcomes),
            answer: StubAnswerRunner()
        )
        let date = Date(timeIntervalSince1970: 1_700_000_000)
        let summary = ArchiveEvidenceImportSummary(
            id: importID,
            displayName: "Conversation \(importID)",
            shape: shape,
            importedAt: date,
            recordCount: 10,
            firstSentAt: date,
            lastSentAt: date,
            isAnonymous: false,
            link: nil,
            attachmentBatchCount: 0,
            attachmentCount: 0,
            materializedAttachmentCount: 0
        )
        model.setTestArchiveEvidence(imports: [summary], selectedImportID: importID)
        model.selectedDestination = .chats
        return model
    }

    @Test("Chats exact entry navigates immediately and resolves after discovery")
    func chatsExactEntryNavigatesImmediatelyAndResolves() async {
        let snapshot = ArchiveSnapshot(
            id: "conv:55555555555555555555555555555555",
            importID: 5,
            label: "Conversation 5",
            firstSeenAt: Date(timeIntervalSince1970: 100),
            lastSeenAt: Date(timeIntervalSince1970: 200)
        )
        let model = makeAttributedModel(importID: 5, discoveryOutcomes: [.ready([snapshot])])
        #expect(model.archiveSnapshots.isEmpty)

        await model.askAboutCurrentArchiveConversation()
        #expect(model.selectedDestination == .agents)
        #expect(model.questionOrigin == .chats)
        #expect(model.questionTargetImportID == 5)
        #expect(model.questionTargetState == .exactLoading(importID: 5))
        #expect(model.selectedArchiveConversationID == nil)
        #expect(!model.canAskArchiveQuestion)

        await model.loadArchiveSnapshots()
        #expect(model.questionTargetState == .exactReady(snapshot: snapshot))
        #expect(model.selectedArchiveConversationID == snapshot.id)
        #expect(model.canAskArchiveQuestion)
    }

    @Test("Chats exact entry transitions to needs-preparation when 0 matches")
    func chatsExactEntryNeedsPreparationWhenNoMatch() async {
        let otherSnapshot = ArchiveSnapshot(
            id: "conv:22222222222222222222222222222222",
            importID: 2,
            label: "Other",
            firstSeenAt: nil,
            lastSeenAt: nil
        )
        let model = makeAttributedModel(importID: 5, discoveryOutcomes: [.ready([otherSnapshot])])
        await model.askAboutCurrentArchiveConversation()

        await model.loadArchiveSnapshots()
        #expect(model.questionTargetState == .exactNeedsPreparation(importID: 5))
        #expect(model.selectedArchiveConversationID == nil)
        #expect(!model.canAskArchiveQuestion)
    }

    @Test("Discovery failure is distinct from needs-preparation")
    func discoveryFailureIsDistinctFromNeedsPreparation() async {
        let model = makeAttributedModel(
            importID: 5,
            discoveryOutcomes: [.failed(.workerFailed(state: "worker_process_died"))]
        )
        await model.askAboutCurrentArchiveConversation()

        await model.loadArchiveSnapshots()
        guard case .exactUnavailable(let id, let reason) = model.questionTargetState else {
            Issue.record("expected exactUnavailable, got \(model.questionTargetState)")
            return
        }
        #expect(id == 5)
        #expect(reason == .discoveryUnavailable)
        #expect(!reason.message.contains("worker_process_died"))
        #expect(reason.message == "Prepared conversations couldn’t be checked. Try again.")
        #expect(model.selectedArchiveConversationID == nil)
        #expect(!model.canAskArchiveQuestion)
    }

    @Test("Duplicate matches fail closed as unavailable")
    func duplicateMatchesFailClosed() async {
        let snapA = ArchiveSnapshot(
            id: "conv:5555555555555555555555555555555a",
            importID: 5,
            label: "Duplicate A",
            firstSeenAt: nil,
            lastSeenAt: nil
        )
        let snapB = ArchiveSnapshot(
            id: "conv:5555555555555555555555555555555b",
            importID: 5,
            label: "Duplicate B",
            firstSeenAt: nil,
            lastSeenAt: nil
        )
        let model = makeAttributedModel(importID: 5, discoveryOutcomes: [.ready([snapA, snapB])])
        await model.askAboutCurrentArchiveConversation()

        await model.loadArchiveSnapshots()
        guard case .exactUnavailable(let id, let reason) = model.questionTargetState else {
            Issue.record("expected exactUnavailable for duplicates, got \(model.questionTargetState)")
            return
        }
        #expect(id == 5)
        #expect(reason == .ambiguousIdentity)
        #expect(reason.message == "This conversation couldn’t be matched safely.")
        #expect(model.selectedArchiveConversationID == nil)
        #expect(!model.canAskArchiveQuestion)
    }

    @Test("Unresolved exact target cannot execute against All Archive")
    func unresolvedTargetCannotAskAllArchive() async {
        let runner = RecordingEvidenceRunner(discoveryOutcomes: [.ready([])])
        let stub = StubAnswerRunner()
        let model = makeAnswerModel(evidence: runner, answer: stub)
        let summary = ArchiveEvidenceImportSummary(
            id: 5, displayName: "Chat", shape: .attributed,
            importedAt: Date(), recordCount: 1, firstSentAt: nil, lastSentAt: nil,
            isAnonymous: false, link: nil, attachmentBatchCount: 0, attachmentCount: 0,
            materializedAttachmentCount: 0
        )
        model.setTestArchiveEvidence(imports: [summary], selectedImportID: 5)
        model.selectedDestination = .chats
        await model.askAboutCurrentArchiveConversation()
        await model.loadArchiveSnapshots()
        #expect(model.questionTargetState == .exactNeedsPreparation(importID: 5))
        #expect(!model.canAskArchiveQuestion)

        await model.askArchiveQuestion("偷偷提问")
        #expect(runner.calls.isEmpty)
        #expect(await stub.callCount == 0)
    }

    @Test("Question origin produces Back to Chats and preserves reader")
    func questionOriginProducesBackToChats() async {
        let model = makeAttributedModel(importID: 7)
        await model.askAboutCurrentArchiveConversation()
        #expect(model.selectedDestination == .agents)
        #expect(model.questionOrigin == .chats)
        #expect(model.primaryNavigationDestination == .chats)

        model.returnFromNonPrimaryDestination()
        #expect(model.selectedDestination == .chats)
        #expect(model.selectedArchiveImportID == 7)
        #expect(model.questionOrigin == nil)
        #expect(model.questionTargetImportID == nil)
    }

    @Test("Ordinary Settings-origin Questions returns to Settings")
    func ordinarySettingsOriginReturnsToSettings() {
        let model = makeAnswerModel(evidence: RecordingEvidenceRunner(), answer: StubAnswerRunner())
        model.openQuestionsFromSettings()
        #expect(model.selectedDestination == .agents)
        #expect(model.questionOrigin == .settings)
        #expect(model.primaryNavigationDestination == .settings)

        model.returnFromNonPrimaryDestination()
        #expect(model.selectedDestination == .settings)
    }

    @Test("Unrelated navigation clears stale question origin and target")
    func unrelatedNavigationClearsStaleState() async {
        let model = makeAttributedModel(importID: 5)
        await model.askAboutCurrentArchiveConversation()
        #expect(model.questionOrigin == .chats)
        #expect(model.questionTargetImportID == 5)

        model.selectedDestination = .overview
        #expect(model.questionOrigin == nil)
        #expect(model.questionTargetImportID == nil)
        #expect(model.questionTargetState == .allArchive)
    }

    @Test("Exact target survives Questions -> Settings -> Questions preparation round trip")
    func exactTargetSurvivesPreparationRoundTrip() async {
        let snap5 = ArchiveSnapshot(
            id: "conv:55555555555555555555555555555555",
            importID: 5,
            label: "Prep 5",
            firstSeenAt: nil,
            lastSeenAt: nil
        )
        // First discovery: empty. Second discovery (after return): has snap5.
        let runner = RecordingEvidenceRunner(discoveryOutcomes: [.ready([]), .ready([snap5])])
        let model = makeAnswerModel(evidence: runner, answer: StubAnswerRunner())
        let summary = ArchiveEvidenceImportSummary(
            id: 5, displayName: "Chat", shape: .attributed,
            importedAt: Date(), recordCount: 1, firstSentAt: nil, lastSentAt: nil,
            isAnonymous: false, link: nil, attachmentBatchCount: 0, attachmentCount: 0,
            materializedAttachmentCount: 0
        )
        model.setTestArchiveEvidence(imports: [summary], selectedImportID: 5)
        model.selectedDestination = .chats
        await model.askAboutCurrentArchiveConversation()
        await model.loadArchiveSnapshots()
        #expect(model.questionTargetState == .exactNeedsPreparation(importID: 5))

        await model.openQuestionsMemorySettings()
        #expect(model.selectedDestination == .settings)
        #expect(model.preparationOrigin == .questions)
        #expect(model.questionTargetImportID == 5)

        model.returnToPreparationOrigin()
        #expect(model.selectedDestination == .agents)
        #expect(model.preparationOrigin == nil)
        #expect(model.questionTargetImportID == 5)

        await model.loadArchiveSnapshots()
        #expect(model.questionTargetState == .exactReady(snapshot: snap5))
        #expect(model.selectedArchiveConversationID == snap5.id)
    }

    @Test("Explicit scope change clears the original target intent")
    func explicitScopeChangeClearsTarget() async {
        let snap5 = ArchiveSnapshot(
            id: "conv:55555555555555555555555555555555",
            importID: 5,
            label: "5",
            firstSeenAt: nil,
            lastSeenAt: nil
        )
        let model = makeAttributedModel(importID: 5, discoveryOutcomes: [.ready([snap5])])
        await model.askAboutCurrentArchiveConversation()
        await model.loadArchiveSnapshots()
        #expect(model.questionTargetImportID == 5)

        model.setAnswerConversation(nil)
        #expect(model.questionTargetImportID == nil)
        #expect(model.questionTargetState == .allArchive)
        #expect(model.selectedArchiveConversationID == nil)
    }

    @Test("Unattributed and Visual rows cannot initiate the exact handoff")
    func unattributedAndVisualCannotInitiateHandoff() async {
        let unattributedModel = makeAttributedModel(importID: 3, shape: .unattributed)
        await unattributedModel.askAboutCurrentArchiveConversation()
        #expect(unattributedModel.selectedDestination == .chats)
        #expect(unattributedModel.questionTargetImportID == nil)

        let visualModel = makeAttributedModel(importID: 4)
        let summary4 = ArchiveEvidenceImportSummary(
            id: 4, displayName: "Conversation 4", shape: .attributed,
            importedAt: Date(), recordCount: 10, firstSentAt: nil, lastSentAt: nil,
            isAnonymous: false, link: nil, attachmentBatchCount: 0, attachmentCount: 0,
            materializedAttachmentCount: 0
        )
        visualModel.setTestArchiveEvidence(imports: [summary4], selectedImportID: 4, selectedVisualID: 10)
        await visualModel.askAboutCurrentArchiveConversation()
        #expect(visualModel.selectedDestination == .chats)
        #expect(visualModel.questionTargetImportID == nil)
    }

    @Test("Questions preparation aligns memorySource to archive from visual default and runs zero sync")
    func questionsPreparationAlignsSourceToArchiveAndRunsNoSync() async {
        let model = makeAttributedModel(importID: 5, discoveryOutcomes: [.ready([])])
        #expect(model.memorySource == .visual)
        await model.askAboutCurrentArchiveConversation()
        await model.loadArchiveSnapshots()
        #expect(model.questionTargetState == .exactNeedsPreparation(importID: 5))
        #expect(model.canOpenQuestionsMemorySettings)

        await model.openQuestionsMemorySettings()
        #expect(model.selectedDestination == .settings)
        #expect(model.preparationOrigin == .questions)
        #expect(model.hasMemorySettingsRequest)
        #expect(model.memorySource == .archive)
        #expect(model.memorySyncPhase == .idle)
    }

    @Test("Chats exact entry is refused when current destination is not chats")
    func chatsExactEntryRefusedOutsideChats() async {
        let model = makeAttributedModel(importID: 5)
        model.selectedDestination = .overview
        await model.askAboutCurrentArchiveConversation()
        #expect(model.selectedDestination == .overview)
        #expect(model.questionOrigin == nil)
        #expect(model.questionTargetImportID == nil)
    }

    @Test("Synthetic worker failure token does not appear in presentation state")
    func syntheticFailureTokenDoesNotLeak() async {
        let token = "synthetic_worker_leak_token_12345"
        let model = makeAttributedModel(
            importID: 5,
            discoveryOutcomes: [.failed(.workerFailed(state: token))]
        )
        await model.askAboutCurrentArchiveConversation()
        await model.loadArchiveSnapshots()
        guard case .exactUnavailable(_, let reason) = model.questionTargetState else {
            Issue.record("expected exactUnavailable")
            return
        }
        #expect(!reason.message.contains(token))
        #expect(!String(describing: model.questionTargetState).contains(token))
    }

    @Test("Exact target is retired if Settings preparation journey is abandoned")
    func exactTargetRetiredIfPreparationAbandoned() async {
        let model = makeAttributedModel(importID: 5, discoveryOutcomes: [.ready([])])
        await model.askAboutCurrentArchiveConversation()
        await model.loadArchiveSnapshots()
        await model.openQuestionsMemorySettings()
        #expect(model.selectedDestination == .settings)
        #expect(model.preparationOrigin == .questions)
        #expect(model.questionTargetImportID == 5)

        // User abandons by navigating to Home instead of Back to Questions
        model.selectedDestination = .overview
        #expect(model.preparationOrigin == nil)
        #expect(model.questionOrigin == nil)
        #expect(model.questionTargetImportID == nil)
        #expect(model.questionTargetState == .allArchive)
    }

    @Test("Storage enablement alone runs nothing")
    func storageEnablementAloneRunsNothing() async {
        let runner = RecordingEvidenceRunner()
        let model = makeAnswerModel(evidence: runner, answer: StubAnswerRunner(), consent: false)
        #expect(!model.allowsLocalPersistence)

        await model.setAllowsLocalPersistence(true)
        #expect(model.allowsLocalPersistence)
        #expect(runner.calls.isEmpty)
        #expect(model.answerPhase == .idle)
        #expect(model.memorySyncPhase == .idle)
        #expect(model.dailySummaryPhase == .idle)
        #expect(model.followUpPhase == .idle)
    }
}

// MARK: - Availability refresh

/// The defect this suite holds shut: availability was read once in AppModel's
/// initializer and never again, so a local model that became ready during the
/// session stayed invisible until a relaunch. The runtime's own answer is
/// always fresh; only the host's cached copy was stale.
@MainActor
@Suite("On-device answer availability refresh")
struct OnDeviceAnswerAvailabilityRefreshTests {
    @Test("AppModel starts from the runtime's answer, not a hardcoded state")
    func startsFromRuntime() {
        let runner = MutableAvailabilityRunner(.unavailable(.modelNotReady))
        let model = makeAnswerModel(
            evidence: RecordingEvidenceRunner(outcomes: []),
            answer: runner
        )
        #expect(model.answerAvailability == .unavailable(.modelNotReady))
        #expect(!model.canAskArchiveQuestion)
    }

    @Test("A model that becomes ready is picked up without a relaunch")
    func picksUpReadiness() {
        let runner = MutableAvailabilityRunner(.unavailable(.modelNotReady))
        let model = makeAnswerModel(
            evidence: RecordingEvidenceRunner(outcomes: []),
            answer: runner
        )
        #expect(!model.canAskArchiveQuestion)

        runner.setAvailability(.available(contextSize: 8_192))
        model.refreshAnswerRuntimeAvailability()

        #expect(model.answerAvailability == .available(contextSize: 8_192))
        #expect(model.canAskArchiveQuestion)
    }

    @Test("Each unavailable reason is reported as itself, not one generic state")
    func reasonsAreNotCollapsed() {
        let runner = MutableAvailabilityRunner(.unavailable(.modelNotReady))
        let model = makeAnswerModel(
            evidence: RecordingEvidenceRunner(outcomes: []),
            answer: runner
        )
        for reason: AnswerRuntimeUnavailableReason in [
            .modelNotReady,
            .appleIntelligenceNotEnabled,
            .deviceNotEligible,
            .frameworkUnavailable,
        ] {
            runner.setAvailability(.unavailable(reason))
            model.refreshAnswerRuntimeAvailability()
            #expect(model.answerAvailability == .unavailable(reason))
            #expect(model.answerAvailability.reason == reason)
        }
    }

    @Test("A reason change while still unavailable is not hidden by the old one")
    func unavailableReasonChangesTruthfully() {
        let runner = MutableAvailabilityRunner(.unavailable(.appleIntelligenceNotEnabled))
        let model = makeAnswerModel(
            evidence: RecordingEvidenceRunner(outcomes: []),
            answer: runner
        )
        runner.setAvailability(.unavailable(.modelNotReady))
        model.refreshAnswerRuntimeAvailability()
        #expect(model.answerAvailability.reason == .modelNotReady)
    }

    @Test("A model that stops being available is reflected too")
    func availabilityCanBeLost() {
        let runner = MutableAvailabilityRunner(.available(contextSize: 8_192))
        let model = makeAnswerModel(
            evidence: RecordingEvidenceRunner(outcomes: []),
            answer: runner
        )
        #expect(model.canAskArchiveQuestion)
        runner.setAvailability(.unavailable(.modelNotReady))
        model.refreshAnswerRuntimeAvailability()
        #expect(model.answerAvailability == .unavailable(.modelNotReady))
        #expect(!model.canAskArchiveQuestion)
    }

    @Test("Refreshing repeatedly is a cheap query, not growing work")
    func refreshIsIdempotent() {
        let runner = MutableAvailabilityRunner(.unavailable(.modelNotReady))
        let model = makeAnswerModel(
            evidence: RecordingEvidenceRunner(outcomes: []),
            answer: runner
        )
        for _ in 0..<5 { model.refreshAnswerRuntimeAvailability() }
        #expect(model.answerAvailability == .unavailable(.modelNotReady))
        #expect(runner.availabilityQueries == 1 + 5)
    }

    @Test("Refreshing never discards an answer the user is reading")
    func refreshKeepsACompletedAnswer() async {
        let evidence = RecordingEvidenceRunner(outcomes: [
            .ready(evidenceSnapshot(rows: [evidenceRow(1, text: "原文")]))
        ])
        let runner = MutableAvailabilityRunner(.available(contextSize: 8_192))
        let model = makeAnswerModel(evidence: evidence, answer: runner)
        await model.askArchiveQuestion("问题？", now: Date())
        #expect(model.answerPhase == .answered)
        let answered = model.answerResult

        model.refreshAnswerRuntimeAvailability()
        model.refreshAnswerRuntimeAvailability()

        #expect(model.answerPhase == .answered)
        #expect(model.answerResult == answered)
        #expect(model.answerResult != nil)
    }

    @Test("A refresh during a live run neither cancels it nor changes its phase")
    func refreshDoesNotDisturbAnActiveRun() async throws {
        let held = HeldEvidenceRunner()
        let runner = MutableAvailabilityRunner(.available(contextSize: 8_192))
        let model = makeAnswerModel(evidence: held, answer: runner)
        let run = Task { await model.askArchiveQuestion("问题？", now: Date()) }
        await held.waitUntilStarted()
        #expect(model.answerPhase == .running)

        runner.setAvailability(.unavailable(.modelNotReady))
        model.refreshAnswerRuntimeAvailability()

        #expect(model.answerPhase == .running)
        #expect(model.isAnswerRunActive)
        await held.finish(.failed(.workerFailed(state: "worker_unavailable")))
        await run.value
    }

    @Test("Ask rechecks availability before reading any evidence")
    func askRechecksBeforeAnyWork() async {
        let evidence = RecordingEvidenceRunner(outcomes: [
            .ready(evidenceSnapshot(rows: [evidenceRow(1, text: "原文")]))
        ])
        let runner = MutableAvailabilityRunner(.unavailable(.modelNotReady))
        let model = makeAnswerModel(evidence: evidence, answer: runner)
        // Availability moved after the model was built; nothing refreshed it.
        runner.setAvailability(.available(contextSize: 8_192))

        await model.askArchiveQuestion("问题？", now: Date())

        #expect(model.answerAvailability == .available(contextSize: 8_192))
        #expect(evidence.calls.count == 1)
        #expect(runner.answerCalls == 1)
        #expect(model.answerPhase == .answered)
    }

    @Test("An Ask-time check that finds the model gone starts nothing at all")
    func askRefusesWhenRuntimeWentAway() async {
        let evidence = RecordingEvidenceRunner(outcomes: [
            .ready(evidenceSnapshot(rows: [evidenceRow(1, text: "原文")]))
        ])
        let runner = MutableAvailabilityRunner(.available(contextSize: 8_192))
        let model = makeAnswerModel(evidence: evidence, answer: runner)
        // The runtime reports itself unavailable between surface refresh and Ask.
        runner.setAvailability(.unavailable(.appleIntelligenceNotEnabled))

        await model.askArchiveQuestion("问题？", now: Date())

        #expect(evidence.calls.isEmpty)
        #expect(runner.answerCalls == 0)
        #expect(model.answerPhase == .failed(.runtimeUnavailable(.appleIntelligenceNotEnabled)))
        #expect(model.answerResult == nil)
        #expect(!model.isAnswerRunActive)
    }

    @Test("The refusal carries the runtime's own reason, never a stale one")
    func refusalUsesTheCurrentReason() async {
        let evidence = RecordingEvidenceRunner(outcomes: [])
        let runner = MutableAvailabilityRunner(.unavailable(.appleIntelligenceNotEnabled))
        let model = makeAnswerModel(evidence: evidence, answer: runner)
        runner.setAvailability(.unavailable(.deviceNotEligible))
        await model.askArchiveQuestion("问题？", now: Date())
        #expect(model.answerPhase == .failed(.runtimeUnavailable(.deviceNotEligible)))
    }

    @Test("The empty-question refusal still precedes the availability check")
    func emptyQuestionStillCostsNothing() async {
        let evidence = RecordingEvidenceRunner(outcomes: [])
        let runner = MutableAvailabilityRunner(.available(contextSize: 8_192))
        let model = makeAnswerModel(evidence: evidence, answer: runner)
        await model.askArchiveQuestion("  \n ", now: Date())
        #expect(evidence.calls.isEmpty)
        #expect(runner.answerCalls == 0)
        #expect(model.answerPhase == .idle)
    }

    @Test("A second Ask still cannot start while a run is in flight")
    func refreshDoesNotBreakSingleFlight() async {
        let held = HeldEvidenceRunner()
        let runner = MutableAvailabilityRunner(.available(contextSize: 8_192))
        let model = makeAnswerModel(evidence: held, answer: runner)
        let first = Task { await model.askArchiveQuestion("第一个？", now: Date()) }
        await held.waitUntilStarted()

        model.refreshAnswerRuntimeAvailability()
        await model.askArchiveQuestion("第二个？", now: Date())

        #expect(await held.calls == 1)
        await held.finish(.failed(.workerFailed(state: "worker_unavailable")))
        await first.value
    }

    @Test("Nothing polls availability in the background")
    func noBackgroundPolling() {
        let appModel = (try? String(
            contentsOf: Self.sourceRoot.appendingPathComponent("AppModel.swift"),
            encoding: .utf8
        )) ?? ""
        let start = "MARK: - Agents: on-device archive answer"
        let end = "MARK: - Local persistence settings"
        guard let from = appModel.range(of: start),
              let to = appModel.range(of: end, range: from.upperBound..<appModel.endIndex)
        else {
            Issue.record("AppModel.swift could not be read")
            return
        }
        let agents = String(appModel[from.upperBound..<to.lowerBound])
        // A timer here would refresh on no user boundary at all, which is the
        // polling this defectfix was told not to introduce.
        for forbidden in ["Timer.", "Timer.publish", "AsyncStream"] {
            #expect(!agents.contains(forbidden))
        }
    }

    private static var sourceRoot: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("WeChatCompanion")
    }
}
