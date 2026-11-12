import XCTest

@testable import MusicOrganizerKit

/// A film that won't start is let go of in a sensible time, said plainly, and another way to
/// play it is offered (the owner, 2026-11-11: "finding film" for minutes, then "a film couldn't
/// be opened"). Made-up ways to play only.
final class FilmStartTests: XCTestCase {
    private func status(
        _ state: String, peers: Int = 0, rate: Int = 0, progress: Double = 0
    ) -> TorrentStatus {
        TorrentStatus(
            state: state, peers: peers, bytesPerSecond: rate, progress: progress, seeds: 0, swarmSeeds: 0,
            swarmPeers: peers)
    }

    func testATorrentNobodySharesIsLetGoAfterFortyFiveSeconds() {
        let nobody = status("finding")
        XCTAssertEqual(FilmStart.verdict(torrent: nobody, waited: 10), .waiting)
        XCTAssertEqual(FilmStart.verdict(torrent: nobody, waited: 44), .waiting)
        XCTAssertEqual(FilmStart.verdict(torrent: nobody, waited: 45), .stuck(FilmStart.nobody))
    }

    /// Listed by a tracker (`seeds`/`others`), with `connected` connected and `answered` of them
    /// having any of the film.
    private func listed(_ seeds: Int, _ others: Int, connected: Int = 0, answered: Int = 0) -> TorrentStatus {
        TorrentStatus(
            state: "finding", peers: connected, bytesPerSecond: 0, progress: 0, seeds: 0, swarmSeeds: seeds,
            swarmPeers: seeds + others, answered: answered)
    }

    func testSharersListedButNoneAnsweringIsSaidSoAndLetGoAfterFortyFiveSeconds() {
        // The owner, 2026-11-12, on a video app: Count said 12/7, and Play found nobody.
        let silent = listed(12, 7)
        XCTAssertEqual(FilmStart.verdict(torrent: silent, waited: 44), .waiting)
        XCTAssertEqual(
            FilmStart.verdict(torrent: silent, waited: 45),
            .stuck("12 seeders and 7 leechers are listed for this one, but none of them answered, so it can't start."))
        XCTAssertEqual(
            FilmStart.nobody(listed(1, 0)), "1 seeder is listed for this one, but none of them answered, so it can't start.")
        XCTAssertEqual(FilmStart.nobody(listed(0, 0)), FilmStart.nobody)
    }

    func testThoseConnectedWithNothingOfTheFilmDontCountAsAnswering() {
        let junk = listed(0, 8, connected: 8, answered: 0)
        XCTAssertEqual(FilmStart.verdict(torrent: junk, waited: 45), .stuck(FilmStart.nobody(junk)))
        XCTAssertEqual(FilmStart.line(junk, waited: 12), "Finding the film… 0:12 · 0/8 listed, none answered yet")
        XCTAssertEqual(FilmStart.verdict(torrent: listed(0, 8, connected: 8, answered: 1), waited: 60), .waiting)
        // An engine that doesn't say who answered: everyone connected, as before.
        XCTAssertEqual(
            FilmStart.verdict(torrent: TorrentStatus(state: "finding", peers: 3, bytesPerSecond: 0, progress: 0), waited: 60),
            .waiting)
    }

    func testSharersSaysWhoIsListedAndWhoAnswered() {
        XCTAssertEqual(Sharers.answered(0), "none answered")
        XCTAssertEqual(Sharers.answered(3), "3 answered")
        XCTAssertEqual(Sharers.listed(seeds: 12, others: 7), "12 seeders and 7 leechers")
        XCTAssertNil(Sharers.listed(seeds: 0, others: 0))
    }

    /// The person and its number beside a way to play (2026-11-12): its trackers' seeders, or
    /// while its film plays, the larger of those and who the engine knows of.
    func testTheSeedersShownAreTheTrackersOrTheEnginesWhicheverIsMore() {
        XCTAssertNil(Sharers.seeds(asked: nil, live: nil))
        XCTAssertEqual(Sharers.seeds(asked: 151, live: nil), 151)
        XCTAssertEqual(Sharers.seeds(asked: 0, live: nil), 0)
        let playing = TorrentStatus(
            state: "fetching", peers: 120, bytesPerSecond: 0, progress: 0.1, seeds: 91,
            swarmSeeds: 91, swarmPeers: 122)
        XCTAssertEqual(Sharers.seeds(asked: 6, live: playing), 91)
        XCTAssertEqual(Sharers.seeds(asked: 251, live: playing), 251)
        XCTAssertEqual(Sharers.seeds(asked: nil, live: playing), 91)
        // An engine that doesn't say who has the torrent: those connected with all of it.
        let older = TorrentStatus(state: "fetching", peers: 9, bytesPerSecond: 0, progress: 0, seeds: 7)
        XCTAssertEqual(Sharers.seeds(asked: 3, live: older), 7)
    }

