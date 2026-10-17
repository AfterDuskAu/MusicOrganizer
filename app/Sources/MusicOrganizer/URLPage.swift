import MusicOrganizerKit
import SwiftUI

/// Media Discovery → URL (the owner, 2026-10-17: "add in a "URL" which would get the video
/// from the page from any website, shows which videos are on the page, once selected and
/// quality are selected. use yt-dlp engine to download"). An address is pasted; the
/// engine reads the page (`page.videos`) and lists its videos; one is chosen, then its
/// quality and the folder it goes in, and Download keeps it as a film is kept
/// (`page.keep`): listed in Downloads while it comes, or for 18+ on Videos → 18+ alone.
///
/// A video from the music service on a page goes through the queue, into Videos, as
/// Video Finder's Download does; SoundCloud's are left to Music Finder. Nothing typed or
/// found here is remembered. Not in a child's profile. The layout is mine, made of the
/// app's own pieces, until the owner's drawing of it comes.
struct PageVideosView: View {
    @Environment(AppModel.self) private var model
    @State private var typed = ""
    @State private var pageTitle: String?
    @State private var note: String?
    @State private var videos: [PageVideo] = []
    @State private var looked = false
    @State private var looking = false
    @State private var problem: String?
    /// The page last looked at: what Open in Browser opens.
    @State private var lookedAt: URL?
    @State private var chosen: PageVideo.ID?
    /// Players from other sites whose qualities are being read, and what went wrong.
    @State private var reading: Set<PageVideo.ID> = []
    @State private var readProblems: [PageVideo.ID: String] = [:]
    @State private var qualities: [PageVideo.ID: PageQuality.ID] = [:]
    /// What each Download started, by its keep's id.
    @State private var started: [PageVideo.ID: String] = [:]
    /// Where the last video went: the next is offered the same folder.
    @AppStorage("urlPagePlace") private var placeKey = KeptPlace.video.rawValue

