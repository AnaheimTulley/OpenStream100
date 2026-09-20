import SwiftUI

struct PairingView: View {
    @EnvironmentObject private var model: RemoteViewModel
    @StateObject private var discovery = DiscoveryService()

    @State private var server = ""
    @State private var token = ""
    @State private var pin = ""
    @State private var showsPIN = false
    @State private var showsScanner = false
    @State private var busy = false
    @State private var notice: String?
    @State private var errorMessage: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                HStack(spacing: 12) {
                    AppMark(size: 50)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("OpenStream100 Remote")
                            .font(.title.bold())
                        Text("Choose a discovered mixer, scan its pairing QR, or enter the details manually.")
                            .foregroundStyle(.secondary)
                    }
                }

                if !discovery.servers.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack {
                            ForEach(discovery.servers) { found in
                                Button {
                                    select(found)
                                } label: {
                                    Label(found.name, systemImage: "desktopcomputer")
                                        .lineLimit(1)
                                }
                                .buttonStyle(.bordered)
                            }
                        }
                    }
                } else {
                    Label("Looking for OpenStream100 on your local network…", systemImage: "bonjour")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }

                if showsPIN {
                    VStack(alignment: .leading, spacing: 10) {
                        Text(notice ?? "Enter the six-digit PIN shown by OpenStream100 on the computer.")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                        HStack {
                            TextField("Pairing PIN", text: $pin)
                                .keyboardType(.numberPad)
                                .textContentType(.oneTimeCode)
                                .onChange(of: pin) { _, value in
                                    pin = String(value.filter(\.isNumber).prefix(6))
                                    errorMessage = nil
                                }
                                .textFieldStyle(.roundedBorder)
                            Button(busy ? "Pairing…" : "Pair iPhone") { completePINPairing() }
                                .buttonStyle(.borderedProminent)
                                .disabled(pin.count != 6 || busy)
                        }
                    }
                    .padding(14)
                    .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 12))
                }

                HStack(spacing: 12) {
                    TextField("Computer address", text: $server, prompt: Text("192.168.1.20:47680"))
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                        .textFieldStyle(.roundedBorder)
                    SecureField("Pairing token", text: $token)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .textFieldStyle(.roundedBorder)
                }

                HStack(spacing: 10) {
                    Button("Open mixer") { saveManualPairing() }
                        .buttonStyle(.borderedProminent)
                        .disabled(server.trimmingCharacters(in: .whitespaces).isEmpty || token.isEmpty)
                    Button {
                        showsScanner = true
                    } label: {
                        Label("Scan QR fallback", systemImage: "qrcode.viewfinder")
                    }
                    .buttonStyle(.bordered)
                    if model.settings != nil {
                        Button("Return to mixer") { model.showingPairing = false }
                            .buttonStyle(.bordered)
                        Button("Forget saved pairing", role: .destructive) { model.forgetPairing() }
                            .buttonStyle(.bordered)
                    }
                }

                if let message = errorMessage ?? discovery.errorMessage {
                    Label(message, systemImage: "exclamationmark.triangle.fill")
                        .font(.subheadline)
                        .foregroundStyle(Color.mutedRed)
                }
            }
            .frame(maxWidth: 920)
            .padding(.horizontal, 42)
            .padding(.vertical, 24)
            .frame(maxWidth: .infinity)
        }
        .onAppear {
            if let settings = model.settings {
                server = settings.server
            }
            discovery.start()
        }
        .onDisappear { discovery.stop() }
        .sheet(isPresented: $showsScanner) {
            QRCodeScannerView { result in
                showsScanner = false
                switch result {
                case let .success(value):
                    do {
                        try model.use(RemoteAPI.parsePairingURI(value))
                    } catch {
                        errorMessage = error.localizedDescription
                    }
                case let .failure(error):
                    errorMessage = error.localizedDescription
                }
            }
            .ignoresSafeArea()
        }
    }

    private func select(_ found: DiscoveredServer) {
        server = found.address
        errorMessage = nil
        Task {
            busy = true
            if await model.updateDiscoveredAddress(found) {
                busy = false
                return
            }
            showsPIN = true
            pin = ""
            notice = "Requesting a pairing PIN from the computer…"
            do {
                let expires = try await RemoteAPI.requestPINPairing(
                    server: found.address,
                    deviceID: DeviceIdentity.id,
                    deviceName: DeviceIdentity.name
                )
                notice = "The pairing PIN is displayed on the computer and expires in about \(expires) seconds."
            } catch {
                notice = "On the computer choose Pair new phone, then enter the PIN shown there."
                errorMessage = error.localizedDescription
            }
            busy = false
        }
    }

    private func completePINPairing() {
        Task {
            busy = true
            defer { busy = false }
            do {
                let paired = try await RemoteAPI.pairWithPIN(
                    server: server,
                    pin: pin,
                    deviceID: DeviceIdentity.id,
                    deviceName: DeviceIdentity.name
                )
                try model.use(paired)
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }

    private func saveManualPairing() {
        do {
            try model.use(RemoteSettings(server: server, token: token))
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
