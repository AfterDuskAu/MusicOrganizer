import XCTest

@testable import MusicOrganizerKit

/// Videos → Movies/Series (the owner, 2026-11-05, as Bear Media II's Downloaded page):
/// the Movies folder's files read by the engine's naming rules, a card a film or a series,
/// movies then series then anime, and each card's plugin title.
final class KeptShelfTests: XCTestCase {
    private let movies = URL(fileURLWithPath: "/Scratch/Movies", isDirectory: true)

    private func file(_ path: String, bytes: Int64 = 1_000, added: TimeInterval = 0) -> KeptFile {
        let url = movies.appendingPathComponent(path)
        return KeptFile(VideoFiles.File(url: url, bytes: bytes, added: Date(timeIntervalSince1970: added)), in: movies)
    }

    private func item(_ id: String, _ type: String, _ name: String, year: String? = nil) -> MediaItem {
        MediaItem(id: id, type: type, name: name, poster: nil, posterShape: "poster", year: year, rating: nil, genres: [])
    }

    // MARK: reading a file

    func testAFilmIsItsNameAndYear() {
        let film = file("Parasite (2019).mp4")
        XCTAssertEqual(film.kind, "movie")
        XCTAssertEqual(film.title, "Parasite")
        XCTAssertEqual(film.year, "2019")
        XCTAssertNil(film.show)
        XCTAssertEqual(film.key, "film:parasite (2019)")
    }

    func testAFilmWithoutAYearIsItsName() {
        let film = file("Home Video.mkv")
        XCTAssertEqual(film.title, "Home Video")
        XCTAssertNil(film.year)
    }

    func testAnEpisodeAsTheEngineNamesIt() {
        let one = file("Series/Silo/Season 1/Silo S01E02 - Holston.mkv")
        XCTAssertEqual(one.kind, "series")
        XCTAssertEqual(one.show, "Silo")
        XCTAssertEqual(one.season, 1)
        XCTAssertEqual(one.episode, 2)
        XCTAssertEqual(one.title, "Holston")
        XCTAssertEqual(one.key, "show:silo")
    }

    func testAnEpisodeWithNoTitleOrSeasonOfItsOwn() {
        XCTAssertEqual(file("Series/Silo/Season 2/Silo S02E03.mp4").title, "")
        let loose = file("Anime/Frieren/Specials/Frieren E05 - Extra.mkv")
        XCTAssertEqual(loose.kind, "anime")
        XCTAssertEqual(loose.season, 0)
        XCTAssertEqual(loose.episode, 5)
        XCTAssertEqual(loose.title, "Extra")
        let unknown = file("Series/Silo/Silo E04.mkv")
        XCTAssertNil(unknown.season)
        XCTAssertEqual(unknown.seasonOrFirst, 1)
    }

    func testAnAnimeFilmIsAFilmInAnime() {
        let film = file("Anime/Your Name (2016).mkv")
        XCTAssertEqual(film.kind, "anime")
        XCTAssertNil(film.show)
        XCTAssertEqual(film.title, "Your Name")
    }

    func testFoldersAreTakenWhateverTheirCaseAndAnythingElseIsAFilm() {
        XCTAssertEqual(file("series/Silo/Season 1/Silo S01E01.mkv").kind, "series")
        XCTAssertEqual(file("TV/Media.localized/Something S01E01.mkv").kind, "movie")
    }

    // MARK: the shelf

    func testMoviesThenSeriesThenAnimeTheNewestFirst() {
        let shelf = KeptShelf(
            [
                file("Old Film (1999).mp4", added: 1),
                file("New Film (2026).mp4", added: 5),
                file("Series/Silo/Season 1/Silo S01E02.mkv", added: 3),
                file("Series/Silo/Season 1/Silo S01E01.mkv", added: 2),
                file("Series/Fargo/Season 1/Fargo S01E01.mkv", added: 4),
                file("Anime/Frieren/Season 1/Frieren S01E01.mkv", added: 6),
            ], titles: KeptTitles())
        XCTAssertEqual(shelf.movies.map(\.name), ["New Film", "Old Film"])
        XCTAssertEqual(shelf.series.map(\.name), ["Fargo", "Silo"])
        XCTAssertEqual(shelf.anime.map(\.name), ["Frieren"])
        XCTAssertEqual(shelf.all.count, 5)
        XCTAssertEqual(shelf.fileCount, 6)
        let silo = shelf.series[1]
        XCTAssertTrue(silo.isShow)
        XCTAssertEqual(silo.line, "2 episodes")
        XCTAssertEqual(silo.files.map(\.episode), [1, 2])
    }

