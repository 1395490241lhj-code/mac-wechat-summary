import CoreGraphics
import Foundation
import Testing
@testable import WeChatCompanion

struct ExtractionPipelineTests {
    // MARK: - Frame delivery

    @Test
    func meaningfulFrameReachesTheExtractor() async {
        let extractor = GatedExtractor(openImmediately: true)
        let coordinator = ExtractionCoordinator(extractor: extractor)
        let observer = PassiveObserver(
            captureSource: StubCaptureSource(image: .syntheticTestImage(brightness: 30))
        )
        await coordinator.start(frames: await observer.meaningfulFrames())

        await observer.captureOneFrame()

        let delivered = await waitUntil { await coordinator.snapshot().framesReceived == 1 }
        await coordinator.waitUntilIdle()
        let metrics = await coordinator.snapshot()
        #expect(delivered)
        #expect(metrics.extractionsSucceeded == 1)
        await coordinator.pause()
    }

    // MARK: - Backpressure

    @Test
    func busyExtractorKeepsOnlyTheNewestFrameAndNeverRunsTwoAtOnce() async {
        let extractor = GatedExtractor()
        let coordinator = ExtractionCoordinator(extractor: extractor)
        let first = ObservedFrame.synthetic(secondsFromNow: 0)
        let second = ObservedFrame.synthetic(secondsFromNow: 1)
        let third = ObservedFrame.synthetic(secondsFromNow: 2)

        await coordinator.submit(first)
        await coordinator.submit(second)
        await coordinator.submit(third)
        let busyMetrics = await coordinator.snapshot()
        #expect(busyMetrics.status == .processing)
        #expect(busyMetrics.framesDroppedWhileBusy == 1)

        await extractor.open()
        await coordinator.waitUntilIdle()

        let metrics = await coordinator.snapshot()
        let handled = await extractor.receivedTimestamps
        let maxConcurrent = await extractor.maxConcurrent
        #expect(metrics.framesReceived == 3)
        #expect(metrics.extractionsStarted == 2)
        #expect(metrics.extractionsSucceeded == 2)
        #expect(maxConcurrent == 1)
        // Newest wins: the middle frame was replaced before it ever ran.
        #expect(handled == [first.capturedAt, third.capturedAt])
        #expect(await coordinator.hasPendingFrame == false)
    }

    @Test
    func backlogStaysBoundedUnderSustainedPressure() async {
        let extractor = GatedExtractor()
        let coordinator = ExtractionCoordinator(extractor: extractor)

        for index in 0..<50 {
            await coordinator.submit(.synthetic(secondsFromNow: Double(index)))
        }
        // One in flight plus at most one pending, regardless of inbound volume.
        #expect(await coordinator.hasPendingFrame)
        let pressured = await coordinator.snapshot()
        #expect(pressured.framesReceived == 50)
        #expect(pressured.framesDroppedWhileBusy == 48)

        await extractor.open()
        await coordinator.waitUntilIdle()
        let metrics = await coordinator.snapshot()
        #expect(metrics.extractionsStarted == 2)
        #expect(await coordinator.hasPendingFrame == false)
    }

    // MARK: - Failures

    @Test
    func extractionFailureIsRecordedAsAggregateMetricsOnly() async {
        let extractor = GatedExtractor(openImmediately: true)
        await extractor.setShouldThrow(true)
        let coordinator = ExtractionCoordinator(extractor: extractor)

        await coordinator.submit(.synthetic(secondsFromNow: 0))
        await coordinator.waitUntilIdle()

        let metrics = await coordinator.snapshot()
        #expect(metrics.extractionsFailed == 1)
        #expect(metrics.extractionsSucceeded == 0)
        #expect(metrics.lastExtractionAt != nil)
        #expect(await coordinator.latest() == nil)
    }

    @Test
    func providerErrorTextIsNeverRetainedOrEncoded() async throws {
        let extractor = GatedExtractor(openImmediately: true)
        await extractor.setShouldThrow(true)
        let coordinator = ExtractionCoordinator(extractor: extractor)

        await coordinator.submit(.synthetic(secondsFromNow: 0))
        await coordinator.waitUntilIdle()

        let metrics = await coordinator.snapshot()
        let json = String(decoding: try JSONEncoder().encode(metrics), as: UTF8.self)
        #expect(!json.contains(ExtractionTestError.secretMarker))
        #expect(!json.contains("message"))
        #expect(!json.contains("description"))
        #expect(!json.contains("reason"))
    }

    // MARK: - Unconfigured default

    @Test
    func unconfiguredExtractorConsumesFramesWithoutFailingOrFabricating() async {
        let coordinator = ExtractionCoordinator()

        for index in 0..<10 {
            await coordinator.submit(.synthetic(secondsFromNow: Double(index)))
        }
        await coordinator.waitUntilIdle()

        let metrics = await coordinator.snapshot()
        #expect(metrics.status == .notConfigured)
        #expect(metrics.framesReceived == 10)
        // Never invoked, so no error storm and nothing invented.
        #expect(metrics.extractionsStarted == 0)
        #expect(metrics.extractionsFailed == 0)
        #expect(await coordinator.latest() == nil)
        #expect(await coordinator.hasPendingFrame == false)
    }

