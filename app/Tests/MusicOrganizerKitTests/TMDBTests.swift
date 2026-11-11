import XCTest

@testable import MusicOrganizerKit

/// TMDB in the app (the owner, 2026-11-05): the original-language choice, a child's
/// ratings, the Finders' TMDB lists, a film's money, and the calendar's General.
final class TMDBTests: XCTestCase {
    func testTheLanguageChoicesStartWithAllThenEnglish() {
        let offered = OriginalLanguage.offered
        XCTAssertTrue(offered.prefix(2) == ["", "en"])
        XCTAssertTrue(OriginalLanguage.name("") == "All")
        XCTAssertTrue(OriginalLanguage.name("en") == "English")
        XCTAssertTrue(OriginalLanguage.name("ko") == "Korean")
        XCTAssertTrue(OriginalLanguage.name("cn") == "Cantonese")
        XCTAssertTrue(offered.contains("zh") && offered.contains("ta"))
        XCTAssertTrue(offered.filter { $0 == "en" }.count == 1)
        // After English, by name.
        let rest = offered.dropFirst(2).map(OriginalLanguage.name)
        XCTAssertTrue(rest == rest.sorted())
    }

    func testAllFiltersNothingAndALanguageKeepsOnlyWhatsKnownToBeIt() {
        let open = TitleRules()
        XCTAssertTrue(open.decide(nil, languageToo: true) == .show)
        XCTAssertTrue(open.wanted(languageToo: true).isEmpty)
        let english = TitleRules(language: "en")
        XCTAssertTrue(english.wanted(languageToo: true) == ["language"])
        XCTAssertTrue(english.decide(nil, languageToo: true) == .waiting)
        XCTAssertTrue(english.decide(TitleFacts(id: "tt1", language: "en", rating: nil), languageToo: true) == .show)
        XCTAssertTrue(english.decide(TitleFacts(id: "tt2", language: "zh", rating: nil), languageToo: true) == .hide)
        // Unknown is left out.
        XCTAssertTrue(english.decide(TitleFacts(id: "tt3", language: nil, rating: nil), languageToo: true) == .hide)
        // Where the language doesn't count (Home, the calendar), nothing is waited for.
        XCTAssertTrue(english.decide(nil, languageToo: false) == .show)
    }

    func testAChildsRatingsKeepOutWhatsRatedOtherwiseOrNotRated() {
        let child = TitleRules(ratings: ["G", "PG"])
        XCTAssertTrue(child.wanted(languageToo: false) == ["rating"])
        XCTAssertTrue(child.decide(nil, languageToo: false) == .waiting)
        XCTAssertTrue(child.decide(TitleFacts(id: "a", language: "en", rating: "PG"), languageToo: false) == .show)
        XCTAssertTrue(child.decide(TitleFacts(id: "b", language: "en", rating: "M"), languageToo: false) == .hide)
        XCTAssertTrue(child.decide(TitleFacts(id: "c", language: "en", rating: nil), languageToo: false) == .hide)
        let both = TitleRules(language: "en", ratings: ["M"])
        XCTAssertTrue(both.wanted(languageToo: true) == ["language", "rating"])
        XCTAssertTrue(both.decide(TitleFacts(id: "d", language: "fr", rating: "M"), languageToo: true) == .hide)
        XCTAssertTrue(both.decide(TitleFacts(id: "e", language: "en", rating: "M"), languageToo: true) == .show)
        XCTAssertTrue(AustralianRating.shown(["MA15+", "G"]) == "G, MA15+")
    }

    func testAChildsProfileIsKeptToTheRatingsTickedAndOnlyAChilds() throws {
        var list = ProfileList(firstNamed: "Me", libraryRoot: "/x")
        let kid = try list.add(name: "Kid", libraryRoot: "/y", isChild: true, newId: { "kid" })
        XCTAssertTrue(list.profile(kid.id)?.ratingsKept == nil)
        list.setRating(kid.id, "PG", allowed: true)
        list.setRating(kid.id, "G", allowed: true)
        list.setRating(kid.id, "X18+", allowed: true)  // never one that can be ticked
        XCTAssertTrue(list.profile(kid.id)?.allowedRatings == ["G", "PG"])
        XCTAssertTrue(list.profile(kid.id)?.ratingsKept == ["G", "PG"])
        list.setRating(kid.id, "G", allowed: false)
        list.setRating(kid.id, "PG", allowed: false)
        XCTAssertTrue(list.profile(kid.id)?.allowedRatings == nil)
        list.setRating(kid.id, "M", allowed: true)
        list.setChild(kid.id, false)
        XCTAssertTrue(list.profile(kid.id)?.ratingsKept == nil)  // not a child's: kept to nothing
        // Kept, and read back; a list saved before ratings reads as any.
        let saved = try JSONEncoder().encode(list)
        XCTAssertTrue(try JSONDecoder().decode(ProfileList.self, from: saved) == list)
        let old = #"{"id":"a","name":"A","isNew":false,"isChild":true,"allowsExplicit":false}"#
        XCTAssertTrue(try JSONDecoder().decode(Profile.self, from: Data(old.utf8)).allowedRatings == nil)
    }

