import AppKit
import CoreGraphics

/// A freeform lasso overlay that reports a bounding screen rect on mouse release.
///
/// While visible, the overlay shows one borderless transparent nonactivating
/// panel per screen, intercepts mouse input, and draws the in-progress
/// freeform path in `defaultRoseColor`. On mouse release it reports the
/// bounding box clipped to the owning screen, in Cocoa global screen
/// coordinates (bottom-left origin, as in `NSScreen.frame`), via `onSelection`.
///
/// Behavior notes:
/// - Panels never activate and never become key or main, so the front app
///   keeps focus for AX queries and paste.
/// - The overlay only intercepts input while visible. Callers typically call
///   `show()` when `FnKeyMonitor.onHoldBegan` fires and `hide()` when the hold
///   ends, cancels, or after handling the selection.
/// - There is no drag-to-move and no image crop here; this class only reports
///   the rect. Use `Geometry` helpers for Quartz or pixel conversions.
/// - Both width and height must reach `minimumSelectionSize`; otherwise the
///   press is ignored and `onSelection` is not called.
/// - Cross-screen drags are clipped to the owning screen so single-display
///   capture stays valid.
/// - The overlay does not hide itself after a selection; the caller must call
///   `hide()`. Starting a new press replaces the previous path.
/// - Screen changes while visible rebuild the per-screen panels.
/// - `windowIDs` exposes the overlay panel numbers so callers can exclude
///   them from screen captures.
///
/// Create, show, and hide on the main actor.
@MainActor
public final class LassoOverlay {
    /// The rose used for the freeform path.
    ///
    /// `NSColor.systemPink` softened slightly for overlay use.
    public static var defaultRoseColor: NSColor {
        NSColor.systemPink.withAlphaComponent(0.85)
    }

    /// Creates a hidden overlay. Call `show()` to intercept input.
    public init() {}

    /// Called on the main actor with the bounding rect when the mouse is released.
    ///
    /// The rect uses Cocoa global screen coordinates (bottom-left origin) and
    /// is clipped to the owning screen so single-display capture stays valid.
    /// It is not called unless both width and height reach `minimumSelectionSize`.
    public var onSelection: ((CGRect) -> Void)?

    /// Whether the overlay windows are currently shown.
    public private(set) var isVisible: Bool = false

    /// The overlay window numbers for screen-capture exclusion.
    ///
    /// Empty while hidden. Pass these to capture APIs that accept
    /// `kCGWindowListExcludeFromList`-style window lists.
    public var windowIDs: [CGWindowID] {
        windows.map { CGWindowID($0.windowNumber) }.filter { $0 != 0 }
    }

    /// The stroke colour for the freeform path. Defaults to `defaultRoseColor`.
    ///
    /// Changes apply to the next shown path and to any path already drawn.
    public var strokeColor: NSColor = LassoOverlay.defaultRoseColor {
        didSet {
            for view in views {
                view.strokeColor = strokeColor
            }
        }
    }

    /// The smallest bounding width and height that still counts, in points.
    ///
    /// Both dimensions must reach this value; otherwise the press is treated
    /// as "no drag" and `onSelection` is not called. Defaults to 4.
    public var minimumSelectionSize: CGFloat = 4 {
        didSet {
            for view in views {
                view.minimumSize = minimumSelectionSize
            }
        }
    }

    /// Whether `show()` switches the cursor to a crosshair. Defaults to true;
    /// a caller that marks the cursor itself can turn it off.
    public var usesCrosshair: Bool = true

    /// Whether the screen is dimmed slightly while the overlay is shown.
    /// Defaults to true. When false the overlay is invisible until a path is drawn
    /// (it still takes the mouse, through a fill one step above transparent).
    public var dimsScreen: Bool = true {
        didSet {
            for view in views {
                view.dimsScreen = dimsScreen
            }
        }
    }

