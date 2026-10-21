#!/bin/bash
# Pack Bear Media to try on another Mac (the owner, 2026-10-22: "wrap up bear media so a
# friend can try using it in a different country, by downloading, or unloading a zip ...
# after hes done testing it, it needs a full uninstall, to delete any trace of it").
#
#     scripts/package_app.sh
#
# Makes app/build/package/Bear Media.zip: a folder with
#   Bear Media.app                 everything it needs inside it: the engine, Python 3.12
#                                  and its packages, ffmpeg, ffprobe, fpcalc and deno
#   Uninstall Bear Media.command   takes every trace of the app off that Mac, and leaves
#                                  the music and films downloaded with it (the owner's choice)
#   Read Me First.txt              how to open it the first time, and how to take it off
#
# It's built for Intel Macs (this one is), and runs on Apple Silicon through Rosetta,
# which macOS offers to install the first time. It isn't notarized (that needs a paid Apple
# developer account), so the first start needs one step from the person trying it: the
# Read Me says which. Nothing of this Mac's settings, sign-ins, plugins, libraries or paths
# goes in it: the app starts as it would for anyone, with the four plugins it brings.
#
# Nothing here touches a library, and nothing is downloaded: Python is this Mac's own
# (python.org's 3.12), its packages are the project's .venv, and the tools are the ones
# the engine uses here.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$REPO/app/build/package"
FOLDER="$OUT/Bear Media"
APP="$FOLDER/Bear Media.app"
ZIP="$OUT/Bear Media.zip"
PYTHON_HOME="/Library/Frameworks/Python.framework/Versions/3.12"
SITE="$REPO/.venv/lib/python3.12/site-packages"
VERSION="0.2.0"
export PATH="$PATH:/usr/local/bin:$HOME/.deno/bin"

fail() { echo "$*" >&2; exit 1; }
[ -x "$PYTHON_HOME/bin/python3.12" ] || fail "Python 3.12 from python.org isn't at $PYTHON_HOME."
[ -d "$SITE" ] || fail "The project's .venv isn't set up (README)."
for tool in ffmpeg ffprobe fpcalc deno; do
    command -v "$tool" >/dev/null || fail "$tool isn't installed on this Mac, so it can't be packed."
done

echo "Building the app…"
swift build --package-path "$REPO/app" -c release
BINARY="$(swift build --package-path "$REPO/app" -c release --show-bin-path)/MusicOrganizer"

# Only ever this script's own output folder.
rm -rf "$FOLDER" "$ZIP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/engine" "$APP/Contents/Resources/tools"
cp "$BINARY" "$APP/Contents/MacOS/MusicOrganizer"
cp "$REPO/app/Resources/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"

# The same as build_app.sh's, without the engine's path on this Mac.
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
</dict>
</plist>
PLIST

echo "Packing Python and the engine…"
PY="$APP/Contents/Resources/python"
# Python itself, without what the engine never uses: its tests, the IDLE editor, Tk, the
# files for building C extensions, pip and the documentation. (Its Resources stay: bin's
# python3.12 starts the interpreter in Resources/Python.app.)
rsync -a \
    --exclude "/lib/python3.12/test/" --exclude "/lib/python3.12/idlelib/" \
    --exclude "/lib/python3.12/tkinter/" --exclude "/lib/python3.12/turtledemo/" \
    --exclude "/lib/python3.12/ensurepip/" --exclude "/lib/python3.12/config-3.12-darwin/" \
    --exclude "/lib/python3.12/site-packages/" --exclude "/lib/python3.12/lib-dynload/_tkinter*" \
    --exclude "/lib/tcl*" --exclude "/lib/tk*" --exclude "/lib/libtcl*" --exclude "/lib/libtk*" \
    --exclude "/lib/itcl*" --exclude "/lib/thread*" --exclude "/lib/tdbc*" \
    --exclude "/lib/pkgconfig/" --exclude "/include/" --exclude "/share/" \
    --exclude "/Resources/English.lproj/" --exclude "/Headers" --exclude "/_CodeSignature/" \
    --exclude "/bin/*" --include "/bin/python3.12" \
    "$PYTHON_HOME/" "$PY/"
cp "$PYTHON_HOME/bin/python3.12" "$PY/bin/python3.12"
# The engine's packages, without the project's own tools (tests, lint) and pip.
mkdir -p "$PY/lib/python3.12/site-packages"
rsync -a \
    --exclude "__pycache__/" --exclude "_editable_impl_*" --exclude "__editable__*" \
    --exclude "/musicorg*" --exclude "/_pytest/" --exclude "/pytest*" --exclude "/py.py" \
    --exclude "/pluggy*" --exclude "/iniconfig*" --exclude "/ruff*" --exclude "/pygments*" \
    --exclude "/pip/" --exclude "/pip-*" \
    "$SITE/" "$PY/lib/python3.12/site-packages/"
# The engine itself, a copy (the project's .venv only points at the project's folder).
rsync -a --exclude "__pycache__/" "$REPO/engine/musicorg/" "$PY/lib/python3.12/site-packages/musicorg/"
mkdir -p "$PY/lib/python3.12/site-packages/musicorg-$VERSION.dist-info"
printf 'Metadata-Version: 2.1\nName: musicorg\nVersion: 0.1.0\n' \
    > "$PY/lib/python3.12/site-packages/musicorg-$VERSION.dist-info/METADATA"

