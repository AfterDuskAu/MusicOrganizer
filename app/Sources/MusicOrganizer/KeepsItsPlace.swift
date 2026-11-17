import SwiftUI

/// A page that scrolls keeps its place when it's made wider or narrower: the lyrics panel
/// beside the pages coming or going (a song with no lyrics), or the window's corner
/// dragged. Left alone, a scroll view keeps its distance from the top, and a grid of cards
/// laid out in more or fewer columns is then showing another part of its list entirely
/// (the owner, 2026-11-17: the cards "all over the place … not even showing the one
/// selected").
///
/// Its place is kept as how far down the page its top is, as a share of the page's
/// length. In a grid of cards all the same height that share is the same card at the
/// top, whatever the number of columns, and going back to the width before comes back to
/// exactly where it was. The share is the one from the owner's own scrolling: the page
/// moving round for a new width, and being put back, doesn't change it. Needs macOS 15
/// (it's told where to scroll to by a distance); on macOS 14 a page is as it was.
struct KeepsItsPlace: ViewModifier {
    func body(content: Content) -> some View {
        if #available(macOS 15, *) {
            content.modifier(PlaceKeeper())
        } else {
            content
        }
    }
}

extension View {
    /// See `KeepsItsPlace`: on a scroll view.
    func keepsItsPlace() -> some View {
        modifier(KeepsItsPlace())
    }
}

@available(macOS 15, *)
private struct PlaceKeeper: ViewModifier {
    /// What the scroll view says, as much as is needed.
    struct Seen: Equatable {
        var top: CGFloat
        var length: CGFloat
        var width: CGFloat
        var height: CGFloat
    }

    /// Kept out of SwiftUI's sight: it changes at every step of a scroll, and nothing
    /// is drawn from it.
    final class Memory {
        /// How far down the page its top was, as a share of its length, when it was
        /// last scrolled.
        var share: CGFloat?
        var width: CGFloat = 0
        /// Until then, a move is the new width's doing, not the owner's.
        var settling = Date.distantPast
        /// The length last scrolled for, while settling: if the page's length comes in
        /// after its width, it's scrolled again for it.
        var aimedAt: CGFloat = 0
    }

    @State private var position = ScrollPosition(edge: .top)
    @State private var memory = Memory()

    func body(content: Content) -> some View {
        content
            .scrollPosition($position)
            .onScrollGeometryChange(for: Seen.self) { scroll in
                Seen(
                    top: scroll.contentOffset.y + scroll.contentInsets.top,
                    length: scroll.contentSize.height, width: scroll.containerSize.width,
                    height: scroll.containerSize.height)
            } action: { _, now in
                see(now)
            }
    }

    private func see(_ now: Seen) {
        if memory.width == 0 { memory.width = now.width }
        if abs(now.width - memory.width) >= 1 {
            memory.width = now.width
            memory.settling = Date().addingTimeInterval(0.5)
            go(now)
        } else if Date() < memory.settling {
            if abs(now.length - memory.aimedAt) >= 1 { go(now) }
        } else if now.length > 0 {
            memory.share = now.top / now.length
        }
    }

    /// To the same share of the page, at its new width.
    private func go(_ now: Seen) {
        memory.aimedAt = now.length
        guard let share = memory.share else { return }
        let most = max(0, now.length - now.height)
        position.scrollTo(y: min(most, max(0, share * now.length)))
    }
}
