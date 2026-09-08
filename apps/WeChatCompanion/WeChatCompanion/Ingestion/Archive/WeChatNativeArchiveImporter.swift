import Foundation

/// What an import did, in facts that are safe to show or log.
///
/// Deliberately carries no source identity, no entry name, no path and no
/// content: everything here is shape or a count.
struct WeChatNativeArchiveImportSummary: Sendable, Equatable {
    enum Outcome: String, Sendable, Equatable {
        case inserted
        /// This exact archive was already stored. A normal answer, not a
        /// failure: it is how a user finds out they already have it.
        case alreadyImported
    }

    let outcome: Outcome
    let transcriptShape: String
    let recordCount: Int
    let attributionAvailable: Bool
    let perMessageTimeAvailable: Bool
    let attachmentCountsByExtension: [String: Int]
    let sourceIdentityKind: String
}

/// Why an import did not happen.
///
/// Kept as distinct cases because each one needs a different response from the
/// user: turn a setting on, fix a database, pick a different file, or nothing
/// at all. Collapsing them into one `importFailed` would leave every one of
/// those people with the same unusable message. No case carries a path, a
/// filename, a conversation key or any content.
enum WeChatNativeArchiveImportError: Error, Equatable, Sendable {
    /// The user has not consented to local persistence. The archive is not read.
    case localPersistenceConsentRequired
    /// Consent is on, but the local store could not be opened. Not the user's
    /// setting, and not this file's fault. The archive is not read.
    case localStoreUnavailable
    /// The container is not a ZIP this reader will accept.
    case archive(ZIPArchiveError)
    /// No entry parses as either observed native transcript shape.
    case unsupportedTranscript
    /// Recognizable transcripts of different shapes; refused rather than
    /// resolved by picking the longer one.
    case ambiguousTranscriptCandidates
    /// The transcript is fine, but the archive's layout establishes no
    /// source-side conversation identity, and B1 keys durable evidence on one.
    case sourceConversationIdentityUnavailable
    /// The write refused itself: the rows inserted disagreed with the
    /// transcript, so the import was rolled back whole. A bug on our side, not
    /// something the user can act on -- and the counts stay internal, because
    /// they say how long the conversation was.
    case persistenceInvariantFailed
    /// The store failed for some other reason. Deliberately opaque: the
    /// underlying SQLite status is a diagnostic, not something to show or to
    /// let a caller branch on.
    case persistenceFailed
}

/// Turns a native WeChat export into durable local evidence.
///
/// This is the first end-to-end path: ZIP → validated archive → source identity
/// → Shape A/B transcript → B1 storage. It is the backend contract a file
/// picker will call; it contains no UI and opens no panel.
///
/// **The archive is read exactly once.** The transcript and the source identity
/// come out of the same `ZIPArchiveReader`, and that same in-memory transcript
/// is what gets fingerprinted and inserted. Reading twice -- once for the
/// transcript, once to work out which conversation it belongs to -- would let
/// the two answers describe different bytes if the file changed in between,
/// which is the hash-then-reparse mistake in a new costume.
struct WeChatNativeArchiveImporter: Sendable {
    let history: LocalMessageHistory

    init(history: LocalMessageHistory) { self.history = history }

    /// Runs between the archive read and the write, so a test can change the
    /// history's state exactly in the window the preflight cannot cover.
    ///
    /// A deterministic seam rather than a sleep: the race is "state changed
    /// after preflight", and reproducing it by timing would make the test flaky
    /// about the one thing it is supposed to pin down.
    typealias AfterReadHook = @Sendable () async -> Void

    @discardableResult
    func importArchive(
        contentsOf url: URL,
        limits: ZIPArchiveReader.Limits = .standard,
        timeZone: TimeZone = WeChatNativeTranscriptParser.defaultTimeZone,
        importedAt: Date = Date(),
        afterReadForTesting: AfterReadHook? = nil
    ) async throws -> WeChatNativeArchiveImportSummary {
        // Preflight before the file is opened. If nothing can be stored, there
        // is no reason to decompress and parse somebody's conversation to find
        // that out -- the cheapest way to protect private data is not to read
        // it. The same check runs again at the point of writing, so a consent
        // change during the import still fails closed.
        switch await history.storeState {
        case .disabled: throw WeChatNativeArchiveImportError.localPersistenceConsentRequired
        case .unavailable: throw WeChatNativeArchiveImportError.localStoreUnavailable
        case .ready: break
        }

        // A URL from a file picker arrives security-scoped; one from a test or
        // an unsandboxed context does not. A `false` return means "no scope was
        // needed", not "access denied", so it must not be treated as a failure.
        // Only a successful start is balanced by a stop.
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }

