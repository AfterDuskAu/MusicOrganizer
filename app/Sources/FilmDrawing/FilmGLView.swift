import AppKit
import Libmpv
import OpenGL.GL

/// Where a film is drawn. mpv draws each frame into this view at the size the view is
/// at that moment, so the picture always fits the window and sits in the middle of it,
/// however the window is resized, made full screen, or the sidebar opened and shut.
///
/// This is mpv's own, long-standing way of drawing inside another app (its "render
/// API"), over OpenGL. Apple calls OpenGL deprecated but still ships it; this small
/// target is built with those warnings off (`app/Package.swift`), since every line
/// here would raise one. (The first version drew through the package's Metal patch,
/// which never learned of a new window size: the film stayed the size the window had
/// when it started.)
///
/// Frames are drawn on a thread of their own, so the film never waits for the rest of
/// the app (a list being laid out, a menu open). Everything that touches the drawing
/// goes through `locked`, one at a time: a frame, a new size, the start and the end.
///
/// **The drawing is held only while a frame is being drawn, never while one is waited
/// for** (2026-10-12). mpv hands a frame over a little before it's due, and by itself
/// waits out that time inside the call that draws it. Held through that wait, the
/// drawing was taken nearly all the time, and macOS, which takes it too whenever this
/// view's place in the window changes, waited behind it: a second for every step of a
/// window being resized, which is what full screen and back are (the owner's "extremely
/// slow"; a sample showed the app waiting for this lock). So the wait is done here,
/// with the drawing let go, and mpv is told not to wait again.
public final class FilmGLView: NSOpenGLView, @unchecked Sendable {
    private var render: OpaquePointer?
    /// The player that's attached, for its clock: a frame's due time is told by it.
    private var player: OpaquePointer?
    /// The longest a frame is waited for, in millionths of a second. mpv hands a frame
    /// over 50 thousandths before it's due, and gives up on one not shown within 200.
    private static let longestWait: Int64 = 100_000
    /// The view's size in the screen's own pixels, as last told on the main thread.
    private var pixels = CGSize.zero
    private let drawing = DispatchQueue(label: "film.drawing", qos: .userInteractive)

    /// Do something with the drawing, with nothing else using it meanwhile.
    private func locked<T>(_ work: (NSOpenGLContext) -> T) -> T? {
        guard let context = openGLContext, let lock = context.cglContextObj else { return nil }
        CGLLockContext(lock)
        defer { CGLUnlockContext(lock) }
        context.makeCurrentContext()
        return work(context)
    }

    public init() {
        let attributes: [NSOpenGLPixelFormatAttribute] = [
            // The modern kind of OpenGL (3.2 "core"), which mpv's drawing is written for.
            NSOpenGLPixelFormatAttribute(NSOpenGLPFAOpenGLProfile),
            NSOpenGLPixelFormatAttribute(NSOpenGLProfileVersion3_2Core),
            NSOpenGLPixelFormatAttribute(NSOpenGLPFADoubleBuffer),
            NSOpenGLPixelFormatAttribute(NSOpenGLPFAAccelerated),
            NSOpenGLPixelFormatAttribute(NSOpenGLPFAColorSize), 32,
            0,
        ]
        super.init(frame: .zero, pixelFormat: NSOpenGLPixelFormat(attributes: attributes))!
        wantsBestResolutionOpenGLSurface = true  // every pixel of a Retina screen
        autoresizingMask = [.width, .height]
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("made in code only") }

    /// Have this player draw here. False when mpv couldn't set its drawing up.
    public func attach(_ mpv: OpaquePointer) -> Bool {
        let made = locked { context -> OpaquePointer? in
            // Don't hold each frame back for the screen's next refresh: mpv keeps the
            // time itself.
            var interval: GLint = 0
            context.setValues(&interval, for: .swapInterval)
            let api = UnsafeMutableRawPointer(
                mutating: (MPV_RENDER_API_TYPE_OPENGL as NSString).utf8String)
            var start = mpv_opengl_init_params(
                get_proc_address: { _, name in
                    let symbol = CFStringCreateWithCString(
                        kCFAllocatorDefault, name, CFStringBuiltInEncodings.ASCII.rawValue)
                    let bundle = CFBundleGetBundleWithIdentifier("com.apple.opengl" as CFString)
                    return CFBundleGetFunctionPointerForName(bundle, symbol)
                },
                get_proc_address_ctx: nil)
            var made: OpaquePointer?
            let result = withUnsafeMutablePointer(to: &start) { start -> Int32 in
                var params = [
                    mpv_render_param(type: MPV_RENDER_PARAM_API_TYPE, data: api),
                    mpv_render_param(type: MPV_RENDER_PARAM_OPENGL_INIT_PARAMS, data: start),
                    mpv_render_param(),
                ]
                return mpv_render_context_create(&made, mpv, &params)
            }
            guard result >= 0, let made else { return nil }
            self.render = made
            self.player = mpv
            self.pixels = self.convertToBacking(self.bounds).size
            return made
        }
        guard let made, let made else { return false }
        // mpv says when there's a new frame.
        mpv_render_context_set_update_callback(
            made,
            { pointer in
                guard let pointer else { return }
                Unmanaged<FilmGLView>.fromOpaque(pointer).takeUnretainedValue().drawSoon()
            }, Unmanaged.passUnretained(self).toOpaque())
        return true
    }

