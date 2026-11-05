// Bear Media's loading video (the owner, 2026-11-05): a plush teddy bear sitting on a
// grassy hill at night, watching a moon that carries the app's play-button mark (two
// orbs, dark brown and orange, chasing each other round a play button) cross the sky from
// the right corner to the left, while the trees behind slide past. Drawn entirely in code
// (the bear ray-marched in 3D, the rest with Core Graphics), frame by frame, and written
// as an HEVC video: no picture from anywhere else. scripts/make_loading_video.sh runs it.
//
//   swiftc -O -o render scripts/loading_video/render.swift
//   ./render video landscape out.mp4          3840×2160, 3 seconds at 30 frames a second
//   ./render video portrait out.mp4           2160×3840, for a screen held upright
//   ./render still landscape 1.5 out.png [s]  one moment (s: pixels per point, 2 = 4K)
//   ./render sheet landscape out.png          eight moments side by side, small
//
// Everything that moves repeats exactly every three seconds, so the video loops with no
// seam: the moon fades in at the right and out at the left, and the bear, having followed
// it across, looks back to the right as it goes.

import AVFoundation
import Accelerate
import CoreGraphics
import CoreVideo
import Foundation
import ImageIO

let loop = 3.0
let fps = 30
let tau = Double.pi * 2
let space = CGColorSpace(name: CGColorSpace.sRGB)!

// MARK: - Maths

struct P {
    var x: Double
    var y: Double
    init(_ x: Double, _ y: Double) {
        self.x = x
        self.y = y
    }
    static func + (a: P, b: P) -> P { P(a.x + b.x, a.y + b.y) }
    static func - (a: P, b: P) -> P { P(a.x - b.x, a.y - b.y) }
    static func * (a: P, k: Double) -> P { P(a.x * k, a.y * k) }
    var length: Double { (x * x + y * y).squareRoot() }
    var unit: P {
        let l = length
        return l > 1e-9 ? P(x / l, y / l) : P(0, 1)
    }
    var perp: P { P(-y, x) }
    func rotated(_ a: Double) -> P {
        let c = cos(a)
        let s = sin(a)
        return P(x * c - y * s, x * s + y * c)
    }
    var cg: CGPoint { CGPoint(x: x, y: y) }
}

func clamp01(_ x: Double) -> Double { min(max(x, 0), 1) }
func mix(_ a: Double, _ b: Double, _ t: Double) -> Double { a + (b - a) * t }
func smooth(_ a: Double, _ b: Double, _ x: Double) -> Double {
    let t = clamp01((x - a) / (b - a))
    return t * t * (3 - 2 * t)
}
/// A wave that goes round a whole number of times in the loop, so it meets itself.
func wave(_ t: Double, _ times: Double, _ phase: Double = 0) -> Double {
    sin(tau * t / loop * times + phase)
}
/// 0 → 1 → 0 over [start, start + length], eased: a movement that happens once.
func bump(_ t: Double, _ start: Double, _ length: Double) -> Double {
    let s = (t - start) / length
    if s <= 0 || s >= 1 { return 0 }
    let v = sin(Double.pi * s)
    return v * v
}

/// The same numbers every run, so every frame draws the same hairs and blades.
struct RNG {
    var state: UInt64
    init(_ seed: UInt64) { state = seed &* 0x9E37_79B9_7F4A_7C15 &+ 0x1234_5678 }
    mutating func next() -> UInt64 {
        state &+= 0x9E37_79B9_7F4A_7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
        z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
        return z ^ (z >> 31)
    }
    mutating func unit() -> Double { Double(next() >> 11) / Double(UInt64(1) << 53) }
    mutating func range(_ a: Double, _ b: Double) -> Double { a + (b - a) * unit() }
    mutating func chance(_ p: Double) -> Bool { unit() < p }
    mutating func gauss() -> Double {
        let u = max(unit(), 1e-12)
        return (-2 * log(u)).squareRoot() * cos(tau * unit())
    }
}

struct RGB {
    var r: Double
    var g: Double
    var b: Double
    init(_ r: Double, _ g: Double, _ b: Double) {
        self.r = r
        self.g = g
        self.b = b
    }
    init(hex: UInt32) {
        r = Double((hex >> 16) & 0xFF) / 255
        g = Double((hex >> 8) & 0xFF) / 255
        b = Double(hex & 0xFF) / 255
    }
    static func * (a: RGB, k: Double) -> RGB { RGB(a.r * k, a.g * k, a.b * k) }
    static func * (a: RGB, c: RGB) -> RGB { RGB(a.r * c.r, a.g * c.g, a.b * c.b) }
    static func + (a: RGB, c: RGB) -> RGB { RGB(a.r + c.r, a.g + c.g, a.b + c.b) }
    func mixed(_ o: RGB, _ t: Double) -> RGB { RGB(mix(r, o.r, t), mix(g, o.g, t), mix(b, o.b, t)) }
    func cg(_ a: Double = 1) -> CGColor {
        CGColor(colorSpace: space, components: [CGFloat(clamp01(r)), CGFloat(clamp01(g)), CGFloat(clamp01(b)), CGFloat(a)])!
    }
}

func gradient(_ stops: [(Double, RGB, Double)]) -> CGGradient {
    CGGradient(
        colorsSpace: space, colors: stops.map { $0.1.cg($0.2) } as CFArray,
        locations: stops.map { CGFloat($0.0) })!
}

func fill(_ ctx: CGContext, _ c: RGB, _ a: Double = 1) {
    ctx.setFillColor(red: CGFloat(clamp01(c.r)), green: CGFloat(clamp01(c.g)), blue: CGFloat(clamp01(c.b)), alpha: CGFloat(a))
}

/// A soft round glow or shadow, fading to nothing at its edge.
func softEllipse(_ ctx: CGContext, _ c: P, _ rx: Double, _ ry: Double, _ color: RGB, _ alpha: Double, core: Double = 0.0) {
    ctx.saveGState()
    ctx.translateBy(x: c.x, y: c.y)
    ctx.scaleBy(x: rx, y: ry)
    let g = gradient([(0, color, alpha), (max(core, 0.001), color, alpha), (1, color, 0)])
    ctx.drawRadialGradient(g, startCenter: .zero, startRadius: 0, endCenter: .zero, endRadius: 1, options: [])
    ctx.restoreGState()
}

/// A smooth line through points, by x (the hill's crest).
struct Curve {
    let points: [P]
    func y(_ x: Double) -> Double {
        let p = points
        if x <= p[0].x { return p[0].y }
        if x >= p[p.count - 1].x { return p[p.count - 1].y }
        var i = 0
        while p[i + 1].x < x { i += 1 }
        let a = p[i]
        let b = p[i + 1]
        let pa = i > 0 ? p[i - 1] : a
        let pb = i + 2 < p.count ? p[i + 2] : b
        let m0 = (b.y - pa.y) / max(1e-9, b.x - pa.x)
        let m1 = (pb.y - a.y) / max(1e-9, pb.x - a.x)
        let h = b.x - a.x
        let s = (x - a.x) / h
        let s2 = s * s
        let s3 = s2 * s
        return (2 * s3 - 3 * s2 + 1) * a.y + (s3 - 2 * s2 + s) * h * m0 + (-2 * s3 + 3 * s2) * b.y + (s3 - s2) * h * m1
    }
}

// MARK: - The two shapes of screen

/// Where everything goes, in points (a 1920×1080 or 1080×1920 picture, y up).
struct Layout {
    let name: String
    let width: Double
    let height: Double
    let bear: P
    let bearScale: Double
    let moonFrom: P
    let moonTo: P
    let moonArc: Double
    let moonRadius: Double
    let crest: Curve
    let horizon: Double
    let starCount: Int

    static let landscape = Layout(
        name: "landscape", width: 1920, height: 1080, bear: P(560, 118), bearScale: 1.0,
        moonFrom: P(1590, 836), moonTo: P(330, 836), moonArc: 46, moonRadius: 112,
        crest: Curve(points: [
            P(-60, 316), P(0, 318), P(300, 346), P(580, 354), P(880, 322), P(1200, 268), P(1560, 232), P(1920, 214),
            P(1990, 212),
        ]),
        horizon: 300, starCount: 2600)

    static let portrait = Layout(
        name: "portrait", width: 1080, height: 1920, bear: P(540, 350), bearScale: 1.3,
        moonFrom: P(800, 1440), moonTo: P(280, 1440), moonArc: 44, moonRadius: 128,
        crest: Curve(points: [
            P(-60, 610), P(0, 614), P(280, 662), P(540, 680), P(800, 660), P(1080, 612), P(1140, 608),
        ]),
        horizon: 610, starCount: 2600)

    static func named(_ name: String) -> Layout { name == "portrait" ? portrait : landscape }
}

// MARK: - Moments

/// Where the moon is and how much of it shows.
func moonAt(_ t: Double, _ L: Layout) -> (pos: P, alpha: Double) {
    let u = t / loop
    let p = 0.8 * u + 0.2 * (u * u * (3 - 2 * u))
    let x = mix(L.moonFrom.x, L.moonTo.x, p)
    let y = mix(L.moonFrom.y, L.moonTo.y, p) + L.moonArc * sin(Double.pi * p)
    let alpha = smooth(0, 0.32, t) * (1 - smooth(2.62, 2.985, t))
    return (P(x, y), alpha)
}

/// The bear at one moment.
struct Pose {
    var breath = 0.0
    /// The whole bear turned a little towards the right, where the moon rises.
    var bodyYaw = 0.24
    /// The head's turn from the body (radians; + is towards the right), its lift and tilt.
    var yaw = 0.0
    var pitch = 0.2
    var roll = 0.0
    var blink = 0.0
    /// Where the eyes look, on the screen.
    var gaze = P(1, 1)
    var earFlick = 0.0
    var pawLift = 0.0
    var footRock = 0.0
    var toeCurl = 0.0
    var moon = P(0, 0)
    var moonAlpha = 0.0
}

