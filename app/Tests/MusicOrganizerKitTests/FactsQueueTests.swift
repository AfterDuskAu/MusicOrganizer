import XCTest

@testable import MusicOrganizerKit

/// The titles waiting for TMDB's word, asked steadily (2026-11-09): a question that fails puts
/// its title back in line, and only the engine's own "couldn't ask" counts as a try.
final class FactsQueueTests: XCTestCase {
    func testTitlesAreAskedInTheOrderTheyCameEachOnce() {
        var line = FactsQueue()
        line.add("tt1", type: "movie", fields: ["rating"])
        line.add("tt2", type: "series", fields: ["rating"])
        line.add("tt1", type: "movie", fields: ["language"])  // still in line: one question
        XCTAssertTrue(line.count == 2)
        let first = line.next()
        XCTAssertTrue(first?.id == "tt1" && first?.fields == ["rating", "language"])
        // Out already with these: not put in line again.
        line.add("tt1", type: "movie", fields: ["rating"])
        XCTAssertTrue(line.next()?.id == "tt2")
        XCTAssertTrue(line.next() == nil)
        line.answered(first!)
        XCTAssertTrue(line.count == 1)  // tt2, still out
    }

    func testTheEngineBeingAwayNeverCountsAndTheTitleGoesFirst() {
        var line = FactsQueue()
        line.add("tt1", type: "movie", fields: ["rating"])
        line.add("tt2", type: "movie", fields: ["rating"])
        // A profile changed: the engine restarts. Asked any number of times, never given up.
        for _ in 0..<(FactsQueue.mostTries * 3) {
            let one = line.next()!
            XCTAssertTrue(one.id == "tt1")
            XCTAssertTrue(line.failed(one, counts: false))
        }
        XCTAssertTrue(line.count == 2)
    }

    func testAFailedTitleGoesToTheEndAndIsGivenUpAfterItsTries() {
        var line = FactsQueue()
        line.add("tt1", type: "movie", fields: ["rating"])
        line.add("tt2", type: "movie", fields: ["rating"])
        var one = line.next()!
        XCTAssertTrue(line.failed(one, counts: true))
        XCTAssertTrue(line.next()?.id == "tt2")
        for _ in 1..<(FactsQueue.mostTries - 1) {
            one = line.next()!
            XCTAssertTrue(one.id == "tt1" && line.failed(one, counts: true))
        }
        one = line.next()!
        XCTAssertFalse(line.failed(one, counts: true))  // given up: shown as not known
        XCTAssertTrue(line.next() == nil)
        // Wanted again later (the app opened on it again): a fresh start.
        line.add("tt1", type: "movie", fields: ["rating"])
        one = line.next()!
        XCTAssertTrue(line.failed(one, counts: true))
    }

    func testAnAnswerAboutTheRatingAloneKeepsTheLanguageKnown() {
        let known = TitleFacts(id: "tt1", language: "en", rating: nil)
        let said = TitleFacts(id: "tt1", language: nil, rating: "PG")
        let merged = FactsQueue.merged(known, said, fields: ["rating"])
        XCTAssertTrue(merged == TitleFacts(id: "tt1", language: "en", rating: "PG"))
        XCTAssertTrue(FactsQueue.merged(nil, said, fields: ["rating", "language"]).language == nil)
    }
}
