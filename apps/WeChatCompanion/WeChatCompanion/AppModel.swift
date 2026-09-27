import Foundation
import Observation

@MainActor
@Observable
final class AppModel {
    var selectedDestination: Destination? = .overview
    var systemStatus = SystemStatus.unknown
    var lastDiagnostic: DiagnosticResult?
    var isRunningDiagnostics = false
    var persistenceFailed = false
    var captureMetrics = WindowCaptureMetrics()
    /// Development preview of the latest accepted meaningful frame.
    /// Memory only, never encoded or saved.
    var capturePreview: ObservedFrame?
    var extractionMetrics = ExtractionMetrics()
    /// Which conversations have been captured and how much is still kept.
    /// Aggregates only -- this never carries message text.
    var captureLedger = CaptureLedger.empty
    /// In-memory only. Cleared when the app exits; never written to disk.
    var latestExtraction: ExtractedConversationFrame?
    /// Transient text-field buffer, cleared as soon as the key reaches the Keychain.
    var apiKeyInput = ""
    /// Drives the destructive-deletion confirmation dialog.
    var isConfirmingHistoryDeletion = false
    private(set) var hasProviderCredential = false
    private(set) var allowsRemoteProcessing = false
    /// Independent of `allowsRemoteProcessing`: this one decides whether
    /// extracted text is written to the local database. Off by default.
    private(set) var allowsLocalPersistence = false
    private(set) var retentionPolicy = RetentionPolicy.defaultPolicy
    private(set) var selectedGeminiModel = GeminiModel.provisionalDefault
    private(set) var credentialErrorOccurred = false
    private(set) var archiveImportStatus = ArchiveImportStatus.idle
#if DEBUG
    private(set) var visualQualityGateMode = VisualQualityGateMode.normal
    var visualQualityGateIsActive: Bool { visualQualityGateMode != .normal }
    var visualQualityGatePhase = VisualQualityGatePhase.inactive
    var visualQualityGateMetrics = VisualQualityMetrics()
    var visualQualityGateReview: VisualQualityFrameReview?
    var visualQualityGateModelID: String?
    @ObservationIgnored private var visualQualityGateTask: Task<Void, Never>?
    @ObservationIgnored private var visualQualityReconciler = VisualQualityReconciliationTracker()
    @ObservationIgnored private var visualQualityGateModel: GeminiModel?
#endif

    @ObservationIgnored private let service: DiagnosticsService
    @ObservationIgnored private let store: DiagnosticsStore
    @ObservationIgnored private let session: SystemWindowCaptureSession
    @ObservationIgnored private let observerStore: ObserverMetricsStore
    @ObservationIgnored private let extractionCoordinator: ExtractionCoordinator
    @ObservationIgnored private let messageHistory: LocalMessageHistory
    @ObservationIgnored private let credentials: any CredentialStoring
    /// Held so rebuilding the extractor cannot silently fall back to the
    /// production transport. Without this seam a test's transport is detached
    /// the first time a setting changes, and any assertion about requests
    /// afterwards becomes unfalsifiable.
    @ObservationIgnored private let geminiTransport: any GeminiTransporting
    @ObservationIgnored private let consentDefaults: UserDefaults
    @ObservationIgnored private let memorySync: any MemorySyncRunning
    @ObservationIgnored private var observerPollingTask: Task<Void, Never>?
    /// Independent of capture polling: an extraction already in flight can
    /// still finish after capture is paused, and Chats must show that result.
    @ObservationIgnored private var extractionPollingTask: Task<Void, Never>?
    @ObservationIgnored private var didBootstrap = false

    init(
        service: DiagnosticsService = DiagnosticsService(),
        store: DiagnosticsStore = .applicationSupport,
        session: SystemWindowCaptureSession = SystemWindowCaptureSession(),
        observerStore: ObserverMetricsStore = .applicationSupport,
        extractionCoordinator: ExtractionCoordinator = ExtractionCoordinator(),
        messageHistory: LocalMessageHistory = .applicationSupport,
        credentials: any CredentialStoring = KeychainCredentialStore(),
        geminiTransport: any GeminiTransporting = GeminiFrameExtractor.productionTransport,
        consentDefaults: UserDefaults = .standard,
        memorySync: any MemorySyncRunning = AppModel.defaultMemorySyncRunner()
    ) {
        self.service = service
        self.store = store
        self.session = session
        self.observerStore = observerStore
        self.extractionCoordinator = extractionCoordinator
        self.messageHistory = messageHistory
        self.credentials = credentials
        self.geminiTransport = geminiTransport
        self.consentDefaults = consentDefaults
        self.memorySync = memorySync
        hasProviderCredential = credentials.hasSecret(
            account: GeminiFrameExtractor.credentialAccount
        )
        allowsRemoteProcessing = consentDefaults.bool(forKey: Self.remoteConsentKey)
        // Absent key reads as false, so a fresh install never persists.
        allowsLocalPersistence = consentDefaults.bool(forKey: Self.localPersistenceConsentKey)
        retentionPolicy = RetentionPolicy.resolved(
            fromStoredID: consentDefaults.string(forKey: Self.retentionPolicyKey)
        )
        selectedGeminiModel = GeminiModel.resolved(
            fromStoredID: consentDefaults.string(forKey: Self.geminiModelKey)
        )
        // The app is the authority on this consent, and it asserts that by
        // always leaving a current, well-formed state behind: a fresh install
        // records an explicit "no" rather than nothing, and a state written by
        // an older build is brought up to date from the flag it mirrors.
        LocalPersistenceConsentState.record(
            allowsLocalMessageStorage: allowsLocalPersistence, in: consentDefaults
        )
    }

