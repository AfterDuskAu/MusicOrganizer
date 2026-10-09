import MusicOrganizerKit
import SwiftUI

/// Library → Artists, split down the middle as the owner drew it (2026-10-03): **Mine**
/// on the left, **Discover** on the right, one search box above both.
///
/// - With nothing open, each side is a list. Mine is the library's artists. Discover is
///   the artists behind What's New's picks, or, after Look Up, the artists YouTube Music
///   finds for what was typed (Mine is narrowed by the same words as they're typed).
/// - Opening an artist, from either list or from Artist Info on any song, gives both
///   sides to that artist: on the left what the owner has of theirs and what their own
///   listening says about it; on the right the artist's page on YouTube Music (who they
///   are, their songs, albums and similar artists), which gets most of the room.
///
/// Concerts aren't here yet: they need a ticket seller's key (docs/ROADMAP.md).
struct ArtistsPage: View {
    @Environment(AppModel.self) private var model
    @AppStorage("whatsNewCount") private var whatsNewCount = 50

    /// The share of the page the owner's side gets once an artist is open (owner: "I'd
    /// give the Discover artist page about 70").
    private static let mineShare = 0.3

    var body: some View {
        @Bindable var page = model.artistBrowser
        VStack(spacing: 0) {
            header(page)
            Divider()
            if let opened = page.opened {
                GeometryReader { room in
                    let mine = max(230, room.size.width * Self.mineShare)
                    HStack(spacing: 0) {
                        MineForArtist(name: opened.name)
                            .frame(width: mine)
                        Divider()
                        ArtistDiscoverPane(page: page)
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                            // In a small window the artist's page has little room: its
                            // song rows slim down.
                            .environment(\.narrowRows, room.size.width - mine < 620)
                    }
                }
            } else {
                HStack(spacing: 0) {
                    MineList(query: page.query) { page.open(name: $0.name) }
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                    Divider()
                    DiscoverList(page: page)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
        }
        .sheet(item: $page.album) { album in
            ArtistAlbumSheet(album: album).environment(model).dressed()
        }
        .task(id: model.phase) {
            // The Discover side starts from What's New's artists: asked for once, as the
            // What's New page itself would on its first opening.
            if !model.whatsNew.hasAsked, model.phase == .ready {
                model.whatsNew.find([.library], count: whatsNewCount)
            }
        }
    }

    /// "Mine", the search box, "Discover"; and the way back while an artist is open.
    private func header(_ page: ArtistBrowser) -> some View {
        @Bindable var page = page
        return HStack(spacing: 12) {
            HStack(spacing: 10) {
                if page.opened != nil {
                    Button { page.back() } label: { Image(systemName: "chevron.left") }
                        .keyboardShortcut(.cancelAction)
                        .help(
                            page.canGoBack
                                ? "Back to \(page.before.last?.name ?? "the artist before")"
                                : "Back to the lists")
                }
                Text("Mine").font(.title2.weight(.semibold)).heading()
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                TextField("An artist's name", text: $page.query)
                    .textFieldStyle(.plain)
                    .onSubmit { page.search() }
                if page.searching {
                    ProgressView().controlSize(.small)
                } else {
                    Button("Look Up") { page.search() }
                        .controlSize(.small)
                        .disabled(page.query.trimmingCharacters(in: .whitespaces).isEmpty)
                        .help("Narrow your artists to this, and ask the music service who goes by it")
                }
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(.quaternary.opacity(0.6), in: RoundedRectangle(cornerRadius: 8))
            .frame(width: 320)
            Text("Discover")
                .font(.title2.weight(.semibold))
                .heading()
                .frame(maxWidth: .infinity, alignment: .trailing)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 10)
        .onChange(of: page.query) {
            if page.query.trimmingCharacters(in: .whitespaces).isEmpty { page.clearSearch() }
        }
    }
}

/// One line of either list: a round picture, a name, a line under it.
private struct ArtistLine<Picture: View>: View {
    let name: String
    let caption: String?
    @ViewBuilder let picture: Picture
    let open: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: open) {
            HStack(spacing: 10) {
                picture.frame(width: 36, height: 36)
                VStack(alignment: .leading, spacing: 1) {
                    Text(name).lineLimit(1)
                    if let caption {
                        Text(caption).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    }
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 6)
            .background(hovering ? AnyShapeStyle(.quaternary.opacity(0.5)) : AnyShapeStyle(.clear))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
    }
}

/// Mine: the library's artists, narrowed by what's typed in the box.
private struct MineList: View {
    let query: String
    let open: (Artist) -> Void
    @Environment(AppModel.self) private var model

    var body: some View {
        let wanted = fold(query.trimmingCharacters(in: .whitespaces))
        let artists = model.artists.filter { wanted.isEmpty || fold($0.name).contains(wanted) }
        if artists.isEmpty {
            Text(
                model.artists.isEmpty
                    ? "No artists in your library yet."
                    : "None of your artists is called that."
            )
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            ScrollView {
                LazyVStack(spacing: 0) {
                    ForEach(artists) { artist in
                        ArtistLine(
                            name: artist.name,
                            caption: artist.trackCount == 1 ? "1 song" : "\(artist.trackCount) songs"
                        ) {
                            CoverView(track: artist.albums.first?.coverTrack, size: .small, corner: 18)
                        } open: {
                            open(artist)
                        }
                        Divider().padding(.leading, 66)
                    }
                }
            }
        }
    }
}

/// Discover: the artists behind What's New's picks, or the ones a search found.
private struct DiscoverList: View {
    let page: ArtistBrowser
    @Environment(AppModel.self) private var model

    var body: some View {
        if page.searching {
            waiting("Asking the music service who goes by “\(page.searched)”…")
        } else if let problem = page.searchProblem {
            note(problem)
        } else if let found = page.results {
            if found.isEmpty {
                note("The music service found no artist called “\(page.searched)”.")
            } else {
                rows(found)
            }
        } else {
            let behind = ArtistNames.behind(model.whatsNew.picks)
            if !behind.isEmpty {
                rows(behind)
            } else if model.whatsNew.working {
                waiting("Finding the artists behind What's New…")
            } else {
                note(
                    "The artists behind What's New show here. Type a name above and Look Up to "
                        + "find anyone.")
            }
        }
    }

    private func rows(_ artists: [ListedArtist]) -> some View {
        ScrollView {
            LazyVStack(spacing: 0) {
                ForEach(artists) { artist in
                    ArtistLine(name: artist.name, caption: artist.caption) {
                        RemotePicture(
                            address: artist.thumbnail, corner: 18, size: .small,
                            placeholder: "person.fill")
                    } open: {
                        page.open(artist)
                    }
                    Divider().padding(.leading, 66)
                }
            }
        }
    }

    private func waiting(_ words: String) -> some View {
        VStack(spacing: 10) {
            ProgressView()
            Text(words).foregroundStyle(.secondary).multilineTextAlignment(.center)
        }
        .padding(30)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func note(_ words: String) -> some View {
        Text(words)
            .foregroundStyle(.secondary)
            .multilineTextAlignment(.center)
            .padding(30)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

/// The owner's side of an artist: what they have of theirs, and what their own listening
/// says about it. Plays are the ones this app has counted (a song played to its end).
private struct MineForArtist: View {
    let name: String
    @Environment(AppModel.self) private var model

    var body: some View {
        // Everything the owner has, downloads kept under Discover included.
        if let artist = ArtistNames.mine(name, in: model.everything.artists) {
            have(artist)
        } else {
            VStack(spacing: 10) {
                Image(systemName: "music.note.list").font(.largeTitle).foregroundStyle(.tertiary)
                Text("You have none of their songs yet.").fontWeight(.medium)
                Text("Download or Download Missing, on the right, brings them into your library.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }
            .padding(20)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private func have(_ artist: Artist) -> some View {
        let tracks = artist.albums.flatMap(\.tracks)
        let plays = model.listening.plays
        let stats = ArtistStats(artist: artist, plays: plays, favourites: model.favourites)
        let rank = ArtistStats.rank(of: artist, among: model.everything.artists, plays: plays)
        return ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("In Your Library").font(.title3.weight(.semibold)).heading()
                    Text(
                        "\(stats.songs) \(stats.songs == 1 ? "song" : "songs") · "
                            + "\(stats.albums) \(stats.albums == 1 ? "album" : "albums")"
                    )
                    .foregroundStyle(.secondary)
                }
                HStack(spacing: 8) {
                    Button("Play", systemImage: "play.fill") { model.player.play(tracks) }
                    Button("Shuffle", systemImage: "shuffle") { model.player.playShuffled(tracks) }
                }
                VStack(alignment: .leading, spacing: 5) {
                    Text("Your Listening").font(.headline)
                    if stats.plays == 0 {
                        Text("You haven't played any of these to the end yet.")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    } else {
                        fact("Played", stats.plays == 1 ? "1 time" : "\(stats.plays) times")
                        if let time = stats.listened { fact("Time listening", time) }
                        if let most = stats.mostPlayed {
                            fact("Most played", "\(most.title) (\(most.plays))")
                        }
                        if let rank {
                            fact("Among your artists", rank == 1 ? "Your most played" : "Number \(rank)")
                        }
                        if let last = stats.lastPlayed { fact("Last played", day(last)) }
                    }
                    if stats.favourites > 0 {
                        fact("Favourites", "\(stats.favourites)")
                    }
                    if let first = stats.firstAdded { fact("First added", day(first)) }
                }
                VStack(alignment: .leading, spacing: 0) {
                    Text("Songs").font(.headline).padding(.bottom, 4)
                    ForEach(Array(tracks.enumerated()), id: \.element.id) { index, track in
                        MineSongRow(track: track, plays: track.trackId.flatMap { plays[$0]?.count } ?? 0) {
                            model.player.play(tracks, startAt: index)
                        }
                        Divider()
                    }
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func fact(_ what: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(what).foregroundStyle(.secondary)
            Spacer(minLength: 4)
            Text(value).multilineTextAlignment(.trailing).lineLimit(2)
        }
        .font(.callout)
    }

    private func day(_ date: Date) -> String {
        date.formatted(date: .abbreviated, time: .omitted)
    }
}

/// One of the owner's songs by the artist: its name and how often it's been played.
private struct MineSongRow: View {
    let track: Track
    let plays: Int
    let play: () -> Void
    @Environment(AppModel.self) private var model

    var body: some View {
        let playing = model.player.current?.id == track.id
        HStack(spacing: 8) {
            Image(systemName: playing ? "speaker.wave.2.fill" : "play.fill")
                .font(.caption)
                .foregroundStyle(playing ? Color.accentColor : .secondary)
                .frame(width: 14)
            Text(track.title).fontWeight(playing ? .semibold : .regular).lineLimit(1)
            Spacer(minLength: 4)
            if plays > 0 {
                Text(plays == 1 ? "1 play" : "\(plays) plays")
                    .font(.caption)
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 5)
        .contentShape(Rectangle())
        .onTapGesture(count: 2, perform: play)
        .takesFirstClick()
        .contextMenu { SongActions(songs: [track], play: play).environment(model) }
        .help("Double-click to play")
    }
}

/// The Discover side of an artist: their page on YouTube Music, or how asking for it is
/// going.
private struct ArtistDiscoverPane: View {
    let page: ArtistBrowser

    var body: some View {
        switch page.phase {
        case .idle:
            Color.clear
        case .loading(let name):
            VStack(spacing: 12) {
                ProgressView()
                Text("Asking the music service about \(name)…")
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        case .missing(let name):
            Message(
                symbol: "person.crop.circle.badge.questionmark", title: "No artist found",
                text: "The music service has no artist called “\(name)”. Check the spelling, or try "
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
        // Beside the picture when there's room; under it when the page is narrow.
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .center, spacing: 20) {
                banner
                about.frame(minWidth: 300, alignment: .leading)
            }
            VStack(alignment: .leading, spacing: 12) {
                banner
                about
            }
        }
    }

    /// YouTube Music's picture of an artist is a wide banner.
    private var banner: some View {
        RemotePicture(address: info.thumbnail, corner: 12, size: .large, placeholder: "person.fill")
            .frame(width: 288, height: 120)
            .shadow(color: .black.opacity(0.2), radius: 8, y: 3)
    }

    private var about: some View {
        Group {
            VStack(alignment: .leading, spacing: 6) {
                Text(info.name)
                    .font(.largeTitle.weight(.bold))
                    .heading()
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
                    .help("Play the songs listed below, from the music service. Nothing is saved.")
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
            Text("About").font(.title3.weight(.semibold)).heading()
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
        let title = HStack(spacing: 12) {
            Text(page.allSongs == nil ? "Top Songs" : "Songs")
                .font(.title3.weight(.semibold))
                .heading()
            if page.allSongs != nil {
                Text("\(listed.count)" + (page.moreSongs ? "+" : ""))
                    .foregroundStyle(.secondary)
            }
        }
        let buttons = HStack(spacing: 12) {
            if page.loadingSongs {
                ProgressView().controlSize(.small)
                Text("Fetching their songs…").foregroundStyle(.secondary)
            } else if page.allSongs == nil, info.songsPlaylistId != nil {
                Button("Show All Songs", systemImage: "list.bullet") { page.showAllSongs() }
                    .help("Every song the music service lists for them, up to 300, with lengths")
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
        .fixedSize()
        return VStack(alignment: .leading, spacing: 8) {
            // The buttons beside the heading when there's room; under it when there isn't.
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 12) {
                    title
                    Spacer()
                    buttons
                }
                VStack(alignment: .leading, spacing: 8) {
                    title
                    buttons
                }
            }
            if let problem = page.songsProblem {
                Text(problem).font(.callout).foregroundStyle(.orange)
            }
            if listed.isEmpty {
                Text("The music service lists no songs for them.").foregroundStyle(.secondary)
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
                Text(title).font(.title3.weight(.semibold)).heading()
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
                Text("Fans Also Like").font(.title3.weight(.semibold)).heading()
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
                    Text(album.title).font(.title2.weight(.semibold)).heading().lineLimit(2)
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
                Text("The music service lists no songs that can be played for this one.")
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
