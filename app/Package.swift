// swift-tools-version: 6.0
// The Mac app (v0.2). `MusicOrganizerKit` holds everything that can be tested without a
// window: the engine connection, the library's shape, the play queue, the lyrics parser.
// `MusicOrganizer` is the SwiftUI app. Build the double-clickable app with
// scripts/build_app.sh.
import PackageDescription

let settings: [SwiftSetting] = [.swiftLanguageMode(.v5)]

let package = Package(
    name: "MusicOrganizer",
    platforms: [.macOS(.v14)],
    targets: [
        .target(name: "MusicOrganizerKit", swiftSettings: settings),
        .executableTarget(
            name: "MusicOrganizer", dependencies: ["MusicOrganizerKit"], swiftSettings: settings),
        .testTarget(
            name: "MusicOrganizerKitTests", dependencies: ["MusicOrganizerKit"],
            swiftSettings: settings),
    ]
)
