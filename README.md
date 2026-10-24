# TagMender

Identifies the music in a large library with **MusicBrainz and Discogs as equal sources**,
shows exactly what it would change, then tags and organises it — unattended, undoable, and
without needing MusicBrainz Picard. Built for libraries laid out as
`<Album Artist>/<Year - Album>/`, with thousands of albums ripped or collected over years.

**Choose music → pick a profile → preview → apply → review what needs attention.**

```bash
tagmender setup                                   # Discogs token, AcoustID key (stored mode 600)
tagmender plan ~/Music/Inbox --profile Lossy      # identify and preview; changes nothing
tagmender run ~/Music/Inbox --profile Lossy       # preview, confirm, apply
tagmender undo 20261024-091200-ab12               # put a job back
tagmender gui                                     # the desktop app
tagmender watch                                   # keep watched folders processed
```

What it guarantees:

- **Nothing changes without a preview** (`plan`), and nothing is applied without `--yes`, a
  confirmation, or a profile you marked unattended.
- **Audio is never touched**: every write goes to a staged copy whose audio data is checked
  against the original before it replaces anything; an existing file is never overwritten.
- **Every job can be undone**, and an interrupted one is recovered (`tagmender recover`).
- **MusicBrainz or Discogs alone is enough**: a Discogs-only pressing is tagged as the Discogs
  Enrichment plugin would tag it, with no invented MusicBrainz IDs; the two are merged only
  when they describe the same pressing.
- **Rules are literal**: `[no vox]` means those characters; excluded albums are labelled
  Ignored or Skipped, never Unresolved.

Install: `sudo apt install ./tagmender_0.2.0_all.deb`, or run `TagMender-0.2.0-x86_64.AppImage`
(no installation; includes the desktop app). From a clone: `python3 tagmender.py ...`.

Documentation: [profiles and naming](docs/PROFILES.md) · [providers and keys](docs/PROVIDERS.md) ·
[formats](docs/FORMATS.md) · [architecture](docs/ARCHITECTURE.md) ·
[Picard coverage](docs/PICARD_COVERAGE.md) · [upgrading from 0.1](docs/MIGRATION.md) ·
[requirements checklist](docs/ASSESSMENT.md) · [release notes](docs/RELEASE_NOTES.md).

## The batch tools (0.1)

They still work exactly as before, and `tagmender <tool>` runs them.

## What it does

