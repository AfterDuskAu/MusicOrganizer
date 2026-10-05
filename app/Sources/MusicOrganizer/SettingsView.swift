import MusicOrganizerKit
import SwiftUI

/// The Settings window (the cog wheel, or ⌘,), laid out as the owner designed it
/// (docs/roadmap/0.2-app-layout.md). A row marked "Coming" is planned but not built.
struct SettingsView: View {
    /// The tab that's showing: remembered, and set by a page that sends the owner here
    /// for one thing (Import Playlists → Spotify opens Profile, with Spotify open).
    @AppStorage(SettingsView.tabKey) private var tab = "profile"

    static let tabKey = "settingsTab"
    /// Which account under Settings → Profile is open (its arrow turned down).
    static let openAccountKey = "settingsOpenAccount"
    private static let tabs = ["profile", "play", "downloads", "lyrics", "sharing", "layout"]

    var body: some View {
        TabView(selection: $tab) {
            ProfileSettingsTab().tabItem { Label("Profile", systemImage: "person.crop.circle") }
                .tag("profile")
            PlaySettings().tabItem { Label("Play Options", systemImage: "play.circle") }
                .tag("play")
            DownloadSettings().tabItem { Label("Downloads", systemImage: "arrow.down.circle") }
                .tag("downloads")
            LyricsSettings().tabItem { Label("Lyrics", systemImage: "quote.bubble") }.tag("lyrics")
            SharingSettings().tabItem { Label("Sharing", systemImage: "iphone") }.tag("sharing")
            LayoutSettings().tabItem { Label("App Layout", systemImage: "paintpalette") }
                .tag("layout")
        }
        .frame(width: 560)
        // A tab remembered from before Settings was regrouped (2026-10-03) opens Profile.
        .onAppear { if !Self.tabs.contains(tab) { tab = "profile" } }
    }
}

/// A small grey explanation under a setting.
struct SideNote: View {
    let text: String

    init(_ text: String) { self.text = text }

