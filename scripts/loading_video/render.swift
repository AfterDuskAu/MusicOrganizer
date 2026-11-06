// Bear Media's loading film (the owner, 2026-11-06): the name in particles, three seconds at
// 4K. Particles in the name's own browns and golds swirl round as a spiral galaxy, spiral in
// onto its letters (the "Honey" sketch the owner chose of four: rounded, bear ears on the B,
// deep brown to honey yellow across it) and ring as they settle; then the name stands
// complete for the last second, with a sheen passing over it. The apps hold its last picture
// until they're ready. Drawn entirely in code (Core Graphics and Core Text, the letters
// Arial Rounded MT Bold's) and written as an HEVC video. scripts/make_loading_video.sh runs it.
//
//   swiftc -O -o render scripts/loading_video/render.swift
//   ./render video <landscape|portrait> out.mp4 [megabits]   3840×2160, or 2160×3840 upright
//   ./render still <layout> <seconds> out.png [scale]          ./render sheet <layout> out.png
import AVFoundation
import CoreGraphics
import CoreText
import CoreVideo
import Foundation
import ImageIO

let length = 3.0
let fps = 30
// The picture's shape, in points (y up): set from the command line before anything is made.
let upright = CommandLine.arguments.count > 2 && CommandLine.arguments[2] == "portrait"
let W = upright ? 1080.0 : 1920.0
let H = upright ? 1920.0 : 1080.0
/// The swirl's width and height for a radius: wide on a wide screen, tall on a tall one.
let swirlShape = upright ? (x: 0.62, y: 1.0) : (x: 1.0, y: 0.58)
let space = CGColorSpace(name: CGColorSpace.sRGB)!

func clamp01(_ x: Double) -> Double { min(max(x, 0), 1) }
func smooth(_ a: Double, _ b: Double, _ x: Double) -> Double {
    let t = clamp01((x - a) / (b - a))
    return t * t * (3 - 2 * t)
}
func easeInOut(_ x: Double) -> Double {
    let t = clamp01(x)
    return t < 0.5 ? 4 * t * t * t : 1 - pow(-2 * t + 2, 3) / 2
}

struct RGB {
    var r, g, b: Double
    init(_ r: Double, _ g: Double, _ b: Double) { (self.r, self.g, self.b) = (r, g, b) }
    init(hex: UInt32) { self.init(Double((hex >> 16) & 0xFF) / 255, Double((hex >> 8) & 0xFF) / 255, Double(hex & 0xFF) / 255) }
    func mixed(_ o: RGB, _ t: Double) -> RGB { RGB(r + (o.r - r) * t, g + (o.g - g) * t, b + (o.b - b) * t) }
    static func * (a: RGB, k: Double) -> RGB { RGB(a.r * k, a.g * k, a.b * k) }
    func cg(_ a: Double = 1) -> CGColor { CGColor(colorSpace: space, components: [CGFloat(clamp01(r)), CGFloat(clamp01(g)), CGFloat(clamp01(b)), CGFloat(a)])! }
}

struct RNG {
    var state: UInt64
    init(_ seed: UInt64) { state = seed &* 0x9E37_79B9_7F4A_7C15 &+ 77 }
    mutating func next() -> UInt64 {
        state &+= 0x9E37_79B9_7F4A_7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
        z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
        return z ^ (z >> 31)
    }
    mutating func unit() -> Double { Double(next() >> 11) / Double(UInt64(1) << 53) }
    mutating func range(_ a: Double, _ b: Double) -> Double { a + (b - a) * unit() }
}

func gradient(_ stops: [(Double, UInt32, Double)]) -> CGGradient {
    CGGradient(colorsSpace: space, colors: stops.map { RGB(hex: $0.1).cg($0.2) } as CFArray, locations: stops.map { CGFloat($0.0) })!
}

// MARK: - The name (the "Honey" sketch)

struct Name {
    let text: CGPath
    let ears: CGPath
    let inner: CGPath
    let box: CGRect
    static let stops: [(Double, UInt32)] = [(0, 0x5A3214), (0.35, 0x9A5A22), (0.7, 0xD99A36), (1, 0xF7CF5A)]

