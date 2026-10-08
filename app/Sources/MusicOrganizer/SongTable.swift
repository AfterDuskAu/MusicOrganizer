import AppKit
import MusicOrganizerKit
import SwiftUI

/// A list of songs as a table, made with AppKit's own table (the owner's yes, 2026-10-09).
///
/// The song lists used SwiftUI's `Table`, which was the slowest thing in the app (the
/// speed audit of 2026-10-08): it did work for every song in the list, not only the ones
/// on screen, and each of its cells was a small SwiftUI view of its own, about three
/// hundred and fifty of them on a screen, each one told about every change of the window.
/// A long list took a second to build, a third of a second to show again, and a few
/// tenths for each screenful scrolled.
///
/// An AppKit table makes only the rows on screen and uses them again as the list scrolls,
/// so 50,000 songs cost what 50 do. This one is made to look and work as the other did:
/// the same columns at the same widths (the title takes what the others leave), a click
/// on a heading to sort, View → Columns and a right-click on the headings to choose the
/// columns, the same right-click menu on a song, a double click or Return to play, and a
/// download dragged onto the sidebar. The list itself (which songs, in what order, what's
/// selected) still belongs to `SongList`: this only shows it.
struct SongTable: NSViewRepresentable {
    let rows: [TrackRow]
    @Binding var selection: Set<Int>
    @Binding var sortOrder: [KeyPathComparator<TrackRow>]
    /// The columns that can be hidden and are showing now (their saved names).
    let shown: Set<String>
    /// What a row shows besides its own song: whether it's the one playing, and a favourite.
    let playing: Track.ID?
    let isPlaying: Bool
    let favourites: Set<String>
    let model: AppModel
    /// Show or hide a column, for every list (View → Columns keeps the choice).
    let setShown: (_ column: String, _ shown: Bool) -> Void
    /// A double click, or Return: play from this place in the list.
    let primary: (_ place: Int) -> Void
    /// The right-click menu for these rows (their ids).
    let menu: (_ ids: Set<Int>) -> AnyView

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeNSView(context: Context) -> NSScrollView {
        let keeper = context.coordinator
        let table = SongTableView()
        table.style = .inset
        table.rowHeight = SongColumns.rowHeight
        table.usesAutomaticRowHeights = false
        table.allowsMultipleSelection = true
        table.allowsColumnReordering = true
        table.allowsColumnResizing = true
        table.columnAutoresizingStyle = .uniformColumnAutoresizingStyle
        // The table is as wide as the room it's in, so the title (the one column that
        // stretches) takes what the others leave; only in a window too narrow for them
        // all does the table scroll sideways.
        table.autoresizingMask = [.width]
        table.focusRingType = .none
        // macOS stripes a table with greys of its own, which don't sit on a warm page;
        // there the page's own colour shows through instead, with a thin line under
        // each row (as SwiftUI's table drew it).
        table.usesAlternatingRowBackgroundColors = !Theme.current.isWarm
        if Theme.current.isWarm {
            table.backgroundColor = .clear
            table.gridStyleMask = .solidHorizontalGridLineMask
        }
        for id in SongColumns.order() { table.addTableColumn(SongColumns.column(id)) }
        table.dataSource = keeper
        table.delegate = keeper
        table.target = keeper
        table.doubleAction = #selector(Coordinator.doubleClicked(_:))
        table.onReturn = { [weak keeper] in keeper?.playSelected() }
        table.menuForRows = { [weak keeper] rows in keeper?.menu(for: rows) }
        table.setDraggingSourceOperationMask([.copy, .move], forLocal: true)
        let headings = NSMenu()
        headings.delegate = keeper
        table.headerView?.menu = headings
        // A list that was sorted before this table was made (the other table was showing).
        if let sorted = sortOrder.first.flatMap(SongColumns.sorted(by:)) {
            keeper.settingUp = true
            table.sortDescriptors = [NSSortDescriptor(key: sorted.id, ascending: sorted.ascending)]
            keeper.settingUp = false
        }
        keeper.table = table

        let scroll = NSScrollView()
        scroll.documentView = table
        scroll.hasVerticalScroller = true
        scroll.hasHorizontalScroller = true
        scroll.autohidesScrollers = true
        scroll.borderType = .noBorder
        scroll.drawsBackground = !Theme.current.isWarm
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        context.coordinator.show(self)
    }

