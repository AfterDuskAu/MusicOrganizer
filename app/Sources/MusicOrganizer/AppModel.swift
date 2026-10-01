import AppKit
import MusicOrganizerKit
import Observation

/// The app's state: the engine, the library it has open, and the player.
///
/// The app never writes inside the library. It reads audio and cover files to play and
/// show them; every change goes through the engine (CLAUDE.md rule 3).
@MainActor
@Observable
final class AppModel {
    enum Phase: Equatable {
        case starting
        case needsLibrary
        case loading
        case ready
        case failed(String)
    }

    private(set) var phase: Phase = .starting
    /// The main library's lists. Without the owner's downloads, if they're kept separate.
    private(set) var library = Library.empty
    /// Every song, wherever it's shown: for favourites, playlists and "is it mine already?".
    private(set) var everything = Library.empty
    /// Songs and videos downloaded from YouTube Music, newest first (Discover → Downloads).
    private(set) var downloaded: [Track] = []
    /// Saved videos (Library → Videos). Empty while downloads are kept under Discover.
    private(set) var videos: [Track] = []
    /// Settings → General: downloads stay under Discover until the owner says otherwise.
    var keepDownloadsSeparate = UserDefaults.standard.object(forKey: "keepDownloadsSeparate")
        as? Bool ?? true
    {
        didSet {
            UserDefaults.standard.set(keepDownloadsSeparate, forKey: "keepDownloadsSeparate")
            Task { await arrange(everything.tracks) }
        }
    }
    /// Goes up whenever the songs change, so lists know to work themselves out again.
    private(set) var libraryVersion = 0
    /// Goes up whenever a play is counted.
    private(set) var playsVersion = 0
    private(set) var engineSettings: EngineSettings?
    private(set) var lyricsSearch: LyricsSearch?

    enum LyricsSearch: Equatable {
        case working(done: Int, total: Int)
        case finished(String)
    }
    private(set) var status: LibraryStatus?
    private(set) var root: URL?
    private(set) var engineVersion: String?
    private(set) var listening = Listening.empty
    private(set) var favourites = Set<String>()
    /// Something to tell the owner (a change the engine refused), shown as an alert.
    var notice: String?
    /// A name being asked for: a new playlist, or a new name for one.
    var namePrompt: NamePrompt?
    /// The song whose details are being edited by hand.
    var editing: Track?
    /// Something to explain to the owner that isn't a failure (a feature that's coming).
    var info: Info?

    struct Info {
        let title: String
        let text: String
    }
    /// The video has the whole screen (`setVideoFullScreen`).
    private(set) var videoFullScreen = false
    /// The app put its window into macOS's full screen for the video, so it takes it
    /// out again afterwards. Kept here, not in the view: the view is rebuilt as the
    /// window changes, and forgot.
    @ObservationIgnored private var tookTheScreen = false

    // The YouTube Music search page.
    var youtubeQuery = ""
    private(set) var youtubeResults: [SearchResult] = []
    private(set) var youtubeSearching = false
    /// How many results the last search asked for, and whether asking for more may find more.
    private(set) var youtubeLimit = AppModel.youtubeStep
    private(set) var youtubeHasMore = false
    static let youtubeStep = 25
    static let youtubeMost = 100
    private(set) var youtubeProblem: String?
    /// The owner's downloads that haven't arrived: waiting, downloading, or ended without
    /// the song. The engine's own list, so it's still right after the app restarts.
    private(set) var pending: [PendingDownload] = []
    /// Asked for a moment ago: the engine's list doesn't have them yet (by video id).
    private(set) var starting = Set<String>()
    /// Downloads that couldn't even be queued, and why (by video id).
    private(set) var startProblems: [String: String] = [:]
    @ObservationIgnored private var watchingDownloads = false

    enum DownloadState: Equatable {
        case working
        case failed(String)
    }
    var searchText = ""
    /// The search field is tucked away behind a magnifying glass until it's wanted.
    var searching = false {
        didSet { if !searching { searchText = "" } }
    }

    let player = Player()
    let lyrics = LyricsModel()
    /// Discover → What's New and Discover → Find: each keeps its own picks.
    let whatsNew = DiscoverPage(named: "whatsNew")
    let find = DiscoverPage(named: "find")
    /// Several downloads planned and waiting for the owner's yes (Download Selected).
    var batch: BatchDownload?

    struct BatchDownload {
        let planId: String
        let videoIds: [String]
        let count: Int
        let minutes: Int
        let days: Int
    }

    @ObservationIgnored private var engine: EngineProcess?
    @ObservationIgnored private var stopping = false
    @ObservationIgnored private var keyMonitor: Any?
    /// While set, the space bar does this instead of play/pause (tap-along lyrics).
    @ObservationIgnored var spaceBar: (() -> Void)?
    private static let rootKey = "libraryRoot"

