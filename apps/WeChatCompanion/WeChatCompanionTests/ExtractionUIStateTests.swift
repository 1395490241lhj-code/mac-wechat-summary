import CoreGraphics
import Foundation
import Testing
@testable import WeChatCompanion

/// The Chats screen showed zeroes while extraction was actually configured,
/// because AppModel only refreshed extraction metrics when configuration
/// changed and never read `latest()` at all.
@MainActor
struct ExtractionUIStateTests {
    @Test
    func refreshSeesMetricsThatChangedInsideTheCoordinator() async {
        let extractor = ControllableExtractor()
        let coordinator = ExtractionCoordinator(
            extractor: extractor,
            capability: ExtractionCapability(userEnabledRemoteProvider: true)
        )
        let model = makeModel(coordinator: coordinator)
        #expect(model.extractionMetrics.framesReceived == 0)

        await coordinator.submit(.uiTestFrame(secondsFromNow: 0))
        await coordinator.waitUntilIdle()
        await model.refreshExtractionState()

        #expect(model.extractionMetrics.framesReceived == 1)
        #expect(model.extractionMetrics.extractionsSucceeded == 1)
    }

    @Test
    func latestExtractionBecomesVisibleAfterSuccess() async {
        let extractor = ControllableExtractor()
        let coordinator = ExtractionCoordinator(
            extractor: extractor,
            capability: ExtractionCapability(userEnabledRemoteProvider: true)
        )
        let model = makeModel(coordinator: coordinator)
        #expect(model.latestExtraction == nil)

        await coordinator.submit(.uiTestFrame(secondsFromNow: 0))
        await coordinator.waitUntilIdle()
        await model.refreshExtractionState()

        #expect(model.latestExtraction != nil)
        #expect(model.latestExtraction?.capturedAt == Date(timeIntervalSince1970: 1_700_000_000))
    }

    /// The case that made the bug visible: capture is paused, yet an extraction
    /// that was already running still completes and must reach the UI.
    @Test
    func extractionCompletionAppearsEvenAfterCapturePaused() async {
        let extractor = ControllableExtractor(startsOpen: false)
        let coordinator = ExtractionCoordinator(
            extractor: extractor,
            capability: ExtractionCapability(userEnabledRemoteProvider: true)
        )
        let model = makeModel(coordinator: coordinator)
        model.startExtractionPolling()

        // Extraction is in flight while capture gets paused.
        await coordinator.submit(.uiTestFrame(secondsFromNow: 0))
        await model.pauseObserving()
        #expect(model.latestExtraction == nil)
        // Capture polling stopped; extraction polling deliberately did not.
        #expect(!model.isPollingObserverMetrics)
        #expect(model.isPollingExtractionState)

        await extractor.complete()
        await coordinator.waitUntilIdle()

        let appeared = await waitUntil { model.latestExtraction != nil }
        #expect(appeared)
        #expect(model.extractionMetrics.extractionsSucceeded == 1)
        model.stopExtractionPolling()
    }

    @Test
    func repeatedExtractionPollingStartsDoNotStackLoops() async {
        let model = makeModel(coordinator: ExtractionCoordinator())
        #expect(!model.isPollingExtractionState)

        model.startExtractionPolling()
        #expect(model.isPollingExtractionState)
        model.startExtractionPolling()
        model.startExtractionPolling()
        #expect(model.isPollingExtractionState)

        model.stopExtractionPolling()
        #expect(!model.isPollingExtractionState)
        model.startExtractionPolling()
        #expect(model.isPollingExtractionState)
        model.stopExtractionPolling()
    }

    /// A consent misconfiguration must be visible rather than looking like
    /// "nothing is happening".
    @Test
    func withheldPendingConsentIsSurfacedToTheUI() async {
        let extractor = ControllableExtractor()
        let coordinator = ExtractionCoordinator(
            extractor: extractor,
            capability: .onDeviceOnly
        )
        let model = makeModel(coordinator: coordinator)

        await coordinator.submit(.uiTestFrame(secondsFromNow: 0))
        await coordinator.waitUntilIdle()
        await model.refreshExtractionState()

        #expect(model.extractionMetrics.framesWithheldPendingConsent == 1)
        #expect(model.extractionMetrics.extractionsStarted == 0)
        #expect(model.latestExtraction == nil)
        #expect(!model.allowsRemoteProcessing)
    }

