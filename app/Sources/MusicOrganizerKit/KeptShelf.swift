import Foundation

/// What a video file in the Movies folder is, read from where it is and its name, by the
/// engine's own rules (`naming.kept_details`), which are also how the engine names what
/// it keeps:
///
///     a film                    <Title> (<Year>)
///     an episode of a series    Series/<Show>/Season 1/<Show> S01E02 - <Title>
///     an episode of an anime    Anime/<Show>/Season 1/<Show> S01E02 - <Title>
///     an anime that's a film    Anime/<Title> (<Year>)
///
/// Nothing is guessed from a film's name beyond its year: a file loose in the Movies
/// folder (or anywhere in it but Series and Anime) is a film.
public struct KeptFile: Identifiable, Hashable, Sendable {
    public let file: VideoFiles.File
    /// Its folder: "movie", "series" or "anime".
    public let kind: String
    /// For an episode: its show's folder's name. Nil for a film.
    public let show: String?
    public let season: Int?
    public let episode: Int?
    /// A film's name without its year, or an episode's own title ("" when it has none).
    public let title: String
    /// A film's year, from its name.
    public let year: String?

    public var id: URL { file.url }

    public init(_ file: VideoFiles.File, in movies: URL) {
        self.file = file
        let top = movies.standardizedFileURL.pathComponents
        let all = file.url.standardizedFileURL.pathComponents
        let parts = all.starts(with: top) && all.count > top.count ? Array(all[top.count...]) : [all.last ?? ""]
        var kind = "movie"
        if parts.count > 1 {
            switch parts[0].lowercased() {
            case "series": kind = "series"
            case "anime": kind = "anime"
            default: break
            }
        }
        self.kind = kind
        let stem = (parts[parts.count - 1] as NSString).deletingPathExtension
        guard kind != "movie", parts.count >= 3 else {
            // A film: "<Title> (<Year>)".
            (show, season, episode) = (nil, nil, nil)
            if let dated = Self.firstMatch(Self.dated, in: stem) {
                (title, year) = (dated[1], dated[2])
            } else {
                (title, year) = (stem, nil)
            }
            return
        }
        show = parts[1]
        year = nil
        var season: Int?
        var episode: Int?
        var rest = ""
        if let numbered = Self.firstMatch(Self.seasonEpisode, in: stem) {
            (season, episode) = (Int(numbered[1]), Int(numbered[2]))
            rest = numbered.after
        } else {
            for folder in parts[2..<(parts.count - 1)] {
                let name = folder.trimmingCharacters(in: .whitespaces)
                if name.lowercased() == "specials" {
                    season = 0
                } else if let found = Self.firstMatch(Self.seasonFolder, in: name), found.whole == name {
                    season = Int(found[1])
                }
            }
            if let numbered = Self.firstMatch(Self.episodeOnly, in: stem) {
                episode = Int(numbered[1])
                rest = numbered.after
            } else if stem.lowercased().hasPrefix(parts[1].lowercased() + " - ") {
                rest = String(stem.dropFirst(parts[1].count))
            }
        }
        (self.season, self.episode) = (season, episode)
        title = rest.hasPrefix(" - ") ? String(rest.dropFirst(3)).trimmingCharacters(in: .whitespaces) : ""
    }

    /// Which card it's on (`KeptShelf.Card.key`): its show, or a film by its name, so a
    /// film's converted copy beside it is on the same card.
    public var key: String {
        if let show { return "show:" + show.lowercased() }
        return "film:" + (file.url.deletingPathExtension().lastPathComponent).lowercased()
    }

    /// Its season for finding it among a series' episodes: one whose season isn't known
    /// ("<Show> E02") is taken to be in the first.
    public var seasonOrFirst: Int? { episode == nil ? nil : season ?? 1 }

