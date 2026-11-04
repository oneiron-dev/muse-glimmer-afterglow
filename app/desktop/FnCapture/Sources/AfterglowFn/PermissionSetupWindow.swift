import AppKit
import FnCapture

@MainActor class PermissionSurface: NSView {
    static let dusk = NSColor(srgbRed: 0x2f / 255, green: 0x24 / 255, blue: 0x40 / 255, alpha: 0.92)
    static let cream = NSColor(srgbRed: 1, green: 0xe2 / 255, blue: 0xc2 / 255, alpha: 1)
    static let warm = NSColor(srgbRed: 1, green: 0xb5 / 255, blue: 0x8a / 255, alpha: 1)
    override var isFlipped: Bool { true }
    override init(frame: NSRect) {
        super.init(frame: frame)
        appearance = NSAppearance(named: .darkAqua)
        wantsLayer = true
        layer?.backgroundColor = Self.dusk.cgColor
        layer?.cornerRadius = 14
        layer?.masksToBounds = true
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    @discardableResult func text(_ string: String, x: CGFloat, y: CGFloat, width: CGFloat,
                                size: CGFloat = 13, bold: Bool = false, height: CGFloat = 42) -> NSTextField {
        let label = NSTextField(wrappingLabelWithString: string)
        label.frame = CGRect(x: x, y: y, width: width, height: height)
        label.font = .systemFont(ofSize: size, weight: bold ? .semibold : .regular)
        label.textColor = Self.cream
        addSubview(label)
        return label
    }
    @discardableResult func button(_ title: String, x: CGFloat, y: CGFloat, width: CGFloat,
                                  target: AnyObject?, action: Selector) -> NSButton {
        let button = NSButton(title: title, target: target, action: action)
        button.frame = CGRect(x: x, y: y, width: width, height: 32)
        button.bezelStyle = .rounded
        button.contentTintColor = Self.cream
        addSubview(button)
        return button
    }
    func mark() {
        let mark = AfterglowPermissionMark(frame: CGRect(x: 24, y: 24, width: 26, height: 26))
        addSubview(mark)
    }
}

@MainActor private final class AfterglowPermissionMark: NSView {
    override func draw(_ dirtyRect: NSRect) {
        PermissionSurface.cream.setFill()
        NSGraphicsContext.saveGraphicsState()
        NSBezierPath(ovalIn: CGRect(x: 3, y: 3, width: 20, height: 20)).addClip()
        CGRect(x: 3, y: 15, width: 20, height: 10).fill()
        CGRect(x: 3, y: 9, width: 20, height: 3).fill()
        CGRect(x: 3, y: 3, width: 20, height: 3).fill()
        NSGraphicsContext.restoreGraphicsState()
    }
}

@MainActor final class PermissionSetupView: PermissionSurface {
    var onAction: (() -> Void)?
    var onLater: (() -> Void)?
    var onRepair: (() -> Void)?
    var onReopen: (() -> Void)?
    var onInstructions: (() -> Void)?
    var onAlternate: (() -> Void)?
    private var lastPresentation = ""

