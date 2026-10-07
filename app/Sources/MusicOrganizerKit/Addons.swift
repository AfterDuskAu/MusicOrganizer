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
}

public struct AddonsAnswer: Decodable, Sendable {
    public let addons: [Addon]
}

/// A film or a channel as a list shows it (the engine's Item).
public struct MediaItem: Decodable, Identifiable, Hashable, Sendable {
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

        /// The day it came out, as the add-on's timestamp starts ("2023-10-25").
        public var day: String { String((released ?? "").prefix(10)) }
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
    /// to search channels by when the add-on has none of those genres.
    static let wanted: [(name: String, genres: [String], search: String)] = [
        ("Gaming", ["Gaming"], "gaming"),
        ("News", ["News", "News & Politics"], "news"),
        ("Sports", ["Sports"], "sports"),
        ("Learning", ["Learning", "Education", "Science & Education"], "educational"),
        ("Podcasts", ["Podcasts"], "podcast"),
    ]

    /// The sections, in the owner's order: Channels, then the five by name (from the
    /// catalog's genre where it has one, or else searched), then the rest of the
    /// catalog's genres under their own names.
    public static func sections(for genres: [String]) -> [Section] {
        var found = [Section(name: "Channels")]
        var used = Set<String>()
        for (name, names, search) in wanted {
            if let genre = names.first(where: genres.contains) {
                found.append(Section(name: name, genre: genre))
                used.insert(genre)
            } else {
                found.append(Section(name: name, search: search))
            }
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

    public init(
        state: String, peers: Int, bytesPerSecond: Int, progress: Double, keeping: Bool? = nil,
        keptPath: String? = nil, keepError: String? = nil
    ) {
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
            return "Kept in your Movies folder as “\((keptPath as NSString).lastPathComponent)”"
        }
        guard keeping == true else { return nil }
        if state == "finding" { return "Keeping: finding the film…" }
        let megabytes = String(format: "%.1f", Double(bytesPerSecond) / 1_000_000)
        return "Keeping: \(Int(progress * 100))% here · \(megabytes) MB/s. It carries on while the app is open."
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

    /// The video as something the app's player can play.
    public var result: SearchResult {
        SearchResult(
            videoId: videoId, title: title, artists: channel.map { [$0] } ?? [],
            durationS: durationS, thumbnail: thumbnail)
    }

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