    /// Only a boolean consent flag is stored here. The API key lives in the
    /// Keychain and never touches UserDefaults.
    static let remoteConsentKey = "extraction.allowsRemoteProcessing"
    /// Non-secret configuration: only the stable model ID is persisted.
    static let geminiModelKey = "extraction.geminiModel"
    /// Separate from `remoteConsentKey` on purpose. Remote consent governs
    /// whether a frame may leave the Mac; this one governs whether extracted
    /// text is written down here.
    static let localPersistenceConsentKey = "persistence.allowsLocalMessageStorage"
    /// Only the policy ID is stored. Never a date, a chat, or a message.
    static let retentionPolicyKey = "persistence.retentionPolicy"

    // MARK: - Extraction settings

    func saveProviderAPIKey() async {
#if DEBUG
        if visualQualityGateIsActive { await finishVisualQualityGate() }
#endif
        let key = apiKeyInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !key.isEmpty else { return }
        credentialErrorOccurred = false
        do {
            try credentials.save(key, account: GeminiFrameExtractor.credentialAccount)
            hasProviderCredential = true
        } catch {
            // Never surface or store the underlying error: it can reference the item.
            credentialErrorOccurred = true
        }
        apiKeyInput = ""
        await applyExtractionConfiguration()
    }

    func removeProviderAPIKey() async {
#if DEBUG
        if visualQualityGateIsActive { await finishVisualQualityGate() }
#endif
        credentialErrorOccurred = false
        do {
            try credentials.remove(account: GeminiFrameExtractor.credentialAccount)
            hasProviderCredential = false
        } catch {
            credentialErrorOccurred = true
        }
        apiKeyInput = ""
        await applyExtractionConfiguration()
    }

    /// Saving a key never enables this. Remote processing requires both a
    /// configured provider and this explicit user opt-in.
    func setAllowsRemoteProcessing(_ isAllowed: Bool) async {
        allowsRemoteProcessing = isAllowed
        consentDefaults.set(isAllowed, forKey: Self.remoteConsentKey)
#if DEBUG
        if !isAllowed, visualQualityGateIsActive {
            await finishVisualQualityGate()
            return
        }
#endif
        await applyExtractionConfiguration()
    }

    /// Changing the model persists the ID and rebuilds the extractor. It makes
    /// no network request and uploads no frame by itself; the credential,
    /// consent value and accumulated metrics are all preserved.
    func setGeminiModel(_ model: GeminiModel) async {
        guard model != selectedGeminiModel else { return }
#if DEBUG
        if visualQualityGateIsActive { await finishVisualQualityGate() }
#endif
        selectedGeminiModel = model
        consentDefaults.set(model.modelID, forKey: Self.geminiModelKey)
        await applyExtractionConfiguration()
    }

    /// True while an extraction is running. The picker is disabled then, so a
    /// model change cannot race an in-flight request.
    var isExtractionProcessing: Bool { extractionMetrics.status == .processing }

    // MARK: - Memory sync (M2.2c, packaged in M2.2d)

    /// The packaged worker when this build has one; otherwise the honest
    /// runner that reports the packaging gap rather than faking a sync.
    ///
    /// A test host is an app bundle too, and since M2.2d it carries a real
    /// worker pointed at the real store. Without this guard a test that built
    /// an `AppModel` with default arguments would sync the user's own
    /// messages into their own memory database as a side effect of running
    /// the suite -- which it did, once, before the guard existed. Tests inject
    /// the runner they mean to exercise; only a shipped app takes the
    /// packaged one.
    static func defaultMemorySyncRunner() -> any MemorySyncRunning {
        // `bundled()` refuses under a test host; this stays as the second of
        // two independent stops rather than trusting either alone.
        guard !PackagedMemorySyncRunner.isUnderTestHost else { return UnavailableMemorySyncRunner() }
        return PackagedMemorySyncRunner.bundled() ?? UnavailableMemorySyncRunner()
    }


    /// The only source this app can offer: the store it fills itself. A
    /// database reader is an operator-side selection and is never substituted.
    let memorySource: MemorySource = .visual
    private(set) var memorySyncPhase: MemorySyncPhase = .idle
    /// The last freshness the runner reported. Kept across a consent
    /// withdrawal -- withdrawing is not a delete request -- but nothing new is
    /// read or written while consent is off.
    private(set) var memoryFreshness: MemoryFreshnessSummary?

    /// Memory persistence and sync exist only under the local-storage consent.
    var isMemoryAvailable: Bool { allowsLocalPersistence }
    var canSyncMemory: Bool { isMemoryAvailable && !memorySyncPhase.isRunning }

