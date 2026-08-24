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

    // MARK: - Helpers

    private func makeModel(coordinator: ExtractionCoordinator) -> AppModel {
        AppModel(
            extractionCoordinator: coordinator,
            credentials: EmptyCredentialStore(),
            consentDefaults: UserDefaults(suiteName: "ExtractionUIStateTests-\(UUID())")!
        )
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

/// Extractor whose completion the test controls. No network, ever.
private actor ControllableExtractor: FrameExtracting {
    nonisolated let isConfigured = true
    nonisolated let processingLocation = ExtractionProcessingLocation.remote

    /// Completes immediately by default; only the pause test needs a call that
    /// stays in flight, and forgetting to release it would hang waitUntilIdle.
    private var isOpen: Bool
    private var waiters: [CheckedContinuation<Void, Never>] = []

    init(startsOpen: Bool = true) { isOpen = startsOpen }

    func complete() {
        isOpen = true
        let pending = waiters
        waiters = []
        for waiter in pending { waiter.resume() }
    }

    func extract(from frame: ObservedFrame) async throws -> ExtractedConversationFrame {
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
    static func uiTestFrame(secondsFromNow: Double) -> ObservedFrame {
        let side = 32
        let context = CGContext(
            data: nil, width: side, height: side, bitsPerComponent: 8,
            bytesPerRow: side, space: CGColorSpaceCreateDeviceGray(),
            bitmapInfo: CGImageAlphaInfo.none.rawValue
        )!
        context.setFillColor(gray: 0.5, alpha: 1)
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
