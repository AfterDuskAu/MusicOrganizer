import XCTest

@testable import MusicOrganizerKit

final class NextEpisodeTests: XCTestCase {
    private func decode<T: Decodable>(_ json: String, as type: T.Type) throws -> T {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(type, from: Data(json.utf8))
    }

    private func videos(_ numbers: [(Int, Int, String)]) throws -> [MediaDetails.Video] {
        let list = numbers.map { season, episode, day in
            #"{"id":"s:\#(season):\#(episode)","title":"E\#(episode)","season":\#(season),"#
                + #""episode":\#(episode),"released":"\#(day)T00:00:00.000Z"}"#
        }
        return try decode("[" + list.joined(separator: ",") + "]", as: [MediaDetails.Video].self)
    }

    private func answer(_ sources: [(String, [(String, String?)])]) throws -> StreamsAnswer {
        let said = sources.map { addon, streams in
            let list = streams.map { kind, quality in
                #"{"kind":"\#(kind)","url":"http://127.0.0.1/x","trackers":[],"#
                    + #""quality":\#(quality.map { "\"\($0)\"" } ?? "null")}"#
            }
            return #"{"addon_id":"\#(addon)","addon":"\#(addon)","streams":[\#(list.joined(separator: ","))]}"#
        }
        return try decode(
            #"{"sources":[\#(said.joined(separator: ","))],"problems":[]}"#, as: StreamsAnswer.self)
    }

    func testTheNextEpisodeIsTheNextInItsSeason() throws {
        // Out of order, as an add-on may give them.
        let all = try videos([(1, 3, "2026-01-03"), (1, 1, "2026-01-01"), (1, 2, "2026-01-02")])
        XCTAssertEqual(NextEpisode.after("s:1:1", in: all)?.id, "s:1:2")
        XCTAssertEqual(NextEpisode.after("s:1:2", in: all)?.id, "s:1:3")
    }

    func testAfterASeasonsLastComesTheNextSeasonsFirst() throws {
        let all = try videos([(1, 1, "2026-01-01"), (1, 2, "2026-01-02"), (3, 2, "2026-03-02"), (3, 1, "2026-03-01")])
        XCTAssertEqual(NextEpisode.after("s:1:2", in: all)?.id, "s:3:1")
    }

    func testNothingComesAfterTheLastOrASpecialOrOneNotKnown() throws {
        let all = try videos([(0, 1, "2025-12-25"), (1, 1, "2026-01-01"), (1, 2, "2026-01-02")])
        XCTAssertNil(NextEpisode.after("s:1:2", in: all))
        XCTAssertNil(NextEpisode.after("s:0:1", in: all))  // a special leads nowhere
        XCTAssertNil(NextEpisode.after("s:9:9", in: all))
    }

    func testAnEpisodeThatIsntOutYetIsntPlayed() throws {
        let all = try videos([(1, 1, "2026-10-05"), (1, 2, "2026-10-19")])
        XCTAssertNil(NextEpisode.after("s:1:1", in: all, today: "2026-10-12"))
        XCTAssertEqual(NextEpisode.after("s:1:1", in: all, today: "2026-10-19")?.id, "s:1:2")
        XCTAssertEqual(NextEpisode.after("s:1:1", in: all)?.id, "s:1:2")
    }

    func testTheSameQualityFromTheSameSourceComesFirst() throws {
        let found = try answer([
            ("a", [("url", "720p"), ("url", "1080p")]), ("b", [("url", "1080p")]),
        ])
        let way = NextEpisode.way(in: found, like: NextEpisode.Played(addonId: "b", quality: "1080p"))
        XCTAssertEqual(way?.addonId, "b")
        XCTAssertEqual(way?.stream.quality, "1080p")
    }

    func testThenTheNextSourceThatHasThatQuality() throws {
        // The source it was played from has it only in 720p now.
        let found = try answer([
            ("a", [("url", "720p")]), ("b", [("url", "720p"), ("torrent", "1080p")]), ("c", [("url", "1080p")]),
        ])
        let way = NextEpisode.way(in: found, like: NextEpisode.Played(addonId: "a", quality: "1080p"))
        XCTAssertEqual(way?.addonId, "b")
        XCTAssertEqual(way?.stream.kind, "torrent")
    }

    func testOtherwiseNothingIsChosenAndThePageAsks() throws {
        let found = try answer([("a", [("url", "720p")]), ("b", [("youtube", "1080p"), ("other", "1080p")])])
        // 1080p is only to be had as something the film player doesn't play.
        XCTAssertNil(NextEpisode.way(in: found, like: NextEpisode.Played(addonId: "a", quality: "1080p")))
        XCTAssertNil(
            NextEpisode.way(
                in: StreamsAnswer(sources: [], problems: []),
                like: NextEpisode.Played(addonId: "a", quality: "1080p")))
    }

    func testAQualityNobodyNamedIsAQualityToo() throws {
        let found = try answer([("a", [("url", "720p"), ("url", nil)])])
        let way = NextEpisode.way(in: found, like: NextEpisode.Played(addonId: "a", quality: nil))
        XCTAssertNil(way?.stream.quality)
        XCTAssertNotNil(way)
    }
}
