# Architecture and decisions

## Modules

| Concern | Module |
|---|---|
| Audio discovery, album grouping | `engine.Engine.albums_in` (disc folders joined, mixed folders split by album tags) |
| Providers | `match_albums` (MusicBrainz, Discogs, CTDB, AcoustID, ISRC, links), `web_tag` (web), `mb_tags` (MB release → tags), Discogs Enrichment's `discogs_mapping` (Discogs release → tags) |
| Fingerprints | `match_albums.fingerprint` (fpcalc, read-only) |
| Candidates and scoring | `match_core.score_candidate`, `classify`, `combine` |
| Reconciliation | `engine.Engine._identified` + `engine.enrich` (same pressing only; MusicBrainz base, Discogs applied as the Discogs Enrichment plugin applies it, settings from `tagger_settings`) |
| Profiles | `profiles` (schema version, defaults, validation, migration) |
| This computer's settings | `app_settings` (`settings.json`: backups, duplicates folder, skipped folders, saved lookups, scans, plugin folder; read by the CLI, the watcher and the app) |
| Rules | `exclusions` |
| Naming | `naming` (template language), `filenames` (safety, limits, shortening) |
| Planning | `plan` (diffs, policies, paths, companions, artwork, conflicts, preview) |
| Execution, recovery | `executor` (stage → verify → commit, journal, rollback, recover, undo, prune) |
| Tag storage | `tagio` (the MusicBrainz tag mapping per format), `payload` (audio-data digests) |
| Artwork | `artwork` (CAA → Discogs → CoverUp by default, staged; earlier profiles keep CoverUp first) |
| Jobs and watching | `executor` (job history), `watcher` |
| Reliability | `logsetup`, `credentials`, `reasons`, `doctor`, `companions`, `connection` (Test Connection, and which service is busy or down during a run) |
| Interfaces | `tagmender.py` (CLI), `gui/` (PySide6: `window` the main window, `actions` every command with its menu and shortcut,
one module per page, `inspector`, `preferences`, `sheets` window-modal sheets, `style` tokens and palettes,
`icons` tinted Lucide icons, `i18n` translations (docs/TRANSLATING.md), `common`, `work`, `models`, `widgets`,
`rows` grouped inset lists for settings, `profile_editor` every profile setting in one list),
`contrib/tagmender-watch.service` |

State lives in XDG folders: `~/.config/tagmender` (profiles, rules, keys, settings.json), `~/.local/state/tagmender`
(state.db: rules history, ignores, jobs, watcher; jobs/<id>/ journals and backups; logs/; cache).

## Decisions

1. **Keep the 0.1 tools and their stack** (Python, mutagen, SQLite, stdlib argparse). The matcher
   holds years of library-specific evidence handling; the engine calls `match_albums.process()`
   unchanged in its decisions and adds only switches (providers on/off, other files of a mixed
   folder, typed provider errors).
2. **Write tags itself** with the MusicBrainz tag mapping for each format (ID3, Vorbis, MP4,
   APEv2, ASF; see THIRD_PARTY_NOTICES.md for the reference code followed), so files read the
   same in other taggers and players. No other tagger is needed.
3. **Discogs tags come from the Discogs Enrichment plugin's own code**, so a Discogs-only album is
   tagged as the plugin would tag it. No copy of the plugin is kept in the repo; packages bundle
   a stamped copy (GPL-2.0-or-later).
4. **Same pressing or nothing.** MusicBrainz and Discogs data are merged only when the matcher
   proved they describe the same pressing (link or twin). Otherwise the deciding provider's
   release stands alone. No MusicBrainz ID is ever created for a Discogs release.
5. **Staged copies, not in-place edits.** Every write goes to a copy next to the destination;
   audio digests (container-aware, `payload.py`) and tag read-back are checked before an atomic
   replace or a no-clobber link. This costs one copy per changed file (≈50 MB/s locally) and
   buys: no half-written files, no silent audio change, no overwritten files.
6. **Undo from tag snapshots** (the 0.1 `backup_tags`/`restore_tags`, byte-exact in its tests)
   for ID3/MP4/Vorbis; a full copy only for formats it does not cover (APEv2, ASF).
7. **Polling watcher.** Works the same on local disks and NFS/SMB shares, where inotify misses
   changes; each pass doubles as reconciliation. Albums are compared by signature, so
   TagMender's own writes never queue them again.
8. **One preview text** (`plan.preview`) for CLI and GUI, so both say exactly the same.
9. **Logs are not state.** Journals and history live in the state DB and job folders; the log
   rotates at 104,857,600 bytes with 3 backups and is written under an flock.
10. **File operations follow common tagger conventions, with a journal on top.**
    - Companion files: like "move additional files" (patterns and named subfolders) and
      "move entire source folder content" in other tools, TagMender moves the album's folder tree:
      companion-extension files beside the music, everything in subfolders without music
      (Scans, covers), nothing from a folder holding another album's music. Per-track files
      (`01 a.lrc`) follow their track's new name; cue sheets and playlists are rewritten.
    - Case-only renames on case-insensitive shares go through a temporary name.
    - "Delete empty directories" / "remove empty source folders": only folders the move
      emptied, never a profile root, recreated by undo.
    - Never overwrite: a taken name gets a stable
      ` ~1a2b` suffix; a name taken only by case or Unicode form counts as taken with
      Windows-compatible names, and an existing folder is reused in its on-disk spelling.
    - "Preserve timestamps" follows the user's tagger settings.
    - Beyond that: every commit point is journalled; `recover` re-derives what a crash left
      from the files on disk; undo is resumable and leaves files edited after the job alone.
11. **beets not used at runtime**: it would be a second library database writing the same files.
    Its documentation informed the template and plugin-free design only.

## Research consulted (2026-10-24)

| Source | Version / commit | Used for |
|---|---|---|
| MusicBrainz tag mapping reference code (see THIRD_PARTY_NOTICES.md) | commit 98e1ad1 | tag mappings per format; unknown-tag rules |
| MusicBrainz tagging documentation: naming, compatibility, tag mapping | latest | naming presets, Windows-compatible names, field list |
| MusicBrainz API, rate limiting | musicbrainz.org/doc (checked 2026-10-24) | 1 req/s, User-Agent format, 503 handling |
| Discogs developers | discogs.com/developers (checked 2026-10-24) | 60/min with token, image auth, User-Agent |
| AcoustID web service | acoustid.org/webservice (checked 2026-10-24) | 3 req/s, non-commercial, meta parameters |
| mutagen | 1.48.1 | all tag I/O |
| [Discogs Enrichment](https://github.com/herculepirate/picard-discogs-enrichment) | 3dc00512 (v0.3.7) | Discogs extraction |
| [CoverUp](https://github.com/herculepirate/picard-coverup) | d1d36d14 | artwork pipeline |
| AppImage tooling | appimagetool (continuous), python-build-standalone CPython 3.12.13 via uv | portable build |

File operations (2026-10-25): the file naming options of open-source taggers and foobar2000's
File Operations options (as discussed on hydrogenaudio.org).
