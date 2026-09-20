import Foundation
import Testing
@testable import OpenStream100Core

@Test func controllerReportDecodesLinuxProtocolLayout() throws {
    let bytes: [UInt8] = [0, 0x08, 0, 0xFE, 0xFF, 0x34, 0x12, 0, 0, 0xFF, 0x7F]
    let report = try #require(ControllerReport(bytes: bytes))

    #expect(report.buttons == 0x08)
    #expect(report.encoderPositions == [-2, 0x1234, 0, 32_767])
}

@Test func controllerReportHandlesEncoderWraparound() throws {
    var beforeBytes = [UInt8](repeating: 0, count: 11)
    beforeBytes[3] = 0xFE
    beforeBytes[4] = 0x7F
    var afterBytes = [UInt8](repeating: 0, count: 11)
    afterBytes[3] = 0x02
    afterBytes[4] = 0x80

    let before = try #require(ControllerReport(bytes: beforeBytes))
    let after = try #require(ControllerReport(bytes: afterBytes))
    #expect(after.encoderDelta(from: before, at: 0) == 4)
}

@Test func configurationAlwaysHasFourValidChannels() throws {
    let source = """
    {"version":0,"channels":[{"id":2,"targetID":null,"colorHex":"invalid","inverted":true}],"knobSensitivity":99,"launchAtLogin":false}
    """.data(using: .utf8)!
    let configuration = try ConfigurationCodec.decode(source)

    #expect(configuration.version == MixerConfiguration.currentVersion)
    #expect(configuration.channels.map(\.id) == [0, 1, 2, 3])
    #expect(configuration.channels[2].inverted)
    #expect(configuration.channels[2].colorHex == MixerConfiguration.defaultChannels[2].colorHex)
    #expect(configuration.knobSensitivity == 4)
}

@Test func applicationAssignmentAndGainRoundTrip() throws {
    var configuration = MixerConfiguration()
    configuration.channels[0].targetID = "application:com.example.player"
    configuration.channels[0].volume = 0.42

    let decoded = try ConfigurationCodec.decode(ConfigurationCodec.encode(configuration))
    #expect(decoded.channels[0].targetID == "application:com.example.player")
    #expect(decoded.channels[0].volume == 0.42)
    #expect(AudioTargetKind.application.rawValue == "application")
}
