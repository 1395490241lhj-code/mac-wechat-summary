import CoreImage
import CoreMedia
import CoreVideo
import Foundation
import ScreenCaptureKit

/// Lifecycle of the user-selected window capture.
///
/// There is deliberately no "waiting for visible WeChat" state: once the user
/// has picked a window through the system picker, observation continues while
/// other apps -- including this one -- are in front of it.
enum WindowCaptureState: String, Codable, Sendable {
    case needsWindowSelection
    case observing
    case paused
    case selectionLost

    var label: String {
        switch self {
        case .needsWindowSelection: "Not Selected"
        case .observing: "Observing"
        case .paused: "Paused"
        case .selectionLost: "Selection Lost"
        }
    }
}

/// Aggregate-only capture telemetry. No titles, no pixels, no content.
struct WindowCaptureMetrics: Codable, Equatable, Sendable {
    var state: WindowCaptureState = .needsWindowSelection
    var framesReceived = 0
    var meaningfulFramesObserved = 0
    var duplicateFramesSkipped = 0
    var incompleteFramesIgnored = 0
    var selectionLostCount = 0
    var lastFrameAt: Date?
    var contentWidth = 0
    var contentHeight = 0
    var pointPixelScale = 0.0
}

enum ObserverPollingPolicy {
    /// A metrics polling loop is created only when none is already running, so
    /// repeated start calls can never stack duplicate loops.
    static func shouldStartPolling(isPolling: Bool) -> Bool { !isPolling }
}

enum WindowCapturePolicy {
    static func state(
        hasSelection: Bool,
        isPaused: Bool,
        selectionLost: Bool
    ) -> WindowCaptureState {
        if selectionLost { return .selectionLost }
        guard hasSelection else { return .needsWindowSelection }
        return isPaused ? .paused : .observing
    }
}

