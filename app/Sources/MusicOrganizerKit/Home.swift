import Foundation

/// One row the Home page can show (the owner's drawing, 2026-10-08): a kind of thing
/// (songs, channels, movies, series) and which of them.
public struct HomeSection: Identifiable, Hashable, Sendable {
    public enum Group: String, CaseIterable, Sendable {
        case songs, channels, movies, series, anime

        public var title: String {
            switch self {
            case .songs: "Songs"
            case .channels: "Channels"
            case .movies: "Movies"
            case .series: "Series"
            case .anime: "Anime"
            }
        }

        /// What add-ons call the kind of their lists: "movie", "series" or "anime". Nil
        /// for songs and channels. (What's in a list of anime is a series or a movie.)
        public var mediaType: String? {
            switch self {
            case .movies: "movie"
            case .series: "series"
            case .anime: "anime"
            default: nil
            }
        }
    }

    public let id: String
    public let group: Group
    public let title: String
    /// A row of one genre ("movies.genre.Action"): the genre.
    public let genre: String?
    /// A row the owner made from a search by two genres: the second genre.
    public let also: String?

    public init(id: String, group: Group, title: String, genre: String? = nil, also: String? = nil) {
        (self.id, self.group, self.title, self.genre, self.also) = (id, group, title, genre, also)
    }

    /// A row made in Movie Finder or Series Finder from a search by two genres (the
    /// owner, 2026-10-08): "Custom Search Documentary + Crime".
    public static func custom(_ group: Group, _ genre: String, _ also: String) -> HomeSection {
        HomeSection(
            id: "\(group.rawValue).custom.\(genre)\(Self.joint)\(also)", group: group,
            title: "Custom Search \(genre) + \(also)", genre: genre, also: also)
    }

    /// A custom row read back from its id (they aren't in the list of rows on offer:
    /// the owner makes them). Nil for any other id.
    public init?(customID id: String) {
        let parts = id.split(separator: ".", maxSplits: 2).map(String.init)
        guard parts.count == 3, parts[1] == "custom", let group = Group(rawValue: parts[0]),
            group == .movies || group == .series
        else { return nil }
        let genres = parts[2].components(separatedBy: Self.joint)
        guard genres.count == 2, !genres[0].isEmpty, !genres[1].isEmpty else { return nil }
        self = .custom(group, genres[0], genres[1])
    }

    /// Between a custom row's two genres in its id. (Not "+" or "&": genres have those.)
    static let joint = "|and|"

    /// What kind of row it is, without its group or genre: "favourites", "continue",
    /// "genre" …
    public var kind: String {
        let parts = id.split(separator: ".", maxSplits: 2).map(String.init)
        return parts.count > 1 ? parts[1] : id
    }

    /// Every row on offer. A genre row is offered for each genre the film add-on's
    /// lists have, so the choice grows with the add-on.
    public static func all(movieGenres: [String], seriesGenres: [String]) -> [HomeSection] {
        var found: [HomeSection] = [
            .init(id: "songs.favourites", group: .songs, title: "Your Favourite Songs"),
            .init(id: "songs.recommended", group: .songs, title: "Recommended Songs"),
            .init(id: "songs.mostPlayed", group: .songs, title: "Your Most Played Songs"),
            .init(id: "songs.recentlyAdded", group: .songs, title: "Recently Added Songs"),
            .init(id: "songs.downloads", group: .songs, title: "Your Latest Downloads"),
            .init(id: "channels.continue", group: .channels, title: "Continue Watching Your Channels"),
            .init(id: "channels.recent", group: .channels, title: "Recently Watched Videos"),
            .init(id: "channels.favourites", group: .channels, title: "Your Favourite Channels"),
            .init(id: "channels.new", group: .channels, title: "New From Your Channels"),
            .init(id: "channels.unwatched", group: .channels, title: "Videos You Haven't Watched"),
            .init(id: "channels.recommended", group: .channels, title: "Recommended Channels"),
            .init(id: "channels.downloaded", group: .channels, title: "Your Downloaded Videos"),
        ]
        for (group, genres) in [(Group.movies, movieGenres), (.series, seriesGenres)] {
            let (key, things) = (group.rawValue, group.title)
            found += [
                .init(id: "\(key).continue", group: group, title: "Continue Watching \(things)"),
                .init(id: "\(key).recent", group: group, title: "Recently Watched \(things)"),
                .init(id: "\(key).favourites", group: group, title: "Your Favourite \(things)"),
                .init(id: "\(key).unwatched", group: group, title: "\(things) You Haven't Watched"),
                .init(id: "\(key).recommended", group: group, title: "Recommended \(things)"),
                .init(id: "\(key).popular", group: group, title: "Popular \(things) Now"),
                .init(id: "\(key).new", group: group, title: "New \(things)"),
                .init(id: "\(key).best", group: group, title: "Best Rated \(things)"),
            ]
            found += genres.map {
                .init(id: "\(key).genre.\($0)", group: group, title: "\($0) \(things)", genre: $0)
            }
        }
        // Anime (2026-10-15, with the owner's Recommended row for it): from the lists of
        // anime the plugins give.
        found += [
            .init(id: "anime.recommended", group: .anime, title: "Recommended Anime"),
            .init(id: "anime.popular", group: .anime, title: "Popular Anime Now"),
        ]
        return found
    }

