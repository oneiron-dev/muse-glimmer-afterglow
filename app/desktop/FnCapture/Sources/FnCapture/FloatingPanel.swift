import AppKit
import CoreGraphics
import WebKit

/// A nonactivating floating panel that shows a URL beside a screen rect.
///
/// The panel hosts a `WKWebView`, never activates the app, and stays within
/// the screen that contains the anchor rect. It closes on Escape, on any
/// outside click, on the close button, or on explicit `close()`; every path
/// funnels through `windowWillClose` so `onClose` fires exactly once per show.
/// Escape is caught by local and global key monitors while visible, so it
/// works before the panel is clicked and when the web view consumes keys; the
/// local monitor consumes Escape, the global monitor only observes.
///
/// Positioning for `show(beside:)` tries the right side of the anchor (top
/// aligned, 12pt gap), then the left side, then below, then above, and finally
/// clamps with `Geometry.clamped` to the screen `visibleFrame`. The panel is
/// resizable with `minimumSize` (360x420).
///
/// Create, show, and close on the main actor. The final release should also
/// happen on the main thread.
@MainActor
public final class FloatingPanel: NSObject {
    /// The minimum panel size, in points.
    public static let minimumSize = CGSize(width: 360, height: 420)
    /// The initial panel size for the first `show(beside:)`, in points.
    public static let defaultSize = CGSize(width: 400, height: 480)
    /// The gap between the anchor rect and the panel, in points.
    public static let besideGap: CGFloat = 12

    /// Creates a hidden panel that loads the given URL.
    ///
    /// - Parameter url: The URL loaded into the web view.
    public init(url: URL) {
        self.url = url
        super.init()
        let panel = FloatingPanelWindow(
            contentRect: NSRect(origin: .zero, size: Self.defaultSize),
            styleMask: [.titled, .closable, .resizable, .nonactivatingPanel],
            backing: .buffered,
            defer: true
        )
        panel.title = "Explanation"
        panel.minSize = Self.minimumSize
        panel.isFloatingPanel = true
        panel.level = .floating
        panel.collectionBehavior = [.moveToActiveSpace, .fullScreenAuxiliary]
        panel.isReleasedWhenClosed = false
        panel.hidesOnDeactivate = false
        panel.isMovableByWindowBackground = false
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        let webView = WKWebView(frame: panel.contentView?.bounds ?? .zero, configuration: configuration)
        webView.autoresizingMask = [.width, .height]
        webView.allowsBackForwardNavigationGestures = true
        panel.contentView?.addSubview(webView)
        self.panel = panel
        self.webView = webView
        panel.onEscape = { [weak self] in
            guard let self else { return }
            MainActor.assumeIsolated {
                self.close()
            }
        }
        webView.load(URLRequest(url: url))
    }

    /// The URL currently loaded in the web view.
    public private(set) var url: URL

    /// Loads a new URL into the web view.
    ///
    /// - Parameter url: The URL to load.
    public func load(_ url: URL) {
        self.url = url
        webView.load(URLRequest(url: url))
    }

    /// Reloads the current page.
    public func reload() {
        webView.reload()
    }

    /// Called on the main actor each time the panel finishes closing.
    ///
    /// This fires for Escape, outside clicks, the close button, and explicit
    /// `close()`. It is kept across show/close cycles.
    public var onClose: (() -> Void)?

    /// Whether the panel is currently shown.
    public private(set) var isVisible: Bool = false

    /// The underlying window, for advanced use such as frame inspection.
    public var window: NSWindow {
        panel
    }

    /// The panel title. Defaults to `"Explanation"`.
    public var title: String {
        get { panel.title }
        set { panel.title = newValue }
    }

    /// Shows the panel beside the given rect and starts dismiss monitors.
    ///
    /// While visible, outside clicks and Escape dismiss the panel.
    ///
    /// - Parameter rect: The anchor rect in Cocoa global screen coordinates
    ///   (bottom-left origin), as reported by `LassoOverlay.onSelection`.
    ///
    /// The screen is the first screen whose frame intersects `rect`, falling
    /// back to the main screen. If already visible, the panel moves to the new
    /// position instead of stacking monitors.
    public func show(beside rect: CGRect) {
        let screen = NSScreen.screens.first(where: { $0.frame.intersects(rect) })
            ?? NSScreen.main
            ?? NSScreen.screens.first
        let visible = screen?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        var size = panel.frame.size
        if size.width <= 0 || size.height <= 0 {
            size = Self.defaultSize
        }
        size.width = max(size.width, Self.minimumSize.width)
        size.height = max(size.height, Self.minimumSize.height)
        size.width = min(size.width, visible.width)
        size.height = min(size.height, visible.height)

        let gap = Self.besideGap
        let candidates = [
            // Right, top aligned.
            CGRect(x: rect.maxX + gap, y: rect.maxY - size.height, width: size.width, height: size.height),
            // Left, top aligned.
            CGRect(x: rect.minX - size.width - gap, y: rect.maxY - size.height, width: size.width, height: size.height),
            // Below, left aligned.
            CGRect(x: rect.minX, y: rect.minY - size.height - gap, width: size.width, height: size.height),
            // Above, left aligned.
            CGRect(x: rect.minX, y: rect.maxY + gap, width: size.width, height: size.height),
        ]
        let visibleCG = CGRect(
            x: visible.origin.x, y: visible.origin.y,
            width: visible.size.width, height: visible.size.height
        )
        var frame = candidates.first(where: { visibleCG.contains(CGRect(x: $0.minX, y: $0.minY, width: $0.width, height: $0.height)) })
            ?? candidates[0]
        frame = Geometry.clamped(frame, to: visibleCG)
        panel.setFrame(NSRect(x: frame.minX, y: frame.minY, width: frame.width, height: frame.height), display: true)

        if isVisible {
            panel.orderFrontRegardless()
            return
        }
        isVisible = true
        panel.delegate = self
        startDismissMonitors()
        panel.orderFrontRegardless()
    }

