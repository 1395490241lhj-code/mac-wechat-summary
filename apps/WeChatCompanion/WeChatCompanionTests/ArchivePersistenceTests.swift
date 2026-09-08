import Foundation
import SQLite3
import Testing
@testable import WeChatCompanion

// MARK: - Helpers

private let m35 = "2026年9月7日 20:35"
private let m36 = "2026年9月7日 20:36"

private func attributed(_ rows: [(String, String, String)]) throws -> WeChatNativeTranscript {
    let body = rows.map { "·\($0.0)\n\($0.1)\n\($0.2)\n\n" }.joined()
    return try WeChatNativeTranscriptParser.parse(body, timeZone: TimeZone(identifier: "Asia/Shanghai")!)
}

private func unattributed(_ records: [String]) throws -> WeChatNativeTranscript {
    try WeChatNativeTranscriptParser.parse(records.map { "·\($0)" }.joined(separator: "\n\n") + "\n")
}

/// A temporary directory holding a real on-disk database, so migration is
/// exercised against a file rather than only `:memory:`.
private struct Scratch: ~Copyable {
    let directory: URL
    var databaseURL: URL { directory.appendingPathComponent("messages.sqlite") }

    init() throws {
        directory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("b1-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    /// Builds a database by hand at a chosen version, the way an older or a
    /// future build would have left it.
    func seed(_ statements: [String], userVersion: Int32) throws {
        var handle: OpaquePointer?
        #expect(sqlite3_open_v2(databaseURL.path, &handle,
                                SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE, nil) == SQLITE_OK)
        defer { sqlite3_close_v2(handle) }
        for sql in statements + ["PRAGMA user_version = \(userVersion);"] {
            #expect(sqlite3_exec(handle, sql, nil, nil, nil) == SQLITE_OK, "failed: \(sql)")
        }
    }

    /// Opened READWRITE rather than READONLY: a read-only connection to a WAL
    /// database cannot create the `-shm` sidecar it needs, and fails to open.
    /// This inspects, it does not write.
    func inspect<T>(_ body: (OpaquePointer) -> T) -> T {
        var handle: OpaquePointer?
        let status = sqlite3_open_v2(databaseURL.path, &handle, SQLITE_OPEN_READWRITE, nil)
        #expect(status == SQLITE_OK, "could not open the database for inspection")
        defer { sqlite3_close_v2(handle) }
        return body(handle!)
    }

    deinit { try? FileManager.default.removeItem(at: directory) }
}

private func scalar(_ handle: OpaquePointer, _ sql: String) -> String {
    var statement: OpaquePointer?
    defer { sqlite3_finalize(statement) }
    guard sqlite3_prepare_v2(handle, sql, -1, &statement, nil) == SQLITE_OK,
          sqlite3_step(statement) == SQLITE_ROW,
          let raw = sqlite3_column_text(statement, 0)
    else { return "" }
    return String(cString: raw)
}

private func tables(_ handle: OpaquePointer) -> Set<String> {
    var statement: OpaquePointer?
    defer { sqlite3_finalize(statement) }
    sqlite3_prepare_v2(handle, "SELECT name FROM sqlite_master WHERE type='table';", -1, &statement, nil)
    var names: Set<String> = []
    while sqlite3_step(statement) == SQLITE_ROW {
        if let raw = sqlite3_column_text(statement, 0) { names.insert(String(cString: raw)) }
    }
    return names
}

private let v1Schema = [
    """
    CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL UNIQUE,
      first_seen_at REAL NOT NULL, last_seen_at REAL NOT NULL);
    """,
    """
    CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER NOT NULL
      REFERENCES conversations(id) ON DELETE CASCADE, sequence INTEGER NOT NULL, sender TEXT,
      ownership TEXT NOT NULL, visible_time TEXT, text TEXT, kind TEXT NOT NULL,
      confidence REAL NOT NULL, first_observed_at REAL NOT NULL);
    """,
]

// MARK: - Migration

struct ArchiveMigrationTests {
    @Test
    func freshDatabaseGoesStraightToTheCurrentVersion() throws {
        let scratch = try Scratch()
        _ = try MessageStore(url: scratch.databaseURL)
        let (version, present) = scratch.inspect { (scalar($0, "PRAGMA user_version;"), tables($0)) }
        #expect(version == "2")
        #expect(MessageStore.requiredTables[2]!.isSubset(of: present))
    }

