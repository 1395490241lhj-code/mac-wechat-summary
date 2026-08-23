import AppKit
import CoreGraphics
import Foundation

enum PassiveObserverState: String, Codable, Sendable {
    case stopped
    case waitingForWeChat
    case observing
    case paused
    case permissionRequired

    var label: String {
        switch self {
        case .stopped: "Stopped"
        case .waitingForWeChat: "Waiting for WeChat"
        case .observing: "Observing"
        case .paused: "Paused"
        case .permissionRequired: "Screen Recording Required"
        }
    }
}

struct PassiveObserverMetrics: Codable, Equatable, Sendable {
    var state: PassiveObserverState = .stopped
    var observationStartedAt: Date?
    var lastCaptureAt: Date?
    var framesSampled = 0
    var meaningfulFramesObserved = 0
    var duplicateFramesSkipped = 0
    var captureFailures = 0
    var consecutiveCaptureFailures = 0
    var lastCaptureFailureAt: Date?
    var samplesDroppedWhileBusy = 0
    var currentCaptureMode: CaptureMode?
    var currentImageWidth = 0
    var currentImageHeight = 0
    var samplingRate = 0.0
}

enum PassiveObserverPolicy {
    static func state(
        started: Bool,
        paused: Bool,
        screenRecordingGranted: Bool,
        wechatFrontmost: Bool
    ) -> PassiveObserverState {
        if !started { return .stopped }
        if paused { return .paused }
        if !screenRecordingGranted { return .permissionRequired }
        return wechatFrontmost ? .observing : .waitingForWeChat
    }
}

enum ObserverPollingPolicy {
    /// A metrics polling loop is created only when none is already running, so
    /// repeated Start calls can never stack duplicate loops.
    static func shouldStartPolling(isPolling: Bool) -> Bool { !isPolling }
}

struct ObserverCaptureGate {
    private(set) var isBusy = false

    mutating func begin() -> Bool {
        guard !isBusy else { return false }
        isBusy = true
        return true
    }

    mutating func finish() {
        isBusy = false
    }
}

/// The capture seam the observer samples through. Injectable so tests can prove
/// that every sample resolves the capture source again.
protocol WeChatCaptureProviding: Sendable {
    func captureCurrentVisibleWeChat() async throws -> WeChatCaptureOutcome
}

extension WeChatCaptureSource: WeChatCaptureProviding {}

