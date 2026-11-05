import MusicOrganizerKit
import Observation
import SwiftUI

/// What TMDB said of the titles the pages list (original language, Australian rating),
/// asked of the engine as they're shown (`tmdb.facts`, fifty at a time), and kept while
/// the app is open. A page decides by `TitleRules` what of it to show.
@MainActor
@Observable
final class TitleFilter {
    private(set) var facts: [String: TitleFacts] = [:]
    private(set) var asking: Set<String> = []
    /// Why the last answers didn't come, for the pages.
    private(set) var trouble: String?
    /// Which of a title's facts have been asked for: a fact not asked isn't known. Watched
    /// like the facts, so a page waiting on an answer is drawn again when it comes.
    private var asked: [String: Set<String>] = [:]

    static let together = 50

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

    /// Ask TMDB, through the engine, what these rules need of these titles and isn't known.
    func look(_ items: [MediaItem], _ rules: TitleRules, languageToo: Bool, model: AppModel) {
        let wanted = rules.wanted(languageToo: languageToo)
        guard !wanted.isEmpty else { return }
        var seen = Set<String>()
        let needed = items.filter {
            seen.insert($0.id).inserted && !asking.contains($0.id) && !(asked[$0.id] ?? []).isSuperset(of: wanted)
        }
        guard !needed.isEmpty else { return }
        asking.formUnion(needed.map(\.id))
        Task {
            for start in stride(from: 0, to: needed.count, by: Self.together) {
                let part = Array(needed[start..<min(start + Self.together, needed.count)])
                // What was asked of them before, with what's wanted now, so an answer is whole.
                let fields = part.reduce(Set(wanted)) { $0.union(asked[$1.id] ?? []) }
                do {
                    let answer = try await model.ask(
                        "tmdb.facts",
                        ["titles": part.map { ["id": $0.id, "type": $0.type] }, "want": Array(fields)],
                        as: FactsAnswer.self)
                    for said in answer.facts { facts[said.id] = said }
                    trouble = nil
                } catch {
                    // Not known: left out, as an unknown always is. Asked again next time
                    // the app opens.
                    trouble = error.localizedDescription
                }
                for item in part {
                    asked[item.id, default: []].formUnion(fields)
                    asking.remove(item.id)
                }
            }
        }
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
                                WebPicture(address: person.photo, symbol: "person.fill")
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
                    WebPicture(address: person?.photo ?? cast.photo, symbol: "person.fill")
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
                    "With your own key from themoviedb.org (Settings → API), Movie Finder and Series Finder offer "
                        + "TMDB's Popular, Top Rated and Upcoming, a film's page shows its cast with their photos "
                        + "and its budget and revenue, the calendar has a General view of what's coming out, and "
                        + "a child's profile can be kept to some Australian ratings. The key is kept on this Mac "
                        + "for every profile, and only a title's or a person's id is sent with it. "
                        + TMDB.attribution)
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