| Tool | Does | Writes to your music? |
| --- | --- | --- |
| `audit_untagged.py` | Lists every album folder and whether its files carry MusicBrainz and/or Discogs release IDs (untagged, partial, tagged). | No — writes a CSV |
| `dedupe_twins.py` | Finds duplicate copies of the same tracks in an album folder (same audio checksum, or same title and length), keeps the preferred one (FLAC over Opus, the tagged copy, the newer rip) and moves the others, with their `.log`/`.cue` from the same rip, to `<library>/2delete/`. | Moves files, only with `--apply`; `--undo` puts them back |
| `match_albums.py` | Identifies each untagged album: MusicBrainz first, then Discogs, scored on artist, title, every track length, disc and track counts, year, barcode and catalogue number (including ones read by OCR from scans in the folder, and ones written in folder names such as `[DOMO 71063-2]`). Each album is filed as **auto** (confident), **review** (a person should decide — with the reason) or **nomatch**. | No — writes CSVs and a state database |
| `match_loop.sh` | Keeps `match_albums.py` running over a whole library, and restarts it automatically when the matcher is updated, so a fix reaches every album already decided. | No |
| `tag_albums.py` | Tags the confidently matched albums **with Picard itself**: writes the matched release ID into the files, runs Picard in batches (load, wait until every lookup is done, save, quit), then checks every file. Tags, file names, folders and images are exactly what Picard writes with your settings, naming script and plugins. Scans Picard leaves behind or drops next to the music are moved into `covers/`. | Yes, through Picard, only with `--apply`; `--undo <run>` restores tags, covers, names, folders and scans |
| `web_tag.py` | The last resort for albums MusicBrainz and Discogs do not have: searches Deezer, Apple iTunes, Bandcamp (DuckDuckGo finds pages Bandcamp's own search misses) and Wikipedia; accepts a result only on the same proof as the matcher (artist, title, track count, track lengths; a different credited artist also needs the scans to name it, or a code, disc ID or fingerprint to agree), writes album, artists and contributing artists, titles, numbers, date, label, barcode and genre, with artist IDs from MusicBrainz (same-name artists told apart by their releases), Wikidata or Discogs, and files the album as your Picard naming script would. `TAGMENDER=Resolved` with the source, or `Unresolved` with the reason when nothing was found. | Writes tags and moves the album folder (and `cover.jpg` only into folders with no image), only with `--apply`; `--undo` restores; `--keep-folders` tags in place |
| `inbox.py` | Watches an inbox folder: each album that arrives (loose tracks are grouped first) is identified, tagged by Picard or from Discogs/the web, filed into the library, or set aside in `_unresolved/`. A systemd user service is in `contrib/`. | Yes: tags and files what arrives |
| `enrich.py` | ReplayGain (EBU R128), genres mapped to your own list, lyrics from LRCLIB (with `.lrc` files), detection of transcoded "lossless" and upsampled "hi-res" files, Navidrome rescan. | Writes tags, only with `--apply`; `--undo` restores |
| `learn_rules.py` | Learns how you file music from your tagged albums (Control Denied under Death) and writes `~/.config/tagmender/rules.json`, which the matcher uses. | No |
| `mb_seed.py` | One page with a pre-filled MusicBrainz "Add release" form for every album tagged from Discogs or the web. | No |
| `dupes_fp.py` | The same recording in different albums or libraries (MusicBrainz recording IDs, AcoustID with `--fingerprint`), with the copy to keep. | No — writes a CSV |
| `health_report.py` | One HTML page per run: % tagged, matcher decisions, why albums wait, tagging runs. | No |
| `coverup_batch.py` | Cover art in batch with CoverUp's own pipeline and your CoverUp settings: front, back and artist images, local hi-res scans first (an unfolded sleeve is cut to its front panel), online sources next, small images enlarged, and an AI-generated cover as a last resort for albums with no cover at all. | Writes image files and embeds the front, only with `--apply`; `--undo` restores |

## Matching rules worth knowing

- **Never a guess.** An album is only *auto* when artist, title, track count
  and track lengths agree and no other release is as good; otherwise it goes
  to *review* with the reason ("runner-up within 0.0 points — likely another
  pressing", "credit lacks Steven Brown", "only 83% of durations agree").
- **The exact pressing.** A barcode or catalogue number that agrees decides
  between pressings; a year written in the folder name (`[RM 1997]`) is
  honoured.
- **Collaborations stay complete.** A database credit that is missing one of
  the artists in your tags is never accepted automatically — tagging from it
  would drop that artist.
- **Folder names are understood**: rip notes (`[24bit 96khz]`, `[Vinyl Rip]`,
  `(Disc 1)`), release types (`Garbage EP` is listed as *Garbage*),
  catalogue numbers in any bracket (`{Japan BRC-657}`), and letters people
  type without accents (`ae3o` for `æ³o`).
- **Discogs data is read with the Discogs Enrichment plugin's own code**, so
  credits, disc counts and multi-artist handling are exactly what Picard
  would write.

## How an album is identified

Everything the folder holds is used as evidence, strongest first:

- **Disc ID**: the CD's table of contents, rebuilt from exact track lengths
  (CD rips), from the rip log (EAC, XLD) or from a cue sheet, looked up on
  MusicBrainz and in the CUETools database (which also links Discogs releases).
- **Barcode and catalogue number**: from the tags, the folder name
  (`[Century Media, 9976158, Germany]`), and the scans: barcodes decoded,
  text read by OCR, including the sideways text on tray-card spines, with the
  usual misreadings (`FOSO1` for `FO501`) allowed for.
- **Audio fingerprints** (AcoustID, as Picard's "Scan") for albums whose
  title or tags say little.
- **What the copy is**: scans of a back cover, tray card or disc mean a
  physical copy; hi-res audio or lossless tracks that are not whole CD
  sectors mean a download; `Vinyl Rip` means vinyl.
- **Label, country and date** from tags, folder name and scans ("Made in
  Germany", "licensed from ..." is ignored), and the track lengths and titles
  closest to one pressing.
- **Links and codes left in the tags**: Bandcamp, Apple/iTunes, Deezer,
  Spotify, Qobuz and Beatport pages and IDs, MusicBrainz and Discogs links, and
  ISRCs (also fetched from Deezer for the album), looked up on MusicBrainz.
- **File names** fill in track numbers and titles when the tags have none
  (`01 - Artist - Title`, vinyl `A1 Title`).
- **Rules learned from your library** (`learn_rules.py`).
- **Every version of the album** (MusicBrainz release group, Discogs master)
  is searched when no hit has your track count or catalogue number.
- Last, your **preferred countries** (Picard's list when set, else
  CA, US, worldwide, Europe, GB, DE).

Cyrillic, Greek and (with `pykakasi`) Japanese are compared in Latin letters.
Discs MusicBrainz has no disc ID for are listed in
`discid_submissions.csv` with a link to attach each one yourself.

## Ripping and naming habits that make every album match

- Keep the rip log (`.log`) and cue sheet with the music, also after converting
  to Opus or MP3: they identify the exact disc.
- Name folders `Year - Album [Label, Catalogue number, Country, Format]`,
  e.g. `2005 - Once Was Not [FONO, FO532CD, Russia, CD]`.
- Scan the back/tray card (both spines in view) and the disc; name them
  `back.jpg`, `tray.jpg`, `disc.jpg`.
- Keep the source's tags (label, catalogue number, barcode) when converting.

## Requirements

- Python 3.10+, and `pip install mutagen Pillow fonttools`
  (on Debian/Ubuntu/Mint: `sudo apt install python3-mutagen python3-pil python3-fonttools`)
- A Discogs personal access token (free, from your Discogs settings)
- A clone of [picard-discogs-enrichment](https://github.com/herculepirate/picard-discogs-enrichment)
  (default location `~/Documents/Github/picard-discogs-enrichment`, or pass `--plugin-dir`)
- For artwork: a clone of [picard-coverup](https://github.com/herculepirate/picard-coverup)
  (`--coverup-dir`); your CoverUp settings are read from Picard's `Picard.ini`
- Optional, for OCR of scans: `tesseract-ocr` and `libzbar0` (+ `pip install pyzbar`);
  `tesseract-ocr-rus tesseract-ocr-jpn` to read Russian and Japanese sleeves
- Optional, for audio fingerprints: `libchromaprint-tools` (fpcalc) and a free
  AcoustID application key (acoustid.org/new-application) in
  `~/.config/tagmender/acoustid_key`
- Optional, to compare Japanese titles: `pip install --user --break-system-packages pykakasi`
- Optional, to find transcoded files: `numpy` (`sudo apt install python3-numpy`)
- Optional, for generated covers: a Stable Diffusion server with an
  Automatic1111-compatible API (e.g. Forge), set in CoverUp's options

## Workflow

```bash
# 1. What is untagged?
python3 audit_untagged.py ~/Music/ambient --out ~/Downloads/audit_ambient.csv

# 2. Duplicate copies (dry run first; moves only with --apply)
TAGMENDER_MUSIC_ROOT=~/Music python3 dedupe_twins.py --root ~/Music/ambient
TAGMENDER_MUSIC_ROOT=~/Music python3 dedupe_twins.py --root ~/Music/ambient --apply

# 3. Identify every untagged album (runs for hours; safe to stop and resume)
read -rs -p "Discogs token: " DISCOGS_TOKEN; echo; export DISCOGS_TOKEN
AUDIT=~/Downloads/audit_ambient.csv nohup ./match_loop.sh > /dev/null 2>&1 & disown
tail -f ~/Downloads/matching/run.log          # progress
touch ~/Downloads/matching/STOP               # stop (within 30 s; finished albums are kept)

# 4. Cover art (plan first; --apply writes; --undo <batch> restores)
python3 coverup_batch.py --audit ~/Downloads/audit_ambient.csv --pass missing
python3 coverup_batch.py --audit ~/Downloads/audit_ambient.csv --pass full --apply
```

Results land in `~/Downloads/matching/matches_auto.csv`, `matches_review.csv`,
`matches_nomatch.csv` and `matches_moved.csv` (folders moved since the audit),
each with the matched release's link, the evidence and, for review, why it
was not automatic.

## Tagging

```bash
# Picard must be closed (its commands would otherwise go to your open Picard)
python3 tag_albums.py                          # dry run: ~/Downloads/tagging/tag_plan.csv
python3 tag_albums.py --apply --limit 2        # try two albums
python3 tag_albums.py --apply                  # everything matched with confidence
python3 tag_albums.py --undo <run-id>          # put a run back exactly as it was
```

Each run reports, per album: *tagged*, *tagged, no Discogs data*, *partly
saved* or *not saved*, and where Picard moved it. Tagged albums get
`TAGMENDER=Resolved`; albums left for a person can be marked
`TAGMENDER=Unresolved` with the reason in `TAGMENDER_REASON`:

```bash
python3 tag_albums.py --mark-unresolved            # dry run
python3 tag_albums.py --mark-unresolved --apply    # undo with --undo <run-id>
```

Add `tagmender, tagmender_reason` to Picard's Options → Tags → *Preserve
these tags*, or Picard removes them the next time it saves those files.

- Picard moves files to its own *Move files to* folder: point it at the
  library being tagged. `tag_albums` refuses albums Picard would move into
  another library.
- `[no vox]` (instrumental) editions are never retagged, and their front
  cover is never replaced.
- Use `--matches-db` to tag a library matched into its own folder, e.g.
  `--matches-db ~/Downloads/matching_extreme/match_state.db`. Picard picks the cover from
its own cover-art sources in their configured order: put **Local Files**
first (Options → Cover Art) to keep the `cover.jpg`/`folder.jpg` already in
an album folder.

## Hands-off

```bash
# keep unresolved albums looked at again, once a day, when last checked 30+ days ago
AUDIT=~/Downloads/audit_extreme_music.csv OUT=~/Downloads/matching_extreme RECHECK_DAYS=30 \
  nohup ./match_loop.sh > /dev/null 2>&1 & disown

# an inbox that tags and files whatever you drop into it
python3 inbox.py --inbox ~/Music/Inbox --library /media/unraidmedia2/music/extreme_music

# finishing touches, monthly re-learned rules, missing releases, duplicates, health
python3 enrich.py /media/unraidmedia2/music/extreme_music --replaygain --genres --lyrics --quality --apply --navidrome
python3 learn_rules.py ~/Downloads/audit_*_music.csv
python3 mb_seed.py /media/unraidmedia2/music/extreme_music
python3 dupes_fp.py /media/unraidmedia2/music/ambient_music /media/unraidmedia2/music/extreme_music
python3 health_report.py --library extreme ~/Downloads/audit_extreme_music.csv ~/Downloads/matching_extreme
```

Classical music: in Picard, tick Options → Metadata → *Use track relationships*
and *Use release relationships* (composer, work, conductor, performers), and
consider the *Classical Extras* plugin for work and movement tags.

Genres: `~/.config/tagmender/genres.json` maps what MusicBrainz and Discogs say
to your own list, e.g. `{"map": {"funeral doom metal": "Doom Metal"}, "allowed":
["Doom Metal", "Black Metal", "Ambient"], "max": 3}`. Navidrome:
`~/.config/tagmender/navidrome.json` with `url`, `user`, `password`.

## Albums no database has

```bash
python3 web_tag.py --matches-db ~/Downloads/matching_extreme/match_state.db                    # dry run
python3 web_tag.py --matches-db ~/Downloads/matching_extreme/match_state.db --apply
python3 web_tag.py --matches-db ~/Downloads/matching_extreme/match_state.db --include-review   # also review albums
python3 web_tag.py --undo web-<run-id>
```

### Is it right? Validate against albums you already tagged

```bash
python3 validate.py /media/unraidmedia2/music/ambient_music     # resumable; changes nothing
```

Every tagged album is identified again BLIND: the release IDs, ISRCs, label, catalogue
number, barcode, country and date in its tags are ignored (they came from the release).
Only names, track lengths, the audio, disc TOC, scans and the ORIGINAL folder name
(from the tagging journals) count. The pick is compared with the release in the tags:
agree / differs / unsure / no match. `~/Downloads/validate_ambient_music/validation.csv`
lists each album, with "differs" first.

To keep it checking while the matcher improves (albums that did not agree are checked
again whenever the matcher changes):

```bash
LIB=/media/unraidmedia2/music/extreme_music nohup ./validate_loop.sh > /dev/null 2>&1 & disown
touch ~/Downloads/validate_extreme_music/STOP      # to stop it
```

When the evidence about your copy (scans, folder name, audio, disc ID; never only the
country order) says an album is tagged as the wrong release, it can be re-tagged:

```bash
python3 validate.py /media/unraidmedia2/music/extreme_music --export-fixes   # writes fixes.db, lists them
python3 tag_albums.py --matches-db ~/Downloads/validate_extreme_music/fixes.db --retag          # preview
python3 tag_albums.py --matches-db ~/Downloads/validate_extreme_music/fixes.db --retag --apply  # undo: --undo RUN
```

The scans are read for every album that has them: a catalogue number, "Made in ...", or
the label's postal address on the back (D-54550 Daun, CA 90250, NG3 4GE) names the pressing.

### Discogs first

Both databases are searched for every album. Discogs decides the pressing: it lists far
more catalogue numbers and licensed editions. When MusicBrainz has the same pressing
(linked to that Discogs release, or the same barcode, or the same catalogue number and
country), `tag_albums.py` tags from it through Picard and Discogs Enrichment fills in.
A Discogs pressing MusicBrainz lacks is tagged by `web_tag.py`. MusicBrainz decides only
when Discogs is unsure. An enhanced CD's video and data entries never count as tracks.

### What the folder name says is a rule

A format (`[Vinyl Rip 24bit 192khz]`, `[2CD]`), country (`Germany`) or catalogue
number in the folder name is a statement about your copy. A release that contradicts
it is never picked automatically, however well its track lengths fit. When no release
of the album fits, the album goes to review. A folder catalogue number found on no
release at all is not held against them (it may not be a catalogue number).

`tag_albums.py` also leaves out matches made by an older matcher
(`--allow-old-matches` overrides) and re-checks each album just before Picard saves it.
To audit albums already tagged against their original folder names:

```bash
python3 check_tagged.py        # writes ~/Downloads/tagged_check.csv; changes nothing
```

## Ignore and skip rules

Rules keep TagMender away from files or whole albums: no matching, tags,
artwork, lyrics, renames or moves. Every tool checks them before any lookup,
and again once an album is identified (so a rule can match a title only the
database knows). Results, CSVs and the history name the rule.

- **Ignore**: left alone in every run while the rule applies. A watched
  folder does not queue it again until its files or the rules change.
- **Skip**: left alone in this run only.
- **Target**: the matching file, or its whole album. Albums are grouped by
  their tags, so in a mixed folder only that album is excluded.
- Matching is literal and ignores case: `[no vox]` means exactly those
  characters, brackets included. `glob` and `regex` only when chosen.
- Fields: album, title, artist, albumartist, genre, filename, folder, path,
  format, or any tag as `tag:NAME`. Operators: contains, equals, starts_with,
  ends_with, glob, regex. Combine with `--any` (OR; default AND), or nest with `--when JSON`.

A default rule ignores `[no vox]` albums (by album tag, folder name or path).

```bash
./tagmender.py rules list
./tagmender.py rules add --name "Live albums" --action skip --target album \
    --cond album contains "(live)" --cond folder contains "[live]" --any
./tagmender.py rules test /media/unraidmedia2/music/extreme_music      # preview; changes nothing
./tagmender.py rules test ~/Music/Inbox --rule live-albums -v           # one rule, even disabled
./tagmender.py rules disable no-vox        # enable / remove the same way
./tagmender.py rules allow "/path/to/Album"   # process anyway (undo: rules disallow)
./tagmender.py rules ignored               # remembered ignores
./tagmender.py rules history
./tagmender.py --profile Lossy rules add ...  # profile rules add to the global ones
```

Every tool also takes `--process-anyway` for one run. Rules live in
`~/.config/tagmender/exclusions.json` (profiles: `~/.config/tagmender/profiles/NAME.json`,
key `exclusions`); remembered ignores and history in `~/.local/state/tagmender/state.db`.
`./tagmender.py match|tag|web|enrich|inbox|coverup|...` runs the tools themselves.

## Safety

- Matching only reads your files.
- Every change to files can be undone: duplicate cleanup moves to
  `2delete/` with an undo log; artwork keeps backups and an undo per batch.
- A cover is never replaced by a smaller one, and a generated cover is
  marked as generated so a real one replaces it whenever one turns up.

## Tests

```bash
./run_tests.sh                     # every suite; needs ffmpeg and the two plugin clones
PYTHON=/path/to/python3 ./run_tests.sh
```

## Building the packages

```bash
packaging/build_deb.sh             # dist/tagmender_<version>_all.deb (+ .sha256)
packaging/build_appimage.sh        # dist/TagMender-<version>-x86_64.AppImage (needs uv)
```

## Licence

GPL-2.0-or-later, the same as the Picard plugins whose code it uses. See THIRD_PARTY_NOTICES.md.
The fonts in `coverup_fonts/` are under the SIL Open Font License; see
`coverup_fonts/licenses/`.
