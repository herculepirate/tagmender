<p align="center"><img src="../assets/icon/tagmender-animated.svg" width="160" height="160" alt="TagMender icon"></p>

# TagMender user guide

Identifies the music in a large library with **MusicBrainz and Discogs as equal sources**,
shows exactly what it would change, then tags and organises it — unattended, undoable, and
with no other tagger needed. Built for libraries laid out as
`<Album Artist>/<Year - Album>/`, with thousands of albums ripped or collected over years.

**Choose music → pick a profile → preview → apply → review what needs attention.**

```bash
tagmender setup                                   # your own keys: Discogs token, AcoustID ... (mode 600)
tagmender profiles examples                       # the Lossy example profile, then choose its library:
tagmender profiles set Lossy destination=~/Music/Lossy
tagmender plan ~/Music/Inbox --profile Lossy      # identify and preview; changes nothing
tagmender run ~/Music/Inbox --profile Lossy       # preview, confirm, apply
tagmender plan "~/Music/Band/2005 - Album" --release https://musicbrainz.org/release/<id>   # this release
tagmender plan ~/Music/Inbox --level audiophile   # this run's tagging level (basic, enthusiast, audiophile)
tagmender plan ~/Music/Inbox --source musicbrainz_preferred   # this run's Sources (see "Sources" below)
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

## What TagMender does

**Identifies every album, and says how sure it is.** Each album folder (or loose track) ends in
one of four tiers, shown in the plan and in the app's status column (details
[below](#when-the-release-is-not-proven-identification-tiers)):

| Tier | Mark in the files | What it gets |
|---|---|---|
| **Exact release** | `TAGMENDER=Resolved` | the pressing is proven: every field of your tagging level |
| **Album only** | `TAGMENDER=Partial` | the album is certain but its pressings tie: what they all share, no release IDs, catalogue number or barcode |
| **Partial** | `TAGMENDER=Partial` | no release picked, but the best candidate is the same album: what is true of every pressing, tagged in place only |
| **Unresolved** | `TAGMENDER=Unresolved` | nothing usable; the reason in `TAGMENDER_REASON`, tried again later |

**Free official APIs first.** MusicBrainz, Discogs, the Cover Art Archive, AcoustID, the CUETools
database, gnudb.org and the Deezer and iTunes barcode lookups are asked, with the evidence in the
folder: tags, file names, folder names, rip logs and cue sheets (disc IDs), barcodes and catalogue
numbers read from scans (OCR), links and codes left in tags, fingerprints. A general web search is
**off by default**; a profile can list Mojeek or Marginalia, tried in order, and
Tavily (a paid AI search) only when named and never alone. No search page is scraped.

**Uses the MusicBrainz tag mapping**, so files read the same in other taggers and players.
File names come from TagMender scripts in the tagging script language ([scripting guide](SCRIPTING.md)), Discogs data and artwork
from Discogs Enrichment and CoverUp's code, and settings imported from an existing tagger
installation are found automatically; every change is staged, verified, journaled and undoable.

**A Mac-style desktop app** (`tagmender gui`): a sidebar of pages (Overview, Needs attention,
History, Watched folders, Jobs), a toolbar, an inspector (tags original and new, artwork,
evidence) and Preferences (Ctrl+,: General, Profiles, Setup, Keys, Account, Network & Privacy,
File Safety, Advanced, Plugins). Its interactive tools:
search MusicBrainz and Discogs, other versions and a pressing chooser for albums whose pressing is
not proven, the script editor, tags from file names, match files manually, look up a CD, the
musicbrainz.org browser buttons, your MusicBrainz account (log in, collections, ratings), AcoustID
submission and a player. See [The desktop app](#the-desktop-app).

## Your own keys

Nothing is bundled: every user brings their own tokens, keys and logins, kept on their own
computer. A Discogs token and an AcoustID application key (with fpcalc) give the best results:
without the token Discogs is switched off, and says so. Everything else is optional.

| For | What | Where to get it |
|---|---|---|
| Discogs | personal access token | <https://www.discogs.com/settings/developers> (Generate new token) |
| AcoustID fingerprints | application key | <https://acoustid.org/new-application> |
| Sending fingerprints (Album > Submit AcoustIDs…) | your AcoustID **user** key (not the application key) | <https://acoustid.org/api-key> |
| MusicBrainz account (collections, ratings) | client ID and secret of your own application, type "Installed application", no callback address; then Log In | <https://musicbrainz.org/account/applications> |
| Covers and artist photos (CoverUp) | fanart.tv, TheAudioDB and Last.fm API keys | <https://fanart.tv/get-an-api-key/>, <https://www.theaudiodb.com>, <https://www.last.fm/api/account/create> |
| Disc lookups on gnudb.org | the e-mail address you registered there | <https://gnudb.org> |
| Web search (off by default) | a Mojeek, Marginalia or Tavily key | see [Setup](SETUP.md#web-search-optional-off-by-default) |

Enter them on the app's **Setup** page (it opens by itself on first run; also Help > Set Up
TagMender…), with `tagmender setup` (each key typed without echo; Enter keeps what is there;
`tagmender setup --stdin NAME` for scripts), or as environment variables (`DISCOGS_TOKEN`,
`TAGMENDER_ACOUSTID_KEY`, ...), which win over saved keys. Each is stored in
`~/.config/tagmender/`, mode 600 (a file others can read is refused), and never shown again,
printed, logged or exported. Wherever a feature cannot work because something is missing,
TagMender says which key, login or program, what it is for, and where to add it.

Step by step, with every notice and its fix: [Setup](SETUP.md). Each service's limits, caching
and what is sent: [Providers and keys](PROVIDERS.md).

## Install

Linux, x86_64. Download from [Releases](https://github.com/herculepirate/tagmender/releases):

| | How | Notes |
|---|---|---|
| **AppImage** (any distro) | `chmod +x TagMender-*-x86_64.AppImage` then run it | Nothing to install; includes Python and the desktop app (its player only in a `--with-player` build). Run with no arguments for the app, or with a command (`./TagMender-*.AppImage plan ~/Music/Inbox`). Ubuntu/Mint 22+ need `sudo apt install libfuse2t64` |
| **.deb** (Debian 12+, Ubuntu 24.04+, Mint 22+) | `sudo apt install ./tagmender_*_all.deb` | Uses the system Python; `tagmender` on the path, a menu entry, the background service. The desktop app needs PySide6, which these releases do not package: `pip install --user --break-system-packages PySide6-Essentials` |
| **From a clone** | `git clone https://github.com/herculepirate/tagmender` | `python3 tagmender.py ...`; see Requirements below |

