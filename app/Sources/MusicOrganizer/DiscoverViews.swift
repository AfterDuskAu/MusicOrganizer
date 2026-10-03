import MusicOrganizerKit
import SwiftUI

/// Discover → What's New: songs the owner doesn't have, picked from their whole library.
/// It asks by itself the first time the page is opened.
struct WhatsNewView: View {
    @Environment(AppModel.self) private var model
    @AppStorage("whatsNewCount") private var count = 50

    var body: some View {
        let page = model.whatsNew
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("What's New").font(.title2.weight(.semibold))
                    Text("Songs you don't have yet, picked from the ones you do.")
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Picker("How many", selection: $count) {
                    ForEach([10, 50, 100], id: \.self) { Text("\($0) songs").tag($0) }
                }
                .labelsHidden()
                .fixedSize()
                .disabled(page.working)
                Button("Different Songs", systemImage: "arrow.triangle.2.circlepath") {
                    page.find([.library], count: count, different: true)
                }
                .disabled(page.working)
                .help("Start from other songs in your library")
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 12)
            Divider()
            PicksView(
                page: page,
                empty: "Nothing new was found. Try Different Songs.")
        }
        .task(id: model.phase) {
            // Asked once, when the page is first opened; after that only when told to.
            if !page.hasAsked, model.phase == .ready { page.find([.library], count: count) }
        }
        .onChange(of: count) { page.find([.library], count: count) }
    }
}

/// Discover → Find: choose where to start from and how many, and get that many picks.
struct FindView: View {
    @Environment(AppModel.self) private var model
    // The first starting point is remembered from one day to the next.
    @AppStorage("findStart") private var savedStart = FindChoice.Start.artist
    @AppStorage("findArtist") private var savedArtist = ""
    @AppStorage("findGenre") private var savedGenre = ""
    @AppStorage("findCount") private var count = 50
    /// Where to start from: one thing, or several together (a playlist and a genre).
    @State private var choices: [FindChoice] = []
    @State private var otherCount = ""
    /// The guided "What music would you like today?" is open.
    @State private var guiding = false

    static let genres = [
        "Hip hop", "R&B", "Pop", "Rock", "Indie", "Alternative", "Electronic", "Dance", "Metal",
        "Punk", "Reggae", "Folk", "Country", "Jazz", "Blues", "Classical", "Latin", "Soul",
    ]
    static let counts = [10, 50, 100, 500]

