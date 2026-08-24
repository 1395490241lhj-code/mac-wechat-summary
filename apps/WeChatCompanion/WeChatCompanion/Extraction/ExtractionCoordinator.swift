import Foundation

enum ExtractionStatus: String, Codable, Sendable, Equatable {
    case notConfigured
    case ready
    case processing
    case paused

    var label: String {
        switch self {
        case .notConfigured: "Not Configured"
        case .ready: "Ready"
        case .processing: "Processing"
        case .paused: "Paused"
        }
    }
}

/// Aggregate-only extraction telemetry. There is deliberately no field that
/// could hold a provider error message, a chat title, or message text.
struct ExtractionMetrics: Codable, Equatable, Sendable {
    var status: ExtractionStatus = .notConfigured
    var framesReceived = 0
    var extractionsStarted = 0
    var extractionsSucceeded = 0
    var extractionsFailed = 0
    var framesDroppedWhileBusy = 0
    var framesWithheldPendingConsent = 0
    var lastExtractionAt: Date?
}

/// Consumes meaningful frames and runs at most one extraction at a time.
///
/// Backpressure is newest-wins with a single pending slot: while an extraction
/// is in flight the coordinator holds at most one waiting frame, and a newer
/// frame replaces (and releases) the older one. There is no queue, so CGImages
/// cannot accumulate no matter how slow the extractor is.
actor ExtractionCoordinator {
    private var extractor: any FrameExtracting
    private var capability: ExtractionCapability

    private var metrics = ExtractionMetrics()
    private var pendingFrame: ObservedFrame?
    private var extractionTask: Task<Void, Never>?
    private var consumeTask: Task<Void, Never>?
    private var isPaused = false
    /// Latest result, in memory only. Never written to disk in this phase.
    private var latestExtraction: ExtractedConversationFrame?

    init(
        extractor: any FrameExtracting = NoConfiguredExtractor(),
        capability: ExtractionCapability = .onDeviceOnly
    ) {
        self.extractor = extractor
        self.capability = capability
    }

    /// Applies a settings change (credential added or removed, consent toggled)
    /// without discarding accumulated metrics.
    func updateConfiguration(
        extractor: any FrameExtracting,
        capability: ExtractionCapability
    ) {
        self.extractor = extractor
        self.capability = capability
    }

    func start(frames: AsyncStream<ObservedFrame>) {
        isPaused = false
        consumeTask?.cancel()
        consumeTask = Task { [weak self] in
            for await frame in frames {
                if Task.isCancelled { return }
                await self?.submit(frame)
            }
        }
    }

    /// Stops consuming, cancels any in-flight extraction and releases the
    /// pending frame, so a paused coordinator holds no imagery.
    func pause() {
        isPaused = true
        consumeTask?.cancel()
        consumeTask = nil
        extractionTask?.cancel()
        extractionTask = nil
        pendingFrame = nil
    }

    func snapshot() -> ExtractionMetrics {
        var current = metrics
        current.status = status
        return current
    }

    /// In-memory only; exposed for the future message store, never persisted here.
    func latest() -> ExtractedConversationFrame? { latestExtraction }

    var hasPendingFrame: Bool { pendingFrame != nil }

    private var status: ExtractionStatus {
        if !extractor.isConfigured { return .notConfigured }
        if isPaused { return .paused }
        return extractionTask == nil ? .ready : .processing
    }

    func submit(_ frame: ObservedFrame) {
        metrics.framesReceived += 1
        guard !isPaused else { return }

        // An unconfigured extractor is never invoked, so it cannot throw once
        // per sample. The frame is simply released.
        guard extractor.isConfigured else { return }

        // Raw frames may only leave the Mac with explicit user consent.
        guard capability.allowsProcessing(at: extractor.processingLocation) else {
            metrics.framesWithheldPendingConsent += 1
            return
        }

        guard extractionTask == nil else {
            // Newest wins: the previously pending frame is dropped and released.
            if pendingFrame != nil { metrics.framesDroppedWhileBusy += 1 }
            pendingFrame = frame
            return
        }
        extractionTask = Task { [weak self] in
            await self?.drain(startingWith: frame)
        }
    }

    private func drain(startingWith first: ObservedFrame) async {
        var next: ObservedFrame? = first
        while let frame = next {
            if Task.isCancelled { break }
            await runExtraction(frame)
            next = pendingFrame
            pendingFrame = nil
        }
        extractionTask = nil
    }

    private func runExtraction(_ frame: ObservedFrame) async {
        metrics.extractionsStarted += 1
        do {
            let extracted = try await extractor.extract(from: frame)
            metrics.extractionsSucceeded += 1
            latestExtraction = extracted
        } catch {
            // Aggregate count only. The provider error is deliberately not
            // stored, logged, or surfaced, so no content can leak through it.
            metrics.extractionsFailed += 1
        }
        metrics.lastExtractionAt = Date()
    }

    /// Test seam: awaits any in-flight extraction so assertions are deterministic.
    func waitUntilIdle() async {
        while let task = extractionTask {
            await task.value
        }
    }
}
