import Foundation
import SQLite3
import Testing
@testable import WeChatCompanion

// MARK: - Fixtures

private let t35 = "2026年9月7日 20:35"
private let t36 = "2026年9月7日 20:36"

private func shapeA(_ rows: [(String, String, String)]) -> String {
    rows.map { "·\($0.0)\n\($0.1)\n\($0.2)\n\n" }.joined()
}

private func shapeB(_ records: [String]) -> String {
    records.map { "·\($0)" }.joined(separator: "\n\n") + "\n"
}

private struct Sandbox: ~Copyable {
    let directory: URL
    var databaseURL: URL { directory.appendingPathComponent("messages.sqlite") }

    init() throws {
        directory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("b2a-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    /// Builds a ZIP with entries at exactly the given paths.
    func archive(_ name: String = "export.zip", entries: [(String, String)]) throws -> URL {
        var builder = ImportZIPBuilder()
        for (path, body) in entries { builder.add(path, body) }
        let url = directory.appendingPathComponent(name)
        try builder.write(to: url)
        return url
    }

    func contents() -> [String] {
        (try? FileManager.default.contentsOfDirectory(atPath: directory.path).sorted()) ?? []
    }

    deinit { try? FileManager.default.removeItem(at: directory) }
}

/// A minimal stored-only ZIP writer, so a test can place entries at exact
/// paths -- which is the whole subject of source-identity derivation.
private struct ImportZIPBuilder {
    private var entries: [(name: String, body: Data)] = []

    mutating func add(_ name: String, _ body: String) {
        entries.append((name, Data(body.utf8)))
    }

    func write(to url: URL) throws {
        var file = Data(), directory = Data()
        for entry in entries {
            var crc = CRC32(); crc.update(entry.body)
            let name = Data(entry.name.utf8)
            let offset = UInt32(file.count)
            file += u32(0x0403_4B50) + u16(20) + u16(0) + u16(0) + u16(0) + u16(0)
            file += u32(crc.value) + u32(UInt32(entry.body.count)) + u32(UInt32(entry.body.count))
            file += u16(UInt16(name.count)) + u16(0) + name + entry.body
            directory += u32(0x0201_4B50) + u16(0x0314) + u16(20) + u16(0) + u16(0)
            directory += u16(0) + u16(0)
            directory += u32(crc.value) + u32(UInt32(entry.body.count)) + u32(UInt32(entry.body.count))
            directory += u16(UInt16(name.count)) + u16(0) + u16(0) + u16(0) + u16(0) + u32(0)
            directory += u32(offset) + name
        }
        let directoryOffset = UInt32(file.count)
        file += directory
        file += u32(0x0605_4B50) + u16(0) + u16(0)
        file += u16(UInt16(entries.count)) + u16(UInt16(entries.count))
        file += u32(UInt32(directory.count)) + u32(directoryOffset) + u16(0)
        try file.write(to: url)
    }

    private func u16(_ v: UInt16) -> Data { Data([UInt8(v & 0xFF), UInt8(v >> 8 & 0xFF)]) }
    private func u32(_ v: UInt32) -> Data {
        Data([UInt8(v & 0xFF), UInt8(v >> 8 & 0xFF), UInt8(v >> 16 & 0xFF), UInt8(v >> 24 & 0xFF)])
    }
}

// MARK: - Source identity derivation

/// Identity comes from the container's shape and nothing else. Every real
/// export puts its transcript at the root and its attachments beneath one
/// directory named for the conversation, so that directory is the only
/// structural identity the artifact offers.
struct ArchiveSourceIdentityTests {
    private func identity(_ entries: [(String, String)]) throws
        -> WeChatNativeArchiveSourceIdentity? {
        let sandbox = try Sandbox()
        let url = try sandbox.archive(entries: entries)
        return try WeChatNativeArchiveReader.read(contentsOf: url).sourceIdentity
    }

    @Test
    func oneSharedTopLevelDirectoryYieldsIdentity() throws {
        let found = try identity([
            ("聊天记录.txt", shapeA([("张三", t35, "哈哈")])),
            ("会话目录/IMG_0001.jpg", "not really a jpeg"),
            ("会话目录/IMG_0002.jpg", "nor this"),
        ])
        #expect(found == .singleTopLevelDirectory("会话目录"))
        #expect(found?.kind == "single_top_level_directory")
    }

    @Test
    func theDirectoryComponentIsPreservedVerbatim() throws {
        // Not trimmed, not lowercased, not normalised: two directories that
        // differ only by normalisation are two different provenance claims.
        for raw in ["  含空格  ", "Emoji 🐉 Dir", "MiXeD Case", "组合\u{0301}字符"] {
            let found = try identity([
                ("聊天记录.txt", shapeB(["记录"])),
                ("\(raw)/a.jpg", "x"),
            ])
            #expect(found == .singleTopLevelDirectory(raw), "mangled: \(raw.debugDescription)")
        }
    }

    @Test
    func twoTopLevelDirectoriesYieldNoIdentity() throws {
        #expect(try identity([
            ("聊天记录.txt", shapeB(["记录"])),
            ("目录甲/a.jpg", "x"),
            ("目录乙/b.jpg", "y"),
        ]) == nil)
    }

    @Test
    func rootLevelAttachmentsYieldNoIdentity() throws {
        #expect(try identity([
            ("聊天记录.txt", shapeB(["记录"])),
            ("IMG_0001.jpg", "x"),
        ]) == nil)
    }

    @Test
    func aMixedLayoutYieldsNoIdentity() throws {
        // One attachment nested, one at the root: no single directory covers
        // them, so nothing is proven.
        #expect(try identity([
            ("聊天记录.txt", shapeB(["记录"])),
            ("会话目录/a.jpg", "x"),
            ("stray.jpg", "y"),
        ]) == nil)
    }

    @Test
    func anAttachmentFreeArchiveYieldsNoIdentity() throws {
        // B0 left this open. The conservative answer is no identity, not one
        // invented from the filename.
        #expect(try identity([("聊天记录.txt", shapeA([("张三", t35, "哈哈")]))]) == nil)
    }

    @Test
    func theZIPFilenameDoesNotAffectIdentity() throws {
        let entries: [(String, String)] = [
            ("聊天记录.txt", shapeB(["记录"])),
            ("会话目录/a.jpg", "x"),
        ]
        let sandbox = try Sandbox()
        let first = try WeChatNativeArchiveReader.read(
            contentsOf: try sandbox.archive("one.zip", entries: entries)).sourceIdentity
        let second = try WeChatNativeArchiveReader.read(
            contentsOf: try sandbox.archive("完全不同的名字.zip", entries: entries)).sourceIdentity
        #expect(first == second)
        #expect(first == .singleTopLevelDirectory("会话目录"))
    }

    @Test
    func transcriptContentsDoNotAffectIdentity() throws {
        let a = try identity([
            ("聊天记录.txt", shapeA([("张三", t35, "完全不同的内容")])),
            ("会话目录/a.jpg", "x"),
        ])
        let b = try identity([
            ("聊天记录.txt", shapeB(["另一种形状", "更多记录"])),
            ("会话目录/a.jpg", "x"),
        ])
        #expect(a == b, "identity must be structural, not derived from content")
    }

    @Test
    func theSummaryReportsAvailabilityButNeverTheValue() throws {
        let sandbox = try Sandbox()
        let secret = "机密会话目录名"
        let url = try sandbox.archive(entries: [
            ("聊天记录.txt", shapeA([("张三", t35, "哈哈")])),
            ("\(secret)/a.jpg", "x"),
        ])
        let archive = try WeChatNativeArchiveReader.read(contentsOf: url)
        let report = WeChatNativeArchiveSummary(archive).reportLines.joined(separator: "\n")
        #expect(report.contains("source identity available: yes"))
        #expect(report.contains("source identity kind: single_top_level_directory"))
        #expect(!report.contains(secret), "the raw identity must never be reported")
        // Nor may a hash stand in for it: a hash of a chat name is still a
        // stable identifier for that chat.
        let key = ArchiveConversationKey(sourceIdentity: archive.sourceIdentity!)
        #expect(!report.contains(key.rawValue))
    }

    @Test
    func theStorageKeyIsDomainAndVersionQualified() throws {
        let key = ArchiveConversationKey(
            sourceIdentity: .singleTopLevelDirectory("会话目录")
        )
        #expect(key.rawValue.hasPrefix("wechat_native_archive.source_identity\u{0}"))
        #expect(key.rawValue.contains("single_top_level_directory"))
        #expect(key.rawValue.hasSuffix("会话目录"))
        // Length framing: a raw value that itself contains the separators
        // cannot impersonate a different derivation.
        let awkward = ArchiveConversationKey(
            sourceIdentity: .singleTopLevelDirectory("a\u{0}1\u{0}b")
        )
        #expect(awkward.rawValue != key.rawValue)
        #expect(awkward.rawValue.contains("\u{0}\(("a\u{0}1\u{0}b").utf8.count)\u{0}"))
    }
}

