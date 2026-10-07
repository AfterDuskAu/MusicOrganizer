import Foundation

/// One of a film's sound or subtitle tracks, as the film player's menus show it.
public struct FilmTrack: Identifiable, Hashable, Sendable {
    public enum Kind: String, Sendable { case sound = "audio", subtitles = "sub" }

    /// The player's own number for it, among tracks of its kind.
    public let id: Int
    public let kind: Kind
    public let label: String
    public let selected: Bool

    public init(id: Int, kind: Kind, title: String?, language: String?, selected: Bool) {
        self.id = id
        self.kind = kind
        self.label = Self.label(number: id, title: title, language: language)
        self.selected = selected
    }

    /// What a menu calls a track: its language in words and what the film says of it
    /// ("English · Commentary"), or "Track 2" when the film says nothing.
    public static func label(number: Int, title: String?, language: String?) -> String {
        let said = (title ?? "").trimmingCharacters(in: .whitespaces)
        let code = (language ?? "").trimmingCharacters(in: .whitespaces)
        let known = code.isEmpty || code == "und" ? nil : Locale.current.localizedString(forLanguageCode: code)
        let tongue = known ?? (code.isEmpty || code == "und" ? "" : code)
        let parts = [tongue, said.caseInsensitiveCompare(tongue) == .orderedSame ? "" : said]
            .filter { !$0.isEmpty }
        return parts.isEmpty ? "Track \(number)" : parts.joined(separator: " · ")
    }
}

/// Where each film was watched up to, so it opens there the next time (the owner,
/// 2026-10-08: closed at 0:32, it opens at 0:32 a week later). Kept by the app on this
/// Mac, by the film's file or torrent; nothing of it is in the library.
public struct FilmPositions: Equatable, Sendable {
    public struct Place: Codable, Equatable, Sendable {
        public let seconds: Double
        public let at: Date
    }

    /// Films remembered at most: the one watched longest ago is forgotten first.
    public static let most = 300
    /// Nearer the start than this, a film just starts again.
    public static let startS = 20.0
    /// Nearer the end than this (the credits), it's finished and starts again.
    public static let endS = 90.0

    public private(set) var places: [String: Place]

    public init(_ places: [String: Place] = [:]) { self.places = places }

    /// Where to open a film: nil to start at the beginning.
    public func place(of key: String) -> Double? { places[key]?.seconds }

    /// The film was at `seconds` of `length` when it was last looked at. One that's
    /// hardly begun, or is in its last minute and a half, is forgotten.
    public mutating func watched(_ key: String, to seconds: Double, of length: Double, now: Date = Date()) {
        guard length > 0 else { return }
        if seconds < Self.startS || seconds > length - Self.endS {
            places[key] = nil
            return
        }
        places[key] = Place(seconds: seconds, at: now)
        while places.count > Self.most, let oldest = places.min(by: { $0.value.at < $1.value.at }) {
            places[oldest.key] = nil
        }
    }

    // MARK: kept in the app's settings

    public static let key = "filmPositions"

    public static func saved(in defaults: UserDefaults = .standard) -> FilmPositions {
        guard let data = defaults.data(forKey: key),
            let places = try? JSONDecoder().decode([String: Place].self, from: data)
        else { return FilmPositions() }
        return FilmPositions(places)
    }

    public func save(in defaults: UserDefaults = .standard) {
        if let data = try? JSONEncoder().encode(places) { defaults.set(data, forKey: Self.key) }
    }
}
