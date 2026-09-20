import CoreAudio
import Foundation
import OpenStream100Core
import AppKit

struct AudioDevice: Identifiable, Hashable {
    let id: AudioDeviceID
    let uid: String
    let name: String
    let kind: AudioTargetKind
    let canSetVolume: Bool

    var target: AudioTarget {
        AudioTarget(id: "\(kind.rawValue):\(uid)", name: name, kind: kind)
    }
}

enum CoreAudioError: LocalizedError {
    case operationFailed(String, OSStatus)
    case targetUnavailable
    case volumeUnavailable

    var errorDescription: String? {
        switch self {
        case let .operationFailed(operation, status):
            return "\(operation) failed (Core Audio error \(status))."
        case .targetUnavailable:
            return "The selected audio device is no longer available."
        case .volumeUnavailable:
            return "This device does not expose software volume control."
        }
    }
}

final class CoreAudioService {
    func targets() -> [AudioTarget] {
        let concrete = devices().map(\.target)
        return [AudioTarget.defaultOutput, AudioTarget.defaultInput]
            + applicationTargets()
            + concrete
    }

    func volume(for target: AudioTarget) -> Float? {
        guard let device = resolve(target) else { return nil }
        return getVolume(deviceID: device.id, input: target.kind.isInput)
    }

    func setVolume(_ value: Float, for target: AudioTarget) throws {
        guard let device = resolve(target) else { throw CoreAudioError.targetUnavailable }
        let input = target.kind.isInput
        let clamped = min(max(value, 0), 1)
        var changed = false
        for element in controllableElements(deviceID: device.id, input: input) {
            var level = clamped
            var address = volumeAddress(input: input, element: element)
            if AudioObjectSetPropertyData(device.id, &address, 0, nil, UInt32(MemoryLayout.size(ofValue: level)), &level) == noErr {
                changed = true
            }
        }
        if !changed { throw CoreAudioError.volumeUnavailable }
    }

    func toggleMute(for target: AudioTarget) throws -> Bool {
        guard let device = resolve(target) else { throw CoreAudioError.targetUnavailable }
        let input = target.kind.isInput
        let elements = controllableMuteElements(deviceID: device.id, input: input)
        guard let first = elements.first else {
            let current = volume(for: target) ?? 0.5
            try setVolume(current > 0 ? 0 : 0.5, for: target)
            return current > 0
        }

        var address = muteAddress(input: input, element: first)
        var current: UInt32 = 0
        var size = UInt32(MemoryLayout.size(ofValue: current))
        guard AudioObjectGetPropertyData(device.id, &address, 0, nil, &size, &current) == noErr else {
            throw CoreAudioError.volumeUnavailable
        }
        var next: UInt32 = current == 0 ? 1 : 0
        var changed = false
        for element in elements {
            address = muteAddress(input: input, element: element)
            if AudioObjectSetPropertyData(device.id, &address, 0, nil, UInt32(MemoryLayout.size(ofValue: next)), &next) == noErr {
                changed = true
            }
        }
        if !changed { throw CoreAudioError.volumeUnavailable }
        return next != 0
    }

