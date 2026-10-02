import Foundation
import SwiftUI

@MainActor
final class RemoteViewModel: ObservableObject {
    @Published private(set) var settings: RemoteSettings?
    @Published private(set) var state: MixerState?
    @Published private(set) var connectionError: String?
    @Published var showingPairing: Bool

    private let credentials = CredentialStore()
    private var api: RemoteAPI?
    private var pollingTask: Task<Void, Never>?
    private var volumeTasks: [Int: Task<Void, Never>] = [:]

    init() {
        let saved = credentials.load()
        settings = saved
        showingPairing = saved == nil
        if let saved { api = try? RemoteAPI(settings: saved) }
    }

    func start() {
        guard settings != nil, !showingPairing else { return }
        pollingTask?.cancel()
        pollingTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self, let api = self.api else { return }
                do {
                    self.state = try await api.state()
                    self.connectionError = nil
                } catch is CancellationError {
                    return
                } catch {
                    self.connectionError = error.localizedDescription
                }
                try? await Task.sleep(for: .milliseconds(125))
            }
        }
    }

    func stop() {
        pollingTask?.cancel()
        pollingTask = nil
        volumeTasks.values.forEach { $0.cancel() }
        volumeTasks.removeAll()
    }

    func use(_ newSettings: RemoteSettings) throws {
        let normalized = try RemoteAPI.normalizedServer(newSettings.server)
        let saved = RemoteSettings(
            server: normalized.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/")),
            token: newSettings.token.trimmingCharacters(in: .whitespacesAndNewlines),
            fingerprint: newSettings.fingerprint
        )
        try credentials.save(saved)
        settings = saved
        api = try RemoteAPI(settings: saved)
        showingPairing = false
        state = nil
        connectionError = nil
        start()
    }

    func forgetPairing() {
        stop()
        credentials.remove()
        settings = nil
        api = nil
        state = nil
        connectionError = nil
        showingPairing = true
    }

    func updateDiscoveredAddress(_ server: DiscoveredServer) async -> Bool {
        guard let current = settings,
              !current.fingerprint.isEmpty,
              current.fingerprint == server.fingerprint
        else { return false }
        let candidate = RemoteSettings(server: server.address, token: current.token, fingerprint: current.fingerprint)
        do {
            let candidateAPI = try RemoteAPI(settings: candidate)
            _ = try await candidateAPI.state()
            try use(candidate)
            return true
        } catch {
            connectionError = "The saved credential was not accepted. Pair again with a new PIN."
            return false
        }
    }

    func setVolume(channel: Int, level: Double) {
        guard let page = state?.page, let api else { return }
        volumeTasks[channel]?.cancel()
        volumeTasks[channel] = Task {
            do { try await api.setVolume(page: page, channel: channel, level: level) }
            catch is CancellationError { return }
            catch { connectionError = error.localizedDescription }
            try? await Task.sleep(for: .milliseconds(45))
        }
    }

    func toggleMute(channel: Int) {
        guard let page = state?.page, let api else { return }
        Task {
            do { try await api.toggleMute(page: page, channel: channel) }
            catch { connectionError = error.localizedDescription }
        }
    }

    func selectPage(_ page: Int) {
        guard let api else { return }
        Task {
            do { try await api.selectPage(page) }
            catch { connectionError = error.localizedDescription }
        }
    }

    func pressButton(_ button: Int) {
        guard let page = state?.page, let api else { return }
        Task {
            do { try await api.pressButton(page: page, button: button) }
            catch { connectionError = error.localizedDescription }
        }
    }

    func icon(path: String) async throws -> Data {
        guard let api else { throw RemoteError.invalidAddress }
        return try await api.icon(path: path)
    }
}
