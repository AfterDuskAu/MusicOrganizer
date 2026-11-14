import XCTest
@testable import MusicOrganizerKit

/// A list of titles pasted into Settings (the owner, 2026-11-13: "paste 50 shows you have in notes,
/// and it detects and adds them to the correct list"): each line tidied into a name, and of what
/// the searches find, the title it means and how sure that is.
final class PastedListTests: XCTestCase {
    private func one(_ text: String) -> PastedList.Line? {
        PastedList.lines(text, thisYear: 2026).first
    }

    // MARK: reading lines

    func testBulletsNumbersAndTicksAreTakenOff() {
        let cases: [(String, String)] = [
            ("• Breaking Bad", "Breaking Bad"),
            ("◦ Dark", "Dark"),
            ("- The Bear", "The Bear"),
            ("* Severance", "Severance"),
            ("- [ ] Andor", "Andor"),
            ("- [x] Andor", "Andor"),
            ("☐ Shōgun", "Shōgun"),
            ("✅ Succession", "Succession"),
            ("1. Dune", "Dune"),
            ("12) The Wire", "The Wire"),
            ("#3 Fargo", "Fargo"),
            ("(4) Ozark", "Ozark"),
            ("5 - Lost", "Lost"),
            ("1.Dune", "Dune"),
            ("Dune ✓", "Dune"),
            ("Dune - watched", "Dune"),
            ("Dune (watched)", "Dune"),
            ("Dune 8/10", "Dune"),
            ("Dune ★★★★½", "Dune"),
            ("“Dune”", "Dune"),
            ("Dune,", "Dune"),
            ("   Dune   ", "Dune"),
        ]
        for (pasted, name) in cases {
            XCTAssertEqual(one(pasted)?.name, name, pasted)
        }
    }

    func testNumbersThatArePartOfTheNameStay() {
        let cases = [
            "10 Things I Hate About You", "12 Angry Men", "1917", "24", "300", "(500) Days of Summer",
            "3:10 to Yuma", "2001: A Space Odyssey", "9-1-1", "Ocean's 11", "Se7en", "Blade Runner 2049",
            "Fahrenheit 9/11", "The Unseen", "Airplane!", "Mother!", "What If...?",
        ]
        for pasted in cases {
            let line = one(pasted)
            XCTAssertNotNil(line, pasted)
            if pasted == "What If...?" {
                XCTAssertEqual(line?.name, "What If...?")
            } else {
                XCTAssertEqual(line?.name, pasted, pasted)
            }
            XCTAssertNil(line?.year, pasted)
        }
    }

    func testSeasonsAndEpisodesMakeItASeries() {
        let cases: [(String, String)] = [
            ("Friends S2", "Friends"),
            ("Friends S02", "Friends"),
            ("The Office S03E05", "The Office"),
            ("Friends 2x05", "Friends"),
            ("Breaking Bad Season 1-5", "Breaking Bad"),
            ("Breaking Bad Seasons 1 to 3", "Breaking Bad"),
            ("Doctor Who series 7", "Doctor Who"),
            ("The Crown Season One", "The Crown"),
            ("Friends S2 - rewatching", "Friends"),
            ("Dark (TV series)", "Dark"),
            ("Chernobyl (miniseries)", "Chernobyl"),
            ("Shōgun - TV show", "Shōgun"),
            ("Normal People TV series", "Normal People"),
            ("Frieren (anime)", "Frieren"),
            ("Lost - The Complete Series", "Lost"),
        ]
        for (pasted, name) in cases {
            let line = one(pasted)
            XCTAssertEqual(line?.name, name, pasted)
            XCTAssertEqual(line?.kind, "series", pasted)
        }
        // "The Truman Show" is a film's name, not a series' mark; nor is "Show" alone at the end.
        XCTAssertEqual(one("The Truman Show")?.name, "The Truman Show")
        XCTAssertNil(one("The Truman Show")?.kind)
        XCTAssertEqual(one("Dune (movie)")?.kind, "movie")
    }

