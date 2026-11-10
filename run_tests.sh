#!/usr/bin/env bash
# Run every test suite. Needs ffmpeg. The built-in Discogs and CoverUp plugins are in plugins_builtin/.
#   ./run_tests.sh                       with python3, several files at once
#   PYTHON=/path/to/python3 ./run_tests.sh
#   ./run_tests.sh --jobs 1              one file at a time
#   ./run_tests.sh --shard 2/4           the second of four parts (CI runs them side by side)
#   ./run_tests.sh tests/test_a.py ...   only these files
# Each file's line says how long it took; a file running longer than --timeout (300 s) is stopped.
# Tests run without the network (tests/_offline): set TAGMENDER_TESTS_NETWORK=1 for a live check.
# Tests never touch your settings or music: each uses temporary folders and its own XDG dirs.
set -u
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
export TAGMENDER_DISCOGS_PLUGIN=${TAGMENDER_DISCOGS_PLUGIN:-$PWD/plugins_builtin/discogs}
export TAGMENDER_COVERUP_DIR=${TAGMENDER_COVERUP_DIR:-$PWD/plugins_builtin/coverup}
export QT_QPA_PLATFORM=${QT_QPA_PLATFORM:-offscreen}
# never your own tagger settings (Flatpak or native): tests that need some set their own
export TAGMENDER_TAGGER_INI=${TAGMENDER_TAGGER_INI_FOR_TESTS:-/nonexistent/tagger.ini}
unset TAGMENDER_PICARD_INI
# no stale bytecode: a file edited twice in one second at the same size keeps its old .pyc
find . -name __pycache__ -not -path "./.git/*" -prune -exec rm -rf {} +
export PYTHONDONTWRITEBYTECODE=1
# no test reaches the network (tests/_offline/sitecustomize.py)
export PYTHONPATH="$PWD/tests/_offline${PYTHONPATH:+:$PYTHONPATH}"
export PYTHON="$PY"
# each file in its own process with its own settings, state, data and cache folders: tests/_runner.py
exec "$PY" tests/_runner.py "$@"
