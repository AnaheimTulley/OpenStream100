import CoreAudio
import Foundation
import OpenStream100Core

enum ProcessTapError: LocalizedError {
    case unavailable
    case applicationNotRunning(String)
    case duplicateApplication
    case operation(String, OSStatus)
    case missingTapUID

    var errorDescription: String? {
        switch self {
        case .unavailable:
            return "Per-application audio requires macOS 14.2 or later."
        case let .applicationNotRunning(name):
            return "\(name) is not currently producing audio."
        case .duplicateApplication:
            return "An application can only be assigned to one channel."
        case let .operation(name, status):
            return "\(name) failed (Core Audio error \(status))."
        case .missingTapUID:
            return "Core Audio did not publish an identifier for the process tap."
        }
    }
}

@MainActor
final class ProcessTapRouter {
    private var sessions: [Int: ProcessTapSessionProtocol] = [:]

    func matches(
        channel: Int,
        targetID: String,
        processObjectIDs: [AudioObjectID],
        outputDeviceUID: String?
    ) -> Bool {
        guard let session = sessions[channel], let outputDeviceUID else { return false }
        return session.targetID == targetID
            && session.processObjectIDs == processObjectIDs
            && session.outputDeviceUID == outputDeviceUID
    }

    func configure(
        channel: Int,
        target: AudioTarget?,
        processObjectIDs: [AudioObjectID],
        outputDeviceUID: String?
    ) throws {
        remove(channel: channel)
        guard let target, target.kind == .application else { return }
        guard !processObjectIDs.isEmpty else {
            throw ProcessTapError.applicationNotRunning(target.name)
        }
        guard !sessions.values.contains(where: {
            $0.targetID == target.id
        }) else {
            throw ProcessTapError.duplicateApplication
        }
        guard #available(macOS 14.2, *), let outputDeviceUID else {
            throw ProcessTapError.unavailable
        }
        sessions[channel] = try ProcessTapSession(
            targetID: target.id,
            processObjectIDs: processObjectIDs,
            outputDeviceUID: outputDeviceUID
        )
    }

    func setGain(_ gain: Float, channel: Int) {
        sessions[channel]?.gain = min(max(gain, 0), 1)
    }

    func remove(channel: Int) {
        guard let session = sessions.removeValue(forKey: channel) else { return }
        session.stop()
    }

    func stopAll() {
        for channel in sessions.keys.sorted() {
            remove(channel: channel)
        }
    }
}

private protocol ProcessTapSessionProtocol: AnyObject {
    var targetID: String { get }
    var processObjectIDs: [AudioObjectID] { get }
    var outputDeviceUID: String { get }
    var gain: Float { get set }
    func stop()
}

@available(macOS 14.2, *)
private final class ProcessTapSession: ProcessTapSessionProtocol, @unchecked Sendable {
    let targetID: String
    let processObjectIDs: [AudioObjectID]
    let outputDeviceUID: String
    var gain: Float {
        get { gainStorage.pointee }
        set { gainStorage.pointee = newValue }
    }

    private var tapID = AudioObjectID(kAudioObjectUnknown)
    private var aggregateDeviceID = AudioObjectID(kAudioObjectUnknown)
    private var ioProcID: AudioDeviceIOProcID?
    private let gainStorage: UnsafeMutablePointer<Float>
    private var stopped = false

