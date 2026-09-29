import CryptoKit
import Foundation

/// Opaque storage bucket for one archive source.
///
/// When the export carries a source-authored conversation identifier, the key
/// is derived from that evidence. Real WeChat merged-forward exports may carry
/// no conversation identifier at all; in that case the importer uses an
/// anonymous import-scoped key derived from the parsed transcript. That fallback
/// does **not** assert that two different exports came from the same chat.
///
/// This is deliberately not a `conversations.id`. Whether an archive came from
/// the same WeChat conversation as something the visual path captured is an
/// open question (D-019, D-022) and schema v2 carries no column that could be
/// mistaken for an answer. A link relation belongs to a later phase, where its
/// deletion semantics can be designed on their own -- pairing a nullable
/// foreign key with a CHECK, as an earlier draft did, makes the referenced
/// table undeletable (F-032).
struct ArchiveConversationKey: Sendable, Equatable, Hashable {
    let rawValue: String

    init(_ rawValue: String) { self.rawValue = rawValue }
}

/// What persisting one archive did.
///
/// `alreadyImported` is a normal outcome, not an error: re-importing the same
/// archive is the expected way for a user to find out they already have it.
enum ArchivePersistenceResult: Sendable, Equatable {
    case inserted(importID: Int64, recordCount: Int)
    case alreadyImported(importID: Int64)
}

enum ArchivePersistenceError: Error, Equatable, Sendable {
    /// Local persistence consent is off, so there is nowhere to write. The
    /// store is deliberately *not* opened as a side effect of trying.
    ///
    /// This means **the user has not consented**, and nothing else. It must
    /// never stand in for a store that failed to open: telling someone to turn
    /// on a setting they already turned on is worse than saying nothing.
    case localPersistenceConsentRequired
    /// Consent is on, but the local store could not be opened -- an
    /// incompatible or unreadable database. A different fact from missing
    /// consent, and the user's setting is left exactly as they set it.
    ///
    /// The underlying `MessageStoreError` is kept on the history actor for
    /// diagnostics rather than carried here, so this stays a small stable
    /// contract that exposes no path and no schema detail.
    case localStoreUnavailable
    /// The number of rows actually inserted disagreed with the transcript. The
    /// import is rolled back whole rather than left partial.
    case recordCountMismatch(expected: Int, inserted: Int)
}

/// The canonical fingerprint of an import.
///
/// **Length-framed, never delimiter-joined.** A delimiter is only unambiguous
/// while no value contains it, and these values are arbitrary user-authored
/// UTF-8: `("a‖b", "c")` and `("a", "b‖c")` hash identically under
/// `join("‖")`, which would make two different archives one import. Every field
/// here is written as `tag NUL byteLength NUL bytes`, so a value can contain
/// NUL, newlines, the delimiter of the day, or any emoji without being able to
/// impersonate a boundary.
///
/// The field order is fixed by this type, never by dictionary iteration, and
/// nothing locale- or timezone-dependent is fed into it: `sentAtText` is hashed
/// as WeChat wrote it, so re-interpreting a timezone cannot change an archive's
/// identity.
enum ArchiveImportFingerprint {
    static let domain = "wechat-native-archive-import-fingerprint"

    /// Bumped only when this encoding changes. Persisted beside every import,
    /// because without it a changed encoding would silently turn the UNIQUE
    /// constraint into "never matches" and re-imports would start duplicating.
    static let formatVersion: Int32 = 1

    /// Bumped only when the *persisted interpretation* of a transcript changes.
    /// Not the app version, not the build number: it exists so a later grammar
    /// fix can find the rows an older parser wrote.
    static let parserVersion: Int32 = 1

    private struct Encoder {
        private var digest = SHA256()

        init(domain: String, formatVersion: Int32) {
            digest.update(data: Data(domain.utf8))
            digest.update(data: Data([0]))
            digest.update(data: Data(String(formatVersion).utf8))
            digest.update(data: Data([0]))
        }

