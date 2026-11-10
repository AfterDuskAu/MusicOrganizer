import XCTest

@testable import MusicOrganizerKit

/// The films on paired video apps, offered on the Mac (2026-11-10).
final class DeviceFilmsTests: XCTestCase {
    func testTheEnginesAnswerIsRead() throws {
        let said = #"""
            {"devices": [{"device": "d1", "profile": "default", "kind": "Android TV", "at": "2026-11-10T08:00:00Z",
              "films": [
                {"id": "dl-1", "kind": "movie", "title": "Heart of the Beast", "year": "2026", "show": "",
                 "season": null, "episode": null, "type": "mkv", "size": 4000000000,
                 "poster": "https://pictures.example/1.jpg", "item": "tt1", "here": false, "wanted": true,
                 "arriving": 0.25},
                {"id": "dl-2", "kind": "series", "title": "Pilot", "year": null, "show": "A Show",
                 "season": 1, "episode": 2, "type": "mp4", "size": 900, "here": true, "wanted": false}
              ]}]}
            """#
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let found = try decoder.decode(DeviceFilms.self, from: Data(said.utf8))
        let (film, episode) = (found.devices[0].films[0], found.devices[0].films[1])
        XCTAssertEqual(film.name, "Heart of the Beast (2026)")
        XCTAssertEqual(film.card.id, "tt1")
        XCTAssertEqual(film.card.poster, "https://pictures.example/1.jpg")
        XCTAssertEqual(film.arriving, 0.25)
        XCTAssertEqual(episode.name, "A Show · S1 E2 · Pilot")
        XCTAssertEqual(episode.card.type, "series")
        // Only what isn't here is offered.
        XCTAssertEqual(found.waiting.map(\.kind), ["Android TV"])
        XCTAssertEqual(found.waiting.first?.films.map(\.film.id), ["dl-1"])
        XCTAssertEqual(found.waitingCount, 1)
    }

    func testAFilmIsOfferedOnceHoweverManyAccountsOfTheDeviceSaidIt() {
        let film = DeviceFilms.Film(id: "dl-1", title: "A Film", year: "2021", size: 10)
        let asked = DeviceFilms.Film(id: "dl-1", title: "A Film", year: "2021", size: 10, wanted: true)
        let other = DeviceFilms.Film(id: "dl-9", title: "Another", size: 10)
        let found = DeviceFilms(devices: [
            .init(device: "tv-d", profile: "default", kind: "Android TV", films: [film]),
            .init(device: "tv-c", profile: "p_c", kind: "Android TV", films: [asked, other]),
            .init(device: "ipad", profile: "default", kind: "iPad", films: [film]),
        ])
        let waiting = found.waiting
        XCTAssertEqual(waiting.map(\.kind), ["Android TV", "iPad"])
        let tv = waiting[0].films
        XCTAssertEqual(tv.map(\.film.id), ["dl-1", "dl-9"])
        // Asked for by one of its pairings: shown as asked for, and every pairing is asked.
        XCTAssertTrue(tv[0].film.wanted)
        XCTAssertEqual(tv[0].from.map(\.device), ["tv-d", "tv-c"])
        XCTAssertEqual(found.waitingCount, 3)
        XCTAssertEqual(DeviceFilms().waitingCount, 0)
    }
}