    init() {
        let font = CTFontCreateWithName("ArialRoundedMTBold" as CFString, 200, nil)
        var glyphPaths: [CGPath] = []
        var x: CGFloat = 0
        for ch in "Bear Media" {
            let s = String(ch) as NSString
            var u = [UniChar](repeating: 0, count: s.length)
            s.getCharacters(&u)
            var g = [CGGlyph](repeating: 0, count: u.count)
            CTFontGetGlyphsForCharacters(font, u, &g, u.count)
            var adv = CGSize.zero
            CTFontGetAdvancesForGlyphs(font, .horizontal, g, &adv, 1)
            if let p = CTFontCreatePathForGlyph(font, g[0], nil) {
                var t = CGAffineTransform(translationX: x, y: 0)
                glyphPaths.append(p.copy(using: &t)!)
            }
            x += adv.width + 4
        }
        let raw = CGMutablePath()
        for p in glyphPaths { raw.addPath(p) }
        let rb = raw.boundingBoxOfPath
        let scale = W * (upright ? 0.74 : 0.68) / rb.width
        var place = CGAffineTransform(translationX: W / 2, y: H / 2 - 18).scaledBy(x: scale, y: scale).translatedBy(x: -rb.midX, y: -rb.midY)
        text = raw.copy(using: &place)!
        // Ears on the B.
        let b = glyphPaths[0].copy(using: &place)!.boundingBoxOfPath
        let r = b.height * 0.19
        let e = CGMutablePath()
        let i = CGMutablePath()
        for fx in [0.2, 0.74] as [CGFloat] {
            let c = CGPoint(x: b.minX + b.width * fx, y: b.maxY + b.height * 0.04)
            e.addEllipse(in: CGRect(x: c.x - r, y: c.y - r, width: r * 2, height: r * 2))
            i.addEllipse(in: CGRect(x: c.x - r * 0.5, y: c.y - r * 0.42, width: r, height: r))
        }
        ears = e
        inner = i
        box = text.boundingBoxOfPath.union(e.boundingBoxOfPath)
    }

    func colour(atX x: Double) -> RGB {
        let u = clamp01((x - box.minX) / box.width)
        for k in 1..<Name.stops.count where u <= Name.stops[k].0 {
            let a = Name.stops[k - 1]
            let b = Name.stops[k]
            return RGB(hex: a.1).mixed(RGB(hex: b.1), (u - a.0) / (b.0 - a.0))
        }
        return RGB(hex: Name.stops.last!.1)
    }

    func contains(_ p: CGPoint) -> Bool { text.contains(p, using: .winding) || ears.contains(p) }

    /// The name itself, crisp: its glow, the ears, the letters, a sheen over the top half,
    /// and a band of light passing over it (`shine`, 0 → 1; nil for none).
    func draw(_ ctx: CGContext, alpha: Double, glowing: Double, shine: Double?) {
        guard alpha > 0.001 else { return }
        ctx.saveGState()
        ctx.setAlpha(CGFloat(alpha))
        ctx.beginTransparencyLayer(auxiliaryInfo: nil)
        let colours = gradient(Name.stops.map { ($0.0, $0.1, 1) })
        let whole = CGMutablePath()
        whole.addPath(ears)
        whole.addPath(text)
        for path in [ears, text] {
            ctx.saveGState()
            ctx.setShadow(offset: .zero, blur: CGFloat(44 * glowing) * 2, color: RGB(hex: 0xE8A23A).cg(0.55 * glowing))
            ctx.addPath(path)
            ctx.setFillColor(RGB(hex: 0xE8A23A).cg(0.6))
            ctx.fillPath()
            ctx.restoreGState()
        }
        for path in [ears, text] {
            ctx.saveGState()
            ctx.addPath(path)
            ctx.clip()
            ctx.drawLinearGradient(colours, start: CGPoint(x: box.minX, y: 0), end: CGPoint(x: box.maxX, y: 0), options: [])
            ctx.restoreGState()
        }
        ctx.saveGState()
        ctx.addPath(inner)
        ctx.clip()
        ctx.drawLinearGradient(gradient([(0, 0x3A1E0A, 1), (1, 0x6E3A16, 1)]), start: CGPoint(x: box.minX, y: 0), end: CGPoint(x: box.maxX, y: 0), options: [])
        ctx.restoreGState()
        ctx.saveGState()
        ctx.addPath(whole)
        ctx.clip()
        ctx.drawLinearGradient(gradient([(0, 0xFFFFFF, 0.32), (1, 0xFFFFFF, 0)]), start: CGPoint(x: 0, y: box.maxY), end: CGPoint(x: 0, y: box.midY), options: [])
        if let shine {
            let x = box.minX - 260 + (box.width + 520) * shine
            let band = gradient([(0, 0xFFF6DA, 0), (0.5, 0xFFF6DA, 0.6), (1, 0xFFF6DA, 0)])
            ctx.drawLinearGradient(band, start: CGPoint(x: x - 120, y: box.minY), end: CGPoint(x: x + 120, y: box.maxY + 60), options: [])
        }
        ctx.restoreGState()
        ctx.endTransparencyLayer()
        ctx.restoreGState()
    }
}

