import MusicOrganizerKit
import SwiftUI

/// Search YouTube Music: play anything straight away, and download a song into the
/// library only when its Download button is clicked.
struct YouTubeSearchView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        VStack(spacing: 0) {
            HStack {
                Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                TextField("Search YouTube Music for a song, artist or album", text: $model.youtubeQuery)
                    .textFieldStyle(.plain)
                    .font(.title3)
                    .onSubmit { model.searchYouTube() }
                if model.youtubeSearching {
                    ProgressView().controlSize(.small)
                } else {
                    Button("Search") { model.searchYouTube() }
                        .disabled(model.youtubeQuery.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
            .padding(12)
            Divider()
            if let problem = model.youtubeProblem {
                Text(problem)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if model.youtubeResults.isEmpty {
                Text("Search, then press play to listen. Nothing is saved unless you click Download.")
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(40)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                let results = model.youtubeResults
                List {
                    ForEach(Array(results.enumerated()), id: \.element.id) { index, result in
                        ResultRow(result: result) {
                            model.player.play(results.map(\.track), startAt: index)
                        }
                    }
                    if model.youtubeHasMore {
                        HStack {
                            Spacer()
                            if model.youtubeSearching {
                                ProgressView().controlSize(.small)
                            } else {
                                Button("Show \(AppModel.youtubeStep) More", systemImage: "chevron.down") {
                                    model.moreFromYouTube()
                                }
                            }
                            Spacer()
                        }
                        .padding(.vertical, 6)
                    }
                }
            }
        }
    }
}

struct ResultRow: View {
    let result: SearchResult
    /// False on the YouTube Queue page itself: there's nothing to queue there.
    var queueButton = true
    /// The engine says the owner has this song (by its name, not only its YouTube id).
    var owned = false
    /// False on an artist's own page: every row there is theirs.
    var artistButton = true
    /// A page that holds the song as the engine gave it downloads it with that, so the
    /// engine needn't look it up again.
    var download: (() -> Void)?
    let play: () -> Void
    @Environment(AppModel.self) private var model
    /// The row is in a narrow place (an artist's page beside the owner's own side): the
    /// Queue button is its symbol alone, and a length that isn't known takes no room.
    @Environment(\.narrowRows) private var narrow

    var body: some View {
        let track = result.track
        let playing = model.player.current?.id == track.id
        HStack(spacing: 10) {
            Button(action: play) {
                ZStack {
                    CoverView(track: track, size: .small, corner: 4)
                        .frame(width: 40, height: 40)
                    Image(systemName: playing ? "speaker.wave.2.fill" : "play.fill")
                        .foregroundStyle(.white)
                        .shadow(radius: 3)
                }
            }
            .buttonStyle(.plain)
            .help("Play from YouTube Music")
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(result.title).fontWeight(playing ? .semibold : .regular).lineLimit(1)
                    if model.heard.contains(result.videoId) { HeardMark() }
                    if result.isExplicit == true {
                        Image(systemName: "e.square.fill").foregroundStyle(.secondary)
                    }
                }
                Text([track.artistName, result.album].compactMap { $0 }.joined(separator: " · "))
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 8)
            if let plays = result.plays {
                Label(plays, systemImage: "play.circle")
                    .font(.callout)
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
                    .help("Played \(plays) times on YouTube Music")
            }
            if artistButton, let artist = result.artists.first {
                Button {
                    model.showArtist(artist)
                } label: {
                    Image(systemName: "person.crop.circle").foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
                .help("Artist: about \(artist), their songs and albums")
            }
            if queueButton {
                let queued = model.isQueued(result)
                let button = Button(
                    queued ? "Queued" : "Queue", systemImage: queued ? "checkmark" : "text.append"
                ) {
                    model.toggleQueued(result)
                }
                .help(
                    queued
                        ? "In the YouTube Queue. Click to take it out."
                        : "Queue: play this after the song that's playing, and keep it in the "
                            + "YouTube Queue")
                if narrow { button.labelStyle(.iconOnly) } else { button }
            }
            if !narrow || result.durationS != nil {
                Text(clockTime(result.durationS))
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
                    .frame(width: 44, alignment: .trailing)
            }
            status
                .frame(width: narrow ? 118 : 130, alignment: .trailing)
        }
        .padding(.vertical, 3)
        .contentShape(Rectangle())
        .onTapGesture(count: 2, perform: play)
        .contextMenu { ArtistInfoItems(artists: result.artists) }
    }

    private func startDownload() {
        if let download { download() } else { model.download(result) }
    }

    @ViewBuilder
    private var status: some View {
        if owned || model.everything.videoIDs.contains(result.videoId) {
            Label("In your library", systemImage: "checkmark.circle.fill")
                .foregroundStyle(.green)
                .font(.callout)
        } else {
            switch model.downloadState(of: result.videoId) {
            case .working:
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small)
                    Text(model.downloadNote(of: result.videoId))
                        .font(.callout)
                        .monospacedDigit()
                        .foregroundStyle(.secondary)
                }
            case .failed(let why):
                Button("Try Again", systemImage: "exclamationmark.triangle", action: startDownload)
                    .help(why)
            case nil:
                Button("Download", systemImage: "arrow.down.circle", action: startDownload)
                    .help("Save this song in your library")
            }
        }
    }
}

