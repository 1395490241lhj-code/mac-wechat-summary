import Foundation
import AppKit
import Observation

enum ContextNavigationTarget: Equatable {
    case archiveBrowser
    case visualConversation(Int64)
    case archiveImport(Int64)
}

enum ContextRevealAnchor: Hashable {
    case visualMessage(Int64)
    case archiveRecord(importID: Int64, sequence: Int)
}

struct ContextRevealRequest: Equatable {
    let generation: UInt64
    let anchor: ContextRevealAnchor
}

/// Why the user is in Settings. Session-only: it keeps the originating task
/// recoverable and names the return action. It never authorises work.
enum PreparationOrigin: Equatable, Sendable {
    case dailySummary
    case followUps

    var destination: Destination { self == .dailySummary ? .dailySummary : .reminders }
    var returnTitle: String { self == .dailySummary ? "Back to Daily Summary" : "Back to Follow-ups" }
}

/// One-shot, session-only presentation intent; Search owns execution and results.
struct ArchiveSearchRequest: Equatable {
    let query: String
    let filter: LocalSearchFilter
}

@MainActor
@Observable
final class AppModel {
    var selectedDestination: Destination? = .overview {
        didSet {
            navigationGeneration &+= 1
            if selectedDestination == .search, oldValue != .search {
                searchParentDestination = (oldValue ?? .overview).primaryDestination
            }
            // Any other exit from Settings retires the return action; only
            // returnToPreparationOrigin() consumes the origin deliberately.
            if oldValue == .settings, selectedDestination != .settings {
                preparationOrigin = nil
            }
        }
    }
    /// Presentation-only, session-local return context. Search/reveal data is unchanged.
    @ObservationIgnored private var navigationGeneration: UInt64 = 0
    private var searchParentDestination: Destination = .overview
    var primaryNavigationDestination: Destination {
        selectedDestination == .search
            ? searchParentDestination
            : (selectedDestination ?? .overview).primaryDestination
    }
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
    /// Session-only consumer content, keyed by source-scoped canonical owner.
    private(set) var consumerConversationPreviews: [ConsumerConversationID: ConsumerConversationPreview] = [:]
    @ObservationIgnored private var consumerPreviewGeneration: UInt64 = 0
    @ObservationIgnored private var consumerPreviewVisualCounts: [Int64: Int]?
    @ObservationIgnored private var consumerPreviewStore: MessageStore?
    @ObservationIgnored private var consumerPreviewRetentionSweepAt: Date?
    @ObservationIgnored private var consumerPreviewMessagesWritten: Int?
    @ObservationIgnored private let consumerPreviewReader: @Sendable ([ConsumerConversationID]) async -> [ConsumerConversationID: ConsumerConversationPreview]
    private(set) var selectedVisualConversationID: Int64?
    private(set) var selectedVisualMessages: [PersistedMessage] = []
    private(set) var visualContextUnavailable = false
    private(set) var archiveContextUnavailable = false
    private(set) var searchHitUnavailable = false
    private(set) var contextNavigationTarget: ContextNavigationTarget?
    private(set) var contextRevealRequest: ContextRevealRequest?
    private(set) var directContextSelectionGeneration: UInt64 = 0
    private(set) var selectedVisualIsHitWindow = false
    private(set) var selectedArchiveIsHitWindow = false
    @ObservationIgnored private var revealGeneration: UInt64 = 0
    @ObservationIgnored private var visualWindowHitID: Int64?
    @ObservationIgnored private var archiveWindowHitSequence: Int?
    @ObservationIgnored private var archiveWindowHitProvenance: LocalSearchResult.Provenance?
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
    private(set) var archiveAttachmentImportStatus = ArchiveAttachmentImportStatus.idle
    private(set) var archiveEvidence = ArchiveEvidenceSnapshot.unavailable(.disabled)
    private(set) var selectedArchiveImportID: Int64?
    private(set) var selectedArchiveRecords: [ArchiveEvidenceRecord] = []
    private(set) var selectedArchiveAttachmentBatches: [ArchiveEvidenceAttachmentBatch] = []
    private(set) var archiveSearchRequest: ArchiveSearchRequest?
    private(set) var archiveDisplayNameStatus = ArchiveDisplayNameStatus.idle
    private(set) var archiveLinkStatus = ArchiveLinkStatus.idle
    /// B6 local full-text search. The query itself is *not* stored here: it
    /// lives in the Search view's own `@State` for the current app session, so
    /// closing the app cannot restore it and nothing writes it to disk.
    private(set) var localSearch = LocalSearchSnapshot()
    /// B5.1: one attachment's status after the user asked to open it. Reset by
    /// the next request; never carries a path or a hash into the UI.
    private(set) var attachmentPreviewStatus = ArchiveAttachmentPreviewStatus.idle
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
    @ObservationIgnored private let shareInbox: WeChatShareInbox?
    @ObservationIgnored private let credentials: any CredentialStoring
    /// Held so rebuilding the extractor cannot silently fall back to the
    /// production transport. Without this seam a test's transport is detached
    /// the first time a setting changes, and any assertion about requests
    /// afterwards becomes unfalsifiable.
    @ObservationIgnored private let geminiTransport: any GeminiTransporting
    @ObservationIgnored private let consentDefaults: UserDefaults
    @ObservationIgnored private let memorySync: any MemorySyncRunning
    @ObservationIgnored private let dailySummary: any DailySummaryRunning
    @ObservationIgnored private let followUpCandidates: any FollowUpCandidateRunning
    @ObservationIgnored private let answerEvidence: any AnswerEvidenceRunning
    @ObservationIgnored private let answerRunner: any AnswerRunning
    @ObservationIgnored private let reminderStore: any ReminderStoring
    @ObservationIgnored private var observerPollingTask: Task<Void, Never>?
    /// Independent of capture polling: an extraction already in flight can
    /// still finish after capture is paused, and Chats must show that result.
    @ObservationIgnored private var extractionPollingTask: Task<Void, Never>?
    @ObservationIgnored private var isConsumingShareInbox = false
    /// Only pending transport IDs; retries do not renew an already-offered reader intent.
    @ObservationIgnored private var shareHandoffOfferedItemIDs: Set<String> = []
    @ObservationIgnored private var shareInboxObserver: NSObjectProtocol?
    @ObservationIgnored private var didBootstrap = false