    @Test
    func refreshedExtractionStateIsNeverPersisted() async throws {
        let extractor = ControllableExtractor()
        let coordinator = ExtractionCoordinator(
            extractor: extractor,
            capability: ExtractionCapability(userEnabledRemoteProvider: true)
        )
        let model = makeModel(coordinator: coordinator)
        await coordinator.submit(.uiTestFrame(secondsFromNow: 0))
        await coordinator.waitUntilIdle()
        await model.refreshExtractionState()
        #expect(model.latestExtraction != nil)

        // Extracted content is not Codable, so it cannot be serialised at all.
        #expect(!(ExtractedConversationFrame.self is any Encodable.Type))
        // Only aggregate counters can be encoded, and they carry no content.
        let json = String(
            decoding: try JSONEncoder().encode(model.extractionMetrics), as: UTF8.self
        )
        #expect(!json.contains("chat"))
        #expect(!json.contains("messages"))
        #expect(!json.contains("text"))

        // A fresh coordinator starts empty: nothing was restored from disk.
        let reloaded = ExtractionCoordinator()
        #expect(await reloaded.latest() == nil)
    }

    @Test
    func visualQualityMetricsKeepFieldDenominatorsAndHallucinationsSeparate() {
        var metrics = VisualQualityMetrics()
        metrics.recordExtractionAttempt()
        metrics.recordReviewedFrame(
            title: .correct,
            messages: [
                VisualQualityMessageAssessment(
                    detection: .correct,
                    sender: .unknown,
                    text: .minorError,
                    time: .notShown,
                    kind: .correct
                ),
                VisualQualityMessageAssessment(
                    detection: .hallucinated,
                    sender: .hallucinated,
                    text: .hallucinated,
                    time: .hallucinated,
                    kind: .wrong
                ),
                VisualQualityMessageAssessment(detection: .duplicate),
            ],
            missingVisibleMessages: 2,
            reconciliation: VisualQualityReconciliationSummary(
                action: .appended,
                overlapCount: 1,
                newMessageCount: 2,
                suppressedIndexes: [0],
                contributionIndexes: [1, 2]
            )
        )

        #expect(metrics.totalFrames == 1)
        #expect(metrics.reviewedFrames == 1)
        #expect(metrics.visibleMessages == 3)
        #expect(metrics.messageDetectionPrecision == VisualQualityRate(numerator: 1, denominator: 3))
        #expect(metrics.messageDetectionRecall == VisualQualityRate(numerator: 1, denominator: 3))
        #expect(metrics.textExactRate == VisualQualityRate(numerator: 0, denominator: 2))
        #expect(metrics.textExactOrMinorRate == VisualQualityRate(numerator: 1, denominator: 2))
        #expect(metrics.senderUnknown == 1)
        #expect(metrics.timeNotShown == 1)
        #expect(metrics.hallucinatedMessageCount == 1)
        #expect(metrics.hallucinatedContentCount == 1)
        #expect(metrics.hallucinationCount == 4)
        #expect(!metrics.fabricatedMessageHardGatePasses)
        #expect(metrics.duplicateAfterReconciliationCount == 1)
        #expect(metrics.lostMessageCountAcrossOverlap == 2)
        #expect(!(VisualQualityMetrics.self is any Encodable.Type))
        #expect(!(VisualQualityFrameReview.self is any Encodable.Type))
    }

