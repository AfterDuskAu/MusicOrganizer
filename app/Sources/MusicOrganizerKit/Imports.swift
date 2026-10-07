import Foundation

/// A YouTube Music track as the engine gives it (its "Candidate"): what an imported
/// song turned out to be, playable straight away and ready to hand back for a download.
public struct ImportCandidate: Decodable, Hashable, Sendable {
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

    public init(
        videoId: String, title: String, artists: [String] = [], album: String? = nil,
        albumBrowseId: String? = nil, durationS: Double? = nil, isExplicit: Bool? = nil,
        videoType: String? = nil, year: String? = nil, thumbnail: String? = nil
    ) {
        self.videoId = videoId
        self.title = title
        self.artists = artists
        self.album = album
        self.albumBrowseId = albumBrowseId
        self.durationS = durationS
        self.isExplicit = isExplicit
        self.videoType = videoType
        self.year = year
        self.thumbnail = thumbnail
    }

    public var artistName: String { artists.joined(separator: ", ") }

    /// The track as a search result: playable straight from YouTube Music.
    public var result: SearchResult {
        SearchResult(
            videoId: videoId, title: title, artists: artists, album: album, durationS: durationS,
            isExplicit: isExplicit, thumbnail: thumbnail)
    }

    /// The track as the engine gave it, to hand back.
    public var params: [String: Any] {
        var found: [String: Any] = ["video_id": videoId, "title": title, "artists": artists]
        if let album { found["album"] = album }
        if let albumBrowseId { found["album_browse_id"] = albumBrowseId }
        if let durationS { found["duration_s"] = Int(durationS) }
        if let isExplicit { found["is_explicit"] = isExplicit }
        if let videoType { found["video_type"] = videoType }
        if let year { found["year"] = year }
        if let thumbnail { found["thumbnail"] = thumbnail }
        return found
    }
}

/// A song as the playlist it came from names it (the engine's "ImportTrack").
public struct ImportTrack: Decodable, Hashable, Sendable {
    public let title: String
    public let artists: [String]
    public let album: String?
    public let durationS: Double?
    public let isExplicit: Bool?
    /// The YouTube track it already is, for a song from a YouTube playlist.
    public let candidate: ImportCandidate?

    public init(
        title: String, artists: [String] = [], album: String? = nil, durationS: Double? = nil,
        isExplicit: Bool? = nil, candidate: ImportCandidate? = nil
    ) {
        self.title = title
        self.artists = artists
        self.album = album
        self.durationS = durationS
        self.isExplicit = isExplicit
        self.candidate = candidate
    }

    public var artistName: String { artists.joined(separator: ", ") }

    /// The song as `import.find` takes it.
    public var params: [String: Any] {
        var found: [String: Any] = ["title": title, "artists": artists]
        if let album { found["album"] = album }
        if let durationS { found["duration_s"] = Int(durationS) }
        if let isExplicit { found["is_explicit"] = isExplicit }
        if let candidate { found["candidate"] = candidate.params }
        return found
    }
}

/// `import.playlist`: a playlist read from where it lives.
public struct ImportedPlaylist: Decodable, Sendable {
    public let source: String
    public let name: String
    public let tracks: [ImportTrack]
    /// The playlist is longer than was read (Spotify: more than 3,000 songs).
    public let more: Bool?
    /// A file that holds several playlists names them all; `tracks` are one's.
    public let playlists: [String]?
}

/// Which playlist to read, and from where (`import.playlist`'s params).
public enum ImportRequest: Equatable, Sendable {
    case youtube(link: String)
    case spotify(playlistId: String)
    /// A public Deezer playlist or album, by its link.
    case deezer(link: String)
    /// One of the owner's lists on Last.fm (`LastfmList`).
    case lastfm(list: String)
    /// A playlist saved as a file (an export from Amazon Music, say). `playlist`
    /// chooses one of the playlists in a file that holds several.
    case file(path: String, playlist: String?)

    public var params: [String: String] {
        switch self {
        case .youtube(let link): return ["source": "youtube", "link": link]
        case .spotify(let id): return ["source": "spotify", "playlist_id": id]
        case .deezer(let link): return ["source": "deezer", "link": link]
        case .lastfm(let list): return ["source": "lastfm", "list": list]
        case .file(let path, let playlist):
            var asked = ["source": "file", "path": path]
            if let playlist { asked["playlist"] = playlist }
            return asked
        }
    }
}

