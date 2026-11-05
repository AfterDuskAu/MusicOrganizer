import Foundation

/// TMDB (The Movie Database), with the owner's own key (2026-11-05): what the plugins don't
/// say. The engine asks (`tmdb.*`, its module `tmdb`); the app shows what came back. The key
/// is the computer's, kept by the engine and never given back to the app.
public enum TMDB {
    /// What TMDB asks every app that uses it to say, where the key is entered.
    public static let attribution = "This product uses the TMDB API but is not endorsed or certified by TMDB."

    /// The Finders' TMDB lists (the engine's **TMDB list**), with the names the owner asked for.
    public static let lists: [(id: String, name: String)] = [
        ("popular", "TMDB – Popular"), ("top_rated", "TMDB – Top Rated"), ("upcoming", "TMDB – Upcoming"),
    ]

    /// The id the app's own stand-in for TMDB goes by among the plugins' lists.
    public static let addonId = "tmdb"

    /// TMDB's lists as a plugin's, so the Finders' list menus offer them beside the others:
    /// films and series, each list taking `skip` for its next page and no genre.
    public static let addon = Addon(
        id: addonId, name: "TMDB", address: "https://www.themoviedb.org", types: ["movie", "series"],
        catalogs: ["movie", "series"].flatMap { type in
            lists.map { Addon.Catalog(type: type, id: $0.id, name: $0.name, extra: [Addon.Extra(name: "skip", required: false, options: [])]) }
        },
        version: nil, description: nil, adult: false)

    public static func isTMDB(_ addon: Addon) -> Bool { addon.id == addonId }
}

/// Settings' "Original language" (the owner, 2026-11-05): Movie Finder's and Series Finder's
/// lists and searches by the language a title was first made in, as TMDB has it. All filters
/// nothing; a language keeps only what was first made in it, and leaves out what TMDB doesn't
/// know (an unknown is never taken to be safe).
public enum OriginalLanguage {
    public static let key = "originalLanguage"
    public static let all = ""

    /// The languages after All and English, by name: the film player's, and a few more that
    /// many films are made in. TMDB's code for Cantonese is "cn".
    static let others: [String] =
        (FilmLanguage.languages.map(\.code) + ["bn", "cn", "fa", "ml", "ta", "te", "tl"]).filter { $0 != "en" }

    /// Every choice, in Settings' order: All, English, then the rest by name.
    public static var offered: [String] {
        [all, "en"] + others.sorted { name($0) < name($1) }
    }

    public static func name(_ code: String) -> String {
        if code == all { return "All" }
        if code == "cn" { return "Cantonese" }
        return Locale(identifier: "en").localizedString(forLanguageCode: code) ?? code
    }
}

/// Australian classifications, which a child's profile can be kept to (the owner chose
/// Australia, 2026-11-05): a film's certification, a series' rating, as TMDB has them; a
/// series' P and C are counted as G, and its AV15+ as MA15+ (the engine does that).
public enum AustralianRating {
    public static let offered = ["G", "PG", "M", "MA15+", "R18+"]

    public static func about(_ rating: String) -> String {
        switch rating {
        case "G": "General"
        case "PG": "Parental guidance recommended"
        case "M": "Mature: recommended for 15 and over"
        case "MA15+": "Not for under 15s without an adult"
        case "R18+": "Restricted to adults"
        default: rating
        }
    }

    /// The ratings allowed as a profile's menu says them: "G, PG", in their own order.
    public static func shown(_ allowed: Set<String>) -> String {
        offered.filter(allowed.contains).joined(separator: ", ")
    }
}

/// What TMDB said of a title: its original language and its Australian rating, nil where it
/// doesn't say (or wasn't asked).
public struct TitleFacts: Decodable, Equatable, Sendable {
    public let id: String
    public let language: String?
    public let rating: String?

    public init(id: String, language: String?, rating: String?) {
        (self.id, self.language, self.rating) = (id, language, rating)
    }
}

public struct FactsAnswer: Decodable, Sendable {
    public let facts: [TitleFacts]
}

