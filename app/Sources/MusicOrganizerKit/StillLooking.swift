import Foundation

/// What the player says while a song from the music service is slow to be found
/// (2026-11-17, the owner: a busy Mac "shouldn't limit bear media on the mac from
/// opening songs on music finder"). On the owner's Mac, busy with other work on its
/// spinning disk, the engine can take half a minute or more to ask where a song plays
/// from. It used to give up after twenty seconds and say so in red; now it waits, and a
/// spinner alone looked like nothing was happening, so after a few seconds this line
/// says it's still coming. A real failure (the service refusing, no network) is still
/// said as before, in its place.
public enum StillLooking {
    /// How long the spinner turns alone before the line shows.
    public static let after: Duration = .seconds(8)

    /// The line under the player, in the owner's words (never the service's name).
    public static func note(for title: String) -> String {
        "Still finding “\(title)”: it's taking longer than usual, which happens when the "
            + "Mac is busy. It'll play as soon as it's found."
    }
}
