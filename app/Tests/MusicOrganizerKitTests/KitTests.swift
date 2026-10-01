import XCTest

@testable import MusicOrganizerKit

final class LRCTests: XCTestCase {
    func testParsesTimesTagsAndRepeats() {
        let lines = LRC.parse(
            """
            [ar:Band]
            [00:12.50]First line
            [00:01.00][01:05]Chorus
            [00:20.00]
            not a timed line
            [1:30.25] Last
            """)
        XCTAssertEqual(lines.map(\.time), [1, 12.5, 20, 65, 90.25])
        XCTAssertEqual(lines.map(\.text), ["Chorus", "First line", "", "Chorus", "Last"])
        XCTAssertEqual(lines.map(\.id), [0, 1, 2, 3, 4])
    }

    func testWordsAndBackToTimedText() {
        let words = LRC.words(of: "[ar:Band]\n[00:12.50]First line\n\n  Second line  \n[00:01.00][01:05]Chorus")
        XCTAssertEqual(words, ["First line", "Second line", "Chorus"])
        let text = LRC.text(of: [(1.0, "First line"), (65.257, "Second line"), (600, "Chorus")])
        XCTAssertEqual(text, "[00:01.00]First line\n[01:05.26]Second line\n[10:00.00]Chorus")
        XCTAssertEqual(LRC.parse(text).map(\.text), ["First line", "Second line", "Chorus"])
    }

    func testCurrentLine() {
        let lines = LRC.parse("[00:10.00]a\n[00:20.00]b\n[00:30.00]c")
        XCTAssertNil(LRC.current(at: 5, in: lines))
        XCTAssertEqual(LRC.current(at: 10, in: lines), 0)
        XCTAssertEqual(LRC.current(at: 29.9, in: lines), 1)
        XCTAssertEqual(LRC.current(at: 500, in: lines), 2)
        XCTAssertNil(LRC.current(at: 5, in: []))
    }
}

final class LibraryTests: XCTestCase {
    let tracks = [
        Track(path: "Music/The Band/First (2001)/02 Second Song.mp3", title: "Second Song",
              artist: "The Band", album: "First", year: 2001, track: 2),
        Track(path: "Music/The Band/First (2001)/01 Opening.mp3", title: "Opening",
              artist: "The Band feat. Guest", albumArtist: "The Band", album: "First",
              year: 2001, track: 1, cover: "Music/The Band/First (2001)/cover.jpg"),
        Track(path: "Music/Ábel/Singles/Élan.m4a", title: "Élan", artist: "Ábel"),
        Track(path: "Music/the band/Later (2010)/01 Zed.mp3", title: "Zed", artist: "the band",
              album: "Later", year: 2010, track: 1),
    ]

    func testAlbumsAreFoldersWithTracksInOrder() {
        let library = Library(tracks: tracks)
        XCTAssertEqual(library.albums.map(\.title), ["Singles", "First", "Later"])
        let first = library.albums[1]
        XCTAssertEqual(first.tracks.map(\.title), ["Opening", "Second Song"])
        XCTAssertEqual(first.artist, "The Band")
        XCTAssertEqual(first.year, 2001)
        XCTAssertEqual(first.coverTrack?.title, "Opening")
    }

    func testArtistsIgnoreCaseAndSortWithoutThe() {
        let library = Library(tracks: tracks)
        XCTAssertEqual(library.artists.map(\.name), ["Ábel", "The Band"])
        XCTAssertEqual(library.artists[1].albums.map(\.title), ["First", "Later"])
        XCTAssertEqual(library.artists[1].trackCount, 3)
    }

    func testSearchIgnoresCaseAndAccents() {
        let library = Library(tracks: tracks)
        XCTAssertEqual(library.search("elan").map(\.title), ["Élan"])
        XCTAssertEqual(library.search("BAND first").map(\.title), ["Opening", "Second Song"])
        XCTAssertEqual(library.search("  ").count, 4)
        XCTAssertEqual(library.search("nothing like this"), [])
        XCTAssertEqual(library.albums(matching: "zed").map(\.title), ["Later"])
        XCTAssertEqual(library.artists(matching: "abel").map(\.name), ["Ábel"])
        XCTAssertEqual(library.tracks.map(\.title), ["Élan", "Opening", "Second Song", "Zed"])
    }

