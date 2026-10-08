import Foundation

/// An add-on: a web service that lists movies or channels (the engine's Addon). The
/// engine asks it; the app only shows what came back.
public struct Addon: Decodable, Identifiable, Hashable, Sendable {
    public struct Extra: Decodable, Hashable, Sendable {
        public let name: String
        public let required: Bool
        public let options: [String]
    }

    public struct Catalog: Decodable, Hashable, Sendable {
        public let type: String
        public let id: String
        public let name: String?
        public let extra: [Extra]

        public func takes(_ name: String) -> Bool { extra.contains { $0.name == name } }
        /// A list that can only be searched: it has nothing to show until something is typed.
        public var needsSearch: Bool { extra.contains { $0.name == "search" && $0.required } }
        /// A list that can be opened as it is: it needs nothing but, at most, a genre.
        /// (An add-on's lists of "the episodes after these ones" need ids, and aren't.)
        public var canBeBrowsed: Bool {
            extra.allSatisfy { !$0.required || $0.name == "genre" }
                && !extra.contains { $0.name.hasSuffix("VideosIds") }
        }
        public var genres: [String] { extra.first { $0.name == "genre" }?.options ?? [] }
        /// A list that has to be asked with one of its genres (a list by year, whose
        /// "genres" are the years): there's no "every" to show.
        public var needsGenre: Bool { extra.contains { $0.name == "genre" && $0.required } }

        /// The genre a list is asked with: the one chosen if the list has it; or else
        /// none, unless the list needs one, when it's the first (the latest year).
        public func genre(chosen: String) -> String? {
            if genres.contains(chosen) { return chosen }
            return needsGenre || id == "year" ? genres.first : nil
        }

        /// Whether "every genre" is on offer: not for a list that needs one, nor a list
        /// by year (the owner: "remove every genre, and start it at the latest year").
        public var offersEveryGenre: Bool { !(needsGenre || id == "year") }
    }

    public let id: String
    public let name: String
    public let address: String
    public let types: [String]
    public let catalogs: [Catalog]
    public let version: String?
    public let description: String?
    /// For adults only, by its own word or the owner's mark. Its lists are shown in one
    /// Finder of their own and nowhere else; a child's profile is never given one.
    public let adult: Bool?
    public var isAdult: Bool { adult == true }

    /// The add-on's name as Settings shows it. The channels add-on calls itself by the
    /// service's name, which the app doesn't show (the owner's rule).
    public var shownName: String {
        name.localizedCaseInsensitiveContains("youtube") ? "Video Channels" : name
    }

    /// What it has, in words: "Movies, Series".
    public var offers: String {
        let words = ["movie": "Movies", "series": "Series", "channel": "Channels", "tv": "TV"]
        return types.map { words[$0] ?? $0.capitalized }.joined(separator: ", ")
    }

    /// The list with the add-on at `index` moved one place up (-1) or down (1), as the
    /// ids to ask the engine for; nil when it's already at that end.
    public static func order(_ addons: [Addon], moving index: Int, by step: Int) -> [String]? {
        let target = index + step
        guard addons.indices.contains(index), addons.indices.contains(target), step != 0
        else { return nil }
        var ids = addons.map(\.id)
        ids.swapAt(index, target)
        return ids
    }
}

public struct AddonsAnswer: Decodable, Sendable {
    public let addons: [Addon]
}

/// A film or a channel as a list shows it (the engine's Item).
public struct MediaItem: Codable, Identifiable, Hashable, Sendable {
    public let id: String
    public let type: String
    public let name: String
    public let poster: String?
    public let posterShape: String
    public let year: String?
    public let rating: Double?
    public let genres: [String]
}

public struct CatalogAnswer: Decodable, Sendable {
    public let items: [MediaItem]
    public let more: Bool
    /// Where the page after this one begins. (With two genres asked for, the engine
    /// reads past films it leaves out, so it isn't how many items came back.)
    public let nextSkip: Int?
}

