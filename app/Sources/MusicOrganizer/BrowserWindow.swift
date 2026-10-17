import AppKit
import MusicOrganizerKit
import SwiftUI
import WebKit

/// Bear Media's browser window (the owner, 2026-10-18: "if the link hits this error. It
/// offers a open in browser option, and then if you click yes, it pulls the info needed
/// once it opens"; and "even if the player is hiding the stream, it searches as many
/// places as possible for it").
///
/// A page that turns the URL page's reader away is opened here, in a real browser
/// (WKWebView, Apple's WebKit, as Safari is): the owner sees the page and does whatever
/// it asks of them (an age check, a "not a robot"), and the window looks for the video in
/// every place `BrowserScript.finder` knows. A video chosen is fetched **by this browser**,
/// in its own requests (a stream's parts from the page's own frame, a whole file by the
/// browser's own download), and handed to the engine, which puts it together and keeps it
/// where the owner chose (`browser.*`). Nothing pretends to be a browser, nothing fetched
/// here is given to another program to fetch again, and nothing protected against copying
/// (DRM, a locked stream) is saved. The window keeps nothing once it's shut: no history,
/// no cookies, no sign-ins (a private store of its own).
@MainActor
final class BrowserWindowController: NSObject, NSWindowDelegate {
    let browser: PageBrowser
    let window: NSWindow
    var closed: (() -> Void)?

    init(model: AppModel, start: URL) {
        browser = PageBrowser(model: model)
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1320, height: 860),
            styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered,
            defer: false)
        super.init()
        window.title = "Bear Media Browser"
        window.isReleasedWhenClosed = false
        window.minSize = NSSize(width: 900, height: 560)
        window.contentView = NSHostingView(rootView: PageBrowserView(browser: browser).environment(model))
        window.delegate = self
        window.center()
        window.setFrameAutosaveName("BearMediaBrowser")
        browser.load(start)
    }

    func show() {
        if Snapshot.isOn {
            // The unseen copy: shown to nobody, and never brought in front of the owner.
            window.alphaValue = 0
            window.ignoresMouseEvents = true
            window.orderFront(nil)
            return
        }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate()
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        guard browser.isSaving else { return true }
        let alert = NSAlert()
        alert.messageText = "A video is still being saved from this window."
        alert.informativeText = "Closing the window stops it, and what has come of it is let go."
        alert.addButton(withTitle: "Keep Saving")
        alert.addButton(withTitle: "Stop and Close")
        alert.beginSheetModal(for: window) { [weak self] answer in
            guard answer == .alertSecondButtonReturn, let self else { return }
            self.browser.stopAll()
            self.window.close()
        }
        return false
    }

    func windowWillClose(_ notification: Notification) {
        browser.close()
        closed?()
    }
}

/// One video being saved from a browser window, as Downloads (or, for 18+, Videos → 18+)
/// lists it.
struct BrowserSave: Identifiable, Equatable {
    let id: String
    let name: String
    let place: KeptPlace
    var line: String
    var progress: Double?
    var running: Bool
    var failed = false
    var apart: Bool { place == .adult }
}

/// What a browser window has found and is saving.
@MainActor
@Observable
final class PageBrowser {
    let web: WKWebView
    @ObservationIgnored private let helper: BrowserHelper
    @ObservationIgnored weak var model: AppModel?

    var address = ""
    var title = ""
    var typed = ""
    var canGoBack = false
    var canGoForward = false
    var loading = false
    var findings = PageFindings() { didSet { rerank() } }
    /// The ways each stream can be saved, by what it's found by (`FoundMedia.id`).
    var choices: [String: [StreamChoice]] = [:]
    var chosen: [String: String] = [:]
    var problems: [String: String] = [:] { didSet { rerank() } }
    var sizes: [String: Int] = [:]
    /// How good each is, as far as can be told (2026-10-18): what the list is sorted by.
    var quality: [String: MediaQuality] = [:] { didSet { rerank() } }
    /// The videos found, the best first (the sharpest, then the biggest, then the
    /// longest), those that can't be saved last: worked out once when any of that changes,
    /// never while the list is drawn (2026-10-18: it was worked out for every row, and
    /// scrolling the list stuttered).
    private(set) var ranked: [FoundMedia] = []
    /// The one marked as the best: the first, when its picture's height is known and it
    /// can be saved.
    private(set) var best: String?
    /// What's being saved, or was, by what it's found by.
    var saves: [String: BrowserSave] = [:]
    var name = ""
    var place: KeptPlace = KeptPlace(rawValue: UserDefaults.standard.string(forKey: "browserPlace") ?? "") ?? .video {
        didSet { UserDefaults.standard.set(place.rawValue, forKey: "browserPlace") }
    }