    func testDecodesTheEnginesTrack() throws {
        let json = """
            {"root": "/library", "tracks": [{"track_id": "t_1", "path": "Music/A/B (2020)/01 S.m4a",
            "title": "S", "artist": "A", "album_artist": null, "album": "B", "year": 2020,
            "track": 1, "disc": null, "genre": null, "duration_s": 228.1, "explicit": true,
            "only_copy": false, "match": "auto_details", "format": "mp3", "bitrate_kbps": 320,
            "cover": null, "embedded_cover": true, "lyrics": "synced"}]}
            """
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let list = try decoder.decode(TrackList.self, from: Data(json.utf8))
        let track = list.tracks[0]
        XCTAssertEqual(track.durationS, 228.1)
        XCTAssertEqual(track.bitrateKbps, 320)
        XCTAssertEqual(track.lyrics, .synced)
        XCTAssertTrue(track.explicit && track.embeddedCover && track.hasCover)
        XCTAssertEqual(track.folder, "Music/A/B (2020)")
    }

    func testCollections() throws {
        let songs = [
            Track(path: "Music/A/B/1.mp3", title: "One", trackId: "t_1", acquired: "2026-09-01T00:00:00Z"),
            Track(path: "Music/A/B/2.mp3", title: "Two", match: "unconfirmed", trackId: "t_2",
                  acquired: "2026-10-01T00:00:00Z"),
            Track(path: "Music/A/B/3.mp3", title: "Three", trackId: "t_3"),
        ]
        let library = Library(tracks: songs)
        XCTAssertEqual(library.tracks(withIDs: ["t_3", "gone", "t_1", "t_3"]).map(\.title),
                       ["Three", "One", "Three"])
        XCTAssertEqual(library.recentlyAdded().map(\.title), ["Two", "One"])
        XCTAssertEqual(library.unconfirmed.map(\.title), ["Two"])
        let plays = ["t_1": PlayCount(count: 2), "t_3": PlayCount(count: 5), "t_2": PlayCount(count: 0)]
        XCTAssertEqual(library.mostPlayed(plays).map(\.title), ["Three", "One"])
        XCTAssertEqual(library.filter(library.recentlyAdded(), "one").map(\.title), ["One"])

        let json = """
            {"favourites": ["t_2"], "plays": {"t_1": {"count": 3, "last_played": "2026-10-01T03:00:00Z"}},
             "playlists": [{"id": "pl_1", "name": "Mix", "created_at": null, "track_ids": ["t_1", "t_2"]}]}
            """
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let listening = try decoder.decode(Listening.self, from: Data(json.utf8))
        XCTAssertEqual(listening.plays["t_1"], PlayCount(count: 3, lastPlayed: "2026-10-01T03:00:00Z"))
        XCTAssertEqual(listening.playlists, [Playlist(id: "pl_1", name: "Mix", trackIds: ["t_1", "t_2"])])
        XCTAssertEqual(listening.playlists[0].trackIds, ["t_1", "t_2"])
    }

    func testASearchResultBecomesAPlayableSong() throws {
        let json = """
            {"results": [{"candidate_id": "c_1", "video_id": "abcdefghijk", "title": "Song",
              "artists": ["A", "B"], "album": "Album", "album_browse_id": "MPRE", "duration_s": 187,
              "is_explicit": null, "is_official_audio": true, "thumbnail": "https://example.invalid/t.jpg",
              "score": null, "reasons": [], "version_tokens": []}]}
            """
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let result = try decoder.decode(SearchAnswer.self, from: Data(json.utf8)).results[0]
        let track = result.track
        XCTAssertEqual(track.videoId, "abcdefghijk")
        XCTAssertEqual(track.artistName, "A, B")
        XCTAssertEqual(track.durationS, 187)
        XCTAssertTrue(track.hasCover)
        XCTAssertNil(track.trackId)
        XCTAssertNil(Track(path: "Music/A/B/1.mp3", title: "One").videoId)
        let library = Library(tracks: [Track(path: "Music/A/B/1.mp3", title: "One", sourceId: "abcdefghijk")])
        XCTAssertTrue(library.videoIDs.contains(result.videoId))
    }

