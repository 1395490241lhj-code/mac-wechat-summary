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

private struct ProcessCandidate: Codable {
    let pid: Int
    let name: String
    let bundleIdentifier: String
    let executable: String
    let isActive: Bool
    let isHidden: Bool
    let isTerminated: Bool
    let isFrontmost: Bool
    let windowCount: Int
    let totalElements: Int
    let maxDepth: Int
    let traversalCapped: Bool
    let roleCounts: [String: Int]
    let listCount: Int
    let scrollAreaCount: Int
    let groupCount: Int
    let staticTextCount: Int
    let identifierCount: Int
    let largestChildCount: Int
    let richnessScore: Int
}

private struct ProcessesResult: Encodable {
    let accessibilityGranted: Bool
    let frontmostPID: Int?
    let richnessFormula = "totalElements + 50*listCount + 25*scrollAreaCount + 2*staticTextCount"
    var candidates: [ProcessCandidate]

    private enum CodingKeys: String, CodingKey {
        case accessibilityGranted, frontmostPID, richnessFormula, candidates
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(accessibilityGranted, forKey: .accessibilityGranted)
        if let frontmostPID {
            try container.encode(frontmostPID, forKey: .frontmostPID)
        } else {
            try container.encodeNil(forKey: .frontmostPID)
        }
        try container.encode(richnessFormula, forKey: .richnessFormula)
        try container.encode(candidates, forKey: .candidates)
    }
}

private struct ProcessAXStats {
    var totalElements = 0
    var maxDepth = 0
    var traversalCapped = false
    var roleCounts: [String: Int] = [:]
    var listCount = 0
    var scrollAreaCount = 0
    var groupCount = 0
    var staticTextCount = 0
    var identifierCount = 0
    var largestChildCount = 0

    var richnessScore: Int {
        totalElements + 50 * listCount + 25 * scrollAreaCount + 2 * staticTextCount
    }
}

private struct AXGraphSummary: Codable {
    var elementCount = 0
    var maxDepth = 0
    var roleCounts: [String: Int] = [:]
    var windowCount = 0
    var groupCount = 0
    var listCount = 0
    var scrollAreaCount = 0
    var staticTextCount = 0
    var textAreaCount = 0
    var webAreaCount = 0
    var imageCount = 0
    var buttonCount = 0
    var identifierCount = 0
    var largestOutboundRelationshipCount = 0
    var traversalCapped = false
}

private struct RelationshipAttributeSummary: Codable {
    let attribute: String
    var elementOccurrences = 0
    var scalarElementReferences = 0
    var arrayOccurrences = 0
    var totalElementReferences = 0
    var maxReferencesOnOneElement = 0
}

private struct RelationshipsResult: Codable {
    let accessibilityGranted: Bool
    let targetPID: Int?
    let targetName: String?
    let targetBundleIdentifier: String?
    let targetExecutable: String?
    let targetIsFrontmost: Bool
    let childrenOnlyElementCount: Int
    let relationshipGraphElementCount: Int
    let additionalElementsDiscovered: Int
    let childrenTraversal: AXGraphSummary?
    let relationshipTraversal: AXGraphSummary?
    let relationshipAttributes: [RelationshipAttributeSummary]
    let notes: [String]
}

private struct AXElementSet {
    private var buckets: [CFHashCode: [AXUIElement]] = [:]
    private(set) var count = 0

