import MusicOrganizerKit
import SwiftUI

/// The whole window given to the song that's playing: its cover, and its lyrics large
/// enough to read from across the room.
struct NowPlayingView: View {
    @Binding var isShown: Bool
    /// False when it's a page of its own (the Local Visualizer), not laid over the library.
    var closable = true
    @Environment(AppModel.self) private var model

    var body: some View {
        let track = model.player.current
        ZStack(alignment: .topLeading) {
            HStack(spacing: 40) {
                VStack(spacing: 14) {
                    CoverView(track: track, size: .large, corner: 12)
                        .frame(maxWidth: 420, maxHeight: 420)
                        .shadow(color: .black.opacity(0.5), radius: 24, y: 10)
                    if let track {
                        VStack(spacing: 4) {
                            Text(track.title).font(.title.weight(.bold)).multilineTextAlignment(.center)
                            Text(track.artistName).font(.title3).foregroundStyle(.secondary)
                            if !track.albumName.isEmpty {
                                Text(track.albumName).font(.callout).foregroundStyle(.tertiary)
                            }
                        }
                        FavouriteButton(track: track)
                            .font(.title2)
                    } else {
                        Text("Nothing playing").font(.title2).foregroundStyle(.secondary)
                    }
                }
                .frame(maxWidth: .infinity)
                LyricsView(large: true)
                    .frame(maxWidth: .infinity)
            }
            .padding(.horizontal, 40)
            .padding(.top, 52)
            .padding(.bottom, 24)
            if closable {
                Button { isShown = false } label: {
                    Image(systemName: "chevron.down.circle.fill")
                        .font(.title)
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
                .keyboardShortcut(.cancelAction)
                .help("Back to the library (Esc)")
                .padding(.leading, 16)
                .padding(.top, 44)  // below the window's close, minimise and zoom buttons
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background { backdrop(track) }
        .clipped()
        .environment(\.colorScheme, .dark)
    }

    /// The cover, blurred right out, under a dark wash: the screen takes the album's colour.
    private func backdrop(_ track: Track?) -> some View {
        // Color.black sets the size; the blurred cover only fills it, and is clipped to it.
        Color.black
            .overlay {
                CoverView(track: track, size: .small, corner: 0)
                    .scaledToFill()
                    .blur(radius: 80)
                    .opacity(0.55)
            }
            .clipped()
    }
}
