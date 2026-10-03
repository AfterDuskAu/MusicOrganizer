import MusicOrganizerKit
import SwiftUI

/// Discover → Artist: who an artist is, their best-known songs, their albums and singles,
/// and the artists their listeners also play, from YouTube Music's own page for them.
/// Every song plays straight away and downloads like any other; the page says which
/// the owner already has. Reached from the box at the top, from Artist Info on any
/// song's right-click menu, or from the artist button on a YouTube Music row or a
/// Discover card.
///
/// Concerts aren't here yet: they need a ticket seller's key (docs/ROADMAP.md).
struct ArtistInfoView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var page = model.artistBrowser
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                if page.canGoBack {
                    Button { page.back() } label: { Image(systemName: "chevron.left") }
                        .help("Back to \(page.before.last?.name ?? "the artist before")")
                }
                Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                TextField("An artist's name", text: $page.query)
                    .textFieldStyle(.plain)
                    .font(.title3)
                    .onSubmit { page.open(name: page.query) }
                if case .loading = page.phase {
                    ProgressView().controlSize(.small)
                } else {
                    Button("Look Up") { page.open(name: page.query) }
                        .disabled(page.query.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
            .padding(12)
            Divider()
            content(page)
        }
        .sheet(item: $page.album) { album in
            ArtistAlbumSheet(album: album).environment(model)
        }
    }

    @ViewBuilder
    private func content(_ page: ArtistBrowser) -> some View {
        switch page.phase {
        case .idle:
            Message(
                symbol: "person.crop.circle", title: "Look up an artist",
                text: "Type an artist's name above. Or right-click any song and choose Artist "
                    + "Info, or click the artist button beside a song on YouTube Music, What's "
                    + "New and Find."
            ) {}
        case .loading(let name):
            VStack(spacing: 12) {
                ProgressView()
                Text("Asking YouTube Music about \(name)…")
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        case .missing(let name):
            Message(
                symbol: "person.crop.circle.badge.questionmark", title: "No artist found",
                text: "YouTube Music has no artist called “\(name)”. Check the spelling, or try "
                    + "the name as it's written on their songs."
            ) {}
        case .failed(let why):
            Message(symbol: "exclamationmark.triangle", title: "That didn't work", text: why) {
                Button("Try Again") { page.again() }
            }
        case .shown:
            if let info = page.info {
                ArtistPageBody(info: info, page: page)
            }
        }
    }
}

/// A picture from YouTube Music by its address: an artist's, or a release's cover. It
/// fills whatever frame it's given and is cut to it (an artist's picture is a wide
/// banner, not a square).
private struct RemotePicture: View {
    let address: String?
    var corner: CGFloat = 8
    var size = CoverSize.medium
    var placeholder = "music.note"
    @State private var loaded: (address: String, image: NSImage)?

    var body: some View {
        Color.clear
            .overlay {
                if let loaded, loaded.address == address {
                    Image(nsImage: loaded.image)
                        .resizable()
                        .aspectRatio(contentMode: .fill)
                } else {
                    ZStack {
                        Rectangle().fill(.quaternary)
                        Image(systemName: placeholder).font(.title).foregroundStyle(.tertiary)
                    }
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: corner))
            .task(id: address) {
                guard let address else { return }
                // The cover loader takes a song; this stands in for one, so the picture
                // is fetched, cut to size and kept in memory the same way.
                let stand = Track(path: "picture:\(address)", title: "", artUrl: address)
                if let image = await Covers.shared.load(stand, root: nil, size: size) {
                    loaded = (address, image)
                }
            }
    }
}

