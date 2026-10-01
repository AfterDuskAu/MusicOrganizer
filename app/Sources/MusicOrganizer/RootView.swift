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

private struct Message<Buttons: View>: View {
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

enum LibrarySection: String, CaseIterable, Identifiable {
    case songs, albums, artists

    var id: String { rawValue }
    var title: String { rawValue.capitalized }
    var symbol: String {
        switch self {
        case .songs: "music.note"
        case .albums: "square.stack"
        case .artists: "music.mic"
        }
    }
}

struct MainView: View {
    @Environment(AppModel.self) private var model
    @State private var section: LibrarySection? = .songs
    @State private var path = NavigationPath()
    @AppStorage("showLyrics") private var showLyrics = false

    var body: some View {
        @Bindable var model = model
        VStack(spacing: 0) {
        NavigationSplitView {
            VStack(spacing: 0) {
                List(selection: $section) {
                    Section("Library") {
                        ForEach(LibrarySection.allCases) { section in
                            Label(section.title, systemImage: section.symbol).tag(section)
                        }
                    }
                }
                StatusFooter()
            }
            .navigationSplitViewColumnWidth(min: 170, ideal: 190, max: 260)
        } detail: {
            NavigationStack(path: $path) {
                Group {
                    switch section ?? .songs {
                    case .songs: SongsView()
                    case .albums: AlbumsView()
                    case .artists: ArtistsView()
                    }
                }
                .navigationDestination(for: Album.self) { AlbumPage(album: $0).environment(model) }
                .navigationDestination(for: Artist.self) { ArtistPage(artist: $0).environment(model) }
            }
        }
        .searchable(text: $model.searchText, prompt: "Songs, artists, albums")
        .onChange(of: section) { path = NavigationPath() }
        .onChange(of: model.searchText) { path = NavigationPath() }
        .inspector(isPresented: $showLyrics) {
            LyricsView()
                .environment(model)
                .inspectorColumnWidth(min: 260, ideal: 340, max: 520)
        }
        .navigationTitle((section ?? .songs).title)
        // Below the split view, not an inset: the sidebar runs the window's full height
        // and would otherwise sit underneath the bar.
        PlayerBar(showLyrics: $showLyrics)
        }
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
