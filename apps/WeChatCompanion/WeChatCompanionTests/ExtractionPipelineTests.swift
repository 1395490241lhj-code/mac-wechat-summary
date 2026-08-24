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
        let session = SystemWindowCaptureSession()
        await session.markSelectedForTesting()
        await coordinator.start(frames: await session.meaningfulFrames())

        await session.ingest(image: .syntheticTestImage(brightness: 30), capturedAt: Date())

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
        let session = SystemWindowCaptureSession()
        await session.markSelectedForTesting()
        await coordinator.start(frames: await session.meaningfulFrames())
        await coordinator.pause()

        await session.ingest(image: .syntheticTestImage(brightness: 90), capturedAt: Date())

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

    // MARK: - Cancellation is not failure

    @Test
    func cancellationErrorCountsAsCancelledNotFailed() async {
        let extractor = GatedExtractor(openImmediately: true)
        await extractor.setError(CancellationError())
        let coordinator = ExtractionCoordinator(extractor: extractor)

        await coordinator.submit(.synthetic(secondsFromNow: 0))
        await coordinator.waitUntilIdle()

        let metrics = await coordinator.snapshot()
        #expect(metrics.extractionsCancelled == 1)
        #expect(metrics.extractionsFailed == 0)
        #expect(metrics.extractionsSucceeded == 0)
        // A cancellation produced no result, so these stay untouched.
        #expect(metrics.lastExtractionAt == nil)
        #expect(metrics.lastCancellationAt != nil)
        #expect(await coordinator.latest() == nil)
    }

    @Test
    func urlSessionCancellationCountsAsCancelledNotFailed() async {
        let extractor = GatedExtractor(openImmediately: true)
        await extractor.setError(URLError(.cancelled))
        let coordinator = ExtractionCoordinator(extractor: extractor)

        await coordinator.submit(.synthetic(secondsFromNow: 0))
        await coordinator.waitUntilIdle()

        let metrics = await coordinator.snapshot()
        #expect(metrics.extractionsCancelled == 1)
        #expect(metrics.extractionsFailed == 0)
    }

    /// Only URLError.cancelled is cancellation; other network errors are real
    /// failures and must not be laundered into the cancelled bucket.
    @Test
    func otherNetworkErrorsRemainFailures() async {
        for code in [URLError.timedOut, .notConnectedToInternet, .badServerResponse] {
            let extractor = GatedExtractor(openImmediately: true)
            await extractor.setError(URLError(code))
            let coordinator = ExtractionCoordinator(extractor: extractor)

            await coordinator.submit(.synthetic(secondsFromNow: 0))
            await coordinator.waitUntilIdle()

            let metrics = await coordinator.snapshot()
            #expect(metrics.extractionsFailed == 1, "\(code) must count as a failure")
            #expect(metrics.extractionsCancelled == 0)
            #expect(metrics.lastExtractionAt != nil)
        }
    }

    @Test
    func genuineProviderErrorStillCountsAsFailure() async {
        let extractor = GatedExtractor(openImmediately: true)
        await extractor.setShouldThrow(true)
        let coordinator = ExtractionCoordinator(extractor: extractor)

        await coordinator.submit(.synthetic(secondsFromNow: 0))
        await coordinator.waitUntilIdle()

        let metrics = await coordinator.snapshot()
        #expect(metrics.extractionsFailed == 1)
        #expect(metrics.extractionsCancelled == 0)
    }

    @Test
    func successfulExtractionAccountingIsUnchanged() async {
        let extractor = GatedExtractor(openImmediately: true)
        let coordinator = ExtractionCoordinator(extractor: extractor)

        await coordinator.submit(.synthetic(secondsFromNow: 0))
        await coordinator.waitUntilIdle()

        let metrics = await coordinator.snapshot()
        #expect(metrics.extractionsSucceeded == 1)
        #expect(metrics.extractionsFailed == 0)
        #expect(metrics.extractionsCancelled == 0)
        #expect(metrics.lastExtractionAt != nil)
        #expect(metrics.lastCancellationAt == nil)
        #expect(await coordinator.latest() != nil)
    }

    /// A cancelled extraction must not overwrite a previously good result.
    @Test
    func cancellationDoesNotReplaceLatestExtraction() async {
        let extractor = GatedExtractor(openImmediately: true)
        let coordinator = ExtractionCoordinator(extractor: extractor)
        await coordinator.submit(.synthetic(secondsFromNow: 0))
        await coordinator.waitUntilIdle()
        let good = await coordinator.latest()
        #expect(good != nil)

        await extractor.setError(CancellationError())
        await coordinator.submit(.synthetic(secondsFromNow: 1))
        await coordinator.waitUntilIdle()

        #expect(await coordinator.latest() == good)
        #expect(await coordinator.snapshot().extractionsCancelled == 1)
    }

    @Test
    func pauseDuringExtractionRecordsCancellationRatherThanFailure() async {
        let extractor = GatedExtractor(openImmediately: true)
        await extractor.setError(CancellationError())
        let coordinator = ExtractionCoordinator(extractor: extractor)

        await coordinator.submit(.synthetic(secondsFromNow: 0))
        await coordinator.waitUntilIdle()
        await coordinator.pause()

        let metrics = await coordinator.snapshot()
        #expect(metrics.extractionsCancelled == 1)
        #expect(metrics.extractionsFailed == 0)
        #expect(metrics.status == .paused)
    }

    /// Pause used to clear the task reference immediately, so a resumed
    /// session could start a second extraction beside the still-unwinding one.
    @Test
    func pauseKeepsTheSlotClaimedUntilTheCancelledTaskExits() async {
        let extractor = GatedExtractor()
        let coordinator = ExtractionCoordinator(extractor: extractor)
        await coordinator.submit(.synthetic(secondsFromNow: 0))

        // Wait until the provider call is genuinely in flight, so the pause
        // below really does interrupt an unfinished extraction.
        let entered = await waitUntil { await extractor.receivedTimestamps.count == 1 }
        #expect(entered)
        #expect(await coordinator.snapshot().extractionsStarted == 1)

        await coordinator.pause()

        // The slot stays claimed: this provider call has not returned yet.
        #expect(await coordinator.hasActiveExtractionTask)
        #expect(await coordinator.hasPendingFrame == false)

        // Resuming and submitting must not start a second concurrent call.
        await coordinator.start(frames: AsyncStream { $0.finish() })
        await coordinator.submit(.synthetic(secondsFromNow: 1))

        // With the old pause(), the freed slot let a second provider call start
        // beside the still-blocked first one. Give that time to happen, then
        // prove it did not.
        let secondCallStarted = await waitUntil {
            await extractor.receivedTimestamps.count > 1
        }
        #expect(!secondCallStarted)
        #expect(await coordinator.snapshot().extractionsStarted == 1)
        #expect(await extractor.maxConcurrent == 1)
        // The new frame waits in the single pending slot instead.
        #expect(await coordinator.hasPendingFrame)

        await extractor.open()
        await coordinator.waitUntilIdle()
        #expect(await extractor.maxConcurrent == 1)
    }

    @Test
    func cancellationTelemetryIsAggregateOnly() async throws {
        let extractor = GatedExtractor(openImmediately: true)
        await extractor.setError(CancellationError())
        let coordinator = ExtractionCoordinator(extractor: extractor)
        await coordinator.submit(.synthetic(secondsFromNow: 0))
        await coordinator.waitUntilIdle()

        let metrics = await coordinator.snapshot()
        let json = String(decoding: try JSONEncoder().encode(metrics), as: UTF8.self)
        #expect(json.contains("extractionsCancelled"))
        #expect(!json.contains("Cancellation error"))
        #expect(!json.contains("reason"))
        #expect(!json.contains("message"))
        #expect(!json.contains(ExtractionTestError.secretMarker))
    }

    // MARK: - Architectural guards

    /// Networking and base64 are legitimate inside a remote provider, and
    /// nowhere else. Everything outside Extraction/Providers must stay offline.
    @Test
    func networkingIsConfinedToTheProviderDirectory() throws {
        let nonProviderSource = try appSourceText(excludingPathComponent: "Providers")
        for forbidden in ["URLSession", "URLRequest", "NWConnection", "base64Encoded"] {
            #expect(
                !nonProviderSource.contains(forbidden),
                "\(forbidden) must stay inside Extraction/Providers"
            )
        }
    }

    /// Frames may be encoded in memory for a request, never written to a file.
    @Test
    func noImageIsEverWrittenToDisk() throws {
        let source = try appSourceText()
        for forbidden in [
            "CGImageDestinationCreateWithURL", "NSBitmapImageRep",
            "tiffRepresentation", "pngData"
        ] {
            #expect(!source.contains(forbidden), "\(forbidden) would persist imagery")
        }
    }

    @Test
    func captureSessionStaysIndependentOfAnyProvider() throws {
        let captureSource = try String(
            contentsOf: appSourceDirectory()
                .appendingPathComponent("Capture/SystemWindowCaptureSession.swift"),
            encoding: .utf8
        )
        for coupling in ["Extract", "OpenAI", "Anthropic", "Gemini"] {
            #expect(!captureSource.contains(coupling))
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

private func appSourceText(excludingPathComponent excluded: String? = nil) throws -> String {
    let files = FileManager.default.enumerator(
        at: appSourceDirectory(),
        includingPropertiesForKeys: nil
    )?.compactMap { $0 as? URL }
        .filter { $0.pathExtension == "swift" }
        .filter { url in
            guard let excluded else { return true }
            return !url.pathComponents.contains(excluded)
        } ?? []
    let joined = try files.map { try String(contentsOf: $0, encoding: .utf8) }
        .joined(separator: "\n")
    // Scan code only: comments legitimately name the APIs we forbid.
    return joined
        .split(separator: "\n", omittingEmptySubsequences: false)
        .filter { !$0.trimmingCharacters(in: .whitespaces).hasPrefix("//") }
        .joined(separator: "\n")
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
    private var errorToThrow: (any Error)?

    init(
        openImmediately: Bool = false,
        location: ExtractionProcessingLocation = .onDevice
    ) {
        isOpen = openImmediately
        processingLocation = location
    }

    func setShouldThrow(_ value: Bool) { shouldThrow = value }

    /// Throw a specific error, so cancellation types can be simulated exactly.
    func setError(_ error: (any Error)?) { errorToThrow = error }

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
        if let errorToThrow { throw errorToThrow }
        if shouldThrow {
            throw ExtractionTestError.providerFailed(detail: ExtractionTestError.secretMarker)
        }
        return ExtractedConversationFrame.empty(capturedAt: frame.capturedAt)
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
