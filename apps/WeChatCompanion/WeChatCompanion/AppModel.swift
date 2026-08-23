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

    @ObservationIgnored private let service: DiagnosticsService
    @ObservationIgnored private let store: DiagnosticsStore
    @ObservationIgnored private let observer: PassiveObserver
    @ObservationIgnored private let observerStore: ObserverMetricsStore
    @ObservationIgnored private var observerPollingTask: Task<Void, Never>?
    @ObservationIgnored private var didBootstrap = false

    init(
        service: DiagnosticsService = DiagnosticsService(),
        store: DiagnosticsStore = .applicationSupport,
        observer: PassiveObserver = PassiveObserver(),
        observerStore: ObserverMetricsStore = .applicationSupport
    ) {
        self.service = service
        self.store = store
        self.observer = observer
        self.observerStore = observerStore
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
        await observer.start()
        observerMetrics = await observer.snapshot()
        guard ObserverPollingPolicy.shouldStartPolling(
            isPolling: observerPollingTask != nil
        ) else { return }
        observerPollingTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                self.observerMetrics = await self.observer.snapshot()
                try? await Task.sleep(for: .milliseconds(500))
            }
        }
    }

    func pauseObserver() async {
        await observer.pause()
        observerMetrics = await observer.snapshot()
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
