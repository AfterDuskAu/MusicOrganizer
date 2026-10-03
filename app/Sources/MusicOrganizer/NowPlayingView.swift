import MusicOrganizerKit
import SwiftUI

/// The whole window given to the song that's playing: its cover or its video, and its
/// lyrics large enough to read from across the room.
struct NowPlayingView: View {
    @Binding var isShown: Bool
    /// False when it's a page of its own (the Local Visualizer), not laid over the library.
    var closable = true
    /// False while the page is kept out of sight: a video's picture isn't drawn then.
    var isActive = true
    @Environment(AppModel.self) private var model

    var body: some View {
        let track = model.player.current
        // In full screen the picture is drawn there, not here as well.
        let showsVideo = isActive && model.player.showsPicture && !model.videoFullScreen
        ZStack(alignment: .topLeading) {
            sideBySide(track, showsVideo: showsVideo)
                .padding(.horizontal, 40)
                .padding(.top, 52)
                .padding(.bottom, 24)
            if closable {
                Button { isShown = false } label: {
                    Image(systemName: "chevron.down.circle.fill")
                        .font(.title)
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
                .keyboardShortcut(.cancelAction)
                .disabled(model.videoFullScreen)  // Esc leaves full screen first
                .help("Back to the library (Esc)")
                .padding(.leading, 16)
                .padding(.top, 44)  // below the window's close, minimise and zoom buttons
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background { backdrop(track) }
        .clipped()
        .environment(\.colorScheme, .dark)
    }

    /// The cover (or the video) on the left, the lyrics on the right.
    private func sideBySide(_ track: Track?, showsVideo: Bool) -> some View {
        HStack(spacing: 40) {
            VStack(spacing: 14) {
                if showsVideo {
                    picture
                } else {
                    CoverView(track: track, size: .large, corner: 12)
                        .frame(maxWidth: 420, maxHeight: 420)
                        .shadow(color: .black.opacity(0.5), radius: 24, y: 10)
                }
                PlayerControls(track: track)
            }
            .frame(maxWidth: .infinity)
            // A video gets the room: the lyrics move over, or make way if there are none.
            if !showsVideo {
                LyricsView(large: true)
                    .frame(maxWidth: .infinity)
            } else if model.lyrics.hasLyrics {
                LyricsView(large: false)
                    .frame(width: 320)
            }
        }
    }

    private var picture: some View {
        VideoSurface(player: model.player.screen, refresh: model.player.pictureRefresh)
            .aspectRatio(16.0 / 9.0, contentMode: .fit)
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .shadow(color: .black.opacity(0.5), radius: 24, y: 10)
            .onTapGesture(count: 2) { model.setVideoFullScreen(true) }
    }

    /// The cover, blurred right out, under a dark wash: the screen takes the album's colour.
    private func backdrop(_ track: Track?) -> some View {
        // Color.black sets the size. The cover is blurred while it's still tiny and only
        // then stretched to fill: blurring it at full window size was heavy enough to
        // make opening this screen stutter.
        Color.black
            .overlay {
                CoverView(track: track, size: .small, corner: 0)
                    .frame(width: 48, height: 48)
                    .blur(radius: 6)
                    .drawingGroup()
                    .scaleEffect(80)  // far bigger than any window; the edges are cut off
                    .opacity(0.55)
            }
            .clipped()
    }
}

/// Under the cover or the video, as the owner drew it (2026-10-03): on the left, Song or
/// Video and the three downloads; in the middle, what's playing; on the right, for a
/// video only, Karaoke, the picture's size and Full Screen. A note about the video, if
/// there is one, goes underneath.
private struct PlayerControls: View {
    let track: Track?
    @Environment(AppModel.self) private var model

    var body: some View {
        let player = model.player
        VStack(spacing: 10) {
            HStack(alignment: .top, spacing: 16) {
                HStack(alignment: .top, spacing: 12) {
                    Picker(
                        "Show",
                        selection: Binding(get: { player.pictureWanted }, set: { player.setVideo($0) })
                    ) {
                        Text("Song").tag(false)
                        Text("Video").tag(true)
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                    .fixedSize()
                    .help("Play the song with its cover, or its official video from YouTube")
                    if let track { DownloadButtons(track: track) }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                names
                    .frame(maxWidth: .infinity)
                HStack(spacing: 10) {
                    if player.showsPicture {
                        KaraokeButton()  // a video from YouTube, or a saved one
                        QualityMenu()  // only for a video played from YouTube
                        Button("Full Screen", systemImage: "arrow.up.left.and.arrow.down.right") {
                            model.setVideoFullScreen(true)
                        }
                        .help("Give the video the whole screen (Esc brings it back)")
                    }
                }
                .frame(maxWidth: .infinity, alignment: .trailing)
            }
            note
        }
        .padding(.top, 6)
    }

    @ViewBuilder
    private var names: some View {
        if let track {
            VStack(spacing: 4) {
                Text(track.title).font(.title.weight(.bold)).multilineTextAlignment(.center)
                Text(track.artistName).font(.title3).foregroundStyle(.secondary)
                if !track.albumName.isEmpty {
                    Text(track.albumName).font(.callout).foregroundStyle(.tertiary)
                }
                FavouriteButton(track: track)
                    .font(.title2)
                    .padding(.top, 4)
            }
        } else {
            Text("Nothing playing").font(.title2).foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private var note: some View {
        let player = model.player
        if let note = player.videoNote {
            Text(note)
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        } else if model.lyrics.videoTiming == .failed {
            Text("The lyrics couldn't be lined up with this video.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        } else if let video = player.video, !video.keepsTime, model.lyrics.hasLyrics,
            model.lyrics.forVideo != video.source.videoId, model.lyrics.videoTiming == .none
        {
            Text(
                "The video isn't the same length as the song, so the lyrics aren't timed. "
                    + "Karaoke lines them up.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
    }
}

/// Download Song, Download Video, Download Both, one above the other. Each says when
/// it's done or on its way instead. The video is the one showing, at the size showing;
/// with the song showing, it's the song's official video, found when it's asked for, at
/// its sharpest up to 1080p.
private struct DownloadButtons: View {
    let track: Track
    @Environment(AppModel.self) private var model

    var body: some View {
        let song = model.songState(of: track)
        let video = model.videoState(of: track)
        VStack(alignment: .leading, spacing: 6) {
            row(song, start: "Download Song", done: "Song in Your Library", symbol: "music.note") {
                model.downloadSongOf(track)
            }
            .help("Save the song itself, sound only, with its album details")
            row(video, start: "Download Video", done: "Video in Your Library", symbol: "film") {
                model.downloadVideoOf(track)
            }
            .help("Save the video whole, picture and sound. It counts as one of the day's downloads.")
            Button("Download Both", systemImage: "square.and.arrow.down.on.square") {
                if song == .ready { model.downloadSongOf(track) }
                if video == .ready { model.downloadVideoOf(track) }
            }
            .disabled(song != .ready || video != .ready)
            .help("The song and its video, as two downloads")
        }
        .controlSize(.small)
        .frame(width: 170, alignment: .leading)
    }

    @ViewBuilder
    private func row(
        _ state: AppModel.SaveState, start: String, done: String, symbol: String,
        action: @escaping () -> Void
    ) -> some View {
        switch state {
        case .ready:
            Button(start, systemImage: symbol, action: action)
        case .saved:
            Label(done, systemImage: "checkmark.circle.fill")
                .foregroundStyle(.green)
                .font(.callout)
        case .working(let words):
            HStack(spacing: 6) {
                ProgressView().controlSize(.mini)
                Text(words).font(.callout).monospacedDigit().foregroundStyle(.secondary)
            }
        case .failed(let why):
            Button("Try Again", systemImage: "exclamationmark.triangle", action: action)
                .help(why)
        case .unavailable(let why):
            Button(start, systemImage: symbol) {}
                .disabled(true)
                .help(why)
        }
    }
}

/// Line the lyrics up with the video that's playing. Nothing is asked of YouTube for
/// this until the button is clicked (owner, 2026-10-02); once done for a video it's
/// remembered, and that video's lyrics are in time by themselves from then on.
private struct KaraokeButton: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        if model.lyrics.videoTiming == .working {
            HStack(spacing: 6) {
                ProgressView().controlSize(.small)
                Text("Lining up the lyrics…").font(.callout).foregroundStyle(.secondary)
            }
        } else if model.lyricsFitVideo {
            Label("Lyrics in time", systemImage: "checkmark.circle.fill")
                .foregroundStyle(.green)
                .font(.callout)
                .help("The lyrics were lined up with this video")
        } else {
            Button("Karaoke", systemImage: "music.mic") { model.karaoke() }
                .help(
                    "Line the lyrics up with this video, by its sound and its captions. It asks "
                        + "YouTube for them once; after that this video is remembered.")
        }
    }
}

/// The picture sizes the video comes in, the sharpest first.
struct QualityMenu: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let player = model.player
        if let video = player.video {
            Menu(video.quality.label) {
                // Toggles, so the menu ticks the one that's chosen.
                Toggle(
                    "Best (\(video.source.qualities[0].label))",
                    isOn: Binding(
                        get: { player.videoPreference == nil },
                        set: { _ in player.setQuality(nil) }))
                Divider()
                ForEach(video.source.qualities) { quality in
                    Toggle(
                        quality.label,
                        isOn: Binding(
                            get: { player.videoPreference != nil && quality == video.quality },
                            set: { _ in player.setQuality(quality) }))
                }
            }
            .fixedSize()
            .help("The size of the video's picture")
        }
    }
}
