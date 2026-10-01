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
    @AppStorage("findStart") private var start = Start.artist
    @AppStorage("findCount") private var count = 50
    @AppStorage("findArtist") private var artist = ""
    @AppStorage("findGenre") private var genre = ""
    @State private var playlistId = ""
    @State private var otherCount = ""
    /// The guided "What music would you like today?" is open.
    @State private var guiding = false

    enum Start: String, CaseIterable, Identifiable {
        case artist, genre, playlist, mostPlayed, topArtist, library

        var id: String { rawValue }
        var title: String {
            switch self {
            case .artist: "An artist, and bands like them"
            case .genre: "A genre"
            case .playlist: "One of my playlists"
            case .mostPlayed: "The songs I play most"
            case .topArtist: "The artist I play most"
            case .library: "My whole library"
            }
        }
    }

    static let genres = [
        "Hip hop", "R&B", "Pop", "Rock", "Indie", "Alternative", "Electronic", "Dance", "Metal",
        "Punk", "Reggae", "Folk", "Country", "Jazz", "Blues", "Classical", "Latin", "Soul",
    ]
    static let counts = [10, 50, 100, 500]

    var body: some View {
        let page = model.find
        VStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 10) {
                    Text("Start from").foregroundStyle(.secondary)
                    Picker("Start from", selection: $start) {
                        ForEach(Start.allCases) { Text($0.title).tag($0) }
                    }
                    .labelsHidden()
                    .fixedSize()
                    detail
                    Spacer()
                    Button("Guide Me…", systemImage: "bubble.left.and.bubble.right") {
                        guiding = true
                    }
                    .help("Three questions instead of these boxes: what music, play or download, how many")
                }
                HStack(spacing: 10) {
                    Text("How many").foregroundStyle(.secondary)
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
                    if page.hasAsked {
                        Button("Different Songs", systemImage: "arrow.triangle.2.circlepath") {
                            page.again(different: true)
                        }
                        .disabled(page.working || start == .artist || start == .topArtist)
                        .help("Start from other songs of yours")
                    }
                }
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 12)
            Divider()
            if page.hasAsked {
                PicksView(page: page, empty: "Nothing new was found for that.")
            } else {
                Message(
                    symbol: "wand.and.stars", title: "Find songs you don't have",
                    text: "Choose where to start from and how many songs you'd like, or let the "
                        + "guide ask you. Play any of them straight away; nothing is saved unless "
                        + "you click Download."
                ) {
                    Button("Guide Me…") { guiding = true }
                }
            }
        }
        .sheet(isPresented: $guiding) { GuideSheet().environment(model) }
        .onAppear {
            if playlistId.isEmpty { playlistId = model.listening.playlists.first?.id ?? "" }
        }
    }

    @ViewBuilder
    private var detail: some View {
        switch start {
        case .artist:
            TextField("Linkin Park (or several, with commas between)", text: $artist)
                .textFieldStyle(.roundedBorder)
                .frame(maxWidth: 340)
                .onSubmit(find)
        case .genre:
            TextField("Genre", text: $genre)
                .textFieldStyle(.roundedBorder)
                .frame(maxWidth: 200)
                .onSubmit(find)
            Menu("Choose") {
                ForEach(Self.genres, id: \.self) { name in
                    Button(name) { genre = name }
                }
            }
            .fixedSize()
        case .playlist:
            if model.listening.playlists.isEmpty {
                Text("You have no playlists yet.").foregroundStyle(.secondary)
            } else {
                Picker("Playlist", selection: $playlistId) {
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
        switch start {
        case .artist: DiscoverSeed.artists(artist)
        case .genre:
            genre.trimmingCharacters(in: .whitespaces).isEmpty
                ? [] : [.genre(genre.trimmingCharacters(in: .whitespaces))]
        case .playlist: model.playlist(playlistId) == nil ? [] : [.playlist(playlistId)]
        case .mostPlayed: [.mostPlayed]
        case .topArtist: [.topArtist]
        case .library: [.library]
        }
    }

    private func find() {
        takeOtherCount()
        model.find.find(seeds, count: count)
    }

    private func takeOtherCount() {
        if let typed = Int(otherCount.trimmingCharacters(in: .whitespaces)) {
            count = min(max(typed, 1), 500)
        }
        otherCount = ""
    }
}

/// A page's picks: a grid of cards, with how the asking is going and what went wrong.
struct PicksView: View {
    let page: DiscoverPage
    let empty: String
    @Environment(AppModel.self) private var model

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
                }
            }
        }
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
                    Text("Downloading…").font(.caption).foregroundStyle(.secondary)
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
