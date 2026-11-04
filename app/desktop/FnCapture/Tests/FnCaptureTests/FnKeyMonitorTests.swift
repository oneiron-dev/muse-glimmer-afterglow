import CoreGraphics
import XCTest
@testable import FnCapture

/// Pure hold-state tests for `FnKeyMonitor`.
///
/// These tests drive `handleTapEvent` and `handleTapDisabled` with synthetic
/// `CGEvent` values that are never posted. No tap is started, so no
/// Accessibility permission is needed.
final class FnKeyMonitorTests: XCTestCase {
    // Device-specific Option bits. The aggregate `maskAlternate` stays true
    // while either Option is held, so Right Option uses `0x40`.
    private var leftDevice: CGEventFlags.RawValue { 0x20 } // NX_DEVICELALTKEYMASK
    private var rightDevice: CGEventFlags.RawValue { 0x40 } // NX_DEVICERALTKEYMASK

    @MainActor
    private func flagsEvent(keycode: Int64, flags: CGEventFlags) -> CGEvent {
        let source = CGEventSource(stateID: .combinedSessionState)
        // Any keyboard event works as a carrier; only keycode and flags are read.
        let event = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: true)!
        event.setIntegerValueField(.keyboardEventKeycode, value: keycode)
        event.flags = flags
        return event
    }

    @MainActor
    private func rightFlags(withLeftHeld: Bool = false) -> CGEventFlags {
        var raw = CGEventFlags.maskAlternate.rawValue | rightDevice
        if withLeftHeld { raw |= leftDevice }
        return CGEventFlags(rawValue: raw)
    }

    @MainActor
    private func leftHeldFlags() -> CGEventFlags {
        CGEventFlags(rawValue: CGEventFlags.maskAlternate.rawValue | leftDevice)
    }

    @MainActor func testCleanFnHold() async {
        let monitor = FnKeyMonitor(mode: .function)
        var began = 0, ended = 0, cancelled = 0
        monitor.onHoldBegan = { began += 1 }
        monitor.onHoldEnded = { ended += 1 }
        monitor.onHoldCancelled = { cancelled += 1 }

        monitor.handleTapEvent(type: .flagsChanged, event: flagsEvent(keycode: 63, flags: .maskSecondaryFn))
        XCTAssertEqual(began, 1)
        XCTAssertTrue(monitor.isHolding)

        monitor.handleTapEvent(type: .flagsChanged, event: flagsEvent(keycode: 63, flags: []))
        XCTAssertEqual(ended, 1)
        XCTAssertEqual(cancelled, 0)
        XCTAssertFalse(monitor.isHolding)
    }

    @MainActor func testFnComboKeyCancels() async {
        let monitor = FnKeyMonitor(mode: .function)
        var began = 0, ended = 0, cancelled = 0
        monitor.onHoldBegan = { began += 1 }
        monitor.onHoldEnded = { ended += 1 }
        monitor.onHoldCancelled = { cancelled += 1 }

        monitor.handleTapEvent(type: .flagsChanged, event: flagsEvent(keycode: 63, flags: .maskSecondaryFn))
        XCTAssertEqual(began, 1)

        monitor.handleTapEvent(type: .keyDown, event: flagsEvent(keycode: 0, flags: .maskSecondaryFn))
        XCTAssertEqual(cancelled, 1)
        XCTAssertFalse(monitor.isHolding)

        monitor.handleTapEvent(type: .flagsChanged, event: flagsEvent(keycode: 63, flags: []))
        XCTAssertEqual(ended, 0)
        XCTAssertEqual(began, 1)
    }

    @MainActor func testFnOtherModifierCancels() async {
        let monitor = FnKeyMonitor(mode: .function)
        var began = 0, ended = 0, cancelled = 0
        monitor.onHoldBegan = { began += 1 }
        monitor.onHoldEnded = { ended += 1 }
        monitor.onHoldCancelled = { cancelled += 1 }

        monitor.handleTapEvent(type: .flagsChanged, event: flagsEvent(keycode: 63, flags: .maskSecondaryFn))
        // Left Shift (56) goes down while Fn is held.
        monitor.handleTapEvent(
            type: .flagsChanged,
            event: flagsEvent(keycode: 56, flags: [.maskSecondaryFn, .maskShift])
        )
        XCTAssertEqual(cancelled, 1)

        monitor.handleTapEvent(type: .flagsChanged, event: flagsEvent(keycode: 63, flags: .maskShift))
        XCTAssertEqual(ended, 0)
        XCTAssertEqual(began, 1)
    }

    @MainActor func testFnPreheldModifierIgnored() async {
        let monitor = FnKeyMonitor(mode: .function)
        var began = 0, ended = 0, cancelled = 0
        monitor.onHoldBegan = { began += 1 }
        monitor.onHoldEnded = { ended += 1 }
        monitor.onHoldCancelled = { cancelled += 1 }

        // Shift already held when Fn goes down: silent ignore, no began.
        monitor.handleTapEvent(
            type: .flagsChanged,
            event: flagsEvent(keycode: 63, flags: [.maskSecondaryFn, .maskShift])
        )
        XCTAssertEqual(began, 0)
        XCTAssertEqual(cancelled, 0)
        XCTAssertFalse(monitor.isHolding)

        monitor.handleTapEvent(type: .flagsChanged, event: flagsEvent(keycode: 63, flags: .maskShift))
        XCTAssertEqual(ended, 0)
        XCTAssertEqual(cancelled, 0)

        // The swallowed release clears state; a clean press works after.
        monitor.handleTapEvent(type: .flagsChanged, event: flagsEvent(keycode: 63, flags: .maskSecondaryFn))
        XCTAssertEqual(began, 1)
        monitor.handleTapEvent(type: .flagsChanged, event: flagsEvent(keycode: 63, flags: []))
        XCTAssertEqual(ended, 1)
    }

    @MainActor func testRightOptionCleanHold() async {
        let monitor = FnKeyMonitor(mode: .rightOption)
        var began = 0, ended = 0, cancelled = 0
        monitor.onHoldBegan = { began += 1 }
        monitor.onHoldEnded = { ended += 1 }
        monitor.onHoldCancelled = { cancelled += 1 }

        monitor.handleTapEvent(type: .flagsChanged, event: flagsEvent(keycode: 61, flags: rightFlags()))
        XCTAssertEqual(began, 1)
        XCTAssertTrue(monitor.isHolding)

        monitor.handleTapEvent(type: .flagsChanged, event: flagsEvent(keycode: 61, flags: []))
        XCTAssertEqual(ended, 1)
        XCTAssertEqual(cancelled, 0)
    }

    @MainActor func testRightOptionIgnoredWhenLeftPreheld() async {
        let monitor = FnKeyMonitor(mode: .rightOption)
        var began = 0, ended = 0, cancelled = 0
        monitor.onHoldBegan = { began += 1 }
        monitor.onHoldEnded = { ended += 1 }
        monitor.onHoldCancelled = { cancelled += 1 }

        // Left already held when Right goes down: ignored.
        monitor.handleTapEvent(type: .flagsChanged, event: flagsEvent(keycode: 61, flags: rightFlags(withLeftHeld: true)))
        XCTAssertEqual(began, 0)
        XCTAssertFalse(monitor.isHolding)

        // Right released with Left still held: aggregate maskAlternate is still
        // true, but the device bit correctly reports up and clears state.
        monitor.handleTapEvent(type: .flagsChanged, event: flagsEvent(keycode: 61, flags: leftHeldFlags()))
        XCTAssertEqual(ended, 0)
        XCTAssertEqual(cancelled, 0)

        // Left released (58). Then a clean Right press must work (no stick).
        monitor.handleTapEvent(type: .flagsChanged, event: flagsEvent(keycode: 58, flags: []))
        monitor.handleTapEvent(type: .flagsChanged, event: flagsEvent(keycode: 61, flags: rightFlags()))
        XCTAssertEqual(began, 1)
        monitor.handleTapEvent(type: .flagsChanged, event: flagsEvent(keycode: 61, flags: []))
        XCTAssertEqual(ended, 1)
    }

    @MainActor func testRightOptionReleaseWithLeftHeldAfterCancel() async {
        let monitor = FnKeyMonitor(mode: .rightOption)
        var began = 0, ended = 0, cancelled = 0
        monitor.onHoldBegan = { began += 1 }
        monitor.onHoldEnded = { ended += 1 }
        monitor.onHoldCancelled = { cancelled += 1 }

        monitor.handleTapEvent(type: .flagsChanged, event: flagsEvent(keycode: 61, flags: rightFlags()))
        XCTAssertEqual(began, 1)

        // Left (58) goes down while Right is held: cancel.
        let both = CGEventFlags(
            rawValue: CGEventFlags.maskAlternate.rawValue | leftDevice | rightDevice
        )
        monitor.handleTapEvent(type: .flagsChanged, event: flagsEvent(keycode: 58, flags: both))
        XCTAssertEqual(cancelled, 1)

        // Right released with Left still held: must read as up via 0x40.
        monitor.handleTapEvent(type: .flagsChanged, event: flagsEvent(keycode: 61, flags: leftHeldFlags()))
        XCTAssertEqual(ended, 0)

        monitor.handleTapEvent(type: .flagsChanged, event: flagsEvent(keycode: 58, flags: []))
        // Without the device-bit fix this clean press would stick.
        monitor.handleTapEvent(type: .flagsChanged, event: flagsEvent(keycode: 61, flags: rightFlags()))
        XCTAssertEqual(began, 2)
        monitor.handleTapEvent(type: .flagsChanged, event: flagsEvent(keycode: 61, flags: []))
        XCTAssertEqual(ended, 1)
    }

    @MainActor func testTapDisabledCancelsHold() async {
        let monitor = FnKeyMonitor(mode: .function)
        var began = 0, ended = 0, cancelled = 0
        monitor.onHoldBegan = { began += 1 }
        monitor.onHoldEnded = { ended += 1 }
        monitor.onHoldCancelled = { cancelled += 1 }

        monitor.handleTapEvent(type: .flagsChanged, event: flagsEvent(keycode: 63, flags: .maskSecondaryFn))
        XCTAssertEqual(began, 1)

        monitor.handleTapDisabled()
        XCTAssertEqual(cancelled, 1)
        XCTAssertFalse(monitor.isHolding)

        monitor.handleTapEvent(type: .flagsChanged, event: flagsEvent(keycode: 63, flags: []))
        XCTAssertEqual(ended, 0)

        // Idle tap-disabled fires nothing.
        let idle = FnKeyMonitor(mode: .function)
        var idleCancelled = 0
        idle.onHoldCancelled = { idleCancelled += 1 }
        idle.handleTapDisabled()
        XCTAssertEqual(idleCancelled, 0)
    }
}
