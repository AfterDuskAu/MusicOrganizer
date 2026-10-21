#!/bin/zsh
# Uninstall Bear Media: takes every trace of the app off this Mac, into the Trash.
#
# What goes: the app; its settings, sign-ins and paired devices; its caches (films half
# arrived included) and logs; what macOS keeps for it (window places, the folders it was
# allowed to use, its browser window's data); and inside each music library it made, the
# app's own records (.musicorg) and its unfinished downloads (_Staging).
#
# What stays: the songs, videos and films downloaded with it (each library's Music folder
# and the films and videos in your Movies folder), as they are, for you to keep or delete.
#
# Made by scripts/package_app.sh, from the project's scripts/package/ folder.

setopt NULL_GLOB
ID="org.musicorganizer.app"
LIB="$HOME/Library"
TRASH="$HOME/.Trash"

say() { print -r -- "$*"; }

say ""
say "Uninstall Bear Media"
say "===================="
say ""

# Bear Media is closed first (it stops its engine as it quits).
if pgrep -f "Bear Media.app/Contents/MacOS/MusicOrganizer" >/dev/null; then
    say "Closing Bear Media…"
    osascript -e "tell application id \"$ID\" to quit" >/dev/null 2>&1
    for _ in {1..30}; do
        pgrep -f "Bear Media.app/Contents/" >/dev/null || break
        sleep 1
    done
    pkill -f "Bear Media.app/Contents/" 2>/dev/null
fi

typeset -a going libraries
add() { [[ -e "$1" || -L "$1" ]] && going+=("$1"); }

# Every copy of the app: where it's usually put, beside this script, and where Spotlight
# has seen one.
for app in "/Applications/Bear Media.app" "$HOME/Applications/Bear Media.app" \
    "${0:A:h}/Bear Media.app" "${0:A:h:h:h}" ${(f)"$(mdfind "kMDItemCFBundleIdentifier == '$ID'" 2>/dev/null)"}; do
    [[ "$app" == *.app && -f "$app/Contents/Info.plist" ]] || continue
    [[ "$(defaults read "$app/Contents/Info" CFBundleIdentifier 2>/dev/null)" == "$ID" ]] || continue
    (( ${going[(Ie)$app]} )) || add "$app"
done

# The libraries it made or opened: the engine's last one, and each profile's.
config="$LIB/Application Support/MusicOrganizer/config.json"
if [[ -f "$config" ]]; then
    last="$(plutil -extract last_library raw -o - "$config" 2>/dev/null)"
    [[ -n "$last" ]] && libraries+=("$last")
fi
profiles="$(defaults export "$ID" - 2>/dev/null | plutil -extract profiles raw -o - - 2>/dev/null | base64 -D 2>/dev/null)"
for root in ${(f)"$(print -r -- "$profiles" | grep -o '"libraryRoot":"[^"]*"' | sed 's/^"libraryRoot":"//; s/"$//; s#\\/#/#g')"}; do
    [[ -n "$root" ]] && libraries+=("$root")
done
for root in ${(u)libraries}; do
    # Only a Bear Media library: one with the app's records in it.
    [[ -d "$root/.musicorg" ]] || continue
    add "$root/.musicorg"
    add "$root/_Staging"
done

# Its settings, caches and logs, and what macOS keeps for it.
add "$LIB/Application Support/MusicOrganizer"
add "$LIB/Caches/MusicOrganizer"
add "$LIB/Logs/MusicOrganizer"
add "$LIB/Preferences/$ID.plist"
add "$LIB/Saved Application State/$ID.savedState"
add "$LIB/Caches/$ID"
add "$LIB/HTTPStorages/$ID"
add "$LIB/HTTPStorages/$ID.binarycookies"
add "$LIB/WebKit/$ID"
add "$LIB/Application Support/$ID"

if (( ${#going} == 0 )); then
    say "Nothing of Bear Media's was found on this Mac."
else
    say "These go to the Trash:"
    for item in $going; do say "  • ${item/#$HOME/~}"; done
fi
say ""
say "These stay, for you to keep or delete: the songs and videos in each library's Music"
say "folder, and the films and videos in your Movies folder."
for root in ${(u)libraries}; do
    [[ -d "$root/Music" ]] && say "  • ${root/#$HOME/~}/Music"
done
say ""
read "answer?Type yes and press Return to uninstall Bear Media: "
if [[ "${answer:l}" != "yes" ]]; then
    say "Nothing was changed."
    exit 0
fi

# macOS forgets the app: the folders it was allowed to use, and its place among apps.
tccutil reset All "$ID" >/dev/null 2>&1
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
for item in $going; do
    [[ "$item" == *.app ]] && "$LSREGISTER" -u "$item" >/dev/null 2>&1
done
# Its settings, out of macOS's memory as well as off the disk.
defaults delete "$ID" >/dev/null 2>&1

failed=0
for item in $going; do
    [[ -e "$item" || -L "$item" ]] || continue
    name="${item:t}"
    target="$TRASH/$name"
    [[ -e "$target" ]] && target="$TRASH/$name $(date +%H.%M.%S)"
    if ! mv "$item" "$target" 2>/dev/null; then
        say "Couldn't move ${item/#$HOME/~} to the Trash."
        failed=1
    fi
done

say ""
if (( failed )); then
    say "Most of Bear Media is in the Trash; what's named above couldn't be moved."
else
    say "Bear Media is in the Trash. Empty the Trash to finish."
fi
say "You can close this window."
