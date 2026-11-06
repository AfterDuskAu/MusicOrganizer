import AVFoundation
import AppKit
import SwiftUI
import os

/// Bear Media's loading screen (the owner, 2026-11-06): a three-second film of the app's
/// name in particles. Browns and golds swirl round as a spiral galaxy and spiral in onto
/// the letters ("Honey": rounded, bear ears on the B, deep brown to honey yellow), and the
/// name stands complete for its last second. It's drawn in code by
/// scripts/loading_video/render.swift and kept as app/Resources/Opening.mp4. It plays once
/// and doesn't repeat: its last picture, the name, stays until the app is ready (the
/// owner: "Once the 3 seconds pass, it doesnt repeat, it just stays like that until the app
/// is ready"). `ended` is called once it has played through, so the screen never goes
/// before the name is whole (`RootView`), whenever the film began.
///
/// The film is shown by the window server (an `AVPlayerLayer`), so it plays smoothly
/// however busy the app's main thread is while the library is read; drawn on that thread
/// the old bear stuttered (the owner, 2026-10-17). Its first picture (Opening.jpg) lies
/// under it from the first moment, so nothing flashes while the film gets ready. With macOS
/// set to reduce motion, the name is shown still. The film has no sound.
struct OpeningFilm: NSViewRepresentable {
    var ended: () -> Void = {}

    func makeNSView(context: Context) -> OpeningFilmView { OpeningFilmView(ended: ended) }
    func updateNSView(_ view: OpeningFilmView, context: Context) {}
    static func dismantleNSView(_ view: OpeningFilmView, coordinator: ()) { view.stop() }
}

final class OpeningFilmView: NSView {
    private let poster = CALayer()
    private let picture = AVPlayerLayer()
    private var player: AVPlayer?
    private var finished: NSObjectProtocol?
    private var ended: (() -> Void)?
    private static let log = Logger(subsystem: "org.musicorganizer.app", category: "opening")
    /// The film's last picture: where it stays.
    private static let lastPicture = CMTime(value: 89, timescale: 30)

    init(ended: @escaping () -> Void) {
        self.ended = ended
        super.init(frame: .zero)
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
        guard let url = Bundle.main.url(forResource: "Opening", withExtension: "mp4") else {
            Self.log.error("The loading film isn't in the app (Opening.mp4); showing its picture only.")
            finish()
            return
        }
        let player = AVPlayer(url: url)
        player.isMuted = true
        player.allowsExternalPlayback = false
        player.preventsDisplaySleepDuringVideoPlayback = false
        player.actionAtItemEnd = .pause  // its last picture, the name, stays
        picture.player = player
        self.player = player
        if NSWorkspace.shared.accessibilityDisplayShouldReduceMotion {
            player.seek(to: Self.lastPicture, toleranceBefore: .zero, toleranceAfter: .zero)
            finish()
            return
        }
        finished = NotificationCenter.default.addObserver(
            forName: AVPlayerItem.didPlayToEndTimeNotification, object: player.currentItem, queue: .main
        ) { [weak self] _ in self?.finish() }
        player.play()
        // If the film can't play, the screen isn't held for it.
        DispatchQueue.main.asyncAfter(deadline: .now() + 5) { [weak self] in self?.finish() }
    }

    private func finish() {
        let ended = self.ended
        self.ended = nil
        ended?()
    }

    /// Called once the loading screen has gone.
    func stop() {
        if let finished { NotificationCenter.default.removeObserver(finished) }
        finished = nil
        player?.pause()
        picture.player = nil
        player = nil
        ended = nil
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
