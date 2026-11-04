// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "FnCapture",
    platforms: [.macOS(.v14)],
    products: [
        .library(name: "FnCapture", targets: ["FnCapture"]),
        .executable(name: "afterglow-fn", targets: ["AfterglowFn"]),
    ],
    targets: [
        .target(name: "FnCapture"),
        .executableTarget(name: "AfterglowFn", dependencies: ["FnCapture"]),
        .testTarget(name: "FnCaptureTests", dependencies: ["FnCapture"]),
        .testTarget(name: "AfterglowFnTests", dependencies: ["AfterglowFn"]),
    ]
)
