import XCTest
@testable import WeChatCompanion

/// B7.1 -- the transcript presentation layer.
///
/// The contract these tests protect is honesty plus identity: a row is filed
/// under the time the source really has, an unattributed record never reads
/// like a senderless attributed one, and grouping never changes which row a
/// search hit scrolls to.
final class TranscriptPresentationTests: XCTestCase {
    // MARK: - Grouping

    func testConsecutiveSameSenderOnSameDayIsOneGroup() {
        let groups = TranscriptGrouping.groups(
            [
                visual(id: 1, sender: "Ana", at: at(12, 1)),
                visual(id: 2, sender: "Ana", at: at(12, 2)),
            ],
            calendar: utc
        )
        XCTAssertEqual(groups.count, 1)
        XCTAssertEqual(groups[0].rows.map(\.id), [.visualMessage(1), .visualMessage(2)])
    }

    func testSenderChangeStartsNewGroup() {
        let groups = TranscriptGrouping.groups(
            [
                visual(id: 1, sender: "Ana", at: at(12, 1)),
                visual(id: 2, sender: "Bo", at: at(12, 2)),
                visual(id: 3, sender: "Ana", at: at(12, 3)),
            ],
            calendar: utc
        )
        XCTAssertEqual(groups.count, 3)
    }

    func testReliableDayBoundaryStartsNewGroup() {
        let groups = TranscriptGrouping.groups(
            [
                visual(id: 1, sender: "Ana", at: at(23, 50)),
                visual(id: 2, sender: "Ana", at: at(0, 10, dayOffset: 1)),
            ],
            calendar: utc
        )
        XCTAssertEqual(groups.count, 2)
    }

    /// An invented "five minutes later is a new turn" rule would make the
    /// reader's hierarchy depend on a number nobody can check.
    func testNoTimeGapHeuristicSplitsASenderRun() {
        let groups = TranscriptGrouping.groups(
            [
                visual(id: 1, sender: "Ana", at: at(0, 0)),
                visual(id: 2, sender: "Ana", at: at(23, 0)),
            ],
            calendar: utc
        )
        XCTAssertEqual(groups.count, 1)
    }

    func testMissingSenderSaysSoAndKeepsItsOwnGroup() {
        let rows = [
            visual(id: 1, sender: nil, at: at(9, 0)),
            visual(id: 2, sender: nil, at: at(9, 1)),
        ]
        XCTAssertEqual(Set(rows.map(\.sender)), ["Sender unknown"])
        let groups = TranscriptGrouping.groups(rows, calendar: utc)
        XCTAssertEqual(groups.count, 1)
    }

    /// An unattributed record is not an attributed record that lost a sender.
    func testUnattributedArchiveNeverMergesWithUnknownSender() {
        let rows = [
            archive(sequence: 1, shape: .attributed, sender: nil, sentAt: at(10, 0)),
            archive(sequence: 2, shape: .unattributed, sender: nil, sentAt: at(10, 1)),
        ]
        XCTAssertEqual(rows[0].sender, "Unknown sender")
        XCTAssertEqual(rows[1].sender, "Unattributed record")
        XCTAssertEqual(TranscriptGrouping.groups(rows, calendar: utc).count, 2)
    }

    func testUndatedRowsGetNoDateSeparator() {
        let groups = TranscriptGrouping.groups(
            [archive(sequence: 1, shape: .attributed, sender: "Ana", sentAtText: "yesterday 14:30")],
            calendar: utc
        )
        XCTAssertEqual(groups.count, 1)
        XCTAssertNil(groups[0].date)
        XCTAssertNil(groups[0].dateLabel(reference: at(9, 0), calendar: utc))
    }

    // MARK: - Date separators

    /// A date separator marks a day transition, not a speaker change: several
    /// senders on one day get one "Yesterday", not one per group.
    func testSenderChangesOnTheSameDayGetOneDateSeparator() {
        let groups = TranscriptGrouping.groups(
            [
                visual(id: 1, sender: "Ana", at: at(12, 1)),
                visual(id: 2, sender: "Bo", at: at(12, 2)),
                visual(id: 3, sender: "Cy", at: at(12, 3)),
            ],
            calendar: utc
        )
        XCTAssertEqual(groups.count, 3)
        XCTAssertEqual(TranscriptGrouping.dateSeparatorIndices(in: groups), [0])
    }

    func testSameSenderOnANewDayGetsASecondSeparator() {
        let groups = TranscriptGrouping.groups(
            [
                visual(id: 1, sender: "Ana", at: at(23, 50)),
                visual(id: 2, sender: "Ana", at: at(0, 10, dayOffset: 1)),
            ],
            calendar: utc
        )
        XCTAssertEqual(TranscriptGrouping.dateSeparatorIndices(in: groups), [0, 1])
    }

