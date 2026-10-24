#!/usr/bin/env bash
# Build tagmender_<version>_all.deb (Debian 12+, Ubuntu 24.04+, Linux Mint 22+).
#
#   packaging/build_deb.sh [--discogs DIR] [--coverup DIR] [--out DIR]
#
# The Discogs Enrichment and CoverUp plugin code (GPL-2.0-or-later, like TagMender) is bundled
# under /usr/lib/tagmender/vendor/ so the package works without your Github clones; a clone
# in ~/Documents/Github, or $TAGMENDER_DISCOGS_PLUGIN / $TAGMENDER_COVERUP_DIR, still wins.
set -euo pipefail
cd "$(dirname "$0")/.."
SRC=$PWD
DISCOGS=${TAGMENDER_DISCOGS_PLUGIN:-$HOME/Documents/Github/picard-discogs-enrichment}
COVERUP=${TAGMENDER_COVERUP_DIR:-$HOME/Documents/Github/picard-coverup}
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

# TagMender itself: the tracked Python files and fonts (no tests)
git ls-files '*.py' 'coverup_fonts/*' | grep -v '^tests/' | while read -r f; do
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
Recommends: ffmpeg, libchromaprint-tools, tesseract-ocr
Suggests: python3-numpy, python3-pyzbar, tesseract-ocr-rus, tesseract-ocr-jpn, python3-pyside6.qtwidgets
Maintainer: herculepirate <56596623+herculepirate@users.noreply.github.com>
Homepage: https://github.com/herculepirate/tagmender
Description: identify, tag and organise music with MusicBrainz and Discogs
 TagMender identifies albums and single tracks with MusicBrainz and Discogs
 as equal sources (plus fingerprints, disc IDs, barcodes and catalogue
 numbers), previews every change, writes tags the way MusicBrainz Picard
 does without needing Picard, and organises files with journaled,
 undoable operations. It runs from the command line, as a background
 watcher (systemd user service) and, with PySide6, as a desktop app.
EOF
mkdir -p "$OUT"
DEB="$OUT/tagmender_${VERSION}_all.deb"
dpkg-deb --root-owner-group --build "$STAGE" "$DEB" >/dev/null
( cd "$OUT" && sha256sum "$(basename "$DEB")" > "$(basename "$DEB").sha256" )
echo "$DEB"
