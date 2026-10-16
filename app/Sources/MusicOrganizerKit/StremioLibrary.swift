import Compression
import Foundation

/// What Stremio remembers of what's been watched, read for an import into a profile (the
/// owner, 2026-10-16: "yes build the import from stremio"): its Library (what the owner
/// added: the Watchlist here), each episode watched, each movie watched, and where each
/// film was left. Stremio for Mac keeps it in its own storage on this Mac (the "library"
/// and "library_recent" records); its export file has the same items, each under "d".
/// Only read: nothing here writes to Stremio.
public enum StremioLibrary {
    /// One title as Stremio keeps it.
    public struct Item: Decodable, Sendable {
        public let id: String
        public let type: String
        public let name: String
        public let poster: String?
        public let posterShape: String?
        /// Taken out of the Library (or never in it): only watched.
        public let removed: Bool
        public let mtime: String?
        public let state: State

        enum CodingKeys: String, CodingKey {
            case id = "_id", type, name, poster, posterShape, removed, mtime = "_mtime", state
        }

        public init(from decoder: Decoder) throws {
            let raw = try decoder.container(keyedBy: CodingKeys.self)
            id = try raw.decode(String.self, forKey: .id)
            type = try raw.decode(String.self, forKey: .type)
            name = try raw.decodeIfPresent(String.self, forKey: .name) ?? ""
            poster = try raw.decodeIfPresent(String.self, forKey: .poster)
            posterShape = try raw.decodeIfPresent(String.self, forKey: .posterShape)
            removed = try raw.decodeIfPresent(Bool.self, forKey: .removed) ?? false
            mtime = try raw.decodeIfPresent(String.self, forKey: .mtime)
            state = try raw.decodeIfPresent(State.self, forKey: .state) ?? State()
        }
    }

    /// Where a title was got to. Times are in milliseconds, as Stremio keeps them.
    public struct State: Decodable, Sendable {
        public var lastWatched: String?
        public var timeOffset: Double?
        public var duration: Double?
        /// The episode it was on ("tt0944947:1:2"), or the film's own id.
        public var videoId: String?
        /// Which episodes have been watched (`Watched`).
        public var watched: String?
        public var flaggedWatched: Int?
        public var timesWatched: Int?

        enum CodingKeys: String, CodingKey {
            case lastWatched, timeOffset, duration, videoId = "video_id", watched, flaggedWatched, timesWatched
        }

        public init() {}

        public init(from decoder: Decoder) throws {
            let raw = try decoder.container(keyedBy: CodingKeys.self)
            lastWatched = try? raw.decodeIfPresent(String.self, forKey: .lastWatched)
            timeOffset = try? raw.decodeIfPresent(Double.self, forKey: .timeOffset)
            duration = try? raw.decodeIfPresent(Double.self, forKey: .duration)
            videoId = try? raw.decodeIfPresent(String.self, forKey: .videoId)
            watched = try? raw.decodeIfPresent(String.self, forKey: .watched)
            flaggedWatched = try? raw.decodeIfPresent(Int.self, forKey: .flaggedWatched)
            timesWatched = try? raw.decodeIfPresent(Int.self, forKey: .timesWatched)
        }
    }

    // MARK: reading

    /// The items of Stremio's own records ("library", "library_recent": `{"items": {id: item}}`),
    /// each title once: where two records have it, the one changed last.
    public static func items(fromRecords records: [Data]) -> [Item] {
        struct Record: Decodable { let items: [String: Lenient] }
        var found: [String: Item] = [:]
        for data in records {
            guard let record = try? JSONDecoder().decode(Record.self, from: data) else { continue }
            for item in record.items.values.compactMap(\.item) { keep(item, in: &found) }
        }
        return Array(found.values)
    }

    /// The items of Stremio's export file (`{"library": [{"d": item}, …]}`).
    public static func items(fromExport data: Data) -> [Item] {
        struct Entry: Decodable { let d: Lenient }
        struct Export: Decodable { let library: [Entry] }
        guard let export = try? JSONDecoder().decode(Export.self, from: data) else { return [] }
        var found: [String: Item] = [:]
        for item in export.library.compactMap(\.d.item) { keep(item, in: &found) }
        return Array(found.values)
    }

