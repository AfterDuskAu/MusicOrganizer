import MusicOrganizerKit
import SwiftUI

/// Settings → Profile → Paste a List (the owner, 2026-11-13, of the iPad: "copy/paste titles
/// directly into a 'card'. On this card it would read the movies/series listed on it, and mark
/// them as already watched. And another Card under it, for watchlisted shows. paste 50 shows you
/// have in notes, and it detects and adds them to the correct list"). Two cards, one under the
/// other: **Mark as Watched** and **Add to Watchlist**.
///
/// A line is tidied into a name (`PastedList.lines`: bullets, numbers, ticks, a season, a year, a
/// heading), looked for with TMDB first and then every plugin, as a Finder's search is, and what's
/// found is shown **before** anything is added: the poster, the name, the year and whether it's a
/// film or a series. A line that could be more than one ("Law & Order") shows its options, and a
/// click ticks or unticks each; several may be ticked for one line (the owner: "you can select
/// several or just 1, 1 tap and it deselects or selects"). Only what's ticked is added, it only
/// ever adds, and what it adds goes to the paired devices with the rest of the profile's watching.
///
/// A child's profile is shown only what its Australian ratings allow, as its pages are, and
/// nothing from a plugin for adults only is ever searched (`addon.search` never asks those).
struct PasteListSettings: View {
    var body: some View {
        Form {
            PasteListCard(adds: .watched)
            PasteListCard(adds: .watchlist)
        }
        .formStyle(.grouped)
        .scrollContentBackground(Theme.current.listBackground)
    }
}

/// One of the two cards.
private struct PasteListCard: View {
    enum Adds {
        case watched, watchlist

        var title: String { self == .watched ? "Mark as Watched" : "Add to Watchlist" }
        var prompt: String {
            self == .watched
                ? "Paste what you've already watched, one on each line"
                : "Paste what you mean to watch, one on each line"
        }
        func button(_ count: Int) -> String {
            self == .watched ? "Mark \(count) as Watched" : "Add \(count) to the Watchlist"
        }
    }

    let adds: Adds
    @Environment(AppModel.self) private var model
    @State private var job = PasteJob()

    var body: some View {
        Section(adds.title) {
            box
            if job.working || job.adding {
                HStack(spacing: 12) {
                    ProgressView().controlSize(.small)
                    Text(job.adding ? "Adding… \(job.done) of \(job.ofLines)" : "Looking… \(job.done) of \(job.ofLines)")
                        .foregroundStyle(.secondary)
                    Spacer(minLength: 8)
                    if !job.adding { Button("Stop") { job.stop() } }
                }
            } else {
                HStack {
                    Button("Find These") { job.find(with: model, kind: adds) }
                        .disabled(job.typed.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    Spacer(minLength: 8)
                    if !job.readings.isEmpty { Button("Clear") { job.clear() } }
                }
            }
            if let problem = job.problem {
                Text(problem).foregroundStyle(.red).font(.callout).fixedSize(horizontal: false, vertical: true)
            }
            if !job.readings.isEmpty {
                // What was found, and the button to add it, above the lines: with fifty pasted
                // the owner shouldn't have to scroll past every one of them to add them.
                Text(PastedList.said(job.readings, choosing: "click"))
                    .font(.callout.weight(.medium))
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                let picked = PastedList.picked(job.readings).count
                Button(adds.button(picked)) { job.add(with: model, kind: adds) }
                    .buttonStyle(.borderedProminent)
                    .disabled(picked == 0 || job.adding)
                ForEach(job.shown) { reading in
                    PastedLineRow(reading: reading) { job.tap($0, in: reading) }
                }
            }
            SideNote(footer)
        }
    }

    /// The box pasted into. A prompt is laid over it: macOS's TextEditor has none of its own.
    private var box: some View {
        ZStack(alignment: .topLeading) {
            TextEditor(text: $job.typed)
                // Tall enough to see what was pasted, never so tall that it fills the page.
                .frame(minHeight: 108, maxHeight: 240)
                .font(.body)
                .scrollContentBackground(.hidden)
                .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 6))
            if job.typed.isEmpty {
                Text(adds.prompt)
                    .foregroundStyle(.tertiary)
                    .padding(.top, 8)
                    .padding(.leading, 6)
                    .allowsHitTesting(false)
            }
        }
    }

    private var footer: String {
        (adds == .watched
            ? "Each line is looked up (TMDB first when you have a key, then every plugin) and shown with its poster "
                + "before anything is marked. A film is marked watched; a series has every episode of it that's out "
                + "marked, as its card's Mark as Watched does. "
            : "Each line is looked up (TMDB first when you have a key, then every plugin) and shown with its poster "
                + "before anything is added. ")
            + "Bullets, numbers, ticks, a year and a season (“S2”, “Season 1-3”) are ignored, and a heading like "
            + "“TV Shows:” says what's under it. Where more than one could be meant, click the right one — or several. "
            + "Nothing is ever taken off a list, and what's added goes to your paired devices at their next sync."
    }
}

