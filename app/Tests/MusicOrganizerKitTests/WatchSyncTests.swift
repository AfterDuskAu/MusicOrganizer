import XCTest

@testable import MusicOrganizerKit

/// What a profile and a paired video app tell each other of what's watched (2026-10-16).
final class WatchSyncTests: XCTestCase {
    private func show(_ id: String) -> MediaItem {
        MediaItem(id: id, type: "series", name: "Show \(id)", poster: nil, posterShape: "poster", year: nil, rating: nil, genres: ["Drama"])
    }

    private func at(_ minutes: Double) -> Date { Date(timeIntervalSince1970: 1_800_000_000 + minutes * 60) }

    func testWhatsSentAndWhatsTakenAddUpAndNothingIsTakenAway() throws {
        // The computer: the series on its Watchlist, episode 1 watched, episode 2 at minute 5.
        var macList = Watchlist([show("tt1")])
        var macDone = Finished()
        macDone.mark("tt1:1:1", watched: true, now: at(0))
        var macHistory = WatchHistory()
        var onTwo = WatchHistory.Entry(
            id: "tt1", kind: .series, name: "Show tt1", detail: "S1 E2", item: show("tt1"), seconds: 300,
            length: 3000, at: at(60))
        onTwo.episodeId = "tt1:1:2"
        macHistory.watched(onTwo)

        // The iPad: on to episode 5 later, episodes 2 to 4 watched there, and another series listed.
        var padList = Watchlist([show("tt2")])
        var padDone = Finished()
        for episode in 2...4 { padDone.mark("tt1:1:\(episode)", watched: true, now: at(100 + Double(episode))) }
        var padHistory = WatchHistory()
        var onFive = WatchHistory.Entry(
            id: "tt1", kind: .series, name: "Show tt1", detail: "S1 E5", item: show("tt1"), seconds: 120,
            length: 3000, at: at(200))
        onFive.episodeId = "tt1:1:5"
        padHistory.watched(onFive)

        // Each sends its own, and each adds the other's (through JSON, as the sync carries it).
        func carried(_ state: WatchSync.State) throws -> WatchSync.State {
            try JSONDecoder().decode(WatchSync.State.self, from: JSONEncoder().encode(state))
        }
        let fromMac = try carried(WatchSync.state(watchlist: macList, finished: macDone, history: macHistory))
        let fromPad = try carried(WatchSync.state(watchlist: padList, finished: padDone, history: padHistory))
        XCTAssertTrue(WatchSync.merge(fromPad, into: &macList, finished: &macDone, history: &macHistory))
        WatchSync.merge(fromMac, into: &padList, finished: &padDone, history: &padHistory)

        for (list, done, history) in [(macList, macDone, macHistory), (padList, padDone, padHistory)] {
            XCTAssertEqual(Set(list.items.map(\.id)), ["tt1", "tt2"])
            XCTAssertEqual(Set(done.days.keys), ["tt1:1:1", "tt1:1:2", "tt1:1:3", "tt1:1:4"])
            XCTAssertEqual(history.entries.first?.episodeId, "tt1:1:5")  // the later place
            XCTAssertEqual(history.entries.first?.seconds, 120)
        }
        XCTAssertEqual(macList.items.map(\.id), ["tt1", "tt2"])  // its own first, the new after
        // Taking the same again changes nothing.
        XCTAssertFalse(WatchSync.merge(fromPad, into: &macList, finished: &macDone, history: &macHistory))
    }

    func testWhatsTakenOffHereStaysOffHere() {
        var list = Watchlist([show("tt1")])
        list.toggle(show("tt1"))  // taken off here
        var done = Finished()
        done.mark("tt1:1:1", watched: true)
        done.mark("tt1:1:1", watched: false)  // marked Not Watched here
        var history = WatchHistory()
        let sent = WatchSync.State(
            watchlist: [WatchSync.Title(show("tt1")), WatchSync.Title(show("tt2"))],
            watched: [.init(id: "tt1:1:1", at: "2026-10-16T01:00:00Z"), .init(id: "tt1:1:2", at: "2026-10-16T01:00:00Z")])
        WatchSync.merge(sent, into: &list, finished: &done, history: &history)
        XCTAssertEqual(list.items.map(\.id), ["tt2"])
        XCTAssertEqual(Set(done.days.keys), ["tt1:1:2"])
        // Put on again here, and watched again here: from then on as any other.
        list.toggle(show("tt1"))
        XCTAssertFalse(list.takenOff.contains("tt1"))
        done.mark("tt1:1:1", watched: true)
        XCTAssertFalse(done.unmarked.contains("tt1:1:1"))
        // Kept on this Mac with the rest.
        let name = "watch-sync-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        list.toggle(show("tt2"))
        list.save(in: defaults)
        XCTAssertEqual(Watchlist.saved(in: defaults).takenOff, ["tt2"])
    }

