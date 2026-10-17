import Foundation

/// A stream's list of parts, read so the browser window can fetch each part in turn and
/// the engine can put them back together (2026-10-18). Two kinds: HLS (`.m3u8`, RFC 8216)
/// and DASH (`.mpd`). Either may be **locked** (its parts encrypted with a key, or under
/// DRM): a locked stream is never saved, and nothing here reads a key.
public enum StreamListError: Error, Equatable, LocalizedError {
    case notAList
    case locked
    case live
    case empty

    public var errorDescription: String? {
        switch self {
        case .notAList: "That isn't a stream's list of parts."
        case .locked: "This video is protected against copying (its parts are locked), so it can't be saved."
        case .live: "That's a live stream, which goes on without end: it can't be saved."
        case .empty: "That stream has no parts to save."
        }
    }
}

/// One part of a stream to fetch: its address, and a byte range of it where the list says.
public struct StreamPart: Equatable, Sendable {
    public let url: URL
    /// `(first, last)`, both counted in: what's asked for with `Range: bytes=first-last`.
    public let range: ClosedRange<Int>?

    public init(url: URL, range: ClosedRange<Int>? = nil) {
        self.url = url
        self.range = range
    }
}

/// One way a stream can be saved: a size of its picture, with the parts to fetch for its
/// picture (and sound, when the sound comes apart), in order.
public struct StreamChoice: Identifiable, Equatable, Sendable {
    public let id: String
    public let height: Int?
    public let bandwidth: Int?
    /// The picture's parts (with the sound in them, when it doesn't come apart). Empty
    /// until its own list has been read (HLS: a size's list is read once it's chosen).
    public var video: [StreamPart]
    public var audio: [StreamPart]
    /// Where the size's own list is (HLS), to be read when it's chosen.
    public let list: URL?
    public let audioList: URL?
    public var seconds: Double?

    public init(
        id: String, height: Int? = nil, bandwidth: Int? = nil, video: [StreamPart] = [],
        audio: [StreamPart] = [], list: URL? = nil, audioList: URL? = nil, seconds: Double? = nil
    ) {
        self.id = id
        self.height = height
        self.bandwidth = bandwidth
        self.video = video
        self.audio = audio
        self.list = list
        self.audioList = audioList
        self.seconds = seconds
    }

    /// "1080p · about 1.2 GB", "720p", "Best".
    public var label: String {
        var said = height.map { "\($0)p" } ?? "As it is"
        if let bandwidth, let seconds, seconds > 0 {
            let bytes = Int64(Double(bandwidth) / 8 * seconds)
            said += " · about " + ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file)
        }
        return said
    }
}

// MARK: - HLS

public enum HLSList {
    /// A master list (the sizes on offer), or a media list (one size's parts).
    public enum Content: Equatable, Sendable {
        case sizes([StreamChoice])
        case parts(StreamParts)
    }

    public struct StreamParts: Equatable, Sendable {
        /// The fMP4 header part (`EXT-X-MAP`), fetched first, when the parts need one.
        public let header: StreamPart?
        public let parts: [StreamPart]
        public let seconds: Double

        public var all: [StreamPart] { (header.map { [$0] } ?? []) + parts }
    }

    /// Read an HLS list fetched from `base`. Throws `StreamListError` for anything that
    /// isn't one, a locked stream, a live one, or one with nothing in it.
    public static func read(_ text: String, base: URL) throws -> Content {
        let lines = text.replacingOccurrences(of: "\r\n", with: "\n").split(
            separator: "\n", omittingEmptySubsequences: true
        ).map { $0.trimmingCharacters(in: .whitespaces) }
        guard lines.first?.hasPrefix("#EXTM3U") == true else { throw StreamListError.notAList }
        if lines.contains(where: { $0.hasPrefix("#EXT-X-STREAM-INF") }) {
            return .sizes(try sizes(lines, base: base))
        }
        return .parts(try parts(lines, base: base))
    }

