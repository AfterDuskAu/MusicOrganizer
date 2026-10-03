import MusicOrganizerKit
import SwiftUI

extension Track {
    // Never-nil values, so a table column can sort by them.
    var sortDuration: Double { durationS ?? 0 }
    var sortYear: Int { year ?? 0 }
    var sortGenre: String { genre ?? "" }
    var sortAdded: String { acquired ?? "" }
    /// "MP3 320", "AAC 128": what the audio is, for the Quality column.
    var quality: String {
        let kind = (format == "140" ? "aac" : format ?? "").uppercased()
        return [kind, bitrateKbps.map(String.init) ?? ""].filter { !$0.isEmpty }.joined(separator: " ")
    }
    /// "1 Oct 2026": the day the song came into the library.
    var addedDay: String {
        guard let acquired, let date = ISO8601DateFormatter().date(from: acquired) else { return "" }
        return date.formatted(date: .abbreviated, time: .omitted)
    }
}

// MARK: lists of songs

/// The columns of a song table that can be shown or hidden: the same choice for every
/// list, made from the menu bar (View → Columns).
enum SongColumns {
    /// (saved name, title)
    static let optional: [(id: String, title: String)] = [
        ("artist", "Artist"), ("album", "Album"), ("year", "Year"), ("genre", "Genre"),
        ("quality", "Quality"), ("added", "Added"), ("plays", "Plays"), ("time", "Time"),
    ]
    /// Hidden until the owner asks for them.
    static let hiddenAtFirst: Set<String> = ["genre", "quality", "added"]
    /// Each column's width. They're all set, not left to the table: a table that
    /// shares the room out itself starts every column at its "ideal" width whatever
    /// the window's (cutting the last one off, or leaving a gap), and only puts that
    /// right when something next changes size, so the columns were in different
    /// places from one opening of a list to the next.
    static let widths: [String: CGFloat] = [
        "artist": 190, "album": 210, "year": 46, "genre": 110, "quality": 70, "added": 90,
        "plays": 40, "time": 52,
    ]
    static let favouriteWidth: CGFloat = 20
    static let titleLeast: CGFloat = 200
    /// What the table puts around each column, and at its two ends (measured).
    static let between: CGFloat = 17
    static let ends: CGFloat = 44

    /// The title's width: whatever the columns that are showing leave of the table's.
    /// In a window too narrow for them all, the title keeps its least width and the
    /// table scrolls sideways.
    static func titleWidth(
        in tableWidth: CGFloat, _ columns: TableColumnCustomization<TrackRow>
    ) -> CGFloat {
        let showing = optional.filter { isShown($0.id, in: columns) }
        let taken = showing.reduce(favouriteWidth) { $0 + (widths[$1.id] ?? 0) }
        let gaps = between * CGFloat(showing.count + 2) + ends
        return max(titleLeast, (tableWidth - taken - gaps).rounded(.down))
    }

    static func isShown(_ id: String, in columns: TableColumnCustomization<TrackRow>) -> Bool {
        switch columns[visibility: id] {
        case .visible: true
        case .hidden: false
        default: !hiddenAtFirst.contains(id)
        }
    }
}

/// View → Columns in the menu bar: what's shown beside each song, in every list.
struct ColumnsMenu: View {
    @AppStorage("songColumns") private var columns = TableColumnCustomization<TrackRow>()

    var body: some View {
        Menu("Columns") {
            ForEach(SongColumns.optional, id: \.id) { column in
                Toggle(column.title, isOn: Binding(
                    get: { SongColumns.isShown(column.id, in: columns) },
                    set: { columns[visibility: column.id] = $0 ? .visible : .hidden }))
            }
        }
    }
}

/// One line of a song table. Its id is its place in the list before any search or sort,
/// because a playlist may hold the same song twice.
struct TrackRow: Identifiable, Sendable {
    let id: Int
    let track: Track
    let plays: Int
}

/// Which songs a list shows.
enum SongSource: Hashable, Sendable {
    case all, favourites, recentlyAdded, mostPlayed, unconfirmed, downloads, videos
    case playlist(String)
}

/// Everything a list's rows are worked out from. The rows are only worked out again
/// when one of these changes: never because of a click or a redraw (Fix A-1).
private struct RowsKey: Hashable {
    let source: SongSource
    let active: Bool
    let library: Int
    let plays: Int
    let members: [String]  // the favourites, or a playlist's songs, in order
    let search: String
    let sort: [String]
}