    @ObservationIgnored var frames: [String: WKFrameInfo] = [:]
    @ObservationIgnored private var reading: Set<String> = []
    /// Videos waiting to have their picture size read, one at a time.
    @ObservationIgnored private var toMeasure: [FoundMedia] = []
    @ObservationIgnored private var measuring = false
    /// How many files of this page have had their size asked, and their picture read: a
    /// page full of little previews isn't asked about all of them.
    @ObservationIgnored private var sizesAsked = 0
    @ObservationIgnored private var measuresAsked = 0
    static let sizesMost = 24
    static let measuresMost = 12
    @ObservationIgnored private var tasks: [String: Task<Void, Never>] = [:]
    @ObservationIgnored private var engineIds: [String: String] = [:]
    @ObservationIgnored var downloads: [String: WKDownload] = [:]
    /// The page's title, address and history, watched as they change (a title can come
    /// well after the page has loaded).
    @ObservationIgnored private var watchers: [NSKeyValueObservation] = []

    /// The most a single answer may be, read in one go (a part of a stream).
    static let most = 64 * 1024 * 1024
    /// How much of a file is asked for at a time, when it's fetched in pieces.
    static let piece = 8 * 1024 * 1024

    init(model: AppModel) {
        self.model = model
        let configuration = WKWebViewConfiguration()
        // A store of its own, kept in memory only: nothing of the pages is kept once the
        // window is shut.
        configuration.websiteDataStore = .nonPersistent()
        configuration.preferences.isElementFullscreenEnabled = true
        configuration.mediaTypesRequiringUserActionForPlayback = []
        let helper = BrowserHelper()
        let controller = configuration.userContentController
        controller.addUserScript(
            WKUserScript(
                source: BrowserScript.finder, injectionTime: .atDocumentStart,
                forMainFrameOnly: false, in: .page))
        controller.add(helper, contentWorld: .page, name: BrowserScript.handler)
        web = WKWebView(frame: .zero, configuration: configuration)
        web.allowsBackForwardNavigationGestures = true
        web.navigationDelegate = helper
        web.uiDelegate = helper
        self.helper = helper
        helper.browser = self
        let changed: (WKWebView) -> Void = { [weak self] _ in Task { @MainActor in self?.refresh() } }
        watchers = [
            web.observe(\.title) { view, _ in changed(view) },
            web.observe(\.url) { view, _ in changed(view) },
            web.observe(\.canGoBack) { view, _ in changed(view) },
            web.observe(\.canGoForward) { view, _ in changed(view) },
            web.observe(\.isLoading) { view, _ in changed(view) },
        ]
    }

    var isSaving: Bool { saves.values.contains(where: \.running) }

    // MARK: going places

    func load(_ url: URL) {
        typed = url.absoluteString
        web.load(URLRequest(url: url))
    }

    func go() {
        var text = typed.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        if !text.lowercased().hasPrefix("http://") && !text.lowercased().hasPrefix("https://") {
            text = "https://" + text
        }
        if let url = URL(string: text) { load(url) }
    }

    func searchAgain() {
        web.evaluateJavaScript(BrowserScript.searchAgain, in: nil, in: .page) { _ in }
        for (_, frame) in frames {
            web.evaluateJavaScript(BrowserScript.searchAgain, in: frame, in: .page) { _ in }
        }
    }

    /// The page shown changed (not only its address within it): what was found on the
    /// last one is forgotten, but what's being saved from it goes on.
    func pageChanged() {
        findings = PageFindings()
        choices = [:]
        chosen = [:]
        problems = [:]
        sizes = [:]
        quality = [:]
        toMeasure = []
        sizesAsked = 0
        measuresAsked = 0
        reading = []
        frames = [:]
        name = ""
    }

    func refresh(newPage: Bool = false) {
        address = web.url?.absoluteString ?? address
        if newPage { typed = address }
        let now = web.title ?? ""
        // The name to save under follows the page's title until the owner changes it.
        if name.isEmpty || name == title { name = now }
        title = now
        canGoBack = web.canGoBack
        canGoForward = web.canGoForward
        loading = web.isLoading
    }

    // MARK: what's found

