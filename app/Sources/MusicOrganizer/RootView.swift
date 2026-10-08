import MusicOrganizerKit
import SwiftUI

struct RootView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        switch model.phase {
        case .starting:
            ProgressView("Starting…")
        case .loading:
            LoadingView()
        case .needsLibrary:
            Message(
                symbol: "music.note.house", title: "Where is your library?",
                text: "Choose the Music Organizer library folder: the one with the Music folder inside."
            ) {
                Button("Choose Library Folder…") { model.chooseLibrary() }
                    .buttonStyle(.borderedProminent)
            }
        case .failed(let message):
            Message(symbol: "exclamationmark.triangle", title: "Something's not right", text: message) {
                Button("Try Again") { model.retry() }
                    .buttonStyle(.borderedProminent)
                Button("Choose Library Folder…") { model.chooseLibrary() }
            }
        case .ready:
            MainView()
        }
    }
}

/// "Reading your library…", with a hint if it goes on for long.
private struct LoadingView: View {
    @State private var slow = false

    var body: some View {
        VStack(spacing: 12) {
            ProgressView("Reading your library…")
            if slow {
                Text(
                    "Still waiting. If macOS is asking whether Music Organizer may use the folder "
                        + "your library is in, choose Allow. New songs are also read once, which "
                        + "can take a minute after a big import.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 420)
            }
        }
        .task {
            try? await Task.sleep(for: .seconds(6))
            slow = true
        }
    }
}

struct Message<Buttons: View>: View {
    let symbol: String
    let title: String
    let text: String
    @ViewBuilder let buttons: Buttons

