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
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: large ? 22 : 14) {
                    ForEach(lines) { line in
                        let sung = line.id == current
                        Text(line.text.isEmpty ? "♪" : line.text)
                            .font((large ? Font.largeTitle : .title2).weight(sung ? .bold : .medium))
                            .foregroundStyle(sung ? .primary : .tertiary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .contentShape(Rectangle())
                            .onTapGesture { model.player.seek(to: line.time) }
                            .id(line.id)
                    }
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 120)
            }
            .onChange(of: current) {
                guard let current else { return }
                withAnimation(.easeInOut(duration: 0.35)) {
                    proxy.scrollTo(current, anchor: .center)
                }
            }
            .onAppear {
                if let current { proxy.scrollTo(current, anchor: .center) }
            }
        }
    }
}
