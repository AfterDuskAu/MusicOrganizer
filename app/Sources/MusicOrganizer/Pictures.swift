import AppKit
import ImageIO

/// Pictures from the web (films' and series' posters, channels' pictures, videos' frames, people's
/// photos), decoded once and kept ready to show (`WebPicture`). The owner, 2026-11-07, as on the
/// iPad: smoother. Each is fetched, then decoded by ImageIO off the main thread, no bigger than
/// the frame it's drawn in, and kept in memory while there's room, so a card scrolled back into a
/// list shows its picture at once. Until then it was AsyncImage, which fetched and decoded a
/// picture again, at its full size, every time its card came back into view. The files are kept
/// by URLSession's own cache, as before; nothing is written.
@MainActor
enum Pictures {
    private static let kept: NSCache<NSString, NSImage> = {
        let cache = NSCache<NSString, NSImage>()
        cache.totalCostLimit = 256 << 20  // bytes of decoded pictures: about 260 posters
        return cache
    }()

    /// A few decoded at once, as `Covers` does: a fast scroll asks for dozens, and decoding them
    /// all together is what makes scrolling stutter.
    private static let gate = Gate(4)

    /// What a picture is kept by: its address, and the frame it's drawn in.
    static func key(_ address: String, size: CGSize?) -> String {
        guard let size else { return "full|\(address)" }
        return "\(Int(size.width))x\(Int(size.height))|\(address)"
    }

    /// The picture, if it's ready now (nothing is fetched or decoded).
    static func ready(_ address: String, size: CGSize?) -> NSImage? {
        kept.object(forKey: key(address, size: size) as NSString)
    }

    /// The picture, fetched and decoded off the main thread, then kept. Nil when it can't be had
    /// (or the card asking has gone).
    static func load(_ url: URL, address: String, size: CGSize?) async -> NSImage? {
        let name = key(address, size: size) as NSString
        if let found = kept.object(forKey: name) { return found }
        guard let data = await fetched(url), !Task.isCancelled else { return nil }
        await gate.enter()
        defer { Task { await gate.leave() } }
        // A card that has scrolled away gives up its turn.
        if Task.isCancelled { return nil }
        if let found = kept.object(forKey: name) { return found }
        // In pixels on a Retina screen, as `Covers` and `WebPictures.sized` count them.
        let frame = size.map { CGSize(width: $0.width * 2, height: $0.height * 2) }
        let screen = largestScreen()
        let decoded = await Task.detached(priority: .userInitiated) {
            Self.decoded(data, filling: frame, most: screen)
        }.value
        guard let decoded else { return nil }
        let image = NSImage(
            cgImage: decoded,
            size: NSSize(width: CGFloat(decoded.width) / 2, height: CGFloat(decoded.height) / 2))
        kept.setObject(image, forKey: name, cost: decoded.bytesPerRow * decoded.height)
        return image
    }

    /// The picture's bytes, or nil when they can't be had: no answer, or an answer that isn't one
    /// (the address has gone).
    private static func fetched(_ url: URL) async -> Data? {
        guard let (data, answer) = try? await URLSession.shared.data(from: url) else { return nil }
        if let http = answer as? HTTPURLResponse, !(200..<300).contains(http.statusCode) { return nil }
        return data
    }

    /// The longest side of the largest screen, in pixels: a picture with no frame of its own (a
    /// film page's background) is decoded no bigger.
    private static func largestScreen() -> Int {
        let sides = NSScreen.screens.map { max($0.frame.width, $0.frame.height) * $0.backingScaleFactor }
        return Int(sides.max() ?? 5120)
    }

    /// Decoded just big enough to fill `frame` (cut to fill it, as it's drawn), or no bigger than
    /// `most` pixels on its longest side when there's no frame; never bigger than it is. Drawn
    /// once now (`kCGImageSourceShouldCacheImmediately`), so showing it costs nothing more.
    private nonisolated static func decoded(_ data: Data, filling frame: CGSize?, most: Int) -> CGImage? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil) else { return nil }
        var longest = most
        if let own = pixelSize(source) {
            let ownLongest = max(own.width, own.height)
            if let frame, own.width > 0, own.height > 0 {
                let scale = max(frame.width / own.width, frame.height / own.height)
                longest = Int((ownLongest * scale).rounded(.up))
            }
            longest = min(longest, Int(ownLongest))
        }
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceShouldCacheImmediately: true,
            kCGImageSourceThumbnailMaxPixelSize: max(longest, 1),
        ]
        return CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary)
    }

    /// The picture's size as it's shown (turned the way its own details say), read without
    /// decoding it.
    private nonisolated static func pixelSize(_ source: CGImageSource) -> CGSize? {
        guard let details = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
            let width = details[kCGImagePropertyPixelWidth] as? Int,
            let height = details[kCGImagePropertyPixelHeight] as? Int
        else { return nil }
        let turned = (details[kCGImagePropertyOrientation] as? Int).map { $0 >= 5 } ?? false
        return turned
            ? CGSize(width: height, height: width)
            : CGSize(width: width, height: height)
    }
}
