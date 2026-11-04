import AVFoundation
import Foundation

/// Errors thrown when starting the microphone recorder.
public enum AudioRecorderError: Error, Sendable {
    /// Start was called while already recording.
    case alreadyRecording
    /// Stop was called with no recording and no buffered audio.
    case notRecording
    /// No microphone input is available.
    case noInput
    /// The audio engine failed to start.
    case engineStartFailed(String)
    /// The input could not be converted to 16 kHz mono.
    case conversionFailed
}

/// Records the microphone to 16 kHz mono signed 16-bit WAV data.
///
/// The tap runs on the audio thread. Samples are guarded by a lock, and the
/// level and auto-stop callbacks are delivered on the main thread. Set
/// `onLevel` and `onAutoStop` before calling `start` and do not mutate them
/// while recording.
///
/// Recording stops automatically at 60 seconds via both a sample cap and a
/// wall-clock timer, so silence still stops. A generation token ignores stale
/// taps and timers from a previous recording. After an auto-stop,
/// `isRecording` becomes false but the buffered audio is kept, so a following
/// `stop()` still returns the WAV data.
public final class AudioRecorder: NSObject, @unchecked Sendable {
    /// The output sample rate in hertz.
    public static let targetSampleRate: Int = 16_000
    /// The maximum recording length. The recorder auto-stops at this point.
    public static let maxDurationSeconds: TimeInterval = 60
    /// The maximum buffered sample count at the target rate.
    public static var maxSamples: Int {
        Int(maxDurationSeconds * Double(targetSampleRate))
    }

    private let lock = NSLock()
    private var engine: AVAudioEngine?
    private var samples: [Int16] = []
    private var recordingLocked = false
    private var autoStopFiredLocked = false
    private var generationLocked = 0

    /// True while the engine tap is installed and capturing.
    public var isRecording: Bool {
        lock.lock()
        defer { lock.unlock() }
        return recordingLocked
    }

    /// The buffered sample count at 16 kHz.
    public var sampleCount: Int {
        lock.lock()
        defer { lock.unlock() }
        return samples.count
    }

    /// The buffered duration in seconds at 16 kHz.
    public var durationSeconds: Double {
        Double(sampleCount) / Double(Self.targetSampleRate)
    }

    /// Called on the main thread with the input level from 0 to 1.
    public var onLevel: ((Float) -> Void)?
    /// Called once on the main thread when the 60 second cap auto-stops.
    ///
    /// After this fires, call `stop()` to collect the WAV data.
    public var onAutoStop: (() -> Void)?

    /// Creates an idle recorder.
    public override init() {
        super.init()
    }

    deinit {
        engine?.inputNode.removeTap(onBus: 0)
        engine?.stop()
    }

    /// Starts capturing from the default microphone.
    ///
    /// - Throws: `AudioRecorderError` when already recording, when no input
    ///   exists, or when the engine fails to start.
    public func start() throws {
        lock.lock()
        if recordingLocked {
            lock.unlock()
            throw AudioRecorderError.alreadyRecording
        }
        lock.unlock()

        let engine = AVAudioEngine()
        let input = engine.inputNode
        let inputFormat = input.inputFormat(forBus: 0)
        guard inputFormat.sampleRate > 0, inputFormat.channelCount > 0 else {
            throw AudioRecorderError.noInput
        }
        guard let targetFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: Double(Self.targetSampleRate),
            channels: 1,
            interleaved: false
        ) else {
            throw AudioRecorderError.conversionFailed
        }
        guard let converter = AVAudioConverter(from: inputFormat, to: targetFormat) else {
            throw AudioRecorderError.conversionFailed
        }

        lock.lock()
        generationLocked += 1
        let generation = generationLocked
        samples.removeAll(keepingCapacity: true)
        samples.reserveCapacity(Self.maxSamples)
        recordingLocked = true
        autoStopFiredLocked = false
        self.engine = engine
        lock.unlock()

        input.installTap(onBus: 0, bufferSize: 4096, format: inputFormat) { [weak self] buffer, _ in
            self?.handleTap(
                buffer: buffer,
                converter: converter,
                targetFormat: targetFormat,
                generation: generation
            )
        }
        engine.prepare()
        do {
            try engine.start()
        } catch {
            input.removeTap(onBus: 0)
            lock.lock()
            recordingLocked = false
            self.engine = nil
            lock.unlock()
            throw AudioRecorderError.engineStartFailed(error.localizedDescription)
        }