extension EnvironmentValues {
    /// Song rows here are in a narrow place, and take a slimmer form (`ResultRow`).
    @Entry var narrowRows = false
}

/// "Artist Info" on a song's right-click menu: one entry for a song with one artist,
/// one each (by name) for a song credited to several.
struct ArtistInfoItems: View {
    let artists: [String]
    @Environment(AppModel.self) private var model

    var body: some View {
        let names = ArtistSongs.names(artists)
        if names.count == 1 {
            Button("Artist Info") { model.showArtist(names[0]) }
        } else {
            ForEach(names, id: \.self) { name in
                Button("Artist Info: \(name)") { model.showArtist(name) }
            }
        }
    }
}

/// Media → YouTube Queue: the songs put on with Up Next, in the order they'll play.
/// None of them is downloaded; each can be, from here.
struct YouTubeQueueView: View {
    @Environment(AppModel.self) private var model
    @State private var showingPlayed = false

    var body: some View {
        let queue = model.youtubeQueue
        VStack(spacing: 0) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text("YouTube Queue").font(.title2.weight(.semibold))
                        Text(queue.count == 1 ? "1 song" : "\(queue.count) songs")
                            .foregroundStyle(.secondary)
                    }
                    Text("Songs from YouTube Music you queued. They play in this order; once played they move to Played Already.")
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("Play", systemImage: "play.fill") { model.playYouTubeQueue() }
                    .disabled(queue.isEmpty)
                Button("Clear", systemImage: "xmark.circle") { model.clearYouTubeQueue() }
                    .disabled(queue.isEmpty)
                    .help("Empty the YouTube Queue. Nothing downloaded is touched.")
                Button("Played Already (\(model.youtubePlayed.count))", systemImage: "clock.arrow.circlepath") {
                    showingPlayed = true
                }
                .disabled(model.youtubePlayed.isEmpty)
                .help("Songs from the queue that have been played. Click one to play it again.")
                .popover(isPresented: $showingPlayed, arrowEdge: .bottom) { PlayedAlready() }
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 12)
            Divider()
            if queue.isEmpty {
                Text("Nothing is queued. Click Queue beside a song on YouTube Music, or Q on What's New and Find.")
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List {
                    ForEach(queue) { result in
                        HStack(spacing: 8) {
                            ResultRow(result: result, queueButton: false) {
                                model.playYouTubeQueue(from: result)
                            }
                            Button {
                                model.removeFromYouTubeQueue(result)
                            } label: {
                                Image(systemName: "minus.circle").foregroundStyle(.secondary)
                            }
                            .buttonStyle(.plain)
                            .help("Take it out of the YouTube Queue")
                        }
                    }
                }
            }
        }
    }
}

/// The songs played from the YouTube Queue, newest first: click one to play it again.
private struct PlayedAlready: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("Played Already").font(.headline)
                Spacer()
                Button("Clear") { model.clearPlayedFromQueue() }
                    .controlSize(.small)
            }
            .padding(12)
            Divider()
            List(model.youtubePlayed) { result in
                Button {
                    model.player.play([result.track])
                } label: {
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
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .help("Play it again")
            }
        }
        .frame(width: 380, height: 420)
    }
}