/// The middle of the head, in the bear's own units above where it sits.
let headCentre = P(0, 372)

func headWorld(_ L: Layout) -> P { L.bear + headCentre * L.bearScale }

/// Which way to face to look at a point on the screen (radians; + is towards the right).
func facing(_ target: P, _ L: Layout) -> Double {
    let h = headWorld(L)
    let dx = target.x - h.x
    let dy = max(60, target.y - h.y)
    return max(-0.8, min(0.8, dx / (abs(dx) + dy) * 1.15)) * 0.66
}

func poseAt(_ t: Double, _ L: Layout) -> Pose {
    var pose = Pose()
    let m = moonAt(t, L)
    pose.moon = m.pos
    pose.moonAlpha = m.alpha
    pose.breath = wave(t, 1, -0.6)
    pose.bodyYaw = L.name == "portrait" ? 0.14 : 0.24
    // The head follows the moon a little behind it, then turns back to the right for
    // the moon that's coming (blinking as it turns, as animals do).
    let follow = facing(moonAt(max(0, t - 0.22), L).pos, L)
    let start = facing(L.moonFrom, L)
    let back = smooth(2.42, 2.96, t)
    pose.yaw = max(-0.8, min(0.6, mix(follow, start, back) - pose.bodyYaw))
    let h = headWorld(L)
    let up = (m.pos.y - h.y) / ((m.pos - h).length + 1)
    pose.pitch = 0.14 + 0.2 * up
    pose.roll = -pose.yaw * 0.16 + wave(t, 1, 0.9) * 0.015
    // The eyes lead: they go back to the right a moment before the head does.
    let lookBack = smooth(2.3, 2.55, t)
    let target = m.pos * (1 - lookBack) + L.moonFrom * lookBack
    pose.gaze = (target - h).unit
    pose.blink = blinkAt(t, 1.05) + blinkAt(t, 2.5)
    pose.earFlick = bump(t, 1.72, 0.32)
    pose.pawLift = bump(t, 0.55, 0.95)
    pose.footRock = wave(t, 1, 1.3)
    pose.toeCurl = bump(t, 1.9, 0.7)
    return pose
}

func blinkAt(_ t: Double, _ start: Double) -> Double {
    let s = t - start
    if s < 0 || s > 0.24 { return 0 }
    if s < 0.07 { return smooth(0, 0.07, s) }
    if s < 0.11 { return 1 }
    return 1 - smooth(0.11, 0.24, s)
}

/// Wind: waves that travel along the hill, each going round a whole number of times.
func wind(_ t: Double, _ x: Double) -> Double {
    0.62 * wave(t, 2, -x / 230) + 0.28 * wave(t, 3, -x / 101 + 1.7) + 0.10 * wave(t, 5, x / 57 + 0.4)
}

// MARK: - The bear: one soft figure in three dimensions

// The bear is a solid made of rounded shapes melted together (a head with chubby cheeks
// and a muzzle, a pear of a body, legs, arms, paws and ears), each pixel found by
// following a ray into it, then lit: a warm light from the front, the sky's blue, the
// moon behind as a rim of cold light, soft shadows and the dark where parts meet. Fur is
// a fine streaked texture laid along the way it grows, and hairs standing out all round
// the outline. Units are the bear's own: it sits at 0, its ears reach about 490; x is to
// the right, y up and z towards us.

typealias V3 = SIMD3<Float>

@inline(__always) func dot3(_ a: V3, _ b: V3) -> Float { (a * b).sum() }
@inline(__always) func len3(_ a: V3) -> Float { dot3(a, a).squareRoot() }
@inline(__always) func norm3(_ a: V3) -> V3 {
    let l = len3(a)
    return l > 1e-8 ? a / l : V3(0, 0, 1)
}
@inline(__always) func clampF(_ x: Float, _ a: Float = 0, _ b: Float = 1) -> Float { min(max(x, a), b) }
@inline(__always) func smoothF(_ a: Float, _ b: Float, _ x: Float) -> Float {
    let t = clampF((x - a) / (b - a))
    return t * t * (3 - 2 * t)
}
@inline(__always) func sdEllipsoid(_ p: V3, _ r: V3) -> Float {
    let k0 = len3(p / r)
    let k1 = len3(p / (r * r))
    return k0 * (k0 - 1) / max(k1, 1e-6)
}
@inline(__always) func sdBox(_ p: V3, _ b: V3) -> Float {
    let q = abs(p) - b
    return len3(simd_max(q, V3(0, 0, 0))) + min(max(q.x, max(q.y, q.z)), 0)
}
@inline(__always) func sdTaper(_ p: V3, _ a: V3, _ b: V3, _ ra: Float, _ rb: Float) -> Float {
    let pa = p - a
    let ba = b - a
    let h = clampF(dot3(pa, ba) / dot3(ba, ba))
    return len3(pa - ba * h) - (ra + (rb - ra) * h)
}
@inline(__always) func smin(_ a: Float, _ b: Float, _ k: Float) -> Float {
    let h = max(k - abs(a - b), 0) / k
    return min(a, b) - h * h * k * 0.25
}
@inline(__always) func smax(_ a: Float, _ b: Float, _ k: Float) -> Float { -smin(-a, -b, k) }

/// A turn in three dimensions, kept as its three columns.
struct M3 {
    var c0: V3
    var c1: V3
    var c2: V3
    static let identity = M3(c0: V3(1, 0, 0), c1: V3(0, 1, 0), c2: V3(0, 0, 1))
    @inline(__always) func apply(_ v: V3) -> V3 { c0 * v.x + c1 * v.y + c2 * v.z }
    var transposed: M3 { M3(c0: V3(c0.x, c1.x, c2.x), c1: V3(c0.y, c1.y, c2.y), c2: V3(c0.z, c1.z, c2.z)) }
    static func * (a: M3, b: M3) -> M3 { M3(c0: a.apply(b.c0), c1: a.apply(b.c1), c2: a.apply(b.c2)) }
    static func rotX(_ a: Float) -> M3 { M3(c0: V3(1, 0, 0), c1: V3(0, cos(a), sin(a)), c2: V3(0, -sin(a), cos(a))) }
    static func rotY(_ a: Float) -> M3 { M3(c0: V3(cos(a), 0, -sin(a)), c1: V3(0, 1, 0), c2: V3(sin(a), 0, cos(a))) }
    static func rotZ(_ a: Float) -> M3 { M3(c0: V3(cos(a), sin(a), 0), c1: V3(-sin(a), cos(a), 0), c2: V3(0, 0, 1)) }
}

@inline(__always) func toLinear(_ c: Float) -> Float { c <= 0.04045 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4) }
@inline(__always) func toScreen(_ c: Float) -> Float {
    var x = max(c, 0)
    if x > 0.82 { x = 0.82 + 0.18 * (1 - exp(-(x - 0.82) / 0.18)) }  // a soft shoulder for highlights
    return x <= 0.0031308 ? x * 12.92 : 1.055 * pow(x, 1 / 2.4) - 0.055
}
func lin(_ r: Float, _ g: Float, _ b: Float) -> V3 { V3(toLinear(r), toLinear(g), toLinear(b)) }

@inline(__always) func hash2(_ x: Int32, _ y: Int32) -> Float {
    var h = UInt32(bitPattern: x &* 374_761_393 &+ y &* 668_265_263)
    h = (h ^ (h >> 13)) &* 1_274_126_177
    h ^= h >> 16
    return Float(h & 0xFFFFFF) / Float(0xFFFFFF)
}

@inline(__always) func vnoise(_ x: Float, _ y: Float) -> Float {
    let xf = x.rounded(.down)
    let yf = y.rounded(.down)
    let ix = Int32(xf)
    let iy = Int32(yf)
    let fx = x - xf
    let fy = y - yf
    let ux = fx * fx * (3 - 2 * fx)
    let uy = fy * fy * (3 - 2 * fy)
    let a = hash2(ix, iy)
    let b = hash2(ix + 1, iy)
    let c = hash2(ix, iy + 1)
    let d = hash2(ix + 1, iy + 1)
    let top = a + (b - a) * ux
    let bottom = c + (d - c) * ux
    return top + (bottom - top) * uy
}

// The shapes (right side; the left is the mirror): a plush teddy bear (the owner,
// 2026-11-05: "use a bear like ted"), a teddy of that kind, not that film's own bear.
// A big round head with a pale snout of its own fabric, small shiny bead eyes, soft
// brow ridges, cupped round ears; a pear of a body, stubby arms jointed at the shoulder,
// legs out in front with felt pads on the soles.
let neckPivot = V3(0, 300, 10)
let bellyC = V3(0, 132, 0)
let bellyR = V3(128, 132, 104)
let chestC = V3(0, 242, -6)
let chestR = V3(96, 90, 80)
let hipC = V3(66, 62, 34)
/// Where the leg's tube ends, inside the foot, so the sole's pad stands clear of it.
let ankleC = V3(114, 60, 118)
let footC = V3(118, 60, 146)
let footR = V3(47, 55, 33)
let soleC = V3(0, -2, 31)
let soleR = V3(35, 42, 6.5)
let armA = V3(94, 262, 22)
let armB = V3(132, 150, 70)
let headC = V3(0, 378, 10)
let headR = V3(122, 112, 104)
let cheekC = V3(56, 346, 30)
let cheekR = V3(60, 54, 56)
let muzzleC = V3(0, 342, 90)
let muzzleR = V3(58, 44, 37)
let noseC = V3(0, 366, 124)
let noseR = V3(19, 12.5, 9)
let browC = V3(50, 416, 92)
let browR = V3(26, 8, 11)
let earBase = V3(82, 442, 0)
let earC = V3(94, 460, 0)
let earR = V3(40, 40, 18)
let carveC = V3(94, 458, 14)
let carveR = V3(26, 26, 12)
let eyeC = V3(50, 395, 98)
let eyeRadius: Float = 11

