import Foundation

/// What's been watched to its end (the owner, 2026-10-15: "movie from start to 90%. this
/// 10% accounts for credits"): a movie played to nine tenths of it, and each episode of a
/// series the same way. Kept by the app on this Mac by the film's or the episode's id,
/// with when it was finished; nothing of it is in the library or sent anywhere. Watching
/// it again doesn't take it off: the owner does that from an episode's or a card's menu.
public struct Finished: Equatable, Sendable {
    /// How much of a film or an episode is all of it: the rest is its credits.
    public static let mark = 0.9
    /// Ids kept at most. **As many as the sync carries** (the engine's `sharing.MOST_WATCHED`,
    /// 2026-11-13):
    /// it was 5,000, and marking whole series as watched is every episode of them (Law & Order:
    /// SVU alone is 524), so a few series filled it and each new mark quietly forgot the one
    /// marked longest ago — the owner found their checks gone on the iPad. Never let this be
    /// smaller than what a sync carries, or what's kept here can't reach the other apps either.
    public static let most = 50_000

    public private(set) var days: [String: Date]
    /// Films and episodes the owner marked Not Watched here, by id: a sync never marks one
    /// watched again (2026-10-16), until it's watched or marked watched here.
    public private(set) var unmarked: Set<String>

    public init(_ days: [String: Date] = [:], unmarked: Set<String> = []) {
        self.days = days
        self.unmarked = unmarked
    }

    public func contains(_ id: String) -> Bool { days[id] != nil }
    public func day(of id: String) -> Date? { days[id] }

    /// Where a film or an episode has got to, `seconds` of `length`. Returns whether this
    /// is what finished it.
    @discardableResult
    public mutating func played(_ id: String, to seconds: Double, of length: Double, now: Date = Date()) -> Bool {
        guard length > 0, seconds >= length * Self.mark, days[id] == nil else { return false }
        mark(id, watched: true, now: now)
        return true
    }

    /// The owner's own word, from a menu: watched, or not. `trimming` false leaves the oldest
    /// to be forgotten afterwards, for a whole series marked at once (`trim`).
    public mutating func mark(_ id: String, watched: Bool, now: Date = Date(), trimming: Bool = true) {
        guard watched else {
            days[id] = nil
            unmarked.insert(id)
            return
        }
        unmarked.remove(id)
        if days[id] == nil { days[id] = now }
        if trimming { trim() }
    }

    /// Only `most` are kept: the ones finished longest ago are forgotten, in one go (2026-11-13).
    /// One at a time, each new mark looked through every id for the oldest, so marking a series
    /// of five hundred episodes did that five hundred times.
    public mutating func trim() {
        guard days.count > Self.most else { return }
        let oldest = days.sorted { $0.value < $1.value }.prefix(days.count - Self.most)
        for one in oldest { days[one.key] = nil }
    }

    /// From what was remembered before this was kept (2026-10-15): a movie, or the
    /// episode a series had got to, that had been played to the mark.
    public static func seeded(from history: WatchHistory) -> Finished {
        var found = Finished()
        for entry in history.entries where entry.progress >= mark {
            switch entry.kind {
            case .movie: found.days[entry.id] = entry.at
            case .series: if let episode = entry.episodeId { found.days[episode] = entry.at }
            case .video: break
            }
        }
        return found
    }

    public static let key = "finishedFilms"
    public static let unmarkedKey = "finishedUnmarked"

    /// What's kept, or nil when nothing ever was (then it's seeded from the history).
    public static func saved(in defaults: UserDefaults = .standard) -> Finished? {
        guard let data = defaults.data(forKey: key),
            let days = try? JSONDecoder().decode([String: Date].self, from: data)
        else { return nil }
        return Finished(days, unmarked: Set(defaults.stringArray(forKey: unmarkedKey) ?? []))
    }

    public func save(in defaults: UserDefaults = .standard) {
        if let data = try? JSONEncoder().encode(days) { defaults.set(data, forKey: Self.key) }
        defaults.set(unmarked.sorted(), forKey: Self.unmarkedKey)
    }
}

