import MusicOrganizerKit
import SwiftUI

/// Discover's guided mode: three questions instead of the Find page's boxes.
///
///     What music would you like today?            Hip hop
///     Would you like to just play it, or download it?   Download it
///     How many songs?                             250
///
/// It adds nothing of its own: it asks the engine for the same picks Find does, which
/// land on the Find page. "Just play it" plays them from YouTube Music and saves
/// nothing. "Download it" shows the plan first (how many, how long) and queues nothing
/// until Start is clicked.
struct GuideSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss

    private enum Step {
        case what, playOrDownload, howMany, finding
        case confirm(AppModel.BatchDownload)
        case failed(String)
    }

    @State private var step = Step.what
    @State private var typed = ""
    /// What was asked for, and how it reads back as the owner's answer.
    @State private var wish: (seed: DiscoverSeed, words: String)?
    @State private var download = false
    @State private var count = 50
    @State private var otherCount = ""
    @FocusState private var typing: Bool

    private static let ideas = ["Hip hop", "R&B", "Pop", "Rock", "Indie", "Electronic"]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            question("What music would you like today?")
            if let wish {
                answer(wish.words)
            } else {
                askWhat
            }
            if wish != nil {
                question("Would you like to just play it, or download it?")
                if case .playOrDownload = step {
                    HStack(spacing: 10) {
                        Button("Just Play It", systemImage: "play.fill") { chose(download: false) }
                        Button("Download It", systemImage: "arrow.down.circle") {
                            chose(download: true)
                        }
                    }
                    .controlSize(.large)
                } else {
                    answer(download ? "Download it" : "Just play it")
                }
            }
            if wish != nil, !isAt(.playOrDownload) {
                question("How many songs?")
                if case .howMany = step {
                    askHowMany
                } else {
                    answer("\(count)")
                }
            }
            last
            Spacer(minLength: 4)
            Divider()
            HStack {
                if wish != nil, !isAt(.finding) {
                    Button("Start Over", action: startOver)
                }
                Spacer()
                Button("Close", role: .cancel) { dismiss() }
                    .keyboardShortcut(.cancelAction)
            }
        }
        .padding(20)
        .frame(width: 500)
        .frame(minHeight: 360, alignment: .top)
        .onAppear { typing = true }
    }

    // MARK: the questions

    private var askWhat: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                TextField("A kind of music or an artist: hip hop, Linkin Park…", text: $typed)
                    .textFieldStyle(.roundedBorder)
                    .focused($typing)
                    .onSubmit(takeTyped)
                Button("Next", action: takeTyped)
                    .keyboardShortcut(.defaultAction)
                    .disabled(typed.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            HStack(spacing: 6) {
                Button("Surprise Me") { wanted(.library, "Surprise me") }
                    .help("Songs like the ones in your library")
                Button("What I Play Most") { wanted(.mostPlayed, "Like what I play most") }
                ForEach(Self.ideas, id: \.self) { name in
                    Button(name) { wanted(.genre(name), name) }
                }
            }
            .controlSize(.small)
        }
    }

    private var askHowMany: some View {
        HStack(spacing: 6) {
            ForEach(Guided.counts, id: \.self) { number in
                Button("\(number)") { find(number) }
            }
            TextField("Other", text: $otherCount)
                .textFieldStyle(.roundedBorder)
                .frame(width: 70)
                .onSubmit { if let number = Guided.count(from: otherCount) { find(number) } }
                .help("Any number from 1 to \(Guided.most)")
        }
    }

    /// What follows the three answers: the finding, then the plan or what went wrong.
    @ViewBuilder
    private var last: some View {
        let page = model.find
        switch step {
        case .finding:
            VStack(alignment: .leading, spacing: 8) {
                question("Finding \(count) \(count == 1 ? "song" : "songs") for you…")
                if page.of > 0 {
                    ProgressView(value: Double(page.done), total: Double(page.of))
                        .frame(width: 260)
                } else {
                    ProgressView().controlSize(.small)
                }
                Text("The music service is asked one radio at a time, a few seconds each.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
        case .confirm(let batch):
            VStack(alignment: .leading, spacing: 10) {
                question(
                    "I found \(Guided.what(batch.count, from: page.seeds)). "
                        + Guided.downloadNote(minutes: batch.minutes, days: batch.days))
                if let note = page.note {
                    Text(note).font(.callout).foregroundStyle(.secondary)
                }
                HStack(spacing: 10) {
                    Button("Start") {
                        model.start(batch)
                        dismiss()
                    }
                    .keyboardShortcut(.defaultAction)
                    Text("They'll show up under Discover → Downloads as they arrive.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
                .controlSize(.large)
            }
        case .failed(let why):
            question(why)
        default:
            EmptyView()
        }
    }

    // MARK: the answers

    private func takeTyped() {
        let words = typed.trimmingCharacters(in: .whitespaces)
        if !words.isEmpty { wanted(.typed(words), words) }
    }

    private func wanted(_ seed: DiscoverSeed, _ words: String) {
        wish = (seed, words)
        step = .playOrDownload
    }

    private func chose(download: Bool) {
        self.download = download
        step = .howMany
    }

    private func startOver() {
        wish = nil
        typed = ""
        otherCount = ""
        step = .what
        typing = true
    }

    /// The third answer is in: ask the engine, then play the picks or plan the download.
    private func find(_ number: Int) {
        guard let wish else { return }
        count = number
        step = .finding
        let page = model.find
        Task {
            guard await page.run([wish.seed], count: number) else {
                step = .failed(page.problem ?? "Find is busy with another search. Try again in a moment.")
                return
            }
            let picks = page.picks
            guard !picks.isEmpty else {
                step = .failed(page.note ?? "Nothing new was found for that.")
                return
            }
            if download {
                do {
                    step = .confirm(try await model.plan(downloading: picks))
                } catch {
                    step = .failed(error.localizedDescription)
                }
            } else {
                // A play queue for now: nothing is saved. The picks stay on the Find page.
                model.player.play(picks.map(\.result.track), startAt: 0)
                dismiss()
            }
        }
    }

    private func isAt(_ wanted: Step) -> Bool {
        switch (step, wanted) {
        case (.what, .what), (.playOrDownload, .playOrDownload), (.howMany, .howMany),
            (.finding, .finding):
            true
        default: false
        }
    }

    // MARK: how it looks

    private func question(_ text: String) -> some View {
        Text(text)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(.quaternary, in: RoundedRectangle(cornerRadius: 12))
            .frame(maxWidth: .infinity, alignment: .leading)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func answer(_ text: String) -> some View {
        Text(text)
            .foregroundStyle(.white)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(Color.accentColor, in: RoundedRectangle(cornerRadius: 12))
            .frame(maxWidth: .infinity, alignment: .trailing)
    }
}
