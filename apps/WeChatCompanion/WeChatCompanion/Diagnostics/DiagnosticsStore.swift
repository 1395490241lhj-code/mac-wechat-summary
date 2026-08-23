import Foundation

struct DiagnosticsStore: Sendable {
    let fileURL: URL

    static var applicationSupport: DiagnosticsStore {
        let base = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first!
        return DiagnosticsStore(
            fileURL: base
                .appendingPathComponent("WeChatCompanion", isDirectory: true)
                .appendingPathComponent("diagnostics.json")
        )
    }

    func load() throws -> DiagnosticResult? {
        guard FileManager.default.fileExists(atPath: fileURL.path) else { return nil }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try decoder.decode(DiagnosticResult.self, from: Data(contentsOf: fileURL))
    }

    func save(_ result: DiagnosticResult) throws {
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
