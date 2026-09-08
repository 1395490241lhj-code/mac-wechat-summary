import Foundation

/// A validated native WeChat export, held in memory.
///
/// Reading one **persists nothing**. There is no store, no Application Support
/// path, no copy of the ZIP and no database anywhere in this path -- the whole
/// result is this value, and it is gone when the caller drops it. That is
/// deliberate: v2's local history has a consent gate, a retention policy and a
/// delete action, and a raw chat archive kept beside it would survive
/// "Delete All History" and quietly make that action a lie. Persisting an
/// archive needs its own lifecycle design first.
struct WeChatNativeArchive: Sendable, Equatable {
    /// The entry the transcript was read from. Kept for internal use; it is
    /// deliberately **not** part of the pasteable acceptance report -- see
    /// `WeChatNativeArchiveSummary`.
    let transcriptEntryName: String
    /// Position of that entry in the archive's central directory, from 0. A
    /// stable way to say *which* entry was chosen without repeating its name.
    let transcriptEntryIndex: Int
    let messages: [WeChatArchiveMessage]
    /// How the timestamps in `messages` were interpreted. A native export
    /// carries no offset, so this is the reader's assumption, recorded.
    let timeZoneIdentifier: String
    /// Facts about the container, kept so the acceptance seam can report shape
    /// without reporting content.
    let entryCount: Int
    let transcriptCandidateCount: Int
    /// Non-transcript file entries, counted by lowercased extension. Phase A
    /// does not read, extract or interpret any of them.
    let attachmentCountsByExtension: [String: Int]
}

enum WeChatNativeArchiveError: Error, Equatable, Sendable {
    case archive(ZIPArchiveError)
    /// No entry in the archive parses as a native WeChat transcript.
    case noRecognizableTranscript
}

/// Reads a native WeChat export ZIP into a validated in-memory transcript.
///
/// The pipeline this belongs to:
///
///     Dukou / WeChat native ZIP → WeChatNativeArchiveReader
///         → validated in-memory transcript → [future] unified MessageStore
///
/// The last arrow does not exist yet and this type must not grow it.
///
/// **This is a second ingestion path, not a replacement.** The visual capture
/// path is unchanged and remains the shipped read path. The two are not merged,
/// compared or reconciled here; a native export is WeChat's own account of a
/// conversation, while a captured frame is ours, and deciding they describe the
/// same messages is an identity question this reader deliberately does not
/// answer (the same discipline as D-019).
///
/// **Permissions.** None beyond reading a file the user handed us. Nothing here
/// touches WeChat, its process, its files, its window or its database.
enum WeChatNativeArchiveReader {
    /// Entries larger than this are not considered as transcripts. A chat
    /// export's TXT is far smaller; anything bigger is not one.
    static let maximumTranscriptBytes = 16 << 20

    /// Validates the container, verifies **every** entry, then parses the best
    /// transcript in it.
    ///
    /// Integrity is not optional and there is no parameter to turn it off. The
    /// contract this phase offers is a single sentence -- *an accepted archive
    /// is one whose every entry was verified* -- and a caller-supplied flag
    /// would turn that into a sentence about what the caller happened to pass.
    /// Each non-directory entry has its compressed stream decoded, its expanded
    /// length compared against the central directory, and its CRC checked;
    /// bytes are streamed and never retained, so the cost is time, not memory.
    ///
    /// If a real, large export ever makes that cost unacceptable, the way back
    /// is measured evidence and a deliberate change to the contract -- not an
    /// argument passed at a call site.
    static func read(
        contentsOf url: URL,
        limits: ZIPArchiveReader.Limits = .standard,
        timeZone: TimeZone = WeChatNativeTranscriptParser.defaultTimeZone
    ) throws -> WeChatNativeArchive {
        let reader: ZIPArchiveReader
        do {
            reader = try ZIPArchiveReader(url: url, limits: limits)
        } catch let error as ZIPArchiveError {
            throw WeChatNativeArchiveError.archive(error)
        }

        // The entry-count limit is applied inside `ZIPArchiveReader`, before a
        // single central-directory record is parsed; re-checking it here would
        // only describe a gate that has already run.
        do { try reader.verifyIntegrity() } catch let error as ZIPArchiveError {
            throw WeChatNativeArchiveError.archive(error)
        }

        let files = reader.entries.filter { !$0.isDirectory }
        let candidates = files.filter {
            $0.pathExtension == "txt" && $0.uncompressedSize <= maximumTranscriptBytes
        }

        // Only entries that parse *completely* as a native transcript compete,
        // and the richest one wins. Transcripts are never concatenated: two
        // TXTs in one export are two accounts, and stitching them would invent
        // a conversation that WeChat never wrote.
        var best: (name: String, index: Int, messages: [WeChatArchiveMessage])?
        var recognized = 0
        for candidate in candidates {
            guard let data = try? reader.data(for: candidate),
                  let body = decodeUTF8(data),
                  let messages = try? WeChatNativeTranscriptParser.parse(body, timeZone: timeZone),
                  !messages.isEmpty
            else { continue }
            recognized += 1
            if best == nil || messages.count > best!.messages.count {
                let index = reader.entries.firstIndex { $0.name == candidate.name } ?? 0
                best = (candidate.name, index, messages)
            }
        }
        guard let best else { throw WeChatNativeArchiveError.noRecognizableTranscript }

        var attachments: [String: Int] = [:]
        for file in files where file.name != best.name {
            attachments[file.pathExtension.isEmpty ? "(none)" : file.pathExtension, default: 0] += 1
        }

        return WeChatNativeArchive(
            transcriptEntryName: best.name,
            transcriptEntryIndex: best.index,
            messages: best.messages,
            timeZoneIdentifier: timeZone.identifier,
            entryCount: reader.entries.count,
            transcriptCandidateCount: recognized,
            attachmentCountsByExtension: attachments
        )
    }

