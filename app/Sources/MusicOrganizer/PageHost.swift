import AppKit
import SwiftUI

/// The pages opened so far, each as a view of its own (see `PageHost`). Kept by the
/// window, so the pages outlive anything that's shown over them for a while. The
/// sections of Settings are kept the same way, by the Settings page.
@MainActor
final class PageStore<Page: Hashable> {
    fileprivate var hosts: [Page: NSHostingView<AnyView>] = [:]
    /// The pages told they're showing (a song list works its rows out only then).
    fileprivate var showing: Set<Page> = []
    /// Where the pages that aren't showing wait: a window that's never put on the screen.
    /// A page taken out of every window would count as closed, and be opened afresh
    /// each time it was shown (asking the add-ons and the music service all over again);
    /// in a window of its own it stays open, with nothing laying it out or drawing it.
    fileprivate lazy var parked: NSView = {
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 900, height: 600), styleMask: [.borderless],
            backing: .buffered, defer: true)
        window.isReleasedWhenClosed = false
        window.isExcludedFromWindowsMenu = true
        waitingRoom = window
        return window.contentView ?? NSView()
    }()
    private var waitingRoom: NSWindow?
}

/// Shows the page that's chosen, and keeps every other page opened so far exactly as it
/// was left (scrolled to the same place, with the same selection and sort) without it
/// costing anything while it's out of sight.
///
/// Until 2026-10-08 the pages were all parts of the one window's worth of SwiftUI, the
/// ones not showing moved far off to the side. They were out of sight, but still there:
/// every click anywhere made the window lay out every page opened so far, so the app
/// got slower with each page visited (measured that day on the owner's copy after an
/// hour's use: five clicks on the sidebar kept it busy for 22 of 25 seconds, and one
/// change to the search box for most of eight). Here each page is put in a view of its
/// own, and a page that isn't showing is moved, in one piece, to a window that's never
/// shown (`PageStore.parked`). Nothing lays it out, draws it or tells it the window has
/// changed until it's put back, which is one step however much is on it.
struct PageHost<Page: Hashable>: NSViewRepresentable {
    let current: Page
    let visited: [Page]
    let store: PageStore<Page>
    /// A page, told whether it's the one showing.
    let make: (_ entry: Page, _ showing: Bool) -> AnyView

    func makeNSView(context: Context) -> NSView { NSView() }

    func updateNSView(_ room: NSView, context: Context) {
        for (entry, host) in store.hosts where !visited.contains(entry) && entry != current {
            host.removeFromSuperview()
            store.hosts[entry] = nil
            store.showing.remove(entry)
        }
        for (entry, host) in store.hosts where entry != current {
            // Told once that it isn't showing any more, so it stops working things out
            // (a search typed on another page isn't run on this one too).
            if store.showing.remove(entry) != nil { host.rootView = make(entry, false) }
            if host.superview !== store.parked { store.parked.addSubview(host) }
        }
        show(current, in: room)
    }

    /// Taken apart with the window, or while an album's page covers the pages: they're
    /// kept by the store either way. Only the pages still in this room are put away:
    /// SwiftUI sometimes makes a new room before it takes the old one apart, and the
    /// page showing has moved to the new one by then (put away from there, the app
    /// opened on an empty page: 2026-10-09).
    static func dismantleNSView(_ room: NSView, coordinator: PageStore<Page>) {
        for host in coordinator.hosts.values where host.superview === room {
            coordinator.parked.addSubview(host)
        }
    }

    func makeCoordinator() -> PageStore<Page> { store }

    private func show(_ entry: Page, in room: NSView) {
        let host: NSHostingView<AnyView>
        if let made = store.hosts[entry] {
            host = made
            // What the page is told from outside (a playlist's name) may have changed.
            host.rootView = make(entry, true)
        } else {
            host = NSHostingView(rootView: make(entry, true))
            // The page takes the room it's given: it never tells the window how big to be.
            host.sizingOptions = []
            host.autoresizingMask = [.width, .height]
            store.hosts[entry] = host
        }
        if host.superview !== room {
            host.frame = room.bounds
            room.addSubview(host, positioned: .above, relativeTo: nil)
        }
        store.showing.insert(entry)
    }
}
