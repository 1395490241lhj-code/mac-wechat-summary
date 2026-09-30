import Foundation
import Testing
@testable import WeChatCompanion

// Every string below is invented for these tests. No real chat content is
// used, read, or logged anywhere in this file.

/// Two self-contained fixtures rather than shared helpers. The equivalents in
/// `LocalMessageSearchTests.swift` and `ArchivePersistenceTests.swift` are both
/// `private`, and lifting either into module scope would collide.
private func makeSearchVisualMessage(
    _ text: String
) -> ExtractedVisibleMessage {
    ExtractedVisibleMessage(
        sender: nil,
        ownership: .other,
        visibleTime: nil,
        text: text,
        kind: .text,
        confidence: 0.9
    )
}

private func makeSearchTestHistory(
    conversations: [String: [ExtractedVisibleMessage]]
) async -> LocalMessageHistory {
    let history = makeTestMessageHistory()
    await history.setEnabled(true)
    for (title, messages) in conversations {
        await history.ingestor()?.ingest(
            ExtractedConversationFrame(
                capturedAt: Date(timeIntervalSince1970: 1_700_000_000),
                chat: ExtractedChatIdentity(title: title, confidence: 0.9),
                messages: messages
            )
        )
    }
    return history
}

/// The query-compiler tests prove the *expression* is neutralised. These prove
/// the neutralised expression actually finds a message through the real index,
/// and -- just as important -- that a query containing an FTS5 operator does
/// not quietly become a different, looser search. FTS5 reads a bare `OR` as a
/// syntax error and `NEAR(a b)` as a proximity operator; if either reached
/// MATCH unescaped, the user would get an error or a wrong answer instead of a
/// literal result.
struct LocalMessageSearchLiteralQueryTests {
    /// One corpus containing every character shape the contract calls out, so
    /// a single fixture covers the whole table below.
    private static let corpus =
        "OR NEAR star* *lead quote\" pct% off_a_b dash- 50% off"
        + " emoji\u{1F600}\u{1F1E8}\u{1F1F3} mixed\u{4E2D}\u{6587}"
        + " \u{7A7A}\u{683C} two  spaces\nnewline tail"

    private func historyWithCorpus() async -> LocalMessageHistory {
        await makeSearchTestHistory(
            conversations: ["Group A": [makeSearchVisualMessage(Self.corpus)]]
        )
    }

    @Test
    func anOperatorShapedQueryStillFindsItsLiteralText() async throws {
        let history = await historyWithCorpus()
        for needle in [
            "OR NEAR",    // both operator words, adjacent
            "star*",       // trailing star
            "*lead",       // leading star
            "50% off",     // percent as a literal character
            "off_a_b",     // underscores are LIKE wildcards, not FTS ones
            "two  spaces", // a doubled space must not collapse
        ] {
            let snapshot = await history.searchLocalMessages(needle)
            #expect(
                !snapshot.results.isEmpty,
                "literal search for \(needle.debugDescription) found nothing"
            )
            // The excerpt is re-read from canonical evidence, so the match must
            // be the text itself -- not merely some row a loosened query
            // happened to hit.
            let excerpts = snapshot.results.map(\.excerpt).joined()
            #expect(excerpts.contains(needle))
        }
    }

    /// Emoji and mixed-script text. A regional-indicator flag is two scalars
    /// that Swift counts as one Character, so the flag case also proves the
    /// one-character literal fallback reaches a non-ASCII needle.
    @Test
    func emojiAndMixedScriptTextIsSearchable() async throws {
        let history = await historyWithCorpus()
        for needle in [
            "\u{1F600}", "\u{1F1E8}\u{1F1F3}",
            "\u{4E2D}\u{6587}", "mixed\u{4E2D}\u{6587}",
        ] {
            let snapshot = await history.searchLocalMessages(needle)
            #expect(!snapshot.results.isEmpty, "\(needle) found nothing")
        }
    }

    /// A newline is a real thing a user types with Shift-Enter or pastes in. It
    /// must be searched for, not dropped or turned into a query separator.
    @Test
    func aQueryContainingANewlineIsTreatedAsText() async throws {
        let history = await historyWithCorpus()
        let snapshot = await history.searchLocalMessages("spaces\nnewline")
        #expect(snapshot.results.count == 1)
    }

    /// Leading and trailing whitespace is trimmed before compiling, so a query
    /// typed with stray spaces still finds its text.
    @Test
    func surroundingWhitespaceIsTrimmed() async throws {
        let history = await historyWithCorpus()
        let snapshot = await history.searchLocalMessages("   OR NEAR   ")
        #expect(snapshot.results.count == 1)
    }

    /// A bare operator word must be a literal, not a syntax error and not a
    /// looser search. `OR` appears in the corpus exactly once, inside
    /// "OR NEAR", so one result proves neither a branch nor the whole corpus.
    @Test
    func aBareOperatorWordIsSearchedLiterally() async throws {
        let history = await historyWithCorpus()
        for needle in ["OR", "NEAR"] {
            let snapshot = await history.searchLocalMessages(needle)
            #expect(snapshot.results.count == 1, "\(needle) was not literal")
        }
    }

    /// Text that matches nothing is not an error. A query that would be a
    /// syntax error in raw FTS5 must come back as an ordinary empty result, so
    /// the user never sees a failure state for something they merely mistyped.
    @Test
    func aWellFormedQueryThatMatchesNothingIsEmptyNotFailed() async throws {
        let history = await historyWithCorpus()
        // Bare `*` is deliberately absent: one character routes to the
        // canonical fallback, which correctly finds the corpus's own `*`.
        for needle in ["NEAR/3", "\"unclosed", "(((", "a ^ b", "zzqxmarker"] {
            let snapshot = await history.searchLocalMessages(needle)
            #expect(snapshot.status == .noMatches, "\(needle) was not a plain miss")
        }
    }
}

