import MusicOrganizerKit
import SwiftUI

/// Discover → Downloads, laid out as the owner drew it (2026-10-02): each genre named on
/// the left, and beside it a box of its songs, each line a song's name, artist and the
/// day it was added. The newest downloads are at the top of each box, and the genre
/// with the newest download comes first.
///
/// A song's genre is its own genre tag: a download gets one from Discover (the genre it
/// was found under) or from the owner's other songs by the artist. One with no genre
/// yet is listed last; Edit Details… gives it one.
struct DownloadsByGenre: View {
    @Environment(AppModel.self) private var model

    private static let genreWidth: CGFloat = 150
    static let artistWidth: CGFloat = 220
    static let addedWidth: CGFloat = 110

    var body: some View {
        let all = model.downloaded
        let shown = model.everything.filter(all, model.searchText)
        VStack(spacing: 0) {
            header(shown, of: all.count)
            Divider()
            if all.isEmpty {
                Text("Songs and videos you download from YouTube Music show up here.")
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if shown.isEmpty {
                ContentUnavailableView.search(text: model.searchText)
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 18) {
                        columnNames
                        ForEach(Genres.groups(shown)) { group in
                            HStack(alignment: .top, spacing: 16) {
                                genreLabel(group)
                                songs(group, in: shown)
                            }
                        }
                    }
                    .padding(20)
                }
            }
        }
    }

    private func header(_ shown: [Track], of total: Int) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text("Downloads").font(.title2.weight(.semibold))
                    Text(total == 1 ? "1 song" : "\(total.formatted()) songs")
                        .foregroundStyle(.secondary)
                }
                Text(
                    model.keepDownloadsSeparate
                        ? "Downloaded songs and videos stay here, apart from your main library. To "
                            + "move one in, drag it onto Library in the sidebar, or right-click → "
                            + "Move to Library."
                        : "Downloaded songs are also in your main library, and videos under "
                            + "Library → Videos (Settings → General)."
                )
                .font(.callout)
                .foregroundStyle(.secondary)
            }
            Spacer()
            Button("Play", systemImage: "play.fill") { model.player.play(shown) }
                .help("Play these songs in order")
            Button("Shuffle", systemImage: "shuffle") { model.player.playShuffled(shown) }
                .help("Play these songs in a random order")
        }
        .disabled(shown.isEmpty)
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }

    /// What each line's three parts are, said once above the first box.
    private var columnNames: some View {
        HStack(spacing: 16) {
            Color.clear.frame(width: Self.genreWidth, height: 1)
            HStack(spacing: 12) {
                Text("Song").frame(maxWidth: .infinity, alignment: .leading)
                Text("Artist").frame(width: Self.artistWidth, alignment: .leading)
                Text("Date Added").frame(width: Self.addedWidth, alignment: .trailing)
            }
            .padding(.horizontal, 12)
        }
        .font(.caption)
        .foregroundStyle(.secondary)
    }

    private func genreLabel(_ group: GenreGroup) -> some View {
        VStack(spacing: 2) {
            Text(group.name.isEmpty ? "No genre yet" : group.name)
                .font(.headline)
                .multilineTextAlignment(.center)
            Text(group.tracks.count == 1 ? "1 song" : "\(group.tracks.count) songs")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(.vertical, 10)
        .padding(.horizontal, 8)
        .frame(width: Self.genreWidth)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(.quaternary))
        .help(
            group.name.isEmpty
                ? "These have no genre in their details. Right-click one → Edit Details… to give it one."
                : "Songs whose genre is \(group.name)")
    }

    private func songs(_ group: GenreGroup, in shown: [Track]) -> some View {
        VStack(spacing: 0) {
            ForEach(Array(group.tracks.enumerated()), id: \.element.id) { place, track in
                if place > 0 { Divider().padding(.leading, 12) }
                DownloadRow(track: track) {
                    // Playing one carries on through the rest of the page, in its order.
                    if let index = shown.firstIndex(of: track) {
                        model.player.play(shown, startAt: index)
                    }
                }
            }
        }
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(.quaternary))
        .frame(maxWidth: .infinity)
    }
}

/// One downloaded song: its name, artist and the day it was added. Double-click plays
/// it; right-click is the same menu as every list of songs; it can be dragged onto
/// Library in the sidebar.
private struct DownloadRow: View {
    let track: Track
    let play: () -> Void
    @Environment(AppModel.self) private var model
    @State private var hovering = false

    var body: some View {
        let row = HStack(spacing: 12) {
            SongTitle(track: track)
                .frame(maxWidth: .infinity, alignment: .leading)
            Text(track.artistName)
                .lineLimit(1)
                .foregroundStyle(.secondary)
                .frame(width: DownloadsByGenre.artistWidth, alignment: .leading)
            Text(track.addedDay)
                .lineLimit(1)
                .foregroundStyle(.secondary)
                .frame(width: DownloadsByGenre.addedWidth, alignment: .trailing)
        }
        .padding(.horizontal, 12)
        .frame(height: 38)
        .background(hovering ? AnyShapeStyle(.quaternary.opacity(0.6)) : AnyShapeStyle(.clear))
        .contentShape(Rectangle())
        .onHover { hovering = $0 }
        .onTapGesture(count: 2, perform: play)
        .contextMenu { SongActions(songs: [track], play: play).environment(model) }
        if let id = track.trackId {
            row.draggable(id)
        } else {
            row
        }
    }
}