    func found(_ items: [(url: String, how: String, type: String?)], frame: String, info: WKFrameInfo) {
        frames[frame] = info
        findings.add(items, frame: frame)
        for media in findings.media where !reading.contains(media.id) {
            reading.insert(media.id)
            Task { await self.read(media) }
        }
    }

    func noted(_ kind: String, detail: String) {
        if kind == "protected" { findings.note(protected: true) }
        if kind == "stream" { findings.note(codec: detail) }
    }

    /// What a found video is: a file's size, or a stream's sizes on offer (its list read
    /// once, from the frame it was found in, as the page's player reads it).
    private func read(_ media: FoundMedia) async {
        do {
            switch media.kind {
            case .file:
                // Its height from its name, until the browser has read it.
                quality[media.id] = MediaQuality(height: MediaLinks.heightNamed(media.url))
                if sizesAsked < Self.sizesMost {
                    sizesAsked += 1
                    if let size = try? await sizeOf(media) {
                        sizes[media.id] = size
                        quality[media.id]?.bytes = size
                    }
                }
                measure(media)
            case .hls:
                let text = try await text(of: media.url, frame: media.frame)
                switch try HLSList.read(text, base: media.url) {
                case .sizes(var sizes):
                    // The parts of the sharpest are read now, for how long it is.
                    if let first = sizes.first?.list,
                        case .parts(let parts)? = try? HLSList.read(try await self.text(of: first, frame: media.frame), base: first)
                    {
                        for at in sizes.indices { sizes[at].seconds = parts.seconds }
                    }
                    choices[media.id] = sizes
                    if let best = sizes.first { quality[media.id] = MediaQuality(best) }
                    // Each size's own list, found too, is the same video: not listed apart.
                    findings.leaveOut(Set(sizes.flatMap { [$0.list, $0.audioList] }.compactMap { $0?.absoluteString }))
                case .parts(let parts):
                    choices[media.id] = [
                        StreamChoice(id: media.id, video: parts.all, seconds: parts.seconds)
                    ]
                    // One size only, which the list doesn't name: the browser reads it.
                    quality[media.id] = MediaQuality(seconds: parts.seconds)
                    measure(media)
                    findings.leaveOut(Set(parts.all.map(\.url.absoluteString)))
                }
            case .dash:
                let text = try await text(of: media.url, frame: media.frame)
                let found = try DASHList.read(text, base: media.url)
                choices[media.id] = found
                if let best = found.first { quality[media.id] = MediaQuality(best) }
                findings.leaveOut(Set(found.flatMap { $0.video + $0.audio }.map(\.url.absoluteString)))
            }
        } catch {
            problems[media.id] = error.localizedDescription
        }
    }

    /// Have the browser read a video's picture size and length, one video at a time (a
    /// page may name many).
    private func measure(_ media: FoundMedia) {
        guard measuresAsked < Self.measuresMost else { return }
        measuresAsked += 1
        toMeasure.append(media)
        guard !measuring else { return }
        measuring = true
        Task {
            while !toMeasure.isEmpty {
                let next = toMeasure.removeFirst()
                guard findings.media.contains(where: { $0.id == next.id }) else { continue }
                let said = try? await web.callAsyncJavaScript(
                    BrowserScript.measure, arguments: ["url": next.url.absoluteString],
                    in: next.frame.flatMap { frames[$0] }, contentWorld: .page)
                guard let found = said as? [String: Any], quality[next.id] != nil else { continue }
                if let height = (found["height"] as? NSNumber)?.intValue, height > 0 {
                    quality[next.id]?.height = height
                }
                if let seconds = (found["seconds"] as? NSNumber)?.doubleValue, seconds > 0 {
                    quality[next.id]?.seconds = seconds
                }
            }
            measuring = false
        }
    }

    private func rerank() {
        let now = MediaLinks.ranked(findings.media, by: quality, last: Set(problems.keys))
        if now != ranked { ranked = now }
        var top: String?
        if let first = now.first, quality[first.id]?.height != nil, problems[first.id] == nil {
            top = first.id
        }
        if top != best { best = top }
    }

    // MARK: saving

    func save(_ media: FoundMedia) {
        guard tasks[media.id] == nil, let model else { return }
        let title = name.trimmingCharacters(in: .whitespacesAndNewlines)
        let named = title.isEmpty ? (self.title.isEmpty ? "Video" : self.title) : title
        let place = place
        var choice: StreamChoice?
        if media.kind != .file {
            let offered = choices[media.id] ?? []
            choice = offered.first { $0.id == chosen[media.id] } ?? offered.first
            guard choice != nil else { return }
        }
        let started = BrowserSave(id: media.id, name: named, place: place, line: "Starting…", progress: nil, running: true)
        saves[media.id] = started
        model.noteBrowserSave(started)
        tasks[media.id] = Task {
            await self.run(media, choice: choice, named: named, place: place)
            self.tasks[media.id] = nil
        }
    }

