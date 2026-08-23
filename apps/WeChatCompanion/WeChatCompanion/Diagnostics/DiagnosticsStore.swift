import Foundation

struct MetadataStore<Value: Codable & Sendable>: Sendable {
    let fileURL: URL

    func load() throws -> Value? {
        guard FileManager.default.fileExists(atPath: fileURL.path) else { return nil }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try decoder.decode(Value.self, from: Data(contentsOf: fileURL))
    }

    func save(_ result: Value) throws {
        let directory = fileURL.deletingLastPathComponent()
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )

        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try encoder.encode(result)
        try data.write(to: fileURL, options: .atomic)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o600],
            ofItemAtPath: fileURL.path
        )
    }
}

typealias DiagnosticsStore = MetadataStore<DiagnosticResult>

extension MetadataStore where Value == DiagnosticResult {
    static var applicationSupport: Self {
        Self(fileURL: Self.applicationSupportURL.appendingPathComponent("diagnostics.json"))
    }
}

private extension MetadataStore {
    static var applicationSupportURL: URL {
        FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first!
        .appendingPathComponent("WeChatCompanion", isDirectory: true)
    }
}