    private func devices() -> [AudioDevice] {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDevices,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size) == noErr else {
            return []
        }
        let count = Int(size) / MemoryLayout<AudioDeviceID>.size
        var ids = [AudioDeviceID](repeating: 0, count: count)
        guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &ids) == noErr else {
            return []
        }

        return ids.flatMap { id -> [AudioDevice] in
            guard let name = stringProperty(id, selector: kAudioObjectPropertyName),
                  let uid = stringProperty(id, selector: kAudioDevicePropertyDeviceUID) else { return [] }
            var result: [AudioDevice] = []
            if hasStreams(deviceID: id, input: false) {
                result.append(AudioDevice(id: id, uid: uid, name: name, kind: .outputDevice, canSetVolume: !controllableElements(deviceID: id, input: false).isEmpty))
            }
            if hasStreams(deviceID: id, input: true) {
                result.append(AudioDevice(id: id, uid: uid, name: name, kind: .inputDevice, canSetVolume: !controllableElements(deviceID: id, input: true).isEmpty))
            }
            return result
        }
        .sorted { lhs, rhs in
            lhs.name.localizedCaseInsensitiveCompare(rhs.name) == .orderedAscending
        }
    }

    private func resolve(_ target: AudioTarget) -> AudioDevice? {
        switch target.kind {
        case .defaultOutput:
            return defaultDevice(input: false)
        case .defaultInput:
            return defaultDevice(input: true)
        case .outputDevice, .inputDevice:
            return devices().first { $0.target.id == target.id && $0.kind == target.kind }
        case .application:
            return nil
        }
    }

    func processObjectIDs(for target: AudioTarget) -> [AudioObjectID] {
        guard target.kind == .application else { return [] }
        let bundleID = String(target.id.dropFirst("application:".count))
        return processObjectIDs().filter {
            stringProperty($0, selector: kAudioProcessPropertyBundleID) == bundleID
                && boolProperty($0, selector: kAudioProcessPropertyIsRunningOutput)
        }.sorted()
    }

    func defaultOutputDeviceUID() -> String? {
        defaultDevice(input: false)?.uid
    }

    private func applicationTargets() -> [AudioTarget] {
        var targetsByBundleID: [String: AudioTarget] = [:]
        for objectID in processObjectIDs() {
            guard boolProperty(objectID, selector: kAudioProcessPropertyIsRunningOutput),
                  let bundleID = stringProperty(objectID, selector: kAudioProcessPropertyBundleID),
                  !bundleID.isEmpty else { continue }
            let pid = processID(objectID)
            let name = pid.flatMap { NSRunningApplication(processIdentifier: $0)?.localizedName }
                ?? bundleID
            targetsByBundleID[bundleID] = AudioTarget(
                id: "application:\(bundleID)",
                name: name,
                kind: .application
            )
        }
        return targetsByBundleID.values.sorted {
            $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending
        }
    }

    private func processObjectIDs() -> [AudioObjectID] {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyProcessObjectList,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var size: UInt32 = 0
        let system = AudioObjectID(kAudioObjectSystemObject)
        guard AudioObjectGetPropertyDataSize(system, &address, 0, nil, &size) == noErr else {
            return []
        }
        var values = [AudioObjectID](
            repeating: kAudioObjectUnknown,
            count: Int(size) / MemoryLayout<AudioObjectID>.size
        )
        guard AudioObjectGetPropertyData(system, &address, 0, nil, &size, &values) == noErr else {
            return []
        }
        return values
    }

    private func processID(_ objectID: AudioObjectID) -> pid_t? {
        var value: pid_t = 0
        var size = UInt32(MemoryLayout.size(ofValue: value))
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioProcessPropertyPID,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        guard AudioObjectGetPropertyData(objectID, &address, 0, nil, &size, &value) == noErr else {
            return nil
        }
        return value
    }

    private func boolProperty(_ objectID: AudioObjectID, selector: AudioObjectPropertySelector) -> Bool {
        var value: UInt32 = 0
        var size = UInt32(MemoryLayout.size(ofValue: value))
        var address = AudioObjectPropertyAddress(
            mSelector: selector,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        return AudioObjectGetPropertyData(objectID, &address, 0, nil, &size, &value) == noErr
            && value != 0
    }

    private func defaultDevice(input: Bool) -> AudioDevice? {
        var id = AudioDeviceID(kAudioObjectUnknown)
        var size = UInt32(MemoryLayout.size(ofValue: id))
        var address = AudioObjectPropertyAddress(
            mSelector: input ? kAudioHardwarePropertyDefaultInputDevice : kAudioHardwarePropertyDefaultOutputDevice,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &id) == noErr,
              id != kAudioObjectUnknown,
              let name = stringProperty(id, selector: kAudioObjectPropertyName),
              let uid = stringProperty(id, selector: kAudioDevicePropertyDeviceUID) else { return nil }
        return AudioDevice(
            id: id,
            uid: uid,
            name: name,
            kind: input ? .inputDevice : .outputDevice,
            canSetVolume: !controllableElements(deviceID: id, input: input).isEmpty
        )
    }

    private func getVolume(deviceID: AudioDeviceID, input: Bool) -> Float? {
        for element in controllableElements(deviceID: deviceID, input: input) {
            var level: Float32 = 0
            var size = UInt32(MemoryLayout.size(ofValue: level))
            var address = volumeAddress(input: input, element: element)
            if AudioObjectGetPropertyData(deviceID, &address, 0, nil, &size, &level) == noErr {
                return level
            }
        }
        return nil
    }

    private func controllableElements(deviceID: AudioDeviceID, input: Bool) -> [AudioObjectPropertyElement] {
        [kAudioObjectPropertyElementMain, 1, 2].filter { element in
            var address = volumeAddress(input: input, element: element)
            var settable = DarwinBoolean(false)
            return AudioObjectHasProperty(deviceID, &address)
                && AudioObjectIsPropertySettable(deviceID, &address, &settable) == noErr
                && settable.boolValue
        }
    }

    private func controllableMuteElements(deviceID: AudioDeviceID, input: Bool) -> [AudioObjectPropertyElement] {
        [kAudioObjectPropertyElementMain, 1, 2].filter { element in
            var address = muteAddress(input: input, element: element)
            var settable = DarwinBoolean(false)
            return AudioObjectHasProperty(deviceID, &address)
                && AudioObjectIsPropertySettable(deviceID, &address, &settable) == noErr
                && settable.boolValue
        }
    }

    private func volumeAddress(input: Bool, element: AudioObjectPropertyElement) -> AudioObjectPropertyAddress {
        AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyVolumeScalar,
            mScope: input ? kAudioDevicePropertyScopeInput : kAudioDevicePropertyScopeOutput,
            mElement: element
        )
    }

    private func muteAddress(input: Bool, element: AudioObjectPropertyElement) -> AudioObjectPropertyAddress {
        AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyMute,
            mScope: input ? kAudioDevicePropertyScopeInput : kAudioDevicePropertyScopeOutput,
            mElement: element
        )
    }

    private func hasStreams(deviceID: AudioDeviceID, input: Bool) -> Bool {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyStreams,
            mScope: input ? kAudioDevicePropertyScopeInput : kAudioDevicePropertyScopeOutput,
            mElement: kAudioObjectPropertyElementMain
        )
        var size: UInt32 = 0
        return AudioObjectGetPropertyDataSize(deviceID, &address, 0, nil, &size) == noErr && size > 0
    }

    private func stringProperty(_ id: AudioObjectID, selector: AudioObjectPropertySelector) -> String? {
        var address = AudioObjectPropertyAddress(
            mSelector: selector,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var value: Unmanaged<CFString>?
        var size = UInt32(MemoryLayout.size(ofValue: value))
        guard AudioObjectGetPropertyData(id, &address, 0, nil, &size, &value) == noErr,
              let value else { return nil }
        return value.takeUnretainedValue() as String
    }
}
