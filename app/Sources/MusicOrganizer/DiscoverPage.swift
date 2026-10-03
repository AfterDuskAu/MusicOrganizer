import Foundation
import MusicOrganizerKit
import Observation

/// One page of Discover's picks (What's New, or Find): what was asked for, how the
/// asking is going, and what came back.
@MainActor
@Observable
final class DiscoverPage {
    typealias Ask = (
        _ seeds: [DiscoverSeed], _ count: Int, _ shuffle: String, _ page: String,
        _ exclude: [String]
    ) async throws -> DiscoverAnswer

    /// How many more songs Show More asks for.
    static let moreStep = 25

    /// Names this page in the engine's progress notes.
    let name: String
    private(set) var picks: [DiscoverPick] = []
    private(set) var working = false
    /// Radios asked for so far, and how many are expected to be needed.
    private(set) var done = 0
    private(set) var of = 0
    private(set) var problem: String?
    /// Something the engine wants said about the picks ("Found 37 new songs, not 50…").
    private(set) var note: String?
    /// The starting points as the engine took them (typed words come back as the genre
    /// or the artist they turned out to be).
    private(set) var seeds: [DiscoverAnswer.Seed] = []
    /// False until the first request has been made.
    private(set) var hasAsked = false
    /// Show More is asking for more.
    private(set) var loadingMore = false
    /// Show More found nothing new: the radios have nothing more to give.
    private(set) var noMore = false
    /// The picks ticked for "Download Selected".
    var selected = Set<String>()

    @ObservationIgnored var ask: Ask?
    @ObservationIgnored private var last: (seeds: [DiscoverSeed], count: Int)?
    /// Goes up for "Different Songs": the engine starts from other songs of the owner's.
    @ObservationIgnored private var round = 0

    init(named name: String) {
        self.name = name
    }

    /// Ask for picks. `different` starts from other songs than last time; without it the
    /// same request on the same day gives the same picks, at once.
    func find(_ seeds: [DiscoverSeed], count: Int, different: Bool = false) {
        Task { await run(seeds, count: count, different: different) }
    }

    /// The same, waited for: true if picks came back (the guide asks this way, and
    /// carries on when they have).
    @discardableResult
    func run(_ seeds: [DiscoverSeed], count: Int, different: Bool = false) async -> Bool {
        guard !working, !seeds.isEmpty, let ask else { return false }
        if different { round += 1 }
        last = (seeds, count)
        hasAsked = true
        working = true
        problem = nil
        (done, of) = (0, 0)
        defer { working = false }
        do {
            let answer = try await ask(seeds, count, "\(Self.today()) \(round)", name, [])
            picks = answer.picks
            noMore = false
            note = answer.note
            self.seeds = answer.seeds ?? []
            selected = []
            return true
        } catch {
            problem = error.localizedDescription
            return false
        }
    }

    /// Back to how the page starts: another profile's library is in use now.
    func reset() {
        (picks, problem, note, seeds, hasAsked, selected, last) = ([], nil, nil, [], false, [], nil)
        (loadingMore, noMore, problemWithMore) = (false, false, nil)
        (done, of, round) = (0, 0, 0)
    }

    /// Show More: another 25 songs from the same starting points, none of them already
    /// on the page. Each time starts from other songs of the owner's, so the radios
    /// reach further.
    func more() {
        guard !working, !loadingMore, let last, let ask else { return }
        (loadingMore, noMore, problemWithMore) = (true, false, nil)
        round += 1
        let shown = picks.map(\.videoId)
        Task {
            defer { loadingMore = false }
            do {
                let answer = try await ask(
                    last.seeds, Self.moreStep, "\(Self.today()) \(round)", name, shown)
                let new = answer.picks.filter { pick in !picks.contains { $0.id == pick.id } }
                picks += new
                noMore = new.isEmpty
            } catch {
                problemWithMore = error.localizedDescription
            }
        }
    }

    /// Why Show More didn't work, said under the songs already found (which stay).
    var problemWithMore: String?

    /// The last request again (after it failed), or with other starting songs.
    func again(different: Bool = false) {
        guard let last else { return }
        find(last.seeds, count: last.count, different: different)
    }

    func progress(done: Int, of: Int) {
        guard working else { return }
        (self.done, self.of) = (done, of)
    }

    private static func today() -> String {
        Date.now.formatted(.iso8601.year().month().day())
    }
}
