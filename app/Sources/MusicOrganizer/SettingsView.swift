import MusicOrganizerKit
import SwiftUI

/// The Settings window (the cog wheel, or ⌘,), laid out as the owner designed it
/// (docs/roadmap/0.2-app-layout.md). A row marked "Coming" is planned but not built.
struct SettingsView: View {
    var body: some View {
        TabView {
            GeneralSettings().tabItem { Label("General", systemImage: "gearshape") }
            QualitySettings().tabItem { Label("Quality", systemImage: "waveform") }
            AccountSettings().tabItem { Label("Accounts", systemImage: "person.crop.circle") }
            LyricsSettings().tabItem { Label("Lyrics", systemImage: "quote.bubble") }
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
            Picker("Songs you download go to", selection: $model.keepDownloadsSeparate) {
                Text("Discover Downloads").tag(true)
                Text("All Library").tag(false)
            }
            .pickerStyle(.radioGroup)
            SideNote(
                "Discover Downloads keeps downloaded songs apart from your main library, under "
                    + "Discover → Downloads, so you can sort them later. All Library also shows "
                    + "them in Songs, Artists, Recently Added and the rest. You can switch at any "
                    + "time; no file is moved either way.")
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
    @State private var typed = ""

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
                LabeledContent("Downloads per day") {
                    HStack {
                        TextField("", text: $typed)
                            .frame(width: 70)
                            .multilineTextAlignment(.trailing)
                            .onSubmit(save)
                        Button("Set", action: save)
                            .disabled(Int(typed) == settings?.dailyCap || settings == nil)
                    }
                }
                if let settings {
                    SideNote(
                        "The standard download rate is \(settings.dailyCapMax) songs per 24 hours. "
                            + "We keep it at \(settings.dailyCapDefault) on purpose, to avoid trouble "
                            + "with YouTube. You can raise it at your own risk. It can't go above "
                            + "\(settings.dailyCapMax).")
                }
            }
        }
        .formStyle(.grouped)
        .onAppear { model.loadSettings() }
        .onChange(of: settings, initial: true) { typed = settings.map { String($0.dailyCap) } ?? "" }
    }

    private func save() {
        guard let settings = model.engineSettings else { return }
        guard let number = Int(typed.trimmingCharacters(in: .whitespaces)),
            (1...settings.dailyCapMax).contains(number)
        else {
            typed = String(settings.dailyCap)  // not a number we can use: put the old one back
            return
        }
        model.setDailyCap(number)
    }
}

private struct AccountSettings: View {
    var body: some View {
        Form {
            account("YouTube", "For age-restricted songs and, with YouTube Premium, 256 kbps downloads.")
            account(
                "Apple Music",
                "Can only read your music list. The songs are then downloaded slowly from YouTube.")
            account(
                "Spotify",
                "Can only read your music list. The songs are then downloaded slowly from YouTube.")
            SideNote("Sign-ins will be kept on this Mac only.")
        }
        .formStyle(.grouped)
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