/// A page of songs: every list in the app (all songs, favourites, a playlist…) is one.
struct SongList: View {
    let source: SongSource
    let title: String
    let empty: String
    var note: String?
    /// False while another page is showing: the list keeps its place but does no work.
    let isActive: Bool

    @Environment(AppModel.self) private var model
    @State private var rows: [TrackRow] = []
    @State private var total = 0
    @State private var ready = false
    @State private var selection = Set<Int>()
    @State private var sortOrder: [KeyPathComparator<TrackRow>] = []
    /// How wide the table's room was when it was made (see `table`).
    @State private var madeAtWidth: CGFloat?
    /// Which columns show, and in what order: the owner's choice, kept for every list.
    @AppStorage("songColumns") private var columns = TableColumnCustomization<TrackRow>()

    private var playlist: Playlist? {
        if case .playlist(let id) = source { model.playlist(id) } else { nil }
    }

    private var key: RowsKey {
        let members: [String] =
            switch source {
            case .favourites: model.listening.favourites
            case .playlist: playlist?.trackIds ?? []
            default: []
            }
        return RowsKey(
            source: source, active: isActive, library: model.libraryVersion,
            plays: model.playsVersion, members: members, search: model.searchText,
            sort: sortOrder.map { "\($0.keyPath) \($0.order)" })
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            if !ready {
                Color.clear
            } else if total == 0 {
                Text(empty)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if rows.isEmpty {
                ContentUnavailableView.search(text: model.searchText)
            } else {
                table
            }
        }
        .task(id: key) { await workOutRows() }
    }

    /// Filter and sort off the main thread, then show the result in one go.
    private func workOutRows() async {
        guard isActive else { return }
        let (source, sortOrder, search) = (source, sortOrder, model.searchText)
        let (library, everything) = (model.library, model.everything)
        let (plays, downloaded, videos) = (model.listening.plays, model.downloaded, model.videos)
        let members = key.members
        let worked = await Task.detached(priority: .userInitiated) {
            () -> (rows: [TrackRow], total: Int) in
            let tracks: [Track] =
                switch source {
                case .all: library.tracks
                case .recentlyAdded: library.recentlyAdded()
                case .mostPlayed: library.mostPlayed(plays)
                case .unconfirmed: library.unconfirmed
                case .downloads: downloaded
                case .videos: videos
                case .favourites, .playlist: everything.tracks(withIDs: members)
                }
            let wanted = Set(everything.filter(tracks, search).map(\.path))
            var rows: [TrackRow] = []
            rows.reserveCapacity(tracks.count)
            for (place, track) in tracks.enumerated() where wanted.contains(track.path) {
                let count = track.trackId.flatMap { plays[$0]?.count } ?? 0
                rows.append(TrackRow(id: place, track: track, plays: count))
            }
            // No column chosen: the list's own order (a playlist's, or newest first).
            return (sortOrder.isEmpty ? rows : rows.sorted(using: sortOrder), tracks.count)
        }.value
        guard !Task.isCancelled else { return }
        rows = worked.rows
        total = worked.total
        ready = true
    }

