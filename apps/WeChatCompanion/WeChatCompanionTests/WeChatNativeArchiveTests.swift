import Compression
import Foundation
import Testing
@testable import WeChatCompanion

// MARK: - Test-only ZIP writer

/// Builds ZIP containers byte by byte, so a test can craft the entries a real
/// library would refuse to produce -- a traversal name, a symlink, an entry
/// declaring a compression method we do not support, a broken CRC.
///
/// Test-only, deliberately: the app reads archives and never writes one.
private struct ZIPBuilder {
    struct Entry {
        var name: String
        var body: Data
        var deflated = false
        var method: UInt16?
        var flags: UInt16 = 0
        var isSymlink = false
        var crcOverride: UInt32?
        var uncompressedSizeOverride: UInt32?
    }

    var entries: [Entry] = []

    mutating func add(
        _ name: String,
        _ text: String,
        deflated: Bool = false,
        method: UInt16? = nil,
        flags: UInt16 = 0,
        isSymlink: Bool = false,
        crcOverride: UInt32? = nil,
        uncompressedSizeOverride: UInt32? = nil
    ) {
        entries.append(Entry(
            name: name,
            body: Data(text.utf8),
            deflated: deflated,
            method: method,
            flags: flags,
            isSymlink: isSymlink,
            crcOverride: crcOverride,
            uncompressedSizeOverride: uncompressedSizeOverride
        ))
    }

    func write(to url: URL) throws {
        var file = Data()
        var directory = Data()

        for entry in entries {
            let payload = entry.deflated ? Self.deflate(entry.body) : entry.body
            let method = entry.method ?? (entry.deflated ? 8 : 0)
            var crc = CRC32()
            crc.update(entry.body)
            let checksum = entry.crcOverride ?? crc.value
            let uncompressed = entry.uncompressedSizeOverride ?? UInt32(entry.body.count)
            let name = Data(entry.name.utf8)
            let offset = UInt32(file.count)

            file += Self.u32(0x0403_4B50)
            file += Self.u16(20) + Self.u16(entry.flags) + Self.u16(method)
            file += Self.u16(0) + Self.u16(0)
            file += Self.u32(checksum) + Self.u32(UInt32(payload.count)) + Self.u32(uncompressed)
            file += Self.u16(UInt16(name.count)) + Self.u16(0)
            file += name
            file += payload

            // Unix "made by", so the symlink bit in the external attributes is
            // read rather than ignored.
            let externalAttributes: UInt32 = entry.isSymlink ? 0xA1FF_0000 : 0
            directory += Self.u32(0x0201_4B50)
            directory += Self.u16(0x0314) + Self.u16(20) + Self.u16(entry.flags) + Self.u16(method)
            directory += Self.u16(0) + Self.u16(0)
            directory += Self.u32(checksum) + Self.u32(UInt32(payload.count)) + Self.u32(uncompressed)
            directory += Self.u16(UInt16(name.count)) + Self.u16(0) + Self.u16(0)
            directory += Self.u16(0) + Self.u16(0) + Self.u32(externalAttributes)
            directory += Self.u32(offset)
            directory += name
        }

        let directoryOffset = UInt32(file.count)
        file += directory
        file += Self.u32(0x0605_4B50)
        file += Self.u16(0) + Self.u16(0)
        file += Self.u16(UInt16(entries.count)) + Self.u16(UInt16(entries.count))
        file += Self.u32(UInt32(directory.count)) + Self.u32(directoryOffset) + Self.u16(0)
        try file.write(to: url)
    }

    private static func u16(_ value: UInt16) -> Data {
        Data([UInt8(value & 0xFF), UInt8(value >> 8 & 0xFF)])
    }

    private static func u32(_ value: UInt32) -> Data {
        Data([
            UInt8(value & 0xFF), UInt8(value >> 8 & 0xFF),
            UInt8(value >> 16 & 0xFF), UInt8(value >> 24 & 0xFF),
        ])
    }

    private static func deflate(_ data: Data) -> Data {
        guard !data.isEmpty else { return Data([0x03, 0x00]) }
        let capacity = data.count + 4_096
        let destination = UnsafeMutablePointer<UInt8>.allocate(capacity: capacity)
        defer { destination.deallocate() }
        let written = data.withUnsafeBytes { raw in
            compression_encode_buffer(
                destination, capacity,
                raw.bindMemory(to: UInt8.self).baseAddress!, data.count,
                nil, COMPRESSION_ZLIB
            )
        }
        return Data(bytes: destination, count: written)
    }
}

