import XCTest

@testable import MusicOrganizerKit

/// Download Season on a series' page (2026-11-06): which qualities are offered, which
/// episodes are still wanted, which way each is taken by, what's said, and the queue of
/// episodes waiting for another of the same torrent. Every name and address is made up.
final class SeasonDownloadTests: XCTestCase {
    private let silo = MediaItem(
        id: "tt0000001", type: "series", name: "Made-up Show", poster: nil, posterShape: "poster", year: "2023",
        rating: nil, genres: [])
    private let movies = URL(fileURLWithPath: "/Scratch/Movies", isDirectory: true)

    private func episode(_ number: Int, day: String? = "2026-01-01", season: Int = 1) -> MediaDetails.Video {
        MediaDetails.Video(
            id: "tt0000001:\(season):\(number)", title: "Part \(number)", season: season, episode: number,
            released: day.map { "\($0)T01:00:00.000Z" })
    }

    private func stream(_ quality: String?, kind: String = "torrent", hash: String? = "ab", file: Int? = nil) -> String {
        let quality = quality.map { "\"\($0)\"" } ?? "null"
        let hash = hash.map { "\"\($0)\"" } ?? "null"
        let file = file.map(String.init) ?? "null"
        let url = kind == "url" ? "\"https://films.invalid/\(UUID().uuidString).mp4\"" : "null"
        return #"{"kind":"\#(kind)","name":"x","quality":\#(quality),"url":\#(url),"info_hash":\#(hash),"file_index":\#(file),"trackers":[]}"#
    }

