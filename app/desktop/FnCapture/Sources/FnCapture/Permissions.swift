import ApplicationServices
import AVFoundation
import CoreGraphics
import Foundation
import IOKit.hidsystem

/// The four permissions the Fn helper needs.
public enum FnPermission: String, CaseIterable, Codable, Sendable {
    /// Microphone input for voice recording.
    case microphone
    /// Screen capture for the selected crop.
    case screenCapture
    /// Accessibility for paste and reading the front item.
    case accessibility
    /// Input monitoring for the hold-to-talk key via IOHID listen events.
    case inputMonitoring
}

/// The authorisation state of one permission.
public enum FnPermissionStatus: String, Codable, Sendable {
    /// The user granted access.
    case authorized
    /// The user denied access, or access is off in Settings.
    case denied
    /// The user has not been asked yet, where the system reports it.
    case notDetermined
    /// Access is restricted by the system or a profile.
    case restricted

    /// True only when authorised.
    public var isAuthorized: Bool {
        self == .authorized
    }
}

/// The four permission states in one value.
public struct PermissionState: Codable, Sendable, Equatable {
    /// Microphone input state.
    public let microphone: FnPermissionStatus
    /// Accessibility state.
    public let accessibility: FnPermissionStatus
    /// Screen recording state.
    public let screenRecording: FnPermissionStatus
    /// Input monitoring state.
    public let inputMonitoring: FnPermissionStatus

    /// Creates a combined permission state.
    ///
    /// - Parameters:
    ///   - microphone: Microphone input state.
    ///   - accessibility: Accessibility state.
    ///   - screenRecording: Screen recording state.
    ///   - inputMonitoring: Input monitoring state.
    public init(
        microphone: FnPermissionStatus,
        accessibility: FnPermissionStatus,
        screenRecording: FnPermissionStatus,
        inputMonitoring: FnPermissionStatus
    ) {
        self.microphone = microphone
        self.accessibility = accessibility
        self.screenRecording = screenRecording
        self.inputMonitoring = inputMonitoring
    }

    /// True when all four permissions are authorised.
    public var allAuthorized: Bool {
        microphone.isAuthorized && accessibility.isAuthorized
            && screenRecording.isAuthorized && inputMonitoring.isAuthorized
    }
}

/// Checks and requests the Fn permissions.
///
/// Microphone uses `AVCaptureDevice`. Screen capture uses
/// `CGPreflightScreenCaptureAccess` and `CGRequestScreenCaptureAccess`.
/// Accessibility uses `AXIsProcessTrusted` with a prompt. Input monitoring
/// uses `IOHIDCheckAccess` and `IOHIDRequestAccess` with
/// `kIOHIDRequestTypeListenEvent`.
///
/// Screen capture and accessibility report only granted or not granted, so a
/// not-yet-asked state appears as denied. Screen recording and input
/// monitoring may need a relaunch after the user grants them.
@MainActor
public enum Permissions {
    /// Returns the current state without prompting, except where noted.
    ///
    /// - Parameter permission: The permission to check.
    /// - Returns: The current `FnPermissionStatus`.
    public static func status(for permission: FnPermission) -> FnPermissionStatus {
        switch permission {
        case .microphone:
            switch AVCaptureDevice.authorizationStatus(for: .audio) {
            case .authorized:
                return .authorized
            case .denied:
                return .denied
            case .restricted:
                return .restricted
            case .notDetermined:
                return .notDetermined
            @unknown default:
                return .denied
            }
        case .screenCapture:
            return CGPreflightScreenCaptureAccess() ? .authorized : .denied
        case .accessibility:
            return AXIsProcessTrusted() ? .authorized : .denied
        case .inputMonitoring:
            let access = IOHIDCheckAccess(kIOHIDRequestTypeListenEvent)
            if access == kIOHIDAccessTypeGranted {
                return .authorized
            }
            if access == kIOHIDAccessTypeDenied {
                return .denied
            }
            return .notDetermined
        }
    }

    /// Requests access, prompting the user when the system allows it.
    ///
    /// Microphone suspends until the user answers. Screen capture,
    /// accessibility and input monitoring show the system prompt and return
    /// the state seen right after asking; the user may still need to approve
    /// in Settings and relaunch.
    ///
    /// - Parameter permission: The permission to request.
    /// - Returns: The state seen after the request.
    public static func request(_ permission: FnPermission) async -> FnPermissionStatus {
        switch permission {
        case .microphone:
            let granted = await withCheckedContinuation { continuation in
                AVCaptureDevice.requestAccess(for: .audio) { granted in
                    continuation.resume(returning: granted)
                }
            }
            if granted {
                return .authorized
            }
            return status(for: .microphone)
        case .screenCapture:
            if CGRequestScreenCaptureAccess() {
                return .authorized
            }
            return status(for: .screenCapture)
        case .accessibility:
            let options = [
                kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true
            ] as CFDictionary
            if AXIsProcessTrustedWithOptions(options) {
                return .authorized
            }
            return .denied
        case .inputMonitoring:
            _ = IOHIDRequestAccess(kIOHIDRequestTypeListenEvent)
            return status(for: .inputMonitoring)
        }
    }

    /// Returns the four permission states in one value.
    ///
    /// This mirrors `statuses()` without the dictionary.
    public static func currentState() -> PermissionState {
        PermissionState(
            microphone: status(for: .microphone),
            accessibility: status(for: .accessibility),
            screenRecording: status(for: .screenCapture),
            inputMonitoring: status(for: .inputMonitoring)
        )
    }

    /// Returns the state of all four permissions.
    public static func statuses() -> [FnPermission: FnPermissionStatus] {
        var result: [FnPermission: FnPermissionStatus] = [:]
        for permission in FnPermission.allCases {
            result[permission] = status(for: permission)
        }
        return result
    }

    /// True when all four permissions are authorised.
    public static func allAuthorized() -> Bool {
        FnPermission.allCases.allSatisfy { status(for: $0).isAuthorized }
    }
}
