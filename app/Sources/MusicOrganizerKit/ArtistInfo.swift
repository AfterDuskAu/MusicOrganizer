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

/// `artist.search`: the artists YouTube Music finds for what was typed.
public struct ArtistSearchAnswer: Decodable, Sendable {
    public let artists: [RelatedArtist]
}

/// An artist on the Discover side of the Artists page: one found by a search, or one
/// of the artists behind What's New's picks.
public struct ListedArtist: Identifiable, Hashable, Sendable {
    public let name: String
    /// Known for an artist a search found; a pick's artist is looked up by name.
    public let artistId: String?
    public let thumbnail: String?
    /// How many of What's New's picks are theirs (0 for a search result).
    public let picks: Int

    public init(name: String, artistId: String? = nil, thumbnail: String? = nil, picks: Int = 0) {
        self.name = name
        self.artistId = artistId
        self.thumbnail = thumbnail
        self.picks = picks
    }

    public var id: String { artistId ?? "name:" + ArtistNames.key(name) }

    /// "3 songs in What's New", or nothing for a search result.
    public var caption: String? {
        picks == 0 ? nil : picks == 1 ? "1 song in What's New" : "\(picks) songs in What's New"
    }
}

/// Names of artists, compared.
public enum ArtistNames {
    /// What two spellings of one artist share: letters and digits only, no accents or
    /// capitals, no leading "The". "JAY-Z", "Jay Z" and "jay z" are one; so are "The
    /// Beatles" and "Beatles".
    public static func key(_ name: String) -> String {
        var folded = fold(name)
        if folded.hasPrefix("the ") { folded = String(folded.dropFirst(4)) }
        return String(folded.unicodeScalars.filter { CharacterSet.alphanumerics.contains($0) })
    }

    /// The artists behind a page of picks, the one with the most picks first (then in
    /// the order they first appear), each with a picture: the cover of their first pick.
    /// A pick credited to several artists counts for its first.
    public static func behind(_ picks: [DiscoverPick]) -> [ListedArtist] {
        var order: [String] = []
        var found: [String: (name: String, thumbnail: String?, count: Int)] = [:]
        for pick in picks {
            guard let name = pick.artists.first, !key(name).isEmpty else { continue }
            let id = key(name)
            if let known = found[id] {
                found[id] = (known.name, known.thumbnail ?? pick.thumbnail, known.count + 1)
            } else {
                order.append(id)
                found[id] = (name, pick.thumbnail, 1)
            }
        }
        return order.enumerated()
            .sorted { (found[$0.element]!.count, -$0.offset) > (found[$1.element]!.count, -$1.offset) }
            .map { _, id in
                let artist = found[id]!
                return ListedArtist(name: artist.name, thumbnail: artist.thumbnail, picks: artist.count)
            }
    }

    /// The owner's own artist of this name, however it's spelled, if they have one.
    public static func mine(_ name: String, in artists: [Artist]) -> Artist? {
        let wanted = key(name)
        guard !wanted.isEmpty else { return nil }
        return artists.first { key($0.name) == wanted }
    }
}

/// What the owner's own listening says about one of their artists: the "fun
/// information" on the Mine side of the Artists page. Plays are the ones this app has
/// counted (a song played to its end), so they start from when the app was first used.
public struct ArtistStats: Equatable, Sendable {
    public struct Song: Equatable, Sendable {
        public let title: String
        public let plays: Int
    }

    public let songs: Int
    public let albums: Int
    /// Times any of their songs was played to its end.
    public let plays: Int
    /// The time those plays add up to.
    public let secondsListened: Double
    public let mostPlayed: Song?
    public let favourites: Int
    public let lastPlayed: Date?
    public let firstAdded: Date?

    public init(artist: Artist, plays counted: [String: PlayCount], favourites: Set<String>) {
        let tracks = artist.albums.flatMap(\.tracks)
        var total = 0
        var seconds = 0.0
        var most: Song?
        var last: Date?
        var first: Date?
        var loved = 0
        for track in tracks {
            if let added = engineDate(track.acquired), first.map({ added < $0 }) ?? true {
                first = added
            }
            guard let id = track.trackId else { continue }
            if favourites.contains(id) { loved += 1 }
            guard let played = counted[id], played.count > 0 else { continue }
            total += played.count
            seconds += Double(played.count) * (track.durationS ?? 0)
            if played.count > (most?.plays ?? 0) { most = Song(title: track.title, plays: played.count) }
            if let when = engineDate(played.lastPlayed), last.map({ when > $0 }) ?? true {
                last = when
            }
        }
        (songs, albums) = (tracks.count, artist.albums.count)
        (self.plays, secondsListened, mostPlayed) = (total, seconds, most)
        (self.favourites, lastPlayed, firstAdded) = (loved, last, first)
    }

    /// "2 hr 31 min", "45 min", "under a minute", or nil when nothing's been played.
    public var listened: String? {
        guard plays > 0 else { return nil }
        let minutes = Int((secondsListened / 60).rounded())
        if minutes < 1 { return "under a minute" }
        if minutes < 60 { return "\(minutes) min" }
        let left = minutes % 60
        return left == 0 ? "\(minutes / 60) hr" : "\(minutes / 60) hr \(left) min"
    }

    /// Where this artist comes among the owner's artists by plays: 1 is the most
    /// played. Nil when none of their songs has been played. Artists played as often
    /// share a place.
    public static func rank(
        of artist: Artist, among artists: [Artist], plays counted: [String: PlayCount]
    ) -> Int? {
        func total(_ one: Artist) -> Int {
            one.albums.flatMap(\.tracks).reduce(0) { sum, track in
                sum + (track.trackId.flatMap { counted[$0]?.count } ?? 0)
            }
        }
        let mine = total(artist)
        guard mine > 0 else { return nil }
        return 1 + artists.filter { $0.id != artist.id && total($0) > mine }.count
    }
}
