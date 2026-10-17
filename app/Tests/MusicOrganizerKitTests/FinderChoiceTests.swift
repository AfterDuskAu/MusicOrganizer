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
}
