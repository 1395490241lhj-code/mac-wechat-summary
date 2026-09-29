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
        /// Written into the local file header only, leaving the central
        /// directory saying something else.
        var localMethodOverride: UInt16?
        var localNameOverride: String?
        var localExtraLengthOverride: UInt16?
    }

    var entries: [Entry] = []
    /// Bytes after the end-of-central-directory record.
    var comment = Data()
    var commentLengthOverride: UInt16?
    var entryCountOverride: UInt16?
    var entriesOnDiskOverride: UInt16?

    mutating func add(
        _ name: String,
        _ text: String,
        deflated: Bool = false,
        method: UInt16? = nil,
        flags: UInt16 = 0,
        isSymlink: Bool = false,
        crcOverride: UInt32? = nil,
        uncompressedSizeOverride: UInt32? = nil,
        localMethodOverride: UInt16? = nil,
        localNameOverride: String? = nil,
        localExtraLengthOverride: UInt16? = nil
    ) {
        add(
            name,
            Data(text.utf8),
            deflated: deflated,
            method: method,
            flags: flags,
            isSymlink: isSymlink,
            crcOverride: crcOverride,
            uncompressedSizeOverride: uncompressedSizeOverride,
            localMethodOverride: localMethodOverride,
            localNameOverride: localNameOverride,
            localExtraLengthOverride: localExtraLengthOverride
        )
    }

    mutating func add(
        _ name: String,
        _ body: Data,
        deflated: Bool = false,
        method: UInt16? = nil,
        flags: UInt16 = 0,
        isSymlink: Bool = false,
        crcOverride: UInt32? = nil,
        uncompressedSizeOverride: UInt32? = nil,
        localMethodOverride: UInt16? = nil,
        localNameOverride: String? = nil,
        localExtraLengthOverride: UInt16? = nil
    ) {
        entries.append(Entry(
            name: name,
            body: body,
            deflated: deflated,
            method: method,
            flags: flags,
            isSymlink: isSymlink,
            crcOverride: crcOverride,
            uncompressedSizeOverride: uncompressedSizeOverride,
            localMethodOverride: localMethodOverride,
            localNameOverride: localNameOverride,
            localExtraLengthOverride: localExtraLengthOverride
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

            let localName = entry.localNameOverride.map { Data($0.utf8) } ?? name
            file += Self.u32(0x0403_4B50)
            file += Self.u16(20) + Self.u16(entry.flags)
            file += Self.u16(entry.localMethodOverride ?? method)
            file += Self.u16(0) + Self.u16(0)
            file += Self.u32(checksum) + Self.u32(UInt32(payload.count)) + Self.u32(uncompressed)
            file += Self.u16(UInt16(localName.count))
            file += Self.u16(entry.localExtraLengthOverride ?? 0)
            file += localName
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
        file += Self.u16(entriesOnDiskOverride ?? UInt16(entries.count))
        file += Self.u16(entryCountOverride ?? UInt16(entries.count))
        file += Self.u32(UInt32(directory.count)) + Self.u32(directoryOffset)
        file += Self.u16(commentLengthOverride ?? UInt16(comment.count))
        file += comment
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

/// Shape A: `·sender` / date / body / blank.
private func transcript(_ rows: [(String, String, String)], newline: String = "\n") -> String {
    rows.map { "·\($0.0)\n\($0.1)\n\($0.2)\n\n" }.joined()
        .replacingOccurrences(of: "\n", with: newline)
}

/// Shape B: `·record` / blank. Mirrors the real archive exactly -- one blank
/// line between records and none after the last.
private func unattributed(_ records: [String], newline: String = "\n") -> String {
    records.map { "·\($0)" }.joined(separator: "\n\n")
        .appending("\n")
        .replacingOccurrences(of: "\n", with: newline)
}

private func isoBMFF(
    majorBrand: String,
    compatibleBrands: [String] = []
) -> Data {
    precondition(majorBrand.utf8.count == 4)
    precondition(compatibleBrands.allSatisfy { $0.utf8.count == 4 })
    let size = UInt32(16 + compatibleBrands.count * 4)
    var data = Data([
        UInt8((size >> 24) & 0xFF),
        UInt8((size >> 16) & 0xFF),
        UInt8((size >> 8) & 0xFF),
        UInt8(size & 0xFF),
    ])
    data.append(Data("ftyp".utf8))
    data.append(Data(majorBrand.utf8))
    data.append(Data([0, 0, 0, 0]))
    for brand in compatibleBrands {
        data.append(Data(brand.utf8))
    }
    return data
}

private let tinyJPEG = Data([
    0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46,
])
private let tinyPNG = Data([
    0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00,
])

/// Shape A messages, or a test failure if the transcript was another shape.
private func attributedMessages(_ t: WeChatNativeTranscript) -> [WeChatAttributedArchiveMessage] {
    guard case .attributed(let a) = t else { return [] }
    return a.messages
}

private func unattributedRecords(_ t: WeChatNativeTranscript) -> [WeChatUnattributedArchiveRecord] {
    guard case .unattributed(let u) = t else { return [] }
    return u.records
}

/// A directory that exists only for the duration of one test.
private struct Scratch: ~Copyable {
    let url: URL
    /// B5.1 fixtures hand the scratch directory to a resolver that outlives
    /// the closure which made it, so cleanup moves to an explicit call. A
    /// noncopyable scratch cannot ride along inside a returned tuple.
    private final class Cleanup {
        var onDeinit = true
    }
    private let cleanup = Cleanup()

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

    func abandonCleanup() { cleanup.onDeinit = false }

    deinit {
        if cleanup.onDeinit { try? FileManager.default.removeItem(at: url) }
    }
}

// MARK: - Shape A (attributed)

struct WeChatAttributedTranscriptTests {
    private func parse(_ body: String, tz: TimeZone = WeChatNativeTranscriptParser.defaultTimeZone) throws -> [WeChatAttributedArchiveMessage] {
        let t = try WeChatNativeTranscriptParser.parse(body, timeZone: tz)
        #expect(t.shapeName == "attributed")
        #expect(t.attributionAvailable)
        #expect(t.perMessageTimeAvailable)
        return attributedMessages(t)
    }

    @Test
    func parsesOrdinaryMessagesInOrder() throws {
        let messages = try parse(transcript([("张三", m35, "哈哈"), ("李四", m36, "收到")]))
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
        let messages = try parse(body)
        #expect(messages[0].text == "第一行\n  第二行有前导空格\n\n第四行在空行之后")
        #expect(messages[1].text == "尾巴")
    }

    @Test
    func keepsAnEmptyBodyAsAnEmptyString() throws {
        let messages = try parse("·张三\n\(m35)\n\n·李四\n\(m36)\n收到\n")
        #expect(messages.count == 2)
        #expect(messages[0].text.isEmpty)
        #expect(messages[1].text == "收到")
    }

    @Test
    func acceptsCRLFLineEndings() throws {
        let messages = try parse(transcript([("张三", m35, "哈哈"), ("李四", m36, "收到")], newline: "\r\n"))
        #expect(messages.map(\.text) == ["哈哈", "收到"])
    }

    @Test
    func acceptsAUTF8ByteOrderMark() throws {
        let messages = try parse("\u{FEFF}" + transcript([("张三", m35, "哈哈")]))
        #expect(messages.count == 1)
        #expect(messages[0].sender == "张三")
    }

    @Test
    func preservesUnicodeAndEmojiInSendersAndBodies() throws {
        let messages = try parse(transcript([
            ("李·四 🐉", m35, "你好 🌍"), ("Ann O\'Neill", m36, "Ünïcødé — ok"),
        ]))
        #expect(messages[0].sender == "李·四 🐉")
        #expect(messages[0].text == "你好 🌍")
        #expect(messages[1].sender == "Ann O\'Neill")
        #expect(messages[1].text == "Ünïcødé — ok")
    }

    @Test
    func repeatedIdenticalMessagesAreAllPreserved() throws {
        let messages = try parse(transcript(Array(repeating: ("张三", m35, "哈哈"), count: 3)))
        #expect(messages.count == 3)
        #expect(messages.map(\.sequence) == [0, 1, 2])
    }

    @Test
    func identicalSenderTimeAndTextDoNotCollapse() throws {
        let messages = try parse(transcript([
            ("张三", m35, "哈哈"), ("张三", m35, "哈哈"), ("李四", m35, "哈哈"),
        ]))
        #expect(messages.count == 3)
        #expect(messages[0].sentAt == messages[2].sentAt)
    }

    @Test
    func senderIsKeptVerbatimAndNeverTrimmed() throws {
        let messages = try parse(transcript([(" 张三 ", m35, "哈哈"), ("李\u{00A0}四", m36, "收到")]))
        #expect(messages[0].sender == " 张三 ")
        #expect(messages[1].sender == "李\u{00A0}四")
    }

    @Test
    func aByteOrderMarkIsStrippedOnlyAtTheStart() throws {
        let interior = "前\u{FEFF}后"
        let messages = try parse("\u{FEFF}" + transcript([("张三", m35, interior), ("李四", m36, "\u{FEFF}开头")]))
        #expect(messages[0].text == interior)
        #expect(messages[1].text == "\u{FEFF}开头")
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
        #expect(try parse("\n\n  \n" + transcript([("张三", m35, "x")])).count == 1)
    }
}

// MARK: - Shape B (unattributed)

struct WeChatUnattributedTranscriptTests {
    private func parse(_ body: String) throws -> [WeChatUnattributedArchiveRecord] {
        let t = try WeChatNativeTranscriptParser.parse(body)
        #expect(t.shapeName == "unattributed")
        // The whole point of the sum type: no attribution can be read out,
        // because there is nowhere to read it from.
        #expect(!t.attributionAvailable)
        #expect(!t.perMessageTimeAvailable)
        return unattributedRecords(t)
    }

    @Test
    func parsesOrdinaryRecordsInOrder() throws {
        let records = try parse(unattributed(["哈哈", "收到", "好的"]))
        #expect(records.count == 3)
        #expect(records.map(\.sequence) == [0, 1, 2])
    }

    @Test
    func keepsTheLeadingMarkerBecauseNothingProvesItIsNotPayload() throws {
        // In Shape A the `·` is unambiguously a marker: a sender follows it.
        // Shape B proves no such thing, and deleting a byte WeChat wrote on an
        // analogy is worse than keeping one that may be structural.
        let records = try parse(unattributed(["哈哈"]))
        #expect(records[0].recordText == "·哈哈")
        #expect(records[0].recordText.hasPrefix("·"))
    }

    @Test
    func repeatedIdenticalRecordsAreAllPreserved() throws {
        let records = try parse(unattributed(Array(repeating: "哈哈", count: 4)))
        #expect(records.count == 4)
        #expect(Set(records.map(\.recordText)).count == 1)
        #expect(records.map(\.sequence) == [0, 1, 2, 3])
    }

    @Test
    func preservesUnicodeEmojiAndMeaningfulWhitespace() throws {
        // The real archive has records whose payload carries leading and
        // trailing spaces. Trimming would rewrite what the user sent.
        let payloads = ["你好 🌍", "  前导空格", "尾随空格  ", "Ünïcødé — ok", "·看起来像标记"]
        let records = try parse(unattributed(payloads))
        #expect(records.map(\.recordText) == payloads.map { "·" + $0 })
    }

    @Test
    func acceptsCRLFAndALeadingByteOrderMark() throws {
        let crlf = try parse(unattributed(["哈哈", "收到"], newline: "\r\n"))
        #expect(crlf.count == 2)
        let bom = try parse("\u{FEFF}" + unattributed(["哈哈"]))
        #expect(bom.count == 1)
        // Interior U+FEFF is content, not an encoding marker.
        let interior = try parse(unattributed(["前\u{FEFF}后"]))
        #expect(interior[0].recordText == "·前\u{FEFF}后")
    }

    @Test
    func toleratesLeadingBlankLinesAndTheFinalNewline() throws {
        #expect(try parse("\n\n" + unattributed(["哈哈", "收到"])).count == 2)
    }

    @Test
    func thereIsNoSenderOrTimestampFieldToRead() throws {
        // Structural, not behavioural: the record type has exactly two stored
        // properties, so no caller can obtain attribution from Shape B.
        let record = try parse(unattributed(["哈哈"]))[0]
        #expect(record == WeChatUnattributedArchiveRecord(sequence: 0, recordText: "·哈哈"))
    }

    @Test
    func rejectsTwoBlankLinesBetweenRecords() {
        #expect(throws: WeChatTranscriptError.notANativeTranscript) {
            try WeChatNativeTranscriptParser.parse("·哈哈\n\n\n·收到\n")
        }
    }

    @Test
    func rejectsRecordsWithNoBlankSeparator() {
        #expect(throws: WeChatTranscriptError.notANativeTranscript) {
            try WeChatNativeTranscriptParser.parse("·哈哈\n·收到\n")
        }
    }

    @Test
    func rejectsANonMarkerLine() {
        #expect(throws: WeChatTranscriptError.notANativeTranscript) {
            try WeChatNativeTranscriptParser.parse("·哈哈\n\n随便一行\n\n·收到\n")
        }
    }
}

