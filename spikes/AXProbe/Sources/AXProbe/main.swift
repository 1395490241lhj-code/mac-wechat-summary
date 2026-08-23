import AppKit
import ApplicationServices
import Foundation

private let weChatBundleID = "com.tencent.xinWeChat"

private struct Result: Codable {
    var wechatRunning = false
    var accessibilityGranted = false
    var backgroundReadSucceeded = false
    var wechatWasFrontmostBefore = false
    var wechatWasFrontmostAfter = false
    var windowCount = 0
    var currentChatFound = false
    var messagesListFound = false
    var visibleMessageCount = 0
    var messageTextReadable = false
    var senderMetadataFound = false
    var timestampMetadataFound = false
    var uiInteractionPerformed = false
    var wechatStillRunning = false
    var notes: [String] = []
}

private func copyAttribute(_ element: AXUIElement, _ attribute: CFString) -> (AXError, CFTypeRef?) {
    var value: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(element, attribute, &value)
    return (error, value)
}

private func stringAttribute(_ element: AXUIElement, _ attribute: CFString) -> String? {
    let (error, value) = copyAttribute(element, attribute)
    guard error == .success, let string = value as? String, !string.isEmpty else { return nil }
    return string
}

private func children(of element: AXUIElement) -> [AXUIElement] {
    let (error, value) = copyAttribute(element, kAXChildrenAttribute as CFString)
    guard error == .success else { return [] }
    return value as? [AXUIElement] ?? []
}

private struct Metadata {
    let role: String?
    let title: String?
    let identifier: String?
    let value: String?
    let description: String?

    init(_ element: AXUIElement) {
        role = stringAttribute(element, kAXRoleAttribute as CFString)
        title = stringAttribute(element, kAXTitleAttribute as CFString)
        identifier = stringAttribute(element, kAXIdentifierAttribute as CFString)
        value = stringAttribute(element, kAXValueAttribute as CFString)
        description = stringAttribute(element, kAXDescriptionAttribute as CFString)
    }

    var strings: [String] { [title, value, description].compactMap { $0 } }
}

private func containsTimestamp(_ text: String) -> Bool {
    text.range(of: #"\b(?:[01]?\d|2[0-3]):[0-5]\d\b|\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b"#,
               options: .regularExpression) != nil
}

private func inspectMessageTree(_ roots: [AXUIElement]) -> (text: Bool, sender: Bool, timestamp: Bool) {
    var stack = roots.map { ($0, 0) }
    var inspected = 0
    var textFound = false
    var senderFound = false
    var timestampFound = false

    while let (element, depth) = stack.popLast(), inspected < 10_000 {
        inspected += 1
        let metadata = Metadata(element)
        let identifier = metadata.identifier?.lowercased() ?? ""
        let role = metadata.role ?? ""

        if !metadata.strings.isEmpty { textFound = true }
        if identifier.contains("sender") || identifier.contains("avatar") ||
            identifier.contains("username") || identifier.contains("nickname") {
            senderFound = !metadata.strings.isEmpty || role == (kAXImageRole as String)
        }
        if identifier.contains("time") || identifier.contains("date") ||
            metadata.strings.contains(where: containsTimestamp) {
            timestampFound = true
        }

        if depth < 30 {
            stack.append(contentsOf: children(of: element).map { ($0, depth + 1) })
        }
    }
    return (textFound, senderFound, timestampFound)
}

private func inspect(_ roots: [AXUIElement], result: inout Result) {
    var stack = roots.map { ($0, 0) }
    var inspected = 0

    while let (element, depth) = stack.popLast(), inspected < 20_000 {
        inspected += 1
        let metadata = Metadata(element)

        if metadata.identifier == "big_title_line_h_view", !metadata.strings.isEmpty {
            result.currentChatFound = true
        }

        let labels = metadata.strings.map { $0.lowercased() }
        if metadata.role == (kAXListRole as String), labels.contains("messages") {
            result.messagesListFound = true
            let visibleMessages = children(of: element)
            result.visibleMessageCount = visibleMessages.count
            let findings = inspectMessageTree(visibleMessages)
            result.messageTextReadable = findings.text
            result.senderMetadataFound = findings.sender
            result.timestampMetadataFound = findings.timestamp
        }

        if depth < 30 {
            stack.append(contentsOf: children(of: element).map { ($0, depth + 1) })
        }
    }

    if inspected == 20_000 {
        result.notes.append("AX traversal stopped at the safety limit")
    }
}

private func main() {
    var result = Result()
    result.accessibilityGranted = AXIsProcessTrusted()
    result.wechatWasFrontmostBefore = NSWorkspace.shared.frontmostApplication?.bundleIdentifier == weChatBundleID

    guard let app = NSRunningApplication.runningApplications(withBundleIdentifier: weChatBundleID).first else {
        result.notes.append("WeChat is not running")
        finish(&result)
        return
    }
    result.wechatRunning = true

    guard result.accessibilityGranted else {
        result.notes.append("Accessibility permission is not granted")
        finish(&result)
        return
    }

    let axApp = AXUIElementCreateApplication(app.processIdentifier)
    let (windowsError, windowsValue) = copyAttribute(axApp, kAXWindowsAttribute as CFString)
    if windowsError == .success, let windows = windowsValue as? [AXUIElement] {
        result.backgroundReadSucceeded = true
        result.windowCount = windows.count
        inspect(windows, result: &result)
    } else {
        result.notes.append("AX windows were unavailable (error \(windowsError.rawValue))")
    }

    finish(&result)
}

private func finish(_ result: inout Result) {
    result.wechatWasFrontmostAfter = NSWorkspace.shared.frontmostApplication?.bundleIdentifier == weChatBundleID
    result.wechatStillRunning = !NSRunningApplication.runningApplications(withBundleIdentifier: weChatBundleID).isEmpty
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    if let data = try? encoder.encode(result) {
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
    }
}

main()
