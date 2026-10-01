import MusicOrganizerKit
import SwiftUI

struct RootView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        switch model.phase {
        case .starting:
            ProgressView("Starting…")
        case .loading:
            ProgressView("Reading your library…")
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
    case songs, albums, artists
    case favourites, recentlyAdded, mostPlayed, unconfirmed
    case playlist(String)

    var title: String {
        switch self {
        case .songs: "Songs"
        case .albums: "Albums"
        case .artists: "Artists"
        case .favourites: "Favourites"
        case .recentlyAdded: "Recently Added"
        case .mostPlayed: "Most Played"
        case .unconfirmed: "Not Identified Yet"
        case .playlist: "Playlist"
        }
    }

    var symbol: String {
        switch self {
        case .songs: "music.note"
        case .albums: "square.stack"
        case .artists: "music.mic"
        case .favourites: "heart"
        case .recentlyAdded: "clock"
        case .mostPlayed: "chart.bar"
        case .unconfirmed: "questionmark.circle"
        case .playlist: "music.note.list"
        }
    }
}

struct MainView: View {
    @Environment(AppModel.self) private var model
    @State private var item: SidebarItem? = .songs
    @State private var path = NavigationPath()
    @State private var showNowPlaying = false
    @State private var deleting: Playlist?
    @AppStorage("showLyrics") private var showLyrics = false

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
                    NavigationStack(path: $path) {
                        detail
                            .navigationDestination(for: Album.self) {
                                AlbumPage(album: $0).environment(model)
                            }
                            .navigationDestination(for: Artist.self) {
                                ArtistPage(artist: $0).environment(model)
                            }
                    }
                }
                .searchable(text: $model.searchText, prompt: "Songs, artists, albums")
                .onChange(of: item) { path = NavigationPath() }
                .onChange(of: model.searchText) { path = NavigationPath() }
                .inspector(isPresented: $showLyrics) {
                    LyricsView()
                        .environment(model)
                        .inspectorColumnWidth(min: 260, ideal: 340, max: 520)
                }
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
            .toolbar(showNowPlaying ? .hidden : .automatic, for: .windowToolbar)
            // Below the split view, not an inset: the sidebar runs the window's full height
            // and would otherwise sit underneath the bar.
            PlayerBar(showLyrics: $showLyrics, showNowPlaying: $showNowPlaying)
        }
        .animation(.easeInOut(duration: 0.25), value: showNowPlaying)
        .sheet(item: $model.namePrompt) { NameSheet(prompt: $0) }
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
        List(selection: $item) {
            Section("Library") {
                row(.songs)
                row(.albums)
                row(.artists)
            }
            Section("Collections") {
                row(.favourites)
                row(.recentlyAdded)
                row(.mostPlayed)
                let waiting = model.library.unconfirmed.count
                if waiting > 0 {
                    row(.unconfirmed).badge(waiting)
                }
            }
            Section {
                ForEach(model.listening.playlists) { playlist in
                    Label(playlist.name, systemImage: SidebarItem.playlist("").symbol)
                        .tag(SidebarItem.playlist(playlist.id))
                        .contextMenu {
                            Button("Rename…") { model.rename(playlist) }
                            Button("Delete…", role: .destructive) { deleting = playlist }
                        }
                }
                Button { model.newPlaylist() } label: {
                    Label("New Playlist…", systemImage: "plus")
                }
                .buttonStyle(.plain)
                .foregroundStyle(.secondary)
            } header: {
                Text("Playlists")
            }
        }
    }

    private func row(_ item: SidebarItem) -> some View {
        Label(item.title, systemImage: item.symbol).tag(item)
    }

    @ViewBuilder
    private var detail: some View {
        let library = model.library
        switch item ?? .songs {
        case .songs:
            SongList(title: "Songs", tracks: library.tracks, empty: "No songs in the library yet.")
        case .albums:
            AlbumsView()
        case .artists:
            ArtistsView()
        case .favourites:
            SongList(
                title: "Favourites", tracks: library.tracks(withIDs: model.listening.favourites),
                empty: "Click the heart beside a song and it shows up here.")
        case .recentlyAdded:
            SongList(
                title: "Recently Added", tracks: library.recentlyAdded(),
                empty: "Nothing has been added yet.")
        case .mostPlayed:
            SongList(
                title: "Most Played", tracks: library.mostPlayed(model.listening.plays),
                empty: "Songs you play all the way through are counted and show up here.")
        case .unconfirmed:
            SongList(
                title: "Not Identified Yet", tracks: library.unconfirmed,
                empty: "Every song has been identified.",
                note: "These songs are here under their own names, so you can play them. "
                    + "They get their official names, covers and lyrics once they're identified.")
        case .playlist(let id):
            if let playlist = model.playlist(id) {
                SongList(
                    title: playlist.name, tracks: model.tracks(in: playlist),
                    empty: "Right-click any song and choose Add to Playlist.", playlist: playlist)
            } else {
                Text("That playlist has gone.").foregroundStyle(.secondary)
            }
        }
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
        .font(.caption)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
    }
}
