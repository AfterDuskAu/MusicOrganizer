import Foundation

/// One song in the library, as the engine's `library.tracks` describes it
/// (docs/ENGINE_API.md → Track). Paths are relative to the library root.
public struct Track: Decodable, Identifiable, Hashable, Sendable {
    public enum Lyrics: String, Decodable, Sendable {
        case synced, plain, none
    }

    public let trackId: String?
    public let path: String
    public let title: String
    public let artist: String?
    public let albumArtist: String?
    public let album: String?
    public let year: Int?
    public let track: Int?
    public let disc: Int?
    public let genre: String?
    public let durationS: Double?
    public let explicit: Bool
    public let onlyCopy: Bool
    /// Where the audio came from: "rip_copy", "youtube_music"…
    public let source: String?
    /// The YouTube video this song came from, if it came from one.
    public let sourceId: String?
    public let match: String?
    /// When the song came into the library (ISO time, so text order is time order).
    public let acquired: String?
    public let format: String?
    public let bitrateKbps: Int?
    public let cover: String?
    public let embeddedCover: Bool
    public let lyrics: Lyrics
    /// A picture on the web: only for a song played from YouTube Music, not in the library.
    public let artUrl: String?
    /// A saved video (`Music/Videos/…mp4`), and the height of its picture in lines.
    public let video: Bool?
    public let height: Int?

    public var id: String { path }
    /// Set for a song played straight from YouTube Music: its path is "yt:<videoId>".
    public var videoId: String? { path.hasPrefix("yt:") ? String(path.dropFirst(3)) : nil }
    public var artistName: String { artist ?? albumArtist ?? "Unknown Artist" }
    public var albumName: String { album ?? "" }
    /// The folder the file is in: one folder is one album.
    public var folder: String {
        path.lastIndex(of: "/").map { String(path[..<$0]) } ?? ""
    }
    public var hasCover: Bool { cover != nil || embeddedCover || artUrl != nil }
    /// Copied in under its own name, still waiting to be identified.
    public var isUnconfirmed: Bool { match == "unconfirmed" }
    /// Downloaded from YouTube Music because the owner asked for it (no rip behind it).
    public var isDownload: Bool { source == "youtube_music" && match == nil }
    /// A video saved in the library: it plays with its picture, and has its own list.
    public var isVideo: Bool { video == true }

    public init(
        path: String, title: String, artist: String? = nil, albumArtist: String? = nil,
        album: String? = nil, year: Int? = nil, track: Int? = nil, disc: Int? = nil,
        genre: String? = nil, durationS: Double? = nil, explicit: Bool = false,
        onlyCopy: Bool = false, match: String? = nil, format: String? = nil,
        bitrateKbps: Int? = nil, cover: String? = nil, embeddedCover: Bool = false,
        lyrics: Lyrics = .none, trackId: String? = nil, acquired: String? = nil,
        sourceId: String? = nil, artUrl: String? = nil, source: String? = nil,
        video: Bool? = nil, height: Int? = nil
    ) {
        self.trackId = trackId
        self.path = path
        self.title = title
        self.artist = artist
        self.albumArtist = albumArtist
        self.album = album
        self.year = year
        self.track = track
        self.disc = disc
        self.genre = genre
        self.durationS = durationS
        self.explicit = explicit
        self.onlyCopy = onlyCopy
        self.match = match
        self.acquired = acquired
        self.sourceId = sourceId
        self.source = source
        self.artUrl = artUrl
        self.video = video
        self.height = height
        self.format = format
        self.bitrateKbps = bitrateKbps
        self.cover = cover
        self.embeddedCover = embeddedCover
        self.lyrics = lyrics
    }
}

public struct TrackList: Decodable, Sendable {
    public let root: String
    public let tracks: [Track]
}

public struct TrackLyrics: Decodable, Sendable {
    public let synced: String?
    public let plain: String?
    /// From `lyrics.for_video` only: how the lyrics were timed to the video ("audio",
    /// "captions", "caption_text", "lrclib").
    public let how: String?
    /// From `lyrics.for_video` only: one plain line about how it went, when there's
    /// something to say (the day's downloads are used up, say).
    public let note: String?
}