// The colours, as light (not as the screen shows them).
let furColour = lin(0.74, 0.47, 0.25)
let muzzleColour = lin(0.84, 0.67, 0.57)
let feltColour = lin(0.76, 0.61, 0.51)
let noseColour = lin(0.06, 0.05, 0.05)
let beadColour = lin(0.05, 0.03, 0.025)
let beadRim = lin(0.22, 0.11, 0.05)
let keyColour = V3(1.0, 0.87, 0.72) * 1.45
let skyColour = V3(0.10, 0.15, 0.33) * 1.25
let groundColour = V3(0.035, 0.05, 0.028)
let moonColour = V3(0.55, 0.68, 1.0) * 1.4
let keyDir = norm3(V3(-0.45, 0.6, 0.66))

/// What the surface is at a point, and how it's coloured.
struct Surface {
    var albedo: V3
    var spec: Float = 0
    var shine: Float = 20
    var eye = false
    var furU: Float = 0
    var furV: Float = 0
    var fur: Float = 1  // how much plush texture shows
    var fine: Float = 1  // shorter pile (the snout, the felt)
    var mouth: Float = 0  // how much of the stitched mouth is here
}

/// The bear's shapes placed for one moment.
struct BearRig {
    let bodyInv: M3
    let headInv: M3
    let breathX: Float
    let breathY: Float
    let lift: Float
    let footInvL: M3
    let footInvR: M3
    let earInvR: M3
    let lidY: Float
    let moonDir: V3
    let moonStrength: Float
    let sway: Float

    init(pose: Pose, t: Double) {
        bodyInv = M3.rotY(Float(pose.bodyYaw)).transposed
        let head = M3.rotY(Float(pose.yaw)) * M3.rotX(Float(-pose.pitch)) * M3.rotZ(Float(pose.roll))
        headInv = head.transposed
        let b = Float(pose.breath)
        breathX = 1 + 0.006 * b
        breathY = 1 + 0.014 * b
        lift = Float(pose.pawLift)
        // The feet lean out a little; the right one rocks, the left one wiggles once.
        let rock = Float(pose.footRock)
        footInvR = M3.rotZ(0.2 + rock * 0.08)
        footInvL = M3.rotZ(0.2 + Float(pose.toeCurl) * 0.12)
        earInvR = M3.rotZ(Float(pose.earFlick) * 0.3)
        // The lids sit just above the beads; a blink brings them down over them.
        lidY = eyeRadius * (1.25 - 2.5 * Float(min(1, pose.blink)))
        moonDir = norm3(V3(Float(pose.gaze.x), Float(pose.gaze.y), -0.4))
        moonStrength = Float(pose.moonAlpha)
        sway = Float(wind(t, 0))
    }

    /// How far a point is from the bear (less than 0: inside).
    func dist(_ pw: V3) -> Float {
        let p = bodyInv.apply(pw)
        let left = p.x < 0
        let q = V3(abs(p.x), p.y, p.z)
        let pt = V3(p.x / breathX, p.y / breathY, p.z / breathX)
        var d = smin(sdEllipsoid(pt - bellyC, bellyR), sdEllipsoid(pt - chestC, chestR), 50)
        if sdBox(q - V3(90, 150, 80), V3(105, 170, 130)) < d + 30 {
            let leg = sdTaper(q, hipC, ankleC, 50, 44)
            let f = (left ? footInvL : footInvR).apply(q - footC)
            let foot = smin(sdEllipsoid(f, footR), sdEllipsoid(f - soleC, soleR), 2)
            d = smin(d, smin(leg, foot, 14), 18)
            let up = left ? lift : 0
            d = smin(d, sdTaper(q, armA, armB + V3(0, 10 * up, 6 * up), 41, 35), 10)
        }
        let h = headInv.apply(p - neckPivot) + neckPivot
        if sdBox(h - V3(0, 368, 45), V3(155, 150, 102)) < d + 12 {
            let hq = V3(abs(h.x), h.y, h.z)
            var dh = smin(sdEllipsoid(h - headC, headR), sdEllipsoid(hq - cheekC, cheekR), 30)
            dh = smin(dh, sdEllipsoid(hq - browC, browR), 8)
            dh = smin(dh, sdEllipsoid(h - muzzleC, muzzleR), 12)
            dh = smin(dh, sdEllipsoid(h - noseC, noseR), 2.5)
            let e = (h.x >= 0 ? earInvR : M3.identity).apply(hq - earBase) + earBase
            dh = smin(dh, smax(sdEllipsoid(e - earC, earR), -sdEllipsoid(e - carveC, carveR), 4), 12)
            let ev = hq - eyeC
            let r = len3(ev)
            dh = min(dh, min(r - eyeRadius, max(r - eyeRadius - 1.6, lidY - ev.y)))
            d = smin(d, dh, 10)
        }
        return d
    }

    func normal(_ p: V3) -> V3 {
        let e: Float = 0.45
        let a = V3(1, -1, -1)
        let b = V3(-1, -1, 1)
        let c = V3(-1, 1, -1)
        let d = V3(1, 1, 1)
        return norm3(a * dist(p + a * e) + b * dist(p + b * e) + c * dist(p + c * e) + d * dist(p + d * e))
    }

    /// What's at a point on (or just off) the surface.
    func surface(_ pw: V3) -> Surface {
        let p = bodyInv.apply(pw)
        let left = p.x < 0
        let q = V3(abs(p.x), p.y, p.z)
        let pt = V3(p.x / breathX, p.y / breathY, p.z / breathX)
        var body = smin(sdEllipsoid(pt - bellyC, bellyR), sdEllipsoid(pt - chestC, chestR), 50)
        let f = (left ? footInvL : footInvR).apply(q - footC)
        let footFur = smin(sdTaper(q, hipC, ankleC, 50, 44), sdEllipsoid(f, footR), 14)
        let sole = sdEllipsoid(f - soleC, soleR)
        body = smin(body, footFur, 18)
        let up = left ? lift : 0
        body = smin(body, sdTaper(q, armA, armB + V3(0, 10 * up, 6 * up), 41, 35), 10)

        let h = headInv.apply(p - neckPivot) + neckPivot
        let hq = V3(abs(h.x), h.y, h.z)
        let skull = smin(smin(sdEllipsoid(h - headC, headR), sdEllipsoid(hq - cheekC, cheekR), 30), sdEllipsoid(hq - browC, browR), 8)
        let muzzle = sdEllipsoid(h - muzzleC, muzzleR)
        let nose = sdEllipsoid(h - noseC, noseR)
        let e = (h.x >= 0 ? earInvR : M3.identity).apply(hq - earBase) + earBase
        let ear = smax(sdEllipsoid(e - earC, earR), -sdEllipsoid(e - carveC, carveR), 4)
        let ev = hq - eyeC
        let r = len3(ev)
        let eye = r - eyeRadius
        let lid = max(r - eyeRadius - 1.6, lidY - ev.y)
        let headFur = min(smin(smin(skull, muzzle, 12), ear, 12), lid)

        var s = Surface(albedo: furColour)
        let nearest = min(min(body, headFur), min(min(sole, nose), eye))
        if eye <= nearest + 0.01 && eye < lid {
            // A bead eye: nearly black, a little brown where it turns away.
            s.eye = true
            let local = norm3(ev)
            s.albedo = beadColour + (beadRim - beadColour) * (1 - max(0, local.z)) * 0.6
            s.spec = 1.6
            s.shine = 220
            s.fur = 0
            return s
        }
        if nose <= nearest + 0.6 && nose < headFur - 0.5 {
            s.albedo = noseColour
            s.spec = 1.0
            s.shine = 60
            s.fur = 0
            return s
        }
        if sole <= nearest + 0.6 && sole < footFur {
            s.albedo = feltColour
            s.fine = 2.6
            s.fur = 0.6
            s.furU = f.y
            s.furV = f.x
            return s
        }
        if headFur < body {
            let m = smoothF(3, -3, muzzle - skull)
            s.albedo = furColour + (muzzleColour - furColour) * m
            s.fine = 1 + 1.6 * m
            s.furU = h.y
            s.furV = h.x + h.z * 0.6
            if ear < skull {
                // The cup of the ear: the same plush, a touch darker.
                let inside = smoothF(2.5, -1.5, sdEllipsoid(e - carveC, carveR + V3(2.5, 2.5, 2.5)))
                s.albedo = s.albedo * (1 - 0.15 * inside)
            }
            if m > 0.3 && h.z > 108 {
                s.mouth = mouthLine(h) * smoothF(108, 120, h.z)
            }
            return s
        }
        s.furU = p.y
        s.furV = p.x + p.z * 0.6
        return s
    }

    /// 1 on the stitched mouth, fading to 0 just off it (in the head's x and y): a seam
    /// down from the nose, and a small smile.
    func mouthLine(_ h: V3) -> Float {
        let x = abs(h.x)
        var d: Float = 99
        if h.y < 356 && h.y > 337 { d = min(d, x) }
        var prev = V3(0, 337, 0)
        for i in 1...6 {
            let s = Float(i) / 6
            let next = V3(19 * s, 337 - 8 * sin(Float.pi * s * 0.82) + 3.5 * s, 0)
            let ab = next - prev
            let ap = V3(x, h.y, 0) - prev
            let k = clampF(dot3(ap, ab) / dot3(ab, ab))
            d = min(d, len3(ap - ab * k))
            prev = next
        }
        return 1 - smoothF(0.9, 2.0, d)
    }

