import Foundation
import Testing
@testable import WeChatCompanion

struct WeChatShareInboxTests {
    private func scratch() throws -> URL {
        let url = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("share-inbox-tests-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    private func sourceFile(in root: URL, bytes: [UInt8] = [0x50, 0x4B, 0x03, 0x04]) throws -> URL {
        let url = root.appendingPathComponent("source.zip")
        try Data(bytes).write(to: url)
        return url
    }

    @Test
    func enqueueCommitsAtomicallyWithoutEmbeddingSourceNames() throws {
        let root = try scratch()
        defer { try? FileManager.default.removeItem(at: root) }
        let source = try sourceFile(in: root)
        let inboxRoot = root.appendingPathComponent("inbox")
        let inbox = WeChatShareInbox(rootURL: inboxRoot)
        let now = Date(timeIntervalSince1970: 1_000)
        let queued = try inbox.enqueueCopy(from: source, now: now)
        let pending = try inbox.pendingItems(now: now)

        #expect(pending.count == 1)
        #expect(pending.first?.id == queued.id)
        #expect(pending.first?.createdAt == queued.createdAt)
        #expect(queued.createdAt == now)
        #expect(try Data(contentsOf: queued.archiveURL) == Data([0x50, 0x4B, 0x03, 0x04]))

        let names = try FileManager.default.contentsOfDirectory(atPath: inboxRoot.path)
        #expect(names.count == 1)
        #expect(names[0].hasPrefix("pending-"))
        #expect(!names[0].hasPrefix(".staging-"))
    }

    @Test
    func successfulRemovalDeletesWholeTransportDirectory() throws {
        let root = try scratch()
        defer { try? FileManager.default.removeItem(at: root) }
        let inbox = WeChatShareInbox(rootURL: root.appendingPathComponent("inbox"))
        let queued = try inbox.enqueueCopy(from: sourceFile(in: root))

        inbox.remove(queued)

        #expect(!FileManager.default.fileExists(atPath: queued.directoryURL.path))
        #expect(try inbox.pendingItems().isEmpty)
    }

    @Test
    func expiredPendingIsPurgedButFreshPendingSurvives() throws {
        let root = try scratch()
        defer { try? FileManager.default.removeItem(at: root) }
        let inbox = WeChatShareInbox(rootURL: root.appendingPathComponent("inbox"))
        let source = try sourceFile(in: root)
        let base = Date(timeIntervalSince1970: 10_000)
        let old = try inbox.enqueueCopy(from: source, now: base)
        let fresh = try inbox.enqueueCopy(
            from: source,
            now: base.addingTimeInterval(23 * 60 * 60)
        )

        let now = base.addingTimeInterval(24 * 60 * 60 + 1)
        let pending = try inbox.pendingItems(now: now)

        #expect(!FileManager.default.fileExists(atPath: old.directoryURL.path))
        #expect(FileManager.default.fileExists(atPath: fresh.directoryURL.path))
        #expect(pending.map(\.id) == [fresh.id])
    }





    @Test
    func fallbackGroupContainerUsesStandardMacOSLocation() {
        let home = URL(fileURLWithPath: "/Users/example", isDirectory: true)
        let url = WeChatShareInbox.fallbackGroupContainerURL(homeDirectory: home)

        #expect(
            url.path
                == "/Users/example/Library/Group Containers/"
                    + WeChatShareInbox.appGroupIdentifier
        )
    }

    @Test
    func enqueueSignalsOnlyAfterPendingCommit() throws {
        let root = try scratch()
        defer { try? FileManager.default.removeItem(at: root) }
        let counter = ShareInboxSignalCounter()
        let inbox = WeChatShareInbox(
            rootURL: root.appendingPathComponent("inbox"),
            signalChange: { counter.increment() }
        )

        _ = try inbox.enqueueCopy(from: sourceFile(in: root))

        #expect(counter.value == 1)
        #expect(try inbox.pendingItems().count == 1)
    }