    var body: some View {
        let page = model.find
        VStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 10) {
                ForEach($choices) { $choice in
                    let first = choice.id == choices.first?.id
                    HStack(spacing: 10) {
                        Text(first ? "Start from" : "and from")
                            .foregroundStyle(.secondary)
                            .frame(width: 68, alignment: .leading)
                        Picker("Start from", selection: $choice.start) {
                            ForEach(FindChoice.Start.allCases) { Text($0.title).tag($0) }
                        }
                        .labelsHidden()
                        .fixedSize()
                        detail(for: $choice)
                        if !first {
                            Button {
                                choices.removeAll { $0.id == choice.id }
                            } label: {
                                Image(systemName: "minus.circle")
                            }
                            .buttonStyle(.plain)
                            .foregroundStyle(.secondary)
                            .help("Take this starting point away")
                        }
                        if choice.id == choices.last?.id, choices.count < FindChoice.most {
                            Button("Add Another", systemImage: "plus") {
                                choices.append(FindChoice(start: .genre, playlistId: firstPlaylist))
                            }
                            .help(
                                "Start from this as well: a playlist and a genre, say. Songs found "
                                    + "from both come first.")
                        }
                        Spacer()
                        if first {
                            Button("Guide Me…", systemImage: "bubble.left.and.bubble.right") {
                                guiding = true
                            }
                            .help(
                                "Three questions instead of these boxes: what music, play or "
                                    + "download, how many")
                        }
                    }
                }
                HStack(spacing: 10) {
                    Text("How many")
                        .foregroundStyle(.secondary)
                        .frame(width: 68, alignment: .leading)
                    Picker("How many", selection: $count) {
                        ForEach(Self.counts, id: \.self) { Text("\($0)").tag($0) }
                        if !Self.counts.contains(count) { Text("\(count)").tag(count) }
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                    .fixedSize()
                    TextField("Other", text: $otherCount)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 64)
                        .onSubmit(takeOtherCount)
                        .help("Any number from 1 to 500")
                    Spacer()
                    Button("Find", systemImage: "wand.and.stars", action: find)
                        .keyboardShortcut(.defaultAction)
                        .disabled(page.working || seeds.isEmpty)
                    Menu("Download Automatically", systemImage: "arrow.down.circle") {
                        ForEach(Guided.counts, id: \.self) { number in
                            Button("\(number) songs") { downloadAutomatically(number) }
                        }
                    }
                    .fixedSize()
                    .disabled(page.working || seeds.isEmpty)
                    .help(
                        "Finds this many songs from the starting points above and downloads "
                            + "every one, with nothing more to click. Leave the app open and "
                            + "they'll be in Discover → Downloads when you're back.")
                    if page.hasAsked {
                        Button("Different Songs", systemImage: "arrow.triangle.2.circlepath") {
                            page.again(different: true)
                        }
                        .disabled(page.working || onlyArtists)
                        .help("Start from other songs of yours")
                    }
                }
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 12)
            if let auto = model.auto {
                Divider()
                AutoDownloadNote(auto: auto) { model.auto = nil }
            }
            Divider()
            if page.hasAsked {
                PicksView(page: page, empty: "Nothing new was found for that.")
            } else {
                Message(
                    symbol: "wand.and.stars", title: "Find songs you don't have",
                    text: "Choose where to start from and how many songs you'd like, or let the "
                        + "guide ask you. Play any of them straight away; nothing is saved unless "
                        + "you click Download. Download Automatically finds and downloads a "
                        + "whole batch in one go, for while you're away."
                ) {
                    Button("Guide Me…") { guiding = true }
                }
            }
        }
        .sheet(isPresented: $guiding) { GuideSheet().environment(model) }
        .onAppear {
            if choices.isEmpty {
                choices = [
                    FindChoice(
                        start: savedStart, artist: savedArtist, genre: savedGenre,
                        playlistId: firstPlaylist)
                ]
            }
        }
    }

    private var firstPlaylist: String { model.listening.playlists.first?.id ?? "" }

    /// What a starting point needs said about it: a name to type, or a playlist to pick.
    @ViewBuilder
    private func detail(for choice: Binding<FindChoice>) -> some View {
        switch choice.wrappedValue.start {
        case .artist:
            TextField("Linkin Park (or several, with commas between)", text: choice.artist)
                .textFieldStyle(.roundedBorder)
                .frame(maxWidth: 340)
                .onSubmit(find)
        case .genre:
            TextField("Genre", text: choice.genre)
                .textFieldStyle(.roundedBorder)
                .frame(maxWidth: 200)
                .onSubmit(find)
            Menu("Choose") {
                ForEach(Self.genres, id: \.self) { name in
                    Button(name) { choice.wrappedValue.genre = name }
                }
            }
            .fixedSize()
        case .playlist:
            if model.listening.playlists.isEmpty {
                Text("You have no playlists yet.").foregroundStyle(.secondary)
            } else {
                Picker("Playlist", selection: choice.playlistId) {
                    ForEach(model.listening.playlists) { Text($0.name).tag($0.id) }
                }
                .labelsHidden()
                .fixedSize()
            }
        case .mostPlayed, .topArtist, .library:
            EmptyView()
        }
    }

    private var seeds: [DiscoverSeed] {
        FindChoice.seeds(of: choices, playlists: Set(model.listening.playlists.map(\.id)))
    }

    /// Nothing here starts from the owner's own songs, so there are no "other songs of
    /// yours" to start from instead.
    private var onlyArtists: Bool {
        choices.allSatisfy { $0.start == .artist || $0.start == .topArtist }
    }

    private func find() {
        takeOtherCount()
        if let first = choices.first {
            (savedStart, savedArtist, savedGenre) = (first.start, first.artist, first.genre)
        }
        model.find.find(seeds, count: count)
    }

    /// Find that many from the starting points in the boxes, and download them all.
    private func downloadAutomatically(_ number: Int) {
        if let first = choices.first {
            (savedStart, savedArtist, savedGenre) = (first.start, first.artist, first.genre)
        }
        model.downloadAutomatically(seeds, count: number)
    }

    private func takeOtherCount() {
        if let typed = Int(otherCount.trimmingCharacters(in: .whitespaces)) {
            count = min(max(typed, 1), 500)
        }
        otherCount = ""
    }
}

