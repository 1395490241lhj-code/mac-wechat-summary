import Foundation

// B7.1 -- the transcript reader's presentation layer.
//
// Everything here is pure: it turns canonical rows into the strings a reader
// sees, and groups them. It never sorts, never merges, and never re-identifies
// a row. TranscriptRow.id is the canonical reveal anchor, carried through
// untouched, so restyling a transcript cannot change where a search hit lands.

/// Which source a transcript row came from.
///
/// The source decides the honest wording for a row that carries no sender, and
/// the provenance words read out to VoiceOver. Two archive shapes are never
/// treated as one author: an unattributed record is not an attributed record
/// whose sender happens to be missing.
enum TranscriptSource: Equatable {
    case visual
    case archiveAttributed
    case archiveUnattributed

    /// Stated once in the context header, not repeated on every row.
    var contextLabel: String {
        switch self {
        case .visual: "Visual capture"
        case .archiveAttributed, .archiveUnattributed: "Archive"
        }
    }

    /// What a single row still has to say about itself, in words.
    var rowProvenance: String {
        switch self {
        case .visual: "Visual capture"
        case .archiveAttributed: "Attributed archive record"
        case .archiveUnattributed: "Unattributed archive record"
        }
    }
}

/// What kind of time a row is filed under.
///
/// Visual capture never observes a sent time, only the moment a message was
/// first seen on screen. Keeping the two apart is what lets the date separator
/// be honest instead of merely plausible.
enum TranscriptTimeKind: Equatable {
    /// When we first saw the message on screen. NOT when it was sent.
    case observed
    /// A timestamp the archive itself carried.
    case archiveSent
}

/// One canonical row, resolved to the values a reader sees.
struct TranscriptRow: Identifiable, Equatable {
    /// The canonical reveal anchor. Grouping and styling must not change it.
    let id: ContextRevealAnchor
    let source: TranscriptSource
    /// Always populated. A row with no sender says so in the words this source
    /// actually supports rather than showing a blank.
    let sender: String
    /// A time string the source itself displayed, shown exactly as displayed.
    /// Never parsed into a Date.
    let literalTime: String?
    /// The date this row is filed under, present only when the source really
    /// has one. A row without one takes part in no date separator.
    let date: Date?
    let timeKind: TranscriptTimeKind?
    /// Lightweight time for the row's own line.
    let shortTime: String?
    let body: String

    /// The row's own time, as the reader sees it.
    ///
    /// A literal time is prefixed only when the wording matters: "WeChat
    /// showed" keeps a display string from being mistaken for one of our own
    /// timestamps, while an archive's raw timestamp stands on its own.
    var timeLabel: String? {
        if let literalTime {
            return source == .visual ? "WeChat showed: \(literalTime)" : literalTime
        }
        return shortTime
    }

    init(
        id: ContextRevealAnchor,
        source: TranscriptSource,
        sender: String,
        literalTime: String?,
        date: Date?,
        timeKind: TranscriptTimeKind?,
        shortTime: String?,
        body: String
    ) {
        self.id = id
        self.source = source
        self.sender = sender
        self.literalTime = literalTime
        self.date = date
        self.timeKind = timeKind
        self.shortTime = shortTime
        self.body = body
    }

    init(visual message: PersistedMessage) {
        let visible = message.visibleTime.flatMap { $0.isEmpty ? nil : $0 }
        self.init(
            id: .visualMessage(message.id),
            source: .visual,
            sender: message.sender ?? "Sender unknown",
            literalTime: visible,
            // The observation time, never a sent time: the row is filed under
            // when we saw it and the separator says so.
            date: message.firstObservedAt,
            timeKind: .observed,
            shortTime: message.firstObservedAt.formatted(date: .omitted, time: .shortened),
            body: message.text ?? "[No text retained]"
        )
    }

    init(archive record: ArchiveEvidenceRecord) {
        // A structured timestamp is the honest one. The raw string is shown
        // verbatim only when nothing better was stored, and is never parsed:
        // a display string like "yesterday 14:30" is not a date, and guessing
        // one would be a lie.
        let reliable = record.sentAt
        self.init(
            id: .archiveRecord(importID: record.importID, sequence: record.sequence),
            source: record.shape == .unattributed ? .archiveUnattributed : .archiveAttributed,
            sender: record.sender
                ?? (record.shape == .unattributed ? "Unattributed record" : "Unknown sender"),
            literalTime: reliable == nil ? record.sentAtText : nil,
            date: reliable,
            timeKind: reliable == nil ? nil : .archiveSent,
            shortTime: reliable?.formatted(date: .omitted, time: .shortened),
            body: record.text
        )
    }
}

/// Consecutive rows that share a sender and a day, so a sender is written once
/// instead of on every line.
///
/// Presentation only. The canonical order is kept, no two rows are merged, and
/// every row inside the group keeps its own reveal anchor.
struct TranscriptGroup: Identifiable, Equatable {
    /// Start of day, or nil when the source had no reliable date for the row.
    let date: Date?
    let timeKind: TranscriptTimeKind?
    let sender: String
    let source: TranscriptSource
    var rows: [TranscriptRow]

    /// A group never carries an anchor of its own: it is identified by its
    /// first row, so scrolling can only ever target a real canonical row.
    var id: ContextRevealAnchor { rows[0].id }

    func dateLabel(reference: Date, calendar: Calendar = .current) -> String? {
        guard let date, let timeKind else { return nil }
        return TranscriptDateLabel.label(
            for: date, kind: timeKind, reference: reference, calendar: calendar
        )
    }
}

