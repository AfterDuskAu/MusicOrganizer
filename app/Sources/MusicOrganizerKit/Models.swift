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
}

/// One result of a YouTube Music search (the engine's Candidate).
public struct SearchResult: Decodable, Identifiable, Hashable, Sendable {
    public let videoId: String
    public let title: String
    public let artists: [String]
    public let album: String?
    public let durationS: Double?
    public let isExplicit: Bool?
    public let thumbnail: String?

    public init(
        videoId: String, title: String, artists: [String], album: String? = nil,
        durationS: Double? = nil, isExplicit: Bool? = nil, thumbnail: String? = nil
    ) {
        self.videoId = videoId
        self.title = title
        self.artists = artists
        self.album = album
        self.durationS = durationS
        self.isExplicit = isExplicit
        self.thumbnail = thumbnail
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
