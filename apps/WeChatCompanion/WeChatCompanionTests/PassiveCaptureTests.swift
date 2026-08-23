import CoreGraphics
import Foundation
import Testing
@testable import WeChatCompanion

struct PassiveCaptureTests {
    @Test
    func captureModeSelectionPrefersWindowThenDisplayRegion() {
        #expect(
            WeChatCapturePolicy.selectedMode(
                windowCaptureNonEmpty: true,
                displayRegionCaptureNonEmpty: true
            ) == .desktopIndependentWindow
        )
        #expect(
            WeChatCapturePolicy.selectedMode(
                windowCaptureNonEmpty: false,
                displayRegionCaptureNonEmpty: true
            ) == .visibleDisplayRegion
        )
        #expect(
            WeChatCapturePolicy.selectedMode(
                windowCaptureNonEmpty: false,
                displayRegionCaptureNonEmpty: false
            ) == nil
        )
    }

    @Test
    func emptyWindowCaptureFallsBackOnlyWhenVisibleAndFrontmost() {
        #expect(
            WeChatCapturePolicy.shouldAttemptDisplayFallback(
                windowCaptureNonEmpty: false,
                wechatFrontmost: true,
                windowOnScreen: true
            )
        )
        #expect(
            !WeChatCapturePolicy.shouldAttemptDisplayFallback(
                windowCaptureNonEmpty: false,
                wechatFrontmost: false,
                windowOnScreen: true
            )
        )
        #expect(
            !WeChatCapturePolicy.shouldAttemptDisplayFallback(
                windowCaptureNonEmpty: false,
                wechatFrontmost: true,
                windowOnScreen: false
            )
        )
    }

    @Test
    func displayCropConvertsGlobalWindowBoundsToDisplayLocalPoints() {
        let display = CGRect(x: 100, y: 50, width: 1_000, height: 800)
        let window = CGRect(x: 200, y: 150, width: 500, height: 400)
        #expect(
            WeChatCapturePolicy.displaySourceRect(
                windowFrame: window,
                displayFrame: display
            ) == CGRect(x: 100, y: 100, width: 500, height: 400)
        )

        let clipped = CGRect(x: 50, y: 0, width: 200, height: 200)
        #expect(
            WeChatCapturePolicy.displaySourceRect(
                windowFrame: clipped,
                displayFrame: display
            ) == CGRect(x: 0, y: 0, width: 150, height: 150)
        )
    }

    @Test
    func compiledSourceContainsNoActiveControlAPIs() throws {
        let sourceDirectory = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("WeChatCompanion")
        let files = FileManager.default.enumerator(
            at: sourceDirectory,
            includingPropertiesForKeys: nil
        )?.compactMap { $0 as? URL }.filter { $0.pathExtension == "swift" } ?? []
        let source = try files.map { try String(contentsOf: $0, encoding: .utf8) }.joined()

        #expect(!source.contains("CGEvent"))
        #expect(!source.contains("AXUIElement"))
        #expect(!source.contains(".activate("))
    }
}
