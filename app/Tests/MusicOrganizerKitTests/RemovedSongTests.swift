import XCTest

@testable import MusicOrganizerKit

final class RemovedSongTests: XCTestCase {
    private let day: (String) -> String = { $0.isEmpty ? "" : "10 Oct 2026" }

    func testTheEnginesAnswerIsRead() throws {
        let json = """
            {"removed": [{"batch_id": "b_1", "op_id": 2, "title": "Melody", "artist": "Band",
              "path": "Music/Band/Tunes (2020)/03 Melody.m4a",
              "kept_at": "_Replaced/Band/Tunes (2020)/03 Melody.m4a",
              "removed_at": "2026-10-10T04:43:19.000000Z", "state": "kept",
              "restored_at": null, "together": 1, "video": false}]}
            """
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let found = try decoder.decode(RemovedAnswer.self, from: Data(json.utf8)).removed
        XCTAssertEqual(found.count, 1)
        XCTAssertEqual(found[0].id, "b_1/2")
        XCTAssertEqual(found[0].keptAt, "_Replaced/Band/Tunes (2020)/03 Melody.m4a")
        XCTAssertTrue(found[0].canPutBack)
        XCTAssertNotNil(engineDate(found[0].removedAt))
    }

    func testWhatALineOfTheLogSays() {
        let kept = RemovedSong(batchId: "b_1", title: "Melody", artist: "Band", removedAt: "t")
        XCTAssertEqual(kept.detail(day: day), "Band · deleted 10 Oct 2026")
        XCTAssertNil(kept.outcome(day: day))
        XCTAssertTrue(kept.canPutBack)

        let video = RemovedSong(batchId: "b_2", title: "Clip", removedAt: "", video: true)
        XCTAssertEqual(video.detail(day: day), "video · deleted from your library")

        let back = RemovedSong(
            batchId: "b_3", title: "Other", removedAt: "t", state: "restored", restoredAt: "t")
        XCTAssertEqual(back.outcome(day: day), "Put back 10 Oct 2026")
        XCTAssertFalse(back.canPutBack)
        XCTAssertEqual(
            RemovedSong(batchId: "b_4", title: "Gone", state: "missing").outcome(day: day),
            "Its file is no longer kept")
    }
}
