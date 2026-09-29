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

enum MessageStoreError: Error, Equatable, Sendable {
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
    /// v1 — visual capture only.
    /// v2 — plus the four archive evidence tables.
    /// v3 — plus the explicit Archive ↔ Visual conversation link relation.
    /// v4 — plus import-level attachment batches and attachment manifests.
    /// v5 — plus operator-confirmed archive display labels.
    static let schemaVersion: Int32 = 5

    /// Tables each published version promises. The bridge checks the same
    /// contract from the read side.
    static let requiredTables: [Int32: Set<String>] = [
        1: ["conversations", "messages"],
        2: [
            "conversations", "messages",
            "archive_conversations", "archive_imports",
            "archive_attributed_records", "archive_unattributed_records",
        ],
        3: [
            "conversations", "messages",
            "archive_conversations", "archive_imports",
            "archive_attributed_records", "archive_unattributed_records",
            "archive_conversation_links",
        ],
        4: [
            "conversations", "messages",
            "archive_conversations", "archive_imports",
            "archive_attributed_records", "archive_unattributed_records",
            "archive_conversation_links",
            "archive_attachment_batches", "archive_attachments",
        ],
        5: [
            "conversations", "messages",
            "archive_conversations", "archive_imports",
            "archive_attributed_records", "archive_unattributed_records",
            "archive_conversation_links",
            "archive_attachment_batches", "archive_attachments",
            "archive_conversation_labels",
        ],
    ]

    private static var postV1Tables: Set<String> {
        requiredTables[5]!.subtracting(requiredTables[1]!)
    }

    private static var v3Tables: Set<String> {
        requiredTables[3]!.subtracting(requiredTables[2]!)
    }

    private static var v4Tables: Set<String> {
        requiredTables[4]!.subtracting(requiredTables[3]!)
    }

