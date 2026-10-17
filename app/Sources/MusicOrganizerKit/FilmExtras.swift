import Foundation

/// One of a film's sound or subtitle tracks, as the film player's menus show it.
public struct FilmTrack: Identifiable, Hashable, Sendable {
    public enum Kind: String, Sendable { case sound = "audio", subtitles = "sub" }

    /// The player's own number for it, among tracks of its kind.
    public let id: Int
    public let kind: Kind
    public let label: String
    public let selected: Bool

    public init(id: Int, kind: Kind, title: String?, language: String?, selected: Bool) {
        self.id = id
        self.kind = kind
        self.label = Self.label(number: id, title: title, language: language)
        self.selected = selected
    }

    /// What a menu calls a track: its language in words and what the film says of it
    /// ("English · Commentary"), or "Track 2" when the film says nothing.
    public static func label(number: Int, title: String?, language: String?) -> String {
        let said = (title ?? "").trimmingCharacters(in: .whitespaces)
        let code = (language ?? "").trimmingCharacters(in: .whitespaces)
        let known = code.isEmpty || code == "und" ? nil : Locale.current.localizedString(forLanguageCode: code)
        let tongue = known ?? (code.isEmpty || code == "und" ? "" : code)
        let parts = [tongue, said.caseInsensitiveCompare(tongue) == .orderedSame ? "" : said]
            .filter { !$0.isEmpty }
        return parts.isEmpty ? "Track \(number)" : parts.joined(separator: " · ")
    }

    /// Whether a film's tracks of one kind are worth a menu (the owner, 2026-10-12:
    /// "only if there are subtitles or audio tracks to pick from"): two sound tracks or
    /// more, since one can't be switched for anything; any subtitles at all, since they
    /// can be switched on and off.
    public static func offersChoice(_ tracks: [FilmTrack], of kind: Kind) -> Bool {
        let ofKind = tracks.filter { $0.kind == kind }
        return kind == .sound ? ofKind.count > 1 : !ofKind.isEmpty
    }

    /// What the menu's own button says: the track that's chosen, so it's seen without
    /// opening the menu ("Sound: English", "Subtitles: Off").
    public static func chosen(_ tracks: [FilmTrack], of kind: Kind) -> String {
        let now = tracks.first { $0.kind == kind && $0.selected }
        return (kind == .sound ? "Sound: " : "Subtitles: ") + (now?.label ?? "Off")
    }
}

/// Where each film was watched up to, so it opens there the next time (the owner,
/// 2026-10-08: closed at 0:32, it opens at 0:32 a week later). Kept by the app on this
/// Mac, by the film's file or torrent; nothing of it is in the library.
public struct FilmPositions: Equatable, Sendable {
    public struct Place: Codable, Equatable, Sendable {
        public let seconds: Double
        public let at: Date
    }

    /// Films remembered at most: the one watched longest ago is forgotten first.
    public static let most = 300
    /// Nearer the start than this, a film just starts again.
    public static let startS = 20.0
    /// Nearer the end than this (the credits), it's finished and starts again.
    public static let endS = 90.0

    public private(set) var places: [String: Place]

    public init(_ places: [String: Place] = [:]) { self.places = places }

    /// Where to open a film: nil to start at the beginning.
    public func place(of key: String) -> Double? { places[key]?.seconds }

    /// The film was at `seconds` of `length` when it was last looked at. One that's
    /// hardly begun, or has been watched (in its last minute and a half, or past nine
    /// tenths of it: `Finished.mark`), is forgotten, and starts again next time.
    public mutating func watched(_ key: String, to seconds: Double, of length: Double, now: Date = Date()) {
        guard length > 0 else { return }
        if seconds < Self.startS || seconds > length - Self.endS || seconds >= length * Finished.mark {
            places[key] = nil
            return
        }
        places[key] = Place(seconds: seconds, at: now)
        while places.count > Self.most, let oldest = places.min(by: { $0.value.at < $1.value.at }) {
            places[oldest.key] = nil
        }
    }

    /// A file the owner moved to another folder (2026-10-17, its right-click Move To):
    /// where it was left goes with it. A file is remembered by its path.
    public mutating func moved(from old: String, to new: String) {
        guard old != new, let place = places.removeValue(forKey: old) else { return }
        places[new] = place
    }

    // MARK: kept in the app's settings

    public static let key = "filmPositions"

    public static func saved(in defaults: UserDefaults = .standard) -> FilmPositions {
        guard let data = defaults.data(forKey: key),
            let places = try? JSONDecoder().decode([String: Place].self, from: data)
        else { return FilmPositions() }
        return FilmPositions(places)
    }

