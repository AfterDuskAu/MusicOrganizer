import Foundation

/// An error the engine answered with. `message` is plain English, written to be shown.
public struct RPCError: Error, LocalizedError, Equatable {
    public let code: Int
    public let message: String

    public static let locked = -32001
    public static let notFound = -32006
    public static let busy = -32007
    /// Not from the engine: the connection closed before an answer came.
    public static let closed = -1

    public init(code: Int, message: String) {
        self.code = code
        self.message = message
    }

    public var errorDescription: String? { message }
}

/// JSON-RPC 2.0, one JSON object per line (docs/ENGINE_API.md section 2), over a pair of
/// file handles: the engine's stdin and stdout in the app, plain pipes in tests.
public final class RPCConnection: @unchecked Sendable {
    public typealias Notification = @Sendable (_ method: String, _ params: [String: Any]) -> Void

    private let input: FileHandle  // we write requests here
    private let output: FileHandle  // and read answers here
    private let lock = NSLock()
    private var nextID = 0
    private var waiting: [Int: CheckedContinuation<Data, Error>] = [:]
    private var closed = false
    private var onNotification: Notification?
    private var onClose: (@Sendable () -> Void)?

    public init(writeTo input: FileHandle, readFrom output: FileHandle) {
        self.input = input
        self.output = output
    }

    /// Start reading. `onNotification` and `onClose` are called on a background thread.
    public func start(onNotification: Notification? = nil, onClose: (@Sendable () -> Void)? = nil) {
        self.onNotification = onNotification
        self.onClose = onClose
        let thread = Thread { [weak self] in self?.readLoop() }
        thread.name = "engine-reader"
        thread.start()
    }

    /// Send a request and wait for its result, as raw JSON.
    public func call(_ method: String, _ params: [String: Any] = [:]) async throws -> Data {
        try await withCheckedThrowingContinuation { continuation in
            lock.lock()
            if closed {
                lock.unlock()
                continuation.resume(throwing: Self.closedError)
                return
            }
            nextID += 1
            let id = nextID
            waiting[id] = continuation
            lock.unlock()
            let request: [String: Any] = [
                "jsonrpc": "2.0", "id": id, "method": method, "params": params,
            ]
            do {
                var line = try JSONSerialization.data(withJSONObject: request)
                line.append(0x0A)
                lock.lock()
                defer { lock.unlock() }
                try input.write(contentsOf: line)
            } catch {
                if let waiter = take(id) { waiter.resume(throwing: Self.closedError) }
            }
        }
    }

    /// Send a request and decode its result. Keys like `duration_s` become `durationS`.
    public func call<T: Decodable>(
        _ method: String, _ params: [String: Any] = [:], as type: T.Type
    ) async throws -> T {
        let data = try await call(method, params)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(type, from: data)
    }

    /// Send a request without waiting for its answer: for the last thing said as the app
    /// closes, when nothing will be left to hear the answer. The engine deals with its
    /// requests in the order they were sent, so one sent before `close()` is done
    /// before the engine stops.
    public func tell(_ method: String, _ params: [String: Any] = [:]) {
        lock.lock()
        defer { lock.unlock() }
        guard !closed else { return }
        nextID += 1
        let request: [String: Any] = [
            "jsonrpc": "2.0", "id": nextID, "method": method, "params": params,
        ]
        guard var line = try? JSONSerialization.data(withJSONObject: request) else { return }
        line.append(0x0A)
        try? input.write(contentsOf: line)
    }

    /// Close our end. The engine takes its stdin closing as "shut down cleanly".
    public func close() {
        try? input.close()
    }

    private static let closedError = RPCError(
        code: RPCError.closed, message: "The engine stopped before it answered.")

    private func take(_ id: Int) -> CheckedContinuation<Data, Error>? {
        lock.lock()
        defer { lock.unlock() }
        return waiting.removeValue(forKey: id)
    }

    private func readLoop() {
        var lines = LineBuffer()
        while true {
            let chunk = output.availableData
            if chunk.isEmpty { break }  // end of file: the engine has gone
            for line in lines.add(chunk) { handle(line) }
        }
        lock.lock()
        closed = true
        let left = waiting
        waiting = [:]
        lock.unlock()
        for waiter in left.values { waiter.resume(throwing: Self.closedError) }
        onClose?()
    }

    private static let theName = Data("YouTube".utf8)

    private func handle(_ line: Data) {
        guard let message = try? JSONSerialization.jsonObject(with: line) as? [String: Any]
        else { return }
        // Nearly every answer has nothing to reword: those aren't walked through at all.
        let named = line.range(of: Self.theName) != nil
        guard let id = message["id"] as? Int else {
            if let method = message["method"] as? String {
                let given = message["params"] ?? [:]
                let params = (named ? Wording.plain(answer: given) : given) as? [String: Any]
                onNotification?(method, params ?? [:])
            }
            return
        }
        guard let waiter = take(id) else { return }
        if let error = message["error"] as? [String: Any] {
            waiter.resume(throwing: RPCError(
                code: error["code"] as? Int ?? 0,
                message: Wording.plain(
                    error["message"] as? String ?? "The engine reported a problem.")))
            return
        }
        // The engine's sentences are put into the app's words on the way in.
        let given = message["result"] ?? NSNull()
        let result = named ? Wording.plain(answer: given) : given
        do {
            waiter.resume(returning: try JSONSerialization.data(
                withJSONObject: result, options: [.fragmentsAllowed]))
        } catch {
            waiter.resume(throwing: error)
        }
    }
}

/// What has arrived from the engine, cut into its lines (one answer is one line).
///
/// Only the piece that has just arrived is looked through for a line's end. Looking
/// through everything waiting each time, as was done until 2026-10-09, made one long
/// answer slow to read in a way that grew with its length squared: the list of every
/// song is a megabyte and arrives in dozens of pieces, and reading it took the app
/// about a second at every opening.
struct LineBuffer {
    private var waiting = Data()

    /// Add what has just arrived; the whole lines there now are given back, in order,
    /// without their line ends (an empty line is left out).
    mutating func add(_ chunk: Data) -> [Data] {
        var lines: [Data] = []
        // Nothing before this point holds a line's end: it was looked through already.
        var from = waiting.count
        waiting.append(chunk)
        while let end = waiting[from...].firstIndex(of: 0x0A) {
            let line = waiting.subdata(in: 0..<end)
            waiting.removeSubrange(0...end)
            from = 0
            if !line.isEmpty { lines.append(line) }
        }
        return lines
    }
}
