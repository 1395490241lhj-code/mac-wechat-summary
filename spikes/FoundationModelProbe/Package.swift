// swift-tools-version: 6.2
import PackageDescription

// macOS 26 unlike the other spikes: FoundationModels does not exist below it.
let package = Package(
    name: "FoundationModelProbe",
    platforms: [.macOS(.v26)],
    targets: [.executableTarget(name: "FoundationModelProbe")]
)