// MARK: - Fixtures

private let m35 = "2026年9月7日 20:35"
private let m36 = "2026年9月7日 20:36"

private func transcript(_ rows: [(String, String, String)], newline: String = "\n") -> String {
    rows.map { "·\($0.0)\n\($0.1)\n\($0.2)\n" }.joined()
        .replacingOccurrences(of: "\n", with: newline)
}

/// A directory that exists only for the duration of one test.
private struct Scratch: ~Copyable {
    let url: URL

    init() throws {
        url = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("archive-tests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    }

    func zip(_ name: String = "chat.zip", _ build: (inout ZIPBuilder) -> Void) throws -> URL {
        var builder = ZIPBuilder()
        build(&builder)
        let target = url.appendingPathComponent(name)
        try builder.write(to: target)
        return target
    }

    deinit { try? FileManager.default.removeItem(at: url) }
}

// MARK: - Parser

struct WeChatNativeTranscriptParserTests {
    @Test
    func parsesOrdinaryMessagesInOrder() throws {
        let messages = try WeChatNativeTranscriptParser.parse(transcript([
            ("张三", m35, "哈哈"),
            ("李四", m36, "收到"),
        ]))
        #expect(messages.map(\.sender) == ["张三", "李四"])
        #expect(messages.map(\.text) == ["哈哈", "收到"])
        #expect(messages.map(\.sequence) == [0, 1])
        #expect(messages.map(\.sentAtText) == [m35, m36])
    }

    @Test
    func keepsMultiLineBodiesIncludingInteriorBlankLinesAndSpaces() throws {
        let body = """
        ·张三
        \(m35)
        第一行
          第二行有前导空格
        \n第四行在空行之后
        ·李四
        \(m36)
        尾巴
        """
        let messages = try WeChatNativeTranscriptParser.parse(body)
        #expect(messages[0].text == "第一行\n  第二行有前导空格\n\n第四行在空行之后")
        #expect(messages[1].text == "尾巴")
    }

    @Test
    func keepsAnEmptyBodyAsAnEmptyString() throws {
        let messages = try WeChatNativeTranscriptParser.parse("""
        ·张三
        \(m35)

        ·李四
        \(m36)
        收到
        """)
        #expect(messages.count == 2)
        #expect(messages[0].text.isEmpty)
        #expect(messages[1].text == "收到")
    }

    @Test
    func acceptsCRLFLineEndings() throws {
        let messages = try WeChatNativeTranscriptParser.parse(
            transcript([("张三", m35, "哈哈"), ("李四", m36, "收到")], newline: "\r\n")
        )
        #expect(messages.map(\.text) == ["哈哈", "收到"])
    }

    @Test
    func acceptsAUTF8ByteOrderMark() throws {
        let messages = try WeChatNativeTranscriptParser.parse(
            "\u{FEFF}" + transcript([("张三", m35, "哈哈")])
        )
        #expect(messages.count == 1)
        #expect(messages[0].sender == "张三")
    }

    @Test
    func preservesUnicodeAndEmojiInSendersAndBodies() throws {
        let messages = try WeChatNativeTranscriptParser.parse(transcript([
            ("李·四 🐉", m35, "你好 🌍\n第二行 😀"),
            ("Ann O'Neill", m36, "Ünïcødé — ok"),
        ]))
        #expect(messages[0].sender == "李·四 🐉")
        #expect(messages[0].text == "你好 🌍\n第二行 😀")
        #expect(messages[1].sender == "Ann O'Neill")
        #expect(messages[1].text == "Ünïcødé — ok")
    }

    @Test
    func repeatedIdenticalMessagesAreAllPreserved() throws {
        let messages = try WeChatNativeTranscriptParser.parse(
            transcript(Array(repeating: ("张三", m35, "哈哈"), count: 3))
        )
        #expect(messages.count == 3)
        #expect(messages.map(\.sequence) == [0, 1, 2])
    }

    @Test
    func identicalSenderTimeAndTextDoNotCollapse() throws {
        // The reader models a transcript, not a set. Two messages that agree on
        // every visible field are still two messages, distinguished by position
        // -- the same rule `FrameReconciler` follows for captured frames.
        let messages = try WeChatNativeTranscriptParser.parse(transcript([
            ("张三", m35, "哈哈"),
            ("张三", m35, "哈哈"),
            ("李四", m35, "哈哈"),
        ]))
        #expect(messages.count == 3)
        #expect(Set(messages.map(\.sequence)).count == 3)
        #expect(messages[0].sentAt == messages[2].sentAt)
    }

    @Test
    func rejectsTextThatIsNotATranscript() {
        #expect(throws: WeChatTranscriptError.notANativeTranscript) {
            try WeChatNativeTranscriptParser.parse("just some notes\nnothing to see")
        }
    }

    @Test
    func rejectsContentBeforeTheFirstRecordButAllowsBlankLines() throws {
        #expect(throws: WeChatTranscriptError.notANativeTranscript) {
            try WeChatNativeTranscriptParser.parse("导出说明\n" + transcript([("张三", m35, "x")]))
        }
        let tolerated = try WeChatNativeTranscriptParser.parse(
            "\n\n  \n" + transcript([("张三", m35, "x")])
        )
        #expect(tolerated.count == 1)
    }
}