    @Test
    func noConfiguredExtractorReportsUnavailableRatherThanEmptyContent() async {
        let extractor = NoConfiguredExtractor()
        #expect(!extractor.isConfigured)
        #expect(extractor.processingLocation == .onDevice)
        await #expect(throws: ExtractionError.extractorNotConfigured) {
            try await extractor.extract(from: .synthetic(secondsFromNow: 0))
        }
    }

    // MARK: - Lifecycle

    @Test
    func pauseStopsConsumptionAndReleasesTheHeldFrame() async {
        let extractor = GatedExtractor()
        let coordinator = ExtractionCoordinator(extractor: extractor)
        await coordinator.submit(.synthetic(secondsFromNow: 0))
        await coordinator.submit(.synthetic(secondsFromNow: 1))
        #expect(await coordinator.hasPendingFrame)

        await coordinator.pause()

        #expect(await coordinator.hasPendingFrame == false)
        let paused = await coordinator.snapshot()
        #expect(paused.status == .paused)
        // Cancellation may land before the in-flight extraction body ever runs,
        // so the only guarantee is that nothing new starts after pause.
        #expect(paused.extractionsStarted <= 1)

        await coordinator.submit(.synthetic(secondsFromNow: 2))
        let metrics = await coordinator.snapshot()
        #expect(metrics.framesReceived == paused.framesReceived + 1)
        #expect(metrics.extractionsStarted == paused.extractionsStarted)
        #expect(await coordinator.hasPendingFrame == false)
    }

    @Test
    func pausedObserverStopsFeedingTheCoordinator() async {
        let extractor = GatedExtractor(openImmediately: true)
        let coordinator = ExtractionCoordinator(extractor: extractor)
        let observer = PassiveObserver(
            captureSource: StubCaptureSource(image: .syntheticTestImage(brightness: 90))
        )
        await coordinator.start(frames: await observer.meaningfulFrames())
        await coordinator.pause()

        await observer.captureOneFrame()

        // Nothing is extracted while paused, whatever the observer emits.
        let quiet = await waitUntil { await coordinator.snapshot().extractionsStarted > 0 }
        #expect(!quiet)
        #expect(await coordinator.snapshot().status == .paused)
    }

    // MARK: - Remote-processing consent boundary

    @Test
    func remoteExtractorIsWithheldUntilTheUserOptsIn() async {
        let extractor = GatedExtractor(openImmediately: true, location: .remote)
        let coordinator = ExtractionCoordinator(
            extractor: extractor,
            capability: .onDeviceOnly
        )

        await coordinator.submit(.synthetic(secondsFromNow: 0))
        await coordinator.waitUntilIdle()

        let metrics = await coordinator.snapshot()
        #expect(metrics.framesWithheldPendingConsent == 1)
        #expect(metrics.extractionsStarted == 0)
        #expect(await extractor.receivedTimestamps.isEmpty)
    }

    @Test
    func remoteExtractorRunsOnlyWithExplicitConsent() async {
        let extractor = GatedExtractor(openImmediately: true, location: .remote)
        let coordinator = ExtractionCoordinator(
            extractor: extractor,
            capability: ExtractionCapability(userEnabledRemoteProvider: true)
        )

        await coordinator.submit(.synthetic(secondsFromNow: 0))
        await coordinator.waitUntilIdle()

        #expect(await coordinator.snapshot().extractionsSucceeded == 1)
    }

    @Test
    func defaultCapabilityKeepsFramesOnDevice() {
        let capability = ExtractionCapability.onDeviceOnly
        #expect(!capability.userEnabledRemoteProvider)
        #expect(capability.allowsProcessing(at: .onDevice))
        #expect(!capability.allowsProcessing(at: .remote))
        #expect(ExtractionCapability(userEnabledRemoteProvider: true)
            .allowsProcessing(at: .remote))
    }

    // MARK: - Schema honesty

    @Test
    func schemaKeepsUnknownRepresentableWithoutInventingFields() {
        let message = ExtractedVisibleMessage()
        #expect(message.sender == nil)
        #expect(message.visibleTime == nil)
        #expect(message.text == nil)
        #expect(message.ownership == .unknown)
        #expect(message.kind == .unknown)
        #expect(message.normalizedBounds == nil)

        let frame = ExtractedConversationFrame.empty(capturedAt: Date())
        #expect(frame.chat == nil)
        #expect(frame.messages.isEmpty)
    }

    // MARK: - Architectural guards

    @Test
    func extractionPhaseAddsNoNetworkingOrImagePersistence() throws {
        let source = try appSourceText()
        for forbidden in [
            "URLSession", "URLRequest", "NWConnection", "Network.framework",
            "CGImageDestination", "NSBitmapImageRep", "tiffRepresentation",
            "base64EncodedString", "base64EncodedData"
        ] {
            #expect(!source.contains(forbidden), "\(forbidden) must not appear in this phase")
        }
    }

    @Test
    func passiveObserverStaysIndependentOfAnyProvider() throws {
        let observerSource = try String(
            contentsOf: appSourceDirectory()
                .appendingPathComponent("Capture/PassiveObserver.swift"),
            encoding: .utf8
        )
        for coupling in ["Extract", "OpenAI", "Anthropic", "Gemini"] {
            #expect(!observerSource.contains(coupling))
        }
    }
}

