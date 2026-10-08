import MusicOrganizerKit
import SwiftUI
import UniformTypeIdentifiers

/// Music Finder → Explore: What's New and Find together on one page (the owner's
/// drawing, 2026-10-07). Each is the page it was; the switch at the top chooses.
struct MusicExploreView: View {
    @AppStorage("musicExploreFind") private var showFind = false

    var body: some View {
        VStack(spacing: 0) {
            Picker("Explore", selection: $showFind) {
                Text("What's New").tag(false)
                Text("Find").tag(true)
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .fixedSize()
            .padding(.top, 10)
            if showFind {
                FindView()
            } else {
                WhatsNewView()
            }
        }
    }
}

/// A page whose row is in the sidebar as the owner drew it, but which isn't built yet.
struct ComingPage: View {
    let title: String
    let text: String

    var body: some View {
        VStack(spacing: 8) {
            Text(title).font(.title2.weight(.semibold)).heading()
            Text(text)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 460)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

/// A picture from the web, cut to fill its frame, with a plain tile until it arrives.
struct WebPicture: View {
    let address: String?
    var symbol = "play.rectangle"

    var body: some View {
        AsyncImage(url: address.flatMap(URL.init(string:))) { phase in
            if let image = phase.image {
                image.resizable().scaledToFill()
            } else {
                ZStack {
                    Rectangle().fill(.quaternary.opacity(0.5))
                    Image(systemName: symbol).font(.title).foregroundStyle(.tertiary)
                }
            }
        }
    }
}

/// One film or channel in a grid: its picture, and its name under it.
struct MediaCard: View {
    let name: String
    let poster: String?
    let square: Bool
    @State private var hovering = false

    init(item: MediaItem) {
        (name, poster, square) = (item.name, item.poster, item.posterShape == "square")
    }

    init(channel: ChannelRef) {
        (name, poster, square) = (channel.name, channel.thumbnail, true)
    }

    var body: some View {
        VStack(spacing: 8) {
            Color.clear
                .aspectRatio(square ? 1 : 2.0 / 3.0, contentMode: .fit)
                .overlay {
                    WebPicture(
                        address: WebPictures.sized(poster, points: 200),
                        symbol: square ? "play.tv" : "film")
                }
                .clipShape(RoundedRectangle(cornerRadius: 10))
                .overlay(
                    RoundedRectangle(cornerRadius: 10)
                        .strokeBorder(hovering ? AnyShapeStyle(.primary) : AnyShapeStyle(.quaternary))
                )
            Text(name)
                .font(.callout.weight(.medium))
                .lineLimit(2)
                .multilineTextAlignment(.center)
                .frame(maxWidth: .infinity, minHeight: 34, alignment: .top)
        }
        .contentShape(Rectangle())
        .onHover { hovering = $0 }
    }
}

/// A grid of films or channels, with the list's own messages and Show More.
struct MediaGrid: View {
    let list: MediaList
    let width: CGFloat
    let open: (MediaItem) -> Void
    let showMore: () -> Void

    var body: some View {
        if let problem = list.problem, list.items.isEmpty {
            Text(problem)
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if list.items.isEmpty {
            ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            ScrollView {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: width), spacing: 22)], spacing: 22) {
                    ForEach(list.items) { item in
                        Button { open(item) } label: { MediaCard(item: item) }
                            .buttonStyle(.plain)
                    }
                }
                .padding(20)
                if list.working {
                    ProgressView().padding(.bottom, 20)
                } else if list.more {
                    Button("Show More", action: showMore).padding(.bottom, 20)
                } else if let problem = list.problem {
                    Text(problem).foregroundStyle(.secondary).padding(.bottom, 20)
                }
            }
        }
    }
}

/// Video Finder → Explore: channels to watch, by section (Channels, Gaming, News,
/// Sports, and the rest the list offers). A click opens a channel and its videos.
struct VideoExploreView: View {
    @Environment(AppModel.self) private var model
    @AppStorage("videoExploreSection") private var sectionName = "Channels"
    @State private var opened: ChannelRef?