/// A film's or a channel's details (the engine's Details).
public struct MediaDetails: Decodable, Sendable {
    public struct Video: Decodable, Identifiable, Hashable, Sendable {
        public let id: String
        public let title: String
        public let thumbnail: String?
        public let released: String?
        /// The id it's played by. Nil for something that isn't a video of a channel's.
        public let videoId: String?
        /// A series' episode: which season (0 for specials) and which episode of it.
        public let season: Int?
        public let episode: Int?
        /// What happens in the episode, when the add-on says.
        public let overview: String?

        /// The day it came out, as the add-on's timestamp starts ("2023-10-25").
        public var day: String { String((released ?? "").prefix(10)) }

        /// "S1 E2", for an episode.
        public var number: String? {
            guard let season, let episode else { return nil }
            return "S\(season) E\(episode)"
        }

        /// What an episode is called when it's played or kept: "East of Eden S01E02".
        public func name(in series: String) -> String {
            guard let season, let episode else { return series }
            return String(format: "%@ S%02dE%02d", series, season, episode)
        }
    }

    /// A series' seasons, in order, with the specials (season 0) last.
    public var seasons: [Int] {
        let found = Set(videos.compactMap(\.season))
        return found.filter { $0 > 0 }.sorted() + (found.contains(0) ? [0] : [])
    }

    /// One season's episodes, in order.
    public func episodes(in season: Int) -> [Video] {
        videos.filter { $0.season == season }.sorted { ($0.episode ?? 0) < ($1.episode ?? 0) }
    }

    public static func seasonName(_ season: Int) -> String {
        season == 0 ? "Specials" : "Season \(season)"
    }

    public let id: String
    public let type: String
    public let name: String
    public let poster: String?
    public let year: String?
    public let rating: Double?
    public let genres: [String]
    public let description: String?
    public let background: String?
    public let logo: String?
    public let runtimeMin: Int?
    public let cast: [String]
    public let directors: [String]
    public let trailerVideoId: String?
    public let videos: [Video]
}

/// One way to play a film (the engine's Stream).
public struct MediaStream: Decodable, Hashable, Sendable {
    public let kind: String
    public let name: String?
    public let title: String?
    public let quality: String?
    public let url: String?
    public let videoId: String?
    public let infoHash: String?
    public let fileIndex: Int?
    public let trackers: [String]

    /// Whether the app can play it: a plain web address, a video by its id, or a torrent.
    public var canPlay: Bool { ["url", "youtube", "torrent"].contains(kind) }

    /// The picture's size as a label: "Cam" for one filmed off a screen.
    public var qualityLabel: String {
        switch quality {
        case "cam": "Cam"
        case "4k": "4K"
        case let size?: size
        case nil: "Unknown"
        }
    }

    /// How it would be played, in plain words.
    public var kindLabel: String {
        switch kind {
        case "torrent": "Torrent"
        case "youtube": "Video"
        default: "Direct"
        }
    }
}

public struct StreamsAnswer: Decodable, Sendable {
    public struct Source: Decodable, Identifiable, Sendable {
        public let addonId: String
        public let addon: String
        public let streams: [MediaStream]
        public var id: String { addonId }
    }

    public struct Problem: Decodable, Hashable, Sendable {
        public let addon: String
        public let message: String
    }

    public let sources: [Source]
    public let problems: [Problem]

    public init(sources: [Source], problems: [Problem]) {
        self.sources = sources
        self.problems = problems
    }
}

/// What the Explore page under Video Finder offers (the owner's drawing, 2026-10-07):
/// Channels, Gaming, News, Sports, Learning and Podcasts. Each is one of the channel
/// add-on's genres, under the owner's name for it; one the add-on has no genre for is
/// filled by a search for channels of that kind instead.
public enum VideoExplore {
    public struct Section: Identifiable, Hashable, Sendable {
        public let name: String
        /// The add-on's genre; nil for every channel, and for a section that's searched.
        public let genre: String?
        /// The words channels are searched by, for a section the add-on has no list for.
        public let search: String?
        public var id: String { name }

