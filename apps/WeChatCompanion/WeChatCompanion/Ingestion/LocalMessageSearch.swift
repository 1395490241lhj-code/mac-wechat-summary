import Foundation
import SQLite3

// MARK: - Runtime gate

/// What the app's own SQLite runtime can actually do.
///
/// The command-line sqlite3 version proves nothing about the library linked
/// into the app bundle, and B6 depends on two specific capabilities existing
/// there: FTS5, and the trigram tokenizer. Chat evidence here is mostly
/// Chinese, which unicode61 would segment per character and stop matching
/// multi-character runs. This probe is a runnable assertion rather than a
/// comment so a future runtime change that drops either capability fails a
/// test instead of silently degrading search.
struct LocalMessageSearchRuntime: Equatable, Sendable {
    let sqliteVersion: String
    let fts5Available: Bool
    let trigramAvailable: Bool

    /// True when the runtime offers everything B6 requires.
    var isSearchCapable: Bool { fts5Available && trigramAvailable }

    /// Runs the real capability probe. Returns nil only when :memory: itself
    /// cannot be opened, which would mean the whole store is already broken.
    static func probe() -> LocalMessageSearchRuntime? {
        var handle: OpaquePointer?
        guard sqlite3_open_v2(
            ":memory:",
            &handle,
            SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX,
            nil
        ) == SQLITE_OK, let handle else {
            if let handle { sqlite3_close_v2(handle) }
            return nil
        }
        defer { sqlite3_close_v2(handle) }

        let version = String(cString: sqlite3_libversion())
        // FTS5 presence is decided by a real CREATE, not by a version string:
        // the module is a compile-time option of the library, so a version
        // claim can be right while the feature is absent. The default
        // tokenizer is used here so this answers FTS5 alone.
        let fts5 = (try? exec(handle, "CREATE VIRTUAL TABLE probe_fts USING fts5(body);")) != nil
        return LocalMessageSearchRuntime(
            sqliteVersion: version,
            fts5Available: fts5,
            trigramAvailable: fts5 && probeTrigram(handle)
        )
    }

    /// Whether needle occurs literally inside haystack under this runtime's
    /// FTS5 + trigram semantics. Test-only evidence for the tokenizer gate.
    func matches(_ haystack: String, _ needle: String) -> Bool {
        var db: OpaquePointer?
        guard sqlite3_open_v2(
            ":memory:", &db,
            SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX, nil
        ) == SQLITE_OK, let db else { return false }
        defer { sqlite3_close_v2(db) }
        try? Self.exec(db, "CREATE VIRTUAL TABLE t USING fts5(body, tokenize='trigram');")
        try? Self.exec(
            db,
            "INSERT INTO t(rowid, body) VALUES (1, \(sqlLiteral(haystack)));"
        )
        // A quoted phrase, exactly what the production compiler emits.
        let match = LocalSearchQueryCompiler.quotedPhrase(needle)
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(
            db, "SELECT 1 FROM t WHERE t MATCH \(sqlLiteral(match)) LIMIT 1;",
            -1, &statement, nil
        ) == SQLITE_OK, let statement else { return false }
        defer { sqlite3_finalize(statement) }
        return sqlite3_step(statement) == SQLITE_ROW
    }

    private static func exec(_ handle: OpaquePointer, _ sql: String) throws {
        var error: UnsafeMutablePointer<CChar>?
        guard sqlite3_exec(handle, sql, nil, nil, &error) == SQLITE_OK else {
            let message = error.map { String(cString: $0) } ?? "sqlite error"
            sqlite3_free(error)
            throw SearchIndexError.sqlite(status: -1, message: message)
        }
    }