/// What Karaoke costs. Lining lyrics up with a video fetches the video's whole sound
/// from YouTube, which counts as a download; and the song's too, unless the song is a
/// file in the library (or the video is a saved one, whose sound is read from its file).
public enum KaraokeCost {
    public static func downloads(songIsAFile: Bool) -> Int { songIsAFile ? 1 : 2 }

    /// "Uses 1 download from your daily limit."
    public static func words(songIsAFile: Bool) -> String {
        songIsAFile
            ? "Uses 1 download from your daily limit."
            : "Uses 2 downloads from your daily limit: the song's sound and the video's."
    }
}

/// One result of a YouTube Music search (the engine's Candidate).
public struct SearchResult: Codable, Identifiable, Hashable, Sendable {
    public let videoId: String
    public let title: String
    public let artists: [String]
    public let album: String?
    public let durationS: Double?
    public let isExplicit: Bool?
    public let thumbnail: String?
    /// How often it's been played on YouTube Music, as it writes it ("497M").
    public let plays: String?

    public init(
        videoId: String, title: String, artists: [String], album: String? = nil,
        durationS: Double? = nil, isExplicit: Bool? = nil, thumbnail: String? = nil,
        plays: String? = nil
    ) {
        self.videoId = videoId
        self.title = title
        self.artists = artists
        self.album = album
        self.durationS = durationS
        self.isExplicit = isExplicit
        self.thumbnail = thumbnail
        self.plays = plays
    }

    public var id: String { videoId }

    /// The result as a song the player can play, straight from YouTube Music.
    public var track: Track {
        Track(
            path: "yt:\(videoId)", title: title,
            artist: artists.isEmpty ? nil : artists.joined(separator: ", "), album: album,
            durationS: durationS, explicit: isExplicit ?? false, sourceId: videoId,
            artUrl: thumbnail)
    }
}

public struct SearchAnswer: Decodable, Sendable {
    public let results: [SearchResult]
}

public struct StreamAnswer: Decodable, Sendable {
    public let url: String
    public let httpHeaders: [String: String]
    public let durationS: Double?
    /// The song's thumbs-up count on YouTube, when YouTube shows one.
    public let likes: Int?
}

/// A Play Options setting switched on a page (lyrics off with the button by the volume
/// slider, say) is switched only for now: the setting itself stays as chosen in
/// Settings, and the page goes back to it after a while. This is how long that while
/// can be, and how it's said.
public enum PageChanges {
    /// The key the choice is saved under: minutes, or 0 for "until the app is next opened".
    public static let key = "pageChangesLastMinutes"
    public static let standard = 30
    public static let options = [5, 30, 60, 0]

    /// "5 minutes", "1 hour", "Until the app is next opened": a choice in Settings.
    public static func label(_ minutes: Int) -> String {
        minutes <= 0 ? "Until the app is next opened" : time(minutes)
    }

    /// "in 30 minutes", "when the app is next opened": when a page goes back.
    public static func goesBack(_ minutes: Int) -> String {
        minutes <= 0 ? "when the app is next opened" : "in \(time(minutes))"
    }

    private static func time(_ minutes: Int) -> String {
        if minutes % 60 == 0 {
            let hours = minutes / 60
            return hours == 1 ? "1 hour" : "\(hours) hours"
        }
        return minutes == 1 ? "1 minute" : "\(minutes) minutes"
    }

    /// What a page shows: the change made on it for now, or else the setting.
    public static func shown(_ change: Bool?, setting: Bool) -> Bool { change ?? setting }
}

/// The custom visualizers the app offers (owner, 2026-10-04): three of the visuals made
/// in their own project, Particle Accelerator, by the numbers that project gives them.
/// One of them moves to the music on the Local Visualizer, where the song's cover would
/// be. Both choices are made in Settings → Play Options.
public enum CustomVisualizer {
    public static let offered = [5, 7, 8]
    /// The one that shows until another is chosen.
    public static let standard = 7
    /// The key the chosen one is saved under: its number.
    public static let whichKey = "customVisualizer"
    /// The key for "use the custom visualizer instead of the song or album cover".
    public static let useKey = "useCustomVisualizer"

    /// The one to show for what was saved: that one while it's still offered, or else
    /// the standard.
    public static func chosen(_ saved: Int?) -> Int {
        guard let saved, offered.contains(saved) else { return standard }
        return saved
    }

