import Compression
import Foundation

/// Closed set of reasons an archive is refused.
///
/// Deliberately carries no entry name, no path and no byte content: a rejected
/// archive is a user's chat export, and the reason it was rejected is the only
/// thing worth reporting. The same discipline as `GeminiExtractionError`.
enum ZIPArchiveError: Error, Equatable, Sendable {
    case unreadable
    case notAZIPArchive
    /// The archive uses ZIP64 records. Refused rather than half-supported.
    case unsupportedZIP64
    case malformedCentralDirectory
    case malformedLocalHeader
    case entryCountOutsideSupportedRange
    case expandedSizeExceedsLimit
    case entryExceedsSizeLimit
    case encryptedEntry
    case unsupportedCompressionMethod
    case unsafeEntryPath
    case symbolicLinkEntry
    case undecodableEntryName
    case integrityCheckFailed
}

/// A minimal, read-only ZIP container reader.
///
/// **Why this exists rather than a package.** macOS publishes no API that
/// enumerates and reads the entries of a ZIP container: `Compression` gives raw
/// DEFLATE and nothing about the container, and `NSFileManager` will only
/// unarchive a whole file through Finder-level services. Every safety rule this
/// ingestion path needs -- reject encrypted entries, reject anything but
/// stored/deflate, reject traversal and symlink entries, cap the expanded size,
/// verify CRC -- is a decision made *from the central directory before
/// decompressing anything*. A general-purpose ZIP package hides exactly those
/// fields and would still leave this file holding the checks, so the package
/// would add a supply-chain surface to a privacy-sensitive app without removing
/// the code that matters.
///
/// **What it deliberately does not do.** It does not write, does not extract to
/// disk, does not create directories, and never resolves an entry name to a
/// filesystem path. An entry is read into memory, under a cap, or not at all.
/// ZIP64, multi-disk archives and encryption are refused, not partially
/// handled.
struct ZIPArchiveReader {
    /// Bounds every archive is read under. Values match the limits the Python
    /// reference importer settled on; they are generous for a chat export and
    /// small enough that a hostile archive cannot exhaust this machine.
    struct Limits: Sendable, Equatable {
        var maximumEntryCount = 1_000
        var maximumTotalUncompressedBytes = 1 << 30  // 1 GiB
        /// The largest single entry this reader will decode into memory.
        var maximumReadableEntryBytes = 16 << 20  // 16 MiB

        static let standard = Limits()
    }

    /// One central-directory record, already validated.
    struct Entry: Sendable, Equatable {
        /// The stored name, normalised to forward slashes. Never used as a
        /// filesystem path -- nothing in this type ever touches the filesystem.
        let name: String
        let isDirectory: Bool
        let compressedSize: Int
        let uncompressedSize: Int
        let crc32: UInt32
        let isDeflated: Bool
        fileprivate let localHeaderOffset: Int

        /// Lowercased path extension, or "" when the name carries none.
        var pathExtension: String {
            let last = name.split(separator: "/").last.map(String.init) ?? ""
            guard let dot = last.lastIndex(of: "."), dot != last.startIndex else { return "" }
            return String(last[last.index(after: dot)...]).lowercased()
        }
    }

    let entries: [Entry]
    private let url: URL
    private let limits: Limits

    /// Parses and validates the whole central directory. Decompresses nothing.
    init(url: URL, limits: Limits = .standard) throws {
        self.url = url
        self.limits = limits

        let handle = try Self.open(url)
        defer { try? handle.close() }
        let fileSize = try Self.size(of: handle)
        guard fileSize >= 22 else { throw ZIPArchiveError.notAZIPArchive }

        let directory = try Self.readCentralDirectory(handle, fileSize: fileSize)
        var parsed: [Entry] = []
        var total = 0
        var cursor = 0

        for _ in 0..<directory.entryCount {
            let (entry, next) = try Self.parseEntry(directory.bytes, at: cursor)
            try Self.validate(entry, fileSize: fileSize, limits: limits)
            if !entry.isDirectory {
                total += entry.uncompressedSize
                guard total <= limits.maximumTotalUncompressedBytes else {
                    throw ZIPArchiveError.expandedSizeExceedsLimit
                }
            }
            parsed.append(entry)
            cursor = next
        }
        self.entries = parsed
    }

    // MARK: - Reading

