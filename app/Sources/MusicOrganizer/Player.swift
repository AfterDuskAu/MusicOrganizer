import AVFoundation
import AppKit
import MediaPlayer
import MusicOrganizerKit
import Observation

/// The position in the song. Its own object, so only the scrubber redraws as it moves.
@MainActor
@Observable
final class PlaybackClock {
    var time: Double = 0
    var duration: Double = 0
}

/// Plays the library's files. It opens them for reading and never changes them.
@MainActor
@Observable
final class Player {
    private(set) var queue = PlayQueue()
    private(set) var current: Track?
    private(set) var isPlaying = false
    var problem: String?
    var volume: Float = UserDefaults.standard.object(forKey: "volume") as? Float ?? 1 {
        didSet {
            audio.volume = volume
            UserDefaults.standard.set(volume, forKey: "volume")
        }
    }
    let clock = PlaybackClock()

    @ObservationIgnored var root: URL?
    @ObservationIgnored var onTrackChange: ((Track?) -> Void)?
    @ObservationIgnored var onTick: ((Double) -> Void)?
    @ObservationIgnored private let audio = AVPlayer()
    @ObservationIgnored private var observers: [Any] = []

    init() {
        audio.volume = volume
        let interval = CMTime(seconds: 0.2, preferredTimescale: 600)
        observers.append(
            audio.addPeriodicTimeObserver(forInterval: interval, queue: .main) { [weak self] time in
                MainActor.assumeIsolated { self?.tick(time.seconds) }
            })
        let center = NotificationCenter.default
        observers.append(
            center.addObserver(
                forName: AVPlayerItem.didPlayToEndTimeNotification, object: nil, queue: .main
            ) { [weak self] note in
                let item = note.object as? AVPlayerItem
                MainActor.assumeIsolated { self?.finished(item) }
            })
        observers.append(
            center.addObserver(
                forName: AVPlayerItem.failedToPlayToEndTimeNotification, object: nil, queue: .main
            ) { [weak self] note in
                let item = note.object as? AVPlayerItem
                MainActor.assumeIsolated { self?.failed(item) }
            })
        setUpMediaKeys()
    }

    // MARK: what the screens call

    func play(_ tracks: [Track], startAt index: Int = 0) {
        queue.play(tracks, startAt: index)
        start(queue.current)
    }

    func playShuffled(_ tracks: [Track]) {
        guard !tracks.isEmpty else { return }
        queue.setShuffle(true)
        queue.play(tracks, startAt: Int.random(in: tracks.indices))
        start(queue.current)
    }

    func toggle() {
        guard current != nil else { return }
        if isPlaying {
            audio.pause()
            isPlaying = false
        } else {
            audio.play()
            isPlaying = true
        }
        publishNowPlaying()
    }

    func next() {
        if let track = queue.advance() { start(track) }
    }

    /// Like every player: back to the start of the song, or to the song before if this
    /// one has only just begun.
    func previous() {
        if clock.time > 3 || queue.position == 0 && queue.repeatMode != .all {
            seek(to: 0)
        } else if let track = queue.back() {
            start(track)
        }
    }

    func jump(toUpNext offset: Int) {
        if let track = queue.jump(toUpNext: offset) { start(track) }
    }

    func seek(to seconds: Double) {
        guard current != nil else { return }
        clock.time = seconds
        onTick?(seconds)
        audio.seek(
            to: CMTime(seconds: seconds, preferredTimescale: 600), toleranceBefore: .zero,
            toleranceAfter: .zero
        ) { [weak self] _ in
            Task { @MainActor in self?.publishNowPlaying() }
        }
    }

    func setShuffle(_ on: Bool) { queue.setShuffle(on) }

    func cycleRepeat() {
        let all = PlayQueue.Repeat.allCases
        queue.repeatMode = all[(all.firstIndex(of: queue.repeatMode)! + 1) % all.count]
    }

    func stop() {
        audio.pause()
        audio.replaceCurrentItem(with: nil)
        queue.play([])
        current = nil
        isPlaying = false
        clock.time = 0
        clock.duration = 0
        onTrackChange?(nil)
        publishNowPlaying()
    }