private struct ArtistPageBody: View {
    let info: ArtistInfo
    let page: ArtistBrowser
    @Environment(AppModel.self) private var model
    @State private var aboutOpen = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 26) {
                header
                if let about = info.description { aboutSection(about) }
                songs
                releases("Albums", info.albums ?? [])
                releases("Singles and EPs", info.singles ?? [])
                related
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        // A different artist starts folded and at the top.
        .id(info.artistId ?? info.name)
    }

    // MARK: who they are

    private var header: some View {
        HStack(alignment: .center, spacing: 20) {
            // YouTube Music's picture of an artist is a wide banner.
            RemotePicture(
                address: info.thumbnail, corner: 12, size: .large, placeholder: "person.fill"
            )
            .frame(width: 288, height: 120)
            .shadow(color: .black.opacity(0.2), radius: 8, y: 3)
            VStack(alignment: .leading, spacing: 6) {
                Text(info.name)
                    .font(.largeTitle.weight(.bold))
                    .lineLimit(2)
                    .textSelection(.enabled)
                if !info.numbers.isEmpty {
                    Text(info.numbers).foregroundStyle(.secondary)
                }
                if let words = info.ownedWords {
                    Label(words, systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                }
                HStack(spacing: 10) {
                    Button("Play", systemImage: "play.fill") {
                        model.player.play(page.songs.map(\.result.track), startAt: 0)
                    }
                    .disabled(page.songs.isEmpty)
                    .help("Play the songs listed below, from YouTube Music. Nothing is saved.")
                    Menu {
                        ForEach(ElsewhereLink.allCases) { link in
                            if let url = link.url(title: "", artist: info.name) {
                                Link("Open in \(link.rawValue)", destination: url)
                            }
                        }
                    } label: {
                        Label("Elsewhere", systemImage: "arrow.up.right.square")
                    }
                    .fixedSize()
                    .help("Look this artist up on Spotify, Apple Music or SoundCloud")
                }
                .padding(.top, 4)
            }
        }
    }

    private func aboutSection(_ about: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("About").font(.title3.weight(.semibold))
            Text(about)
                .lineLimit(aboutOpen ? nil : 4)
                .textSelection(.enabled)
                .frame(maxWidth: 760, alignment: .leading)
            Button(aboutOpen ? "Less" : "More") { aboutOpen.toggle() }
                .buttonStyle(.link)
        }
    }

    // MARK: their songs

    private var songs: some View {
        let listed = page.songs
        let missing = ArtistSongs.missing(listed).filter { model.canDownload($0.videoId) }
        return VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 12) {
                Text(page.allSongs == nil ? "Top Songs" : "Songs")
                    .font(.title3.weight(.semibold))
                if page.allSongs != nil {
                    Text("\(listed.count)" + (page.moreSongs ? "+" : ""))
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if page.loadingSongs {
                    ProgressView().controlSize(.small)
                    Text("Fetching their songs…").foregroundStyle(.secondary)
                } else if page.allSongs == nil, info.songsPlaylistId != nil {
                    Button("Show All Songs", systemImage: "list.bullet") { page.showAllSongs() }
                        .help("Every song YouTube Music lists for them, up to 300, with lengths")
                }
                Button(
                    missing.isEmpty ? "Download Missing" : "Download Missing (\(missing.count))",
                    systemImage: "arrow.down.circle"
                ) {
                    model.planDownloads(of: missing)
                }
                .disabled(missing.isEmpty)
                .help(
                    "Download the songs listed here that you don't have. You'll see how many "
                        + "and how long first; they count towards your daily limit.")
            }
            if let problem = page.songsProblem {
                Text(problem).font(.callout).foregroundStyle(.orange)
            }
            if listed.isEmpty {
                Text("YouTube Music lists no songs for them.").foregroundStyle(.secondary)
            } else {
                ArtistSongRows(songs: listed)
            }
        }
    }

    // MARK: what they've released

    @ViewBuilder
    private func releases(_ title: String, _ listed: [ArtistRelease]) -> some View {
        if !listed.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text(title).font(.title3.weight(.semibold))
                if let problem = page.albumProblem {
                    Text(problem).font(.callout).foregroundStyle(.orange)
                }
                ScrollView(.horizontal, showsIndicators: false) {
                    LazyHStack(alignment: .top, spacing: 16) {
                        ForEach(listed) { release in
                            Button { page.open(release) } label: {
                                VStack(alignment: .leading, spacing: 4) {
                                    RemotePicture(address: release.thumbnail)
                                        .frame(width: 140, height: 140)
                                        .overlay {
                                            if page.openingAlbum == release.browseId {
                                                ProgressView()
                                                    .padding(10)
                                                    .background(.regularMaterial, in: Circle())
                                            }
                                        }
                                    Text(release.title).fontWeight(.medium).lineLimit(2)
                                    Text(release.caption).font(.callout).foregroundStyle(.secondary)
                                }
                                .frame(width: 140, alignment: .leading)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            .help("See its songs")
                        }
                    }
                    .padding(.vertical, 2)
                }
            }
        }
    }

    // MARK: who else

    @ViewBuilder
    private var related: some View {
        let listed = info.related ?? []
        if !listed.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text("Fans Also Like").font(.title3.weight(.semibold))
                ScrollView(.horizontal, showsIndicators: false) {
                    LazyHStack(alignment: .top, spacing: 18) {
                        ForEach(listed) { artist in
                            Button { page.open(artist) } label: {
                                VStack(spacing: 4) {
                                    RemotePicture(
                                        address: artist.thumbnail, corner: 48,
                                        placeholder: "person.fill"
                                    )
                                    .frame(width: 96, height: 96)
                                    Text(artist.name).fontWeight(.medium).lineLimit(1)
                                    if let audience = artist.monthlyAudience {
                                        Text("\(audience) a month")
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                            .lineLimit(1)
                                    }
                                }
                                .frame(width: 110)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            .help("Open \(artist.name)'s page")
                        }
                    }
                    .padding(.vertical, 2)
                }
            }
        }
    }
}