    /// The Recommended rows put on a Home page that was made before they were asked for
    /// (the owner, 2026-10-15: "Give a recommended section in home. for tv shows and
    /// series, anime"): each after the last row of its kind, or at the end.
    public static let recommendedAdded = "homeRecommendedAdded"
    public static let recommended = ["series.recommended", "anime.recommended"]

    /// The page as it starts, before the owner has changed it: their drawing's rows.
    public static let standard = [
        "songs.favourites", "songs.recommended", "channels.continue", "channels.favourites",
        "channels.recommended", "movies.continue", "movies.popular", "series.continue",
        "series.popular",
    ]
}

/// Which rows the owner's Home page shows, in their order (the page's Overview edits it).
public struct HomeLayout: Equatable, Sendable {
    public private(set) var shown: [String]

    public init(_ shown: [String] = HomeSection.standard) {
        var seen = Set<String>()
        self.shown = shown.filter { seen.insert($0).inserted }
    }

    public func shows(_ id: String) -> Bool { shown.contains(id) }

    /// Put a row on the page (at the end), or take it off.
    public mutating func set(_ id: String, shown on: Bool) {
        if on, !shown.contains(id) { shown.append(id) }
        if !on { shown.removeAll { $0 == id } }
    }

    /// The Recommended rows (`HomeSection.recommended`) put on the page once: each after
    /// the last row of its kind (anime's after the series'), or at the end.
    public mutating func addRecommended() {
        for id in HomeSection.recommended where !shown.contains(id) {
            let kind = String(id.prefix { $0 != "." })
            let after = kind == "anime" ? ["anime.", "series."] : [kind + "."]
            let rows = shown
            if let last = after.compactMap({ prefix in rows.lastIndex { $0.hasPrefix(prefix) } }).first {
                shown.insert(id, at: last + 1)
            } else {
                shown.append(id)
            }
        }
    }

    /// Move a row one place up (-1) or down (1).
    public mutating func move(_ id: String, by step: Int) {
        guard let from = shown.firstIndex(of: id), shown.indices.contains(from + step) else { return }
        shown.swapAt(from, from + step)
    }

    /// Put the rows in this order (the page's list, after one was dragged). A row that's
    /// chosen but not in `ids` (it isn't on offer just now) keeps its place at the end.
    public mutating func arrange(_ ids: [String]) {
        var seen = Set<String>()
        let wanted = ids.filter { shown.contains($0) && seen.insert($0).inserted }
        shown = wanted + shown.filter { !seen.contains($0) }
    }

    /// The rows to draw: the chosen ones that are still on offer, in order.
    public func sections(from all: [HomeSection]) -> [HomeSection] {
        let known = Dictionary(all.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        return shown.compactMap { known[$0] ?? HomeSection(customID: $0) }
    }

    public static let key = "homeLayout"

    public static func saved(in defaults: UserDefaults = .standard) -> HomeLayout {
        (defaults.array(forKey: key) as? [String]).map(HomeLayout.init) ?? HomeLayout()
    }

    public func save(in defaults: UserDefaults = .standard) {
        defaults.set(shown, forKey: Self.key)
    }
}

/// What has been watched, for Home's "Continue Watching" and "Recently Watched": channel
/// videos, movies and series' episodes. Kept by the app on this Mac, newest first;
/// nothing of it is in the library or sent anywhere.
public struct WatchHistory: Equatable, Sendable {
    public struct Entry: Codable, Identifiable, Hashable, Sendable {
        public enum Kind: String, Codable, Sendable { case video, movie, series }

