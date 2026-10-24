# Continuation notes

Read `docs/ASSESSMENT.md` first: it marks every requirement done, partial, deferred or blocked.
The steps below are in a sensible order; each stays usable on its own. Nothing that works should
be rewritten.

## First, on your own machine

1. `git fetch` the branch (or apply the bundle) and run `./run_tests.sh`.
2. `tagmender doctor`, then `tagmender setup` (Discogs token, AcoustID, fanart.tv).
3. Try the engine on a few albums you know well, without applying:
   `tagmender plan "/media/unraidmedia2/music/extreme_music/Some Band" --profile <yours>`.
   Live MusicBrainz/Discogs traffic could not be exercised from the build sandbox; this is the
   first real check of `mb_tags` and the Discogs path against the live services.
4. Compare its tags with what Picard + Discogs Enrichment wrote on an album tagged by
   `tag_albums.py`. Differences are bugs in `mb_tags.py` or `engine._identified`.

## Next steps

1. **Per-album attempt record.** One table holding provider attempts and outcomes, evidence,
   adapter versions and retry eligibility per album (today spread over plan JSON, journal and
   watcher state). `tagmender unresolved` and the GUI's Needs attention would read it.
2. **Lyrics as a planned step.** Move `enrich.lyrics_for` into the planner (embedded and/or
   `.lrc`), with the same undo, instead of the separate `enrich.py` run.
3. **Workers.** `watch.workers` > 1 (identification is network-bound; writes stay serial per
   folder via the existing file locks).
4. **Classical and Indian conventions.** Use MusicBrainz work relationships
   (`inc=work-rels+work-level-rels+artist-rels`) for composer/work/movement; a raga/tala/form
   field set for Hindustani and Carnatic albums, filled only from sourced data.
5. **Pause/resume** in the GUI (the executor already stops cleanly between albums).
6. **Optional API** over a Unix socket (`plan`, `apply`, `jobs`, progress), reusing the engine.
7. **Flatpak**, after deciding folder access (document portals vs `--filesystem=` for the
   library and inbox) and how the watcher runs (background portal).
8. **Optional local AI** (Ollama on the P920 / Unraid): explain unresolved albums and propose
   rules or profile changes as typed actions validated by the planner. Never a metadata source.

## Conventions to keep

- Nothing changes without a plan; apply needs `--yes`, a confirmation, or an unattended profile.
- Every write: staged copy, audio digest unchanged, tags read back, no overwrite, journal, undo.
- Never loosen thresholds silently; rules are literal and case-insensitive by default.
- Tokens come from the environment or 0600 files; never printed, logged or exported.
- Tests use generated audio and recorded responses, with isolated XDG folders.
- The public repo is authored as herculepirate.
- Changing `match_albums.py` or `match_core.py` changes the matcher's version stamp; saved
  matches are then re-evaluated by `match_loop.sh` (cheap: lookups come from the cache).