    /// Decodes one entry into memory, verifying its CRC and declared size.
    ///
    /// The output cap is enforced *while inflating*, so an entry that lies
    /// about its uncompressed size in the central directory cannot expand past
    /// the limit before anyone notices.
    func data(for entry: Entry) throws -> Data {
        guard entry.uncompressedSize <= limits.maximumReadableEntryBytes else {
            throw ZIPArchiveError.entryExceedsSizeLimit
        }
        var output = Data()
        output.reserveCapacity(entry.uncompressedSize)
        try stream(entry, cap: limits.maximumReadableEntryBytes) { output.append($0) }
        return output
    }

    /// Streams every entry through a CRC check without retaining any of it.
    ///
    /// This is the archive-integrity pass: a truncated, corrupted or
    /// mis-declared entry is caught here rather than at the moment some later
    /// phase happens to read it. Cost is proportional to the archive, which is
    /// why it is a separate call and not part of `init`.
    func verifyIntegrity() throws {
        for entry in entries where !entry.isDirectory {
            try stream(entry, cap: limits.maximumTotalUncompressedBytes) { _ in }
        }
    }

    /// Reads one entry, feeding decoded chunks to `sink`, then checks the CRC
    /// and the declared length. Nothing is retained by this function itself.
    private func stream(
        _ entry: Entry, cap: Int, into sink: (Data) throws -> Void
    ) throws {
        let handle = try Self.open(url)
        defer { try? handle.close() }

        let dataOffset = try Self.dataOffset(of: entry, in: handle)
        try handle.seek(toOffset: UInt64(dataOffset))

        var crc = CRC32()
        var produced = 0
        // A nested function rather than a stored closure: a `let` of closure
        // type would be escaping and could not capture the non-escaping `sink`.
        func emit(_ chunk: Data) throws {
            produced += chunk.count
            guard produced <= cap else { throw ZIPArchiveError.entryExceedsSizeLimit }
            crc.update(chunk)
            try sink(chunk)
        }

        if entry.isDeflated {
            try Self.inflate(handle, compressedSize: entry.compressedSize, emit: emit)
        } else {
            var remaining = entry.compressedSize
            while remaining > 0 {
                let want = min(remaining, Self.chunkSize)
                guard let chunk = try handle.read(upToCount: want), chunk.count == want else {
                    throw ZIPArchiveError.integrityCheckFailed
                }
                remaining -= want
                try emit(chunk)
            }
        }

        guard produced == entry.uncompressedSize, crc.value == entry.crc32 else {
            throw ZIPArchiveError.integrityCheckFailed
        }
    }

    // MARK: - Container parsing

    private static let chunkSize = 256 << 10

    private static func open(_ url: URL) throws -> FileHandle {
        guard let handle = try? FileHandle(forReadingFrom: url) else {
            throw ZIPArchiveError.unreadable
        }
        return handle
    }

    private static func size(of handle: FileHandle) throws -> Int {
        guard let end = try? handle.seekToEnd() else { throw ZIPArchiveError.unreadable }
        return Int(end)
    }

    private static func read(_ handle: FileHandle, at offset: Int, count: Int) throws -> Data {
        guard offset >= 0, count >= 0 else { throw ZIPArchiveError.malformedCentralDirectory }
        try handle.seek(toOffset: UInt64(offset))
        guard let data = try handle.read(upToCount: count), data.count == count else {
            throw ZIPArchiveError.malformedCentralDirectory
        }
        return data
    }

    private static func readCentralDirectory(
        _ handle: FileHandle, fileSize: Int
    ) throws -> (bytes: Data, entryCount: Int) {
        // The end-of-central-directory record sits within the last 22 bytes
        // plus a comment of at most 65535, so that is the whole search window.
        let window = min(fileSize, 22 + 0xFFFF)
        let tail = try read(handle, at: fileSize - window, count: window)
        guard let eocd = lastIndex(of: 0x0605_4B50, in: tail) else {
            throw ZIPArchiveError.notAZIPArchive
        }
        guard tail.count - eocd >= 22 else { throw ZIPArchiveError.notAZIPArchive }

        let disk = u16(tail, eocd + 4)
        let diskWithDirectory = u16(tail, eocd + 6)
        let entryCount = Int(u16(tail, eocd + 10))
        let directorySize = Int(u32(tail, eocd + 12))
        let directoryOffset = Int(u32(tail, eocd + 16))

        // Any of these sentinels means the real values live in a ZIP64 record.
        guard entryCount != 0xFFFF,
              directorySize != 0xFFFF_FFFF,
              directoryOffset != 0xFFFF_FFFF
        else { throw ZIPArchiveError.unsupportedZIP64 }
        // Multi-disk archives are refused rather than reassembled.
        guard disk == 0, diskWithDirectory == 0 else { throw ZIPArchiveError.notAZIPArchive }
        guard entryCount >= 1 else { throw ZIPArchiveError.entryCountOutsideSupportedRange }
        guard directoryOffset >= 0,
              directorySize >= 0,
              directoryOffset + directorySize <= fileSize
        else { throw ZIPArchiveError.malformedCentralDirectory }

        let bytes = try read(handle, at: directoryOffset, count: directorySize)
        return (bytes, entryCount)
    }

