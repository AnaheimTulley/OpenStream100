import Combine
import Foundation
import OpenStream100Core

@MainActor
final class MixerModel: ObservableObject {
    @Published var configuration: MixerConfiguration
    @Published private(set) var targets: [AudioTarget] = []
    @Published private(set) var levels: [Float?] = [nil, nil, nil, nil]
    @Published private(set) var isControllerConnected = false
    @Published private(set) var statusMessage = "Starting…"
    @Published private(set) var lastError: String?

    private let audio = CoreAudioService()
    private let controller = ControllerService()
    private let processTapRouter = ProcessTapRouter()
    private let configurationURL: URL
    private var previousReport: ControllerReport?
    private var accumulators = [Int](repeating: 0, count: 4)
    private var mutedApplicationLevels = [Float?](repeating: nil, count: 4)
    private var refreshTimer: Timer?
    private var saveTask: Task<Void, Never>?
    private var started = false
    private var refreshTick = 0

    init() {
        configurationURL = Self.defaultConfigurationURL()
        configuration = Self.loadConfiguration(from: configurationURL)

        controller.onConnectionChanged = { [weak self] connected in
            Task { @MainActor in
                self?.isControllerConnected = connected
                self?.updateStatus()
            }
        }
        controller.onReport = { [weak self] report in
            Task { @MainActor in self?.process(report) }
        }
    }

