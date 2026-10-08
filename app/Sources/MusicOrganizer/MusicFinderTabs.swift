import MusicOrganizerKit
import SwiftUI

/// Music Finder → Covers & Remixes: remixes and covers of songs the owner has, that they
/// don't have. The same cards as What's New. It asks by itself the first time it's opened.
struct RemixesView: View {
    @Environment(AppModel.self) private var model
    @AppStorage("remixesCount") private var count = 50

    var body: some View {
        let page = model.remixes
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Covers & Remixes").font(.title2.weight(.semibold)).heading()
                    Text("Remixes and covers of songs you have, starting from what you've played lately.")
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Picker("How many", selection: $count) {
                    ForEach([10, 50, 100], id: \.self) { Text("\($0) songs").tag($0) }
                }
                .labelsHidden()
                .fixedSize()
                .disabled(page.working)
                Button("Different Songs", systemImage: "arrow.triangle.2.circlepath") {
                    page.find([.library], count: count, different: true)
                }
                .disabled(page.working)
                .help("Start from other songs in your library")
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 12)
            Divider()
            PicksView(
                page: page,
                empty: "No remixes or covers were found. Try Different Songs.",
                waiting: "Looking for remixes and covers of your songs…")
        }
        .task(id: model.phase) {
            if !page.hasAsked, model.phase == .ready { page.find([.library], count: count) }
        }
        .onChange(of: count) { page.find([.library], count: count) }
    }
}

/// Music Finder → Playlists: playlists on the music service around what the owner has
/// been playing. A click reads one on the Import Playlists page.
struct FoundPlaylistsView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Playlists").font(.title2.weight(.semibold)).heading()
                    Text("Playlists around the songs you've played lately and play most. It moves on as you listen.")
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("Different Playlists", systemImage: "arrow.triangle.2.circlepath") {
                    model.findPlaylists(different: true)
                }
                .disabled(model.findingPlaylists)
                .help("Start from other songs of yours")
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 12)
            Divider()
            if model.findingPlaylists {
                VStack(spacing: 12) {
                    ProgressView()
                    Text("Looking for playlists around your songs…")
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let problem = model.playlistsProblem {
                Message(symbol: "exclamationmark.triangle", title: "That didn't work", text: problem) {
                    Button("Try Again") { model.findPlaylists() }
                }
            } else if model.foundPlaylists.isEmpty {
                Text(model.playlistsNote ?? "No playlists were found. Try Different Playlists.")
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(40)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVGrid(
                        columns: [GridItem(.adaptive(minimum: 168, maximum: 220), spacing: 18)],
                        alignment: .leading, spacing: 20
                    ) {
                        ForEach(model.foundPlaylists) { playlist in
                            Button { model.open(playlist) } label: { card(playlist) }
                                .buttonStyle(.plain)
                                .help("Open this playlist: play its songs, or download them")
                        }
                    }
                    .padding(20)
                }
            }
        }
        // Asked the first time the tab is opened, and again whenever a song has been
        // played through since the page was found: it follows what's being listened to.
        .task(id: "\(model.phase == .ready) \(model.listening.playedStamp)") {
            guard model.phase == .ready else { return }
            if !model.playlistsAsked || model.playlistsStamp != model.listening.playedStamp {
                model.findPlaylists()
            }
        }
    }

    private func card(_ playlist: FoundPlaylist) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            // A square the picture fills, whatever its own shape: a wide one (a video's
            // frame) spilled over the next card when it sized the card itself.
            Color.clear
                .aspectRatio(1, contentMode: .fit)
                .overlay { WebPicture(address: playlist.thumbnail, symbol: "music.note.list") }
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .shadow(color: .black.opacity(0.18), radius: 5, y: 2)
            Text(playlist.title).fontWeight(.medium).lineLimit(1)
            if let author = playlist.author {
                Text(author).font(.callout).foregroundStyle(.secondary).lineLimit(1)
            }
            Text(playlist.why).font(.caption).foregroundStyle(.tertiary).lineLimit(1)
        }
        .contentShape(Rectangle())
    }
}