    private static func parseEntry(_ bytes: Data, at offset: Int) throws -> (Entry, Int) {
        guard offset + 46 <= bytes.count, u32(bytes, offset) == 0x0201_4B50 else {
            throw ZIPArchiveError.malformedCentralDirectory
        }
        let versionMadeBy = u16(bytes, offset + 4)
        let flags = u16(bytes, offset + 8)
        let method = u16(bytes, offset + 10)
        let crc = u32(bytes, offset + 16)
        let compressed = u32(bytes, offset + 20)
        let uncompressed = u32(bytes, offset + 24)
        let nameLength = Int(u16(bytes, offset + 28))
        let extraLength = Int(u16(bytes, offset + 30))
        let commentLength = Int(u16(bytes, offset + 32))
        let externalAttributes = u32(bytes, offset + 38)
        let localHeaderOffset = Int(u32(bytes, offset + 42))

        let nameStart = offset + 46
        let end = nameStart + nameLength + extraLength + commentLength
        guard end <= bytes.count else { throw ZIPArchiveError.malformedCentralDirectory }
        guard compressed != 0xFFFF_FFFF,
              uncompressed != 0xFFFF_FFFF,
              localHeaderOffset != 0xFFFF_FFFF
        else { throw ZIPArchiveError.unsupportedZIP64 }

        let rawName = bytes.subdata(in: nameStart..<(nameStart + nameLength))
        guard let decoded = String(data: rawName, encoding: .utf8) else {
            // Bit 11 promises UTF-8; anything else would have to be guessed,
            // and a guessed path is exactly what the traversal checks exist to
            // prevent. Refuse instead.
            throw ZIPArchiveError.undecodableEntryName
        }
        let name = decoded.replacingOccurrences(of: "\\", with: "/")

        // Encryption: bit 0 is the classic flag, bit 6 strong encryption.
        guard flags & 0x1 == 0, flags & 0x40 == 0 else { throw ZIPArchiveError.encryptedEntry }
        guard method == 0 || method == 8 else {
            throw ZIPArchiveError.unsupportedCompressionMethod
        }

        let isUnix = (versionMadeBy >> 8) == 3
        let mode = (externalAttributes >> 16) & 0xFFFF
        if isUnix, mode & UInt32(S_IFMT) == UInt32(S_IFLNK) {
            throw ZIPArchiveError.symbolicLinkEntry
        }

        let entry = Entry(
            name: name,
            isDirectory: name.hasSuffix("/"),
            compressedSize: Int(compressed),
            uncompressedSize: Int(uncompressed),
            crc32: crc,
            isDeflated: method == 8,
            localHeaderOffset: localHeaderOffset
        )
        return (entry, end)
    }

    private static func validate(_ entry: Entry, fileSize: Int, limits: Limits) throws {
        let name = entry.name
        guard !name.isEmpty else { throw ZIPArchiveError.unsafeEntryPath }
        guard !name.hasPrefix("/") else { throw ZIPArchiveError.unsafeEntryPath }
        let components = name.split(separator: "/", omittingEmptySubsequences: true)
        guard !components.contains("..") else { throw ZIPArchiveError.unsafeEntryPath }
        // "C:/x" and "C:x" -- a drive-qualified name is never a relative path.
        if let first = components.first, first.contains(":") {
            throw ZIPArchiveError.unsafeEntryPath
        }
        guard entry.localHeaderOffset >= 0,
              entry.localHeaderOffset + 30 <= fileSize,
              entry.compressedSize >= 0,
              entry.uncompressedSize >= 0,
              entry.localHeaderOffset + entry.compressedSize <= fileSize
        else { throw ZIPArchiveError.malformedLocalHeader }
    }

    private static func dataOffset(of entry: Entry, in handle: FileHandle) throws -> Int {
        let header = try? read(handle, at: entry.localHeaderOffset, count: 30)
        guard let header, u32(header, 0) == 0x0403_4B50 else {
            throw ZIPArchiveError.malformedLocalHeader
        }
        // Sizes are read from the central directory, never from here: with a
        // data descriptor (flag bit 3) the local header carries zeros.
        let nameLength = Int(u16(header, 26))
        let extraLength = Int(u16(header, 28))
        return entry.localHeaderOffset + 30 + nameLength + extraLength
    }

