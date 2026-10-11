import Foundation

/// The keys that work a film or a video while it plays (the owner, 2026-10-12): the
/// space bar pauses and plays, the up and down arrows raise and lower the sound, and
/// the left and right arrows go back and forward. Which key means what, and by how
/// much, is here so it can be tested without a window; the app decides when the keys
/// are the player's to take (a film open, or a video given the whole screen) and when
/// they belong to a list or to typing.
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
