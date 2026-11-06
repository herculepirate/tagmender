#!/usr/bin/env bash
# Build TagMender-<version>-x86_64.AppImage: a portable desktop + command-line build with its own
# Python (python-build-standalone, via uv), mutagen, Pillow, fonttools and PySide6, and the
# built-in Discogs and CoverUp plugins. No system Python packages needed.
#
#   packaging/build_appimage.sh [--python DIR] [--out DIR] [--with-player]
#
# --with-player also bundles Qt Multimedia (from PySide6-Addons, the same version; adds its FFmpeg
# libraries) so the app's player plays; without it the player's controls say there is no sound
# output and everything else works. Reading a CD in the drive is bundled: python-discid from
# requirements-appimage.txt and libdiscid.so.0 copied from this machine (sudo apt install
# libdiscid0; it needs only glibc 2.14). Without it the build warns and Look Up CD reads rip logs
# and pasted tables of contents only. pykakasi (Japanese titles) and pyzbar (barcodes on scans) are
# bundled too; pyzbar uses the system's zbar library (sudo apt install libzbar0), which pulls in too
# many system libraries to bundle.
#
# Needs: uv (pip install uv) unless --python points at an unpacked python-build-standalone,
# and appimagetool (downloaded to ./.cache when missing).
# Runs on x86_64 Linux with glibc 2.28 or newer (PySide6 wheels); ffmpeg, fpcalc and tesseract
# are used from the system when present (not bundled).
set -euo pipefail
cd "$(dirname "$0")/.."
SRC=$PWD
OUT=$SRC/dist
PYDIR=""
PLAYER=0
while [ $# -gt 0 ]; do
  case "$1" in
    --python) PYDIR=$2; shift 2;;
    --out) OUT=$2; shift 2;;
    --with-player) PLAYER=1; shift;;
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
mkdir -p "$APPDIR/usr/lib/tagmender"
cp -a "$PYDIR" "$APPDIR/usr/python"
rm -f "$APPDIR"/usr/python/lib/python3.*/EXTERNALLY-MANAGED     # our own private copy: pip may install into it
"$APPDIR/usr/python/bin/python3" -m pip install --quiet --no-warn-script-location --disable-pip-version-check \
  -r packaging/requirements-appimage.txt
if [ "$PLAYER" = 1 ]; then          # PySide6-Addons at the pinned PySide6 version, for QtMultimedia only
  PYSIDE=$(sed -n 's/^PySide6-Essentials==//p' packaging/requirements-appimage.txt)
  "$APPDIR/usr/python/bin/python3" -m pip install --quiet --no-warn-script-location --disable-pip-version-check \
    --no-deps "PySide6-Addons==$PYSIDE"
  MM=""                             # keep libQt6Multimedia*, QtMultimedia*.so and its FFmpeg libraries
else
  MM=Multimedia,
fi
# trim what a runtime never needs
rm -rf "$APPDIR"/usr/python/lib/python3.*/test "$APPDIR"/usr/python/lib/python3.*/idlelib \
       "$APPDIR"/usr/python/lib/python3.*/tkinter "$APPDIR"/usr/python/lib/tcl* "$APPDIR"/usr/python/lib/tk* \
       "$APPDIR"/usr/python/include
SP=$(echo "$APPDIR"/usr/python/lib/python3.*/site-packages)
rm -rf "$SP"/PySide6/{Qt/qml,Qt/translations,examples,include,typesystems,glue,scripts,lupdate,linguist,designer,assistant}
eval "rm -rf \"\$SP\"/PySide6/Qt/lib/libQt6{WebEngine,Pdf,Quick3D,3D,Designer,Qml,Quick,Charts,DataVisualization,Graphs,${MM}Location,Positioning,Sensors,Bluetooth,Nfc,SerialPort,Scxml,RemoteObjects,TextToSpeech,VirtualKeyboard,WebSockets,WebChannel,HttpServer,SpatialAudio}*"
eval "rm -rf \"\$SP\"/PySide6/Qt{WebEngine,Pdf,Quick3D,3D,Designer,Qml,Quick,Charts,DataVisualization,Graphs,${MM}Location,Positioning,Sensors,Bluetooth,Nfc,SerialPort,Scxml,RemoteObjects,TextToSpeech,WebSockets,WebChannel,HttpServer,SpatialAudio}*.{so,abi3.so,pyi}" 2>/dev/null || true
if [ "$PLAYER" = 1 ]; then          # the rest of PySide6-Addons: not used
  rm -rf "$SP"/PySide6/Qt/lib/libQt6MultimediaQuick* \
         "$SP"/PySide6/Qt/plugins/{qmltooling,sceneparsers,geometryloaders,renderers,renderplugins,position,sensors,texttospeech,webview,designer}
fi
find "$APPDIR/usr/python" -name __pycache__ -prune -exec rm -rf {} +
LIBDISCID=$(ldconfig -p 2>/dev/null | sed -n 's/.*libdiscid\.so\.0 (libc6,x86-64) => //p' | head -1)
if [ -n "$LIBDISCID" ]; then        # its own folder: AppRun puts only this one on LD_LIBRARY_PATH
  install -D -m 0644 "$(readlink -f "$LIBDISCID")" "$APPDIR/usr/lib/discid/libdiscid.so.0"
else
  echo "warning: libdiscid.so.0 not found (sudo apt install libdiscid0): the AppImage will not read a CD in the drive" >&2
fi

# TagMender: the tracked Python files (gui/ and tagscript/ included), the built-in plugins, the
# fonts, the app's icons (Lucide, with its LICENSE) and translations; not tests/ or the developer
# tools in tools/
git ls-files '*.py' 'plugins_builtin/*' 'coverup_fonts/*' 'assets/icons/lucide/*' 'assets/i18n/*' | grep -v -e '^tests/' -e '^tools/' | while read -r f; do
  install -D -m 0644 "$f" "$APPDIR/usr/lib/tagmender/$f"
done

cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
# No arguments (started from a menu or a file manager): the desktop app. Otherwise the command line.
HERE=$(dirname "$(readlink -f "$0")")
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
export LD_LIBRARY_PATH="$HERE/usr/lib/discid${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"   # libdiscid only
[ $# -eq 0 ] && set -- gui
exec "$HERE/usr/python/bin/python3" "$HERE/usr/lib/tagmender/tagmender.py" "$@"
EOF
chmod +x "$APPDIR/AppRun"
sed 's#^Exec=.*#Exec=tagmender gui#' packaging/io.github.herculepirate.TagMender.desktop \
  > "$APPDIR/io.github.herculepirate.TagMender.desktop"
cp packaging/tagmender.svg "$APPDIR/io.github.herculepirate.TagMender.svg"
# the thumbnail file managers show for the AppImage: a PNG (appimagetool will refuse an SVG link)
cp assets/icon/png/tagmender-256.png "$APPDIR/.DirIcon"
install -D -m 0644 assets/icon/png/tagmender-256.png \
  "$APPDIR/usr/share/icons/hicolor/256x256/apps/io.github.herculepirate.TagMender.png"
mkdir -p "$APPDIR/usr/share/metainfo"
cp packaging/io.github.herculepirate.TagMender.metainfo.xml "$APPDIR/usr/share/metainfo/"

chmod -R a+rX "$APPDIR"          # (every file readable by every user, whatever the checkout's modes)

IMG="$OUT/TagMender-$VERSION-x86_64.AppImage"
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --no-appstream "$APPDIR" "$IMG" >/dev/null
( cd "$OUT" && sha256sum "$(basename "$IMG")" > "$(basename "$IMG").sha256" )
echo "$IMG"