    /// What a menu calls one: "Visualizer 7".
    public static func title(_ number: Int) -> String { "Visualizer \(number)" }
}

/// What the Local Visualizer's switch says: the song with its cover, its video, or the
/// song with the custom visualizer where the cover would be.
public enum PagePicture: String, CaseIterable, Sendable {
    case song, video, visualizer

    public var label: String {
        switch self {
        case .song: "Song"
        case .video: "Video"
        case .visualizer: "Visualizer"
        }
    }

    /// The video comes first: the visualizer only ever stands in for the cover.
    public static func chosen(videoWanted: Bool, visualizerOn: Bool) -> PagePicture {
        videoWanted ? .video : visualizerOn ? .visualizer : .song
    }
}

/// A big count as YouTube writes them: 950, 5.2K, 900K, 1.3M, 2.1B.
public enum CompactCount {
    public static func text(_ count: Int) -> String {
        let steps: [(Double, String)] = [(1e9, "B"), (1e6, "M"), (1e3, "K")]
        let number = Double(max(count, 0))
        for (index, (size, letter)) in steps.enumerated() {
            guard number >= size else { continue }
            let amount = number / size
            // "999.6K" would round to "1000K": that's "1M".
            if amount >= 999.5, index > 0 { return "1" + steps[index - 1].1 }
            if amount >= 9.95 { return "\(Int(amount.rounded()))\(letter)" }
            let tenths = (amount * 10).rounded() / 10
            return tenths == tenths.rounded()
                ? "\(Int(tenths))\(letter)" : String(format: "%.1f%@", tenths, letter)
        }
        return "\(Int(number))"
    }
}

/// `settings.get`: the engine's own settings that the app shows.
public struct EngineSettings: Decodable, Equatable, Sendable {
    public let dailyCap: Int
    public let dailyCapDefault: Int
    public let dailyCapMax: Int
}

public struct PlanAnswer: Decodable, Sendable {
    public struct Summary: Decodable, Sendable {
        public let operations: Int
        public let skipped: [String: Int]?
        /// For a plan of downloads: how many, roughly how long the queue will take, and
        /// how many days the daily limit spreads them over.
        public let downloads: Int?
        public let estMinutes: Int?
        public let days: Int?
    }
    public let planId: String
    public let summary: Summary
}

public struct BatchAnswer: Decodable, Sendable {
    public let batchId: String
}

public struct JobsAnswer: Decodable, Sendable {
    public struct Job: Decodable, Sendable {
        public let state: String
        public let reason: String?
        public let message: String?

        public init(state: String, reason: String? = nil, message: String? = nil) {
            self.state = state
            self.reason = reason
            self.message = message
        }

        public var isOver: Bool { state != "queued" && state != "running" }
        public var worked: Bool { state == "done" }
    }
    public let jobs: [Job]
}

public struct PlayCount: Decodable, Equatable, Sendable {
    public let count: Int
    public let lastPlayed: String?

    public init(count: Int, lastPlayed: String? = nil) {
        self.count = count
        self.lastPlayed = lastPlayed
    }
}

public struct Playlist: Decodable, Identifiable, Hashable, Sendable {
    public let id: String
    public var name: String
    public var trackIds: [String]

    public init(id: String, name: String, trackIds: [String] = []) {
        self.id = id
        self.name = name
        self.trackIds = trackIds
    }
}

/// `listening.get`: the owner's favourites, play counts and playlists. Songs are named
/// by their track id, which stays the same when a file is renamed.
public struct Listening: Decodable, Sendable {
    public var favourites: [String]  // most recent first
    public var plays: [String: PlayCount]
    public var playlists: [Playlist]
    /// Downloads the owner has moved into the main library's lists.
    public var library: [String]?
    /// YouTube ids of songs played all the way through from YouTube (picks and search
    /// results, not the owner's own): the red checkmark.
    public var heard: [String]?

    public static let empty = Listening(favourites: [], plays: [:], playlists: [])

    public init(
        favourites: [String], plays: [String: PlayCount], playlists: [Playlist],
        library: [String] = []
    ) {
        self.favourites = favourites
        self.plays = plays
        self.playlists = playlists
        self.library = library
    }
}