    /// Shows one intercepting window per screen (with a crosshair cursor when `usesCrosshair`).
    ///
    /// Safe to call when already visible; the call is then ignored.
    public func show() {
        if isVisible { return }
        createWindows()
        isVisible = true
        if usesCrosshair {
            NSCursor.crosshair.push()
            didPushCursor = true
        }
        screenObserver = NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            guard let self else { return }
            MainActor.assumeIsolated {
                self.rebuildWhileVisible()
            }
        }
    }

    /// Hides all overlay windows, clears paths, and restores the cursor.
    ///
    /// Safe to call when already hidden. `onSelection` is kept.
    public func hide() {
        if !isVisible { return }
        destroyWindows()
        isVisible = false
        if didPushCursor {
            NSCursor.pop()
            didPushCursor = false
        }
        if let screenObserver {
            NotificationCenter.default.removeObserver(screenObserver)
            self.screenObserver = nil
        }
    }

    // MARK: - Private

    private var windows: [LassoPanel] = []
    private var views: [LassoView] = []
    private var screenObserver: NSObjectProtocol?
    private var didPushCursor: Bool = false

    private func rebuildWhileVisible() {
        guard isVisible else { return }
        destroyWindows()
        createWindows()
    }

    private func createWindows() {
        var newWindows: [LassoPanel] = []
        var newViews: [LassoView] = []
        for screen in NSScreen.screens {
            let frame = screen.frame
            let view = LassoView(
                frame: NSRect(origin: .zero, size: frame.size),
                strokeColor: strokeColor,
                minimumSize: minimumSelectionSize
            )
            view.dimsScreen = dimsScreen
            view.onMouseUpSelection = { [weak self] rect in
                guard let self else { return }
                self.onSelection?(rect)
            }
            let window = LassoPanel(
                contentRect: frame,
                styleMask: [.borderless, .nonactivatingPanel],
                backing: .buffered,
                defer: false
            )
            window.backgroundColor = .clear
            window.isOpaque = false
            window.hasShadow = false
            window.ignoresMouseEvents = false
            window.level = .screenSaver
            window.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle]
            window.isMovable = false
            window.isReleasedWhenClosed = false
            window.hidesOnDeactivate = false
            window.contentView = view
            view.autoresizingMask = [.width, .height]
            window.orderFrontRegardless()
            newWindows.append(window)
            newViews.append(view)
        }
        windows = newWindows
        views = newViews
    }

    private func destroyWindows() {
        for window in windows {
            window.orderOut(nil)
        }
        windows = []
        views = []
    }
}

/// A borderless nonactivating panel that never takes focus.
///
/// The overlay must not activate the helper or steal key status from the front
/// app, which would break AX queries and paste.
@MainActor
private final class LassoPanel: NSPanel {
    /// Never becomes key, so the front app keeps focus.
    override var canBecomeKey: Bool { false }
    /// Never becomes main.
    override var canBecomeMain: Bool { false }
}

/// A full-screen transparent view that tracks one freeform path.
///
/// Mouse down starts a path, dragging extends it, and mouse up reports the
/// bounding box clipped to the owning screen. Drawing is a softened rose
/// stroke with a faint fill over a slight dim.
@MainActor
private final class LassoView: NSView {
    /// The stroke colour for the path.
    var strokeColor: NSColor {
        didSet { needsDisplay = true }
    }
    /// Both width and height must reach this size to report, in points.
    var minimumSize: CGFloat
    /// Whether to draw the slight dim behind the path.
    var dimsScreen: Bool = true {
        didSet { needsDisplay = true }
    }
    /// Called with the screen-space bounding rect on qualifying mouse up.
    var onMouseUpSelection: ((CGRect) -> Void)?

    private var points: [NSPoint] = []
    private var isDrawing: Bool = false