    func start() {
        guard !started else { return }
        started = true
        refreshTargets()
        controller.start()
        refreshTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self else { return }
                self.refreshTick += 1
                if self.refreshTick.isMultiple(of: 3) {
                    self.refreshTargets()
                } else {
                    self.refreshLevels()
                }
            }
        }
        updateStatus()
    }

    func stop() {
        guard started else { return }
        started = false
        refreshTimer?.invalidate()
        controller.stop()
        processTapRouter.stopAll()
    }

    func refreshTargets() {
        targets = audio.targets()
        synchronizeApplicationRoutes()
        refreshLevels()
    }

    func target(for channel: MixerChannel) -> AudioTarget? {
        guard let targetID = channel.targetID else { return nil }
        return targets.first { $0.id == targetID }
    }

    func selectTarget(_ targetID: String?, channel index: Int) {
        guard configuration.channels.indices.contains(index) else { return }
        configuration.channels[index].targetID = targetID
        configureApplicationRoute(channel: index)
        persistSoon()
        refreshLevels()
    }

    func setInverted(_ inverted: Bool, channel index: Int) {
        guard configuration.channels.indices.contains(index) else { return }
        configuration.channels[index].inverted = inverted
        persistSoon()
    }

    func setSensitivity(_ sensitivity: Double) {
        configuration.knobSensitivity = sensitivity
        persistSoon()
    }

    func setVolume(_ value: Float, channel index: Int) {
        guard let target = configuredTarget(at: index) else { return }
        if target.kind == .application {
            let clamped = min(max(value, 0), 1)
            processTapRouter.setGain(clamped, channel: index)
            configuration.channels[index].volume = Double(clamped)
            levels[index] = clamped
            if clamped > 0 { mutedApplicationLevels[index] = nil }
            persistSoon()
            return
        }
        do {
            try audio.setVolume(value, for: target)
            levels[index] = value
            lastError = nil
        } catch {
            lastError = error.localizedDescription
            refreshLevels()
        }
    }

    func toggleMute(channel index: Int) {
        guard let target = configuredTarget(at: index) else { return }
        if target.kind == .application {
            let current = levels[index] ?? Float(configuration.channels[index].volume ?? 1)
            if current > 0 {
                mutedApplicationLevels[index] = current
                setVolume(0, channel: index)
            } else {
                setVolume(mutedApplicationLevels[index] ?? 0.5, channel: index)
                mutedApplicationLevels[index] = nil
            }
            return
        }
        do {
            _ = try audio.toggleMute(for: target)
            lastError = nil
            refreshLevels()
        } catch {
            lastError = error.localizedDescription
        }
    }

    private func refreshLevels() {
        for index in configuration.channels.indices {
            guard let target = configuredTarget(at: index) else {
                levels[index] = nil
                continue
            }
            if target.kind == .application {
                levels[index] = Float(configuration.channels[index].volume ?? 1)
            } else {
                levels[index] = audio.volume(for: target)
            }
        }
    }

    private func synchronizeApplicationRoutes() {
        for index in configuration.channels.indices {
            configureApplicationRoute(channel: index, reportErrors: false)
        }
    }

    private func configureApplicationRoute(channel index: Int, reportErrors: Bool = true) {
        guard let target = configuredTarget(at: index), target.kind == .application else {
            processTapRouter.remove(channel: index)
            return
        }
        let processObjectIDs = audio.processObjectIDs(for: target)
        let outputDeviceUID = audio.defaultOutputDeviceUID()
        guard !processTapRouter.matches(
            channel: index,
            targetID: target.id,
            processObjectIDs: processObjectIDs,
            outputDeviceUID: outputDeviceUID
        ) else { return }
        do {
            try processTapRouter.configure(
                channel: index,
                target: target,
                processObjectIDs: processObjectIDs,
                outputDeviceUID: outputDeviceUID
            )
            let gain = Float(configuration.channels[index].volume ?? 1)
            processTapRouter.setGain(gain, channel: index)
            levels[index] = gain
            lastError = nil
        } catch {
            if reportErrors { lastError = error.localizedDescription }
        }
    }

    private func configuredTarget(at index: Int) -> AudioTarget? {
        guard configuration.channels.indices.contains(index) else { return nil }
        return target(for: configuration.channels[index])
    }

    private func process(_ report: ControllerReport) {
        defer { previousReport = report }
        guard let previousReport else { return }

        let countsPerPercent = max(1, Int((12 / configuration.knobSensitivity).rounded()))
        for index in 0..<4 {
            guard configuredTarget(at: index) != nil else {
                accumulators[index] = 0
                continue
            }
            var delta = report.encoderDelta(from: previousReport, at: index)
            if configuration.channels[index].inverted { delta = -delta }
            if abs(delta) > 2 { accumulators[index] += delta }

            if abs(accumulators[index]) >= countsPerPercent {
                let direction: Float = accumulators[index] > 0 ? 1 : -1
                let steps = min(abs(accumulators[index]) / countsPerPercent, 8)
                let current = levels[index] ?? 0.5
                setVolume(current + direction * Float(steps) / 100, channel: index)
                accumulators[index] -= (accumulators[index] > 0 ? 1 : -1) * steps * countsPerPercent
            }
            if report.knobPressed(at: index, previous: previousReport) {
                toggleMute(channel: index)
            }
        }
    }

    private func updateStatus() {
        if isControllerConnected {
            statusMessage = "Stream 100 connected"
        } else {
            statusMessage = "Stream 100 not connected — the on-screen mixer remains available"
        }
    }

    private func persistSoon() {
        saveTask?.cancel()
        let configuration = configuration
        let url = configurationURL
        saveTask = Task {
            try? await Task.sleep(for: .milliseconds(200))
            guard !Task.isCancelled else { return }
            do {
                let data = try ConfigurationCodec.encode(configuration)
                try FileManager.default.createDirectory(
                    at: url.deletingLastPathComponent(),
                    withIntermediateDirectories: true
                )
                try data.write(to: url, options: .atomic)
            } catch {
                lastError = "Could not save settings: \(error.localizedDescription)"
            }
        }
    }

    private static func defaultConfigurationURL() -> URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        return base.appending(path: "OpenStream100/config.json")
    }

    private static func loadConfiguration(from url: URL) -> MixerConfiguration {
        guard let data = try? Data(contentsOf: url),
              let configuration = try? ConfigurationCodec.decode(data) else {
            return MixerConfiguration()
        }
        return configuration
    }
}
