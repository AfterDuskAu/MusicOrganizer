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
/// each sliding sideways, with More at its end. Customise Home chooses the rows.
struct HomeView: View {
    /// Home is the page showing: the arrows are its cards' then (`CardKeys`).
    var isActive = true
    @Environment(AppModel.self) private var model
    @State private var lists = HomeLists()
    @State private var editing = false
    @State private var keys = CardKeys()

    var body: some View {
        let all = HomeSection.all(movieGenres: genres("movie"), seriesGenres: genres("series"))
        let sections = model.homeLayout.sections(from: all)
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Text("Home").font(.title2.weight(.semibold)).heading()
                Spacer()
                Button("Customise Home", systemImage: "slider.horizontal.3") { editing = true }
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
                ScrollViewReader { page in
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 26) {
                            ForEach(sections) { section in
                                HomeRow(section: section, lists: lists).id(section.id)
                            }
                        }
                        .padding(.vertical, 18)
                    }
                    // The row the arrows have gone to is brought into sight.
                    .onChange(of: keys.shows) {
                        guard let row = keys.showing else { return }
                        withAnimation(.easeInOut(duration: 0.2)) { page.scrollTo(row, anchor: .center) }
                    }
                }
                .environment(keys)
            }
        }
        // The arrows move a highlight through the cards while Home is the page showing
        // (the owner, 2026-10-12), and Return opens the card it's on.
        .onChange(of: sections.map(\.id), initial: true) { keys.order = sections.map(\.id) }
        .onChange(of: isActive, initial: true) {
            if isActive {
                model.cardKeys = keys
            } else {
                keys.clear()
                if model.cardKeys === keys { model.cardKeys = nil }
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

/// One row of Home: its name with View More, and its cards a page at a time. Only whole
/// cards are shown, as many as the window has room for; an arrow at the row's end brings
/// the next ones, and one at its start goes back (the owner, 2026-10-08). A row that
/// comes from a list reads further on as it's paged through, for as long as there's more.
private struct HomeRow: View {
    let section: HomeSection
    let lists: HomeLists
    @Environment(AppModel.self) private var model
    @Environment(CardKeys.self) private var keys: CardKeys?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            // View More sits on the row's own line, clear of the cards: at the end of
            // the row a click on it went to the card underneath (the owner, 2026-10-08).
            HStack(alignment: .firstTextBaseline) {
                Text(section.title).font(.title3.weight(.semibold)).heading()
                Spacer()
                Button("View More", systemImage: "arrow.right") { more() }
                    .help("Open the page this row is from")
            }
            .padding(.leading, 44)
            .padding(.trailing, 20)
            content
        }
        .environment(\.cardRow, section.id)
        .onAppear { keys?.saw(section.id) }
        .task(id: taskKey) { await load(adding: false) }
    }

    @ViewBuilder
    private var content: some View {
        switch section.group {
        case .songs: songs
        case .channels: channels
        case .movies, .series, .anime: films
        }
    }

    // MARK: songs

    @ViewBuilder
    private var songs: some View {
        if section.kind == "recommended" {
            let page = model.whatsNew
            let picks = page.picks
            if picks.isEmpty {
                note(
                    page.working
                        ? "Finding songs you might like…"
                        : page.problem ?? "Songs like yours show up here once some have been found.")
            } else {
                PagedCards(
                    items: picks, width: 130, height: 212,
                    canLoadMore: !page.noMore, loadingMore: page.loadingMore, loadMore: { page.more() },
                    open: { pick in
                        let index = picks.firstIndex { $0.id == pick.id } ?? 0
                        model.player.play(picks.map(\.result.track), startAt: index)
                    }
                ) { pick in
                    SongCard(track: pick.result.track, under: pick.artistName, artist: pick.artists.first) {
                        let index = picks.firstIndex { $0.id == pick.id } ?? 0
                        model.player.play(picks.map(\.result.track), startAt: index)
                    } extra: {
                        if model.canDownload(pick) {
                            Button("Download", systemImage: "arrow.down.circle") { model.download(pick) }
                                .controlSize(.small)
                        }
                    }
                }
            }
        } else {
            let tracks = tracks
            if tracks.isEmpty {
                note(emptySongs)
            } else {
                PagedCards(
                    items: tracks, width: 130, height: 196,
                    open: { track in
                        model.player.play(tracks, startAt: tracks.firstIndex { $0.id == track.id } ?? 0)
                    }
                ) { track in
                    SongCard(track: track, under: track.artist ?? "", artist: track.albumArtist ?? track.artist) {
                        model.player.play(tracks, startAt: tracks.firstIndex { $0.id == track.id } ?? 0)
                    } extra: {
                        EmptyView()
                    }
                }
            }
        }
    }

    private var tracks: [Track] {
        switch section.kind {
        case "favourites": model.everything.tracks(withIDs: model.listening.favourites)
        case "mostPlayed": model.library.mostPlayed(model.listening.plays)
        case "recentlyAdded": Array(model.library.recentlyAdded().prefix(Self.mostOwn))
        case "downloads": Array(model.downloaded.prefix(Self.mostOwn))
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
            } else {
                PagedCards(
                    items: model.followedChannels, width: 112, height: 176, open: { model.open($0) }
                ) { channel in
                    Button { model.open(channel) } label: { MediaCard(channel: channel) }
                        .buttonStyle(.plain)
                }
            }
        case "recommended":
            let list = lists.list(section.id)
            let followed = Set(model.followedChannels.map(\.channelId))
            let found = list.items.compactMap(ChannelRef.init(item:)).filter { !followed.contains($0.channelId) }
            if found.isEmpty {
                note(list.problem ?? "Finding channels…")
            } else {
                PagedCards(
                    items: found, width: 112, height: 176, canLoadMore: list.more,
                    loadingMore: list.working, loadMore: { Task { await load(adding: true) } },
                    open: { model.open($0) }
                ) { channel in
                    Button { model.open(channel) } label: { MediaCard(channel: channel) }
                        .buttonStyle(.plain)
                }
            }
        case "new", "unwatched":
            // "unwatched": the same videos, without the ones watched past a quarter. A
            // video has no heart of its own, so it's the channels followed that count.
            let videos = section.kind == "new"
                ? lists.newVideos : lists.newVideos.filter { !model.history.isBegun($0.videoId) }
            if model.followedChannels.isEmpty {
                note(
                    section.kind == "new"
                        ? "The newest videos from the channels you follow show up here."
                        : "New videos from the channels you follow that you haven't watched show up here.")
            } else if videos.isEmpty {
                note(lists.newVideosWorking ? "Looking at your channels…" : "Nothing new was found.")
            } else {
                PagedCards(
                    items: videos, width: 208, height: 190,
                    open: { video in
                        model.playVideos([video.result], startAt: 0, channels: VideoHit.channels(of: [video]))
                    }
                ) { video in
                    VideoCard(
                        name: video.title,
                        detail: [video.channel ?? "", video.age()].filter { !$0.isEmpty }
                            .joined(separator: " · "),
                        picture: video.thumbnail, progress: nil
                    ) {
                        model.playVideos(
                            [video.result], startAt: 0, channels: VideoHit.channels(of: [video]))
                    }
                }
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
            } else {
                PagedCards(items: entries, width: 208, height: 190, open: { model.resume($0) }) { entry in
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
    }

    // MARK: movies and series

    private var type: String { section.group.mediaType ?? "movie" }
    private var historyKind: WatchHistory.Entry.Kind { section.group == .series ? .series : .movie }
    private var isRecommended: Bool { section.kind == "recommended" }
    private var isAnime: Bool { section.group == .anime }

    /// What Recommended goes by (the owner, 2026-10-15: "takes your watchlisted watched"):
    /// what's on their Watchlist, what they've watched and what they've hearted, of the
    /// row's kind. Anime is apart from the rest: its genres are its own plugin's.
    private var taste: [MediaItem] {
        let kind = section.group == .movies ? "movie" : "series"
        var seen = Set<String>()
        return (model.watchlist.items + model.history.entries.compactMap(\.item) + model.mediaFavourites.items)
            .filter { item in
                (isAnime ? item.anime == true : item.anime != true && item.type == kind)
                    && seen.insert(item.id).inserted
            }
    }

    /// The list Recommended asks, and the owner's leading genres that it offers.
    private var recommending: (source: (addon: Addon, catalog: Addon.Catalog), genres: [String])? {
        guard let source = listSource(for: isAnime ? "popular" : "top") else { return nil }
        let offered = Recommending.genres(of: taste, most: 30).filter(source.catalog.genres.contains)
        return offered.isEmpty ? nil : (source, Array(offered.prefix(3)))
    }

    /// The plugin's list a row is from: for movies and series, Cinemeta's by its id
    /// ("top", "year", "imdbRating"); for anime, the first list of anime whose id has
    /// the word in it, else the first that can be asked by genre.
    private func listSource(for wanted: String) -> (addon: Addon, catalog: Addon.Catalog)? {
        let lists = model.media.catalogs(of: type)
        guard isAnime else { return lists.first { $0.catalog.id == wanted } }
        return lists.first { $0.catalog.id.contains(wanted) && !$0.catalog.genres.isEmpty }
            ?? lists.first { !$0.catalog.genres.isEmpty } ?? lists.first
    }

    @ViewBuilder
    private var films: some View {
        switch section.kind {
        case "continue", "recent":
            let entries = (section.kind == "continue"
                ? model.history.unfinished(historyKind) : model.history.recent(historyKind))
                .filter { $0.item != nil }
            if entries.isEmpty {
                note(
                    section.kind == "continue"
                        ? "One you stop part way through shows up here, to carry on with."
                        : "What you watch shows up here.")
            } else {
                PagedCards(
                    items: entries, width: 122, height: 250,
                    open: { entry in
                        guard let item = entry.item else { return }
                        if section.kind == "continue" { model.resume(entry) } else { model.open(item) }
                    }
                ) { entry in
                    if let item = entry.item {
                        // Continue Watching plays it from where it was left (the owner,
                        // 2026-10-12); Recently Watched opens its page, as before.
                        Button {
                            if section.kind == "continue" { model.resume(entry) } else { model.open(item) }
                        } label: {
                            MediaCard(item: item)
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
                        .contextMenu {
                            Button("Open Its Page") { model.open(item) }
                            Button("Remove From This Row") { model.forgetWatched(entry) }
                        }
                    }
                }
            }
        case "favourites":
            let items = model.mediaFavourites.of(type: type)
            if items.isEmpty {
                note("Click the heart on one's page and it shows up here.")
            } else {
                PagedCards(items: items, width: 122, height: 250, open: { model.open($0) }) { filmCard($0) }
            }
        case "unwatched":
            // Hearted, and not watched past a quarter (the owner, 2026-10-12).
            let items = model.mediaFavourites.of(type: type).filter { !model.history.isBegun($0.id) }
            if items.isEmpty {
                note(
                    "One you've given a heart but not watched a quarter of yet shows up "
                        + "here. Click the heart on one's page.")
            } else {
                PagedCards(items: items, width: 122, height: 250, open: { model.open($0) }) { filmCard($0) }
            }
        default:
            // One of the film add-on's lists: Popular, New, Best Rated, a genre, a search
            // by two genres, or Recommended: the popular lists of the owner's leading
            // genres, closest to their taste first, without what they've seen or listed.
            let list = lists.list(section.id)
            let seen = isRecommended ? Set(taste.map(\.id)) : []
            let items = seen.isEmpty ? list.items : list.items.filter { !seen.contains($0.id) }
            if isRecommended, recommending == nil {
                note(
                    model.media.loaded
                        ? "Put a few on your Watchlist, watch something, or give one a heart, and "
                            + "suggestions like them show up here."
                        : "Loading…")
            } else if items.isEmpty {
                note(list.problem ?? "Loading…")
            } else {
                PagedCards(
                    items: items, width: 122, height: 250, canLoadMore: list.more,
                    loadingMore: list.working, loadMore: { Task { await load(adding: true) } },
                    open: { model.open(isAnime ? $0.asAnime : $0) }
                ) { filmCard($0) }
            }
        }
    }

    private func filmCard(_ item: MediaItem) -> some View {
        // What's opened from a list of anime is marked as anime, as in Anime Finder.
        Button { model.open(isAnime ? item.asAnime : item) } label: { MediaCard(item: item) }
            .buttonStyle(.plain)
    }

    private func note(_ text: String) -> some View {
        Text(text)
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 44)
            .padding(.vertical, 6)
    }

    // MARK: what a row fetches

    /// What the row's fetching depends on: asked again when this changes.
    private var taskKey: String {
        switch (section.group, section.kind) {
        case (.songs, "recommended"): "\(model.phase)"
        case (.channels, "new"): model.followedChannels.map(\.channelId).joined(separator: ",")
        default: "\(model.media.loaded)|\(isRecommended ? recommending?.genres.joined(separator: ",") ?? "" : "")"
        }
    }

    /// Fetch what the row shows, or with `adding` the next stretch of its list.
    private func load(adding: Bool) async {
        let media = model.media
        switch (section.group, section.kind) {
        case (.songs, "recommended"):
            let page = model.whatsNew
            if !page.hasAsked, model.phase == .ready { page.find([.library], count: 20) }
        case (.channels, "new"), (.channels, "unwatched"):
            await lists.loadNewVideos(model, channels: model.followedChannels)
        case (.channels, "recommended"):
            if let source = media.catalogs(of: "channel").first {
                await lists.list(section.id).load(
                    model, addon: source.addon, catalog: source.catalog, adding: adding)
            }
        case (_, "recommended"):
            guard let (source, genres) = recommending else { return }
            await lists.list(section.id).recommend(
                model, addon: source.addon, catalog: source.catalog, genres: genres, taste: taste,
                anime: isAnime)
        case (.anime, _):
            guard let source = listSource(for: "popular") else { return }
            await lists.list(section.id).load(model, addon: source.addon, catalog: source.catalog, adding: adding)
        case (.movies, let kind), (.series, let kind):
            guard !["continue", "recent", "favourites", "unwatched"].contains(kind) else { return }
            let wanted = ["new": "year", "best": "imdbRating"][kind] ?? "top"
            guard let source = media.catalogs(of: type).first(where: { $0.catalog.id == wanted })
            else { return }
            await lists.list(section.id).load(
                model, addon: source.addon, catalog: source.catalog,
                genre: source.catalog.genre(chosen: section.genre ?? ""), also: section.also ?? "",
                adding: adding)
        default:
            break
        }
    }

    // MARK: View More

    /// Open the page the row is a taste of.
    private func more() {
        switch (section.group, section.kind) {
        case (.songs, "favourites"): model.goTo = .favourites
        case (.songs, "recommended"):
            // Explore's What's New is where these are from, whichever tab was left showing.
            UserDefaults.standard.set(MusicFinderTab.whatsNew.rawValue, forKey: MusicFinderTab.key)
            model.clearYouTubeSearch()
            model.goTo = .youtube
        case (.songs, "mostPlayed"): model.goTo = .mostPlayed
        case (.songs, "recentlyAdded"): model.goTo = .recentlyAdded
        case (.songs, _): model.goTo = .downloads
        case (.channels, "recommended"): model.goTo = .videoFinder
        case (.channels, _): model.goTo = .channels
        case (.anime, _):
            // Its lists, not a search left in its box (2026-10-17), as from the sidebar.
            model.clickedInSidebar(.animeFinder)
            model.goTo = .animeFinder
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
            let finder: SidebarItem = section.group == .series ? .seriesFinder : .movieFinder
            model.clickedInSidebar(finder)
            model.goTo = finder
        }
    }

    /// Of the owner's own newest things, how many a row offers: the page has the rest.
    static let mostOwn = 60
}

/// A row's cards, a page at a time: as many whole cards as fit across, never part of
/// one, with an arrow at the end for the next page and one at the start to go back.
/// The cards keep their size; what's left over is shared out between them.
private struct PagedCards<Item: Identifiable, Card: View>: View {
    let items: [Item]
    let width: CGFloat
    let height: CGFloat
    /// There's more to fetch beyond `items`: the forward arrow stays, and asks for it.
    var canLoadMore = false
    var loadingMore = false
    var loadMore: () -> Void = {}
    /// What a click on a card does: what Return does on the card the arrows' highlight
    /// is on (`CardKeys`).
    let open: (Item) -> Void
    @ViewBuilder let card: (Item) -> Card
    @Environment(CardKeys.self) private var keys: CardKeys?
    @Environment(\.cardRow) private var row
    /// The first card showing.
    @State private var start = 0
    /// The forward arrow was clicked on the last page: go on when more has arrived.
    @State private var waiting = false

    private static var gap: CGFloat { 16 }
    /// Room kept at each end of the row for its arrow. The arrows stand beside the cards,
    /// never over one: a button laid over a card lost its clicks to the card (seen
    /// twice, 2026-10-08).
    private static var gutter: CGFloat { 44 }

    var body: some View {
        GeometryReader { room in
            let inner = room.size.width - 2 * Self.gutter
            let fits = HomePaging.fitting(inner, card: width, gap: Self.gap)
            let from = min(start, max(items.count - 1, 0))
            let showing = Array(items.dropFirst(from).prefix(fits))
            let hasNext = from + fits < items.count
            // A full page is spread across the room; a short last page keeps the gap.
            let spread = showing.count == fits && fits > 1
                ? (inner - CGFloat(fits) * width) / CGFloat(fits - 1)
                : Self.gap
            HStack(alignment: .center, spacing: 0) {
                // (A ZStack, not a Group: the room is kept whether the arrow is there or
                // not, so nothing moves sideways when it comes and goes.)
                ZStack {
                    if from > 0 {
                        arrow("chevron.left", "Back") { start = max(from - fits, 0) }
                    }
                }
                .frame(width: Self.gutter, height: height)
                HStack(alignment: .top, spacing: spread) {
                    ForEach(Array(showing.enumerated()), id: \.element.id) { place, item in
                        card(item).frame(width: width, alignment: .top)
                            // The arrows' highlight, round the card it's on.
                            .overlay {
                                if keys?.isOn(row, place) == true {
                                    RoundedRectangle(cornerRadius: 10)
                                        .strokeBorder(Color.accentColor, lineWidth: 3)
                                        .padding(-6)
                                        .allowsHitTesting(false)
                                }
                            }
                    }
                }
                .frame(width: max(inner, width), alignment: .leading)
                // What the arrows may ask of this row, told again whenever what it's
                // showing changes: how many cards, its next ones or the ones before,
                // and a card opened.
                .onChange(of: RowShape(ids: showing.map { AnyHashable($0.id) }, fits: fits, more: hasNext), initial: true) {
                    keys?.tell(
                        row,
                        CardKeys.Row(
                            count: showing.count,
                            page: { forward in
                                if forward {
                                    guard hasNext else {
                                        if canLoadMore, !loadingMore { loadMore() }
                                        return nil
                                    }
                                    start = from + fits
                                    return min(fits, items.count - (from + fits))
                                }
                                guard from > 0 else { return nil }
                                start = max(from - fits, 0)
                                return fits
                            },
                            open: { place in
                                if showing.indices.contains(place) { open(showing[place]) }
                            }))
                }
                ZStack {
                    if hasNext || canLoadMore {
                        arrow("chevron.right", loadingMore && !hasNext ? "Finding more…" : "More") {
                            if hasNext {
                                start = from + fits
                            } else if showing.count == fits {
                                waiting = true  // nothing further is here yet: on when it is
                            }
                            // (A page that isn't full just fills up as more arrives.)
                            // Near the end of what's here: the next stretch is asked for,
                            // so it has usually arrived by the time it's wanted.
                            if canLoadMore, !loadingMore, from + 2 * fits >= items.count { loadMore() }
                        }
                    }
                }
                .frame(width: Self.gutter, height: height)
            }
            .onChange(of: items.count) { _, new in
                if waiting, new > from + fits {  // what was waited for has arrived
                    start = from + fits
                    waiting = false
                }
                if new <= from { start = max(new - fits, 0) }
            }
            .onChange(of: loadingMore) { _, busy in
                if !busy { waiting = false }  // it came back with nothing more
            }
        }
        .frame(height: height)
    }

    /// What a row is showing, for telling when the keys' picture of it is out of date.
    private struct RowShape: Equatable {
        let ids: [AnyHashable]
        let fits: Int
        let more: Bool
    }

    private func arrow(_ symbol: String, _ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.title2.weight(.semibold))
                .foregroundStyle(.white)
                .frame(width: 34, height: 54)
                .background(.black.opacity(0.75), in: RoundedRectangle(cornerRadius: 8))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(title)
    }
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
        .frame(maxWidth: .infinity, alignment: .leading)
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
        .frame(maxWidth: .infinity, alignment: .leading)
        .onHover { hovering = $0 }
    }
}

