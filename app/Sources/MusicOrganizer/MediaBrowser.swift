import Foundation
import MusicOrganizerKit
import Observation

/// One list from an add-on, as a page shows it: Explore's channels, or Movie Finder's
/// films. It holds what was asked for, what came back, and how the asking is going.
@MainActor
@Observable
final class MediaList {
    private(set) var items: [MediaItem] = []
    private(set) var more = false
    private(set) var working = false
    private(set) var problem: String?
    /// What the items on show were asked with, so the same thing isn't asked twice.
    private(set) var shownFor: String?
    /// Where the next page begins, as the engine said.
    private var cursor = 0
    /// The newest asking, and what it's for: an answer that comes after a later asking
    /// began is left unshown (`Askings`).
    @ObservationIgnored private var askings = Askings()

    /// Back to nothing: another profile is in use now.
    func reset() {
        (items, more, working, problem, shownFor, cursor) = ([], false, false, nil, nil, 0)
        askings = Askings()
    }

    /// A new asking for `wanted`, or nil when there's no need: the same is on its way,
    /// or on show already. Show More (`adding`) waits for whatever is being asked.
    private func begin(_ wanted: String, adding: Bool = false) -> Int? {
        if adding {
            guard !working else { return nil }
        } else if askings.asking == wanted || (!askings.isAsking && wanted == shownFor && problem == nil) {
            return nil
        }
        working = true
        problem = nil
        return askings.begin(wanted)
    }

    /// The asking with this ticket has its answer: true if it's still the one wanted
    /// (the page is then marked as showing it), false if a later one has taken over.
    private func end(_ ticket: Int, showing wanted: String) -> Bool {
        guard askings.isNewest(ticket) else { return false }
        askings.end(ticket)
        shownFor = wanted
        working = false
        return true
    }

    /// Ask for the first page, or with `adding` for the next one.
    func load(
        _ model: AppModel, addon: Addon, catalog: Addon.Catalog, genre: String? = nil,
        search: String = "", also: String = "", adding: Bool = false
    ) async {
        let wanted = "\(addon.id)|\(catalog.type)|\(catalog.id)|\(genre ?? "")|\(search)|\(also)"
        guard let ticket = begin(wanted, adding: adding) else { return }
        if !adding { (items, cursor, more) = ([], 0, false) }
        var asked: [String: Any] = ["addon_id": addon.id, "type": catalog.type, "id": catalog.id]
        if let genre { asked["genre"] = genre }
        if !search.isEmpty { asked["search"] = search }
        if adding { asked["skip"] = cursor }
        // A second genre: only what's tagged with both (the engine does the sifting).
        let both = genre != nil && !also.isEmpty
        if both { asked["also"] = [also] }
        let answer: Result<CatalogAnswer, Error>
        do {
            answer = .success(try await model.ask("addon.catalog", asked, as: CatalogAnswer.self))
        } catch {
            answer = .failure(error)
        }
        guard end(ticket, showing: wanted) else { return }
        switch answer {
        case .success(let found):
            let known = Set(items.map(\.id))
            let fresh = found.items.filter { !known.contains($0.id) }
            items += fresh
            cursor = found.nextSkip ?? items.count
            // A page with nothing new on it means the list has run out, whatever it says.
            // (Not with two genres: a stretch of the list can have nothing with both.)
            more = found.more && (both || !fresh.isEmpty) && catalog.takes("skip")
            if items.isEmpty {
                problem = more
                    ? "Nothing with both genres near the top of the list. Try another pair."
                    : "Nothing was found."
            }
        case .failure(let error):
            problem = error.localizedDescription
        }
    }

