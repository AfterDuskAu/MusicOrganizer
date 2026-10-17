import XCTest

@testable import MusicOrganizerKit

/// Downloads from a plugin's web address, and the 18+ folder and page (the owner,
/// 2026-10-17: "add a way to download from the porn finder. it only offers play. These
/// downloads would move to a new section under videos called 18+").
final class AdultDownloadsTests: XCTestCase {
    private let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    private func stream(_ json: String) throws -> MediaStream {
        try decoder.decode(MediaStream.self, from: Data(json.utf8))
    }

    private func item(_ type: String, _ name: String) throws -> MediaItem {
        let said = #"{"id":"x:1","type":"\#(type)","name":"\#(name)","poster":null,"poster_shape":"poster","year":"2001","rating":null,"genres":[]}"#
        return try decoder.decode(MediaItem.self, from: Data(said.utf8))
    }

    private let address = #"{"kind":"url","name":"A Plugin","title":null,"quality":"1080p","size":901234567,"url":"https://films.invalid/a.mp4","video_id":null,"info_hash":null,"file_index":null,"trackers":[]}"#
    private let torrent = #"{"kind":"torrent","name":"A Plugin","title":null,"quality":"720p","size":null,"url":null,"video_id":null,"info_hash":"ABAB","file_index":3,"trackers":["udp://t.invalid:1"]}"#
    private let video = #"{"kind":"youtube","name":"Trailer","title":null,"quality":null,"size":null,"url":null,"video_id":"abcdefghijk","info_hash":null,"file_index":null,"trackers":[]}"#

    func testAWebAddressCanBeDownloadedAsATorrentCan() throws {
        let plain = try stream(address)
        XCTAssertTrue(plain.canDownload)
        XCTAssertEqual(plain.keepId, "https://films.invalid/a.mp4")
        let shared = try stream(torrent)
        XCTAssertTrue(shared.canDownload)
        XCTAssertEqual(shared.keepId, "abab")  // a torrent's id, in the case keeps use
        XCTAssertFalse(try stream(video).canDownload)  // a video by its id: only played
        XCTAssertNil(PendingKeeps.Keep(try stream(video), of: try item("movie", "A Film")))
    }

    func testAnAddressesKeepAsksTheEngineToDownloadIt() throws {
        let film = try item("movie", "A Film")
        let keep = try XCTUnwrap(PendingKeeps.Keep(try stream(address), convert: false, of: film, year: "2001"))
        XCTAssertTrue(keep.isAddress)
        XCTAssertEqual(keep.id, "https://films.invalid/a.mp4")
        XCTAssertEqual(keep.infoHash, "")
        XCTAssertEqual(keep.keepMethod, "stream.keep")
        XCTAssertEqual(keep.statusMethod, "stream.status")
        XCTAssertEqual(keep.stopMethod, "stream.stop")
        XCTAssertEqual(keep.which as? [String: String], ["url": "https://films.invalid/a.mp4"])
        let asked = keep.asked
        XCTAssertEqual(asked["url"] as? String, "https://films.invalid/a.mp4")
        XCTAssertEqual(asked["size"] as? Int, 901_234_567)  // the plugin's own figure
        XCTAssertEqual(asked["title"] as? String, "A Film")
        XCTAssertEqual(asked["year"] as? String, "2001")
        XCTAssertEqual(asked["kind"] as? String, "movie")
        XCTAssertEqual(asked["convert"] as? Bool, false)
        XCTAssertNil(asked["info_hash"])
        XCTAssertNil(asked["trackers"])
        XCTAssertNil(asked["file_index"])

        // A torrent's is as it was.
        let shared = try XCTUnwrap(PendingKeeps.Keep(try stream(torrent), of: film))
        XCTAssertFalse(shared.isAddress)
        XCTAssertEqual(shared.id, "abab")
        XCTAssertEqual([shared.keepMethod, shared.statusMethod, shared.stopMethod],
                       ["torrent.keep", "torrent.status", "torrent.stop_keeping"])
        XCTAssertEqual(shared.asked["info_hash"] as? String, "abab")
        XCTAssertEqual(shared.asked["file_index"] as? Int, 3)
        XCTAssertNil(shared.asked["url"])
    }

    func testAnAddressesKeepIsRememberedAndCarriedOnAndOldOnesStillRead() throws {
        let name = "adultkeeps-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: name))
        defer { defaults.removePersistentDomain(forName: name) }
        // A keep saved before addresses could be kept is read as the torrent's it was.
        let old = #"{"keeps":[{"infoHash":"cd","trackers":[],"title":"Old","convert":true,"apart":true,"kind":"movie"}]}"#
        defaults.set(Data(old.utf8), forKey: PendingKeeps.key)
        var pending = PendingKeeps.load(from: defaults)
        let before = try XCTUnwrap(pending.keeps.first)
        XCTAssertFalse(before.isAddress)
        XCTAssertNil(before.url)
        XCTAssertEqual(before.id, "cd")
        // It was from the Finder for adults: asked for again, it goes in 18+, not loose.
        XCTAssertEqual(before.asked["kind"] as? String, "adult")
        XCTAssertEqual(before.kindName, "18+")

        let film = try item("movie", "A Film")
        let keep = try XCTUnwrap(PendingKeeps.Keep(try stream(address), apart: true, of: film, year: "2001"))
        pending.add(keep)
        pending.add(keep)  // asked again: once
        pending.save(to: defaults)
        let found = PendingKeeps.load(from: defaults)
        XCTAssertEqual(found.keeps.map(\.id), ["cd", "https://films.invalid/a.mp4"])
        XCTAssertEqual(found.keeps[1], keep)
        XCTAssertEqual(found.keeps[1].asked["kind"] as? String, "adult")
        XCTAssertEqual(found.keeps[1].asked["url"] as? String, "https://films.invalid/a.mp4")
        var after = found
        after.remove("https://films.invalid/a.mp4")
        XCTAssertEqual(after.keeps.map(\.id), ["cd"])
        after.remove("CD")  // a torrent's in any case
        XCTAssertEqual(after.keeps, [])
    }