    func testSidebarChoices() {
        XCTAssertEqual(SidebarChoice.read(nil), SidebarChoice.all)
        XCTAssertEqual(SidebarChoice.read(""), SidebarChoice.all)
        XCTAssertEqual(SidebarChoice.read("songs,nonsense,artists,songs"), ["songs", "artists"])
        XCTAssertEqual(SidebarChoice.hidden(["songs", "artists"]),
                       ["albums", "videos", "favourites", "mostPlayed", "recentlyAdded", "unconfirmed"])
        XCTAssertEqual(SidebarChoice.adding("albums", to: ["songs", "artists"]),
                       ["songs", "albums", "artists"])
        XCTAssertEqual(SidebarChoice.write(["songs", "artists"]), "songs,artists")
    }

    func testANewSidebarEntryIsShownOnceWithoutAsking() {
        // A choice saved before Videos existed gains it, in its standard place.
        let first = SidebarChoice.catchUp(saved: "songs,artists,favourites", seen: nil)
        XCTAssertEqual(first.entries, "songs,artists,videos,favourites")
        XCTAssertEqual(first.seen, SidebarChoice.write(SidebarChoice.all))
        // Removed by the owner afterwards, it stays removed.
        let later = SidebarChoice.catchUp(saved: "songs,artists,favourites", seen: first.seen)
        XCTAssertEqual(later.entries, "songs,artists,favourites")
        // Nothing saved at all: every entry.
        XCTAssertEqual(SidebarChoice.catchUp(saved: nil, seen: nil).entries,
                       SidebarChoice.write(SidebarChoice.all))
    }