    private static func sizes(_ lines: [String], base: URL) throws -> [StreamChoice] {
        var audioLists: [String: URL] = [:]  // group → the default (else first) sound's list
        for line in lines where line.hasPrefix("#EXT-X-MEDIA:") {
            let said = attributes(line)
            guard said["TYPE"] == "AUDIO", let group = said["GROUP-ID"],
                let uri = said["URI"], let url = URL(string: uri, relativeTo: base)?.absoluteURL
            else { continue }
            if audioLists[group] == nil || said["DEFAULT"] == "YES" { audioLists[group] = url }
        }
        if lines.contains(where: { $0.hasPrefix("#EXT-X-SESSION-KEY") }) {
            throw StreamListError.locked
        }
        var found: [StreamChoice] = []
        var waiting: [String: String]?
        for line in lines {
            if line.hasPrefix("#EXT-X-STREAM-INF:") {
                waiting = attributes(line)
            } else if let said = waiting, !line.hasPrefix("#") {
                waiting = nil
                guard let url = URL(string: line, relativeTo: base)?.absoluteURL else { continue }
                let height = said["RESOLUTION"].flatMap { $0.split(separator: "x").last }.flatMap { Int($0) }
                let bandwidth = said["AVERAGE-BANDWIDTH"].flatMap(Int.init) ?? said["BANDWIDTH"].flatMap(Int.init)
                // A size with no picture (sound alone) isn't a video to save.
                if let codecs = said["CODECS"], !codecs.contains("avc") && !codecs.contains("hvc")
                    && !codecs.contains("hev") && !codecs.contains("vp0") && !codecs.contains("vp9")
                    && !codecs.contains("av01") && height == nil
                {
                    continue
                }
                found.append(
                    StreamChoice(
                        id: url.absoluteString, height: height, bandwidth: bandwidth, list: url,
                        audioList: said["AUDIO"].flatMap { audioLists[$0] }))
            }
        }
        guard !found.isEmpty else { throw StreamListError.empty }
        // The sharpest first; of two the same size, the one with more to it.
        return found.sorted { ($0.height ?? 0, $0.bandwidth ?? 0) > ($1.height ?? 0, $1.bandwidth ?? 0) }
    }

    private static func parts(_ lines: [String], base: URL) throws -> StreamParts {
        var header: StreamPart?
        var found: [StreamPart] = []
        var seconds = 0.0
        var nextRange: (length: Int, offset: Int?)?
        var lastEnd: [URL: Int] = [:]  // where the last range of each address ended
        var ended = false
        var vod = false
        for line in lines {
            if line.hasPrefix("#EXT-X-KEY:") || line.hasPrefix("#EXT-X-SESSION-KEY:") {
                if attributes(line)["METHOD"]?.uppercased() != "NONE" { throw StreamListError.locked }
            } else if line.hasPrefix("#EXT-X-MAP:") {
                let said = attributes(line)
                guard let uri = said["URI"], let url = URL(string: uri, relativeTo: base)?.absoluteURL else {
                    continue
                }
                header = StreamPart(url: url, range: said["BYTERANGE"].flatMap { range($0, after: 0) })
            } else if line.hasPrefix("#EXTINF:") {
                let value = line.dropFirst("#EXTINF:".count).split(separator: ",").first ?? ""
                seconds += Double(value.trimmingCharacters(in: .whitespaces)) ?? 0
            } else if line.hasPrefix("#EXT-X-BYTERANGE:") {
                let value = String(line.dropFirst("#EXT-X-BYTERANGE:".count))
                let bits = value.split(separator: "@")
                if let length = Int(bits[0]) {
                    nextRange = (length, bits.count > 1 ? Int(bits[1]) : nil)
                }
            } else if line.hasPrefix("#EXT-X-ENDLIST") {
                ended = true
            } else if line.hasPrefix("#EXT-X-PLAYLIST-TYPE:") {
                vod = line.uppercased().hasSuffix("VOD")
            } else if !line.hasPrefix("#") {
                guard let url = URL(string: line, relativeTo: base)?.absoluteURL else { continue }
                var range: ClosedRange<Int>?
                if let next = nextRange {
                    let first = next.offset ?? lastEnd[url] ?? 0
                    range = first...(first + next.length - 1)
                    lastEnd[url] = first + next.length
                    nextRange = nil
                }
                found.append(StreamPart(url: url, range: range))
            }
        }
        guard ended || vod else { throw StreamListError.live }
        guard !found.isEmpty else { throw StreamListError.empty }
        return StreamParts(header: header, parts: found, seconds: seconds)
    }