        mutating func field(_ tag: String, _ value: String) {
            let bytes = Data(value.utf8)
            digest.update(data: Data(tag.utf8))
            digest.update(data: Data([0]))
            digest.update(data: Data(String(bytes.count).utf8))
            digest.update(data: Data([0]))
            digest.update(data: bytes)
        }

        mutating func field(_ tag: String, _ value: Int) { field(tag, String(value)) }

        consuming func finalized() -> String {
            digest.finalize().map { String(format: "%02x", $0) }.joined()
        }
    }

    /// Computed from the parsed transcript that is about to be inserted -- the
    /// same in-memory value, never a second parse and never the file bytes,
    /// so there is no window in which the thing hashed and the thing stored
    /// could differ.
    static func fingerprint(
        of transcript: WeChatNativeTranscript, conversationKey: ArchiveConversationKey
    ) -> String {
        var encoder = Encoder(domain: domain, formatVersion: formatVersion)
        encoder.field("source_type", ArchiveSourceType.weChatNativeArchive.rawValue)
        encoder.field("transcript_shape", transcript.shapeName)
        encoder.field("source_conversation_key", conversationKey.rawValue)
        encoder.field("record_count", transcript.recordCount)

        switch transcript {
        case .attributed(let attributed):
            for message in attributed.messages {
                encoder.field("\(message.sequence).sequence", message.sequence)
                encoder.field("\(message.sequence).sender", message.sender)
                encoder.field("\(message.sequence).sent_at_text", message.sentAtText)
                encoder.field("\(message.sequence).text", message.text)
            }
        case .unattributed(let unattributed):
            for record in unattributed.records {
                encoder.field("\(record.sequence).sequence", record.sequence)
                encoder.field("\(record.sequence).record_text", record.recordText)
            }
        }
        return encoder.finalized()
    }
}

/// The only source this store accepts today. A row must say what produced it:
/// the store will outlive one importer.
enum ArchiveSourceType: String, Sendable {
    case weChatNativeArchive = "wechat_native_archive"
}


enum ArchiveEvidenceShape: String, Sendable, Equatable {
    case attributed
    case unattributed

    var label: String {
        switch self {
        case .attributed: "Attributed"
        case .unattributed: "Unattributed"
        }
    }
}

struct ArchiveEvidenceImportSummary: Identifiable, Sendable, Equatable {
    let id: Int64
    let displayName: String?
    let shape: ArchiveEvidenceShape
    let importedAt: Date
    let recordCount: Int
    let firstSentAt: Date?
    let lastSentAt: Date?
    let isAnonymous: Bool
    let link: ArchiveConversationLink?
    let attachmentBatchCount: Int
    let attachmentCount: Int
    let materializedAttachmentCount: Int
}


struct ArchiveEvidenceRecordID: Hashable, Sendable {
    let importID: Int64
    let sequence: Int
}

struct ArchiveEvidenceRecord: Identifiable, Sendable, Equatable {
    let importID: Int64
    let importedAt: Date
    let shape: ArchiveEvidenceShape
    let sequence: Int
    let sender: String?
    let sentAt: Date?
    let sentAtText: String?
    let text: String

    var id: ArchiveEvidenceRecordID {
        ArchiveEvidenceRecordID(importID: importID, sequence: sequence)
    }
}


struct ArchiveEvidenceSnapshot: Sendable, Equatable {
    let storeState: LocalHistoryStoreState
    let imports: [ArchiveEvidenceImportSummary]

    static func unavailable(_ state: LocalHistoryStoreState) -> ArchiveEvidenceSnapshot {
        ArchiveEvidenceSnapshot(storeState: state, imports: [])
    }
}


enum ArchiveConversationLinkBasis: String, Sendable, Equatable {
    case `operator`
    case sourceProvided = "source_provided"
}