    func testYears() {
        XCTAssertEqual(one("Dune (2021)").map { [$0.name, "\($0.year ?? 0)"] }, ["Dune", "2021"])
        XCTAssertEqual(one("Dune [1984]")?.year, 1984)
        XCTAssertEqual(one("Dune (2021, Villeneuve)")?.year, 2021)
        // A span of years is a series'.
        let span = one("Breaking Bad (2008–2013)")
        XCTAssertEqual(span?.name, "Breaking Bad")
        XCTAssertEqual(span?.year, 2008)
        XCTAssertEqual(span?.kind, "series")
        XCTAssertEqual(one("Severance (2022-)")?.kind, "series")
        // A season's year after it.
        XCTAssertEqual(one("Breaking Bad S1-5 (2008)")?.year, 2008)
        // At the end, the year may be part of the name: both are kept.
        let end = one("Wonder Woman 1984")
        XCTAssertEqual(end?.name, "Wonder Woman")
        XCTAssertEqual(end?.whole, "Wonder Woman 1984")
        XCTAssertEqual(end?.year, 1984)
        XCTAssertEqual(end?.searches, ["Wonder Woman", "Wonder Woman 1984"])
        XCTAssertEqual(one("Dune - 2021")?.year, 2021)
        // Not a year: too far ahead, or the whole name.
        XCTAssertNil(one("Blade Runner 2049")?.year)
        XCTAssertNil(one("1917")?.year)
        XCTAssertNil(one("Dune")?.whole)
    }

    func testHeadingsAreSkippedAndSayTheKindBelowThem() {
        let lines = PastedList.lines(
            """
            Movies:
            Dune
            Heat (1995)

            TV Shows
            - Dark
            - Fargo (movie)
            Watched:
            Up
            """, thisYear: 2026)
        XCTAssertEqual(lines.map(\.name), ["Dune", "Heat", "Dark", "Fargo", "Up"])
        XCTAssertEqual(lines.map(\.kind), ["movie", "movie", "series", "movie", nil])
        XCTAssertEqual(lines.map(\.id), [0, 1, 2, 3, 4])
    }

    func testBlankLinesNothingLeftAndTheSameTwiceAreSkipped() {
        let lines = PastedList.lines("Dune\n\n   \n-\n•\nSeason 2\n1.\nDUNE\ndune ✓\nDune (2021)\n✓✓\n", thisYear: 2026)
        XCTAssertEqual(lines.map(\.text), ["Dune", "Dune (2021)"])
    }

    func testWindowsLineEndsAndALongPaste() {
        XCTAssertEqual(PastedList.lines("Dune\r\nHeat\r\n", thisYear: 2026).map(\.name), ["Dune", "Heat"])
        let many = (1...400).map { "Film number \($0) part" }.joined(separator: "\n")
        XCTAssertEqual(PastedList.lines(many, thisYear: 2026).count, PastedList.most)
    }

    func testFiftyLinesFromNotes() {
        let pasted = (1...50).map { "\($0). Show \($0) S\($0 % 9 + 1)" }.joined(separator: "\n")
        let lines = PastedList.lines(pasted, thisYear: 2026)
        XCTAssertEqual(lines.count, 50)
        XCTAssertEqual(lines.first?.name, "Show 1")
        XCTAssertTrue(lines.allSatisfy { $0.kind == "series" })
    }

    // MARK: matching

    private func item(_ id: String, _ name: String, _ year: String?, _ type: String = "movie") -> MediaItem {
        pastedItem(id, name, year, type)
    }

    private func found(_ items: [MediaItem], popularity: [Double?]? = nil) -> [PastedList.Found] {
        items.enumerated().map { PastedList.Found($1, popularity: popularity?[$0], place: $0) }
    }

    func testTheSameNameAndYearIsSure() {
        let line = one("Dune (2021)")!
        let match = PastedList.match(line, among: found([
            item("tt1", "Dune", "1984"), item("tt2", "Dune", "2021"), item("tt3", "Dune: Part Two", "2024"),
        ]))
        XCTAssertEqual(match.item?.id, "tt2")
        XCTAssertEqual(match.sureness, .sure)
        XCTAssertEqual(match.others.map(\.id), ["tt1", "tt3"])
        XCTAssertTrue(PastedList.ticked(match.sureness))
    }

    func testOneTitleOfTheNameIsSureAndCapitalsPunctuationAndTheDontMatter() {
        let match = PastedList.match(one("office")!, among: found([item("tt9", "The Office!", "2005", "series")]))
        XCTAssertEqual(match.item?.id, "tt9")
        XCTAssertEqual(match.sureness, .sure)
        let accents = PastedList.match(one("Shogun")!, among: found([item("tt8", "Shōgun", "2024", "series")]))
        XCTAssertEqual(accents.sureness, .sure)
    }