        let archive: WeChatNativeArchive
        do {
            archive = try WeChatNativeArchiveReader.read(
                contentsOf: url, limits: limits, timeZone: timeZone
            )
        } catch let error as WeChatNativeArchiveError {
            throw Self.mapped(error)
        }

        await afterReadForTesting?()

        guard let identity = archive.sourceIdentity else {
            // Refusing costs the user one import. Inventing a key -- from the
            // filename, from a transcript hash, from anything at all -- would
            // silently merge two conversations or split one, and nothing later
            // could tell that it had happened.
            throw WeChatNativeArchiveImportError.sourceConversationIdentityUnavailable
        }

        // The preflight above is an optimisation, not the guarantee: consent can
        // be withdrawn, or the store can fail, while the archive is being read.
        // B1 re-checks at write time and this maps whatever it says back into
        // one enum, so a caller never has to know that `ArchivePersistenceError`
        // or `MessageStoreError` exist.
        let result: ArchivePersistenceResult
        do {
            result = try await history.persistArchiveEvidence(
                transcript: archive.transcript,
                conversationKey: ArchiveConversationKey(sourceIdentity: identity),
                importedAt: importedAt
            )
        } catch let error as ArchivePersistenceError {
            throw Self.mapped(error)
        } catch {
            // MessageStoreError and anything else the storage layer can raise.
            // Collapsed on purpose: a raw SQLite status is a diagnostic, and
            // leaking the type would make it part of this contract.
            throw WeChatNativeArchiveImportError.persistenceFailed
        }

        return WeChatNativeArchiveImportSummary(
            outcome: result.isInsert ? .inserted : .alreadyImported,
            transcriptShape: archive.transcriptShape,
            recordCount: archive.recordCount,
            attributionAvailable: archive.attributionAvailable,
            perMessageTimeAvailable: archive.perMessageTimeAvailable,
            attachmentCountsByExtension: archive.attachmentCountsByExtension,
            sourceIdentityKind: identity.kind
        )
    }

    private static func mapped(_ error: ArchivePersistenceError) -> WeChatNativeArchiveImportError {
        switch error {
        case .localPersistenceConsentRequired: .localPersistenceConsentRequired
        case .localStoreUnavailable: .localStoreUnavailable
        // Never reported as a consent or store problem: it is neither, and
        // saying so would send the user to fix something that is not broken.
        case .recordCountMismatch: .persistenceInvariantFailed
        }
    }

    private static func mapped(_ error: WeChatNativeArchiveError) -> WeChatNativeArchiveImportError {
        switch error {
        case .archive(let underlying): .archive(underlying)
        case .noRecognizableTranscript: .unsupportedTranscript
        case .ambiguousTranscriptCandidates: .ambiguousTranscriptCandidates
        }
    }
}

extension ArchivePersistenceResult {
    var isInsert: Bool {
        if case .inserted = self { return true }
        return false
    }
}

extension ArchiveConversationKey {
    /// Builds the storage key from a reader-level source identity.
    ///
    /// The mapping lives here, at the coordinator boundary, so the archive
    /// reader keeps no dependency on the persistence layer.
    ///
    /// The key is **domain- and version-qualified, and length-framed**, so a
    /// future derivation rule cannot silently produce the same key as this one
    /// for a different thing. `\0` separators are safe because B1's storage is
    /// embedded-NUL correct; the raw directory component is carried verbatim.
    init(sourceIdentity: WeChatNativeArchiveSourceIdentity) {
        switch sourceIdentity {
        case .singleTopLevelDirectory(let raw):
            let bytes = raw.utf8.count
            self.init(
                "wechat_native_archive.source_identity\u{0}"
                + "\(sourceIdentity.kind)\u{0}1\u{0}\(bytes)\u{0}\(raw)"
            )
        }
    }
}
