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

    static func shareableContent() async throws -> SCShareableContent {
        try await SCShareableContent.excludingDesktopWindows(
            false,
            onScreenWindowsOnly: false
        )
    }

    static func largestWindow(for pid: pid_t, in content: SCShareableContent) -> SCWindow? {
        content.windows
            .filter {
                $0.owningApplication?.processID == pid
                    && $0.windowLayer == 0
                    && $0.frame.width >= 320
                    && $0.frame.height >= 240
            }
            .max(by: { $0.frame.width * $0.frame.height < $1.frame.width * $1.frame.height })
    }

}
