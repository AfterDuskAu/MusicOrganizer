import Foundation

/// Measures how responsive the app is: a stall is the main thread not answering for
/// 100 ms or more. Off unless the app is started with MUSICORG_STALLS=1, when every
/// stall is printed to the error stream with how long it lasted.
public final class StallWatch: @unchecked Sendable {
    public static let shared = StallWatch()
    private let queue = DispatchQueue(label: "stall-watch", qos: .userInteractive)
    private var running = false

    public func startIfAsked(environment: [String: String] = ProcessInfo.processInfo.environment) {
        guard environment["MUSICORG_STALLS"] == "1", !running else { return }
        running = true
        queue.async { [self] in loop() }
    }

    private func loop() {
        while true {
            let asked = DispatchTime.now()
            let answered = DispatchSemaphore(value: 0)
            DispatchQueue.main.async { answered.signal() }
            answered.wait()
            let waited = Double(DispatchTime.now().uptimeNanoseconds - asked.uptimeNanoseconds) / 1e6
            if waited >= 100 {
                FileHandle.standardError.write(Data(String(format: "STALL %.0f ms\n", waited).utf8))
            }
            Thread.sleep(forTimeInterval: 0.02)
        }
    }
}