    var body: some View {
        Text(text)
            .font(.callout)
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// Settings → App Layout: the look the app is dressed in (owner, 2026-10-03). The look is
/// put on when the app opens (`Theme`), so a new choice shows once it's been reopened.
private struct LayoutSettings: View {
    @AppStorage(AppLook.key) private var saved = AppLook.native.rawValue

    var body: some View {
        let chosen = AppLook(saved: saved)
        Form {
            Section {
                Picker(
                    "Look",
                    selection: Binding(
                        get: { chosen },
                        set: { look in
                            saved = look.rawValue
                            Theme.saveHighlight(for: look)
                        })
                ) {
                    ForEach(AppLook.allCases, id: \.self) { look in
                        Text(look.title).tag(look)
                    }
                }
                .pickerStyle(.radioGroup)
                ForEach(AppLook.allCases, id: \.self) { look in
                    SideNote("\(look.title): \(look.about)")
                }
                SideNote(
                    "The pages and everything on them are the same in both. The look is for "
                        + "everyone who uses Music Organizer on this Mac, not one profile.")
            }
            if chosen != Theme.current.look {
                Section {
                    HStack(alignment: .firstTextBaseline) {
                        Text(
                            "Music Organizer will be in the \(chosen.title) the next time "
                                + "it's opened.")
                        Spacer()
                        if Theme.canReopen {
                            Button("Reopen Now") { Theme.reopen() }
                        }
                    }
                    SideNote(
                        Theme.canReopen
                            ? "Reopening takes a few seconds, and stops the music that's playing."
                            : "Quit Music Organizer and open it again.")
                }
            }
        }
        .formStyle(.grouped)
        .scrollContentBackground(Theme.current.listBackground)
    }
}

/// Settings → Play Options: how songs and videos play, as the owner laid it out
/// (2026-10-03): the Local Visualizer's options, then the custom visualizer's (three of
/// Particle Accelerator's visuals, since 2026-10-04).
private struct PlaySettings: View {
    @AppStorage(Player.playWhileVideoLoadsKey) private var playWhileLoading = true
    @AppStorage(Player.alwaysBestVideoKey) private var alwaysBestVideo = false
    @AppStorage(Player.visualizerLyricsKey) private var visualizerLyrics = true
    @AppStorage(Player.fullScreenLyricsKey) private var fullScreenLyrics = false
    @AppStorage(CustomVisualizer.whichKey) private var whichVisualizer = CustomVisualizer.standard
    @AppStorage(CustomVisualizer.useKey) private var useVisualizer = false
    @AppStorage(CustomVisualizer.qualityKey) private var visualizerQuality =
        CustomVisualizer.standardQuality
    @AppStorage(PageChanges.key) private var changesLast = PageChanges.standard
    @Environment(AppModel.self) private var model

    var body: some View {
        Form {
            Section {
                Picker("A change made on a page lasts", selection: $changesLast) {
                    ForEach(PageChanges.options, id: \.self) { minutes in
                        Text(PageChanges.label(minutes)).tag(minutes)
                    }
                }
                SideNote(
                    "What's chosen here is how the app always starts and stays. Switching "
                        + "something on a page (lyrics off with the button beside the volume "
                        + "slider, say) doesn't change these settings: it lasts this long, then "
                        + "the page goes back to what's chosen here.")
            }
            Section("Visualizer") {
                Toggle("Videos: always the highest quality available", isOn: $alwaysBestVideo)
                SideNote(
                    "Videos then always play at their sharpest (up to 1080p), and the Local "
                        + "Visualizer has no picture-size menu. This is about playing only: what "
                        + "a download saves is set under Downloads.")
                yesNo("Always show lyrics", $visualizerLyrics)
                    // The setting is the standing choice: a page switched for now follows it again.
                    .onChange(of: visualizerLyrics) {
                        model.forgetPageChange(Player.visualizerLyricsKey)
                    }
                SideNote(
                    "Yes: the lyrics are beside the cover or video whenever the song has any; a "
                        + "song with none gives the whole page to the cover or video. No: the "
                        + "cover or video always has the whole page. The lyrics button beside "
                        + "the volume slider switches them on the Local Visualizer for now, "
                        + "without changing this.")
                yesNo("Show lyrics in full screen", $fullScreenLyrics)
                    .onChange(of: fullScreenLyrics) {
                        model.forgetPageChange(Player.fullScreenLyricsKey)
                    }
                SideNote(
                    "Yes: with a video or the visualizer on the whole screen, the song's lyrics "
                        + "take a column on the right and the picture the rest. No: the picture "
                        + "has the whole screen. The lyrics button in full screen switches them "
                        + "for now.")
                yesNo(
                    "When Video is chosen, play the song while its video loads?", $playWhileLoading)
                SideNote(
                    "Yes: the song starts at once, and its video takes over when it's ready. No: "
                        + "nothing plays until the video is ready, then the video starts from its "
                        + "beginning. A song with no video plays as soon as that's known.")
            }
            Section("Custom Visualizer") {
                Picker(
                    "Which visualizer",
                    selection: Binding(
                        get: { CustomVisualizer.chosen(whichVisualizer) },
                        set: { whichVisualizer = $0 })
                ) {
                    ForEach(CustomVisualizer.offered, id: \.self) { number in
                        Text(CustomVisualizer.title(number)).tag(number)
                    }
                }
                yesNo(
                    "Use the custom visualizer instead of the song or album cover",
                    $useVisualizer
                )
                .onChange(of: useVisualizer) {
                    model.forgetPageChange(CustomVisualizer.useKey)
                    if useVisualizer { model.hearForVisualizer() }
                }
                SideNote(
                    "Yes: on the Local Visualizer, the visualizer moves to the music where "
                        + "the song's cover would be. No: the cover is there. The Song, Video, "
                        + "Visualizer switch on that page changes it for now, without changing "
                        + "this. A video is always shown as a video. The visualizers are made "
                        + "in their own project, Particle Accelerator.")
                Picker(
                    "Quality",
                    selection: Binding(
                        get: { CustomVisualizer.quality(visualizerQuality) },
                        set: { visualizerQuality = $0 })
                ) {
                    ForEach(CustomVisualizer.qualities, id: \.self) { name in
                        Text(CustomVisualizer.qualityTitle(name)).tag(name)
                    }
                }
                SideNote(
                    "A higher quality draws more sparks and a sharper picture, and asks more "
                        + "of the graphics card. If the picture stutters, choose a lower one. "
                        + "Medium is the quality the three were tuned at. Auto chooses for "
                        + "this Mac.")
            }
        }
        .formStyle(.grouped)
        .scrollContentBackground(Theme.current.listBackground)
    }

    private func yesNo(_ question: String, _ answer: Binding<Bool>) -> some View {
        Picker(question, selection: answer) {
            Text("Yes").tag(true)
            Text("No").tag(false)
        }
        .pickerStyle(.radioGroup)
        .horizontalRadioGroupLayout()
    }
}

/// Settings → Downloads: where downloads show, the library folder, the quality, and
/// how many a day.
private struct DownloadSettings: View {
    @Environment(AppModel.self) private var model
    /// Nil until it's been set here: then it's what the playing setting says, which
    /// used to cover downloading too (`Player.alwaysBestDownload`).
    @AppStorage(Player.alwaysBestDownloadKey) private var alwaysBestDownload: Bool?
    @AppStorage(Player.alwaysBestVideoKey) private var alwaysBestVideo = false
    /// The limit moves in steps of this many.
    private static let step = 50

    var body: some View {
        @Bindable var model = model
        let settings = model.engineSettings
        Form {
            Section {
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
                        + "either way.")
            }
            Section {
                LabeledContent("Library folder") {
                    VStack(alignment: .trailing, spacing: 4) {
                        Text(model.root?.path ?? "Not chosen")
                            .lineLimit(2)
                            .truncationMode(.middle)
                            .textSelection(.enabled)
                        Button("Select…") { model.chooseLibrary() }
                    }
                }
                SideNote(
                    "This profile's library. Songs are kept in its Music folder by artist and "
                        + "album, and videos in Music/Videos.")
            }
            Section {
                Toggle(
                    "Always download the highest quality available",
                    isOn: Binding(
                        get: { alwaysBestDownload ?? alwaysBestVideo },
                        set: { alwaysBestDownload = $0 })
                )
                SideNote(
                    "On: Download Video always saves the video at its sharpest (up to 1080p), "
                        + "whatever size is playing. Off: it saves the size that's showing. "
                        + "Songs come in one quality for now, 128 kbps; when 256 kbps arrives "
                        + "with the YouTube sign-in, this will take it too. How videos play is "
                        + "set under Play Options.")
            }
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
        .scrollContentBackground(Theme.current.listBackground)
        .onAppear { model.loadSettings() }
    }
}

/// Settings → Profile: the people who use the app on this Mac. A profile is a name
/// and a library folder of its own; it isn't an account anywhere, and has no password.
private struct ProfileSettingsTab: View {
    @Environment(AppModel.self) private var model
    @State private var adding = false
    @State private var renaming: Profile?
    @State private var newName = ""
    @State private var removing: Profile?
    @State private var problem: String?

    var body: some View {
        let list = model.profiles
        Form {
            Section("Profiles on this Mac") {
                ForEach(list.profiles) { profile in
                    row(profile, inUse: profile.id == list.currentId)
                }
            }
            Section {
                Button("Add New Profile…", systemImage: "plus") { adding = true }
                if let problem {
                    Text(problem)
                        .foregroundStyle(.orange)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                SideNote(
                    "Each profile has its own music, downloads, playlists, favourites, sign-ins "
                        + "and settings, in a library folder of its own, so nobody's music is "
                        + "mixed with anyone else's. Switching deletes nothing: a profile is "
                        + "exactly as it was left when you switch back, and downloads it was "
                        + "still waiting for carry on then. The daily download limit is shared, "
                        + "because YouTube counts the computer, not the person.")
            }
            AccountSettings()
        }
        .formStyle(.grouped)
        .scrollContentBackground(Theme.current.listBackground)
        .sheet(isPresented: $adding) { NewProfileSheet().environment(model).dressed() }
        .alert("Rename Profile", isPresented: renamingShown, presenting: renaming) { profile in
            TextField("Name", text: $newName)
            Button("Rename") { attempt { try model.renameProfile(profile.id, to: newName) } }
            Button("Cancel", role: .cancel) {}
        }
        .confirmationDialog(
            "Remove \(removing?.name ?? "this profile") from the list?",
            isPresented: removingShown, presenting: removing
        ) { profile in
            Button("Remove from the List", role: .destructive) {
                attempt { try model.removeProfile(profile.id) }
            }
            Button("Cancel", role: .cancel) {}
        } message: { profile in
            Text(
                "Nothing is deleted. Its music stays where it is"
                    + (profile.libraryRoot.map { ", in \($0)" } ?? "")
                    + ", and the profile can be made again from that folder.")
        }
    }

    private func row(_ profile: Profile, inUse: Bool) -> some View {
        LabeledContent {
            HStack(spacing: 8) {
                if inUse {
                    Text("In use")
                        .font(.caption.weight(.medium))
                        .padding(.horizontal, 7)
                        .padding(.vertical, 2)
                        .background(Color.accentColor.opacity(0.25), in: Capsule())
                } else {
                    Button("Switch") {
                        problem = nil
                        model.switchProfile(to: profile.id)
                    }
                    .help("Open the app for \(profile.name): their music, playlists and settings")
                }
                Menu {
                    Button("Rename…") {
                        newName = profile.name
                        renaming = profile
                    }
                    Toggle(
                        "A Child's Profile",
                        isOn: Binding(
                            get: { profile.isChild }, set: { model.setChild(profile.id, $0) }))
                    Button("Remove from the List…") { removing = profile }
                        .disabled(inUse || model.profiles.profiles.count < 2)
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
                .menuStyle(.borderlessButton)
                .menuIndicator(.hidden)
                .fixedSize()
            }
        } label: {
            HStack(spacing: 6) {
                Text(profile.name).fontWeight(inUse ? .semibold : .regular)
                if profile.isChild {
                    Text("Child")
                        .font(.caption.weight(.medium))
                        .padding(.horizontal, 6)
                        .padding(.vertical, 1)
                        .background(.quaternary, in: Capsule())
                        .foregroundStyle(.secondary)
                }
            }
            Text(profile.libraryRoot ?? "No library folder chosen yet")
                .lineLimit(1)
                .truncationMode(.middle)
        }
    }

    private func attempt(_ change: () throws -> Void) {
        do {
            try change()
            problem = nil
        } catch {
            problem = error.localizedDescription
        }
    }

    private var renamingShown: Binding<Bool> {
        Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })
    }

    private var removingShown: Binding<Bool> {
        Binding(get: { removing != nil }, set: { if !$0 { removing = nil } })
    }
}

/// A new profile: its name, and where its music will be kept.
private struct NewProfileSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    /// A folder chosen by hand; without one, the suggested folder is used.
    @State private var chosen: String?
    @State private var problem: String?
    @FocusState private var typing: Bool

    @State private var isChild = false

    /// In the Mac's Music folder, named after the profile ("Music Kids"), unless a
    /// folder was chosen by hand.
    private var folder: String? {
        chosen
            ?? model.profiles.suggestedRoot(for: name, in: AppModel.musicFolder) {
                FileManager.default.fileExists(atPath: $0)
            }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("New Profile").font(.title3.weight(.semibold)).heading()
            TextField("Name", text: $name, prompt: Text("A name: a person's, or Kids"))
                .textFieldStyle(.roundedBorder)
                .focused($typing)
                .onSubmit(create)
            VStack(alignment: .leading, spacing: 4) {
                Text("Its music will be kept in:").foregroundStyle(.secondary)
                HStack(spacing: 8) {
                    Text(folder ?? "Choose a folder for it")
                        .lineLimit(2)
                        .truncationMode(.middle)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Button("Choose…", action: choose)
                }
            }
            Text(
                "A new, empty library is made there. The profile in use now is put away exactly "
                    + "as it is, and comes back when you switch to it again."
            )
            .font(.callout)
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            Toggle("This is a child's profile", isOn: $isChild)
            Text(
                "For now this only marks the profile. What it will do is still to be decided: "
                    + "most likely no explicit songs and nothing age-restricted."
            )
            .font(.callout)
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            if let problem {
                Text(problem)
                    .foregroundStyle(.orange)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button("Create and Switch", action: create)
                    .keyboardShortcut(.defaultAction)
                    .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty || folder == nil)
            }
        }
        .padding(20)
        .frame(width: 460)
        .onAppear { typing = true }
    }

    private func choose() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.message = "Choose an empty folder for this profile's music, or make a new one."
        panel.prompt = "Use This Folder"
        if panel.runModal() == .OK, let url = panel.url { chosen = url.path }
    }

    private func create() {
        guard let folder else { return }
        do {
            try model.addProfile(named: name, libraryRoot: folder, isChild: isChild)
            dismiss()
        } catch {
            problem = error.localizedDescription
        }
    }
}

