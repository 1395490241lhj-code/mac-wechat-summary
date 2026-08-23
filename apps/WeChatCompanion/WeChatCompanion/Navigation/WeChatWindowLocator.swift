import AppKit
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
