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
    /// In-memory only. Cleared when the app exits; never written to disk.
    var latestExtraction: ExtractedConversationFrame?
    /// Transient text-field buffer, cleared as soon as the key reaches the Keychain.
    var apiKeyInput = ""
    private(set) var hasProviderCredential = false
    private(set) var allowsRemoteProcessing = false
    private(set) var credentialErrorOccurred = false

    @ObservationIgnored private let service: DiagnosticsService
    @ObservationIgnored private let store: DiagnosticsStore
    @ObservationIgnored private let session: SystemWindowCaptureSession
    @ObservationIgnored private let observerStore: ObserverMetricsStore
    @ObservationIgnored private let extractionCoordinator: ExtractionCoordinator
    @ObservationIgnored private let credentials: any CredentialStoring
    @ObservationIgnored private let consentDefaults: UserDefaults
    @ObservationIgnored private var observerPollingTask: Task<Void, Never>?
    @ObservationIgnored private var didBootstrap = false

    init(
        service: DiagnosticsService = DiagnosticsService(),
        store: DiagnosticsStore = .applicationSupport,
        session: SystemWindowCaptureSession = SystemWindowCaptureSession(),
        observerStore: ObserverMetricsStore = .applicationSupport,
        extractionCoordinator: ExtractionCoordinator = ExtractionCoordinator(),
        credentials: any CredentialStoring = KeychainCredentialStore(),
        consentDefaults: UserDefaults = .standard
    ) {
        self.service = service
        self.store = store
        self.session = session
        self.observerStore = observerStore
        self.extractionCoordinator = extractionCoordinator
        self.credentials = credentials
        self.consentDefaults = consentDefaults
        hasProviderCredential = credentials.hasSecret(
            account: GeminiFrameExtractor.credentialAccount
        )
        allowsRemoteProcessing = consentDefaults.bool(forKey: Self.remoteConsentKey)
    }

    /// Only a boolean consent flag is stored here. The API key lives in the
    /// Keychain and never touches UserDefaults.
    static let remoteConsentKey = "extraction.allowsRemoteProcessing"

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

    private func applyExtractionConfiguration() async {
        await extractionCoordinator.updateConfiguration(
            extractor: GeminiFrameExtractor(credentials: credentials),
            capability: ExtractionCapability(userEnabledRemoteProvider: allowsRemoteProcessing)
        )
        extractionMetrics = await extractionCoordinator.snapshot()
    }

    var lastDiagnosticStatus: DiagnosticStatus {
        lastDiagnostic?.status ?? .neverRun
    }

    /// True while a metrics polling loop is running. Polling only runs while the
    /// observer is active, so a paused observer costs nothing.
    var isPollingObserverMetrics: Bool { observerPollingTask != nil }

    var needsWindowSelection: Bool {
        captureMetrics.state == .needsWindowSelection || captureMetrics.state == .selectionLost
    }

    func bootstrap(autoRunDiagnostics: Bool, runObserverValidation: Bool) async {
        guard !didBootstrap else { return }
        didBootstrap = true
        lastDiagnostic = try? store.load()
        await refreshSystemStatus()
        await applyExtractionConfiguration()
        await extractionCoordinator.start(frames: await session.meaningfulFrames())
        await refreshCaptureMetrics()
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
        startMetricsPolling()
    }

    func resumeObserving() async {
        await session.resume()
        await refreshCaptureMetrics()
        startMetricsPolling()
    }

    func pauseObserving() async {
        await session.pause()
        await refreshCaptureMetrics()
        stopMetricsPolling()
    }

    func stopObserving() async {
        await session.stopObserving()
        await refreshCaptureMetrics()
        capturePreview = nil
        stopMetricsPolling()
    }

    func clearCapturePreview() async {
        await session.clearPreview()
        capturePreview = nil
    }

    private func refreshCaptureMetrics() async {
        captureMetrics = await session.snapshot()
        capturePreview = await session.latestPreview()
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
