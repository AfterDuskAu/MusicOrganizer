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
/// add-on's genres, under the owner's name for it; one the add-on has no genre for
/// isn't offered until there's somewhere to read it from.
public enum VideoExplore {
    public struct Section: Identifiable, Hashable, Sendable {
        public let name: String
        /// The add-on's genre; nil for every channel.
        public let genre: String?
        public var id: String { name }
    }

    /// The owner's names, and the genre each is under in a channel add-on.
    static let wanted: [(name: String, genres: [String])] = [
        ("Gaming", ["Gaming"]),
        ("News", ["News", "News & Politics"]),
        ("Sports", ["Sports"]),
        ("Learning", ["Learning", "Education", "Science & Education"]),
        ("Podcasts", ["Podcasts"]),
    ]

    /// The sections a catalog can fill, Channels first, then the rest of its genres
    /// under their own names.
    public static func sections(for genres: [String]) -> [Section] {
        var found = [Section(name: "Channels", genre: nil)]
        var used = Set<String>()
        for (name, names) in wanted {
            if let genre = names.first(where: genres.contains) {
                found.append(Section(name: name, genre: genre))
                used.insert(genre)
            }
        }
        found += genres.filter { !used.contains($0) }.map { Section(name: $0, genre: $0) }
        return found
    }

    /// The owner's sections this catalog has nothing for.
    public static func missing(from genres: [String]) -> [String] {
        wanted.filter { !$0.genres.contains(where: genres.contains) }.map(\.name)
    }
}

/// How a film playing from a torrent is getting on (the engine's `torrent.status`).
public struct TorrentStatus: Decodable, Equatable, Sendable {
    public let state: String
    public let peers: Int
    public let bytesPerSecond: Int
    public let progress: Double

    public init(state: String, peers: Int, bytesPerSecond: Int, progress: Double) {
        self.state = state
        self.peers = peers
        self.bytesPerSecond = bytesPerSecond
        self.progress = progress
    }

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
