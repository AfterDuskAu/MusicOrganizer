import XCTest

@testable import MusicOrganizerKit

final class LibrarySortTests: XCTestCase {
    /// Songs with the names that sorting trips over: accents, capitals, a leading
    /// "The", the same title by two artists, the same album in two folders.
    private func songs(_ count: Int) -> [Track] {
        let titles = [
            "Élan", "elan", "The End", "End", "the end", "Zed", "ábaco", "Abaco", "Ｗｉｄｅ", "wide",
            "10 Years", "2 Hearts", "Über", "uber", "The The", "", "Señorita", "senorita",
        ]
        let artists = ["The Band", "the band", "Band", "Ábel", "Abel", "Zoë", "zoe", "The Zoë"]
        let albums = ["First", "first", "The First", "Élan", "Later", nil]
        return (0..<count).map { n in
            let artist = artists[(n / 3) % artists.count]
            let album = albums[(n / 5) % albums.count]
            return Track(
                path: "Music/\(artist)/\(album ?? "Singles") \(n % 7)/\(n).m4a",
                title: "\(titles[n % titles.count])\(n % 4 == 0 ? "" : " \(n % 11)")",
                artist: artist, album: album, year: n % 9 == 0 ? nil : 1990 + n % 30,
                track: n % 6 == 0 ? nil : n % 13, disc: n % 10 == 0 ? 2 : nil, trackId: "t_\(n)")
        }
    }

    /// Each name's sorting form is made once (2026-10-10), not at every comparison:
    /// the order must be exactly what comparing the names themselves gives.
    func testTheLibrarysOrderIsWhatComparingTheNamesGives() {
        let tracks = songs(700)
        let library = Library(tracks: tracks)
        XCTAssertEqual(
            library.tracks.map(\.path),
            tracks.sorted {
                (sortKey($0.title), sortKey($0.artistName), $0.path)
                    < (sortKey($1.title), sortKey($1.artistName), $1.path)
            }.map(\.path))
        XCTAssertEqual(
            library.albums.map(\.id),
            library.albums.sorted {
                (sortKey($0.artist), $0.year ?? 0, sortKey($0.title), $0.id)
                    < (sortKey($1.artist), $1.year ?? 0, sortKey($1.title), $1.id)
            }.map(\.id))
        XCTAssertEqual(
            library.artists.map(\.id),
            library.artists.sorted { (sortKey($0.name), $0.id) < (sortKey($1.name), $1.id) }.map(\.id))
        for album in library.albums {
            let inOrder = zip(album.tracks, album.tracks.dropFirst()).allSatisfy { one, next in
                (one.disc ?? 1, one.track ?? Int.max, fold(one.title))
                    <= (next.disc ?? 1, next.track ?? Int.max, fold(next.title))
            }
            XCTAssertTrue(inOrder, album.id)
            XCTAssertTrue(album.tracks.allSatisfy { $0.folder == album.id }, album.id)
        }
        // Every song is in one album and one only, and the albums in one artist each.
        XCTAssertEqual(library.albums.flatMap(\.tracks).map(\.path).sorted(), tracks.map(\.path).sorted())
        XCTAssertEqual(
            library.artists.flatMap(\.albums).map(\.id).sorted(), library.albums.map(\.id).sorted())
    }

    func testSortedByAKeyMadeOnce() {
        var made = 0
        let words = ["pear", "Apple", "fig", "apple", "Banana"]
        let sorted = words.sorted(keyed: { word -> (String, String) in
            made += 1
            return (word.lowercased(), word)
        }) { $0 < $1 }
        XCTAssertEqual(sorted, ["Apple", "apple", "Banana", "fig", "pear"])
        XCTAssertEqual(made, words.count)
    }
}