    /// Stop drawing for the player that was attached. Call before the player is shut.
    public func detach() {
        let old = locked { _ -> OpaquePointer? in
            let old = self.render
            self.render = nil
            self.player = nil
            return old
        }
        guard let old, let old else { return }
        mpv_render_context_set_update_callback(old, nil, nil)
        _ = locked { _ in mpv_render_context_free(old) }
        drawSoon()  // back to plain black
    }

    private func drawSoon() {
        drawing.async { [weak self] in self?.drawFrame() }
    }

    public override func reshape() {
        // The window changed size (or went full screen): the drawing is told, between
        // two frames, and the next frame is drawn at the new size.
        _ = locked { context in
            context.update()
            self.pixels = self.convertToBacking(self.bounds).size
        }
        drawSoon()
    }

    public override func draw(_ dirtyRect: NSRect) {
        drawSoon()
    }

    /// How long until the frame mpv has ready is due, in millionths of a second: 0 if
    /// it's due now, or there's no new frame (the last one is only being drawn again).
    private func untilDue() -> Int64 {
        let wait = locked { _ -> Int64 in
            guard let render = self.render, let player = self.player else { return 0 }
            var info = mpv_render_frame_info()
            let got = withUnsafeMutablePointer(to: &info) { info in
                mpv_render_context_get_info(
                    render, mpv_render_param(type: MPV_RENDER_PARAM_NEXT_FRAME_INFO, data: info))
            }
            guard got >= 0, info.target_time > 0 else { return 0 }
            // mpv's header says the time is in millionths of a second, by the clock
            // `mpv_get_time_us` reads. The mpv this is built with (0.41) gives it in
            // thousand-millionths, by `mpv_get_time_ns` (seen 2026-10-12: taken for the
            // other, every frame waited its longest and four in five were dropped). So
            // it's read as whichever clock it's near, and if it's near neither, the
            // frame isn't waited for at all.
            let due = info.target_time
            let fine = mpv_get_time_ns(player)
            let coarse = mpv_get_time_us(player)
            if abs(due - fine) < 10_000_000_000 { return (due - fine) / 1000 }
            if abs(due - coarse) < 10_000_000 { return due - coarse }
            return 0
        }
        return min(max(wait ?? 0, 0), Self.longestWait)
    }

    /// Draw what the film shows now, at the size the view is now.
    private func drawFrame() {
        // The frame's moment is waited for with the drawing let go, so nothing else
        // that needs the drawing (macOS, at every change of the window's size) waits
        // behind a frame that isn't even being drawn yet.
        let wait = untilDue()
        if wait > 0 { usleep(useconds_t(wait)) }
        var shown: OpaquePointer?
        _ = locked { context in
            let size = self.pixels
            glViewport(0, 0, GLsizei(size.width), GLsizei(size.height))
            glClearColor(0, 0, 0, 1)
            glClear(GLbitfield(GL_COLOR_BUFFER_BIT))
            if let render = self.render, size.width >= 1, size.height >= 1 {
                var bound: GLint = 0
                glGetIntegerv(GLenum(GL_FRAMEBUFFER_BINDING), &bound)
                var target = mpv_opengl_fbo(
                    fbo: Int32(bound), w: Int32(size.width), h: Int32(size.height),
                    internal_format: 0)
                var flip: CInt = 1
                var block: CInt = 0  // the frame's moment has been waited for already
                withUnsafeMutablePointer(to: &target) { target in
                    withUnsafeMutablePointer(to: &flip) { flip in
                        withUnsafeMutablePointer(to: &block) { block in
                            var params = [
                                mpv_render_param(type: MPV_RENDER_PARAM_OPENGL_FBO, data: target),
                                mpv_render_param(type: MPV_RENDER_PARAM_FLIP_Y, data: flip),
                                mpv_render_param(
                                    type: MPV_RENDER_PARAM_BLOCK_FOR_TARGET_TIME, data: block),
                                mpv_render_param(),
                            ]
                            _ = mpv_render_context_render(render, &params)
                        }
                    }
                }
                shown = render
            }
            context.flushBuffer()
        }
        if let shown { mpv_render_context_report_swap(shown) }
    }
}
