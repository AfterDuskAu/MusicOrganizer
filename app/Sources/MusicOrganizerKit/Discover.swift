import Foundation

/// Where Discover's picks start from (the engine's "Discover seed").
public enum DiscoverSeed: Hashable, Sendable {
    case library
    case mostPlayed
    case topArtist
    case playlist(String)  // its id
    case artist(String)
    case genre(String)
    /// Whatever the owner typed when asked what music they'd like: the engine works
    /// out whether it's a genre or an artist.
    case typed(String)
    /// The owner's most played songs on Last.fm, once it's set up.
    case lastfm

    /// The seed as `discover.suggest` takes it.
    public var params: [String: String] {
        switch self {
        case .library: ["kind": "library"]
        case .mostPlayed: ["kind": "most_played"]
        case .topArtist: ["kind": "top_artist"]
        case .playlist(let id): ["kind": "playlist", "playlist_id": id]
        case .artist(let name): ["kind": "artist", "name": name]
        case .genre(let name): ["kind": "genre", "name": name]
        case .typed(let words): ["kind": "typed", "name": words]
        case .lastfm: ["kind": "lastfm"]
        }
    }

    /// Several artists typed in one box ("Linkin Park, Korn") are a seed each.
    public static func artists(_ typed: String) -> [DiscoverSeed] {
        var seen = Set<String>()
        return typed.split(whereSeparator: { $0 == "," || $0 == ";" || $0 == "\n" })
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty && seen.insert($0.lowercased()).inserted }
            .map(DiscoverSeed.artist)
    }
}

/// One starting point as the Find page's boxes hold it: which kind, and what was typed
/// or chosen for it.
public struct FindChoice: Identifiable, Equatable, Sendable {
    public enum Start: String, CaseIterable, Identifiable, Sendable {
        case artist, genre, playlist, mostPlayed, topArtist, library, lastfm

        public var id: String { rawValue }
        public var title: String {
            switch self {
            case .artist: "An artist, and bands like them"
            case .genre: "A genre"
            case .playlist: "One of my playlists"
            case .mostPlayed: "The songs I play most"
            case .topArtist: "The artist I play most"
            case .library: "My whole library"
            case .lastfm: "My most played on Last.fm"
            }
        }
    }

    /// The Find page offers this many starting points at once (the engine takes eight).
    public static let most = 4

    public let id: UUID
    public var start: Start
    public var artist: String
    public var genre: String
    public var playlistId: String

    public init(
        start: Start = .artist, artist: String = "", genre: String = "", playlistId: String = "",
        id: UUID = UUID()
    ) {
        self.id = id
        self.start = start
        self.artist = artist
        self.genre = genre
        self.playlistId = playlistId
    }

    /// The seeds this one choice makes: none while its box is empty, several for
    /// several artists. `playlists` are the ids of the playlists there are.
    public func seeds(playlists: Set<String>) -> [DiscoverSeed] {
        switch start {
        case .artist: return DiscoverSeed.artists(artist)
        case .genre:
            let name = genre.trimmingCharacters(in: .whitespaces)
            return name.isEmpty ? [] : [.genre(name)]
        case .playlist: return playlists.contains(playlistId) ? [.playlist(playlistId)] : []
        case .mostPlayed: return [.mostPlayed]
        case .topArtist: return [.topArtist]
        case .library: return [.library]
        case .lastfm: return [.lastfm]
        }
    }

    /// Every choice's seeds together, each once, eight at most. Empty if any choice is
    /// still unfilled: Find waits until every box has something in it.
    public static func seeds(of choices: [FindChoice], playlists: Set<String>) -> [DiscoverSeed] {
        var all: [DiscoverSeed] = []
        for choice in choices {
            let found = choice.seeds(playlists: playlists)
            if found.isEmpty { return [] }
            for seed in found where !all.contains(seed) { all.append(seed) }
        }
        return Array(all.prefix(8))
    }
}

/// One of Discover's picks: a YouTube Music song the owner doesn't have, and why it's here.
public struct DiscoverPick: Decodable, Identifiable, Hashable, Sendable {
    public let videoId: String
    public let title: String
    public let artists: [String]
    public let album: String?
    public let albumBrowseId: String?
    public let durationS: Double?
    public let isExplicit: Bool?
    public let videoType: String?
    public let year: String?
    public let thumbnail: String?
    /// One plain line: "On the radio for 3 of your songs", "Similar to Linkin Park".
    public let why: String
    /// How many of the radios asked had this song.
    public let hits: Int
    /// The genre it was found under, when that's known. It goes with the pick into a
    /// download, and becomes the downloaded song's genre.
    public let genre: String?

