import MusicOrganizerKit
import SwiftUI

struct PlayerBar: View {
    @Binding var showLyrics: Bool
    @Environment(AppModel.self) private var model
    @State private var showQueue = false

    var body: some View {
        let player = model.player
        VStack(spacing: 0) {
            Divider()
            if let problem = player.problem {
                Text(problem)
                    .font(.callout)
                    .foregroundStyle(.red)
                    .padding(.top, 6)
            }
            HStack(spacing: 16) {
                nowPlaying(player)
                    .frame(maxWidth: .infinity, alignment: .leading)
                VStack(spacing: 2) {
                    buttons(player)
                    Scrubber(player: player)
                }
                .frame(minWidth: 320, idealWidth: 460, maxWidth: 520)
                extras(player)
                    .frame(maxWidth: .infinity, alignment: .trailing)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
        }
        .background(.bar)
    }

    private func nowPlaying(_ player: Player) -> some View {
        HStack(spacing: 10) {
            CoverView(track: player.current, size: .small, corner: 5)
                .frame(width: 44, height: 44)
            if let track = player.current {
                VStack(alignment: .leading, spacing: 2) {
                    Text(track.title).fontWeight(.medium).lineLimit(1)
                    Text(track.artistName).font(.callout).foregroundStyle(.secondary).lineLimit(1)
                }
            } else {
                Text("Nothing playing").foregroundStyle(.secondary)
            }
        }
    }

    private func buttons(_ player: Player) -> some View {
        HStack(spacing: 18) {
            Button {
                player.setShuffle(!player.queue.shuffle)
            } label: {
                Image(systemName: "shuffle")
                    .foregroundStyle(player.queue.shuffle ? Color.accentColor : .secondary)
            }
            .help("Shuffle")
            Button { player.previous() } label: { Image(systemName: "backward.fill") }
                .help("Previous")
            Button { player.toggle() } label: {
                Image(systemName: player.isPlaying ? "pause.fill" : "play.fill")
                    .font(.title2)
                    .frame(width: 26)
            }
            .help(player.isPlaying ? "Pause (Space)" : "Play (Space)")
            Button { player.next() } label: { Image(systemName: "forward.fill") }
                .help("Next")
            Button {
                player.cycleRepeat()
            } label: {
                Image(systemName: player.queue.repeatMode == .one ? "repeat.1" : "repeat")
                    .foregroundStyle(player.queue.repeatMode == .off ? .secondary : Color.accentColor)
            }
            .help("Repeat: \(player.queue.repeatMode.label)")
        }
        .buttonStyle(.plain)
        .font(.title3)
        .disabled(player.current == nil)
    }

    private func extras(_ player: Player) -> some View {
        @Bindable var player = player
        return HStack(spacing: 12) {
            Image(systemName: "speaker.fill").foregroundStyle(.secondary)
            Slider(value: $player.volume, in: 0...1)
                .controlSize(.small)
                .frame(width: 90)
            Button { showLyrics.toggle() } label: {
                Image(systemName: "quote.bubble")
                    .foregroundStyle(showLyrics ? Color.accentColor : .primary)
            }
            .help("Lyrics")
            Button { showQueue.toggle() } label: { Image(systemName: "list.bullet") }
                .help("Up next")
                .popover(isPresented: $showQueue, arrowEdge: .top) { UpNext().environment(model) }
        }
        .buttonStyle(.plain)
        .font(.title3)
    }
}

/// The position slider. It watches the clock by itself, so the rest of the bar stays put.
private struct Scrubber: View {
    let player: Player
    @State private var dragging: Double?

    var body: some View {
        let clock = player.clock
        HStack(spacing: 8) {
            Text(clockTime(player.current == nil ? nil : dragging ?? clock.time))
                .frame(width: 40, alignment: .trailing)
            Slider(
                value: Binding(get: { dragging ?? clock.time }, set: { dragging = $0 }),
                in: 0...max(clock.duration, 1)
            ) { editing in
                if !editing, let dragging {
                    player.seek(to: dragging)
                    self.dragging = nil
                }
            }
            .controlSize(.mini)
            .disabled(player.current == nil)
            Text(clockTime(player.current == nil ? nil : clock.duration))
                .frame(width: 40, alignment: .leading)
        }
        .font(.caption)
        .monospacedDigit()
        .foregroundStyle(.secondary)
    }
}

private struct UpNext: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let upNext = Array(model.player.queue.upNext.prefix(100))
        VStack(alignment: .leading, spacing: 0) {
            Text("Up Next").font(.headline).padding(12)
            Divider()
            if upNext.isEmpty {
                Text("Nothing after this song.")
                    .foregroundStyle(.secondary)
                    .padding(20)
                    .frame(maxWidth: .infinity)
            } else {
                List(Array(upNext.enumerated()), id: \.offset) { offset, track in
                    Button {
                        model.player.jump(toUpNext: offset)
                    } label: {
                        HStack(spacing: 8) {
                            CoverView(track: track, size: .small, corner: 4)
                                .frame(width: 30, height: 30)
                            VStack(alignment: .leading) {
                                Text(track.title).lineLimit(1)
                                Text(track.artistName)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                            }
                            Spacer(minLength: 0)
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
                .listStyle(.plain)
            }
        }
        .frame(width: 320, height: 420)
    }
}