    @Test
    func visualQualityReconciliationUsesProductionFrameReconciler() {
        var reconciler = VisualQualityReconciliationTracker()

        let first = reconciler.reconcile(qualityFrame("a", "b", "c"))
        #expect(first.action == .appended)
        #expect(first.newMessageCount == 3)

        let overlap = reconciler.reconcile(qualityFrame("b", "c", "d"))
        #expect(overlap.action == .appended)
        #expect(overlap.overlapCount == 2)
        #expect(overlap.newMessageCount == 1)

        let repeated = reconciler.reconcile(qualityFrame("b", "c", "d"))
        #expect(repeated.action == .unchanged)
        #expect(repeated.suppressedIndexes == Set([0, 1, 2]))

        var indexedReconciler = VisualQualityReconciliationTracker()
        _ = indexedReconciler.reconcile(qualityFrame("a", "b", "c"))
        let meaningfulRows = qualityFrame("b", "c", "d")
        let withUnusableLeadingRow = ExtractedConversationFrame(
            capturedAt: meaningfulRows.capturedAt,
            chat: meaningfulRows.chat,
            messages: [ExtractedVisibleMessage()] + meaningfulRows.messages
        )
        let indexedOverlap = indexedReconciler.reconcile(withUnusableLeadingRow)
        #expect(indexedOverlap.suppressedIndexes == Set([1, 2]))
        #expect(indexedOverlap.contributionIndexes == Set([3]))

        let scrolledUp = reconciler.reconcile(qualityFrame("older", "a", "b"))
        #expect(scrolledUp.action == .prepended)
        #expect(scrolledUp.newMessageCount == 1)
        #expect(scrolledUp.overlapCount == 2)

        let jump = reconciler.reconcile(qualityFrame("far", "away"))
        #expect(jump.action == .gap)
        #expect(jump.newMessageCount == 2)
    }

    @Test
    func visualQualityGateDoesNotRunWithoutRemoteProcessingConsent() async throws {
        let credentials = QualityGateCredentialStore()
        try credentials.save("synthetic-key", account: GeminiFrameExtractor.credentialAccount)
        let transport = QualityGateStubTransport()
        let defaults = UserDefaults(suiteName: "VisualQualityConsent-\(UUID())")!
        let model = AppModel(
            messageHistory: makeTestMessageHistory(),
            credentials: credentials,
            geminiTransport: transport,
            consentDefaults: defaults
        )

        await model.beginVisualQualityGate()

        #expect(!model.visualQualityGateIsActive)
        #expect(await transport.requestCount == 0)
    }

    @Test
    func visualQualityIntentDisablesNormalExtractionBeforeWindowSelection() async throws {
        let credentials = QualityGateCredentialStore()
        try credentials.save("synthetic-key", account: GeminiFrameExtractor.credentialAccount)
        let defaults = UserDefaults(suiteName: "VisualQualityPreparation-\(UUID())")!
        defaults.set(true, forKey: AppModel.remoteConsentKey)
        let extractor = ControllableExtractor()
        let ingestor = QualityGateIngestSpy()
        let session = SystemWindowCaptureSession()
        let coordinator = ExtractionCoordinator(
            extractor: extractor,
            capability: ExtractionCapability(userEnabledRemoteProvider: true),
            ingestor: ingestor
        )
        let model = AppModel(
            session: session,
            extractionCoordinator: coordinator,
            messageHistory: makeTestMessageHistory(),
            credentials: credentials,
            consentDefaults: defaults
        )

        await model.beginVisualQualityGate()
        let emptyFrames = AsyncStream<ObservedFrame> { $0.finish() }
        await coordinator.start(frames: emptyFrames)
        await coordinator.submit(.uiTestFrame(secondsFromNow: 1))
        await coordinator.waitUntilIdle()

        #expect(model.visualQualityGateIsActive)
        #expect(await session.snapshot().state == .needsWindowSelection)
        #expect(await coordinator.snapshot().status == .paused)
        #expect(await extractor.callCount() == 0)
        #expect(await ingestor.ingestionCount == 0)
    }