    mutating func insert(_ element: AXUIElement) -> Bool {
        let hash = CFHash(element)
        if buckets[hash]?.contains(where: { CFEqual($0, element) }) == true { return false }
        buckets[hash, default: []].append(element)
        count += 1
        return true
    }
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

private func inspectProcessAX(_ root: AXUIElement) -> ProcessAXStats {
    var stats = ProcessAXStats()
    var stack = [(root, 0)]

    while let (element, depth) = stack.popLast(), stats.totalElements < 20_000 {
        stats.totalElements += 1
        stats.maxDepth = max(stats.maxDepth, depth)

        let role = safeRole(stringAttribute(element, kAXRoleAttribute as CFString))
        let elementChildren = children(of: element)
        stats.roleCounts[role, default: 0] += 1
        stats.largestChildCount = max(stats.largestChildCount, elementChildren.count)
        if role == (kAXListRole as String) { stats.listCount += 1 }
        if role == (kAXScrollAreaRole as String) { stats.scrollAreaCount += 1 }
        if role == (kAXGroupRole as String) { stats.groupCount += 1 }
        if role == (kAXStaticTextRole as String) { stats.staticTextCount += 1 }
        if sanitizedIdentifier(stringAttribute(element, kAXIdentifierAttribute as CFString)) != nil {
            stats.identifierCount += 1
        }

        if depth < 30 {
            stack.append(contentsOf: elementChildren.reversed().map { ($0, depth + 1) })
        } else if !elementChildren.isEmpty {
            stats.traversalCapped = true
        }
    }

    if !stack.isEmpty { stats.traversalCapped = true }
    return stats
}

private func safeRelationshipAttributeName(_ name: String) -> String {
    guard name.hasPrefix("AX"), name.count <= 128,
          name.unicodeScalars.allSatisfy({ $0.isASCII &&
              (CharacterSet.alphanumerics.contains($0) || $0 == "_") }) else {
        return "<redacted>"
    }
    return name
}

private func relationshipReferences(in value: CFTypeRef) ->
    (references: [AXUIElement], scalar: Bool, array: Bool) {
    if CFGetTypeID(value) == AXUIElementGetTypeID() {
        return ([value as! AXUIElement], true, false)
    }
    guard CFGetTypeID(value) == CFArrayGetTypeID(), let values = value as? [Any] else {
        return ([], false, false)
    }

    let references = values.compactMap { item -> AXUIElement? in
        let object = item as AnyObject
        guard CFGetTypeID(object) == AXUIElementGetTypeID() else { return nil }
        return (object as! AXUIElement)
    }
    return (references, false, !references.isEmpty)
}

private func recordGraphElement(_ element: AXUIElement, depth: Int, outboundCount: Int,
                                summary: inout AXGraphSummary) {
    let role = safeRole(stringAttribute(element, kAXRoleAttribute as CFString))
    summary.elementCount += 1
    summary.maxDepth = max(summary.maxDepth, depth)
    summary.roleCounts[role, default: 0] += 1
    summary.largestOutboundRelationshipCount = max(summary.largestOutboundRelationshipCount,
                                                   outboundCount)
    if role == (kAXWindowRole as String) { summary.windowCount += 1 }
    if role == (kAXGroupRole as String) { summary.groupCount += 1 }
    if role == (kAXListRole as String) { summary.listCount += 1 }
    if role == (kAXScrollAreaRole as String) { summary.scrollAreaCount += 1 }
    if role == (kAXStaticTextRole as String) { summary.staticTextCount += 1 }
    if role == (kAXTextAreaRole as String) { summary.textAreaCount += 1 }
    if role == "AXWebArea" { summary.webAreaCount += 1 }
    if role == (kAXImageRole as String) { summary.imageCount += 1 }
    if role == (kAXButtonRole as String) { summary.buttonCount += 1 }
    if stringAttribute(element, kAXIdentifierAttribute as CFString) != nil {
        summary.identifierCount += 1
    }
}

private func childrenGraph(from root: AXUIElement) -> AXGraphSummary {
    var summary = AXGraphSummary()
    var seen = AXElementSet()
    _ = seen.insert(root)
    var stack = [(root, 0)]

    while let (element, depth) = stack.popLast(), summary.elementCount < 20_000 {
        let elementChildren = children(of: element)
        recordGraphElement(element, depth: depth, outboundCount: elementChildren.count,
                           summary: &summary)

        if depth == 30 {
            for child in elementChildren where seen.insert(child) {
                summary.traversalCapped = true
            }
            continue
        }
        for child in elementChildren.reversed() {
            if seen.count >= 20_000 {
                summary.traversalCapped = true
            } else if seen.insert(child) {
                stack.append((child, depth + 1))
            }
        }
    }
    if !stack.isEmpty { summary.traversalCapped = true }
    return summary
}

private func relationshipGraph(from root: AXUIElement) ->
    (summary: AXGraphSummary, attributes: [RelationshipAttributeSummary]) {
    var summary = AXGraphSummary()
    var attributeStats: [String: RelationshipAttributeSummary] = [:]
    var seen = AXElementSet()
    _ = seen.insert(root)
    var stack = [(root, 0)]

    while let (element, depth) = stack.popLast(), summary.elementCount < 20_000 {
        var outboundReferences: [AXUIElement] = []

        for rawName in attributeNames(of: element) {
            let name = safeRelationshipAttributeName(rawName)
            var stats = attributeStats[name] ?? RelationshipAttributeSummary(attribute: name)
            stats.elementOccurrences += 1

            let (error, value) = copyAttribute(element, rawName as CFString)
            if error == .success, let value {
                let relationship = relationshipReferences(in: value)
                let referenceCount = relationship.references.count
                if relationship.scalar { stats.scalarElementReferences += referenceCount }
                if relationship.array { stats.arrayOccurrences += 1 }
                stats.totalElementReferences += referenceCount
                stats.maxReferencesOnOneElement = max(stats.maxReferencesOnOneElement,
                                                       referenceCount)
                outboundReferences.append(contentsOf: relationship.references)
            }
            attributeStats[name] = stats
        }

        recordGraphElement(element, depth: depth, outboundCount: outboundReferences.count,
                           summary: &summary)
        if depth == 30 {
            for reference in outboundReferences where seen.insert(reference) {
                summary.traversalCapped = true
            }
            continue
        }
        for reference in outboundReferences.reversed() {
            if seen.count >= 20_000 {
                summary.traversalCapped = true
            } else if seen.insert(reference) {
                stack.append((reference, depth + 1))
            }
        }
    }
    if !stack.isEmpty { summary.traversalCapped = true }

    let attributes = attributeStats.values
        .filter { $0.totalElementReferences > 0 }
        .sorted {
            $0.totalElementReferences == $1.totalElementReferences
                ? $0.attribute < $1.attribute
                : $0.totalElementReferences > $1.totalElementReferences
        }
    return (summary, attributes)
}

private func isInsideWeChatApp(_ url: URL?) -> Bool {
    url?.standardized.path.contains("/WeChat.app/") == true
}

private func isWeChatCandidate(_ app: NSRunningApplication) -> Bool {
    app.bundleIdentifier == weChatBundleID ||
        app.localizedName == "WeChat" ||
        app.localizedName == "WeChatAppEx" ||
        isInsideWeChatApp(app.executableURL) ||
        isInsideWeChatApp(app.bundleURL)
}

private func safeProcessName(_ name: String?) -> String {
    guard let name, name == "WeChat" || name == "WeChatAppEx" else { return "<redacted>" }
    return name
}

private func processCandidate(_ app: NSRunningApplication, frontmostPID: pid_t?) -> ProcessCandidate {
    let pid = app.processIdentifier
    let axApp = AXUIElementCreateApplication(pid)
    let (windowsError, windowsValue) = copyAttribute(axApp, kAXWindowsAttribute as CFString)
    let windowCount = windowsError == .success ? (windowsValue as? [AXUIElement])?.count ?? 0 : 0
    let stats = inspectProcessAX(axApp)

    return ProcessCandidate(
        pid: Int(pid),
        name: safeProcessName(app.localizedName),
        bundleIdentifier: app.bundleIdentifier ?? "<unknown>",
        executable: app.executableURL?.lastPathComponent ?? "<unknown>",
        isActive: app.isActive,
        isHidden: app.isHidden,
        isTerminated: app.isTerminated,
        isFrontmost: pid == frontmostPID,
        windowCount: windowCount,
        totalElements: stats.totalElements,
        maxDepth: stats.maxDepth,
        traversalCapped: stats.traversalCapped,
        roleCounts: stats.roleCounts,
        listCount: stats.listCount,
        scrollAreaCount: stats.scrollAreaCount,
        groupCount: stats.groupCount,
        staticTextCount: stats.staticTextCount,
        identifierCount: stats.identifierCount,
        largestChildCount: stats.largestChildCount,
        richnessScore: stats.richnessScore
    )
}

private func relationshipTarget(from candidates: [NSRunningApplication],
                                frontmostPID: pid_t?) -> NSRunningApplication? {
    if let frontmostPID,
       let frontmost = candidates.first(where: { $0.processIdentifier == frontmostPID }) {
        return frontmost
    }

    var best: (app: NSRunningApplication, score: Int)?
    for app in candidates {
        let score = processCandidate(app, frontmostPID: frontmostPID).richnessScore
        if let current = best,
           score < current.score ||
            score == current.score && app.processIdentifier >= current.app.processIdentifier {
            continue
        } else {
            best = (app, score)
        }
    }
    return best?.app
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

private func processesMain() {
    let frontmostPID = NSWorkspace.shared.frontmostApplication?.processIdentifier
    let matchingApps = NSWorkspace.shared.runningApplications.filter(isWeChatCandidate)
    var candidates = matchingApps.map { processCandidate($0, frontmostPID: frontmostPID) }
    candidates.sort {
        $0.richnessScore == $1.richnessScore
            ? $0.pid < $1.pid
            : $0.richnessScore > $1.richnessScore
    }
    let result = ProcessesResult(
        accessibilityGranted: AXIsProcessTrusted(),
        frontmostPID: frontmostPID.map(Int.init),
        candidates: candidates
    )
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    if let data = try? encoder.encode(result) {
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
    }
}

private func relationshipsMain() {
    let frontmostPID = NSWorkspace.shared.frontmostApplication?.processIdentifier
    let candidates = NSWorkspace.shared.runningApplications.filter(isWeChatCandidate)
    guard let target = relationshipTarget(from: candidates, frontmostPID: frontmostPID) else {
        let result = RelationshipsResult(
            accessibilityGranted: AXIsProcessTrusted(),
            targetPID: nil,
            targetName: nil,
            targetBundleIdentifier: nil,
            targetExecutable: nil,
            targetIsFrontmost: false,
            childrenOnlyElementCount: 0,
            relationshipGraphElementCount: 0,
            additionalElementsDiscovered: 0,
            childrenTraversal: nil,
            relationshipTraversal: nil,
            relationshipAttributes: [],
            notes: ["No running WeChat-related process was found"]
        )
        writeRelationships(result)
        return
    }

    let root = AXUIElementCreateApplication(target.processIdentifier)
    let childrenTraversal = childrenGraph(from: root)
    let relationshipTraversal = relationshipGraph(from: root)
    let result = RelationshipsResult(
        accessibilityGranted: AXIsProcessTrusted(),
        targetPID: Int(target.processIdentifier),
        targetName: safeProcessName(target.localizedName),
        targetBundleIdentifier: target.bundleIdentifier ?? "<unknown>",
        targetExecutable: target.executableURL?.lastPathComponent ?? "<unknown>",
        targetIsFrontmost: target.processIdentifier == frontmostPID,
        childrenOnlyElementCount: childrenTraversal.elementCount,
        relationshipGraphElementCount: relationshipTraversal.summary.elementCount,
        additionalElementsDiscovered: max(0, relationshipTraversal.summary.elementCount -
                                          childrenTraversal.elementCount),
        childrenTraversal: childrenTraversal,
        relationshipTraversal: relationshipTraversal.summary,
        relationshipAttributes: relationshipTraversal.attributes,
        notes: []
    )
    writeRelationships(result)
}

private func writeRelationships(_ result: RelationshipsResult) {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    if let data = try? encoder.encode(result) {
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
    }
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

if CommandLine.arguments.dropFirst() == ["--relationships"] {
    relationshipsMain()
} else if CommandLine.arguments.dropFirst() == ["--processes"] {
    processesMain()
} else if CommandLine.arguments.dropFirst() == ["--structure"] {
    structureMain()
} else {
    normalMain()
}
