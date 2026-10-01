import AVFoundation
import AppKit
import ImageIO
import MusicOrganizerKit
import SwiftUI

enum CoverSize: Int {
    case small = 96  // rows and the player bar
    case medium = 400  // the albums grid
    case large = 1000  // an album's page
}

/// Covers, read from the library (never written) and kept in memory at the size shown.
/// The album folder's `cover.jpg` comes first; a file's own embedded picture second.
@MainActor
final class Covers {
    static let shared = Covers()
    private let cache = NSCache<NSString, NSImage>()

    private init() {
        cache.totalCostLimit = 200 * 1024 * 1024
    }

    private func key(_ track: Track, _ size: CoverSize) -> NSString {
        "\(track.cover ?? track.path)|\(size.rawValue)" as NSString
    }

    /// Already in memory? Asked while a view is built, so a cached cover never flashes
    /// a placeholder.
    func cached(_ track: Track, _ size: CoverSize) -> NSImage? {
        cache.object(forKey: key(track, size))
    }

    func load(_ track: Track, root: URL, size: CoverSize) async -> NSImage? {
        let key = key(track, size)
        if let image = cache.object(forKey: key) { return image }
        guard track.hasCover else { return nil }
        let sidecar = track.cover.map { root.appendingPathComponent($0) }
        let audio = root.appendingPathComponent(track.path)
        let pixels = size.rawValue
        let decoded = await Task.detached(priority: .userInitiated) { () -> CGImage? in
            if let sidecar, let source = CGImageSourceCreateWithURL(sidecar as CFURL, nil),
                let image = Self.thumbnail(source, pixels)
            {
                return image
            }
            guard let data = await Self.embeddedPicture(audio),
                let source = CGImageSourceCreateWithData(data as CFData, nil)
            else { return nil }
            return Self.thumbnail(source, pixels)
        }.value
        guard let decoded else { return nil }
        let image = NSImage(
            cgImage: decoded, size: NSSize(width: decoded.width / 2, height: decoded.height / 2))
        cache.setObject(image, forKey: key, cost: decoded.bytesPerRow * decoded.height)
        return image
    }

    private nonisolated static func thumbnail(_ source: CGImageSource, _ pixels: Int) -> CGImage? {
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceShouldCacheImmediately: true,
            kCGImageSourceThumbnailMaxPixelSize: pixels,
        ]
        return CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary)
    }

    private nonisolated static func embeddedPicture(_ audio: URL) async -> Data? {
        let asset = AVURLAsset(url: audio)
        guard let metadata = try? await asset.load(.commonMetadata) else { return nil }
        let pictures = AVMetadataItem.metadataItems(
            from: metadata, filteredByIdentifier: .commonIdentifierArtwork)
        guard let picture = pictures.first else { return nil }
        return try? await picture.load(.dataValue)
    }
}

/// A square cover for a track's album, or a quiet placeholder.
struct CoverView: View {
    let track: Track?
    let size: CoverSize
    var corner: CGFloat = 6

    @Environment(AppModel.self) private var model
    @State private var loaded: (key: String, image: NSImage)?

    private var key: String { "\(track?.cover ?? track?.path ?? "")|\(size.rawValue)" }

    var body: some View {
        let image = loaded?.key == key ? loaded?.image : track.flatMap { Covers.shared.cached($0, size) }
        ZStack {
            if let image {
                Image(nsImage: image)
                    .resizable()
                    .aspectRatio(contentMode: .fill)
            } else {
                Rectangle().fill(.quaternary)
                Image(systemName: "music.note")
                    .font(size == .small ? .body : .largeTitle)
                    .foregroundStyle(.tertiary)
            }
        }
        .aspectRatio(1, contentMode: .fit)
        .clipShape(RoundedRectangle(cornerRadius: corner))
        .task(id: key) {
            guard let track, let root = model.root else { return }
            if let image = await Covers.shared.load(track, root: root, size: size) {
                loaded = (key, image)
            }
        }
    }
}