    /// Keeps the table and the list in step, and answers the table's questions.
    @MainActor
    final class Coordinator: NSObject, NSTableViewDataSource, NSTableViewDelegate, NSMenuDelegate {
        private var list: SongTable
        fileprivate weak var table: SongTableView?
        private var rows: [TrackRow] = []
        /// Where each row's id is in the table.
        private var places: [Int: Int] = [:]
        /// True while the table is being told something, so it isn't taken for a click.
        fileprivate var settingUp = false
        /// The columns have been fitted to the table's room once.
        private var fitted = false

        init(_ list: SongTable) {
            self.list = list
        }

        /// The list as it is now. Only what changed is touched: the rows on screen are
        /// asked for again, never the whole list.
        func show(_ new: SongTable) {
            let old = list
            list = new
            guard let table else { return }
            settingUp = true
            defer { settingUp = false }
            var changed = !fitted
            for column in table.tableColumns where SongColumns.canHide(column.identifier.rawValue) {
                let hidden = !new.shown.contains(column.identifier.rawValue)
                if column.isHidden != hidden {
                    column.isHidden = hidden
                    changed = true
                }
            }
            // A column come or gone: the title gives up, or takes, its room.
            if changed {
                table.sizeToFit()
                fitted = true
            }
            follow(SongColumns.order(), in: table)
            if rows != new.rows {
                rows = new.rows
                places = Dictionary(
                    rows.enumerated().map { ($1.id, $0) }, uniquingKeysWith: { first, _ in first })
                table.reloadData()
            } else {
                if old.playing != new.playing || old.isPlaying != new.isPlaying {
                    again(SongColumns.title, in: table)
                }
                if old.favourites != new.favourites { again(SongColumns.favourite, in: table) }
            }
            let wanted = IndexSet(new.selection.compactMap { places[$0] })
            if table.selectedRowIndexes != wanted {
                table.selectRowIndexes(wanted, byExtendingSelection: false)
            }
        }

        /// The rows on screen show one of their cells afresh.
        private func again(_ column: String, in table: NSTableView) {
            let place = table.column(withIdentifier: NSUserInterfaceItemIdentifier(column))
            guard place >= 0 else { return }
            let onScreen = table.rows(in: table.visibleRect)
            guard onScreen.length > 0 else { return }
            table.reloadData(
                forRowIndexes: IndexSet(integersIn: onScreen.location..<onScreen.location + onScreen.length),
                columnIndexes: [place])
        }

        /// The columns in the order kept for every list (changed in another list, say).
        private func follow(_ order: [String], in table: NSTableView) {
            for (place, id) in order.enumerated() {
                let now = table.column(withIdentifier: NSUserInterfaceItemIdentifier(id))
                if now >= 0, now != place, place < table.numberOfColumns {
                    table.moveColumn(now, toColumn: place)
                }
            }
        }

        // MARK: the rows

        func numberOfRows(in tableView: NSTableView) -> Int { rows.count }

        func tableView(
            _ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int
        ) -> NSView? {
            guard let name = tableColumn?.identifier, rows.indices.contains(row) else { return nil }
            let line = rows[row]
            let track = line.track
            switch name.rawValue {
            case SongColumns.favourite:
                let cell: HeartCell = reused(name, in: tableView)
                let on = track.trackId.map(list.favourites.contains) ?? false
                let model = list.model
                cell.show(on: on, canChange: track.trackId != nil) {
                    model.setFavourite([track], !on)
                }
                return cell
            case SongColumns.title:
                let cell: TitleCell = reused(name, in: tableView)
                let playing = list.playing == track.id
                cell.show(track, playing: playing, sounding: playing && list.isPlaying, root: list.model.root)
                return cell
            default:
                let cell: WordsCell = reused(name, in: tableView)
                let (words, quiet, digits) = Self.words(for: name.rawValue, in: line)
                cell.show(words, quiet: quiet, digits: digits)
                return cell
            }
        }

        /// What a plain column says of a song, whether it's said quietly (in the second
        /// colour), and whether its digits keep one width.
        private static func words(for column: String, in line: TrackRow) -> (String, Bool, Bool) {
            let track = line.track
            switch column {
            case "artist": return (track.artistName, false, false)
            case "album": return (track.albumName, false, false)
            case "year": return (track.year.map(String.init) ?? "", true, false)
            case "genre": return (track.sortGenre, true, false)
            case "quality": return (track.quality, true, false)
            case "added": return (track.addedDay, true, false)
            case "plays": return (line.plays > 0 ? String(line.plays) : "", true, true)
            case "time": return (clockTime(track.durationS), true, true)
            default: return ("", true, false)
            }
        }

