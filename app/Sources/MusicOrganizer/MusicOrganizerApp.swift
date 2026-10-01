import AppKit
import SwiftUI

@main
struct MusicOrganizerApp: App {
    @State private var model = AppModel()

    init() {
        // Started as a plain program (`swift run`) the app would have no Dock icon or
        // menu bar; in the built app this changes nothing.
        NSApplication.shared.setActivationPolicy(.regular)
    }

    var body: some Scene {
        Settings {
            SettingsView().environment(model)
        }
        Window("Music Organizer", id: "main") {
            RootView()
                .environment(model)
                .frame(minWidth: 880, minHeight: 540)
                .task {
                    NSApplication.shared.activate(ignoringOtherApps: true)
                    await model.start()
                }
        }
        .defaultSize(width: 1180, height: 760)
        .commands {
            CommandGroup(after: .newItem) {
                Button("Choose Library Folder…") { model.chooseLibrary() }
                    .keyboardShortcut("o")
                Button("New Playlist…") { model.newPlaylist() }
                    .keyboardShortcut("n")
                    .disabled(model.phase != .ready)
                Button("Reload Library") { Task { await model.reload() } }
                    .keyboardShortcut("r")
                    .disabled(model.phase != .ready)
            }
            CommandMenu("Controls") {
                Button(model.player.isPlaying ? "Pause" : "Play") { model.player.toggle() }
                    .keyboardShortcut("p")
                Button("Next Song") { model.player.next() }
                    .keyboardShortcut(.rightArrow)
                Button("Previous Song") { model.player.previous() }
                    .keyboardShortcut(.leftArrow)
                Divider()
                Toggle("Shuffle", isOn: Binding(
                    get: { model.player.queue.shuffle }, set: { model.player.setShuffle($0) }))
                Button("Repeat: \(model.player.queue.repeatMode.label)") {
                    model.player.cycleRepeat()
                }
            }
        }
    }
}
