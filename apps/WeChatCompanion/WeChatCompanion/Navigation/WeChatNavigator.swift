import AppKit
import ApplicationServices
import CoreGraphics
import ScreenCaptureKit

enum WeChatWindowLocator {
    static let bundleIdentifier = "com.tencent.xinWeChat"

    static func runningApplication() -> NSRunningApplication? {
        NSRunningApplication.runningApplications(
            withBundleIdentifier: bundleIdentifier
        ).first(where: { !$0.isTerminated })
    }

    static func largestScreenCaptureWindow(for pid: pid_t) async throws -> SCWindow? {
        let content = try await SCShareableContent.excludingDesktopWindows(
            false,
            onScreenWindowsOnly: false
        )
        return content.windows
            .filter {
                $0.owningApplication?.processID == pid
                    && $0.windowLayer == 0
                    && $0.isOnScreen
                    && $0.frame.width > 0
                    && $0.frame.height > 0
            }
            .max(by: { $0.frame.width * $0.frame.height < $1.frame.width * $1.frame.height })
    }

    static func largestNormalWindowFrame(for pid: pid_t) -> CGRect? {
        guard let rawWindows = CGWindowListCopyWindowInfo(
            [.optionOnScreenOnly, .excludeDesktopElements],
            kCGNullWindowID
        ) as? [[String: Any]] else { return nil }

        return rawWindows.compactMap { window -> CGRect? in
            guard window[kCGWindowOwnerPID as String] as? Int == Int(pid),
                  window[kCGWindowLayer as String] as? Int == 0,
                  let bounds = window[kCGWindowBounds as String] as? NSDictionary,
                  let frame = CGRect(dictionaryRepresentation: bounds),
                  frame.width >= 320,
                  frame.height >= 240 else { return nil }
            return frame
        }
        .max(by: { $0.width * $0.height < $1.width * $1.height })
    }
}

struct WeChatNavigator {
    enum Direction: Sendable {
        case up
        case down

        var wheelDelta: Int32 { self == .up ? 1 : -1 }

        var opposite: Direction { self == .up ? .down : .up }
    }

    enum DeliveryMode: Sendable {
        case backgroundTargeted
        case foregroundGlobal
    }

    // One conservative geometry heuristic owns every interaction coordinate.
    // The left 32% is treated as navigation and the bottom 20% as the composer.
    static func messageHistoryPoint(in windowFrame: CGRect) -> CGPoint {
        CGPoint(
            x: windowFrame.minX + windowFrame.width * 0.70,
            y: windowFrame.minY + windowFrame.height * 0.46
        )
    }

    static func messageHistoryPixelRect(width: Int, height: Int) -> CGRect {
        CGRect(
            x: Double(width) * 0.32,
            y: Double(height) * 0.10,
            width: Double(width) * 0.66,
            height: Double(height) * 0.68
        ).integral
    }

    static func isSafeMessageHistoryPoint(_ point: CGPoint, in frame: CGRect) -> Bool {
        point.x > frame.minX + frame.width * 0.32
            && point.x < frame.maxX
            && point.y > frame.minY + frame.height * 0.10
            && point.y < frame.minY + frame.height * 0.80
    }

    @discardableResult
    func scrollCurrentChat(
        pid: pid_t,
        windowFrame: CGRect,
        direction: Direction,
        amount: Int32 = 6,
        mode: DeliveryMode
    ) -> Bool {
        guard AXIsProcessTrusted() else { return false }
        let point = Self.messageHistoryPoint(in: windowFrame)
        guard Self.isSafeMessageHistoryPoint(point, in: windowFrame) else { return false }

        let boundedAmount = min(max(abs(amount), 1), 12)
        guard let event = CGEvent(
            scrollWheelEvent2Source: nil,
            units: .line,
            wheelCount: 1,
            wheel1: direction.wheelDelta * boundedAmount,
            wheel2: 0,
            wheel3: 0
        ) else { return false }
        event.location = point

        switch mode {
        case .backgroundTargeted:
            event.postToPid(pid)
        case .foregroundGlobal:
            event.post(tap: .cghidEventTap)
        }
        return true
    }
}