    public func save(in defaults: UserDefaults = .standard) {
        if let data = try? JSONEncoder().encode(places) { defaults.set(data, forKey: Self.key) }
    }
}

/// The films the owner asked to keep that aren't in the Movies folder yet, remembered
/// by the app on this Mac (not in the library), so a keep that was cut off when the app
/// closed carries on when it's opened again. One is forgotten when its film has been
/// kept, couldn't be, or the owner stops it. A keep is of a torrent, or (2026-10-17) of
/// a plugin's plain web address (`url`), which the engine downloads (`stream.keep`), or
/// (the same day) of a video on a web page, downloaded by yt-dlp (`page.keep`).
public struct PendingKeeps: Codable, Equatable, Sendable {
    public struct Keep: Codable, Equatable, Sendable, Identifiable {
        /// The torrent's id; empty for a web address's keep.
        public let infoHash: String
        /// The web address a plugin gave (a way to play of kind "url"), for a keep that
        /// isn't a torrent's (2026-10-17). Nil in every keep remembered before then.
        public let url: String?
        /// How big the plugin said the film is, if it did: the engine looks for room by it
        /// when the address doesn't say.
        public let size: Int?
        public let trackers: [String]
        public let title: String
        public let year: String?
        public let fileIndex: Int?
        public let convert: Bool
        /// Kept from the Finder for adults: it isn't listed with the other downloads, but
        /// on the 18+ page, and it's kept in the Movies folder's 18+ folder (2026-10-17).
        /// (Nil in a keep remembered before this was noted.)
        public let apart: Bool?
        /// What it is, which decides its folder in the Movies folder: "movie", "series",
        /// "anime" or (2026-10-17) "adult". Nil (a keep remembered before there were
        /// folders): a movie.
        public let kind: String?
        /// For an episode: the series or anime it's part of, and where it comes in it,
        /// as far as the add-on said. `title` is then the episode's own, and may be empty.
        public let show: String?
        public let season: Int?
        public let episode: Int?
        /// A video from a web page (the URL page, 2026-10-17): its place on the page, if
        /// it has no address of its own, and the quality chosen. `url` is the page's.
        public let item: Int?
        public let quality: String?
        /// What it goes by: its address, or its torrent's id; a page's video by its page,
        /// its place there and its quality.
        public var id: String {
            if let quality, let url { return "\(url)#\(item ?? 0)#\(quality)" }
            return url ?? infoHash
        }
        /// Downloaded from a web page by yt-dlp.
        public var isPage: Bool { quality != nil }
        public var isApart: Bool { apart == true }
        /// Downloaded from a web address, not fetched from a torrent.
        public var isAddress: Bool { url != nil }

        /// What it's kept as: "adult" for one from the Finder for adults, whatever kind
        /// was remembered with it (one asked for before 2026-10-17 said "movie", and would
        /// have gone loose in the Movies folder).
        public var keptKind: String? { isApart ? "adult" : kind }

        /// What it's called where keeps are listed (Downloads): a film's title, or for
        /// an episode its show's name and number, "East of Eden S01E02".
        public var name: String {
            guard let show else { return title }
            if let season, let episode { return String(format: "%@ S%02dE%02d", show, season, episode) }
            if let episode { return String(format: "%@ E%02d", show, episode) }
            return title.isEmpty ? show : "\(show) - \(title)"
        }

        /// What it is, in a word, where keeps are listed: "Movie", "Series", "Anime",
        /// "18+" or (a video from a page saved there) "Video".
        public var kindName: String {
            switch keptKind {
            case "series": "Series"
            case "anime": "Anime"
            case "adult": "18+"
            case "video": "Video"
            default: "Movie"
            }
        }

        /// The folder it's going to, as Downloads says it: "Videos" or "Movies".
        public var placeName: String { keptKind == "video" ? "Videos" : "Movies" }

        public init(
            infoHash: String, trackers: [String], title: String, year: String? = nil,
            fileIndex: Int? = nil, convert: Bool = true, apart: Bool = false,
            kind: String? = nil, show: String? = nil, season: Int? = nil, episode: Int? = nil,
            url: String? = nil, size: Int? = nil, item: Int? = nil, quality: String? = nil
        ) {
            self.apart = apart ? true : nil
            self.item = item
            self.quality = quality
            self.infoHash = url == nil ? infoHash.lowercased() : ""
            self.url = url
            self.size = size
            self.trackers = trackers
            self.title = title
            self.year = year
            self.fileIndex = fileIndex
            self.convert = convert
            self.kind = kind
            self.show = show
            self.season = season
            self.episode = episode
        }