# Every program and library in it for Intel only, so the engine always runs as one kind on
# Apple Silicon (through Rosetta, as the app does): Python from python.org has both kinds,
# and some of the engine's packages only Intel's. And each that named a place in this Mac's
# Python names its place inside the app instead.
echo "Making Python's parts point inside the app…"
while IFS= read -r -d '' file; do
    file -b "$file" | grep -q "Mach-O" || continue
    if lipo -info "$file" 2>/dev/null | grep -q "Architectures in the fat file"; then
        lipo "$file" -thin x86_64 -output "$file.thin" && mv "$file.thin" "$file"
    fi
    # Its own name, if it's one of Python's libraries.
    own="$(otool -D "$file" 2>/dev/null | tail -n +2 | head -1)"
    if [[ "$own" == "$PYTHON_HOME"* ]]; then
        install_name_tool -id "@loader_path/${own#"$PYTHON_HOME"/}" "$file" 2>/dev/null \
            || fail "Couldn't rename $file."
        # (Its old signature is replaced when the app is signed, below: taken off first, the
        # file can't be changed by install_name_tool at all.)
    fi
    otool -L "$file" | tail -n +2 | awk '{print $1}' | while read -r needed; do
        [[ "$needed" == "$PYTHON_HOME"* ]] || continue
        inside="$PY/${needed#"$PYTHON_HOME"/}"
        relative="$("$REPO/.venv/bin/python" -c 'import os,sys; print(os.path.relpath(sys.argv[1], os.path.dirname(sys.argv[2])))' "$inside" "$file")"
        install_name_tool -change "$needed" "@loader_path/$relative" "$file" 2>/dev/null \
            || fail "Couldn't point $file at $relative."
    done
    # Signed again at once ("ad hoc"): a changed library with its old signature won't load.
    codesign --force --sign - "$file" >/dev/null 2>&1 || fail "Couldn't sign $file."
done < <(find "$PY" -type f \( -perm -u+x -o -name "*.so" -o -name "*.dylib" -o -name "Python" \) -print0)
# python3.12 starts the interpreter in Python.app (a framework build's way): signed as a
# bundle too, now its program inside has been changed.
codesign --force --sign - "$PY/Resources/Python.app" >/dev/null 2>&1 || fail "Couldn't sign Python.app."
# None may still need this Mac's own Python.
while IFS= read -r -d '' file; do
    if otool -L "$file" 2>/dev/null | tail -n +2 | grep -q "$PYTHON_HOME"; then
        fail "$file still points at this Mac's own Python."
    fi
done < <(find "$PY" -type f \( -name "*.so" -o -name "*.dylib" -o -name "Python" -o -name "python3.12" \) -print0)

# The tools the engine runs: found first on the engine's PATH.
for tool in ffmpeg ffprobe fpcalc deno; do
    cp "$(command -v "$tool")" "$APP/Contents/Resources/tools/$tool"
done

# How the app starts its engine (EngineProcess.packedEngine).
cat > "$APP/Contents/Resources/engine/musicorg" <<'LAUNCH'
#!/bin/sh
# Bear Media's engine, packed inside the app (scripts/package_app.sh).
HERE="$(cd "$(dirname "$0")" && pwd)"
RESOURCES="$(dirname "$HERE")"
PYTHONHOME="$RESOURCES/python"
export PYTHONHOME
export PYTHONNOUSERSITE=1
# Nothing is written into the app while it runs.
export PYTHONDONTWRITEBYTECODE=1
# Python's own secure connections trust what the packages' own list does.
export SSL_CERT_FILE="$PYTHONHOME/lib/python3.12/site-packages/certifi/cacert.pem"
export PATH="$RESOURCES/tools:/usr/bin:/bin:/usr/sbin:/sbin"
exec "$PYTHONHOME/bin/python3.12" -m musicorg "$@"
LAUNCH
chmod +x "$APP/Contents/Resources/engine/musicorg"

# Compiled once here, so a start on the other Mac doesn't compile it all again (and never
# writes into the app). The names inside start at the app, not at this Mac's folders.
PYTHONHOME="$PY" "$PY/bin/python3.12" -m compileall -q -j 0 --invalidation-mode unchecked-hash \
    -s "$FOLDER" -p "/" "$PY/lib/python3.12/site-packages" >/dev/null

# The uninstaller, also inside the app, for when the zip's folder has gone.
cp "$REPO/scripts/package/Uninstall Bear Media.command" "$FOLDER/"
cp "$REPO/scripts/package/Uninstall Bear Media.command" "$APP/Contents/Resources/"
chmod +x "$FOLDER/Uninstall Bear Media.command" "$APP/Contents/Resources/Uninstall Bear Media.command"
cp "$REPO/scripts/package/Read Me First.txt" "$FOLDER/"

echo "Signing…"
# Signed "ad hoc": each program and library, then the app. A Mac without this one's
# certificate treats both kinds the same, and Apple Silicon needs a signature on each.
find "$APP/Contents/Resources" -type f -print0 | while IFS= read -r -d '' file; do
    if file -b "$file" | grep -q "Mach-O"; then
        codesign --force --sign - "$file" >/dev/null 2>&1 || fail "Couldn't sign $file."
    fi
done
codesign --force --sign - "$APP" >/dev/null
codesign --verify --strict "$APP" || fail "The app's signature doesn't hold."

echo "Zipping…"
ditto -c -k --sequesterRsrc --keepParent "$FOLDER" "$ZIP"
echo "Packed: $ZIP ($(du -h "$ZIP" | cut -f1))"