// MARK: - Coordinator

struct ArchiveImportCoordinatorTests {
    private func readyHistory(_ sandbox: borrowing Sandbox) async -> LocalMessageHistory {
        let history = LocalMessageHistory(url: sandbox.databaseURL)
        await history.setEnabled(true)
        return history
    }

    private func validArchive(_ sandbox: borrowing Sandbox, _ body: String) throws -> URL {
        try sandbox.archive(entries: [
            ("聊天记录.txt", body),
            ("会话目录/a.jpg", "x"),
        ])
    }

    @Test
    func consentOffRefusesWithoutReadingTheFile() async throws {
        let sandbox = try Sandbox()
        let history = LocalMessageHistory(url: sandbox.databaseURL)  // consent never enabled
        let importer = WeChatNativeArchiveImporter(history: history)
        // A URL that does not exist: if the importer touched the file at all
        // this would surface as an archive error instead.
        let missing = sandbox.directory.appendingPathComponent("nope.zip")
        await #expect(throws: WeChatNativeArchiveImportError.localPersistenceConsentRequired) {
            try await importer.importArchive(contentsOf: missing)
        }
    }

    @Test
    func anUnavailableStoreRefusesWithoutReadingTheFile() async throws {
        let sandbox = try Sandbox()
        // A database this build refuses to open.
        var handle: OpaquePointer?
        sqlite3_open_v2(sandbox.databaseURL.path, &handle,
                        SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE, nil)
        sqlite3_exec(handle, "PRAGMA user_version = 3;", nil, nil, nil)
        sqlite3_exec(handle, "CREATE TABLE conversations(id INTEGER PRIMARY KEY, title TEXT NOT NULL UNIQUE, first_seen_at REAL NOT NULL, last_seen_at REAL NOT NULL);", nil, nil, nil)
        sqlite3_close_v2(handle)

        let history = LocalMessageHistory(url: sandbox.databaseURL)
        await history.setEnabled(true)
        #expect(await history.storeState == .unavailable)

        let importer = WeChatNativeArchiveImporter(history: history)
        let missing = sandbox.directory.appendingPathComponent("nope.zip")
        await #expect(throws: WeChatNativeArchiveImportError.localStoreUnavailable) {
            try await importer.importArchive(contentsOf: missing)
        }
    }

    @Test
    func aShapeAArchiveBecomesDurableAttributedEvidence() async throws {
        let sandbox = try Sandbox()
        let history = await readyHistory(sandbox)
        let importer = WeChatNativeArchiveImporter(history: history)
        let url = try validArchive(sandbox, shapeA([("张三", t35, "哈哈"), ("李四", t36, "收到")]))

        let summary = try await importer.importArchive(contentsOf: url)
        #expect(summary.outcome == .inserted)
        #expect(summary.transcriptShape == "attributed")
        #expect(summary.recordCount == 2)
        #expect(summary.attributionAvailable)
        #expect(summary.perMessageTimeAvailable)
        #expect(summary.sourceIdentityKind == "single_top_level_directory")
        #expect(summary.attachmentCountsByExtension == ["jpg": 1])

        let store = await history.openStore()!
        #expect(try await store.archiveRecordCount(shape: "attributed") == 2)
        #expect(try await store.archiveRecordCount(shape: "unattributed") == 0)
        #expect(try await store.archiveConversationCount() == 1)
    }

    @Test
    func aShapeBArchiveBecomesDurableUnattributedEvidence() async throws {
        let sandbox = try Sandbox()
        let history = await readyHistory(sandbox)
        let importer = WeChatNativeArchiveImporter(history: history)
        let url = try validArchive(sandbox, shapeB(["哈哈", "收到", "好的"]))

        let summary = try await importer.importArchive(contentsOf: url)
        #expect(summary.outcome == .inserted)
        #expect(summary.transcriptShape == "unattributed")
        #expect(summary.recordCount == 3)
        #expect(!summary.attributionAvailable)
        #expect(!summary.perMessageTimeAvailable)

        let store = await history.openStore()!
        #expect(try await store.archiveRecordCount(shape: "unattributed") == 3)
        #expect(try await store.archiveRecordCount(shape: "attributed") == 0)
    }

    @Test
    func importingTheSameArchiveTwiceIsAlreadyImported() async throws {
        let sandbox = try Sandbox()
        let history = await readyHistory(sandbox)
        let importer = WeChatNativeArchiveImporter(history: history)
        let url = try validArchive(sandbox, shapeA([("张三", t35, "哈哈")]))

        #expect(try await importer.importArchive(contentsOf: url).outcome == .inserted)
        #expect(try await importer.importArchive(contentsOf: url).outcome == .alreadyImported)

        let store = await history.openStore()!
        #expect(try await store.archiveImportCount() == 1)
        #expect(try await store.archiveRecordCount(shape: "attributed") == 1)
    }

    @Test
    func aRecognizedTranscriptWithNoSourceIdentityIsRefusedAndStoresNothing() async throws {
        let sandbox = try Sandbox()
        let history = await readyHistory(sandbox)
        let importer = WeChatNativeArchiveImporter(history: history)
        // Attachment-free: the transcript parses, but nothing names the source.
        let url = try sandbox.archive(entries: [("聊天记录.txt", shapeA([("张三", t35, "哈哈")]))])

        await #expect(throws: WeChatNativeArchiveImportError.sourceConversationIdentityUnavailable) {
            try await importer.importArchive(contentsOf: url)
        }
        let store = await history.openStore()!
        #expect(try await store.archiveImportCount() == 0)
        #expect(try await store.archiveConversationCount() == 0)
        #expect(try await store.archiveRecordCount(shape: "attributed") == 0)
    }

    @Test
    func aReaderFailureStoresNothing() async throws {
        let sandbox = try Sandbox()
        let history = await readyHistory(sandbox)
        let importer = WeChatNativeArchiveImporter(history: history)
        let notAZip = sandbox.directory.appendingPathComponent("bad.zip")
        try Data("definitely not a zip".utf8).write(to: notAZip)

        await #expect(throws: WeChatNativeArchiveImportError.archive(.notAZIPArchive)) {
            try await importer.importArchive(contentsOf: notAZip)
        }
        let store = await history.openStore()!
        #expect(try await store.archiveImportCount() == 0)
        #expect(try await store.archiveConversationCount() == 0)
    }

    @Test
    func anUnrecognizedTranscriptIsRefusedDistinctly() async throws {
        let sandbox = try Sandbox()
        let history = await readyHistory(sandbox)
        let importer = WeChatNativeArchiveImporter(history: history)
        let url = try sandbox.archive(entries: [
            ("readme.txt", "not a transcript at all"),
            ("会话目录/a.jpg", "x"),
        ])
        await #expect(throws: WeChatNativeArchiveImportError.unsupportedTranscript) {
            try await importer.importArchive(contentsOf: url)
        }
    }

    @Test
    func mixedShapeCandidatesAreRefusedDistinctly() async throws {
        let sandbox = try Sandbox()
        let history = await readyHistory(sandbox)
        let importer = WeChatNativeArchiveImporter(history: history)
        let url = try sandbox.archive(entries: [
            ("a.txt", shapeA([("张三", t35, "哈哈")])),
            ("b.txt", shapeB(["1", "2", "3"])),
            ("会话目录/a.jpg", "x"),
        ])
        await #expect(throws: WeChatNativeArchiveImportError.ambiguousTranscriptCandidates) {
            try await importer.importArchive(contentsOf: url)
        }
    }

    @Test
    func aSuccessfulImportRetainsNoCopyOfTheArchive() async throws {
        let sandbox = try Sandbox()
        let history = await readyHistory(sandbox)
        let importer = WeChatNativeArchiveImporter(history: history)
        let url = try validArchive(sandbox, shapeA([("张三", t35, "哈哈")]))
        let before = sandbox.contents()

        _ = try await importer.importArchive(contentsOf: url)

        // Only the database and its sidecars may appear; no ZIP copy, no
        // extracted attachment, no staging directory.
        let added = Set(sandbox.contents()).subtracting(before)
        for name in added {
            #expect(name.hasPrefix("messages.sqlite"), "unexpected artifact: \(name)")
        }
        #expect(!added.contains { $0.hasSuffix(".zip") })
        #expect(FileManager.default.fileExists(atPath: url.path), "the source must be left alone")
    }
}

