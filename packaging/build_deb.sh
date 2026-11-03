#!/usr/bin/env bash
# Build tagmender_<version>_all.deb (Debian 12+, Ubuntu 24.04+, Linux Mint 22+).
#
#   packaging/build_deb.sh [--discogs DIR] [--coverup DIR] [--out DIR]
#
# The Discogs Enrichment and CoverUp plugin code (GPL-2.0-or-later, like TagMender) is bundled
# under /usr/lib/tagmender/vendor/ so the package works without clones; they are read from
# next to this folder (../picard-discogs-enrichment, ../picard-coverup), --discogs / --coverup,
# or $TAGMENDER_DISCOGS_PLUGIN / $TAGMENDER_COVERUP_DIR.
set -euo pipefail
cd "$(dirname "$0")/.."
SRC=$PWD
DISCOGS=${TAGMENDER_DISCOGS_PLUGIN:-$SRC/../picard-discogs-enrichment}
COVERUP=${TAGMENDER_COVERUP_DIR:-$SRC/../picard-coverup}
OUT=$SRC/dist
while [ $# -gt 0 ]; do
  case "$1" in
    --discogs) DISCOGS=$2; shift 2;;
    --coverup) COVERUP=$2; shift 2;;
    --out) OUT=$2; shift 2;;
    *) echo "unknown option $1" >&2; exit 2;;
  esac
done
VERSION=$(python3 -c "import sys; sys.path.insert(0, '.'); import version; print(version.VERSION)")
[ -f "$DISCOGS/discogs_mapping.py" ] || { echo "Discogs Enrichment not found at $DISCOGS" >&2; exit 1; }
[ -f "$COVERUP/pipeline.py" ] || { echo "CoverUp not found at $COVERUP" >&2; exit 1; }

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
LIB=$STAGE/usr/lib/tagmender
mkdir -p "$LIB/vendor" "$STAGE/usr/bin" "$STAGE/DEBIAN" "$STAGE/usr/share/applications" \
         "$STAGE/usr/share/icons/hicolor/scalable/apps" "$STAGE/usr/lib/systemd/user" \
         "$STAGE/usr/share/doc/tagmender"

# TagMender itself: the tracked Python files (gui/ and tagscript/ included), the fonts, the
# app's icons (Lucide, with its LICENSE) and translations; not tests/ or the developer tools in tools/
git ls-files '*.py' 'coverup_fonts/*' 'assets/icons/lucide/*' 'assets/i18n/*' | grep -v -e '^tests/' -e '^tools/' | while read -r f; do
  install -D -m 0644 "$f" "$LIB/$f"
done
chmod 0755 "$LIB/tagmender.py"

vendor() {   # copy a plugin's tracked files (no tests, no .git) and stamp its commit
  local from=$1 to=$2
  ( cd "$from" && git ls-files 2>/dev/null || find . -type f -not -path './.git/*' ) | grep -v '^tests/' |
    while read -r f; do install -D -m 0644 "$from/$f" "$to/$f"; done
  git -C "$from" rev-parse HEAD > "$to/VENDORED_COMMIT" 2>/dev/null || echo unknown > "$to/VENDORED_COMMIT"
}
vendor "$DISCOGS" "$LIB/vendor/picard-discogs-enrichment"
vendor "$COVERUP" "$LIB/vendor/picard-coverup"

# python-discid (pure Python; reads a CD in the drive through libdiscid0): next to tagmender.py,
# so it imports without pip. The wheel is kept in .cache/wheels, so later builds work offline.
DISCID_WHEEL=.cache/wheels/discid-1.4.2-py3-none-any.whl
if [ ! -f "$DISCID_WHEEL" ]; then
  mkdir -p .cache/wheels
  python3 -m pip download --quiet --no-deps --disable-pip-version-check -d .cache/wheels discid==1.4.2 ||
    echo "warning: python-discid not downloaded (offline?): the package will not read a CD in the drive" >&2
fi
if [ -f "$DISCID_WHEEL" ]; then
  echo "0d4f854ce31ecef2bc978d6fbca624998a081c068cac8389a3656fff1a56ca32  $DISCID_WHEEL" | sha256sum -c --quiet
  python3 - "$DISCID_WHEEL" "$LIB" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as z:
    for n in z.namelist():
        if n.startswith("discid/") and not n.endswith("/"):
            z.extract(n, sys.argv[2])
        elif n.endswith("COPYING.LESSER"):
            open(sys.argv[2] + "/discid/COPYING.LESSER", "wb").write(z.read(n))
PY
  chmod -R u=rwX,go=rX "$LIB/discid"
fi

cat > "$STAGE/usr/bin/tagmender" <<'EOF'
#!/bin/sh
exec /usr/bin/python3 /usr/lib/tagmender/tagmender.py "$@"
EOF
chmod 0755 "$STAGE/usr/bin/tagmender"

install -m 0644 packaging/io.github.herculepirate.TagMender.desktop "$STAGE/usr/share/applications/"
install -m 0644 packaging/tagmender.svg \
  "$STAGE/usr/share/icons/hicolor/scalable/apps/io.github.herculepirate.TagMender.svg"
sed 's#^ExecStart=.*#ExecStart=/usr/bin/tagmender watch --nice#' contrib/tagmender-watch.service \
  > "$STAGE/usr/lib/systemd/user/tagmender-watch.service"
for d in README.md LICENSE THIRD_PARTY_NOTICES.md docs/*.md; do
  [ -f "$d" ] && install -m 0644 "$d" "$STAGE/usr/share/doc/tagmender/$(basename "$d")"
done

SIZE=$(du -sk "$STAGE/usr" | cut -f1)
cat > "$STAGE/DEBIAN/control" <<EOF
Package: tagmender
Version: $VERSION
Section: sound
Priority: optional
Architecture: all
Installed-Size: $SIZE
Depends: python3 (>= 3.10), python3-mutagen (>= 1.45), python3-pil, python3-fonttools
Recommends: ffmpeg, libchromaprint-tools, tesseract-ocr, libdiscid0
Suggests: python3-numpy, python3-pyzbar, tesseract-ocr-rus, tesseract-ocr-jpn, python3-pyside6.qtwidgets,
 python3-pyside6.qtmultimedia
Maintainer: herculepirate <56596623+herculepirate@users.noreply.github.com>
Homepage: https://github.com/herculepirate/tagmender
Description: identify, tag and organise music with MusicBrainz and Discogs
 TagMender identifies albums and single tracks with MusicBrainz and Discogs
 as equal sources (plus fingerprints, disc IDs, barcodes and catalogue
 numbers), previews every change, writes tags with the MusicBrainz tag
 mapping (so files read the same in other taggers and players), and
 organises files with journaled, undoable operations. It runs from the command line, as a background
 watcher (systemd user service) and, with PySide6, as a desktop app
 (Qt Multimedia for its player; libdiscid0 to read a CD in the drive).
 Every user brings their own keys (Discogs token, AcoustID key, ...):
 tagmender setup, or the app's Preferences > Keys.
EOF
mkdir -p "$OUT"
DEB="$OUT/tagmender_${VERSION}_all.deb"
dpkg-deb --root-owner-group --build "$STAGE" "$DEB" >/dev/null
( cd "$OUT" && sha256sum "$(basename "$DEB")" > "$(basename "$DEB").sha256" )
echo "$DEB"
