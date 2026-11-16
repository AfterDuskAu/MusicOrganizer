import SwiftUI

/// Bear Media's bear (the owner's logo, 2026-10-17: a bear holding a ticket with a play
/// button, on a night sky): drawn, not a picture, so it's sharp at any size. It's the app's
/// icon (`make_icon.swift`, beside this, which `make_icon.sh` builds with it). Until
/// 2026-11-05 it was the app's loading screen too, and moved (it breathed, swayed, blinked
/// and spun its play button); that went with the loading screen, a film since, and this file
/// with it out of the app (2026-11-16). It's drawn still, as the logo is.
///
/// Only SwiftUI, and nothing of the app's own. Measured off the owner's 256-point picture, so
/// its shapes are given in those points and scaled to fit.
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

    /// The bear as the logo is (eyes open, upright), filling `size` as a square would, centred.
    static func draw(in context: inout GraphicsContext, size: CGSize) {
        let side = min(size.width, size.height)
        context.translateBy(x: (size.width - side) / 2, y: (size.height - side) / 2)
        context.scaleBy(x: side / 256, y: side / 256)
        drawBody(in: context)
        // The head and its ears.
        drawEar(in: context, x: 84, inner: 82)
        drawEar(in: context, x: 172, inner: 174)
        drawFace(in: context)
        drawEye(in: context, x: 105)
        drawEye(in: context, x: 151)
        // The ticket, held up in front.
        drawTicket(in: context)
        drawPlayButton(in: context, centre: discCentre)
        drawPaw(in: context, x: 72.5, inward: 1)
        drawPaw(in: context, x: 183.5, inward: -1)
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

    private static func drawEar(in context: GraphicsContext, x: CGFloat, inner: CGFloat) {
        context.fill(Path(ellipseIn: rect(x, 62, 21, 21)), with: .color(fur))
        context.fill(Path(ellipseIn: rect(inner, 61.5, 11.5, 11.5)), with: .color(light))
    }

    /// An eye, open, with its glint.
    private static func drawEye(in context: GraphicsContext, x: CGFloat) {
        context.fill(Path(ellipseIn: rect(x, 96, 6.6, 7.2)), with: .color(ink))
        context.fill(Path(ellipseIn: rect(x + 2.4, 96 - 2.9, 2.2, 2.2)), with: .color(.white.opacity(0.95)))
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

    /// The play button: a disc with its edge, and the arrow on it.
    private static func drawPlayButton(in context: GraphicsContext, centre: CGPoint) {
        let radius: CGFloat = 17.5
        let face = CGRect(x: centre.x - radius, y: centre.y - radius, width: radius * 2, height: radius * 2)
        context.drawLayer { glow in
            glow.addFilter(.shadow(color: Color(hex: 0xC2661A).opacity(0.35), radius: 3, x: 0, y: 1.5))
            glow.fill(Path(ellipseIn: face), with: .color(discEdge))
        }
        context.fill(Path(ellipseIn: face), with: .color(disc))
        var arrow = Path()
        let mid = face.midX + 1.6
        arrow.move(to: CGPoint(x: mid - 6.2, y: centre.y - 9))
        arrow.addLine(to: CGPoint(x: mid - 6.2, y: centre.y + 9))
        arrow.addLine(to: CGPoint(x: mid + 9.4, y: centre.y))
        arrow.closeSubpath()
        context.fill(arrow, with: .color(play))
        context.stroke(arrow, with: .color(play), style: StrokeStyle(lineWidth: 2.4, lineJoin: .round))
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

}

/// The bear on its night sky with its stars, filling whatever room it's given: the icon's
/// picture. The sky's colours can be another app's own. The sky's glow and its stars'
/// twinkle are as they are at `moment` (seconds since `Date`'s reference date, as the
/// loading screen's clock counted them); the bear is as the logo is.
struct BearScene: View {
    var skyTop = BearDrawing.skyTop
    var skyBottom = BearDrawing.skyBottom
    var moment: Double
    /// How much of the height the bear takes. It rises from the bottom edge, as in the
    /// owner's picture, its body going on below it.
    var bearSize: CGFloat = 0.8

    var body: some View {
        Canvas { context, size in
            drawSky(in: &context, size: size, at: moment)
            let side = bearSide(in: size)
            var bear = context
            bear.clip(to: Path(CGRect(origin: .zero, size: size)))
            bear.translateBy(x: (size.width - side) / 2, y: size.height - side)
            BearDrawing.draw(in: &bear, size: CGSize(width: side, height: side))
        }
        .background(skyBottom)
        .ignoresSafeArea()
    }

    private func bearSide(in size: CGSize) -> CGFloat { BearDrawing.side(in: size, share: bearSize) }

    /// The sky: dark at the top, lighter towards the bottom, a warm glow behind the bear,
    /// and a few stars, each as bright as its twinkle has it at `time`.
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