    init(
        service: DiagnosticsService = DiagnosticsService(),
        store: DiagnosticsStore = .applicationSupport,
        session: SystemWindowCaptureSession = SystemWindowCaptureSession(),
        observerStore: ObserverMetricsStore = .applicationSupport,
        extractionCoordinator: ExtractionCoordinator = ExtractionCoordinator(),
        messageHistory: LocalMessageHistory = .applicationSupport,
        shareInbox: WeChatShareInbox? = AppModel.defaultShareInbox(),
        credentials: any CredentialStoring = KeychainCredentialStore(),
        geminiTransport: any GeminiTransporting = GeminiFrameExtractor.productionTransport,
        consentDefaults: UserDefaults = .standard,
        memorySync: any MemorySyncRunning = AppModel.defaultMemorySyncRunner(),
        dailySummary: any DailySummaryRunning = AppModel.defaultDailySummaryRunner(),
        followUpCandidates: any FollowUpCandidateRunning = AppModel.defaultFollowUpRunner(),
        answerEvidence: any AnswerEvidenceRunning = AppModel.defaultAnswerEvidenceRunner(),
        answerRunner: any AnswerRunning = SystemLanguageModelAnswerRunner(),
        reminderStore: any ReminderStoring = AppModel.defaultReminderStore(),
        consumerPreviewReader: (@Sendable ([ConsumerConversationID]) async -> [ConsumerConversationID: ConsumerConversationPreview])? = nil
    ) {
        self.service = service
        self.store = store
        self.session = session
        self.observerStore = observerStore
        self.extractionCoordinator = extractionCoordinator
        self.messageHistory = messageHistory
        self.consumerPreviewReader = consumerPreviewReader ?? { ids in
            await messageHistory.consumerConversationPreviews(ids: ids)
        }
        self.shareInbox = shareInbox
        self.credentials = credentials
        self.geminiTransport = geminiTransport
        self.consentDefaults = consentDefaults
        self.memorySync = memorySync
        self.dailySummary = dailySummary
        self.followUpCandidates = followUpCandidates
        self.answerEvidence = answerEvidence
        self.answerRunner = answerRunner
        self.answerAvailability = answerRunner.currentAvailability()
        self.reminderStore = reminderStore
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
    static func defaultShareInbox() -> WeChatShareInbox? {
        guard !RuntimeEnvironment.isUnderTestHost else { return nil }
        return WeChatShareInbox.appGroup()
    }

    static func defaultMemorySyncRunner() -> any MemorySyncRunning {
        // `bundled()` refuses under a test host; this stays as the second of
        // two independent stops rather than trusting either alone.
        guard !PackagedMemorySyncRunner.isUnderTestHost else { return UnavailableMemorySyncRunner() }
        return PackagedMemorySyncRunner.bundled() ?? UnavailableMemorySyncRunner()
    }

    static func defaultDailySummaryRunner() -> any DailySummaryRunning {
        guard !PackagedMemorySyncRunner.isUnderTestHost else {
            return UnavailableDailySummaryRunner()
        }
        return PackagedMemorySyncRunner.bundled() ?? UnavailableDailySummaryRunner()
    }

    static func defaultFollowUpRunner() -> any FollowUpCandidateRunning {
        guard !PackagedMemorySyncRunner.isUnderTestHost else {
            return UnavailableFollowUpRunner()
        }
        return PackagedMemorySyncRunner.bundled() ?? UnavailableFollowUpRunner()
    }

    /// Reports the packaging gap rather than faking an evidence window. The
    /// packaged worker itself is reached through the follow-up runner, which is
    /// the same object; this exists only so the default above is never nil.
    static func defaultAnswerEvidenceRunner() -> any AnswerEvidenceRunning {
        guard !PackagedMemorySyncRunner.isUnderTestHost else {
            return UnavailableAnswerEvidenceRunner()
        }
        return PackagedMemorySyncRunner.bundled() ?? UnavailableAnswerEvidenceRunner()
    }

    static func defaultReminderStore() -> any ReminderStoring {
        if RuntimeEnvironment.isUnderTestHost {
            return VolatileReminderStore()
        }
        return LocalReminderStore.applicationSupport
    }


    /// The app offers only sources it owns locally: visual capture and
    /// imported archive evidence. The external database reader remains an
    /// operator-side selection and is never substituted.
    private(set) var memorySource: MemorySource = .visual
    private(set) var memorySyncPhase: MemorySyncPhase = .idle
    /// The last freshness the runner reported. Kept across a consent
    /// withdrawal -- withdrawing is not a delete request -- but nothing new is
    /// read or written while consent is off.
    private(set) var memoryFreshness: MemoryFreshnessSummary?

    /// Memory persistence and sync exist only under the local-storage consent.
    var isMemoryAvailable: Bool { allowsLocalPersistence }
    var canSyncMemory: Bool { isMemoryAvailable && !memorySyncPhase.isRunning }

    func setMemorySource(_ source: MemorySource) async {
        guard MemorySource.appSelectable.contains(source),
              source != memorySource,
              !memorySyncPhase.isRunning else { return }
        memorySource = source
        memorySyncPhase = .idle
        memoryFreshness = nil
        if allowsLocalPersistence {
            await refreshMemoryFreshness()
        }
    }

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

    // MARK: - Daily Summary

    private(set) var dailySummarySource: MemorySource = .archive
    private(set) var dailySummaryWindow: DailySummaryWindow = .today
    private(set) var dailySummaryPhase: DailySummaryPhase = .idle
    private(set) var dailySummarySnapshot: DailySummarySnapshot?
    private var memorySettingsRequested = false
    var hasMemorySettingsRequest: Bool { memorySettingsRequested }
    /// Why Settings was opened, kept only while the user stays there.
    private(set) var preparationOrigin: PreparationOrigin?

    func openArchiveDailySummary() {
        guard !dailySummaryPhase.isRunning else { return }
        setDailySummarySource(.archive)
        selectedDestination = .dailySummary
    }

    var canOpenDailySummaryMemorySettings: Bool {
        // The Advanced preparation section is only meaningful once conversations
        // can be stored. While storage is off the single route is the
        // top-level Local Message Storage control, via openStorageSettings(from:).
        allowsLocalPersistence
            && !dailySummaryPhase.isRunning
            && (!memorySyncPhase.isRunning || memorySource == dailySummarySource)
    }

    func openDailySummaryMemorySettings() async {
        guard canOpenDailySummaryMemorySettings else { return }
        let source = dailySummarySource
        await setMemorySource(source)
        guard dailySummarySource == source, memorySource == source,
              canOpenDailySummaryMemorySettings else { return }
        preparationOrigin = .dailySummary
        memorySettingsRequested = true
        selectedDestination = .settings
    }

    /// Opens Settings on the top-level local-storage control while remembering
    /// the task the user came from. Records intent and navigates; runs nothing.
    func openStorageSettings(from origin: PreparationOrigin) {
        preparationOrigin = origin
        selectedDestination = .settings
    }

    /// Navigation only: no sync, summary, or scan runs on the way back.
    func returnToPreparationOrigin() {
        guard let origin = preparationOrigin else { return }
        preparationOrigin = nil
        selectedDestination = origin.destination
    }

    /// Clears only the one-shot scroll request; the origin outlives it so the
    /// return action survives after the section has been scrolled to.
    func consumeMemorySettingsRequest() -> Bool {
        defer { memorySettingsRequested = false }
        return memorySettingsRequested
    }

    var canPrepareDailySummary: Bool {
        allowsLocalPersistence && !dailySummaryPhase.isRunning
    }

    func setDailySummarySource(_ source: MemorySource) {
        guard MemorySource.appSelectable.contains(source),
              source != dailySummarySource,
              !dailySummaryPhase.isRunning else { return }
        dailySummarySource = source
        dailySummarySnapshot = nil
        dailySummaryPhase = .idle
    }

    func setDailySummaryWindow(_ window: DailySummaryWindow) {
        guard window != dailySummaryWindow,
              !dailySummaryPhase.isRunning else { return }
        dailySummaryWindow = window
        dailySummarySnapshot = nil
        dailySummaryPhase = .idle
    }

    func prepareDailySummary(now: Date = Date()) async {
        guard allowsLocalPersistence else {
            dailySummarySnapshot = nil
            dailySummaryPhase = .failed(.consentWithheld)
            return
        }
        guard !dailySummaryPhase.isRunning else { return }

        let bounds = dailySummaryWindow.bounds(now: now)
        dailySummaryPhase = .running
        switch await dailySummary.prepare(
            source: dailySummarySource,
            start: bounds.start,
            end: bounds.end,
            messageLimit: 200
        ) {
        case .ready(let snapshot):
            dailySummarySnapshot = snapshot
            dailySummaryPhase = .ready
        case .failed(let failure):
            dailySummarySnapshot = nil
            dailySummaryPhase = .failed(failure)
        }
    }



    // MARK: - Reminders / follow-up

    private(set) var followUpSource: MemorySource = .archive
    private(set) var followUpWindow: FollowUpWindow = .today
    private(set) var followUpPhase: FollowUpPhase = .idle
    private(set) var followUpSnapshot: FollowUpCandidateSnapshot?
    private(set) var savedFollowUps: [SavedFollowUp] = []
    private(set) var reminderStoreError: ReminderStoreError?

    var canScanFollowUps: Bool {
        allowsLocalPersistence && !followUpPhase.isRunning
    }

    var canOpenFollowUpMemorySettings: Bool {
        allowsLocalPersistence
            && !followUpPhase.isRunning
            && (!memorySyncPhase.isRunning || memorySource == followUpSource)
    }

    func openFollowUpMemorySettings() async {
        guard canOpenFollowUpMemorySettings else { return }
        let source = followUpSource
        await setMemorySource(source)
        guard followUpSource == source, memorySource == source,
              canOpenFollowUpMemorySettings else { return }
        preparationOrigin = .followUps
        memorySettingsRequested = true
        selectedDestination = .settings
    }

    func setFollowUpSource(_ source: MemorySource) {
        guard MemorySource.appSelectable.contains(source),
              source != followUpSource,
              !followUpPhase.isRunning else { return }
        followUpSource = source
        followUpSnapshot = nil
        followUpPhase = .idle
    }

    func setFollowUpWindow(_ window: FollowUpWindow) {
        guard window != followUpWindow,
              !followUpPhase.isRunning else { return }
        followUpWindow = window
        followUpSnapshot = nil
        followUpPhase = .idle
    }

    func scanFollowUps(now: Date = Date()) async {
        guard allowsLocalPersistence else {
            followUpSnapshot = nil
            followUpPhase = .failed(.consentWithheld)
            return
        }
        guard !followUpPhase.isRunning else { return }

        let bounds = followUpWindow.bounds(now: now)
        followUpPhase = .running
        switch await followUpCandidates.scan(
            source: followUpSource,
            start: bounds.start,
            end: bounds.end,
            messageLimit: 200,
            candidateLimit: 50
        ) {
        case .ready(let snapshot):
            followUpSnapshot = snapshot
            followUpPhase = .ready
        case .failed(let failure):
            followUpSnapshot = nil
            followUpPhase = .failed(failure)
        }
    }

    func refreshSavedFollowUps() async {
        guard allowsLocalPersistence else {
            savedFollowUps = []
            reminderStoreError = nil
            return
        }
        do {
            savedFollowUps = try await reminderStore.load()
            reminderStoreError = nil
        } catch let error as ReminderStoreError {
            savedFollowUps = []
            reminderStoreError = error
        } catch {
            savedFollowUps = []
            reminderStoreError = .unavailable
        }
    }


    func saveFollowUpCandidate(_ candidateID: Int, now: Date = Date()) async {
        guard allowsLocalPersistence,
              let snapshot = followUpSnapshot,
              let candidate = snapshot.candidates.first(where: { $0.id == candidateID }),
              candidate.textTruncated == false,
              let conversation = snapshot.conversations.first(
                where: { $0.id == candidate.conversationIndex }
              )
        else { return }

        let reminder = SavedFollowUp(
            id: UUID(),
            source: candidate.source,
            conversationLabel: conversation.label,
            sender: candidate.sender,
            evidenceTimestamp: candidate.timestamp,
            evidenceTimestampKind: candidate.timestampKind,
            scanWindowStart: snapshot.start,
            scanWindowEnd: snapshot.end,
            coverageStatus: snapshot.coverage.status,
            coverageCaveats: snapshot.coverage.caveats,
            savedAt: now,
            text: candidate.text,
            reasons: candidate.reasons,
            status: .pending,
            archiveEvidence: candidate.archiveEvidence
        )
        do {
            savedFollowUps = try await reminderStore.add(reminder)
            reminderStoreError = nil
        } catch let error as ReminderStoreError {
            reminderStoreError = error
        } catch {
            reminderStoreError = .unavailable
        }
    }

    func setSavedFollowUpStatus(_ id: UUID, status: SavedFollowUpStatus) async {
        guard allowsLocalPersistence else { return }
        do {
            savedFollowUps = try await reminderStore.setStatus(id: id, status: status)
            reminderStoreError = nil
        } catch let error as ReminderStoreError {
            reminderStoreError = error
        } catch {
            reminderStoreError = .unavailable
        }
    }

    func deleteSavedFollowUp(_ id: UUID) async {
        guard allowsLocalPersistence else { return }
        do {
            savedFollowUps = try await reminderStore.delete(id: id)
            reminderStoreError = nil
        } catch let error as ReminderStoreError {
            reminderStoreError = error
        } catch {
            reminderStoreError = .unavailable
        }
    }

    // MARK: - Agents: on-device archive answer

    /// Reuses the Daily Summary window control verbatim. Both surfaces read the
    /// same already-synced Archive Memory over the same three ranges, so a
    /// second window type here would be two definitions of one choice.
    private(set) var answerWindow: DailySummaryWindow = .today
    private(set) var answerPhase: AnswerPhase = .idle
    private(set) var answerResult: AnswerRunResult?
    /// The sealed window the answer was built from, kept only to disclose it.
    private(set) var answerSnapshot: AnswerEvidenceSnapshot?
    private(set) var answerAvailability: AnswerRuntimeAvailability
    private(set) var answerRevealTarget: ArchiveEvidenceAnchor?
    private(set) var archiveSnapshots: [ArchiveSnapshot] = []
    private(set) var selectedArchiveConversationID: String?
    @ObservationIgnored private var answerTask: Task<Void, Never>?
    /// The one user-visible deadline for a run. It is deliberately *earlier*
    /// than the packaged worker's own kill-switch: that one bounds a child
    /// process and cannot time a run that is already inside the model, so the
    /// two are not the same timer and must not fire at the same instant. The
    /// packaged runner raises its ceiling to match; see
    /// PackagedMemorySyncRunner.answerEvidenceTimeout.
    @ObservationIgnored var answerTimeout: TimeInterval = 120

    /// The live task, not the phase, owns the single-flight lease. A run that
    /// has been asked to cancel is still alive until it actually unwinds, and a
    /// second run must not start under it.
    var isAnswerRunActive: Bool { answerTask != nil }

    var canAskArchiveQuestion: Bool {
        allowsLocalPersistence && !isAnswerRunActive && answerAvailability.isAvailable
    }

    func setAnswerWindow(_ window: DailySummaryWindow) {
        guard window != answerWindow, !isAnswerRunActive else { return }
        answerWindow = window
        answerResult = nil
        answerSnapshot = nil
        answerPhase = .idle
    }

    func loadArchiveSnapshots() async {
        guard !isAnswerRunActive else { return }
        if case let .ready(snapshots) = await answerEvidence.archiveConversations() {
            archiveSnapshots = snapshots
            if let selectedArchiveConversationID,
               !snapshots.contains(where: { $0.id == selectedArchiveConversationID }) {
                setAnswerConversation(nil)
            }
        }
    }

    func setAnswerConversation(_ snapshot: ArchiveSnapshot?) {
        guard !isAnswerRunActive else { return }
        selectedArchiveConversationID = snapshot?.id
        answerResult = nil
        answerSnapshot = nil
        answerRevealTarget = nil
        answerPhase = .idle
    }

    /// One question, one run. Refuses a second run, an empty question, and a
    /// run whose model is unavailable -- that last is checked *before* any
    /// evidence is read, so an unavailable runtime costs no worker round trip.
    func askArchiveQuestion(_ question: String, now: Date = Date()) async {
        let trimmed = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !isAnswerRunActive else { return }
        // Re-read before deciding. The cached copy may predate the model
        // becoming ready, and this is the last point at which finding out
        // costs the user nothing.
        refreshAnswerRuntimeAvailability()
        guard answerAvailability.isAvailable else {
            answerResult = nil
            answerSnapshot = nil
            answerPhase = .failed(
                .runtimeUnavailable(answerAvailability.reason ?? .frameworkUnavailable)
            )
            return
        }
        // Checked before any worker round trip: a question that cannot fit the
        // context is refused, never shortened, and never costs a read.
        if let contextSize = answerAvailability.contextSize,
           !AnswerModelInput.fitsQuestion(trimmed, contextSize: contextSize) {
            answerResult = nil
            answerSnapshot = nil
            answerPhase = .failed(.questionTooLong)
            return
        }
        answerResult = nil
        answerSnapshot = nil
        answerRevealTarget = nil
        answerPhase = .running

        let bounds = answerWindow.bounds(now: now)
        let timeout = answerTimeout
        // One task owns the run and its deadline together, so cancelling the
        // run cannot leave a timer behind still counting. The two children
        // race and the loser is cancelled; the run only ends once the
        // cancelled child has actually unwound, which is what makes the handle
        // below a truthful lease rather than a claim.
        let work = Task { [weak self] in
            guard let self else { return }
            await withTaskGroup(of: Bool.self) { group in
                group.addTask {
                    await self.runArchiveAnswer(question: trimmed, bounds: bounds)
                    return false
                }
                group.addTask {
                    // true only when the span actually elapsed; a cancellation
                    // arrives as a thrown error and is not a timeout.
                    do {
                        try await Task.sleep(for: .seconds(timeout))
                        return true
                    } catch {
                        return false
                    }
                }
                if await group.next() == true {
                    group.cancelAll()
                    await group.next()   // the run has now really stopped
                    answerPhase = .timedOut
                } else {
                    group.cancelAll()
                }
            }
        }
        answerTask = work
        await work.value
        // Only now, with the run actually finished, does ownership go back.
        // Clearing it earlier let a second run start under a live one, and let
        // this run's own cleanup wipe the second run's handle and result.
        answerTask = nil
    }

    private func runArchiveAnswer(question: String, bounds: (start: Date, end: Date)) async {
        if Task.isCancelled {
            answerPhase = .cancelled
            return
        }
        let outcome = await answerEvidence.answerEvidenceScoped(
            start: bounds.start, end: bounds.end, messageLimit: AnswerModelInput.maxRows,
            conversationCanonicalID: selectedArchiveConversationID
        )
        if Task.isCancelled {
            answerPhase = .cancelled
            return
        }
        switch outcome {
        case .failed(let failure):
            answerSnapshot = nil
            answerPhase = .failed(.evidence(failure))
            return
        case .ready(let snapshot):
            answerSnapshot = snapshot
        }
        guard let snapshot = answerSnapshot else { return }
        do {
            let result = try await answerRunner.answer(question: question, snapshot: snapshot)
            if Task.isCancelled {
                answerPhase = .cancelled
                answerResult = nil
                return
            }
            answerResult = result
            answerPhase = .answered
        } catch let failure as AnswerFailure {
            answerResult = nil
            // Before the generic case, because a cancellation is not a
            // failure. A runner that reports one as a domain case -- rather
            // than as the `CancellationError` the production runner now lets
            // through -- must still read as a cancellation here, so the two
            // cancellation paths cannot disagree about what the user sees.
            answerPhase = failure == .cancelled ? .cancelled : .failed(failure)
        } catch is CancellationError {
            answerResult = nil
            answerPhase = .cancelled
        } catch {
            answerResult = nil
            answerPhase = .failed(.generationFailed)
        }
    }

    /// Cancels the run and leaves no partial answer. A run that already
    /// finished keeps its result: cancelling nothing must not discard an answer
    /// the user is reading.
    func cancelArchiveAnswer() {
        guard answerTask != nil else { return }
        answerTask?.cancel()
        answerResult = nil
        answerPhase = .cancelled
    }

    /// Reveals a validated citation through the existing exact-reveal path.
    /// There is no second Archive reveal path for Agents.
    func openArchiveAnswerCitation(_ citation: AnswerCitation) async {
        answerRevealTarget = citation.archiveEvidence
        await openSearchResult(citation.searchResult)
    }

    /// Re-reads the runtime's own answer instead of trusting the copy taken in
    /// the initializer. A local model that becomes ready mid-session -- Apple
    /// Intelligence switched on, or the model finishing its first download --
    /// is a system fact that changes while the app runs, and reading it once at
    /// launch made a relaunch the only way to notice.
    ///
    /// Synchronous and cheap on purpose: the runtime's query is a local
    /// framework read, so this belongs on the two boundaries a user can
    /// actually observe -- appearing on the Agents surface, and pressing Ask.
    /// There is no poller, because nothing about a timer is more truthful than
    /// asking at the moment the answer matters. It touches only availability,
    /// never the phase, the result or the live task, so refreshing mid-run
    /// cannot cancel or rewrite a run the user is watching.
    func refreshAnswerRuntimeAvailability() {
        answerAvailability = answerRunner.currentAvailability()
    }

    // MARK: - Local persistence settings

    /// Turning this on opens (and if needed creates) the local database.
    /// Turning it off detaches the ingestor so no further message is written,
    /// and deliberately KEEPS what is already stored -- withdrawing consent for
    /// future writes is not a request to delete. Use `deleteLocalMessageHistory`
    /// for that.
    func setAllowsLocalPersistence(_ isAllowed: Bool) async {
        invalidateConsumerConversationPreviews()
        let navigation = navigationGeneration
        let selection = revealGeneration
        allowsLocalPersistence = isAllowed
        consentDefaults.set(isAllowed, forKey: Self.localPersistenceConsentKey)
        LocalPersistenceConsentState.record(allowsLocalMessageStorage: isAllowed, in: consentDefaults)
        if !isAllowed {
            // Takes effect immediately: no sync can start, and a result from
            // before the withdrawal is not shown as if it were current state.
            memorySyncPhase = .idle
            dailySummarySnapshot = nil
            dailySummaryPhase = .idle
            followUpSnapshot = nil
            followUpPhase = .idle
            savedFollowUps = []
            reminderStoreError = nil
        }
        await messageHistory.setEnabled(isAllowed)
        await applyExtractionConfiguration()
        await refreshCaptureLedger()
        await refreshArchiveEvidence()
        if isAllowed {
            await consumePendingShareArchives(navigation: navigation, selection: selection)
            await refreshSavedFollowUps()
        }
    }

    /// Applies immediately, including a sweep of anything the new policy has
    /// already expired. Only meaningful while persistence is on.
    func setRetentionPolicy(_ policy: RetentionPolicy) async {
        guard policy != retentionPolicy else { return }
        invalidateConsumerConversationPreviews()
        retentionPolicy = policy
        consentDefaults.set(policy.rawValue, forKey: Self.retentionPolicyKey)
        await messageHistory.setRetention(policy)
        // The sweep may have just removed messages the ledger is counting.
        await refreshCaptureLedger()
        await refreshArchiveEvidence()
    }

    func refreshArchiveEvidence() async {
        let generation = invalidateConsumerConversationPreviews()
        await refreshArchiveReader()
        await loadConsumerConversationPreviews(generation: generation)
    }

    /// Keep preview suspension after the existing reader reconciliation.
    private func refreshArchiveReader() async {
        let snapshot = await messageHistory.archiveEvidenceSnapshot()
        archiveEvidence = snapshot

        guard snapshot.storeState == .ready else {
            selectedArchiveImportID = nil
            selectedArchiveRecords = []
            selectedArchiveAttachmentBatches = []
            archiveWindowHitSequence = nil
            archiveWindowHitProvenance = nil
            selectedArchiveIsHitWindow = false
            contextRevealRequest = nil
            searchHitUnavailable = false
            return
        }

        let availableIDs = Set(snapshot.imports.map(\.id))
        if selectedVisualConversationID != nil || visualContextUnavailable {
            selectedArchiveImportID = nil
            selectedArchiveRecords = []
            selectedArchiveAttachmentBatches = []
            return
        }
        if let selectedArchiveImportID, availableIDs.contains(selectedArchiveImportID) {
            if let sequence = archiveWindowHitSequence,
               let provenance = archiveWindowHitProvenance {
                switch await messageHistory.archiveHitWindow(
                    importID: selectedArchiveImportID,
                    sequence: sequence,
                    provenance: provenance
                ) {
                case .ready(let records): selectedArchiveRecords = records
                case .hitUnavailable:
                    selectedArchiveRecords = []
                    archiveWindowHitSequence = nil
                    archiveWindowHitProvenance = nil
                    selectedArchiveIsHitWindow = false
                    contextRevealRequest = nil
                    searchHitUnavailable = true
                case .contextUnavailable:
                    self.selectedArchiveImportID = nil
                    selectedArchiveRecords = []
                    archiveContextUnavailable = true
                    contextRevealRequest = nil
                case .storageDisabled, .storeUnavailable:
                    selectedArchiveRecords = []
                    contextRevealRequest = nil
                    searchHitUnavailable = false
                }
            } else if searchHitUnavailable {
                selectedArchiveRecords = []
            } else {
                selectedArchiveRecords = await messageHistory.archiveRecords(
                    importID: selectedArchiveImportID
                )
            }
            selectedArchiveAttachmentBatches = await messageHistory.archiveAttachmentBatches(
                importID: selectedArchiveImportID
            )
        } else if selectedArchiveImportID != nil, archiveWindowHitSequence != nil {
            self.selectedArchiveImportID = nil
            selectedArchiveRecords = []
            selectedArchiveAttachmentBatches = []
            archiveContextUnavailable = true
            contextRevealRequest = nil
        } else if let first = snapshot.imports.first {
            selectedArchiveImportID = first.id
            selectedArchiveRecords = await messageHistory.archiveRecords(importID: first.id)
            selectedArchiveAttachmentBatches = await messageHistory.archiveAttachmentBatches(
                importID: first.id
            )
        } else {
            selectedArchiveImportID = nil
            selectedArchiveRecords = []
            selectedArchiveAttachmentBatches = []
        }
    }

    func openSearchResult(_ result: LocalSearchResult) async {
        revealGeneration &+= 1
        let generation = revealGeneration
        contextRevealRequest = nil
        searchHitUnavailable = false
        switch result.target {
        case .visualMessage(let conversationID, let messageID):
            let read = await messageHistory.visualHitWindow(
                conversationID: conversationID, messageID: messageID
            )
            guard revealGeneration == generation else { return }
            await refreshCaptureLedger()
            guard revealGeneration == generation else { return }
            selectedArchiveImportID = nil
            selectedArchiveRecords = []
            selectedArchiveAttachmentBatches = []
            archiveWindowHitSequence = nil
            archiveWindowHitProvenance = nil
            selectedArchiveIsHitWindow = false
            archiveContextUnavailable = false
            selectedVisualMessages = []
            selectedVisualIsHitWindow = false
            visualWindowHitID = nil
            visualContextUnavailable = false
            searchHitUnavailable = false
            switch read {
            case .ready(let messages) where messages.contains(where: { $0.id == messageID }):
                guard captureLedger.conversations.contains(where: { $0.id == conversationID }) else {
                    visualContextUnavailable = true
                    contextNavigationTarget = .visualConversation(conversationID)
                    break
                }
                selectedVisualConversationID = conversationID
                selectedVisualMessages = messages
                selectedVisualIsHitWindow = true
                visualWindowHitID = messageID
                contextNavigationTarget = .visualConversation(conversationID)
                contextRevealRequest = ContextRevealRequest(
                    generation: generation, anchor: .visualMessage(messageID)
                )
            case .contextUnavailable:
                selectedVisualConversationID = nil
                visualContextUnavailable = true
                contextNavigationTarget = .visualConversation(conversationID)
            case .hitUnavailable, .ready:
                if captureLedger.conversations.contains(where: { $0.id == conversationID }) {
                    selectedVisualConversationID = conversationID
                    searchHitUnavailable = true
                } else {
                    selectedVisualConversationID = nil
                    visualContextUnavailable = true
                }
                contextNavigationTarget = .visualConversation(conversationID)
            case .storageDisabled, .storeUnavailable:
                selectedVisualConversationID = nil
                contextNavigationTarget = nil
            }
        case .archiveRecord(let importID, let sequence, let provenance):
            let read = await messageHistory.archiveHitWindow(
                importID: importID, sequence: sequence, provenance: provenance
            )
            guard revealGeneration == generation else { return }
            selectedVisualConversationID = nil
            selectedVisualMessages = []
            selectedVisualIsHitWindow = false
            visualWindowHitID = nil
            visualContextUnavailable = false
            selectedArchiveImportID = nil
            selectedArchiveRecords = []
            selectedArchiveAttachmentBatches = []
            archiveWindowHitSequence = nil
            archiveWindowHitProvenance = nil
            selectedArchiveIsHitWindow = false
            archiveContextUnavailable = false
            await refreshArchiveEvidence()
            guard revealGeneration == generation else { return }
            searchHitUnavailable = false
            selectedArchiveImportID = nil
            selectedArchiveRecords = []
            selectedArchiveAttachmentBatches = []
            switch read {
            case .ready(let records) where records.contains(where: {
                $0.importID == importID && $0.sequence == sequence
                    && (($0.shape == .attributed && provenance == .archiveAttributed)
                        || ($0.shape == .unattributed && provenance == .archiveUnattributed))
            }):
                guard archiveEvidence.imports.contains(where: { $0.id == importID }) else {
                    archiveContextUnavailable = true
                    contextNavigationTarget = .archiveImport(importID)
                    break
                }
                selectedArchiveImportID = importID
                selectedArchiveRecords = records
                selectedArchiveIsHitWindow = true
                archiveWindowHitSequence = sequence
                archiveWindowHitProvenance = provenance
                selectedArchiveAttachmentBatches = await messageHistory.archiveAttachmentBatches(
                    importID: importID
                )
                guard revealGeneration == generation else { return }
                contextNavigationTarget = .archiveImport(importID)
                contextRevealRequest = ContextRevealRequest(
                    generation: generation,
                    anchor: .archiveRecord(importID: importID, sequence: sequence)
                )
            case .contextUnavailable:
                selectedArchiveImportID = nil
                archiveContextUnavailable = true
                contextNavigationTarget = .archiveImport(importID)
            case .hitUnavailable, .ready:
                if archiveEvidence.imports.contains(where: { $0.id == importID }) {
                    selectedArchiveImportID = importID
                    searchHitUnavailable = true
                } else {
                    archiveContextUnavailable = true
                }
                contextNavigationTarget = .archiveImport(importID)
            case .storageDisabled, .storeUnavailable:
                selectedArchiveImportID = nil
                contextNavigationTarget = nil
            }
        }
        selectedDestination = .chats
    }

    func consumeContextReveal(generation: UInt64) {
        if contextRevealRequest?.generation == generation { contextRevealRequest = nil }
    }

    /// Reveals the exact Archive record a saved follow-up was drawn from.
    ///
    /// The anchor is the whole lookup: import and sequence, taken from the
    /// worker when the follow-up was saved and revalidated against the store
    /// here. Nothing is matched, searched, or recalled -- this is the existing
    /// exact-reveal path with a different way in, so it starts no sync, scan,
    /// summary, or network work. A follow-up with no anchor has nothing to
    /// reveal, so nothing happens at all: no navigation and no error state.
    func openSavedFollowUpEvidence(_ id: UUID) async {
        guard allowsLocalPersistence,
              let reminder = savedFollowUps.first(where: { $0.id == id }),
              reminder.source == .archive,
              let anchor = reminder.archiveEvidence
        else { return }
        await openSearchResult(LocalSearchResult(
            id: "saved-follow-up:\(id.uuidString):\(anchor.importID):\(anchor.sequence)",
            target: .archiveRecord(
                importID: anchor.importID, sequence: anchor.sequence,
                provenance: .archiveAttributed
            ),
            source: .archiveAttributed,
            provenance: .archiveAttributed,
            conversationLabel: reminder.conversationLabel,
            sender: reminder.sender,
            timestamp: reminder.evidenceTimestamp,
            excerpt: reminder.text,
            linkState: nil
        ))
    }

    func selectVisualConversation(_ conversationID: Int64) async {
        revealGeneration &+= 1
        let generation = revealGeneration
        directContextSelectionGeneration &+= 1
        contextRevealRequest = nil
        searchHitUnavailable = false
        visualWindowHitID = nil
        archiveWindowHitSequence = nil
        archiveWindowHitProvenance = nil
        selectedVisualIsHitWindow = false
        selectedArchiveIsHitWindow = false
        archiveContextUnavailable = false
        await refreshCaptureLedger()
        // A newer row selection or exact reveal owns the reader after this
        // suspension. Conversation identity alone cannot distinguish intents.
        guard revealGeneration == generation else { return }
        selectedArchiveImportID = nil
        selectedArchiveRecords = []
        selectedArchiveAttachmentBatches = []
        selectedVisualConversationID = conversationID
        contextNavigationTarget = .visualConversation(conversationID)
        selectedVisualMessages = []
        visualContextUnavailable = false
        guard let messages = await messageHistory.recentVisualMessages(conversationID: conversationID)
        else {
            guard revealGeneration == generation,
                  selectedVisualConversationID == conversationID else { return }
            selectedVisualConversationID = nil
            visualContextUnavailable = true
            return
        }
        guard revealGeneration == generation,
              selectedVisualConversationID == conversationID else { return }
        selectedVisualMessages = messages
    }

    /// Explicit Overview entry; reads existing evidence but starts no processing.
    func openArchiveBrowser() async {
        if archiveEvidence.storeState == .ready,
           let importID = archiveEvidence.imports.first(where: {
               $0.id == selectedArchiveImportID
           })?.id ?? archiveEvidence.imports.first?.id {
            await selectArchiveImport(importID)
        } else {
            revealGeneration &+= 1
            directContextSelectionGeneration &+= 1
            contextRevealRequest = nil
            selectedVisualConversationID = nil
            selectedVisualMessages = []
            visualContextUnavailable = false
            selectedArchiveImportID = nil
            selectedArchiveRecords = []
            selectedArchiveAttachmentBatches = []
            contextNavigationTarget = .archiveBrowser
            archiveContextUnavailable = false
        }
        selectedDestination = .chats
    }

    func selectArchiveImport(_ importID: Int64) async {
        revealGeneration &+= 1
        let generation = revealGeneration
        directContextSelectionGeneration &+= 1
        contextRevealRequest = nil
        searchHitUnavailable = false
        visualWindowHitID = nil
        archiveWindowHitSequence = nil
        archiveWindowHitProvenance = nil
        selectedVisualIsHitWindow = false
        selectedArchiveIsHitWindow = false
        archiveContextUnavailable = false
        guard archiveEvidence.imports.contains(where: { $0.id == importID }) else { return }
        selectedVisualConversationID = nil
        selectedVisualMessages = []
        visualContextUnavailable = false
        selectedArchiveImportID = importID
        contextNavigationTarget = .archiveImport(importID)
        selectedArchiveRecords = []
        selectedArchiveAttachmentBatches = []
        let records = await messageHistory.archiveRecords(importID: importID)
        guard revealGeneration == generation, selectedArchiveImportID == importID else { return }
        selectedArchiveRecords = records
        let batches = await messageHistory.archiveAttachmentBatches(
            importID: importID
        )
        guard revealGeneration == generation, selectedArchiveImportID == importID else { return }
        selectedArchiveAttachmentBatches = batches
    }

    func beginConsumerSearch(_ query: String) {
        archiveSearchRequest = ArchiveSearchRequest(query: query, filter: .all)
        selectedDestination = .search
    }

    func beginArchiveSearch(_ query: String) {
        archiveSearchRequest = ArchiveSearchRequest(query: query, filter: .archive)
        selectedDestination = .search
    }

    func consumeArchiveSearchRequest() -> ArchiveSearchRequest? {
        defer { archiveSearchRequest = nil }
        return archiveSearchRequest
    }

    /// B6: run one local literal search and publish the display-safe snapshot.
    ///
    /// A blank query never reaches the store, so an idle Search page costs
    /// nothing and builds no index.
    func searchLocalMessages(_ query: String, filter: LocalSearchFilter) async {
        guard !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            localSearch = LocalSearchSnapshot()
            return
        }
        // Published before the await so the view can actually paint the
        // building state: the first search of a session builds the whole
        // index, and one opaque await would otherwise freeze the page with no
        // explanation. The index itself never publishes a partial result, so a
        // cancelled build simply leaves this state behind.
        localSearch = LocalSearchSnapshot(status: .preparing)
        localSearch = await messageHistory.searchLocalMessages(query, filter: filter)
    }