    /// Explicit, foreground, user-initiated. The consent gate is checked here
    /// first, so the runner is never reached without it; the runner is then
    /// asked for the configured source and nothing else.
    func syncMemoryNow() async {
        guard allowsLocalPersistence else {
            memorySyncPhase = .failed(.consentWithheld)
            return
        }
        guard !memorySyncPhase.isRunning else { return }
        memorySyncPhase = .running
        switch await memorySync.sync(source: memorySource) {
        case .succeeded(let counts, let freshness):
            memorySyncPhase = .succeeded(counts)
            if let freshness {
                memoryFreshness = freshness
            } else {
                memoryFreshness = await memorySync.freshness(source: memorySource)
            }
        case .failed(let failure):
            memorySyncPhase = .failed(failure)
        }
    }

    func refreshMemoryFreshness() async {
        guard allowsLocalPersistence else { return }
        if let freshness = await memorySync.freshness(source: memorySource) {
            memoryFreshness = freshness
        }
    }

    // MARK: - Local persistence settings

    /// Turning this on opens (and if needed creates) the local database.
    /// Turning it off detaches the ingestor so no further message is written,
    /// and deliberately KEEPS what is already stored -- withdrawing consent for
    /// future writes is not a request to delete. Use `deleteLocalMessageHistory`
    /// for that.
    func setAllowsLocalPersistence(_ isAllowed: Bool) async {
        allowsLocalPersistence = isAllowed
        consentDefaults.set(isAllowed, forKey: Self.localPersistenceConsentKey)
        LocalPersistenceConsentState.record(allowsLocalMessageStorage: isAllowed, in: consentDefaults)
        if !isAllowed {
            // Takes effect immediately: no sync can start, and a result from
            // before the withdrawal is not shown as if it were current state.
            memorySyncPhase = .idle
        }
        await messageHistory.setEnabled(isAllowed)
        await applyExtractionConfiguration()
        await refreshCaptureLedger()
    }

    /// Applies immediately, including a sweep of anything the new policy has
    /// already expired. Only meaningful while persistence is on.
    func setRetentionPolicy(_ policy: RetentionPolicy) async {
        guard policy != retentionPolicy else { return }
        retentionPolicy = policy
        consentDefaults.set(policy.rawValue, forKey: Self.retentionPolicyKey)
        await messageHistory.setRetention(policy)
        // The sweep may have just removed messages the ledger is counting.
        await refreshCaptureLedger()
    }

    func importWeChatArchive(from url: URL) async {
        archiveImportStatus = .importing
        do {
            let outcome = try await WeChatArchiveImportService(history: messageHistory)
                .importArchive(contentsOf: url)
            switch outcome.persistence {
            case .inserted:
                archiveImportStatus = .imported(
                    recordCount: outcome.recordCount,
                    transcriptShape: outcome.transcriptShape
                )
            case .alreadyImported:
                archiveImportStatus = .alreadyImported
            }
            await refreshCaptureLedger()
        } catch ArchivePersistenceError.localPersistenceConsentRequired {
            archiveImportStatus = .localPersistenceConsentRequired
        } catch ArchivePersistenceError.localStoreUnavailable {
            archiveImportStatus = .localStoreUnavailable
        } catch WeChatArchiveImportError.conversationIdentityUnavailable {
            archiveImportStatus = .conversationIdentityUnavailable
        } catch {
            archiveImportStatus = .invalidArchive
        }
    }

    /// Destructive: removes every locally stored conversation and message,
    /// including the database's write-ahead sidecar files.
    ///
    /// Scope is exactly that. The API key, the remote-processing consent, the
    /// persistence consent, the retention choice and the diagnostics records
    /// are all left alone.
    func deleteLocalMessageHistory() async {
        await messageHistory.deleteAllHistory()
        await applyExtractionConfiguration()
        await refreshCaptureLedger()
    }

    private func applyExtractionConfiguration() async {
        await extractionCoordinator.updateConfiguration(
            extractor: GeminiFrameExtractor(
                credentials: credentials,
                transport: geminiTransport,
                model: selectedGeminiModel
            ),
            capability: ExtractionCapability(userEnabledRemoteProvider: allowsRemoteProcessing),
            // Nil unless the user consented, so the default build path writes
            // nothing to disk.
            ingestor: await messageHistory.ingestor()
        )
        await refreshExtractionState()
    }

    /// Single source of truth for extraction UI freshness. One actor hop keeps
    /// the counters and the latest result consistent with each other.
    func refreshExtractionState() async {
        let state = await extractionCoordinator.state()
        extractionMetrics = state.metrics
        latestExtraction = state.latest
    }

    var lastDiagnosticStatus: DiagnosticStatus {
        lastDiagnostic?.status ?? .neverRun
    }

    /// True while a metrics polling loop is running. Polling only runs while the
    /// observer is active, so a paused observer costs nothing.
    var isPollingObserverMetrics: Bool { observerPollingTask != nil }

    var isPollingExtractionState: Bool { extractionPollingTask != nil }

    var needsWindowSelection: Bool {
        captureMetrics.state == .needsWindowSelection || captureMetrics.state == .selectionLost
    }

