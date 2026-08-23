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

private struct Point: Codable {
    let x: Double
    let y: Double
}

private struct Size: Codable {
    let width: Double
    let height: Double
}

private struct StructuralElement: Codable {
    let path: String
    let depth: Int
    let role: String
    let subrole: String?
    let childCount: Int
    let titleExists: Bool
    let titleLength: Int?
    let valueExists: Bool
    let valueLength: Int?
    let descriptionExists: Bool
    let descriptionLength: Int?
    let identifier: String?
    let position: Point?
    let size: Size?
    let availableAttributeNames: [String]
}

private struct IdentifierCandidate: Codable {
    let identifier: String
    let role: String
    let path: String
    let childCount: Int
}

private struct TreeStats: Codable {
    var totalElementsInspected = 0
    var maxDepthReached = 0
    var traversalCapped = false
}

private struct StructureResult: Codable {
    var wechatRunning = false
    var accessibilityGranted = false
    var backgroundReadSucceeded = false
    var wechatWasFrontmostBefore = false
    var wechatWasFrontmostAfter = false
    var wechatStillRunning = false
    var windowCount = 0
    var uiInteractionPerformed = false
    var listCandidates: [StructuralElement] = []
    var staticTextCandidates: [StructuralElement] = []
    var nonEmptyIdentifiers: [IdentifierCandidate] = []
    var roleCounts: [String: Int] = [:]
    var treeStats = TreeStats()
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

private func attributeNames(of element: AXUIElement) -> [String] {
    var names: CFArray?
    guard AXUIElementCopyAttributeNames(element, &names) == .success else { return [] }
    return names as? [String] ?? []
}

private func textFacts(_ element: AXUIElement, _ attribute: CFString) -> (exists: Bool, length: Int?) {
    let (error, value) = copyAttribute(element, attribute)
    guard error == .success, let value else { return (false, nil) }
    return (true, (value as? String)?.count)
}

private func pointAttribute(_ element: AXUIElement) -> Point? {
    let (error, rawValue) = copyAttribute(element, kAXPositionAttribute as CFString)
    guard error == .success, let rawValue, CFGetTypeID(rawValue) == AXValueGetTypeID() else { return nil }
    let value = rawValue as! AXValue
    guard AXValueGetType(value) == .cgPoint else { return nil }
    var point = CGPoint.zero
    guard AXValueGetValue(value, .cgPoint, &point) else { return nil }
    return Point(x: point.x, y: point.y)
}

private func sizeAttribute(_ element: AXUIElement) -> Size? {
    let (error, rawValue) = copyAttribute(element, kAXSizeAttribute as CFString)
    guard error == .success, let rawValue, CFGetTypeID(rawValue) == AXValueGetTypeID() else { return nil }
    let value = rawValue as! AXValue
    guard AXValueGetType(value) == .cgSize else { return nil }
    var size = CGSize.zero
    guard AXValueGetValue(value, .cgSize, &size) else { return nil }
    return Size(width: size.width, height: size.height)
}

private func safeRole(_ role: String?) -> String {
    guard let role, role.hasPrefix("AX"), role.unicodeScalars.allSatisfy(\.isASCII) else {
        return "AXUnknown"
    }
    return role
}

private func sanitizedIdentifier(_ identifier: String?) -> String? {
    guard let identifier, !identifier.isEmpty else { return nil }
    let lower = identifier.lowercased()
    if lower.hasPrefix("session_item_") { return "session_item_<redacted>" }
    if lower.contains("wxid") || identifier != lower ||
        !identifier.unicodeScalars.allSatisfy(\.isASCII) {
        return "<redacted>"
    }

    let safeTokens: Set<Substring> = [
        "area", "avatar", "big", "button", "cell", "chat", "container", "content",
        "date", "group", "h", "image", "input", "item", "label", "line", "list",
        "main", "message", "messages", "row", "scroll", "search", "sidebar", "table",
        "text", "time", "title", "toolbar", "v", "view", "window",
    ]
    let tokens = identifier.split(separator: "_", omittingEmptySubsequences: false)
    guard tokens.count > 1, tokens.allSatisfy({ !$0.isEmpty && safeTokens.contains($0) }) else {
        return "<redacted>"
    }
    return identifier
}

private let safeAttributeNames: Set<String> = [
    kAXChildrenAttribute as String,
    kAXDescriptionAttribute as String,
    kAXIdentifierAttribute as String,
    kAXPositionAttribute as String,
    kAXRoleAttribute as String,
    kAXSizeAttribute as String,
    kAXSubroleAttribute as String,
    kAXTitleAttribute as String,
    kAXValueAttribute as String,
]

private func structuralElement(_ element: AXUIElement, path: String, depth: Int,
                               role: String, childCount: Int) -> StructuralElement {
    let title = textFacts(element, kAXTitleAttribute as CFString)
    let value = textFacts(element, kAXValueAttribute as CFString)
    let description = textFacts(element, kAXDescriptionAttribute as CFString)
    return StructuralElement(
        path: path,
        depth: depth,
        role: role,
        subrole: stringAttribute(element, kAXSubroleAttribute as CFString).map { safeRole($0) },
        childCount: childCount,
        titleExists: title.exists,
        titleLength: title.length,
        valueExists: value.exists,
        valueLength: value.length,
        descriptionExists: description.exists,
        descriptionLength: description.length,
        identifier: sanitizedIdentifier(stringAttribute(element, kAXIdentifierAttribute as CFString)),
        position: pointAttribute(element),
        size: sizeAttribute(element),
        availableAttributeNames: attributeNames(of: element).filter(safeAttributeNames.contains).sorted()
    )
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

private func inspectStructure(_ roots: [AXUIElement], result: inout StructureResult) {
    let candidateLimit = 500
    var stack = roots.enumerated().reversed().map { ($0.element, String($0.offset), 0) }

    while let (element, path, depth) = stack.popLast(), result.treeStats.totalElementsInspected < 20_000 {
        result.treeStats.totalElementsInspected += 1
        result.treeStats.maxDepthReached = max(result.treeStats.maxDepthReached, depth)

        let role = safeRole(stringAttribute(element, kAXRoleAttribute as CFString))
        let elementChildren = children(of: element)
        let metadata = structuralElement(element, path: path, depth: depth,
                                         role: role, childCount: elementChildren.count)
        result.roleCounts[role, default: 0] += 1

        if elementChildren.count > 0,
           role == (kAXListRole as String) || role == (kAXScrollAreaRole as String) ||
           role == (kAXGroupRole as String) {
            if result.listCandidates.count < candidateLimit { result.listCandidates.append(metadata) }
        }
        if role == (kAXStaticTextRole as String),
           metadata.identifier != nil || metadata.position != nil || metadata.size != nil {
            if result.staticTextCandidates.count < candidateLimit { result.staticTextCandidates.append(metadata) }
        }
        if let identifier = metadata.identifier, result.nonEmptyIdentifiers.count < candidateLimit {
            result.nonEmptyIdentifiers.append(IdentifierCandidate(
                identifier: identifier,
                role: role,
                path: path,
                childCount: elementChildren.count
            ))
        }

        if depth < 30 {
            for (index, child) in elementChildren.enumerated().reversed() {
                stack.append((child, "\(path)/\(index)", depth + 1))
            }
        } else if !elementChildren.isEmpty {
            result.treeStats.traversalCapped = true
        }
    }

    if !stack.isEmpty {
        result.treeStats.traversalCapped = true
    }
}

private func normalMain() {
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

private func structureMain() {
    var result = StructureResult()
    result.accessibilityGranted = AXIsProcessTrusted()
    result.wechatWasFrontmostBefore = NSWorkspace.shared.frontmostApplication?.bundleIdentifier == weChatBundleID

    guard let app = NSRunningApplication.runningApplications(withBundleIdentifier: weChatBundleID).first else {
        result.notes.append("WeChat is not running")
        finishStructure(&result)
        return
    }
    result.wechatRunning = true

    guard result.accessibilityGranted else {
        result.notes.append("Accessibility permission is not granted")
        finishStructure(&result)
        return
    }

    let axApp = AXUIElementCreateApplication(app.processIdentifier)
    let (windowsError, windowsValue) = copyAttribute(axApp, kAXWindowsAttribute as CFString)
    if windowsError == .success, let windows = windowsValue as? [AXUIElement] {
        result.backgroundReadSucceeded = true
        result.windowCount = windows.count
        inspectStructure(windows, result: &result)
    } else {
        result.notes.append("AX windows were unavailable (error \(windowsError.rawValue))")
    }

    finishStructure(&result)
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

private func finishStructure(_ result: inout StructureResult) {
    result.wechatWasFrontmostAfter = NSWorkspace.shared.frontmostApplication?.bundleIdentifier == weChatBundleID
    result.wechatStillRunning = !NSRunningApplication.runningApplications(withBundleIdentifier: weChatBundleID).isEmpty
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    if let data = try? encoder.encode(result) {
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
    }
}

if CommandLine.arguments.dropFirst() == ["--structure"] {
    structureMain()
} else {
    normalMain()
}
