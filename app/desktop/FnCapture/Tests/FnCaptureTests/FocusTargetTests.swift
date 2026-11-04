import XCTest
@testable import FnCapture

/// Dictated words go only into the field that was focused when you started speaking.
final class FocusTargetTests: XCTestCase {
    private func allows(_ start: String?, _ now: String?) -> Bool {
        FocusTarget.allowsPaste(start: start, now: now) { $0 == $1 }
    }

    func testTheSameFieldGetsThePaste() {
        XCTAssertTrue(allows("conversation A", "conversation A"))
    }

    func testAnotherFieldInTheSameAppGetsNothing() {
        XCTAssertFalse(allows("conversation A", "conversation B"))
    }

    func testAFieldThatCannotBeReadNowGetsNothing() {
        XCTAssertFalse(allows("conversation A", nil))
    }

    func testAnUnknownStartKeepsTheAppCheckOnly() {
        XCTAssertTrue(allows(nil, "conversation B"))
        XCTAssertTrue(allows(nil, nil))
    }
}