    func bootstrap(autoRunDiagnostics: Bool, runObserverValidation: Bool) async {
        guard !didBootstrap else { return }
        didBootstrap = true
        lastDiagnostic = try? store.load()
        await refreshSystemStatus()
        // Restores the stored consent and retention choice. With consent off
        // this opens nothing, so no database file is created at launch.
        await messageHistory.setRetention(retentionPolicy)
        await messageHistory.setEnabled(allowsLocalPersistence)
        await applyExtractionConfiguration()
        await extractionCoordinator.start(frames: await session.meaningfulFrames())
        await refreshCaptureMetrics()
        startExtractionPolling()
        if autoRunDiagnostics {
            await runDiagnostics(requestPermissionIfNeeded: false)
        }
        if runObserverValidation {
            try? await Task.sleep(for: .seconds(5))
            captureMetrics = await session.snapshot()
            try? observerStore.save(captureMetrics)
        }
    }

    func refreshSystemStatus() async {
        systemStatus = await service.currentSystemStatus()
    }

    func runDiagnostics(requestPermissionIfNeeded: Bool = true) async {
        guard !isRunningDiagnostics else { return }
        isRunningDiagnostics = true
        persistenceFailed = false

        let result = await service.run(
            requestPermissionIfNeeded: requestPermissionIfNeeded
        )
        do {
            try store.save(result)
        } catch {
            persistenceFailed = true
        }
        lastDiagnostic = result
        await refreshSystemStatus()
        isRunningDiagnostics = false
    }

    /// Presents the system window picker. The user chooses the WeChat window
    /// themselves; we never activate or control WeChat to do it.
    func selectWeChatWindow() async {
#if DEBUG
        guard !visualQualityGateIsActive else { return }
#endif
        await session.selectWindow()
        await refreshCaptureMetrics()
        await refreshExtractionState()
        startMetricsPolling()
        startExtractionPolling()
    }

    func resumeObserving() async {
#if DEBUG
        if visualQualityGateIsActive { await finishVisualQualityGate(resumeCapture: false) }
#endif
        await session.resume()
        await refreshCaptureMetrics()
        await refreshExtractionState()
        startMetricsPolling()
        startExtractionPolling()
    }

    func pauseObserving() async {
#if DEBUG
        if visualQualityGateIsActive {
            await finishVisualQualityGate(resumeCapture: false)
            return
        }
#endif
        await session.pause()
        await refreshCaptureMetrics()
        // Capture polling stops, but extraction polling deliberately does not:
        // an in-flight extraction may still complete and must become visible.
        await refreshExtractionState()
        stopMetricsPolling()
    }

    func stopObserving() async {
#if DEBUG
        if visualQualityGateIsActive { await finishVisualQualityGate(resumeCapture: false) }
#endif
        await session.stopObserving()
        await refreshCaptureMetrics()
        await refreshExtractionState()
        capturePreview = nil
        stopMetricsPolling()
    }

    func clearCapturePreview() async {
        await session.clearPreview()
        capturePreview = nil
    }

#if DEBUG
    func beginVisualQualityGate() async {
        guard visualQualityGateMode == .normal,
              allowsRemoteProcessing,
              hasProviderCredential else { return }

        visualQualityGateMode = .preparingEvaluation
        extractionCoordinator.closeForEvaluationImmediately()
        await extractionCoordinator.beginEvaluationIsolation()
        await extractionCoordinator.waitUntilIdle()
        await extractionCoordinator.clearLatest()
        await session.pause()
        await session.clearPreview()
        capturePreview = nil
        latestExtraction = nil
        stopMetricsPolling()
        stopExtractionPolling()

        visualQualityGateMetrics = VisualQualityMetrics()
        visualQualityGateModel = selectedGeminiModel
        visualQualityGateModelID = selectedGeminiModel.modelID
        visualQualityReconciler.reset()
        visualQualityGatePhase = .selectingWindow
    }

    /// Installs the evaluation-only consumer before opening the system picker.
    /// No ScreenCaptureKit stream starts until the user selects a window.
    func selectWindowForVisualQualityGate() async {
        guard visualQualityGateMode == .preparingEvaluation else { return }
        await armVisualQualityFrameConsumer()
        let finish: @Sendable () -> Void = { [weak self] in
            Task { @MainActor in await self?.finishVisualQualityGate() }
        }
        await session.selectWindow(onCancel: finish, onFailure: finish)
    }

    /// Arms the in-memory evaluation stream without opening a picker or
    /// starting capture. Kept internal so synthetic tests can inject frames.
    func armVisualQualityFrameConsumer() async {
        guard visualQualityGateMode == .preparingEvaluation,
              visualQualityGateTask == nil else { return }
        let frames = await session.meaningfulFrames()
        visualQualityGateTask = Task { [weak self] in
            await self?.extractOneVisualQualityFrame(from: frames)
        }
    }

    func captureNextVisualQualityFrame() async {
        guard visualQualityGateMode == .evaluating,
              visualQualityGateTask == nil,
              visualQualityGatePhase == .readyForNext || visualQualityGatePhase == .failed else {
            return
        }
        visualQualityGatePhase = .capturing
        await scheduleVisualQualityFrameCapture()
    }

    func setVisualQualityTitleRating(_ rating: VisualQualityTitleRating?) {
        visualQualityGateReview?.titleRating = rating
    }

    func setVisualQualityMessageRating(
        at index: Int,
        _ update: (inout VisualQualityMessageAssessment) -> Void
    ) {
        guard var review = visualQualityGateReview,
              review.messageAssessments.indices.contains(index) else { return }
        update(&review.messageAssessments[index])
        visualQualityGateReview = review
    }

