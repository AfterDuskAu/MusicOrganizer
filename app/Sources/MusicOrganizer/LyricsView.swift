import MusicOrganizerKit
import SwiftUI

/// The lyrics beside the library: timed lines light up as they're sung, and a click on a
/// line jumps there.
struct LyricsView: View {
    var large = false
    @Environment(AppModel.self) private var model

    var body: some View {
        Group {
            switch model.lyrics.state {
            case .nothingPlaying:
                note("Play a song to see its lyrics.")
            case .loading:
                ProgressView()
            case .missing:
                note("No lyrics for this song yet.")
            case .plain(let text):
                ScrollView {
                    Text(text)
                        .font(large ? .title : .title3)
                        .lineSpacing(6)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(20)
                }
            case .synced(let lines):
                TimedLyrics(lines: lines, large: large)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func note(_ text: String) -> some View {
        Text(text)
            .foregroundStyle(.secondary)
            .multilineTextAlignment(.center)
            .padding(24)
    }
}

private struct TimedLyrics: View {
    let lines: [LyricLine]
    let large: Bool
    @Environment(AppModel.self) private var model

    var body: some View {
        let current = model.lyrics.currentLine
        let timed = model.lyrics.timed
        ScrollViewReader { proxy in
            ScrollView {
                // Every line is laid out once and never changes size: the line being sung
                // is shown by brightness alone. (Bold text and a lazily built list made
                // the lyrics jump about as they scrolled: Fix A-1.)
                VStack(alignment: .leading, spacing: large ? 22 : 14) {
                    ForEach(lines) { line in
                        Text(line.text.isEmpty ? "♪" : line.text)
                            .font((large ? Font.largeTitle : .title2).weight(.semibold))
                            .foregroundStyle(.primary)
                            .opacity(line.id == current ? 1 : timed ? 0.3 : 0.7)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .contentShape(Rectangle())
                            .onTapGesture { if timed { model.player.seek(to: line.time) } }
                            .id(line.id)
                    }
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 160)
                .animation(.easeInOut(duration: 0.3), value: current)
            }
            .onChange(of: current) {
                guard let current else { return }
                withAnimation(.easeInOut(duration: 0.6)) {
                    proxy.scrollTo(current, anchor: .center)
                }
            }
            .onAppear {
                if let current { proxy.scrollTo(current, anchor: .center) }
            }
        }
    }
}
