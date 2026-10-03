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
    /// Settings → Downloads: downloads stay under Discover until the owner says otherwise.
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
    /// Songs from YouTube played all the way through, by YouTube id (the red checkmark
    /// on What's New, Find and YouTube Music). Kept for good, per profile.
    private(set) var heard = Set<String>()
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

    /// The YouTube Queue: songs from YouTube Music put on with Up Next, in order. A
    /// playlist of songs that aren't downloaded, kept with the profile's settings. Shown
    /// under YouTube Music in the sidebar while it has anything in it.
    private(set) var youtubeQueue: [SearchResult] = AppModel.savedYouTubeQueue()
    private static let youtubeQueueKey = "youtubeQueue"

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
    @ObservationIgnored private var watchingDailyLimit = false
    /// What Download Automatically last started from.
    @ObservationIgnored private var lastAuto: [DiscoverSeed]?

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
    /// Discover → Import Playlists: a playlist from elsewhere, and what was found of it.
    let importing = ImportPage()
    /// Several downloads planned and waiting for the owner's yes (Download Selected).
    var batch: BatchDownload?
    /// How Discover's Download Automatically is going, until its note is closed.
    var auto: AutoDownload?
    /// The people who use the app on this Mac, each with a library of their own, and
    /// which of them it's open for (Settings → Profile).
    private(set) var profiles = AppModel.savedProfiles()
    /// How the download queue is doing: asked for when its worker starts or stops.
    private(set) var queueStatus: QueueStatus?
    /// Which services are set up and signed in to (Settings → Profile → Accounts).
    private(set) var accounts: AccountStatus?
    /// The browser is open on a service's sign-in page, and its answer is awaited.
    private(set) var signingIn = false
    /// Why the last sign-in didn't work, until the next one is tried.
    private(set) var accountNote: String?

    /// Find and download in one go, with no more clicks.
    enum AutoDownload: Equatable {
        case finding(Int)
        case started(String)
        case failed(String)
    }
    /// Downloads the owner asked to delete, waiting for their yes.
    var deletingDownloads: [Track]?

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
        importing.read = { [weak self] what in
            guard let self else { throw RPCError(code: 0, message: "The engine isn't running.") }
            return try await self.readImport(what)
        }
        importing.list = { [weak self] in try await self?.spotifyPlaylists() ?? [] }
        importing.find = { [weak self] tracks in try await self?.findImported(tracks) ?? [] }
        importing.download = { [weak self] name, owned, songs in
            guard let self else { throw RPCError(code: 0, message: "The engine isn't running.") }
            return try await self.downloadImport(named: name, owned: owned, songs: songs)
        }
        for page in [whatsNew, find] {
            page.ask = { [weak self] seeds, count, shuffle, name, exclude in
                guard let self else { throw CancellationError() }
                return try await self.suggest(seeds, count, shuffle, name, exclude: exclude)
            }
        }
        player.onTrackChange = { [weak self] track in self?.showLyrics(for: track) }
        player.onTick = { [weak self] time in
            guard let self else { return }
            // A music video that isn't as long as the song isn't the song second for
            // second, so the song's timed lyrics are shown without a line lit up;
            // lyrics timed to the video itself are lit.
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
            let engine = try EngineProcess(
                executable: executable, environment: ["MUSICORG_PROFILE": profiles.current.id])
            self.engine = engine
            stopping = false
            engine.connection.start(
                onNotification: { [weak self] method, params in
                    if method == "account.changed" {
                        let signedIn = params["signed_in"] as? Bool ?? false
                        let problem = params["problem"] as? String
                        Task { @MainActor in
                            self?.accountChanged(signedIn: signedIn, problem: problem)
                        }
                        return
                    }
                    if method == "import.progress" {
                        let done = params["done"] as? Int ?? 0
                        Task { @MainActor in self?.importing.progress(done: done) }
                        return
                    }
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
        if let root = libraryToOpen {
            await open(URL(fileURLWithPath: root))
        } else {
            phase = .needsLibrary
        }
    }

    /// The library folder of the profile in use. A folder given when the app was
    /// started (`-libraryRoot <folder>`, a developer's check) comes before it.
    private var libraryToOpen: String? {
        let given = UserDefaults.standard.volatileDomain(forName: UserDefaults.argumentDomain)
        return given[Self.rootKey] as? String ?? profiles.current.libraryRoot
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
        if let owner = profiles.profiles.first(where: {
            $0.id != profiles.currentId && $0.libraryRoot == url.path
        }) {
            notice = ProfileList.Problem.sameFolder(owner.name).localizedDescription
            return
        }
        profiles.setCurrentLibrary(url.path)
        saveProfiles()
        player.stop()
        retry()  // an engine serves one library, so a new choice starts a new engine
    }

    // MARK: The YouTube Queue

    /// Up Next on the YouTube Music page: the song joins the YouTube Queue, and plays
    /// after the song that's playing (straight away, if nothing is).
    func upNext(_ result: SearchResult) {
        if !youtubeQueue.contains(result) {
            youtubeQueue.append(result)
            saveYouTubeQueue()
        }
        player.queueNext(result.track)
    }

    /// Play the YouTube Queue from one of its songs.
    func playYouTubeQueue(from result: SearchResult? = nil) {
        let start = result.flatMap { youtubeQueue.firstIndex(of: $0) } ?? 0
        player.play(youtubeQueue.map(\.track), startAt: start)
    }

    func removeFromYouTubeQueue(_ result: SearchResult) {
        youtubeQueue.removeAll { $0 == result }
        saveYouTubeQueue()
    }

    func clearYouTubeQueue() {
        youtubeQueue = []
        saveYouTubeQueue()
    }

    private static func savedYouTubeQueue() -> [SearchResult] {
        guard let data = UserDefaults.standard.data(forKey: youtubeQueueKey) else { return [] }
        return (try? JSONDecoder().decode([SearchResult].self, from: data)) ?? []
    }

    private func saveYouTubeQueue() {
        if youtubeQueue.isEmpty {
            UserDefaults.standard.removeObject(forKey: Self.youtubeQueueKey)
        } else if let data = try? JSONEncoder().encode(youtubeQueue) {
            UserDefaults.standard.set(data, forKey: Self.youtubeQueueKey)
        }
    }

    // MARK: Profiles

    private static let profilesKey = "profiles"

    /// The saved list; or, the first time, one profile with the library that was in use
    /// before there were profiles, named after this Mac's user.
    private static func savedProfiles() -> ProfileList {
        if let data = UserDefaults.standard.data(forKey: profilesKey),
            let saved = try? JSONDecoder().decode(ProfileList.self, from: data),
            !saved.profiles.isEmpty
        {
            return saved
        }
        return ProfileList(
            firstNamed: NSFullUserName(),
            libraryRoot: UserDefaults.standard.persistentDomain(forName: settingsDomain)?[rootKey]
                as? String)
    }

    private func saveProfiles() {
        if let data = try? JSONEncoder().encode(profiles) {
            UserDefaults.standard.set(data, forKey: Self.profilesKey)
        }
    }

    /// Make a profile with a library folder of its own, and switch to it. The engine
    /// makes the library when it's first opened; nothing of anyone else's is touched.
    func addProfile(named name: String, libraryRoot: String) throws {
        let made = try profiles.add(name: name, libraryRoot: libraryRoot)
        saveProfiles()
        switchProfile(to: made.id)
    }

    func renameProfile(_ id: String, to name: String) throws {
        try profiles.rename(id, to: name)
        saveProfiles()
    }

    /// Take a profile off the list. Its library folder stays exactly where it is.
    func removeProfile(_ id: String) throws {
        try profiles.remove(id)
        UserDefaults.standard.removeObject(forKey: Self.settingsKey(id))
        saveProfiles()
    }

    /// Change who the app is open for: their library, playlists, sign-ins and settings.
    /// The profile being left is put away exactly as it is, and nothing is deleted.
    /// Its downloads still waiting carry on when it's switched back to.
    func switchProfile(to id: String) {
        guard id != profiles.currentId, profiles.profile(id) != nil else { return }
        player.stop()
        putAwaySettings(of: profiles.currentId)
        profiles.switchTo(id)
        saveProfiles()
        bringBackSettings(of: id)
        clearForAnotherLibrary()
        retry()  // an engine serves one library and one profile: a new one starts
    }

    private static var settingsDomain: String {
        Bundle.main.bundleIdentifier ?? ProcessInfo.processInfo.processName
    }

    private static func settingsKey(_ id: String) -> String { "profileSettings.\(id)" }

    private func putAwaySettings(of id: String) {
        let all = UserDefaults.standard.persistentDomain(forName: Self.settingsDomain) ?? [:]
        UserDefaults.standard.set(ProfileSettings.toKeep(all), forKey: Self.settingsKey(id))
    }

    private func bringBackSettings(of id: String) {
        let all = UserDefaults.standard.persistentDomain(forName: Self.settingsDomain) ?? [:]
        let theirs = UserDefaults.standard.dictionary(forKey: Self.settingsKey(id)) ?? [:]
        let changes = ProfileSettings.changes(from: all, to: theirs)
        for key in changes.remove { UserDefaults.standard.removeObject(forKey: key) }
        for (key, value) in changes.set { UserDefaults.standard.set(value, forKey: key) }
    }

    /// Nothing of the last profile's stays on screen while the next one's is read.
    private func clearForAnotherLibrary() {
        (library, everything, downloaded, videos) = (.empty, .empty, [], [])
        (listening, favourites, heard, status, root) = (.empty, [], [], nil, nil)
        (pending, starting, startProblems) = ([], [], [:])
        (queueStatus, accounts, accountNote, signingIn) = (nil, nil, nil, false)
        (auto, batch, deletingDownloads, lastAuto) = (nil, nil, nil, nil)
        (youtubeQuery, youtubeResults, youtubeProblem, searchText) = ("", [], nil, "")
        libraryVersion += 1
        for page in [whatsNew, find] { page.reset() }
        importing.reset()
        keepDownloadsSeparate =
            UserDefaults.standard.object(forKey: "keepDownloadsSeparate") as? Bool ?? true
        youtubeQueue = Self.savedYouTubeQueue()  // the next profile's own
    }

    private func open(_ folder: URL) async {
        guard let connection = engine?.connection else { return }
        phase = .loading
        do {
            if profiles.current.isNew, profiles.current.libraryRoot == folder.path {
                // A new profile's library: the engine makes it, once.
                _ = try await connection.call("library.init", ["root": folder.path])
                profiles.setCurrentLibrary(folder.path)
                saveProfiles()
            }
            _ = try await connection.call("library.open", ["root": folder.path])
            try await load()
            phase = .ready
            // Downloads asked for before the app was last closed carry on in the engine.
            await refreshDownloads()
            watchDownloads()
            watchDailyLimit()
            loadAccounts()
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
            heard = Set(found.heard ?? [])
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
        case "queue.state":
            // The queue's worker started or stopped: at the daily limit, say.
            await refreshQueueStatus()
            await refreshDownloads()
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
        if track.trackId == nil, let videoId = track.videoId {
            // A song from YouTube, heard to its end: it gets the red checkmark, for good.
            heard.insert(videoId)
            change { connection in
                _ = try await connection.call(
                    "listening.heard", ["video_id": videoId], as: HeardCount.self)
            }
            return
        }
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
        _ seeds: [DiscoverSeed], _ count: Int, _ shuffle: String, _ page: String,
        exclude: [String] = []
    ) async throws -> DiscoverAnswer {
        guard let connection = engine?.connection else {
            throw RPCError(code: 0, message: "The engine isn't running.")
        }
        var params: [String: Any] = [
            "seeds": seeds.map(\.params), "count": count, "shuffle": shuffle, "token": page,
        ]
        if !exclude.isEmpty { params["exclude"] = exclude }
        return try await connection.call("discover.suggest", params, as: DiscoverAnswer.self)
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
        Task {
            do {
                batch = try await plan(downloading: picks)
            } catch {
                notice = error.localizedDescription
            }
        }
    }

    /// A plan for downloading these picks (the ones not already here or on their way):
    /// how many, about how long, over how many days. Nothing is queued by this.
    func plan(downloading picks: [DiscoverPick]) async throws -> BatchDownload {
        let wanted = picks.filter(canDownload)
        guard let connection = engine?.connection else {
            throw RPCError(code: RPCError.closed, message: "The engine isn't running.")
        }
        guard !wanted.isEmpty else {
            throw RPCError(code: 0, message: "All of those are in your library already, or on their way.")
        }
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
        return BatchDownload(
            planId: plan.planId, videoIds: wanted.map(\.videoId),
            count: plan.summary.downloads ?? plan.summary.operations,
            minutes: plan.summary.estMinutes ?? 0, days: plan.summary.days ?? 1)
    }

    /// Discover's Download Automatically: find this many songs from these starting
    /// points and queue every one, with nothing more to click. The click on the number
    /// is the owner's yes; the engine still makes a plan first and then applies it, so
    /// the batch is journaled like any other. The picks land on the Find page.
    func downloadAutomatically(_ seeds: [DiscoverSeed], count: Int) {
        if case .finding = auto { return }
        guard !seeds.isEmpty, let connection = engine?.connection else { return }
        auto = .finding(count)
        // Asked for again, it starts from other songs: the first lot are on their way.
        let again = lastAuto == seeds
        lastAuto = seeds
        let page = find
        Task {
            guard await page.run(seeds, count: count, different: again) else {
                auto = .failed(
                    page.problem ?? "Find is busy with another search. Try again in a moment.")
                return
            }
            let picks = page.picks
            guard !picks.isEmpty else {
                auto = .failed(page.note ?? "Nothing new was found for that.")
                return
            }
            do {
                let batch = try await plan(downloading: picks)
                // What today's limit has room for, before these join the queue.
                let status = try? await connection.call("queue.status", as: QueueStatus.self)
                let limit = status?.dailyCap ?? 250
                let room = limit - (status?.dailyCount ?? 0) - pending.filter(\.isActive).count
                start(batch)
                auto = .started(
                    Guided.startedNote(
                        batch.count, from: page.seeds, wanted: count, minutes: batch.minutes,
                        allowance: room, limit: limit))
            } catch {
                auto = .failed(error.localizedDescription)
            }
        }
    }

    // MARK: Import Playlists

    /// Read a playlist from where it lives: a YouTube or YouTube Music link, or one of
    /// the signed-in Spotify account's playlists.
    private func readImport(_ what: ImportRequest) async throws -> ImportedPlaylist {
        guard let connection = engine?.connection else {
            throw RPCError(code: RPCError.closed, message: "The engine isn't running.")
        }
        return try await connection.call("import.playlist", what.params, as: ImportedPlaylist.self)
    }

    private func spotifyPlaylists() async throws -> [SpotifyPlaylist] {
        guard let connection = engine?.connection else {
            throw RPCError(code: RPCError.closed, message: "The engine isn't running.")
        }
        return try await connection.call(
            "import.playlists", ["source": "spotify"], as: SpotifyPlaylistsAnswer.self
        ).playlists
    }

    // MARK: Accounts (for reading playlists)

    /// Ask the engine which services are set up and signed in to.
    func loadAccounts() {
        guard let connection = engine?.connection else { return }
        Task {
            if let found = try? await connection.call("account.status", as: AccountStatus.self),
                found != accounts
            {
                accounts = found
            }
        }
    }

    /// Sign in to Spotify: the engine listens for Spotify's answer, and Spotify's own
    /// sign-in page opens in the browser. The password is typed there, never here.
    func signInToSpotify(clientId: String) {
        guard let connection = engine?.connection, !signingIn else { return }
        (signingIn, accountNote) = (true, nil)
        Task {
            do {
                var params: [String: Any] = ["service": "spotify"]
                let typed = clientId.trimmingCharacters(in: .whitespacesAndNewlines)
                if !typed.isEmpty { params["client_id"] = typed }
                let answer = try await connection.call(
                    "account.sign_in", params, as: SignInAnswer.self)
                guard let page = answer.spotifyPage else {
                    throw RPCError(code: 0, message: "The engine gave an address that isn't Spotify's.")
                }
                NSWorkspace.shared.open(page)
            } catch {
                (signingIn, accountNote) = (false, error.localizedDescription)
            }
            loadAccounts()
        }
    }

    func signOutOfSpotify() {
        guard let connection = engine?.connection else { return }
        (signingIn, accountNote) = (false, nil)
        importing.forgetSpotifyPlaylists()
        Task {
            if let found = try? await connection.call(
                "account.sign_out", ["service": "spotify"], as: AccountStatus.self)
            {
                accounts = found
            }
        }
    }

    /// Spotify answered a sign-in (or nobody came back from its page in time).
    private func accountChanged(signedIn: Bool, problem: String?) {
        signingIn = false
        accountNote = signedIn ? nil : (problem ?? "The sign-in didn't work.")
        importing.forgetSpotifyPlaylists()
        loadAccounts()
        // Back to the app from the browser, now that there's something to see.
        if signedIn { NSApp.activate(ignoringOtherApps: true) }
    }

    /// Which of these songs the owner has, and what the rest are on YouTube Music.
    private func findImported(_ tracks: [ImportTrack]) async throws -> [ImportFound] {
        guard let connection = engine?.connection else {
            throw RPCError(code: RPCError.closed, message: "The engine isn't running.")
        }
        return try await connection.call(
            "import.find", ["tracks": tracks.map(\.params), "token": "import"],
            as: ImportFindAnswer.self
        ).found
    }

    /// Make the owner's playlist for an import (or use the one of that name), put the
    /// songs they already have in it, and queue the rest: each joins the playlist as it
    /// arrives. The click is the owner's yes; the engine still plans and journals the
    /// batch. Returns what to say about it.
    private func downloadImport(
        named name: String, owned: [String], songs: [ImportCandidate]
    ) async throws -> String {
        guard let connection = engine?.connection else {
            throw RPCError(code: RPCError.closed, message: "The engine isn't running.")
        }
        var playlist = listening.playlists.first {
            $0.name.compare(name, options: .caseInsensitive) == .orderedSame
        }
        if playlist == nil {
            let made = try await connection.call(
                "playlist.create", ["name": name], as: PlaylistsAnswer.self)
            listening.playlists = made.playlists
            playlist = made.playlists.last
        }
        guard let playlist else {
            throw RPCError(code: 0, message: "The playlist couldn't be made.")
        }
        let adding = owned.filter { !playlist.trackIds.contains($0) }
        if !adding.isEmpty {
            let answer = try await connection.call(
                "playlist.set_tracks",
                ["playlist_id": playlist.id, "track_ids": playlist.trackIds + adding],
                as: PlaylistsAnswer.self)
            listening.playlists = answer.playlists
        }
        let wanted = songs.filter {
            !everything.videoIDs.contains($0.videoId) && downloadState(of: $0.videoId) != .working
        }
        guard !wanted.isEmpty else {
            return owned.isEmpty
                ? "There was nothing to download."
                : "Your playlist \"\(playlist.name)\" has the \(owned.count) "
                    + "\(owned.count == 1 ? "song" : "songs") you already had. Nothing needed "
                    + "downloading."
        }
        let plan = try await connection.call(
            "plan.create",
            [
                "kind": "download",
                "options": [
                    "video_ids": wanted.map(\.videoId),
                    "candidates": wanted.map(\.params),
                    "playlist_id": playlist.id,
                ],
            ], as: PlanAnswer.self)
        guard plan.summary.operations > 0 else { throw Self.nothingToDo(plan) }
        let count = plan.summary.downloads ?? plan.summary.operations
        // What today's limit has room for, before these join the queue.
        let status = try? await connection.call("queue.status", as: QueueStatus.self)
        let limit = status?.dailyCap ?? 250
        let room = limit - (status?.dailyCount ?? 0) - pending.filter(\.isActive).count
        start(
            BatchDownload(
                planId: plan.planId, videoIds: wanted.map(\.videoId), count: count,
                minutes: plan.summary.estMinutes ?? 0, days: plan.summary.days ?? 1))
        return Imports.startedNote(
            count, playlist: playlist.name, owned: owned.count,
            minutes: plan.summary.estMinutes ?? 0, allowance: room, limit: limit)
    }

    /// Cancel every download that's still waiting its turn. The one downloading right
    /// now carries on.
    func cancelWaitingDownloads() {
        guard let connection = engine?.connection else { return }
        Task {
            do {
                pending = try await connection.call(
                    "queue.dismiss", ["waiting": true], as: DownloadsAnswer.self
                ).downloads
            } catch {
                notice = error.localizedDescription
                await refreshDownloads()
            }
        }
    }

    /// Why the waiting downloads aren't moving, if they aren't: the daily limit, or
    /// YouTube refusing this Mac for a while.
    var downloadsHoldUp: String? {
        guard pending.contains(where: { $0.isActive && !$0.isRunning }) else { return nil }
        return queueStatus?.holdUp { $0.formatted(date: .omitted, time: .shortened) }
    }

    private func refreshQueueStatus() async {
        guard let connection = engine?.connection else { return }
        if let found = try? await connection.call("queue.status", as: QueueStatus.self),
            found != queueStatus
        {
            queueStatus = found
        }
    }

    /// How much of the daily download limit is used: the sidebar's counter. It's the
    /// engine's own count, so everything that uses the limit is in it: songs, videos,
    /// Download Automatically, and downloads made from the command line.
    var dailyUse: DailyUse? { queueStatus.map(DailyUse.init) }

    /// Keep the counter right while nothing is happening too: a download leaves the
    /// count when it's a day old, which nothing announces. Asked once a minute (the
    /// engine reads it from the library's own queue file; YouTube isn't asked).
    private func watchDailyLimit() {
        guard !watchingDailyLimit else { return }
        watchingDailyLimit = true
        Task {
            while engine != nil, !stopping {
                await refreshQueueStatus()
                try? await Task.sleep(for: .seconds(60))
            }
            watchingDailyLimit = false
        }
    }

    /// The owner said yes: the planned downloads go to the queue.
    func confirmBatch() {
        guard let batch else { return }
        self.batch = nil
        start(batch)
    }

    /// Queue a planned batch of downloads. They show at the top of Discover → Downloads
    /// until they arrive.
    func start(_ batch: BatchDownload) {
        guard let connection = engine?.connection else { return }
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

    /// What a button says while its song or video is on its way: "Downloading… 42%"
    /// once the engine can say how far along it is.
    func downloadNote(of videoId: String, saving: Bool = false) -> String {
        if let found = pending.first(where: { $0.videoId == videoId && $0.isActive }) {
            if let percent = found.percent {
                return percent >= 100 ? "Finishing…" : "\(saving ? "Saving" : "Downloading")… \(percent)%"
            }
            if !found.isRunning { return "Waiting its turn…" }
        }
        return saving ? "Saving…" : "Downloading…"
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
        // A download counts towards the daily limit as it starts: the counter is asked
        // for again whenever a different one is downloading, or the list got shorter.
        let before = (pending.filter(\.isRunning).map(\.jobId), pending.count)
        if pending != found.downloads { pending = found.downloads }
        if before != (pending.filter(\.isRunning).map(\.jobId), pending.count) {
            await refreshQueueStatus()
        }
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
                await refreshQueueStatus()  // the sidebar's counter shows the new limit
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

    // MARK: deleting a download

    /// Send downloads to the Trash (after the owner's yes). Only songs and videos that
    /// were downloaded can go this way: the engine refuses anything from the owner's
    /// own files. A deleted download can be put back from the Trash, or downloaded again.
    func deleteDownloads(_ tracks: [Track]) {
        let paths = tracks.filter(\.isDownload).map(\.path)
        guard !paths.isEmpty else { return }
        // The one that's playing stops first: it's about to go.
        if let current = player.current, paths.contains(current.path) { player.stop() }
        Task {
            do {
                _ = try await run(plan: "remove", ["paths": paths])
            } catch {
                notice = error.localizedDescription
                try? await load()
            }
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
        lyrics.videoTiming = .none
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
            var found: TrackLyrics?
            if track.isVideo, let asked = videoLyricsRequest(for: track, full: false) {
                // A saved video: lyrics already timed to it (by Karaoke, earlier), if any.
                found = try? await connection.call(
                    "lyrics.for_video", asked.params, as: TrackLyrics.self)
            }
            if found?.synced == nil, let lookUpId {
                var asked: [String: Any] = ["title": track.title, "video_id": lookUpId]
                if let artist = track.artist { asked["artist"] = artist }
                if let album = track.album { asked["album"] = album }
                if let length = track.durationS { asked["duration_s"] = length }
                found = try? await connection.call("lyrics.find", asked, as: TrackLyrics.self)
            } else if found == nil {
                found = try? await connection.call(
                    "library.lyrics", ["path": track.path], as: TrackLyrics.self)
            }
            // The song changed meanwhile, or lyrics timed to its video are already up.
            guard lyrics.trackPath == track.path, lyrics.forVideo == nil else { return }
            let lines = found?.synced.map(LRC.parse) ?? []
            if !lines.isEmpty {
                lyrics.show(.synced(lines), for: track.path, how: found?.how)
                lyrics.follow(player.clock.time)
            } else if let plain = found?.plain, !plain.isEmpty {
                lyrics.show(.plain(plain), for: track.path)
            } else {
                lyrics.show(.missing, for: track.path)
            }
        }
    }

    /// What `lyrics.for_video` is asked for the video that's playing: a song's video
    /// from YouTube (`showing`), or a saved video played from the library. `full` is
    /// the Karaoke button; without it the engine asks YouTube nothing.
    private func videoLyricsRequest(
        for track: Track, showing: ShowingVideo? = nil, full: Bool
    ) -> (videoId: String, params: [String: Any])? {
        guard let artist = track.artist ?? track.albumArtist else { return nil }
        var asked: [String: Any] = ["title": track.title, "artist": artist, "full": full]
        if track.isVideo, let videoId = track.sourceId {
            asked["video_id"] = videoId
            asked["video_path"] = track.path
            if let length = track.durationS { asked["video_duration_s"] = length }
            return (videoId, asked)
        }
        guard let showing else { return nil }
        asked["video_id"] = showing.source.videoId
        asked["video_duration_s"] = showing.source.length
        if track.videoId == nil { asked["song_path"] = track.path }  // a library song
        if let songId = track.sourceId { asked["song_video_id"] = songId }
        if let length = track.durationS { asked["song_duration_s"] = length }
        return (showing.source.videoId, asked)
    }

    /// A song's video has started. A video is rarely the song second for second, so the
    /// song's timed lyrics may be out of step with it. What's done by itself costs
    /// YouTube nothing: lyrics already timed to this video (by Karaoke, on an earlier
    /// play) are shown; otherwise the song's own stay, lit up only if the video is as
    /// long as the song. Lining them up properly is the Karaoke button (`karaoke`).
    private func showLyrics(forVideo showing: ShowingVideo?) {
        guard let track = player.current else { return }
        guard let showing else {
            lyrics.videoTiming = .none
            if lyrics.forVideo != nil { showLyrics(for: track) }  // the song's own again
            return
        }
        let videoId = showing.source.videoId
        guard lyrics.forVideo != videoId else { return }  // already timed to this video
        guard let connection = engine?.connection,
            let asked = videoLyricsRequest(for: track, showing: showing, full: false)
        else { return }
        Task {
            let found = try? await connection.call(
                "lyrics.for_video", asked.params, as: TrackLyrics.self)
            guard player.video?.source.videoId == videoId, player.current == track else { return }
            let lines = found?.synced.map(LRC.parse) ?? []
            guard !lines.isEmpty else { return }  // nothing known: the song's words stay
            lyrics.show(.synced(lines), for: track.path, forVideo: videoId, how: found?.how)
            lyrics.follow(player.clock.time)
        }
    }

    /// The Karaoke button: line the lyrics up with the video that's playing, whatever it
    /// takes. The engine reads the video's sound and its captions from YouTube (once:
    /// the answer is kept, so the same video is in time by itself from then on).
    func karaoke() {
        guard let track = player.current, let connection = engine?.connection,
            lyrics.videoTiming != .working,
            let asked = videoLyricsRequest(for: track, showing: player.video, full: true)
        else { return }
        let saved = track.isVideo
        lyrics.videoTiming = .working
        Task {
            let found = try? await connection.call(
                "lyrics.for_video", asked.params, as: TrackLyrics.self)
            guard player.current == track,
                saved || player.video?.source.videoId == asked.videoId
            else { return }
            let lines = found?.synced.map(LRC.parse) ?? []
            guard !lines.isEmpty else {
                lyrics.videoTiming = .failed
                return
            }
            lyrics.show(
                .synced(lines), for: track.path, forVideo: saved ? nil : asked.videoId,
                how: found?.how)
            lyrics.videoTiming = .none
            lyrics.follow(player.clock.time)
        }
    }

    /// The lyrics on screen were lined up with the video that's playing (by its sound
    /// or its captions), not just taken as the song's.
    var lyricsFitVideo: Bool {
        guard let track = player.current, lyrics.trackPath == track.path,
            ["audio", "captions", "caption_text"].contains(lyrics.how ?? "")
        else { return false }
        return track.isVideo || lyrics.forVideo == player.video?.source.videoId
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
    /// How the engine timed them to a video ("audio", "captions"…), when it did.
    private(set) var how: String?
    private(set) var currentLine: Int?
    /// False while the lyrics' times don't fit what's playing (a music video that isn't
    /// the song second for second): no line is lit up then.
    var timed = true {
        didSet { if !timed { currentLine = nil } }
    }
    /// How timing the lyrics to the video that's playing is going.
    var videoTiming = VideoTiming.none

    enum VideoTiming {
        case none, working, failed
    }

    /// There are lyrics to show right now: a song is on, and its words were found.
    var hasLyrics: Bool {
        switch state {
        case .synced, .plain: true
        default: false
        }
    }

    func show(
        _ state: State, for path: String?, forVideo videoId: String? = nil, how: String? = nil
    ) {
        self.state = state
        trackPath = path
        forVideo = videoId
        self.how = how
        currentLine = nil
    }

    /// Called a few times a second; only changes anything when the line changes.
    func follow(_ time: Double) {
        guard timed, case .synced(let lines) = state else { return }
        let line = LRC.current(at: time, in: lines)
        if line != currentLine { currentLine = line }
    }
}
