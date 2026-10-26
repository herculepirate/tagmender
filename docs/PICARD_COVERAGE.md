# Picard feature coverage

Compared against MusicBrainz Picard 3.0.2 (`picard/options.py`, `picard/mbjson.py`,
`picard/album.py`, `picard/script/functions.py`, the option pages in `picard/ui/options/`).

**Full** = equivalent, tested. **Partial** = some of it. **Missing** = not done yet.
**Excluded** = deliberately not done. **Plugin** = provided by a Picard plugin TagMender runs.
Picard's default is given where it matters, because TagMender aims to write what a default
Picard (plus Discogs Enrichment) would. Picard's own settings are read from Picard.ini (a
Picard 2 Picard.ini means Picard 2's defaults); a profile's `"picard"` entries override them.
`tests/test_picard_parity.py` checks the MusicBrainz mapping and the file formats against
output recorded from Picard's own code.

## Finding the release

| Picard | Default | TagMender | Notes |
|---|---|---|---|
| Lookup by tags, file names, folder | - | Full | plus catalogue numbers, barcodes, OCR of scans, rip logs, cue sheets, links in tags |
| Guess track number and title from file names | on | Full | in the matcher |
| Scan (AcoustID fingerprints) | - | Full | needs fpcalc and a key; off in the default profile |
| Disc ID lookup | - | Full | from EAC/XLD logs, cue sheets and track lengths; no CD drive reading (Excluded) |
| Preferred release countries | - | Full | Picard's own list is read; also picks the RELEASECOUNTRY written |
| Preferred release formats (weights) | - | Partial | formats come from folder names and the files, not Picard's weights |
| Preferred release types (weights) | - | Missing | Picard ranks album / single / EP / live / compilation ... by sliders |
| Match thresholds | - | Full | TagMender's own (`thresholds` in profiles) |
| Choose another release ("Other versions", paste an MBID or URL) | - | Full | `plan|run ALBUM --release LINK`; in the app, "Use another release…" |
| Standalone recordings (`nat_name`) | `[standalone recordings]` | Missing | non-album tracks are left unresolved |
| Interactive cluster / drag-and-drop matching | - | Excluded | plans are previewed and approved instead |

## What is written

| Picard | Default | TagMender | Notes |
|---|---|---|---|
| Album, title, track/disc numbers and totals, dates, IDs, label, catalogue no., barcode, ASIN, media, disc subtitle, ISRC, script, language, release type/status/country | - | Full | `mb_tags.py`, field names and values as `mbjson.py` |
| Credited artist names | - | Full | |
| Standardize artist names (none / variations only / variations and name changes) | variations only | Full | Picard's own setting; checked against Picard's code on its test data |
| Always standardize multi-valued artist tags (ARTISTS) | off | Full | |
| Sort name from the alias matching the credit | - | Full | |
| Translate artist names (locales, from sort name) | off | Full | script exceptions are not read (Picard keeps them in a form TagMender cannot read) |
| Translate album / track titles | off | Full | |
| Various Artists name (`va_name`) | Various Artists | Full | album artist and track artist |
| `compilation=1` on Various Artists releases | - | Full | an existing compilation flag on other albums is left as it is (Picard removes it) |
| Convert Unicode punctuation to ASCII | off | Full | Picard's own code (`picard_text.py`) |
| Release relationships (release-level producer, engineer, mixer ..., license, ASIN) | on | Full | |
| Track relationships (composer, lyricist, writer, arranger, conductor, performer:instrument, work, work ID, work languages) | off | Full | |
| Standardized instrument / vocal credits | on | Full | |
| Genres from MusicBrainz (min usage, max, filter, album artist genres, folksonomy tags, join) | off | Full | Picard's own filter code (`picard_util.py`); "only my genres" needs a MusicBrainz login and writes none |
| Ratings (POPM / RATING) | off | Missing | |
| Discogs Enrichment | - | Plugin | its own code and settings, read from Picard.ini |
| ReplayGain | plugin | Full | Audiophile level, EBU R128 |
| Lyrics | plugin | Full | Audiophile level, LRCLIB, synced `.lrc` and embedded plain |
| Classical Extras plugin | - | Missing | |

## Tag options

| Picard | Default | TagMender | Notes |
|---|---|---|---|
| Tag mapping per format | - | Full | ID3, Vorbis, MP4, APEv2, ASF: checked against files Picard's own code writes (credits in TMCL/TIPL and PERFORMER, MVIN, WCOP, WOAR, cpil ...) |
| Preserve these tags | - | Full | read from Picard.ini |
| Clear existing tags | off | Excluded | per-field policies instead (preserve, fill, replace, remove, lock) |
| ID3v2 version | 2.4 | Partial | an existing v2.3 tag stays valid v2.3 (dates in TYER/TDAT, credits in IPLS, as Picard converts them), new tags are v2.4; no setting |
| ID3v2.3 join character | `/` | Partial | fixed `/` |
| Write ID3v1 | on | Partial | an existing ID3v1 is kept, none is added |
| Remove APEv2 from MP3/AAC/AC3, ID3 from FLAC | off | Missing | |
| Fix missing FLAC seek points | off | Missing | |
| WAV RIFF INFO | off | Missing | |
| Preserve timestamps | off | Full | follows Picard's setting unless the profile says |

## Cover art

| Picard | Default | TagMender | Notes |
|---|---|---|---|
| Providers (CAA, local files, URL relationships) | CAA | Full | CoverUp (plugin) first, then CAA and Discogs; order per profile |
| CAA image size, approved only, restrict types | - | Partial | fixed front-1200 |
| Embed / save as files | - | Full | |
| Embed only one front | on | Full | |
| Overwrite existing images | off | Full | `replace: larger_only` replaces only small covers, backed up |
| Resize / convert / enlarge for tags and files | off | Partial | embed limit 1200 px, enlarge small; no format conversion setting |
| Keep embedded images | off | Full | |
| Remove images from tags | off | Missing | |
| Artist photo | - | Extra | not in Picard |

## Files and naming

| Picard | Default | TagMender | Notes |
|---|---|---|---|
| Rename and move files | - | Full | journaled and undoable |
| Move additional files | off | Full | cue/playlist references updated |
| Delete empty directories | on | Full | |
| Windows compatibility | on | Full | plus byte, UTF-16 and path limits, shortening |
| Replace non-ASCII characters | off | Missing | |
| Overwrite existing files | off | Excluded | never overwrites |
| Naming script language | - | Partial | 39 of Picard's 82 functions, incl. `$set`/`$unset`; missing: `$cleanmulti $copy $copymerge $countryname $dateformat $datetime $day $delete $delprefix $endswith $eq_all $eq_any $find $firstwords $foreach $get_new $get_original $inmulti $is_audio $is_complete $is_multi $is_video $len $map $matchedtracks $max $min $mod $month $ne_all $ne_any $pad $performer $replacemulti $reverse $reversemulti $setmulti $slice $startswith $strip $substr $swapprefix $unique $while` |
| Tagger scripts (change tags with a script) | - | Missing | |
| Plugins in general | - | Excluded | only Discogs Enrichment and CoverUp |

## MusicBrainz account features

| Picard | TagMender | Notes |
|---|---|---|
| Submit AcoustID fingerprints, ISRCs, ratings | Excluded | TagMender never writes to MusicBrainz or AcoustID |
| User collections, ListenBrainz, browser integration, player, CD ripping | Excluded | outside a tagger that works unattended |
