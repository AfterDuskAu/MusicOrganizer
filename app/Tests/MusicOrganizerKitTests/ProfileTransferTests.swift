import XCTest

@testable import MusicOrganizerKit

final class ProfileTransferTests: XCTestCase {
    private func scratch() throws -> UserDefaults {
        try XCTUnwrap(UserDefaults(suiteName: "transfer-test-\(UUID().uuidString)"))
    }

    func testOnlyTheNamedSettingsTravelAndComeBack() throws {
        let here = try scratch()
        here.set("warm", forKey: "appLook")
        here.set(true, forKey: NextEpisode.key)
        here.set(["a", "b"], forKey: "sidebarHidden")
        here.set(true, forKey: "shareWithDevices")  // sharing never starts by itself elsewhere
        here.set("/Users/someone/Music", forKey: "libraryRoot")
        let packed = SettingsCarrier.pack(from: here)
        XCTAssertTrue(JSONSerialization.isValidJSONObject(packed))
        let values = try XCTUnwrap(packed["values"] as? [String: Any])
        XCTAssertEqual(Set(values.keys), ["appLook", NextEpisode.key, "sidebarHidden"])

        let there = try scratch()
        // Whatever an export says, only the named settings are taken from it.
        var tampered = packed
        var more = values
        more["shareWithDevices"] = ["value": true]
        tampered["values"] = more
        XCTAssertEqual(SettingsCarrier.unpack(tampered, into: there), 3)
        XCTAssertEqual(there.string(forKey: "appLook"), "warm")
        XCTAssertTrue(there.bool(forKey: NextEpisode.key))
        XCTAssertEqual(there.stringArray(forKey: "sidebarHidden"), ["a", "b"])
        XCTAssertNil(there.object(forKey: "shareWithDevices"))
        XCTAssertNil(there.object(forKey: "libraryRoot"))
    }

    func testAWayAFilmWasPlayedNeverTravels() throws {
        let here = try scratch()
        let made = "tok" + String(repeating: "9", count: 20)  // as a plugin's set-up might hold
        let said = #"{"kind":"url","url":"https://films.invalid/\#(made)/film.mp4","trackers":[]}"#
        var entry = WatchHistory.Entry(id: "tt1", kind: .movie, name: "A Film", seconds: 30, length: 100)
        entry.stream = try JSONDecoder().decode(MediaStream.self, from: Data(said.utf8))
        entry.playName = "A Film"
        here.set(try JSONEncoder().encode([entry]), forKey: WatchHistory.key)
        let places = ["https://films.invalid/\(made)/film.mp4": ["seconds": 30.0, "at": 0.0],
                      "/Movies/A Film.mp4": ["seconds": 12.0, "at": 0.0]]
        here.set(try JSONSerialization.data(withJSONObject: places), forKey: FilmPositions.key)

        let packed = SettingsCarrier.pack(from: here)
        let written = String(decoding: try JSONSerialization.data(withJSONObject: packed), as: UTF8.self)
        let values = try XCTUnwrap(packed["values"] as? [String: [String: String]])
        let history = try XCTUnwrap(Data(base64Encoded: try XCTUnwrap(values[WatchHistory.key]?["data"])))
        XCTAssertFalse(String(decoding: history, as: UTF8.self).contains(made))
        XCTAssertFalse(written.contains(made))
        // What was watched, and how far, still goes.
        let back = try JSONDecoder().decode([WatchHistory.Entry].self, from: history)
        XCTAssertEqual(back.map(\.name), ["A Film"])
        XCTAssertEqual(back.first?.seconds, 30)
        XCTAssertNil(back.first?.stream)
        let positions = try XCTUnwrap(Data(base64Encoded: try XCTUnwrap(values[FilmPositions.key]?["data"])))
        let kept = try XCTUnwrap(JSONSerialization.jsonObject(with: positions) as? [String: Any])
        XCTAssertEqual(Array(kept.keys), ["/Movies/A Film.mp4"])
    }

    func testWhatAnImportBringsIsSaidInPlainWords() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let said = #"{"profile":"D","songs":{"brought":2,"already":1,"beside":1},"#
            + #""films":{"brought":0,"already":0},"records":"kept_own","addons":1}"#
        let summary = try decoder.decode(TransferSummary.self, from: Data(said.utf8))
        let lines = summary.lines(done: false)
        XCTAssertEqual(lines.first, "2 songs and videos would come in; 1 are in this library already.")
        XCTAssertTrue(lines.contains { $0.contains("nothing of yours is written over") })
        XCTAssertEqual(lines.last, "1 plugin would be added.")
        XCTAssertEqual(lines.count, 3)  // no line about films: there are none
    }

    func testAPartSaysWhatItAmountsTo() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let said = #"{"parts":{"music":{"files":3,"bytes":3000000},"movies":{"files":0,"bytes":0},"#
            + #""records":{"favourites":1,"playlists":2},"settings":{"addons":4,"addons_left_out":["Set Up"]}}}"#
        let contents = try decoder.decode(ProfileContents.self, from: Data(said.utf8))
        XCTAssertTrue(contents.about("music").hasPrefix("3 files · "))
        XCTAssertEqual(contents.about("movies"), "nothing yet")
        XCTAssertEqual(contents.about("records"), "2 playlists, 1 favourite, and what's been played")
        XCTAssertEqual(contents.about("settings"), "your choices in Settings, and 4 plugins")
        XCTAssertEqual(contents.parts["settings"]?.addonsLeftOut, ["Set Up"])
    }
}