/// An artist's songs, a row each, as on the YouTube Music page: play, queue, download.
private struct ArtistSongRows: View {
    let songs: [ArtistSong]
    @Environment(AppModel.self) private var model

    var body: some View {
        LazyVStack(spacing: 0) {
            ForEach(Array(songs.enumerated()), id: \.element.id) { index, song in
                ResultRow(
                    result: song.result, owned: song.owned, artistButton: false,
                    download: { model.download(song.candidate) }
                ) {
                    model.player.play(songs.map(\.result.track), startAt: index)
                }
                .padding(.horizontal, 6)
                Divider()
            }
        }
    }
}

/// An album's, EP's or single's songs, opened from an artist's page.
private struct ArtistAlbumSheet: View {
    let album: ArtistAlbum
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        let missing = ArtistSongs.missing(album.songs).filter { model.canDownload($0.videoId) }
        VStack(spacing: 0) {
            HStack(alignment: .center, spacing: 16) {
                RemotePicture(address: album.thumbnail)
                    .frame(width: 96, height: 96)
                VStack(alignment: .leading, spacing: 4) {
                    Text(album.title).font(.title2.weight(.semibold)).lineLimit(2)
                    Text(
                        ([album.artists.joined(separator: ", "), album.year ?? ""]
                            .filter { !$0.isEmpty } + [songCount]).joined(separator: " · ")
                    )
                    .foregroundStyle(.secondary)
                    HStack(spacing: 10) {
                        Button("Play", systemImage: "play.fill") {
                            model.player.play(album.songs.map(\.result.track), startAt: 0)
                        }
                        .disabled(album.songs.isEmpty)
                        Button(
                            missing.isEmpty
                                ? "Download Missing" : "Download Missing (\(missing.count))",
                            systemImage: "arrow.down.circle"
                        ) {
                            // The question (how many, how long) is asked over the main
                            // window, so this sheet steps aside first.
                            dismiss()
                            model.planDownloads(of: missing)
                        }
                        .disabled(missing.isEmpty)
                        .help("Download this one's songs that you don't have")
                    }
                    .padding(.top, 2)
                }
                Spacer()
                Button("Done") { dismiss() }
                    .keyboardShortcut(.cancelAction)
            }
            .padding(16)
            Divider()
            if album.songs.isEmpty {
                Text("YouTube Music lists no songs that can be played for this one.")
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    ArtistSongRows(songs: album.songs).padding(.horizontal, 10)
                }
            }
        }
        .frame(width: 760, height: 560)
    }

    private var songCount: String {
        album.songs.count == 1 ? "1 song" : "\(album.songs.count) songs"
    }
}