// MARK: - Query persistence

/// The query exists in SwiftUI state and in the model for the current session
/// and nowhere else. The index is a rebuildable cache rather than a record, so
/// losing it must cost nothing.
struct LocalMessageSearchQueryPrivacyTests {
    /// Uses a real file rather than `makeTestMessageHistory()`: an in-memory
    /// store dies with the consent toggle, so "off then on" would prove only
    /// that an empty database is empty. Only an on-disk store can show the index
    /// being rebuilt from evidence that outlived it.
    @Test
    func theIndexIsRecreatedOnDemandAfterBeingDropped() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("WeChatCompanionTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let history = LocalMessageHistory(url: directory.appendingPathComponent("messages.sqlite"))
        await history.setEnabled(true)
        await history.ingestor()?.ingest(
            ExtractedConversationFrame(
                capturedAt: Date(),
                chat: ExtractedChatIdentity(title: "Group A", confidence: 0.9),
                messages: [makeSearchVisualMessage("zqxmarker in the body")]
            )
        )
        let before = await history.searchLocalMessages("zqxmarker")
        #expect(before.results.count == 1)
        // Consent off destroys the index with the store, exactly as a relaunch
        // would; turning it back on must rebuild from canonical evidence.
        await history.setEnabled(false)
        await history.setEnabled(true)
        let after = await history.searchLocalMessages("zqxmarker")
        #expect(after.results.count == 1)
    }

    /// A blank search box must not silently return the whole corpus, and must
    /// not build an index for a search that was never made.
    @Test
    func anEmptyQueryYieldsNoSearchAtAll() async throws {
        let history = await makeSearchTestHistory(
            conversations: ["Group A": [makeSearchVisualMessage("zqxmarker in the body")]]
        )
        for blank in ["", "   ", "\n\t"] {
            let snapshot = await history.searchLocalMessages(blank)
            #expect(snapshot.results.isEmpty)
            // Nothing was indexed, because nothing was searched for. Read the
            // aggregate off the snapshot rather than asking the index, whose
            // count accessor builds on demand and would answer 1 here.
            #expect(snapshot.indexedDocumentCount == 0)
        }
    }
}

// MARK: - UI wiring

/// The Search destination and the read model it renders. These are structural
/// assertions on the shipped types rather than on a rendered window: a
/// snapshot test would prove a drawing, not that the dangerous fields are
/// absent from the type the UI actually consumes.
struct LocalMessageSearchUIWiringTests {
    @Test @MainActor
    func archiveSearchHandsOffExactQueryWithoutExecutingAndIsConsumedOnce() throws {
        let app = AppModel(messageHistory: makeTestMessageHistory(), shareInbox: nil)
        let query = "  synthetic OR marker\n"
        app.beginArchiveSearch(query)
        #expect(app.selectedDestination == .search)
        #expect(app.localSearch.status == .idle)
        #expect(app.localSearch.results.isEmpty)
        let request = try #require(app.archiveSearchRequest)
        #expect(request.query == query)
        #expect(request.filter == .archive)
        #expect(app.consumeArchiveSearchRequest() == request)
        #expect(app.archiveSearchRequest == nil)
        #expect(app.consumeArchiveSearchRequest() == nil)
    }

