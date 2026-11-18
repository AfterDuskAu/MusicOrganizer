import XCTest

@testable import MusicOrganizerKit

/// A thumbs down on a film or a series, and Home's Recommended From Your Favourites (the owner,
/// 2026-11-17: "add a thumbs down on series page, and movie page, if thumbed down, dont
/// recommend them. for example, if marked as watched, But thumbed down, it wont show as
/// recommended and wont say when theres new episodes. If hearted, on home add a list of
/// recommendations based on those likes").
final class ThumbsDownTests: XCTestCase {
    private func item(_ id: String, _ type: String = "series", _ genres: [String] = [], anime: Bool = false)
        -> MediaItem
    {
        let made = MediaItem(
            id: id, type: type, name: "N \(id)", poster: nil, posterShape: "poster", year: nil, rating: nil,
            genres: genres)
        return anime ? made.asAnime : made
    }

    /// Minutes from a fixed moment.
    private func at(_ minute: Double) -> Date { Date(timeIntervalSince1970: 1_800_000_000 + minute * 60) }

    /// Episodes 1 to `count` of season 1, each out at the minute given.
    private func episodes(_ series: String, out minutes: [Double]) throws -> [MediaDetails.Video] {
        let format = ISO8601DateFormatter()
        let list = minutes.enumerated().map { place, minute in
            let number = place + 1
            return #"{"id":"\#(series):1:\#(number)","title":"E\#(number)","season":1,"episode":\#(number),"released":"\#(format.string(from: at(minute)))"}"#
        }
        return try JSONDecoder().decode([MediaDetails.Video].self, from: Data(("[" + list.joined(separator: ",") + "]").utf8))
    }

    // MARK: kept

    func testAThumbsDownIsGivenTakenOffAndKept() throws {
        var down = ThumbsDown()
        let film = item("tt1", "movie")
        XCTAssertFalse(down.contains(film))
        down.toggle(film)
        down.toggle(item("tt2"))
        XCTAssertTrue(down.contains("tt1"))
        XCTAssertEqual(down.items.map(\.id), ["tt2", "tt1"])  // the newest first
        down.toggle(film)  // one click on, one off
        XCTAssertFalse(down.contains("tt1"))
        XCTAssertEqual(down.ids, ["tt2"])
        down.remove("tt9")  // one that hasn't one: nothing
        XCTAssertEqual(ThumbsDown([item("a"), item("a")]).items.count, 1)

        let name = "thumbs-down-test-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: name))
        defer { defaults.removePersistentDomain(forName: name) }
        XCTAssertEqual(ThumbsDown.saved(in: defaults), ThumbsDown())
        down.save(in: defaults)
        XCTAssertEqual(ThumbsDown.saved(in: defaults), down)
        // A profile's own record: it goes with an export's records.
        XCTAssertTrue(SettingsCarrier.records.contains(ThumbsDown.key))
        // A heart can be taken off by id, as a thumbs down given does.
        var hearts = MediaFavourites([film])
        hearts.remove("tt1")
        XCTAssertFalse(hearts.contains(film))
    }

    // MARK: no new episodes for a series with a thumbs down

    func testAWatchedSeriesWithAThumbsDownNeverSaysItHasNewEpisodes() throws {
        var done = Finished()
        var progress = SeriesProgress()
        // Two out, both watched: caught up, watched.
        done.mark("s:1:1", watched: true, now: at(0))
        done.mark("s:1:2", watched: true, now: at(1))
        progress.learned("s", episodes: try episodes("s", out: [-100, -50]), finished: done, now: at(2), listed: true)
        XCTAssertTrue(progress.isWatched("s", finished: done))
        // A third comes out.
        progress.learned("s", episodes: try episodes("s", out: [-100, -50, 10]), finished: done, now: at(20), listed: true)
        // Without a thumbs down, as before: not watched, "+1", NEW EPISODE, back to To Watch.
        XCTAssertFalse(progress.isWatched("s", finished: done))
        XCTAssertEqual(progress.newEpisodes("s", finished: done), ["s:1:3"])
        XCTAssertTrue(progress.hasNewEpisode("s", finished: done))
        XCTAssertEqual(progress.withNewEpisodes(finished: done), ["s"])
        XCTAssertEqual(progress.withFreshEpisodes(finished: done), ["s"])
        // With one: still watched, no "+1", no NEW EPISODE, not back to To Watch, not in Home's
        // New Episodes.
        XCTAssertTrue(progress.isWatched("s", finished: done, quietly: true))
        XCTAssertEqual(progress.newEpisodes("s", finished: done, quiet: true), [])
        XCTAssertFalse(progress.hasNewEpisode("s", finished: done, quiet: true))
        XCTAssertEqual(progress.withNewEpisodes(finished: done, quiet: ["s"]), [])
        XCTAssertEqual(progress.withFreshEpisodes(finished: done, quiet: ["s"]), [])
        // Another series isn't quietened by it.
        XCTAssertEqual(progress.withFreshEpisodes(finished: done, quiet: ["other"]), ["s"])
        // Marked Not Watched by the owner: not watched, thumbs down or not.
        done.mark("s:1:2", watched: false)
        XCTAssertFalse(progress.isWatched("s", finished: done, quietly: true))
        // One never caught up with isn't made watched by a thumbs down.
        var begun = SeriesProgress()
        var some = Finished()
        some.mark("b:1:1", watched: true, now: at(0))
        begun.learned("b", episodes: try episodes("b", out: [-100, -50]), finished: some, now: at(2))
        XCTAssertFalse(begun.isWatched("b", finished: some, quietly: true))
        XCTAssertFalse(SeriesProgress().isWatched("never read", finished: some, quietly: true))
    }

