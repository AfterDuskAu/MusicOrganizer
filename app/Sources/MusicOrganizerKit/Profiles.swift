import Foundation

/// One person's corner of the app on this computer: a name, and a library folder of
/// their own. Nothing online: a profile isn't an account anywhere, and has no password.
/// Each has its own music, downloads, playlists, favourites, sign-ins and settings.
public struct Profile: Codable, Identifiable, Equatable, Sendable {
    /// The first profile's id. The engine uses it too when nobody says which profile
    /// (the command line), so what was there before profiles belongs to the first one.
    public static let firstId = "default"

    public let id: String
    public var name: String
    /// The profile's library folder; nil until one has been chosen.
    public var libraryRoot: String?
    /// A library that hasn't been made yet: the engine makes it when it's first opened.
    public var isNew: Bool

    public init(id: String, name: String, libraryRoot: String? = nil, isNew: Bool = false) {
        self.id = id
        self.name = name
        self.libraryRoot = libraryRoot
        self.isNew = isNew
    }
}

/// The profiles on this computer, and which one is in use.
public struct ProfileList: Codable, Equatable, Sendable {
    public private(set) var profiles: [Profile]
    public private(set) var currentId: String

    public static let longestName = 40

    public enum Problem: Error, Equatable, LocalizedError {
        case noName, tooLong, taken(String), sameFolder(String), lastOne, inUse

        public var errorDescription: String? {
            switch self {
            case .noName: "A profile needs a name."
            case .tooLong: "That name is too long (the limit is \(ProfileList.longestName) characters)."
            case .taken(let name): "There's already a profile called \(name)."
            case .sameFolder(let name):
                "That folder is \(name)'s library. Each profile needs a folder of its own, "
                    + "or the music would be mixed."
            case .lastOne: "There has to be at least one profile."
            case .inUse: "Switch to another profile first, then remove this one."
            }
        }
    }

    /// The list before there were profiles: one, with the library that was in use.
    public init(firstNamed name: String, libraryRoot: String?) {
        let shown = name.trimmingCharacters(in: .whitespacesAndNewlines)
        profiles = [
            Profile(id: Profile.firstId, name: shown.isEmpty ? "Me" : shown, libraryRoot: libraryRoot)
        ]
        currentId = Profile.firstId
    }

    public var current: Profile {
        profiles.first { $0.id == currentId } ?? profiles[0]
    }

    public func profile(_ id: String) -> Profile? { profiles.first { $0.id == id } }

    /// A name as it's kept: trimmed, single spaces. Throws if it can't be one, or is
    /// another profile's already (whatever its capitals).
    public func checked(name: String, for id: String? = nil) throws -> String {
        let shown = name.split(whereSeparator: \.isWhitespace).joined(separator: " ")
        guard !shown.isEmpty else { throw Problem.noName }
        guard shown.count <= Self.longestName else { throw Problem.tooLong }
        if let other = profiles.first(where: {
            $0.id != id && $0.name.compare(shown, options: .caseInsensitive) == .orderedSame
        }) {
            throw Problem.taken(other.name)
        }
        return shown
    }

    /// Add a profile with a library folder of its own (made by the engine when it's
    /// first opened). `newId` makes the id; it's given in tests.
    @discardableResult
    public mutating func add(
        name: String, libraryRoot: String, newId: () -> String = ProfileList.newId
    ) throws -> Profile {
        let shown = try checked(name: name)
        let folder = Self.folderKey(libraryRoot)
        if let owner = profiles.first(where: { $0.libraryRoot.map(Self.folderKey) == folder }) {
            throw Problem.sameFolder(owner.name)
        }
        var id = newId()
        while profiles.contains(where: { $0.id == id }) { id = Self.newId() }
        let made = Profile(id: id, name: shown, libraryRoot: libraryRoot, isNew: true)
        profiles.append(made)
        return made
    }

    public mutating func rename(_ id: String, to name: String) throws {
        guard let index = profiles.firstIndex(where: { $0.id == id }) else { return }
        profiles[index].name = try checked(name: name, for: id)
    }

