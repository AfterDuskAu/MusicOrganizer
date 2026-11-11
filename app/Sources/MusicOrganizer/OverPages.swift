import AppKit
import MusicOrganizerKit
import SwiftUI

/// A page opened over a page of the sidebar (`AppModel.trail`): a film's or a series' page,
/// a channel's, or a series in the Movies folder that no plugin knows (its files).
enum OverPage: Identifiable, Hashable {
    /// `apart`: from the Finder kept apart. `fromShelf`: from Videos → Movies/Series, where a
    /// series opens at the first season with an episode on this Mac.
    case title(MediaItem, apart: Bool = false, fromShelf: Bool = false)
    case channel(ChannelRef)
    /// A card of Videos → Movies/Series, by its key.
    case keptFolder(String)

    var id: String {
        switch self {
        case .title(let item, _, _): "title:\(item.id)"
        case .channel(let channel): "channel:\(channel.channelId)"
        case .keptFolder(let key): "folder:\(key)"
        }
    }
}

/// A page of the sidebar with what's been opened over it, the last opened showing (the
/// owner, 2026-11-11: "Watchlist>Movie>(back)> Watchlist, in the same spot it was at. Or
/// calendar> Series > (back) > Calendar"). Everything under the top stays as it was left,
/// out of sight and out of reach, so Back finds it scrolled to the same place, with the
/// same season, episode and cast member open. Until then a title opened from the
/// Watchlist, the Calendar or Home was opened in its Finder, and a Finder's or a channel's
/// list was made afresh when its page was shut.
struct OverPages<Base: View>: View {
    let entry: SidebarItem
    /// Whether this page of the sidebar is the one showing.
    let active: Bool
    @ViewBuilder let base: () -> Base
    @Environment(AppModel.self) private var model

    /// One page of the trail, told apart by where it is as well as what it is: the page at
    /// a place in the trail is a view of its own for each title (opened over another, a
    /// film's page kept the last one's trailer and episode: 2026-10-15).
    private struct Layer: Hashable {
        let at: Int
        let page: OverPage
    }

    var body: some View {
        let pages = model.trail.pages(on: entry)
        let layers = pages.enumerated().map { Layer(at: $0.offset, page: $0.element) }
        ZStack {
            base()
                .covered(!pages.isEmpty)
            ForEach(layers, id: \.self) { layer in
                let top = layer.at == pages.count - 1
                OverPageView(
                    page: layer.page, active: active && top,
                    backTo: layer.at == 0 ? entry.title : pages[layer.at - 1].name(in: model)
                ) {
                    model.goBack(on: entry)
                }
                .covered(!top)
            }
        }
        // Let go of a search box under it, so its ring isn't drawn and typing goes nowhere.
        .onChange(of: pages.count) { before, now in
            if now > before { NSApp.keyWindow?.makeFirstResponder(nil) }
        }
    }
}

extension OverPage {
    /// What it's called, for the tooltip of the Back of a page over it.
    @MainActor
    func name(in model: AppModel) -> String {
        switch self {
        case .title(let item, _, _): item.name
        case .channel(let channel): channel.name
        case .keptFolder(let key): model.keptShelf.all.first { $0.key == key }?.name ?? "the series"
        }
    }
}

extension View {
    /// Under another page: out of sight, out of reach and out of macOS's accessibility, but
    /// still there, scrolled where it was.
    fileprivate func covered(_ yes: Bool) -> some View {
        opacity(yes ? 0 : 1)
            .allowsHitTesting(!yes)
            .accessibilityHidden(yes)
    }
}

/// One page of a trail.
private struct OverPageView: View {
    let page: OverPage
    /// The page on top, on the page of the sidebar that's showing.
    let active: Bool
    /// What Back goes to, for its tooltip.
    let backTo: String
    let back: () -> Void
    @Environment(AppModel.self) private var model

    var body: some View {
        switch page {
        case .title(let item, let apart, let fromShelf):
            MovieView(
                film: item, apart: apart, startsAtDownloaded: fromShelf, active: active,
                backHelp: "Back to \(backTo)", back: back)
        case .channel(let channel):
            ChannelView(channel: channel, backHelp: "Back to \(backTo)", back: back)
        case .keptFolder(let key):
            if let card = model.keptShelf.all.first(where: { $0.key == key }) {
                KeptFolderView(card: card, backHelp: "Back to \(backTo)", back: back)
            } else {
                // Its files moved or binned since: nothing of it to show.
                VStack(alignment: .leading, spacing: 10) {
                    BackButton(help: "Back to \(backTo)", action: back)
                    Text("Its files aren't in your Movies folder any more.").foregroundStyle(.secondary)
                    Spacer()
                }
                .padding(20)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }
}