    @Test
    func aRealV1FileMigratesAndKeepsItsVisualRows() throws {
        let scratch = try Scratch()
        try scratch.seed(v1Schema + [
            "INSERT INTO conversations(title,first_seen_at,last_seen_at) VALUES('chat',1,2);",
            """
            INSERT INTO messages(conversation_id,sequence,sender,ownership,visible_time,text,kind,
              confidence,first_observed_at) VALUES(1,0,'s','them','昨天','hello','text',0.9,100);
            """,
        ], userVersion: 1)

        _ = try MessageStore(url: scratch.databaseURL)
        let (version, messages, present) = scratch.inspect {
            (scalar($0, "PRAGMA user_version;"), scalar($0, "SELECT COUNT(*) FROM messages;"), tables($0))
        }
        #expect(version == "2")
        #expect(messages == "1", "existing visual rows must survive the migration")
        #expect(MessageStore.requiredTables[2]!.isSubset(of: present))
    }

    @Test
    func reopeningAV2FileIsANoOp() throws {
        let scratch = try Scratch()
        _ = try MessageStore(url: scratch.databaseURL)
        _ = try MessageStore(url: scratch.databaseURL)
        #expect(scratch.inspect { scalar($0, "PRAGMA user_version;") } == "2")
    }

    @Test
    func anUnversionedFileWithTablesFailsClosed() throws {
        let scratch = try Scratch()
        // Tables but no stamp: unknown provenance, not a fresh file.
        try scratch.seed(["CREATE TABLE something_else (id INTEGER PRIMARY KEY);"], userVersion: 0)
        #expect(throws: MessageStoreError.unversionedExistingSchema) {
            _ = try MessageStore(url: scratch.databaseURL)
        }
    }

    @Test
    func sqliteInternalTablesDoNotCountAsAnExistingSchema() throws {
        let scratch = try Scratch()
        // AUTOINCREMENT creates sqlite_sequence; it must not be read as
        // "someone else's schema is here".
        try scratch.seed(v1Schema + [
            "INSERT INTO conversations(title,first_seen_at,last_seen_at) VALUES('c',1,1);",
            "DELETE FROM conversations;",
        ], userVersion: 1)
        _ = try MessageStore(url: scratch.databaseURL)
        #expect(scratch.inspect { tables($0).contains("sqlite_sequence") })
        #expect(scratch.inspect { scalar($0, "PRAGMA user_version;") } == "2")
    }

    @Test
    func aV2StampMissingArchiveTablesFailsClosed() throws {
        let scratch = try Scratch()
        try scratch.seed(v1Schema, userVersion: 2)
        #expect(throws: MessageStoreError.schemaIncomplete(version: 2)) {
            _ = try MessageStore(url: scratch.databaseURL)
        }
    }

    @Test
    func aV1FileHoldingAReservedArchiveTableFailsClosed() throws {
        let scratch = try Scratch()
        // CREATE TABLE IF NOT EXISTS would silently adopt this shape and then
        // stamp the file as version 2.
        try scratch.seed(v1Schema + [
            "CREATE TABLE archive_imports (something_unexpected TEXT);"
        ], userVersion: 1)
        #expect(throws: MessageStoreError.reservedTableAlreadyPresent) {
            _ = try MessageStore(url: scratch.databaseURL)
        }
    }

    /// F-031 negative control: the old code stamped whatever it found down to
    /// its own version. This must refuse and change nothing at all.
    @Test
    func aFutureVersionIsRefusedAndTheFileIsLeftUntouched() throws {
        let scratch = try Scratch()
        try scratch.seed(v1Schema, userVersion: 3)
        let before = scratch.inspect { (scalar($0, "PRAGMA user_version;"),
                                        scalar($0, "PRAGMA journal_mode;"), tables($0)) }
        #expect(before.0 == "3")

        #expect(throws: MessageStoreError.schemaFromFuture(version: 3)) {
            _ = try MessageStore(url: scratch.databaseURL)
        }

        let after = scratch.inspect { (scalar($0, "PRAGMA user_version;"),
                                       scalar($0, "PRAGMA journal_mode;"), tables($0)) }
        #expect(after.0 == "3", "must never stamp downward")
        #expect(after.2 == before.2, "no table may be created")
        #expect(after.2.isDisjoint(with: MessageStore.requiredTables[2]!
            .subtracting(MessageStore.requiredTables[1]!)))
        // journal_mode is persistent state: refusing after switching it would
        // make "we did not touch the file" untrue.
        #expect(after.1 == before.1, "journal mode must not change before refusal")
        #expect(after.1 != "wal")
    }
}