    init() {
        for page in [whatsNew, find] {
            page.ask = { [weak self] seeds, count, shuffle, name in
                guard let self else { throw CancellationError() }
                return try await self.suggest(seeds, count, shuffle, name)
            }
        }
        player.onTrackChange = { [weak self] track in self?.showLyrics(for: track) }
        player.onTick = { [weak self] time in
            guard let self else { return }
            // A music video with an intro isn't the song second for second, so the
            // song's timed lyrics are shown without a line lit up; lyrics found for the
            // video itself are timed to it.
            let videoId = player.video?.source.videoId
            let inTime = player.lyricsInTime || (videoId != nil && lyrics.forVideo == videoId)
            if lyrics.timed != inTime { lyrics.timed = inTime }
            lyrics.follow(time)
        }
        player.onVideoChange = { [weak self] showing in self?.showLyrics(forVideo: showing) }
        player.onFinished = { [weak self] track in self?.countPlay(of: track) }
        player.findStream = { [weak self] videoId in
            guard let connection = self?.engine?.connection else {
                throw RPCError(code: RPCError.closed, message: "The engine isn't running.")
            }
            let found = try await connection.call(
                "youtube.stream", ["video_id": videoId], as: StreamAnswer.self)
            guard let url = URL(string: found.url) else {
                throw RPCError(code: 0, message: "YouTube's answer couldn't be read.")
            }
            return (url, found.httpHeaders, found.durationS)
        }
        player.findVideo = { [weak self] track in
            guard let connection = self?.engine?.connection else {
                throw RPCError(code: RPCError.closed, message: "The engine isn't running.")
            }
            // A song with no artist to its name can't be told from others of its title.
            guard let artist = track.artist ?? track.albumArtist else { return nil }
            var asked: [String: Any] = ["title": track.title, "artist": artist]
            // A library song's file says which version it is (a rip named "Song R" is a
            // remix, whatever its title says), so a remix never gets the original's video.
            if track.videoId == nil { asked["path"] = track.path }
            let found = try await connection.call("youtube.video", asked, as: VideoAnswer.self)
            return SongVideo(found)
        }
    }

    // MARK: starting and stopping

