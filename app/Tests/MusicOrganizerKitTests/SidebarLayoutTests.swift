import XCTest

@testable import MusicOrganizerKit

/// Every row of the sidebar can be moved, into any section (the owner, 2026-11-11).
final class SidebarLayoutTests: XCTestCase {
    func testTheStandardOrderIsTheOwners() {
        let layout = SidebarLayout.read(nil)
        XCTAssertEqual(layout.sections.map(\.name), ["Music", "Playlists", "Videos", "Media Discovery"])
        // The Watchlist and the Calendar under Home, above Music Finder.
        XCTAssertEqual(layout.rows(in: "Media Discovery").prefix(4), ["home", "watchlist", "calendar", "youtube"])
        XCTAssertEqual(layout.rows(in: "Videos"), ["channels", "movies", "adultVideos", "videos"])
        XCTAssertEqual(layout.rows(in: "Playlists"), ["import"])
        // Every row the sidebar has is in it once, Music's own entries and the rest.
        let all = layout.sections.flatMap(\.rows)
        XCTAssertEqual(Set(all), Set(SidebarChoice.all).union(SidebarRows.all.map(\.key)))
        XCTAssertEqual(all.count, Set(all).count)
    }

    func testAnyRowGoesInAnySectionAndIsKept() {
        var layout = SidebarLayout.read(nil)
        layout.move("songs", to: "Media Discovery", at: 0)
        layout.move("calendar", to: "Music")
        layout.move("import", to: "Videos", at: 1)
        layout.nudgeSection("Media Discovery", by: -3)
        let saved = layout.write()
        let back = SidebarLayout.read(saved)
        XCTAssertEqual(back, layout)
        XCTAssertEqual(back.sections.first?.name, "Media Discovery")
        XCTAssertEqual(back.rows(in: "Media Discovery").first, "songs")
        XCTAssertEqual(back.rows(in: "Music").last, "calendar")
        XCTAssertEqual(back.rows(in: "Videos").prefix(2), ["channels", "import"])
        XCTAssertEqual(back.rows(in: "Playlists"), [])
        XCTAssertEqual(back.section(of: "import"), "Videos")
    }

    func testDraggingWithinASection() {
        var layout = SidebarLayout.read(nil)
        // Calendar dragged above Home (onMove's offsets and destination).
        layout.move(in: "Media Discovery", from: [2], to: 0)
        XCTAssertEqual(layout.rows(in: "Media Discovery").prefix(3), ["calendar", "home", "watchlist"])
        // Home dragged to below Music Finder: the destination counts the row itself.
        layout.move(in: "Media Discovery", from: [1], to: 4)
        XCTAssertEqual(layout.rows(in: "Media Discovery").prefix(4), ["calendar", "watchlist", "youtube", "home"])
        // Moved down its own section by name, the same way.
        layout.move("calendar", to: "Media Discovery", at: 3)
        XCTAssertEqual(layout.rows(in: "Media Discovery").prefix(4), ["watchlist", "youtube", "calendar", "home"])
        layout.nudge("home", by: -1)
        XCTAssertEqual(layout.rows(in: "Media Discovery").prefix(4), ["watchlist", "youtube", "home", "calendar"])
        // Past either end: nothing moves.
        layout.nudge("watchlist", by: -1)
        XCTAssertEqual(layout.rows(in: "Media Discovery").first, "watchlist")
    }

    func testWhatsSavedIsReadForgivingly() {
        // Unknown sections and rows dropped, a row twice kept once, and a section and the
        // rows that weren't saved put back in their standard places.
        let layout = SidebarLayout.read("Videos:movies,nonsense,home,movies|Nowhere:songs|Music:albums,songs")
        XCTAssertEqual(layout.sections.map(\.name), ["Videos", "Music", "Playlists", "Media Discovery"])
        // A row not saved goes after the one before it in its standard section, where that
        // is (Channel has none: the top).
        XCTAssertEqual(layout.rows(in: "Videos"), ["channels", "movies", "adultVideos", "videos", "home"])
        // The Music rows that weren't saved each go after the one before them: songs, saved
        // after albums, stays there.
        XCTAssertEqual(layout.rows(in: "Music").prefix(3), ["albums", "artists", "favourites"])
        XCTAssertEqual(layout.rows(in: "Music").last, "songs")
        XCTAssertEqual(layout.section(of: "watchlist"), "Media Discovery")
        XCTAssertEqual(layout.rows(in: "Media Discovery").first, "watchlist")
        let all = layout.sections.flatMap(\.rows)
        XCTAssertEqual(all.count, SidebarLayout.allRows.count)
        XCTAssertEqual(Set(all), Set(SidebarLayout.allRows))
    }

    func testAnEmptySaveIsTheStandard() {
        XCTAssertEqual(SidebarLayout.read(""), SidebarLayout.standard)
        XCTAssertEqual(SidebarLayout.read("garbage"), SidebarLayout.standard)
    }
}