    /// UTF-8, with an optional BOM. A native export is UTF-8; anything that is
    /// not is not a candidate, rather than being guessed at.
    private static func decodeUTF8(_ data: Data) -> String? {
        var bytes = data
        if bytes.starts(with: [0xEF, 0xBB, 0xBF]) { bytes = bytes.dropFirst(3) }
        return String(data: bytes, encoding: .utf8)
    }
}

/// What a real-archive acceptance run is allowed to report.
///
/// Every field is shape, never content. There is no message text, no sender and
/// no chat title, so a summary can be pasted into a report without leaking a
/// private conversation -- the same rule `DiagnosticResult` and
/// `WindowCaptureMetrics` already follow.
///
/// **The chosen entry's filename is not reported, and that is a change of
/// mind.** It was, on the assumption that a WeChat export names its transcript
/// something fixed and generic. Nobody has opened a real export, so that is a
/// guess, and the thing being guessed about is whether a filename contains a
/// chat title or a contact's name. The entry is identified by its extension and
/// its ordinal instead, which answers "which entry was chosen" without betting
/// a private name on an unverified assumption. If a real export shows the name
/// is a fixed constant, report it then.
///
/// The two timestamps are the only values derived from message data, and they
/// are bounds, not content: they say the export covers a span, not what was
/// said in it.
struct WeChatNativeArchiveSummary: Sendable, Equatable {
    let isValid: Bool
    let entryCount: Int
    let transcriptCandidateCount: Int
    /// Lowercased extension of the chosen entry, e.g. "txt".
    let chosenTranscriptExtension: String
    /// Its position in the central directory, from 0.
    let chosenTranscriptIndex: Int
    let messageCount: Int
    let firstSentAtText: String
    let lastSentAtText: String
    let timeZoneIdentifier: String
    let attachmentCountsByExtension: [String: Int]
    /// Closed-set reason the archive was refused, or nil when it was accepted.
    let failureReason: String?

    init(_ archive: WeChatNativeArchive) {
        isValid = true
        failureReason = nil
        entryCount = archive.entryCount
        transcriptCandidateCount = archive.transcriptCandidateCount
        chosenTranscriptExtension = WeChatNativeArchiveSummary
            .extensionOf(archive.transcriptEntryName)
        chosenTranscriptIndex = archive.transcriptEntryIndex
        messageCount = archive.messages.count
        firstSentAtText = archive.messages.first?.sentAtText ?? ""
        lastSentAtText = archive.messages.last?.sentAtText ?? ""
        timeZoneIdentifier = archive.timeZoneIdentifier
        attachmentCountsByExtension = archive.attachmentCountsByExtension
    }

    /// A refusal, described without saying anything about the file.
    init(failure: String) {
        isValid = false
        failureReason = failure
        entryCount = 0
        transcriptCandidateCount = 0
        chosenTranscriptExtension = ""
        chosenTranscriptIndex = -1
        messageCount = 0
        firstSentAtText = ""
        lastSentAtText = ""
        timeZoneIdentifier = ""
        attachmentCountsByExtension = [:]
    }

    static func extensionOf(_ name: String) -> String {
        let last = name.split(separator: "/").last.map(String.init) ?? ""
        guard let dot = last.lastIndex(of: "."), dot != last.startIndex else { return "" }
        return String(last[last.index(after: dot)...]).lowercased()
    }