    func testUnfinishedDownloads() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let json = """
            {"downloads": [
              {"job_id": 7, "batch_id": "b_1", "state": "running", "reason": null, "message": null,
               "video_id": "abcdefghijk", "title": "Song", "artists": ["Band", "Guest"],
               "video": true, "height": 720, "fps": 30, "thumbnail": null},
              {"job_id": 6, "batch_id": "b_0", "state": "needs_review", "reason": "duration_mismatch",
               "message": null, "video_id": "zyxwvutsrqp", "title": null, "artists": [],
               "video": false, "height": null, "fps": null, "thumbnail": null}]}
            """
        let found = try decoder.decode(DownloadsAnswer.self, from: Data(json.utf8)).downloads
        XCTAssertEqual(found.map(\.id), [7, 6])
        XCTAssertTrue(found[0].isActive && found[0].isRunning && found[0].video)
        XCTAssertEqual(found[0].artistName, "Band, Guest")
        XCTAssertEqual(found[0].progressNote, "Downloading…")
        XCTAssertNil(found[0].problem)
        XCTAssertFalse(found[1].isActive)
        XCTAssertEqual(found[1].name, "zyxwvutsrqp")
        XCTAssertEqual(found[1].problem, "duration mismatch")
        XCTAssertEqual(PendingDownload(jobId: 1, state: "queued").progressNote, "Waiting its turn…")
        XCTAssertEqual(PendingDownload(jobId: 1, state: "failed", message: "No.").problem, "No.")
    }

    func testListeningReadsMovedDownloadsAndOlderAnswers() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let now = try decoder.decode(
            Listening.self,
            from: Data(#"{"favourites": [], "plays": {}, "playlists": [], "library": ["t_1"]}"#.utf8))
        XCTAssertEqual(now.library, ["t_1"])
        let older = try decoder.decode(
            Listening.self, from: Data(#"{"favourites": [], "plays": {}, "playlists": []}"#.utf8))
        XCTAssertNil(older.library)
    }

    func testASavedVideoIsMarked() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let json = """
            {"track_id": "t", "path": "Music/Videos/Band/Song.mp4", "title": "Song",
             "explicit": false, "only_copy": false, "embedded_cover": true, "lyrics": "none",
             "source": "youtube_music", "video": true, "height": 720}
            """
        let video = try decoder.decode(Track.self, from: Data(json.utf8))
        XCTAssertTrue(video.isVideo)
        XCTAssertEqual(video.height, 720)
        XCTAssertTrue(video.isDownload)
        // An engine answer from before videos existed still reads: it's a song.
        let old = json.replacingOccurrences(of: #", "video": true, "height": 720"#, with: "")
        XCTAssertFalse(try decoder.decode(Track.self, from: Data(old.utf8)).isVideo)
    }

    func testDownloadsAndLyricsTally() {
        XCTAssertTrue(Track(path: "a", title: "A", source: "youtube_music").isDownload)
        XCTAssertFalse(Track(path: "a", title: "A", match: "auto_exact", source: "youtube_music").isDownload)
        XCTAssertFalse(Track(path: "a", title: "A", source: "rip_copy").isDownload)
        let jobs = [
            JobsAnswer.Job(state: "done", message: "synced from LRCLIB"),
            JobsAnswer.Job(state: "done", message: "plain from YouTube Music"),
            JobsAnswer.Job(state: "done", message: "not_found"),
            JobsAnswer.Job(state: "failed", message: "synced"),
        ]
        let tally = lyricsTally(jobs)
        XCTAssertEqual([tally.timed, tally.plain, tally.none], [1, 1, 2])
    }

    func testClockTime() {
        XCTAssertEqual(clockTime(187.9), "3:07")
        XCTAssertEqual(clockTime(3765), "1:02:45")
        XCTAssertEqual(clockTime(nil), "–:––")
    }
}

/// A generator that always gives the same numbers, so a shuffle can be checked.
struct Fixed: RandomNumberGenerator {
    var state: UInt64 = 42
    mutating func next() -> UInt64 {
        state = state &* 6_364_136_223_846_793_005 &+ 1_442_695_040_888_963_407
        return state
    }
}

final class PlayQueueTests: XCTestCase {
    let songs = (1...5).map { Track(path: "Music/A/B/\($0).mp3", title: "Song \($0)") }

    func testPlaysInOrderAndStopsAtTheEnd() {
        var queue = PlayQueue()
        XCTAssertNil(queue.current)
        queue.play(songs, startAt: 3)
        XCTAssertEqual(queue.current?.title, "Song 4")
        XCTAssertEqual(queue.upNext.map(\.title), ["Song 5"])
        XCTAssertEqual(queue.advance()?.title, "Song 5")
        XCTAssertNil(queue.advance())
        XCTAssertEqual(queue.current?.title, "Song 5")
        XCTAssertEqual(queue.back()?.title, "Song 4")
    }

    func testRepeat() {
        var queue = PlayQueue()
        queue.play(songs, startAt: 4)
        queue.repeatMode = .all
        XCTAssertEqual(queue.advance(finished: true)?.title, "Song 1")
        XCTAssertEqual(queue.back()?.title, "Song 5")
        queue.repeatMode = .one
        XCTAssertEqual(queue.advance(finished: true)?.title, "Song 5")
        XCTAssertEqual(queue.advance()?.title, "Song 1")  // pressing Next still moves on
    }

    func testShuffleKeepsTheSongThatIsPlayingAndPlaysEverySongOnce() {
        var queue = PlayQueue()
        var generator = Fixed()
        queue.play(songs, startAt: 2)
        queue.setShuffle(true, using: &generator)
        XCTAssertEqual(queue.current?.title, "Song 3")
        var heard = [queue.current!.title]
        while let next = queue.advance() { heard.append(next.title) }
        XCTAssertEqual(heard.sorted(), songs.map(\.title))
        XCTAssertNotEqual(heard, songs.map(\.title))
        queue.setShuffle(false, using: &generator)
        XCTAssertEqual(queue.current?.title, heard.last)
        XCTAssertEqual(queue.order, [0, 1, 2, 3, 4])
    }

    func testJumpAndEmpty() {
        var queue = PlayQueue()
        queue.play(songs)
        XCTAssertEqual(queue.jump(toUpNext: 2)?.title, "Song 4")
        XCTAssertNil(queue.jump(toUpNext: 9))
        queue.play([])
        XCTAssertNil(queue.current)
        XCTAssertNil(queue.advance())
    }
}

final class RPCConnectionTests: XCTestCase {
    /// A pretend engine on the other end of two pipes.
    func makePair() -> (RPCConnection, toEngine: FileHandle, fromEngine: FileHandle) {
        let requests = Pipe(), answers = Pipe()
        let connection = RPCConnection(
            writeTo: requests.fileHandleForWriting, readFrom: answers.fileHandleForReading)
        return (connection, requests.fileHandleForReading, answers.fileHandleForWriting)
    }

    func readRequest(_ handle: FileHandle) throws -> [String: Any] {
        var line = Data()
        while !line.contains(0x0A) { line.append(handle.availableData) }
        return try XCTUnwrap(JSONSerialization.jsonObject(with: line) as? [String: Any])
    }

    func testCallAnswerErrorAndNotification() async throws {
        let (connection, toEngine, fromEngine) = makePair()
        let noted = expectation(description: "notification")
        connection.start(onNotification: { method, params in
            if method == "library.changed", params["tracks_added"] as? Int == 2 { noted.fulfill() }
        })
        async let answer = connection.call(
            "library.status", as: LibraryStatus.self)
        let request = try readRequest(toEngine)
        XCTAssertEqual(request["method"] as? String, "library.status")
        let id = try XCTUnwrap(request["id"] as? Int)
        fromEngine.write(Data(
            """
            {"jsonrpc":"2.0","method":"library.changed","params":{"tracks_added":2}}
            {"jsonrpc":"2.0","id":\(id),"result":{"items_by_state":{"review":7},"tracks":3}}

            """.utf8))
        let status = try await answer
        XCTAssertEqual(status.tracks, 3)
        XCTAssertEqual(status.waitingForReview, 7)
        await fulfillment(of: [noted], timeout: 5)

        async let failing = connection.call("library.lyrics", ["path": "x"])
        let second = try readRequest(toEngine)
        XCTAssertEqual((second["params"] as? [String: Any])?["path"] as? String, "x")
        fromEngine.write(Data(
            """
            {"jsonrpc":"2.0","id":\(second["id"] as! Int),"error":{"code":-32006,"message":"Gone."}}

            """.utf8))
        do {
            _ = try await failing
            XCTFail("expected an error")
        } catch let error as RPCError {
            XCTAssertEqual(error, RPCError(code: RPCError.notFound, message: "Gone."))
        }
    }

    func testAClosedEngineFailsWaitingAndLaterCalls() async throws {
        let (connection, toEngine, fromEngine) = makePair()
        let closed = expectation(description: "closed")
        connection.start(onClose: { closed.fulfill() })
        async let waiting = connection.call("library.tracks")
        _ = try readRequest(toEngine)
        try fromEngine.close()
        do {
            _ = try await waiting
            XCTFail("expected an error")
        } catch let error as RPCError {
            XCTAssertEqual(error.code, RPCError.closed)
        }
        await fulfillment(of: [closed], timeout: 5)
        do {
            _ = try await connection.call("library.tracks")
            XCTFail("expected an error")
        } catch let error as RPCError {
            XCTAssertEqual(error.code, RPCError.closed)
        }
    }
}

final class EngineLocateTests: XCTestCase {
    func testOrder() {
        let app = URL(fileURLWithPath: "/work/project/app/build/Music Organizer.app")
        let inProject = "/work/project/.venv/bin/musicorg"
        XCTAssertEqual(
            EngineProcess.locate(
                environment: ["MUSICORG_ENGINE": "/given"], appLocation: app, recorded: "/recorded",
                exists: { _ in true })?.path, "/given")
        XCTAssertEqual(
            EngineProcess.locate(
                environment: [:], appLocation: app, recorded: "/recorded",
                exists: { $0 == inProject || $0 == "/recorded" })?.path, inProject)
        XCTAssertEqual(
            EngineProcess.locate(
                environment: [:], appLocation: URL(fileURLWithPath: "/Applications/M.app"),
                recorded: "/recorded", exists: { $0 == "/recorded" })?.path, "/recorded")
        XCTAssertNil(
            EngineProcess.locate(
                environment: [:], appLocation: app, recorded: nil, exists: { _ in false }))
    }
}

final class SongVideoTests: XCTestCase {
    private func answer(_ sizes: [(String, Int)] = [("1080p60", 1080), ("1080p", 1080), ("720p", 720), ("360p", 360)])
        -> VideoAnswer
    {
        VideoAnswer(
            found: true, videoId: "abcdefghijk", title: "Song", durationS: 245,
            httpHeaders: ["User-Agent": "x"], audioUrl: "https://example.invalid/a",
            qualities: sizes.map {
                .init(label: $0.0, height: $0.1, fps: 30, url: "https://example.invalid/\($0.0)")
            })
    }

    func testReadsTheEnginesAnswer() throws {
        let data = Data(
            """
            {"found": true, "video_id": "abcdefghijk", "title": "Song", "duration_s": 245.0,
             "http_headers": {"User-Agent": "x"}, "audio_url": "https://example.invalid/a",
             "qualities": [{"label": "720p", "height": 720, "fps": 24, "url": "https://example.invalid/v"}]}
            """.utf8)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let video = try XCTUnwrap(SongVideo(try decoder.decode(VideoAnswer.self, from: data)))
        XCTAssertEqual(video.videoId, "abcdefghijk")
        XCTAssertEqual(video.length, 245)
        XCTAssertEqual(video.headers, ["User-Agent": "x"])
        XCTAssertEqual(video.qualities.map(\.label), ["720p"])

        let none = try decoder.decode(VideoAnswer.self, from: Data(#"{"found": false}"#.utf8))
        XCTAssertNil(SongVideo(none))
    }

    func testAnAnswerThatCantBePlayedIsNoVideo() {
        XCTAssertNil(SongVideo(answer([])))  // no picture
        XCTAssertNil(SongVideo(VideoAnswer(found: true, videoId: "abcdefghijk", durationS: 245)))
        let noLength = VideoAnswer(
            found: true, videoId: "abcdefghijk", audioUrl: "https://example.invalid/a",
            qualities: [.init(label: "720p", height: 720, fps: 24, url: "https://example.invalid/v")])
        XCTAssertNil(SongVideo(noLength))
    }

    func testThePictureThatsChosen() throws {
        let video = try XCTUnwrap(SongVideo(answer()))
        XCTAssertEqual(video.quality(for: nil).label, "1080p60")  // the sharpest
        XCTAssertEqual(video.quality(for: .init(height: 1080, label: "1080p")).label, "1080p")
        XCTAssertEqual(video.quality(for: .init(height: 720, label: "720p")).label, "720p")
        // Not on offer for this video: the sharpest that's no bigger, else the smallest.
        XCTAssertEqual(video.quality(for: .init(height: 480, label: "480p")).label, "360p")
        XCTAssertEqual(video.quality(for: .init(height: 720, label: "720p60")).label, "720p")
        XCTAssertEqual(video.quality(for: .init(height: 144, label: "144p")).label, "360p")
    }

    func testOnlyAVideoAsLongAsTheSongKeepsItsTime() throws {
        let video = try XCTUnwrap(SongVideo(answer()))  // 245 s
        XCTAssertTrue(video.keepsTime(with: 245))
        XCTAssertTrue(video.keepsTime(with: 243.4))
        XCTAssertFalse(video.keepsTime(with: 236))  // the video has an intro
        XCTAssertFalse(video.keepsTime(with: nil))
    }
}
