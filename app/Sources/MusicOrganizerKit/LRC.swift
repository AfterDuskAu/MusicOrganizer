import Foundation

/// One timed line of lyrics.
public struct LyricLine: Identifiable, Equatable, Sendable {
    public let id: Int
    public let time: Double  // seconds from the start of the song
    public let text: String  // "" is a pause between verses
}

/// Timed lyrics in the `.lrc` format: `[01:23.45]A line`. A line may carry several
/// times (a repeated chorus); tags like `[ar:…]` are skipped.
public enum LRC {
    public static func parse(_ text: String) -> [LyricLine] {
        var found: [(time: Double, text: String)] = []
        for raw in text.split(whereSeparator: \.isNewline) {
            var rest = Substring(raw.trimmingCharacters(in: .whitespaces))
            var times: [Double] = []
            while rest.hasPrefix("["), let close = rest.firstIndex(of: "]") {
                guard let time = seconds(rest[rest.index(after: rest.startIndex)..<close])
                else { break }
                times.append(time)
                rest = rest[rest.index(after: close)...]
            }
            let words = rest.trimmingCharacters(in: .whitespaces)
            for time in times { found.append((time, words)) }
        }
        found.sort { $0.time < $1.time }
        return found.enumerated().map { LyricLine(id: $0, time: $1.time, text: $1.text) }
    }

    /// The line being sung at `time`: the last one that has started. Nil before the first.
    public static func current(at time: Double, in lines: [LyricLine]) -> Int? {
        var low = 0, high = lines.count
        while low < high {
            let middle = (low + high) / 2
            if lines[middle].time <= time { low = middle + 1 } else { high = middle }
        }
        return low == 0 ? nil : low - 1
    }

    /// The words alone: each line of `text` without its time stamps, blank lines and
    /// tags like `[ar:…]` left out. This is what tap-along syncing starts from.
    public static func words(of text: String) -> [String] {
        var found: [String] = []
        for raw in text.split(whereSeparator: \.isNewline) {
            var rest = Substring(raw.trimmingCharacters(in: .whitespaces))
            while rest.hasPrefix("["), let close = rest.firstIndex(of: "]") {
                rest = rest[rest.index(after: close)...]
            }
            let line = rest.trimmingCharacters(in: .whitespaces)
            if !line.isEmpty { found.append(line) }
        }
        return found
    }

    /// Timed lyrics as text: one `[mm:ss.xx]words` line for each line with its time.
    public static func text(of timed: [(time: Double, text: String)]) -> String {
        timed.map { line in
            let hundredths = Int((max(0, line.time) * 100).rounded())
            return String(
                format: "[%02d:%02d.%02d]%@", hundredths / 6000, hundredths / 100 % 60,
                hundredths % 100, line.text)
        }.joined(separator: "\n")
    }

    /// "01:23.45" or "1:23" → seconds. Nil for anything else, such as "ar:Name".
    private static func seconds(_ stamp: Substring) -> Double? {
        let parts = stamp.split(separator: ":", omittingEmptySubsequences: false)
        guard parts.count == 2, let minutes = Int(parts[0]), minutes >= 0,
            parts[1].first?.isNumber == true, let secs = Double(parts[1]), secs >= 0
        else { return nil }
        return Double(minutes) * 60 + secs
    }
}