    private func counted(_ total: Int) -> String {
        let (one, many) = source == .videos ? ("video", "videos") : ("song", "songs")
        return total == 1 ? "1 \(one)" : "\(total.formatted()) \(many)"
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(title).font(.title2.weight(.semibold)).lineLimit(1)
                    Text(counted(total))
                        .foregroundStyle(.secondary)
                }
                if let note {
                    Text(note).font(.callout).foregroundStyle(.secondary)
                }
            }
            Spacer()
            Button("Play", systemImage: "play.fill") { model.player.play(rows.map(\.track)) }
                .help("Play these songs in order")
            Button("Shuffle", systemImage: "shuffle") { model.player.playShuffled(rows.map(\.track)) }
                .help("Play these songs in a random order")
        }
        .disabled(rows.isEmpty)
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }

    private var table: some View {
        // A table starts every column at its "ideal" width whatever room it has, so
        // the title's is worked out from the room there is when the table is made.
        // After that it's left alone: the title is the one column that can stretch,
        // and the table itself gives it or takes from it as the window changes.
        // (Changing a column's width on a table that's already up moved its headings
        // and not its rows.)
        GeometryReader { space in
            table(titleWidth: SongColumns.titleWidth(in: madeAtWidth ?? space.size.width, columns))
                .onAppear { if madeAtWidth == nil { madeAtWidth = space.size.width } }
        }
    }

    private func table(titleWidth: CGFloat) -> some View {
        Table(
            of: TrackRow.self, selection: $selection, sortOrder: $sortOrder,
            columnCustomization: $columns
        ) {
            TableColumn("") { row in
                // Table cells don't inherit the window's environment on macOS.
                FavouriteButton(track: row.track).environment(model)
            }
            .width(20)
            .customizationID("favourite")
            .disabledCustomizationBehavior(.all)
            TableColumn("Title", value: \.track.title) { row in
                SongTitle(track: row.track).environment(model)
            }
            // Every other column has a set width, so the columns are in the same places
            // in every list, however long the names are and whenever the list is opened
            // (owner, 2026-10-02): the title takes what they leave.
            .width(min: SongColumns.titleLeast, ideal: titleWidth)
            .customizationID("title")
            .disabledCustomizationBehavior(.visibility)
            TableColumn("Artist", value: \.track.artistName)
                .width(SongColumns.widths["artist"]!)
                .customizationID("artist")
            TableColumn("Album", value: \.track.albumName)
                .width(SongColumns.widths["album"]!)
                .customizationID("album")
            TableColumn("Year", value: \.track.sortYear) { row in
                Text(row.track.year.map(String.init) ?? "").foregroundStyle(.secondary)
            }
            .width(SongColumns.widths["year"]!)
            .customizationID("year")
            TableColumn("Genre", value: \.track.sortGenre) { row in
                Text(row.track.sortGenre).foregroundStyle(.secondary)
            }
            .width(SongColumns.widths["genre"]!)
            .defaultVisibility(.hidden)
            .customizationID("genre")
            TableColumn("Quality", value: \.track.quality) { row in
                Text(row.track.quality).foregroundStyle(.secondary)
            }
            .width(SongColumns.widths["quality"]!)
            .defaultVisibility(.hidden)
            .customizationID("quality")
            TableColumn("Added", value: \.track.sortAdded) { row in
                Text(row.track.addedDay).foregroundStyle(.secondary)
            }
            .width(SongColumns.widths["added"]!)
            .defaultVisibility(.hidden)
            .customizationID("added")
            TableColumn("Plays", value: \.plays) { row in
                Text(row.plays > 0 ? String(row.plays) : "")
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
            }
            .width(SongColumns.widths["plays"]!)
            .customizationID("plays")
            TableColumn("Time", value: \.track.sortDuration) { row in
                Text(clockTime(row.track.durationS))
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
            }
            .width(SongColumns.widths["time"]!)
            .customizationID("time")
        } rows: {
            ForEach(rows) { row in
                // A download can be dragged onto the sidebar: onto the Library to move it
                // there, or back onto Downloads. What's carried is the song's id. Other
                // rows stay plain, as they were.
                if row.track.isDownload, let id = row.track.trackId {
                    TableRow(row).draggable(id)
                } else {
                    TableRow(row)
                }
            }
        }
        .background(FixedRows(height: 34))
        .contextMenu(forSelectionType: Int.self) { ids in
            menu(for: ids)
        } primaryAction: { ids in
            if let first = ids.first, let index = rows.firstIndex(where: { $0.id == first }) {
                model.player.play(rows.map(\.track), startAt: index)
            }
        }
    }

    @ViewBuilder
    private func menu(for ids: Set<Int>) -> some View {
        let picked = rows.filter { ids.contains($0.id) }
        if let first = picked.first, let index = rows.firstIndex(where: { $0.id == first.id }) {
            SongActions(songs: picked.map(\.track)) {
                model.player.play(rows.map(\.track), startAt: index)
            }
            .environment(model)
            if let playlist, playlist.trackIds.count == total {
                Divider()
                // Row ids are places in the playlist, so these act on exactly those lines.
                Button("Remove from This Playlist") {
                    let kept = playlist.trackIds.enumerated().filter { !ids.contains($0.offset) }
                    model.setTracks(kept.map(\.element), of: playlist)
                    selection = []
                }
                if picked.count == 1, sortOrder.isEmpty, model.searchText.isEmpty {
                    Button("Move Up") { move(first.id, by: -1, in: playlist) }
                        .disabled(first.id == 0)
                    Button("Move Down") { move(first.id, by: 1, in: playlist) }
                        .disabled(first.id >= playlist.trackIds.count - 1)
                }
            }
        }
    }

    private func move(_ place: Int, by step: Int, in playlist: Playlist) {
        var ids = playlist.trackIds
        let target = place + step
        guard ids.indices.contains(place), ids.indices.contains(target) else { return }
        ids.swapAt(place, target)
        model.setTracks(ids, of: playlist)
        selection = [target]
    }
}

