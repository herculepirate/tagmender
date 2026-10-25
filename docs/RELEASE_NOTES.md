# Release notes

## Unreleased

**New: editions of one album in separate folders** (profile naming settings, and in the
desktop app): "Separate editions in folder names" (Off / Edition / Edition + reissue year)
gives `2004 - I [2019 Vinyl]`, `2004 - I [2014 CD]`, `1986 - Reign in Blood [2015 WEB]`;
an original CD and albums without a media tag keep their folder. The edition can go into
the Album tag too. A release mixing media (a CD with a 7" bonus disc) stays in one folder.
The desktop app shows the matching Picard script lines. John's three Picard naming scripts
are presets (`all-music`, `various-artists`, `classical-performer`); naming now also knows
Picard's `$set`, `$rsearch`, `$sortmulti`, `$in`, `$add`, `$sub`, `$mul`, `$div`.

**File operations reviewed** in fifteen independent rounds (tests/test_fileops*.py, and four
crash/error fuzzers in tests/fuzz/)
- An interrupted album is put back as it was by `tagmender recover`; run the plan again.
- A new job waits for an interrupted one to be recovered, or for one running elsewhere.
- A loose track in a library root, or one file named on the command line, is planned on
  its own (it had taken every file below its folder with it); nested albums stay separate.
- Disc folders: "CD 1", "Disc One", "Side A"; a folder named only by a number is a disc
  only beside its siblings 1, 2, ... ("Adele/19" and "Adele/21" are albums). An untagged
  disc set stays one album.
- Two releases the template gives one folder get folders of their own; `max_album_words`
  is honoured; `remove_empty_dirs: false` keeps emptied folders.
- A saved plan is refused for a file edited since (also when size and time were kept).
- `apply`/`run` exit 1 when albums were blocked (e.g. an unmounted destination);
  `recover` exits 1 on problems.
- Crash-safe at every step: `recover` works out from the files what a crash left and completes
  the journal, so undo can always put things back; undo can be re-run and leaves files whose
  tags were edited after the job alone (`undo --force` to override).
- Albums move with their whole folder: scans, covers, per-disc logs and cue sheets; per-track
  lyrics files follow their track; cue sheets and playlists are rewritten per disc.
- Long file names, case-only renames on SMB shares, unmounted shares, look-alike folders,
  hidden ("...And Justice for All") and reserved ("AUX.") names, symlinks out of the library,
  ownership, and timestamps (as Picard is set) are handled.
- Grouping: tracks missing the album artist tag join the album the others name; "Album
  (Disc One)", "(Disc 2: Live)" and "(CD 1) [Remastered]" are one album; same-titled albums
  by different artists in one folder stay apart unless they are one compilation.
- Copy profiles keep to their own destination (a second copy profile, or a changed
  destination, gets its own copies) and find copies an earlier version filed under
  "Garfunkel & Simon/".
- Staging copies are named `.tm-<job>-<hash>.flac.part`, so no player or lister takes one
  for a track; a plain move of DSD/WAV/WavPack files keeps no backup copy and is undone by
  renaming only.
- Undo refuses to run while a share the job used is unmounted, and leaves an album's cover,
  scans, log and cue with its music when the music stays (edited since the job).
- `tagmender jobs prune` also removes old fetched artwork (not that of plans still waiting).
- A track already in place is recognised under any spelling of its path (`run .`, a
  symlinked folder, another letter case on an SMB/exFAT share): never taken for a hard link.
- Plans and journals hold absolute paths, and file names that are not valid UTF-8.
- Undo moves an album back whole or not at all; restores keep preserved timestamps; a
  folder left holding only .DS_Store/Thumbs.db/desktop.ini is removed, as Picard does
  (the files are kept for undo); new folders take their parent's owner and mode.
- EAC cue sheets naming the rip's WAV files are rewritten; the watcher retries failed and
  blocked albums, and copies an album once its destination is mounted again.
- `recover` waits for a share that is down (it never rebuilds an album on the system disk);
  undo of a copy needs only the destination; folders put back by undo, rollback and recover
  take their parent's owner and mode; undo of a hard-linked copy keeps later source edits.
- Undo keeps an album together when a later job moved it, when it was downloaded again
  into the inbox, or when its rewritten cue or new cover changed since; lyrics a player
  updated go back with their edits; an undo cut short can be finished.
- A saved plan still applies after a share was remounted with another device number.
- Undo tells a track deleted since from one a later job moved (`--force` puts the rest
  back), keeps a copy whose tags were edited with its time kept, and is not redone by the
  watcher; moves work between bind mounts / mergerfs branches of one disk.
- The watcher and `retry` fetch artwork and apply the tagging level, as `run` does; an
  unresolved loose track goes straight into `move_to`.

**New: tagging levels** (profile `tag_level`, and in the desktop app's profile settings)
- Basic: album artist, artist, album, title, track and disc numbers and totals, date, genre.
- Enthusiast (default): everything MusicBrainz and Discogs supply, as Picard writes it.
- Audiophile: also ReplayGain 2.0 (R128 for Opus), lyrics from LRCLIB (`.lrc` files, plain
  lyrics embedded), AcoustID IDs, the disc ID and verdict from the rip log, dynamic range, and
  a warning for lossless files made from lossy ones. Measurements are cached and never
  replace a value the file already has; new `.lrc` files are undone with the job.
- A lower level never removes tags; `level_fields` adds or drops single fields.

**Fixed (from the first live plan, Exhorder)**
- Discogs data is now applied the way your Picard's Discogs Enrichment plugin applies it, with
  its settings read from Picard.ini: merge mode (yours: override), style mode, marketplace
  numbers off unless switched on, performers / companies / identifiers, protected tags, notes,
  original date in Date, track tags, collaboration splitting. Before, Discogs only filled gaps,
  the profile forced Discogs' genre, and marketplace numbers were written regardless.
- Tags Picard is told to preserve (Options > Tags) are kept as they are on the file - e.g.
  `totaltracks` "08" is no longer rewritten to "8".
- A disc set's `cover.jpg` goes in the album folder, not in "Disc 01".
- Plans and `doctor` say which Discogs Enrichment settings were used and where they came from.

**Known differences from Picard**
- Genres from MusicBrainz itself (Picard's Options > Genres) are not fetched; with merge mode
  override, Discogs' genre replaces them in Picard too.
- Tags not written by either source are left on the file (Picard's "Clear existing tags"
  removes them).
- Track numbers on disc sets are counted within each disc; the plugin's automatic path counts
  through the whole release.

## 0.2.0 (2026-10-24)

One engine behind a command line, a background watcher and a desktop app.

**New**
- `tagmender plan | apply | run | undo | recover | jobs | watch | scan | unresolved | retry |
  formats | doctor | setup | gui`, with JSON output and stable exit codes.
- Picard-free tagging with Picard's tag mappings; MusicBrainz and Discogs as equal sources.
- Previews of every change; staged, audio-verified, never-overwriting writes; journal, crash
  recovery, per-album rollback, undo.
- Validated profiles (copy/move/in place, destination, naming, thresholds, field policies,
  formats, unresolved handling); example Lossy profile.
- Naming templates (documented Picard subset) and filename limits with shortening.
- Watched folders (polling; safe on shares), systemd user service.
- Reason codes in `TAGMENDER_REASON`; rotating, redacted log; diagnostics export.
- Desktop app (PySide6).
- Packages: `.deb` (architecture-independent) and AppImage (x86_64, glibc ≥ 2.28).

**Fixed**
- CoverUp's generated covers failed without PyQt6 (headless).
- The matcher's User-Agent now follows MusicBrainz's requested format.

**Verified (this build)**
- `./run_tests.sh`: all suites pass on CPython 3.13.17 and on the AppImage's CPython 3.12.13
  (414+ tests).
- `.deb`: built, installed with `dpkg` on Ubuntu 24.04 (its apt dependencies were satisfied by
  pip modules: apt mirrors were unreachable from the build machine), then: `--version`,
  `doctor` (bundled plugins found), `scan`, `plan`, `run --yes` (verified write), `unresolved`,
  `undo`, `gui` start.
- AppImage: run with a clean environment (no system Python modules): the same commands, GUI
  start with no arguments, and the whole test suite on its interpreter.
- Benchmark (local ext4, 100 albums / 1,000 FLAC files, providers off): grouping 6,000+ files/s,
  planning ~950 files/s, verified apply ~127 files/s (~49 MB/s), undo 1.6 s, peak memory 51 MB.
  Identification itself is bounded by providers: MusicBrainz ~1 request/s, Discogs ~54/min.

**Not verified here**
- Live MusicBrainz, Discogs, AcoustID, Cover Art Archive traffic (unreachable from the build
  machine; covered by recorded-style responses).
- A screen reader; desktop environments other than Qt's offscreen platform.

**Checksums**: `dist/*.sha256` next to each artifact.

**Updating**: install the new `.deb` over the old one, or replace the AppImage file. Settings,
rules, state and job history are kept (see `docs/MIGRATION.md`).
