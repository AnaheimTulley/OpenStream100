import Foundation

public enum AudioTargetKind: String, Codable, CaseIterable, Sendable {
    case defaultOutput
    case defaultInput
    case outputDevice
    case inputDevice
    case application

    public var isInput: Bool {
        self == .defaultInput || self == .inputDevice
    }
}

public struct AudioTarget: Codable, Hashable, Identifiable, Sendable {
    public let id: String
    public var name: String
    public var kind: AudioTargetKind

    public init(id: String, name: String, kind: AudioTargetKind) {
        self.id = id
        self.name = name
        self.kind = kind
    }

    public static let defaultOutput = AudioTarget(
        id: "openstream100.default-output",
        name: "System Output",
        kind: .defaultOutput
    )

    public static let defaultInput = AudioTarget(
        id: "openstream100.default-input",
        name: "System Input",
        kind: .defaultInput
    )
}

public struct MixerChannel: Codable, Equatable, Identifiable, Sendable {
    public var id: Int
    public var targetID: String?
    public var colorHex: String
    public var inverted: Bool
    public var volume: Double?

    public init(
        id: Int,
        targetID: String?,
        colorHex: String,
        inverted: Bool = false,
        volume: Double? = nil
    ) {
        self.id = id
        self.targetID = targetID
        self.colorHex = colorHex
        self.inverted = inverted
        self.volume = volume
    }
}

public struct MixerConfiguration: Codable, Equatable, Sendable {
    public static let currentVersion = 1

    public var version: Int
    public var channels: [MixerChannel]
    public var knobSensitivity: Double
    public var launchAtLogin: Bool

    public init(
        version: Int = currentVersion,
        channels: [MixerChannel] = MixerConfiguration.defaultChannels,
        knobSensitivity: Double = 1,
        launchAtLogin: Bool = false
    ) {
        self.version = version
        self.channels = channels
        self.knobSensitivity = knobSensitivity
        self.launchAtLogin = launchAtLogin
        normalize()
    }

    public static let defaultChannels = [
        MixerChannel(id: 0, targetID: AudioTarget.defaultOutput.id, colorHex: "#52B788"),
        MixerChannel(id: 1, targetID: AudioTarget.defaultInput.id, colorHex: "#F4A261"),
        MixerChannel(id: 2, targetID: nil, colorHex: "#5DADE2"),
        MixerChannel(id: 3, targetID: nil, colorHex: "#C77DFF"),
    ]

    public mutating func normalize() {
        // Configuration files are user-editable. Keep the last channel when an
        // ID is repeated rather than trapping in Dictionary(uniqueKeysWithValues:).
        var byID: [Int: MixerChannel] = [:]
        for channel in channels where (0..<4).contains(channel.id) {
            byID[channel.id] = channel
        }
        channels = (0..<4).map { index in
            var channel = byID.removeValue(forKey: index) ?? Self.defaultChannels[index]
            channel.id = index
            if !Self.validColor(channel.colorHex) {
                channel.colorHex = Self.defaultChannels[index].colorHex
            }
            if let volume = channel.volume, volume.isFinite {
                channel.volume = min(max(volume, 0), 1)
            } else if channel.volume != nil {
                channel.volume = nil
            }
            return channel
        }
        knobSensitivity = knobSensitivity.isFinite
            ? min(max(knobSensitivity, 0.5), 4)
            : 1
        version = Self.currentVersion
    }

    private static func validColor(_ value: String) -> Bool {
        value.range(of: "^#[0-9A-Fa-f]{6}$", options: .regularExpression) != nil
    }
}

public enum ConfigurationCodec {
    public static func encode(_ configuration: MixerConfiguration) throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        return try encoder.encode(configuration)
    }

    public static func decode(_ data: Data) throws -> MixerConfiguration {
        var configuration = try JSONDecoder().decode(MixerConfiguration.self, from: data)
        configuration.normalize()
        return configuration
    }
}
