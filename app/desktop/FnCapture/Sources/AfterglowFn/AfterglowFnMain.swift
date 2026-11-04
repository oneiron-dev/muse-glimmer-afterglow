import AppKit
import FnCapture

@main struct AfterglowFnMain {
    @MainActor static func main() {
        let application = NSApplication.shared
        if let index = CommandLine.arguments.firstIndex(of: "--render-permissions") {
            guard CommandLine.arguments.indices.contains(index + 1) else {
                fputs("Pass an output directory for permission previews.\n", stderr)
                return
            }
            do { try PermissionPreview.render(to: URL(fileURLWithPath: CommandLine.arguments[index + 1])) }
            catch { fputs("Could not render permission previews.\n", stderr) }
            return
        }
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
    private lazy var setup: PermissionSetupController = {
        let controller = PermissionSetupController(statuses: Permissions.statuses())
        controller.onRefresh = { [weak self] in self?.refresh() }
        controller.onClose = { [weak self] in self?.cancelPractice() }
        controller.onReopen = { [weak self] in self?.restartHelper() }
        return controller
    }()
    private var practicing = false
    private var relaunching = false
    private var previousPermissions: [FnPermission: FnPermissionStatus] = [:]
    private var modeItems: [FnKeyMonitor.Mode: NSMenuItem] = [:]
    private let monitor = FnKeyMonitor()
    private let recorder = AudioRecorder()
    private let overlay = LassoOverlay()
    private let pill = RecordingPill()
    private let glow = CursorGlow()
    private let paster = Paster()
    private var panel: FloatingPanel?
    private var server: ServerAddress?
    private var recording = false
    private var busy = false
    private var selection: CGRect?
    private var frontAppName = ""
    private var frontApp: NSRunningApplication?
    /// What you said last, cleaned up, so a wrong guess (explain instead of paste) is one click to fix.
    private var lastWords: String?
    private let copyLast = NSMenuItem(title: "Copy your last words", action: nil, keyEquivalent: "")
    private var captureTask: Task<CropContext, Error>?
    private var requestTask: Task<Void, Never>?

    struct CropContext {
        let png: Data
        let ocr: [OCRText]
        let ax: AXContext
        let rect: CGRect
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        var environment = ProcessInfo.processInfo.environment
        if environment["AFTERGLOW_URL"] == nil, !CommandLine.arguments.contains("--url"),
           let saved = UserDefaults.standard.string(forKey: "AfterglowServerURL") {
            environment["AFTERGLOW_URL"] = saved
        }
        do {
            server = try ServerAddress(arguments: CommandLine.arguments, environment: environment)
            // A connection setting, not permission evidence. Also survives a system-managed reopen.
            UserDefaults.standard.set(server?.base.absoluteString, forKey: "AfterglowServerURL")
        }
        catch { pill.error("Check your server URL") }
        if let raw = UserDefaults.standard.string(forKey: "AfterglowHoldMode"), let mode = FnKeyMonitor.Mode(rawValue: raw) { monitor.mode = mode }
        makeMenu()
        // Return from the event tap before starting or stopping audio hardware.
        monitor.onHoldBegan = { [weak self] in DispatchQueue.main.async { self?.begin() } }
        monitor.onHoldEnded = { [weak self] in DispatchQueue.main.async { self?.finish() } }
        monitor.onHoldCancelled = { [weak self] in DispatchQueue.main.async { self?.cancel() } }
        recorder.onLevel = { [weak self] level in
            DispatchQueue.main.async {
                guard let self else { return }
                if self.practicing {
                    if level > 0.01 { self.setup.model.audioArrived = true; self.setup.redraw() }
                } else if self.recording { self.glow.level(level) }
            }
        }
        recorder.onAutoStop = { [weak self] in
            DispatchQueue.main.async { self?.finish() }
        }
        overlay.onSelection = { [weak self] rect in self?.select(rect) }
        monitor.onEscape = { [weak self] in DispatchQueue.main.async { self?.stopOnEscape() } }
        monitor.onKeyEvent = { [weak self] type in
            guard let self, self.setup.isPracticing else { return }
            if type == .keyDown { self.setup.model.keyDownHeard = true }
            if type == .keyUp { self.setup.model.keyUpHeard = true }
            DispatchQueue.main.async { self.setup.redraw() }
        }
        // No dim and no crosshair: the Afterglow mark beside the cursor says "listening".
        overlay.dimsScreen = false
        overlay.usesCrosshair = false
        overlay.strokeColor = NSColor(srgbRed: 1.0, green: 0.78, blue: 0.62, alpha: 0.95)
        refresh()
        let step = UserDefaults.standard.string(forKey: PermissionSetupController.resumeKey)
        UserDefaults.standard.removeObject(forKey: PermissionSetupController.resumeKey)
        if !setup.model.dictationReady || step != nil { setup.show(resume: step) }
    }

    func applicationDidBecomeActive(_ notification: Notification) { setup.refresh() }

    func applicationWillTerminate(_ notification: Notification) {
        setup.stop(); monitor.stop(); recorder.cancel(); captureTask?.cancel(); requestTask?.cancel()
        overlay.hide(); panel?.close(); pill.hide(); glow.hide()
    }

    func menuWillOpen(_ menu: NSMenu) {
        refresh()
        copyLast.isEnabled = lastWords != nil
        copyLast.toolTip = lastWords
    }

    @objc private func copyLastWords() {
        guard let lastWords else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(lastWords, forType: .string)
    }

    private func makeMenu() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        statusItem.button?.image = horizonImage()
        statusItem.button?.toolTip = "Afterglow fn"
        let menu = NSMenu()
        menu.delegate = self
        menu.addItem(statusLine)
        copyLast.action = #selector(copyLastWords); copyLast.target = self
        menu.addItem(copyLast)
        menu.addItem(.separator())
        let permissions = NSMenuItem(title: "Permissions…", action: #selector(showPermissions), keyEquivalent: "")
        permissions.target = self; menu.addItem(permissions)
        let restart = NSMenuItem(title: "Restart to apply permissions",
                                 action: #selector(restartHelper), keyEquivalent: "")
        restart.target = self; menu.addItem(restart)
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

    private func refresh() {
        guard !relaunching else { return }
        let statuses = Permissions.statuses()
        setup.model.statuses = statuses
        for (mode, item) in modeItems { item.state = mode == monitor.mode ? .on : .off }
        let keysAllowed = statuses[.accessibility] == .authorized && statuses[.inputMonitoring] == .authorized
        let keyPermissions: [FnPermission] = [.accessibility, .inputMonitoring]
        let keysChanged = !previousPermissions.isEmpty && keyPermissions.contains { previousPermissions[$0] != statuses[$0] }
        let requiredRevoked = PermissionSetupModel.dictation.contains {
            previousPermissions[$0] == .authorized && statuses[$0] != .authorized
        }
        let screenRevoked = previousPermissions[.screenCapture] == .authorized && statuses[.screenCapture] != .authorized
        previousPermissions = statuses
        if requiredRevoked || (screenRevoked && selection != nil) {
            cancel(); cancelPractice(); captureTask?.cancel(); requestTask?.cancel()
            setup.model.message = "Access changed. Finish setup before trying again."
        }
        if keysChanged && monitor.isMonitoring { cancel(); cancelPractice(); monitor.stop() }
        if keysAllowed && !monitor.isMonitoring {
            do { try monitor.start(); setup.model.keyMonitorFailed = false }
            catch {
                setup.model.keyMonitorFailed = true
                setup.model.message = "Your key is not heard yet. If access is on, reopen the helper to check."
            }
        } else if !keysAllowed && monitor.isMonitoring {
            cancel(); cancelPractice(); monitor.stop()
        }
        statusLine.title = server == nil ? "Check your server URL" : setup.model.menuStatus
        setup.redraw()
    }

    @objc private func showPermissions() {
        cancel(); cancelPractice()
        setup.show()
    }

    @objc private func restartHelper() {
        guard !relaunching else { return }
        guard setup.model.requesting == nil else {
            setup.model.message = "Finish your permission choice before reopening."
            setup.redraw(); return
        }
        let step = setup.model.savedStep
        UserDefaults.standard.set(step, forKey: PermissionSetupController.resumeKey)
        let plan = HelperLaunchPlan(bundleURL: Bundle.main.bundleURL, arguments: CommandLine.arguments,
                                    environment: ProcessInfo.processInfo.environment, effectiveURL: server?.base)
        relaunching = true; setup.model.reopening = true; setup.redraw()
        Task {
            let success = await HelperRelaunch.perform(plan: plan, stop: {
                self.cancel(); self.cancelPractice(); self.monitor.stop(); self.recorder.cancel()
                self.captureTask?.cancel(); self.captureTask = nil
                self.requestTask?.cancel(); self.overlay.hide(); self.panel?.close()
                self.pill.hide(); self.glow.hide(); self.setup.stop()
            }, launch: { await HelperRelaunch.launch($0) }, terminate: { NSApp.terminate(nil) })
            if !success {
                relaunching = false; setup.model.reopening = false
                setup.show(resume: step)
                setup.model.message = "Could not reopen. Quit from the menu, then open this same Afterglow fn app to finish."
                setup.redraw()
            }
        }
    }
    @objc private func changeMode(_ sender: NSMenuItem) {
        guard let raw = sender.representedObject as? String, let mode = FnKeyMonitor.Mode(rawValue: raw) else { return }
        cancel(); cancelPractice(); monitor.mode = mode
        UserDefaults.standard.set(mode.rawValue, forKey: "AfterglowHoldMode")
        refresh()
    }
    @objc private func openAfterglow() { if let server { NSWorkspace.shared.open(server.base) } }
    @objc private func quit() { NSApp.terminate(nil) }

    private func begin() {
        guard !relaunching else { return }
        if setup.isVisible {
            guard setup.isPracticing, !practicing, !recording, !busy else { return }
            guard Permissions.status(for: .microphone).isAuthorized else { setup.refresh(); return }
            setup.model.keyHeard = true; setup.model.keyReleased = false; setup.model.audioArrived = false
            do { try recorder.start(); practicing = true }
            catch { setup.model.message = "Could not start your microphone. Check the input, then hold fn again." }
            setup.redraw(); return
        }
        guard !recording, !busy else { return }
        guard server != nil else { pill.error("Check your server URL"); return }
        guard PermissionSetupModel.dictation.allSatisfy({ Permissions.status(for: $0).isAuthorized }) else {
            setup.show(); return
        }
        selection = nil; captureTask = nil
        frontApp = NSWorkspace.shared.frontmostApplication
        frontAppName = frontApp?.localizedName ?? ""
        do {
            try recorder.start()
            recording = true
            pill.hide(); glow.listen()
            overlay.show()
        } catch { glow.hide(); pill.error("Could not start your microphone") }
    }

    private func select(_ rect: CGRect) {
        guard recording else { return }
        selection = rect
        captureTask?.cancel()
        let ids = overlay.windowIDs + [pill.windowID, glow.windowID]
        guard Permissions.status(for: .screenCapture).isAuthorized else {
            cancel(); setup.show(screen: true); return
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

    /// The explanation's question is what you said; older servers send only its id.
    private func words(for reply: AmbientReply, server: ServerAddress) async -> String? {
        if let text = reply.text, !text.isEmpty { return text }
        guard let id = reply.id, var parts = URLComponents(url: server.base.appendingPathComponent("api/explain"), resolvingAgainstBaseURL: false) else { return nil }
        parts.queryItems = [URLQueryItem(name: "id", value: id)]
        guard let url = parts.url, let (data, _) = try? await URLSession.shared.data(from: url),
              let doc = try? JSONDecoder().decode(ExplanationQuestion.self, from: data) else { return nil }
        return doc.question
    }

    /// A wrong guess: you wanted your words pasted, not explained.
    private func pasteInstead(_ words: String, into app: NSRunningApplication?) {
        if Permissions.status(for: .accessibility).isAuthorized,
           NSWorkspace.shared.frontmostApplication?.processIdentifier == app?.processIdentifier {
            paster.paste(words)
        } else {
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(words, forType: .string)
            pill.error("Copied. Paste it with ⌘V")
        }
    }

    /// Esc stops whatever is in progress: a recording is dropped, a request is cancelled.
    private func stopOnEscape() {
        if recording { cancel(); return }
        guard busy, let task = requestTask else { return }
        task.cancel()
        glow.hide()
    }

    private func cancelPractice() {
        guard practicing else { return }
        practicing = false; recorder.cancel()
    }

    private func cancel() {
        cancelPractice()
        guard recording else { return }
        recording = false
        recorder.cancel(); captureTask?.cancel(); captureTask = nil
        overlay.hide(); pill.hide(); glow.hide(); selection = nil
    }

    private func finish() {
        if practicing {
            setup.model.keyReleased = true
            if recorder.sampleCount > 0 && setup.model.audioArrived {
                setup.model.message = "Your key and microphone work. Nothing was sent."
            } else { setup.model.message = "Your key works. Try speaking a little louder to check audio." }
            cancelPractice(); setup.redraw(); return
        }
        guard recording, let server else { return }
        guard Permissions.status(for: .microphone).isAuthorized,
              Permissions.status(for: .accessibility).isAuthorized,
              Permissions.status(for: .inputMonitoring).isAuthorized,
              selection == nil || Permissions.status(for: .screenCapture).isAuthorized else {
            cancel(); setup.show(); return
        }
        recording = false; overlay.hide()
        guard let audio = recorder.stop() else { glow.hide(); return }
        let crop = captureTask
        captureTask = nil
        let anchor = selection ?? CGRect(origin: NSEvent.mouseLocation, size: CGSize(width: 1, height: 1))
        let targetApp = frontApp
        let appName = frontAppName
        busy = true; glow.work(); refresh()
        requestTask = Task { [weak self] in
            guard let self else { return }
            defer { busy = false; requestTask = nil; refresh() }
            do {
                // The circled area must be ready soon; a stuck capture never leaves the mark spinning.
                var context: CropContext?
                if let crop {
                    do {
                        context = try await Deadline.within(12, message: "Could not capture the circled area. Try again") { try await crop.value }
                    } catch { crop.cancel(); throw error }
                }
                try Task.checkCancellation()
                let request = AmbientRequest(audio: audio, crop: context?.png, ocr: context?.ocr,
                                             ax: context?.ax, appName: appName, screenRect: context?.rect)
                let reply = try await AmbientClient(address: server).send(request)
                try Task.checkCancellation()
                switch reply.mode {
                case "paste":
                    guard let text = reply.text else { throw HelperError.message("Your agent returned no text") }
                    // Your last words are kept even when the paste below is refused, so the menu can copy them.
                    lastWords = text
                    // Never paste into a different app if the user switched while waiting.
                    guard NSWorkspace.shared.frontmostApplication?.processIdentifier == targetApp?.processIdentifier else {
                        throw HelperError.message("Focus changed. Please try again")
                    }
                    guard Permissions.status(for: .accessibility).isAuthorized else {
                        throw HelperError.message("Paste access changed. Open Permissions to check")
                    }
                    paster.paste(text); glow.hide()
                case "explain":
                    guard let path = reply.url else { throw HelperError.message("Your agent returned no explanation") }
                    let url = try server.explanation(path)
                    let words = await self.words(for: reply, server: server)
                    // Esc during the words fetch stops here: no window opens for a cancelled request.
                    try Task.checkCancellation()
                    lastWords = words ?? lastWords
                    panel?.close()
                    let explanation = FloatingPanel(url: url)
                    explanation.title = "Your agent"
                    panel = explanation
                    if let words {
                        explanation.window.addTitlebarAccessoryViewController(YouSaidBar(words: words, paste: { [weak self, weak explanation] in
                            guard let self else { return }
                            explanation?.close()
                            self.pasteInstead(words, into: targetApp)
                        }))
                    }
                    explanation.show(beside: anchor); glow.hide()
                default: throw HelperError.message("Your agent returned an unknown reply")
                }
            } catch is CancellationError { glow.hide() }
            catch is ScreenCaptureError {
                glow.hide(); setup.show(screen: true)
                setup.model.message = "Screen capture failed. If its switch is on, try Fix a switch that is on but not working, then circle again."
                setup.redraw()
            }
            catch let error as HelperError { glow.hide(); pill.error(error.localizedDescription) }
            catch {
                glow.hide()
                // Esc cancels the request; a cancelled request is not an error to show.
                if !Task.isCancelled { pill.error("Could not reach your agent or capture") }
            }
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
