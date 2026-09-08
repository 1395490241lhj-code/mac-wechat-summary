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
    /// The entry the transcript was read from.
    let transcriptEntryName: String
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

    /// Validates the container, verifies every entry's CRC, then parses the
    /// best transcript in it.
    ///
    /// - Parameter verifyIntegrity: streams every entry through a CRC check.
    ///   On by default -- a corrupt archive should be refused up front rather
    ///   than at whatever later moment something happens to read that entry.
    ///   Cost is proportional to the archive, so a caller reading a very large
    ///   export may choose to skip it; the chosen transcript is CRC-verified
    ///   either way, because reading it verifies it.
    static func read(
        contentsOf url: URL,
        limits: ZIPArchiveReader.Limits = .standard,
        timeZone: TimeZone = WeChatNativeTranscriptParser.defaultTimeZone,
        verifyIntegrity: Bool = true
    ) throws -> WeChatNativeArchive {
        let reader: ZIPArchiveReader
        do {
            reader = try ZIPArchiveReader(url: url, limits: limits)
        } catch let error as ZIPArchiveError {
            throw WeChatNativeArchiveError.archive(error)
        }

        guard reader.entries.count <= limits.maximumEntryCount else {
            throw WeChatNativeArchiveError.archive(.entryCountOutsideSupportedRange)
        }
        if verifyIntegrity {
            do { try reader.verifyIntegrity() } catch let error as ZIPArchiveError {
                throw WeChatNativeArchiveError.archive(error)
            }
        }

        let files = reader.entries.filter { !$0.isDirectory }
        let candidates = files.filter {
            $0.pathExtension == "txt" && $0.uncompressedSize <= maximumTranscriptBytes
        }

        // Only entries that parse *completely* as a native transcript compete,
        // and the richest one wins. Transcripts are never concatenated: two
        // TXTs in one export are two accounts, and stitching them would invent
        // a conversation that WeChat never wrote.
        var best: (name: String, messages: [WeChatArchiveMessage])?
        var recognized = 0
        for candidate in candidates {
            guard let data = try? reader.data(for: candidate),
                  let body = decodeUTF8(data),
                  let messages = try? WeChatNativeTranscriptParser.parse(body, timeZone: timeZone),
                  !messages.isEmpty
            else { continue }
            recognized += 1
            if best == nil || messages.count > best!.messages.count {
                best = (candidate.name, messages)
            }
        }
        guard let best else { throw WeChatNativeArchiveError.noRecognizableTranscript }

        var attachments: [String: Int] = [:]
        for file in files where file.name != best.name {
            attachments[file.pathExtension.isEmpty ? "(none)" : file.pathExtension, default: 0] += 1
        }

        return WeChatNativeArchive(
            transcriptEntryName: best.name,
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
/// Every field is shape, never content. There is no message text, no sender, no
/// conversation title and no filesystem path, so a summary can be pasted into a
/// report or a test log without leaking a private conversation -- the same rule
/// `DiagnosticResult` and `WindowCaptureMetrics` already follow.
///
/// The two timestamps are the *only* values derived from message data, and they
/// are bounds, not content: they say the export covers a span, not what was
/// said in it.
struct WeChatNativeArchiveSummary: Sendable, Equatable {
    let isValid: Bool
    let entryCount: Int
    let transcriptCandidateCount: Int
    let chosenTranscriptEntryName: String
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
        chosenTranscriptEntryName = archive.transcriptEntryName
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
        chosenTranscriptEntryName = ""
        messageCount = 0
        firstSentAtText = ""
        lastSentAtText = ""
        timeZoneIdentifier = ""
        attachmentCountsByExtension = [:]
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
            "chosen transcript: \(chosenTranscriptEntryName)",
            "message count: \(messageCount)",
            "first timestamp: \(firstSentAtText)",
            "last timestamp: \(lastSentAtText)",
            "interpreted in timezone: \(timeZoneIdentifier)",
            "attachments by extension: \(attachments.isEmpty ? "(none)" : attachments)",
        ]
    }
}
