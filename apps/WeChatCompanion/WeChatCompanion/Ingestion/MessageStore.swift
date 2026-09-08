import Foundation
import SQLite3

/// Owns the sqlite3 connection so it is closed exactly once when the store is
/// released. A class rather than a stored property on the actor, because an
/// actor's `deinit` is nonisolated and may not touch isolated state.
private final class DatabaseHandle: @unchecked Sendable {
    let pointer: OpaquePointer

    init(_ pointer: OpaquePointer) { self.pointer = pointer }

    deinit { sqlite3_close_v2(pointer) }
}

enum MessageStoreError: Error, Equatable {
    case cannotOpen(status: Int32)
    case statementFailed(status: Int32)
    /// The file carries tables but no `user_version`. Its provenance is
    /// unknown, and no unversioned layout has ever shipped, so guessing one
    /// would be a migration inventing a history.
    case unversionedExistingSchema
    /// Written by a newer schema than this build understands. Refused before
    /// anything is read, stamped or modified.
    case schemaFromFuture(version: Int32)
    /// The version stamp claims a shape the file does not have.
    case schemaIncomplete(version: Int32)
    /// A reserved v2 table name already exists in a v1 file. Something other
    /// than this app wrote it; `CREATE TABLE IF NOT EXISTS` would silently
    /// bless whatever shape it has.
    case reservedTableAlreadyPresent
}

