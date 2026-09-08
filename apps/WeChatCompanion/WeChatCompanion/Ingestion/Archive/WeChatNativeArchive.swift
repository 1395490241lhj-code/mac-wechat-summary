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
/// Identity of the conversation **as the archive's own structure expresses it**.
///
/// Derived from the container layout and nothing else. Never from the ZIP's
/// filename, the transcript's contents, a sender, a timestamp, an attachment
/// basename, a record count, a transcript hash, or a visual conversation title
/// — every one of those is either content or a coincidence, and D-019/D-022
/// forbid resolving identity from them.
///
/// The associated value is the directory component **verbatim**: not trimmed,
/// not lowercased, not Unicode-normalised. Two source directories that differ
/// only by normalisation are two different claims about provenance, and this
/// type is not the place to decide they are the same.
enum WeChatNativeArchiveSourceIdentity: Sendable, Equatable {
    /// Every non-transcript file entry lives beneath one shared top-level
    /// directory, which is the only layout real exports have shown.
    case singleTopLevelDirectory(String)

    /// Stable, privacy-safe label. Reportable; the value never is.
    var kind: String {
        switch self {
        case .singleTopLevelDirectory: "single_top_level_directory"
        }
    }
}

struct WeChatNativeArchive: Sendable, Equatable {
    /// The entry the transcript was read from. Kept for internal use; it is
    /// deliberately **not** part of the pasteable acceptance report -- see
    /// `WeChatNativeArchiveSummary`.
    let transcriptEntryName: String
    /// Position of that entry in the archive's central directory, from 0.
    let transcriptEntryIndex: Int
    /// Which of the two observed shapes this archive turned out to carry, with
    /// its records. Not a message list: an unattributed archive has no messages
    /// in the sense the rest of this app means by the word.
    let transcript: WeChatNativeTranscript
    /// Facts about the container, kept so the acceptance seam can report shape
    /// without reporting content.
    let entryCount: Int
    let transcriptCandidateCount: Int
    /// Non-transcript file entries, counted by lowercased extension. Phase A
    /// does not read, extract or interpret any of them.
    let attachmentCountsByExtension: [String: Int]
    /// Source-side conversation identity, or `nil` when the layout does not
    /// establish one. Derived in the **same read** as the transcript, so the
    /// two can never describe different files.
    let sourceIdentity: WeChatNativeArchiveSourceIdentity?

    // Privacy-safe aggregate semantics. "No strict attributed parse" is not
    // "invalid archive", and these are what say so.
    var transcriptShape: String { transcript.shapeName }
    var recordCount: Int { transcript.recordCount }
    var attributionAvailable: Bool { transcript.attributionAvailable }
    var perMessageTimeAvailable: Bool { transcript.perMessageTimeAvailable }

    /// Attributed messages, or nil when this archive carries none. There is no
    /// variant of this that invents an empty sender or a placeholder date.
    var attributedMessages: [WeChatAttributedArchiveMessage]? {
        if case .attributed(let t) = transcript { return t.messages }
        return nil
    }

    var unattributedRecords: [WeChatUnattributedArchiveRecord]? {
        if case .unattributed(let t) = transcript { return t.records }
        return nil
    }

    /// Only an attributed transcript has timestamp bounds.
    var timestampBounds: (first: String, last: String)? {
        guard case .attributed(let t) = transcript,
              let first = t.messages.first, let last = t.messages.last
        else { return nil }
        return (first.sentAtText, last.sentAtText)
    }

    var timeZoneIdentifier: String? {
        if case .attributed(let t) = transcript { return t.timeZoneIdentifier }
        return nil
    }
}

enum WeChatNativeArchiveError: Error, Equatable, Sendable {
    case archive(ZIPArchiveError)
    /// No entry in the archive parses as either observed native shape.
    case noRecognizableTranscript
    /// The archive carries recognizable transcripts of **different shapes**.
    /// Refused rather than resolved by count: picking the larger one would be
    /// choosing between "who said what when" and "some ordered text" on the
    /// basis of which happened to be longer.
    case ambiguousTranscriptCandidates
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

