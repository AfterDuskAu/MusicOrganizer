import XCTest

@testable import MusicOrganizerKit

/// Back goes to the page that was showing before (the owner, 2026-11-11).
final class BackTrailTests: XCTestCase {
    private struct Page: Identifiable, Hashable {
        let id: String
        var note = ""
    }

    func testBackUnwindsInTheOrderThingsWereOpened() {
        // Watchlist → a series → someone in its cast's film → Back → Back → the Watchlist.
        var trail = BackTrail<String, Page>()
        trail.open(Page(id: "series"), on: "watchlist")
        trail.open(Page(id: "film"), on: "watchlist")
        XCTAssertEqual(trail.pages(on: "watchlist").map(\.id), ["series", "film"])
        XCTAssertEqual(trail.top(on: "watchlist")?.id, "film")
        XCTAssertTrue(trail.back(on: "watchlist"))
        XCTAssertEqual(trail.top(on: "watchlist")?.id, "series")
        XCTAssertTrue(trail.back(on: "watchlist"))
        XCTAssertNil(trail.top(on: "watchlist"))
        XCTAssertFalse(trail.isCovered("watchlist"))
        // Nothing left: Back does nothing.
        XCTAssertFalse(trail.back(on: "watchlist"))
    }

    func testEachPageHasItsOwnTrail() {
        // A series opened from the Calendar stays on the Calendar; the Watchlist's is its own.
        var trail = BackTrail<String, Page>()
        trail.open(Page(id: "series A"), on: "calendar")
        trail.open(Page(id: "film B"), on: "watchlist")
        trail.back(on: "watchlist")
        XCTAssertEqual(trail.top(on: "calendar")?.id, "series A")
        XCTAssertFalse(trail.isCovered("watchlist"))
        XCTAssertTrue(trail.isCovered("calendar"))
    }

    func testTheSameTitleOnTopIsntOpenedTwice() {
        // The film player's ✕ asks for its series' page, which is the one on top already
        // (it was played from there): one Back still goes back to the page under it.
        var trail = BackTrail<String, Page>()
        trail.open(Page(id: "series"), on: "home")
        trail.open(Page(id: "series", note: "from the player"), on: "home")
        XCTAssertEqual(trail.pages(on: "home").count, 1)
        XCTAssertEqual(trail.top(on: "home")?.note, "")
        // Lower down, it's opened again (someone's page led back to it).
        trail.open(Page(id: "person"), on: "home")
        trail.open(Page(id: "series"), on: "home")
        XCTAssertEqual(trail.pages(on: "home").map(\.id), ["series", "person", "series"])
    }

    func testClearingAPageAndEveryPage() {
        var trail = BackTrail<String, Page>()
        trail.open(Page(id: "a"), on: "home")
        trail.open(Page(id: "b"), on: "watchlist")
        trail.clear("home")
        XCTAssertFalse(trail.isCovered("home"))
        XCTAssertTrue(trail.isCovered("watchlist"))
        trail.clearAll()
        XCTAssertFalse(trail.isCovered("watchlist"))
    }

    func testPagesThatAreGoneAreTakenOut() {
        var trail = BackTrail<String, Page>()
        trail.open(Page(id: "a"), on: "home")
        trail.open(Page(id: "gone"), on: "home")
        trail.open(Page(id: "gone"), on: "calendar")
        trail.removeAll { $0.id == "gone" }
        XCTAssertEqual(trail.pages(on: "home").map(\.id), ["a"])
        XCTAssertFalse(trail.isCovered("calendar"))
    }
}
