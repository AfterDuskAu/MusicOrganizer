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

    // MARK: whose the arrows are

    private func place(_ change: (inout ArrowKeys.Place) -> Void) -> ArrowKeys.Place {
        var place = ArrowKeys.Place()
        change(&place)
        return place
    }

    func testAFieldASheetAndTheSpaceBarAreNeverTheArrows() {
        let playing = place { $0.somethingPlaying = true; $0.watching = true }
        XCTAssertEqual(ArrowKeys.owner(of: .playPause, at: playing), .theirs)
        XCTAssertEqual(ArrowKeys.owner(of: .forward, at: place { $0.typing = true; $0.watching = true }), .theirs)
        XCTAssertEqual(ArrowKeys.owner(of: .louder, at: place { $0.elsewhere = true; $0.watching = true }), .theirs)
    }

    func testWhileThePictureIsWatchedAllFourAreThePlayers() {
        let watching = place { $0.watching = true; $0.cardsShowing = true; $0.pageListHasKeyboard = true }
        for key in [PlayerKey.louder, .quieter, .back, .forward] {
            XCTAssertEqual(ArrowKeys.owner(of: key, at: watching), .player)
        }
    }

    func testASongPlayingHasTheArrowsOnAnyPage() {
        let playing = place { $0.somethingPlaying = true }
        for key in [PlayerKey.louder, .quieter, .back, .forward] {
            XCTAssertEqual(ArrowKeys.owner(of: key, at: playing), .player)
        }
        // With nothing playing they're left alone: the sidebar moves, as macOS has it.
        XCTAssertEqual(ArrowKeys.owner(of: .quieter, at: place { _ in }), .theirs)
        XCTAssertEqual(ArrowKeys.owner(of: .forward, at: place { _ in }), .theirs)
    }

    func testASongListKeepsUpAndDownAndGivesLeftAndRightToTheSong() {
        let inList = place { $0.somethingPlaying = true; $0.pageListHasKeyboard = true }
        XCTAssertEqual(ArrowKeys.owner(of: .louder, at: inList), .theirs)
        XCTAssertEqual(ArrowKeys.owner(of: .quieter, at: inList), .theirs)
        XCTAssertEqual(ArrowKeys.owner(of: .back, at: inList), .player)
        XCTAssertEqual(ArrowKeys.owner(of: .forward, at: inList), .player)
        // Nothing playing: nothing to go back and on in.
        XCTAssertEqual(ArrowKeys.owner(of: .forward, at: place { $0.pageListHasKeyboard = true }), .theirs)
    }

    func testOnHomeTheArrowsAreTheCards() {
        let home = place { $0.cardsShowing = true; $0.somethingPlaying = true }
        for key in [PlayerKey.louder, .quieter, .back, .forward] {
            XCTAssertEqual(ArrowKeys.owner(of: key, at: home), .cards)
        }
    }

    func testTheSidebarCanBeGoneThroughPastHome() {
        let passing = place { $0.cardsShowing = true; $0.arrowingSidebar = true }
        XCTAssertEqual(ArrowKeys.owner(of: .quieter, at: passing), .theirs)
        XCTAssertEqual(ArrowKeys.owner(of: .louder, at: passing), .theirs)
        // Right steps off the sidebar into the cards.
        XCTAssertEqual(ArrowKeys.owner(of: .forward, at: passing), .cards)
    }

    // MARK: the highlight on a page of cards

    func testTheFirstArrowPutsTheHighlightOnTheFirstCardThereIs() {
        let rows: [CardFocus.Row] = [.empty, .cards(4), .cards(2)]
        XCTAssertEqual(CardFocus.step(from: nil, key: .forward, rows: rows), .to(CardFocus(row: 1, column: 0)))
        XCTAssertEqual(CardFocus.step(from: nil, key: .quieter, rows: rows), .to(CardFocus(row: 1, column: 0)))
        XCTAssertEqual(CardFocus.step(from: nil, key: .back, rows: [.empty]), .stay)
        XCTAssertEqual(CardFocus.step(from: nil, key: .back, rows: [.empty, .unseen]), .reveal(row: 1))
    }

    func testLeftAndRightGoAlongTheRowAndOnToItsNextCards() {
        let rows: [CardFocus.Row] = [.cards(3)]
        let middle = CardFocus(row: 0, column: 1)
        XCTAssertEqual(CardFocus.step(from: middle, key: .forward, rows: rows), .to(CardFocus(row: 0, column: 2)))
        XCTAssertEqual(CardFocus.step(from: middle, key: .back, rows: rows), .to(CardFocus(row: 0, column: 0)))
        XCTAssertEqual(
            CardFocus.step(from: CardFocus(row: 0, column: 2), key: .forward, rows: rows),
            .page(row: 0, forward: true))
        XCTAssertEqual(
            CardFocus.step(from: CardFocus(row: 0, column: 0), key: .back, rows: rows),
            .page(row: 0, forward: false))
    }

    func testUpAndDownGoToTheNearestRowWithCards() {
        let rows: [CardFocus.Row] = [.cards(6), .empty, .cards(2), .unseen]
        // Down from the fifth card of six: the row under has only two, so its last.
        XCTAssertEqual(
            CardFocus.step(from: CardFocus(row: 0, column: 4), key: .quieter, rows: rows),
            .to(CardFocus(row: 2, column: 1)))
        XCTAssertEqual(
            CardFocus.step(from: CardFocus(row: 2, column: 1), key: .louder, rows: rows),
            .to(CardFocus(row: 0, column: 1)))
        // Further down hasn't been scrolled to yet: the page is asked to show it.
        XCTAssertEqual(CardFocus.step(from: CardFocus(row: 2, column: 0), key: .quieter, rows: rows), .reveal(row: 3))
        XCTAssertEqual(CardFocus.step(from: CardFocus(row: 0, column: 0), key: .louder, rows: rows), .stay)
    }

    func testAHighlightOnARowThatLostItsCardsStartsAgain() {
        let rows: [CardFocus.Row] = [.empty, .cards(2)]
        XCTAssertEqual(
            CardFocus.step(from: CardFocus(row: 0, column: 3), key: .forward, rows: rows),
            .to(CardFocus(row: 1, column: 0)))
        // A row showing fewer cards than before: the highlight is taken as on its last.
        XCTAssertEqual(
            CardFocus.step(from: CardFocus(row: 1, column: 5), key: .back, rows: rows),
            .to(CardFocus(row: 1, column: 0)))
    }
}
