import MusicOrganizerKit
import SwiftUI

/// Time lyrics by ear: the song plays, and a tap (or the space bar) as each line starts
/// records when it's sung. Nothing is saved here; the timed text goes back to the Edit
/// Details sheet, where Save keeps it.
struct TapSyncSheet: View {
    let track: Track
    let words: [String]
    let done: (String) -> Void

    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var times: [Double] = []
    @State private var started = false

    private var finished: Bool { times.count >= words.count }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Sync by Tapping").font(.headline)
            Text(
                started
                    ? "Tap (or press the space bar) at the moment each line starts."
                    : "The song will play from the start. Tap as each line begins to be sung.")
                .foregroundStyle(.secondary)
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(Array(words.enumerated()), id: \.offset) { index, line in
                            HStack(alignment: .firstTextBaseline, spacing: 10) {
                                Text(index < times.count ? clockTime(times[index]) : "")
                                    .monospacedDigit()
                                    .foregroundStyle(.secondary)
                                    .frame(width: 44, alignment: .trailing)
                                Text(line)
                                    .fontWeight(index == times.count ? .semibold : .regular)
                                    .opacity(index < times.count ? 0.45 : 1)
                            }
                            .id(index)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(12)
                }
                .frame(height: 300)
                .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 8))
                .onChange(of: times.count) {
                    withAnimation { proxy.scrollTo(min(times.count, words.count - 1), anchor: .center) }
                }
            }
            HStack {
                Button(started ? "Start Again" : "Start", action: start)
                Button("Back One Line") { if !times.isEmpty { times.removeLast() } }
                    .disabled(times.isEmpty)
                Spacer()
                Text("\(times.count) of \(words.count)").foregroundStyle(.secondary).monospacedDigit()
                Button(action: tap) {
                    Text("Tap").frame(width: 90)
                }
                .controlSize(.large)
                .buttonStyle(.borderedProminent)
                .disabled(!started || finished)
            }
            Divider()
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { close() }
                    .keyboardShortcut(.cancelAction)
                Button("Use These Times") {
                    done(LRC.text(of: zip(times, words).map { (time: $0, text: $1) }))
                    close()
                }
                .keyboardShortcut(.defaultAction)
                .disabled(!finished)
            }
        }
        .padding(20)
        .frame(width: 520)
        .onAppear { model.spaceBar = { tap() } }
        .onDisappear { model.spaceBar = nil }
    }

    private func start() {
        times = []
        started = true
        model.player.play([track])
    }

    private func tap() {
        guard started, !finished else { return }
        // A tap comes a moment after the ear hears the line; take a little off.
        times.append(max(0, model.player.exactTime - 0.15))
    }

    private func close() {
        if started, model.player.isPlaying { model.player.toggle() }
        dismiss()
    }
}