    func testAThumbedDownSeriesTheOwnerHadFinishedIsntCarriedOnWithForANewEpisode() throws {
        var done = Finished()
        var progress = SeriesProgress()
        var history = WatchHistory()
        done.mark("s:1:1", watched: true, now: at(0))
        done.mark("s:1:2", watched: true, now: at(1))
        progress.learned("s", episodes: try episodes("s", out: [-100, -50]), finished: done, now: at(2))
        // A third comes out at minute 10; at 30 the owner plays the first again to its end.
        progress.learned("s", episodes: try episodes("s", out: [-100, -50, 10]), finished: done, now: at(20))
        var again = WatchHistory.Entry(id: "s", kind: .series, name: "S", seconds: 2900, length: 3000, at: at(30))
        again.episodeId = "s:1:1"
        history.watched(again)
        // As before, it's carried on with: the new episode is the one to watch.
        XCTAssertEqual(history.continuing(finished: done, progress: progress).map(\.id), ["s"])
        // With a thumbs down it's watched, and nothing says there's a new one.
        XCTAssertTrue(history.continuing(finished: done, progress: progress, quiet: ["s"]).isEmpty)
        // Left part way through an episode, it's carried on with all the same.
        history.place("s", seconds: 600, length: 3000, now: at(40))
        XCTAssertEqual(history.continuing(finished: done, progress: progress, quiet: ["s"]).map(\.id), ["s"])
    }

    // MARK: recommended

    func testATitleWithAThumbsDownIsntPartOfTheTasteOrRecommended() throws {
        let liked = item("a", "series", ["Crime"])
        let disliked = item("b", "series", ["Horror"])
        let film = item("m", "movie", ["Horror"])
        let anime = item("k", "series", ["Shounen"], anime: true)
        var down = ThumbsDown()
        down.toggle(disliked)
        let records = [liked, disliked, film, anime, liked]
        XCTAssertEqual(Recommending.taste(of: records, kind: "series", anime: false).map(\.id), ["a", "b"])
        XCTAssertEqual(Recommending.taste(of: records, kind: "series", anime: false, without: down).map(\.id), ["a"])
        XCTAssertEqual(Recommending.taste(of: records, kind: "movie", anime: false, without: down).map(\.id), ["m"])
        XCTAssertEqual(Recommending.taste(of: records, kind: "series", anime: true, without: down).map(\.id), ["k"])
        // A list's title with a thumbs down is left out, as one seen is; each title once.
        let crime = [item("x1", "series", ["Crime"]), item("b", "series", ["Crime"]), item("a", "series")]
        let other = [item("x2", "series", ["Comedy"]), item("x1", "series", ["Crime"])]
        let seen: Set<String> = ["a"]
        let ranked = Recommending.rank(
            [crime, other], taste: [liked], leavingOut: { seen.contains($0) || down.contains($0) })
        XCTAssertEqual(ranked.map(\.id), ["x1", "x2"])
        // The set form is the same as before.
        XCTAssertEqual(Recommending.rank([crime, other], taste: [liked], seen: ["a", "b"]).map(\.id), ["x1", "x2"])
    }