    /// FTS5 is a compile-time module option and trigram is a tokenizer that
    /// module can be built without, so they are two facts about the same build
    /// but not the same fact. Probed separately: a runtime with FTS5 and no
    /// trigram must fail B6 loudly rather than pass a check that only proves
    /// the first half.
    private static func probeTrigram(_ handle: OpaquePointer) -> Bool {
        var handle2: OpaquePointer?
        guard sqlite3_open_v2(
            ":memory:", &handle2,
            SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX, nil
        ) == SQLITE_OK, let handle2 else {
            if let handle2 { sqlite3_close_v2(handle2) }
            return false
        }
        defer { sqlite3_close_v2(handle2) }
        guard (try? exec(
            handle2,
            "CREATE VIRTUAL TABLE t USING fts5(body, tokenize='trigram');"
        )) != nil else { return false }
        guard (try? exec(handle2, "INSERT INTO t(rowid, body) VALUES (1, 'abcdef');")) != nil
        else { return false }
        // A MATCH on a trigram table can only succeed if the tokenizer really
        // produced tokens, not if the CREATE merely named one.
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(
            handle2, "SELECT 1 FROM t WHERE t MATCH '\"bcd\"' LIMIT 1;",
            -1, &statement, nil
        ) == SQLITE_OK, let statement else { return false }
        defer { sqlite3_finalize(statement) }
        return sqlite3_step(statement) == SQLITE_ROW
    }
}

// MARK: - Read models

enum LocalSearchSource: String, Sendable, Equatable, CaseIterable {
    case visual
    case archiveAttributed = "archive_attributed"
    case archiveUnattributed = "archive_unattributed"

    /// What the user sees, never the internal token.
    var label: String {
        switch self {
        case .visual: "Visual"
        case .archiveAttributed, .archiveUnattributed: "Archive"
        }
    }

    var isArchive: Bool { self != .visual }
}

/// One search hit, already re-validated against the canonical store.
///
/// Everything here is display-safe by construction: there is no field for a
/// stored path, a content hash, a conversation key, or an import id. Provenance
/// is described in words, not in storage identifiers.
struct LocalSearchResult: Identifiable, Sendable, Equatable {
    let id: String
    let source: LocalSearchSource
    let provenance: Provenance
    /// The latest canonical presentation label, read at result time.
    let conversationLabel: String
    let sender: String?
    /// Canonical only: never a parsed display string.
    let timestamp: Date?
    let excerpt: String
    /// "Unlinked export" / "Explicitly linked" for anonymous archive imports.
    let linkState: String?

    enum Provenance: Sendable, Equatable {
        case visualCaptured
        case archiveAttributed
        case archiveUnattributed

        var label: String {
            switch self {
            case .visualCaptured: "Visual capture"
            case .archiveAttributed: "Attributed archive record"
            case .archiveUnattributed: "Unattributed archive record"
            }
        }
    }

}

/// The user-facing filter. Deliberately coarse: attributed vs unattributed is
/// shown per result rather than becoming a control, because the distinction is
/// about evidence quality, not about what someone usually searches for.
enum LocalSearchFilter: String, Sendable, Equatable, CaseIterable {
    case all
    case visual
    case archive

    var label: String {
        switch self {
        case .all: "All"
        case .visual: "Visual"
        case .archive: "Archive"
        }
    }

    var sources: Set<LocalSearchSource> {
        switch self {
        case .all: Set(LocalSearchSource.allCases)
        case .visual: [.visual]
        case .archive: [.archiveAttributed, .archiveUnattributed]
        }
    }
}

enum LocalSearchStatus: Sendable, Equatable {
    case storageDisabled
    case storeUnavailable
    case idle
    case preparing
    case results(count: Int)
    case noMatches
    case failed
}

struct LocalSearchSnapshot: Sendable, Equatable {
    var status: LocalSearchStatus = .idle
    var results: [LocalSearchResult] = []
    /// Aggregate only. No document text, sender or conversation title.
    var indexedDocumentCount: Int = 0
}

// MARK: - Query compilation

/// Turns what the user typed into a safe FTS5 match expression.
///
/// **User input is never FTS grammar.** FTS5 reads OR, NEAR, star, quote, caret,
/// dash, colon and parentheses as operators, so a naive MATCH on raw text lets
/// someone searching for the word OR silently change what the query means -- or
/// make it a syntax error. Everything is wrapped in one quoted phrase with
/// internal quotes doubled, which makes the whole string a single literal
/// phrase and neutralizes every operator character.
enum LocalSearchQueryCompiler {
    /// Fixed contract. A search box is not a place to paste a novel, and an
    /// unbounded query is an unbounded FTS5 expression.
    static let maximumLength = 128

    /// Trigram cannot match anything shorter than three characters, so those
    /// lengths take the bounded canonical instr() path instead. Without this a
    /// search for a single CJK character or letter would always return nothing.
    static let shortestFTSQuery = 3