// MARK: - Discrimination

struct WeChatTranscriptDiscriminationTests {
    @Test
    func validShapeAClassifiesAsAttributed() throws {
        let t = try WeChatNativeTranscriptParser.parse(transcript([("张三", m35, "哈哈")]))
        #expect(t.shapeName == "attributed")
        #expect(t.recordCount == 1)
    }

    @Test
    func validShapeBClassifiesAsUnattributed() throws {
        let t = try WeChatNativeTranscriptParser.parse(unattributed(["哈哈", "收到"]))
        #expect(t.shapeName == "unattributed")
        #expect(t.recordCount == 2)
    }

    @Test
    func shapeAIsNeverDowngradedToShapeB() throws {
        // Shape B would have to see the date line as a record; it must not.
        for rows in [[("张三", m35, "哈哈")],
                     [("张三", m35, "哈哈"), ("李四", m36, "收到")]] {
            let t = try WeChatNativeTranscriptParser.parse(transcript(rows))
            #expect(t.shapeName == "attributed")
        }
    }

    @Test
    func shapeBIsNeverTreatedAsShapeA() throws {
        // A Shape B payload that merely *contains* a date must not promote the
        // record to an attributed message.
        let t = try WeChatNativeTranscriptParser.parse(unattributed(["哈哈", "开会时间 \(m35)"]))
        #expect(t.shapeName == "unattributed")
        #expect(t.recordCount == 2)
    }

    @Test
    func aHybridFailsClosedRatherThanSwallowingRecords() {
        // One attributed record followed by an unattributed one. Parsed as
        // Shape A this silently absorbs the second into the first's body --
        // exactly the failure the two-parser design exists to prevent.
        let hybrid = "·张三\n\(m35)\n哈哈\n\n·没有署名的记录\n"
        #expect(throws: WeChatTranscriptError.notANativeTranscript) {
            try WeChatNativeTranscriptParser.parse(hybrid)
        }
    }

    @Test
    func aShapeBRecordFollowedByAnAttributedOneAlsoFails() {
        let hybrid = "·没有署名的记录\n\n·张三\n\(m35)\n哈哈\n"
        #expect(throws: WeChatTranscriptError.notANativeTranscript) {
            try WeChatNativeTranscriptParser.parse(hybrid)
        }
    }

    @Test
    func anUnknownTextFailsClosed() {
        for text in ["", "just some notes", "导出说明\n更多说明", "· \n· \n· "] where text != "· \n· \n· " {
            #expect(throws: WeChatTranscriptError.notANativeTranscript) {
                try WeChatNativeTranscriptParser.parse(text)
            }
        }
    }
}

// MARK: - Time semantics (Shape A only)

struct WeChatArchiveTimeSemanticsTests {
    private func parse(_ body: String, tz: TimeZone) throws -> [WeChatAttributedArchiveMessage] {
        attributedMessages(try WeChatNativeTranscriptParser.parse(body, timeZone: tz))
    }

    @Test
    func parsesToMinutePrecisionInAnExplicitTimeZone() throws {
        let shanghai = TimeZone(identifier: "Asia/Shanghai")!
        let messages = try parse(transcript([("张三", m35, "哈哈")]), tz: shanghai)
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = shanghai
        let parts = calendar.dateComponents([.year, .month, .day, .hour, .minute, .second], from: messages[0].sentAt)
        #expect(parts.year == 2026)
        #expect(parts.month == 9)
        #expect(parts.day == 7)
        #expect(parts.hour == 20)
        #expect(parts.minute == 35)
        // A native export states a minute, never a second.
        #expect(parts.second == 0)
    }

    @Test
    func theSameTextInTwoTimeZonesIsTwoDifferentInstants() throws {
        let a = try parse(transcript([("张三", m35, "x")]), tz: TimeZone(identifier: "Asia/Shanghai")!)[0]
        let b = try parse(transcript([("张三", m35, "x")]), tz: TimeZone(identifier: "America/New_York")!)[0]
        #expect(a.sentAt != b.sentAt)
        #expect(a.sentAtText == b.sentAtText)
    }

    @Test
    func aSingleDigitHourParses() throws {
        let messages = try parse(transcript([("张三", "2026年9月7日 9:05", "早")]), tz: .current)
        #expect(messages.count == 1)
        #expect(messages[0].sentAtText == "2026年9月7日 9:05")
    }

