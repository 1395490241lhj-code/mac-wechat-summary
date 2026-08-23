import Foundation
import Testing
@testable import WeChatCompanion

struct DiagnosticsStoreTests {
    @Test
    func saveAndLoadPrivacySafeMetadata() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let store = DiagnosticsStore(
            fileURL: directory.appendingPathComponent("diagnostics.json")
        )
        var expected = DiagnosticResult()
        expected.timestamp = Date(timeIntervalSince1970: 1_700_000_000)
        expected.captureWidth = 1200
        expected.captureHeight = 800
        expected.recognizedTextObservationCount = 7
        expected.totalRecognizedCharacterCount = 91

        try store.save(expected)

        #expect(try store.load() == expected)
        let persisted = try String(contentsOf: store.fileURL, encoding: .utf8)
        #expect(!persisted.contains("\"recognizedText\":"))
        #expect(!persisted.contains("windowTitle"))
        #expect(!persisted.contains("private example content"))
        #expect(FileManager.default.fileExists(atPath: store.fileURL.path))
    }
}
