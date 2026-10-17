import AppKit
import QuartzCore
import SwiftUI

/// The loading screen's bear as Core Animation layers (the owner, 2026-10-17: "the loading
/// screen glitches quite a bit"). Drawn by SwiftUI's Canvas, every picture of it was made
/// on the app's main thread, which is at its busiest while the bear is up (the library
/// being read, the first page made under it), and each picture was six soft-edged layers
/// the size of the screen: it stuttered. Here each part is drawn once
/// (`BearDrawing.draw(_:in:)`) and moved by the window server, which goes on smoothly
/// however busy the app is. The movements are the Canvas bear's (`BearDrawing.Pose`).
struct BearLayerScene: NSViewRepresentable {
    func makeNSView(context: Context) -> BearLayerView { BearLayerView() }
    func updateNSView(_ view: BearLayerView, context: Context) {}
}

final class BearLayerView: NSView {
    private let sky = CAGradientLayer()
    private let glow = CAGradientLayer()
    private let stars = BearDrawing.stars.map { _ in CALayer() }
    /// The bear's square, and inside it what moves together: the head (lifting as the
    /// bear breathes, swaying from the neck), the eyes, and the ticket with the paws.
    private let bear = CALayer()
    private let lift = CALayer()
    private let sway = CALayer()
    private let eyes = CALayer()
    private let held = CALayer()
    private let tile = CALayer()
    private var parts: [BearDrawing.Part: CALayer] = [:]
    /// The size of the bear's square, and the screen's scale, its parts were drawn for.
    private var drawn: (side: CGFloat, scale: CGFloat) = (0, 0)
    private var redrawing: DispatchWorkItem?

    override init(frame: NSRect) {
        super.init(frame: frame)
        layer = CALayer()
        wantsLayer = true
        build()
    }

    required init?(coder: NSCoder) { nil }

    override func layout() {
        super.layout()
        arrange()
    }

    override func viewDidChangeBackingProperties() {
        super.viewDidChangeBackingProperties()
        arrange()
    }

    // MARK: the layers

    private func build() {
        guard let root = layer else { return }
        root.masksToBounds = true  // the bear's body goes on below the window
        sky.colors = [Self.colour(BearDrawing.skyHex.top), Self.colour(BearDrawing.skyHex.bottom)]
        sky.startPoint = CGPoint(x: 0.5, y: 1)
        sky.endPoint = CGPoint(x: 0.5, y: 0)
        glow.type = .radial
        glow.colors = [Self.colour(BearDrawing.glowHex, 0.22), Self.colour(BearDrawing.glowHex, 0)]
        glow.startPoint = CGPoint(x: 0.5, y: 0.5)
        glow.endPoint = CGPoint(x: 1, y: 1)
        root.addSublayer(sky)
        root.addSublayer(glow)
        for star in stars {
            star.backgroundColor = CGColor(gray: 1, alpha: 0.85)
            root.addSublayer(star)
        }
        for part in BearDrawing.Part.allCases {
            let layer = CALayer()
            layer.contentsGravity = .resize
            parts[part] = layer
        }
        func add(_ which: [BearDrawing.Part], to container: CALayer) {
            for part in which { container.addSublayer(parts[part]!) }
        }
        root.addSublayer(bear)
        add([.body], to: bear)
        bear.addSublayer(lift)
        lift.addSublayer(sway)
        add([.leftEar, .rightEar, .head], to: sway)
        sway.addSublayer(eyes)
        add([.eyes, .lids], to: eyes)
        bear.addSublayer(held)
        add([.ticket], to: held)
        held.addSublayer(tile)
        add([.sheen], to: tile)
        add([.disc, .leftPaw, .rightPaw], to: held)
        tile.masksToBounds = true
        tile.cornerCurve = .continuous
        parts[.lids]!.opacity = 0
        guard !Self.still else { return }
        for (index, star) in stars.enumerated() {
            let period = 2.3 + Double(index % 5) * 0.7
            let phase = (Double(index) * 1.9).truncatingRemainder(dividingBy: period)
            star.add(Self.wave("opacity", 0.85 * 0.1, 0.85, half: period / 2, phase: phase), forKey: "twinkle")
        }
        glow.add(Self.wave("opacity", 0.82, 1, half: 3), forKey: "glow")
    }