/// The videos downloaded from channels (the Videos folder), as cards.
private struct DownloadedVideoCards: View {
    @Environment(AppModel.self) private var model
    @State private var files: [VideoFiles.File] = []
    @State private var looked = false

    var body: some View {
        Group {
            if files.isEmpty {
                Text(looked ? "Videos you download show up here." : "Looking…")
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 44)
                    .padding(.vertical, 6)
            } else {
                PagedCards(
                    items: files, width: 208, height: 190, open: { model.playFilm($0.url, title: $0.name) }
                ) { file in
                    VideoCard(name: file.name, detail: file.kind, picture: nil, progress: nil) {
                        model.playFilm(file.url, title: file.name)
                    }
                }
            }
        }
        .task(id: "\(model.keptArrived) \(model.videosFolder?.path ?? "")") {
            let folder = model.videosFolder
            files = await Task.detached { folder.map(VideoFiles.inside) ?? [] }.value
            looked = true
        }
    }
}

/// Customise Home (first called Overview): which rows are on the page, in what order, and every row that
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
                    Text("Choose the rows it shows, and drag them into your order.")
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("Start Again") { model.homeLayout = HomeLayout() }
                    .help("Put the page back as it began")
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            .padding(16)
            Divider()
            // The rows on the page are a list of their own. As one section of a longer
            // list, macOS drew the line that shows where a dragged row will land in the
            // wrong place, down among the ticks (the owner saw it, 2026-10-08).
            Text("On Your Home Page, Top to Bottom (Drag to Reorder)")
                .font(.subheadline.weight(.semibold))
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 16)
                .padding(.top, 12)
            if shown.isEmpty {
                Text("Nothing yet: switch rows on below.")
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(16)
            } else {
                List {
                    ForEach(shown) { section in
                        HStack {
                            Image(systemName: "line.3.horizontal").foregroundStyle(.tertiary)
                                .help("Drag to move this row")
                            Text(section.title)
                            Text(section.group.title).foregroundStyle(.secondary)
                            if section.kind == "custom" {
                                Text("made in its Finder").foregroundStyle(.tertiary)
                            }
                            Spacer()
                            Button("Take Off", systemImage: "minus.circle") {
                                model.homeLayout.set(section.id, shown: false)
                            }
                            .labelStyle(.iconOnly)
                            .buttonStyle(.borderless)
                            .help("Take this row off Home")
                        }
                    }
                    .onMove { from, to in
                        var ids = shown.map(\.id)
                        ids.move(fromOffsets: from, toOffset: to)
                        model.homeLayout.arrange(ids)
                    }
                }
                // As tall as its rows, up to two fifths of the sheet; past that it scrolls.
                .frame(height: min(CGFloat(shown.count) * 28 + 16, Self.height * 0.4))
            }
            Divider()
            List {
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
        .frame(width: 720, height: Self.height)
    }

    /// As tall as the screen has room for, up to 980 points (the owner, 2026-10-12: at
    /// 640, with twelve rows on the page, only about eight of the rows that could be
    /// added were in sight at a time).
    private static var height: CGFloat {
        min(980, max(640, (NSScreen.main?.visibleFrame.height ?? 800) - 140))
    }
}

