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
@MainActor
final class VisualizerSound {
    let listener = MusicListener()
    private var isListening = false

    func hear(_ player: AVPlayer) {
        guard !isListening else { return }
        isListening = true
        listener.listen(to: player)
    }
}

/// One of Particle Accelerator's visuals, moving to the song that's playing. It shows
/// the visual as that project's library has it (its standard): nothing about it is
/// changed or kept here.
struct CustomVisualizerView: View {
    /// Which one, by Particle Accelerator's number for it (`CustomVisualizer.offered`).
    let number: Int
    @Environment(AppModel.self) private var model

    var body: some View {
        let listener = model.visualizerSound.listener
        AcceleratorView(listener: listener, settings: settings)
            .overlay(alignment: .bottom) {
                // Why nothing moves, when the listener can't hear what's playing.
                if let problem = listener.problem {
                    Text(problem)
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
        // Medium, not Auto. It's the quality the owner tuned these three at, so it's the
        // picture they chose. And on the 2019 iMac Auto means High, where Visualizer 8
        // as the owner has it takes the graphics card about 18 ms a frame, more than
        // the 16.7 that 60 frames a second allows; at Medium it takes 4.5 (Particle
        // Accelerator's docs/OUTPUT.md, 2026-10-04). Auto belongs here once it adapts.
        settings.quality = .medium
        return settings
    }
}
