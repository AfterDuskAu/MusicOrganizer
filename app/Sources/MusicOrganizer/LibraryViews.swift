import MusicOrganizerKit
import SwiftUI

extension Track {
    // Never-nil values, so a table column can sort by them.
    var sortDuration: Double { durationS ?? 0 }
    var sortYear: Int { year ?? 0 }
}

// MARK: lists of songs

/// One line of a song table. Its id is its place in the list, because a playlist may
/// hold the same song twice.
struct TrackRow: Identifiable {
    let id: Int
    let track: Track
    let plays: Int
}

/// A page of songs: every list in the app (all songs, favourites, a playlist…) is one.
struct SongList: View {
    let title: String
    let tracks: [Track]
    let empty: String
    var note: String?
    var playlist: Playlist?

    @Environment(AppModel.self) private var model
    @State private var selection = Set<Int>()
    @State private var sortOrder: [KeyPathComparator<TrackRow>] = []

    var body: some View {
        let shown = model.songs(in: tracks)
        let unsorted = shown.enumerated().map {
            TrackRow(id: $0.offset, track: $0.element, plays: model.playCount($0.element))
        }
        // No column chosen: the list's own order (a playlist's, or newest first).
        let rows = sortOrder.isEmpty ? unsorted : unsorted.sorted(using: sortOrder)
        VStack(spacing: 0) {
            if let note {
                Text(note)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 8)
                Divider()
            }
            if tracks.isEmpty {
                Text(empty)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if rows.isEmpty {
                ContentUnavailableView.search(text: model.searchText)
            } else {
                table(rows)
            }
        }
        .navigationTitle(title)
        .navigationSubtitle(tracks.count == 1 ? "1 song" : "\(tracks.count.formatted()) songs")
        .toolbar {
            ToolbarItemGroup {
                Button("Play", systemImage: "play.fill") {
                    model.player.play(rows.map(\.track))
                }
                .help("Play these songs in order")
                Button("Shuffle", systemImage: "shuffle") {
                    model.player.playShuffled(rows.map(\.track))
                }
                .help("Play these songs in a random order")
            }
        }
    }

    private func table(_ rows: [TrackRow]) -> some View {
        Table(rows, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("") { row in
                // Table cells don't inherit the window's environment on macOS.
                FavouriteButton(track: row.track).environment(model)
            }
            .width(20)
            TableColumn("Title", value: \.track.title) { row in
                SongTitle(track: row.track).environment(model)
            }
            .width(min: 200, ideal: 320)
            TableColumn("Artist", value: \.track.artistName)
                .width(min: 100, ideal: 200)
            TableColumn("Album", value: \.track.albumName)
                .width(min: 100, ideal: 200)
            TableColumn("Year", value: \.track.sortYear) { row in
                Text(row.track.year.map(String.init) ?? "").foregroundStyle(.secondary)
            }
            .width(46)
            TableColumn("Plays", value: \.plays) { row in
                Text(row.plays > 0 ? String(row.plays) : "")
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
            }
            .width(40)
            TableColumn("Time", value: \.track.sortDuration) { row in
                Text(clockTime(row.track.durationS))
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
            }
            .width(52)
        }
        .contextMenu(forSelectionType: Int.self) { ids in
            menu(for: ids, in: rows)
        } primaryAction: { ids in
            if let first = ids.first, let index = rows.firstIndex(where: { $0.id == first }) {
                model.player.play(rows.map(\.track), startAt: index)
            }
        }
    }

    @ViewBuilder
    private func menu(for ids: Set<Int>, in rows: [TrackRow]) -> some View {
        let picked = rows.filter { ids.contains($0.id) }
        let songs = picked.map(\.track)
        if let first = picked.first, let index = rows.firstIndex(where: { $0.id == first.id }) {
            Button("Play") { model.player.play(rows.map(\.track), startAt: index) }
            if picked.count == 1 {
                Button("Edit Details…") { model.editing = first.track }
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
            if let playlist, model.searchText.isEmpty {
                Divider()
                // Row ids are places in the playlist, so these act on exactly those lines.
                Button("Remove from This Playlist") {
                    let kept = playlist.trackIds.enumerated().filter { !ids.contains($0.offset) }
                    model.setTracks(kept.map(\.element), of: playlist)
                    selection = []
                }
                if picked.count == 1, sortOrder.isEmpty {
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

// MARK: artists

struct ArtistsView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let artists = model.artists
        if artists.isEmpty {
            ContentUnavailableView.search(text: model.searchText)
        } else {
            List(artists) { artist in
                NavigationLink(value: artist) {
                    HStack(spacing: 10) {
                        CoverView(track: artist.albums.first?.coverTrack, size: .small, corner: 18)
                            .frame(width: 36, height: 36)
                        VStack(alignment: .leading) {
                            Text(artist.name)
                            Text(artist.trackCount == 1 ? "1 song" : "\(artist.trackCount) songs")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .padding(.vertical, 2)
                }
            }
        }
    }
}

struct ArtistPage: View {
    let artist: Artist
    @Environment(AppModel.self) private var model

    var body: some View {
        let tracks = artist.albums.flatMap(\.tracks)
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text(artist.name).font(.largeTitle.weight(.bold))
                HStack {
                    Button("Play", systemImage: "play.fill") { model.player.play(tracks) }
                        .buttonStyle(.borderedProminent)
                    Button("Shuffle", systemImage: "shuffle") { model.player.playShuffled(tracks) }
                }
                .controlSize(.large)
                AlbumGrid(albums: artist.albums)
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .navigationTitle(artist.name)
    }
}