    private func answer(_ sources: [(String, [String])]) throws -> StreamsAnswer {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let made = try sources.map { id, streams in
            try decoder.decode(
                StreamsAnswer.Source.self,
                from: Data(#"{"addon_id":"\#(id)","addon":"\#(id)","streams":[\#(streams.joined(separator: ","))]}"#.utf8))
        }
        return StreamsAnswer(sources: made, problems: [])
    }

    // MARK: the qualities offered

    func testEachQualityOnceBestFirstByTheOwnersOrderOfPlugins() throws {
        let ways = try answer([
            ("A", [stream("720p"), stream("1080p"), stream(nil, kind: "youtube", hash: nil)]),
            ("B", [stream("1080p"), stream("4k"), stream("cam")]),
        ])
        let choices = SeasonDownload.choices(in: ways)
        XCTAssertEqual(choices.map(\.stream.quality), ["4k", "1080p", "720p", "cam"])
        XCTAssertEqual(choices.map(\.addonId), ["B", "A", "A", "B"])  // 1080p: A's, higher in the list
    }

    func testOnlyWhatCanBeDownloadedIsOffered() throws {
        let ways = try answer([("A", [stream("1080p", hash: nil), stream("720p", kind: "url", hash: nil)])])
        XCTAssertEqual(SeasonDownload.choices(in: ways).map(\.stream.quality), ["720p"])
    }

    // MARK: each episode's way

    func testTheSameQualityFromTheSamePluginElseTheNextPluginElseNone() throws {
        let chosen = NextEpisode.Played(addonId: "B", quality: "1080p")
        let both = try answer([("A", [stream("1080p")]), ("B", [stream("720p"), stream("1080p", hash: "bb")])])
        XCTAssertEqual(SeasonDownload.way(in: both, like: chosen)?.addonId, "B")
        XCTAssertEqual(SeasonDownload.way(in: both, like: chosen)?.stream.infoHash, "bb")
        let elsewhere = try answer([("A", [stream("720p")]), ("C", [stream("1080p")])])
        XCTAssertEqual(SeasonDownload.way(in: elsewhere, like: chosen)?.addonId, "C")
        let none = try answer([("B", [stream("720p")])])
        XCTAssertNil(SeasonDownload.way(in: none, like: chosen))
        // A way that can't be downloaded isn't taken, whatever its quality.
        let unkeepable = try answer([("B", [stream("1080p", hash: nil)])])
        XCTAssertNil(SeasonDownload.way(in: unkeepable, like: chosen))
    }

    // MARK: the episodes wanted

    func testOutNotHereAndNotOnItsWay() {
        let episodes = [episode(3), episode(1), episode(2), episode(4, day: "2026-12-01"), episode(5, day: nil)]
        let here = KeptShelf(
            [KeptFile(
                VideoFiles.File(
                    url: movies.appendingPathComponent("Series/Made-up Show/Season 1/Made-up Show S01E02.mkv"),
                    bytes: 1, added: Date()), in: movies)],
            titles: KeptTitles()
        ).series.first
        XCTAssertNotNil(here)
        let coming = PendingKeeps.Keep(infoHash: "ab", trackers: [], of: silo, part: episode(3))
        let wanted = SeasonDownload.wanted(episodes, of: silo, here: here, coming: [coming], today: "2026-11-06")
        // 2 is here, 3 is on its way, 4 isn't out yet; 5 has no day, so it's taken as out.
        XCTAssertEqual(wanted.map(\.episode), [1, 5])
    }

    func testAKeepIsKnownAsItsEpisode() {
        let made = PendingKeeps.Keep(infoHash: "ab", trackers: [], of: silo, part: episode(2))
        XCTAssertTrue(made.isEpisode(episode(2), of: silo))
        XCTAssertFalse(made.isEpisode(episode(3), of: silo))
        XCTAssertFalse(made.isEpisode(episode(2, season: 2), of: silo))
        let other = MediaItem(
            id: "tt0000002", type: "series", name: "Made-up Show", poster: nil, posterShape: "poster", year: nil,
            rating: nil, genres: [])
        XCTAssertFalse(made.isEpisode(episode(2), of: other))  // the same name, another title
        // One from before the title was noted is known by its show's name, in any capitals.
        let older = PendingKeeps.Keep(
            infoHash: "ab", trackers: [], title: "Part 2", kind: "series", show: "made-up SHOW", season: 1, episode: 2)
        XCTAssertTrue(older.isEpisode(episode(2), of: silo))
        // A film isn't an episode of anything.
        XCTAssertFalse(PendingKeeps.Keep(infoHash: "ab", trackers: [], title: "Film").isEpisode(episode(2), of: silo))
    }

    // MARK: what's said

    func testWhatsSaid() {
        XCTAssertEqual(
            SeasonDownload.said(started: 7, missing: [episode(5)], season: 2, quality: "1080p"),
            "Downloading 7 episodes of Season 2. Not found in 1080p: E05.")
        XCTAssertEqual(
            SeasonDownload.said(started: 1, missing: [], season: 1, quality: "4k"), "Downloading 1 episode of Season 1.")
        XCTAssertEqual(
            SeasonDownload.said(started: 0, missing: [episode(1)], season: 0, quality: nil),
            "None of Specials's episodes could be downloaded in Unknown.")
        XCTAssertTrue(
            SeasonDownload.said(started: 3, waiting: 2, missing: [], season: 1, quality: "720p")
                .contains("one after another"))
    }

    // MARK: the queue of episodes waiting for their torrent

    func testEpisodesOfOneTorrentWaitInTurn() {
        let defaults = UserDefaults(suiteName: "SeasonQueueTests-\(UUID().uuidString)")!
        var queue = SeasonQueue()
        let two = PendingKeeps.Keep(infoHash: "AB", trackers: [], fileIndex: 2, of: silo, part: episode(2))
        let three = PendingKeeps.Keep(infoHash: "ab", trackers: [], fileIndex: 3, of: silo, part: episode(3))
        let elsewhere = PendingKeeps.Keep(infoHash: "cd", trackers: [], of: silo, part: episode(4))
        queue.add(two)
        queue.add(three)
        queue.add(two)  // not twice
        queue.add(elsewhere)
        XCTAssertEqual(queue.waiting.count, 3)
        XCTAssertNotEqual(SeasonQueue.slot(two), SeasonQueue.slot(three))  // one torrent, told apart
        XCTAssertEqual(queue.torrents, ["ab", "cd"])
        queue.save(to: defaults)
        var again = SeasonQueue.load(from: defaults)
        XCTAssertEqual(again, queue)
        // In turn, by the torrent (in any case): two, then three, then nothing.
        XCTAssertEqual(again.next(of: "ab")?.episode, 2)
        XCTAssertEqual(again.next(of: "AB")?.episode, 3)
        XCTAssertNil(again.next(of: "ab"))
        again.remove(slot: SeasonQueue.slot(elsewhere))
        XCTAssertEqual(again.waiting, [])
        XCTAssertEqual(SeasonQueue.load(from: UserDefaults(suiteName: "SeasonQueueTests-empty-\(UUID().uuidString)")!), SeasonQueue())
    }
}
