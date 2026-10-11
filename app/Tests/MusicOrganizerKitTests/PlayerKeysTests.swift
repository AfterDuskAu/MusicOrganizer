import XCTest

@testable import MusicOrganizerKit

final class PlayerKeysTests: XCTestCase {
    func testTheSpaceBarAndTheFourArrowsAreThePlayers() {
        XCTAssertEqual(PlayerKey(keyCode: 49), .playPause)
        XCTAssertEqual(PlayerKey(keyCode: 126), .louder)
        XCTAssertEqual(PlayerKey(keyCode: 125), .quieter)
        XCTAssertEqual(PlayerKey(keyCode: 123), .back)
        XCTAssertEqual(PlayerKey(keyCode: 124), .forward)
    }

    func testAnyOtherKeyIsNot() {
        XCTAssertNil(PlayerKey(keyCode: 36))  // Return
        XCTAssertNil(PlayerKey(keyCode: 53))  // Esc
        XCTAssertNil(PlayerKey(keyCode: 0))  // A
    }

    func testAnArrowMovesTheSoundATwentieth() {
        XCTAssertEqual(PlayerKey.louder.volume(from: 50, full: 100), 55, accuracy: 0.001)
        XCTAssertEqual(PlayerKey.quieter.volume(from: 50, full: 100), 45, accuracy: 0.001)
        XCTAssertEqual(PlayerKey.louder.volume(from: 0.5, full: 1), 0.55, accuracy: 0.001)
    }

    func testTheSoundStopsAtNothingAndAtFull() {
        XCTAssertEqual(PlayerKey.louder.volume(from: 98, full: 100), 100)
        XCTAssertEqual(PlayerKey.quieter.volume(from: 0.02, full: 1), 0)
        XCTAssertEqual(PlayerKey.quieter.volume(from: 0, full: 100), 0)
    }

    func testAKeyThatIsNotForTheSoundLeavesIt() {
        XCTAssertEqual(PlayerKey.forward.volume(from: 40, full: 100), 40)
        XCTAssertEqual(PlayerKey.playPause.volume(from: 40, full: 100), 40)
    }

    func testLeftAndRightGoTenSecondsBackAndOn() {
        XCTAssertEqual(PlayerKey.back.place(from: 60, length: 600), 50)
        XCTAssertEqual(PlayerKey.forward.place(from: 60, length: 600), 70)
    }

    func testNeverBeforeTheStartOrOntoTheVeryEnd() {
        XCTAssertEqual(PlayerKey.back.place(from: 4, length: 600), 0)
        XCTAssertEqual(PlayerKey.forward.place(from: 595, length: 600), 599)
    }

    func testNoPlaceToGoWhenTheLengthIsNotKnownOrTheKeyIsNotAnArrowAcross() {
        XCTAssertNil(PlayerKey.forward.place(from: 5, length: 0))
        XCTAssertNil(PlayerKey.louder.place(from: 5, length: 600))
        XCTAssertNil(PlayerKey.playPause.place(from: 5, length: 600))
    }
}
