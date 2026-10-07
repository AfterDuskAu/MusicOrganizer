import Foundation

/// The owner's rule (2026-10-07): the app's words don't say "YouTube", except on the
/// playlist link under Import Playlists. The app's own sentences were reworded; the
/// engine's messages (a refusal, a pause, a note about what was found) arrive already
/// written, and are put into the app's words here as they come in.
///
/// Only sentences the engine wrote are touched: the fields named in `sentences`, and an
/// error's message. A song's or a channel's name is never changed.
public enum Wording {
    /// The fields of an engine answer that hold a sentence for the owner.
    static let sentences: Set<String> = ["message", "note", "why", "kids_note", "reason"]

    /// One sentence in the app's words.
    public static func plain(_ text: String) -> String {
        guard text.contains("YouTube") else { return text }
        var words = text
        // At the start of the text or of a sentence it takes a capital.
        for (from, to) in [("YouTube Music", "The music service"), ("YouTube", "The service")] {
            if words.hasPrefix(from) { words = to + words.dropFirst(from.count) }
            for end in [". ", ": ", "! ", "? "] {
                words = words.replacingOccurrences(of: end + from, with: end + to)
            }
        }
        words = words.replacingOccurrences(of: "YouTube Music", with: "the music service")
        return words.replacingOccurrences(of: "YouTube", with: "the service")
    }

    /// An engine answer with its sentences in the app's words, whatever its shape.
    public static func plain(answer: Any) -> Any {
        if let fields = answer as? [String: Any] {
            var out: [String: Any] = [:]
            for (name, value) in fields {
                if sentences.contains(name), let text = value as? String {
                    out[name] = plain(text)
                } else {
                    out[name] = plain(answer: value)
                }
            }
            return out
        }
        if let list = answer as? [Any] { return list.map { plain(answer: $0) } }
        return answer
    }
}