    /// Creates a view covering one screen.
    init(frame frameRect: NSRect, strokeColor: NSColor, minimumSize: CGFloat) {
        self.strokeColor = strokeColor
        self.minimumSize = minimumSize
        super.init(frame: frameRect)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) is not supported")
    }

    /// Always transparent; the dim is drawn in `draw(_:)`.
    override var isOpaque: Bool { false }

    /// Accepts the first click even though the panel never becomes key.
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    /// Starts a new freeform path, replacing any previous path.
    override func mouseDown(with event: NSEvent) {
        let point = convert(event.locationInWindow, from: nil)
        points = [point]
        isDrawing = true
        needsDisplay = true
    }

    /// Extends the in-progress path.
    override func mouseDragged(with event: NSEvent) {
        guard isDrawing else { return }
        points.append(convert(event.locationInWindow, from: nil))
        needsDisplay = true
    }

    /// Finishes the path and reports its owning-screen rect when large enough.
    ///
    /// Tiny presses are cleared silently. Qualifying paths stay drawn until
    /// the next press or until the overlay is hidden. Cross-screen drags are
    /// clipped to the owning screen so single-display capture stays valid.
    override func mouseUp(with event: NSEvent) {
        guard isDrawing else { return }
        points.append(convert(event.locationInWindow, from: nil))
        isDrawing = false
        needsDisplay = true
        guard let bounds = boundingBox(), isQualifyingSelection(bounds) else {
            points = []
            needsDisplay = true
            return
        }
        let windowRect = convert(bounds, to: nil)
        guard let window else { return }
        let screenRect = window.convertToScreen(windowRect)
        let rect = CGRect(
            x: screenRect.origin.x,
            y: screenRect.origin.y,
            width: screenRect.size.width,
            height: screenRect.size.height
        )
        let owning = window.screen?.frame ?? window.frame
        let owningCG = CGRect(
            x: owning.origin.x,
            y: owning.origin.y,
            width: owning.size.width,
            height: owning.size.height
        )
        let clipped = rect.intersection(owningCG)
        guard !clipped.isNull, clipped.width >= minimumSize, clipped.height >= minimumSize else {
            points = []
            needsDisplay = true
            return
        }
        onMouseUpSelection?(clipped)
    }

    /// Draws the dim (or an invisible fill that still takes the mouse) plus the current freeform path.
    override func draw(_ dirtyRect: NSRect) {
        // Fully clear pixels let clicks fall through to the app below, so the
        // undimmed overlay keeps one alpha step (1/255) that no eye can see.
        NSColor.black.withAlphaComponent(dimsScreen ? 0.12 : 1.0 / 255.0).setFill()
        bounds.fill()
        guard !points.isEmpty else { return }
        if points.count == 1 {
            strokeColor.setFill()
            let dot = NSRect(x: points[0].x - 1.5, y: points[0].y - 1.5, width: 3, height: 3)
            NSBezierPath(ovalIn: dot).fill()
            return
        }
        let path = NSBezierPath()
        path.lineWidth = 2.5
        path.lineCapStyle = .round
        path.lineJoinStyle = .round
        path.move(to: points[0])
        for point in points.dropFirst() {
            path.line(to: point)
        }
        strokeColor.withAlphaComponent(0.08).setFill()
        let fill = path.copy() as? NSBezierPath ?? path
        fill.close()
        fill.fill()
        // A soft light around the line, so it reads as drawn with light.
        let glow = NSShadow()
        glow.shadowColor = strokeColor.withAlphaComponent(0.7)
        glow.shadowBlurRadius = 6
        glow.shadowOffset = .zero
        NSGraphicsContext.saveGraphicsState()
        glow.set()
        strokeColor.setStroke()
        path.stroke()
        NSGraphicsContext.restoreGraphicsState()
    }

    private func boundingBox() -> NSRect? {
        guard !points.isEmpty else { return nil }
        var minX = points[0].x
        var maxX = points[0].x
        var minY = points[0].y
        var maxY = points[0].y
        for point in points.dropFirst() {
            minX = min(minX, point.x)
            maxX = max(maxX, point.x)
            minY = min(minY, point.y)
            maxY = max(maxY, point.y)
        }
        return NSRect(x: minX, y: minY, width: maxX - minX, height: maxY - minY)
    }

    private func isQualifyingSelection(_ rect: NSRect) -> Bool {
        rect.width >= minimumSize && rect.height >= minimumSize
    }
}
