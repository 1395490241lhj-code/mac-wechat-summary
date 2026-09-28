import Foundation

enum WeChatShareInboxSignal {
    static let didChange = Notification.Name(
        "com.lianghongjing.WeChatCompanion.shareInbox.didChange"
    )

    static func post() {
        DistributedNotificationCenter.default().postNotificationName(
            didChange,
            object: nil,
            userInfo: nil,
            deliverImmediately: true
        )
    }
}

enum WeChatShareInboxError: Error, Equatable {
    case appGroupUnavailable
    case archiveTooLarge
}

struct WeChatShareInboxItem: Equatable, Sendable {
    let id: String
    let directoryURL: URL
    let archiveURL: URL
    let createdAt: Date
}

struct WeChatShareInbox: Sendable {
    static let appGroupIdentifier = "5M5KT5ZG74.com.lianghongjing.WeChatCompanion"
    static let appURL = URL(string: "wechatcompanion://share-import")!
    static let pendingLifetime: TimeInterval = 24 * 60 * 60
    static let stagingLifetime: TimeInterval = 10 * 60
    static let maximumArchiveBytes: Int64 = 1_073_741_824

    private static let directoryName = "WeChatShareInbox"
    private static let archiveName = "archive.zip"
    private static let manifestName = "handoff.json"

    let rootURL: URL
    private let signalChange: @Sendable () -> Void

    init(
        rootURL: URL,
        signalChange: @escaping @Sendable () -> Void = {}
    ) {
        self.rootURL = rootURL
        self.signalChange = signalChange
    }

    static func appGroup() -> WeChatShareInbox? {
        let manager = FileManager.default
        let container: URL
        if let systemContainer = manager.containerURL(
            forSecurityApplicationGroupIdentifier: appGroupIdentifier
        ) {
            container = systemContainer
        } else {
#if os(macOS)
            // The shipped main app is deliberately not sandboxed, while the
            // Share Extension is. On that host shape Foundation may not vend
            // the App Group URL even though the signed extension uses it.
            // A non-sandboxed macOS app can safely address its own Team-ID
            // group container at the standard per-user location.
            container = fallbackGroupContainerURL(
                homeDirectory: manager.homeDirectoryForCurrentUser
            )
#else
            return nil
#endif
        }
        return WeChatShareInbox(
            rootURL: container.appendingPathComponent(directoryName, isDirectory: true),
            signalChange: WeChatShareInboxSignal.post
        )
    }

    static func fallbackGroupContainerURL(homeDirectory: URL) -> URL {
        homeDirectory
            .appendingPathComponent("Library", isDirectory: true)
            .appendingPathComponent("Group Containers", isDirectory: true)
            .appendingPathComponent(appGroupIdentifier, isDirectory: true)
    }

    @discardableResult
    func enqueueCopy(
        from sourceURL: URL,
        now: Date = Date()
    ) throws -> WeChatShareInboxItem {
        try ensureRoot()
        let id = UUID().uuidString.lowercased()
        let staging = rootURL.appendingPathComponent(".staging-" + id, isDirectory: true)
        let pending = rootURL.appendingPathComponent("pending-" + id, isDirectory: true)
        try FileManager.default.createDirectory(at: staging, withIntermediateDirectories: false)
        try applyDirectoryPermissions(staging)
        var committed = false
        defer {
            if !committed {
                try? FileManager.default.removeItem(at: staging)
            }
        }

        let sourceSize = try fileSize(sourceURL)
        guard sourceSize <= Self.maximumArchiveBytes else {
            throw WeChatShareInboxError.archiveTooLarge
        }

        let archiveURL = staging.appendingPathComponent(Self.archiveName, isDirectory: false)
        try FileManager.default.copyItem(at: sourceURL, to: archiveURL)
        let copiedSize = try fileSize(archiveURL)
        guard copiedSize == sourceSize, copiedSize <= Self.maximumArchiveBytes else {
            throw WeChatShareInboxError.archiveTooLarge
        }
        try applyFilePermissions(archiveURL)

        let manifest = Manifest(
            version: 1,
            id: id,
            createdAt: now
        )
        let manifestURL = staging.appendingPathComponent(Self.manifestName)
        try Self.encoder.encode(manifest).write(to: manifestURL, options: .atomic)
        try applyFilePermissions(manifestURL)

        try FileManager.default.moveItem(at: staging, to: pending)
        committed = true
        signalChange()
        return WeChatShareInboxItem(
            id: id,
            directoryURL: pending,
            archiveURL: pending.appendingPathComponent(Self.archiveName),
            createdAt: now
        )
    }

