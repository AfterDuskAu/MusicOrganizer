import MusicOrganizerKit
import SwiftUI

/// Search YouTube Music: play anything straight away, and download a song into the
/// library only when its Download button is clicked.
struct YouTubeSearchView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        VStack(spacing: 0) {
            HStack {
                Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                TextField("Search YouTube Music for a song, artist or album", text: $model.youtubeQuery)
                    .textFieldStyle(.plain)
                    .font(.title3)
                    .onSubmit { model.searchYouTube() }
                if model.youtubeSearching {
                    ProgressView().controlSize(.small)
                } else {
                    Button("Search") { model.searchYouTube() }
                        .disabled(model.youtubeQuery.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
            .padding(12)
            Divider()
            if let problem = model.youtubeProblem {
                Text(problem)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if model.youtubeResults.isEmpty {
                Text("Search, then press play to listen. Nothing is saved unless you click Download.")
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(40)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                let results = model.youtubeResults
                List {
                    ForEach(Array(results.enumerated()), id: \.element.id) { index, result in
                        ResultRow(result: result) {
                            model.player.play(results.map(\.track), startAt: index)
                        }
                    }
                    if model.youtubeHasMore {
                        HStack {
                            Spacer()
                            if model.youtubeSearching {
                                ProgressView().controlSize(.small)
                            } else {
                                Button("Show \(AppModel.youtubeStep) More", systemImage: "chevron.down") {
                                    model.moreFromYouTube()
                                }
                            }
                            Spacer()
                        }
                        .padding(.vertical, 6)
                    }
                }
            }
        }
    }
}

private struct ResultRow: View {
    let result: SearchResult
    let play: () -> Void
    @Environment(AppModel.self) private var model

    var body: some View {
        let track = result.track
        let playing = model.player.current?.id == track.id
        HStack(spacing: 10) {
            Button(action: play) {
                ZStack {
                    CoverView(track: track, size: .small, corner: 4)
                        .frame(width: 40, height: 40)
                    Image(systemName: playing ? "speaker.wave.2.fill" : "play.fill")
                        .foregroundStyle(.white)
                        .shadow(radius: 3)
                }
            }
            .buttonStyle(.plain)
            .help("Play from YouTube Music")
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(result.title).fontWeight(playing ? .semibold : .regular).lineLimit(1)
                    if model.heard.contains(result.videoId) { HeardMark() }
                    if result.isExplicit == true {
                        Image(systemName: "e.square.fill").foregroundStyle(.secondary)
                    }
                }
                Text([track.artistName, result.album].compactMap { $0 }.joined(separator: " · "))
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 8)
            Text(clockTime(result.durationS))
                .monospacedDigit()
                .foregroundStyle(.secondary)
            status
                .frame(width: 130, alignment: .trailing)
        }
        .padding(.vertical, 3)
        .contentShape(Rectangle())
        .onTapGesture(count: 2, perform: play)
    }

    @ViewBuilder
    private var status: some View {
        if model.everything.videoIDs.contains(result.videoId) {
            Label("In your library", systemImage: "checkmark.circle.fill")
                .foregroundStyle(.green)
                .font(.callout)
        } else {
            switch model.downloadState(of: result.videoId) {
            case .working:
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small)
                    Text(model.downloadNote(of: result.videoId))
                        .font(.callout)
                        .monospacedDigit()
                        .foregroundStyle(.secondary)
                }
            case .failed(let why):
                Button("Try Again", systemImage: "exclamationmark.triangle") { model.download(result) }
                    .help(why)
            case nil:
                Button("Download", systemImage: "arrow.down.circle") { model.download(result) }
                    .help("Save this song in your library")
            }
        }
    }
}