    func testSeveralOfTheNameWithoutAYearAreLikelyTheMostPopularFirst() {
        let match = PastedList.match(
            one("Dune")!,
            among: found([item("tt1", "Dune", "1984"), item("tt2", "Dune", "2021"), item("tt5", "Dune", "2000", "series")],
                popularity: [40, 200, 30]))
        XCTAssertEqual(match.item?.id, "tt2")
        XCTAssertEqual(match.sureness, .likely)
        XCTAssertNotNil(match.why)
        XCTAssertTrue(PastedList.ticked(match.sureness))
        // Ten times as popular as the next of the name: sure ("Friends" the series, not a film of it).
        let friends = PastedList.match(
            one("Friends")!, among: found([item("tt7", "Friends", "2011"), item("tt108778", "Friends", "1994", "series")], popularity: [2, 300]))
        XCTAssertEqual(friends.item?.id, "tt108778")
        XCTAssertEqual(friends.sureness, .sure)
    }

    func testTheKindALineSaysWins() {
        let all = found([item("tt7", "Friends", "2011"), item("tt108778", "Friends", "1994", "series")], popularity: [300, 2])
        let series = PastedList.match(one("Friends S3")!, among: all)
        XCTAssertEqual(series.item?.id, "tt108778")
        XCTAssertEqual(series.sureness, .sure)
        let film = PastedList.match(one("Friends (movie)")!, among: all)
        XCTAssertEqual(film.item?.id, "tt7")
        // Only a film of the name, but the line says a series: the closest, not ticked at first.
        let wrong = PastedList.match(one("Heat (TV series)")!, among: found([item("tt113277", "Heat", "1995")]))
        XCTAssertEqual(wrong.sureness, .closest)
        XCTAssertFalse(PastedList.ticked(wrong.sureness))
    }

    func testANumberAtTheEndIsTheNameWhenATitleHasIt() {
        let match = PastedList.match(
            one("Wonder Woman 1984")!,
            among: found([item("tt0451279", "Wonder Woman", "2017"), item("tt7126948", "Wonder Woman 1984", "2020")]))
        XCTAssertEqual(match.item?.id, "tt7126948")
        XCTAssertEqual(match.sureness, .sure)
        let dune = PastedList.match(one("Dune 2021")!, among: found([item("tt1", "Dune", "1984"), item("tt2", "Dune", "2021")]))
        XCTAssertEqual(dune.item?.id, "tt2")
        XCTAssertEqual(dune.sureness, .sure)
    }

    func testAYearOffOrAnotherYearIsLikely() {
        let off = PastedList.match(one("Heat (1996)")!, among: found([item("tt113277", "Heat", "1995")]))
        XCTAssertEqual(off.sureness, .likely)
        let other = PastedList.match(one("Heat (2010)")!, among: found([item("tt113277", "Heat", "1995")]))
        XCTAssertEqual(other.item?.id, "tt113277")
        XCTAssertEqual(other.sureness, .likely)
        XCTAssertEqual(other.why, "Its year is 1995, not 2010: check it's the one.")
        // A series named with a later season's year is still it.
        let crown = PastedList.match(one("The Crown 2020")!, among: found([item("tt4786824", "The Crown", "2016–2023", "series")]))
        XCTAssertEqual(crown.sureness, .sure)
    }

    func testNoTitleOfTheNameIsTheClosestOrNothing() {
        let close = PastedList.match(one("Avengers Infinity")!, among: found([item("tt4154756", "Avengers: Infinity War", "2018")]))
        XCTAssertEqual(close.item?.id, "tt4154756")
        XCTAssertEqual(close.sureness, .closest)
        let none = PastedList.match(one("Zzqx")!, among: found([item("tt1", "Dune", "2021")]))
        XCTAssertNil(none.item)
        XCTAssertEqual(none.sureness, .none)
        XCTAssertFalse(PastedList.ticked(.none))
        XCTAssertEqual(PastedList.match(one("Dune")!, among: []).sureness, .none)
    }

