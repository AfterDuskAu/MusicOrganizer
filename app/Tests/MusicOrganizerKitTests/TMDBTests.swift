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
        let names = TMDB.addon.catalogs.filter { $0.type == "movie" }.map(\.name)
        XCTAssertTrue(names == ["TMDB – Popular", "TMDB – Top Rated", "TMDB – Upcoming"])
        XCTAssertTrue(TMDB.addon.catalogs.filter { $0.type == "series" }.map(\.id) == ["popular", "top_rated", "upcoming"])
        XCTAssertTrue(TMDB.addon.catalogs.allSatisfy { $0.canBeBrowsed && $0.takes("skip") && $0.genres.isEmpty })
        XCTAssertTrue(TMDB.isTMDB(TMDB.addon) && !TMDB.addon.isAdult)
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