    var body: some View {
        VStack(spacing: 14) {
            Image(systemName: symbol)
                .font(.system(size: 44))
                .foregroundStyle(.secondary)
            Text(title).font(.title2.weight(.semibold)).heading()
            Text(text)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
                .frame(maxWidth: 460)
            HStack { buttons }
                .padding(.top, 6)
        }
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

/// What the sidebar can show.
enum SidebarItem: Hashable {
    case songs, albums, artists, videos
    case favourites, recentlyAdded, mostPlayed, unconfirmed
    case visualizer, musicExplore, importPlaylists, youtube, youtubeQueue, downloads
    /// The owner's drawing of 2026-10-07: videos and movies beside the music.
    case channels, movies, videoFinder, videoExplore, movieFinder
    /// The owner's drawing of 2026-10-08: Home at the top of Media Discovery, and series
    /// in a Finder of their own.
    case home, seriesFinder
    /// Settings: a page like the others, opened by the cog wheel or ⌘, (no row of its own).
    case settings
    case playlist(String)

    /// A name for this entry that can be saved, and read back with `init(key:)`.
    var key: String {
        switch self {
        case .playlist(let id): "playlist:\(id)"
        case .visualizer: "visualizer"
        case .musicExplore: "musicExplore"
        case .settings: "settings"
        case .channels: "channels"
        case .movies: "movies"
        case .videoFinder: "videoFinder"
        case .videoExplore: "videoExplore"
        case .movieFinder: "movieFinder"
        case .home: "home"
        case .seriesFinder: "seriesFinder"
        case .importPlaylists: "import"
        case .youtube: "youtube"
        case .youtubeQueue: "youtubeQueue"
        case .downloads: "downloads"
        default: libraryName ?? "songs"
        }
    }

    init(key: String) {
        if key.hasPrefix("playlist:") {
            self = .playlist(String(key.dropFirst(9)))
            return
        }
        switch key {
        case "visualizer": self = .visualizer
        // What's New and Find were pages of their own until 2026-10-07: one page now.
        case "whatsNew", "find", "musicExplore": self = .musicExplore
        case "settings": self = .settings
        case "channels": self = .channels
        case "movies": self = .movies
        case "videoFinder": self = .videoFinder
        case "videoExplore": self = .videoExplore
        case "movieFinder": self = .movieFinder
        case "home": self = .home
        case "seriesFinder": self = .seriesFinder
        case "import": self = .importPlaylists
        // Discover → Artist was a page of its own for a few hours; it's the Artists page now.
        case "artistInfo": self = .artists
        case "youtube": self = .youtube
        case "youtubeQueue": self = .youtubeQueue
        case "downloads": self = .downloads
        default: self = SidebarItem(libraryEntry: key) ?? .songs
        }
    }

    /// The name this Library entry is saved under (`SidebarChoice`).
    var libraryName: String? {
        SidebarChoice.all.first { SidebarItem(libraryEntry: $0) == self }
    }

    /// The Library entry saved under this name (`SidebarChoice`).
    init?(libraryEntry name: String) {
        switch name {
        case "songs": self = .songs
        case "albums": self = .albums
        case "artists": self = .artists
        case "videos": self = .videos
        case "favourites": self = .favourites
        case "mostPlayed": self = .mostPlayed
        case "recentlyAdded": self = .recentlyAdded
        case "unconfirmed": self = .unconfirmed
        default: return nil
        }
    }

    var title: String {
        switch self {
        case .songs: "Songs"
        case .albums: "Albums"
        case .artists: "Artists"
        case .videos: "Music Videos"
        case .favourites: "Favourites"
        case .recentlyAdded: "Recently Added"
        case .mostPlayed: "Most Played"
        case .unconfirmed: "Not Identified Yet"
        case .musicExplore, .videoExplore: "Explore"
        case .settings: "Settings"
        case .channels: "Channel"
        case .movies: "Movies"
        case .videoFinder: "Video Finder"
        case .movieFinder: "Movie Finder"
        case .home: "Home"
        case .seriesFinder: "Series Finder"
        case .importPlaylists: "Import Playlists"
        case .youtube: "Music Finder"
        case .youtubeQueue: "Queue"
        case .visualizer: "Visualizer"
        case .downloads: "Downloads"
        case .playlist: "Playlist"
        }
    }

    var symbol: String {
        switch self {
        case .songs: "music.note"
        case .albums: "square.stack"
        case .artists: "music.mic"
        case .videos: "film"
        case .favourites: "heart"
        case .recentlyAdded: "clock"
        case .mostPlayed: "chart.bar"
        case .unconfirmed: "questionmark.circle"
        case .musicExplore, .videoExplore: "sparkles"
        case .settings: "gearshape"
        case .channels: "play.tv"
        case .movies: "popcorn"
        case .videoFinder: "play.rectangle.on.rectangle"
        case .movieFinder: "movieclapper"
        case .home: "house"
        case .seriesFinder: "tv"
        case .importPlaylists: "square.and.arrow.down.on.square"
        case .youtube: "magnifyingglass"
        case .youtubeQueue: "text.append"
        case .visualizer: "waveform"
        case .downloads: "arrow.down.circle"
        case .playlist: "music.note.list"
        }
    }
}

struct MainView: View {
    @Environment(AppModel.self) private var model
    @State private var item: SidebarItem? = SidebarItem(
        key: UserDefaults.standard.string(forKey: "lastSection") ?? "songs")
    /// The pages opened so far. They're kept, hidden, when another is chosen, so each
    /// one is exactly as it was left: scrolled to the same place, with the same
    /// selection and sort (Fix A-1).
    @State private var visited: [SidebarItem] = []
    /// What's been opened inside each page (an album, an artist), page by page.
    @State private var paths: [SidebarItem: NavigationPath] = [:]
    @State private var showNowPlaying = false
    /// The page being made ready ahead of its first click, if one is (`warmUp`).
    @State private var warming: SidebarItem?
    @State private var deleting: Playlist?
    @AppStorage("showLyrics") private var showLyrics = false
    /// Lyrics beside the cover or video on the player page (Settings → Play Options).
    @AppStorage(Player.visualizerLyricsKey) private var visualizerLyrics = true
    /// The Library entries the owner keeps in the sidebar, in order.
    @AppStorage("sidebarLibrary") private var savedEntries = SidebarChoice.write(SidebarChoice.all)
    /// The Library entries the owner has been offered so far: a new one is shown once.
    @AppStorage("sidebarSeen") private var seenEntries: String?
    // Each group of the sidebar folds away, and stays as it was left.
    @AppStorage("openLibrary") private var openLibrary = true
    @AppStorage("openMedia") private var openMedia = true
    @AppStorage("openDiscover") private var openDiscover = true
    @AppStorage("openPlaylists") private var openPlaylists = true

    var body: some View {
        @Bindable var model = model
        VStack(spacing: 0) {
            Group {
                NavigationSplitView {
                    VStack(spacing: 0) {
                        sidebar
                        StatusFooter()
                    }
                    .background(Theme.current.sidebar)
                    // Search sits beside the sidebar button (the owner's drawing,
                    // 2026-10-07). It stays there when the sidebar is shut: macOS moves
                    // both to the left of the top bar together.
                    .toolbar {
                        ToolbarItem { searchButton }
                    }
                    // Its width is its own: a page that wants more room never squeezes it
                    // (owner, 2026-10-03). Only the sidebar button hides it.
                    .navigationSplitViewColumnWidth(min: 220, ideal: 220, max: 300)
                } detail: {
                    NavigationStack(path: pathBinding) {
                        pagesWithLyrics
                            // No page's name in the top bar (owner, 2026-10-07): each
                            // page says what it is itself.
                            .navigationTitle("")
                            .navigationDestination(for: Album.self) {
                                AlbumPage(album: $0).environment(model).belowTitleBar()
                            }
                    }
                }
                // The Warm Look's glow shows through the title bar.
                .toolbarBackground(Theme.current.isWarm ? .hidden : .automatic, for: .windowToolbar)
                .onChange(of: item, initial: true) { opened(current) }
                // The library's own pages are made ready just after the app opens, one at
                // a time, so the first click on each finds it built (see `warmUp`).
                .task(id: model.phase) { await warmUp() }
                .onAppear {
                    let caughtUp = SidebarChoice.catchUp(saved: savedEntries, seen: seenEntries)
                    if caughtUp.seen != seenEntries {
                        savedEntries = caughtUp.entries
                        seenEntries = caughtUp.seen
                    }
                }
                .onChange(of: model.searchText) { paths[current] = NavigationPath() }
                // A page asked for from elsewhere (Artist Info on a song). `initial`: one
                // asked for while the library was still being read (Settings, from the
                // menu) is opened now; left waiting, it made every later ask do nothing.
                .onChange(of: model.goTo, initial: true) {
                    guard let wanted = model.goTo else { return }
                    model.goTo = nil
                    showNowPlaying = false
                    item = wanted
                }
                .onChange(of: model.listening.playlists) { forgetDeletedPlaylists() }
            }
            // An overlay, so the big cover and lyrics can never change the window's layout.
            .overlay {
                if showNowPlaying {
                    NowPlayingView(isShown: $showNowPlaying)
                        .environment(model)
                        .ignoresSafeArea(.container, edges: .top)  // under the title bar too
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                }
            }
            .toolbar(
                // A film, like the big cover, has the top of the window too.
                showNowPlaying || model.pictureFullScreen || model.film.isOpen ? .hidden : .automatic,
                for: .windowToolbar)
            // Below the split view, not an inset: the sidebar runs the window's full height
            // and would otherwise sit underneath the bar.
            // The lyrics button switches the lyrics of the page that's showing. On the
            // player page that's only for now: the setting stays as chosen in Settings.
            PlayerBar(
                showLyrics: onPlayerPage
                    ? Binding(
                        get: { model.shows(Player.visualizerLyricsKey, setting: visualizerLyrics) },
                        set: {
                            model.switchForNow(
                                Player.visualizerLyricsKey, to: $0, setting: visualizerLyrics)
                        })
                    : $showLyrics,
                lyricsForPlayerPage: onPlayerPage, showNowPlaying: $showNowPlaying)
        }
        // The Warm Look's glow, behind everything, from the very top of the window: it
        // shows through the title bar and the top of the page (the sidebar covers it).
        .background { CoverWash().environment(model).ignoresSafeArea() }
        // Over everything, the player bar included: the whole screen's own controls take
        // over.
        .overlay {
            if model.film.isOpen {
                // A film has the whole window while it plays.
                FilmPlayerView().environment(model)
            } else if model.pictureFullScreen {
                FullScreenPicture().environment(model)
            }
        }
        .animation(.easeInOut(duration: 0.25), value: showNowPlaying)
        .modifier(PlayerPageOpening(shown: $showNowPlaying, onVisualizer: current == .visualizer))
        .sheet(item: $model.namePrompt) { NameSheet(prompt: $0).dressed() }
        .sheet(item: $model.editing) { EditSheet(track: $0).environment(model).dressed() }
        .alert(
            "That didn't work", isPresented: Binding(
                get: { model.notice != nil }, set: { if !$0 { model.notice = nil } })
        ) {
            Button("OK") {}
        } message: {
            Text(model.notice ?? "")
        }
        .alert(
            model.info?.title ?? "", isPresented: Binding(
                get: { model.info != nil }, set: { if !$0 { model.info = nil } })
        ) {
            Button("OK") {}
        } message: {
            Text(model.info?.text ?? "")
        }
        .confirmationDialog(
            Self.deleteQuestion(model.deletingDownloads ?? []),
            isPresented: Binding(
                get: { model.deletingDownloads != nil },
                set: { if !$0 { model.deletingDownloads = nil } })
        ) {
            Button("Move to Trash", role: .destructive) {
                if let tracks = model.deletingDownloads { model.deleteDownloads(tracks) }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text(
                "It goes to the Trash with its lyrics, and comes off your playlists and "
                    + "favourites. You can download it again.")
        }
        // Several downloads at once are a batch: the plan is shown before anything is queued.
        .alert(
            model.batch.map { "Download \($0.count) \($0.count == 1 ? "song" : "songs")?" } ?? "",
            isPresented: Binding(
                get: { model.batch != nil }, set: { if !$0 { model.batch = nil } })
        ) {
            Button("Download") { model.confirmBatch() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text(model.batch.map(Self.batchNote) ?? "")
        }
        .confirmationDialog(
            "Delete the playlist “\(deleting?.name ?? "")”?",
            isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } })
        ) {
            Button("Delete Playlist", role: .destructive) {
                if let deleting {
                    if item == .playlist(deleting.id) { item = .songs }
                    model.delete(deleting)
                }
            }
        } message: {
            Text("Only the list goes. The songs stay in your library.")
        }
    }

    private var sidebar: some View {
        let shown = SidebarChoice.read(savedEntries)
        let hidden = SidebarChoice.hidden(shown)
        let waiting = model.library.unconfirmed.count
        // The rows are SidebarItems themselves, so a click selects one. (Looping over
        // their saved names instead made every Library row unclickable.)
        // Music Videos is one of these entries too, but its row is under Videos.
        let entries = shown.compactMap(SidebarItem.init(libraryEntry:))
            .filter { $0 != .unconfirmed || waiting > 0 }  // nothing waiting: nothing to show
            .filter { $0 != .videos }
        return List(selection: $item) {
            Section(isExpanded: $openLibrary) {
                ForEach(entries, id: \.self) { entry in
                    Label(entry.title, systemImage: entry.symbol)
                        .badge(entry == .unconfirmed ? waiting : 0)
                        // A download dragged here moves into the main library.
                        .dropDestination(for: String.self) { ids, _ in
                            model.moveDownloads(DraggedSongs.ids(in: ids), toLibrary: true)
                            return true
                        }
                        .contextMenu {
                            Button("Remove from Sidebar") { remove(entry, from: shown) }
                                .disabled(shown.count <= 1)
                        }
                }
            } header: {
                header("Music") {
                    Menu {
                        ForEach(hidden.filter { $0 != "videos" }, id: \.self) { name in
                            if let entry = SidebarItem(libraryEntry: name) {
                                Button(entry.title) {
                                    savedEntries = SidebarChoice.write(
                                        SidebarChoice.adding(name, to: shown))
                                }
                            }
                        }
                    } label: {
                        Image(systemName: "plus")
                    }
                    .menuStyle(.borderlessButton)
                    .menuIndicator(.hidden)
                    .fixedSize()
                    .disabled(hidden.allSatisfy { $0 == "videos" })
                    .help(
                        hidden.allSatisfy { $0 == "videos" }
                            ? "Everything is already shown" : "Add an entry to the sidebar")
                }
            }
            Section(isExpanded: $openMedia) {
                ForEach([SidebarItem.channels, .movies], id: \.self) { entry in
                    Label(entry.title, systemImage: entry.symbol)
                }
                if shown.contains("videos") {
                    Label(SidebarItem.videos.title, systemImage: SidebarItem.videos.symbol)
                        .tag(SidebarItem.videos)
                        .contextMenu {
                            Button("Remove from Sidebar") { remove(.videos, from: shown) }
                        }
                }
            } header: {
                header("Videos") {
                    Button {
                        savedEntries = SidebarChoice.write(SidebarChoice.adding("videos", to: shown))
                    } label: {
                        Image(systemName: "plus")
                    }
                    .buttonStyle(.borderless)
                    .disabled(shown.contains("videos"))
                    .help(
                        shown.contains("videos")
                            ? "Everything is already shown" : "Add Music Videos to the sidebar")
                }
            }
            Section("Media Discovery", isExpanded: $openDiscover) {
                // In the owner's order; each Explore sits under its Finder, set in a little.
                ForEach(
                    [
                        // (The Visualizer's row was taken out on 2026-10-08, until the
                        // owner has a place for it: the page is still a click on the
                        // cover in the player bar away.)
                        SidebarItem.home, .youtube, .musicExplore, .videoFinder, .videoExplore,
                        .movieFinder, .seriesFinder, .downloads,
                    ], id: \.self
                ) {
                    entry in
                    if entry == .musicExplore || entry == .videoExplore {
                        Label(entry.title, systemImage: entry.symbol).padding(.leading, 18)
                        if entry == .musicExplore, !model.youtubeQueue.isEmpty {
                            // Shown while anything is queued with Up Next.
                            Label(
                                SidebarItem.youtubeQueue.title,
                                systemImage: SidebarItem.youtubeQueue.symbol
                            )
                            .padding(.leading, 18)
                            .badge(model.youtubeQueue.count)
                            .tag(SidebarItem.youtubeQueue)
                        }
                    } else if entry == .downloads {
                        Label(entry.title, systemImage: entry.symbol)
                            .badge(model.downloaded.count + model.pending.filter(\.isActive).count)
                            // Dragged back here, a download leaves the main library's lists.
                            .dropDestination(for: String.self) { ids, _ in
                                model.moveDownloads(DraggedSongs.ids(in: ids), toLibrary: false)
                                return true
                            }
                    } else {
                        Label(entry.title, systemImage: entry.symbol)
                    }
                }
            }
            Section(isExpanded: $openPlaylists) {
                ForEach(model.listening.playlists.map { SidebarItem.playlist($0.id) }, id: \.self) {
                    entry in
                    if case .playlist(let id) = entry, let playlist = model.playlist(id) {
                        Label(playlist.name, systemImage: entry.symbol)
                            .contextMenu {
                                Button("Rename…") { model.rename(playlist) }
                                let others = model.profiles.profiles.filter {
                                    $0.id != model.profiles.currentId
                                }
                                if !others.isEmpty {
                                    Menu("Copy to Profile") {
                                        ForEach(others) { profile in
                                            Button(profile.name) {
                                                model.copyPlaylist(playlist, to: profile.id)
                                            }
                                        }
                                    }
                                }
                                Button("Delete…", role: .destructive) { deleting = playlist }
                            }
                    }
                }
                Button { model.newPlaylist() } label: {
                    Label("New Playlist…", systemImage: "plus")
                }
                .buttonStyle(.plain)
                .foregroundStyle(.secondary)
                Label(SidebarItem.importPlaylists.title, systemImage: SidebarItem.importPlaylists.symbol)
                    .tag(SidebarItem.importPlaylists)
            } header: {
                header("Playlists") {
                    Button { model.newPlaylist() } label: { Image(systemName: "plus") }
                        .buttonStyle(.borderless)
                        .help("New playlist")
                }
            }
        }
        .listStyle(.sidebar)
        .scrollContentBackground(Theme.current.listBackground)
    }

    private func header(_ title: String, @ViewBuilder button: () -> some View) -> some View {
        HStack {
            Text(title)
            Spacer()
            button()
        }
        .padding(.trailing, 6)
    }

    private func remove(_ entry: SidebarItem, from shown: [String]) {
        guard let name = entry.libraryName else { return }
        savedEntries = SidebarChoice.write(shown.filter { $0 != name })
        if item == entry {
            item = shown.first { $0 != name }.flatMap(SidebarItem.init(libraryEntry:))
        }
    }

    private var current: SidebarItem { item ?? .songs }

    /// A page that shows the lyrics itself: the Local Visualizer, or the big now-playing.
    private var onPlayerPage: Bool { current == .visualizer || showNowPlaying }

    /// The lyrics panel isn't a fixture: it's there only while a song with lyrics is on,
    /// and never beside a page that shows the lyrics itself.
    private var lyricsPanelShown: Bool {
        showLyrics && model.lyrics.hasLyrics && model.player.current != nil
            && current != .visualizer && !showNowPlaying
    }

    /// The pages, with the lyrics beside them when there are lyrics to show. The panel
    /// is put there in one step: sliding it in made the song table lay itself out again
    /// for every frame of the slide, which looked like a glitch.
    private var pagesWithLyrics: some View {
        HStack(spacing: 0) {
            pages
            if lyricsPanelShown {
                Divider()
                LyricsView()
                    .environment(model)
                    .frame(width: 320)
                    .background(Theme.current.panel)
            }
        }
        .transaction { $0.animation = nil }
    }

    private var pathBinding: Binding<NavigationPath> {
        Binding(
            get: { paths[current] ?? NavigationPath() },
            set: { paths[current] = $0 })
    }

    /// Search lives behind this button, so no page carries a search field it isn't using.
    private var searchButton: some View {
        Button {
            model.searching.toggle()
        } label: {
            Image(systemName: "magnifyingglass")
                .foregroundStyle(model.searching ? Color.accentColor : .primary)
        }
        .keyboardShortcut("f")
        .help(model.searching ? "Close search" : "Search your library (⌘F)")
    }

    /// Building a song list for the first time stops the app for about a second (its
    /// table makes a few hundred small views). Left to the first click, that's a second's
    /// wait on every list the owner opens. So once the library has loaded, the lists are
    /// built ahead, out of sight like any page that's been visited, one every second or
    /// so, so no two builds run together and a click in between is still answered.
    /// Only the library's own pages: a page that asks the web for something when it
    /// opens (Explore, the Finders) is never opened ahead.
    private func warmUp() async {
        guard model.phase == .ready, !Snapshot.isOn else { return }
        let shown = SidebarChoice.read(savedEntries).compactMap(SidebarItem.init(libraryEntry:))
        for entry in shown + [.downloads] where entry != .unconfirmed {
            try? await Task.sleep(for: .seconds(1.2))
            if Task.isCancelled { return }
            guard !visited.contains(entry) else { continue }
            visited.append(entry)
            warming = entry
            // Long enough for the page to be laid out and its rows built, then it's
            // parked far off with the other pages that aren't showing.
            try? await Task.sleep(for: .seconds(1.5))
            if warming == entry { warming = nil }
        }
    }

    private func opened(_ entry: SidebarItem) {
        if !visited.contains(entry) { visited.append(entry) }
        UserDefaults.standard.set(entry.key, forKey: "lastSection")
    }

    private func forgetDeletedPlaylists() {
        visited.removeAll { entry in
            if case .playlist(let id) = entry { model.playlist(id) == nil } else { false }
        }
    }

    /// Every page opened so far, one on top of the other, with only the chosen one
    /// showing. A hidden page keeps its place but does no work.
    private var pages: some View {
        VStack(spacing: 0) {
            if model.searching, current != .youtube, current != .visualizer, current != .settings {
                SearchBar()
                Divider()
            }
            stackedPages
        }
    }

    private var stackedPages: some View {
        GeometryReader { room in
            stack(pageWidth: room.size.width)
        }
    }

    private func stack(pageWidth: CGFloat) -> some View {
        ZStack {
            ForEach(visited, id: \.self) { entry in
                let active = entry == current
                // A song list works its rows out only when it's the page showing, so one
                // being made ready ahead is told it is, for that moment.
                page(entry, active: active || entry == warming)
                    // A page that isn't showing is moved far out of sight, not made
                    // invisible: for an invisible view SwiftUI takes its AppKit views
                    // (a whole song table) out of the window and puts them all back
                    // when it shows again, which froze the app on every switch.
                    // (A page being made ready ahead sits almost a whole page-width to
                    // the right for a moment, with two points of its left edge still in
                    // view: a table builds its rows only for what's in view, and a
                    // sliver its full height is enough for every row on the first
                    // screen. Wholly out of view, far off or just beside, it builds
                    // nothing until it's shown: tried both, 2026-10-07.)
                    .offset(x: active ? 0 : entry == warming ? max(pageWidth - 2, 0) : 30_000)
                    .allowsHitTesting(active)
                    .accessibilityHidden(!active)
                // No zIndex either: changing which page is on top also made AppKit take
                // every page's views out and put them back (profiled 2026-10-01).
            }
        }
        .clipped()
    }

    private static func deleteQuestion(_ tracks: [Track]) -> String {
        if tracks.count == 1, let only = tracks.first {
            return "Delete “\(only.title)”\(only.isVideo ? " (the video)" : "")?"
        }
        return "Delete \(tracks.count) downloads?"
    }

    /// What a batch of downloads will take, said before the owner agrees to it.
    private static func batchNote(_ batch: AppModel.BatchDownload) -> String {
        var note = "It takes \(roughTime(minutes: batch.minutes)), paced so the service doesn't refuse "
            + "this Mac. The songs show up under Discover → Downloads as they arrive."
        if batch.days > 1 {
            note += " Your daily limit spreads them over \(batch.days) days."
        }
        return note
    }

    @ViewBuilder
    private func page(_ entry: SidebarItem, active: Bool) -> some View {
        switch entry {
        case .songs:
            SongList(source: .all, title: "Songs", empty: "No songs in the library yet.", isActive: active)
        case .albums:
            AlbumsView()
        case .artists:
            ArtistsPage()
        case .favourites:
            SongList(
                source: .favourites, title: "Favourites",
                empty: "Click the heart beside a song and it shows up here.", isActive: active)
        case .recentlyAdded:
            SongList(
                source: .recentlyAdded, title: "Recently Added",
                empty: "Nothing has been added yet.", isActive: active)
        case .mostPlayed:
            SongList(
                source: .mostPlayed, title: "Most Played",
                empty: "Songs you play all the way through are counted and show up here.",
                isActive: active)
        case .unconfirmed:
            SongList(
                source: .unconfirmed, title: "Not Identified Yet",
                empty: "Every song has been identified.",
                note: "These songs are here under their own names, so you can play them. "
                    + "They get their official names, covers and lyrics once they're identified.",
                isActive: active)
        case .videos:
            SongList(
                source: .videos, title: "Videos",
                empty: model.keepDownloadsSeparate
                    ? "Saved videos are under Discover → Downloads. Settings → Downloads "
                        + "(All Library) lists them here instead."
                    : "Videos you save show up here. Play a song on the Local Visualizer, "
                        + "switch to Video, and click Save Video.",
                isActive: active)
        case .youtube:
            YouTubeSearchView()
        case .youtubeQueue:
            YouTubeQueueView()
        case .visualizer:
            // The song that's playing, with its cover and lyrics: the player's own tab
            // (parked Fix A-3 will give it the owner's layout).
            NowPlayingView(isShown: .constant(true), closable: false, isActive: active)
        case .downloads:
            VStack(spacing: 0) {
                PendingDownloads()
                DownloadsByGenre()
            }
        case .settings:
            SettingsView()
        case .musicExplore:
            MusicExploreView()
        case .videoExplore:
            VideoExploreView()
        case .movieFinder:
            MovieFinderView(kind: "movie")
        case .seriesFinder:
            MovieFinderView(kind: "series")
        case .home:
            HomeView()
        case .videoFinder:
            VideoFinderView()
        case .channels:
            ChannelsView()
        case .movies:
            MoviesView()
        case .importPlaylists:
            ImportView()
        case .playlist(let id):
            if let playlist = model.playlist(id) {
                SongList(
                    source: .playlist(id), title: playlist.name,
                    empty: "Right-click any song and choose Add to Playlist.", isActive: active)
            } else {
                Text("That playlist has gone.").foregroundStyle(.secondary)
            }
        }
    }
}

/// The library search field, shown under the toolbar when the magnifying glass is on.
private struct SearchBar: View {
    @Environment(AppModel.self) private var model
    @FocusState private var focused: Bool

