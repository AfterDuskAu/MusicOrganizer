import Foundation

/// A list of titles pasted into Settings (the owner, 2026-11-13: "copy/paste titles directly into
/// a 'card'. On this card it would read the movies/series listed on it, and mark them as already
/// watched. And another Card under it, for watchlisted shows. paste 50 shows you have in notes, and
/// it detects and adds them to the correct list"). Each line is tidied into a name to look for
/// (`lines`), and what the searches find for it is weighed (`match`): the same name first, then the
/// same year, then the kind the line says; how sure that is decides whether it's ticked at first.
public enum PastedList {
    /// Lines read at most from one paste.
    public static let most = 300

    /// A film or a series, as a line says it.
    public struct Line: Identifiable, Equatable, Sendable {
        /// Its place among the lines kept.
        public let id: Int
        /// The line as it was pasted, tidied of spaces.
        public let text: String
        /// The name looked for: without a list's bullet or number, a season, a year, a tick.
        public let name: String
        /// With a number at its end kept, when that number may be part of the name as much as
        /// its year ("Wonder Woman 1984", "Dune 2021").
        public let whole: String?
        public let year: Int?
        /// "series" or "movie" when the line, or a heading above it ("TV Shows:"), says which.
        public let kind: String?

        public init(id: Int, text: String, name: String, whole: String? = nil, year: Int? = nil, kind: String? = nil) {
            (self.id, self.text, self.name, self.whole, self.year, self.kind) = (id, text, name, whole, year, kind)
        }

        /// What's searched for: the name, and the whole of it when it ends in a number.
        public var searches: [String] { [name] + (whole.map { [$0] } ?? []) }
    }

    /// The pasted text, a title a line: blank lines, headings ("Movies:", "TV Shows") and lines
    /// with nothing of a name left are skipped, and the same title twice is kept once. A
    /// heading saying movies or series counts for the lines under it, until the next heading.
    public static func lines(_ pasted: String, thisYear: Int = Calendar.current.component(.year, from: Date())) -> [Line] {
        var made: [Line] = []
        var seen = Set<String>()
        var heading: String?
        for raw in pasted.components(separatedBy: .newlines) {
            guard made.count < most else { break }
            let text = raw.replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
                .trimmingCharacters(in: .whitespaces)
            guard !text.isEmpty else { continue }
            if let said = Self.heading(text) {
                heading = said.kind
                continue
            }
            guard let line = read(text, thisYear: thisYear) else { continue }
            let kind = line.kind ?? heading
            let key = "\(PastedList.normal(line.name))|\(line.year.map(String.init) ?? "")|\(kind ?? "")"
            guard seen.insert(key).inserted else { continue }
            made.append(Line(id: made.count, text: text, name: line.name, whole: line.whole, year: line.year, kind: kind))
        }
        return made
    }

    /// A heading in a list, not a title, and the kind it says, if any.
    struct Heading: Equatable {
        let kind: String?
    }

