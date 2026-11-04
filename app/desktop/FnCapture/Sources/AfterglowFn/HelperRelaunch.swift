import AppKit

struct HelperLaunchPlan: Equatable {
    let bundleURL: URL
    let arguments: [String]
    let environment: [String: String]

    init(bundleURL: URL, arguments: [String], environment: [String: String], effectiveURL: URL?) {
        self.bundleURL = bundleURL
        self.arguments = Array(arguments.dropFirst())
        // Do not forward the rest of the parent's environment, which may contain secrets.
        self.environment = environment["AFTERGLOW_URL"].map { ["AFTERGLOW_URL": $0] }
            ?? effectiveURL.map { ["AFTERGLOW_URL": $0.absoluteString] } ?? [:]
    }
}

@MainActor enum HelperRelaunch {
    static func launch(_ plan: HelperLaunchPlan) async -> Bool {
        guard plan.bundleURL.pathExtension == "app" else { return false }
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.arguments = plan.arguments
        configuration.environment = plan.environment
        configuration.createsNewApplicationInstance = true
        configuration.activates = false
        return await withCheckedContinuation { continuation in
            NSWorkspace.shared.openApplication(at: plan.bundleURL, configuration: configuration) { app, error in
                continuation.resume(returning: app != nil && error == nil)
            }
        }
    }

    /// The running copy stays alive, stopped, and with recovery UI if launch fails.
    static func perform(plan: HelperLaunchPlan, stop: () -> Void,
                        launch: (HelperLaunchPlan) async -> Bool,
                        terminate: () -> Void) async -> Bool {
        stop()
        let launched = await launch(plan)
        if launched { terminate() }
        return launched
    }
}