    func stop(_ mediaId: String) {
        tasks[mediaId]?.cancel()
        downloads[mediaId]?.cancel()
    }

    func stopAll() {
        for id in tasks.keys { stop(id) }
    }

    func close() {
        stopAll()
        watchers = []
        web.stopLoading()
        web.navigationDelegate = nil
        web.uiDelegate = nil
        web.configuration.userContentController.removeAllScriptMessageHandlers()
    }

    private func say(_ id: String, _ line: String, progress: Double? = nil, running: Bool = true, failed: Bool = false) {
        guard var now = saves[id] else { return }
        now.line = line
        now.progress = progress
        now.running = running
        now.failed = failed
        saves[id] = now
        model?.noteBrowserSave(now)
    }

    private func run(_ media: FoundMedia, choice: StreamChoice?, named: String, place: KeptPlace) async {
        guard let model else { return }
        var engineId: String?
        do {
            let convert = UserDefaults.standard.object(forKey: TorrentStatus.convertKey) as? Bool ?? true
            var asked: [String: Any] = [
                "page_url": address, "media_url": media.url.absoluteString, "title": named,
                "kind": place.rawValue, "convert": convert,
            ]
            if let size = sizes[media.id] { asked["size"] = size }
            let started = try await model.ask("browser.start", asked, as: BrowserStarted.self)
            engineId = started.id
            engineIds[media.id] = started.id
            model.browserStops[media.id] = { [weak self] in self?.stop(media.id) }
            if media.kind == .file {
                try await downloadWhole(media, to: URL(fileURLWithPath: started.filePath), engineId: started.id)
            } else if var choice {
                if let list = choice.list {
                    say(media.id, "Reading the stream's parts…")
                    guard case .parts(let parts) = try HLSList.read(try await text(of: list, frame: media.frame), base: list) else {
                        throw StreamListError.notAList
                    }
                    choice.video = parts.all
                    if let audio = choice.audioList,
                        case .parts(let sound) = try HLSList.read(try await text(of: audio, frame: media.frame), base: audio)
                    {
                        choice.audio = sound.all
                    }
                }
                try await sendParts(choice, of: media, engineId: started.id)
            }
            try Task.checkCancellation()
            say(media.id, "Putting it together…", progress: 1)
            _ = try await model.ask("browser.done", ["id": started.id], as: FetchStatus.self)
            while true {
                let status = try await model.ask("stream.status", ["id": started.id], as: FetchStatus.self).asKeep
                if !status.isKeeping {
                    say(media.id, status.keepLine ?? "Done", progress: nil, running: false, failed: status.keepError != nil)
                    break
                }
                say(media.id, status.keepLine ?? "Saving…", progress: status.keepProgress)
                try await Task.sleep(for: .seconds(1))
            }
        } catch {
            let stopped = Task.isCancelled || error is CancellationError
            if let engineId { Task { _ = try? await model.ask("stream.stop", ["id": engineId], as: Empty.self) } }
            say(media.id, stopped ? "Stopped." : "Couldn't save it: \(error.localizedDescription)",
                running: false, failed: !stopped)
        }
        model.browserStops[media.id] = nil
    }