    /// One item, or nothing for one that can't be read (the rest are still taken).
    private struct Lenient: Decodable {
        let item: Item?
        init(from decoder: Decoder) throws { item = try? Item(from: decoder) }
    }

    private static func keep(_ item: Item, in found: inout [String: Item]) {
        if let old = found[item.id], (old.mtime ?? "") >= (item.mtime ?? "") { return }
        found[item.id] = item
    }

    // MARK: watched episodes

    /// Which episodes of a series are watched, as Stremio writes it: the last episode
    /// watched, how many episodes there were up to and with it then, and a bit for each
    /// episode in the series' order (lowest bit first), squeezed with zlib and in base64.
    /// Episodes added to the series since are lined up by that last one.
    public struct Watched: Equatable, Sendable {
        public let anchor: String
        public let anchorLength: Int
        public let bits: [UInt8]

        public init?(_ text: String) {
            let parts = text.split(separator: ":", omittingEmptySubsequences: false).map(String.init)
            guard parts.count >= 3, let length = Int(parts[parts.count - 2]),
                let packed = Data(base64Encoded: parts[parts.count - 1]), let bits = Self.inflate(packed)
            else { return nil }
            anchor = parts[..<(parts.count - 2)].joined(separator: ":")
            anchorLength = length
            self.bits = bits
        }

        /// Whether the bit at `index` is set.
        func isSet(_ index: Int) -> Bool {
            guard index >= 0, index / 8 < bits.count else { return false }
            return bits[index / 8] >> (index % 8) & 1 == 1
        }

        /// The watched episodes' ids among `videoIds` (the series' episodes in its
        /// plugin's order). None when the last episode watched isn't among them.
        public func episodes(in videoIds: [String]) -> [String] {
            guard let at = videoIds.firstIndex(of: anchor) else { return [] }
            let offset = anchorLength - at - 1
            return videoIds.indices.filter { isSet($0 + offset) }.map { videoIds[$0] }
        }

        /// zlib's stream (two bytes of header, then deflate) opened.
        static func inflate(_ data: Data) -> [UInt8]? {
            guard data.count > 2 else { return nil }
            let body = [UInt8](data.dropFirst(2))
            var out = [UInt8](repeating: 0, count: 1 << 16)
            let written = compression_decode_buffer(&out, out.count, body, body.count, nil, COMPRESSION_ZLIB)
            return written > 0 ? Array(out.prefix(written)) : nil
        }
    }

    // MARK: what's taken

    /// Titles from the plugins the app reads films and anime from: Cinemeta's ("tt…") and
    /// Anime Kitsu's ("kitsu:…"). Anything else (another plugin's own ids, an adults-only
    /// plugin's among them, and channels) is left out: nothing of a plugin for adults is
    /// ever remembered here (CLAUDE.md).
    public static func isTaken(_ item: Item) -> Bool {
        ["movie", "series", "anime"].contains(item.type)
            && (item.id.hasPrefix("tt") || item.id.hasPrefix("kitsu:"))
    }

    /// The title as the app's lists keep it. What its plugin says now (`details`) is
    /// preferred: Stremio keeps no genres, and calls a Kitsu series "anime".
    public static func mediaItem(_ item: Item, details: MediaDetails?) -> MediaItem {
        let type = details?.type ?? (item.type == "movie" ? "movie" : "series")
        let made = MediaItem(
            id: item.id, type: type == "movie" ? "movie" : "series",
            name: details?.name ?? item.name, poster: details?.poster ?? item.poster,
            posterShape: item.posterShape ?? "poster", year: details?.year,
            rating: details?.rating, genres: details?.genres ?? [])
        return item.type == "anime" || item.id.hasPrefix("kitsu:") ? made.asAnime : made
    }

    /// A series' episodes in the order Stremio counts them for `Watched`: by season (the
    /// specials, season 0, first) and number, as Cinemeta lists them. (The engine gives
    /// them newest first; counted in that order the bits fell on the wrong episodes.)
    public static func stremioOrder(_ videos: [MediaDetails.Video]) -> [String] {
        videos.enumerated().sorted { first, second in
            let a = (first.element.season ?? 0, first.element.episode ?? 0)
            let b = (second.element.season ?? 0, second.element.episode ?? 0)
            return a == b ? first.offset < second.offset : a < b
        }.map(\.element.id)
    }