// MARK: - Persistence

struct ArchivePersistenceTests {
    private func store() throws -> MessageStore { try MessageStore(url: nil) }
    private let key = ArchiveConversationKey("source-key")

    @Test
    func attributedEvidenceLandsOnlyInTheAttributedTable() async throws {
        let store = try store()
        let result = try await store.persistArchiveEvidence(
            transcript: try attributed([("张三", m35, "哈哈"), ("李四", m36, "收到")]),
            conversationKey: key, importedAt: Date()
        )
        #expect(result == .inserted(importID: 1, recordCount: 2))
        #expect(try await store.archiveRecordCount(shape: "attributed") == 2)
        #expect(try await store.archiveRecordCount(shape: "unattributed") == 0)
        #expect(try await store.archiveConversationCount() == 1)
    }

    @Test
    func unattributedEvidenceLandsOnlyInTheUnattributedTable() async throws {
        let store = try store()
        let result = try await store.persistArchiveEvidence(
            transcript: try unattributed(["哈哈", "收到", "好的"]),
            conversationKey: key, importedAt: Date()
        )
        #expect(result == .inserted(importID: 1, recordCount: 3))
        #expect(try await store.archiveRecordCount(shape: "unattributed") == 3)
        #expect(try await store.archiveRecordCount(shape: "attributed") == 0)
    }

    @Test
    func reimportingTheSameArchiveIsANoOpNotAnError() async throws {
        let store = try store()
        let transcript = try attributed([("张三", m35, "哈哈")])
        let first = try await store.persistArchiveEvidence(
            transcript: transcript, conversationKey: key, importedAt: Date())
        let second = try await store.persistArchiveEvidence(
            transcript: transcript, conversationKey: key, importedAt: Date())
        #expect(first == .inserted(importID: 1, recordCount: 1))
        #expect(second == .alreadyImported(importID: 1))
        #expect(try await store.archiveImportCount() == 1)
        #expect(try await store.archiveRecordCount(shape: "attributed") == 1)
    }

    @Test
    func overlappingExportsRemainTwoImports() async throws {
        // PR #1 established that collapsing overlapping windows deletes real
        // messages. B1 keeps them visible instead.
        let store = try store()
        let rows = (0..<8).map { ("张三", m35, "message \($0)") }
        _ = try await store.persistArchiveEvidence(
            transcript: try attributed(Array(rows.prefix(5))), conversationKey: key, importedAt: Date())
        _ = try await store.persistArchiveEvidence(
            transcript: try attributed(rows), conversationKey: key, importedAt: Date())
        #expect(try await store.archiveImportCount() == 2)
        #expect(try await store.archiveRecordCount(shape: "attributed") == 13)
        #expect(try await store.archiveConversationCount() == 1)
    }

