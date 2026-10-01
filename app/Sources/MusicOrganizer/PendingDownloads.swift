import MusicOrganizerKit
import SwiftUI

/// The top of Discover → Downloads: what's on its way, and what didn't arrive. A
/// download shows here from the moment it's asked for until it's a song in the list
/// below; one that failed stays, with Try Again and a way to take it off the list.
struct PendingDownloads: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let pending = model.pending
        if !pending.isEmpty {
            VStack(spacing: 0) {
                ForEach(pending) { download in
                    row(download)
                    Divider()
                }
            }
            .background(.background.secondary)
        }
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
                    .help("Ask YouTube for it again")
            } else {
                VStack(alignment: .leading, spacing: 3) {
                    // The engine doesn't say how far along it is, only that it's busy.
                    ProgressView().progressViewStyle(.linear)
                    Text(download.progressNote).font(.caption).foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity)
            }
            Button {
                model.dismiss(download)
            } label: {
                Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
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
        let what = download.video ? "Video" + (download.height.map { ", \($0)p" } ?? "") : "Song"
        return download.artists.isEmpty ? what : "\(download.artistName) · \(what)"
    }
}
