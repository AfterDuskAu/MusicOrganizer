import MusicOrganizerKit
import SwiftUI
import UniformTypeIdentifiers

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
    /// Told when the picture can't be had: its address has gone, or there was none.
    var failed: (() -> Void)?

    var body: some View {
        if let url = address.flatMap(URL.init(string:)) {
            AsyncImage(url: url) { phase in
                switch phase {
                case .success(let image): image.resizable().scaledToFill()
                case .failure: placeholder.onAppear { failed?() }
                default: placeholder
                }
            }
        } else {
            placeholder.onAppear { failed?() }
        }
    }

    private var placeholder: some View {
        ZStack {
            Rectangle().fill(.quaternary.opacity(0.5))
            Image(systemName: symbol).font(.title).foregroundStyle(.tertiary)
        }
    }
}

/// One film or channel in a grid: its picture, and its name under it.
struct MediaCard: View {
    let name: String
    let poster: String?
    let square: Bool
    /// A channel's id: its picture is found afresh when the address given has gone
    /// (`ChannelPictures` in the Kit).
    let channelId: String?
    /// A film's or a series' id, for the green check on one that's been watched.
    let filmId: String?
    @Environment(AppModel.self) private var model: AppModel?
    @State private var hovering = false

    init(item: MediaItem) {
        (name, poster, square) = (item.name, item.poster, item.posterShape == "square")
        channelId = item.type == "channel" ? ChannelRef(item: item)?.channelId : nil
        filmId = item.type == "channel" ? nil : item.id
    }

    init(channel: ChannelRef) {
        (name, poster, square) = (channel.name, channel.thumbnail, true)
        channelId = channel.channelId
        filmId = nil
    }

    /// The picture found afresh for a channel, when there is one; the one given otherwise.
    private var picture: String? {
        channelId.flatMap { model?.channelPictures.address(for: $0) } ?? poster
    }