    func shadow(_ ro: V3) -> Float {
        var res: Float = 1
        var t: Float = 2.5
        for _ in 0..<24 {
            let h = dist(ro + keyDir * t)
            res = min(res, 7 * h / t)
            if res < 0.02 { return 0 }
            t += min(max(h, 2.5), 30)
            if t > 280 { break }
        }
        let r = clampF(res)
        return r * r * (3 - 2 * r)
    }

    func occlusion(_ p: V3, _ n: V3) -> Float {
        var occ: Float = 0
        var w: Float = 1
        for h: Float in [4, 10, 19, 32, 50] {
            occ += (h - dist(p + n * h)) * w
            w *= 0.62
        }
        let a = clampF(1 - occ * 0.0105, 0.15, 1)
        return a * (0.5 + 0.5 * smoothF(-6, 75, p.y))
    }

    /// The light on a point of the surface, as light (not yet as the screen shows it).
    func light(_ p: V3, _ n: V3, _ s: Surface, withShadow: Bool = true) -> V3 {
        var albedo = s.albedo
        if s.fur > 0 {
            // Plush: a fine, dense pile, in soft clumps.
            let scale = 1.6 / s.fine
            let n1 = vnoise(s.furV / scale, s.furU / (scale * 1.8))
            let n2 = vnoise(s.furV / (scale * 0.5) + 17, s.furU / (scale * 0.8) + 31)
            let n3 = vnoise(s.furV / 7 + 5, s.furU / 7 + 9)
            let pile = 0.4 * n1 + 0.25 * n2 + 0.35 * n3
            let amount = 0.55 * s.fur / s.fine.squareRoot()
            albedo = albedo * (1 - amount / 2 + amount * pile)
        }
        if s.mouth > 0 { albedo = albedo * (1 - 0.86 * s.mouth) }
        let ndl = dot3(n, keyDir)
        let diffuse = max(0, (ndl + 0.22) / 1.22)
        let sh = withShadow && ndl > -0.22 ? shadow(p + n * 1.5) : 1
        let ao = occlusion(p, n)
        let sky = groundColour + (skyColour - groundColour) * (n.y * 0.5 + 0.5)
        var c = albedo * (keyColour * (diffuse * (0.15 + 0.85 * sh)) + sky * ao) * (0.35 + 0.65 * ao)
        let ndm = dot3(n, moonDir)
        let facing = max(0, n.z)
        let fresnel = (1 - facing) * (1 - facing) * (1 - facing)
        c += albedo * moonColour * (max(0, ndm) * 0.9 * moonStrength)
        if !s.eye {
            // Moonlight caught at the edge of the fur, and the warm light's glow there too.
            c += moonColour * (fresnel * max(0, (ndm + 0.35) / 1.35) * 0.5 * moonStrength) * (0.35 + albedo * 2)
            c += keyColour * albedo * (fresnel * 0.7 * diffuse)  // plush's soft sheen
        }
        if s.spec > 0 {
            let view = V3(0, 0, 1)
            let half = norm3(keyDir + view)
            c += keyColour * (s.spec * pow(max(0, dot3(n, half)), s.shine) * (0.3 + 0.7 * sh) * 0.5)
            let halfMoon = norm3(moonDir + view)
            c += moonColour * (s.spec * pow(max(0, dot3(n, halfMoon)), s.shine) * moonStrength * 0.6)
        }
        if s.eye {
            // The bright catch-lights that make an eye look wet and alive.
            let r = n * (2 * n.z) - V3(0, 0, 1)
            let key = smoothF(0.965, 0.985, dot3(r, keyDir))
            let fill = smoothF(0.988, 0.996, dot3(r, norm3(V3(0.55, -0.35, 0.76))))
            let moon = smoothF(0.975, 0.99, dot3(r, moonDir)) * moonStrength
            c += V3(1, 0.98, 0.95) * (key * 3.2 + fill * 1.1) + moonColour * (moon * 1.4)
        }
        return c
    }

    /// Hairs standing out from the outline, at a point `gap` off the surface.
    func hairs(_ q: V3, gap: Float, reach: Float) -> (alpha: Float, colour: V3) {
        let n = normal(q)
        var n2x = n.x
        var n2y = n.y
        let l = (n2x * n2x + n2y * n2y).squareRoot()
        if l < 1e-4 { return (0, V3(0, 0, 0)) }
        n2x /= l
        n2y /= l
        let along = q.x * -n2y + q.y * n2x
        let out = gap / reach
        var a: Float = 0
        for layer in 0..<3 {
            let spacing: Float = [1.4, 1.0, 0.75][layer]
            let bend = sway * out * out * 2.2 + Float(layer) * 0.41
            let v = (along + bend) / spacing
            let i = v.rounded(.down)
            let r = hash2(Int32(i), Int32(layer * 7 + 3))
            let length: Float = 0.3 + 0.7 * r * r
            let width = max(0, 1 - out / length) * 0.85
            let profile = 1 - abs(v - i - 0.5) * 2
            a = max(a, clampF((profile - (1 - width)) / 0.35))
        }
        a = max(a, clampF(1 - out * 4)) * 0.95
        let s = surface(q)
        if s.eye || s.fur == 0 { return (clampF(1 - out * 6), light(q, n, s, withShadow: false)) }
        return (a, light(q, n, s, withShadow: false))
    }
}

final class Bear3D {
    /// The bear drawn for one moment: its picture, and where it goes in the frame (points).
    func render(pose: Pose, layout L: Layout, scale s: Double, t: Double) -> (CGImage, CGRect) {
        let rig = BearRig(pose: pose, t: t)
        let lo = P(-270, -30)
        let hi = P(290, 530)
        let x0 = Int(((L.bear.x + lo.x * L.bearScale) * s).rounded(.down))
        let y0 = Int(((L.bear.y + lo.y * L.bearScale) * s).rounded(.down))
        let x1 = Int(((L.bear.x + hi.x * L.bearScale) * s).rounded(.up))
        let y1 = Int(((L.bear.y + hi.y * L.bearScale) * s).rounded(.up))
        let w = x1 - x0
        let h = y1 - y0
        let ctx = bitmap(w, h)
        ctx.clear(CGRect(x: 0, y: 0, width: w, height: h))
        let rowWords = ctx.bytesPerRow / 4
        let pixels = ctx.data!.assumingMemoryBound(to: UInt32.self)
        let reach: Float = 6.5
        let bearX = Float(L.bear.x)
        let bearY = Float(L.bear.y)
        let unit = Float(1 / (s * L.bearScale))
        let bs = Float(L.bearScale)
        let chunks = 24
        DispatchQueue.concurrentPerform(iterations: chunks) { chunk in
            var row = chunk
            while row < h {
                let py = Float(y1 - 1 - row) + 0.5
                let y = (py / Float(s) - bearY) / bs
                for column in 0..<w {
                    let px = Float(x0 + column) + 0.5
                    let x = (px / Float(s) - bearX) / bs
                    var colour = V3(0, 0, 0)
                    var alpha: Float = 0
                    // Follow the ray in from in front of the bear.
                    var z: Float = 270
                    var hit = false
                    var localMin = Float.greatestFiniteMagnitude
                    var localZ = z
                    var passMin = Float.greatestFiniteMagnitude
                    var passZ: Float = 0
                    for _ in 0..<110 {
                        let d = rig.dist(V3(x, y, z))
                        if d < localMin {
                            localMin = d
                            localZ = z
                        } else if d > localMin + 2 {
                            if localMin < passMin {
                                passMin = localMin
                                passZ = localZ
                            }
                            localMin = Float.greatestFiniteMagnitude
                        }
                        if d < 0.06 {
                            hit = true
                            break
                        }
                        z -= max(d * 0.82, 0.04)
                        if z < -240 { break }
                    }
                    if !hit && localMin < passMin {
                        passMin = localMin
                        passZ = localZ
                    }
                    if hit {
                        let p = V3(x, y, z)
                        let n = rig.normal(p)
                        colour = rig.light(p, n, rig.surface(p))
                        alpha = 1
                        // An edge in front (the chin over the chest): its hairs over this.
                        if passMin < reach && passZ > z + 6 {
                            let fuzz = rig.hairs(V3(x, y, passZ), gap: passMin, reach: reach)
                            colour = colour + (fuzz.colour - colour) * fuzz.alpha
                        }
                    } else if passMin < reach {
                        let fuzz = rig.hairs(V3(x, y, passZ), gap: passMin, reach: reach)
                        colour = fuzz.colour
                        alpha = fuzz.alpha
                    }
                    if alpha > 0 {
                        let a = clampF(alpha)
                        let r = UInt32(toScreen(colour.x) * a * 255 + 0.5)
                        let g = UInt32(toScreen(colour.y) * a * 255 + 0.5)
                        let b = UInt32(toScreen(colour.z) * a * 255 + 0.5)
                        let av = UInt32(a * 255 + 0.5)
                        pixels[row * rowWords + column] = (av << 24) | (min(r, av) << 16) | (min(g, av) << 8) | min(b, av)
                    }
                }
                row += chunks
            }
        }
        _ = unit
        let rect = CGRect(x: Double(x0) / s, y: Double(y0) / s, width: Double(w) / s, height: Double(h) / s)
        return (ctx.makeImage()!, rect)
    }
}

// MARK: - The moon with the mark on it

let brownOrb = RGB(hex: 0x4A3B35)
let orangeOrb = RGB(hex: 0xE8923A)