/// Production capture built on Apple's system window-sharing picker.
///
/// The user selects the WeChat conversation window themselves; we retain and
/// stream the EXACT `SCContentFilter` the picker hands back and never rebuild
/// it from a window ID, process ID, or title. Only that one window's pixels are
/// ever captured -- strictly more private than capturing a whole display and
/// cropping, which is what this replaced.
///
/// Validated on real hardware: the selected window keeps producing correct,
/// updating frames while WeChat Companion itself is foregrounded in front of
/// it. Behaviour when the window is minimised or on another Space has NOT been
/// validated and is not claimed.
actor SystemWindowCaptureSession {
    /// ~2 fps, matching the processing target the previous architecture used.
    static let minimumFrameInterval = CMTime(value: 1, timescale: 2)
    static let frameBufferLimit = 1

    private var metrics = WindowCaptureMetrics()
    private var selectedFilter: SCContentFilter?
    private var stream: SCStream?
    private var pickerObserver: WindowPickerObserver?
    private var outputBridge: WindowStreamOutput?
    private var lastAcceptedFingerprint: FrameFingerprint?
    private var frameContinuation: AsyncStream<ObservedFrame>.Continuation?
    private var latestPreviewFrame: ObservedFrame?
    private var hasSelection = false
    private var isPaused = false
    private var selectionLost = false

    // MARK: - Selection

    /// Presents the macOS content-sharing picker restricted to a single window.
    /// We never activate, move, or otherwise control the target app.
    func selectWindow() async {
        let observer = WindowPickerObserver(
            onFilter: { [weak self] box in
                Task { await self?.applySelection(box.filter) }
            },
            onCancel: {},
            onFail: { [weak self] in
                Task { await self?.handleSelectionLost() }
            }
        )
        pickerObserver = observer

        await MainActor.run {
            let picker = SCContentSharingPicker.shared
            var configuration = SCContentSharingPickerConfiguration()
            configuration.allowedPickerModes = [.singleWindow]
            picker.defaultConfiguration = configuration
            picker.maximumStreamCount = 1
            picker.add(observer)
            picker.isActive = true
            picker.present(using: .window)
        }
    }

    private func applySelection(_ filter: SCContentFilter) async {
        // Changing selection must never leave the previous stream running.
        await teardownStream()
        selectedFilter = filter
        hasSelection = true
        selectionLost = false
        isPaused = false
        lastAcceptedFingerprint = nil
        metrics.contentWidth = Int(filter.contentRect.width)
        metrics.contentHeight = Int(filter.contentRect.height)
        metrics.pointPixelScale = CGFloat(filter.pointPixelScale)
        await startStream()
    }

    // MARK: - Lifecycle

    func pause() async {
        guard hasSelection else { return }
        isPaused = true
        // Actually stop capturing rather than discarding frames: a paused
        // observer must not be reading the screen at all.
        await teardownStream()
        lastAcceptedFingerprint = nil
        refreshState()
    }

    func resume() async {
        guard hasSelection, !selectionLost else { return }
        isPaused = false
        await startStream()
    }

    /// Full stop: releases the filter, the stream and the retained preview.
    func stopObserving() async {
        await teardownStream()
        selectedFilter = nil
        hasSelection = false
        selectionLost = false
        isPaused = false
        lastAcceptedFingerprint = nil
        latestPreviewFrame = nil
        if pickerObserver != nil {
            await MainActor.run { SCContentSharingPicker.shared.isActive = false }
        }
        pickerObserver = nil
        refreshState()
    }

    private func startStream() async {
        guard let filter = selectedFilter, !isPaused else {
            refreshState()
            return
        }
        let configuration = SCStreamConfiguration()
        let scale = CGFloat(filter.pointPixelScale)
        configuration.width = max(1, Int((filter.contentRect.width * scale).rounded()))
        configuration.height = max(1, Int((filter.contentRect.height * scale).rounded()))
        configuration.showsCursor = false
        configuration.queueDepth = 3
        configuration.pixelFormat = kCVPixelFormatType_32BGRA
        configuration.minimumFrameInterval = Self.minimumFrameInterval

        let bridge = WindowStreamOutput(
            onImage: { [weak self] image, capturedAt in
                Task { await self?.ingest(image: image, capturedAt: capturedAt) }
            },
            onIncomplete: { [weak self] in
                Task { await self?.noteIncompleteFrame() }
            },
            onStopped: { [weak self] in
                Task { await self?.handleSelectionLost() }
            }
        )
        outputBridge = bridge

        let newStream = SCStream(filter: filter, configuration: configuration, delegate: bridge)
        do {
            try newStream.addStreamOutput(
                bridge,
                type: .screen,
                sampleHandlerQueue: DispatchQueue(label: "wechatcompanion.windowcapture")
            )
            try await newStream.startCapture()
            stream = newStream
        } catch {
            // Fail honestly: no fallback to display capture.
            await handleSelectionLost()
            return
        }
        refreshState()
    }

    private func teardownStream() async {
        if let stream {
            try? await stream.stopCapture()
        }
        stream = nil
        outputBridge = nil
    }

    private func handleSelectionLost() async {
        await teardownStream()
        guard !selectionLost else { return }
        selectionLost = true
        metrics.selectionLostCount += 1
        lastAcceptedFingerprint = nil
        refreshState()
    }

    private func refreshState() {
        metrics.state = WindowCapturePolicy.state(
            hasSelection: hasSelection,
            isPaused: isPaused,
            selectionLost: selectionLost
        )
    }

    // MARK: - Frame intake

    /// Processes one COMPLETE frame. Internal so tests can drive the pipeline
    /// without a real stream.
    func ingest(image: CGImage, capturedAt: Date) {
        guard metrics.state == .observing else { return }
        metrics.framesReceived += 1
        metrics.lastFrameAt = capturedAt

        guard let fingerprint = FrameFingerprint(image: image) else { return }
        if let previous = lastAcceptedFingerprint,
           !fingerprint.isMeaningfullyDifferent(from: previous) {
            metrics.duplicateFramesSkipped += 1
            return
        }

        lastAcceptedFingerprint = fingerprint
        metrics.meaningfulFramesObserved += 1
        let observed = ObservedFrame(
            image: image,
            capturedAt: capturedAt,
            captureMode: .systemSelectedWindow,
            fingerprint: fingerprint
        )
        latestPreviewFrame = observed
        frameContinuation?.yield(observed)
    }

    /// Incomplete frames carry no usable pixels and are never processed.
    func noteIncompleteFrame() {
        metrics.incompleteFramesIgnored += 1
    }

    // MARK: - Output

    func snapshot() -> WindowCaptureMetrics { metrics }

    /// Newest-frame semantics with a single slot, so frames cannot pile up
    /// behind a slow extractor.
    func meaningfulFrames() -> AsyncStream<ObservedFrame> {
        let pair = AsyncStream<ObservedFrame>.makeStream(
            bufferingPolicy: .bufferingNewest(Self.frameBufferLimit)
        )
        frameContinuation?.finish()
        frameContinuation = pair.continuation
        return pair.stream
    }

    /// Development preview: the exact frame handed to the extraction stream.
    /// Memory only, survives pause, released on stop.
    func latestPreview() -> ObservedFrame? { latestPreviewFrame }

    func clearPreview() { latestPreviewFrame = nil }

    // MARK: - Test seams

    /// Marks a selection active without presenting the picker or opening a real
    /// stream, so frame intake can be exercised deterministically in tests.
    func markSelectedForTesting() {
        hasSelection = true
        selectionLost = false
        isPaused = false
        refreshState()
    }

    /// Drives the same path the stream delegate uses when capture stops.
    func simulateSelectionLostForTests() async {
        await handleSelectionLost()
    }
}

