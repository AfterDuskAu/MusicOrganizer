import XCTest

@testable import MusicOrganizerKit

/// The terms and the privacy policy the app shows are the ones kept in `docs/`.
final class LegalTests: XCTestCase {
    private var docs: URL {
        URL(fileURLWithPath: #filePath)  // app/Tests/MusicOrganizerKitTests/LegalTests.swift
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().appendingPathComponent("docs")
    }

    func testTheWordsInDocsAreTheWordsTheAppShows() throws {
        for (file, title, parts) in [
            ("TERMS.md", "Terms of Use", Legal.terms), ("PRIVACY.md", "Privacy Policy", Legal.privacy),
        ] {
            let page = docs.appendingPathComponent(file)
            if ProcessInfo.processInfo.environment["MUSICORG_WRITE_LEGAL"] != nil {
                try Legal.page(title, parts).write(to: page, atomically: true, encoding: .utf8)
            }
            let kept = try String(contentsOf: page, encoding: .utf8)
            XCTAssertEqual(
                kept, Legal.page(title, parts),
                "\(file) isn't what the app shows. Run this test with MUSICORG_WRITE_LEGAL=1 to write it afresh.")
        }
    }

    func testTheTermsSayWhoAnswersForWhatTheAppIsUsedFor() {
        let all = Legal.terms.map(\.text).joined(separator: " ")
        XCTAssertTrue(all.contains("solely the responsibility of the person using it"))
        XCTAssertTrue(all.contains("no warranty"))
        // The privacy policy doesn't name a service the app's words never name.
        XCTAssertFalse((Legal.terms + Legal.privacy).contains { $0.text.contains("YouTube") })
    }
}
