import Foundation

/// What a profile and a paired video app on a phone or tablet tell each other of what's
/// watched (the owner, 2026-10-16: "This sync only brings in watchlist, watched, progress
/// of the shows ... It adds information, doesn't remove it"): the Watchlist, every film and
/// episode watched, and where each title was got to. The shape is the home sync's
/// (docs/ENGINE_API.md, section 3: Title, Watched, Progress). Each side adds what the
/// other sent to its own: nothing is ever taken away.
public enum WatchSync {
    /// A film or series, as a list has it.
    public struct Title: Codable, Equatable, Sendable {
        public var id: String
        public var type: String
        public var name: String
        public var poster: String?
        public var posterShape: String?
        public var year: String?
        public var genres: [String]?
        public var anime: Bool?

        public init(_ item: MediaItem) {
            (id, type, name, poster, posterShape) = (item.id, item.type, item.name, item.poster, item.posterShape)
            (year, genres, anime) = (item.year, item.genres.isEmpty ? nil : item.genres, item.anime == true ? true : nil)
        }

        public var item: MediaItem {
            let made = MediaItem(
                id: id, type: type == "movie" ? "movie" : "series", name: name, poster: poster,
                posterShape: posterShape ?? "poster", year: year, rating: nil, genres: genres ?? [])
            return anime == true ? made.asAnime : made
        }
    }

    /// A film or an episode watched, and when.
    public struct Watched: Codable, Equatable, Sendable {
        public var id: String
        public var at: String
    }

    /// Where a title was got to: for a series, which episode.
    public struct Progress: Codable, Equatable, Sendable {
        public var id: String
        public var type: String
        public var name: String
        public var poster: String?
        public var episode: String?
        public var detail: String?
        public var seconds: Double
        public var length: Double
        public var at: String
        public var title: Title?
    }

    public struct State: Codable, Equatable, Sendable {
        public var format: Int?
        public var revision: String?
        public var watchlist: [Title]
        public var watched: [Watched]
        public var progress: [Progress]

        public init(watchlist: [Title] = [], watched: [Watched] = [], progress: [Progress] = []) {
            (self.watchlist, self.watched, self.progress) = (watchlist, watched, progress)
        }
    }

    /// A profile's (or an account's) watching, to send.
    public static func state(watchlist: Watchlist, finished: Finished, history: WatchHistory) -> State {
        let watched = finished.days.sorted { $0.key < $1.key }.map { Watched(id: $0.key, at: stamp($0.value)) }
        let progress = history.entries.compactMap { entry -> Progress? in
            guard entry.kind != .video, let item = entry.item else { return nil }
            return Progress(
                id: entry.id, type: entry.kind == .series ? "series" : "movie", name: entry.name,
                poster: entry.picture, episode: entry.episodeId, detail: entry.detail,
                seconds: entry.seconds, length: entry.length, at: stamp(entry.at), title: Title(item))
        }
        return State(watchlist: watchlist.items.map(Title.init), watched: watched, progress: progress)
    }

    /// What the other side sent, added to these records: a title not on the Watchlist goes
    /// on it (after the ones there), an id not watched is watched, and of two places for a
    /// title the later one wins. Nothing is taken off, unwatched or moved back. What the
    /// owner took off this Watchlist, or marked Not Watched here, isn't put back: the other
    /// side keeps its own (`Watchlist.takenOff`, `Finished.unmarked`). Returns whether
    /// anything changed.
    @discardableResult
    public static func merge(
        _ sent: State, into watchlist: inout Watchlist, finished: inout Finished, history: inout WatchHistory
    ) -> Bool {
        let before = (watchlist.items.count, finished.days.count, history.entries)
        watchlist.add(sent.watchlist.map(\.item).filter { !watchlist.takenOff.contains($0.id) })
        for one in sent.watched where !finished.contains(one.id) && !finished.unmarked.contains(one.id) {
            finished.mark(one.id, watched: true, now: Released.date(one.at) ?? Date())
        }
        let entries = sent.progress.compactMap { line -> WatchHistory.Entry? in
            guard let at = Released.date(line.at) else { return nil }
            // The title as sent with it; else as it's known here; else made from the line,
            // so the place is never one without a title (nothing to open, nothing to send).
            let item = line.title?.item ?? history.entries.first { $0.id == line.id }?.item
                ?? MediaItem(
                    id: line.id, type: line.type == "movie" ? "movie" : "series", name: line.name,
                    poster: line.poster, posterShape: "poster", year: nil, rating: nil, genres: [])
            var entry = WatchHistory.Entry(
                id: line.id, kind: line.type == "series" ? .series : .movie, name: line.name,
                detail: line.detail, picture: line.poster, item: item, seconds: line.seconds,
                length: line.length, at: at)
            entry.episodeId = line.episode
            return entry
        }
        history.merge(entries)
        return before != (watchlist.items.count, finished.days.count, history.entries)
    }

    /// A moment as the sync writes it: "2026-10-16T05:30:00Z".
    public static func stamp(_ moment: Date) -> String {
        let format = ISO8601DateFormatter()
        format.formatOptions = [.withInternetDateTime]
        return format.string(from: moment)
    }
}
