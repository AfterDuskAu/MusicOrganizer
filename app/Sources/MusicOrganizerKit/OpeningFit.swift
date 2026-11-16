import CoreGraphics

/// Bear Media's loading film, fitted to the window (the owner, 2026-11-16: "The loading video.
/// doesnt match a small window. it should sync to the size of the window"). Until then it
/// always filled the window by cropping what didn't fit, and a window narrower than about 3:2
/// (the owner's, a half of the screen, is taller than it is wide) lost both ends of the name.
///
/// So the film comes in two shapes, drawn by the same code (scripts/make_loading_video.sh):
/// wide (16:9, `Opening.mp4`) and upright (9:16, `Opening-upright.mp4`). A window the wide film
/// can fill with the whole name in it plays that one; any other window plays the upright one.
/// Either fills the window, cropping what's beyond its edges but never the name: where filling
/// would cut the name (a window made narrow while the wide film is playing), the film is as big
/// as the name allows, in the middle of the window, on its own night sky (`OpeningFilmView`).
public enum OpeningShape: String, Sendable, CaseIterable {
    case wide
    case upright

    /// The film's size in points, as scripts/loading_video/render.swift draws it (its pixels
    /// are twice that).
    public var size: CGSize {
        switch self {
        case .wide: CGSize(width: 1920, height: 1080)
        case .upright: CGSize(width: 1080, height: 1920)
        }
    }

    /// How much of the film's width and height the name takes, measured out from the film's
    /// middle so that it stays whole whichever way the film is cropped (the letters and the
    /// bear's ears, as render.swift places them: `Name.box`).
    public var name: CGSize {
        switch self {
        case .wide: CGSize(width: 0.685, height: 0.194)
        case .upright: CGSize(width: 0.745, height: 0.075)
        }
    }

    /// The film's name in the app's resources: `<resource>.mp4`, with its first picture as
    /// `<resource>.jpg`.
    public var resource: String {
        switch self {
        case .wide: "Opening"
        case .upright: "Opening-upright"
        }
    }
}

public enum OpeningFit {
    /// The most of the window the name may take: the rest is sky round it. At 0.84 the name
    /// has at least 8% of the window's width clear on either side.
    public static let mostOfWidth: CGFloat = 0.84
    public static let mostOfHeight: CGFloat = 0.7

    /// Which film a window of this size plays: the wide one when it fills the window with the
    /// whole name in it, otherwise the upright one.
    public static func shape(for window: CGSize) -> OpeningShape {
        fills(.wide, window) ? .wide : .upright
    }

    /// Where the film goes in a window of this size, with the window's corner at 0,0. It fills
    /// the window (reaching past its edges, which crop it) unless the name would then be cut
    /// or crowd the edges; then it's as big as the name allows, in the middle. Never stretched.
    public static func frame(of shape: OpeningShape, in window: CGSize) -> CGRect {
        guard window.width > 0, window.height > 0 else { return .zero }
        let film = shape.size
        let filling = max(window.width / film.width, window.height / film.height)
        let nameAllows = min(
            window.width * mostOfWidth / (film.width * shape.name.width),
            window.height * mostOfHeight / (film.height * shape.name.height))
        let scale = min(filling, nameAllows)
        let size = CGSize(width: film.width * scale, height: film.height * scale)
        return CGRect(
            x: (window.width - size.width) / 2, y: (window.height - size.height) / 2,
            width: size.width, height: size.height)
    }

    /// The film covers the whole window (with the whole name in it).
    public static func fills(_ shape: OpeningShape, _ window: CGSize) -> Bool {
        let frame = frame(of: shape, in: window)
        return frame.minX <= 0.5 && frame.minY <= 0.5
    }
}