    @Test
    func theParsedInstantDoesNotDependOnTheUsersCalendarPreference() {
        let formatter = WeChatNativeTranscriptParser.formatter(timeZone: TimeZone(identifier: "Asia/Shanghai")!)
        #expect(formatter.calendar.identifier == .gregorian)
        #expect(formatter.locale.identifier == "en_US_POSIX")
        #expect(!formatter.isLenient)
    }

    @Test
    func anUnattributedTranscriptExposesNoTimeAtAll() throws {
        let t = try WeChatNativeTranscriptParser.parse(unattributed(["哈哈"]))
        #expect(!t.perMessageTimeAvailable)
        #expect(attributedMessages(t).isEmpty)
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
        #expect(archive.recordCount == 2)
        #expect(archive.entryCount == 3)
        #expect(archive.transcriptCandidateCount == 1)
        #expect(archive.attachmentCountsByExtension == ["jpg": 1, "mp4": 1])
    }

    @Test
    func readsAStoredUncompressedTranscript() throws {
        let scratch = try Scratch()
        let url = try scratch.zip { $0.add("chat.txt", transcript([("张三", m35, "哈哈")])) }
        #expect(try WeChatNativeArchiveReader.read(contentsOf: url).recordCount == 1)
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
    func anOversizedEntryCountIsRefusedBeforeAnyRecordIsParsed() throws {
        // The EOCD claims 5000 entries; the central directory holds one. A
        // reader that gated the count *after* the parse loop would walk off the
        // end of the directory and report `malformedCentralDirectory`. Getting
        // `entryCountOutsideSupportedRange` instead is the proof that the limit
        // ran first and nothing was parsed or allocated.
        let scratch = try Scratch()
        var builder = ZIPBuilder()
        builder.add("聊天记录.txt", transcript([("张三", m35, "x")]))
        builder.entryCountOverride = 5_000
        builder.entriesOnDiskOverride = 5_000
        let url = scratch.url.appendingPathComponent("many.zip")
        try builder.write(to: url)

        expectArchiveError(.entryCountOutsideSupportedRange) {
            _ = try WeChatNativeArchiveReader.read(contentsOf: url)
        }
    }

    @Test
    func everyEntryIsVerified_notJustTheTranscript() throws {
        // The transcript is intact; an attachment is not. An accepted archive
        // means the *whole* archive was verified, so this must be refused.
        let scratch = try Scratch()
        let url = try scratch.zip { builder in
            builder.add("聊天记录.txt", transcript([("张三", m35, "x")]), deflated: true)
            builder.add("images/1.jpg", "corrupted attachment", crcOverride: 0xDEAD_BEEF)
        }
        expectArchiveError(.integrityCheckFailed) {
            _ = try WeChatNativeArchiveReader.read(contentsOf: url)
        }
    }

    @Test
    func anEOCDSignatureInsideTheCommentDoesNotReplaceTheRealRecord() throws {
        // The comment sits after the record, so a backwards scan finds a
        // planted signature first. Only the record whose own comment length
        // lands exactly on end-of-file is the real one.
        let scratch = try Scratch()
        var builder = ZIPBuilder()
        builder.add("聊天记录.txt", transcript([("张三", m35, "哈哈"), ("李四", m36, "收到")]))
        builder.comment = Data([0x50, 0x4B, 0x05, 0x06]) + Data(repeating: 0, count: 26)
        let url = scratch.url.appendingPathComponent("planted.zip")
        try builder.write(to: url)

        let archive = try WeChatNativeArchiveReader.read(contentsOf: url)
        #expect(archive.recordCount == 2)
    }

    @Test
    func mismatchedDiskEntryCountsAreRejected() throws {
        let scratch = try Scratch()
        var builder = ZIPBuilder()
        builder.add("聊天记录.txt", transcript([("张三", m35, "x")]))
        builder.entriesOnDiskOverride = 2  // the total still says 1
        let url = scratch.url.appendingPathComponent("disks.zip")
        try builder.write(to: url)

        expectArchiveError(.inconsistentEndOfCentralDirectory) {
            _ = try WeChatNativeArchiveReader.read(contentsOf: url)
        }
    }

    @Test
    func aCommentLengthThatDoesNotReachEndOfFileIsRejected() throws {
        let scratch = try Scratch()
        var builder = ZIPBuilder()
        builder.add("聊天记录.txt", transcript([("张三", m35, "x")]))
        builder.comment = Data(repeating: 0x41, count: 8)
        builder.commentLengthOverride = 3  // claims 3, wrote 8
        let url = scratch.url.appendingPathComponent("comment.zip")
        try builder.write(to: url)

        expectArchiveError(.notAZIPArchive) {
            _ = try WeChatNativeArchiveReader.read(contentsOf: url)
        }
    }

    @Test
    func aLocalHeaderMethodThatContradictsTheCentralDirectoryIsRejected() throws {
        let scratch = try Scratch()
        let url = try scratch.zip {
            // Central directory says deflate; the local header claims stored.
            $0.add(
                "聊天记录.txt", transcript([("张三", m35, "x")]),
                deflated: true, localMethodOverride: 0
            )
        }
        expectArchiveError(.inconsistentLocalHeader) {
            _ = try WeChatNativeArchiveReader.read(contentsOf: url)
        }
    }

    @Test
    func aLocalHeaderNameThatContradictsTheCentralDirectoryIsRejected() throws {
        let scratch = try Scratch()
        let url = try scratch.zip {
            // Same byte length, different bytes: caught by comparison, not size.
            $0.add(
                "聊天记录.txt", transcript([("张三", m35, "x")]),
                localNameOverride: "聊天纪录.txt"
            )
        }
        expectArchiveError(.inconsistentLocalHeader) {
            _ = try WeChatNativeArchiveReader.read(contentsOf: url)
        }
    }

    @Test
    func anEntryWhoseDataRunsPastEndOfFileIsRejected() throws {
        let scratch = try Scratch()
        let url = try scratch.zip {
            // A huge declared extra field pushes the data start past the file.
            $0.add(
                "聊天记录.txt", transcript([("张三", m35, "x")]),
                localExtraLengthOverride: 60_000
            )
        }
        expectArchiveError(.malformedLocalHeader) {
            _ = try WeChatNativeArchiveReader.read(contentsOf: url)
        }
    }

    @Test
    func anUnattributedArchiveIsValidAndRecognized_notInvalid() throws {
        // The earlier reader reported a real Shape B export as
        // `noRecognizableTranscript`, i.e. an invalid archive. It is neither.
        let scratch = try Scratch()
        let url = try scratch.zip { builder in
            builder.add("聊天记录.txt", unattributed(["哈哈", "收到", "好的"]), deflated: true)
            builder.add("images/1.jpg", "not really a jpeg")
        }
        let archive = try WeChatNativeArchiveReader.read(contentsOf: url)
        #expect(archive.transcriptShape == "unattributed")
        #expect(archive.recordCount == 3)
        #expect(!archive.attributionAvailable)
        #expect(!archive.perMessageTimeAvailable)
        #expect(archive.attributedMessages == nil)
        #expect(archive.unattributedRecords?.count == 3)
        #expect(archive.timestampBounds == nil)

        let summary = WeChatNativeArchiveSummary(archive)
        let report = summary.reportLines.joined(separator: "\n")
        #expect(report.contains("container valid: yes"))
        #expect(report.contains("transcript recognized: yes"))
        #expect(report.contains("transcript shape: unattributed"))
        #expect(report.contains("record count: 3"))
        #expect(report.contains("attribution available: no"))
        #expect(report.contains("per-message time available: no"))
        // No timestamp line may appear for a shape that has no timestamps.
        #expect(!report.contains("first timestamp"))
        #expect(!report.contains("interpreted in timezone"))
    }

    @Test
    func anAttributedArchiveReportsAttributionAndBounds() throws {
        let scratch = try Scratch()
        let url = try scratch.zip {
            $0.add("聊天记录.txt", transcript([("张三", m35, "哈哈"), ("李四", m36, "收到")]), deflated: true)
        }
        let archive = try WeChatNativeArchiveReader.read(contentsOf: url)
        #expect(archive.transcriptShape == "attributed")
        #expect(archive.recordCount == 2)
        #expect(archive.attributionAvailable)
        #expect(archive.perMessageTimeAvailable)
        #expect(archive.unattributedRecords == nil)
        let report = WeChatNativeArchiveSummary(archive).reportLines.joined(separator: "\n")
        #expect(report.contains("transcript shape: attributed"))
        #expect(report.contains("first timestamp: \(m35)"))
        #expect(report.contains("last timestamp: \(m36)"))
    }

    @Test
    func aSingleCandidateOfEitherShapeIsUsed() throws {
        let scratch = try Scratch()
        let a = try scratch.zip("a.zip") { $0.add("c.txt", transcript([("张三", m35, "哈哈")])) }
        #expect(try WeChatNativeArchiveReader.read(contentsOf: a).transcriptShape == "attributed")
        let b = try scratch.zip("b.zip") { $0.add("c.txt", unattributed(["哈哈"])) }
        #expect(try WeChatNativeArchiveReader.read(contentsOf: b).transcriptShape == "unattributed")
    }

    @Test
    func mixedShapeCandidatesFailClosed() throws {
        // Choosing the longer one would be choosing between "who said what
        // when" and "some ordered text" on the basis of length.
        let scratch = try Scratch()
        let url = try scratch.zip { builder in
            builder.add("a.txt", transcript([("张三", m35, "哈哈")]))
            builder.add("b.txt", unattributed(["1", "2", "3", "4", "5"]))
        }
        #expect(throws: WeChatNativeArchiveError.ambiguousTranscriptCandidates) {
            _ = try WeChatNativeArchiveReader.read(contentsOf: url)
        }
    }