    /// Home's Recommended (the owner, 2026-10-15): the popular list of each of the owner's
    /// leading genres, put together closest to their taste first, without what they've
    /// seen or listed (`Recommending`). What's in a list of anime is marked as anime.
    func recommend(
        _ model: AppModel, addon: Addon, catalog: Addon.Catalog, genres: [String], taste: [MediaItem],
        anime: Bool = false
    ) async {
        let wanted = "recommend|\(addon.id)|\(catalog.type)|\(catalog.id)|\(genres.joined(separator: ","))"
        guard let ticket = begin(wanted) else { return }
        var found: [[MediaItem]] = []
        for genre in genres {
            let asked: [String: Any] = ["addon_id": addon.id, "type": catalog.type, "id": catalog.id, "genre": genre]
            if let answer = try? await model.ask("addon.catalog", asked, as: CatalogAnswer.self) {
                found.append(answer.items)
            }
        }
        guard end(ticket, showing: wanted) else { return }
        let ranked = Recommending.rank(found, taste: taste, seen: Set(taste.map(\.id)))
        items = anime ? ranked.map(\.asAnime) : ranked
        (more, cursor) = (false, items.count)
        if items.isEmpty {
            problem = found.isEmpty ? "The plugins couldn't be reached just now." : "Nothing new to suggest just now."
        }
    }

    /// Every plugin asked for some words (the owner, 2026-10-13: a search "searches the
    /// whole database", whichever list is chosen), and what they found put together.
    func search(_ model: AppModel, type: String, words: String) async {
        let wanted = "search|\(type)|\(words)"
        guard let ticket = begin(wanted) else { return }
        (items, cursor, more) = ([], 0, false)
        let answer: Result<MediaSearchAnswer, Error>
        do {
            answer = .success(
                try await model.ask("addon.search", ["type": type, "query": words], as: MediaSearchAnswer.self))
        } catch {
            answer = .failure(error)
        }
        guard end(ticket, showing: wanted) else { return }
        switch answer {
        case .success(let found):
            items = found.items
            if items.isEmpty {
                problem = found.problems.first.map { "\($0.addon): \($0.message)" }
                    ?? "Nothing was found for “\(words)”."
            }
        case .failure(let error):
            problem = error.localizedDescription
        }
    }
}

/// The add-ons, and what's open from them. One for the whole app, so a page is as it
/// was left.
@MainActor
@Observable
final class MediaBrowser {
    private(set) var addons: [Addon] = []
    private(set) var problem: String?
    private(set) var loaded = false
    let channels = MediaList()
    let films = MediaList()
    let series = MediaList()
    let anime = MediaList()
    /// The lists of add-ons for adults only: one Finder's, and nothing else's.
    let adult = MediaList()

    /// Another profile is in use now: its add-ons are read afresh (a child's profile
    /// isn't given the ones for adults only), and nothing the last one had open stays.
    func forget() {
        (addons, problem, loaded) = ([], nil, false)
        for list in [channels, films, series, anime, adult] { list.reset() }
    }

    func load(_ model: AppModel) async {
        guard !loaded else { return }
        do {
            addons = try await model.ask("addon.list", [:], as: AddonsAnswer.self).addons
            problem = addons.isEmpty ? "The lists couldn't be reached. Check the internet connection." : nil
            loaded = !addons.isEmpty
        } catch {
            problem = error.localizedDescription
        }
    }

    /// Change the owner's list of add-ons (add one, take one away, put them in another
    /// order, put the app's own back): the engine answers with the list as it now is.
    /// Returns what went wrong, in the engine's words, or nil.
    func change(_ model: AppModel, _ method: String, _ asked: [String: Any] = [:]) async -> String? {
        do {
            addons = try await model.ask(method, asked, as: AddonsAnswer.self).addons
            (loaded, problem) = (true, nil)
            return nil
        } catch {
            return error.localizedDescription
        }
    }

    /// Every list of one kind ("movie", "channel") that can be opened without typing,
    /// with the add-on it's from.
    /// Never from an add-on for adults only: those lists are kept to their own Finder
    /// (`adultCatalogs`), out of Home and every other page.
    func catalogs(of type: String) -> [(addon: Addon, catalog: Addon.Catalog)] {
        addons.filter { !$0.isAdult }.flatMap { addon in
            addon.catalogs.filter { $0.type == type && $0.canBeBrowsed }.map { (addon, $0) }
        }
    }

    /// Every list, of whatever kind, from the add-ons for adults only.
    func adultCatalogs() -> [(addon: Addon, catalog: Addon.Catalog)] {
        addons.filter(\.isAdult).flatMap { addon in
            addon.catalogs.filter(\.canBeBrowsed).map { (addon, $0) }
        }
    }
}