    /// Everything put where it goes for the window's size; the bear's parts drawn again
    /// when its size has settled at another.
    private func arrange() {
        let size = bounds.size
        guard size.width > 0, size.height > 0 else { return }
        let side = BearDrawing.side(in: size, share: 0.8)
        let scale = window?.backingScaleFactor ?? NSScreen.main?.backingScaleFactor ?? 2
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        defer { CATransaction.commit() }
        sky.frame = bounds
        // The glow is behind the bear's head; the stars are spread over the whole sky.
        let reach = min(size.width, size.height) * 0.5
        let head = CGPoint(x: size.width / 2, y: side * (256 - 110) / 256)
        glow.frame = CGRect(x: head.x - reach, y: head.y - reach, width: reach * 2, height: reach * 2)
        let dot = min(size.width, size.height) / 256
        for (star, place) in zip(stars, BearDrawing.stars) {
            let radius = place.size * dot * 1.2
            star.frame = CGRect(
                x: place.x * size.width - radius, y: (1 - place.y) * size.height - radius,
                width: radius * 2, height: radius * 2)
            star.cornerRadius = radius
        }
        // The bear rises from the bottom edge, its square drawn at one size and shown at
        // another while the window's size changes.
        bear.position = CGPoint(x: size.width / 2, y: side / 2)
        if drawn.side == 0 || drawn.scale != scale {
            draw(side: side, scale: scale)
        } else if drawn.side != side {
            bear.transform = CATransform3DMakeScale(side / drawn.side, side / drawn.side, 1)
            redrawing?.cancel()
            let again = DispatchWorkItem { [weak self] in self?.arrangeAfresh() }
            redrawing = again
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.25, execute: again)
        }
    }

    private func arrangeAfresh() {
        drawn = (0, 0)
        arrange()
    }

    /// Each part drawn for a square this size, placed, and set moving.
    private func draw(side: CGFloat, scale: CGFloat) {
        drawn = (side, scale)
        let unit = side / 256
        bear.transform = CATransform3DIdentity
        bear.bounds = CGRect(x: 0, y: 0, width: side, height: side)
        // A point of the owner's picture (y down from its top) in the bear's square (y up).
        func place(_ area: CGRect) -> CGRect {
            CGRect(x: area.minX * unit, y: (256 - area.maxY) * unit, width: area.width * unit, height: area.height * unit)
        }
        // Where in its own area a part turns or grows from.
        func pivot(_ part: BearDrawing.Part, _ point: CGPoint) -> CGPoint {
            let area = BearDrawing.area(of: part)
            return CGPoint(x: (point.x - area.minX) / area.width, y: (area.maxY - point.y) / area.height)
        }
        let pivots: [BearDrawing.Part: CGPoint] = [
            .body: pivot(.body, CGPoint(x: 128, y: 256)),  // breathes from where it sits
            .leftEar: pivot(.leftEar, CGPoint(x: 93, y: 76)),  // flicks from where it meets the head
            .eyes: pivot(.eyes, CGPoint(x: 128, y: 96)),  // blinks to the middle of the eyes
            .disc: pivot(.disc, BearDrawing.discCentre),  // spins about its upright line
        ]
        for container in [lift, held] { container.frame = bear.bounds }
        sway.anchorPoint = CGPoint(x: 0.5, y: (256 - 152) / 256)  // the neck
        sway.frame = bear.bounds
        eyes.frame = place(BearDrawing.area(of: .eyes))
        let tileArea = BearDrawing.ticketTile
        tile.frame = place(tileArea)
        tile.cornerRadius = 7.5 * unit
        for (part, layer) in parts {
            layer.anchorPoint = pivots[part] ?? CGPoint(x: 0.5, y: 0.5)
            let area = BearDrawing.area(of: part)
            switch part {
            case .eyes, .lids:
                layer.frame = eyes.bounds
            case .sheen:
                // Inside the tile, which cuts it off at its edges.
                layer.frame = CGRect(
                    x: (area.minX - tileArea.minX) * unit, y: 0, width: area.width * unit, height: tile.bounds.height)
            default:
                layer.frame = place(area)
            }
            layer.contentsScale = scale
            layer.contents = Self.picture(of: part, unit: unit, scale: scale)
        }
        move(unit: unit)
    }

    // MARK: the movements

    private static let still = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion

    private func move(unit: CGFloat) {
        for layer in [lift, sway, eyes, held] + Array(parts.values) { layer.removeAllAnimations() }
        guard !Self.still else { return }
        let degree = Double.pi / 180
        parts[.body]!.add(Self.wave("transform.scale.y", 1 - 0.014, 1 + 0.014, half: 1.8), forKey: "breath")
        parts[.body]!.add(Self.wave("transform.scale.x", 1 - 0.006, 1 + 0.006, half: 1.8), forKey: "breadth")
        lift.add(Self.wave("transform.translation.y", -1.2 * unit, 1.2 * unit, half: 1.8), forKey: "lift")
        sway.add(Self.wave("transform.rotation.z", -2.2 * degree, 2.2 * degree, half: 2.65), forKey: "sway")
        held.add(Self.wave("transform.translation.y", 1.7 * unit, -1.7 * unit, half: 1.8, phase: 0.46), forKey: "bob")
        eyes.add(Self.wave("transform.translation.x", -1.1 * unit, 1.1 * unit, half: 3.85), forKey: "lookAcross")
        eyes.add(Self.wave("transform.translation.y", -0.5 * unit, 0.5 * unit, half: 4.55), forKey: "lookUp")

        // Blinks at uneven moments, once a double, over twelve seconds: the eyes squeeze
        // to a slit and the shut lids show for a moment.
        var squeeze: [(Double, Double)] = [(0, 1)]
        var open: [(Double, Double)] = [(0, 1)]
        var shut: [(Double, Double)] = [(0, 0)]
        for start in [0.6, 3.9, 7.4, 7.75, 10.6] {
            squeeze += [(start, 1), (start + 0.1, 0.15), (start + 0.2, 1)]
            open += [(start + 0.06, 1), (start + 0.08, 0), (start + 0.12, 0), (start + 0.14, 1)]
            shut += [(start + 0.06, 0), (start + 0.08, 1), (start + 0.12, 1), (start + 0.14, 0)]
        }
        parts[.eyes]!.add(Self.loop("transform.scale.y", squeeze + [(12, 1)], every: 12), forKey: "blink")
        parts[.eyes]!.add(Self.loop("opacity", open + [(12, 1)], every: 12), forKey: "open")
        parts[.lids]!.add(Self.loop("opacity", shut + [(12, 0)], every: 12), forKey: "shut")

        // The left ear flicks once in a while.
        var flick: [(Double, Double)] = [(0, 0), (5, 0)]
        for step in 1...8 {
            let moment = 0.4 * Double(step) / 8
            flick.append((5 + moment, sin(.pi * moment / 0.4) * 9 * degree))
        }
        parts[.leftEar]!.add(Self.loop("transform.rotation.z", flick + [(8.5, 0)], every: 8.5), forKey: "flick")

        // The play button: a whole turn, eased, like a coin spun on a table (its face
        // narrows to its edge and opens again, the arrow always pointing on), then a rest.
        var spin: [(Double, Double)] = []
        for step in 0...36 {
            let turning = Double(step) / 36
            let eased = turning < 0.5 ? 2 * turning * turning : 1 - pow(-2 * turning + 2, 2) / 2
            spin.append((turning * 0.6 * 2.4, max(0.06, abs(cos(eased * 2 * .pi)))))
        }
        parts[.disc]!.add(Self.loop("transform.scale.x", spin + [(2.4, 1)], every: 2.4), forKey: "spin")

        // A sheen passes over the ticket every few seconds, from beyond one side of the
        // tile to beyond the other.
        let tileArea = BearDrawing.ticketTile
        let across = { (shine: Double) -> Double in (-44 + (tileArea.width + 80) * shine) * unit }
        let sheen = CAKeyframeAnimation(keyPath: "position.x")
        sheen.values = [across(0), across(1), across(1)]
        sheen.keyTimes = [0, 0.45, 1]
        sheen.duration = 3.7
        sheen.repeatCount = .infinity
        parts[.sheen]!.position.x = across(0)
        parts[.sheen]!.add(sheen, forKey: "sheen")
    }

    /// To and fro between two values, easing at each end.
    private static func wave(
        _ key: String, _ from: Double, _ to: Double, half: Double, phase: Double = 0
    ) -> CABasicAnimation {
        let wave = CABasicAnimation(keyPath: key)
        (wave.fromValue, wave.toValue) = (from, to)
        wave.duration = half
        wave.autoreverses = true
        wave.repeatCount = .infinity
        wave.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
        wave.timeOffset = phase
        return wave
    }

    /// Values at moments (seconds) over a round that repeats.
    private static func loop(_ key: String, _ moments: [(Double, Double)], every round: Double) -> CAKeyframeAnimation {
        let loop = CAKeyframeAnimation(keyPath: key)
        loop.values = moments.map(\.1)
        loop.keyTimes = moments.map { NSNumber(value: $0.0 / round) }
        loop.duration = round
        loop.repeatCount = .infinity
        return loop
    }

    // MARK: drawing

    /// One part drawn into a picture, at the screen's own sharpness.
    private static func picture(of part: BearDrawing.Part, unit: CGFloat, scale: CGFloat) -> CGImage? {
        let area = BearDrawing.area(of: part)
        let canvas = Canvas { context, _ in
            var context = context
            context.scaleBy(x: unit, y: unit)
            context.translateBy(x: -area.minX, y: -area.minY)
            BearDrawing.draw(part, in: context)
        }
        .frame(width: area.width * unit, height: area.height * unit)
        let renderer = ImageRenderer(content: canvas)
        renderer.scale = scale
        return renderer.cgImage
    }

    private static func colour(_ hex: UInt32, _ alpha: CGFloat = 1) -> CGColor {
        CGColor(
            srgbRed: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255, alpha: alpha)
    }
}