        /// A film from its page, or the episode chosen there (`part`). A film is kept
        /// under its own name and year; an episode under its show's name, with its
        /// season and episode. Anime is what was found in a list of anime; anything from
        /// the Finder for adults (`apart`) is "adult", kept in the 18+ folder.
        public init(
            infoHash: String, trackers: [String], fileIndex: Int? = nil, convert: Bool = true,
            apart: Bool = false, of item: MediaItem, part: MediaDetails.Video? = nil,
            year: String? = nil, url: String? = nil, size: Int? = nil
        ) {
            let kind = apart ? "adult" : item.keptKind
            guard let part, kind != "movie" else {
                self.init(
                    infoHash: infoHash, trackers: trackers,
                    title: part?.name(in: item.name) ?? item.name,
                    year: part == nil ? year.map { String($0.prefix(4)) } : nil,
                    fileIndex: fileIndex, convert: convert, apart: apart, kind: kind,
                    url: url, size: size)
                return
            }
            self.init(
                infoHash: infoHash, trackers: trackers, title: part.title, fileIndex: fileIndex,
                convert: convert, apart: apart, kind: kind, show: item.name,
                season: part.season, episode: part.episode, url: url, size: size)
        }

        /// A way to play's keep, from its page: a torrent's, or a web address's. Nil for
        /// one that can't be downloaded (a video by its id).
        public init?(
            _ stream: MediaStream, convert: Bool = true, apart: Bool = false, of item: MediaItem,
            part: MediaDetails.Video? = nil, year: String? = nil
        ) {
            if stream.kind == "url", let url = stream.url {
                self.init(
                    infoHash: "", trackers: [], convert: convert, apart: apart, of: item,
                    part: part, year: year, url: url, size: stream.size)
            } else if stream.kind == "torrent", let hash = stream.infoHash {
                self.init(
                    infoHash: hash, trackers: stream.trackers, fileIndex: stream.fileIndex,
                    convert: convert, apart: apart, of: item, part: part, year: year)
            } else {
                return nil
            }
        }

        /// A video chosen on the URL page, to be saved in `place`.
        public init(
            page video: PageVideo, quality: PageQuality, title: String, place: KeptPlace,
            convert: Bool = true
        ) {
            self.init(
                infoHash: "", trackers: [], title: title, convert: convert,
                apart: place == .adult, kind: place.rawValue, url: video.url,
                size: quality.size, item: video.item, quality: quality.id)
        }

        /// What the engine is asked to keep it: `torrent.keep`, or `stream.keep` for a web
        /// address, or `page.keep` for a page's video (`keepMethod`).
        public var asked: [String: Any] {
            var asked: [String: Any] = ["title": title, "convert": convert]
            if let quality, let url {
                asked["url"] = url
                asked["quality"] = quality
                if let item { asked["item"] = item }
            } else if let url {
                asked["url"] = url
                if let size { asked["size"] = size }
            } else {
                asked["info_hash"] = infoHash
                asked["trackers"] = trackers
                if let fileIndex { asked["file_index"] = fileIndex }
            }
            if let year { asked["year"] = year }
            if let kind = keptKind { asked["kind"] = kind }
            if let show { asked["show"] = show }
            if let season { asked["season"] = season }
            if let episode { asked["episode"] = episode }
            return asked
        }

        /// How the engine is asked to keep it, how it's going, and to stop.
        public var keepMethod: String {
            isPage ? "page.keep" : isAddress ? "stream.keep" : "torrent.keep"
        }
        public var statusMethod: String { isAddress ? "stream.status" : "torrent.status" }
        public var stopMethod: String { isAddress ? "stream.stop" : "torrent.stop_keeping" }
        /// What says which it is, for those.
        public var which: [String: Any] {
            if let quality, let url {
                var which: [String: Any] = ["url": url, "quality": quality]
                if let item { which["item"] = item }
                return which
            }
            return url.map { ["url": $0] } ?? ["info_hash": infoHash]
        }
    }

    public private(set) var keeps: [Keep] = []
    public static let key = "filmsBeingKept"

    public init() {}

    public mutating func add(_ keep: Keep) {
        keeps.removeAll { $0.id == keep.id }
        keeps.append(keep)
    }

    /// Forget a keep, by what it goes by (`Keep.id`): a web address, or a torrent's id in
    /// any case.
    public mutating func remove(_ id: String) {
        keeps.removeAll { $0.id == id || (!$0.isAddress && $0.infoHash == id.lowercased()) }
    }

    public static func load(from defaults: UserDefaults = .standard) -> PendingKeeps {
        guard let data = defaults.data(forKey: key),
            let found = try? JSONDecoder().decode(PendingKeeps.self, from: data)
        else { return PendingKeeps() }
        return found
    }

    public func save(to defaults: UserDefaults = .standard) {
        if let data = try? JSONEncoder().encode(self) { defaults.set(data, forKey: Self.key) }
    }
}
