import Foundation

/// A picture from the web, asked for again when its server drops or stalls the request
/// (2026-11-11: Cinemeta's picture server left most of Movie Finder's posters grey for minutes
/// after a reopen, answering some requests at once and holding others until they timed out, and
/// a card asked only once). An answer that the picture isn't there (404) is believed at once; a
/// dropped connection, a timeout or a server's own fault (5xx) is tried again after a short wait.
public enum PictureFetch {
    /// The waits before each try, in seconds: the first at once, then two more.
    public static let waits: [Double] = [0, 2, 6]

    /// The picture's bytes, or nil when they can't be had: gone, or no answer after every try,
    /// or the card asking has gone (the task was cancelled).
    public static func data(
        waits: [Double] = PictureFetch.waits,
        fetch: () async throws -> (Data, URLResponse)
    ) async -> Data? {
        for wait in waits {
            if wait > 0 {
                try? await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000))
            }
            if Task.isCancelled { return nil }
            do {
                let (data, answer) = try await fetch()
                guard let http = answer as? HTTPURLResponse else { return data }
                if (200..<300).contains(http.statusCode) { return data }
                // The server's own fault may pass; anything else says the picture isn't there.
                if http.statusCode >= 500 { continue }
                return nil
            } catch {
                if (error as? URLError)?.code == .cancelled || Task.isCancelled { return nil }
            }
        }
        return nil
    }
}