func drawMoon(_ ctx: CGContext, centre c: P, radius R: Double, t: Double, alpha: Double, pixels: Double) {
    guard alpha > 0.002 else { return }
    ctx.saveGState()
    ctx.setAlpha(CGFloat(alpha))
    // Its glow on the sky.
    ctx.setBlendMode(.screen)
    softEllipse(ctx, c, R * 5.2, R * 5.2, RGB(0.55, 0.62, 0.85), 0.22, core: 0.18)
    softEllipse(ctx, c, R * 2.0, R * 2.0, RGB(1.0, 0.96, 0.86), 0.42, core: 0.48)
    ctx.setBlendMode(.normal)
    ctx.beginTransparencyLayer(auxiliaryInfo: nil)
    // The disc: pale, with faint seas and a darker edge.
    let disc = CGRect(x: c.x - R, y: c.y - R, width: R * 2, height: R * 2)
    ctx.saveGState()
    ctx.addEllipse(in: disc)
    ctx.clip()
    let g = gradient([
        (0, RGB(1.0, 0.995, 0.97), 1), (0.6, RGB(0.96, 0.93, 0.85), 1), (1, RGB(0.86, 0.81, 0.70), 1),
    ])
    ctx.drawRadialGradient(
        g, startCenter: (c + P(-R * 0.28, R * 0.3)).cg, startRadius: 0, endCenter: c.cg, endRadius: R * 1.02,
        options: [.drawsAfterEndLocation])
    let seas: [(Double, Double, Double, Double)] = [
        (-0.42, 0.38, 0.26, 0.10), (0.36, 0.5, 0.2, 0.08), (0.5, -0.32, 0.3, 0.09), (-0.3, -0.52, 0.22, 0.08),
        (-0.62, -0.08, 0.16, 0.07), (0.1, 0.72, 0.14, 0.06), (0.7, 0.12, 0.13, 0.06),
    ]
    for s in seas {
        softEllipse(ctx, c + P(s.0 * R, s.1 * R), s.2 * R, s.2 * R * 0.85, RGB(0.62, 0.57, 0.48), s.3, core: 0.4)
    }
    let limb = gradient([(0, RGB(0.7, 0.62, 0.5), 0), (0.78, RGB(0.7, 0.62, 0.5), 0), (1, RGB(0.66, 0.58, 0.46), 0.42)])
    ctx.drawRadialGradient(limb, startCenter: c.cg, startRadius: 0, endCenter: c.cg, endRadius: R, options: [])
    ctx.restoreGState()

    // The mark: two orbs chasing each other round, each with its trail, and the play button.
    let turn = tau * t / (loop / 2)  // two turns in the loop, anticlockwise
    let span = (150 + 16 * wave(t, 2)) * Double.pi / 180
    let ring = R * 0.66
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: 0), blur: CGFloat(R * 0.07 * pixels), color: RGB(0.2, 0.12, 0.05).cg(0.35))
    ctx.beginTransparencyLayer(auxiliaryInfo: nil)
    for (head, colour, light, dark) in [
        (turn + 150 * Double.pi / 180, brownOrb, RGB(hex: 0x76655C), RGB(hex: 0x2E2420)),
        (turn - 30 * Double.pi / 180, orangeOrb, RGB(hex: 0xFFC27A), RGB(hex: 0xC06A1E)),
    ] {
        drawTrail(ctx, c, ring: ring, head: head, span: span, width: R * 0.155, colour: colour, R: R)
        let o = c + P(cos(head), sin(head)) * ring
        let orb = R * 0.168
        ctx.saveGState()
        ctx.addEllipse(in: CGRect(x: o.x - orb, y: o.y - orb, width: orb * 2, height: orb * 2))
        ctx.clip()
        let og = gradient([(0, light, 1), (0.55, colour, 1), (1, dark, 1)])
        ctx.drawRadialGradient(
            og, startCenter: (o + P(-orb * 0.35, orb * 0.4)).cg, startRadius: 0, endCenter: o.cg, endRadius: orb * 1.05,
            options: [.drawsAfterEndLocation])
        ctx.restoreGState()
        softEllipse(ctx, o + P(-orb * 0.33, orb * 0.38), orb * 0.38, orb * 0.26, RGB(1, 1, 1), 0.55, core: 0.3)
    }
    // The play button, breathing a little.
    let pulse = 1 + 0.035 * wave(t, 2, 0.5)
    let a = R * 0.6 * pulse
    let o = c + P(R * 0.05, 0)
    let tri = CGMutablePath()
    let tip = o + P(a * 0.577, 0)
    let top = o + P(-a * 0.289, a * 0.5)
    let bottom = o + P(-a * 0.289, -a * 0.5)
    let corner = a * 0.12
    tri.move(to: ((top + bottom) * 0.5).cg)
    tri.addArc(tangent1End: top.cg, tangent2End: tip.cg, radius: corner)
    tri.addArc(tangent1End: tip.cg, tangent2End: bottom.cg, radius: corner)
    tri.addArc(tangent1End: bottom.cg, tangent2End: top.cg, radius: corner)
    tri.closeSubpath()
    ctx.saveGState()
    ctx.addPath(tri)
    ctx.clip()
    let pg = gradient([(0, RGB(0.70, 0.70, 0.71), 1), (1, RGB(0.50, 0.50, 0.52), 1)])
    ctx.drawLinearGradient(pg, start: (o + P(0, a * 0.5)).cg, end: (o - P(0, a * 0.5)).cg, options: [])
    ctx.restoreGState()
    ctx.endTransparencyLayer()
    ctx.restoreGState()

    // A thin bright rim.
    ctx.setStrokeColor(RGB(1, 1, 0.97).cg(0.35))
    ctx.setLineWidth(CGFloat(R * 0.02))
    ctx.strokeEllipse(in: disc.insetBy(dx: R * 0.01, dy: R * 0.01))
    ctx.endTransparencyLayer()
    ctx.restoreGState()
}

/// One orb's trail: a tube that thins to nothing behind it, curling a little inward.
func drawTrail(_ ctx: CGContext, _ c: P, ring: Double, head: Double, span: Double, width: Double, colour: RGB, R: Double) {
    let n = 64
    var outer: [P] = []
    var inner: [P] = []
    var middle: [P] = []
    for i in 0...n {
        let s = Double(i) / Double(n)
        let a = head - span * s
        let r = ring * (1 - 0.07 * s)
        let w = width * pow(1 - s, 0.85) + R * 0.008
        let d = P(cos(a), sin(a))
        outer.append(c + d * (r + w / 2))
        inner.append(c + d * (r - w / 2))
        middle.append(c + d * (r + w * 0.16))
    }
    let path = CGMutablePath()
    path.move(to: outer[0].cg)
    for p in outer.dropFirst() { path.addLine(to: p.cg) }
    for p in inner.reversed() { path.addLine(to: p.cg) }
    path.closeSubpath()
    ctx.addPath(path)
    fill(ctx, colour)
    ctx.fillPath()
    // The white sheen along the tube near the orb.
    let sheen = CGMutablePath()
    let m = 22
    for i in 3...m {
        let s = Double(i) / Double(n)
        let a = head - span * s
        let r = ring * (1 - 0.07 * s) + width * 0.18
        let p = c + P(cos(a), sin(a)) * r
        if i == 3 { sheen.move(to: p.cg) } else { sheen.addLine(to: p.cg) }
    }
    ctx.addPath(sheen)
    ctx.setStrokeColor(RGB(1, 1, 1).cg(0.62))
    ctx.setLineWidth(CGFloat(width * 0.16))
    ctx.setLineCap(.round)
    ctx.strokePath()
    _ = middle
}

// MARK: - Stars

struct Star {
    var p: P
    var r: Double
    var alpha: Double
    var times: Double
    var phase: Double
    var depth: Double
    var tint: Int
    var bright: Bool
}

func makeStars(_ L: Layout) -> (faint: [Star], twinkling: [Star]) {
    var rng = RNG(77)
    var faint: [Star] = []
    var twinkling: [Star] = []
    let skyLow = L.horizon + 30
    func randomSkyY(_ power: Double) -> Double { skyLow + (L.height - skyLow) * pow(rng.unit(), power) }
    for _ in 0..<L.starCount {
        faint.append(
            Star(
                p: P(rng.range(0, L.width), randomSkyY(0.75)), r: rng.range(0.35, 0.9), alpha: rng.range(0.16, 0.55),
                times: 0, phase: 0, depth: 0, tint: Int(rng.range(0, 3)), bright: false))
    }
    // A faint band of the Milky Way, thick with tiny stars.
    let a = P(-0.1 * L.width, L.height * (L.name == "portrait" ? 0.62 : 0.5))
    let b = P(1.1 * L.width, L.height * 0.98)
    for _ in 0..<(L.starCount * 3 / 4) {
        let s = rng.unit()
        let along = a + (b - a) * s
        let off = (b - a).unit.perp * (rng.gauss() * 55)
        let p = along + off
        if p.y < skyLow { continue }
        faint.append(
            Star(
                p: p, r: rng.range(0.3, 0.7), alpha: rng.range(0.12, 0.4), times: 0, phase: 0, depth: 0,
                tint: Int(rng.range(0, 3)), bright: false))
    }
    let twinkleCount = Int(Double(L.starCount) * 0.11)
    for i in 0..<twinkleCount {
        let bright = i < 15
        twinkling.append(
            Star(
                p: P(rng.range(20, L.width - 20), randomSkyY(bright ? 0.6 : 0.7)),
                r: bright ? rng.range(1.5, 2.4) : rng.range(0.7, 1.45), alpha: rng.range(0.55, 1.0),
                times: Double(Int(rng.range(1, bright ? 3 : 5))), phase: rng.range(0, tau), depth: rng.range(0.3, 0.75),
                tint: Int(rng.range(0, 3)), bright: bright))
    }
    return (faint, twinkling)
}

let starTints = [RGB(1, 1, 1), RGB(0.82, 0.88, 1.0), RGB(1.0, 0.93, 0.82)]
let starGlows = starTints.map { gradient([(0, $0, 1), (0.22, $0, 0.85), (0.45, $0, 0.22), (1, $0, 0)]) }