    @Test @MainActor
    func archiveHandoffUsesCanonicalResultsAndExactRecordReveal() async throws {
        let history = await makeSearchTestHistory(conversations: [
            "Group A": [makeSearchVisualMessage("handoff-marker visual")]
        ])
        let transcript = try WeChatNativeTranscriptParser.parse(
            "·Synthetic sender\n2026年9月7日 20:35\nhandoff-marker archive\n\n",
            timeZone: TimeZone(identifier: "Asia/Shanghai")!
        )
        let insertion = try await history.persistArchiveEvidence(
            transcript: transcript,
            conversationKey: ArchiveConversationKey("native-anonymous-v1:handoff-fixture"),
            importedAt: Date(timeIntervalSince1970: 1_700_000_000)
        )
        guard case .inserted(let importID, _) = insertion else {
            Issue.record("expected a fresh synthetic import")
            return
        }
        let app = AppModel(messageHistory: history, shareInbox: nil)
        app.beginArchiveSearch("  handoff-marker  ")
        #expect(app.localSearch.status == .idle)
        let request = try #require(app.consumeArchiveSearchRequest())
        await app.searchLocalMessages(request.query, filter: request.filter)
        #expect(app.localSearch.status == .results(count: 1))
        let hit = try #require(app.localSearch.results.first)
        #expect(hit.source == .archiveAttributed)
        #expect(hit.provenance == .archiveAttributed)
        #expect(hit.linkState == "Unlinked export")
        #expect(hit.sender == "Synthetic sender")
        #expect(hit.timestamp != nil)
        #expect(hit.excerpt == "handoff-marker archive")
        #expect(hit.target == .archiveRecord(
            importID: importID, sequence: 0, provenance: .archiveAttributed
        ))
        await app.openSearchResult(hit)
        #expect(app.selectedDestination == .chats)
        #expect(app.selectedArchiveImportID == importID)
        #expect(app.selectedArchiveIsHitWindow)
        #expect(app.contextRevealRequest?.anchor == .archiveRecord(importID: importID, sequence: 0))
        #expect(app.selectedArchiveRecords.contains { $0.importID == importID && $0.sequence == 0 })
        #expect(!app.searchHitUnavailable)
        #expect(app.consumeArchiveSearchRequest() == nil)
    }

    @Test
    func searchIsItsOwnSidebarDestination() {
        #expect(Destination.allCases.contains(.search))
        #expect(Destination.search.rawValue == "Search")
        #expect(Destination.search.systemImage == "magnifyingglass")
        // It is not folded into the archive surface.
        #expect(Destination.chats.systemImage != Destination.search.systemImage)
    }

    /// The result row is the only thing the UI renders, so if the type has no
    /// field for a path or a hash there is nothing for the view to leak.
    @Test
    func theResultReadModelCarriesNoPathOrHash() {
        let result = LocalSearchResult(
            id: "visual:1",
            target: .visualMessage(conversationID: 1, messageID: 1),
            source: .visual,
            provenance: .visualCaptured,
            conversationLabel: "Group A",
            sender: nil,
            timestamp: nil,
            excerpt: "text",
            linkState: nil
        )
        let names = Mirror(reflecting: result).children.compactMap { $0.label }
        #expect(names.count == 9)
        for forbidden in ["path", "hash", "sha", "relative", "storage"] {
            #expect(
                !names.contains { $0.lowercased().contains(forbidden) },
                "the read model exposes \(forbidden)"
            )
        }
    }

    /// Provenance is shown in words. A user must be able to tell an attributed
    /// record from an unattributed one without the app inventing a sender or a
    /// timestamp it does not have.
    @Test
    func provenanceIsDescribedInWords() {
        #expect(LocalSearchResult.Provenance.visualCaptured.label == "Visual capture")
        #expect(
            LocalSearchResult.Provenance.archiveAttributed.label
                == "Attributed archive record"
        )
        #expect(
            LocalSearchResult.Provenance.archiveUnattributed.label
                == "Unattributed archive record"
        )
        // Both archive shapes share one source label; the distinction is
        // provenance, which is shown per result rather than filtered on.
        #expect(LocalSearchSource.archiveAttributed.label == "Archive")
        #expect(LocalSearchSource.archiveUnattributed.label == "Archive")
    }

    /// Navigation uses typed canonical identity, never the display-only id.
    @Test
    func resultsExposeExactCanonicalNavigationTarget() {
        let result = LocalSearchResult(
            id: "display-only",
            target: .visualMessage(conversationID: 3, messageID: 17),
            source: .visual,
            provenance: .visualCaptured,
            conversationLabel: "Group A",
            sender: nil,
            timestamp: nil,
            excerpt: "text",
            linkState: nil
        )
        let names = Mirror(reflecting: result).children.compactMap { $0.label }
        for forbidden in ["importID", "conversationID", "messageID", "sequence"] {
            #expect(!names.contains(forbidden))
        }
        #expect(result.target == .visualMessage(conversationID: 3, messageID: 17))
        #expect(result.id == "display-only")
    }

    @Test
    func theFilterOffersExactlyTheThreeSourceChoices() {
        #expect(LocalSearchFilter.allCases == [.all, .visual, .archive])
        #expect(LocalSearchFilter.all.sources.count == 3)
        #expect(LocalSearchFilter.visual.sources == [.visual])
        #expect(LocalSearchFilter.archive.sources.count == 2)
        #expect(LocalSearchFilter.all.label == "All")
    }

    /// The published status the view switches over. A `.results` case that did
    /// not carry its count, or a state that could report "no matches" for a
    /// search never made, would both be UI defects the switch depends on.
    @Test
    func statusCarriesTheResultCountForTheView() async throws {
        let history = await makeSearchTestHistory(
            conversations: ["Group A": [makeSearchVisualMessage("zqxmarker in the body")]]
        )
        let hit = await history.searchLocalMessages("zqxmarker")
        #expect(hit.status == .results(count: 1))
        let miss = await history.searchLocalMessages("zzqxmarker")
        #expect(miss.status == .noMatches)
        #expect(miss.results.isEmpty)
    }
}