    @Test
    func severalCandidatesOfOneShapeTakeTheRichest() throws {
        let scratch = try Scratch()
        let url = try scratch.zip { builder in
            builder.add("small.txt", unattributed(["1"]))
            builder.add("big.txt", unattributed(["1", "2", "3"]))
        }
        let archive = try WeChatNativeArchiveReader.read(contentsOf: url)
        #expect(archive.transcriptShape == "unattributed")
        #expect(archive.recordCount == 3)
        #expect(archive.transcriptCandidateCount == 2)
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
        #expect(archive.recordCount == 3)
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
            builder.add("与某人的聊天记录.txt", transcript([("张三", m35, secret)]), deflated: true)
            builder.add("images/1.jpg", "x")
        }
        let summary = WeChatNativeArchiveSummary(
            try WeChatNativeArchiveReader.read(contentsOf: url)
        )
        let report = summary.reportLines.joined(separator: "\n")
        #expect(!report.contains(secret))
        #expect(!report.contains("张三"))
        #expect(report.contains("record count: 1"))
        #expect(report.contains("first timestamp: \(m35)"))
        #expect(report.contains("jpg=1"))
    }

    @Test
    func theSummaryIdentifiesTheTranscriptWithoutNamingIt() throws {
        // Nobody has opened a real export, so whether a transcript filename
        // carries a chat title or a contact's name is unknown. The report must
        // not bet a private name on that assumption.
        let scratch = try Scratch()
        let revealingName = "与张三的聊天记录.txt"
        let url = try scratch.zip {
            $0.add(revealingName, transcript([("张三", m35, "哈哈")]))
        }
        let archive = try WeChatNativeArchiveReader.read(contentsOf: url)
        let report = WeChatNativeArchiveSummary(archive).reportLines.joined(separator: "\n")

        #expect(archive.transcriptEntryName == revealingName)  // known internally
        #expect(!report.contains(revealingName))               // never reported
        #expect(!report.contains("张三"))
        #expect(report.contains("extension=txt"))
        #expect(report.contains("index=0"))
    }

    @Test
    func theInventoryReportsShapeWithoutAnyFilename() throws {
        // Replaces pasting `unzip -l` of a private export: Phase B needs the
        // tree's shape, not WeChat's choice of names.
        let scratch = try Scratch()
        let url = try scratch.zip { builder in
            builder.add("与李四的聊天记录.txt", transcript([("李四", m35, "哈哈")]))
            builder.add("images/私密照片.jpg", String(repeating: "x", count: 2_048))
            builder.add("images/another.jpg", "y")
            builder.add("video/家庭录像.mp4", "z")
        }
        let inventory = try WeChatNativeArchiveInventory.read(contentsOf: url)
        let report = inventory.reportLines.joined(separator: "\n")

        #expect(inventory.fileCount == 4)
        #expect(inventory.maximumPathDepth == 2)
        #expect(inventory.topLevelDirectoryCount == 2)
        for name in ["与李四的聊天记录", "私密照片", "家庭录像", "another", "images", "video"] {
            #expect(!report.contains(name), "inventory must not contain \(name)")
        }
        #expect(report.contains("jpg: count=2"))
        #expect(report.contains("mp4: count=1"))
        // Sizes are bucketed, never exact.
        #expect(report.contains("<10KiB"))
        #expect(!report.contains("2048"))
    }

    @Test
    func theInventoryWorksOnAnArchiveWithNoRecognizableTranscript() throws {
        // The case most worth diagnosing on a real export is the one where the
        // transcript did not parse at all.
        let scratch = try Scratch()
        let url = try scratch.zip { builder in
            builder.add("readme.txt", "not a transcript")
            builder.add("data/blob.bin", "x")
        }
        #expect(throws: WeChatNativeArchiveError.noRecognizableTranscript) {
            _ = try WeChatNativeArchiveReader.read(contentsOf: url)
        }
        let inventory = try WeChatNativeArchiveInventory.read(contentsOf: url)
        #expect(inventory.fileCount == 2)
        #expect(inventory.topLevelDirectoryCount == 1)
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

        // The inventory first, and separately: it is the part that still says
        // something useful when the transcript does not parse, which is exactly
        // the failure worth diagnosing on a first real export.
        let inventory = try? WeChatNativeArchiveInventory.read(contentsOf: url)
        let shape = inventory?.reportLines.joined(separator: "\n") ?? "inventory: unavailable"

        let summary: WeChatNativeArchiveSummary
        do {
            summary = WeChatNativeArchiveSummary(
                try WeChatNativeArchiveReader.read(contentsOf: url)
            )
        } catch {
            let reason = "\(type(of: error)).\(error)"
            print("DUKOU FIXTURE\n" + shape + "\n" + WeChatNativeArchiveSummary(failure: reason, containerValid: inventory != nil)
                .reportLines.joined(separator: "\n"))
            throw error
        }
        print("DUKOU FIXTURE\n" + shape + "\n" + summary.reportLines.joined(separator: "\n"))
        #expect(summary.transcriptRecognized)
        #expect(summary.recordCount > 0)
    }
}