/// One pasted line: what was typed, and under it the titles found for it, each a poster to click.
private struct PastedLineRow: View {
    let reading: PastedList.Reading
    let tap: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Image(systemName: mark.symbol).foregroundStyle(mark.colour)
                Text(reading.line.text).fontWeight(.medium).lineLimit(1)
                Spacer(minLength: 8)
                if !reading.picked.isEmpty, !reading.needsALook {
                    Text(reading.picked.count == 1 ? "1 picked" : "\(reading.picked.count) picked")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            if let why = reading.why {
                Text(why).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            if reading.foundNothing {
                Text("Nothing was found by this name. Change the line and find them again.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(alignment: .top, spacing: 10) {
                        ForEach(reading.options, id: \.id) { item in
                            PastedOption(item: item, ticked: reading.chosen.contains(item.id)) { tap(item.id) }
                        }
                    }
                    .padding(.vertical, 2)
                }
            }
        }
        .padding(.vertical, 4)
    }

    private var mark: (symbol: String, colour: Color) {
        if reading.foundNothing { return ("questionmark.circle", .secondary) }
        if reading.picked.isEmpty { return ("circle", .secondary) }
        return reading.needsALook ? ("checkmark.circle.fill", Color.accentColor) : ("checkmark.circle.fill", .green)
    }
}

/// One title offered for a line: its poster, name and year, ticked or not. A click ticks it,
/// another unticks it.
private struct PastedOption: View {
    let item: MediaItem
    let ticked: Bool
    let tap: () -> Void

    private static let wide: CGFloat = 78

    var body: some View {
        Button(action: tap) {
            VStack(spacing: 5) {
                Color.clear
                    .frame(width: Self.wide, height: Self.wide * 3 / 2)
                    .overlay {
                        WebPicture(
                            address: item.poster, size: CGSize(width: Self.wide, height: Self.wide * 3 / 2))
                    }
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    .overlay {
                        RoundedRectangle(cornerRadius: 8)
                            .strokeBorder(
                                ticked ? AnyShapeStyle(Color.accentColor) : AnyShapeStyle(.quaternary),
                                lineWidth: ticked ? 3 : 1)
                    }
                    .overlay(alignment: .topTrailing) {
                        if ticked {
                            Image(systemName: "checkmark.circle.fill")
                                .symbolRenderingMode(.palette)
                                .foregroundStyle(.white, Color.accentColor)
                                .padding(4)
                        }
                    }
                Text(item.name)
                    .font(.caption)
                    .lineLimit(2)
                    .multilineTextAlignment(.center)
                    .foregroundStyle(ticked ? AnyShapeStyle(.primary) : AnyShapeStyle(.secondary))
                Text([item.year, item.type == "series" ? "Series" : "Film"].compactMap { $0 }.joined(separator: " · "))
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
            .frame(width: 92)
        }
        .buttonStyle(.plain)
        .help(ticked ? "Click to leave “\(item.name)” out" : "Click to include “\(item.name)”")
        .accessibilityLabel("\(item.name), \(item.year ?? "")")
        .accessibilityAddTraits(ticked ? [.isSelected, .isButton] : .isButton)
    }
}

/// One card's work: the pasted text, the looking (a few lines at a time, so fifty never hold the
/// app up), what was found, and the adding.
@MainActor
@Observable
final class PasteJob {
    var typed = ""
    private(set) var readings: [PastedList.Reading] = []
    private(set) var working = false
    private(set) var adding = false
    private(set) var done = 0
    private(set) var ofLines = 0
    private(set) var problem: String?
    @ObservationIgnored private var task: Task<Void, Never>?

    /// How many lines are looked for at once.
    static let together = 4

    /// The lines as the card shows them: those wanting a look first, each in the order pasted.
    var shown: [PastedList.Reading] {
        readings.filter(\.needsALook) + readings.filter { !$0.needsALook }
    }

    fileprivate func find(with model: AppModel, kind: PasteListCard.Adds) {
        let lines = PastedList.lines(typed)
        guard !lines.isEmpty else {
            problem = "Paste a list of titles first, one on each line."
            return
        }
        task?.cancel()
        (problem, readings, done, ofLines, working) = (nil, [], 0, lines.count, true)
        task = Task {
            var made: [PastedList.Reading] = []
            var waiting = lines[...]
            while !waiting.isEmpty, !Task.isCancelled {
                let batch = Array(waiting.prefix(Self.together))
                waiting = waiting.dropFirst(batch.count)
                let found = await withTaskGroup(of: (Int, PastedList.Reading).self) { group in
                    for line in batch {
                        group.addTask { @MainActor in (line.id, await Self.look(line, with: model)) }
                    }
                    var some: [(Int, PastedList.Reading)] = []
                    for await one in group { some.append(one) }
                    return some.sorted { $0.0 < $1.0 }.map(\.1)
                }
                guard !Task.isCancelled else { break }
                made += found
                (readings, done) = (made, made.count)
            }
            working = false
            if readings.isEmpty, problem == nil, !Task.isCancelled {
                problem = "Nothing was found. Check the names, and that the plugins can be reached."
            }
        }
    }

