import AVFoundation
import SwiftUI

/// Where a video's picture is drawn. The player that plays the songs draws it, so the
/// picture and the sound are one thing and the player bar's buttons work on both.
struct VideoSurface: NSViewRepresentable {
    let player: AVPlayer
    /// When this changes, the layer lets go of the player and takes it again (the
    /// player's watchdog asks for that when the picture has stopped).
    var refresh = 0

    func makeNSView(context: Context) -> PictureView {
        let view = PictureView()
        view.picture.player = player
        view.refresh = refresh
        return view
    }

    func updateNSView(_ view: PictureView, context: Context) {
        if view.refresh != refresh {
            view.refresh = refresh
            view.picture.player = nil
        }
        if view.picture.player !== player { view.picture.player = player }
    }

    static func dismantleNSView(_ view: PictureView, coordinator: ()) {
        view.picture.player = nil  // a picture nobody sees isn't worked out
    }

    final class PictureView: NSView {
        let picture = AVPlayerLayer()
        var refresh = 0

        override init(frame: NSRect) {
            super.init(frame: frame)
            picture.videoGravity = .resizeAspect
            picture.backgroundColor = NSColor.black.cgColor
            layer = picture
            wantsLayer = true
        }

        @available(*, unavailable)
        required init?(coder: NSCoder) { fatalError("not used") }
    }
}
