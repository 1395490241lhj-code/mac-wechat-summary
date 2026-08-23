import Foundation

struct InteractionDiagnosticResult: Codable, Equatable, Sendable {
    var timestamp = Date()
    var accessibilityGranted = false
    var screenRecordingGranted = false
    var wechatRunning = false
    var wechatWindowFound = false
    var backgroundScrollAttempted = false
    var backgroundScrollSucceeded = false
    var foregroundScrollAttempted = false
    var foregroundScrollSucceeded = false
    var imageDifferenceDetected = false
    var changedPixelRatio = 0.0
    var restoreAttempted = false
    var restoreAppearsSuccessful = false
    var messageWasSent = false
    var printableKeyboardInputPerformed = false
    var databaseAccessPerformed = false
    var privateContentPersisted = false

    var status: InteractionDiagnosticStatus {
        if !accessibilityGranted { return .accessibilityPermissionRequired }
        if !wechatRunning { return .wechatNotRunning }
        if !wechatWindowFound { return .windowNotFound }
        if !screenRecordingGranted { return .visualVerificationUnavailable }
        if !backgroundScrollSucceeded && !foregroundScrollSucceeded { return .noChangeDetected }
        if !restoreAppearsSuccessful { return .restoreNotVerified }
        return .succeeded
    }
}

enum InteractionDiagnosticStatus: String, Codable, Sendable {
    case neverRun
    case succeeded
    case accessibilityPermissionRequired
    case wechatNotRunning
    case windowNotFound
    case visualVerificationUnavailable
    case noChangeDetected
    case restoreNotVerified

    var label: String {
        switch self {
        case .neverRun: "Not Run"
        case .succeeded: "Completed"
        case .accessibilityPermissionRequired: "Accessibility Required"
        case .wechatNotRunning: "WeChat Not Running"
        case .windowNotFound: "Window Not Found"
        case .visualVerificationUnavailable: "Visual Verification Unavailable"
        case .noChangeDetected: "No Scroll Detected"
        case .restoreNotVerified: "Restore Not Verified"
        }
    }
}
