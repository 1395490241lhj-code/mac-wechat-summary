import Foundation
import Testing
@testable import WeChatCompanion

struct DiagnosticPrivacyTests {
    @Test
    func encodedResultContainsOnlyAggregateContract() throws {
        var result = DiagnosticResult()
        result.screenRecordingGranted = true
        result.wechatRunning = true
        result.captureSucceeded = true
        result.selectedCaptureMode = .visibleDisplayRegion
        result.ocrSucceeded = true
        result.recognizedTextObservationCount = 3
        result.totalRecognizedCharacterCount = 42

        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        let json = String(decoding: try encoder.encode(result), as: UTF8.self)

        #expect(!json.contains("\"recognizedText\":"))
        #expect(!json.contains("chatName"))
        #expect(!json.contains("contactName"))
        #expect(!json.contains("windowTitle"))
        #expect(!json.contains("wxid"))
        #expect(!json.contains("private example content"))
        #expect(!json.contains("imageData"))
        #expect(!json.contains("screenshot"))
        #expect(json.contains("totalRecognizedCharacterCount"))
        #expect(json.contains("selectedCaptureMode"))
        #expect(json.contains("privateContentPersisted"))
    }
}
