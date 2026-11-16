#!/bin/bash
# Draw Bear Media's icon (the owner's logo, 2026-10-17) from the drawing of the bear,
# scripts/BearDrawing.swift, into app/Resources/AppIcon.icns, which scripts/build_app.sh puts
# in the app. Run it again after the bear changes.
#
#     scripts/make_icon.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

swiftc -parse-as-library -O "$REPO/scripts/make_icon.swift" \
    "$REPO/scripts/BearDrawing.swift" -o "$WORK/make_icon"
mkdir -p "$WORK/AppIcon.iconset"
"$WORK/make_icon" "$WORK/AppIcon.iconset"
iconutil -c icns "$WORK/AppIcon.iconset" -o "$REPO/app/Resources/AppIcon.icns"
echo "Made: app/Resources/AppIcon.icns"
