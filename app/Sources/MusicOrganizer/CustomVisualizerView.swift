import AVFoundation
import MusicOrganizerKit
import ParticleAccelerator
import SwiftUI

/// Hears what the app plays, for the custom visualizer. It's Particle Accelerator's own
/// listener: it adds a listening tap to the song that's playing, passes the sound on
/// untouched, and writes nothing.
///
/// The player is handed over the first time a visualizer is wanted, and stays handed
/// over until the app is closed: when the app opens, if Settings → Play Options says to
/// use the visualizer, or else when the Local Visualizer is first switched to it. Handed
/// over in the middle of a song, the song stops for about half a second, once (that's
/// Particle Accelerator's own measurement); before a song starts, nothing is heard. An
/// app whose owner never uses a visualizer plays exactly as it did before there was one.
///
/// **It doesn't listen while the sound goes to an output with a long delay** (an AirPlay
/// device holds two seconds). The owner heard the song skip every few seconds on an
/// Apple TV (2026-10-05), on every page and not only this one, because once listening
/// started it went on. Measured there: without the tap, each new piece of the song
/// reaches the player's sound queue with a tenth of a second of sound still in hand;
/// with it, the queue is empty every time. On the Mac's speakers there are two seconds
/// in hand either way. So the tap is taken off when the sound moves to such an output,
/// and put back when it returns.
@MainActor
@Observable
final class VisualizerSound {
    @ObservationIgnored let listener = MusicListener()
    /// Why the visualizer isn't listening, while it isn't: words for the page.
    private(set) var note: String?
    /// The app's player, from the first time a visualizer is wanted.
    @ObservationIgnored private var player: AVPlayer?
    @ObservationIgnored private var isListening = false
    /// The owner has the AirPlay button's list of devices open, or the player is sending
    /// to one of them. A player with a listening tap on it can't be sent to an AirPlay
    /// device, so the tap comes off while a device is being chosen and stays off while
    /// one is in use.
    @ObservationIgnored private var isChoosingADevice = false
    @ObservationIgnored private var isSendingToAirPlay = false

    func hear(_ player: AVPlayer) {
        guard self.player == nil else { return }
        self.player = player
        SoundOutput.onChange { [weak self] in self?.followTheOutput() }
        followTheOutput()
    }

    func choosingADevice(_ choosing: Bool) {
        isChoosingADevice = choosing
        followTheOutput()
    }

    func sendingToAirPlay(_ sending: Bool) {
        isSendingToAirPlay = sending
        followTheOutput()
    }

    /// Listens, or stops listening, to suit where the sound is going now.
    private func followTheOutput() {
        guard let player else { return }
        if isChoosingADevice || isSendingToAirPlay {
            note = isSendingToAirPlay ? CustomVisualizer.notListening(to: "an AirPlay device") : nil
            guard isListening else { return }
            isListening = false
            listener.stop()
            return
        }
        let output = SoundOutput.current()
        if CustomVisualizer.mayListen(outputDelay: output?.delay) {
            note = nil
            guard !isListening else { return }
            isListening = true
            listener.listen(to: player)
        } else {
            note = CustomVisualizer.notListening(to: output?.name ?? "this output")
            guard isListening else { return }
            isListening = false
            listener.stop()
        }
    }
}

/// One of Particle Accelerator's visuals, moving to the song that's playing. It shows
/// the visual as that project's library has it (its standard): nothing about it is
/// changed or kept here.
struct CustomVisualizerView: View {
    /// Which one, by Particle Accelerator's number for it (`CustomVisualizer.offered`).
    let number: Int
    @Environment(AppModel.self) private var model
    /// Settings → Play Options → Custom Visualizer → Quality.
    @AppStorage(CustomVisualizer.qualityKey) private var quality = CustomVisualizer.standardQuality

    var body: some View {
        let listener = model.visualizerSound.listener
        AcceleratorView(listener: listener, settings: settings)
            .overlay(alignment: .bottom) {
                // Why nothing moves: the app isn't listening (the sound is going to an
                // AirPlay device), or the listener can't hear what's playing.
                if let why = model.visualizerSound.note ?? listener.problem {
                    Text(why)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                        .padding(12)
                }
            }
            .onAppear { model.hearForVisualizer() }
            .accessibilityLabel(CustomVisualizer.title(number))
    }

    private var settings: AcceleratorSettings {
        var settings = AcceleratorSettings()
        settings.visual = number
        // The quality chosen in Settings, which is Medium until it's changed, not Auto.
        // Medium is the quality the owner tuned these three at, so it's the picture
        // they chose. And on the 2019 iMac Auto means High, where Visualizer 8 as the
        // owner has it takes the graphics card about 18 ms a frame, more than the 16.7
        // that 60 frames a second allows; at Medium it takes 4.5 (Particle
        // Accelerator's docs/OUTPUT.md, 2026-10-04).
        settings.quality =
            ParticleAccelerator.Quality(rawValue: CustomVisualizer.quality(quality)) ?? .medium
        return settings
    }
}
