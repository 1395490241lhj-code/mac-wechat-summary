import Foundation
import SQLite3
import Testing
@testable import WeChatCompanion

// MARK: - Synthetic fixtures

// Every string below is invented for these tests. No real chat content is
// used, read, or logged anywhere in this file.

/// Shape A needs a real WeChat timestamp line; a bare HH:mm is not a date and
/// the strict parser correctly refuses the whole file as "not a transcript".
private func attributed(_ rows: [(String, String, String)]) throws -> WeChatNativeTranscript {
    let body = rows
        .map { "·\($0.0)\n2026年9月7日 \($0.1)\n\($0.2)\n\n" }
        .joined()
    return try WeChatNativeTranscriptParser.parse(
        body, timeZone: TimeZone(identifier: "Asia/Shanghai")!
    )
}

private func unattributed(_ records: [String]) throws -> WeChatNativeTranscript {
    try WeChatNativeTranscriptParser.parse(
        records.map { "·\($0)" }.joined(separator: "\n\n") + "\n"
    )
}

private func visualMessage(
    _ text: String,
    sender: String? = nil
) -> ExtractedVisibleMessage {
    ExtractedVisibleMessage(
        sender: sender,
        ownership: .other,
        visibleTime: nil,
        text: text,
        kind: .text,
        confidence: 0.9
    )
}

/// A store with consent on and the given conversations already persisted.
private func makeHistory(
    conversations: [String: [ExtractedVisibleMessage]] = [:]
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

// MARK: - Tokenizer and runtime gate

/// B6 gate: the app's own SQLite runtime must offer FTS5 *and* the trigram
/// tokenizer, and trigram must actually match Chinese, English and mixed
/// substrings. A command-line sqlite3 version proves nothing about the runtime
/// that ships inside the app, so this runs against the same SQLite3 module the
/// production index uses.
struct LocalMessageSearchTokenizerTests {
    @Test
    func theAppRuntimeOffersFTS5AndTrigram() throws {
        let runtime = try #require(LocalMessageSearchRuntime.probe())
        #expect(!runtime.sqliteVersion.isEmpty)
        #expect(runtime.fts5Available)
        #expect(runtime.trigramAvailable)
        #expect(runtime.isSearchCapable)
    }

    @Test
    func trigramMatchesChineseEnglishAndMixedSubstrings() throws {
        let runtime = try #require(LocalMessageSearchRuntime.probe())
        #expect(runtime.matches("今天天气很好 hello world", "今天天气"))
        #expect(runtime.matches("今天天气很好 hello world", "hello"))
        #expect(runtime.matches("今天天气很好 hello world", "ell"))
        // One quoted phrase is a contiguous run of trigrams, so a query that
        // spans a script boundary still matches: trigrams do not stop at the
        // space between 天气很 and hello.
        #expect(runtime.matches("今天天气很好 hello world", "很好 hell"))
        #expect(runtime.matches("今天天气很好 hello world", "今天天气"))
        #expect(!runtime.matches("今天天气很好 hello world", "xyz"))
    }
}

// MARK: - Query compilation

/// User input is text, not FTS5 grammar. Every operator character must survive
/// as a character to search for rather than changing what the query means.
struct LocalSearchQueryCompilerTests {
    @Test
    func shortQueriesGoLiteralAndEmptyOrOverlongQueriesAreRejected() {
        #expect(LocalSearchQueryCompiler.compile("   ") == .rejected)
        #expect(LocalSearchQueryCompiler.compile("\n\t ") == .rejected)
        #expect(LocalSearchQueryCompiler.compile("") == .rejected)
        #expect(LocalSearchQueryCompiler.compile("a") == .literalFallback("a"))
        #expect(LocalSearchQueryCompiler.compile("  ab  ") == .literalFallback("ab"))
        #expect(LocalSearchQueryCompiler.compile("abc") == .fts("\"abc\""))
        let tooLong = String(repeating: "a", count: LocalSearchQueryCompiler.maximumLength + 1)
        #expect(LocalSearchQueryCompiler.compile(tooLong) == .rejected)
        let atLimit = String(repeating: "a", count: LocalSearchQueryCompiler.maximumLength)
        #expect(
            LocalSearchQueryCompiler.compile(atLimit)
                == .fts("\"" + String(repeating: "a", count: LocalSearchQueryCompiler.maximumLength) + "\"")
        )
    }

    @Test
    func operatorCharactersAreQuotedNotInterpreted() {
        // Each of these is an FTS5 operator when handed to MATCH raw. Wrapped in
        // one quoted phrase they are the literal characters the user typed.
        for literal in ["OR", "NEAR", "*", "\"", "%", "_", "-", "(", ")", "^", ":"] {
            let escaped = literal.replacingOccurrences(of: "\"", with: "\"\"")
            #expect(
                LocalSearchQueryCompiler.quotedPhrase(literal) == "\"" + escaped + "\"",
                "\(literal) was not neutralised"
            )
        }
        #expect(LocalSearchQueryCompiler.compile("a OR b") == .fts("\"a OR b\""))
        #expect(LocalSearchQueryCompiler.compile("NEAR(a b)") == .fts("\"NEAR(a b)\""))
        #expect(LocalSearchQueryCompiler.compile("100%*") == .fts("\"100%*\""))
    }

    /// A query that is only a quote would, unescaped, open a string that never
    /// closes. Doubling it makes it search for a quote character.
    @Test
    func aQuoteOnlyQueryStaysWellFormed() throws {
        let runtime = try #require(LocalMessageSearchRuntime.probe())
        #expect(runtime.matches("he said \"hi\" loudly", "\"hi\""))
    }
}
// MARK: - Sources