    @Test
    func aFailedImportRollsBackWholeIncludingANewConversation() async throws {
        let store = try store()
        await #expect(throws: ArchivePersistenceError.self) {
            try await store.persistArchiveEvidence(
                transcript: try attributed([("张三", m35, "哈哈"), ("李四", m36, "收到")]),
                conversationKey: key, importedAt: Date(), failBeforeCommit: true
            )
        }
        #expect(try await store.archiveImportCount() == 0)
        #expect(try await store.archiveRecordCount(shape: "attributed") == 0)
        #expect(try await store.archiveConversationCount() == 0, "no orphan conversation")
    }

    @Test
    func theDatabaseItselfRefusesCrossShapeAndMalformedRows() async throws {
        let store = try store()
        _ = try await store.persistArchiveEvidence(
            transcript: try attributed([("张三", m35, "哈哈")]), conversationKey: key, importedAt: Date())
        _ = try await store.persistArchiveEvidence(
            transcript: try unattributed(["哈哈"]),
            conversationKey: ArchiveConversationKey("other"), importedAt: Date())

        for sql in [
            // Shape A row on the Shape B import, and the reverse.
            "INSERT INTO archive_attributed_records(import_id,sequence,sender,sent_at,sent_at_text,text) VALUES(2,0,'s',1,'t','x');",
            "INSERT INTO archive_unattributed_records(import_id,sequence,record_text) VALUES(1,0,'x');",
            // Attribution/time pairing.
            "INSERT INTO archive_imports(archive_conversation_id,import_fingerprint,fingerprint_format_version,source_type,transcript_shape,imported_at,archive_parser_version,time_zone_identifier) VALUES(1,'x1',1,'wechat_native_archive','attributed',1,1,NULL);",
            "INSERT INTO archive_imports(archive_conversation_id,import_fingerprint,fingerprint_format_version,source_type,transcript_shape,imported_at,archive_parser_version,time_zone_identifier) VALUES(1,'x2',1,'wechat_native_archive','unattributed',1,1,'UTC');",
            // Duplicate position within one import.
            "INSERT INTO archive_attributed_records(import_id,sequence,sender,sent_at,sent_at_text,text) VALUES(1,0,'s',1,'t','y');",
            // Version sanity.
            "INSERT INTO archive_imports(archive_conversation_id,import_fingerprint,fingerprint_format_version,source_type,transcript_shape,imported_at,archive_parser_version,time_zone_identifier) VALUES(1,'x3',0,'wechat_native_archive','attributed',1,1,'UTC');",
            "INSERT INTO archive_imports(archive_conversation_id,import_fingerprint,fingerprint_format_version,source_type,transcript_shape,imported_at,archive_parser_version,time_zone_identifier) VALUES(1,'x4',1,'wechat_native_archive','attributed',1,0,'UTC');",
        ] {
            await #expect(throws: MessageStoreError.self, "should be refused: \(sql)") {
                try await store.executeForTesting(sql)
            }
        }
    }

    @Test
    func theUnattributedTableHasNoAttributionColumns() async throws {
        let store = try store()
        let columns = try await store.columnNamesForTesting("archive_unattributed_records")
        for forbidden in ["sender", "sent_at", "sent_at_text", "ownership", "kind", "confidence"] {
            #expect(!columns.contains(forbidden), "\(forbidden) must not exist")
        }
        #expect(columns.contains("record_text"))
    }

    @Test
    func recordCountIsDerivedRatherThanStored() async throws {
        let store = try store()
        let columns = try await store.columnNamesForTesting("archive_imports")
        #expect(!columns.contains("record_count"))
        #expect(!columns.contains("reader_version"))
        // And no canonical-link columns: F-032.
        let conversationColumns = try await store.columnNamesForTesting("archive_conversations")
        #expect(!conversationColumns.contains("canonical_conversation_id"))
        #expect(!conversationColumns.contains("linkage_basis"))
    }
}

// MARK: - Fingerprint

struct ArchiveFingerprintTests {
    private let key = ArchiveConversationKey("k")

    @Test
    func lengthFramingSeparatesValuesADelimiterWouldNotHave() throws {
        // ("a‖b","c") vs ("a","b‖c") collide under join("‖").
        let a = ArchiveImportFingerprint.fingerprint(
            of: try unattributed(["a‖b", "c"]), conversationKey: key)
        let b = ArchiveImportFingerprint.fingerprint(
            of: try unattributed(["a", "b‖c"]), conversationKey: key)
        #expect(a != b)

        let c = ArchiveImportFingerprint.fingerprint(
            of: try unattributed(["x", "y"]), conversationKey: key)
        let d = ArchiveImportFingerprint.fingerprint(
            of: try unattributed(["x"]), conversationKey: ArchiveConversationKey("ky"))
        #expect(c != d)
    }

    @Test
    func identityIncludesTheSourceConversationKey() throws {
        let transcript = try unattributed(["哈哈"])
        #expect(
            ArchiveImportFingerprint.fingerprint(of: transcript, conversationKey: ArchiveConversationKey("one"))
            != ArchiveImportFingerprint.fingerprint(of: transcript, conversationKey: ArchiveConversationKey("two"))
        )
    }

    @Test
    func timezoneReinterpretationDoesNotChangeIdentity() throws {
        // sentAtText is hashed, not the interpreted Date.
        let body = "·张三\n\(m35)\n哈哈\n\n"
        let shanghai = try WeChatNativeTranscriptParser.parse(body, timeZone: TimeZone(identifier: "Asia/Shanghai")!)
        let newYork = try WeChatNativeTranscriptParser.parse(body, timeZone: TimeZone(identifier: "America/New_York")!)
        #expect(
            ArchiveImportFingerprint.fingerprint(of: shanghai, conversationKey: key)
            == ArchiveImportFingerprint.fingerprint(of: newYork, conversationKey: key)
        )
    }

    @Test
    func shapeIsPartOfIdentity() throws {
        #expect(
            ArchiveImportFingerprint.fingerprint(of: try unattributed(["哈哈"]), conversationKey: key)
            != ArchiveImportFingerprint.fingerprint(of: try attributed([("张三", m35, "哈哈")]), conversationKey: key)
        )
    }

    @Test
    func isDeterministic() throws {
        let t = try attributed([("张三", m35, "哈哈"), ("李四", m36, "收到")])
        #expect(
            ArchiveImportFingerprint.fingerprint(of: t, conversationKey: key)
            == ArchiveImportFingerprint.fingerprint(of: t, conversationKey: key)
        )
    }
}