    @Test
    func lateNormalProviderResultCannotReachIngestorAfterEvaluationIntent() async throws {
        let credentials = QualityGateCredentialStore()
        try credentials.save("synthetic-key", account: GeminiFrameExtractor.credentialAccount)
        let defaults = UserDefaults(suiteName: "VisualQualityLateResult-\(UUID())")!
        defaults.set(true, forKey: AppModel.remoteConsentKey)
        let extractor = ControllableExtractor(startsOpen: false)
        let ingestor = QualityGateIngestSpy()
        let coordinator = ExtractionCoordinator(
            extractor: extractor,
            capability: ExtractionCapability(userEnabledRemoteProvider: true),
            ingestor: ingestor
        )
        let model = AppModel(
            extractionCoordinator: coordinator,
            messageHistory: makeTestMessageHistory(),
            credentials: credentials,
            consentDefaults: defaults
        )

        await coordinator.submit(.uiTestFrame(secondsFromNow: 0))
        #expect(await waitUntilAsync { await extractor.callCount() == 1 })
        let preparation = Task { await model.beginVisualQualityGate() }
        #expect(await waitUntil { model.visualQualityGateMode == .preparingEvaluation })
        await extractor.complete()
        await preparation.value
        await coordinator.waitUntilIdle()

        #expect(await extractor.callCount() == 1)
        #expect(await ingestor.ingestionCount == 0)
        #expect(await coordinator.latest() == nil)
    }