    func testSenderAndDayBothChangingGetsASecondSeparator() {
        let groups = TranscriptGrouping.groups(
            [
                visual(id: 1, sender: "Ana", at: at(23, 50)),
                visual(id: 2, sender: "Bo", at: at(0, 10, dayOffset: 1)),
            ],
            calendar: utc
        )
        XCTAssertEqual(TranscriptGrouping.dateSeparatorIndices(in: groups), [0, 1])
    }

    /// A raw display string is not a date, so an undated group neither emits a
    /// separator of its own nor hides the next real day.
    func testUndatedGroupsNeitherAddNorSuppressSeparators() {
        let groups = TranscriptGrouping.groups(
            [
                visual(id: 1, sender: "Ana", at: at(12, 1)),
                archive(sequence: 1, shape: .attributed, sender: "Ana", sentAtText: "yesterday 14:30"),
                visual(id: 2, sender: "Bo", at: at(9, 0, dayOffset: 1)),
            ],
            calendar: utc
        )
        XCTAssertEqual(groups.map(\.date), [utc.startOfDay(for: at(12, 1)), nil, utc.startOfDay(for: at(9, 0, dayOffset: 1))])
        XCTAssertEqual(TranscriptGrouping.dateSeparatorIndices(in: groups), [0, 2])
    }

    // MARK: - Reveal anchors

    /// The B6.2 contract: grouping is a presentation wrapper, so the set of
    /// scrollable anchors must be byte-for-byte the same before and after.
    func testGroupingPreservesEveryCanonicalRevealAnchor() {
        let rows = [
            visual(id: 1, sender: "Ana", at: at(12, 1)),
            visual(id: 2, sender: "Ana", at: at(12, 2)),
            visual(id: 3, sender: "Bo", at: at(12, 3)),
        ]
        let grouped = TranscriptGrouping.groups(rows, calendar: utc).flatMap(\.rows)
        XCTAssertEqual(grouped.map(\.id), rows.map(\.id))
        XCTAssertEqual(Set(grouped.map(\.id)), [.visualMessage(1), .visualMessage(2), .visualMessage(3)])
    }

    /// Choosing which groups carry a separator must not touch the rows or their
    /// anchors: the B6.2 reveal target is identical with and without any date
    /// separator at all.
    func testDateSeparatorsDoNotDisturbCanonicalAnchors() {
        let rows = [
            visual(id: 1, sender: "Ana", at: at(12, 1)),
            visual(id: 2, sender: "Bo", at: at(12, 2)),
            archive(sequence: 7, shape: .unattributed, sender: nil, sentAtText: "yesterday 14:30"),
            visual(id: 3, sender: "Ana", at: at(9, 0, dayOffset: 1)),
        ]
        let groups = TranscriptGrouping.groups(rows, calendar: utc)
        XCTAssertEqual(groups.flatMap(\.rows).map(\.id), rows.map(\.id))
        XCTAssertEqual(
            groups.map(\.id),
            [.visualMessage(1), .visualMessage(2), .archiveRecord(importID: 42, sequence: 7), .visualMessage(3)]
        )
    }

    func testArchiveAnchorsStayImportAndSequence() {
        let row = archive(sequence: 7, shape: .attributed, sender: "Ana")
        XCTAssertEqual(row.id, .archiveRecord(importID: 42, sequence: 7))
    }

    // MARK: - Retained count wording

    /// The count row used to read "7 messages kept messages": the word lived in
    /// both the presentation string and the view. It belongs to the string.
    func testRetainedCountWordingIsSingularAndPlural() {
        XCTAssertEqual(ledger(kept: 1).conversations[0].retainedCount, "1 message kept")
        XCTAssertEqual(ledger(kept: 7).conversations[0].retainedCount, "7 messages kept")
    }

    /// A group is identified by its first row, so nothing can ever scroll to a
    /// container that has no canonical row behind it.
    func testGroupIdentityIsItsFirstRow() {
        let groups = TranscriptGrouping.groups(
            [
                visual(id: 1, sender: "Ana", at: at(12, 1)),
                visual(id: 2, sender: "Ana", at: at(12, 2)),
            ],
            calendar: utc
        )
        XCTAssertEqual(groups[0].id, .visualMessage(1))
    }

    // MARK: - Time semantics

    func testVisualDatesAreObservedNotSent() {
        let row = visual(id: 1, sender: "Ana", at: at(12, 0))
        XCTAssertEqual(row.timeKind, .observed)
        XCTAssertEqual(
            TranscriptGrouping.groups([row], calendar: utc)[0].dateLabel(
                reference: at(12, 0), calendar: utc
            ),
            "Observed today"
        )
    }

    func testVisibleTimeIsShownLiterallyAndPrefixed() {
        let message = PersistedMessage(
            id: 1, conversationID: 1, sequence: 1, sender: "Ana", ownership: .unknown,
            visibleTime: "\u{6628}\u{5929} 14:30", text: "body", kind: .text,
            confidence: 1, firstObservedAt: at(12, 0)
        )
        XCTAssertEqual(TranscriptRow(visual: message).timeLabel, "WeChat showed: \u{6628}\u{5929} 14:30")
    }