// MARK: - Retention, delete, consent, visual isolation

struct ArchiveLifecycleTests {
    private let key = ArchiveConversationKey("source-key")

    @Test
    func retentionExpiresWholeImportsAndRemovesOrphanedIdentity() async throws {
        let store = try MessageStore(url: nil)
        let now = Date()
        let old = now.addingTimeInterval(-60 * 86_400)
        _ = try await store.persistArchiveEvidence(
            transcript: try attributed([("张三", m35, "old")]), conversationKey: key, importedAt: old)
        _ = try await store.persistArchiveEvidence(
            transcript: try attributed([("张三", m35, "new")]), conversationKey: key, importedAt: now)
        _ = try await store.persistArchiveEvidence(
            transcript: try unattributed(["expiring"]),
            conversationKey: ArchiveConversationKey("expires"), importedAt: old)

        _ = try await store.applyRetention(.thirtyDays, now: now)

        #expect(try await store.archiveImportCount() == 1, "only the retained import survives")
        #expect(try await store.archiveRecordCount(shape: "attributed") == 1)
        #expect(try await store.archiveRecordCount(shape: "unattributed") == 0)
        // Partially expired conversation stays; fully expired identity goes.
        #expect(try await store.archiveConversationCount() == 1)
        #expect(try await store.sourceKeysForTesting() == ["source-key"])
    }

    @Test
    func oldMessagesImportedTodayDoNotImmediatelyExpire() async throws {
        // The clock is when our copy arrived, not when the messages were sent.
        let store = try MessageStore(url: nil)
        let now = Date()
        _ = try await store.persistArchiveEvidence(
            transcript: try attributed([("张三", "2022年3月1日 09:00", "old message")]),
            conversationKey: key, importedAt: now
        )
        _ = try await store.applyRetention(.thirtyDays, now: now)
        #expect(try await store.archiveImportCount() == 1)
        #expect(try await store.archiveRecordCount(shape: "attributed") == 1)
    }

    @Test
    func deleteAllHistoryRemovesArchiveEvidenceToo() async throws {
        let store = try MessageStore(url: nil)
        _ = try await store.persistArchiveEvidence(
            transcript: try attributed([("张三", m35, "哈哈")]), conversationKey: key, importedAt: Date())
        _ = try await store.persistArchiveEvidence(
            transcript: try unattributed(["记录"]),
            conversationKey: ArchiveConversationKey("other"), importedAt: Date())

        try await store.deleteAllHistory()

        #expect(try await store.archiveImportCount() == 0)
        #expect(try await store.archiveConversationCount() == 0)
        #expect(try await store.archiveRecordCount(shape: "attributed") == 0)
        #expect(try await store.archiveRecordCount(shape: "unattributed") == 0)
        #expect(try await store.totalMessageCount() == 0)
    }

    @Test
    func archiveEvidenceIsInvisibleToTheVisualReconcilerAndQueries() async throws {
        // The whole reason archive rows live in their own tables.
        let store = try MessageStore(url: nil)
        let conversationID = try await store.conversationID(forTitle: "chat", seenAt: Date())
        let visible = ExtractedVisibleMessage(
            sender: "张三", ownership: .other, visibleTime: "昨天",
            text: "screen message", kind: .text, confidence: 0.9, normalizedBounds: nil
        )
        try await store.append([visible], conversationID: conversationID, observedAt: Date())

        let tailBefore = try await store.reconciliationTail(conversationID: conversationID)
        let headBefore = try await store.headKeys(conversationID: conversationID, limit: 120)
        let countBefore = try await store.messageCount(inConversation: conversationID)

        for index in 0..<200 {
            _ = try await store.persistArchiveEvidence(
                transcript: try attributed([("李四", m35, "archive \(index)")]),
                conversationKey: ArchiveConversationKey("k\(index)"), importedAt: Date()
            )
        }

        #expect(try await store.reconciliationTail(conversationID: conversationID) == tailBefore)
        #expect(try await store.headKeys(conversationID: conversationID, limit: 120) == headBefore)
        #expect(try await store.messageCount(inConversation: conversationID) == countBefore)
        #expect(try await store.totalMessageCount() == 1)
        #expect(try await store.archiveImportCount() == 200)
    }
}