struct WeChatNativeArchiveAttachmentTests {
    @Test
    func materializeModeKeepsOnlyVerifiedSupportedBytes() throws {
        let scratch = try Scratch()
        let url = try scratch.zip { builder in
            builder.add("聊天记录.txt", transcript([("张三", m35, "x")]))
            builder.add("media/ok.jpg", tinyJPEG)
            builder.add("media/fake.jpg", Data("not a jpeg".utf8))
            builder.add("media/unknown.exe", Data([1, 2, 3, 4]))
        }

        let archive = try WeChatNativeArchiveReader.read(
            contentsOf: url,
            attachmentReadMode: .materializeSupported
        )

        #expect(archive.attachments.count == 3)
        let byIndex = Dictionary(uniqueKeysWithValues: archive.attachments.map {
            ($0.sourceEntryIndex, $0)
        })
        let materialized = try #require(byIndex.values.first(where: {
            $0.pathExtension == "jpg" && $0.disposition == .materializable
        }))
        #expect(materialized.kind == .image)
        #expect(materialized.canonicalExtension == "jpg")
        #expect(materialized.payload == tinyJPEG)
        #expect(materialized.contentSHA256?.count == 64)

        let mismatch = try #require(byIndex.values.first(where: {
            $0.pathExtension == "jpg" && $0.disposition == .typeMismatch
        }))
        #expect(mismatch.payload == nil)
        #expect(mismatch.contentSHA256 == nil)

        let unsupported = try #require(byIndex.values.first(where: {
            $0.pathExtension == "exe"
        }))
        #expect(unsupported.disposition == .unsupportedType)
        #expect(unsupported.payload == nil)
        #expect(unsupported.kind == nil)
    }

    @Test
    func inventoryOnlyNeverLoadsAttachmentEvidence() throws {
        let scratch = try Scratch()
        let url = try scratch.zip { builder in
            builder.add("聊天记录.txt", transcript([("张三", m35, "x")]))
            builder.add("images/one.jpg", tinyJPEG)
        }

        let archive = try WeChatNativeArchiveReader.read(contentsOf: url)

        #expect(archive.attachmentCountsByExtension == ["jpg": 1])
        #expect(archive.attachments.isEmpty)
    }

    @Test
    func isoBaseMediaBrandsMustMatchTheClaimedExtension() throws {
        let scratch = try Scratch()
        let url = try scratch.zip { builder in
            builder.add("聊天记录.txt", transcript([("张三", m35, "x")]))
            builder.add("media/good.heic", isoBMFF(majorBrand: "heic", compatibleBrands: ["mif1"]))
            builder.add("media/fake.heic", isoBMFF(majorBrand: "mp42", compatibleBrands: ["isom"]))
            builder.add("media/good.avif", isoBMFF(majorBrand: "avif", compatibleBrands: ["mif1"]))
            builder.add("media/fake.avif", isoBMFF(majorBrand: "heic", compatibleBrands: ["mif1"]))
            builder.add("media/good.mp4", isoBMFF(majorBrand: "mp42", compatibleBrands: ["isom"]))
            builder.add("media/fake.mp4", isoBMFF(majorBrand: "heic", compatibleBrands: ["mif1"]))
            builder.add("media/good.mov", isoBMFF(majorBrand: "qt  "))
            builder.add("media/fake.mov", isoBMFF(majorBrand: "mp42"))
        }

        let archive = try WeChatNativeArchiveReader.read(
            contentsOf: url,
            attachmentReadMode: .materializeSupported
        )
        let grouped = Dictionary(grouping: archive.attachments, by: \.pathExtension)

        for ext in ["heic", "avif", "mp4", "mov"] {
            let items = try #require(grouped[ext])
            #expect(items.count == 2)
            #expect(items.filter { $0.disposition == .materializable }.count == 1)
            #expect(items.filter { $0.disposition == .typeMismatch }.count == 1)
        }
    }

    @Test
    func perFileReadableLimitProducesMetadataOnlyOversizedEvidence() throws {
        let scratch = try Scratch()
        var largeJPEG = tinyJPEG
        largeJPEG.append(Data(repeating: 0xAA, count: 128))
        let url = try scratch.zip { builder in
            builder.add("聊天记录.txt", transcript([("张三", m35, "x")]))
            builder.add("images/large.jpg", largeJPEG)
        }
        var limits = ZIPArchiveReader.Limits.standard
        limits.maximumReadableEntryBytes = 96

        let archive = try WeChatNativeArchiveReader.read(
            contentsOf: url,
            limits: limits,
            attachmentReadMode: .materializeSupported
        )

        let item = try #require(archive.attachments.first)
        #expect(item.disposition == .oversized)
        #expect(item.payload == nil)
        #expect(item.contentSHA256 == nil)
    }

    @Test
    func materializationCountBudgetKeepsTheNewestPolicyBoundExplicit() throws {
        let scratch = try Scratch()
        let url = try scratch.zip { builder in
            builder.add("聊天记录.txt", transcript([("张三", m35, "x")]))
            for index in 0...100 {
                builder.add("images/\(index).jpg", tinyJPEG)
            }
        }

        let archive = try WeChatNativeArchiveReader.read(
            contentsOf: url,
            attachmentReadMode: .materializeSupported
        )

        #expect(archive.attachments.count == 101)
        #expect(archive.attachments.filter { $0.disposition == .materializable }.count == 100)
        #expect(archive.attachments.filter { $0.disposition == .budgetExceeded }.count == 1)
        #expect(archive.attachments.last?.disposition == .budgetExceeded)
        #expect(archive.attachments.last?.payload == nil)
        #expect(archive.attachments.last?.contentSHA256?.count == 64)
    }
}



struct ArchiveAttachmentStoreTests {
    @Test
    func importMaterializesIntoGeneratedPrivatePathsAndDeleteHistoryRemovesBytes() async throws {
        let scratch = try Scratch()
        let source = try scratch.zip { builder in
            builder.add("聊天记录.txt", transcript([("张三", m35, "x")]))
            builder.add("images/private-name.jpg", tinyJPEG)
            builder.add("images/fake.png", Data("not png".utf8))
        }
        let databaseURL = scratch.url.appendingPathComponent("messages.sqlite")
        let attachmentRoot = scratch.url.appendingPathComponent("archive-attachments", isDirectory: true)
        let history = LocalMessageHistory(
            url: databaseURL,
            attachmentRoot: attachmentRoot
        )
        await history.setEnabled(true)
        let service = WeChatArchiveImportService(history: history)

        let first = try await service.importArchive(
            contentsOf: source,
            importedAt: Date(timeIntervalSince1970: 1234)
        )
        let importID: Int64
        switch first.persistence {
        case .inserted(let id, _):
            importID = id
        case .alreadyImported:
            Issue.record("first import unexpectedly deduplicated")
            return
        }
        #expect(first.attachments == .inserted(attachmentCount: 2, materializedCount: 1))

        let summaries = await history.archiveEvidenceSnapshot().imports
        let summary = try #require(summaries.first)
        #expect(summary.id == importID)
        #expect(summary.attachmentBatchCount == 1)
        #expect(summary.attachmentCount == 2)
        #expect(summary.materializedAttachmentCount == 1)

        let batches = await history.archiveAttachmentBatches(importID: importID)
        let batch = try #require(batches.first)
        #expect(batch.attachmentCount == 2)
        #expect(batch.materializedCount == 1)
        #expect(batch.attachments.map(\.storageState).contains(.materialized))
        #expect(batch.attachments.map(\.storageState).contains(.typeMismatch))

        let importDirectory = attachmentRoot.appendingPathComponent(
            "import-\(importID)", isDirectory: true
        )
        let importChildren = try FileManager.default.contentsOfDirectory(
            at: importDirectory,
            includingPropertiesForKeys: nil
        )
        #expect(importChildren.count == 1)
        let batchDirectory = try #require(importChildren.first)
        #expect(batchDirectory.lastPathComponent.hasPrefix("batch-"))

        let storedFiles = try FileManager.default.contentsOfDirectory(
            at: batchDirectory,
            includingPropertiesForKeys: nil
        )
        #expect(storedFiles.count == 1)
        let storedFile = try #require(storedFiles.first)
        #expect(storedFile.lastPathComponent.hasSuffix(".jpg"))
        #expect(!storedFile.lastPathComponent.contains("private-name"))
        #expect(try Data(contentsOf: storedFile) == tinyJPEG)

        let rootMode = try FileManager.default.attributesOfItem(
            atPath: attachmentRoot.path
        )[.posixPermissions] as? NSNumber
        let importMode = try FileManager.default.attributesOfItem(
            atPath: importDirectory.path
        )[.posixPermissions] as? NSNumber
        let batchMode = try FileManager.default.attributesOfItem(
            atPath: batchDirectory.path
        )[.posixPermissions] as? NSNumber
        let fileMode = try FileManager.default.attributesOfItem(
            atPath: storedFile.path
        )[.posixPermissions] as? NSNumber
        #expect(rootMode?.intValue == 0o700)
        #expect(importMode?.intValue == 0o700)
        #expect(batchMode?.intValue == 0o700)
        #expect(fileMode?.intValue == 0o600)

        let second = try await service.importArchive(contentsOf: source)
        #expect(second.attachments == .alreadyPersisted(
            attachmentCount: 2,
            materializedCount: 1
        ))
        #expect(await history.archiveAttachmentBatches(importID: importID).count == 1)

        await history.deleteAllHistory()

        #expect(!FileManager.default.fileExists(atPath: attachmentRoot.path))
        #expect(FileManager.default.fileExists(atPath: source.path))
        #expect(await history.archiveEvidenceSnapshot().imports.isEmpty)
    }

    @Test
    func retentionSweepRemovesExpiredAttachmentBytesWithTheirImport() async throws {
        let scratch = try Scratch()
        let source = try scratch.zip { builder in
            builder.add("聊天记录.txt", transcript([("张三", m35, "x")]))
            builder.add("images/old.jpg", tinyJPEG)
        }
        let databaseURL = scratch.url.appendingPathComponent("messages.sqlite")
        let attachmentRoot = scratch.url.appendingPathComponent(
            "archive-attachments", isDirectory: true
        )
        let history = LocalMessageHistory(
            url: databaseURL,
            retention: .untilDeleted,
            attachmentRoot: attachmentRoot
        )
        await history.setEnabled(true)
        let service = WeChatArchiveImportService(history: history)
        let oldImportDate = Date(timeIntervalSinceNow: -(10 * 86_400))

        let outcome = try await service.importArchive(
            contentsOf: source,
            importedAt: oldImportDate
        )
        let importID: Int64
        switch outcome.persistence {
        case .inserted(let id, _):
            importID = id
        case .alreadyImported:
            Issue.record("expected a new import")
            return
        }

        let importDirectory = attachmentRoot.appendingPathComponent(
            "import-\(importID)", isDirectory: true
        )
        #expect(FileManager.default.fileExists(atPath: importDirectory.path))
        #expect(await history.archiveEvidenceSnapshot().imports.count == 1)

        await history.setRetention(.sevenDays)

        #expect(await history.archiveEvidenceSnapshot().imports.isEmpty)
        #expect(!FileManager.default.fileExists(atPath: importDirectory.path))
        #expect(FileManager.default.fileExists(atPath: attachmentRoot.path))
    }

    @Test
    func reconcileNeverFollowsImportOrRootSymlinks() throws {
        let scratch = try Scratch()
        let outside = scratch.url.appendingPathComponent("outside", isDirectory: true)
        try FileManager.default.createDirectory(at: outside, withIntermediateDirectories: true)
        let sentinel = outside.appendingPathComponent("sentinel")
        try Data("keep".utf8).write(to: sentinel)

        let root = scratch.url.appendingPathComponent("attachments", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let importLink = root.appendingPathComponent("import-1", isDirectory: true)
        try FileManager.default.createSymbolicLink(
            at: importLink,
            withDestinationURL: outside
        )

        let store = ArchiveAttachmentStore(rootURL: root)
        store.reconcile(validBatches: [1: [String(repeating: "a", count: 64)]])

        #expect(!FileManager.default.fileExists(atPath: importLink.path))
        #expect(FileManager.default.fileExists(atPath: sentinel.path))

        let rootLink = scratch.url.appendingPathComponent("root-link", isDirectory: true)
        try FileManager.default.createSymbolicLink(
            at: rootLink,
            withDestinationURL: outside
        )
        ArchiveAttachmentStore(rootURL: rootLink).reconcile(validBatches: [:])

        #expect(!FileManager.default.fileExists(atPath: rootLink.path))
        #expect(FileManager.default.fileExists(atPath: sentinel.path))
    }
}

