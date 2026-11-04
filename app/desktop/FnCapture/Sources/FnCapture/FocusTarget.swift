import ApplicationServices
import Foundation

/// The text field that had keyboard focus when you started speaking. Dictated words are pasted only into it:
/// another conversation or document in the same app gets nothing, and the words go to the clipboard instead.
public struct FocusTarget {
    /// The focused accessibility element, or nil when the app did not say (then only the app check applies).
    let element: AXUIElement?

    /// No field known yet (before the first recording).
    public static let unknown = FocusTarget(element: nil)

    /// Seconds one accessibility call may wait for a slow app.
    public static var messagingTimeout: Float = 0.25

    /// The element that has keyboard focus now.
    public static func current() -> FocusTarget {
        let systemWide = AXUIElementCreateSystemWide()
        AXUIElementSetMessagingTimeout(systemWide, messagingTimeout)
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(systemWide, kAXFocusedUIElementAttribute as CFString, &value) == .success,
              let value, CFGetTypeID(value) == AXUIElementGetTypeID() else {
            return FocusTarget(element: nil)
        }
        return FocusTarget(element: (value as! AXUIElement))
    }

    /// True when a paste may go to `now`: the same field as at the start, or no field was known at the start.
    public func allowsPaste(into now: FocusTarget) -> Bool {
        Self.allowsPaste(start: element, now: now.element) { CFEqual($0, $1) }
    }

    /// The rule, apart from accessibility: known start and a different or unknown field now refuse the paste.
    static func allowsPaste<T>(start: T?, now: T?, equal: (T, T) -> Bool) -> Bool {
        guard let start else { return true }
        guard let now else { return false }
        return equal(start, now)
    }
}