/// The local, on-device store for reconciled chat messages.
///
/// Only message-level structure reaches this file. There is no column for an
/// image, a frame, a screenshot path, a provider response, or bubble geometry,
/// so raw capture data cannot be persisted here even by mistake.
///
/// The database lives in Application Support with 0700/0600 permissions, the
/// same posture the diagnostics store already uses.
actor MessageStore {
    /// How many stored messages the reconciler compares a frame against. An
    /// overlap can never exceed one screen of bubbles, so a window well above
    /// any plausible screenful is enough and keeps the comparison O(1) in the
    /// size of the conversation.
    static let reconciliationWindow = 120

    /// The on-disk schema contract, published through SQLite's `user_version`.
    ///
    /// Read-only consumers outside this app -- the MCP bridge -- check it and
    /// refuse to read anything they do not recognise, rather than inferring the
    /// shape from the tables they happen to find. Bump it whenever a column is
    /// added, removed, renamed, or changes meaning.
    /// v1 — visual capture only. v2 — plus the four archive evidence tables.
    static let schemaVersion: Int32 = 2

    /// Tables each published version promises. The bridge checks the same
    /// contract from the read side.
    static let requiredTables: [Int32: Set<String>] = [
        1: ["conversations", "messages"],
        2: [
            "conversations", "messages",
            "archive_conversations", "archive_imports",
            "archive_attributed_records", "archive_unattributed_records",
        ],
    ]

    private static var archiveTables: Set<String> {
        requiredTables[2]!.subtracting(requiredTables[1]!)
    }

    private let database: DatabaseHandle
    /// Every use is inside an actor-isolated method, so access is serialised.
    private var handle: OpaquePointer { database.pointer }
    /// Retained so text bound into a statement is copied, not referenced.
    private static let transient = unsafeBitCast(
        -1, to: sqlite3_destructor_type.self
    )

    /// - Parameter url: `nil` opens a private in-memory database, used by tests
    ///   so no test run ever writes chat rows to disk.
    init(url: URL?) throws {
        if let url {
            try Self.prepareDirectory(for: url)
        }
        var handle: OpaquePointer?
        let status = sqlite3_open_v2(
            url?.path ?? ":memory:",
            &handle,
            SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX,
            nil
        )
        guard status == SQLITE_OK, let handle else {
            if let handle { sqlite3_close_v2(handle) }
            throw MessageStoreError.cannotOpen(status: status)
        }
        self.database = DatabaseHandle(handle)
        try Self.migrate(handle)
        if let url { try? Self.restrictPermissions(of: url) }
    }

    static var applicationSupport: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)
            .first!
            .appendingPathComponent("WeChatCompanion", isDirectory: true)
            .appendingPathComponent("messages.sqlite")
    }

    // MARK: - Schema

    /// Brings the file to the current version, or refuses to touch it.
    ///
    /// **Order matters more than it looks.** The version is read, and the
    /// decision to accept or refuse is made, *before* any statement that can
    /// change persistent state. In particular `PRAGMA journal_mode = WAL` is
    /// deliberately not executed until the schema has been accepted: it
    /// rewrites the file header and creates sidecars, so running it first would
    /// make "we refused to touch a database from the future" untrue. Only
    /// `foreign_keys`, which is connection-local, is set beforehand.
    private static func migrate(_ handle: OpaquePointer) throws {
        try exec(handle, "PRAGMA foreign_keys = ON;")

        let version = try readUserVersion(handle)
        switch version {
        case 0:
            // A stamp of 0 is not evidence of a fresh file -- an unstamped file
            // with tables in it is unknown provenance, and no unversioned
            // layout has ever shipped for us to migrate from.
            guard try applicationTables(handle).isEmpty else {
                throw MessageStoreError.unversionedExistingSchema
            }
            try transaction(handle) {
                for statement in schemaV1 + schemaV2Archive { try exec(handle, statement) }
                try exec(handle, "PRAGMA user_version = 2;")
            }
        case 1:
            try require(tablesFor: 1, in: handle, version: 1)
            // These names never existed in shipped v1. If one is already here,
            // something else wrote it; CREATE TABLE IF NOT EXISTS would adopt
            // whatever shape it has and call the result version 2.
            guard try applicationTables(handle).isDisjoint(with: archiveTables) else {
                throw MessageStoreError.reservedTableAlreadyPresent
            }
            try transaction(handle) {
                for statement in schemaV2Archive { try exec(handle, statement) }
                // Stamped only after every object the version promises exists.
                try require(tablesFor: 2, in: handle, version: 1)
                try exec(handle, "PRAGMA user_version = 2;")
            }
        case 2:
            try require(tablesFor: 2, in: handle, version: 2)
        case let future where future > schemaVersion:
            throw MessageStoreError.schemaFromFuture(version: future)
        default:
            throw MessageStoreError.schemaFromFuture(version: version)
        }

        // Accepted: only now may the file's persistent journalling change.
        try exec(handle, "PRAGMA journal_mode = WAL;")
    }

    private static func readUserVersion(_ handle: OpaquePointer) throws -> Int32 {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(handle, "PRAGMA user_version;", -1, &statement, nil) == SQLITE_OK,
              let statement
        else {
            if let statement { sqlite3_finalize(statement) }
            throw MessageStoreError.statementFailed(status: SQLITE_ERROR)
        }
        defer { sqlite3_finalize(statement) }
        guard sqlite3_step(statement) == SQLITE_ROW else {
            throw MessageStoreError.statementFailed(status: SQLITE_ERROR)
        }
        return Int32(sqlite3_column_int64(statement, 0))
    }

    /// User tables only. SQLite's own bookkeeping (`sqlite_sequence` and
    /// friends) is not evidence that someone else's schema is present.
    private static func applicationTables(_ handle: OpaquePointer) throws -> Set<String> {
        var statement: OpaquePointer?
        let sql = "SELECT name FROM sqlite_master WHERE type = 'table';"
        guard sqlite3_prepare_v2(handle, sql, -1, &statement, nil) == SQLITE_OK, let statement else {
            if let statement { sqlite3_finalize(statement) }
            throw MessageStoreError.statementFailed(status: SQLITE_ERROR)
        }
        defer { sqlite3_finalize(statement) }
        var names: Set<String> = []
        while sqlite3_step(statement) == SQLITE_ROW {
            if let raw = sqlite3_column_text(statement, 0) {
                let name = String(cString: raw)
                if !name.hasPrefix("sqlite_") { names.insert(name) }
            }
        }
        return names
    }

    private static func require(
        tablesFor version: Int32, in handle: OpaquePointer, version stamped: Int32
    ) throws {
        let present = try applicationTables(handle)
        guard requiredTables[version]!.isSubset(of: present) else {
            throw MessageStoreError.schemaIncomplete(version: stamped)
        }
    }

    private static func transaction(_ handle: OpaquePointer, _ body: () throws -> Void) throws {
        try exec(handle, "BEGIN IMMEDIATE;")
        do {
            try body()
            try exec(handle, "COMMIT;")
        } catch {
            // The stamp is written inside the transaction, so a rollback leaves
            // the file at exactly one known version, never between two.
            try? exec(handle, "ROLLBACK;")
            throw error
        }
    }

    private static let schemaV1: [String] = [
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL UNIQUE,
            first_seen_at REAL NOT NULL,
            last_seen_at REAL NOT NULL
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL
                REFERENCES conversations(id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL,
            sender TEXT,
            ownership TEXT NOT NULL,
            visible_time TEXT,
            text TEXT,
            kind TEXT NOT NULL,
            confidence REAL NOT NULL,
            first_observed_at REAL NOT NULL
        );
        """,
        // Ordering is by sequence, never by observation time: a backfilled
        // older message is observed later than the newer ones around it.
        """
        CREATE INDEX IF NOT EXISTS messages_by_position
            ON messages(conversation_id, sequence);
        """,
    ]

    /// Archive evidence. Separate tables, not extra columns on `messages`: an
    /// archive row has no ownership, no kind, no confidence and no observation
    /// time, and Shape B has no sender either. Mixing them would put archive
    /// keys into the reconciler's window and force invented values for the
    /// columns retention and "recent" are computed from.
    private static let schemaV2Archive: [String] = [
        """
        CREATE TABLE IF NOT EXISTS archive_conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_conversation_key TEXT NOT NULL UNIQUE
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS archive_imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            archive_conversation_id INTEGER NOT NULL
                REFERENCES archive_conversations(id) ON DELETE CASCADE,
            import_fingerprint TEXT NOT NULL UNIQUE,
            fingerprint_format_version INTEGER NOT NULL
                CHECK (fingerprint_format_version > 0),
            source_type TEXT NOT NULL CHECK (source_type = 'wechat_native_archive'),
            transcript_shape TEXT NOT NULL
                CHECK (transcript_shape IN ('attributed', 'unattributed')),
            imported_at REAL NOT NULL,
            archive_parser_version INTEGER NOT NULL CHECK (archive_parser_version > 0),
            time_zone_identifier TEXT,
            CHECK (
                (transcript_shape = 'attributed'   AND time_zone_identifier IS NOT NULL) OR
                (transcript_shape = 'unattributed' AND time_zone_identifier IS NULL)
            ),
            UNIQUE (id, transcript_shape)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS archive_attributed_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_id INTEGER NOT NULL,
            transcript_shape TEXT NOT NULL DEFAULT 'attributed'
                CHECK (transcript_shape = 'attributed'),
            sequence INTEGER NOT NULL CHECK (sequence >= 0),
            sender TEXT NOT NULL,
            sent_at REAL NOT NULL,
            sent_at_text TEXT NOT NULL,
            text TEXT NOT NULL,
            UNIQUE (import_id, sequence),
            FOREIGN KEY (import_id, transcript_shape)
                REFERENCES archive_imports(id, transcript_shape) ON DELETE CASCADE
        );
        """,
        // No sender, no time, no ownership, no kind. The absence is the point:
        // a query cannot read an attribution the archive never carried.
        """
        CREATE TABLE IF NOT EXISTS archive_unattributed_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_id INTEGER NOT NULL,
            transcript_shape TEXT NOT NULL DEFAULT 'unattributed'
                CHECK (transcript_shape = 'unattributed'),
            sequence INTEGER NOT NULL CHECK (sequence >= 0),
            record_text TEXT NOT NULL,
            UNIQUE (import_id, sequence),
            FOREIGN KEY (import_id, transcript_shape)
                REFERENCES archive_imports(id, transcript_shape) ON DELETE CASCADE
        );
        """,
        """
        CREATE INDEX IF NOT EXISTS archive_imports_by_conversation
            ON archive_imports(archive_conversation_id, imported_at);
        """,
        """
        CREATE INDEX IF NOT EXISTS archive_attributed_by_time
            ON archive_attributed_records(import_id, sent_at);
        """,
    ]

    /// The schema version recorded in the database file itself.
    func storedSchemaVersion() throws -> Int32 {
        try query("PRAGMA user_version;") { Int32(sqlite3_column_int64($0, 0)) }.first ?? 0
    }

    // MARK: - Queries

    func conversations() throws -> [ConversationRecord] {
        try query(
            """
            SELECT id, title, first_seen_at, last_seen_at FROM conversations
            ORDER BY last_seen_at DESC, id DESC;
            """
        ) { statement in
            ConversationRecord(
                id: sqlite3_column_int64(statement, 0),
                title: Self.string(statement, 1) ?? "",
                firstSeenAt: Date(timeIntervalSince1970: sqlite3_column_double(statement, 2)),
                lastSeenAt: Date(timeIntervalSince1970: sqlite3_column_double(statement, 3))
            )
        }
    }

    func conversation(titled title: String) throws -> ConversationRecord? {
        try conversations().first { $0.title == title }
    }

    /// Messages in conversation order, oldest first.
    func messages(inConversation id: Int64) throws -> [PersistedMessage] {
        try messageRows(
            conversationID: id,
            order: "ASC",
            limit: -1
        )
    }

    /// The newest `limit` messages, still returned oldest-first so callers read
    /// them in conversation order.
    func recentMessages(inConversation id: Int64, limit: Int) throws -> [PersistedMessage] {
        try messageRows(conversationID: id, order: "DESC", limit: limit).reversed()
    }

    func messageCount(inConversation id: Int64) throws -> Int {
        try query("SELECT COUNT(*) FROM messages WHERE conversation_id = ?;", bind: { statement in
            sqlite3_bind_int64(statement, 1, id)
        }) { statement in
            Int(sqlite3_column_int64(statement, 0))
        }.first ?? 0
    }

    private func messageRows(
        conversationID: Int64,
        order: String,
        limit: Int
    ) throws -> [PersistedMessage] {
        // `order` is a compile-time literal from this file, never caller input.
        try query(
            """
            SELECT id, conversation_id, sequence, sender, ownership, visible_time,
                   text, kind, confidence, first_observed_at
            FROM messages WHERE conversation_id = ?
            ORDER BY sequence \(order) LIMIT ?;
            """,
            bind: { statement in
                sqlite3_bind_int64(statement, 1, conversationID)
                sqlite3_bind_int64(statement, 2, Int64(limit))
            }
        ) { statement in
            PersistedMessage(
                id: sqlite3_column_int64(statement, 0),
                conversationID: sqlite3_column_int64(statement, 1),
                sequence: sqlite3_column_int64(statement, 2),
                sender: Self.string(statement, 3),
                ownership: MessageOwnership(rawValue: Self.string(statement, 4) ?? "") ?? .unknown,
                visibleTime: Self.string(statement, 5),
                text: Self.string(statement, 6),
                kind: VisibleMessageKind(rawValue: Self.string(statement, 7) ?? "") ?? .unknown,
                confidence: sqlite3_column_double(statement, 8),
                firstObservedAt: Date(timeIntervalSince1970: sqlite3_column_double(statement, 9))
            )
        }
    }

    // MARK: - Writes

    /// Finds or creates the conversation and refreshes when it was last seen.
    func conversationID(forTitle title: String, seenAt: Date) throws -> Int64 {
        let stamp = seenAt.timeIntervalSince1970
        try run(
            """
            INSERT INTO conversations (title, first_seen_at, last_seen_at)
            VALUES (?, ?, ?)
            ON CONFLICT(title) DO UPDATE SET
                last_seen_at = MAX(last_seen_at, excluded.last_seen_at);
            """
        ) { statement in
            sqlite3_bind_text(statement, 1, title, -1, Self.transient)
            sqlite3_bind_double(statement, 2, stamp)
            sqlite3_bind_double(statement, 3, stamp)
        }
        let ids = try query(
            "SELECT id FROM conversations WHERE title = ?;",
            bind: { statement in
                sqlite3_bind_text(statement, 1, title, -1, Self.transient)
            },
            row: { sqlite3_column_int64($0, 0) }
        )
        guard let id = ids.first else {
            throw MessageStoreError.statementFailed(status: SQLITE_ERROR)
        }
        return id
    }

    /// The identity keys the reconciler aligns a frame against, oldest first.
    func reconciliationTail(conversationID: Int64) throws -> [MessageIdentityKey] {
        try recentMessages(
            inConversation: conversationID,
            limit: Self.reconciliationWindow
        ).map(MessageIdentityKey.init)
    }

    /// The identity keys at the START of the stored history, used when a frame
    /// scrolls up and reveals older messages.
    func headKeys(conversationID: Int64, limit: Int) throws -> [MessageIdentityKey] {
        try messageRows(conversationID: conversationID, order: "ASC", limit: limit)
            .map(MessageIdentityKey.init)
    }

    /// Appends after the newest stored message.
    func append(
        _ messages: [ExtractedVisibleMessage],
        conversationID: Int64,
        observedAt: Date
    ) throws {
        guard !messages.isEmpty else { return }
        var sequence = try bound(conversationID: conversationID, newest: true) ?? 0
        for message in messages {
            sequence += 1
            try insert(message, conversationID: conversationID, sequence: sequence, observedAt: observedAt)
        }
    }

    /// Inserts before the oldest stored message, preserving the frame's own
    /// order. Existing rows are never renumbered.
    func prepend(
        _ messages: [ExtractedVisibleMessage],
        conversationID: Int64,
        observedAt: Date
    ) throws {
        guard !messages.isEmpty else { return }
        var sequence = try bound(conversationID: conversationID, newest: false) ?? 0
        for message in messages.reversed() {
            sequence -= 1
            try insert(message, conversationID: conversationID, sequence: sequence, observedAt: observedAt)
        }
    }

    private func bound(conversationID: Int64, newest: Bool) throws -> Int64? {
        let function = newest ? "MAX" : "MIN"
        return try query(
            "SELECT \(function)(sequence) FROM messages WHERE conversation_id = ?;",
            bind: { sqlite3_bind_int64($0, 1, conversationID) },
            row: { statement in
                sqlite3_column_type(statement, 0) == SQLITE_NULL
                    ? nil : sqlite3_column_int64(statement, 0)
            }
        ).first ?? nil
    }

    private func insert(
        _ message: ExtractedVisibleMessage,
        conversationID: Int64,
        sequence: Int64,
        observedAt: Date
    ) throws {
        try run(
            """
            INSERT INTO messages (
                conversation_id, sequence, sender, ownership, visible_time,
                text, kind, confidence, first_observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """
        ) { statement in
            sqlite3_bind_int64(statement, 1, conversationID)
            sqlite3_bind_int64(statement, 2, sequence)
            Self.bind(statement, 3, message.sender)
            sqlite3_bind_text(statement, 4, message.ownership.rawValue, -1, Self.transient)
            Self.bind(statement, 5, message.visibleTime)
            Self.bind(statement, 6, message.text)
            sqlite3_bind_text(statement, 7, message.kind.rawValue, -1, Self.transient)
            sqlite3_bind_double(statement, 8, message.confidence)
            sqlite3_bind_double(statement, 9, observedAt.timeIntervalSince1970)
            // normalizedBounds is intentionally NOT persisted.
        }
    }

    // MARK: - Deletion and retention

    /// Removes every conversation and message, then truncates the write-ahead
    /// log so the deleted rows do not survive in the sidecar files.
    ///
    /// `LocalMessageHistory` additionally removes the database files
    /// themselves; this path exists for the case where the store stays open.
    func deleteAllHistory() throws {
        // Archive evidence first: it is chat text and chat identity too, and
        // "Delete All History" would be a lie if it left any behind. Cascades
        // would cover the records, but deleting each table explicitly is what
        // a test can assert.
        try run("DELETE FROM archive_attributed_records;")
        try run("DELETE FROM archive_unattributed_records;")
        try run("DELETE FROM archive_imports;")
        try run("DELETE FROM archive_conversations;")
        try run("DELETE FROM messages;")
        try run("DELETE FROM conversations;")
        // Checkpoint first: without it the deletions live on in the -wal file.
        try Self.exec(handle, "PRAGMA wal_checkpoint(TRUNCATE);")
        try Self.exec(handle, "VACUUM;")
    }

    /// Deletes messages older than the policy allows and drops any conversation
    /// left with no messages. Returns how many messages were removed.
    ///
    /// The cutoff is applied to `first_observed_at` -- when we saw the message
    /// on screen. `visible_time` is a display string, not a date, and using it
    /// would silently keep or drop the wrong rows.
    @discardableResult
    func applyRetention(_ policy: RetentionPolicy, now: Date = Date()) throws -> Int {
        guard let maximumAge = policy.maximumAge else { return 0 }
        let cutoff = now.addingTimeInterval(-maximumAge).timeIntervalSince1970

        let before = try totalMessageCount()
        try run("DELETE FROM messages WHERE first_observed_at < ?;") { statement in
            sqlite3_bind_double(statement, 1, cutoff)
        }
        try run("""
            DELETE FROM conversations WHERE id NOT IN (
                SELECT DISTINCT conversation_id FROM messages
            );
            """)
        let removed = before - (try totalMessageCount())
        let archiveRemoved = try applyArchiveRetention(cutoff: cutoff)
        if removed > 0 || archiveRemoved > 0 {
            try Self.exec(handle, "PRAGMA wal_checkpoint(TRUNCATE);")
        }
        return removed
    }

    /// Expires archive evidence on `imported_at` -- how long we keep **our
    /// copy** -- and never on `sent_at`.
    ///
    /// Keying Shape A on its own send time would delete an imported 2022
    /// conversation the instant a 30-day policy swept, which is not what a
    /// retention setting means. It is also the only clock Shape B has, so both
    /// shapes expire the same way.
    ///
    /// - Returns: how many imports were removed.
    private func applyArchiveRetention(cutoff: TimeInterval) throws -> Int {
        let before = try archiveImportCount()
        try run("DELETE FROM archive_imports WHERE imported_at < ?;") { statement in
            sqlite3_bind_double(statement, 1, cutoff)
        }
        // Records cascade. The conversation must not outlive them:
        // `source_conversation_key` is chat identity, and keeping it after its
        // last import expired would leave the sensitive half behind.
        try run("""
            DELETE FROM archive_conversations WHERE id NOT IN (
                SELECT DISTINCT archive_conversation_id FROM archive_imports
            );
            """)
        return before - (try archiveImportCount())
    }

    func archiveImportCount() throws -> Int {
        try query("SELECT COUNT(*) FROM archive_imports;") { Int(sqlite3_column_int64($0, 0)) }
            .first ?? 0
    }

    func archiveConversationCount() throws -> Int {
        try query("SELECT COUNT(*) FROM archive_conversations;") { Int(sqlite3_column_int64($0, 0)) }
            .first ?? 0
    }

    func archiveRecordCount(shape: String) throws -> Int {
        let table = shape == "attributed"
            ? "archive_attributed_records" : "archive_unattributed_records"
        return try query("SELECT COUNT(*) FROM \(table);") { Int(sqlite3_column_int64($0, 0)) }
            .first ?? 0
    }

    // MARK: - Archive evidence

    /// Persists one already-parsed transcript, atomically.
    ///
    /// B1 does not read a ZIP. It takes the transcript Phase A produced, and
    /// the *same* in-memory value feeds both the fingerprint and the rows --
    /// there is no window in which the thing hashed and the thing stored could
    /// differ, which is the hash-then-reparse mistake PR #1 had to fix.
    ///
    /// Re-importing the same archive is a no-op, reported as
    /// `alreadyImported`. Overlapping exports are **not** deduplicated: a
    /// 65-record and an overlapping 83-record export stay two imports, because
    /// repeated identical messages inside overlapping windows are
    /// information-theoretically ambiguous and collapsing them deletes real
    /// messages.
    @discardableResult
    func persistArchiveEvidence(
        transcript: WeChatNativeTranscript,
        conversationKey: ArchiveConversationKey,
        importedAt: Date,
        failBeforeCommit: Bool = false
    ) throws -> ArchivePersistenceResult {
        let fingerprint = ArchiveImportFingerprint.fingerprint(
            of: transcript, conversationKey: conversationKey
        )
        if let existing = try importID(forFingerprint: fingerprint) {
            return .alreadyImported(importID: existing)
        }

        try Self.exec(handle, "BEGIN IMMEDIATE;")
        do {
            let conversationID = try upsertArchiveConversation(conversationKey)
            let importID = try insertArchiveImport(
                conversationID: conversationID,
                fingerprint: fingerprint,
                transcript: transcript,
                importedAt: importedAt
            )
            let inserted = try insertRecords(transcript, importID: importID)
            // `record_count` is not a column -- SQLite cannot enforce
            // "count equals children" -- so the write path checks it instead,
            // and refuses to commit a partial import.
            guard inserted == transcript.recordCount, !failBeforeCommit else {
                throw ArchivePersistenceError.recordCountMismatch(
                    expected: transcript.recordCount, inserted: inserted
                )
            }
            try Self.exec(handle, "COMMIT;")
            return .inserted(importID: importID, recordCount: inserted)
        } catch {
            try? Self.exec(handle, "ROLLBACK;")
            throw error
        }
    }

    func importID(forFingerprint fingerprint: String) throws -> Int64? {
        try query(
            "SELECT id FROM archive_imports WHERE import_fingerprint = ?;",
            bind: { Self.bind($0, 1, fingerprint) },
            row: { sqlite3_column_int64($0, 0) }
        ).first
    }

    private func upsertArchiveConversation(_ key: ArchiveConversationKey) throws -> Int64 {
        if let existing = try query(
            "SELECT id FROM archive_conversations WHERE source_conversation_key = ?;",
            bind: { Self.bind($0, 1, key.rawValue) },
            row: { sqlite3_column_int64($0, 0) }
        ).first { return existing }
        try run("INSERT INTO archive_conversations(source_conversation_key) VALUES (?);") {
            Self.bind($0, 1, key.rawValue)
        }
        return sqlite3_last_insert_rowid(handle)
    }

    private func insertArchiveImport(
        conversationID: Int64,
        fingerprint: String,
        transcript: WeChatNativeTranscript,
        importedAt: Date
    ) throws -> Int64 {
        try run("""
            INSERT INTO archive_imports(
                archive_conversation_id, import_fingerprint, fingerprint_format_version,
                source_type, transcript_shape, imported_at, archive_parser_version,
                time_zone_identifier
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """) { statement in
            sqlite3_bind_int64(statement, 1, conversationID)
            Self.bind(statement, 2, fingerprint)
            sqlite3_bind_int(statement, 3, ArchiveImportFingerprint.formatVersion)
            Self.bind(statement, 4, ArchiveSourceType.weChatNativeArchive.rawValue)
            Self.bind(statement, 5, transcript.shapeName)
            sqlite3_bind_double(statement, 6, importedAt.timeIntervalSince1970)
            sqlite3_bind_int(statement, 7, ArchiveImportFingerprint.parserVersion)
            Self.bind(statement, 8, transcript.timeZoneIdentifierForStorage)
        }
        return sqlite3_last_insert_rowid(handle)
    }

    private func insertRecords(
        _ transcript: WeChatNativeTranscript, importID: Int64
    ) throws -> Int {
        var inserted = 0
        switch transcript {
        case .attributed(let attributed):
            for message in attributed.messages {
                try run("""
                    INSERT INTO archive_attributed_records(
                        import_id, sequence, sender, sent_at, sent_at_text, text
                    ) VALUES (?, ?, ?, ?, ?, ?);
                    """) { statement in
                    sqlite3_bind_int64(statement, 1, importID)
                    sqlite3_bind_int64(statement, 2, Int64(message.sequence))
                    Self.bind(statement, 3, message.sender)
                    sqlite3_bind_double(statement, 4, message.sentAt.timeIntervalSince1970)
                    Self.bind(statement, 5, message.sentAtText)
                    Self.bind(statement, 6, message.text)
                }
                inserted += 1
            }
        case .unattributed(let unattributed):
            for record in unattributed.records {
                try run("""
                    INSERT INTO archive_unattributed_records(
                        import_id, sequence, record_text
                    ) VALUES (?, ?, ?);
                    """) { statement in
                    sqlite3_bind_int64(statement, 1, importID)
                    sqlite3_bind_int64(statement, 2, Int64(record.sequence))
                    Self.bind(statement, 3, record.recordText)
                }
                inserted += 1
            }
        }
        return inserted
    }

    func totalMessageCount() throws -> Int {
        try query("SELECT COUNT(*) FROM messages;") { Int(sqlite3_column_int64($0, 0)) }
            .first ?? 0
    }

    // MARK: - Test seams

    /// Runs one statement so a test can prove the *database* refuses a row,
    /// not merely that the writing code never builds one.
    func executeForTesting(_ sql: String) throws { try run(sql) }

    func columnNamesForTesting(_ table: String) throws -> Set<String> {
        Set(try query("PRAGMA table_info(\(table));") { Self.string($0, 1) ?? "" })
    }

    func sourceKeysForTesting() throws -> [String] {
        try query("SELECT source_conversation_key FROM archive_conversations ORDER BY id;") {
            Self.string($0, 0) ?? ""
        }
    }

    // MARK: - SQLite plumbing

    private static func exec(_ handle: OpaquePointer, _ sql: String) throws {
        let status = sqlite3_exec(handle, sql, nil, nil, nil)
        guard status == SQLITE_OK else {
            throw MessageStoreError.statementFailed(status: status)
        }
    }

    private func run(
        _ sql: String,
        bind: (OpaquePointer) -> Void = { _ in }
    ) throws {
        _ = try query(sql, bind: bind, row: { _ -> Int in 0 })
    }

    private func query<Row>(
        _ sql: String,
        bind: (OpaquePointer) -> Void = { _ in },
        row: (OpaquePointer) -> Row
    ) throws -> [Row] {
        var statement: OpaquePointer?
        let prepared = sqlite3_prepare_v2(handle, sql, -1, &statement, nil)
        guard prepared == SQLITE_OK, let statement else {
            if let statement { sqlite3_finalize(statement) }
            throw MessageStoreError.statementFailed(status: prepared)
        }
        defer { sqlite3_finalize(statement) }
        bind(statement)

        var rows: [Row] = []
        while true {
            let step = sqlite3_step(statement)
            switch step {
            case SQLITE_ROW: rows.append(row(statement))
            case SQLITE_DONE: return rows
            default: throw MessageStoreError.statementFailed(status: step)
            }
        }
    }

    private static func bind(_ statement: OpaquePointer, _ index: Int32, _ value: String?) {
        if let value {
            sqlite3_bind_text(statement, index, value, -1, transient)
        } else {
            sqlite3_bind_null(statement, index)
        }
    }

    private static func string(_ statement: OpaquePointer, _ index: Int32) -> String? {
        guard let raw = sqlite3_column_text(statement, index) else { return nil }
        return String(cString: raw)
    }

    private static func prepareDirectory(for url: URL) throws {
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(),
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
    }

    private static func restrictPermissions(of url: URL) throws {
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o600], ofItemAtPath: url.path
        )
    }
}
