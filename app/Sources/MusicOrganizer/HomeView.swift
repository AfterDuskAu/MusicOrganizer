import MusicOrganizerKit
import Observation
import SwiftUI

/// What Home fetches for itself and keeps while the app is open: a list for each row
/// that comes from an add-on, and the newest videos of the channels followed.
@MainActor
@Observable
final class HomeLists {
    private var lists: [String: MediaList] = [:]
    private(set) var newVideos: [VideoHit] = []
    private(set) var newVideosWorking = false
    @ObservationIgnored private var newVideosFor: [String] = []

    func list(_ id: String) -> MediaList {
        if let found = lists[id] { return found }
        let made = MediaList()
        lists[id] = made
        return made
    }

    /// The latest few videos from each of the first channels followed, newest first.
    /// One paced request for each channel, made once for the channels as they stand.
    func loadNewVideos(_ model: AppModel, channels: [ChannelRef]) async {
        let wanted = channels.prefix(Self.channelsAsked).map(\.channelId)
        guard wanted != newVideosFor, !newVideosWorking else { return }
        newVideosWorking = true
        var found: [VideoHit] = []
        for channel in wanted {
            if let page = try? await model.ask(
                "channel.videos", ["channel_id": channel, "limit": Self.videosEach], as: ChannelPage.self)
            {
                found += page.videos
                newVideos = VideoSort.newest.arranged(found)
            }
        }
        (newVideosFor, newVideosWorking) = (wanted, false)
    }

    static let channelsAsked = 6
    static let videosEach = 4
}

/// Home (the owner's drawing, 2026-10-08): rows of what's theirs and what's suggested,
/// each sliding sideways, with More at its end. Overview chooses the rows.
struct HomeView: View {
    @Environment(AppModel.self) private var model
    @State private var lists = HomeLists()
    @State private var editing = false

    var body: some View {
        let all = HomeSection.all(movieGenres: genres("movie"), seriesGenres: genres("series"))
        let sections = model.homeLayout.sections(from: all)
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Text("Home").font(.title2.weight(.semibold)).heading()
                Spacer()
                Button("Overview", systemImage: "slider.horizontal.3") { editing = true }
                    .help("Choose what's on your Home page, and in what order")
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 12)
            Divider()
            if sections.isEmpty {
                VStack(spacing: 10) {
                    Text("Your Home page is empty.").foregroundStyle(.secondary)
                    Button("Choose What's on It…") { editing = true }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 26) {
                        ForEach(sections) { section in
                            HomeRow(section: section, lists: lists)
                        }
                    }
                    .padding(.vertical, 18)
                }
            }
        }
        .task(id: model.phase) { await model.media.load(model) }
        .sheet(isPresented: $editing) {
            HomeOverview(all: all).environment(model).dressed()
        }
    }

    /// The genres a kind's Popular list can be asked by: what its genre rows are made of.
    private func genres(_ type: String) -> [String] {
        model.media.catalogs(of: type).first { $0.catalog.id == "top" }?.catalog.genres ?? []
    }
}

