import MusicOrganizerKit
import SwiftUI

/// Settings → Sharing (2026-10-04): sharing this profile's library with a phone player
/// on the home network. The switch is off until the owner turns it on. The engine does
/// the sharing and only ever lets a device read; this page switches it, shows where this
/// Mac is, and pairs and removes devices.
struct SharingSettings: View {
    @Environment(AppModel.self) private var model
    @AppStorage(AppModel.sharingKey) private var wanted = false
    @AppStorage(AppModel.sharingFilmsKey) private var films = false
    @State private var removing: SharingStatus.Device?

    var body: some View {
        let status = model.sharing
        Form {
            Section {
                Toggle("Share this library with your devices", isOn: $wanted)
                    .onChange(of: wanted) { model.setSharing(wanted) }
                if wanted {
                    LabeledContent("This Mac's address") { address(status) }
                    Toggle("Share your movies and downloaded videos too", isOn: $films)
                        .onChange(of: films) { model.setSharing(wanted) }
                }
                if let note = model.sharingNote {
                    Text(note)
                        .foregroundStyle(.orange)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                SideNote(
                    "On: a phone player on the same Wi-Fi can copy this profile's songs, videos, "
                        + "covers, lyrics and playlists, and play its own copies. It can't "
                        + "change a music file. Three things come back from it when it syncs: "
                        + "a playlist made on it, a song put in a playlist, and time spent "
                        + "listening. Off: nothing is listening.")
                if wanted {
                    SideNote(
                        "Movies and videos: the MP4, M4V and MOV files in your Movies "
                            + "folder and your Videos folder, whoever put them there. "
                            + "Other kinds (MKV, AVI) are left out, because a phone can't "
                            + "play them: Settings → Downloads converts a movie as it's kept.")
                }
                SideNote(
                    "This Mac shares only on the home network, only with devices paired below, "
                        + "and only while Music Organizer is open. Nothing goes to the internet. "
                        + "If macOS asks whether to accept incoming connections, choose Allow.")
            }
            Section("Paired Devices") {
                if let devices = status?.devices, !devices.isEmpty {
                    ForEach(devices) { device in row(device) }
                } else {
                    Text("No device is paired yet.").foregroundStyle(.secondary)
                }
                if let kind = model.justPaired {
                    Label("\(kind) is paired, and can sync now.", systemImage: "checkmark.circle")
                }
                Button("Pair a Device…", systemImage: "plus") { model.pairDevice() }
                    .disabled(status?.on != true)
                SideNote(
                    "A device is paired once, with a six-digit code shown here. Removing a "
                        + "device stops it syncing; the music already on it stays there.")
            }
        }
        .formStyle(.grouped)
        .scrollContentBackground(Theme.current.listBackground)
        .onAppear { model.loadSharing() }
        .sheet(
            isPresented: Binding(
                get: { model.pairingCode != nil }, set: { if !$0 { model.stopPairing() } })
        ) {
            PairDeviceSheet().environment(model).dressed()
        }
        .confirmationDialog(
            "Remove this \(removing?.device ?? "device")?",
            isPresented: Binding(get: { removing != nil }, set: { if !$0 { removing = nil } }),
            presenting: removing
        ) { device in
            Button("Remove", role: .destructive) { model.forgetDevice(device.id) }
            Button("Cancel", role: .cancel) {}
        } message: { _ in
            Text(
                "It can't sync again until it's paired again. The music already on it stays "
                    + "there, and nothing on this Mac changes.")
        }
    }

    /// Where a device finds this Mac, for typing in by hand when it isn't found by itself.
    @ViewBuilder
    private func address(_ status: SharingStatus?) -> some View {
        if let place = status?.whereToFind {
            VStack(alignment: .trailing, spacing: 2) {
                Text(place).monospacedDigit().textSelection(.enabled)
                Text("A device usually finds this Mac by itself. If not, type this into it.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
        } else if status?.on == true {
            Text("This Mac isn't on a home network").foregroundStyle(.secondary)
        } else if model.sharingNote == nil {
            ProgressView().controlSize(.small)
        }
    }

    private func row(_ device: SharingStatus.Device) -> some View {
        LabeledContent {
            Button("Remove…") { removing = device }
        } label: {
            Label(device.device, systemImage: device.device == "iPad" ? "ipad" : "iphone")
            Text(device.about())
        }
    }
}

/// The six-digit code a device types to be paired. Closing this takes the code away.
private struct PairDeviceSheet: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(spacing: 14) {
            Text("Pair a Device").font(.title3.weight(.semibold)).heading()
            Text(
                "On the device, open the player's Sync page and choose this Mac. Then type "
                    + "this code:"
            )
            .multilineTextAlignment(.center)
            .fixedSize(horizontal: false, vertical: true)
            TimelineView(.periodic(from: .now, by: 1)) { moment in
                let left = Int(
                    (model.pairingUntil ?? .distantPast).timeIntervalSince(moment.date)
                        .rounded(.up))
                if let code = model.pairingCode, left > 0 {
                    Text(code.spaced)
                        .font(.system(size: 46, weight: .semibold))
                        .monospacedDigit()
                        .textSelection(.enabled)
                    Text("It works once, for the next \(PairingCode.clock(left)).")
                        .foregroundStyle(.secondary)
                } else {
                    Text("That code has run out.").foregroundStyle(.secondary)
                }
            }
            HStack {
                Button("New Code") { model.pairDevice() }
                Spacer()
                Button("Done") { model.stopPairing() }
                    .keyboardShortcut(.defaultAction)
            }
            .padding(.top, 6)
        }
        .padding(24)
        .frame(width: 380)
    }
}
