import Foundation

/// A song on an artist's page: a YouTube Music track as the engine gives it (its
/// "Candidate"), and whether the owner already has the song.
public struct ArtistSong: Decodable, Identifiable, Hashable, Sendable {
    public let candidate: ImportCandidate
    /// The owner has this song: by its YouTube id, or by title, version and artist.
    public let owned: Bool

    private enum Keys: String, CodingKey { case owned }

    public init(candidate: ImportCandidate, owned: Bool = false) {
        self.candidate = candidate
        self.owned = owned
    }

    public init(from decoder: Decoder) throws {
        candidate = try ImportCandidate(from: decoder)
        owned = try decoder.container(keyedBy: Keys.self).decodeIfPresent(Bool.self, forKey: .owned)
            ?? false
    }

    public var id: String { candidate.videoId }
    /// The song as a search result: playable straight from YouTube Music.
    public var result: SearchResult { candidate.result }
}

/// An album, EP or single on an artist's page.
public struct ArtistRelease: Decodable, Identifiable, Hashable, Sendable {
    public let browseId: String
    public let title: String
    public let year: String?
    /// "Album", "EP" or "Single", when YouTube Music says which.
    public let kind: String?
    public let isExplicit: Bool?
    public let thumbnail: String?

    public init(
        browseId: String, title: String, year: String? = nil, kind: String? = nil,
        isExplicit: Bool? = nil, thumbnail: String? = nil
    ) {
        self.browseId = browseId
        self.title = title
        self.year = year
        self.kind = kind
        self.isExplicit = isExplicit
        self.thumbnail = thumbnail
    }

    public var id: String { browseId }

    /// "Single · 2025", "2024", or nothing.
    public var caption: String {
        [kind, year].compactMap { $0 }.joined(separator: " · ")
    }
}

/// An artist "fans might also like", as an artist's page lists them.
public struct RelatedArtist: Decodable, Identifiable, Hashable, Sendable {
    public let artistId: String
    public let name: String
    /// Their monthly audience on YouTube Music, as it writes it ("28.3M").
    public let monthlyAudience: String?
    public let thumbnail: String?

    public init(
        artistId: String, name: String, monthlyAudience: String? = nil, thumbnail: String? = nil
    ) {
        self.artistId = artistId
        self.name = name
        self.monthlyAudience = monthlyAudience
        self.thumbnail = thumbnail
    }

    public var id: String { artistId }
}

/// `artist.info`: an artist's page on YouTube Music. When `found` is false, YouTube
/// Music has no artist for the name, and only `name` is there.
public struct ArtistInfo: Decodable, Equatable, Sendable {
    public let found: Bool
    public let name: String
    public let artistId: String?
    public let description: String?
    public let subscribers: String?
    public let monthlyAudience: String?
    public let views: String?
    public let thumbnail: String?
    public let songs: [ArtistSong]?
    /// The playlist of all their songs, for `artist.songs`.
    public let songsPlaylistId: String?
    public let albums: [ArtistRelease]?
    public let singles: [ArtistRelease]?
    public let related: [RelatedArtist]?
    /// How many songs credited to them the owner has.
    public let ownedSongs: Int?

    /// "25.4M subscribers · 170M monthly audience · 22,614,775,651 views": whichever
    /// of the three YouTube Music gave.
    public var numbers: String {
        var parts: [String] = []
        if let subscribers { parts.append("\(subscribers) subscribers") }
        if let monthlyAudience { parts.append("\(monthlyAudience) monthly audience") }
        if let views { parts.append(views.hasSuffix("views") ? views : "\(views) views") }
        return parts.joined(separator: " · ")
    }

    /// "You have 31 of their songs", or nothing when the owner has none.
    public var ownedWords: String? {
        guard let ownedSongs, ownedSongs > 0 else { return nil }
        return ownedSongs == 1 ? "You have 1 of their songs" : "You have \(ownedSongs) of their songs"
    }
}

/// `artist.songs`: every song of an artist's (up to three hundred).
public struct ArtistSongsAnswer: Decodable, Sendable {
    public let songs: [ArtistSong]
    public let more: Bool
}

/// `artist.album`: one album's, EP's or single's songs, in its order.
public struct ArtistAlbum: Decodable, Equatable, Identifiable, Sendable {
    public let browseId: String
    public let title: String
    public let year: String?
    public let artists: [String]
    public let thumbnail: String?
    public let songs: [ArtistSong]

    public var id: String { browseId }
}

/// What the Artist page works out from its songs.
public enum ArtistSongs {
    /// The songs the owner doesn't have and that aren't in `skip` (in the library by
    /// their id, or on their way): what "Download Missing" would fetch, each once.
    public static func missing(_ songs: [ArtistSong], skip: Set<String> = []) -> [ImportCandidate] {
        var seen = Set<String>()
        return songs.compactMap { song -> ImportCandidate? in
            guard !song.owned, !skip.contains(song.id) else { return nil }
            return seen.insert(song.id).inserted ? song.candidate : nil
        }
    }

    /// Which artist "Artist Info" means for a song credited to several: the names as
    /// credited, each once, the first three. A song from the library has one credit
    /// line ("A, B" or "A feat. B"), which is looked up as it stands: the engine takes
    /// the artist of exactly that name, or else YouTube Music's best guess.
    public static func names(_ artists: [String]) -> [String] {
        var seen = Set<String>()
        return artists.map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty && seen.insert($0.lowercased()).inserted }
            .prefix(3).map { $0 }
    }
}
