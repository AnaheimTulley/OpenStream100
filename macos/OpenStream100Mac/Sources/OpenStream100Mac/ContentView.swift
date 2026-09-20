import OpenStream100Core
import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var model: MixerModel

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    limitationNotice
                    channelGrid
                    controllerSettings
                    if let error = model.lastError {
                        Label(error, systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(.red)
                            .font(.callout)
                    }
                }
                .padding(24)
            }
        }
        .frame(minWidth: 780, idealWidth: 900, minHeight: 560, idealHeight: 680)
        .background(Color(nsColor: .windowBackgroundColor))
    }

    private var header: some View {
        HStack(spacing: 14) {
            ZStack {
                RoundedRectangle(cornerRadius: 12)
                    .fill(.blue.gradient)
                Image(systemName: "slider.horizontal.3")
                    .font(.system(size: 25, weight: .semibold))
                    .foregroundStyle(.white)
            }
            .frame(width: 48, height: 48)

            VStack(alignment: .leading, spacing: 3) {
                Text("OpenStream100")
                    .font(.title2.weight(.semibold))
                HStack(spacing: 6) {
                    Circle()
                        .fill(model.isControllerConnected ? Color.green : Color.secondary)
                        .frame(width: 8, height: 8)
                    Text(model.statusMessage)
                        .foregroundStyle(.secondary)
                }
                .font(.callout)
            }
            Spacer()
            Button("Refresh Audio Devices", systemImage: "arrow.clockwise") {
                model.refreshTargets()
            }
        }
        .padding(20)
    }

    private var limitationNotice: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: "info.circle.fill")
                .foregroundStyle(.blue)
                .font(.title3)
            VStack(alignment: .leading, spacing: 4) {
                Text("macOS audio support")
                    .font(.headline)
                Text("Application channels use private Core Audio process taps and require macOS 14.2 or later. macOS asks for System Audio Recording permission the first time a routed application starts. Audio stays on this Mac and is not recorded to disk.")
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(14)
        .background(.blue.opacity(0.09), in: RoundedRectangle(cornerRadius: 12))
    }

    private var channelGrid: some View {
        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 14) {
            ForEach(model.configuration.channels) { channel in
                ChannelCard(channel: channel)
            }
        }
    }

    private var controllerSettings: some View {
        GroupBox("Controller") {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text("Knob sensitivity")
                    Slider(
                        value: Binding(
                            get: { model.configuration.knobSensitivity },
                            set: { model.setSensitivity($0) }
                        ),
                        in: 0.5...4,
                        step: 0.5
                    )
                    Text(model.configuration.knobSensitivity.formatted(.number.precision(.fractionLength(1))) + "×")
                        .monospacedDigit()
                        .frame(width: 38, alignment: .trailing)
                }
                Text("Turn a hardware encoder to adjust its assigned device. Press the encoder to mute or unmute it.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            .padding(8)
        }
    }
}

private struct ChannelCard: View {
    @EnvironmentObject private var model: MixerModel
    let channel: MixerChannel

    private var level: Float? {
        model.levels.indices.contains(channel.id) ? model.levels[channel.id] : nil
    }

    var body: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 12) {
                Picker("Audio target", selection: Binding(
                    get: { channel.targetID },
                    set: { model.selectTarget($0, channel: channel.id) }
                )) {
                    Text("Disabled").tag(String?.none)
                    Divider()
                    ForEach(model.targets) { target in
                        Text(target.name + targetSuffix(target)).tag(Optional(target.id))
                    }
                }

                HStack {
                    Image(systemName: channel.targetID == nil ? "speaker.slash" : "speaker.wave.2")
                        .foregroundStyle(channelColor)
                    Slider(
                        value: Binding(
                            get: { Double(level ?? 0) },
                            set: { model.setVolume(Float($0), channel: channel.id) }
                        ),
                        in: 0...1
                    )
                    .disabled(level == nil)
                    Text(level.map { "\(Int(($0 * 100).rounded()))%" } ?? "—")
                        .monospacedDigit()
                        .frame(width: 42, alignment: .trailing)
                    Button {
                        model.toggleMute(channel: channel.id)
                    } label: {
                        Image(systemName: "speaker.slash.fill")
                    }
                    .buttonStyle(.borderless)
                    .disabled(level == nil)
                    .help("Mute or unmute")
                }

                Toggle("Invert encoder direction", isOn: Binding(
                    get: { channel.inverted },
                    set: { model.setInverted($0, channel: channel.id) }
                ))
                .font(.callout)
            }
            .padding(6)
        } label: {
            HStack {
                Circle().fill(channelColor).frame(width: 10, height: 10)
                Text("Channel \(channel.id + 1)")
                    .font(.headline)
            }
        }
    }

    private var channelColor: Color {
        Color(hex: channel.colorHex) ?? .accentColor
    }

    private func targetSuffix(_ target: AudioTarget) -> String {
        switch target.kind {
        case .application: " — Application"
        case .inputDevice: " — Input"
        case .outputDevice: " — Output"
        default: ""
        }
    }
}

private extension Color {
    init?(hex: String) {
        guard hex.count == 7, hex.first == "#", let value = UInt64(hex.dropFirst(), radix: 16) else { return nil }
        self.init(
            red: Double((value >> 16) & 0xFF) / 255,
            green: Double((value >> 8) & 0xFF) / 255,
            blue: Double(value & 0xFF) / 255
        )
    }
}
