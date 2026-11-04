import AppKit

/// The same dusk-and-cream mark is used in the bundle icon and the setup guide.
@MainActor enum PermissionArtwork {
    static var icon: NSImage {
        NSImage(size: NSSize(width: 512, height: 512), flipped: false) { _ in
            NSColor(srgbRed: 0x2f / 255, green: 0x24 / 255, blue: 0x40 / 255, alpha: 1).setFill()
            NSBezierPath(roundedRect: CGRect(x: 8, y: 8, width: 496, height: 496), xRadius: 110, yRadius: 110).fill()
            NSGraphicsContext.saveGraphicsState()
            NSBezierPath(ovalIn: CGRect(x: 88, y: 88, width: 336, height: 336)).addClip()
            NSColor(srgbRed: 1, green: 0xe2 / 255, blue: 0xc2 / 255, alpha: 1).setFill()
            CGRect(x: 88, y: 266, width: 336, height: 160).fill()
            CGRect(x: 88, y: 178, width: 336, height: 52).fill()
            CGRect(x: 88, y: 88, width: 336, height: 54).fill()
            NSGraphicsContext.restoreGraphicsState()
            return true
        }
    }
}
