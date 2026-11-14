import XCTest

@testable import MusicOrganizerKit

/// Series read together, and a re-read that says what it said before (2026-11-14, the speed
/// work ported from the iPad): `SeriesProgress.learning` and `sameToShow`.
final class SeriesReadTogetherTests: XCTestCase {
    private let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    /// Episodes of a series, "<id>:<season>:<episode>", each out on its day.
    private func episodes(_ id: String, _ numbers: [(Int, Int, String)]) throws -> [MediaDetails.Video] {
        let list = numbers.map { season, episode, day in
            #"{"id":"\#(id):\#(season):\#(episode)","title":"E\#(episode)","season":\#(season),"episode":\#(episode),"released":"\#(day)T01:00:00.000Z"}"#
        }
        return try decoder.decode([MediaDetails.Video].self, from: Data(("[" + list.joined(separator: ",") + "]").utf8))
    }

    private func day(_ text: String) -> Date {
        ISO8601DateFormatter().date(from: text + "T12:00:00Z")!
    }

    /// Several series worked out on a copy come out exactly as they do one at a time: the
    /// same thing, off the main thread.
    func testSeriesReadTogetherSayTheSameAsOneAtATime() throws {
        let now = day("2026-11-14")
        var read: [(id: String, episodes: [MediaDetails.Video], listed: Bool)] = []
        for number in 1...3 {
            let id = "s\(number)"
            read.append(
                (
                    id: id,
                    episodes: try episodes(id, [(1, 1, "2026-10-01"), (1, 2, "2026-10-08"), (1, 3, "2026-10-15")]),
                    listed: number != 2
                ))
        }
        var done = Finished()
        done.mark("s1:1:1", watched: true, now: now)
        done.mark("s2:1:1", watched: true, now: now)

        var oneAtATime = SeriesProgress()
        for one in read {
            oneAtATime.learned(one.id, episodes: one.episodes, finished: done, now: now, listed: one.listed)
        }
        let together = SeriesProgress().learning(read, finished: done, now: now)
        XCTAssertEqual(together, oneAtATime)
        XCTAssertTrue(together.sameToShow(as: oneAtATime))
    }

    /// A series read again with the same episodes changes only when it was read, so the pages
    /// aren't drawn again for it; a new episode, or one watched since, is a real change.
    func testAReReadThatFindsNothingNewIsTheSameToShow() throws {
        let (first, later) = (day("2026-11-14"), day("2026-11-15"))
        let shown = try episodes("s", [(1, 1, "2026-10-01"), (1, 2, "2026-10-08")])
        var done = Finished()
        done.mark("s:1:1", watched: true, now: first)
        let before = SeriesProgress().learning([(id: "s", episodes: shown, listed: true)], finished: done, now: first)

        // The same episodes a day later: only its reading time differs.
        let again = before.learning([(id: "s", episodes: shown, listed: true)], finished: done, now: later)
        XCTAssertTrue(again.sameToShow(as: before))
        XCTAssertNotEqual(again, before)  // `read` moved on, which is why it's kept
        XCTAssertEqual(again.series["s"]?.read, later)

        // An episode out since: a real change, and the pages are drawn again for it.
        let withMore = try episodes("s", [(1, 1, "2026-10-01"), (1, 2, "2026-10-08"), (1, 3, "2026-11-01")])
        let fresh = before.learning([(id: "s", episodes: withMore, listed: true)], finished: done, now: later)
        XCTAssertFalse(fresh.sameToShow(as: before))

        // One watched since: a real change too.
        done.mark("s:1:2", watched: true, now: later)
        let caught = before.learning([(id: "s", episodes: shown, listed: true)], finished: done, now: later)
        XCTAssertFalse(caught.sameToShow(as: before))
    }

    /// A series the owner didn't have before is never "the same to show".
    func testASeriesNotThereBeforeIsAChange() throws {
        let now = day("2026-11-14")
        let one = try episodes("s1", [(1, 1, "2026-10-01")])
        let two = try episodes("s2", [(1, 1, "2026-10-01")])
        let before = SeriesProgress().learning([(id: "s1", episodes: one, listed: true)], finished: Finished(), now: now)
        let after = before.learning([(id: "s2", episodes: two, listed: true)], finished: Finished(), now: now)
        XCTAssertFalse(after.sameToShow(as: before))
        XCTAssertFalse(before.sameToShow(as: after))
    }
}
