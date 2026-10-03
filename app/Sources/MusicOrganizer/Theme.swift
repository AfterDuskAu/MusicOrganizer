import AppKit
import MusicOrganizerKit
import SwiftUI

/// The look the app is dressed in (Settings → App Layout; owner, 2026-10-03).
///
/// It's read once, when the app opens, and stays until the app is opened again. macOS
/// gives an app its highlight colour (a selected row, a ticked box, the ring round a text
/// field) only at that moment, so a look changed halfway would be half one and half the
/// other. Settings says so, and has a button that reopens the app.
///
/// The native look changes nothing: every value here is then macOS's own, so the app is
/// exactly as it was before there were looks. The Warm Look's colours are in the Kit
/// (`WarmPalette`), where a test checks the words can be read on them.
struct Theme {
    let look: AppLook

    static let current = Theme(
        look: AppLook(saved: UserDefaults.standard.string(forKey: AppLook.key)))

    var isWarm: Bool { look == .warm }

    // MARK: surfaces

    /// Behind the pages.
    var page: AnyShapeStyle {
        isWarm ? AnyShapeStyle(Color(WarmPalette.page)) : AnyShapeStyle(.clear)
    }

    /// Behind the sidebar.
    var sidebar: AnyShapeStyle {
        isWarm ? AnyShapeStyle(Color(WarmPalette.sidebar)) : AnyShapeStyle(.clear)
    }

    /// The player bar along the bottom.
    var bar: AnyShapeStyle {
        isWarm ? AnyShapeStyle(Color(WarmPalette.bar)) : AnyShapeStyle(.bar)
    }

    /// A panel beside or on a page (the lyrics beside the library, a list's footer).
    var panel: AnyShapeStyle {
        isWarm ? AnyShapeStyle(Color(WarmPalette.panel)) : AnyShapeStyle(.background.secondary)
    }

    /// Whether a list or table draws macOS's own background behind its rows. The Warm
    /// Look takes it away, so the page's colour shows through.
    var listBackground: Visibility { isWarm ? .hidden : .automatic }

    // MARK: words

    /// The colour of words; nil leaves them to macOS.
    var text: Color? { isWarm ? Color(WarmPalette.text) : nil }

    /// The type a heading is set in; nil is macOS's own.
    var headingDesign: Font.Design? { isWarm ? .serif : nil }

    // MARK: what macOS has to be told

    /// Where macOS keeps an app's own highlight colour, when it has one.
    private static let highlightKey = "AppleAccentColor"

    /// Called once, before any window is made.
    @MainActor
    func putOn() {
        Theme.saveHighlight(for: look)
        if isWarm { NSApplication.shared.appearance = NSAppearance(named: .darkAqua) }
    }

    /// The highlight colour that goes with a look, saved among the app's own settings
    /// (nothing of the Mac's is touched): orange for the Warm Look, and for the native
    /// look none, which is whatever the Mac is set to. macOS reads it when the app opens.
    static func saveHighlight(for look: AppLook) {
        let saved = UserDefaults.standard
        if look == .warm {
            saved.set(WarmPalette.highlight, forKey: highlightKey)
        } else {
            saved.removeObject(forKey: highlightKey)
        }
    }

    /// False when this copy of the app can't open itself again (one started from the
    /// build folder, not the app).
    @MainActor
    static var canReopen: Bool { Bundle.main.bundleURL.pathExtension == "app" }

    /// What the helper runs, given this app's process number and where the app is: wait
    /// for the app to be gone (giving up after a quarter of a minute, so an app that
    /// didn't quit isn't opened again much later), then open it.
    static let waitThenOpen =
        "n=0; while kill -0 \"$1\" 2>/dev/null && [ $n -lt 75 ]; do sleep 0.2; n=$((n+1)); done; "
        + "kill -0 \"$1\" 2>/dev/null || { sleep 0.5; open \"$2\"; }"

    /// Quit, and open again in the look that's been chosen.
    @MainActor
    static func reopen() {
        guard canReopen else { return }
        // A small helper waits for this app (and its engine, which lets go of the
        // library as it stops) to be gone, then opens the app again.
        let helper = Process()
        helper.executableURL = URL(fileURLWithPath: "/bin/sh")
        helper.arguments = [
            "-c", Theme.waitThenOpen,
            "sh", String(ProcessInfo.processInfo.processIdentifier), Bundle.main.bundleURL.path,
        ]
        guard (try? helper.run()) != nil else { return }
        NSApplication.shared.terminate(nil)
    }
}

extension Color {
    init(_ colour: RGB) {
        self.init(.sRGB, red: colour.red, green: colour.green, blue: colour.blue)
    }
}