    func stop() {
        task?.cancel()
        working = false
    }

    func clear() {
        task?.cancel()
        (typed, readings, problem, working, done, ofLines) = ("", [], nil, false, 0, 0)
    }

    /// A click on one of a line's options (the owner: "1 tap and it deselects or selects").
    func tap(_ id: String, in reading: PastedList.Reading) {
        guard let at = readings.firstIndex(where: { $0.id == reading.id }) else { return }
        readings[at] = PastedList.tapped(id, in: readings[at])
    }

    /// Everything ticked, marked watched or put on the Watchlist.
    fileprivate func add(with model: AppModel, kind: PasteListCard.Adds) {
        let items = PastedList.picked(readings)
        guard !items.isEmpty, !adding else { return }
        task?.cancel()
        (adding, done, ofLines) = (true, 0, items.count)
        task = Task {
            switch kind {
            case .watchlist:
                let added = model.addToWatchlist(pasted: items)
                done = items.count
                model.notice = added == items.count
                    ? "\(added) added to your Watchlist."
                    : "\(added) added to your Watchlist; \(items.count - added) were on it already."
            case .watched:
                let missed = await model.markWatched(pasted: items) { [weak self] through in self?.done = through }
                let marked = items.count - missed.count
                model.notice = missed.isEmpty
                    ? "Marked \(marked) as watched."
                    : "Marked \(marked) as watched. These couldn't be marked, because their episodes couldn't be "
                        + "read: " + missed.prefix(3).joined(separator: ", ") + (missed.count > 3 ? "…" : "")
            }
            adding = false
            clear()
        }
    }

    // MARK: looking one line up

    /// One line looked for: TMDB first (when the computer has a key), then every plugin, as a
    /// Finder's search is, of the kind the line says or of both. In a child's profile only what
    /// its ratings allow is offered, as everywhere else.
    private static func look(_ line: PastedList.Line, with model: AppModel) async -> PastedList.Reading {
        var found = await search(line.name, line: line, model: model)
        // Nothing of that name: the line's number may be part of it ("Wonder Woman 1984").
        if let whole = line.whole, PastedList.match(line, among: found).sureness != .sure {
            found = PastedList.together(
                tmdb: found, plugins: (await search(whole, line: line, model: model)).map(\.item),
                hasId: { $0.hasPrefix("tt") })
        }
        if model.titleRules(language: OriginalLanguage.all).ratings != nil {
            found = await allowed(found, model: model)
        }
        return PastedList.reading(line, among: found)
    }

    /// Some words asked of TMDB and of every plugin, for the kinds the line may be.
    private static func search(
        _ words: String, line: PastedList.Line, model: AppModel
    ) async -> [PastedList.Found] {
        let kinds = line.kind.map { [$0] } ?? ["movie", "series"]
        var all: [PastedList.Found] = []
        for kind in kinds {
            let asksTMDB = model.tmdbHasKey
            async let fromTMDB: TMDBFound? = asksTMDB
                ? try? await model.ask("tmdb.search", ["type": kind, "query": words], as: TMDBFound.self)
                : nil
            let theirs =
                (try? await model.ask(
                    "addon.search", ["type": kind, "query": words], as: MediaSearchAnswer.self))?.items ?? []
            let mine = await fromTMDB
            let talked = mine?.popularity ?? [:]
            let tmdb = (mine?.items ?? []).enumerated().map {
                PastedList.Found($1, popularity: talked[$1.id], place: $0)
            }
            all += PastedList.together(tmdb: tmdb, plugins: theirs, hasId: { $0.hasPrefix("tt") })
        }
        var seen = Set<String>()
        return all.filter { seen.insert($0.item.id).inserted }
    }

    /// A child's profile: only what its Australian ratings allow, asked of TMDB through the
    /// engine as a page's list does (`TitleFilter`). A title TMDB says nothing of is left out.
    private static func allowed(_ found: [PastedList.Found], model: AppModel) async -> [PastedList.Found] {
        let rules = model.titleRules(language: OriginalLanguage.all)
        let items = found.map(\.item)
        model.titles.look(items, rules, languageToo: false, model: model)
        for _ in 0..<40 where model.titles.isWaiting(items, rules, languageToo: false) {
            try? await Task.sleep(for: .milliseconds(150))
        }
        return found.filter { model.titles.shows($0.item, rules, languageToo: false) }
    }
}
