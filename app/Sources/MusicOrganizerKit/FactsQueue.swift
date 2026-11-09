import Foundation

/// The titles waiting for TMDB's word (`tmdb.facts`), asked steadily: a few out at once, each
/// one taken up as soon as another is answered (2026-11-09, as the tablet app has it since
/// 2026-11-07). Until then the app asked in tens, each ten waiting for its slowest, and a
/// question that failed was taken as "TMDB doesn't know it": the title was hidden from a child
/// until the app was opened again. A profile being changed restarts the engine, and every title
/// of a list asked in that moment was hidden (the owner, 2026-11-09: "it may record them, but
/// they dont show up").
///
/// Now a question that isn't answered puts its title back in line. Only a question the engine
/// answered without it (TMDB couldn't be asked just now) counts as a try, and a title is given
/// up after `mostTries`; the engine stopping or starting (a profile being changed) never counts.
public struct FactsQueue: Sendable {
    public struct Ask: Equatable, Sendable {
        public let id: String
        public let type: String
        /// What's asked of it: "language", "rating".
        public var fields: Set<String>
    }

    public static let mostTries = 5

    /// In line, in the order they were put there.
    private var waiting: [Ask] = []
    /// Asked, not answered yet: what was asked of each.
    private var out: [String: Set<String>] = [:]
    private var tries: [String: Int] = [:]

    public init() {}

    public var hasWaiting: Bool { !waiting.isEmpty }
    public var count: Int { waiting.count + out.count }

    /// Put in line with these fields, unless it's in line or out with them already.
    public mutating func add(_ id: String, type: String, fields: Set<String>) {
        guard !fields.isEmpty else { return }
        if let asked = out[id], asked.isSuperset(of: fields) { return }
        if let place = waiting.firstIndex(where: { $0.id == id }) {
            waiting[place].fields.formUnion(fields)
        } else {
            waiting.append(Ask(id: id, type: type, fields: fields))
        }
    }

    /// The next to ask, now out.
    public mutating func next() -> Ask? {
        guard !waiting.isEmpty else { return nil }
        let ask = waiting.removeFirst()
        out[ask.id, default: []].formUnion(ask.fields)
        return ask
    }

    /// Answered.
    public mutating func answered(_ ask: Ask) {
        out[ask.id] = nil
        tries[ask.id] = nil
    }

    /// Not answered: back in line, at the front when nothing was its fault (the engine wasn't
    /// there), else at the end. False when it has been tried `mostTries` times: given up.
    public mutating func failed(_ ask: Ask, counts: Bool) -> Bool {
        out[ask.id] = nil
        if counts {
            let tried = (tries[ask.id] ?? 0) + 1
            guard tried < Self.mostTries else {
                tries[ask.id] = nil
                return false
            }
            tries[ask.id] = tried
            add(ask.id, type: ask.type, fields: ask.fields)
        } else if let place = waiting.firstIndex(where: { $0.id == ask.id }) {
            waiting[place].fields.formUnion(ask.fields)
        } else {
            waiting.insert(ask, at: 0)
        }
        return true
    }

    /// What's known of a title once an answer for these fields has come: the fields asked taken
    /// from the answer, the rest kept from what was known (an answer about its rating alone
    /// says nothing of its language).
    public static func merged(_ known: TitleFacts?, _ said: TitleFacts, fields: Set<String>) -> TitleFacts {
        TitleFacts(
            id: said.id,
            language: fields.contains("language") ? said.language : known?.language,
            rating: fields.contains("rating") ? said.rating : known?.rating)
    }
}
