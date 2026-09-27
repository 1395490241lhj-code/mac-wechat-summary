import CryptoKit
import Foundation

enum WeChatArchiveImportError: Error, Equatable, Sendable {
    case conversationIdentityUnavailable
}

struct WeChatArchiveImportOutcome: Sendable, Equatable {
    let persistence: ArchivePersistenceResult
    let transcriptShape: String
    let recordCount: Int
}

enum ArchiveConversationIdentityResolver {
    private static let domain = "wechat-native-archive-conversation-key-v1"

    static func resolve(
        archive: WeChatNativeArchive,
        suggestedConversationName: String?
    ) throws -> ArchiveConversationKey {
        if let topLevel = topLevelDirectory(of: archive.transcriptEntryName) {
            return opaqueKey(kind: "archive-directory", value: topLevel)
        }

        if let suggestedConversationName,
           let normalized = normalizedSuggestedName(suggestedConversationName) {
            return opaqueKey(kind: "share-suggested-name", value: normalized)
        }

        throw WeChatArchiveImportError.conversationIdentityUnavailable
    }

    private static func topLevelDirectory(of entryName: String) -> String? {
        let parts = entryName.split(separator: "/", omittingEmptySubsequences: true)
        guard parts.count > 1 else { return nil }
        let value = String(parts[0]).trimmingCharacters(in: .whitespacesAndNewlines)
        return value.isEmpty ? nil : value
    }

    private static func normalizedSuggestedName(_ value: String) -> String? {
        var name = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return nil }
        if name.lowercased().hasSuffix(".zip") {
            name.removeLast(4)
            name = name.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        return name.isEmpty ? nil : name
    }

    private static func opaqueKey(kind: String, value: String) -> ArchiveConversationKey {
        var digest = SHA256()
        digest.update(data: Data(domain.utf8))
        digest.update(data: Data([0]))
        digest.update(data: Data(kind.utf8))
        digest.update(data: Data([0]))
        digest.update(data: Data(value.utf8))
        let hex = digest.finalize().map { String(format: "%02x", $0) }.joined()
        return ArchiveConversationKey("native-v1:" + hex)
    }
}

/// B2 import service: a file the user explicitly handed to the app is read,
/// validated and persisted as archive evidence under the existing local-history
/// consent. The ZIP is never copied, moved or retained by this service.
struct WeChatArchiveImportService: Sendable {
    let history: LocalMessageHistory

    func importArchive(
        contentsOf url: URL,
        suggestedConversationName: String? = nil,
        importedAt: Date = Date()
    ) async throws -> WeChatArchiveImportOutcome {
        let didAccess = url.startAccessingSecurityScopedResource()
        defer {
            if didAccess { url.stopAccessingSecurityScopedResource() }
        }

        let archive = try WeChatNativeArchiveReader.read(contentsOf: url)
        let conversationKey = try ArchiveConversationIdentityResolver.resolve(
            archive: archive,
            suggestedConversationName: suggestedConversationName
        )
        let persistence = try await history.persistArchiveEvidence(
            transcript: archive.transcript,
            conversationKey: conversationKey,
            importedAt: importedAt
        )
        return WeChatArchiveImportOutcome(
            persistence: persistence,
            transcriptShape: archive.transcriptShape,
            recordCount: archive.recordCount
        )
    }
}