        // Wall-clock cap: the sample cap never fires when no buffers arrive.
        let deadline = DispatchTime.now() + Self.maxDurationSeconds
        DispatchQueue.main.asyncAfter(deadline: deadline) { [weak self] in
            self?.handleWallClockCap(generation: generation)
        }
    }

    /// Stops capturing and returns the buffered audio as WAV data.
    ///
    /// Uses the shared `WAV.encode(samples:sampleRate:)` helper. Returns nil
    /// when no samples were captured. Safe to call after an auto-stop to
    /// collect the capped recording.
    public func stop() -> Data? {
        stopEngine()
        lock.lock()
        let buffered = samples
        samples.removeAll()
        recordingLocked = false
        autoStopFiredLocked = false
        generationLocked += 1
        lock.unlock()
        guard !buffered.isEmpty else { return nil }
        return WAV.encode(samples: buffered, sampleRate: Self.targetSampleRate)
    }

    /// Stops capturing and discards buffered audio.
    public func cancel() {
        stopEngine()
        lock.lock()
        samples.removeAll()
        recordingLocked = false
        autoStopFiredLocked = false
        generationLocked += 1
        lock.unlock()
    }

    // MARK: - Private

    private func stopEngine() {
        lock.lock()
        let engine = self.engine
        lock.unlock()
        engine?.inputNode.removeTap(onBus: 0)
        engine?.stop()
        lock.lock()
        // Only clear if this is still the active engine.
        if self.engine === engine {
            self.engine = nil
        }
        lock.unlock()
    }

    private func handleTap(
        buffer: AVAudioPCMBuffer,
        converter: AVAudioConverter,
        targetFormat: AVAudioFormat,
        generation: Int
    ) {
        guard buffer.frameLength > 0 else { return }
        lock.lock()
        let active = recordingLocked && generation == generationLocked
        lock.unlock()
        guard active else { return }
        let inputFormat = buffer.format
        let ratio = Double(Self.targetSampleRate) / max(inputFormat.sampleRate, 1)
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 16
        guard let outBuffer = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: capacity) else {
            return
        }
        var supplied = false
        var convertError: NSError?
        let status = converter.convert(to: outBuffer, error: &convertError) { _, outStatus in
            if supplied {
                outStatus.pointee = .noDataNow
                return nil
            }
            supplied = true
            outStatus.pointee = .haveData
            return buffer
        }
        // The converter often returns .inputRanDry after writing output when
        // the output capacity overhangs the input, so accept either status
        // whenever frames were produced.
        guard (status == .haveData || status == .inputRanDry),
              convertError == nil,
              outBuffer.frameLength > 0,
              let channel = outBuffer.floatChannelData?[0] else {
            return
        }
        let frames = Int(outBuffer.frameLength)
        var sum: Float = 0
        var converted = [Int16]()
        converted.reserveCapacity(frames)
        for index in 0..<frames {
            let sample = channel[index]
            sum += sample * sample
            let clamped = max(-1, min(1, sample))
            converted.append(Int16(clamped * 32767))
        }
        let level = frames > 0 ? min(1, sqrt(sum / Float(frames)) * 2) : 0

        var shouldAutoStop = false
        lock.lock()
        // Re-check generation: stop/cancel/start during conversion must not
        // let a stale tap append to the new recording.
        if recordingLocked, generation == generationLocked {
            let room = max(0, Self.maxSamples - samples.count)
            if room > 0 {
                samples.append(contentsOf: converted.prefix(room))
            }
            if samples.count >= Self.maxSamples, !autoStopFiredLocked {
                autoStopFiredLocked = true
                shouldAutoStop = true
            }
        } else {
            lock.unlock()
            return
        }
        let levelCallback = onLevel
        lock.unlock()

        if let levelCallback {
            let captured = level
            DispatchQueue.main.async {
                levelCallback(captured)
            }
        }
        if shouldAutoStop {
            DispatchQueue.main.async { [weak self] in
                self?.finishAutoStop(generation: generation)
            }
        }
    }

    private func handleWallClockCap(generation: Int) {
        lock.lock()
        guard recordingLocked,
              generation == generationLocked,
              !autoStopFiredLocked else {
            lock.unlock()
            return
        }
        autoStopFiredLocked = true
        lock.unlock()
        finishAutoStop(generation: generation)
    }

    private func finishAutoStop(generation: Int) {
        lock.lock()
        // Ignore stale timers and taps from a previous recording.
        guard recordingLocked, generation == generationLocked else {
            lock.unlock()
            return
        }
        recordingLocked = false
        let callback = onAutoStop
        let engine = self.engine
        lock.unlock()
        engine?.inputNode.removeTap(onBus: 0)
        engine?.stop()
        lock.lock()
        if self.engine === engine {
            self.engine = nil
        }
        lock.unlock()
        callback?()
    }
}
