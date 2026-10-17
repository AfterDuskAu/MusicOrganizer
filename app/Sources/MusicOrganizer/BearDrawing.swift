import SwiftUI

/// Bear Media's bear (the owner's logo, 2026-10-17: a bear holding a ticket with a play
/// button, on a night sky): drawn, not a picture, so it's sharp at any size, the whole
/// screen included, and alive. It breathes, sways a little, blinks, twitches an ear, and
/// the play button on its ticket spins.
///
/// Only SwiftUI, and nothing of the app's own: the app's icon is drawn from this same
/// file (`scripts/make_icon.sh`), and it can be copied as it is into an app for a phone
/// or a tablet. Measured off the owner's 256-point picture, so its shapes are given in
/// those points and scaled to fit.
enum BearDrawing {
    /// The night sky behind the bear, from its top to its bottom (the owner's picture),
    /// and the warm glow behind the bear, as 0xRRGGBB.
    static let skyHex: (top: UInt32, bottom: UInt32) = (0x1F1F26, 0x3C3C48)
    static let glowHex: UInt32 = 0xC9A27E
    static let skyTop = Color(hex: skyHex.top)
    static let skyBottom = Color(hex: skyHex.bottom)
    /// Where the stars are, as parts of the sky's width and height, and how big.
    static let stars: [(x: CGFloat, y: CGFloat, size: CGFloat)] = [
        (0.14, 0.15, 1.6), (0.84, 0.12, 1.5), (0.09, 0.41, 1.2), (0.9, 0.32, 1.3),
        (0.24, 0.07, 0.9), (0.68, 0.06, 1.0), (0.95, 0.6, 0.9), (0.05, 0.72, 1.0),
        (0.31, 0.24, 0.8), (0.76, 0.22, 0.8), (0.58, 0.14, 0.7), (0.18, 0.88, 0.8),
    ]
    /// The ticket, its tile, and the play button's middle, in the picture's points.
    static let ticketOuter = CGRect(x: 76, y: 150, width: 103, height: 73)
    static let ticketTile = ticketOuter.insetBy(dx: 5.5, dy: 5.5)
    static let discCentre = CGPoint(x: 128, y: 188)

    /// How big the bear's square is in a scene of this size, taking `share` of its height.
    /// On a screen held upright (a phone) it may be wider than the screen: only sky is cut
    /// off at the sides.
    static func side(in size: CGSize, share: CGFloat) -> CGFloat {
        min(size.height * share, size.width * (size.height > size.width * 1.3 ? 1.2 : 0.9))
    }

    private static let fur = Color(hex: 0xAB6F43)
    private static let furShade = Color(hex: 0x95603A)
    private static let furLine = Color(hex: 0x7A4A2A)
    private static let light = Color(hex: 0xE1B388)
    private static let ink = Color(hex: 0x381C06)
    private static let cheek = Color(hex: 0xDF856D)
    private static let frame = Color(hex: 0xFDB04B)
    private static let frameLine = Color(hex: 0xC9782C)
    private static let tileTop = Color(hex: 0xFFD7A0)
    private static let tileBottom = Color(hex: 0xFFBF68)
    private static let disc = Color(hex: 0xFFFCF8)
    private static let discEdge = Color(hex: 0xE6D6C2)
    private static let play = Color(hex: 0xFF9C27)