struct ArchiveConversationLink: Sendable, Equatable {
    let archiveConversationID: Int64
    let visualConversationID: Int64
    let visualConversationTitle: String
    let basis: ArchiveConversationLinkBasis
    let assertedAt: Date
}


enum ArchiveConversationLinkError: Error, Equatable, Sendable {
    case importUnknown
    case visualConversationUnknown
    case conflict(existingVisualConversationID: Int64)
}


// MARK: - B5 attachment evidence

enum ArchiveAttachmentStorageState: String, Sendable, Equatable {
    case materialized
    case unsupportedType = "unsupported_type"
    case typeMismatch = "type_mismatch"
    case oversized
    case budgetExceeded = "budget_exceeded"
}

struct ArchiveAttachmentManifest: Sendable, Equatable {
    let sourceEntryIndex: Int
    let pathExtension: String
    let byteCount: Int
    let crc32: UInt32
    let kind: WeChatNativeAttachmentKind?
    let storageState: ArchiveAttachmentStorageState
    let contentSHA256: String?
    let storedRelativePath: String?
}

enum ArchiveAttachmentBatchPersistenceResult: Sendable, Equatable {
    case inserted(batchID: Int64, attachmentCount: Int, materializedCount: Int)
    case alreadyPersisted(batchID: Int64, attachmentCount: Int, materializedCount: Int)
}

enum ArchiveAttachmentPersistenceError: Error, Equatable, Sendable {
    case localPersistenceConsentRequired
    case localStoreUnavailable
    case attachmentStoreUnavailable
    case materializationFailed
    case manifestPersistenceFailed
}

/// Stable identity for one attachment set observed beside a parsed transcript.
///
/// Source filenames are intentionally absent. The fingerprint is scoped to the
/// transcript import in SQLite and uses only source-entry ordinal, safe extension,
/// uncompressed byte count and CRC declared by the already-verified ZIP. App
/// materialization policy is excluded, so changing a size budget cannot make the
/// same source ZIP look like a different attachment batch.
enum ArchiveAttachmentBatchFingerprint {
    private static let domain = "wechat-native-archive-attachment-batch-v1"

    static func fingerprint(of attachments: [WeChatNativeArchiveAttachment]) -> String {
        var digest = SHA256()
        digest.update(data: Data(domain.utf8))
        digest.update(data: Data([0]))
        digest.update(data: Data(String(attachments.count).utf8))
        digest.update(data: Data([0]))
        for attachment in attachments.sorted(by: { $0.sourceEntryIndex < $1.sourceEntryIndex }) {
            append("entry", String(attachment.sourceEntryIndex), to: &digest)
            append("ext", attachment.pathExtension, to: &digest)
            append("bytes", String(attachment.byteCount), to: &digest)
            append("crc32", String(attachment.crc32), to: &digest)
        }
        return digest.finalize().map { String(format: "%02x", $0) }.joined()
    }

    private static func append(_ tag: String, _ value: String, to digest: inout SHA256) {
        let bytes = Data(value.utf8)
        digest.update(data: Data(tag.utf8))
        digest.update(data: Data([0]))
        digest.update(data: Data(String(bytes.count).utf8))
        digest.update(data: Data([0]))
        digest.update(data: bytes)
    }
}

struct ArchiveEvidenceAttachment: Identifiable, Sendable, Equatable {
    let id: Int64
    let sourceEntryIndex: Int
    let pathExtension: String
    let byteCount: Int
    let kind: WeChatNativeAttachmentKind?
    let storageState: ArchiveAttachmentStorageState

    var isMaterialized: Bool { storageState == .materialized }
}

struct ArchiveEvidenceAttachmentBatch: Identifiable, Sendable, Equatable {
    let id: Int64
    let observedAt: Date
    let attachmentCount: Int
    let materializedCount: Int
    let attachments: [ArchiveEvidenceAttachment]
}


enum ArchiveConversationDisplayNameError: Error, Equatable, Sendable {
    case importUnknown
    case invalidName
}