        public init(name: String, genre: String? = nil, search: String? = nil) {
            self.name = name
            self.genre = genre
            self.search = search
        }
    }

    /// The owner's names, the genre each is under in a channel add-on, and the words
    /// channels are searched by for it.
    static let wanted: [(name: String, genres: [String], search: String)] = [
        ("Gaming", ["Gaming"], "gaming"),
        ("News", ["News", "News & Politics"], "news"),
        ("Sports", ["Sports"], "sports"),
        ("Learning", ["Learning", "Education", "Science & Education"], "educational"),
        ("Podcasts", ["Podcasts"], "podcast"),
    ]

    /// The sections, in the owner's order: Channels, then the five by name, then the
    /// rest of the catalog's genres under their own names. The five are always searched
    /// afresh (the owner, 2026-10-07: "make sure that all the channels are up to date"):
    /// the channels add-on's own lists are as they stood in 2023. Its genre for one of
    /// the five is so not shown a second time under its own name.
    public static func sections(for genres: [String]) -> [Section] {
        var found = [Section(name: "Channels")]
        var used = Set<String>()
        for (name, names, search) in wanted {
            found.append(Section(name: name, search: search))
            used.formUnion(names)
        }
        found += genres.filter { !used.contains($0) }.map { Section(name: $0, genre: $0) }
        return found
    }
}

/// How a film playing from a torrent is getting on (the engine's `torrent.status`).
public struct TorrentStatus: Decodable, Equatable, Sendable {
    public let state: String
    public let peers: Int
    public let bytesPerSecond: Int
    public let progress: Double
    /// The film is being kept and hasn't all arrived yet.
    public let keeping: Bool?
    /// Where a kept film was saved.
    public let keptPath: String?
    /// Why a film couldn't be kept.
    public let keepError: String?
    /// Something to know about how it was kept ("It was kept as it arrived: …").
    public let keepNote: String?
    /// While a kept film is being made into one phones and tablets play: how far, 0 to 1.
    public let converting: Double?

    /// Settings → Downloads: a kept film is converted for phones and tablets (on as standard).
    public static let convertKey = "convertKeptFilms"

    public init(
        state: String, peers: Int, bytesPerSecond: Int, progress: Double, keeping: Bool? = nil,
        keptPath: String? = nil, keepError: String? = nil, keepNote: String? = nil,
        converting: Double? = nil
    ) {
        self.keepNote = keepNote
        self.converting = converting
        self.state = state
        self.peers = peers
        self.bytesPerSecond = bytesPerSecond
        self.progress = progress
        self.keeping = keeping
        self.keptPath = keptPath
        self.keepError = keepError
    }

    /// One line about a film being kept, or nil when it isn't: "Keeping: 34% here ·
    /// 5.2 MB/s", "Kept in your Movies folder", or why it couldn't be.
    public var keepLine: String? {
        if let keepError { return "Couldn't keep it: \(keepError)" }
        if let keptPath {
            let kept = "Kept in your Movies folder as “\((keptPath as NSString).lastPathComponent)”"
            return keepNote.map { "\(kept). \($0)" } ?? kept
        }
        guard keeping == true else { return nil }
        if let converting {
            return "Converting it for phones and tablets: \(Int(converting * 100))%. "
                + "It carries on while the app is open, and picks up again when it's reopened."
        }
        if state == "finding" { return "Keeping: finding the film…" }
        let megabytes = String(format: "%.1f", Double(bytesPerSecond) / 1_000_000)
        return "Keeping: \(Int(progress * 100))% here · \(megabytes) MB/s. It carries on while the app is open, and picks up again when it's reopened."
    }

    /// Still on its way: worth asking about again.
    public var isKeeping: Bool { keeping == true && keptPath == nil && keepError == nil }

