import Foundation

/// A deadline for one awaited operation, such as the circled area's capture.
enum Deadline {
    /// Runs `operation`, or throws `HelperError.message(message)` after `seconds`. Neither the
    /// deadline nor a cancellation (Esc) waits for a stuck operation: the first result wins and
    /// later ones are dropped. The caller cancels the operation's own task if it has one.
    static func within<T>(_ seconds: Double, message: String, _ operation: @escaping () async throws -> T) async throws -> T {
        let first = FirstResult<T>()
        let work = Task {
            do { first.finish(.success(try await operation())) } catch { first.finish(.failure(error)) }
        }
        let timer = Task {
            try? await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
            first.finish(.failure(HelperError.message(message)))
        }
        defer { work.cancel(); timer.cancel() }
        return try await withTaskCancellationHandler {
            try await first.value()
        } onCancel: {
            first.finish(.failure(CancellationError()))
        }
    }
}

/// The first of several racing results; later ones are ignored.
final class FirstResult<T>: @unchecked Sendable {
    private let lock = NSLock()
    private var result: Result<T, Error>?
    private var waiter: CheckedContinuation<T, Error>?

    func finish(_ outcome: Result<T, Error>) {
        lock.lock()
        guard result == nil else { lock.unlock(); return }
        result = outcome
        let waiting = waiter
        waiter = nil
        lock.unlock()
        waiting?.resume(with: outcome)
    }

    func value() async throws -> T {
        try await withCheckedThrowingContinuation { continuation in
            lock.lock()
            if let result {
                lock.unlock()
                continuation.resume(with: result)
                return
            }
            waiter = continuation
            lock.unlock()
        }
    }
}
