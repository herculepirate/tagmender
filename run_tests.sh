#!/usr/bin/env bash
# Run every test suite. Needs ffmpeg, and the two plugin clones (defaults: ~/Documents/Github/...).
#   ./run_tests.sh                       with python3
#   PYTHON=/path/to/python3 ./run_tests.sh
# Tests never touch your settings or music: each uses temporary folders and its own XDG dirs.
set -u
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
export TAGMENDER_DISCOGS_PLUGIN=${TAGMENDER_DISCOGS_PLUGIN:-$HOME/Documents/Github/picard-discogs-enrichment}
export TAGMENDER_COVERUP_DIR=${TAGMENDER_COVERUP_DIR:-$HOME/Documents/Github/picard-coverup}
export QT_QPA_PLATFORM=${QT_QPA_PLATFORM:-offscreen}
fail=0
total=0
for t in tests/test_*.py; do
  args=()
  case "$t" in
    tests/test_match.py) args=("$TAGMENDER_DISCOGS_PLUGIN");;
    tests/test_coverup_batch.py) args=("$TAGMENDER_COVERUP_DIR");;
  esac
  out=$(XDG_CONFIG_HOME=$(mktemp -d) XDG_STATE_HOME=$(mktemp -d) "$PY" -W ignore "$t" "${args[@]}" 2>&1)
  rc=$?
  ran=$(printf '%s\n' "$out" | grep -oE '^Ran [0-9]+' | grep -oE '[0-9]+' || echo 0)
  total=$((total + ran))
  last=$(printf '%s\n' "$out" | tail -1)
  printf '%-34s %4s tests  %s\n' "$t" "$ran" "$last"
  if [ $rc -ne 0 ]; then
    fail=1
    printf '%s\n' "$out" | grep -E '^(FAIL|ERROR):' | head -10
  fi
done
echo "$total tests in all"
exit $fail
