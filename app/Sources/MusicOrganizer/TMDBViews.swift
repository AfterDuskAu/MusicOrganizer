import MusicOrganizerKit
import Observation
import SwiftUI

/// What TMDB said of the titles the pages list (original language, Australian rating),
/// asked of the engine as they're shown (`tmdb.facts`), and kept while the app is open. A page
/// decides by `TitleRules` what of it to show.
///
/// Asked steadily since 2026-11-09 (as the tablet app since 2026-11-07): `together` questions
/// out at once, a title each, each taken up as soon as another is answered (`FactsQueue`), and
/// what's answered shown a few times a second. Before, the titles went in tens (fifty before
/// 2026-11-07), each ten waiting for its slowest, and a question that failed hid its title as
/// "not rated" until the app was opened again: a profile being changed restarts the engine, and
/// a child's lists were nearly empty after it (the owner: "it may record them, but they dont
/// show up"). Now a question that fails is asked again. A page asks its plugin for more while
/// few are still waiting (`waitingCount`, `lookingAhead`). The engine asks TMDB eight at a time
/// over connections kept open, and keeps what it said in its cache folder (CLAUDE.md, `tmdb`).
@MainActor
@Observable
final class TitleFilter {
    private(set) var facts: [String: TitleFacts] = [:]
    /// Why the last answers didn't come, for the pages.
    private(set) var trouble: String?
    /// Which of a title's facts have been asked for: a fact not asked isn't known. Watched
    /// like the facts, so a page waiting on an answer is drawn again when it comes.
    private var asked: [String: Set<String>] = [:]

    @ObservationIgnored private var line = FactsQueue()
    @ObservationIgnored private var askers = 0
    /// Answers come, to be shown together (`show`): each one shown alone redrew a page of
    /// cards thirty times a second.
    @ObservationIgnored private var heard: [(said: TitleFacts?, ask: FactsQueue.Ask)] = []
    @ObservationIgnored private var showing = false
    @ObservationIgnored private weak var model: AppModel?

    /// Questions out at once: the engine asks TMDB `TOGETHER` (eight) at a time.
    static let together = 8
    /// Fewer than this many of a list's titles waiting, and the list asks for its next page.
    static let lookingAhead = 40

    /// What's known of a title for these rules, or nil while it hasn't been asked.
    private func known(_ id: String, _ wanted: [String]) -> TitleFacts? {
        guard (asked[id] ?? []).isSuperset(of: wanted) else { return nil }
        return facts[id] ?? TitleFacts(id: id, language: nil, rating: nil)
    }

    func decide(_ item: MediaItem, _ rules: TitleRules, languageToo: Bool) -> TitleRules.Decision {
        rules.decide(known(item.id, rules.wanted(languageToo: languageToo)), languageToo: languageToo)
    }

    func shows(_ item: MediaItem, _ rules: TitleRules, languageToo: Bool) -> Bool {
        decide(item, rules, languageToo: languageToo) == .show
    }

    func isWaiting(_ items: [MediaItem], _ rules: TitleRules, languageToo: Bool) -> Bool {
        items.contains { decide($0, rules, languageToo: languageToo) == .waiting }
    }

    /// How many of these are still waiting for TMDB's word.
    func waitingCount(_ items: [MediaItem], _ rules: TitleRules, languageToo: Bool) -> Int {
        items.reduce(0) { $0 + (decide($1, rules, languageToo: languageToo) == .waiting ? 1 : 0) }
    }

    /// Ask TMDB, through the engine, what these rules need of these titles and isn't known:
    /// put in line, in the list's order, and asked steadily.
    func look(_ items: [MediaItem], _ rules: TitleRules, languageToo: Bool, model: AppModel) {
        let wanted = Set(rules.wanted(languageToo: languageToo))
        guard !wanted.isEmpty else { return }
        self.model = model
        for item in items where !(asked[item.id] ?? []).isSuperset(of: wanted) {
            // What was asked of it before too, so an answer is whole.
            line.add(item.id, type: item.type, fields: wanted.union(asked[item.id] ?? []))
        }
        while askers < Self.together, line.hasWaiting {
            askers += 1
            Task { await ask() }
        }
    }

