import Foundation

/// One message from an **attributed** native export (Shape A).
///
/// This is **not** a `PersistedMessage` and must not be confused with one.
/// The capture path observes pixels and can only say *when we saw* a message
/// (`firstObservedAt`) plus whatever display string WeChat rendered
/// (`visibleTime`, which F-005 says is not a date). A native export is a
/// different class of evidence: WeChat states a sender, a calendar date and a
/// wall-clock minute. That belongs in its own fields and must never be written
/// into `firstObservedAt` -- we did not observe these messages at all, WeChat
/// told us about them.
struct WeChatAttributedArchiveMessage: Sendable, Equatable {
    /// Position in the transcript, from 0, in the order WeChat wrote it.
    let sequence: Int
    /// Exactly the bytes WeChat wrote between the `·` marker and the end of
    /// the line. Not trimmed, not normalised.
    let sender: String
    /// The parsed instant, to **minute** precision. Never claims seconds.
    let sentAt: Date
    /// The exact text WeChat wrote, kept verbatim so the parse can always be
    /// audited against the source and so a future timezone correction has
    /// something to re-derive from.
    let sentAtText: String
    /// The message body, verbatim apart from the record separator.
    let text: String
}

/// One record from an **unattributed** native export (Shape B).
///
/// There is deliberately no `sender`, no `sentAt`, no `sentAtText` and no
/// ownership here, and none may be added. The export does not contain them, so
/// the *type* is what says so: a caller cannot accidentally read an attribution
/// that was never in the archive, because there is no field to read. Making
/// these optional on one shared struct would put that mistake one `?? ""` away.
struct WeChatUnattributedArchiveRecord: Sendable, Equatable {
    /// Position in the transcript, from 0, in the order WeChat wrote it.
    let sequence: Int
    /// The record's line **verbatim, including the leading `·`**.
    ///
    /// In Shape A that character is unambiguously a record marker, because a
    /// sender follows it. Nothing in a Shape B archive proves the same, and
    /// deleting a byte WeChat actually wrote on an analogy is worse than
    /// keeping one that may turn out to be structural. It is named
    /// `recordText`, not `messageText`: that a record corresponds one-to-one
    /// with a message is an assumption this artifact does not establish.
    let recordText: String
}

struct WeChatAttributedTranscript: Sendable, Equatable {
    let messages: [WeChatAttributedArchiveMessage]
    /// How the timestamps were interpreted. A native export carries no offset,
    /// so this is the reader's assumption, recorded.
    let timeZoneIdentifier: String
}

struct WeChatUnattributedTranscript: Sendable, Equatable {
    let records: [WeChatUnattributedArchiveRecord]
}

/// What a native export's transcript turned out to be.
///
/// A sum type rather than a struct with optional attribution. Two real shapes
/// have been observed from one conversation under one WeChat build (F-030), and
/// the difference between them is not a detail: one carries who spoke and when,
/// the other carries neither. A consumer must be forced to handle that split,
/// not offered a nil it can ignore.
enum WeChatNativeTranscript: Sendable, Equatable {
    case attributed(WeChatAttributedTranscript)
    case unattributed(WeChatUnattributedTranscript)

    /// Stable, privacy-safe label.
    var shapeName: String {
        switch self {
        case .attributed: "attributed"
        case .unattributed: "unattributed"
        }
    }

    /// Messages for Shape A, records for Shape B. Never a message count for an
    /// archive that has no messages in it.
    var recordCount: Int {
        switch self {
        case .attributed(let t): t.messages.count
        case .unattributed(let t): t.records.count
        }
    }

    var attributionAvailable: Bool {
        if case .attributed = self { return true }
        return false
    }

    var perMessageTimeAvailable: Bool { attributionAvailable }

    /// The timezone to persist beside an import: the interpretation an
    /// attributed transcript was read under, and `nil` for a shape that has no
    /// time to interpret. The schema enforces the same pairing.
    var timeZoneIdentifierForStorage: String? {
        switch self {
        case .attributed(let t): t.timeZoneIdentifier
        case .unattributed: nil
        }
    }
}

