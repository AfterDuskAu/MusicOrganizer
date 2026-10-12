import Foundation

/// Channels' pictures, found afresh. A channels add-on's list gives each channel's
/// picture by an address that stops working when the channel changes its picture, and
/// the list the app starts with ends in 2023: two thirds of its pictures had gone by
/// 2026-10-12 (63 of the first 96). So when a channel's picture can't be had, the app
/// asks for the channel as it is now and remembers the picture's address here: kept by
/// the app on this Mac, the newest two thousand, and nothing of it is in the library.
public struct ChannelPictures: Equatable, Sendable {
    public static let key = "channelPictures"
    public static let most = 2000

    struct Found: Codable, Equatable, Sendable {
        let address: String
        let at: Double
    }

    private(set) var found: [String: Found] = [:]

    public init() {}

    public var count: Int { found.count }

    /// The picture remembered for this channel, if one was found.
    public func address(for channelId: String) -> String? {
        found[channelId]?.address
    }

    /// Remember this channel's picture as it is now. The oldest make way past `most`.
    public mutating func remember(_ address: String, for channelId: String, at: Date = Date()) {
        guard !address.isEmpty, !channelId.isEmpty else { return }
        found[channelId] = Found(address: address, at: at.timeIntervalSince1970)
        guard found.count > Self.most else { return }
        let oldest = found.sorted { $0.value.at < $1.value.at }.prefix(found.count - Self.most)
        for (id, _) in oldest { found[id] = nil }
    }

    public static func saved(in defaults: UserDefaults = .standard) -> ChannelPictures {
        var pictures = ChannelPictures()
        if let data = defaults.data(forKey: key),
            let found = try? JSONDecoder().decode([String: Found].self, from: data)
        {
            pictures.found = found
        }
        return pictures
    }

    public func save(in defaults: UserDefaults = .standard) {
        if let data = try? JSONEncoder().encode(found) { defaults.set(data, forKey: Self.key) }
    }
}
