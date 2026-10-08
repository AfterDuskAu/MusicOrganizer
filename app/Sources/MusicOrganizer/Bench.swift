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
/// - `hide`, `show`: the app hidden, and brought back
/// - `shrink`, `grow`: the window put in the Dock, and brought back
/// - `wait`: nothing, to see what the app does by itself
///
/// `/<seconds>` after a step waits that long before the next (4 otherwise):
/// `youtube/10`.
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
    /// `MUSICORG_PAGES=<way>`: how the pages that aren't showing are kept, to compare
    /// (`PageHost`). "stacked" is the way it was until 2026-10-08 (all in the window,
    /// moved aside); "hidden" leaves each in the window, hidden; "detached" takes them
    /// out of every window. Anything else is the way the app works: parked in a window
    /// nobody sees.
    static let pages = ProcessInfo.processInfo.environment["MUSICORG_PAGES"] ?? ""
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

    /// The steps that aren't a page: true if this was one.
    static func took(_ name: String) -> Bool {
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
