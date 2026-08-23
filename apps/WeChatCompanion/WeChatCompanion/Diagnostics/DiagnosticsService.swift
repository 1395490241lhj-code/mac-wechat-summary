import CoreGraphics
import Foundation
import Vision

actor DiagnosticsService {
    private let captureSource: WeChatCaptureSource

    init(captureSource: WeChatCaptureSource = WeChatCaptureSource()) {
        self.captureSource = captureSource
    }

    func currentSystemStatus() -> SystemStatus {
        SystemStatus(
            wechatInstalled: FileManager.default.fileExists(atPath: "/Applications/WeChat.app"),
            wechatRunning: WeChatWindowLocator.runningApplication() != nil,
            screenRecordingGranted: CGPreflightScreenCaptureAccess()
        )
    }

    func run(requestPermissionIfNeeded: Bool) async -> DiagnosticResult {
        var result = DiagnosticResult()
        result.screenRecordingGranted = CGPreflightScreenCaptureAccess()
        if !result.screenRecordingGranted, requestPermissionIfNeeded {
            result.screenRecordingGranted = CGRequestScreenCaptureAccess()
        }

        guard result.screenRecordingGranted else {
            return result
        }

        do {
            let outcome = try await captureSource.captureCurrentVisibleWeChat()
            result.wechatRunning = outcome.wechatRunning
            result.wechatFrontmost = outcome.wechatFrontmost
            result.wechatWindowFound = outcome.windowFound
            result.windowOnScreen = outcome.windowOnScreen
            result.windowCaptureAttempted = outcome.windowCaptureAttempted
            result.windowCaptureNonEmpty = outcome.windowCaptureNonEmpty
            result.displayRegionCaptureAttempted = outcome.displayRegionCaptureAttempted
            result.displayRegionCaptureNonEmpty = outcome.displayRegionCaptureNonEmpty
            result.waitingForVisibleWeChat = outcome.waitingForVisibleWeChat

            guard let frame = outcome.frame else { return result }
            result.selectedCaptureMode = frame.mode
            result.captureSucceeded = true
            result.captureWidth = frame.image.width
            result.captureHeight = frame.image.height
            result.imageAppearsNonEmpty = true

            let ocr = try recognizeTextMetrics(in: frame.image)
            result.ocrSucceeded = true
            result.recognizedTextObservationCount = ocr.observationCount
            result.totalRecognizedCharacterCount = ocr.characterCount
            result.averageConfidence = ocr.averageConfidence
        } catch {
            // Failure state is represented by the aggregate booleans above.
        }
        return result
    }

    private func recognizeTextMetrics(in image: CGImage) throws -> OCRMetrics {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = true
        let supported = try request.supportedRecognitionLanguages()
        let preferred = ["zh-Hans", "en-US"].filter(supported.contains)
        if !preferred.isEmpty { request.recognitionLanguages = preferred }

        try VNImageRequestHandler(cgImage: image).perform([request])
        let observations = request.results ?? []
        var characterCount = 0
        var confidenceTotal = 0.0
        var confidenceCount = 0

        for observation in observations {
            guard let candidate = observation.topCandidates(1).first else { continue }
            // The recognized string is transient; only its count leaves this scope.
            characterCount += candidate.string.count
            confidenceTotal += Double(candidate.confidence)
            confidenceCount += 1
        }

        return OCRMetrics(
            observationCount: observations.count,
            characterCount: characterCount,
            averageConfidence: confidenceCount == 0
                ? 0
                : confidenceTotal / Double(confidenceCount)
        )
    }
}

private struct OCRMetrics {
    let observationCount: Int
    let characterCount: Int
    let averageConfidence: Double
}
