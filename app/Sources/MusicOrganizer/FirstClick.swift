import SwiftUI

extension View {
    /// Whether a click on this counts when it's the click that brings the app forward
    /// from behind another app's window.
    ///
    /// macOS asks each thing clicked. A button says yes, of whatever kind (and so do a
    /// song list, the sidebar and a row of tabs); a line that's picked by a click or
    /// played by a double-click says no, so the first click on it did nothing but bring
    /// the window forward. The owner's choice (2026-10-09): the harmless ones answer the
    /// first click, and a button that undoes something at once, with no question asked
    /// (the ✕ on a download that's waiting), doesn't.
    ///
    /// Said of a whole line, it holds for everything in it except what's told `false`
    /// inside. macOS 15 and later; before that everything stays as macOS has it.
    @ViewBuilder
    func takesFirstClick(_ takes: Bool = true) -> some View {
        if #available(macOS 15.0, *) {
            allowsWindowActivationEvents(takes)
        } else {
            self
        }
    }
}