/// B5.1 local viewing. The resolver is the whole security boundary here, so
/// these tests drive it directly with hostile paths as well as a real import.
struct ArchiveAttachmentPreviewResolverTests {
    /// Materializes one real JPEG into a real store and returns the read model
    /// rows plus the root, so each refusal can be built from real state.
    private func seeded() async throws -> (
        history: LocalMessageHistory,
        root: URL,
        store: ArchiveAttachmentStore,
        batches: [ArchiveEvidenceAttachmentBatch]
    ) {
        let scratch = try Scratch()
        let source = try scratch.zip { builder in
            builder.add("聊天记录.txt", transcript([("张三", m35, "x")]))
            builder.add("images/secret-name.jpg", tinyJPEG)
        }
        let root = scratch.url.appendingPathComponent("archive-attachments", isDirectory: true)
        let history = LocalMessageHistory(
            url: scratch.url.appendingPathComponent("messages.sqlite"),
            attachmentRoot: root
        )
        await history.setEnabled(true)
        let outcome = try await WeChatArchiveImportService(history: history)
            .importArchive(contentsOf: source)
        let importID: Int64
        switch outcome.persistence {
        case .inserted(let id, _): importID = id
        case .alreadyImported:
            Issue.record("expected a new import")
            throw CocoaError(.fileWriteUnknown)
        }
        let batches = await history.archiveAttachmentBatches(importID: importID)
        scratch.abandonCleanup()
        return (history, root, ArchiveAttachmentStore(rootURL: root), batches)
    }

    private func materializedRow(
        _ batches: [ArchiveEvidenceAttachmentBatch]
    ) throws -> ArchiveEvidenceAttachment {
        let row = try #require(
            batches.flatMap(\.attachments).first { $0.storageState == .materialized }
        )
        return row
    }

    @Test
    func aValidMaterializedAttachmentResolvesToItsGeneratedFile() async throws {
        let seeded = try await seeded()
        let row = try materializedRow(seeded.batches)
        let url = await seeded.history.archiveAttachmentPreviewURL(row)
        let resolved = try #require(url)
        #expect(resolved.path.hasSuffix(".jpg"))
        // Generated identity only: ordinal + content hash, never the ZIP name.
        #expect(!resolved.lastPathComponent.contains("secret-name"))
        #expect(FileManager.default.fileExists(atPath: resolved.path))
        // And it really is the bytes we stored, not a guess.
        #expect(try Data(contentsOf: resolved) == tinyJPEG)
    }

    @Test
    func aMetadataOnlyAttachmentRefusesToResolve() async throws {
        let scratch = try Scratch()
        let source = try scratch.zip { builder in
            builder.add("聊天记录.txt", transcript([("张三", m35, "x")]))
            builder.add("images/fake.png", Data("not png".utf8))
        }
        let root = scratch.url.appendingPathComponent("archive-attachments", isDirectory: true)
        let history = LocalMessageHistory(
            url: scratch.url.appendingPathComponent("messages.sqlite"),
            attachmentRoot: root
        )
        await history.setEnabled(true)
        let outcome = try await WeChatArchiveImportService(history: history)
            .importArchive(contentsOf: source)
        let importID: Int64
        switch outcome.persistence {
        case .inserted(let id, _): importID = id
        case .alreadyImported:
            Issue.record("expected a new import")
            throw CocoaError(.fileWriteUnknown)
        }
        let batches = await history.archiveAttachmentBatches(importID: importID)
        let metadataOnly = try #require(
            batches.flatMap(\.attachments).first { !$0.isMaterialized }
        )
        #expect(await history.archiveAttachmentPreviewURL(metadataOnly) == nil)
        // Even if someone handed the resolver a path, a metadata-only row has
        // no stored path to resolve in the first place.
        #expect(ArchiveAttachmentStore(rootURL: root).previewableFileURL(
            relativePath: nil
        ) == nil)
    }

    @Test
    func aMissingMaterializedFileRefusesWithoutRepairing() async throws {
        let seeded = try await seeded()
        let row = try materializedRow(seeded.batches)
        let url = try #require(await seeded.history.archiveAttachmentPreviewURL(row))
        try FileManager.default.removeItem(at: url)
        #expect(await seeded.history.archiveAttachmentPreviewURL(row) == nil)
        // Refusing is not repairing: the evidence row is untouched.
        let after = await seeded.history.archiveAttachmentBatches(
            importID: try #require(seeded.batches.first?.id)
        )
        #expect(after.flatMap(\.attachments).contains(where: { $0.id == row.id }))
    }

    @Test
    func aDotDotTraversalPathRefuses() throws {
        let scratch = try Scratch()
        let root = scratch.url.appendingPathComponent("root", isDirectory: true)
        let store = ArchiveAttachmentStore(rootURL: root)
        #expect(store.previewableFileURL(
            relativePath: "import-1/batch-\(String(repeating: "a", count: 64))/../../escape.jpg"
        ) == nil)
        #expect(store.previewableFileURL(
            relativePath: "../escape.jpg"
        ) == nil)
        #expect(store.previewableFileURL(relativePath: "") == nil)
        #expect(store.previewableFileURL(relativePath: nil) == nil)
    }

    @Test
    func anAbsoluteOrRootedPathRefuses() throws {
        let scratch = try Scratch()
        let root = scratch.url.appendingPathComponent("root", isDirectory: true)
        let store = ArchiveAttachmentStore(rootURL: root)
        // An absolute path must not escape just because it was appended.
        #expect(store.previewableFileURL(relativePath: "/etc/passwd") == nil)
    }

    @Test
    func aFinalTargetSymlinkRefuses() throws {
        let scratch = try Scratch()
        let outside = scratch.url.appendingPathComponent("outside", isDirectory: true)
        try FileManager.default.createDirectory(at: outside, withIntermediateDirectories: true)
        let real = outside.appendingPathComponent("real.jpg")
        try tinyJPEG.write(to: real)

        let root = scratch.url.appendingPathComponent("root", isDirectory: true)
        let batch = root.appendingPathComponent(
            "import-1/batch-\(String(repeating: "b", count: 64))", isDirectory: true
        )
        try FileManager.default.createDirectory(at: batch, withIntermediateDirectories: true)
        let link = batch.appendingPathComponent("1-link.jpg")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: real)

        let store = ArchiveAttachmentStore(rootURL: root)
        #expect(store.previewableFileURL(
            relativePath: "import-1/batch-\(String(repeating: "b", count: 64))/1-link.jpg"
        ) == nil)
    }

    @Test
    func anIntermediateSymlinkEscapeRefuses() throws {
        let scratch = try Scratch()
        let outside = scratch.url.appendingPathComponent("outside", isDirectory: true)
        try FileManager.default.createDirectory(at: outside, withIntermediateDirectories: true)
        try tinyJPEG.write(to: outside.appendingPathComponent("real.jpg"))

        let root = scratch.url.appendingPathComponent("root", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        // A whole import directory redirected out of the root.
        let importLink = root.appendingPathComponent("import-1", isDirectory: true)
        try FileManager.default.createSymbolicLink(at: importLink, withDestinationURL: outside)

        let store = ArchiveAttachmentStore(rootURL: root)
        #expect(store.previewableFileURL(relativePath: "import-1/real.jpg") == nil)
    }

    @Test
    func theRootItselfBeingASymlinkRefuses() throws {
        let scratch = try Scratch()
        let outside = scratch.url.appendingPathComponent("outside", isDirectory: true)
        try FileManager.default.createDirectory(at: outside, withIntermediateDirectories: true)
        try tinyJPEG.write(to: outside.appendingPathComponent("real.jpg"))
        let rootLink = scratch.url.appendingPathComponent("root-link", isDirectory: true)
        try FileManager.default.createSymbolicLink(at: rootLink, withDestinationURL: outside)

        // A root that is a symlink is a broken trust anchor, not a shortcut.
        let store = ArchiveAttachmentStore(rootURL: rootLink)
        #expect(store.previewableFileURL(relativePath: "real.jpg") == nil)
    }

    @Test
    func aDirectoryInsteadOfAFileRefuses() throws {
        let scratch = try Scratch()
        let root = scratch.url.appendingPathComponent("root", isDirectory: true)
        try FileManager.default.createDirectory(
            at: root.appendingPathComponent("import-1/thing.jpg"),
            withIntermediateDirectories: true
        )
        let store = ArchiveAttachmentStore(rootURL: root)
        #expect(store.previewableFileURL(relativePath: "import-1/thing.jpg") == nil)
    }

    @Test
    func theReadModelNeverCarriesAPathOrAHash() async throws {
        let seeded = try await seeded()
        let row = try materializedRow(seeded.batches)
        // The preview model is exactly the B5 surface: identity, type, size,
        // kind, storage state. No path, no hash, no source filename.
        let propertyNames = Mirror(reflecting: type(of: row)).children.compactMap {
            ($0.label ?? "").uppercased()
        }
        #expect(!propertyNames.contains { $0.contains("PATH") && $0 != "PATHEXTENSION" })
        #expect(!propertyNames.contains { $0.contains("HASH") || $0.contains("FINGERPRINT") })
        #expect(!propertyNames.contains { $0.contains("FILENAME") || $0.contains("NAME") })

        let fields = [
            String(describing: row.id),
            String(describing: row.sourceEntryIndex),
            row.pathExtension,
            String(describing: row.byteCount),
            String(describing: row.kind),
            String(describing: row.storageState),
        ]
        #expect(!fields.contains { $0.contains("import-") })
        #expect(!fields.contains { $0.contains("batch-") })
        #expect(!fields.contains { $0.contains("secret-name") })
        #expect(!fields.contains { $0.count == 64 && $0.allSatisfy(\.isHexDigit) })

        // Resolving for a preview does not mutate the manifest or the store.
        let before = try await sqliteCounts(at: seeded.history)
        _ = await seeded.history.archiveAttachmentPreviewURL(row)
        _ = await seeded.history.archiveAttachmentPreviewURL(row)
        #expect(try await sqliteCounts(at: seeded.history) == before)
    }

    @Test
    func previewIsNotReachableForAnythingButMaterializedRows() async throws {
        let seeded = try await seeded()
        for row in seeded.batches.flatMap(\.attachments) {
            // The UI exposes actions under `isMaterialized`; prove the resolver
            // agrees, so a future UI change cannot widen what is openable.
            if row.isMaterialized {
                #expect(await seeded.history.archiveAttachmentPreviewURL(row) != nil)
            } else {
                #expect(await seeded.history.archiveAttachmentPreviewURL(row) == nil)
            }
        }
    }

    private func sqliteCounts(at history: LocalMessageHistory) async throws -> String {
        // Counted through the read model, which is the only surface B5.1 uses.
        let snapshot = await history.archiveEvidenceSnapshot()
        let imports = await history.archiveAttachmentBatches(
            importID: snapshot.imports.first?.id ?? 0
        )
        return "\(imports.count):\(imports.flatMap(\.attachments).count):" +
            "\(imports.flatMap(\.attachments).map(\.byteCount).reduce(0, +))"
    }

}

