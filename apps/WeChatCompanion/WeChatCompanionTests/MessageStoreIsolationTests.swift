import Foundation
import Testing
@testable import WeChatCompanion

/// Privacy-safe fingerprint of a file's existence and identity.
///
/// Metadata only -- never contents. Enough to prove a file was neither created,
/// modified nor replaced, and nothing more.
struct FileFingerprint: Equatable, Sendable, CustomStringConvertible {
    let exists: Bool
    let size: Int
    let modified: Date?
    let fileID: UInt64?

    init(_ url: URL) {
        let attributes = try? FileManager.default.attributesOfItem(atPath: url.path)
        exists = attributes != nil
        size = (attributes?[.size] as? Int) ?? -1
        modified = attributes?[.modificationDate] as? Date
        // Catches replace-with-an-identical-copy, which size and mtime would miss.
        fileID = (attributes?[.systemFileNumber] as? NSNumber)?.uint64Value
    }

    /// Says whether the file changed, never where it is or what is in it.
    var description: String {
        exists ? "exists size=\(size)" : "absent"
    }
}

/// The canonical memory-store locations, fingerprinted together.
///
/// Used by the memory-sync guard, which proves **non-mutation** rather than
/// absence: an operator who has legitimately run Sync Now owns this file, and
/// its existence is not evidence that a test wrote to it.
func canonicalMemoryStoreFingerprints() -> [String: FileFingerprint] {
    let base = MemoryStoreLocation.canonical
    return [
        "db": FileFingerprint(base),
        "wal": FileFingerprint(URL(fileURLWithPath: base.path + "-wal")),
        "shm": FileFingerprint(URL(fileURLWithPath: base.path + "-shm")),
    ]
}

/// The canonical locations a test must never touch, fingerprinted together.
private func canonicalStoreFingerprints() -> [String: FileFingerprint] {
    let base = MessageStore.applicationSupport
    return [
        "db": FileFingerprint(base),
        "wal": FileFingerprint(URL(fileURLWithPath: base.path + "-wal")),
        "shm": FileFingerprint(URL(fileURLWithPath: base.path + "-shm")),
    ]
}

/// F-023 guards the *memory* store's location. Nothing guarded the *message*
/// store's, and `AppModel.messageHistory` defaults to it -- so any test that
/// built an `AppModel` without injecting a history and then turned local
/// persistence on drove consent transitions against the operator's real
/// database. This is the missing half of that guard.
///
/// **Isolation has two layers, and this suite can only see one of them.**
///
/// Layer 1 is dependency injection: no test may build an `AppModel` without
/// supplying its own history. That is what the structural check below enforces.
///
/// Layer 2 is the host. The test target uses the app itself as its `TEST_HOST`,
/// so `@main` runs for real and `bootstrap()` would restore the stored consent
/// and open the operator's `messages.sqlite` **before any test executes** --
/// measured: a suite that never touches `AppModel` still moved the canonical
/// `-shm` timestamp. `AppBootstrapPolicy` now suppresses that.
///
/// An in-process test cannot observe state from before its own host launched,
/// so it cannot prove layer 2. The authoritative proof is an external
/// before/after snapshot of the canonical files taken around the whole
/// `xcodebuild test` run; this file deliberately does not claim to replace it.
struct MessageStoreIsolationTests {
    @Test @MainActor
    func aRepresentativePersistenceFlowNeverTouchesTheCanonicalStore() async throws {
        let before = canonicalStoreFingerprints()

        // The full consent lifecycle, through AppModel, using the standard test
        // helper -- the exact path that previously reached the real file.
        let defaults = UserDefaults(suiteName: "isolation-\(UUID().uuidString)")!
        let model = AppModel(
            messageHistory: makeTestMessageHistory(), consentDefaults: defaults
        )
        await model.setAllowsLocalPersistence(true)
        await model.setRetentionPolicy(.sevenDays)
        await model.setAllowsLocalPersistence(false)
        await model.setAllowsLocalPersistence(true)
        await model.deleteLocalMessageHistory()

        let after = canonicalStoreFingerprints()
        for key in ["db", "wal", "shm"] {
            #expect(after[key] == before[key],
                    "the canonical message store's \(key) changed: \(before[key]!) -> \(after[key]!)")
        }
    }

    /// The rule itself, as a structural check: a test that reaches for the
    /// canonical location is the bug, so the suite should not contain one.
    @Test
    func noTestConstructsAnAppModelWithoutInjectingAHistory() throws {
        let directory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        // This file is excluded: it contains the search needle itself as a
        // string literal, and it never builds an AppModel for real.
        let ownName = URL(fileURLWithPath: #filePath).lastPathComponent
        let sources = try FileManager.default
            .contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "swift" && $0.lastPathComponent != ownName }

        for source in sources {
            let text = try String(contentsOf: source, encoding: .utf8)
            var searchRange = text.startIndex..<text.endIndex
            while let found = text.range(of: "AppModel(", range: searchRange) {
                searchRange = found.upperBound..<text.endIndex
                // Skip mentions inside doc comments.
                let lineStart = text[..<found.lowerBound].lastIndex(of: "\n")
                    .map { text.index(after: $0) } ?? text.startIndex
                if text[lineStart..<found.lowerBound].trimmingCharacters(in: .whitespaces)
                    .hasPrefix("///") { continue }

                // Read the balanced call and require an injected history.
                var depth = 1
                var index = found.upperBound
                while index < text.endIndex, depth > 0 {
                    if text[index] == "(" { depth += 1 }
                    if text[index] == ")" { depth -= 1 }
                    index = text.index(after: index)
                }
                let call = String(text[found.lowerBound..<index])
                let file = source.lastPathComponent
                #expect(
                    call.contains("messageHistory:"),
                    "\(file) builds an AppModel without injecting a messageHistory; it would use the real store"
                )
            }
        }
    }
}

/// The host-level half of isolation.
struct RuntimeEnvironmentTests {
    @Test
    func thisProcessIsRecognisedAsATestHost() {
        // If this were ever false, the bootstrap guard would silently stop
        // guarding and nothing else would notice.
        #expect(RuntimeEnvironment.isUnderTestHost)
    }

    @Test
    func bootstrapIsSuppressedUnderATestHostAndAllowedOtherwise() {
        // A pure policy seam, so the decision is testable without launching a
        // second process and without mutating the environment.
        #expect(!AppBootstrapPolicy.shouldBootstrap(isUnderTestHost: true))
        #expect(AppBootstrapPolicy.shouldBootstrap(isUnderTestHost: false))
    }

    @Test
    func theMemorySyncRunnerUsesTheSameDefinitionOfTestHost() {
        // One predicate, two call sites. Two copies would eventually disagree.
        #expect(PackagedMemorySyncRunner.isUnderTestHost == RuntimeEnvironment.isUnderTestHost)
        #expect(PackagedMemorySyncRunner.bundled() == nil)
    }
}