    func testAFilmAndItsConvertedCopyAreOneCard() {
        let shelf = KeptShelf([file("Nosferatu (2024).mkv"), file("Nosferatu (2024).mp4")], titles: KeptTitles())
        XCTAssertEqual(shelf.movies.count, 1)
        XCTAssertEqual(shelf.movies[0].files.count, 2)
        XCTAssertFalse(shelf.movies[0].isShow)
    }

    func testEpisodesBySeasonAndNumberWithTheSpecialsLast() {
        let shelf = KeptShelf(
            [
                file("Series/Silo/Specials/Silo S00E01.mkv"),
                file("Series/Silo/Season 2/Silo S02E01.mkv"),
                file("Series/Silo/Season 1/Silo S01E10.mkv"),
                file("Series/Silo/Season 1/Silo S01E02.mkv"),
            ], titles: KeptTitles())
        let silo = shelf.series[0]
        XCTAssertEqual(silo.files.map { "\($0.season!)x\($0.episode!)" }, ["1x2", "1x10", "2x1", "0x1"])
        XCTAssertEqual(silo.perSeason, [0: 1, 1: 2, 2: 1])
        XCTAssertEqual(silo.firstSeason, 1)
        XCTAssertNotNil(silo.file(season: 2, episode: 1))
        XCTAssertNil(silo.file(season: 2, episode: 2))
    }

    func testANameTheDiskCouldntHoldIsShownAndAskedForAsItWas() {
        let card = KeptShelf([file("The Punisher_ One Last Kill (2026).mp4")], titles: KeptTitles()).movies[0]
        XCTAssertEqual(card.name, "The Punisher: One Last Kill")
        XCTAssertEqual(card.searchWords, "The Punisher One Last Kill")
        XCTAssertEqual(card.year, "2026")
        XCTAssertEqual(card.type, "movie")
    }

    // MARK: which title each card is

    func testATitleNotedForACardIsItsItem() {
        var titles = KeptTitles()
        titles.note(item("tt1", "series", "Silo"), for: "show:silo")
        let shelf = KeptShelf([file("Series/Silo/Season 1/Silo S01E01.mkv")], titles: titles)
        XCTAssertEqual(shelf.series[0].item?.id, "tt1")
        XCTAssertEqual(shelf.card(for: item("tt1", "series", "Silo"))?.key, "show:silo")
        XCTAssertNil(shelf.card(for: item("tt2", "series", "Silo")))
    }

    func testAMatchIsTheSameNameTypeAndYear() {
        let card = KeptShelf([file("Ant-Man and the Wasp_ Quantumania (2023).mp4")], titles: KeptTitles()).movies[0]
        let found = [
            item("s1", "series", "Ant-Man and the Wasp: Quantumania", year: "2023"),
            item("m0", "movie", "Ant-Man and the Wasp: Quantumania", year: "2019"),
            item("m1", "movie", "Ant-Man and the Wasp: Quantumania", year: "2023"),
        ]
        XCTAssertEqual(KeptTitles.match(found, for: card)?.id, "m1")
        // A year either side (a plugin's year can be the film's festival year).
        XCTAssertEqual(KeptTitles.match([item("m2", "movie", "ant-man and the wasp quantumania", year: "2022")], for: card)?.id, "m2")
        XCTAssertNil(KeptTitles.match([item("m3", "movie", "Ant-Man", year: "2023")], for: card))
        XCTAssertNil(KeptTitles.match([item("m4", "movie", "Ant-Man and the Wasp: Quantumania", year: nil)], for: card))
    }

    func testAShowMatchesASeriesOfItsNameAccentsAside() {
        let card = KeptShelf([file("Series/Pokemon/Season 1/Pokemon S01E01.mkv")], titles: KeptTitles()).series[0]
        XCTAssertEqual(KeptTitles.match([item("f", "movie", "Pokémon"), item("s", "series", "Pokémon", year: "1997–")], for: card)?.id, "s")
    }

    func testATitleNotFoundIsLookedForAgainOnlyAfterAWeek() {
        var titles = KeptTitles()
        let now = Date(timeIntervalSince1970: 1_000_000)
        XCTAssertTrue(titles.shouldLookUp("film:x", now: now))
        titles.notFound("film:x", at: now)
        XCTAssertFalse(titles.shouldLookUp("film:x", now: now.addingTimeInterval(3600)))
        XCTAssertTrue(titles.shouldLookUp("film:x", now: now.addingTimeInterval(KeptTitles.missWait + 1)))
        titles.note(item("tt9", "movie", "X"), for: "film:x")
        XCTAssertFalse(titles.shouldLookUp("film:x", now: now.addingTimeInterval(KeptTitles.missWait + 1)))
    }

