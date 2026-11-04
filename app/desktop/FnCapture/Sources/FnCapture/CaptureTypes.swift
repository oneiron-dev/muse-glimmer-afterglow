import Foundation

/// A recognised text box, normalised to the image with its origin at the top left.
public struct OCRText: Codable, Equatable {
    public let text: String
    public let x: Double
    public let y: Double
    public let w: Double
    public let h: Double
    public init(text: String, x: Double, y: Double, w: Double, h: Double) {
        self.text = text; self.x = x; self.y = y; self.w = w; self.h = h
    }
}

/// A small accessibility snapshot with a truncated value.
public struct AXContext: Codable, Equatable {
    public let role: String?
    public let title: String?
    public let value: String?
    public let appName: String?
    public init(role: String?, title: String?, value: String?, appName: String?) {
        self.role = role; self.title = title; self.value = value; self.appName = appName
    }
    enum CodingKeys: String, CodingKey {
        case role, title, value
        case appName = "app_name"
    }
}

/// AppKit coordinates are global points with a bottom-left origin. Quartz uses
/// global points with the primary display's top-left as its origin.
public enum Geometry {
    public static func quartzRect(fromAppKit rect: CGRect, primaryScreenHeight: CGFloat) -> CGRect {
        CGRect(x: rect.minX, y: primaryScreenHeight - rect.maxY, width: rect.width, height: rect.height)
    }
    /// Outward-rounded pixel boundaries for a point rectangle at the display scale.
    public static func pixelRect(fromPoints rect: CGRect, scale: CGFloat) -> CGRect {
        let x = floor(rect.minX * scale), y = floor(rect.minY * scale)
        return CGRect(x: x, y: y, width: ceil(rect.maxX * scale) - x, height: ceil(rect.maxY * scale) - y)
    }
    /// Vision's bottom-left box converted to normalised top-left coordinates.
    public static func topLeftBox(fromVision rect: CGRect) -> CGRect {
        CGRect(x: rect.minX, y: 1 - rect.maxY, width: rect.width, height: rect.height)
    }
    /// Keeps a floating window within a display, including displays left of the primary.
    public static func clamped(_ rect: CGRect, to screen: CGRect) -> CGRect {
        let width = min(rect.width, screen.width), height = min(rect.height, screen.height)
        return CGRect(x: min(max(rect.minX, screen.minX), screen.maxX - width),
                      y: min(max(rect.minY, screen.minY), screen.maxY - height), width: width, height: height)
    }
}
