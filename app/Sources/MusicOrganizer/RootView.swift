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
    /// Anime in a Finder of its own, beside series (owner, 2026-10-08).
    case animeFinder
    /// The lists of add-ons for adults only, in a Finder of their own (owner, 2026-10-08).
    case adultFinder
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
        case .animeFinder: "animeFinder"
        case .adultFinder: "adultFinder"
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
        // … and since 2026-10-08 that page is Music Finder itself.
        case "whatsNew", "find", "musicExplore": self = .youtube
        case "settings": self = .settings
        case "channels": self = .channels
        case "movies": self = .movies
        case "videoFinder": self = .videoFinder
        case "videoExplore": self = .videoFinder  // Explore is Video Finder itself now
        case "animeFinder": self = .animeFinder
        case "adultFinder": self = .adultFinder
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
        case .animeFinder: "Anime Finder"
        case .adultFinder: "Porn Finder"
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
        case .animeFinder: "sparkles.tv"
        case .adultFinder: "eye.slash"
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
    /// Each of those pages as a view of its own (`PageHost`).
    @State private var store = PageStore<SidebarItem>()
    /// What's been opened inside each page (an album, an artist), page by page.
    @State private var paths: [SidebarItem: NavigationPath] = [:]
    @State private var showNowPlaying = false
    @State private var deleting: Playlist?
    @AppStorage("showLyrics") private var showLyrics = false
    /// Lyrics beside the cover or video on the player page (Settings → Play Options).
    @AppStorage(Player.visualizerLyricsKey) private var visualizerLyrics = true
    /// The Library entries the owner keeps in the sidebar, in order.
    @AppStorage("sidebarLibrary") private var savedEntries = SidebarChoice.write(SidebarChoice.all)
    /// The Library entries the owner has been offered so far: a new one is shown once.
    @AppStorage("sidebarSeen") private var seenEntries: String?
    /// The rows outside Music the owner has taken out of the sidebar.
    @AppStorage(SidebarRows.key) private var savedHidden = ""
    @State private var customising = false
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
                    // Before it (the owner, 2026-10-08): Customise Sidebar, and the
                    // Visualizer, which had a row in the sidebar until then.
                    .toolbar {
                        // One item, set close: three separate ones don't fit beside
                        // the sidebar's button, and macOS folds the last away.
                        ToolbarItem {
                            HStack(spacing: 10) {
                                customiseButton
                                visualizerButton
                                searchButton
                            }
                            .buttonStyle(.plain)
                            .padding(.horizontal, 4)
                        }
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
                // (The library's lists were built ahead here, one by one, just after the
                // app opened, when a list took a second to build. With AppKit's table a
                // long list opens in under half a second, and building six pages ahead
                // kept the app busy for three seconds of its first half minute, when the
                // first clicks are made: taken out 2026-10-09.)
                .task(id: model.phase) { await bench() }
                .onAppear {
                    let caughtUp = SidebarChoice.catchUp(saved: savedEntries, seen: seenEntries)
                    if caughtUp.seen != seenEntries {
                        savedEntries = caughtUp.entries
                        seenEntries = caughtUp.seen
                    }
                }
                .onChange(of: model.searchText) { paths[current] = NavigationPath() }
                // An album clicked on a page: opened over the page it was clicked on.
                .onChange(of: model.openedAlbum) {
                    guard let album = model.openedAlbum else { return }
                    model.openedAlbum = nil
                    paths[current, default: NavigationPath()].append(album)
                }
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
        .modifier(AddingAgainQuestion())
        .modifier(DeletingQuestions())
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
                    row(entry)
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
                ForEach([SidebarItem.channels, .movies].filter { !hiddenRows.contains($0.key) }, id: \.self) {
                    entry in
                    row(entry)
                        .contextMenu { Button("Remove from Sidebar") { hide(entry) } }
                }
                if shown.contains("videos") {
                    row(.videos)
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
                // In the owner's order. Each Explore was a row under its Finder until
                // 2026-10-08: it's what the Finder shows now when nothing is searched for.
                // (The Visualizer has a button in the top bar instead of a row.)
                ForEach(
                    [
                        SidebarItem.home, .youtube, .videoFinder, .movieFinder, .seriesFinder,
                        .animeFinder, .adultFinder, .downloads,
                    ].filter { !hiddenRows.contains($0.key) }
                        // Never in a child's profile, whatever the sidebar's choices say.
                        .filter { $0 != .adultFinder || !model.profiles.current.isChild },
                    id: \.self
                ) {
                    entry in
                    if entry == .downloads {
                        row(entry)
                            .badge(model.downloadsBadge)
                            // Dragged back here, a download leaves the main library's lists.
                            .dropDestination(for: String.self) { ids, _ in
                                model.moveDownloads(DraggedSongs.ids(in: ids), toLibrary: false)
                                return true
                            }
                            .contextMenu { Button("Remove from Sidebar") { hide(entry) } }
                    } else {
                        row(entry)
                            .contextMenu { Button("Remove from Sidebar") { hide(entry) } }
                        if entry == .youtube, !model.youtubeQueue.isEmpty {
                            // Shown while anything is queued with Up Next.
                            row(.youtubeQueue, inset: 18)
                            .badge(model.youtubeQueue.count)
                            .tag(SidebarItem.youtubeQueue)
                        }
                    }
                }
            }
            Section(isExpanded: $openPlaylists) {
                ForEach(model.listening.playlists.map { SidebarItem.playlist($0.id) }, id: \.self) {
                    entry in
                    if case .playlist(let id) = entry, let playlist = model.playlist(id) {
                        row(entry, named: playlist.name)
                            .contextMenu {
                                Button("Rename…") { model.rename(playlist) }
                                Button(
                                    playlist.duplicates > 0
                                        ? "Remove \(playlist.duplicates) "
                                            + (playlist.duplicates == 1 ? "Duplicate" : "Duplicates")
                                        : "Remove Duplicates"
                                ) { model.removeDuplicates(from: playlist) }
                                .disabled(playlist.duplicates == 0)
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
                if !hiddenRows.contains(SidebarItem.importPlaylists.key) {
                    row(.importPlaylists)
                    .tag(SidebarItem.importPlaylists)
                    .contextMenu { Button("Remove from Sidebar") { hide(.importPlaylists) } }
                }
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

    /// A row of the sidebar: a page's name and picture, opened by a click even when
    /// it's the click that brings the app forward from behind another app.
    ///
    /// macOS's list leaves that click to the window: with the app behind, a row clicked
    /// did nothing but bring it forward, and wanted a second click (found 2026-10-09).
    /// So the row takes the click itself, across its whole width, and opens its page;
    /// with the app in front the list picks the row as well, which comes to the same.
    private func row(_ entry: SidebarItem, named name: String? = nil, inset: CGFloat = 0) -> some View {
        Label(name ?? entry.title, systemImage: entry.symbol)
            .padding(.leading, inset)
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
            .simultaneousGesture(TapGesture().onEnded { item = entry })
            .takesFirstClick()
    }

    private func header(_ title: String, @ViewBuilder button: () -> some View) -> some View {
        HStack {
            Text(title)
            Spacer()
            button()
        }
        .padding(.trailing, 6)
    }

    /// The rows outside Music that the owner has taken out (Customise Sidebar).
    private var hiddenRows: Set<String> { SidebarRows.hidden(savedHidden) }

    /// Take a row out of the sidebar. It comes back from Customise Sidebar.
    private func hide(_ entry: SidebarItem) {
        savedHidden = SidebarRows.write(savedHidden, entry.key, shown: false)
        if item == entry { item = .songs }
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

    private var customiseButton: some View {
        Button {
            customising = true
        } label: {
            Image(systemName: "slider.horizontal.3")
        }
        .help("Customise Sidebar: choose what the sidebar shows")
        .sheet(isPresented: $customising) {
            CustomiseSidebar(savedEntries: $savedEntries, savedHidden: $savedHidden)
                .environment(model)
                .dressed()
                .frame(width: 420, height: 640)  // tall enough for every row without scrolling
        }
    }

    private var visualizerButton: some View {
        Button {
            showNowPlaying = false
            item = .visualizer
        } label: {
            Image(systemName: "waveform")
                .foregroundStyle(current == .visualizer ? Color.accentColor : .primary)
        }
        .help("Visualizer: the song that's playing, with its picture and lyrics")
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

    /// A developer's check (`Bench`): the steps asked for, one after the other.
    private func bench() async {
        guard Bench.isOn, model.phase == .ready else { return }
        Bench.say(String(format: "BENCH ready %.2f s after the app was started", Bench.sinceLaunch()))
        try? await Task.sleep(for: .seconds(3))
        Bench.report("launch")  // everything since the app started
        for step in Bench.steps {
            if Task.isCancelled { return }
            Bench.begin()
            if step.name.hasPrefix("search=") {
                let words = String(step.name.dropFirst(7))
                model.searching = !words.isEmpty
                model.searchText = words
            } else if step.name.hasPrefix("finder=") {
                model.youtubeQuery = String(step.name.dropFirst(7))
            } else if step.name == "putback" {
                // The newest song in Settings → Deleted Items put back: its Undo.
                if let newest = model.removedLog.first(where: \.canPutBack) {
                    Bench.say("BENCH putback: \(newest.title), deleted \(newest.removedAt)")
                    model.putBack(newest)
                } else {
                    Bench.say("BENCH putback: nothing in the log can be put back")
                }
            } else if step.name == "remove=library" {
                // The newest download taken out of the library, its file kept: what
                // Delete from Library does once the owner has said yes.
                if let newest = model.downloaded.first {
                    Bench.say("BENCH remove: \(newest.path), of \(model.downloaded.count) downloads")
                    model.deleteDownloads([newest], keepingFiles: true)
                }
            } else if !Bench.took(step.name) {
                showNowPlaying = false
                item = SidebarItem(key: step.name)
            }
            try? await Task.sleep(for: .seconds(step.wait))
            Bench.report(step.name)
        }
        NSApp.terminate(nil)
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

    /// The page that's chosen, under the library's search bar when that's open. Every
    /// page opened so far is kept exactly as it was left, out of the window (`PageHost`).
    private var pages: some View {
        VStack(spacing: 0) {
            if model.searching, current != .youtube, current != .visualizer, current != .settings {
                SearchBar()
                Divider()
            }
            openPages
        }
    }

    private var openPages: some View {
        let model = model
        return PageHost(current: current, visited: visited, store: store) { entry, showing in
            // A page in a view of its own is told what the window's pages are told: the
            // model, and the look's colour for words.
            AnyView(
                PageView(entry: entry, active: showing)
                    // A page shorter than the window starts at the top, under the search
                    // bar, never in the middle of the window (the owner, 2026-10-08:
                    // Downloads with a search that found nothing).
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                    .environment(model)
                    .lookText()
                    .onAppear { if Bench.isOn { Bench.say("appear \(entry.key)") } })
        }
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
}

/// One page of the app: what a row of the sidebar opens.
struct PageView: View {
    let entry: SidebarItem
    /// False while another page is showing: a song list keeps its place but does no work.
    let active: Bool
    @Environment(AppModel.self) private var model

    var body: some View {
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
            MusicFinderView()
        case .youtubeQueue:
            YouTubeQueueView()
        case .visualizer:
            // The song that's playing, with its cover and lyrics: the player's own tab
            // (parked Fix A-3 will give it the owner's layout).
            NowPlayingView(isShown: .constant(true), closable: false, isActive: active)
        case .downloads:
            VStack(spacing: 0) {
                FilmKeeps()
                PendingDownloads()
                DownloadsByGenre()
            }
        case .settings:
            SettingsView()
        case .musicExplore:
            MusicFinderView()  // no row leads here any more: the same page as Music Finder
        case .videoExplore:
            VideoFinderView()
        case .animeFinder:
            MovieFinderView(kind: "anime")
        case .adultFinder:
            if model.profiles.current.isChild {
                Text("This page isn't part of this profile.").foregroundStyle(.secondary)
            } else {
                MovieFinderView(kind: "adult")
            }
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
    /// What's in the box. The lists are searched once the typing has paused for a
    /// moment, not at every letter: working a long list out again takes the app about
    /// half a second, and with that done for each letter the letters themselves were
    /// held up (the owner, 2026-10-08: "takes a long time to even be able to type").
    @State private var typed = ""

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
            TextField("Search your songs, artists and albums", text: $typed)
                .textFieldStyle(.plain)
                .focused($focused)
                .onSubmit { model.searchText = typed }
                .task(id: typed) {
                    guard typed != model.searchText else { return }
                    try? await Task.sleep(for: .milliseconds(250))
                    if !Task.isCancelled { model.searchText = typed }
                }
                .onChange(of: model.searchText, initial: true) {
                    if model.searchText != typed { typed = model.searchText }
                }
                .onExitCommand { model.searching = false }
            if !typed.isEmpty {
                Button {
                    typed = ""
                    model.searchText = ""
                } label: {
                    Image(systemName: "xmark.circle.fill")
                }
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

/// Customise Sidebar (the owner, 2026-10-08): which rows the sidebar shows. A row taken
/// out is only out of the sidebar: nothing of its page is lost, and it comes back here.
private struct CustomiseSidebar: View {
    @Binding var savedEntries: String
    @Binding var savedHidden: String
    @Environment(\.dismiss) private var dismiss
    @Environment(AppModel.self) private var model

    var body: some View {
        let shown = SidebarChoice.read(savedEntries)
        VStack(spacing: 0) {
            HStack {
                Text("Customise Sidebar").font(.title2.weight(.semibold)).heading()
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            .padding(16)
            Divider()
            List {
                Section("Music") {
                    ForEach(SidebarChoice.all.filter { $0 != "videos" }, id: \.self) { name in
                        if let entry = SidebarItem(libraryEntry: name) {
                            Toggle(
                                isOn: Binding(
                                    get: { shown.contains(name) },
                                    set: { library(name, $0, shown) })
                            ) { Label(entry.title, systemImage: entry.symbol) }
                            // The sidebar keeps at least one of Music's rows.
                            .disabled(shown == [name])
                        }
                    }
                }
                ForEach(["Videos", "Media Discovery", "Playlists"], id: \.self) { group in
                    Section(group) {
                        ForEach(
                            SidebarRows.all.filter { $0.group == group }
                                .filter { $0.key != "adultFinder" || !model.profiles.current.isChild },
                            id: \.key
                        ) { row in
                            let entry = SidebarItem(key: row.key)
                            Toggle(
                                isOn: Binding(
                                    get: { !SidebarRows.hidden(savedHidden).contains(row.key) },
                                    set: { savedHidden = SidebarRows.write(savedHidden, row.key, shown: $0) })
                            ) { Label(entry.title, systemImage: entry.symbol) }
                        }
                        if group == "Videos" {
                            Toggle(
                                isOn: Binding(
                                    get: { shown.contains("videos") },
                                    set: { library("videos", $0, shown) })
                            ) { Label(SidebarItem.videos.title, systemImage: SidebarItem.videos.symbol) }
                        }
                    }
                }
            }
            .scrollContentBackground(Theme.current.listBackground)
            Text("Your own playlists are always listed. Settings and the Visualizer are in the top bar and beside your name.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(12)
        }
    }

    private func library(_ name: String, _ on: Bool, _ shown: [String]) {
        savedEntries = SidebarChoice.write(
            on ? SidebarChoice.adding(name, to: shown) : shown.filter { $0 != name })
    }
}

/// Asked before a song goes into a playlist that has it already: skip those, or add
/// them again. (A modifier of its own: the main view's body is long enough as it is.)
/// The two questions before a download is deleted: from the computer (to the Trash), and
/// from the library only (its file kept). Kept apart from the window's own view, which
/// is as much as the compiler will take in one piece.
private struct DeletingQuestions: ViewModifier {
    @Environment(AppModel.self) private var model

    func body(content: Content) -> some View {
        content
            .confirmationDialog(
                Self.question(model.deletingDownloads ?? []),
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
            .confirmationDialog(
                Self.question(model.removingDownloads ?? [], fromLibrary: true),
                isPresented: Binding(
                    get: { model.removingDownloads != nil },
                    set: { if !$0 { model.removingDownloads = nil } })
            ) {
                Button("Delete from Library", role: .destructive) {
                    if let tracks = model.removingDownloads {
                        model.deleteDownloads(tracks, keepingFiles: true)
                    }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text(
                    "It leaves your library, playlists and favourites. Its file stays on this "
                        + "Mac, with its lyrics: set aside in the library folder's “_Replaced” "
                        + "folder, where nothing is ever deleted for you.")
            }
    }

    private static func question(_ tracks: [Track], fromLibrary: Bool = false) -> String {
        let whence = fromLibrary ? " from your library" : ""
        if tracks.count == 1, let only = tracks.first {
            return "Delete “\(only.title)”\(only.isVideo ? " (the video)" : "")\(whence)?"
        }
        return "Delete \(tracks.count) downloads\(whence)?"
    }
}

private struct AddingAgainQuestion: ViewModifier {
    @Environment(AppModel.self) private var model

    func body(content: Content) -> some View {
        let waiting = model.addingAgain
        content.alert(
            waiting?.question ?? "",
            isPresented: Binding(
                get: { model.addingAgain != nil }, set: { if !$0 { model.addingAgain = nil } })
        ) {
            if let waiting {
                if waiting.new.isEmpty {
                    Button("Add Again") { model.finishAdding(again: true) }
                    Button("Don't Add", role: .cancel) { model.addingAgain = nil }
                } else {
                    Button("Skip \(waiting.already == 1 ? "It" : "Them")") {
                        model.finishAdding(again: false)
                    }
                    .keyboardShortcut(.defaultAction)
                    Button("Add Again") { model.finishAdding(again: true) }
                    Button("Cancel", role: .cancel) { model.addingAgain = nil }
                }
            }
        } message: {
            if let waiting {
                Text(
                    waiting.new.isEmpty
                        ? "Add Again puts \(waiting.already == 1 ? "it" : "them") in a second time."
                        : "Skip adds only the \(waiting.new.count) "
                            + "\(waiting.new.count == 1 ? "song" : "songs") that \(waiting.new.count == 1 ? "isn't" : "aren't") there yet. "
                            + "Add Again puts every one in, the ones already there a second time.")
            }
        }
    }
}