// MARK: - The particles

struct Particle {
    var target: CGPoint
    var angle: Double  // where it starts round the swirl
    var radius: Double
    var speed: Double  // radians a second, anticlockwise
    var start: Double  // when it begins to come in
    var takes: Double
    var curl: Double
    var from: RGB
    var to: RGB
    var size0: Double
    var size1: Double
    var phase: Double
    var drifts: Bool  // one of the few that drift away as sparks at the end
}

/// The swirl's colours: the name's own browns, ambers and golds (the owner: "make the
/// swirls similar color to the yellow/brown title name"), the brighter ones more often, as
/// the darkest browns hardly show against the night.
let palette: [UInt32] = [0x7A4418, 0x9A5A22, 0xB8732E, 0xC9853A, 0xD99A36, 0xD99A36, 0xE8B04A, 0xE8B04A,
                         0xF2C14E, 0xF7CF5A, 0xFFE08A, 0xFFF0C0]
let centre = CGPoint(x: W / 2, y: H / 2)

func makeParticles(_ name: Name, count: Int) -> [Particle] {
    var rng = RNG(2026)
    var out: [Particle] = []
    let box = name.box
    while out.count < count {
        let p = CGPoint(x: rng.range(box.minX, box.maxX), y: rng.range(box.minY, box.maxY))
        guard name.contains(p) else { continue }
        let u = (p.x - box.minX) / box.width
        var to = name.colour(atX: p.x) * rng.range(1.0, 1.35)
        if name.inner.contains(p) { to = RGB(hex: 0x4A2610) * rng.range(0.9, 1.1) }
        let start = 0.5 + 0.55 * u + rng.range(-0.12, 0.12)
        // Three spiral arms, like a galaxy, rather than an even disc.
        let radius = 120 + 1030 * pow(rng.unit(), 0.8)
        let arm = Double(rng.next() % 3) * 2 * .pi / 3
        let spread = (rng.unit() + rng.unit() + rng.unit() - 1.5) * 0.5
        out.append(Particle(
            target: p, angle: arm + radius * 0.0042 + spread, radius: radius, speed: rng.range(1.7, 3.0),
            start: start, takes: rng.range(0.55, 0.8), curl: rng.range(-0.38, 0.38),
            from: RGB(hex: palette[Int(rng.next() % UInt64(palette.count))]) * rng.range(0.8, 1.1), to: to,
            size0: rng.range(1.5, 3.2), size1: rng.range(2.4, 3.6), phase: rng.range(0, 2 * .pi),
            drifts: rng.unit() < 0.08))
    }
    return out
}

/// Where a particle is at a moment, how far in it has come (0 → 1), and since when it landed.
func place(_ q: Particle, _ t: Double) -> (CGPoint, Double, Double) {
    let a = q.angle + q.speed * t
    let r = q.radius * (1 - 0.42 * smooth(0, 1.7, t))
    let swirl = CGPoint(x: centre.x + r * cos(a) * swirlShape.x, y: centre.y + r * sin(a) * swirlShape.y)
    let s = easeInOut((t - q.start) / q.takes)
    let dx = q.target.x - swirl.x
    let dy = q.target.y - swirl.y
    let bend = q.curl * sin(.pi * s)
    var x = swirl.x + dx * s - dy * bend
    var y = swirl.y + dy * s + dx * bend
    let landed = t - (q.start + q.takes)
    if landed > 0 {
        // It rings as it settles.
        let ring = 2.4 * sin(55 * landed + q.phase) * exp(-7 * landed)
        x += ring * cos(q.phase)
        y += ring * sin(q.phase)
    }
    if q.drifts && t > 2.15 {
        let d = t - 2.15
        x += d * d * 90 * cos(q.phase)
        y += d * 60 + d * d * 40
    }
    return (CGPoint(x: x, y: y), s, landed)
}

// MARK: - A frame

final class Film: @unchecked Sendable {
    let s: Double
    let name = Name()
    let particles: [Particle]
    let sky: CGImage

