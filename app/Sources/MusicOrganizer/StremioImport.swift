import Foundation
import MusicOrganizerKit
import Observation
import SQLite3
import SwiftUI

/// Settings → Profile → Export and Import → From Stremio (the owner, 2026-10-16: "yes build
/// the import from stremio. For Account D. Later we will do the same for Account C"). What
/// the Stremio app on this Mac remembers (its Library, each episode and movie watched, and
/// where each was left) is read, shown, and on the owner's click added to the profile in
/// use. Stremio's storage is only read: a copy of it is made in a folder of the app's own,
/// read, and deleted; only its Library's two records are taken from it (never its
/// sign-in). Each title's details are asked of the owner's plugins, for its episodes and
/// genres.
@MainActor
@Observable
final class StremioImporter {
    enum Stage: Equatable {
        case idle, reading, ready
        case fetching(done: Int, total: Int)
        case done(String)
        case failed(String)
    }

    private(set) var stage: Stage = .idle
    private(set) var items: [StremioLibrary.Item] = []

    var isWorking: Bool {
        switch stage {
        case .reading, .fetching: true
        default: false
        }
    }

    /// What was found, in a few lines, before anything is taken.
    var lines: [String] {
        let taken = items.filter(StremioLibrary.isTaken)
        let library = taken.filter { !$0.removed }
        func count(_ type: String) -> Int { library.filter { kind(of: $0) == type }.count }
        let movies = taken.filter {
            $0.type == "movie" && (($0.state.flaggedWatched ?? 0) > 0 || ($0.state.timesWatched ?? 0) > 0)
        }
        let series = taken.filter { $0.type != "movie" && $0.state.watched != nil }
        let partWay = taken.filter { ($0.state.timeOffset ?? 0) > 0 }
        var said = [
            "In its Library: \(library.count) (\(count("series")) series, \(count("movie")) movies, "
                + "\(count("anime")) anime). They go on your Watchlist.",
            "Watched: \(movies.count) movies, and episodes of \(series.count) series.",
            "Left part way: \(partWay.count), to carry on with where you left off.",
        ]
        let leftOut = items.count - taken.count
        if leftOut > 0 {
            said.append(
                "Left out: \(leftOut) from other plugins and channels. Nothing from a plugin for "
                    + "adults is ever kept here.")
        }
        return said
    }

    private func kind(of item: StremioLibrary.Item) -> String {
        item.type == "anime" || item.id.hasPrefix("kitsu:") ? "anime" : item.type
    }

    /// Read what Stremio on this Mac remembers.
    func read() {
        guard !isWorking else { return }
        stage = .reading
        Task.detached(priority: .userInitiated) {
            let found = Self.readStorage()
            await MainActor.run {
                switch found {
                case .success(let items) where items.isEmpty:
                    self.stage = .failed("Stremio on this Mac has nothing watched or in its Library yet.")
                case .success(let items):
                    (self.items, self.stage) = (items, .ready)
                case .failure(let problem):
                    self.stage = .failed(problem.message)
                }
            }
        }
    }

    /// Ask the plugins for each title's details, and put what was found in the profile in use.
    func run(_ model: AppModel) {
        guard stage == .ready else { return }
        let taken = items.filter(StremioLibrary.isTaken)
        stage = .fetching(done: 0, total: taken.count)
        Task {
            var details: [String: MediaDetails] = [:]
            var waiting = taken[...]
            var done = 0
            // Six at a time: each is one question to a plugin (not the gate to YouTube).
            await withTaskGroup(of: (String, MediaDetails?).self) { group in
                func next() {
                    guard let item = waiting.popFirst() else { return }
                    let type = item.type == "movie" ? "movie" : "series"
                    group.addTask { @MainActor in
                        let found = try? await model.ask(
                            "addon.details", ["type": type, "id": item.id, "adult": false], as: MediaDetails.self)
                        return (item.id, found)
                    }
                }
                for _ in 0..<6 { next() }
                for await (id, found) in group {
                    if let found { details[id] = found }
                    done += 1
                    stage = .fetching(done: done, total: taken.count)
                    next()
                }
            }
            let plan = StremioLibrary.plan(items, details: details)
            let added = model.takeStremio(plan, details: details)
            var said = "Imported into “\(model.profiles.current.name)”: \(added) put on your Watchlist, "
                + "\(plan.episodesWatched.formatted()) episodes of \(plan.seriesWithEpisodes) series and "
                + "\(plan.moviesWatched) movies marked watched, and \(plan.history.count) titles in what "
                + "you've watched (\(plan.partWay) to carry on with)."
            if plan.episodesUnread > 0 {
                said += " The episodes of \(plan.episodesUnread) series couldn't be read from your "
                    + "plugins just now; importing again later adds them."
            }
            stage = .done(said)
        }
    }

    func reset() {
        (stage, items) = (.idle, [])
    }

    // MARK: Stremio's storage

    struct Problem: Error {
        let message: String
    }