struct LocalMessageSearchSourceTests {
    @Test
    func visualMessageBodyMatches() async throws {
        let history = await makeHistory(
            conversations: ["Group A": [visualMessage("contains zqxmarker inside")]]
        )
        let snapshot = await history.searchLocalMessages("zqxmarker")
        #expect(snapshot.results.count == 1)
        let result = try #require(snapshot.results.first)
        #expect(result.source == .visual)
        #expect(result.provenance == .visualCaptured)
        #expect(result.conversationLabel == "Group A")
        #expect(result.timestamp != nil)
        let conversation = try #require(await history.captureLedger().conversations.first)
        #expect(result.target == .visualConversation(conversation.id))
    }

    @Test
    func visualSenderMatches() async throws {
        let history = await makeHistory(
            conversations: ["Group A": [visualMessage("body text", sender: "zqxmarker")]]
        )
        let snapshot = await history.searchLocalMessages("zqxmarker")
        #expect(snapshot.results.count == 1)
        #expect(snapshot.results.first?.sender == "zqxmarker")
    }

    @Test
    func archiveAttributedSenderAndBodyBothMatch() async throws {
        let history = await makeHistory()
        let transcript = try attributed([
            ("zqxmarker", "20:35", "the body mentions abcdef"),
            ("someone", "20:36", "zqxmarker in the second body"),
        ])
        _ = try await history.persistArchiveEvidence(
            transcript: transcript,
            conversationKey: ArchiveConversationKey("chat-a"),
            importedAt: Date()
        )

        let byBody = await history.searchLocalMessages("abcdef")
        #expect(byBody.results.count == 1)
        #expect(byBody.results.first?.provenance == .archiveAttributed)
        #expect(byBody.results.first?.sender == "zqxmarker")
        let imported = try #require(await history.archiveEvidenceSnapshot().imports.first)
        #expect(byBody.results.first?.target == .archiveImport(imported.id))

        let bySender = await history.searchLocalMessages("someone")
        #expect(bySender.results.count == 1)
        #expect(bySender.results.first?.excerpt.contains("second body") == true)
    }

    @Test
    func archiveUnattributedMatchesWithoutInventingASender() async throws {
        let history = await makeHistory()
        _ = try await history.persistArchiveEvidence(
            transcript: try unattributed(["plain record zqxmarker here"]),
            conversationKey: ArchiveConversationKey("chat-b"),
            importedAt: Date()
        )
        let snapshot = await history.searchLocalMessages("zqxmarker")
        let result = try #require(snapshot.results.first)
        #expect(result.source == .archiveUnattributed)
        #expect(result.provenance == .archiveUnattributed)
        // An archive that carried no attribution must not gain a fabricated one.
        #expect(result.sender == nil)
        #expect(result.timestamp == nil)
    }

