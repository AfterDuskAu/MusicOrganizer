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
    public let match: String?
    public let format: String?
    public let bitrateKbps: Int?
    public let cover: String?
    public let embeddedCover: Bool
    public let lyrics: Lyrics

    public var id: String { path }
    public var artistName: String { artist ?? albumArtist ?? "Unknown Artist" }
    public var albumName: String { album ?? "" }
    /// The folder the file is in: one folder is one album.
    public var folder: String {
        path.lastIndex(of: "/").map { String(path[..<$0]) } ?? ""
    }
    public var hasCover: Bool { cover != nil || embeddedCover }

    public init(
        path: String, title: String, artist: String? = nil, albumArtist: String? = nil,
        album: String? = nil, year: Int? = nil, track: Int? = nil, disc: Int? = nil,
        genre: String? = nil, durationS: Double? = nil, explicit: Bool = false,
        onlyCopy: Bool = false, match: String? = nil, format: String? = nil,
        bitrateKbps: Int? = nil, cover: String? = nil, embeddedCover: Bool = false,
        lyrics: Lyrics = .none, trackId: String? = nil
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
    }

    /// The songs matching every word of `query`, whatever the case or accents.
    public func search(_ query: String) -> [Track] {
        let words = fold(query).split(separator: " ")
        if words.isEmpty { return tracks }
        return tracks.filter { track in
            guard let text = searchText[track.path] else { return false }
            return words.allSatisfy { text.contains($0) }
        }
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