    func addMissingVisibleMessage() {
        guard visualQualityGateReview != nil else { return }
        visualQualityGateReview?.missingVisibleMessages += 1
    }

    func removeMissingVisibleMessage() {
        guard let count = visualQualityGateReview?.missingVisibleMessages, count > 0 else { return }
        visualQualityGateReview?.missingVisibleMessages = count - 1
    }

    func recordVisualQualityReview() async {
        guard let review = visualQualityGateReview, review.isComplete else { return }
        visualQualityGateMetrics.recordReviewedFrame(
            title: review.titleRating!,
            messages: review.messageAssessments,
            missingVisibleMessages: review.missingVisibleMessages,
            reconciliation: review.reconciliation
        )
        visualQualityGateReview = nil
        await session.clearPreview()
        capturePreview = nil
        visualQualityGatePhase = .readyForNext
    }

    func finishVisualQualityGate(resumeCapture: Bool = true) async {
        guard visualQualityGateMode != .normal else { return }
        visualQualityGateTask?.cancel()
        if let task = visualQualityGateTask { await task.value }
        visualQualityGateTask = nil
        await session.pause()
        await session.clearPreview()
        capturePreview = nil
        visualQualityGateReview = nil
        visualQualityReconciler.reset()
        visualQualityGateModel = nil
        visualQualityGatePhase = .finished

        await applyExtractionConfiguration()
        await extractionCoordinator.clearLatest()
        latestExtraction = nil
        let freshFrames = await session.meaningfulFrames()
        await extractionCoordinator.endEvaluationIsolation(frames: freshFrames)
        visualQualityGateMode = .normal
        if resumeCapture {
            await session.resume()
            startMetricsPolling()
        }
        startExtractionPolling()
        await refreshCaptureMetrics()
        await refreshExtractionState()
    }

    private func scheduleVisualQualityFrameCapture() async {
        guard visualQualityGateTask == nil else { return }
        let frames = await session.meaningfulFrames()
        visualQualityGateTask = Task { [weak self] in
            await self?.extractOneVisualQualityFrame(from: frames)
        }
        await session.resume()
    }

    private func extractOneVisualQualityFrame(from frames: AsyncStream<ObservedFrame>) async {
        for await frame in frames {
            guard !Task.isCancelled, visualQualityGateMode != .normal else { break }
            visualQualityGateMode = .evaluating
            visualQualityGatePhase = .capturing
            await session.pause()
            await session.clearPreview()
            capturePreview = nil

            let extractor = GeminiFrameExtractor(
                credentials: credentials,
                transport: geminiTransport,
                model: visualQualityGateModel ?? selectedGeminiModel
            )
            guard ExtractionCapability(userEnabledRemoteProvider: allowsRemoteProcessing)
                .allowsProcessing(at: extractor.processingLocation) else {
                visualQualityGatePhase = .failed
                visualQualityGateTask = nil
                return
            }
            visualQualityGateMetrics.recordExtractionAttempt()
            guard extractor.isConfigured else {
                visualQualityGateMetrics.recordExtractionFailure(.missingCredential)
                visualQualityGatePhase = .failed
                visualQualityGateTask = nil
                return
            }

            do {
                let result = try await extractor.extract(from: frame)
                guard !Task.isCancelled, visualQualityGateIsActive else { return }
                let reconciliation = visualQualityReconciler.reconcile(result)
                visualQualityGateReview = VisualQualityFrameReview(
                    frame: frame,
                    extraction: result,
                    reconciliation: reconciliation
                )
                visualQualityGatePhase = .reviewing
            } catch is CancellationError {
                visualQualityGateMetrics.cancelledFrames += 1
            } catch let error as URLError where error.code == .cancelled {
                visualQualityGateMetrics.cancelledFrames += 1
            } catch {
                let category = (error as? any ExtractionFailureDescribing)?
                    .failureDiagnostics.category ?? .other
                visualQualityGateMetrics.recordExtractionFailure(category)
                visualQualityGatePhase = .failed
            }
            visualQualityGateTask = nil
            return
        }
        visualQualityGateTask = nil
    }
#endif

    /// The ledger also has to follow user actions that change what is kept --
    /// consent, retention and deletion -- because capture polling may not be
    /// running when any of them happens.
    func refreshCaptureLedger() async {
        captureLedger = await messageHistory.captureLedger()
    }

    private func refreshCaptureMetrics() async {
        captureMetrics = await session.snapshot()
        capturePreview = await session.latestPreview()
        await refreshCaptureLedger()
    }

    private func startMetricsPolling() {
        guard ObserverPollingPolicy.shouldStartPolling(
            isPolling: observerPollingTask != nil
        ) else { return }
        observerPollingTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.refreshCaptureMetrics()
                try? await Task.sleep(for: .milliseconds(500))
            }
        }
    }

    private func stopMetricsPolling() {
        observerPollingTask?.cancel()
        observerPollingTask = nil
    }

    /// Snapshot-only loop at ~2Hz: no image work, no network, no persistence.
    /// Started once and never stacked, so repeated calls are harmless.
    func startExtractionPolling() {
        guard ObserverPollingPolicy.shouldStartPolling(
            isPolling: extractionPollingTask != nil
        ) else { return }
        extractionPollingTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.refreshExtractionState()
                try? await Task.sleep(for: .milliseconds(500))
            }
        }
    }

    func stopExtractionPolling() {
        extractionPollingTask?.cancel()
        extractionPollingTask = nil
    }

}