    @Test
    func visualQualitySessionReviewsOneSyntheticFrameWithoutIngestionOrPersistence() async throws {
        let credentials = QualityGateCredentialStore()
        try credentials.save("synthetic-key", account: GeminiFrameExtractor.credentialAccount)
        let transport = QualityGateStubTransport()
        let defaults = UserDefaults(suiteName: "VisualQualitySession-\(UUID())")!
        defaults.set(true, forKey: AppModel.remoteConsentKey)
        let session = SystemWindowCaptureSession()
        let normalExtractor = ControllableExtractor()
        let ingestor = QualityGateIngestSpy()
        let coordinator = ExtractionCoordinator(
            extractor: normalExtractor,
            capability: ExtractionCapability(userEnabledRemoteProvider: true),
            ingestor: ingestor
        )
        let model = AppModel(
            session: session,
            extractionCoordinator: coordinator,
            messageHistory: makeTestMessageHistory(),
            credentials: credentials,
            geminiTransport: transport,
            consentDefaults: defaults
        )

        await model.beginVisualQualityGate()
        await model.armVisualQualityFrameConsumer()
        await session.markSelectedForTesting()
        let image = ObservedFrame.uiTestFrame(secondsFromNow: 1).image
        await session.ingest(image: image, capturedAt: Date(timeIntervalSince1970: 1_700_000_001))

        #expect(await waitUntil { model.visualQualityGatePhase == .reviewing })
        #expect(model.visualQualityGateMetrics.totalFrames == 1)
        #expect(model.visualQualityGateMetrics.reviewedFrames == 0)
        #expect(model.visualQualityGateReview?.extraction.messages.count == 1)
        #expect(model.visualQualityGateMode == .evaluating)
        #expect(await transport.requestCount == 1)
        #expect(await normalExtractor.callCount() == 0)
        #expect(await ingestor.ingestionCount == 0)

        await model.finishVisualQualityGate()

        #expect(model.visualQualityGateReview.map { _ in true } == nil)
        #expect(model.visualQualityGateMetrics.totalFrames == 1)
        #expect(await ingestor.ingestionCount == 0)
        #expect(await session.snapshot().state == .observing)
        #expect(await session.latestPreview() == nil)
        #expect(await coordinator.latest() == nil)
        #expect(await normalExtractor.callCount() == 0)
        #expect(!defaults.dictionaryRepresentation().keys.contains {
            $0.localizedCaseInsensitiveContains("visualquality")
        })
    }

    @Test
    func multipleEvaluationFramesNeverEnterNormalProviderOrIngestor() async throws {
        let credentials = QualityGateCredentialStore()
        try credentials.save("synthetic-key", account: GeminiFrameExtractor.credentialAccount)
        let defaults = UserDefaults(suiteName: "VisualQualityMultipleFrames-\(UUID())")!
        defaults.set(true, forKey: AppModel.remoteConsentKey)
        let session = SystemWindowCaptureSession()
        let normalExtractor = ControllableExtractor()
        let ingestor = QualityGateIngestSpy()
        let coordinator = ExtractionCoordinator(
            extractor: normalExtractor,
            capability: ExtractionCapability(userEnabledRemoteProvider: true),
            ingestor: ingestor
        )
        let transport = QualityGateStubTransport()
        let model = AppModel(
            session: session,
            extractionCoordinator: coordinator,
            messageHistory: makeTestMessageHistory(),
            credentials: credentials,
            geminiTransport: transport,
            consentDefaults: defaults
        )

        await model.beginVisualQualityGate()
        await session.markSelectedForTesting()
        await model.armVisualQualityFrameConsumer()
        await session.ingest(
            image: ObservedFrame.uiTestFrame(secondsFromNow: 1, gray: 0.2).image,
            capturedAt: Date(timeIntervalSince1970: 1_700_000_001)
        )
        #expect(await waitUntil { model.visualQualityGatePhase == .reviewing })

        model.setVisualQualityTitleRating(.correct)
        model.setVisualQualityMessageRating(at: 0) {
            $0.detection = .correct
            $0.sender = .correct
            $0.text = .exact
            $0.time = .notShown
            $0.kind = .correct
        }
        await model.recordVisualQualityReview()
        await model.captureNextVisualQualityFrame()
        await session.ingest(
            image: ObservedFrame.uiTestFrame(secondsFromNow: 2, gray: 0.8).image,
            capturedAt: Date(timeIntervalSince1970: 1_700_000_002)
        )

        #expect(await waitUntil { model.visualQualityGatePhase == .reviewing })
        #expect(await transport.requestCount == 2)
        #expect(await normalExtractor.callCount() == 0)
        #expect(await ingestor.ingestionCount == 0)
        await model.finishVisualQualityGate(resumeCapture: false)
    }

    @Test
    func cancellingBeforeWindowSelectionRestoresNormalWithoutSideEffects() async throws {
        let credentials = QualityGateCredentialStore()
        try credentials.save("synthetic-key", account: GeminiFrameExtractor.credentialAccount)
        let defaults = UserDefaults(suiteName: "VisualQualityCancel-\(UUID())")!
        defaults.set(true, forKey: AppModel.remoteConsentKey)
        let normalExtractor = ControllableExtractor()
        let ingestor = QualityGateIngestSpy()
        let coordinator = ExtractionCoordinator(
            extractor: normalExtractor,
            capability: ExtractionCapability(userEnabledRemoteProvider: true),
            ingestor: ingestor
        )
        let transport = QualityGateStubTransport()
        let model = AppModel(
            extractionCoordinator: coordinator,
            messageHistory: makeTestMessageHistory(),
            credentials: credentials,
            geminiTransport: transport,
            consentDefaults: defaults
        )

        await model.beginVisualQualityGate()
        #expect(model.visualQualityGateMode == .preparingEvaluation)
        await model.finishVisualQualityGate()

        #expect(model.visualQualityGateMode == .normal)
        #expect(!model.visualQualityGateIsActive)
        #expect(await transport.requestCount == 0)
        #expect(await normalExtractor.callCount() == 0)
        #expect(await ingestor.ingestionCount == 0)
    }

    @Test
    func endingEvaluationDoesNotReplayItsFrameAndOnlyNewFramesResumeNormalPath() async throws {
        let credentials = QualityGateCredentialStore()
        try credentials.save("synthetic-key", account: GeminiFrameExtractor.credentialAccount)
        let defaults = UserDefaults(suiteName: "VisualQualityEnd-\(UUID())")!
        defaults.set(true, forKey: AppModel.remoteConsentKey)
        let session = SystemWindowCaptureSession()
        let normalExtractor = ControllableExtractor()
        let ingestor = QualityGateIngestSpy()
        let coordinator = ExtractionCoordinator(
            extractor: normalExtractor,
            capability: ExtractionCapability(userEnabledRemoteProvider: true),
            ingestor: ingestor
        )
        let transport = QualityGateStubTransport()
        let model = AppModel(
            session: session,
            extractionCoordinator: coordinator,
            messageHistory: makeTestMessageHistory(),
            credentials: credentials,
            geminiTransport: transport,
            consentDefaults: defaults
        )

        await model.beginVisualQualityGate()
        await session.markSelectedForTesting()
        await model.armVisualQualityFrameConsumer()
        await session.ingest(
            image: ObservedFrame.uiTestFrame(secondsFromNow: 1, gray: 0.25).image,
            capturedAt: Date(timeIntervalSince1970: 1_700_000_001)
        )
        #expect(await waitUntil { model.visualQualityGatePhase == .reviewing })
        await model.finishVisualQualityGate()
        await coordinator.waitUntilIdle()

        #expect(model.visualQualityGateMode == .normal)
        #expect(await transport.requestCount == 1)
        #expect(await ingestor.ingestionCount == 0)

        await coordinator.updateConfiguration(
            extractor: normalExtractor,
            capability: ExtractionCapability(userEnabledRemoteProvider: true),
            ingestor: ingestor
        )
        await session.ingest(
            image: ObservedFrame.uiTestFrame(secondsFromNow: 2, gray: 0.75).image,
            capturedAt: Date(timeIntervalSince1970: 1_700_000_002)
        )
        let newFrameReachedNormalPath = await waitUntilAsync {
            let calls = await normalExtractor.callCount()
            let writes = await ingestor.ingestionCount
            return calls == 1 && writes == 1
        }

        #expect(newFrameReachedNormalPath)
        #expect(await normalExtractor.callCount() == 1)
        #expect(await ingestor.ingestionCount == 1)
    }

    @Test
    func localPersistenceConsentDoesNotOpenTheNormalIngestorDuringEvaluation() async throws {
        for localConsent in [false, true] {
            let credentials = QualityGateCredentialStore()
            try credentials.save("synthetic-key", account: GeminiFrameExtractor.credentialAccount)
            let defaults = UserDefaults(suiteName: "VisualQualityLocalConsent-\(UUID())")!
            defaults.set(true, forKey: AppModel.remoteConsentKey)
            defaults.set(localConsent, forKey: AppModel.localPersistenceConsentKey)
            let session = SystemWindowCaptureSession()
            let normalExtractor = ControllableExtractor()
            let ingestor = QualityGateIngestSpy()
            let coordinator = ExtractionCoordinator(
                extractor: normalExtractor,
                capability: ExtractionCapability(userEnabledRemoteProvider: true),
                ingestor: ingestor
            )
            let transport = QualityGateStubTransport()
            let model = AppModel(
                session: session,
                extractionCoordinator: coordinator,
                messageHistory: makeTestMessageHistory(),
                credentials: credentials,
                geminiTransport: transport,
                consentDefaults: defaults
            )

            await model.beginVisualQualityGate()
            await session.markSelectedForTesting()
            await model.armVisualQualityFrameConsumer()
            await session.ingest(
                image: ObservedFrame.uiTestFrame(
                    secondsFromNow: 1,
                    gray: localConsent ? 0.8 : 0.2
                ).image,
                capturedAt: Date(timeIntervalSince1970: 1_700_000_001)
            )

            #expect(await waitUntil { model.visualQualityGatePhase == .reviewing })
            #expect(model.allowsLocalPersistence == localConsent)
            #expect(await transport.requestCount == 1)
            #expect(await normalExtractor.callCount() == 0)
            #expect(await ingestor.ingestionCount == 0)
            await model.finishVisualQualityGate(resumeCapture: false)
        }
    }

    // MARK: - Helpers

    private func makeModel(coordinator: ExtractionCoordinator) -> AppModel {
        AppModel(
            extractionCoordinator: coordinator,
            messageHistory: makeTestMessageHistory(), credentials: EmptyCredentialStore(),
            consentDefaults: UserDefaults(suiteName: "ExtractionUIStateTests-\(UUID())")!
        )
    }
}

