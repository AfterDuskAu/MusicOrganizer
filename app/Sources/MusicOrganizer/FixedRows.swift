import AppKit
import SwiftUI

/// Gives the table it sits behind one fixed row height.
///
/// A SwiftUI `Table` on the Mac is an AppKit table that, left alone, measures the height
/// of every row as it comes into view by laying out all its cells. Profiling (Fix A-1,
/// 2026-10-02) showed that measuring was nearly all of the freeze on each click and
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

        /// The nearest table: the first one inside the closest ancestor that has one.
        func apply() {
            var ancestor = superview
            while let view = ancestor {
                if let table = Self.table(in: view) {
                    if table.usesAutomaticRowHeights || table.rowHeight != height {
                        table.usesAutomaticRowHeights = false
                        table.rowHeight = height
                    }
                    return
                }
                ancestor = view.superview
            }
        }

        private static func table(in view: NSView) -> NSTableView? {
            if let table = view as? NSTableView { return table }
            for child in view.subviews {
                if let found = table(in: child) { return found }
            }
            return nil
        }
    }
}
