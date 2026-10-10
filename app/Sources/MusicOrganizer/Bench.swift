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
/// - `first=<x>x<y>`: whether the thing at this spot of the window (points from its top
///   left, as a picture of it shows) takes the click that brings the app forward
/// - `click=<x>x<y>`: the mouse pressed and let go at this spot, as a click by hand arrives
///   (with the unseen copy, which is never in front, that's a click on an app behind
///   another; it works the pages' own lines and buttons, but a song list's rows don't
///   answer a made-up press at all, in front or not)
/// - `down=<points>`: the page that's showing (one that isn't a song list) scrolled down
///   this far, and how long it is written out
/// - `size=<w>x<h>`: the window dragged to this size in twelve steps, and how long a
///   step took written out
/// - `settings=<section>`: this section of Settings chosen ("profile", "play", "downloads",
///   "lyrics", "addons", "sharing"), with the Settings page showing
/// - `flip=<key>`: a yes-or-no setting switched over, by its saved name
/// - `state`: the page showing, the song rows picked and whether the app is in front, written out
/// - `hide`, `show`: the app hidden, and brought back
/// - `shrink`, `grow`: the window put in the Dock, and brought back
/// - `wait`: nothing, to see what the app does by itself
///
/// `/<seconds>` after a step waits that long before the next (4 otherwise):
/// `youtube/10`. The first lines written are `ready` (how long after macOS started the
/// app its library was showing) and `launch`: how long the app was busy from its start
/// to its first step.
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

    /// A moment of the app's opening, written with how long after its start it came.
    static func mark(_ what: String) {
        if isOn { say(String(format: "BENCH opening: %.2f s %@", sinceLaunch(), what)) }
    }

    /// How long ago macOS started this app: by its own record, so the time the app
    /// takes to be loaded is in it too.
    static func sinceLaunch() -> Double {
        var info = kinfo_proc()
        var size = MemoryLayout<kinfo_proc>.stride
        var name: [Int32] = [CTL_KERN, KERN_PROC, KERN_PROC_PID, getpid()]
        guard sysctl(&name, 4, &info, &size, nil, 0) == 0 else { return 0 }
        let born = info.kp_proc.p_starttime
        return Date().timeIntervalSince1970 - (Double(born.tv_sec) + Double(born.tv_usec) / 1_000_000)
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
            // The mouse pressed and let go over the heart, through the window, as a click
            // by hand arrives: the let-go is waiting when the press is answered.
            guard let window = table.window,
                let heart = table.view(atColumn: 0, row: row, makeIfNecessary: true)?.subviews.first
            else { break }
            let spot = heart.convert(NSPoint(x: heart.bounds.midX, y: heart.bounds.midY), to: nil)
            let (down, up) = (mouse(.leftMouseDown, at: spot, in: window), mouse(.leftMouseUp, at: spot, in: window))
            if let down, let up {
                window.postEvent(up, atStart: false)
                window.sendEvent(down)
            }
        case "drag":
            let carried = (0..<table.numberOfRows).compactMap {
                table.dataSource?.tableView?(table, pasteboardWriterForRow: $0) as? NSPasteboardItem
            }
            let files = carried.filter { $0.types.contains(.fileURL) }.count
            let ids = carried.filter { $0.types.contains(.string) }.count
            let first = carried.first.map { item in
                item.types.map { "\($0.rawValue) = \(item.string(forType: $0) ?? "?")" }.joined(separator: "; ")
            }
            say(
                "BENCH drag: \(carried.count) of \(table.numberOfRows) rows can be dragged, \(files) with "
                    + "their file and \(ids) with a download's id; out of the app they may be "
                    + "\(table.draggingSession(NSDraggingSession(), sourceOperationMaskFor: .outsideApplication) == .copy ? "copied only" : "more than copied"); "
                    + "the first carries \(first ?? "nothing")")
        default:
            if let row = Int(value) { table.scrollRowToVisible(min(row, table.numberOfRows - 1)) }
        }
        return true
    }

    private static func mouse(_ kind: NSEvent.EventType, at spot: NSPoint, in window: NSWindow) -> NSEvent? {
        NSEvent.mouseEvent(
            with: kind, location: spot, modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime,
            windowNumber: window.windowNumber, context: nil, eventNumber: 0, clickCount: 1, pressure: 1)
    }

    /// The steps that aren't a page: true if this was one.
    static func took(_ name: String) -> Bool {
        if tookTable(name) { return true }
        // A window in the Dock can't become the main one, so it's looked for first.
        let window = NSApp.windows.first { $0.isMiniaturized }
            ?? NSApp.windows.first { $0.canBecomeMain }
        if name.hasPrefix("first=") {
            // What macOS would be told if the app were behind another and this spot were
            // clicked: the very question it asks, of the very view it asks it of. The
            // press is sent the way a real one arrives and stopped before anything sees
            // it, because a table answers "what's under the mouse" by the press in hand.
            let spot = name.dropFirst(6).split(separator: "x").compactMap { Double($0) }
            guard spot.count == 2, let window, let frame = window.contentView?.superview,
                let press = mouse(.leftMouseDown, at: NSPoint(x: spot[0], y: window.frame.height - spot[1]), in: window)
            else { return true }
            var answer = "wasn't asked"
            let watch = NSEvent.addLocalMonitorForEvents(matching: .leftMouseDown) { event in
                guard event === press || event.timestamp == press.timestamp else { return event }
                let under = frame.hitTest(event.locationInWindow)
                answer =
                    (under?.acceptsFirstMouse(for: event) == true ? "takes the first click" : "leaves the first click")
                    + " (\(under.map { String(describing: type(of: $0)) } ?? "nothing there"))"
                return nil
            }
            NSApp.sendEvent(press)
            if let watch { NSEvent.removeMonitor(watch) }
            say("BENCH first: \(Int(spot[0]))x\(Int(spot[1])) \(answer)")
            return true
        }
        if name.hasPrefix("click=") {
            // A press and a let-go at this spot, sent the way real ones arrive, so macOS's
            // own rule for a click on an app that isn't in front is the one applied.
            let spot = name.dropFirst(6).split(separator: "x").compactMap { Double($0) }
            guard spot.count == 2, let window else { return true }
            let place = NSPoint(x: spot[0], y: window.frame.height - spot[1])
            if let down = mouse(.leftMouseDown, at: place, in: window), let up = mouse(.leftMouseUp, at: place, in: window) {
                window.postEvent(up, atStart: false)
                NSApp.sendEvent(down)
            }
            return true
        }
        if name.hasPrefix("down=") {
            // The page's own scrolling part (the widest that isn't a table's, which the
            // sidebar's and a song list's are; of two as wide, the one in front: a film's
            // page over its Finder) moved down by this many points.
            guard let points = Double(name.dropFirst(5)), let content = window?.contentView else { return true }
            var widest: NSScrollView?
            func look(in view: NSView) {
                if let scroll = view as? NSScrollView, !scroll.isHiddenOrHasHiddenAncestor,
                    !(scroll.documentView is NSTableView), scroll.frame.width >= (widest?.frame.width ?? 0)
                {
                    widest = scroll
                }
                view.subviews.forEach(look(in:))
            }
            look(in: content)
            guard let scroll = widest, let page = scroll.documentView else {
                say("BENCH down: no page that scrolls is showing")
                return true
            }
            let clip = scroll.contentView
            let most = max(0, page.frame.height - clip.bounds.height)
            let to = min(most, max(0, clip.bounds.origin.y + (page.isFlipped ? points : -points)))
            clip.scroll(to: NSPoint(x: clip.bounds.origin.x, y: to))
            scroll.reflectScrolledClipView(clip)
            say("BENCH down: the page is \(Int(page.frame.height)) points long, and now at \(Int(to))")
            return true
        }
        if name.hasPrefix("size=") {
            // The window dragged to this size in twelve steps, as a hand on its corner
            // does it: each step is laid out and drawn before the next.
            let size = name.dropFirst(5).split(separator: "x").compactMap { Double($0) }
            guard size.count == 2, let window else { return true }
            let from = window.frame
            let began = Date()
            for step in 1...12 {
                let part = Double(step) / 12
                let width = from.width + (size[0] - from.width) * part
                let height = from.height + (size[1] - from.height) * part
                // The top left corner stays where it is.
                window.setFrame(
                    NSRect(x: from.minX, y: from.maxY - height, width: width, height: height), display: true)
                window.layoutIfNeeded()
                window.displayIfNeeded()
            }
            say(String(
                format: "BENCH size: to %.0f by %.0f in twelve steps, %.0f ms a step",
                window.frame.width, window.frame.height, Date().timeIntervalSince(began) * 1000 / 12))
            return true
        }
        if name.hasPrefix("settings=") {
            // A section of Settings chosen, as a click on its name does it.
            UserDefaults.standard.set(String(name.dropFirst(9)), forKey: SettingsView.tabKey)
            return true
        }
        if name.hasPrefix("flip=") {
            // A yes-or-no setting switched over, as its switch in Settings does it.
            let key = String(name.dropFirst(5))
            UserDefaults.standard.set(!UserDefaults.standard.bool(forKey: key), forKey: key)
            return true
        }
        if name == "state" {
            let picked = songTable().map { Array($0.selectedRowIndexes).map(String.init).joined(separator: "+") }
            say(
                "BENCH state: page \(UserDefaults.standard.string(forKey: "lastSection") ?? "none"), "
                    + "song rows picked: \(picked.map { $0.isEmpty ? "none" : $0 } ?? "no song list showing"), "
                    + "app in front: \(NSApp.isActive), window key: \(window?.isKeyWindow == true)")
            return true
        }
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