    /// A stream's parts, fetched in turn from the page's frame and handed to the engine:
    /// the picture's (with its sound in it, or alone), then the sound's when it comes apart.
    private func sendParts(_ choice: StreamChoice, of media: FoundMedia, engineId: String) async throws {
        guard let model else { return }
        let total = max(choice.video.count + choice.audio.count, 1)
        var done = 0
        var bytes = 0
        var toldAt = Date.distantPast
        for (track, parts) in [("video", choice.video), ("audio", choice.audio)] where !parts.isEmpty {
            var at = 0
            for part in parts {
                try await eachPiece(of: part, frame: media.frame) { piece in
                    try Task.checkCancellation()
                    let answer = try await model.ask(
                        "browser.part", ["id": engineId, "track": track, "at": at, "data": piece],
                        as: PartTaken.self)
                    bytes += answer.bytes - at
                    at = answer.bytes
                }
                done += 1
                let fraction = Double(done) / Double(total)
                if Date().timeIntervalSince(toldAt) > 1 || done == total {
                    toldAt = Date()
                    let whole = fraction > 0.02 ? Int(Double(bytes) / fraction) : nil
                    var said: [String: Any] = ["id": engineId, "progress": fraction]
                    if let whole { said["size"] = whole }
                    _ = try? await model.ask("browser.progress", said, as: Empty.self)
                    let size = ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file)
                    say(media.id, "Saving: \(Int(fraction * 100))% · \(size). Keep this window open until it's done.", progress: fraction)
                }
            }
        }
    }

    /// One part's bytes, as base64 pieces handed to `send` in turn: its range, or all of
    /// it (a whole file a manifest names, fetched a piece at a time when the address takes
    /// ranges). Each piece is sent before the next is fetched.
    private func eachPiece(
        of part: StreamPart, frame: String?, send: (String) async throws -> Void
    ) async throws {
        if let range = part.range {
            let answer = try await fetch(part.url, first: range.lowerBound, last: range.upperBound, frame: frame)
            if answer.status == 200, let data = Data(base64Encoded: answer.data), data.count > range.upperBound {
                // The address sent all of it: the range is cut out here.
                try await send(data.subdata(in: range.lowerBound..<(range.upperBound + 1)).base64EncodedString())
            } else {
                try await send(answer.data)
            }
            return
        }
        let first = try await fetch(part.url, first: 0, last: Self.piece - 1, frame: frame)
        try await send(first.data)
        guard first.status == 206, let whole = first.total, whole > first.length else { return }
        var at = first.length
        while at < whole {
            try Task.checkCancellation()
            let next = try await fetch(part.url, first: at, last: min(at + Self.piece, whole) - 1, frame: frame)
            guard next.length > 0 else { throw BrowserError.cutOff }
            try await send(next.data)
            at += next.length
        }
    }

    /// A whole video file, downloaded by the browser itself (its own download, with the
    /// page's cookies) into the one place the engine named for it.
    private func downloadWhole(_ media: FoundMedia, to path: URL, engineId: String) async throws {
        var request = URLRequest(url: media.url)
        request.setValue(address, forHTTPHeaderField: "Referer")
        let download = await web.startDownload(using: request)
        downloads[media.id] = download
        defer { downloads[media.id] = nil }
        let watch = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(1))
                guard let self, let model = self.model else { return }
                let progress = download.progress
                guard progress.totalUnitCount > 0 else { continue }
                let fraction = progress.fractionCompleted
                _ = try? await model.ask(
                    "browser.progress",
                    ["id": engineId, "progress": fraction, "size": Int(progress.totalUnitCount)], as: Empty.self)
                let size = ByteCountFormatter.string(fromByteCount: progress.totalUnitCount, countStyle: .file)
                self.say(media.id, "Saving: \(Int(fraction * 100))% of \(size). Keep this window open until it's done.", progress: fraction)
            }
        }
        defer { watch.cancel() }
        try await withTaskCancellationHandler {
            try await helper.finish(download, at: path)
        } onCancel: {
            Task { @MainActor in download.cancel() }
        }
    }

    // MARK: asking the page

    struct Fetched {
        let status: Int
        let data: String
        let length: Int
        let total: Int?
    }

    /// A piece of a video, fetched by the page. Asked first as the page's own player asks
    /// (2026-10-18: no cookies to another site, as players do; a site that lets any page
    /// read its parts refuses them to a page that sends its cookies), then with the
    /// page's cookies, and a piece of a file then as the whole part, should the site refuse
    /// a request for a piece. Each way is tried twice, a moment apart.
    private func fetch(_ url: URL, first: Int?, last: Int?, frame: String?) async throws -> Fetched {
        let ways: [(credentials: String, ranged: Bool)] =
            first == nil
            ? [("same-origin", false), ("include", false)]
            : [("same-origin", true), ("include", true), ("same-origin", false)]
        var lastError: Error = BrowserError.cutOff
        for round in 0..<2 {
            if round > 0 { try await Task.sleep(for: .seconds(2)) }
            for way in ways {
                do {
                    return try await fetchOnce(
                        url, first: way.ranged ? first : nil, last: way.ranged ? last : nil,
                        credentials: way.credentials, frame: frame)
                } catch let error as BrowserError {
                    switch error {
                    case .answered(let status) where status >= 400 && status < 500 && ![401, 403, 429].contains(status):
                        throw error  // the site says it isn't there: no other way changes that
                    case .tooBig where !way.ranged:
                        lastError = error
                    default:
                        lastError = error
                    }
                } catch is CancellationError {
                    throw CancellationError()
                } catch {
                    // The frame it was found in has gone: the page's main frame is asked instead.
                    if let frame { frames[frame] = nil }
                    lastError = BrowserError.refused(error.localizedDescription)
                }
            }
        }
        throw lastError
    }

    private func fetchOnce(
        _ url: URL, first: Int?, last: Int?, credentials: String, frame: String?
    ) async throws -> Fetched {
        let said = try await web.callAsyncJavaScript(
            BrowserScript.fetchPiece,
            arguments: [
                "url": url.absoluteString, "first": first.map { $0 as Any } ?? NSNull(),
                "last": last.map { $0 as Any } ?? NSNull(), "most": Self.most, "credentials": credentials,
            ],
            in: frame.flatMap { frames[$0] }, contentWorld: .page)
        guard let answer = said as? [String: Any] else { throw BrowserError.cutOff }
        if let failed = answer["failed"] as? String { throw BrowserError.refused(failed) }
        if answer["tooBig"] as? Bool == true { throw BrowserError.tooBig }
        let status = (answer["status"] as? NSNumber)?.intValue ?? 0
        guard status == 200 || status == 206, let data = answer["data"] as? String else {
            throw BrowserError.answered(status)
        }
        let range = answer["range"] as? String
        let total = range.flatMap { $0.split(separator: "/").last }.flatMap { Int($0) }
        return Fetched(
            status: status, data: data, length: (answer["length"] as? NSNumber)?.intValue ?? 0, total: total)
    }

    private func text(of url: URL, frame: String?) async throws -> String {
        let answer = try await fetch(url, first: nil, last: nil, frame: frame)
        guard let data = Data(base64Encoded: answer.data) else { throw StreamListError.notAList }
        return String(data: data, encoding: .utf8) ?? String(data: data, encoding: .isoLatin1) ?? ""
    }

    private func sizeOf(_ media: FoundMedia) async throws -> Int? {
        let said = try await web.callAsyncJavaScript(
            BrowserScript.sizeOf, arguments: ["url": media.url.absoluteString],
            in: media.frame.flatMap { frames[$0] }, contentWorld: .page)
        return (said as? NSNumber)?.intValue
    }
}