    func testFromTheFinderForAdultsEverythingIsKeptAsAdult() throws {
        let anime = try item("series", "A Show").asAnime
        let part = try decoder.decode(
            MediaDetails.Video.self,
            from: Data(#"{"id":"x:1:2","title":"Pilot","thumbnail":null,"released":null,"season":1,"episode":2,"overview":null,"video_id":null}"#.utf8))
        let keep = try XCTUnwrap(PendingKeeps.Keep(try stream(torrent), apart: true, of: anime, part: part))
        XCTAssertEqual(keep.asked["kind"] as? String, "adult")
        XCTAssertEqual(keep.asked["show"] as? String, "A Show")
        XCTAssertEqual(keep.kindName, "18+")
        XCTAssertTrue(keep.isApart)
        // Anywhere else, what it is.
        let plain = try XCTUnwrap(PendingKeeps.Keep(try stream(torrent), of: anime, part: part))
        XCTAssertEqual(plain.asked["kind"] as? String, "anime")
        XCTAssertFalse(plain.isApart)
    }

    func testADownloadsStatusIsShownAsATorrentsKeepIs() throws {
        let going = try decoder.decode(
            FetchStatus.self,
            from: Data(#"{"id":"s-1","state":"fetching","bytes":340,"size":1000,"bytes_per_second":5200000,"progress":0.34,"keeping":true,"kept_path":null,"keep_error":null,"keep_note":null,"converting":null}"#.utf8))
        XCTAssertTrue(going.asKeep.isKeeping)
        XCTAssertEqual(going.asKeep.keepProgress, 0.34)
        XCTAssertEqual(going.asKeep.keepLine?.hasPrefix("Downloading: 34% here · 5.2 MB/s"), true)
        let kept = try decoder.decode(
            FetchStatus.self,
            from: Data(#"{"id":"s-1","state":"complete","bytes":1000,"size":null,"bytes_per_second":0,"progress":1,"keeping":false,"kept_path":"/m/18+/A Film (2001).mp4","keep_error":null,"keep_note":null,"converting":null}"#.utf8))
        XCTAssertFalse(kept.asKeep.isKeeping)
        XCTAssertEqual(
            kept.asKeep.keepLine, "Downloaded to the 18+ folder in your Movies folder as “A Film (2001).mp4”")
        let loose = TorrentStatus(
            state: "complete", peers: 0, bytesPerSecond: 0, progress: 1, keeping: false,
            keptPath: "/m/A Film (2001).mkv")
        XCTAssertEqual(loose.keepLine, "Downloaded to your Movies folder as “A Film (2001).mkv”")
        let failed = try decoder.decode(
            FetchStatus.self,
            from: Data(#"{"id":"s-1","state":"fetching","bytes":0,"size":null,"bytes_per_second":0,"progress":0,"keeping":false,"kept_path":null,"keep_error":"What that address gave isn't a film: nothing was kept.","keep_note":null,"converting":null}"#.utf8))
        XCTAssertFalse(failed.asKeep.isKeeping)
        XCTAssertEqual(failed.asKeep.keepLine?.hasPrefix("Couldn't download it:"), true)
    }

    func testThe18PageIsInTheSidebarButNeverInAChildsProfile() {
        let videos = SidebarRows.all.filter { $0.group == "Videos" }.map(\.key)
        XCTAssertEqual(videos.last, "adultVideos")  // the last row under Videos
        for row in ["adultFinder", "adultVideos"] {
            XCTAssertTrue(SidebarRows.offered(row, isChild: false), row)
            XCTAssertFalse(SidebarRows.offered(row, isChild: true), row)
        }
        for row in SidebarRows.all.map(\.key) where !SidebarRows.adultRows.contains(row) {
            XCTAssertTrue(SidebarRows.offered(row, isChild: true), row)
        }
        // It can be taken out and put back like the others.
        let saved = SidebarRows.write(nil, "adultVideos", shown: false)
        XCTAssertEqual(SidebarRows.hidden(saved), ["adultVideos"])
        XCTAssertEqual(SidebarRows.write(saved, "adultVideos", shown: true), "")
    }

    func testVideosMoviesLeavesTheFolderFor18Out() throws {
        let movies = FileManager.default.temporaryDirectory
            .appendingPathComponent("adultfolder-\(UUID().uuidString)")
        let theirs = VideoFiles.adultFolder(in: movies)
        let videos = movies.appendingPathComponent("Videos", isDirectory: true)
        for folder in [theirs, videos] {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        }
        defer { try? FileManager.default.removeItem(at: movies) }
        XCTAssertEqual(theirs.lastPathComponent, "18+")
        for name in ["A Film.mp4", "18+/For Adults.mkv", "Videos/A Talk.mp4"] {
            try Data("x".utf8).write(to: movies.appendingPathComponent(name))
        }
        XCTAssertEqual(VideoFiles.inside(movies, leavingOut: [videos, theirs]).map(\.name), ["A Film"])
        XCTAssertEqual(VideoFiles.inside(theirs).map(\.name), ["For Adults"])
        XCTAssertEqual(VideoFiles.inside(movies, leavingOut: [URL]()).count, 3)
        XCTAssertEqual(VideoFiles.inside(movies, leavingOut: [movies]).count, 3)  // never itself
    }
}
