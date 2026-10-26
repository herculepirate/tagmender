# Profiles, naming and examples

A profile lives in `~/.config/tagmender/profiles/NAME.json` and holds only what differs from the
defaults. `tagmender profiles show NAME --full` prints every setting.

```bash
tagmender profiles examples                      # installs Lossy
tagmender profiles new Inbox --from Lossy --set operation=move --set destination=/media/unraidmedia2/music
tagmender profiles set Inbox 'sources=[{"path": "/home/hp/Music/Inbox", "watch": true}]' unattended=true
tagmender profiles check
```

More examples are in `contrib/profiles/` (copy one into `~/.config/tagmender/profiles/`).

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `sources` | `[]` | `[{"path", "recursive", "watch"}]`; the deepest profile wins where roots overlap |
| `exclude_paths` | 2delete, _unresolved, .Trash | globs on the full path, never processed |
| `formats` | every format | e.g. `["mp3","opus","m4a"]`; others are Skipped, never converted |
| `providers` | MB, Discogs, web on; AcoustID off | `{"discogs": {"enabled": true, "identify": true}}` |
| `primary` | `discogs` | which provider's tags form the base when MusicBrainz and Discogs hold the *same* pressing (`discogs`, `musicbrainz`, `both`). Which pressing it is, is the matcher's call: evidence about your copy first (folder catalogue number, barcode, scans, disc ID), Discogs ahead of MusicBrainz, then your preferred-country order |
| `follow_picard` | `true` | apply Discogs data exactly as your Picard's Discogs Enrichment plugin does, with its settings read from Picard.ini (merge mode, style mode, marketplace numbers, performers, companies, identifiers, protected tags, notes, original date in Date, track tags, split collaborations), and keep the tags Picard's Options > Tags > "Preserve these tags" keeps (written only where the file has none). `false`: the plugin's defaults |
| `discogs_enrichment` | `{}` | overrides single plugin settings for this profile, e.g. `{"merge_mode": "fill_gaps", "style_mode": "genre"}` |
| `picard` | `{}` | overrides Picard's own settings for what is written from MusicBrainz, otherwise read from Picard.ini (`follow_picard`), else Picard 3's defaults: `standardize_artist_names` (`"none"` = as credited, `"variations"` = the artist's name unless the credit is a former name, `"all"`), `always_standardize_multivalue_artist`, `translate_artist_names`, `translation_locales` (e.g. `["en"]`), `translate_from_sortname`, `translate_album_titles`, `translate_track_titles`, `release_ars` (producer, engineer ... credited on the release), `track_ars` (composer, lyricist, performers and instruments, works), `standardize_instruments`, `standardize_vocals`, `va_name`, `convert_punctuation`, `preferred_release_countries`, `use_genres`, `folksonomy_tags`, `artists_genres`, `max_genres`, `min_genre_usage`, `genres_filter`, `join_genres`; what saving a file does beside its fields: `write_id3v23` (`true` ID3v2.3, `false` ID3v2.4, `null` as each file has it - the default without Picard.ini), `id3v23_join_with` (`"/"`), `write_id3v1` (`true` write, `false` remove, `null` update one that is there), `remove_ape_from_mp3`, `remove_id3_from_flac`, `fix_missing_seekpoints_flac`; file naming: `ascii_filenames`, `replace_spaces_with_underscores`, `replace_dir_separator` (`"_"`). E.g. `{"standardize_artist_names": "none", "track_ars": true}` |
| `field_sources` | `{}` | per field, providers in order; overrides the merge for that field |
| `thresholds` | identify 90 (margin 8), replace 90, organise 95 | matcher scores 0-100 (heuristic, not probability) |
| `tag_level` | `enthusiast` | `basic`: album artist, artist, album, title, track/disc numbers and totals, date, genre (never a comment). `enthusiast`: everything MusicBrainz and Discogs supply, as Picard + Discogs Enrichment write it. `audiophile`: enthusiast plus measurements (below). A lower level never removes tags a higher one wrote |
| `level_fields` | `{"add": [], "drop": []}` | `add`: more fields at Basic, e.g. `["label"]`; `drop`: never write these, at any level |
| `audiophile` | all on | `replaygain` (ReplayGain 2.0 + true peak, R128 for Opus), `lyrics` (LRCLIB; synced as `.lrc` next to the track, plain lyrics embedded; see `lyrics`), `acoustid` (AcoustID ID confirmed against the MusicBrainz recording; needs fpcalc and a key), `rip_info` (MusicBrainz disc ID from the rip log's TOC, and the log's verdict in `tagmender_rip`), `quality` (`dynamic_range`, `album_dynamic_range`, and `tagmender_quality` when a lossless file was made from a lossy one). Measurements never replace a value the file has, and are cached |
| `lyrics` | sidecar on, embed off | `sidecar`: time-synced lyrics as `.lrc` next to the track; `embed`: also in the file's lyrics tag (plain lyrics are always embedded, they have no file of their own); `enabled`: lyrics below Audiophile too |
| `field_policy` | replace | `{"default": "replace", "fields": {"album": "lock", "comment": "remove"}}` |
| `artwork` | CoverUp, embed front, cover.jpg only into folders without an image | `provider` (coverup, caa, discogs: tried first), `embed_front`, `files`, `min_px` (smallest short side taken), `max_bytes` (largest download). The embedded copy is at most CoverUp's `embed_max_side` (1200 px), always JPEG/PNG; cover.jpg is always a JPEG. `sources` (order of coverup, caa, discogs, web, local), `when` (needed / always), `enlarge`, `generate` (off / template / ai / auto). `replace`: `larger_only` (default) replaces an album's own front cover smaller than `min_px` - embedded, or the folder's cover/folder/front file - by a larger one found, else by its own enlarged; other pictures stay; the job keeps the old ones for undo. `never` leaves every picture that is there. `artist`: `{"enabled": true, "embed": true, "file": ""}` - the artist's photo into each track (Enthusiast and Audiophile); `"file": "artist.jpg"` also saves it in the artist folder (for Plex, Jellyfin, Navidrome, LMS), off by default |
| `operation` | `in_place` | `in_place`, `copy`, `move` |
| `destination` | — | must exist (never created); required for copy/move |
| `naming` | preset `tagmender` | or `{"template": "..."}`; presets `all-music`, `various-artists`, `classical-performer` are John's Picard scripts |
| `naming.editions` | `off` | "Separate editions in folder names": `edition` adds [Vinyl] / [WEB] / [Tape]; `edition_year` adds [2019 Vinyl] / [2014 CD] (an original CD, and unknown media, get nothing) |
| `naming.album_editions` | `off` | the same values, added to the Album tag ("I" becomes "I [2019 Vinyl]"); the folder then never adds it twice |
| `companions` | .cue .log .lrc .jpg .png .pdf .txt .m3u | go along with a moved/copied album from the folders holding its music; subfolders without music (Scans, covers) go entirely; per-track files follow their track's new name |
| `preserve_timestamps` | `null` | keep files' modification times; `null` follows Picard's "Preserve timestamps of tagged files" |
| `filenames` | windows-compatible, 255 bytes per name, 4096 per path, 5-word album folders | `abbreviations`: `{"Volume": "Vol."}` |
| `unresolved` | `mark` | `leave`, `mark` (TAGMENDER tags), `move` (to `move_to`) |
| `retry` | after 30 days, 6 attempts | watched folders |
| `unattended` | false | apply without asking (watcher) |
| `watch` | settle 10 min, every 300 s | `settle_minutes` (≥ 0): an album waits until nothing in it changed this long; `interval_seconds` (≥ 1): how often watched folders are looked at. `tagmender watch --add FOLDER --profile NAME` sets a watched source |
| `backup_retention_days` | 30 | `tagmender jobs prune` |
| `exclusions` | `[]` | this profile's Ignore/Skip rules, added to the global ones |

## Naming presets

| Preset | Gives |
|---|---|
| `tagmender` | `Robert Rich & Steve Roach/1992 - Soma The Twentieth Anniversary Edition/03.TTE.flac`, multi-disc `.../Disc 02/203.TTE.flac` (TagMender's existing scheme) |
| `picard` | Picard's default: `Album Artist/Album/03 Title.flac` |
| `artist-album-track` | `Album Artist/1992 - Album/1-03 - Title.flac` |
| `classical` | `Composer/1992 - Album/03 - Title.flac` |
| `singleton` | `Artist/Singles/Artist - Title.flac` |

## Template language

A subset of Picard's scripting, documented exactly (anything else is an error when the profile is
checked):

`%field%` (several values joined with "; "), `%_year%` (original year, else release year),
`%_albumartists_sorted%`, `%_multiartist%`; `$if`, `$if2`, `$not`, `$and`, `$or`, `$eq`, `$ne`,
`$gt`, `$lt`, `$gte`, `$lte` (numeric when both sides are numbers), `$num`, `$left`, `$right`,
`$lower`, `$upper`, `$title`, `$trim`, `$replace`, `$firstalphachar`, `$truncate`, `$year`,
`$join`, `$getmulti`, `$lenmulti`, `$noop`, and TagMender's `$words(text,n)` and `$initials(text)`.
`/` separates folders; a `/` inside a value becomes `_`. Not supported: `$set`, `$unset`,
`$copy`, `$rsearch`, `$rreplace`, plugin functions.
