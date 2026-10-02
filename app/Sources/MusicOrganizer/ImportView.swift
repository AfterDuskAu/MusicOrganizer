import MusicOrganizerKit
import SwiftUI

/// Discover → Import Playlists: choose a playlist (a YouTube link, or one of your
/// Spotify playlists), see which of its songs you have and which were found on YouTube
/// Music, and download the rest in one go. They arrive in a playlist of the same name.
///
/// Built so far: YouTube and YouTube Music playlists by their link (no sign-in), and
/// Spotify once signed in (Settings → Accounts). Apple Music comes next, through the
/// same list and the same button.
struct ImportView: View {
    @Environment(AppModel.self) private var model
    @AppStorage("importSource") private var source = Source.youtube
    @AppStorage("importLink") private var link = ""
    @State private var spotifyChoice = ""

    enum Source: String, CaseIterable, Identifiable {
        case youtube, spotify

        var id: String { rawValue }
        var title: String {
            switch self {
            case .youtube: "YouTube link"
            case .spotify: "Spotify"
            }
        }
    }

    var body: some View {
        let page = model.importing
        VStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .firstTextBaseline, spacing: 12) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Import Playlists").font(.title2.weight(.semibold))
                        Text(
                            "Bring a playlist across. Its songs are found on YouTube Music and "
                                + "downloaded into a playlist of the same name here."
                        )
                        .foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    Picker("From", selection: $source) {
                        ForEach(Source.allCases) { Text($0.title).tag($0) }
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                    .fixedSize()
                    .disabled(page.isBusy)
                }
                switch source {
                case .youtube: youtube(page)
                case .spotify: spotify(page)
                }
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

    // MARK: where from

    @ViewBuilder
    private func youtube(_ page: ImportPage) -> some View {
        HStack(spacing: 10) {
            TextField("A YouTube or YouTube Music playlist's link (Share → Copy link)", text: $link)
                .textFieldStyle(.roundedBorder)
                .onSubmit { page.open(.youtube(link: link)) }
            Button("Read Playlist", systemImage: "list.bullet.rectangle") {
                page.open(.youtube(link: link))
            }
            .keyboardShortcut(.defaultAction)
            .disabled(page.isBusy || link.trimmingCharacters(in: .whitespaces).isEmpty)
        }
        caption("The playlist has to be Public or Unlisted: nothing is signed in to.")
    }

    @ViewBuilder
    private func spotify(_ page: ImportPage) -> some View {
        if model.accounts?.spotify.signedIn == true {
            let lists = page.spotifyPlaylists
            HStack(spacing: 10) {
                if page.listing {
                    ProgressView().controlSize(.small)
                    Text("Asking Spotify for your playlists…").foregroundStyle(.secondary)
                } else if lists.isEmpty {
                    Text(page.listProblem ?? "No playlists were found.")
                        .foregroundStyle(page.listProblem == nil ? Color.secondary : Color.orange)
                        .frame(maxWidth: .infinity, alignment: .leading)
                } else {
                    Picker("Playlist", selection: $spotifyChoice) {
                        ForEach(lists) { list in
                            Text(list.label).tag(list.id).disabled(!list.readable)
                        }
                    }
                    .labelsHidden()
                    .frame(maxWidth: 420)
                    Button("Read Playlist", systemImage: "list.bullet.rectangle") {
                        page.open(.spotify(playlistId: spotifyChoice))
                    }
                    .keyboardShortcut(.defaultAction)
                    .disabled(page.isBusy || lists.first { $0.id == spotifyChoice }?.readable != true)
                }
                Spacer(minLength: 0)
                Button("Refresh", systemImage: "arrow.clockwise") { page.listSpotifyPlaylists() }
                    .disabled(page.listing || page.isBusy)
                    .help("Ask Spotify for your playlists again")
            }
            .task {
                // Asked once, when Spotify is first chosen; after that only when told to.
                if page.spotifyPlaylists.isEmpty, page.listProblem == nil { page.listSpotifyPlaylists() }
            }
            .onChange(of: lists) { chooseFirst(lists) }
            .onAppear { chooseFirst(lists) }
            caption(
                "Signed in as \(model.accounts?.spotify.name ?? "your Spotify account"). Each song "
                    + "is looked up on YouTube Music, a couple of seconds apiece.")
        } else {
            HStack(spacing: 10) {
                Text("Spotify isn't signed in to yet.")
                SettingsLink { Text("Open Settings…") }
                    // Settings opens on its Accounts tab.
                    .simultaneousGesture(
                        TapGesture().onEnded {
                            UserDefaults.standard.set("accounts", forKey: SettingsView.tabKey)
                        })
                Spacer(minLength: 0)
            }
            caption(
                "Settings → Accounts has the steps: your own free app at Spotify, which needs "
                    + "your account to have Premium, then a sign-in on Spotify's own page.")
        }
    }

    /// Keep the choice on a playlist that's there and can be read.
    private func chooseFirst(_ lists: [SpotifyPlaylist]) {
        if lists.first(where: { $0.id == spotifyChoice })?.readable != true {
            spotifyChoice = lists.first { $0.readable }?.id ?? ""
        }
    }

    private func caption(_ words: String) -> some View {
        Text(words)
            .font(.callout)
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: what was read

    @ViewBuilder
    private func content(_ page: ImportPage) -> some View {
        switch page.phase {
        case .idle:
            Message(
                symbol: "square.and.arrow.down.on.square", title: "Bring a playlist across",
                text: "Choose a playlist above. You'll see which of its songs you already have "
                    + "and which were found, before anything is downloaded."
            ) {}
        case .reading:
            VStack(spacing: 12) {
                ProgressView()
                Text("Reading the playlist…")
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        case .failed(let why):
            Message(symbol: "exclamationmark.triangle", title: "That didn't work", text: why) {
                Button("Try Again") { again(page) }
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

    private func again(_ page: ImportPage) {
        switch source {
        case .youtube: page.open(.youtube(link: link))
        case .spotify: page.open(.spotify(playlistId: spotifyChoice))
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
                    if page.tooLong {
                        Text("It's longer than can be read at once: these are its first songs.")
                            .font(.callout)
                            .foregroundStyle(.orange)
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
