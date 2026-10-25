# Release notes

## Unreleased

**File operations reviewed** (tests/test_fileops.py; an independent review found the rest)
- Crash-safe at every step: `recover` works out from the files what a crash left and completes
  the journal, so undo can always put things back; undo can be re-run and leaves files whose
  tags were edited after the job alone (`undo --force` to override).
- Albums move with their whole folder: scans, covers, per-disc logs and cue sheets; per-track
  lyrics files follow their track; cue sheets and playlists are rewritten per disc.
- Long file names, case-only renames on SMB shares, unmounted shares, look-alike folders,
  hidden ("...And Justice for All") and reserved ("AUX.") names, symlinks out of the library,
  ownership, and timestamps (as Picard is set) are handled.

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
