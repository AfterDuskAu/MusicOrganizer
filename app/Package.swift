// swift-tools-version: 6.0
// The Mac app (v0.2). `MusicOrganizerKit` holds everything that can be tested without a
// window: the engine connection, the library's shape, the play queue, the lyrics parser.
// `MusicOrganizer` is the SwiftUI app. Build the double-clickable app with
// scripts/build_app.sh. The app's one package from elsewhere is Particle Accelerator,
// the owner's visuals project.
import PackageDescription

let settings: [SwiftSetting] = [.swiftLanguageMode(.v5)]

let package = Package(
    name: "MusicOrganizer",
    platforms: [.macOS(.v14)],
    dependencies: [
        // The custom visualizers (docs/roadmap/0.2-visualizer.md). Pinned to one commit,
        // so a change over there never arrives by itself. To take a newer one: change
        // the commit, run `swift package resolve` here, and commit Package.resolved.
        .package(
            url: "https://github.com/AfterDuskAu/ParticleAccelerator",
            revision: "da956fa781639b6fc8ae98798f873681e496a18e"),
    ],
    targets: [
        .target(name: "MusicOrganizerKit", swiftSettings: settings),
        .executableTarget(
            name: "MusicOrganizer",
            dependencies: [
                "MusicOrganizerKit",
                .product(name: "ParticleAccelerator", package: "ParticleAccelerator"),
            ],
            swiftSettings: settings),
        .testTarget(
            name: "MusicOrganizerKitTests", dependencies: ["MusicOrganizerKit"],
            swiftSettings: settings),
    ]
)
