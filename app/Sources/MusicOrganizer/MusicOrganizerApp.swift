import AppKit
import MusicOrganizerKit
import SwiftUI

@main
struct MusicOrganizerApp: App {
    @State private var model = AppModel()

    init() {
        Theme.current.putOn()
        // Started as a plain program (`swift run`) the app would have no Dock icon or
        // menu bar; in the built app this changes nothing.
        NSApplication.shared.setActivationPolicy(Snapshot.isOn ? .accessory : .regular)
        StallWatch.shared.startIfAsked()
        Snapshot.startIfAsked()
    }

    var body: some Scene {
        // The main window comes first: the first scene is the one opened at launch.
        Window("Music Organizer", id: "main") {
            RootView()
                .environment(model)
                .frame(minWidth: 880, minHeight: 540)
                .dressed()
                .background(WindowLook())
                .task {
                    if !Snapshot.isOn { NSApplication.shared.activate(ignoringOtherApps: true) }
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
            // View → Columns: what's shown beside each song, for every list at once.
            CommandGroup(after: .sidebar) {
                ColumnsMenu()
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
        Settings {
            SettingsView().environment(model)
                .dressed()
                .background(WindowLook())
        }
    }
}
