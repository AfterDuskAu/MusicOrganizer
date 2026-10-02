import MusicOrganizerKit
import SwiftUI

/// Discover → Import Playlists: paste a playlist's link, see which of its songs you
/// have and which were found on YouTube Music, and download the rest in one go. They
/// arrive in a playlist of the same name.
///
/// Built so far: YouTube and YouTube Music playlists, by their link (no sign-in).
/// Spotify and Apple Music come next, through the same list and the same button.
struct ImportView: View {
    @Environment(AppModel.self) private var model
    @AppStorage("importLink") private var link = ""

    var body: some View {
        let page = model.importing
        VStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Import Playlists").font(.title2.weight(.semibold))
                    Text(
                        "Bring a playlist across. Its songs are found on YouTube Music and "
                            + "downloaded into a playlist of the same name here."
                    )
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                HStack(spacing: 10) {
                    TextField(
                        "A YouTube or YouTube Music playlist's link (Share → Copy link)", text: $link
                    )
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { page.open(link) }
                    Button("Read Playlist", systemImage: "list.bullet.rectangle") { page.open(link) }
                        .keyboardShortcut(.defaultAction)
                        .disabled(page.isBusy || link.trimmingCharacters(in: .whitespaces).isEmpty)
                }
                Text(
                    "The playlist has to be Public or Unlisted: nothing is signed in to. Spotify "
                        + "and Apple Music aren't built yet."
                )
                .font(.callout)
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 12)
            if let note = page.note {
                Divider()
                ImportNote(note: note) { page.note = nil }
            }
            Divider()
            content(page)
        }
    }

    @ViewBuilder
    private func content(_ page: ImportPage) -> some View {
        switch page.phase {
        case .idle:
            Message(
                symbol: "square.and.arrow.down.on.square", title: "Bring a playlist across",
                text: "Paste a playlist's link above. You'll see which of its songs you already "
                    + "have and which were found, before anything is downloaded."
            ) {}
        case .reading:
            VStack(spacing: 12) {
                ProgressView()
                Text("Reading the playlist…")
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        case .failed(let why):
            Message(symbol: "exclamationmark.triangle", title: "That didn't work", text: why) {
                Button("Try Again") { page.open(link) }
            }
        case .finding, .ready:
            VStack(spacing: 0) {
                summary(page)
                Divider()
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(page.rows) { row in
                            ImportRowView(
                                row: row,
                                ticked: page.ticked.contains(row.id),
                                tick: { on in
                                    if on { page.ticked.insert(row.id) } else { page.ticked.remove(row.id) }
                                })
                            Divider().padding(.leading, 62)
                        }
                    }
                }
            }
        }
    }

    /// The playlist's name, the sums, and the one button.
    private func summary(_ page: ImportPage) -> some View {
        let counts = page.counts
        let songs = page.toDownload.count
        return HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(page.name).font(.title3.weight(.semibold)).lineLimit(1)
                if page.phase == .finding {
                    ProgressView(value: Double(page.done), total: Double(max(page.rows.count, 1)))
                        .frame(maxWidth: 260)
                    Text("Finding its songs on YouTube Music: \(page.done) of \(page.rows.count)")
                        .font(.callout)
                        .monospacedDigit()
                        .foregroundStyle(.secondary)
                } else {
                    Text(Imports.summary(counts, ticked: min(page.ticked.count, counts.unsure)))
                        .font(.callout)
                        .foregroundStyle(.secondary)
                    if let problem = page.problem {
                        Text(problem).font(.callout).foregroundStyle(.orange)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if page.phase == .ready, counts.waiting > 0 {
                Button("Carry On") { page.carryOn() }
                    .help("Look for the \(counts.waiting) songs that haven't been looked for yet")
            }
            if page.downloading {
                ProgressView().controlSize(.small)
            }
            Button(
                songs > 0 ? "Download Automatically (\(songs))" : "Make the Playlist",
                systemImage: songs > 0 ? "arrow.down.circle" : "music.note.list"
            ) {
                page.downloadAutomatically()
            }
            .controlSize(.large)
            .disabled(page.phase != .ready || page.downloading || (songs == 0 && counts.owned == 0))
            .help(
                songs > 0
                    ? "Makes your playlist \"\(page.name)\" and downloads these \(songs) songs into "
                        + "it, with nothing more to click. They count towards your daily limit."
                    : "Makes your playlist \"\(page.name)\" from the songs you already have")
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 10)
    }
}