private func qualityFrame(_ texts: String...) -> ExtractedConversationFrame {
    ExtractedConversationFrame(
        capturedAt: Date(timeIntervalSince1970: 1_700_000_000),
        chat: ExtractedChatIdentity(title: "Synthetic chat", confidence: 1),
        messages: texts.map {
            ExtractedVisibleMessage(
                sender: "Synthetic sender",
                ownership: .other,
                visibleTime: "12:34",
                text: $0,
                kind: .text,
                confidence: 1
            )
        }
    )
}

private actor QualityGateStubTransport: GeminiTransporting {
    private(set) var requestCount = 0

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        requestCount += 1
        let payload = """
        {"candidates":[{"content":{"parts":[{"text":"{\\"chat\\":{\\"title\\":\\"Synthetic chat\\",\\"confidence\\":1},\\"messages\\":[{\\"sender\\":\\"Synthetic sender\\",\\"ownership\\":\\"other\\",\\"visibleTime\\":null,\\"text\\":\\"synthetic text\\",\\"kind\\":\\"text\\",\\"confidence\\":1,\\"bounds\\":null}]}"}]}}]}
        """
        let response = HTTPURLResponse(
            url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil
        )!
        return (Data(payload.utf8), response)
    }
}

private struct QualityGateCredentialStore: CredentialStoring {
    func save(_ secret: String, account: String) throws {}
    func secret(account: String) throws -> String? { "synthetic-key" }
    func remove(account: String) throws {}
}