    /// Clears the published snapshot. The query text is the view's own state
    /// and is reset by the view; nothing to forget on disk either way.
    func clearLocalSearch() {
        localSearch = LocalSearchSnapshot()
    }

    /// B5.1: open one materialized attachment in the system previewer.
    ///
    /// User-initiated only. Selecting an import never reaches this path. The
    /// URL comes from the central resolver, which has already proven the file
    /// is a regular file beneath the app-owned attachment root; when it refuses
    /// (missing, replaced, outside the root) this reports unavailability and
    /// opens nothing.
    func previewArchiveAttachment(_ attachment: ArchiveEvidenceAttachment) async {
        attachmentPreviewStatus = .opening
        guard let url = await messageHistory.archiveAttachmentPreviewURL(attachment) else {
            attachmentPreviewStatus = .unavailable
            return
        }
        let opened = await MainActor.run { NSWorkspace.shared.open(url) }
        attachmentPreviewStatus = opened ? .opened : .unavailable
    }

    /// B5.1: select the same file in Finder. Same resolver, same refusals.
    func revealArchiveAttachment(_ attachment: ArchiveEvidenceAttachment) async {
        attachmentPreviewStatus = .opening
        guard let url = await messageHistory.archiveAttachmentPreviewURL(attachment) else {
            attachmentPreviewStatus = .unavailable
            return
        }
        await MainActor.run { NSWorkspace.shared.activateFileViewerSelecting([url]) }
        attachmentPreviewStatus = .revealed
    }