/// What can be done with the songs picked in a list: the right-click menu every list of
/// songs shares.
struct SongActions: View {
    let songs: [Track]
    let play: () -> Void
    @Environment(AppModel.self) private var model

    var body: some View {
        Button("Play", action: play)
        if songs.count == 1, let only = songs.first {
            Button("Edit Details…") { model.editing = only }
            if !only.isVideo {
                // The button is here; what it does is still to be decided (owner,
                // 2026-10-01: the checks come first).
                Button("Swap Audio…") { model.explainSwap(of: only) }
            }
            // The Artists page on this artist: the owner's side, and their page on YouTube Music.
            if let artist = only.artist ?? only.albumArtist {
                ArtistInfoItems(artists: [artist])
            }
        }
        // Downloads kept under Discover can be moved into the main library, and back.
        let downloads = songs.filter(\.isDownload)
        if model.keepDownloadsSeparate, !downloads.isEmpty {
            let ids = downloads.compactMap(\.trackId)
            if downloads.allSatisfy(model.isMoved) {
                Button("Move Back to Downloads") { model.moveDownloads(ids, toLibrary: false) }
            } else {
                Button("Move to Library") { model.moveDownloads(ids, toLibrary: true) }
            }
        }
        // A download can be deleted (it goes to the Trash). The owner's own songs can't.
        if !downloads.isEmpty, downloads.count == songs.count {
            Button(
                downloads.count == 1 ? "Delete…" : "Delete \(downloads.count) Downloads…",
                role: .destructive
            ) {
                model.deletingDownloads = downloads
            }
        }
        Divider()
        if songs.allSatisfy(model.isFavourite) {
            Button("Remove from Favourites") { model.setFavourite(songs, false) }
        } else {
            Button("Add to Favourites") { model.setFavourite(songs, true) }
        }
        Menu("Add to Playlist") {
            ForEach(model.listening.playlists) { playlist in
                Button(playlist.name) { model.add(songs, to: playlist) }
            }
            if !model.listening.playlists.isEmpty { Divider() }
            Button("New Playlist…") { model.newPlaylist(with: songs) }
        }
    }
}

/// The heart beside a song: filled when it's a favourite.
struct FavouriteButton: View {
    let track: Track
    @Environment(AppModel.self) private var model

    var body: some View {
        let on = model.isFavourite(track)
        Button {
            model.setFavourite([track], !on)
        } label: {
            Image(systemName: on ? "heart.fill" : "heart")
                .foregroundStyle(on ? Color.pink : Color.secondary.opacity(0.5))
        }
        .buttonStyle(.plain)
        .disabled(track.trackId == nil)
        .help(on ? "Remove from Favourites" : "Add to Favourites")
    }
}

/// A song's cover, title and small badges, as shown in lists.
struct SongTitle: View {
    let track: Track
    var showCover = true
    @Environment(AppModel.self) private var model

    var body: some View {
        let playing = model.player.current?.id == track.id
        HStack(spacing: 8) {
            if showCover {
                CoverView(track: track, size: .small, corner: 4)
                    .frame(width: 30, height: 30)
            }
            Text(track.title)
                .lineLimit(1)
                .fontWeight(playing ? .semibold : .regular)
            if track.explicit {
                Image(systemName: "e.square.fill")
                    .foregroundStyle(.secondary)
                    .help("Explicit")
            }
            if track.isVideo {
                Image(systemName: "film")
                    .foregroundStyle(.secondary)
                    .help("A saved video" + (track.height.map { " (\($0)p)" } ?? ""))
            }
            if track.isUnconfirmed {
                Image(systemName: "questionmark.circle")
                    .foregroundStyle(.orange)
                    .help("Not identified yet: shown under its own name")
            }
            if playing {
                // No accent colour: it would vanish on a selected (blue) row.
                Image(systemName: model.player.isPlaying ? "speaker.wave.2.fill" : "speaker.fill")
            }
            Spacer(minLength: 0)
            if track.lyrics == .synced {
                Image(systemName: "quote.bubble")
                    .foregroundStyle(.tertiary)
                    .help("Has timed lyrics")
            }
        }
    }
}

