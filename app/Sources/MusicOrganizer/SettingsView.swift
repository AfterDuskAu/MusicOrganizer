import MusicOrganizerKit
import SwiftUI

/// The Settings window (the cog wheel, or ⌘,), laid out as the owner designed it
/// (docs/roadmap/0.2-app-layout.md). A row marked "Coming" is planned but not built.
struct SettingsView: View {
    /// The tab that's showing: remembered, and set by a page that sends the owner here
    /// for one thing (Import Playlists → Spotify opens Accounts).
    @AppStorage(SettingsView.tabKey) private var tab = "general"

    static let tabKey = "settingsTab"

    var body: some View {
        TabView(selection: $tab) {
            GeneralSettings().tabItem { Label("General", systemImage: "gearshape") }.tag("general")
            QualitySettings().tabItem { Label("Quality", systemImage: "waveform") }.tag("quality")
            AccountSettings().tabItem { Label("Accounts", systemImage: "person.crop.circle") }
                .tag("accounts")
            LyricsSettings().tabItem { Label("Lyrics", systemImage: "quote.bubble") }.tag("lyrics")
        }
        .frame(width: 560)
    }
}

/// A small grey explanation under a setting.
private struct SideNote: View {
    let text: String

    init(_ text: String) { self.text = text }

    var body: some View {
        Text(text)
            .font(.callout)
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
    }
}

private struct ComingBadge: View {
    var body: some View {
        Text("Coming")
            .font(.caption.weight(.medium))
            .padding(.horizontal, 7)
            .padding(.vertical, 2)
            .background(.quaternary, in: Capsule())
            .foregroundStyle(.secondary)
    }
}

private struct GeneralSettings: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        Form {
            Picker("Songs and videos you download go to", selection: $model.keepDownloadsSeparate) {
                Text("Discover Downloads").tag(true)
                Text("All Library").tag(false)
            }
            .pickerStyle(.radioGroup)
            SideNote(
                "Discover Downloads keeps what you download apart from your main library, under "
                    + "Discover → Downloads, so you can sort it later. All Library also shows "
                    + "the songs in Songs, Artists, Recently Added and the rest, and the videos "
                    + "under Library → Videos. You can switch at any time; no file is moved "
                    + "either way. On disk, songs are in the library's Music folder by artist "
                    + "and album, and videos in Music/Videos.")
            LabeledContent("Library folder") {
                VStack(alignment: .leading, spacing: 4) {
                    Text(model.root?.path ?? "Not chosen").textSelection(.enabled)
                    Button("Choose Another…") { model.chooseLibrary() }
                }
            }
        }
        .formStyle(.grouped)
    }
}

private struct QualitySettings: View {
    @Environment(AppModel.self) private var model
    /// The limit moves in steps of this many.
    private static let step = 50

    var body: some View {
        let settings = model.engineSettings
        Form {
            Section {
                Picker("Download quality", selection: .constant(128)) {
                    Text("128 kbps").tag(128)
                    Text("256 kbps (coming)").tag(256)
                }
                .pickerStyle(.radioGroup)
                .disabled(true)
                SideNote(
                    "Standard is 128 kbps AAC. YouTube Premium offers 256. Signing in to your "
                        + "YouTube account will turn this on; that isn't built yet.")
            }
            Section {
                if let settings {
                    Stepper(
                        value: Binding(
                            get: { settings.dailyCap },
                            // A limit typed in an older version (say 275) moves to the
                            // nearest step in the direction of the click.
                            set: { new in
                                let step = Self.step
                                let snapped =
                                    new > settings.dailyCap
                                    ? (settings.dailyCap / step + 1) * step
                                    : ((settings.dailyCap - 1) / step) * step
                                model.setDailyCap(min(max(snapped, step), settings.dailyCapMax))
                            }),
                        in: Self.step...settings.dailyCapMax, step: Self.step
                    ) {
                        LabeledContent("Downloads per day") {
                            Text("\(settings.dailyCap)").monospacedDigit().fontWeight(.medium)
                        }
                    }
                    SideNote(
                        "Songs and videos count alike. The standard is \(settings.dailyCapDefault) "
                            + "in 24 hours, kept low on purpose to avoid trouble with YouTube. It "
                            + "can be changed in steps of \(Self.step), up to \(settings.dailyCapMax).")
                    if settings.dailyCap > settings.dailyCapDefault {
                        Label {
                            Text(
                                "Above \(settings.dailyCapDefault) there is a high risk that YouTube "
                                    + "refuses this Mac for some hours. While it does, nothing can "
                                    + "be downloaded, and songs and videos may not play from "
                                    + "YouTube either. Downloads wait and carry on by themselves "
                                    + "afterwards; how long the wait is, is up to YouTube.")
                        } icon: {
                            Image(systemName: "exclamationmark.triangle.fill")
                        }
                        .font(.callout)
                        .foregroundStyle(.orange)
                    }
                } else {
                    LabeledContent("Downloads per day") { ProgressView().controlSize(.small) }
                }
            }
        }
        .formStyle(.grouped)
        .onAppear { model.loadSettings() }
    }
}