    func setArchiveImportDisplayName(_ importID: Int64, displayName: String) async {
        guard archiveEvidence.imports.contains(where: { $0.id == importID }) else {
            archiveDisplayNameStatus = .unavailable
            return
        }
        archiveDisplayNameStatus = .saving
        do {
            try await messageHistory.setArchiveImportDisplayName(
                importID: importID,
                displayName: displayName
            )
            archiveDisplayNameStatus = .saved
            await refreshArchiveEvidence()
        } catch ArchiveConversationDisplayNameError.invalidName {
            archiveDisplayNameStatus = .invalid
        } catch {
            archiveDisplayNameStatus = .unavailable
        }
    }

    func clearArchiveImportDisplayName(_ importID: Int64) async {
        guard archiveEvidence.imports.contains(where: { $0.id == importID }) else {
            archiveDisplayNameStatus = .unavailable
            return
        }
        archiveDisplayNameStatus = .saving
        do {
            try await messageHistory.clearArchiveImportDisplayName(importID: importID)
            archiveDisplayNameStatus = .cleared
            await refreshArchiveEvidence()
        } catch {
            archiveDisplayNameStatus = .unavailable
        }
    }

    func linkArchiveImport(_ importID: Int64, toVisualConversationID visualConversationID: Int64) async {
        guard archiveEvidence.imports.contains(where: { $0.id == importID }),
              captureLedger.conversations.contains(where: { $0.id == visualConversationID })
        else {
            archiveLinkStatus = .unavailable
            return
        }

        archiveLinkStatus = .linking
        do {
            try await messageHistory.linkArchiveImport(
                importID: importID,
                toVisualConversationID: visualConversationID
            )
            archiveLinkStatus = .linked
            await refreshArchiveEvidence()
        } catch ArchiveConversationLinkError.conflict {
            archiveLinkStatus = .conflict
        } catch {
            archiveLinkStatus = .unavailable
        }
    }

