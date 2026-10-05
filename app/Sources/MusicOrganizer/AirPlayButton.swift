import AVKit
import SwiftUI

/// The AirPlay button in the player bar (owner, 2026-10-05: "HAVE THE APP SEND it to the
/// apple tv itself"). It's macOS's own button and list of devices. Choosing one makes the
/// app's player send what it plays to that device itself, and not through the Mac's
/// sound output.
///
/// Why: through the Mac's sound output, an AirPlay device plays 1.75 s behind whatever
/// is sent (measured), so Play is heard two seconds late whatever the app does. A device
/// the player sends to itself is told to play and pause, and does it there and then.
struct AirPlayButton: NSViewRepresentable {
    let player: AVPlayer
    /// The list of devices is opening (true) or has closed (false).
    var choosing: (Bool) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(choosing: choosing) }

    func makeNSView(context: Context) -> AVRoutePickerView {
        let view = AVRoutePickerView()
        view.isRoutePickerButtonBordered = false
        view.player = player
        view.delegate = context.coordinator
        return view
    }

    func updateNSView(_ view: AVRoutePickerView, context: Context) {
        if view.player !== player { view.player = player }
        context.coordinator.choosing = choosing
    }

    final class Coordinator: NSObject, AVRoutePickerViewDelegate {
        var choosing: (Bool) -> Void

        init(choosing: @escaping (Bool) -> Void) { self.choosing = choosing }

        func routePickerViewWillBeginPresentingRoutes(_ routePickerView: AVRoutePickerView) {
            choosing(true)
        }

        func routePickerViewDidEndPresentingRoutes(_ routePickerView: AVRoutePickerView) {
            choosing(false)
        }
    }
}
