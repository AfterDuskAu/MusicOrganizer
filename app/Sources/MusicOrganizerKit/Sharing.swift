import Foundation
import dnssd

/// Sharing the library with a phone player on the home network (2026-10-04).
/// What the engine says of it (`sharing.status`), for Settings to show. The engine does
/// the sharing; the app switches it on, shows the pairing code, and announces it.
public struct SharingStatus: Decodable, Equatable, Sendable {
    /// A paired device. It says only what kind it is ("iPhone"): nothing that names it
    /// or whose it is. Its key is never given to the app.
    public struct Device: Decodable, Equatable, Identifiable, Sendable {
        public let id: String
        public let device: String
        public let pairedAt: String?
        public let lastSynced: String?

        public init(id: String, device: String, pairedAt: String? = nil, lastSynced: String? = nil) {
            self.id = id
            self.device = device
            self.pairedAt = pairedAt
            self.lastSynced = lastSynced
        }

        /// "Paired 4 Oct 2026. Last synced 4 Oct 2026 at 12:31." or "Not synced yet."
        public func about(
            calendar: Calendar = .current, locale: Locale = .current
        ) -> String {
            let day = Date.FormatStyle(
                date: .abbreviated, time: .omitted, locale: locale, calendar: calendar,
                timeZone: calendar.timeZone)
            let moment = Date.FormatStyle(
                date: .abbreviated, time: .shortened, locale: locale, calendar: calendar,
                timeZone: calendar.timeZone)
            var words: [String] = []
            if let paired = SharingStatus.date(pairedAt) { words.append("Paired \(paired.formatted(day)).") }
            if let synced = SharingStatus.date(lastSynced) {
                words.append("Last synced \(synced.formatted(moment)).")
            } else {
                words.append("Not synced yet.")
            }
            return words.joined(separator: " ")
        }
    }

    public let on: Bool
    /// The port the engine is listening on, while sharing is on.
    public let port: Int?
    /// This Mac's address on the home network, or nil when it isn't on one.
    public let address: String?
    /// The library's name, as a phone shows it.
    public let name: String
    /// What a share is announced as on the network (Bonjour).
    public let service: String
    public let devices: [Device]
    /// Whether a pairing code is showing and still works.
    public let pairing: Bool
    public let pairingSecondsLeft: Int

    public init(
        on: Bool, port: Int? = nil, address: String? = nil, name: String = "",
        service: String = "", devices: [Device] = [], pairing: Bool = false,
        pairingSecondsLeft: Int = 0
    ) {
        self.on = on
        self.port = port
        self.address = address
        self.name = name
        self.service = service
        self.devices = devices
        self.pairing = pairing
        self.pairingSecondsLeft = pairingSecondsLeft
    }

    /// What to type into a phone that can't find this Mac by itself: the address and the
    /// port, with a colon between. Nil while sharing is off or there's no home network.
    public var whereToFind: String? {
        guard on, let address, !address.isEmpty, let port else { return nil }
        return "\(address):\(port)"
    }

    /// The devices paired since `before` was read: the ones a code was just used by.
    public func newDevices(since before: SharingStatus?) -> [Device] {
        let known = Set((before?.devices ?? []).map(\.id))
        return devices.filter { !known.contains($0.id) }
    }

    static func date(_ text: String?) -> Date? {
        guard let text else { return nil }
        return ISO8601DateFormatter().date(from: text)
    }
}

/// A pairing code the engine made (`sharing.pair`), for the screen. It works once, and
/// for `seconds` from when it was made.
public struct PairingCode: Decodable, Equatable, Sendable {
    public let code: String
    public let seconds: Int

    public init(code: String, seconds: Int) {
        self.code = code
        self.seconds = seconds
    }

    /// In two halves, which is easier to read across to a phone.
    public var spaced: String {
        guard code.count == 6 else { return code }
        return "\(code.prefix(3)) \(code.suffix(3))"
    }

    /// "4:59", "0:07": how long the code still works, with `left` seconds to go.
    public static func clock(_ left: Int) -> String {
        let left = max(0, left)
        return "\(left / 60):" + String(format: "%02d", left % 60)
    }
}

/// Announces a shared library on the local network (Bonjour), so that a phone player
/// finds this Mac by itself. The announcement is under this Mac's own name, goes no
/// further than the local network, and ends when `stop()` is called or when the app
/// does: macOS drops an announcement as soon as the program that made it has gone.
public final class HomeAnnouncer {
    private var service: DNSServiceRef?
    /// The port being announced, or nil when nothing is.
    public private(set) var port: Int?

    public init() {}

    deinit { stop() }

    /// Announce `type` (like "_homemusicsync._tcp") on `port`. False if macOS wouldn't.
    @discardableResult
    public func start(type: String, port: Int) -> Bool {
        if service != nil, self.port == port { return true }
        stop()
        guard let number = UInt16(exactly: port), number > 0 else { return false }
        var made: DNSServiceRef?
        // No name: this Mac's own. "local.": the local network only, never a wider domain.
        let problem = DNSServiceRegister(
            &made, 0, 0, nil, type, "local.", nil, number.bigEndian, 0, nil, nil, nil)
        guard problem == DNSServiceErrorType(kDNSServiceErr_NoError), let made else { return false }
        service = made
        self.port = port
        return true
    }

    public func stop() {
        if let service { DNSServiceRefDeallocate(service) }
        service = nil
        port = nil
    }
}