    func unlinkArchiveImport(_ importID: Int64) async {
        guard archiveEvidence.imports.contains(where: { $0.id == importID }) else {
            archiveLinkStatus = .unavailable
            return
        }

        archiveLinkStatus = .linking
        do {
            try await messageHistory.unlinkArchiveImport(importID: importID)
            archiveLinkStatus = .unlinked
            await refreshArchiveEvidence()
        } catch {
            archiveLinkStatus = .unavailable
        }
    }

    func importWeChatArchive(from url: URL) async {
        let navigation = navigationGeneration
        let selection = revealGeneration
        let result = await performArchiveImport(from: url)
        if let importID = result.importID {
            await openImportedConversation(importID, navigation: navigation, selection: selection)
        }
    }

    func consumePendingShareArchives() async {
        await consumePendingShareArchives(navigation: navigationGeneration, selection: revealGeneration)
    }

    private func consumePendingShareArchives(navigation: UInt64, selection: UInt64) async {
        guard !isConsumingShareInbox, let shareInbox else { return }
        isConsumingShareInbox = true
        defer { isConsumingShareInbox = false }

        let items: [WeChatShareInboxItem]
        do {
            items = try shareInbox.pendingItems()
        } catch {
            return
        }
        shareHandoffOfferedItemIDs.formIntersection(items.map(\.id))
        guard !items.isEmpty else { return }
        var didOfferHandoff = false

        for item in items {
            let result = await performArchiveImport(from: item.archiveURL)
            // One offer per drain, using only the canonical persistence identity.
            // A newer user action supersedes the whole drain's presentation intent.
            if let importID = result.importID {
                let isNewIntent = shareHandoffOfferedItemIDs.insert(item.id).inserted
                if !didOfferHandoff, isNewIntent {
                    didOfferHandoff = true
                    await openImportedConversation(importID, navigation: navigation, selection: selection)
                }
            }
            guard result.terminal else { return }
            shareInbox.remove(item)
        }
    }

