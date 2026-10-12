#!/bin/bash
# Builds Taskflow.app from Sources/ and, with --install, copies it to ~/Applications.
# The app runs taskflow.py from this taskflow folder with the Python below, so rebuild if either moves: python.org's 3.11
# when it is there, else the python3 on your PATH; PYTHON=/path/to/python3 macapp/build_app.sh picks another.
# It builds in ~/Library/Caches, outside this folder: codesign refuses bundles that carry cloud-sync metadata.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TF_DIR="$(dirname "$HERE")"
if [ -z "${PYTHON:-}" ]; then   # not chosen: python.org's 3.11 when it is there, else the python3 on the PATH
    PYTHON="/Library/Frameworks/Python.framework/Versions/3.11/bin/python3"
    [ -x "$PYTHON" ] || PYTHON="$(command -v python3 || true)"
fi
BUILD="$HOME/Library/Caches/local.taskflow/build"
APP="$BUILD/Taskflow.app"
ICNS="$HERE/Taskflow.icns"
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"

[ -n "$PYTHON" ] && [ -x "$PYTHON" ] || { echo "Python 3 not found${PYTHON:+ at $PYTHON}: install it, or set PYTHON=/path/to/python3" >&2; exit 1; }
echo "Using $PYTHON"
[ -f "$TF_DIR/taskflow.py" ] || { echo "taskflow.py not found in $TF_DIR" >&2; exit 1; }

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

echo "Compiling…"
swiftc -swift-version 5 -O -target "$(uname -m)-apple-macos13.0" \
    -framework AppKit -framework WebKit \
    "$HERE"/Sources/*.swift -o "$APP/Contents/MacOS/Taskflow"

sed -e "s|@PYTHON@|$PYTHON|" -e "s|@SCRIPT@|$TF_DIR/taskflow.py|" \
    -e "s|@BUILD@|$(date +%Y%m%d%H%M)|" -e "s|@DATE@|$(date +%Y-%m-%d)|" \
    "$HERE/Info.plist.in" > "$APP/Contents/Info.plist"
plutil -lint "$APP/Contents/Info.plist" >/dev/null

if [ ! -f "$ICNS" ]; then        # delete Taskflow.icns to redraw it from make_icon.swift
    echo "Drawing the icon…"
    ICONSET="$BUILD/Taskflow.iconset"
    rm -rf "$ICONSET"; mkdir -p "$ICONSET"
    swift "$HERE/make_icon.swift" "$BUILD/icon_1024.png" >/dev/null
    for s in 16 32 128 256 512; do
        sips -z $s $s "$BUILD/icon_1024.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
        sips -z $((s * 2)) $((s * 2)) "$BUILD/icon_1024.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
    done
    iconutil -c icns "$ICONSET" -o "$ICNS"
    rm -rf "$ICONSET"
fi
cp -X "$ICNS" "$APP/Contents/Resources/Taskflow.icns"

xattr -cr "$APP"
codesign --force --sign - "$APP"
codesign --verify "$APP"
echo "Built $APP"

if [ "${1:-}" = "--install" ]; then
    mkdir -p "$HOME/Applications"
    if pgrep -xq Taskflow; then
        echo "Taskflow is running; quit it first, then run: $0 --install" >&2
        exit 1
    fi
    rm -rf "$HOME/Applications/Taskflow.app"
    ditto --norsrc --noextattr "$APP" "$HOME/Applications/Taskflow.app"
    codesign --verify "$HOME/Applications/Taskflow.app"
    "$LSREGISTER" -f "$HOME/Applications/Taskflow.app"
    echo "Installed $HOME/Applications/Taskflow.app"
fi
