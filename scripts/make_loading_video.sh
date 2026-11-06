#!/bin/bash
# Make Bear Media's loading film (the owner, 2026-11-06: the name in particles) from its
# code, scripts/loading_video/render.swift: three seconds at 4K, played once, its last
# picture (the name) held until the app is ready. It goes in app/Resources as Opening.mp4,
# with its first picture as Opening.jpg; scripts/build_app.sh puts both in the app.
#
#     scripts/make_loading_video.sh            the Mac's film (3840×2160)
#     scripts/make_loading_video.sh --upright  also a 2160×3840 one for a screen held
#                                              upright, in a folder it names
#
# Rendering takes a few minutes; ffmpeg makes the picture. Nothing else is touched.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "Building the renderer…"
swiftc -O -o "$WORK/render" "$REPO/scripts/loading_video/render.swift"

echo "Rendering the film…"
"$WORK/render" video landscape "$WORK/landscape.mp4" 18
cp "$WORK/landscape.mp4" "$REPO/app/Resources/Opening.mp4"
ffmpeg -v error -y -i "$WORK/landscape.mp4" -vf "select=eq(n\,0),scale=1920:1080" -vframes 1 -q:v 3 \
    "$REPO/app/Resources/Opening.jpg"
echo "Made: app/Resources/Opening.mp4 and Opening.jpg"

if [ "${1:-}" = "--upright" ]; then
    OUT="$(mktemp -d)"
    "$WORK/render" video portrait "$OUT/Opening-portrait.mp4" 18
    ffmpeg -v error -y -i "$OUT/Opening-portrait.mp4" -vf "select=eq(n\,0),scale=1080:1920" -vframes 1 -q:v 3 \
        "$OUT/Opening-portrait.jpg"
    cp "$REPO/app/Resources/Opening.mp4" "$OUT/Opening-landscape.mp4"
    cp "$REPO/app/Resources/Opening.jpg" "$OUT/Opening-landscape.jpg"
    echo "Both shapes: $OUT"
fi
