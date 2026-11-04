import AppKit
import FnCapture

@MainActor class PermissionSurface: NSView {
    static let dusk = NSColor(srgbRed: 0x2f / 255, green: 0x24 / 255, blue: 0x40 / 255, alpha: 0.92)
    static let cream = NSColor(srgbRed: 1, green: 0xe2 / 255, blue: 0xc2 / 255, alpha: 1)
    static let warm = NSColor(srgbRed: 1, green: 0xb5 / 255, blue: 0x8a / 255, alpha: 1)
    /// Text on a cream pill: the dusk itself, opaque.
    static let duskInk = NSColor(srgbRed: 0x2f / 255, green: 0x24 / 255, blue: 0x40 / 255, alpha: 1)
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
        let mark = AfterglowPermissionMark(frame: CGRect(x: 21, y: 21, width: 26, height: 26))
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

/// A pill button in the house colours: cream on dusk for the one action, quiet text for the rest.
@MainActor final class PermissionPill: NSButton {
    enum Kind { case primary, quiet, link }
    private let kind: Kind
    init(_ title: String, kind: Kind, target: AnyObject?, action: Selector) {
        self.kind = kind
        super.init(frame: .zero)
        self.target = target; self.action = action
        isBordered = false
        wantsLayer = true
        layer?.cornerRadius = kind == .link ? 0 : 14
        setTitle(title)
        refresh()
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    func setTitle(_ title: String) {
        let color: NSColor = kind == .primary ? PermissionSurface.duskInk : kind == .link ? PermissionSurface.warm : PermissionSurface.cream
        attributedTitle = NSAttributedString(string: title, attributes: [
            .font: NSFont.systemFont(ofSize: 12.5, weight: kind == .link ? .regular : .semibold),
            .foregroundColor: color.withAlphaComponent(isEnabled ? 1 : 0.5)])
        sizeToFit()
        let pad: CGFloat = kind == .link ? 0 : 28
        setFrameSize(NSSize(width: ceil(frame.width) + pad, height: kind == .link ? 18 : 28))
    }
    override var isEnabled: Bool { didSet { setTitle(attributedTitle.string); refresh() } }
    private func refresh() {
        switch kind {
        case .primary: layer?.backgroundColor = PermissionSurface.cream.withAlphaComponent(isEnabled ? 1 : 0.35).cgColor
        case .quiet: layer?.backgroundColor = PermissionSurface.cream.withAlphaComponent(0.1).cgColor
        case .link: layer?.backgroundColor = nil
        }
    }
    override func resetCursorRects() { if isEnabled { addCursorRect(bounds, cursor: .pointingHand) } }
}

/// The setup window's content: three steps in one card, one action at a time, status on the right, a quiet footer.
@MainActor final class PermissionSetupView: PermissionSurface {
    var onAction: (() -> Void)?
    var onLater: (() -> Void)?
    var onRepair: (() -> Void)?
    var onReopen: (() -> Void)?
    var onInstructions: (() -> Void)?
    var onAlternate: (() -> Void)?
    private var lastPresentation = ""
    private static let width: CGFloat = 440
    private static let pad: CGFloat = 24

