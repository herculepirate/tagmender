#!/usr/bin/env bash
# Build TagMender-<version>-x86_64.AppImage: a portable desktop + command-line build with its own
# Python (python-build-standalone, via uv), mutagen, Pillow, fonttools and PySide6, and the
# Discogs Enrichment and CoverUp plugin code. No system Python packages needed.
#
#   packaging/build_appimage.sh [--discogs DIR] [--coverup DIR] [--python DIR] [--out DIR]
#
# Needs: uv (pip install uv) unless --python points at an unpacked python-build-standalone,
# and appimagetool (downloaded to ./.cache when missing).
# Runs on x86_64 Linux with glibc 2.28 or newer (PySide6 wheels); ffmpeg, fpcalc and tesseract
# are used from the system when present (not bundled).
set -euo pipefail
cd "$(dirname "$0")/.."
SRC=$PWD
DISCOGS=${TAGMENDER_DISCOGS_PLUGIN:-$HOME/Documents/Github/picard-discogs-enrichment}
COVERUP=${TAGMENDER_COVERUP_DIR:-$HOME/Documents/Github/picard-coverup}
OUT=$SRC/dist
PYDIR=""
while [ $# -gt 0 ]; do
  case "$1" in
    --discogs) DISCOGS=$2; shift 2;;
    --coverup) COVERUP=$2; shift 2;;
    --python) PYDIR=$2; shift 2;;
    --out) OUT=$2; shift 2;;
    *) echo "unknown option $1" >&2; exit 2;;
  esac
done
VERSION=$(python3 -c "import sys; sys.path.insert(0, '.'); import version; print(version.VERSION)")
CACHE=$SRC/.cache
mkdir -p "$CACHE" "$OUT"
if [ -z "$PYDIR" ]; then
  uv python install 3.12 --install-dir "$CACHE/python" >/dev/null
  PYDIR=$(ls -d "$CACHE"/python/cpython-3.12.*-linux-x86_64-gnu | sort -V | tail -1)
fi
TOOL=$CACHE/appimagetool
if [ ! -x "$TOOL" ]; then
  curl -fsSL -o "$TOOL" https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
  chmod +x "$TOOL"
fi

APPDIR=$(mktemp -d)/TagMender.AppDir
trap 'rm -rf "$(dirname "$APPDIR")"' EXIT
mkdir -p "$APPDIR/usr/lib/tagmender/vendor"
cp -a "$PYDIR" "$APPDIR/usr/python"
rm -f "$APPDIR"/usr/python/lib/python3.*/EXTERNALLY-MANAGED     # our own private copy: pip may install into it
"$APPDIR/usr/python/bin/python3" -m pip install --quiet --no-warn-script-location --disable-pip-version-check \
  -r packaging/requirements-appimage.txt
# trim what a runtime never needs
rm -rf "$APPDIR"/usr/python/lib/python3.*/test "$APPDIR"/usr/python/lib/python3.*/idlelib \
       "$APPDIR"/usr/python/lib/python3.*/tkinter "$APPDIR"/usr/python/lib/tcl* "$APPDIR"/usr/python/lib/tk* \
       "$APPDIR"/usr/python/include
SP=$(echo "$APPDIR"/usr/python/lib/python3.*/site-packages)
rm -rf "$SP"/PySide6/{Qt/qml,Qt/translations,examples,include,typesystems,glue,scripts,lupdate,linguist,designer,assistant}
rm -rf "$SP"/PySide6/Qt/lib/libQt6{WebEngine,Pdf,Quick3D,3D,Designer,Qml,Quick,Charts,DataVisualization,Graphs,Multimedia,Location,Positioning,Sensors,Bluetooth,Nfc,SerialPort,Scxml,RemoteObjects,TextToSpeech,VirtualKeyboard,WebSockets,WebChannel,HttpServer,SpatialAudio}*
rm -rf "$SP"/PySide6/Qt{WebEngine,Pdf,Quick3D,3D,Designer,Qml,Quick,Charts,DataVisualization,Graphs,Multimedia,Location,Positioning,Sensors,Bluetooth,Nfc,SerialPort,Scxml,RemoteObjects,TextToSpeech,WebSockets,WebChannel,HttpServer,SpatialAudio}*.{so,abi3.so,pyi} 2>/dev/null || true
find "$APPDIR/usr/python" -name __pycache__ -prune -exec rm -rf {} +

git ls-files '*.py' 'coverup_fonts/*' | grep -v '^tests/' | while read -r f; do
  install -D -m 0644 "$f" "$APPDIR/usr/lib/tagmender/$f"
done
vendor() {
  local from=$1 to=$2
  ( cd "$from" && git ls-files ) | grep -v '^tests/' | while read -r f; do install -D -m 0644 "$from/$f" "$to/$f"; done
  git -C "$from" rev-parse HEAD > "$to/VENDORED_COMMIT"
}
vendor "$DISCOGS" "$APPDIR/usr/lib/tagmender/vendor/picard-discogs-enrichment"
vendor "$COVERUP" "$APPDIR/usr/lib/tagmender/vendor/picard-coverup"

cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
# No arguments (started from a menu or a file manager): the desktop app. Otherwise the command line.
HERE=$(dirname "$(readlink -f "$0")")
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
[ $# -eq 0 ] && set -- gui
exec "$HERE/usr/python/bin/python3" "$HERE/usr/lib/tagmender/tagmender.py" "$@"
EOF
chmod +x "$APPDIR/AppRun"
sed 's#^Exec=.*#Exec=tagmender gui#' packaging/io.github.herculepirate.TagMender.desktop \
  > "$APPDIR/io.github.herculepirate.TagMender.desktop"
cp packaging/tagmender.svg "$APPDIR/io.github.herculepirate.TagMender.svg"
mkdir -p "$APPDIR/usr/share/metainfo"
cp packaging/io.github.herculepirate.TagMender.metainfo.xml "$APPDIR/usr/share/metainfo/"

IMG="$OUT/TagMender-$VERSION-x86_64.AppImage"
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --no-appstream "$APPDIR" "$IMG" >/dev/null
( cd "$OUT" && sha256sum "$(basename "$IMG")" > "$(basename "$IMG").sha256" )
echo "$IMG"
