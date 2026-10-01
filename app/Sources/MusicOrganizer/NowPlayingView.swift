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
                names(track)
                VideoControls()
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
        VideoSurface(player: model.player.screen)
            .aspectRatio(16.0 / 9.0, contentMode: .fit)
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .shadow(color: .black.opacity(0.5), radius: 24, y: 10)
            .onTapGesture(count: 2) { model.setVideoFullScreen(true) }
    }

    @ViewBuilder
    private func names(_ track: Track?) -> some View {
        if let track {
            VStack(spacing: 4) {
                Text(track.title).font(.title.weight(.bold)).multilineTextAlignment(.center)
                Text(track.artistName).font(.title3).foregroundStyle(.secondary)
                if !track.albumName.isEmpty {
                    Text(track.albumName).font(.callout).foregroundStyle(.tertiary)
                }
            }
            FavouriteButton(track: track)
                .font(.title2)
        } else {
            Text("Nothing playing").font(.title2).foregroundStyle(.secondary)
        }
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

/// Cover or video; and for a video, the size of its picture (as on YouTube) and a way to
/// give it the whole screen.
private struct VideoControls: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let player = model.player
        VStack(spacing: 8) {
            HStack(spacing: 10) {
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
                if player.showsPicture {
                    QualityMenu()  // only for a video played from YouTube
                    Button("Full Screen", systemImage: "arrow.up.left.and.arrow.down.right") {
                        model.setVideoFullScreen(true)
                    }
                    .help("Give the video the whole screen (Esc brings it back)")
                }
                if let video = player.video {
                    SaveVideoButton(video: video)
                    // The song itself, for a song being played from YouTube Music: a
                    // video is a different file, and often a different cut.
                    if let songId = player.current?.videoId {
                        SaveSongButton(videoId: songId)
                    }
                }
            }
            if let note = player.videoNote {
                Text(note)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            } else if let video = player.video, !video.keepsTime, model.lyrics.hasLyrics {
                Text("The video isn't the same length as the song, so the lyrics aren't timed.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }
        }
        .padding(.top, 6)
    }
}

/// Keep the video that's playing: saved whole, at the picture size that's showing.
private struct SaveVideoButton: View {
    let video: ShowingVideo
    @Environment(AppModel.self) private var model

    var body: some View {
        let videoId = video.source.videoId
        if model.everything.videoIDs.contains(videoId) {
            Label("Saved", systemImage: "checkmark.circle.fill")
                .foregroundStyle(.green)
                .font(.callout)
                .help("This video is in your library")
        } else {
            switch model.downloadState(of: videoId) {
            case .working:
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small)
                    Text("Saving…").font(.callout).foregroundStyle(.secondary)
                }
            case .failed(let why):
                Button("Try Again", systemImage: "exclamationmark.triangle") {
                    model.saveVideo(video)
                }
                .help(why)
            case nil:
                Button("Save Video", systemImage: "arrow.down.circle") { model.saveVideo(video) }
                    .help(
                        "Save this video in your library at \(video.quality.label). It counts "
                            + "as one of the day's downloads.")
            }
        }
    }
}

/// Download the song too, beside its video: the audio alone, as any downloaded song.
private struct SaveSongButton: View {
    let videoId: String
    @Environment(AppModel.self) private var model

    var body: some View {
        if model.everything.videoIDs.contains(videoId) {
            Label("Song Saved", systemImage: "checkmark.circle.fill")
                .foregroundStyle(.green)
                .font(.callout)
                .help("This song is in your library")
        } else {
            switch model.downloadState(of: videoId) {
            case .working:
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small)
                    Text("Downloading…").font(.callout).foregroundStyle(.secondary)
                }
            case .failed(let why):
                Button("Try Again", systemImage: "exclamationmark.triangle") {
                    model.downloadSong(videoId)
                }
                .help(why)
            case nil:
                Button("Download Song Too", systemImage: "music.note") {
                    model.downloadSong(videoId)
                }
                .help("Save the song itself (sound only, with its album details) in your library")
            }
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
