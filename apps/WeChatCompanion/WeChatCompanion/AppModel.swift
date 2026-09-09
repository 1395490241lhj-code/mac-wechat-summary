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
        await applyExtractionConfiguration()
    }

    /// Changing the model persists the ID and rebuilds the extractor. It makes
    /// no network request and uploads no frame by itself; the credential,
    /// consent value and accumulated metrics are all preserved.
    func setGeminiModel(_ model: GeminiModel) async {
        guard model != selectedGeminiModel else { return }
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
        await session.selectWindow()
        await refreshCaptureMetrics()
        await refreshExtractionState()
        startMetricsPolling()
        startExtractionPolling()
    }

    func resumeObserving() async {
        await session.resume()
        await refreshCaptureMetrics()
        await refreshExtractionState()
        startMetricsPolling()
        startExtractionPolling()
    }

    func pauseObserving() async {
        await session.pause()
        await refreshCaptureMetrics()
        // Capture polling stops, but extraction polling deliberately does not:
        // an in-flight extraction may still complete and must become visible.
        await refreshExtractionState()
        stopMetricsPolling()
    }

    func stopObserving() async {
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
