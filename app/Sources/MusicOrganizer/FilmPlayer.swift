import AppKit
import FilmDrawing
import Libmpv
import MusicOrganizerKit
import Observation
import SwiftUI

/// The player for films and for any video file: libmpv, which plays nearly every kind
/// (MKV, MP4, MOV, AVI…) with the Mac's graphics chip doing the decoding where it can.
/// One film at a time. It's apart from the music player, which it pauses when a film
/// starts. It only reads what it's given: a file, or an address.
@MainActor
@Observable
final class FilmPlayer {
    /// What's playing: nil when the player is shut.
    private(set) var title: String?
    private(set) var paused = false
    private(set) var time = 0.0
    private(set) var duration = 0.0
    /// Waiting for more of the film to arrive.
    private(set) var buffering = false
    private(set) var problem: String?
    var volume = 100.0 {
        didSet { set("volume", volume) }
    }

    /// Where the film is drawn. One view for the player's whole life.
    @ObservationIgnored let surface = FilmGLView()
    @ObservationIgnored private var mpv: OpaquePointer?
    @ObservationIgnored private var clock: Timer?
    /// The film's sound and subtitle tracks, for the two menus. Read once a second.
    private(set) var tracks: [FilmTrack] = []
    /// What this film is remembered by, for opening where it was left.
    @ObservationIgnored private var remembered: String?
    @ObservationIgnored private var looks = 0

    var isOpen: Bool { title != nil }
    var soundTracks: [FilmTrack] { tracks.filter { $0.kind == .sound } }
    var subtitleTracks: [FilmTrack] { tracks.filter { $0.kind == .subtitles } }

