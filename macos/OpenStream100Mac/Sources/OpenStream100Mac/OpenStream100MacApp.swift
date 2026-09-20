import SwiftUI

@main
struct OpenStream100MacApp: App {
    @StateObject private var model = MixerModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(model)
                .onAppear { model.start() }
        }
        .windowStyle(.titleBar)
        .commands {
            CommandGroup(after: .appInfo) {
                Button("Refresh Audio Devices") { model.refreshTargets() }
                    .keyboardShortcut("r")
            }
        }

        Settings {
            VStack(alignment: .leading, spacing: 12) {
                Text("OpenStream100 for macOS")
                    .font(.headline)
                Text("Settings are saved in Application Support/OpenStream100/config.json.")
                    .foregroundStyle(.secondary)
            }
            .padding(24)
            .frame(width: 430)
        }
    }
}