Optional, for everything to work: `sudo apt install ffmpeg libchromaprint-tools tesseract-ocr`
(audio checks of unusual formats, AcoustID fingerprints, reading scans). The Discogs
and CoverUp plugins are built in (Preferences › Plugins). Then Preferences › Setup in the app (or `tagmender setup`) stores your own keys in `~/.config/tagmender/`, readable only by you:
see [Your own keys](#your-own-keys).

**First time? Read [First run](FIRST_RUN.md)**: preview first, try one folder, undo once.
Nothing is changed until you apply a preview, and every job can be undone.

Documentation: [setup](SETUP.md) · [profiles and naming](PROFILES.md) ·
[providers and keys](PROVIDERS.md) · [formats](FORMATS.md) · [architecture](ARCHITECTURE.md) ·
[features](FEATURES.md) · [scripting guide](SCRIPTING.md) · [upgrading from 0.1](MIGRATION.md) ·
[roadmap](ROADMAP.md) · [release notes](RELEASE_NOTES.md).

## The batch tools (0.1)

They still work exactly as before, and `tagmender <tool>` runs them. Older batch tools not
listed here have their own `-h`.

## What it does

| Tool | Does | Writes to your music? |
| --- | --- | --- |
| `audit_untagged.py` | Lists every album folder and whether its files carry MusicBrainz and/or Discogs release IDs (untagged, partial, tagged). | No — writes a CSV |
| `dedupe_twins.py` | Finds duplicate copies of the same tracks in an album folder (same audio checksum, or same title and length), keeps the preferred one (FLAC over Opus, the tagged copy, the newer rip) and moves the others, with their `.log`/`.cue` from the same rip, to `<library>/_Duplicates/` (the duplicates folder: Preferences > File Safety), or to the Trash when File Safety says so (or `--trash`). | Moves files, only with `--apply`; `--undo` puts them back |
| `match_albums.py` | Identifies each untagged album: MusicBrainz first, then Discogs, scored on artist, title, every track length, disc and track counts, year, barcode and catalogue number (including ones read by OCR from scans in the folder, and ones written in folder names such as `[DOMO 71063-2]`). Each album is filed as **auto** (confident), **review** (a person should decide — with the reason) or **nomatch**. | No — writes CSVs and a state database |
| `match_loop.sh` | Keeps `match_albums.py` running over a whole library, and restarts it automatically when the matcher is updated, so a fix reaches every album already decided. | No |
| `web_tag.py` | The last resort for albums MusicBrainz and Discogs do not have: searches Deezer, Apple iTunes, Bandcamp (its own search; no search engine's page is scraped) and Wikipedia; accepts a result only on the same proof as the matcher (artist, title, track count, track lengths; a different credited artist also needs the scans to name it, or a code, disc ID or fingerprint to agree), writes album, artists and contributing artists, titles, numbers, date, label, barcode and genre, with artist IDs from MusicBrainz (same-name artists told apart by their releases), Wikidata or Discogs, and files the album by TagMender's own scheme (the `tagmender` naming preset). `TAGMENDER=Resolved` with the source, or `Unresolved` with the reason when nothing was found. | Writes tags and moves the album folder (and `cover.jpg` only into folders with no image), only with `--apply`; `--undo` restores; `--keep-folders` tags in place |
| `inbox.py` | Watches an inbox folder: each album that arrives (loose tracks are grouped first) is identified, tagged from MusicBrainz, Discogs or the web, filed into the library, or set aside in `_unresolved/`. A systemd user service is in `contrib/`. | Yes: tags and files what arrives |
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
- **Discogs data is read with the built-in Discogs plugin**, so credits,
  disc counts and multi-artist handling are the same each time.

## How an album is identified

Everything the folder holds is used as evidence, strongest first:

- **Disc ID**: the CD's table of contents, rebuilt from exact track lengths
  (CD rips), from the rip log (EAC, XLD) or from a cue sheet, looked up on
  MusicBrainz and in the CUETools database (which also links Discogs releases).
- **Barcode and catalogue number**: from the tags (`BARCODE`, and `UPC`, `EAN`,
  `EAN/UPN` when their check digit is right, and the result says when the barcode
  came from one of these rather than `BARCODE`; an `ASIN`/`AMAZON_ID` tag is searched
  on MusicBrainz), the folder name
  (`[Century Media, 9976158, Germany]`), and the scans (read when the folder has
  scans or a PDF: profile setting `ocr: auto|on|off`, `--ocr` forces it): barcodes decoded,
  text read by OCR, including the sideways text on tray-card spines, with the
  usual misreadings (`FOSO1` for `FO501`) allowed for. A catalogue number is
  also searched with the label (from the tags, or a `Label:` line in the
  ripper's notes or any other document in the folder) on MusicBrainz and
  Discogs; what that finds is scored like any other candidate.
- **Audio fingerprints** (AcoustID) for albums whose
  title or tags say little. Profile setting `acoustid: unsure|always|off`
  (default `unsure`); `always` also fingerprints albums identified for sure and
  notes when AcoustID points to other recordings (`match_albums.py --acoustid`;
  a `--acoustid always` run also checks the albums earlier runs matched without
  it). Without fpcalc or an AcoustID key, `always` is said once and skipped.
- **gnudb.org (freedb)**: for an album still unsure with a table of contents
  (rip log, cue sheet or CD-rip FLAC lengths), what gnudb holds for the disc;
  its artist and title are searched on MusicBrainz and Discogs and the hits
  scored like any other, its track titles compared with the pick's in a note,
  never proof. gnudb answers registered applications only; until it lists this version,
  lookups are refused with one note. Needs the e-mail address you registered at gnudb.org
  (`tagmender setup`, `TAGMENDER_GNUDB_USER` or `~/.config/tagmender/gnudb_user`);
  without it gnudb is skipped with one note; an address gnudb refuses is
  noted as such, and gnudb is left alone for 30 minutes after 3 refusals. Off with `"web": {"gnudb": false}`
  or `match_albums.py --no-gnudb`.
- **What the copy is**: scans of a back cover, tray card or disc mean a
  physical copy; hi-res audio or lossless tracks that are not whole CD
  sectors mean a download; `Vinyl Rip` means vinyl.
- **Label, country and date** from tags, folder name and scans ("Made in
  Germany", "licensed from ..." is ignored), and the track lengths and titles
  closest to one pressing.
- **The disc itself**: the matrix / runout code round a disc's hub (read from a
  disc scan) compared with Discogs' Matrix / Runout, SID and label codes; a
  clue between pressings that otherwise tie, never proof on its own, and only
  when every tied pressing lists a code of that kind. A folder with no scan
  images has the front cover embedded in its files read instead (only with
  `ocr: on`, not auto), for a catalogue number or label to search; what it
  shows is noted, but never picks a pressing (it may be art fetched for
  another release).
- **Links and codes left in the tags**: Bandcamp, Apple/iTunes, Deezer,
  Spotify, Qobuz and Beatport pages and IDs, MusicBrainz and Discogs links, and
  ISRCs (also fetched from Deezer for the album), looked up on MusicBrainz.
- **Shops by barcode**: when the album's barcode (tags, scans, documents) finds
  nothing sure, Deezer and iTunes are asked what they sell under it; an album
  there with your track count and lengths under another title or artist is
  searched by that name on MusicBrainz and Discogs. The hit is a clue in the
  evidence ("Deezer: UPC ... = Artist - Title"), never barcode proof for a
  release that does not list the barcode. Off with `"web": {"shops": false}`
  in a profile, or `match_albums.py --no-shops`; a shop that cannot be reached
  is noted and skipped.
- **Other spellings of a code**: a catalogue number or barcode read on the
  scans that finds nothing is asked of MusicBrainz and Discogs again with its
  separators removed or added (`QDISS 0201 CDD`), O/0 and I/1 told apart, and
  through Discogs' free-text search, which reads notes and identifiers.
- **Web search** (optional, off by default; no AI search engine and no
  scraping): Mojeek or Marginalia, listed in a profile in
  the order to try them (`"web": {"search": {"engines": ["mojeek", "marginalia"]}}`;
  one that is not set up, fails, is at its cap or links no release hands over to
  the next), for at most 3 of an
  investigated album's strongest codes, never junk ones (`0000000000000`,
  fragments), with a monthly cap per engine (an engine that cannot be
  reached uses none of it). Tavily, a paid AI search, only
  when named, and never alone: Marginalia is added after it when no free
  engine is listed. Its key is entered with `tagmender setup` (or
  `--stdin mojeek`, `marginalia`, ...) or in the app's Preferences (Ctrl+,).
  See docs/PROVIDERS.md.
- **File names** fill in track numbers and titles when the tags have none
  (`01 - Artist - Title`, vinyl `A1 Title`).
- **Rules learned from your library** (`learn_rules.py`).
- **Every version of the album** (MusicBrainz release group, Discogs master)
  is searched when no hit has your track count or catalogue number (up to 25
  Discogs versions when the album has a barcode or catalogue number to compare,
  6 otherwise).
- Last, your **preferred countries** (from your tagger settings or the profile's
  `tagger` entries when set, else CA, US, worldwide, Europe, GB, DE). Then your
  **preferred release types** and **preferred medium formats** (none unless set), as the very last
  guess: they only choose between pressings known to be one album (one release
  group or Discogs master) that nothing above told apart; they never add to a
  score, so such a pick is a guess, never automatic, and the album is tagged as
  album only. A pressing of a discouraged type is
  never picked, and a format is compared only where it is certain (an SACD or a
  7" single is never taken for your preferred "CD" or "12\" Vinyl").

### When the release is not proven: identification tiers

Each album ends in one tier, shown in the plan (after the score) and in the app's status column:

1. **Exact release** (`TAGMENDER=Resolved`): the pressing is proven; every field of your level.
   Tracks missing from the folder do not stop it when the release is proven by more than its
   name (a disc ID or TOC from a rip log, cue sheet or CDTOC tag, the barcode, or the catalogue
   number with the matrix) and every file fits its own track of the release by length and
   title: each file gets its real track number and the release's totals, and
   `TAGMENDER_MISSING` lists the tracks that are not there ("2, 4, 5, 7-10, 14"; "1:3, 2:5"
   as disc:track on several discs).
2. **Album only** (`TAGMENDER=Partial`, `TAGMENDER_REASON` "ambiguous_release: album known,
   pressing not established ..."): the album is certain, its pressings tie; what they all share,
   no release IDs, catalogue number or barcode. Marked Partial, not Resolved, because no release
   is proven: `tagmender unresolved` lists it, `tagmender retry` and watched folders try it
   again. `web_tag.py --apply` marks its album-only albums the same way.
3. **Partial** (`TAGMENDER=Partial`, the reason in `TAGMENDER_REASON`): the matcher would not
   pick a release, but the best candidate is the same album - same track count, artist and title
   as close as an automatic pick needs, 80 % of the track lengths agreeing, and no other candidate
   as good that is not shown to be a pressing of it. When MusicBrainz or Discogs failed, the album
   is Unresolved and retried instead; files that already name a release only get what they lack. It gets what is true of every pressing: artists, album, titles, numbers,
   genre, the original date, recording-level credits and works, MusicBrainz artist, release-group
   and recording IDs, the Discogs master and artist IDs, the release group's cover. Never a release
   ID, barcode, catalogue number, label, country, media, the pressing's date, ASIN, script or
   status (the original date only where the files have no date); whatever the pressings (or
   MusicBrainz and Discogs) disagree on is left out. Tagged in place only, never moved or copied,
   and identified again later as Unresolved albums are (`tagmender unresolved` lists both,
   `tagmender retry` and watched folders try them again). Turn it off with
   `unresolved.allow_partial: false`; `unresolved.action: leave` writes nothing on these albums.
   **From the web**: when neither MusicBrainz nor Discogs establishes the album, Deezer,
   iTunes, Bandcamp and Wikipedia are asked (their public catalogues; no search page is
   scraped). A web album established against your files as a database's album would be (artist
   and title, and the tracks by their lengths, or by their titles in order) gives a Partial
   album too, `TAGMENDER_REASON` "web_fallback_only: not on MusicBrainz or Discogs: album tags
   from Deezer ..." and the page in `TAGMENDER_SOURCE`: album, album artist, artists and their
   MusicBrainz or Discogs artist IDs (only when known without doubt), titles, genre, the cover
   the web album shows (after your other cover sources), track and disc numbers when the web
   album's order is your files', and the date only when the source is sure it is the album's
   first release (an encyclopedia article, or a shop's date when its title names no version
   and the year agrees with your files' or folder's). Never a release ID (a shop's album ID is
   none), barcode, catalogue number, label, country or media. When two versions fit alike
   ("... (Remaster 2024)" and "... (Remix 2024)") the album's own title is written; another
   album as good writes nothing. Off with `"web": {"partial": false}` (Profiles: "Tag from
   shops' pages when no database has the album"; and with
   `unresolved.allow_partial: false` or `unresolved.action: leave`).
4. **Unresolved** (`TAGMENDER=Unresolved` + the reason): nothing usable.

**A set whose discs are different releases.** A disc set no release fits (disc 1 the 2024
remaster, disc 2 its remix, each sold on its own) is identified disc by disc: each disc folder
against one-disc releases, with the same proof as a whole album (a MusicBrainz or Discogs
release matched for sure, or a web album whose track lengths agree; a match by titles alone
does not tell versions apart). When every disc proves its own release, each disc folder is
planned as an album of its own, tagged from its own release: that release's title (the remix
keeps "(Remix 2024)"), and its own disc and track numbers (disc 1 of 1). The plan notes which
disc is which release. When one disc proves nothing, or two discs prove the same release, the
set stays as it was found. When the discs are versions of one album (one release group, one
Discogs master, or one title apart from the version), each disc is split into a folder of its
own, as below.

**Versions of one album kept apart.** A release that is one version of its album - its
MusicBrainz disambiguation names it ("remaster 2024", "remix 2024", "deluxe edition"), or its
Discogs format does ("Remix 2024"; Remastered, Mono, Deluxe Edition for a release tagged
from Discogs alone) - keeps that version in
the album title when the release group (Discogs master) holds another version: a remaster and
a remix both listed as "Nothing but Death Remains" are tagged "Nothing but Death Remains
(Remaster 2024)" and "... (Remix 2024)", with the version in `ALBUMVERSION` (and
MusicBrainz's disambiguation in `RELEASECOMMENT`), so media servers show two albums and a move
files them into two folders. Your files' own spelling of the version is kept, and a track title
your files mark with it ("Tales... (Remaster 2024)") keeps the mark where the release's title
lacks it. A release group of one version, or a remaster beside only plain releases, changes
nothing, and a pressing's words ("Japanese edition", "SHM-CD") stay out of the title. At the
Basic tag level only the album title carries the version. Off with the profile's
`"keep_versions": false` (Profiles: "Keep a release's version in the album title"); two versions
split from one folder still keep their marks, so their folders differ.

**Two versions mixed in one folder.** A disc folder holding the same titles twice
("101.T.flac" and "101.T (1).flac", their audio different) is never tagged as one release.
TagMender splits it into two albums instead, in the plan:

- Each file is given to a version by its length against each version's release (Song Of
  Sirens 2:33 on the remaster, 2:46 on the remix). A copy's " (1)", the version marks in the
  titles and the album tags only make the first guess; the lengths decide.