    public var id: String { videoId }
    public var artistName: String { artists.joined(separator: ", ") }

    /// The pick as a search result: playable straight from YouTube Music.
    public var result: SearchResult {
        SearchResult(
            videoId: videoId, title: title, artists: artists, album: album, durationS: durationS,
            isExplicit: isExplicit, thumbnail: thumbnail)
    }

    /// The pick as the engine gave it, to hand back with a download plan: the engine
    /// then doesn't look the song up on YouTube Music a second time.
    public var candidate: [String: Any] {
        var found: [String: Any] = ["video_id": videoId, "title": title, "artists": artists]
        if let album { found["album"] = album }
        if let albumBrowseId { found["album_browse_id"] = albumBrowseId }
        if let durationS { found["duration_s"] = Int(durationS) }
        if let isExplicit { found["is_explicit"] = isExplicit }
        if let videoType { found["video_type"] = videoType }
        if let year { found["year"] = year }
        if let thumbnail { found["thumbnail"] = thumbnail }
        if let genre { found["genre"] = genre }
        return found
    }
}

/// `discover.suggest`: the picks, and anything to tell the owner about them.
public struct DiscoverAnswer: Decodable, Sendable {
    /// A starting point as the engine took it: typed words come back as the genre or
    /// the artist they turned out to be.
    public struct Seed: Decodable, Equatable, Sendable {
        public let kind: String
        public let label: String

        public init(kind: String, label: String) {
            self.kind = kind
            self.label = label
        }
    }

    public let picks: [DiscoverPick]
    public let wanted: Int
    public let radios: Int
    public let note: String?
    public let seeds: [Seed]?
}

/// The words of the guided "What music would you like today?": what was found, and
/// what downloading it will take. The guide adds no logic of its own to Discover; it
/// asks the same questions the Find page has boxes for.
public enum Guided {
    public static let counts = [10, 25, 50, 100, 250]
    public static let most = 500

    /// "250" → 250. Nil for anything that isn't a number from 1 to 500.
    public static func count(from typed: String) -> Int? {
        guard let number = Int(typed.trimmingCharacters(in: .whitespaces)),
            (1...most).contains(number)
        else { return nil }
        return number
    }

    /// "237 hip hop songs", "50 songs by Linkin Park and artists like them".
    public static func what(_ count: Int, from seeds: [DiscoverAnswer.Seed]) -> String {
        let songs = count == 1 ? "song" : "songs"
        guard seeds.count == 1, let seed = seeds.first else { return "\(count) \(songs)" }
        switch seed.kind {
        case "genre": return "\(count) \(seed.label.lowercased()) \(songs)"
        case "artist": return "\(count) \(songs) by \(seed.label) and artists like them"
        case "playlist": return "\(count) \(songs) like the ones in \(seed.label)"
        case "most_played": return "\(count) \(songs) like the ones you play most"
        case "lastfm": return "\(count) \(songs) like your most played on Last.fm"
        default: return "\(count) \(songs) like the ones in your library"
        }
    }

    /// What the owner is told before a download starts.
    public static func downloadNote(minutes: Int, days: Int) -> String {
        var note = "They're weighted towards the artists you have most of, and skip anything "
            + "you already have. It takes \(roughTime(minutes: minutes)), paced so the service "
            + "doesn't refuse this Mac."
        if days > 1 { note += " Your daily limit spreads them over \(days) days." }
        return note
    }
}

extension Guided {
    /// What the owner is told once Download Automatically has queued its songs: how
    /// many, how long, and what the daily limit does to them. `allowance` is how many
    /// more downloads today's limit has room for (before these), and `limit` the limit.
    public static func startedNote(
        _ count: Int, from seeds: [DiscoverAnswer.Seed], wanted: Int, minutes: Int,
        allowance: Int, limit: Int
    ) -> String {
        let are = count == 1 ? "is" : "are"
        var note = "\(what(count, from: seeds).capitalizedFirst) \(are) on the way"
        note += count < wanted ? " (\(wanted) were asked for; that's all that was new). " : ". "
        let now = min(count, max(allowance, 0))
        if now == count {
            note += "It takes \(roughTime(minutes: minutes)), paced so the service doesn't refuse "
                + "this Mac."
        } else if now == 0 {
            note += "Your limit of \(limit) downloads in 24 hours is used up for now, so they "
                + "start by themselves as it frees up."
        } else {
            note += "Your limit of \(limit) downloads in 24 hours has room for \(now) now; the "
                + "other \(count - now) start by themselves as it frees up."
        }
        return note + " Leave Music Organizer open: the Mac stays awake while they download."
    }
}