    var body: some View {
        VStack(spacing: 8) {
            Color.clear
                .aspectRatio(square ? 1 : 2.0 / 3.0, contentMode: .fit)
                .overlay {
                    WebPicture(
                        address: WebPictures.sized(picture, points: 200),
                        symbol: square ? "play.tv" : "film",
                        failed: channelId.map { id in { model?.wantPicture(of: id) } })
                }
                .clipShape(RoundedRectangle(cornerRadius: 10))
                .overlay(
                    RoundedRectangle(cornerRadius: 10)
                        .strokeBorder(hovering ? AnyShapeStyle(.primary) : AnyShapeStyle(.quaternary))
                )
                // Watched: a green check in the picture's top left corner (the owner,
                // 2026-10-13). A movie played to nine tenths, a series whose latest
                // episode has been (2026-10-15: `AppModel.isWatched`). Nothing from the
                // Finder kept apart is ever remembered as watched, so nothing there has one.
                .overlay(alignment: .topLeading) {
                    if let filmId, model?.isWatched(filmId) == true {
                        Image(systemName: "checkmark.circle.fill")
                            .font(.title2)
                            .symbolRenderingMode(.palette)
                            .foregroundStyle(.white, .green)
                            .shadow(color: .black.opacity(0.6), radius: 2)
                            .padding(6)
                            .help("You've watched this")
                            .accessibilityLabel("Watched")
                    }
                }
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
        // Video Finder's row clicked in the sidebar: back to the sections (2026-10-17).
        .onChange(of: model.clicks(on: .videoFinder).count) { opened = nil }
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
        .takesFirstClick()
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
                BackButton(action: back)
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
                // Its picture as it is today, for its card wherever that's shown.
                model.rememberPicture(page?.thumbnail, of: channel.channelId)
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
                    if !videos.isEmpty || problem != nil {
                        Button("Back to Explore", systemImage: "xmark") { typed = "" }
                            .help("Put the search away")
                    }
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 12)
                // The search box emptied: the search is put away, and Explore is back.
                .onChange(of: typed) { _, now in
                    if now.isEmpty { (videos, problem, asked) = ([], nil, "") }
                }
                Divider()
                if let problem {
                    Text(problem)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if videos.isEmpty, working {
                    Text("Searching…")
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if videos.isEmpty {
                    // Nothing searched for: the page is Explore (channels by section),
                    // which was a row of its own under Video Finder until 2026-10-08.
                    VideoExploreView()
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
        // A click on Video Finder's row in the sidebar: the search put away, and Explore
        // back, as the other Finders' lists are (2026-10-17).
        .onChange(of: model.clicks(on: .videoFinder).count) {
            typed = ""
            (videos, problem, asked) = ([], nil, "")
            opened = nil
        }
    }

    private func search() {
        let words = typed.trimmingCharacters(in: .whitespaces)
        guard !words.isEmpty, !working else { return }
        (working, problem, asked) = (true, nil, words)
        Task {
            var found: [VideoHit] = []
            var trouble: String?
            do {
                found = try await model.ask(
                    "video.search", ["query": words, "limit": 30], as: VideosAnswer.self
                ).videos
            } catch {
                trouble = error.localizedDescription
            }
            working = false
            // Put away while it was asked for (the box emptied, the row clicked in the
            // sidebar): its answer isn't wanted, and Explore stays (2026-10-17).
            guard asked == words else { return }
            videos = found
            problem = trouble ?? (found.isEmpty ? "Nothing found for “\(words)”." : nil)
        }
    }
}

/// Videos → Channel: the channels the owner follows. A click opens one, with its
/// newest videos.
struct ChannelsView: View {
    /// False while another page is showing (`PageHost`): the folder isn't looked at then.
    var isActive = true
    @Environment(AppModel.self) private var model
    @State private var opened: ChannelRef?
    /// The videos downloaded from channels and Video Finder: the Videos folder
    /// (`AppModel.videosFolder`).
    @State private var kept: [VideoFiles.File] = []
    @State private var looked = false
    /// Goes up when the app comes back to the front: the Finder may have changed the folder.
    @State private var backInFront = 0

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
        // Looked at again whenever a video has just arrived or been binned, each time
        // the page is shown, and when the app comes back to the front (as Videos → Movies
        // is, 2026-10-17: a video deleted in the Finder stayed listed).
        .task(id: "\(model.keptArrived) \(model.videosFolder?.path ?? "") \(isActive) \(backInFront)") {
            guard isActive else { return }
            let folder = model.videosFolder
            kept = await Task.detached { folder.map(VideoFiles.inside) ?? [] }.value
            looked = true
        }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            if isActive { backInFront += 1 }
        }
    }
}

/// One video file in a list (Videos → Movies, and the downloaded videos under Videos →
/// Channel): double-click plays it in the film player.
struct VideoFileRow: View {
    let file: VideoFiles.File
    /// False on Videos → 18+: nothing of a film played there is kept, not even where it
    /// was got to (2026-10-17).
    var remembering = true
    @Environment(AppModel.self) private var model

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "film").foregroundStyle(.secondary)
            Text(file.name).lineLimit(1)
            Spacer()
            if let note = model.conversionNotes[file.url.path] {
                Text(note).foregroundStyle(.secondary).lineLimit(1)
            }
            Text(file.kind).foregroundStyle(.secondary)
            Text(ByteCountFormatter.string(fromByteCount: file.bytes, countStyle: .file))
                .foregroundStyle(.secondary)
                .monospacedDigit()
                .frame(width: 90, alignment: .trailing)
        }
        .contentShape(Rectangle())
        .onTapGesture(count: 2) { model.playFilm(file.url, title: file.name, remembering: remembering) }
        .takesFirstClick()
        .contextMenu {
            Button("Play") { model.playFilm(file.url, title: file.name, remembering: remembering) }
            Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([file.url]) }
            Divider()
            Button("Convert for Phones and Tablets") { model.convertFilm(file.url) }
            Divider()
            // The file itself, to the Trash, once the owner says yes (2026-10-17).
            Button("Move to Trash…", role: .destructive) { model.trashingFile = file }
        }
    }
}

/// A Finder's search box. What's typed is the box's own until Return is pressed (or the
/// box is emptied), so a letter redraws the box and nothing else. With the words kept by
/// the page, every letter made the page work itself out again, its grid of cards with
/// it, and the letters lagged behind the typing (the owner, 2026-10-17). The search
/// itself was never sent before Return, and still isn't.
struct FinderSearchField: View {
    let prompt: String
    /// What's searched for: set on Return, and "" when the box is emptied.
    @Binding var search: String
    /// Goes up when the page puts its search away (its row clicked in the sidebar,
    /// another list chosen): the box is emptied.
    let clears: Int
    @State private var typed = ""

    var body: some View {
        TextField(prompt, text: $typed)
            .textFieldStyle(.roundedBorder)
            .onSubmit { search = typed.trimmingCharacters(in: .whitespaces) }
            .onChange(of: typed) { _, now in if now.isEmpty, !search.isEmpty { search = "" } }
            .onChange(of: clears) { typed = "" }
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
        let name =
            ["series": "seriesFinder", "anime": "animeFinder", "adult": "adultFinder"][kind]
            ?? "movieFinder"
        _listKey = AppStorage(wrappedValue: "", "\(name)List")
        _genre = AppStorage(wrappedValue: "", "\(name)Genre")
        _also = AppStorage(wrappedValue: "", "\(name)Also")
    }

