import Foundation

/// Which lines of a list are picked, for a page that draws its own lines (Discover →
/// Downloads). It works the way a Mac list does: a click picks one line, ⌘-click adds a
/// line or takes it away, and Shift-click picks every line from the last one clicked to
/// this one.
public struct RowSelection: Equatable, Sendable {
    public enum Click: Sendable {
        /// A plain click: this line and no other.
        case one
        /// ⌘-click: this line joins the others, or leaves them.
        case toggle
        /// Shift-click: every line from the last one clicked to this one.
        case extend
    }

    public private(set) var chosen: Set<String> = []
    /// Where a Shift-click measures from: the last line clicked without Shift.
    private var anchor: String?

    public init() {}

    public var isEmpty: Bool { chosen.isEmpty }

    public func contains(_ id: String) -> Bool { chosen.contains(id) }

    /// A click on one line. `order` is every line on the page, top to bottom.
    public mutating func click(_ id: String, _ kind: Click, in order: [String]) {
        switch kind {
        case .one:
            chosen = [id]
            anchor = id
        case .toggle:
            if chosen.contains(id) {
                chosen.remove(id)
            } else {
                chosen.insert(id)
            }
            anchor = id
        case .extend:
            guard let from = anchor.flatMap({ order.firstIndex(of: $0) }),
                let to = order.firstIndex(of: id)
            else {
                // Nothing to measure from: the same as a plain click.
                chosen = [id]
                anchor = id
                return
            }
            chosen = Set(order[min(from, to)...max(from, to)])
        }
    }

    public mutating func clear() {
        chosen = []
        anchor = nil
    }

    /// Forget lines that are no longer on the page (moved, deleted, or searched away).
    public mutating func keep(only order: [String]) {
        chosen.formIntersection(order)
        if let anchor, !order.contains(anchor) { self.anchor = nil }
    }

    /// What a right-click or a drag on one line is about: every picked line when this is
    /// one of them, and otherwise this line alone. In the page's order.
    public func acting(on id: String, in order: [String]) -> [String] {
        guard chosen.contains(id) else { return [id] }
        return order.filter(chosen.contains)
    }
}

/// The order a table's columns are shown in, when the owner can drag them about.
public enum ColumnOrder {
    /// The columns as they should stand: `first` (one that never moves), then the ones
    /// the owner has put in an order, in that order, then any that aren't in it (a
    /// column added since), in their usual order. Names that are no longer columns are
    /// left out, and so is a name that comes twice.
    public static func arranged(kept: [String], all: [String], first: String) -> [String] {
        var seen: Set<String> = [first]
        var order = [first]
        for name in kept + all where all.contains(name) && seen.insert(name).inserted {
            order.append(name)
        }
        return order
    }
}

/// Several songs' ids carried by one drag. A drag carries one piece of text, so the ids
/// travel as its lines.
public enum DraggedSongs {
    public static func text(of ids: [String]) -> String {
        ids.joined(separator: "\n")
    }

    public static func ids(in dropped: [String]) -> [String] {
        dropped.flatMap { $0.split(separator: "\n").map(String.init) }
    }
}