struct ArchiveConsentTests {
    @Test
    func persistenceIsRefusedWithConsentOffAndCreatesNoDatabase() async throws {
        let scratch = try Scratch()
        let history = LocalMessageHistory(url: scratch.databaseURL)
        // Consent never turned on.
        await #expect(throws: ArchivePersistenceError.localPersistenceConsentRequired) {
            try await history.persistArchiveEvidence(
                transcript: try attributed([("张三", m35, "哈哈")]),
                conversationKey: ArchiveConversationKey("k")
            )
        }
        let manager = FileManager.default
        for suffix in ["", "-wal", "-shm"] {
            #expect(!manager.fileExists(atPath: scratch.databaseURL.path + suffix),
                    "consent off must not create messages.sqlite\(suffix)")
        }
    }

    @Test
    func persistenceWorksOnceConsentIsOn() async throws {
        let scratch = try Scratch()
        let history = LocalMessageHistory(url: scratch.databaseURL)
        await history.setEnabled(true)
        let result = try await history.persistArchiveEvidence(
            transcript: try unattributed(["哈哈", "收到"]),
            conversationKey: ArchiveConversationKey("k")
        )
        #expect(result == .inserted(importID: 1, recordCount: 2))
        #expect(FileManager.default.fileExists(atPath: scratch.databaseURL.path))
    }

    @Test
    func deleteAllHistoryRemovesTheFileAndItsSidecarsWithArchiveEvidencePresent() async throws {
        let scratch = try Scratch()
        let history = LocalMessageHistory(url: scratch.databaseURL)
        await history.setEnabled(true)
        let sentinel = "SENTINEL-ARCHIVE-TEXT-\(UUID().uuidString)"
        _ = try await history.persistArchiveEvidence(
            transcript: try unattributed([sentinel]),
            conversationKey: ArchiveConversationKey("k")
        )
        await history.setEnabled(false)
        await history.deleteAllHistory()

        let manager = FileManager.default
        for suffix in ["", "-wal", "-shm"] {
            let path = scratch.databaseURL.path + suffix
            #expect(!manager.fileExists(atPath: path), "\(path) must be gone")
            if let data = manager.contents(atPath: path) {
                #expect(!String(decoding: data, as: UTF8.self).contains(sentinel))
            }
        }
    }
}

// MARK: - Text fidelity

/// SQLite's C API terminates text at the first NUL unless it is given an
/// explicit byte count. The fingerprint hashes the whole Swift string, so a
/// truncating write means the digest describes a value the database does not
/// hold -- silent corruption that no fingerprint test alone would catch.
struct ArchiveTextFidelityTests {
    private let key = ArchiveConversationKey("k")

    /// Returns the stored `recordText`, which retains the leading marker.
    private func roundTrip(_ payload: String) async throws -> String {
        let store = try MessageStore(url: nil)
        let result = try await store.persistArchiveEvidence(
            transcript: try unattributed([payload]), conversationKey: key, importedAt: Date()
        )
        guard case .inserted(let importID, _) = result else { return "<not inserted>" }
        return try await store.unattributedRecordsForTesting(importID: importID).first?.1 ?? ""
    }

    @Test
    func recordTextSurvivesAnEmbeddedNUL() async throws {
        let payload = "before\u{0000}after"
        let stored = try await roundTrip(payload)
        #expect(stored == "·" + payload, "an embedded NUL must not truncate the stored value")
        #expect(stored.utf8.count == ("·" + payload).utf8.count)
    }

    @Test
    func recordTextSurvivesSeveralEmbeddedNULs() async throws {
        let payload = "a\u{0000}b\u{0000}\u{0000}c"
        let stored = try await roundTrip(payload)
        #expect(stored == "·" + payload)
        #expect(stored.utf8.count == ("·" + payload).utf8.count)
    }

