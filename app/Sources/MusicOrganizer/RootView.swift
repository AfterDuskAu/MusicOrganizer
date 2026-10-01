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
            Text(title).font(.title2.weight(.semibold))
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
    case visualizer, whatsNew, find, youtube, downloads
    case playlist(String)

    /// A name for this entry that can be saved, and read back with `init(key:)`.
    var key: String {
        switch self {
        case .playlist(let id): "playlist:\(id)"
        case .visualizer: "visualizer"
        case .whatsNew: "whatsNew"
        case .find: "find"
        case .youtube: "youtube"
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
        case "whatsNew": self = .whatsNew
        case "find": self = .find
        case "youtube": self = .youtube
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
        case .videos: "Videos"
        case .favourites: "Favourites"
        case .recentlyAdded: "Recently Added"
        case .mostPlayed: "Most Played"
        case .unconfirmed: "Not Identified Yet"
        case .whatsNew: "What's New"
        case .find: "Find"
        case .youtube: "YouTube Music"
        case .visualizer: "Local Visualizer"
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
        case .whatsNew: "sparkles"
        case .find: "wand.and.stars"
        case .youtube: "play.rectangle"
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
    @State private var deleting: Playlist?
    @AppStorage("showLyrics") private var showLyrics = false
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
                    .navigationSplitViewColumnWidth(min: 180, ideal: 210, max: 280)
                } detail: {
                    NavigationStack(path: pathBinding) {
                        pagesWithLyrics
                            .navigationTitle(title(of: current))
                            .navigationDestination(for: Album.self) {
                                AlbumPage(album: $0).environment(model)
                            }
                            .navigationDestination(for: Artist.self) {
                                ArtistPage(artist: $0).environment(model)
                            }
                    }
                }
                .toolbar {
                    // Search lives behind this button, beside the sidebar button, so no
                    // page carries a search field it isn't using.
                    ToolbarItem(placement: .navigation) {
                        Button {
                            model.searching.toggle()
                        } label: {
                            Image(systemName: "magnifyingglass")
                                .foregroundStyle(model.searching ? Color.accentColor : .primary)
                        }
                        .keyboardShortcut("f")
                        .help(model.searching ? "Close search" : "Search your library (⌘F)")
                    }
                }
                .onChange(of: item, initial: true) { opened(current) }
                .onAppear {
                    let caughtUp = SidebarChoice.catchUp(saved: savedEntries, seen: seenEntries)
                    if caughtUp.seen != seenEntries {
                        savedEntries = caughtUp.entries
                        seenEntries = caughtUp.seen
                    }
                }
                .onChange(of: model.searchText) { paths[current] = NavigationPath() }
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
                showNowPlaying || model.videoFullScreen ? .hidden : .automatic, for: .windowToolbar)
            // Below the split view, not an inset: the sidebar runs the window's full height
            // and would otherwise sit underneath the bar.
            PlayerBar(showLyrics: $showLyrics, showNowPlaying: $showNowPlaying)
        }
        // Over everything, the player bar included: the video's own controls take over.
        .overlay {
            if model.videoFullScreen {
                FullScreenVideo().environment(model)
            }
        }
        .animation(.easeInOut(duration: 0.25), value: showNowPlaying)
        .sheet(item: $model.namePrompt) { NameSheet(prompt: $0) }
        .sheet(item: $model.editing) { EditSheet(track: $0).environment(model) }
        .alert(
            "That didn't work", isPresented: Binding(
                get: { model.notice != nil }, set: { if !$0 { model.notice = nil } })
        ) {
            Button("OK") {}
        } message: {
            Text(model.notice ?? "")
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
        let entries = shown.compactMap(SidebarItem.init(libraryEntry:))
            .filter { $0 != .unconfirmed || waiting > 0 }  // nothing waiting: nothing to show
        return List(selection: $item) {
            Section(isExpanded: $openLibrary) {
                ForEach(entries, id: \.self) { entry in
                    Label(entry.title, systemImage: entry.symbol)
                        .badge(entry == .unconfirmed ? waiting : 0)
                        .contextMenu {
                            Button("Remove from Sidebar") { remove(entry, from: shown) }
                                .disabled(shown.count <= 1)
                        }
                }
            } header: {
                header("Library") {
                    Menu {
                        ForEach(hidden, id: \.self) { name in
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
                    .disabled(hidden.isEmpty)
                    .help(hidden.isEmpty ? "Everything is already shown" : "Add an entry to the sidebar")
                }
            }
            Section("Media", isExpanded: $openMedia) {
                ForEach([SidebarItem.visualizer, .youtube], id: \.self) { entry in
                    Label(entry.title, systemImage: entry.symbol)
                }
            }
            Section("Discover", isExpanded: $openDiscover) {
                ForEach([SidebarItem.whatsNew, .find, .downloads], id: \.self) { entry in
                    Label(entry.title, systemImage: entry.symbol)
                        .badge(entry == .downloads ? model.downloaded.count : 0)
                }
            }
            Section(isExpanded: $openPlaylists) {
                ForEach(model.listening.playlists.map { SidebarItem.playlist($0.id) }, id: \.self) {
                    entry in
                    if case .playlist(let id) = entry, let playlist = model.playlist(id) {
                        Label(playlist.name, systemImage: entry.symbol)
                            .contextMenu {
                                Button("Rename…") { model.rename(playlist) }
                                Button("Delete…", role: .destructive) { deleting = playlist }
                            }
                    }
                }
                Button { model.newPlaylist() } label: {
                    Label("New Playlist…", systemImage: "plus")
                }
                .buttonStyle(.plain)
                .foregroundStyle(.secondary)
            } header: {
                header("Playlists") {
                    Button { model.newPlaylist() } label: { Image(systemName: "plus") }
                        .buttonStyle(.borderless)
                        .help("New playlist")
                }
            }
        }
        .listStyle(.sidebar)
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
                    .background(.background.secondary)
            }
        }
        .transaction { $0.animation = nil }
    }

    private var pathBinding: Binding<NavigationPath> {
        Binding(
            get: { paths[current] ?? NavigationPath() },
            set: { paths[current] = $0 })
    }

    private func title(of entry: SidebarItem) -> String {
        if case .playlist(let id) = entry { model.playlist(id)?.name ?? "Playlist" } else { entry.title }
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
            if model.searching, current != .youtube, current != .visualizer {
                SearchBar()
                Divider()
            }
            stackedPages
        }
    }

    private var stackedPages: some View {
        ZStack {
            ForEach(visited, id: \.self) { entry in
                let active = entry == current
                page(entry, active: active)
                    // A page that isn't showing is moved far out of sight, not made
                    // invisible: for an invisible view SwiftUI takes its AppKit views
                    // (a whole song table) out of the window and puts them all back
                    // when it shows again, which froze the app on every switch.
                    .offset(x: active ? 0 : 30_000)
                    .allowsHitTesting(active)
                    .accessibilityHidden(!active)
                // No zIndex either: changing which page is on top also made AppKit take
                // every page's views out and put them back (profiled 2026-10-01).
            }
        }
        .clipped()
    }

    @ViewBuilder
    private func page(_ entry: SidebarItem, active: Bool) -> some View {
        switch entry {
        case .songs:
            SongList(source: .all, title: "Songs", empty: "No songs in the library yet.", isActive: active)
        case .albums:
            AlbumsView()
        case .artists:
            ArtistsView()
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
                    ? "Saved videos are under Discover → Downloads. Settings → General "
                        + "(All Library) lists them here instead."
                    : "Videos you save show up here. Play a song on the Local Visualizer, "
                        + "switch to Video, and click Save Video.",
                isActive: active)
        case .youtube:
            YouTubeSearchView()
        case .visualizer:
            // The song that's playing, with its cover and lyrics: the player's own tab
            // (parked Fix A-3 will give it the owner's layout).
            NowPlayingView(isShown: .constant(true), closable: false, isActive: active)
        case .downloads:
            SongList(
                source: .downloads, title: "Downloads",
                empty: "Songs and videos you download from YouTube Music show up here.",
                note: model.keepDownloadsSeparate
                    ? "Downloaded songs and videos stay here, apart from your main library. "
                        + "Settings → General can put them in the main library instead."
                    : "Downloaded songs are also in your main library, and videos under "
                        + "Library → Videos (Settings → General).",
                isActive: active)
        case .whatsNew:
            Message(
                symbol: "sparkles", title: "What's New is coming",
                text: "New songs matched to your library will be listed here. It arrives with Discover."
            ) {}
        case .find:
            Message(
                symbol: "wand.and.stars", title: "Find is coming",
                text: "Pick an artist, a genre and how many songs you want, and get recommendations. "
                    + "It arrives with Discover. Until then, YouTube Music (under Media) finds any song by name."
            ) {
                Button("Open YouTube Music") { item = .youtube }
            }
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

/// The bottom of the sidebar: what's in the library, and what's still waiting.
private struct StatusFooter: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        HStack(alignment: .bottom) {
        VStack(alignment: .leading, spacing: 3) {
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
        SettingsLink {
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