    func testTitlesAreKeptInTheirOwnSetting() throws {
        let defaults = try XCTUnwrap(UserDefaults(suiteName: "KeptShelfTests"))
        defaults.removePersistentDomain(forName: "KeptShelfTests")
        var titles = KeptTitles()
        titles.note(item("tt1", "series", "Silo").asAnime, for: "show:silo")
        titles.save(in: defaults)
        let back = KeptTitles.load(from: defaults)
        XCTAssertEqual(back, titles)
        XCTAssertEqual(back.item("show:silo")?.keptKind, "anime")
        defaults.removePersistentDomain(forName: "KeptShelfTests")
    }

    // MARK: a series' page with its plugins out of reach

    private func details(_ id: String, videos: [MediaDetails.Video]) -> MediaDetails {
        MediaDetails(
            id: id, type: "series", name: "Silo", poster: "p", year: "2023", rating: 8, genres: ["Drama"],
            description: "Underground.", cast: ["Rebecca Ferguson"], videos: videos)
    }

    func testOfflineAPageHasOnlyTheEpisodesHereWithWhatWasKept() {
        var titles = KeptTitles()
        titles.note(item("tt1", "series", "Silo"), for: "show:silo")
        let files = [file("Series/Silo/Season 1/Silo S01E02 - Holston.mkv"), file("Series/Silo/Season 2/Silo S02E01.mkv")]
        let card = KeptShelf(files, titles: titles).series[0]
        // Its page was opened while the plugins answered: what they said is kept.
        let told = details(
            "tt1",
            videos: [
                .init(id: "tt1:1:1", title: "Freedom Day", season: 1, episode: 1),
                .init(id: "tt1:1:2", title: "Holston's Pick", season: 1, episode: 2, overview: "A sheriff."),
            ])
        XCTAssertTrue(titles.keep(told, here: card))
        XCTAssertFalse(titles.keep(told, here: card), "the same again changes nothing")
        let kept = titles.title("show:silo")
        XCTAssertEqual(kept?.summary, "Underground.")
        XCTAssertEqual(kept?.episodes?.map(\.id), ["tt1:1:2"], "only the episodes here")

        let offline = try! XCTUnwrap(card.offline(kept))
        XCTAssertEqual(offline.description, "Underground.")
        XCTAssertEqual(offline.cast, ["Rebecca Ferguson"])
        XCTAssertEqual(offline.videos.map(\.id), ["tt1:1:2", "tt1:2:1"])
        XCTAssertEqual(offline.videos[0].title, "Holston's Pick", "the plugin's own title, when it was kept")
        XCTAssertEqual(offline.seasons, [1, 2])
    }

    func testOfflineNeedsTheSeriesTitle() {
        let card = KeptShelf([file("Series/Silo/Season 1/Silo S01E01.mkv")], titles: KeptTitles()).series[0]
        XCTAssertNil(card.offline(nil))
        let film = KeptShelf([file("Parasite (2019).mp4")], titles: KeptTitles()).movies[0]
        XCTAssertNil(film.offline(nil))
    }

    func testDetailsOfAnotherTitleAreNotKept() {
        var titles = KeptTitles()
        titles.note(item("tt1", "series", "Silo"), for: "show:silo")
        let card = KeptShelf([file("Series/Silo/Season 1/Silo S01E01.mkv")], titles: titles).series[0]
        XCTAssertFalse(titles.keep(details("tt2", videos: []), here: card))
    }

    // MARK: a keep remembers its title

    func testAKeepFromAPageRemembersItsTitleButNotOneKeptApart() {
        let silo = item("tt1", "series", "Silo")
        let episode = MediaDetails.Video(id: "tt1:1:1", title: "Freedom Day", season: 1, episode: 1)
        let kept = PendingKeeps.Keep(infoHash: "ab", trackers: [], of: silo, part: episode)
        XCTAssertEqual(kept.film?.id, "tt1")
        let apart = PendingKeeps.Keep(infoHash: "ab", trackers: [], apart: true, of: silo, part: episode)
        XCTAssertNil(apart.film)
        // One remembered before titles were noted still reads.
        let old = #"{"infoHash":"ab","trackers":[],"title":"X","convert":true}"#
        let read = try? JSONDecoder().decode(PendingKeeps.Keep.self, from: Data(old.utf8))
        XCTAssertNotNil(read)
        XCTAssertNil(read?.film)
    }

    func testAFileStreamIsAPlainAddress() {
        let url = movies.appendingPathComponent("Parasite (2019).mp4")
        let stream = MediaStream.file(url)
        XCTAssertEqual(stream.kind, "url")
        XCTAssertTrue(stream.isFile)
        XCTAssertEqual(stream.url.flatMap(URL.init(string:))?.path, url.path)
    }
}
