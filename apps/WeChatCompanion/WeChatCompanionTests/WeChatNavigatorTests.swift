import CoreGraphics
import Foundation
import Testing
@testable import WeChatCompanion

struct WeChatNavigatorTests {
    @Test
    func interactionGeometryStaysInMessageHistory() {
        let frame = CGRect(x: 100, y: 200, width: 1_000, height: 800)
        let point = WeChatNavigator.messageHistoryPoint(in: frame)
        let pixels = WeChatNavigator.messageHistoryPixelRect(width: 1_000, height: 800)

        #expect(WeChatNavigator.isSafeMessageHistoryPoint(point, in: frame))
        #expect(pixels.minX >= 320)
        #expect(pixels.maxX <= 1_000)
        #expect(pixels.maxY <= 640)
    }

    @Test
    func navigatorExposesOnlyScrollEventCreation() throws {
        let projectDirectory = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let source = try String(
            contentsOf: projectDirectory
                .appendingPathComponent("WeChatCompanion/Navigation/WeChatNavigator.swift"),
            encoding: .utf8
        )

        #expect(source.contains("scrollWheelEvent2Source"))
        #expect(!source.contains("keyboardEventSource"))
        #expect(!source.contains("mouseEventSource"))
    }
}
