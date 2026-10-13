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
}

