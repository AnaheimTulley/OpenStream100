import SwiftUI

@main
struct OpenStream100RemoteApp: App {
    @StateObject private var model = RemoteViewModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(model)
                .preferredColorScheme(.dark)
        }
    }
}