enum BrowserError: Error, LocalizedError {
    case cutOff
    case tooBig
    case refused(String)
    case answered(Int)
    case notVideo

    var errorDescription: String? {
        switch self {
        case .cutOff: "the page stopped sending it"
        case .tooBig: "a part of it was too big to take in one piece"
        case .refused(let why): "the site wouldn't let the page fetch it (\(why))"
        case .answered(let status) where status == 403 || status == 410:
            "the site refused it (HTTP \(status)): its address for the video may have run out. "
                + "Play the video on the page again, then Download"
        case .answered(let status): "the site answered with an error (HTTP \(status))"
        case .notVideo: "the site sent a web page, not the video"
        }
    }
}

struct BrowserStarted: Decodable {
    let id: String
    let filePath: String
}

struct PartTaken: Decodable {
    let bytes: Int
}

private struct Empty: Decodable {}

/// The window's go-between with WebKit: what the page's script sends, where the page goes,
/// windows a page opens (shown here instead), and the browser's own downloads.
@MainActor
final class BrowserHelper: NSObject, WKScriptMessageHandler, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    weak var browser: PageBrowser?
    private var places: [ObjectIdentifier: URL] = [:]
    private var waiting: [ObjectIdentifier: CheckedContinuation<Void, Error>] = [:]
    private var ended: [ObjectIdentifier: Error?] = [:]
    /// Why a download was turned down before it began, by the download.
    private var refusals: [ObjectIdentifier: Error] = [:]

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let browser, let said = message.body as? [String: Any], let kind = said["kind"] as? String else { return }
        let frame = said["frame"] as? String ?? message.frameInfo.request.url?.absoluteString ?? ""
        if kind == "found", let items = said["items"] as? [[String: Any]] {
            let found = items.compactMap { item -> (url: String, how: String, type: String?)? in
                guard let url = item["url"] as? String, let how = item["how"] as? String else { return nil }
                return (url, how, item["type"] as? String)
            }
            browser.found(found, frame: frame, info: message.frameInfo)
        } else {
            browser.noted(kind, detail: said["detail"] as? String ?? "")
        }
    }

    // MARK: where the page goes

    func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
        browser?.pageChanged()
        browser?.refresh(newPage: true)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { browser?.refresh() }
    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) { browser?.refresh() }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { browser?.refresh() }

    func webView(
        _ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse
    ) async -> WKNavigationResponsePolicy {
        // The page's own downloads aren't made: a video is saved from the list, by Bear Media.
        navigationResponse.canShowMIMEType || !navigationResponse.isForMainFrame ? .allow : .cancel
    }

    func webView(
        _ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction
    ) async -> WKNavigationActionPolicy {
        navigationAction.shouldPerformDownload ? .cancel : .allow
    }

    /// A page that opens a window of its own: shown in this one.
    func webView(
        _ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
        for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures
    ) -> WKWebView? {
        if navigationAction.targetFrame == nil { webView.load(navigationAction.request) }
        return nil
    }

    func webView(
        _ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
        initiatedByFrame frame: WKFrameInfo
    ) async {
        let alert = NSAlert()
        alert.messageText = message
        alert.runModal()
    }

    func webView(
        _ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
        initiatedByFrame frame: WKFrameInfo
    ) async -> Bool {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        return alert.runModal() == .alertFirstButtonReturn
    }

    // MARK: the browser's own downloads

    /// Wait for a download to finish into `path`.
    func finish(_ download: WKDownload, at path: URL) async throws {
        let key = ObjectIdentifier(download)
        places[key] = path
        download.delegate = self
        if let done = ended.removeValue(forKey: key) {
            if let error = done { throw error }
            return
        }
        try await withCheckedThrowingContinuation { waiting[key] = $0 }
    }

    func download(
        _ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String
    ) async -> URL? {
        // An error, or a web page in the video's place (an address that has run out often
        // answers with one): not saved at all, and said plainly (2026-10-18).
        let key = ObjectIdentifier(download)
        if let answer = response as? HTTPURLResponse, answer.statusCode >= 400 {
            refusals[key] = BrowserError.answered(answer.statusCode)
            return nil
        }
        let kind = (response.mimeType ?? "").lowercased()
        if kind.hasPrefix("text/html") || kind.hasPrefix("application/xhtml") {
            refusals[key] = BrowserError.notVideo
            return nil
        }
        return places[key]
    }

    func downloadDidFinish(_ download: WKDownload) { end(download, nil) }

    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        end(download, refusals.removeValue(forKey: ObjectIdentifier(download)) ?? error)
    }

    private func end(_ download: WKDownload, _ error: Error?) {
        let key = ObjectIdentifier(download)
        places[key] = nil
        if let waiter = waiting.removeValue(forKey: key) {
            if let error { waiter.resume(throwing: error) } else { waiter.resume() }
        } else {
            ended[key] = error
        }
    }
}

