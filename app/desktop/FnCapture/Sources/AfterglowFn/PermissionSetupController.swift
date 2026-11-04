import AppKit
import FnCapture

@MainActor final class PermissionSetupController: NSObject, NSWindowDelegate {
    static let resumeKey = "AfterglowPermissionSetupStep"
    let model: PermissionSetupModel
    private var setupWindow: PermissionSetupWindow?
    private let guide = PermissionGuidePanel()
    private var poll: Timer?
    private var requestTask: Task<Void, Never>?
    private var lastPane: FnPermission = .accessibility
    var onRefresh: (() -> Void)?
    var onClose: (() -> Void)?
    var onReopen: (() -> Void)?
    var isVisible: Bool { setupWindow?.isVisible == true }
    var isPracticing: Bool { isVisible && model.dictationReady && !model.screenStep && !model.reopening }

    init(statuses: [FnPermission: FnPermissionStatus]) {
        model = PermissionSetupModel(statuses: statuses)
        super.init()
        guide.onVisibilityChanged = { [weak self] in self?.updatePolling() }
    }
    func show(resume: String? = nil, screen: Bool = false) {
        if setupWindow == nil {
            let window = PermissionSetupWindow()
            window.delegate = self
            let view = window.setupView
            view.onAction = { [weak self] in self?.requestActive() }
            view.onLater = { [weak self] in self?.setupWindow?.close() }
            view.onRepair = { [weak self] in self?.repair() }
            view.onReopen = { [weak self] in self?.onReopen?() }
            view.onInstructions = { [weak self] in self?.instructions() }
            view.onAlternate = { [weak self] in
                guard let self else { return }
                self.openPane(self.model.active ?? self.lastPane, alternate: true)
            }
            setupWindow = window
        }
        model.screenStep = screen || resume == FnPermission.screenCapture.rawValue
        lastPane = model.active ?? (model.screenStep ? .screenCapture : .accessibility)
        if screen { model.deferScreenAccess() }
        refresh()
        setupWindow?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        updatePolling()
    }
    func refresh() {
        model.statuses = Permissions.statuses()
        setupWindow?.setupView.update(model)
        onRefresh?()
    }
    func redraw() { setupWindow?.setupView.update(model) }
    func windowWillClose(_ notification: Notification) {
        guide.stop(); poll?.invalidate(); poll = nil
        onClose?()
    }
    func stop() {
        guide.stop(); poll?.invalidate(); poll = nil
        requestTask?.cancel()
    }
    private func updatePolling() {
        guard isVisible || guide.isVisible else { poll?.invalidate(); poll = nil; return }
        guard poll == nil else { return }
        let timer = Timer(timeInterval: 0.5, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.refresh() }
        }
        RunLoop.main.add(timer, forMode: .common); poll = timer
    }
    private func requestActive() {
        guard requestTask == nil, model.requesting == nil, !model.reopening else { return }
        guide.stop()
        requestTask = Task { [weak self] in
            guard let self else { return }
            await model.requestActive(probe: { Permissions.statuses() }, request: { permission in
                self.redraw()
                _ = await Permissions.request(permission)
            }, openSettings: { permission in
                guard self.isVisible, !Task.isCancelled else { return }
                self.openPane(permission)
            })
            refresh()
            requestTask = nil
        }
    }
    private func openPane(_ permission: FnPermission, alternate: Bool = false) {
        guard model.requesting == nil, !model.reopening else { return }
        lastPane = permission
        let opened = NSWorkspace.shared.open(Permissions.settingsURL(for: permission, alternate: alternate))
        if opened {
            model.settingsVisited.insert(permission)
            guide.show(for: permission, from: setupWindow?.frame ?? .zero)
            if permission == .inputMonitoring || permission == .screenCapture {
                model.message = "Access not confirmed. If you enabled it, use Reopen to finish to check again."
            } else { model.message = "Turn on Afterglow fn in Settings. Return here to continue." }
        } else { model.message = "Open System Settings > Privacy & Security, then choose \(paneName(permission))." }
        redraw()
    }
    private func paneName(_ permission: FnPermission) -> String {
        switch permission {
        case .accessibility: return "Accessibility"
        case .inputMonitoring: return "Input Monitoring"
        case .microphone: return "Microphone"
        case .screenCapture: return "Screen Recording (Screen & System Audio Recording on some versions)"
        }
    }
    private func repair() {
        openPane(model.active ?? lastPane)
        model.message = "Remove Afterglow fn with the minus button, then add it again, or turn it off and on."
        redraw()
    }
    private func instructions() {
        let permission = model.active ?? lastPane
        model.message = "System Settings > Privacy & Security > \(paneName(permission)). Turn on Afterglow fn. If the page is wrong, try the other Settings link."
        redraw()
    }
}
