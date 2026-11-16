#!/bin/bash
# Make Bear Media's loading film (the owner, 2026-11-06: the name in particles) from its
# code, scripts/loading_video/render.swift: three seconds at 4K, played once, its last
# picture (the name) held until the app is ready. It's made in two shapes, which the app
# chooses between by the window's (the owner, 2026-11-16: "it should sync to the size of
# the window"): wide, 3840×2160, as Opening.mp4, and upright, 2160×3840, as
# Opening-upright.mp4, each with its first picture as a JPEG, in app/Resources;
# scripts/build_app.sh puts all four in the app.
#
#     scripts/make_loading_video.sh            the Mac's films, both shapes
#     scripts/make_loading_video.sh --upright  also copies of both, named as the iPad and
#                                              phone apps keep them (Opening-landscape,
#                                              Opening-portrait), in a folder it names
#
# Rendering takes a few minutes; ffmpeg makes the pictures. Nothing else is touched.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
RES="$REPO/app/Resources"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "Building the renderer…"
swiftc -O -o "$WORK/render" "$REPO/scripts/loading_video/render.swift"

echo "Rendering the wide film…"
"$WORK/render" video landscape "$WORK/landscape.mp4" 18
cp "$WORK/landscape.mp4" "$RES/Opening.mp4"
ffmpeg -v error -y -i "$WORK/landscape.mp4" -vf "select=eq(n\,0),scale=1920:1080" -vframes 1 -q:v 3 \
    "$RES/Opening.jpg"

echo "Rendering the upright film…"
"$WORK/render" video portrait "$WORK/portrait.mp4" 18
cp "$WORK/portrait.mp4" "$RES/Opening-upright.mp4"
ffmpeg -v error -y -i "$WORK/portrait.mp4" -vf "select=eq(n\,0),scale=1080:1920" -vframes 1 -q:v 3 \
    "$RES/Opening-upright.jpg"
echo "Made: app/Resources/Opening.mp4, Opening.jpg, Opening-upright.mp4 and Opening-upright.jpg"

if [ "${1:-}" = "--upright" ]; then
    OUT="$(mktemp -d)"
    cp "$RES/Opening.mp4" "$OUT/Opening-landscape.mp4"
    cp "$RES/Opening.jpg" "$OUT/Opening-landscape.jpg"
    cp "$RES/Opening-upright.mp4" "$OUT/Opening-portrait.mp4"
    cp "$RES/Opening-upright.jpg" "$OUT/Opening-portrait.jpg"
    echo "Both shapes, for the iPad and phone apps: $OUT"
fi