/// One row of Home: its name, its cards sliding sideways, and More.
private struct HomeRow: View {
    let section: HomeSection
    let lists: HomeLists
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            // View More sits on the row's own line, clear of the cards: at the end of
            // the sliding row a click on it went to the card underneath (the owner,
            // 2026-10-08).
            HStack(alignment: .firstTextBaseline) {
                Text(section.title).font(.title3.weight(.semibold)).heading()
                Spacer()
                Button("View More", systemImage: "arrow.right") { more() }
                    .help("Open the page this row is from")
            }
            .padding(.horizontal, 20)
            ScrollView(.horizontal) {
                LazyHStack(alignment: .top, spacing: 16) { cards }
                    .padding(.horizontal, 20)
                    .padding(.vertical, 4)
            }
            .frame(height: height)
        }
    }

    private var height: CGFloat {
        if hasNothingYet { return 44 }  // one line saying so, not a row's worth of space
        return switch section.group {
        case .songs: 212
        case .channels: ["favourites", "recommended"].contains(section.kind) ? 176 : 190
        case .movies, .series: 250
        }
    }

    /// A row of the owner's own things that has none in it yet.
    private var hasNothingYet: Bool {
        switch (section.group, section.kind) {
        case (.songs, "recommended"): model.whatsNew.picks.isEmpty
        case (.songs, _): tracks.isEmpty
        case (.channels, "favourites"), (.channels, "new"): model.followedChannels.isEmpty
        case (.channels, "continue"): model.history.unfinished(.video).isEmpty
        case (.channels, "recent"): model.history.recent(.video).isEmpty
        case (.movies, "continue"), (.series, "continue"): model.history.unfinished(historyKind).isEmpty
        case (.movies, "recent"), (.series, "recent"): model.history.recent(historyKind).isEmpty
        case (.movies, "favourites"), (.series, "favourites"):
            model.mediaFavourites.of(type: type).isEmpty
        default: false
        }
    }

    @ViewBuilder
    private var cards: some View {
        switch section.group {
        case .songs: songs
        case .channels: channels
        case .movies, .series: films
        }
    }

    // MARK: songs

    @ViewBuilder
    private var songs: some View {
        switch section.kind {
        case "recommended":
            let page = model.whatsNew
            let picks = Array(page.picks.prefix(Self.most))
            if picks.isEmpty {
                note(
                    page.working
                        ? "Finding songs you might like…"
                        : page.problem ?? "Songs like yours show up here once some have been found.")
                    .task(id: model.phase) {
                        if !page.hasAsked, model.phase == .ready { page.find([.library], count: 20) }
                    }
            }
            ForEach(Array(picks.enumerated()), id: \.element.id) { index, pick in
                SongCard(track: pick.result.track, under: pick.artistName, artist: pick.artists.first) {
                    model.player.play(picks.map(\.result.track), startAt: index)
                } extra: {
                    if model.canDownload(pick) {
                        Button("Download", systemImage: "arrow.down.circle") { model.download(pick) }
                            .controlSize(.small)
                    }
                }
            }
        default:
            let tracks = Array(tracks.prefix(Self.most))
            if tracks.isEmpty { note(emptySongs) }
            ForEach(Array(tracks.enumerated()), id: \.element.id) { index, track in
                SongCard(track: track, under: track.artist ?? "", artist: track.albumArtist ?? track.artist) {
                    model.player.play(tracks, startAt: index)
                } extra: {
                    EmptyView()
                }
            }
        }
    }

    private var tracks: [Track] {
        switch section.kind {
        case "favourites": model.everything.tracks(withIDs: model.listening.favourites)
        case "mostPlayed": model.library.mostPlayed(model.listening.plays, limit: Self.most)
        case "recentlyAdded": model.library.recentlyAdded()
        case "downloads": model.downloaded
        default: []
        }
    }

    private var emptySongs: String {
        switch section.kind {
        case "favourites": "Click the heart beside a song and it shows up here."
        case "mostPlayed": "The songs you play most show up here."
        case "downloads": "Songs you download show up here."
        default: "Nothing here yet."
        }
    }

    // MARK: channels and their videos

    @ViewBuilder
    private var channels: some View {
        switch section.kind {
        case "favourites":
            if model.followedChannels.isEmpty {
                note("Channels you follow show up here. Open one and click Follow.")
            }
            ForEach(model.followedChannels) { channel in
                Button { model.open(channel) } label: { MediaCard(channel: channel).frame(width: 112) }
                    .buttonStyle(.plain)
            }
        case "recommended":
            let media = model.media
            let list = lists.list(section.id)
            let followed = Set(model.followedChannels.map(\.channelId))
            let found = list.items.compactMap(ChannelRef.init(item:)).filter { !followed.contains($0.channelId) }
            if found.isEmpty { note(list.problem ?? "Finding channels…") }
            ForEach(found.prefix(Self.most)) { channel in
                Button { model.open(channel) } label: { MediaCard(channel: channel).frame(width: 112) }
                    .buttonStyle(.plain)
            }
            Color.clear.frame(width: 1, height: 1)
                .task(id: media.loaded) {
                    if let source = media.catalogs(of: "channel").first {
                        await list.load(model, addon: source.addon, catalog: source.catalog)
                    }
                }
        case "new":
            if model.followedChannels.isEmpty {
                note("The newest videos from the channels you follow show up here.")
            } else if lists.newVideos.isEmpty {
                note(lists.newVideosWorking ? "Looking at your channels…" : "Nothing new was found.")
            }
            ForEach(lists.newVideos.prefix(Self.most)) { video in
                VideoCard(
                    name: video.title, detail: [video.channel ?? "", video.age()].filter { !$0.isEmpty }
                        .joined(separator: " · "),
                    picture: video.thumbnail, progress: nil
                ) {
                    model.playVideos(
                        [video.result], startAt: 0, channels: VideoHit.channels(of: [video]))
                }
            }
            Color.clear.frame(width: 1, height: 1)
                .task(id: model.followedChannels) {
                    await lists.loadNewVideos(model, channels: model.followedChannels)
                }
        case "downloaded":
            DownloadedVideoCards()
        default:
            let entries = section.kind == "continue"
                ? model.history.unfinished(.video) : model.history.recent(.video)
            if entries.isEmpty {
                note(
                    section.kind == "continue"
                        ? "A video you stop part way through shows up here, to carry on with."
                        : "Videos you watch show up here.")
            }
            ForEach(entries.prefix(Self.most)) { entry in
                VideoCard(
                    name: entry.name, detail: entry.detail ?? "", picture: entry.picture,
                    progress: entry.isUnfinished ? entry.progress : nil
                ) {
                    model.resume(entry)
                }
                .contextMenu {
                    if let id = entry.channelId {
                        Button("Open Its Channel") {
                            model.open(ChannelRef(channelId: id, name: entry.detail ?? "Channel"))
                        }
                    }
                    Button("Remove From This Row") { model.forgetWatched(entry) }
                }
            }
        }
    }

    // MARK: movies and series

    private var type: String { section.group.mediaType ?? "movie" }
    private var historyKind: WatchHistory.Entry.Kind { section.group == .series ? .series : .movie }

    @ViewBuilder
    private var films: some View {
        switch section.kind {
        case "continue", "recent":
            let entries = section.kind == "continue"
                ? model.history.unfinished(historyKind) : model.history.recent(historyKind)
            if entries.isEmpty {
                note(
                    section.kind == "continue"
                        ? "One you stop part way through shows up here, to carry on with."
                        : "What you watch shows up here.")
            }
            ForEach(entries.prefix(Self.most)) { entry in
                if let item = entry.item {
                    Button { model.open(item) } label: {
                        MediaCard(item: item)
                            .frame(width: 122)
                            .overlay(alignment: .top) {
                                if entry.isUnfinished {
                                    ProgressView(value: entry.progress)
                                        .tint(.accentColor)
                                        .padding(.horizontal, 6)
                                        .padding(.top, 172)
                                }
                            }
                    }
                    .buttonStyle(.plain)
                    .help(entry.detail ?? item.name)
                    .contextMenu { Button("Remove From This Row") { model.forgetWatched(entry) } }
                }
            }
        case "favourites":
            let items = model.mediaFavourites.of(type: type)
            if items.isEmpty { note("Click the heart on one's page and it shows up here.") }
            ForEach(items.prefix(Self.most)) { item in filmCard(item) }
        default:
            catalogCards
        }
    }

    /// A row that's one of the film add-on's lists: Popular, New, Best Rated, a genre,
    /// or (Recommended) Popular in the genre met most among the owner's own.
    @ViewBuilder
    private var catalogCards: some View {
        let media = model.media
        let list = lists.list(section.id)
        let recommended = section.kind == "recommended"
        let taste = MediaFavourites.leadingGenre(
            model.mediaFavourites.of(type: type) + model.history.recent(historyKind).compactMap(\.item))
        if recommended, taste == nil {
            note("Mark a few favourites, or watch something, and suggestions show up here.")
        } else {
            if list.items.isEmpty { note(list.problem ?? "Loading…") }
            ForEach(list.items.prefix(Self.most)) { item in filmCard(item) }
            Color.clear.frame(width: 1, height: 1)
                .task(id: "\(media.loaded)|\(taste ?? "")") {
                    let wanted = ["new": "year", "best": "imdbRating"][section.kind] ?? "top"
                    guard let source = media.catalogs(of: type).first(where: { $0.catalog.id == wanted })
                    else { return }
                    let genre = recommended ? taste : section.genre
                    await list.load(
                        model, addon: source.addon, catalog: source.catalog,
                        genre: source.catalog.genre(chosen: genre ?? ""), also: section.also ?? "")
                }
        }
    }

    private func filmCard(_ item: MediaItem) -> some View {
        Button { model.open(item) } label: { MediaCard(item: item).frame(width: 122) }
            .buttonStyle(.plain)
    }

    private func note(_ text: String) -> some View {
        Text(text)
            .foregroundStyle(.secondary)
            .frame(maxHeight: .infinity)
            .padding(.trailing, 12)
    }

    // MARK: More

    /// Open the page the row is a taste of.
    private func more() {
        switch (section.group, section.kind) {
        case (.songs, "favourites"): model.goTo = .favourites
        case (.songs, "recommended"):
            // Explore's What's New is where these are from, whichever tab was left showing.
            UserDefaults.standard.set(false, forKey: "musicExploreFind")
            model.goTo = .musicExplore
        case (.songs, "mostPlayed"): model.goTo = .mostPlayed
        case (.songs, "recentlyAdded"): model.goTo = .recentlyAdded
        case (.songs, _): model.goTo = .downloads
        case (.channels, "recommended"): model.goTo = .videoExplore
        case (.channels, _): model.goTo = .channels
        case (.movies, _), (.series, _):
            // A list's row opens that list in its Finder, at the row's genre.
            let name = section.group == .series ? "seriesFinder" : "movieFinder"
            let wanted = [
                "new": "year", "best": "imdbRating", "popular": "top", "genre": "top", "custom": "top",
            ][section.kind]
            if let wanted,
                let source = model.media.catalogs(of: type).first(where: { $0.catalog.id == wanted })
            {
                UserDefaults.standard.set("\(source.addon.id)|\(source.catalog.id)", forKey: "\(name)List")
                UserDefaults.standard.set(section.genre ?? "", forKey: "\(name)Genre")
                UserDefaults.standard.set(section.also ?? "", forKey: "\(name)Also")
            }
            model.goTo = section.group == .series ? .seriesFinder : .movieFinder
        }
    }

    /// Cards in a row at most: More has the rest.
    static let most = 30
}

