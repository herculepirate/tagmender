# TagMender

Finds the albums in a large music library that were never tagged properly,
identifies each one on MusicBrainz and Discogs, cleans up duplicate copies,
and fills in cover art — in batch, unattended, and in step with how
[MusicBrainz Picard](https://picard.musicbrainz.org/) tags with the
[Discogs Enrichment](https://github.com/herculepirate/picard-discogs-enrichment)
and [CoverUp](https://github.com/herculepirate/picard-coverup) plugins.

It is built for libraries laid out as `<Album Artist>/<Year - Album>/`, with
thousands of albums ripped or collected over years: some tagged, some with
only folder names, some with scans of the sleeve and nothing else.

## What it does

| Tool | Does | Writes to your music? |
| --- | --- | --- |
| `audit_untagged.py` | Lists every album folder and whether its files carry MusicBrainz and/or Discogs release IDs (untagged, partial, tagged). | No — writes a CSV |
| `dedupe_twins.py` | Finds duplicate copies of the same tracks in an album folder (same audio checksum, or same title and length), keeps the preferred one (FLAC over Opus, the tagged copy, the newer rip) and moves the others, with their `.log`/`.cue` from the same rip, to `<library>/2delete/`. | Moves files, only with `--apply`; `--undo` puts them back |
| `match_albums.py` | Identifies each untagged album: MusicBrainz first, then Discogs, scored on artist, title, every track length, disc and track counts, year, barcode and catalogue number (including ones read by OCR from scans in the folder, and ones written in folder names such as `[DOMO 71063-2]`). Each album is filed as **auto** (confident), **review** (a person should decide — with the reason) or **nomatch**. | No — writes CSVs and a state database |
| `match_loop.sh` | Keeps `match_albums.py` running over a whole library, and restarts it automatically when the matcher is updated, so a fix reaches every album already decided. | No |
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

## Requirements

- Python 3.10+, and `pip install mutagen Pillow fonttools`
  (on Debian/Ubuntu/Mint: `sudo apt install python3-mutagen python3-pil python3-fonttools`)
- A Discogs personal access token (free, from your Discogs settings)
- A clone of [picard-discogs-enrichment](https://github.com/herculepirate/picard-discogs-enrichment)
  (default location `~/Documents/Github/picard-discogs-enrichment`, or pass `--plugin-dir`)
- For artwork: a clone of [picard-coverup](https://github.com/herculepirate/picard-coverup)
  (`--coverup-dir`); your CoverUp settings are read from Picard's `Picard.ini`
- Optional, for OCR of scans: `tesseract-ocr` and `libzbar0` (+ `pip install pyzbar`)
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

Writing the tags themselves is done in Picard, from the matched release, so
the result is Picard's own. Batch tag writing from the *auto* list is planned.

## Safety

- Matching only reads your files.
- Every change to files can be undone: duplicate cleanup moves to
  `2delete/` with an undo log; artwork keeps backups and an undo per batch.
- A cover is never replaced by a smaller one, and a generated cover is
  marked as generated so a real one replaces it whenever one turns up.

## Tests

```bash
python3 tests/test_match.py ~/Documents/Github/picard-discogs-enrichment
python3 tests/test_dedupe.py
python3 tests/test_coverup_batch.py ~/Documents/Github/picard-coverup
```

## Licence

GPL-2.0-or-later, the same as the Picard plugins whose code it uses.
The fonts in `coverup_fonts/` are under the SIL Open Font License; see
`coverup_fonts/licenses/`.
