import AppKit
import FnCapture

@MainActor final class RecordingPill {
    private let panel: NSPanel
    private let label = NSTextField(labelWithString: "Listening")
    private let meter = NSLevelIndicator()
    private var dismissal: DispatchWorkItem?

    init() {
        panel = NSPanel(contentRect: CGRect(x: 0, y: 0, width: 210, height: 44),
                        styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.level = .statusBar
        panel.ignoresMouseEvents = true
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        let content = NSVisualEffectView(frame: panel.contentRect(forFrameRect: panel.frame))
        content.material = .hudWindow; content.blendingMode = .behindWindow; content.state = .active
        content.wantsLayer = true; content.layer?.cornerRadius = 22
        content.layer?.masksToBounds = true
        label.frame = CGRect(x: 14, y: 13, width: 120, height: 18)
        label.font = .systemFont(ofSize: 12, weight: .medium)
        label.lineBreakMode = .byTruncatingTail
        meter.frame = CGRect(x: 140, y: 16, width: 54, height: 12)
        meter.levelIndicatorStyle = .continuousCapacity
        meter.minValue = 0; meter.maxValue = 1
        content.addSubview(label); content.addSubview(meter)
        panel.contentView = content
    }
    var windowID: CGWindowID { CGWindowID(panel.windowNumber) }
    func show(_ text: String, metering: Bool = false) {
        dismissal?.cancel(); dismissal = nil
        label.stringValue = text; label.toolTip = text
        meter.isHidden = !metering
        if metering { meter.doubleValue = 0 }
        let cursor = NSEvent.mouseLocation
        let screen = NSScreen.screens.first { $0.frame.contains(cursor) } ?? NSScreen.main
        let proposed = CGRect(x: cursor.x + 14, y: cursor.y - 58, width: 210, height: 44)
        panel.setFrame(screen.map { Geometry.clamped(proposed, to: $0.visibleFrame) } ?? proposed, display: true)
        panel.orderFrontRegardless()
    }
    func level(_ value: Float) { meter.doubleValue = Double(max(0, min(1, value))) }
    func error(_ message: String) {
        show(message)
        let work = DispatchWorkItem { [weak self] in self?.hide() }
        dismissal = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 3, execute: work)
    }
    func hide() { dismissal?.cancel(); dismissal = nil; panel.orderOut(nil) }
}