// MARK: - Time semantics

struct WeChatArchiveTimeSemanticsTests {
    @Test
    func parsesToMinutePrecisionInAnExplicitTimeZone() throws {
        let shanghai = TimeZone(identifier: "Asia/Shanghai")!
        let messages = try WeChatNativeTranscriptParser.parse(
            transcript([("张三", m35, "哈哈")]), timeZone: shanghai
        )
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = shanghai
        let parts = calendar.dateComponents(
            [.year, .month, .day, .hour, .minute, .second], from: messages[0].sentAt
        )
        #expect(parts.year == 2026)
        #expect(parts.month == 9)
        #expect(parts.day == 7)
        #expect(parts.hour == 20)
        #expect(parts.minute == 35)
        // A native export states a minute. It never states a second, and this
        // reader must never imply one.
        #expect(parts.second == 0)
    }

    @Test
    func theSameTextInTwoTimeZonesIsTwoDifferentInstants() throws {
        let shanghai = try WeChatNativeTranscriptParser.parse(
            transcript([("张三", m35, "x")]), timeZone: TimeZone(identifier: "Asia/Shanghai")!
        )[0]
        let newYork = try WeChatNativeTranscriptParser.parse(
            transcript([("张三", m35, "x")]), timeZone: TimeZone(identifier: "America/New_York")!
        )[0]
        // Documents the assumption rather than hiding it: a native export
        // carries no offset, so the importing Mac's zone decides the instant.
        #expect(shanghai.sentAt != newYork.sentAt)
        #expect(shanghai.sentAtText == newYork.sentAtText)
    }

    @Test
    func aSingleDigitHourParses() throws {
        let messages = try WeChatNativeTranscriptParser.parse(
            transcript([("张三", "2026年9月7日 9:05", "早")])
        )
        #expect(messages.count == 1)
        #expect(messages[0].sentAtText == "2026年9月7日 9:05")
    }

    @Test
    func theParsedInstantDoesNotDependOnTheUsersCalendarPreference() throws {
        // A DateFormatter that inherited a Buddhist calendar from system
        // settings would read 2026年 as a different year entirely.
        let formatter = WeChatNativeTranscriptParser.formatter(
            timeZone: TimeZone(identifier: "Asia/Shanghai")!
        )
        #expect(formatter.calendar.identifier == .gregorian)
        #expect(formatter.locale.identifier == "en_US_POSIX")
        #expect(!formatter.isLenient)
    }
}

// MARK: - ZIP safety

struct WeChatNativeArchiveSafetyTests {
    private func expectArchiveError(
        _ expected: ZIPArchiveError, _ body: () throws -> Void
    ) {
        #expect(throws: WeChatNativeArchiveError.archive(expected)) { try body() }
    }

