import Foundation
import FnCapture

/// Navigation and capability evidence are separate from macOS authorization.
@MainActor final class PermissionSetupModel {
    static let dictation: [FnPermission] = [.accessibility, .inputMonitoring, .microphone]
    var statuses: [FnPermission: FnPermissionStatus]
    private(set) var requesting: FnPermission?
    private(set) var asked: Set<FnPermission> = []
    var settingsVisited: Set<FnPermission> = []
    var screenStep = false
    var keyMonitorFailed = false
    var keyHeard = false
    var keyReleased = false
    var keyDownHeard = false
    var keyUpHeard = false
    var audioArrived = false
    var message = ""
    var reopening = false

    init(statuses: [FnPermission: FnPermissionStatus]) { self.statuses = statuses }
    func allowed(_ permission: FnPermission) -> Bool { statuses[permission] == .authorized }
    var dictationReady: Bool { Self.dictation.allSatisfy(allowed) }
    var active: FnPermission? {
        if let required = Self.dictation.first(where: { !allowed($0) }) { return required }
        return screenStep && !allowed(.screenCapture) ? .screenCapture : nil
    }
    var savedStep: String { screenStep ? FnPermission.screenCapture.rawValue : active?.rawValue ?? "practice" }
    var menuStatus: String {
        !dictationReady || keyMonitorFailed ? "Finish setup" : allowed(.screenCapture) ? "Dictation ready" : "Screen attachments not set up"
    }
    var canReopen: Bool {
        if keyMonitorFailed { return true }
        guard let active, [.inputMonitoring, .screenCapture].contains(active) else { return false }
        return (asked.contains(active) || settingsVisited.contains(active)) && !allowed(active)
    }
    func statusText(_ permission: FnPermission) -> String {
        if allowed(permission) { return "Allowed" }
        if permission == .microphone {
            if statuses[permission] == .restricted { return "Restricted" }
            if statuses[permission] == .denied { return "Access off" }
        }
        return "Not set up"
    }
    func actionTitle(_ permission: FnPermission) -> String {
        if permission == .microphone && statuses[permission] != .notDetermined { return "Open Microphone Settings" }
        if asked.contains(permission) { return "Open Settings" }
        switch permission {
        case .accessibility: return "Enable paste"
        case .inputMonitoring: return "Enable key access"
        case .microphone: return "Enable microphone"
        case .screenCapture: return "Set up screen access"
        }
    }
    /// Returns false instead of queuing a second system request.
    func beginRequest(_ permission: FnPermission) -> Bool {
        guard requesting == nil, !reopening, active == permission, !allowed(permission) else { return false }
        requesting = permission
        return true
    }
    func needsNativeRequest(_ permission: FnPermission) -> Bool {
        if permission == .microphone { return statuses[permission] == .notDetermined }
        return !asked.contains(permission)
    }
    func finishRequest(_ permission: FnPermission) { asked.insert(permission); requesting = nil }
    func requestActive(probe: () -> [FnPermission: FnPermissionStatus],
                       request: (FnPermission) async -> Void,
                       openSettings: (FnPermission) -> Void) async {
        guard let permission = active, beginRequest(permission) else { return }
        if needsNativeRequest(permission) { await request(permission) }
        statuses = probe()
        finishRequest(permission)
        if !allowed(permission) { openSettings(permission) }
    }
    /// First-circle navigation never queues a crop or audio for later reuse.
    func deferScreenAccess() { screenStep = true; message = "After setup, circle again. Your previous recording was discarded." }
}