/// Where each series the owner watches stands (the owner, 2026-10-15): it's watched when
/// its latest episode out has been ("only mark it ... as watched if the latest episode
/// has been watched"), and it goes back to be watched when a new one comes out ("only add
/// it back to the 'to watch' if there is a new episode that hasn't been watched"). Its
/// latest episode is learned whenever its episodes are read (its page, the Watchlist, the
/// calendar) and remembered here, with when it was read. Kept by the app on this Mac.
public struct SeriesProgress: Equatable, Sendable {
    public struct Known: Codable, Equatable, Sendable {
        /// Its latest episode out, by id, when it has one.
        public var latest: String?
        /// The latest episode out at the time the owner had watched it: so a new one
        /// after it is told from one they haven't got to.
        public var caughtUp: String?
        public var read: Date
        /// The latest episode out when the owner last had no new one to watch: what's out
        /// after it is new, Home's New Episodes and a card's "+1, +2" (2026-10-18, as a
        /// video app on a tablet has it since 2026-10-17). "" when nothing was out then;
        /// nil before this was kept (then it's taken from `caughtUp`).
        public var seen: String?
        /// The episodes out after `seen`, in order, as last read.
        public var fresh: [String]?
        /// When the latest of them came out: the newest first.
        public var freshAt: Date?

        public init(
            latest: String?, caughtUp: String?, read: Date, seen: String? = nil, fresh: [String]? = nil,
            freshAt: Date? = nil
        ) {
            (self.latest, self.caughtUp, self.read) = (latest, caughtUp, read)
            (self.seen, self.fresh, self.freshAt) = (seen, fresh, freshAt)
        }
    }

    public private(set) var series: [String: Known]

    public init(_ series: [String: Known] = [:]) { self.series = series }

    /// A series' latest episode out: the last by season and number among the ones whose
    /// time has come. Specials (season 0) aren't counted. An episode with no time of its
    /// own counts as out only when none of them has one.
    public static func latest(in videos: [MediaDetails.Video], now: Date = Date()) -> MediaDetails.Video? {
        out(in: videos, now: now).last
    }

    /// A series' episodes out, by season and number, by the same rule.
    public static func out(in videos: [MediaDetails.Video], now: Date = Date()) -> [MediaDetails.Video] {
        let numbered = videos.filter { ($0.season ?? 0) > 0 && $0.episode != nil }
        let dated = numbered.filter { $0.releasedAt != nil }
        let out = dated.isEmpty ? numbered : dated.filter { $0.releasedAt! <= now }
        return out.sorted { ($0.season ?? 0, $0.episode ?? 0) < ($1.season ?? 0, $1.episode ?? 0) }
    }

    public func latest(of id: String) -> String? { series[id]?.latest }

    /// A series' episodes have been read.
    public mutating func learned(
        _ id: String, episodes: [MediaDetails.Video], finished: Finished, now: Date = Date(), listed: Bool = false
    ) {
        let out = Self.out(in: episodes, now: now)
        let latest = out.last?.id
        let isNew = series[id] == nil
        var known = series[id] ?? Known(latest: nil, caughtUp: nil, read: now)
        // The latest episode out when the owner last watched any of it, in the last
        // `followingFor` (one left long ago isn't being followed): what came out since is new.
        let lastWatched = episodes.compactMap { finished.day(of: $0.id) }.max()
            .flatMap { now.timeIntervalSince($0) <= Self.followingFor ? $0 : nil }
        let watchedSeen = lastWatched.flatMap { watched in
            out.last { video in video.releasedAt.map { $0 <= watched } ?? true }?.id
        }
        // Kept from before there was `seen`: new is what came after the owner caught up. Read for
        // the first time: what was out when it was last watched, else everything out now.
        if known.seen == nil { known.seen = isNew ? watchedSeen ?? latest ?? "" : known.caughtUp ?? known.latest ?? "" }
        // Never later than that, however long ago it was first read (2026-11-10: a profile's
        // series read before its watching came took every episode out as seen).
        if let watchedSeen, let watchedAt = out.firstIndex(where: { $0.id == watchedSeen }),
            let seenAt = out.firstIndex(where: { $0.id == known.seen }), seenAt > watchedAt
        {
            known.seen = watchedSeen
        }
        (known.latest, known.read) = (latest, now)
        if let latest, finished.contains(latest) { known.caughtUp = latest }
        // What's out after the last one seen; with none of it left to watch, everything
        // out now has been seen. (One seen that's gone from the list is the same.)
        let seen = known.seen ?? ""
        let after = seen.isEmpty ? out[...] : out.firstIndex { $0.id == seen }.map { out[($0 + 1)...] } ?? []
        if after.allSatisfy({ finished.contains($0.id) }) { known.seen = latest ?? seen }
        // On the Watchlist (`listed`; the owner, 2026-11-10, for every app: "New Episodes, should
        // only follow watchlisted series ... Any unwatched episodes show up at +N", "it shouldnt
        // matter based on dates"): every episode out after the last one watched, whenever it
        // came out. One not begun has none. Off the Watchlist, what's above: what came out
        // since it was seen ("It can keep the 60 day new episode for just generally watched
        // series").
        let shown: ArraySlice<MediaDetails.Video> = listed
            ? out.lastIndex { finished.contains($0.id) }.map { out[($0 + 1)...] } ?? []
            : after
        if shown.allSatisfy({ finished.contains($0.id) }) {
            (known.fresh, known.freshAt) = (nil, nil)
        } else {
            known.fresh = shown.map(\.id)
            known.freshAt = shown.compactMap(\.releasedAt).max() ?? known.freshAt ?? now
        }
        series[id] = known
    }