func drawStar(_ ctx: CGContext, _ s: Star, alpha: Double, scale: Double) {
    guard alpha > 0.01 else { return }
    ctx.setAlpha(CGFloat(min(1, alpha)))
    let r = s.r * scale * (s.bright ? 4.5 : 3.0)
    ctx.drawRadialGradient(
        starGlows[s.tint], startCenter: s.p.cg, startRadius: 0, endCenter: s.p.cg, endRadius: r, options: [])
    if s.bright {
        // Four soft points of light.
        let tint = starTints[s.tint]
        for (dx, dy, length) in [(1.0, 0.0, 15.0), (0.0, 1.0, 15.0), (0.7, 0.7, 7.0), (-0.7, 0.7, 7.0)] {
            let l = length * s.r * scale * 0.9
            let g = gradient([(0, tint, 0), (0.5, tint, 0.55), (1, tint, 0)])
            ctx.saveGState()
            ctx.setLineWidth(CGFloat(s.r * 0.5))
            ctx.setLineCap(.round)
            let from = s.p - P(dx, dy) * l
            let to = s.p + P(dx, dy) * l
            ctx.move(to: from.cg)
            ctx.addLine(to: to.cg)
            ctx.replacePathWithStrokedPath()
            ctx.clip()
            ctx.drawLinearGradient(g, start: from.cg, end: to.cg, options: [])
            ctx.restoreGState()
        }
    }
    ctx.setAlpha(1)
}

// MARK: - Grass

struct Blade {
    var root: P
    var height: Double
    var width: Double
    var lean: Double
    var flex: Double
    var colour: RGB
}

func drawBlade(_ ctx: CGContext, _ b: Blade, t: Double) {
    let a = b.lean + b.flex * wind(t, b.root.x) * 0.24 * (b.height / 24)
    let d = P(sin(a), cos(a))
    let half = P(sin(a * 0.4), cos(a * 0.4))
    let tip = b.root + d * b.height
    let mid = b.root + half * (b.height * 0.55)
    let n = P(1, 0)
    let w = b.width * 0.5
    fill(ctx, b.colour)
    ctx.move(to: (b.root - n * w).cg)
    ctx.addQuadCurve(to: tip.cg, control: (mid - n * (w * 0.7)).cg)
    ctx.addQuadCurve(to: (b.root + n * w).cg, control: (mid + n * (w * 0.7)).cg)
    ctx.closePath()
    ctx.fillPath()
}

let grassTop = RGB(0.25, 0.56, 0.19)
let grassLit = RGB(0.42, 0.72, 0.30)

func makeGrass(_ L: Layout) -> (crest: [Blade], back: [Blade], front: [Blade]) {
    var rng = RNG(5150)
    var crest: [Blade] = []
    var x = -30.0
    while x < L.width + 30 {
        let tall = rng.chance(0.08)
        var colour = grassTop * rng.range(0.7, 1.15)
        if rng.chance(0.22) { colour = grassLit * rng.range(0.8, 1.1) }
        crest.append(
            Blade(
                root: P(x, L.crest.y(x) - rng.range(0, 10)), height: tall ? rng.range(28, 44) : rng.range(12, 28),
                width: rng.range(2.6, 4.4), lean: rng.range(-0.3, 0.3), flex: rng.range(0.6, 1.25), colour: colour))
        x += rng.range(0.7, 1.9)
    }
    var face: [Blade] = []
    let count = Int(L.width * 7.4)
    let bear = L.bear
    for _ in 0..<count {
        let x = rng.range(-30, L.width + 30)
        let top = L.crest.y(x) - 5
        let y = rng.range(-12, top)
        let d = clamp01(1 - y / top)  // 0 at the crest, 1 at the bottom (nearer to us)
        var height = rng.range(9, 20) * (1 + 1.3 * d)
        let width = rng.range(2.2, 3.7) * (1 + 0.9 * d)
        // Trodden down a little where the bear sits.
        let dx = (x - bear.x) / (240 * L.bearScale)
        let dy = (y - bear.y) / (60 * L.bearScale)
        if dx * dx + dy * dy < 1 { height *= 0.6 }
        let shadeLevel = mix(1.0, 0.5, d) * rng.range(0.68, 1.22)
        var colour = grassTop * shadeLevel
        if rng.chance(0.08) { colour = grassLit * (shadeLevel * 0.9) }
        face.append(
            Blade(root: P(x, y), height: height, width: width, lean: rng.range(-0.35, 0.35), flex: rng.range(0.5, 1.2), colour: colour))
    }
    face.sort { $0.root.y > $1.root.y }
    let line = bear.y + 2 * L.bearScale
    let back = face.filter { $0.root.y >= line }
    let front = face.filter { $0.root.y < line }
    return (crest, back, front)
}

// MARK: - Pictures drawn once

func bitmap(_ width: Int, _ height: Int) -> CGContext {
    CGContext(
        data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0, space: space,
        bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue)!
}

/// The sky, its faint stars and the far mountains, which are too far off to move.
func makeSky(_ L: Layout, _ s: Double, faint: [Star]) -> CGImage {
    let ctx = bitmap(Int(L.width * s), Int(L.height * s))
    ctx.scaleBy(x: s, y: s)
    let h = L.horizon
    let g = gradient([
        (0, RGB(0.17, 0.29, 0.49), 1), (h / L.height, RGB(0.15, 0.27, 0.48), 1),
        (min(0.95, (h + 160) / L.height), RGB(0.09, 0.17, 0.35), 1), (min(0.97, (h + 420) / L.height), RGB(0.045, 0.09, 0.22), 1),
        (1, RGB(0.016, 0.035, 0.11), 1),
    ])
    ctx.drawLinearGradient(g, start: .zero, end: CGPoint(x: 0, y: L.height), options: [])
    // Haze and the Milky Way's glow.
    var rng = RNG(404)
    let a = P(-0.1 * L.width, L.height * (L.name == "portrait" ? 0.62 : 0.5))
    let b = P(1.1 * L.width, L.height * 0.98)
    for _ in 0..<70 {
        let p = a + (b - a) * rng.unit() + (b - a).unit.perp * (rng.gauss() * 45)
        let r = rng.range(50, 140)
        softEllipse(ctx, p, r, r * 0.6, RGB(0.55, 0.62, 0.85), rng.range(0.012, 0.03))
    }
    softEllipse(ctx, P(L.width * 0.5, h + 30), L.width * 0.9, 190, RGB(0.32, 0.45, 0.70), 0.22)
    for star in faint {
        let tint = starTints[star.tint]
        fill(ctx, tint, star.alpha)
        ctx.fillEllipse(in: CGRect(x: star.p.x - star.r, y: star.p.y - star.r, width: star.r * 2, height: star.r * 2))
    }
    // Far mountains, misty blue.
    let path = CGMutablePath()
    path.move(to: CGPoint(x: -10, y: h - 200))
    var phase = RNG(9)
    let ph = (0..<8).map { _ in phase.range(0, tau) }
    var x = -10.0
    while x <= L.width + 10 {
        let u = x / 1920 * tau
        var y = h + 74
        y += 34 * sin(u * 1.3 + ph[0]) + 24 * sin(u * 2.7 + ph[1]) + 14 * sin(u * 5.1 + ph[2]) + 8 * sin(u * 9.3 + ph[3])
        y += 3 * sin(u * 23 + ph[4]) + 2 * sin(u * 41 + ph[5])
        path.addLine(to: CGPoint(x: x, y: y))
        x += 3
    }
    path.addLine(to: CGPoint(x: L.width + 10, y: h - 200))
    path.closeSubpath()
    ctx.saveGState()
    ctx.addPath(path)
    ctx.clip()
    let mg = gradient([(0, RGB(0.17, 0.30, 0.52), 1), (1, RGB(0.11, 0.20, 0.38), 1)])
    ctx.drawLinearGradient(mg, start: CGPoint(x: 0, y: h - 40), end: CGPoint(x: 0, y: h + 130), options: [])
    ctx.restoreGState()
    ctx.addPath(path)
    ctx.setStrokeColor(RGB(0.42, 0.55, 0.80).cg(0.25))
    ctx.setLineWidth(1.4)
    ctx.strokePath()
    // Mist over their feet.
    let mist = gradient([(0, RGB(0.30, 0.44, 0.68), 0.5), (1, RGB(0.30, 0.44, 0.68), 0)])
    ctx.drawLinearGradient(
        mist, start: CGPoint(x: 0, y: h - 30), end: CGPoint(x: 0, y: h + 70), options: [.drawsBeforeStartLocation])
    return ctx.makeImage()!
}

/// A band of trees that slides past, drawn once, wide enough to repeat.
final class Strip {
    init(image: CGImage, margin: Double, y0: Double, width: Double, height: Double, period: Double, speed: Double) {
        self.image = image
        self.margin = margin
        self.y0 = y0
        self.width = width
        self.height = height
        self.period = period
        self.speed = speed
    }
    let image: CGImage
    let margin: Double
    let y0: Double
    let width: Double
    let height: Double
    let period: Double
    let speed: Double
}

func roundTree(_ path: CGMutablePath, base: P, height: Double, width: Double, rng: inout RNG, leafy: Int) {
    let trunkW = width * 0.09
    path.addRect(CGRect(x: base.x - trunkW / 2, y: base.y, width: trunkW, height: height * 0.5))
    let c = base + P(0, height * 0.62)
    let rx = width / 2
    let ry = height * 0.38
    for _ in 0..<12 {
        let a = rng.range(0, tau)
        let d = rng.range(0, 0.55)
        let r = rng.range(0.38, 0.6) * min(rx, ry * 1.2)
        let p = c + P(cos(a) * rx * d, sin(a) * ry * d)
        path.addEllipse(in: CGRect(x: p.x - r, y: p.y - r, width: r * 2, height: r * 2))
    }
    for i in 0..<leafy {
        let a = Double(i) / Double(leafy) * tau + rng.range(-0.1, 0.1)
        let r = rng.range(0.08, 0.16) * width
        let p = c + P(cos(a) * rx * rng.range(0.82, 0.98), sin(a) * ry * rng.range(0.82, 0.98))
        path.addEllipse(in: CGRect(x: p.x - r, y: p.y - r, width: r * 2, height: r * 2))
    }
}