    func start() async {
        installSpaceBar()
        NotificationCenter.default.addObserver(
            forName: NSApplication.willTerminateNotification, object: nil, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.stopEngine() }
        }
        // Leaving macOS's full screen by its own means (the green button, the menu)
        // puts the video back in the page as well.
        NotificationCenter.default.addObserver(
            forName: NSWindow.didExitFullScreenNotification, object: nil, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated {
                self?.tookTheScreen = false
                self?.videoFullScreen = false
            }
        }
        await connect()
    }

    private func connect() async {
        phase = .starting
        guard let executable = EngineProcess.locate() else {
            phase = .failed(
                "Music Organizer can't find its engine. Build the app again with "
                    + "scripts/build_app.sh from the project folder.")
            return
        }
        do {
            let engine = try EngineProcess(executable: executable)
            self.engine = engine
            stopping = false
            engine.connection.start(
                onNotification: { [weak self] method, params in
                    if method == "discover.progress" {
                        let page = params["token"] as? String
                        let (done, of) = (params["done"] as? Int ?? 0, params["of"] as? Int ?? 0)
                        Task { @MainActor in self?.discoverSaid(page, done: done, of: of) }
                        return
                    }
                    Task { @MainActor in await self?.engineSaid(method) }
                },
                onClose: { [weak self, weak engine] in
                    Task { @MainActor in self?.engineClosed(engine) }
                })
            let hello = try await engine.connection.call(
                "engine.hello", ["client": "mac-app", "client_version": appVersion],
                as: Hello.self)
            engineVersion = hello.engineVersion
        } catch {
            phase = .failed("The engine didn't start: \(error.localizedDescription)")
            return
        }
        if let saved = UserDefaults.standard.string(forKey: Self.rootKey) {
            await open(URL(fileURLWithPath: saved))
        } else {
            phase = .needsLibrary
        }
    }

    private func stopEngine() {
        stopping = true
        engine?.stop()
        engine = nil
    }

    private func engineClosed(_ closed: EngineProcess?) {
        guard !stopping, let closed, closed === engine else { return }
        engine = nil
        let detail = closed.lastErrors.split(whereSeparator: \.isNewline).last.map(String.init)
        phase = .failed("The engine stopped unexpectedly." + (detail.map { "\n\($0)" } ?? ""))
    }

    func retry() {
        Task {
            stopEngine()
            await connect()
        }
    }

    // MARK: the library

    func chooseLibrary() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.message = "Choose your Music Organizer library folder (the one with Music inside)."
        panel.prompt = "Open Library"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        UserDefaults.standard.set(url.path, forKey: Self.rootKey)
        player.stop()
        retry()  // an engine serves one library, so a new choice starts a new engine
    }

    private func open(_ folder: URL) async {
        guard let connection = engine?.connection else { return }
        phase = .loading
        do {
            _ = try await connection.call("library.open", ["root": folder.path])
            try await load()
            phase = .ready
            // Downloads asked for before the app was last closed carry on in the engine.
            await refreshDownloads()
            watchDownloads()
        } catch {
            phase = .failed(error.localizedDescription)
        }
    }

    func reload() async {
        guard phase == .ready else { return }
        do { try await load() } catch { phase = .failed(error.localizedDescription) }
    }

    private func load() async throws {
        guard let connection = engine?.connection else { return }
        let list = try await connection.call("library.tracks", as: TrackList.self)
        root = URL(fileURLWithPath: list.root)
        player.root = root
        // Before the songs are sorted: it says which downloads were moved into the library.
        if let found = try? await connection.call("listening.get", as: Listening.self) {
            listening = found
            favourites = Set(found.favourites)
            playsVersion += 1
        }
        await arrange(list.tracks)
        status = try? await connection.call("library.status", as: LibraryStatus.self)
    }

    /// Sort the songs into what each part of the app shows.
    private func arrange(_ tracks: [Track]) async {
        let separate = keepDownloadsSeparate
        let moved = Set(listening.library ?? [])
        let sorted = await Task.detached {
            () -> (all: Library, main: Library, downloads: [Track], videos: [Track]) in
            // A download is in the main library once the owner moved it there, or when
            // downloads aren't kept apart at all.
            let inMain: (Track) -> Bool = {
                !$0.isDownload || !separate || ($0.trackId.map(moved.contains) ?? false)
            }
            return (
                Library(tracks: tracks),
                // A video is never among the songs, albums or artists: it has its own list.
                Library(tracks: tracks.filter { !$0.isVideo && inMain($0) }),
                tracks.filter { $0.isDownload && !(separate && inMain($0)) }
                    .sorted { ($0.acquired ?? "", $1.path) > ($1.acquired ?? "", $0.path) },
                tracks.filter { $0.isVideo && inMain($0) }
                    .sorted { (sortKey($0.title), $0.path) < (sortKey($1.title), $1.path) }
            )
        }.value
        everything = sorted.all
        library = sorted.main
        libraryVersion += 1
        downloaded = sorted.downloads
        videos = sorted.videos
    }

    /// Whether a downloaded song or video has been moved into the main library.
    func isMoved(_ track: Track) -> Bool {
        track.trackId.map { (listening.library ?? []).contains($0) } ?? false
    }

    /// Move downloads into the main library's lists, or back under Downloads. No file
    /// moves: it's where they're listed.
    func moveDownloads(_ trackIds: [String], toLibrary: Bool) {
        let ids = trackIds.filter { !$0.isEmpty }
        guard !ids.isEmpty, let connection = engine?.connection else { return }
        Task {
            do {
                let moved = try await connection.call(
                    "listening.move",
                    ["track_ids": ids, "to": toLibrary ? "library" : "downloads"],
                    as: MovedAnswer.self)
                listening.library = moved.library
                await arrange(everything.tracks)
            } catch {
                notice = error.localizedDescription
            }
        }
    }

    private func discoverSaid(_ name: String?, done: Int, of: Int) {
        for page in [whatsNew, find] where page.name == name {
            page.progress(done: done, of: of)
        }
    }

    private func engineSaid(_ method: String) async {
        switch method {
        case "library.changed": await reload()
        case "review.changed":
            if let connection = engine?.connection {
                status = try? await connection.call("library.status", as: LibraryStatus.self)
            }
        default: break
        }
    }

    // MARK: what the screens show

    var songs: [Track] { library.search(searchText) }
    var albums: [Album] { library.albums(matching: searchText) }
    var artists: [Artist] { library.artists(matching: searchText) }

    func songs(in collection: [Track]) -> [Track] { library.filter(collection, searchText) }

    // MARK: favourites, play counts, playlists (kept by the engine in the library)

    func isFavourite(_ track: Track) -> Bool {
        track.trackId.map(favourites.contains) ?? false
    }

    func playCount(_ track: Track) -> Int {
        track.trackId.flatMap { listening.plays[$0]?.count } ?? 0
    }

    func setFavourite(_ tracks: [Track], _ on: Bool) {
        let ids = tracks.compactMap(\.trackId)
        for id in ids {  // shown at once; the engine's answer then settles it
            if on { favourites.insert(id) } else { favourites.remove(id) }
        }
        change { connection in
            for id in ids {
                let answer = try await connection.call(
                    "listening.favourite", ["track_id": id, "on": on], as: FavouritesAnswer.self)
                self.listening.favourites = answer.favourites
                self.favourites = Set(answer.favourites)
            }
        }
    }

    private func countPlay(of track: Track) {
        guard let id = track.trackId else { return }
        change { connection in
            let count = try await connection.call(
                "listening.played", ["track_id": id], as: PlayCount.self)
            self.listening.plays[id] = count
            self.playsVersion += 1
        }
    }

    func playlist(_ id: String) -> Playlist? { listening.playlists.first { $0.id == id } }

    func tracks(in playlist: Playlist) -> [Track] { everything.tracks(withIDs: playlist.trackIds) }

    var favouriteSongs: [Track] { everything.tracks(withIDs: listening.favourites) }

    /// Ask for a name, then make the playlist (with `tracks` in it, if any).
    func newPlaylist(with tracks: [Track] = []) {
        namePrompt = NamePrompt(title: "New Playlist", button: "Create", name: "") { name in
            let ids = tracks.compactMap(\.trackId)
            self.change { connection in
                var answer = try await connection.call(
                    "playlist.create", ["name": name], as: PlaylistsAnswer.self)
                if !ids.isEmpty, let made = answer.playlists.last {
                    answer = try await connection.call(
                        "playlist.set_tracks", ["playlist_id": made.id, "track_ids": ids],
                        as: PlaylistsAnswer.self)
                }
                self.listening.playlists = answer.playlists
            }
        }
    }

    func rename(_ playlist: Playlist) {
        namePrompt = NamePrompt(title: "Rename Playlist", button: "Rename", name: playlist.name) {
            name in
            self.playlistCall("playlist.rename", ["playlist_id": playlist.id, "name": name])
        }
    }

    func delete(_ playlist: Playlist) {
        playlistCall("playlist.delete", ["playlist_id": playlist.id])
    }

    func add(_ tracks: [Track], to playlist: Playlist) {
        setTracks(playlist.trackIds + tracks.compactMap(\.trackId), of: playlist)
    }

    func setTracks(_ ids: [String], of playlist: Playlist) {
        if let index = listening.playlists.firstIndex(where: { $0.id == playlist.id }) {
            listening.playlists[index].trackIds = ids
        }
        playlistCall("playlist.set_tracks", ["playlist_id": playlist.id, "track_ids": ids])
    }

    private func playlistCall(_ method: String, _ params: [String: Any]) {
        change { connection in
            let answer = try await connection.call(method, params, as: PlaylistsAnswer.self)
            self.listening.playlists = answer.playlists
        }
    }

    /// Ask the engine for a change; if it says no, say why and show what's really saved.
    private func change(_ work: @escaping @MainActor (RPCConnection) async throws -> Void) {
        guard let connection = engine?.connection else { return }
        Task {
            do {
                try await work(connection)
            } catch {
                notice = error.localizedDescription
                if let found = try? await connection.call("listening.get", as: Listening.self) {
                    listening = found
                    favourites = Set(found.favourites)
                }
            }
        }
    }

    // MARK: YouTube Music: search, play, download when asked

    func searchYouTube() {
        runYouTubeSearch(limit: Self.youtubeStep)
    }

    /// The same search again, asking for the next lot of results as well.
    func moreFromYouTube() {
        runYouTubeSearch(limit: min(youtubeLimit + Self.youtubeStep, Self.youtubeMost))
    }

    private func runYouTubeSearch(limit: Int) {
        let query = youtubeQuery.trimmingCharacters(in: .whitespaces)
        guard !query.isEmpty, !youtubeSearching, let connection = engine?.connection else { return }
        youtubeSearching = true
        youtubeProblem = nil
        Task {
            do {
                let found = try await connection.call(
                    "search.ytmusic", ["query": query, "limit": limit], as: SearchAnswer.self)
                youtubeResults = found.results
                youtubeLimit = limit
                // A full page means there may be more; YouTube Music is asked for at most 100.
                youtubeHasMore = found.results.count >= limit && limit < Self.youtubeMost
                if found.results.isEmpty { youtubeProblem = "Nothing found for “\(query)”." }
            } catch {
                youtubeProblem = error.localizedDescription
            }
            youtubeSearching = false
        }
    }

    /// Download one song into the library. Only ever called by the owner's click.
    func download(_ result: SearchResult) { downloadSong(result.videoId) }

    // MARK: Discover: picks, and downloading them

    /// Ask the engine for songs the owner doesn't have. Takes seconds: the engine asks
    /// YouTube Music for several radios, at its usual careful pace.
    private func suggest(
        _ seeds: [DiscoverSeed], _ count: Int, _ shuffle: String, _ page: String
    ) async throws -> DiscoverAnswer {
        guard let connection = engine?.connection else {
            throw RPCError(code: 0, message: "The engine isn't running.")
        }
        return try await connection.call(
            "discover.suggest",
            ["seeds": seeds.map(\.params), "count": count, "shuffle": shuffle, "token": page],
            as: DiscoverAnswer.self)
    }

    /// Download one of Discover's picks. The engine is handed the pick back as it gave
    /// it, so it doesn't look the song up a second time.
    func download(_ pick: DiscoverPick) {
        startDownload(
            pick.videoId, ["video_ids": [pick.videoId], "candidates": [pick.candidate]])
    }

    /// Whether a pick could still be downloaded: not in the library, not on its way.
    func canDownload(_ pick: DiscoverPick) -> Bool {
        !everything.videoIDs.contains(pick.videoId) && downloadState(of: pick.videoId) != .working
    }

    /// Several picks at once. It's a batch, so the plan comes first: how many and how
    /// long. Nothing is queued until the owner says yes (`confirmBatch`).
    func planDownloads(_ picks: [DiscoverPick]) {
        let wanted = picks.filter(canDownload)
        guard !wanted.isEmpty, let connection = engine?.connection else { return }
        Task {
            do {
                let plan = try await connection.call(
                    "plan.create",
                    [
                        "kind": "download",
                        "options": [
                            "video_ids": wanted.map(\.videoId),
                            "candidates": wanted.map(\.candidate),
                        ],
                    ], as: PlanAnswer.self)
                guard plan.summary.operations > 0 else { throw Self.nothingToDo(plan) }
                batch = BatchDownload(
                    planId: plan.planId, videoIds: wanted.map(\.videoId),
                    count: plan.summary.downloads ?? plan.summary.operations,
                    minutes: plan.summary.estMinutes ?? 0, days: plan.summary.days ?? 1)
            } catch {
                notice = error.localizedDescription
            }
        }
    }

    /// The owner said yes: the planned downloads go to the queue.
    func confirmBatch() {
        guard let batch, let connection = engine?.connection else { return }
        self.batch = nil
        starting.formUnion(batch.videoIds)
        Task {
            do {
                _ = try await connection.call(
                    "plan.apply", ["plan_id": batch.planId], as: BatchAnswer.self)
            } catch {
                notice = error.localizedDescription
            }
            await refreshDownloads()
            starting.subtract(batch.videoIds)
            watchDownloads()
        }
    }

    /// Download a YouTube Music song (by its id) into the library.
    func downloadSong(_ videoId: String) {
        startDownload(videoId, ["video_ids": [videoId]])
    }

    /// What a Download or Save Video button should say about this video id.
    func downloadState(of videoId: String) -> DownloadState? {
        if starting.contains(videoId) { return .working }
        if let found = pending.first(where: { $0.videoId == videoId }) {
            return found.isActive ? .working : .failed(found.problem ?? "It didn't work.")
        }
        return startProblems[videoId].map(DownloadState.failed)
    }

    /// Queue a download and return at once: it then shows at the top of Discover →
    /// Downloads until it arrives.
    private func startDownload(_ videoId: String, _ options: [String: Any]) {
        guard downloadState(of: videoId) != .working, let connection = engine?.connection
        else { return }
        starting.insert(videoId)
        startProblems[videoId] = nil
        Task {
            do {
                // An earlier try that ended badly leaves the list: this one takes its place.
                for old in pending where old.videoId == videoId && !old.isActive {
                    _ = try? await connection.call(
                        "queue.dismiss", ["job_id": old.jobId], as: DownloadsAnswer.self)
                }
                let plan = try await connection.call(
                    "plan.create", ["kind": "download", "options": options], as: PlanAnswer.self)
                guard plan.summary.operations > 0 else { throw Self.nothingToDo(plan) }
                _ = try await connection.call(
                    "plan.apply", ["plan_id": plan.planId], as: BatchAnswer.self)
            } catch {
                startProblems[videoId] = error.localizedDescription
            }
            await refreshDownloads()
            starting.remove(videoId)
            watchDownloads()
        }
    }

    /// Ask the engine which downloads haven't arrived. One that was on its way and is
    /// off the list has arrived: the library is read again, and it shows as a song.
    func refreshDownloads() async {
        guard let connection = engine?.connection,
            let found = try? await connection.call("queue.downloads", as: DownloadsAnswer.self)
        else { return }
        let onTheirWay = Set(pending.filter(\.isActive).map(\.jobId))
        if pending != found.downloads { pending = found.downloads }
        if !onTheirWay.subtracting(found.downloads.map(\.jobId)).isEmpty { try? await load() }
    }

    /// Keep asking, once a second, while anything is on its way.
    func watchDownloads() {
        guard !watchingDownloads, pending.contains(where: \.isActive) else { return }
        watchingDownloads = true
        Task {
            while pending.contains(where: \.isActive) {
                try? await Task.sleep(for: .seconds(1))
                await refreshDownloads()
            }
            watchingDownloads = false
        }
    }

    /// Try a download again that ended without the song.
    func retry(_ download: PendingDownload) {
        guard let videoId = download.videoId else { return }
        if download.video, let height = download.height {
            var wanted: [String: Any] = ["video_id": videoId, "height": height]
            if let fps = download.fps { wanted["fps"] = fps }
            startDownload(videoId, ["videos": [wanted]])
        } else {
            startDownload(videoId, ["video_ids": [videoId]])
        }
    }

    /// Take a download off the list: one still waiting is cancelled before it starts.
    func dismiss(_ download: PendingDownload) {
        guard let connection = engine?.connection else { return }
        Task {
            do {
                pending = try await connection.call(
                    "queue.dismiss", ["job_id": download.jobId], as: DownloadsAnswer.self
                ).downloads
            } catch {
                notice = error.localizedDescription
                await refreshDownloads()
            }
        }
    }

    /// Save the video that's playing into the library, whole, at the picture size
    /// that's showing. It lands in Downloads, like a downloaded song.
    func saveVideo(_ showing: ShowingVideo) {
        let wanted: [String: Any] = [
            "video_id": showing.source.videoId, "height": showing.quality.height,
            "fps": showing.quality.fps,
        ]
        startDownload(showing.source.videoId, ["videos": [wanted]])
    }

    private static func nothingToDo(_ plan: PlanAnswer) -> RPCError {
        let why = plan.summary.skipped?.keys.sorted().first?
            .replacingOccurrences(of: "_", with: " ")
        return RPCError(code: 0, message: "Nothing to do" + (why.map { " (\($0))." } ?? "."))
    }

    // MARK: the video on the whole screen

    /// Give the video the whole screen, or bring the app back. The window goes into
    /// macOS's full screen for it, and comes out again if it went in for this.
    func setVideoFullScreen(_ on: Bool) {
        guard on != videoFullScreen else { return }
        videoFullScreen = on
        guard let window = mainWindow else { return }
        let isFull = window.styleMask.contains(.fullScreen)
        if on {
            if !isFull {
                tookTheScreen = true
                window.toggleFullScreen(nil)
            }
        } else if tookTheScreen {
            tookTheScreen = false
            if isFull { window.toggleFullScreen(nil) }
        }
    }

    /// The library's window (not Settings, and not a sheet).
    private var mainWindow: NSWindow? {
        NSApp.windows.first { $0.isVisible && $0.canBecomeMain && $0.styleMask.contains(.resizable) }
            ?? NSApp.mainWindow
    }

    /// Make a plan, apply it, and wait for its jobs to end. Throws what went wrong, in
    /// the engine's own plain words. The library is reloaded afterwards.
    @discardableResult
    func run(
        plan kind: String, _ options: [String: Any],
        progress: ((_ done: Int, _ total: Int) -> Void)? = nil
    ) async throws -> [JobsAnswer.Job] {
        guard let connection = engine?.connection else {
            throw RPCError(code: RPCError.closed, message: "The engine isn't running.")
        }
        let plan = try await connection.call(
            "plan.create", ["kind": kind, "options": options], as: PlanAnswer.self)
        guard plan.summary.operations > 0 else { throw Self.nothingToDo(plan) }
        let batch = try await connection.call(
            "plan.apply", ["plan_id": plan.planId], as: BatchAnswer.self)
        var jobs: [JobsAnswer.Job] = []
        repeat {
            // A long run is asked about less often: each answer lists every job.
            try await Task.sleep(for: .milliseconds(plan.summary.operations > 50 ? 3000 : 700))
            jobs = try await connection.call(
                "queue.jobs", ["batch_id": batch.batchId], as: JobsAnswer.self
            ).jobs
            progress?(jobs.filter(\.isOver).count, jobs.count)
        } while !jobs.allSatisfy(\.isOver)
        try? await load()
        if progress != nil { return jobs }  // a long run reports its own tally
        if let failed = jobs.first(where: { !$0.worked }) {
            throw RPCError(
                code: 0,
                message: failed.message ?? failed.reason?.replacingOccurrences(of: "_", with: " ")
                    ?? "It didn't work.")
        }
        return jobs
    }

    // MARK: settings

    func loadSettings() {
        guard let connection = engine?.connection else { return }
        Task {
            engineSettings = try? await connection.call("settings.get", as: EngineSettings.self)
        }
    }

    func setDailyCap(_ downloads: Int) {
        guard let connection = engine?.connection else { return }
        Task {
            do {
                engineSettings = try await connection.call(
                    "settings.set", ["daily_cap": downloads], as: EngineSettings.self)
            } catch {
                notice = error.localizedDescription
            }
        }
    }

    /// Look up lyrics for every song that has none (LRCLIB, then YouTube Music).
    func findMissingLyrics() {
        if case .working = lyricsSearch { return }
        lyricsSearch = .working(done: 0, total: 0)
        Task {
            do {
                let jobs = try await run(plan: "lyrics", ["missing": true]) { done, total in
                    self.lyricsSearch = .working(done: done, total: total)
                }
                let tally = lyricsTally(jobs)
                lyricsSearch = .finished(
                    "Looked up \(jobs.count.formatted()) songs: \(tally.timed.formatted()) got timed "
                        + "lyrics, \(tally.plain.formatted()) got plain lyrics, and "
                        + "\(tally.none.formatted()) weren't found.")
            } catch {
                lyricsSearch = .finished(error.localizedDescription)
            }
        }
    }

    // MARK: fixing a song by hand

    /// The lyrics as the owner would edit them: the timed ones if there are any.
    func lyricsText(for track: Track) async -> String {
        guard let connection = engine?.connection, track.lyrics != .none,
            let found = try? await connection.call(
                "library.lyrics", ["path": track.path], as: TrackLyrics.self)
        else { return "" }
        return found.synced ?? found.plain ?? ""
    }

    /// Look for timed lyrics for a song by its names (LRCLIB, then YouTube Music), for
    /// the Edit Details sheet. Nothing is saved until the owner saves the sheet.
    func findLyrics(
        title: String, artist: String, album: String, for track: Track
    ) async -> (text: String, timed: Bool, source: String?)? {
        guard let connection = engine?.connection else { return nil }
        var asked: [String: Any] = ["title": title]
        if !artist.isEmpty { asked["artist"] = artist }
        if !album.isEmpty { asked["album"] = album }
        if let length = track.durationS { asked["duration_s"] = length }
        if let videoId = track.sourceId { asked["video_id"] = videoId }
        guard let found = try? await connection.call("lyrics.find", asked, as: FoundLyrics.self)
        else { return nil }
        if let synced = found.synced, !synced.isEmpty { return (synced, true, found.source) }
        if let plain = found.plain, !plain.isEmpty { return (plain, false, found.source) }
        return nil
    }

    func saveEdit(
        of track: Track, changes: [String: Any], lyrics: String?, coverFile: URL?
    ) async throws {
        var options: [String: Any] = ["path": track.path, "changes": changes]
        if let lyrics { options["lyrics"] = lyrics }
        if let coverFile { options["cover_file"] = coverFile.path }
        try await run(plan: "edit", options)
        Covers.shared.forgetAll()  // a cover may have changed
        if player.current?.trackId == track.trackId, let id = track.trackId,
            let now = everything.tracks(withIDs: [id]).first
        {
            showLyrics(for: now)
        }
    }

    // MARK: swapping a song's audio (not built yet)

    /// Swap Audio has its button and nothing behind it yet: the owner is deciding which
    /// checks must pass before a file may be replaced. Nothing is changed.
    func explainSwap(of track: Track) {
        info = Info(
            title: "Swap Audio is coming",
            text: "This will replace “\(track.title)” with YouTube Music's official audio, "
                + "keeping its names, cover, lyrics, favourites and playlists, and keeping your "
                + "old file so it can be undone.\n\nIt isn't switched on yet. The checks that "
                + "have to pass first are still being decided: that it's the same song, the same "
                + "version, the same recording, and really the better copy. Nothing has been "
                + "changed.")
    }

    // MARK: lyrics

    private func showLyrics(for track: Track?) {
        guard let track else {
            lyrics.show(.nothingPlaying, for: nil)
            return
        }
        // Looked up for now and never saved: a song played from YouTube Music, and a
        // saved video (which keeps no lyrics of its own).
        let lookUpId = track.videoId ?? (track.isVideo ? track.sourceId : nil)
        guard let connection = engine?.connection, track.lyrics != .none || lookUpId != nil
        else {
            lyrics.show(.missing, for: track.path)
            return
        }
        lyrics.show(.loading, for: track.path)
        Task {
            let found: TrackLyrics?
            if let lookUpId {
                var asked: [String: Any] = ["title": track.title, "video_id": lookUpId]
                if let artist = track.artist { asked["artist"] = artist }
                if let album = track.album { asked["album"] = album }
                if let length = track.durationS { asked["duration_s"] = length }
                found = try? await connection.call("lyrics.find", asked, as: TrackLyrics.self)
            } else {
                found = try? await connection.call(
                    "library.lyrics", ["path": track.path], as: TrackLyrics.self)
            }
            // The song changed meanwhile, or lyrics timed to its video are already up.
            guard lyrics.trackPath == track.path, lyrics.forVideo == nil else { return }
            let lines = found?.synced.map(LRC.parse) ?? []
            if !lines.isEmpty {
                lyrics.show(.synced(lines), for: track.path)
                lyrics.follow(player.clock.time)
            } else if let plain = found?.plain, !plain.isEmpty {
                lyrics.show(.plain(plain), for: track.path)
            } else {
                lyrics.show(.missing, for: track.path)
            }
        }
    }

    /// A song's video is often not the song second for second (an intro, a scene in the
    /// middle), so the song's timed lyrics don't fit it; and many songs have no timed
    /// lyrics at all. Lyrics timed to the video itself are looked for (LRCLIB keeps
    /// them by length, and people time them to the video's cut too; then YouTube
    /// Music's own). Found, they replace the song's while the video plays. Nothing is
    /// saved.
    private func showLyrics(forVideo showing: ShowingVideo?) {
        guard let track = player.current else { return }
        guard let showing else {
            if lyrics.forVideo != nil { showLyrics(for: track) }  // the song's own again
            return
        }
        // The song's own timed lyrics fit a video that's the song second for second.
        if showing.keepsTime, track.lyrics == .synced { return }
        guard let connection = engine?.connection, let artist = track.artist ?? track.albumArtist
        else { return }
        let videoId = showing.source.videoId
        let asked: [String: Any] = [
            "title": track.title, "artist": artist, "duration_s": showing.source.length,
            "video_id": videoId,
        ]
        Task {
            let found = try? await connection.call("lyrics.find", asked, as: TrackLyrics.self)
            guard player.video?.source.videoId == videoId, player.current == track else { return }
            let lines = found?.synced.map(LRC.parse) ?? []
            guard !lines.isEmpty else { return }  // none timed to it: the song's words stay
            lyrics.show(.synced(lines), for: track.path, forVideo: videoId)
            lyrics.follow(player.clock.time)
        }
    }

    // MARK: the space bar

    /// Space plays and pauses, as in every music app, except while typing in a field.
    private func installSpaceBar() {
        guard keyMonitor == nil else { return }
        keyMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            // Esc leaves the video's full screen, whatever has the keyboard's attention
            // (a button's own shortcut wasn't reliable there).
            if event.keyCode == 53 {
                let left = MainActor.assumeIsolated { () -> Bool in
                    guard let self, self.videoFullScreen else { return false }
                    self.setVideoFullScreen(false)
                    return true
                }
                return left ? nil : event
            }
            guard event.keyCode == 49,
                event.modifierFlags.intersection([.command, .option, .control]).isEmpty
            else { return event }
            let borrowed = MainActor.assumeIsolated { () -> Bool in
                guard let action = self?.spaceBar else { return false }
                action()
                return true
            }
            if borrowed { return nil }
            guard !(event.window?.firstResponder is NSText) else { return event }
            MainActor.assumeIsolated { self?.player.toggle() }
            return nil
        }
    }

    private var appVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.2.0"
    }
}

