# Release notes

## Unreleased

**Tags written as Picard 3 writes them, checked against Picard's own code.** Picard's settings
in Options > Metadata are now followed (read from Picard.ini, or a profile's `"picard"` entries):
standardized artist names (Picard 3's default writes "The Beatles" for a credit "Beatles",
keeping credits that are one of the artist's former names; a Picard 2 Picard.ini keeps credited
names), sort names from the alias matching the credit, translated artist names, album and track
titles, release relationships (producer, engineer, mixer ... credited on the release: on in
Picard), track relationships (composer, lyricist, performers and instruments, works and their
languages), the Various Artists name and `compilation` on Various Artists releases, Unicode
punctuation as ASCII, and the preferred release countries. `tests/test_picard_parity.py`
compares the result with what Picard 3.0.2's own code makes of its test data. The text
representation's language is no longer written as LANGUAGE (Picard writes the works' languages
there). `tagmender doctor` shows the settings followed.

**File formats, compared frame by frame with files Picard tagged.** Performer credits now go
where Picard puts them (ID3 TMCL, Vorbis and APEv2 PERFORMER "name (instrument)"; none in MP4
and WMA) and producer, engineer, arranger, mixer and DJ mixer into ID3 TIPL - before, they were
custom tags (TXXX:performer:guitar, PERFORMER:GUITAR) that players and Picard did not read as
credits; such tags are rewritten the right way on the next run. An ID3v2.3 tag now stays valid
v2.3 when TagMender writes to it: before, its date moved into a v2.4 frame (TDRC) that strictly
v2.3 readers do not see. Also as Picard: movement number/total (MVIN, MP4 ©mvi/©mvc), license
(WCOP), website (WOAR), lyrics language, MP4 compilation (cpil), ReplayGain names in upper case
in ID3, MP4 and APEv2, and the WMA fields Picard knows. WMA files are no longer rewritten on
every run for a track total they cannot hold, and an M4A with a compilation flag no longer
fails to read.

**Desktop app**: the results of background work (preview, apply, watched folders ...) are now
handled on the window's own thread; before, they ran on the worker's thread, which could leave
the window showing stale numbers or crash it.

