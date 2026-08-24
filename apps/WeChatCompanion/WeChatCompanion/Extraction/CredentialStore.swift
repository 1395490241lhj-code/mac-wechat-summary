import Foundation
import Security

/// Secret storage boundary. The only production implementation is the macOS
/// Keychain; tests inject an in-memory double so no test ever touches it.
protocol CredentialStoring: Sendable {
    func save(_ secret: String, account: String) throws
    func secret(account: String) throws -> String?
    func remove(account: String) throws
}

extension CredentialStoring {
    func hasSecret(account: String) -> Bool {
        guard let secret = try? secret(account: account) else { return false }
        return !(secret ?? "").isEmpty
    }
}

enum CredentialStoreError: Error, Equatable {
    /// Carries only the OSStatus code, never the secret or its contents.
    case keychainFailure(status: Int32)
    case invalidSecretEncoding
}

/// Keychain-backed storage. The secret is never copied into UserDefaults, a
/// plist, Application Support, logs, or any file this app writes.
struct KeychainCredentialStore: CredentialStoring {
    static let service = "com.lianghongjing.WeChatCompanion"

    let service: String

    init(service: String = KeychainCredentialStore.service) {
        self.service = service
    }

    private func baseQuery(account: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account
        ]
    }

    func save(_ secret: String, account: String) throws {
        guard let data = secret.data(using: .utf8) else {
            throw CredentialStoreError.invalidSecretEncoding
        }
        var query = baseQuery(account: account)
        let attributes: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        ]

        let updateStatus = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if updateStatus == errSecSuccess { return }
        guard updateStatus == errSecItemNotFound else {
            throw CredentialStoreError.keychainFailure(status: updateStatus)
        }

        query.merge(attributes) { current, _ in current }
        let addStatus = SecItemAdd(query as CFDictionary, nil)
        guard addStatus == errSecSuccess else {
            throw CredentialStoreError.keychainFailure(status: addStatus)
        }
    }

    func secret(account: String) throws -> String? {
        var query = baseQuery(account: account)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne

        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess else {
            throw CredentialStoreError.keychainFailure(status: status)
        }
        guard let data = item as? Data, let secret = String(data: data, encoding: .utf8) else {
            throw CredentialStoreError.invalidSecretEncoding
        }
        return secret
    }

    func remove(account: String) throws {
        let status = SecItemDelete(baseQuery(account: account) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw CredentialStoreError.keychainFailure(status: status)
        }
    }
}
