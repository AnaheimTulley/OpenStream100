import Foundation
import Security
import UIKit

struct CredentialStore: Sendable {
    private let service = "org.openstream100.remote.credentials"
    private let account = "paired-mixer"

    func load() -> RemoteSettings? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data
        else { return nil }
        return try? JSONDecoder().decode(RemoteSettings.self, from: data)
    }

    func save(_ settings: RemoteSettings) throws {
        let data = try JSONEncoder().encode(settings)
        let key: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        let attributes: [String: Any] = [kSecValueData as String: data]
        let status = SecItemUpdate(key as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            var insertion = key
            insertion[kSecValueData as String] = data
            guard SecItemAdd(insertion as CFDictionary, nil) == errSecSuccess else {
                throw RemoteError.server("Could not save the pairing credential.")
            }
        } else if status != errSecSuccess {
            throw RemoteError.server("Could not update the pairing credential.")
        }
    }

    func remove() {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
    }
}

@MainActor
enum DeviceIdentity {
    static var id: String {
        let key = "openstream100-device-id"
        if let stored = UserDefaults.standard.string(forKey: key) { return stored }
        let value = UUID().uuidString
        UserDefaults.standard.set(value, forKey: key)
        return value
    }

    static var name: String {
        UIDevice.current.name.isEmpty ? "iPhone" : UIDevice.current.name
    }
}