/// The arrow keys on a page of cards (the owner, 2026-10-12: on a homepage the arrows
/// "switch through whats on the window"). A highlight goes from card to card: left and
/// right along a row and on to its next cards, up and down between rows, and Return
/// opens the card it's on, as a click does. Where the highlight goes is reckoned in the
/// Kit (`CardFocus`); this holds it, and what each row said it's showing.
@MainActor
@Observable
final class CardKeys {
    /// What the keys may ask of a row.
    struct Row {
        /// How many cards it's showing.
        let count: Int
        /// Show its next cards (true) or the ones before: how many it then shows, or
        /// nil when there are none that way.
        let page: (Bool) -> Int?
        /// Open the card at this place of the ones showing.
        let open: (Int) -> Void
    }

    /// The row the highlight is on, and which of its cards.
    private(set) var row: String?
    private(set) var column = 0
    /// The row to bring into sight, and a count that changes each time one is asked for.
    private(set) var showing: String?
    private(set) var shows = 0
    /// The page's rows from the top, by their ids.
    @ObservationIgnored var order: [String] = []
    @ObservationIgnored private var rows: [String: Row] = [:]
    /// Rows that have been on the screen: one of them with nothing told has no cards.
    @ObservationIgnored private var seen: Set<String> = []
    /// A row scrolled to before it had been seen: the highlight goes to it when its
    /// cards are there.
    @ObservationIgnored private var awaited: String?

