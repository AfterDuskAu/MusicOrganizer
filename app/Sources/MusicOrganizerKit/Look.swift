import Foundation

/// How the app is dressed: Settings → App Layout (owner, 2026-10-03). The pages and what's
/// on them are the same in every look; only the colours and the headings' type change.
public enum AppLook: String, CaseIterable, Sendable {
    /// macOS's own colours and type, light or dark as the Mac is set: the app as it was.
    case native
    /// Warm near-black with cream text, an amber highlight and serif headings. Always dark.
    case warm

    /// Where the choice is saved. It's the computer's, not a profile's: a look is put on
    /// when the app opens, and switching profiles doesn't reopen it.
    public static let key = "appLook"

    /// What's saved, read back: anything unknown (or nothing) is the native look.
    public init(saved: String?) {
        self = saved.flatMap(AppLook.init(rawValue:)) ?? .native
    }

    /// The owner's names for them.
    public var title: String {
        switch self {
        case .native: "Apple Native Build"
        case .warm: "Warm Look"
        }
    }

    public var about: String {
        switch self {
        case .native:
            "The Mac's own colours and type, light or dark as your Mac is set."
        case .warm:
            "Warm near-black with cream text, an amber highlight and serif headings; the top "
                + "of the window takes its colour from the song that's playing. Always dark."
        }
    }
}

/// A colour as red, green and blue, each from 0 to 1.
public struct RGB: Equatable, Sendable {
    public let red: Double
    public let green: Double
    public let blue: Double

    public init(red: Double, green: Double, blue: Double) {
        self.red = min(max(red, 0), 1)
        self.green = min(max(green, 0), 1)
        self.blue = min(max(blue, 0), 1)
    }

    /// From the six digits a colour is usually written with: `RGB(0x1F1915)`.
    public init(_ hex: UInt32) {
        red = Double((hex >> 16) & 0xFF) / 255
        green = Double((hex >> 8) & 0xFF) / 255
        blue = Double(hex & 0xFF) / 255
    }

    /// How bright it looks, from 0 (black) to 1 (white): the measure contrast is worked
    /// out from (WCAG 2).
    public var luminance: Double {
        func linear(_ part: Double) -> Double {
            part <= 0.03928 ? part / 12.92 : pow((part + 0.055) / 1.055, 2.4)
        }
        return 0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue)
    }

    /// How well one colour reads on the other, from 1 (the same) to 21 (black on white).
    /// Text wants 4.5 or more; 7 or more is comfortable.
    public func contrast(with other: RGB) -> Double {
        let (lighter, darker) = (max(luminance, other.luminance), min(luminance, other.luminance))
        return (lighter + 0.05) / (darker + 0.05)
    }

    /// What this colour, taken from a part of a cover, gives the glow at the top of the
    /// Warm Look's window. The colour keeps its own hue, a little stronger, and is
    /// brought to a brightness that shows on a dark page without glaring (a dark red
    /// cover glows red; a white one doesn't light the window up). A part with next to
    /// no colour of its own (black, white, grey) gives `plain` instead.
    public func glow(plain: RGB) -> RGB {
        let (most, least) = (max(red, green, blue), min(red, green, blue))
        guard most >= 0.1, (most - least) / most >= 0.15 else { return plain }
        let spread = most - least
        var hue: Double  // in sixths of the colour wheel
        if most == red {
            hue = (green - blue) / spread
        } else if most == green {
            hue = 2 + (blue - red) / spread
        } else {
            hue = 4 + (red - green) / spread
        }
        if hue < 0 { hue += 6 }
        let strength = min(1, spread / most * 1.2)
        let brightness = min(max(most, 0.5), 0.72)
        // Back from hue, strength and brightness to red, green and blue.
        let part = Int(hue) % 6
        let along = hue - Double(Int(hue))
        let low = brightness * (1 - strength)
        let falling = brightness * (1 - strength * along)
        let rising = brightness * (1 - strength * (1 - along))
        switch part {
        case 0: return RGB(red: brightness, green: rising, blue: low)
        case 1: return RGB(red: falling, green: brightness, blue: low)
        case 2: return RGB(red: low, green: brightness, blue: rising)
        case 3: return RGB(red: low, green: falling, blue: brightness)
        case 4: return RGB(red: rising, green: low, blue: brightness)
        default: return RGB(red: brightness, green: low, blue: falling)
        }
    }
}

/// The Warm Look's colours, all in one place. A test checks the text can be read on every
/// surface, so a colour changed here can't quietly make a page hard to read.
public enum WarmPalette {
    /// Behind the pages.
    public static let page = RGB(0x1F1915)
    /// Behind the sidebar: a shade darker than the pages.
    public static let sidebar = RGB(0x18130F)
    /// The player bar along the bottom: a shade lighter, so it reads as its own strip.
    public static let bar = RGB(0x2A211B)
    /// A panel beside or on a page (the lyrics beside the library, a list's footer).
    public static let panel = RGB(0x272019)
    /// Words: cream, not white.
    public static let text = RGB(0xF2E8D9)
    /// The glow at the top of the window while nothing plays, and what a cover with no
    /// colour of its own gives it: the amber of a lamp.
    public static let glow = RGB(0x9A6336)

    public static let surfaces = [page, sidebar, bar, panel]

    /// macOS's own highlight colours, by the number macOS saves them under. The Warm
    /// Look uses orange: the nearest of them to amber.
    public static let highlight = 1
}
