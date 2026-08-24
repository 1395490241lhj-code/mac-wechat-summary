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
    var observerMetrics = PassiveObserverMetrics()
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
    @ObservationIgnored private let observer: PassiveObserver
    @ObservationIgnored private let observerStore: ObserverMetricsStore
    @ObservationIgnored private let extractionCoordinator: ExtractionCoordinator
    @ObservationIgnored private let credentials: any CredentialStoring
    @ObservationIgnored private let consentDefaults: UserDefaults
    @ObservationIgnored private var observerPollingTask: Task<Void, Never>?
    @ObservationIgnored private var didBootstrap = false

    init(
        service: DiagnosticsService = DiagnosticsService(),
        store: DiagnosticsStore = .applicationSupport,
        observer: PassiveObserver = PassiveObserver(),
        observerStore: ObserverMetricsStore = .applicationSupport,
        extractionCoordinator: ExtractionCoordinator = ExtractionCoordinator(),
        credentials: any CredentialStoring = KeychainCredentialStore(),
        consentDefaults: UserDefaults = .standard
    ) {
        self.service = service
        self.store = store
        self.observer = observer
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

    func bootstrap(autoRunDiagnostics: Bool, runObserverValidation: Bool) async {
        guard !didBootstrap else { return }
        didBootstrap = true
        lastDiagnostic = try? store.load()
        await refreshSystemStatus()
        await startObserver()
        if autoRunDiagnostics {
            await runDiagnostics(requestPermissionIfNeeded: false)
        }
        if runObserverValidation {
            try? await Task.sleep(for: .seconds(5))
            observerMetrics = await observer.snapshot()
            try? observerStore.save(observerMetrics)
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

    func startObserver() async {
        await applyExtractionConfiguration()
        await observer.start()
        await extractionCoordinator.start(frames: await observer.meaningfulFrames())
        observerMetrics = await observer.snapshot()
        extractionMetrics = await extractionCoordinator.snapshot()
        guard ObserverPollingPolicy.shouldStartPolling(
            isPolling: observerPollingTask != nil
        ) else { return }
        observerPollingTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                self.observerMetrics = await self.observer.snapshot()
                self.extractionMetrics = await self.extractionCoordinator.snapshot()
                self.latestExtraction = await self.extractionCoordinator.latest()
                try? await Task.sleep(for: .milliseconds(500))
            }
        }
    }

    func pauseObserver() async {
        await observer.pause()
        await extractionCoordinator.pause()
        observerMetrics = await observer.snapshot()
        extractionMetrics = await extractionCoordinator.snapshot()
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