**New: the artist's photo at Enthusiast and Audiophile.** artist.jpg in the artist folder (what
Plex, Jellyfin, Navidrome and LMS show for the artist) and in each track as an "Artist/performer"
picture beside the cover (at most 800 px: it is in every track). From CoverUp's artist sources
(fanart.tv, TheAudioDB, Wikimedia Commons, Discogs), looked up once per artist and only while a
track or the artist folder lacks it; never over an artist.jpg that is there, never into a folder
not named after the album artist or a library root, never for a compilation; undo removes it.
Profile: artwork.artist {"enabled", "file", "embed"}; in the app, "Artist photo" under the covers.
Reviewed (tests/test_review8_artist.py): never another artist's photo (a front search is reused
only for its own album and artist); none for a collaboration ("A & B"); only beside a front
cover, never a track's only picture; an artist folder's folder.jpg or artist.jpg is the artist's
photo (used, never doubled; "Beatles, The" is The Beatles); looked up once per artist across runs
(30 days for "none found"), never again for formats or folders that cannot take it, or for a
copy profile's copies; a loose single moved out of an artist folder leaves its pictures there;
undo is never held back by an artist.jpg a media server replaced. Ninth review
(tests/test_review9_*.py): a folder.jpg beside music of the artist folder's own (a loose album)
is that album's cover, not the artist's photo; a self-titled album ("Metallica/Metallica/...")
is never taken for the artist folder; an album holding another album ("Live 1995") or a disc
set holding a bonus album takes its cover and log along; a failed download is asked again,
only "none found" waits 30 days; artists without a MusicBrainz ID are remembered by their
folder, and a library a move profile already filed is not looked up again. Tenth review
(tests/test_review10_*.py): an artist folder holding the artist's albums keeps its photo when a
loose release (a 7" single, a flat EP) moves out; the artist folder's photo - also the one a
move or copy files into - is the one embedded, never a second downloaded one; folder.jpg beside
a single loose track is still the artist's; photos are kept between runs only by MusicBrainz
ID and refreshed after 180 days, "none found" after 7; `tagmender artist-photos` lists them and
`--forget` drops a wrong one.

**Small covers are replaced** (artwork.replace "larger_only", the default; "Replace an album's
own cover when it is smaller than the minimum size" in the app). A cover smaller than
artwork.min_px (500 px) - embedded in a track, or the folder's cover/folder/front file - is
replaced by a larger one found; when nothing larger is found anywhere, by the album's own cover
enlarged (artwork.enlarge, not under 250 px). Only the front cover is replaced: other pictures
(back, booklet) stay, a cover.png stays a PNG, nothing is generated over an album's own cover.
The old pictures and the old file are kept by the job; undo puts them back. Reviewed
(tests/test_review2_cover_watch.py): a back cover or booklet page is never taken for the front
(nor overwritten: MP3 picture descriptions stay apart); a folder shared with other music lends
no album its cover; a cover embedded as large as TagMender embeds is not replaced again on the
next run; a copy or move never rewrites the source's cover file; a small cover.jpg beside a
large embedded front gets that front as it is. Third review (tests/test_review3_cover_watch.py):
a cover.jpg that is a symbolic link is left alone (it stopped the album being tagged); CoverUp's
embed_max_side is read before CoverUp is first used (else albums were replaced on every run);
a copy profile judges covers at its copies (existing copies get the larger cover; a second run
looks nothing up), and never rewrites a source cover when no track is copied; untagged tracks
of other albums in the folder make it a shared folder; an unreadable largest cover falls back to
the next, and nothing is generated over an album that has covers; a track with only a back
cover gets a front beside it; a watcher pass never opens the library inside the watched folder;
in the Flatpak the background watcher runs in its own sandbox instance (it outlives the app).
Fourth review (tests/test_review4_cover_watch.py): a partly copied album is judged at the
copies it has; an untagged album holding a second copy of itself (FLAC and MP3) is one album;
a lone disc folder ("Album/Disc 01") keeps its cover where the album's is looked for; an
album with a good cover of its own never gets a generated one and needs no lookup; CoverUp's
embed size applies only to profiles using CoverUp; an idle watcher pass never walks the
watched folder for a loose track; undo leaves a cover the user put in after the job alone;
the background service gets the plugin locations (never keys; those from
~/.config/tagmender/env if present). Fifth review (tests/test_review5_cover_watch.py): an artist
folder's photo (Artist/folder.jpg beside a loose single and the artist's albums) is never taken
for the single's cover, replaced or embedded; an album held twice in one folder (FLAC and MP3)
gets its small cover replaced once, then no more lookups; covers are judged by the tracks
TagMender writes; a finished job is marked ENDED, so the watcher no longer rereads every job's
journal before every pass and job. Sixth review (tests/test_review6_cover.py): the artwork
step and the plan ask one question - does this folder hold another album's music? - so a picture
in an artist folder is never an untagged single's cover, a folder that will never get a cover
file is not looked up again on every run, and an album's untagged copies and its files in
_old/2delete folders do not stop its small cover being replaced. Seventh review
(tests/test_review7_cover.py): a small Bonus/Extras/B-Sides folder (untagged, or by the same
artist) does not make the album's folder shared, so its cover.jpg is its own; no cover is ever
generated beside a real cover file; a loose single moved into a folder of its own gets a cover
file; WavPack, APE and DSF albums (never embedded into) are not looked up on every run; the
shared-folder question stops at the first other album (a loose track at a library root no
longer reads the library); "01 - a.mp3" is a copy of "01 a.flac".

**Watched folders file what is dropped into them.** Each watched folder belongs to a profile;
an album folder or loose tracks dropped there are identified, tagged and moved (or copied)
into that profile's library once nothing in them changes. The app's Watched folders page
ties a folder to a profile (and moves it between profiles), shows what happens to what is
dropped there, keeps watching while the app is open, installs the background service, lists
the plans waiting for approval (Show plan / Apply / Apply all / Dismiss) and what was done
recently; `tagmender watch --add/--remove/--list/--pending/--approve/--dismiss/--service`
does the same from the command line. Fixed on the way: a library inside the watched folder
had every album TagMender filed there identified again as a new drop; the background service
only noticed a folder added in the app after a restart (profiles are now read on every pass);
the service's systemd sandbox (ProtectSystem=strict) made every library outside home, /media
and /mnt read-only - USB drives under /run/media, /srv - and can keep a user service from
starting where unprivileged user namespaces are off; it is gone, the service runs as you do.
Only one TagMender watches at a time (the app, the service and a terminal no longer race).
Reviewed independently (tests/test_watch_review1.py): a library inside the watched folder is
never taken for new drops (what was there before watching included); an approved plan that
could not be carried out (the drive out) is planned again; a pass never looks at an album
while a job or an approval is moving it; closing the app during a check waits for it; one
plan waits per album (a retry replaces it, never piles up); in place + asks first, an approved
album is not identified again; Stop works in the Flatpak (a stop request, not a signal);
`watch --add` with a misspelt profile changes nothing; "Look every" reaches a running app; a
second watcher exits with an error so the service retries; a copy that keeps the files' old
times (cp -a, rsync -a) is waited for by the folder's own time, and rsync's temporary files
count as a download in progress.
`watch.settle_minutes` and `watch.interval_seconds` are checked; a whole drive cannot be
watched. **New: a Flatpak** (packaging/flatpak/) with access to every folder, sharing
~/.config/tagmender with the other installs; background watching through the Background portal.

**Artwork / CoverUp reviewed** in three rounds (tests/test_artwork_review*.py): covers are always JPEG
(a TIFF/WebP/PNG/CMYK scan is converted, transparency onto white), the embedded copy is
capped at CoverUp's embed_max_side so a hi-res scan never fails an album, ID3v2.3 stays
v2.3, a cover that is gone at apply time is skipped instead of failing the album, CoverUp
reads the same Picard.ini as the rest of TagMender, `min_px`/`max_bytes` are honoured,
another album's pictures are never taken (loose tracks, shared folders), and cover.jpg is
not added beside a cover.webp/gif/tif. An album sharing its folder is described to CoverUp
by its own files (never another album's embedded front or release IDs); 16-bit scans,
EXIF-rotated scans, CoverUp's jpeg_quality, earlier generated covers and Synology @eaDir
folders are handled. No cover.jpg goes into a folder shared with other music; CoverUp
searches only the release the identification gave; an album's own picture that nothing
beats is used; colour profiles are kept; CoverUp's file_max_side applies to cover.jpg.

**New: editions of one album in separate folders** (profile naming settings, and in the
desktop app): "Separate editions in folder names" (Off / Edition / Edition + reissue year)
gives `2004 - I [2019 Vinyl]`, `2004 - I [2014 CD]`, `1986 - Reign in Blood [2015 WEB]`;
an original CD and albums without a media tag keep their folder. The edition can go into
the Album tag too. A release mixing media (a CD with a 7" bonus disc) stays in one folder.
The desktop app shows the matching Picard script lines. John's three Picard naming scripts
are presets (`all-music`, `various-artists`, `classical-performer`); naming now also knows
Picard's `$set`, `$rsearch`, `$sortmulti`, `$in`, `$add`, `$sub`, `$mul`, `$div`.
`$initials` now works as Picard's (letters only: "99 Luftballons" gives "L"), so titles
whose first word is a number get a new file name under the tagmender preset. Names
follow Picard's Windows-compatible rules exactly: a folder's trailing dot becomes "_"
("R.E.M_"), a leading dot becomes "_" ("_..And Justice for All"), a file name keeps its
dots; `$title`, `$num`, `$left`, `$right`, `$if`, comparisons and `$rsearch` behave as
Picard's.

**File operations reviewed** in seventeen independent rounds (tests/test_fileops*.py, and four
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