/// What a page shows of the titles it lists: by the original language chosen in Settings
/// (Movie Finder and Series Finder only), and in a child's profile by the ratings allowed.
public struct TitleRules: Equatable, Sendable {
    /// "" for every language.
    public var language: String
    /// nil for any rating; else only these.
    public var ratings: Set<String>?

    public init(language: String = OriginalLanguage.all, ratings: Set<String>? = nil) {
        (self.language, self.ratings) = (language, ratings)
    }

    public enum Decision: Equatable, Sendable {
        case show, hide
        /// TMDB hasn't been asked yet.
        case waiting
    }

    /// What TMDB is to be asked of a title, for the rules a page goes by.
    public func wanted(languageToo: Bool) -> [String] {
        (languageToo && !language.isEmpty ? ["language"] : []) + (ratings != nil ? ["rating"] : [])
    }

    /// Whether a title is shown. `languageToo`: the page goes by the language chosen (Movie
    /// Finder, Series Finder); everywhere else only a child's ratings count. A title TMDB
    /// said nothing of is left out, as is one rated otherwise or not rated.
    public func decide(_ facts: TitleFacts?, languageToo: Bool) -> Decision {
        let byLanguage = languageToo && !language.isEmpty
        guard byLanguage || ratings != nil else { return .show }
        guard let facts else { return .waiting }
        if byLanguage, facts.language != language { return .hide }
        if let ratings, !(facts.rating.map(ratings.contains) ?? false) { return .hide }
        return .show
    }
}

/// Someone in a title's cast, with their photo.
public struct TMDBCast: Decodable, Identifiable, Hashable, Sendable {
    public let personId: Int
    public let name: String
    public let character: String?
    public let photo: String?
    public var id: Int { personId }
}

/// A title's page's extras from TMDB: its cast, and a film's budget and revenue (US dollars).
public struct TMDBDetails: Decodable, Sendable {
    public let tmdbId: Int?
    public let cast: [TMDBCast]
    public let budget: Int?
    public let revenue: Int?
}

/// A film or series someone is in, by TMDB's own id ("tmdb:550"): opened by its IMDb id,
/// asked for first (`tmdb.imdb`).
public struct TMDBCredit: Decodable, Identifiable, Hashable, Sendable {
    public let id: String
    public let type: String
    public let name: String
    public let poster: String?
    public let year: String?
    public let character: String?

    public var item: MediaItem {
        MediaItem(id: id, type: type, name: name, poster: poster, posterShape: "poster", year: year, rating: nil, genres: [])
    }
}

public struct TMDBPerson: Decodable, Sendable {
    public let id: Int
    public let name: String
    public let photo: String?
    public let biography: String?
    public let credits: [TMDBCredit]
}

/// A film coming out in Australia, or a new series, on the calendar's General.
public struct TMDBRelease: Decodable, Identifiable, Hashable, Sendable {
    public let id: String
    public let type: String
    public let name: String
    public let poster: String?
    /// "2026-11-13": the day in Australia for a film, the first episode's for a series.
    public let day: String

    public var item: MediaItem {
        MediaItem(id: id, type: type, name: name, poster: poster, posterShape: "poster", year: String(day.prefix(4)), rating: nil, genres: [])
    }
}

public struct TMDBReleases: Decodable, Sendable {
    public let releases: [TMDBRelease]
}

public struct TMDBStatus: Decodable, Sendable {
    public let hasKey: Bool
}

/// `tmdb.imdb`'s answer: the IMDb id a title's page is opened by, or nil.
public struct TMDBOpened: Decodable, Sendable {
    public let id: String?
}

public enum Money {
    /// An amount of US dollars as a film's page says it: "$63,000,000".
    public static func dollars(_ amount: Int) -> String {
        let format = NumberFormatter()
        format.numberStyle = .currency
        format.currencyCode = "USD"
        format.locale = Locale(identifier: "en_US")
        format.maximumFractionDigits = 0
        return format.string(from: NSNumber(value: amount)) ?? "$\(amount)"
    }
}
