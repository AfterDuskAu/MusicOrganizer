import Foundation

/// Playing a series' next episode by itself when one ends (the owner, 2026-10-12: "make
/// it a settings switch. for autoplay."). Off until it's switched on in Settings →
/// Player. Which episode is next, and which of its ways to play is taken, is reckoned
/// here so it can be tested without a film; the app asks for the ways and plays one.
///
/// **For a torrent, the switch is the owner's standing word:** the next episode's torrent
/// is joined with no click on it, which nothing else in the app does (CLAUDE.md, the
/// promises for torrents). With the switch off, nothing is ever joined without a click.
public enum NextEpisode {
    /// The switch's saved name. Off as standard.
    public static let key = "playsNextEpisode"

    /// How far through an episode Next Episode is offered (the owner, 2026-11-09, for every
    /// app: "At 95% of runtime, it offers the play next episode"; until then it was there the
    /// whole episode). The next one still starts by itself at the very end with the switch on, or
    /// Play All; never before the end ("It doesnt move automatically to the next episode before
    /// the film ends": the closing-credits skip went the same day).
    public static let offeredAt = 0.95

    /// Next Episode is offered: this far through an episode whose length is known.
    public static func isOffered(time: Double, duration: Double) -> Bool {
        duration > 0 && time >= duration * offeredAt
    }

    /// How the episode that ended was played: by which add-on's way, at what quality.
    public struct Played: Codable, Hashable, Sendable {
        public let addonId: String
        public let quality: String?

        public init(addonId: String, quality: String?) {
            self.addonId = addonId
            self.quality = quality
        }
    }

    /// The episode after this one: the next in its season, or the first of the next
    /// season there is. Nil after the last one, for a special (season 0, which leads
    /// nowhere), and for one that isn't out yet (`today` is the day, "2026-10-12", as
    /// the add-on writes an episode's own day).
    /// The episode before `id` (the owner, 2026-11-09, for every app: "A previous episode and
    /// after button"): the one before it in its season, or the last of the season before;
    /// never a special (season 0). Nil for the first episode, and for a special.
    public static func before(_ id: String, in videos: [MediaDetails.Video]) -> MediaDetails.Video? {
        guard let now = videos.first(where: { $0.id == id }), let season = now.season, season > 0,
            let episode = now.episode
        else { return nil }
        let earlier = videos.compactMap { video -> (MediaDetails.Video, Int, Int)? in
            guard let itsSeason = video.season, itsSeason > 0, let itsEpisode = video.episode,
                itsSeason < season || (itsSeason == season && itsEpisode < episode)
            else { return nil }
            return (video, itsSeason, itsEpisode)
        }
        return earlier.max { ($0.1, $0.2) < ($1.1, $1.2) }?.0
    }

    public static func after(
        _ id: String, in videos: [MediaDetails.Video], today: String? = nil
    ) -> MediaDetails.Video? {
        guard let now = videos.first(where: { $0.id == id }), let season = now.season, season > 0,
            let episode = now.episode
        else { return nil }
        func number(_ video: MediaDetails.Video) -> (Int, Int)? {
            guard let season = video.season, season > 0, let episode = video.episode else { return nil }
            return (season, episode)
        }
        let later = videos.filter { video in
            guard let (itsSeason, itsEpisode) = number(video) else { return false }
            return itsSeason > season || (itsSeason == season && itsEpisode > episode)
        }
        let next = later.min { first, second in
            guard let a = number(first), let b = number(second) else { return false }
            return a < b
        }
        if let next, let today, !next.day.isEmpty, next.day > today { return nil }
        return next
    }

    /// The episode to carry on with, from Home (Continue Watching or New Episodes, 2026-10-18):
    /// the one after the episode last played (`last`) when it isn't watched yet, else the
    /// first of the new ones (`fresh`, in order) not watched yet. Nil when there's neither.
    public static func toWatch(
        after last: String?, in videos: [MediaDetails.Video], fresh: [String], finished: Finished,
        today: String? = nil
    ) -> MediaDetails.Video? {
        if let last, let next = after(last, in: videos, today: today), !finished.contains(next.id) {
            return next
        }
        let first = fresh.first { !finished.contains($0) }
        return first.flatMap { id in videos.first { $0.id == id } }
    }

    /// The episode a click on a series in Home carries on with (2026-10-18): the one last
    /// played when it isn't watched (one started and left before it was begun, the next
    /// one started by itself, say); else `toWatch`'s. One left part way is played on from
    /// where it was, before this is asked.
    public static func toCarryOn(
        from last: String?, in videos: [MediaDetails.Video], fresh: [String], finished: Finished,
        today: String? = nil
    ) -> MediaDetails.Video? {
        if let last, !finished.contains(last), let it = videos.first(where: { $0.id == last }) { return it }
        return toWatch(after: last, in: videos, fresh: fresh, finished: finished, today: today)
    }

    /// The season a series' page opens at (2026-11-11: the page showed the first season, not
    /// how far the owner had got): the one with the episode to carry on with (`toCarryOn`),
    /// else the one with the episode last played. Nil for a series never played (the page
    /// opens at its first season, as before).
    public static func seasonReached(
        from last: String?, in videos: [MediaDetails.Video], fresh: [String], finished: Finished,
        today: String? = nil
    ) -> Int? {
        guard let last else { return nil }
        let next = toCarryOn(from: last, in: videos, fresh: fresh, finished: finished, today: today)
        return (next ?? videos.first { $0.id == last })?.season
    }

    /// The owner's rule for how the next episode is played: **the same quality from the
    /// same source**; failing that, **the next source that has that quality** (in the
    /// owner's order of add-ons); and failing that nothing, and the app opens the
    /// episode's page for the owner to choose. Only what the film player plays (an
    /// address, or a torrent) is taken.
    public static func way(in answer: StreamsAnswer, like played: Played) -> StreamsAnswer.Way? {
        let alike = answer.ways.filter {
            ["url", "torrent"].contains($0.stream.kind) && $0.stream.quality == played.quality
        }
        return alike.first { $0.addonId == played.addonId } ?? alike.first
    }
}

/// "Are you still watching?" (the owner, 2026-11-09, for every app: "add are you still watching on
/// all apps, after 3 episodes"): once three episodes in a row have started by themselves (the
/// next-episode switch, or Play All) with nothing pressed, the next one waits for the owner's
/// answer. A press in the player, or anything started by hand, starts the count again.
public enum StillWatching {
    /// Episodes started by themselves in a row before the question.
    public static let after = 3

    /// Whether the next episode waits for the question's answer.
    public static func asks(_ startedByThemselves: Int) -> Bool { startedByThemselves >= after }
}