    /// The bear at this moment (seconds; any steady clock), filling `size` as a square
    /// would, centred. At `still` it's as the logo is: eyes open, upright.
    static func draw(in context: inout GraphicsContext, size: CGSize, at time: Double, still: Bool = false) {
        let side = min(size.width, size.height)
        context.translateBy(x: (size.width - side) / 2, y: (size.height - side) / 2)
        context.scaleBy(x: side / 256, y: side / 256)
        let pose = still ? Pose() : Pose(at: time)

        // The body, breathing from where it sits.
        var body = context
        body.translateBy(x: 128, y: 256)
        body.scaleBy(x: 1 + pose.breath * 0.006, y: 1 + pose.breath * 0.014)
        body.translateBy(x: -128, y: -256)
        drawBody(in: body)

        // The head and its ears, swaying a little from the neck, and lifting as it breathes.
        var head = context
        head.translateBy(x: 128, y: 152 - pose.breath * 1.2)
        head.rotate(by: .radians(pose.sway))
        head.translateBy(x: -128, y: -152)
        drawEar(in: head, x: 84, inner: 82, tilt: pose.twitch)
        drawEar(in: head, x: 172, inner: 174, tilt: 0)
        drawFace(in: head)
        drawEye(in: head, x: 105, look: pose.look, blink: pose.blink)
        drawEye(in: head, x: 151, look: pose.look, blink: pose.blink)

        // The ticket, held up in front, rising and falling with the paws, a sheen passing
        // over it now and then.
        var held = context
        held.translateBy(x: 0, y: pose.bob)
        drawTicket(in: held)
        if pose.shine > 0, pose.shine < 1 {
            var sheen = held
            sheen.clip(to: Path(roundedRect: ticketTile, cornerRadius: 7.5, style: .continuous))
            drawBand(in: sheen, at: ticketTile.minX - 40 + (ticketTile.width + 80) * pose.shine)
        }
        drawPlayButton(in: held, centre: discCentre, spin: pose.spin)
        drawPaw(in: held, x: 72.5, inward: 1)
        drawPaw(in: held, x: 183.5, inward: -1)
    }

    // MARK: one part at a time

    /// The bear's parts, each drawn on its own and still, for the loading screen to move
    /// each in the window server (`BearLayers`): the same shapes as above.
    enum Part: CaseIterable {
        case body, leftEar, rightEar, head, eyes, lids, ticket, sheen, disc, leftPaw, rightPaw
    }

    /// Where a part is, in the picture's points, with room for its shadow or softness.
    static func area(of part: Part) -> CGRect {
        switch part {
        case .body: CGRect(x: 50, y: 155, width: 156, height: 166)
        case .leftEar: CGRect(x: 60, y: 38, width: 48, height: 48)
        case .rightEar: CGRect(x: 148, y: 38, width: 48, height: 48)
        case .head: CGRect(x: 60, y: 42, width: 136, height: 118)
        case .eyes, .lids: CGRect(x: 94, y: 86, width: 68, height: 20)
        case .ticket: CGRect(x: 62, y: 140, width: 132, height: 100)
        case .sheen: CGRect(x: ticketTile.minX - 30, y: ticketTile.minY, width: 52, height: ticketTile.height)
        case .disc: CGRect(x: 104, y: 164, width: 48, height: 50)
        case .leftPaw: CGRect(x: 53, y: 167, width: 40, height: 48)
        case .rightPaw: CGRect(x: 163, y: 167, width: 40, height: 48)
        }
    }

    /// One part, still, in the picture's points: the eyes open, the lids shut, the sheen
    /// at the start of its pass.
    static func draw(_ part: Part, in context: GraphicsContext) {
        switch part {
        case .body: drawBody(in: context)
        case .leftEar: drawEar(in: context, x: 84, inner: 82, tilt: 0)
        case .rightEar: drawEar(in: context, x: 172, inner: 174, tilt: 0)
        case .head: drawFace(in: context)
        case .eyes: for x in [105.0, 151.0] { drawEye(in: context, x: x, look: .zero, blink: 0) }
        case .lids: for x in [105.0, 151.0] { drawEye(in: context, x: x, look: .zero, blink: 1) }
        case .ticket: drawTicket(in: context)
        case .sheen: drawBand(in: context, at: area(of: .sheen).minX + 30)
        case .disc: drawPlayButton(in: context, centre: discCentre, spin: 0)
        case .leftPaw: drawPaw(in: context, x: 72.5, inward: 1)
        case .rightPaw: drawPaw(in: context, x: 183.5, inward: -1)
        }
    }

    // MARK: the parts

    private static func drawBody(in context: GraphicsContext) {
        context.fill(Path(ellipseIn: rect(128, 238, 74, 80)), with: .linearGradient(
            Gradient(colors: [fur, furShade]), startPoint: CGPoint(x: 128, y: 170), endPoint: CGPoint(x: 128, y: 300)))
    }

