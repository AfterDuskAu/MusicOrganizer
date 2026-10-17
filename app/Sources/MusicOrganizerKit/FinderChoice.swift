import Foundation

/// A Finder's two genre menus (Movie, Series, Anime Finder): a genre, and a second one
/// ("and Action") that keeps only what's tagged with both (`addon.catalog`'s `also`).
public struct GenreChoice: Equatable, Sendable {
    public var genre: String
    /// The second genre, or "" for none ("One Genre").
    public var also: String

    public init(genre: String = "", also: String = "") {
        (self.genre, self.also) = (genre, also)
    }

    /// A genre chosen in the first menu. The second is empty again whenever the first
    /// changes (the owner, 2026-10-17: "when selecting a genre, you pick one. the second
    /// one should always be empty, it keeps giving a *And Action* or whatever was
    /// previously selected there"): the "and …" picked for one genre isn't carried over
    /// to the next, where it may even be the genre itself.
    public mutating func choose(genre new: String) {
        guard new != genre else { return }
        (genre, also) = (new, "")
    }

    /// A list chosen in the list menu while `shown` is the one showing. Another list's
    /// genres are its own, so both menus start empty (until 2026-10-17 only the first was
    /// emptied, and the old "and …" came back as soon as a genre was chosen on the new
    /// list). The same list chosen again, which is a way back from a search, keeps both.
    public mutating func choose(list: String, over shown: String) {
        guard list != shown else { return }
        (genre, also) = ("", "")
    }
}

/// The newest of a page's askings for its list. A search typed and then cleared before
/// its answer came, or a genre changed while the list before it was still arriving: the
/// later asking is the one wanted, and the earlier one's answer, whenever it comes, is
/// left unshown. (Until 2026-10-17 a list being asked for turned every other asking
/// away, so a search cleared too soon left its answer on the page under an empty box,
/// with the genres still chosen above it but not shown: "searching removes any of the
/// genre choosings".)
public struct Askings: Equatable, Sendable {
    private var count = 0
    /// What the newest asking is for, while its answer hasn't come.
    public private(set) var asking: String?

    public init() {}

    /// A new asking, for `wanted`: its ticket. Any asking before it is no longer wanted.
    public mutating func begin(_ wanted: String) -> Int {
        count += 1
        asking = wanted
        return count
    }

    /// Whether the asking with this ticket is still the newest: its answer is wanted.
    public func isNewest(_ ticket: Int) -> Bool { ticket == count }

    /// The asking with this ticket has had its answer.
    public mutating func end(_ ticket: Int) {
        if ticket == count { asking = nil }
    }

    public var isAsking: Bool { asking != nil }
}