    /// Stable, greppable lines for an acceptance run.
    var reportLines: [String] {
        guard isValid else { return ["archive valid: no (\(failureReason ?? "unknown"))"] }
        let attachments = attachmentCountsByExtension
            .sorted { $0.key < $1.key }
            .map { "\($0.key)=\($0.value)" }
            .joined(separator: " ")
        return [
            "archive valid: yes",
            "entry count: \(entryCount)",
            "transcript candidates: \(transcriptCandidateCount)",
            "chosen transcript: extension=\(chosenTranscriptExtension.isEmpty ? "(none)" : chosenTranscriptExtension) index=\(chosenTranscriptIndex)",
            "message count: \(messageCount)",
            "first timestamp: \(firstSentAtText)",
            "last timestamp: \(lastSentAtText)",
            "interpreted in timezone: \(timeZoneIdentifier)",
            "attachments by extension: \(attachments.isEmpty ? "(none)" : attachments)",
        ]
    }
}

/// A structural inventory of an archive, with no filenames in it.
///
/// This exists so nobody has to paste `unzip -l` of a private chat export. That
/// listing is every filename WeChat chose, and we do not yet know whether those
/// names carry a chat title, a contact's name or anything else identifying.
/// What Phase B actually needs from a real export is the *shape* -- how deep the
/// tree goes, how many top-level directories there are, which extensions appear,
/// how many of each and roughly how large -- and all of that can be reported
/// without a single name.
///
/// Sizes are bucketed rather than exact: a precise byte count of a single
/// attachment is closer to a fingerprint than a shape.
struct WeChatNativeArchiveInventory: Sendable, Equatable {
    struct Group: Sendable, Equatable {
        let pathExtension: String
        let count: Int
        let totalSizeBucket: String
    }

    let entryCount: Int
    let fileCount: Int
    let directoryCount: Int
    let maximumPathDepth: Int
    let topLevelDirectoryCount: Int
    let groups: [Group]

    /// Reads an archive for its shape alone. Works even when no transcript
    /// parses, which is exactly the case worth diagnosing on a real export.
    static func read(
        contentsOf url: URL, limits: ZIPArchiveReader.Limits = .standard
    ) throws -> WeChatNativeArchiveInventory {
        do {
            return WeChatNativeArchiveInventory(try ZIPArchiveReader(url: url, limits: limits))
        } catch let error as ZIPArchiveError {
            throw WeChatNativeArchiveError.archive(error)
        }
    }

    init(_ reader: ZIPArchiveReader) {
        let entries = reader.entries
        let files = entries.filter { !$0.isDirectory }
        entryCount = entries.count
        fileCount = files.count
        directoryCount = entries.count - files.count
        maximumPathDepth = entries
            .map { $0.name.split(separator: "/").count }
            .max() ?? 0
        topLevelDirectoryCount = Set(
            entries.compactMap { entry -> Substring? in
                let parts = entry.name.split(separator: "/")
                return parts.count > 1 ? parts[0] : nil
            }
        ).count

        var counts: [String: (count: Int, bytes: Int)] = [:]
        for file in files {
            let key = file.pathExtension.isEmpty ? "(none)" : file.pathExtension
            let existing = counts[key] ?? (0, 0)
            counts[key] = (existing.count + 1, existing.bytes + file.uncompressedSize)
        }
        groups = counts
            .sorted { $0.key < $1.key }
            .map { Group(
                pathExtension: $0.key,
                count: $0.value.count,
                totalSizeBucket: Self.bucket($0.value.bytes)
            ) }
    }

    /// Order-of-magnitude only.
    static func bucket(_ bytes: Int) -> String {
        switch bytes {
        case 0: "0"
        case ..<1_024: "<1KiB"
        case ..<(10 * 1_024): "<10KiB"
        case ..<(100 * 1_024): "<100KiB"
        case ..<(1_024 * 1_024): "<1MiB"
        case ..<(10 * 1_024 * 1_024): "<10MiB"
        case ..<(100 * 1_024 * 1_024): "<100MiB"
        default: ">=100MiB"
        }
    }

    var reportLines: [String] {
        [
            "entries: \(entryCount) (files \(fileCount), directories \(directoryCount))",
            "max path depth: \(maximumPathDepth)",
            "top-level directories: \(topLevelDirectoryCount)",
        ] + groups.map {
            "  \($0.pathExtension): count=\($0.count) total=\($0.totalSizeBucket)"
        }
    }
}
