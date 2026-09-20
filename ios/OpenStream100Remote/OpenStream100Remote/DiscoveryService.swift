@preconcurrency import Network
import Foundation

@MainActor
final class DiscoveryService: ObservableObject {
    @Published private(set) var servers: [DiscoveredServer] = []
    @Published private(set) var errorMessage: String?

    private var browser: NWBrowser?

    func start() {
        guard browser == nil else { return }
        let parameters = NWParameters.tcp
        parameters.includePeerToPeer = true
        let browser = NWBrowser(for: .bonjour(type: "_openstream100._tcp", domain: nil), using: parameters)
        browser.stateUpdateHandler = { [weak self] state in
            guard case let .failed(error) = state else { return }
            Task { @MainActor in
                self?.errorMessage = "Automatic discovery failed: \(error.localizedDescription)"
            }
        }
        browser.browseResultsChangedHandler = { [weak self] results, _ in
            let mapped = results.compactMap(Self.mapResult).sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
            Task { @MainActor in self?.servers = mapped }
        }
        self.browser = browser
        browser.start(queue: DispatchQueue(label: "org.openstream100.remote.discovery"))
    }

    func stop() {
        browser?.cancel()
        browser = nil
        servers = []
    }

    private nonisolated static func mapResult(_ result: NWBrowser.Result) -> DiscoveredServer? {
        guard case let .service(name, _, _, interface) = result.endpoint else { return nil }
        let metadata = result.metadata
        let txtRecord: NWTXTRecord?
        if case let .bonjour(record) = metadata { txtRecord = record } else { txtRecord = nil }
        let fingerprint = txtRecord?.getEntry(for: "fingerprint").flatMap { entry -> String? in
            if case let .string(value) = entry { return value }
            return nil
        } ?? ""
        let advertised = txtRecord?.getEntry(for: "server").flatMap { entry -> String? in
            if case let .string(value) = entry { return value }
            return nil
        }
        guard let address = advertised, !address.isEmpty else {
            // NWBrowser intentionally does not expose the resolved host and port. The Linux
            // service advertises its reachable address in the TXT record for mobile clients.
            _ = interface
            return nil
        }
        return DiscoveredServer(name: name, address: address, fingerprint: fingerprint)
    }
}
