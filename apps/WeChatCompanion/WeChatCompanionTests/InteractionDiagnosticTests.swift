import Foundation
import Testing
@testable import WeChatCompanion

struct InteractionDiagnosticTests {
    @Test
    func encodedResultContainsOnlySafeCapabilityMetadata() throws {
        var result = InteractionDiagnosticResult()
        result.timestamp = Date(timeIntervalSince1970: 1_700_000_000)
        result.changedPixelRatio = 0.125

        let json = String(decoding: try JSONEncoder().encode(result), as: UTF8.self)

        #expect(!json.contains("chatName"))
        #expect(!json.contains("contactName"))
        #expect(!json.contains("messageText"))
        #expect(!json.contains("windowTitle"))
        #expect(!json.contains("screenshot"))
        #expect(json.contains("changedPixelRatio"))
        #expect(json.contains("privateContentPersisted"))
    }

    @Test
    func statusRequiresVerifiedRestorationAfterSuccessfulScroll() {
        var result = InteractionDiagnosticResult()
        result.accessibilityGranted = true
        result.screenRecordingGranted = true
        result.wechatRunning = true
        result.wechatWindowFound = true
        result.backgroundScrollSucceeded = true

        #expect(result.status == .restoreNotVerified)

        result.restoreAttempted = true
        result.restoreAppearsSuccessful = true
        #expect(result.status == .succeeded)
    }

    @Test
    func statusReportsSafeFailureStates() {
        var result = InteractionDiagnosticResult()
        #expect(result.status == .accessibilityPermissionRequired)

        result.accessibilityGranted = true
        #expect(result.status == .wechatNotRunning)

        result.wechatRunning = true
        #expect(result.status == .windowNotFound)

        result.wechatWindowFound = true
        #expect(result.status == .visualVerificationUnavailable)

        result.screenRecordingGranted = true
        #expect(result.status == .noChangeDetected)
        #expect(!result.messageWasSent)
        #expect(!result.printableKeyboardInputPerformed)
        #expect(!result.databaseAccessPerformed)
        #expect(!result.privateContentPersisted)
    }

    @Test
    func interactionMetadataRoundTripsWithoutPrivatePayload() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = InteractionDiagnosticsStore(
            fileURL: directory.appendingPathComponent("interaction-diagnostics.json")
        )
        var expected = InteractionDiagnosticResult()
        expected.timestamp = Date(timeIntervalSince1970: 1_700_000_000)
        expected.accessibilityGranted = true

        try store.save(expected)

        #expect(try store.load() == expected)
    }
}