    /// Take a profile off the list. Its library folder and everything in it stay where
    /// they are: nothing is deleted. The one in use can't be removed, nor the last one.
    public mutating func remove(_ id: String) throws {
        guard profiles.count > 1 else { throw Problem.lastOne }
        guard id != currentId else { throw Problem.inUse }
        profiles.removeAll { $0.id == id }
    }

    public mutating func switchTo(_ id: String) {
        if profiles.contains(where: { $0.id == id }) { currentId = id }
    }

    /// The profile in use got a library folder (chosen by hand), or its new library
    /// has now been made.
    public mutating func setCurrentLibrary(_ root: String?, isNew: Bool = false) {
        guard let index = profiles.firstIndex(where: { $0.id == currentId }) else { return }
        profiles[index].libraryRoot = root
        profiles[index].isNew = isNew
    }

    public static func newId() -> String {
        "p_" + String(format: "%08x", UInt32.random(in: 0...UInt32.max))
    }

    /// Two ways of writing the same folder are the same folder.
    static func folderKey(_ path: String) -> String {
        URL(fileURLWithPath: path).standardizedFileURL.path.lowercased()
    }

    /// Where a new profile's library would go: beside the library in use, named after
    /// the profile ("Music Library" → "Music Library (Kids)"). Nil when there's no
    /// library yet to put it beside.
    public func suggestedRoot(for name: String) -> String? {
        guard let root = current.libraryRoot else { return nil }
        let allowed = name.split(whereSeparator: \.isWhitespace).joined(separator: " ")
            .filter { !"/:\\\0".contains($0) && !$0.isNewline }
        let shown = allowed.trimmingCharacters(in: CharacterSet(charactersIn: ". "))
        guard !shown.isEmpty else { return nil }
        let folder = URL(fileURLWithPath: root).standardizedFileURL
        return folder.deletingLastPathComponent()
            .appendingPathComponent("\(folder.lastPathComponent) (\(shown))").path
    }
}

/// A playlist one profile sent to another (Copy to Profile): its songs are copied into the
/// other profile's library the next time that profile is opened, by its own engine.
public struct PendingShare: Codable, Equatable, Sendable {
    /// The sending profile's library, and its songs' paths in it ("Music/…").
    public let sourceRoot: String
    public let paths: [String]
    public let playlistName: String
    /// Who sent it, to say so.
    public let fromName: String

    public init(sourceRoot: String, paths: [String], playlistName: String, fromName: String) {
        self.sourceRoot = sourceRoot
        self.paths = paths
        self.playlistName = playlistName
        self.fromName = fromName
    }

    /// The playlist's name in the receiving library: its own name, unless a playlist of
    /// that name is there already, then "Road Trip (from C)", then "(from C 2)" and on.
    public func nameHere(among names: [String]) -> String {
        let taken = Set(names.map { $0.lowercased() })
        if !taken.contains(playlistName.lowercased()) { return playlistName }
        var name = "\(playlistName) (from \(fromName))"
        var number = 2
        while taken.contains(name.lowercased()) {
            name = "\(playlistName) (from \(fromName) \(number))"
            number += 1
        }
        return name
    }
}

/// Which of the app's saved settings belong to a profile, so they can be put away when
/// another profile is switched to and brought back afterwards.
public enum ProfileSettings {
    /// The app's own keys for the profiles themselves, and what macOS keeps for the
    /// app (window places, its own switches): those stay as they are.
    public static func belongsToProfile(_ key: String) -> Bool {
        if key == "profiles" || key == "libraryRoot" || key == "pendingShares"
            || key.hasPrefix("profileSettings.")
        {
            return false
        }
        return !["NS", "Apple", "com.apple.", "WebKit"].contains { key.hasPrefix($0) }
    }

    /// What to put away for the profile being left: its own settings out of everything saved.
    public static func toKeep(_ all: [String: Any]) -> [String: Any] {
        all.filter { belongsToProfile($0.key) }
    }

    /// The changes that turn what's saved now into the profile being switched to:
    /// keys to set, and keys to take away (so they're back at the app's own defaults).
    public static func changes(
        from now: [String: Any], to wanted: [String: Any]
    ) -> (set: [String: Any], remove: [String]) {
        let kept = toKeep(wanted)
        let gone = now.keys.filter { belongsToProfile($0) && kept[$0] == nil }
        return (kept, gone.sorted())
    }
}