    /// The head, with its cheeks, muzzle, nose and mouth: all but the eyes.
    private static func drawFace(in context: GraphicsContext) {
        context.fill(Path(ellipseIn: rect(128, 100, 63, 54)), with: .color(fur))
        context.drawLayer { soft in
            soft.addFilter(.blur(radius: 1.6))
            soft.opacity = 0.5
            soft.fill(Path(ellipseIn: rect(90, 119, 10.5, 6.2)), with: .color(cheek))
            soft.fill(Path(ellipseIn: rect(166, 119, 10.5, 6.2)), with: .color(cheek))
        }
        context.fill(Path(ellipseIn: rect(128, 126.5, 26.5, 18.5)), with: .color(light))
        context.fill(Path(ellipseIn: rect(128, 117, 10, 6.6)), with: .color(ink))
        context.fill(Path(ellipseIn: rect(124.2, 114.6, 2.8, 1.5)), with: .color(.white.opacity(0.45)))
        var mouth = Path()
        mouth.move(to: CGPoint(x: 128, y: 122.5))
        mouth.addLine(to: CGPoint(x: 128, y: 129.5))
        mouth.move(to: CGPoint(x: 114.5, y: 129))
        mouth.addQuadCurve(to: CGPoint(x: 128, y: 129.5), control: CGPoint(x: 120.5, y: 138))
        mouth.addQuadCurve(to: CGPoint(x: 141.5, y: 129), control: CGPoint(x: 135.5, y: 138))
        context.stroke(mouth, with: .color(ink), style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
    }

    private static func drawEar(in context: GraphicsContext, x: CGFloat, inner: CGFloat, tilt: Double) {
        var ear = context
        // An ear twitches about where it meets the head.
        let base = CGPoint(x: x + (x < 128 ? 9 : -9), y: 76)
        ear.translateBy(x: base.x, y: base.y)
        ear.rotate(by: .radians(tilt))
        ear.translateBy(x: -base.x, y: -base.y)
        ear.fill(Path(ellipseIn: rect(x, 62, 21, 21)), with: .color(fur))
        ear.fill(Path(ellipseIn: rect(inner, 61.5, 11.5, 11.5)), with: .color(light))
    }

    private static func drawEye(in context: GraphicsContext, x: CGFloat, look: CGPoint, blink: Double) {
        let open = max(0.08, 1 - blink)
        let centre = CGPoint(x: x + look.x, y: 96 + look.y)
        if open < 0.35 {
            // Shut: a little curve, as the lid closes.
            var lid = Path()
            lid.move(to: CGPoint(x: centre.x - 6.5, y: centre.y - 0.5))
            lid.addQuadCurve(to: CGPoint(x: centre.x + 6.5, y: centre.y - 0.5), control: CGPoint(x: centre.x, y: centre.y + 4))
            context.stroke(lid, with: .color(ink), style: StrokeStyle(lineWidth: 2.2, lineCap: .round))
            return
        }
        context.fill(Path(ellipseIn: rect(centre.x, centre.y, 6.6, 7.2 * open)), with: .color(ink))
        if open > 0.7 {
            context.fill(Path(ellipseIn: rect(centre.x + 2.4, centre.y - 2.9, 2.2, 2.2)), with: .color(.white.opacity(0.95)))
        }
    }

    /// The ticket: its frame and shadow, its tile and stitching (not the play button).
    private static func drawTicket(in context: GraphicsContext) {
        let (outer, tile) = (ticketOuter, ticketTile)
        let outerPath = Path(roundedRect: outer, cornerRadius: 11, style: .continuous)
        let tilePath = Path(roundedRect: tile, cornerRadius: 7.5, style: .continuous)
        // Its shadow, on the bear.
        context.drawLayer { shadow in
            shadow.addFilter(.shadow(color: .black.opacity(0.35), radius: 6, x: 0, y: 5))
            shadow.fill(outerPath, with: .color(frame))
        }
        context.fill(tilePath, with: .linearGradient(
            Gradient(colors: [tileTop, tileBottom]), startPoint: CGPoint(x: 0, y: tile.minY),
            endPoint: CGPoint(x: 0, y: tile.maxY)))
        // The stitching along the top and bottom, as on a ticket.
        for x in stride(from: 88.0, through: 163.0, by: 12.5) {
            for y in [151.6, 219.2] {
                context.fill(
                    Path(roundedRect: CGRect(x: x, y: y, width: 6, height: 2.3), cornerRadius: 1.1),
                    with: .color(frameLine.opacity(0.75)))
            }
        }
    }

    /// The sheen's band of light, slanting, with `x` the top of its leading edge.
    private static func drawBand(in context: GraphicsContext, at x: CGFloat) {
        let tile = ticketTile
        var sheen = context
        var band = Path()
        band.move(to: CGPoint(x: x, y: tile.minY))
        band.addLine(to: CGPoint(x: x + 16, y: tile.minY))
        band.addLine(to: CGPoint(x: x - 8, y: tile.maxY))
        band.addLine(to: CGPoint(x: x - 24, y: tile.maxY))
        band.closeSubpath()
        sheen.addFilter(.blur(radius: 3))
        sheen.fill(band, with: .color(.white.opacity(0.32)))
    }

    /// The play button, turning about its upright line like a coin spun on a table: its
    /// face narrows to its edge and opens again, and the arrow always points on.
    private static func drawPlayButton(in context: GraphicsContext, centre: CGPoint, spin: Double) {
        let radius: CGFloat = 17.5
        let turn = cos(spin)
        let width = max(0.06, abs(turn))
        // Its edge shows, beside the face, as it turns.
        let edge = (1 - width) * 2.6 * (sin(spin) >= 0 ? 1 : -1)
        context.drawLayer { glow in
            glow.addFilter(.shadow(color: Color(hex: 0xC2661A).opacity(0.35), radius: 3, x: 0, y: 1.5))
            glow.fill(
                Path(ellipseIn: CGRect(
                    x: centre.x - radius * width + min(0, edge), y: centre.y - radius,
                    width: radius * 2 * width + abs(edge), height: radius * 2)),
                with: .color(discEdge))
        }
        let face = CGRect(x: centre.x - radius * width + max(0, edge), y: centre.y - radius, width: radius * 2 * width, height: radius * 2)
        context.fill(Path(ellipseIn: face), with: .color(disc.opacity(0.88 + 0.12 * width)))
        var arrow = Path()
        let mid = face.midX + 1.6 * width
        arrow.move(to: CGPoint(x: mid - 6.2 * width, y: centre.y - 9))
        arrow.addLine(to: CGPoint(x: mid - 6.2 * width, y: centre.y + 9))
        arrow.addLine(to: CGPoint(x: mid + 9.4 * width, y: centre.y))
        arrow.closeSubpath()
        context.fill(arrow, with: .color(play))
        context.stroke(arrow, with: .color(play), style: StrokeStyle(lineWidth: 2.4 * width, lineJoin: .round))
    }

    private static func drawPaw(in context: GraphicsContext, x: CGFloat, inward: CGFloat) {
        context.drawLayer { paw in
            paw.addFilter(.shadow(color: .black.opacity(0.18), radius: 2, x: 0, y: 1.5))
            paw.fill(Path(ellipseIn: rect(x, 190, 15.5, 19)), with: .color(fur))
        }
        var toes = Path()
        for y in [177.5, 184.0, 190.5] {
            toes.move(to: CGPoint(x: x + inward * 7, y: y))
            toes.addLine(to: CGPoint(x: x + inward * 14, y: y))
        }
        context.stroke(toes, with: .color(furLine), style: StrokeStyle(lineWidth: 1.5, lineCap: .round))
    }

    private static func rect(_ x: CGFloat, _ y: CGFloat, _ halfWidth: CGFloat, _ halfHeight: CGFloat) -> CGRect {
        CGRect(x: x - halfWidth, y: y - halfHeight, width: halfWidth * 2, height: halfHeight * 2)
    }

    // MARK: how it moves

    /// Where everything is at a moment. Each movement has its own length, so together
    /// they don't fall into step and look like a loop.
    private struct Pose {
        var breath = 0.0  // -1 … 1
        var sway = 0.0  // radians
        var bob = 0.0  // points
        var blink = 0.0  // 0 open … 1 shut
        var twitch = 0.0  // radians
        var look = CGPoint.zero
        var spin = 0.0  // radians
        var shine = 0.0  // 0 … 1 across the ticket, else none

        init() {}

        init(at time: Double) {
            breath = sin(time * 2 * .pi / 3.6)
            sway = sin(time * 2 * .pi / 5.3) * 2.2 * .pi / 180
            bob = sin(time * 2 * .pi / 3.6 + 0.8) * 1.7
            blink = Self.blink(at: time)
            twitch = Self.twitch(at: time)
            look = CGPoint(x: sin(time * 2 * .pi / 7.7) * 1.1, y: sin(time * 2 * .pi / 9.1) * 0.5)
            // The play button: a whole turn, eased, then a moment's rest.
            let round = (time / 2.4).truncatingRemainder(dividingBy: 1)
            let turning = min(1, round / 0.6)
            spin = (turning < 0.5 ? 2 * turning * turning : 1 - pow(-2 * turning + 2, 2) / 2) * 2 * .pi
            let pass = (time / 3.7).truncatingRemainder(dividingBy: 1)
            shine = pass < 0.45 ? pass / 0.45 : 0
        }

        /// Blinks at uneven moments, once a double, over a twelve-second round.
        private static func blink(at time: Double) -> Double {
            let now = time.truncatingRemainder(dividingBy: 12)
            for start in [0.6, 3.9, 7.4, 7.75, 10.6] where now >= start && now < start + 0.2 {
                return sin(.pi * (now - start) / 0.2)
            }
            return 0
        }

        /// The left ear flicks once in a while.
        private static func twitch(at time: Double) -> Double {
            let now = time.truncatingRemainder(dividingBy: 8.5)
            guard now > 5, now < 5.4 else { return 0 }
            return -sin(.pi * (now - 5) / 0.4) * 9 * .pi / 180
        }
    }
}

/// The bear on its night sky with its stars, alive, filling whatever room it's given:
/// Bear Media's loading screen. The sky's colours can be another app's own. Given a
/// `moment`, it's that moment only, not moving (a picture of it); `still` is the logo.
struct BearScene: View {
    var skyTop = BearDrawing.skyTop
    var skyBottom = BearDrawing.skyBottom
    var moment: Double?
    var still = false
    /// How much of the height the bear takes. It rises from the bottom edge, as in the
    /// owner's picture, its body going on below it.
    var bearSize: CGFloat = 0.8
    /// Pictures a second at most: under the app's own start, fewer leave it more room.
    var frameRate: Double = 60