    var body: some View {
        let media = model.media
        let source = media.catalogs(of: "channel").first
        let sections = VideoExplore.sections(for: source?.catalog.genres ?? [])
        let section = sections.first { $0.name == sectionName } ?? sections[0]
        VStack(spacing: 0) {
            if let opened {
                ChannelView(channel: opened) { self.opened = nil }
            } else {
                HStack(spacing: 12) {
                    Text("Explore").font(.title2.weight(.semibold)).heading()
                    Picker("Section", selection: $sectionName) {
                        ForEach(sections) { Text($0.name).tag($0.name) }
                    }
                    .labelsHidden()
                    .fixedSize()
                    Spacer()
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 12)
                Divider()
                if let words = section.search {
                    // A section the list has no genre for: channels found by a search.
                    ChannelSearchGrid(words: words) { opened = $0 }
                } else if let source {
                    MediaGrid(list: media.channels, width: 150, open: { opened = ChannelRef(item: $0) }) {
                        Task {
                            await media.channels.load(
                                model, addon: source.addon, catalog: source.catalog,
                                genre: section.genre, adding: true)
                        }
                    }
                    .task(id: section) {
                        await media.channels.load(
                            model, addon: source.addon, catalog: source.catalog, genre: section.genre)
                    }
                } else {
                    Text(media.problem ?? "Loading…")
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
        }
        .task(id: model.phase) { await media.load(model) }
    }
}

/// Channels found by a search, in a grid: Explore's Learning and Podcasts. Asked once
/// for each set of words while the app is open.
struct ChannelSearchGrid: View {
    let words: String
    let open: (ChannelRef) -> Void
    @Environment(AppModel.self) private var model
    @State private var problem: String?

    var body: some View {
        let channels = model.channelSearches[words]
        Group {
            if let channels, !channels.isEmpty {
                ScrollView {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 22)], spacing: 22) {
                        ForEach(channels) { channel in
                            Button { open(channel) } label: { MediaCard(channel: channel) }
                                .buttonStyle(.plain)
                        }
                    }
                    .padding(20)
                }
            } else if let problem = problem ?? (channels == nil ? nil : "Nothing was found.") {
                Text(problem).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .task(id: words) {
            guard model.channelSearches[words] == nil else { return }
            problem = nil
            do {
                model.channelSearches[words] = try await model.ask(
                    "channel.search", ["query": words, "limit": 40], as: ChannelsAnswer.self
                ).channels
            } catch {
                problem = error.localizedDescription
            }
        }
    }
}

/// One video in a list: its picture, its name, whose it is, and how long, how watched
/// and how new it is. Double-click plays it; a click on the channel's name opens the channel.
struct VideoRow: View {
    let video: VideoHit
    var openChannel: ((ChannelRef) -> Void)?
    let play: () -> Void
    @Environment(AppModel.self) private var model