enum ArchiveImportStatus: Equatable {
    case idle
    case importing
    case imported(recordCount: Int, transcriptShape: String)
    case alreadyImported
    case localPersistenceConsentRequired
    case localStoreUnavailable
    case conversationIdentityUnavailable
    case invalidArchive

    var message: String {
        switch self {
        case .idle:
            "Choose a WeChat merged-forward ZIP to add it to local history."
        case .importing:
            "Importing archive…"
        case .imported(let recordCount, let transcriptShape):
            "Imported \(recordCount) \(transcriptShape) records."
        case .alreadyImported:
            "This archive is already in local history."
        case .localPersistenceConsentRequired:
            "Local message storage is off. Enable it in Settings before importing."
        case .localStoreUnavailable:
            "Local history is unavailable. The archive was not imported."
        case .conversationIdentityUnavailable:
            "The archive is valid, but its conversation identity could not be established."
        case .invalidArchive:
            "The file could not be imported as a supported WeChat archive."
        }
    }
}

enum Destination: String, CaseIterable, Identifiable {
    case overview = "Overview"
    case chats = "Chats"
    case dailySummary = "Daily Summary"
    case reminders = "Reminders"
    case agents = "Agents"
    case diagnostics = "Diagnostics"
    case settings = "Settings"

    var id: Self { self }

    var systemImage: String {
        switch self {
        case .overview: "rectangle.grid.2x2"
        case .chats: "bubble.left.and.bubble.right"
        case .dailySummary: "text.document"
        case .reminders: "checklist"
        case .agents: "person.2"
        case .diagnostics: "stethoscope"
        case .settings: "gearshape"
        }
    }
}

#if DEBUG
enum VisualQualityGateMode: Equatable {
    case normal
    case preparingEvaluation
    case evaluating
}

enum VisualQualityGatePhase: Equatable {
    case inactive
    case selectingWindow
    case capturing
    case reviewing
    case failed
    case readyForNext
    case finished
}

struct VisualQualityRate: Equatable, Sendable {
    let numerator: Int
    let denominator: Int
}

enum VisualQualityTitleRating: String, CaseIterable, Identifiable, Hashable {
    case correct
    case wrong
    case unknown
    case hallucinated

    var id: Self { self }
    var label: String { rawValue.capitalized }
}

enum VisualQualityDetectionRating: String, CaseIterable, Identifiable, Hashable {
    case correct
    case missing
    case hallucinated
    case duplicate

    var id: Self { self }
    var label: String { rawValue.capitalized }
}

enum VisualQualitySenderRating: String, CaseIterable, Identifiable, Hashable {
    case correct
    case wrong
    case unknown
    case hallucinated

    var id: Self { self }
    var label: String { rawValue.capitalized }
}

enum VisualQualityTextRating: String, CaseIterable, Identifiable, Hashable {
    case exact
    case minorError
    case wrong
    case missing
    case hallucinated
    case visuallyUnreadable

    var id: Self { self }
    var label: String {
        switch self {
        case .exact: "Exact"
        case .minorError: "Minor Error"
        case .wrong: "Wrong"
        case .missing: "Missing"
        case .hallucinated: "Hallucinated"
        case .visuallyUnreadable: "Visually Unreadable"
        }
    }
}

enum VisualQualityTimeRating: String, CaseIterable, Identifiable, Hashable {
    case correct
    case wrong
    case notShown
    case unreadable
    case hallucinated

    var id: Self { self }
    var label: String {
        switch self {
        case .correct: "Correct"
        case .wrong: "Wrong"
        case .notShown: "Not Shown"
        case .unreadable: "Unreadable"
        case .hallucinated: "Hallucinated"
        }
    }
}

enum VisualQualityKindRating: String, CaseIterable, Identifiable, Hashable {
    case correct
    case wrong
    case unsupported
    case hallucinated

    var id: Self { self }
    var label: String { rawValue.capitalized }
}

struct VisualQualityMessageAssessment: Equatable {
    var detection: VisualQualityDetectionRating? = nil
    var sender: VisualQualitySenderRating? = nil
    var text: VisualQualityTextRating? = nil
    var time: VisualQualityTimeRating? = nil
    var kind: VisualQualityKindRating? = nil

    var isComplete: Bool {
        detection != nil && sender != nil && text != nil && time != nil && kind != nil
    }
}

struct VisualQualityFrameReview {
    let frame: ObservedFrame
    let extraction: ExtractedConversationFrame
    let reconciliation: VisualQualityReconciliationSummary
    var titleRating: VisualQualityTitleRating?
    var messageAssessments: [VisualQualityMessageAssessment]
    var missingVisibleMessages = 0

    init(
        frame: ObservedFrame,
        extraction: ExtractedConversationFrame,
        reconciliation: VisualQualityReconciliationSummary
    ) {
        self.frame = frame
        self.extraction = extraction
        self.reconciliation = reconciliation
        self.messageAssessments = extraction.messages.map { _ in VisualQualityMessageAssessment() }
    }

