import Compression
import XCTest

@testable import MusicOrganizerKit

/// Reading what Stremio remembers, for an import into a profile (2026-10-16).
final class StremioLibraryTests: XCTestCase {
    /// A watched-episodes field as Stremio writes it: last episode, count up to it, and
    /// the bits (lowest first) squeezed with zlib, in base64.
    private func field(anchor: String, length: Int, watched: [Int]) -> String {
        var bytes = [UInt8](repeating: 0, count: (watched.max() ?? 0) / 8 + 1)
        for index in watched { bytes[index / 8] |= 1 << (index % 8) }
        var out = [UInt8](repeating: 0, count: 256)
        let written = compression_encode_buffer(&out, out.count, bytes, bytes.count, nil, COMPRESSION_ZLIB)
        let zlib = Data([0x78, 0x9C] + out.prefix(written) + [0, 0, 0, 0])  // header, deflate, (unchecked) sum
        return "\(anchor):\(length):\(zlib.base64EncodedString())"
    }

    private func details(_ json: String) throws -> MediaDetails {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(MediaDetails.self, from: Data(json.utf8))
    }

    /// A series' details with episodes "<id>:<season>:<n>" for the numbers given.
    private func series(_ id: String, _ episodes: [(Int, Int)], genres: [String] = ["Drama"]) throws -> MediaDetails {
        let videos = episodes.map { season, number in
            #"{"id":"\#(id):\#(season):\#(number)","title":"E\#(number)","season":\#(season),"episode":\#(number)}"#
        }
        let list = genres.map { "\"\($0)\"" }.joined(separator: ",")
        return try details(
            #"{"id":"\#(id)","type":"series","name":"Show \#(id)","genres":[\#(list)],"cast":[],"directors":[],"videos":[\#(videos.joined(separator: ","))]}"#)
    }

    func testWatchedEpisodesAreReadAndLinedUp() throws {
        let ids = (1...6).map { "tt1:1:\($0)" }
        // Episodes 1, 2 and 4 watched, the last of them episode 4 (the fourth of the list).
        let written = try XCTUnwrap(StremioLibrary.Watched(field(anchor: "tt1:1:4", length: 4, watched: [0, 1, 3])))
        XCTAssertEqual(written.anchor, "tt1:1:4")
        XCTAssertEqual(written.episodes(in: ids), ["tt1:1:1", "tt1:1:2", "tt1:1:4"])
        // Two specials put at the front of the list since: still the same episodes.
        let withSpecials = ["tt1:0:1", "tt1:0:2"] + ids
        XCTAssertEqual(written.episodes(in: withSpecials), ["tt1:1:1", "tt1:1:2", "tt1:1:4"])
        // The last one watched isn't in the list: nothing can be said.
        XCTAssertEqual(written.episodes(in: ["tt1:2:1"]), [])
        XCTAssertNil(StremioLibrary.Watched("nonsense"))
    }

    func testStremiosRecordsAndItsExportAreRead() throws {
        let item = #"{"_id":"tt1","type":"series","name":"Show","removed":false,"_mtime":"2026-10-01T00:00:00.000Z","state":{"lastWatched":"2026-10-01T10:00:00.000Z","timeOffset":300000,"duration":3000000,"video_id":"tt1:1:2"}}"#
        let newer = item.replacingOccurrences(of: "2026-10-01T00", with: "2026-10-05T00")
            .replacingOccurrences(of: "\"removed\":false", with: "\"removed\":true")
        let library = Data(#"{"uid":"u","items":{"tt1":\#(item),"bad":{"nothing":1}}}"#.utf8)
        let recent = Data(#"{"uid":"u","items":{"tt1":\#(newer)}}"#.utf8)
        let read = StremioLibrary.items(fromRecords: [library, recent])
        XCTAssertEqual(read.map(\.id), ["tt1"])  // once, the one changed last; the bad one skipped
        XCTAssertEqual(read.first?.removed, true)
        XCTAssertEqual(read.first?.state.videoId, "tt1:1:2")
        XCTAssertEqual(read.first?.state.timeOffset, 300000)
        let export = Data(#"{"library":[{"d":\#(item)},{"d":\#(item)}],"user":{}}"#.utf8)
        XCTAssertEqual(StremioLibrary.items(fromExport: export).count, 1)
    }

    func testAnImportPlan() throws {
        func item(_ id: String, _ type: String, removed: Bool, _ state: String, name: String = "N") -> String {
            #"{"_id":"\#(id)","type":"\#(type)","name":"\#(name)","removed":\#(removed),"_mtime":"2026-10-01T00:00:00.000Z","state":{\#(state)}}"#
        }
        // Stremio counts the special first: episodes 1 to 3 are its second to fourth.
        let watchedField = field(anchor: "tt1:1:3", length: 4, watched: [1, 2, 3])
        let items = [
            // A series in the Library: three episodes watched, the fourth half way.
            item("tt1", "series", removed: false,
                 #""lastWatched":"2026-10-08T10:00:00.000Z","timeOffset":1500000,"duration":3000000,"video_id":"tt1:1:4","watched":"\#(watchedField)""#),
            // A movie Stremio marked watched, only in the history.
            item("tt2", "movie", removed: true, #""lastWatched":"2026-10-07T10:00:00.000Z","timesWatched":1,"flaggedWatched":1"#),
            // A movie left at 95%: watched by the owner's rule, though Stremio didn't mark it.
            item("tt3", "movie", removed: true, #""lastWatched":"2026-10-06T10:00:00.000Z","timeOffset":5700000,"duration":6000000"#),
            // A movie left a third of the way, in the Library.
            item("tt4", "movie", removed: false, #""lastWatched":"2026-10-05T10:00:00.000Z","timeOffset":2000000,"duration":6000000"#),
            // Anime from Kitsu, in the Library, never played.
            item("kitsu:9", "anime", removed: false, ""),
            // Another plugin's own ids, and a channel: left out.
            item("some-plugin-title-1", "series", removed: false, #""lastWatched":"2026-10-04T10:00:00.000Z""#),
            item("yt_id:UC1", "channel", removed: false, ""),
        ].joined(separator: ",")
        let all = try JSONDecoder().decode([StremioLibrary.Item].self, from: Data("[\(items)]".utf8))
        // The episodes as the engine gives them: newest first, a special among them.
        let plan = StremioLibrary.plan(
            all, details: ["tt1": try series("tt1", [(1, 4), (1, 3), (0, 1), (1, 2), (1, 1)])])
        XCTAssertEqual(plan.leftOut, 2)
        XCTAssertEqual(plan.watchlist.map(\.id), ["tt1", "tt4", "kitsu:9"])  // the latest watched first
        XCTAssertEqual(plan.watchlist.last?.anime, true)
        XCTAssertEqual(plan.watchlist.last?.type, "series")
        XCTAssertEqual(plan.watchlist.first?.genres, ["Drama"])  // from the plugin: Stremio has none
        XCTAssertEqual(Set(plan.finished.keys), ["tt1:1:1", "tt1:1:2", "tt1:1:3", "tt2", "tt3"])
        XCTAssertEqual([plan.moviesWatched, plan.episodesWatched, plan.seriesWithEpisodes], [2, 3, 1])
        // Where each was left: the series on episode 4 at 25 minutes, the movie a third in.
        XCTAssertEqual(plan.history.map(\.id), ["tt1", "tt2", "tt3", "tt4"])
        let show = try XCTUnwrap(plan.history.first)
        XCTAssertEqual(show.episodeId, "tt1:1:4")
        XCTAssertEqual(show.seconds, 1500)
        XCTAssertEqual(show.detail, "S1 E4 · E4")
        XCTAssertTrue(show.isUnfinished)
        XCTAssertEqual(plan.partWay, 2)  // the series and tt4; tt3 is past nine tenths
        // Without the series' episodes, its watched ones can't be lined up: said, not guessed.
        XCTAssertEqual(StremioLibrary.plan(all, details: [:]).episodesUnread, 1)
    }

    func testAnImportAddsToWhatsWatchedAndTakesNothingAway() {
        let older = Date(timeIntervalSince1970: 1_000_000)
        let newer = older.addingTimeInterval(3600)
        var history = WatchHistory()
        var here = WatchHistory.Entry(id: "tt1", kind: .series, name: "Show", seconds: 100, length: 3000, at: newer)
        here.episodeId = "tt1:1:5"
        history.watched(WatchHistory.Entry(id: "tt9", kind: .movie, name: "Kept", at: older))
        history.watched(here)
        var stale = WatchHistory.Entry(id: "tt1", kind: .series, name: "Show", seconds: 900, length: 3000, at: older)
        stale.episodeId = "tt1:1:2"
        history.merge([stale, WatchHistory.Entry(id: "tt2", kind: .movie, name: "New", at: older.addingTimeInterval(60))])
        // The later of the two kept (episode 5 here, not Stremio's episode 2); the new one added;
        // nothing taken away; the latest first.
        XCTAssertEqual(history.entries.map(\.id), ["tt1", "tt2", "tt9"])
        XCTAssertEqual(history.entries.first?.episodeId, "tt1:1:5")
    }
}