/// A song on Home: its cover (click to play), its name and who it's by.
private struct SongCard<Extra: View>: View {
    let track: Track
    let under: String
    /// Whose page the button beside the name opens.
    let artist: String?
    let play: () -> Void
    @ViewBuilder let extra: () -> Extra
    @Environment(AppModel.self) private var model
    @State private var hovering = false

    var body: some View {
        let playing = model.player.current?.id == track.id
        VStack(alignment: .leading, spacing: 3) {
            Button(action: play) {
                CoverView(track: track, size: .medium, corner: 8)
                    .frame(width: 130, height: 130)
                    .overlay {
                        Image(systemName: playing ? "speaker.wave.2.fill" : "play.circle.fill")
                            .font(.system(size: 38))
                            .foregroundStyle(.white)
                            .shadow(radius: 6)
                            .opacity(hovering || playing ? 1 : 0)
                    }
            }
            .buttonStyle(.plain)
            Text(track.title).font(.callout.weight(playing ? .semibold : .medium)).lineLimit(1)
            // The artist's name, with the button for their page beside it, as on Explore.
            HStack(spacing: 5) {
                Text(under).font(.callout).foregroundStyle(.secondary).lineLimit(1)
                if let artist, !artist.isEmpty {
                    Button {
                        model.showArtist(artist)
                    } label: {
                        Image(systemName: "person.crop.circle").foregroundStyle(.secondary)
                    }
                    .buttonStyle(.plain)
                    .help("Artist: about \(artist), their songs and albums")
                }
            }
            extra()
        }
        .frame(width: 130, alignment: .leading)
        .onHover { hovering = $0 }
    }
}