/// `listening.heard`: how many times a YouTube song has been heard to its end.
public struct HeardCount: Decodable, Sendable {
    public let count: Int
}

public struct MovedAnswer: Decodable, Sendable {
    public let library: [String]
}

/// One of the owner's downloads that hasn't arrived (`queue.downloads`): waiting its
/// turn, downloading, or ended without the song.
public struct PendingDownload: Decodable, Identifiable, Equatable, Sendable {
    public let jobId: Int
    public let state: String
    public let reason: String?
    public let message: String?
    public let videoId: String?
    public let title: String?
    public let artists: [String]
    public let video: Bool
    public let height: Int?
    public let fps: Int?
    public let thumbnail: String?
    /// The share of it that has arrived, 0 to 1, while it's downloading and that's known.
    public let progress: Double?

    public init(
        jobId: Int, state: String, reason: String? = nil, message: String? = nil,
        videoId: String? = nil, title: String? = nil, artists: [String] = [], video: Bool = false,
        height: Int? = nil, fps: Int? = nil, thumbnail: String? = nil, progress: Double? = nil
    ) {
        self.jobId = jobId
        self.state = state
        self.reason = reason
        self.message = message
        self.videoId = videoId
        self.title = title
        self.artists = artists
        self.video = video
        self.height = height
        self.fps = fps
        self.thumbnail = thumbnail
        self.progress = progress
    }

    public var id: Int { jobId }
    /// Still to come: waiting its turn, or downloading now.
    public var isActive: Bool { state == "queued" || state == "running" }
    public var isRunning: Bool { state == "running" }
    public var name: String { title ?? videoId ?? "Download" }
    public var artistName: String { artists.joined(separator: ", ") }
    /// Why it ended without the song, in the engine's plain words. Nil while it's active.
    public var problem: String? {
        guard !isActive else { return nil }
        return message ?? reason?.replacingOccurrences(of: "_", with: " ") ?? "It didn't work."
    }
    /// The share that has arrived as a whole percentage, while that's known.
    public var percent: Int? {
        guard isRunning, let progress else { return nil }
        return Int((min(max(progress, 0), 1) * 100).rounded(.down))
    }

    /// What the list says while it's on its way: "Waiting its turn…", "Downloading…",
    /// "Downloading… 42%", and once it has all arrived, the work that's left.
    public var progressNote: String {
        guard isRunning else { return "Waiting its turn…" }
        guard let percent else { return "Downloading…" }
        return percent >= 100 ? "Checking and naming it…" : "Downloading… \(percent)%"
    }
}

public struct DownloadsAnswer: Decodable, Sendable {
    public let downloads: [PendingDownload]
}

/// `queue.status`: how the download queue is doing, and how much of the daily limit
/// has been used.
public struct QueueStatus: Decodable, Equatable, Sendable {
    public let state: String
    public let reason: String?
    /// When a pause by YouTube ends.
    public let resumeAt: String?
    public let queued: Int
    public let running: Int
    /// Downloads in the last 24 hours, and the most there may be.
    public let dailyCount: Int
    public let dailyCap: Int
    /// When the next download may start, while the daily limit is reached.
    public let dailyResumeAt: String?

    public init(
        state: String = "idle", reason: String? = nil, resumeAt: String? = nil, queued: Int = 0,
        running: Int = 0, dailyCount: Int = 0, dailyCap: Int = 250, dailyResumeAt: String? = nil
    ) {
        self.state = state
        self.reason = reason
        self.resumeAt = resumeAt
        self.queued = queued
        self.running = running
        self.dailyCount = dailyCount
        self.dailyCap = dailyCap
        self.dailyResumeAt = dailyResumeAt
    }

    /// Why downloads that are waiting aren't moving, and when they will: the daily
    /// limit, or YouTube refusing this computer for a while. Nil while the queue is
    /// simply working through them. `clock` writes a time the way the owner reads it.
    public func holdUp(clock: (Date) -> String) -> String? {
        if state == "paused_by_youtube" {
            let until = engineDate(resumeAt).map { " until \(clock($0))" } ?? " for a few hours"
            return "YouTube is slowing this Mac down, so downloads are resting\(until). "
                + "They carry on by themselves after that."
        }
        if state == "paused" { return "Downloads are paused." }
        guard let next = engineDate(dailyResumeAt) else { return nil }
        return "That's \(dailyCap) downloads in 24 hours, your daily limit. The rest carry on "
            + "by themselves from \(clock(next))."
    }
}

