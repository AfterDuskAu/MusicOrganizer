import Foundation

/// The engine (`musicorg serve`) running as a child of the app. The app owns its
/// lifetime: closing the connection, or the app quitting or crashing, closes the engine's
/// stdin, and the engine shuts down cleanly by itself.
public final class EngineProcess: @unchecked Sendable {
    public let connection: RPCConnection
    private let process: Process
    private let errors = ErrorTail()

    /// `executable` is the `musicorg` command, e.g. `<repo>/.venv/bin/musicorg`.
    /// `environment` is added to this app's own for the engine: which profile it's
    /// running for (`MUSICORG_PROFILE`), so each person's sign-ins stay their own.
    public init(executable: URL, environment: [String: String] = [:]) throws {
        signal(SIGPIPE, SIG_IGN)  // writing to an engine that has gone is an error, not a crash
        let stdin = Pipe(), stdout = Pipe(), stderr = Pipe()
        process = Process()
        process.executableURL = executable
        process.arguments = ["serve"]
        if !environment.isEmpty {
            process.environment = ProcessInfo.processInfo.environment.merging(environment) { $1 }
        }
        process.standardInput = stdin
        process.standardOutput = stdout
        process.standardError = stderr
        let errors = self.errors
        stderr.fileHandleForReading.readabilityHandler = { handle in
            errors.add(handle.availableData)
        }
        connection = RPCConnection(
            writeTo: stdin.fileHandleForWriting, readFrom: stdout.fileHandleForReading)
        try process.run()
    }

    /// The last things the engine wrote to its log stream, for an error screen.
    public var lastErrors: String { errors.text }

    public func stop() {
        connection.close()
    }

    /// Where the engine is, tried in order: the MUSICORG_ENGINE environment variable,
    /// a `.venv/bin/musicorg` in a folder above the app (the app built inside the
    /// project), then the path the build script wrote into the app.
    public static func locate(
        environment: [String: String] = ProcessInfo.processInfo.environment,
        appLocation: URL = Bundle.main.bundleURL,
        recorded: String? = Bundle.main.object(forInfoDictionaryKey: "MusicOrgEngine") as? String,
        exists: (String) -> Bool = { FileManager.default.isExecutableFile(atPath: $0) }
    ) -> URL? {
        if let given = environment["MUSICORG_ENGINE"], exists(given) {
            return URL(fileURLWithPath: given)
        }
        var folder = appLocation.standardizedFileURL
        while folder.path != "/" && !folder.path.isEmpty {
            folder = folder.deletingLastPathComponent()
            let candidate = folder.appendingPathComponent(".venv/bin/musicorg")
            if exists(candidate.path) { return candidate }
        }
        if let recorded, exists(recorded) { return URL(fileURLWithPath: recorded) }
        return nil
    }
}

private final class ErrorTail: @unchecked Sendable {
    private let lock = NSLock()
    private var data = Data()

    func add(_ more: Data) {
        lock.lock()
        defer { lock.unlock() }
        data.append(more)
        if data.count > 4000 { data = data.suffix(4000) }
    }

    var text: String {
        lock.lock()
        defer { lock.unlock() }
        return String(decoding: data, as: UTF8.self)
    }
}
