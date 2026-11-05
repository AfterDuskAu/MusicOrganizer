import AVFoundation
import AppKit
import SwiftUI
import os

/// Bear Media's loading screen (the owner, 2026-11-05): a three-second film of a bear cub
/// sitting on a hill at night, watching the moon (the app's mark on it: two orbs chasing
/// round a play button) cross the sky while the trees slide past behind. It's drawn in
/// code by scripts/loading_video/render.swift, kept as app/Resources/Opening.mp4, and
/// played here round and round until the app is ready (`AppModel.showingBear`: three
/// seconds at least, one whole play).
///
/// The film is shown by the window server (an `AVPlayerLayer`), so it plays smoothly
/// however busy the app's main thread is while the library is read; drawn on that thread
/// the old bear stuttered (the owner, 2026-10-17). Its first picture (Opening.jpg) lies
/// under it from the first moment, so nothing flashes while the film gets ready; with
/// macOS set to reduce motion, that picture is all there is. The film has no sound.
struct OpeningFilm: NSViewRepresentable {
    func makeNSView(context: Context) -> OpeningFilmView { OpeningFilmView() }
    func updateNSView(_ view: OpeningFilmView, context: Context) {}
    static func dismantleNSView(_ view: OpeningFilmView, coordinator: ()) { view.stop() }
}

final class OpeningFilmView: NSView {
    private let poster = CALayer()
    private let picture = AVPlayerLayer()
    private var player: AVQueuePlayer?
    private var looper: AVPlayerLooper?
    private static let log = Logger(subsystem: "org.musicorganizer.app", category: "opening")

    override init(frame: NSRect) {
        super.init(frame: frame)
        layer = CALayer()
        wantsLayer = true
        layer?.masksToBounds = true
        // The film's night sky, for the moment before even its picture is there.
        layer?.backgroundColor = CGColor(srgbRed: 0.03, green: 0.06, blue: 0.16, alpha: 1)
        poster.contentsGravity = .resizeAspectFill
        if let url = Bundle.main.url(forResource: "Opening", withExtension: "jpg") {
            poster.contents = NSImage(contentsOf: url)
        }
        picture.videoGravity = .resizeAspectFill
        layer?.addSublayer(poster)
        layer?.addSublayer(picture)
        start()
    }

    required init?(coder: NSCoder) { nil }

    private func start() {
        guard !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion else { return }
        guard let url = Bundle.main.url(forResource: "Opening", withExtension: "mp4") else {
            Self.log.error("The loading film isn't in the app (Opening.mp4); showing its picture only.")
            return
        }
        let player = AVQueuePlayer()
        player.isMuted = true
        player.allowsExternalPlayback = false
        player.preventsDisplaySleepDuringVideoPlayback = false
        looper = AVPlayerLooper(player: player, templateItem: AVPlayerItem(url: url))
        picture.player = player
        self.player = player
        player.play()
    }

    /// Called once the loading screen has gone.
    func stop() {
        looper?.disableLooping()
        player?.pause()
        picture.player = nil
        looper = nil
        player = nil
    }

    override func layout() {
        super.layout()
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        poster.frame = bounds
        picture.frame = bounds
        CATransaction.commit()
    }
}
