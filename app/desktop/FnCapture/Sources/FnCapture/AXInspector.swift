import AppKit
import ApplicationServices
import CoreGraphics
import Foundation

/// Inspects the accessibility element at the centre of a rectangle.
///
/// The input uses global AppKit screen coordinates. It is converted with the
/// shared `Geometry.quartzRect(fromAppKit:primaryScreenHeight:)` helper before
/// calling `AXUIElementCopyElementAtPosition`, which expects Quartz points.
/// Role, title and value are truncated to `maxFieldLength` characters. The
/// front app name comes from `NSWorkspace`. This never throws: when access is
/// missing or no element exists, the returned `AXContext` carries nil fields.
@MainActor
public enum AXInspector {
    /// The maximum kept length for role, title and value strings.
    public static var maxFieldLength: Int = 200

    /// Returns the accessibility snapshot at the rectangle centre.
    ///
    /// - Parameter rect: The region in global AppKit screen coordinates.
    /// - Returns: An `AXContext` with role, title, truncated value and app name.
    public static func inspect(rect: CGRect) -> AXContext {
        let appName = NSWorkspace.shared.frontmostApplication?.localizedName
        guard let primaryHeight = NSScreen.screens.first?.frame.height else {
            return AXContext(role: nil, title: nil, value: nil, appName: appName)
        }
        let quartz = Geometry.quartzRect(fromAppKit: rect, primaryScreenHeight: primaryHeight)
        let center = CGPoint(x: quartz.midX, y: quartz.midY)
        let systemWide = AXUIElementCreateSystemWide()
        var element: AXUIElement?
        let status = AXUIElementCopyElementAtPosition(
            systemWide, Float(center.x), Float(center.y), &element
        )
        guard status == .success, let element else {
            return AXContext(role: nil, title: nil, value: nil, appName: appName)
        }
        let role = truncated(copyAttribute(element, kAXRoleAttribute as CFString))
        let title = truncated(copyAttribute(element, kAXTitleAttribute as CFString))
        let value = truncated(copyAttribute(element, kAXValueAttribute as CFString))
        return AXContext(role: role, title: title, value: value, appName: appName)
    }

    // MARK: - Private

    private static func copyAttribute(_ element: AXUIElement, _ name: CFString) -> String? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, name, &value) == .success else {
            return nil
        }
        if let string = value as? String {
            return string
        }
        if let number = value as? NSNumber {
            return number.stringValue
        }
        return nil
    }

    private static func truncated(_ string: String?) -> String? {
        guard let string else { return nil }
        guard string.count > maxFieldLength else { return string }
        return String(string.prefix(maxFieldLength))
    }
}
