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

/// The video on screen: which one, and which size of its picture.
struct ShowingVideo: Equatable {
    let source: SongVideo
    let quality: SongVideo.Quality
    /// It's as long as the song, so the song's timed lyrics fit it too.
    let keepsTime: Bool
}

/// Plays the library's files, and songs and their videos straight from YouTube. It opens
/// the library's files for reading and never changes them, and it saves nothing.
@MainActor
@Observable
final class Player {
    private(set) var queue = PlayQueue()
    private(set) var current: Track?
    private(set) var isPlaying = false
    /// Waiting for YouTube to say where the song can be played from.
    private(set) var isFetching = false
    /// The song has stopped to wait for more of itself to arrive.
    private(set) var isBuffering = false
    var problem: String?
    var volume: Float = UserDefaults.standard.object(forKey: "volume") as? Float ?? 1 {
        didSet {
            audio.volume = volume
            UserDefaults.standard.set(volume, forKey: "volume")
        }
    }
    let clock = PlaybackClock()

    /// Show each song's official video in place of its cover. Off whenever the app
    /// starts: while it's on, a song's sound comes from its video, not the library's file.
    private(set) var videoOn = false
    /// The video that's playing, when there is one.
    private(set) var video: ShowingVideo? {
        didSet {
            // A different video (or none): not a different size of the same one.
            if oldValue?.source.videoId != video?.source.videoId { onVideoChange?(video) }
        }
    }
    /// What to say where the video would be: still looking, or why there's none.
    private(set) var videoNote: String?
    /// The picture size last chosen. Nil is "the best there is".
    private(set) var videoPreference = Player.savedPreference()
    /// The owner switched a saved video to its cover: its sound plays on, unseen.
    private(set) var pictureHidden = false

    @ObservationIgnored var root: URL?
    @ObservationIgnored var onTrackChange: ((Track?) -> Void)?
    @ObservationIgnored var onTick: ((Double) -> Void)?
    /// A video took over from the song, or the song is back (nil).
    @ObservationIgnored var onVideoChange: ((ShowingVideo?) -> Void)?
    /// A song played to its end (not skipped): that's what counts as a play.
    @ObservationIgnored var onFinished: ((Track) -> Void)?
    /// Where a YouTube video's audio can be played from (the engine asks YouTube).
    @ObservationIgnored var findStream: ((String) async throws -> (URL, [String: String], Double?))?
    /// A song's official video, or nil if it has none (the engine asks YouTube Music).
    @ObservationIgnored var findVideo: ((Track) async throws -> SongVideo?)?
    @ObservationIgnored private let audio = AVPlayer()
    @ObservationIgnored private var observers: [Any] = []

