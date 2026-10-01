import MusicOrganizerKit
import SwiftUI

/// The whole window given to the song that's playing: its cover, and its lyrics large
/// enough to read from across the room.
struct NowPlayingView: View {
    @Binding var isShown: Bool
    /// False when it's a page of its own (the Local Visualizer), not laid over the library.
    var closable = true
    /// False while the page is kept out of sight: a video's picture isn't drawn then.
    var isActive = true
    @Environment(AppModel.self) private var model

    var body: some View {
        let track = model.player.current
        let showsVideo = isActive && model.player.video != nil
        ZStack(alignment: .topLeading) {
            HStack(spacing: 40) {
                VStack(spacing: 14) {
                    if showsVideo {
                        VideoSurface(player: model.player.screen)
                            .aspectRatio(16.0 / 9.0, contentMode: .fit)
                            .clipShape(RoundedRectangle(cornerRadius: 12))
                            .shadow(color: .black.opacity(0.5), radius: 24, y: 10)
                    } else {
                        CoverView(track: track, size: .large, corner: 12)
                            .frame(maxWidth: 420, maxHeight: 420)
                            .shadow(color: .black.opacity(0.5), radius: 24, y: 10)
                    }
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

/// Cover or video, and the size of the video's picture, as on YouTube.
private struct VideoControls: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let player = model.player
        VStack(spacing: 8) {
            HStack(spacing: 10) {
                Picker(
                    "Show", selection: Binding(get: { player.videoOn }, set: { player.setVideo($0) })
                ) {
                    Text("Cover").tag(false)
                    Text("Video").tag(true)
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .fixedSize()
                .help("Show the song's cover, or its official video from YouTube")
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