    func pendingItems(now: Date = Date()) throws -> [WeChatShareInboxItem] {
        try ensureRoot()
        try purgeExpired(now: now)

        let children = try FileManager.default.contentsOfDirectory(
            at: rootURL,
            includingPropertiesForKeys: [.isDirectoryKey, .isSymbolicLinkKey],
            options: [.skipsHiddenFiles]
        )
        var items: [WeChatShareInboxItem] = []
        for directory in children where directory.lastPathComponent.hasPrefix("pending-") {
            guard let item = try validatedItem(at: directory) else {
                try? FileManager.default.removeItem(at: directory)
                continue
            }
            items.append(item)
        }
        return items.sorted { lhs, rhs in
            lhs.createdAt == rhs.createdAt ? lhs.id < rhs.id : lhs.createdAt < rhs.createdAt
        }
    }

    func remove(_ item: WeChatShareInboxItem) {
        let parent = item.directoryURL.deletingLastPathComponent().resolvingSymlinksInPath().path
        let root = rootURL.resolvingSymlinksInPath().path
        guard parent == root else { return }
        try? FileManager.default.removeItem(at: item.directoryURL)
    }

    func purgeExpired(now: Date = Date()) throws {
        try ensureRoot()
        let manager = FileManager.default
        let children = try manager.contentsOfDirectory(
            at: rootURL,
            includingPropertiesForKeys: [
                .isDirectoryKey, .isSymbolicLinkKey, .contentModificationDateKey,
            ],
            options: []
        )

        for directory in children {
            let name = directory.lastPathComponent
            let values = try? directory.resourceValues(
                forKeys: [.isDirectoryKey, .isSymbolicLinkKey, .contentModificationDateKey]
            )
            guard values?.isDirectory == true, values?.isSymbolicLink != true else { continue }

            if name.hasPrefix(".staging-") {
                let modified = values?.contentModificationDate ?? .distantPast
                if now.timeIntervalSince(modified) > Self.stagingLifetime {
                    try? manager.removeItem(at: directory)
                }
                continue
            }
            guard name.hasPrefix("pending-") else { continue }
            let manifestURL = directory.appendingPathComponent(Self.manifestName)
            guard let data = try? Data(contentsOf: manifestURL),
                  let manifest = try? Self.decoder.decode(Manifest.self, from: data),
                  manifest.version == 1
            else {
                try? manager.removeItem(at: directory)
                continue
            }

            if now.timeIntervalSince(manifest.createdAt) > Self.pendingLifetime {
                try? manager.removeItem(at: directory)
            }
        }
    }

    private func validatedItem(at directory: URL) throws -> WeChatShareInboxItem? {
        let directoryValues = try directory.resourceValues(
            forKeys: [.isDirectoryKey, .isSymbolicLinkKey]
        )
        guard directoryValues.isDirectory == true, directoryValues.isSymbolicLink != true else {
            return nil
        }

        let manifestURL = directory.appendingPathComponent(Self.manifestName)
        let manifest = try Self.decoder.decode(Manifest.self, from: Data(contentsOf: manifestURL))
        guard manifest.version == 1,
              directory.lastPathComponent == "pending-" + manifest.id
        else { return nil }
        let archiveURL = directory.appendingPathComponent(Self.archiveName)
        let archiveValues = try archiveURL.resourceValues(
            forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey]
        )
        guard archiveValues.isRegularFile == true,
              archiveValues.isSymbolicLink != true,
              Int64(archiveValues.fileSize ?? 0) <= Self.maximumArchiveBytes
        else { return nil }

        return WeChatShareInboxItem(
            id: manifest.id,
            directoryURL: directory,
            archiveURL: archiveURL,
            createdAt: manifest.createdAt
        )
    }

    private func ensureRoot() throws {
        try FileManager.default.createDirectory(
            at: rootURL,
            withIntermediateDirectories: true
        )
        try applyDirectoryPermissions(rootURL)
    }

    private func fileSize(_ url: URL) throws -> Int64 {
        let values = try url.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey])
        guard values.isRegularFile == true else { return Int64.max }
        return Int64(values.fileSize ?? 0)
    }
    private func applyDirectoryPermissions(_ url: URL) throws {
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o700],
            ofItemAtPath: url.path
        )
    }

    private func applyFilePermissions(_ url: URL) throws {
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o600],
            ofItemAtPath: url.path
        )
    }

    private struct Manifest: Codable {
        let version: Int
        let id: String
        let createdAt: Date
    }

    private static let encoder: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .millisecondsSince1970
        return encoder
    }()
    private static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .millisecondsSince1970
        return decoder
    }()
}