private struct AccountSettings: View {
    @Environment(AppModel.self) private var model
    @State private var clientId = ""

    private static let dashboard = URL(string: "https://developer.spotify.com/dashboard")!

    var body: some View {
        Form {
            Section("Spotify") { spotify }
            Section {
                account("YouTube", "For private playlists and, with YouTube Premium, 256 kbps downloads.")
                account(
                    "Apple Music",
                    "Can only read your music list. The songs are then downloaded slowly from YouTube.")
            }
            SideNote("Sign-ins are kept on this Mac only, never in your library.")
        }
        .formStyle(.grouped)
        .onAppear {
            model.loadAccounts()
            if clientId.isEmpty { clientId = model.accounts?.spotify.clientId ?? "" }
        }
        .onChange(of: model.accounts) {
            if clientId.isEmpty { clientId = model.accounts?.spotify.clientId ?? "" }
        }
    }

    @ViewBuilder
    private var spotify: some View {
        let status = model.accounts?.spotify
        if status?.signedIn == true {
            LabeledContent {
                Button("Sign Out") { model.signOutOfSpotify() }
            } label: {
                Text("Signed in" + (status?.name.map { " as \($0)" } ?? ""))
                Text("Your playlists are under Discover → Import Playlists → Spotify.")
            }
        } else {
            VStack(alignment: .leading, spacing: 8) {
                Text("Spotify makes everyone bring an app of their own. It's free, and takes a few minutes, once:")
                    .frame(maxWidth: .infinity, alignment: .leading)
                step(1, "Open Spotify's developer page and log in with your Spotify account. It has to have Premium.")
                Link("Open developer.spotify.com/dashboard", destination: Self.dashboard)
                    .padding(.leading, 22)
                step(2, "Create app. Any name and description. For Redirect URI, paste exactly this, tick Web API, and save:")
                HStack(spacing: 8) {
                    Text(status?.redirectUri ?? "")
                        .font(.callout.monospaced())
                        .textSelection(.enabled)
                    Button("Copy") {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(status?.redirectUri ?? "", forType: .string)
                    }
                    .controlSize(.small)
                    .disabled((status?.redirectUri ?? "").isEmpty)
                }
                .padding(.leading, 22)
                step(3, "On the app's page, copy its Client ID (not the Client secret) and paste it here:")
                TextField("Client ID", text: $clientId, prompt: Text("32 letters and digits"))
                    .labelsHidden()
                    .textFieldStyle(.roundedBorder)
                    .font(.callout.monospaced())
                    .padding(.leading, 22)
                HStack(spacing: 10) {
                    Button("Sign In with Spotify…") { model.signInToSpotify(clientId: clientId) }
                        .disabled(model.signingIn || !Imports.looksLikeSpotifyClientId(clientId))
                    if model.signingIn {
                        ProgressView().controlSize(.small)
                        Text("Finish in your browser…").foregroundStyle(.secondary)
                    }
                }
                .padding(.leading, 22)
                if let note = model.accountNote {
                    Text(note)
                        .foregroundStyle(.orange)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            SideNote(
                "Your password is typed on Spotify's own page in your browser, never here. Music "
                    + "Organizer is only allowed to read your playlists and Liked Songs: it can't "
                    + "change anything, and Spotify's audio is never used.")
        }
    }

    private func step(_ number: Int, _ words: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text("\(number).").monospacedDigit().frame(width: 16, alignment: .trailing)
            Text(words).frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func account(_ name: String, _ note: String) -> some View {
        LabeledContent {
            ComingBadge()
        } label: {
            Text(name)
            Text(note)
        }
    }
}

private struct LyricsSettings: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        Form {
            LabeledContent("Songs without lyrics") {
                switch model.lyricsSearch {
                case .working(let done, let total):
                    VStack(alignment: .trailing, spacing: 4) {
                        if total > 0 {
                            ProgressView(value: Double(done), total: Double(total)).frame(width: 200)
                            Text("\(done.formatted()) of \(total.formatted())")
                                .font(.callout)
                                .foregroundStyle(.secondary)
                        } else {
                            ProgressView().controlSize(.small)
                        }
                    }
                default:
                    Button("Find Missing Lyrics") { model.findMissingLyrics() }
                }
            }
            if case .finished(let summary) = model.lyricsSearch {
                Text(summary).fixedSize(horizontal: false, vertical: true)
            }
            SideNote(
                "Looks up every song that has no lyrics, about one a second, and saves what it "
                    + "finds: timed lyrics where they exist, plain ones otherwise. Lyrics are only "
                    + "taken when they fit the song's length. You can keep using the app while it "
                    + "runs, and the whole run can be undone.")
        }
        .formStyle(.grouped)
    }
}
