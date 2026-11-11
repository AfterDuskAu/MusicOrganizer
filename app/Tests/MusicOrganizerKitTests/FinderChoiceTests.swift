import XCTest

@testable import MusicOrganizerKit

/// A Finder's genre menus and its askings (the owner's three reports of 2026-10-17).
final class FinderChoiceTests: XCTestCase {
    func testChoosingAGenreEmptiesTheSecond() {
        var choice = GenreChoice(genre: "Drama", also: "Action")
        choice.choose(genre: "Comedy")
        XCTAssertEqual(choice, GenreChoice(genre: "Comedy", also: ""))
        // The old second genre as the new first: still empty, never "Action and Action".
        choice = GenreChoice(genre: "Drama", also: "Action")
        choice.choose(genre: "Action")
        XCTAssertEqual(choice, GenreChoice(genre: "Action", also: ""))
        // Every Genre again: nothing for a second to narrow.
        choice = GenreChoice(genre: "Drama", also: "Action")
        choice.choose(genre: "")
        XCTAssertEqual(choice, GenreChoice())
    }

    func testTheSameGenreAgainKeepsTheSecond() {
        var choice = GenreChoice(genre: "Drama", also: "Action")
        choice.choose(genre: "Drama")
        XCTAssertEqual(choice, GenreChoice(genre: "Drama", also: "Action"))
    }

    func testAnotherListEmptiesBothTheSameListKeepsThem() {
        var choice = GenreChoice(genre: "Drama", also: "Action")
        choice.choose(list: "cinemeta|top", over: "cinemeta|top")  // a way back from a search
        XCTAssertEqual(choice, GenreChoice(genre: "Drama", also: "Action"))
        choice.choose(list: "cinemeta|year", over: "cinemeta|top")
        XCTAssertEqual(choice, GenreChoice())
        // And a genre chosen on the new list doesn't bring the old "and Action" back.
        choice.choose(genre: "2026")
        XCTAssertEqual(choice, GenreChoice(genre: "2026", also: ""))
    }

    func testTheNewestAskingIsTheOneShown() {
        var askings = Askings()
        XCTAssertFalse(askings.isAsking)
        let list = askings.begin("top|Drama")
        let search = askings.begin("search|series|alien")
        XCTAssertEqual(askings.asking, "search|series|alien")
        // The search cleared before its answer came: the list is asked for again.
        let again = askings.begin("top|Drama")
        XCTAssertFalse(askings.isNewest(list))
        XCTAssertFalse(askings.isNewest(search))  // its answer, when it comes, isn't shown
        askings.end(search)
        XCTAssertTrue(askings.isAsking)  // an older answer doesn't end the newest asking
        XCTAssertTrue(askings.isNewest(again))
        askings.end(again)
        XCTAssertFalse(askings.isAsking)
        XCTAssertNil(askings.asking)
    }

    /// What a list by two genres says when an answer has nothing with both (2026-11-11).
    func testTwoGenresWithNothingSayWhereTheyLooked() throws {
        let none = try XCTUnwrap(
            TwoGenresNote.after(
                type: "series", genre: "Thriller", also: "Documentary", more: false, listEmpty: true, skip: 0,
                nextSkip: 976))
        XCTAssertEqual(none.problem, "None of these series is both Thriller and Documentary.")
        XCTAssertFalse(none.further)  // the lists' end: nothing to look further for
        // At the end with something on the list: it just ends.
        XCTAssertNil(
            TwoGenresNote.after(
                type: "movie", genre: "Crime", also: "War", more: false, listEmpty: false, skip: 50, nextSkip: 100))
        let first = try XCTUnwrap(
            TwoGenresNote.after(
                type: "series", genre: "Thriller", also: "Documentary", more: true, listEmpty: true, skip: 0,
                nextSkip: 400))
        XCTAssertEqual(first.problem, "None of the first 400 series is both Thriller and Documentary.")
        XCTAssertTrue(first.further)
        let next = try XCTUnwrap(
            TwoGenresNote.after(
                type: "movie", genre: "Crime", also: "Documentary", more: true, listEmpty: false, skip: 150,
                nextSkip: 550))
        XCTAssertEqual(next.problem, "No more movies that are both Crime and Documentary in the next 400.")
        XCTAssertTrue(next.further)
        XCTAssertEqual(TwoGenresNote.plural("anime"), "anime")
        XCTAssertEqual(TwoGenresNote.plural("channel"), "titles")
    }

    /// An answer by two genres carries where it's reading; one from a list by one genre, or
    /// from an engine before 2026-11-11, has none.
    func testAnAnswerByTwoGenresSaysWhichListItsReading() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let two = try decoder.decode(
            CatalogAnswer.self,
            from: Data(#"{"items": [], "more": true, "next_skip": 400, "reading": {"genre": null, "step": 50}}"#.utf8))
        XCTAssertEqual(two.reading, GenreReading(genre: nil, step: 50))
        XCTAssertEqual(two.reading?.asked as? [String: Int], ["step": 50])
        let chosen = GenreReading(genre: "Documentary", step: 50).asked
        XCTAssertEqual(chosen["genre"] as? String, "Documentary")
        let old = try decoder.decode(CatalogAnswer.self, from: Data(#"{"items": [], "more": false, "next_skip": 3}"#.utf8))
        XCTAssertNil(old.reading)
        let one = try decoder.decode(
            CatalogAnswer.self, from: Data(#"{"items": [], "more": false, "next_skip": 3, "reading": null}"#.utf8))
        XCTAssertNil(one.reading)
    }
}
