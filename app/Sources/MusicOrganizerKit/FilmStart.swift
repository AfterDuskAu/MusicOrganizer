import Foundation

/// A film or episode that won't start (the owner, 2026-11-11, on a video app: an episode
/// "keeps loading as "finding film" for minutes on end without moving forward", and then "a
/// film couldn't be opened"). The Mac had the same shape: a torrent nobody was sharing sat on
/// "Finding the film…" while the engine waited two minutes for its details, with nothing to
/// say whether anyone had been found, and ended on "This film couldn't be opened." with
/// nothing to try but the ✕.
///
/// Now, while a film hasn't started, the player says how long it has been looking and whether
/// anyone sharing it has been found; once it's plain that it won't start, it's let go and the
/// owner is told so, with another way to play it (`another`) and a way back. Only before the
/// film has started: once it's playing, a part that hasn't come yet is waited for for as long
/// as the film is open.
///
/// The times: a torrent that's shared comes in seconds (Blender's Sintel's details in 2.6 s
/// over the DHT alone, measured on 2026-11-11); one nobody shares had none after four minutes.
public enum FilmStart {
    /// A torrent whose details haven't come, with nobody sharing it found: let go after this.
    public static let nobodyFor: TimeInterval = 45
    /// Its details still haven't come, though someone sharing it was found.
    public static let detailsFor: TimeInterval = 90
    /// Its details came, but nothing of the film has, and nobody sharing it is there.
    public static let nothingFor: TimeInterval = 90
    /// A web address that hasn't started the film.
    public static let addressFor: TimeInterval = 90

    public enum Verdict: Equatable, Sendable {
        /// Still worth waiting for.
        case waiting
        /// It won't start: what to tell the owner.
        case stuck(String)
    }

    public static let nobody = "Nobody is sharing this one right now, so it can't start."
    public static let noDetails = "This one didn't start: those sharing it didn't send it."
    public static let noAnswer = "This one didn't start: its address didn't answer."

    /// Nobody answered: who's listed, if anyone is, so it squares with Count's figures (the
    /// owner, 2026-11-12, on a video app: Count said 12/7, and Play found nobody). `Sharers`.
    public static func nobody(_ status: TorrentStatus) -> String {
        let seeds = status.swarmSeeds ?? 0
        let everyone = status.swarmPeers ?? 0
        guard let listed = Sharers.listed(seeds: seeds, others: everyone - seeds) else { return nobody }
        let are = everyone == 1 ? "is" : "are"
        return "\(listed) \(are) listed for this one, but none of them answered, so it can't start."
    }

    /// A torrent's film that hasn't started, `waited` seconds after it was opened. `status` is
    /// nil until the engine has first said how the torrent is doing.
    public static func verdict(torrent status: TorrentStatus?, waited: TimeInterval) -> Verdict {
        guard let status else { return waited >= detailsFor ? .stuck(noDetails) : .waiting }
        switch status.state {
        case "finding":
            // Only those that really answered count (`Sharers`): some answer for any torrent.
            if status.heard == 0, waited >= nobodyFor { return .stuck(nobody(status)) }
            return waited >= detailsFor ? .stuck(status.heard == 0 ? nobody(status) : noDetails) : .waiting
        case "checking", "complete":
            // Looking through what's here of a film played before: it says how far, and opens
            // when it's done. All here: it opens.
            return .waiting
        default:
            // Nothing coming now, and nobody to send it or nothing of it come at all.
            let stalled = status.bytesPerSecond == 0 && (status.heard == 0 || status.progress == 0)
            return stalled && waited >= nothingFor ? .stuck(nobody) : .waiting
        }
    }

    /// A web address's film that hasn't started, `waited` seconds after it was opened.
    public static func verdict(address waited: TimeInterval) -> Verdict {
        waited >= addressFor ? .stuck(noAnswer) : .waiting
    }

    /// The player's line under its spinner while a torrent's film hasn't started: "Finding the
    /// film… 0:32 · nobody sharing it found yet", "Finding the film… 0:32 · 12/7 listed, none
    /// answered yet", "Finding the film… 0:05 · 3 sharing it answered"; once its details are
    /// here, the torrent's own line ("2 seeds · 4 peers · …").
    public static func line(_ status: TorrentStatus?, waited: TimeInterval) -> String {
        let clock = Self.clock(waited)
        guard let status else { return "Finding the film… \(clock)" }
        guard status.state == "finding" else { return status.line }
        let found: String
        if status.heard > 0 {
            found = "\(status.heard) sharing it answered"
        } else if (status.swarmPeers ?? 0) > 0, let swarm = status.swarm {
            found = "\(swarm) listed, none answered yet"
        } else {
            found = "nobody sharing it found yet"
        }
        return "Finding the film… \(clock) · \(found)"
    }

    /// "0:32", "1:05".
    public static func clock(_ seconds: TimeInterval) -> String {
        let whole = max(Int(seconds), 0)
        return "\(whole / 60):" + String(format: "%02d", whole % 60)
    }

    /// Another way to play a film or episode after one that didn't start: by the owner's rule
    /// for the next episode first (the same quality from the same plugin, then that quality from
    /// the next plugin in their list: `NextEpisode.way`), then any other way, in the order its
    /// page lists them. Ways already tried (`tried`, by `MediaStream.key`) are passed over, and
    /// only what the film player plays is taken (a web address or a torrent).
    public static func another(
        in answer: StreamsAnswer, tried: Set<String>, like played: NextEpisode.Played?
    ) -> StreamsAnswer.Way? {
        let open = answer.ways.filter { playsInFilmPlayer($0.stream) && !tried.contains($0.stream.key) }
        if let played {
            let alike = open.filter { $0.stream.quality == played.quality }
            if let found = alike.first(where: { $0.addonId == played.addonId }) ?? alike.first { return found }
        }
        return open.first
    }

    /// The way to play as the button that offers it says it: its quality and its plugin's
    /// name, "720p · <plugin>".
    public static func describe(_ way: StreamsAnswer.Way) -> String {
        [way.stream.qualityLabel, way.addon.split(separator: "\n").first.map(String.init)]
            .compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
    }

    /// A way the film player opens: a web address, or a torrent by its id (a video by its id
    /// plays in the app's own player instead, and a `url_special` needs a player of its own).
    static func playsInFilmPlayer(_ stream: MediaStream) -> Bool {
        (stream.kind == "url" && stream.url.flatMap(URL.init(string:)) != nil)
            || (stream.kind == "torrent" && stream.infoHash != nil)
    }
}

extension MediaStream {
    /// What a way to play is told apart by when another is looked for (`FilmStart.another`): a
    /// torrent's id and the file in it, or its address.
    public var key: String {
        if kind == "torrent", let infoHash { return "torrent:\(infoHash.lowercased()):\(fileIndex.map(String.init) ?? "")" }
        return url ?? videoId ?? ""
    }
}