/// The lists Last.fm keeps for a listener, as Import Playlists offers them (the
/// engine's "Last.fm list").
public enum LastfmList: String, CaseIterable, Identifiable, Sendable {
    case loved
    case top7day = "top_7day"
    case top1month = "top_1month"
    case top3month = "top_3month"
    case top6month = "top_6month"
    case top12month = "top_12month"
    case topOverall = "top_overall"

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .loved: "Loved Tracks"
        case .top7day: "Most played: last 7 days"
        case .top1month: "Most played: last month"
        case .top3month: "Most played: last 3 months"
        case .top6month: "Most played: last 6 months"
        case .top12month: "Most played: last year"
        case .topOverall: "Most played: all time"
        }
    }
}

/// One of the signed-in Spotify account's playlists (`import.playlists`).
public struct SpotifyPlaylist: Decodable, Identifiable, Hashable, Sendable {
    public let id: String
    public let name: String
    public let owner: String?
    public let total: Int?
    /// False for someone else's playlist that's only followed: Spotify lists it, but
    /// doesn't give its songs.
    public let readable: Bool

    public init(id: String, name: String, owner: String? = nil, total: Int? = nil, readable: Bool = true) {
        self.id = id
        self.name = name
        self.owner = owner
        self.total = total
        self.readable = readable
    }

    /// "Road Trip (80 songs)", and why it can't be read if it can't.
    public var label: String {
        var words = name
        if let total { words += " (\(total.formatted()) \(total == 1 ? "song" : "songs"))" }
        return readable ? words : words + ": someone else's, so Spotify won't give its songs"
    }
}

public struct SpotifyPlaylistsAnswer: Decodable, Sendable {
    public let playlists: [SpotifyPlaylist]
}

/// `account.status`: which services are set up and signed in to. Never a token.
public struct AccountStatus: Decodable, Equatable, Sendable {
    public struct Spotify: Decodable, Equatable, Sendable {
        /// The owner's own Spotify app's Client ID, once they've given it.
        public let clientId: String?
        public let signedIn: Bool
        public let name: String?
        /// What that app must have registered as its Redirect URI.
        public let redirectUri: String

        public init(clientId: String? = nil, signedIn: Bool = false, name: String? = nil, redirectUri: String = "") {
            self.clientId = clientId
            self.signedIn = signedIn
            self.name = name
            self.redirectUri = redirectUri
        }
    }

    /// Last.fm, which has no sign-in: a username and the owner's own API key. The key
    /// itself is never given out, only whether one is saved.
    public struct Lastfm: Decodable, Equatable, Sendable {
        public let user: String?
        public let hasKey: Bool
        public let connected: Bool

        public init(user: String? = nil, hasKey: Bool = false, connected: Bool = false) {
            self.user = user
            self.hasKey = hasKey
            self.connected = connected
        }
    }

    public let spotify: Spotify
    /// Nil from an engine older than Last.fm.
    public let lastfm: Lastfm?

    public init(spotify: Spotify = Spotify(), lastfm: Lastfm? = nil) {
        self.spotify = spotify
        self.lastfm = lastfm
    }
}

/// `account.sign_in`: the address to open in the browser.
public struct SignInAnswer: Decodable, Sendable {
    public let authorizeUrl: String

    /// The address, if it really is Spotify's own sign-in page over https: nothing else
    /// is ever opened for a sign-in.
    public var spotifyPage: URL? {
        guard let url = URL(string: authorizeUrl), url.scheme == "https",
            url.host == "accounts.spotify.com"
        else { return nil }
        return url
    }
}

/// What one imported song is to the owner (`import.find`).
public struct ImportFound: Decodable, Hashable, Sendable {
    public enum State: String, Sendable {
        case owned, queued, found, unsure
        case notFound = "not_found"
    }

    public let state: String
    public let candidate: ImportCandidate?
    public let trackId: String?
    public let why: String?

    public init(
        state: State, candidate: ImportCandidate? = nil, trackId: String? = nil, why: String? = nil
    ) {
        self.state = state.rawValue
        self.candidate = candidate
        self.trackId = trackId
        self.why = why
    }

    /// The state as the app knows it; one it doesn't know reads as not found.
    public var kind: State { State(rawValue: state) ?? .notFound }
}