// MARK: - State changing between preflight and write

/// The preflight is an optimisation, not the guarantee. Consent can be withdrawn
/// while the archive is being read, and the write-time refusal must reach the
/// caller as an *importer* error -- B2b should never have to know that
/// `ArchivePersistenceError` or `MessageStoreError` exist.
struct ArchiveImportRaceTests {
    /// `Sandbox` is noncopyable, so it cannot travel in a tuple; each test owns
    /// its own and builds the archive inline.
    private func archive(in sandbox: borrowing Sandbox) throws -> URL {
        try sandbox.archive(entries: [
            ("聊天记录.txt", shapeA([("张三", t35, "哈哈")])),
            ("会话目录/a.jpg", "x"),
        ])
    }

    @Test
    func consentRevokedAfterPreflightSurfacesAsAnImporterConsentError() async throws {
        let sandbox = try Sandbox()
        let url = try archive(in: sandbox)
        let history = LocalMessageHistory(url: sandbox.databaseURL)
        await history.setEnabled(true)
        let store = await history.openStore()!
        let importer = WeChatNativeArchiveImporter(history: history)

        await #expect(throws: WeChatNativeArchiveImportError.localPersistenceConsentRequired) {
            try await importer.importArchive(contentsOf: url) {
                // Exactly the window the preflight cannot cover.
                await history.setEnabled(false)
            }
        }
        #expect(try await store.archiveImportCount() == 0)
        #expect(try await store.archiveConversationCount() == 0)
        #expect(try await store.archiveRecordCount(shape: "attributed") == 0)
    }

    @Test
    func aStoreThatBecomesUnavailableAfterPreflightSurfacesAsAnImporterStoreError() async throws {
        let sandbox = try Sandbox()
        let url = try archive(in: sandbox)
        let history = LocalMessageHistory(url: sandbox.databaseURL)
        await history.setEnabled(true)
        let importer = WeChatNativeArchiveImporter(history: history)

        await #expect(throws: WeChatNativeArchiveImportError.localStoreUnavailable) {
            try await importer.importArchive(contentsOf: url) {
                // Consent stays on; the store goes away. Toggling off and on
                // over a database this build refuses to open is the honest way
                // to reach `unavailable` without faking the state directly.
                await history.setEnabled(false)
                var handle: OpaquePointer?
                sqlite3_open_v2(sandbox.databaseURL.path, &handle,
                                SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE, nil)
                sqlite3_exec(handle, "PRAGMA user_version = 3;", nil, nil, nil)
                sqlite3_close_v2(handle)
                await history.setEnabled(true)
            }
        }
        #expect(await history.storeState == .unavailable)
    }

    @Test
    func noPersistenceLayerErrorEscapesTheImporter() async throws {
        // The contract B2b depends on: one enum, whatever went wrong.
        let sandbox = try Sandbox()
        let url = try archive(in: sandbox)
        let history = LocalMessageHistory(url: sandbox.databaseURL)
        await history.setEnabled(true)
        let importer = WeChatNativeArchiveImporter(history: history)
        do {
            try await importer.importArchive(contentsOf: url) {
                await history.setEnabled(false)
            }
            Issue.record("expected a refusal")
        } catch is WeChatNativeArchiveImportError {
            // Correct: the importer's own type.
        } catch {
            Issue.record("leaked \(type(of: error)) instead of WeChatNativeArchiveImportError")
        }
    }
}