- Each version must be proven as an album of its own (a MusicBrainz or Discogs release matched
  for sure, or a web album whose lengths agree), the two must be different releases with
  different titles, and every file must fit one version clearly better than the other. When
  the files are not matched that way (a folder named for 2024, the 1994 CD fitting as well), the
  releases of the album's MusicBrainz release group that name a version ("remaster 2024",
  "remix 2024") are tried against the lengths; a release holding more tracks (a deluxe set with
  both) is not one version. Tracks whose two versions are within a second of each other follow
  the tracks the lengths did tell apart, as the file names pair them (" (1)"), or as the discs
  hold them.
- Each version is tagged from its own release ("Purgatory Afterglow (Remaster 2024)", disc 1 of
  1, its own track numbers) and gets a folder of its own, named by your naming as for any album:
  a move or copy profile files it into the destination; in place (or a Partial version, which is
  never filed) it goes into a new folder beside the one it was in.
- The files go with their version: a disc folder's rip log, cue sheet and scans with that disc's
  version, a track's own `.lrc` with its track, a cue sheet or log that names only one version's
  files with that version. What both share (the set's cover.jpg, Scans/) is copied to each.
  Cue sheets and playlists are rewritten to the new file names. The emptied folder is removed;
  one that still holds something stays.
- The plan's preview lists every move, copy and tag change, and says why ("2 versions of this
  album were in one folder: ..."). Nothing moves until you apply the plan (`run --yes` applies it
  like any other); every step is staged, verified and journaled, `tagmender undo` puts every file
  back under its old name and tags, and `tagmender recover` finishes or reverses a split that was
  interrupted. The versions are split together or not at all, and never into a folder that is
  already there (that is a conflict to look at).