        /// What it is: a video's id, or a film's or series' id from its add-on.
        public let id: String
        public let kind: Kind
        public let name: String
        /// A video's channel, or an episode ("S1 E2 · Pilot").
        public let detail: String?
        public let picture: String?
        /// A channel video's channel, so its page can be opened.
        public let channelId: String?
        /// A film or series as its list gave it, so its page can be opened again.
        public let item: MediaItem?
        public var seconds: Double
        public var length: Double
        public var at: Date
        /// The way a film or an episode was played, and the name it was played under, so
        /// a click on it in Continue Watching plays it again from where it was left
        /// (the owner, 2026-10-12) without its page being gone through. Nil for a video,
        /// which is played by its id, and for one watched before this was remembered.
        public var stream: MediaStream?
        public var playName: String?
        /// A series' episode that was played, and the add-on whose way to play it was:
        /// what's needed to play the one after it by itself (`NextEpisode`). Nil for
        /// anything else, and for one watched before this was remembered.
        public var episodeId: String?
        public var source: String?

        public init(
            id: String, kind: Kind, name: String, detail: String? = nil, picture: String? = nil,
            channelId: String? = nil, item: MediaItem? = nil, seconds: Double = 0,
            length: Double = 0, at: Date = Date()
        ) {
            (self.id, self.kind, self.name, self.detail, self.picture) = (id, kind, name, detail, picture)
            (self.channelId, self.item, self.seconds, self.length, self.at) =
                (channelId, item, seconds, length, at)
        }

        /// What's remembered when a film, or an episode of a series, is played from its
        /// page: the film, or the series with the episode it has got to.
        public static func watching(_ item: MediaItem, episode: MediaDetails.Video? = nil) -> Entry {
            let about = episode.map { [$0.number, $0.title].compactMap { $0 }.joined(separator: " · ") }
            return Entry(
                id: item.id, kind: item.type == "series" ? .series : .movie, name: item.name, detail: about,
                picture: item.poster, item: item)
        }

        /// Part way through: begun, and not past the mark that's all of it (the owner,
        /// 2026-10-15: nine tenths; the rest is the credits, `Finished.mark`).
        public var isUnfinished: Bool {
            length > 0 && seconds >= FilmPositions.startS && progress < Finished.mark
        }

        /// How far through it is, 0 to 1.
        public var progress: Double { length > 0 ? min(max(seconds / length, 0), 1) : 0 }
    }

    public static let most = 200
    public private(set) var entries: [Entry]

    public init(_ entries: [Entry] = []) { self.entries = entries }

    /// Something was started, or has moved on: it goes to the front. An entry for the
    /// same thing keeps its place in the film if the new one doesn't know it yet; a
    /// series' only if it's the same episode (the next one starts at its beginning).
    public mutating func watched(_ entry: Entry) {
        var fresh = entry
        if let old = entries.first(where: { $0.id == entry.id }), entry.length == 0,
            old.episodeId == entry.episodeId || entry.episodeId == nil
        {
            (fresh.seconds, fresh.length) = (old.seconds, old.length)
        }
        entries.removeAll { $0.id == entry.id }
        entries.insert(fresh, at: 0)
        if entries.count > Self.most { entries.removeLast(entries.count - Self.most) }
    }

    /// Where something has got to, without moving it in the list.
    public mutating func place(_ id: String, seconds: Double, length: Double, now: Date = Date()) {
        guard length > 0, let index = entries.firstIndex(where: { $0.id == id }) else { return }
        (entries[index].seconds, entries[index].length, entries[index].at) = (seconds, length, now)
    }

    public mutating func forget(_ id: String) { entries.removeAll { $0.id == id } }

    /// Past this much of something, it has been begun in earnest: the owner's mark
    /// (2026-10-12) for what still counts as not watched.
    public static let begun = 0.25

    /// Whether this has been watched past the mark. Something never played hasn't.
    public func isBegun(_ id: String) -> Bool {
        entries.first { $0.id == id }.map { $0.progress > Self.begun } ?? false
    }

    public func recent(_ kind: Entry.Kind) -> [Entry] { entries.filter { $0.kind == kind } }

    /// Whether a film or a series has been watched at all: played from its page, however
    /// far (the owner, 2026-10-13: "if you've watched anything", it has a green check on
    /// its picture). A channel's video isn't what this is asked of.
    public func hasWatched(_ id: String) -> Bool {
        entries.contains { $0.id == id && $0.kind != .video }
    }