    private func openImportedConversation(_ importID: Int64, navigation: UInt64, selection: UInt64) async {
        guard navigationGeneration == navigation, revealGeneration == selection,
              archiveEvidence.imports.contains(where: { $0.id == importID }) else { return }
        // Set the destination before the reader suspends, never after a newer intent.
        selectedDestination = .chats
        await selectArchiveImport(importID)
    }

    private func performArchiveImport(from url: URL) async -> (terminal: Bool, importID: Int64?) {
        guard allowsLocalPersistence else {
            archiveImportStatus = .localPersistenceConsentRequired
            return (false, nil)
        }
        guard await messageHistory.storeState == .ready else {
            archiveImportStatus = .localStoreUnavailable
            return (false, nil)
        }

        archiveImportStatus = .importing
        archiveAttachmentImportStatus = .idle
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
            switch outcome.attachments {
            case .none:
                archiveAttachmentImportStatus = .none
            case .inserted(let count, let materialized):
                archiveAttachmentImportStatus = .inserted(
                    attachmentCount: count,
                    materializedCount: materialized
                )
            case .alreadyPersisted(let count, let materialized):
                archiveAttachmentImportStatus = .alreadyPersisted(
                    attachmentCount: count,
                    materializedCount: materialized
                )
            case .unavailable:
                archiveAttachmentImportStatus = .unavailable
            }
            await refreshCaptureLedger()
            await refreshArchiveEvidence()
            // A native share is terminal only when both transcript and
            // attachment evidence reached their intended local state. If the
            // transcript committed but attachment materialization/persistence
            // failed, keep the transport ZIP so a later pass can retry the
            // same import idempotently and finish the attachment batch.
            return (outcome.attachments != .unavailable, outcome.importID)
        } catch ArchivePersistenceError.localPersistenceConsentRequired {
            archiveImportStatus = .localPersistenceConsentRequired
            return (false, nil)
        } catch ArchivePersistenceError.localStoreUnavailable {
            archiveImportStatus = .localStoreUnavailable
            return (false, nil)
        } catch {
            archiveImportStatus = .invalidArchive
            return (true, nil)
        }
    }

    /// Destructive: removes every locally stored conversation and message,
    /// including the database's write-ahead sidecar files.
    ///
    /// Scope is exactly that. The API key, the remote-processing consent, the
    /// persistence consent, the retention choice and the diagnostics records
    /// are all left alone.
    func deleteLocalMessageHistory() async {
        invalidateConsumerConversationPreviews()
        await messageHistory.deleteAllHistory()
        await applyExtractionConfiguration()
        await refreshCaptureLedger()
        await refreshArchiveEvidence()
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

    private func startShareInboxObservation() {
        guard shareInbox != nil,
              shareInboxObserver == nil,
              !RuntimeEnvironment.isUnderTestHost else { return }

        shareInboxObserver = DistributedNotificationCenter.default().addObserver(
            forName: WeChatShareInboxSignal.didChange,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                await self?.consumePendingShareArchives()
            }
        }
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
        startShareInboxObservation()
        await consumePendingShareArchives()
        await refreshArchiveEvidence()
        await refreshSavedFollowUps()
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

    @discardableResult
    private func invalidateConsumerConversationPreviews() -> UInt64 {
        consumerPreviewGeneration &+= 1
        consumerPreviewVisualCounts = nil
        consumerPreviewStore = nil
        consumerPreviewRetentionSweepAt = nil
        consumerPreviewMessagesWritten = nil
        consumerConversationPreviews = [:]
        return consumerPreviewGeneration
    }

    func refreshConsumerConversationPreviews() async {
        let generation = invalidateConsumerConversationPreviews()
        await loadConsumerConversationPreviews(generation: generation)
    }

    private func loadConsumerConversationPreviews(generation: UInt64) async {
        let ids = ConsumerConversationRow.rows(archive: archiveEvidence, visual: captureLedger).map(\.id)
        let visualCounts = Dictionary(uniqueKeysWithValues:
            captureLedger.conversations.map { ($0.id, $0.retainedMessageCount) })
        let retentionSweepAt = captureLedger.health.lastRetentionSweepAt
        let messagesWritten = captureLedger.health.messagesAppended + captureLedger.health.messagesPrepended
        guard allowsLocalPersistence, let store = await messageHistory.openStore(),
              await messageHistory.storeState == .ready,
              generation == consumerPreviewGeneration else { return }
        consumerPreviewStore = store
        consumerPreviewVisualCounts = visualCounts
        consumerPreviewRetentionSweepAt = retentionSweepAt
        consumerPreviewMessagesWritten = messagesWritten
        guard !ids.isEmpty else { return }
        let previews = await consumerPreviewReader(ids)
        let retainedIDs = await messageHistory.retainedConsumerConversationIDs()
        let state = await messageHistory.storeState
        let currentStore = await messageHistory.openStore()
        guard generation == consumerPreviewGeneration, allowsLocalPersistence, state == .ready,
              currentStore === store else { return }
        let currentIDs = Set(ConsumerConversationRow.rows(archive: archiveEvidence, visual: captureLedger).map(\.id))
        consumerConversationPreviews = previews.filter { currentIDs.contains($0.key) && retainedIDs.contains($0.key) }
    }

    /// The ledger follows consent, retention and deletion even without capture polling.
    func refreshCaptureLedger() async {
        await refreshVisualReader()
        let store = await messageHistory.openStore()
        guard allowsLocalPersistence, captureLedger.storeState == .ready, store != nil else {
            if consumerPreviewStore != nil || consumerPreviewVisualCounts != nil || !consumerConversationPreviews.isEmpty {
                invalidateConsumerConversationPreviews()
            }
            return
        }
        // Activity timestamps affect ordering, not the retained canonical tail.
        let counts = Dictionary(uniqueKeysWithValues:
            captureLedger.conversations.map { ($0.id, $0.retainedMessageCount) })
        // Sweeps can expire Archive alone; completed writes cover balanced sweep/write races.
        guard consumerPreviewStore !== store || consumerPreviewVisualCounts != counts
            || consumerPreviewRetentionSweepAt != captureLedger.health.lastRetentionSweepAt
            || consumerPreviewMessagesWritten != captureLedger.health.messagesAppended + captureLedger.health.messagesPrepended else { return }
        await refreshConsumerConversationPreviews()
    }

    private func refreshVisualReader() async {
        captureLedger = await messageHistory.captureLedger()
        guard captureLedger.storeState == .ready else {
            selectedVisualConversationID = nil
            selectedVisualMessages = []
            visualWindowHitID = nil
            selectedVisualIsHitWindow = false
            contextRevealRequest = nil
            searchHitUnavailable = false
            visualContextUnavailable = false
            return
        }
        if let selectedVisualConversationID {
            if captureLedger.conversations.contains(where: { $0.id == selectedVisualConversationID }) {
                if let messageID = visualWindowHitID {
                    switch await messageHistory.visualHitWindow(
                        conversationID: selectedVisualConversationID, messageID: messageID
                    ) {
                    case .ready(let messages):
                        if self.selectedVisualConversationID == selectedVisualConversationID {
                            selectedVisualMessages = messages
                        }
                    case .hitUnavailable:
                        selectedVisualMessages = []
                        visualWindowHitID = nil
                        selectedVisualIsHitWindow = false
                        contextRevealRequest = nil
                        searchHitUnavailable = true
                    case .contextUnavailable:
                        self.selectedVisualConversationID = nil
                        selectedVisualMessages = []
                        visualContextUnavailable = true
                        contextRevealRequest = nil
                    case .storageDisabled, .storeUnavailable:
                        selectedVisualMessages = []
                        contextRevealRequest = nil
                        searchHitUnavailable = false
                    }
                } else if !searchHitUnavailable,
                          let messages = await messageHistory.recentVisualMessages(
                            conversationID: selectedVisualConversationID
                          ), self.selectedVisualConversationID == selectedVisualConversationID {
                    selectedVisualMessages = messages
                }
            } else {
                self.selectedVisualConversationID = nil
                selectedVisualMessages = []
                visualContextUnavailable = true
                contextRevealRequest = nil
                searchHitUnavailable = false
            }
        }
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


enum ArchiveDisplayNameStatus: Equatable {
    case idle
    case saving
    case saved
    case cleared
    case invalid
    case unavailable

    var message: String? {
        switch self {
        case .idle:
            nil
        case .saving:
            "Saving display name…"
        case .saved:
            "Display name saved."
        case .cleared:
            "Display name cleared."
        case .invalid:
            "Use a single-line name between 1 and 120 characters."
        case .unavailable:
            "The display name could not be updated."
        }
    }
}

enum ArchiveLinkStatus: Equatable {
    case idle
    case linking
    case linked
    case unlinked
    case conflict
    case unavailable

    var message: String? {
        switch self {
        case .idle:
            nil
        case .linking:
            "Updating link…"
        case .linked:
            "Archive link saved."
        case .unlinked:
            "Archive link removed."
        case .conflict:
            "This archive is already linked to a different captured conversation."
        case .unavailable:
            "The archive link could not be updated."
        }
    }
}

/// B5.1 local viewing. `unavailable` is the one state a caller must be ready
/// for: the manifest says materialized but the file did not survive validation.
enum ArchiveAttachmentPreviewStatus: Equatable {
    case idle
    case opening
    case opened
    case revealed
    case unavailable

    var message: String? {
        switch self {
        case .idle:
            nil
        case .opening:
            "Opening…"
        case .opened, .revealed:
            nil
        case .unavailable:
            "This file is not available locally any more."
        }
    }
}



enum ArchiveAttachmentImportStatus: Equatable {
    case idle
    case none
    case inserted(attachmentCount: Int, materializedCount: Int)
    case alreadyPersisted(attachmentCount: Int, materializedCount: Int)
    case unavailable

    var needsAttention: Bool {
        switch self {
        case .unavailable: true
        case .inserted(let count, let materialized), .alreadyPersisted(let count, let materialized): materialized < count
        case .idle, .none: false
        }
    }

    var message: String? {
        switch self {
        case .idle, .none:
            nil
        case .inserted(let count, let materialized):
            "\(count) attachment item(s) recorded; \(materialized) safely materialized locally."
        case .alreadyPersisted(let count, let materialized):
            "Attachment batch already recorded (\(count) item(s), \(materialized) materialized)."
        case .unavailable:
            "Conversation text is saved, but attachments could not be saved. Adding this conversation is not finished; try again later."
        }
    }
}

enum ArchiveImportStatus: Equatable {
    case idle
    case importing
    case imported(recordCount: Int, transcriptShape: String)
    case alreadyImported
    case localPersistenceConsentRequired
    case localStoreUnavailable
    case invalidArchive

    var consumerMessage: String {
        switch self {
        case .importing: "Adding your conversation…"
        case .invalidArchive: "Could not read that conversation export. Choose a supported WeChat ZIP."
        case .localPersistenceConsentRequired: "This conversation is waiting. Enable local storage in Settings to save and read it."
        case .localStoreUnavailable: "Local storage is unavailable. The conversation was not added."
        default: ""
        }
    }

    var needsAttention: Bool {
        switch self {
        case .idle, .imported, .alreadyImported: false
        default: true
        }
    }

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
        case .invalidArchive:
            "The file could not be imported as a supported WeChat archive."
        }
    }
}