    init() { super.init(frame: CGRect(x: 0, y: 0, width: Self.width, height: 420)) }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    func update(_ model: PermissionSetupModel) {
        let statuses = FnPermission.allCases.map { model.statuses[$0]?.rawValue ?? "unknown" }.joined(separator: ",")
        let asked = FnPermission.allCases.filter { model.asked.contains($0) }.map(\.rawValue).joined(separator: ",")
        let signature = "\(statuses)\(model.requesting as Any)\(asked)\(model.screenStep)\(model.keyHeard)\(model.keyReleased)\(model.keyDownHeard)\(model.keyUpHeard)\(model.audioArrived)\(model.message)\(model.reopening)\(model.keyMonitorFailed)"
        guard signature != lastPresentation else { return }
        lastPresentation = signature
        subviews.forEach { $0.removeFromSuperview() }
        let w = Self.width, x = Self.pad, inner = w - 2 * Self.pad
        let busy = model.requesting != nil || model.reopening

        // Header: the mark, the name, one line on what the helper does.
        mark()
        label("Afterglow fn", x: 56, y: 27, width: 200, size: 12, weight: .semibold, alpha: 0.7)
        var y: CGFloat = 64
        y = label("Set up dictation anywhere", x: x, y: y, width: inner, size: 20, weight: .semibold).frame.maxY + 6
        y = label("Hold fn to speak, let go to paste. Circle part of the screen only when you want to show it.",
                  x: x, y: y, width: inner, size: 12.5, alpha: 0.72).frame.maxY + 18

        // The three steps in one card.
        let rows: [(FnPermission, String, String)] = [
            (.accessibility, "Paste into apps", "Puts your words where you are typing."),
            (.inputMonitoring, "Hear your fn key", "Notices fn and Right Option in any app."),
            (.microphone, "Use the microphone", "Records only while you hold the key.")]
        // Rows are 58 pt; the active one grows to hold its button under the reason.
        let heights = rows.map { model.active == $0.0 ? CGFloat(96) : CGFloat(58) }
        box(x: x, y: y, width: inner, height: heights.reduce(0, +))
        var ry = y
        for (i, row) in rows.enumerated() {
            if i > 0 { hairline(x: x + 16, y: ry, width: inner - 32) }
            let allowed = model.allowed(row.0)
            let active = model.active == row.0
            addSubview(StepGlyph(frame: CGRect(x: x + 16, y: ry + 18, width: 22, height: 22), number: i + 1,
                                 state: allowed ? .done : active ? .active : .waiting))
            let textWidth = inner - 50 - (active ? 16 : 98)
            label(row.1, x: x + 50, y: ry + 12, width: textWidth, size: 13.5, weight: .semibold, alpha: allowed || active ? 1 : 0.6)
            label(row.2, x: x + 50, y: ry + 31, width: textWidth, size: 11.5, alpha: 0.6)
            if active {
                let title = model.requesting == row.0 ? "Waiting for you…" : model.actionTitle(row.0)
                let b = pill(title, .primary, #selector(act))
                b.isEnabled = !busy
                b.setFrameOrigin(NSPoint(x: x + 50, y: ry + 54))
            } else {
                let status = label(model.statusText(row.0), x: x + inner - 96, y: ry + 20, width: 82, size: 12,
                                   weight: allowed ? .semibold : .regular, alpha: allowed ? 1 : 0.5, align: .right)
                if allowed { status.textColor = Self.warm }
            }
            ry += heights[i]
        }
        y = ry + 14

        // Screen Recording: a quiet line until the first circle asks for it.
        if model.screenStep {
            let sh: CGFloat = 76
            box(x: x, y: y, width: inner, height: sh)
            label("Show the circled area too?", x: x + 16, y: y + 12, width: inner - 32, size: 13.5, weight: .semibold)
            label("Allow Screen Recording. Only the area you circle is captured, never system audio. Dictation works without it.",
                  x: x + 16, y: y + 31, width: inner - 170, size: 11.5, alpha: 0.6)
            if model.active == .screenCapture {
                let b = pill(model.requesting == .screenCapture ? "Waiting for you…" : model.actionTitle(.screenCapture), .primary, #selector(act))
                b.isEnabled = !busy
                b.setFrameOrigin(NSPoint(x: x + inner - 14 - b.frame.width, y: y + 24))
            } else {
                label("Allowed. Circle again.", x: x + inner - 164, y: y + 29, width: 150, size: 12, weight: .semibold, align: .right).textColor = Self.warm
            }
            y += sh + 14
        } else if model.dictationReady {
            // Practice: nothing leaves this window.
            y = label("Try it: hold fn and say a few words.", x: x, y: y, width: inner, size: 13.5, weight: .semibold).frame.maxY + 8
            y = check(model.keyHeard && model.keyReleased, "Key heard", x: x, y: y) + 4
            y = check(model.audioArrived, "Voice heard", x: x, y: y) + 6
            y = label("Practice stays here. Nothing is sent to your agent.", x: x, y: y, width: inner, size: 11.5, alpha: 0.55).frame.maxY + 14
        } else {
            y = label("Screen Recording is optional. You are asked the first time you circle part of the screen.",
                      x: x, y: y, width: inner, size: 11.5, alpha: 0.55).frame.maxY + 14
        }

        // A message, and the restart macOS needs for some switches.
        if !model.message.isEmpty || model.canReopen {
            let textWidth = model.canReopen ? inner - 150 : inner
            let m = label(model.message.isEmpty ? "macOS applies this switch after a restart." : model.message,
                          x: x, y: y, width: textWidth, size: 11.5, alpha: 0.85)
            m.textColor = Self.warm
            var bottom = m.frame.maxY
            if model.canReopen {
                let b = pill("Reopen to finish", .primary, #selector(reopen))
                b.isEnabled = !busy
                b.setFrameOrigin(NSPoint(x: x + inner - b.frame.width, y: y - 4))
                bottom = max(bottom, b.frame.maxY)
            }
            y = bottom + 14
        }

        // Footer: help on the left, Later on the right.
        hairline(x: 0, y: y, width: w)
        y += 12
        let fix = pill("A switch is on but not working?", .link, #selector(fix))
        fix.isEnabled = !busy
        fix.setFrameOrigin(NSPoint(x: x, y: y + 5))
        let help = pill("Instructions", .link, #selector(instructions))
        help.setFrameOrigin(NSPoint(x: fix.frame.maxX + 16, y: y + 5))
        if let active = model.active, model.asked.contains(active) {
            let other = pill("Open Settings another way", .link, #selector(alternate))
            other.isEnabled = !busy
            other.setFrameOrigin(NSPoint(x: x, y: y + 27))
        }
        let later = pill(model.screenStep ? "Not now" : model.dictationReady ? "Done" : "Later", .quiet, #selector(later))
        later.isEnabled = !model.reopening
        later.setFrameOrigin(NSPoint(x: w - x - later.frame.width, y: y))
        y += (model.active.map { model.asked.contains($0) } ?? false) ? 54 : 44

        resize(to: y)
    }

    // MARK: building blocks

    @discardableResult private func label(_ string: String, x: CGFloat, y: CGFloat, width: CGFloat, size: CGFloat,
                                          weight: NSFont.Weight = .regular, alpha: CGFloat = 1,
                                          align: NSTextAlignment = .left) -> NSTextField {
        let l = NSTextField(wrappingLabelWithString: string)
        l.font = .systemFont(ofSize: size, weight: weight)
        l.textColor = Self.cream.withAlphaComponent(alpha)
        l.alignment = align
        l.preferredMaxLayoutWidth = width
        let h = ceil(l.sizeThatFits(NSSize(width: width, height: .greatestFiniteMagnitude)).height)
        l.frame = CGRect(x: x, y: y, width: width, height: h)
        addSubview(l)
        return l
    }

    private func pill(_ title: String, _ kind: PermissionPill.Kind, _ action: Selector) -> PermissionPill {
        let b = PermissionPill(title, kind: kind, target: self, action: action)
        addSubview(b)
        return b
    }

    @discardableResult private func box(x: CGFloat, y: CGFloat, width: CGFloat, height: CGFloat) -> NSView {
        let v = NSView(frame: CGRect(x: x, y: y, width: width, height: height))
        v.wantsLayer = true
        v.layer?.backgroundColor = Self.cream.withAlphaComponent(0.06).cgColor
        v.layer?.cornerRadius = 12
        v.layer?.borderColor = Self.cream.withAlphaComponent(0.08).cgColor
        v.layer?.borderWidth = 0.5
        addSubview(v)
        return v
    }

    private func hairline(x: CGFloat, y: CGFloat, width: CGFloat) {
        let v = NSView(frame: CGRect(x: x, y: y, width: width, height: 0.5))
        v.wantsLayer = true
        v.layer?.backgroundColor = Self.cream.withAlphaComponent(0.1).cgColor
        addSubview(v)
    }

    private func check(_ done: Bool, _ text: String, x: CGFloat, y: CGFloat) -> CGFloat {
        addSubview(StepGlyph(frame: CGRect(x: x, y: y, width: 16, height: 16), number: 0, state: done ? .done : .waiting))
        return label(text, x: x + 24, y: y, width: 300, size: 12, alpha: done ? 1 : 0.6).frame.maxY
    }

    /// Fits the window to the content, keeping its top edge where it is.
    private func resize(to height: CGFloat) {
        let h = ceil(height)
        guard abs(frame.height - h) > 0.5 else { return }
        if let window {
            var f = window.frame
            let top = f.maxY
            f.size = window.frameRect(forContentRect: CGRect(x: 0, y: 0, width: Self.width, height: h)).size
            f.origin.y = top - f.height
            window.setFrame(f, display: true, animate: window.isVisible && !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion)
        } else {
            setFrameSize(NSSize(width: Self.width, height: h))
        }
    }

    @objc private func act() { onAction?() }
    @objc private func later() { onLater?() }
    @objc private func fix() { onRepair?() }
    @objc private func reopen() { onReopen?() }
    @objc private func instructions() { onInstructions?() }
    @objc private func alternate() { onAlternate?() }
}

/// A step's marker: a warm disc with a check when done, a cream ring with its number when it is next, faint otherwise.
@MainActor private final class StepGlyph: NSView {
    enum State { case done, active, waiting }
    private let number: Int
    private let state: State
    init(frame: CGRect, number: Int, state: State) {
        self.number = number; self.state = state
        super.init(frame: frame)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    override var isFlipped: Bool { true }
    override func draw(_ dirtyRect: NSRect) {
        let r = bounds.insetBy(dx: 0.75, dy: 0.75)
        switch state {
        case .done:
            PermissionSurface.warm.setFill()
            NSBezierPath(ovalIn: r).fill()
            let tick = NSBezierPath()
            let s = bounds.width
            tick.move(to: NSPoint(x: s * 0.28, y: s * 0.52))
            tick.line(to: NSPoint(x: s * 0.44, y: s * 0.67))
            tick.line(to: NSPoint(x: s * 0.73, y: s * 0.35))
            tick.lineWidth = max(1.5, s * 0.09)
            tick.lineCapStyle = .round; tick.lineJoinStyle = .round
            PermissionSurface.duskInk.setStroke()
            tick.stroke()
        case .active, .waiting:
            let alpha: CGFloat = state == .active ? 1 : 0.35
            let ring = NSBezierPath(ovalIn: r)
            ring.lineWidth = 1.25
            PermissionSurface.cream.withAlphaComponent(alpha).setStroke()
            ring.stroke()
            guard number > 0 else { return }
            let text = NSAttributedString(string: "\(number)", attributes: [
                .font: NSFont.systemFont(ofSize: bounds.width * 0.5, weight: .semibold),
                .foregroundColor: PermissionSurface.cream.withAlphaComponent(alpha)])
            let size = text.size()
            text.draw(at: NSPoint(x: (bounds.width - size.width) / 2, y: (bounds.height - size.height) / 2))
        }
    }
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
