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

    private static func migrate(_ handle: OpaquePointer) throws {
        try exec(handle, "PRAGMA journal_mode = WAL;")
        try exec(handle, "PRAGMA foreign_keys = ON;")
        try exec(handle, """
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL UNIQUE,
                first_seen_at REAL NOT NULL,
                last_seen_at REAL NOT NULL
            );
            """)
        try exec(handle, """
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
            """)
        // Ordering is by sequence, never by observation time: a backfilled
        // older message is observed later than the newer ones around it.
        try exec(handle, """
            CREATE INDEX IF NOT EXISTS messages_by_position
                ON messages(conversation_id, sequence);
            """)
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
        if removed > 0 {
            try Self.exec(handle, "PRAGMA wal_checkpoint(TRUNCATE);")
        }
        return removed
    }

    func totalMessageCount() throws -> Int {
        try query("SELECT COUNT(*) FROM messages;") { Int(sqlite3_column_int64($0, 0)) }
            .first ?? 0
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