        private func reused<Cell: NSTableCellView>(
            _ name: NSUserInterfaceItemIdentifier, in table: NSTableView
        ) -> Cell {
            if let cell = table.makeView(withIdentifier: name, owner: nil) as? Cell { return cell }
            let cell = Cell()
            cell.identifier = name
            return cell
        }

        func tableView(_ tableView: NSTableView, rowViewForRow row: Int) -> NSTableRowView? {
            let name = NSUserInterfaceItemIdentifier("row")
            if let row = tableView.makeView(withIdentifier: name, owner: nil) as? LinedRow { return row }
            let row = LinedRow()
            row.identifier = name
            return row
        }

        /// Typing a song's first letters goes to it.
        func tableView(
            _ tableView: NSTableView, typeSelectStringFor tableColumn: NSTableColumn?, row: Int
        ) -> String? {
            guard tableColumn?.identifier.rawValue == SongColumns.title, rows.indices.contains(row)
            else { return nil }
            return rows[row].track.title
        }

        // MARK: selecting, sorting, playing

        func tableViewSelectionDidChange(_ notification: Notification) {
            guard !settingUp, let table else { return }
            let ids = Set(table.selectedRowIndexes.compactMap { rows.indices.contains($0) ? rows[$0].id : nil })
            if list.selection != ids { list.selection = ids }
        }

        func tableView(
            _ tableView: NSTableView, sortDescriptorsDidChange oldDescriptors: [NSSortDescriptor]
        ) {
            guard !settingUp, let first = tableView.sortDescriptors.first, let key = first.key,
                let comparator = SongColumns.comparator(for: key, ascending: first.ascending)
            else { return }
            list.sortOrder = [comparator]
        }

        @objc func doubleClicked(_ sender: Any?) {
            guard let table, rows.indices.contains(table.clickedRow) else { return }
            list.primary(table.clickedRow)
        }

        @objc func playSelected() {
            guard let table, rows.indices.contains(table.selectedRow) else { return }
            list.primary(table.selectedRow)
        }

        /// The right-click menu for these rows of the table: the one every list of songs
        /// shares, as SwiftUI describes it.
        func menu(for places: IndexSet) -> NSMenu? {
            let ids = Set(places.compactMap { rows.indices.contains($0) ? rows[$0].id : nil })
            guard !ids.isEmpty else { return nil }
            if #available(macOS 14.4, *) {
                return NSHostingMenu(rootView: list.menu(ids))
            }
            // An older macOS can't make a menu from SwiftUI: Play, at least.
            let menu = NSMenu()
            let play = NSMenuItem(title: "Play", action: #selector(playSelected), keyEquivalent: "")
            play.target = self
            menu.addItem(play)
            return menu
        }

        // MARK: dragging

        /// A download can be dragged onto the sidebar: onto the Library to move it there,
        /// or back onto Downloads. What's carried is the song's id. Other rows don't drag.
        func tableView(_ tableView: NSTableView, pasteboardWriterForRow row: Int) -> NSPasteboardWriting? {
            guard rows.indices.contains(row), rows[row].track.isDownload,
                let id = rows[row].track.trackId
            else { return nil }
            return id as NSString
        }

        // MARK: the columns

        /// The heart stays first: it can't be moved, and nothing moves in front of it.
        func tableView(
            _ tableView: NSTableView, shouldReorderColumn columnIndex: Int, toColumn newColumnIndex: Int
        ) -> Bool {
            let heart = tableView.column(withIdentifier: NSUserInterfaceItemIdentifier(SongColumns.favourite))
            return columnIndex != heart && newColumnIndex != heart
        }

        func tableViewColumnDidMove(_ notification: Notification) {
            guard !settingUp, let table else { return }
            SongColumns.keep(order: table.tableColumns.map(\.identifier.rawValue))
        }