    /// Attachment metadata is not text evidence. Searching for a stored
    /// relative path, a content hash, or a media extension must find nothing.
    @Test
    func attachmentMetadataNeverMatches() async throws {
        let history = await makeHistory()
        _ = try await history.persistArchiveEvidence(
            transcript: try attributed([("sender", "20:35", "body with no filename in it")]),
            conversationKey: ArchiveConversationKey("chat-attach"),
            importedAt: Date()
        )
        // Whatever the manifest's internals are, none of them are searchable.
        for needle in ["jpg", "archive-attachments", "content_sha256", "stored_relative_path"] {
            let snapshot = await history.searchLocalMessages(needle)
            #expect(snapshot.results.isEmpty, "\(needle) matched")
        }
    }

    /// Memory, Daily Summary, Reminders and AI output are separate stores with
    /// their own text. Search must reach none of them, even for a word that
    /// exists only there.
    @Test
    func memoryDerivedContentNeverMatches() async throws {
        let history = await makeHistory(
            conversations: ["Group A": [visualMessage("ordinary body text")]]
        )
        _ = try await history.persistArchiveEvidence(
            transcript: try attributed([("sender", "20:35", "ordinary archive text")]),
            conversationKey: ArchiveConversationKey("chat-mem"),
            importedAt: Date()
        )
        for needle in ["Daily Summary", "reminder", "Memory", "ai response"] {
            let snapshot = await history.searchLocalMessages(needle)
            #expect(snapshot.results.isEmpty, "\(needle) matched")
        }
    }
}

// MARK: - Provenance and labels

struct LocalMessageSearchProvenanceTests {
    @Test
    func displayNameIsAResultLabelOnlyAndRenamingKeepsIdentity() async throws {
        let history = await makeHistory()
        let result = try await history.persistArchiveEvidence(
            transcript: try attributed([("sender", "20:35", "zqxmarker body")]),
            // A real anonymous export uses the opaque native-anonymous-v1 key.
            // Link state is only meaningful -- and only shown -- for those.
            conversationKey: ArchiveConversationKey("native-anonymous-v1:fixture-c"),
            importedAt: Date()
        )
        guard case .inserted(let importID, _) = result else {
            Issue.record("expected a fresh import")
            return
        }

        let before = await history.searchLocalMessages("zqxmarker")
        #expect(before.results.first?.conversationLabel == "Imported WeChat Archive")
        #expect(before.results.first?.linkState == "Unlinked export")

        try await history.setArchiveImportDisplayName(importID: importID, displayName: "Renamed Trip")
        let after = await history.searchLocalMessages("zqxmarker")
        #expect(after.results.count == 1)
        #expect(after.results.first?.conversationLabel == "Renamed Trip")
        // Renaming is a label change, not an identity or link change.
        #expect(after.results.first?.id == before.results.first?.id)
        #expect(after.results.first?.linkState == "Unlinked export")

        // The display name itself is not indexed text.
        let byLabel = await history.searchLocalMessages("Renamed")
        #expect(byLabel.results.isEmpty)
    }

    @Test
    func linkingAnArchiveDoesNotMergeItsRowsWithVisual() async throws {
        let history = await makeHistory(
            conversations: ["Shared Title": [visualMessage("zqxmarker visual")]]
        )
        let imported = try await history.persistArchiveEvidence(
            transcript: try attributed([("sender", "20:35", "zqxmarker archived")]),
            conversationKey: ArchiveConversationKey("native-anonymous-v1:fixture-d"),
            importedAt: Date()
        )
        let store = try #require(await history.openStore())
        let conversation = try #require(
            try await store.conversation(titled: "Shared Title")
        )
        guard case .inserted(let importID, _) = imported else {
            Issue.record("expected a fresh import")
            return
        }
        try await history.linkArchiveImport(
            importID: importID, toVisualConversationID: conversation.id
        )