    /// Open a film and start it, where it was left the last time if it was left part
    /// way. `address` is a file's or a web address; `key` is what the film is
    /// remembered by when its address isn't the same each time (a torrent's).
    func open(_ address: URL, title: String, key: String? = nil) {
        close()
        self.title = title
        remembered = key ?? (address.isFileURL ? address.path : address.absoluteString)
        (tracks, looks) = ([], 0)
        (paused, time, duration, buffering, problem) = (false, 0, 0, true, nil)
        guard let made = mpv_create() else {
            problem = "The film player couldn't start."
            return
        }
        mpv = made
        for (name, value) in [
            ("vo", "libmpv"),  // mpv draws into the app's own view, at the view's size
            ("hwdec", "auto-safe"),  // the graphics chip decodes what it can
            ("ytdl", "no"),  // nothing is looked up or fetched by the player itself
            ("input-default-bindings", "no"), ("input-media-keys", "no"),
            ("subs-fallback", "yes"), ("keep-open", "yes"),
            // A film from a torrent may take a while to start arriving.
            ("network-timeout", "180"),
        ] {
            mpv_set_option_string(made, name, value)
        }
        mpv_request_log_messages(made, "no")
        // MUSICORG_FILM_LOG=1: mpv says what it's doing on stderr, for finding a fault.
        if ProcessInfo.processInfo.environment["MUSICORG_FILM_LOG"] != nil {
            mpv_set_option_string(made, "terminal", "yes")
            mpv_set_option_string(made, "msg-level", "all=v")
        }
        guard mpv_initialize(made) >= 0 else {
            problem = "The film player couldn't start."
            shut()
            return
        }
        guard surface.attach(made) else {
            problem = "The film player couldn't draw on this Mac."
            shut()
            return
        }
        set("volume", volume)
        if let key = remembered, let place = FilmPositions.saved().place(of: key) {
            mpv_set_property_string(made, "start", String(Int(place)))
        }
        let location = address.isFileURL ? address.path : address.absoluteString
        command(["loadfile", location])
        clock = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.look() }
        }
    }

    func close() {
        remember()
        title = nil
        remembered = nil
        tracks = []
        shut()
    }

    /// Play this sound track.
    func chooseSound(_ track: FilmTrack) {
        if let mpv { mpv_set_property_string(mpv, "aid", String(track.id)) }
        readTracks()
    }

    /// Show these subtitles, or none.
    func chooseSubtitles(_ track: FilmTrack?) {
        if let mpv { mpv_set_property_string(mpv, "sid", track.map { String($0.id) } ?? "no") }
        readTracks()
    }

    /// Note where the film has got to, for the next time it's opened.
    private func remember() {
        guard let remembered, duration > 0 else { return }
        var places = FilmPositions.saved()
        places.watched(remembered, to: time, of: duration)
        places.save()
    }

    private func text(_ name: String) -> String? {
        guard let mpv, let found = mpv_get_property_string(mpv, name) else { return nil }
        defer { mpv_free(found) }
        return String(cString: found)
    }

    private func readTracks() {
        guard mpv != nil, let count = text("track-list/count").flatMap(Int.init) else { return }
        var found: [FilmTrack] = []
        for index in 0..<count {
            let at = "track-list/\(index)"
            guard let kind = text("\(at)/type").flatMap(FilmTrack.Kind.init(rawValue:)),
                let id = text("\(at)/id").flatMap(Int.init)
            else { continue }
            found.append(
                FilmTrack(
                    id: id, kind: kind, title: text("\(at)/title"), language: text("\(at)/lang"),
                    selected: text("\(at)/selected") == "yes"))
        }
        if found != tracks { tracks = found }
    }

    func togglePause() {
        paused.toggle()
        var flag: Int32 = paused ? 1 : 0
        if let mpv { mpv_set_property(mpv, "pause", MPV_FORMAT_FLAG, &flag) }
    }

    func seek(to seconds: Double) {
        time = seconds
        command(["seek", String(seconds), "absolute"])
    }

    func skip(_ seconds: Double) {
        command(["seek", String(seconds), "relative"])
    }

    // MARK: talking to mpv

    private func shut() {
        clock?.invalidate()
        clock = nil
        if let mpv {
            self.mpv = nil
            surface.detach()  // before the player goes: its drawing belongs to it
            // Off the main thread: stopping waits for the picture's last frame.
            let address = Int(bitPattern: mpv)
            DispatchQueue.global().async { mpv_terminate_destroy(OpaquePointer(bitPattern: address)) }
        }
    }

    /// Read where the film is. Asked four times a second, which is plenty for a slider.
    private func look() {
        guard let mpv else { return }
        var seconds = 0.0
        if mpv_get_property(mpv, "time-pos", MPV_FORMAT_DOUBLE, &seconds) >= 0 { time = seconds }
        var length = 0.0
        if mpv_get_property(mpv, "duration", MPV_FORMAT_DOUBLE, &length) >= 0 { duration = length }
        var waiting: Int32 = 0
        mpv_get_property(mpv, "paused-for-cache", MPV_FORMAT_FLAG, &waiting)
        var idle: Int32 = 0
        mpv_get_property(mpv, "core-idle", MPV_FORMAT_FLAG, &idle)
        buffering = waiting != 0 || (duration == 0 && idle != 0)
        // Nothing loaded any more and it never had a length: it couldn't be opened.
        var nothing: Int32 = 0
        mpv_get_property(mpv, "idle-active", MPV_FORMAT_FLAG, &nothing)
        if nothing != 0, duration == 0 {
            problem = "This film couldn't be opened."
            buffering = false
        }
        // Once a second: its tracks (they arrive with the film). Every ten: where it is.
        looks += 1
        if looks % 4 == 0 { readTracks() }
        if looks % 40 == 0 { remember() }
    }

    private func set(_ name: String, _ value: Double) {
        guard let mpv else { return }
        var value = value
        mpv_set_property(mpv, name, MPV_FORMAT_DOUBLE, &value)
    }

    private func command(_ words: [String]) {
        guard let mpv else { return }
        var pointers: [UnsafePointer<CChar>?] = words.map { UnsafePointer(strdup($0)) }
        pointers.append(nil)
        mpv_command(mpv, &pointers)
        for pointer in pointers { free(UnsafeMutablePointer(mutating: pointer)) }
    }
}

/// Where the film is drawn: the player's own view, the size SwiftUI gives it.
struct FilmSurface: NSViewRepresentable {
    let view: FilmGLView

    func makeNSView(context: Context) -> FilmGLView { view }

    func updateNSView(_ view: FilmGLView, context: Context) {}
}

/// A film over the whole window: the picture, and its controls along the bottom, which
/// go away while the mouse rests.
struct FilmPlayerView: View {
    @Environment(AppModel.self) private var model
    @State private var controlsShown = true
    @State private var lastMove = Date()
    @State private var dragging: Double?

