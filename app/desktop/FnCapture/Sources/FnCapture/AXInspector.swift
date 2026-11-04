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
        // The agent reads text, not pixels: screen text recognition drops emoji and symbols,
        // so the text of the elements across the circle (a 3 x 3 grid) goes with it too.
        let texts = gridTexts(in: quartz, systemWide: systemWide, first: element)
        let value = truncated(texts.isEmpty ? copyAttribute(element, kAXValueAttribute as CFString) : texts.joined(separator: " · "))
        return AXContext(role: role, title: title, value: value, appName: appName)
    }

    /// Distinct non-empty texts (value, then title, then description) of the elements under a
    /// 3 x 3 grid of points inside `quartz`, the centre element first. At most 8 texts.
    static func gridTexts(in quartz: CGRect, systemWide: AXUIElement, first: AXUIElement) -> [String] {
        var elements: [AXUIElement] = [first]
        for fy in [0.2, 0.5, 0.8] {
            for fx in [0.2, 0.5, 0.8] where !(fx == 0.5 && fy == 0.5) {
                var element: AXUIElement?
                let x = Float(quartz.minX + quartz.width * fx), y = Float(quartz.minY + quartz.height * fy)
                if AXUIElementCopyElementAtPosition(systemWide, x, y, &element) == .success, let element,
                   !elements.contains(where: { CFEqual($0, element) }) {
                    elements.append(element)
                }
            }
        }
        var seen = Set<String>(), out: [String] = []
        for element in elements {
            for name in [kAXValueAttribute, kAXTitleAttribute, kAXDescriptionAttribute] {
                guard let raw = copyAttribute(element, name as CFString) else { continue }
                let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !text.isEmpty, !seen.contains(text) else { continue }
                seen.insert(text); out.append(String(text.prefix(160)))
                break
            }
            if out.count >= 8 { break }
        }
        return out
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
