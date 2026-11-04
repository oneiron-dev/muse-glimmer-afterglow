import AppKit
import QuartzCore
import FnCapture

/// Real file-URL drag. It never moves the bundle or promises a microphone drop target.
@MainActor private final class BundleDragView: NSImageView, NSDraggingSource {
    override func mouseDown(with event: NSEvent) {
        guard Bundle.main.bundleURL.pathExtension == "app", let image else { return }
        let item = NSDraggingItem(pasteboardWriter: Bundle.main.bundleURL as NSURL)
        item.setDraggingFrame(bounds, contents: image)
        beginDraggingSession(with: [item], event: event, source: self)
    }
    func draggingSession(_ session: NSDraggingSession, sourceOperationMaskFor context: NSDraggingContext) -> NSDragOperation { .copy }
    override var acceptsFirstResponder: Bool { false }
}

@MainActor final class PermissionGuideView: PermissionSurface {
    init(permission: FnPermission, arrowRight: Bool = false) {
        super.init(frame: CGRect(x: 0, y: 0, width: 296, height: 240))
        mark()
        text("AFTERGLOW FN", x: 60, y: 30, width: 200, size: 11, bold: true)
        text("Turn on Afterglow fn", x: 24, y: 65, width: 248, size: 18, bold: true)
        let reason: String
        switch permission {
        case .accessibility: reason = "Let your agent paste your words into your apps."
        case .inputMonitoring: reason = "Let your agent hear fn and Right Option."
        case .screenCapture: reason = "Attach only the area you circle, without system audio."
        case .microphone: reason = "Record your voice only while you hold your key."
        }
        text(reason, x: 24, y: 98, width: 248, height: 44)
        if permission == .accessibility || permission == .inputMonitoring || permission == .screenCapture {
            let icon = BundleDragView(frame: CGRect(x: 24, y: 155, width: 44, height: 44))
            icon.image = Bundle.main.bundleURL.pathExtension == "app" ? NSWorkspace.shared.icon(forFile: Bundle.main.bundleURL.path) : PermissionArtwork.icon
            icon.imageScaling = .scaleProportionallyUpOrDown
            icon.toolTip = "Drag this app into the list if it is missing"
            addSubview(icon)
            text("If missing, try dragging this app into the list.", x: 80, y: 158, width: 184, size: 12)
        } else {
            text("Use the switch in Microphone Settings.", x: 24, y: 155, width: 248, size: 12)
        }
        text(arrowRight ? "→  Toward the permission list" : "←  Toward the permission list", x: 24, y: 210, width: 248, size: 12).textColor = Self.warm
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
}

@MainActor final class PermissionGuidePanel {
    private let panel: NSPanel
    private var timer: Timer?
    private var observer: NSObjectProtocol?
    private var permission: FnPermission?
    private var sourceFrame = CGRect.zero
    private var animateNext = false
    private var arrowRight = false
    var onVisibilityChanged: (() -> Void)?
    var isVisible: Bool { panel.isVisible }

    init() {
        panel = NSPanel(contentRect: CGRect(x: 0, y: 0, width: 296, height: 240),
                        styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.backgroundColor = .clear; panel.isOpaque = false; panel.hasShadow = true
        panel.level = .floating; panel.hidesOnDeactivate = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
    }
    func show(for permission: FnPermission, from frame: CGRect) {
        stop()
        self.permission = permission; sourceFrame = frame; animateNext = true
        panel.contentView = PermissionGuideView(permission: permission)
        observer = NSWorkspace.shared.notificationCenter.addObserver(forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.frontChanged() }
        }
        frontChanged()
    }
    func stop() {
        timer?.invalidate(); timer = nil
        if let observer { NSWorkspace.shared.notificationCenter.removeObserver(observer) }
        observer = nil; permission = nil
        hide()
    }
    private func frontChanged() {
        timer?.invalidate(); timer = nil
        guard permission != nil, NSWorkspace.shared.frontmostApplication?.bundleIdentifier == "com.apple.systempreferences" else { hide(); return }
        track()
        let t = Timer(timeInterval: 0.15, repeats: true) { [weak self] _ in MainActor.assumeIsolated { self?.track() } }
        RunLoop.main.add(t, forMode: .common); timer = t
    }
    private func hide() {
        guard panel.isVisible else { return }
        panel.orderOut(nil); onVisibilityChanged?()
    }
    /// Geometry only. No titles, screenshots, AX scraping or exact-row claims.
    private func settingsFrame() -> CGRect? {
        guard let app = NSWorkspace.shared.frontmostApplication,
              app.bundleIdentifier == "com.apple.systempreferences",
              let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] else { return nil }
        for info in windows {
            guard (info[kCGWindowOwnerPID as String] as? Int32) == app.processIdentifier,
                  (info[kCGWindowLayer as String] as? Int) == 0,
                  let dictionary = info[kCGWindowBounds as String] as? [String: Any],
                  let cg = CGRect(dictionaryRepresentation: dictionary as CFDictionary), cg.width > 200, cg.height > 200 else { continue }
            // Window-server coordinates use the main display's top-left origin.
            let top = CGDisplayBounds(CGMainDisplayID()).height
            return CGRect(x: cg.minX, y: top - cg.maxY, width: cg.width, height: cg.height)
        }
        return nil
    }
    private func track() {
        guard let frame = settingsFrame(),
              let screen = NSScreen.screens.first(where: { $0.visibleFrame.intersects(frame) }),
              screen.visibleFrame.intersection(frame).width > 200,
              screen.visibleFrame.intersection(frame).height > 200 else { hide(); return }
        let visible = screen.visibleFrame
        let size = panel.frame.size
        let gap: CGFloat = 12
        let rightFits = frame.maxX + gap + size.width <= visible.maxX
        let leftFits = frame.minX - gap - size.width >= visible.minX
        guard rightFits || leftFits else { hide(); return }
        let point = CGPoint(x: rightFits ? frame.maxX + gap : frame.minX - gap - size.width,
                            y: min(max(frame.midY - size.height / 2, visible.minY), visible.maxY - size.height))
        if arrowRight != !rightFits, let permission {
            arrowRight = !rightFits
            panel.contentView = PermissionGuideView(permission: permission, arrowRight: arrowRight)
        }
        let destination = CGRect(origin: point, size: size)
        if animateNext {
            animateNext = false
            let reduced = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
            panel.setFrame(reduced ? destination : CGRect(x: sourceFrame.midX - size.width / 2, y: sourceFrame.midY - size.height / 2, width: size.width, height: size.height), display: false)
            panel.alphaValue = 0; panel.orderFrontRegardless()
            NSAnimationContext.runAnimationGroup { context in
                context.duration = 0.3
                panel.animator().alphaValue = 1
            }
            if !reduced, let layer = panel.contentView?.layer {
                // Animate the actual panel from the setup window; a short spring settles its contents.
                NSAnimationContext.runAnimationGroup { context in
                    context.duration = 0.3; context.timingFunction = CAMediaTimingFunction(name: .easeOut)
                    panel.animator().setFrame(destination, display: true)
                }
                let spring = CASpringAnimation(keyPath: "transform.scale")
                spring.fromValue = 0.94; spring.toValue = 1
                spring.stiffness = 400; spring.damping = 30; spring.duration = 0.3
                layer.add(spring, forKey: "setupEntrance")
            }
            onVisibilityChanged?()
        } else {
            panel.setFrame(destination, display: false)
            if !panel.isVisible { panel.alphaValue = 1; panel.orderFrontRegardless(); onVisibilityChanged?() }
        }
    }
}