    /// What the player has been given to play.
    private enum Loaded { case file, youtubeSound, video }
    @ObservationIgnored private var loaded = Loaded.file
    /// Goes up whenever something new is started, so an answer from YouTube that arrives
    /// after the owner has moved on is dropped.
    @ObservationIgnored private var ticket = 0
    /// The same, for a video being looked for while the song already plays: starting
    /// the song's sound mustn't drop it, and a new song or "Song" must.
    @ObservationIgnored private var videoTicket = 0
    /// Nothing is playing because a video that stopped working is being fetched again.
    @ObservationIgnored private var waitingForVideo = false
    /// A tap on the picture being played: it says whether new frames are still coming.
    @ObservationIgnored private var frames: AVPlayerItemVideoOutput?
    @ObservationIgnored private var lastFrame = Date()
    @ObservationIgnored private var lastNudge = Date.distantPast
    @ObservationIgnored private var nudges = 0
    /// Goes up when the picture has to be taken hold of again (`VideoSurface` watches it).
    private(set) var pictureRefresh = 0
    /// How many fresh addresses have been asked for since this song was started.
    @ObservationIgnored private var retries = 0
    private static let maxRetries = 2
    @ObservationIgnored private var itemWatch: NSKeyValueObservation?
    @ObservationIgnored private var waitingWatch: NSKeyValueObservation?
    @ObservationIgnored private var bufferingTimer: Task<Void, Never>?
    /// The videos found so far, by song. Their addresses stop working after a few hours,
    /// so each is kept for half an hour.
    @ObservationIgnored private var videos: [String: (found: SongVideo?, at: Date)] = [:]
    @ObservationIgnored private var lookUps: [String: Task<SongVideo?, Error>] = [:]
    private nonisolated static let headersKey = "AVURLAssetHTTPHeaderFieldsKey"

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
        waitingWatch = audio.observe(\.timeControlStatus) { [weak self] player, _ in
            let waiting = player.timeControlStatus == .waitingToPlayAtSpecifiedRate
            Task { @MainActor in self?.waitingChanged(waiting) }
        }
        setUpMediaKeys()
    }

    /// The player itself, for the view that shows a video's picture.
    var screen: AVPlayer { audio }

    /// There's a picture to show: a video from YouTube, or a saved video's own.
    var showsPicture: Bool { video != nil || (current?.isVideo == true && !pictureHidden) }

    /// What the Cover / Video switch says. A saved video counts as "Video" by itself.
    var pictureWanted: Bool { current?.isVideo == true ? !pictureHidden : videoOn }

    /// False while a video that isn't as long as the song is playing: the song's timed
    /// lyrics can't fit it. (A video of the same length may still be a little out:
    /// that's what the Karaoke button puts right.)
    var lyricsInTime: Bool { loaded != .video || video?.keepsTime ?? true }

    // MARK: what the screens call

    /// The position right now, to the hundredth of a second (the clock only moves five
    /// times a second, which is too coarse for timing lyrics by ear).
    var exactTime: Double {
        let seconds = audio.currentTime().seconds
        return seconds.isFinite ? max(0, seconds) : 0
    }

    func play(_ tracks: [Track], startAt index: Int = 0) {
        queue.play(tracks, startAt: index)
        start(queue.current)
    }

    /// Up Next: play `track` after the song that's playing (and after others put there
    /// the same way). With nothing playing, it plays now.
    func queueNext(_ track: Track) {
        if !queue.queueNext(track) {
            play([track])
        }
    }

    func playShuffled(_ tracks: [Track]) {
        guard !tracks.isEmpty else { return }
        queue.setShuffle(true)
        queue.play(tracks, startAt: Int.random(in: tracks.indices))
        start(queue.current)
    }

    func toggle() {
        guard let current, !isFetching else { return }
        if isPlaying {
            audio.pause()
            isPlaying = false
        } else if audio.currentItem == nil || audio.currentItem?.status == .failed {
            start(current)  // it never got going (YouTube refused it): ask afresh
            return
        } else {
            lastFrame = Date()
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
        lastFrame = Date()
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
        ticket += 1
        videoTicket += 1
        audio.pause()
        audio.replaceCurrentItem(with: nil)
        queue.play([])
        current = nil
        isPlaying = false
        isFetching = false
        video = nil
        videoNote = nil
        clock.time = 0
        clock.duration = 0
        onTrackChange?(nil)
        publishNowPlaying()
    }

    // MARK: the song's video

    /// Show the song's official video, or go back to its cover.
    func setVideo(_ on: Bool) {
        if current?.isVideo == true {
            // A saved video is its own file: the switch only shows or hides its picture.
            pictureHidden = !on
            return
        }
        guard on != videoOn else { return }
        videoOn = on
        guard let track = current else { return }
        if on {
            // The sound carries on (or goes on loading) while the video is looked for.
            startVideo(track, at: exactTime, interrupt: false, playing: isPlaying)
            return
        }
        videoTicket += 1  // a video still being looked for isn't wanted any more
        videoNote = nil
        let showing = loaded == .video && audio.currentItem != nil
        let keepsTime = video?.keepsTime ?? false
        video = nil
        if showing {
            startSound(track, at: keepsTime ? exactTime : 0, playing: isPlaying)
        } else if waitingForVideo {  // nothing else is on its way: play the song
            startSound(track, at: 0, playing: true)
        }
    }

    /// Choose the picture's size, as on YouTube. Nil is "the best there is". It's
    /// remembered for the videos that follow.
    func setQuality(_ quality: SongVideo.Quality?) {
        videoPreference = quality.map { VideoPreference(height: $0.height, label: $0.label) }
        UserDefaults.standard.set(
            videoPreference.flatMap { try? JSONEncoder().encode($0) }, forKey: "videoPreference")
        guard let showing = video, let track = current, loaded == .video else { return }
        let wanted = showing.source.quality(for: videoPreference)
        guard wanted != showing.quality else { return }
        let mine = nextTicket()
        Task {
            do {
                // The size on screen plays on until the new one is ready to take over.
                let item = try await Self.joined(showing.source, wanted)
                guard ticket == mine else { return }
                video = ShowingVideo(
                    source: showing.source, quality: wanted, keepsTime: showing.keepsTime)
                videoNote = nil
                begin(
                    track, item, .video, at: exactTime, playing: isPlaying,
                    length: showing.source.length)
            } catch {
                guard ticket == mine else { return }
                videoNote = "\(wanted.label) couldn't be loaded: \(error.localizedDescription)"
            }
        }
    }

    private static func savedPreference() -> VideoPreference? {
        UserDefaults.standard.data(forKey: "videoPreference").flatMap {
            try? JSONDecoder().decode(VideoPreference.self, from: $0)
        }
    }

    // MARK: playing

    private func nextTicket() -> Int {
        ticket += 1
        return ticket
    }

    private func start(_ track: Track?) {
        guard let track else { return }
        retries = 0
        video = nil
        videoNote = nil
        pictureHidden = false
        videoTicket += 1
        // The song itself always starts at once. With Video on, its video is looked for
        // meanwhile and takes over when it's ready (owner, 2026-10-01: no waiting in
        // silence). A saved video needs nothing from YouTube.
        startSound(track, at: 0, playing: true)
        if videoOn, !track.isVideo {
            startVideo(track, at: 0, interrupt: false, playing: true)
        }
    }

    /// The song's own sound: the library's file, or YouTube's audio for a song that
    /// isn't in the library.
    private func startSound(_ track: Track, at position: Double, playing: Bool) {
        waitingForVideo = false
        if let videoId = track.videoId {
            startFromYouTube(track, videoId, at: position, playing: playing)
            return
        }
        ticket += 1
        isFetching = false
        guard let root else { return }
        let url = root.appendingPathComponent(track.path)
        guard FileManager.default.isReadableFile(atPath: url.path) else {
            problem = "“\(track.title)” isn't where the library says it is. Try File → Reload Library."
            return
        }
        begin(track, AVPlayerItem(url: url), .file, at: position, playing: playing)
    }

    /// Nothing can play yet: YouTube is being asked where the song or its video is.
    private func wait(for track: Track, at position: Double) {
        audio.pause()
        audio.replaceCurrentItem(with: nil)
        itemWatch = nil
        let changed = current != track
        current = track
        isPlaying = false
        isFetching = true
        problem = nil
        clock.time = position
        if changed {
            clock.duration = track.durationS ?? 0
            onTrackChange?(track)
        }
        publishNowPlaying()
    }

    /// Nothing is saved: the audio is played from YouTube's own address for it.
    private func startFromYouTube(
        _ track: Track, _ videoId: String, at position: Double, playing: Bool
    ) {
        guard let findStream else { return }
        let mine = nextTicket()
        wait(for: track, at: position)
        Task {
            do {
                let (url, headers, length) = try await findStream(videoId)
                guard ticket == mine else { return }  // something else was chosen meanwhile
                let asset = AVURLAsset(
                    url: Spoil.address(url), options: [Self.headersKey: headers])
                begin(
                    track, AVPlayerItem(asset: asset), .youtubeSound, at: position,
                    playing: playing, length: (track.durationS ?? 0) > 0 ? nil : length)
            } catch {
                guard ticket == mine else { return }
                isFetching = false
                problem = "“\(track.title)” can't be played from YouTube: \(error.localizedDescription)"
            }
        }
    }

    /// The song's official video, picture and sound. Without `interrupt`, whatever is
    /// playing (or still loading) carries on until the video is ready to take over; a
    /// song with no video just plays on. With `interrupt` (a video that stopped working
    /// and is being started again), nothing plays while it's looked for.
    ///
    /// `position` is a place in the song, unless `inVideoTime` says it's one in the
    /// video (a video being started again where it stopped).
    private func startVideo(
        _ track: Track, at position: Double, interrupt: Bool, playing: Bool,
        inVideoTime: Bool = false
    ) {
        guard findVideo != nil else {
            if interrupt { startSound(track, at: position, playing: playing) }
            return
        }
        videoTicket += 1
        let mine = videoTicket
        if interrupt {
            ticket += 1
            wait(for: track, at: position)
            waitingForVideo = true
        }
        videoNote = "Looking for this song's video…"
        Task {
            do {
                guard let found = try await lookUpVideo(of: track) else {
                    guard videoTicket == mine, current == track else { return }
                    videoNote = "YouTube Music has no official video for this song."
                    if interrupt {
                        startSound(track, at: inVideoTime ? 0 : position, playing: playing)
                    }
                    return
                }
                guard videoTicket == mine, current == track else { return }
                let quality = found.quality(for: videoPreference)
                let item = try await Self.joined(found, quality)
                guard videoTicket == mine, current == track else { return }
                let keepsTime = found.keepsTime(with: track.durationS)
                // The song's sound may still be loading (a song from YouTube): then the
                // video simply starts. Otherwise it takes over where the song is, if
                // it's the song second for second, and from its beginning if not.
                let soundOn = !isFetching && audio.currentItem != nil
                let place = interrupt ? position : soundOn ? exactTime : 0
                let goOn = interrupt ? playing : soundOn ? isPlaying : true
                ticket += 1  // the song's own sound, if it's still on its way, is dropped
                video = ShowingVideo(source: found, quality: quality, keepsTime: keepsTime)
                videoNote = nil
                begin(
                    track, item, .video, at: keepsTime || inVideoTime ? place : 0,
                    playing: goOn, length: found.length)
            } catch {
                guard videoTicket == mine, current == track else { return }
                videos[track.id] = nil  // its addresses may be the trouble: ask afresh
                if retries < Self.maxRetries {
                    retries += 1
                    startVideo(
                        track, at: position, interrupt: interrupt, playing: playing,
                        inVideoTime: inVideoTime)
                    return
                }
                videoNote = "This song's video couldn't be loaded: \(error.localizedDescription)"
                if interrupt {
                    retries = 0
                    startSound(track, at: inVideoTime ? 0 : position, playing: playing)
                }
            }
        }
    }

    private func lookUpVideo(of track: Track) async throws -> SongVideo? {
        if let kept = videos[track.id], Date().timeIntervalSince(kept.at) < 30 * 60 {
            return kept.found
        }
        if let running = lookUps[track.id] { return try await running.value }
        guard let findVideo else { return nil }
        let asking = Task { try await findVideo(track) }
        lookUps[track.id] = asking
        defer { lookUps[track.id] = nil }
        let found = try await asking.value
        videos[track.id] = (found, Date())
        return found
    }

    /// Finding a video takes YouTube five to ten seconds. The next song's is looked for
    /// while this one plays, so one video follows another without a silence.
    private func lookAhead() {
        guard videoOn, let next = queue.upNext.first, !next.isVideo else { return }
        Task { _ = try? await lookUpVideo(of: next) }
    }

    /// YouTube serves a video's picture and its sound apart. Joined into one item they
    /// play, pause and seek as one, so they can't drift apart.
    private static func joined(
        _ video: SongVideo, _ quality: SongVideo.Quality
    ) async throws -> AVPlayerItem {
        AVPlayerItem(
            asset: try await join(
                picture: Spoil.address(quality.url), sound: video.sound, headers: video.headers,
                length: video.length))
    }

    /// Not on the main thread: opening the two addresses and joining them is real work.
    private nonisolated static func join(
        picture: URL, sound: URL, headers: [String: String], length: Double
    ) async throws -> AVAsset {
        let options: [String: Any] = [headersKey: headers]
        let pictureAsset = AVURLAsset(url: picture, options: options)
        let soundAsset = AVURLAsset(url: sound, options: options)
        guard let pictureTrack = try await pictureAsset.loadTracks(withMediaType: .video).first,
            let soundTrack = try await soundAsset.loadTracks(withMediaType: .audio).first
        else { throw CocoaError(.fileReadCorruptFile) }
        // Each of YouTube's streams claims to be twice as long as it is, so the length
        // YouTube gave for the video is the one that's used.
        let whole = AVMutableComposition()
        let wanted = CMTime(seconds: length, preferredTimescale: 600)
        for (track, kind) in [(pictureTrack, AVMediaType.video), (soundTrack, .audio)] {
            let has = try await track.load(.timeRange).duration
            guard
                let part = whole.addMutableTrack(
                    withMediaType: kind, preferredTrackID: kCMPersistentTrackID_Invalid)
            else { throw CocoaError(.fileReadCorruptFile) }
            try part.insertTimeRange(
                CMTimeRange(start: .zero, duration: CMTimeMinimum(wanted, has)), of: track,
                at: .zero)
        }
        return whole
    }

    private func begin(
        _ track: Track, _ item: AVPlayerItem, _ kind: Loaded, at position: Double = 0,
        playing: Bool = true, length: Double? = nil
    ) {
        problem = nil
        isFetching = false
        waitingForVideo = false
        loaded = kind
        watch(item)
        frames = nil
        if kind == .video || track.isVideo {
            let tap = AVPlayerItemVideoOutput(pixelBufferAttributes: nil)
            item.add(tap)
            frames = tap
        }
        lastFrame = Date()
        nudges = 0
        audio.replaceCurrentItem(with: item)
        if position > 0 {
            audio.seek(
                to: CMTime(seconds: position, preferredTimescale: 600), toleranceBefore: .zero,
                toleranceAfter: .zero)
        }
        if playing { audio.play() }
        let changed = current != track
        current = track
        isPlaying = playing
        clock.time = position
        clock.duration = length ?? track.durationS ?? 0
        if changed { onTrackChange?(track) }
        publishNowPlaying()
        lookAhead()
        Task {
            let image = await Covers.shared.load(track, root: root, size: .large)
            if current == track { publishNowPlaying(artwork: image) }
        }
    }

    private func tick(_ seconds: Double) {
        guard current != nil, !isFetching, seconds.isFinite else { return }
        if loaded == .youtubeSound {
            // YouTube's stream claims to be twice as long as the song (its second half is
            // silence), so the length YouTube Music gave is the one that counts: the
            // song ends there.
            if clock.duration > 0, seconds >= clock.duration - 0.2 {
                finished(audio.currentItem)
                return
            }
        } else if let length = audio.currentItem?.duration.seconds, length.isFinite, length > 0,
            abs(length - clock.duration) > 0.5
        {
            clock.duration = length
        }
        clock.time = seconds
        onTick?(seconds)
        checkPicture()
    }

    /// The picture has been seen to stop on one frame while the song and its lyrics
    /// carry on (owner, 2026-10-01), and the player reports nothing wrong. So new
    /// frames are counted: none for three seconds of playing, and the picture is
    /// fetched afresh from where the song is, and its layer takes hold again. What was
    /// seen is written down, because the cause isn't known yet. Tried against two
    /// minutes of real playback with a pause and three jumps: the longest gap between
    /// frames was 0.17 s, and this never fired.
    private func checkPicture() {
        guard let frames, isPlaying, audio.timeControlStatus == .playing else {
            lastFrame = Date()
            return
        }
        let now = audio.currentTime()
        if frames.hasNewPixelBuffer(forItemTime: now) {
            _ = frames.copyPixelBuffer(forItemTime: now, itemTimeForDisplay: nil)
            lastFrame = Date()
            return
        }
        let stuck = Date().timeIntervalSince(lastFrame)
        guard stuck > 3, Date().timeIntervalSince(lastNudge) > 8 else { return }
        let what = video.map { "\($0.source.videoId) \($0.quality.label)" } ?? "a saved video"
        guard nudges < 3 else {
            if nudges == 3 {
                nudges += 1
                PlayerLog.note("picture still stuck after 3 nudges: \(what) at \(Int(now.seconds)) s")
            }
            return
        }
        nudges += 1
        lastNudge = Date()
        lastFrame = Date()
        PlayerLog.note(
            "no new picture for \(String(format: "%.1f", stuck)) s: \(what) at "
                + "\(Int(now.seconds)) s of “\(current?.title ?? "?")”; nudge \(nudges)")
        pictureRefresh += 1
        audio.seek(to: now, toleranceBefore: .zero, toleranceAfter: .zero)
    }

    private func finished(_ item: AVPlayerItem?) {
        guard item === audio.currentItem else { return }
        if let current { onFinished?(current) }
        if let track = queue.advance(finished: true) {
            start(track)
        } else {  // the end of the queue
            isPlaying = false
            audio.seek(to: .zero)
            clock.time = 0
            publishNowPlaying()
        }
    }

    // MARK: when it doesn't play

    /// Apple's player doesn't announce an address it couldn't open: the song just sits at
    /// 0:00 looking as if it's playing. So each item is watched.
    private func watch(_ item: AVPlayerItem) {
        itemWatch = item.observe(\.status) { [weak self] item, _ in
            guard item.status == .failed else { return }
            let failed = ObjectIdentifier(item)
            Task { @MainActor in self?.itemFailed(failed) }
        }
    }

    private func failed(_ item: AVPlayerItem?) {
        if let item { itemFailed(ObjectIdentifier(item)) }
    }

    /// YouTube's addresses don't always work: it refuses one now and then, and they stop
    /// working after a few hours. A fresh one is asked for, twice at most, and the song
    /// carries on from where it was.
    private func itemFailed(_ failed: ObjectIdentifier) {
        guard let item = audio.currentItem, ObjectIdentifier(item) == failed, let track = current
        else { return }
        let position = clock.time
        let playing = isPlaying
        if loaded != .file, retries < Self.maxRetries {
            retries += 1
            if loaded == .video {
                videos[track.id] = nil
                startVideo(track, at: position, interrupt: true, playing: playing, inVideoTime: true)
            } else if let videoId = track.videoId {
                startFromYouTube(track, videoId, at: position, playing: playing)
            }
            return
        }
        if loaded == .video {  // the song can still be heard
            let keepsTime = video?.keepsTime ?? false
            retries = 0
            video = nil
            videoNote = "This song's video stopped working, so its sound is playing instead."
            startSound(track, at: keepsTime ? position : 0, playing: playing)
            return
        }
        audio.pause()
        isPlaying = false
        isBuffering = false
        problem =
            loaded == .file
            ? "“\(track.title)” couldn't be played."
            : "“\(track.title)” can't be played from YouTube right now. Try it again in a minute."
        publishNowPlaying()
    }

    private func waitingChanged(_ waiting: Bool) {
        bufferingTimer?.cancel()
        guard waiting else {
            isBuffering = false
            return
        }
        // Only said after most of a second: every song waits for a moment as it starts.
        bufferingTimer = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(800))
            guard !Task.isCancelled, let self else { return }
            if audio.timeControlStatus == .waitingToPlayAtSpecifiedRate { isBuffering = true }
        }
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