    func testTMDBsFindsFirstAndAPluginsGivesItsId() {
        let tmdb = [
            PastedList.Found(item("tv:1396", "Breaking Bad", "2008", "series"), popularity: 300, place: 0),
            PastedList.Found(item("movie:5", "Breaking Bad", "2011"), popularity: 1, place: 1),
        ]
        let plugins = [item("tt0903747", "Breaking Bad", "2008–2013", "series"), item("tt2", "Breaking Badly", "2020")]
        let both = PastedList.together(tmdb: tmdb, plugins: plugins, hasId: { $0.hasPrefix("tt") })
        XCTAssertEqual(both.map(\.item.id), ["tt0903747", "movie:5", "tt2"])
        XCTAssertEqual(both[0].popularity, 300)
        let match = PastedList.match(one("Breaking Bad")!, among: both)
        XCTAssertEqual(match.item?.id, "tt0903747")
        XCTAssertEqual(match.sureness, .sure)
    }

    // MARK: what's added

    func testTheWatchlistOnlyAddsNewOnesFirst() {
        var list = Watchlist([item("tt1", "A", nil), item("tt2", "B", nil)], takenOff: ["tt3", "tt9"])
        let added = list.put([item("tt3", "C", nil), item("tt1", "A", nil), item("tt4", "D", nil), item("tt3", "C", nil)])
        XCTAssertEqual(added, 2)
        XCTAssertEqual(list.items.map(\.id), ["tt3", "tt4", "tt1", "tt2"])
        XCTAssertEqual(list.takenOff, ["tt9"])
    }

    func testMarkedTitlesKeepTheNewestFirstEachOnce() {
        var marked = MarkedTitles([item("tt1", "A", nil), item("tt2", "B", nil)])
        marked.note([item("tt2", "B", nil), item("tt3", "C", nil)])
        XCTAssertEqual(marked.items.map(\.id), ["tt2", "tt3", "tt1"])
        let defaults = UserDefaults(suiteName: "PastedListTests-\(UUID().uuidString)")!
        marked.save(in: defaults)
        XCTAssertEqual(MarkedTitles.saved(in: defaults), marked)
        XCTAssertTrue(SettingsCarrier.records.contains(MarkedTitles.key))
        XCTAssertTrue(ProfileSettings.belongsToProfile(MarkedTitles.key))
    }
}

/// A line and the options under it (the owner, 2026-11-13: "law and order ... you can select
/// several or just 1, 1 tap and it deselects or selects").
final class PastedReadingTests: XCTestCase {
    private func line(_ text: String) -> PastedList.Line { PastedList.lines(text, thisYear: 2026)[0] }

    private func found(_ items: [(String, String, String?, String)], popularity: [Double?]? = nil) -> [PastedList.Found] {
        items.enumerated().map { place, one in
            PastedList.Found(
                pastedItem(one.0, one.1, one.2, one.3), popularity: popularity?[place], place: place)
        }
    }

    /// "Law & Order": several series of nearly that name, none of them sure. Its options are
    /// shown, nothing is ticked until the owner taps, and several can be.
    func testLawAndOrderOffersItsOptions() {
        let all = found([
            ("tt0098844", "Law & Order", "1990–2010", "series"),
            ("tt0203259", "Law & Order: Special Victims Unit", "1999", "series"),
            ("tt0275140", "Law & Order: Criminal Intent", "2001", "series"),
        ], popularity: [30, 90, 20])
        var reading = PastedList.reading(line("Law & order"), among: all)
        // The one of exactly that name leads, even though another is more popular.
        XCTAssertEqual(reading.options.first?.id, "tt0098844")
        XCTAssertEqual(reading.options.count, 3)
        XCTAssertEqual(reading.sureness, .sure)
        XCTAssertEqual(reading.chosen, ["tt0098844"])
        // A tap adds a second; another tap on the first takes it off.
        reading = PastedList.tapped("tt0203259", in: reading)
        XCTAssertEqual(reading.chosen, ["tt0098844", "tt0203259"])
        XCTAssertEqual(reading.picked.map(\.name), ["Law & Order", "Law & Order: Special Victims Unit"])
        reading = PastedList.tapped("tt0098844", in: reading)
        XCTAssertEqual(reading.picked.map(\.id), ["tt0203259"])
        reading = PastedList.tapped("tt0203259", in: reading)
        XCTAssertTrue(reading.picked.isEmpty)
    }

