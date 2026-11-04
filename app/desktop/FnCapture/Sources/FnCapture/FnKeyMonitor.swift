import AppKit
import ApplicationServices
import CoreGraphics

/// Errors thrown by ``FnKeyMonitor/start()``.
public enum FnKeyMonitorError: Error, Sendable {
    /// Accessibility permission is missing, so the event tap would receive nothing.
    ///
    /// Enable the app under System Settings > Privacy & Security > Accessibility,
    /// then call ``FnKeyMonitor/start()`` again.
    case accessibilityPermissionMissing
    /// `CGEvent.tapCreate` returned `nil`.
    ///
    /// This usually means the event tap could not be installed (missing
    /// permission, another tap conflict, or a transient system failure).
    case eventTapCreationFailed
    /// The tap was created but its run-loop source could not be created.
    case runLoopSourceCreationFailed
    /// `start()` was called while already monitoring.
    ///
    /// Call ``FnKeyMonitor/stop()`` first, or ignore the duplicate start.
    case alreadyMonitoring
}

extension FnKeyMonitorError: LocalizedError {
    /// A short human-readable description for each case.
    public var errorDescription: String? {
        switch self {
        case .accessibilityPermissionMissing:
            return "Accessibility permission is required to monitor the Fn key."
        case .eventTapCreationFailed:
            return "Could not create the keyboard event tap."
        case .runLoopSourceCreationFailed:
            return "Could not attach the event tap to the run loop."
        case .alreadyMonitoring:
            return "Fn monitoring is already running."
        }
    }
}

/// Monitors a hold-to-talk modifier with a listen-only tap.
///
/// The monitor installs a listen-only `CGEventTap` on the main run loop and
/// watches `flagsChanged` for the configured key:
/// - `.function`: the Fn key (keycode 63, `maskSecondaryFn`).
/// - `.rightOption`: the Right Option key (keycode 61, device bit `0x40`) as a
///   fallback for keyboards and layouts where Fn is unreliable. The aggregate
///   `maskAlternate` stays true while either Option is held, so the
///   device-specific right-Option bit is used to avoid a stuck hold.
///
/// A clean press begins a hold (`onHoldBegan`) and its release ends it
/// (`onHoldEnded`). A press is ignored when another modifier is already held,
/// and an active hold is cancelled (`onHoldCancelled`) when any other key
/// (`keyDown` / `keyUp`) or modifier (other `flagsChanged`) arrives. The
/// eventual release after a cancel or an ignored press is swallowed. This
/// keeps Fn combos (Fn+arrows, Fn+delete, dictation, and so on) from
/// triggering a hold.
///
/// The tap uses `.listenOnly` and all events are returned unmodified.
/// The tap is re-enabled automatically after `.tapDisabledByTimeout` or
/// `.tapDisabledByUserInput`, and any active hold is cancelled at that point
/// so a missed release can never leave a stuck recording.
/// Create, start, and stop on the main actor, and call `stop()` before the
/// final release.
@MainActor
public final class FnKeyMonitor {
    /// Which physical key drives the hold detector.
    public enum Mode: String, Sendable {
        /// The Fn key (keycode 63).
        case function
        /// The Right Option key (keycode 61), used as a fallback hold key.
        case rightOption
    }

    /// Keyboard event keycode for the Fn key.
    public static let fnKeycode: Int64 = 63
    /// Keyboard event keycode for the Right Option key.
    public static let rightOptionKeycode: Int64 = 61

    /// Whether the process is trusted for accessibility event taps.
    ///
    /// Check this before calling `start()`, or catch
    /// `FnKeyMonitorError.accessibilityPermissionMissing`.
    public static var isAccessibilityTrusted: Bool {
        AXIsProcessTrusted()
    }

    /// Which key drives the hold detector.
    ///
    /// Changing the mode while a hold is active cancels that hold and fires
    /// `onHoldCancelled`. Any stale cancelled-press state from the previous
    /// mode is cleared so the next press in the new mode works cleanly.
    public var mode: Mode {
        didSet {
            if oldValue == mode { return }
            if isHolding {
                isHolding = false
                holdWasCancelled = false
                onHoldCancelled?()
            } else {
                holdWasCancelled = false
            }
        }
    }

    /// Whether the event tap is currently installed on the main run loop.
    public private(set) var isMonitoring: Bool = false
    /// Whether a hold is currently active (began and not yet ended/cancelled).
    public private(set) var isHolding: Bool = false

    /// Called on the main actor when the configured key goes down and starts a hold.
    public var onHoldBegan: (() -> Void)?
    /// Called on the main actor when the held key is released cleanly.
    ///
    /// This is not called after `onHoldCancelled` for the same press.
    public var onHoldEnded: (() -> Void)?
    /// Called on the main actor when another key or modifier is used with the held key.
    ///
    /// The eventual release of the held key is then ignored (no `onHoldEnded`).
    /// Presses ignored because another modifier was already held never fire
    /// `onHoldBegan` and never fire this callback either.
    public var onHoldCancelled: (() -> Void)?

