import CoreGraphics
import Foundation
import Testing
@testable import WeChatCompanion

struct SystemWindowCaptureSessionTests {
    // MARK: - Lifecycle state

    @Test
    func stateStartsAtNeedsWindowSelection() async {
        let session = SystemWindowCaptureSession()
        #expect(await session.snapshot().state == .needsWindowSelection)
        #expect(await session.latestPreview() == nil)
    }

    @Test
    func lifecycleStateFollowsSelectionPauseAndLoss() {
        #expect(WindowCapturePolicy.state(
            hasSelection: false, isPaused: false, selectionLost: false
        ) == .needsWindowSelection)
        #expect(WindowCapturePolicy.state(
            hasSelection: true, isPaused: false, selectionLost: false
        ) == .observing)
        #expect(WindowCapturePolicy.state(
            hasSelection: true, isPaused: true, selectionLost: false
        ) == .paused)
        // Loss wins over everything: we never silently keep observing.
        #expect(WindowCapturePolicy.state(
            hasSelection: true, isPaused: false, selectionLost: true
        ) == .selectionLost)
        #expect(WindowCapturePolicy.state(
            hasSelection: false, isPaused: false, selectionLost: true
        ) == .selectionLost)
    }

    /// There is no "WeChat must be frontmost" input to the state machine at all.
    @Test
    func observationDoesNotDependOnFrontmostApp() async {
        let session = await SystemWindowCaptureSession.observingForTests()
        // Nothing about the frontmost application is consulted; frames are
        // accepted purely because a window was selected.
        await session.ingest(image: .testImage(brightness: 40), capturedAt: Date())
        #expect(await session.snapshot().meaningfulFramesObserved == 1)
        #expect(await session.snapshot().state == .observing)
    }

    // MARK: - Frame intake

    @Test
    func completeFrameIsProcessedAndPreviewed() async {
        let session = await SystemWindowCaptureSession.observingForTests()
        let capturedAt = Date(timeIntervalSince1970: 1_700_000_000)

        await session.ingest(image: .testImage(brightness: 60), capturedAt: capturedAt)

        let metrics = await session.snapshot()
        #expect(metrics.framesReceived == 1)
        #expect(metrics.meaningfulFramesObserved == 1)
        #expect(metrics.duplicateFramesSkipped == 0)
        #expect(metrics.lastFrameAt == capturedAt)
        let preview = await session.latestPreview()
        #expect(preview?.captureMode == .systemSelectedWindow)
        #expect(preview?.capturedAt == capturedAt)
    }

    @Test
    func incompleteFramesAreIgnoredAndNeverProcessed() async {
        let session = await SystemWindowCaptureSession.observingForTests()

        await session.noteIncompleteFrame()
        await session.noteIncompleteFrame()

        let metrics = await session.snapshot()
        #expect(metrics.incompleteFramesIgnored == 2)
        #expect(metrics.framesReceived == 0)
        #expect(metrics.meaningfulFramesObserved == 0)
        #expect(await session.latestPreview() == nil)
    }

    @Test
    func duplicateFramesAreDeduplicated() async {
        let session = await SystemWindowCaptureSession.observingForTests()

        await session.ingest(image: .testImage(brightness: 100), capturedAt: Date())
        await session.ingest(image: .testImage(brightness: 100), capturedAt: Date())
        await session.ingest(image: .testImage(brightness: 240), capturedAt: Date())

        let metrics = await session.snapshot()
        #expect(metrics.framesReceived == 3)
        #expect(metrics.meaningfulFramesObserved == 2)
        #expect(metrics.duplicateFramesSkipped == 1)
    }

    @Test
    func pausedSessionProcessesNoFrames() async {
        let session = await SystemWindowCaptureSession.observingForTests()
        await session.ingest(image: .testImage(brightness: 30), capturedAt: Date())
        await session.pause()

        await session.ingest(image: .testImage(brightness: 200), capturedAt: Date())

        let metrics = await session.snapshot()
        #expect(metrics.state == .paused)
        #expect(metrics.framesReceived == 1)
        #expect(metrics.meaningfulFramesObserved == 1)
        // The last good preview survives a pause so it can be inspected.
        #expect(await session.latestPreview() != nil)
    }

    // MARK: - Bounded output

    @Test
    func meaningfulFramesStreamKeepsOnlyTheNewestFrame() async {
        #expect(SystemWindowCaptureSession.frameBufferLimit == 1)
        let session = await SystemWindowCaptureSession.observingForTests()
        let stream = await session.meaningfulFrames()

        // Nothing consumes the stream while these are produced.
        for index in 0..<6 {
            await session.ingest(
                image: .testImage(brightness: UInt8(20 + index * 40)),
                capturedAt: Date(timeIntervalSince1970: Double(1_700_000_000 + index))
            )
        }

        var delivered: [Date] = []
        for await frame in stream {
            delivered.append(frame.capturedAt)
            break
        }
        // Newest wins: the first buffered frame is the last one produced.
        #expect(delivered == [Date(timeIntervalSince1970: 1_700_000_005)])
    }

    @Test
    func requestingANewStreamFinishesThePreviousOne() async {
        let session = await SystemWindowCaptureSession.observingForTests()
        let first = await session.meaningfulFrames()
        _ = await session.meaningfulFrames()

        var finished = true
        for await _ in first { finished = false }
        #expect(finished)
    }

    // MARK: - Stop and selection loss

    @Test
    func stopReleasesSelectionAndPreview() async {
        let session = await SystemWindowCaptureSession.observingForTests()
        await session.ingest(image: .testImage(brightness: 80), capturedAt: Date())
        #expect(await session.latestPreview() != nil)

        await session.stopObserving()

        #expect(await session.snapshot().state == .needsWindowSelection)
        #expect(await session.latestPreview() == nil)
    }

    @Test
    func selectionLossStopsProcessingAndRequiresReselection() async {
        let session = await SystemWindowCaptureSession.observingForTests()
        await session.ingest(image: .testImage(brightness: 50), capturedAt: Date())

        await session.simulateSelectionLostForTests()

        let metrics = await session.snapshot()
        #expect(metrics.state == .selectionLost)
        #expect(metrics.selectionLostCount == 1)

        // No automatic recovery: further frames are not processed.
        await session.ingest(image: .testImage(brightness: 220), capturedAt: Date())
        #expect(await session.snapshot().meaningfulFramesObserved == 1)
    }

    // MARK: - Architectural guards

    /// Production capture must not reconstruct the filter, capture a display,
    /// crop, or read a window title.
    @Test
    func productionCaptureUsesOnlyThePickerFilter() throws {
        let source = try sessionSource()
        #expect(source.contains("SCContentSharingPicker"))
        #expect(source.contains(".singleWindow"))
        #expect(source.contains("SCStream(filter: filter"))

        for forbidden in [
            "desktopIndependentWindow", "excludingWindows", "including: [",
            "sourceRect", "DisplayCropGeometry", "cropping(to:",
            "kCGWindowName", "frontmostApplication", "CGDisplayBounds"
        ] {
            #expect(!source.contains(forbidden), "\(forbidden) must not be in production capture")
        }
    }

    @Test
    func productionCaptureNeverPersistsOrExportsImages() throws {
        let source = try sessionSource()
        for forbidden in [
            "CGImageDestination", "NSBitmapImageRep", "pngData", "tiffRepresentation",
            "base64", "write(to:", "NSPasteboard"
        ] {
            #expect(!source.contains(forbidden), "\(forbidden) must not be in production capture")
        }
        // Matched as calls, not substrings: FrameFingerprint( contains "print(".
        for logging in ["print(", "NSLog(", "os_log("] {
            #expect(!Self.containsCall(logging, in: source), "\(logging) must not be called")
        }
    }

    /// True only when `needle` appears as a call rather than inside a longer
    /// identifier.
    static func containsCall(_ needle: String, in source: String) -> Bool {
        var searchRange = source.startIndex..<source.endIndex
        while let range = source.range(of: needle, range: searchRange) {
            let isStart = range.lowerBound == source.startIndex
            let preceding = isStart
                ? nil : source[source.index(before: range.lowerBound)]
            if let preceding {
                if !(preceding.isLetter || preceding.isNumber || preceding == "_") {
                    return true
                }
            } else {
                return true
            }
            searchRange = range.upperBound..<source.endIndex
        }
        return false
    }

    /// The legacy display-capture source must not be reachable from production
    /// observation, so it can never become a silent fallback.
    @Test
    func legacyCaptureSourceIsDiagnosticsOnly() throws {
        let session = try sessionSource()
        #expect(!session.contains("WeChatCaptureSource"))
        #expect(!session.contains("WeChatWindowLocator"))

        let appModel = try appSource(named: "AppModel.swift")
        #expect(!appModel.contains("WeChatCaptureSource"))
    }

    private func sessionSource() throws -> String {
        try appSource(named: "Capture/SystemWindowCaptureSession.swift")
    }

    private func appSource(named path: String) throws -> String {
        try String(
            contentsOf: URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .appendingPathComponent("WeChatCompanion")
                .appendingPathComponent(path),
            encoding: .utf8
        )
    }
}

private extension SystemWindowCaptureSession {
    /// A session behaving as though the user already picked a window.
    static func observingForTests() async -> SystemWindowCaptureSession {
        let session = SystemWindowCaptureSession()
        await session.markSelectedForTesting()
        return session
    }
}

private extension CGImage {
    /// Synthetic frame. No real WeChat imagery exists in any test.
    static func testImage(brightness: UInt8, side: Int = 64) -> CGImage {
        let context = CGContext(
            data: nil, width: side, height: side, bitsPerComponent: 8,
            bytesPerRow: side, space: CGColorSpaceCreateDeviceGray(),
            bitmapInfo: CGImageAlphaInfo.none.rawValue
        )!
        context.setFillColor(gray: CGFloat(brightness) / 255, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: side, height: side))
        return context.makeImage()!
    }
}