    var body: some View {
        @Bindable var model = model
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
            TextField("Search your songs, artists and albums", text: $model.searchText)
                .textFieldStyle(.plain)
                .focused($focused)
                .onExitCommand { model.searching = false }
            if !model.searchText.isEmpty {
                Button { model.searchText = "" } label: { Image(systemName: "xmark.circle.fill") }
                    .buttonStyle(.plain)
                    .foregroundStyle(.secondary)
                    .help("Clear")
            }
            Button("Done") { model.searching = false }
                .controlSize(.small)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .onAppear { focused = true }
    }
}

/// A small sheet that asks for one name.
private struct NameSheet: View {
    let prompt: NamePrompt
    @State private var name = ""
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(prompt.title).font(.headline)
            TextField("Name", text: $name)
                .textFieldStyle(.roundedBorder)
                .frame(width: 300)
                .onSubmit(finish)
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button(prompt.button, action: finish)
                    .keyboardShortcut(.defaultAction)
                    .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(20)
        .onAppear { name = prompt.name }
    }

    private func finish() {
        let trimmed = name.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return }
        prompt.done(trimmed)
        dismiss()
    }
}

/// How much of the daily download limit is used, always in view: "12/250", green while
/// there's plenty left, orange from three fifths of it, red from 85%.
private struct DailyLimitCounter: View {
    let use: DailyUse