    init(scale s: Double) {
        self.s = s
        particles = makeParticles(name, count: 30000)
        let ctx = bitmap(Int(W * s), Int(H * s))
        ctx.scaleBy(x: s, y: s)
        ctx.drawLinearGradient(gradient([(0, 0x050A1C, 1), (1, 0x10224A, 1)]), start: CGPoint(x: 0, y: H), end: .zero, options: [])
        var rng = RNG(9)
        for _ in 0..<900 {
            let r = rng.range(0.4, 1.3)
            ctx.setFillColor(RGB(1, 1, 1).cg(rng.range(0.12, 0.5)))
            ctx.fillEllipse(in: CGRect(x: rng.range(0, W), y: rng.range(0, H), width: r * 2, height: r * 2))
        }
        ctx.translateBy(x: W / 2, y: H / 2)
        ctx.scaleBy(x: W / 2, y: H / 2)
        ctx.drawRadialGradient(gradient([(0, 0x000000, 0), (0.5, 0x000000, 0), (1, 0x000008, 0.5)]), startCenter: .zero, startRadius: 0, endCenter: .zero, endRadius: 1.42, options: [.drawsAfterEndLocation])
        sky = ctx.makeImage()!
    }

    var width: Int { Int(W * s) }
    var height: Int { Int(H * s) }

    func render(_ t: Double, into ctx: CGContext) {
        ctx.saveGState()
        ctx.scaleBy(x: s, y: s)
        ctx.draw(sky, in: CGRect(x: 0, y: 0, width: W, height: H))
        let crisp = smooth(1.85, 2.2, t)
        let handOver = smooth(2.05, 2.4, t)
        let dt = 0.5 / Double(fps)  // half a frame's travel: a camera's blur
        ctx.setLineCap(.round)
        let pulse = name.box.minX + (t - 1.95) * 2600  // a wave of light along the name as it rings
        // Glows first, added together, while they swirl.
        ctx.setBlendMode(.plusLighter)
        for q in particles {
            let (p, s, _) = place(q, t)
            let swirling = 1 - smooth(0.5, 1.0, s)
            let showing = smooth(0, 0.35, t - q.radius / 1150 * 0.15)
            let glowA = 0.11 * swirling * showing
            guard glowA > 0.004 else { continue }
            let (b, _, _) = place(q, t - dt)
            let c = q.from.mixed(q.to, smooth(0.15, 0.85, s))
            ctx.setStrokeColor(c.cg(glowA))
            ctx.setLineWidth(CGFloat(q.size0 * 4.5))
            ctx.move(to: b)
            ctx.addLine(to: p)
            ctx.strokePath()
        }
        ctx.setBlendMode(.normal)
        // The particles themselves.
        for q in particles {
            let (p, s, landed) = place(q, t)
            let showing = smooth(0, 0.35, t - q.radius / 1150 * 0.15)
            var alpha = 0.95 * showing * (1 - 0.85 * handOver)
            if q.drifts && t > 2.15 { alpha = 0.9 * showing * (1 - smooth(2.3, 3.0, t)) }
            guard alpha > 0.01 else { continue }
            let (b, _, _) = place(q, t - dt)
            var c = q.from.mixed(q.to, smooth(0.15, 0.85, s))
            if landed > 0 { c = c.mixed(RGB(1, 0.95, 0.8), 0.5 * exp(-10 * landed)) }
            let wave = exp(-pow((Double(p.x) - pulse) / 70, 2))
            c = c.mixed(RGB(1, 0.93, 0.7), 0.45 * wave * (1 - crisp * 0.5))
            ctx.setStrokeColor(c.cg(alpha))
            ctx.setLineWidth(CGFloat(q.size0 + (q.size1 - q.size0) * s))
            ctx.move(to: b)
            ctx.addLine(to: p)
            ctx.strokePath()
        }
        // The name, complete.
        let shine = t > 2.25 ? smooth(2.25, 2.95, t) : nil
        name.draw(ctx, alpha: crisp, glowing: 0.8 + 0.25 * smooth(2.0, 2.4, t), shine: shine)
        ctx.restoreGState()
    }

    func image(at t: Double) -> CGImage {
        let ctx = bitmap(width, height)
        render(t, into: ctx)
        return ctx.makeImage()!
    }
}

func bitmap(_ w: Int, _ h: Int) -> CGContext {
    CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0, space: space,
              bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue)!
}

func writePNG(_ image: CGImage, _ path: String) {
    let dest = CGImageDestinationCreateWithURL(URL(fileURLWithPath: path) as CFURL, "public.png" as CFString, 1, nil)!
    CGImageDestinationAddImage(dest, image, nil)
    CGImageDestinationFinalize(dest)
}