extension NSColor {
    convenience init(_ colour: RGB) {
        self.init(srgbRed: colour.red, green: colour.green, blue: colour.blue, alpha: 1)
    }
}

extension View {
    /// A window's or a sheet's whole content, in the look: its words' colour, and a
    /// surface behind it. Natively nothing changes.
    @ViewBuilder
    func dressed(_ surface: KeyPath<Theme, AnyShapeStyle> = \.page) -> some View {
        if let text = Theme.current.text {
            foregroundStyle(text).background(Theme.current[keyPath: surface])
        } else {
            self
        }
    }

    /// Words in the look's colour, for the places that don't take it from their window
    /// (a table's cells are drawn apart from the page they're on).
    @ViewBuilder
    func lookText() -> some View {
        if let text = Theme.current.text { foregroundStyle(text) } else { self }
    }

    /// A page's main button (Play): filled with the highlight colour in the Warm Look.
    @ViewBuilder
    func mainButton() -> some View {
        if Theme.current.isWarm { buttonStyle(.borderedProminent) } else { self }
    }

    /// For a page that scrolls right up to the title bar. The Warm Look's title bar is
    /// see-through (the glow shows in it), so a list must stop below it, not slide under
    /// its buttons. Natively the title bar hides what's under it, and nothing changes.
    @ViewBuilder
    func belowTitleBar() -> some View {
        if Theme.current.isWarm { padding(.top, 1).clipped() } else { self }
    }

    /// A heading: a page's or a section's title.
    func heading() -> some View { fontDesign(Theme.current.headingDesign) }
}

/// Tells the window it's put in to wear the look: the window's own colour behind
/// everything, and a title bar with no strip of its own, so the page's colour runs to the
/// top. Natively it does nothing.
struct WindowLook: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView { Finder() }

    func updateNSView(_ view: NSView, context: Context) {}

    private final class Finder: NSView {
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            guard Theme.current.isWarm, let window else { return }
            window.backgroundColor = NSColor(WarmPalette.page)
            window.titlebarAppearsTransparent = true
            window.titlebarSeparatorStyle = .none
        }
    }
}

/// The Warm Look's glow, from the very top of the window down through the title bar and a
/// page's heading: amber while nothing plays, and the colours of the cover of the song
/// that's playing, left to right as they are on the cover. Natively nothing is drawn.
struct CoverWash: View {
    @Environment(AppModel.self) private var model
    @State private var colours = [Color(WarmPalette.glow)]
    /// The title bar and a page's heading. It has faded out by the first row of a list,
    /// whose own heading (Title, Artist, Album) isn't see-through.
    static let height: CGFloat = 96

    var body: some View {
        if Theme.current.isWarm {
            let track = model.player.current
            VStack(spacing: 0) {
                LinearGradient(colors: colours, startPoint: .leading, endPoint: .trailing)
                    .mask {
                        LinearGradient(
                            colors: [.black.opacity(0.5), .black.opacity(0.25), .clear],
                            startPoint: .top, endPoint: .bottom)
                    }
                    .frame(height: CoverWash.height)
                Spacer(minLength: 0)
            }
            .allowsHitTesting(false)
            .animation(.easeInOut(duration: 0.8), value: colours)
            .task(id: track?.id) {
                var found: [RGB] = []
                if let track,
                    let cover = await Covers.shared.load(track, root: model.root, size: .small)
                {
                    found = CoverWash.colours(of: cover)
                }
                colours = found.isEmpty
                    ? [Color(WarmPalette.glow)]
                    : found.map { Color($0.glow(plain: WarmPalette.glow)) }
            }
        }
    }

    /// A cover's colours across its width: the picture brought down to a few points, each
    /// the average of its part of the cover.
    static func colours(of cover: NSImage, points: Int = 4) -> [RGB] {
        guard let image = cover.cgImage(forProposedRect: nil, context: nil, hints: nil),
            let context = CGContext(
                data: nil, width: points, height: 1, bitsPerComponent: 8, bytesPerRow: points * 4,
                space: CGColorSpace(name: CGColorSpace.sRGB) ?? CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)
        else { return [] }
        context.interpolationQuality = .high
        context.draw(image, in: CGRect(x: 0, y: 0, width: points, height: 1))
        guard let data = context.data else { return [] }
        let bytes = data.bindMemory(to: UInt8.self, capacity: points * 4)
        return (0..<points).map { point in
            RGB(
                red: Double(bytes[point * 4]) / 255, green: Double(bytes[point * 4 + 1]) / 255,
                blue: Double(bytes[point * 4 + 2]) / 255)
        }
    }
}
