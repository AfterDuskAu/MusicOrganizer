#!/bin/bash
# Build the Mac app (v0.2) and put it in app/build/ as "Music Organizer.app".
#
#     scripts/build_app.sh          build it
#     scripts/build_app.sh --open   build it and open it
#
# The app is for this Mac only: it uses the engine in this project's .venv, and isn't
# signed for other computers (packaging is v0.5). Nothing here touches a music library.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE="$REPO/.venv/bin/musicorg"
APP="$REPO/app/build/Music Organizer.app"
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
mkdir -p "$APP/Contents/MacOS"
cp "$BINARY" "$APP/Contents/MacOS/MusicOrganizer"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Music Organizer</string>
    <key>CFBundleDisplayName</key><string>Music Organizer</string>
    <key>CFBundleIdentifier</key><string>org.musicorganizer.app</string>
    <key>CFBundleExecutable</key><string>MusicOrganizer</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$VERSION</string>
    <key>CFBundleVersion</key><string>$VERSION</string>
    <key>LSMinimumSystemVersion</key><string>14.0</string>
    <key>LSApplicationCategoryType</key><string>public.app-category.music</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>MusicOrgEngine</key><string>$ENGINE</string>
</dict>
</plist>
PLIST

codesign --force --sign - "$APP" >/dev/null 2>&1 || true

echo "Built: $APP"
if [ "${1:-}" = "--open" ]; then
    open "$APP"
fi
