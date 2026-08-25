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

    /// Test and diagnostics seam. Never nil while enabled.
    func openStore() -> MessageStore? { store }

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
    func deleteAllHistory() async {
        try? await store?.deleteAllHistory()
        activeIngestor = nil
        store = nil
        removeDatabaseFiles()
        if isEnabled { await open() }
    }

    private func open() async {
        guard store == nil else { return }
        guard let opened = try? MessageStore(url: url) else { return }
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
