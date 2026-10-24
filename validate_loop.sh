#!/usr/bin/env bash
# validate_loop.sh - keep validate.py running over a library: every pass checks the albums
# not yet checked, and again every album that did not agree once the matcher has changed
# (a fix). Stop: touch "$OUT/STOP" (or Ctrl+C).
#   LIB=/media/unraidmedia2/music/extreme_music ./validate_loop.sh
set -u
LIB="${LIB:?set LIB to the library folder}"
OUT="${OUT:-$HOME/Downloads/validate_$(basename "$LIB")}"
PAUSE="${PAUSE:-1800}"
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$OUT"
: "${DISCOGS_TOKEN:?export DISCOGS_TOKEN first}"
while [ ! -e "$OUT/STOP" ]; do
  echo "=== pass $(date -Iseconds)" | tee -a "$OUT/loop.log"
  python3 "$HERE/validate.py" "$LIB" --out-dir "$OUT" 2>&1 | tee -a "$OUT/loop.log" | tail -12
  for _ in $(seq "$PAUSE"); do [ -e "$OUT/STOP" ] && break; sleep 1; done
done
rm -f "$OUT/STOP"
echo "stopped"
