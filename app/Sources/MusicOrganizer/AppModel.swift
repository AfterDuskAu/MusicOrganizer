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
    private(set) var library = Library.empty
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

    // The YouTube Music search page.
    var youtubeQuery = ""
    private(set) var youtubeResults: [SearchResult] = []
    private(set) var youtubeSearching = false
    private(set) var youtubeProblem: String?
    private(set) var downloads: [String: DownloadState] = [:]

    enum DownloadState: Equatable {
        case working
        case failed(String)
    }
    var searchText = ""

    let player = Player()
    let lyrics = LyricsModel()

    @ObservationIgnored private var engine: EngineProcess?
    @ObservationIgnored private var stopping = false
    @ObservationIgnored private var keyMonitor: Any?
    private static let rootKey = "libraryRoot"

    init() {
        player.onTrackChange = { [weak self] track in self?.showLyrics(for: track) }
        player.onTick = { [weak self] time in self?.lyrics.follow(time) }
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
            return (url, found.httpHeaders)
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
                onNotification: { [weak self] method, _ in
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
        let built = await Task.detached { Library(tracks: list.tracks) }.value
        root = URL(fileURLWithPath: list.root)
        player.root = root
        library = built
        status = try? await connection.call("library.status", as: LibraryStatus.self)
        if let found = try? await connection.call("listening.get", as: Listening.self) {
            listening = found
            favourites = Set(found.favourites)
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
        }
    }

    func playlist(_ id: String) -> Playlist? { listening.playlists.first { $0.id == id } }

    func tracks(in playlist: Playlist) -> [Track] { library.tracks(withIDs: playlist.trackIds) }

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
        let query = youtubeQuery.trimmingCharacters(in: .whitespaces)
        guard !query.isEmpty, !youtubeSearching, let connection = engine?.connection else { return }
        youtubeSearching = true
        youtubeProblem = nil
        Task {
            do {
                let found = try await connection.call(
                    "search.ytmusic", ["query": query, "limit": 25], as: SearchAnswer.self)
                youtubeResults = found.results
                if found.results.isEmpty { youtubeProblem = "Nothing found for “\(query)”." }
            } catch {
                youtubeProblem = error.localizedDescription
            }
            youtubeSearching = false
        }
    }

    /// Download one song into the library. Only ever called by the owner's click.
    func download(_ result: SearchResult) {
        guard downloads[result.videoId] != .working else { return }
        downloads[result.videoId] = .working
        Task {
            do {
                try await run(plan: "download", ["video_ids": [result.videoId]])
                downloads[result.videoId] = nil  // it now shows as "in your library"
            } catch {
                downloads[result.videoId] = .failed(error.localizedDescription)
            }
        }
    }

    /// Make a plan, apply it, and wait for its jobs to end. Throws what went wrong, in
    /// the engine's own plain words. The library is reloaded afterwards.
    func run(plan kind: String, _ options: [String: Any]) async throws {
        guard let connection = engine?.connection else {
            throw RPCError(code: RPCError.closed, message: "The engine isn't running.")
        }
        let plan = try await connection.call(
            "plan.create", ["kind": kind, "options": options], as: PlanAnswer.self)
        guard plan.summary.operations > 0 else {
            let why = plan.summary.skipped?.keys.sorted().first?.replacingOccurrences(of: "_", with: " ")
            throw RPCError(code: 0, message: "Nothing to do" + (why.map { " (\($0))." } ?? "."))
        }
        let batch = try await connection.call(
            "plan.apply", ["plan_id": plan.planId], as: BatchAnswer.self)
        var jobs: [JobsAnswer.Job] = []
        repeat {
            try await Task.sleep(for: .milliseconds(700))
            jobs = try await connection.call(
                "queue.jobs", ["batch_id": batch.batchId], as: JobsAnswer.self
            ).jobs
        } while !jobs.allSatisfy(\.isOver)
        try? await load()
        if let failed = jobs.first(where: { !$0.worked }) {
            throw RPCError(
                code: 0,
                message: failed.message ?? failed.reason?.replacingOccurrences(of: "_", with: " ")
                    ?? "It didn't work.")
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

    func saveEdit(
        of track: Track, changes: [String: Any], lyrics: String?, coverFile: URL?
    ) async throws {
        var options: [String: Any] = ["path": track.path, "changes": changes]
        if let lyrics { options["lyrics"] = lyrics }
        if let coverFile { options["cover_file"] = coverFile.path }
        try await run(plan: "edit", options)
        Covers.shared.forgetAll()  // a cover may have changed
        if player.current?.trackId == track.trackId, let id = track.trackId,
            let now = library.tracks(withIDs: [id]).first
        {
            showLyrics(for: now)
        }
    }

    // MARK: lyrics

    private func showLyrics(for track: Track?) {
        guard let track else {
            lyrics.show(.nothingPlaying, for: nil)
            return
        }
        guard track.lyrics != .none, track.videoId == nil, let connection = engine?.connection else {
            lyrics.show(.missing, for: track.path)
            return
        }
        lyrics.show(.loading, for: track.path)
        Task {
            let found = try? await connection.call(
                "library.lyrics", ["path": track.path], as: TrackLyrics.self)
            guard lyrics.trackPath == track.path else { return }  // the song changed meanwhile
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

    // MARK: the space bar

    /// Space plays and pauses, as in every music app, except while typing in a field.
    private func installSpaceBar() {
        guard keyMonitor == nil else { return }
        keyMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard event.keyCode == 49,
                event.modifierFlags.intersection([.command, .option, .control]).isEmpty,
                !(event.window?.firstResponder is NSText)
            else { return event }
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
    private(set) var currentLine: Int?

    func show(_ state: State, for path: String?) {
        self.state = state
        trackPath = path
        currentLine = nil
    }

    /// Called a few times a second; only changes anything when the line changes.
    func follow(_ time: Double) {
        guard case .synced(let lines) = state else { return }
        let line = LRC.current(at: time, in: lines)
        if line != currentLine { currentLine = line }
    }
}
