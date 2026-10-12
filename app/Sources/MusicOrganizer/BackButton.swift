import SwiftUI

/// The way back from a page opened over another: a channel, a film or a series, a found
/// playlist, an artist. A solid button, every part of it a place to click (the owner,
/// 2026-10-12: with only the arrow's own thin line to hit, "I've missed the click several
/// times when it should be simple"). One for every such page, so they're all alike.
struct BackButton: View {
    var help = "Back"
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Label("Back", systemImage: "chevron.left")
                .font(.body.weight(.semibold))
                .padding(.horizontal, 14)
                .frame(height: 32)
                .background(Theme.current.panel, in: Capsule())
                .overlay { Capsule().strokeBorder(.secondary.opacity(0.4)) }
                .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        // Harmless, so it answers the click that brings the app forward too.
        .takesFirstClick()
        .help(help)
    }
}
