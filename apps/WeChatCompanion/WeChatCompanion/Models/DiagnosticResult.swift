import Foundation

struct DiagnosticResult: Codable, Equatable, Sendable {
    var timestamp = Date()
    var screenRecordingGranted = false
    var wechatRunning = false
    var wechatWindowFound = false
    var wechatFrontmost = false
    var windowOnScreen = false
    var windowCaptureAttempted = false
    var windowCaptureNonEmpty = false
    var displayRegionCaptureAttempted = false
    var displayRegionCaptureNonEmpty = false
    var waitingForVisibleWeChat = false
    var selectedCaptureMode: CaptureMode?
    var captureSucceeded = false
    var captureWidth = 0
    var captureHeight = 0
    var imageAppearsNonEmpty = false
    var ocrSucceeded = false
    var recognizedTextObservationCount = 0
    var totalRecognizedCharacterCount = 0
    var averageConfidence = 0.0
    var uiInteractionPerformed = false
    var privateContentPersisted = false

    var status: DiagnosticStatus {
        if !screenRecordingGranted { return .screenRecordingPermissionRequired }
        if !wechatRunning { return .wechatNotRunning }
        if !wechatWindowFound { return .windowNotFound }
        if waitingForVisibleWeChat { return .waitingForVisibleWeChat }
        if !captureSucceeded { return .captureFailed }
        if !ocrSucceeded { return .ocrFailed }
        return .succeeded
    }
}

enum DiagnosticStatus: String, Codable, Sendable {
    case neverRun
    case succeeded
    case screenRecordingPermissionRequired
    case wechatNotRunning
    case windowNotFound
    case waitingForVisibleWeChat
    case captureFailed
    case ocrFailed

    var label: String {
        switch self {
        case .neverRun: "Not Run"
        case .succeeded: "Completed"
        case .screenRecordingPermissionRequired: "Permission Required"
        case .wechatNotRunning: "WeChat Not Running"
        case .windowNotFound: "Window Not Found"
        case .waitingForVisibleWeChat: "Waiting for Visible WeChat"
        case .captureFailed: "Capture Failed"
        case .ocrFailed: "OCR Failed"
        }
    }
}

struct SystemStatus: Equatable, Sendable {
    var wechatInstalled: Bool
    var wechatRunning: Bool
    var screenRecordingGranted: Bool

    static let unknown = SystemStatus(
        wechatInstalled: false,
        wechatRunning: false,
        screenRecordingGranted: false
    )
}