    func testAChannelsVideosArentSent() {
        var history = WatchHistory()
        history.watched(WatchHistory.Entry(id: "v1", kind: .video, name: "A Video", seconds: 10, length: 100))
        let sent = WatchSync.state(watchlist: Watchlist(), finished: Finished(), history: history)
        XCTAssertTrue(sent.progress.isEmpty)
    }

    func testASeriesCaughtUpOnAnotherDeviceIsCaughtUpHere() {
        let days = ["2026-10-01", "2026-10-08"].enumerated().map { index, day in
            #"{"id":"tt1:1:\#(index + 1)","title":"E","season":1,"episode":\#(index + 1),"released":"\#(day)T01:00:00.000Z"}"#
        }
        let videos = try! JSONDecoder().decode([MediaDetails.Video].self, from: Data("[\(days.joined(separator: ","))]".utf8))
        var progress = SeriesProgress()
        var done = Finished()
        progress.learned("tt1", episodes: videos, finished: done, now: at(0).addingTimeInterval(86_400 * 400))
        done.mark("tt1:1:2", watched: true)
        progress.notice(done)
        XCTAssertTrue(progress.isWatched("tt1", finished: done))
        XCTAssertEqual(progress.series["tt1"]?.caughtUp, "tt1:1:2")
    }

    func testAPlaceSentWithoutItsTitleKeepsOne() {
        var list = Watchlist()
        var done = Finished()
        var history = WatchHistory()
        let sent = WatchSync.State(progress: [
            .init(id: "tt3", type: "movie", name: "Film Three", poster: nil, episode: nil, detail: nil,
                  seconds: 600, length: 6000, at: "2026-10-16T05:00:00Z", title: nil)
        ])
        WatchSync.merge(sent, into: &list, finished: &done, history: &history)
        XCTAssertEqual(history.entries.first?.item?.name, "Film Three")
        // And it's sent on from here.
        XCTAssertEqual(WatchSync.state(watchlist: list, finished: done, history: history).progress.map(\.id), ["tt3"])
    }

    /// 2026-11-10: each series' new episodes are sent with the watching, so a device set up
    /// later shows the same New Episodes (it takes everything out as seen when it first
    /// reads a series).
    func testNewEpisodesAreSentWithTheWatching() throws {
        let many = (1...250).map { "tt9:2:\($0)" }
        let progress = SeriesProgress([
            "tt5": .init(latest: "tt5:1:4", caughtUp: nil, read: at(30), seen: "tt5:1:2",
                         fresh: ["tt5:1:3", "tt5:1:4"], freshAt: at(30)),
            "tt6": .init(latest: "tt6:1:2", caughtUp: nil, read: at(50), seen: "tt6:1:1",
                         fresh: ["tt6:1:2"], freshAt: at(50)),
            "tt7": .init(latest: "tt7:1:1", caughtUp: nil, read: at(10), seen: "", fresh: ["tt7:1:1"], freshAt: at(10)),
            "tt8": .init(latest: "tt8:1:1", caughtUp: "tt8:1:1", read: at(10), seen: "tt8:1:1"),
            "tt9": .init(latest: "tt9:2:250", caughtUp: nil, read: at(5), seen: "tt9:1:9", fresh: many, freshAt: at(5)),
        ])
        var done = Finished()
        done.mark("tt5:1:3", watched: true, now: at(40))
        done.mark("tt7:1:1", watched: true, now: at(40))
        let sent = WatchSync.state(watchlist: Watchlist(), finished: done, history: WatchHistory(), series: progress)
        // Only what isn't watched, the series whose latest came out last first, and no more
        // than the computer takes of one series.
        XCTAssertEqual(sent.series, [
            .init(id: "tt6", fresh: ["tt6:1:2"], freshAt: WatchSync.stamp(at(50))),
            .init(id: "tt5", fresh: ["tt5:1:4"], freshAt: WatchSync.stamp(at(30))),
            .init(id: "tt9", fresh: Array(many.prefix(WatchSync.mostFresh)), freshAt: WatchSync.stamp(at(5))),
        ])
        // Without the series, none is said (as a side from before it).
        let without = WatchSync.state(watchlist: Watchlist(), finished: done, history: WatchHistory())
        XCTAssertNil(without.series)
        let written = String(decoding: try JSONEncoder().encode(without), as: UTF8.self)
        XCTAssertFalse(written.contains("series"))
        // Read either way; and taking a device's changes nothing here.
        let old = try JSONDecoder().decode(WatchSync.State.self, from: Data(#"{"watchlist":[],"watched":[],"progress":[]}"#.utf8))
        XCTAssertNil(old.series)
        let back = try JSONDecoder().decode(WatchSync.State.self, from: try JSONEncoder().encode(sent))
        XCTAssertEqual(back.series, sent.series)
        var list = Watchlist()
        var finished = done
        var history = WatchHistory()
        XCTAssertFalse(WatchSync.merge(back, into: &list, finished: &finished, history: &history))
    }
}
