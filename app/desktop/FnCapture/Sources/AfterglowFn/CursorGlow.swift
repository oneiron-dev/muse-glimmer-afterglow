import AppKit
import QuartzCore

/// The Afterglow mark beside the cursor while fn is held, in place of a full-screen dim.
///
/// It is the dock icon in small: the cream sun and its two light bands on a dusk
/// disc. While listening, the bands brighten with the voice, the disc glows and
/// two soft rings ripple out, and the mark follows the cursor. While the agent
/// works it looks different on purpose: the mark stays where you let go, a short
/// arc of light circles it, and after a moment a small note says it is working
/// and that esc stops it. The window never takes clicks or focus.
@MainActor final class CursorGlow {
    enum Phase { case hidden, listening, working }

    private static let size: CGFloat = 120
    /// The window is wider than the mark so the working note fits beside it.
    private static let width: CGFloat = 320
    private static let disc: CGFloat = 30
    /// Below and to the right of the arrow's tip, so the mark never covers what you point at.
    private static let offset = CGPoint(x: 22, y: -22)

    private static let dusk = CGColor(srgbRed: 0x2f / 255, green: 0x24 / 255, blue: 0x40 / 255, alpha: 1)
    private static let cream = CGColor(srgbRed: 1.0, green: 0xe2 / 255, blue: 0xc2 / 255, alpha: 1)
    private static let glow = CGColor(srgbRed: 1.0, green: 0.71, blue: 0.54, alpha: 1)

    private let panel: NSPanel
    /// Appear and disappear act on this layer (scale and opacity) only.
    private let stage = CALayer()
    private let ringBox = CALayer()
    private let rings = [CAShapeLayer(), CAShapeLayer()]
    private let discLayer = CALayer()
    private let bands = [CALayer(), CALayer()]
    private let track = CAShapeLayer()
    private let orbit = CAShapeLayer()
    private let note = CALayer()
    private let noteText = CATextLayer()
    private var noteWork: [DispatchWorkItem] = []
    private var ticker: Timer?
    private var target: Float = 0
    private var shown: Float = 0
    private(set) var phase: Phase = .hidden
    private var hideWork: DispatchWorkItem?

    private var reduceMotion: Bool { NSWorkspace.shared.accessibilityDisplayShouldReduceMotion }