struct WeChatArchiveImportIdentityTests {
    @Test
    func topLevelDirectoryProducesOpaqueStableIdentity() throws {
        let scratch = try Scratch()
        let url = try scratch.zip {
            $0.add("群聊名称/聊天记录.txt", transcript([("张三", m35, "x")]))
        }
        let archive = try WeChatNativeArchiveReader.read(contentsOf: url)

        let a = ArchiveConversationIdentityResolver.resolve(archive: archive)
        let b = ArchiveConversationIdentityResolver.resolve(archive: archive)

        #expect(a == b)
        #expect(a.rawValue.hasPrefix("native-v1:"))
        #expect(!a.rawValue.contains("群聊名称"))
    }

    @Test
    func rootLevelTranscriptGetsAnonymousImportScopedIdentity() throws {
        let firstScratch = try Scratch()
        let secondScratch = try Scratch()
        let changedScratch = try Scratch()
        let firstURL = try firstScratch.zip {
            $0.add("聊天记录.txt", transcript([("张三", m35, "x")]))
        }
        let secondURL = try secondScratch.zip {
            $0.add("聊天记录.txt", transcript([("张三", m35, "x")]))
        }
        let changedURL = try changedScratch.zip {
            $0.add("聊天记录.txt", transcript([("张三", m35, "y")]))
        }

        let first = ArchiveConversationIdentityResolver.resolve(
            archive: try WeChatNativeArchiveReader.read(contentsOf: firstURL)
        )
        let second = ArchiveConversationIdentityResolver.resolve(
            archive: try WeChatNativeArchiveReader.read(contentsOf: secondURL)
        )
        let changed = ArchiveConversationIdentityResolver.resolve(
            archive: try WeChatNativeArchiveReader.read(contentsOf: changedURL)
        )

        #expect(first == second)
        #expect(first != changed)
        #expect(first.rawValue.hasPrefix("native-anonymous-v1:"))
        #expect(!first.rawValue.contains("张三"))
        #expect(!first.rawValue.contains("x"))
    }

    @Test
    func rootLevelUnattributedTranscriptAlsoGetsAnonymousIdentity() throws {
        let scratch = try Scratch()
        let url = try scratch.zip {
            $0.add("聊天记录.txt", unattributed(["x"]))
        }
        let archive = try WeChatNativeArchiveReader.read(contentsOf: url)
        let key = ArchiveConversationIdentityResolver.resolve(archive: archive)

        #expect(key.rawValue.hasPrefix("native-anonymous-v1:"))
        #expect(!key.rawValue.contains("x"))
    }
}

struct WeChatArchiveImportServiceTests {
    @Test
    func importRequiresExistingLocalPersistenceConsent() async throws {
        let scratch = try Scratch()
        let url = try scratch.zip {
            $0.add("家庭群/聊天记录.txt", transcript([("张三", m35, "x")]))
        }
        let history = LocalMessageHistory(url: nil)
        let service = WeChatArchiveImportService(history: history)

        await #expect(throws: ArchivePersistenceError.localPersistenceConsentRequired) {
            _ = try await service.importArchive(contentsOf: url)
        }
        #expect(await history.hasOpenStore == false)
    }


    @Test
    func importPersistsParsedEvidenceWithoutKeepingTheArchive() async throws {
        let scratch = try Scratch()
        let url = try scratch.zip {
            $0.add("聊天记录.txt", transcript([
                ("张三", m35, "x"), ("李四", m36, "y"),
            ]))
        }
        let history = LocalMessageHistory(url: nil)
        await history.setEnabled(true)
        let service = WeChatArchiveImportService(history: history)

        let first = try await service.importArchive(contentsOf: url)
        let second = try await service.importArchive(contentsOf: url)

        #expect(first.transcriptShape == "attributed")
        #expect(first.recordCount == 2)
        guard case .inserted(_, let count) = first.persistence else {
            Issue.record("first import should insert")
            return
        }
        #expect(count == 2)
        guard case .alreadyImported = second.persistence else {
            Issue.record("second import should be idempotent")
            return
        }

        #expect(FileManager.default.fileExists(atPath: url.path))
        #expect(await history.hasOpenStore)
    }
}


@MainActor
struct WeChatShareInboxAppModelTests {
    private func defaults() -> (UserDefaults, String) {
        let name = "share-inbox-model-" + UUID().uuidString
        let value = UserDefaults(suiteName: name)!
        value.removePersistentDomain(forName: name)
        return (value, name)
    }

    private func model(
        inbox: WeChatShareInbox,
        history: LocalMessageHistory,
        defaults: UserDefaults
    ) -> AppModel {
        AppModel(
            messageHistory: history,
            shareInbox: inbox,
            credentials: ShareInboxTestCredentials(),
            consentDefaults: defaults
        )
    }

    @Test
    func consentOffLeavesPendingTransportUnreadAndIntact() async throws {
        let scratch = try Scratch()
        let source = try scratch.zip {
            $0.add("家庭群/聊天记录.txt", transcript([("张三", m35, "x")]))
        }
        let inbox = WeChatShareInbox(rootURL: scratch.url.appendingPathComponent("inbox"))
        let queued = try inbox.enqueueCopy(from: source)
        let (storedDefaults, suite) = defaults()
        defer { storedDefaults.removePersistentDomain(forName: suite) }
        let history = LocalMessageHistory(url: nil)
        let app = model(inbox: inbox, history: history, defaults: storedDefaults)

        await app.consumePendingShareArchives()

        #expect(app.archiveImportStatus == .localPersistenceConsentRequired)
        #expect(FileManager.default.fileExists(atPath: queued.archiveURL.path))
        #expect(try inbox.pendingItems().map(\.id) == [queued.id])
        #expect(await history.hasOpenStore == false)
    }

