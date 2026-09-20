import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var model: RemoteViewModel

    var body: some View {
        ZStack {
            Color.appBackground.ignoresSafeArea()
            if model.showingPairing || model.settings == nil {
                PairingView()
            } else {
                MixerView()
            }
        }
        .tint(.streamTeal)
        .onAppear { model.start() }
        .onDisappear { model.stop() }
    }
}

extension Color {
    static let appBackground = Color(red: 8 / 255, green: 11 / 255, blue: 16 / 255)
    static let panelBackground = Color(red: 25 / 255, green: 31 / 255, blue: 42 / 255).opacity(0.9)
    static let streamTeal = Color(red: 48 / 255, green: 204 / 255, blue: 190 / 255)
    static let mutedRed = Color(red: 245 / 255, green: 77 / 255, blue: 91 / 255)

    init(hex: String, fallback: Color = Color(red: 91 / 255, green: 130 / 255, blue: 246 / 255)) {
        let value = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        guard value.count == 6, let number = UInt64(value, radix: 16) else {
            self = fallback
            return
        }
        self.init(
            red: Double((number >> 16) & 0xFF) / 255,
            green: Double((number >> 8) & 0xFF) / 255,
            blue: Double(number & 0xFF) / 255
        )
    }
}
