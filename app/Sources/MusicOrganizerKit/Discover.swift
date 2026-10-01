import Foundation

/// Where Discover's picks start from (the engine's "Discover seed").
public enum DiscoverSeed: Hashable, Sendable {
    case library
    case mostPlayed
    case topArtist
    case playlist(String)  // its id
    case artist(String)
    case genre(String)

    /// The seed as `discover.suggest` takes it.
    public var params: [String: String] {
        switch self {
        case .library: ["kind": "library"]
        case .mostPlayed: ["kind": "most_played"]
        case .topArtist: ["kind": "top_artist"]
        case .playlist(let id): ["kind": "playlist", "playlist_id": id]
        case .artist(let name): ["kind": "artist", "name": name]
        case .genre(let name): ["kind": "genre", "name": name]
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
        return found
    }
}

/// `discover.suggest`: the picks, and anything to tell the owner about them.
public struct DiscoverAnswer: Decodable, Sendable {
    public let picks: [DiscoverPick]
    public let wanted: Int
    public let radios: Int
    public let note: String?
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

/// How long a batch of downloads will take, in words: "about 25 minutes", "about 2 hours".
public func roughTime(minutes: Int) -> String {
    if minutes < 1 { return "under a minute" }
    if minutes == 1 { return "about a minute" }
    if minutes < 90 { return "about \(minutes) minutes" }
    let hours = (Double(minutes) / 60).rounded()
    return "about \(Int(hours)) hours"
}