    /// `"length@offset"` → the range it names.
    private static func range(_ text: String, after: Int) -> ClosedRange<Int>? {
        let bits = text.split(separator: "@")
        guard let length = Int(bits[0]), length > 0 else { return nil }
        let first = bits.count > 1 ? Int(bits[1]) ?? after : after
        return first...(first + length - 1)
    }

    /// An HLS tag's attributes, `NAME=value` or `NAME="value, with commas"`.
    static func attributes(_ line: String) -> [String: String] {
        guard let colon = line.firstIndex(of: ":") else { return [:] }
        var found: [String: String] = [:]
        var name = ""
        var value = ""
        var inName = true
        var quoted = false
        func keep() {
            if !name.isEmpty { found[name.trimmingCharacters(in: .whitespaces).uppercased()] = value }
            (name, value, inName) = ("", "", true)
        }
        for character in line[line.index(after: colon)...] {
            if inName {
                if character == "=" { inName = false } else if character != "," { name.append(character) }
            } else if character == "\"" {
                quoted.toggle()
            } else if character == "," && !quoted {
                keep()
            } else {
                value.append(character)
            }
        }
        keep()
        return found
    }
}

// MARK: - DASH

public enum DASHList {
    /// The ways a DASH manifest's video can be saved, sharpest first, each with its
    /// picture's parts and the best sound's. Throws `StreamListError` for anything that
    /// isn't one, a protected (DRM) one, a live one, or one with nothing in it.
    public static func read(_ text: String, base: URL) throws -> [StreamChoice] {
        guard let data = text.data(using: .utf8) else { throw StreamListError.notAList }
        let reader = Reader(base: base)
        let parser = XMLParser(data: data)
        parser.delegate = reader
        guard parser.parse() || reader.stoppedAtNextPeriod, reader.sawManifest else {
            throw StreamListError.notAList
        }
        if reader.isLive { throw StreamListError.live }
        let pictures = reader.representations.filter { $0.isPicture && !$0.locked }
        let sounds = reader.representations.filter { $0.isSound && !$0.locked }
        if pictures.isEmpty && reader.representations.contains(where: \.locked) {
            throw StreamListError.locked
        }
        let sound = sounds.max { ($0.bandwidth ?? 0) < ($1.bandwidth ?? 0) }
        let soundParts = sound.map { $0.parts(seconds: reader.seconds) } ?? []
        let choices = pictures.map { picture in
            StreamChoice(
                id: picture.id + "@" + String(picture.bandwidth ?? 0), height: picture.height,
                bandwidth: picture.bandwidth.map { $0 + (sound?.bandwidth ?? 0) },
                video: picture.parts(seconds: reader.seconds), audio: soundParts,
                seconds: reader.seconds)
        }.filter { !$0.video.isEmpty }
        guard !choices.isEmpty else { throw StreamListError.empty }
        return choices.sorted { ($0.height ?? 0, $0.bandwidth ?? 0) > ($1.height ?? 0, $1.bandwidth ?? 0) }
    }

    /// `PT1H2M3.5S` → seconds.
    static func seconds(_ text: String?) -> Double? {
        guard let text, text.hasPrefix("P") else { return nil }
        var total = 0.0
        var number = ""
        var inTime = false
        for character in text.dropFirst() {
            switch character {
            case "T": inTime = true
            case "D": total += (Double(number) ?? 0) * 86_400; number = ""
            case "H": total += (Double(number) ?? 0) * 3_600; number = ""
            case "M": total += (Double(number) ?? 0) * (inTime ? 60 : 2_592_000); number = ""
            case "S": total += Double(number) ?? 0; number = ""
            default: number.append(character)
            }
        }
        return total
    }