    /// One line for the player: "Finding the film…", "6 sources · 4.6 MB/s · 9% here".
    public var line: String {
        switch state {
        case "finding": return "Finding the film…"
        case "complete": return "All of the film is here"
        default:
            let megabytes = String(format: "%.1f", Double(bytesPerSecond) / 1_000_000)
            let sources = peers == 1 ? "1 source" : "\(peers) sources"
            return "\(sources) · \(megabytes) MB/s · \(Int(progress * 100))% here"
        }
    }
}

public struct TorrentPlaying: Decodable, Sendable {
    public let infoHash: String
    public let url: String
}

/// A video as a list shows it (the engine's Video): a search result, or one of a
/// channel's.
public struct VideoHit: Decodable, Identifiable, Hashable, Sendable {
    public let videoId: String
    public let title: String
    public let channel: String?
    public let channelId: String?
    public let durationS: Double?
    public let views: Int?
    /// The day it came out ("2026-10-01"). A search gives none.
    public let published: String?
    public let thumbnail: String?

    public var id: String { videoId }

    /// Which channel each of these videos is from (video id → channel id), where known.
    public static func channels(of videos: [VideoHit]) -> [String: String] {
        Dictionary(
            videos.compactMap { video in video.channelId.map { (video.videoId, $0) } },
            uniquingKeysWith: { first, _ in first })
    }

    /// The video as something the app's player can play.
    public var result: SearchResult {
        SearchResult(
            videoId: videoId, title: title, artists: channel.map { [$0] } ?? [],
            durationS: durationS, thumbnail: thumbnail)
    }

    /// About when it came out, in words: "today", "3 days ago", "2 months ago", "4 years
    /// ago". The service gives a list only "3 days ago", never the day, so that's all
    /// that's honestly known. Empty when there's no date.
    public func age(now: Date = Date()) -> String {
        guard let published, let day = Self.day.date(from: published) else { return "" }
        let days = Int(now.timeIntervalSince(day) / 86_400)
        func said(_ count: Int, _ unit: String) -> String {
            "\(count) \(unit)\(count == 1 ? "" : "s") ago"
        }
        switch days {
        case ..<1: return "today"
        case 1..<7: return said(days, "day")
        case 7..<30: return said(days / 7, "week")
        case 30..<365: return said(days / 30, "month")
        default: return said(days / 365, "year")
        }
    }

    private static let day: DateFormatter = {
        let format = DateFormatter()
        format.locale = Locale(identifier: "en_US_POSIX")
        format.timeZone = TimeZone(identifier: "UTC")
        format.dateFormat = "yyyy-MM-dd"
        return format
    }()

    /// How long it is, as a clock: "2:24", "1:02:03". Empty when unknown.
    public var length: String {
        guard let durationS, durationS > 0 else { return "" }
        let whole = Int(durationS)
        let (hours, minutes, seconds) = (whole / 3600, whole % 3600 / 60, whole % 60)
        return hours > 0
            ? String(format: "%d:%02d:%02d", hours, minutes, seconds)
            : String(format: "%d:%02d", minutes, seconds)
    }

    /// How often it's been watched, in round numbers: "1.6M views". Empty when unknown.
    public var viewsLabel: String {
        guard let views else { return "" }
        return views == 1 ? "1 view" : "\(Self.round(views)) views"
    }

    /// 950 → "950", 12,400 → "12K", 1,643,948 → "1.6M", 2,100,000,000 → "2.1B".
    public static func round(_ number: Int) -> String {
        func short(_ value: Double, _ letter: String) -> String {
            let text = value < 10 ? String(format: "%.1f", value) : String(Int(value))
            return (text.hasSuffix(".0") ? String(text.dropLast(2)) : text) + letter
        }
        switch number {
        case ..<1000: return String(number)
        case ..<1_000_000: return short(Double(number) / 1000, "K")
        case ..<1_000_000_000: return short(Double(number) / 1_000_000, "M")
        default: return short(Double(number) / 1_000_000_000, "B")
        }
    }
}

