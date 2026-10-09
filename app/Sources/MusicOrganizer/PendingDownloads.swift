import MusicOrganizerKit
import SwiftUI

/// The top of Discover → Downloads: what's on its way, and what didn't arrive. A
/// download shows here from the moment it's asked for until it's a song in the list
/// below; one that failed stays, with Try Again and a way to take it off the list.
///
/// Hundreds at once (Discover's Download Automatically) are listed shortly: the one
/// downloading, the next few in line, and a count of the rest with Cancel Waiting.
struct PendingDownloads: View {
    @Environment(AppModel.self) private var model
    @State private var cancelling = false

    var body: some View {
        let pending = model.pending
        if !pending.isEmpty {
            let shown = DownloadsShown(pending)
            let waiting = pending.filter { $0.isActive && !$0.isRunning }.count
            VStack(spacing: 0) {
                if waiting > 1 || model.downloadsHoldUp != nil {
                    summary(waiting: waiting, of: pending.filter(\.isActive).count)
                    Divider()
                }
                ForEach(shown.rows) { download in
                    row(download)
                    Divider()
                }
                if shown.moreWaiting > 0 || shown.moreEnded > 0 {
                    Text(Self.rest(waiting: shown.moreWaiting, ended: shown.moreEnded))
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 7)
                    Divider()
                }
            }
            .background(Theme.current.panel)
            .confirmationDialog(
                "Cancel the \(waiting) downloads still waiting?", isPresented: $cancelling
            ) {
                Button("Cancel \(waiting) Downloads", role: .destructive) {
                    model.cancelWaitingDownloads()
                }
                Button("Keep Downloading", role: .cancel) {}
            } message: {
                Text("The one downloading right now carries on. Songs that have arrived stay.")
            }
        }
    }

    /// How many are on their way, why they aren't moving if they aren't, and a way to
    /// call off the ones that haven't started.
    private func summary(waiting: Int, of active: Int) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text("\(active) \(active == 1 ? "download" : "downloads") on the way")
                    .fontWeight(.medium)
                if let holdUp = model.downloadsHoldUp {
                    Text(holdUp).font(.callout).foregroundStyle(.secondary)
                }
            }
            Spacer()
            if waiting > 1 {
                Button("Cancel Waiting…") { cancelling = true }
                    .help("Call off the \(waiting) downloads that haven't started")
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
    }

    static func rest(waiting: Int, ended: Int) -> String {
        var parts: [String] = []
        if waiting > 0 { parts.append("\(waiting) more waiting their turn") }
        if ended > 0 { parts.append("\(ended) more that didn't arrive") }
        return "and " + parts.joined(separator: ", and ")
    }

    private func row(_ download: PendingDownload) -> some View {
        HStack(spacing: 12) {
            Image(systemName: download.video ? "film" : "music.note")
                .foregroundStyle(.secondary)
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 2) {
                Text(download.name).fontWeight(.medium).lineLimit(1)
                Text(detail(download)).font(.callout).foregroundStyle(.secondary).lineLimit(1)
            }
            .frame(minWidth: 160, maxWidth: 320, alignment: .leading)
            if let problem = download.problem {
                Label(problem, systemImage: "exclamationmark.triangle.fill")
                    .font(.callout)
                    .foregroundStyle(.red)
                    .lineLimit(2)
                    .help(problem)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Button("Try Again") { model.retry(download) }
                    .help("Ask the service for it again")
            } else {
                VStack(alignment: .leading, spacing: 3) {
                    // A bar that fills while the engine can say how far along it is; a
                    // moving one while it waits its turn or works on what has arrived.
                    if let progress = download.progress, download.isRunning, progress < 1 {
                        ProgressView(value: progress).progressViewStyle(.linear)
                    } else {
                        ProgressView().progressViewStyle(.linear)
                    }
                    Text(download.progressNote)
                        .font(.caption)
                        .monospacedDigit()
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity)
            }
            Button {
                model.dismiss(download)
            } label: {
                Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
            // Never by the click that only brought the app forward: it can't be taken back.
            .takesFirstClick(false)
            .disabled(download.isRunning)
            .help(
                download.isRunning
                    ? "It's downloading right now"
                    : download.isActive ? "Cancel this download" : "Take this off the list")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
    }

    private func detail(_ download: PendingDownload) -> String {
        let what = download.kind
        return download.artists.isEmpty ? what : "\(download.artistName) · \(what)"
    }
}

/// Above them on Downloads: the movies being kept from torrents (2026-10-08), each with
/// how far along it is and a way to stop it; and the ones just kept, until they're taken
/// off the list. The same rows as a song on its way. Until now a keep showed only on
/// its movie's own page.
struct FilmKeeps: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let keeps = model.listedKeeps
        if !keeps.isEmpty {
            VStack(spacing: 0) {
                ForEach(keeps) { keep in
                    row(keep, model.filmKeeps[keep.infoHash])
                    Divider()
                }
            }
            .background(Theme.current.panel)
        }
    }

    private func row(_ keep: PendingKeeps.Keep, _ status: TorrentStatus?) -> some View {
        let keeping = status?.isKeeping ?? true  // not heard from yet: it's being asked for
        return HStack(spacing: 12) {
            Image(systemName: "popcorn").foregroundStyle(.secondary).frame(width: 20)
            VStack(alignment: .leading, spacing: 2) {
                Text(keep.name).fontWeight(.medium).lineLimit(1)
                Text("\(keep.kindName) · to Movies").font(.callout).foregroundStyle(.secondary).lineLimit(1)
            }
            .frame(minWidth: 160, maxWidth: 320, alignment: .leading)
            VStack(alignment: .leading, spacing: 3) {
                if keeping {
                    if let progress = status?.keepProgress {
                        ProgressView(value: progress).progressViewStyle(.linear)
                    } else {
                        ProgressView().progressViewStyle(.linear)
                    }
                }
                Text(status?.keepLine ?? "Finding the movie…")
                    .font(keeping ? .caption : .callout)
                    .monospacedDigit()
                    .foregroundStyle(status?.keepError != nil ? .red : .secondary)
                    .lineLimit(2)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Button {
                if keeping { model.stopKeepingFilm(keep.infoHash) } else { model.dismissKeep(keep.infoHash) }
            } label: {
                Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
            .takesFirstClick(false)
            .help(keeping ? "Stop keeping this movie" : "Take this off the list")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
    }
}
