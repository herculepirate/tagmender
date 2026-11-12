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
# Runs on x86_64 Linux with glibc 2.28 or newer (PySide6 wheels). fpcalc is bundled (Chromaprint's
# static release, checksum-pinned); tesseract (+ eng/jpn/rus/osd) and libzbar from the
# native-tools workflow's archive (--native-tools PATH, or .cache/native-tools-x86_64.tar.gz);
# ffmpeg from the system.
set -euo pipefail
cd "$(dirname "$0")/.."
SRC=$PWD
OUT=$SRC/dist
PYDIR=""
PLAYER=0
NATIVE=""                          # tesseract + leptonica + libzbar from the native-tools workflow
while [ $# -gt 0 ]; do
  case "$1" in
    --python) PYDIR=$2; shift 2;;
    --out) OUT=$2; shift 2;;
    --with-player) PLAYER=1; shift;;
    --native-tools) NATIVE=$2; shift 2;;
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
# fpcalc (AcoustID fingerprints): Chromaprint's own static release, pinned (LGPL-2.1+; it brings
# FFmpeg's decoders, LGPL-2.1+). Sources: https://github.com/acoustid/chromaprint/releases/tag/v1.5.1
FPCALC_VER=1.5.1
FPCALC_SHA=4d7433a7f778e5946d7225230681cbcd634e153316ecac87c538c33ac32387a5
FPCALC_TGZ=$CACHE/chromaprint-fpcalc-$FPCALC_VER-linux-x86_64.tar.gz
if [ ! -f "$FPCALC_TGZ" ]; then
  curl -fsSL -o "$FPCALC_TGZ" \
    "https://github.com/acoustid/chromaprint/releases/download/v$FPCALC_VER/chromaprint-fpcalc-$FPCALC_VER-linux-x86_64.tar.gz"
fi
echo "$FPCALC_SHA  $FPCALC_TGZ" | sha256sum -c --quiet - || { echo "fpcalc download does not match its checksum" >&2; exit 1; }

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
install -D -m 0755 /dev/null "$APPDIR/usr/tools/bin/fpcalc"
tar xzf "$FPCALC_TGZ" -O "chromaprint-fpcalc-$FPCALC_VER-linux-x86_64/fpcalc" > "$APPDIR/usr/tools/bin/fpcalc"
printf 'fpcalc %s: Chromaprint (LGPL-2.1+, https://github.com/acoustid/chromaprint), statically linked with FFmpeg (LGPL-2.1+, https://ffmpeg.org); unmodified release binary.\n' \
  "$FPCALC_VER" > "$APPDIR/usr/tools/NOTICE"
NATIVE=${NATIVE:-$CACHE/native-tools-x86_64.tar.gz}
if [ -f "$NATIVE" ]; then         # (issue #4: OCR and barcodes on scans without the system's tools)
  tar xzf "$NATIVE" -C "$APPDIR/usr/tools" --strip-components=1
  mkdir -p "$APPDIR/usr/tools/libexec"
  mv "$APPDIR/usr/tools/bin/tesseract" "$APPDIR/usr/tools/libexec/tesseract"
  cat > "$APPDIR/usr/tools/bin/tesseract" <<'WRAP'
#!/bin/sh
# the bundled tesseract with its own language data (the system's tessdata is never mixed in)
D=$(dirname "$(readlink -f "$0")")/..
TESSDATA_PREFIX="$D/share/tessdata" exec "$D/libexec/tesseract" "$@"
WRAP
  chmod 0755 "$APPDIR/usr/tools/bin/tesseract"
  "$APPDIR/usr/tools/bin/tesseract" --version >/dev/null 2>&1 \
    || { echo "the bundled tesseract does not run" >&2; exit 1; }
else
  echo "warning: no native tools ($NATIVE; the native-tools workflow builds them): the AppImage uses the system's tesseract and zbar" >&2
fi
if [ -n "$LIBDISCID" ]; then        # its own folder: AppRun puts only this one on LD_LIBRARY_PATH
  install -D -m 0644 "$(readlink -f "$LIBDISCID")" "$APPDIR/usr/lib/discid/libdiscid.so.0"
else
  echo "warning: libdiscid.so.0 not found (sudo apt install libdiscid0): the AppImage will not read a CD in the drive" >&2
fi

# TagMender: the tracked Python files (gui/ and tagscript/ included), the built-in plugins, the
# fonts, the app's icons (Lucide, with its LICENSE) and translations; not tests/ or the developer
# tools in tools/
git ls-files '*.py' 'plugins_builtin/*' 'coverup_fonts/*' 'assets/icons/lucide/*' 'assets/i18n/*' 'assets/icon/tagmender-icon.svg' 'assets/ocr_order.json' | grep -v -e '^tests/' -e '^tools/' | while read -r f; do
  install -D -m 0644 "$f" "$APPDIR/usr/lib/tagmender/$f"
done

cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
# No arguments (started from a menu or a file manager): the desktop app. Otherwise the command line.
HERE=$(dirname "$(readlink -f "$0")")
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
export LD_LIBRARY_PATH="$HERE/usr/lib/discid${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"   # libdiscid only
export PATH="$HERE/usr/tools/bin:$PATH"     # the bundled fpcalc and tesseract first; the system's after them
[ -f "$HERE/usr/tools/lib/libzbar.so.0" ] && export TAGMENDER_LIBZBAR="$HERE/usr/tools/lib/libzbar.so.0"
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