    enum Compiled: Equatable, Sendable {
        case fts(String)
        case literalFallback(String)
        case rejected
    }

    static func compile(_ raw: String) -> Compiled {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return .rejected }
        guard trimmed.count <= maximumLength else { return .rejected }
        guard trimmed.count >= shortestFTSQuery else { return .literalFallback(trimmed) }
        return .fts(quotedPhrase(trimmed))
    }

    /// A single quoted phrase. Doubling the quote character is FTS5's own
    /// escape for a literal quote inside a quoted string, so a query that is
    /// just a quote searches for a quote character rather than opening a
    /// string that never closes.
    static func quotedPhrase(_ literal: String) -> String {
        "\"" + literal.replacingOccurrences(of: "\"", with: "\"\"") + "\""
    }
}

// MARK: - Index

enum SearchIndexError: Error, Equatable, Sendable {
    case sqlite(status: Int32, message: String)
    case unsupportedRuntime
}

/// The rebuildable, session-derived full-text index.
///
/// **Derived state, never a source of truth.** It holds a copy of message text
/// in memory only so FTS5 can find candidates; every result is re-read from
/// the canonical MessageStore before it reaches the UI, and a row that has since
/// been deleted is dropped rather than shown. Nothing is written to disk, so the
/// index cannot outlive the process, cannot be stale across a relaunch, and
/// never needs a schema version of its own.
///
/// **What is deliberately not indexed:** attachment bytes, filenames,
/// extensions, paths and hashes; Memory, Daily Summary, Reminders and AI
/// output; operator display names. Searching for a file extension must not
/// match a stored attachment, and a display name must never make a
/// conversation findable when no message in it says the word.
actor LocalMessageSearchIndex {
    /// How many canonical rows are pulled into the index per batch. Bounded so
    /// building a large corpus never materializes the whole corpus as one
    /// Swift array.
    static let chunkSize = 500

    /// Hard result cap. The read model is bounded even if a query would match
    /// thousands of rows.
    static let maximumResults = 100

    /// Excerpt length. A search result is a UI row, not a document viewer, and
    /// an unbounded excerpt would let one long message dominate the read model.
    private static let excerptLength = 240

    private let store: MessageStore
    private var handle: OpaquePointer?
    private var isBuilt = false
    private var indexedDocumentCount = 0

    init(store: MessageStore) {
        self.store = store
    }

    // MARK: Freshness

    /// Drops the index. The next query rebuilds from canonical state.
    ///
    /// Called by every path that changes searchable text: visual persistence,
    /// archive import, retention sweeps, Delete All History, and consent being
    /// withdrawn. It is deliberately *not* called by display-name or
    /// attachment-only changes, because neither changes indexed text.
    func invalidate() {
        isBuilt = false
        indexedDocumentCount = 0
        close()
    }

    /// Aggregate document count, for the preparing state and acceptance
    /// reporting. Builds if needed; never returns text.
    func documentCount() async throws -> Int {
        if try await !ensureBuilt() { return 0 }
        return indexedDocumentCount
    }

    // MARK: Query

    /// Runs one literal search and returns display-safe results.
    ///
    /// Read-only by construction: the only SQL executed against the canonical
    /// store is SELECT, and nothing is written anywhere by this type.
    func search(
        _ raw: String,
        filter: LocalSearchFilter = .all,
        limit: Int = LocalMessageSearchIndex.maximumResults
    ) async throws -> LocalSearchSnapshot {
        let boundedLimit = max(1, min(limit, LocalMessageSearchIndex.maximumResults))
        switch LocalSearchQueryCompiler.compile(raw) {
        case .rejected:
            return LocalSearchSnapshot(status: .noMatches, results: [])
        case .literalFallback(let needle):
            return try await finalize(
                candidates: try await literalScan(
                    needle, sources: filter.sources, limit: boundedLimit
                ),
                needle: needle,
                limit: boundedLimit
            )
        case .fts(let expression):
            guard try await ensureBuilt() else {
                return LocalSearchSnapshot(status: .failed)
            }
            return try await finalize(
                candidates: try matchFTS(
                    expression, sources: filter.sources, limit: boundedLimit
                ),
                needle: raw.trimmingCharacters(in: .whitespacesAndNewlines),
                limit: boundedLimit
            )
        }
    }

    /// One canonical-read pass that turns index candidates into results.
    ///
    /// This is the boundary that keeps the index honest: nothing the FTS copy
    /// said about a row is trusted. Labels, sender, timestamp and excerpt all
    /// come from a fresh read of the canonical store, and a candidate whose row
    /// no longer exists simply disappears.
    private func finalize(
        candidates: [SearchCandidate],
        needle: String,
        limit: Int
    ) async throws -> LocalSearchSnapshot {
        var results: [LocalSearchResult] = []
        for candidate in candidates where results.count < limit {
            guard let result = try await canonicalResult(for: candidate, needle: needle) else {
                continue
            }
            results.append(result)
        }
        guard !results.isEmpty else {
            return LocalSearchSnapshot(
                status: .noMatches,
                results: [],
                indexedDocumentCount: indexedDocumentCount
            )
        }
        return LocalSearchSnapshot(
            status: .results(count: results.count),
            results: results,
            indexedDocumentCount: indexedDocumentCount
        )
    }

    private func canonicalResult(
        for candidate: SearchCandidate,
        needle: String
    ) async throws -> LocalSearchResult? {
        switch candidate.source {
        case .visual:
            guard let messageID = candidate.visualID,
                  let message = try await store.persistedMessage(id: messageID),
                  let label = try await store.conversationTitle(forMessageID: messageID)
            else { return nil }
            return LocalSearchResult(
                id: "visual:\(messageID)",
                source: .visual,
                provenance: .visualCaptured,
                conversationLabel: label,
                sender: message.sender,
                // Capture metadata, never a parsed visible-time string.
                timestamp: message.firstObservedAt,
                excerpt: Self.excerpt(from: message.text, around: needle),
                linkState: nil
            )
        case .archiveAttributed, .archiveUnattributed:
            guard let importID = candidate.importID,
                  let sequence = candidate.sequence,
                  let record = try await store.archiveRecord(
                      importID: importID, sequence: sequence
                  ),
                  let summary = try await store.archiveImportSummary(id: importID)
            else { return nil }
            return LocalSearchResult(
                id: "archive:\(importID):\(sequence)",
                source: candidate.source,
                provenance: candidate.source == .archiveAttributed
                    ? .archiveAttributed
                    : .archiveUnattributed,
                // Read at result time, so a rename after indexing shows up
                // without a rebuild.
                conversationLabel: summary.displayName ?? "Imported WeChat Archive",
                sender: record.sender,
                timestamp: record.sentAt,
                excerpt: Self.excerpt(from: record.text, around: needle),
                linkState: summary.isAnonymous
                    ? (summary.link == nil ? "Unlinked export" : "Explicitly linked")
                    : nil
            )
        }
    }

    // MARK: Index build

    /// Builds the index if it is missing. Returns false when the runtime cannot
    /// support the search at all, which is a refusal rather than a silent
    /// downgrade to a different tokenizer.
    private func ensureBuilt() async throws -> Bool {
        if isBuilt, handle != nil { return true }
        guard try openIndex() else { return false }
        do {
            try await buildAll()
        } catch {
            // A failed or cancelled build must not publish a partial index.
            invalidate()
            throw error
        }
        isBuilt = true
        return true
    }

    private func openIndex() throws -> Bool {
        close()
        var handle: OpaquePointer?
        guard sqlite3_open_v2(
            ":memory:", &handle,
            SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX, nil
        ) == SQLITE_OK, let handle else {
            self.handle = nil
            return false
        }
        self.handle = handle
        do {
            // Provenance is UNINDEXED: carried, not searched. The two indexed
            // columns are the sender and the message text -- the only
            // searchable evidence B6 indexes.
            try exec(
                """
                CREATE VIRTUAL TABLE search_documents USING fts5(
                    sender, body,
                    source UNINDEXED,
                    visual_id UNINDEXED,
                    import_id UNINDEXED,
                    sequence UNINDEXED,
                    tokenize='trigram'
                );
                """
            )
            return true
        } catch {
            close()
            return false
        }
    }

    /// Enumerates canonical rows in bounded chunks and inserts them.
    ///
    /// Cancellation is checked between chunks, so abandoning a build mid-way
    /// cannot publish a half-indexed result set.
    private func buildAll() async throws {
        var inserted = 0
        var cursor: Int64 = 0
        while true {
            try Task.checkCancellation()
            let rows = try await store.searchableVisualMessages(
                afterMessageID: cursor, limit: Self.chunkSize
            )
            if rows.isEmpty { break }
            cursor = rows[rows.count - 1].messageID
            try insertAll(rows.map {
                SearchDocument(
                    source: .visual,
                    sender: $0.sender,
                    body: $0.text,
                    visualID: $0.messageID,
                    importID: nil,
                    sequence: nil
                )
            })
            inserted += rows.count
        }

        var archiveCursor = (importID: Int64(0), sequence: 0)
        while true {
            try Task.checkCancellation()
            let rows = try await store.searchableArchiveAttributedRecords(
                after: archiveCursor, limit: Self.chunkSize
            )
            if rows.isEmpty { break }
            archiveCursor = (rows[rows.count - 1].importID, rows[rows.count - 1].sequence)
            try insertAll(rows.map {
                SearchDocument(
                    source: .archiveAttributed,
                    sender: $0.sender,
                    body: $0.text,
                    visualID: nil,
                    importID: $0.importID,
                    sequence: $0.sequence
                )
            })
            inserted += rows.count
        }

        archiveCursor = (importID: Int64(0), sequence: 0)
        while true {
            try Task.checkCancellation()
            let rows = try await store.searchableArchiveUnattributedRecords(
                after: archiveCursor, limit: Self.chunkSize
            )
            if rows.isEmpty { break }
            archiveCursor = (rows[rows.count - 1].importID, rows[rows.count - 1].sequence)
            try insertAll(rows.map {
                SearchDocument(
                    source: .archiveUnattributed,
                    sender: nil,
                    body: $0.text,
                    visualID: nil,
                    importID: $0.importID,
                    sequence: $0.sequence
                )
            })
            inserted += rows.count
        }
        indexedDocumentCount = inserted
    }

    private func insertAll(_ documents: [SearchDocument]) throws {
        try exec("BEGIN;")
        do {
            for document in documents {
                try insert(document)
            }
            try exec("COMMIT;")
        } catch {
            try? exec("ROLLBACK;")
            throw error
        }
    }

    private func insert(_ document: SearchDocument) throws {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(
            handle,
            """
            INSERT INTO search_documents(sender, body, source, visual_id, import_id, sequence)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            -1, &statement, nil
        ) == SQLITE_OK, let statement else {
            throw SearchIndexError.sqlite(status: -1, message: "prepare insert")
        }
        defer { sqlite3_finalize(statement) }
        Self.bind(statement, 1, document.sender)
        Self.bind(statement, 2, document.body)
        Self.bind(statement, 3, document.source.rawValue)
        if let visualID = document.visualID {
            sqlite3_bind_int64(statement, 4, visualID)
        } else {
            sqlite3_bind_null(statement, 4)
        }
        if let importID = document.importID {
            sqlite3_bind_int64(statement, 5, importID)
        } else {
            sqlite3_bind_null(statement, 5)
        }
        if let sequence = document.sequence {
            sqlite3_bind_int64(statement, 6, Int64(sequence))
        } else {
            sqlite3_bind_null(statement, 6)
        }
        guard sqlite3_step(statement) == SQLITE_DONE else {
            throw SearchIndexError.sqlite(status: -1, message: "insert")
        }
    }

    // MARK: FTS matching

    private func matchFTS(
        _ expression: String,
        sources: Set<LocalSearchSource>,
        limit: Int
    ) throws -> [SearchCandidate] {
        // The MATCH runs against the bare table name and returns rowids only.
        // UNINDEXED columns carry no FTS terms, and reading them from a table
        // constrained by MATCH is not guaranteed to return their values -- in
        // this runtime it returns NULL -- so provenance is fetched separately.
        // Filtering by source also happens there, which lets a source filter
        // exclude most of the corpus without starving the result limit: the
        // over-fetch below is what pays for that.
        let rowids = try rankedRowIDs(expression, overFetch: limit * 8)
        guard !rowids.isEmpty else { return [] }
        return try provenance(forRowIDs: rowids, sources: sources, limit: limit)
    }

    private func rankedRowIDs(_ expression: String, overFetch: Int) throws -> [Int64] {
        let rows: [Int64?] = try select(
            """
            SELECT rowid FROM search_documents
            WHERE search_documents MATCH ?
            ORDER BY rank LIMIT ?;
            """,
            binds: { statement in
                Self.bind(statement, 1, expression)
                sqlite3_bind_int64(statement, 2, Int64(overFetch))
            },
            row: { statement in SearchIndexText.int64(statement, 0) }
        )
        return rows.compactMap { $0 }
    }

    private func provenance(
        forRowIDs rowids: [Int64],
        sources: Set<LocalSearchSource>,
        limit: Int
    ) throws -> [SearchCandidate] {
        // Both lists are inlined rather than bound. Measured on this runtime
        // (SQLite 3.54.0): bound parameters inside an IN list against an FTS5
        // table do not reliably work -- a single-element list returns no rows
        // instead of one, and a three-element source filter fails with
        // "datatype mismatch". Inlining is safe here because both lists are
        // built from types that cannot carry SQL: Int64 formatting, and a
        // closed enum of string constants. A bound value outside an IN list
        // (the LIMIT below) behaves correctly and stays bound.
        let ids = rowids.map(String.init).joined(separator: ", ")
        let ordered = sources.sorted { $0.rawValue < $1.rawValue }
        let values = ordered.map { sqlLiteral($0.rawValue) }.joined(separator: ", ")
        return try select(
            """
            SELECT source, visual_id, import_id, sequence FROM search_documents
            WHERE rowid IN (\(ids)) AND source IN (\(values))
            ORDER BY rowid LIMIT ?;
            """,
            binds: { statement in
                sqlite3_bind_int64(statement, 1, Int64(limit))
            },
            row: SearchCandidate.init(row:)
        )
    }

    /// The bounded literal path for one and two character queries.
    ///
    /// It reads the canonical tables directly with instr(), which is literal by
    /// construction, parameterized, capped, and provenance-correct. It does not
    /// consult the index at all, so a short query is never limited by
    /// trigram's three-character minimum.
    private func literalScan(
        _ needle: String,
        sources: Set<LocalSearchSource>,
        limit: Int
    ) async throws -> [SearchCandidate] {
        var candidates: [SearchCandidate] = []
        func room() -> Int { max(1, limit - candidates.count) }
        if sources.contains(.visual) {
            candidates += try await store.searchableVisualMessages(
                containing: needle, limit: room()
            ).map {
                SearchCandidate(source: .visual, visualID: $0.messageID, importID: nil, sequence: nil)
            }
        }
        if sources.contains(.archiveAttributed) {
            candidates += try await store.searchableArchiveAttributedRecords(
                containing: needle, limit: room()
            ).map {
                SearchCandidate(
                    source: .archiveAttributed, visualID: nil,
                    importID: $0.importID, sequence: $0.sequence
                )
            }
        }
        if sources.contains(.archiveUnattributed) {
            candidates += try await store.searchableArchiveUnattributedRecords(
                containing: needle, limit: room()
            ).map {
                SearchCandidate(
                    source: .archiveUnattributed, visualID: nil,
                    importID: $0.importID, sequence: $0.sequence
                )
            }
        }
        return Array(candidates.prefix(limit))
    }

    // MARK: Excerpt

    /// A bounded excerpt centred on the first literal occurrence.
    static func excerpt(from text: String?, around needle: String) -> String {
        let body = text ?? ""
        guard !body.isEmpty else { return "" }
        guard body.count > excerptLength else { return body }
        guard !needle.isEmpty, let range = body.range(of: needle) else {
            return String(body.prefix(excerptLength)) + "…"
        }
        let centre = body.distance(from: body.startIndex, to: range.lowerBound)
        let start = max(0, centre - excerptLength / 2)
        let prefix = start > 0 ? "…" : ""
        let slice = String(body.dropFirst(start).prefix(excerptLength))
        return prefix + slice + (start + excerptLength < body.count ? "…" : "")
    }

    // MARK: SQLite plumbing

    private func close() {
        if let handle { sqlite3_close_v2(handle) }
        handle = nil
    }

    private func exec(_ sql: String) throws {
        guard let handle else { throw SearchIndexError.unsupportedRuntime }
        var error: UnsafeMutablePointer<CChar>?
        guard sqlite3_exec(handle, sql, nil, nil, &error) == SQLITE_OK else {
            let message = error.map { String(cString: $0) } ?? "sqlite error"
            sqlite3_free(error)
            throw SearchIndexError.sqlite(status: -1, message: message)
        }
    }

    private func select<Row>(
        _ sql: String,
        binds: (OpaquePointer) -> Void,
        row: (OpaquePointer) -> Row
    ) throws -> [Row] {
        guard let handle else { throw SearchIndexError.unsupportedRuntime }
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(handle, sql, -1, &statement, nil) == SQLITE_OK, let statement else {
            throw SearchIndexError.sqlite(status: -1, message: "prepare select")
        }
        defer { sqlite3_finalize(statement) }
        binds(statement)
        var rows: [Row] = []
        while true {
            let step = sqlite3_step(statement)
            switch step {
            case SQLITE_ROW: rows.append(row(statement))
            case SQLITE_DONE: return rows
            default:
                throw SearchIndexError.sqlite(status: step, message: "select")
            }
        }
    }

    /// Bound by explicit byte count, never -1: a NUL inside chat text would
    /// otherwise truncate the string and silently drop everything after it.
    private static func bind(_ statement: OpaquePointer, _ index: Int32, _ value: String?) {
        guard let value else {
            sqlite3_bind_null(statement, index)
            return
        }
        var bytes = Array(value.utf8)
        // An empty string must bind a non-nil pointer, or SQLite reads it as NULL.
        sqlite3_bind_text(
            statement, index, &bytes, Int32(bytes.count),
            unsafeBitCast(-1, to: sqlite3_destructor_type.self)
        )
    }

}

// MARK: - Index-side values

/// One visual message as the index needs it: identity plus the two indexed
/// values. Deliberately not a `PersistedMessage`, so nothing that reads a
/// conversation's full history can be reached from the index build.
struct SearchableVisualMessage: Sendable, Equatable {
    let messageID: Int64
    let sender: String?
    let text: String?
}

/// One archive record as the index needs it. `sender` is nil for Shape B,
/// which is the honest description of an archive that carried no attribution.
struct SearchableArchiveRecord: Sendable, Equatable {
    let importID: Int64
    let sequence: Int
    let sender: String?
    let text: String
}

/// One row on its way into the index. sender and body are the only indexed
/// values; everything else is carried provenance.
struct SearchDocument: Sendable, Equatable {
    let source: LocalSearchSource
    let sender: String?
    let body: String?
    let visualID: Int64?
    let importID: Int64?
    let sequence: Int?
}

/// A hit read back out of the index: identity only, no text. It is resolved
/// against the canonical store before anything reaches the UI.
struct SearchCandidate: Sendable, Equatable {
    let source: LocalSearchSource
    let visualID: Int64?
    let importID: Int64?
    let sequence: Int?

    init(source: LocalSearchSource, visualID: Int64?, importID: Int64?, sequence: Int?) {
        self.source = source
        self.visualID = visualID
        self.importID = importID
        self.sequence = sequence
    }

    init(row: OpaquePointer) {
        source = LocalSearchSource(rawValue: SearchIndexText.string(row, 0) ?? "") ?? .visual
        visualID = SearchIndexText.int64(row, 1)
        importID = SearchIndexText.int64(row, 2)
        sequence = SearchIndexText.int64(row, 3).map(Int.init)
    }
}

private enum SearchIndexText {
    static func string(_ statement: OpaquePointer, _ index: Int32) -> String? {
        guard sqlite3_column_type(statement, index) != SQLITE_NULL,
              let pointer = sqlite3_column_text(statement, index)
        else { return nil }
        return String(cString: pointer)
    }

    static func int64(_ statement: OpaquePointer, _ index: Int32) -> Int64? {
        guard sqlite3_column_type(statement, index) != SQLITE_NULL else { return nil }
        return sqlite3_column_int64(statement, index)
    }
}

private func sqlLiteral(_ value: String) -> String {
    "'" + value.replacingOccurrences(of: "'", with: "''") + "'"
}