// MARK: - Several recognized transcripts

/// Phase A allows several recognized candidates of one shape and takes the
/// richest. A losing candidate is still a transcript, so it must not be treated
/// as an ordinary entry when deriving source identity -- otherwise a root-level
/// TXT would prove that no single top-level directory covers the archive, and
/// identity would vanish for an archive that plainly has one.
struct ArchiveMultipleTranscriptCandidateTests {
    @Test
    func aLosingRootLevelCandidateDoesNotDestroySourceIdentity() throws {
        let sandbox = try Sandbox()
        let url = try sandbox.archive(entries: [
            ("small.txt", shapeA([("张三", t35, "哈哈")])),
            ("big.txt", shapeA([("张三", t35, "哈哈"), ("李四", t36, "收到"), ("王五", t36, "好")])),
            ("会话目录/a.jpg", "x"),
            ("会话目录/b.jpg", "y"),
        ])
        let archive = try WeChatNativeArchiveReader.read(contentsOf: url)
        #expect(archive.transcriptCandidateCount == 2)
        #expect(archive.recordCount == 3, "the richest transcript wins")
        #expect(archive.sourceIdentity == .singleTopLevelDirectory("会话目录"))
        // The losing transcript is not counted as an attachment either.
        #expect(archive.attachmentCountsByExtension == ["jpg": 2])
    }

