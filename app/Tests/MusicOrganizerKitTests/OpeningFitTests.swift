import CoreGraphics
import XCTest

@testable import MusicOrganizerKit

/// The loading film fitted to the window (the owner, 2026-11-16: "it should sync to the size
/// of the window"): the wide film or the upright one by the window's shape, filling it, the
/// name never cut and the film never stretched.
final class OpeningFitTests: XCTestCase {
    /// Windows the app can have: its smallest, its first size, the whole 27-inch screen, the
    /// owner's own (the left half of the screen), and a few other shapes.
    private let smallest = CGSize(width: 880, height: 540)
    private let firstSize = CGSize(width: 1180, height: 760)
    private let wholeScreen = CGSize(width: 2560, height: 1415)
    private let halfScreen = CGSize(width: 1269, height: 1399)
    private let tallNarrow = CGSize(width: 880, height: 1300)
    private let square = CGSize(width: 1000, height: 1000)
    private let wideFlat = CGSize(width: 2560, height: 540)

    private var windows: [CGSize] { [smallest, firstSize, wholeScreen, halfScreen, tallNarrow, square, wideFlat] }

    /// The name's box on the screen, for a film placed at `frame`.
    private func nameBox(_ shape: OpeningShape, at frame: CGRect) -> CGRect {
        let width = frame.width * shape.name.width
        let height = frame.height * shape.name.height
        return CGRect(x: frame.midX - width / 2, y: frame.midY - height / 2, width: width, height: height)
    }

    func testAWideWindowPlaysTheWideFilm() {
        XCTAssertEqual(OpeningFit.shape(for: smallest), .wide)
        XCTAssertEqual(OpeningFit.shape(for: firstSize), .wide)
        XCTAssertEqual(OpeningFit.shape(for: wholeScreen), .wide)
        XCTAssertEqual(OpeningFit.shape(for: wideFlat), .wide)
    }

    func testANarrowerWindowPlaysTheUprightFilm() {
        XCTAssertEqual(OpeningFit.shape(for: halfScreen), .upright)
        XCTAssertEqual(OpeningFit.shape(for: tallNarrow), .upright)
        XCTAssertEqual(OpeningFit.shape(for: square), .upright)
        // Just too narrow for the wide film to fill with the name whole: about 1.45 to 1.
        XCTAssertEqual(OpeningFit.shape(for: CGSize(width: 1400, height: 1000)), .upright)
        XCTAssertEqual(OpeningFit.shape(for: CGSize(width: 1500, height: 1000)), .wide)
    }

    func testTheChosenFilmFillsEveryWindowWithTheNameWhole() {
        for window in windows {
            let shape = OpeningFit.shape(for: window)
            let frame = OpeningFit.frame(of: shape, in: window)
            XCTAssertTrue(OpeningFit.fills(shape, window), "\(shape) doesn't fill \(window)")
            XCTAssertLessThanOrEqual(frame.minX, 0.5)
            XCTAssertLessThanOrEqual(frame.minY, 0.5)
            XCTAssertGreaterThanOrEqual(frame.maxX, window.width - 0.5)
            XCTAssertGreaterThanOrEqual(frame.maxY, window.height - 0.5)
            let name = nameBox(shape, at: frame)
            let bounds = CGRect(origin: .zero, size: window)
            XCTAssertTrue(bounds.contains(name), "the name is cut in \(window)")
            XCTAssertLessThanOrEqual(name.width, window.width * OpeningFit.mostOfWidth + 0.5)
        }
    }

    func testTheFilmIsNeverStretched() {
        for window in windows {
            for shape in OpeningShape.allCases {
                let frame = OpeningFit.frame(of: shape, in: window)
                XCTAssertEqual(
                    frame.width / frame.height, shape.size.width / shape.size.height, accuracy: 0.0001)
            }
        }
    }

    func testTheFilmIsInTheMiddleOfTheWindow() {
        for window in windows {
            for shape in OpeningShape.allCases {
                let frame = OpeningFit.frame(of: shape, in: window)
                XCTAssertEqual(frame.midX, window.width / 2, accuracy: 0.001)
                XCTAssertEqual(frame.midY, window.height / 2, accuracy: 0.001)
            }
        }
    }

    /// The wide film, already playing, in a window made narrow (a resize while it plays): it
    /// can't fill the window without cutting the name, so it's as wide as the name allows,
    /// with sky above and below it, and the name whole.
    func testTheWideFilmInANarrowWindowKeepsTheNameWhole() {
        let frame = OpeningFit.frame(of: .wide, in: halfScreen)
        XCTAssertFalse(OpeningFit.fills(.wide, halfScreen))
        XCTAssertGreaterThan(frame.minY, 0)
        XCTAssertLessThanOrEqual(frame.minX, 0.5)  // still the window's whole width
        let name = nameBox(.wide, at: frame)
        XCTAssertEqual(name.width, halfScreen.width * OpeningFit.mostOfWidth, accuracy: 0.5)
        XCTAssertTrue(CGRect(origin: .zero, size: halfScreen).contains(name))
    }

    /// The upright film, already playing, in a window made wide: it still fills it, cropped
    /// above and below, with the name whole.
    func testTheUprightFilmInAWideWindowStillFillsIt() {
        XCTAssertTrue(OpeningFit.fills(.upright, wholeScreen))
        XCTAssertTrue(OpeningFit.fills(.upright, smallest))
        let frame = OpeningFit.frame(of: .upright, in: wholeScreen)
        XCTAssertTrue(CGRect(origin: .zero, size: wholeScreen).contains(nameBox(.upright, at: frame)))
    }

    /// Before the window has a size, there's nowhere to put it.
    func testAnEmptyWindowHasNoFrame() {
        XCTAssertEqual(OpeningFit.frame(of: .wide, in: .zero), .zero)
    }

    /// The film for the window as it is today, the size of a screen, is the same as before: the
    /// wide film filling it.
    func testTheWholeScreenIsAsBefore() {
        let window = CGSize(width: 2560, height: 1440)
        XCTAssertEqual(OpeningFit.shape(for: window), .wide)
        let frame = OpeningFit.frame(of: .wide, in: window)
        XCTAssertEqual(frame.minX, 0, accuracy: 0.001)
        XCTAssertEqual(frame.minY, 0, accuracy: 0.001)
        XCTAssertEqual(frame.width, 2560, accuracy: 0.001)
        XCTAssertEqual(frame.height, 1440, accuracy: 0.001)
    }
}