/// How much of the daily download limit is used: the counter at the bottom of the
/// sidebar ("12/250"), and how close to the limit that is.
public struct DailyUse: Equatable, Sendable {
    public enum Level: Sendable {
        /// Plenty left (green), a good part used (orange), nearly all used (red).
        case safe, middling, nearlyOut
    }

    public let used: Int
    public let limit: Int
    /// Downloads asked for that haven't started: they'll count as they start.
    public let waiting: Int

    public init(used: Int, limit: Int, waiting: Int = 0) {
        self.used = used
        self.limit = limit
        self.waiting = waiting
    }

    public init(_ status: QueueStatus) {
        self.init(used: status.dailyCount, limit: status.dailyCap, waiting: status.queued)
    }

    public var text: String { "\(used)/\(limit)" }

    /// Green under three fifths of the limit, orange from there, red from 85%.
    public var level: Level {
        guard limit > 0 else { return .nearlyOut }
        let share = Double(used) / Double(limit)
        return share >= 0.85 ? .nearlyOut : share >= 0.6 ? .middling : .safe
    }

    /// What the counter says when the pointer rests on it.
    public var explained: String {
        var words = "\(used) of your \(limit) downloads from YouTube in the last 24 hours. Songs "
            + "and videos count alike; playing and searching don't count. Each one comes off "
            + "the count a day after it was downloaded."
        if used >= limit {
            words += " The limit is reached: downloads carry on by themselves as it frees up."
        }
        if waiting > 0 {
            words += " \(waiting) more \(waiting == 1 ? "is" : "are") waiting to download."
        }
        return words + " The limit is set in Settings."
    }
}

/// A time as the engine writes it ("2026-10-02T09:30:00.250000Z"), with or without
/// parts of a second.
public func engineDate(_ text: String?) -> Date? {
    guard let text, !text.isEmpty else { return nil }
    let parts = ISO8601DateFormatter()
    parts.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return parts.date(from: text) ?? ISO8601DateFormatter().date(from: text)
}

/// Which of the downloads on their way the Downloads page lists. A few are all shown;
/// of hundreds (Discover's Download Automatically), the one downloading, the next few
/// in line and the first few that didn't arrive, with the rest counted.
public struct DownloadsShown: Equatable, Sendable {
    public let rows: [PendingDownload]
    public let moreWaiting: Int
    public let moreEnded: Int

    /// More than this many, and the list is shortened.
    public static let most = 6

    public init(_ pending: [PendingDownload]) {
        guard pending.count > Self.most else {
            (rows, moreWaiting, moreEnded) = (pending, 0, 0)
            return
        }
        let running = pending.filter(\.isRunning)
        // The queue takes the oldest first: the lowest job number is next in line.
        let waiting = pending.filter { $0.isActive && !$0.isRunning }.sorted { $0.jobId < $1.jobId }
        let ended = pending.filter { !$0.isActive }
        rows = running + waiting.prefix(3) + ended.prefix(2)
        moreWaiting = max(waiting.count - 3, 0)
        moreEnded = max(ended.count - 2, 0)
    }
}

public struct FavouritesAnswer: Decodable, Sendable {
    public let favourites: [String]
}

public struct PlaylistsAnswer: Decodable, Sendable {
    public let playlists: [Playlist]
}

/// `library.status`: only the parts the app shows.
public struct LibraryStatus: Decodable, Sendable {
    public let itemsByState: [String: Int]
    public let tracks: Int

    public var waitingForReview: Int { itemsByState["review"] ?? 0 }
    public var notFound: Int { itemsByState["not_found"] ?? 0 }
}

public struct Album: Identifiable, Hashable, Sendable {
    public let id: String  // the album's folder
    public let title: String
    public let artist: String
    public let year: Int?
    public let tracks: [Track]

    /// The track whose cover stands for the album.
    public var coverTrack: Track? { tracks.first(where: \.hasCover) }
    public var duration: Double { tracks.reduce(0) { $0 + ($1.durationS ?? 0) } }

