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
        default: return "\(count) \(songs) like the ones in your library"
        }
    }

    /// What the owner is told before a download starts.
    public static func downloadNote(minutes: Int, days: Int) -> String {
        var note = "They're weighted towards the artists you have most of, and skip anything "
            + "you already have. It takes \(roughTime(minutes: minutes)), paced so YouTube "
            + "doesn't refuse this Mac."
        if days > 1 { note += " Your daily limit spreads them over \(days) days." }
        return note
    }
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
