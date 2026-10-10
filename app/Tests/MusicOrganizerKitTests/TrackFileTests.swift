import XCTest

@testable import MusicOrganizerKit

final class TrackFileTests: XCTestCase {
    /// What Show in Finder points at, and what a song dragged out of the app is.
    func testWhereASongsFileIs() {
        let root = URL(fileURLWithPath: "/Volumes/Made Up/Library", isDirectory: true)
        let song = Track(path: "Music/The Band/First (2001)/01 Opening.m4a", title: "Opening")
        XCTAssertEqual(
            song.file(in: root)?.path, "/Volumes/Made Up/Library/Music/The Band/First (2001)/01 Opening.m4a")
        XCTAssertEqual(song.file(in: root)?.isFileURL, true)
        // A song played straight from the music service has no file, and neither has
        // any song before the library's folder is known.
        XCTAssertNil(Track(path: "yt:abcdefghijk", title: "Streamed").file(in: root))
        XCTAssertNil(song.file(in: nil))
    }
}