extension String {
    /// The same words with a capital at the front; the rest is left as it is.
    var capitalizedFirst: String { prefix(1).uppercased() + dropFirst() }
}

/// Where else a song can be looked at: a search for it on another service. Plain links,
/// with no account and no key.
public enum ElsewhereLink: String, CaseIterable, Identifiable, Sendable {
    case spotify = "Spotify"
    case appleMusic = "Apple Music"
    case soundCloud = "SoundCloud"

    public var id: String { rawValue }

    public func url(title: String, artist: String) -> URL? {
        let words = "\(artist) \(title)".trimmingCharacters(in: .whitespaces)
        guard !words.isEmpty else { return nil }
        var parts = URLComponents()
        parts.scheme = "https"
        switch self {
        case .spotify:
            parts.host = "open.spotify.com"
            parts.path = "/search/\(words)"
        case .appleMusic:
            parts.host = "music.apple.com"
            parts.path = "/search"
            parts.queryItems = [URLQueryItem(name: "term", value: words)]
        case .soundCloud:
            parts.host = "soundcloud.com"
            parts.path = "/search"
            parts.queryItems = [URLQueryItem(name: "q", value: words)]
        }
        return parts.url
    }
}

/// How long a batch of downloads will take, in words: "about 25 minutes", "about 2½ hours".
public func roughTime(minutes: Int) -> String {
    if minutes < 1 { return "under a minute" }
    if minutes == 1 { return "about a minute" }
    if minutes < 90 { return "about \(minutes) minutes" }
    let halves = Int((Double(minutes) / 30).rounded())  // to the nearest half hour
    return "about \(halves / 2)\(halves % 2 == 1 ? "½" : "") hours"
}

/// Songs under one genre, as Discover → Downloads lists them.
public struct GenreGroup: Identifiable, Equatable, Sendable {
    /// The genre as it's shown ("Hip Hop"); empty for songs that have none yet.
    public let name: String
    public let tracks: [Track]

    public var id: String { name }

    public init(name: String, tracks: [Track]) {
        self.name = name
        self.tracks = tracks
    }
}

/// Discover → Downloads, as the owner drew it on 2026-10-03: a box of videos and a box of
/// songs, each newest first; which box comes first is the owner's choice.
public enum DownloadGroups {
    public static func byKind(_ tracks: [Track], videosFirst: Bool) -> [GenreGroup] {
        let videos = GenreGroup(name: "Videos", tracks: tracks.filter(\.isVideo))
        let songs = GenreGroup(name: "Songs", tracks: tracks.filter { !$0.isVideo })
        return (videosFirst ? [videos, songs] : [songs, videos]).filter { !$0.tracks.isEmpty }
    }
}

public enum Genres {
    /// What makes two genre tags the same group: the first genre named ("Hip-Hop/Rap"
    /// is filed under hip hop), whatever its capitals, hyphens and spacing.
    public static func key(_ tag: String?) -> String {
        let first = firstNamed(tag)
        let spaced = first.lowercased().map { "-_".contains($0) ? " " : $0 }
        return String(spaced).split(separator: " ").joined(separator: " ")
            .replacingOccurrences(of: " & ", with: "&")
    }

    /// The first genre a tag names, as it's written: "Hip-Hop/Rap" → "Hip-Hop".
    public static func firstNamed(_ tag: String?) -> String {
        let first = (tag ?? "").split(whereSeparator: { "/,;|".contains($0) }).first ?? ""
        return first.trimmingCharacters(in: .whitespaces)
    }

    /// Songs grouped by genre, in the order given within each group (newest first, for
    /// downloads). The group with the newest song comes first; songs with no genre come
    /// last. A group is named the way most of its songs spell it.
    public static func groups(_ tracks: [Track]) -> [GenreGroup] {
        var order: [String] = []
        var members: [String: [Track]] = [:]
        var spellings: [String: [String: Int]] = [:]
        for track in tracks {
            let key = key(track.genre)
            if members[key] == nil { order.append(key) }
            members[key, default: []].append(track)
            if !key.isEmpty { spellings[key, default: [:]][firstNamed(track.genre), default: 0] += 1 }
        }
        func newest(_ key: String) -> String { members[key]?.compactMap(\.acquired).max() ?? "" }
        let sorted = order.enumerated().sorted { a, b in
            if a.element.isEmpty != b.element.isEmpty { return b.element.isEmpty }
            let (left, right) = (newest(a.element), newest(b.element))
            return left != right ? left > right : a.offset < b.offset
        }
        return sorted.map { _, key in
            let name = spellings[key]?.max { a, b in
                a.value != b.value ? a.value < b.value : a.key > b.key
            }?.key
            return GenreGroup(name: name ?? "", tracks: members[key] ?? [])
        }
    }
}