        let snapshot = await history.searchLocalMessages("zqxmarker")
        // Two distinct pieces of evidence stay two distinct rows.
        #expect(snapshot.results.count == 2)
        #expect(Set(snapshot.results.map(\.source)) == [.visual, .archiveAttributed])
        #expect(Set(snapshot.results.map(\.id)).count == 2)
        let linked = try #require(snapshot.results.first { $0.source == .archiveAttributed })
        #expect(linked.linkState == "Explicitly linked")
    }

    @Test
    func identicalTextInBothSourcesYieldsTwoProvenanceDistinctResults() async throws {
        let history = await makeHistory(
            conversations: ["Group V": [visualMessage("zqxmarker shared text")]]
        )
        _ = try await history.persistArchiveEvidence(
            transcript: try attributed([("sender", "20:35", "zqxmarker shared text")]),
            conversationKey: ArchiveConversationKey("chat-e"),
            importedAt: Date()
        )
        let snapshot = await history.searchLocalMessages("zqxmarker")
        #expect(snapshot.results.count == 2)
        #expect(Set(snapshot.results.map(\.provenance)) == [.visualCaptured, .archiveAttributed])
    }
}
// MARK: - Canonical truth

struct LocalMessageSearchCanonicalTests {
    /// The index is a candidate finder. A row deleted after indexing must not
    /// reach the UI, even though the in-memory copy still holds its text.
    @Test
    func aHitWhoseCanonicalRowVanishedIsDropped() async throws {
        let history = await makeHistory(
            conversations: ["Group A": [visualMessage("zqxmarker doomed")]]
        )
        let store = try #require(await history.openStore())
        let index = LocalMessageSearchIndex(store: store)
        #expect(try await index.documentCount() == 1)
        // Deleting every canonical row removes the message; the index still
        // holds its text until invalidated, which is exactly the stale-hit case.
        try await store.deleteAllHistory()
        let snapshot = try await index.search("zqxmarker")
        #expect(snapshot.results.isEmpty)
        #expect(snapshot.status == .noMatches)
    }

    /// The read model carries human labels only. There is no field for a stored
    /// path, a content hash, a conversation key, or a raw import id, and the
    /// strings that exist are not accidentally one of those.
    @Test
    func resultsCarryNoInternalIdentifierPathOrHash() async throws {
        let history = await makeHistory(
            conversations: ["Group A": [visualMessage("zqxmarker clean")]]
        )
        _ = try await history.persistArchiveEvidence(
            transcript: try attributed([("sender", "20:35", "zqxmarker clean archive")]),
            conversationKey: ArchiveConversationKey("chat-f"),
            importedAt: Date()
        )
        let snapshot = await history.searchLocalMessages("zqxmarker")
        #expect(!snapshot.results.isEmpty)
        for result in snapshot.results {
            for value in [result.conversationLabel, result.excerpt, result.linkState ?? ""] {
                #expect(!value.contains("/"))
                #expect(!value.contains("\\"))
                #expect(!value.contains(".sqlite"))
            }
        }
    }

    @Test
    func excerptsAreBounded() async throws {
        let long = String(repeating: "zqxmarker ", count: 400)
        let history = await makeHistory(conversations: ["Group A": [visualMessage(long)]])
        let snapshot = await history.searchLocalMessages("zqxmarker")
        let excerpt = try #require(snapshot.results.first?.excerpt)
        #expect(excerpt.count <= 260)
    }

    @Test
    func resultCountIsCappedAtOneHundred() async throws {
        let history = await makeHistory()
        // 150 rows, so the corpus genuinely exceeds the cap.
        await history.ingestor()?.ingest(
            ExtractedConversationFrame(
                capturedAt: Date(timeIntervalSince1970: 1_700_000_000),
                chat: ExtractedChatIdentity(title: "Big Group", confidence: 0.9),
                messages: (1...150).map { visualMessage("zqxmarker row \($0)") }
            )
        )
        let snapshot = await history.searchLocalMessages("zqxmarker")
        #expect(snapshot.results.count == LocalMessageSearchIndex.maximumResults)
        #expect(LocalMessageSearchIndex.maximumResults == 100)
    }

