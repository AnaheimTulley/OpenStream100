import SwiftUI

struct MixerView: View {
    @EnvironmentObject private var model: RemoteViewModel

    var body: some View {
        if let state = model.state {
            VStack(spacing: 8) {
                MixerHeader(state: state)
                HStack(spacing: 8) {
                    ForEach(state.channels) { channel in
                        ChannelStrip(channel: channel)
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                    }
                }
                HStack(spacing: 8) {
                    ForEach(state.actions) { action in
                        Button(action.label) { model.pressButton(action.index) }
                            .buttonStyle(.bordered)
                            .disabled(action.actionID == "disabled")
                            .lineLimit(1)
                            .font(.caption)
                            .frame(maxWidth: .infinity, minHeight: 36)
                    }
                }
            }
            .padding(10)
        } else {
            VStack(spacing: 14) {
                ProgressView()
                    .controlSize(.large)
                Text(model.connectionError ?? "Connecting to OpenStream100…")
                    .foregroundStyle(model.connectionError == nil ? .primary : Color.mutedRed)
                if model.connectionError != nil {
                    Button("Find paired mixer") { model.showingPairing = true }
                        .buttonStyle(.bordered)
                }
            }
        }
    }
}

private struct MixerHeader: View {
    @EnvironmentObject private var model: RemoteViewModel
    let state: MixerState

    var body: some View {
        HStack(spacing: 8) {
            AppMark(size: 30)
            Text("OpenStream100")
                .font(.headline.bold())
            Label(
                model.connectionError == nil ? "Connected" : "Reconnecting",
                systemImage: "circle.fill"
            )
            .font(.caption)
            .foregroundStyle(model.connectionError == nil ? Color.green : Color.yellow)
            Spacer()
            Button { select(state.page - 1) } label: { Image(systemName: "chevron.left") }
                .buttonStyle(.bordered)
                .disabled(state.pageCount <= 1)
            Text("Page \(state.page + 1) / \(state.pageCount)")
                .font(.subheadline.monospacedDigit())
                .frame(minWidth: 86)
            Button { select(state.page + 1) } label: { Image(systemName: "chevron.right") }
                .buttonStyle(.bordered)
                .disabled(state.pageCount <= 1)
            Button("Pairing") { model.showingPairing = true }
                .buttonStyle(.bordered)
        }
    }

    private func select(_ rawPage: Int) {
        let page = (rawPage + state.pageCount) % state.pageCount
        model.selectPage(page)
    }
}

private struct ChannelStrip: View {
    @EnvironmentObject private var model: RemoteViewModel
    let channel: MixerChannel

    private var accent: Color { Color(hex: channel.color) }

    var body: some View {
        VStack(spacing: 5) {
            Capsule()
                .fill(accent)
                .frame(height: 4)
            ChannelIcon(channel: channel)
                .frame(width: 34, height: 34)
            Text(channel.label)
                .font(.subheadline.weight(.semibold))
                .lineLimit(1)
            Text(channel.available ? "\(Int(channel.level * 100))%" : "Waiting for audio")
                .font(.caption)
                .foregroundStyle(channel.available ? Color.secondary : Color.gray)
            VerticalMixerControl(
                level: channel.level,
                meterLeft: channel.meterLeft,
                meterRight: channel.meterRight,
                muted: channel.muted,
                enabled: channel.available,
                accent: accent,
                onChange: { model.setVolume(channel: channel.index, level: $0) }
            )
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            Button(channel.muted ? "UNMUTE" : "MUTE") { model.toggleMute(channel: channel.index) }
                .font(.caption.weight(.semibold))
                .buttonStyle(ChannelButtonStyle(muted: channel.muted))
                .disabled(!channel.available)
                .frame(maxWidth: .infinity, minHeight: 36)
        }
        .padding(8)
        .background(Color.panelBackground, in: RoundedRectangle(cornerRadius: 12))
    }
}

private struct ChannelIcon: View {
    @EnvironmentObject private var model: RemoteViewModel
    let channel: MixerChannel
    @State private var image: UIImage?

    var body: some View {
        Group {
            if let image {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFit()
                    .accessibilityLabel("\(channel.label) icon")
            } else {
                ZStack {
                    Circle().fill(Color(red: 48 / 255, green: 58 / 255, blue: 72 / 255))
                    Text("\(channel.index + 1)")
                        .font(.caption.bold())
                        .foregroundStyle(.secondary)
                }
            }
        }
        .task(id: channel.iconPath) {
            image = nil
            guard let path = channel.iconPath else { return }
            image = try? await UIImage(data: model.icon(path: path))
        }
    }
}