        // Only entries that classify as one of the two observed shapes compete.
        // Transcripts are never concatenated: two TXTs in one export are two
        // accounts, and stitching them would invent a conversation WeChat never
        // wrote.
        var recognized: [(name: String, index: Int, transcript: WeChatNativeTranscript)] = []
        for candidate in candidates {
            guard let data = try? reader.data(for: candidate),
                  let body = decodeUTF8(data),
                  let transcript = try? WeChatNativeTranscriptParser.parse(body, timeZone: timeZone)
            else { continue }
            let index = reader.entries.firstIndex { $0.name == candidate.name } ?? 0
            recognized.append((candidate.name, index, transcript))
        }
        guard !recognized.isEmpty else { throw WeChatNativeArchiveError.noRecognizableTranscript }

        // Mixed shapes are ambiguous and fail closed. Within one shape the
        // richest transcript wins -- the rule Dukou's own reader uses, and the
        // only one with any evidence behind it. No real archive observed so far
        // carries more than one TXT at all.
        let shapes = Set(recognized.map(\.transcript.shapeName))
        guard shapes.count == 1 else {
            throw WeChatNativeArchiveError.ambiguousTranscriptCandidates
        }
        let best = recognized.max { $0.transcript.recordCount < $1.transcript.recordCount }!

        let others = files.filter { $0.name != best.name }
        var attachments: [String: Int] = [:]
        for file in others {
            attachments[file.pathExtension.isEmpty ? "(none)" : file.pathExtension, default: 0] += 1
        }