    /// The films, or the series, that have been watched, the latest first, as their
    /// lists gave them: the Watched list of Movie Finder and Series Finder.
    public func watched(_ kind: Entry.Kind) -> [MediaItem] { recent(kind).compactMap(\.item) }
    public func unfinished(_ kind: Entry.Kind) -> [Entry] { recent(kind).filter(\.isUnfinished) }

    public static let key = "watchHistory"

    public static func saved(in defaults: UserDefaults = .standard) -> WatchHistory {
        guard let data = defaults.data(forKey: key),
            let entries = try? JSONDecoder().decode([Entry].self, from: data)
        else { return WatchHistory() }
        return WatchHistory(entries)
    }

    public func save(in defaults: UserDefaults = .standard) {
        if let data = try? JSONEncoder().encode(entries) { defaults.set(data, forKey: Self.key) }
    }
}

/// The movies and series the owner has marked as favourites (the heart on a film's page).
/// Kept by the app on this Mac, newest first.
public struct MediaFavourites: Equatable, Sendable {
    public private(set) var items: [MediaItem]

    public init(_ items: [MediaItem] = []) { self.items = items }

    public func contains(_ item: MediaItem) -> Bool { items.contains { $0.id == item.id } }

    public mutating func toggle(_ item: MediaItem) {
        if contains(item) {
            items.removeAll { $0.id == item.id }
        } else {
            items.insert(item, at: 0)
        }
    }

    public func of(type: String) -> [MediaItem] { items.filter { $0.type == type } }

    /// The genre met most often among these and what's been watched: what "Recommended"
    /// goes by. Nil when there's nothing to go on. A tie goes to the name first in the alphabet.
    public static func leadingGenre(_ items: [MediaItem]) -> String? {
        var counts: [String: Int] = [:]
        for genre in items.flatMap(\.genres) { counts[genre, default: 0] += 1 }
        return counts.max { ($0.value, $1.key) < ($1.value, $0.key) }?.key
    }

    public static let key = "mediaFavourites"

    public static func saved(in defaults: UserDefaults = .standard) -> MediaFavourites {
        guard let data = defaults.data(forKey: key),
            let items = try? JSONDecoder().decode([MediaItem].self, from: data)
        else { return MediaFavourites() }
        return MediaFavourites(items)
    }

    public func save(in defaults: UserDefaults = .standard) {
        if let data = try? JSONEncoder().encode(items) { defaults.set(data, forKey: Self.key) }
    }
}

/// The Watchlist: the movies and series the owner means to watch, put on it from a film's
/// or a series' own page (the owner, 2026-10-14: "say you click on a tv show, you can add
/// it to watchlist"). Kept by the app on this Mac, the newest first; nothing of it is in
/// the library. What's been watched is `WatchHistory`'s, and shows as a green check on
/// the same cards.
public struct Watchlist: Equatable, Sendable {
    public private(set) var items: [MediaItem]

    public init(_ items: [MediaItem] = []) { self.items = items }

    public func contains(_ item: MediaItem) -> Bool { items.contains { $0.id == item.id } }

    /// Put it on the list, at the front; or, if it's on it, take it off.
    public mutating func toggle(_ item: MediaItem) {
        if contains(item) {
            items.removeAll { $0.id == item.id }
        } else {
            items.insert(item, at: 0)
        }
    }

    /// The films ("movie"), or the series ("series"), on the list.
    public func of(type: String) -> [MediaItem] { items.filter { $0.type == type } }

    public static let key = "watchlist"

    public static func saved(in defaults: UserDefaults = .standard) -> Watchlist {
        guard let data = defaults.data(forKey: key),
            let items = try? JSONDecoder().decode([MediaItem].self, from: data)
        else { return Watchlist() }
        return Watchlist(items)
    }

    public func save(in defaults: UserDefaults = .standard) {
        if let data = try? JSONEncoder().encode(items) { defaults.set(data, forKey: Self.key) }
    }
}

/// A Home row shows whole cards only, as many as fit, and pages through the rest.
public enum HomePaging {
    /// How many whole cards `card` wide, `gap` apart, fit across `room`: at least one.
    public static func fitting(_ room: Double, card: Double, gap: Double) -> Int {
        guard card > 0, room.isFinite else { return 1 }
        return max(Int((room + gap) / (card + gap)), 1)
    }
}