    /// One of the questions out at once: the next title in line, until there's none.
    private func ask() async {
        defer { askers -= 1 }
        while let one = line.next() {
            guard let model else {
                _ = line.failed(one, counts: false)
                return
            }
            do {
                let answer = try await model.ask(
                    "tmdb.facts",
                    ["titles": [["id": one.id, "type": one.type]], "want": Array(one.fields)],
                    as: FactsAnswer.self)
                if trouble != nil { trouble = nil }
                if let said = answer.facts.first(where: { $0.id == one.id }) {
                    line.answered(one)
                    hear(said, one)
                } else {
                    // TMDB couldn't be asked about it just now: again in a while.
                    if !line.failed(one, counts: true) { hear(nil, one) }
                    try? await Task.sleep(for: .seconds(3))
                }
            } catch {
                let words = error.localizedDescription
                if trouble != words { trouble = words }
                // The engine stopped or isn't there yet (a profile being changed): its turn
                // again once it's back, counted against nothing. An error the engine gave
                // (the key turned away) counts.
                let away = (error as? RPCError)?.code == RPCError.closed
                if !line.failed(one, counts: !away) { hear(nil, one) }
                try? await Task.sleep(for: .seconds(away ? 2 : 5))
            }
        }
    }

    /// An answer, or a title given up on (nil: not known, so not shown), shown with the others
    /// that come in the next moment.
    private func hear(_ said: TitleFacts?, _ ask: FactsQueue.Ask) {
        heard.append((said, ask))
        guard !showing else { return }
        showing = true
        Task {
            try? await Task.sleep(for: .milliseconds(200))
            show()
        }
    }

    private func show() {
        for (said, ask) in heard {
            if let said { facts[ask.id] = FactsQueue.merged(facts[ask.id], said, fields: ask.fields) }
            asked[ask.id, default: []].formUnion(ask.fields)
        }
        heard.removeAll()
        showing = false
    }
}

/// A title's cast from TMDB, a photo each, in a row that slides sideways (the owner,
/// 2026-11-05): a click opens that person's page.
struct CastRow: View {
    let cast: [TMDBCast]
    let open: (TMDBCast) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("CAST").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(alignment: .top, spacing: 14) {
                    ForEach(cast) { person in
                        Button { open(person) } label: {
                            VStack(spacing: 6) {
                                WebPicture(
                                    address: person.photo, symbol: "person.fill", size: CGSize(width: 84, height: 84))
                                    .frame(width: 84, height: 84)
                                    .clipShape(Circle())
                                    .overlay(Circle().strokeBorder(.white.opacity(0.25)))
                                Text(person.name).font(.callout.weight(.medium)).lineLimit(2)
                                if let character = person.character {
                                    Text(character).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                                }
                            }
                            .multilineTextAlignment(.center)
                            .frame(width: 100)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .help("\(person.name): every film and series they're in")
                    }
                }
                .padding(.vertical, 2)
            }
        }
    }
}

/// Someone from a title's cast (the owner, 2026-11-05): their photo and biography, and every
/// film and series they're in, from TMDB. A click on one opens its page (by its IMDb id). In
/// a child's profile kept to some ratings, only those rated so are shown.
struct PersonView: View {
    let cast: TMDBCast
    let back: () -> Void
    @Environment(AppModel.self) private var model
    @State private var person: TMDBPerson?
    @State private var problem: String?
    @State private var opening: String?