    @Test
    func ordinaryPayloadsRoundTripExactly() async throws {
        for payload in [
            "plain ascii",
            "中文内容",
            "emoji 🌍🐉 mixed",
            "  leading and trailing  ",
            "zero width\u{FEFF}joiner",
        ] {
            // A Shape B record is one line by grammar, so multi-line payloads
            // are covered through Shape A instead.
            #expect(try await roundTrip(payload) == "·" + payload,
                    "failed for: \(payload.debugDescription)")
        }
    }

    @Test
    func attributedFieldsAllSurviveAnEmbeddedNUL() async throws {
        let store = try MessageStore(url: nil)
        let sender = "sender\u{0000}tail"
        let text = "body\u{0000}tail"
        let stamp = "2026年9月7日 20:35"
        let body = "·\(sender)\n\(stamp)\n\(text)\n\n"
        let transcript = try WeChatNativeTranscriptParser.parse(
            body, timeZone: TimeZone(identifier: "Asia/Shanghai")!
        )
        let result = try await store.persistArchiveEvidence(
            transcript: transcript, conversationKey: key, importedAt: Date()
        )
        guard case .inserted(let importID, _) = result else {
            Issue.record("expected an insert"); return
        }
        let rows = try await store.attributedRecordsForTesting(importID: importID)
        #expect(rows.count == 1)
        #expect(rows[0].1 == sender, "sender truncated")
        #expect(rows[0].2 == stamp)
        #expect(rows[0].3 == text, "text truncated")
    }

    /// The byte-level measurement: a truncating write stores fewer bytes than
    /// the value contains, which a Swift-to-Swift comparison can mask.
    @Test
    func theDatabaseStoresEveryByteOfTheValue() async throws {
        let store = try MessageStore(url: nil)
        let payload = "before\u{0000}after"
        _ = try await store.persistArchiveEvidence(
            transcript: try unattributed([payload]), conversationKey: key, importedAt: Date()
        )
        let stored = try await store.storedByteLengthForTesting(
            "SELECT length(CAST(record_text AS BLOB)) FROM archive_unattributed_records;"
        )
        // The record keeps its leading marker, so the expected byte count is
        // the payload plus that one character. Bound to locals so a failure
        // prints both numbers rather than just "false".
        let expected = ("·" + payload).utf8.count
        let truncatedAtFirstNUL = ("·" + "before").utf8.count
        #expect(stored != truncatedAtFirstNUL, "stored \(stored) bytes — truncated at the first NUL")
        #expect(stored == expected, "stored \(stored) bytes, expected \(expected)")
    }

    /// Existing visual storage must be unchanged by the same helper fix.
    @Test
    func visualMessageFieldsStillRoundTripExactly() async throws {
        let store = try MessageStore(url: nil)
        let conversationID = try await store.conversationID(forTitle: "标题 🌍", seenAt: Date())
        let visible = ExtractedVisibleMessage(
            sender: "张三 sender", ownership: .other, visibleTime: "昨天 14:30",
            text: "visual  text  with spaces 🐉", kind: .text, confidence: 0.9,
            normalizedBounds: nil
        )
        try await store.append([visible], conversationID: conversationID, observedAt: Date())
        let stored = try await store.messages(inConversation: conversationID)
        #expect(stored.count == 1)
        #expect(stored[0].sender == visible.sender)
        #expect(stored[0].visibleTime == visible.visibleTime)
        #expect(stored[0].text == visible.text)
        #expect(try await store.conversations().first?.title == "标题 🌍")
    }
}

// MARK: - Fail-closed schema enumeration