    public static func == (a: Album, b: Album) -> Bool { a.id == b.id }
    public func hash(into hasher: inout Hasher) { hasher.combine(id) }
}

public struct Artist: Identifiable, Hashable, Sendable {
    public let id: String  // the name, folded: "JAY Z" and "Jay Z" are one artist
    public let name: String
    public let albums: [Album]
    public var trackCount: Int { albums.reduce(0) { $0 + $1.tracks.count } }

    public static func == (a: Artist, b: Artist) -> Bool { a.id == b.id }
    public func hash(into hasher: inout Hasher) { hasher.combine(id) }
}

/// The library grouped for the screens. Built once per load, off the main thread.
public struct Library: Sendable {
    public let tracks: [Track]  // by title
    public let albums: [Album]  // by artist, then year, then title
    public let artists: [Artist]  // by name
    private let searchText: [String: String]  // track path → folded "title artist album"
    private let byID: [String: Track]
    /// The YouTube videos the library's songs came from: "is this one mine already?"
    public let videoIDs: Set<String>

    public static let empty = Library(tracks: [])

    public init(tracks: [Track]) {
        var byFolder: [String: [Track]] = [:]
        for track in tracks { byFolder[track.folder, default: []].append(track) }
        let albums = byFolder.map { folder, members -> Album in
            let sorted = members.sorted {
                ($0.disc ?? 1, $0.track ?? Int.max, fold($0.title))
                    < ($1.disc ?? 1, $1.track ?? Int.max, fold($1.title))
            }
            let first = sorted[0]
            let named = sorted.first { $0.album != nil }
            return Album(
                id: folder,
                title: named?.album ?? folder.split(separator: "/").last.map(String.init) ?? "Unsorted",
                artist: sorted.first { $0.albumArtist != nil }?.albumArtist ?? first.artistName,
                year: sorted.compactMap(\.year).first,
                tracks: sorted)
        }.sorted {
            (sortKey($0.artist), $0.year ?? 0, sortKey($0.title), $0.id)
                < (sortKey($1.artist), $1.year ?? 0, sortKey($1.title), $1.id)
        }
        var byArtist: [String: [Album]] = [:]
        for album in albums { byArtist[fold(album.artist), default: []].append(album) }
        self.albums = albums
        self.artists = byArtist.map { key, albums in
            Artist(id: key, name: albums[0].artist, albums: albums)
        }.sorted { (sortKey($0.name), $0.id) < (sortKey($1.name), $1.id) }
        self.tracks = tracks.sorted {
            (sortKey($0.title), sortKey($0.artistName), $0.path)
                < (sortKey($1.title), sortKey($1.artistName), $1.path)
        }
        var text: [String: String] = [:]
        text.reserveCapacity(tracks.count)
        for track in tracks {
            text[track.path] = fold("\(track.title) \(track.artistName) \(track.albumName)")
        }
        searchText = text
        var ids: [String: Track] = [:]
        for track in tracks {
            if let id = track.trackId { ids[id] = track }
        }
        byID = ids
        videoIDs = Set(tracks.compactMap(\.sourceId))
    }

    /// The songs with these ids, in that order; ids no longer in the library are left out.
    public func tracks(withIDs ids: [String]) -> [Track] {
        ids.compactMap { byID[$0] }
    }

    /// Newest first: what came into the library most recently.
    public func recentlyAdded() -> [Track] {
        tracks.filter { $0.acquired != nil }
            .sorted { ($0.acquired ?? "", $1.path) > ($1.acquired ?? "", $0.path) }
    }

    /// The most played first; songs never played are left out.
    public func mostPlayed(_ plays: [String: PlayCount], limit: Int = 200) -> [Track] {
        let counted = tracks.compactMap { track -> (Track, Int)? in
            guard let id = track.trackId, let count = plays[id]?.count, count > 0 else { return nil }
            return (track, count)
        }
        return Array(counted.sorted { ($0.1, $1.0.path) > ($1.1, $0.0.path) }.map(\.0).prefix(limit))
    }

    public var unconfirmed: [Track] { tracks.filter(\.isUnconfirmed) }