    func testRecommendedFromYourFavouritesAsksTheHeartedFilmsAndSeriesLeadingGenres() throws {
        let hearted = [
            item("s1", "series", ["Crime", "Drama"]), item("m1", "movie", ["Action", "Comedy"]),
            item("s2", "series", ["Crime", "Mystery"]), item("m2", "movie", ["Action", "Musical"]),
            item("k1", "series", ["Shounen"], anime: true),  // anime: its own Recommended row
            item("s3", "series", ["Horror", "Horror Comedy"]),
        ]
        let offered: (String) -> [String] = { type in
            type == "movie" ? ["Action", "Comedy", "Drama"] : ["Crime", "Drama", "Mystery", "Horror"]
        }
        let asks = Recommending.liked(hearted, offered: offered)
        // The films' first; a genre their list can't be asked by (Musical) is passed over for the next.
        XCTAssertEqual(
            asks,
            [
                .init(type: "movie", genre: "Action"), .init(type: "movie", genre: "Comedy"),
                .init(type: "series", genre: "Crime"), .init(type: "series", genre: "Drama"),
                .init(type: "series", genre: "Horror"),
            ])
        // A series with a thumbs down doesn't count, whatever the records say.
        var down = ThumbsDown()
        down.toggle(hearted[5])
        XCTAssertEqual(
            Recommending.liked(hearted, without: down, offered: offered).filter { $0.type == "series" }.map(\.genre),
            ["Crime", "Drama", "Mystery"])
        // Nothing hearted, or only anime: nothing to ask.
        XCTAssertEqual(Recommending.liked([], offered: offered), [])
        XCTAssertEqual(Recommending.liked([hearted[4]], offered: offered), [])
        XCTAssertEqual(Recommending.liked(hearted, offered: { _ in [] }), [])
    }

    // MARK: Home

    func testRecommendedFromYourFavouritesIsPutOnHomeOnce() {
        let all = HomeSection.all(movieGenres: [], seriesGenres: [])
        let row = all.first { $0.id == HomeSection.liked }
        XCTAssertEqual(row?.title, "Recommended From Your Favourites")
        XCTAssertEqual(row?.group, .moviesAndSeries)
        XCTAssertEqual(row?.kind, "liked")
        XCTAssertNil(row?.group.mediaType)
        // After the films' and series' Recommended rows.
        var layout = HomeLayout([
            "songs.favourites", "movies.recommended", "series.recommended", "anime.recommended", "movies.popular",
        ])
        layout.addLiked()
        XCTAssertEqual(
            layout.shown,
            ["songs.favourites", "movies.recommended", "series.recommended", HomeSection.liked, "anime.recommended",
             "movies.popular"])
        layout.addLiked()  // there already: nothing more
        XCTAssertEqual(layout.shown.count, 6)
        // Taken off by the owner, it stays off: it's put on once, by the app's own mark.
        layout.set(HomeSection.liked, shown: false)
        XCTAssertFalse(layout.shows(HomeSection.liked))
        // With no Recommended row, after the last of the films' or series'; with none, at the end.
        var other = HomeLayout(["series.continue", "movies.popular", "channels.new"])
        other.addLiked()
        XCTAssertEqual(other.shown, ["series.continue", "movies.popular", HomeSection.liked, "channels.new"])
        var bare = HomeLayout(["songs.favourites"])
        bare.addLiked()
        XCTAssertEqual(bare.shown, ["songs.favourites", HomeSection.liked])
    }

    // MARK: the Watchlist page (the owner, 2026-11-17: "Anything that is thumbed down, but
    // Watchlisted doesnt appear on the watchlist")

    func testTheWatchlistPageLeavesOutWhatHasAThumbsDownButTheListKeepsIt() {
        var list = Watchlist()
        for id in ["a", "b", "c"] { list.toggle(item(id)) }
        var down = ThumbsDown()
        XCTAssertEqual(down.without(list.items).map(\.id), ["c", "b", "a"])  // none: as it was
        down.toggle(item("b"))
        XCTAssertEqual(down.without(list.items).map(\.id), ["c", "a"])  // in its order, without it
        XCTAssertTrue(list.contains(item("b")))  // still on the list itself
        down.toggle(item("b"))  // taken off: back on the page
        XCTAssertEqual(down.without(list.items).map(\.id), ["c", "b", "a"])
    }

    // MARK: thumbs downs in the sync (the owner, 2026-11-17: "yes")

    func testATakenOffThumbsDownIsRememberedAndKept() throws {
        var down = ThumbsDown()
        down.toggle(item("a"))
        down.toggle(item("b"))
        XCTAssertEqual(down.takenOff, [])
        down.toggle(item("a"))  // its button
        down.remove("b")  // a heart given
        XCTAssertEqual(down.takenOff, ["a", "b"])
        down.remove("z")  // one that never had one isn't "taken off"
        XCTAssertEqual(down.takenOff, ["a", "b"])
        down.toggle(item("a"))  // given again here: no longer taken off
        XCTAssertEqual(down.takenOff, ["b"])

        let name = "thumbs-down-off-test-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: name))
        defer { defaults.removePersistentDomain(forName: name) }
        down.save(in: defaults)
        XCTAssertEqual(ThumbsDown.saved(in: defaults), down)
        XCTAssertEqual(ThumbsDown.saved(in: defaults).takenOff, ["b"])
        // A profile's own record, in an export's records and put away with the profile.
        XCTAssertTrue(SettingsCarrier.records.contains(ThumbsDown.takenOffKey))
        XCTAssertTrue(ProfileSettings.belongsToProfile(ThumbsDown.takenOffKey))
    }

