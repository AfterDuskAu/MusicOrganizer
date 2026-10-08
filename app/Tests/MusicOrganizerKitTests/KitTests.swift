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
        // What kind of download the list says it is; one kept outside the library says so.
        XCTAssertEqual(PendingDownload(jobId: 1, state: "queued").kind, "Song")
        XCTAssertEqual(
            PendingDownload(jobId: 1, state: "queued", video: true, height: 720).kind, "Video, 720p")
        XCTAssertEqual(
            PendingDownload(jobId: 1, state: "queued", video: true, height: 1080, kept: true).kind,
            "Video, up to 1080p · to Videos")
        XCTAssertEqual(PendingDownload.keptHeight(upTo: 2160), 1080)
        XCTAssertEqual(PendingDownload.keptHeight(upTo: 700), 480)
        XCTAssertEqual(PendingDownload.keptHeight(upTo: 100), 144)
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
        XCTAssertNil(video.likes)  // an answer from before likes were sent

        let none = try decoder.decode(VideoAnswer.self, from: Data(#"{"found": false}"#.utf8))
        XCTAssertNil(SongVideo(none))
    }

    func testThumbsUpCountsComeWithWhatPlaysFromYouTube() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let stream = try decoder.decode(
            StreamAnswer.self,
            from: Data(
                #"{"url": "https://example.invalid/a", "http_headers": {}, "duration_s": 199, "likes": 899796}"#
                    .utf8))
        XCTAssertEqual(stream.likes, 899_796)
        let hidden = try decoder.decode(
            StreamAnswer.self,
            from: Data(
                #"{"url": "https://example.invalid/a", "http_headers": {}, "duration_s": 199, "likes": null}"#
                    .utf8))
        XCTAssertNil(hidden.likes)
        let video = VideoAnswer(
            found: true, videoId: "abcdefghijk", durationS: 245,
            audioUrl: "https://example.invalid/a",
            qualities: [.init(label: "720p", height: 720, fps: 24, url: "https://example.invalid/v")],
            likes: 5200)
        XCTAssertEqual(SongVideo(video)?.likes, 5200)
        // Written as YouTube writes them.
        let written = [
            0: "0", 950: "950", 1000: "1K", 5200: "5.2K", 9949: "9.9K", 9950: "10K",
            899_796: "900K", 999_499: "999K", 999_500: "1M", 1_250_000: "1.3M",
            12_000_000: "12M", 2_100_000_000: "2.1B", -5: "0",
        ]
        for (count, words) in written {
            XCTAssertEqual(CompactCount.text(count), words, "\(count)")
        }
    }

    func testAnAnswerThatCantBePlayedIsNoVideo() {
        XCTAssertNil(SongVideo(answer([])))  // no picture
        // A long video says its pictures are playlists with the sound in them.
        XCTAssertEqual(SongVideo(answer())?.segmented, false)
        let long = VideoAnswer(
            found: true, videoId: "abcdefghijk", durationS: 3479,
            audioUrl: "https://example.invalid/sound.m3u8",
            qualities: [.init(label: "720p", height: 720, fps: 30, url: "http://127.0.0.1:1/k/n.m3u8")],
            segmented: true)
        XCTAssertEqual(SongVideo(long)?.segmented, true)
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

    // MARK: Discover

    func testAPickIsPlayableAndGoesBackAsTheEngineGaveIt() throws {
        let json = """
            {"picks": [{"video_id": "abcdefghijk", "title": "Song", "artists": ["A", "B"],
              "album": "Album", "album_browse_id": "MPRE", "duration_s": 187, "is_explicit": null,
              "is_official_audio": true, "video_type": "MUSIC_VIDEO_TYPE_ATV", "year": "2003",
              "thumbnail": "https://example.invalid/t.jpg",
              "why": "On the radio for 3 of your songs", "hits": 3, "genre": "Hip Hop"}],
             "wanted": 10, "radios": 4, "seeds": [], "note": null}
            """
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let answer = try decoder.decode(DiscoverAnswer.self, from: Data(json.utf8))
        let pick = answer.picks[0]
        XCTAssertEqual(pick.why, "On the radio for 3 of your songs")
        XCTAssertEqual(pick.artistName, "A, B")
        XCTAssertEqual(pick.result.track.videoId, "abcdefghijk")
        XCTAssertEqual(pick.result.track.durationS, 187)
        XCTAssertNil(answer.note)
        let back = pick.candidate
        XCTAssertEqual(
            Set(back.keys),
            ["video_id", "title", "artists", "album", "album_browse_id", "duration_s",
             "video_type", "year", "thumbnail", "genre"])
        XCTAssertEqual(back["video_id"] as? String, "abcdefghijk")
        XCTAssertEqual(back["duration_s"] as? Int, 187)
        XCTAssertEqual(back["artists"] as? [String], ["A", "B"])
        XCTAssertTrue(JSONSerialization.isValidJSONObject(back))
    }

    func testDiscoverSeeds() {
        XCTAssertEqual(DiscoverSeed.library.params, ["kind": "library"])
        XCTAssertEqual(DiscoverSeed.mostPlayed.params, ["kind": "most_played"])
        XCTAssertEqual(DiscoverSeed.topArtist.params, ["kind": "top_artist"])
        XCTAssertEqual(
            DiscoverSeed.playlist("pl_1").params, ["kind": "playlist", "playlist_id": "pl_1"])
        XCTAssertEqual(DiscoverSeed.genre("hip hop").params, ["kind": "genre", "name": "hip hop"])
        XCTAssertEqual(DiscoverSeed.lastfm.params, ["kind": "lastfm"])
        XCTAssertEqual(FindChoice(start: .lastfm).seeds(playlists: []), [.lastfm])
        XCTAssertEqual(
            DiscoverSeed.artists(" Linkin Park, Korn ;linkin park,, "),
            [.artist("Linkin Park"), .artist("Korn")])
        XCTAssertEqual(DiscoverSeed.artists("  "), [])
    }

    func testFindsStartingPointsTogether() {
        let playlists: Set<String> = ["pl_1"]
        let trip = FindChoice(start: .playlist, playlistId: "pl_1")
        let rock = FindChoice(start: .genre, genre: " Rock ")
        XCTAssertEqual(
            FindChoice.seeds(of: [trip, rock], playlists: playlists),
            [.playlist("pl_1"), .genre("Rock")])
        // Several artists in one box, and the same thing asked for twice.
        let artists = FindChoice(start: .artist, artist: "Linkin Park, Korn")
        XCTAssertEqual(
            FindChoice.seeds(of: [artists, rock, rock, FindChoice(start: .library)], playlists: []),
            [.artist("Linkin Park"), .artist("Korn"), .genre("Rock"), .library])
        // A box that's still empty, or a playlist that has gone: nothing is asked yet.
        XCTAssertEqual(FindChoice.seeds(of: [rock, FindChoice(start: .artist)], playlists: []), [])
        XCTAssertEqual(FindChoice.seeds(of: [trip], playlists: []), [])
        XCTAssertEqual(FindChoice.seeds(of: [], playlists: []), [])
        XCTAssertEqual(FindChoice(start: .mostPlayed).seeds(playlists: []), [.mostPlayed])
        XCTAssertEqual(FindChoice(start: .topArtist).seeds(playlists: []), [.topArtist])
        // Never more than the engine takes.
        let many = FindChoice(start: .artist, artist: (1...12).map { "Band \($0)" }.joined(separator: ","))
        XCTAssertEqual(FindChoice.seeds(of: [many], playlists: []).count, 8)
    }

    func testLinksToASongElsewhere() {
        XCTAssertEqual(
            ElsewhereLink.spotify.url(title: "What's New?", artist: "AC/DC")?.absoluteString,
            "https://open.spotify.com/search/AC/DC%20What's%20New%3F")
        XCTAssertEqual(
            ElsewhereLink.appleMusic.url(title: "Numb", artist: "Linkin Park")?.absoluteString,
            "https://music.apple.com/search?term=Linkin%20Park%20Numb")
        XCTAssertEqual(
            ElsewhereLink.soundCloud.url(title: "A & B", artist: "C")?.absoluteString,
            "https://soundcloud.com/search?q=C%20A%20%26%20B")
        XCTAssertNil(ElsewhereLink.spotify.url(title: " ", artist: ""))
    }

    func testADownloadSaysHowFarAlongItIs() throws {
        let json = """
            {"downloads": [
              {"job_id": 1, "batch_id": "b", "state": "running", "reason": null, "message": null,
               "video_id": "abcdefghijk", "title": "Song", "artists": ["A"], "video": false,
               "height": null, "fps": null, "thumbnail": null, "progress": 0.428},
              {"job_id": 2, "batch_id": "b", "state": "queued", "reason": null, "message": null,
               "video_id": "lmnopqrstuv", "title": "Other", "artists": [], "video": true,
               "height": 720, "fps": 30, "thumbnail": null, "progress": null}]}
            """
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let found = try decoder.decode(DownloadsAnswer.self, from: Data(json.utf8)).downloads
        XCTAssertEqual(found[0].percent, 42)
        XCTAssertEqual(found[0].progressNote, "Downloading… 42%")
        XCTAssertNil(found[1].percent)
        XCTAssertEqual(found[1].progressNote, "Waiting its turn…")
        XCTAssertEqual(
            PendingDownload(jobId: 3, state: "running").progressNote, "Downloading…")
        XCTAssertEqual(
            PendingDownload(jobId: 3, state: "running", progress: 1).progressNote,
            "Checking and naming it…")
        XCTAssertNil(PendingDownload(jobId: 3, state: "queued", progress: 0.5).percent)
        XCTAssertEqual(PendingDownload(jobId: 3, state: "running", progress: 1.7).percent, 100)
    }

    func testDownloadsAreGroupedIntoVideosAndSongs() throws {
        func track(_ path: String, _ title: String) throws -> Track {
            Track(path: path, title: title, video: path.hasSuffix(".mp4") ? true : nil)
        }
        let newest = try track("Music/Videos/Band/Clip.mp4", "Clip")
        let song = try track("Music/Band/Album/01 Tune.m4a", "Tune")
        let older = try track("Music/Videos/Band/Old Clip.mp4", "Old Clip")
        XCTAssertTrue(newest.isVideo)
        XCTAssertFalse(song.isVideo)
        let videosFirst = DownloadGroups.byKind([newest, song, older], videosFirst: true)
        XCTAssertEqual(videosFirst.map(\.name), ["Videos", "Songs"])
        XCTAssertEqual(videosFirst[0].tracks.map(\.title), ["Clip", "Old Clip"])  // order kept
        XCTAssertEqual(
            DownloadGroups.byKind([newest, song], videosFirst: false).map(\.name), ["Songs", "Videos"])
        // An empty box isn't shown.
        XCTAssertEqual(DownloadGroups.byKind([song], videosFirst: true).map(\.name), ["Songs"])
    }

    func testDownloadsAreGroupedByGenre() {
        func song(_ title: String, _ genre: String?, _ added: String) -> Track {
            Track(path: "Music/\(title).m4a", title: title, genre: genre, acquired: added)
        }
        let groups = Genres.groups([
            song("a", "Hip-Hop/Rap", "2026-10-02T09:00:00Z"),
            song("b", nil, "2026-10-02T08:00:00Z"),
            song("c", "Indie", "2026-10-01T10:00:00Z"),
            song("d", "hip hop", "2026-09-30T10:00:00Z"),
            song("e", "Rap", "2026-10-01T12:00:00Z"),
            song("f", "Hip Hop", "2026-09-29T10:00:00Z"),
            song("g", "Hip Hop", "2026-09-28T10:00:00Z"),
            song("h", " ", "2026-09-27T10:00:00Z"),
        ])
        // The group with the newest song first; songs with no genre last.
        XCTAssertEqual(groups.map(\.name), ["Hip Hop", "Rap", "Indie", ""])
        // One group for every way of writing it, named as most of its songs spell it,
        // its songs in the order they were given.
        XCTAssertEqual(groups[0].tracks.map(\.title), ["a", "d", "f", "g"])
        XCTAssertEqual(groups[3].tracks.map(\.title), ["b", "h"])
        XCTAssertEqual(Genres.key("R & B"), Genres.key("r&b"))
        XCTAssertEqual(Genres.key("Electronica/Dance"), "electronica")
        XCTAssertEqual(Genres.key(nil), "")
        XCTAssertEqual(Genres.groups([]), [])
    }

    func testTheGuidesWords() {
        XCTAssertEqual(DiscoverSeed.typed("Hip hop").params, ["kind": "typed", "name": "Hip hop"])
        XCTAssertEqual(Guided.count(from: " 250 "), 250)
        XCTAssertNil(Guided.count(from: "0"))
        XCTAssertNil(Guided.count(from: "501"))
        XCTAssertNil(Guided.count(from: "lots"))
        typealias Seed = DiscoverAnswer.Seed
        XCTAssertEqual(
            Guided.what(237, from: [Seed(kind: "genre", label: "Hip hop")]), "237 hip hop songs")
        XCTAssertEqual(
            Guided.what(50, from: [Seed(kind: "artist", label: "Linkin Park")]),
            "50 songs by Linkin Park and artists like them")
        XCTAssertEqual(
            Guided.what(1, from: [Seed(kind: "library", label: "your library")]),
            "1 song like the ones in your library")
        XCTAssertEqual(
            Guided.what(10, from: [Seed(kind: "most_played", label: "your most played")]),
            "10 songs like the ones you play most")
        XCTAssertEqual(
            Guided.what(25, from: [Seed(kind: "lastfm", label: "your Last.fm")]),
            "25 songs like your most played on Last.fm")
        XCTAssertEqual(Guided.what(10, from: []), "10 songs")
        XCTAssertTrue(Guided.downloadNote(minutes: 150, days: 1).contains("about 2½ hours"))
        XCTAssertFalse(Guided.downloadNote(minutes: 150, days: 1).contains("daily limit"))
        XCTAssertTrue(Guided.downloadNote(minutes: 400, days: 2).contains("over 2 days"))
    }

    func testWhatADownloadStartedAutomaticallySays() {
        typealias Seed = DiscoverAnswer.Seed
        let hipHop = [Seed(kind: "genre", label: "Hip hop")]
        let all = Guided.startedNote(
            250, from: hipHop, wanted: 250, minutes: 95, allowance: 250, limit: 250)
        XCTAssertTrue(all.hasPrefix("250 hip hop songs are on the way. It takes about 1½ hours"))
        XCTAssertTrue(all.hasSuffix("the Mac stays awake while they download."))
        XCTAssertFalse(all.contains("limit"))
        // Fewer were new than were asked for.
        let fewer = Guided.startedNote(
            37, from: hipHop, wanted: 50, minutes: 20, allowance: 250, limit: 250)
        XCTAssertTrue(fewer.hasPrefix("37 hip hop songs are on the way (50 were asked for;"))
        // Some of today's limit is used already: the rest wait, and that's said.
        let some = Guided.startedNote(
            250, from: hipHop, wanted: 250, minutes: 95, allowance: 120, limit: 250)
        XCTAssertTrue(some.contains("has room for 120 now; the other 130 start by themselves"))
        XCTAssertFalse(some.contains("It takes"))
        let none = Guided.startedNote(
            10, from: [], wanted: 10, minutes: 5, allowance: -3, limit: 250)
        XCTAssertTrue(none.hasPrefix("10 songs are on the way. Your limit of 250"))
        XCTAssertTrue(none.contains("used up for now"))
        let one = Guided.startedNote(1, from: [], wanted: 1, minutes: 1, allowance: 9, limit: 250)
        XCTAssertTrue(one.hasPrefix("1 song is on the way. It takes about a minute"))
    }

    func testWhyWaitingDownloadsAreNotMoving() {
        let clock: (Date) -> String = { _ in "3:10 pm" }
        XCTAssertNil(QueueStatus(state: "running", queued: 40, running: 1).holdUp(clock: clock))
        XCTAssertNil(QueueStatus().holdUp(clock: clock))
        let limit = QueueStatus(
            queued: 30, dailyCount: 250, dailyCap: 250,
            dailyResumeAt: "2026-10-03T05:10:00.250000Z")
        XCTAssertEqual(
            limit.holdUp(clock: clock),
            "That's 250 downloads in 24 hours, your daily limit. The rest carry on by "
                + "themselves from 3:10 pm.")
        let refused = QueueStatus(state: "paused_by_youtube", resumeAt: "2026-10-03T05:10:00Z")
        XCTAssertTrue(refused.holdUp(clock: clock)?.contains("resting until 3:10 pm") == true)
        XCTAssertTrue(
            QueueStatus(state: "paused_by_youtube").holdUp(clock: clock)?
                .contains("for a few hours") == true)
        XCTAssertEqual(QueueStatus(state: "paused").holdUp(clock: clock), "Downloads are paused.")
        // The engine's times, with and without parts of a second.
        XCTAssertNotNil(engineDate("2026-10-03T05:10:00.250000Z"))
        XCTAssertEqual(
            engineDate("2026-10-03T05:10:00.000000Z"), engineDate("2026-10-03T05:10:00Z"))
        XCTAssertNil(engineDate(nil))
        XCTAssertNil(engineDate("soon"))
    }

    func testQueueStatusIsRead() throws {
        let json = """
            {"state": "idle", "reason": null, "resume_at": null, "queued": 12, "running": 0,
             "done": 240, "failed": 0, "needs_review": 1, "daily_count": 250, "daily_cap": 250,
             "daily_resume_at": "2026-10-03T05:10:00.250000Z"}
            """
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let status = try decoder.decode(QueueStatus.self, from: Data(json.utf8))
        XCTAssertEqual(status.queued, 12)
        XCTAssertEqual(status.dailyCount, 250)
        XCTAssertNotNil(engineDate(status.dailyResumeAt))
    }

    func testAnImportIsRead() throws {
        let json = """
            {"source": "youtube", "name": "Road Trip", "tracks": [
              {"title": "Tune", "artists": ["Band", "Guest"], "album": null, "duration_s": 214,
               "is_explicit": true,
               "candidate": {"video_id": "songCCCCCCC", "title": "Tune", "artists": ["Band"],
                             "album": "Tunes", "album_browse_id": "MPREb_1", "duration_s": 214,
                             "is_explicit": true, "video_type": "MUSIC_VIDEO_TYPE_ATV",
                             "year": null, "thumbnail": null, "is_official_audio": true}}]}
            """
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let playlist = try decoder.decode(ImportedPlaylist.self, from: Data(json.utf8))
        XCTAssertEqual(playlist.name, "Road Trip")
        let track = try XCTUnwrap(playlist.tracks.first)
        XCTAssertEqual(track.artistName, "Band, Guest")
        // It goes back to the engine as it came.
        let back = track.params
        XCTAssertEqual(back["title"] as? String, "Tune")
        XCTAssertEqual(back["duration_s"] as? Int, 214)
        XCTAssertEqual(back["is_explicit"] as? Bool, true)
        XCTAssertNil(back["album"])
        let candidate = try XCTUnwrap(back["candidate"] as? [String: Any])
        XCTAssertEqual(candidate["video_id"] as? String, "songCCCCCCC")
        XCTAssertEqual(candidate["video_type"] as? String, "MUSIC_VIDEO_TYPE_ATV")
        XCTAssertEqual(candidate["album_browse_id"] as? String, "MPREb_1")
        XCTAssertEqual(track.candidate?.result.videoId, "songCCCCCCC")

        let answer = try decoder.decode(
            ImportFindAnswer.self,
            from: Data(
                """
                {"found": [{"state": "owned", "track_id": "t_1"}, {"state": "not_found"},
                           {"state": "something new"}]}
                """.utf8))
        XCTAssertEqual(answer.found.map(\.kind), [.owned, .notFound, .notFound])
        XCTAssertEqual(answer.found.first?.trackId, "t_1")
    }

    func testUpNextPlaysAfterTheSongPlayingInTheOrderAsked() {
        func song(_ title: String) -> Track { Track(path: "yt:\(title)", title: title) }
        var queue = PlayQueue()
        XCTAssertFalse(queue.queueNext(song("lonely")))  // nothing playing to put it after
        queue.play([song("a"), song("b"), song("c")], startAt: 0)
        XCTAssertTrue(queue.queueNext(song("x")))
        XCTAssertTrue(queue.queueNext(song("y")))
        XCTAssertEqual(queue.upNext.map(\.title), ["x", "y", "b", "c"])
        XCTAssertEqual(queue.advance()?.title, "x")
        // Asked for while x plays: after y, which was asked for first.
        XCTAssertTrue(queue.queueNext(song("z")))
        XCTAssertEqual(queue.upNext.map(\.title), ["y", "z", "b", "c"])
        // Pressed again: it comes back out; the song playing stays where it is.
        XCTAssertTrue(queue.removeUpcoming(song("y").id))
        XCTAssertEqual(queue.upNext.map(\.title), ["z", "b", "c"])
        XCTAssertFalse(queue.removeUpcoming(song("x").id))  // playing now
        XCTAssertFalse(queue.removeUpcoming(song("nope").id))
        XCTAssertTrue(queue.queueNext(song("v")))  // still after z, the other one asked for
        XCTAssertEqual(queue.upNext.map(\.title), ["z", "v", "b", "c"])
        // A new list starts afresh.
        queue.play([song("d")], startAt: 0)
        XCTAssertTrue(queue.queueNext(song("w")))
        XCTAssertEqual(queue.upNext.map(\.title), ["w"])
        // A result is kept and read back whole (the YouTube Queue is saved this way).
        let result = SearchResult(videoId: "DuQGokwsWF8", title: "T", artists: ["A"], durationS: 130)
        let saved = try? JSONEncoder().encode([result])
        XCTAssertEqual(saved.flatMap { try? JSONDecoder().decode([SearchResult].self, from: $0) }, [result])
    }

    func testProfilesKeepPeopleApart() throws {
        // Before profiles there was one library: it becomes the first profile's.
        var list = ProfileList(firstNamed: "  D ", libraryRoot: "/Volumes/Music/Library")
        XCTAssertEqual(list.current, Profile(id: "default", name: "D", libraryRoot: "/Volumes/Music/Library"))
        XCTAssertEqual(ProfileList(firstNamed: " ", libraryRoot: nil).current.name, "Me")

        // A new profile gets a folder of its own in the Music folder, named after it.
        let music = "/Users/someone/Music"
        XCTAssertEqual(list.suggestedRoot(for: " C ", in: music), "/Users/someone/Music/Music C")
        XCTAssertEqual(
            list.suggestedRoot(for: "Kids / 1:2", in: music), "/Users/someone/Music/Music Kids  12")
        XCTAssertNil(list.suggestedRoot(for: " . ", in: music))
        XCTAssertNil(list.suggestedRoot(for: "C", in: nil))
        // Even with no library of its own yet, the first profile's list can suggest one.
        XCTAssertEqual(
            ProfileList(firstNamed: "D", libraryRoot: nil).suggestedRoot(for: "C", in: music),
            "/Users/someone/Music/Music C")
        // A folder that's there already is never suggested: the next free name is.
        XCTAssertEqual(
            list.suggestedRoot(for: "C", in: music) { $0.hasSuffix("/Music C") },
            "/Users/someone/Music/Music C 2")
        let made = try list.add(name: "  C  ", libraryRoot: "/Volumes/Music/Library (C)") { "p_1" }
        XCTAssertEqual(made, Profile(id: "p_1", name: "C", libraryRoot: "/Volumes/Music/Library (C)", isNew: true))
        XCTAssertFalse(made.isChild)
        // Nor one that's another profile's, however it's written.
        var two = ProfileList(firstNamed: "D", libraryRoot: "/Users/someone/Music/music kids")
        XCTAssertEqual(
            two.suggestedRoot(for: "Kids", in: music), "/Users/someone/Music/Music Kids 2")
        // A child's profile is marked as one, and the mark can be changed.
        let kids = try two.add(name: "Kids", libraryRoot: "/k", isChild: true) { "p_k" }
        XCTAssertTrue(kids.isChild)
        two.setChild("p_k", false)
        XCTAssertEqual(two.profile("p_k")?.isChild, false)
        two.setChild("p_k", true)
        // A list saved before the mark existed still reads, with nobody a child.
        let old = Data(
            #"{"profiles":[{"id":"default","name":"D","libraryRoot":"/x","isNew":false}],"currentId":"default"}"#
                .utf8)
        XCTAssertEqual(try JSONDecoder().decode(ProfileList.self, from: old).current.isChild, false)
        XCTAssertEqual(
            try JSONDecoder().decode(ProfileList.self, from: JSONEncoder().encode(two)), two)
        XCTAssertEqual(list.current.id, "default")  // adding doesn't switch

        // Names are one each, whatever their capitals; and so are folders.
        XCTAssertThrowsError(try list.add(name: "c", libraryRoot: "/x")) {
            XCTAssertEqual($0 as? ProfileList.Problem, .taken("C"))
        }
        XCTAssertThrowsError(try list.add(name: "", libraryRoot: "/x")) {
            XCTAssertEqual($0 as? ProfileList.Problem, .noName)
        }
        XCTAssertThrowsError(try list.add(name: String(repeating: "n", count: 41), libraryRoot: "/x")) {
            XCTAssertEqual($0 as? ProfileList.Problem, .tooLong)
        }
        XCTAssertThrowsError(try list.add(name: "Kids", libraryRoot: "/Volumes/Music/library/")) {
            XCTAssertEqual($0 as? ProfileList.Problem, .sameFolder("D"))
        }
        XCTAssertTrue(
            ProfileList.Problem.sameFolder("D").localizedDescription.contains("would be mixed"))
        try list.add(name: "Kids", libraryRoot: "/Volumes/Music/Library (Kids)") { "p_1" }  // an id in use
        XCTAssertEqual(Set(list.profiles.map(\.id)).count, 3)

        // Switching, and what can't be removed.
        list.switchTo("p_1")
        XCTAssertEqual(list.current.name, "C")
        list.setCurrentLibrary("/Volumes/Music/Library (C)")  // the engine has made it
        XCTAssertFalse(list.current.isNew)
        list.switchTo("nobody")
        XCTAssertEqual(list.current.id, "p_1")
        XCTAssertThrowsError(try list.remove("p_1")) {
            XCTAssertEqual($0 as? ProfileList.Problem, .inUse)
        }
        try list.rename("default", to: "Dad")
        XCTAssertThrowsError(try list.rename("default", to: "kids"))
        try list.rename("default", to: "dad")  // its own name, in other capitals
        try list.remove("default")
        XCTAssertEqual(list.profiles.map(\.name), ["C", "Kids"])
        var one = ProfileList(firstNamed: "D", libraryRoot: nil)
        XCTAssertThrowsError(try one.remove("default")) {
            XCTAssertEqual($0 as? ProfileList.Problem, .lastOne)
        }

        // It's saved and read back whole.
        let saved = try JSONEncoder().encode(list)
        XCTAssertEqual(try JSONDecoder().decode(ProfileList.self, from: saved), list)
        XCTAssertTrue(ProfileList.newId().hasPrefix("p_"))
        XCTAssertEqual(ProfileList.newId().count, 10)
    }

    func testAPlaylistSentToAnotherProfileIsNamedSoNothingIsMixed() {
        let sent = PendingShare(sourceRoot: "/L (C)", paths: ["Music/A/B/01 T.m4a"], playlistName: "Road Trip", fromName: "C")
        XCTAssertEqual(sent.nameHere(among: ["Chill"]), "Road Trip")
        XCTAssertEqual(sent.nameHere(among: ["road trip"]), "Road Trip (from C)")
        XCTAssertEqual(sent.nameHere(among: ["Road Trip", "Road Trip (from C)"]), "Road Trip (from C 2)")
        // What's waiting to be copied isn't any one profile's setting.
        XCTAssertFalse(ProfileSettings.belongsToProfile("pendingShares"))
        let saved = try? JSONEncoder().encode(["p_1": [sent]])
        XCTAssertEqual(
            saved.flatMap { try? JSONDecoder().decode([String: [PendingShare]].self, from: $0) },
            ["p_1": [sent]])
    }

    func testWhichSettingsBelongToAProfile() {
        let saved: [String: Any] = [
            "findArtist": "Linkin Park", "sidebarLibrary": "songs,albums", "importSource": "spotify",
            "profiles": Data(), "libraryRoot": "/somewhere", "profileSettings.default": ["a": 1],
            "NSWindow Frame main": "0 0 800 600", "AppleLanguages": ["en"],
        ]
        XCTAssertEqual(
            Set(ProfileSettings.toKeep(saved).keys), ["findArtist", "sidebarLibrary", "importSource"])
        // Switching to someone with other settings: theirs are set, and what only the
        // first person had goes back to the app's own default.
        let theirs: [String: Any] = ["findArtist": "Phoenix", "libraryRoot": "/not theirs to set"]
        let changes = ProfileSettings.changes(from: saved, to: theirs)
        XCTAssertEqual(changes.set.keys.sorted(), ["findArtist"])
        XCTAssertEqual(changes.set["findArtist"] as? String, "Phoenix")
        XCTAssertEqual(changes.remove, ["importSource", "sidebarLibrary"])
        // A brand new profile starts from the app's defaults.
        XCTAssertEqual(
            ProfileSettings.changes(from: saved, to: [:]).remove,
            ["findArtist", "importSource", "sidebarLibrary"])
    }

    func testTheLookIsReadBackAndIsTheComputersOwn() {
        XCTAssertEqual(AppLook(saved: nil), .native)
        XCTAssertEqual(AppLook(saved: "warm"), .warm)
        // A look from a newer version, or a typo: the app opens as it always did.
        XCTAssertEqual(AppLook(saved: "neon"), .native)
        XCTAssertEqual(AppLook.allCases.map(\.title), ["Apple Native Build", "Warm Look"])
        // The look is put on when the app opens, so it isn't put away with a profile's
        // settings; nor is the highlight colour macOS keeps for the app.
        XCTAssertFalse(ProfileSettings.belongsToProfile(AppLook.key))
        XCTAssertFalse(ProfileSettings.belongsToProfile("AppleAccentColor"))
    }

    func testTheWarmLooksWordsCanBeReadOnEverySurface() {
        XCTAssertEqual(RGB(0xFFFFFF).contrast(with: RGB(0x000000)), 21, accuracy: 0.01)
        XCTAssertEqual(RGB(0x808080).contrast(with: RGB(0x808080)), 1, accuracy: 0.01)
        XCTAssertEqual(RGB(0xFF8000).red, 1)
        XCTAssertEqual(RGB(0xFF8000).green, 128.0 / 255, accuracy: 0.0001)
        for surface in WarmPalette.surfaces {
            // 7 is "comfortable" for words; the quieter words on a page are drawn at
            // about half strength, so the full-strength ones need plenty to spare.
            XCTAssertGreaterThan(WarmPalette.text.contrast(with: surface), 12)
            // And every surface is warm: more red than green, more green than blue.
            XCTAssertGreaterThan(surface.red, surface.green)
            XCTAssertGreaterThan(surface.green, surface.blue)
        }
    }

    func testWhatACoverGivesTheGlow() {
        let amber = WarmPalette.glow
        // Black, white and grey have no colour to give: the look's own amber.
        XCTAssertEqual(RGB(0x000000).glow(plain: amber), amber)
        XCTAssertEqual(RGB(0x0A0503).glow(plain: amber), amber)
        XCTAssertEqual(RGB(0xFFFFFF).glow(plain: amber), amber)
        XCTAssertEqual(RGB(0x777777).glow(plain: amber), amber)
        // A dark red glows red, bright enough to see on a dark page.
        let red = RGB(0x3C0A0A).glow(plain: amber)
        XCTAssertEqual(red.red, 0.5, accuracy: 0.001)
        XCTAssertEqual(red.green, red.blue, accuracy: 0.001)
        XCTAssertLessThan(red.green, 0.1)
        // A pale pink keeps its hue, and doesn't glare.
        let pink = RGB(0xF7C6D0).glow(plain: amber)
        XCTAssertEqual(pink.red, 0.72, accuracy: 0.001)
        XCTAssertGreaterThan(pink.blue, pink.green)
        XCTAssertGreaterThan(pink.green, 0.5)
        // Every hue comes back as itself: green stays green, blue stays blue.
        let green = RGB(0x1E7A2C).glow(plain: amber)
        XCTAssertTrue(green.green > green.blue && green.blue > green.red)
        let blue = RGB(0x2040C0).glow(plain: amber)
        XCTAssertTrue(blue.blue > blue.green && blue.green > blue.red)
        let yellow = RGB(0xC8B400).glow(plain: amber)
        XCTAssertTrue(yellow.red > yellow.green && yellow.green > yellow.blue)
        let purple = RGB(0x7020A0).glow(plain: amber)
        XCTAssertTrue(purple.blue > purple.red && purple.red > purple.green)
    }

    func testSpotifyAccountsAndPlaylistsAreRead() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let status = try decoder.decode(
            AccountStatus.self,
            from: Data(
                """
                {"spotify": {"client_id": null, "signed_in": false, "name": null,
                             "redirect_uri": "http://127.0.0.1:36463/callback"}}
                """.utf8))
        XCTAssertFalse(status.spotify.signedIn)
        XCTAssertEqual(status.spotify.redirectUri, "http://127.0.0.1:36463/callback")
        XCTAssertNil(status.lastfm)  // an engine from before Last.fm
        let lists = try decoder.decode(
            SpotifyPlaylistsAnswer.self,
            from: Data(
                """
                {"playlists": [
                  {"id": "liked", "name": "Liked Songs", "owner": "Me", "total": 1412, "readable": true},
                  {"id": "theirs000001", "name": "Top Hits", "owner": "spotify", "total": 1,
                   "readable": false},
                  {"id": "mine00000001", "name": "Unknown Size", "owner": null, "total": null,
                   "readable": true}]}
                """.utf8))
        XCTAssertEqual(lists.playlists[0].label, "Liked Songs (1,412 songs)")
        XCTAssertEqual(
            lists.playlists[1].label,
            "Top Hits (1 song): someone else's, so Spotify won't give its songs")
        XCTAssertEqual(lists.playlists[2].label, "Unknown Size")
        XCTAssertEqual(
            ImportRequest.spotify(playlistId: "liked").params,
            ["source": "spotify", "playlist_id": "liked"])
        XCTAssertEqual(
            ImportRequest.youtube(link: "PLabc").params, ["source": "youtube", "link": "PLabc"])
        // Only Spotify's own sign-in page is ever opened.
        func page(_ address: String) throws -> URL? {
            try decoder.decode(
                SignInAnswer.self, from: Data("{\"authorize_url\": \"\(address)\"}".utf8)
            ).spotifyPage
        }
        XCTAssertNotNil(try page("https://accounts.spotify.com/authorize?client_id=x"))
        XCTAssertNil(try page("http://accounts.spotify.com/authorize"))
        XCTAssertNil(try page("https://accounts.spotify.com.example.org/authorize"))
        XCTAssertNil(try page("not an address"))
        // Made up here, so no file holds something shaped like a real id.
        let half = "0123456789abcdef"
        XCTAssertTrue(Imports.looksLikeSpotifyClientId(" \(half)\(half.uppercased())\n"))
        XCTAssertFalse(Imports.looksLikeSpotifyClientId(half))
        XCTAssertFalse(Imports.looksLikeSpotifyClientId(half + half.dropLast() + "!"))
    }

    func testDeezerLastfmAndFilesAreAskedFor() throws {
        XCTAssertEqual(
            ImportRequest.deezer(link: "deezer.com/playlist/1").params,
            ["source": "deezer", "link": "deezer.com/playlist/1"])
        XCTAssertEqual(
            ImportRequest.lastfm(list: LastfmList.top6month.rawValue).params,
            ["source": "lastfm", "list": "top_6month"])
        XCTAssertEqual(
            ImportRequest.file(path: "/tmp/list.csv", playlist: nil).params,
            ["source": "file", "path": "/tmp/list.csv"])
        XCTAssertEqual(
            ImportRequest.file(path: "/tmp/list.csv", playlist: "Gym").params,
            ["source": "file", "path": "/tmp/list.csv", "playlist": "Gym"])
        // The lists as the engine names them.
        XCTAssertEqual(
            LastfmList.allCases.map(\.rawValue),
            ["loved", "top_7day", "top_1month", "top_3month", "top_6month", "top_12month",
             "top_overall"])

        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let status = try decoder.decode(
            AccountStatus.self,
            from: Data(
                """
                {"spotify": {"client_id": null, "signed_in": false, "name": null, "redirect_uri": ""},
                 "lastfm": {"user": "Listener", "has_key": true, "connected": true}}
                """.utf8))
        XCTAssertEqual(status.lastfm, .init(user: "Listener", hasKey: true, connected: true))
        let read = try decoder.decode(
            ImportedPlaylist.self,
            from: Data(
                """
                {"source": "file", "name": "Road Trip", "more": false, "playlists": ["Road Trip", "Gym"],
                 "tracks": [{"title": "One", "artists": ["Band"], "album": null, "duration_s": null,
                             "is_explicit": null}]}
                """.utf8))
        XCTAssertEqual(read.playlists, ["Road Trip", "Gym"])
        XCTAssertEqual(read.tracks.map(\.title), ["One"])
        // Made up here, so no file holds something shaped like a real key.
        let half = "0123456789abcdef"
        XCTAssertTrue(Imports.looksLikeLastfmKey(" \(half)\(half)\n"))
        XCTAssertFalse(Imports.looksLikeLastfmKey(half))
        XCTAssertFalse(Imports.looksLikeLastfmKey(half + half.dropLast() + "g"))
    }

    func testKaraokeSaysWhatItCosts() throws {
        // A song that's a file: only the video's sound is fetched.
        XCTAssertEqual(KaraokeCost.downloads(songIsAFile: true), 1)
        XCTAssertEqual(KaraokeCost.words(songIsAFile: true), "Uses 1 download from your daily limit.")
        // A song played from YouTube: its sound is fetched as well.
        XCTAssertEqual(KaraokeCost.downloads(songIsAFile: false), 2)
        XCTAssertTrue(KaraokeCost.words(songIsAFile: false).hasPrefix("Uses 2 downloads"))
        // The engine's answer says why, when it couldn't.
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let answer = try decoder.decode(
            TrackLyrics.self,
            from: Data(
                #"{"synced": null, "plain": null, "how": null, "source": null, "note": "Today's download limit is used up."}"#
                    .utf8))
        XCTAssertNil(answer.synced)
        XCTAssertEqual(answer.note, "Today's download limit is used up.")
        let older = try decoder.decode(
            TrackLyrics.self, from: Data(#"{"synced": "[00:01.00]La", "plain": null}"#.utf8))
        XCTAssertNil(older.note)
    }

    func testAChangeMadeOnAPageIsOnlyForNow() {
        // The page shows what was switched on it, or else the setting.
        XCTAssertTrue(PageChanges.shown(nil, setting: true))
        XCTAssertFalse(PageChanges.shown(false, setting: true))
        XCTAssertTrue(PageChanges.shown(true, setting: false))
        // How long, as Settings lists it and as a tip says it.
        XCTAssertEqual(
            PageChanges.options.map(PageChanges.label),
            ["5 minutes", "30 minutes", "1 hour", "Until the app is next opened"])
        XCTAssertEqual(PageChanges.goesBack(30), "in 30 minutes")
        XCTAssertEqual(PageChanges.goesBack(60), "in 1 hour")
        XCTAssertEqual(PageChanges.goesBack(120), "in 2 hours")
        XCTAssertEqual(PageChanges.goesBack(1), "in 1 minute")
        XCTAssertEqual(PageChanges.goesBack(0), "when the app is next opened")
        XCTAssertTrue(PageChanges.options.contains(PageChanges.standard))
    }

    func testTheCustomVisualizersOfferedAndTheStandardOne() {
        // The owner's three, and Visualizer 7 until another is chosen.
        XCTAssertEqual(CustomVisualizer.offered, [5, 7, 8])
        XCTAssertEqual(CustomVisualizer.standard, 7)
        XCTAssertEqual(CustomVisualizer.chosen(nil), 7)
        XCTAssertEqual(CustomVisualizer.chosen(5), 5)
        XCTAssertEqual(CustomVisualizer.chosen(8), 8)
        // A number saved once and not offered any more (or never): the standard.
        XCTAssertEqual(CustomVisualizer.chosen(3), 7)
        XCTAssertEqual(CustomVisualizer.chosen(0), 7)
        XCTAssertEqual(CustomVisualizer.offered.map(CustomVisualizer.title),
            ["Visualizer 5", "Visualizer 7", "Visualizer 8"])
    }

    func testTheVisualizersQualities() {
        // Particle Accelerator's five, and Medium until another is chosen.
        XCTAssertEqual(CustomVisualizer.qualities, ["auto", "low", "medium", "high", "ultra"])
        XCTAssertEqual(CustomVisualizer.standardQuality, "medium")
        XCTAssertEqual(CustomVisualizer.quality(nil), "medium")
        XCTAssertEqual(CustomVisualizer.quality("ultra"), "ultra")
        XCTAssertEqual(CustomVisualizer.quality("auto"), "auto")
        // A name that isn't one of them: the standard.
        XCTAssertEqual(CustomVisualizer.quality("ultimate"), "medium")
        XCTAssertEqual(
            CustomVisualizer.qualities.map(CustomVisualizer.qualityTitle),
            ["Auto", "Low", "Medium", "High", "Ultra"])
    }

    func testTheVisualizerDoesNotListenOnAnOutputWithALongDelay() {
        // As measured: the iMac's speakers, Bluetooth headphones, an Apple TV over AirPlay.
        XCTAssertTrue(CustomVisualizer.mayListen(outputDelay: 0.026))
        XCTAssertTrue(CustomVisualizer.mayListen(outputDelay: 0.195))
        XCTAssertFalse(CustomVisualizer.mayListen(outputDelay: 2.012))
        XCTAssertFalse(CustomVisualizer.mayListen(outputDelay: CustomVisualizer.longestOutputDelay))
        // Nothing known about the output: as it always was.
        XCTAssertTrue(CustomVisualizer.mayListen(outputDelay: nil))
        XCTAssertEqual(
            CustomVisualizer.notListening(to: "Apple TV"),
            "The visualizer can't follow the music while the sound is going to Apple TV. "
                + "Listening there makes the song skip, so it's off until the sound is back "
                + "on the Mac's speakers, headphones or a wired output.")
    }

    func testWhatCountsAsAnOutputWithALongDelay() {
        // As measured: the iMac's speakers, Bluetooth headphones, an Apple TV over AirPlay.
        XCTAssertFalse(LongDelayOutput.isOne(delay: 0.026))
        XCTAssertFalse(LongDelayOutput.isOne(delay: 0.195))
        XCTAssertTrue(LongDelayOutput.isOne(delay: 2.012))
        XCTAssertTrue(LongDelayOutput.isOne(delay: LongDelayOutput.threshold))
        XCTAssertFalse(LongDelayOutput.isOne(delay: nil))
        // After Pause the output stays muted until what was already sent has run out.
        XCTAssertEqual(LongDelayOutput.silenceAfterPause(delay: 2.012), 2.212, accuracy: 0.0001)
        XCTAssertGreaterThan(LongDelayOutput.silenceAfterPause(delay: 2.012), 2.012)
        // The visualizer goes by the same line.
        XCTAssertEqual(CustomVisualizer.longestOutputDelay, LongDelayOutput.threshold)
    }

    func testTheLocalVisualizersSwitch() {
        XCTAssertEqual(PagePicture.allCases.map(\.label), ["Song", "Video", "Visualizer"])
        XCTAssertEqual(PagePicture.chosen(videoWanted: false, visualizerOn: false), .song)
        XCTAssertEqual(PagePicture.chosen(videoWanted: false, visualizerOn: true), .visualizer)
        // The visualizer stands in for the cover only: a video is still a video.
        XCTAssertEqual(PagePicture.chosen(videoWanted: true, visualizerOn: false), .video)
        XCTAssertEqual(PagePicture.chosen(videoWanted: true, visualizerOn: true), .video)
    }

    func testWhatStandsWhereTheCoverWouldBe() {
        // A video, once its picture is ready, whatever else is switched on.
        XCTAssertEqual(
            PagePicture.showing(videoReady: true, visualizerOn: true, songPlaying: true), .video)
        XCTAssertEqual(
            PagePicture.showing(videoReady: true, visualizerOn: false, songPlaying: true), .video)
        // The visualizer, for a song that's playing: with nothing playing it has nothing
        // to move to, and the page shows what it always did.
        XCTAssertEqual(
            PagePicture.showing(videoReady: false, visualizerOn: true, songPlaying: true),
            .visualizer)
        XCTAssertEqual(
            PagePicture.showing(videoReady: false, visualizerOn: true, songPlaying: false), .song)
        XCTAssertEqual(
            PagePicture.showing(videoReady: false, visualizerOn: false, songPlaying: true), .song)
        // The whole screen is for a video or the visualizer, never for a cover.
        XCTAssertTrue(PagePicture.video.canFillTheScreen)
        XCTAssertTrue(PagePicture.visualizer.canFillTheScreen)
        XCTAssertFalse(PagePicture.song.canFillTheScreen)
    }

    func testAnArtistsPageIsRead() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let json = """
            {"found": true, "artist_id": "UCaaaaaaaaaaaaaaaaaaaaaa", "name": "Band",
             "description": "About them.", "subscribers": "25.4M", "monthly_audience": "170M",
             "views": "22,614 views", "thumbnail": "https://example.invalid/a.jpg",
             "songs": [
               {"video_id": "abcdefghijk", "title": "Song", "artists": ["Band"], "album": "Record",
                "album_browse_id": "MPREb_1", "duration_s": null, "is_explicit": false,
                "video_type": "MUSIC_VIDEO_TYPE_ATV", "year": null, "thumbnail": null,
                "plays": null, "is_official_audio": true, "owned": true},
               {"video_id": "lmnopqrstuv", "title": "Other", "artists": ["Band"], "owned": false}],
             "songs_playlist_id": "OLAK5uy_x",
             "albums": [{"browse_id": "MPREb_1", "title": "Record", "year": "2024", "kind": null,
                         "is_explicit": true, "thumbnail": null}],
             "singles": [{"browse_id": "MPREb_2", "title": "One", "year": "2025", "kind": "Single",
                          "is_explicit": null, "thumbnail": null}],
             "related": [{"artist_id": "UCbbbbbbbbbbbbbbbbbbbbbb", "name": "Others",
                          "monthly_audience": "28.3M", "thumbnail": null}],
             "owned_songs": 31}
            """
        let info = try decoder.decode(ArtistInfo.self, from: Data(json.utf8))
        XCTAssertEqual(info.name, "Band")
        XCTAssertEqual(info.numbers, "25.4M subscribers · 170M monthly audience · 22,614 views")
        XCTAssertEqual(info.ownedWords, "You have 31 of their songs")
        XCTAssertEqual(info.songs?.map(\.owned), [true, false])
        XCTAssertEqual(info.songs?.first?.result.track.videoId, "abcdefghijk")
        XCTAssertEqual(info.albums?.first?.caption, "2024")
        XCTAssertEqual(info.singles?.first?.caption, "Single · 2025")
        XCTAssertEqual(info.related?.first?.monthlyAudience, "28.3M")
        // A song goes back to the engine as it came, for a download plan.
        let back = try XCTUnwrap(info.songs?.first?.candidate.params)
        XCTAssertEqual(back["video_id"] as? String, "abcdefghijk")
        XCTAssertEqual(back["album_browse_id"] as? String, "MPREb_1")
        XCTAssertTrue(JSONSerialization.isValidJSONObject(back))

        // Nobody of that name: only the name comes back.
        let none = try decoder.decode(
            ArtistInfo.self, from: Data(#"{"found": false, "name": "Zzyzx"}"#.utf8))
        XCTAssertFalse(none.found)
        XCTAssertEqual(none.numbers, "")
        XCTAssertNil(none.ownedWords)

        let album = try decoder.decode(
            ArtistAlbum.self,
            from: Data(
                """
                {"browse_id": "MPREb_1", "title": "Record", "year": "2024", "artists": ["Band"],
                 "thumbnail": null,
                 "songs": [{"video_id": "abcdefghijk", "title": "Song", "artists": ["Band"],
                            "duration_s": 187, "owned": false}]}
                """.utf8))
        XCTAssertEqual(album.songs.first?.result.durationS, 187)
    }

    func testArtistsOnTheDiscoverSideAndWhoseTheyAre() throws {
        // One artist, however a name is written.
        XCTAssertEqual(ArtistNames.key("JAY-Z"), ArtistNames.key("Jay Z"))
        XCTAssertEqual(ArtistNames.key("The Beatles"), ArtistNames.key("beatles"))
        XCTAssertEqual(ArtistNames.key("Beyoncé"), ArtistNames.key("BEYONCE"))
        XCTAssertNotEqual(ArtistNames.key("Phoenix"), ArtistNames.key("Phoenix Rising"))
        XCTAssertEqual(ArtistNames.key(" - "), "")

        // The artists behind a page of picks: the one with most picks first, then as met.
        let json = """
            {"picks": [
              {"video_id": "aaaaaaaaaaa", "title": "One", "artists": ["Snoop Dogg"], "why": "w", "hits": 1,
               "thumbnail": "https://example.invalid/snoop.jpg"},
              {"video_id": "bbbbbbbbbbb", "title": "Two", "artists": ["Jay-Z", "Linkin Park"], "why": "w", "hits": 1,
               "thumbnail": null},
              {"video_id": "ccccccccccc", "title": "Three", "artists": ["JAY Z"], "why": "w", "hits": 1,
               "thumbnail": "https://example.invalid/jay.jpg"},
              {"video_id": "ddddddddddd", "title": "Four", "artists": [], "why": "w", "hits": 1},
              {"video_id": "eeeeeeeeeee", "title": "Five", "artists": ["Dr. Dre"], "why": "w", "hits": 1}],
             "wanted": 5, "radios": 1, "seeds": [], "note": null}
            """
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let picks = try decoder.decode(DiscoverAnswer.self, from: Data(json.utf8)).picks
        let behind = ArtistNames.behind(picks)
        XCTAssertEqual(behind.map(\.name), ["Jay-Z", "Snoop Dogg", "Dr. Dre"])
        XCTAssertEqual(behind.map(\.picks), [2, 1, 1])
        XCTAssertEqual(behind[0].thumbnail, "https://example.invalid/jay.jpg")  // their first with one
        XCTAssertEqual(behind[0].caption, "2 songs in What's New")
        XCTAssertEqual(behind[1].caption, "1 song in What's New")
        XCTAssertNil(behind[0].artistId)
        XCTAssertEqual(ArtistNames.behind([]), [])
        // A search's artist has no picks to count, and is opened by its id.
        let found = ListedArtist(name: "Linkin Park", artistId: "UCx")
        XCTAssertNil(found.caption)
        XCTAssertEqual(found.id, "UCx")
        let answer = try decoder.decode(
            ArtistSearchAnswer.self,
            from: Data(
                #"{"artists": [{"artist_id": "UCx", "name": "Linkin Park", "monthly_audience": null, "thumbnail": null}]}"#
                    .utf8))
        XCTAssertEqual(answer.artists.map(\.name), ["Linkin Park"])
    }

    func testWhatTheOwnersListeningSaysAboutAnArtist() {
        func song(
            _ n: Int, _ title: String, artist: String = "Jay Z", album: String = "Album",
            seconds: Double = 180, added: String = "2026-10-01T10:00:00Z"
        ) -> Track {
            Track(
                path: "Music/\(artist)/\(album)/\(n) \(title).m4a", title: title, artist: artist,
                albumArtist: artist, album: album, durationS: seconds, trackId: "t_\(n)",
                acquired: added)
        }
        let library = Library(tracks: [
            song(1, "Often", seconds: 240, added: "2026-09-30T08:00:00Z"),
            song(2, "Once", album: "Other", seconds: 200),
            song(3, "Never"),
            song(4, "Theirs", artist: "Phoenix"),
            song(5, "Unplayed", artist: "Air"),
        ])
        let plays = [
            "t_1": PlayCount(count: 12, lastPlayed: "2026-10-02T09:30:00Z"),
            "t_2": PlayCount(count: 1, lastPlayed: "2026-10-03T01:00:00.250000Z"),
            "t_4": PlayCount(count: 20),
        ]
        // Found however the name is written.
        let jay = try! XCTUnwrap(ArtistNames.mine("JAY-Z", in: library.artists))
        XCTAssertEqual(jay.name, "Jay Z")
        XCTAssertNil(ArtistNames.mine("Linkin Park", in: library.artists))
        XCTAssertNil(ArtistNames.mine("", in: library.artists))

        let stats = ArtistStats(artist: jay, plays: plays, favourites: ["t_2", "t_3", "t_4"])
        XCTAssertEqual(stats.songs, 3)
        XCTAssertEqual(stats.albums, 2)
        XCTAssertEqual(stats.plays, 13)
        XCTAssertEqual(stats.secondsListened, 12 * 240 + 200)
        XCTAssertEqual(stats.listened, "51 min")
        XCTAssertEqual(stats.mostPlayed, .init(title: "Often", plays: 12))
        XCTAssertEqual(stats.favourites, 2)  // only theirs
        XCTAssertEqual(stats.lastPlayed, engineDate("2026-10-03T01:00:00.250000Z"))
        XCTAssertEqual(stats.firstAdded, engineDate("2026-09-30T08:00:00Z"))
        // Phoenix was played more: Jay Z is the owner's second.
        XCTAssertEqual(ArtistStats.rank(of: jay, among: library.artists, plays: plays), 2)
        let phoenix = try! XCTUnwrap(ArtistNames.mine("phoenix", in: library.artists))
        XCTAssertEqual(ArtistStats.rank(of: phoenix, among: library.artists, plays: plays), 1)

        // Nothing played yet: no time, no most played, no place among the artists.
        let air = try! XCTUnwrap(ArtistNames.mine("Air", in: library.artists))
        let quiet = ArtistStats(artist: air, plays: plays, favourites: [])
        XCTAssertEqual(quiet.plays, 0)
        XCTAssertNil(quiet.listened)
        XCTAssertNil(quiet.mostPlayed)
        XCTAssertNil(quiet.lastPlayed)
        XCTAssertNil(ArtistStats.rank(of: air, among: library.artists, plays: plays))
        // How long, in words.
        func listened(_ seconds: Double) -> String? {
            ArtistStats(
                artist: Library(tracks: [song(9, "S", artist: "X", seconds: seconds)]).artists[0],
                plays: ["t_9": PlayCount(count: 1)], favourites: []
            ).listened
        }
        XCTAssertEqual(listened(20), "under a minute")
        XCTAssertEqual(listened(3600), "1 hr")
        XCTAssertEqual(listened(9060), "2 hr 31 min")
    }

    func testWhatAnArtistsPageWouldDownload() {
        func song(_ id: String, owned: Bool = false) -> ArtistSong {
            ArtistSong(candidate: ImportCandidate(videoId: id, title: id), owned: owned)
        }
        let songs = [song("a"), song("b", owned: true), song("c"), song("a"), song("d")]
        XCTAssertEqual(ArtistSongs.missing(songs).map(\.videoId), ["a", "c", "d"])
        // One already in the library by its id, or on its way, isn't fetched again.
        XCTAssertEqual(ArtistSongs.missing(songs, skip: ["c"]).map(\.videoId), ["a", "d"])
        XCTAssertEqual(ArtistSongs.missing([]).count, 0)
        // Whose page "Artist Info" opens for a song.
        XCTAssertEqual(ArtistSongs.names(["Jay-Z", " Linkin Park ", "jay-z", ""]), ["Jay-Z", "Linkin Park"])
        XCTAssertEqual(ArtistSongs.names(["A", "B", "C", "D"]), ["A", "B", "C"])
        XCTAssertEqual(ArtistSongs.names([]), [])
    }

    func testWhatAnImportWillDownload() {
        func song(_ id: String) -> ImportCandidate { ImportCandidate(videoId: id, title: id) }
        let rows = [
            ImportRow(id: 0, track: ImportTrack(title: "A"), found: ImportFound(state: .owned, trackId: "t_1")),
            ImportRow(id: 1, track: ImportTrack(title: "B"), found: ImportFound(state: .found, candidate: song("b"))),
            ImportRow(id: 2, track: ImportTrack(title: "C"), found: ImportFound(state: .unsure, candidate: song("c"), why: "The length is different.")),
            ImportRow(id: 3, track: ImportTrack(title: "D"), found: ImportFound(state: .unsure, candidate: song("d"))),
            ImportRow(id: 4, track: ImportTrack(title: "E"), found: ImportFound(state: .notFound)),
            ImportRow(id: 5, track: ImportTrack(title: "F"), found: ImportFound(state: .queued, candidate: song("f"))),
            ImportRow(id: 6, track: ImportTrack(title: "B again"), found: ImportFound(state: .found, candidate: song("b"))),
            ImportRow(id: 7, track: ImportTrack(title: "A again"), found: ImportFound(state: .owned, trackId: "t_1")),
            ImportRow(id: 8, track: ImportTrack(title: "No id"), found: ImportFound(state: .owned)),
            ImportRow(id: 9, track: ImportTrack(title: "Not looked for")),
        ]
        let counts = Imports.counts(rows)
        XCTAssertEqual(
            counts,
            Imports.Counts(owned: 3, queued: 1, found: 2, unsure: 2, notFound: 1, waiting: 1))
        // Certain ones, and the unsure ones that are ticked; the same track only once.
        XCTAssertEqual(Imports.toDownload(rows, ticked: []).map(\.videoId), ["b"])
        XCTAssertEqual(Imports.toDownload(rows, ticked: [3, 4, 5]).map(\.videoId), ["b", "d"])
        XCTAssertEqual(Imports.ownedIds(rows), ["t_1"])
        XCTAssertEqual(
            Imports.summary(counts),
            "10 songs: 3 in your library, 2 to download, 1 already on the way, 2 not sure, "
                + "1 not found")
        XCTAssertEqual(
            Imports.summary(counts, ticked: 2),
            "10 songs: 3 in your library, 4 to download, 1 already on the way, 1 not found")
        XCTAssertEqual(Imports.summary(Imports.Counts(waiting: 1)), "1 song")
    }

    func testWhatAnImportSaysOnceStarted() {
        let all = Imports.startedNote(
            180, playlist: "Road Trip", owned: 20, minutes: 70, allowance: 250, limit: 250)
        XCTAssertTrue(
            all.hasPrefix(
                "180 songs are on the way, and join your playlist \"Road Trip\" as they arrive "
                    + "(20 you already had are in it now). It takes about 70 minutes"))
        let one = Imports.startedNote(
            1, playlist: "Mix", owned: 0, minutes: 1, allowance: 5, limit: 250)
        XCTAssertTrue(
            one.hasPrefix(
                "1 song is on the way, and joins your playlist \"Mix\" as it arrives. It takes"))
        let over = Imports.startedNote(
            400, playlist: "Everything", owned: 1, minutes: 150, allowance: 250, limit: 250)
        XCTAssertTrue(over.contains("(1 you already had is in it now)"))
        XCTAssertTrue(over.contains("has room for 250 now; the other 150 start by themselves"))
    }

    func testTheDailyLimitCounter() {
        XCTAssertEqual(DailyUse(used: 1, limit: 250).text, "1/250")
        XCTAssertEqual(DailyUse(used: 10, limit: 400).text, "10/400")
        XCTAssertEqual(DailyUse(used: 0, limit: 250).level, .safe)
        XCTAssertEqual(DailyUse(used: 149, limit: 250).level, .safe)
        XCTAssertEqual(DailyUse(used: 150, limit: 250).level, .middling)
        XCTAssertEqual(DailyUse(used: 212, limit: 250).level, .middling)
        XCTAssertEqual(DailyUse(used: 213, limit: 250).level, .nearlyOut)
        XCTAssertEqual(DailyUse(used: 250, limit: 250).level, .nearlyOut)
        // The limit was lowered below what's been used: still red, and it says so.
        XCTAssertEqual(DailyUse(used: 300, limit: 250).level, .nearlyOut)
        XCTAssertTrue(DailyUse(used: 300, limit: 250).explained.contains("limit is reached"))
        // A bigger limit moves the colours with it.
        XCTAssertEqual(DailyUse(used: 213, limit: 400).level, .safe)
        let fromStatus = DailyUse(QueueStatus(queued: 3, dailyCount: 12, dailyCap: 250))
        XCTAssertEqual(fromStatus, DailyUse(used: 12, limit: 250, waiting: 3))
        XCTAssertTrue(fromStatus.explained.hasPrefix("12 of your 250 downloads"))
        XCTAssertTrue(fromStatus.explained.contains("3 more are waiting"))
        XCTAssertFalse(fromStatus.explained.contains("limit is reached"))
        XCTAssertFalse(DailyUse(used: 1, limit: 250).explained.contains("waiting to download"))
    }

    func testHundredsOfDownloadsAreListedShortly() {
        func download(_ id: Int, _ state: String) -> PendingDownload {
            PendingDownload(jobId: id, state: state, videoId: "v\(id)")
        }
        // A handful are all listed, as they came.
        let few = [download(3, "queued"), download(2, "running"), download(1, "failed")]
        let short = DownloadsShown(few)
        XCTAssertEqual(short.rows, few)
        XCTAssertEqual([short.moreWaiting, short.moreEnded], [0, 0])
        // Hundreds, newest first as the engine gives them: the one downloading, then the
        // next three in line (the oldest), then two that didn't arrive.
        let many =
            (5...250).reversed().map { download($0, "queued") }
            + [download(4, "running"), download(3, "failed"), download(2, "needs_review"),
               download(1, "failed")]
        let shown = DownloadsShown(many)
        XCTAssertEqual(shown.rows.map(\.jobId), [4, 5, 6, 7, 3, 2])
        XCTAssertEqual(shown.moreWaiting, 243)
        XCTAssertEqual(shown.moreEnded, 1)
    }

    func testRoughTime() {
        XCTAssertEqual(roughTime(minutes: 0), "under a minute")
        XCTAssertEqual(roughTime(minutes: 1), "about a minute")
        XCTAssertEqual(roughTime(minutes: 25), "about 25 minutes")
        XCTAssertEqual(roughTime(minutes: 89), "about 89 minutes")
        XCTAssertEqual(roughTime(minutes: 130), "about 2 hours")
        XCTAssertEqual(roughTime(minutes: 150), "about 2½ hours")
        XCTAssertEqual(roughTime(minutes: 90), "about 1½ hours")
        XCTAssertEqual(roughTime(minutes: 600), "about 10 hours")
    }
}

/// Sharing with a phone player at home. No address or pairing code is written here:
/// each is put together while the test runs.
final class SharingTests: XCTestCase {
    private let address = [192, 168, 1, 20].map(String.init).joined(separator: ".")
    private let code = (1...6).map(String.init).joined()

    func testWhatTheEngineSaysIsRead() throws {
        let json = """
            {"on": true, "port": 40000, "address": "\(address)", "name": "A Library",
             "service": "_homemusicsync._tcp", "pairing": true, "pairing_seconds_left": 280,
             "devices": [{"id": "d_1", "device": "iPhone", "paired_at": "2026-01-31T09:30:00Z",
                          "last_synced": null}]}
            """
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let status = try decoder.decode(SharingStatus.self, from: Data(json.utf8))
        XCTAssertEqual(status.whereToFind, "\(address):40000")
        XCTAssertEqual(status.devices.map(\.device), ["iPhone"])
        XCTAssertEqual(status.pairingSecondsLeft, 280)
        XCTAssertTrue(status.pairing)

        let off = try decoder.decode(
            SharingStatus.self,
            from: Data(
                """
                {"on": false, "port": null, "address": null, "name": "A Library", "service": "s",
                 "devices": [], "pairing": false, "pairing_seconds_left": 0}
                """.utf8))
        XCTAssertNil(off.whereToFind)
    }

    func testTheAddressIsOnlyShownWhenThereIsOne() {
        XCTAssertNil(SharingStatus(on: true, port: 40000, address: nil).whereToFind)
        XCTAssertNil(SharingStatus(on: false, port: 40000, address: address).whereToFind)
        XCTAssertNil(SharingStatus(on: true, port: nil, address: address).whereToFind)
    }

    func testWhoWasJustPaired() {
        let phone = SharingStatus.Device(id: "d_1", device: "iPhone")
        let tablet = SharingStatus.Device(id: "d_2", device: "iPad")
        let before = SharingStatus(on: true, devices: [phone])
        let after = SharingStatus(on: true, devices: [phone, tablet])
        XCTAssertEqual(after.newDevices(since: before), [tablet])
        XCTAssertEqual(after.newDevices(since: nil), [phone, tablet])
        XCTAssertEqual(before.newDevices(since: after), [])
    }

    func testADevicesLine() {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        let locale = Locale(identifier: "en_GB")
        let never = SharingStatus.Device(
            id: "d_1", device: "iPhone", pairedAt: "2026-01-31T09:30:00Z")
        XCTAssertEqual(
            never.about(calendar: calendar, locale: locale),
            "Paired 31 Jan 2026. Not synced yet.")
        let synced = SharingStatus.Device(
            id: "d_1", device: "iPhone", pairedAt: "2026-01-31T09:30:00Z",
            lastSynced: "2026-02-01T18:05:00Z")
        let line = synced.about(calendar: calendar, locale: locale)
        XCTAssertTrue(line.hasPrefix("Paired 31 Jan 2026. Last synced 1 Feb 2026"), line)
        XCTAssertTrue(line.contains("18:05"), line)
        XCTAssertEqual(
            SharingStatus.Device(id: "d_1", device: "iPhone").about(), "Not synced yet.")
    }

    func testAPairingCodeOnTheScreen() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let shown = try decoder.decode(
            PairingCode.self, from: Data("{\"code\": \"\(code)\", \"seconds\": 300}".utf8))
        XCTAssertEqual(shown, PairingCode(code: code, seconds: 300))
        XCTAssertEqual(shown.spaced, "\(code.prefix(3)) \(code.suffix(3))")
        XCTAssertEqual(PairingCode(code: "12", seconds: 1).spaced, "12")
        XCTAssertEqual(PairingCode.clock(300), "5:00")
        XCTAssertEqual(PairingCode.clock(61), "1:01")
        XCTAssertEqual(PairingCode.clock(7), "0:07")
        XCTAssertEqual(PairingCode.clock(-3), "0:00")
    }

    func testTheSwitchIsAProfilesOwnSetting() {
        // Each profile has its own switch: one person sharing doesn't share the next's.
        XCTAssertTrue(ProfileSettings.belongsToProfile("shareWithDevices"))
    }

    func testAnAnnouncerThatWasNeverStartedAnnouncesNothing() {
        let announcer = HomeAnnouncer()
        XCTAssertNil(announcer.port)
        XCTAssertFalse(announcer.start(type: "_homemusicsync._tcp", port: 0))
        XCTAssertFalse(announcer.start(type: "_homemusicsync._tcp", port: 70000))
        XCTAssertNil(announcer.port)
        announcer.stop()  // twice is fine
    }
}

final class RowSelectionTests: XCTestCase {
    private let order = ["a", "b", "c", "d", "e"]

    func testAClickPicksOneAndCommandClickAddsAndDrops() {
        var selection = RowSelection()
        selection.click("b", .one, in: order)
        selection.click("d", .toggle, in: order)
        XCTAssertEqual(selection.chosen, ["b", "d"])
        selection.click("b", .toggle, in: order)
        XCTAssertEqual(selection.chosen, ["d"])
        selection.click("a", .one, in: order)
        XCTAssertEqual(selection.chosen, ["a"])
    }

    func testShiftClickPicksTheRunEitherWay() {
        var selection = RowSelection()
        selection.click("d", .one, in: order)
        selection.click("b", .extend, in: order)
        XCTAssertEqual(selection.chosen, ["b", "c", "d"])
        // Measured from the same line again, not from the last Shift-click.
        selection.click("e", .extend, in: order)
        XCTAssertEqual(selection.chosen, ["d", "e"])
    }

    func testShiftClickWithNothingPickedIsAPlainClick() {
        var selection = RowSelection()
        selection.click("c", .extend, in: order)
        XCTAssertEqual(selection.chosen, ["c"])
    }

    func testLinesThatLeaveThePageAreForgotten() {
        var selection = RowSelection()
        selection.click("b", .one, in: order)
        selection.click("d", .toggle, in: order)
        selection.keep(only: ["a", "b", "c"])
        XCTAssertEqual(selection.chosen, ["b"])
        // "d" was where Shift measured from, and it's gone.
        selection.click("a", .extend, in: ["a", "b", "c"])
        XCTAssertEqual(selection.chosen, ["a"])
        selection.clear()
        XCTAssertTrue(selection.isEmpty)
    }

    func testARightClickIsAboutThePickedLinesOnlyWhenItIsOnOne() {
        var selection = RowSelection()
        selection.click("d", .one, in: order)
        selection.click("b", .toggle, in: order)
        XCTAssertEqual(selection.acting(on: "b", in: order), ["b", "d"])
        XCTAssertEqual(selection.acting(on: "e", in: order), ["e"])
    }

    func testSeveralSongsTravelInOneDrag() {
        let text = DraggedSongs.text(of: ["one", "two"])
        XCTAssertEqual(DraggedSongs.ids(in: [text, "three"]), ["one", "two", "three"])
        XCTAssertEqual(DraggedSongs.ids(in: ["only"]), ["only"])
    }
}

final class ChildProfileTests: XCTestCase {
    func testAllowingExplicitIsSavedWithTheProfileAndOffUntilSwitchedOn() throws {
        var list = ProfileList(firstNamed: "Me", libraryRoot: nil)
        let id = list.current.id
        XCTAssertFalse(list.current.allowsExplicit)
        list.setChild(id, true)
        list.setAllowsExplicit(id, true)
        let saved = try JSONEncoder().encode(list)
        let read = try JSONDecoder().decode(ProfileList.self, from: saved)
        XCTAssertTrue(read.current.isChild)
        XCTAssertTrue(read.current.allowsExplicit)
    }

    func testAProfileSavedBeforeTheSwitchReadsAsNotAllowing() throws {
        let old = Data(#"{"id":"default","name":"Me","isChild":true}"#.utf8)
        let profile = try JSONDecoder().decode(Profile.self, from: old)
        XCTAssertTrue(profile.isChild)
        XCTAssertFalse(profile.allowsExplicit)
    }

    func testASearchAnswerCarriesWhatWasLeftOut() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let said = Data(#"{"results":[],"kids_note":"No clean version was found for: Loud (Band)."}"#.utf8)
        XCTAssertEqual(
            try decoder.decode(SearchAnswer.self, from: said).kidsNote,
            "No clean version was found for: Loud (Band).")
        let plain = Data(#"{"results":[]}"#.utf8)
        XCTAssertNil(try decoder.decode(SearchAnswer.self, from: plain).kidsNote)
    }
}

final class AddonTests: XCTestCase {
    private let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    func testTheExplorePageOffersTheOwnersSectionsThatTheAddonCanFill() {
        let genres = ["Animation", "Gaming", "News & Politics", "Sports"]
        let sections = VideoExplore.sections(for: genres)
        XCTAssertEqual(
            sections.map(\.name),
            ["Channels", "Gaming", "News", "Sports", "Learning", "Podcasts", "Animation"])
        // The owner's five are searched afresh, whatever the add-on lists (its lists are old).
        XCTAssertEqual(sections.map(\.genre), [nil, nil, nil, nil, nil, nil, "Animation"])
        XCTAssertEqual(
            sections.map(\.search), [nil, "gaming", "news", "sports", "educational", "podcast", nil])
        XCTAssertEqual(VideoExplore.sections(for: []).count, 6)
    }

    func testAnAddonAndItsListsAreRead() throws {
        let said = Data(
            #"""
            {"addons":[{"id":"a","name":"A","version":"1","description":null,"address":"https://a.example/manifest.json",
            "base":"https://a.example","types":["channel"],"resources":[],
            "catalogs":[{"type":"channel","id":"top","name":null,"extra":[
              {"name":"genre","required":false,"options":["Gaming"]},{"name":"skip","required":false,"options":[]}]},
             {"type":"channel","id":"videos","name":null,"extra":[{"name":"search","required":true,"options":[]}]}]}]}
            """#.utf8)
        let addon = try XCTUnwrap(decoder.decode(AddonsAnswer.self, from: said).addons.first)
        // As Settings shows it, and moved about in the list.
        XCTAssertEqual(addon.version, "1")
        XCTAssertEqual(addon.offers, "Channels")
        XCTAssertEqual(addon.shownName, "A")
        XCTAssertEqual(Addon.order([addon, addon], moving: 0, by: 1), ["a", "a"])
        XCTAssertNil(Addon.order([addon], moving: 0, by: 1))
        XCTAssertNil(Addon.order([addon, addon], moving: 0, by: -1))
        XCTAssertEqual(addon.catalogs[0].genres, ["Gaming"])
        XCTAssertTrue(addon.catalogs[0].takes("skip"))
        XCTAssertFalse(addon.catalogs[0].takes("search"))
        XCTAssertTrue(addon.catalogs[1].needsSearch)
        // A list with genres offers "every genre" and asks with none until one is chosen.
        XCTAssertTrue(addon.catalogs[0].offersEveryGenre)
        XCTAssertNil(addon.catalogs[0].genre(chosen: ""))
        XCTAssertEqual(addon.catalogs[0].genre(chosen: "Gaming"), "Gaming")
        XCTAssertNil(addon.catalogs[0].genre(chosen: "Left over from another list"))
    }

    func testAListByYearStartsAtTheLatestYearAndHasNoEveryGenre() throws {
        let said = Data(
            #"""
            {"type":"movie","id":"year","name":null,"extra":[
              {"name":"genre","required":true,"options":["2026","2025","2024"]}]}
            """#.utf8)
        let byYear = try decoder.decode(Addon.Catalog.self, from: said)
        XCTAssertFalse(byYear.offersEveryGenre)
        XCTAssertEqual(byYear.genre(chosen: ""), "2026")
        XCTAssertEqual(byYear.genre(chosen: "2024"), "2024")
        XCTAssertEqual(byYear.genre(chosen: "Drama"), "2026")
    }

    func testAStreamSaysWhatItIsInPlainWords() throws {
        let said = Data(
            #"""
            {"sources":[{"addon_id":"a","addon":"A","streams":[
              {"kind":"torrent","name":"1080p","title":"1.5 GB","quality":"1080p","url":null,"video_id":null,
               "info_hash":"ab","file_index":0,"trackers":["udp://t.example:80"]},
              {"kind":"url","name":null,"title":null,"quality":"cam","url":"https://v.example/a.mp4","video_id":null,
               "info_hash":null,"file_index":null,"trackers":[]}]}],
             "problems":[{"addon":"B","message":"b.example took too long to answer."}]}
            """#.utf8)
        let answer = try decoder.decode(StreamsAnswer.self, from: said)
        let streams = answer.sources[0].streams
        XCTAssertEqual(streams.map(\.qualityLabel), ["1080p", "Cam"])
        XCTAssertEqual(streams.map(\.kindLabel), ["Torrent", "Direct"])
        XCTAssertEqual(answer.problems.first?.addon, "B")
    }

    func testAChannelsVideoIsReadWithItsDay() throws {
        let said = Data(
            #"""
            {"id":"yt_id:C","type":"channel","name":"C","poster":null,"poster_shape":"square","year":null,
             "rating":null,"genres":[],"description":null,"background":null,"logo":null,"runtime_min":null,
             "cast":[],"directors":[],"trailer_video_id":null,
             "videos":[{"id":"yt_id:C:abcdefghijk","title":"T","thumbnail":null,
               "released":"2023-10-25T21:00:15.000Z","season":null,"episode":null,"video_id":"abcdefghijk"}]}
            """#.utf8)
        let details = try decoder.decode(MediaDetails.self, from: said)
        XCTAssertEqual(details.videos.first?.day, "2023-10-25")
        XCTAssertEqual(details.videos.first?.videoId, "abcdefghijk")
    }
}

final class VideoFilesTests: XCTestCase {
    func testAConversionsProgressAndEndAreRead() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let going = try decoder.decode(
            FilmConversion.self,
            from: Data(
                #"{"converting": {"path": "/m/a.mkv", "progress": 0.416, "remakes_picture": true}, "last": null}"#
                    .utf8))
        XCTAssertEqual(going.converting?.remakesPicture, true)
        XCTAssertEqual(FilmConversion.note(progress: going.converting?.progress ?? 0), "Converting… 42%")
        XCTAssertEqual(FilmConversion.note(progress: 7), "Converting… 100%")
        let ended = try decoder.decode(
            FilmConversion.self,
            from: Data(#"{"converting": null, "last": {"path": "/m/a.mkv", "saved": "/m/a.mp4"}}"#.utf8))
        XCTAssertNil(ended.converting)
        XCTAssertEqual(ended.last?.saved, "/m/a.mp4")
        XCTAssertNil(ended.last?.error)
        let started = try decoder.decode(
            FilmConversion.Started.self, from: Data(#"{"needed": false, "remakes_picture": false}"#.utf8))
        XCTAssertFalse(started.needed)
    }

    func testOnlyVideoFilesAreListedByNameFromFoldersInsideToo() throws {
        let folder = FileManager.default.temporaryDirectory
            .appendingPathComponent("videofiles-\(UUID().uuidString)")
        let inner = folder.appendingPathComponent("Old Films")
        try FileManager.default.createDirectory(at: inner, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        for name in ["b film.MKV", "notes.txt", ".hidden.mp4", "Old Films/a film.avi"] {
            try Data("x".utf8).write(to: folder.appendingPathComponent(name))
        }
        let found = VideoFiles.inside(folder)
        XCTAssertEqual(found.map(\.name), ["a film", "b film"])
        XCTAssertEqual(found.map(\.kind), ["AVI", "MKV"])
        XCTAssertEqual(found.first?.bytes, 1)
        XCTAssertEqual(VideoFiles.inside(folder.appendingPathComponent("missing")), [])
        // One folder inside can be left out (Videos, in Movies: it has a page of its own).
        XCTAssertEqual(VideoFiles.inside(folder, leavingOut: inner).map(\.name), ["b film"])
        XCTAssertEqual(VideoFiles.inside(inner, leavingOut: inner).map(\.name), ["a film"])
    }
}

final class TorrentStatusTests: XCTestCase {
    func testHowAFilmFromATorrentIsGettingOnInOneLine() {
        XCTAssertEqual(
            TorrentStatus(state: "finding", peers: 0, bytesPerSecond: 0, progress: 0).line,
            "Finding the film…")
        XCTAssertEqual(
            TorrentStatus(state: "fetching", peers: 6, bytesPerSecond: 4_665_073, progress: 0.0948).line,
            "6 sources · 4.7 MB/s · 9% here")
        XCTAssertEqual(
            TorrentStatus(state: "fetching", peers: 1, bytesPerSecond: 0, progress: 0).line,
            "1 source · 0.0 MB/s · 0% here")
        XCTAssertEqual(
            TorrentStatus(state: "complete", peers: 3, bytesPerSecond: 0, progress: 1).line,
            "All of the film is here")
    }

    func testAFilmBeingKeptSaysHowFarItIsAndWhereItWent() {
        let playing = TorrentStatus(state: "fetching", peers: 6, bytesPerSecond: 0, progress: 0.3)
        XCTAssertNil(playing.keepLine)
        XCTAssertFalse(playing.isKeeping)
        let keeping = TorrentStatus(
            state: "fetching", peers: 6, bytesPerSecond: 5_200_000, progress: 0.34, keeping: true)
        XCTAssertTrue(keeping.isKeeping)
        XCTAssertEqual(
            keeping.keepLine, "Keeping: 34% here · 5.2 MB/s. It carries on while the app is open.")
        let kept = TorrentStatus(
            state: "complete", peers: 0, bytesPerSecond: 0, progress: 1, keeping: false,
            keptPath: "/Users/someone/Movies/The Kid (1921).mp4")
        XCTAssertFalse(kept.isKeeping)
        XCTAssertEqual(kept.keepLine, "Kept in your Movies folder as “The Kid (1921).mp4”")
        let failed = TorrentStatus(
            state: "complete", peers: 0, bytesPerSecond: 0, progress: 1, keepError: "No room.")
        XCTAssertEqual(failed.keepLine, "Couldn't keep it: No room.")
        let converting = TorrentStatus(
            state: "complete", peers: 0, bytesPerSecond: 0, progress: 1, keeping: true, converting: 0.42)
        XCTAssertEqual(
            converting.keepLine,
            "Converting it for phones and tablets: 42%. It carries on while the app is open.")
        XCTAssertTrue(converting.isKeeping)
        let noted = TorrentStatus(
            state: "complete", peers: 0, bytesPerSecond: 0, progress: 1, keeping: false,
            keptPath: "/Users/someone/Movies/Old.avi", keepNote: "It was kept as it arrived: no.")
        XCTAssertEqual(
            noted.keepLine, "Kept in your Movies folder as “Old.avi”. It was kept as it arrived: no.")
    }
}

final class VideoHitTests: XCTestCase {
    private func videos() throws -> [VideoHit] {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let said = Data(
            #"""
            {"videos":[
             {"video_id":"aaaaaaaaaaa","title":"A","channel":null,"channel_id":null,
              "duration_s":100,"views":50,"published":"2026-10-01","thumbnail":null},
             {"video_id":"bbbbbbbbbbb","title":"B","channel":null,"channel_id":null,
              "duration_s":null,"views":null,"published":null,"thumbnail":null},
             {"video_id":"ccccccccccc","title":"C","channel":null,"channel_id":null,
              "duration_s":900,"views":7000,"published":"2024-10-08","thumbnail":null},
             {"video_id":"ddddddddddd","title":"D","channel":null,"channel_id":null,
              "duration_s":100,"views":50,"published":"2026-10-07","thumbnail":null}]}
            """#.utf8)
        return try decoder.decode(VideosAnswer.self, from: said).videos
    }

    func testVideosArrangedEachWay() throws {
        let found = try videos()
        func order(_ sort: VideoSort) -> String { sort.arranged(found).map(\.title).joined() }
        XCTAssertEqual(order(.bestMatch), "ABCD")
        // One with nothing to go by is last; ties keep the search's order.
        XCTAssertEqual(order(.newest), "DACB")
        XCTAssertEqual(order(.oldest), "CADB")
        XCTAssertEqual(order(.mostViews), "CADB")
        XCTAssertEqual(order(.leastViews), "ADCB")
        XCTAssertEqual(order(.longest), "CADB")
        XCTAssertEqual(order(.shortest), "ADCB")
        XCTAssertEqual(VideoSort.allCases.map(\.title).count, 7)
    }

    func testAVideosAgeInWords() throws {
        let found = try videos()
        var day = DateComponents()
        (day.year, day.month, day.day, day.hour) = (2026, 10, 7, 12)
        day.timeZone = TimeZone(identifier: "UTC")
        let now = Calendar(identifier: .gregorian).date(from: day)!
        XCTAssertEqual(
            found.map { $0.age(now: now) }, ["6 days ago", "", "1 year ago", "today"])
    }

    func testAVideosLengthAndViewsInRoundNumbers() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let said = Data(
            #"""
            {"videos":[{"video_id":"abcdefghijk","title":"T","channel":"NASA","channel_id":"UCx",
              "duration_s":144,"views":1643948,"published":null,"thumbnail":null},
             {"video_id":"abcdefghijl","title":"Long","channel":null,"channel_id":null,
              "duration_s":3723,"views":null,"published":"2026-10-01","thumbnail":null}]}
            """#.utf8)
        let videos = try decoder.decode(VideosAnswer.self, from: said).videos
        XCTAssertEqual(videos.map(\.length), ["2:24", "1:02:03"])
        XCTAssertEqual(videos.map(\.viewsLabel), ["1.6M views", ""])
        XCTAssertEqual(videos[0].result.artists, ["NASA"])
        XCTAssertEqual(videos[1].result.artists, [])
        XCTAssertEqual(
            [950, 1000, 12_400, 999_999, 2_000_000, 2_100_000_000].map(VideoHit.round),
            ["950", "1K", "12K", "999K", "2M", "2.1B"])
    }

    func testAChannelFromAnAddonIsKnownByItsOwnId() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        func item(_ id: String) throws -> MediaItem {
            let said = #"{"id":"\#(id)","type":"channel","name":"N","poster":"https://p.example/a.jpg","poster_shape":"square","year":null,"rating":null,"genres":[]}"#
            return try decoder.decode(MediaItem.self, from: Data(said.utf8))
        }
        let channel = try XCTUnwrap(ChannelRef(item: item("yt_id:UCLA_DiR1FfKNvjuUpBHmylQ")))
        XCTAssertEqual(channel.channelId, "UCLA_DiR1FfKNvjuUpBHmylQ")
        XCTAssertEqual(channel.thumbnail, "https://p.example/a.jpg")
        XCTAssertNil(ChannelRef(item: try item("tt0012349")))
    }
}

final class WebPicturesTests: XCTestCase {
    func testAChannelsPictureIsAskedForAtTheSizeShown() {
        XCTAssertEqual(
            WebPictures.sized("https://yt3.ggpht.com/abc=s800-c-k-c0x00ffffff-no-rj", points: 200),
            "https://yt3.ggpht.com/abc=s400-c-k-c0x00ffffff-no-rj")
        XCTAssertEqual(
            WebPictures.sized("https://yt3.googleusercontent.com/abc=s900-c-k", points: 56),
            "https://yt3.googleusercontent.com/abc=s112-c-k")
        // Anything else is left as it is.
        XCTAssertEqual(
            WebPictures.sized("https://images.metahub.space/poster/small/tt1/img", points: 200),
            "https://images.metahub.space/poster/small/tt1/img")
        XCTAssertEqual(WebPictures.sized("https://yt3.ggpht.com/no-size", points: 200), "https://yt3.ggpht.com/no-size")
        XCTAssertNil(WebPictures.sized(nil, points: 200))
    }
}

final class WordingTests: XCTestCase {
    func testTheEnginesSentencesArePutIntoTheAppsWords() {
        XCTAssertEqual(
            Wording.plain("YouTube is slowing us down; try again after 3:10am."),
            "The service is slowing us down; try again after 3:10am.")
        XCTAssertEqual(
            Wording.plain("Not found. YouTube Music has no such song on YouTube."),
            "Not found. The music service has no such song on the service.")
        XCTAssertEqual(Wording.plain("Nothing about it"), "Nothing about it")
    }

    func testOnlySentencesAreChangedNeverANameOrAnId() throws {
        let answer: [String: Any] = [
            "note": "YouTube Music gave 3 radios.",
            "picks": [["title": "YouTube Poop", "why": "Like a song on YouTube Music"]],
            "channel": "YouTube Movies",
        ]
        let plain = try XCTUnwrap(Wording.plain(answer: answer) as? [String: Any])
        XCTAssertEqual(plain["note"] as? String, "The music service gave 3 radios.")
        XCTAssertEqual(plain["channel"] as? String, "YouTube Movies")
        let pick = try XCTUnwrap((plain["picks"] as? [[String: Any]])?.first)
        XCTAssertEqual(pick["title"] as? String, "YouTube Poop")
        XCTAssertEqual(pick["why"] as? String, "Like a song on the music service")
    }
}

final class FilmExtrasTests: XCTestCase {
    func testATracksNameInAMenu() {
        XCTAssertEqual(FilmTrack.label(number: 2, title: nil, language: nil), "Track 2")
        XCTAssertEqual(FilmTrack.label(number: 1, title: "Commentary", language: "und"), "Commentary")
        XCTAssertEqual(FilmTrack.label(number: 1, title: " ", language: "zz-nothing"), "zz-nothing")
        let english = FilmTrack(id: 3, kind: .subtitles, title: "SDH", language: "en", selected: true)
        XCTAssertTrue(english.label.hasSuffix(" · SDH") && english.label.count > 6)
        XCTAssertEqual(FilmTrack.Kind(rawValue: "audio"), .sound)
        XCTAssertNil(FilmTrack.Kind(rawValue: "video"))
    }

    func testAFilmOpensWhereItWasLeft() {
        var places = FilmPositions()
        let start = Date(timeIntervalSince1970: 1_000_000)
        places.watched("a", to: 32, of: 6000, now: start)
        XCTAssertEqual(places.place(of: "a"), 32)
        XCTAssertNil(places.place(of: "b"))
        // Hardly begun, in its credits, or of no known length: it starts again.
        places.watched("b", to: 5, of: 6000)
        places.watched("c", to: 5950, of: 6000)
        places.watched("d", to: 300, of: 0)
        XCTAssertEqual(places.places.keys.sorted(), ["a"])
        places.watched("a", to: 5990, of: 6000)
        XCTAssertNil(places.place(of: "a"))
        // The film watched longest ago is forgotten first.
        for number in 0...FilmPositions.most {
            places.watched("f\(number)", to: 100, of: 6000, now: start.addingTimeInterval(Double(number)))
        }
        XCTAssertEqual(places.places.count, FilmPositions.most)
        XCTAssertNil(places.place(of: "f0"))
        XCTAssertEqual(places.place(of: "f1"), 100)
        // It survives being put away and read back.
        let defaults = UserDefaults(suiteName: "film-positions-test")!
        defaults.removePersistentDomain(forName: "film-positions-test")
        places.save(in: defaults)
        XCTAssertEqual(FilmPositions.saved(in: defaults), places)
        defaults.removePersistentDomain(forName: "film-positions-test")
    }
}

final class SeriesTests: XCTestCase {
    func testASeriesSeasonsAndEpisodes() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        func video(_ season: Int, _ episode: Int) -> String {
            #"{"id":"tt1:\#(season):\#(episode)","title":"E\#(episode)","thumbnail":null,"released":"2026-10-01T11:00:00.000Z","season":\#(season),"episode":\#(episode),"overview":"What happens","video_id":null}"#
        }
        let said = #"{"id":"tt1","type":"series","name":"East of Eden","poster":null,"year":"2026","rating":null,"genres":[],"description":null,"background":null,"logo":null,"runtime_min":59,"cast":[],"directors":[],"trailer_video_id":null,"videos":[\#([video(2, 1), video(0, 1), video(1, 2), video(1, 1)].joined(separator: ","))]}"#
        let series = try decoder.decode(MediaDetails.self, from: Data(said.utf8))
        // In order, with the specials last.
        XCTAssertEqual(series.seasons, [1, 2, 0])
        XCTAssertEqual(series.seasons.map(MediaDetails.seasonName), ["Season 1", "Season 2", "Specials"])
        let first = series.episodes(in: 1)
        XCTAssertEqual(first.map(\.id), ["tt1:1:1", "tt1:1:2"])
        XCTAssertEqual(first[1].number, "S1 E2")
        XCTAssertEqual(first[1].name(in: series.name), "East of Eden S01E02")
        XCTAssertEqual(first[1].overview, "What happens")
        XCTAssertEqual(first[1].day, "2026-10-01")
        XCTAssertTrue(series.episodes(in: 9).isEmpty)
    }

    func testOnlyListsThatNeedNothingAreOffered() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        func list(_ id: String, _ extra: String) -> String {
            #"{"type":"series","id":"\#(id)","name":null,"extra":[\#(extra)]}"#
        }
        let all = [
            list("top", #"{"name":"genre","required":false,"options":[]},{"name":"search","required":false,"options":[]}"#),
            list("year", #"{"name":"genre","required":true,"options":["2026"]}"#),
            list("last-videos", #"{"name":"lastVideosIds","required":false,"options":[]}"#),
            list("found", #"{"name":"search","required":true,"options":[]}"#),
        ].joined(separator: ",")
        let lists = try decoder.decode([Addon.Catalog].self, from: Data("[\(all)]".utf8))
        XCTAssertEqual(lists.map(\.canBeBrowsed), [true, true, false, false])
    }
}

final class HomeTests: XCTestCase {
    private func item(_ id: String, _ type: String, _ genres: [String]) -> MediaItem {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let list = genres.map { "\"\($0)\"" }.joined(separator: ",")
        let said = #"{"id":"\#(id)","type":"\#(type)","name":"N \#(id)","poster":null,"poster_shape":"poster","year":null,"rating":null,"genres":[\#(list)]}"#
        return try! decoder.decode(MediaItem.self, from: Data(said.utf8))
    }

    func testTheRowsOnOfferAndTheOwnersChoiceOfThem() {
        let all = HomeSection.all(movieGenres: ["Action", "Comedy"], seriesGenres: ["Crime"])
        XCTAssertEqual(Set(all.map(\.id)).count, all.count)  // no two the same
        XCTAssertTrue(all.contains { $0.id == "movies.genre.Action" && $0.title == "Action Movies" })
        XCTAssertTrue(all.contains { $0.id == "series.genre.Crime" && $0.genre == "Crime" })
        XCTAssertEqual(all.first { $0.id == "series.continue" }?.kind, "continue")
        XCTAssertEqual(all.first { $0.id == "movies.genre.Action" }?.kind, "genre")
        // The page starts as the owner drew it, and every row of that is on offer.
        var layout = HomeLayout()
        XCTAssertEqual(layout.sections(from: all).map(\.id), HomeSection.standard)
        layout.set("movies.genre.Action", shown: true)
        layout.set("movies.genre.Action", shown: true)  // once is once
        layout.set("songs.favourites", shown: false)
        layout.move("movies.genre.Action", by: -1)
        layout.move("songs.recommended", by: -1)  // already first: stays
        XCTAssertEqual(layout.shown.first, "songs.recommended")
        XCTAssertEqual(layout.shown.suffix(2), ["movies.genre.Action", "series.popular"])
        // A search by two genres, added from its Finder, is a row too.
        let custom = HomeSection.custom(.series, "Documentary", "Crime")
        XCTAssertEqual(custom.title, "Custom Search Documentary + Crime")
        XCTAssertEqual(custom.kind, "custom")
        XCTAssertEqual([custom.genre, custom.also], ["Documentary", "Crime"])
        XCTAssertEqual(HomeSection(customID: custom.id), custom)
        XCTAssertEqual(
            HomeSection(customID: HomeSection.custom(.movies, "Sci-Fi", "Film & TV").id)?.also, "Film & TV")
        for wrong in ["movies.genre.Action", "songs.custom.A|and|B", "movies.custom.A", "movies.custom.|and|B"] {
            XCTAssertNil(HomeSection(customID: wrong))
        }
        layout.set(custom.id, shown: true)
        XCTAssertEqual(layout.sections(from: all).last, custom)
        // Dragged into another order; a chosen row that isn't listed just now stays, last.
        var dragged = HomeLayout(["a", "b", "c", "d"])
        dragged.arrange(["c", "a", "c", "x", "b"])
        XCTAssertEqual(dragged.shown, ["c", "a", "b", "d"])
        layout.set(custom.id, shown: false)
        // A row that's no longer on offer (its genre went from the add-on) isn't drawn.
        XCTAssertFalse(
            layout.sections(from: HomeSection.all(movieGenres: [], seriesGenres: []))
                .contains { $0.id == "movies.genre.Action" })
        let defaults = UserDefaults(suiteName: "home-test")!
        defaults.removePersistentDomain(forName: "home-test")
        XCTAssertEqual(HomeLayout.saved(in: defaults), HomeLayout())
        layout.save(in: defaults)
        XCTAssertEqual(HomeLayout.saved(in: defaults), layout)
        defaults.removePersistentDomain(forName: "home-test")
    }

    func testARowShowsOnlyWholeCards() {
        // Five cards of 122 with 16 between need 674; a point less and it's four.
        XCTAssertEqual(HomePaging.fitting(674, card: 122, gap: 16), 5)
        XCTAssertEqual(HomePaging.fitting(673, card: 122, gap: 16), 4)
        XCTAssertEqual(HomePaging.fitting(1400, card: 122, gap: 16), 10)
        XCTAssertEqual(HomePaging.fitting(50, card: 122, gap: 16), 1)  // never none
        XCTAssertEqual(HomePaging.fitting(-40, card: 122, gap: 16), 1)
    }

    func testWhatsBeenWatched() {
        var history = WatchHistory()
        let start = Date(timeIntervalSince1970: 2_000_000)
        history.watched(.init(id: "v1", kind: .video, name: "One", detail: "Channel", at: start))
        history.watched(.init(id: "m1", kind: .movie, name: "Film", item: item("m1", "movie", ["Drama"])))
        history.place("v1", seconds: 300, length: 3000)
        XCTAssertEqual(history.recent(.video).map(\.id), ["v1"])
        XCTAssertEqual(history.unfinished(.video).first?.progress, 0.1)
        XCTAssertTrue(history.unfinished(.movie).isEmpty)  // not begun
        // Started again from a list, it keeps its place and comes to the front.
        history.watched(.init(id: "v1", kind: .video, name: "One"))
        XCTAssertEqual(history.entries.map(\.id), ["v1", "m1"])
        XCTAssertEqual(history.entries[0].seconds, 300)
        // Hardly begun, or nearly over: not something to carry on with.
        history.place("v1", seconds: 5, length: 3000)
        XCTAssertTrue(history.unfinished(.video).isEmpty)
        history.place("v1", seconds: 2990, length: 3000)
        XCTAssertTrue(history.unfinished(.video).isEmpty)
        // A short video's last stretch is a share of it, not a minute and a half.
        history.place("v1", seconds: 60, length: 100)
        XCTAssertEqual(history.unfinished(.video).count, 1)
        history.forget("v1")
        XCTAssertEqual(history.entries.map(\.id), ["m1"])
        for number in 0...WatchHistory.most { history.watched(.init(id: "x\(number)", kind: .video, name: "X")) }
        XCTAssertEqual(history.entries.count, WatchHistory.most)
        let defaults = UserDefaults(suiteName: "history-test")!
        defaults.removePersistentDomain(forName: "history-test")
        history.save(in: defaults)
        XCTAssertEqual(WatchHistory.saved(in: defaults).entries.count, WatchHistory.most)
        defaults.removePersistentDomain(forName: "history-test")
    }

    func testFavouriteFilmsAndWhatTheySuggest() {
        var favourites = MediaFavourites()
        let (film, show) = (item("m1", "movie", ["Crime", "Drama"]), item("s1", "series", ["Crime"]))
        favourites.toggle(film)
        favourites.toggle(show)
        XCTAssertTrue(favourites.contains(film))
        XCTAssertEqual(favourites.of(type: "movie").map(\.id), ["m1"])
        XCTAssertEqual(MediaFavourites.leadingGenre(favourites.items), "Crime")
        XCTAssertEqual(MediaFavourites.leadingGenre([film]), "Crime")  // a tie: first in the alphabet
        XCTAssertNil(MediaFavourites.leadingGenre([]))
        let defaults = UserDefaults(suiteName: "media-favourites-test")!
        defaults.removePersistentDomain(forName: "media-favourites-test")
        favourites.save(in: defaults)
        XCTAssertEqual(MediaFavourites.saved(in: defaults), favourites)
        favourites.toggle(film)
        XCTAssertFalse(favourites.contains(film))
        defaults.removePersistentDomain(forName: "media-favourites-test")
    }
}