/// A video on Home: its picture (click to play), its name, and how far through it is.
private struct VideoCard: View {
    let name: String
    let detail: String
    let picture: String?
    let progress: Double?
    let play: () -> Void
    @State private var hovering = false

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Button(action: play) {
                Color.clear
                    .frame(width: 208, height: 117)
                    .overlay { WebPicture(address: picture, symbol: "play.rectangle") }
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    .overlay {
                        Image(systemName: "play.circle.fill")
                            .font(.system(size: 38))
                            .foregroundStyle(.white)
                            .shadow(radius: 6)
                            .opacity(hovering ? 1 : 0)
                    }
                    .overlay(alignment: .bottom) {
                        if let progress {
                            ProgressView(value: progress).tint(.accentColor).padding(.horizontal, 6)
                                .padding(.bottom, 4)
                        }
                    }
            }
            .buttonStyle(.plain)
            Text(name).font(.callout.weight(.medium)).lineLimit(2)
            Text(detail).font(.callout).foregroundStyle(.secondary).lineLimit(1)
        }
        .frame(width: 208, alignment: .leading)
        .onHover { hovering = $0 }
    }
}

/// The videos downloaded from channels (Media, in Downloads), as cards.
private struct DownloadedVideoCards: View {
    @Environment(AppModel.self) private var model
    @State private var files: [VideoFiles.File] = []
    @State private var looked = false