    var body: some View {
        HStack(spacing: 5) {
            Circle().fill(colour).frame(width: 8, height: 8)
            Text(use.text)
                .font(.callout.weight(.semibold))
                .monospacedDigit()
                .foregroundStyle(colour)
            Text("downloads today").foregroundStyle(.secondary)
        }
        .help(use.explained)
        .accessibilityElement(children: .combine)
    }

    private var colour: Color {
        switch use.level {
        case .safe: .green
        case .middling: .orange
        case .nearlyOut: .red
        }
    }
}

/// The bottom of the sidebar: what's in the library, and what's still waiting.
private struct StatusFooter: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        HStack(alignment: .bottom) {
        VStack(alignment: .leading, spacing: 3) {
            if model.profiles.profiles.count > 1 {
                // Whose music this is, once there's more than one person. Click to switch.
                OpenSettings {
                    Label(model.profiles.current.name, systemImage: "person.crop.circle")
                        .font(.callout.weight(.medium))
                }
                .buttonStyle(.plain)
                .simultaneousGesture(
                    TapGesture().onEnded {
                        UserDefaults.standard.set("profile", forKey: SettingsView.tabKey)
                    }
                )
                .help("The profile in use. Click to switch (Settings → Profiles).")
                .padding(.bottom, 3)
            }
            if let use = model.dailyUse {
                DailyLimitCounter(use: use)
                    .padding(.bottom, 3)
            }
            Text("\(model.library.tracks.count.formatted()) songs")
            if let status = model.status, status.waitingForReview > 0 {
                Text("\(status.waitingForReview.formatted()) waiting for review")
                    .foregroundStyle(.secondary)
            }
            if let version = model.engineVersion {
                Text("Engine \(version)").foregroundStyle(.tertiary)
            }
        }
        Spacer()
        OpenSettings {
            Image(systemName: "gearshape").font(.title3)
        }
        .buttonStyle(.plain)
        .foregroundStyle(.secondary)
        .help("Settings")
        }
        .font(.caption)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
    }
}

/// Two things about the big player page covering the window, kept out of `RootView`'s
/// body, which is as much as the compiler will take in one piece.
private struct PlayerPageOpening: ViewModifier {
    @Environment(AppModel.self) private var model
    @Binding var shown: Bool
    let onVisualizer: Bool

    func body(content: Content) -> some View {
        content
            // Settings → Play Options: playing something opens the Visualizer page.
            .onChange(of: model.player.playsAsked) {
                if UserDefaults.standard.bool(forKey: Player.opensVisualizerKey),
                    !onVisualizer, !model.film.isOpen
                {
                    shown = true
                }
            }
            // A search box left with the typing cursor would show its outline through
            // the page that has just covered it.
            .onChange(of: shown) { _, isShown in
                if isShown {
                    NSApp.keyWindow?.makeFirstResponder(nil)
                    model.searching = false
                }
            }
    }
}
