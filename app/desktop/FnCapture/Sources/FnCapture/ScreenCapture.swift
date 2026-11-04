import AppKit
import CoreGraphics
import Foundation
import ImageIO
import ScreenCaptureKit

/// Errors thrown when capturing a screen region.
public enum ScreenCaptureError: Error, Sendable {
    /// No display matches the requested rectangle.
    case noDisplay
    /// The requested rectangle is empty.
    case emptyRect
    /// Shareable content could not be listed.
    case noShareableContent
    /// The screenshot manager failed.
    case captureFailed(String)
    /// The captured image could not be encoded as PNG.
    case encodingFailed
}

/// Captures an AppKit rectangle to PNG via ScreenCaptureKit.
///
/// The input rectangle uses global AppKit screen coordinates with a
/// bottom-left origin. It is converted with the shared
/// `Geometry.quartzRect(fromAppKit:primaryScreenHeight:)` helper before the
/// capture. Overlay windows are excluded by merging the per-call
/// `excludedWindowIDs` with `overlayExcludedWindowIDs`.
///
/// Validated from the macOS SDK headers: `SCScreenshotManager` is available
/// from macOS 14.0 with
/// `captureImage(contentFilter:configuration:completionHandler:)`, and
/// `SCContentFilter(display:excludingWindows:)` selects the display while
/// excluding windows. This package targets macOS 14, so no fallback is kept.
@MainActor
public enum ScreenCapture {
    /// Window IDs of the Fn overlay to always exclude from captures.
    ///
    /// The overlay owner syncs this list. It is merged with the per-call
    /// `excludedWindowIDs` on every capture.
    public static var overlayExcludedWindowIDs: [CGWindowID] = []

    /// Captures the rectangle and returns PNG data.
    ///
    /// - Parameters:
    ///   - rect: The region in global AppKit screen coordinates.
    ///   - excludedWindowIDs: Extra window IDs to exclude, besides the overlay.
    /// - Returns: PNG-encoded image data of the region.
    public static func capturePNG(
        rect: CGRect,
        excludedWindowIDs: [CGWindowID] = []
    ) async throws -> Data {
        let image = try await captureImage(rect: rect, excludedWindowIDs: excludedWindowIDs)
        let data = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(
            data as CFMutableData, "public.png" as CFString, 1, nil
        ) else {
            throw ScreenCaptureError.encodingFailed
        }
        CGImageDestinationAddImage(destination, image, nil)
        guard CGImageDestinationFinalize(destination) else {
            throw ScreenCaptureError.encodingFailed
        }
        return data as Data
    }

    /// Captures the rectangle and returns the raw image.
    ///
    /// - Parameters:
    ///   - rect: The region in global AppKit screen coordinates.
    ///   - excludedWindowIDs: Extra window IDs to exclude, besides the overlay.
    /// - Returns: The captured region as a `CGImage`.
    public static func captureImage(
        rect: CGRect,
        excludedWindowIDs: [CGWindowID] = []
    ) async throws -> CGImage {
        guard rect.width > 0, rect.height > 0 else {
            throw ScreenCaptureError.emptyRect
        }
        guard let primary = NSScreen.screens.first else {
            throw ScreenCaptureError.noDisplay
        }
        let targetScreen = NSScreen.screens.first(where: { $0.frame.intersects(rect) })
            ?? NSScreen.main
            ?? primary
        let primaryHeight = primary.frame.height
        let quartzRect = Geometry.quartzRect(fromAppKit: rect, primaryScreenHeight: primaryHeight)

        let content: SCShareableContent
        do {
            content = try await SCShareableContent.current
        } catch {
            throw ScreenCaptureError.noShareableContent
        }
        guard !content.displays.isEmpty else {
            throw ScreenCaptureError.noDisplay
        }

        let displayID = targetScreen.deviceDescription[
            NSDeviceDescriptionKey("NSScreenNumber")
        ] as? CGDirectDisplayID
        let display = content.displays.first(where: { $0.displayID == displayID })
            ?? content.displays.first(where: { $0.frame.intersects(quartzRect) })
            ?? content.displays.first
        guard let display else {
            throw ScreenCaptureError.noDisplay
        }

        let excluded = Set(excludedWindowIDs + overlayExcludedWindowIDs)
        let excludedWindows = excluded.isEmpty ? [] : content.windows.filter {
            excluded.contains($0.windowID)
        }
        let filter = SCContentFilter(display: display, excludingWindows: excludedWindows)

        // SCStreamConfiguration.sourceRect is in the display's logical points.
        // SCDisplay.frame is global, so shift the global Quartz rect into it.
        let sourceRect = CGRect(
            x: quartzRect.minX - display.frame.minX,
            y: quartzRect.minY - display.frame.minY,
            width: quartzRect.width,
            height: quartzRect.height
        )
        let scale = max(1, targetScreen.backingScaleFactor)
        let pixels = Geometry.pixelRect(fromPoints: rect, scale: scale)
        let configuration = SCStreamConfiguration()
        configuration.sourceRect = sourceRect
        configuration.width = max(1, Int(pixels.width))
        configuration.height = max(1, Int(pixels.height))
        configuration.showsCursor = false
        configuration.scalesToFit = false

        return try await withCheckedThrowingContinuation { continuation in
            SCScreenshotManager.captureImage(
                contentFilter: filter,
                configuration: configuration
            ) { image, error in
                if let image {
                    continuation.resume(returning: image)
                } else {
                    continuation.resume(
                        throwing: ScreenCaptureError.captureFailed(
                            error?.localizedDescription ?? "unknown"
                        )
                    )
                }
            }
        }
    }
}