    // MARK: - Inflate

    /// Raw DEFLATE, streamed. `COMPRESSION_ZLIB` in Apple's Compression
    /// framework is RFC 1951 raw deflate, which is exactly what ZIP stores.
    ///
    /// Both buffers are owned by this function, so the pointers handed to the
    /// stream stay valid for its whole life -- a `Data`'s bytes borrowed inside
    /// `withUnsafeBytes` would not.
    private static func inflate(
        _ handle: FileHandle, compressedSize: Int, emit: (Data) throws -> Void
    ) throws {
        let stream = UnsafeMutablePointer<compression_stream>.allocate(capacity: 1)
        defer { stream.deallocate() }
        guard compression_stream_init(
            stream, COMPRESSION_STREAM_DECODE, COMPRESSION_ZLIB
        ) == COMPRESSION_STATUS_OK else {
            throw ZIPArchiveError.integrityCheckFailed
        }
        defer { compression_stream_destroy(stream) }

        let input = UnsafeMutablePointer<UInt8>.allocate(capacity: chunkSize)
        let output = UnsafeMutablePointer<UInt8>.allocate(capacity: chunkSize)
        defer { input.deallocate(); output.deallocate() }

        stream.pointee.src_ptr = UnsafePointer(input)
        stream.pointee.src_size = 0
        var remaining = compressedSize
        var sourceExhausted = false

        while true {
            if stream.pointee.src_size == 0, !sourceExhausted {
                let want = min(remaining, chunkSize)
                if want == 0 {
                    sourceExhausted = true
                } else {
                    guard let chunk = try handle.read(upToCount: want), chunk.count == want else {
                        throw ZIPArchiveError.integrityCheckFailed
                    }
                    chunk.copyBytes(to: input, count: want)
                    stream.pointee.src_ptr = UnsafePointer(input)
                    stream.pointee.src_size = want
                    remaining -= want
                }
            }

            stream.pointee.dst_ptr = output
            stream.pointee.dst_size = chunkSize
            let flags = sourceExhausted ? Int32(COMPRESSION_STREAM_FINALIZE.rawValue) : 0
            let status = compression_stream_process(stream, flags)
            guard status != COMPRESSION_STATUS_ERROR else {
                throw ZIPArchiveError.integrityCheckFailed
            }

            let produced = chunkSize - stream.pointee.dst_size
            if produced > 0 { try emit(Data(bytes: output, count: produced)) }
            if status == COMPRESSION_STATUS_END { return }
            // Finalizing with no input left and nothing produced means the
            // stream cannot advance: truncated or corrupt deflate data.
            if sourceExhausted, produced == 0 { throw ZIPArchiveError.integrityCheckFailed }
        }
    }

    // MARK: - Little-endian field access

    private static func u16(_ data: Data, _ offset: Int) -> UInt16 {
        let base = data.startIndex + offset
        return UInt16(data[base]) | UInt16(data[base + 1]) << 8
    }

    private static func u32(_ data: Data, _ offset: Int) -> UInt32 {
        let base = data.startIndex + offset
        return UInt32(data[base])
            | UInt32(data[base + 1]) << 8
            | UInt32(data[base + 2]) << 16
            | UInt32(data[base + 3]) << 24
    }

    private static func lastIndex(of signature: UInt32, in data: Data) -> Int? {
        guard data.count >= 4 else { return nil }
        var index = data.count - 4
        while index >= 0 {
            if u32(data, index) == signature { return index }
            index -= 1
        }
        return nil
    }
}

/// CRC-32 (IEEE 802.3), the checksum ZIP stores per entry.
struct CRC32 {
    private static let table: [UInt32] = (0..<256).map { index -> UInt32 in
        var value = UInt32(index)
        for _ in 0..<8 {
            value = value & 1 == 1 ? 0xEDB8_8320 ^ (value >> 1) : value >> 1
        }
        return value
    }

    private var state: UInt32 = 0xFFFF_FFFF

    var value: UInt32 { state ^ 0xFFFF_FFFF }

    mutating func update(_ data: Data) {
        var current = state
        for byte in data {
            current = Self.table[Int((current ^ UInt32(byte)) & 0xFF)] ^ (current >> 8)
        }
        state = current
    }
}
