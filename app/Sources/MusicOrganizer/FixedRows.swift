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

        init(height: CGFloat) {
            self.height = height
            super.init(frame: .zero)
        }

        @available(*, unavailable)
        required init?(coder: NSCoder) { fatalError("not used") }

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            DispatchQueue.main.async { [weak self] in self?.apply() }
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
                    if abs(theirs.minX - mine.minX) < 2, abs(theirs.minY - mine.minY) < 2,
                        abs(theirs.width - mine.width) < 2, abs(theirs.height - mine.height) < 2
                    {
                        if table.usesAutomaticRowHeights || table.rowHeight != height {
                            table.usesAutomaticRowHeights = false
                            table.rowHeight = height
                        }
                        return
                    }
                }
                ancestor = view.superview
                climbed += 1
            }
        }

        private static func tables(in view: NSView) -> [NSTableView] {
            if let table = view as? NSTableView { return [table] }
            return view.subviews.flatMap(tables(in:))
        }
    }
}
