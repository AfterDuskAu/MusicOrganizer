import MusicOrganizerKit
import SwiftUI

extension Track {
    // Never-nil values, so a table column can sort by them.
    var sortDuration: Double { durationS ?? 0 }
    var sortYear: Int { year ?? 0 }
}

// MARK: songs

struct SongsView: View {
    @Environment(AppModel.self) private var model
    @State private var selection = Set<Track.ID>()
    @State private var sortOrder = [KeyPathComparator(\Track.title)]

    var body: some View {
        let songs = model.songs.sorted(using: sortOrder)
        Group {
            if songs.isEmpty {
                ContentUnavailableView.search(text: model.searchText)
            } else {
                Table(songs, selection: $selection, sortOrder: $sortOrder) {
                    TableColumn("Title", value: \.title) { track in
                        // Table cells don't inherit the window's environment on macOS.
                        SongTitle(track: track).environment(model)
                    }
                    .width(min: 200, ideal: 320)
                    TableColumn("Artist", value: \.artistName)
                        .width(min: 100, ideal: 200)
                    TableColumn("Album", value: \.albumName)
                        .width(min: 100, ideal: 200)
                    TableColumn("Year", value: \.sortYear) { track in
                        Text(track.year.map(String.init) ?? "").foregroundStyle(.secondary)
                    }
                    .width(46)
                    TableColumn("Time", value: \.sortDuration) { track in
                        Text(clockTime(track.durationS))
                            .monospacedDigit()
                            .foregroundStyle(.secondary)
                    }
                    .width(52)
                }
                .contextMenu(forSelectionType: Track.ID.self) { ids in
                    if let first = ids.first, let index = songs.firstIndex(where: { $0.id == first }) {
                        Button("Play") { model.player.play(songs, startAt: index) }
                    }
                } primaryAction: { ids in
                    if let first = ids.first, let index = songs.firstIndex(where: { $0.id == first }) {
                        model.player.play(songs, startAt: index)
                    }
                }
            }
        }
        .toolbar {
            ToolbarItem {
                Button("Shuffle All", systemImage: "shuffle") { model.player.playShuffled(songs) }
                    .help("Play these songs in a random order")
            }
        }
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
