#!/bin/bash
# Build the Mac app (v0.2) and put it in app/build/ as "Bear Media.app" (the app's name
# since 2026-10-17, the owner's; it was "Music Organizer.app" before).
#
#     scripts/build_app.sh          build it
#     scripts/build_app.sh --open   build it and open it (if it's open already, it's left
#                                   as it is and you're told to quit and reopen it)
#
# The app is for this Mac only: it uses the engine in this project's .venv, and isn't
# signed for other computers. scripts/package_app.sh makes one to try on another Mac, with
# everything inside it (2026-10-22). Nothing here touches a music library.
#
# The first build after the visualizers' package changes fetches it from GitHub (Particle
# Accelerator, pinned in app/Package.swift), so that one build needs the internet.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE="$REPO/.venv/bin/musicorg"
APP="$REPO/app/build/Bear Media.app"
# The same app under its old name: taken away once the new one is built, unless it's open.
OLD_APP="$REPO/app/build/Music Organizer.app"
ICON="$REPO/app/Resources/AppIcon.icns"
VERSION="0.2.0"

if [ ! -x "$ENGINE" ]; then
    echo "The engine isn't installed at .venv/bin/musicorg. Set the project up first (README)." >&2
    exit 1
fi

echo "Building…"
swift build --package-path "$REPO/app" -c release
BINARY="$(swift build --package-path "$REPO/app" -c release --show-bin-path)/MusicOrganizer"

# Only ever this script's own output folder.
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BINARY" "$APP/Contents/MacOS/MusicOrganizer"
# The bear (scripts/make_icon.sh draws it from the app's own drawing).
cp "$ICON" "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Bear Media</string>
    <key>CFBundleDisplayName</key><string>Bear Media</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>CFBundleIdentifier</key><string>org.musicorganizer.app</string>
    <key>CFBundleExecutable</key><string>MusicOrganizer</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$VERSION</string>
    <key>CFBundleVersion</key><string>$VERSION</string>
    <key>LSMinimumSystemVersion</key><string>14.0</string>
    <key>LSApplicationCategoryType</key><string>public.app-category.music</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
    <key>MusicOrgEngine</key><string>$ENGINE</string>
</dict>
</plist>
PLIST

# macOS remembers "may this app use the Desktop / Downloads / Music folder?" per app, and
# tells apps apart by their signature. Signed "ad hoc" (the fallback below), every changed
# build is a new app to macOS, so it asks all over again at the next start. Signed with a
# certificate that stays the same, it asks once. docs/SIGNING.md says how to make that
# certificate (one minute, once); it's found here by its name.
IDENTITY="Music Organizer Dev"
if security find-identity -p codesigning 2>/dev/null | grep -q "\"$IDENTITY\""; then
    codesign --force --sign "$IDENTITY" "$APP" >/dev/null 2>&1 \
        || { echo "Signing with \"$IDENTITY\" failed; see docs/SIGNING.md." >&2; exit 1; }
    echo "Signed as \"$IDENTITY\": macOS will remember the folders you allowed."
else
    codesign --force --sign - "$APP" >/dev/null 2>&1 || true
    echo "Not signed with a lasting identity, so macOS may ask about folders again after"
    echo "each new build. docs/SIGNING.md fixes that in a minute."
fi

echo "Built: $APP"

# The app's old name, gone so there's one app to open (the settings, the library and
# everything kept go by the app's id, which hasn't changed). Left while it's open.
if [ -d "$OLD_APP" ]; then
    if grep -Fxq "$OLD_APP/Contents/MacOS/MusicOrganizer" <<<"$(ps -axo comm=)"; then
        echo
        echo "The app is still open under its old name, Music Organizer. Quit it (⌘Q), open"
        echo "Bear Media instead, and build again to tidy the old one away."
    else
        rm -rf "$OLD_APP"
    fi
fi
if [ "${1:-}" = "--open" ]; then
    # `open` only brings an app that's already open to the front, and that one is the copy
    # from before this build, with nothing on screen to say so. So say so here, and leave
    # it alone: a song may be playing in it, or a download or a phone's sync under way.
    RUNNING="$(ps -axo comm=)"
    if grep -Fxq "$APP/Contents/MacOS/MusicOrganizer" <<<"$RUNNING"; then
        echo
        echo "Bear Media is still open, and that's the copy from BEFORE this build."
        echo "To see the new one, quit it (⌘Q), then open it again:"
        echo "    open \"$APP\""
    else
        open "$APP"
    fi
fi