func pineTree(_ path: CGMutablePath, base: P, height: Double, width: Double, rng: inout RNG) {
    let tiers = Int(rng.range(5, 8))
    var left: [P] = []
    var right: [P] = []
    for i in 1...tiers {
        let f = Double(i) / Double(tiers)
        let y = base.y + height * (1 - f * 0.88)
        let w = width / 2 * pow(f, 0.9) * rng.range(0.85, 1.1)
        left.append(P(base.x - w, y - height * 0.02))
        left.append(P(base.x - w * 0.42, y + height * 0.035))
        right.append(P(base.x + w, y - height * 0.02 + rng.range(-2, 2)))
        right.append(P(base.x + w * 0.42, y + height * 0.035))
    }
    path.move(to: CGPoint(x: base.x, y: base.y + height))
    for p in left { path.addLine(to: p.cg) }
    path.addLine(to: CGPoint(x: base.x - width * 0.05, y: base.y))
    path.addLine(to: CGPoint(x: base.x + width * 0.05, y: base.y))
    for p in right.reversed() { path.addLine(to: p.cg) }
    path.closeSubpath()
}

func makeStrip(
    _ L: Layout, _ s: Double, period: Double, speed: Double, base: Double, low: Double, high: Double, seed: UInt64,
    colour: RGB, rim: RGB, rimAlpha: Double, spacing: ClosedRange<Double>, heights: ClosedRange<Double>,
    widths: ClosedRange<Double>, pineShare: Double, leafy: Int, fillBelow: Bool
) -> Strip {
    let margin = 40.0
    let width = period + L.width + margin * 2
    let height = high - low
    let ctx = bitmap(Int((width * s).rounded()), Int((height * s).rounded()))
    ctx.scaleBy(x: s, y: s)
    ctx.translateBy(x: margin, y: -low)
    var rng = RNG(seed)
    let path = CGMutablePath()
    // One period's trees, drawn again a period along for as far as the strip goes.
    var trees: [(x: Double, h: Double, w: Double, pine: Bool, seed: UInt64)] = []
    var x = 0.0
    while x < period - spacing.lowerBound * 0.5 {
        trees.append((x, rng.range(heights.lowerBound, heights.upperBound), rng.range(widths.lowerBound, widths.upperBound), rng.chance(pineShare), rng.next()))
        x += rng.range(spacing.lowerBound, spacing.upperBound)
    }
    var copies = -1
    while Double(copies) * period < width {
        for tree in trees {
            let bx = tree.x + Double(copies) * period
            if bx < -margin - tree.w || bx > width - margin + tree.w { continue }
            var tr = RNG(tree.seed)
            if tree.pine {
                pineTree(path, base: P(bx, base), height: tree.h * 1.15, width: tree.w * 0.7, rng: &tr)
            } else {
                roundTree(path, base: P(bx, base), height: tree.h, width: tree.w, rng: &tr, leafy: leafy)
            }
        }
        copies += 1
    }
    if fillBelow {
        path.addRect(CGRect(x: -margin, y: low - 10, width: width, height: base - low + 12))
    }
    // A lighter edge along the tops, as the sky lights them, then the trees themselves.
    ctx.saveGState()
    ctx.translateBy(x: 0, y: 2.2)
    ctx.addPath(path)
    fill(ctx, rim, rimAlpha)
    ctx.fillPath()
    ctx.restoreGState()
    ctx.addPath(path)
    fill(ctx, colour)
    ctx.fillPath()
    var image = ctx.makeImage()!
    // Blurred along the way they move, as a camera would see them (half a frame's travel).
    let blur = Int((speed / Double(fps) * 0.5 * s).rounded())
    if blur >= 2 { image = motionBlur(image, length: blur | 1) }
    return Strip(image: image, margin: margin, y0: low, width: width, height: height, period: period, speed: speed)
}

func motionBlur(_ image: CGImage, length: Int) -> CGImage {
    let w = image.width
    let h = image.height
    let ctx = bitmap(w, h)
    ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
    guard let data = ctx.data else { return image }
    var src = vImage_Buffer(data: data, height: vImagePixelCount(h), width: vImagePixelCount(w), rowBytes: ctx.bytesPerRow)
    let out = bitmap(w, h)
    var dst = vImage_Buffer(data: out.data!, height: vImagePixelCount(h), width: vImagePixelCount(w), rowBytes: out.bytesPerRow)
    vImageBoxConvolve_ARGB8888(&src, &dst, nil, 0, 0, 1, UInt32(length), nil, vImage_Flags(kvImageEdgeExtend))
    return out.makeImage()!
}

/// The hill under the grass blades: its colour, and short grass that doesn't move.
func makeHill(_ L: Layout, _ s: Double) -> CGImage {
    let ctx = bitmap(Int(L.width * s), Int(L.height * s))
    ctx.scaleBy(x: s, y: s)
    let path = CGMutablePath()
    path.move(to: CGPoint(x: -20, y: -20))
    var x = -20.0
    while x <= L.width + 20 {
        path.addLine(to: CGPoint(x: x, y: L.crest.y(x)))
        x += 4
    }
    path.addLine(to: CGPoint(x: L.width + 20, y: -20))
    path.closeSubpath()
    ctx.saveGState()
    ctx.addPath(path)
    ctx.clip()
    let top = L.crest.points.map(\.y).max()!
    let g = gradient([(0, RGB(0.05, 0.15, 0.05), 1), (0.55, RGB(0.11, 0.30, 0.10), 1), (1, RGB(0.18, 0.44, 0.15), 1)])
    ctx.drawLinearGradient(g, start: .zero, end: CGPoint(x: 0, y: top), options: [])
    var rng = RNG(31)
    for _ in 0..<Int(L.width * 6) {
        let x = rng.range(-10, L.width + 10)
        let c = L.crest.y(x)
        let y = rng.range(-10, c)
        let d = clamp01(1 - y / c)
        let h = rng.range(5, 12) * (1 + d)
        let dark = rng.chance(0.6)
        fill(ctx, dark ? RGB(0.04, 0.12, 0.04) : RGB(0.26, 0.52, 0.2), dark ? 0.35 : 0.22)
        ctx.move(to: CGPoint(x: x - 1.2, y: y))
        ctx.addLine(to: CGPoint(x: x + rng.range(-3, 3), y: y + h))
        ctx.addLine(to: CGPoint(x: x + 1.2, y: y))
        ctx.closePath()
        ctx.fillPath()
    }
    ctx.restoreGState()
    return ctx.makeImage()!
}

// MARK: - The scene

final class Scene: @unchecked Sendable {
    let L: Layout
    let s: Double
    let sky: CGImage
    let hill: CGImage
    let mid: Strip
    let near: Strip
    let twinkling: [Star]
    let grass: (crest: [Blade], back: [Blade], front: [Blade])
    let bear = Bear3D()
    let noise: [UInt8]

    init(_ L: Layout, scale s: Double) {
        self.L = L
        self.s = s
        let stars = makeStars(L)
        twinkling = stars.twinkling
        sky = makeSky(L, s, faint: stars.faint)
        hill = makeHill(L, s)
        let h = L.horizon
        // Two bands of trees: the forest further off, and the trees nearer, which pass
        // twice as fast. Each moves a whole repeat in the loop.
        mid = makeStrip(
            L, s, period: 960, speed: 960 / loop, base: h - 10, low: h - 140, high: h + 150, seed: 21,
            colour: RGB(0.075, 0.14, 0.27), rim: RGB(0.22, 0.32, 0.52), rimAlpha: 0.7, spacing: 16...34,
            heights: 48...118, widths: 34...78, pineShare: 0.5, leafy: 16, fillBelow: true)
        near = makeStrip(
            L, s, period: 1920, speed: 1920 / loop, base: h - 110, low: h - 200, high: h + 300, seed: 22,
            colour: RGB(0.032, 0.065, 0.13), rim: RGB(0.16, 0.25, 0.42), rimAlpha: 0.75, spacing: 120...260,
            heights: 170...330, widths: 100...200, pineShare: 0.42, leafy: 34, fillBelow: false)
        grass = makeGrass(L)
        var rng = RNG(99)
        let count = Int(L.width * s) * Int(L.height * s)
        var noise = [UInt8](repeating: 0, count: count)
        for i in 0..<count {
            // Triangular, centred on 2: a whisper of grain that stops the sky banding.
            noise[i] = UInt8(Int(rng.next() & 3) + Int((rng.next() >> 8) & 1))
        }
        self.noise = noise
    }

