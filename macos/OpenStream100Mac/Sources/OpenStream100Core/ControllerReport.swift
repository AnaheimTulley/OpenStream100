import Foundation

public struct ControllerReport: Equatable, Sendable {
    public static let minimumByteCount = 11

    public let buttons: UInt8
    public let encoderPositions: [Int16]

    public init?(bytes: some Collection<UInt8>) {
        let report = Array(bytes)
        guard report.count >= Self.minimumByteCount else { return nil }

        buttons = report[1]
        encoderPositions = (0..<4).map { index in
            let offset = 3 + index * 2
            return Int16(bitPattern: UInt16(report[offset]) | UInt16(report[offset + 1]) << 8)
        }
    }

    public func encoderDelta(from previous: ControllerReport, at index: Int) -> Int {
        guard encoderPositions.indices.contains(index) else { return 0 }
        let before = Int(previous.encoderPositions[index])
        let after = Int(encoderPositions[index])
        return (after - before + 32_768).modulo(65_536) - 32_768
    }

    public func knobPressed(at index: Int, previous: ControllerReport) -> Bool {
        guard Self.knobMasks.indices.contains(index) else { return false }
        let mask = Self.knobMasks[index]
        return buttons & mask != 0 && previous.buttons & mask == 0
    }

    public func actionButtonPressed(at index: Int, previous: ControllerReport) -> Bool {
        guard Self.actionButtonMasks.indices.contains(index) else { return false }
        let mask = Self.actionButtonMasks[index]
        return buttons & mask != 0 && previous.buttons & mask == 0
    }

    private static let knobMasks: [UInt8] = [0x08, 0x01, 0x04, 0x02]
    private static let actionButtonMasks: [UInt8] = [0x10, 0x20, 0x40, 0x80]
}

private extension Int {
    func modulo(_ divisor: Int) -> Int {
        let result = self % divisor
        return result >= 0 ? result : result + divisor
    }
}