    @Test
    func severalCandidatesWithAttachmentsInTwoDirectoriesStillYieldNoIdentity() throws {
        let sandbox = try Sandbox()
        let url = try sandbox.archive(entries: [
            ("small.txt", shapeB(["一"])),
            ("big.txt", shapeB(["一", "二", "三"])),
            ("目录甲/a.jpg", "x"),
            ("目录乙/b.jpg", "y"),
        ])
        let archive = try WeChatNativeArchiveReader.read(contentsOf: url)
        #expect(archive.recordCount == 3)
        #expect(archive.sourceIdentity == nil)
    }

    @Test
    func mixedShapeCandidatesAreStillAmbiguous() throws {
        let sandbox = try Sandbox()
        let url = try sandbox.archive(entries: [
            ("a.txt", shapeA([("张三", t35, "哈哈")])),
            ("b.txt", shapeB(["一", "二", "三"])),
            ("会话目录/a.jpg", "x"),
        ])
        #expect(throws: WeChatNativeArchiveError.ambiguousTranscriptCandidates) {
            _ = try WeChatNativeArchiveReader.read(contentsOf: url)
        }
    }

    @Test
    func theRawIdentityStaysOutOfSummariesEvenWithSeveralCandidates() throws {
        let sandbox = try Sandbox()
        let secret = "机密会话目录"
        let url = try sandbox.archive(entries: [
            ("small.txt", shapeA([("张三", t35, "哈哈")])),
            ("big.txt", shapeA([("张三", t35, "哈哈"), ("李四", t36, "收到")])),
            ("\(secret)/a.jpg", "x"),
        ])
        let archive = try WeChatNativeArchiveReader.read(contentsOf: url)
        let report = WeChatNativeArchiveSummary(archive).reportLines.joined(separator: "\n")
        #expect(report.contains("source identity available: yes"))
        #expect(!report.contains(secret))
        #expect(!report.contains(ArchiveConversationKey(sourceIdentity: archive.sourceIdentity!).rawValue))
    }
}