    func testThumbsDownsAreSentWithTheWatching() throws {
        var down = ThumbsDown()
        down.toggle(item("tt1", "movie", ["Drama"]))
        down.toggle(item("kitsu:2", anime: true))
        let state = WatchSync.state(watchlist: Watchlist(), finished: Finished(), history: WatchHistory(), thumbsDown: down)
        XCTAssertEqual(state.thumbsDown?.map(\.id), ["kitsu:2", "tt1"])
        let json = try XCTUnwrap(
            JSONSerialization.jsonObject(with: JSONEncoder().encode(state)) as? [String: Any])
        let sent = try XCTUnwrap(json["thumbsDown"] as? [[String: Any]])  // the agreement's name
        XCTAssertEqual(sent.first?["anime"] as? Bool, true)
        XCTAssertEqual(sent.last?["genres"] as? [String], ["Drama"])
        // As it comes back, the titles are as they went.
        let back = try JSONDecoder().decode(WatchSync.State.self, from: JSONEncoder().encode(state))
        XCTAssertEqual(back.thumbsDown?.map(\.item), down.items)
        // From an engine or a device from before: no list, and nothing to add.
        let old = try JSONDecoder().decode(
            WatchSync.State.self, from: Data(#"{"watchlist":[],"watched":[],"progress":[]}"#.utf8))
        XCTAssertNil(old.thumbsDown)
        var mine = ThumbsDown([item("x")])
        XCTAssertFalse(WatchSync.mergeThumbsDown(old, into: &mine, hearted: MediaFavourites()))
        XCTAssertEqual(mine.items.map(\.id), ["x"])
    }

    func testThumbsDownsFromADeviceAreAddedAndNeverTakeAnythingAway() {
        let sent = WatchSync.State(thumbsDown: ["a", "b", "c", "d", "e"].map { WatchSync.Title(item($0)) })
        var down = ThumbsDown([item("x"), item("a")])
        down.toggle(item("b"))  // given here...
        down.toggle(item("b"))  // ...and taken off here: a sync doesn't put it back
        let hearts = MediaFavourites([item("c")])  // hearted here: the heart stays
        XCTAssertTrue(WatchSync.mergeThumbsDown(sent, into: &down, hearted: hearts))
        // The new ones after the ones here, in the order sent; nothing here taken off.
        XCTAssertEqual(down.items.map(\.id), ["x", "a", "d", "e"])
        XCTAssertEqual(down.takenOff, ["b"])
        // The same again changes nothing.
        XCTAssertFalse(WatchSync.mergeThumbsDown(sent, into: &down, hearted: hearts))
        // A device that has taken one off sends none for it: it stays here (nothing is removed).
        let fewer = WatchSync.State(thumbsDown: [WatchSync.Title(item("a"))])
        XCTAssertFalse(WatchSync.mergeThumbsDown(fewer, into: &down, hearted: hearts))
        XCTAssertEqual(down.items.map(\.id), ["x", "a", "d", "e"])
        // The heart wasn't the owner taking the thumbs down off: once it's gone, the next sync
        // brings the thumbs down.
        XCTAssertTrue(WatchSync.mergeThumbsDown(sent, into: &down, hearted: MediaFavourites()))
        XCTAssertEqual(down.items.map(\.id), ["x", "a", "d", "e", "c"])
        // Given here again, one taken off is taken from a sync too, as any other.
        down.toggle(item("b"))
        down.remove("b")
        XCTAssertFalse(WatchSync.mergeThumbsDown(sent, into: &down, hearted: MediaFavourites()))
    }

    func testAThumbsDownThatArrivesActsAsOneGivenHere() throws {
        // A series caught up with, and a new episode out: with a thumbs down from a device, it
        // stays watched and says nothing new, exactly as one given here.
        var done = Finished()
        var progress = SeriesProgress()
        done.mark("s:1:1", watched: true, now: at(0))
        progress.learned("s", episodes: try episodes("s", out: [-100]), finished: done, now: at(1), listed: true)
        progress.learned("s", episodes: try episodes("s", out: [-100, 10]), finished: done, now: at(20), listed: true)
        XCTAssertFalse(progress.isWatched("s", finished: done))
        var down = ThumbsDown()
        WatchSync.mergeThumbsDown(
            WatchSync.State(thumbsDown: [WatchSync.Title(item("s"))]), into: &down, hearted: MediaFavourites())
        XCTAssertTrue(progress.isWatched("s", finished: done, quietly: down.contains("s")))
        XCTAssertEqual(progress.withNewEpisodes(finished: done, quiet: down.ids), [])
        XCTAssertEqual(progress.withFreshEpisodes(finished: done, quiet: down.ids), [])
    }
}