    init() {
        let s = Self.size
        panel = NSPanel(contentRect: CGRect(x: 0, y: 0, width: Self.width, height: s),
                        styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.ignoresMouseEvents = true
        panel.level = NSWindow.Level(rawValue: NSWindow.Level.screenSaver.rawValue + 1)
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        panel.hidesOnDeactivate = false
        let root = NSView(frame: CGRect(x: 0, y: 0, width: Self.width, height: s))
        root.wantsLayer = true
        panel.contentView = root

        let center = CGPoint(x: s / 2, y: s / 2)
        stage.bounds = CGRect(x: 0, y: 0, width: s, height: s)
        stage.position = center
        stage.opacity = 0
        root.layer?.addSublayer(stage)

        ringBox.frame = stage.bounds
        stage.addSublayer(ringBox)
        let d = Self.disc
        for ring in rings {
            ring.path = Self.circle(d / 2)
            ring.fillColor = nil
            ring.strokeColor = Self.glow
            ring.lineWidth = 1.25
            ring.opacity = 0
            ring.position = center
            ringBox.addSublayer(ring)
        }

        discLayer.bounds = CGRect(x: 0, y: 0, width: d, height: d)
        discLayer.position = center
        discLayer.cornerRadius = d / 2
        discLayer.backgroundColor = Self.dusk
        discLayer.borderColor = CGColor(srgbRed: 1, green: 0.886, blue: 0.761, alpha: 0.22)
        discLayer.borderWidth = 0.5
        discLayer.shadowColor = Self.glow
        discLayer.shadowOffset = .zero
        discLayer.shadowOpacity = 0.4
        discLayer.shadowRadius = 4
        discLayer.shadowPath = CGPath(ellipseIn: discLayer.bounds, transform: nil)
        stage.addSublayer(discLayer)

        // The mark, drawn in the icon's own 24-unit box (y down), at one point per unit.
        let mark = CALayer()
        mark.bounds = CGRect(x: 0, y: 0, width: 24, height: 24)
        mark.position = CGPoint(x: d / 2, y: d / 2)
        mark.isGeometryFlipped = true
        let clip = CAShapeLayer()
        clip.path = CGPath(ellipseIn: CGRect(x: 4, y: 4, width: 16, height: 16), transform: nil)
        mark.mask = clip
        let sun = CALayer()
        sun.frame = CGRect(x: 4, y: 4, width: 16, height: 7.3)
        sun.backgroundColor = Self.cream
        mark.addSublayer(sun)
        for (band, frame) in zip(bands, [CGRect(x: 4, y: 12.6, width: 16, height: 2.6), CGRect(x: 4, y: 16.4, width: 16, height: 3.6)]) {
            band.frame = frame
            band.backgroundColor = Self.cream
            mark.addSublayer(band)
        }
        bands[0].opacity = 0.5
        bands[1].opacity = 0.24
        discLayer.addSublayer(mark)

        // Working: a faint track and a short arc of light that circles the disc.
        let r = d / 2 + 5
        for layer in [track, orbit] {
            layer.bounds = CGRect(x: 0, y: 0, width: 2 * r, height: 2 * r)
            layer.position = center
            layer.path = CGPath(ellipseIn: layer.bounds, transform: nil)
            layer.fillColor = nil
            layer.lineCap = .round
            layer.opacity = 0
            stage.addSublayer(layer)
        }
        track.strokeColor = Self.cream
        track.lineWidth = 1
        orbit.strokeColor = Self.glow
        orbit.lineWidth = 2
        orbit.strokeEnd = 0.24
        orbit.shadowColor = Self.glow
        orbit.shadowOpacity = 0.8
        orbit.shadowRadius = 3
        orbit.shadowOffset = .zero

        // The working note, beside the mark.
        let scale = NSScreen.main?.backingScaleFactor ?? 2
        note.backgroundColor = Self.dusk.copy(alpha: 0.94)
        note.cornerRadius = 11
        note.borderColor = Self.cream.copy(alpha: 0.18)
        note.borderWidth = 0.5
        note.opacity = 0
        noteText.contentsScale = scale
        noteText.font = NSFont.systemFont(ofSize: 12, weight: .medium)
        noteText.fontSize = 12
        noteText.foregroundColor = Self.cream
        noteText.alignmentMode = .left
        note.addSublayer(noteText)
        root.layer?.addSublayer(note)
    }

    private static func circle(_ r: CGFloat) -> CGPath {
        CGPath(ellipseIn: CGRect(x: -r, y: -r, width: 2 * r, height: 2 * r), transform: nil)
    }

    /// The window number, so screen captures leave the mark out.
    var windowID: CGWindowID { CGWindowID(panel.windowNumber) }

    /// Shows the mark at the cursor and follows it; the bands, glow and rings follow the voice.
    func listen() {
        target = 0; shown = 0
        stopWorking()
        enter(.listening)
        startRings()
    }

    /// The agent is working: the mark stays put, an arc circles it, and a note appears if it takes a moment.
    func work() {
        stopRings()
        enter(.working)
        startWorking()
        showNote("Working on it  ·  esc to stop", after: 1.5)
        showNote("Still working  ·  esc to stop", after: 15)
    }

    /// The voice level from the recorder, 0...1.
    func level(_ value: Float) { target = max(0, min(1, value)) }

    /// Fades the mark out (faster than it came in) and stops following the cursor.
    func hide() {
        guard phase != .hidden else { return }
        phase = .hidden
        animate(scale: reduceMotion ? 1 : 0.8, opacity: 0, duration: 0.14, spring: false)
        let work = DispatchWorkItem { [weak self] in
            guard let self, self.phase == .hidden else { return }
            self.ticker?.invalidate(); self.ticker = nil
            self.stopRings(); self.stopWorking(); self.hideNote()
            self.panel.orderOut(nil)
        }
        hideWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.16, execute: work)
    }

