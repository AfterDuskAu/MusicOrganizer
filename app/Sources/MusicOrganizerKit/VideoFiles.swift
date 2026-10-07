import Foundation

/// Video files on this Mac that the film player can open: what Videos → Movies lists
/// from the Movies folder. Only looked at, never changed.
public enum VideoFiles {
    public struct File: Identifiable, Hashable, Sendable {
        public let url: URL
        public let bytes: Int64
        public var id: URL { url }
        /// The file's name without its ending: what the list shows.
        public var name: String { url.deletingPathExtension().lastPathComponent }
        public var kind: String { url.pathExtension.uppercased() }
    }

    /// The endings of the kinds of file listed. The player itself opens more than these.
    public static let endings: Set<String> = [
        "mkv", "mp4", "m4v", "mov", "avi", "webm", "wmv", "flv", "mpg", "mpeg", "ts", "m2ts",
        "3gp", "ogv",
    ]

    public static func isVideo(_ url: URL) -> Bool {
        endings.contains(url.pathExtension.lowercased())
    }

    /// The video files in a folder and the folders inside it, by name. Hidden files,
    /// and what's inside a package (a Final Cut library, say), are left alone.
    public static func inside(_ folder: URL) -> [File] {
        let keys: [URLResourceKey] = [.isRegularFileKey, .fileSizeKey]
        guard
            let walker = FileManager.default.enumerator(
                at: folder, includingPropertiesForKeys: keys,
                options: [.skipsHiddenFiles, .skipsPackageDescendants])
        else { return [] }
        var found: [File] = []
        for case let url as URL in walker where isVideo(url) {
            let values = try? url.resourceValues(forKeys: Set(keys))
            guard values?.isRegularFile == true else { continue }
            found.append(File(url: url, bytes: Int64(values?.fileSize ?? 0)))
        }
        return found.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }
}
