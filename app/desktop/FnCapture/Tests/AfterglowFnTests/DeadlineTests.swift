import XCTest
@testable import AfterglowFn

/// A stuck capture must not hold the helper: the deadline and Esc both return at once.
final class DeadlineTests: XCTestCase {
    func testFastOperationWins() async throws {
        let value = try await Deadline.within(1, message: "late") { 7 }
        XCTAssertEqual(value, 7)
    }

    func testDeadlineDoesNotWaitForAStuckOperation() async {
        let started = Date()
        do {
            _ = try await Deadline.within(0.1, message: "late") { () -> Int in
                try? await Task.sleep(nanoseconds: 3_000_000_000)  // ignores cancellation, like a stuck capture
                return 1
            }
            XCTFail("expected the deadline")
        } catch {
            XCTAssertEqual(error.localizedDescription, HelperError.message("late").localizedDescription)
        }
        XCTAssertLessThan(Date().timeIntervalSince(started), 1.0)
    }

    func testCancelReturnsAtOnce() async {
        let started = Date()
        let task = Task { () -> Int in
            try await Deadline.within(10, message: "late") { () -> Int in
                try? await Task.sleep(nanoseconds: 3_000_000_000)
                return 1
            }
        }
        try? await Task.sleep(nanoseconds: 100_000_000)
        task.cancel()
        let result = await task.result
        if case .success = result { XCTFail("expected cancellation") }
        XCTAssertLessThan(Date().timeIntervalSince(started), 1.0)
    }
}
