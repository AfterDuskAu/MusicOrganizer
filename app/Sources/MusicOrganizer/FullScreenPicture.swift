import AppKit
import MusicOrganizerKit
import SwiftUI

/// The video, or the custom visualizer, on the whole screen (the visualizer since
/// 2026-10-05, the owner: "just like video"). The controls come up when the mouse moves
/// and go away again when it rests; Esc, or a double click, brings the app back. With
/// Settings → Play Options → Show lyrics in full screen, a song's lyrics take a column on
/// the right and the picture the rest: nothing is laid over it.
struct FullScreenPicture: View {
    @Environment(AppModel.self) private var model
    @AppStorage(Player.fullScreenLyricsKey) private var lyricsOn = false
    @AppStorage(CustomVisualizer.useKey) private var useVisualizer = false
    @AppStorage(CustomVisualizer.whichKey) private var whichVisualizer = CustomVisualizer.standard
    /// The visualizer has had the screen. Switched on for now on the page, it goes back
    /// to the cover when its time is up (Settings → Play Options); here it stays until
    /// the screen is given back, so it never turns into a cover in the middle of a song.
    @State private var keepsVisualizer = false
    @State private var controlsShown = true
    @State private var hiding: Task<Void, Never>?

    var body: some View {
        let player = model.player
        let showing = showing
        ZStack {
            Color.black
            HStack(spacing: 0) {
                Group {
                    switch showing {
                    case .video:
                        VideoSurface(player: player.screen, refresh: player.pictureRefresh)
                    case .visualizer:
                        // It fills what it's given: its own background is black too.
                        CustomVisualizerView(number: CustomVisualizer.chosen(whichVisualizer))
                    case .song:
                        noVideo(player)
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                if lyricsShown && model.lyrics.settled {
                    LyricsView(large: true)
                        .frame(minWidth: 280, idealWidth: 480, maxWidth: 480)
                }
            }
            if controlsShown {
                VStack(spacing: 0) {
                    top(player)
                    Spacer()
                    bottom(player, beside: showing == .visualizer ? "visualizer" : "video")
                }
                .transition(.opacity)
            }
        }
        .ignoresSafeArea()
        .environment(\.colorScheme, .dark)
        .contentShape(Rectangle())
        .onTapGesture(count: 2) { model.setPictureFullScreen(false) }
        .onContinuousHover { phase in
            if case .active = phase { wake() }
        }
        // An arrow that changed the sound or the place: the controls show it.
        .onChange(of: model.keyPresses) { wake() }
        .onAppear { wake() }
        .onChange(of: showing, initial: true) {
            if showing == .visualizer { keepsVisualizer = true }
        }
        .onDisappear {
            hiding?.cancel()
            NSCursor.setHiddenUntilMouseMoves(false)
        }
    }

    /// The lyrics column: the setting, unless it's been switched here for now.
    private var lyricsShown: Bool { model.shows(Player.fullScreenLyricsKey, setting: lyricsOn) }

    /// What has the screen: what the Local Visualizer would show in the cover's place.
    private var showing: PagePicture {
        PagePicture.showing(
            videoReady: model.player.showsPicture,
            visualizerOn: keepsVisualizer
                || model.shows(CustomVisualizer.useKey, setting: useVisualizer),
            songPlaying: model.player.current != nil)
    }

    /// A song with no video of its own, while the screen is given to videos.
    private func noVideo(_ player: Player) -> some View {
        VStack(spacing: 16) {
            CoverView(track: player.current, size: .large, corner: 12)
                .frame(maxWidth: 420, maxHeight: 420)
            if let track = player.current {
                Text(track.title).font(.title.weight(.bold)).heading()
                Text(track.artistName).font(.title3).foregroundStyle(.secondary)
            }
            if let note = player.videoNote {
                Text(note).font(.callout).foregroundStyle(.secondary)
            }
        }
    }

    /// The ✕ in the top left corner, as a film has it (the owner, 2026-10-12), with what's
    /// playing beside it. No band across the top behind them: nothing is to run across
    /// the top of a full screen. A button here didn't work on 2026-10-03 ("the leave full
    /// screen button still doesn't work"): macOS's strip for the window's top bar lies
    /// over this corner in full screen and took the click. Now that strip lets clicks
    /// through while it's hidden (`AppModel.tidyFullScreenTopBar`), and the ✕ is clear
    /// of the edge the menu bar comes down from.
    private func top(_ player: Player) -> some View {
        HStack(alignment: .top, spacing: 14) {
            CornerClose(help: "Back to the app (Esc)") { model.setPictureFullScreen(false) }
            if let track = player.current {
                Text("\(track.title) · \(track.artistName)")
                    .font(.headline)
                    .lineLimit(1)
                    .shadow(color: .black.opacity(0.9), radius: 3)
                    .padding(.top, 66)
            }
            Spacer()
        }
    }

    /// The controls along the bottom. `picture` is what the lyrics would be beside,
    /// "video" or "visualizer", for the buttons' own words.
    private func bottom(_ player: Player, beside picture: String) -> some View {
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
                model.switchForNow(Player.fullScreenLyricsKey, to: !lyricsShown, setting: lyricsOn)
            } label: {
                Image(systemName: "quote.bubble")
                    .foregroundStyle(lyricsShown ? Color.accentColor : .primary)
            }
            .disabled(!model.lyrics.settled)
            .help(
                model.lyrics.settled
                    ? "Lyrics beside the \(picture): \(lyricsShown ? "on" : "off"). Click to "
                        + "switch them for now; your setting is in Settings → Player."
                    : "This song has no lyrics to show")
            Button {
                model.setPictureFullScreen(false)
            } label: {
                Label("Leave Full Screen", systemImage: "arrow.down.right.and.arrow.up.left")
                    .font(.callout)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(.white.opacity(0.18), in: Capsule())
                    .contentShape(Capsule())
            }
            .help("Back to the app (Esc, or double-click the \(picture))")
        }
        .buttonStyle(.plain)
        .font(.title3)
        .padding(.horizontal, 24)
        .padding(.vertical, 14)
        .background(.black.opacity(0.55))
    }

    /// Show the controls, and put them away again, with the pointer, after five seconds
    /// of a still mouse (the owner, 2026-10-17; three until then).
    private func wake() {
        if !controlsShown {
            withAnimation(.easeOut(duration: 0.15)) { controlsShown = true }
        }
        hiding?.cancel()
        hiding = Task {
            try? await Task.sleep(for: .seconds(5))
            guard !Task.isCancelled else { return }
            withAnimation(.easeIn(duration: 0.4)) { controlsShown = false }
            NSCursor.setHiddenUntilMouseMoves(true)
        }
    }
}
