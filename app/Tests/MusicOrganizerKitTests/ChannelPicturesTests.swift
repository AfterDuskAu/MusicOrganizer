import XCTest

@testable import MusicOrganizerKit

final class ChannelPicturesTests: XCTestCase {
    func testAPictureFoundIsRemembered() {
        var pictures = ChannelPictures()
        XCTAssertNil(pictures.address(for: "UC-one"))
        pictures.remember("https://pictures.example/one=s800", for: "UC-one")
        XCTAssertEqual(pictures.address(for: "UC-one"), "https://pictures.example/one=s800")
    }

    func testANewerPictureTakesTheOldOnesPlace() {
        var pictures = ChannelPictures()
        pictures.remember("https://pictures.example/old", for: "UC-one")
        pictures.remember("https://pictures.example/new", for: "UC-one")
        XCTAssertEqual(pictures.address(for: "UC-one"), "https://pictures.example/new")
        XCTAssertEqual(pictures.count, 1)
    }

    func testNothingIsRememberedForNoAddressOrNoChannel() {
        var pictures = ChannelPictures()
        pictures.remember("", for: "UC-one")
        pictures.remember("https://pictures.example/one", for: "")
        XCTAssertEqual(pictures.count, 0)
    }

    func testTheOldestMakeWayPastTheMost() {
        var pictures = ChannelPictures()
        let start = Date(timeIntervalSince1970: 1_000_000)
        for number in 0..<(ChannelPictures.most + 5) {
            pictures.remember(
                "https://pictures.example/\(number)", for: "UC-\(number)",
                at: start.addingTimeInterval(Double(number)))
        }
        XCTAssertEqual(pictures.count, ChannelPictures.most)
        XCTAssertNil(pictures.address(for: "UC-0"))
        XCTAssertNil(pictures.address(for: "UC-4"))
        XCTAssertNotNil(pictures.address(for: "UC-5"))
        XCTAssertNotNil(pictures.address(for: "UC-\(ChannelPictures.most + 4)"))
    }

    func testTheyAreKeptAndReadBack() throws {
        let name = "channel-pictures-test-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: name))
        defer { defaults.removePersistentDomain(forName: name) }
        var pictures = ChannelPictures()
        pictures.remember("https://pictures.example/one", for: "UC-one")
        pictures.save(in: defaults)
        XCTAssertEqual(ChannelPictures.saved(in: defaults), pictures)
        XCTAssertEqual(ChannelPictures.saved(in: defaults).address(for: "UC-one"), "https://pictures.example/one")
    }
}