    @Test
    func readsAValidSyntheticArchive() throws {
        let scratch = try Scratch()
        let url = try scratch.zip { builder in
            builder.add("聊天记录.txt", transcript([
                ("张三", m35, "哈哈"), ("李四", m36, "收到"),
            ]), deflated: true)
            builder.add("images/IMG_0001.jpg", "not really a jpeg")
            builder.add("video/VID_0001.MP4", "not really a video")
        }
        let archive = try WeChatNativeArchiveReader.read(contentsOf: url)
        #expect(archive.transcriptEntryName == "聊天记录.txt")
        #expect(archive.messages.count == 2)
        #expect(archive.entryCount == 3)
        #expect(archive.transcriptCandidateCount == 1)
        #expect(archive.attachmentCountsByExtension == ["jpg": 1, "mp4": 1])
    }

    @Test
    func readsAStoredUncompressedTranscript() throws {
        let scratch = try Scratch()
        let url = try scratch.zip { $0.add("chat.txt", transcript([("张三", m35, "哈哈")])) }
        #expect(try WeChatNativeArchiveReader.read(contentsOf: url).messages.count == 1)
    }

    @Test
    func rejectsPathTraversalEntries() throws {
        let scratch = try Scratch()
        let url = try scratch.zip { $0.add("../聊天记录.txt", transcript([("张三", m35, "x")])) }
        expectArchiveError(.unsafeEntryPath) {
            _ = try WeChatNativeArchiveReader.read(contentsOf: url)
        }
    }

    @Test
    func rejectsAbsolutePathEntries() throws {
        let scratch = try Scratch()
        let url = try scratch.zip { $0.add("/etc/聊天记录.txt", transcript([("张三", m35, "x")])) }
        expectArchiveError(.unsafeEntryPath) {
            _ = try WeChatNativeArchiveReader.read(contentsOf: url)
        }
    }

    @Test
    func rejectsDriveQualifiedEntries() throws {
        let scratch = try Scratch()
        let url = try scratch.zip { $0.add("C:\\聊天记录.txt", transcript([("张三", m35, "x")])) }
        expectArchiveError(.unsafeEntryPath) {
            _ = try WeChatNativeArchiveReader.read(contentsOf: url)
        }
    }

    @Test
    func rejectsSymbolicLinkEntries() throws {
        let scratch = try Scratch()
        let url = try scratch.zip { builder in
            builder.add("聊天记录.txt", transcript([("张三", m35, "x")]))
            builder.add("escape", "/etc/passwd", isSymlink: true)
        }
        expectArchiveError(.symbolicLinkEntry) {
            _ = try WeChatNativeArchiveReader.read(contentsOf: url)
        }
    }

    @Test
    func rejectsEncryptedEntries() throws {
        let scratch = try Scratch()
        let url = try scratch.zip {
            $0.add("聊天记录.txt", transcript([("张三", m35, "x")]), flags: 0x1)
        }
        expectArchiveError(.encryptedEntry) {
            _ = try WeChatNativeArchiveReader.read(contentsOf: url)
        }
    }

    @Test
    func rejectsUnsupportedCompressionMethods() throws {
        let scratch = try Scratch()
        // 12 is bzip2: a legal ZIP method we deliberately do not implement.
        let url = try scratch.zip {
            $0.add("聊天记录.txt", transcript([("张三", m35, "x")]), method: 12)
        }
        expectArchiveError(.unsupportedCompressionMethod) {
            _ = try WeChatNativeArchiveReader.read(contentsOf: url)
        }
    }

    @Test
    func rejectsAnEntryWhoseCRCDoesNotMatch() throws {
        let scratch = try Scratch()
        let url = try scratch.zip {
            $0.add("聊天记录.txt", transcript([("张三", m35, "x")]), crcOverride: 0xDEAD_BEEF)
        }
        expectArchiveError(.integrityCheckFailed) {
            _ = try WeChatNativeArchiveReader.read(contentsOf: url)
        }
    }

    @Test
    func rejectsAnEntryWhoseDeclaredLengthDoesNotMatch() throws {
        let scratch = try Scratch()
        let url = try scratch.zip {
            $0.add(
                "聊天记录.txt", transcript([("张三", m35, "x")]),
                uncompressedSizeOverride: 999_999
            )
        }
        expectArchiveError(.integrityCheckFailed) {
            _ = try WeChatNativeArchiveReader.read(contentsOf: url)
        }
    }

