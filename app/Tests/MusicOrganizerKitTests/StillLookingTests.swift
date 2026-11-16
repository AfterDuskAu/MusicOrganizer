import XCTest

@testable import MusicOrganizerKit

/// A song that's slow to be found on a busy Mac is said to be coming, not failed
/// (2026-11-17).
final class StillLookingTests: XCTestCase {
    func testTheLineNamesTheSongAndSaysItsComing() {
        let line = StillLooking.note(for: "Song Title")
        XCTAssertTrue(line.contains("“Song Title”"))
        XCTAssertTrue(line.contains("busy"))
        XCTAssertTrue(line.contains("play as soon as it's found"))
    }

    func testTheLineNeverNamesTheService() {
        // The owner's rule: the app's words don't say "YouTube".
        let line = StillLooking.note(for: "Song Title")
        XCTAssertFalse(line.contains("YouTube"))
        XCTAssertEqual(Wording.plain(line), line)
    }

    func testTheSpinnerTurnsAloneForAFewSecondsFirst() {
        // A song that's found at once (most are) never shows the line.
        XCTAssertGreaterThanOrEqual(StillLooking.after, .seconds(5))
        XCTAssertLessThanOrEqual(StillLooking.after, .seconds(15))
    }
}