    struct Template {
        var media: String?
        var initialization: String?
        var startNumber = 1
        var timescale = 1.0
        var duration: Double?
        var timeline: [(t: Int?, d: Int, r: Int)] = []
    }

    struct Representation {
        var id: String
        var bandwidth: Int?
        var height: Int?
        var mimeType: String
        var codecs: String
        var base: URL
        var template: Template?
        var list: (header: String?, parts: [String])?
        var baseRange: String?  // SegmentBase: the whole file, its header in a range of it
        var locked: Bool

        var isPicture: Bool {
            mimeType.hasPrefix("video") || height != nil
                || ["avc", "hvc", "hev", "vp9", "vp09", "av01"].contains(where: codecs.contains)
        }
        var isSound: Bool { !isPicture && (mimeType.hasPrefix("audio") || codecs.contains("mp4a") || codecs.contains("opus")) }

        func parts(seconds: Double?) -> [StreamPart] {
            func url(_ text: String) -> URL? { URL(string: text, relativeTo: base)?.absoluteURL }
            if let template, let media = template.media {
                var found: [StreamPart] = []
                if let header = template.initialization.map({ fill($0, number: nil, time: nil) }), let at = url(header) {
                    found.append(StreamPart(url: at))
                }
                var number = template.startNumber
                if !template.timeline.isEmpty {
                    var time = 0
                    for entry in template.timeline {
                        if let t = entry.t { time = t }
                        for _ in 0...max(entry.r, 0) {
                            if let at = url(fill(media, number: number, time: time)) { found.append(StreamPart(url: at)) }
                            time += entry.d
                            number += 1
                        }
                    }
                } else if let duration = template.duration, duration > 0, let seconds, seconds > 0 {
                    let count = Int((seconds / (duration / template.timescale)).rounded(.up))
                    for _ in 0..<min(count, 100_000) {
                        if let at = url(fill(media, number: number, time: nil)) { found.append(StreamPart(url: at)) }
                        number += 1
                    }
                }
                return found
            }
            if let list {
                return ([list.header].compactMap { $0 } + list.parts).compactMap(url).map { StreamPart(url: $0) }
            }
            // One file holds the whole of it (SegmentBase, or a BaseURL alone).
            return [StreamPart(url: base)]
        }

        /// A template's address with its `$…$` filled in.
        func fill(_ text: String, number: Int?, time: Int?) -> String {
            var out = ""
            var rest = Substring(text)
            while let open = rest.firstIndex(of: "$") {
                out += rest[..<open]
                let after = rest[rest.index(after: open)...]
                guard let close = after.firstIndex(of: "$") else { out += rest[open...]; return out }
                let name = String(after[..<close])
                rest = after[after.index(after: close)...]
                let bits = name.split(separator: "%", maxSplits: 1)
                let value: Int?
                switch bits.first.map(String.init) ?? "" {
                case "": out += "$"; continue
                case "RepresentationID": out += id; continue
                case "Number": value = number
                case "Time": value = time
                case "Bandwidth": value = bandwidth
                default: out += "$\(name)$"; continue
                }
                guard let value else { continue }
                if bits.count == 2, bits[1].hasSuffix("d"), let width = Int(bits[1].dropFirst().dropLast()) {
                    out += String(format: "%0\(width)d", value)
                } else {
                    out += String(value)
                }
            }
            return out + rest
        }
    }

    /// Reads the parts of an MPD the window needs: BaseURLs at every level, each
    /// AdaptationSet's and Representation's kind, size and template, and any
    /// ContentProtection (which locks what it's in).
    final class Reader: NSObject, XMLParserDelegate {
        let base: URL
        var sawManifest = false
        var isLive = false
        var seconds: Double?
        var representations: [Representation] = []
        /// Reading stopped at a second period (only the first is the video).
        var stoppedAtNextPeriod = false

        private var bases: [URL]
        private var marks: [Int] = []  // how many BaseURLs each open Period and AdaptationSet began with
        private var text = ""
        private var setInfo: (mime: String, codecs: String, height: Int?, template: Template?, locked: Bool)?
        private var current: Representation?
        private var periods = 0
        private var listHeader: String?
        private var listParts: [String] = []
        private var inList = false
        private var pendingTimeline: [(t: Int?, d: Int, r: Int)] = []
        private var templateOwner: String?  // "set" or "rep"