enum WeChatTranscriptError: Error, Equatable, Sendable {
    /// The text is neither shape. Includes hybrids: a file where some records
    /// are attributed and some are not is refused, never partially read.
    case notANativeTranscript
    /// A record header matched Shape A but its timestamp is not a real date.
    case unparsableTimestamp
}

/// Parses the TXT that macOS WeChat writes into a native chat export.
///
/// Two shapes have been observed from real exports (E-021, F-030):
///
///     Shape A — attributed          Shape B — unattributed
///     ·发送者                        ·记录正文
///     2026年9月7日 20:35             (blank)
///     正文（可跨多行，可为空）        ·下一条记录
///     (blank)                        (blank)
///
/// **They are parsed by two separate strict parsers, never by one permissive
/// pattern.** A regex loose enough to accept both would accept a hybrid too,
/// and a hybrid parsed as Shape A silently swallows every unattributed record
/// into the previous message's body. Classification is: try A strictly, then B
/// strictly, then fail.
///
/// **Provenance.** Shape A matches Dukou's `WeChatTranscriptRecord.parse` and
/// is verified against two real exports. Shape B is verified against one. What
/// selects between them is unknown.
enum WeChatNativeTranscriptParser {
    /// The timezone an attributed export is interpreted in.
    ///
    /// A native export writes `2026年9月7日 20:35` and **no offset**. Nothing in
    /// the file says which zone that wall-clock reading belongs to, so it is
    /// read in the timezone of the Mac doing the import. That is a documented
    /// assumption, not a fact recovered from the archive: the same ZIP imported
    /// on a Mac in another zone yields different instants, and `sentAtText` is
    /// kept verbatim so a corrected reading is always derivable.
    static let defaultTimeZone = TimeZone.current

    /// `·` then a sender to end of line, then a `yyyy年M月d日 H:mm` line.
    private static let headerPattern = #"(?m)^·([^\n]+)\n(\d{4}年\d{1,2}月\d{1,2}日 \d{1,2}:\d{2})(?:\n|$)"#

    private static let headerExpression = try! NSRegularExpression(
        pattern: headerPattern, options: []
    )

    /// A formatter fixed in every dimension that could otherwise be inherited
    /// from the user's settings: an ICU-stable locale, an explicit Gregorian
    /// calendar, an explicit timezone, and strict parsing. A formatter that
    /// picked up a Buddhist calendar from system preferences would silently
    /// produce dates centuries away.
    static func formatter(timeZone: TimeZone = defaultTimeZone) -> DateFormatter {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = timeZone
        formatter.dateFormat = "yyyy年M月d日 H:mm"
        formatter.isLenient = false
        return formatter
    }

    // MARK: - Classification

    /// Classifies a transcript into exactly one shape, or refuses it.
    static func parse(
        _ body: String, timeZone: TimeZone = defaultTimeZone
    ) throws -> WeChatNativeTranscript {
        let normalized = normalize(body)
        if let attributed = try parseAttributed(normalized, timeZone: timeZone) {
            return .attributed(attributed)
        }
        if let unattributed = parseUnattributed(normalized) {
            return .unattributed(unattributed)
        }
        throw WeChatTranscriptError.notANativeTranscript
    }

    /// Normalise line endings, and strip **one leading** byte-order mark.
    ///
    /// A BOM at the very start is an encoding marker. The same scalar anywhere
    /// else is U+FEFF ZERO WIDTH NO-BREAK SPACE -- ordinary text a user can
    /// send -- so removing it globally would silently rewrite record content.
    private static func normalize(_ body: String) -> String {
        var normalized = body
            .replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")
        if normalized.hasPrefix("\u{FEFF}") { normalized.removeFirst() }
        return normalized
    }

    /// Lines that begin a record. Used to prove a parse covered the whole file.
    private static func markerLineCount(_ normalized: String) -> Int {
        normalized.split(separator: "\n", omittingEmptySubsequences: false)
            .count { $0.hasPrefix("·") }
    }

    // MARK: - Shape A, strictly

