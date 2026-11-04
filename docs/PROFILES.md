# Profiles, naming and examples

A profile lives in `~/.config/tagmender/profiles/NAME.json` and holds only what differs from the
defaults. `tagmender profiles show NAME --full` prints every setting.

```bash
tagmender profiles examples                      # installs Lossy, with no library folder yet
tagmender profiles set Lossy destination=~/Music/Lossy   # yours to choose; check says until then
tagmender profiles new Inbox --from Lossy --set operation=move --set destination=/mnt/music
tagmender profiles set Inbox 'sources=[{"path": "~/Music/Inbox", "watch": true}]' unattended=true
tagmender profiles check
```

More examples are in `contrib/profiles/` (copy one into `~/.config/tagmender/profiles/`).

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `sources` | `[]` | `[{"path", "recursive", "watch", "destination"}]`; the deepest profile wins where roots overlap. A source's own `destination` (absolute; empty = the profile's) is where a copy or move files the albums found in that folder, so one profile can send `/inbox/ambient` to `/music/ambient` and `/inbox/extreme` to `/music/extreme`. An all-lossy album still goes to `destinations.lossy` when one is set |
| `exclude_paths` | _unresolved, .Trash | globs on the full path, never processed; a pattern matching the profile's own source or destination, or everything (`*`), is refused |
| `skip_folders` | .Trash\*, .recycle, #recycle, @Recycle, @eaDir, $RECYCLE.BIN, lost+found | folder names (patterns allowed, e.g. `_*`) never looked into; hidden folders, TagMender's own unidentified folder and the duplicates folder are always skipped; `*` is refused |
| `grouping` | `{"join_deluxe_folders": true}` | a sibling "(Deluxe Edition)" folder holding only the bonus tracks is read with its album |
| `destinations` | `{"lossy": "", "mounted_only": "auto"}` | `lossy`: an identified album whose files are all lossy (MP3, Opus, Ogg, AAC) is copied or moved into this library instead of `destination`; a mixed album goes to `destination`. `mounted_only`: `auto` - nothing is written into a destination below a mount point `/etc/fstab` names that is not mounted now, or under /media, /mnt, /run/media, /srv or /net with nothing mounted there (a library kept on the system disk under /srv needs a mount of its own there, or a destination elsewhere); `always` - also any other destination, which must then be on a mount of its own (never the system disk) |
| `quality_action` | `note` | a lossless album that looks converted from lossy (Audiophile's quality check): `note` (a tag and a note), `hold` (held for review), `move` (set aside untagged into `unresolved.move_to`, which it then needs) |
| `singles` | `{"identify": true}` | a loose track is matched to its recording by its fingerprint; `false` leaves loose tracks alone |
| `formats` | every format | e.g. `["mp3","opus","m4a"]`, or a kind decided by the codec: `["lossless"]` (ALAC in an .m4a too) or `["lossy"]` (AAC, never ALAC); others are Skipped, never converted |
| `formats_by_codec` | on (off for a profile from an earlier version) | `"alac"` or `"aac"` in `formats` also takes an .m4a or .mp4 holding that codec; off, only files with that extension |
| `providers` | MB, Discogs, web, AcoustID on (fingerprints are sent to AcoustID; needs fpcalc and a key); web Partial off | `{"discogs": {"enabled": true, "identify": true}}`; `{"web": {"shops": false}}` stops asking Deezer and iTunes what they sell under the album's barcode (on with web); `{"web": {"gnudb": false}}` stops asking gnudb.org (freedb) what it holds for the disc's table of contents (on with web; needs a registered e-mail, see PROVIDERS.md); `{"web": {"partial": true}}` tags albums MusicBrainz and Discogs do not prove from Deezer, iTunes, Bandcamp or Wikipedia (off by default: a Partial album with every tag that names no release, never release IDs, barcode, catalogue number, label, country or media; see the user guide's identification tiers); `{"web": {"search": {"engines": ["mojeek", "marginalia"], "monthly_cap": 1000}}}` turns on a general web search for the codes on the scans of an album being investigated, the engines tried in order: `[]` (the default: none), `mojeek` (`mojeek_key`), `marginalia` (`marginalia_key`, else the shared public key) or `tavily` (a paid AI search, only when named), each set up with `tagmender setup` (or `--stdin <engine>`) or the app's Preferences (Ctrl+,). An engine not set up, failing, at its cap or answering without a release link hands over to the next. Tavily is never the only engine: a list with `tavily` and neither `mojeek` nor `marginalia` gets `marginalia` added at its end. The older `"engine": "mojeek"` (or `"search": "mojeek"`) is a one-item list. `monthly_cap` is the searches a month on each engine: `null` (each one's default), a number (every engine listed; an added Marginalia keeps its 500) or `{"tavily": 100}` (the others keep their default); only searches that reach an engine count; at its cap it hands over, with one note. See PROVIDERS.md |
| `acoustid` | `unsure` | AcoustID fingerprints in identification (needs fpcalc and a key): `unsure` only albums not identified for sure; `always` every album, and a sure album picked from MusicBrainz whose fingerprints point to other recordings than the pick's gets a note ("AcoustID disagrees: ...") in the evidence, the pick unchanged; `off` never (also no fingerprint for a lone track). Writing each track's AcoustID at Audiophile is `audiophile.acoustid` |
| `ocr` | `auto` | OCR of scans (barcodes, catalogue numbers, labels, the matrix codes round a disc's hub): `auto` only for album folders with scan images (not a plain `cover.jpg` or a shop picture) or a PDF booklet; `on` every album, and one with no scans has the front cover embedded in its files read (codes to search and a note, never picking a pressing; a barcode only when decoded whole); `off` never. `--ocr` and the app's "Read scans" turn it on for that run. When tesseract, Pillow or pyzbar is missing, OCR is off for the run and `plan`/`run` say why (`note: ocr: ...`; the app shows it with the plan), so nothing empty is remembered for the scans |
| `source` | `musicbrainz_preferred` | which database identifies the release and whose tags win ("Sources" in the app; `--source` on `plan` and `run` for one run): `musicbrainz_only` (Discogs is not asked; MusicBrainz tags only, merge `mb_only`; the files' `DISCOGS_*` tags are removed), `musicbrainz_preferred` (MusicBrainz picks the release when it has your pressing, Discogs identifies when it cannot and fills the gaps, merge `fill_gaps`), `discogs_preferred` (Discogs picks the release, MusicBrainz identifies when it cannot, Discogs's values win, merge `override`), `discogs_only` (MusicBrainz is not asked; no MusicBrainz IDs are written and the files' own are removed, merge `discogs_only`). Release IDs already in the files are evidence for finding the release, never a conflict: in a preferred mode the other database's release ID, when it differs, is replaced by that database's entry of the chosen pressing, else removed with its track IDs (not when that database was not asked, failed or was not sure of another release; the release-group, recording and disc IDs stay). The preview names each one ("replaces your MusicBrainz release ID ..."), and undo restores it. When both databases are sure of different pressings, the preference only breaks a tie: evidence about your copy on one pick only decides for it, evidence on both goes to review, and a release your files are already tagged with is kept. Which pressing it is, is otherwise the matcher's call: evidence about your copy first (folder catalogue number, barcode, scans, disc ID), then your preferred-country order, then, as a last guess between pressings of one album (never automatic), your preferred release types and formats. The older `primary` is still read: `discogs` and `both` as `discogs_preferred` (`both` never chose a database of its own), `musicbrainz` as `musicbrainz_preferred`; a profile merge mode of `mb_only` or `discogs_only` as `musicbrainz_preferred` or `discogs_preferred`, the merge mode kept (both databases still identify) |
| `follow_tagger_settings` | `false` | apply Discogs data exactly as your Discogs Enrichment settings say, from settings imported from an existing tagger installation (style mode, marketplace numbers, performers, companies, identifiers, protected tags, notes, original date in Date, track tags, split collaborations), and keep the tags your tagger settings list as "Preserve these tags" (written only where the file has none). Also taken from your tagger settings (no profile entry for them): the standalone recordings name (`nat_name`), the ID3 text encoding (`id3v2_encoding`: `utf-8`; `utf-16`; `iso-8859-1`, where characters Latin-1 lacks are simplified, "ł" to "l" - UTF-8 is written as UTF-16 in an ID3v2.3 tag), the Cover Art Archive options (see `artwork`) and the preferred releases (countries, release types, formats: the matcher's last tie-break, see `tagger`). The settings are found automatically; `TAGMENDER_TAGGER_INI` names another file. `false`: the plugin's defaults, and TagMender's own for those |
| `discogs_enrichment` | `{}` | overrides single plugin settings for this profile, e.g. `{"style_mode": "genre"}`. The merge mode follows `source`; in a preferred mode `"merge_mode"` here (`fill_gaps`, `override`, `combine`) fine-tunes it, e.g. `combine` keeps both databases' values in multi-value fields |
| `tagger` | `{}` | overrides your tagger settings for what is written from MusicBrainz (otherwise taken from them with `follow_tagger_settings`, else the standard defaults): `standardize_artist_names` (`"none"` = as credited, `"variations"` = the artist's name unless the credit is a former name, `"all"`), `always_standardize_multivalue_artist`, `translate_artist_names`, `translation_locales` (e.g. `["en"]`), `translate_from_sortname`, `translate_album_titles`, `translate_track_titles`, `release_ars` (producer, engineer ... credited on the release), `track_ars` (composer, lyricist, performers and instruments, works), `standardize_instruments`, `standardize_vocals`, `va_name`, `convert_punctuation`, `preferred_release_countries` (also the matcher's country order, as the release types and formats below; taken from your tagger settings only with `follow_tagger_settings` on; none set, or `[]`: the profile's `matching.preferred_countries`), `use_genres`, `folksonomy_tags`, `artists_genres`, `max_genres`, `min_genre_usage`, `genres_filter`, `join_genres`; what saving a file does beside its fields: `write_id3v23` (`true` ID3v2.3, `false` ID3v2.4, `null` as each file has it - the default without tagger settings), `id3v23_join_with` (`"/"`), `write_id3v1` (`true` write, `false` remove, `null` update one that is there), `remove_ape_from_mp3`, `remove_id3_from_flac`, `fix_missing_seekpoints_flac` (the ID3 text encoding, `id3v2_encoding`, comes from your tagger settings only, see `follow_tagger_settings`); file naming: `ascii_filenames`, `replace_spaces_with_underscores`, `replace_dir_separator` (`"_"`). identification: `preferred_release_types` (e.g. `["Album", "EP"]`, first preferred most), `discouraged_release_types`, `preferred_release_formats` (MusicBrainz's medium format names, e.g. `["CD", "Digital Media"]`) - applied by the matcher as only the last tie-break between pressings of one album (one release group or Discogs master), after the country order, never a score, so such a pick is never automatic; a discouraged type is never picked, and `CD` or `Vinyl` never stands for an SACD or a sized vinyl (`12" Vinyl` is compared only between MusicBrainz releases that name their formats). E.g. `{"standardize_artist_names": "none", "track_ars": true}` |
| `field_sources` | `{}` | per field, providers in order; overrides the merge for that field |
| `thresholds` | identify 90 (margin 8), replace 90, organise 95 | matcher scores 0-100 (heuristic, not probability): `auto_min_confidence` and `auto_min_margin` (an album is identified for sure), `replace_min_confidence` (tags the files have are replaced), `organize_min_confidence` (files are renamed and moved). These are the floor: a profile may raise them, never lower them (a lower value is refused) |
| `matching` | countries `auto`, no type or format preference, extra-careful off | `preferred_countries`: `auto` (your tagger's list when followed, else `auto_countries`: `region` - your region from the system language, then Worldwide, then Europe - or a list), `none`, or a list such as `["GB", "XE"]`; `preferred_release_types`, `discouraged_release_types`, `preferred_formats` (lists); `extra_careful`: raises the thresholds to 95 (margin 12), replace 95, organise 98 |
| `identification` | `{"accept": "partial"}` | `exact` (only a proven release is tagged), `album` (also an album whose pressing is unsure), `partial` (also what is certain of an album no release is proven for); Partial always waits for review. While MusicBrainz or Discogs does not answer, no album-only or Partial tags are written: the album is tried again |
| `discogs_details` | performers on, companies off, codes at Audiophile, marketplace and notes off | Discogs details written as tags of their own: `performers`, `companies`, `identifiers` (matrix and other codes), `marketplace`, `notes`; each `true`, `false`, `"audiophile"` (only at that level) or `"tagger"` (as your tagger's Discogs settings say). An entry in `discogs_enrichment` wins |
| `dates` | `{"date_tag": "release"}` | what Date holds: `release` (this pressing's date), `original` (the first release's; Original Date holds it too), `tagger` (MusicBrainz: the pressing's; Discogs: as your tagger's Discogs settings say) |
| `versions` | `{"style": "paren", "extra_tags": true}` | how a kept version shows (see `keep_versions`): `style` `paren` ("Title (Remaster 2024)"), `bracket` ("Title [Remaster 2024]"), `tags_only` (the title stays plain); `extra_tags`: write `ALBUMVERSION` and `RELEASECOMMENT` |
| `cleanup` | comments kept | `comments`: `keep`, or `remove` (a ripper's or a shop's comment is taken off the files). (The tags your tagger settings list as "Preserve these tags" are kept, see `follow_tagger_settings`.) |
| `loudness` | album gain and Opus gains on, never recalculated | `replace_existing`: ReplayGain values from other tools are replaced by TagMender's (Audiophile); `album_gain`: album gain and peak; `opus_r128`: R128 gains in Opus files |
| `genres` | MusicBrainz genres on (at most 5) unless your tagger settings are followed; Discogs styles in Genre when nothing else gives one; web: a shop's own genres | `musicbrainz`: `auto`, `tagger`, `true`, `false`; `max`; `discogs_styles`: `own_tag` (`DISCOGS_STYLE` only), `genre`, `genre_if_none` (also in Genre when the album has no genre otherwise), `both`, `tagger` (as your tagger's Discogs settings say); `web`: `off`, `store`, `keywords` (also Bandcamp's keywords) |
| `tag_level` | `enthusiast` | `basic`: album artist, artist, album, title, track/disc numbers and totals, date, genre (never a comment). `enthusiast`: everything MusicBrainz and Discogs supply, with the MusicBrainz tag mapping and Discogs Enrichment. `audiophile`: enthusiast plus measurements (below). A lower level never removes tags a higher one wrote. A Partial album (see `unresolved.allow_partial`) writes the level's Partial field set only. `--level basic\|enthusiast\|audiophile` on `plan` and `run` overrides it for one run; `apply --level` refuses a plan made at another level |
| `keep_versions` | `true` | a release that is one version of its album (MusicBrainz's disambiguation "remaster 2024", "remix 2024", "deluxe edition"; Discogs's format text "Remix 2024", or - for a release tagged from Discogs alone - its descriptions Remastered, Mono, Remixed, Deluxe Edition, Special Edition; a pressing's words such as "Japanese edition" or "limited edition" name no version) keeps it in the album title, `Title (Version)`, when its release group (Discogs master) holds a release naming another version (a plain release beside a remaster does not count; only the words naming the version are used, and a title that names its version already gets no second mark), so media servers tell the two apart; at Enthusiast and Audiophile the version is also written in `ALBUMVERSION` and MusicBrainz's disambiguation in `RELEASECOMMENT` (both removed again when a later match sets none), and a track title your files mark with the version keeps the mark where the release's title lacks it. Your files' spelling of the version is kept. Never on an album-only or Partial album. `false`: the release's titles as listed |
| `split_versions` | `true` | two versions of an album found in one folder (a remaster's files beside its remix's, "101.T.flac" and "101.T (1).flac"; or a set with a disc of each version) are planned as two albums, each tagged from its own release and moved into a folder of its own - into the destination by the naming for a move or copy profile, else beside the folder it was in - when every file fits one version's track lengths clearly better than the other's and each version proves its own release; the shared cover and scans are copied to each. Each split version keeps its version in its album title even with `keep_versions` off, so the two albums and folders differ. Otherwise the folder is Unresolved with the reason. `false`: never split |
| `level_fields` | `{"add": [], "drop": []}` | `add`: more fields at Basic, e.g. `["label"]`; `drop`: never write these, at any level |
| `audiophile` | all on | `replaygain` (ReplayGain 2.0 + true peak, R128 for Opus), `lyrics` (LRCLIB; synced as `.lrc` next to the track, plain lyrics embedded; see `lyrics`), `acoustid` (AcoustID ID confirmed against the MusicBrainz recording; needs fpcalc and a key), `rip_info` (MusicBrainz disc ID from the rip log's TOC, and the log's verdict in `tagmender_rip`), `quality` (`dynamic_range`, `album_dynamic_range`, and `tagmender_quality` when a lossless file was made from a lossy one: a brick-wall cutoff, e.g. at 16 kHz, found in the spectrum; needs ffmpeg, numpy makes it quicker; without ffmpeg it is skipped and noted; never used to identify). Measurements never replace a value the file has, and are cached |
| `lyrics` | sidecar and embed on, synced preferred | `sidecar`: time-synced lyrics as `.lrc` next to the track; `embed`: also in the file's lyrics tag (plain lyrics are always embedded, they have no file of their own); `synced`: prefer time-synced lyrics (`false`: the plain text, in the file); `enabled`: lyrics below Audiophile too |
| `field_policy` | replace | `{"default": "replace", "fields": {"album": "lock", "comment": "remove"}}` |
| `artwork` | CAA, Discogs, CoverUp; embed front; cover.jpg only into folders without an image | `provider` (an older key, read only by profiles that kept the old `sources` list coverup, caa, discogs: the one tried first), `embed_front`, `files`, `min_px` (smallest short side taken), `max_bytes` (largest download). The embedded copy is at most CoverUp's `embed_max_side` (1200 px; the profile's own `artwork.embed_max_side`, e.g. 600 for a phone library, or 0 for the size found, wins when set), always JPEG/PNG; cover.jpg is always a JPEG. `sources` (order of coverup, caa, discogs, web, local), `when` (needed / always), `enlarge`, `generate` (off / template / ai / auto). `replace`: `never` (default) leaves every picture that is there; `larger_only` replaces an album's own front cover smaller than `min_px` - embedded, or the folder's cover/folder/front file - by a larger one found, else by its own enlarged; other pictures stay; the job keeps the old ones for undo. `artist`: `{"enabled": false, "embed": false, "file": "artist.jpg"}` - when enabled, the artist's photo saved as artist.jpg in the artist folder (for Plex, Jellyfin, Navidrome, LMS), and with `embed` also into each track (Enthusiast and Audiophile). `sources` defaults to caa, discogs, coverup. The Cover Art Archive source (`caa`) follows the cover art options in your tagger settings: `caa_image_types` / `caa_image_types_to_omit` (with `caa_restrict_image_types`; "front" must be among them - only a front is embedded and saved), `caa_image_size` (250, 500, 1200 px or full size; a smaller thumbnail when that one is missing; the next larger one, up to the full image, when the one at that size is below `min_px` on its short side - CAA thumbnails keep the cover's shape, so a 500x490 one at 500 px - rather than enlarging it), `caa_approved_only`, and the release ("Cover Art Archive") and release group ("CaaReleaseGroup") in the order of `ca_providers`, each only when enabled there (both off: no `caa` source; `ca_providers=@Invalid()`, Qt's null, is the default list, as is a value with an item that could not be read and no Cover Art Archive provider among those read - noted, never taken as every provider off; an empty value is). Each source is asked on its own: an error on the release's images (503, 429, a dropped connection) still lets the release group be tried. An enabled option profile's value of any of these, in those settings, wins over `[setting]`. Other cover providers named there (URL relationships, Amazon, plugin providers) have no TagMender source and are not used; `sources` keeps the order of TagMender's own sources. The saved cover's name is `cover_image_filename` (a naming script, `cover` by default, a file name only) + `.jpg`. Without tagger settings (or with `follow_tagger_settings: false`): the front at 1200 px, the release's or else the release group's, `cover.jpg` |
| `operation` | `in_place` | `in_place`, `copy`, `move` |
| `mark_resolved` | `true` | `TAGMENDER=Resolved` in each file of an identified album; re-runs, checks and retries use it to find finished albums |
| `remove_empty_dirs` | `true` | after a move, the folders it left empty are removed (undo puts them back) |
| `allow_unverified_writes` | `false` | tags are written only to formats whose audio TagMender can check stayed the same after the write; `true` also writes the others |
| `rename_in_place` | `false` | with `in_place`: an identified album's files are also renamed by the naming (the file names only; folders stay), at the rename level (`organize_min_confidence`), staged and undoable |
| `on_existing` | `keep_both` | a different file already has the name a file is given: `keep_both` (the new one gets a second name, see `naming.duplicate_suffix`), `skip` (that file is left out, noted), `ask` (the album waits for your review; with `unattended` both are kept) |
| `on_unwritable` | `skip_album` | a folder that cannot be written (a read-only share, no permission): `skip_album` leaves the album as it is, with the reason; `skip_file` leaves only those files out. A copy only reads its source |
| `companions_rewrite_cues` | `true` | the new file names are written into cue sheets and playlists that go with the album; `false` keeps them exactly as they are |
| `destination` | — | must exist (never created); required for copy/move; used for every source folder without a destination of its own |
| `naming` | preset `artist-album-track` ("Artist/1991 - Album/01 - Title", "1-01 - Title" for a set) | or `{"template": "..."}`; presets in "Naming presets" below, plus `all-music`, `various-artists` and `classical-performer` |
| `naming.editions` | `off` | "Separate editions in folder names": `edition` adds [Vinyl] / [WEB] / [Tape]; `edition_year` adds [2019 Vinyl] / [2014 CD] (an original CD, and unknown media, get nothing) |
| `naming.duplicate_suffix` | `number` | a second file of one name: `number` (" (2)", " (3)" ...; the first free one, the same while the folder stays as it is) or `code` (" ~1a2b", the same for one file always) |
| `naming.album_editions` | `off` | the same values, added to the Album tag ("I" becomes "I [2019 Vinyl]"); the folder then never adds it twice |
| `companions` | .cue .log .lrc .jpg .jpeg .png .webp .gif .bmp .pdf .txt .m3u .m3u8 .nfo .accurip .md5 .sfv .ffp .toc .tif .tiff .heic .sha1 .sha256 .crc | go along with a moved/copied album from the folders holding its music; subfolders without music (Scans, covers) go entirely; per-track files follow their track's new name |
| `subfolders` | both off | folders of their own inside the album folder. `covers` (`false`): every picture of the album - every format (JPEG, PNG, GIF, BMP, TIFF, WebP, HEIC, AVIF) and PDF booklets - moves from the album folder and its picture folders (Scans/, Artwork/, Covers/ ...: their name is dropped, what is below them is kept, `Scans/Booklet/01.jpg` becomes `covers/Booklet/01.jpg`) into `covers_name` (`"Artwork"`); the front cover (`cover.*`, `folder.*`, `front.*`, `poster.*`, `AlbumArt*`, and the cover file name your tagger settings give) stays beside the music; a folder without one keeps the picture most like a front cover (its name says front, or cover and not back, inlay, tray, CD ..., else the folder's only picture) beside the music, and one kept only in a picture folder moves beside the music when the album folder has none. `misc` (`false`): rip logs and notes (`.log`, `.txt`, `.info`, `.nfo`, `.toc`, `.accurip`, `.md`, `.rtf`, `.doc`, `.docx`, `.odt`, `.htm`, `.html`, `.url`, `.json`, `.xml`, `.csv`) move into `misc_name` (`"Extras"`); cue sheets, playlists and checksum lists (`.cue`, `.m3u`, `.m3u8`, `.sfv`, `.md5`, `.ffp`, `.st5`, `.sha1`, `.sha256`, `.crc`) and a track's `.lrc` stay beside the audio they name, and so do `album`/`artist`/`folder`/`metadata` `.nfo`, `.xml` and `.json` files (media servers read them from the album folder). `where`: `album` (default) - one of each in the album folder, a folder per disc inside it (`Artwork/CD1/`); `disc` - each disc folder of a set gets its own (`CD1/covers/`, `CD1/misc/`), the album folder's own files go into the album folder's. Done only for an identified album (a folder nothing identified is left as it is): for one tagged in place (an in-place profile, or one a move profile filed already), and for one moved or copied (into the new folder; a copy profile never changes the source). Planned, staged, journaled and undoable as every move; a name taken in the folder gets a suffix, never overwritten; planning again plans nothing new; where a file or link already has the folder's name, the files stay where they are (a plan note says so). TagMender still reads the scans, logs and notes in these folders. Folders with other music, and other folders holding more than pictures and papers (Extras/ with a video), are left as they are |
| `preserve_timestamps` | `null` | keep files' modification times; `null` follows "Preserve timestamps of tagged files" in your tagger settings |
| `filenames` | windows-compatible, 255 bytes per name, 4096 per path, full album names | `max_name_bytes` and `max_dir_bytes` (255: a file's and a folder's name, in UTF-8 bytes), `max_path_chars` (4096: the whole path); `compat`: `windows` (names every system and NAS share takes) or `linux`; `fat` (with `windows`): a USB stick or car player - at most 255 characters below the stick's own folder; `max_album_words`: cut the album folder's name to this many words (0, the default: the full title; at most 20); `abbreviations`: `{"Volume": "Vol."}` |
| `unresolved` | `mark`, `allow_partial` on | `action`: `leave`, `mark` (TAGMENDER tags), `move` (to `move_to`). `allow_partial` (default `true`): an album whose release is not proven but which is itself established gets its release-independent fields and `TAGMENDER=Partial` (see "Identification tiers" below); `false`: the Unresolved marker only. With `action: leave` nothing is written on such an album, Partial tags neither (`allow_partial` then does nothing) |
| `retry` | after 30 days, 6 attempts | watched folders: Unresolved and Partial albums are identified again after `after_days`, at most `max_attempts` times (`tagmender unresolved` lists both, `tagmender retry` tries both again) |
| `unattended` | false | apply without asking (watcher): only albums identified for sure (Ready); any it is not sure of (Unresolved, Partial, two pressings) waits for your approval |
| `watch` | settle 10 min, every 300 s | `settle_minutes` (≥ 0): an album waits until nothing in it changed this long; `interval_seconds` (1 to 86400; the Watched folders page offers every minute up to once a day): how often watched folders are looked at; one album is worked on at a time. The watcher looks as often as the shortest `interval_seconds` of all profiles. These two and `unattended` are set on the Watched folders page too. `tagmender watch --add FOLDER --profile NAME` sets a watched source |
| `backup_retention_days` | 30 | no longer read: how long Undo works is this computer's setting (`backups.keep_days`, below); kept so earlier files load |
| `exclusions` | `[]` | this profile's Ignore/Skip rules, added to the global ones |

## How the Profiles page shows these settings

Every key above stays as it is in the file. Some rows of the Profiles page merge several keys into
one choice; each reads the keys it covers, and writes the same keys when you change it. A saved
combination a row cannot say exactly is shown as "As saved (...)" and is never rewritten until you
choose something else.

| Row | Keys it reads and writes |
| --- | --- |
| Find lyrics: Off, At the Audiophile level, Always | `lyrics.enabled` and `audiophile.lyrics`. Below the Audiophile level only `lyrics.enabled` counts; at it only `audiophile.lyrics` does. Lyrics looked up below the level but not at it is "As saved" |
| Sources: the four values, or Web only | `source`, `providers.musicbrainz.enabled`/`identify`, `providers.discogs.enabled`/`identify`, `providers.web.enabled`. A preferred database whose other one is off shows as that database only (the other is never asked either way). Web only: both databases do not identify, the web fallback does |
| Tag an album when: with "Partial, also from store pages" | `identification.accept`, and `providers.web.partial` (read only with `partial`) |
| What happens to files: Fix in place, Fix in place and rename, Copy, Move | `operation` and `rename_in_place` |
| Then (a file is already there) | `on_existing` and `naming.duplicate_suffix` (Skip and Ask me leave the suffix as saved) |
| Folder for lossy files | `destinations.lossy` (empty: with the rest) |
| Skip these folders and paths | `skip_folders` (names) and `exclude_paths` (an entry with a `/` is a path) |
| Measurements (Audiophile) | `audiophile.replaygain`, `acoustid`, `rip_info`, `quality` |
| Details to write (Discogs) | the five `discogs_details.*` keys; `"tagger"` or `"audiophile"` shows as "As saved" |
| Credits | `tagger.release_ars` and `tagger.track_ars` |
| Artist photos: Off, Save artist.jpg, Put it in each track, Both | `artwork.artist.enabled`, `.file`, `.embed` |
| Put the cover in each track | `artwork.embed_front` and `artwork.embed_max_side` |
| Folders inside the album folder | `subfolders.covers` and `subfolders.misc` |

`unattended`, `watch.settle_minutes` and `watch.interval_seconds` are set on the Watched folders
page, not in the profile editor. A few rows appear only when their saved value differs from the
default (`grouping.join_deluxe_folders`, `split_versions`, `mark_resolved`,
`companions_rewrite_cues`, `remove_empty_dirs`), and the rows that follow another tagger appear
when its settings file is found or the profile says something other than the default; the values
are honoured either way. A row that does nothing in the current state (Basic level, a database
that is not asked, covers off, in place without renaming, a copy) is greyed with the reason.

## Profiles written by earlier versions (schema 3)

This version's defaults suit most people (MusicBrainz preferred, readable names, no artist photo
in every track, your own covers kept). A profile file written by an earlier version keeps doing
what it did: each setting it left unset is read as the earlier default (Discogs preferred, the
`tagmender` short names with 5-word album folders, `covers`/`misc` in each disc folder, artist
photos in the tracks, small covers replaced, web Partial on, AcoustID off, `_*` and `2delete`
folders skipped, the earlier companions and country order, your tagger settings followed).
`tagmender profiles migrate` writes those values into the file (each nested setting on its own,
the old file kept as `NAME.json.bak-<time>`).

The built-in settings (no profile chosen) are not a file, so they do change. The app offers once
to save the earlier ones as a profile; the command line and the watcher say so once, and
`tagmender profiles save-previous-builtin` saves them as the profile "Built-in (previous)".

A profile never names code to run: the plugin folder and another tagger's command are this
computer's settings only.

## This computer's settings

`~/.config/tagmender/settings.json` holds what belongs to the machine rather than a profile. It is
written the first time it is read: a new install gets the defaults below, one that ran an earlier
version keeps its behaviour (`2delete` for duplicates, the newest copy kept, `_*` skipped, no
automatic backup cleaning, saved lookups kept for ever, Skip by 5 seconds, scans read in English,
Russian and Japanese, a 100 MB log, names shown in diagnostics). A setting the file leaves out is
read as what the machine started as (`started_as`: `new` or `earlier`). The command line, the watcher and the app all read it.

| Setting | Default | Meaning |
|---|---|---|
| `backups.keep_days` | 30 | how long Undo works: a job's backups are kept this many days (0: for ever); `tagmender jobs prune` uses it unless `--days` is given |
| `backups.auto_clean` | on (off on an earlier install) | after each apply, backups older than `keep_days` are deleted and those jobs can no longer be undone |
| `backups.location` | the state folder | never on a library's share, inside a library folder or in a folder that is not mounted; stored for future use, nothing reads it yet |
| `duplicates.folder_name` | `_Duplicates` (`2delete` on an earlier install) | where the duplicate tools set a second copy aside, at the library's top; always skipped when looking for albums, and so are the names it had before (`duplicates.previous_names`, kept when you rename it) and both built-in names |
| `duplicates.dispose`, `duplicates.keep` | `folder`, `best` (`newest` on an earlier install) | stored for future use, nothing reads them yet |
| `skip_folders` | the NAS recycle bins (.Trash\*, .recycle, #recycle, @Recycle, @eaDir, $RECYCLE.BIN, lost+found); `_*` and `2delete` on an earlier install | folders the whole-library tools never look into |
| `library_folder` | the system Music folder | where the app's folder choosers open (Preferences > General) |
| `player.skip_seconds` | 10 (5 on an earlier install) | the app's Skip by (Preferences > General) |
| `notifications.finished`, `notifications.watch` | on | the app tells you when a run ends or the watcher finished an album, while it is in the background |
| `network.refresh_days` | 30 (for ever on an earlier install) | a saved lookup is asked again only when its album is processed again, never in a background sweep (0: for ever) |
| `ocr.languages` | `[]` (`["eng", "rus", "jpn"]` on an earlier install) | `[]`: English plus your system language, of the Tesseract packs installed; or a list such as `["eng", "jpn"]` |
| `ocr.effort` | `standard` | scans read for an album: `quick` 8, `standard` 15, `thorough` 40 |
| `diagnostics.hide_names` | on (off on an earlier install) | the diagnostics export replaces folder and file names with short codes (Preferences > Advanced) |
| `plugins.folder` | `""` | the folder holding the Discogs Enrichment and CoverUp code (`""`: the environment, then the settings folder, then the bundled copy). Code runs from there, so a profile can never name it |
| `network.offline`, `network.store_country`, `network.wikipedia_language`, `default_profile`, `appearance`, `permissions.new_folders`, `media_server.rescan`, `logs` (`max_mb` 10; 100 on an earlier install), `tagger_command` | | stored for future use, nothing reads them yet |

In the app: Preferences > General sets the music folder, Skip by and the notifications; Network &
Privacy the saved lookups and reading scans, with Test Connection; File Safety the backups and the
duplicates folder (shortening the backups names the jobs that would lose Undo first); Advanced the
plugin folder and whether diagnostics hide names.

## Identification tiers

Every album in a plan carries the tier identification reached (`identification.tier` in the plan's
JSON; `plan` prints it after the score, the app's status column says "Ready · Album only" or
"Ready · Partial"):

| Tier | When | Written | TAGMENDER |
|---|---|---|---|
| `exact` | the release (pressing) is proven: barcode, catalogue number, disc ID, scores | every field of the level | `Resolved` |
| `album_only` | the album is certain, its pressings tie | fields every tied pressing shares; no release IDs, catalogue number or barcode | `Partial` + `ambiguous_release: album known, pressing not established ...` (no release is proven; `tagmender unresolved` lists it, `tagmender retry` and the watcher try it again; `web_tag.py --apply` marks it so too) |
| `recording` | a single file's recording, from its fingerprint | title, artist, recording and artist IDs; album, date, genre and release-group ID when the recording is on exactly one MusicBrainz release group; a recording on no release at all (a standalone recording) gets the standalone recordings name as its album (`nat_name` in your tagger settings, else `[standalone recordings]`) when the file has no album of its own (one it has is kept) | `Resolved` |
| `partial` | review or no match, but the best candidate's **album** is established | the level's Partial field set (below), never renamed, moved or copied | `Partial` + the reason |
| `unresolved` | nothing usable | nothing but the marker | `Unresolved` + the reason |

**Established album** (all of these, the best candidate rescored against your files): the same
number of tracks; artist and title as similar as an automatic pick needs (`auto_min_artist`,
`auto_min_title`, 0.85); at least `auto_min_duration_frac` (80 %) of the track lengths agree; a
credit that leaves out none of the artists your tags name; and no other candidate the matcher
scored within `auto_min_margin` points that is not shown to be in the same MusicBrainz release
group or Discogs master (a candidate of a provider that was not checked counts against it, and so
does a full list of three candidates whose last is still that close: the next is not seen). A
Discogs release without a master gives nothing. When MusicBrainz or Discogs failed for the album
(an outage, rate limiting, an unreadable answer: also partway through the search) it is
Unresolved with that reason and retried later, never Partial. Files that already name a release
(a MusicBrainz or Discogs release ID, or `TAGMENDER=Resolved`) only get the fields they lack:
their date, album, titles and mark stay as they are. A Partial album is identified again later, as
an Unresolved one is (watched folders after `retry.after_days`, `tagmender retry`): once
MusicBrainz or Discogs has its barcode or disc ID it may be matched exactly, and the Partial mark
and reason are replaced.

**Partial fields** (from the MusicBrainz release group and its recordings, and the Discogs master):
album artist(s), artist(s), sort names, album, track titles, track/disc numbers, genre (and
Discogs styles as your style mode routes them), original date/year (the release group's or
master's first release; `date` is set to it only where the files have no date, since a date they
carry may be their own pressing's), release type, recording-level credits and works
(composer, lyricist, performers, mixer ...), ISRCs, MusicBrainz artist, release-group and recording
IDs, the Discogs master and artist IDs. The album title and genres are the release group's (never
one pressing's "X (Deluxe Edition)"); the album artist only when the release group's credit, or
every pressing's, is the same. A field the pressings of the release group, or MusicBrainz
and Discogs, disagree on is left out: a recording (with its ISRCs and credits) only where every
pressing with your track count has the same one, titles and artists only where they all agree,
track and disc numbers only where they all split the discs alike, with no video or other
non-audio disc beside the audio ones, and your files not split into another number of discs (a
release group too big to read whole: only for a one-disc release whose files are not split into
discs), the original year only
when both databases agree. A track title MusicBrainz and Discogs spell differently is
your file's own where yours is either one's title (case, accents and punctuation aside), as your
files spell it, else MusicBrainz's; with Discogs's titles alone, where one of yours is Discogs's
title spelt otherwise, your title is kept when a MusicBrainz release among the candidates has it
(at most six of them read) - that release only shows Discogs's spelling wrong, its own spelling is
never written. A total
your files have ("TRACKTOTAL=03", under one Vorbis name or both, or ID3's "1/03") is written
plain ("3") when the file is written, as an exact match writes it (a Vorbis total under one name
also under both names), unless `field_policy` (or the preserved tags in your tagger settings) keeps that field. From the Discogs master alone: a track title only where the master and
the release that fits your files agree on it, a track's own artist credit (split albums,
compilations), track and disc numbers only when the master has one version, that one-disc
release. Never written: release IDs, barcode, catalogue number, label, country, media,
the pressing's own date, ASIN, script, release status, and credits of the release itself (its
mastering, its producer). Basic writes only the Basic fields of that set; Audiophile adds its
measurements (ReplayGain, dynamic range, quality, the rip log's verdict and disc ID, AcoustID);
lyrics wherever they are looked up (Audiophile, or `lyrics.enabled`). A Partial album deletes
nothing, and `naming.album_editions` adds no edition to its album tag (its media is not proven). Front cover: the release group's at the Cover Art Archive, the Discogs master's image.
A Partial or album-only album in a folder marked as restored or remastered audio (`[Restored]`,
`[RM]`, "Remaster(ed)", "RMX" in the folder's name, or the set's folder above a disc folder) gets
no ISRCs: an ISRC names one master's recording, and restored audio is another master. Its
recording IDs stay (the same performance).

## Naming presets

| Preset | Gives |
|---|---|
| `tagmender` | `Robert Rich & Steve Roach/1992 - Soma The Twentieth Anniversary Edition/03.TTE.flac`, multi-disc `.../Disc 02/203.TTE.flac` (TagMender's existing scheme) |
| `standard` | `Album Artist/Album/03 Title.flac` |
| `artist-album-track` | `Album Artist/1992 - Album/1-03 - Title.flac` |
| `classical` | `Composer/1992 - Album/03 - Title.flac` |
| `singleton` | `Artist/Singles/Artist - Title.flac` |

## Template language

A naming template is a script in TagMender's tagging script language: every function of the
language, TagMender's `$words(text,n)` (the first n words; an album ending in its `ALBUMVERSION`
in brackets, "(Remaster 2024)", keeps that whole) and its own variables `%_year%` (original year,
else release year), `%_albumartists_sorted%`, `%_multiartist%`, `%_edition%` and
`%_edition_kind%`. `%field%` gives the tag (several values joined with "; "). `/` separates
folders; a `/` inside a value becomes `_`. An unknown function is an error when the profile is
checked. The naming script never changes tags.

See the [Scripting guide](SCRIPTING.md) for the syntax, every function with examples, and recipes
(genre folders, year - album, multi-disc, first-letter folders, Various Artists).