    func testTmdbsListsAreOfferedLikeAPluginsInTheFinders() {
        let plain = TMDB.addon()
        let names = plain.catalogs.filter { $0.type == "movie" }.map(\.name)
        XCTAssertTrue(names == ["TMDB – Popular", "TMDB – Top Rated", "TMDB – Upcoming", "TMDB – Trending"])
        XCTAssertTrue(plain.catalogs.filter { $0.type == "series" }.map(\.id) == ["popular", "top_rated", "upcoming", "trending"])
        // Before TMDB's genres have come, its lists have no genre menu.
        XCTAssertTrue(plain.catalogs.allSatisfy { $0.canBeBrowsed && $0.takes("skip") && $0.genres.isEmpty })
        XCTAssertTrue(TMDB.isTMDB(plain) && !plain.isAdult)
    }

    func testTheStandardPluginsListsByTmdbsOwnGenresButTrending() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let found = try decoder.decode(TMDBGenres.self, from: Data(#"""
            {"genres": {"movie": ["Comedy", "Horror"], "series": ["Action & Adventure", "Crime", "Documentary"]}}
            """#.utf8))
        let addon = TMDB.addon(genres: found.genres)
        let popular = try XCTUnwrap(addon.catalogs.first { $0.type == "series" && $0.id == "popular" })
        XCTAssertTrue(popular.genres == ["Action & Adventure", "Crime", "Documentary"])
        XCTAssertTrue(popular.offersEveryGenre && popular.genre(chosen: "Crime") == "Crime")
        XCTAssertTrue(popular.genre(chosen: "Sci-Fi") == nil)  // another plugin's genre: none here
        XCTAssertTrue(addon.catalogs.first { $0.type == "movie" && $0.id == "top_rated" }?.genres == ["Comedy", "Horror"])
        // Trending is the week's, by no genre.
        XCTAssertTrue(addon.catalogs.filter { $0.id == "trending" }.allSatisfy { $0.genres.isEmpty && $0.takes("skip") })
        XCTAssertTrue(addon.catalogs.allSatisfy(\.canBeBrowsed))
    }

    func testATitlesPageFromTMDBWithThePluginsForTheRest() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let answer = try decoder.decode(TMDBDetails.self, from: Data(#"""
            {"tmdb_id": 1, "budget": null, "revenue": null,
             "cast": [{"person_id": 5, "name": "Robert Durst", "character": null, "photo": null}],
             "name": "The Jinx", "poster": "https://image.tmdb.org/t/p/w500/p.jpg",
             "background": "https://image.tmdb.org/t/p/w1280/b.jpg", "year": "2015", "rating": 8.0,
             "genres": ["Documentary", "Crime"], "description": "Robert Durst.", "runtime_min": 45,
             "directors": ["Andrew Jarecki"], "trailer_video_id": "x2"}
            """#.utf8))
        let tmdb = try XCTUnwrap(answer.page(id: "tt4299972", type: "series"))
        XCTAssertTrue(tmdb.name == "The Jinx" && tmdb.runtimeMin == 45 && tmdb.trailerVideoId == "x2")
        XCTAssertTrue(tmdb.cast == ["Robert Durst"] && tmdb.directors == ["Andrew Jarecki"] && tmdb.videos.isEmpty)
        // The plugin's page fills in what TMDB hasn't (its logo), and its episodes are kept:
        // their numbering is the one the ways to play go by.
        let plugin = MediaDetails(
            id: "tt4299972", type: "series", name: "The Jinx: The Life and Deaths of Robert Durst", poster: "plugin.jpg",
            year: "2015", rating: 7.9, genres: ["Crime"], description: nil, background: nil, logo: "logo.png",
            runtimeMin: nil, cast: ["Someone"], directors: [], trailerVideoId: "old",
            videos: [MediaDetails.Video(id: "tt4299972:1:1", title: "Chapter 1", season: 1, episode: 1)])
        let shown = try XCTUnwrap(MediaDetails.tmdbFirst(tmdb, plugin))
        XCTAssertTrue(shown.id == "tt4299972" && shown.name == "The Jinx")
        XCTAssertTrue(shown.poster == tmdb.poster && shown.background == tmdb.background)
        XCTAssertTrue(shown.description == "Robert Durst." && shown.rating == 8.0)
        XCTAssertTrue(shown.genres == ["Documentary", "Crime"] && shown.cast == ["Robert Durst"])
        XCTAssertTrue(shown.logo == "logo.png" && shown.trailerVideoId == "x2")
        XCTAssertTrue(shown.videos.map(\.id) == ["tt4299972:1:1"])
        XCTAssertTrue(MediaDetails.tmdbFirst(nil, plugin)?.name == plugin.name)
        XCTAssertTrue(MediaDetails.tmdbFirst(tmdb, nil)?.name == "The Jinx")
        XCTAssertTrue(MediaDetails.tmdbFirst(nil, nil) == nil)
        // What TMDB hasn't is the plugin's, field by field.
        let bare = try decoder.decode(TMDBDetails.self, from: Data(#"""
            {"tmdb_id": 1, "cast": [], "budget": null, "revenue": null, "name": "The Jinx", "poster": null,
             "background": null, "year": null, "rating": null, "genres": [], "description": null,
             "runtime_min": null, "directors": [], "trailer_video_id": null}
            """#.utf8))
        let filled = try XCTUnwrap(MediaDetails.tmdbFirst(bare.page(id: "tt4299972", type: "series"), plugin))
        XCTAssertTrue(filled.poster == "plugin.jpg" && filled.year == "2015" && filled.rating == 7.9)
        XCTAssertTrue(filled.genres == ["Crime"] && filled.cast == ["Someone"] && filled.trailerVideoId == "old")
        // TMDB not knowing the title, or an engine from before: no page of TMDB's.
        let older = try decoder.decode(TMDBDetails.self, from: Data(#"{"tmdb_id": null, "cast": [], "budget": null, "revenue": null}"#.utf8))
        XCTAssertTrue(older.page(id: "tt1", type: "movie") == nil)
    }

    func testASeriesNoPluginHasEpisodesOfTakesTmdbs() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let found = try decoder.decode(TMDBEpisodes.self, from: Data(#"""
            {"videos": [
              {"id": "tt4299972:1:1", "title": "Chapter 1", "thumbnail": "https://image.tmdb.org/t/p/w300/s.jpg",
               "released": "2015-02-08", "video_id": null, "season": 1, "episode": 1, "overview": "First."},
              {"id": "tt4299972:1:2", "title": "Episode 2", "thumbnail": null, "released": null, "video_id": null,
               "season": 1, "episode": 2, "overview": null}]}
            """#.utf8))
        let tmdbOnly = MediaDetails(
            id: "tt4299972", type: "series", name: "The Jinx", poster: nil, year: nil, rating: nil, genres: [],
            description: nil, cast: [], videos: [])
        let shown = tmdbOnly.withEpisodes(found.videos)
        XCTAssertTrue(shown.seasons == [1] && shown.episodes(in: 1).map(\.number) == ["S1 E1", "S1 E2"])
        XCTAssertTrue(shown.videos.first?.day == "2015-02-08" && shown.videos.first?.videoId == nil)
        // A page with episodes of its own keeps them.
        XCTAssertTrue(shown.withEpisodes([]).videos.count == 2)
        let search = try decoder.decode(TMDBFound.self, from: Data(#"""
            {"items": [{"id": "tt0137523", "type": "movie", "name": "Fight Club", "poster": null,
                        "poster_shape": "poster", "year": "1999", "rating": 8.4, "genres": ["Thriller"]}]}
            """#.utf8))
        XCTAssertTrue(search.items.map(\.id) == ["tt0137523"])
    }

    func testAFilmsMoneyIsInDollars() {
        XCTAssertTrue(Money.dollars(63_000_000) == "$63,000,000")
        XCTAssertTrue(Money.dollars(1) == "$1")
    }

    func testTmdbsAnswersAreRead() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let details = try decoder.decode(TMDBDetails.self, from: Data(#"""
            {"tmdb_id": 550, "budget": 63000000, "revenue": null,
             "cast": [{"person_id": 819, "name": "Edward Norton", "character": "The Narrator", "photo": null}]}
            """#.utf8))
        XCTAssertTrue(details.cast.first?.id == 819 && details.budget == 63_000_000 && details.revenue == nil)
        let facts = try decoder.decode(FactsAnswer.self, from: Data(#"{"facts": [{"id": "tt_1", "language": "en", "rating": "PG"}]}"#.utf8))
        XCTAssertTrue(facts.facts == [TitleFacts(id: "tt_1", language: "en", rating: "PG")])
        let person = try decoder.decode(TMDBPerson.self, from: Data(#"""
            {"id": 819, "name": "Edward Norton", "photo": null, "biography": "An actor.",
             "credits": [{"id": "tmdb:550", "type": "movie", "name": "Fight Club", "poster": null, "year": "1999", "character": "The Narrator"}]}
            """#.utf8))
        XCTAssertTrue(person.credits.first?.item.id == "tmdb:550" && person.credits.first?.item.type == "movie")
    }

    func testTheMonthsReleasesGoOnTheirDays() {
        let found = [
            TMDBRelease(id: "tmdb:1", type: "movie", name: "A Film", poster: nil, day: "2026-11-13"),
            TMDBRelease(id: "tmdb:9", type: "series", name: "A Series", poster: nil, day: "2026-11-30"),
        ]
        let days = CalendarEntry.byDay(CalendarEntry.releases(found))
        XCTAssertTrue(days["2026-11-13"]?.first?.detail == "Film")
        XCTAssertTrue(days["2026-11-30"]?.first?.detail == "New series")
        XCTAssertTrue(days["2026-11-30"]?.first?.release?.item.type == "series")
        XCTAssertTrue(found[0].item.year == "2026")
    }
}
