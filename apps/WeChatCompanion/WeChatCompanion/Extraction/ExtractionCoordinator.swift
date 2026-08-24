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
    /// Genuine provider, network, or parse failures only. A cancelled
    /// extraction is never counted here.
    var extractionsFailed = 0
    /// Extractions abandoned because the work was cancelled, normally by
    /// pausing. Expected lifecycle, not an error.
    var extractionsCancelled = 0
    var framesDroppedWhileBusy = 0
    var framesWithheldPendingConsent = 0
    /// Completion time of the last real attempt -- a success or a genuine
    /// failure. Cancellations deliberately do not move it, so it never means
    /// several different things at once.
    var lastExtractionAt: Date?
    /// Time of the last cancelled extraction, tracked separately.
    var lastCancellationAt: Date?
    /// Aggregate-only diagnosis of the most recent genuine failure. Provider
    /// independent, and structurally incapable of holding response content.
    var lastFailure: ExtractionFailureDiagnostics?
}

/// One consistent read of everything the extraction UI needs.
struct ExtractionState: Sendable {
    let metrics: ExtractionMetrics
    /// Memory only. Never persisted, never written to disk.
    let latest: ExtractedConversationFrame?
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
    /// Identifies the drain task that owns `extractionTask`, so a late
    /// unwinding task cannot clear a newer one's reference.
    private var extractionGeneration = 0
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
        // Cancel but deliberately KEEP the reference. The task may still be
        // unwinding inside the provider; clearing it here would let a resumed
        // session start a second extraction alongside it. The task clears its
        // own reference once it actually exits.
        extractionTask?.cancel()
        pendingFrame = nil
    }

    func snapshot() -> ExtractionMetrics {
        var current = metrics
        current.status = status
        return current
    }

    /// In-memory only; exposed for the future message store, never persisted here.
    func latest() -> ExtractedConversationFrame? { latestExtraction }

    /// Metrics and latest result read in one actor hop, so the UI can never
    /// show counters from one instant beside a result from another.
    /// Read-only: scheduling, cancellation and backpressure are untouched.
    func state() -> ExtractionState {
        ExtractionState(metrics: snapshot(), latest: latestExtraction)
    }

    var hasPendingFrame: Bool { pendingFrame != nil }

    /// True while a drain task exists, including one that has been cancelled
    /// but has not finished unwinding yet.
    var hasActiveExtractionTask: Bool { extractionTask != nil }

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
        startDrain(with: frame)
    }

    /// Claims the single extraction slot for `frame` under a fresh generation.
    private func startDrain(with frame: ObservedFrame) {
        extractionGeneration += 1
        let generation = extractionGeneration
        extractionTask = Task { [weak self] in
            await self?.drain(startingWith: frame, generation: generation)
        }
    }

    /// Releases the slot and immediately promotes any frame that arrived while
    /// this drain was running.
    ///
    /// A cancelled drain deliberately refuses to process the pending frame
    /// itself -- that work belongs to the resumed session -- but the frame must
    /// still be driven, and only once this drain's provider call has actually
    /// exited. Without promotion here it would sit orphaned until some later
    /// frame happened to arrive, and could then be processed *after* a newer
    /// one, breaking newest-wins ordering.
    private func finishDrain(generation: Int) {
        // Generation ownership: an old drain must never disturb a newer task.
        guard generation == extractionGeneration else { return }
        extractionTask = nil

        guard !isPaused, let promoted = pendingFrame else { return }
        pendingFrame = nil
        startDrain(with: promoted)
    }

    private func drain(startingWith first: ObservedFrame, generation: Int) async {
        var next: ObservedFrame? = first
        while let frame = next {
            if Task.isCancelled { break }
            await runExtraction(frame)
            // Do not consume a pending frame after cancellation: it belongs to
            // whatever session comes next, not to this unwinding one.
            if Task.isCancelled { break }
            next = pendingFrame
            pendingFrame = nil
        }
        finishDrain(generation: generation)
    }

    private func runExtraction(_ frame: ObservedFrame) async {
        metrics.extractionsStarted += 1
        do {
            let extracted = try await extractor.extract(from: frame)
            metrics.extractionsSucceeded += 1
            latestExtraction = extracted
            metrics.lastExtractionAt = Date()
        } catch is CancellationError {
            recordCancellation()
        } catch let error as URLError where error.code == .cancelled {
            // Cancelling a task can surface through URLSession rather than as
            // a CancellationError. Only this one code counts as cancellation:
            // other network errors remain genuine failures.
            recordCancellation()
        } catch {
            // Aggregate count only. The provider error object is never stored;
            // an error may however describe itself in content-free terms.
            metrics.extractionsFailed += 1
            metrics.lastFailure = Self.diagnostics(for: error)
            metrics.lastExtractionAt = Date()
        }
    }

    /// Classifies without knowing anything about a specific provider: an error
    /// either describes itself through the shared seam, or it is `.other`.
    private static func diagnostics(for error: any Error) -> ExtractionFailureDiagnostics {
        var diagnostics = (error as? any ExtractionFailureDescribing)?.failureDiagnostics
            ?? ExtractionFailureDiagnostics(category: .other)
        diagnostics.occurredAt = Date()
        return diagnostics
    }

    /// A cancelled extraction produced no result, so it never replaces
    /// `latestExtraction`, never moves `lastExtractionAt`, and never records a
    /// failure diagnosis.
    private func recordCancellation() {
        metrics.extractionsCancelled += 1
        metrics.lastCancellationAt = Date()
    }

    /// Test seam: awaits until no extraction is running and nothing is left
    /// pending. Promotion happens before a drain's task completes, so the loop
    /// picks up the promoted task on its next pass.
    func waitUntilIdle() async {
        while let task = extractionTask {
            await task.value
        }
    }
}
