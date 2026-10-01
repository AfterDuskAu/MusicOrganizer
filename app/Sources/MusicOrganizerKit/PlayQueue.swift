import Foundation

/// What is playing and what comes next. Pure logic, no audio.
public struct PlayQueue: Sendable {
    public enum Repeat: String, Sendable, CaseIterable {
        case off, all, one
    }

    public private(set) var tracks: [Track] = []
    /// The order tracks are played in: positions into `tracks`.
    public private(set) var order: [Int] = []
    public private(set) var position: Int?
    public private(set) var shuffle = false
    public var repeatMode: Repeat = .off

    public init() {}

    public var current: Track? { position.map { tracks[order[$0]] } }

    public var upNext: [Track] {
        guard let position else { return [] }
        return order[(position + 1)...].map { tracks[$0] }
    }

    /// Start playing `tracks` at `start`. With shuffle on, that track plays first and
    /// the rest follow in a random order.
    public mutating func play(
        _ tracks: [Track], startAt start: Int = 0,
        using generator: inout some RandomNumberGenerator
    ) {
        self.tracks = tracks
        guard !tracks.isEmpty else {
            order = []
            position = nil
            return
        }
        let start = min(max(start, 0), tracks.count - 1)
        if shuffle {
            order = [start] + tracks.indices.filter { $0 != start }.shuffled(using: &generator)
            position = 0
        } else {
            order = Array(tracks.indices)
            position = start
        }
    }

    public mutating func play(_ tracks: [Track], startAt start: Int = 0) {
        var generator = SystemRandomNumberGenerator()
        play(tracks, startAt: start, using: &generator)
    }

    /// Turn shuffle on or off without changing the song that's playing.
    public mutating func setShuffle(
        _ on: Bool, using generator: inout some RandomNumberGenerator
    ) {
        shuffle = on
        guard let position else { return }
        let playing = order[position]
        if on {
            order = [playing] + tracks.indices.filter { $0 != playing }.shuffled(using: &generator)
            self.position = 0
        } else {
            order = Array(tracks.indices)
            self.position = playing
        }
    }

    public mutating func setShuffle(_ on: Bool) {
        var generator = SystemRandomNumberGenerator()
        setShuffle(on, using: &generator)
    }

    /// Move to the next song and return it. `finished` means the current song played to
    /// its end (so "repeat one" plays it again); a press of Next always moves on.
    /// Nil at the end of the queue with repeat off: playback stops there.
    public mutating func advance(finished: Bool = false) -> Track? {
        guard let position else { return nil }
        if finished && repeatMode == .one { return current }
        if position + 1 < order.count {
            self.position = position + 1
        } else if repeatMode == .off {
            return nil
        } else {
            self.position = 0
        }
        return current
    }

    /// Move to the song before, staying on the first one.
    public mutating func back() -> Track? {
        guard let position else { return nil }
        if position > 0 {
            self.position = position - 1
        } else if repeatMode == .all {
            self.position = order.count - 1
        }
        return current
    }

    /// Jump to a song in "up next" (0 is the very next one).
    public mutating func jump(toUpNext offset: Int) -> Track? {
        guard let position, offset >= 0, position + 1 + offset < order.count else { return nil }
        self.position = position + 1 + offset
        return current
    }
}
