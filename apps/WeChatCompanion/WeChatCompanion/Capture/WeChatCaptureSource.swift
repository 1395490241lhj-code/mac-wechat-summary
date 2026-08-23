import AppKit
import CoreGraphics
import Foundation
import ScreenCaptureKit

enum CaptureMode: String, Codable, Sendable {
    case desktopIndependentWindow
    case visibleDisplayRegion

    var label: String {
        switch self {
        case .desktopIndependentWindow: "Window"
        case .visibleDisplayRegion: "Visible Display Region"
        }
    }
}

struct CaptureFrame: Sendable {
    let image: CGImage
    let mode: CaptureMode
    let timestamp: Date
}

struct WeChatCaptureOutcome: Sendable {
    var frame: CaptureFrame?
    var wechatRunning = false
    var wechatFrontmost = false
    var windowFound = false
    var windowOnScreen = false
    var windowCaptureAttempted = false
    var windowCaptureNonEmpty = false
    var displayRegionCaptureAttempted = false
    var displayRegionCaptureNonEmpty = false
    var waitingForVisibleWeChat = false
}

enum WeChatCapturePolicy {
    static func shouldAttemptDisplayFallback(
        windowCaptureNonEmpty: Bool,
        wechatFrontmost: Bool,
        windowOnScreen: Bool
    ) -> Bool {
        !windowCaptureNonEmpty && wechatFrontmost && windowOnScreen
    }

    static func selectedMode(
        windowCaptureNonEmpty: Bool,
        displayRegionCaptureNonEmpty: Bool
    ) -> CaptureMode? {
        if windowCaptureNonEmpty { return .desktopIndependentWindow }
        if displayRegionCaptureNonEmpty { return .visibleDisplayRegion }
        return nil
    }

    static func displaySourceRect(windowFrame: CGRect, displayFrame: CGRect) -> CGRect? {
        let intersection = windowFrame.intersection(displayFrame)
        guard !intersection.isNull, intersection.width > 0, intersection.height > 0 else {
            return nil
        }
        return CGRect(
            x: intersection.minX - displayFrame.minX,
            y: intersection.minY - displayFrame.minY,
            width: intersection.width,
            height: intersection.height
        )
    }
}

enum CaptureImageValidator {
    static func appearsNonEmpty(_ image: CGImage) -> Bool {
        let side = 64
        var pixels = [UInt8](repeating: 0, count: side * side * 4)
        let rendered = pixels.withUnsafeMutableBytes { bytes -> Bool in
            guard let baseAddress = bytes.baseAddress,
                  let context = CGContext(
                    data: baseAddress,
                    width: side,
                    height: side,
                    bitsPerComponent: 8,
                    bytesPerRow: side * 4,
                    space: CGColorSpaceCreateDeviceRGB(),
                    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
                  ) else { return false }
            context.interpolationQuality = .low
            context.draw(image, in: CGRect(x: 0, y: 0, width: side, height: side))
            return true
        }
        guard rendered else { return false }

        var grayscale = [UInt8](repeating: 0, count: side * side)
        var opaquePixels = 0
        for index in grayscale.indices {
            let pixel = index * 4
            let alpha = pixels[pixel + 3]
            if alpha > 8 { opaquePixels += 1 }
            grayscale[index] = UInt8(
                (77 * Int(pixels[pixel])
                    + 150 * Int(pixels[pixel + 1])
                    + 29 * Int(pixels[pixel + 2])) >> 8
            )
        }

        let values = grayscale.map(Double.init)
        let mean = values.reduce(0, +) / Double(values.count)
        let variance = values.reduce(0) { $0 + ($1 - mean) * ($1 - mean) }
            / Double(values.count)
        let range = Int(grayscale.max() ?? 0) - Int(grayscale.min() ?? 0)
        var edges = 0
        for y in 1..<side {
            for x in 1..<side {
                let index = y * side + x
                if abs(Int(grayscale[index]) - Int(grayscale[index - 1])) > 10
                    || abs(Int(grayscale[index]) - Int(grayscale[index - side])) > 10 {
                    edges += 1
                }
            }
        }
        let alphaCoverage = Double(opaquePixels) / Double(grayscale.count)
        let edgeDensity = Double(edges) / Double((side - 1) * (side - 1))
        return alphaCoverage > 0.5
            && range > 8
            && (variance > 4 || edgeDensity > 0.005)
    }
}

actor WeChatCaptureSource {
    func captureCurrentVisibleWeChat() async throws -> WeChatCaptureOutcome {
        var outcome = WeChatCaptureOutcome()
        guard let application = WeChatWindowLocator.runningApplication() else { return outcome }
        outcome.wechatRunning = true
        outcome.wechatFrontmost = NSWorkspace.shared.frontmostApplication?.processIdentifier
            == application.processIdentifier

        let content = try await WeChatWindowLocator.shareableContent()
        guard let window = WeChatWindowLocator.largestWindow(
            for: application.processIdentifier,
            in: content
        ) else { return outcome }
        outcome.windowFound = true
        outcome.windowOnScreen = window.isOnScreen

        outcome.windowCaptureAttempted = true
        let windowFilter = SCContentFilter(desktopIndependentWindow: window)
        if let image = try? await capture(
            filter: windowFilter,
            sourceRect: nil,
            pointPixelScale: CGFloat(windowFilter.pointPixelScale)
        ), CaptureImageValidator.appearsNonEmpty(image) {
            outcome.windowCaptureNonEmpty = true
            outcome.frame = CaptureFrame(
                image: image,
                mode: .desktopIndependentWindow,
                timestamp: Date()
            )
            return outcome
        }

        guard WeChatCapturePolicy.shouldAttemptDisplayFallback(
            windowCaptureNonEmpty: false,
            wechatFrontmost: outcome.wechatFrontmost,
            windowOnScreen: outcome.windowOnScreen
        ) else {
            outcome.waitingForVisibleWeChat = true
            return outcome
        }

        guard let display = content.displays.max(by: {
            $0.frame.intersection(window.frame).area < $1.frame.intersection(window.frame).area
        }),
              let sourceRect = WeChatCapturePolicy.displaySourceRect(
                windowFrame: window.frame,
                displayFrame: display.frame
              ) else { return outcome }

        outcome.displayRegionCaptureAttempted = true
        let displayFilter = SCContentFilter(display: display, excludingWindows: [])
        if let image = try? await capture(
            filter: displayFilter,
            sourceRect: sourceRect,
            pointPixelScale: CGFloat(displayFilter.pointPixelScale)
        ), CaptureImageValidator.appearsNonEmpty(image) {
            outcome.displayRegionCaptureNonEmpty = true
            outcome.frame = CaptureFrame(
                image: image,
                mode: .visibleDisplayRegion,
                timestamp: Date()
            )
        }
        return outcome
    }

    private func capture(
        filter: SCContentFilter,
        sourceRect: CGRect?,
        pointPixelScale: CGFloat
    ) async throws -> CGImage {
        let rect = sourceRect ?? filter.contentRect
        let configuration = SCStreamConfiguration()
        if let sourceRect { configuration.sourceRect = sourceRect }
        configuration.width = max(1, Int(ceil(rect.width * pointPixelScale)))
        configuration.height = max(1, Int(ceil(rect.height * pointPixelScale)))
        configuration.showsCursor = false
        return try await SCScreenshotManager.captureImage(
            contentFilter: filter,
            configuration: configuration
        )
    }
}

private extension CGRect {
    var area: CGFloat { isNull ? 0 : width * height }
}