    var body: some View {
        Group {
            if files.isEmpty {
                Text(looked ? "Videos you download show up here." : "Looking…")
                    .foregroundStyle(.secondary)
                    .frame(maxHeight: .infinity)
            }
            ForEach(files.prefix(HomeRow.most)) { file in
                VideoCard(name: file.name, detail: file.kind, picture: nil, progress: nil) {
                    model.playFilm(file.url, title: file.name)
                }
            }
        }
        .task(id: model.keptArrived) {
            let folder = ChannelsView.keptFolder
            files = await Task.detached { folder.map(VideoFiles.inside) ?? [] }.value
            looked = true
        }
    }
}

/// Home's Overview: which rows are on the page, in what order, and every row that
/// could be.
private struct HomeOverview: View {
    let all: [HomeSection]
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        let shown = model.homeLayout.sections(from: all)
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Your Home Page").font(.title2.weight(.semibold)).heading()
                    Text("Choose the rows it shows and put them in your order.")
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("Start Again") { model.homeLayout = HomeLayout() }
                    .help("Put the page back as it began")
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            .padding(16)
            Divider()
            List {
                Section("On Your Home Page, Top to Bottom") {
                    if shown.isEmpty { Text("Nothing yet: switch rows on below.").foregroundStyle(.secondary) }
                    ForEach(Array(shown.enumerated()), id: \.element.id) { index, section in
                        HStack {
                            Text(section.title)
                            Text(section.group.title).foregroundStyle(.secondary)
                            if section.kind == "custom" {
                                Text("made in its Finder").foregroundStyle(.tertiary)
                            }
                            Spacer()
                            Button("Move Up", systemImage: "chevron.up") {
                                model.homeLayout.move(section.id, by: -1)
                            }
                            .disabled(index == 0)
                            Button("Move Down", systemImage: "chevron.down") {
                                model.homeLayout.move(section.id, by: 1)
                            }
                            .disabled(index == shown.count - 1)
                            Button("Take Off", systemImage: "minus.circle") {
                                model.homeLayout.set(section.id, shown: false)
                            }
                        }
                        .labelStyle(.iconOnly)
                        .buttonStyle(.borderless)
                    }
                }
                ForEach(HomeSection.Group.allCases, id: \.self) { group in
                    Section(group.title) {
                        ForEach(all.filter { $0.group == group }) { section in
                            Toggle(
                                section.title,
                                isOn: Binding(
                                    get: { model.homeLayout.shows(section.id) },
                                    set: { model.homeLayout.set(section.id, shown: $0) }))
                        }
                    }
                }
            }
        }
        .frame(width: 560, height: 640)
    }
}