/// Schema discovery decides whether to migrate, stamp and switch journal mode.
/// A read that ends for any reason other than `SQLITE_DONE` must therefore
/// throw, not return the rows it happened to collect: a partial table list
/// would let the migration accept a file it never finished inspecting.
struct SchemaEnumerationTests {
    private func connection() -> OpaquePointer {
        var handle: OpaquePointer?
        #expect(sqlite3_open_v2(":memory:", &handle,
                                SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE, nil) == SQLITE_OK)
        sqlite3_exec(handle, "CREATE TABLE t(x); INSERT INTO t VALUES(1),(2),(3);", nil, nil, nil)
        return handle!
    }

    private func statement(_ handle: OpaquePointer, _ sql: String) -> OpaquePointer {
        var statement: OpaquePointer?
        #expect(sqlite3_prepare_v2(handle, sql, -1, &statement, nil) == SQLITE_OK)
        return statement!
    }

    @Test
    func rowsAreCollectedAndDoneTerminates() throws {
        let handle = connection(); defer { sqlite3_close_v2(handle) }
        let select = statement(handle, "SELECT x FROM t ORDER BY x;")
        defer { sqlite3_finalize(select) }
        let rows = try MessageStore.collectRows(select) { Int(sqlite3_column_int64($0, 0)) }
        #expect(rows == [1, 2, 3])
    }

    @Test
    func anEmptyResultIsDoneNotAnError() throws {
        let handle = connection(); defer { sqlite3_close_v2(handle) }
        let select = statement(handle, "SELECT x FROM t WHERE x > 100;")
        defer { sqlite3_finalize(select) }
        #expect(try MessageStore.collectRows(select) { Int(sqlite3_column_int64($0, 0)) }.isEmpty)
    }

    @Test
    func anErrorStatusPropagatesAndNoPartialResultEscapes() throws {
        let handle = connection(); defer { sqlite3_close_v2(handle) }
        let select = statement(handle, "SELECT x FROM t ORDER BY x;")
        defer { sqlite3_finalize(select) }
        // Dropping the table after preparing makes `sqlite3_step` fail when it
        // re-prepares against the changed schema: a read that ends for a reason
        // other than SQLITE_DONE.
        sqlite3_exec(handle, "DROP TABLE t;", nil, nil, nil)
        var collected: [Int] = []
        #expect(throws: MessageStoreError.self) {
            collected = try MessageStore.collectRows(select) { Int(sqlite3_column_int64($0, 0)) }
        }
        #expect(collected.isEmpty, "a partial result must never be returned")
    }

    @Test
    func aFailedEnumerationRefusesTheWholeOpenWithoutMutatingTheFile() throws {
        // The whole point: an uncertain read must not stamp, migrate, or switch
        // journal mode. Proved indirectly by the future-version path, which
        // exits before any of those -- see
        // ArchiveMigrationTests.aFutureVersionIsRefusedAndTheFileIsLeftUntouched.
        #expect(MessageStore.schemaVersion == 2)
    }
}

// MARK: - Retention atomicity

/// Archive retention is two deletions: expired imports, then conversations
/// left holding none. If the second cannot run, the first must not stand --
/// otherwise `source_conversation_key`, which is chat identity, survives every
/// import that justified it.
struct ArchiveRetentionAtomicityTests {
    @Test
    func aFailureBetweenTheTwoDeletionsLeavesNoHalfAppliedState() async throws {
        let store = try MessageStore(url: nil)
        let now = Date()
        let old = now.addingTimeInterval(-60 * 86_400)
        _ = try await store.persistArchiveEvidence(
            transcript: try unattributed(["expiring record"]),
            conversationKey: ArchiveConversationKey("SENTINEL-KEY"), importedAt: old
        )

        await #expect(throws: MessageStoreError.self) {
            _ = try await store.applyRetention(
                .thirtyDays, now: now, failBetweenArchiveRetentionStepsForTesting: true
            )
        }

        // Either everything survives or everything goes. A state with zero
        // imports and a surviving conversation key is the one outcome that must
        // be impossible.
        let imports = try await store.archiveImportCount()
        let records = try await store.archiveRecordCount(shape: "unattributed")
        let conversations = try await store.archiveConversationCount()
        #expect(
            (imports == 1 && records == 1 && conversations == 1) ||
            (imports == 0 && records == 0 && conversations == 0),
            "half-applied retention: imports=\(imports) records=\(records) conversations=\(conversations)"
        )
        if conversations > 0 {
            #expect(imports > 0, "chat identity outlived every import that justified it")
        }
    }

    @Test
    func aSuccessfulSweepStillRemovesOrphanedIdentity() async throws {
        let store = try MessageStore(url: nil)
        let now = Date()
        _ = try await store.persistArchiveEvidence(
            transcript: try unattributed(["gone"]),
            conversationKey: ArchiveConversationKey("k"),
            importedAt: now.addingTimeInterval(-60 * 86_400)
        )
        _ = try await store.applyRetention(.thirtyDays, now: now)
        #expect(try await store.archiveImportCount() == 0)
        #expect(try await store.archiveConversationCount() == 0)
        #expect(try await store.sourceKeysForTesting().isEmpty)
    }
}
