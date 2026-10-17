import XCTest

@testable import MusicOrganizerKit

/// The URL page (2026-10-17): a web page's videos as the engine gives them, a download of
/// one remembered as a keep, and Move To's folders.
final class PageVideosTests: XCTestCase {
    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(type, from: Data(json.utf8))
    }

    private let answer = """
        {"title": "Two clips", "note": null, "videos": [
          {"url": "https://www.youtube.com/embed/aqz-KE-bpKQ", "item": null, "title": null,
           "duration_s": null, "thumbnail": null, "site": "YouTube", "qualities": null,
           "service": "youtube", "video_id": "aqz-KE-bpKQ"},
          {"url": "http://example.test/two.html", "item": 2, "title": "Two clips (1)",
           "duration_s": 3725, "thumbnail": null, "site": "example.test", "service": null,
           "video_id": null, "qualities": [
             {"id": "v720+bestaudio/v720", "label": "720p", "height": 720, "size": 1500000000},
             {"id": "0", "label": "MP4", "height": null, "size": null}]},
          {"url": "https://player.example.test/v/1", "item": null, "title": null,
           "duration_s": null, "thumbnail": null, "site": "Vimeo", "qualities": null,
           "service": null, "video_id": null}
        ]}
        """

    func testAPagesVideosAreReadAsTheEngineGivesThem() throws {
        let page = try decode(PageAnswer.self, answer)
        XCTAssertEqual(page.title, "Two clips")
        let (service, clip, linked) = (page.videos[0], page.videos[1], page.videos[2])
        XCTAssertTrue(service.isMusicService)
        XCTAssertFalse(service.isElsewhere)
        XCTAssertEqual(service.videoId, "aqz-KE-bpKQ")
        // The music service is never named on screen.
        XCTAssertEqual(service.name, "A video from the music service")
        XCTAssertEqual(service.from, "The music service")
        XCTAssertEqual(clip.item, 2)
        XCTAssertEqual(clip.length, "1:02:05")
        XCTAssertEqual(clip.qualities?.map(\.described), ["720p · 1.5 GB", "MP4"])
        XCTAssertEqual(clip.id, "http://example.test/two.html#2")
        XCTAssertNil(linked.qualities)
        XCTAssertEqual(linked.name, "A video from Vimeo")
        XCTAssertEqual(linked.saveName(page: "Two clips"), "Two clips")
        let read = linked.read(
            PageVideo(url: "https://player.example.test/v/1", title: "A Short", qualities: []))
        XCTAssertEqual(read.title, "A Short")
        XCTAssertEqual(read.site, "Vimeo")
        XCTAssertEqual(read.qualities, [])
        let sound = PageVideo(url: "https://soundcloud.com/a/b", service: "soundcloud")
        XCTAssertTrue(sound.isElsewhere)
    }

    func testADownloadFromAPageIsAKeepOfItsOwn() throws {
        let page = try decode(PageAnswer.self, answer)
        let clip = page.videos[1]
        let quality = try XCTUnwrap(clip.qualities?.first)
        let keep = PendingKeeps.Keep(
            page: clip, quality: quality, title: "Big Clip", place: .video, convert: false)
        XCTAssertTrue(keep.isPage)
        XCTAssertTrue(keep.isAddress)
        XCTAssertFalse(keep.isApart)
        XCTAssertEqual(keep.id, "http://example.test/two.html#2#v720+bestaudio/v720")
        XCTAssertEqual(keep.keepMethod, "page.keep")
        XCTAssertEqual(keep.statusMethod, "stream.status")
        XCTAssertEqual(keep.stopMethod, "stream.stop")
        XCTAssertEqual(keep.kindName, "Video")
        XCTAssertEqual(keep.placeName, "Videos")
        let asked = keep.asked
        XCTAssertEqual(asked["url"] as? String, "http://example.test/two.html")
        XCTAssertEqual(asked["item"] as? Int, 2)
        XCTAssertEqual(asked["quality"] as? String, "v720+bestaudio/v720")
        XCTAssertEqual(asked["kind"] as? String, "video")
        XCTAssertEqual(asked["title"] as? String, "Big Clip")
        XCTAssertEqual(asked["size"] as? Int, nil)  // the engine reckons its own
        XCTAssertNil(asked["info_hash"])
        XCTAssertEqual(keep.which["quality"] as? String, "v720+bestaudio/v720")
        XCTAssertEqual(keep.which["item"] as? Int, 2)
        // To 18+: kept apart, listed on Videos → 18+ only.
        let adult = PendingKeeps.Keep(page: clip, quality: quality, title: "A", place: .adult)
        XCTAssertTrue(adult.isApart)
        XCTAssertEqual(adult.asked["kind"] as? String, "adult")
        XCTAssertEqual(adult.placeName, "Movies")
        // Remembered and read back, beside a torrent's keep; and forgotten by its id.
        var pending = PendingKeeps()
        pending.add(keep)
        pending.add(PendingKeeps.Keep(infoHash: "ABC", trackers: [], title: "Film"))
        let defaults = try XCTUnwrap(UserDefaults(suiteName: "PageVideosTests"))
        defer { defaults.removePersistentDomain(forName: "PageVideosTests") }
        pending.save(to: defaults)
        var back = PendingKeeps.load(from: defaults)
        XCTAssertEqual(back, pending)
        back.remove(keep.id)
        XCTAssertEqual(back.keeps.map(\.id), ["abc"])
    }

    func testAKeepRememberedBeforePagesStillReads() throws {
        let old = #"{"keeps":[{"infoHash":"","url":"http://x.test/a.mp4","trackers":[],"title":"A","convert":true}]}"#
        let found = try JSONDecoder().decode(PendingKeeps.self, from: Data(old.utf8))
        XCTAssertFalse(found.keeps[0].isPage)
        XCTAssertEqual(found.keeps[0].keepMethod, "stream.keep")
        XCTAssertEqual(found.keeps[0].id, "http://x.test/a.mp4")
    }

    func testWhichFolderAKeptFileIsIn() {
        let movies = URL(fileURLWithPath: "/Users/me/Movies", isDirectory: true)
        let videos = movies.appendingPathComponent("Videos", isDirectory: true)
        func place(_ rel: String) -> KeptPlace? {
            KeptPlace.of(movies.appendingPathComponent(rel), movies: movies, videos: videos)
        }
        XCTAssertEqual(place("The Kid (1921).mkv"), .movie)
        XCTAssertEqual(place("Videos/A Talk.mp4"), .video)
        XCTAssertEqual(place("Series/A Show/Season 1/A Show S01E01.mkv"), .series)
        XCTAssertEqual(place("anime/Spirited Away.mkv"), .anime)
        XCTAssertEqual(place("18+/A Film.mp4"), .adult)
        XCTAssertEqual(place("Kept/Old.avi"), .movie)
        XCTAssertNil(KeptPlace.of(URL(fileURLWithPath: "/tmp/x.mp4"), movies: movies, videos: videos))
        // A videos folder chosen elsewhere.
        let clips = URL(fileURLWithPath: "/Volumes/Big/Clips", isDirectory: true)
        XCTAssertEqual(
            KeptPlace.of(clips.appendingPathComponent("a.mp4"), movies: movies, videos: clips), .video)
    }

    func testEighteenIsNeverOfferedInAChildsProfile() {
        XCTAssertEqual(KeptPlace.offered(isChild: false), [.movie, .series, .anime, .video, .adult])
        XCTAssertEqual(KeptPlace.offered(isChild: true), [.movie, .series, .anime, .video])
        XCTAssertEqual(KeptPlace.allCases.map(\.name), ["Videos", "Movies", "Series", "Anime", "18+"])
    }

    func testTheURLPageIsInMediaDiscoveryButNotForAChild() {
        let discovery = SidebarRows.all.filter { $0.group == "Media Discovery" }.map(\.key)
        XCTAssertEqual(discovery.suffix(3), ["adultFinder", "url", "downloads"])
        XCTAssertTrue(SidebarRows.offered("url", isChild: false))
        XCTAssertFalse(SidebarRows.offered("url", isChild: true))
        XCTAssertEqual(SidebarRows.write(nil, "url", shown: false), "url")
    }

    func testWhereAFilmWasLeftGoesWithItWhenMoved() {
        var places = FilmPositions()
        places.watched("/Movies/A.mkv", to: 600, of: 6000)
        places.moved(from: "/Movies/A.mkv", to: "/Movies/18+/A.mkv")
        XCTAssertNil(places.place(of: "/Movies/A.mkv"))
        XCTAssertEqual(places.place(of: "/Movies/18+/A.mkv"), 600)
        places.moved(from: "/Movies/Gone.mkv", to: "/Movies/B.mkv")  // nothing to carry
        XCTAssertNil(places.place(of: "/Movies/B.mkv"))
    }

    func testADownloadSaysWhichFolderItWentTo() {
        XCTAssertEqual(TorrentStatus.place(of: "/Users/me/Movies/A (2001).mkv"), "your Movies folder")
        XCTAssertEqual(TorrentStatus.place(of: "/Users/me/Movies/Videos/A.mp4"), "your Videos folder")
        XCTAssertEqual(
            TorrentStatus.place(of: "/Users/me/Movies/18+/A.mp4"), "the 18+ folder in your Movies folder")
        XCTAssertEqual(
            TorrentStatus.place(of: "/Users/me/Movies/Anime/A.mp4"), "the Anime folder in your Movies folder")
        XCTAssertEqual(
            TorrentStatus.place(of: "/Users/me/Movies/Series/Show/Season 1/Show S01E01.mkv"),
            "your Movies folder")
    }

    func testAKeepsErrorIsInTheAppsWords() {
        let said = Wording.plain(answer: ["keep_error": "That video is YouTube's or SoundCloud's."])
        XCTAssertEqual(
            (said as? [String: Any])?["keep_error"] as? String,
            "That video is the service's or SoundCloud's.")
    }
}
