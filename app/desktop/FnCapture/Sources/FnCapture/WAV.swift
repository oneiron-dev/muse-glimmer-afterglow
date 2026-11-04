import Foundation

/// Encodes mono signed 16-bit PCM with a little-endian RIFF/WAVE header.
public enum WAV {
    public static func encode(samples: [Int16], sampleRate: Int = 16_000) -> Data {
        precondition(sampleRate > 0 && samples.count <= (Int(UInt32.max) - 36) / 2)
        var data = Data()
        func text(_ value: String) { data.append(contentsOf: value.utf8) }
        func u16(_ value: UInt16) {
            data.append(UInt8(truncatingIfNeeded: value))
            data.append(UInt8(truncatingIfNeeded: value >> 8))
        }
        func u32(_ value: UInt32) {
            for shift in stride(from: 0, to: 32, by: 8) { data.append(UInt8(truncatingIfNeeded: value >> shift)) }
        }
        let count = UInt32(samples.count * 2)
        text("RIFF"); u32(36 + count); text("WAVEfmt "); u32(16)
        u16(1); u16(1); u32(UInt32(sampleRate)); u32(UInt32(sampleRate * 2))
        u16(2); u16(16); text("data"); u32(count)
        for sample in samples { u16(UInt16(bitPattern: sample)) }
        return data
    }
}
