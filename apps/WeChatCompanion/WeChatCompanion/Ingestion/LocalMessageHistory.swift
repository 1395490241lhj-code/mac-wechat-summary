import Foundation

/// Owns the lifecycle of the on-device message database.
///
/// The database is created lazily and ONLY while the user has turned local
/// persistence on. With the consent off no store exists, no ingestor is handed
/// to the extraction coordinator, and no file is created on disk -- extraction
/// still runs and still shows results in memory.
///
/// This consent is deliberately separate from the remote-processing consent.
/// Remote consent decides whether a frame may leave the Mac; this one decides
/// whether the extracted text is written down. Neither implies the other.
actor LocalMessageHistory {
    /// nil means an in-memory database, used by tests so no test run ever
    /// creates a file.
    private let url: URL?
    private var store: MessageStore?
    private var activeIngestor: MessageIngestor?
    private var isEnabled = false
    private var retention: RetentionPolicy

    init(url: URL?, retention: RetentionPolicy = .defaultPolicy) {
        self.url = url
        self.retention = retention
    }

    static var applicationSupport: LocalMessageHistory {
        LocalMessageHistory(url: MessageStore.applicationSupport)
    }

    /// The ingestor to hand the extraction coordinator, or nil while the
    /// consent is off.
    func ingestor() -> MessageIngestor? { activeIngestor }

    /// True only while a database is actually open.
    var hasOpenStore: Bool { store != nil }

    /// Test and diagnostics seam.
    ///
    /// `nil` while consent is off **or** when the local store failed closed --
    /// an enabled consent no longer implies a usable store, because a database
    /// this build refuses to open (a future schema, a stamp whose tables are
    /// missing, an unversioned file) leaves consent on and readiness absent.
    func openStore() -> MessageStore? { store }

    /// Whether the user has consented to local persistence. Independent of
    /// whether a store could actually be opened.
    var isConsentEnabled: Bool { isEnabled }

    /// Aggregate readiness, safe to surface anywhere: it names a state, never a
    /// path, a conversation or any content.
    var storeState: LocalHistoryStoreState {
        guard isEnabled else { return .disabled }
        return store == nil ? .unavailable : .ready
    }

    /// What the Chats tab shows: which conversations were captured, how much
    /// of each is still kept, and the ingestion health counters.
    ///
    /// Fails soft. A store that cannot be read reports its state and empty
    /// conversations rather than throwing into the UI refresh loop, because a
    /// ledger is a status view -- an unreadable store is an answer, not an
    /// error to surface as a crash.
    func captureLedger() async -> CaptureLedger {
        let state = storeState
        guard state == .ready, let store else {
            return CaptureLedger(storeState: state)
        }
        let conversations = (try? await store.conversationSummaries()) ?? []
        let health = await activeIngestor?.snapshot() ?? IngestionMetrics()
        return CaptureLedger(storeState: state, conversations: conversations, health: health)
    }

    /// Why the last open attempt failed, kept for diagnostics. Cleared on a
    /// successful open and when consent is turned off, so it never lingers as
    /// a stale explanation for a state that has since changed.
    private(set) var lastOpenFailure: MessageStoreError?

    /// Turning this on creates the database if it does not exist yet and sweeps
    /// expired messages before anything new is written. Turning it off releases
    /// the store and the ingestor but deliberately KEEPS the existing history:
    /// withdrawing consent for future writes is not a request to delete.
    func setEnabled(_ enabled: Bool) async {
        guard enabled != isEnabled || (enabled && store == nil) else { return }
        isEnabled = enabled
        if enabled {
            await open()
        } else {
            activeIngestor = nil
            store = nil
            lastOpenFailure = nil
        }
    }

    func setRetention(_ policy: RetentionPolicy) async {
        retention = policy
        await activeIngestor?.setRetention(policy)
    }

    /// Destroys all locally stored chat history.
    ///
    /// Removes the rows, then removes the database file together with its
    /// `-wal` and `-shm` sidecars, so nothing survives in the write-ahead log.
    /// The store is reopened empty when the consent is still on.
    ///
    /// Scope is exactly this database. The Keychain credential, the remote
    /// consent flag, the persistence consent flag, the retention choice and the
    /// diagnostics files are all untouched.
    /// Also the recovery path when the store is unavailable.
    ///
    /// Deletion owns the filesystem whether or not a store is open, so a user
    /// sitting on a database this build refuses to open can clear it and get a
    /// working store back. That is an honest way out that costs the migration
    /// nothing: the app still never rewrites or downgrades an incompatible
    /// file, it only deletes one when the user asks for deletion.
    func deleteAllHistory() async {
        try? await store?.deleteAllHistory()
        activeIngestor = nil
        store = nil
        lastOpenFailure = nil
        removeDatabaseFiles()
        if isEnabled { await open() }
    }

    /// Persists already-parsed archive evidence, if the user has consented to
    /// local persistence.
    ///
    /// **Choosing an archive is not consent to start writing chat text to
    /// disk.** With the consent off this refuses and, critically, does *not*
    /// call `open()`: an import attempt must not be the thing that creates the
    /// database. That is why the gate lives here rather than inside
    /// `MessageStore`, which stays a testable primitive with no opinion about
    /// consent.
    @discardableResult
    func persistArchiveEvidence(
        transcript: WeChatNativeTranscript,
        conversationKey: ArchiveConversationKey,
        importedAt: Date = Date()
    ) async throws -> ArchivePersistenceResult {
        // Two different refusals. "Consent is off" is a fact about what the
        // user asked for; "the store would not open" is a fact about this
        // machine's database. Reporting the second as the first sends someone
        // to a setting that is already on.
        guard isEnabled else {
            throw ArchivePersistenceError.localPersistenceConsentRequired
        }
        guard let store else {
            throw ArchivePersistenceError.localStoreUnavailable
        }
        return try await store.persistArchiveEvidence(
            transcript: transcript,
            conversationKey: conversationKey,
            importedAt: importedAt
        )
    }

    /// Opens the store, or records why it could not be opened.
    ///
    /// The failure is deliberately **not** swallowed by `try?`. Consent stays
    /// exactly as the user set it -- flipping it off here would make the app's
    /// internal state disagree with the setting they can see -- and no
    /// fallback is attempted: no fresh database beside the old one, no
    /// recreate, no downgrade, no pretend-success in memory. An incompatible
    /// file is left untouched, which is what makes the migration's fail-closed
    /// guarantee mean anything.
    private func open() async {
        guard store == nil else { return }
        let opened: MessageStore
        do {
            opened = try MessageStore(url: url)
        } catch let error as MessageStoreError {
            lastOpenFailure = error
            activeIngestor = nil
            store = nil
            return
        } catch {
            lastOpenFailure = .cannotOpen(status: -1)
            activeIngestor = nil
            store = nil
            return
        }
        lastOpenFailure = nil
        store = opened
        let ingestor = MessageIngestor(store: opened, retention: retention)
        // Applied before the first new write, so a policy tightened while the
        // app was closed is honoured immediately.
        await ingestor.sweepNow()
        activeIngestor = ingestor
    }

    /// SQLite in WAL mode keeps two sidecar files beside the database. Deleting
    /// only the `.sqlite` would leave committed rows readable in the `-wal`.
    private func removeDatabaseFiles() {
        guard let url else { return }
        let manager = FileManager.default
        for path in [url.path, url.path + "-wal", url.path + "-shm"] {
            try? manager.removeItem(atPath: path)
        }
    }
}

/// Readiness of the local history store, separate from consent.
///
/// The three states are distinct facts and collapsing any two of them misleads:
/// `disabled` is the user's choice, `unavailable` is this machine's database,
/// and only `ready` means a write can succeed. Aggregate by construction --
/// it names a state and never a path, a conversation or any content.
enum LocalHistoryStoreState: String, Sendable, Equatable {
    /// Local persistence consent is off. No database exists or is opened.
    case disabled
    /// Consent is on and the store is open.
    case ready
    /// Consent is on, but the store could not be opened and was left untouched.
    case unavailable
}