    init(targetID: String, processObjectIDs: [AudioObjectID], outputDeviceUID: String) throws {
        self.targetID = targetID
        self.processObjectIDs = processObjectIDs
        self.outputDeviceUID = outputDeviceUID
        gainStorage = .allocate(capacity: 1)
        gainStorage.initialize(to: 1)

        do {
            let tapDescription = CATapDescription(stereoMixdownOfProcesses: processObjectIDs)
            tapDescription.name = "OpenStream100 — \(targetID)"
            tapDescription.isPrivate = true
            tapDescription.muteBehavior = .mutedWhenTapped

            var createdTap = AudioObjectID(kAudioObjectUnknown)
            var status = AudioHardwareCreateProcessTap(tapDescription, &createdTap)
            guard status == noErr else {
                throw ProcessTapError.operation("Creating the application audio tap", status)
            }
            tapID = createdTap

            guard let tapUID = Self.stringProperty(tapID, selector: kAudioTapPropertyUID) else {
                throw ProcessTapError.missingTapUID
            }

            let aggregateUID = "org.openstream100.tap.\(UUID().uuidString)"
            let description: [String: Any] = [
                kAudioAggregateDeviceNameKey: "OpenStream100 Application Route",
                kAudioAggregateDeviceUIDKey: aggregateUID,
                kAudioAggregateDeviceMainSubDeviceKey: outputDeviceUID,
                kAudioAggregateDeviceSubDeviceListKey: [
                    [kAudioSubDeviceUIDKey: outputDeviceUID],
                ],
                kAudioAggregateDeviceTapListKey: [
                    [
                        kAudioSubTapUIDKey: tapUID,
                        kAudioSubTapDriftCompensationKey: true,
                    ],
                ],
                kAudioAggregateDeviceTapAutoStartKey: true,
                kAudioAggregateDeviceIsPrivateKey: true,
            ]

            var createdAggregate = AudioObjectID(kAudioObjectUnknown)
            status = AudioHardwareCreateAggregateDevice(description as CFDictionary, &createdAggregate)
            guard status == noErr else {
                throw ProcessTapError.operation("Creating the application audio route", status)
            }
            aggregateDeviceID = createdAggregate

            var createdIOProc: AudioDeviceIOProcID?
            status = AudioDeviceCreateIOProcIDWithBlock(
                &createdIOProc,
                aggregateDeviceID,
                nil
            ) { [gainStorage] _, inputData, _, outputData, _ in
                Self.render(inputData: inputData, outputData: outputData, gain: gainStorage.pointee)
            }
            guard status == noErr, let createdIOProc else {
                throw ProcessTapError.operation("Creating the application audio renderer", status)
            }
            ioProcID = createdIOProc

            status = AudioDeviceStart(aggregateDeviceID, createdIOProc)
            guard status == noErr else {
                throw ProcessTapError.operation("Starting the application audio route", status)
            }
        } catch {
            stop()
            throw error
        }
    }

    func stop() {
        guard !stopped else { return }
        stopped = true
        if let ioProcID, aggregateDeviceID != kAudioObjectUnknown {
            AudioDeviceStop(aggregateDeviceID, ioProcID)
            AudioDeviceDestroyIOProcID(aggregateDeviceID, ioProcID)
            self.ioProcID = nil
        }
        if aggregateDeviceID != kAudioObjectUnknown {
            AudioHardwareDestroyAggregateDevice(aggregateDeviceID)
            aggregateDeviceID = kAudioObjectUnknown
        }
        if tapID != kAudioObjectUnknown {
            AudioHardwareDestroyProcessTap(tapID)
            tapID = kAudioObjectUnknown
        }
    }

    deinit {
        stop()
        gainStorage.deinitialize(count: 1)
        gainStorage.deallocate()
    }

    private static func render(
        inputData: UnsafePointer<AudioBufferList>,
        outputData: UnsafeMutablePointer<AudioBufferList>,
        gain: Float
    ) {
        let inputs = UnsafeMutableAudioBufferListPointer(
            UnsafeMutablePointer(mutating: inputData)
        )
        let outputs = UnsafeMutableAudioBufferListPointer(outputData)
        guard !inputs.isEmpty else { return }
        let inputOffset = max(0, inputs.count - outputs.count)

        for outputIndex in outputs.indices {
            let output = outputs[outputIndex]
            guard let outputRaw = output.mData else { continue }
            memset(outputRaw, 0, Int(output.mDataByteSize))

            let inputIndex = min(inputOffset + outputIndex, inputs.count - 1)
            guard inputs.indices.contains(inputIndex),
                  let inputRaw = inputs[inputIndex].mData else { continue }
            let byteCount = min(
                Int(inputs[inputIndex].mDataByteSize),
                Int(output.mDataByteSize)
            )
            let sampleCount = byteCount / MemoryLayout<Float>.size
            let source = inputRaw.assumingMemoryBound(to: Float.self)
            let destination = outputRaw.assumingMemoryBound(to: Float.self)
            for sample in 0..<sampleCount {
                destination[sample] = source[sample] * gain
            }
        }
    }

    private static func stringProperty(
        _ objectID: AudioObjectID,
        selector: AudioObjectPropertySelector
    ) -> String? {
        var address = AudioObjectPropertyAddress(
            mSelector: selector,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var value: Unmanaged<CFString>?
        var size = UInt32(MemoryLayout.size(ofValue: value))
        guard AudioObjectGetPropertyData(objectID, &address, 0, nil, &size, &value) == noErr,
              let value else { return nil }
        return value.takeUnretainedValue() as String
    }
}