    var isComplete: Bool {
        titleRating != nil && messageAssessments.allSatisfy(\.isComplete)
    }
}

struct VisualQualityReconciliationSummary: Equatable, Sendable {
    let action: VisualQualityReconciliationAction
    let overlapCount: Int
    let newMessageCount: Int
    let suppressedIndexes: Set<Int>
    let contributionIndexes: Set<Int>
}

enum VisualQualityReconciliationAction: Equatable, Sendable {
    case unattributed
    case empty
    case appended
    case prepended
    case gap
    case unchanged

    var label: String {
        switch self {
        case .unattributed: "No Conversation Identity"
        case .empty: "No Meaningful Messages"
        case .appended: "Append"
        case .prepended: "Prepend"
        case .gap: "Gap"
        case .unchanged: "No New Messages"
        }
    }
}

/// Aggregate-only, session-memory measurements. This type intentionally has
/// no Codable conformance and no field for titles, rows, text, or images.
struct VisualQualityMetrics: Equatable {
    var totalFrames = 0
    var reviewedFrames = 0
    var cancelledFrames = 0
    var extractionFailures = 0
    var failureCategories: [ExtractionFailureCategory: Int] = [:]
    var lastFailureCategory: ExtractionFailureCategory?
    var titleCorrect = 0
    var titleWrong = 0
    var titleUnknown = 0
    var titleHallucinated = 0
    var detectionCorrect = 0
    var detectionMissing = 0
    var detectionHallucinated = 0
    var detectionDuplicate = 0
    var senderCorrect = 0
    var senderWrong = 0
    var senderUnknown = 0
    var senderHallucinated = 0
    var textExact = 0
    var textMinorError = 0
    var textWrong = 0
    var textMissing = 0
    var textHallucinated = 0
    var textVisuallyUnreadable = 0
    var timeCorrect = 0
    var timeWrong = 0
    var timeNotShown = 0
    var timeUnreadable = 0
    var timeHallucinated = 0
    var kindCorrect = 0
    var kindWrong = 0
    var kindUnsupported = 0
    var kindHallucinated = 0
    var duplicateAfterReconciliationCount = 0
    var lostMessageCountAcrossOverlap = 0
    var appendFrames = 0
    var prependFrames = 0
    var gapFrames = 0
    var unchangedFrames = 0

    var visibleMessages: Int { detectionCorrect + detectionMissing }
    var hallucinatedMessageCount: Int { detectionHallucinated }
    var hallucinatedContentCount: Int { textHallucinated }
    var hallucinationCount: Int {
        titleHallucinated + detectionHallucinated + senderHallucinated
            + textHallucinated + timeHallucinated + kindHallucinated
    }
    var fabricatedMessageHardGatePasses: Bool {
        hallucinatedMessageCount == 0 && hallucinatedContentCount == 0
    }
    var unknownCount: Int { titleUnknown + senderUnknown }
    var wrongCount: Int { titleWrong + senderWrong + textWrong + timeWrong + kindWrong }

    var conversationTitleAccuracy: VisualQualityRate {
        rate(titleCorrect, titleCorrect + titleWrong + titleHallucinated)
    }
    var messageDetectionPrecision: VisualQualityRate {
        rate(detectionCorrect, detectionCorrect + detectionHallucinated + detectionDuplicate)
    }
    var messageDetectionRecall: VisualQualityRate {
        rate(detectionCorrect, detectionCorrect + detectionMissing)
    }
    var senderAccuracy: VisualQualityRate {
        rate(senderCorrect, senderCorrect + senderWrong + senderHallucinated)
    }
    var textExactRate: VisualQualityRate {
        rate(textExact, textExact + textMinorError + textWrong + textMissing + textHallucinated)
    }
    var textExactOrMinorRate: VisualQualityRate {
        rate(textExact + textMinorError, textExact + textMinorError + textWrong + textMissing + textHallucinated)
    }
    var visibleTimeAccuracy: VisualQualityRate {
        rate(timeCorrect, timeCorrect + timeWrong + timeHallucinated)
    }
    var messageKindAccuracy: VisualQualityRate {
        rate(kindCorrect, kindCorrect + kindWrong + kindUnsupported + kindHallucinated)
    }

    mutating func recordExtractionAttempt() { totalFrames += 1 }

    mutating func recordExtractionFailure(_ category: ExtractionFailureCategory) {
        extractionFailures += 1
        lastFailureCategory = category
        failureCategories[category, default: 0] += 1
    }