    private var isSeries: Bool { kind == "series" }
    /// Anime: the lists add-ons give under that name (2026-10-08). What's in them are
    /// series and films like any other, opened the same way.
    private var isAnime: Bool { kind == "anime" }
    /// The add-ons for adults only (the owner, 2026-10-08): every list of theirs, here
    /// and nowhere else. Nothing opened here is remembered for Home.
    private var isAdult: Bool { kind == "adult" }
    /// A page whose rows can't be put on Home.
    private var apart: Bool { isAnime || isAdult }
    /// This Finder's row in the sidebar.
    private var page: SidebarItem {
        isSeries ? .seriesFinder : isAnime ? .animeFinder : isAdult ? .adultFinder : .movieFinder
    }
    /// What's searched for: set when Return is pressed in the box (`FinderSearchField`,
    /// which keeps what's typed to itself).
    @State private var search = ""
    /// Goes up to empty the search box.
    @State private var clears = 0
    @State private var opened: MediaItem?

    var body: some View {
        let media = model.media
        let lists = isAdult ? media.adultCatalogs() : media.catalogs(of: kind)
        let chosen = lists.first { Self.key($0) == listKey } ?? lists.first
        ZStack {
            VStack(spacing: 0) {
                HStack(spacing: 12) {
                    Text(
                        isSeries ? "Series Finder" : isAnime ? "Anime Finder"
                            : isAdult ? "Porn Finder" : "Movie Finder")
                        .font(.title2.weight(.semibold)).heading()
                        .lineLimit(1)
                        .fixedSize()
                    if let chosen {
                        Picker(
                            "List",
                            selection: Binding(
                                get: { ownList ?? Self.key(chosen) },
                                set: { pick($0, over: ownList ?? Self.key(chosen)) })
                        ) {
                            ForEach(lists, id: \.catalog) { Text(Self.name($0)).tag(Self.key($0)) }
                            // What's been watched, as a list of its own (the owner,
                            // 2026-10-13). Not where nothing is remembered as watched.
                            if hasWatchedList {
                                Divider()
                                Text("Watchlist").tag(Self.watchlistKey)
                                Text("Watched").tag(Self.watchedKey)
                            }
                        }
                        .labelsHidden()
                        .fixedSize()
                        if !chosen.catalog.genres.isEmpty, ownList == nil {
                            Picker(
                                "Genre",
                                selection: Binding(
                                    get: { chosen.catalog.genre(chosen: genre) ?? "" },
                                    set: { choose(genre: $0) })
                            ) {
                                if chosen.catalog.offersEveryGenre { Text("Every Genre").tag("") }
                                ForEach(chosen.catalog.genres, id: \.self) { Text($0).tag($0) }
                            }
                            .labelsHidden()
                            .fixedSize()
                            if let first = chosen.catalog.genre(chosen: genre), chosen.catalog.id != "year" {
                                Picker(
                                    "Second Genre",
                                    selection: Binding(
                                        get: { also },
                                        set: {
                                            also = $0
                                            putSearchAway()
                                        })
                                ) {
                                    Text("One Genre").tag("")
                                    ForEach(chosen.catalog.genres.filter { $0 != first }, id: \.self) {
                                        Text("and \($0)").tag($0)
                                    }
                                }
                                .labelsHidden()
                                .fixedSize()
                                .help("Only movies tagged with both genres")
                                // Two genres chosen: the search can be a row on Home.
                                if !apart, !also.isEmpty, also != first,
                                    chosen.catalog.genres.contains(also)
                                {
                                    let row = HomeSection.custom(isSeries ? .series : .movies, first, also)
                                    let there = model.homeLayout.shows(row.id)
                                    Button("Home", systemImage: there ? "checkmark" : "plus") {
                                        model.homeLayout.set(row.id, shown: !there)
                                    }
                                    .fixedSize()
                                    .help(
                                        there
                                            ? "“\(row.title)” is a row on Home. Click to take it off."
                                            : "Add this search to Home, as “\(row.title)”")
                                }
                            }
                        }
                        Spacer()
                        // A search looks through everything every plugin has of this
                        // kind, whichever list is showing (the owner, 2026-10-13); so
                        // the box is always there, and as wide as there's room for. The
                        // Finder kept apart searches the one list, as before.
                        if searchesEverything || chosen.catalog.takes("search") {
                            FinderSearchField(
                                prompt: searchesEverything ? "Search all \(Self.plural(kind))" : "Search this list",
                                search: $search, clears: clears
                            )
                            .frame(minWidth: 220, idealWidth: 340, maxWidth: 440)
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
                    let list =
                        isSeries ? media.series : isAnime ? media.anime
                        : isAdult ? media.adult : media.films
                    let everywhere = searchesEverything && !search.isEmpty
                    if let ownList, !everywhere {
                        grid(
                            of: ownList == Self.watchedKey
                                ? model.watchedItems(isSeries ? "series" : "movie")
                                : model.watchlist.of(type: isSeries ? "series" : "movie"),
                            empty: ownList == Self.watchedKey
                                ? (isSeries
                                    ? "Nothing watched yet. A series whose latest episode you've watched shows "
                                    : "Nothing watched yet. A movie you've watched nine tenths of shows ")
                                    + "up here, and has a green check on its picture in every list."
                                : "Nothing on your Watchlist yet. Open \(isSeries ? "a series" : "a movie") "
                                    + "and click Add to Watchlist.")
                    } else {
                        // What's opened from a list of anime is marked as anime, so that what's
                        // kept of it goes in the Movies folder's Anime folder.
                        MediaGrid(list: list, width: 140, open: { opened = isAnime ? $0.asAnime : $0 }) {
                            Task {
                                await list.load(
                                    model, addon: chosen.addon, catalog: chosen.catalog, genre: wanted,
                                    search: search, also: second, adding: true)
                            }
                        }
                        .task(id: "\(Self.key(chosen))|\(wanted ?? "")|\(search)|\(second)|\(everywhere)") {
                            if everywhere {
                                await list.search(model, type: chosen.catalog.type, words: search)
                            } else {
                                await list.load(
                                    model, addon: chosen.addon, catalog: chosen.catalog, genre: wanted,
                                    search: search, also: second)
                            }
                        }
                    }
                } else if apart, media.loaded {
                    Message(
                        symbol: isAdult ? "eye.slash" : "sparkles.tv",
                        title: isAdult ? "No plugin for adults yet" : "No list of anime yet",
                        text: isAdult
                            ? "The app doesn't come with one. Add one by its address in Settings → "
                                + "Plugins: if it doesn't say itself that it's for adults only, "
                                + "mark it there. Its lists show up here and nowhere else: not on "
                                + "Home, not in the other Finders, and never in a child's profile."
                            : "None of your plugins lists anime. Add one that does in Settings → "
                                + "Plugins, and its lists show up here."
                    ) {
                        OpenSettings { Text("Open Settings…") }
                            .simultaneousGesture(
                                TapGesture().onEnded {
                                    UserDefaults.standard.set("addons", forKey: SettingsView.tabKey)
                                })
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
                // A page of its own for each film: opened over another (from Home, or the
                // Watchlist) it kept that one's trailer, episode and ways to play until
                // its own details came, and Play could play the other one (2026-10-15).
                MovieView(film: opened, apart: isAdult) { self.opened = nil }
                    .id(opened.id)
            }
        }
        .onChange(of: opened) { _, film in
            // Let go of the search box, so its ring isn't drawn and typing goes nowhere.
            if film != nil { NSApp.keyWindow?.makeFirstResponder(nil) }
        }
        // A click on this Finder's row in the sidebar, whether it was showing or not:
        // its lists again, at the genres chosen before, with no search and no film open
        // over them (the owner, 2026-10-17: "clicking Series Finder after a search should
        // move it back to series finder").
        .onChange(of: model.clicks(on: page).count) {
            clearSearch()
            opened = nil
        }
        // A film or series clicked on Home opens here.
        .onChange(of: model.filmToOpen, initial: true) { _, wanted in
            if let wanted, !apart, (wanted.type == "series") == isSeries {
                opened = wanted
                model.filmToOpen = nil
            }
        }
        .task(id: model.phase) { await media.load(model) }
    }

    /// A list chosen in the list menu, `shown` being the one showing: the search is put
    /// away, and the genres too unless it's the same list again (`GenreChoice`).
    private func pick(_ key: String, over shown: String) {
        var choice = GenreChoice(genre: genre, also: also)
        choice.choose(list: key, over: shown)
        listKey = key
        (genre, also) = (choice.genre, choice.also)
        clearSearch()
    }

    /// A genre chosen in the first menu: the second is empty again (`GenreChoice`).
    private func choose(genre wanted: String) {
        var choice = GenreChoice(genre: genre, also: also)
        choice.choose(genre: wanted)
        (genre, also) = (choice.genre, choice.also)
        putSearchAway()
    }

    /// A genre chosen while a search of everything is showing is a wish to see that
    /// genre's list, so the search is put away (2026-10-17). In the Finder kept apart a
    /// search is of the one list, at its genre, and stays. A search never changes the
    /// genres chosen: they're there again when it's put away.
    private func putSearchAway() {
        if searchesEverything, !search.isEmpty { clearSearch() }
    }

    private func clearSearch() {
        search = ""
        clears += 1
    }

    /// The list menu's "Watched" and "Watchlist", by the names they're saved under.
    private static let watchedKey = "watched"
    private static let watchlistKey = "watchlist"
    /// Movies and series have a Watched list and a Watchlist. (What's opened from a list
    /// of anime is remembered as a series or a film, and is in theirs.)
    private var hasWatchedList: Bool { !apart }
    /// The list of the owner's own that's chosen in the menu, if one is.
    private var ownList: String? {
        hasWatchedList && [Self.watchedKey, Self.watchlistKey].contains(listKey) ? listKey : nil
    }
    /// Every plugin is searched at once, except in the Finder kept apart.
    private var searchesEverything: Bool { !isAdult }

    private static func plural(_ kind: String) -> String {
        switch kind {
        case "series": "series"
        case "anime": "anime"
        default: "movies"
        }
    }

    /// A list of the owner's own (what's been watched; the Watchlist), as cards.
    @ViewBuilder
    private func grid(of items: [MediaItem], empty: String) -> some View {
        if items.isEmpty {
            Text(empty)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(40)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            ScrollView {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 140), spacing: 22)], spacing: 22) {
                    ForEach(items) { item in
                        Button { opened = item } label: { MediaCard(item: item) }
                            .buttonStyle(.plain)
                    }
                }
                .padding(20)
            }
        }
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
    /// Opened from the Finder for adults: it has no heart, and playing it isn't
    /// remembered, so nothing of it reaches Home.
    var apart = false
    let back: () -> Void
    @Environment(AppModel.self) private var model
    @State private var details: MediaDetails?
    @State private var streams: StreamsAnswer?
    /// Every way to play is shown, not only the three of each quality (Show All).
    @State private var showsAllWays = false
    @State private var problem: String?
    /// A series: the season showing, and the episode whose ways to play are showing.
    @State private var season: Int?
    @State private var episode: MediaDetails.Video?

    private var isSeries: Bool { film.type == "series" }
    /// What's played or kept: the film, or the episode chosen ("East of Eden S01E02").
    private var playName: String { episode?.name(in: film.name) ?? film.name }
    /// What Home remembers of it once it's played: the film, or the series with the
    /// episode it has got to.
    private var watched: WatchHistory.Entry { .watching(film, episode: episode) }

    var body: some View {
        // The page is the size of the room it's given. The film's picture is laid over a
        // plain black behind it and cut to fit, so a big picture never makes the page (and
        // with it every other page) bigger than the window.
        ZStack(alignment: .topLeading) {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    BackButton(help: "Back to the list", action: back)
                    HStack(alignment: .firstTextBaseline, spacing: 16) {
                        Text(film.name).font(.system(size: 40, weight: .bold)).heading()
                        let loved = model.mediaFavourites.contains(film)
                        if !apart { Button(
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
                    }
                    // The Watchlist (the owner, 2026-10-14): on every movie's and
                    // series' page. Not in the Finder kept apart, where nothing is
                    // remembered.
                    if !apart {
                        let listed = model.watchlist.contains(film)
                        Button {
                            model.toggleWatchlist(film)
                        } label: {
                            Label(
                                listed ? "On Your Watchlist" : "Add to Watchlist",
                                systemImage: listed ? "checkmark" : "plus"
                            )
                                .font(.body.weight(.semibold))
                                .padding(.horizontal, 14)
                                .frame(height: 32)
                                .background(.white.opacity(listed ? 0.28 : 0.14), in: Capsule())
                                .overlay { Capsule().strokeBorder(.white.opacity(0.4)) }
                                .contentShape(Capsule())
                        }
                        .buttonStyle(.plain)
                        .help(
                            listed
                                ? "It's on your Watchlist (in the sidebar, under Videos). Click to take it off."
                                : "Put it on your Watchlist, to find it again and watch it later")
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
        .onChange(of: model.episodeToChoose) { showAskedEpisode() }
        .task(id: film.id) {
            // `adult`: which side of the fence answers. A title from the Finder for
            // adults is asked of those add-ons only, and any other title never of them.
            let asked: [String: Any] = ["type": film.type, "id": film.id, "adult": apart]
            do {
                details = try await model.ask("addon.details", asked, as: MediaDetails.self)
            } catch {
                problem = error.localizedDescription
            }
            if isSeries {
                // A series is played an episode at a time: the first season shows, and
                // an episode's ways to play are asked for when it's clicked.
                season = details?.seasons.first
                showAskedEpisode()
                // Its latest episode, for whether it's been watched.
                if !apart, let details { model.learn(film, episodes: details.videos) }
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
                // Only the episodes on screen are made. A series that has gone on for
                // years lists a thousand or more in one season, and made all at once
                // they held the app for three seconds as the page opened, and a second
                // at every episode clicked (2026-10-10). The gap between them is the
                // part's own.
                LazyVStack(alignment: .leading, spacing: 10) {
                    ForEach(details.episodes(in: showing)) { one in
                        let seen = !apart && model.finished.contains(one.id)
                        Button { choose(one) } label: {
                            HStack(alignment: .top, spacing: 14) {
                                // A check before each episode that's been watched (the
                                // owner, 2026-10-15), to nine tenths of it or marked so.
                                Image(systemName: "checkmark.circle.fill")
                                    .symbolRenderingMode(.palette)
                                    .foregroundStyle(.white, .green)
                                    .opacity(seen ? 1 : 0)
                                    .frame(width: 18)
                                    .help(seen ? "You've watched this episode" : "")
                                    .accessibilityLabel(seen ? "Watched" : "")
                                    .padding(.trailing, -6)
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
                        .contextMenu {
                            if !apart {
                                Button(seen ? "Mark as Not Watched" : "Mark as Watched") {
                                    model.markWatched([one.id], !seen, of: film)
                                }
                                let earlier = Self.before(one, in: details.videos)
                                if !earlier.isEmpty {
                                    Button("Mark Every Episode Before This as Watched") {
                                        model.markWatched(earlier.map(\.id), true, of: film)
                                    }
                                }
                            }
                        }
                    }
                }
            }
            .frame(maxWidth: 760, alignment: .leading)
        }
    }

    /// The episodes before this one, by season and number (specials aren't before anything).
    private static func before(_ one: MediaDetails.Video, in videos: [MediaDetails.Video]) -> [MediaDetails.Video] {
        guard let season = one.season, season > 0, let number = one.episode else { return [] }
        return videos.filter { video in
            guard let itsSeason = video.season, itsSeason > 0, let itsNumber = video.episode else { return false }
            return (itsSeason, itsNumber) < (season, number)
        }
    }

    /// The next episode couldn't be played by itself the way the last one was: its ways
    /// to play are shown here, for the owner to choose (`AppModel.playEpisode(after:)`).
    private func showAskedEpisode() {
        guard let asked = model.episodeToChoose, asked.series == film.id,
            let one = details?.videos.first(where: { $0.id == asked.episode })
        else { return }
        model.episodeToChoose = nil
        season = one.season ?? season
        choose(one)
    }

    /// Show where an episode can be played from.
    private func choose(_ one: MediaDetails.Video) {
        guard episode != one else { return }
        (episode, streams, showsAllWays) = (one, nil, false)
        Task {
            let found = try? await model.ask(
                "addon.streams", ["type": film.type, "id": one.id, "adult": apart],
                as: StreamsAnswer.self)
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
                            ? "None of your plugins has this episode to play. The ones the app "
                                + "starts with have details of series, but only movies to play."
                            : "None of your plugins has this film to play.")
                        .foregroundStyle(.secondary)
                }
                // One bar a way to play, as the owner drew it (2026-10-11): where it's from,
                // its quality, its size, who has it, then Play and Keep. Only a torrent has
                // seeders and leechers, so the heading is there only when one is listed.
                // At most three of a quality, by the owner's order of add-ons, until Show
                // All is pressed (the owner, 2026-10-11: the same film in 1080p from
                // twenty-five add-ons shouldn't be twenty-five lines).
                let all = streams.ways
                let few = streams.shortlist()
                let ways = showsAllWays ? all : few
                if ways.contains(where: { $0.stream.kind == "torrent" }) {
                    Text("Seeders/Leechers")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .padding(.leading, WayToPlayColumns.swarmStart)
                        .padding(.bottom, -6)
                }
                ForEach(ways) { way in
                    let stream = way.stream
                    Group {
                        HStack(spacing: WayToPlayColumns.gap) {
                            Text(way.addon)
                                .lineLimit(1)
                                .frame(width: WayToPlayColumns.source, alignment: .leading)
                            Text(stream.qualityLabel).fontWeight(.semibold)
                                .frame(width: WayToPlayColumns.quality, alignment: .leading)
                            Text(stream.sizeLabel ?? "–")
                                .monospacedDigit()
                                .frame(width: WayToPlayColumns.size, alignment: .leading)
                            swarm(of: stream)
                                .frame(width: WayToPlayColumns.swarm, alignment: .leading)
                            Spacer(minLength: 0)
                            Button("Play", systemImage: "play.fill") {
                                // An episode says which it is and how it was chosen, so
                                // the next one can follow it (Settings → Player).
                                let now = episode.map {
                                    AppModel.EpisodeNow(
                                        series: film, episodeId: $0.id,
                                        played: NextEpisode.Played(addonId: way.addonId, quality: stream.quality),
                                        apart: apart, episodes: details?.videos)
                                }
                                model.play(
                                    stream, title: playName, watching: apart ? nil : watched, episode: now,
                                    apart: apart)
                            }
                            .disabled(!stream.canPlay)
                            .help(stream.canPlay ? "Play it now" : "This one can't be played in the app yet")
                            // A torrent, or (2026-10-17, the owner: "add a way to download
                            // from the porn finder. it only offers play") a plain web
                            // address: the same button, followed the same way.
                            if stream.canDownload, let id = stream.keepId {
                                let keeping = model.filmKeeps[id]
                                if keeping?.isKeeping == true {
                                    Button("Stop Downloading", systemImage: "xmark.circle") {
                                        model.stopKeepingFilm(id)
                                    }
                                    .takesFirstClick(false)
                                    .help(
                                        "Stop fetching it. Nothing has been put in your Movies folder yet"
                                            + (stream.kind == "url"
                                                ? "; what has arrived is kept for two days, so Download carries on" : ""))
                                } else {
                                    Button("Download", systemImage: "arrow.down.circle") {
                                        model.keepFilm(
                                            stream, of: film, episode: episode,
                                            year: details?.year ?? film.year, apart: apart)
                                    }
                                    .disabled(keeping != nil)
                                    .help(
                                        "Fetch the whole film and save it in your Movies folder"
                                            + (apart ? "'s 18+ folder" : "")
                                            + (stream.sizeLabel.map { " (\($0))" } ?? ""))
                                }
                            }
                        }
                        .padding(.vertical, 6)
                        // What the add-on itself says of it, and how it would be played.
                        .help(
                            ([stream.name, stream.title].compactMap { $0 } + [stream.kindLabel])
                                .joined(separator: " · "))
                        if let line = stream.keepId.flatMap({ model.filmKeeps[$0]?.keepLine }) {
                            Text(line).font(.callout).foregroundStyle(.secondary)
                        }
                    }
                }
                if all.count > few.count {
                    Button(showsAllWays ? "Show Fewer" : "Show All \(all.count)") {
                        showsAllWays.toggle()
                    }
                    .buttonStyle(.link)
                    .help(
                        showsAllWays
                            ? "Back to three of each quality, from the plugins highest in your list"
                            : "Three of each quality are shown, from the plugins highest in your "
                                + "list (Settings → Plugins). This shows every one")
                }
                ForEach(streams.problems, id: \.self) { problem in
                    Text("\(problem.addon): \(problem.message)").foregroundStyle(.secondary)
                }
                if streams.sources.contains(where: { $0.streams.contains { $0.kind == "torrent" } }) {
                    Text(
                        "A torrent shares the parts it has fetched with others while it's "
                            + "arriving, and after that as much as Settings → Plugins → Downloads allows. "
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

    /// Who has a way to play: for a torrent, its seeders and leechers ("25/5") once
    /// they've been counted, else a Count button (the app joins a torrent only from a
    /// click, so nobody is counted for a film that was only looked at). Anything that
    /// isn't a torrent has none: a dash.
    @ViewBuilder
    private func swarm(of stream: MediaStream) -> some View {
        if stream.kind != "torrent" {
            Text("–").foregroundStyle(.secondary)
        } else if let said = model.swarm(of: stream) {
            Text(said).monospacedDigit()
                .help(
                    "Seeders have the whole film; leechers are the others sharing it. The "
                        + "film's trackers' own count where they give one; otherwise those "
                        + "this Mac has met so far, which grows while the film plays.")
        } else if stream.infoHash.map({ model.swarmCounting.contains($0.lowercased()) }) == true {
            ProgressView().controlSize(.small)
        } else {
            Button("Count") { model.countSwarm(stream) }
                .controlSize(.small)
                .help(
                    "Count who has this torrent. The app joins it for a few seconds and "
                        + "fetches nothing of the film; others sharing it can see this Mac meanwhile.")
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
///
/// With `adult`, Videos → 18+ (2026-10-17): the same page for the Movies folder's 18+
/// folder alone, what's downloaded from the Finder for adults, which Videos → Movies
/// leaves out; with those downloads still under way at its top (they're not listed in
/// Downloads), and nothing remembered of what's played there.
struct MoviesView: View {
    /// False while another page is showing (`PageHost`): the folder isn't looked at then.
    var isActive = true
    var adult = false
    @Environment(AppModel.self) private var model
    @State private var files: [VideoFiles.File] = []
    @State private var looked = false
    /// Goes up when the app comes back to the front: the Finder may have changed the folder.
    @State private var backInFront = 0

    var body: some View {
        let count = files.count
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(adult ? "18+" : "Movies").font(.title2.weight(.semibold)).heading()
                        Text(count == 1 ? "1 file" : "\(count.formatted()) files")
                            .foregroundStyle(.secondary)
                    }
                    Text(
                        adult
                            ? "What you've downloaded from the Porn Finder, in the 18+ folder in your Movies "
                                + "folder. Double-click one to play it: nothing of it is remembered."
                            : "The video files in your Movies folder. Double-click one to play it.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if !adult {
                    Button("Open File…", systemImage: "folder") { openFile() }
                        .help("Play a video file from anywhere on this Mac")
                }
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 12)
            Divider()
            if adult { FilmKeeps(adult: true) }
            if count == 0 {
                Text(
                    !looked ? "Looking…"
                        : adult ? "Nothing has been downloaded from the Porn Finder yet."
                        : "There are no video files in your Movies folder yet."
                )
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List(files) { VideoFileRow(file: $0, remembering: !adult) }
                    .scrollContentBackground(Theme.current.listBackground)
            }
        }
        // Looked at again whenever a film or a video has just been kept or binned, each
        // time the page is shown, and when the app comes back to the front (the owner,
        // 2026-10-17: "I went into Finder to delete a movie but it still shows in movies").
        .task(id: "\(model.keptArrived) \(model.moviesFolder?.path ?? "") \(isActive) \(backInFront)") {
            if isActive { await look() }
        }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            if isActive { backInFront += 1 }
        }
        .task {
            // For checking the player without a click: MUSICORG_FILM=<file> plays it once,
            // silently; MUSICORG_FILM=torrent:<info-hash>:<file number> plays that torrent.
            if !adult, let what = ProcessInfo.processInfo.environment["MUSICORG_FILM"], !model.film.isOpen {
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
        // Videos has a page of its own (Videos → Channel), and so does the 18+ folder
        // (Videos → 18+), so what's in them isn't listed here.
        let movies = model.moviesFolder
        let videos = model.videosFolder
        let adult = adult
        files = await Task.detached {
            guard let movies else { return [] }
            let theirs = VideoFiles.adultFolder(in: movies)
            if adult { return VideoFiles.inside(theirs) }
            return VideoFiles.inside(movies, leavingOut: [videos, theirs].compactMap { $0 })
        }.value
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

/// Videos → Watchlist (the owner, 2026-10-14): To Watch, the movies and series put on the
/// Watchlist from their pages that haven't been watched yet, with a series caught up with
/// that has a new episode; and Watched (both by the owner's rule of 2026-10-15). A click
/// opens one's page, in its Finder; a right-click takes it off the list, or marks a movie.
struct WatchlistView: View {
    @Environment(AppModel.self) private var model
    @AppStorage("watchlistShows") private var shows = "towatch"

    var body: some View {
        let watched = shows == "watched"
        // To Watch: what's on the Watchlist and not watched yet, and a series caught up
        // with that has a new episode. Watched: a movie played to nine tenths, a series
        // whose latest episode has been (the owner, 2026-10-15).
        let movies = watched ? model.watchedItems("movie") : model.toWatch("movie")
        let allSeries = watched ? model.watchedItems("series") : model.toWatch("series")
        let (anime, series) = (allSeries.filter { $0.anime == true }, allSeries.filter { $0.anime != true })
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Text("Watchlist").font(.title2.weight(.semibold)).heading()
                Picker("Show", selection: $shows) {
                    Text("To Watch").tag("towatch")
                    Text("Watched").tag("watched")
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .fixedSize()
                Spacer()
                if model.readingSeries {
                    ProgressView().controlSize(.small)
                        .help("Looking for new episodes of your series")
                }
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 12)
            Divider()
            if movies.isEmpty, allSeries.isEmpty {
                Text(
                    watched
                        ? "Nothing watched yet. A movie you've watched nine tenths of, or a series whose "
                            + "latest episode you've watched, shows up here, and has a green check on its "
                            + "picture in every list."
                        : "Nothing to watch on your Watchlist. Open a movie or a series (Movie Finder, "
                            + "Series Finder) and click Add to Watchlist on its page. A series you've "
                            + "caught up with comes back here when a new episode is out.")
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(40)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    // Lazy: a grid further down isn't made until it's scrolled to.
                    LazyVStack(alignment: .leading, spacing: 8) {
                        cards("Movies", movies)
                        cards("Series", series)
                        cards("Anime", anime)
                    }
                    .padding(.vertical, 14)
                }
            }
        }
        // Whether a series has a new episode, or its latest has been watched, goes by its
        // episodes: read again when they're six hours old.
        .task { await model.readSeries(model.ownSeries) }
    }

    @ViewBuilder
    private func cards(_ title: String, _ items: [MediaItem]) -> some View {
        if !items.isEmpty {
            Text(title).font(.title3.weight(.semibold)).heading().padding(.horizontal, 20)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 140), spacing: 22)], spacing: 22) {
                ForEach(items) { item in
                    Button { model.open(item) } label: {
                        MediaCard(item: item)
                            .overlay(alignment: .top) {
                                if model.hasNewEpisode(item.id) {
                                    Text("NEW EPISODE")
                                        .font(.caption2.weight(.bold))
                                        .foregroundStyle(.white)
                                        .padding(.horizontal, 7)
                                        .padding(.vertical, 3)
                                        .background(Color.accentColor, in: Capsule())
                                        .padding(.top, 8)
                                        .help("A new episode is out since you caught up")
                                }
                            }
                    }
                    .buttonStyle(.plain)
                    .contextMenu {
                        Button("Open Its Page") { model.open(item) }
                        if model.watchlist.contains(item) {
                            Button("Remove From Watchlist") { model.toggleWatchlist(item) }
                        } else {
                            Button("Add to Watchlist") { model.toggleWatchlist(item) }
                        }
                        // A movie by the owner's word; a series' episodes are marked on its page.
                        if item.type != "series" {
                            let seen = model.isWatched(item.id)
                            Button(seen ? "Mark as Not Watched" : "Mark as Watched") {
                                model.markWatched([item.id], !seen)
                            }
                        }
                    }
                }
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 14)
        }
    }
}