/// The accounts of the profile in use, each a row with an arrow: turned down, it shows
/// how to sign in, or who's signed in. Sign-ins only ever read: they bring music lists
/// across, and are kept on this Mac only.
private struct AccountSettings: View {
    @Environment(AppModel.self) private var model
    @AppStorage(SettingsView.openAccountKey) private var open = ""
    @State private var clientId = ""
    @State private var lastfmUser = ""
    @State private var lastfmKey = ""

    private static let dashboard = URL(string: "https://developer.spotify.com/dashboard")!
    private static let lastfmKeyPage = URL(string: "https://www.last.fm/api/account/create")!

    var body: some View {
        Section("Accounts for \(model.profiles.current.name)") {
            account("YouTube", symbol: "play.rectangle", state: comingWords) {
                SideNote(
                    "For private playlists and Liked Music, age-restricted songs, and, with YouTube "
                        + "Premium, 256 kbps downloads. Not built yet.")
            }
            account("Spotify", symbol: "music.note.list", state: spotifyState) { spotify }
            account("Apple Music", symbol: "applelogo", state: comingWords) {
                SideNote(
                    "Will read your playlists from the Music app on this Mac. The songs are then "
                        + "found and downloaded from YouTube. Not built yet.")
            }
            account("Deezer", symbol: "link", state: "No sign-in needed") {
                SideNote(
                    "A public Deezer playlist or album is read from its link, with nothing to "
                        + "sign in to: Discover → Import Playlists → Deezer.")
            }
            account("Amazon Music", symbol: "doc", state: "From a file") {
                SideNote(
                    "Amazon Music can't be read directly and has no export of its own. Save a "
                        + "playlist as a CSV or text file with a service such as TuneMyMusic or "
                        + "Soundiiz, then choose the file under Discover → Import Playlists → "
                        + "Amazon Music.")
            }
            account("Last.fm", symbol: "waveform", state: lastfmState) { lastfm }
            account("SoundCloud", symbol: "cloud", state: comingWords) {
                SideNote("Not built yet.")
            }
        }
        .onAppear {
            model.loadAccounts()
            fillIn()
        }
        .onChange(of: model.accounts) { fillIn() }
    }

