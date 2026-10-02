import Foundation

let remoteProtocolVersion = 1

struct RemoteSettings: Codable, Equatable, Sendable {
    let server: String
    let token: String
    let fingerprint: String

    init(server: String, token: String, fingerprint: String = "") {
        self.server = server
        self.token = token
        self.fingerprint = fingerprint
    }
}

struct MixerChannel: Decodable, Identifiable, Equatable, Sendable {
    let index: Int
    let label: String
    let color: String
    let available: Bool
    let muted: Bool
    let level: Double
    let meterLeft: Double
    let meterRight: Double
    let iconPath: String?

    var id: Int { index }

    enum CodingKeys: String, CodingKey {
        case index, label, color, available, muted, level, icon
        case meterLeft = "meter_left"
        case meterRight = "meter_right"
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        index = try values.decode(Int.self, forKey: .index)
        label = try values.decodeIfPresent(String.self, forKey: .label) ?? "Disabled"
        color = try values.decodeIfPresent(String.self, forKey: .color) ?? "#5B82F6"
        available = try values.decodeIfPresent(Bool.self, forKey: .available) ?? false
        muted = try values.decodeIfPresent(Bool.self, forKey: .muted) ?? false
        level = min(max(try values.decodeIfPresent(Double.self, forKey: .level) ?? 0, 0), 1)
        meterLeft = min(max(try values.decodeIfPresent(Double.self, forKey: .meterLeft) ?? 0, 0), 1)
        meterRight = min(max(try values.decodeIfPresent(Double.self, forKey: .meterRight) ?? 0, 0), 1)
        iconPath = try values.decodeIfPresent(String.self, forKey: .icon).flatMap { $0.isEmpty ? nil : $0 }
    }
}

struct MixerAction: Decodable, Identifiable, Equatable, Sendable {
    let index: Int
    let actionID: String
    let label: String

    var id: Int { index }

    enum CodingKeys: String, CodingKey {
        case index, label
        case actionID = "id"
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        index = try values.decode(Int.self, forKey: .index)
        actionID = try values.decodeIfPresent(String.self, forKey: .actionID) ?? "disabled"
        label = try values.decodeIfPresent(String.self, forKey: .label) ?? "Disabled"
    }
}

struct MixerState: Decodable, Equatable, Sendable {
    let protocolVersion: Int
    let revision: Int64
    let page: Int
    let pageCount: Int
    let channels: [MixerChannel]
    let actions: [MixerAction]

    enum CodingKeys: String, CodingKey {
        case revision, page, channels, actions
        case protocolVersion = "protocol"
        case pageCount = "page_count"
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        protocolVersion = try values.decode(Int.self, forKey: .protocolVersion)
        revision = try values.decodeIfPresent(Int64.self, forKey: .revision) ?? 0
        page = try values.decodeIfPresent(Int.self, forKey: .page) ?? 0
        pageCount = max(try values.decodeIfPresent(Int.self, forKey: .pageCount) ?? 1, 1)
        channels = try values.decode([MixerChannel].self, forKey: .channels)
        actions = try values.decode([MixerAction].self, forKey: .actions)
    }
}

struct DiscoveredServer: Identifiable, Equatable, Sendable {
    let name: String
    let address: String
    let fingerprint: String

    var id: String { "\(name)|\(address)" }
}

enum RemoteError: LocalizedError, Equatable {
    case invalidAddress
    case invalidResponse
    case server(String)
    case unsupportedProtocol
    case invalidPairingCode
    case incompletePairingCode
    case invalidPIN

    var errorDescription: String? {
        switch self {
        case .invalidAddress: "Enter the computer address."
        case .invalidResponse: "The computer returned an invalid response."
        case let .server(message): message
        case .unsupportedProtocol: "The computer uses an unsupported remote protocol."
        case .invalidPairingCode: "This is not an OpenStream100 pairing code."
        case .incompletePairingCode: "The pairing code is incomplete."
        case .invalidPIN: "Enter the six-digit PIN shown on the computer."
        }
    }
}