/// SCContentFilter is not Sendable; it is produced on the picker's queue and
/// only read inside the session actor.
struct WindowFilterBox: @unchecked Sendable {
    let filter: SCContentFilter
}

private final class WindowPickerObserver: NSObject, SCContentSharingPickerObserver,
    @unchecked Sendable {
    private let onFilter: @Sendable (WindowFilterBox) -> Void
    private let onCancel: @Sendable () -> Void
    private let onFail: @Sendable () -> Void

    init(
        onFilter: @escaping @Sendable (WindowFilterBox) -> Void,
        onCancel: @escaping @Sendable () -> Void,
        onFail: @escaping @Sendable () -> Void
    ) {
        self.onFilter = onFilter
        self.onCancel = onCancel
        self.onFail = onFail
    }

    func contentSharingPicker(
        _ picker: SCContentSharingPicker,
        didUpdateWith filter: SCContentFilter,
        for stream: SCStream?
    ) {
        onFilter(WindowFilterBox(filter: filter))
    }

    func contentSharingPicker(_ picker: SCContentSharingPicker, didCancelFor stream: SCStream?) {
        onCancel()
    }

    func contentSharingPickerStartDidFailWithError(_ error: any Error) {
        onFail()
    }
}

/// Converts complete frames to images on the stream's own queue. Nothing is
/// logged and nothing is written to disk.
private final class WindowStreamOutput: NSObject, SCStreamOutput, SCStreamDelegate,
    @unchecked Sendable {
    private let context = CIContext(options: nil)
    private let onImage: @Sendable (CGImage, Date) -> Void
    private let onIncomplete: @Sendable () -> Void
    private let onStopped: @Sendable () -> Void

    init(
        onImage: @escaping @Sendable (CGImage, Date) -> Void,
        onIncomplete: @escaping @Sendable () -> Void,
        onStopped: @escaping @Sendable () -> Void
    ) {
        self.onImage = onImage
        self.onIncomplete = onIncomplete
        self.onStopped = onStopped
    }

    func stream(
        _ stream: SCStream,
        didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
        of type: SCStreamOutputType
    ) {
        guard type == .screen, CMSampleBufferIsValid(sampleBuffer) else { return }
        guard isComplete(sampleBuffer),
              let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else {
            onIncomplete()
            return
        }
        let ciImage = CIImage(cvPixelBuffer: pixelBuffer)
        guard let image = context.createCGImage(ciImage, from: ciImage.extent) else {
            onIncomplete()
            return
        }
        onImage(image, Date())
    }

    func stream(_ stream: SCStream, didStopWithError error: any Error) {
        onStopped()
    }

    private func isComplete(_ sampleBuffer: CMSampleBuffer) -> Bool {
        guard let attachments = CMSampleBufferGetSampleAttachmentsArray(
            sampleBuffer, createIfNecessary: false
        ) as? [[SCStreamFrameInfo: Any]],
            let first = attachments.first,
            let rawStatus = first[.status] as? Int,
            let status = SCFrameStatus(rawValue: rawStatus)
        else { return false }
        return status == .complete
    }
}