    @Test
    func enablingConsentConsumesValidPendingArchiveAndDeletesTransport() async throws {
        let scratch = try Scratch()
        let source = try scratch.zip {
            $0.add("家庭群/聊天记录.txt", transcript([
                ("张三", m35, "x"), ("李四", m36, "y"),
            ]))
        }
        let inbox = WeChatShareInbox(rootURL: scratch.url.appendingPathComponent("inbox"))
        let queued = try inbox.enqueueCopy(from: source)
        let (storedDefaults, suite) = defaults()
        defer { storedDefaults.removePersistentDomain(forName: suite) }
        let history = LocalMessageHistory(url: nil)
        let app = model(inbox: inbox, history: history, defaults: storedDefaults)

        await app.setAllowsLocalPersistence(true)

        #expect(app.archiveImportStatus == .imported(
            recordCount: 2,
            transcriptShape: "attributed"
        ))
        #expect(!FileManager.default.fileExists(atPath: queued.directoryURL.path))
        #expect(try inbox.pendingItems().isEmpty)
        #expect(await history.hasOpenStore)
    }

    @Test
    func pendingArchiveWithAttachmentMaterializesEvidenceBeforeTransportDeletion() async throws {
        let scratch = try Scratch()
        let source = try scratch.zip { builder in
            builder.add("聊天记录.txt", transcript([("张三", m35, "x")]))
            builder.add("images/private-name.jpg", tinyJPEG)
        }
        let inbox = WeChatShareInbox(rootURL: scratch.url.appendingPathComponent("inbox"))
        let queued = try inbox.enqueueCopy(from: source)
        let (storedDefaults, suite) = defaults()
        defer { storedDefaults.removePersistentDomain(forName: suite) }

        let databaseURL = scratch.url.appendingPathComponent("messages.sqlite")
        let attachmentRoot = scratch.url.appendingPathComponent("archive-attachments", isDirectory: true)
        let history = LocalMessageHistory(
            url: databaseURL,
            attachmentRoot: attachmentRoot
        )
        let app = model(inbox: inbox, history: history, defaults: storedDefaults)

        await app.setAllowsLocalPersistence(true)

        #expect(app.archiveImportStatus == .imported(
            recordCount: 1,
            transcriptShape: "attributed"
        ))
        #expect(app.archiveAttachmentImportStatus == .inserted(
            attachmentCount: 1,
            materializedCount: 1
        ))
        #expect(!FileManager.default.fileExists(atPath: queued.directoryURL.path))
        #expect(try inbox.pendingItems().isEmpty)
        #expect(app.archiveEvidence.imports.first?.attachmentCount == 1)
        #expect(app.archiveEvidence.imports.first?.materializedAttachmentCount == 1)
        #expect(app.selectedArchiveAttachmentBatches.count == 1)
        #expect(app.selectedArchiveAttachmentBatches.first?.attachments.count == 1)
        #expect(app.selectedArchiveAttachmentBatches.first?.attachments.first?.storageState == .materialized)

        let importID = try #require(app.archiveEvidence.imports.first?.id)
        let importDirectory = attachmentRoot.appendingPathComponent(
            "import-\(importID)", isDirectory: true
        )
        #expect(FileManager.default.fileExists(atPath: importDirectory.path))
    }

    @Test
    func attachmentFailureKeepsPendingAndRetryCompletesWithoutDuplicatingTranscript() async throws {
        let scratch = try Scratch()
        let source = try scratch.zip { builder in
            builder.add("聊天记录.txt", transcript([("张三", m35, "x")]))
            builder.add("images/private-name.jpg", tinyJPEG)
        }
        let inbox = WeChatShareInbox(rootURL: scratch.url.appendingPathComponent("inbox"))
        let (storedDefaults, suite) = defaults()
        defer { storedDefaults.removePersistentDomain(forName: suite) }

        let databaseURL = scratch.url.appendingPathComponent("messages.sqlite")
        let attachmentRoot = scratch.url.appendingPathComponent("attachment-root")
        let history = LocalMessageHistory(
            url: databaseURL,
            attachmentRoot: attachmentRoot
        )
        let app = model(inbox: inbox, history: history, defaults: storedDefaults)

        // Open the store first with no pending work, then create a filesystem
        // fault. This exercises an attachment failure during a live import
        // rather than the startup reconcile path, which deliberately heals
        // invalid attachment-root nodes.
        await app.setAllowsLocalPersistence(true)
        try Data("blocking file".utf8).write(to: attachmentRoot)
        let queued = try inbox.enqueueCopy(from: source)

        await app.consumePendingShareArchives()

        #expect(app.archiveImportStatus == .imported(
            recordCount: 1,
            transcriptShape: "attributed"
        ))
        #expect(app.archiveAttachmentImportStatus == .unavailable)
        #expect(FileManager.default.fileExists(atPath: queued.directoryURL.path))
        #expect(try inbox.pendingItems().map(\.id) == [queued.id])
        #expect(app.archiveEvidence.imports.count == 1)
        #expect(app.archiveEvidence.imports.first?.attachmentCount == 0)

        try FileManager.default.removeItem(at: attachmentRoot)
        await app.consumePendingShareArchives()

        #expect(app.archiveImportStatus == .alreadyImported)
        #expect(app.archiveAttachmentImportStatus == .inserted(
            attachmentCount: 1,
            materializedCount: 1
        ))
        #expect(!FileManager.default.fileExists(atPath: queued.directoryURL.path))
        #expect(try inbox.pendingItems().isEmpty)
        #expect(app.archiveEvidence.imports.count == 1)
        #expect(app.archiveEvidence.imports.first?.recordCount == 1)
        #expect(app.archiveEvidence.imports.first?.attachmentCount == 1)
        #expect(app.selectedArchiveAttachmentBatches.count == 1)
    }

    @Test
    func invalidPendingArchiveIsTerminalAndDeleted() async throws {
        let scratch = try Scratch()
        let source = scratch.url.appendingPathComponent("invalid.zip")
        try Data("not a zip".utf8).write(to: source)
        let inbox = WeChatShareInbox(rootURL: scratch.url.appendingPathComponent("inbox"))
        let queued = try inbox.enqueueCopy(from: source)
        let (storedDefaults, suite) = defaults()
        defer { storedDefaults.removePersistentDomain(forName: suite) }
        let history = LocalMessageHistory(url: nil)
        let app = model(inbox: inbox, history: history, defaults: storedDefaults)

        await app.setAllowsLocalPersistence(true)

        #expect(app.archiveImportStatus == .invalidArchive)
        #expect(!FileManager.default.fileExists(atPath: queued.directoryURL.path))
        #expect(try inbox.pendingItems().isEmpty)
    }

    @Test
    func rootLevelWeChatArchiveImportsAndDeletesTransport() async throws {
        let scratch = try Scratch()
        let source = try scratch.zip {
            $0.add("聊天记录.txt", transcript([("张三", m35, "x")]))
        }
        let inbox = WeChatShareInbox(rootURL: scratch.url.appendingPathComponent("inbox"))
        let queued = try inbox.enqueueCopy(from: source)
        let (storedDefaults, suite) = defaults()
        defer { storedDefaults.removePersistentDomain(forName: suite) }
        let history = LocalMessageHistory(url: nil)
        let app = model(inbox: inbox, history: history, defaults: storedDefaults)

        await app.setAllowsLocalPersistence(true)

        #expect(app.archiveImportStatus == .imported(
            recordCount: 1,
            transcriptShape: "attributed"
        ))
        #expect(!FileManager.default.fileExists(atPath: queued.directoryURL.path))
        #expect(try inbox.pendingItems().isEmpty)
        #expect(app.archiveEvidence.storeState == .ready)
        #expect(app.archiveEvidence.imports.count == 1)
        #expect(app.archiveEvidence.imports.first?.recordCount == 1)
        #expect(app.selectedArchiveImportID == app.archiveEvidence.imports.first?.id)
        #expect(app.selectedArchiveRecords.map(\.text) == ["x"])
    }
}

private final class ShareInboxTestCredentials: CredentialStoring, @unchecked Sendable {
    private let lock = NSLock()
    private var storage: [String: String] = [:]

    func save(_ secret: String, account: String) throws {
        lock.withLock { storage[account] = secret }
    }

    func secret(account: String) throws -> String? {
        lock.withLock { storage[account] }
    }

    func remove(account: String) throws {
        lock.withLock { storage[account] = nil }
    }
}
