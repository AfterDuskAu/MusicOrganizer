#!/bin/bash
# Draw Bear Media's icon (the owner's logo, 2026-10-17) from the app's own drawing of the
# bear, app/Sources/MusicOrganizer/BearDrawing.swift, into app/Resources/AppIcon.icns,
# which scripts/build_app.sh puts in the app. Run it again after the bear changes.
#
#     scripts/make_icon.sh                 the icon
#     scripts/make_icon.sh <seconds>…      also pictures of the loading screen at those
#                                          moments, in a folder it names
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

swiftc -parse-as-library -O "$REPO/scripts/make_icon.swift" \
    "$REPO/app/Sources/MusicOrganizer/BearDrawing.swift" -o "$WORK/make_icon"
mkdir -p "$WORK/AppIcon.iconset"
"$WORK/make_icon" "$WORK/AppIcon.iconset"
iconutil -c icns "$WORK/AppIcon.iconset" -o "$REPO/app/Resources/AppIcon.icns"
echo "Made: app/Resources/AppIcon.icns"

if [ $# -gt 0 ]; then
    SHOTS="$(mktemp -d)"
    "$WORK/make_icon" "$SHOTS" "$@"
    echo "The loading screen: $SHOTS"
fi