    func testStructuredArchiveSentAtFormatsAsAnArchiveDate() {
        let row = archive(sequence: 1, shape: .attributed, sender: "Ana", sentAt: at(9, 0))
        XCTAssertEqual(row.timeKind, .archiveSent)
        XCTAssertEqual(
            TranscriptGrouping.groups([row], calendar: utc)[0].dateLabel(
                reference: at(9, 0), calendar: utc
            ),
            "Today"
        )
    }

    /// A display string like "yesterday 14:30" is not a date. Parsing it would
    /// invent one.
    func testSentAtTextOnlyStaysLiteralAndInfersNoDate() {
        let row = archive(sequence: 1, shape: .attributed, sender: "Ana", sentAtText: "yesterday 14:30")
        XCTAssertNil(row.date)
        XCTAssertNil(row.timeKind)
        XCTAssertEqual(row.timeLabel, "yesterday 14:30")
    }

    func testNoTimestampAtAllShowsNothingExtra() {
        let row = archive(sequence: 1, shape: .attributed, sender: "Ana")
        XCTAssertNil(row.timeLabel)
    }

    func testDateLabelIsDeterministicViaInjectedReference() {
        let date = at(9, 0, dayOffset: -2)
        for (kind, expected) in [(TranscriptTimeKind.observed, "Observed yesterday"), (TranscriptTimeKind.archiveSent, "Yesterday")] {
            XCTAssertEqual(
                TranscriptDateLabel.label(for: at(9, 0, dayOffset: -1), kind: kind, reference: at(9, 0), calendar: utc),
                expected
            )
        }
        // Older days fall back to an absolute date, which must not depend on
        // the machine's clock.
        XCTAssertTrue(TranscriptDateLabel.label(for: date, kind: .archiveSent, reference: at(9, 0), calendar: utc).contains("2026"))
    }

    // MARK: - Content honesty

    func testMissingTextIsShownAsAPlaceholder() {
        let message = PersistedMessage(
            id: 1, conversationID: 1, sequence: 1, sender: "Ana", ownership: .unknown,
            visibleTime: nil, text: nil, kind: .text, confidence: 1, firstObservedAt: at(12, 0)
        )
        XCTAssertEqual(TranscriptRow(visual: message).body, "[No text retained]")
    }

    func testSourcesCarryDistinctRowProvenance() {
        XCTAssertEqual(TranscriptSource.visual.rowProvenance, "Visual capture")
        XCTAssertEqual(TranscriptSource.archiveAttributed.rowProvenance, "Attributed archive record")
        XCTAssertEqual(TranscriptSource.archiveUnattributed.rowProvenance, "Unattributed archive record")
    }

    /// These are different problems with different fixes, so the empty states
    /// must not collapse into one "no data" line.
    func testEmptyStatesReadDifferently() {
        let states: [TranscriptState] = [
            .storageOff, .storeUnavailable, .neverCaptured, .emptyImport,
            .noRetainedRows, .contextVanished, .searchHitVanished,
        ]
        let texts = states.map(\.text)
        XCTAssertEqual(Set(texts).count, texts.count)
        XCTAssertNotEqual(TranscriptState.noRetainedRows.text, TranscriptState.contextVanished.text)
    }

    // MARK: - Fixtures

    private var utc: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        return calendar
    }

    /// 2026-09-29 at the given UTC time, optionally on a later day.
    private func at(_ hour: Int, _ minute: Int, dayOffset: Int = 0) -> Date {
        utc.date(
            from: DateComponents(
                timeZone: TimeZone(identifier: "UTC"),
                year: 2026, month: 9, day: 29 + dayOffset, hour: hour, minute: minute
            )
        )!
    }

    private func visual(id: Int64, sender: String?, at observed: Date) -> TranscriptRow {
        TranscriptRow(visual: PersistedMessage(
            id: id, conversationID: 1, sequence: id, sender: sender, ownership: .unknown,
            visibleTime: nil, text: "body \(id)", kind: .text, confidence: 1,
            firstObservedAt: observed
        ))
    }

    private func archive(
        sequence: Int,
        shape: ArchiveEvidenceShape,
        sender: String?,
        sentAt: Date? = nil,
        sentAtText: String? = nil
    ) -> TranscriptRow {
        TranscriptRow(archive: ArchiveEvidenceRecord(
            importID: 42, importedAt: at(8, 0), shape: shape, sequence: sequence,
            sender: sender, sentAt: sentAt, sentAtText: sentAtText, text: "body \(sequence)"
        ))
    }

    private func ledger(kept: Int) -> CaptureLedgerPresentation {
        CaptureLedgerPresentation(ledger: CaptureLedger(
            storeState: .ready,
            conversations: [CapturedConversationSummary(
                id: 1, title: "Chat", retainedMessageCount: kept,
                firstCapturedAt: at(8, 0), lastCapturedAt: at(9, 0)
            )]
        ))
    }
}