    /// The corpus is read in bounded chunks, and a build that is abandoned
    /// cannot publish a partial index.
    @Test
    func aLargeCorpusIndexesInChunksAndCancellationPublishesNothing() async throws {
        let history = await makeHistory()
        await history.ingestor()?.ingest(
            ExtractedConversationFrame(
                capturedAt: Date(timeIntervalSince1970: 1_700_000_000),
                chat: ExtractedChatIdentity(title: "Huge Group", confidence: 0.9),
                messages: (1...1_200).map { visualMessage("chunkrow \($0) needle") }
            )
        )
        let store = try #require(await history.openStore())
        let index = LocalMessageSearchIndex(store: store)
        #expect(try await index.documentCount() == 1_200)
        #expect(LocalMessageSearchIndex.chunkSize == 500)

        // A cancelled query must not leave a half-published index behind.
        let cancelling = Task { try await index.search("needle") }
        cancelling.cancel()
        _ = try? await cancelling.value
        // Whatever the race, the index is rebuilt whole: the count is still
        // the complete corpus and a later query still returns a capped set.
        #expect(try await index.documentCount() == 1_200)
        #expect(try await index.search("needle").results.count == 100)
    }

    @Test
    func searchingNeverWritesToTheCanonicalStore() async throws {
        let history = await makeHistory(
            conversations: ["Group A": [visualMessage("zqxmarker read only")]]
        )
        let store = try #require(await history.openStore())
        let before = try await store.searchableVisualMessages(
            afterMessageID: 0, limit: 2_000
        ).count
        _ = await history.searchLocalMessages("zqxmarker")
        _ = await history.searchLocalMessages("ro")
        let after = try await store.searchableVisualMessages(
            afterMessageID: 0, limit: 2_000
        ).count
        #expect(after == before)
    }
}

// MARK: - Invalidation

struct LocalMessageSearchInvalidationTests {
    @Test
    func newVisualPersistenceIsPickedUpByTheNextSearch() async throws {
        let history = await makeHistory()
        #expect(await history.searchLocalMessages("zqxmarker").results.isEmpty)

        await history.ingestor()?.ingest(
            ExtractedConversationFrame(
                capturedAt: Date(timeIntervalSince1970: 1_700_000_100),
                chat: ExtractedChatIdentity(title: "Group A", confidence: 0.9),
                messages: [visualMessage("zqxmarker arrives later")]
            )
        )
        #expect(await history.searchLocalMessages("zqxmarker").results.count == 1)
    }

    @Test
    func newArchiveImportIsPickedUpByTheNextSearch() async throws {
        let history = await makeHistory()
        #expect(await history.searchLocalMessages("zqxmarker").results.isEmpty)
        _ = try await history.persistArchiveEvidence(
            transcript: try attributed([("sender", "20:35", "zqxmarker imported")]),
            conversationKey: ArchiveConversationKey("chat-g"),
            importedAt: Date()
        )
        #expect(await history.searchLocalMessages("zqxmarker").results.count == 1)
    }

    @Test
    func aDisplayNameChangeDoesNotThrowTheIndexAway() async throws {
        let history = await makeHistory()
        let result = try await history.persistArchiveEvidence(
            transcript: try attributed([("sender", "20:35", "zqxmarker body")]),
            conversationKey: ArchiveConversationKey("chat-h"),
            importedAt: Date()
        )
        guard case .inserted(let importID, _) = result else {
            Issue.record("expected a fresh import")
            return
        }
        #expect(await history.localSearchDocumentCount() == 1)
        try await history.setArchiveImportDisplayName(importID: importID, displayName: "Trip")
        // A cheap read of the live index: unchanged means the rename did not
        // invalidate, because a display name is not indexed text.
        #expect(await history.localSearchDocumentCount() == 1)
    }

    @Test
    func aRetentionSweepInvalidatesTheIndex() async throws {
        let history = LocalMessageHistory(url: nil, retention: .sevenDays)
        await history.setEnabled(true)
        // A frame old enough for the seven-day policy to remove it.
        await history.ingestor()?.ingest(
            ExtractedConversationFrame(
                capturedAt: Date(timeIntervalSince1970: 1_600_000_000),
                chat: ExtractedChatIdentity(title: "Old Group", confidence: 0.9),
                messages: [visualMessage("zqxmarker expired")]
            )
        )
        #expect(await history.searchLocalMessages("zqxmarker").results.count == 1)
        await history.ingestor()?.sweepNow()
        // The canonical row is gone, so a rebuilt index cannot contain it.
        #expect(await history.searchLocalMessages("zqxmarker").results.isEmpty)
    }

