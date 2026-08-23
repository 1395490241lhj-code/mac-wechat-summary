import Foundation
import Observation

@MainActor
@Observable
final class AppModel {
    var selectedDestination: Destination? = .overview
    var systemStatus = SystemStatus.unknown
    var lastDiagnostic: DiagnosticResult?
    var lastInteractionDiagnostic: InteractionDiagnosticResult?
    var isRunningDiagnostics = false
    var isRunningInteractionDiagnostics = false
    var persistenceFailed = false
    var interactionPersistenceFailed = false

    @ObservationIgnored private let service: DiagnosticsService
    @ObservationIgnored private let interactionService: InteractionDiagnosticsService
    @ObservationIgnored private let store: DiagnosticsStore
    @ObservationIgnored private let interactionStore: InteractionDiagnosticsStore
    @ObservationIgnored private var didBootstrap = false

    init(
        service: DiagnosticsService = DiagnosticsService(),
        interactionService: InteractionDiagnosticsService = InteractionDiagnosticsService(),
        store: DiagnosticsStore = .applicationSupport,
        interactionStore: InteractionDiagnosticsStore = .applicationSupport
    ) {
        self.service = service
        self.interactionService = interactionService
        self.store = store
        self.interactionStore = interactionStore
    }

    var lastDiagnosticStatus: DiagnosticStatus {
        lastDiagnostic?.status ?? .neverRun
    }

    func bootstrap(
        autoRunDiagnostics: Bool,
        autoRunInteractionDiagnostics: Bool
    ) async {
        guard !didBootstrap else { return }
        didBootstrap = true
        lastDiagnostic = try? store.load()
        lastInteractionDiagnostic = try? interactionStore.load()
        await refreshSystemStatus()
        if autoRunDiagnostics {
            await runDiagnostics(requestPermissionIfNeeded: false)
        }
        if autoRunInteractionDiagnostics {
            await runInteractionDiagnostics(requestPermissionIfNeeded: false)
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

    func runInteractionDiagnostics(requestPermissionIfNeeded: Bool = true) async {
        guard !isRunningInteractionDiagnostics else { return }
        isRunningInteractionDiagnostics = true
        interactionPersistenceFailed = false

        let result = await interactionService.run(
            requestPermissionIfNeeded: requestPermissionIfNeeded
        )
        do {
            try interactionStore.save(result)
        } catch {
            interactionPersistenceFailed = true
        }
        lastInteractionDiagnostic = result
        await refreshSystemStatus()
        isRunningInteractionDiagnostics = false
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