    var hasHighlight: Bool { row != nil }

    func isOn(_ row: String, _ column: Int) -> Bool {
        self.row == row && min(self.column, max((rows[row]?.count ?? 1) - 1, 0)) == column
    }

    func saw(_ row: String) { seen.insert(row) }

    /// A row says what it's showing now.
    func tell(_ row: String, _ what: Row) {
        rows[row] = what
        if awaited == row, what.count > 0 {
            awaited = nil
            (self.row, column) = (row, 0)
        }
    }

    /// An arrow was pressed.
    func press(_ key: PlayerKey) {
        let states: [CardFocus.Row] = order.map { id in
            if let row = rows[id] { return row.count > 0 ? .cards(row.count) : .empty }
            return seen.contains(id) ? .empty : .unseen
        }
        let now = row.flatMap { order.firstIndex(of: $0) }.map { CardFocus(row: $0, column: column) }
        switch CardFocus.step(from: now, key: key, rows: states) {
        case .to(let focus):
            (row, column) = (order[focus.row], focus.column)
            show(order[focus.row])
        case .page(let index, let forward):
            if let count = rows[order[index]]?.page(forward) { column = forward ? 0 : max(count - 1, 0) }
        case .reveal(let index):
            awaited = order[index]
            show(order[index])
        case .stay:
            break
        }
    }

    /// Return: the card the highlight is on is opened. False when there's no highlight.
    func open() -> Bool {
        guard let row, let told = rows[row], told.count > 0 else { return false }
        told.open(min(column, told.count - 1))
        return true
    }

    /// No highlight: the page has been left, or Esc was pressed.
    func clear() {
        (row, column, awaited) = (nil, 0, nil)
    }

    private func show(_ row: String) {
        showing = row
        shows += 1
    }
}

private struct CardRowKey: EnvironmentKey {
    static let defaultValue = ""
}

extension EnvironmentValues {
    /// The row of Home a row of cards belongs to: its section's id.
    var cardRow: String {
        get { self[CardRowKey.self] }
        set { self[CardRowKey.self] = newValue }
    }
}