func writeVideo(_ film: Film, _ path: String, bitrate: Int) throws {
    let (w, h) = (film.width, film.height)
    let url = URL(fileURLWithPath: path)
    try? FileManager.default.removeItem(at: url)
    let writer = try AVAssetWriter(outputURL: url, fileType: .mp4)
    let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
        AVVideoCodecKey: AVVideoCodecType.hevc, AVVideoWidthKey: w, AVVideoHeightKey: h,
        AVVideoColorPropertiesKey: [AVVideoColorPrimariesKey: AVVideoColorPrimaries_ITU_R_709_2,
                                    AVVideoTransferFunctionKey: AVVideoTransferFunction_ITU_R_709_2,
                                    AVVideoYCbCrMatrixKey: AVVideoYCbCrMatrix_ITU_R_709_2],
        AVVideoCompressionPropertiesKey: [AVVideoAverageBitRateKey: bitrate, AVVideoExpectedSourceFrameRateKey: fps,
                                          AVVideoMaxKeyFrameIntervalKey: fps, AVVideoAllowFrameReorderingKey: true],
    ])
    input.expectsMediaDataInRealTime = false
    let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
        kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA, kCVPixelBufferWidthKey as String: w,
        kCVPixelBufferHeightKey as String: h, kCVPixelBufferCGBitmapContextCompatibilityKey as String: true,
    ])
    writer.add(input)
    guard writer.startWriting() else { throw writer.error ?? NSError(domain: "film", code: 1) }
    writer.startSession(atSourceTime: .zero)
    let total = Int(length) * fps
    let batch = ProcessInfo.processInfo.activeProcessorCount
    var index = 0
    while index < total {
        let n = min(batch, total - index)
        var buffers: [CVPixelBuffer] = []
        for _ in 0..<n {
            var b: CVPixelBuffer?
            CVPixelBufferPoolCreatePixelBuffer(nil, adaptor.pixelBufferPool!, &b)
            buffers.append(b!)
        }
        let first = index
        DispatchQueue.concurrentPerform(iterations: n) { k in
            let buffer = buffers[k]
            CVPixelBufferLockBaseAddress(buffer, [])
            let ctx = CGContext(data: CVPixelBufferGetBaseAddress(buffer), width: w, height: h, bitsPerComponent: 8,
                                bytesPerRow: CVPixelBufferGetBytesPerRow(buffer), space: space,
                                bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue)!
            film.render(Double(first + k) / Double(fps), into: ctx)
            CVPixelBufferUnlockBaseAddress(buffer, [])
        }
        for (k, buffer) in buffers.enumerated() {
            while !input.isReadyForMoreMediaData { usleep(5000) }
            adaptor.append(buffer, withPresentationTime: CMTime(value: Int64(first + k), timescale: Int32(fps)))
        }
        index += n
        FileHandle.standardError.write("\r\(index)/\(total)".data(using: .utf8)!)
    }
    input.markAsFinished()
    writer.endSession(atSourceTime: CMTime(value: Int64(total), timescale: Int32(fps)))
    let done = DispatchSemaphore(value: 0)
    writer.finishWriting { done.signal() }
    done.wait()
    FileHandle.standardError.write("\n".data(using: .utf8)!)
    if writer.status != .completed { throw writer.error ?? NSError(domain: "film", code: 2) }
}

let args = CommandLine.arguments
switch args.count > 3 ? args[1] : "" {
case "video":
    let megabits = args.count > 4 ? Double(args[4]) ?? 18 : 18
    try writeVideo(Film(scale: 2), args[3], bitrate: Int(megabits * 1_000_000))
case "still":
    let film = Film(scale: args.count > 5 ? Double(args[5]) ?? 1 : 1)
    writePNG(film.image(at: Double(args[3])!), args[4])
case "sheet":
    let film = Film(scale: upright ? 0.25 : 0.4)
    let moments = [0.1, 0.5, 0.9, 1.3, 1.7, 2.0, 2.4, 2.9]
    let (w, h) = (film.width, film.height)
    let columns = upright ? 4 : 2
    let rows = moments.count / columns
    let sheet = bitmap(w * columns + 4 * (columns - 1), h * rows + 4 * (rows - 1))
    sheet.setFillColor(RGB(1, 1, 1).cg())
    sheet.fill(CGRect(x: 0, y: 0, width: sheet.width, height: sheet.height))
    var images = [CGImage?](repeating: nil, count: moments.count)
    let lock = NSLock()
    DispatchQueue.concurrentPerform(iterations: moments.count) { i in
        let im = film.image(at: moments[i])
        lock.lock(); images[i] = im; lock.unlock()
    }
    for (i, im) in images.enumerated() {
        sheet.draw(im!, in: CGRect(x: (i % columns) * (w + 4), y: (rows - 1 - i / columns) * (h + 4), width: w, height: h))
    }
    writePNG(sheet.makeImage()!, args[3])
default:
    print("usage: render video <landscape|portrait> out.mp4 [megabits] | still <layout> <seconds> out.png [scale] | sheet <layout> out.png")
}
