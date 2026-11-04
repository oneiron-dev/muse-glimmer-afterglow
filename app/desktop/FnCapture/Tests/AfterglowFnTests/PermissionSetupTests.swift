import XCTest
import FnCapture
@testable import AfterglowFn

final class PermissionSetupTests: XCTestCase {
    @MainActor private func statuses(_ changes: [FnPermission: FnPermissionStatus] = [:]) -> [FnPermission: FnPermissionStatus] {
        var statuses = Dictionary(uniqueKeysWithValues: FnPermission.allCases.map { ($0, FnPermissionStatus.authorized) })
        statuses[.screenCapture] = .denied
        return statuses.merging(changes) { _, new in new }
    }
    @MainActor func testExistingGrantSkipsAllDictationRequests() async {
        let state = statuses()
        let model = PermissionSetupModel(statuses: state)
        var requests = 0
        await model.requestActive(probe: { state }, request: { _ in requests += 1 }, openSettings: { _ in XCTFail("No Settings needed") })
        XCTAssertTrue(model.dictationReady)
        XCTAssertNil(model.active)
        XCTAssertEqual(model.statusText(.accessibility), "Allowed")
        XCTAssertEqual(requests, 0)
        XCTAssertEqual(model.menuStatus, "Screen attachments not set up")
    }
    @MainActor func testStaleFalseIsNotReportedAsDeniedOrGranted() async {
        let state = statuses([.inputMonitoring: .denied])
        let model = PermissionSetupModel(statuses: state)
        model.settingsVisited.insert(.inputMonitoring)
        XCTAssertTrue(model.canReopen)
        XCTAssertTrue(model.needsNativeRequest(.inputMonitoring), "A repair visit must not count as a native request")
        var requests: [FnPermission] = []
        var panes: [FnPermission] = []
        for _ in 0..<2 {
            await model.requestActive(probe: { state }, request: { requests.append($0) }, openSettings: { panes.append($0) })
        }
        XCTAssertEqual(model.statusText(.inputMonitoring), "Not set up")
        XCTAssertFalse(model.dictationReady)
        XCTAssertTrue(model.canReopen)
        XCTAssertEqual(requests, [.inputMonitoring])
        XCTAssertEqual(panes, [.inputMonitoring, .inputMonitoring])
        XCTAssertEqual(model.savedStep, FnPermission.inputMonitoring.rawValue)
        model.screenStep = true
        XCTAssertEqual(model.active, .inputMonitoring, "Optional screen setup must not hide required key setup")
        XCTAssertEqual(PermissionSetupModel(statuses: statuses([.accessibility: .denied])).statusText(.accessibility), "Not set up")
    }
    @MainActor func testDeniedMicrophoneOpensSettingsWithoutRequest() async {
        let state = statuses([.microphone: .denied])
        let model = PermissionSetupModel(statuses: state)
        XCTAssertEqual(model.actionTitle(.microphone), "Open Microphone Settings")
        var panes: [FnPermission] = []
        await model.requestActive(probe: { state }, request: { _ in XCTFail("Must not re-request a denied mic") }, openSettings: { panes.append($0) })
        XCTAssertEqual(panes, [.microphone])
        XCTAssertEqual(model.statusText(.microphone), "Access off")
    }
    @MainActor func testScreenAccessIsDeferredUntilFirstCircle() async {
        let state = statuses()
        let model = PermissionSetupModel(statuses: state)
        XCTAssertNil(model.active)
        XCTAssertFalse(model.beginRequest(.screenCapture))
        model.deferScreenAccess()
        XCTAssertEqual(model.active, .screenCapture)
        XCTAssertEqual(model.savedStep, FnPermission.screenCapture.rawValue)
        var requests: [FnPermission] = []
        await model.requestActive(probe: { state }, request: { requests.append($0) }, openSettings: { _ in })
        XCTAssertEqual(requests, [.screenCapture])
        XCTAssertTrue(model.canReopen)
        XCTAssertFalse(model.allowed(.screenCapture))
    }
    @MainActor func testOnlyOneRequestCanBeInFlight() async {
        let state = statuses([.microphone: .notDetermined])
        let model = PermissionSetupModel(statuses: state)
        var calls = 0
        await model.requestActive(probe: { state }, request: { _ in
            calls += 1
            XCTAssertEqual(model.requesting, .microphone)
            await model.requestActive(probe: { state }, request: { _ in XCTFail("Overlapping request") }, openSettings: { _ in XCTFail("Overlapping Settings") })
            XCTAssertFalse(model.beginRequest(.screenCapture))
        }, openSettings: { _ in })
        XCTAssertEqual(calls, 1)
        XCTAssertNil(model.requesting)
    }
    @MainActor func testFailedRelaunchKeepsOldCopyAndStep() async {
        let model = PermissionSetupModel(statuses: statuses([.inputMonitoring: .denied]))
        let step = model.savedStep
        let plan = HelperLaunchPlan(bundleURL: URL(fileURLWithPath: "/tmp/AfterglowFn.app"), arguments: ["helper"], environment: [:], effectiveURL: nil)
        var events: [String] = []
        let success = await HelperRelaunch.perform(plan: plan, stop: { events.append("stop") }, launch: { _ in
            events.append("launch"); return false
        }, terminate: { events.append("terminate") })
        XCTAssertFalse(success)
        XCTAssertEqual(events, ["stop", "launch"])
        XCTAssertEqual(model.savedStep, step)
    }
    @MainActor func testRelaunchPreservesBundleArgumentsAndURL() async throws {
        let bundle = URL(fileURLWithPath: "/tmp/AfterglowFn.app")
        let args = ["helper", "--url", "https://explicit.test:9000"]
        let env = ["AFTERGLOW_URL": "https://remote.test:8000", "UNRELATED": "not forwarded"]
        let address = try ServerAddress(arguments: args, environment: env)
        let plan = HelperLaunchPlan(bundleURL: bundle, arguments: args, environment: env, effectiveURL: address.base)
        XCTAssertEqual(plan.arguments, Array(args.dropFirst()))
        XCTAssertEqual(plan.environment, ["AFTERGLOW_URL": "https://remote.test:8000"])
        XCTAssertEqual(plan.bundleURL, bundle)
        var events: [String] = []
        let success = await HelperRelaunch.perform(plan: plan, stop: { events.append("stop") }, launch: { received in
            XCTAssertEqual(received, plan); events.append("launch"); return true
        }, terminate: { events.append("terminate") })
        XCTAssertTrue(success)
        XCTAssertEqual(events, ["stop", "launch", "terminate"])
        let envOnly = HelperLaunchPlan(bundleURL: bundle, arguments: ["helper"], environment: env, effectiveURL: nil)
        XCTAssertEqual(try ServerAddress(arguments: ["helper"] + envOnly.arguments, environment: envOnly.environment).base.absoluteString, "https://remote.test:8000")
        let saved = HelperLaunchPlan(bundleURL: bundle, arguments: ["helper"], environment: [:], effectiveURL: address.base)
        XCTAssertEqual(saved.environment["AFTERGLOW_URL"], address.base.absoluteString)
    }
}
