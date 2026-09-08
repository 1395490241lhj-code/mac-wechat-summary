import Foundation

/// One message as WeChat itself wrote it into a native export.
///
/// This is **not** a `PersistedMessage` and must not be confused with one.
/// The capture path observes pixels and can only say *when we saw* a message
/// (`firstObservedAt`) plus whatever display string WeChat happened to render
/// (`visibleTime`, which F-005 says is not a date). A native export is a
/// different class of evidence: WeChat states a calendar date and a wall-clock
/// minute for every message. That belongs in its own field, `sentAt`, and must
/// never be written into `firstObservedAt` -- we did not observe these messages
/// at all, WeChat told us about them.
struct WeChatArchiveMessage: Sendable, Equatable {
    /// Position in the transcript, from 0, in the order WeChat wrote it.
    let sequence: Int
    /// Exactly the bytes WeChat wrote between the `·` marker and the end of
    /// the line. Not trimmed, not normalised: until a real export shows that
    /// the separator carries padding, any cleanup would be us editing the
    /// user's data on a guess.
    let sender: String
    /// The parsed instant, to **minute** precision. Never claims seconds.
    let sentAt: Date
    /// The exact text WeChat wrote, kept verbatim so the parse can always be
    /// audited against the source and so a future timezone correction has
    /// something to re-derive from.
    let sentAtText: String
    /// The message body, verbatim apart from the record separator. Interior and
    /// edge whitespace inside a body is content and is not trimmed.
    let text: String
}

enum WeChatTranscriptError: Error, Equatable, Sendable {
    /// The text has no WeChat record header where one must be, or carries
    /// content before the first record. Not a transcript.
    case notANativeTranscript
    /// A record header matched the shape but its timestamp is not a real date.
    case unparsableTimestamp
}

/// Parses the TXT that macOS WeChat writes into a native chat export.
///
/// The grammar, one record:
///
///     ·发送者
///     2026年9月7日 20:35
///     正文（可跨多行，可为空）
///
/// Records run back to back; a body ends where the next header begins.
///
/// **Provenance.** This grammar comes from Dukou's reader and from the Python
/// reference importer on `feature/dukou-archive-importer`. It has **not** been
/// checked against a real export produced by WeChat on this machine. Until it
/// has, every claim here is about the grammar we were told about.
enum WeChatNativeTranscriptParser {
    /// The timezone a native export is interpreted in.
    ///
    /// A native export writes `2026年9月7日 20:35` and **no offset**. There is
    /// nothing in the file that says which zone that wall-clock reading belongs
    /// to, so it is read in the timezone of the Mac doing the import. That is a
    /// documented assumption, not a fact recovered from the archive: importing
    /// the same ZIP on a Mac set to another zone yields different instants, and
    /// `sentAtText` is kept verbatim so a corrected reading is always derivable
    /// without re-reading the archive.
    static let defaultTimeZone = TimeZone.current

    /// `·` then a sender to end of line, then a `yyyy年M月d日 H:mm` line.
    ///
    /// The hour is 1-2 digits because a zero-padded reading is not guaranteed;
    /// the minute is always two.
    private static let headerPattern = #"(?m)^·([^\n]+)\n(\d{4}年\d{1,2}月\d{1,2}日 \d{1,2}:\d{2})(?:\n|$)"#

    private static let headerExpression = try! NSRegularExpression(
        pattern: headerPattern, options: []
    )

    /// A formatter fixed in every dimension that could otherwise be inherited
    /// from the user's settings: an ICU-stable locale, an explicit Gregorian
    /// calendar, an explicit timezone, and strict parsing. A formatter that
    /// picks up a Buddhist or Japanese calendar from system preferences would
    /// silently produce dates centuries away.
    static func formatter(timeZone: TimeZone = defaultTimeZone) -> DateFormatter {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = timeZone
        formatter.dateFormat = "yyyy年M月d日 H:mm"
        formatter.isLenient = false
        return formatter
    }

    /// - Returns: every message, in transcript order.
    /// - Throws: `WeChatTranscriptError` when the text is not a native
    ///   transcript. Never returns an empty array without throwing.
    static func parse(
        _ body: String, timeZone: TimeZone = defaultTimeZone
    ) throws -> [WeChatArchiveMessage] {
        // Normalise line endings, and strip **one leading** byte-order mark.
        //
        // A BOM at the very start is an encoding marker. The same scalar
        // anywhere else is U+FEFF ZERO WIDTH NO-BREAK SPACE -- ordinary text a
        // user can and does send -- so removing it globally would silently
        // rewrite message bodies. Only position 0 is an encoding question.
        var normalized = body
            .replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")
        if normalized.hasPrefix("\u{FEFF}") { normalized.removeFirst() }

        let scalars = normalized as NSString
        let matches = headerExpression.matches(
            in: normalized, options: [], range: NSRange(location: 0, length: scalars.length)
        )
        guard let first = matches.first else { throw WeChatTranscriptError.notANativeTranscript }

        // A BOM or blank lines before the first record are tolerated; any other
        // leading content means this is some other TXT that merely contains a
        // WeChat-shaped line.
        let preamble = scalars.substring(to: first.range.location)
        guard preamble.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw WeChatTranscriptError.notANativeTranscript
        }

        let dateFormatter = formatter(timeZone: timeZone)
        var messages: [WeChatArchiveMessage] = []
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
            // Only the newline that separates this record from the next is
            // removed. Spaces and interior blank lines are message content:
            // trimming them would quietly rewrite what the user wrote.
            if text.hasSuffix("\n") { text.removeLast() }

            messages.append(WeChatArchiveMessage(
                sequence: index,
                sender: sender,
                sentAt: sentAt,
                sentAtText: sentAtText,
                text: text
            ))
        }
        return messages
    }
}
