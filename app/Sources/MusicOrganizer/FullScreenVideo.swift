import AppKit
import MusicOrganizerKit
import SwiftUI

/// The video on the whole screen. The controls come up when the mouse moves and go
/// away again when it rests; Esc, or a double click, brings the app back. With Settings →
/// Play Options → Show lyrics in full screen, a song's lyrics take a column on the
/// right and the video the rest: nothing is laid over the picture.
struct FullScreenVideo: View {
    @Environment(AppModel.self) private var model
    @AppStorage(Player.fullScreenLyricsKey) private var lyricsOn = false
    @State private var controlsShown = true
    @State private var hiding: Task<Void, Never>?

    var body: some View {
        let player = model.player
        ZStack {
            Color.black
            HStack(spacing: 0) {
                Group {
                    if player.showsPicture {
                        VideoSurface(player: player.screen, refresh: player.pictureRefresh)
                    } else {
                        noVideo(player)
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                if lyricsOn && model.lyrics.settled {
                    LyricsView(large: true)
                        .frame(minWidth: 280, idealWidth: 480, maxWidth: 480)
                }
            }
            if controlsShown {
                VStack(spacing: 0) {
                    top(player)
                    Spacer()
                    bottom(player)
                }
                .transition(.opacity)
            }
        }
        .ignoresSafeArea()
        .environment(\.colorScheme, .dark)
        .contentShape(Rectangle())
        .onTapGesture(count: 2) { model.setVideoFullScreen(false) }
        .onContinuousHover { phase in
            if case .active = phase { wake() }
        }
        .onAppear { wake() }
        .onDisappear {
            hiding?.cancel()
            NSCursor.setHiddenUntilMouseMoves(false)
        }
    }

    /// A song with no video of its own, while the screen is given to videos.
    private func noVideo(_ player: Player) -> some View {
        VStack(spacing: 16) {
            CoverView(track: player.current, size: .large, corner: 12)
                .frame(maxWidth: 420, maxHeight: 420)
            if let track = player.current {
                Text(track.title).font(.title.weight(.bold))
                Text(track.artistName).font(.title3).foregroundStyle(.secondary)
            }
            if let note = player.videoNote {
                Text(note).font(.callout).foregroundStyle(.secondary)
            }
        }
    }

    /// What's playing, along the top. No button up here: in full screen macOS keeps the
    /// top edge for its menu bar, which slides down over it and took the click (owner,
    /// 2026-10-03: "the leave full screen button still doesn't work").
    private func top(_ player: Player) -> some View {
        HStack(spacing: 12) {
            if let track = player.current {
                Text("\(track.title) · \(track.artistName)")
                    .font(.headline)
                    .lineLimit(1)
            }
            Spacer()
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 14)
        .background(.black.opacity(0.55))
    }

    private func bottom(_ player: Player) -> some View {
        @Bindable var player = player
        return HStack(spacing: 16) {
            Button { player.previous() } label: { Image(systemName: "backward.fill") }
                .help("Previous")
            Button { player.toggle() } label: {
                if player.isFetching || player.isBuffering {
                    ProgressView().controlSize(.small).frame(width: 26)
                } else {
                    Image(systemName: player.isPlaying ? "pause.fill" : "play.fill")
                        .font(.title2)
                        .frame(width: 26)
                }
            }
            .help(player.isPlaying ? "Pause (Space)" : "Play (Space)")
            Button { player.next() } label: { Image(systemName: "forward.fill") }
                .help("Next")
            Scrubber(player: player)
            Image(systemName: "speaker.fill").foregroundStyle(.secondary)
            Slider(value: $player.volume, in: 0...1)
                .controlSize(.small)
                .frame(width: 90)
            QualityMenu()
            Button {
                model.setVideoFullScreen(false)
            } label: {
                Label("Leave Full Screen", systemImage: "arrow.down.right.and.arrow.up.left")
                    .font(.callout)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(.white.opacity(0.18), in: Capsule())
                    .contentShape(Capsule())
            }
            .help("Back to the app (Esc, or double-click the video)")
        }
        .buttonStyle(.plain)
        .font(.title3)
        .padding(.horizontal, 24)
        .padding(.vertical, 14)
        .background(.black.opacity(0.55))
    }

    /// Show the controls, and put them away again after three seconds of a still mouse.
    private func wake() {
        if !controlsShown {
            withAnimation(.easeOut(duration: 0.15)) { controlsShown = true }
        }
        hiding?.cancel()
        hiding = Task {
            try? await Task.sleep(for: .seconds(3))
            guard !Task.isCancelled else { return }
            withAnimation(.easeIn(duration: 0.4)) { controlsShown = false }
            NSCursor.setHiddenUntilMouseMoves(true)
        }
    }
}
