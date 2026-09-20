import Foundation

actor RemoteAPI {
    private let baseURL: URL
    private let token: String
    private let session: URLSession

    init(settings: RemoteSettings, session: URLSession = .shared) throws {
        baseURL = try Self.normalizedServer(settings.server)
        token = settings.token.trimmingCharacters(in: .whitespacesAndNewlines)
        self.session = session
    }

    func state() async throws -> MixerState {
        let data = try await request(method: "GET", path: "/state")
        let state = try JSONDecoder().decode(MixerState.self, from: data)
        guard state.protocolVersion == remoteProtocolVersion else {
            throw RemoteError.unsupportedProtocol
        }
        return state
    }

    func setVolume(page: Int, channel: Int, level: Double) async throws {
        try await command("set_volume", page: page, channel: channel, value: min(max(level, 0), 1))
    }

    func toggleMute(page: Int, channel: Int) async throws {
        try await command("toggle_mute", page: page, channel: channel)
    }

    func selectPage(_ page: Int) async throws {
        try await command("select_page", page: page)
    }

    func pressButton(page: Int, button: Int) async throws {
        try await command("press_button", page: page, channel: button)
    }

    func icon(path: String) async throws -> Data {
        try await request(method: "GET", path: path)
    }

    private func command(
        _ name: String,
        page: Int,
        channel: Int? = nil,
        value: Double? = nil
    ) async throws {
        var body: [String: Any] = [
            "protocol": remoteProtocolVersion,
            "request_id": UUID().uuidString,
            "command": name,
            "page": page,
        ]
        if let channel { body["channel"] = channel }
        if let value { body["value"] = value }
        _ = try await request(method: "POST", path: "/command", json: body)
    }

    private func request(method: String, path: String, json: [String: Any]? = nil) async throws -> Data {
        let url: URL
        if path.hasPrefix("/api/") {
            guard let resolved = URL(string: path, relativeTo: baseURL)?.absoluteURL else {
                throw RemoteError.invalidAddress
            }
            url = resolved
        } else {
            url = baseURL.appending(path: "api/v1\(path)")
        }

        var request = URLRequest(url: url, timeoutInterval: 1.5)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if let json {
            request.httpBody = try JSONSerialization.data(withJSONObject: json)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await session.data(for: request)
        try Self.validate(data: data, response: response)
        return data
    }

    static func requestPINPairing(server: String, deviceID: String, deviceName: String) async throws -> Int {
        let baseURL = try normalizedServer(server)
        let body: [String: Any] = [
            "protocol": remoteProtocolVersion,
            "device_id": deviceID,
            "device_name": deviceName,
        ]
        let data = try await unauthenticatedPOST(baseURL: baseURL, path: "api/v1/pair/request", body: body)
        guard
            let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
            let pairing = object["pairing"] as? [String: Any]
        else { return 120 }
        return pairing["expires_in"] as? Int ?? 120
    }

    static func pairWithPIN(
        server: String,
        pin: String,
        deviceID: String,
        deviceName: String
    ) async throws -> RemoteSettings {
        let cleanPIN = pin.filter(\.isNumber)
        guard cleanPIN.count == 6 else { throw RemoteError.invalidPIN }
        let baseURL = try normalizedServer(server)
        let body: [String: Any] = [
            "protocol": remoteProtocolVersion,
            "pin": cleanPIN,
            "device_id": deviceID,
            "device_name": deviceName,
        ]
        let data = try await unauthenticatedPOST(baseURL: baseURL, path: "api/v1/pair/complete", body: body)
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw RemoteError.invalidResponse
        }
        let token = object["token"] as? String ?? ""
        guard token.count >= 32 else { throw RemoteError.invalidResponse }
        return RemoteSettings(
            server: baseURL.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/")),
            token: token,
            fingerprint: object["token_fingerprint"] as? String ?? ""
        )
    }

    static func normalizedServer(_ value: String) throws -> URL {
        var trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        while trimmed.hasSuffix("/") { trimmed.removeLast() }
        guard !trimmed.isEmpty else { throw RemoteError.invalidAddress }
        if !trimmed.hasPrefix("http://") && !trimmed.hasPrefix("https://") {
            trimmed = "http://\(trimmed)"
        }
        guard let components = URLComponents(string: trimmed),
              let scheme = components.scheme,
              ["http", "https"].contains(scheme),
              components.host != nil,
              let url = components.url
        else { throw RemoteError.invalidAddress }
        return url
    }

    static func parsePairingURI(_ value: String) throws -> RemoteSettings {
        guard let components = URLComponents(string: value.trimmingCharacters(in: .whitespacesAndNewlines)),
              components.scheme == "openstream100",
              components.host == "pair"
        else { throw RemoteError.invalidPairingCode }
        let parameters = Dictionary(uniqueKeysWithValues: (components.queryItems ?? []).map { ($0.name, $0.value ?? "") })
        guard parameters["protocol"] == String(remoteProtocolVersion) else {
            throw RemoteError.unsupportedProtocol
        }
        let server = parameters["server"] ?? ""
        let token = parameters["token"] ?? ""
        guard !server.isEmpty, token.count >= 32 else { throw RemoteError.incompletePairingCode }
        return RemoteSettings(server: server, token: token, fingerprint: parameters["fingerprint"] ?? "")
    }

    private static func unauthenticatedPOST(baseURL: URL, path: String, body: [String: Any]) async throws -> Data {
        let url = baseURL.appending(path: path)
        var request = URLRequest(url: url, timeoutInterval: 2)
        request.httpMethod = "POST"
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let (data, response) = try await URLSession.shared.data(for: request)
        try validate(data: data, response: response)
        return data
    }

    private static func validate(data: Data, response: URLResponse) throws {
        guard let http = response as? HTTPURLResponse else { throw RemoteError.invalidResponse }
        guard (200...299).contains(http.statusCode) else {
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            let message = object?["error"] as? String
            throw RemoteError.server(message?.isEmpty == false ? message! : "Computer returned HTTP \(http.statusCode).")
        }
    }
}