    @Test
    func rejectsAFileThatIsNotAZIPArchive() throws {
        let scratch = try Scratch()
        let url = scratch.url.appendingPathComponent("not.zip")
        try Data("definitely not a zip archive".utf8).write(to: url)
        expectArchiveError(.notAZIPArchive) {
            _ = try WeChatNativeArchiveReader.read(contentsOf: url)
        }
    }

    @Test
    func rejectsAnUnreadableFile() {
        expectArchiveError(.unreadable) {
            _ = try WeChatNativeArchiveReader.read(
                contentsOf: URL(fileURLWithPath: "/nonexistent/nowhere.zip")
            )
        }
    }

    @Test
    func rejectsAnArchiveWithNoRecognizableTranscript() throws {
        let scratch = try Scratch()
        let url = try scratch.zip { builder in
            builder.add("readme.txt", "just some notes\nnot a chat at all")
            builder.add("images/1.png", "not really a png")
        }
        #expect(throws: WeChatNativeArchiveError.noRecognizableTranscript) {
            _ = try WeChatNativeArchiveReader.read(contentsOf: url)
        }
    }

    @Test
    func enforcesTheEntryCountLimit() throws {
        let scratch = try Scratch()
        let url = try scratch.zip { builder in
            builder.add("聊天记录.txt", transcript([("张三", m35, "x")]))
            for index in 0..<5 { builder.add("f\(index).bin", "x") }
        }
        var limits = ZIPArchiveReader.Limits.standard
        limits.maximumEntryCount = 3
        expectArchiveError(.entryCountOutsideSupportedRange) {
            _ = try WeChatNativeArchiveReader.read(contentsOf: url, limits: limits)
        }
    }

    @Test
    func enforcesTheTotalExpandedSizeLimit() throws {
        let scratch = try Scratch()
        let url = try scratch.zip { builder in
            builder.add("聊天记录.txt", transcript([("张三", m35, "x")]))
            builder.add("big.bin", String(repeating: "x", count: 4_096))
        }
        var limits = ZIPArchiveReader.Limits.standard
        limits.maximumTotalUncompressedBytes = 1_024
        expectArchiveError(.expandedSizeExceedsLimit) {
            _ = try WeChatNativeArchiveReader.read(contentsOf: url, limits: limits)
        }
    }

    @Test
    func aTranscriptTooLargeToReadIsNotACandidate() throws {
        let scratch = try Scratch()
        let url = try scratch.zip { $0.add("聊天记录.txt", transcript([("张三", m35, "x")])) }
        var limits = ZIPArchiveReader.Limits.standard
        limits.maximumReadableEntryBytes = 4
        // Refused as "no transcript" rather than read past the cap.
        #expect(throws: WeChatNativeArchiveError.noRecognizableTranscript) {
            _ = try WeChatNativeArchiveReader.read(contentsOf: url, limits: limits)
        }
    }

    @Test
    func picksTheRecognizableTranscriptWithTheMostMessages() throws {
        let scratch = try Scratch()
        let url = try scratch.zip { builder in
            builder.add("readme.txt", "not a transcript at all")
            builder.add("small.txt", transcript([("张三", m35, "1")]))
            builder.add("big.txt", transcript([
                ("张三", m35, "1"), ("李四", m36, "2"), ("王五", m36, "3"),
            ]))
        }
        let archive = try WeChatNativeArchiveReader.read(contentsOf: url)
        #expect(archive.transcriptEntryName == "big.txt")
        #expect(archive.messages.count == 3)
        // Two candidates parsed; neither was concatenated into the other.
        #expect(archive.transcriptCandidateCount == 2)
    }
}

// MARK: - Boundary

