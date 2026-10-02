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
            note += "It takes \(roughTime(minutes: minutes)), paced so YouTube doesn't refuse "
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