    var body: some View {
        VStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("URL").font(.title2.weight(.semibold)).heading()
                    Text(
                        "Paste the address of a web page to see the videos on it. Choose one, "
                            + "its quality and where it goes, then click Download."
                    )
                    .font(.callout)
                    .foregroundStyle(.secondary)
                }
                HStack(spacing: 8) {
                    TextField("https://", text: $typed)
                        .textFieldStyle(.roundedBorder)
                        .onSubmit(look)
                    Button("Find Videos", action: look)
                        .keyboardShortcut(.defaultAction)
                        .disabled(address == nil || looking)
                }
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 12)
            Divider()
            content
        }
    }

    @ViewBuilder private var content: some View {
        if looking {
            ProgressView("Reading the page…")
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if let problem {
            VStack(spacing: 14) {
                Text(problem)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                openInBrowser(prominent: true)
            }
            .padding(.horizontal, 40)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if videos.isEmpty {
            VStack(spacing: 14) {
                Text(looked ? note ?? "No video was found on that page." : "The videos on the page show up here.")
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                if looked { openInBrowser(prominent: true) }
            }
            .padding(.horizontal, 40)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            VStack(alignment: .leading, spacing: 0) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(pageTitle ?? "On this page").font(.headline).lineLimit(1)
                    Text(videos.count == 1 ? "1 video" : "\(videos.count) videos")
                        .foregroundStyle(.secondary)
                    Spacer()
                    openInBrowser(prominent: false)
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 10)
                if let note {
                    Text(note)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 20)
                        .padding(.bottom, 8)
                }
                List(videos) { video in
                    row(video)
                }
                .scrollContentBackground(Theme.current.listBackground)
            }
        }
    }

    // MARK: one video

    private func row(_ video: PageVideo) -> some View {
        let open = chosen == video.id
        return HStack(alignment: .top, spacing: 14) {
            Color.clear
                .frame(width: 128, height: 72)
                .overlay {
                    if video.thumbnail != nil {
                        WebPicture(address: video.thumbnail)
                    } else {
                        Image(systemName: "film").font(.title2).foregroundStyle(.secondary)
                    }
                }
                .background(.quaternary.opacity(0.5))
                .clipShape(RoundedRectangle(cornerRadius: 6))
                .overlay(alignment: .bottomTrailing) {
                    if !video.length.isEmpty {
                        Text(video.length)
                            .font(.caption.weight(.medium))
                            .monospacedDigit()
                            .foregroundStyle(.white)
                            .padding(.horizontal, 5)
                            .padding(.vertical, 2)
                            .background(.black.opacity(0.75), in: RoundedRectangle(cornerRadius: 4))
                            .padding(4)
                    }
                }
            VStack(alignment: .leading, spacing: 6) {
                Text(video.name).font(.body.weight(.medium)).lineLimit(2)
                if let from = video.from {
                    Text(from).font(.callout).foregroundStyle(.secondary)
                }
                if open { choices(video) }
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
        .onTapGesture { choose(video) }
        .takesFirstClick()
    }

    /// What's under the video chosen: its quality, where it goes, and Download.
    @ViewBuilder private func choices(_ video: PageVideo) -> some View {
        if video.isMusicService {
            Text("From the music service: it's downloaded through the queue, into Videos, as Video Finder's are.")
                .font(.callout)
                .foregroundStyle(.secondary)
            if let videoId = video.videoId {
                let working = model.downloadState(of: videoId) == .working
                Button(working ? "Downloading…" : "Download") {
                    model.keepVideo(
                        SearchResult(
                            videoId: videoId, title: video.saveName(page: pageTitle), artists: [],
                            durationS: video.durationS, thumbnail: video.thumbnail))
                }
                .disabled(working)
            }
        } else if video.isElsewhere {
            Text("SoundCloud's tracks are found and downloaded in Music Finder.")
                .font(.callout)
                .foregroundStyle(.secondary)
        } else if reading.contains(video.id) {
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text("Finding its qualities…").font(.callout).foregroundStyle(.secondary)
            }
        } else if let why = readProblems[video.id] {
            Text(why).font(.callout).foregroundStyle(.secondary)
        } else if let offered = video.qualities, !offered.isEmpty {
            let quality = Binding(
                get: { qualities[video.id] ?? offered[0].id },
                set: { qualities[video.id] = $0 })
            let place = Binding(
                get: { KeptPlace(rawValue: placeKey) ?? .video },
                set: { placeKey = $0.rawValue })
            HStack(spacing: 12) {
                Picker("Quality", selection: quality) {
                    ForEach(offered) { Text($0.described).tag($0.id) }
                }
                .fixedSize()
                Picker("Save to", selection: place) {
                    ForEach(KeptPlace.offered(isChild: model.profiles.current.isChild)) {
                        Text($0.name).tag($0)
                    }
                }
                .fixedSize()
                Button("Download") {
                    let picked = offered.first { $0.id == quality.wrappedValue } ?? offered[0]
                    started[video.id] = model.keepPageVideo(
                        video, quality: picked, title: video.saveName(page: pageTitle),
                        place: place.wrappedValue)
                }
            }
            if let id = started[video.id] { progress(id) }
        } else {
            Text("No size of it can be downloaded.").font(.callout).foregroundStyle(.secondary)
        }
    }

    /// How a Download started here is going. One going to 18+ is followed on Videos →
    /// 18+ only, as every download for adults is (2026-10-17).
    @ViewBuilder private func progress(_ id: String) -> some View {
        if model.keep(id)?.isApart == true {
            Text("Downloading to 18+: follow it on Videos → 18+.")
                .font(.callout)
                .foregroundStyle(.secondary)
        } else {
            let status = model.filmKeeps[id]
            Text(status?.keepLine ?? "Starting…")
                .font(.callout)
                .monospacedDigit()
                .foregroundStyle(status?.keepError != nil ? .red : .secondary)
                .lineLimit(2)
        }
    }

    /// Open the page in Bear Media's browser window (the owner, 2026-10-18): where a page
    /// that turned the reader away is shown as in any browser, and its video looked for as
    /// it plays. Not for the music service's pages or SoundCloud's, which aren't saved there.
    @ViewBuilder private func openInBrowser(prominent: Bool) -> some View {
        if let page = lookedAt, !Self.isService(page) {
            if prominent {
                VStack(spacing: 6) {
                    Button("Open in Browser") { model.openBrowser(page) }
                        .buttonStyle(.borderedProminent)
                    Text("The page opens in Bear Media's own browser window. Play its video there, and choose it from the list beside the page.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                }
            } else {
                Button("Open in Browser") { model.openBrowser(page) }
                    .help("Not the video you wanted? Open the page in Bear Media's browser window and choose it as it plays.")
            }
        }
    }

    private static func isService(_ url: URL) -> Bool {
        let host = (url.host() ?? "").lowercased()
        return ["youtube.com", "youtu.be", "youtube-nocookie.com", "soundcloud.com"].contains {
            host == $0 || host.hasSuffix("." + $0)
        }
    }

    // MARK: the workings

    /// What's typed, if it's a web address (one without its "https://" is given one).
    private var address: String? {
        let text = typed.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !text.contains(" ") else { return nil }
        if text.lowercased().hasPrefix("http://") || text.lowercased().hasPrefix("https://") {
            return text
        }
        return text.contains(".") ? "https://" + text : nil
    }

    private func look() {
        guard let address, !looking else { return }
        looking = true
        problem = nil
        lookedAt = URL(string: address)
        Task {
            defer { looking = false }
            do {
                show(try await model.lookUpPage(address))
            } catch {
                problem = error.localizedDescription
                videos = []
            }
            looked = true
        }
    }

    private func show(_ answer: PageAnswer) {
        pageTitle = answer.title
        note = answer.note
        videos = answer.videos
        readProblems = [:]
        qualities = [:]
        started = [:]
        // One video: it's chosen already.
        chosen = nil
        if answer.videos.count == 1, let only = answer.videos.first { choose(only) }
    }

    /// A click on a video: it's chosen, and a player from another site is read for its
    /// qualities (one look-up, at the click). One that turns out to be a list of videos
    /// itself is opened as a page of its own.
    private func choose(_ video: PageVideo) {
        chosen = video.id
        guard video.qualities == nil, video.service == nil, !reading.contains(video.id),
            readProblems[video.id] == nil
        else { return }
        reading.insert(video.id)
        Task {
            defer { reading.remove(video.id) }
            do {
                let found = try await model.lookUpPage(video.url)
                if found.videos.count == 1, let one = found.videos.first {
                    let read = video.read(one)
                    if let at = videos.firstIndex(where: { $0.id == video.id }) {
                        videos[at] = read
                        chosen = read.id
                    }
                } else if found.videos.isEmpty {
                    readProblems[video.id] = found.note ?? "No video was found there."
                } else {
                    typed = video.url
                    show(found)
                }
            } catch {
                readProblems[video.id] = error.localizedDescription
            }
        }
    }
}
