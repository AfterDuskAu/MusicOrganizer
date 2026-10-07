import AppKit
import SwiftUI

/// Gives the table it sits behind one fixed row height.
///
/// A SwiftUI `Table` on the Mac is an AppKit table that, left alone, measures the height
/// of every row as it comes into view by laying out all its cells. Profiling (Fix A-1,
/// 2026-10-01) showed that measuring was nearly all of the freeze on each click and
/// scroll. Our rows are all the same height, so the table is told the height instead.
struct FixedRows: NSViewRepresentable {
    let height: CGFloat

    func makeNSView(context: Context) -> Finder { Finder(height: height) }

    func updateNSView(_ view: Finder, context: Context) {
        view.height = height
        view.apply()
    }

    final class Finder: NSView {
        var height: CGFloat
        private weak var watched: NSTableView?
        private var watching: [NSKeyValueObservation] = []
        private var fixing = false

        init(height: CGFloat) {
            self.height = height
            super.init(frame: .zero)
        }

        @available(*, unavailable)
        required init?(coder: NSCoder) { fatalError("not used") }

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            apply()
            DispatchQueue.main.async { [weak self] in self?.apply() }
            lookAgain(after: 0.05, tries: 8)
        }

        // The table is known by filling the same space as this view, which is only true
        // once the page has been laid out. Asked just once, straight after the page was
        // made, the answer was often "not yet", and nothing asked again until something
        // else changed about a second later: until then the rows were the table's own,
        // taller, height, and then shrank (the owner, 2026-10-07). So it's asked again
        // whenever this view is given its place, and a few times shortly after.
        override func setFrameSize(_ newSize: NSSize) {
            super.setFrameSize(newSize)
            if watched == nil { apply() }
        }

        override func setFrameOrigin(_ newOrigin: NSPoint) {
            super.setFrameOrigin(newOrigin)
            if watched == nil { apply() }
        }

        private func lookAgain(after seconds: TimeInterval, tries: Int) {
            guard tries > 0 else { return }
            DispatchQueue.main.asyncAfter(deadline: .now() + seconds) { [weak self] in
                guard let self, self.watched == nil else { return }
                self.apply()
                self.lookAgain(after: seconds, tries: tries - 1)
            }
        }

        /// Find the table this view sits behind and fix its row height. It must be that
        /// table and no other: the first version took the first table it came across,
        /// which could be another page's list (the YouTube results, whose taller rows
        /// were then squashed). So a table only counts if it has several columns, as a
        /// song table does and a plain list doesn't, and fills exactly the space this
        /// view fills.
        func apply() {
            guard window != nil else { return }
            let mine = convert(bounds, to: nil)
            var ancestor = superview
            var climbed = 0
            while let view = ancestor, climbed < 12 {
                for table in Self.tables(in: view) where table.numberOfColumns > 1 {
                    guard let scroll = table.enclosingScrollView else { continue }
                    let theirs = scroll.convert(scroll.bounds, to: nil)
                    // (Taking hold of the table before its first layout, when it's
                    // found close by but not yet in place, halved the wait on first
                    // opening a list, but the table then showed only its Title column:
                    // tried and taken out, 2026-10-07. It's held once it's in place.)
                    if abs(theirs.minX - mine.minX) < 2, abs(theirs.minY - mine.minY) < 2,
                        abs(theirs.width - mine.width) < 2, abs(theirs.height - mine.height) < 2
                    {
                        hold(table)
                        return
                    }
                }
                ancestor = view.superview
                climbed += 1
            }
        }

        /// Set the height, and keep it. SwiftUI puts its own height back whenever the
        /// selection changes (seen 2026-10-02: the rows went from 34 points to 24 at
        /// the first click, squashed together with their covers cut off), so the
        /// table's height is watched and put straight back.
        private func hold(_ table: NSTableView) {
            fix(table)
            guard watched !== table else { return }
            watched = table
            watching = [
                table.observe(\.rowHeight) { [weak self] table, _ in self?.fix(table) },
                table.observe(\.usesAutomaticRowHeights) { [weak self] table, _ in
                    self?.fix(table)
                },
            ]
        }

        private func fix(_ table: NSTableView) {
            // Setting either one tells the watchers above, which come back here.
            guard !fixing else { return }
            fixing = true
            defer { fixing = false }
            if table.usesAutomaticRowHeights { table.usesAutomaticRowHeights = false }
            if table.rowHeight != height { table.rowHeight = height }
        }

        private static func tables(in view: NSView) -> [NSTableView] {
            if let table = view as? NSTableView { return [table] }
            return view.subviews.flatMap(tables(in:))
        }
    }
}
