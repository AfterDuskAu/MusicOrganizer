// swift-tools-version: 6.0
// The Mac app (v0.2). `MusicOrganizerKit` holds everything that can be tested without a
// window: the engine connection, the library's shape, the play queue, the lyrics parser.
// `MusicOrganizer` is the SwiftUI app. Build the double-clickable app with
// scripts/build_app.sh. The app's packages from elsewhere are Particle Accelerator, the
// owner's visuals project, and MPVKit (libmpv), which plays films and video files.
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
            revision: "19474f060a24ffa917ad1ba544d763336fd2efe2"),
        // libmpv, the player for films and for any video file (the owner's yes, 2026-10-07).
        // MPVKit is mpv built as a Swift package; the `MPVKit` product is its LGPL build
        // (never `MPVKit-GPL`). Pinned to one version, like the package above.
        .package(url: "https://github.com/mpvkit/MPVKit.git", exact: "1.0.0"),
    ],
    targets: [
        .target(name: "MusicOrganizerKit", swiftSettings: settings),
        .executableTarget(
            name: "MusicOrganizer",
            dependencies: [
                "MusicOrganizerKit",
                .product(name: "ParticleAccelerator", package: "ParticleAccelerator"),
                .product(name: "MPVKit", package: "MPVKit"),
            ],
            swiftSettings: settings),
        .testTarget(
            name: "MusicOrganizerKitTests", dependencies: ["MusicOrganizerKit"],
            swiftSettings: settings),
    ]
)
