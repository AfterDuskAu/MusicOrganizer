/// Which section of the sidebar each row is in, in what order, and the order of the
/// sections (the owner, 2026-11-11: "allow all the options in the sidebar to be customized
/// ... Moving to different subcategories, and mixing and matching outside of their
/// predetermined spot"). Any row can go in any section. Whether a row is shown at all is
/// kept apart, as before (`SidebarChoice` for Music's own entries, `SidebarRows` for the
/// rest), so the rows taken out before stay out.
///
/// The owner's own playlists are always in Playlists, at its top: they're the owner's lists,
/// not rows of the app.
public struct SidebarLayout: Equatable, Sendable {
    public struct Section: Equatable, Sendable {
        public let name: String
        public var rows: [String]

        public init(name: String, rows: [String]) {
            self.name = name
            self.rows = rows
        }
    }

    public private(set) var sections: [Section]

    /// The sections, by name.
    public static let music = "Music"
    public static let playlists = "Playlists"
    public static let videos = "Videos"
    public static let discovery = "Media Discovery"

    /// The standard sidebar (the owner, 2026-11-11): Playlists after Music and before Videos,
    /// and the Watchlist and the Calendar under Home, above Music Finder.
    public static let standard = SidebarLayout(sections: [
        Section(name: music, rows: SidebarChoice.all.filter { $0 != "videos" }),
        Section(name: playlists, rows: ["import"]),
        Section(name: videos, rows: ["channels", "movies", "adultVideos", "videos"]),
        Section(
            name: discovery,
            rows: [
                "home", "watchlist", "calendar", "youtube", "videoFinder", "movieFinder", "seriesFinder",
                "animeFinder", "adultFinder", "url", "downloads",
            ]),
    ])

    /// Every row, by the name it's saved under.
    public static let allRows: [String] = standard.sections.flatMap(\.rows)
    public static let sectionNames: [String] = standard.sections.map(\.name)

    /// Where it's saved.
    public static let key = "sidebarLayout"

    private init(sections: [Section]) {
        self.sections = sections
    }

    /// The section a row is in.
    public func section(of row: String) -> String? {
        sections.first { $0.rows.contains(row) }?.name
    }

    /// The rows of a section, in order.
    public func rows(in section: String) -> [String] {
        sections.first { $0.name == section }?.rows ?? []
    }

    // MARK: saved as text

    /// "Music:songs,albums|Playlists:import|…". Read back whatever was saved: sections and
    /// rows it doesn't know are dropped, each kept once, and anything missing (nothing
    /// saved yet, or a row a later version brings) goes in its standard place.
    public static func read(_ saved: String?) -> SidebarLayout {
        guard let saved, !saved.isEmpty else { return standard }
        var sections: [Section] = []
        var placed = Set<String>()
        for part in saved.split(separator: "|") {
            let halves = part.split(separator: ":", maxSplits: 1).map(String.init)
            guard let name = halves.first, sectionNames.contains(name), !sections.contains(where: { $0.name == name })
            else { continue }
            let rows = (halves.count > 1 ? halves[1] : "").split(separator: ",").map(String.init)
                .filter { allRows.contains($0) && placed.insert($0).inserted }
            sections.append(Section(name: name, rows: rows))
        }
        var layout = SidebarLayout(sections: sections)
        for section in standard.sections where !layout.sections.contains(where: { $0.name == section.name }) {
            layout.sections.append(Section(name: section.name, rows: []))
        }
        for row in allRows where !placed.contains(row) { layout.putBack(row) }
        return layout
    }

    public func write() -> String {
        sections.map { "\($0.name):\($0.rows.joined(separator: ","))" }.joined(separator: "|")
    }

    /// A row that wasn't placed, in its standard section after the row before it there.
    private mutating func putBack(_ row: String) {
        guard let home = Self.standard.section(of: row), let at = sections.firstIndex(where: { $0.name == home })
        else { return }
        let before = Self.standard.rows(in: home).prefix { $0 != row }
        let after = before.reversed().compactMap { sections[at].rows.firstIndex(of: $0) }.first
        sections[at].rows.insert(row, at: after.map { $0 + 1 } ?? 0)
    }

    // MARK: changed

    /// A row moved into a section (another, or its own), at a place in it (the end when nil).
    public mutating func move(_ row: String, to section: String, at place: Int? = nil) {
        guard Self.allRows.contains(row), let to = sections.firstIndex(where: { $0.name == section }) else { return }
        var place = place
        for index in sections.indices {
            if let was = sections[index].rows.firstIndex(of: row) {
                sections[index].rows.remove(at: was)
                // Moved down its own section: the rows after it have moved up one.
                if index == to, let wanted = place, was < wanted { place = wanted - 1 }
            }
        }
        let rows = sections[to].rows
        sections[to].rows.insert(row, at: min(max(place ?? rows.count, 0), rows.count))
    }

    /// Rows dragged within a section (as `ForEach.onMove` gives them).
    public mutating func move(in section: String, from offsets: [Int], to destination: Int) {
        guard let at = sections.firstIndex(where: { $0.name == section }) else { return }
        let rows = sections[at].rows
        let moving = offsets.filter(rows.indices.contains).sorted()
        guard !moving.isEmpty else { return }
        var kept = rows.enumerated().filter { !moving.contains($0.offset) }.map(\.element)
        let place = destination - moving.filter { $0 < destination }.count
        kept.insert(contentsOf: moving.map { rows[$0] }, at: min(max(place, 0), kept.count))
        sections[at].rows = kept
    }

    /// A row one place up (-1) or down (+1) in its section.
    public mutating func nudge(_ row: String, by step: Int) {
        guard let at = sections.firstIndex(where: { $0.rows.contains(row) }),
            let index = sections[at].rows.firstIndex(of: row)
        else { return }
        let target = index + step
        guard sections[at].rows.indices.contains(target) else { return }
        sections[at].rows.swapAt(index, target)
    }

    /// A section one place up (-1) or down (+1).
    public mutating func nudgeSection(_ name: String, by step: Int) {
        guard let index = sections.firstIndex(where: { $0.name == name }) else { return }
        let target = index + step
        guard sections.indices.contains(target) else { return }
        sections.swapAt(index, target)
    }
}