    /// Closes the panel.
    ///
    /// This triggers `windowWillClose`, which performs cleanup and fires
    /// `onClose`. Safe to call when already hidden.
    public func close() {
        guard isVisible else { return }
        panel.close()
    }

    // MARK: - Private

    private var panel: FloatingPanelWindow!
    private var webView: WKWebView!
    private var localMonitor: Any?
    private var globalMonitor: Any?
    private var localKeyMonitor: Any?
    private var globalKeyMonitor: Any?

    private func startDismissMonitors() {
        stopDismissMonitors()
        localMonitor = NSEvent.addLocalMonitorForEvents(
            matching: [.leftMouseDown, .rightMouseDown, .otherMouseDown]
        ) { [weak self] event in
            guard let self else { return event }
            guard Thread.isMainThread else { return event }
            let outside = MainActor.assumeIsolated {
                self.isOutsidePanel(event: event)
            }
            if outside {
                DispatchQueue.main.async { [weak self] in
                    guard let self else { return }
                    MainActor.assumeIsolated {
                        self.close()
                    }
                }
            }
            return event
        }
        globalMonitor = NSEvent.addGlobalMonitorForEvents(
            matching: [.leftMouseDown, .rightMouseDown, .otherMouseDown]
        ) { [weak self] _ in
            guard self != nil else { return }
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                MainActor.assumeIsolated {
                    self.close()
                }
            }
        }
        localKeyMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard let self else { return event }
            guard Thread.isMainThread else { return event }
            guard event.keyCode == 53 else { return event }
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                MainActor.assumeIsolated {
                    self.close()
                }
            }
            return nil
        }
        globalKeyMonitor = NSEvent.addGlobalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard self != nil else { return }
            guard Thread.isMainThread else { return }
            guard event.keyCode == 53 else { return }
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                MainActor.assumeIsolated {
                    self.close()
                }
            }
        }
    }

    private func stopDismissMonitors() {
        if let localMonitor {
            NSEvent.removeMonitor(localMonitor)
            self.localMonitor = nil
        }
        if let globalMonitor {
            NSEvent.removeMonitor(globalMonitor)
            self.globalMonitor = nil
        }
        if let localKeyMonitor {
            NSEvent.removeMonitor(localKeyMonitor)
            self.localKeyMonitor = nil
        }
        if let globalKeyMonitor {
            NSEvent.removeMonitor(globalKeyMonitor)
            self.globalKeyMonitor = nil
        }
    }

    private func isOutsidePanel(event: NSEvent) -> Bool {
        event.window != panel
    }

    private func finishClose() {
        guard isVisible else { return }
        isVisible = false
        stopDismissMonitors()
        panel.delegate = nil
        onClose?()
    }
}

// MARK: - NSWindowDelegate

extension FloatingPanel: NSWindowDelegate {
    /// Performs cleanup and fires `onClose` exactly once per show.
    ///
    /// This is the single funnel for Escape, outside clicks, the close button,
    /// and explicit `close()`.
    public func windowWillClose(_ notification: Notification) {
        finishClose()
    }
}

/// A nonactivating panel that routes Escape to `onEscape`.
///
/// Escape arrives either as `cancelOperation(_:)` or as a raw key down with
/// keycode 53; both paths call `onEscape` instead of beeping.
@MainActor
private final class FloatingPanelWindow: NSPanel {
    /// Called on the main actor when Escape is pressed.
    var onEscape: (() -> Void)?

    /// Routes the standard cancel action to `onEscape`.
    override func cancelOperation(_ sender: Any?) {
        onEscape?()
    }

    /// Routes Escape key downs to `onEscape`; all other keys behave normally.
    override func keyDown(with event: NSEvent) {
        if event.keyCode == 53 {
            onEscape?()
            return
        }
        super.keyDown(with: event)
    }
}
