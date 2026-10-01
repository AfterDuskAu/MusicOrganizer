# Stop macOS asking about folders at every start

**The problem.** macOS protects the Desktop, Documents, Downloads and Music folders: an app must be allowed to use each one, and macOS remembers the answer per app. It tells apps apart by their signature. A build made by `scripts/build_app.sh` with no certificate is signed "ad hoc", so every changed build looks like a brand-new app, and macOS asks all over again. While it waits for an answer the app sits on "Reading your library…".

**The fix.** Give the build one signature that never changes: a certificate you make yourself, on your own Mac. It costs nothing, needs no Apple account, and never leaves your Mac. The build script looks for it by name and uses it when it's there.

## Make the certificate (once)

1. Open **Keychain Access** (press ⌘-Space, type "Keychain Access", press Return).
2. In the menu bar: **Keychain Access → Certificate Assistant → Create a Certificate…**
3. Fill it in exactly like this:
   - **Name:** `Music Organizer Dev`
   - **Identity Type:** Self-Signed Root
   - **Certificate Type:** Code Signing
4. Click **Create**, then **Continue** if it warns that the certificate is self-signed, then **Done**.

## Use it

Build the app again:

```bash
~/Developer/MusicOrganizer/scripts/build_app.sh --open
```

- The first time, macOS asks whether `codesign` may use the new key: type your Mac password and choose **Always Allow**.
- The script should print `Signed as "Music Organizer Dev"`.
- At that first start macOS asks about each folder one last time. Choose **Allow** for each. After that it remembers, however many times the app is rebuilt.

## Good to know

- Starting the app **without** rebuilding it (double-click `app/build/Music Organizer.app`) never makes macOS ask again, with or without the certificate.
- The certificate only works on this Mac. Handing the app to friends needs Apple's own signing, which is the packaging step (v0.5).
- To undo: delete "Music Organizer Dev" in Keychain Access. The script goes back to ad hoc signing.