// MARK: - the window's page

struct PageBrowserView: View {
    @Bindable var browser: PageBrowser
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Button { browser.web.goBack() } label: { Image(systemName: "chevron.left") }
                    .disabled(!browser.canGoBack)
                    .help("Back")
                Button { browser.web.goForward() } label: { Image(systemName: "chevron.right") }
                    .disabled(!browser.canGoForward)
                    .help("Forward")
                Button {
                    if browser.loading { browser.web.stopLoading() } else { browser.web.reload() }
                } label: {
                    Image(systemName: browser.loading ? "xmark" : "arrow.clockwise")
                }
                .help(browser.loading ? "Stop loading" : "Load the page again")
                TextField("Address", text: $browser.typed)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { browser.go() }
            }
            .buttonStyle(.borderless)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            Divider()
            HSplitView {
                BrowserWebView(web: browser.web)
                    .frame(minWidth: 500, maxWidth: .infinity, maxHeight: .infinity)
                FoundVideosPanel(browser: browser)
                    .frame(minWidth: 300, idealWidth: 340, maxWidth: 460, maxHeight: .infinity)
            }
        }
        .dressed()
    }
}

struct BrowserWebView: NSViewRepresentable {
    let web: WKWebView
    func makeNSView(context: Context) -> WKWebView { web }
    func updateNSView(_ view: WKWebView, context: Context) {}
}

/// Beside the page: the videos found on it, and what's being saved.
struct FoundVideosPanel: View {
    @Bindable var browser: PageBrowser
    @Environment(AppModel.self) private var model

