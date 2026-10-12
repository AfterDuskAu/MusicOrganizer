import Foundation

/// A profile's export and import (2026-10-12, the owner's yes: Settings → Profile →
/// Export and Import). The engine copies the library and writes the export (`transfer`
/// there); here are the answers it gives, in the app's shapes, and what the app hands
/// over of its own settings to go with it.
public enum ProfileTransfer {
    /// What can be ticked, in the order the page lists it.
    public static let parts = ["music", "videos", "movies", "records", "settings"]

    public static func title(_ part: String) -> String {
        switch part {
        case "music": "Music"
        case "videos": "Videos"
        case "movies": "Movies and series"
        case "records": "Playlists, favourites and listening"
        default: "Settings and plugins"
        }
    }
}

/// What a profile has to export (the engine's `profile.contents`).
public struct ProfileContents: Decodable, Sendable {
    public struct Part: Decodable, Sendable {
        public let files: Int?
        public let bytes: Int?
        public let favourites: Int?
        public let playlists: Int?
        public let addons: Int?
        public let addonsLeftOut: [String]?
    }

    public let parts: [String: Part]

    /// What a part amounts to, beside its tick-box: "1,013 files · 4.2 GB".
    public func about(_ part: String) -> String {
        guard let found = parts[part] else { return "" }
        if let files = found.files {
            guard files > 0 else { return "nothing yet" }
            let size = ByteCountFormatter.string(fromByteCount: Int64(found.bytes ?? 0), countStyle: .file)
            return "\(files.formatted()) file\(files == 1 ? "" : "s") · \(size)"
        }
        if part == "records" {
            let hearts = found.favourites ?? 0
            let lists = found.playlists ?? 0
            return "\(lists.formatted()) playlist\(lists == 1 ? "" : "s"), "
                + "\(hearts.formatted()) favourite\(hearts == 1 ? "" : "s"), and what's been played"
        }
        let plugins = found.addons ?? 0
        return "your choices in Settings, and \(plugins) plugin\(plugins == 1 ? "" : "s")"
    }
}

/// An export or an import under way, and how the last one ended (`profile.transfer`).
public struct TransferStatus: Decodable, Sendable {
    public struct Working: Decodable, Sendable {
        public let kind: String
        public let done: Int
        public let total: Int
        public let name: String
    }

    public struct Last: Decodable, Sendable {
        public let kind: String
        public let ok: Bool
        public let message: String?
        public let summary: TransferSummary?
    }

    public let working: Working?
    public let last: Last?
}

/// What an export made, or what an import brought (or would bring).
public struct TransferSummary: Decodable, Sendable {
    public struct Songs: Decodable, Sendable {
        public let brought: Int
        public let already: Int
        public let beside: Int
    }

    public struct Films: Decodable, Sendable {
        public let brought: Int
        public let already: Int
    }

    public let folder: String?
    public let profile: String?
    public let made: String?
    public let leftOut: [String]?
    public let songs: Songs?
    public let films: Films?
    public let records: String?
    public let addons: Int?
    public let batchId: String?

    /// What an import brings, or brought, in plain words, a line for each kind.
    public func lines(done: Bool) -> [String] {
        var said: [String] = []
        let verb = done ? "came in" : "would come in"
        if let songs {
            var line = "\(songs.brought.formatted()) song\(songs.brought == 1 ? "" : "s") and videos \(verb)"
            if songs.already > 0 { line += "; \(songs.already.formatted()) are in this library already" }
            said.append(line + ".")
        }
        if let films, films.brought + films.already > 0 {
            var line = "\(films.brought.formatted()) movie\(films.brought == 1 ? "" : "s") and kept videos \(verb)"
            if films.already > 0 { line += "; \(films.already.formatted()) are here already" }
            said.append(line + ".")
        }
        switch records {
        case "taken":
            said.append("Its playlists, favourites and listening \(done ? "came with it" : "would come with it").")
        case "kept_own":
            said.append(
                "This profile has playlists, favourites or plays of its own, so the export's "
                    + "\(done ? "were" : "would be") left out: nothing of yours is written over. "
                    + "To have them, import into a new profile.")
        default: break
        }
        if let addons, addons > 0 {
            said.append("\(addons) plugin\(addons == 1 ? "" : "s") \(done ? "were added" : "would be added").")
        }
        return said
    }
}

/// The app's own settings and records that go with an export, and come back with an
/// import: only the ones named here. **Nothing that would start something by itself on
/// another computer, and nothing that could hold a key:** not the switch that shares the
/// library with devices, not the list of profiles and their folders, not films being
/// downloaded, and a way a film was played (an address, which a plugin's own set-up may
/// have written a key into) is taken out of what's been watched.
public enum SettingsCarrier {
    /// Choices made in Settings and on the pages.
    public static let settings = [
        "appLook", "pageChangesLastMinutes", "customVisualizer", "useCustomVisualizer",
        "customVisualizerQuality", "playSongWhileVideoLoads", "alwaysBestVideo", "playOpensVisualizer",
        "visualizerLyrics", "fullScreenLyrics", "alwaysBestVideoDownload", "convertKeptFilms",
        "torrentUploadSpeed", NextEpisode.key, MusicSearchSource.key, "musicFinderTab", "sidebarHidden",
        HomeLayout.key, "songColumnOrder",
    ]
    /// What the app remembers of what's been watched and liked.
    public static let records = [WatchHistory.key, MediaFavourites.key, FilmPositions.key, ChannelPictures.key]

    public static var carried: Set<String> { Set(settings + records) }

    /// What's handed to the engine for an export: each carried setting that's been set,
    /// in a shape that can be written as JSON (a block of data as base64).
    public static func pack(from defaults: UserDefaults = .standard) -> [String: Any] {
        var values: [String: Any] = [:]
        for key in carried.sorted() {
            guard let value = defaults.object(forKey: key) else { continue }
            if let data = value as? Data {
                values[key] = ["data": cleaned(key, data).base64EncodedString()]
            } else if JSONSerialization.isValidJSONObject([value]) {
                values[key] = ["value": value]
            }
        }
        return ["format": 1, "values": values]
    }

    /// Put back what an export held. Only the carried settings are ever taken, whatever
    /// the export says. Returns how many were set.
    @discardableResult
    public static func unpack(_ packed: [String: Any], into defaults: UserDefaults = .standard) -> Int {
        guard let values = packed["values"] as? [String: Any] else { return 0 }
        var set = 0
        for (key, held) in values where carried.contains(key) {
            guard let held = held as? [String: Any] else { continue }
            if let text = held["data"] as? String, let data = Data(base64Encoded: text) {
                defaults.set(cleaned(key, data), forKey: key)
                set += 1
            } else if let value = held["value"] {
                defaults.set(value, forKey: key)
                set += 1
            }
        }
        return set
    }

    /// A record without what mustn't travel: what's been watched without the ways it
    /// was played, and where films were left without the ones known by a web address.
    static func cleaned(_ key: String, _ data: Data) -> Data {
        if key == WatchHistory.key, var entries = try? JSONDecoder().decode([WatchHistory.Entry].self, from: data) {
            for index in entries.indices {
                (entries[index].stream, entries[index].playName) = (nil, nil)
                (entries[index].episodeId, entries[index].source) = (nil, nil)
            }
            return (try? JSONEncoder().encode(entries)) ?? Data()
        }
        if key == FilmPositions.key, let places = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            let kept = places.filter { !$0.key.lowercased().hasPrefix("http") }
            return (try? JSONSerialization.data(withJSONObject: kept)) ?? Data()
        }
        return data
    }
}