    /// Creates a monitor for the given mode. Call `start()` to begin.
    ///
    /// - Parameter mode: The key to watch. Defaults to `.function`.
    public init(mode: Mode = .function) {
        self.mode = mode
    }

    /// Installs the event tap and begins monitoring.
    ///
    /// The tap is created with `.listenOnly`: it observes without modifying
    /// or swallowing events.
    ///
    /// - Throws: `FnKeyMonitorError.alreadyMonitoring` if already running,
    ///   `FnKeyMonitorError.accessibilityPermissionMissing` if the process is
    ///   not trusted, or a creation failure if the tap cannot be installed.
    public func start() throws {
        if isMonitoring {
            throw FnKeyMonitorError.alreadyMonitoring
        }
        guard Self.isAccessibilityTrusted else {
            throw FnKeyMonitorError.accessibilityPermissionMissing
        }
        let flagsBit = CGEventMask(1) << CGEventMask(CGEventType.flagsChanged.rawValue)
        let downBit = CGEventMask(1) << CGEventMask(CGEventType.keyDown.rawValue)
        let upBit = CGEventMask(1) << CGEventMask(CGEventType.keyUp.rawValue)
        let mask = flagsBit | downBit | upBit

        let box = FnKeyMonitorWeakBox(self)
        let opaque = Unmanaged.passRetained(box).toOpaque()
        guard let tap = CGEvent.tapCreate(
            tap: .cgSessionEventTap,
            place: .headInsertEventTap,
            options: .listenOnly,
            eventsOfInterest: mask,
            callback: fnKeyMonitorTapCallback,
            userInfo: opaque
        ) else {
            Unmanaged<FnKeyMonitorWeakBox>.fromOpaque(opaque).release()
            throw FnKeyMonitorError.eventTapCreationFailed
        }
        guard let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0) else {
            CGEvent.tapEnable(tap: tap, enable: false)
            CFMachPortInvalidate(tap)
            Unmanaged<FnKeyMonitorWeakBox>.fromOpaque(opaque).release()
            throw FnKeyMonitorError.eventTapCreationFailed
        }
        let holder = FnKeyMonitorTapHolder()
        holder.tap = tap
        holder.source = source
        holder.userInfo = opaque
        self.holder = holder
        CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes)
        CGEvent.tapEnable(tap: tap, enable: true)
        isMonitoring = true
        isHolding = false
        holdWasCancelled = false
    }

    /// Removes the event tap and resets hold state.
    ///
    /// This never fires `onHoldBegan`, `onHoldEnded`, or `onHoldCancelled`.
    /// It is safe to call when not monitoring.
    public func stop() {
        guard isMonitoring else { return }
        if let holder {
            if let source = holder.source {
                CFRunLoopRemoveSource(CFRunLoopGetMain(), source, .commonModes)
            }
            if let tap = holder.tap {
                CGEvent.tapEnable(tap: tap, enable: false)
                CFMachPortInvalidate(tap)
            }
            if let opaque = holder.userInfo {
                Unmanaged<FnKeyMonitorWeakBox>.fromOpaque(opaque).release()
                holder.userInfo = nil
            }
            holder.tap = nil
            holder.source = nil
        }
        holder = nil
        isMonitoring = false
        isHolding = false
        holdWasCancelled = false
    }

    // MARK: - Tap event handling (main actor, same file as the C callback)

    /// Re-enables the tap after the system disables it, cancelling any hold.
    ///
    /// Both `.tapDisabledByTimeout` and `.tapDisabledByUserInput` (secure
    /// input or revoked permission) mean releases may have been missed, so an
    /// active hold is cancelled via `onHoldCancelled` to avoid a stuck recording.
    func handleTapDisabled() {
        if isHolding {
            cancelHold()
        }
        guard isMonitoring, let tap = holder?.tap else { return }
        CGEvent.tapEnable(tap: tap, enable: true)
    }

    /// Routes a tap event to the hold state machine. Events are never swallowed.
    func handleTapEvent(type: CGEventType, event: CGEvent) {
        switch type {
        case .flagsChanged:
            let keycode = event.getIntegerValueField(.keyboardEventKeycode)
            handleFlagsChanged(keycode: keycode, flags: event.flags)
        case .keyDown, .keyUp:
            // Any real key press while holding means Fn is used as a combo.
            if isHolding {
                cancelHold()
            }
        default:
            break
        }
    }

    // MARK: - Private

    /// Device-specific left-Option bit (`NX_DEVICELALTKEYMASK`).
    private static let leftOptionDeviceMask: CGEventFlags.RawValue = 0x20
    /// Device-specific right-Option bit (`NX_DEVICERALTKEYMASK`).
    ///
    /// `maskAlternate` is aggregate and stays true while either Option is held,
    /// so Right Option uses this bit to detect its own release.
    private static let rightOptionDeviceMask: CGEventFlags.RawValue = 0x40

    /// True after a hold is cancelled or a press is ignored, until release.
    ///
    /// This swallows the release so a cancelled or ignored press never fires
    /// `onHoldEnded`, and it blocks a repeated down from starting mid-press.
    private var holdWasCancelled: Bool = false
    /// Retains the tap, its run-loop source, and the callback context.
    ///
    /// Its `deinit` is a best-effort safety net; callers must still call
    /// `stop()` on the main actor before release. Automatic ARC release of
    /// this holder is what makes `deinit` safe without touching isolated state.
    private var holder: FnKeyMonitorTapHolder?

    private func handleFlagsChanged(keycode: Int64, flags: CGEventFlags) {
        switch mode {
        case .function:
            if keycode == Self.fnKeycode {
                let isDown = flags.contains(.maskSecondaryFn)
                if isDown && hasPreheldModifierForFn(flags: flags) {
                    ignoreCurrentPress()
                    return
                }
                handleLevelTransition(isDown: isDown)
            } else if isHolding {
                cancelHold()
            }
        case .rightOption:
            if keycode == Self.rightOptionKeycode {
                let isDown = (flags.rawValue & Self.rightOptionDeviceMask) != 0
                if isDown && hasPreheldModifierForRightOption(flags: flags) {
                    ignoreCurrentPress()
                    return
                }
                handleLevelTransition(isDown: isDown)
            } else if isHolding {
                cancelHold()
            }
        }
    }

    private func hasPreheldModifierForFn(flags: CGEventFlags) -> Bool {
        let others: CGEventFlags = [
            .maskAlphaShift, .maskShift, .maskControl, .maskAlternate,
            .maskCommand, .maskHelp, .maskNumericPad,
        ]
        return !flags.intersection(others).isEmpty
    }

    private func hasPreheldModifierForRightOption(flags: CGEventFlags) -> Bool {
        let others: CGEventFlags = [
            .maskAlphaShift, .maskShift, .maskControl, .maskCommand,
            .maskHelp, .maskNumericPad, .maskSecondaryFn,
        ]
        if !flags.intersection(others).isEmpty { return true }
        return (flags.rawValue & Self.leftOptionDeviceMask) != 0
    }

    private func ignoreCurrentPress() {
        if !isHolding && !holdWasCancelled {
            holdWasCancelled = true
        }
    }

    private func handleLevelTransition(isDown: Bool) {
        if isDown {
            if isHolding || holdWasCancelled { return }
            isHolding = true
            onHoldBegan?()
        } else {
            if holdWasCancelled {
                holdWasCancelled = false
                isHolding = false
                return
            }
            if isHolding {
                isHolding = false
                onHoldEnded?()
            }
        }
    }

    private func cancelHold() {
        guard isHolding else { return }
        isHolding = false
        holdWasCancelled = true
        onHoldCancelled?()
    }
}