    /// Films or episodes marked watched, or not, by the owner, from a menu: one, every episode
    /// before one, or every episode of a series that's out (a series' card, 2026-11-11: "Add a
    /// new option as mark as watched, this would include every episode in it"). Episodes say
    /// their series, which may be caught up by them. Marked not watched, each is kept as the
    /// owner's word (`Finished.unmarked`), so a paired device's sync doesn't mark it again.
    public mutating func marked(
        _ ids: [String], watched: Bool, of series: String?, in done: inout Finished, now: Date = Date()
    ) {
        for id in ids {
            done.mark(id, watched: watched, now: now, trimming: false)
            if watched, let series { finished(id, of: series) }
        }
        done.trim()
    }

    /// Every episode of a series that's out, by season and number, specials left out: what a
    /// series' card's Mark as Watched marks.
    public static func everyOut(in videos: [MediaDetails.Video], now: Date = Date()) -> [String] {
        out(in: videos, now: now).map(\.id)
    }

    /// An episode has been finished: if it's the series' latest, the owner is caught up.
    public mutating func finished(_ episodeId: String, of id: String) {
        guard series[id]?.latest == episodeId else { return }
        series[id]?.caughtUp = episodeId
    }

    /// Episodes were marked watched from elsewhere (a sync): a series whose latest episode
    /// is among them is caught up.
    public mutating func notice(_ finished: Finished) {
        for (id, known) in series {
            if let latest = known.latest, finished.contains(latest) { series[id]?.caughtUp = latest }
        }
    }

    /// Watched: its latest episode out has been. One whose episodes have never been read
    /// isn't, yet.
    public func isWatched(_ id: String, finished: Finished) -> Bool {
        series[id]?.latest.map(finished.contains) ?? false
    }

    /// Being watched again (2026-11-11: every episode of a series marked watched, then played
    /// again from its seventh season, and it wasn't in Continue Watching): its latest episode
    /// out has been watched, and the series has been played since, at an episode before it. One
    /// last played before it was finished, or before Mark as Watched, isn't; nor one played at
    /// its latest episode, which is watched to its end again.
    public func isRewatching(_ entry: WatchHistory.Entry, finished: Finished) -> Bool {
        guard entry.kind == .series, let latest = series[entry.id]?.latest, let episode = entry.episodeId,
            episode != latest, let done = finished.day(of: latest)
        else { return false }
        return entry.at > done
    }

    /// The episodes out since the owner last had none new to watch, that they haven't
    /// watched, in order: a card's "+1, +2" and Home's New Episodes (2026-10-18).
    public func newEpisodes(_ id: String, finished: Finished) -> [String] {
        (series[id]?.fresh ?? []).filter { !finished.contains($0) }
    }

    /// The series with new episodes (`newEpisodes`), the one whose latest came out last
    /// first (a tie: by id): Home's New Episodes.
    public func withFreshEpisodes(finished: Finished) -> [String] {
        series.keys.filter { !newEpisodes($0, finished: finished).isEmpty }
            .sorted { (series[$0]?.freshAt ?? .distantPast, $1) > (series[$1]?.freshAt ?? .distantPast, $0) }
    }

    /// The owner had caught up, and an episode has come out since that they haven't
    /// watched: the Watchlist's NEW EPISODE (its own rule, kept as it was: 2026-10-18).
    public func hasNewEpisode(_ id: String, finished: Finished) -> Bool {
        guard let known = series[id], let latest = known.latest, let caughtUp = known.caughtUp else {
            return false
        }
        return caughtUp != latest && !finished.contains(latest)
    }

