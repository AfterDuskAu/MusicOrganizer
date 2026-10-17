import Foundation

/// The app's terms of use and its privacy policy, in plain words (the owner, 2026-10-12:
/// "a basic terms and privacy policy that what this app is used for doesn't fall back to
/// the developer, any irresponsible use is solely on the user"). Shown in Settings →
/// General, and kept word for word in `docs/TERMS.md` and `docs/PRIVACY.md` (a test
/// holds the two together). The privacy policy says what the app really does: a change to
/// what leaves this computer is a change to these words too.
public enum Legal {
    public struct Part: Equatable, Sendable {
        public let heading: String
        public let text: String
    }

    /// The day these words were last changed.
    public static let dated = "17 October 2026"

    public static let terms: [Part] = [
        Part(
            heading: "What this app is",
            text: "Bear Media is a tool for finding, playing and keeping media on your own "
                + "computer. It doesn't host, sell or supply any music, video or film itself: "
                + "what it plays and downloads comes from services and plugins that other "
                + "people run."),
        Part(
            heading: "What you do with it is yours to answer for",
            text: "You choose what to search for, play, download, keep and share, and which "
                + "plugins to add. It's up to you to make sure you're allowed to: music, videos "
                + "and films belong to their owners, the law on copying them differs from "
                + "country to country, and the services the app reaches have terms of their "
                + "own, which you agree to keep. Any use of the app that breaks a law, someone's "
                + "rights or a service's terms is solely the responsibility of the person "
                + "using it, not of the app's developer."),
        Part(
            heading: "Plugins",
            text: "A plugin is made and run by someone else. The developer doesn't make, check "
                + "or answer for what a plugin lists, links to or plays. You decide which ones "
                + "to add, and you answer for what you use them for."),
        Part(
            heading: "No promises",
            text: "The app is given as it is, with no warranty of any kind. The developer "
                + "isn't liable for anything lost or damaged through using it (files, records, "
                + "time or money), or for what anyone does with it. Keep your own copies of "
                + "anything that matters to you."),
        Part(
            heading: "If you don't agree",
            text: "Don't use the app. Using it means you accept these terms."),
    ]

    public static let privacy: [Part] = [
        Part(
            heading: "What the developer collects",
            text: "Nothing. There's no account to make, nothing is measured or tracked, and "
                + "the app sends nothing to its developer."),
        Part(
            heading: "What stays on this computer",
            text: "Your library, playlists, favourites, play counts, listening time, what "
                + "you've watched, your settings and your sign-ins are kept on this computer, "
                + "in your library's folder and the app's own settings folder. Deleting those "
                + "deletes them."),
        Part(
            heading: "What leaves this computer",
            text: "Only what it takes to do what you ask. A search, a song or video played "
                + "or downloaded, lyrics and covers looked up, and a plugin's lists and films "
                + "are each asked of the service or plugin that has them, which sees what was "
                + "asked for and your internet address, as it would in a web browser. A film "
                + "played or downloaded from a torrent is fetched from, and sent on to, other "
                + "people who have it, who can see your internet address, as with any torrent "
                + "program. If you connect Spotify or Last.fm, the app reads your playlists "
                + "and lists from them, and changes nothing there. If you import from "
                + "Stremio, the app reads what Stremio on this Mac remembers of what you've "
                + "watched (on this Mac only; Stremio isn't changed, and its sign-in isn't "
                + "read), and asks your plugins for each title's details."),
        Part(
            heading: "Sync with your devices",
            text: "Off until you switch it on. Then a device you've paired with a code can "
                + "copy your library over your home network, and nothing is sent to the "
                + "internet for it. A video app you've paired also sends what you've watched "
                + "on it (your Watchlist, what you've watched and where you got to), which is "
                + "added to your profile here, and can copy its downloaded films into your "
                + "Movies folder; all of it over your home network only."),
        Part(
            heading: "Other people's services",
            text: "Every service and plugin the app reaches has a privacy policy of its own, "
                + "and what it does with a request is up to it, not to this app."),
    ]

    /// The words as a Markdown page, for the file kept in `docs/`.
    public static func page(_ title: String, _ parts: [Part]) -> String {
        var lines = ["# \(title)", "", "Bear Media. Last changed \(dated).", ""]
        for part in parts {
            lines += ["## \(part.heading)", "", part.text, ""]
        }
        return lines.joined(separator: "\n")
    }
}
