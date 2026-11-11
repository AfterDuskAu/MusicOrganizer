/// The pages opened over each page of the app, in the order they were opened, so that Back
/// goes to the page that was showing before (the owner, 2026-11-11: "i want all apps to have
/// a structure that pressing back takes it to its last page ... Watchlist>Movie>(back)>
/// Watchlist, in the same spot it was at"). Until then a film or a series opened from the
/// Watchlist, the Calendar or Home was opened in its Finder, and Back left it there.
///
/// Each page of the sidebar (`Base`) has a trail of its own: what's opened from it goes on
/// top of it, Back takes the top one away, and the page under it is there again as it was
/// left (the app keeps every page in the trail, so it's scrolled to the same place). Pages
/// are told apart by their `id`: the same title asked for again while it's on top (the
/// film player's ✕ back to its series) isn't opened twice.
public struct BackTrail<Base: Hashable, Page: Identifiable & Hashable> {
    private var trails: [Base: [Page]] = [:]

    public init() {}

    /// What's open over a page, the first opened first.
    public func pages(on base: Base) -> [Page] { trails[base] ?? [] }

    /// The page showing over a page, if any.
    public func top(on base: Base) -> Page? { trails[base]?.last }

    /// Whether anything is open over a page.
    public func isCovered(_ base: Base) -> Bool { !(trails[base] ?? []).isEmpty }

    /// A page opened over `base`, on top of what's open there already. The one on top
    /// already, asked for again, stays as it is (it isn't opened a second time).
    public mutating func open(_ page: Page, on base: Base) {
        if trails[base]?.last?.id == page.id { return }
        trails[base, default: []].append(page)
    }

    /// Back: the page on top taken away, and the one under it showing again. False when
    /// nothing was open over `base`.
    @discardableResult
    public mutating func back(on base: Base) -> Bool {
        guard trails[base]?.popLast() != nil else { return false }
        if trails[base]?.isEmpty == true { trails[base] = nil }
        return true
    }

    /// Everything open over a page taken away: its row clicked in the sidebar.
    public mutating func clear(_ base: Base) { trails[base] = nil }

    /// Every page's trail taken away: another profile is in use.
    public mutating func clearAll() { trails = [:] }

    /// Pages that can't be shown any more (a playlist deleted) taken out of every trail.
    public mutating func removeAll(where gone: (Page) -> Bool) {
        for (base, pages) in trails {
            let kept = pages.filter { !gone($0) }
            trails[base] = kept.isEmpty ? nil : kept
        }
    }
}