    var body: some View {
        if let moment {
            scene(at: moment)
        } else {
            TimelineView(.animation(minimumInterval: 1 / frameRate)) { timeline in
                scene(at: timeline.date.timeIntervalSinceReferenceDate)
            }
        }
    }

    private func scene(at time: Double) -> some View {
        Canvas { context, size in
            drawSky(in: &context, size: size, at: time)
            let side = bearSide(in: size)
            var bear = context
            bear.clip(to: Path(CGRect(origin: .zero, size: size)))
            bear.translateBy(x: (size.width - side) / 2, y: size.height - side)
            BearDrawing.draw(in: &bear, size: CGSize(width: side, height: side), at: time, still: still)
        }
        .background(skyBottom)
        .ignoresSafeArea()
    }

    private func bearSide(in size: CGSize) -> CGFloat { BearDrawing.side(in: size, share: bearSize) }

    /// The sky: dark at the top, lighter towards the bottom, a warm glow behind the bear,
    /// and a few stars that twinkle.
    private func drawSky(in context: inout GraphicsContext, size: CGSize, at time: Double) {
        let all = CGRect(origin: .zero, size: size)
        context.fill(Path(all), with: .linearGradient(
            Gradient(colors: [skyTop, skyBottom]), startPoint: .zero, endPoint: CGPoint(x: 0, y: size.height)))
        let side = min(size.width, size.height)
        // Behind the bear's head, wherever the bear is.
        let centre = CGPoint(x: size.width / 2, y: size.height - bearSide(in: size) * (1 - 110 / 256))
        let glow = 0.22 + 0.04 * sin(time * 2 * .pi / 6)
        context.fill(Path(all), with: .radialGradient(
            Gradient(colors: [Color(hex: BearDrawing.glowHex).opacity(glow), .clear]), center: centre,
            startRadius: 0, endRadius: side * 0.5))
        let dot = side / 256
        for (index, star) in BearDrawing.stars.enumerated() {
            let twinkle = 0.55 + 0.45 * sin(time * 2 * .pi / (2.3 + Double(index % 5) * 0.7) + Double(index) * 1.9)
            let r = star.2 * dot * 1.2
            context.fill(
                Path(ellipseIn: CGRect(x: star.0 * size.width - r, y: star.1 * size.height - r, width: r * 2, height: r * 2)),
                with: .color(.white.opacity(0.85 * twinkle)))
        }
    }
}

extension Color {
    /// A colour given as 0xRRGGBB.
    init(hex: UInt32) {
        self.init(
            red: Double((hex >> 16) & 0xFF) / 255, green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255)
    }
}