    /// Everything an import would put in a profile, worked out before anything is put.
    public struct Plan: Sendable {
        /// What was in Stremio's Library: for the Watchlist, the latest first.
        public var watchlist: [MediaItem] = []
        /// Films and episodes watched, with when.
        public var finished: [String: Date] = [:]
        /// Every title watched, with where it was left: for Continue Watching, Recently
        /// Watched and the Watched list.
        public var history: [WatchHistory.Entry] = []
        public var moviesWatched = 0
        public var episodesWatched = 0
        public var seriesWithEpisodes = 0
        public var partWay = 0
        /// Titles left out (other plugins, channels).
        public var leftOut = 0
        /// Series whose watched episodes couldn't be lined up (their episodes couldn't be read).
        public var episodesUnread = 0

        public init() {}
    }

    /// What an import of these items would put in a profile. `details` is each title's
    /// details as its plugin gives them now, by id: a series' episodes are needed to know
    /// which were watched. A movie is watched when Stremio says so (it has its own mark,
    /// and counts one played most of the way) or when it was left past nine tenths.
    public static func plan(_ items: [Item], details: [String: MediaDetails]) -> Plan {
        var plan = Plan()
        let taken = items.filter(isTaken)
        plan.leftOut = items.count - taken.count
        func moment(_ text: String?) -> Date? { text.flatMap(Released.date) }
        for item in taken.sorted(by: { ($0.state.lastWatched ?? $0.mtime ?? "") > ($1.state.lastWatched ?? $1.mtime ?? "") }) {
            let found = details[item.id]
            let media = mediaItem(item, details: found)
            let state = item.state
            let when = moment(state.lastWatched) ?? moment(item.mtime) ?? Date()
            if !item.removed { plan.watchlist.append(media) }
            let seconds = (state.timeOffset ?? 0) / 1000
            let length = (state.duration ?? 0) / 1000
            if media.type == "movie" {
                let watched = (state.flaggedWatched ?? 0) > 0 || (state.timesWatched ?? 0) > 0
                    || (length > 0 && seconds >= length * Finished.mark)
                if watched {
                    plan.finished[item.id] = when
                    plan.moviesWatched += 1
                }
            } else if let field = state.watched, let watched = Watched(field) {
                if let videos = found?.videos, !videos.isEmpty {
                    let episodes = watched.episodes(in: stremioOrder(videos))
                    for episode in episodes { plan.finished[episode] = when }
                    plan.episodesWatched += episodes.count
                    if !episodes.isEmpty { plan.seriesWithEpisodes += 1 }
                } else {
                    plan.episodesUnread += 1
                }
            }
            // Watched at some time: a line of the history, where it was left.
            guard state.lastWatched != nil else { continue }
            let episode = media.type == "series"
                ? found?.videos.first { $0.id == state.videoId } : nil
            var entry = WatchHistory.Entry.watching(media, episode: episode)
            (entry.seconds, entry.length, entry.at) = (seconds, length, when)
            entry.episodeId = media.type == "series" ? state.videoId : nil
            if entry.isUnfinished { plan.partWay += 1 }
            plan.history.append(entry)
        }
        return plan
    }
}

extension WatchHistory {
    /// Entries from elsewhere (an import) put with these: one that isn't here is added;
    /// one that is takes the newer of the two (the later watched). Nothing is taken away.
    /// The latest watched first, at most `most`.
    public mutating func merge(_ others: [Entry]) {
        var byId = Dictionary(entries.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        for other in others {
            if let here = byId[other.id], here.at >= other.at { continue }
            var taken = other
            // The way it was played here is kept, when it's the same episode.
            if let here = byId[other.id], taken.stream == nil, here.episodeId == other.episodeId {
                (taken.stream, taken.playName, taken.source) = (here.stream, here.playName, here.source)
            }
            byId[other.id] = taken
        }
        self = WatchHistory(Array(byId.values.sorted { $0.at > $1.at }.prefix(Self.most)))
    }
}