    mutating func recordReviewedFrame(
        title: VisualQualityTitleRating,
        messages: [VisualQualityMessageAssessment],
        missingVisibleMessages: Int,
        reconciliation: VisualQualityReconciliationSummary
    ) {
        reviewedFrames += 1
        detectionMissing += missingVisibleMessages
        if reconciliation.overlapCount > 0 {
            lostMessageCountAcrossOverlap += missingVisibleMessages
        }
        switch title {
        case .correct: titleCorrect += 1
        case .wrong: titleWrong += 1
        case .unknown: titleUnknown += 1
        case .hallucinated: titleHallucinated += 1
        }

        for (index, message) in messages.enumerated() {
            switch message.detection {
            case .correct:
                detectionCorrect += 1
            case .hallucinated:
                detectionHallucinated += 1
            case .missing:
                detectionMissing += 1
            case .duplicate:
                detectionDuplicate += 1
                if reconciliation.contributionIndexes.contains(index) {
                    duplicateAfterReconciliationCount += 1
                }
            case nil:
                break
            }
            switch message.sender {
            case .correct: senderCorrect += 1
            case .wrong: senderWrong += 1
            case .unknown: senderUnknown += 1
            case .hallucinated: senderHallucinated += 1
            case nil: break
            }
            switch message.text {
            case .exact: textExact += 1
            case .minorError: textMinorError += 1
            case .wrong: textWrong += 1
            case .missing: textMissing += 1
            case .hallucinated: textHallucinated += 1
            case .visuallyUnreadable: textVisuallyUnreadable += 1
            case nil: break
            }
            switch message.time {
            case .correct: timeCorrect += 1
            case .wrong: timeWrong += 1
            case .notShown: timeNotShown += 1
            case .unreadable: timeUnreadable += 1
            case .hallucinated: timeHallucinated += 1
            case nil: break
            }
            switch message.kind {
            case .correct: kindCorrect += 1
            case .wrong: kindWrong += 1
            case .unsupported: kindUnsupported += 1
            case .hallucinated: kindHallucinated += 1
            case nil: break
            }
        }

        switch reconciliation.action {
        case .appended: appendFrames += 1
        case .prepended: prependFrames += 1
        case .gap: gapFrames += 1
        case .unchanged: unchangedFrames += 1
        case .unattributed, .empty: break
        }
    }

    private func rate(_ numerator: Int, _ denominator: Int) -> VisualQualityRate {
        VisualQualityRate(numerator: numerator, denominator: denominator)
    }
}

/// Runs the same run-alignment logic as MessageIngestor without opening a
/// MessageStore or retaining screenshots beyond the active review row.
struct VisualQualityReconciliationTracker {
    private var conversationTitle: String?
    private var storedHead: [MessageIdentityKey] = []
    private var storedTail: [MessageIdentityKey] = []

    mutating func reconcile(_ frame: ExtractedConversationFrame) -> VisualQualityReconciliationSummary {
        guard let title = frame.chat?.title else {
            reset()
            return VisualQualityReconciliationSummary(
                action: .unattributed, overlapCount: 0, newMessageCount: 0,
                suppressedIndexes: [], contributionIndexes: []
            )
        }
        if title != conversationTitle {
            reset()
            conversationTitle = title
        }

        let meaningful = frame.messages.enumerated().filter {
            MessageIngestor.isMeaningfulForQualityGate($0.element)
        }
        let sourceIndexes = meaningful.map(\.offset)
        let incoming = meaningful.map { MessageIdentityKey($0.element) }
        guard !incoming.isEmpty else {
            return VisualQualityReconciliationSummary(
                action: .empty, overlapCount: 0, newMessageCount: 0,
                suppressedIndexes: [], contributionIndexes: []
            )
        }
        let decision = FrameReconciler.reconcile(
            incoming: incoming,
            storedTail: storedTail,
            storedHead: storedHead.isEmpty ? nil : storedHead
        )
        let summary: VisualQualityReconciliationSummary
        switch decision {
        case let .appended(overlap, range):
            summary = VisualQualityReconciliationSummary(
                action: .appended,
                overlapCount: overlap,
                newMessageCount: range.count,
                suppressedIndexes: Set(sourceIndexes.prefix(overlap)),
                contributionIndexes: Set(range.map { sourceIndexes[$0] })
            )
            if storedHead.isEmpty { storedHead = Array(incoming.prefix(MessageStore.reconciliationWindow)) }
            storedTail = Array((storedTail + incoming[range]).suffix(MessageStore.reconciliationWindow))
        case let .prepended(overlap, range):
            let overlapStart = incoming.count - overlap
            summary = VisualQualityReconciliationSummary(
                action: .prepended,
                overlapCount: overlap,
                newMessageCount: range.count,
                suppressedIndexes: Set(sourceIndexes[overlapStart..<incoming.count]),
                contributionIndexes: Set(range.map { sourceIndexes[$0] })
            )
            storedHead = Array((Array(incoming[range]) + storedHead).prefix(MessageStore.reconciliationWindow))
        case let .gap(range):
            summary = VisualQualityReconciliationSummary(
                action: .gap,
                overlapCount: 0,
                newMessageCount: range.count,
                suppressedIndexes: [],
                contributionIndexes: Set(range.map { sourceIndexes[$0] })
            )
            if storedHead.isEmpty { storedHead = Array(incoming.prefix(MessageStore.reconciliationWindow)) }
            storedTail = Array((storedTail + incoming[range]).suffix(MessageStore.reconciliationWindow))
        case .nothingNew:
            summary = VisualQualityReconciliationSummary(
                action: .unchanged,
                overlapCount: incoming.count,
                newMessageCount: 0,
                suppressedIndexes: Set(sourceIndexes),
                contributionIndexes: []
            )
        }
        return summary
    }

    mutating func reset() {
        conversationTitle = nil
        storedHead.removeAll(keepingCapacity: false)
        storedTail.removeAll(keepingCapacity: false)
    }
}
#endif