/// How Download Automatically is going: finding, on the way (how many, how long), or
/// what went wrong. It stays until it's closed.
private struct AutoDownloadNote: View {
    let auto: AppModel.AutoDownload
    let close: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            switch auto {
            case .finding:
                ProgressView().controlSize(.small)
            case .started:
                Image(systemName: "arrow.down.circle.fill").foregroundStyle(Color.accentColor)
            case .failed:
                Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
            }
            // The words take the room there is and wrap inside it.
            Text(words)
                .frame(maxWidth: .infinity, alignment: .leading)
            if auto.isOver {
                Button(action: close) {
                    Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
                .help("Close this note. The downloads carry on.")
            }
        }
        .font(.callout)
        .padding(.horizontal, 20)
        .padding(.vertical, 9)
        .background(.background.secondary)
    }
}

extension AutoDownloadNote {
    fileprivate var words: String {
        switch auto {
        case .finding(let count):
            "Finding \(count) \(count == 1 ? "song" : "songs") to download…"
        case .started(let note): note
        case .failed(let why): why
        }
    }
}

extension AppModel.AutoDownload {
    /// Found and queued, or failed: nothing more is coming.
    var isOver: Bool {
        if case .finding = self { false } else { true }
    }
}

/// A page's picks: a grid of cards, with how the asking is going and what went wrong.
struct PicksView: View {
    let page: DiscoverPage
    let empty: String
    @Environment(AppModel.self) private var model
    @State private var showingQueue = false

