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
