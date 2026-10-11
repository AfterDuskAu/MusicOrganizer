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
/// - `remove=library`: the newest download taken out of the library, its file kept (never
///   on a library that matters: it's what Delete from Library does after the owner's yes)
/// - `putback`: the newest song in Settings → Deleted Items put back (its Undo); the
///   section has to have been opened first (`settings=deleted`)
/// - `sort=<column>`: the song table showing, sorted by this column (`sort=-year`: backwards)
/// - `select=<row>+<row>`: these rows of it selected
/// - `column=<name>`: this column of it shown, or hidden if it's showing
/// - `menu=<row>`: this row right-clicked; the menu's items are written out, and it isn't opened
/// - `scroll=<row>`: the table scrolled to this row
/// - `heart=<row>`: this row's heart clicked
/// - `drag=rows`: how many rows can be dragged, and what the first carries, written out
/// - `move=<row>+<row>@<row>`: these rows dropped above that row of their own list, as a drag
///   by hand ends (a playlist in its own order takes it; any other list says it isn't allowed)
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
/// - `fullscreen`: the window sent into macOS's full screen, or brought out of it, as the
///   green button does it (only a copy that's seen can: it takes the whole screen)
/// - `windows`: from here on, every change to the app's own windows written out (which
///   there are, where, how see-through, whether what's in them is hidden), with the
///   moments full screen begins and ends; and for three seconds after each such moment,
///   how much of macOS's strip across the top of a full screen is drawn
/// - `key=<name>`: a key pressed, as one on the keyboard arrives: `space`, `up`, `down`,
///   `left` or `right`
/// - `film`: whether a film is open and playing, where it has got to and how loud, written out
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
        let steps = ["sort", "select", "column", "menu", "scroll", "heart", "drag", "move"]
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
        case "move":
            // Rows dropped at another place in their own list ("1+3@0": rows 1 and 3,
            // above row 0), as a drop by hand arrives: what the rows carry goes on a
            // pasteboard of this step's own (never the owner's clipboard), and the table
            // is asked the two questions macOS asks it.
            let halves = value.split(separator: "@")
            guard halves.count == 2, let to = Int(halves[1]) else { break }
            let dragged = halves[0].split(separator: "+").compactMap { Int($0) }
            let board = NSPasteboard(name: NSPasteboard.Name("org.musicorganizer.bench.move"))
            board.clearContents()
            board.writeObjects(
                dragged.compactMap { table.dataSource?.tableView?(table, pasteboardWriterForRow: $0) })
            let drop = MadeUpDrop(from: table, carrying: board)
            let asked =
                table.dataSource?.tableView?(
                    table, validateDrop: drop, proposedRow: to, proposedDropOperation: .above) ?? []
            let taken =
                asked == .move
                && (table.dataSource?.tableView?(table, acceptDrop: drop, row: to, dropOperation: .above)
                    ?? false)
            say(
                "BENCH move: rows \(dragged) above row \(to): "
                    + (asked == .move ? (taken ? "moved" : "allowed, then refused") : "not allowed here"))
            board.releaseGlobally()
        default:
            if let row = Int(value) { table.scrollRowToVisible(min(row, table.numberOfRows - 1)) }
        }
        return true
    }

    /// A drop no hand made, for the `move` step: where it's from and what it carries are
    /// real, and nothing else about it is asked.
    private final class MadeUpDrop: NSObject, NSDraggingInfo {
        private let table: NSTableView
        private let board: NSPasteboard

        init(from table: NSTableView, carrying board: NSPasteboard) {
            self.table = table
            self.board = board
        }

        var draggingDestinationWindow: NSWindow? { table.window }
        var draggingSourceOperationMask: NSDragOperation { [.copy, .move] }
        var draggingLocation: NSPoint { .zero }
        var draggedImageLocation: NSPoint { .zero }
        var draggedImage: NSImage? { nil }
        var draggingPasteboard: NSPasteboard { board }
        var draggingSource: Any? { table }
        var draggingSequenceNumber: Int { 0 }
        var draggingFormation: NSDraggingFormation = .default
        var animatesToDestination = false
        var numberOfValidItemsForDrop = 0
        var springLoadingHighlight: NSSpringLoadingHighlight { .none }

        func slideDraggedImage(to screenPoint: NSPoint) {}
        func resetSpringLoading() {}
        func enumerateDraggingItems(
            options enumOpts: NSDraggingItemEnumerationOptions = [], for view: NSView?,
            classes classArray: [AnyClass], searchOptions: [NSPasteboard.ReadingOptionKey: Any] = [:],
            using block: @escaping (NSDraggingItem, Int, UnsafeMutablePointer<ObjCBool>) -> Void
        ) {}
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
        if name.hasPrefix("key=") {
            let keys: [String: (UInt16, String)] = [
                "space": (49, " "), "up": (126, "\u{F700}"), "down": (125, "\u{F701}"),
                "left": (123, "\u{F702}"), "right": (124, "\u{F703}"),
            ]
            guard let (code, letter) = keys[String(name.dropFirst(4))], let window,
                let press = NSEvent.keyEvent(
                    with: .keyDown, location: .zero, modifierFlags: [],
                    timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: window.windowNumber,
                    context: nil, characters: letter, charactersIgnoringModifiers: letter, isARepeat: false,
                    keyCode: code)
            else { return true }
            NSApp.sendEvent(press)
            return true
        }
        if name == "windows" {
            WindowWatch.start()
            return true
        }
        switch name {
        case "fullscreen": window?.toggleFullScreen(nil)
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

/// Writes out every change to the app's own windows, for finding what macOS puts on the
/// screen around a change like full screen (the strip across the top of a film was a
/// window of macOS's own making, and only shows in the Warm Look).
@MainActor
private enum WindowWatch {
    private static var seen: [Int: String] = [:]
    private static var began = Date()
    private static var timer: Timer?
    private static var measuringUntil = Date.distantPast
    private static var lastMeasure = Date.distantPast

    static func start() {
        guard timer == nil else { return }
        began = Date()
        for (name, what) in [
            (NSWindow.willEnterFullScreenNotification, "full screen begins"),
            (NSWindow.didEnterFullScreenNotification, "full screen reached"),
            (NSWindow.willExitFullScreenNotification, "leaving full screen begins"),
            (NSWindow.didExitFullScreenNotification, "full screen left"),
        ] {
            NotificationCenter.default.addObserver(forName: name, object: nil, queue: nil) { _ in
                MainActor.assumeIsolated {
                    say(what)
                    measuringUntil = Date().addingTimeInterval(3)
                    look()
                }
            }
        }
        // In every mode of the run loop, so a change made while macOS animates is seen.
        let timer = Timer(timeInterval: 1.0 / 120, repeats: true) { _ in
            MainActor.assumeIsolated { look() }
        }
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
        look()
    }

    private static func say(_ what: String) {
        Bench.say(String(format: "WINDOWS %7.3f  %@", Date().timeIntervalSince(began), what))
    }

    private static func isStrip(_ window: NSWindow) -> Bool {
        String(describing: type(of: window)).contains("ToolbarFullScreen")
    }

    private static func look() {
        var now: [Int: String] = [:]
        for window in NSApp.windows {
            let frame = window.frame
            now[window.windowNumber] = String(
                format: "%@ at %.0f,%.0f size %.0fx%.0f alpha %.2f %@%@",
                String(describing: type(of: window)), frame.minX, frame.minY, frame.width, frame.height,
                window.alphaValue, window.isVisible ? "shown" : "hidden",
                window.contentView?.isHidden == true ? ", what's in it hidden" : "")
        }
        for (number, line) in now.sorted(by: { $0.key < $1.key }) where seen[number] != line {
            say("#\(number) \(line)")
            // A window of macOS's own making, the first time it's seen: what's in it.
            if seen[number] == nil, line.contains("FullScreen"),
                let window = NSApp.windows.first(where: { $0.windowNumber == number })
            {
                inside(window)
            }
        }
        for number in seen.keys.sorted() where now[number] == nil { say("#\(number) gone") }
        seen = now
        if Date() < measuringUntil, Date().timeIntervalSince(lastMeasure) >= 0.05 {
            lastMeasure = Date()
            measureStrip()
        }
    }

    /// What's in a window: its views, and which of them are hidden.
    private static func inside(_ window: NSWindow) {
        guard let top = window.contentView?.superview ?? window.contentView else { return }
        say("   opaque \(window.isOpaque), background \(window.backgroundColor.description), shadow \(window.hasShadow)")
        func tell(_ view: NSView, _ depth: Int) {
            guard depth < 4 else { return }
            say(
                String(repeating: "  ", count: depth + 2)
                    + "\(type(of: view)) \(Int(view.frame.width))x\(Int(view.frame.height))"
                    + (view.isHidden ? " hidden" : "") + (view === window.contentView ? " (content)" : ""))
            view.subviews.forEach { tell($0, depth + 1) }
        }
        tell(top, 0)
    }

    /// How much of the strip's window is drawn at all, as it is on the screen: the window
    /// pictured by itself (a picture of all the app's windows together puts the main one
    /// over it, and the app can't picture the screen), and its dots that aren't
    /// see-through counted. Nothing of any other app's is in the picture.
    private static func measureStrip() {
        for strip in NSApp.windows where strip.isVisible && isStrip(strip) {
            // The window's number itself, not boxed up: that's the list macOS wants.
            var one: [UnsafeRawPointer?] = [UnsafeRawPointer(bitPattern: strip.windowNumber)]
            let (wide, high) = (64, 8)
            guard let only = CFArrayCreate(nil, &one, 1, nil),
                let alone = CGImage(
                    windowListFromArrayScreenBounds: .null, windowArray: only,
                    imageOption: [.nominalResolution, .boundsIgnoreFraming]),
                let space = CGColorSpace(name: CGColorSpace.sRGB),
                let canvas = CGContext(
                    data: nil, width: wide, height: high, bitsPerComponent: 8, bytesPerRow: wide * 4,
                    space: space, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
            else { continue }
            canvas.draw(alone, in: CGRect(x: 0, y: 0, width: wide, height: high))
            guard let dots = canvas.data else { continue }
            var solid = 0
            for dot in 0..<(wide * high) { solid += Int(dots.load(fromByteOffset: dot * 4 + 3, as: UInt8.self)) }
            say(String(format: "the strip's window by itself: %d%% of it is drawn", solid * 100 / (wide * high * 255)))
        }
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