    init() { super.init(frame: CGRect(x: 0, y: 0, width: 540, height: 740)) }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    func update(_ model: PermissionSetupModel) {
        let statuses = FnPermission.allCases.map { model.statuses[$0]?.rawValue ?? "unknown" }.joined(separator: ",")
        let asked = FnPermission.allCases.filter { model.asked.contains($0) }.map(\.rawValue).joined(separator: ",")
        let signature = "\(statuses)\(model.requesting as Any)\(asked)\(model.screenStep)\(model.keyHeard)\(model.keyReleased)\(model.keyDownHeard)\(model.keyUpHeard)\(model.audioArrived)\(model.message)\(model.reopening)\(model.keyMonitorFailed)"
        guard signature != lastPresentation else { return }
        lastPresentation = signature
        subviews.forEach { $0.removeFromSuperview() }
        mark()
        text("AFTERGLOW FN", x: 60, y: 30, width: 300, size: 11, bold: true)
        text("Set up Afterglow fn", x: 24, y: 70, width: 492, size: 25, bold: true)
        text("Hold fn to speak. Release to paste. Circle only when you want to attach part of your screen.",
             x: 24, y: 112, width: 492)
        let rows: [(FnPermission, String, String)] = [
            (.accessibility, "Paste into apps", "Paste your words and read the item inside a circle."),
            (.inputMonitoring, "Hear your fn key", "Detect fn and Right Option while you use other apps."),
            (.microphone, "Use the microphone", "Record only while you hold your key.")]
        for (index, row) in rows.enumerated() {
            let y = CGFloat(176 + index * 106)
            let allowed = model.allowed(row.0)
            text(allowed ? "✓" : "\(index + 1)", x: 26, y: y, width: 24, size: 17, bold: true)
            text(row.1, x: 60, y: y, width: 270, size: 15, bold: true, height: 24)
            text(row.2, x: 60, y: y + 27, width: 440, size: 12, height: 30)
            if model.active == row.0 {
                let action = button(model.requesting == nil ? model.actionTitle(row.0) : "Waiting for your choice…",
                                    x: 56, y: y + 57, width: 268, target: self, action: #selector(act))
                action.isEnabled = model.requesting == nil && !model.reopening
            } else {
                text(model.statusText(row.0), x: 345, y: y + 2, width: 170, size: 12)
            }
        }
        if model.screenStep {
            text("Attach this area? Allow Screen Recording.", x: 24, y: 494, width: 492, size: 15, bold: true)
            text("Dictation works without it. Afterglow captures only your circle, not system audio.", x: 24, y: 520, width: 492, size: 12)
            if model.active == .screenCapture {
                let b = button(model.actionTitle(.screenCapture), x: 20, y: 557, width: 240, target: self, action: #selector(act))
                b.isEnabled = model.requesting == nil && !model.reopening
            } else { text("Allowed. Circle again when you are ready.", x: 24, y: 561, width: 490, size: 13, bold: true) }
        } else if model.dictationReady {
            text("Hold fn and say something.", x: 24, y: 494, width: 490, size: 17, bold: true)
            text("Practice stays here. Nothing is sent to your agent.", x: 24, y: 522, width: 490, size: 12)
            let key = model.keyHeard && model.keyReleased ? "Key heard and released" : "Waiting for your key"
            let audio = model.audioArrived ? "Audio arrived" : "Waiting for your voice"
            text("\(key). \(audio).", x: 24, y: 548, width: 490, size: 12)
            text(model.keyDownHeard && model.keyUpHeard ? "Other key press and release heard. Combos cancel safely." : "Also press and release a letter to check combination cancellation.",
                 x: 24, y: 572, width: 492, size: 11)
        } else {
            text("Screen Recording", x: 24, y: 494, width: 490, size: 15, bold: true)
            text("Optional. Asked the first time you circle part of the screen.", x: 24, y: 522, width: 490, size: 12)
        }
        if model.dictationReady && !model.screenStep {
            text("Screen Recording: Optional. Asked the first time you circle part of the screen.",
                 x: 24, y: 596, width: 492, size: 11, height: 18)
        }
        text(model.message, x: 24, y: 620, width: model.canReopen ? 310 : 492, size: 11, height: 34)
        if model.canReopen {
            let b = button("Reopen to finish", x: 344, y: 606, width: 172, target: self, action: #selector(reopen))
            b.isEnabled = !model.reopening && model.requesting == nil
        }
        let repair = button("Fix a switch that is on but not working", x: 18, y: 656, width: 335, target: self, action: #selector(fix))
        repair.isBordered = false; repair.alignment = .left
        repair.isEnabled = model.requesting == nil && !model.reopening
        let later = button(model.screenStep ? "Not now" : "Later", x: 412, y: 687, width: 104, target: self, action: #selector(later))
        later.isEnabled = !model.reopening
        let instructions = button("Show instructions", x: 18, y: 691, width: 150, target: self, action: #selector(instructions))
        instructions.isBordered = false; instructions.alignment = .left
        let alternate = button("Try other Settings link", x: 174, y: 691, width: 215, target: self, action: #selector(alternate))
        alternate.isBordered = false; alternate.isEnabled = model.requesting == nil && !model.reopening
    }
    @objc private func act() { onAction?() }
    @objc private func later() { onLater?() }
    @objc private func fix() { onRepair?() }
    @objc private func reopen() { onReopen?() }
    @objc private func instructions() { onInstructions?() }
    @objc private func alternate() { onAlternate?() }
}

@MainActor final class PermissionSetupWindow: NSWindow {
    let setupView = PermissionSetupView()
    init() {
        super.init(contentRect: setupView.bounds, styleMask: [.titled, .closable, .fullSizeContentView], backing: .buffered, defer: false)
        title = "Set up Afterglow fn"
        titlebarAppearsTransparent = true
        titleVisibility = .hidden
        isReleasedWhenClosed = false
        backgroundColor = .clear; isOpaque = false
        contentView = setupView
        center()
    }
}
