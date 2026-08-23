import Testing
@testable import WeChatCompanion

struct StatusModelTests {
    @Test
    func diagnosticStatusUsesSafeFailureStates() {
        #expect(DiagnosticResult().status == .screenRecordingPermissionRequired)

        var result = DiagnosticResult()
        result.screenRecordingGranted = true
        #expect(result.status == .wechatNotRunning)

        result.wechatRunning = true
        #expect(result.status == .windowNotFound)

        result.wechatWindowFound = true
        result.waitingForVisibleWeChat = true
        #expect(result.status == .waitingForVisibleWeChat)

        result.waitingForVisibleWeChat = false
        #expect(result.status == .captureFailed)

        result.captureSucceeded = true
        #expect(result.status == .ocrFailed)

        result.ocrSucceeded = true
        #expect(result.status == .succeeded)
        #expect(result.uiInteractionPerformed == false)
        #expect(result.privateContentPersisted == false)
    }

    @Test
    func systemStatusRetainsPassiveCaptureCapabilities() {
        let status = SystemStatus(
            wechatInstalled: true,
            wechatRunning: false,
            screenRecordingGranted: true
        )

        #expect(status.wechatInstalled)
        #expect(!status.wechatRunning)
        #expect(status.screenRecordingGranted)
    }
}
