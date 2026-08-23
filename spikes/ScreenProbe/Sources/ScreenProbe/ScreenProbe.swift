import AppKit
import CoreGraphics
import Foundation
import ScreenCaptureKit
import Vision

private let weChatBundleID = "com.tencent.xinWeChat"

private struct ProbeResult: Codable {
    var screenRecordingGranted = false
    var wechatRunning = false
    var wechatWindowFound = false
    var owningPID: Int?
    var windowID: UInt32?
    var windowFrameWidth = 0.0
    var windowFrameHeight = 0.0
    var windowIsOnScreen = false
    var frontmostPIDBefore: Int?
    var frontmostPIDAfter: Int?
    var wechatWasFrontmostBefore = false
    var wechatWasFrontmostAfter = false
    var captureSucceeded = false
    var captureWidth = 0
    var captureHeight = 0
    var imageAppearsNonEmpty = false
    var brightnessVariance = 0.0
    var ocrSucceeded = false
    var recognizedTextObservationCount = 0
    var totalRecognizedCharacterCount = 0
    var nonEmptyTextObservationCount = 0
    var averageConfidence = 0.0
    var boundingBoxCount = 0
    var uiInteractionPerformed = false
    var privateContentPersisted = false
    var notes: [String] = []
}

private func imageMetrics(_ image: CGImage) -> (appearsNonEmpty: Bool, variance: Double) {
    let side = 64
    var pixels = [UInt8](repeating: 0, count: side * side)
    let drewImage = pixels.withUnsafeMutableBytes { bytes -> Bool in
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
    guard drewImage else { return (false, 0) }

    let values = pixels.map(Double.init)
    let mean = values.reduce(0, +) / Double(values.count)
    let variance = values.reduce(0) { $0 + ($1 - mean) * ($1 - mean) } / Double(values.count)
    let range = Int(pixels.max() ?? 0) - Int(pixels.min() ?? 0)
    return (range > 4 && variance > 1, variance)
}

private func performOCR(_ image: CGImage, result: inout ProbeResult) {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    if let supported = try? request.supportedRecognitionLanguages() {
        let preferred = ["zh-Hans", "en-US"].filter(supported.contains)
        if !preferred.isEmpty { request.recognitionLanguages = preferred }
    }

    do {
        try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
        let observations = request.results ?? []
        result.recognizedTextObservationCount = observations.count
        var confidenceTotal = 0.0
        var confidenceCount = 0

        for observation in observations {
            let box = observation.boundingBox
            if box.width > 0 && box.height > 0 { result.boundingBoxCount += 1 }
            guard let candidate = observation.topCandidates(1).first else { continue }
            // Privacy: only the transient string length leaves this scope.
            let characterCount = candidate.string.count
            result.totalRecognizedCharacterCount += characterCount
            if characterCount > 0 { result.nonEmptyTextObservationCount += 1 }
            confidenceTotal += Double(candidate.confidence)
            confidenceCount += 1
        }

        if confidenceCount > 0 {
            result.averageConfidence = confidenceTotal / Double(confidenceCount)
        }
        result.ocrSucceeded = true
    } catch {
        result.notes.append("Vision OCR failed")
    }
}

private func finish(_ result: inout ProbeResult) {
    let frontmost = NSWorkspace.shared.frontmostApplication
    result.frontmostPIDAfter = frontmost.map { Int($0.processIdentifier) }
    result.wechatWasFrontmostAfter = frontmost?.bundleIdentifier == weChatBundleID
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    if let data = try? encoder.encode(result) {
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
    }
}

@main
private struct ScreenProbe {
    static func main() async {
        var result = ProbeResult()
        let frontmost = NSWorkspace.shared.frontmostApplication
        result.frontmostPIDBefore = frontmost.map { Int($0.processIdentifier) }
        result.wechatWasFrontmostBefore = frontmost?.bundleIdentifier == weChatBundleID
        result.wechatRunning = !NSRunningApplication
            .runningApplications(withBundleIdentifier: weChatBundleID).isEmpty

        result.screenRecordingGranted = CGPreflightScreenCaptureAccess() || CGRequestScreenCaptureAccess()
        guard result.screenRecordingGranted else {
            result.notes.append("Screen Recording permission is not granted")
            finish(&result)
            return
        }

        let content: SCShareableContent
        do {
            content = try await SCShareableContent.excludingDesktopWindows(
                false,
                onScreenWindowsOnly: false
            )
        } catch {
            result.notes.append("ScreenCaptureKit window discovery failed")
            finish(&result)
            return
        }

        let windows = content.windows.filter { window in
            window.owningApplication?.bundleIdentifier == weChatBundleID &&
                window.windowLayer == 0 && window.isOnScreen &&
                window.frame.width > 0 && window.frame.height > 0
        }
        guard let window = windows.max(by: {
            $0.frame.width * $0.frame.height < $1.frame.width * $1.frame.height
        }) else {
            result.notes.append("No visible normal WeChat window was found")
            finish(&result)
            return
        }

        result.wechatWindowFound = true
        result.owningPID = window.owningApplication.map { Int($0.processID) }
        result.windowID = window.windowID
        result.windowFrameWidth = Double(window.frame.width)
        result.windowFrameHeight = Double(window.frame.height)
        result.windowIsOnScreen = window.isOnScreen

        let configuration = SCStreamConfiguration()
        configuration.width = max(1, Int(window.frame.width))
        configuration.height = max(1, Int(window.frame.height))
        configuration.showsCursor = false
        let filter = SCContentFilter(desktopIndependentWindow: window)

        do {
            let image = try await SCScreenshotManager.captureImage(
                contentFilter: filter,
                configuration: configuration
            )
            result.captureSucceeded = true
            result.captureWidth = image.width
            result.captureHeight = image.height
            let metrics = imageMetrics(image)
            result.imageAppearsNonEmpty = metrics.appearsNonEmpty
            result.brightnessVariance = metrics.variance
            performOCR(image, result: &result)
        } catch {
            result.notes.append("ScreenCaptureKit capture failed")
        }

        finish(&result)
    }
}
