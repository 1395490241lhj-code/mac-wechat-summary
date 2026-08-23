import CoreGraphics
import Foundation
import Testing
@testable import WeChatCompanion

struct PassiveObserverTests {
    @Test
    func stateTracksPermissionVisibilityAndPause() {
        #expect(
            PassiveObserverPolicy.state(
                started: true,
                paused: false,
                screenRecordingGranted: true,
                wechatFrontmost: true
            ) == .observing
        )
        #expect(
            PassiveObserverPolicy.state(
                started: true,
                paused: false,
                screenRecordingGranted: true,
                wechatFrontmost: false
            ) == .waitingForWeChat
        )
        #expect(
            PassiveObserverPolicy.state(
                started: true,
                paused: true,
                screenRecordingGranted: true,
                wechatFrontmost: true
            ) == .paused
        )
        #expect(
            PassiveObserverPolicy.state(
                started: true,
                paused: false,
                screenRecordingGranted: false,
                wechatFrontmost: true
            ) == .permissionRequired
        )
    }

    @Test
    func fingerprintSkipsNoiseAndAcceptsMeaningfulChange() {
        let original = FrameFingerprint(samples: .init(repeating: 0, count: 256))
        let noise = FrameFingerprint(samples: .init(repeating: 8, count: 256))
        let changed = FrameFingerprint(samples: .init(repeating: 255, count: 32) + .init(repeating: 0, count: 224))

        #expect(!noise.isMeaningfullyDifferent(from: original))
        #expect(changed.isMeaningfullyDifferent(from: original))
    }

    @Test
    func captureGatePreventsBacklog() {
        var gate = ObserverCaptureGate()
        let firstBegin = gate.begin()
        let beginWhileBusy = gate.begin()
        gate.finish()
        let beginAfterFinish = gate.begin()

        #expect(firstBegin)
        #expect(!beginWhileBusy)
        #expect(beginAfterFinish)
        #expect(PassiveObserver.frameBufferLimit == 1)
    }

    @Test
    func metricsPersistenceContainsOnlyAggregateMetadata() throws {
        var metrics = PassiveObserverMetrics()
        metrics.framesSampled = 8
        metrics.meaningfulFramesObserved = 2
        metrics.duplicateFramesSkipped = 6
        let data = try JSONEncoder().encode(metrics)
        let json = String(decoding: data, as: UTF8.self)

        #expect(!json.contains("image"))
        #expect(!json.contains("fingerprint"))
        #expect(!json.contains("content"))
        #expect(!json.contains("title"))
    }

    @Test
    func eachSampleResolvesTheCaptureSourceAgain() async {
        let source = CountingCaptureSource()
        await source.setOutcome(.visibleFrame(CGImage.solidTestImage(brightness: 40)))
        let observer = PassiveObserver(captureSource: source)

        await observer.captureOneFrame()
        await observer.captureOneFrame()
        await observer.captureOneFrame()

        let callCount = await source.callCount
        let metrics = await observer.snapshot()
        #expect(callCount == 3)
        #expect(metrics.framesSampled == 3)
        #expect(metrics.meaningfulFramesObserved == 1)
        #expect(metrics.duplicateFramesSkipped == 2)
    }

    @Test
    func captureFailuresAccumulateWithoutTouchingLifecycleState() async {
        let source = CountingCaptureSource()
        await source.setError(CaptureTestError.unavailable)
        let observer = PassiveObserver(captureSource: source)
        let stateBefore = await observer.snapshot().state

        await observer.captureOneFrame()
        await observer.captureOneFrame()
        let failed = await observer.snapshot()

        #expect(failed.captureFailures == 2)
        #expect(failed.consecutiveCaptureFailures == 2)
        #expect(failed.lastCaptureFailureAt != nil)
        #expect(failed.state == stateBefore)
    }

    @Test
    func usableCaptureResetsConsecutiveFailures() async {
        let source = CountingCaptureSource()
        await source.setError(CaptureTestError.unavailable)
        let observer = PassiveObserver(captureSource: source)
        await observer.captureOneFrame()

        await source.setOutcome(.visibleFrame(CGImage.solidTestImage(brightness: 200)))
        await observer.captureOneFrame()
        let recovered = await observer.snapshot()

        #expect(recovered.consecutiveCaptureFailures == 0)
        #expect(recovered.captureFailures == 1)
        #expect(recovered.lastCaptureFailureAt != nil)
        #expect(recovered.meaningfulFramesObserved == 1)
    }

    @Test
    func emptyFrameCountsAsFailureRatherThanLifecycleChange() async {
        let source = CountingCaptureSource()
        var outcome = WeChatCaptureOutcome()
        outcome.wechatFrontmost = true
        outcome.windowFound = true
        await source.setOutcome(outcome)
        let observer = PassiveObserver(captureSource: source)
        let stateBefore = await observer.snapshot().state

        await observer.captureOneFrame()
        let metrics = await observer.snapshot()

        #expect(metrics.consecutiveCaptureFailures == 1)
        #expect(metrics.meaningfulFramesObserved == 0)
        #expect(metrics.state == stateBefore)
    }

    @Test
    func failureMetricsPersistOnlyAggregateMetadata() throws {
        var metrics = PassiveObserverMetrics()
        metrics.captureFailures = 3
        metrics.consecutiveCaptureFailures = 2
        metrics.lastCaptureFailureAt = Date(timeIntervalSince1970: 1_700_000_000)
        let json = String(decoding: try JSONEncoder().encode(metrics), as: UTF8.self)

        #expect(json.contains("consecutiveCaptureFailures"))
        #expect(!json.contains("error"))
        #expect(!json.contains("message"))
        #expect(!json.contains("description"))
    }

    @Test
    func pollingStartsOnceAndStopsWhilePaused() {
        #expect(ObserverPollingPolicy.shouldStartPolling(isPolling: false))
        #expect(!ObserverPollingPolicy.shouldStartPolling(isPolling: true))
    }

    @MainActor
    @Test
    func pauseStopsMetricsPollingAndStartNeverStacksLoops() async {
        let model = AppModel(observer: PassiveObserver(captureSource: CountingCaptureSource()))
        #expect(!model.isPollingObserverMetrics)

        await model.startObserver()
        #expect(model.isPollingObserverMetrics)

        await model.startObserver()
        #expect(model.isPollingObserverMetrics)

        await model.pauseObserver()
        #expect(!model.isPollingObserverMetrics)

        await model.startObserver()
        #expect(model.isPollingObserverMetrics)
        await model.pauseObserver()
        #expect(!model.isPollingObserverMetrics)
    }
}