    /// When the latest new episode of a series came out.
    public func newSince(_ id: String) -> Date? { series[id]?.freshAt }

    /// The series the owner had caught up with that have a new episode now.
    public func withNewEpisodes(finished: Finished) -> [String] {
        series.keys.filter { hasNewEpisode($0, finished: finished) }.sorted()
    }

    /// Whether a series' episodes are due to be read again: never read, not for a while,
    /// or not since new episodes were counted (`seen`, 2026-10-18: read once more then, so
    /// Home's New Episodes has them at once).
    public func isDue(_ id: String, every interval: TimeInterval, now: Date = Date()) -> Bool {
        guard let known = series[id], known.seen != nil else { return true }
        return now.timeIntervalSince(known.read) > interval
    }

    public static let key = "seriesProgress"

    /// A series watched in this long is being followed: read for the first time, what came out
    /// since is new (the owner's choice, 2026-11-10, as the video apps have it).
    public static let followingFor: TimeInterval = 60 * 86_400

    /// The rule the progress was kept by: 3 since a series on the Watchlist has every episode
    /// after the last one watched as new, and one off it what came out after its last watch in
    /// 60 days (2026-11-10, as the video apps count it). One kept before has every series read
    /// again at once, instead of in up to six hours. Kept per profile, as every setting is.
    public static let ruleKey = "seriesProgressRule"
    public static let ruleNow = 3

    public static func saved(in defaults: UserDefaults = .standard) -> SeriesProgress {
        guard let data = defaults.data(forKey: key),
            let series = try? JSONDecoder().decode([String: Known].self, from: data)
        else { return SeriesProgress() }
        if defaults.integer(forKey: ruleKey) == ruleNow { return SeriesProgress(series) }
        return SeriesProgress(series.mapValues { known in
            var known = known
            known.read = .distantPast
            return known
        })
    }

    public func save(in defaults: UserDefaults = .standard) {
        if let data = try? JSONEncoder().encode(series) { defaults.set(data, forKey: Self.key) }
        defaults.set(Self.ruleNow, forKey: Self.ruleKey)
    }
}

extension MediaDetails.Video {
    /// When it came out, from the add-on's own timestamp ("2023-10-25T01:00:00.000Z").
    public var releasedAt: Date? { released.flatMap(Released.date) }
}

/// The add-ons' timestamps, read with or without their fractions of a second. The readers
/// are made once: a calendar reads every episode of every series it shows.
enum Released {
    private static let withFraction: ISO8601DateFormatter = {
        let format = ISO8601DateFormatter()
        format.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return format
    }()
    private static let whole = ISO8601DateFormatter()
    private static let dayOnly: DateFormatter = {
        let format = DateFormatter()
        format.locale = Locale(identifier: "en_US_POSIX")
        format.timeZone = TimeZone(identifier: "UTC")
        format.dateFormat = "yyyy-MM-dd"
        return format
    }()

    static func date(_ text: String) -> Date? {
        guard !text.isEmpty else { return nil }
        // Only a day: its start, in UTC.
        return withFraction.date(from: text) ?? whole.date(from: text) ?? dayOnly.date(from: String(text.prefix(10)))
    }
}

/// Home's Recommended rows (the owner, 2026-10-15: one "which takes your watchlisted
/// watched and recommends shows based on that"). The owner's taste is what's on their
/// Watchlist, what they've watched and what they've hearted; the lists asked are the
/// popular lists of its leading genres; and what comes back is put in order of how close
/// it is to the taste, without anything already seen or listed.
public enum Recommending {
    /// The genres the taste is made of, the most met first (a tie: the first in the
    /// alphabet first). Nil-free; empty when there's nothing to go on.
    public static func genres(of taste: [MediaItem], most: Int = 3) -> [String] {
        var counts: [String: Int] = [:]
        for genre in taste.flatMap(\.genres) { counts[genre, default: 0] += 1 }
        return counts.sorted { ($0.value, $1.key) > ($1.value, $0.key) }.prefix(most).map(\.key)
    }

