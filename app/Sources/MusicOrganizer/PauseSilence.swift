import AppKit
import CoreAudio
import MusicOrganizerKit

/// Makes Pause stop the sound at once on an output with a long delay.
///
/// An AirPlay device holds two seconds of sound. On Pause, Apple's player stops feeding
/// it at once but leaves the stream to the device open, so what was already sent plays
/// out: the song carries on for two seconds. (Measured on 2026-10-05, on an Apple TV: a
/// player built on an audio engine has the stream shut within 0.03 s, which is why the
/// owner didn't hear this from other apps. Taking the song out of our player didn't
/// help: the stream still ran on for 2.1 s.)
///
/// The Mac's mute reaches an AirPlay device in a few thousandths of a second (0.006 s
/// when measured), as its mute key does. So on Pause the output is muted, and unmuted
/// again once what was in the pipe has run out.
///
/// - It's only done on such an output, and only for a pause the owner asked for.
/// - An output that's already muted is left alone: that's the owner's own doing.
/// - Only the output this muted is ever unmuted, and it's unmuted if the app is quit
///   in those two seconds.
/// - Play isn't made any quicker by this. Sound takes 1.75 s to come out of an AirPlay
///   device after it's sent, whatever app sends it.
@MainActor
final class PauseSilence {
    /// The output this has muted, until it's unmuted again.
    private var muted: AudioObjectID?
    private var unmuting: Task<Void, Never>?
    private var quitWatch: NSObjectProtocol?

    init() {
        quitWatch = NotificationCenter.default.addObserver(
            forName: NSApplication.willTerminateNotification, object: nil, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.end() }
        }
    }

    /// The owner paused the song.
    func paused() {
        guard let output = SoundOutput.current(), LongDelayOutput.isOne(delay: output.delay)
        else { return }
        if muted == nil {
            guard SoundOutput.isMuted(output.device) == false,
                SoundOutput.setMuted(true, on: output.device)
            else { return }
            muted = output.device
        }
        // Paused again before the last pause's silence was over: it starts afresh, since
        // there's newer sound in the pipe now.
        let seconds = LongDelayOutput.silenceAfterPause(delay: output.delay)
        unmuting?.cancel()
        unmuting = Task { [weak self] in
            try? await Task.sleep(for: .seconds(seconds))
            guard !Task.isCancelled else { return }
            self?.end()
        }
    }

    /// Unmutes the output, if this muted it.
    func end() {
        unmuting?.cancel()
        unmuting = nil
        guard let device = muted else { return }
        muted = nil
        _ = SoundOutput.setMuted(false, on: device)
    }
}