enum TranscriptGrouping {
    /// Splits rows into runs of the same sender on the same day.
    ///
    /// A new group starts only when the row's sender/provenance identity
    /// changes or it lands on another reliable day. There is deliberately no
    /// time-gap heuristic: an invented "five minutes later is a new turn"
    /// rule is an interpretation the evidence does not support, and it would
    /// make the reader's hierarchy depend on a number nobody can check.
    static func groups(_ rows: [TranscriptRow], calendar: Calendar = .current) -> [TranscriptGroup] {
        var groups: [TranscriptGroup] = []
        for row in rows {
            let day = row.date.map { calendar.startOfDay(for: $0) }
            if var last = groups.last,
               last.sender == row.sender,
               last.source == row.source,
               last.date == day {
                last.rows.append(row)
                groups[groups.count - 1] = last
            } else {
                groups.append(
                    TranscriptGroup(
                        date: day, timeKind: row.timeKind,
                        sender: row.sender, source: row.source, rows: [row]
                    )
                )
            }
        }
        return groups
    }

    /// Which groups open a new day, walking the transcript in order.
    ///
    /// A separator marks a calendar-day transition, not a group boundary: one
    /// day can hold several sender groups when the speakers alternate, and
    /// repeating "Yesterday" for each of them would say something untrue about
    /// the timeline. Rows with no reliable date never claim a day, so they
    /// neither emit a separator nor suppress the next real one.
    static func dateSeparatorIndices(in groups: [TranscriptGroup]) -> Set<Int> {
        var indices: Set<Int> = []
        var lastDay: Date?
        for (index, group) in groups.enumerated() {
            guard let day = group.date else { continue }
            if day != lastDay {
                indices.insert(index)
                lastDay = day
            }
        }
        return indices
    }
}

enum TranscriptDateLabel {
    /// A separator that always names the kind of time it is about, so an
    /// observation date can never be read as a sent date.
    ///
    /// reference and calendar are injected so the result is deterministic
    /// under test; the app passes the current date.
    static func label(
        for date: Date,
        kind: TranscriptTimeKind,
        reference: Date,
        calendar: Calendar = .current
    ) -> String {
        let day = calendar.startOfDay(for: date)
        let today = calendar.startOfDay(for: reference)
        let yesterday = calendar.date(byAdding: .day, value: -1, to: today) ?? today
        if day == today {
            return kind == .observed ? "Observed today" : "Today"
        }
        if day == yesterday {
            return kind == .observed ? "Observed yesterday" : "Yesterday"
        }
        let formatted = date.formatted(
            Date.FormatStyle().month(.abbreviated).day().year()
        )
        return kind == .observed ? "Observed \(formatted)" : formatted
    }
}

/// Wording for the states where a transcript has nothing to show. These are
/// different problems with different fixes, so they never collapse into one
/// "no data" line.
enum TranscriptState: Equatable {
    case storageOff
    case storeUnavailable
    case neverCaptured
    case emptyImport
    case noRetainedRows
    case contextVanished
    case searchHitVanished

    var text: String {
        switch self {
        case .storageOff:
            "Local message storage is off, so nothing here is being kept. "
                + "Turn it on in Settings to start recording."
        case .storeUnavailable:
            "Local message storage is on, but the message store could not be "
                + "opened, so nothing can be read right now."
        case .neverCaptured:
            "No conversations captured yet. Share a WeChat window and keep the "
                + "chat name at the top of the window visible."
        case .emptyImport:
            "This import has no readable records."
        case .noRetainedRows:
            "Nothing is retained here any more. Retention removes older messages "
                + "automatically."
        case .contextVanished:
            "This conversation is no longer in local history. It was removed by "
                + "retention, or local history was cleared."
        case .searchHitVanished:
            "This search result is no longer retained, so there is nothing to "
                + "reveal here."
        }
    }
}

// Consumer conversation list, separate from canonical transcript grouping.
enum ConsumerConversationID: Hashable {
    case archiveImport(Int64)
    case visualConversation(Int64)
}

struct ConsumerConversationRow: Identifiable, Equatable {
    let id: ConsumerConversationID
    let title: String
    /// Save/observation time, never represented as the last sent-message time.
    let date: Date
    let note: String

    /// The visual row splits date and time; its spoken label retains both.
    var accessibilityDescription: String {
        "\(title), \(date.formatted(date: .complete, time: .omitted)), \(note)"
    }

    static func rows(archive: ArchiveEvidenceSnapshot, visual: CaptureLedger) -> [Self] {
        let saved = archive.storeState == .ready ? archive.imports.map {
            Self(id: .archiveImport($0.id), title: $0.displayName ?? "Saved conversation", date: $0.importedAt,
                 note: "Saved at \($0.importedAt.formatted(date: .omitted, time: .shortened))")
        } : []
        let observed = visual.storeState == .ready ? visual.conversations.map {
            Self(id: .visualConversation($0.id), title: $0.title, date: $0.lastCapturedAt,
                 note: "Last seen at \($0.lastCapturedAt.formatted(date: .omitted, time: .shortened))")
        } : []
        return (saved + observed).sorted {
            if $0.date != $1.date { return $0.date > $1.date }
            switch ($0.id, $1.id) {
            case (.archiveImport(let left), .archiveImport(let right)),
                 (.visualConversation(let left), .visualConversation(let right)): return left < right
            case (.archiveImport, .visualConversation): return true
            case (.visualConversation, .archiveImport): return false
            }
        }
    }
}

/// User-facing completeness wording; unknown states never imply a complete history.
enum FollowUpCoveragePresentation {
    static func message(for status: String) -> String? {
        switch status {
        case "complete": nil
        case "partial": "Based on part of this conversation."
        case "unavailable": "Conversation history is unavailable."
        case "not_observed": "Conversation history has not been checked."
        default: "Full conversation history could not be confirmed."
        }
    }
}