    /// `tracks`, in their order, narrowed to those matching every word of `query`.
    public func filter(_ tracks: [Track], _ query: String) -> [Track] {
        let words = fold(query).split(separator: " ")
        if words.isEmpty { return tracks }
        return tracks.filter { track in
            guard let text = searchText[track.path] else { return false }
            return words.allSatisfy { text.contains($0) }
        }
    }

    /// The songs matching every word of `query`, whatever the case or accents.
    public func search(_ query: String) -> [Track] {
        filter(tracks, query)
    }

    public func albums(matching query: String) -> [Album] {
        let found = Set(search(query).map(\.folder))
        return albums.filter { found.contains($0.id) }
    }

    public func artists(matching query: String) -> [Artist] {
        let words = fold(query).split(separator: " ")
        if words.isEmpty { return artists }
        return artists.filter { artist in words.allSatisfy { artist.id.contains($0) } }
    }

    public func album(of track: Track) -> Album? {
        albums.first { $0.id == track.folder }
    }
}

/// The sidebar's Library entries the owner has chosen, saved as text ("songs,artists").
/// Unknown names are dropped, and an empty or missing value gives the standard set.
public enum SidebarChoice {
    public static let all = [
        "songs", "albums", "artists", "videos", "favourites", "mostPlayed", "recentlyAdded",
        "unconfirmed",
    ]
    /// The entries the first version of the sidebar had: what a choice saved before
    /// "seen" was kept had been offered.
    static let original = [
        "songs", "albums", "artists", "favourites", "mostPlayed", "recentlyAdded", "unconfirmed",
    ]

    public static func read(_ saved: String?) -> [String] {
        guard let saved else { return all }
        var seen = Set<String>()
        let kept = saved.split(separator: ",").map(String.init)
            .filter { all.contains($0) && seen.insert($0).inserted }
        return kept.isEmpty ? all : kept
    }

    public static func write(_ entries: [String]) -> String { entries.joined(separator: ",") }

    /// The entries not shown, in the standard order: what "+" offers.
    public static func hidden(_ shown: [String]) -> [String] { all.filter { !shown.contains($0) } }

    /// Add an entry back in its standard place among those shown.
    public static func adding(_ entry: String, to shown: [String]) -> [String] {
        all.filter { shown.contains($0) || $0 == entry }
    }

    /// An entry this version brings (Videos) is shown once without being asked for:
    /// the owner's saved choice is from before it existed, so it can't have been
    /// removed on purpose. `seen` lists the entries already offered (nil: the original
    /// ones). Returns the choice and the "seen" list to save.
    public static func catchUp(saved: String?, seen: String?) -> (entries: String, seen: String) {
        let offered = seen.map { $0.split(separator: ",").map(String.init) } ?? original
        var shown = read(saved)
        for entry in all where !offered.contains(entry) { shown = adding(entry, to: shown) }
        return (write(shown), write(all))
    }
}

/// How a "find missing lyrics" run went, from its jobs' messages.
public func lyricsTally(_ jobs: [JobsAnswer.Job]) -> (timed: Int, plain: Int, none: Int) {
    var timed = 0, plain = 0
    for job in jobs where job.worked {
        let message = job.message ?? ""
        if message.hasPrefix("synced") { timed += 1 } else if message.hasPrefix("plain") { plain += 1 }
    }
    return (timed, plain, jobs.count - timed - plain)
}

/// Lower case, no accents: for comparing and searching.
public func fold(_ text: String) -> String {
    text.folding(options: [.diacriticInsensitive, .caseInsensitive, .widthInsensitive], locale: nil)
}

/// What a name sorts by: folded, without a leading "The".
public func sortKey(_ text: String) -> String {
    let folded = fold(text)
    return folded.hasPrefix("the ") ? String(folded.dropFirst(4)) : folded
}

/// "3:07", "1:02:45".
public func clockTime(_ seconds: Double?) -> String {
    guard let seconds, seconds.isFinite, seconds >= 0 else { return "–:––" }
    let whole = Int(seconds.rounded(.down))
    let (hours, minutes, secs) = (whole / 3600, whole / 60 % 60, whole % 60)
    return hours > 0
        ? String(format: "%d:%02d:%02d", hours, minutes, secs)
        : String(format: "%d:%02d", minutes, secs)
}
