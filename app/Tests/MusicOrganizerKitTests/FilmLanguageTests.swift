import XCTest

@testable import MusicOrganizerKit

/// The standard language (the owner, 2026-10-18): a film's sound in it when it has it, and
/// its subtitles only ever in it.
final class FilmLanguageTests: XCTestCase {
    private func options(_ code: String) -> [String: String] {
        Dictionary(uniqueKeysWithValues: FilmLanguage.playerOptions(for: code))
    }

    func testEnglishIsAskedForByEveryTagAFilmMayUse() {
        let english = options("en")
        XCTAssertEqual(english["alang"], "en,eng")
        XCTAssertEqual(english["slang"], "en,eng")
        // No subtitles in another language when there are none in English.
        XCTAssertEqual(english["subs-fallback"], "no")
        // English subtitles show with English sound, as subtitles showed before.
        XCTAssertEqual(english["subs-with-matching-audio"], "yes")
    }

    func testOlderMatroskaTagsCountToo() {
        XCTAssertEqual(options("de")["alang"], "de,deu,ger")
        XCTAssertEqual(options("zh")["slang"], "zh,zho,chi")
    }

    func testEachFilmsOwnIsHowFilmsPlayedBefore() {
        XCTAssertEqual(options(FilmLanguage.filmsOwn), ["subs-fallback": "yes"])
        XCTAssertNil(options(FilmLanguage.filmsOwn)["alang"])
        // A code that isn't offered (a setting from a later version) is the same.
        XCTAssertEqual(options("xx"), ["subs-fallback": "yes"])
    }

    func testSettingsListsLanguagesByNameWithEnglishAsStandard() {
        XCTAssertEqual(FilmLanguage.standard, "en")
        XCTAssertEqual(FilmLanguage.name("en"), "English")
        XCTAssertEqual(FilmLanguage.name("tr"), "Turkish")
        XCTAssertEqual(FilmLanguage.name(FilmLanguage.filmsOwn), "Each film's own")
        let names = FilmLanguage.listed.map(FilmLanguage.name)
        XCTAssertEqual(names, names.sorted())
        XCTAssertEqual(Set(FilmLanguage.listed).count, FilmLanguage.languages.count)
    }
}