    // MARK: playing

    private func start(_ track: Track?) {
        guard let track, let root else { return }
        let url = root.appendingPathComponent(track.path)
        guard FileManager.default.isReadableFile(atPath: url.path) else {
            problem = "“\(track.title)” isn't where the library says it is. Try File → Reload Library."
            return
        }
        problem = nil
        audio.replaceCurrentItem(with: AVPlayerItem(url: url))
        audio.play()
        current = track
        isPlaying = true
        clock.time = 0
        clock.duration = track.durationS ?? 0
        onTrackChange?(track)
        publishNowPlaying()
        Task {
            let image = await Covers.shared.load(track, root: root, size: .large)
            if current == track { publishNowPlaying(artwork: image) }
        }
    }

    private func tick(_ seconds: Double) {
        guard current != nil, seconds.isFinite else { return }
        clock.time = seconds
        if let length = audio.currentItem?.duration.seconds, length.isFinite, length > 0,
            abs(length - clock.duration) > 0.5
        {
            clock.duration = length
        }
        onTick?(seconds)
    }

    private func finished(_ item: AVPlayerItem?) {
        guard item === audio.currentItem else { return }
        if let track = queue.advance(finished: true) {
            start(track)
        } else {  // the end of the queue
            isPlaying = false
            audio.seek(to: .zero)
            clock.time = 0
            publishNowPlaying()
        }
    }

    private func failed(_ item: AVPlayerItem?) {
        guard item === audio.currentItem, let current else { return }
        problem = "“\(current.title)” couldn't be played."
        isPlaying = false
    }

    // MARK: the keyboard's media keys and the menu bar's Now Playing

    private func setUpMediaKeys() {
        let commands = MPRemoteCommandCenter.shared()
        func on(_ command: MPRemoteCommand, _ action: @escaping @MainActor (Player) -> Void) {
            command.addTarget { [weak self] _ in
                guard let self else { return .commandFailed }
                MainActor.assumeIsolated { action(self) }
                return .success
            }
        }
        on(commands.playCommand) { if !$0.isPlaying { $0.toggle() } }
        on(commands.pauseCommand) { if $0.isPlaying { $0.toggle() } }
        on(commands.togglePlayPauseCommand) { $0.toggle() }
        on(commands.nextTrackCommand) { $0.next() }
        on(commands.previousTrackCommand) { $0.previous() }
        commands.changePlaybackPositionCommand.addTarget { [weak self] event in
            guard let self, let event = event as? MPChangePlaybackPositionCommandEvent else {
                return .commandFailed
            }
            let position = event.positionTime
            MainActor.assumeIsolated { self.seek(to: position) }
            return .success
        }
    }

    @ObservationIgnored private var artwork: (path: String, image: MPMediaItemArtwork)?

    private func publishNowPlaying(artwork image: NSImage? = nil) {
        let center = MPNowPlayingInfoCenter.default()
        guard let current else {
            center.nowPlayingInfo = nil
            center.playbackState = .stopped
            return
        }
        if let image {
            artwork = (current.path, MPMediaItemArtwork(boundsSize: image.size) { _ in image })
        }
        var info: [String: Any] = [
            MPMediaItemPropertyTitle: current.title,
            MPMediaItemPropertyArtist: current.artistName,
            MPMediaItemPropertyAlbumTitle: current.albumName,
            MPMediaItemPropertyPlaybackDuration: clock.duration,
            MPNowPlayingInfoPropertyElapsedPlaybackTime: clock.time,
            MPNowPlayingInfoPropertyPlaybackRate: isPlaying ? 1.0 : 0.0,
        ]
        if let artwork, artwork.path == current.path {
            info[MPMediaItemPropertyArtwork] = artwork.image
        }
        center.nowPlayingInfo = info
        center.playbackState = isPlaying ? .playing : .paused
    }
}

extension PlayQueue.Repeat {
    var label: String {
        switch self {
        case .off: "Off"
        case .all: "All"
        case .one: "One Song"
        }
    }
}