    /// Asked of the trackers once in ten minutes, each torrent once however many ways have it.
    func testTheTrackersAreAskedAboutEachTorrentOnceInTenMinutes() {
        var book = SwarmBook()
        let start = Date(timeIntervalSince1970: 1_000_000)
        let film = String(repeating: "ab", count: 20)
        let other = String(repeating: "cd", count: 20)
        XCTAssertEqual(book.toAsk([film.uppercased(), film, other], now: start), [film, other])
        XCTAssertTrue(book.isAsking(film.uppercased()))
        XCTAssertNil(book.seeds(of: film, now: start))
        XCTAssertEqual(book.toAsk([film], now: start), [])  // being asked already
        // Only the film was answered about: the other's trackers said nothing.
        book.heard([TrackerCount(infoHash: film.uppercased(), seeds: 151, peers: 160)], asked: [film, other], now: start)
        XCTAssertFalse(book.isAsking(film))
        XCTAssertEqual(book.seeds(of: film, now: start), 151)
        XCTAssertNil(book.seeds(of: other, now: start))
        // Shown again within ten minutes: not asked again, for either.
        XCTAssertEqual(book.toAsk([film, other], now: start.addingTimeInterval(599)), [])
        XCTAssertEqual(book.seeds(of: film, now: start.addingTimeInterval(599)), 151)
        // After that: asked afresh, and the old answer isn't believed meanwhile.
        let later = start.addingTimeInterval(SwarmBook.keptFor)
        XCTAssertNil(book.seeds(of: film, now: later))
        XCTAssertEqual(Set(book.toAsk([film, other], now: later)), [film, other])
        // The engine couldn't be asked: asked again the next time they're shown.
        book.failed([film, other])
        XCTAssertFalse(book.isAsking(film))
        XCTAssertEqual(Set(book.toAsk([film, other], now: later)), [film, other])
    }

    func testWithSomeoneFoundItsDetailsAreGivenLonger() {
        let found = status("finding", peers: 3)
        XCTAssertEqual(FilmStart.verdict(torrent: found, waited: 60), .waiting)
        XCTAssertEqual(FilmStart.verdict(torrent: found, waited: 90), .stuck(FilmStart.noDetails))
    }

    func testATorrentNotHeardOfYetIsGivenTheLongerTime() {
        XCTAssertEqual(FilmStart.verdict(torrent: nil, waited: 60), .waiting)
        XCTAssertEqual(FilmStart.verdict(torrent: nil, waited: 90), .stuck(FilmStart.noDetails))
    }

    func testDetailsWithNothingOfTheFilmComingIsLetGo() {
        // Its details came (from what the engine kept of it, say), but nobody's there to send it.
        XCTAssertEqual(FilmStart.verdict(torrent: status("fetching"), waited: 60), .waiting)
        XCTAssertEqual(FilmStart.verdict(torrent: status("fetching"), waited: 90), .stuck(FilmStart.nobody))
        // Someone's there, but not one piece has come in all that time.
        XCTAssertEqual(FilmStart.verdict(torrent: status("fetching", peers: 4), waited: 90), .stuck(FilmStart.nobody))
    }

    func testAFilmThatIsArrivingIsNeverLetGo() {
        // Slowly, but it's coming: however long it takes to start.
        XCTAssertEqual(FilmStart.verdict(torrent: status("fetching", peers: 1, rate: 40_000), waited: 600), .waiting)
        // A pause in what's arriving, with someone there and some of it here already.
        XCTAssertEqual(
            FilmStart.verdict(torrent: status("fetching", peers: 2, rate: 0, progress: 0.02), waited: 600), .waiting)
        XCTAssertEqual(FilmStart.verdict(torrent: status("complete", progress: 1), waited: 600), .waiting)
    }

    func testLookingThroughWhatsHereIsWaitedFor() {
        // The engine looking through what's here of a film played before: it says how far, and
        // the film opens when it's done, however long that takes.
        let checking = status("checking", progress: 0.4)
        XCTAssertEqual(FilmStart.verdict(torrent: checking, waited: 600), .waiting)
        XCTAssertEqual(FilmStart.line(checking, waited: 30), checking.line)
    }

    func testAWebAddressIsGivenNinetySeconds() {
        XCTAssertEqual(FilmStart.verdict(address: 89), .waiting)
        XCTAssertEqual(FilmStart.verdict(address: 90), .stuck(FilmStart.noAnswer))
    }

