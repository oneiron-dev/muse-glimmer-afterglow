import AppKit
import FnCapture

@main struct AfterglowFnMain {
    @MainActor static func main() {
        let application = NSApplication.shared
        let delegate = HelperDelegate()
        application.delegate = delegate
        application.setActivationPolicy(.accessory)
        application.run()
        withExtendedLifetime(delegate) {}
    }
}

@MainActor final class HelperDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var statusItem: NSStatusItem!
    private let statusLine = NSMenuItem(title: "Starting", action: nil, keyEquivalent: "")
    private var permissionItems: [FnPermission: NSMenuItem] = [:]
    private var modeItems: [FnKeyMonitor.Mode: NSMenuItem] = [:]
    private let monitor = FnKeyMonitor()
    private let recorder = AudioRecorder()
    private let overlay = LassoOverlay()
    private let pill = RecordingPill()
    private let paster = Paster()
    private var panel: FloatingPanel?
    private var server: ServerAddress?
    private var recording = false
    private var busy = false
    private var selection: CGRect?
    private var frontAppName = ""
    private var frontApp: NSRunningApplication?
    private var captureTask: Task<CropContext, Error>?
    private var requestTask: Task<Void, Never>?

    struct CropContext {
        let png: Data
        let ocr: [OCRText]
        let ax: AXContext
        let rect: CGRect
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        do { server = try ServerAddress(arguments: CommandLine.arguments, environment: ProcessInfo.processInfo.environment) }
        catch { pill.error("Check your server URL") }
        makeMenu()
        // Return from the event tap before starting or stopping audio hardware.
        monitor.onHoldBegan = { [weak self] in DispatchQueue.main.async { self?.begin() } }
        monitor.onHoldEnded = { [weak self] in DispatchQueue.main.async { self?.finish() } }
        monitor.onHoldCancelled = { [weak self] in DispatchQueue.main.async { self?.cancel() } }
        recorder.onLevel = { [weak self] level in
            DispatchQueue.main.async { self?.pill.level(level) }
        }
        recorder.onAutoStop = { [weak self] in
            DispatchQueue.main.async { self?.finish() }
        }
        overlay.onSelection = { [weak self] rect in self?.select(rect) }
        refresh()
    }

    func applicationWillTerminate(_ notification: Notification) {
        monitor.stop(); recorder.cancel(); captureTask?.cancel(); requestTask?.cancel()
        overlay.hide(); panel?.close(); pill.hide()
    }

    func menuWillOpen(_ menu: NSMenu) { refresh() }

    private func makeMenu() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        statusItem.button?.image = horizonImage()
        statusItem.button?.toolTip = "Afterglow fn"
        let menu = NSMenu()
        menu.delegate = self
        menu.addItem(statusLine)
        menu.addItem(.separator())
        for permission in FnPermission.allCases {
            let item = NSMenuItem(title: permissionTitle(permission), action: #selector(requestPermission(_:)), keyEquivalent: "")
            item.target = self; item.representedObject = permission.rawValue
            permissionItems[permission] = item; menu.addItem(item)
        }
        menu.addItem(.separator())
        for mode in [FnKeyMonitor.Mode.function, .rightOption] {
            let item = NSMenuItem(title: mode == .function ? "Hold fn" : "Hold Right Option",
                                  action: #selector(changeMode(_:)), keyEquivalent: "")
            item.target = self; item.representedObject = mode.rawValue
            modeItems[mode] = item; menu.addItem(item)
        }
        menu.addItem(.separator())
        let open = NSMenuItem(title: "Open Afterglow", action: #selector(openAfterglow), keyEquivalent: "")
        open.target = self; menu.addItem(open)
        let quit = NSMenuItem(title: "Quit", action: #selector(quit), keyEquivalent: "q")
        quit.target = self; menu.addItem(quit)
        statusItem.menu = menu
    }

    private func permissionTitle(_ permission: FnPermission) -> String {
        switch permission {
        case .microphone: return "Microphone"
        case .screenCapture: return "Screen Recording"
        case .accessibility: return "Accessibility"
        case .inputMonitoring: return "Input Monitoring"
        }
    }

    private func refresh() {
        for permission in FnPermission.allCases {
            let allowed = Permissions.status(for: permission).isAuthorized
            permissionItems[permission]?.title = "\(permissionTitle(permission)): \(allowed ? "Allowed" : "Allow…")"
        }
        for (mode, item) in modeItems { item.state = mode == monitor.mode ? .on : .off }
        let keysAllowed = Permissions.status(for: .accessibility).isAuthorized && Permissions.status(for: .inputMonitoring).isAuthorized
        if keysAllowed && !monitor.isMonitoring {
            do { try monitor.start() }
            catch { statusLine.title = "Could not watch your key"; return }
        } else if !keysAllowed && monitor.isMonitoring {
            cancel(); monitor.stop()
        }
        if server == nil { statusLine.title = "Check your server URL" }
        else if !keysAllowed { statusLine.title = "Allow key access below" }
        else if !Permissions.status(for: .microphone).isAuthorized { statusLine.title = "Allow your microphone below" }
        else { statusLine.title = busy ? "Your agent is working" : "Ready: hold \(monitor.mode == .function ? "fn" : "Right Option")" }
    }

    @objc private func requestPermission(_ sender: NSMenuItem) {
        guard let raw = sender.representedObject as? String, let permission = FnPermission(rawValue: raw) else { return }
        Task { _ = await Permissions.request(permission); refresh() }
    }
    @objc private func changeMode(_ sender: NSMenuItem) {
        guard let raw = sender.representedObject as? String, let mode = FnKeyMonitor.Mode(rawValue: raw) else { return }
        cancel(); monitor.mode = mode; refresh()
    }
    @objc private func openAfterglow() { if let server { NSWorkspace.shared.open(server.base) } }
    @objc private func quit() { NSApp.terminate(nil) }

    private func begin() {
        guard !recording, !busy, server != nil else { return }
        guard Permissions.status(for: .microphone).isAuthorized else { pill.error("Allow your microphone first"); return }
        selection = nil; captureTask = nil
        frontApp = NSWorkspace.shared.frontmostApplication
        frontAppName = frontApp?.localizedName ?? ""
        do {
            try recorder.start()
            recording = true
            pill.show("Listening", metering: true)
            overlay.show()
        } catch { pill.error("Could not start your microphone") }
    }

    private func select(_ rect: CGRect) {
        guard recording else { return }
        selection = rect
        captureTask?.cancel()
        let ids = overlay.windowIDs + [pill.windowID]
        guard Permissions.status(for: .screenCapture).isAuthorized else {
            cancel(); pill.error("Allow Screen Recording first"); return
        }
        // Hide the hit-test window so AX sees the selected app, not our overlay.
        overlay.hide()
        let ax = AXInspector.inspect(rect: rect)
        captureTask = Task {
            let png = try await ScreenCapture.capturePNG(rect: rect, excludedWindowIDs: ids)
            try Task.checkCancellation()
            let ocr = try await TextRecognizer.recognize(png: png)
            try Task.checkCancellation()
            return CropContext(png: png, ocr: ocr, ax: ax, rect: rect)
        }
    }

    private func cancel() {
        guard recording else { return }
        recording = false
        recorder.cancel(); captureTask?.cancel(); captureTask = nil
        overlay.hide(); pill.hide(); selection = nil
    }

    private func finish() {
        guard recording, let server else { return }
        recording = false; overlay.hide()
        guard let audio = recorder.stop() else { pill.hide(); return }
        let crop = captureTask
        captureTask = nil
        let anchor = selection ?? CGRect(origin: NSEvent.mouseLocation, size: CGSize(width: 1, height: 1))
        let targetApp = frontApp
        let appName = frontAppName
        busy = true; pill.show("Your agent is working"); refresh()
        requestTask = Task { [weak self] in
            guard let self else { return }
            defer { busy = false; requestTask = nil; refresh() }
            do {
                let context = try await crop?.value
                try Task.checkCancellation()
                let request = AmbientRequest(audio: audio, crop: context?.png, ocr: context?.ocr,
                                             ax: context?.ax, appName: appName, screenRect: context?.rect)
                let reply = try await AmbientClient(address: server).send(request)
                try Task.checkCancellation()
                switch reply.mode {
                case "paste":
                    guard let text = reply.text else { throw HelperError.message("Your agent returned no text") }
                    // Never paste into a different app if the user switched while waiting.
                    guard NSWorkspace.shared.frontmostApplication?.processIdentifier == targetApp?.processIdentifier else {
                        throw HelperError.message("Focus changed. Please try again")
                    }
                    paster.paste(text); pill.hide()
                case "explain":
                    guard let path = reply.url else { throw HelperError.message("Your agent returned no explanation") }
                    let url = try server.explanation(path)
                    panel?.close()
                    let explanation = FloatingPanel(url: url)
                    explanation.title = "Your agent"
                    panel = explanation
                    explanation.show(beside: anchor); pill.hide()
                default: throw HelperError.message("Your agent returned an unknown reply")
                }
            } catch is CancellationError { pill.hide() }
            catch let error as HelperError { pill.error(error.localizedDescription) }
            catch { pill.error("Could not reach your agent or capture") }
        }
    }

    private func horizonImage() -> NSImage {
        let image = NSImage(size: NSSize(width: 20, height: 20), flipped: false) { _ in
            NSColor.black.setStroke(); NSColor.black.setFill()
            let circle = NSBezierPath(ovalIn: CGRect(x: 3, y: 7, width: 14, height: 12))
            circle.lineWidth = 1.5; circle.stroke()
            // Clear the lower part of the circle, then draw the horizon and two bands.
            NSColor.clear.setFill()
            CGRect(x: 1, y: 0, width: 18, height: 12).fill(using: .copy)
            NSColor.black.setFill()
            for (y, width) in [(11.0, 18.0), (7.0, 14.0), (3.0, 10.0)] {
                NSBezierPath(roundedRect: CGRect(x: (20 - width) / 2, y: y, width: width, height: 1.5), xRadius: 0.75, yRadius: 0.75).fill()
            }
            return true
        }
        image.isTemplate = true
        return image
    }
}