        /// A right-click on the headings: which columns show.
        func menuNeedsUpdate(_ menu: NSMenu) {
            menu.removeAllItems()
            for column in SongColumns.optional {
                let item = NSMenuItem(
                    title: column.title, action: #selector(switchColumn(_:)), keyEquivalent: "")
                item.target = self
                item.representedObject = column.id
                item.state = list.shown.contains(column.id) ? .on : .off
                menu.addItem(item)
            }
        }

        @objc func switchColumn(_ item: NSMenuItem) {
            guard let id = item.representedObject as? String else { return }
            list.setShown(id, !list.shown.contains(id))
        }
    }
}

// MARK: the columns, for both tables

extension SongColumns {
    /// View → Old Song Table: the song lists drawn by SwiftUI's table, as they were until
    /// 2026-10-09, kept to look at beside the new one.
    static let oldTableKey = "oldSongTable"
    static let favourite = "favourite"
    static let title = "title"
    static let rowHeight: CGFloat = 34
    private static let orderKey = "songColumnOrder"

    static func canHide(_ id: String) -> Bool { optional.contains { $0.id == id } }

    /// Every column, in the order kept for every list: the heart first, then as the
    /// owner has dragged them (a column that's new since then goes on the end).
    static func order() -> [String] {
        ColumnOrder.arranged(
            kept: UserDefaults.standard.stringArray(forKey: orderKey) ?? [],
            all: [title] + optional.map(\.id), first: favourite)
    }

    static func keep(order: [String]) {
        UserDefaults.standard.set(order.filter { $0 != favourite }, forKey: orderKey)
    }

    /// One column of the AppKit table. Every width is set but the title's, which takes
    /// what the others leave (see `widths`).
    @MainActor
    static func column(_ id: String) -> NSTableColumn {
        let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier(id))
        column.title = id == title ? "Title" : optional.first { $0.id == id }?.title ?? ""
        if id == title {
            column.minWidth = titleLeast
            column.width = 320
            column.resizingMask = .autoresizingMask
        } else {
            let width = id == favourite ? favouriteWidth : widths[id] ?? 80
            (column.minWidth, column.width, column.maxWidth) = (width, width, width)
            column.resizingMask = []
        }
        if id != favourite {
            column.sortDescriptorPrototype = NSSortDescriptor(key: id, ascending: true)
        }
        return column
    }

    /// How a column sorts the list: words as the Finder would, numbers as numbers.
    static func comparator(for id: String, ascending: Bool) -> KeyPathComparator<TrackRow>? {
        let order: SortOrder = ascending ? .forward : .reverse
        switch id {
        case "title":
            return KeyPathComparator(\TrackRow.track.title, comparator: .localizedStandard, order: order)
        case "artist":
            return KeyPathComparator(\TrackRow.track.artistName, comparator: .localizedStandard, order: order)
        case "album":
            return KeyPathComparator(\TrackRow.track.albumName, comparator: .localizedStandard, order: order)
        case "year": return KeyPathComparator(\TrackRow.track.sortYear, order: order)
        case "genre":
            return KeyPathComparator(\TrackRow.track.sortGenre, comparator: .localizedStandard, order: order)
        case "quality":
            return KeyPathComparator(\TrackRow.track.quality, comparator: .localizedStandard, order: order)
        case "added":
            return KeyPathComparator(\TrackRow.track.sortAdded, comparator: .localizedStandard, order: order)
        case "plays": return KeyPathComparator(\TrackRow.plays, order: order)
        case "time": return KeyPathComparator(\TrackRow.track.sortDuration, order: order)
        default: return nil
        }
    }

    /// Which column a list is sorted by, and which way.
    static func sorted(by comparator: KeyPathComparator<TrackRow>) -> (id: String, ascending: Bool)? {
        let id = ([title] + optional.map(\.id)).first {
            Self.comparator(for: $0, ascending: true)?.keyPath == comparator.keyPath
        }
        return id.map { ($0, comparator.order == .forward) }
    }
}

/// View → Old Song Table, in the menu bar.
struct OldSongTableSwitch: View {
    @AppStorage(SongColumns.oldTableKey) private var old = false

    var body: some View {
        Toggle("Old Song Table", isOn: $old)
    }
}

// MARK: the table and its cells

/// The table itself: a right-click, and Return, are its own to answer.
private final class SongTableView: NSTableView {
    var menuForRows: ((IndexSet) -> NSMenu?)?
    var onReturn: (() -> Void)?