    var body: some View {
        let media = browser.ranked
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text("Videos on this page").font(.headline)
                Spacer()
                Button("Search Again") { browser.searchAgain() }
                    .help("Look for the video in every place on the page again")
            }
            Text(
                media.isEmpty
                    ? "Play the video on the page: Bear Media looks for it in the player, the page's "
                        + "requests and its code as it plays."
                    : "The best first. Choose one, then Download. Keep this window open until it's saved."
            )
            .font(.callout)
            .foregroundStyle(.secondary)
            if browser.findings.protected {
                Text("This page plays video protected against copying (DRM): that video can't be saved.")
                    .font(.callout)
                    .foregroundStyle(.orange)
            }
            TextField("Name", text: $browser.name)
                .textFieldStyle(.roundedBorder)
                .help("What the video is saved as")
            Picker("Save to", selection: $browser.place) {
                ForEach(KeptPlace.offered(isChild: model.profiles.current.isChild)) { Text($0.name).tag($0) }
            }
            Divider()
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    ForEach(media) { found in
                        row(found)
                        Divider()
                    }
                }
            }
        }
        .padding(12)
        .background(Theme.current.panel)
    }

    private func row(_ found: FoundMedia) -> some View {
        let save = browser.saves[found.id]
        let offered = browser.choices[found.id]
        return VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text(found.kindName).fontWeight(.medium)
                if browser.best == found.id {
                    Text("Highest quality")
                        .font(.caption.weight(.semibold))
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(.green.opacity(0.25), in: Capsule())
                }
                Spacer()
                if save?.running == true {
                    Button("Stop") { browser.stop(found.id) }
                } else {
                    Button("Download") { browser.save(found) }
                        .disabled(found.kind != .file && (offered ?? []).isEmpty)
                }
            }
            if let offered, offered.count > 1 {
                Picker("Quality", selection: Binding(
                    get: { browser.chosen[found.id] ?? offered[0].id },
                    set: { browser.chosen[found.id] = $0 })
                ) {
                    ForEach(offered) { Text(MediaQuality($0).label).tag($0.id) }
                }
                .fixedSize()
            } else if let known = browser.quality[found.id], browser.problems[found.id] == nil {
                // A file's (or a stream with one size): its height, how big, how long.
                Text(known.label).font(.callout)
            } else if found.kind != .file && browser.problems[found.id] == nil {
                Text("Reading the stream…").font(.callout).foregroundStyle(.secondary)
            }
            Text(found.url.host() ?? found.url.absoluteString)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .truncationMode(.middle)
                .help(found.url.absoluteString)
            Text("Found in " + found.found.sorted().joined(separator: ", "))
                .font(.caption)
                .foregroundStyle(.secondary)
            if let problem = browser.problems[found.id] {
                Text(problem).font(.callout).foregroundStyle(.secondary)
            }
            if let save {
                if let progress = save.progress, save.running {
                    ProgressView(value: progress).progressViewStyle(.linear)
                }
                Text(save.line)
                    .font(.callout)
                    .foregroundStyle(save.failed ? .red : .secondary)
                    .lineLimit(3)
            }
        }
    }
}

/// The videos being saved from browser windows: in Downloads, or (18+) on Videos → 18+.
struct BrowserSavesList: View {
    var adult = false
    @Environment(AppModel.self) private var model

    var body: some View {
        let saves = model.browserSaves.filter { $0.apart == adult }
        if !saves.isEmpty {
            VStack(spacing: 0) {
                ForEach(saves) { save in
                    HStack(spacing: 12) {
                        Image(systemName: "safari").foregroundStyle(.secondary).frame(width: 20)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(save.name).fontWeight(.medium).lineLimit(1)
                            Text("From the browser window · to \(save.place.name)")
                                .font(.callout).foregroundStyle(.secondary).lineLimit(1)
                        }
                        .frame(minWidth: 160, maxWidth: 320, alignment: .leading)
                        VStack(alignment: .leading, spacing: 3) {
                            if save.running {
                                if let progress = save.progress {
                                    ProgressView(value: progress).progressViewStyle(.linear)
                                } else {
                                    ProgressView().progressViewStyle(.linear)
                                }
                            }
                            Text(save.line)
                                .font(save.running ? .caption : .callout)
                                .foregroundStyle(save.failed ? .red : .secondary)
                                .lineLimit(2)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        Button {
                            if save.running { model.browserStops[save.id]?() } else { model.dismissBrowserSave(save.id) }
                        } label: {
                            Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary)
                        }
                        .buttonStyle(.plain)
                        .takesFirstClick(false)
                        .help(save.running ? "Stop saving this video" : "Take this off the list")
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 8)
                    Divider()
                }
            }
            .background(Theme.current.panel)
        }
    }
}
