import Foundation
import IOKit.hid
import OpenStream100Core

final class ControllerService: @unchecked Sendable {
    static let vendorID = 0x06F8
    static let productID = 0xE053

    var onConnectionChanged: (@Sendable (Bool) -> Void)?
    var onReport: (@Sendable (ControllerReport) -> Void)?

    private let manager: IOHIDManager

    init() {
        manager = IOHIDManagerCreate(kCFAllocatorDefault, IOOptionBits(kIOHIDOptionsTypeNone))
        let matching: [String: Any] = [
            kIOHIDVendorIDKey as String: Self.vendorID,
            kIOHIDProductIDKey as String: Self.productID,
        ]
        IOHIDManagerSetDeviceMatching(manager, matching as CFDictionary)

        let context = Unmanaged.passUnretained(self).toOpaque()
        IOHIDManagerRegisterDeviceMatchingCallback(manager, { context, _, _, _ in
            guard let context else { return }
            let service = Unmanaged<ControllerService>.fromOpaque(context).takeUnretainedValue()
            service.onConnectionChanged?(true)
        }, context)
        IOHIDManagerRegisterDeviceRemovalCallback(manager, { context, _, _, _ in
            guard let context else { return }
            let service = Unmanaged<ControllerService>.fromOpaque(context).takeUnretainedValue()
            service.onConnectionChanged?(false)
        }, context)
        IOHIDManagerRegisterInputReportCallback(manager, { context, result, _, type, _, report, length in
            guard result == kIOReturnSuccess,
                  type == kIOHIDReportTypeInput,
                  let context,
                  length > 0 else { return }
            let service = Unmanaged<ControllerService>.fromOpaque(context).takeUnretainedValue()
            if let decoded = ControllerReport(bytes: UnsafeBufferPointer(start: report, count: length)) {
                service.onReport?(decoded)
            }
        }, context)
    }

    func start() {
        IOHIDManagerScheduleWithRunLoop(manager, CFRunLoopGetMain(), CFRunLoopMode.commonModes.rawValue)
        IOHIDManagerOpen(manager, IOOptionBits(kIOHIDOptionsTypeNone))
        let devices = IOHIDManagerCopyDevices(manager) as? Set<IOHIDDevice>
        onConnectionChanged?(!(devices?.isEmpty ?? true))
    }

    func stop() {
        IOHIDManagerUnscheduleFromRunLoop(manager, CFRunLoopGetMain(), CFRunLoopMode.commonModes.rawValue)
        IOHIDManagerClose(manager, IOOptionBits(kIOHIDOptionsTypeNone))
    }

    deinit {
        IOHIDManagerClose(manager, IOOptionBits(kIOHIDOptionsTypeNone))
    }
}
