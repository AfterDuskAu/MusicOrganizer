import Foundation

/// The videos a web page plays, as Bear Media's browser window finds them (the owner,
/// 2026-10-18: "if the link hits this error. It offers a open in browser option, and then
/// if you click yes, it pulls the info needed once it opens", and "even if the player is
/// hiding the stream, it searches as many places as possible for it").
///
/// The window is a real browser (Apple's WebKit), opened by the owner's click; the page is
/// looked at as any browser looks at it, and a video it plays is fetched by that browser
/// in its own requests. Nothing pretends to be a browser, and nothing it fetches is handed
/// to another program to fetch again. A video protected against copying (DRM, or a stream
/// whose parts are locked with a key) is never saved.
public enum MediaKind: String, Sendable, Codable {
    /// A whole video file (MP4, WebM, MOV…).
    case file
    /// A playlist of parts (HLS, `.m3u8`).
    case hls
    /// A DASH manifest (`.mpd`).
    case dash
}

/// One place a video was found on a page.
public struct FoundMedia: Identifiable, Hashable, Sendable {
    public let url: URL
    public let kind: MediaKind
    /// Where it was found: "the player", "the page's requests", "the page's code", "the
    /// page's details", "a link".
    public var found: Set<String>
    /// The address of the frame it was found in: what it's fetched from, so it's asked
    /// for as the page's own player asks for it.
    public var frame: String?
    public var contentType: String?

    public init(
        url: URL, kind: MediaKind, found: Set<String> = [], frame: String? = nil,
        contentType: String? = nil
    ) {
        self.url = url
        self.kind = kind
        self.found = found
        self.frame = frame
        self.contentType = contentType
    }

    public var id: String { url.absoluteString }

    /// What it is, in words: "Stream (HLS)", "Stream (DASH)", "MP4 file".
    public var kindName: String {
        switch kind {
        case .hls: "Stream (HLS)"
        case .dash: "Stream (DASH)"
        case .file:
            url.pathExtension.isEmpty ? "Video file" : "\(url.pathExtension.uppercased()) file"
        }
    }
}

public enum MediaLinks {
    /// The endings of whole video files.
    public static let fileEndings: Set<String> = ["mp4", "m4v", "mov", "webm", "mkv", "flv", "ogv"]

    /// What an address is, by its ending or the kind its answer said it was; nil for
    /// anything that isn't a video, a playlist of one, or a manifest.
    public static func kind(of url: URL, contentType: String? = nil) -> MediaKind? {
        guard let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https" else {
            return nil
        }
        let type = (contentType ?? "").lowercased()
        let ending = url.pathExtension.lowercased()
        if ending == "m3u8" || type.contains("mpegurl") { return .hls }
        if ending == "mpd" || type.contains("dash+xml") { return .dash }
        if fileEndings.contains(ending) || type.hasPrefix("video/") { return .file }
        return nil
    }

    /// Whether an address looks like one part of a stream (a segment), not a whole video:
    /// players fetch hundreds of them, and none is worth listing.
    public static func isPart(_ url: URL) -> Bool {
        let path = url.path.lowercased()
        let ending = url.pathExtension.lowercased()
        if ["ts", "m4s", "aac", "m4a", "vtt", "webvtt", "cmfv", "cmfa", "mp3"].contains(ending) {
            return true
        }
        let name = url.deletingPathExtension().lastPathComponent.lowercased()
        let partNames = ["seg", "segment", "chunk", "frag", "fragment", "part", "init"]
        if partNames.contains(where: { name.hasPrefix($0) || name.contains("-\($0)") || name.contains("_\($0)") }),
            name.rangeOfCharacter(from: .decimalDigits) != nil || name.hasPrefix("init")
        {
            return true
        }
        // A byte range asked for in the address itself: a piece of a file.
        let query = (url.query ?? "").lowercased()
        if query.contains("range=") || query.contains("bytes=") || path.contains("/range/") { return true }
        return false
    }

    /// Addresses of videos written anywhere in a page's text: its HTML, its scripts, a
    /// player's settings, the answer of one of its requests. Players often write them
    /// escaped (`https:\/\/…`, `https://…`), and those are found too.
    public static func find(in text: String, base: URL? = nil, most: Int = 200) -> [URL] {
        guard !text.isEmpty else { return [] }
        var found: [URL] = []
        var seen = Set<String>()
        let whole = NSRange(text.startIndex..., in: text)
        for match in linkPattern.matches(in: text, range: whole) {
            guard found.count < most, let range = Range(match.range, in: text) else { break }
            let cleaned = unescape(String(text[range]))
            guard let url = URL(string: cleaned, relativeTo: base)?.absoluteURL,
                kind(of: url) != nil, !isPart(url), seen.insert(url.absoluteString).inserted
            else { continue }
            found.append(url)
        }
        return found
    }

    /// An escaped address put back as it's written: `\/` and `\u002F` are "/", `\u0026`
    /// and `&amp;` are "&", and one written wholly encoded ("https%3A%2F%2F…") is decoded.
    static func unescape(_ text: String) -> String {
        var out = text
        let lower = out.lowercased()
        if lower.hasPrefix("https%3a%2f%2f") || lower.hasPrefix("http%3a%2f%2f") {
            out = out.removingPercentEncoding ?? out
        }
        for (from, to) in [("\\/", "/"), ("\\u002F", "/"), ("\\u002f", "/"), ("\\u0026", "&"), ("&amp;", "&")] {
            out = out.replacingOccurrences(of: from, with: to)
        }
        return out
    }

    private static let linkPattern: NSRegularExpression = {
        // http(s), then "://" written plainly or escaped, then the address's own letters
        // (an escaped "/" among them), ending in a video's or a playlist's ending, with
        // its query kept.
        let slash = #"(?:/|\\/|\\u002[fF]|%2[fF])"#
        let colon = #"(?::|%3[aA])"#
        let letter = #"(?:[^\s"'<>()\\]|\\/|\\u002[fF])"#
        let ending = #"\.(?:m3u8|mpd|mp4|m4v|mov|webm|mkv|flv|ogv)(?![A-Za-z0-9])"#
        let tail = #"(?:\?(?:[^\s"'<>()\\]|\\/|\\u0026)*)?"#
        let pattern = "https?\(colon)\(slash)\(slash)\(letter)*?\(ending)\(tail)"
        return try! NSRegularExpression(pattern: pattern, options: [.caseInsensitive])
    }()

    /// Many addresses that differ only in their numbers, in one folder, are a stream's
    /// parts however they're named: they're left out once there are more than `most`.
    public static func withoutRunsOfParts(_ urls: [URL], most: Int = 6) -> [URL] {
        func shape(_ url: URL) -> String {
            let folder = url.deletingLastPathComponent().absoluteString
            let name = url.lastPathComponent.replacingOccurrences(
                of: #"\d+"#, with: "#", options: .regularExpression)
            return folder + name
        }
        var counts: [String: Int] = [:]
        for url in urls { counts[shape(url), default: 0] += 1 }
        return urls.filter { counts[shape($0), default: 0] <= most }
    }
}