    /// A right-click on a song that isn't selected selects it, so it's plain which songs
    /// the menu is about; on one that is, the menu is about everything selected.
    override func menu(for event: NSEvent) -> NSMenu? {
        let place = row(at: convert(event.locationInWindow, from: nil))
        guard place >= 0 else { return nil }
        if !selectedRowIndexes.contains(place) {
            selectRowIndexes([place], byExtendingSelection: false)
        }
        return menuForRows?(selectedRowIndexes)
    }

    override func keyDown(with event: NSEvent) {
        // Return or Enter plays the song that's selected.
        if event.keyCode == 36 || event.keyCode == 76, selectedRow >= 0 {
            onReturn?()
        } else {
            super.keyDown(with: event)
        }
    }

    /// macOS gone light or dark (the native look follows it): the cells' own colours
    /// are set afresh.
    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        if !Theme.current.isWarm { reloadData() }
    }
}

/// A row whose line underneath stops short of the table's edges, as far as its words do.
private final class LinedRow: NSTableRowView {
    private static let inset: CGFloat = 16

    override init(frame: NSRect) {
        super.init(frame: frame)
        // A row's cells are drawn into the row's one picture, not each into its own:
        // ten small pictures a row was most of what scrolling cost.
        canDrawSubviewsIntoLayer = true
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("not used") }

    override func drawSeparator(in dirtyRect: NSRect) {
        guard !isSelected, !isNextRowSelected else { return }
        NSColor.separatorColor.setFill()
        NSRect(x: Self.inset, y: bounds.maxY - 1, width: bounds.width - Self.inset * 2, height: 1)
            .intersection(dirtyRect).fill()
    }
}

/// The colours of the words and marks in a row: the look's, or macOS's own.
@MainActor
private enum Ink {
    static var words: NSColor { Theme.current.isWarm ? NSColor(WarmPalette.text) : .labelColor }
    /// SwiftUI's second, third and fourth levels of a colour are that colour let through
    /// less and less.
    static var quiet: NSColor {
        Theme.current.isWarm ? NSColor(WarmPalette.text).withAlphaComponent(0.5) : .secondaryLabelColor
    }
    static var faint: NSColor {
        Theme.current.isWarm ? NSColor(WarmPalette.text).withAlphaComponent(0.25) : .tertiaryLabelColor
    }
    static var faintest: NSColor {
        Theme.current.isWarm ? NSColor(WarmPalette.text).withAlphaComponent(0.2) : .quaternaryLabelColor
    }

    /// A small symbol, the size of the words beside it. Each is made once and used in
    /// every row: making one afresh for each cell was a good part of building a screen.
    static func symbol(_ name: String, saying words: String? = nil) -> NSImage? {
        if let made = symbols[name] { return made }
        let made = NSImage(systemSymbolName: name, accessibilityDescription: words)?
            .withSymbolConfiguration(
                NSImage.SymbolConfiguration(pointSize: NSFont.systemFontSize, weight: .regular))
        symbols[name] = made
        return made
    }

    private static var symbols: [String: NSImage] = [:]

    static func mark(_ name: String, _ colour: NSColor) -> NSImageView {
        let view = NSImageView()
        view.image = symbol(name)
        // Drawn at its own size in the room it's given (`room(for:)`), never squeezed.
        view.imageScaling = .scaleNone
        view.contentTintColor = colour
        view.isHidden = true
        return view
    }

    /// The room a mark takes: its picture's own size. (What an image view says it wants
    /// is only the letter-high middle of a symbol, and a symbol given that was drawn
    /// half its size.)
    static func room(for mark: NSImageView) -> NSSize { mark.image?.size ?? .zero }

    static let labelEdge: CGFloat = 2
    /// How tall a line of words is: the same for every row, so it's measured once.
    static let lineHeight: CGFloat = {
        let sample = line()
        sample.stringValue = "Song"
        return (sample.cell?.cellSize.height ?? 16).rounded(.up)
    }()
    static let plain = NSFont.systemFont(ofSize: NSFont.systemFontSize)
    static let strong = NSFont.systemFont(ofSize: NSFont.systemFontSize, weight: .semibold)
    static let digits = NSFont.monospacedDigitSystemFont(ofSize: NSFont.systemFontSize, weight: .regular)

    static func line() -> NSTextField {
        let line = NSTextField(labelWithString: "")
        line.lineBreakMode = .byTruncatingTail
        line.maximumNumberOfLines = 1
        line.font = plain
        return line
    }
}

