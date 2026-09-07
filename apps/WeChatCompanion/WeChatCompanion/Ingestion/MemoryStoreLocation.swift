import Foundation

/// Where the app-owned memory store lives (M2.2d).
///
/// One owner, one location. The app writes it, the bundled worker fills it,
/// and the read-only memory MCP is pointed at it; none of them spells the
/// path independently, because two literals are how two components end up
/// using two files without anyone noticing. The Python side derives the same
/// location in `memory/memory_paths.py`, and a test compares the two.
///
/// It is not a user setting. There is no picker, no defaults key, and no way
/// to point the app at another file; an operator or a test can still name an
/// explicit store, which is what the CLI seam has always been for.
///
/// Deriving a path creates nothing. `prepareDirectory` is the only thing that
/// makes a directory, and only a caller that has already passed the consent
/// gate calls it, so a Mac whose user never consented has no directory rather
/// than an empty one.
enum MemoryStoreLocation {
    /// Must match `memory_paths.APP_DIRECTORY_NAME`.
    static let appDirectoryName = "WeChatCompanion"
    /// Must match `memory_paths.STORE_FILE_NAME`.
    static let storeFileName = "memory.sqlite"
    /// Must match `memory_paths.MESSAGE_STORE_FILE_NAME`.
    static let messageStoreFileName = "messages.sqlite"

    /// The path from the user's home directory. Safe to show or log: it says
    /// where the store is without saying whose it is.
    static let relativeComponents = [
        "Library", "Application Support", appDirectoryName, storeFileName,
    ]

    static var relativeDescription: String { relativeComponents.joined(separator: "/") }

    static var directory: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)
            .first!
            .appendingPathComponent(appDirectoryName, isDirectory: true)
    }

    /// The canonical memory store. Creates nothing.
    static var canonical: URL { directory.appendingPathComponent(storeFileName) }

    /// The app's existing message store, in the same owned directory.
    static var messageStore: URL { directory.appendingPathComponent(messageStoreFileName) }

    /// Creates the owning directory, owner-only. Call only after consent.
    @discardableResult
    static func prepareDirectory(for store: URL) throws -> URL {
        try FileManager.default.createDirectory(
            at: store.deletingLastPathComponent(),
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        return store
    }

    static func exists(_ store: URL = canonical) -> Bool {
        FileManager.default.fileExists(atPath: store.path)
    }
}