    /// The lists found (one for each genre asked) made into one: taken a place at a time
    /// from each in turn, each title once, without what's in `seen`; then the ones whose
    /// genres are worth most to the taste first (a genre is worth how often it was met in
    /// it), a tie keeping that order, so the most popular of the lists lead.
    public static func rank(_ lists: [[MediaItem]], taste: [MediaItem], seen: Set<String>) -> [MediaItem] {
        var worth: [String: Int] = [:]
        for genre in taste.flatMap(\.genres) { worth[genre, default: 0] += 1 }
        var met = seen
        var merged: [MediaItem] = []
        for place in 0..<(lists.map(\.count).max() ?? 0) {
            for list in lists where list.indices.contains(place) {
                let item = list[place]
                if met.insert(item.id).inserted { merged.append(item) }
            }
        }
        let scored = merged.enumerated().map { place, item in
            (item, place, Set(item.genres).reduce(0) { $0 + worth[$1, default: 0] })
        }
        return scored.sorted { ($0.2, $1.1) > ($1.2, $0.1) }.map(\.0)
    }
}

/// The calendar's month (the owner's drawing, 2026-10-15): a row for each week, Monday
/// first, with the days of the month before and after left empty.
public struct MonthGrid: Equatable, Sendable {
    public let year: Int
    /// 1 to 12.
    public let month: Int
    /// Each week's seven days, Monday first: the day of the month, or nil for a day
    /// that's another month's.
    public let weeks: [[Int?]]

    public init(year: Int, month: Int) {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        let first = calendar.date(from: DateComponents(year: year, month: month, day: 1))!
        self.year = calendar.component(.year, from: first)
        self.month = calendar.component(.month, from: first)
        let days = calendar.range(of: .day, in: .month, for: first)!.count
        // Sunday is 1 to Calendar: Monday first, it's the seventh.
        let before = (calendar.component(.weekday, from: first) + 5) % 7
        var slots: [Int?] = Array(repeating: nil, count: before) + (1...days).map { $0 }
        while slots.count % 7 != 0 { slots.append(nil) }
        weeks = stride(from: 0, to: slots.count, by: 7).map { Array(slots[$0..<$0 + 7]) }
    }

    /// The month a day is in.
    public init(containing date: Date, in timeZone: TimeZone = .current) {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        let parts = calendar.dateComponents([.year, .month], from: date)
        self.init(year: parts.year!, month: parts.month!)
    }

    /// The month `step` months on (or back, below 0).
    public func moved(by step: Int) -> MonthGrid {
        let index = year * 12 + (month - 1) + step
        return MonthGrid(year: index / 12, month: index % 12 + 1)
    }

    /// A day of this month as the calendar files things under it: "2026-10-15".
    public func day(_ number: Int) -> String {
        String(format: "%04d-%02d-%02d", year, month, number)
    }

    /// The month's name ("October"), and its short one ("Oct").
    public var name: String { (Self.names.monthSymbols as [String])[month - 1] }
    public var shortName: String { (Self.names.shortMonthSymbols as [String])[month - 1] }

    /// The days of the week, Monday first.
    public static var weekdays: [String] {
        let names: [String] = Self.names.weekdaySymbols
        return Array(names[1...]) + [names[0]]
    }

    private static var names: DateFormatter {
        let format = DateFormatter()
        format.locale = Locale(identifier: "en_AU")
        return format
    }
}

/// One thing on a day of the calendar: an episode of one of the owner's series coming
/// out, or a video one of their channels put up (the owner, 2026-10-12: "shows when the
/// next episode comes out, or ... your favourite channel uploads without warning").
public struct CalendarEntry: Identifiable, Hashable, Sendable {
    public let id: String
    /// The day it's on, "2026-10-15", on this Mac's clock.
    public let day: String
    /// For the order within a day.
    public let when: Date?
    /// The series' name, or the video's title.
    public let title: String
    /// "S2 E5 · Its Title", or the channel's name.
    public let detail: String
    public let picture: String?
    /// An episode: its series and its id, to open it on the series' page.
    public let series: MediaItem?
    public let episodeId: String?
    /// A channel's video, to play it.
    public let video: VideoHit?
    /// A film or series from TMDB, on the calendar's General (2026-11-05), to open its page.
    public var release: TMDBRelease?

    /// Every episode of a series that has a time, each on its day.
    public static func episodes(
        of series: MediaItem, _ videos: [MediaDetails.Video], in timeZone: TimeZone = .current
    ) -> [CalendarEntry] {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        return videos.compactMap { video in
            guard let when = video.releasedAt else { return nil }
            let about = [video.number, video.title.isEmpty ? nil : video.title].compactMap { $0 }
            return CalendarEntry(
                id: "\(series.id)|\(video.id)", day: day(of: when, on: calendar), when: when,
                title: series.name, detail: about.joined(separator: " · "),
                picture: series.poster, series: series, episodeId: video.id, video: nil)
        }
    }