    @Test
    func deleteAllHistoryDestroysTheIndex() async throws {
        let history = await makeHistory(
            conversations: ["Group A": [visualMessage("zqxmarker doomed")]]
        )
        #expect(await history.searchLocalMessages("zqxmarker").results.count == 1)
        await history.deleteAllHistory()
        let after = await history.searchLocalMessages("zqxmarker")
        #expect(after.results.isEmpty)
        // Delete All History removes the index outright. Consent is still on,
        // so the store reopens empty and the next query rebuilds from that empty
        // canonical state -- no stale hit survives from text the in-memory index
        // was still holding.
        #expect(after.status == .noMatches)
        #expect(await history.localSearchDocumentCount() == 0)
    }
}

// MARK: - Consent, short queries, filters

struct LocalMessageSearchConsentTests {
    @Test
    func searchIsUnavailableWhileStorageIsOff() async {
        let history = makeTestMessageHistory()
        let snapshot = await history.searchLocalMessages("zqxmarker")
        #expect(snapshot.status == .storageDisabled)
        #expect(snapshot.results.isEmpty)
    }

    @Test
    func withdrawingConsentDestroysTheIndex() async throws {
        let history = await makeHistory(
            conversations: ["Group A": [visualMessage("zqxmarker held")]]
        )
        #expect(await history.localSearchDocumentCount() == 1)
        await history.setEnabled(false)
        let after = await history.searchLocalMessages("zqxmarker")
        #expect(after.status == .storageDisabled)
        #expect(await history.localSearchDocumentCount() == 0)
    }

    /// Trigram cannot match anything shorter than three characters. Without the
    /// bounded canonical fallback a search for one CJK character or one letter
    /// would always return nothing.
    @Test
    func oneAndTwoCharacterQueriesStillMatch() async throws {
        let history = await makeHistory(
            conversations: ["Group A": [visualMessage("car 車 collecting 車輛")]]
        )
        _ = try await history.persistArchiveEvidence(
            transcript: try attributed([("car", "20:35", "車 in the archive")]),
            conversationKey: ArchiveConversationKey("chat-i"),
            importedAt: Date()
        )
        for needle in ["車", "ca", "car"] {
            let snapshot = await history.searchLocalMessages(needle)
            #expect(!snapshot.results.isEmpty, "\(needle) found nothing")
        }
        #expect(await history.searchLocalMessages("L").results.isEmpty)
    }

    @Test
    func theSourceFilterNarrowsResults() async throws {
        let history = await makeHistory(
            conversations: ["Group A": [visualMessage("zqxmarker visual")]]
        )
        _ = try await history.persistArchiveEvidence(
            transcript: try attributed([("sender", "20:35", "zqxmarker archive")]),
            conversationKey: ArchiveConversationKey("chat-j"),
            importedAt: Date()
        )
        let all = await history.searchLocalMessages("zqxmarker", filter: .all)
        let visual = await history.searchLocalMessages("zqxmarker", filter: .visual)
        let archive = await history.searchLocalMessages("zqxmarker", filter: .archive)
        #expect(all.results.count == 2)
        #expect(visual.results.count == 1)
        #expect(visual.results.first?.source == .visual)
        #expect(archive.results.count == 1)
        #expect(archive.results.first?.source == .archiveAttributed)
    }

