import AppKit
import MusicOrganizerKit
import SwiftUI

/// Fix a song by hand: its names, its cover, its lyrics. The engine makes the change
/// (checked, journaled, undoable); this sheet only collects what the owner wants.
struct EditSheet: View {
    let track: Track
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss

    @State private var title = ""
    @State private var artist = ""
    @State private var albumArtist = ""
    @State private var album = ""
    @State private var genre = ""
    @State private var year = ""
    @State private var number = ""
    @State private var lyrics = ""
    @State private var lyricsAsLoaded: String?
    @State private var coverFile: URL?
    @State private var saving = false
    @State private var problem: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Edit Details").font(.headline)
            HStack(alignment: .top, spacing: 18) {
                VStack(spacing: 8) {
                    Group {
                        if let coverFile, let image = NSImage(contentsOf: coverFile) {
                            Image(nsImage: image).resizable().aspectRatio(contentMode: .fill)
                        } else {
                            CoverView(track: track, size: .medium, corner: 0)
                        }
                    }
                    .frame(width: 150, height: 150)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    Button("Choose Picture…", action: chooseCover)
                    if coverFile != nil {
                        Button("Keep the Old Cover") { coverFile = nil }.controlSize(.small)
                    }
                }
                Form {
                    TextField("Title", text: $title)
                    TextField("Artist", text: $artist)
                    TextField("Album", text: $album)
                    TextField("Album artist", text: $albumArtist)
                    TextField("Genre", text: $genre)
                    HStack {
                        TextField("Year", text: $year).frame(width: 130)
                        TextField("Track", text: $number).frame(width: 130)
                    }
                }
                .frame(width: 380)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text("Lyrics").font(.subheadline.weight(.medium))
                TextEditor(text: $lyrics)
                    .font(.body.monospaced())
                    .frame(height: 190)
                    .overlay(RoundedRectangle(cornerRadius: 6).stroke(.quaternary))
                    .disabled(lyricsAsLoaded == nil)
                Text("Type or paste the words. Lines that start with a time, like [01:23.45], "
                    + "light up as the song plays. Empty the box to remove the lyrics.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if let problem {
                Text(problem).foregroundStyle(.red).font(.callout).fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                if saving { ProgressView().controlSize(.small) }
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button("Save", action: save)
                    .keyboardShortcut(.defaultAction)
                    .disabled(saving || title.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(20)
        .frame(width: 600)
        .task {
            title = track.title
            artist = track.artist ?? ""
            albumArtist = track.albumArtist ?? ""
            album = track.album ?? ""
            genre = track.genre ?? ""
            year = track.year.map(String.init) ?? ""
            number = track.track.map(String.init) ?? ""
            let text = await model.lyricsText(for: track)
            lyrics = text
            lyricsAsLoaded = text
        }
    }

    private func chooseCover() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.jpeg, .png, .image]
        panel.allowsMultipleSelection = false
        panel.message = "Choose a picture for this song's cover. The picture itself isn't changed."
        if panel.runModal() == .OK { coverFile = panel.url }
    }

    private func save() {
        var changes: [String: Any] = [:]
        func text(_ key: String, _ new: String, _ old: String?) {
            let trimmed = new.trimmingCharacters(in: .whitespaces)
            if trimmed != (old ?? "") { changes[key] = trimmed.isEmpty ? NSNull() : trimmed }
        }
        text("title", title, track.title)
        text("artist", artist, track.artist)
        text("album_artist", albumArtist, track.albumArtist)
        text("album", album, track.album)
        text("genre", genre, track.genre)
        for (key, typed, old) in [("year", year, track.year), ("track", number, track.track)] {
            let trimmed = typed.trimmingCharacters(in: .whitespaces)
            if trimmed.isEmpty {
                if old != nil { changes[key] = NSNull() }
            } else if let value = Int(trimmed) {
                if value != old { changes[key] = value }
            } else {
                problem = "\(key.capitalized) should be a number."
                return
            }
        }
        let newLyrics = lyricsAsLoaded != nil && lyrics != lyricsAsLoaded ? lyrics : nil
        if changes.isEmpty && newLyrics == nil && coverFile == nil {
            dismiss()
            return
        }
        saving = true
        problem = nil
        Task {
            do {
                try await model.saveEdit(
                    of: track, changes: changes, lyrics: newLyrics, coverFile: coverFile)
                dismiss()
            } catch {
                problem = error.localizedDescription
                saving = false
            }
        }
    }
}