/// What Music Finder shows while nothing is being searched for (the owner's drawing,
/// 2026-10-08): the four ways of finding music without typing a name.
public enum MusicFinderTab: String, CaseIterable, Identifiable, Sendable {
    case whatsNew, find, playlists, remixes

    public static let key = "musicFinderTab"
    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .whatsNew: "What's New"
        case .find: "Find"
        case .playlists: "Playlists"
        case .remixes: "Covers & Remixes"
        }
    }

    /// The tab saved under this name; What's New for a name that isn't one. The page
    /// before this one remembered only "Find or not" (`musicExploreFind`).
    public init(saved: String?, showedFind: Bool = false) {
        self = saved.flatMap(MusicFinderTab.init(rawValue:)) ?? (showedFind ? .find : .whatsNew)
    }
}

/// A playlist on the music service found around what the owner plays
/// (`discover.playlists`). Opening one reads it on the Import Playlists page.
public struct FoundPlaylist: Decodable, Identifiable, Hashable, Sendable {
    public let playlistId: String
    public let title: String
    public let author: String?
    public let thumbnail: String?
    /// One plain line: "For “Song” by Artist".
    public let why: String

    public var id: String { playlistId }
    /// The playlist's link, as Import Playlists reads one.
    public var link: String { "https://music.youtube.com/playlist?list=\(playlistId)" }

    public init(playlistId: String, title: String, author: String? = nil, thumbnail: String? = nil, why: String = "") {
        self.playlistId = playlistId
        self.title = title
        self.author = author
        self.thumbnail = thumbnail
        self.why = why
    }
}

/// The playlists Music Finder has shown lately, so the page doesn't keep showing the
/// same ones (the owner, 2026-10-08). Kept by the app on this Mac. The oldest are
/// forgotten first, and can then come round again.
public struct PlaylistsSeen: Codable, Equatable, Sendable {
    public static let key = "playlistsSeen"
    public static let most = 120
    public private(set) var ids: [String] = []

    public init() {}

    public mutating func add(_ shown: [String]) {
        ids.removeAll(where: shown.contains)
        ids += shown
        if ids.count > Self.most { ids.removeFirst(ids.count - Self.most) }
    }

    public mutating func forget() { ids = [] }

    public static func load(from defaults: UserDefaults = .standard) -> PlaylistsSeen {
        var seen = PlaylistsSeen()
        seen.ids = defaults.stringArray(forKey: key) ?? []
        return seen
    }

    public func save(to defaults: UserDefaults = .standard) { defaults.set(ids, forKey: Self.key) }
}

extension Listening {
    /// Changes whenever a song has been played through: the song played last, and when.
    /// The Playlists page looks again when this does.
    public var playedStamp: String {
        let last = plays.compactMap { id, play in play.lastPlayed.map { (when: $0, id: id) } }
            .max { $0.when < $1.when }
        return last.map { "\($0.id) \($0.when)" } ?? ""
    }
}

public struct FoundPlaylistsAnswer: Decodable, Sendable {
    public let playlists: [FoundPlaylist]
    public let note: String?
}

/// The rows of the sidebar that aren't Music's own entries (those are `SidebarChoice`),
/// and which of them the owner has taken out (Customise Sidebar, 2026-10-08).
public enum SidebarRows {
    /// The rows that can be hidden, by the name each is saved under, in the sidebar's
    /// order, with the group each is in.
    public static let all: [(key: String, group: String)] = [
        ("channels", "Videos"), ("movies", "Videos"),
        ("home", "Media Discovery"), ("youtube", "Media Discovery"),
        ("videoFinder", "Media Discovery"), ("movieFinder", "Media Discovery"),
        ("seriesFinder", "Media Discovery"), ("animeFinder", "Media Discovery"),
        ("adultFinder", "Media Discovery"), ("downloads", "Media Discovery"),
        ("import", "Playlists"),
    ]
    public static let key = "sidebarHidden"

    public static func hidden(_ saved: String?) -> Set<String> {
        let known = Set(all.map(\.key))
        return Set((saved ?? "").split(separator: ",").map(String.init)).intersection(known)
    }

    /// The saved form with one row shown or hidden.
    public static func write(_ saved: String?, _ row: String, shown: Bool) -> String {
        var now = hidden(saved)
        if shown { now.remove(row) } else { now.insert(row) }
        return all.map(\.key).filter(now.contains).joined(separator: ",")
    }
}