When the lengths do not tell the files apart, a version proves no release, or both halves fit
the same release, nothing is split: the folder is Unresolved, "two versions of the album are
mixed in this folder (N titles twice): split them into separate folders (not split for you:
...)", and the plan's notes say which files fit which version where the web lists both. The
profile's `"split_versions": false` (Profiles: "Split two versions found in one folder") always
leaves such folders for you. A track saved twice (the same audio) is one copy, as before; the
same titles on discs tagged 1 and 2 are a set's. A folder whose titles are twice only as a
track and its version ("Shine" and "Shine (Remix)") is first looked up as one album, on the
web too: an EP with its remixes is tagged as that EP; only when nothing fits is it called two
versions mixed.

A single file identified by its fingerprint is a recording; it also gets the album, date, genre
and release-group ID when the recording is on exactly one MusicBrainz release group, and the
standalone recordings name (`[standalone recordings]`, or `nat_name` from your tagger settings) as its album
when it is on no release at all and the file has no album of its own (an album it has is kept). Album-only and Partial albums in a folder marked restored or
remastered (`[Restored]`, `[RM]`, "Remaster", "RMX") get no ISRCs - restored audio is another
master - but keep their recording IDs. Details:
[docs/PROFILES.md](PROFILES.md#identification-tiers).

Cyrillic, Greek and (with `pykakasi`) Japanese are compared in Latin letters.
Discs MusicBrainz has no disc ID for are listed in
`discid_submissions.csv` with a link to attach each one yourself.

## Covers and misc folders

A profile can keep each album folder tidy (Profiles: "Folders inside the album folder": the pictures
folder and the rip logs folder; profile `subfolders`, both off by default):

```
Album/cover.jpg                    stays: the front cover, what players show
Album/back.jpg, Album/booklet.pdf  -> Album/Artwork/
Album/Scans/Booklet/01.jpg         -> Album/Artwork/Booklet/01.jpg
Album/Album.log, Album/info.txt    -> Album/Extras/
Album/Album.cue, Album/Album.m3u   stay beside the audio (they name the files by their path)
Album/Album.md5, Album/Album.ffp   stay too (checksum lists name the files the same way)
Album/album.nfo, Album/artist.nfo  stay (media servers read them from the album folder)
Album/Video/clip.mkv, clip.txt     stay (a folder with more than pictures and papers in it)
Album/CD1/back.jpg                 -> Album/Artwork/CD1/back.jpg    ("One for the album")
                                   -> Album/CD1/Artwork/back.jpg    ("One in each disc")
```

A profile from an earlier version keeps the names it had, `covers` and `misc`, one in each disc's
folder.

Every picture format goes into `Artwork` (JPEG, PNG, GIF, BMP, TIFF, WebP, HEIC, AVIF), and so
do PDF booklets; `Extras` takes rip logs, text and info files (`.log`, `.txt`, `.nfo`, `.info`,
`.toc`, `.accurip` ...). A front cover named otherwise ("Front Cover.jpg", "Band - Album -
Front.jpg") stays beside the music when the folder has no cover.jpg or folder.jpg; so does the
folder's only picture, unless its name says it is the back, inlay, tray or CD. Both folder
names can be changed. Only an identified album is tidied: a folder nothing identified is left
as it is. An album tagged where it is gets them in its own folder; a moved or copied album in its
new folder (a copy never changes the source). The plan lists each file "into the covers folder"
or "into the misc folder"; nothing moves until you apply it, and undo puts every file back. A
name already taken in the folder gets a suffix; planning again plans nothing new. TagMender still
reads the scans, logs and notes there when it identifies the album again. A version split (two
versions in one folder) gives each version its own.

## The report: what a run did and what is left for you

After a preview or a job, the report lists every album in three sections:

| Section | What is in it |
|---|---|
| Needs your decision | Albums whose release is not proven (Partial, Album only), two versions mixed in one folder, a split not applied yet (check which version is which), a release that lists no track lengths ("the release lists no track lengths, so the files can't be proven to match") |
| Could not finish | Albums not identified, blocked by a conflict (a folder already there), or that failed or were stopped while applying |
| Done (Ready to apply, for a preview) | Tags written, files moved or copied, pictures into `covers`, versions split into folders of their own, missing tracks ("8 tracks of 32 missing: tagged the 24 present") |

Each row has the album's folder as a link (it opens in your file manager), the issue in plain
words, and the releases that fit best, each with its MusicBrainz or Discogs page. In the app,
Apply This Release… previews the album again with that release on the Overview: nothing is
written until you press Apply there, which asks first. The report also comes as a web page or a
spreadsheet (Export as Web Page…, Export as CSV…).

- After Apply: the app opens the report of what was just done; `tagmender run` and `tagmender
  apply` end with the report's counts and the `tagmender report JOB` command that shows it whole.
- App: Edit > Show Report… (Ctrl+Shift+B), for the job selected under Jobs (or its Show Report… button), the
  Overview's preview, or the albums listed under Needs attention (with the releases their last
  plan suggested).
- Terminal: `tagmender report` (the newest job), `tagmender report JOB` or `tagmender report
  PLAN` (a plan saved with `plan -o`, before applying). `--html PATH` and `--csv PATH` save it
  too; `--json` prints it as JSON. Each suggested release comes with its command,
  `tagmender plan FOLDER --release REF` (with `--profile NAME`, the job's profile), which
  previews; one version of a folder holding two is chosen in the app. Exit code 0 when nothing is left for you, 4 when an album needs your
  decision or could not finish, 1 when the job or plan cannot be read.

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
- Your own keys (a Discogs token and an AcoustID application key give the best results): see [Setup](SETUP.md)
- Discogs and artwork: the built-in Discogs and CoverUp plugins (included; Preferences › Plugins
  switches them off and on). When settings imported from an existing tagger installation are found
  (automatically; `TAGMENDER_TAGGER_INI` names another file), your CoverUp settings come from
  there, and so do the Cover Art Archive options (image types and types to leave out, size,
  approved only, release / release group order) and the cover file name
  (`cover_image_filename`, `cover.jpg` by default) for the `caa` source and the saved cover, and
  the ID3 text encoding (`id3v2_encoding`) used when MP3/WAV/AIFF files are written
- Optional, for OCR of scans: `tesseract-ocr` and `libzbar0` (+ `pip install pyzbar`;
  without them scans are simply not read, and `ocr: auto` notes it and goes on);
  `tesseract-ocr-rus tesseract-ocr-jpn` to read Russian and Japanese sleeves
- Optional, for audio fingerprints: `libchromaprint-tools` (fpcalc) and a free
  AcoustID application key (acoustid.org/new-application) in
  `~/.config/tagmender/acoustid_key`
- Optional, to compare Japanese titles: `pip install --user --break-system-packages pykakasi`
- Optional, to find transcoded files quicker: `numpy` (`sudo apt install python3-numpy`);
  without it the check (Audiophile `quality`, `enrich.py --quality`) uses a slower
  pure-Python FFT on fewer frames, and without ffmpeg it is skipped and noted
- For the desktop app: PySide6 (`pip install --user PySide6-Essentials`; add
  `--break-system-packages` on Debian/Ubuntu/Mint). Its player also needs Qt Multimedia, which pip
  ships in `PySide6-Addons` (same version; Debian 13+: `python3-pyside6.qtmultimedia`); without it
  the player's controls say why there is no sound and the rest of the app works
- Optional, to read a CD in the drive (File > Look Up CD…): python-discid (`pip install --user discid`,
  with `--break-system-packages` on Debian/Ubuntu/Mint; the packages include it) and `sudo apt install libdiscid0`; without it a rip log or a pasted table
  of contents still works
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

## The desktop app

`tagmender gui` (or `python3 -m gui`) opens the same engine in a window: a sidebar of pages
(Overview, Needs attention, History, Watched folders, Jobs), the page, and an
inspector on the right (Ctrl+Alt+I). Drop a music folder on the window or choose one (Ctrl+O),
pick a profile, Preview (Ctrl+P), then Apply (Ctrl+Return); Edit > Undo Last Job (Ctrl+Alt+Z) puts
it back. Every command is in the menu bar, and Ctrl+/ lists the shortcuts.

With two or more albums listed (several folders dropped at once, or a folder holding several
albums), each row has a **Profile** drop-down: *As Above* uses the profile chosen in the
toolbar, or choose another for that album (several selected: right-click › Profile). Look Up and
Preview plan each album with its own profile, and Apply runs one job per profile (each undone
on its own from Jobs). Changing an album's profile after it was looked up looks it up again.
Look Up while another look-up runs queues the albums; each row shows its result as soon as
that album is matched (*Waiting…* and *Looking up…* on the others), and Apply shows the album
and file it is writing.

Its interactive tools each change only the preview; nothing reaches a music
file until you apply it, through the same staged, verified and undoable path as everything else:

| Where | What it does |
|---|---|
| Album > Search for Release… (Ctrl+Shift+F) | Search MusicBrainz releases, recordings and artists, or Discogs releases; Use This Release previews the album again with it |
| Album > Other Versions… (Ctrl+Shift+L) | Every release of the album's MusicBrainz release group and Discogs master, with how well each fits your files |
| Album > Choose Your Pressing… (Ctrl+Shift+P) | For an album whose pressing is not proven (Album only, Partial): only the pressings that fit equally, with the details that tell them apart highlighted and what to check on your copy |
| Album > Info… (Ctrl+I) | A file's format, codec, bitrate, length, every tag as stored and its embedded pictures (read only) |
| Inspector > Tags (Album > Tag) | The selected album's tags, original and new; edit a value (double-click, Return or F2), revert it, add or remove a tag (Delete), for one file or all of them; Edit > Undo Edit (Ctrl+Z) takes back the last edit |
| Inspector > Artwork | The cover now and the new one; drop an image file or link to use it instead |
| Tools > Script Editor… (Ctrl+Shift+E) | The profile's file naming script and tagger scripts, with highlighting, completion, function help, errors marked where they are and a preview on the selected album; the language is in the [Scripting guide](SCRIPTING.md) |
| Tools > Tags from File Names… (Ctrl+Shift+G) | A pattern such as `%artist% - %album%/%tracknumber% %title%` read from the selected album's file names, with a preview; the tags become your edits |
| Edit > Job Details… (Ctrl+Shift+J) | What a job did, file by file and tag by tag (on the Jobs page), with Undo |
| Edit > Show Report… (Ctrl+Shift+B) | The report of the selected job, the preview or the albums needing attention: what needs your decision, what could not finish, what was done, with folder links and Apply This Release (see [The report](#the-report-what-a-run-did-and-what-is-left-for-you)) |
| Window > Log (Ctrl+Shift+Y) | TagMender's log: by level, searched as you type, following new lines |
| Album > Match Files Manually… (Ctrl+Shift+M) | The album's files beside the release's tracks: drag a file onto the track it is (or select both and press Match); Use These Matches puts the pairs into the preview |
| File > Look Up CD… (Ctrl+K) | Find the release of a CD in the drive, a rip log or a pasted table of contents; the sheet says what is sent before Look Up sends it |
| Album > Collections… | Your MusicBrainz collections, ticked where they hold the album's release; each change is listed before it is sent (needs your login) |
| Album > Submit Ratings… | Send the stars you gave in the inspector's Rating row to MusicBrainz (needs your login); Apply also writes them into the files (not MP4 or APEv2, which hold none) |
| Help > Set Up TagMender… | Preferences › Setup: every key, login and optional program, what each enables, where to get it and whether it is set; enter keys there (it opens by itself on first run) |
| Album > Submit AcoustIDs… | Send the album's fingerprints to AcoustID with your user key, after a sheet lists every track |
| Controls > Play/Pause (Space) | Play the selected album in the player bar under the pages; Next, Previous, Stop. It lets go of the files before Apply or Undo |
| Profiles > Settings | Every setting of the profile in sections (Source, Identification, Tags, Artwork, Target, Albums nothing identified, Automation); see [Profile settings in the app](#profile-settings-in-the-app) |
| Preferences > General | Appearance, language, reduce motion; where folder choosers open (Folder choosers open in); how far Skip Forward and Back go; keep watching while open; notifications (only while TagMender is in the background) |
| Preferences > Network & Privacy | Test Connection (MusicBrainz, Discogs, AcoustID, the Cover Art Archive); the proxy (the `http_proxy` and `https_proxy` environment variables); how long saved lookups are kept before an album processed again asks again; the languages and effort for reading scans |
| Preferences > File Safety | How long Undo works (backups kept 1 week, 30 or 90 days, or for ever), cleaning old backups after each apply (a sheet names the jobs that would lose Undo first), the folder duplicate copies go into |
| Preferences > Plugins | The built-in Discogs and CoverUp plugins and any you install, each with its switch (this computer's setting, never a profile's) |
| Preferences > Advanced | TagMender's checks and the diagnostics export (folder and file names hidden unless you untick it) |
| Preferences > Account | Your own MusicBrainz application (client ID and secret), Log In / Log Out, your collections; your AcoustID user key; browser integration (musicbrainz.org's tagger buttons, off by default, 127.0.0.1 only) and its port; the CD drive |

The table's context menu (right-click, or the Menu key) and the inspector's Change Release
button (its arrow) offer the same album commands. Your edits are kept when the folder is
previewed again, and are shown as yours in the Tags tab. Drop folders or audio files anywhere on
the window: the first one the preview does not hold yet is previewed, and the status bar says
what became of the others.

Nothing is bundled for the account features: you bring your own MusicBrainz application and
AcoustID user key (Preferences › Setup; [Setup](SETUP.md#your-musicbrainz-account) has the steps), kept 0600 in
`~/.config/tagmender` and never shown again. Anything sent to MusicBrainz or AcoustID is your
choice for one album, after a sheet that says exactly what will be sent.

### Profile settings in the app

Preferences › Profiles shows the chosen profile's settings as grouped lists, in the order the work
happens: **Source** (the folders it covers, folders to skip, which audio files, a separate folder
for lossy files), **Identification** (the preferred database, which albums are tagged, the lookups
used as evidence, preferred releases), **Tags**, **Artwork**, **Target** (what happens to files,
the library folder, names with a live example of the folder and file name, pictures and logs
folders, files that go with the album, a file that is already there), **Albums nothing
identified** and **Automation**. Each row has its label and control, and a line under it saying
what it does; rarer settings sit under each section's **Advanced**. A few rows merge several saved
settings into one choice (Sources, including Web only; Find lyrics; What happens to files, including
renaming in place; Artist photos). A saved combination such a row cannot say exactly shows as "As
saved" and stays as it is until you choose something else. A row that does nothing in the current
state (the Basic level, a database that is not asked, covers turned off, a copy) is greyed with the
reason. Whether albums are filed without asking, how long to wait and how often to look are set on
the Watched folders page.

- A dot beside a row means it differs from the defaults. **Restore Defaults…** (all settings) and
  **Restore Section Defaults…** list each change (old → default) before anything changes.
- **Start From…** offers three presets: **Typical Library** (the defaults), **Collector** (Discogs
  first, every detail and code, artist photos, editions in folder names, asks before a second
  copy) and **Keep My Files as They Are** (fixed in place, only missing tags filled in, covers and
  comments kept). Folders, the description and what happens to files stay as they are. New
  Profile… starts from one of them; Duplicate Profile… copies the profile shown (without its
  folders, which belong to one profile).
- A problem shows in red under its row as you edit (a copy with no library folder, a skip pattern
  that would skip everything); Save Profile stays off and says how many there are.
- A row that needs a key or program you have not set up is greyed out and says why, with a link
  to Setup.
- The toolbar's search field shows only the settings whose words hold what you type.
- Only what you changed is written to the profile; a value set by hand in its file stays.

The matching thresholds are not in the app: they can only be raised, with **Extra-careful
matching** (Identification > Advanced).

On the first start after an upgrade, a sheet says that the built-in settings (used when no
profile is chosen) have new defaults, and offers to save the old ones as the profile "Built-in
(previous)". It is asked once.

### Upgrading from an earlier version

Your saved profiles keep their behaviour; new installs get these defaults. A profile file written
by an earlier version reads every setting it left unset as the earlier default, so the same
albums get the same tags and names after the upgrade; `tagmender profiles migrate` writes those
values into the file and keeps the old one beside it. The defaults a new profile and a new
install get:

- MusicBrainz preferred, Discogs filling the gaps; your other tagger's settings not followed
  unless you turn that on; AcoustID fingerprints used when an album is unsure (needs fpcalc and
  a key); web Partial tags off.
- Names "Artist/1991 - Album/01 - Title" ("1-01 - Title" in a set), the full album title, and
  a second file of one name gets " (2)".
- One Artwork and one Extras folder per album when those folders are turned on; no artist
  photo; your own covers never replaced.
- Lyrics written into the file as well as the .lrc; MusicBrainz genres (at most 5), Discogs
  styles in Genre only when nothing else gives one.
- Only the NAS recycle bins are skipped (not every folder starting with "_"); duplicates go to
  "_Duplicates".
- Undo works for 30 days, and older backups are cleaned after each apply; saved lookups are asked
  again after 30 days, when their album is processed again.

An earlier install keeps its own machine settings (duplicates in "2delete", newest copy kept,
folders starting with "_" skipped, no automatic backup cleaning, lookups kept for ever, Skip by 5
seconds, scans read in English, Russian and Japanese, a 100 MB log, names shown in diagnostics);
Preferences changes them. The built-in
settings (no profile chosen) are the one exception, see the sheet above. The Lossy example
profile comes without a library folder: choose one (the profile's **Library folder**, or
`tagmender profiles set Lossy destination=...`) before using it.

## Hands-off

### Watched folders

Drop an album folder, or loose tracks, into a watched folder: once nothing in it has changed
for a while (10 minutes by default; downloads in progress are waited for), the folder's
profile identifies it, tags it and files it - moved or copied into that profile's library,
or tagged where it is. Each watched folder belongs to one profile, so an inbox for vinyl
rips can file into one library and a downloads folder into another.

```bash
tagmender watch --add ~/Music/Inbox --profile Lossy --auto   # file without asking
tagmender watch --add ~/Downloads/Rips --profile Vinyl       # plans wait for approval
tagmender watch --list
tagmender watch --pending                                    # plans waiting for approval
tagmender watch --approve all                                # (or a job id; --dismiss sets one aside)
tagmender watch                                              # watch, in this terminal
tagmender watch --service install                            # watch in the background, also at login
```

The desktop app has the same on its **Watched folders** page: the folders and what happens
to what is dropped there, *Keep watching while TagMender is open*, *Run in the background*,
the plans waiting for approval (Show plan / Apply / Apply all / Dismiss) and what was done
recently. What it filed is remembered, so a library inside the watched folder is not taken
for a new drop. An album nothing identifies stays where it was dropped (or goes to the
profile's unresolved folder) and is looked at again after `retry.after_days`.

Folder access: the .deb, the AppImage and a clone run as you, and so does the background
service (a systemd user service, not sandboxed): they read and write every folder you can,
libraries on `/media`, `/mnt`, `/run/media`, `/srv` or a mounted share included. The Flatpak
asks for the same (`--filesystem=host`), keeps its settings in `~/.config/tagmender` like the
others, and watches in the background through the desktop's Background portal (it starts at
login; a Flatpak cannot install a systemd service).

### Other unattended tools

```bash
# keep unresolved albums looked at again, once a day, when last checked 30+ days ago
AUDIT=~/Downloads/audit_music.csv OUT=~/Downloads/matching_music RECHECK_DAYS=30 \
  nohup ./match_loop.sh > /dev/null 2>&1 & disown

# an inbox that tags and files whatever you drop into it
python3 inbox.py --inbox ~/Music/Inbox --library /mnt/music

# finishing touches, monthly re-learned rules, missing releases, duplicates, health
python3 enrich.py /mnt/music --replaygain --genres --lyrics --quality --apply --navidrome
python3 learn_rules.py ~/Downloads/audit_*.csv
python3 mb_seed.py /mnt/music
python3 dupes_fp.py /srv/music /mnt/music
python3 health_report.py --library main ~/Downloads/audit_music.csv ~/Downloads/matching_music
```

Classical music: set `"tagger": {"track_ars": true, "release_ars": true}` in the profile
(composer, work, conductor, performers); see [docs/PROFILES.md](PROFILES.md).

Genres: `~/.config/tagmender/genres.json` maps what MusicBrainz and Discogs say
to your own list, e.g. `{"map": {"funeral doom metal": "Doom Metal"}, "allowed":
["Doom Metal", "Black Metal", "Ambient"], "max": 3}`. Navidrome:
`~/.config/tagmender/navidrome.json` with `url`, `user`, `password`.

## Albums no database has

```bash
python3 web_tag.py --matches-db ~/Downloads/matching_music/match_state.db                    # dry run
python3 web_tag.py --matches-db ~/Downloads/matching_music/match_state.db --apply
python3 web_tag.py --matches-db ~/Downloads/matching_music/match_state.db --include-review   # also review albums
python3 web_tag.py --undo web-<run-id>
```

### Is it right? Validate against albums you already tagged

```bash
python3 validate.py /srv/music     # resumable; changes nothing
```

Every tagged album is identified again BLIND: the release IDs, ISRCs, label, catalogue
number, barcode, country and date in its tags are ignored (they came from the release).
Only names, track lengths, the audio, disc TOC, scans and the ORIGINAL folder name
(from the tagging journals) count. The pick is compared with the release in the tags:
agree / differs / unsure / no match. `~/Downloads/validate_music/validation.csv`
lists each album, with "differs" first.

To keep it checking while the matcher improves (albums that did not agree are checked
again whenever the matcher changes):

```bash
LIB=/mnt/music nohup ./validate_loop.sh > /dev/null 2>&1 & disown
touch ~/Downloads/validate_music/STOP      # to stop it
```

When the evidence about your copy (scans, folder name, audio, disc ID; never only the
country order) says an album is tagged as the wrong release, it can be re-tagged:

```bash
python3 validate.py /mnt/music --export-fixes   # writes fixes.db, lists them
tagmender plan "/path/to/Album" --release https://musicbrainz.org/release/<id>  # preview one fix
```

The scans are read for every album that has them: a catalogue number, "Made in ...", or
the label's postal address on the back (D-54550 Daun, CA 90250, NG3 4GE) names the pressing.

#### How scans are read

Each scan has a kind, told by its name or its folder's: back, tray (inlay), disc (CD, matrix),
obi, spine, label (sticker, barcode), booklet, front, other. The kinds whose scans most often
carry a code that the identified release confirms (a barcode, catalogue number or matrix) are
read first; within a kind, single panels come before booklet spreads. The page text is read in
your languages (Preferences > Network & Privacy); the closer readings that hunt for codes are
read in English, since codes are Latin letters and digits. Several scans are read at once.
When a barcode decoded off the scans already finds the release for sure, only the first three
scans' text is read. An album still unsure is investigated: the first six scans again in full,
their codes searched, and the rest only if the album is still unsure. A catalogue number that
looks like a misreading (fewer than 5 letters and digits, or no digit: "ZB 44") is not searched;
numbers from tags, rip logs and file names always are.

The order starts from counts shipped with TagMender and learns from your albums: after each
album identified for sure, it counts, per kind, the scans read and those whose code the
release confirmed. Only those counts are kept (`ocr_order.json` in the state folder), never a
file name, folder or album. `tagmender ocr stats` shows them; `tagmender ocr stats --export
FILE` saves them to share: attach it to an issue on TagMender's GitHub page, and the shipped
order improves for everyone. Nothing is sent automatically.

### Sources: which database decides

A profile's **Sources** setting (`source`; Preferences › Profiles in the app, "Sources"; `--source` on
`plan` and `run` for one run) says which database identifies the release and whose tags win:

| Sources | Identifies | Tags written | IDs already in your files |
|---|---|---|---|
| **MusicBrainz only** (`musicbrainz_only`) | MusicBrainz; Discogs is not asked | MusicBrainz's only | every `DISCOGS_*` tag is removed |
| **MusicBrainz preferred** (`musicbrainz_preferred`, the default) | MusicBrainz picks the release when it has your pressing; Discogs when MusicBrainz cannot | MusicBrainz's, Discogs filling the gaps | a Discogs release ID that differs is replaced by the Discogs entry of the chosen pressing, else removed with that release's other Discogs fields |
| **Discogs preferred** (`discogs_preferred`; profiles from earlier versions keep it) | Discogs picks the release; MusicBrainz when Discogs cannot | MusicBrainz's base with Discogs's values winning | a MusicBrainz release ID that differs is replaced by the MusicBrainz release of the chosen pressing, else removed with its track IDs when MusicBrainz was sure of another release (its release-group, recording and disc IDs stay) |
| **Discogs only** (`discogs_only`) | Discogs; MusicBrainz is not asked | Discogs's only, no MusicBrainz IDs | every MusicBrainz ID is removed, except the disc ID (your CD's own) |

Release IDs already in your files are evidence: the releases they name are looked up and
scored with the others. They are never a veto. When the other database's IDs disagree with
the release TagMender found, that is no conflict and does not send the album to review.
When both databases are sure of different pressings, the preference only breaks a tie. A
pick with evidence about your copy (its catalogue number, barcode, disc ID) that the other
lacks wins; when each pick has evidence of its own, the album goes to review; when your files
are already tagged with one of the two, that one is kept. Only then does the preferred
database's pick stand. When both list your pressing, they are merged, and the preferred
database's values win where they differ. A preferred mode removes nothing when the other
database was not asked, failed for the album (an outage, no Discogs token) or was not sure of
another release, since that says nothing about your pressing. The disc ID is never removed.

Every replaced or removed ID is shown in the preview. The plan's notes name it ("replaces your
MusicBrainz release ID ...", "removes your Discogs release ID ..."), and undo puts it back.
Replacing or removing a tag needs the replace score (`thresholds.replace_min_confidence`), as
any other change does. The Basic level writes and removes no IDs.

When MusicBrainz has the same pressing (linked to that Discogs release, or the same barcode,
or the same catalogue number and country), it is tagged from MusicBrainz and Discogs fills in.
A Discogs pressing MusicBrainz lacks is tagged by `web_tag.py`. An enhanced CD's video and
data entries never count as tracks.

Older profiles keep working. `primary` is read as `source`: `discogs` as Discogs preferred,
`musicbrainz` as MusicBrainz preferred. `both` is read as Discogs preferred, which is how
those profiles already worked, since a sure Discogs pick decided. A Discogs Enrichment merge
mode of `mb_only` or `discogs_only` in the profile is read as MusicBrainz preferred or Discogs
preferred: both databases still identify, as before, and the merge mode still keeps the other
database's tags out.

### What the folder name says is a rule

A format (`[Vinyl Rip 24bit 192khz]`, `[2CD]`), country (`Germany`) or catalogue
number in the folder name is a statement about your copy. A release that contradicts
it is never picked automatically, however well its track lengths fit. When no release
of the album fits, the album goes to review. A folder catalogue number found on no
release at all is not held against them (it may not be a catalogue number).

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
./tagmender.py rules test /mnt/music      # preview; changes nothing
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
`./tagmender.py match|web|enrich|inbox|coverup|...` runs the tools themselves.

## Safety

- Matching only reads your files.
- Every change to files can be undone: duplicate cleanup moves to
  the duplicates folder (`_Duplicates` by default, Preferences > File Safety) with an undo log; artwork keeps backups and an undo per batch.
- A cover is never replaced by a smaller one, and a generated cover is
  marked as generated so a real one replaces it whenever one turns up.

## Tests

```bash
./run_tests.sh                     # every suite; needs ffmpeg
PYTHON=/path/to/python3 ./run_tests.sh
```

## Building the packages

```bash
packaging/build_deb.sh             # dist/tagmender_<version>_all.deb (+ .sha256)
packaging/build_appimage.sh        # dist/TagMender-<version>-x86_64.AppImage (needs uv and the network)
packaging/build_appimage.sh --with-player   # the same, with Qt Multimedia for the player (larger)
flatpak-builder --user --install --force-clean build-dir \
    packaging/flatpak/io.github.herculepirate.TagMender.yml     # needs org.kde.Sdk//6.10, io.qt.PySide.BaseApp//6.10
```

Each carries the Python files (the `gui/` package and `tagscript/` included), the fonts, the
Lucide icons with their licence and the translations in `assets/i18n/`; not `tests/` or the developer
tools in `tools/`. The Flatpak builds the committed `main` branch of the checkout (tracked files only), so
commit first. Reading a CD in the drive: the .deb carries python-discid and recommends
`libdiscid0`; the AppImage carries python-discid and the build machine's `libdiscid.so.0`
(install `libdiscid0` before building); the Flatpak builds libdiscid and reaches the drive
with `--device=all`.

## Licence

GPL-2.0-or-later, including its built-in Discogs and CoverUp plugins. See
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
The fonts in `coverup_fonts/` are under the SIL Open Font License; see
`coverup_fonts/licenses/`.