    func render(_ t: Double, into ctx: CGContext) {
        let L = self.L
        ctx.saveGState()
        ctx.interpolationQuality = .high
        ctx.scaleBy(x: s, y: s)
        let full = CGRect(x: 0, y: 0, width: L.width, height: L.height)
        ctx.draw(sky, in: full)

        let moon = moonAt(t, L)
        // Stars twinkle, and fade near the moon's glow.
        for star in twinkling {
            let tw = 1 - star.depth * (0.5 + 0.5 * wave(t, star.times, star.phase))
            let d = (star.p - moon.pos).length / (L.moonRadius * 3.4)
            let veil = 1 - 0.85 * moon.alpha * exp(-d * d)
            drawStar(ctx, star, alpha: star.alpha * tw * veil, scale: 1)
        }
        drawMoon(ctx, centre: moon.pos, radius: L.moonRadius, t: t, alpha: moon.alpha, pixels: s)

        for strip in [mid, near] {
            let shift = (strip.speed * t).truncatingRemainder(dividingBy: strip.period)
            ctx.draw(
                strip.image,
                in: CGRect(x: -strip.margin - shift, y: strip.y0, width: strip.width, height: strip.height))
            let mist = gradient([(0, RGB(0.25, 0.38, 0.62), strip === mid ? 0.42 : 0.2), (1, RGB(0.25, 0.38, 0.62), 0)])
            ctx.drawLinearGradient(
                mist, start: CGPoint(x: 0, y: L.horizon - 40), end: CGPoint(x: 0, y: L.horizon + (strip === mid ? 70 : 110)),
                options: [.drawsBeforeStartLocation])
        }

        ctx.draw(hill, in: full)
        for blade in grass.crest { drawBlade(ctx, blade, t: t) }
        for blade in grass.back { drawBlade(ctx, blade, t: t) }

        let pose = poseAt(t, L)
        // A soft shadow where the bear sits, then the bear.
        softEllipse(
            ctx, L.bear + P(0, 6) * L.bearScale, 255 * L.bearScale, 50 * L.bearScale, RGB(0.01, 0.04, 0.01), 0.66,
            core: 0.25)
        let picture = bear.render(pose: pose, layout: L, scale: s, t: t)
        ctx.draw(picture.0, in: picture.1)

        for blade in grass.front { drawBlade(ctx, blade, t: t) }

        // A gentle darkening towards the corners.
        ctx.saveGState()
        ctx.translateBy(x: L.width / 2, y: L.height / 2)
        ctx.scaleBy(x: L.width / 2, y: L.height / 2)
        let vignette = gradient([(0, RGB(0, 0, 0.02), 0), (0.44, RGB(0, 0, 0.02), 0), (1, RGB(0, 0, 0.02), 0.55)])
        ctx.drawRadialGradient(
            vignette, startCenter: .zero, startRadius: 0, endCenter: .zero, endRadius: 1.42, options: [.drawsAfterEndLocation])
        ctx.restoreGState()
        ctx.restoreGState()
    }

    /// Grain added straight into the pixels (BGRA, 8 bits each).
    func grain(_ base: UnsafeMutableRawPointer, rowBytes: Int, width: Int, height: Int) {
        noise.withUnsafeBufferPointer { n in
            for y in 0..<height {
                let row = base.advanced(by: y * rowBytes).assumingMemoryBound(to: UInt8.self)
                let nrow = y * width
                for x in 0..<width {
                    let v = Int(n[nrow + x]) - 2
                    if v == 0 { continue }
                    for c in 0..<3 {
                        let i = x * 4 + c
                        row[i] = UInt8(max(0, min(255, Int(row[i]) + v)))
                    }
                }
            }
        }
    }

    var pixelWidth: Int { Int(L.width * s) }
    var pixelHeight: Int { Int(L.height * s) }

    func image(at t: Double) -> CGImage {
        let ctx = bitmap(pixelWidth, pixelHeight)
        render(t, into: ctx)
        grain(ctx.data!, rowBytes: ctx.bytesPerRow, width: pixelWidth, height: pixelHeight)
        return ctx.makeImage()!
    }
}

// MARK: - Output

func writePNG(_ image: CGImage, _ path: String) {
    let url = URL(fileURLWithPath: path)
    let dest = CGImageDestinationCreateWithURL(url as CFURL, "public.png" as CFString, 1, nil)!
    CGImageDestinationAddImage(dest, image, nil)
    CGImageDestinationFinalize(dest)
}

func writeVideo(_ scene: Scene, _ path: String, bitrate: Int) throws {
    let w = scene.pixelWidth
    let h = scene.pixelHeight
    let url = URL(fileURLWithPath: path)
    try? FileManager.default.removeItem(at: url)
    let writer = try AVAssetWriter(outputURL: url, fileType: .mp4)
    writer.shouldOptimizeForNetworkUse = true
    let settings: [String: Any] = [
        AVVideoCodecKey: AVVideoCodecType.hevc,
        AVVideoWidthKey: w,
        AVVideoHeightKey: h,
        AVVideoColorPropertiesKey: [
            AVVideoColorPrimariesKey: AVVideoColorPrimaries_ITU_R_709_2,
            AVVideoTransferFunctionKey: AVVideoTransferFunction_ITU_R_709_2,
            AVVideoYCbCrMatrixKey: AVVideoYCbCrMatrix_ITU_R_709_2,
        ],
        AVVideoCompressionPropertiesKey: [
            AVVideoAverageBitRateKey: bitrate,
            AVVideoExpectedSourceFrameRateKey: fps,
            AVVideoMaxKeyFrameIntervalKey: fps,
            AVVideoAllowFrameReorderingKey: true,
        ],
    ]
    let input = AVAssetWriterInput(mediaType: .video, outputSettings: settings)
    input.expectsMediaDataInRealTime = false
    let adaptor = AVAssetWriterInputPixelBufferAdaptor(
        assetWriterInput: input,
        sourcePixelBufferAttributes: [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
            kCVPixelBufferWidthKey as String: w,
            kCVPixelBufferHeightKey as String: h,
            kCVPixelBufferCGBitmapContextCompatibilityKey as String: true,
        ])
    writer.add(input)
    guard writer.startWriting() else { throw writer.error ?? NSError(domain: "render", code: 1) }
    writer.startSession(atSourceTime: .zero)
    let total = Int(loop) * fps
    let batch = max(1, ProcessInfo.processInfo.activeProcessorCount)
    var index = 0
    let started = Date()
    while index < total {
        let n = min(batch, total - index)
        var buffers: [CVPixelBuffer] = []
        for _ in 0..<n {
            var buffer: CVPixelBuffer?
            CVPixelBufferPoolCreatePixelBuffer(nil, adaptor.pixelBufferPool!, &buffer)
            buffers.append(buffer!)
        }
        let first = index
        DispatchQueue.concurrentPerform(iterations: n) { k in
            let buffer = buffers[k]
            CVPixelBufferLockBaseAddress(buffer, [])
            let base = CVPixelBufferGetBaseAddress(buffer)!
            let rowBytes = CVPixelBufferGetBytesPerRow(buffer)
            let ctx = CGContext(
                data: base, width: w, height: h, bitsPerComponent: 8, bytesPerRow: rowBytes, space: space,
                bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue)!
            ctx.clear(CGRect(x: 0, y: 0, width: w, height: h))
            scene.render(Double(first + k) / Double(fps), into: ctx)
            scene.grain(base, rowBytes: rowBytes, width: w, height: h)
            CVPixelBufferUnlockBaseAddress(buffer, [])
        }
        for (k, buffer) in buffers.enumerated() {
            while !input.isReadyForMoreMediaData { usleep(5000) }
            adaptor.append(buffer, withPresentationTime: CMTime(value: Int64(first + k), timescale: Int32(fps)))
        }
        index += n
        let rate = Date().timeIntervalSince(started) / Double(index)
        FileHandle.standardError.write("\r\(index)/\(total) frames, \(String(format: "%.2f", rate)) s a frame   ".data(using: .utf8)!)
    }
    input.markAsFinished()
    writer.endSession(atSourceTime: CMTime(value: Int64(total), timescale: Int32(fps)))
    let done = DispatchSemaphore(value: 0)
    writer.finishWriting { done.signal() }
    done.wait()
    FileHandle.standardError.write("\n".data(using: .utf8)!)
    if writer.status != .completed { throw writer.error ?? NSError(domain: "render", code: 2) }
}

// MARK: - Main

let args = CommandLine.arguments
func usage() -> Never {
    FileHandle.standardError.write(
        "usage: render video <landscape|portrait> out.mp4 [megabits]\n       render still <layout> <seconds> out.png [scale]\n       render sheet <layout> out.png\n"
            .data(using: .utf8)!)
    exit(2)
}
guard args.count >= 4 else { usage() }
let layout = Layout.named(args[2])
switch args[1] {
case "video":
    let megabits = args.count > 4 ? Double(args[4]) ?? 16 : 16
    let scene = Scene(layout, scale: 2)
    do {
        try writeVideo(scene, args[3], bitrate: Int(megabits * 1_000_000))
    } catch {
        FileHandle.standardError.write("Couldn't write the video: \(error)\n".data(using: .utf8)!)
        exit(1)
    }
case "still":
    guard args.count >= 5, let t = Double(args[3]) else { usage() }
    let scale = args.count > 5 ? Double(args[5]) ?? 1 : 1
    let scene = Scene(layout, scale: scale)
    writePNG(scene.image(at: t), args[4])
case "sheet":
    let scale = layout.name == "portrait" ? 0.3 : 0.4
    let scene = Scene(layout, scale: scale)
    let moments = [0.05, 0.4, 0.8, 1.1, 1.5, 1.9, 2.45, 2.85]
    let cols = layout.name == "portrait" ? 4 : 2
    let rows = (moments.count + cols - 1) / cols
    let w = scene.pixelWidth
    let h = scene.pixelHeight
    let sheet = bitmap(w * cols + 4 * (cols - 1), h * rows + 4 * (rows - 1))
    fill(sheet, RGB(1, 1, 1))
    sheet.fill(CGRect(x: 0, y: 0, width: sheet.width, height: sheet.height))
    var images = [CGImage?](repeating: nil, count: moments.count)
    let lock = NSLock()
    DispatchQueue.concurrentPerform(iterations: moments.count) { i in
        let image = scene.image(at: moments[i])
        lock.lock()
        images[i] = image
        lock.unlock()
    }
    for (i, image) in images.enumerated() {
        let col = i % cols
        let row = rows - 1 - i / cols
        sheet.draw(image!, in: CGRect(x: col * (w + 4), y: row * (h + 4), width: w, height: h))
    }
    writePNG(sheet.makeImage()!, args[3])
default:
    usage()
}