struct NamePrompt: Identifiable {
    let id = UUID()
    let title: String
    let button: String
    let name: String
    let done: (String) -> Void
}

private struct FoundLyrics: Decodable {
    let synced: String?
    let plain: String?
    let source: String?
}

private struct Hello: Decodable {
    let engineVersion: String
}

/// The lyrics of the song that's playing, and the line being sung.
@MainActor
@Observable
final class LyricsModel {
    enum State: Equatable {
        case nothingPlaying
        case loading
        case synced([LyricLine])
        case plain(String)
        case missing
    }

    private(set) var state: State = .nothingPlaying
    private(set) var trackPath: String?
    /// Set when these lyrics are timed to the song's video (by its id), not the song.
    private(set) var forVideo: String?
    private(set) var currentLine: Int?
    /// False while the lyrics' times don't fit what's playing (a music video that isn't
    /// the song second for second): no line is lit up then.
    var timed = true {
        didSet { if !timed { currentLine = nil } }
    }

    /// There are lyrics to show right now: a song is on, and its words were found.
    var hasLyrics: Bool {
        switch state {
        case .synced, .plain: true
        default: false
        }
    }

    func show(_ state: State, for path: String?, forVideo videoId: String? = nil) {
        self.state = state
        trackPath = path
        forVideo = videoId
        currentLine = nil
    }

    /// Called a few times a second; only changes anything when the line changes.
    func follow(_ time: Double) {
        guard timed, case .synced(let lines) = state else { return }
        let line = LRC.current(at: time, in: lines)
        if line != currentLine { currentLine = line }
    }
}
