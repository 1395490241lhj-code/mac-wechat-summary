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
    private let attachmentStore: ArchiveAttachmentStore?
    private var store: MessageStore?
    private var activeIngestor: MessageIngestor?
    private var isEnabled = false
    private var retention: RetentionPolicy

    init(
        url: URL?,
        retention: RetentionPolicy = .defaultPolicy,
        attachmentRoot: URL? = nil
    ) {
        self.url = url
        self.retention = retention
        self.attachmentStore = attachmentRoot.map(ArchiveAttachmentStore.init(rootURL:))
    }

    static var applicationSupport: LocalMessageHistory {
        let databaseURL = MessageStore.applicationSupport
        return LocalMessageHistory(
            url: databaseURL,
            attachmentRoot: databaseURL.deletingLastPathComponent()
                .appendingPathComponent("archive-attachments", isDirectory: true)
        )
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

    func archiveEvidenceSnapshot() async -> ArchiveEvidenceSnapshot {
        let state = storeState
        guard state == .ready, let store else {
            return .unavailable(state)
        }
        let imports = (try? await store.archiveImportSummaries()) ?? []
        return ArchiveEvidenceSnapshot(storeState: state, imports: imports)
    }

    func archiveRecords(importID: Int64, limit: Int = 500) async -> [ArchiveEvidenceRecord] {
        guard storeState == .ready, let store else { return [] }
        return (try? await store.archiveRecords(importID: importID, limit: limit)) ?? []
    }



    func archiveAttachmentBatches(
        importID: Int64
    ) async -> [ArchiveEvidenceAttachmentBatch] {
        guard storeState == .ready, let store else { return [] }
        return (try? await store.archiveAttachmentBatches(importID: importID)) ?? []
    }

    @discardableResult
    func persistArchiveAttachmentBatch(
        importID: Int64,
        attachments: [WeChatNativeArchiveAttachment],
        observedAt: Date = Date()
    ) async throws -> ArchiveAttachmentBatchPersistenceResult? {
        guard isEnabled else {
            throw ArchiveAttachmentPersistenceError.localPersistenceConsentRequired
        }
        guard let store else {
            throw ArchiveAttachmentPersistenceError.localStoreUnavailable
        }
        guard !attachments.isEmpty else { return nil }

        let fingerprint = ArchiveAttachmentBatchFingerprint.fingerprint(of: attachments)
        if let existing = try await store.attachmentBatchResult(
            importID: importID,
            fingerprint: fingerprint
        ) {
            return existing
        }
        guard let attachmentStore else {
            throw ArchiveAttachmentPersistenceError.attachmentStoreUnavailable
        }

        let manifests: [ArchiveAttachmentManifest]
        do {
            manifests = try attachmentStore.materialize(
                importID: importID,
                batchFingerprint: fingerprint,
                attachments: attachments
            )
        } catch {
            throw ArchiveAttachmentPersistenceError.materializationFailed
        }

        do {
            let result = try await store.persistArchiveAttachmentBatch(
                importID: importID,
                fingerprint: fingerprint,
                observedAt: observedAt,
                manifests: manifests
            )
            await reconcileAttachments()
            return result
        } catch {
            attachmentStore.removeBatch(
                importID: importID,
                batchFingerprint: fingerprint
            )
            throw ArchiveAttachmentPersistenceError.manifestPersistenceFailed
        }
    }

    func searchArchiveEvidence(_ query: String, limit: Int = 100) async -> [ArchiveEvidenceRecord] {
        guard storeState == .ready, let store else { return [] }
        return (try? await store.searchArchiveEvidence(query, limit: limit)) ?? []
    }

    func linkArchiveImport(
        importID: Int64,
        toVisualConversationID visualConversationID: Int64,
        assertedAt: Date = Date()
    ) async throws {
        guard storeState == .ready, let store else {
            throw ArchivePersistenceError.localStoreUnavailable
        }
        _ = try await store.linkArchiveImport(
            importID: importID,
            toVisualConversationID: visualConversationID,
            basis: .operator,
            assertedAt: assertedAt
        )
    }

    func unlinkArchiveImport(importID: Int64) async throws {
        guard storeState == .ready, let store else {
            throw ArchivePersistenceError.localStoreUnavailable
        }
        try await store.unlinkArchiveImport(importID: importID)
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
        attachmentStore?.removeAll()
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
        let ingestor = MessageIngestor(
            store: opened,
            retention: retention,
            retentionDidSweep: { [weak self] in
                await self?.reconcileAttachments()
            }
        )
        // Applied before the first new write, so a policy tightened while the
        // app was closed is honoured immediately.
        await ingestor.sweepNow()
        await reconcileAttachments()
        activeIngestor = ingestor
    }


    private func reconcileAttachments() async {
        guard let store, let attachmentStore else { return }
        guard let valid = try? await store.archiveAttachmentBatchKeys() else { return }
        attachmentStore.reconcile(validBatches: valid)
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


/// App-owned materialized attachment bytes for B5.
///
/// This type is deliberately outside Ingestion/Archive: the Archive directory
/// is a pure-read boundary. Every path below is generated from database IDs and
/// fingerprints, never from a ZIP entry name.
struct ArchiveAttachmentStore: Sendable {
    let rootURL: URL

    func materialize(
        importID: Int64,
        batchFingerprint: String,
        attachments: [WeChatNativeArchiveAttachment]
    ) throws -> [ArchiveAttachmentManifest] {
        guard isFingerprint(batchFingerprint) else {
            throw ArchiveAttachmentPersistenceError.materializationFailed
        }
        let manager = FileManager.default
        let importDirectory = rootURL
            .appendingPathComponent("import-\(importID)", isDirectory: true)
        let batchDirectory = importDirectory
            .appendingPathComponent("batch-\(batchFingerprint)", isDirectory: true)

        do {
            try prepareDirectory(rootURL, manager: manager)
            try prepareDirectory(importDirectory, manager: manager)
            // If a prior process died after files were written but before the
            // manifest committed, the database has already told the caller this
            // batch is absent. Rebuild that orphan directory from scratch.
            if manager.fileExists(atPath: batchDirectory.path) {
                try manager.removeItem(at: batchDirectory)
            }
            try prepareDirectory(batchDirectory, manager: manager)

            var manifests: [ArchiveAttachmentManifest] = []
            manifests.reserveCapacity(attachments.count)

            for attachment in attachments {
                let state = storageState(for: attachment.disposition)
                var relativePath: String?
                if state == .materialized {
                    guard let payload = attachment.payload,
                          let sha = attachment.contentSHA256,
                          sha.count == 64,
                          sha.unicodeScalars.allSatisfy(Self.isHex),
                          let canonicalExtension = attachment.canonicalExtension,
                          let kind = attachment.kind
                    else {
                        throw ArchiveAttachmentPersistenceError.materializationFailed
                    }
                    let filename = "\(attachment.sourceEntryIndex)-\(sha).\(canonicalExtension)"
                    let fileURL = batchDirectory.appendingPathComponent(filename, isDirectory: false)
                    try payload.write(to: fileURL, options: .atomic)
                    try manager.setAttributes(
                        [.posixPermissions: 0o600], ofItemAtPath: fileURL.path
                    )
                    relativePath = "import-\(importID)/batch-\(batchFingerprint)/\(filename)"
                    _ = kind
                }

                manifests.append(ArchiveAttachmentManifest(
                    sourceEntryIndex: attachment.sourceEntryIndex,
                    pathExtension: attachment.pathExtension,
                    byteCount: attachment.byteCount,
                    crc32: attachment.crc32,
                    kind: attachment.kind,
                    storageState: state,
                    contentSHA256: attachment.contentSHA256,
                    storedRelativePath: relativePath
                ))
            }
            return manifests
        } catch let error as ArchiveAttachmentPersistenceError {
            try? manager.removeItem(at: batchDirectory)
            throw error
        } catch {
            try? manager.removeItem(at: batchDirectory)
            throw ArchiveAttachmentPersistenceError.materializationFailed
        }
    }

    func removeBatch(importID: Int64, batchFingerprint: String) {
        guard isFingerprint(batchFingerprint) else { return }
        let url = rootURL
            .appendingPathComponent("import-\(importID)", isDirectory: true)
            .appendingPathComponent("batch-\(batchFingerprint)", isDirectory: true)
        try? FileManager.default.removeItem(at: url)
    }

    func removeAll() {
        try? FileManager.default.removeItem(at: rootURL)
    }

    /// Deletes crash-orphans and retention-orphans. The database is the source
    /// of truth for which import/batch directories remain justified.
    func reconcile(validBatches: [Int64: Set<String>]) {
        let manager = FileManager.default
        guard manager.fileExists(atPath: rootURL.path) else { return }
        guard safeDirectory(rootURL) else {
            try? manager.removeItem(at: rootURL)
            return
        }
        guard let importDirectories = try? manager.contentsOfDirectory(
            at: rootURL,
            includingPropertiesForKeys: [.isDirectoryKey, .isSymbolicLinkKey],
            options: [.skipsHiddenFiles]
        ) else { return }

        for importDirectory in importDirectories {
            guard safeDirectory(importDirectory) else {
                try? manager.removeItem(at: importDirectory)
                continue
            }
            guard let importID = Self.importID(fromDirectoryName: importDirectory.lastPathComponent),
                  let fingerprints = validBatches[importID]
            else {
                try? manager.removeItem(at: importDirectory)
                continue
            }

            guard let batchDirectories = try? manager.contentsOfDirectory(
                at: importDirectory,
                includingPropertiesForKeys: [.isDirectoryKey, .isSymbolicLinkKey],
                options: [.skipsHiddenFiles]
            ) else { continue }

            for batchDirectory in batchDirectories {
                guard safeDirectory(batchDirectory) else {
                    try? manager.removeItem(at: batchDirectory)
                    continue
                }
                let name = batchDirectory.lastPathComponent
                guard name.hasPrefix("batch-") else {
                    try? manager.removeItem(at: batchDirectory)
                    continue
                }
                let fingerprint = String(name.dropFirst("batch-".count))
                if !fingerprints.contains(fingerprint) {
                    try? manager.removeItem(at: batchDirectory)
                }
            }
        }
    }

    private func safeDirectory(_ url: URL) -> Bool {
        guard let values = try? url.resourceValues(
            forKeys: [.isDirectoryKey, .isSymbolicLinkKey]
        ) else { return false }
        return values.isDirectory == true && values.isSymbolicLink != true
    }

    private func prepareDirectory(_ url: URL, manager: FileManager) throws {
        if manager.fileExists(atPath: url.path) {
            let values = try url.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
            guard values.isDirectory == true, values.isSymbolicLink != true else {
                throw ArchiveAttachmentPersistenceError.materializationFailed
            }
        } else {
            try manager.createDirectory(
                at: url,
                withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700]
            )
        }
        try manager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: url.path)
    }

    private func storageState(
        for disposition: WeChatNativeAttachmentDisposition
    ) -> ArchiveAttachmentStorageState {
        switch disposition {
        case .materializable: .materialized
        case .unsupportedType: .unsupportedType
        case .typeMismatch: .typeMismatch
        case .oversized: .oversized
        case .budgetExceeded: .budgetExceeded
        }
    }

    private func isFingerprint(_ value: String) -> Bool {
        value.count == 64 && value.unicodeScalars.allSatisfy(Self.isHex)
    }

    private static func isHex(_ scalar: Unicode.Scalar) -> Bool {
        (scalar.value >= 48 && scalar.value <= 57)
            || (scalar.value >= 97 && scalar.value <= 102)
    }

    private static func importID(fromDirectoryName name: String) -> Int64? {
        guard name.hasPrefix("import-") else { return nil }
        return Int64(name.dropFirst("import-".count))
    }
}