    /// What's saved goes into the boxes that are still empty.
    private func fillIn() {
        if clientId.isEmpty { clientId = model.accounts?.spotify.clientId ?? "" }
        if lastfmUser.isEmpty { lastfmUser = model.accounts?.lastfm?.user ?? "" }
    }

    private var lastfmState: String {
        guard let status = model.accounts?.lastfm else { return "" }
        if status.connected { return "Set up" + (status.user.map { " for \($0)" } ?? "") }
        return model.connectingLastfm ? "Checking…" : "Not set up"
    }

    /// Last.fm has no sign-in: a username, and an API key the owner makes themselves.
    @ViewBuilder
    private var lastfm: some View {
        let status = model.accounts?.lastfm
        if status?.connected == true {
            LabeledContent {
                Button("Forget") {
                    model.forgetLastfm()
                    (lastfmUser, lastfmKey) = ("", "")
                }
            } label: {
                Text("Set up" + (status?.user.map { " for \($0)" } ?? ""))
                Text(
                    "Your most played there is a starting point under Discover → Find (My "
                        + "Last.fm), and your lists are under Discover → Import Playlists → Last.fm.")
            }
        } else {
            VStack(alignment: .leading, spacing: 8) {
                Text("Last.fm keeps a record of what you listen to, on Spotify and elsewhere. There's no password to type here, but Last.fm gives its answers only to an app with a key, and you make that yourself. It's free, and takes a minute, once:")
                    .frame(maxWidth: .infinity, alignment: .leading)
                step(1, "Your Last.fm username (the name in your profile's address, last.fm/user/…):")
                TextField("Username", text: $lastfmUser, prompt: Text("Your Last.fm username"))
                    .labelsHidden()
                    .textFieldStyle(.roundedBorder)
                    .padding(.leading, 22)
                step(2, "Open Last.fm's page for a new API account and log in. Any name will do for the application; leave the other boxes empty, and submit.")
                Link("Open last.fm/api/account/create", destination: Self.lastfmKeyPage)
                    .padding(.leading, 22)
                step(3, "Copy the one called API key (not the Shared secret) and paste it here:")
                TextField("API key", text: $lastfmKey, prompt: Text("32 letters and digits"))
                    .labelsHidden()
                    .textFieldStyle(.roundedBorder)
                    .font(.callout.monospaced())
                    .padding(.leading, 22)
                HStack(spacing: 10) {
                    Button("Set Up Last.fm") {
                        model.connectLastfm(user: lastfmUser, apiKey: lastfmKey)
                    }
                    .disabled(
                        model.connectingLastfm
                            || lastfmUser.trimmingCharacters(in: .whitespaces).isEmpty
                            // A key saved before is kept: only a new one has to look right.
                            || !(Imports.looksLikeLastfmKey(lastfmKey)
                                || (lastfmKey.isEmpty && status?.hasKey == true)))
                    if model.connectingLastfm {
                        ProgressView().controlSize(.small)
                        Text("Asking Last.fm…").foregroundStyle(.secondary)
                    }
                }
                .padding(.leading, 22)
                if let note = model.lastfmNote {
                    Text(note)
                        .foregroundStyle(.orange)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            SideNote(
                "The key and your username are kept on this Mac only, for this profile. Music "
                    + "Organizer only reads from Last.fm: it never sends what you play here, and "
                    + "your profile there has to be public to be read.")
        }
    }

    private var comingWords: String { "Coming" }

    private var spotifyState: String {
        guard let status = model.accounts?.spotify else { return "" }
        if status.signedIn { return "Signed in" + (status.name.map { " as \($0)" } ?? "") }
        return model.signingIn ? "Signing in…" : "Not signed in"
    }

    /// One account: its name and state, and what's under its arrow.
    private func account(
        _ name: String, symbol: String, state: String, @ViewBuilder inside: () -> some View
    ) -> some View {
        let content = inside()
        return DisclosureGroup(
            isExpanded: Binding(get: { open == name }, set: { open = $0 ? name : "" })
        ) {
            content.padding(.vertical, 4)
        } label: {
            LabeledContent {
                Text(state).foregroundStyle(.secondary)
            } label: {
                Label(name, systemImage: symbol)
            }
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
        .scrollContentBackground(Theme.current.listBackground)
    }
}