// MARK: - Test support

private func appSourceDirectory() -> URL {
    URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent()
        .deletingLastPathComponent()
        .appendingPathComponent("WeChatCompanion")
}

private func appSourceText() throws -> String {
    let files = FileManager.default.enumerator(
        at: appSourceDirectory(),
        includingPropertiesForKeys: nil
    )?.compactMap { $0 as? URL }.filter { $0.pathExtension == "swift" } ?? []
    return try files.map { try String(contentsOf: $0, encoding: .utf8) }.joined()
}

private func waitUntil(
    timeout: Duration = .milliseconds(400),
    _ condition: @Sendable () async -> Bool
) async -> Bool {
    let deadline = ContinuousClock.now + timeout
    while ContinuousClock.now < deadline {
        if await condition() { return true }
        try? await Task.sleep(for: .milliseconds(5))
    }
    return await condition()
}

enum ExtractionTestError: Error {
    static let secretMarker = "provider-detail-that-must-not-be-stored"
    case providerFailed(detail: String)
}

/// Extractor test double whose completion the test controls, so backpressure
/// assertions are deterministic rather than timing-dependent.
private actor GatedExtractor: FrameExtracting {
    nonisolated let isConfigured = true
    nonisolated let processingLocation: ExtractionProcessingLocation

    private(set) var receivedTimestamps: [Date] = []
    private(set) var maxConcurrent = 0
    private var activeCount = 0
    private var isOpen: Bool
    private var waiters: [CheckedContinuation<Void, Never>] = []
    private var shouldThrow = false

    init(
        openImmediately: Bool = false,
        location: ExtractionProcessingLocation = .onDevice
    ) {
        isOpen = openImmediately
        processingLocation = location
    }

    func setShouldThrow(_ value: Bool) { shouldThrow = value }

    func open() {
        isOpen = true
        let pending = waiters
        waiters = []
        for waiter in pending { waiter.resume() }
    }

    func extract(from frame: ObservedFrame) async throws -> ExtractedConversationFrame {
        receivedTimestamps.append(frame.capturedAt)
        activeCount += 1
        maxConcurrent = max(maxConcurrent, activeCount)
        defer { activeCount -= 1 }

        if !isOpen {
            await withCheckedContinuation { waiters.append($0) }
        }
        if shouldThrow {
            throw ExtractionTestError.providerFailed(detail: ExtractionTestError.secretMarker)
        }
        return ExtractedConversationFrame.empty(capturedAt: frame.capturedAt)
    }
}

private struct StubCaptureSource: WeChatCaptureProviding {
    let image: CGImage

    func captureCurrentVisibleWeChat() async throws -> WeChatCaptureOutcome {
        var outcome = WeChatCaptureOutcome()
        outcome.wechatRunning = true
        outcome.wechatFrontmost = true
        outcome.windowFound = true
        outcome.windowOnScreen = true
        outcome.displayRegionCaptureNonEmpty = true
        outcome.frame = CaptureFrame(
            image: image,
            mode: .visibleDisplayRegion,
            timestamp: Date()
        )
        return outcome
    }
}

private extension ObservedFrame {
    /// Synthetic frame built in code. No real WeChat imagery is used in tests.
    static func synthetic(secondsFromNow: Double) -> ObservedFrame {
        let image = CGImage.syntheticTestImage(brightness: 128)
        return ObservedFrame(
            image: image,
            capturedAt: Date(timeIntervalSince1970: 1_700_000_000 + secondsFromNow),
            captureMode: .visibleDisplayRegion,
            fingerprint: FrameFingerprint(image: image)!
        )
    }
}

private extension CGImage {
    static func syntheticTestImage(brightness: UInt8, side: Int = 64) -> CGImage {
        let context = CGContext(
            data: nil,
            width: side,
            height: side,
            bitsPerComponent: 8,
            bytesPerRow: side,
            space: CGColorSpaceCreateDeviceGray(),
            bitmapInfo: CGImageAlphaInfo.none.rawValue
        )!
        context.setFillColor(gray: CGFloat(brightness) / 255, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: side, height: side))
        return context.makeImage()!
    }
}