/// One song of the playlist: what it's called there, and what it is to the owner.
private struct ImportRowView: View {
    let row: ImportRow
    let ticked: Bool
    let tick: (Bool) -> Void
    @Environment(AppModel.self) private var model
    @State private var hovering = false

    var body: some View {
        let candidate = row.found?.candidate
        HStack(spacing: 12) {
            Group {
                if let candidate {
                    CoverView(track: candidate.result.track, size: .small, corner: 4)
                } else {
                    RoundedRectangle(cornerRadius: 4).fill(.quaternary)
                        .overlay { Image(systemName: "music.note").foregroundStyle(.secondary) }
                }
            }
            .frame(width: 30, height: 30)
            VStack(alignment: .leading, spacing: 1) {
                Text(row.track.title).lineLimit(1)
                Text(row.track.artistName).font(.callout).foregroundStyle(.secondary).lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            state
        }
        .padding(.horizontal, 20)
        .frame(height: 44)
        .background(hovering ? AnyShapeStyle(.quaternary.opacity(0.5)) : AnyShapeStyle(.clear))
        .contentShape(Rectangle())
        .onHover { hovering = $0 }
        .onTapGesture(count: 2) {
            // Hear what was found before downloading it. Nothing is saved.
            if let candidate { model.player.play([candidate.result.track], startAt: 0) }
        }
        .help(candidate == nil ? "" : "Double-click to play what was found, from YouTube Music")
    }

    @ViewBuilder
    private var state: some View {
        switch row.found?.kind {
        case nil:
            Text("Waiting").foregroundStyle(.tertiary)
        case .owned:
            Label("In your library", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
        case .queued:
            Label("On its way", systemImage: "arrow.down.circle").foregroundStyle(.secondary)
        case .found:
            Label("Will download", systemImage: "arrow.down.circle.fill")
                .foregroundStyle(Color.accentColor)
        case .unsure:
            VStack(alignment: .trailing, spacing: 1) {
                Toggle(
                    "Download anyway",
                    isOn: Binding(get: { ticked }, set: { tick($0) })
                )
                .toggleStyle(.checkbox)
                Text(unsureWords).font(.caption).foregroundStyle(.orange).lineLimit(1)
            }
        case .notFound:
            Label("Not found", systemImage: "questionmark.circle").foregroundStyle(.secondary)
        }
    }

    /// Why it isn't certain, and what the likeliest song is called.
    private var unsureWords: String {
        let why = row.found?.why ?? "Not a certain match."
        guard let candidate = row.found?.candidate else { return why }
        return "\(why) Found: \(candidate.title), \(candidate.artistName)"
    }
}

/// What's said once an import's downloads are queued, or what went wrong.
private struct ImportNote: View {
    let note: ImportPage.Note
    let close: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            switch note {
            case .started:
                Image(systemName: "arrow.down.circle.fill").foregroundStyle(Color.accentColor)
            case .failed:
                Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
            }
            // The words take the room there is and wrap inside it.
            Text(words).frame(maxWidth: .infinity, alignment: .leading)
            Button(action: close) {
                Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
            .help("Close this note. The downloads carry on.")
        }
        .font(.callout)
        .padding(.horizontal, 20)
        .padding(.vertical, 9)
        .background(.background.secondary)
    }

    private var words: String {
        switch note {
        case .started(let words): words
        case .failed(let why): why
        }
    }
}
