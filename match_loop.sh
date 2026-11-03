#!/usr/bin/env bash
# match_loop.sh - keeps match_albums.py running over a whole library folder,
# and starts a fresh round whenever match_albums.py / match_core.py change.
#
# It only ever writes inside $OUT (CSVs, state db, log). It never touches the
# music library.
#
#   export DISCOGS_TOKEN=...            (read -rs -p "Discogs token: " DISCOGS_TOKEN && export DISCOGS_TOKEN)
#   AUDIT=~/Downloads/audit_ambient.csv nohup ./match_loop.sh &   run in the background
#   tail -f ~/Downloads/matching/run.log
#   touch ~/Downloads/matching/STOP     ask it to stop after the current round
#
#   RECHECK_DAYS=30 ...                 also look again, once a day, at review/nomatch
#                                       albums last checked more than 30 days ago
#
# Settings (environment): AUDIT, OUT, POLL (seconds between change checks), EXTRA (extra args),
# RECHECK_DAYS (re-check unresolved albums older than this many days, once a day)
set -u
cd "$(dirname "$0")" || exit 1
AUDIT="${AUDIT:-}"
OUT="${OUT:-$HOME/Downloads/matching}"
POLL="${POLL:-30}"
EXTRA="${EXTRA:-}"
RECHECK_DAYS="${RECHECK_DAYS:-}"
last_round=0
mkdir -p "$OUT"
# One runner at a time: two would share one database and double the API rate.
exec 9>"$OUT/runner.lock"
flock -n 9 || { echo "match_loop.sh is already running for $OUT"; exit 1; }
LOG="$OUT/run.log"
STATUS="$OUT/loop_status.json"
STOP="$OUT/STOP"

[ -n "${DISCOGS_TOKEN:-}" ] || { echo "Set DISCOGS_TOKEN first"; exit 1; }
[ -n "$AUDIT" ] || { echo "Set AUDIT to the audit CSV (from audit_untagged.py)"; exit 1; }
[ -f "$AUDIT" ] || { echo "Audit CSV not found: $AUDIT"; exit 1; }

scripts_hash() { cat match_albums.py match_core.py 2>/dev/null | md5sum | cut -d' ' -f1; }
write_status() {  # state round rc hash
  printf '{"state":"%s","round":%s,"last_exit":%s,"scripts":"%s","updated":"%s"}\n' \
    "$1" "$2" "$3" "$4" "$(date -Is)" > "$STATUS"
}

rm -f "$STOP"
round=0
while [ ! -e "$STOP" ]; do
  round=$((round + 1))
  h="$(scripts_hash)"
  echo "=== round $round start $(date -Is) scripts=$h" >> "$LOG"
  write_status running "$round" -1 "$h"
  # shellcheck disable=SC2086
  # No --retry-* here: when the scripts change, every album decided by the old
  # version is re-evaluated automatically (and its old result kept until then).
  RECHECK=""
  [ -n "$RECHECK_DAYS" ] && RECHECK="--recheck-days $RECHECK_DAYS"
  python3 -u match_albums.py "$AUDIT" --out-dir "$OUT" --ocr $RECHECK $EXTRA >> "$LOG" 2>&1 &
  pid=$!
  # A full round can take many hours. If a fix is installed (scripts change)
  # or STOP appears, end this round now; finished albums are already saved.
  # SIGTERM, not SIGINT: background jobs of a non-interactive shell ignore SIGINT.
  while kill -0 "$pid" 2>/dev/null; do
    if [ -e "$STOP" ] || [ "$(scripts_hash)" != "$h" ]; then
      echo "=== round $round interrupted $(date -Is): scripts changed or STOP" >> "$LOG"
      kill -TERM "$pid" 2>/dev/null
      break
    fi
    sleep "$POLL"
  done
  wait "$pid"
  rc=$?
  echo "=== round $round finished $(date -Is) exit=$rc" >> "$LOG"
  write_status waiting "$round" "$rc" "$h"
  last_round=$(date +%s)
  # Idle until the scripts change (a fix was installed), STOP appears, or (with
  # RECHECK_DAYS) a day has passed and unresolved albums are due for a fresh look.
  while [ ! -e "$STOP" ] && [ "$(scripts_hash)" = "$h" ]; do
    if [ -n "$RECHECK_DAYS" ] && [ $(( $(date +%s) - last_round )) -ge 86400 ]; then
      break
    fi
    sleep "$POLL"
  done
done
write_status stopped "$round" "${rc:-0}" "$(scripts_hash)"
echo "=== stopped $(date -Is)" >> "$LOG"
