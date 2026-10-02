import Foundation
import MusicOrganizerKit
import Observation

/// Discover → Import Playlists: a playlist read from where it lives, each of its songs
/// found on YouTube Music, and what's said once its downloads are queued.
@MainActor
@Observable
final class ImportPage {
    enum Phase: Equatable {
        case idle, reading, finding, ready
        case failed(String)
    }

    enum Note: Equatable {
        case started(String)
        case failed(String)
    }

    typealias Read = (_ what: ImportRequest) async throws -> ImportedPlaylist
    typealias List = () async throws -> [SpotifyPlaylist]
    typealias Find = (_ tracks: [ImportTrack]) async throws -> [ImportFound]
    typealias Download = (_ name: String, _ owned: [String], _ songs: [ImportCandidate])
        async throws -> String

    private(set) var phase = Phase.idle
    /// The playlist's name where it came from: the owner's playlist gets the same one.
    private(set) var name = ""
    private(set) var rows: [ImportRow] = []
    /// How many of the songs have been looked for so far.
    private(set) var done = 0
    /// Why the looking stopped early, if it did (YouTube asked us to slow down, say).
    private(set) var problem: String?
    private(set) var downloading = false
    /// The playlist is longer than was read: only its first songs are listed.
    private(set) var tooLong = false
    /// The not-sure songs the owner ticked to download anyway.
    var ticked = Set<Int>()
    var note: Note?

    /// The signed-in Spotify account's playlists, to choose one from.
    private(set) var spotifyPlaylists: [SpotifyPlaylist] = []
    private(set) var listing = false
    private(set) var listProblem: String?

    @ObservationIgnored var read: Read?
    @ObservationIgnored var list: List?
    @ObservationIgnored var find: Find?
    @ObservationIgnored var download: Download?
    /// Goes up with each new playlist read: an older one's answers are dropped.
    @ObservationIgnored private var run = 0
    @ObservationIgnored private var chunkStart = 0

    var isBusy: Bool { phase == .reading || phase == .finding || downloading }
    var counts: Imports.Counts { Imports.counts(rows) }
    var toDownload: [ImportCandidate] { Imports.toDownload(rows, ticked: ticked) }

    /// Ask Spotify which playlists the signed-in account has.
    func listSpotifyPlaylists() {
        guard !listing, let list else { return }
        (listing, listProblem) = (true, nil)
        Task {
            defer { listing = false }
            do {
                spotifyPlaylists = try await list()
            } catch {
                listProblem = error.localizedDescription
            }
        }
    }

    /// Signed out, or signed in as someone else: the old list isn't theirs.
    func forgetSpotifyPlaylists() {
        (spotifyPlaylists, listProblem) = ([], nil)
    }

    /// Read a playlist from where it lives, then look for each of its songs.
    func open(_ what: ImportRequest) {
        guard !isBusy, let read else { return }
        run += 1
        let mine = run
        (phase, rows, name, ticked, note, done, problem) = (.reading, [], "", [], nil, 0, nil)
        Task {
            do {
                let playlist = try await read(what)
                guard mine == run else { return }
                name = playlist.name
                rows = playlist.tracks.enumerated().map { ImportRow(id: $0.offset, track: $0.element) }
                guard !rows.isEmpty else {
                    phase = .failed("That playlist has no songs in it that can be read.")
                    return
                }
                tooLong = playlist.more == true
                await findRest(mine)
            } catch {
                guard mine == run else { return }
                phase = .failed(error.localizedDescription)
            }
        }
    }

    /// Look for the songs that haven't been looked for yet (after the looking stopped
    /// early: what was found before then is kept).
    func carryOn() {
        guard phase == .ready, rows.contains(where: { $0.found == nil }) else { return }
        let mine = run
        Task { await findRest(mine) }
    }

    private func findRest(_ mine: Int) async {
        guard let find else { return }
        (phase, problem) = (.finding, nil)
        var start = rows.firstIndex { $0.found == nil } ?? rows.count
        done = start
        while start < rows.count {
            let end = min(start + Imports.atOnce, rows.count)
            chunkStart = start
            do {
                let found = try await find(rows[start..<end].map(\.track))
                guard mine == run else { return }
                for (offset, answer) in found.enumerated() where start + offset < rows.count {
                    rows[start + offset].found = answer
                }
            } catch {
                guard mine == run else { return }
                problem = error.localizedDescription
                break
            }
            start = end
            done = end
        }
        phase = .ready
    }

    /// The engine says how far one lot of songs has got.
    func progress(done: Int) {
        if phase == .finding { self.done = min(chunkStart + done, rows.count) }
    }

    /// Make the owner's playlist of the same name, put the songs they already have in
    /// it, and queue the rest: each joins the playlist as it arrives.
    func downloadAutomatically() {
        guard phase == .ready, !downloading, let download else { return }
        let songs = toDownload
        let owned = Imports.ownedIds(rows)
        guard !songs.isEmpty || !owned.isEmpty else { return }
        downloading = true
        let mine = run
        Task {
            defer { downloading = false }
            do {
                let words = try await download(name, owned, songs)
                guard mine == run else { return }
                // What was sent is on its way now: it isn't offered a second time.
                let sent = Set(songs.map(\.videoId))
                for index in rows.indices {
                    if let found = rows[index].found, let candidate = found.candidate,
                        sent.contains(candidate.videoId), found.kind != .owned
                    {
                        rows[index].found = ImportFound(state: .queued, candidate: candidate)
                    }
                }
                ticked = []
                note = .started(words)
            } catch {
                note = .failed(error.localizedDescription)
            }
        }
    }
}
