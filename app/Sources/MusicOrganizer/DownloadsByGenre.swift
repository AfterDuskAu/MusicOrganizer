import MusicOrganizerKit
import SwiftUI

/// Discover → Downloads, laid out as the owner drew it (2026-10-03): "Videos" and
/// "Songs" named on the left, and beside each a box of them, each line a name, artist and
/// the day it was added, newest first. Videos or songs first is the owner's choice, and
/// remembered. (Until 2026-10-03 the boxes were genres.)
struct DownloadsByGenre: View {
    @Environment(AppModel.self) private var model
    @AppStorage("downloadsVideosFirst") private var videosFirst = true
    /// The lines picked: a click picks one, ⌘-click adds or drops one, Shift-click picks a run.
    @State private var selection = RowSelection()

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
                Text("Songs and videos you download from the music service show up here.")
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if shown.isEmpty {
                ContentUnavailableView.search(text: model.searchText)
                    // It fills the page: left at its own height, the whole page (its heading too)
                    // sat in the middle of the window, under a gap.
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                let groups = DownloadGroups.byKind(shown, videosFirst: videosFirst)
                // Top to bottom as the page shows them: what a Shift-click measures along.
                let order = groups.flatMap(\.tracks)
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 18) {
                        columnNames
                        ForEach(groups) { group in
                            HStack(alignment: .top, spacing: 16) {
                                genreLabel(group)
                                songs(group, in: shown, order: order)
                            }
                        }
                    }
                    .padding(20)
                    // A click on the page beside the lines lets go of them.
                    .background(Color.clear.contentShape(Rectangle()).onTapGesture { selection.clear() })
                }
                .onChange(of: order.map(\.id)) { _, ids in selection.keep(only: ids) }
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
                            + "move some in, pick them (⌘-click or Shift-click for several), then "
                            + "drag them onto Library in the sidebar, or right-click → Move to Library."
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

    private func songs(_ group: GenreGroup, in shown: [Track], order: [Track]) -> some View {
        VStack(spacing: 0) {
            ForEach(Array(group.tracks.enumerated()), id: \.element.id) { place, track in
                if place > 0 { Divider().padding(.leading, 12) }
                // A right-click or a drag on a picked line is about every picked line.
                let ids = selection.acting(on: track.id, in: order.map(\.id))
                DownloadRow(
                    track: track,
                    picked: selection.contains(track.id),
                    acting: order.filter { ids.contains($0.id) },
                    click: { selection.click(track.id, $0, in: order.map(\.id)) }
                ) {
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

/// One downloaded song: its name, artist and the day it was added. A click picks it
/// (⌘ and Shift pick several); double-click plays it; right-click is the same menu as
/// every list of songs; it can be dragged onto Library in the sidebar, with the other
/// picked lines when it's one of them.
private struct DownloadRow: View {
    let track: Track
    let picked: Bool
    /// The songs a right-click or a drag here is about.
    let acting: [Track]
    let click: (RowSelection.Click) -> Void
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
        .background(
            picked
                ? AnyShapeStyle(Color.accentColor.opacity(0.25))
                : hovering ? AnyShapeStyle(.quaternary.opacity(0.6)) : AnyShapeStyle(.clear)
        )
        .contentShape(Rectangle())
        .onHover { hovering = $0 }
        .onTapGesture(count: 2, perform: play)
        // Beside the double-click, not after it, so a click picks the line at once.
        .simultaneousGesture(
            TapGesture().onEnded {
                let keys = NSEvent.modifierFlags
                click(keys.contains(.command) ? .toggle : keys.contains(.shift) ? .extend : .one)
            }
        )
        .takesFirstClick()
        .contextMenu { SongActions(songs: acting, play: play).environment(model) }
        let ids = acting.compactMap(\.trackId)
        if track.trackId != nil, !ids.isEmpty {
            row.draggable(DraggedSongs.text(of: ids))
        } else {
            row
        }
    }
}
