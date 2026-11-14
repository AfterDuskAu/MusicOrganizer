import Foundation
import MusicOrganizerKit

/// The profile's records written off the main thread, a moment after they change (2026-11-14,
/// as the iPad has it since 2026-11-13). Every Mark as Watched wrote the whole of what's been
/// watched out as JSON where it stood — for the owner's six thousand that is ten to twenty
/// milliseconds of the main thread for one click, and a series' card wrote the progress of
/// every series out again on top of it. Now the record is kept here and written a quarter of a
/// second later, on another thread, each kind of record only once however many times it changed
/// meanwhile.
///
/// **Nothing is ever lost by the wait.** What's waiting is written at once:
///
/// - before the app quits (`AppModel.start` watches macOS's own notice);
/// - before a profile is left, so what it has watched goes into its own settings and never
///   into the next profile's (`AppModel.switchProfile`);
/// - before anything reads the settings themselves rather than the records in memory: a
///   profile's export (`SettingsCarrier.pack`), an import putting one back;
/// - before a paired video app is given this profile's watching (`AppModel.giveWatching`).
///
/// Reading is unchanged: the records in memory are the app's own, and the settings are only how
/// they come back next time.
@MainActor
final class Records {
    /// What's waiting to be written, by its key: the newest value only.
    private var waiting: [String: @Sendable () -> Void] = [:]
    private var soon: Task<Void, Never>?
    /// How long a change waits for others to join it.
    static let after: Duration = .milliseconds(250)

    /// Write this record soon. `key` is the record's own key, so a second change to the same
    /// record before it's written takes the place of the first.
    func save(_ key: String, _ write: @escaping @Sendable () -> Void) {
        waiting[key] = write
        guard soon == nil else { return }
        soon = Task { [weak self] in
            try? await Task.sleep(for: Self.after)
            guard let self, !Task.isCancelled else { return }
            self.soon = nil
            self.write()
        }
    }

    /// Everything waiting, written now, here and not on another thread: a flush is the app
    /// about to quit, leave a profile or read the settings themselves, and work handed to
    /// another thread might not be done by then. Safe to call at any time, and nothing at all
    /// when nothing waits.
    func flush() {
        soon?.cancel()
        soon = nil
        write(here: true)
    }

    private func write(here: Bool = false) {
        guard !waiting.isEmpty else { return }
        let writes = Array(waiting.values)
        waiting = [:]
        guard !here else {
            for write in writes { write() }
            return
        }
        // Off the main thread: UserDefaults may be written from any thread, and making the
        // JSON of what's been watched (thousands of ids) is most of the work.
        Task.detached(priority: .utility) {
            for write in writes { write() }
        }
    }
}
