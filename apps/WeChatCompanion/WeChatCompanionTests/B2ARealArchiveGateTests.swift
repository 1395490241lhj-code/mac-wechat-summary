import Foundation
import Testing
@testable import WeChatCompanion

/// End-to-end gate against the three real local exports, opt-in.
///
/// Runs only when `TEST_RUNNER_B2A_FIXTURE_DIR` names a directory of archives
/// copied out for this run; nothing private is committed, and the real app
/// database is never touched -- the store is a temporary file that this test
/// deletes. Output is aggregate: counts, shapes, and whether identity was
/// available. Never a source identity value, a hash of one, a filename or a path.
struct B2ARealArchiveGateTests {
    static var fixtureDirectory: String? {
        let environment = ProcessInfo.processInfo.environment
        for key in ["B2A_FIXTURE_DIR", "TEST_RUNNER_B2A_FIXTURE_DIR"] {
            if let value = environment[key], !value.isEmpty { return value }
        }
        return nil
    }

    @Test(.enabled(if: fixtureDirectory != nil, "no B2A_FIXTURE_DIR: real gate not run"))
    func importsAllThreeRealArchivesIntoOneConversation() async throws {
        let directory = URL(fileURLWithPath: try #require(Self.fixtureDirectory))
        let fixtures = try FileManager.default
            .contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension.lowercased() == "zip" }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
        #expect(fixtures.count == 3)

        let scratch = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("b2a-real-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: scratch, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: scratch) }

        let history = LocalMessageHistory(url: scratch.appendingPathComponent("messages.sqlite"))
        await history.setEnabled(true)
        let importer = WeChatNativeArchiveImporter(history: history)

        var lines: [String] = ["fixture | shape | records | identity | first | second"]
        var summaries: [WeChatNativeArchiveImportSummary] = []
        for (index, fixture) in fixtures.enumerated() {
            let summary = try await importer.importArchive(contentsOf: fixture)
            summaries.append(summary)
            lines.append("R\(index + 1) | \(summary.transcriptShape) | \(summary.recordCount) "
                + "| \(summary.sourceIdentityKind == "none" ? "unavailable" : "available") "
                + "| \(summary.outcome.rawValue) | ")
        }

        // Re-import: every one must be recognised as already stored.
        var second: [String] = []
        for fixture in fixtures {
            second.append(try await importer.importArchive(contentsOf: fixture).outcome.rawValue)
        }
        for (index, outcome) in second.enumerated() {
            lines[index + 1] += outcome
        }

        let store = await history.openStore()!
        let conversations = try await store.archiveConversationCount()
        let imports = try await store.archiveImportCount()
        let attributed = try await store.archiveRecordCount(shape: "attributed")
        let unattributed = try await store.archiveRecordCount(shape: "unattributed")

        // Source identity equality reported only as same/different -- never the
        // value, never a hash of it.
        var keys: [String] = []
        for fixture in fixtures {
            let archive = try WeChatNativeArchiveReader.read(contentsOf: fixture)
            keys.append(ArchiveConversationKey(sourceIdentity: try #require(archive.sourceIdentity)).rawValue)
        }
        let pairs = ["R1/R2: \(keys[0] == keys[1] ? "same" : "different")",
                     "R1/R3: \(keys[0] == keys[2] ? "same" : "different")",
                     "R2/R3: \(keys[1] == keys[2] ? "same" : "different")"]

        print("""
            B2A REAL GATE
            \(lines.joined(separator: "\n"))
            source identity: \(pairs.joined(separator: "  "))
            conversation rows: \(conversations)
            imports: \(imports)
            attributed records: \(attributed)
            unattributed records: \(unattributed)
            total archive records: \(attributed + unattributed)
            """)

        #expect(second.allSatisfy { $0 == "alreadyImported" })
        #expect(imports == 3)
        #expect(conversations == 1)
        #expect(attributed + unattributed == 215)
    }
}
