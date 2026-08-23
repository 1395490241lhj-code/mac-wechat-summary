import AppKit
import ApplicationServices
import CoreGraphics
import ScreenCaptureKit

actor InteractionDiagnosticsService {
    private let navigator = WeChatNavigator()

    func run(requestPermissionIfNeeded: Bool) async -> InteractionDiagnosticResult {
        var result = InteractionDiagnosticResult()
        result.screenRecordingGranted = CGPreflightScreenCaptureAccess()
        result.accessibilityGranted = accessibilityGranted(
            requestPermissionIfNeeded: requestPermissionIfNeeded
        )

        guard let application = WeChatWindowLocator.runningApplication() else {
            return result
        }
        result.wechatRunning = true

        var captureWindow: SCWindow?
        if result.screenRecordingGranted {
            captureWindow = try? await WeChatWindowLocator.largestScreenCaptureWindow(
                for: application.processIdentifier
            )
        }
        let frame = captureWindow?.frame
            ?? WeChatWindowLocator.largestNormalWindowFrame(for: application.processIdentifier)
        result.wechatWindowFound = frame != nil

        guard result.accessibilityGranted,
              result.screenRecordingGranted,
              let frame else { return result }

        let beforeBackground = await captureSignature(captureWindow)
        result.backgroundScrollAttempted = true
        let backgroundPosted = navigator.scrollCurrentChat(
            pid: application.processIdentifier,
            windowFrame: frame,
            direction: .up,
            mode: .backgroundTargeted
        )
        await settle()
        let afterBackground = await captureSignature(captureWindow)

        if let comparison = compare(beforeBackground, afterBackground) {
            result.changedPixelRatio = comparison.changedPixelRatio
            result.imageDifferenceDetected = comparison.changeDetected
            result.backgroundScrollSucceeded = backgroundPosted && comparison.changeDetected
        }

        if result.backgroundScrollSucceeded {
            await restore(
                result: &result,
                application: application,
                frame: frame,
                captureWindow: captureWindow,
                original: beforeBackground,
                mode: .backgroundTargeted
            )
            return result
        }

        if beforeBackground == nil, backgroundPosted {
            result.restoreAttempted = navigator.scrollCurrentChat(
                pid: application.processIdentifier,
                windowFrame: frame,
                direction: .down,
                mode: .backgroundTargeted
            )
            await settle()
        }

        let previouslyFrontmost = NSWorkspace.shared.frontmostApplication
        let activated = application.activate(options: [])
        guard activated else { return result }
        defer {
            if previouslyFrontmost?.processIdentifier != application.processIdentifier {
                previouslyFrontmost?.activate(options: [])
            }
        }

        await settle(milliseconds: 350)
        let beforeForeground = await captureSignature(captureWindow)
        result.foregroundScrollAttempted = true
        let foregroundPosted = navigator.scrollCurrentChat(
            pid: application.processIdentifier,
            windowFrame: frame,
            direction: .up,
            mode: .foregroundGlobal
        )
        await settle()
        let afterForeground = await captureSignature(captureWindow)

        if let comparison = compare(beforeForeground, afterForeground) {
            if comparison.changedPixelRatio > result.changedPixelRatio {
                result.changedPixelRatio = comparison.changedPixelRatio
            }
            result.imageDifferenceDetected = result.imageDifferenceDetected
                || comparison.changeDetected
            result.foregroundScrollSucceeded = foregroundPosted && comparison.changeDetected
        }

        if result.foregroundScrollSucceeded {
            await restore(
                result: &result,
                application: application,
                frame: frame,
                captureWindow: captureWindow,
                original: beforeForeground,
                mode: .foregroundGlobal
            )
        } else if beforeForeground == nil, foregroundPosted {
            result.restoreAttempted = navigator.scrollCurrentChat(
                pid: application.processIdentifier,
                windowFrame: frame,
                direction: .down,
                mode: .foregroundGlobal
            ) || result.restoreAttempted
            await settle()
        }

        return result
    }

    private func accessibilityGranted(requestPermissionIfNeeded: Bool) -> Bool {
        if requestPermissionIfNeeded, !AXIsProcessTrusted() {
            _ = AXIsProcessTrustedWithOptions(
                ["AXTrustedCheckOptionPrompt": true] as CFDictionary
            )
        }
        return AXIsProcessTrusted()
    }

    private func restore(
        result: inout InteractionDiagnosticResult,
        application: NSRunningApplication,
        frame: CGRect,
        captureWindow: SCWindow?,
        original: ImageSignature?,
        mode: WeChatNavigator.DeliveryMode
    ) async {
        result.restoreAttempted = navigator.scrollCurrentChat(
            pid: application.processIdentifier,
            windowFrame: frame,
            direction: .down,
            mode: mode
        )
        await settle()
        guard let comparison = compare(original, await captureSignature(captureWindow)) else {
            return
        }
        result.restoreAppearsSuccessful = comparison.changedPixelRatio < 0.03
    }

    private func settle(milliseconds: Int = 700) async {
        try? await Task.sleep(for: .milliseconds(milliseconds))
    }

    private func captureSignature(_ window: SCWindow?) async -> ImageSignature? {
        guard let window else { return nil }
        let configuration = SCStreamConfiguration()
        configuration.width = 320
        configuration.height = max(1, Int(320 * window.frame.height / window.frame.width))
        configuration.showsCursor = false

        guard let image = try? await SCScreenshotManager.captureImage(
            contentFilter: SCContentFilter(desktopIndependentWindow: window),
            configuration: configuration
        ) else { return nil }

        let cropRect = WeChatNavigator.messageHistoryPixelRect(
            width: image.width,
            height: image.height
        )
        guard let cropped = image.cropping(to: cropRect) else { return nil }

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
            context.draw(cropped, in: CGRect(x: 0, y: 0, width: side, height: side))
            return true
        }
        return rendered ? ImageSignature(pixels: pixels) : nil
    }

    private func compare(
        _ before: ImageSignature?,
        _ after: ImageSignature?
    ) -> ImageComparison? {
        guard let before, let after, before.pixels.count == after.pixels.count else {
            return nil
        }
        let changed = zip(before.pixels, after.pixels).reduce(into: 0) { count, pair in
            if abs(Int(pair.0) - Int(pair.1)) > 12 { count += 1 }
        }
        let ratio = Double(changed) / Double(before.pixels.count)
        return ImageComparison(changedPixelRatio: ratio, changeDetected: ratio >= 0.03)
    }
}

private struct ImageSignature: Equatable {
    let pixels: [UInt8]
}

private struct ImageComparison {
    let changedPixelRatio: Double
    let changeDetected: Bool
}