private enum CaptureTestError: Error {
    case unavailable
}

/// Records how many times the observer resolved the capture source.
private actor CountingCaptureSource: WeChatCaptureProviding {
    private(set) var callCount = 0
    private var outcome = WeChatCaptureOutcome()
    private var error: Error?

    func setOutcome(_ outcome: WeChatCaptureOutcome) {
        self.outcome = outcome
        error = nil
    }

    func setError(_ error: Error) {
        self.error = error
    }

    func captureCurrentVisibleWeChat() async throws -> WeChatCaptureOutcome {
        callCount += 1
        if let error { throw error }
        return outcome
    }
}

private extension WeChatCaptureOutcome {
    static func visibleFrame(_ image: CGImage) -> Self {
        var outcome = Self()
        outcome.wechatRunning = true
        outcome.wechatFrontmost = true
        outcome.windowFound = true
        outcome.windowOnScreen = true
        outcome.displayRegionCaptureAttempted = true
        outcome.displayRegionCaptureNonEmpty = true
        outcome.frame = CaptureFrame(
            image: image,
            mode: .visibleDisplayRegion,
            timestamp: Date()
        )
        return outcome
    }
}

private extension CGImage {
    static func solidTestImage(brightness: UInt8, side: Int = 64) -> CGImage {
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
