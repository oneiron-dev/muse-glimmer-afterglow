import AppKit
import FnCapture

/// Offscreen views only. No delegate, permission probe, audio, network or ordered window.
@MainActor enum PermissionPreview {
    static func render(to directory: URL) throws {
        NSApp.setActivationPolicy(.prohibited)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let bitmap = NSBitmapImageRep(cgImage: PermissionArtwork.icon.cgImage(forProposedRect: nil, context: nil, hints: nil)!)
        try bitmap.representation(using: .png, properties: [:])!.write(to: directory.appendingPathComponent("permissions-icon.png"))
        let model = PermissionSetupModel(statuses: [.accessibility: .authorized, .inputMonitoring: .denied,
                                                    .microphone: .notDetermined, .screenCapture: .denied])
        let setup = PermissionSetupView()
        setup.update(model)
        try save(setup, to: directory.appendingPathComponent("permissions-setup.png"))
        try save(PermissionGuideView(permission: .inputMonitoring), to: directory.appendingPathComponent("permissions-guide.png"))
        model.statuses[.inputMonitoring] = .authorized
        model.statuses[.microphone] = .authorized
        model.keyHeard = true; model.keyReleased = true; model.audioArrived = true
        setup.update(model)
        try save(setup, to: directory.appendingPathComponent("permissions-practice.png"))
    }
    private static func save(_ view: NSView, to url: URL) throws {
        let window = NSWindow(contentRect: view.bounds, styleMask: .borderless, backing: .buffered, defer: false)
        window.contentView = view
        view.layoutSubtreeIfNeeded()
        guard let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else {
            throw HelperError.message("Could not create preview bitmap")
        }
        view.cacheDisplay(in: view.bounds, to: bitmap)
        guard let data = bitmap.representation(using: .png, properties: [:]) else {
            throw HelperError.message("Could not encode preview bitmap")
        }
        try data.write(to: url)
        window.contentView = nil
    }
}
