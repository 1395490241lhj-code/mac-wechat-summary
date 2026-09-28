import CryptoKit
import Foundation

enum WeChatArchiveAttachmentImportOutcome: Sendable, Equatable {
    case none
    case inserted(attachmentCount: Int, materializedCount: Int)
    case alreadyPersisted(attachmentCount: Int, materializedCount: Int)
    case unavailable
}

struct WeChatArchiveImportOutcome: Sendable, Equatable {
    let persistence: ArchivePersistenceResult
    let transcriptShape: String
    let recordCount: Int
    let attachments: WeChatArchiveAttachmentImportOutcome
}

enum ArchiveConversationIdentityResolver {
    private static let domain = "wechat-native-archive-conversation-key-v1"
    private static let anonymousSeedKey = ArchiveConversationKey(
        "native-anonymous-import-seed-v1"
    )

    static func resolve(archive: WeChatNativeArchive) -> ArchiveConversationKey {
        if let topLevel = topLevelDirectory(of: archive.transcriptEntryName) {
            return opaqueKey(
                kind: "archive-directory",
                value: topLevel,
                prefix: "native-v1:"
            )
        }

        // Real WeChat 4.1.x merged-forward exports can contain only a root-level
        // transcript, with no source-authored conversation identifier at all.
        // Do not guess from participants, message text, or provider filenames.
        //
        // Instead, give that exact parsed export an opaque import-scoped bucket.
        // The same transcript resolves to the same key, so re-import stays
        // idempotent. Different exports remain separate until a later linkage
        // phase has trustworthy identity evidence.
        let transcriptFingerprint = ArchiveImportFingerprint.fingerprint(
            of: archive.transcript,
            conversationKey: anonymousSeedKey
        )
        return opaqueKey(
            kind: "anonymous-import",
            value: transcriptFingerprint,
            prefix: "native-anonymous-v1:"
        )
    }

    private static func topLevelDirectory(of entryName: String) -> String? {
        let parts = entryName.split(separator: "/", omittingEmptySubsequences: true)
        guard parts.count > 1 else { return nil }
        let value = String(parts[0]).trimmingCharacters(in: .whitespacesAndNewlines)
        return value.isEmpty ? nil : value
    }

    private static func opaqueKey(
        kind: String,
        value: String,
        prefix: String
    ) -> ArchiveConversationKey {
        var digest = SHA256()
        digest.update(data: Data(domain.utf8))
        digest.update(data: Data([0]))
        digest.update(data: Data(kind.utf8))
        digest.update(data: Data([0]))
        digest.update(data: Data(value.utf8))
        let hex = digest.finalize().map { String(format: "%02x", $0) }.joined()
        return ArchiveConversationKey(prefix + hex)
    }
}

/// B2 import service: a file the user explicitly handed to the app is read,
/// validated and persisted as archive evidence under the existing local-history
/// consent. The ZIP is never copied, moved or retained by this service.
struct WeChatArchiveImportService: Sendable {
    let history: LocalMessageHistory

    func importArchive(
        contentsOf url: URL,
        importedAt: Date = Date()
    ) async throws -> WeChatArchiveImportOutcome {
        let didAccess = url.startAccessingSecurityScopedResource()
        defer {
            if didAccess { url.stopAccessingSecurityScopedResource() }
        }

        let archive = try WeChatNativeArchiveReader.read(
            contentsOf: url,
            attachmentReadMode: .materializeSupported
        )
        let conversationKey = ArchiveConversationIdentityResolver.resolve(
            archive: archive
        )
        let persistence = try await history.persistArchiveEvidence(
            transcript: archive.transcript,
            conversationKey: conversationKey,
            importedAt: importedAt
        )
        let importID: Int64
        switch persistence {
        case .inserted(let id, _), .alreadyImported(let id):
            importID = id
        }

        let attachmentOutcome: WeChatArchiveAttachmentImportOutcome
        if archive.attachments.isEmpty {
            attachmentOutcome = .none
        } else {
            do {
                switch try await history.persistArchiveAttachmentBatch(
                    importID: importID,
                    attachments: archive.attachments,
                    observedAt: importedAt
                ) {
                case .inserted(_, let count, let materialized):
                    attachmentOutcome = .inserted(
                        attachmentCount: count,
                        materializedCount: materialized
                    )
                case .alreadyPersisted(_, let count, let materialized):
                    attachmentOutcome = .alreadyPersisted(
                        attachmentCount: count,
                        materializedCount: materialized
                    )
                case nil:
                    attachmentOutcome = .none
                }
            } catch {
                // Transcript persistence has already succeeded. Keep that fact
                // truthful and surface attachment failure as its own outcome.
                attachmentOutcome = .unavailable
            }
        }

        return WeChatArchiveImportOutcome(
            persistence: persistence,
            transcriptShape: archive.transcriptShape,
            recordCount: archive.recordCount,
            attachments: attachmentOutcome
        )
    }
}