/// Convenience alias for the hold-key mode.
///
/// Use `FnKeyMonitor.Mode.function` or `FnHoldMode.function` interchangeably.
public typealias FnHoldMode = FnKeyMonitor.Mode

/// Weak back-reference from the C tap callback to its monitor.
///
/// The tap retains this box (not the monitor), so a monitor released without
/// `stop()` simply observes `nil` instead of crashing on a dangling pointer.
private final class FnKeyMonitorWeakBox {
    weak var monitor: FnKeyMonitor?
    init(_ monitor: FnKeyMonitor) {
        self.monitor = monitor
    }
}

/// Retains tap resources with a best-effort `deinit` cleanup.
///
/// `FnKeyMonitor.stop()` performs the orderly teardown and nils these fields;
/// `deinit` only runs when the caller forgot `stop()`.
private final class FnKeyMonitorTapHolder {
    var tap: CFMachPort?
    var source: CFRunLoopSource?
    var userInfo: UnsafeMutableRawPointer?
    deinit {
        if let source {
            CFRunLoopRemoveSource(CFRunLoopGetMain(), source, .commonModes)
        }
        if let tap {
            CGEvent.tapEnable(tap: tap, enable: false)
            CFMachPortInvalidate(tap)
        }
        if let userInfo {
            Unmanaged<FnKeyMonitorWeakBox>.fromOpaque(userInfo).release()
        }
    }
}

/// The C event-tap callback for a listen-only tap; forwards to the main actor.
///
/// The tap is installed on the main run loop, so this always runs on the main
/// thread. Off-main calls (which should never happen) return the event untouched.
private let fnKeyMonitorTapCallback: CGEventTapCallBack = { _, type, event, refcon in
    if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
        if let refcon, Thread.isMainThread {
            let box = Unmanaged<FnKeyMonitorWeakBox>.fromOpaque(refcon).takeUnretainedValue()
            MainActor.assumeIsolated {
                box.monitor?.handleTapDisabled()
            }
        }
        return nil
    }
    guard Thread.isMainThread else {
        return Unmanaged.passUnretained(event)
    }
    if let refcon {
        let box = Unmanaged<FnKeyMonitorWeakBox>.fromOpaque(refcon).takeUnretainedValue()
        MainActor.assumeIsolated {
            if let monitor = box.monitor {
                monitor.handleTapEvent(type: type, event: event)
            }
        }
    }
    return Unmanaged.passUnretained(event)
}
