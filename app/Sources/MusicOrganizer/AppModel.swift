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

    // MARK: lyrics

    private func showLyrics(for track: Track?) {
        guard let track else {
            lyrics.show(.nothingPlaying, for: nil)
            return
        }
        guard track.lyrics != .none, let connection = engine?.connection else {
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