    @Test
    func staleStagingIsPurgedWithoutTouchingFreshStaging() throws {
        let root = try scratch()
        defer { try? FileManager.default.removeItem(at: root) }
        let inboxRoot = root.appendingPathComponent("inbox")
        let inbox = WeChatShareInbox(rootURL: inboxRoot)
        try FileManager.default.createDirectory(at: inboxRoot, withIntermediateDirectories: true)

        let old = inboxRoot.appendingPathComponent(".staging-old", isDirectory: true)
        let fresh = inboxRoot.appendingPathComponent(".staging-fresh", isDirectory: true)
        try FileManager.default.createDirectory(at: old, withIntermediateDirectories: false)
        try FileManager.default.createDirectory(at: fresh, withIntermediateDirectories: false)

        let now = Date(timeIntervalSince1970: 50_000)
        try FileManager.default.setAttributes(
            [.modificationDate: now.addingTimeInterval(-WeChatShareInbox.stagingLifetime - 1)],
            ofItemAtPath: old.path
        )
        try FileManager.default.setAttributes(
            [.modificationDate: now],
            ofItemAtPath: fresh.path
        )

        try inbox.purgeExpired(now: now)

        #expect(!FileManager.default.fileExists(atPath: old.path))
        #expect(FileManager.default.fileExists(atPath: fresh.path))
    }

    @Test
    func malformedPendingDirectoryIsTerminalAndRemoved() throws {
        let root = try scratch()
        defer { try? FileManager.default.removeItem(at: root) }
        let inboxRoot = root.appendingPathComponent("inbox")
        let inbox = WeChatShareInbox(rootURL: inboxRoot)
        try FileManager.default.createDirectory(at: inboxRoot, withIntermediateDirectories: true)

        let malformed = inboxRoot.appendingPathComponent("pending-bad", isDirectory: true)
        try FileManager.default.createDirectory(at: malformed, withIntermediateDirectories: false)
        try Data("not-json".utf8).write(
            to: malformed.appendingPathComponent("handoff.json")
        )

        let pending = try inbox.pendingItems()

        #expect(pending.isEmpty)
        #expect(!FileManager.default.fileExists(atPath: malformed.path))
    }
}


struct WeChatShareExtensionConfigurationTests {
    @Test
    func shareExtensionUsesFileActivationRuleVersionTwo() throws {
        let testsURL = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let appRoot = testsURL.deletingLastPathComponent()
        let plistURL = appRoot.appendingPathComponent("WeChatCompanionShare/Info.plist")
        let data = try Data(contentsOf: plistURL)
        let root = try #require(
            PropertyListSerialization.propertyList(from: data, options: [], format: nil)
                as? [String: Any]
        )
        let extensionDictionary = try #require(root["NSExtension"] as? [String: Any])
        let attributes = try #require(
            extensionDictionary["NSExtensionAttributes"] as? [String: Any]
        )
        let activation = try #require(
            attributes["NSExtensionActivationRule"] as? [String: Any]
        )

        #expect(activation["NSExtensionActivationDictionaryVersion"] as? Int == 2)
        #expect(activation["NSExtensionActivationSupportsFileWithMaxCount"] as? Int == 32)
        #expect(activation["NSExtensionActivationSupportsAttachmentsWithMinCount"] == nil)
        #expect(activation["NSExtensionActivationSupportsAttachmentsWithMaxCount"] == nil)
        #expect(
            extensionDictionary["NSExtensionPrincipalClass"] as? String
                == "WeChatCompanionShareViewController"
        )
    }

    @Test
    func shareServicePrincipalRemainsAViewController() throws {
        let testsURL = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        let appRoot = testsURL.deletingLastPathComponent()
        let sourceURL = appRoot
            .appendingPathComponent("WeChatCompanionShare/ShareRequestHandler.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)

        #expect(source.contains("@objc(WeChatCompanionShareViewController)"))
        #expect(source.contains("final class ShareViewController: NSViewController"))
        #expect(!source.contains("NSExtensionRequestHandling"))
    }
}

private final class ShareInboxSignalCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var storage = 0

    var value: Int { lock.withLock { storage } }

    func increment() {
        lock.withLock { storage += 1 }
    }
}