    @Test @MainActor
    func searchResultsOpenTheirOwnCanonicalContexts() async throws {
        let visualBody = String(repeating: "prefix ", count: 80) + "zqxmarker visual"
        let history = await makeHistory(
            conversations: ["Group A": [visualMessage(visualBody)]]
        )
        _ = try await history.persistArchiveEvidence(
            transcript: try attributed([("sender", "20:35", "zqxmarker archive")]),
            conversationKey: ArchiveConversationKey("chat-navigation"),
            importedAt: Date()
        )
        let app = AppModel(messageHistory: history, shareInbox: nil)
        let visual = try #require(await history.searchLocalMessages("zqxmarker", filter: .visual).results.first)
        await app.openSearchResult(visual)
        #expect(app.selectedDestination == .chats)
        #expect(app.contextNavigationTarget == visual.target)
        #expect(app.selectedVisualMessages.map(\.text) == [visualBody])
        #expect(visual.excerpt != visualBody)

        let archive = try #require(await history.searchLocalMessages("zqxmarker", filter: .archive).results.first)
        await app.openSearchResult(archive)
        #expect(app.selectedDestination == .chats)
        #expect(app.contextNavigationTarget == archive.target)
        #expect(app.archiveEvidence.imports.contains(where: { $0.id == app.selectedArchiveImportID }))
        #expect(app.selectedArchiveRecords.map(\.text) == ["zqxmarker archive"])
    }

    @Test @MainActor
    func visualAndArchiveSelectionsAreMutuallyExclusive() async throws {
        let history = await makeHistory(
            conversations: ["Group A": [visualMessage("visual context")]]
        )
        _ = try await history.persistArchiveEvidence(
            transcript: try attributed([("sender", "20:35", "archive context")]),
            conversationKey: ArchiveConversationKey("chat-selection"),
            importedAt: Date()
        )
        let app = AppModel(messageHistory: history, shareInbox: nil)
        await app.refreshCaptureLedger()
        await app.refreshArchiveEvidence()
        let visualID = try #require(app.captureLedger.conversations.first?.id)
        let importID = try #require(app.archiveEvidence.imports.first?.id)

        await app.selectVisualConversation(visualID)
        #expect(app.selectedVisualConversationID == visualID)
        #expect(app.contextNavigationTarget == .visualConversation(visualID))
        #expect(app.selectedArchiveImportID == nil)

        await app.selectArchiveImport(importID)
        #expect(app.selectedArchiveImportID == importID)
        #expect(app.contextNavigationTarget == .archiveImport(importID))
        #expect(app.selectedVisualConversationID == nil)
        #expect(app.archiveEvidence.imports.first?.link == nil)
    }

    @Test @MainActor
    func directVisualBrowseReadsNewestHundredCanonicalRowsInOrder() async throws {
        let history = await makeHistory(
            conversations: ["Group A": [visualMessage("message 000")]]
        )
        let store = try #require(await history.openStore())
        let conversation = try #require(await store.conversation(titled: "Group A"))
        try await store.append(
            (1...105).map { visualMessage(String(format: "message %03d", $0)) },
            conversationID: conversation.id,
            observedAt: Date(timeIntervalSince1970: 1_700_000_001)
        )
        let app = AppModel(messageHistory: history, shareInbox: nil)
        await app.selectVisualConversation(conversation.id)
        #expect(app.selectedVisualMessages.count == 100)
        #expect(app.selectedVisualMessages.first?.text == "message 006")
        #expect(app.selectedVisualMessages.last?.text == "message 105")
        #expect(app.selectedVisualMessages.map(\.sequence)
            == app.selectedVisualMessages.map(\.sequence).sorted())
    }

    @Test @MainActor
    func vanishedVisualConversationDoesNotOpenStaleContext() async throws {
        let history = await makeHistory(
            conversations: ["Group A": [visualMessage("vanishing context")]]
        )
        let app = AppModel(messageHistory: history, shareInbox: nil)
        await app.refreshCaptureLedger()
        let id = try #require(app.captureLedger.conversations.first?.id)
        let hit = try #require(await history.searchLocalMessages("vanishing context").results.first)
        await history.deleteAllHistory()
        await app.selectVisualConversation(id)
        #expect(app.selectedVisualConversationID == nil)
        #expect(app.selectedVisualMessages.isEmpty)
        #expect(app.visualContextUnavailable)
        await app.openSearchResult(hit)
        #expect(app.selectedDestination == .chats)
        #expect(app.selectedVisualConversationID == nil)
        #expect(app.visualContextUnavailable)
    }
}