    /// A name spelled a little wrong: nothing is ticked, and the closest are offered.
    func testASlightlyWrongNameIsNotTickedButOffered() {
        let reading = PastedList.reading(
            line("Breking Bad"),
            among: found([("tt0903747", "Breaking Bad", "2008", "series"), ("tt2", "Breaking Bad Movie", "2019", "movie")]))
        XCTAssertTrue(reading.chosen.isEmpty)
        XCTAssertTrue(reading.needsALook)
        XCTAssertFalse(reading.foundNothing)
        XCTAssertEqual(reading.options.first?.id, "tt0903747")
    }

    /// Two of the same name and no year: ticked, and said to be checked, with the other under it.
    func testTwoOfTheSameNameAreTickedAndSaidToBeChecked() {
        let reading = PastedList.reading(
            line("Dune"), among: found([("tt1", "Dune", "1984", "movie"), ("tt2", "Dune", "2021", "movie")], popularity: [50, 60]))
        XCTAssertEqual(reading.sureness, .likely)
        XCTAssertEqual(reading.chosen, ["tt2"])
        XCTAssertNotNil(reading.why)
        XCTAssertEqual(reading.options.count, 2)
    }

    func testWhatTheCardSays() {
        let sure = PastedList.reading(line("Heat (1995)"), among: found([("tt113277", "Heat", "1995", "movie")]))
        let look = PastedList.reading(line("Dune"), among: found([("tt1", "Dune", "1984", "movie"), ("tt2", "Dune", "2021", "movie")]))
        let none = PastedList.reading(line("Zzqx"), among: found([("tt1", "Dune", "2021", "movie")]))
        XCTAssertTrue(none.foundNothing)
        XCTAssertEqual(PastedList.said([]), "")
        XCTAssertEqual(PastedList.said([sure]), "1 of 1 found exactly")
        XCTAssertEqual(
            PastedList.said([sure, look, none]),
            "1 of 3 found exactly · 1 may be more than one — tap the right one · 1 found nothing")
    }

    /// The Mac says "click" where the owner's tablet says "tap" (the Kit's own default).
    func testTheMacSaysClick() {
        let look = PastedList.reading(
            line("Dune"), among: found([("tt1", "Dune", "1984", "movie"), ("tt2", "Dune", "2021", "movie")]))
        XCTAssertEqual(
            PastedList.said([look, look], choosing: "click"),
            "0 of 2 found exactly · 2 may be more than one — click the right ones")
        XCTAssertEqual(
            PastedList.said([look], choosing: "click"),
            "0 of 1 found exactly · 1 may be more than one — click the right one")
    }

    func testEverythingTickedEachTitleOnce() {
        let one = PastedList.reading(line("Heat (1995)"), among: found([("tt113277", "Heat", "1995", "movie")]))
        let two = PastedList.reading(line("Heat"), among: found([("tt113277", "Heat", "1995", "movie")]))
        XCTAssertEqual(PastedList.picked([one, two]).map(\.id), ["tt113277"])
    }
}

/// Names typed a little wrong (the owner, 2026-11-13: "maybe just slight spelling words").
final class PastedSpellingTests: XCTestCase {
    func testOneLetterInSixIsClose() {
        XCTAssertTrue(PastedList.isClose("breaking bad", "breking bad"))
        XCTAssertTrue(PastedList.isClose("the sopranos", "the sopranos "))
        XCTAssertTrue(PastedList.isClose("seinfeld", "seinfield"))
        XCTAssertTrue(PastedList.isClose("game of thrones", "game of thronez"))
        XCTAssertFalse(PastedList.isClose("up", "us"))
        XCTAssertFalse(PastedList.isClose("dune", "duke"))
        XCTAssertFalse(PastedList.isClose("heat", "head"))
        XCTAssertFalse(PastedList.isClose("friends", "fringe"))
    }

    func testApartGivesUpEarly() {
        XCTAssertEqual(PastedList.apart("abc", "abc", atMost: 2), 0)
        XCTAssertEqual(PastedList.apart("abc", "abd", atMost: 2), 1)
        XCTAssertEqual(PastedList.apart("", "abc", atMost: 2), 3)
        XCTAssertGreaterThan(PastedList.apart("abcdef", "zzzzzz", atMost: 2), 2)
    }
}

/// A film or series as the tests name it.
func pastedItem(_ id: String, _ name: String, _ year: String?, _ type: String = "movie") -> MediaItem {
    MediaItem(id: id, type: type, name: name, poster: nil, posterShape: "poster", year: year, rating: nil, genres: [])
}
