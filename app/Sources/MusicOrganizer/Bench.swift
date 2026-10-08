import AppKit

/// A developer's check, like `Snapshot`: how long the app can't answer after each click.
/// With `MUSICORG_BENCH=<steps>` the app takes the steps by itself once the library has
/// loaded, writes how long its main thread was busy after each to the error stream
/// ("BENCH favourites 412 ms, longest 380"), and quits. A change to the pages is
/// measured with it, the same way each time.
///
/// The steps have commas between them. Each is a page's saved name (`SidebarItem.key`:
/// "songs", "favourites", "youtube", "home", "settings"…), or one of:
///
/// - `search=<words>`: the library search with these words in it (`search=` shuts it)
/// - `finder=<words>`: these words in Music Finder's search box, as if typed (not searched for)
/// - `sort=<column>`: the song table showing, sorted by this column (`sort=-year`: backwards)
/// - `select=<row>+<row>`: these rows of it selected
/// - `column=<name>`: this column of it shown, or hidden if it's showing
/// - `menu=<row>`: this row right-clicked; the menu's items are written out, and it isn't opened
/// - `scroll=<row>`: the table scrolled to this row
/// - `heart=<row>`: this row's heart clicked
/// - `drag=rows`: how many rows can be dragged, and what the first carries, written out
/// - `hide`, `show`: the app hidden, and brought back
/// - `shrink`, `grow`: the window put in the Dock, and brought back
/// - `wait`: nothing, to see what the app does by itself
///
/// `/<seconds>` after a step waits that long before the next (4 otherwise):
/// `youtube/10`. The first line written is `launch`: everything from the app's start to
/// its first step.
@MainActor
enum Bench {
    struct Step {
        let name: String
        let wait: Double
    }

    static let steps: [Step] = (ProcessInfo.processInfo.environment["MUSICORG_BENCH"] ?? "")
        .split(separator: ",")
        .map { step in
            let parts = step.split(separator: "/", maxSplits: 1)
            let wait = parts.count > 1 ? Double(parts[1]) ?? 4 : 4
            return Step(name: String(parts.first ?? ""), wait: wait)
        }
    static var isOn: Bool { !steps.isEmpty }
    private static let meter = BusyMeter()

    static func startIfAsked() {
        if isOn { meter.start() }
    }

    /// Forget what's been measured: the next step starts from nothing.
    static func begin() {
        _ = meter.take()
    }

    static func report(_ name: String) {
        let (total, longest) = meter.take()
        say(String(format: "BENCH %@ %.0f ms, longest %.0f", name, total, longest))
    }

    static func say(_ line: String) {
        FileHandle.standardError.write(Data((line + "\n").utf8))
    }

    /// The song table on the page that's showing.
    private static func songTable() -> NSTableView? {
        func find(in view: NSView) -> NSTableView? {
            if let table = view as? NSTableView, table.numberOfColumns > 1, !table.isHiddenOrHasHiddenAncestor {
                return table
            }
            for child in view.subviews {
                if let found = find(in: child) { return found }
            }
            return nil
        }
        return NSApp.windows.first { $0.canBecomeMain }?.contentView.flatMap(find(in:))
    }

    /// The steps that work the song table as a click would.
    private static func tookTable(_ name: String) -> Bool {
        let parts = name.split(separator: "=", maxSplits: 1).map(String.init)
        let steps = ["sort", "select", "column", "menu", "scroll", "heart", "drag"]
        guard parts.count == 2, steps.contains(parts[0]) else {
            return false
        }
        guard let table = songTable() else {
            say("BENCH \(name): no song table is showing")
            return true
        }
        // Only the song lists' own table: a table of SwiftUI's keeps its sort and its
        // columns to itself, and stops the app if they're set from outside.
        guard table.delegate is SongTable.Coordinator || parts[0] == "scroll" else {
            say("BENCH \(name): that isn't a song list's table")
            return true
        }
        let value = parts[1]
        switch parts[0] {
        case "sort":
            let backwards = value.hasPrefix("-")
            table.sortDescriptors = [
                NSSortDescriptor(key: backwards ? String(value.dropFirst()) : value, ascending: !backwards)
            ]
        case "select":
            table.selectRowIndexes(
                IndexSet(value.split(separator: "+").compactMap { Int($0) }), byExtendingSelection: false)
        case "column":
            guard let menu = table.headerView?.menu else { break }
            menu.delegate?.menuNeedsUpdate?(menu)
            if let place = menu.items.firstIndex(where: { $0.representedObject as? String == value }) {
                menu.performActionForItem(at: place)
            }
        case "menu":
            guard let row = Int(value), row < table.numberOfRows, let window = table.window else { break }
            let spot = table.convert(table.rect(ofRow: row).insetBy(dx: 60, dy: 4).origin, to: nil)
            let click = NSEvent.mouseEvent(
                with: .rightMouseDown, location: spot, modifierFlags: [], timestamp: 0,
                windowNumber: window.windowNumber, context: nil, eventNumber: 0, clickCount: 1, pressure: 1)
            let menu = click.flatMap { table.menu(for: $0) }
            menu?.update()
            say("BENCH menu: " + (menu?.items.map { $0.isSeparatorItem ? "---" : $0.title } ?? ["none"]).joined(separator: " | "))
        case "heart":
            guard let row = Int(value), row < table.numberOfRows else { break }
            let cell = table.view(atColumn: 0, row: row, makeIfNecessary: true)
            (cell?.subviews.first { $0 is NSButton } as? NSButton)?.performClick(nil)
        case "drag":
            let carried = (0..<table.numberOfRows).compactMap {
                table.dataSource?.tableView?(table, pasteboardWriterForRow: $0) as? String
            }
            say("BENCH drag: \(carried.count) of \(table.numberOfRows) rows can be dragged; the first carries \(carried.first ?? "nothing")")
        default:
            if let row = Int(value) { table.scrollRowToVisible(min(row, table.numberOfRows - 1)) }
        }
        return true
    }

    /// The steps that aren't a page: true if this was one.
    static func took(_ name: String) -> Bool {
        if tookTable(name) { return true }
        // A window in the Dock can't become the main one, so it's looked for first.
        let window = NSApp.windows.first { $0.isMiniaturized }
            ?? NSApp.windows.first { $0.canBecomeMain }
        switch name {
        case "hide": NSApp.hide(nil)
        case "show":
            NSApp.unhide(nil)
            NSApp.activate(ignoringOtherApps: true)
        case "shrink": window?.miniaturize(nil)
        case "grow": window?.deminiaturize(nil)
        case "wait": break
        default: return false
        }
        return true
    }
}

/// Adds up the time the main thread kept a question waiting 10 ms or more.
private final class BusyMeter: @unchecked Sendable {
    private let lock = NSLock()
    private var total = 0.0
    private var longest = 0.0

    func start() {
        Thread.detachNewThread { [self] in
            while true {
                let asked = DispatchTime.now().uptimeNanoseconds
                let answered = DispatchSemaphore(value: 0)
                DispatchQueue.main.async { answered.signal() }
                answered.wait()
                let waited = Double(DispatchTime.now().uptimeNanoseconds - asked) / 1e6
                if waited >= 10 {
                    lock.withLock {
                        total += waited
                        longest = max(longest, waited)
                    }
                }
                Thread.sleep(forTimeInterval: 0.004)
            }
        }
    }

    func take() -> (total: Double, longest: Double) {
        lock.withLock {
            defer { (total, longest) = (0, 0) }
            return (total, longest)
        }
    }
}
