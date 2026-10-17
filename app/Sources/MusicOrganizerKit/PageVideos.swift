import Foundation

/// The videos on a web page the owner pasted on the URL page (the owner, 2026-10-17: "add
/// in a "URL" which would get the video from the page from any website, shows which videos
/// are on the page, once selected and quality are selected. use yt-dlp engine to
/// download"), as the engine reads them (`page.videos`). Nothing is downloaded until the
/// owner chooses one, a quality and a folder, and clicks Download (`page.keep`).
public struct PageAnswer: Decodable, Equatable, Sendable {
    public let title: String?
    public let videos: [PageVideo]
    /// A sentence about what was or wasn't found, already in the app's words.
    public let note: String?

    public init(title: String?, videos: [PageVideo], note: String?) {
        self.title = title
        self.videos = videos
        self.note = note
    }
}

public struct PageVideo: Decodable, Equatable, Hashable, Sendable, Identifiable {
    /// What it's asked for by again: its own address, or its page's.
    public let url: String
    /// Its place on the page, for one with no address of its own.
    public let item: Int?
    public let title: String?
    public let durationS: Double?
    public let thumbnail: String?
    public let site: String?
    /// The sizes it can be downloaded in, sharpest first. Nil: not read yet (a player from
    /// another site on the page): `page.videos` with its `url` reads it.
    public let qualities: [PageQuality]?
    /// "youtube" or "soundcloud": downloaded elsewhere, never from this page.
    public let service: String?
    /// The music service's id for it, which the queue downloads.
    public let videoId: String?

    public init(
        url: String, item: Int? = nil, title: String? = nil, durationS: Double? = nil,
        thumbnail: String? = nil, site: String? = nil, qualities: [PageQuality]? = nil,
        service: String? = nil, videoId: String? = nil
    ) {
        self.url = url
        self.item = item
        self.title = title
        self.durationS = durationS
        self.thumbnail = thumbnail
        self.site = site
        self.qualities = qualities
        self.service = service
        self.videoId = videoId
    }

    public var id: String { "\(url)#\(item ?? 0)" }

    /// From the music service: downloaded through the queue, into Videos.
    public var isMusicService: Bool { service == "youtube" }

    /// Not readable here at all (SoundCloud's, which Music Finder downloads).
    public var isElsewhere: Bool { service != nil && !isMusicService }

    /// What the list calls it: its title, or where it's from when the page didn't say.
    public var name: String {
        if let title, !title.isEmpty { return title }
        if isMusicService { return "A video from the music service" }
        return "A video from \(site ?? "this page")"
    }

    /// Where it's from, in the app's words (never the music service's name).
    public var from: String? {
        isMusicService ? "The music service" : site
    }

    /// Its length as a clock shows it, "4:05" or "1:02:03"; empty when it isn't known.
    public var length: String {
        guard let durationS, durationS > 0 else { return "" }
        let whole = Int(durationS.rounded())
        let (hours, minutes, seconds) = (whole / 3600, whole / 60 % 60, whole % 60)
        return hours > 0
            ? String(format: "%d:%02d:%02d", hours, minutes, seconds)
            : String(format: "%d:%02d", minutes, seconds)
    }

    /// The name it's saved under: its title, or the page's when it has none.
    public func saveName(page: String?) -> String {
        if let title, !title.isEmpty { return title }
        if let page, !page.isEmpty { return page }
        return site ?? "Video"
    }

    /// The same video with its qualities read (`page.videos` on its own address).
    public func read(_ found: PageVideo) -> PageVideo {
        PageVideo(
            url: found.url, item: found.item, title: found.title ?? title,
            durationS: found.durationS ?? durationS, thumbnail: found.thumbnail ?? thumbnail,
            site: found.site ?? site, qualities: found.qualities, service: found.service,
            videoId: found.videoId)
    }
}

public struct PageQuality: Decodable, Equatable, Hashable, Sendable, Identifiable {
    /// What the engine is given back (`page.keep`'s `quality`).
    public let id: String
    public let label: String
    public let height: Int?
    public let size: Int?

    public init(id: String, label: String, height: Int? = nil, size: Int? = nil) {
        self.id = id
        self.label = label
        self.height = height
        self.size = size
    }

    /// "1080p · 1.2 GB", or "1080p" when the site doesn't say how big.
    public var described: String {
        guard let size, size > 0 else { return label }
        return "\(label) · \(ByteCountFormatter.string(fromByteCount: Int64(size), countStyle: .file))"
    }
}

/// The folders a kept film or video can be in, by the engine's kind (`page.keep`'s `kind`,
/// `media.move`'s `to`): where a video from a page is saved, and where a right-click moves
/// a kept one (the owner, 2026-10-17: "it can move to any download folder, including the
/// 18+ one, incase there is a problem with detection"). 18+ is never offered in a
/// child's profile.
public enum KeptPlace: String, CaseIterable, Sendable, Identifiable {
    case video, movie, series, anime, adult

    public var id: String { rawValue }

    public var name: String {
        switch self {
        case .video: "Videos"
        case .movie: "Movies"
        case .series: "Series"
        case .anime: "Anime"
        case .adult: "18+"
        }
    }

    /// The places offered in a profile, in the menu's order.
    public static func offered(isChild: Bool) -> [KeptPlace] {
        [.movie, .series, .anime, .video] + (isChild ? [] : [.adult])
    }

    /// Which of them a kept file is in now, by its folders: the videos folder, or inside
    /// the Movies folder `Series`, `Anime` or `18+` (the engine's own rule,
    /// `naming.kept_details`), else loose there. Nil for a file in neither folder.
    public static func of(_ file: URL, movies: URL?, videos: URL?) -> KeptPlace? {
        let path = file.standardizedFileURL.resolvingSymlinksInPath().path
        func inside(_ folder: URL?) -> [String]? {
            guard let folder else { return nil }
            let top = folder.standardizedFileURL.resolvingSymlinksInPath().path
            guard path.hasPrefix(top + "/") else { return nil }
            return path.dropFirst(top.count + 1).split(separator: "/").map(String.init)
        }
        // The videos folder first: it's most often inside Movies.
        if inside(videos) != nil { return .video }
        guard let parts = inside(movies) else { return nil }
        guard parts.count > 1 else { return .movie }
        switch parts[0].lowercased() {
        case "series": return .series
        case "anime": return .anime
        case VideoFiles.adultFolderName: return .adult
        default: return .movie
        }
    }
}

/// Where a film or video is after `media.move`.
public struct MovedFile: Decodable, Equatable, Sendable {
    public let path: String
}