    /// - Returns: nil when the text is simply not Shape A. Throws only when it
    ///   *is* Shape A and something inside it is malformed.
    private static func parseAttributed(
        _ normalized: String, timeZone: TimeZone
    ) throws -> WeChatAttributedTranscript? {
        let scalars = normalized as NSString
        let matches = headerExpression.matches(
            in: normalized, options: [], range: NSRange(location: 0, length: scalars.length)
        )
        guard let first = matches.first else { return nil }

        // A BOM or blank lines before the first record are tolerated; any other
        // leading content means this is some other TXT that merely contains a
        // WeChat-shaped line.
        let preamble = scalars.substring(to: first.range.location)
        guard preamble.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }

        // Every record-marker line must be a header. Without this a hybrid --
        // some records attributed, some not -- parses as Shape A and quietly
        // absorbs each unattributed record into the previous message's body.
        // Refusing costs us an attributed archive whose body line happens to
        // begin with `·`; accepting would cost us messages, silently.
        guard markerLineCount(normalized) == matches.count else { return nil }

        let dateFormatter = formatter(timeZone: timeZone)
        var messages: [WeChatAttributedArchiveMessage] = []
        messages.reserveCapacity(matches.count)

        for (index, match) in matches.enumerated() {
            let sender = scalars.substring(with: match.range(at: 1))
            let sentAtText = scalars.substring(with: match.range(at: 2))
            guard let sentAt = dateFormatter.date(from: sentAtText) else {
                throw WeChatTranscriptError.unparsableTimestamp
            }
            let bodyStart = match.range.location + match.range.length
            let bodyEnd = index + 1 < matches.count
                ? matches[index + 1].range.location
                : scalars.length
            var text = scalars.substring(with: NSRange(
                location: bodyStart, length: max(0, bodyEnd - bodyStart)
            ))
            // Remove only the record separator. Real exports end each record
            // with its own newline plus one blank line ("\n\n"); the last
            // record has just the newline. Spaces and *interior* blank lines
            // are content and survive.
            //
            // A body that genuinely ended with its own blank line is
            // indistinguishable from the separator and loses it. That is a
            // real, small loss, and the alternative -- keeping the separator --
            // would append a phantom blank line to every message in the file.
            if text.hasSuffix("\n\n") {
                text.removeLast(2)
            } else if text.hasSuffix("\n") {
                text.removeLast()
            }

            messages.append(WeChatAttributedArchiveMessage(
                sequence: index, sender: sender, sentAt: sentAt,
                sentAtText: sentAtText, text: text
            ))
        }
        return WeChatAttributedTranscript(
            messages: messages, timeZoneIdentifier: timeZone.identifier
        )
    }

    // MARK: - Shape B, strictly

    /// The narrowest grammar the real archive actually shows: a `·`-prefixed
    /// line, then exactly one blank line or end of input, repeated. Every
    /// non-blank line must be a record. Anything else -- a date line, a
    /// continuation, two blank lines between records -- is not Shape B.
    ///
    /// - Returns: nil when the text is not Shape B.
    private static func parseUnattributed(_ normalized: String) -> WeChatUnattributedTranscript? {
        var lines = normalized.split(separator: "\n", omittingEmptySubsequences: false)
            .map(String.init)
        // A BOM or blank lines before the first record are tolerated, as is the
        // final newline every observed export ends with.
        while let first = lines.first, first.trimmingCharacters(in: .whitespaces).isEmpty {
            lines.removeFirst()
        }
        while let last = lines.last, last.isEmpty { lines.removeLast() }
        guard !lines.isEmpty else { return nil }

        var records: [WeChatUnattributedArchiveRecord] = []
        var index = 0
        while index < lines.count {
            let line = lines[index]
            guard line.hasPrefix("·") else { return nil }
            records.append(WeChatUnattributedArchiveRecord(
                sequence: records.count, recordText: line
            ))
            index += 1
            guard index < lines.count else { break }
            // Exactly one blank line separates records.
            guard lines[index].isEmpty else { return nil }
            index += 1
            // ...and it must be followed by another record, never by a second
            // blank or by the end of the file.
            guard index < lines.count else { return nil }
        }
        guard !records.isEmpty else { return nil }
        return WeChatUnattributedTranscript(records: records)
    }
}
