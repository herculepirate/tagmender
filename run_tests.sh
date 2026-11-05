#!/usr/bin/env bash
# Run every test suite. Needs ffmpeg, and the two plugin clones (defaults: next to this folder, ../picard-...).
#   ./run_tests.sh                       with python3
#   PYTHON=/path/to/python3 ./run_tests.sh
# Tests never touch your settings or music: each uses temporary folders and its own XDG dirs.
set -u
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
export TAGMENDER_DISCOGS_PLUGIN=${TAGMENDER_DISCOGS_PLUGIN:-$PWD/../picard-discogs-enrichment}
export TAGMENDER_COVERUP_DIR=${TAGMENDER_COVERUP_DIR:-$PWD/../picard-coverup}
export QT_QPA_PLATFORM=${QT_QPA_PLATFORM:-offscreen}
# never your own tagger settings (Flatpak or native): tests that need some set their own
export TAGMENDER_TAGGER_INI=${TAGMENDER_TAGGER_INI_FOR_TESTS:-/nonexistent/tagger.ini}
unset TAGMENDER_PICARD_INI
# no stale bytecode: a file edited twice in one second at the same size keeps its old .pyc
find . -name __pycache__ -not -path "./.git/*" -prune -exec rm -rf {} +
export PYTHONDONTWRITEBYTECODE=1
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
    # (and why: each failure's error line, so a failure seen only on another machine says what it was)
    printf '%s\n' "$out" | grep -E '^[A-Za-z_.]*(Error|Exception)\b' | head -10 | sed 's/^/    /'
    # a suite that crashed says no FAIL or ERROR: show how it ended
    if ! printf '%s\n' "$out" | grep -qE '^(FAIL|ERROR):'; then
      printf '%s\n' "$out" | tail -15 | sed 's/^/    /'
      # it stopped before naming what failed: once more, test by test, to show the last one
      echo "    -- again, naming each test:"
      XDG_CONFIG_HOME=$(mktemp -d) XDG_STATE_HOME=$(mktemp -d) "$PY" -W ignore -m unittest -v "$t" 2>&1 \
        | grep -E ' \.\.\. |^(FAIL|ERROR|Traceback|  File|[A-Za-z]+Error)' | tail -25 | sed 's/^/    /'
    fi
  fi
done
echo "$total tests in all"
exit $fail
