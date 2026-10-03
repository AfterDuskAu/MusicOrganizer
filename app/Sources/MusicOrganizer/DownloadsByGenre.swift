import MusicOrganizerKit
import SwiftUI

/// Discover → Downloads, laid out as the owner drew it (2026-10-03): "Videos" and
/// "Songs" named on the left, and beside each a box of them, each line a name, artist and
/// the day it was added, newest first. Videos or songs first is the owner's choice, and
/// remembered. (Until 2026-10-03 the boxes were genres.)
struct DownloadsByGenre: View {
    @Environment(AppModel.self) private var model
    @AppStorage("downloadsVideosFirst") private var videosFirst = true

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
                        ForEach(DownloadGroups.byKind(shown, videosFirst: videosFirst)) { group in
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
                    Text("Downloads").font(.title2.weight(.semibold)).heading()
                    Text(total == 1 ? "1 song" : "\(total.formatted()) songs")
                        .foregroundStyle(.secondary)
                }
                Text(
                    model.keepDownloadsSeparate
                        ? "Downloaded songs and videos stay here, apart from your main library. To "
                            + "move one in, drag it onto Library in the sidebar, or right-click → "
                            + "Move to Library."
                        : "Downloaded songs are also in your main library, and videos under "
                            + "Library → Videos (Settings → Downloads)."
                )
                .font(.callout)
                .foregroundStyle(.secondary)
            }
            Spacer()
            Picker("Order", selection: $videosFirst) {
                Text("Videos First").tag(true)
                Text("Songs First").tag(false)
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .fixedSize()
            .help("Which box comes first")
            Button("Play", systemImage: "play.fill") { model.player.play(shown) }
                .mainButton()
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
            Image(systemName: group.name == "Videos" ? "film" : "music.note")
                .font(.title3)
                .foregroundStyle(.secondary)
            Text(group.name)
                .font(.headline)
                .multilineTextAlignment(.center)
            Text("\(group.tracks.count)")
                .font(.caption)
                .monospacedDigit()
                .foregroundStyle(.secondary)
        }
        .padding(.vertical, 10)
        .padding(.horizontal, 8)
        .frame(width: Self.genreWidth)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(.quaternary))
        .help(group.name == "Videos" ? "Videos saved whole, picture and sound" : "Songs: sound only")
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
        .background(Theme.current.panel, in: RoundedRectangle(cornerRadius: 10))
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
