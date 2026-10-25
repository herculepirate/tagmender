# Crash fuzzers for the file operations

Slow (minutes), so not part of `run_tests.sh`. Each scenario (in-place tagging with cover and
lyrics, move, move without tag changes, move to another filesystem, copy, destinations that
already are hard links, rename in place, rollback, rollback to another filesystem) is run once
per kill point: a forked child is killed (`os._exit`, as by SIGKILL or power loss) at the N-th
link/unlink/rename/replace/mkdir/rmdir, journal write or copy. Then `recover`, `undo` and a
second `undo` must bring the library back exactly, with no hidden `.tm-*` file left.
`fuzz_recover.py` also kills `recover` itself at every point.

    TAGMENDER_DISCOGS_PLUGIN=~/Documents/Github/picard-discogs-enrichment python3 -W ignore tests/fuzz/fuzz_crash.py
    TAGMENDER_DISCOGS_PLUGIN=~/Documents/Github/picard-discogs-enrichment python3 -W ignore tests/fuzz/fuzz_recover.py

`fuzz_multi.py` does the same for plans whose albums interact (one album moving into
another's folder, one album's destination being another's source, one album failing in
various orders). `fuzz_raise.py` makes the N-th file operation fail once with an I/O error
instead of a crash (a share dropping for a moment), for every N, in all those scenarios.

    python3 -W ignore tests/fuzz/fuzz_multi.py
    python3 -W ignore tests/fuzz/fuzz_raise.py

Each ends with "NO FINDINGS" when everything came back.
