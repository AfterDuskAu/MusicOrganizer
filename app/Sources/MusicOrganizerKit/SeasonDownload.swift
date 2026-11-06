import Foundation

/// Download Season on a series' page (2026-11-06, the owner's yes to building it as the
/// tablet app has it; CLAUDE.md's Videos → Movies/Series had said it wasn't built): every
/// episode of the season showing that's out and isn't on this Mac or on its way, each kept
/// as a Download from its page is. The owner chooses a quality (and with it the plugin)
/// once, from the ways to play the first of them; each episode is then chosen by the
/// next-episode rule (`NextEpisode.way`) among what can be downloaded: that quality from
/// that plugin, else the next plugin in the owner's list with that quality, else that
/// episode is left and the notice names it. Reckoned here so it can be tested without a
/// plugin; the app asks for the ways and keeps each one.
public enum SeasonDownload {
    /// The order qualities are offered in, best first.
    static let rank = ["4k", "2160p", "1440p", "1080p", "720p", "480p", "360p"]

    static func place(_ quality: String?) -> Int {
        guard let quality else { return rank.count }
        if quality == "cam" { return rank.count + 1 }
        return rank.firstIndex(of: quality) ?? rank.count
    }

    /// What a season can be downloaded in, from one episode's ways to play: each quality
    /// once, best first, by the first way of it in the owner's order of plugins. Only what
    /// can be downloaded (a torrent, or a plain web address).
    public static func choices(in answer: StreamsAnswer) -> [StreamsAnswer.Way] {
        var seen = Set<String>()
        let first = answer.ways.filter { $0.stream.canDownload && seen.insert($0.stream.quality ?? "").inserted }
        return first.enumerated()
            .sorted { (place($0.element.stream.quality), $0.offset) < (place($1.element.stream.quality), $1.offset) }
            .map(\.element)
    }

    /// An episode's way to download, by the next-episode rule: the same quality from the
    /// same plugin, else the first plugin in the owner's list with that quality, else none.
    public static func way(in answer: StreamsAnswer, like chosen: NextEpisode.Played) -> StreamsAnswer.Way? {
        let alike = answer.ways.filter { $0.stream.canDownload && $0.stream.quality == chosen.quality }
        return alike.first { $0.addonId == chosen.addonId } ?? alike.first
    }

    /// The episodes of a season still to download, in order: out by `today` ("2026-11-06",
    /// as the plugin writes an episode's day; one with no day counts as out), not on this
    /// Mac (`here`), and not being downloaded or waiting to be (`coming`).
    public static func wanted(
        _ episodes: [MediaDetails.Video], of series: MediaItem, here: KeptShelf.Card?,
        coming: [PendingKeeps.Keep], today: String
    ) -> [MediaDetails.Video] {
        episodes.filter { episode in
            if !episode.day.isEmpty, episode.day > today { return false }
            if here?.file(season: episode.season, episode: episode.episode) != nil { return false }
            return !coming.contains { $0.isEpisode(episode, of: series) }
        }
        .sorted { ($0.episode ?? 0) < ($1.episode ?? 0) }
    }

    /// What's said once the season's downloads have been asked for: "Downloading 7
    /// episodes of Season 2. Not found in 1080p: E05." `waiting`: how many of them wait
    /// for another episode of the same torrent first.
    public static func said(
        started: Int, waiting: Int = 0, missing: [MediaDetails.Video], season: Int, quality: String?
    ) -> String {
        let name = MediaDetails.seasonName(season)
        let label = MediaStream.label(of: quality)
        guard started > 0 else { return "None of \(name)'s episodes could be downloaded in \(label)." }
        var words = "Downloading \(started == 1 ? "1 episode" : "\(started) episodes") of \(name)."
        if waiting > 0 {
            words += " Episodes that come in one torrent are downloaded one after another."
        }
        if !missing.isEmpty {
            let numbers = missing.map { $0.episode.map { "E" + String(format: "%02d", $0) } ?? $0.title }
            words += " Not found in \(label): \(numbers.joined(separator: ", "))."
        }
        return words
    }
}

extension MediaStream {
    /// A quality as it's shown: "4K", "Cam", "1080p", or "Unknown".
    public static func label(of quality: String?) -> String {
        switch quality {
        case "cam": "Cam"
        case "4k": "4K"
        case let size?: size
        case nil: "Unknown"
        }
    }
}

extension PendingKeeps.Keep {
    /// What it goes by while it waits its turn (`SeasonQueue.slot`).
    public var waitingSlot: String { SeasonQueue.slot(self) }

    /// Whether this keep is of that episode of that series: by the title it was downloaded
    /// from the page of, else (a keep from before that was noted) by its show's name.
    public func isEpisode(_ episode: MediaDetails.Video, of series: MediaItem) -> Bool {
        guard ["series", "anime"].contains(kind ?? ""), season == episode.season, self.episode == episode.episode,
            episode.episode != nil
        else { return false }
        if let film { return film.id == series.id }
        return show?.compare(series.name, options: .caseInsensitive) == .orderedSame
    }
}

/// The episodes of a season waiting for their torrent (Download Season, 2026-11-06). The
/// engine keeps one film of a torrent at a time, and a season often comes as one torrent
/// with a file for each episode: so the first of them is kept, and the others wait here,
/// remembered by the app on this Mac (not in the library), and each is asked for once the
/// one before it of the same torrent has been kept (or couldn't be). They're listed in
/// Downloads as waiting, and carry on when the app is opened again.
public struct SeasonQueue: Codable, Equatable, Sendable {
    public private(set) var waiting: [PendingKeeps.Keep]

    public init(waiting: [PendingKeeps.Keep] = []) { self.waiting = waiting }

    /// What a waiting episode goes by in the list: its torrent and its file, which tell
    /// apart episodes that a keep's own id (the torrent's) doesn't.
    public static func slot(_ keep: PendingKeeps.Keep) -> String {
        "\(keep.infoHash.lowercased())#\(keep.fileIndex.map(String.init) ?? "")#\(keep.name)"
    }

    /// Put an episode at the end, unless it's waiting already.
    public mutating func add(_ keep: PendingKeeps.Keep) {
        guard !waiting.contains(where: { Self.slot($0) == Self.slot(keep) }) else { return }
        waiting.append(keep)
    }

    /// The next episode waiting for this torrent, taken out of the queue; nil if none is.
    public mutating func next(of infoHash: String) -> PendingKeeps.Keep? {
        guard let at = waiting.firstIndex(where: { $0.infoHash.lowercased() == infoHash.lowercased() }) else {
            return nil
        }
        return waiting.remove(at: at)
    }

    /// Take a waiting episode out (its ✕ in Downloads).
    public mutating func remove(slot: String) {
        waiting.removeAll { Self.slot($0) == slot }
    }

    /// The torrents that have episodes waiting.
    public var torrents: [String] {
        var seen = Set<String>()
        return waiting.map { $0.infoHash.lowercased() }.filter { seen.insert($0).inserted }
    }

    public static let key = "seasonQueue"

    public static func load(from defaults: UserDefaults = .standard) -> SeasonQueue {
        guard let data = defaults.data(forKey: key), let found = try? JSONDecoder().decode(SeasonQueue.self, from: data)
        else { return SeasonQueue() }
        return found
    }

    public func save(to defaults: UserDefaults = .standard) {
        if let data = try? JSONEncoder().encode(self) { defaults.set(data, forKey: Self.key) }
    }
}