    // MARK: - Private

    private func enter(_ next: Phase) {
        hideWork?.cancel(); hideWork = nil
        let appearing = phase == .hidden
        phase = next
        // Listening follows the cursor; working stays where you let go.
        if appearing || next == .listening { follow() }
        if appearing {
            if !panel.isVisible { setNow(scale: reduceMotion ? 1 : 0.5, opacity: 0) }
            panel.orderFrontRegardless()
            animate(scale: 1, opacity: 1, duration: 0.2, spring: !reduceMotion)
        }
        if ticker == nil {
            let t = Timer(timeInterval: 1.0 / 60.0, repeats: true) { [weak self] _ in
                MainActor.assumeIsolated { self?.tick() }
            }
            RunLoop.main.add(t, forMode: .common)
            ticker = t
        }
    }

    private func tick() {
        guard phase == .listening else { return }
        follow()
        // Fast attack, slow release, so the light reads as a voice and not as noise.
        let k: Float = target > shown ? 0.45 : 0.1
        shown += (target - shown) * k
        let v = CGFloat(shown)
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        bands[0].opacity = Float(0.5 + 0.45 * v)
        bands[1].opacity = Float(0.24 + 0.56 * v)
        discLayer.shadowRadius = 4 + 9 * v
        discLayer.shadowOpacity = Float(0.4 + 0.5 * v)
        ringBox.opacity = Float(0.35 + 0.65 * v)
        CATransaction.commit()
    }

    private func follow() {
        let p = NSEvent.mouseLocation
        let s = Self.size
        panel.setFrameOrigin(CGPoint(x: (p.x + Self.offset.x - s / 2).rounded(), y: (p.y + Self.offset.y - s / 2).rounded()))
        // The mark sits in the window's left square; the note uses the rest.
    }

    private func startRings() {
        guard !reduceMotion else { return }
        let period: CFTimeInterval = 1.8
        let now = CACurrentMediaTime()
        for (i, ring) in rings.enumerated() {
            // Grow the circle itself, not its scale, so the line stays a hairline as it spreads.
            let grow = CABasicAnimation(keyPath: "path")
            grow.fromValue = Self.circle(Self.disc / 2)
            grow.toValue = Self.circle(Self.disc / 2 * 2.4)
            let fade = CAKeyframeAnimation(keyPath: "opacity")
            fade.values = [0.0, 0.8, 0.0]
            fade.keyTimes = [0, 0.15, 1]
            let group = CAAnimationGroup()
            group.animations = [grow, fade]
            group.duration = period
            group.timingFunction = CAMediaTimingFunction(controlPoints: 0.2, 0.7, 0.3, 1)
            group.repeatCount = .infinity
            group.beginTime = now + Double(i) * period / 2
            group.fillMode = .backwards
            ring.add(group, forKey: "ripple")
        }
    }

    private func stopRings() {
        rings.forEach { $0.removeAnimation(forKey: "ripple") }
    }

    private func startWorking() {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        bands[0].opacity = 0.5; bands[1].opacity = 0.24
        discLayer.shadowRadius = 5; discLayer.shadowOpacity = 0.55
        ringBox.opacity = 0
        track.opacity = 0.14
        orbit.opacity = 1
        CATransaction.commit()
        guard !reduceMotion else { return }
        let spin = CABasicAnimation(keyPath: "transform.rotation.z")
        spin.fromValue = 0
        spin.toValue = -2 * Double.pi
        spin.duration = 1.1
        spin.repeatCount = .infinity
        spin.timingFunction = CAMediaTimingFunction(name: .linear)
        orbit.add(spin, forKey: "spin")
        let breathe = CABasicAnimation(keyPath: "shadowRadius")
        breathe.fromValue = 3
        breathe.toValue = 10
        breathe.duration = 0.9
        breathe.autoreverses = true
        breathe.repeatCount = .infinity
        breathe.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
        discLayer.add(breathe, forKey: "breathe")
        let now = CACurrentMediaTime()
        for (i, band) in bands.enumerated() {
            let shimmer = CAKeyframeAnimation(keyPath: "opacity")
            shimmer.values = [0.2, 0.95, 0.2]
            shimmer.keyTimes = [0, 0.5, 1]
            shimmer.duration = 1.2
            shimmer.repeatCount = .infinity
            shimmer.beginTime = now + Double(i) * 0.3
            shimmer.fillMode = .backwards
            shimmer.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
            band.add(shimmer, forKey: "shimmer")
        }
    }

