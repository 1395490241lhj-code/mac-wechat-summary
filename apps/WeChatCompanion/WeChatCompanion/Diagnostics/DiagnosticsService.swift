import AppKit
import CoreGraphics
import Foundation
import ScreenCaptureKit
import Vision

actor DiagnosticsService {
    func currentSystemStatus() -> SystemStatus {
        SystemStatus(
            wechatInstalled: FileManager.default.fileExists(atPath: "/Applications/WeChat.app"),
            wechatRunning: WeChatWindowLocator.runningApplication() != nil,
            screenRecordingGranted: CGPreflightScreenCaptureAccess()
        )
    }

    func run(requestPermissionIfNeeded: Bool) async -> DiagnosticResult {
        var result = DiagnosticResult()
        result.wechatWasFrontmostBefore = isWeChatFrontmost
        let application = WeChatWindowLocator.runningApplication()
        result.wechatRunning = application != nil

        result.screenRecordingGranted = CGPreflightScreenCaptureAccess()
        if !result.screenRecordingGranted, requestPermissionIfNeeded {
            result.screenRecordingGranted = CGRequestScreenCaptureAccess()
        }

        guard result.screenRecordingGranted else {
            result.wechatWasFrontmostAfter = isWeChatFrontmost
            return result
        }

        do {
            guard let application,
                  let window = try await WeChatWindowLocator.largestScreenCaptureWindow(
                    for: application.processIdentifier
                  ) else {
                result.wechatWasFrontmostAfter = isWeChatFrontmost
                return result
            }

            result.wechatWindowFound = true
            let configuration = SCStreamConfiguration()
            configuration.width = max(1, Int(window.frame.width))
            configuration.height = max(1, Int(window.frame.height))
            configuration.showsCursor = false

            let image = try await SCScreenshotManager.captureImage(
                contentFilter: SCContentFilter(desktopIndependentWindow: window),
                configuration: configuration
            )
            result.captureSucceeded = true
            result.captureWidth = image.width
            result.captureHeight = image.height
            result.imageAppearsNonEmpty = imageAppearsNonEmpty(image)

            let ocr = try recognizeTextMetrics(in: image)
            result.ocrSucceeded = true
            result.recognizedTextObservationCount = ocr.observationCount
            result.totalRecognizedCharacterCount = ocr.characterCount
            result.averageConfidence = ocr.averageConfidence
        } catch {
            // Failure state is represented by the aggregate booleans above.
        }

        result.wechatWasFrontmostAfter = isWeChatFrontmost
        return result
    }

    private var isWeChatFrontmost: Bool {
        NSWorkspace.shared.frontmostApplication?.bundleIdentifier
            == WeChatWindowLocator.bundleIdentifier
    }

    private func imageAppearsNonEmpty(_ image: CGImage) -> Bool {
        let side = 64
        var pixels = [UInt8](repeating: 0, count: side * side)
        let rendered = pixels.withUnsafeMutableBytes { bytes -> Bool in
            guard let baseAddress = bytes.baseAddress,
                  let context = CGContext(
                    data: baseAddress,
                    width: side,
                    height: side,
                    bitsPerComponent: 8,
                    bytesPerRow: side,
                    space: CGColorSpaceCreateDeviceGray(),
                    bitmapInfo: CGImageAlphaInfo.none.rawValue
                  ) else { return false }
            context.interpolationQuality = .low
            context.draw(image, in: CGRect(x: 0, y: 0, width: side, height: side))
            return true
        }
        guard rendered else { return false }

        let values = pixels.map(Double.init)
        let mean = values.reduce(0, +) / Double(values.count)
        let variance = values.reduce(0) { $0 + ($1 - mean) * ($1 - mean) }
            / Double(values.count)
        let range = Int(pixels.max() ?? 0) - Int(pixels.min() ?? 0)
        return range > 4 && variance > 1
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