public struct ImportFindAnswer: Decodable, Sendable {
    public let found: [ImportFound]
}

/// One line of an import: the song as its playlist names it, and what was found for it
/// (nil until it has been looked for).
public struct ImportRow: Identifiable, Hashable, Sendable {
    public let id: Int  // its place in the playlist
    public let track: ImportTrack
    public var found: ImportFound?

    public init(id: Int, track: ImportTrack, found: ImportFound? = nil) {
        self.id = id
        self.track = track
        self.found = found
    }
}

/// The sums and the words of an import: what's the owner's already, what will download.
public enum Imports {
    /// How many songs `import.find` is asked about at a time.
    public static let atOnce = 50

    /// Whether this could be a Last.fm API key: 32 hexadecimal digits.
    public static func looksLikeLastfmKey(_ text: String) -> Bool {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.count == 32 && trimmed.allSatisfy { $0.isASCII && $0.isHexDigit }
    }

    /// Whether this could be a Spotify Client ID: 32 letters and digits.
    public static func looksLikeSpotifyClientId(_ text: String) -> Bool {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.count == 32 && trimmed.allSatisfy { $0.isASCII && ($0.isLetter || $0.isNumber) }
    }

    public struct Counts: Equatable, Sendable {
        public var owned = 0
        public var queued = 0
        public var found = 0
        public var unsure = 0
        public var notFound = 0
        public var waiting = 0  // not looked for yet
    }

    public static func counts(_ rows: [ImportRow]) -> Counts {
        var counts = Counts()
        for row in rows {
            switch row.found?.kind {
            case .owned: counts.owned += 1
            case .queued: counts.queued += 1
            case .found: counts.found += 1
            case .unsure: counts.unsure += 1
            case .notFound: counts.notFound += 1
            case nil: counts.waiting += 1
            }
        }
        return counts
    }

    /// The tracks to download: every certain one, and the unsure ones the owner
    /// ticked. Each YouTube track once, in the playlist's order.
    public static func toDownload(_ rows: [ImportRow], ticked: Set<Int>) -> [ImportCandidate] {
        var seen = Set<String>()
        return rows.compactMap { row -> ImportCandidate? in
            guard let found = row.found, let candidate = found.candidate else { return nil }
            guard found.kind == .found || (found.kind == .unsure && ticked.contains(row.id))
            else { return nil }
            return seen.insert(candidate.videoId).inserted ? candidate : nil
        }
    }

    /// The owner's own copies of the playlist's songs, each once, in the playlist's
    /// order: they go into the new playlist straight away.
    public static func ownedIds(_ rows: [ImportRow]) -> [String] {
        var seen = Set<String>()
        return rows.compactMap { row -> String? in
            guard row.found?.kind == .owned, let id = row.found?.trackId else { return nil }
            return seen.insert(id).inserted ? id : nil
        }
    }

    /// "212 songs: 20 in your library, 180 to download, 8 not sure, 4 not found".
    public static func summary(_ counts: Counts, ticked: Int = 0) -> String {
        let total =
            counts.owned + counts.queued + counts.found + counts.unsure + counts.notFound
            + counts.waiting
        var parts: [String] = []
        if counts.owned > 0 { parts.append("\(counts.owned) in your library") }
        if counts.found + ticked > 0 { parts.append("\(counts.found + ticked) to download") }
        if counts.queued > 0 { parts.append("\(counts.queued) already on the way") }
        if counts.unsure - ticked > 0 { parts.append("\(counts.unsure - ticked) not sure") }
        if counts.notFound > 0 { parts.append("\(counts.notFound) not found") }
        let songs = "\(total) \(total == 1 ? "song" : "songs")"
        return parts.isEmpty ? songs : songs + ": " + parts.joined(separator: ", ")
    }

    /// What the owner is told once an import's downloads are queued. `allowance` is how
    /// many more downloads the daily limit has room for (before these).
    public static func startedNote(
        _ count: Int, playlist: String, owned: Int, minutes: Int, allowance: Int, limit: Int
    ) -> String {
        var note = "\(count) \(count == 1 ? "song is" : "songs are") on the way, and "
            + "\(count == 1 ? "joins" : "join") your playlist \"\(playlist)\" as "
            + "\(count == 1 ? "it arrives" : "they arrive")"
        note += owned > 0
            ? " (\(owned) you already had \(owned == 1 ? "is" : "are") in it now). " : ". "
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