    /// Channels' videos, each on the day it was put up (the day the engine gives).
    public static func uploads(_ videos: [VideoHit]) -> [CalendarEntry] {
        videos.compactMap { video in
            guard let published = video.published, published.count >= 10 else { return nil }
            return CalendarEntry(
                id: "video|\(video.videoId)", day: String(published.prefix(10)), when: nil,
                title: video.title, detail: video.channel ?? "", picture: video.thumbnail,
                series: nil, episodeId: nil, video: video)
        }
    }

    /// The month's releases from TMDB (the calendar's General, 2026-11-05), each on its day:
    /// "Film" or "New series" under its name.
    public static func releases(_ found: [TMDBRelease]) -> [CalendarEntry] {
        found.map { one in
            var entry = CalendarEntry(
                id: "tmdb|\(one.type)|\(one.id)", day: one.day, when: nil, title: one.name,
                detail: one.type == "series" ? "New series" : "Film", picture: one.poster, series: nil,
                episodeId: nil, video: nil)
            entry.release = one
            return entry
        }
    }

    /// What's on each day: episodes first, by time, then videos.
    public static func byDay(_ entries: [CalendarEntry]) -> [String: [CalendarEntry]] {
        Dictionary(grouping: entries, by: \.day).mapValues { day in
            day.sorted { first, second in
                switch (first.when, second.when) {
                case let (a?, b?): a == b ? first.title < second.title : a < b
                case (.some, nil): true
                case (nil, .some): false
                case (nil, nil): first.title < second.title
                }
            }
        }
    }

    /// A moment's day on this Mac's clock, "2026-10-15".
    public static func day(of moment: Date, in timeZone: TimeZone = .current) -> String {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        return day(of: moment, on: calendar)
    }

    private static func day(of moment: Date, on calendar: Calendar) -> String {
        let parts = calendar.dateComponents([.year, .month, .day], from: moment)
        return String(format: "%04d-%02d-%02d", parts.year!, parts.month!, parts.day!)
    }
}

/// How far each film and episode begun has been watched, by its id (the owner, 2026-11-07, as on
/// the tablet: an orange tick with how far, "✓ 56%", on a series' list of episodes and on a
/// movie's page, and nowhere else: not on cards). Kept by the app on this Mac, in the profile's
/// settings; watched is `Finished`.
public struct PartWatched: Equatable, Sendable {
    public struct Part: Codable, Equatable, Sendable {
        public let progress: Double
        public let at: Date
    }

    /// Films and episodes remembered at most: the one watched longest ago is forgotten first.
    public static let most = 500

    public private(set) var parts: [String: Part]

    public init(_ parts: [String: Part] = [:]) { self.parts = parts }

    /// How far it got, 0 to 1, while it's begun and not past nine tenths; nil otherwise.
    public func progress(of id: String) -> Double? { parts[id]?.progress }

    /// It was at `seconds` of `length`. Hardly begun (`FilmPositions.startS`), what was noted
    /// before stays; past nine tenths (`Finished.mark`) it's forgotten, being watched.
    public mutating func noted(_ id: String, at seconds: Double, of length: Double, now: Date = Date()) {
        guard length > 0, seconds >= FilmPositions.startS else { return }
        let progress = min(seconds / length, 1)
        if progress >= Finished.mark {
            parts[id] = nil
            return
        }
        parts[id] = Part(progress: progress, at: now)
        while parts.count > Self.most, let oldest = parts.min(by: { $0.value.at < $1.value.at }) {
            parts[oldest.key] = nil
        }
    }

    // MARK: kept in the app's settings (a profile's, like the rest of its records)

    public static let key = "partWatched"

    public static func saved(in defaults: UserDefaults = .standard) -> PartWatched {
        guard let data = defaults.data(forKey: key),
            let parts = try? JSONDecoder().decode([String: Part].self, from: data)
        else { return PartWatched() }
        return PartWatched(parts)
    }

    public func save(in defaults: UserDefaults = .standard) {
        if let data = try? JSONEncoder().encode(parts) { defaults.set(data, forKey: Self.key) }
    }
}
