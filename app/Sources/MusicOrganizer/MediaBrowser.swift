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

    /// Ask for the first page, or with `adding` for the next one.
    func load(
        _ model: AppModel, addon: Addon, catalog: Addon.Catalog, genre: String? = nil,
        search: String = "", also: String = "", adding: Bool = false
    ) async {
        let wanted = "\(addon.id)|\(catalog.type)|\(catalog.id)|\(genre ?? "")|\(search)|\(also)"
        if working || (!adding && wanted == shownFor && problem == nil) { return }
        working = true
        problem = nil
        if !adding { (items, cursor) = ([], 0) }
        var asked: [String: Any] = ["addon_id": addon.id, "type": catalog.type, "id": catalog.id]
        if let genre { asked["genre"] = genre }
        if !search.isEmpty { asked["search"] = search }
        if adding { asked["skip"] = cursor }
        // A second genre: only what's tagged with both (the engine does the sifting).
        let both = genre != nil && !also.isEmpty
        if both { asked["also"] = [also] }
        do {
            let found = try await model.ask("addon.catalog", asked, as: CatalogAnswer.self)
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
        } catch {
            problem = error.localizedDescription
        }
        shownFor = wanted
        working = false
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
    func catalogs(of type: String) -> [(addon: Addon, catalog: Addon.Catalog)] {
        addons.flatMap { addon in
            addon.catalogs.filter { $0.type == type && $0.canBeBrowsed }.map { (addon, $0) }
        }
    }
}
