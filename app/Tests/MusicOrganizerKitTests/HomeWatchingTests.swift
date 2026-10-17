import XCTest

@testable import MusicOrganizerKit

final class HomeWatchingTests: XCTestCase {
    private func film(_ id: String, seconds: Double = 0, length: Double = 0) -> WatchHistory.Entry {
        WatchHistory.Entry(id: id, kind: .movie, name: "A Film", seconds: seconds, length: length)
    }

    private var way: MediaStream {
        MediaStream(
            kind: "torrent", name: "1080p", title: nil, quality: "1080p", size: 900_000_000, url: nil,
            videoId: nil, infoHash: String(repeating: "ab", count: 20), fileIndex: 2,
            trackers: ["udp://tracker.example:6969"])
    }

    func testTheWayAFilmWasPlayedIsKeptWithIt() throws {
        var entry = film("tt1", seconds: 600, length: 6000)
        (entry.stream, entry.playName) = (way, "A Film (1921)")
        let name = "home-watching-test-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: name))
        defer { defaults.removePersistentDomain(forName: name) }
        var history = WatchHistory()
        history.watched(entry)
        history.save(in: defaults)
        let back = try XCTUnwrap(WatchHistory.saved(in: defaults).entries.first)
        XCTAssertEqual(back.stream, way)
        XCTAssertEqual(back.playName, "A Film (1921)")
        XCTAssertEqual(back.stream?.fileIndex, 2)
    }

    func testAnEntryKeptBeforeTheWayWasRememberedStillReads() throws {
        let old = #"[{"id":"tt1","kind":"movie","name":"A Film","seconds":600,"length":6000,"at":700000000}]"#
        let entries = try JSONDecoder().decode([WatchHistory.Entry].self, from: Data(old.utf8))
        XCTAssertEqual(entries.first?.id, "tt1")
        XCTAssertNil(entries.first?.stream)
        XCTAssertNil(entries.first?.playName)
    }

    func testBegunIsPastAQuarter() {
        var history = WatchHistory()
        history.watched(film("quarter", seconds: 1500, length: 6000))
        history.watched(film("more", seconds: 1501, length: 6000))
        history.watched(film("hardly", seconds: 120, length: 6000))
        history.watched(film("unknown-length", seconds: 500, length: 0))
        XCTAssertFalse(history.isBegun("quarter"))  // a quarter exactly isn't past it
        XCTAssertTrue(history.isBegun("more"))
        XCTAssertFalse(history.isBegun("hardly"))
        XCTAssertFalse(history.isBegun("unknown-length"))
        XCTAssertFalse(history.isBegun("never-played"))
    }

    func testHomeOffersTheNotWatchedRows() {
        let ids = HomeSection.all(movieGenres: [], seriesGenres: []).map(\.id)
        XCTAssertTrue(ids.contains("movies.unwatched"))
        XCTAssertTrue(ids.contains("series.unwatched"))
        XCTAssertTrue(ids.contains("channels.unwatched"))
        // Offered, not put on the page for anyone: Customise Home switches them on.
        XCTAssertFalse(HomeSection.standard.contains { $0.hasSuffix(".unwatched") })
    }

    // MARK: watched at all (the green check, and the Watched list)

