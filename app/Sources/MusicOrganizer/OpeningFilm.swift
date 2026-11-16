import AVFoundation
import AppKit
import MusicOrganizerKit
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
/// It fits the window, whatever its size (the owner, 2026-11-16: "it should sync to the size
/// of the window"): a window wide enough plays the wide film, any other the upright one
/// (Opening-upright.mp4), which fills it; and as the window is resized the film follows,
/// never stretched and never cutting the name (`OpeningFit` in the Kit says where it goes).
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
    /// The film and, under it, its first picture, placed together where `OpeningFit` says.
    private let stage = CALayer()
    private let poster = CALayer()
    private let picture = AVPlayerLayer()
    /// The film's night sky, behind it: what shows round the film in a window it can't fill
    /// (made narrow while the wide film plays), and before even its first picture is there.
    private let sky = CAGradientLayer()
    /// Fades the film's own edges into that sky, where they don't reach the window's.
    private let edges = CAGradientLayer()
    /// Which film is playing; nil until the view has a size.
    private var shape: OpeningShape?
    private var player: AVPlayer?
    private var finished: NSObjectProtocol?
    private var ended: (() -> Void)?
    private static let log = Logger(subsystem: "org.musicorganizer.app", category: "opening")
    /// The film's last picture: where it stays.
    private static let lastPicture = CMTime(value: 89, timescale: 30)
    /// The film's colours at its top and bottom edges (both shapes have the same sky).
    private static let skyTop = CGColor(srgbRed: 4 / 255, green: 8 / 255, blue: 20 / 255, alpha: 1)
    private static let skyBottom = CGColor(srgbRed: 15 / 255, green: 27 / 255, blue: 58 / 255, alpha: 1)

    init(ended: @escaping () -> Void) {
        self.ended = ended
        super.init(frame: .zero)
        layer = CALayer()
        wantsLayer = true
        layer?.masksToBounds = true
        layer?.backgroundColor = Self.skyTop
        // A layer's 0,0 is its bottom corner here: the top colour at y 1.
        sky.colors = [Self.skyTop, Self.skyBottom]
        sky.startPoint = CGPoint(x: 0.5, y: 1)
        sky.endPoint = CGPoint(x: 0.5, y: 0)
        edges.colors = [CGColor.clear, CGColor.black, CGColor.black, CGColor.clear]
        edges.locations = [0, 0.12, 0.88, 1]
        poster.contentsGravity = .resize  // the stage is the film's own shape
        picture.videoGravity = .resize
        stage.addSublayer(poster)
        stage.addSublayer(picture)
        layer?.addSublayer(sky)
        layer?.addSublayer(stage)
        // If the film can't play, the screen isn't held for it.
        DispatchQueue.main.asyncAfter(deadline: .now() + 5) { [weak self] in self?.finish() }
    }

    required init?(coder: NSCoder) { nil }

    /// The film for this shape, in the app.
    private static func film(_ shape: OpeningShape) -> URL? {
        Bundle.main.url(forResource: shape.resource, withExtension: "mp4")
    }

    /// Plays the film for this shape from its beginning (or shows its last picture, with
    /// macOS set to reduce motion), in place of one that has nothing on the screen yet.
    private func play(_ wanted: OpeningShape) {
        var shape = wanted
        if wanted == .upright, Self.film(.upright) == nil { shape = .wide }
        guard shape != self.shape else { return }
        if shape != wanted {
            Self.log.error("The upright loading film isn't in the app (Opening-upright.mp4); fitting the wide one.")
        }
        self.shape = shape
        if let url = Bundle.main.url(forResource: shape.resource, withExtension: "jpg") {
            poster.contents = NSImage(contentsOf: url)
        }
        if let finished { NotificationCenter.default.removeObserver(finished) }
        finished = nil
        player?.pause()
        guard let url = Self.film(shape) else {
            Self.log.error("The loading film isn't in the app (\(shape.resource, privacy: .public).mp4); showing its picture only.")
            picture.player = nil
            player = nil
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
        guard bounds.width > 0, bounds.height > 0 else { return }
        // Nothing here is animated: the film is where the window says at once (a changed
        // picture would otherwise fade in, stretched to its new place).
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        defer { CATransaction.commit() }
        // The film for the window's shape; chosen again if the window changes shape before
        // any of the film is on the screen (as the window takes its place when the app
        // opens). From its first picture on it stays, and only its size follows the window.
        let wanted = OpeningFit.shape(for: bounds.size)
        if wanted != shape, !picture.isReadyForDisplay { play(wanted) }
        guard let shape else { return }
        let frame = OpeningFit.frame(of: shape, in: bounds.size)
        sky.frame = bounds
        stage.frame = frame
        poster.frame = stage.bounds
        picture.frame = stage.bounds
        // Where the film doesn't reach the window's edges, its own edges fade into the sky.
        if frame.minY > 0.5 || frame.minX > 0.5 {
            let acrossTopAndBottom = frame.minY > 0.5
            edges.startPoint = acrossTopAndBottom ? CGPoint(x: 0.5, y: 0) : CGPoint(x: 0, y: 0.5)
            edges.endPoint = acrossTopAndBottom ? CGPoint(x: 0.5, y: 1) : CGPoint(x: 1, y: 0.5)
            edges.frame = stage.bounds
            stage.mask = edges
        } else {
            stage.mask = nil
        }
    }
}