/// For checking by hand that a refused address is recovered from. Started with
/// MUSICORG_SPOIL=2, the app turns the first two addresses the player is given into ones
/// YouTube refuses (as it does by itself now and then).
@MainActor
enum Spoil {
    private static var left = Int(ProcessInfo.processInfo.environment["MUSICORG_SPOIL"] ?? "") ?? 0

    static func address(_ url: URL) -> URL {
        guard left > 0 else { return url }
        left -= 1
        let spoiled = url.absoluteString.replacingOccurrences(
            of: #"expire=\d+"#, with: "expire=1700000000", options: .regularExpression)
        return URL(string: spoiled) ?? url
    }
}

/// A few lines about what the player saw when something went wrong that it can't
/// explain, kept in the app's own cache folder (never in the library), so the next
/// time it happens there's something to read. The file is started again when it grows.
enum PlayerLog {
    static let file: URL = {
        let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
        return caches.appendingPathComponent("org.musicorganizer.app/player.log")
    }()

    static func note(_ text: String) {
        let stamp = ISO8601DateFormatter().string(from: Date())
        let line = Data("\(stamp) \(text)\n".utf8)
        FileHandle.standardError.write(line)
        let manager = FileManager.default
        try? manager.createDirectory(
            at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        let size = (try? manager.attributesOfItem(atPath: file.path)[.size] as? Int) ?? 0
        if size > 200_000 || !manager.fileExists(atPath: file.path) {
            try? line.write(to: file)
        } else if let handle = try? FileHandle(forWritingTo: file) {
            defer { try? handle.close() }
            _ = try? handle.seekToEnd()
            try? handle.write(contentsOf: line)
        }
    }
}
