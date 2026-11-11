import XCTest

@testable import MusicOrganizerKit

/// A picture is asked for again when its server drops or stalls the request (2026-11-11).
final class PictureFetchTests: XCTestCase {
    private let url = URL(string: "https://pictures.example/poster.jpg")!

    private func answer(_ code: Int) -> URLResponse {
        HTTPURLResponse(url: url, statusCode: code, httpVersion: "HTTP/2", headerFields: nil)!
    }

    func testADroppedOrStalledRequestIsAskedAgain() async {
        var asked = 0
        let got = await PictureFetch.data(waits: [0, 0, 0]) {
            asked += 1
            if asked == 1 { throw URLError(.networkConnectionLost) }
            if asked == 2 { throw URLError(.timedOut) }
            return (Data("poster".utf8), self.answer(200))
        }
        XCTAssertEqual(got, Data("poster".utf8))
        XCTAssertEqual(asked, 3)
    }

    func testAServersOwnFaultIsTriedAgainButAMissingPictureIsBelieved() async {
        var asked = 0
        let after503 = await PictureFetch.data(waits: [0, 0]) {
            asked += 1
            return (Data("poster".utf8), self.answer(asked == 1 ? 503 : 200))
        }
        XCTAssertNotNil(after503)
        XCTAssertEqual(asked, 2)

        asked = 0
        let gone = await PictureFetch.data(waits: [0, 0, 0]) {
            asked += 1
            return (Data(), self.answer(404))
        }
        XCTAssertNil(gone)
        XCTAssertEqual(asked, 1)
    }

    func testItGivesUpAfterEveryTryAndWhenTheCardHasGone() async {
        var asked = 0
        let never = await PictureFetch.data(waits: [0, 0, 0]) {
            asked += 1
            throw URLError(.timedOut)
        }
        XCTAssertNil(never)
        XCTAssertEqual(asked, 3)

        asked = 0
        let cancelled = await PictureFetch.data(waits: [0, 0, 0]) {
            asked += 1
            throw URLError(.cancelled)
        }
        XCTAssertNil(cancelled)
        XCTAssertEqual(asked, 1)
    }
}