    var body: some View {
        if page.working {
            VStack(spacing: 12) {
                if page.of > 0 {
                    ProgressView(value: Double(page.done), total: Double(page.of))
                        .frame(width: 260)
                } else {
                    ProgressView()
                }
                Text("Asking YouTube Music for songs like yours…")
                Text("One radio at a time, so YouTube doesn't mind. It takes a few seconds a radio.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if let problem = page.problem {
            Message(symbol: "exclamationmark.triangle", title: "That didn't work", text: problem) {
                Button("Try Again") { page.again() }
            }
        } else if page.picks.isEmpty {
            Text(page.note ?? empty)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(40)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            VStack(spacing: 0) {
                actions
                Divider()
                ScrollView {
                    LazyVGrid(
                        columns: [GridItem(.adaptive(minimum: 168, maximum: 220), spacing: 18)],
                        alignment: .leading, spacing: 20
                    ) {
                        let picks = page.picks
                        ForEach(Array(picks.enumerated()), id: \.element.id) { index, pick in
                            PickCard(
                                pick: pick,
                                isSelected: page.selected.contains(pick.id),
                                toggle: { toggle(pick) },
                                play: {
                                    model.player.play(picks.map(\.result.track), startAt: index)
                                })
                        }
                    }
                    .padding(20)
                    showMore
                }
            }
        }
    }

    /// Under the cards: ask for 25 more, as often as the owner likes.
    @ViewBuilder
    private var showMore: some View {
        VStack(spacing: 8) {
            if page.loadingMore {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("Looking further…").foregroundStyle(.secondary)
                }
            } else {
                Button("Show \(DiscoverPage.moreStep) More", systemImage: "plus.circle") { page.more() }
                    .help("Look further from the same starting points for songs not shown yet")
            }
            if let why = page.problemWithMore {
                Text(why).font(.callout).foregroundStyle(.orange)
            } else if page.noMore {
                Text("Nothing more was found this time. Try again for other starting songs.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.bottom, 24)
    }

    /// The row above the cards: how many, and what can be done with several at once.
    private var actions: some View {
        let picks = page.picks
        let chosen = picks.filter { page.selected.contains($0.id) && model.canDownload($0) }
        let available = picks.filter(model.canDownload)
        return HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text("\(picks.count) \(picks.count == 1 ? "song" : "songs")")
                    .fontWeight(.medium)
                if let note = page.note {
                    Text(note).font(.callout).foregroundStyle(.secondary).lineLimit(2)
                }
            }
            Spacer()
            Button("Play All", systemImage: "play.fill") {
                model.player.play(picks.map(\.result.track), startAt: 0)
            }
            .help("Play these from YouTube Music. Nothing is saved.")
            Button("Queue (\(model.youtubeQueue.count))", systemImage: "text.append") {
                showingQueue = true
            }
            .disabled(model.youtubeQueue.isEmpty)
            .help("What's queued with Q, in the order it'll play")
            .popover(isPresented: $showingQueue, arrowEdge: .bottom) { QueueList() }
            if page.selected.isEmpty {
                Button("Select All") { page.selected = Set(available.map(\.id)) }
                    .disabled(available.isEmpty)
            } else {
                Button("Select None") { page.selected = [] }
            }
            Button(
                chosen.isEmpty ? "Download Selected" : "Download Selected (\(chosen.count))",
                systemImage: "arrow.down.circle"
            ) {
                model.planDownloads(chosen)
            }
            .disabled(chosen.isEmpty)
            .help("Tick the songs you want, then download them together")
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 8)
    }

    private func toggle(_ pick: DiscoverPick) {
        if page.selected.contains(pick.id) {
            page.selected.remove(pick.id)
        } else {
            page.selected.insert(pick.id)
        }
    }
}

/// One pick: its picture, what it is, why it's here, and what can be done with it.
private struct PickCard: View {
    let pick: DiscoverPick
    let isSelected: Bool
    let toggle: () -> Void
    let play: () -> Void
    @Environment(AppModel.self) private var model
    @State private var hovering = false

    var body: some View {
        let track = pick.result.track
        let playing = model.player.current?.id == track.id
        let owned = model.everything.videoIDs.contains(pick.videoId)
        VStack(alignment: .leading, spacing: 4) {
            Button(action: play) {
                CoverView(track: track, size: .medium, corner: 8)
                    .shadow(color: .black.opacity(0.18), radius: 5, y: 2)
                    .overlay {
                        // Play is the big, obvious thing to do with a pick.
                        Image(systemName: playing ? "speaker.wave.2.fill" : "play.circle.fill")
                            .font(.system(size: 44))
                            .foregroundStyle(.white)
                            .shadow(radius: 6)
                            .opacity(hovering || playing ? 1 : 0)
                    }
            }
            .buttonStyle(.plain)
            .help("Play from YouTube Music. Nothing is saved.")
            .overlay(alignment: .topLeading) {
                if !owned {
                    Button(action: toggle) {
                        Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                            .font(.title2)
                            .foregroundStyle(isSelected ? Color.accentColor : .white)
                            .shadow(radius: 3)
                    }
                    .buttonStyle(.plain)
                    .padding(6)
                    .opacity(hovering || isSelected ? 1 : 0)
                    .help(isSelected ? "Take it out of the selection" : "Select it, to download several together")
                }
            }
            HStack(spacing: 5) {
                Text(pick.title).fontWeight(playing ? .semibold : .medium).lineLimit(1)
                if model.heard.contains(pick.videoId) { HeardMark() }
                if pick.isExplicit == true {
                    Image(systemName: "e.square.fill").foregroundStyle(.secondary)
                }
            }
            .padding(.top, 4)
            Text(pick.artistName).foregroundStyle(.secondary).lineLimit(1)
            Text(pick.why)
                .font(.caption)
                .foregroundStyle(.tertiary)
                .lineLimit(2, reservesSpace: true)
                .help(pick.why)
            HStack(spacing: 6) {
                status(owned: owned)
                let queued = model.isQueued(pick.result)
                Button {
                    model.toggleQueued(pick.result)
                } label: {
                    Image(systemName: queued ? "q.circle.fill" : "q.circle")
                        .font(.title3)
                        .foregroundStyle(queued ? Color.accentColor : .secondary)
                }
                .buttonStyle(.plain)
                .help(
                    queued
                        ? "In the queue. Click to take it out."
                        : "Queue: play this after the song that's playing (YouTube Queue)")
                Spacer(minLength: 0)
                Text(clockTime(pick.durationS))
                    .font(.caption)
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
                Menu {
                    ForEach(ElsewhereLink.allCases) { link in
                        if let url = link.url(title: pick.title, artist: pick.artists.first ?? "") {
                            Link("Open in \(link.rawValue)", destination: url)
                        }
                    }
                } label: {
                    Image(systemName: "arrow.up.right.square")
                }
                .menuStyle(.borderlessButton)
                .menuIndicator(.hidden)
                .fixedSize()
                .help("Look this song up on Spotify, Apple Music or SoundCloud")
            }
        }
        .font(.callout)
        .onHover { hovering = $0 }
    }

    @ViewBuilder
    private func status(owned: Bool) -> some View {
        if owned {
            Label("In your library", systemImage: "checkmark.circle.fill")
                .foregroundStyle(.green)
                .font(.caption)
        } else {
            switch model.downloadState(of: pick.videoId) {
            case .working:
                HStack(spacing: 5) {
                    ProgressView().controlSize(.small)
                    Text(model.downloadNote(of: pick.videoId))
                        .font(.caption)
                        .monospacedDigit()
                        .foregroundStyle(.secondary)
                }
            case .failed(let why):
                Button("Try Again", systemImage: "exclamationmark.triangle") { model.download(pick) }
                    .controlSize(.small)
                    .help(why)
            case nil:
                Button("Download", systemImage: "arrow.down.circle") { model.download(pick) }
                    .controlSize(.small)
                    .help("Save this song in your library")
            }
        }
    }
}

/// The small red checkmark beside a song from YouTube that's been played all the way
/// through (What's New, Find, YouTube Music). Remembered for good, per profile.
struct HeardMark: View {
    var body: some View {
        Image(systemName: "checkmark")
            .font(.caption.weight(.heavy))
            .foregroundStyle(.red)
            .help("You've played this one all the way through")
            .accessibilityLabel("Played all the way through")
    }
}

/// The YouTube Queue, shown from What's New and Find: what's queued with Q, in the
/// order it'll play. A song can be taken out here (or with its Q again).
private struct QueueList: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("Queue").font(.headline)
                Spacer()
                Button("Play", systemImage: "play.fill") { model.playYouTubeQueue() }
                    .controlSize(.small)
            }
            .padding(12)
            Divider()
            List(model.youtubeQueue) { result in
                HStack(spacing: 8) {
                    CoverView(track: result.track, size: .small, corner: 3)
                        .frame(width: 28, height: 28)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(result.title).lineLimit(1)
                        Text(result.artists.joined(separator: ", "))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                    Spacer(minLength: 6)
                    Text(clockTime(result.durationS))
                        .font(.caption)
                        .monospacedDigit()
                        .foregroundStyle(.secondary)
                    Button {
                        model.removeFromYouTubeQueue(result)
                    } label: {
                        Image(systemName: "minus.circle").foregroundStyle(.secondary)
                    }
                    .buttonStyle(.plain)
                    .help("Take it out of the queue")
                }
            }
        }
        .frame(width: 380, height: 400)
    }
}