    private func stopWorking() {
        discLayer.removeAnimation(forKey: "breathe")
        bands.forEach { $0.removeAnimation(forKey: "shimmer") }
        orbit.removeAnimation(forKey: "spin")
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        orbit.opacity = 0; track.opacity = 0; ringBox.opacity = 1
        CATransaction.commit()
        hideNote()
    }

    private func showNote(_ text: String, after delay: Double) {
        let work = DispatchWorkItem { [weak self] in
            guard let self, self.phase == .working else { return }
            let font = NSFont.systemFont(ofSize: 12, weight: .medium)
            let w = ceil((text as NSString).size(withAttributes: [.font: font]).width) + 22
            let s = Self.size
            CATransaction.begin()
            CATransaction.setDisableActions(true)
            self.noteText.string = text
            self.note.frame = CGRect(x: s / 2 + Self.disc / 2 + 12, y: s / 2 - 11, width: w, height: 22)
            self.noteText.frame = CGRect(x: 11, y: 3, width: w - 22, height: 16)
            CATransaction.commit()
            let fade = CABasicAnimation(keyPath: "opacity")
            fade.fromValue = self.note.presentation()?.opacity ?? 0
            fade.toValue = 1
            fade.duration = 0.2
            fade.timingFunction = CAMediaTimingFunction(name: .easeOut)
            self.note.opacity = 1
            self.note.add(fade, forKey: "fade")
        }
        noteWork.append(work)
        DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: work)
    }

    private func hideNote() {
        noteWork.forEach { $0.cancel() }
        noteWork = []
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        note.removeAllAnimations()
        note.opacity = 0
        CATransaction.commit()
    }

    private func setNow(scale: CGFloat, opacity: Float) {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        stage.removeAllAnimations()
        stage.transform = CATransform3DMakeScale(scale, scale, 1)
        stage.opacity = opacity
        CATransaction.commit()
    }

    /// Animates the stage from where it is now, so a quick release and press never jumps.
    private func animate(scale: CGFloat, opacity: Float, duration: CFTimeInterval, spring: Bool) {
        let current = stage.presentation() ?? stage
        let fromScale = (current.value(forKeyPath: "transform.scale") as? CGFloat) ?? 1
        let fromOpacity = current.opacity
        let grow: CABasicAnimation
        if spring {
            let sp = CASpringAnimation(keyPath: "transform.scale")
            sp.mass = 1; sp.stiffness = 320; sp.damping = 22
            sp.duration = sp.settlingDuration
            grow = sp
        } else {
            grow = CABasicAnimation(keyPath: "transform.scale")
            grow.duration = duration
            grow.timingFunction = CAMediaTimingFunction(name: opacity > 0 ? .easeOut : .easeIn)
        }
        grow.fromValue = fromScale
        grow.toValue = scale
        let fade = CABasicAnimation(keyPath: "opacity")
        fade.fromValue = fromOpacity
        fade.toValue = opacity
        fade.duration = duration
        fade.timingFunction = CAMediaTimingFunction(name: opacity > 0 ? .easeOut : .easeIn)
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        stage.transform = CATransform3DMakeScale(scale, scale, 1)
        stage.opacity = opacity
        CATransaction.commit()
        stage.add(grow, forKey: "scale")
        stage.add(fade, forKey: "fade")
    }
}
