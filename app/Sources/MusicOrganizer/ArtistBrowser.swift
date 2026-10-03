import Foundation
import MusicOrganizerKit
import Observation

/// Discover → Artist: the artist being looked at, the ones looked at before, and what's
/// been asked for about them (all their songs, an album's songs).
@MainActor
@Observable
final class ArtistBrowser {
    enum Phase: Equatable {
        case idle
        case loading(String)
        case shown
        /// YouTube Music has no artist of this name.
        case missing(String)
        case failed(String)
    }

    typealias LookUp = (_ name: String?, _ artistId: String?) async throws -> ArtistInfo
    typealias Songs = (_ playlistId: String) async throws -> ArtistSongsAnswer
    typealias Album = (_ browseId: String) async throws -> ArtistAlbum

    private(set) var phase = Phase.idle
    private(set) var info: ArtistInfo?
    /// Every song of theirs, once it's been asked for; until then the page's few.
    private(set) var allSongs: [ArtistSong]?
    /// There are more songs than were read.
    private(set) var moreSongs = false
    private(set) var loadingSongs = false
    private(set) var songsProblem: String?
    /// The album that's open, its songs in a sheet.
    var album: ArtistAlbum?
    /// The album being opened, by its id.
    private(set) var openingAlbum: String?
    private(set) var albumProblem: String?
    /// The artists looked at before this one, for Back: the latest last.
    private(set) var before: [Asked] = []
    /// What's typed in the page's search box.
    var query = ""

    struct Asked: Equatable {
        let name: String
        let artistId: String?
    }

    @ObservationIgnored var lookUp: LookUp?
    @ObservationIgnored var findSongs: Songs?
    @ObservationIgnored var findAlbum: Album?
    /// Goes up with each artist asked for: an older one's answer is dropped.
    @ObservationIgnored private var run = 0
    @ObservationIgnored private var last: Asked?

    /// The songs to list: all of theirs if they've been fetched, else the page's own few.
    var songs: [ArtistSong] { allSongs ?? info?.songs ?? [] }
    var canGoBack: Bool { !before.isEmpty }

    /// Back to how the page starts: another profile's library is in use now.
    func reset() {
        run += 1
        (phase, info, allSongs, moreSongs, loadingSongs, songsProblem) = (.idle, nil, nil, false, false, nil)
        (album, openingAlbum, albumProblem, before, query, last) = (nil, nil, nil, [], "", nil)
    }

    /// Look an artist up by name: typed in the box, or a song's artist.
    func open(name: String) {
        let wanted = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !wanted.isEmpty else { return }
        // The artist already showing: nothing to ask.
        if phase == .shown, info?.name.caseInsensitiveCompare(wanted) == .orderedSame { return }
        show(Asked(name: wanted, artistId: nil), remember: true)
    }

    /// One of the artists this one's listeners also play.
    func open(_ related: RelatedArtist) {
        show(Asked(name: related.name, artistId: related.artistId), remember: true)
    }

    /// The artist looked at before this one.
    func back() {
        guard let previous = before.popLast() else { return }
        show(previous, remember: false)
    }

    /// Ask again, after it didn't work.
    func again() {
        if let last { show(last, remember: false) }
    }

    private func show(_ asked: Asked, remember: Bool) {
        guard let lookUp else { return }
        if remember, phase == .shown, let info {
            before.append(Asked(name: info.name, artistId: info.artistId))
        }
        run += 1
        let mine = run
        last = asked
        (phase, allSongs, moreSongs, loadingSongs, songsProblem) = (.loading(asked.name), nil, false, false, nil)
        (album, openingAlbum, albumProblem) = (nil, nil, nil)
        Task {
            do {
                // By id when it's known: that needs no search first.
                let found = try await lookUp(
                    asked.artistId == nil ? asked.name : nil, asked.artistId)
                guard mine == run else { return }
                if found.found {
                    info = found
                    query = found.name
                    phase = .shown
                } else {
                    phase = .missing(asked.name)
                }
            } catch {
                guard mine == run else { return }
                phase = .failed(error.localizedDescription)
            }
        }
    }

    /// Every song of theirs, in place of the page's few. One request a hundred songs.
    func showAllSongs() {
        guard !loadingSongs, allSongs == nil, let findSongs, let list = info?.songsPlaylistId
        else { return }
        let mine = run
        (loadingSongs, songsProblem) = (true, nil)
        Task {
            do {
                let found = try await findSongs(list)
                guard mine == run else { return }
                (allSongs, moreSongs) = (found.songs, found.more)
            } catch {
                guard mine == run else { return }
                songsProblem = error.localizedDescription
            }
            if mine == run { loadingSongs = false }
        }
    }

    /// Open an album, EP or single: its songs, in a sheet.
    func open(_ release: ArtistRelease) {
        guard openingAlbum == nil, let findAlbum else { return }
        let mine = run
        (openingAlbum, albumProblem) = (release.browseId, nil)
        Task {
            do {
                let found = try await findAlbum(release.browseId)
                guard mine == run else { return }
                album = found
            } catch {
                guard mine == run else { return }
                albumProblem = "“\(release.title)” couldn't be opened: \(error.localizedDescription)"
            }
            if mine == run { openingAlbum = nil }
        }
    }
}