enum Destination: String, CaseIterable, Identifiable {
    case overview = "Home"
    case chats = "Chats"
    case search = "Search"
    case dailySummary = "Daily Summary"
    case reminders = "Follow-ups"
    case agents = "Agents"
    case diagnostics = "Diagnostics"
    case settings = "Settings"

    static let primary: [Self] = [.overview, .chats, .settings]

    var primaryDestination: Self {
        switch self {
        case .search, .dailySummary, .reminders: .overview
        case .agents, .diagnostics: .settings
        default: self
        }
    }

    var id: Self { self }

    var systemImage: String {
        switch self {
        case .overview: "rectangle.grid.2x2"
        case .chats: "bubble.left.and.bubble.right"
        case .search: "magnifyingglass"
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

#if DEBUG
extension AppModel {
    /// Presentation-only synthetic data for Xcode previews; never bootstraps a store.
    func configureConsumerPreview(
        destination: Destination,
        followUpPreparationRequired: Bool = false,
        storageOff: Bool = false,
        preparationOrigin: PreparationOrigin? = nil
    ) {
        let date = Date(timeIntervalSince1970: 1_791_151_200)
        allowsLocalPersistence = !storageOff
        archiveEvidence = ArchiveEvidenceSnapshot(storeState: .ready, imports: [
            ArchiveEvidenceImportSummary(id: 1, displayName: "Weekend plans", shape: .attributed,
                importedAt: date, recordCount: 2, firstSentAt: date, lastSentAt: date,
                isAnonymous: true, link: nil, attachmentBatchCount: 0, attachmentCount: 0, materializedAttachmentCount: 0)
        ])
        consumerConversationPreviews = [
            .archiveImport(1): ConsumerConversationPreview(sender: "Sam", text: "I will bring the notes.", kind: nil)
        ]
        selectedArchiveImportID = 1
        selectedArchiveRecords = [
            ArchiveEvidenceRecord(importID: 1, importedAt: date, shape: .attributed, sequence: 0,
                sender: "Alex", sentAt: date, sentAtText: nil, text: "Let’s meet at the cafe tomorrow."),
            ArchiveEvidenceRecord(importID: 1, importedAt: date, shape: .attributed, sequence: 1,
                sender: "Sam", sentAt: date, sentAtText: nil, text: "I will bring the notes.")
        ]
        if destination == .reminders {
            if followUpPreparationRequired { followUpPhase = .failed(.memoryUnavailable(state: "not_ready")) }
            savedFollowUps = [SavedFollowUpStatus.pending, .completed].map { status in
                SavedFollowUp(id: UUID(), source: .archive, conversationLabel: "Weekend plans",
                    sender: "Alex", evidenceTimestamp: date, evidenceTimestampKind: "source_created",
                    scanWindowStart: date.addingTimeInterval(-3600), scanWindowEnd: date,
                    coverageStatus: "partial", coverageCaveats: ["archive:partial"], savedAt: date,
                    text: status == .pending ? "Confirm the cafe booking" : "Bring the notes",
                    reasons: ["explicit_request"], status: status,
                    archiveEvidence: ArchiveEvidenceAnchor(importID: 1, sequence: 0))
            }
        }
        selectedDestination = destination
        if let preparationOrigin {
            openStorageSettings(from: preparationOrigin)
        }
    }
}
#endif