private struct VerticalMixerControl: View {
    let level: Double
    let meterLeft: Double
    let meterRight: Double
    let muted: Bool
    let enabled: Bool
    let accent: Color
    let onChange: (Double) -> Void

    @State private var localLevel = 0.0
    @State private var dragging = false

    var body: some View {
        GeometryReader { proxy in
            let width = min(proxy.size.width * 0.58, 64)
            let left = (proxy.size.width - width) / 2
            let gap = 4.0
            let barWidth = (width - gap) / 2
            let markerY = (1 - localLevel) * proxy.size.height

            ZStack(alignment: .topLeading) {
                RoundedRectangle(cornerRadius: 5)
                    .fill(Color(red: 37 / 255, green: 45 / 255, blue: 57 / 255))
                    .frame(width: width, height: proxy.size.height)
                    .offset(x: left)
                if !muted {
                    meterBar(value: meterLeft, width: barWidth, height: proxy.size.height)
                        .offset(x: left, y: (1 - meterLeft) * proxy.size.height)
                    meterBar(value: meterRight, width: barWidth, height: proxy.size.height)
                        .offset(x: left + barWidth + gap, y: (1 - meterRight) * proxy.size.height)
                }
                Capsule()
                    .fill(.white)
                    .frame(width: width + 10, height: 4)
                    .offset(x: left - 5, y: max(min(markerY - 2, proxy.size.height - 4), 0))
            }
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { drag in
                        guard enabled else { return }
                        dragging = true
                        let value = min(max(1 - drag.location.y / proxy.size.height, 0), 1)
                        localLevel = value
                        onChange(value)
                    }
                    .onEnded { drag in
                        guard enabled else { return }
                        localLevel = min(max(1 - drag.location.y / proxy.size.height, 0), 1)
                        dragging = false
                        onChange(localLevel)
                    }
            )
        }
        .opacity(enabled ? 1 : 0.55)
        .onAppear { localLevel = level }
        .onChange(of: level) { _, value in
            if !dragging { localLevel = value }
        }
    }

    private func meterBar(value: Double, width: Double, height: Double) -> some View {
        RoundedRectangle(cornerRadius: 3)
            .fill(accent.opacity(0.82))
            .frame(width: width, height: value * height)
    }
}

private struct ChannelButtonStyle: ButtonStyle {
    let muted: Bool

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundStyle(.white)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(
                (muted ? Color.mutedRed : Color(red: 48 / 255, green: 58 / 255, blue: 72 / 255))
                    .opacity(configuration.isPressed ? 0.7 : 1),
                in: RoundedRectangle(cornerRadius: 8)
            )
    }
}

struct AppMark: View {
    let size: CGFloat

    var body: some View {
        Canvas { context, canvas in
            let scale = min(canvas.width, canvas.height) / 128
            let rect = CGRect(x: 4 * scale, y: 4 * scale, width: 120 * scale, height: 120 * scale)
            context.fill(Path(roundedRect: rect, cornerRadius: 27 * scale), with: .color(Color(hex: "#182332")))
            let top = CGRect(x: 16 * scale, y: 16 * scale, width: 96 * scale, height: 7 * scale)
            context.fill(Path(roundedRect: top, cornerRadius: 3.5 * scale), with: .color(.streamTeal))
            let colors: [Color] = [.streamTeal, .green, .yellow, .mutedRed]
            let yValues: [CGFloat] = [67, 56, 78, 61]
            for index in 0..<4 {
                let x = CGFloat(28 + 24 * index) * scale
                var track = Path()
                track.move(to: CGPoint(x: x, y: 42 * scale))
                track.addLine(to: CGPoint(x: x, y: 94 * scale))
                context.stroke(track, with: .color(Color(hex: "#526074")), style: StrokeStyle(lineWidth: 6 * scale, lineCap: .round))
                let knob = CGRect(x: x - 9 * scale, y: yValues[index] * scale - 9 * scale, width: 18 * scale, height: 18 * scale)
                context.fill(Path(ellipseIn: knob), with: .color(colors[index]))
                context.stroke(Path(ellipseIn: knob), with: .color(Color(hex: "#EDF4F9")), lineWidth: 3 * scale)
            }
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}
