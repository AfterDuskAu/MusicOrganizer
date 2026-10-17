import XCTest

@testable import MusicOrganizerKit

/// What's been watched to its end, where a series stands, Home's recommendations and the
/// calendar's month (the owner, 2026-10-15).
final class WatchedTests: XCTestCase {
    private let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    private func item(_ id: String, _ type: String = "series", _ genres: [String] = []) throws -> MediaItem {
        let list = genres.map { "\"\($0)\"" }.joined(separator: ",")
        return try decoder.decode(
            MediaItem.self,
            from: Data(#"{"id":"\#(id)","type":"\#(type)","name":"N \#(id)","poster_shape":"poster","genres":[\#(list)]}"#.utf8))
    }

    /// Episodes "s:<season>:<episode>", each out on its day ("" for none).
    private func episodes(_ numbers: [(Int, Int, String)]) throws -> [MediaDetails.Video] {
        let list = numbers.map { season, episode, day in
            let released = day.isEmpty ? "" : #","released":"\#(day)T01:00:00.000Z""#
            return #"{"id":"s:\#(season):\#(episode)","title":"E\#(episode)","season":\#(season),"episode":\#(episode)\#(released)}"#
        }
        return try decoder.decode([MediaDetails.Video].self, from: Data(("[" + list.joined(separator: ",") + "]").utf8))
    }

    private func day(_ text: String) -> Date {
        ISO8601DateFormatter().date(from: text + "T12:00:00Z")!
    }

    // MARK: watched to its end

    func testAFilmIsWatchedAtNineTenthsAndStaysWatched() {
        var done = Finished()
        XCTAssertFalse(done.played("tt1", to: 5300, of: 6000))  // 88%: not yet
        XCTAssertFalse(done.contains("tt1"))
        XCTAssertTrue(done.played("tt1", to: 5400, of: 6000))  // 90%: the rest is credits
        XCTAssertTrue(done.contains("tt1"))
        XCTAssertFalse(done.played("tt1", to: 5900, of: 6000))  // finished once, not again
        // Watched again from the start, it stays watched; the owner takes it off.
        XCTAssertFalse(done.played("tt1", to: 30, of: 6000))
        XCTAssertTrue(done.contains("tt1"))
        done.mark("tt1", watched: false)
        XCTAssertFalse(done.contains("tt1"))
        XCTAssertFalse(done.played("x", to: 100, of: 0))  // no known length
        // An episode by hand, and back.
        done.mark("tt2:1:1", watched: true)
        XCTAssertTrue(done.contains("tt2:1:1"))
    }

    func testWhatWasWatchedBeforeIsTakenIn() {
        var history = WatchHistory()
        history.watched(.init(id: "m1", kind: .movie, name: "Done", seconds: 5500, length: 6000))
        history.watched(.init(id: "m2", kind: .movie, name: "Begun", seconds: 600, length: 6000))
        var show = WatchHistory.Entry(id: "s1", kind: .series, name: "Show", seconds: 2500, length: 2600)
        show.episodeId = "s1:1:4"
        history.watched(show)
        history.watched(.init(id: "v1", kind: .video, name: "Video", seconds: 99, length: 100))
        let done = Finished.seeded(from: history)
        XCTAssertEqual(done.days.keys.sorted(), ["m1", "s1:1:4"])
    }

    func testFinishedIsKeptOnThisMac() throws {
        let name = "finished-test-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: name))
        defer { defaults.removePersistentDomain(forName: name) }
        XCTAssertNil(Finished.saved(in: defaults))  // never kept: seeded from the history
        var done = Finished()
        done.mark("tt1", watched: true, now: Date(timeIntervalSince1970: 1_000_000))
        done.save(in: defaults)
        XCTAssertEqual(Finished.saved(in: defaults), done)
        XCTAssertTrue(SettingsCarrier.records.contains(Finished.key))
        XCTAssertTrue(SettingsCarrier.records.contains(SeriesProgress.key))
    }

    // MARK: where a series stands

    func testASeriesLatestEpisodeIsTheLastOneOut() throws {
        let all = try episodes([
            (1, 1, "2026-01-01"), (1, 2, "2026-01-08"), (2, 1, "2026-10-10"), (2, 2, "2026-10-17"),
            (0, 1, "2026-10-12"),  // a special: never the latest
        ])
        XCTAssertEqual(SeriesProgress.latest(in: all, now: day("2026-10-15"))?.id, "s:2:1")
        XCTAssertEqual(SeriesProgress.latest(in: all, now: day("2026-10-18"))?.id, "s:2:2")
        XCTAssertEqual(SeriesProgress.latest(in: all, now: day("2025-12-01"))?.id, nil)  // none out
        // With no times at all, every episode counts as out; with some, only those.
        let undated = try episodes([(1, 1, ""), (1, 2, "")])
        XCTAssertEqual(SeriesProgress.latest(in: undated, now: day("2026-10-15"))?.id, "s:1:2")
        let mixed = try episodes([(1, 1, "2026-01-01"), (1, 2, "")])
        XCTAssertEqual(SeriesProgress.latest(in: mixed, now: day("2026-10-15"))?.id, "s:1:1")
    }

    func testASeriesIsWatchedOnlyWhenItsLatestEpisodeIs() throws {
        let all = try episodes([(1, 1, "2026-10-01"), (1, 2, "2026-10-08"), (1, 3, "2026-10-22")])
        var done = Finished()
        var progress = SeriesProgress()
        XCTAssertFalse(progress.isWatched("s", finished: done))  // never read
        progress.learned("s", episodes: all, finished: done, now: day("2026-10-15"))
        XCTAssertEqual(progress.latest(of: "s"), "s:1:2")
        done.mark("s:1:1", watched: true)
        XCTAssertFalse(progress.isWatched("s", finished: done))  // not the latest yet
        done.mark("s:1:2", watched: true)
        progress.finished("s:1:2", of: "s")
        XCTAssertTrue(progress.isWatched("s", finished: done))
        XCTAssertFalse(progress.hasNewEpisode("s", finished: done))
        // A week on, episode 3 is out: back to be watched, as a new episode.
        progress.learned("s", episodes: all, finished: done, now: day("2026-10-23"))
        XCTAssertFalse(progress.isWatched("s", finished: done))
        XCTAssertTrue(progress.hasNewEpisode("s", finished: done))
        XCTAssertEqual(progress.withNewEpisodes(finished: done), ["s"])
        done.mark("s:1:3", watched: true)
        progress.finished("s:1:3", of: "s")
        XCTAssertTrue(progress.isWatched("s", finished: done))
        XCTAssertFalse(progress.hasNewEpisode("s", finished: done))
    }

    func testANewEpisodeIsOnlyNewToSomeoneWhoHadCaughtUp() throws {
        let all = try episodes([(1, 1, "2026-10-01"), (1, 2, "2026-10-08")])
        var done = Finished()
        done.mark("s:1:1", watched: true)
        var progress = SeriesProgress()
        progress.learned("s", episodes: all, finished: done, now: day("2026-10-15"))
        // Part way through a series isn't a new episode.
        XCTAssertFalse(progress.hasNewEpisode("s", finished: done))
        // Read when the latest was already watched: caught up from then.
        done.mark("s:1:2", watched: true)
        progress.learned("s", episodes: all, finished: done, now: day("2026-10-16"))
        XCTAssertTrue(progress.isWatched("s", finished: done))
        // Read again in a day: due only after the time given.
        XCTAssertFalse(progress.isDue("s", every: 86_400 * 2, now: day("2026-10-17")))
        XCTAssertTrue(progress.isDue("s", every: 3600, now: day("2026-10-17")))
        XCTAssertTrue(progress.isDue("other", every: 3600))
    }

    // MARK: recommended

    func testRecommendationsFollowTheOwnersGenres() throws {
        let taste = [
            try item("a", "series", ["Crime", "Drama"]), try item("b", "series", ["Crime", "Thriller"]),
            try item("c", "series", ["Comedy"]),
        ]
        XCTAssertEqual(Recommending.genres(of: taste), ["Crime", "Comedy", "Drama"])
        XCTAssertEqual(Recommending.genres(of: taste, most: 1), ["Crime"])
        XCTAssertEqual(Recommending.genres(of: []), [])
        let crime = [try item("x1", "series", ["Crime"]), try item("a", "series", ["Crime"]),
                     try item("x2", "series", ["Crime", "Drama"])]
        let comedy = [try item("y1", "series", ["Comedy"]), try item("x1", "series", ["Crime"])]
        let ranked = Recommending.rank([crime, comedy], taste: taste, seen: ["a"])
        // "a" has been seen; x1 comes once; x2 has two of the taste's genres, so leads;
        // x1 (Crime, met twice) before y1 (Comedy, once).
        XCTAssertEqual(ranked.map(\.id), ["x2", "x1", "y1"])
    }

    // MARK: the calendar

    func testAMonthIsLaidOutMondayFirst() {
        // October 2026 begins on a Thursday and has 31 days: five weeks, as drawn.
        let october = MonthGrid(year: 2026, month: 10)
        XCTAssertEqual(october.weeks.count, 5)
        XCTAssertEqual(october.weeks[0], [nil, nil, nil, 1, 2, 3, 4])
        XCTAssertEqual(october.weeks[4], [26, 27, 28, 29, 30, 31, nil])
        XCTAssertEqual(october.day(5), "2026-10-05")
        XCTAssertEqual(october.name, "October")
        XCTAssertEqual(october.moved(by: -1).shortName, "Sep")
        XCTAssertEqual(october.moved(by: 1).shortName, "Nov")
        XCTAssertEqual(october.moved(by: 3), MonthGrid(year: 2027, month: 1))
        XCTAssertEqual(MonthGrid(year: 2026, month: 1).moved(by: -1), MonthGrid(year: 2025, month: 12))
        XCTAssertEqual(MonthGrid.weekdays.first, "Monday")
        XCTAssertEqual(MonthGrid.weekdays.last, "Sunday")
        // A month that starts on a Monday has nothing before it; February 2027 is four weeks.
        XCTAssertEqual(MonthGrid(year: 2027, month: 2).weeks.count, 4)
        XCTAssertEqual(MonthGrid(year: 2027, month: 2).weeks[0].first, 1)
    }

    func testEpisodesAndUploadsGoOnTheirDays() throws {
        let show = try item("tt9", "series")
        let all = try episodes([(1, 1, "2026-10-01"), (1, 2, ""), (1, 3, "2026-10-15")])
        let utc = TimeZone(identifier: "UTC")!
        let entries = CalendarEntry.episodes(of: show, all, in: utc)
        XCTAssertEqual(entries.map(\.day), ["2026-10-01", "2026-10-15"])  // no time, no day
        XCTAssertEqual(entries.first?.detail, "S1 E1 · E1")
        XCTAssertEqual(entries.first?.episodeId, "s:1:1")
        // Out at one in the morning in UTC is the same morning further east, and the
        // evening before further west: each Mac's own day.
        let sydney = TimeZone(identifier: "Australia/Sydney")!
        let losAngeles = TimeZone(identifier: "America/Los_Angeles")!
        XCTAssertEqual(CalendarEntry.episodes(of: show, all, in: sydney).first?.day, "2026-10-01")
        XCTAssertEqual(CalendarEntry.episodes(of: show, all, in: losAngeles).first?.day, "2026-09-30")
        let video = try decoder.decode(
            VideoHit.self,
            from: Data(#"{"video_id":"v1","title":"A Video","channel":"A Channel","published":"2026-10-15"}"#.utf8))
        let days = CalendarEntry.byDay(entries + CalendarEntry.uploads([video]))
        XCTAssertEqual(days["2026-10-15"]?.map(\.title), ["N tt9", "A Video"])  // episodes first
        XCTAssertEqual(days["2026-10-15"]?.last?.detail, "A Channel")
    }

    // MARK: Home

    func testTheRecommendedRowsArePutOnHomeOnce() {
        var layout = HomeLayout(["songs.favourites", "series.continue", "series.popular", "movies.popular"])
        layout.addRecommended()
        XCTAssertEqual(
            layout.shown,
            ["songs.favourites", "series.continue", "series.popular", "series.recommended", "anime.recommended",
             "movies.popular"])
        layout.addRecommended()  // there already: nothing more
        XCTAssertEqual(layout.shown.count, 6)
        var bare = HomeLayout(["songs.favourites"])
        bare.addRecommended()
        XCTAssertEqual(bare.shown, ["songs.favourites", "series.recommended", "anime.recommended"])
        let all = HomeSection.all(movieGenres: [], seriesGenres: [])
        XCTAssertEqual(all.first { $0.id == "anime.recommended" }?.title, "Recommended Anime")
        XCTAssertEqual(all.first { $0.id == "anime.recommended" }?.group.mediaType, "anime")
        XCTAssertNil(HomeSection(customID: "anime.custom.A|and|B"))  // not made from a Finder
    }

    func testAFilmPastNineTenthsIsntCarriedOnWith() {
        var history = WatchHistory()
        history.watched(.init(id: "m1", kind: .movie, name: "Film"))
        history.place("m1", seconds: 5300, length: 6000)
        XCTAssertEqual(history.unfinished(.movie).map(\.id), ["m1"])
        history.place("m1", seconds: 5450, length: 6000)  // into the credits
        XCTAssertTrue(history.unfinished(.movie).isEmpty)
        var places = FilmPositions()
        places.watched("m1", to: 5450, of: 6000)
        XCTAssertNil(places.place(of: "m1"))  // opened again, it starts again
    }

    // MARK: new episodes, "+1, +2" (2026-10-18, as the video app on a tablet counts them)

    func testNewEpisodesAreCountedFromWhenTheOwnerLastHadNoneNew() throws {
        let all = try episodes([
            (1, 1, "2026-10-01"), (1, 2, "2026-10-08"), (1, 3, "2026-10-15"), (1, 4, "2026-10-22"), (0, 1, "2026-10-20"),
        ])
        var done = Finished()
        var progress = SeriesProgress()
        done.mark("s:1:1", watched: true)
        done.mark("s:1:2", watched: true)
        progress.learned("s", episodes: all, finished: done, now: day("2026-10-09"))
        XCTAssertEqual(progress.newEpisodes("s", finished: done), [])
        // Two come out (and a special, which isn't counted): +2, the newest's day kept.
        progress.learned("s", episodes: all, finished: done, now: day("2026-10-23"))
        XCTAssertEqual(progress.newEpisodes("s", finished: done), ["s:1:3", "s:1:4"])
        XCTAssertEqual(progress.series["s"]?.freshAt, ISO8601DateFormatter().date(from: "2026-10-22T01:00:00Z"))
        done.mark("s:1:3", watched: true)
        XCTAssertEqual(progress.newEpisodes("s", finished: done), ["s:1:4"])
        done.mark("s:1:4", watched: true)
        XCTAssertEqual(progress.newEpisodes("s", finished: done), [])
        XCTAssertTrue(progress.isWatched("s", finished: done))
        // Marked not watched again, an episode that came out new is new again.
        done.mark("s:1:4", watched: false)
        XCTAssertEqual(progress.newEpisodes("s", finished: done), ["s:1:4"])
        done.mark("s:1:4", watched: true)
        // Read with none of them left to watch: from then, all of them have been seen.
        progress.learned("s", episodes: all, finished: done, now: day("2026-10-24"))
        done.mark("s:1:4", watched: false)
        XCTAssertEqual(progress.newEpisodes("s", finished: done), [])
    }

    func testPartWayThroughOnlyWhatCameOutSinceIsNew() throws {
        let all = try episodes([(1, 1, "2026-10-01"), (1, 2, "2026-10-08"), (1, 3, "2026-10-15")])
        var done = Finished()
        done.mark("s:1:1", watched: true)
        var progress = SeriesProgress()
        progress.learned("s", episodes: all, finished: done, now: day("2026-10-09"))
        // Episode 2 was out already: not new. Episode 3 is.
        progress.learned("s", episodes: all, finished: done, now: day("2026-10-16"))
        XCTAssertEqual(progress.newEpisodes("s", finished: done), ["s:1:3"])
        XCTAssertFalse(progress.isWatched("s", finished: done))
        // The Watchlist's NEW EPISODE keeps its own rule (the owner, 2026-10-18: "Watchlist
        // stays how it is"): only for someone who had caught up.
        XCTAssertFalse(progress.hasNewEpisode("s", finished: done))
    }

    func testASeriesWithNothingOutYetHasItsFirstEpisodeAsNew() throws {
        let all = try episodes([(1, 1, "2026-10-20"), (1, 2, "2026-10-20")])
        var progress = SeriesProgress()
        progress.learned("s", episodes: all, finished: Finished(), now: day("2026-10-16"))
        XCTAssertNil(progress.latest(of: "s"))
        progress.learned("s", episodes: all, finished: Finished(), now: day("2026-10-21"))
        XCTAssertEqual(progress.newEpisodes("s", finished: Finished()).count, 2)
    }

    func testWhatWasKeptBeforeCountsFromWhenTheOwnerCaughtUp() throws {
        let all = try episodes([(1, 1, "2026-10-01"), (1, 2, "2026-10-08"), (1, 3, "2026-10-15")])
        var done = Finished()
        done.mark("s:1:1", watched: true)
        done.mark("s:1:2", watched: true)
        // As it was saved before `seen`: caught up with episode 2.
        let saved = Data(#"{"s":{"latest":"s:1:2","caughtUp":"s:1:2","read":0}}"#.utf8)
        var progress = SeriesProgress(try JSONDecoder().decode([String: SeriesProgress.Known].self, from: saved))
        // Read once more straight away, whenever it was read last, to count them.
        XCTAssertTrue(progress.isDue("s", every: 86_400 * 365 * 100, now: day("2026-10-16")))
        progress.learned("s", episodes: all, finished: done, now: day("2026-10-16"))
        XCTAssertFalse(progress.isDue("s", every: 3600, now: day("2026-10-16")))
        XCTAssertEqual(progress.newEpisodes("s", finished: done), ["s:1:3"])
        XCTAssertTrue(progress.hasNewEpisode("s", finished: done))  // the Watchlist's too
    }

    func testTheNewestNewEpisodeComesFirst() throws {
        let early = try episodes([(1, 1, "2026-10-01"), (1, 2, "2026-10-10")])
        let late = try episodes([(1, 1, "2026-10-01"), (1, 2, "2026-10-12")])
        let one = try episodes([(1, 1, "2026-10-01")])
        var progress = SeriesProgress()
        for id in ["a", "b", "c"] { progress.learned(id, episodes: one, finished: Finished(), now: day("2026-10-02")) }
        progress.learned("a", episodes: early, finished: Finished(), now: day("2026-10-13"))
        progress.learned("b", episodes: late, finished: Finished(), now: day("2026-10-13"))
        progress.learned("c", episodes: one, finished: Finished(), now: day("2026-10-13"))
        XCTAssertEqual(progress.withFreshEpisodes(finished: Finished()), ["b", "a"])
    }

    func testTheEpisodeToCarryOnWithIsTheNextNotWatchedElseTheFirstNew() throws {
        let all = try episodes([(1, 1, "2026-10-01"), (1, 2, "2026-10-08"), (1, 3, "2026-10-15"), (1, 4, "2026-10-22")])
        var done = Finished()
        let today = "2026-10-23"
        // After the last one played.
        XCTAssertEqual(
            NextEpisode.toWatch(after: "s:1:1", in: all, fresh: [], finished: done, today: today)?.id, "s:1:2")
        // That one watched already (on another device, say): the first new one.
        done.mark("s:1:2", watched: true)
        XCTAssertEqual(
            NextEpisode.toWatch(after: "s:1:1", in: all, fresh: ["s:1:3", "s:1:4"], finished: done, today: today)?.id,
            "s:1:3")
        // Never played here: the first new one not watched.
        done.mark("s:1:3", watched: true)
        XCTAssertEqual(
            NextEpisode.toWatch(after: nil, in: all, fresh: ["s:1:3", "s:1:4"], finished: done, today: today)?.id,
            "s:1:4")
        // Nothing to carry on with.
        XCTAssertNil(NextEpisode.toWatch(after: "s:1:4", in: all, fresh: [], finished: done, today: today))
    }
}