    private static var v5Tables: Set<String> {
        requiredTables[5]!.subtracting(requiredTables[4]!)
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
                for statement in schemaV1 + schemaV2Archive + schemaV3Links
                    + schemaV4Attachments + schemaV5Labels {
                    try exec(handle, statement)
                }
                try require(tablesFor: 5, in: handle, version: 0)
                try exec(handle, "PRAGMA user_version = 5;")
            }
        case 1:
            try require(tablesFor: 1, in: handle, version: 1)
            guard try applicationTables(handle).isDisjoint(with: postV1Tables) else {
                throw MessageStoreError.reservedTableAlreadyPresent
            }
            try transaction(handle) {
                for statement in schemaV2Archive + schemaV3Links
                    + schemaV4Attachments + schemaV5Labels {
                    try exec(handle, statement)
                }
                try require(tablesFor: 5, in: handle, version: 1)
                try exec(handle, "PRAGMA user_version = 5;")
            }
        case 2:
            try require(tablesFor: 2, in: handle, version: 2)
            guard try applicationTables(handle).isDisjoint(
                with: v3Tables.union(v4Tables).union(v5Tables)
            ) else {
                throw MessageStoreError.reservedTableAlreadyPresent
            }
            try transaction(handle) {
                for statement in schemaV3Links + schemaV4Attachments + schemaV5Labels {
                    try exec(handle, statement)
                }
                try require(tablesFor: 5, in: handle, version: 2)
                try exec(handle, "PRAGMA user_version = 5;")
            }
        case 3:
            try require(tablesFor: 3, in: handle, version: 3)
            guard try applicationTables(handle).isDisjoint(
                with: v4Tables.union(v5Tables)
            ) else {
                throw MessageStoreError.reservedTableAlreadyPresent
            }
            try transaction(handle) {
                for statement in schemaV4Attachments + schemaV5Labels {
                    try exec(handle, statement)
                }
                try require(tablesFor: 5, in: handle, version: 3)
                try exec(handle, "PRAGMA user_version = 5;")
            }
        case 4:
            try require(tablesFor: 4, in: handle, version: 4)
            guard try applicationTables(handle).isDisjoint(with: v5Tables) else {
                throw MessageStoreError.reservedTableAlreadyPresent
            }
            try transaction(handle) {
                for statement in schemaV5Labels { try exec(handle, statement) }
                try require(tablesFor: 5, in: handle, version: 4)
                try exec(handle, "PRAGMA user_version = 5;")
            }
        case 5:
            try require(tablesFor: 5, in: handle, version: 5)
        case let future where future > schemaVersion:
            throw MessageStoreError.schemaFromFuture(version: future)
        default:
            throw MessageStoreError.schemaFromFuture(version: version)
        }

        // Accepted: only now may the file's persistent journalling change.
        try exec(handle, "PRAGMA journal_mode = WAL;")
    }

    /// Every read that the migration decision rests on goes through here, and
    /// an uncertain read throws with SQLite's **actual** status rather than a
    /// generic error -- the difference between "the file says 2" and "we could
    /// not find out" must reach the caller, because the second must never
    /// stamp, migrate or change journal mode.
    private static func prepared<T>(
        _ handle: OpaquePointer, _ sql: String, _ body: (OpaquePointer) throws -> T
    ) throws -> T {
        var statement: OpaquePointer?
        let prepared = sqlite3_prepare_v2(handle, sql, -1, &statement, nil)
        guard prepared == SQLITE_OK, let statement else {
            if let statement { sqlite3_finalize(statement) }
            throw MessageStoreError.statementFailed(status: prepared)
        }
        defer { sqlite3_finalize(statement) }
        return try body(statement)
    }

    private static func readUserVersion(_ handle: OpaquePointer) throws -> Int32 {
        try prepared(handle, "PRAGMA user_version;") { statement in
            let step = sqlite3_step(statement)
            guard step == SQLITE_ROW else {
                throw MessageStoreError.statementFailed(status: step)
            }
            return Int32(sqlite3_column_int64(statement, 0))
        }
    }

    /// Collects rows from a prepared statement, failing closed on any status
    /// that is neither `SQLITE_ROW` nor `SQLITE_DONE`. Extracted so a test can
    /// drive it with a statement that errors mid-iteration and prove a partial
    /// result never escapes.
    static func collectRows<T>(
        _ statement: OpaquePointer, _ row: (OpaquePointer) -> T
    ) throws -> [T] {
        var rows: [T] = []
        while true {
            let step = sqlite3_step(statement)
            switch step {
            case SQLITE_ROW: rows.append(row(statement))
            case SQLITE_DONE: return rows
            default: throw MessageStoreError.statementFailed(status: step)
            }
        }
    }

    /// User tables only. SQLite's own bookkeeping (`sqlite_sequence` and
    /// friends) is not evidence that someone else's schema is present.
    private static func applicationTables(_ handle: OpaquePointer) throws -> Set<String> {
        // Treating "anything that is not a row" as the end would let a read
        // error return a *partial* table list, and schema discovery would then
        // accept a file it never finished inspecting.
        let names = try prepared(handle, "SELECT name FROM sqlite_master WHERE type = 'table';") {
            try collectRows($0) { string($0, 0) }
        }
        return Set(names.compactMap { $0 }.filter { !$0.hasPrefix("sqlite_") })
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

    /// B4 linkage is a separate relation, never a column on either observation.
    /// Deleting the relation removes the assertion without rewriting Archive
    /// evidence or visual capture history.
    private static let schemaV3Links: [String] = [
        """
        CREATE TABLE IF NOT EXISTS archive_conversation_links (
            archive_conversation_id INTEGER PRIMARY KEY
                REFERENCES archive_conversations(id) ON DELETE CASCADE,
            visual_conversation_id INTEGER NOT NULL
                REFERENCES conversations(id) ON DELETE CASCADE,
            basis TEXT NOT NULL CHECK (basis IN ('operator', 'source_provided')),
            asserted_at REAL NOT NULL
        );
        """,
        """
        CREATE INDEX IF NOT EXISTS archive_links_by_visual_conversation
            ON archive_conversation_links(visual_conversation_id);
        """,
    ]



    /// B5 attachment evidence is source-package scoped, never message scoped.
    /// One transcript import may accumulate multiple attachment batches when
    /// the same transcript is re-shared from different source ZIPs.
    private static let schemaV4Attachments: [String] = [
        """
        CREATE TABLE IF NOT EXISTS archive_attachment_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_id INTEGER NOT NULL
                REFERENCES archive_imports(id) ON DELETE CASCADE,
            batch_fingerprint TEXT NOT NULL,
            observed_at REAL NOT NULL,
            attachment_count INTEGER NOT NULL CHECK (attachment_count > 0),
            materialized_count INTEGER NOT NULL
                CHECK (materialized_count >= 0 AND materialized_count <= attachment_count),
            UNIQUE (import_id, batch_fingerprint)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS archive_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL
                REFERENCES archive_attachment_batches(id) ON DELETE CASCADE,
            source_entry_index INTEGER NOT NULL CHECK (source_entry_index >= 0),
            path_extension TEXT NOT NULL,
            byte_count INTEGER NOT NULL CHECK (byte_count >= 0),
            crc32 INTEGER NOT NULL CHECK (crc32 >= 0 AND crc32 <= 4294967295),
            media_kind TEXT
                CHECK (media_kind IS NULL OR media_kind IN ('image', 'video', 'document')),
            storage_state TEXT NOT NULL
                CHECK (storage_state IN (
                    'materialized', 'unsupported_type', 'type_mismatch',
                    'oversized', 'budget_exceeded'
                )),
            content_sha256 TEXT,
            stored_relative_path TEXT,
            relation_scope TEXT NOT NULL DEFAULT 'import_only'
                CHECK (relation_scope = 'import_only'),
            CHECK (
                (storage_state = 'materialized'
                    AND media_kind IS NOT NULL
                    AND content_sha256 IS NOT NULL
                    AND stored_relative_path IS NOT NULL)
                OR
                (storage_state <> 'materialized'
                    AND stored_relative_path IS NULL)
            ),
            UNIQUE (batch_id, source_entry_index)
        );
        """,
        """
        CREATE INDEX IF NOT EXISTS archive_attachment_batches_by_import
            ON archive_attachment_batches(import_id, observed_at);
        """,
        """
        CREATE INDEX IF NOT EXISTS archive_attachments_by_batch
            ON archive_attachments(batch_id, source_entry_index);
        """,
    ]

    /// B5.2 operator-confirmed display labels. This is presentation metadata,
    /// not conversation identity and not an Archive ↔ Visual link.
    private static let schemaV5Labels: [String] = [
        """
        CREATE TABLE IF NOT EXISTS archive_conversation_labels (
            archive_conversation_id INTEGER PRIMARY KEY
                REFERENCES archive_conversations(id) ON DELETE CASCADE,
            display_name TEXT NOT NULL
                CHECK (length(trim(display_name)) BETWEEN 1 AND 120),
            basis TEXT NOT NULL DEFAULT 'operator'
                CHECK (basis = 'operator'),
            updated_at REAL NOT NULL
        );
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

    /// Every conversation with the number of messages the store still holds
    /// for it, newest activity first.
    ///
    /// One grouped statement rather than a count per conversation, because the
    /// ledger refreshes on the capture cadence. Read-only: it adds no table,
    /// no column and no schema version.
    func conversationSummaries() throws -> [CapturedConversationSummary] {
        try query(
            """
            SELECT c.id, c.title, c.first_seen_at, c.last_seen_at, COUNT(m.id)
            FROM conversations AS c
            LEFT JOIN messages AS m ON m.conversation_id = c.id
            GROUP BY c.id
            ORDER BY c.last_seen_at DESC, c.id DESC;
            """
        ) { statement in
            CapturedConversationSummary(
                id: sqlite3_column_int64(statement, 0),
                title: Self.string(statement, 1) ?? "",
                retainedMessageCount: Int(sqlite3_column_int64(statement, 4)),
                firstCapturedAt: Date(timeIntervalSince1970: sqlite3_column_double(statement, 2)),
                lastCapturedAt: Date(timeIntervalSince1970: sqlite3_column_double(statement, 3))
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

    /// Nil means the conversation is gone; an empty array means it exists but
    /// currently has no retained messages. Both reads are on this store actor.
    func recentMessagesIfConversationExists(id: Int64, limit: Int) throws -> [PersistedMessage]? {
        let exists = try query(
            "SELECT 1 FROM conversations WHERE id = ? LIMIT 1;",
            bind: { sqlite3_bind_int64($0, 1, id) },
            row: { _ in true }
        ).first != nil
        guard exists else { return nil }
        return try recentMessages(inConversation: id, limit: limit)
    }

    /// Search navigation rechecks the exact canonical row and its owner in one
    /// store-actor operation. The ordinary Chats read remains newest-100.
    func visualHitWindow(
        conversationID: Int64, messageID: Int64
    ) throws -> SearchHitWindow<PersistedMessage> {
        guard let recent = try recentMessagesIfConversationExists(id: conversationID, limit: 100)
        else { return .contextUnavailable }
        guard let hit = try persistedMessage(id: messageID), hit.conversationID == conversationID
        else { return .hitUnavailable }
        if recent.contains(where: { $0.id == messageID }) { return .ready(recent) }

        let before = try query(
            """
            SELECT id, conversation_id, sequence, sender, ownership, visible_time,
                   text, kind, confidence, first_observed_at
            FROM messages WHERE conversation_id = ?
              AND (sequence < ? OR (sequence = ? AND id <= ?))
            ORDER BY sequence DESC, id DESC LIMIT 50;
            """,
            bind: {
                sqlite3_bind_int64($0, 1, conversationID)
                sqlite3_bind_int64($0, 2, hit.sequence)
                sqlite3_bind_int64($0, 3, hit.sequence)
                sqlite3_bind_int64($0, 4, messageID)
            }, row: Self.persistedMessage
        ).reversed()
        let after = try query(
            """
            SELECT id, conversation_id, sequence, sender, ownership, visible_time,
                   text, kind, confidence, first_observed_at
            FROM messages WHERE conversation_id = ?
              AND (sequence > ? OR (sequence = ? AND id > ?))
            ORDER BY sequence ASC, id ASC LIMIT 50;
            """,
            bind: {
                sqlite3_bind_int64($0, 1, conversationID)
                sqlite3_bind_int64($0, 2, hit.sequence)
                sqlite3_bind_int64($0, 3, hit.sequence)
                sqlite3_bind_int64($0, 4, messageID)
            }, row: Self.persistedMessage
        )
        let window = Array(before) + after
        return window.contains(where: { $0.id == messageID }) ? .ready(window) : .hitUnavailable
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

    /// Read-only B3 summary of every persisted archive import, newest first.
    /// Internal source keys are reduced to one boolean and never leave storage.
    func archiveImportSummaries() throws -> [ArchiveEvidenceImportSummary] {
        try query(
            """
            SELECT i.id, i.transcript_shape, i.imported_at,
                   CASE i.transcript_shape
                     WHEN 'attributed' THEN (SELECT COUNT(*) FROM archive_attributed_records a WHERE a.import_id = i.id)
                     ELSE (SELECT COUNT(*) FROM archive_unattributed_records u WHERE u.import_id = i.id)
                   END,
                   (SELECT MIN(sent_at) FROM archive_attributed_records a WHERE a.import_id = i.id),
                   (SELECT MAX(sent_at) FROM archive_attributed_records a WHERE a.import_id = i.id),
                   c.source_conversation_key,
                   c.id,
                   l.visual_conversation_id,
                   v.title,
                   l.basis,
                   l.asserted_at,
                   (SELECT COUNT(*) FROM archive_attachment_batches b WHERE b.import_id = i.id),
                   (SELECT COUNT(*) FROM archive_attachments a
                      JOIN archive_attachment_batches b ON b.id = a.batch_id
                     WHERE b.import_id = i.id),
                   (SELECT COUNT(*) FROM archive_attachments a
                      JOIN archive_attachment_batches b ON b.id = a.batch_id
                     WHERE b.import_id = i.id AND a.storage_state = 'materialized'),
                   (SELECT display_name
                      FROM archive_conversation_labels n
                     WHERE n.archive_conversation_id = c.id)
            FROM archive_imports i
            JOIN archive_conversations c ON c.id = i.archive_conversation_id
            LEFT JOIN archive_conversation_links l ON l.archive_conversation_id = c.id
            LEFT JOIN conversations v ON v.id = l.visual_conversation_id
            ORDER BY i.imported_at DESC, i.id DESC;
            """
        ) { statement in
            let key = Self.string(statement, 6) ?? ""
            let link: ArchiveConversationLink?
            if sqlite3_column_type(statement, 8) == SQLITE_NULL {
                link = nil
            } else {
                link = ArchiveConversationLink(
                    archiveConversationID: sqlite3_column_int64(statement, 7),
                    visualConversationID: sqlite3_column_int64(statement, 8),
                    visualConversationTitle: Self.string(statement, 9) ?? "",
                    basis: ArchiveConversationLinkBasis(
                        rawValue: Self.string(statement, 10) ?? ""
                    ) ?? .operator,
                    assertedAt: Date(timeIntervalSince1970: sqlite3_column_double(statement, 11))
                )
            }
            return ArchiveEvidenceImportSummary(
                id: sqlite3_column_int64(statement, 0),
                displayName: Self.string(statement, 15),
                shape: ArchiveEvidenceShape(rawValue: Self.string(statement, 1) ?? "") ?? .unattributed,
                importedAt: Date(timeIntervalSince1970: sqlite3_column_double(statement, 2)),
                recordCount: Int(sqlite3_column_int64(statement, 3)),
                firstSentAt: Self.optionalDate(statement, 4),
                lastSentAt: Self.optionalDate(statement, 5),
                isAnonymous: key.hasPrefix("native-anonymous-v1:"),
                link: link,
                attachmentBatchCount: Int(sqlite3_column_int64(statement, 12)),
                attachmentCount: Int(sqlite3_column_int64(statement, 13)),
                materializedAttachmentCount: Int(sqlite3_column_int64(statement, 14))
            )
        }
    }


    @discardableResult
    func setArchiveImportDisplayName(
        importID: Int64,
        displayName: String,
        updatedAt: Date = Date()
    ) throws -> String {
        let normalized = displayName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalized.isEmpty,
              normalized.count <= 120,
              normalized.rangeOfCharacter(from: .newlines) == nil,
              normalized.rangeOfCharacter(from: .controlCharacters) == nil
        else {
            throw ArchiveConversationDisplayNameError.invalidName
        }

        guard let archiveConversationID = try query(
            "SELECT archive_conversation_id FROM archive_imports WHERE id = ?;",
            bind: { sqlite3_bind_int64($0, 1, importID) },
            row: { sqlite3_column_int64($0, 0) }
        ).first else {
            throw ArchiveConversationDisplayNameError.importUnknown
        }

        try run(
            """
            INSERT INTO archive_conversation_labels(
                archive_conversation_id, display_name, basis, updated_at
            ) VALUES (?, ?, 'operator', ?)
            ON CONFLICT(archive_conversation_id) DO UPDATE SET
                display_name = excluded.display_name,
                basis = 'operator',
                updated_at = excluded.updated_at;
            """
        ) { statement in
            sqlite3_bind_int64(statement, 1, archiveConversationID)
            Self.bind(statement, 2, normalized)
            sqlite3_bind_double(statement, 3, updatedAt.timeIntervalSince1970)
        }
        return normalized
    }

    func clearArchiveImportDisplayName(importID: Int64) throws {
        guard let archiveConversationID = try query(
            "SELECT archive_conversation_id FROM archive_imports WHERE id = ?;",
            bind: { sqlite3_bind_int64($0, 1, importID) },
            row: { sqlite3_column_int64($0, 0) }
        ).first else {
            throw ArchiveConversationDisplayNameError.importUnknown
        }
        try run(
            "DELETE FROM archive_conversation_labels WHERE archive_conversation_id = ?;"
        ) { sqlite3_bind_int64($0, 1, archiveConversationID) }
    }

    @discardableResult
    func linkArchiveImport(
        importID: Int64,
        toVisualConversationID visualConversationID: Int64,
        basis: ArchiveConversationLinkBasis,
        assertedAt: Date = Date()
    ) throws -> ArchiveConversationLink {
        guard let archiveConversationID = try query(
            "SELECT archive_conversation_id FROM archive_imports WHERE id = ?;",
            bind: { sqlite3_bind_int64($0, 1, importID) },
            row: { sqlite3_column_int64($0, 0) }
        ).first else {
            throw ArchiveConversationLinkError.importUnknown
        }

        guard let visualTitle = try query(
            "SELECT title FROM conversations WHERE id = ?;",
            bind: { sqlite3_bind_int64($0, 1, visualConversationID) },
            row: { Self.string($0, 0) ?? "" }
        ).first else {
            throw ArchiveConversationLinkError.visualConversationUnknown
        }

        if let existing = try query(
            """
            SELECT visual_conversation_id, basis, asserted_at
            FROM archive_conversation_links
            WHERE archive_conversation_id = ?;
            """,
            bind: { sqlite3_bind_int64($0, 1, archiveConversationID) },
            row: {
                (
                    sqlite3_column_int64($0, 0),
                    ArchiveConversationLinkBasis(
                        rawValue: Self.string($0, 1) ?? ""
                    ) ?? .operator,
                    Date(timeIntervalSince1970: sqlite3_column_double($0, 2))
                )
            }
        ).first {
            guard existing.0 == visualConversationID else {
                throw ArchiveConversationLinkError.conflict(
                    existingVisualConversationID: existing.0
                )
            }
            return ArchiveConversationLink(
                archiveConversationID: archiveConversationID,
                visualConversationID: existing.0,
                visualConversationTitle: visualTitle,
                basis: existing.1,
                assertedAt: existing.2
            )
        }

        try run(
            """
            INSERT INTO archive_conversation_links(
                archive_conversation_id, visual_conversation_id, basis, asserted_at
            ) VALUES (?, ?, ?, ?);
            """
        ) { statement in
            sqlite3_bind_int64(statement, 1, archiveConversationID)
            sqlite3_bind_int64(statement, 2, visualConversationID)
            Self.bind(statement, 3, basis.rawValue)
            sqlite3_bind_double(statement, 4, assertedAt.timeIntervalSince1970)
        }

        return ArchiveConversationLink(
            archiveConversationID: archiveConversationID,
            visualConversationID: visualConversationID,
            visualConversationTitle: visualTitle,
            basis: basis,
            assertedAt: assertedAt
        )
    }

    func unlinkArchiveImport(importID: Int64) throws {
        guard let archiveConversationID = try query(
            "SELECT archive_conversation_id FROM archive_imports WHERE id = ?;",
            bind: { sqlite3_bind_int64($0, 1, importID) },
            row: { sqlite3_column_int64($0, 0) }
        ).first else {
            throw ArchiveConversationLinkError.importUnknown
        }
        try run(
            "DELETE FROM archive_conversation_links WHERE archive_conversation_id = ?;"
        ) { sqlite3_bind_int64($0, 1, archiveConversationID) }
    }

    /// Ordered rows for one import. The result is bounded so a large export
    /// cannot accidentally materialize unbounded UI state.
    func archiveRecords(importID: Int64, limit: Int = 500) throws -> [ArchiveEvidenceRecord] {
        let boundedLimit = max(1, min(limit, 2_000))
        return try query(
            """
            SELECT i.id, i.imported_at, i.transcript_shape,
                   a.sequence, a.sender, a.sent_at, a.sent_at_text, a.text
            FROM archive_imports i
            JOIN archive_attributed_records a ON a.import_id = i.id
            WHERE i.id = ?
            UNION ALL
            SELECT i.id, i.imported_at, i.transcript_shape,
                   u.sequence, NULL, NULL, NULL, u.record_text
            FROM archive_imports i
            JOIN archive_unattributed_records u ON u.import_id = i.id
            WHERE i.id = ?
            ORDER BY sequence ASC
            LIMIT ?;
            """,
            bind: { statement in
                sqlite3_bind_int64(statement, 1, importID)
                sqlite3_bind_int64(statement, 2, importID)
                sqlite3_bind_int64(statement, 3, Int64(boundedLimit))
            },
            row: Self.archiveEvidenceRecord
        )
    }

    func archiveHitWindow(
        importID: Int64,
        sequence: Int,
        provenance: LocalSearchResult.Provenance
    ) throws -> SearchHitWindow<ArchiveEvidenceRecord> {
        guard let shape = try importShape(importID: importID) else { return .contextUnavailable }
        let expected: ArchiveEvidenceShape
        switch provenance {
        case .archiveAttributed: expected = .attributed
        case .archiveUnattributed: expected = .unattributed
        case .visualCaptured: return .hitUnavailable
        }
        guard shape == expected,
              let hit = try archiveRecord(importID: importID, sequence: sequence),
              hit.shape == expected
        else { return .hitUnavailable }

        let ordinary = try archiveRecords(importID: importID)
        if ordinary.contains(where: { $0.sequence == sequence }) { return .ready(ordinary) }

        // Both fragments are fixed SQL chosen from the import's canonical
        // shape. Only the import and sequence values are bound from the hit.
        let table = shape == .attributed
            ? "archive_attributed_records" : "archive_unattributed_records"
        let columns = shape == .attributed
            ? "r.sender, r.sent_at, r.sent_at_text, r.text"
            : "NULL, NULL, NULL, r.record_text"
        func side(_ comparison: String, _ order: String) throws -> [ArchiveEvidenceRecord] {
            try query(
                """
                SELECT i.id, i.imported_at, i.transcript_shape,
                       r.sequence, \(columns)
                FROM archive_imports i JOIN \(table) r ON r.import_id = i.id
                WHERE i.id = ? AND r.sequence \(comparison) ?
                ORDER BY r.sequence \(order) LIMIT 250;
                """,
                bind: {
                    sqlite3_bind_int64($0, 1, importID)
                    sqlite3_bind_int64($0, 2, Int64(sequence))
                }, row: Self.archiveEvidenceRecord
            )
        }
        let window = Array(try side("<=", "DESC").reversed()) + (try side(">", "ASC"))
        return window.contains(where: { $0.id == hit.id }) ? .ready(window) : .hitUnavailable
    }

    /// Literal substring search across archive evidence. This is intentionally
    /// not FTS: B3 adds a small read surface without changing schema v2.
    func searchArchiveEvidence(_ queryText: String, limit: Int = 100) throws -> [ArchiveEvidenceRecord] {
        let needle = queryText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !needle.isEmpty else { return [] }
        let boundedLimit = max(1, min(limit, 500))
        return try query(
            """
            SELECT i.id, i.imported_at, i.transcript_shape,
                   a.sequence, a.sender, a.sent_at, a.sent_at_text, a.text
            FROM archive_imports i
            JOIN archive_attributed_records a ON a.import_id = i.id
            WHERE instr(a.sender, ?) > 0
               OR instr(a.sent_at_text, ?) > 0
               OR instr(a.text, ?) > 0
            UNION ALL
            SELECT i.id, i.imported_at, i.transcript_shape,
                   u.sequence, NULL, NULL, NULL, u.record_text
            FROM archive_imports i
            JOIN archive_unattributed_records u ON u.import_id = i.id
            WHERE instr(u.record_text, ?) > 0
            ORDER BY 2 DESC, 1 DESC, 4 ASC
            LIMIT ?;
            """,
            bind: { statement in
                Self.bind(statement, 1, needle)
                Self.bind(statement, 2, needle)
                Self.bind(statement, 3, needle)
                Self.bind(statement, 4, needle)
                sqlite3_bind_int64(statement, 5, Int64(boundedLimit))
            },
            row: Self.archiveEvidenceRecord
        )
    }

    private static func archiveEvidenceRecord(_ statement: OpaquePointer) -> ArchiveEvidenceRecord {
        ArchiveEvidenceRecord(
            importID: sqlite3_column_int64(statement, 0),
            importedAt: Date(timeIntervalSince1970: sqlite3_column_double(statement, 1)),
            shape: ArchiveEvidenceShape(rawValue: string(statement, 2) ?? "") ?? .unattributed,
            sequence: Int(sqlite3_column_int64(statement, 3)),
            sender: string(statement, 4),
            sentAt: optionalDate(statement, 5),
            sentAtText: string(statement, 6),
            text: string(statement, 7) ?? ""
        )
    }

    private static func optionalDate(_ statement: OpaquePointer, _ index: Int32) -> Date? {
        guard sqlite3_column_type(statement, index) != SQLITE_NULL else { return nil }
        return Date(timeIntervalSince1970: sqlite3_column_double(statement, index))
    }

    // MARK: - B6 search-index read surface

    /// The single visual message, by identity, for re-validating a search hit.
    func persistedMessage(id: Int64) throws -> PersistedMessage? {
        try query(
            """
            SELECT id, conversation_id, sequence, sender, ownership, visible_time,
                   text, kind, confidence, first_observed_at
            FROM messages WHERE id = ? LIMIT 1;
            """,
            bind: { sqlite3_bind_int64($0, 1, id) },
            row: Self.persistedMessage
        ).first
    }

    /// The presentation title of the conversation a message belongs to, read at
    /// result time. `nil` when the message is gone, which is exactly the case a
    /// stale index hit must be dropped for.
    func conversationTitle(forMessageID messageID: Int64) throws -> String? {
        try query(
            """
            SELECT c.title FROM messages m
            JOIN conversations c ON c.id = m.conversation_id
            WHERE m.id = ? LIMIT 1;
            """,
            bind: { sqlite3_bind_int64($0, 1, messageID) },
            row: { Self.string($0, 0) }
        ).first ?? nil
    }

    func archiveRecord(importID: Int64, sequence: Int) throws -> ArchiveEvidenceRecord? {
        let shape = try importShape(importID: importID)
        switch shape {
        case .attributed:
            return try query(
                """
                SELECT i.id, i.imported_at, i.transcript_shape,
                       a.sequence, a.sender, a.sent_at, a.sent_at_text, a.text
                FROM archive_imports i
                JOIN archive_attributed_records a ON a.import_id = i.id
                WHERE a.import_id = ? AND a.sequence = ? LIMIT 1;
                """,
                bind: {
                    sqlite3_bind_int64($0, 1, importID)
                    sqlite3_bind_int64($0, 2, Int64(sequence))
                },
                row: Self.archiveEvidenceRecord
            ).first
        case .unattributed:
            return try query(
                """
                SELECT i.id, i.imported_at, i.transcript_shape,
                       u.sequence, NULL, NULL, NULL, u.record_text
                FROM archive_imports i
                JOIN archive_unattributed_records u ON u.import_id = i.id
                WHERE u.import_id = ? AND u.sequence = ? LIMIT 1;
                """,
                bind: {
                    sqlite3_bind_int64($0, 1, importID)
                    sqlite3_bind_int64($0, 2, Int64(sequence))
                },
                row: Self.archiveEvidenceRecord
            ).first
        case nil:
            return nil
        }
    }

    /// One import's presentation state: operator display label and link state.
    /// `nil` once the import is gone, which drops any stale search hit.
    func archiveImportSummary(id: Int64) throws -> ArchiveEvidenceImportSummary? {
        try archiveImportSummaries().first { $0.id == id }
    }

    /// Keyset page of visual messages, oldest id first, for the index build.
    /// `afterMessageID: 0` starts at the beginning. Read-only and bounded: the
    /// build never holds the whole corpus as one Swift array.
    func searchableVisualMessages(
        afterMessageID: Int64,
        limit: Int
    ) throws -> [SearchableVisualMessage] {
        let boundedLimit = max(1, min(limit, 2_000))
        return try query(
            """
            SELECT id, sender, text FROM messages
            WHERE id > ? ORDER BY id ASC LIMIT ?;
            """,
            bind: {
                sqlite3_bind_int64($0, 1, afterMessageID)
                sqlite3_bind_int64($0, 2, Int64(boundedLimit))
            },
            row: { statement in
                SearchableVisualMessage(
                    messageID: sqlite3_column_int64(statement, 0),
                    sender: Self.string(statement, 1),
                    text: Self.string(statement, 2)
                )
            }
        )
    }

    /// Bounded literal fallback for one and two character queries: the same
    /// sender-or-text test trigram cannot express, run as parameterized `instr()`
    /// against the canonical table. Read-only, local, and capped.
    func searchableVisualMessages(
        containing needle: String,
        limit: Int
    ) throws -> [SearchableVisualMessage] {
        let trimmed = needle.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return [] }
        let boundedLimit = max(1, min(limit, 100))
        return try query(
            """
            SELECT id, sender, text FROM messages
            WHERE instr(COALESCE(sender, ''), ?) > 0
               OR instr(COALESCE(text, ''), ?) > 0
            ORDER BY id ASC LIMIT ?;
            """,
            bind: {
                Self.bind($0, 1, trimmed)
                Self.bind($0, 2, trimmed)
                sqlite3_bind_int64($0, 3, Int64(boundedLimit))
            },
            row: { statement in
                SearchableVisualMessage(
                    messageID: sqlite3_column_int64(statement, 0),
                    sender: Self.string(statement, 1),
                    text: Self.string(statement, 2)
                )
            }
        )
    }

    func searchableArchiveAttributedRecords(
        after cursor: (importID: Int64, sequence: Int),
        limit: Int
    ) throws -> [SearchableArchiveRecord] {
        let boundedLimit = max(1, min(limit, 2_000))
        return try query(
            """
            SELECT import_id, sequence, sender, text FROM archive_attributed_records
            WHERE import_id > ? OR (import_id = ? AND sequence > ?)
            ORDER BY import_id ASC, sequence ASC LIMIT ?;
            """,
            bind: {
                sqlite3_bind_int64($0, 1, cursor.importID)
                sqlite3_bind_int64($0, 2, cursor.importID)
                sqlite3_bind_int64($0, 3, Int64(cursor.sequence))
                sqlite3_bind_int64($0, 4, Int64(boundedLimit))
            },
            row: Self.searchableArchiveRecord
        )
    }

    func searchableArchiveUnattributedRecords(
        after cursor: (importID: Int64, sequence: Int),
        limit: Int
    ) throws -> [SearchableArchiveRecord] {
        let boundedLimit = max(1, min(limit, 2_000))
        return try query(
            """
            -- The NULL sender keeps one row shape across both archive shapes, so
            -- the shared mapper below reads text from the same column always.
            SELECT import_id, sequence, NULL, record_text
            FROM archive_unattributed_records
            WHERE import_id > ? OR (import_id = ? AND sequence > ?)
            ORDER BY import_id ASC, sequence ASC LIMIT ?;
            """,
            bind: {
                sqlite3_bind_int64($0, 1, cursor.importID)
                sqlite3_bind_int64($0, 2, cursor.importID)
                sqlite3_bind_int64($0, 3, Int64(cursor.sequence))
                sqlite3_bind_int64($0, 4, Int64(boundedLimit))
            },
            row: Self.searchableArchiveRecord
        )
    }

    /// The bounded literal fallback for archive records, in both shapes.
    func searchableArchiveAttributedRecords(
        containing needle: String,
        limit: Int
    ) throws -> [SearchableArchiveRecord] {
        try archiveLiteralScan(
            table: "archive_attributed_records",
            textColumn: "text",
            senderColumn: "sender",
            needle: needle,
            limit: limit
        )
    }

    func searchableArchiveUnattributedRecords(
        containing needle: String,
        limit: Int
    ) throws -> [SearchableArchiveRecord] {
        try archiveLiteralScan(
            table: "archive_unattributed_records",
            textColumn: "record_text",
            senderColumn: nil,
            needle: needle,
            limit: limit
        )
    }

    /// `table` and `column` are literals from this file, never caller input.
    private func archiveLiteralScan(
        table: String,
        textColumn: String,
        senderColumn: String?,
        needle: String,
        limit: Int
    ) throws -> [SearchableArchiveRecord] {
        let trimmed = needle.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return [] }
        let boundedLimit = max(1, min(limit, 100))
        let senderTest = senderColumn.map { "instr(COALESCE(\($0), ''), ?) > 0 OR " } ?? ""
        // Always four columns, so the shared mapper above reads the same
        // positions for both shapes. Shape B has no sender column at all, so
        // its slot is filled with an explicit NULL.
        let senderProjection = senderColumn.map { "\($0), " } ?? "NULL, "
        return try query(
            """
            SELECT import_id, sequence, \(senderProjection)
                   \(textColumn)
            FROM \(table)
            WHERE \(senderTest)instr(\(textColumn), ?) > 0
            ORDER BY import_id ASC, sequence ASC LIMIT ?;
            """,
            bind: {
                var index: Int32 = 1
                if senderColumn != nil {
                    Self.bind($0, index, trimmed)
                    index += 1
                }
                Self.bind($0, index, trimmed)
                index += 1
                sqlite3_bind_int64($0, index, Int64(boundedLimit))
            },
            row: Self.searchableArchiveRecord
        )
    }

    private func importShape(importID: Int64) throws -> ArchiveEvidenceShape? {
        try query(
            "SELECT transcript_shape FROM archive_imports WHERE id = ? LIMIT 1;",
            bind: { sqlite3_bind_int64($0, 1, importID) },
            row: { ArchiveEvidenceShape(rawValue: Self.string($0, 0) ?? "") }
        ).first ?? nil
    }

    private static func persistedMessage(_ statement: OpaquePointer) -> PersistedMessage {
        PersistedMessage(
            id: sqlite3_column_int64(statement, 0),
            conversationID: sqlite3_column_int64(statement, 1),
            sequence: sqlite3_column_int64(statement, 2),
            sender: string(statement, 3),
            ownership: MessageOwnership(rawValue: string(statement, 4) ?? "") ?? .unknown,
            visibleTime: string(statement, 5),
            text: string(statement, 6),
            kind: VisibleMessageKind(rawValue: string(statement, 7) ?? "") ?? .unknown,
            confidence: sqlite3_column_double(statement, 8),
            firstObservedAt: Date(timeIntervalSince1970: sqlite3_column_double(statement, 9))
        )
    }

    /// Reads either shape into one row shape: an unattributed record simply
    /// has no sender, which is the honest description of what the archive kept.
    private static func searchableArchiveRecord(
        _ statement: OpaquePointer
    ) -> SearchableArchiveRecord {
        // Both shapes select `import_id, sequence, sender, text` -- the
        // unattributed shape fills sender with an explicit NULL. So sender is
        // read as nullable and text always comes from column 3. Inferring the
        // layout from whether a sender is present is what broke it: an
        // unattributed row's NULL sender was mistaken for a missing column and
        // text was then read from the NULL column instead.
        return SearchableArchiveRecord(
            importID: sqlite3_column_int64(statement, 0),
            sequence: Int(sqlite3_column_int64(statement, 1)),
            sender: string(statement, 2),
            text: string(statement, 3) ?? ""
        )
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
            Self.bind(statement, 1, title)
            sqlite3_bind_double(statement, 2, stamp)
            sqlite3_bind_double(statement, 3, stamp)
        }
        let ids = try query(
            "SELECT id FROM conversations WHERE title = ?;",
            bind: { statement in
                Self.bind(statement, 1, title)
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
            Self.bind(statement, 4, message.ownership.rawValue)
            Self.bind(statement, 5, message.visibleTime)
            Self.bind(statement, 6, message.text)
            Self.bind(statement, 7, message.kind.rawValue)
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
        try run("DELETE FROM archive_attachments;")
        try run("DELETE FROM archive_attachment_batches;")
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
    func applyRetention(
        _ policy: RetentionPolicy,
        now: Date = Date(),
        failBetweenArchiveRetentionStepsForTesting: Bool = false
    ) throws -> Int {
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
        let archiveRemoved = try applyArchiveRetention(
            cutoff: cutoff, failBetweenStepsForTesting: failBetweenArchiveRetentionStepsForTesting
        )
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
    private func applyArchiveRetention(
        cutoff: TimeInterval, failBetweenStepsForTesting: Bool = false
    ) throws -> Int {
        let before = try archiveImportCount()
        // One unit, not two statements. Expiring the imports and dropping the
        // conversations they justified must succeed or fail together: stopping
        // in between leaves zero imports and a surviving
        // `source_conversation_key`, which is chat identity outliving every
        // reason to hold it -- the one outcome retention must never produce.
        try Self.exec(handle, "BEGIN IMMEDIATE;")
        do {
            try run("DELETE FROM archive_imports WHERE imported_at < ?;") { statement in
                sqlite3_bind_double(statement, 1, cutoff)
            }
            if failBetweenStepsForTesting {
                throw MessageStoreError.statementFailed(status: SQLITE_ERROR)
            }
            // Records cascade with their import.
            try run("""
                DELETE FROM archive_conversations WHERE id NOT IN (
                    SELECT DISTINCT archive_conversation_id FROM archive_imports
                );
                """)
            try Self.exec(handle, "COMMIT;")
        } catch {
            try? Self.exec(handle, "ROLLBACK;")
            throw error
        }
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



    // MARK: - B5 attachment evidence

    func attachmentBatchResult(
        importID: Int64,
        fingerprint: String
    ) throws -> ArchiveAttachmentBatchPersistenceResult? {
        let rows = try query(
            """
            SELECT id, attachment_count, materialized_count
            FROM archive_attachment_batches
            WHERE import_id = ? AND batch_fingerprint = ?;
            """,
            bind: {
                sqlite3_bind_int64($0, 1, importID)
                Self.bind($0, 2, fingerprint)
            },
            row: {
                (
                    sqlite3_column_int64($0, 0),
                    Int(sqlite3_column_int64($0, 1)),
                    Int(sqlite3_column_int64($0, 2))
                )
            }
        )
        guard let row = rows.first else { return nil }
        return .alreadyPersisted(
            batchID: row.0,
            attachmentCount: row.1,
            materializedCount: row.2
        )
    }

    @discardableResult
    func persistArchiveAttachmentBatch(
        importID: Int64,
        fingerprint: String,
        observedAt: Date,
        manifests: [ArchiveAttachmentManifest]
    ) throws -> ArchiveAttachmentBatchPersistenceResult {
        guard !manifests.isEmpty else {
            throw ArchiveAttachmentPersistenceError.manifestPersistenceFailed
        }
        if let existing = try attachmentBatchResult(
            importID: importID, fingerprint: fingerprint
        ) {
            return existing
        }

        let materializedCount = manifests.filter { $0.storageState == .materialized }.count
        try Self.exec(handle, "BEGIN IMMEDIATE;")
        do {
            try run("""
                INSERT INTO archive_attachment_batches(
                    import_id, batch_fingerprint, observed_at,
                    attachment_count, materialized_count
                ) VALUES (?, ?, ?, ?, ?);
                """) { statement in
                    sqlite3_bind_int64(statement, 1, importID)
                    Self.bind(statement, 2, fingerprint)
                    sqlite3_bind_double(statement, 3, observedAt.timeIntervalSince1970)
                    sqlite3_bind_int64(statement, 4, Int64(manifests.count))
                    sqlite3_bind_int64(statement, 5, Int64(materializedCount))
                }
            let batchID = sqlite3_last_insert_rowid(handle)

            var inserted = 0
            for manifest in manifests {
                try run("""
                    INSERT INTO archive_attachments(
                        batch_id, source_entry_index, path_extension, byte_count,
                        crc32, media_kind, storage_state, content_sha256,
                        stored_relative_path, relation_scope
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'import_only');
                    """) { statement in
                        sqlite3_bind_int64(statement, 1, batchID)
                        sqlite3_bind_int64(statement, 2, Int64(manifest.sourceEntryIndex))
                        Self.bind(statement, 3, manifest.pathExtension)
                        sqlite3_bind_int64(statement, 4, Int64(manifest.byteCount))
                        sqlite3_bind_int64(statement, 5, Int64(manifest.crc32))
                        Self.bind(statement, 6, manifest.kind?.rawValue)
                        Self.bind(statement, 7, manifest.storageState.rawValue)
                        Self.bind(statement, 8, manifest.contentSHA256)
                        Self.bind(statement, 9, manifest.storedRelativePath)
                    }
                inserted += 1
            }
            guard inserted == manifests.count else {
                throw ArchiveAttachmentPersistenceError.manifestPersistenceFailed
            }
            try Self.exec(handle, "COMMIT;")
            return .inserted(
                batchID: batchID,
                attachmentCount: inserted,
                materializedCount: materializedCount
            )
        } catch {
            try? Self.exec(handle, "ROLLBACK;")
            throw error
        }
    }

    func archiveAttachmentBatches(
        importID: Int64
    ) throws -> [ArchiveEvidenceAttachmentBatch] {
        let batches = try query(
            """
            SELECT id, observed_at, attachment_count, materialized_count
            FROM archive_attachment_batches
            WHERE import_id = ?
            ORDER BY observed_at DESC, id DESC;
            """,
            bind: { sqlite3_bind_int64($0, 1, importID) },
            row: {
                (
                    sqlite3_column_int64($0, 0),
                    Date(timeIntervalSince1970: sqlite3_column_double($0, 1)),
                    Int(sqlite3_column_int64($0, 2)),
                    Int(sqlite3_column_int64($0, 3))
                )
            }
        )

        var result: [ArchiveEvidenceAttachmentBatch] = []
        result.reserveCapacity(batches.count)
        for batch in batches {
            let rawAttachments = try query(
                """
                SELECT id, source_entry_index, path_extension, byte_count,
                       media_kind, storage_state
                FROM archive_attachments
                WHERE batch_id = ?
                ORDER BY source_entry_index ASC, id ASC;
                """,
                bind: { sqlite3_bind_int64($0, 1, batch.0) },
                row: {
                    (
                        sqlite3_column_int64($0, 0),
                        Int(sqlite3_column_int64($0, 1)),
                        Self.string($0, 2) ?? "",
                        Int(sqlite3_column_int64($0, 3)),
                        Self.string($0, 4),
                        Self.string($0, 5) ?? ""
                    )
                }
            )
            var attachments: [ArchiveEvidenceAttachment] = []
            attachments.reserveCapacity(rawAttachments.count)
            for raw in rawAttachments {
                guard let state = ArchiveAttachmentStorageState(rawValue: raw.5) else {
                    throw MessageStoreError.statementFailed(status: SQLITE_CORRUPT)
                }
                let kind: WeChatNativeAttachmentKind?
                if let value = raw.4 {
                    guard let parsed = WeChatNativeAttachmentKind(rawValue: value) else {
                        throw MessageStoreError.statementFailed(status: SQLITE_CORRUPT)
                    }
                    kind = parsed
                } else {
                    kind = nil
                }
                attachments.append(ArchiveEvidenceAttachment(
                    id: raw.0,
                    sourceEntryIndex: raw.1,
                    pathExtension: raw.2,
                    byteCount: raw.3,
                    kind: kind,
                    storageState: state
                ))
            }
            guard attachments.count == batch.2 else {
                throw MessageStoreError.statementFailed(status: SQLITE_CORRUPT)
            }
            result.append(ArchiveEvidenceAttachmentBatch(
                id: batch.0,
                observedAt: batch.1,
                attachmentCount: batch.2,
                materializedCount: batch.3,
                attachments: attachments
            ))
        }
        return result
    }

    func archiveAttachmentBatchKeys() throws -> [Int64: Set<String>] {
        let rows = try query(
            """
            SELECT import_id, batch_fingerprint
            FROM archive_attachment_batches
            ORDER BY import_id, id;
            """
        ) {
            (sqlite3_column_int64($0, 0), Self.string($0, 1) ?? "")
        }
        var result: [Int64: Set<String>] = [:]
        for (importID, fingerprint) in rows {
            result[importID, default: []].insert(fingerprint)
        }
        return result
    }

    /// B5.1: the stored relative path for one materialized attachment.
    ///
    /// This is the ONLY place the relative path leaves SQLite, and it is read
    /// on demand for one attachment the user explicitly asked to open. Nothing
    /// here is cached into the read model, so a stored path or content hash can
    /// never appear in the Chats UI or in any aggregate.
    func archiveAttachmentRelativePath(attachmentID: Int64) throws -> String? {
        try query(
            """
            SELECT stored_relative_path
            FROM archive_attachments
            WHERE id = ? AND storage_state = 'materialized';
            """,
            bind: { sqlite3_bind_int64($0, 1, attachmentID) },
            row: { Self.string($0, 0) }
        ).first ?? nil
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

    /// Reads back what was actually stored, so a round-trip test measures the
    /// database rather than the value the caller still holds in memory.
    func attributedRecordsForTesting(importID: Int64) throws -> [(Int, String, String, String)] {
        try query(
            """
            SELECT sequence, sender, sent_at_text, text FROM archive_attributed_records
            WHERE import_id = ? ORDER BY sequence;
            """,
            bind: { sqlite3_bind_int64($0, 1, importID) },
            row: {
                (Int(sqlite3_column_int64($0, 0)), Self.string($0, 1) ?? "",
                 Self.string($0, 2) ?? "", Self.string($0, 3) ?? "")
            }
        )
    }

    func unattributedRecordsForTesting(importID: Int64) throws -> [(Int, String)] {
        try query(
            """
            SELECT sequence, record_text FROM archive_unattributed_records
            WHERE import_id = ? ORDER BY sequence;
            """,
            bind: { sqlite3_bind_int64($0, 1, importID) },
            row: { (Int(sqlite3_column_int64($0, 0)), Self.string($0, 1) ?? "") }
        )
    }

    /// Byte length of a stored value as SQLite sees it -- the measurement that
    /// exposes a C-string truncation, which a Swift `String` comparison alone
    /// would not.
    func storedByteLengthForTesting(_ sql: String) throws -> Int {
        try query(sql) { Int(sqlite3_column_int64($0, 0)) }.first ?? -1
    }

    func sourceKeysForTesting() throws -> [String] {
        try query("SELECT source_conversation_key FROM archive_conversations ORDER BY id;") {
            Self.string($0, 0) ?? ""
        }
    }

    func linkCountForTesting() throws -> Int {
        try query("SELECT COUNT(*) FROM archive_conversation_links;") {
            Int(sqlite3_column_int64($0, 0))
        }.first ?? 0
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

    /// Binds text by **explicit byte count**, never `-1`.
    ///
    /// `-1` tells SQLite the value is NUL-terminated, so a string containing
    /// U+0000 is silently stored only up to the first one. Message text is
    /// arbitrary user-authored UTF-8 and the import fingerprint hashes all of
    /// it, so a truncating write would make the digest describe a value the
    /// database does not hold -- corruption that no fingerprint test could see.
    private static func bind(_ statement: OpaquePointer, _ index: Int32, _ value: String?) {
        guard let value else {
            sqlite3_bind_null(statement, index)
            return
        }
        var bytes = Array(value.utf8)
        // `transient` copies, so the array need not outlive this call. An empty
        // string must still bind a non-nil pointer, or SQLite treats it as NULL.
        sqlite3_bind_text(statement, index, &bytes, Int32(bytes.count), transient)
    }

    /// Reads text by its **actual byte count**.
    ///
    /// `String(cString:)` stops at the first NUL for the same reason, so a
    /// value written correctly would still come back truncated.
    private static func string(_ statement: OpaquePointer, _ index: Int32) -> String? {
        guard let raw = sqlite3_column_text(statement, index) else { return nil }
        let count = Int(sqlite3_column_bytes(statement, index))
        return String(decoding: UnsafeBufferPointer(start: raw, count: count), as: UTF8.self)
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