    var body: some View {
        HStack(spacing: 14) {
            Color.clear
                .frame(width: 128, height: 72)
                .overlay { WebPicture(address: video.thumbnail) }
                .clipShape(RoundedRectangle(cornerRadius: 6))
                .overlay(alignment: .bottomTrailing) {
                    if !video.length.isEmpty {
                        Text(video.length)
                            .font(.caption.weight(.medium))
                            .monospacedDigit()
                            .foregroundStyle(.white)
                            .padding(.horizontal, 5)
                            .padding(.vertical, 2)
                            .background(.black.opacity(0.75), in: RoundedRectangle(cornerRadius: 4))
                            .padding(4)
                    }
                }
            VStack(alignment: .leading, spacing: 4) {
                Text(video.title).font(.body.weight(.medium)).lineLimit(2)
                HStack(spacing: 8) {
                    if let name = video.channel {
                        if let openChannel, let id = video.channelId {
                            Button(name) { openChannel(ChannelRef(channelId: id, name: name)) }
                                .buttonStyle(.link)
                                .help("Open this channel")
                        } else {
                            Text(name)
                        }
                    }
                    Text([video.viewsLabel, video.age()].filter { !$0.isEmpty }.joined(separator: " · "))
                }
                .font(.callout)
                .foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
        .onTapGesture(count: 2, perform: play)
        .contextMenu {
            Button("Play", action: play)
            Button("Download") { model.keepVideo(video.result) }
                .disabled(model.downloadState(of: video.videoId) == .working)
        }
    }
}

/// One channel: who they are, and the videos they've put up most lately, newest first,
/// read afresh each time. Follow keeps the channel under Videos → Channel.
struct ChannelView: View {
    let channel: ChannelRef
    let back: () -> Void
    @Environment(AppModel.self) private var model
    @State private var page: ChannelPage?
    @State private var problem: String?

    var body: some View {
        let followed = model.followedChannels.contains { $0.channelId == channel.channelId }
        VStack(spacing: 0) {
            HStack(spacing: 14) {
                Button("Back", systemImage: "chevron.left", action: back)
                    .labelStyle(.iconOnly)
                    .help("Back")
                WebPicture(
                    address: WebPictures.sized(page?.thumbnail ?? channel.thumbnail, points: 56),
                    symbol: "play.tv"
                )
                    .frame(width: 56, height: 56)
                    .clipShape(Circle())
                VStack(alignment: .leading, spacing: 2) {
                    Text(page?.name ?? channel.name).font(.title2.weight(.semibold)).heading()
                    if let followers = page?.followers {
                        Text("\(VideoHit.round(followers)) followers").foregroundStyle(.secondary)
                    }
                }
                Spacer()
                Button(followed ? "Following" : "Follow", systemImage: followed ? "checkmark" : "plus") {
                    model.follow(
                        ChannelRef(
                            channelId: channel.channelId, name: page?.name ?? channel.name,
                            thumbnail: page?.thumbnail ?? channel.thumbnail),
                        on: !followed)
                }
                .help(
                    followed
                        ? "Stop following: it leaves Videos → Channel"
                        : "Keep this channel under Videos → Channel")
                if let videos = page?.videos, !videos.isEmpty {
                    Button("Play", systemImage: "play.fill") { play(videos, from: videos[0]) }
                        .mainButton()
                        .help("Play this channel's videos, newest first")
                }
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 12)
            Divider()
            if let page {
                if page.videos.isEmpty {
                    Text("This channel has no videos to list.")
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    List(page.videos) { video in
                        VideoRow(video: video) { play(page.videos, from: video) }
                    }
                    .scrollContentBackground(Theme.current.listBackground)
                }
            } else {
                Group {
                    if let problem {
                        Text(problem).foregroundStyle(.secondary)
                    } else {
                        ProgressView()
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .task(id: channel.channelId) {
            (page, problem) = (nil, nil)
            do {
                page = try await model.ask(
                    "channel.videos", ["channel_id": channel.channelId], as: ChannelPage.self)
            } catch {
                problem = error.localizedDescription
            }
        }
    }

    /// Play the channel's videos from one of them, carrying on down the list.
    private func play(_ videos: [VideoHit], from first: VideoHit) {
        guard let start = videos.firstIndex(of: first) else { return }
        model.playVideos(videos.map(\.result), startAt: start, channels: VideoHit.channels(of: videos))
    }
}

/// Video Finder: a search of every kind of video. Double-click plays one; a click on a
/// channel's name opens the channel.
struct VideoFinderView: View {
    @Environment(AppModel.self) private var model
    @State private var typed = ""
    @State private var asked = ""
    @State private var videos: [VideoHit] = []
    @State private var working = false
    @State private var problem: String?
    @State private var opened: ChannelRef?
    @State private var sort = VideoSort.bestMatch

    var body: some View {
        let shown = sort.arranged(videos)
        ZStack {
            VStack(spacing: 0) {
                HStack(spacing: 12) {
                    Text("Video Finder").font(.title2.weight(.semibold)).heading()
                    TextField("Search for a video", text: $typed)
                        .textFieldStyle(.roundedBorder)
                        .onSubmit { search() }
                    Picker("Sort", selection: $sort) {
                        ForEach(VideoSort.allCases, id: \.self) { Text($0.title).tag($0) }
                    }
                    .labelsHidden()
                    .fixedSize()
                    .disabled(videos.isEmpty)
                    .help("How the videos found are arranged")
                    if working {
                        ProgressView().controlSize(.small)
                    } else {
                        Button("Search") { search() }
                            .disabled(typed.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 12)
                Divider()
                if let problem {
                    Text(problem)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if videos.isEmpty {
                    Text(
                        working
                            ? "Searching…"
                            : "Search, then double-click a video to play it. Nothing is saved.")
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    List(shown) { video in
                        VideoRow(video: video, openChannel: { opened = $0 }) {
                            if let start = shown.firstIndex(of: video) {
                                model.playVideos(
                                    shown.map(\.result), startAt: start,
                                    channels: VideoHit.channels(of: shown))
                            }
                        }
                    }
                    .scrollContentBackground(Theme.current.listBackground)
                }
            }
            // Out of sight and out of reach while a channel is open over it.
            .opacity(opened == nil ? 1 : 0)
            .allowsHitTesting(opened == nil)
            if let opened {
                ChannelView(channel: opened) { self.opened = nil }
                    .dressed()
            }
        }
        .onChange(of: opened) { _, channel in
            if channel != nil { NSApp.keyWindow?.makeFirstResponder(nil) }
        }
    }

    private func search() {
        let words = typed.trimmingCharacters(in: .whitespaces)
        guard !words.isEmpty, !working else { return }
        (working, problem, asked) = (true, nil, words)
        Task {
            do {
                let found = try await model.ask(
                    "video.search", ["query": words, "limit": 30], as: VideosAnswer.self)
                videos = found.videos
                if videos.isEmpty { problem = "Nothing found for “\(words)”." }
            } catch {
                problem = error.localizedDescription
            }
            working = false
        }
    }
}

/// Videos → Channel: the channels the owner follows. A click opens one, with its
/// newest videos.
struct ChannelsView: View {
    @Environment(AppModel.self) private var model
    @State private var opened: ChannelRef?
    /// The videos downloaded from channels and Video Finder: Media, in the Downloads folder.
    @State private var kept: [VideoFiles.File] = []
    @State private var looked = false

    static var keptFolder: URL? {
        FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first?
            .appendingPathComponent("Media", isDirectory: true)
    }

    var body: some View {
        let channels = model.followedChannels
        VStack(spacing: 0) {
            if let opened {
                ChannelView(channel: opened) { self.opened = nil }
            } else {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text("Channels").font(.title2.weight(.semibold)).heading()
                    Text(channels.count == 1 ? "1 channel" : "\(channels.count) channels")
                        .foregroundStyle(.secondary)
                    Spacer()
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 12)
                Divider()
                // The channels followed: one row, which slides sideways when there are
                // more than fit (the owner, 2026-10-08).
                if channels.isEmpty {
                    Text(
                        "Channels you follow show up here. Find one under Video Finder, or its "
                            + "Explore, open it, and click Follow.")
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 28)
                        .padding(.horizontal, 20)
                } else {
                    ScrollView(.horizontal) {
                        LazyHStack(alignment: .top, spacing: 18) {
                            ForEach(channels) { channel in
                                Button { opened = channel } label: {
                                    MediaCard(channel: channel).frame(width: 120)
                                }
                                .buttonStyle(.plain)
                                .contextMenu {
                                    Button("Stop Following") { model.follow(channel, on: false) }
                                }
                            }
                        }
                        .padding(.horizontal, 20)
                        .padding(.vertical, 14)
                    }
                    .frame(height: 196)
                }
                Divider()
                // Under them: only the videos that have been downloaded.
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text("Downloaded Videos").font(.headline)
                    Text(kept.count == 1 ? "1 video" : "\(kept.count) videos")
                        .foregroundStyle(.secondary)
                    Spacer()
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 10)
                if kept.isEmpty {
                    Text(
                        looked
                            ? "Videos you download show up here. Right-click a video and choose Download."
                            : "Looking…")
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    List(kept) { VideoFileRow(file: $0) }
                        .scrollContentBackground(Theme.current.listBackground)
                }
            }
        }
        // A channel clicked on Home opens here.
        .onChange(of: model.channelToOpen, initial: true) { _, wanted in
            if let wanted {
                opened = wanted
                model.channelToOpen = nil
            }
        }
        // Looked at again whenever a video has just arrived.
        .task(id: model.keptArrived) {
            let folder = Self.keptFolder
            kept = await Task.detached { folder.map(VideoFiles.inside) ?? [] }.value
            looked = true
        }
    }
}

/// One video file in a list (Videos → Movies, and the downloaded videos under Videos →
/// Channel): double-click plays it in the film player.
struct VideoFileRow: View {
    let file: VideoFiles.File
    @Environment(AppModel.self) private var model

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "film").foregroundStyle(.secondary)
            Text(file.name).lineLimit(1)
            Spacer()
            Text(file.kind).foregroundStyle(.secondary)
            Text(ByteCountFormatter.string(fromByteCount: file.bytes, countStyle: .file))
                .foregroundStyle(.secondary)
                .monospacedDigit()
                .frame(width: 90, alignment: .trailing)
        }
        .contentShape(Rectangle())
        .onTapGesture(count: 2) { model.playFilm(file.url, title: file.name) }
        .contextMenu {
            Button("Play") { model.playFilm(file.url, title: file.name) }
            Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([file.url]) }
        }
    }
}

/// Movie Finder: films to browse, list by list, with a search where the list has one.
/// A click opens a film: what it is, and where it can be played from.
struct MovieFinderView: View {
    @Environment(AppModel.self) private var model
    /// Movies or series: "movie" or "series", as add-ons call them. Each has a Finder
    /// of its own in the sidebar, and its own list, genre and second genre.
    let kind: String
    @AppStorage private var listKey: String
    @AppStorage private var genre: String
    /// A second genre: only films tagged with both are listed.
    @AppStorage private var also: String

    init(kind: String) {
        self.kind = kind
        let name = kind == "series" ? "seriesFinder" : "movieFinder"
        _listKey = AppStorage(wrappedValue: "", "\(name)List")
        _genre = AppStorage(wrappedValue: "", "\(name)Genre")
        _also = AppStorage(wrappedValue: "", "\(name)Also")
    }

    private var isSeries: Bool { kind == "series" }
    @State private var typed = ""
    @State private var search = ""
    @State private var opened: MediaItem?

    var body: some View {
        let media = model.media
        let lists = media.catalogs(of: kind)
        let chosen = lists.first { Self.key($0) == listKey } ?? lists.first
        ZStack {
            VStack(spacing: 0) {
                HStack(spacing: 12) {
                    Text(isSeries ? "Series Finder" : "Movie Finder")
                        .font(.title2.weight(.semibold)).heading()
                    if let chosen {
                        Picker("List", selection: Binding(get: { Self.key(chosen) }, set: { pick($0) })) {
                            ForEach(lists, id: \.catalog) { Text(Self.name($0)).tag(Self.key($0)) }
                        }
                        .labelsHidden()
                        .fixedSize()
                        if !chosen.catalog.genres.isEmpty {
                            Picker(
                                "Genre",
                                selection: Binding(
                                    get: { chosen.catalog.genre(chosen: genre) ?? "" },
                                    set: { genre = $0 })
                            ) {
                                if chosen.catalog.offersEveryGenre { Text("Every Genre").tag("") }
                                ForEach(chosen.catalog.genres, id: \.self) { Text($0).tag($0) }
                            }
                            .labelsHidden()
                            .fixedSize()
                            if let first = chosen.catalog.genre(chosen: genre), chosen.catalog.id != "year" {
                                Picker("Second Genre", selection: $also) {
                                    Text("One Genre").tag("")
                                    ForEach(chosen.catalog.genres.filter { $0 != first }, id: \.self) {
                                        Text("and \($0)").tag($0)
                                    }
                                }
                                .labelsHidden()
                                .fixedSize()
                                .help("Only movies tagged with both genres")
                            }
                        }
                        Spacer()
                        if chosen.catalog.takes("search") {
                            TextField("Search this list", text: $typed)
                                .textFieldStyle(.roundedBorder)
                                .frame(width: 220)
                                .onSubmit { search = typed.trimmingCharacters(in: .whitespaces) }
                                .onChange(of: typed) { _, now in if now.isEmpty { search = "" } }
                        }
                    } else {
                        Spacer()
                    }
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 12)
                Divider()
                if let chosen {
                    let wanted = chosen.catalog.genre(chosen: genre)
                    // A second genre counts only with a first, and not the same one, on a
                    // list by genre (a list by year's "genres" are its years).
                    let second =
                        wanted != nil && also != wanted && chosen.catalog.id != "year"
                            && chosen.catalog.genres.contains(also) ? also : ""
                    let list = isSeries ? media.series : media.films
                    MediaGrid(list: list, width: 140, open: { opened = $0 }) {
                        Task {
                            await list.load(
                                model, addon: chosen.addon, catalog: chosen.catalog, genre: wanted,
                                search: search, also: second, adding: true)
                        }
                    }
                    .task(id: "\(Self.key(chosen))|\(wanted ?? "")|\(search)|\(second)") {
                        await list.load(
                            model, addon: chosen.addon, catalog: chosen.catalog, genre: wanted,
                            search: search, also: second)
                    }
                } else {
                    Text(media.problem ?? "Loading…")
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            // The list stays as it was left (scrolled to the same place) while a film is
            // open over it, but out of sight and out of reach: its search box showed
            // through the film's page.
            .opacity(opened == nil ? 1 : 0)
            .allowsHitTesting(opened == nil)
            if let opened {
                MovieView(film: opened) { self.opened = nil }
            }
        }
        .onChange(of: opened) { _, film in
            // Let go of the search box, so its ring isn't drawn and typing goes nowhere.
            if film != nil { NSApp.keyWindow?.makeFirstResponder(nil) }
        }
        // A film or series clicked on Home opens here.
        .onChange(of: model.filmToOpen, initial: true) { _, wanted in
            if let wanted, (wanted.type == "series") == isSeries {
                opened = wanted
                model.filmToOpen = nil
            }
        }
        .task(id: model.phase) { await media.load(model) }
    }

    private func pick(_ key: String) {
        listKey = key
        (typed, search, genre) = ("", "", "")
    }

    private static func key(_ list: (addon: Addon, catalog: Addon.Catalog)) -> String {
        "\(list.addon.id)|\(list.catalog.id)"
    }

    /// A list's name: its own, or else what it's called in its add-on ("top").
    private static func name(_ list: (addon: Addon, catalog: Addon.Catalog)) -> String {
        if let name = list.catalog.name, !name.isEmpty { return name }
        switch list.catalog.id {
        case "top": return "Popular"
        case "year": return "New"
        case "imdbRating": return "Best Rated"
        default: return list.addon.name
        }
    }
}

/// One film, over its own picture: length, year, rating, genres, cast, directors and
/// summary, and under them every place it can be played from, add-on by add-on.
struct MovieView: View {
    let film: MediaItem
    let back: () -> Void
    @Environment(AppModel.self) private var model
    @State private var details: MediaDetails?
    @State private var streams: StreamsAnswer?
    @State private var problem: String?
    /// A series: the season showing, and the episode whose ways to play are showing.
    @State private var season: Int?
    @State private var episode: MediaDetails.Video?

    private var isSeries: Bool { film.type == "series" }
    /// What's played or kept: the film, or the episode chosen ("East of Eden S01E02").
    private var playName: String { episode?.name(in: film.name) ?? film.name }
    /// What Home remembers of it once it's played: the film, or the series with the
    /// episode it has got to.
    private var watched: WatchHistory.Entry {
        let about = episode.map { [$0.number, $0.title].compactMap { $0 }.joined(separator: " · ") }
        return WatchHistory.Entry(
            id: film.id, kind: isSeries ? .series : .movie, name: film.name, detail: about,
            picture: film.poster, item: film)
    }

    var body: some View {
        // The page is the size of the room it's given. The film's picture is laid over a
        // plain black behind it and cut to fit, so a big picture never makes the page (and
        // with it every other page) bigger than the window.
        ZStack(alignment: .topLeading) {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    Button("Back", systemImage: "chevron.left", action: back)
                        .labelStyle(.iconOnly)
                        .buttonStyle(.plain)
                        .font(.title2)
                        .help("Back to the films")
                    HStack(alignment: .firstTextBaseline, spacing: 16) {
                        Text(film.name).font(.system(size: 40, weight: .bold)).heading()
                        let loved = model.mediaFavourites.contains(film)
                        Button(
                            loved ? "Remove From Favourites" : "Add to Favourites",
                            systemImage: loved ? "heart.fill" : "heart"
                        ) {
                            model.toggleFavourite(film)
                        }
                        .labelStyle(.iconOnly)
                        .buttonStyle(.plain)
                        .font(.title)
                        .help(loved ? "Take it out of your favourites" : "A favourite: it shows on Home")
                    }
                    HStack(spacing: 28) {
                        if let minutes = details?.runtimeMin { Text("\(minutes) min") }
                        if let year = details?.year ?? film.year { Text(year) }
                        if let rating = details?.rating ?? film.rating {
                            Label(String(format: "%.1f", rating), systemImage: "star.fill")
                        }
                    }
                    .font(.title3.weight(.semibold))
                    chips("Genres", details?.genres ?? film.genres)
                    chips("Cast", details?.cast ?? [])
                    chips("Directors", details?.directors ?? [])
                    if let summary = details?.description {
                        part("Summary") { Text(summary).frame(maxWidth: 760, alignment: .leading) }
                    }
                    if let trailer = details?.trailerVideoId {
                        Button("Trailer", systemImage: "movieclapper") {
                            model.playVideos(
                                [SearchResult(videoId: trailer, title: "\(film.name) (Trailer)", artists: [])],
                                startAt: 0, remember: false)
                        }
                        .help("Play the trailer. Switch the player page to Video to see it.")
                    }
                    if let problem { Text(problem).foregroundStyle(.secondary) }
                    if isSeries { episodes }
                    sources
                }
                .padding(32)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background {
            Color.black
                .overlay {
                    WebPicture(address: details?.background ?? film.poster, symbol: "film")
                        .opacity(0.35)
                }
                .clipped()
        }
        .clipped()
        .foregroundStyle(.white)
        .environment(\.colorScheme, .dark)
        .task(id: film.id) {
            let asked: [String: Any] = ["type": film.type, "id": film.id]
            do {
                details = try await model.ask("addon.details", asked, as: MediaDetails.self)
            } catch {
                problem = error.localizedDescription
            }
            if isSeries {
                // A series is played an episode at a time: the first season shows, and
                // an episode's ways to play are asked for when it's clicked.
                season = details?.seasons.first
            } else {
                streams = try? await model.ask("addon.streams", asked, as: StreamsAnswer.self)
            }
        }
    }

    /// A series' seasons, and the episodes of the one showing.
    @ViewBuilder
    private var episodes: some View {
        if let details, !details.seasons.isEmpty {
            let showing = season ?? details.seasons[0]
            part("Episodes") {
                Picker("Season", selection: Binding(get: { showing }, set: { season = $0 })) {
                    ForEach(details.seasons, id: \.self) { Text(MediaDetails.seasonName($0)).tag($0) }
                }
                .labelsHidden()
                .fixedSize()
                ForEach(details.episodes(in: showing)) { one in
                    Button { choose(one) } label: {
                        HStack(alignment: .top, spacing: 14) {
                            Text(one.episode.map(String.init) ?? "")
                                .monospacedDigit()
                                .frame(width: 28, alignment: .trailing)
                                .foregroundStyle(.secondary)
                            VStack(alignment: .leading, spacing: 3) {
                                Text(one.title.isEmpty ? "Episode \(one.episode ?? 0)" : one.title)
                                    .fontWeight(.medium)
                                if episode == one, let overview = one.overview {
                                    Text(overview)
                                        .font(.callout)
                                        .foregroundStyle(.secondary)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                            }
                            Spacer()
                            Text(one.day).foregroundStyle(.secondary).monospacedDigit()
                        }
                        .padding(.vertical, 6)
                        .padding(.horizontal, 10)
                        .background(
                            episode == one ? AnyShapeStyle(.white.opacity(0.14)) : AnyShapeStyle(.clear),
                            in: RoundedRectangle(cornerRadius: 8))
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
            .frame(maxWidth: 760, alignment: .leading)
        }
    }

    /// Show where an episode can be played from.
    private func choose(_ one: MediaDetails.Video) {
        guard episode != one else { return }
        (episode, streams) = (one, nil)
        Task {
            let found = try? await model.ask(
                "addon.streams", ["type": film.type, "id": one.id], as: StreamsAnswer.self)
            if episode == one { streams = found ?? StreamsAnswer(sources: [], problems: []) }
        }
    }

    @ViewBuilder
    private var sources: some View {
        if let streams {
            part(episode.flatMap(\.number).map { "Where to Play \($0)" } ?? "Where to Play It") {
                if streams.sources.isEmpty {
                    Text(
                        isSeries
                            ? "None of your add-ons has this episode to play. The ones the app "
                                + "starts with have details of series, but only movies to play."
                            : "None of your add-ons has this film to play.")
                        .foregroundStyle(.secondary)
                }
                ForEach(streams.sources) { source in
                    ForEach(Array(source.streams.enumerated()), id: \.offset) { _, stream in
                        HStack(spacing: 16) {
                            Text(source.addon).frame(width: 190, alignment: .leading)
                            Text(stream.qualityLabel).fontWeight(.semibold).frame(width: 80, alignment: .leading)
                            Text([stream.name, stream.title].compactMap { $0 }.joined(separator: " · "))
                                .lineLimit(1)
                            Spacer()
                            Text(stream.kindLabel).foregroundStyle(.secondary)
                            Button("Play", systemImage: "play.fill") {
                                model.play(stream, title: playName, watching: watched)
                            }
                            .disabled(!stream.canPlay)
                            .help(stream.canPlay ? "Play it now" : "This one can't be played in the app yet")
                            if stream.kind == "torrent" {
                                Button("Keep", systemImage: "arrow.down.circle") {
                                    model.keepFilm(
                                        stream, title: playName,
                                        year: isSeries ? nil : details?.year ?? film.year)
                                }
                                .disabled(stream.infoHash.flatMap { model.filmKeeps[$0] } != nil)
                                .help("Fetch the whole film and save it in your Movies folder")
                            }
                        }
                        .padding(.vertical, 6)
                        if let line = stream.infoHash.flatMap({ model.filmKeeps[$0]?.keepLine }) {
                            Text(line).font(.callout).foregroundStyle(.secondary)
                        }
                    }
                }
                ForEach(streams.problems, id: \.self) { problem in
                    Text("\(problem.addon): \(problem.message)").foregroundStyle(.secondary)
                }
                if streams.sources.contains(where: { $0.streams.contains { $0.kind == "torrent" } }) {
                    Text(
                        "A torrent shares the parts it has fetched with others while it plays. "
                            + "What's fetched is kept for a day after you last played it, then deleted."
                    )
                    .font(.callout)
                    .foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: 760, alignment: .leading)
        }
    }

    @ViewBuilder
    private func chips(_ title: String, _ names: [String]) -> some View {
        if !names.isEmpty {
            part(title) {
                HStack(spacing: 10) {
                    ForEach(names.prefix(6), id: \.self) { name in
                        Text(name)
                            .padding(.horizontal, 14)
                            .padding(.vertical, 6)
                            .background(.white.opacity(0.12), in: Capsule())
                    }
                }
            }
        }
    }

    private func part(_ title: String, @ViewBuilder content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title.uppercased()).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            content()
        }
    }
}

/// Videos → Movies: the video files in this Mac's Movies folder, and any other file the
/// owner opens. Double-click plays one in the film player. Nothing here is changed.
struct MoviesView: View {
    @Environment(AppModel.self) private var model
    @State private var files: [VideoFiles.File] = []
    @State private var looked = false

    private static var folder: URL? {
        FileManager.default.urls(for: .moviesDirectory, in: .userDomainMask).first
    }

    var body: some View {
        let count = files.count
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text("Movies").font(.title2.weight(.semibold)).heading()
                        Text(count == 1 ? "1 file" : "\(count.formatted()) files")
                            .foregroundStyle(.secondary)
                    }
                    Text("The video files in your Movies folder. Double-click one to play it.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("Open File…", systemImage: "folder") { openFile() }
                    .help("Play a video file from anywhere on this Mac")
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 12)
            Divider()
            if count == 0 {
                Text(looked ? "There are no video files in your Movies folder yet." : "Looking…")
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List(files) { VideoFileRow(file: $0) }
                    .scrollContentBackground(Theme.current.listBackground)
            }
        }
        // Looked at again whenever a film or a video has just been kept.
        .task(id: model.keptArrived) { await look() }
        .task {
            // For checking the player without a click: MUSICORG_FILM=<file> plays it once,
            // silently; MUSICORG_FILM=torrent:<info-hash>:<file number> plays that torrent.
            if let what = ProcessInfo.processInfo.environment["MUSICORG_FILM"], !model.film.isOpen {
                model.film.volume = 0
                let parts = what.split(separator: ":").map(String.init)
                if parts.count == 3, parts[0] == "torrent" {
                    let said = #"{"kind":"torrent","info_hash":"\#(parts[1])","file_index":\#(parts[2]),"trackers":[]}"#
                    let decoder = JSONDecoder()
                    decoder.keyDecodingStrategy = .convertFromSnakeCase
                    if let stream = try? decoder.decode(MediaStream.self, from: Data(said.utf8)) {
                        model.play(stream, title: "Check")
                    }
                } else {
                    let url = URL(fileURLWithPath: what)
                    model.playFilm(url, title: url.deletingPathExtension().lastPathComponent)
                }
            }
        }
    }

    /// Off the main thread: a big folder takes a moment to walk.
    private func look() async {
        let movies = Self.folder
        files = await Task.detached { movies.map(VideoFiles.inside) ?? [] }.value
        looked = true
    }

    private func openFile() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.allowedContentTypes = [.movie, .video, .audiovisualContent]
        panel.allowsOtherFileTypes = true
        if panel.runModal() == .OK, let url = panel.url {
            model.playFilm(url, title: url.deletingPathExtension().lastPathComponent)
        }
    }
}