    /// Where Stremio for Mac keeps its storage: a web page's local storage, in WebKit's
    /// folder for the app. Any app of Stremio's that keeps its Library there is looked in.
    nonisolated static func storageFiles() -> [URL] {
        let fm = FileManager.default
        let webKit = fm.homeDirectoryForCurrentUser.appendingPathComponent("Library/WebKit")
        let apps = (try? fm.contentsOfDirectory(atPath: webKit.path))?.filter { $0.lowercased().contains("stremio") } ?? []
        var found: [URL] = []
        for app in apps {
            let base = webKit.appendingPathComponent(app).appendingPathComponent("WebsiteData")
            guard let walker = fm.enumerator(at: base, includingPropertiesForKeys: nil) else { continue }
            for case let file as URL in walker where file.lastPathComponent == "localstorage.sqlite3" {
                found.append(file)
            }
        }
        return found
    }

    /// Stremio's Library, read from a copy of its storage (so the app's own file is never
    /// opened, even to read, while Stremio may be writing it).
    nonisolated static func readStorage() -> Result<[StremioLibrary.Item], Problem> {
        let files = storageFiles()
        guard !files.isEmpty else {
            return .failure(Problem(message: "Stremio for Mac isn't on this Mac, or hasn't been opened and signed in yet."))
        }
        var records: [Data] = []
        let fm = FileManager.default
        for file in files {
            let copy = fm.temporaryDirectory.appendingPathComponent("stremio-read-\(UUID().uuidString)")
            defer { try? fm.removeItem(at: copy) }
            do {
                try fm.createDirectory(at: copy, withIntermediateDirectories: true)
                for suffix in ["", "-wal", "-shm"] {
                    let from = URL(fileURLWithPath: file.path + suffix)
                    if fm.fileExists(atPath: from.path) {
                        try fm.copyItem(at: from, to: copy.appendingPathComponent("localstorage.sqlite3" + suffix))
                    }
                }
            } catch {
                return .failure(Problem(message: "Stremio's storage couldn't be read: \(error.localizedDescription)"))
            }
            records += libraryRecords(in: copy.appendingPathComponent("localstorage.sqlite3"))
        }
        return .success(StremioLibrary.items(fromRecords: records))
    }

    /// The two records of the Library ("library", "library_recent"), as UTF-8 JSON. WebKit
    /// keeps local storage's values in UTF-16.
    nonisolated static func libraryRecords(in database: URL) -> [Data] {
        var db: OpaquePointer?
        guard sqlite3_open_v2(database.path, &db, SQLITE_OPEN_READONLY, nil) == SQLITE_OK else {
            sqlite3_close(db)
            return []
        }
        defer { sqlite3_close(db) }
        var statement: OpaquePointer?
        let query = "SELECT value FROM ItemTable WHERE key IN ('library', 'library_recent')"
        guard sqlite3_prepare_v2(db, query, -1, &statement, nil) == SQLITE_OK else { return [] }
        defer { sqlite3_finalize(statement) }
        var found: [Data] = []
        while sqlite3_step(statement) == SQLITE_ROW {
            guard let bytes = sqlite3_column_blob(statement, 0) else { continue }
            let raw = Data(bytes: bytes, count: Int(sqlite3_column_bytes(statement, 0)))
            if let text = String(data: raw, encoding: .utf16LittleEndian) { found.append(Data(text.utf8)) }
        }
        return found
    }
}

/// The From Stremio part of Settings → Profile → Export and Import.
struct StremioImportSection: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let importer = model.stremio
        Section("From Stremio") {
            switch importer.stage {
            case .idle, .failed:
                LabeledContent("What you've watched in Stremio on this Mac") {
                    Button("Read Stremio") { importer.read() }
                }
                if case .failed(let problem) = importer.stage {
                    Text(problem).foregroundStyle(.orange)
                }
            case .reading:
                HStack {
                    ProgressView().controlSize(.small)
                    Text("Reading Stremio…")
                }
            case .ready:
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(importer.lines, id: \.self) { Text($0) }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                HStack {
                    Button("Not Now") { importer.reset() }
                    Spacer()
                    Button("Import Into “\(model.profiles.current.name)”") { importer.run(model) }
                        .buttonStyle(.borderedProminent)
                }
            case .fetching(let done, let total):
                VStack(alignment: .leading, spacing: 6) {
                    Text("Reading each title's details from your plugins…")
                    ProgressView(value: Double(done), total: Double(max(total, 1)))
                    Text("\(done.formatted()) of \(total.formatted())").font(.callout).foregroundStyle(.secondary)
                }
            case .done(let said):
                Text(said)
                Button("Read Again") { importer.reset() }
            }
            SideNote(
                "Stremio's Library goes on your Watchlist; the episodes and movies it has as watched are "
                    + "marked watched; and what you left part way carries on where you left off. Stremio "
                    + "is only read, never changed. An import adds to this profile: nothing here is taken "
                    + "away, and what you've watched here more lately is kept. For another person's "
                    + "Stremio, sign in to theirs in Stremio, switch to their profile here, and import again.")
        }
    }
}