private actor QualityGateIngestSpy: MessageIngesting {
    private(set) var ingestionCount = 0

    func ingest(_ frame: ExtractedConversationFrame) async {
        ingestionCount += 1
    }
}

private func waitUntil(
    timeout: Duration = .milliseconds(1500),
    _ condition: @MainActor () -> Bool
) async -> Bool {
    let deadline = ContinuousClock.now + timeout
    while ContinuousClock.now < deadline {
        if await MainActor.run(body: condition) { return true }
        try? await Task.sleep(for: .milliseconds(10))
    }
    return await MainActor.run(body: condition)
}

private func waitUntilAsync(
    timeout: Duration = .milliseconds(1500),
    _ condition: @Sendable () async -> Bool
) async -> Bool {
    let deadline = ContinuousClock.now + timeout
    while ContinuousClock.now < deadline {
        if await condition() { return true }
        try? await Task.sleep(for: .milliseconds(10))
    }
    return await condition()
}

/// Extractor whose completion the test controls. No network, ever.
private actor ControllableExtractor: FrameExtracting {
    nonisolated let isConfigured = true
    nonisolated let processingLocation = ExtractionProcessingLocation.remote

    /// Completes immediately by default; only the pause test needs a call that
    /// stays in flight, and forgetting to release it would hang waitUntilIdle.
    private var isOpen: Bool
    private var waiters: [CheckedContinuation<Void, Never>] = []
    private var invocationCount = 0

    func callCount() -> Int { invocationCount }

    init(startsOpen: Bool = true) { isOpen = startsOpen }

    func complete() {
        isOpen = true
        let pending = waiters
        waiters = []
        for waiter in pending { waiter.resume() }
    }

    func extract(from frame: ObservedFrame) async throws -> ExtractedConversationFrame {
        invocationCount += 1
        if !isOpen {
            await withCheckedContinuation { waiters.append($0) }
        }
        return ExtractedConversationFrame(
            capturedAt: frame.capturedAt,
            chat: nil,
            messages: []
        )
    }
}

private struct EmptyCredentialStore: CredentialStoring {
    func save(_ secret: String, account: String) throws {}
    func secret(account: String) throws -> String? { nil }
    func remove(account: String) throws {}
}

private extension ObservedFrame {
    /// Synthetic frame. No real WeChat imagery in any test.
    static func uiTestFrame(secondsFromNow: Double, gray: CGFloat = 0.5) -> ObservedFrame {
        let side = 32
        let context = CGContext(
            data: nil, width: side, height: side, bitsPerComponent: 8,
            bytesPerRow: side, space: CGColorSpaceCreateDeviceGray(),
            bitmapInfo: CGImageAlphaInfo.none.rawValue
        )!
        context.setFillColor(gray: gray, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: side, height: side))
        let image = context.makeImage()!
        return ObservedFrame(
            image: image,
            capturedAt: Date(timeIntervalSince1970: 1_700_000_000 + secondsFromNow),
            captureMode: .systemSelectedWindow,
            fingerprint: FrameFingerprint(image: image)!
        )
    }
}
