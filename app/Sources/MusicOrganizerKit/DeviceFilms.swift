import Foundation

/// The films downloaded on paired video apps (the owner, 2026-11-10: "when a movie is
/// downloaded on any of the devices. On bear media mac. it offers the ability to download from
/// that device"): each device's downloads as it last said them (`sharing.device_films`). The
/// Mac can't fetch from a device: one asked for (`sharing.want`) is sent by the device at its
/// next sync, while the app is open on it (docs/ENGINE_API.md, section 3).
public struct DeviceFilms: Decodable, Sendable, Equatable {
    public struct Film: Decodable, Sendable, Equatable, Identifiable {
        /// The device's own id for its download.
        public let id: String
        /// `movie`, `series` or `anime`.
        public let kind: String
        public let title: String
        public let year: String?
        public let show: String?
        public let season: Int?
        public let episode: Int?
        /// Its ending: `mkv`, `mp4`...
        public let type: String
        public let size: Int64
        public let poster: String?
        /// The plugin's id for the title, when the device said.
        public let item: String?
        /// In the Movies folder already.
        public let here: Bool
        /// Asked for, and not here yet.
        public let wanted: Bool
        /// How much of it has arrived (0 to 1) while it's being sent.
        public let arriving: Double?

        public init(
            id: String, kind: String = "movie", title: String, year: String? = nil, show: String? = nil,
            season: Int? = nil, episode: Int? = nil, type: String = "mkv", size: Int64, poster: String? = nil,
            item: String? = nil, here: Bool = false, wanted: Bool = false, arriving: Double? = nil
        ) {
            (self.id, self.kind, self.title, self.year, self.show) = (id, kind, title, year, show)
            (self.season, self.episode, self.type, self.size) = (season, episode, type, size)
            (self.poster, self.item, self.here, self.wanted, self.arriving) = (poster, item, here, wanted, arriving)
        }

        public var isEpisode: Bool { !(show ?? "").isEmpty }

        /// How it's named: "Heart of the Beast (2026)", or "A Show · S1 E2 · Pilot".
        public var name: String {
            if let show, !show.isEmpty {
                var parts = [show]
                switch (season, episode) {
                case let (season?, episode?): parts.append("S\(season) E\(episode)")
                case let (nil, episode?): parts.append("E\(episode)")
                default: break
                }
                if !title.isEmpty, title != show { parts.append(title) }
                return parts.joined(separator: " · ")
            }
            if let year, !year.isEmpty { return "\(title) (\(year))" }
            return title
        }

        /// As a card shows it: with its poster, and its plugin title's id when the device said.
        public var card: MediaItem {
            MediaItem(
                id: item ?? "", type: kind == "movie" ? "movie" : "series", name: name, poster: poster,
                posterShape: "poster", year: isEpisode ? nil : year, rating: nil, genres: [])
        }
    }

    public struct Device: Decodable, Sendable, Equatable, Identifiable {
        /// The engine's id for the device's pairing (one for each profile it's paired with).
        public let device: String
        public let profile: String
        /// What kind of device it is, in its own words: "Android TV", "iPad".
        public let kind: String
        public let at: String?
        public let films: [Film]

        public init(device: String, profile: String, kind: String, at: String? = nil, films: [Film]) {
            (self.device, self.profile, self.kind, self.at, self.films) = (device, profile, kind, at, films)
        }

        public var id: String { profile + "/" + device }
    }

    public let devices: [Device]

    public init(devices: [Device] = []) { self.devices = devices }

    /// A film on a device, as the page shows it: once, however many of the device's accounts
    /// said they have it (each account of a device is paired on its own, and a device's
    /// downloads are the same in each), and every pairing that said so, to ask each.
    public struct Waiting: Identifiable, Equatable, Sendable {
        public let kind: String
        public let film: Film
        public let from: [Device]
        public var id: String { kind + "/" + film.id }
    }

    /// The films on devices that aren't on this Mac, grouped by the kind of device, each film
    /// once. A device says its films from the account open on it, so the list said last by
    /// any of its accounts is what it has now (an older one, under another account's key,
    /// may name a film since deleted there). Asked for, or arriving, under any of its
    /// accounts counts.
    public var waiting: [(kind: String, films: [Waiting])] {
        var order: [String] = []
        var byKind: [String: [Device]] = [:]
        for device in devices {
            if byKind[device.kind] == nil { order.append(device.kind) }
            byKind[device.kind, default: []].append(device)
        }
        return order.compactMap { kind -> (kind: String, films: [Waiting])? in
            let pairings = byKind[kind] ?? []
            guard let newest = pairings.max(by: { ($0.at ?? "") < ($1.at ?? "") }) else { return nil }
            let films = newest.films.filter { !$0.here }.map { film -> Waiting in
                let from = pairings.filter { $0.films.contains { $0.id == film.id } }
                let said = from.flatMap { $0.films.filter { $0.id == film.id } }
                // Arriving under any pairing, else asked for under any: shown so.
                let shown = said.first { $0.arriving != nil } ?? said.first { $0.wanted } ?? film
                return Waiting(kind: kind, film: shown, from: from)
            }
            return films.isEmpty ? nil : (kind, films)
        }
    }

    /// How many films on devices aren't on this Mac.
    public var waitingCount: Int { waiting.reduce(0) { $0 + $1.films.count } }
}