    // "S01E02", wherever it is in a name: "Show S01E02 - Title", "show.s1e2.1080p".
    static let seasonEpisode = try! NSRegularExpression(
        pattern: #"(?<![A-Za-z0-9])S(\d{1,4})\s?E(\d{1,4})(?!\d)"#, options: .caseInsensitive)
    // "E02": an episode whose season isn't known.
    static let episodeOnly = try! NSRegularExpression(
        pattern: #"(?<![A-Za-z0-9])E(\d{1,4})(?![A-Za-z0-9])"#, options: .caseInsensitive)
    static let seasonFolder = try! NSRegularExpression(pattern: #"season\s*(\d{1,4})"#, options: .caseInsensitive)
    static let dated = try! NSRegularExpression(pattern: #"^(.+?) \((\d{4})\)$"#)

    struct Found {
        let groups: [String]
        let whole: String
        let after: String
        subscript(_ index: Int) -> String { groups[index] }
    }

    static func firstMatch(_ pattern: NSRegularExpression, in text: String) -> Found? {
        let all = NSRange(text.startIndex..., in: text)
        guard let match = pattern.firstMatch(in: text, range: all) else { return nil }
        let groups = (0..<match.numberOfRanges).map { index -> String in
            Range(match.range(at: index), in: text).map { String(text[$0]) } ?? ""
        }
        let end = Range(match.range, in: text)?.upperBound ?? text.endIndex
        return Found(groups: groups, whole: groups[0], after: String(text[end...]))
    }
}

/// Videos → Movies/Series (the owner, 2026-11-05, as Bear Media II's Downloaded page of
/// 2026-11-04: "Movies first, then Series"): what's in the Movies folder, a card a film or
/// a series. A film's card plays it; a series' card opens its page, which marks the
/// episodes that are here. Anime comes after them, a film of it played and a series of it
/// opened, the same way. The 18+ folder and the videos folder have pages of their own.
public struct KeptShelf: Equatable, Sendable {
    /// One card: a film with its file (and any copy of it), or a series with every
    /// episode of it that's here.
    public struct Card: Identifiable, Hashable, Sendable {
        /// Its show's folder, or a film's name (`KeptFile.key`).
        public let key: String
        /// Its folder: "movie", "series" or "anime".
        public let kind: String
        /// A film's file, or a series' episodes by season and number (specials last).
        public let files: [KeptFile]
        /// The film or series it is, as a plugin knows it, when that's known: noted when
        /// it was downloaded, or found by its name (`KeptTitles`).
        public let item: MediaItem?

        public var id: String { key }
        /// Opened on its page (or, not known, as a list of its files) rather than played.
        public var isShow: Bool { files.first?.show != nil }

        /// What it's called: the plugin's name for it, or its folder's or file's ("_" is
        /// how a name's ":" is written on the disk).
        public var name: String {
            if let item { return item.name }
            let written = files.first.map { $0.show ?? $0.title } ?? key
            return written.replacingOccurrences(of: "_ ", with: ": ")
        }

        /// A film's year, from its file's name.
        public var year: String? { isShow ? nil : files.first?.year }

        /// The words a plugin is asked for it by: its name with what the disk couldn't
        /// hold taken out.
        public var searchWords: String {
            let written = files.first.map { $0.show ?? $0.title } ?? ""
            return written.replacingOccurrences(of: "_", with: " ")
                .split(separator: " ").joined(separator: " ")
        }

        /// What it's looked up as: a series, or a movie.
        public var type: String { isShow ? "series" : "movie" }

        /// "3 episodes", or a film's size.
        public var line: String {
            if isShow { return files.count == 1 ? "1 episode" : "\(files.count) episodes" }
            return files.first.map { ByteCountFormatter.string(fromByteCount: $0.file.bytes, countStyle: .file) } ?? ""
        }

        /// When its newest file came.
        public var added: Date? { files.compactMap(\.file.added).max() }

        /// The episode here of this season and number, if it is.
        public func file(season: Int?, episode: Int?) -> KeptFile? {
            guard let episode else { return nil }
            let season = season ?? 1
            return files.first { $0.episode == episode && $0.seasonOrFirst == season }
        }

        /// How many episodes of each season are here.
        public var perSeason: [Int: Int] {
            var counts: [Int: Int] = [:]
            for file in files { if let season = file.seasonOrFirst { counts[season, default: 0] += 1 } }
            return counts
        }

        /// The first season with an episode here (specials last): where its page opens.
        public var firstSeason: Int? {
            let here = Set(perSeason.keys)
            return here.filter { $0 > 0 }.min() ?? (here.contains(0) ? 0 : nil)
        }

        /// A series' page when its plugins can't be reached: what's kept of it on this Mac
        /// (`kept`), with only the episodes that are here. Nil for a film, or a series
        /// whose plugin title isn't known.
        public func offline(_ kept: KeptTitles.Title?) -> MediaDetails? {
            guard isShow, let item else { return nil }
            let known = kept?.episodes ?? []
            let videos = files.compactMap { file -> MediaDetails.Video? in
                guard let number = file.episode else { return nil }
                let season = file.seasonOrFirst ?? 1
                if let video = known.first(where: { $0.episode == number && ($0.season ?? 1) == season }) {
                    return video
                }
                return MediaDetails.Video(
                    id: "\(item.id):\(season):\(number)", title: file.title, season: season, episode: number)
            }
            return MediaDetails(
                id: item.id, type: item.type, name: item.name, poster: item.poster, year: item.year,
                rating: item.rating, genres: kept?.genres ?? item.genres, description: kept?.summary,
                cast: kept?.cast ?? [], videos: videos)
        }

        public init(key: String, kind: String, files: [KeptFile], item: MediaItem?) {
            (self.key, self.kind, self.files, self.item) = (key, kind, files, item)
        }
    }

    public let movies: [Card]
    public let series: [Card]
    public let anime: [Card]

    public var isEmpty: Bool { movies.isEmpty && series.isEmpty && anime.isEmpty }
    /// Every card, movies then series then anime.
    public var all: [Card] { movies + series + anime }
    /// How many files are on it.
    public var fileCount: Int { all.reduce(0) { $0 + $1.files.count } }

    public init() { (movies, series, anime) = ([], [], []) }

    /// The files, a card each film or show, the newest first; with each one's plugin
    /// title where `titles` knows it.
    public init(_ files: [KeptFile], titles: KeptTitles) {
        var order: [String] = []
        var byKey: [String: [KeptFile]] = [:]
        for file in files {
            if byKey[file.key] == nil { order.append(file.key) }
            byKey[file.key, default: []].append(file)
        }
        let cards = order.compactMap { key -> Card? in
            guard let here = byKey[key], let first = here.first else { return nil }
            // A film and its copy: the one with the plainest name first. A show: by season
            // and number, the specials last.
            let sorted = here.sorted(by: Self.inOrder)
            // Kind by the first of its files: a show is in one folder.
            return Card(key: key, kind: first.kind, files: sorted, item: titles.item(key))
        }
        .sorted { ($0.added ?? .distantPast, $1.name) > ($1.added ?? .distantPast, $0.name) }
        // An anime that's a film is in Anime's folder, so it's with the anime. A file
        // left loose in Series (no show folder) is with the series, played as a film.
        movies = cards.filter { $0.kind == "movie" }
        series = cards.filter { $0.kind == "series" }
        anime = cards.filter { $0.kind == "anime" }
    }

    /// What's here of a film or a series, by its plugin id.
    public func card(for item: MediaItem) -> Card? {
        all.first { $0.item?.id == item.id }
    }

    /// Episodes by season and number (specials last); a film's files by name, the
    /// shortest (the original, not "(2)") first.
    static func inOrder(_ first: KeptFile, _ second: KeptFile) -> Bool {
        func key(_ file: KeptFile) -> (Int, Int, Int, String) {
            let season = file.seasonOrFirst ?? 0
            return (
                season == 0 ? Int.max : season, file.episode ?? 0, file.file.name.count,
                file.file.url.lastPathComponent
            )
        }
        return key(first) < key(second)
    }
}

/// Which plugin title each film and series in the Movies folder is, remembered by the app
/// on this Mac (not in the library): noted when it's downloaded from its page, or found
/// once by its name among the plugins (`match`). With it, a little of each title's details
/// (its summary, cast, genres, and what its plugin said of the episodes that are here), so
/// its page shows them when the plugins can't be reached.
public struct KeptTitles: Codable, Equatable, Sendable {
    public struct Title: Codable, Equatable, Sendable {
        public var item: MediaItem
        public var summary: String?
        public var cast: [String]?
        public var genres: [String]?
        /// The episodes on this Mac, as its plugin last told of them.
        public var episodes: [MediaDetails.Video]?

        public init(item: MediaItem) { self.item = item }
    }

    public private(set) var titles: [String: Title] = [:]
    /// Looked for by name and not found: when, so it's asked for again only after a week.
    public private(set) var missed: [String: Date] = [:]

    public static let key = "keptTitles"
    /// How long a title that wasn't found waits before it's looked for again.
    public static let missWait: TimeInterval = 7 * 24 * 3600

    public init() {}

    public func item(_ key: String) -> MediaItem? { titles[key]?.item }
    public func title(_ key: String) -> Title? { titles[key] }

    /// This card is `item` (downloaded from its page, or found by its name). What was kept
    /// of it stays when it's the same title.
    public mutating func note(_ item: MediaItem, for key: String) {
        if titles[key]?.item.id == item.id {
            titles[key]?.item = item
        } else {
            titles[key] = Title(item: item)
        }
        missed[key] = nil
    }

    /// What its plugin said of it just now (its page was opened), kept for when the
    /// plugins can't be reached: true when that changed what's kept.
    @discardableResult
    public mutating func keep(_ details: MediaDetails, here card: KeptShelf.Card) -> Bool {
        guard var title = titles[card.key], title.item.id == details.id else { return false }
        let before = title
        title.summary = details.description
        title.cast = details.cast.isEmpty ? nil : details.cast
        title.genres = details.genres.isEmpty ? nil : details.genres
        if card.isShow {
            let here = details.videos.filter { card.file(season: $0.season, episode: $0.episode) != nil }
            title.episodes = here.isEmpty ? nil : here
        }
        titles[card.key] = title
        return title != before
    }

    /// Looked for by name, and none of the plugins has it.
    public mutating func notFound(_ key: String, at now: Date = Date()) {
        missed[key] = now
    }

    /// Whether to look for this card's title by its name: not known, and not looked for
    /// in vain in the last week.
    public func shouldLookUp(_ key: String, now: Date = Date()) -> Bool {
        guard titles[key] == nil else { return false }
        guard let when = missed[key] else { return true }
        return now.timeIntervalSince(when) > Self.missWait
    }

    /// Of what the plugins found for a card's name, the one it is: a series for a show, a
    /// movie for a film, with the same name once case, accents and what the disk can't
    /// hold (":" is "_") are set aside, and for a film the year in its name (or one either
    /// side). Nil when none is.
    public static func match(_ found: [MediaItem], for card: KeptShelf.Card) -> MediaItem? {
        let wanted = plain(card.searchWords)
        guard !wanted.isEmpty else { return nil }
        let year = card.year.flatMap { Int($0) }
        return found.first { item in
            guard item.type == card.type, plain(item.name) == wanted else { return false }
            guard let year else { return true }
            guard let made = item.year.flatMap({ Int($0.prefix(4)) }) else { return false }
            return abs(made - year) <= 1
        }
    }

    /// A name with only its letters and digits, without case or accents.
    static func plain(_ name: String) -> String {
        name.folding(options: [.caseInsensitive, .diacriticInsensitive, .widthInsensitive], locale: nil)
            .filter { $0.isLetter || $0.isNumber }
    }

    public static func load(from defaults: UserDefaults = .standard) -> KeptTitles {
        guard let data = defaults.data(forKey: key),
            let kept = try? JSONDecoder().decode(KeptTitles.self, from: data)
        else { return KeptTitles() }
        return kept
    }

    public func save(in defaults: UserDefaults = .standard) {
        if let data = try? JSONEncoder().encode(self) { defaults.set(data, forKey: Self.key) }
    }
}