        return WeChatNativeArchive(
            transcriptEntryName: best.name,
            transcriptEntryIndex: best.index,
            transcript: best.transcript,
            entryCount: reader.entries.count,
            transcriptCandidateCount: recognized.count,
            attachmentCountsByExtension: attachments,
            sourceIdentity: sourceIdentity(ofEntriesOtherThanTranscript: others)
        )
    }

    /// Derives source identity from the container layout, strictly.
    ///
    /// Every real export observed so far puts its transcript at the archive
    /// root and its attachments beneath **one** top-level directory named for
    /// the conversation, so that directory is the only structural identity the
    /// artifact offers. The transcript is therefore excluded from the check --
    /// requiring it to sit under the directory too would reject every real
    /// archive we have.
    ///
    /// Returns `nil` -- identity unavailable -- rather than guessing when:
    /// there are no non-transcript entries at all (an attachment-free export
    /// has no directory to name it), any of them sits at the archive root, or
    /// they are spread across more than one top-level directory. Refusing is
    /// the conservative answer: an invented conversation key would merge two
    /// conversations or split one, and both are silent.
    private static func sourceIdentity(
        ofEntriesOtherThanTranscript entries: [ZIPArchiveReader.Entry]
    ) -> WeChatNativeArchiveSourceIdentity? {
        guard !entries.isEmpty else { return nil }
        var shared: String?
        for entry in entries {
            let components = entry.name.split(separator: "/", omittingEmptySubsequences: false)
            // A root-level entry has no top-level directory to belong to.
            guard components.count >= 2 else { return nil }
            let top = String(components[0])
            guard !top.isEmpty else { return nil }
            if let shared {
                guard shared == top else { return nil }
            } else {
                shared = top
            }
        }
        return shared.map(WeChatNativeArchiveSourceIdentity.singleTopLevelDirectory)
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
/// **The chosen entry's filename is not reported.** It was, on the assumption
/// that a WeChat export names its transcript something fixed and generic. A
/// real export later showed the archive's one top-level directory named after
/// the conversation and its participants, so that assumption was worth exactly
/// nothing. The entry is identified by extension and ordinal instead.
///
/// **A recognized transcript is not the same as an attributed one.** An
/// unattributed archive is valid, recognized, and simply carries no
/// attribution; reporting it as an invalid archive was the earlier mistake this
/// type now refuses to repeat. Timestamp bounds appear only when the shape has
/// timestamps to bound.
struct WeChatNativeArchiveSummary: Sendable, Equatable {
    let containerValid: Bool
    let transcriptRecognized: Bool
    let entryCount: Int
    let transcriptCandidateCount: Int
    /// Lowercased extension of the chosen entry, e.g. "txt".
    let chosenTranscriptExtension: String
    /// Its position in the central directory, from 0.
    let chosenTranscriptIndex: Int
    /// "attributed" or "unattributed".
    let transcriptShape: String
    let recordCount: Int
    let attributionAvailable: Bool
    let perMessageTimeAvailable: Bool
    /// Present only for an attributed transcript.
    let firstSentAtText: String?
    let lastSentAtText: String?
    let timeZoneIdentifier: String?
    let attachmentCountsByExtension: [String: Int]
    /// Whether the layout established a source-side conversation identity, and
    /// which rule found it. **The identity value itself is never reported**,
    /// and neither is a hash of it -- a hash of a chat name is still a stable
    /// identifier for that chat, so it is not a privacy-safe substitute.
    let sourceIdentityAvailable: Bool
    let sourceIdentityKind: String
    /// Closed-set reason the archive was refused, or nil when it was accepted.
    let failureReason: String?

    init(_ archive: WeChatNativeArchive) {
        containerValid = true
        transcriptRecognized = true
        failureReason = nil
        entryCount = archive.entryCount
        transcriptCandidateCount = archive.transcriptCandidateCount
        chosenTranscriptExtension = WeChatNativeArchiveSummary
            .extensionOf(archive.transcriptEntryName)
        chosenTranscriptIndex = archive.transcriptEntryIndex
        transcriptShape = archive.transcriptShape
        recordCount = archive.recordCount
        attributionAvailable = archive.attributionAvailable
        perMessageTimeAvailable = archive.perMessageTimeAvailable
        firstSentAtText = archive.timestampBounds?.first
        lastSentAtText = archive.timestampBounds?.last
        timeZoneIdentifier = archive.timeZoneIdentifier
        attachmentCountsByExtension = archive.attachmentCountsByExtension
        sourceIdentityAvailable = archive.sourceIdentity != nil
        sourceIdentityKind = archive.sourceIdentity?.kind ?? "none"
    }

    /// A refusal, described without saying anything about the file.
    ///
    /// - Parameter containerValid: whether the ZIP itself was fine. The two
    ///   gates are reported separately on purpose: "the container is sound but
    ///   its transcript is a shape we do not recognise" and "this is not a
    ///   usable ZIP" are different results and must not collapse into one word.
    init(failure: String, containerValid: Bool) {
        self.containerValid = containerValid
        transcriptRecognized = false
        failureReason = failure
        entryCount = 0
        transcriptCandidateCount = 0
        chosenTranscriptExtension = ""
        chosenTranscriptIndex = -1
        transcriptShape = "none"
        recordCount = 0
        attributionAvailable = false
        perMessageTimeAvailable = false
        firstSentAtText = nil
        lastSentAtText = nil
        timeZoneIdentifier = nil
        attachmentCountsByExtension = [:]
        sourceIdentityAvailable = false
        sourceIdentityKind = "none"
    }

    static func extensionOf(_ name: String) -> String {
        let last = name.split(separator: "/").last.map(String.init) ?? ""
        guard let dot = last.lastIndex(of: "."), dot != last.startIndex else { return "" }
        return String(last[last.index(after: dot)...]).lowercased()
    }

    /// Stable, greppable lines for an acceptance run.
    var reportLines: [String] {
        guard transcriptRecognized else {
            return [
                "container valid: \(containerValid ? "yes" : "no")",
                "transcript recognized: no (\(failureReason ?? "unknown"))",
            ]
        }
        let attachments = attachmentCountsByExtension
            .sorted { $0.key < $1.key }
            .map { "\($0.key)=\($0.value)" }
            .joined(separator: " ")
        var lines = [
            "container valid: yes",
            "transcript recognized: yes",
            "transcript shape: \(transcriptShape)",
            "record count: \(recordCount)",
            "attribution available: \(attributionAvailable ? "yes" : "no")",
            "per-message time available: \(perMessageTimeAvailable ? "yes" : "no")",
            "entry count: \(entryCount)",
            "transcript candidates: \(transcriptCandidateCount)",
            "chosen transcript: extension=\(chosenTranscriptExtension.isEmpty ? "(none)" : chosenTranscriptExtension) index=\(chosenTranscriptIndex)",
        ]
        if let firstSentAtText, let lastSentAtText {
            lines.append("first timestamp: \(firstSentAtText)")
            lines.append("last timestamp: \(lastSentAtText)")
        }
        if let timeZoneIdentifier {
            lines.append("interpreted in timezone: \(timeZoneIdentifier)")
        }
        lines.append("attachments by extension: \(attachments.isEmpty ? "(none)" : attachments)")
        lines.append("source identity available: \(sourceIdentityAvailable ? "yes" : "no")")
        lines.append("source identity kind: \(sourceIdentityKind)")
        return lines
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