        init(base: URL) {
            self.base = base
            bases = [base]
        }

        func parser(
            _ parser: XMLParser, didStartElement name: String, namespaceURI: String?,
            qualifiedName: String?, attributes said: [String: String] = [:]
        ) {
            text = ""
            switch name {
            case "MPD":
                sawManifest = true
                isLive = said["type"] == "dynamic"
                seconds = DASHList.seconds(said["mediaPresentationDuration"])
            case "Period":
                periods += 1
                if periods > 1 {  // the first period is the video
                    stoppedAtNextPeriod = true
                    parser.abortParsing()
                }
                if seconds == nil { seconds = DASHList.seconds(said["duration"]) }
                marks.append(bases.count)
            case "AdaptationSet":
                marks.append(bases.count)
                let mime = said["mimeType"] ?? said["contentType"].map { "\($0)/" } ?? ""
                setInfo = (mime, said["codecs"] ?? "", said["height"].flatMap(Int.init), nil, false)
            case "Representation":
                current = Representation(
                    id: said["id"] ?? "", bandwidth: said["bandwidth"].flatMap(Int.init),
                    height: said["height"].flatMap(Int.init) ?? setInfo?.height,
                    mimeType: said["mimeType"] ?? setInfo?.mime ?? "",
                    codecs: said["codecs"] ?? setInfo?.codecs ?? "", base: bases.last ?? base,
                    template: setInfo?.template, list: nil, baseRange: nil, locked: setInfo?.locked ?? false)
            case "ContentProtection":
                if current != nil { current?.locked = true } else { setInfo?.locked = true }
            case "SegmentTemplate":
                var template = Template()
                template.media = said["media"]
                template.initialization = said["initialization"]
                template.startNumber = said["startNumber"].flatMap(Int.init) ?? 1
                template.timescale = said["timescale"].flatMap(Double.init) ?? 1
                template.duration = said["duration"].flatMap(Double.init)
                pendingTimeline = []
                if current != nil {
                    current?.template = template
                    templateOwner = "rep"
                } else {
                    setInfo?.template = template
                    templateOwner = "set"
                }
            case "S":
                let t = said["t"].flatMap(Int.init)
                if let d = said["d"].flatMap(Int.init) {
                    pendingTimeline.append((t, d, said["r"].flatMap(Int.init) ?? 0))
                }
            case "SegmentList":
                inList = true
                listHeader = nil
                listParts = []
            case "Initialization":
                if inList { listHeader = said["sourceURL"] }
            case "SegmentURL":
                if let media = said["media"] { listParts.append(media) }
            case "SegmentBase":
                current?.baseRange = said["indexRange"] ?? ""
            default:
                break
            }
        }

        func parser(_ parser: XMLParser, foundCharacters string: String) { text += string }

        func parser(
            _ parser: XMLParser, didEndElement name: String, namespaceURI: String?, qualifiedName: String?
        ) {
            switch name {
            case "BaseURL":
                let parent = current?.base ?? bases.last ?? base
                if let url = URL(string: text.trimmingCharacters(in: .whitespacesAndNewlines), relativeTo: parent)?.absoluteURL {
                    if current != nil { current?.base = url } else { bases.append(url) }
                }
            case "SegmentTimeline":
                if templateOwner == "rep" {
                    current?.template?.timeline = pendingTimeline
                } else {
                    setInfo?.template?.timeline = pendingTimeline
                }
            case "SegmentList":
                inList = false
                current?.list = (listHeader, listParts)
            case "Representation":
                if let done = current { representations.append(done) }
                current = nil
            case "AdaptationSet":
                setInfo = nil
                if let mark = marks.popLast() { bases = Array(bases.prefix(mark)) }
            case "Period":
                if let mark = marks.popLast() { bases = Array(bases.prefix(mark)) }
            default:
                break
            }
            text = ""
        }
    }
}