    /// A line ending in a colon ("TV shows I've watched:"), or one of the words a list is headed with.
    static func heading(_ text: String) -> Heading? {
        let plain = text.lowercased()
            .trimmingCharacters(in: CharacterSet(charactersIn: "#*-–—:•").union(.whitespaces))
        func kind() -> String? {
            if plain.range(of: #"\b(tv|series|shows?|seasons?|sitcoms?|docuseries)\b"#, options: .regularExpression) != nil {
                return "series"
            }
            if plain.range(of: #"\b(movies?|films?|cinema)\b"#, options: .regularExpression) != nil { return "movie" }
            return nil
        }
        if text.hasSuffix(":") { return Heading(kind: kind()) }
        let headings: Set<String> = [
            "movies", "films", "movie", "film", "tv", "tv shows", "tv show", "shows", "series", "tv series", "anime",
            "watched", "to watch", "watchlist", "watch list", "my watchlist", "already watched", "seen", "documentaries",
        ]
        return headings.contains(plain) ? Heading(kind: kind()) : nil
    }

    // MARK: one line

    /// A line tidied into a name: a list's bullet, number or tick box before it; a tick, a score
    /// or "watched" after it; a season or an episode ("S2", "Season 1-3", "S01E05", "2x05");
    /// "(TV series)"; and a year, "(2019)", "[2019]", "(2008–2013)" or one at its end. Nil when no
    /// name is left.
    static func read(_ text: String, thisYear: Int) -> Line? {
        var rest = text
        // Before it: bullets, tick boxes and numbers ("1.", "2)", "#3", "(4)", "5 -"), as often as
        // they come. Never a number that's part of a name ("10 Things…", "(500) Days of Summer",
        // "3:10 to Yuma", "2001: A Space Odyssey").
        let lead =
            #"^(?:[•◦▪▫‣⁃·●○■□☐☑✓✔✅❌➤→–—]+\s*|[-*+>]+\s+|\[[ xX✓✔]?\]\s*|\(\d{1,2}\)\s+|#\d{1,3}[.):]?\s+|\d{1,3}[.)](?=\s|\p{L}|$)\s*|\d{1,3}\s+[-–—]\s+)"#
        while let found = rest.range(of: lead, options: .regularExpression), !found.isEmpty {
            rest.removeSubrange(found)
        }
        // After it: ticks, stars, a score ("8/10"), "watched", and stray separators, as often as they come.
        let tail =
            #"(?i)(?:\s*[-–—:|,;/.]\s*$|\s*[✓✔✅☑❌✗✘★☆⭐✩✪✫✬✭✮✯✰½🌟\x{FE0F}]+\s*$|\s*\(?\d{1,2}(?:\.\d)?\s*/\s*(?:10|5|100)\)?\s*$|\s*(?:[-–—:|]\s*|[(\[]\s*)(?:watched|seen|rewatch(?:ed)?|finished|done|to watch|unwatched)\s*[)\]]?\s*$|\s+(?:watched|rewatched)\s*$)"#
        while let found = rest.range(of: tail, options: .regularExpression), !found.isEmpty {
            rest.removeSubrange(found)
        }
        rest = unquoted(rest.trimmingCharacters(in: .whitespaces))
        var kind: String?
        var year: Int?
        // "(TV series)", "- TV show", "(miniseries)", "(anime)", "TV series" at the end: a series.
        let tvMark =
            #"(?i)(?:\s*[(\[]\s*(?:tv(?:\s+(?:series|show|mini-?\s?series))?|series|show|mini-?\s?series|limited series|docu-?series|anime)\s*[)\]]|\s+[-–—:|]\s*(?:tv(?:\s+(?:series|show))?|series|show|mini-?\s?series|anime)|\s+(?:tv series|tv show|mini-?\s?series|limited series|docu-?series))\s*$"#
        if let found = rest.range(of: tvMark, options: .regularExpression) {
            rest.removeSubrange(found)
            kind = "series"
        }
        // "(Movie)", "(film)": a film.
        if let found = rest.range(of: #"(?i)\s*[(\[]\s*(?:movie|film)\s*[)\]]\s*$"#, options: .regularExpression) {
            rest.removeSubrange(found)
            kind = "movie"
        }
        // A season or an episode, and everything after it but a year: a series.
        let season =
            #"(?i)(?:\bS\d{1,2}(?:\s*E\d{1,3})?\b|\b\d{1,2}x\d{2,3}\b|\b(?:seasons?|series)\s*#?\s*(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\b|\b(?:the complete series|complete series|all seasons)\b)"#
        if let found = rest.range(of: season, options: .regularExpression) {
            let after = String(rest[found.lowerBound...])
            rest = String(rest[..<found.lowerBound])
            kind = "series"
            year = yearIn(after, thisYear: thisYear)
        }
        // A year in brackets, or a span of years in them (a series): the last such.
        let bracketed = #"[(\[]\s*(?:19|20)\d{2}\s*(?:[-–—]\s*(?:(?:19|20)\d{2})?\s*)?(?:,[^)\]]*)?[)\]]"#
        if let found = last(bracketed, in: rest) {
            let inside = String(rest[found])
            if let first = Int(String(inside.filter(\.isNumber).prefix(4))), plausible(first, thisYear) {
                year = year ?? first
                if inside.range(of: #"[-–—]"#, options: .regularExpression) != nil { kind = kind ?? "series" }
                rest.removeSubrange(found)
            }
        }
        rest = trimmed(rest)
        // A year at its end: kept as the name's too, since it may be part of it.
        var whole: String?
        if year == nil, let found = rest.range(of: #"[\s,]+[-–—]?\s*(?:19|20)\d{2}$"#, options: .regularExpression) {
            let number = Int(String(rest[found].filter(\.isNumber))) ?? 0
            let before = trimmed(String(rest[..<found.lowerBound]))
            if plausible(number, thisYear), !before.isEmpty {
                whole = rest
                year = number
                rest = before
            }
        }
        rest = trimmed(rest)
        guard rest.contains(where: { $0.isLetter || $0.isNumber }) else { return nil }
        return Line(id: 0, text: text, name: rest, whole: whole, year: year, kind: kind)
    }

    /// Where a pattern is found last in some words.
    static func last(_ pattern: String, in words: String) -> Range<String.Index>? {
        guard let expression = try? NSRegularExpression(pattern: pattern) else { return nil }
        let all = expression.matches(in: words, range: NSRange(words.startIndex..., in: words))
        return all.last.flatMap { Range($0.range, in: words) }
    }

    /// A year a film or a series may be of: not before 1900, nor more than two years ahead.
    static func plausible(_ year: Int, _ thisYear: Int) -> Bool { (1900...(thisYear + 2)).contains(year) }

    /// The first year in some words, if any.
    static func yearIn(_ words: String, thisYear: Int) -> Int? {
        guard let found = words.range(of: #"\b(?:19|20)\d{2}\b"#, options: .regularExpression),
            let year = Int(words[found]), plausible(year, thisYear)
        else { return nil }
        return year
    }

    /// Spaces, and separators left at either end ("Friends -", ": Dune").
    static func trimmed(_ words: String) -> String {
        words.trimmingCharacters(in: CharacterSet(charactersIn: "-–—:|,;/").union(.whitespaces))
            .replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
    }

    /// Quotes around a name taken off ("“Dune”", "'Up'").
    static func unquoted(_ words: String) -> String {
        let pairs: [(Character, Character)] = [("\"", "\""), ("“", "”"), ("'", "'"), ("‘", "’"), ("«", "»")]
        for (open, close) in pairs where words.count > 2 && words.first == open && words.last == close {
            return String(words.dropFirst().dropLast())
        }
        return words
    }

    /// A name as compared: lower case, no accents, "&" as "and", apostrophes gone, other
    /// punctuation as spaces, and no "the" before it. So "office" finds "The Office!", and
    /// "Shogun" finds "Shōgun".
    public static func normal(_ name: String) -> String {
        var made = name.folding(options: [.diacriticInsensitive, .caseInsensitive], locale: nil).lowercased()
        made = made.replacingOccurrences(of: "&", with: " and ")
        made = made.replacingOccurrences(of: #"['’`]"#, with: "", options: .regularExpression)
        made = made.replacingOccurrences(of: #"[^a-z0-9]+"#, with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespaces)
        if made.hasPrefix("the ") { made.removeFirst(4) }
        return made
    }

    // MARK: what was found

    /// One title a search found, with where: TMDB's popularity when it's TMDB's find, and its
    /// place in what its search gave (the plugins' order).
    public struct Found: Equatable, Sendable {
        public let item: MediaItem
        public let popularity: Double?
        public let place: Int

        public init(_ item: MediaItem, popularity: Double? = nil, place: Int) {
            (self.item, self.popularity, self.place) = (item, popularity, place)
        }
    }

    /// What TMDB found, then what the plugins found that TMDB hadn't. A plugin's title that's
    /// TMDB's too (the same name, year and kind) gives TMDB's its id when TMDB's has none of the
    /// plugins' kind yet (`hasId`: an IMDb id, which the app's records go by), so it needn't be
    /// asked of TMDB.
    public static func together(tmdb: [Found], plugins: [MediaItem], hasId: (String) -> Bool) -> [Found] {
        func key(_ item: MediaItem) -> String { "\(PastedList.normal(item.name))|\(year(of: item) ?? 0)|\(item.type)" }
        var made = tmdb
        var place = Dictionary(made.enumerated().map { (key($1.item), $0) }, uniquingKeysWith: { first, _ in first })
        var ids = Set(made.map(\.item.id))
        for (at, item) in plugins.enumerated() where !ids.contains(item.id) {
            if let index = place[key(item)] {
                let theirs = made[index]
                if !hasId(theirs.item.id), hasId(item.id) {
                    made[index] = Found(theirs.item.withId(item.id), popularity: theirs.popularity, place: theirs.place)
                    ids.insert(item.id)
                }
                continue
            }
            place[key(item)] = made.count
            ids.insert(item.id)
            made.append(Found(item, place: at))
        }
        return made
    }

    /// How sure a line's title is: `sure` (its name, and its year when the line has one, and no
    /// other with that name to speak of), `likely` (its name, but another has it too, or the year
    /// is off: shown to be checked), `closest` (nothing had its name: the nearest, not ticked at
    /// first), or `none` (nothing found).
    public enum Sureness: String, Sendable { case sure, likely, closest, none }

    public struct Match: Equatable, Sendable {
        public let item: MediaItem?
        public let sureness: Sureness
        /// What else was found that may be it, the likeliest first: what a line can be changed to.
        public let others: [MediaItem]
        /// Why it's not sure, in words.
        public let why: String?
    }

    /// The title a line means, of what its searches found (`found`, in the order to prefer
    /// when nothing else tells them apart).
    public static func match(_ line: Line, among found: [Found]) -> Match {
        let name = PastedList.normal(line.name)
        let whole = line.whole.map(PastedList.normal)
        guard !name.isEmpty else { return Match(item: nil, sureness: .none, others: [], why: nil) }
        struct Weighed {
            let found: Found
            /// 4: its name with the number at its end; 3: its name; 2: its name, before a subtitle;
            /// 1: every word of one in the other; 0: none.
            let named: Int
            /// 2: its year; 1: a year either side; 0: the line has none, or the title says none; -2: another.
            let dated: Int
            /// 1: the kind the line says; 0: it says none; -3: another.
            let kinded: Int
        }
        let wanted = words(name)
        let weighed = found.compactMap { one -> Weighed? in
            let theirs = PastedList.normal(one.item.name)
            let named: Int
            if let whole, theirs == whole {
                named = 4
            } else if theirs == name {
                named = 3
            } else if let colon = one.item.name.firstIndex(where: { ":–—".contains($0) }),
                PastedList.normal(String(one.item.name[..<colon])) == name
            {
                named = 2
            } else if isClose(theirs, name) || (whole.map { isClose(theirs, $0) } ?? false) {
                // The same name typed a little wrong ("Breking Bad").
                named = 1
            } else {
                let them = words(theirs)
                let shared = wanted.intersection(them).count
                named = !wanted.isEmpty && (wanted.isSubset(of: them) || them.isSubset(of: wanted))
                    && Double(shared) / Double(max(wanted.count, them.count)) >= 0.5 ? 1 : 0
            }
            guard named > 0 else { return nil }
            var dated = 0
            // Its name with the number at its end ("Wonder Woman 1984"): the number was the name's, not a year.
            if named < 4, let asked = line.year, let year = year(of: one.item) {
                dated = year == asked ? 2 : abs(year - asked) == 1 ? 1 : -2
                // A series named with a later season's year ("The Crown 2020") is still it.
                if dated < 0, one.item.type == "series", asked > year { dated = 0 }
            }
            let kinded = line.kind.map { $0 == one.item.type ? 1 : -3 } ?? 0
            return Weighed(found: one, named: named, dated: dated, kinded: kinded)
        }
        // The best first: a kind the line allows, the name, the kind it says, the year, then TMDB's
        // popularity (a title of TMDB's before one with none), then the order found.
        let ranked = weighed.sorted { a, b in
            if (a.kinded >= 0) != (b.kinded >= 0) { return a.kinded >= 0 }
            if a.named != b.named { return a.named > b.named }
            if a.kinded != b.kinded { return a.kinded > b.kinded }
            if a.dated != b.dated { return a.dated > b.dated }
            let (pa, pb) = (a.found.popularity ?? -1, b.found.popularity ?? -1)
            if pa != pb { return pa > pb }
            return a.found.place < b.found.place
        }
        var seen = Set<String>()
        let unique = ranked.filter { seen.insert($0.found.item.id).inserted }
        guard let best = unique.first else { return Match(item: nil, sureness: .none, others: [], why: "Nothing was found by this name.") }
        let item = best.found.item
        let others = Array(unique.dropFirst().prefix(8).map(\.found.item))
        guard best.named >= 3, best.kinded >= 0 else {
            return Match(
                item: item, sureness: .closest, others: others,
                why: best.kinded < 0
                    ? "Nothing of that kind by this name: the closest is a \(item.type == "series" ? "series" : "film")."
                    : "Nothing by exactly this name: this is the closest.")
        }
        // Others with the very same name (or name and number), of a kind the line allows.
        let rivals = unique.dropFirst().filter { $0.named == best.named && $0.kinded >= 0 }
        func likely(_ why: String) -> Match { Match(item: item, sureness: .likely, others: others, why: why) }
        let sure = Match(item: item, sureness: .sure, others: others, why: nil)
        if best.named < 4, let asked = line.year {
            switch best.dated {
            case 2:
                return rivals.contains { $0.dated == 2 } ? likely("More than one has this name and year: check it's the one.") : sure
            case 1:
                return likely("Its year is \(year(of: item).map(String.init) ?? "not known"), not \(asked): check it's the one.")
            case 0:
                return rivals.isEmpty ? sure : likely("\(rivals.count + 1) have this name: check it's the one.")
            default:
                return likely("Its year is \(year(of: item).map(String.init) ?? "not known"), not \(asked): check it's the one.")
            }
        }
        // No year: sure when nothing else has the name, or when TMDB has this one ten times as
        // popular as the next with it ("Friends" the series, not a film of that name).
        guard let next = rivals.first else { return sure }
        if let top = best.found.popularity, top >= max(next.found.popularity ?? 0, 0.1) * 10 { return sure }
        let years = ([best] + rivals).prefix(3).compactMap { year(of: $0.found.item) }.map(String.init)
        return likely("\(rivals.count + 1) have this name\(years.count > 1 ? " (\(years.joined(separator: ", ")))" : ""): check it's the one.")
    }

    /// The words of a name as compared.
    static func words(_ normal: String) -> Set<String> {
        Set(normal.split(separator: " ").map(String.init))
    }

    /// Two names near enough to be the same one typed a little wrong (the owner, 2026-11-13:
    /// "maybe just slight spelling words"): one letter in six at most, and never for a name of
    /// three letters or fewer, where one letter is another word ("Up", "Us").
    static func isClose(_ one: String, _ other: String) -> Bool {
        let longest = max(one.count, other.count)
        // Short names are taken as they are: "Dune" and "Duke", "Heat" and "Head", "Up" and
        // "Us" are each other's neighbours by a letter and are not the same film.
        guard longest >= 7 else { return one == other }
        let allowed = min(3, longest / 6)
        guard abs(one.count - other.count) <= allowed else { return false }
        return apart(one, other, atMost: allowed) <= allowed
    }

    /// How many letters apart two names are (insertions, deletions, swaps), giving up once
    /// they're more than `atMost` apart.
    static func apart(_ one: String, _ other: String, atMost: Int) -> Int {
        let (a, b) = (Array(one), Array(other))
        guard !a.isEmpty else { return b.count }
        guard !b.isEmpty else { return a.count }
        var row = Array(0...b.count)
        for (i, left) in a.enumerated() {
            var above = row[0]
            row[0] = i + 1
            var best = row[0]
            for (j, right) in b.enumerated() {
                let corner = above
                above = row[j + 1]
                row[j + 1] = left == right ? corner : min(corner, above, row[j]) + 1
                best = min(best, row[j + 1])
            }
            if best > atMost { return atMost + 1 }
        }
        return row[b.count]
    }

    /// A title's year, from its own ("2008", "2008–2013", "2008-").
    public static func year(of item: MediaItem) -> Int? {
        guard let text = item.year, text.count >= 4 else { return nil }
        return Int(text.prefix(4))
    }

    /// Whether a line starts ticked: when it's sure or likely.
    public static func ticked(_ sureness: Sureness) -> Bool { sureness == .sure || sureness == .likely }

    // MARK: a line and what it's to add

    /// One line of the paste, with what was found for it and what's ticked (the owner,
    /// 2026-11-13: "The card will then show, law and order, and the options under it, picture of
    /// the series/show and the correct title, you can select several or just 1, 1 tap and it
    /// deselects or selects"). Several may be ticked for one line: "Law & Order" can add the
    /// series, Special Victims Unit and Criminal Intent at once.
    public struct Reading: Identifiable, Equatable, Sendable {
        public let line: Line
        public let sureness: Sureness
        /// Why it needs a look, in words; nil when it's sure.
        public let why: String?
        /// Every title offered for the line, the likeliest first (what a tap ticks).
        public let options: [MediaItem]
        /// Which are ticked, by id.
        public var chosen: Set<String>

        public var id: Int { line.id }

        public init(line: Line, sureness: Sureness, why: String? = nil, options: [MediaItem], chosen: Set<String>) {
            (self.line, self.sureness, self.why) = (line, sureness, why)
            (self.options, self.chosen) = (options, chosen)
        }

        /// Shown with its options open: the ones that aren't sure of themselves.
        public var needsALook: Bool { sureness != .sure }
        /// Nothing was found by this name at all.
        public var foundNothing: Bool { options.isEmpty }
        /// The titles this line will add.
        public var picked: [MediaItem] { options.filter { chosen.contains($0.id) } }
    }

    /// A line with what its searches found: the best is ticked when it's sure or likely, and
    /// nothing is ticked when only something close turned up (the owner looks at it first).
    public static func reading(_ line: Line, among found: [Found]) -> Reading {
        let match = match(line, among: found)
        let options = (match.item.map { [$0] } ?? []) + match.others.filter { $0.id != match.item?.id }
        let chosen = ticked(match.sureness) ? Set([match.item?.id].compactMap { $0 }) : []
        return Reading(line: line, sureness: match.sureness, why: match.why, options: options, chosen: chosen)
    }

    /// A tap on one of a line's options: ticked if it wasn't, unticked if it was (the owner: "1
    /// tap and it deselects or selects"). Several at once are allowed.
    public static func tapped(_ id: String, in reading: Reading) -> Reading {
        var made = reading
        if made.chosen.contains(id) {
            made.chosen.remove(id)
        } else {
            made.chosen.insert(id)
        }
        return made
    }

    /// What the card says when the looking is done (the owner, 2026-11-13: "58/60 have identical
    /// matches to what you wrote, and 2 may have more than 1 match"): how many were found
    /// outright, and how many want a look. "" while nothing has been read. `choosing` is the word
    /// for picking one, as the owner said it on a tablet ("tap"); the Mac says "click".
    public static func said(_ readings: [Reading], choosing: String = "tap") -> String {
        guard !readings.isEmpty else { return "" }
        let sure = readings.filter { $0.sureness == .sure }.count
        let nothing = readings.filter(\.foundNothing).count
        let look = readings.count - sure - nothing
        var parts = ["\(sure) of \(readings.count) found exactly"]
        if look > 0 {
            parts.append("\(look) may be more than one — \(choosing) the right one\(look == 1 ? "" : "s")")
        }
        if nothing > 0 { parts.append("\(nothing) found nothing") }
        return parts.joined(separator: " · ")
    }

    /// Everything ticked, each title once, in the order the lines were pasted.
    public static func picked(_ readings: [Reading]) -> [MediaItem] {
        var seen = Set<String>()
        return readings.flatMap(\.picked).filter { seen.insert($0.id).inserted }
    }
}

extension MediaItem {
    /// The same title under another id (TMDB's find, by the IMDb id the plugins know it by).
    public func withId(_ id: String) -> MediaItem {
        let made = MediaItem(
            id: id, type: type, name: name, poster: poster, posterShape: posterShape, year: year, rating: rating,
            genres: genres)
        return anime == true ? made.asAnime : made
    }
}

/// Titles marked watched by the owner's word, as a pasted list gave them (2026-11-13), so the
/// Watchlist's Watched can show them, a series among them is kept up with for its new episodes,
/// and New Episodes knows its name, though it was never played, listed or given a heart. Kept for
/// each profile on this Mac, the newest first, at most `most`, and it travels with an export's
/// records (`SettingsCarrier`). Whether each is watched is still `Finished`'s and
/// `SeriesProgress`'s.
public struct MarkedTitles: Equatable, Sendable {
    public static let most = 2000
    public private(set) var items: [MediaItem]

    public init(_ items: [MediaItem] = []) { self.items = items }

    /// These put first, each once (one kept before moves up), at most `most` in all.
    public mutating func note(_ marked: [MediaItem]) {
        var seen = Set<String>()
        let fresh = marked.filter { seen.insert($0.id).inserted }
        items = Array((fresh + items.filter { !seen.contains($0.id) }).prefix(Self.most))
    }

    public static let key = "markedTitles"

    public static func saved(in defaults: UserDefaults = .standard) -> MarkedTitles {
        guard let data = defaults.data(forKey: key), let items = try? JSONDecoder().decode([MediaItem].self, from: data)
        else { return MarkedTitles() }
        return MarkedTitles(items)
    }

    public func save(in defaults: UserDefaults = .standard) {
        if let data = try? JSONEncoder().encode(items) { defaults.set(data, forKey: Self.key) }
    }
}

extension Watchlist {
    /// Titles put on the list by the owner's word (a pasted list, 2026-11-13): those not on it
    /// go first, in the order given; one on it already stays where it is. Nothing is taken off.
    /// Each is no longer "taken off here", as when it's added from its page. Returns how many
    /// were added.
    @discardableResult
    public mutating func put(_ these: [MediaItem]) -> Int {
        var seen = Set(items.map(\.id))
        let fresh = these.filter { seen.insert($0.id).inserted }
        self = Watchlist(fresh + items, takenOff: takenOff.subtracting(these.map(\.id)))
        return fresh.count
    }
}
