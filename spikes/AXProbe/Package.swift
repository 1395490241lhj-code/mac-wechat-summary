// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "AXProbe",
    platforms: [.macOS(.v12)],
    targets: [.executableTarget(name: "AXProbe")]
)