    func testAFilmPlayedHoweverFarHasBeenWatched() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let film = try decoder.decode(
            MediaItem.self, from: Data(#"{"id":"tt1","type":"movie","name":"A Film","poster_shape":"poster","genres":[]}"#.utf8))
        let show = try decoder.decode(
            MediaItem.self, from: Data(#"{"id":"tt2","type":"series","name":"A Show","poster_shape":"poster","genres":[]}"#.utf8))
        var history = WatchHistory()
        XCTAssertFalse(history.hasWatched("tt1"))
        history.watched(.watching(film))
        history.watched(.watching(show))
        history.watched(WatchHistory.Entry(id: "vid12345678", kind: .video, name: "A Video"))
        XCTAssertTrue(history.hasWatched("tt1"))  // begun is enough: "if you've watched anything"
        XCTAssertTrue(history.hasWatched("tt2"))
        XCTAssertFalse(history.hasWatched("vid12345678"))  // a channel's video isn't a film
        XCTAssertFalse(history.hasWatched("tt9"))
        // The Watched lists: films in one, series in the other, the latest first.
        XCTAssertEqual(history.watched(.movie).map(\.id), ["tt1"])
        XCTAssertEqual(history.watched(.series).map(\.id), ["tt2"])
        history.forget("tt1")
        XCTAssertFalse(history.hasWatched("tt1"))
    }

    // MARK: the Watchlist

    func testAFilmIsPutOnTheWatchlistAndTakenOff() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        func item(_ id: String, _ type: String) throws -> MediaItem {
            try decoder.decode(
                MediaItem.self,
                from: Data(#"{"id":"\#(id)","type":"\#(type)","name":"\#(id)","poster_shape":"poster","genres":[]}"#.utf8))
        }
        let (film, show, other) = (try item("tt1", "movie"), try item("tt2", "series"), try item("tt3", "movie"))
        var list = Watchlist()
        XCTAssertFalse(list.contains(film))
        list.toggle(film)
        list.toggle(show)
        list.toggle(other)
        XCTAssertTrue(list.contains(film) && list.contains(show))
        // The newest first, films and series apart.
        XCTAssertEqual(list.of(type: "movie").map(\.id), ["tt3", "tt1"])
        XCTAssertEqual(list.of(type: "series").map(\.id), ["tt2"])
        list.toggle(film)  // on it already: taken off
        XCTAssertEqual(list.items.map(\.id), ["tt3", "tt2"])
        // It's kept on this Mac, and travels with an export's records.
        let kept = try XCTUnwrap(UserDefaults(suiteName: "watchlist-test-\(UUID().uuidString)"))
        list.save(in: kept)
        XCTAssertEqual(Watchlist.saved(in: kept), list)
        XCTAssertTrue(SettingsCarrier.records.contains(Watchlist.key))
        XCTAssertTrue(SidebarRows.all.contains { $0.key == "watchlist" && $0.group == "Videos" })
    }

    // MARK: Continue Watching Series (the owner, 2026-10-18: "It should always be the one that was last watched")

    private func show(_ id: String) -> MediaItem {
        MediaItem(id: id, type: "series", name: "Show \(id)", poster: nil, posterShape: "poster", year: nil, rating: nil, genres: [])
    }

    private func at(_ minutes: Double) -> Date { Date(timeIntervalSince1970: 1_800_000_000 + minutes * 60) }

    /// A series at one of its episodes ("tt2:1:3"), `seconds` of `length` into it, watched at minute `minute`.
    private func episode(
        _ series: String, _ season: Int, _ number: Int, seconds: Double = 0, length: Double = 0, minute: Double
    ) -> WatchHistory.Entry {
        var entry = WatchHistory.Entry(
            id: series, kind: .series, name: "Show \(series)", detail: "S\(season) E\(number)", item: show(series),
            seconds: seconds, length: length, at: at(minute))
        entry.episodeId = "\(series):\(season):\(number)"
        return entry
    }

    /// A series' episodes, season 1, each out long ago.
    private func episodes(_ series: String, _ count: Int) throws -> [MediaDetails.Video] {
        let list = (1...count).map {
            #"{"id":"\#(series):1:\#($0)","title":"E\#($0)","season":1,"episode":\#($0),"released":"2020-01-0\#($0)T01:00:00.000Z"}"#
        }
        return try JSONDecoder().decode([MediaDetails.Video].self, from: Data(("[" + list.joined(separator: ",") + "]").utf8))
    }

    func testTheSeriesWatchedLastIsFirstInContinueWatching() throws {
        var history = WatchHistory()
        var finished = Finished()
        var progress = SeriesProgress()
        // An older series left part way through an episode, a day ago.
        history.watched(episode("tt1", 1, 2, seconds: 600, length: 3000, minute: -1440))
        // One the owner caught up with (its latest, episode 3, played to its end): nothing to carry on with.
        finished.mark("tt3:1:3", watched: true, now: at(-600))
        history.watched(episode("tt3", 1, 3, seconds: 2950, length: 3000, minute: -600))
        progress.learned("tt3", episodes: try episodes("tt3", 3), finished: finished)
        // One opened for a moment and left, nothing of it watched: not something being watched.
        history.watched(episode("tt4", 1, 1, seconds: 2, length: 3000, minute: -300))
        // The series being watched now: episodes 1 and 2 played to their end, one after the
        // other, the place noted as each played.
        for number in 1...2 {
            let minute = Double(number * 30)
            history.watched(episode("tt2", 1, number, minute: minute))
            history.place("tt2", seconds: 2950, length: 3000, now: at(minute + 29))
            XCTAssertTrue(finished.played("tt2:1:\(number)", to: 2950, of: 3000, now: at(minute + 29)))
        }
        progress.learned("tt2", episodes: try episodes("tt2", 8), finished: finished)
        // Between episodes, the one it's at played to its end: still the first. (Before
        // 2026-10-18 it wasn't in the row at all: only part way counted.)
        XCTAssertEqual(history.continuing(finished: finished, progress: progress).map(\.id), ["tt2", "tt1"])
        XCTAssertEqual(history.unfinished(.series).map(\.id), ["tt1"])
        // Episode 3 started by itself, nothing known of it yet (no length): still the first.
        history.watched(episode("tt2", 1, 3, minute: 90))
        XCTAssertEqual(history.continuing(finished: finished, progress: progress).map(\.id), ["tt2", "tt1"])

        // Reopened: the same order.
        let name = "continue-watching-test-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: name))
        defer { defaults.removePersistentDomain(forName: name) }
        history.save(in: defaults)
        history = WatchHistory.saved(in: defaults)
        XCTAssertEqual(history.continuing(finished: finished, progress: progress).map(\.id), ["tt2", "tt1"])

        // The older one played again from the row (the app stamps it as watched now): first.
        var again = try XCTUnwrap(history.entries.first { $0.id == "tt1" })
        again.at = at(120)
        history.watched(again)
        XCTAssertEqual(history.continuing(finished: finished, progress: progress).map(\.id), ["tt1", "tt2"])

        // A sync from a paired video app that had the other series where it was earlier
        // changes nothing; then one that watched it there later puts it first.
        var pad = WatchHistory()
        pad.watched(episode("tt2", 1, 2, seconds: 1500, length: 3000, minute: 60))
        var list = Watchlist()
        XCTAssertFalse(
            WatchSync.merge(
                WatchSync.state(watchlist: Watchlist(), finished: Finished(), history: pad), into: &list,
                finished: &finished, history: &history))
        XCTAssertEqual(history.continuing(finished: finished, progress: progress).map(\.id), ["tt1", "tt2"])
        pad.watched(episode("tt2", 1, 3, seconds: 400, length: 3000, minute: 150))
        WatchSync.merge(
            WatchSync.state(watchlist: Watchlist(), finished: Finished(), history: pad), into: &list,
            finished: &finished, history: &history)
        XCTAssertEqual(history.continuing(finished: finished, progress: progress).map(\.id), ["tt2", "tt1"])
        XCTAssertEqual(history.entries.first?.episodeId, "tt2:1:3")

        // Where one has got to makes it the one watched last, though it was further back.
        history.place("tt1", seconds: 700, length: 3000, now: at(160))
        XCTAssertEqual(history.continuing(finished: finished, progress: progress).map(\.id), ["tt1", "tt2"])
        // And once its latest episode out is watched, a series has nothing to carry on with.
        history.watched(episode("tt2", 1, 8, minute: 170))
        history.place("tt2", seconds: 2990, length: 3000, now: at(199))
        XCTAssertTrue(finished.played("tt2:1:8", to: 2990, of: 3000, now: at(199)))
        progress.finished("tt2:1:8", of: "tt2")
        XCTAssertEqual(history.continuing(finished: finished, progress: progress).map(\.id), ["tt1"])
    }

    func testAHistoryKeptOutOfOrderIsReadLatestFirst() {
        // Kept before 2026-10-18, a series played again from Continue Watching could be at
        // the front with the time it was watched before: read back, it's put in its place.
        let entries = [
            episode("tt1", 1, 1, minute: 10), episode("tt2", 1, 1, minute: 30), episode("tt3", 1, 1, minute: 30),
        ]
        XCTAssertEqual(WatchHistory(entries).entries.map(\.id), ["tt2", "tt3", "tt1"])  // a tie keeps its order
    }

    func testAKitsuSeriesIsBetweenEpisodesToo() {
        // Kitsu's ids have a colon of their own ("kitsu:7442", its episodes "kitsu:7442:2").
        var history = WatchHistory()
        var entry = WatchHistory.Entry(
            id: "kitsu:7442", kind: .series, name: "An Anime", item: show("kitsu:7442"), seconds: 1400,
            length: 1440, at: at(0))
        entry.episodeId = "kitsu:7442:2"
        history.watched(entry)
        var finished = Finished()
        XCTAssertTrue(history.continuing(finished: finished, progress: SeriesProgress()).isEmpty)
        finished.mark("kitsu:7442:2", watched: true, now: at(0))
        XCTAssertEqual(history.continuing(finished: finished, progress: SeriesProgress()).map(\.id), ["kitsu:7442"])
    }

    func testACardBetweenEpisodesPlaysTheNextTheWayTheLastWasPlayed() {
        var finished = Finished()
        // Played to its end here: between episodes.
        var ended = episode("tt2", 1, 2, seconds: 2950, length: 3000, minute: 0)
        XCTAssertTrue(ended.isBetweenEpisodes(finished: finished))
        // Part way: carried on with where it was left, not the next.
        XCTAssertFalse(episode("tt2", 1, 2, seconds: 600, length: 3000, minute: 0).isBetweenEpisodes(finished: finished))
        // The next started by itself, not begun yet: it's played, not the one after it.
        XCTAssertFalse(episode("tt2", 1, 3, minute: 0).isBetweenEpisodes(finished: finished))
        // Watched to its end elsewhere (a sync), and only begun here: between episodes.
        let synced = episode("tt2", 1, 4, seconds: 5, length: 3000, minute: 0)
        XCTAssertFalse(synced.isBetweenEpisodes(finished: finished))
        finished.mark("tt2:1:4", watched: true)
        XCTAssertTrue(synced.isBetweenEpisodes(finished: finished))
        // A film is never between episodes.
        XCTAssertFalse(film("tt1", seconds: 5900, length: 6000).isBetweenEpisodes(finished: finished))

        // The way the last was played, for the next to be played the same way (`NextEpisode`).
        XCTAssertNil(ended.played)  // not remembered: its page is opened instead
        (ended.stream, ended.source) = (way, "torrentio.example")
        XCTAssertEqual(ended.played, NextEpisode.Played(addonId: "torrentio.example", quality: "1080p"))
        var movie = film("tt1")
        (movie.stream, movie.source) = (way, "torrentio.example")
        XCTAssertNil(movie.played)
    }
}
