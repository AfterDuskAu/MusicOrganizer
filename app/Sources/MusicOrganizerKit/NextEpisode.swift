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