/// A plain line of words: the artist, the album, the year…
///
/// The cells place what's in them themselves (`layout`), with no constraints to be
/// solved: a screen of a table is some three hundred cells.
private final class WordsCell: NSTableCellView {
    private let line = Ink.line()

    override init(frame: NSRect) {
        super.init(frame: frame)
        addSubview(line)
        textField = line
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("not used") }

    func show(_ words: String, quiet: Bool, digits: Bool) {
        line.stringValue = words
        line.textColor = quiet ? Ink.quiet : Ink.words
        line.font = digits ? Ink.digits : Ink.plain
        needsLayout = true
    }

    override func layout() {
        super.layout()
        let height = Ink.lineHeight
        // A label keeps two points clear at each end of its words: the words themselves
        // start at the cell's edge, as SwiftUI's did.
        line.frame = backingAlignedRect(
            NSRect(
                x: -Ink.labelEdge, y: (bounds.height - height) / 2,
                width: bounds.width + Ink.labelEdge * 2, height: height),
            options: .alignAllEdgesNearest)
    }
}

/// The heart beside a song: filled when it's a favourite.
private final class HeartCell: NSTableCellView {
    private let heart = NSButton()
    private var change: (() -> Void)?

    override init(frame: NSRect) {
        super.init(frame: frame)
        heart.isBordered = false
        heart.imagePosition = .imageOnly
        heart.target = self
        heart.action = #selector(clicked)
        addSubview(heart)
    }

    /// The heart's own size: the same in every row, so it's asked for once.
    private static var size: NSSize?

    override func layout() {
        super.layout()
        let size = Self.size ?? heart.fittingSize
        if Self.size == nil, size.width > 0 { Self.size = size }
        heart.frame = NSRect(
            x: 0, y: ((bounds.height - size.height) / 2).rounded(), width: size.width, height: size.height)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("not used") }

    func show(on: Bool, canChange: Bool, change: @escaping () -> Void) {
        self.change = change
        heart.image = Ink.symbol(on ? "heart.fill" : "heart", saying: on ? "Favourite" : "Not a favourite")
        // Not a favourite: the second colour at half strength, which is what macOS's
        // third colour is.
        heart.contentTintColor = on ? .systemPink : .tertiaryLabelColor
        heart.isEnabled = canChange
        heart.toolTip = on ? "Remove from Favourites" : "Add to Favourites"
        needsLayout = true
    }

    @objc private func clicked() { change?() }
}

/// A song's cover, title and small badges, as `SongTitle` shows them.
private final class TitleCell: NSTableCellView {
    private static let gap: CGFloat = 8
    private static let coverSide: CGFloat = 30

    private let cover = CoverBox()
    private let line = Ink.line()
    private let explicit = Ink.mark("e.square.fill", Ink.quiet)
    private let video = Ink.mark("film", Ink.quiet)
    private let unknown = Ink.mark("questionmark.circle", .systemOrange)
    private let speaker = Ink.mark("speaker.fill", Ink.words)
    private let lyrics = Ink.mark("quote.bubble", Ink.faint)
    private var showing: String?
    private var loading: Task<Void, Never>?
    /// The room the title's words take, all of them showing.
    private var wanted = NSSize.zero

    override init(frame: NSRect) {
        super.init(frame: frame)
        for view in [cover, line, explicit, video, unknown, speaker, lyrics] { addSubview(view) }
        textField = line
    }

    /// A badge shown or hidden, with what it says when the pointer rests on it. A badge
    /// that isn't showing says nothing: macOS keeps watch over every tip there is, row
    /// by row, as the list scrolls.
    private func set(_ badge: NSImageView, shown: Bool, saying tip: String) {
        if badge.isHidden == shown { badge.isHidden = !shown }
        let wanted = shown ? tip : nil
        if badge.toolTip != wanted { badge.toolTip = wanted }
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("not used") }

