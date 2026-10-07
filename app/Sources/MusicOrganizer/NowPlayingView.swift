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
    /// Settings → Play Options → Always show lyrics. The lyrics button in the player bar
    /// switches the page for now, without changing the setting.
    @AppStorage(Player.visualizerLyricsKey) private var lyricsOn = true
    /// Settings → Play Options → Custom Visualizer. The page's Song | Video | Visualizer
    /// switch changes the first for now, without changing the setting.
    @AppStorage(CustomVisualizer.useKey) private var useVisualizer = false
    @AppStorage(CustomVisualizer.whichKey) private var whichVisualizer = CustomVisualizer.standard

    var body: some View {
        let track = model.player.current
        // A video, or the custom visualizer for a song that's playing, stands where the
        // cover would. It's only drawn while the page can be seen; and in full screen
        // it's drawn there, not here as well.
        let showing: PagePicture =
            isActive && !model.pictureFullScreen
            ? PagePicture.showing(
                videoReady: model.player.showsPicture,
                visualizerOn: model.shows(CustomVisualizer.useKey, setting: useVisualizer),
                songPlaying: track != nil)
            : .song
        // A song with no lyrics, or lyrics turned off: the cover or video has the page.
        let showsLyrics =
            model.shows(Player.visualizerLyricsKey, setting: lyricsOn) && model.lyrics.settled
        ZStack(alignment: .topLeading) {
            sideBySide(track, showing: showing, showsLyrics: showsLyrics)
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
                .disabled(model.pictureFullScreen)  // Esc leaves full screen first
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

    /// The cover (or the video, or the custom visualizer) on the left, the lyrics on the
    /// right. The left side, as the owner drew it (2026-10-03): Song | Video | Visualizer
    /// on top, the picture, and everything else centred under it: the song's name, the
    /// heart, Karaoke (and Full Screen), then the downloads. With no lyrics to show, the
    /// left side is the whole page, and the cover may be bigger.
    private func sideBySide(
        _ track: Track?, showing: PagePicture, showsLyrics: Bool
    ) -> some View {
        let cover: CGFloat = showsLyrics ? 420 : 560
        return HStack(spacing: 32) {
            VStack(spacing: 14) {
                ShowPicker()
                switch showing {
                case .video:
                    picture
                case .visualizer:
                    visualizer
                case .song:
                    CoverView(track: track, size: .large, corner: 12)
                        .aspectRatio(1, contentMode: .fit)
                        .frame(maxWidth: cover, maxHeight: cover)
                        .shadow(color: .black.opacity(0.5), radius: 24, y: 10)
                }
                PlayerControls(track: track, showing: showing)
            }
            .frame(minWidth: 320, maxWidth: .infinity)
            // A video or a visualizer gets the room: the lyrics take a narrow column
            // beside it.
            if showsLyrics {
                if showing != .song {
                    LyricsView(large: false)
                        .frame(minWidth: 200, idealWidth: 320, maxWidth: 320)
                } else {
                    LyricsView(large: true)
                        .frame(minWidth: 220, maxWidth: .infinity)
                }
            }
        }
    }

    private var picture: some View {
        VideoSurface(player: model.player.screen, refresh: model.player.pictureRefresh)
            .aspectRatio(16.0 / 9.0, contentMode: .fit)
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .shadow(color: .black.opacity(0.5), radius: 24, y: 10)
            .onTapGesture(count: 2) { model.setPictureFullScreen(true) }
    }

    /// The custom visualizer chosen in Settings, in the video's shape and place. Like
    /// the video, a double click gives it the whole screen.
    private var visualizer: some View {
        CustomVisualizerView(number: CustomVisualizer.chosen(whichVisualizer))
            .aspectRatio(16.0 / 9.0, contentMode: .fit)
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .shadow(color: .black.opacity(0.5), radius: 24, y: 10)
            .onTapGesture(count: 2) { model.setPictureFullScreen(true) }
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

/// Song, Video or Visualizer, always in the same place: above the picture, in the middle.
/// Song and Visualizer both play the song itself: the one shows its cover, the other the
/// custom visualizer where the cover would be.
private struct ShowPicker: View {
    @Environment(AppModel.self) private var model
    @AppStorage(CustomVisualizer.useKey) private var useVisualizer = false

    var body: some View {
        let player = model.player
        Picker(
            "Show",
            selection: Binding(
                get: {
                    PagePicture.chosen(
                        videoWanted: player.pictureWanted,
                        visualizerOn: model.shows(CustomVisualizer.useKey, setting: useVisualizer))
                },
                set: { show($0) })
        ) {
            ForEach(PagePicture.allCases, id: \.self) { Text($0.label).tag($0) }
        }
        .pickerStyle(.segmented)
        .labelsHidden()
        .fixedSize()
        .help(
            "Play the song with its cover, its official video from the service, or the song "
                + "with the visualizer moving to it")
    }

    private func show(_ picture: PagePicture) {
        model.player.setVideo(picture == .video)
        guard picture != .video else { return }
        // Only for now: whether the visualizer takes the cover's place is a standing
        // choice, made in Settings → Play Options.
        model.switchForNow(
            CustomVisualizer.useKey, to: picture == .visualizer, setting: useVisualizer)
    }
}

/// Under the picture, everything centred, one thing under the next (the owner,
/// 2026-10-03: "I quite like this, it's all centred"): the song's name, artist and album;
/// the heart; Karaoke, for a video its picture size, and for a video or the visualizer
/// Full Screen; then the three downloads, in a row when there's room and one above the
/// other when there isn't. A note about the video, if there is one, goes last.
private struct PlayerControls: View {
    let track: Track?
    /// What's drawn above: the video, the custom visualizer or the cover.
    let showing: PagePicture
    @Environment(AppModel.self) private var model
    @AppStorage("alwaysBestVideo") private var alwaysBestVideo = false

    private var showsVideo: Bool { showing == .video }

    var body: some View {
        VStack(spacing: 14) {
            VStack(spacing: 10) {
                names.frame(maxWidth: .infinity)
                HStack(spacing: 10) { extras }
            }
            if let track { DownloadButtons(track: track) }
            note
        }
        .padding(.top, 2)
    }

    @ViewBuilder
    private var names: some View {
        if let track {
            VStack(spacing: 4) {
                Text(track.title)
                    .font(.title.weight(.bold))
                    .heading()
                    .multilineTextAlignment(.center)
                    .lineLimit(3)
                    .minimumScaleFactor(0.75)
                Text(track.artistName)
                    .font(.title3)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .lineLimit(2)
                if !track.albumName.isEmpty {
                    Text(track.albumName)
                        .font(.callout)
                        .foregroundStyle(.tertiary)
                        .lineLimit(1)
                }
                // The thumbs-up count sits to the heart's right. An unseen copy of it on
                // the left takes the same room, so the heart stays in the middle.
                let likes = model.player.current?.id == track.id ? model.player.likes : nil
                HStack(spacing: 12) {
                    if let likes { LikesLabel(likes: likes, ofVideo: showsVideo).hidden() }
                    FavouriteButton(track: track).font(.title2)
                    if let likes { LikesLabel(likes: likes, ofVideo: showsVideo) }
                }
                .padding(.top, 4)
            }
            .fixedSize(horizontal: false, vertical: true)
        } else {
            Text("Nothing playing").font(.title2).foregroundStyle(.secondary)
        }
    }

    /// Under the heart: Karaoke for every song; for a video, its picture size (unless
    /// Settings keeps it at the sharpest); and for a video or the visualizer, Full Screen.
    @ViewBuilder
    private var extras: some View {
        if track != nil {
            KaraokeButton(videoShowing: showsVideo)
        }
        if showsVideo && !alwaysBestVideo { QualityMenu() }
        if showing.canFillTheScreen {
            Button {
                model.setPictureFullScreen(true)
            } label: {
                Image(systemName: "arrow.up.left.and.arrow.down.right")
            }
            .help(
                "Give the \(showsVideo ? "video" : "visualizer") the whole screen "
                    + "(Esc brings it back)")
        }
    }

    /// The song playing is a file in the library (not one played from YouTube): Karaoke
    /// then fetches one sound, the video's, and not the song's as well.
    private var songIsAFile: Bool { track?.videoId == nil }

    @ViewBuilder
    private var note: some View {
        let player = model.player
        if let note = player.videoNote {
            caption(note)
        } else if let note = model.lyricsNote {
            caption(note)
        } else if model.lyrics.videoTiming == .failed {
            caption("The lyrics couldn't be lined up with this video.")
        } else if let video = player.video, !video.keepsTime, model.lyrics.hasLyrics,
            model.lyrics.forVideo != video.source.videoId, model.lyrics.videoTiming == .none
        {
            caption(
                "The video isn't the same length as the song, so the lyrics aren't timed. "
                    + "Karaoke lines them up. " + KaraokeCost.words(songIsAFile: songIsAFile))
        }
    }

    private func caption(_ words: String) -> some View {
        Text(words)
            .font(.callout)
            .foregroundStyle(.secondary)
            .multilineTextAlignment(.center)
            .frame(maxWidth: 420)
    }
}

/// The thumbs-up count YouTube shows for the song (or its video) that's playing. It comes
/// with the answer that says where to play it from, so it's only there for what plays
/// from YouTube: a song played from the library's own file has none.
private struct LikesLabel: View {
    let likes: Int
    let ofVideo: Bool

    var body: some View {
        Label(CompactCount.text(likes), systemImage: "hand.thumbsup")
            .labelStyle(.titleAndIcon)
            .font(.callout)
            .monospacedDigit()
            .foregroundStyle(.secondary)
            .lineLimit(1)
            .fixedSize()
            .help(
                "\(likes.formatted()) thumbs up on the service for this \(ofVideo ? "video" : "song")")
    }
}

/// Download Song, Download Video, Download Both: in a row when there's room, one above
/// the other when there isn't. Each says when it's done or on its way instead. The
/// video is the one showing, at the size showing; with the song showing, it's the
/// song's official video, found when it's asked for, at its sharpest up to 1080p.
private struct DownloadButtons: View {
    let track: Track
    @Environment(AppModel.self) private var model

    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 12) { buttons }
            VStack(alignment: .leading, spacing: 6) { buttons }
        }
        .controlSize(.small)
    }

    @ViewBuilder
    private var buttons: some View {
        let song = model.songState(of: track)
        let video = model.videoState(of: track)
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
                .lineLimit(1)
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

/// Karaoke, on every song (owner, 2026-10-03). With the video showing: line the lyrics up
/// with it, by its sound and its captions (nothing is asked of YouTube for this until
/// it's clicked; once done for a video it's remembered). With the song showing: find
/// lyrics for a song that has none, or only plain ones (timed where they exist, from
/// LRCLIB or YouTube Music), and keep them: saved into a song of the owner's, shown for
/// one played from YouTube.
private struct KaraokeButton: View {
    let videoShowing: Bool
    @Environment(AppModel.self) private var model

    var body: some View {
        if model.lyrics.videoTiming == .working {
            ProgressView()
                .controlSize(.small)
                .help(videoShowing ? "Lining up the lyrics…" : "Looking for lyrics…")
        } else if videoShowing && model.lyricsFitVideo {
            Image(systemName: "checkmark.circle.fill")
                .foregroundStyle(.green)
                .help("The lyrics were lined up with this video")
        } else if videoShowing {
            Button { model.karaoke() } label: { Image(systemName: "music.mic") }
                .help(
                    "Karaoke: line the lyrics up with this video, by its sound and its captions. "
                        + KaraokeCost.words(songIsAFile: model.player.current?.videoId == nil)
                        + " It's fetched once; after that this video is remembered and costs "
                        + "nothing.")
        } else {
            let timed = model.lyrics.isSynced
            Button { model.karaokeSong() } label: { Image(systemName: "music.mic") }
                .disabled(timed)
                .help(
                    timed
                        ? "Karaoke: these lyrics are timed already"
                        : "Karaoke: find timed lyrics for this song and keep them")
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
