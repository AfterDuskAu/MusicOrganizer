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

    func forgetAll() { cache.removeAllObjects() }

    /// Already in memory? Asked while a view is built, so a cached cover never flashes
    /// a placeholder.
    func cached(_ track: Track, _ size: CoverSize) -> NSImage? {
        cache.object(forKey: key(track, size))
    }

    func load(_ track: Track, root: URL?, size: CoverSize) async -> NSImage? {
        let key = key(track, size)
        if let image = cache.object(forKey: key) { return image }
        guard track.hasCover else { return nil }
        let pixels = size.rawValue
        let remote = track.artUrl.flatMap(URL.init(string:))
        // Said to be files, not folders: left to find that out for itself, each of these
        // looked on the disk, on the main thread, for every cover not yet in memory
        // (seventy looks for a screenful of songs; profiled 2026-10-09).
        let sidecar = root.flatMap { root in
            track.cover.map { root.appendingPathComponent($0, isDirectory: false) }
        }
        let audio = track.videoId == nil ? root?.appendingPathComponent(track.path, isDirectory: false) : nil
        // A few at a time: a fast scroll asks for dozens of covers at once, and decoding
        // them all together is what made scrolling stutter (Fix A-1). A row that has
        // scrolled away gives up its turn.
        await Self.gate.enter()
        defer { Task { await Self.gate.leave() } }
        if Task.isCancelled { return nil }
        if let image = cache.object(forKey: key) { return image }
        let decoded = await Task.detached(priority: .userInitiated) { () -> CGImage? in
            if let remote {  // a YouTube Music song's picture
                guard remote.scheme == "https",
                    let (data, _) = try? await URLSession.shared.data(from: remote),
                    let source = CGImageSourceCreateWithData(data as CFData, nil)
                else { return nil }
                return Self.thumbnail(source, pixels)
            }
            // Small covers are kept in the app's own cache folder (never the library), so
            // a cover is only cut down from its full size once.
            let original = sidecar ?? audio
            let saved = size == .large ? nil : original.flatMap { Self.savedThumbnail(for: $0, pixels) }
            // Read into memory here, off the main thread: left for later, the picture
            // was unpacked on the main thread each time it was first drawn, which was
            // most of what a screenful of new rows cost (profiled 2026-10-09).
            let unpacked = [kCGImageSourceShouldCacheImmediately: true] as CFDictionary
            if let saved, let source = CGImageSourceCreateWithURL(saved as CFURL, nil),
                let image = CGImageSourceCreateImageAtIndex(source, 0, unpacked)
            {
                return image
            }
            var made: CGImage?
            if let sidecar, let source = CGImageSourceCreateWithURL(sidecar as CFURL, nil) {
                made = Self.thumbnail(source, pixels)
            }
            if made == nil, let audio, let data = await Self.embeddedPicture(audio),
                let source = CGImageSourceCreateWithData(data as CFData, nil)
            {
                made = Self.thumbnail(source, pixels)
            }
            if let made, let saved { Self.save(made, to: saved) }
            return made
        }.value
        guard let decoded else { return nil }
        let image = NSImage(
            cgImage: decoded, size: NSSize(width: decoded.width / 2, height: decoded.height / 2))
        cache.setObject(image, forKey: key, cost: decoded.bytesPerRow * decoded.height)
        return image
    }

    private static let gate = Gate(4)

    /// Where the small copy of this cover is kept: named after the original's path, size
    /// and modified time, so a changed cover gets a new small copy.
    private nonisolated static func savedThumbnail(for original: URL, _ pixels: Int) -> URL? {
        guard let folder = thumbnailFolder,
            let values = try? original.resourceValues(forKeys: [.contentModificationDateKey, .fileSizeKey]),
            let changed = values.contentModificationDate
        else { return nil }
        let name = "\(original.path)|\(values.fileSize ?? 0)|\(changed.timeIntervalSince1970)|\(pixels)"
        var hash: UInt64 = 14_695_981_039_346_656_037  // FNV-1a: a short, stable name
        for byte in name.utf8 { hash = (hash ^ UInt64(byte)) &* 1_099_511_628_211 }
        return folder.appendingPathComponent(String(hash, radix: 16) + ".jpg", isDirectory: false)
    }

    private nonisolated static let thumbnailFolder: URL? = {
        guard let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first
        else { return nil }
        let folder = caches.appendingPathComponent("org.musicorganizer.app/covers", isDirectory: true)
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        return folder
    }()

    private nonisolated static func save(_ image: CGImage, to file: URL) {
        guard let out = CGImageDestinationCreateWithURL(file as CFURL, "public.jpeg" as CFString, 1, nil)
        else { return }
        CGImageDestinationAddImage(
            out, image, [kCGImageDestinationLossyCompressionQuality: 0.85] as CFDictionary)
        CGImageDestinationFinalize(out)
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

/// Lets a fixed number of jobs run at once; the rest wait their turn.
actor Gate {
    private var free: Int
    private var waiting: [CheckedContinuation<Void, Never>] = []

    init(_ slots: Int) { free = slots }

    func enter() async {
        if free > 0 {
            free -= 1
            return
        }
        await withCheckedContinuation { waiting.append($0) }
    }

    func leave() {
        if waiting.isEmpty { free += 1 } else { waiting.removeFirst().resume() }
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
            guard let track else { return }
            if let image = await Covers.shared.load(track, root: model.root, size: size) {
                loaded = (key, image)
            }
        }
    }
}