    func show(_ track: Track, playing: Bool, sounding: Bool, root: URL?) {
        line.stringValue = track.title
        line.textColor = Ink.words
        line.font = playing ? Ink.strong : Ink.plain
        set(explicit, shown: track.explicit, saying: "Explicit")
        set(video, shown: track.isVideo, saying: "A saved video" + (track.height.map { " (\($0)p)" } ?? ""))
        set(unknown, shown: track.isUnconfirmed, saying: "Not identified yet: shown under its own name")
        speaker.isHidden = !playing
        speaker.image = Ink.symbol(sounding ? "speaker.wave.2.fill" : "speaker.fill", saying: "Playing")
        set(lyrics, shown: track.lyrics == .synced, saying: "Has timed lyrics")
        // Measured once here, not each time the row is laid out.
        wanted = line.cell?.cellSize ?? line.intrinsicContentSize
        needsLayout = true

        // The cover: at once if it's in memory, and otherwise when it has been read, if
        // this cell is still that song's by then (a row scrolled away is used again).
        let key = track.cover ?? track.path
        guard key != showing || cover.picture == nil else { return }
        showing = key
        loading?.cancel()
        if let ready = Covers.shared.cached(track, .small) {
            cover.picture = ready
            return
        }
        cover.picture = nil
        loading = Task { [weak self] in
            let read = await Covers.shared.load(track, root: root, size: .small)
            guard !Task.isCancelled, let self, self.showing == key else { return }
            self.cover.picture = read
        }
    }

    /// Left to right: the cover, the title in the room there is, the badges straight
    /// after it, and the lyrics mark at the far end.
    override func layout() {
        super.layout()
        let height = bounds.height
        func centred(_ size: NSSize, at x: CGFloat) -> NSRect {
            // To the nearest dot of the screen, not the nearest point: half a point out
            // shows, beside SwiftUI's.
            backingAlignedRect(
                NSRect(x: x, y: (height - size.height) / 2, width: size.width, height: size.height),
                options: .alignAllEdgesNearest)
        }
        cover.frame = centred(NSSize(width: Self.coverSide, height: Self.coverSide), at: 0)
        var right = bounds.width
        if !lyrics.isHidden {
            let size = Ink.room(for: lyrics)
            right -= size.width
            lyrics.frame = centred(size, at: right)
            right -= Self.gap
        }
        let badges = [explicit, video, unknown, speaker].filter { !$0.isHidden }
        let badgesWidth = badges.reduce(0) { $0 + Ink.room(for: $1).width + Self.gap }
        // A label keeps two points clear at each end of its words: the words themselves
        // start the gap's width from the cover.
        var x = Self.coverSide + Self.gap - Ink.labelEdge
        // The badges follow the words exactly; the label itself is given a whole point
        // more than its words need, so the last letter is never cut.
        let width = max(0, min(wanted.width, right - x - badgesWidth))
        line.frame = centred(NSSize(width: width.rounded(.up), height: wanted.height.rounded(.up)), at: x)
        x += width - Ink.labelEdge
        for badge in badges {
            x += Self.gap
            let size = Ink.room(for: badge)
            badge.frame = centred(size, at: x)
            x += size.width
        }
    }
}

/// A square cover, or a quiet placeholder until there's one.
private final class CoverBox: NSView {
    private static let corner: CGFloat = 4
    private let note = Ink.mark("music.note", Ink.faint)

    var picture: NSImage? {
        didSet {
            guard picture !== oldValue else { return }
            note.isHidden = picture != nil
            needsDisplay = true
        }
    }

    override init(frame: NSRect) {
        super.init(frame: frame)
        note.isHidden = false
        addSubview(note)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("not used") }

    /// The cover fills the square, cut to it if it isn't square itself, with the
    /// corners rounded.
    override func draw(_ dirtyRect: NSRect) {
        NSBezierPath(roundedRect: bounds, xRadius: Self.corner, yRadius: Self.corner).addClip()
        guard let picture, picture.size.width > 0, picture.size.height > 0 else {
            Ink.faintest.setFill()
            bounds.fill()
            return
        }
        let scale = max(bounds.width / picture.size.width, bounds.height / picture.size.height)
        let (width, height) = (picture.size.width * scale, picture.size.height * scale)
        picture.draw(
            in: NSRect(
                x: (bounds.width - width) / 2, y: (bounds.height - height) / 2, width: width, height: height),
            from: .zero, operation: .sourceOver, fraction: 1, respectFlipped: true, hints: nil)
    }

    override func layout() {
        super.layout()
        let size = Ink.room(for: note)
        note.frame = backingAlignedRect(
            NSRect(
                x: (bounds.width - size.width) / 2, y: (bounds.height - size.height) / 2,
                width: size.width, height: size.height),
            options: .alignAllEdgesNearest)
    }
}