    func testThePlayersLineSaysHowLongAndWhoIsFound() {
        XCTAssertEqual(FilmStart.line(nil, waited: 3), "Finding the film… 0:03")
        XCTAssertEqual(FilmStart.line(status("finding"), waited: 32), "Finding the film… 0:32 · nobody sharing it found yet")
        XCTAssertEqual(FilmStart.line(status("finding", peers: 1), waited: 65), "Finding the film… 1:05 · 1 sharing it answered")
        XCTAssertEqual(FilmStart.line(status("finding", peers: 3), waited: 5), "Finding the film… 0:05 · 3 sharing it answered")
        // Its details here: the torrent's own line.
        let arriving = TorrentStatus(
            state: "fetching", peers: 6, bytesPerSecond: 4_600_000, progress: 0.09, seeds: 2, swarmSeeds: 2,
            swarmPeers: 6)
        XCTAssertEqual(FilmStart.line(arriving, waited: 20), arriving.line)
    }

    // MARK: another way

    private func stream(
        _ kind: String, _ quality: String?, url: String? = nil, videoId: String? = nil, hash: String? = nil,
        file: Int? = nil
    ) -> MediaStream {
        MediaStream(
            kind: kind, name: nil, title: nil, quality: quality, size: nil, url: url, videoId: videoId,
            infoHash: hash, fileIndex: file, trackers: [])
    }

    private func torrent(_ hash: Character, _ quality: String?) -> MediaStream {
        stream("torrent", quality, hash: String(repeating: hash, count: 40), file: 0)
    }

    private func answer(_ sources: [(String, [MediaStream])]) -> StreamsAnswer {
        StreamsAnswer(
            sources: sources.map { StreamsAnswer.Source(addonId: $0.0, addon: $0.0.uppercased(), streams: $0.1) },
            problems: [])
    }

    func testAnotherWayIsTheSameQualityFromTheSamePluginFirst() {
        let ways = answer([
            ("a", [torrent("1", "720p"), torrent("2", "1080p"), torrent("3", "720p")]),
            ("b", [torrent("4", "720p")]),
        ])
        let tried: Set = [torrent("1", "720p").key]
        let found = FilmStart.another(in: ways, tried: tried, like: .init(addonId: "a", quality: "720p"))
        XCTAssertEqual(found?.stream.key, torrent("3", "720p").key)
    }

    func testThenThatQualityFromTheNextPluginThenAnyOther() {
        let ways = answer([
            ("a", [torrent("1", "720p"), torrent("2", "1080p")]),
            ("b", [torrent("4", "720p")]),
        ])
        let like = NextEpisode.Played(addonId: "a", quality: "720p")
        var tried: Set = [torrent("1", "720p").key]
        XCTAssertEqual(FilmStart.another(in: ways, tried: tried, like: like)?.stream.key, torrent("4", "720p").key)
        tried.insert(torrent("4", "720p").key)
        // None of that quality left: the first other way on its page.
        XCTAssertEqual(FilmStart.another(in: ways, tried: tried, like: like)?.stream.key, torrent("2", "1080p").key)
        tried.insert(torrent("2", "1080p").key)
        XCTAssertNil(FilmStart.another(in: ways, tried: tried, like: like))
    }

    func testOnlyWhatTheFilmPlayerPlaysIsOffered() {
        let special = stream("url_special", "720p", url: "http://127.0.0.1/a")
        let video = stream("youtube", "720p", videoId: "abcdefghijk")
        let plain = stream("url", "480p", url: "http://127.0.0.1/b")
        let ways = answer([("a", [special, video, plain])])
        XCTAssertEqual(FilmStart.another(in: ways, tried: [], like: .init(addonId: "a", quality: "720p"))?.stream, plain)
        // A film played with no rule to go by (from Continue Watching, its plugin not known).
        XCTAssertEqual(FilmStart.another(in: ways, tried: [], like: nil)?.stream, plain)
    }

    func testAWayIsKnownByItsTorrentAndFileOrItsAddress() {
        let hash = String(repeating: "A", count: 40)
        // Two files of one torrent are two ways; the id's case doesn't matter.
        XCTAssertEqual(stream("torrent", nil, hash: hash, file: 3).key, "torrent:\(hash.lowercased()):3")
        XCTAssertNotEqual(stream("torrent", nil, hash: hash, file: 3).key, stream("torrent", nil, hash: hash, file: 4).key)
        XCTAssertEqual(stream("url", nil, url: "http://127.0.0.1/b").key, "http://127.0.0.1/b")
    }

    func testTheButtonSaysTheQualityAndThePlugin() {
        let ways = answer([("a", [torrent("1", "720p"), torrent("2", nil)])]).ways
        XCTAssertEqual(FilmStart.describe(ways[0]), "720p · A")
        XCTAssertEqual(FilmStart.describe(ways[1]), "Unknown · A")
    }
}