// MARK: albums

struct AlbumsView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let albums = model.albums
        if albums.isEmpty {
            ContentUnavailableView.search(text: model.searchText)
        } else {
            ScrollView {
                AlbumGrid(albums: albums)
                    .padding(20)
            }
        }
    }
}

struct AlbumGrid: View {
    let albums: [Album]

    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 150, maximum: 210), spacing: 20)], spacing: 22) {
            ForEach(albums) { album in
                NavigationLink(value: album) {
                    VStack(alignment: .leading, spacing: 4) {
                        CoverView(track: album.coverTrack, size: .medium, corner: 8)
                            .shadow(color: .black.opacity(0.18), radius: 5, y: 2)
                        Text(album.title)
                            .lineLimit(1)
                            .padding(.top, 4)
                        Text(album.artist)
                            .lineLimit(1)
                            .foregroundStyle(.secondary)
                    }
                    .font(.callout)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
        }
    }
}

struct AlbumPage: View {
    let album: Album
    @Environment(AppModel.self) private var model
    @State private var selection = Set<Track.ID>()

    var body: some View {
        List(selection: $selection) {
            header
                .listRowSeparator(.hidden)
                .selectionDisabled()
            ForEach(Array(album.tracks.enumerated()), id: \.element.id) { index, track in
                HStack(spacing: 10) {
                    Text(track.track.map(String.init) ?? "\(index + 1)")
                        .monospacedDigit()
                        .foregroundStyle(.secondary)
                        .frame(width: 26, alignment: .trailing)
                    VStack(alignment: .leading, spacing: 1) {
                        SongTitle(track: track, showCover: false)
                        if track.artistName != album.artist {
                            Text(track.artistName).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    Text(clockTime(track.durationS))
                        .monospacedDigit()
                        .foregroundStyle(.secondary)
                }
                .padding(.vertical, 3)
                .tag(track.id)
            }
        }
        .contextMenu(forSelectionType: Track.ID.self) { ids in
            if let index = index(of: ids) {
                Button("Play") { model.player.play(album.tracks, startAt: index) }
                Button("Edit Details…") { model.editing = album.tracks[index] }
            }
        } primaryAction: { ids in
            if let index = index(of: ids) { model.player.play(album.tracks, startAt: index) }
        }
        .navigationTitle(album.title)
    }

    private func index(of ids: Set<Track.ID>) -> Int? {
        ids.first.flatMap { id in album.tracks.firstIndex { $0.id == id } }
    }

    private var header: some View {
        HStack(alignment: .bottom, spacing: 20) {
            CoverView(track: album.coverTrack, size: .large, corner: 10)
                .frame(width: 210, height: 210)
                .shadow(color: .black.opacity(0.25), radius: 10, y: 4)
            VStack(alignment: .leading, spacing: 6) {
                Text(album.title).font(.largeTitle.weight(.bold)).lineLimit(2)
                Text(album.artist).font(.title2).foregroundStyle(.secondary)
                Text(summary).font(.callout).foregroundStyle(.secondary)
                HStack {
                    Button("Play", systemImage: "play.fill") { model.player.play(album.tracks) }
                        .buttonStyle(.borderedProminent)
                    Button("Shuffle", systemImage: "shuffle") {
                        model.player.playShuffled(album.tracks)
                    }
                }
                .controlSize(.large)
                .padding(.top, 8)
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 14)
    }

    private var summary: String {
        var parts: [String] = []
        if let year = album.year { parts.append(String(year)) }
        parts.append(album.tracks.count == 1 ? "1 song" : "\(album.tracks.count) songs")
        parts.append("\(max(1, Int((album.duration / 60).rounded()))) min")
        return parts.joined(separator: " · ")
    }
}