    var body: some View {
        let film = model.film
        ZStack(alignment: .bottom) {
            Color.black
            FilmSurface(view: film.surface)
            if let problem = film.problem {
                Text(problem).foregroundStyle(.white).frame(maxHeight: .infinity)
            } else if film.buffering {
                ProgressView().controlSize(.large).frame(maxHeight: .infinity)
            }
            if controlsShown || film.paused || film.problem != nil {
                controls(film)
            }
        }
        .environment(\.colorScheme, .dark)
        .ignoresSafeArea()
        .onContinuousHover { _ in
            lastMove = Date()
            controlsShown = true
        }
        .onTapGesture(count: 2) { NSApp.keyWindow?.toggleFullScreen(nil) }
        .task {
            // The controls go away three seconds after the mouse last moved.
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(1))
                if Date().timeIntervalSince(lastMove) > 3 { controlsShown = false }
            }
        }
    }

    private func controls(_ film: FilmPlayer) -> some View {
        VStack(spacing: 8) {
            Text(film.title ?? "").font(.headline).lineLimit(1)
            if let status = model.filmStatus {
                Text(status.line).font(.callout).foregroundStyle(.secondary)
            }
            HStack(spacing: 12) {
                Text(Self.clock(dragging ?? film.time)).monospacedDigit()
                Slider(
                    value: Binding(get: { dragging ?? film.time }, set: { dragging = $0 }),
                    in: 0...max(film.duration, 1)
                ) { editing in
                    if !editing, let dragging {
                        film.seek(to: dragging)
                        self.dragging = nil
                    }
                }
                .disabled(film.duration == 0)
                Text(Self.clock(film.duration)).monospacedDigit()
            }
            HStack(spacing: 18) {
                Button("Close", systemImage: "xmark") { close() }
                    .help("Stop the film and go back")
                Spacer()
                Button("Back 10 Seconds", systemImage: "gobackward.10") { film.skip(-10) }
                Button(film.paused ? "Play" : "Pause", systemImage: film.paused ? "play.fill" : "pause.fill") {
                    film.togglePause()
                }
                .font(.title)
                .keyboardShortcut(.space, modifiers: [])
                Button("Forward 30 Seconds", systemImage: "goforward.30") { film.skip(30) }
                Spacer()
                if film.soundTracks.count > 1 {
                    Menu {
                        ForEach(film.soundTracks) { track in
                            Toggle(track.label, isOn: Binding(
                                get: { track.selected }, set: { _ in film.chooseSound(track) }))
                        }
                    } label: {
                        Label("Sound Track", systemImage: "waveform")
                    }
                    .menuStyle(.borderlessButton)
                    .menuIndicator(.hidden)
                    .fixedSize()
                    .help("Which sound track plays")
                }
                if !film.subtitleTracks.isEmpty {
                    Menu {
                        Toggle("Off", isOn: Binding(
                            get: { !film.subtitleTracks.contains(where: \.selected) },
                            set: { _ in film.chooseSubtitles(nil) }))
                        Divider()
                        ForEach(film.subtitleTracks) { track in
                            Toggle(track.label, isOn: Binding(
                                get: { track.selected }, set: { _ in film.chooseSubtitles(track) }))
                        }
                    } label: {
                        Label("Subtitles", systemImage: "captions.bubble")
                    }
                    .menuStyle(.borderlessButton)
                    .menuIndicator(.hidden)
                    .fixedSize()
                    .help("Subtitles")
                }
                Image(systemName: "speaker.wave.2.fill")
                Slider(value: Bindable(film).volume, in: 0...100).frame(width: 110)
                Button("Full Screen", systemImage: "arrow.up.left.and.arrow.down.right") {
                    NSApp.keyWindow?.toggleFullScreen(nil)
                }
            }
            .labelStyle(.iconOnly)
            .buttonStyle(.plain)
            .font(.title3)
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 24)
        .padding(.vertical, 14)
        .background(.black.opacity(0.6))
    }

    private func close() {
        model.closeFilm()
    }

    /// Seconds as a film's clock: 1:02:03, or 2:03 under an hour.
    static func clock(_ seconds: Double) -> String {
        let whole = Int(max(seconds, 0))
        let (hours, minutes, rest) = (whole / 3600, whole % 3600 / 60, whole % 60)
        return hours > 0
            ? String(format: "%d:%02d:%02d", hours, minutes, rest)
            : String(format: "%d:%02d", minutes, rest)
    }
}
