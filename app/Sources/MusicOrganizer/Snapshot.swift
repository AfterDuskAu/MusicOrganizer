import AppKit

/// A developer's check. With `MUSICORG_SNAPSHOT=<folder>` the app runs unseen (no Dock
/// icon, a window nobody can see or click, never brought to the front) and saves a
/// picture of its window into that folder every few seconds. It lets a screen be looked
/// at without touching the copy of the app someone is using. The folder is the
/// developer's own: never the library.
@MainActor
enum Snapshot {
    static let folder: URL? = ProcessInfo.processInfo.environment["MUSICORG_SNAPSHOT"]
        .flatMap { $0.isEmpty ? nil : URL(fileURLWithPath: $0) }
    static var isOn: Bool { folder != nil }
    private static var taken = 0
    private static var timers: [Timer] = []

    static func startIfAsked() {
        guard let folder else { return }
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        // The window is hidden from its first moment, then pictured every few seconds.
        timers.append(
            Timer.scheduledTimer(withTimeInterval: 0.02, repeats: true) { _ in
                MainActor.assumeIsolated { hide() }
            })
        timers.append(
            Timer.scheduledTimer(withTimeInterval: 4, repeats: true) { _ in
                MainActor.assumeIsolated { take(into: folder) }
            })
    }

    private static func hide() {
        for window in NSApp.windows where window.alphaValue > 0 {
            window.alphaValue = 0
            window.ignoresMouseEvents = true
        }
    }

    private static func take(into folder: URL) {
        // The main window and Settings, and a sheet that's up on either.
        for window in NSApp.windows where window.canBecomeMain || window.isSheet {
            // `MUSICORG_SNAPSHOT_WIDTH=<points>`: after the first picture the window
            // is made this wide, to see a page follow the change.
            if taken == 1, let wanted = ProcessInfo.processInfo.environment["MUSICORG_SNAPSHOT_WIDTH"],
                let width = Double(wanted)
            {
                var frame = window.frame
                frame.size.width = width
                window.setFrame(frame, display: true)
            }
            // The view around the content, so the title bar and its buttons are in the
            // picture too.
            guard let view = window.contentView?.superview ?? window.contentView,
                let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds)
            else { continue }
            // The window's own background first: the views don't draw it.
            if let context = NSGraphicsContext(bitmapImageRep: bitmap) {
                NSGraphicsContext.saveGraphicsState()
                NSGraphicsContext.current = context
                window.effectiveAppearance.performAsCurrentDrawingAppearance {
                    window.backgroundColor.setFill()
                    NSRect(origin: .zero, size: bitmap.size).fill()
                }
                NSGraphicsContext.restoreGraphicsState()
            }
            view.cacheDisplay(in: view.bounds, to: bitmap)
            taken += 1
            let file = folder.appendingPathComponent(String(format: "window-%03d.png", taken))
            try? bitmap.representation(using: .png, properties: [:])?.write(to: file)
        }
    }
}
