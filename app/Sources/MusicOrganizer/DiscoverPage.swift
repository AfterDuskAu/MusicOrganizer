import Foundation
import MusicOrganizerKit
import Observation

/// One page of Discover's picks (What's New, or Find): what was asked for, how the
/// asking is going, and what came back.
@MainActor
@Observable
final class DiscoverPage {
    typealias Ask = (_ seeds: [DiscoverSeed], _ count: Int, _ shuffle: String, _ page: String)
        async throws -> DiscoverAnswer

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
    /// False until the first request has been made.
    private(set) var hasAsked = false
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
        guard !working, !seeds.isEmpty, let ask else { return }
        if different { round += 1 }
        last = (seeds, count)
        hasAsked = true
        working = true
        problem = nil
        (done, of) = (0, 0)
        let shuffle = "\(Self.today()) \(round)"
        Task {
            do {
                let answer = try await ask(seeds, count, shuffle, name)
                picks = answer.picks
                note = answer.note
                selected = []
            } catch {
                problem = error.localizedDescription
            }
            working = false
        }
    }

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