public struct VideosAnswer: Decodable, Sendable {
    public let videos: [VideoHit]
}

/// A channel by what's needed to open its page: its id, and its name and picture to
/// show until the page has loaded. Also how a followed channel is kept.
public struct ChannelRef: Decodable, Identifiable, Hashable, Sendable {
    public let channelId: String
    public let name: String
    public let thumbnail: String?

    public var id: String { channelId }

    public init(channelId: String, name: String, thumbnail: String? = nil) {
        self.channelId = channelId
        self.name = name
        self.thumbnail = thumbnail
    }

    /// A channel from a channels add-on, whose ids read "yt_id:<the channel's id>".
    public init?(item: MediaItem) {
        let id = item.id.hasPrefix("yt_id:") ? String(item.id.dropFirst(6)) : item.id
        guard id.hasPrefix("UC"), id.count == 24 else { return nil }
        self.init(channelId: id, name: item.name, thumbnail: item.poster)
    }
}

public struct ChannelsAnswer: Decodable, Sendable {
    public let channels: [ChannelRef]
}

/// A channel's page (the engine's `channel.videos`).
public struct ChannelPage: Decodable, Sendable {
    public let channelId: String
    public let name: String
    public let followers: Int?
    public let description: String?
    public let thumbnail: String?
    public let followed: Bool
    public let videos: [VideoHit]
}

/// Pictures from the web, asked for at the size they're shown.
public enum WebPictures {
    /// The address of a picture `points` wide on a Retina screen. A channel's picture
    /// comes from a server that makes any size on request (the size is in the address,
    /// "=s800"): asking for the 800-pixel one to fill a 150-point card fetched and drew
    /// seven times the pixels shown, for a hundred cards. Other addresses are left alone.
    public static func sized(_ address: String?, points: Int) -> String? {
        guard let address else { return nil }
        guard address.contains("ggpht.com/") || address.contains("googleusercontent.com/") else {
            return address
        }
        let pixels = max(points * 2, 64)
        guard let found = address.range(of: #"=s\d+"#, options: .regularExpression) else {
            return address
        }
        return address.replacingCharacters(in: found, with: "=s\(pixels)")
    }
}

/// How a list of videos is arranged (Video Finder's menu). The list is the one the
/// search gave, put in another order: nothing more is asked of the service.
public enum VideoSort: String, CaseIterable, Sendable {
    case bestMatch, newest, oldest, mostViews, leastViews, longest, shortest

    public var title: String {
        switch self {
        case .bestMatch: "Best Match"
        case .newest: "Newest"
        case .oldest: "Oldest"
        case .mostViews: "Most Views"
        case .leastViews: "Least Views"
        case .longest: "Longest"
        case .shortest: "Shortest"
        }
    }

    /// The videos in this order. One with nothing to go by (no date, no count) goes
    /// last, and videos that tie stay in the order the search gave them.
    public func arranged(_ videos: [VideoHit]) -> [VideoHit] {
        func by<Value: Comparable>(
            _ value: (VideoHit) -> Value?, descending: Bool
        ) -> [VideoHit] {
            videos.enumerated().sorted { one, other in
                switch (value(one.element), value(other.element)) {
                case let (a?, b?) where a != b: descending ? a > b : a < b
                case (_?, nil): true
                case (nil, _?): false
                default: one.offset < other.offset
                }
            }.map(\.element)
        }
        switch self {
        case .bestMatch: return videos
        case .newest: return by(\.published, descending: true)
        case .oldest: return by(\.published, descending: false)
        case .mostViews: return by(\.views, descending: true)
        case .leastViews: return by(\.views, descending: false)
        case .longest: return by(\.durationS, descending: true)
        case .shortest: return by(\.durationS, descending: false)
        }
    }
}