    var body: some View {
        let rules = model.titleRules(language: OriginalLanguage.all)
        let titles = model.titles
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                BackButton(help: "Back to the film", action: back)
                HStack(alignment: .top, spacing: 26) {
                    WebPicture(
                        address: person?.photo ?? cast.photo, symbol: "person.fill",
                        size: CGSize(width: 180, height: 270))
                        .frame(width: 180, height: 270)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                    VStack(alignment: .leading, spacing: 10) {
                        Text(person?.name ?? cast.name).font(.system(size: 34, weight: .bold)).heading()
                        if let character = cast.character { Text("As \(character)").foregroundStyle(.secondary) }
                        if let biography = person?.biography {
                            Text(biography).frame(maxWidth: 700, alignment: .leading).textSelection(.enabled)
                        }
                    }
                }
                if let problem { Text(problem).foregroundStyle(.secondary) }
                if let person {
                    let items = person.credits.map(\.item)
                    let shown = person.credits.filter { titles.shows($0.item, rules, languageToo: false) }
                    Text("Films and Series").font(.title3.weight(.semibold)).heading()
                    if titles.isWaiting(items, rules, languageToo: false) {
                        ProgressView().controlSize(.small)
                    } else if shown.isEmpty {
                        Text(
                            person.credits.isEmpty
                                ? "TMDB lists nothing they're in."
                                : "Nothing they're in is rated for this profile.")
                            .foregroundStyle(.secondary)
                    }
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 130), spacing: 20)], spacing: 20) {
                        ForEach(shown) { credit in
                            Button { open(credit) } label: {
                                VStack(spacing: 2) {
                                    MediaCard(item: credit.item)
                                        .overlay { if opening == credit.id { ProgressView() } }
                                    Text([credit.year, credit.character].compactMap { $0 }.joined(separator: " · "))
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                        .lineLimit(1)
                                }
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }
            .padding(32)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        // Over the film's own page, which is drawn on black.
        .background { Color.black }
        .task(id: cast.personId) {
            do {
                let found = try await model.ask("tmdb.person", ["person_id": cast.personId], as: TMDBPerson.self)
                person = found
                titles.look(found.credits.map(\.item), rules, languageToo: false, model: model)
            } catch {
                problem = error.localizedDescription
            }
        }
    }

    private func open(_ credit: TMDBCredit) {
        guard opening == nil else { return }
        opening = credit.id
        Task {
            await model.openFromTMDB(credit.item)
            opening = nil
        }
    }
}

/// Settings → Plugins → TMDB (2026-11-05): the owner's own key, and the Finders' original
/// language. The key is the computer's; once saved it's never shown again.
struct TMDBSettings: View {
    @Environment(AppModel.self) private var model
    @AppStorage(OriginalLanguage.key) private var originalLanguage = OriginalLanguage.all
    @State private var typed = ""
    @State private var working = false
    @State private var problem: String?

    var body: some View {
        Form {
            Section("Your TMDB Key") {
                if model.tmdbHasKey {
                    HStack {
                        Label("A key is saved on this Mac", systemImage: "checkmark.seal.fill")
                        Spacer()
                        Button("Remove the Key", role: .destructive) {
                            Task { await model.forgetTMDB() }
                        }
                    }
                } else {
                    HStack {
                        SecureField("Key", text: $typed, prompt: Text("API Read Access Token or API Key"))
                            .labelsHidden()
                            .onSubmit { save() }
                        if working {
                            ProgressView().controlSize(.small)
                        } else {
                            Button("Save") { save() }
                                .disabled(typed.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                        }
                    }
                }
                if let problem {
                    Text(problem).foregroundStyle(.orange).frame(maxWidth: .infinity, alignment: .leading)
                }
                SideNote(
                    "With your own key from themoviedb.org (Settings → API), TMDB is the first plugin: Movie Finder "
                        + "and Series Finder offer its Popular, Top Rated, Upcoming and Trending first, by its genres "
                        + "(two at once in one question), a title's page is TMDB's (the plugins fill in what it "
                        + "hasn't, and a series' episodes and every way to play are theirs), a search shows what TMDB "
                        + "finds first, a film's page shows its cast with their photos and its budget and revenue, "
                        + "the calendar has a General view of what's coming out, and a child's profile can be kept "
                        + "to some Australian ratings. The key is kept on this Mac for every profile, and only a "
                        + "title's or a person's id, a list's genres and language, or a search's words are sent "
                        + "with it. " + TMDB.attribution)
            }
            Section("Original Language") {
                Picker("Show", selection: $originalLanguage) {
                    ForEach(OriginalLanguage.offered, id: \.self) { code in
                        Text(code == OriginalLanguage.all ? "All" : "Only \(OriginalLanguage.name(code))").tag(code)
                    }
                }
                .disabled(!model.tmdbHasKey)
                SideNote(
                    "Movie Finder's and Series Finder's lists and searches by the language a title was first made "
                        + "in, as TMDB has it: with English, a Chinese film dubbed in English is left out and an "
                        + "English film with some Spanish in it stays. A title TMDB doesn't know is left out too. "
                        + "All leaves everything in. Needs your TMDB key.")
            }
        }
        .formStyle(.grouped)
        .scrollContentBackground(Theme.current.listBackground)
    }

    private func save() {
        let key = typed
        guard !key.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, !working else { return }
        (working, problem) = (true, nil)
        Task {
            problem = await model.connectTMDB(key)
            if problem == nil { typed = "" }
            working = false
        }
    }
}