struct WeChatNativeArchiveBoundaryTests {
    @Test
    func readingAnArchiveWritesNothingAnywhere() throws {
        let scratch = try Scratch()
        let url = try scratch.zip { builder in
            builder.add("聊天记录.txt", transcript([("张三", m35, "哈哈")]), deflated: true)
            builder.add("images/1.jpg", "not really a jpeg")
        }
        let before = try FileManager.default.contentsOfDirectory(atPath: scratch.url.path).sorted()
        _ = try WeChatNativeArchiveReader.read(contentsOf: url)
        let after = try FileManager.default.contentsOfDirectory(atPath: scratch.url.path).sorted()
        #expect(before == after)
        // The app's own locations are untouched: this path has no store.
        #expect(!FileManager.default.fileExists(
            atPath: MemoryStoreLocation.directory.appendingPathComponent("archive").path
        ))
    }

    @Test
    func theArchiveReaderContainsNoPersistenceOrNetworkCalls() throws {
        // Structural, in the spirit of `compiledSourceContainsNoActiveControlAPIs`
        // and F-008: the guarantee that this path stores nothing is a property
        // of the source, not of anyone's intention to keep it that way.
        let directory = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("WeChatCompanion/Ingestion/Archive")
        let files = try FileManager.default
            .contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "swift" }
        #expect(!files.isEmpty)
        let source = try files.map { try String(contentsOf: $0, encoding: .utf8) }.joined()

        for forbidden in [
            "import SQLite3", "sqlite3_", "createDirectory", "FileHandle(forWritingTo",
            "applicationSupportDirectory", "URLSession", "Process(", "NSWorkspace",
        ] {
            #expect(!source.contains(forbidden), "archive reader must not use \(forbidden)")
        }
    }

    @Test
    func aSummaryReportsShapeAndNeverContent() throws {
        let scratch = try Scratch()
        let secret = "私密内容不应出现在报告里"
        let url = try scratch.zip { builder in
            builder.add("聊天记录.txt", transcript([("张三", m35, secret)]), deflated: true)
            builder.add("images/1.jpg", "x")
        }
        let summary = WeChatNativeArchiveSummary(
            try WeChatNativeArchiveReader.read(contentsOf: url)
        )
        let report = summary.reportLines.joined(separator: "\n")
        #expect(!report.contains(secret))
        #expect(!report.contains("张三"))
        #expect(report.contains("message count: 1"))
        #expect(report.contains("first timestamp: \(m35)"))
        #expect(report.contains("jpg=1"))
    }
}

// MARK: - Real-archive acceptance seam

/// Opt-in acceptance against a real WeChat export.
///
/// Real chat archives are never committed (repository hygiene: every fixture in
/// this repo is synthetic). Point `DUKOU_FIXTURE_PATH` at a real export to run
/// this; with the variable unset it skips, so CI and every other developer are
/// unaffected.
///
///     TEST_RUNNER_DUKOU_FIXTURE_PATH=/path/to/real.zip xcodebuild test ...
///
/// `xcodebuild` does not hand its own environment to the test host, so the
/// variable is read under both names: `DUKOU_FIXTURE_PATH` when the test runs
/// from Xcode with a scheme variable, and the `TEST_RUNNER_`-prefixed form that
/// `xcodebuild` does forward. Without either the test is **skipped**, not
/// silently passed -- a green run that never opened the archive would be the
/// worst possible outcome for an acceptance gate.
///
/// The output is `WeChatNativeArchiveSummary.reportLines` and nothing else:
/// shape, counts, the two bounding timestamps, attachment counts. No message
/// text, no sender and no chat title is printed, so the result of an acceptance
/// run can be pasted into a report.
struct WeChatNativeArchiveFixtureTests {
    static var fixturePath: String? {
        let environment = ProcessInfo.processInfo.environment
        for key in ["DUKOU_FIXTURE_PATH", "TEST_RUNNER_DUKOU_FIXTURE_PATH"] {
            if let value = environment[key], !value.isEmpty { return value }
        }
        return nil
    }

    @Test(.enabled(if: fixturePath != nil, "no DUKOU_FIXTURE_PATH: real-archive gate not run"))
    func realArchiveAcceptance() throws {
        let path = try #require(Self.fixturePath)
        let url = URL(fileURLWithPath: path)
        let summary: WeChatNativeArchiveSummary
        do {
            summary = WeChatNativeArchiveSummary(
                try WeChatNativeArchiveReader.read(contentsOf: url)
            )
        } catch {
            let reason = "\(type(of: error)).\(error)"
            print("DUKOU FIXTURE\n" + WeChatNativeArchiveSummary(failure: reason)
                .reportLines.joined(separator: "\n"))
            throw error
        }
        print("DUKOU FIXTURE\n" + summary.reportLines.joined(separator: "\n"))
        #expect(summary.isValid)
        #expect(summary.messageCount > 0)
    }
}
