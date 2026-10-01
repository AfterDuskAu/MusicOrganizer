import AVFoundation
import SwiftUI

/// Where a video's picture is drawn. The player that plays the songs draws it, so the
/// picture and the sound are one thing and the player bar's buttons work on both.
struct VideoSurface: NSViewRepresentable {
    let player: AVPlayer

    func makeNSView(context: Context) -> PictureView {
        let view = PictureView()
        view.picture.player = player
        return view
    }

    func updateNSView(_ view: PictureView, context: Context) {
        if view.picture.player !== player { view.picture.player = player }
    }

    static func dismantleNSView(_ view: PictureView, coordinator: ()) {
        view.picture.player = nil  // a picture nobody sees isn't worked out
    }

    final class PictureView: NSView {
        let picture = AVPlayerLayer()

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
