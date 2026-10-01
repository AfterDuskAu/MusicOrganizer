import Foundation

/// `youtube.video`: a song's official music video, as the engine found it.
public struct VideoAnswer: Decodable, Sendable {
    public struct Quality: Decodable, Sendable {
        public let label: String
        public let height: Int
        public let fps: Int
        public let url: String

        public init(label: String, height: Int, fps: Int, url: String) {
            self.label = label
            self.height = height
            self.fps = fps
            self.url = url
        }
    }

    public let found: Bool
    public let videoId: String?
    public let title: String?
    public let durationS: Double?
    public let httpHeaders: [String: String]?
    public let audioUrl: String?
    public let qualities: [Quality]?

    public init(
        found: Bool, videoId: String? = nil, title: String? = nil, durationS: Double? = nil,
        httpHeaders: [String: String]? = nil, audioUrl: String? = nil, qualities: [Quality]? = nil
    ) {
        self.found = found
        self.videoId = videoId
        self.title = title
        self.durationS = durationS
        self.httpHeaders = httpHeaders
        self.audioUrl = audioUrl
        self.qualities = qualities
    }
}

/// The picture size the owner last chose, kept between runs of the app.
public struct VideoPreference: Codable, Equatable, Sendable {
    public let height: Int
    public let label: String

    public init(height: Int, label: String) {
        self.height = height
        self.label = label
    }
}

/// A song's video, ready to play: its sound, and its picture in each size on offer.
/// YouTube serves the two apart, so the player joins one picture to the sound.
public struct SongVideo: Equatable, Sendable {
    public struct Quality: Identifiable, Hashable, Sendable {
        public let label: String  // "1080p", "720p60"
        public let height: Int
        public let url: URL

        public var id: String { label }
    }

    public let videoId: String
    public let length: Double
    public let sound: URL
    public let headers: [String: String]
    /// The sharpest first. Never empty.
    public let qualities: [Quality]

    /// Nil unless the engine found a video with everything needed to play it.
    public init?(_ answer: VideoAnswer) {
        guard answer.found, let videoId = answer.videoId, let length = answer.durationS, length > 0,
            let sound = answer.audioUrl.flatMap(URL.init(string:))
        else { return nil }
        let qualities = (answer.qualities ?? []).compactMap { quality in
            URL(string: quality.url).map {
                Quality(label: quality.label, height: quality.height, url: $0)
            }
        }
        guard !qualities.isEmpty else { return nil }
        self.videoId = videoId
        self.length = length
        self.sound = sound
        self.headers = answer.httpHeaders ?? [:]
        self.qualities = qualities
    }

    /// The picture to show. With nothing chosen, the sharpest. Otherwise the very size
    /// that was chosen if this video has it, else the sharpest that's no bigger, else
    /// (every size is bigger) the smallest.
    public func quality(for wanted: VideoPreference?) -> Quality {
        guard let wanted else { return qualities[0] }
        return qualities.first { $0.label == wanted.label }
            ?? qualities.first { $0.height <= wanted.height }
            ?? qualities[qualities.count - 1]
    }

    /// A video as long as the song (within two seconds) is taken to be the same
    /// recording, so the song's timed lyrics and its place in the song still fit. A
    /// music video with an intro or a scene in the middle doesn't.
    public func keepsTime(with songLength: Double?) -> Bool {
        guard let songLength, songLength > 0 else { return false }
        return abs(songLength - length) <= 2
    }
}
