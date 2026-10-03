import Foundation
import MusicOrganizerKit
import Observation

/// Library → Artists: what the Discover side has found (a search's artists), the artist
/// being looked at, the ones looked at before, and what's been asked for about them
/// (all their songs, an album's songs).
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
    typealias Search = (_ query: String) async throws -> [RelatedArtist]
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
    /// The artist asked for (showing, on its way, or not found): the page shows their two
    /// halves. Nil: the page shows the two lists.
    private(set) var opened: Asked?
    /// The artists a search found on YouTube Music, for the Discover list. Nil until a
    /// search is made: the list then shows the artists behind What's New.
    private(set) var results: [ListedArtist]?
    /// What was searched for, to say so when nothing was found.
    private(set) var searched = ""
    private(set) var searching = false
    private(set) var searchProblem: String?

    struct Asked: Equatable {
        let name: String
        let artistId: String?
    }

    @ObservationIgnored var lookUp: LookUp?
    @ObservationIgnored var findArtists: Search?
    @ObservationIgnored private var searchRun = 0
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
        searchRun += 1
        (opened, results, searched, searching, searchProblem) = (nil, nil, "", false, nil)
    }

    /// Ask YouTube Music which artists go by what's typed: the Discover list. The page
    /// goes back to its two lists if an artist was open.
    func search() {
        let wanted = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !wanted.isEmpty, let findArtists else { return }
        close()
        searchRun += 1
        let mine = searchRun
        (searching, searchProblem, searched) = (true, nil, wanted)
        Task {
            do {
                let found = try await findArtists(wanted)
                guard mine == searchRun else { return }
                results = found.map {
                    ListedArtist(name: $0.name, artistId: $0.artistId, thumbnail: $0.thumbnail)
                }
            } catch {
                guard mine == searchRun else { return }
                (results, searchProblem) = ([], error.localizedDescription)
            }
            searching = false
        }
    }

    /// The box was emptied: the Discover list is What's New's artists again.
    func clearSearch() {
        searchRun += 1
        (results, searched, searching, searchProblem) = (nil, "", false, nil)
    }

    /// Back to the two lists.
    func close() {
        run += 1
        (phase, info, allSongs, moreSongs, loadingSongs, songsProblem) = (.idle, nil, nil, false, false, nil)
        (album, openingAlbum, albumProblem, before, opened, last) = (nil, nil, nil, [], nil, nil)
    }

    /// An artist on the Discover list: by id when a search found them, else by name.
    func open(_ listed: ListedArtist) {
        show(Asked(name: listed.name, artistId: listed.artistId), remember: true)
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

    /// The artist looked at before this one; from the first, back to the two lists.
    func back() {
        guard let previous = before.popLast() else {
            close()
            return
        }
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
        (last, opened) = (asked, asked)
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
                    // As YouTube Music spells them, and by id from now on.
                    opened = Asked(name: found.name, artistId: found.artistId)
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