actor PassiveObserver {
    static let sampleInterval = Duration.milliseconds(500)
    static let frameBufferLimit = 1

    private let captureSource: any WeChatCaptureProviding
    private var metrics = PassiveObserverMetrics()
    private var lastAcceptedFingerprint: FrameFingerprint?
    private var captureGate = ObserverCaptureGate()
    private var samplingTask: Task<Void, Never>?
    private var workspaceTokens: [NSObjectProtocol] = []
    private var screenToken: NSObjectProtocol?
    private var started = false
    private var paused = false
    private var frameContinuation: AsyncStream<ObservedFrame>.Continuation?
    private var frameConsumerID: UUID?

    init(captureSource: any WeChatCaptureProviding = WeChatCaptureSource()) {
        self.captureSource = captureSource
    }

    func start() {
        started = true
        paused = false
        if metrics.observationStartedAt == nil { metrics.observationStartedAt = Date() }
        installLifecycleObserversIfNeeded()
        refreshState()
        guard metrics.state != .permissionRequired else { return }
        if samplingTask == nil {
            samplingTask = Task { [weak self] in
                while !Task.isCancelled {
                    await self?.tick()
                    try? await Task.sleep(for: Self.sampleInterval)
                }
            }
        }
    }

    func pause() {
        paused = true
        lastAcceptedFingerprint = nil
        refreshState()
    }

    func stop() {
        started = false
        paused = false
        samplingTask?.cancel()
        samplingTask = nil
        lastAcceptedFingerprint = nil
        removeLifecycleObservers()
        metrics.state = .stopped
    }

    func snapshot() -> PassiveObserverMetrics {
        metrics
    }

    func meaningfulFrames() -> AsyncStream<ObservedFrame> {
        let consumerID = UUID()
        let pair = AsyncStream<ObservedFrame>.makeStream(
            bufferingPolicy: .bufferingNewest(Self.frameBufferLimit)
        )
        frameContinuation?.finish()
        frameContinuation = pair.continuation
        frameConsumerID = consumerID
        pair.continuation.onTermination = { [weak self] _ in
            Task { await self?.removeFrameConsumer(consumerID) }
        }
        return pair.stream
    }

    private func removeFrameConsumer(_ consumerID: UUID) {
        guard frameConsumerID == consumerID else { return }
        frameContinuation = nil
        frameConsumerID = nil
    }

    private func tick() {
        refreshState()
        guard metrics.state == .observing else { return }
        guard captureGate.begin() else {
            metrics.samplesDroppedWhileBusy += 1
            return
        }
        Task { [weak self] in await self?.captureOneFrame() }
    }

    /// Performs exactly one sample. The sampling loop calls this once per tick,
    /// so each sample resolves the capture source (and its window) again.
    func captureOneFrame() async {
        defer { captureGate.finish() }
        do {
            let outcome = try await captureSource.captureCurrentVisibleWeChat()
            metrics.framesSampled += 1
            metrics.lastCaptureAt = Date()
            updateSamplingRate()

            guard outcome.wechatFrontmost else {
                lastAcceptedFingerprint = nil
                refreshState()
                return
            }
            guard let frame = outcome.frame,
                  let fingerprint = FrameFingerprint(image: frame.image) else {
                recordCaptureFailure()
                return
            }

            recordUsableCapture()
            metrics.currentCaptureMode = frame.mode
            metrics.currentImageWidth = frame.image.width
            metrics.currentImageHeight = frame.image.height
            if let previous = lastAcceptedFingerprint,
               !fingerprint.isMeaningfullyDifferent(from: previous) {
                metrics.duplicateFramesSkipped += 1
                return
            }

            lastAcceptedFingerprint = fingerprint
            metrics.meaningfulFramesObserved += 1
            frameContinuation?.yield(
                ObservedFrame(
                    image: frame.image,
                    capturedAt: frame.timestamp,
                    captureMode: frame.mode,
                    fingerprint: fingerprint
                )
            )
        } catch {
            // Only aggregate metadata is recorded; the error itself is never
            // stored or logged, so no captured content can leak into metrics.
            recordCaptureFailure()
        }
    }

    private func recordCaptureFailure() {
        metrics.captureFailures += 1
        metrics.consecutiveCaptureFailures += 1
        metrics.lastCaptureFailureAt = Date()
    }

    private func recordUsableCapture() {
        metrics.consecutiveCaptureFailures = 0
    }

    private func refreshState() {
        let frontmost = NSWorkspace.shared.frontmostApplication?.bundleIdentifier
            == WeChatWindowLocator.bundleIdentifier
        let next = PassiveObserverPolicy.state(
            started: started,
            paused: paused,
            screenRecordingGranted: CGPreflightScreenCaptureAccess(),
            wechatFrontmost: frontmost
        )
        if metrics.state == .observing, next != .observing {
            lastAcceptedFingerprint = nil
        }
        metrics.state = next
    }

    private func updateSamplingRate() {
        guard let startedAt = metrics.observationStartedAt else { return }
        let elapsed = max(Date().timeIntervalSince(startedAt), 0.001)
        metrics.samplingRate = Double(metrics.framesSampled) / elapsed
    }

    private func installLifecycleObserversIfNeeded() {
        guard workspaceTokens.isEmpty, screenToken == nil else { return }
        let workspaceCenter = NSWorkspace.shared.notificationCenter
        let names: [Notification.Name] = [
            NSWorkspace.didActivateApplicationNotification,
            NSWorkspace.didTerminateApplicationNotification,
            NSWorkspace.didLaunchApplicationNotification,
            NSWorkspace.activeSpaceDidChangeNotification
        ]
        workspaceTokens = names.map { name in
            workspaceCenter.addObserver(forName: name, object: nil, queue: .main) {
                [weak self] _ in
                Task { await self?.refreshState() }
            }
        }
        screenToken = NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { await self?.refreshState() }
        }
    }

    private func removeLifecycleObservers() {
        let workspaceCenter = NSWorkspace.shared.notificationCenter
        workspaceTokens.forEach(workspaceCenter.removeObserver)
        workspaceTokens.removeAll()
        if let screenToken {
            NotificationCenter.default.removeObserver(screenToken)
            self.screenToken = nil
        }
    }
}