/// What's found on a page, put together: one entry an address, with every place it was
/// found, the parts of streams left out, playlists and manifests first.
public struct PageFindings: Sendable {
    public private(set) var media: [FoundMedia] = []
    /// The page asked for protected (DRM) playback: what it plays can't be saved.
    public private(set) var protected = false
    /// The page plays a stream it builds itself (MediaSource): its parts may come from a
    /// playlist the page fetched, which is looked for among the rest.
    public private(set) var streamCodecs: Set<String> = []
    private var parts: [URL] = []
    /// What a stream read already covers: never listed again, however often it's found.
    private var covered: Set<String> = []

    public init() {}

    /// What one frame said it found: `(address, where, kind of answer)`.
    public mutating func add(_ found: [(url: String, how: String, type: String?)], frame: String?) {
        let base = frame.flatMap(URL.init(string:))
        for one in found {
            guard let url = URL(string: one.url, relativeTo: base)?.absoluteURL else { continue }
            guard let kind = MediaLinks.kind(of: url, contentType: one.type),
                !covered.contains(url.absoluteString)
            else { continue }
            if kind == .file && MediaLinks.isPart(url) { continue }
            if let at = media.firstIndex(where: { $0.url == url }) {
                media[at].found.insert(one.how)
                if media[at].contentType == nil { media[at].contentType = one.type }
                continue
            }
            if kind == .file { parts.append(url) }
            media.append(FoundMedia(url: url, kind: kind, found: [one.how], frame: frame, contentType: one.type))
        }
        // Files that turn out to be a run of numbered pieces are a stream's parts.
        let kept = Set(MediaLinks.withoutRunsOfParts(parts).map(\.absoluteString))
        media.removeAll { $0.kind == .file && !kept.contains($0.url.absoluteString) }
        // Streams first; otherwise in the order they were found.
        media = media.enumerated().sorted {
            (Self.order($0.element), $0.offset) < (Self.order($1.element), $1.offset)
        }.map(\.element)
    }

    public mutating func note(protected: Bool = false, codec: String? = nil) {
        if protected { self.protected = true }
        if let codec { streamCodecs.insert(codec) }
    }

    /// Leave out the addresses that belong to a stream read since: its parts (which can
    /// look like whole files), and each size's own list (the same video as its stream).
    public mutating func leaveOut(_ urls: Set<String>) {
        covered.formUnion(urls)
        media.removeAll { covered.contains($0.url.absoluteString) }
    }

    private static func order(_ media: FoundMedia) -> Int {
        switch media.kind {
        case .hls: 0
        case .dash: 1
        case .file: 2
        }
    }
}
