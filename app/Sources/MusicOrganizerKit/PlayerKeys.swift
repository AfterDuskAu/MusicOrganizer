import Foundation

/// The keys that work a film or a video while it plays (the owner, 2026-10-12): the
/// space bar pauses and plays, the up and down arrows raise and lower the sound, and
/// the left and right arrows go back and forward. Which key means what, and by how
/// much, is here so it can be tested without a window; whose an arrow is at any moment
/// (the player's, Home's cards', a list's, a field's) is `ArrowKeys`, below.
public enum PlayerKey: Equatable, Sendable {
    case playPause
    case louder
    case quieter
    case back
    case forward

    /// A step of the sound: a twentieth of all there is.
    public static let volumeStep = 0.05
    /// How far an arrow goes through a film or a video, in seconds.
    public static let skip = 10.0

    /// The key pressed, by the number macOS gives it (`NSEvent.keyCode`): the space bar
    /// and the four arrows. Any other key isn't the player's.
    public init?(keyCode: UInt16) {
        switch keyCode {
        case 49: self = .playPause
        case 126: self = .louder
        case 125: self = .quieter
        case 123: self = .back
        case 124: self = .forward
        default: return nil
        }
    }

    /// The sound after this key, where `full` is as loud as it goes (1 for the music
    /// player, 100 for the film player). Never under nothing or over full.
    public func volume(from now: Double, full: Double) -> Double {
        switch self {
        case .louder: min(full, now + Self.volumeStep * full)
        case .quieter: max(0, now - Self.volumeStep * full)
        default: now
        }
    }

    /// Where to go to after this key: ten seconds back or on, never before the start
    /// and never onto the very end (which would finish it). Nil when the key doesn't
    /// move through it, or its length isn't known yet.
    public func place(from now: Double, length: Double) -> Double? {
        guard length > 0 else { return nil }
        switch self {
        case .back: return max(0, now - Self.skip)
        case .forward: return min(max(length - 1, 0), now + Self.skip)
        default: return nil
        }
    }
}

/// Whose the arrow keys are at any moment (the owner, the same evening: "Arrow keys
/// function everywhere, on every video, song playing (skips, adjusts volume), on a
/// homepage (allows for the user to switch through whats on the window)"). Until then
/// they were the player's only while a film was open or a picture had the whole screen.
public enum ArrowKeys {
    public enum Owner: Equatable, Sendable {
        /// Left to whatever has the keyboard: a field being typed in, a sheet, a list.
        case theirs
        /// The film, the video or the song: left and right go back and on, up and down
        /// move the sound.
        case player
        /// The page of cards that's showing (Home): the highlight moves from card to card.
        case cards
    }

    /// Where the keyboard is when the key is pressed.
    public struct Place: Equatable, Sendable {
        /// A field is being typed in.
        public var typing = false
        /// The key was pressed in a sheet or a panel (a file panel's arrows are its own).
        public var elsewhere = false
        /// A film is open, a video or the visualizer has the whole screen, or the
        /// player's own page is showing: the picture is what's being looked at.
        public var watching = false
        /// A song or a video is in the music player (playing, or paused part way).
        public var somethingPlaying = false
        /// A page of cards is the page showing (Home).
        public var cardsShowing = false
        /// A list on the page itself has the keyboard (a song list, a list of files).
        public var pageListHasKeyboard = false
        /// The sidebar has the keyboard, and its last up or down arrow was its own: the
        /// owner is going through the sidebar's rows by key.
        public var arrowingSidebar = false

        public init() {}
    }

    /// - A field, a sheet or a panel keeps its keys.
    /// - While the picture is what's looked at, all four are the player's.
    /// - A list on the page keeps up and down (they move through its rows); left and
    ///   right, which a list has no use for, go back and on in what's playing.
    /// - On a page of cards the arrows move the highlight, unless the sidebar is being
    ///   gone through by key: then up and down carry on down the sidebar, so Home isn't
    ///   a row the keys can't get past, and left or right steps into the cards.
    /// - Anywhere else they're the player's whenever something is playing. With nothing
    ///   playing they're left alone, and move through the sidebar as macOS has it.
    public static func owner(of key: PlayerKey, at place: Place) -> Owner {
        guard key != .playPause, !place.typing, !place.elsewhere else { return .theirs }
        if place.watching { return .player }
        let upOrDown = key == .louder || key == .quieter
        if place.pageListHasKeyboard {
            return upOrDown || !place.somethingPlaying ? .theirs : .player
        }
        if place.cardsShowing {
            return place.arrowingSidebar && upOrDown ? .theirs : .cards
        }
        return place.somethingPlaying ? .player : .theirs
    }
}

/// The highlight on a page of cards (Home's rows), moved by the arrows: which row, and
/// which of the cards it's showing. Worked out here so it can be tested without a page.
public struct CardFocus: Equatable, Sendable {
    public var row: Int
    public var column: Int

    public init(row: Int, column: Int) {
        self.row = row
        self.column = column
    }

    /// A row of the page, as the keys see it.
    public enum Row: Equatable, Sendable {
        /// It's showing this many cards.
        case cards(Int)
        /// It has been on the screen and has no cards (a line of words instead).
        case empty
        /// It's further down than the page has been scrolled: what's in it isn't known.
        case unseen
    }

    public enum Step: Equatable, Sendable {
        /// The highlight goes here.
        case to(CardFocus)
        /// The row is asked for its next cards, or the ones before: the highlight was on
        /// the last (or first) card it's showing.
        case page(row: Int, forward: Bool)
        /// The page is scrolled to this row, which hasn't been seen yet; the highlight
        /// goes to it when its cards are there.
        case reveal(row: Int)
        case stay
    }

    /// What an arrow does. With no highlight yet (or one whose row has lost its cards),
    /// any arrow puts it on the first card there is. Left and right go along the row
    /// and on to its next cards; up and down go to the nearest row that has cards,
    /// keeping as near the same place across as that row allows.
    public static func step(from now: CardFocus?, key: PlayerKey, rows: [Row]) -> Step {
        func count(_ row: Int) -> Int {
            if rows.indices.contains(row), case .cards(let count) = rows[row] { return count }
            return 0
        }
        guard key != .playPause else { return .stay }
        guard let now, count(now.row) > 0 else {
            for (row, state) in rows.enumerated() {
                if case .cards(let count) = state, count > 0 { return .to(CardFocus(row: row, column: 0)) }
                if state == .unseen { return .reveal(row: row) }
            }
            return .stay
        }
        let column = min(now.column, count(now.row) - 1)
        switch key {
        case .forward:
            return column + 1 < count(now.row)
                ? .to(CardFocus(row: now.row, column: column + 1)) : .page(row: now.row, forward: true)
        case .back:
            return column > 0
                ? .to(CardFocus(row: now.row, column: column - 1)) : .page(row: now.row, forward: false)
        case .quieter, .louder:
            let others = key == .quieter
                ? Array(rows.indices.dropFirst(now.row + 1)) : Array(rows.indices.prefix(now.row).reversed())
            for row in others {
                if count(row) > 0 { return .to(CardFocus(row: row, column: min(column, count(row) - 1))) }
                if rows[row] == .unseen { return .reveal(row: row) }
            }
            return .stay
        case .playPause:
            return .stay
        }
    }
}
