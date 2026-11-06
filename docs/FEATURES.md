# Features

What TagMender does, feature by feature. Updated 2026-11-01.

**Full** = done and tested. **Partial** = some of it (what is missing is said).
**Not planned** = deliberately not done, with the reason.

Tags use the MusicBrainz tag mapping, so files read the same in other taggers and players.
Settings imported from an existing tagger installation are found automatically and used where
TagMender has no setting of its own (`TAGMENDER_TAGGER_INI` names another file); a profile's
`"tagger"` entries override them, and `follow_tagger_settings: false` ignores them
([profiles](PROFILES.md)). Without either, the standard defaults apply.

## Finding the release

| Feature | Status | Notes |
|---|---|---|
| Lookup by tags, file names, folder names | Full | plus catalogue numbers, barcodes, OCR of scans, rip logs, cue sheets, links in tags |
| Order scans are read in | Full | the kinds of scan (back, disc, obi, booklet...) whose codes most often confirm the release first, learned from your albums (role counts only; `tagmender ocr stats`, `--export` to share them in a GitHub issue; nothing is sent) |
| Codes searched beyond the databases' own search | Full | codes read on scans are asked of MusicBrainz and Discogs in their other spellings (separators, O/0, I/1) and through Discogs' free-text search; an optional web search (Mojeek, Marginalia; off by default, at most 3 codes an album, never junk ones, a monthly cap counting only searches that reach the engine; set up with `tagmender setup` or Preferences); no AI search unless chosen (`tavily`), no search page scraped |
| Track number and title from file names | Full | in the matcher |
| Audio fingerprints (AcoustID) | Full | needs fpcalc and your own AcoustID application key; when to fingerprint is the profile's `acoustid: unsure` (default) / `always` / `off`; the AcoustID provider itself is off in the default profile |
| Disc ID lookup | Full | from EAC/XLD logs, cue sheets and track lengths; in the app also File > Look Up CD… (see "The app" below) |
| Preferred release countries | Full | your list (tagger settings or the profile's `tagger.preferred_release_countries`); also picks the RELEASECOUNTRY written |
| Preferred release formats | Partial | only the last tie-break, after the country order, between pressings known to be one album (one MusicBrainz release group or Discogs master, or a release group whose Discogs master link names the Discogs pick's master); never a score, so such a pick is never automatic (the album is tagged without the pressing's IDs). From your tagger settings or the profile's `tagger.preferred_release_formats`. Format names are compared exactly only when every tied pressing lists them and all are MusicBrainz releases; otherwise by kind (CD, vinyl, digital, cassette), and only for a generic name (`CD`, `Vinyl`, `Digital Media`, `Cassette`): a kind cannot tell SACD, SHM-CD, HDCD, CD-R or a sized vinyl from its generic format, so a preferred sub-format leaves the tie undecided. Asking for the release group's Discogs master link costs one request an album, made only when a preferred format is set. Formats read from the folder name and the files are evidence and come first |
| Preferred and discouraged release types | Partial | a tie-break as the formats, just before them, and only between pressings of one album (in practice between Discogs releases of one master, since MusicBrainz gives a whole release group one set of types). A pressing of a discouraged type leaves the tie before the formats are compared, so the tie-break never picks it, but it is never dropped against a different album. Order of the tie-breaks: your copy's evidence, the release your files are tagged with, one pressing listed twice, the original pressing, the country order, release types, formats |
| Match thresholds | Full | `thresholds` in profiles |
| Choose another release (other versions, paste an MBID or link) | Full | `plan|run ALBUM --release LINK`; in the app, Album > Change Release: Paste a Release Link…, Search MusicBrainz and Discogs… or Other Versions of This Album… |
| Standalone recordings | Full | a single file whose fingerprint gives a recording on no MusicBrainz release gets the standalone recordings name as its album (`nat_name`, `[standalone recordings]` by default; tier "recording"); a recording on one release group gets that album, on several none. A known album's track is never named a standalone recording, and an album the file already has is kept |
| Interactive matching of files to tracks | Partial | Album > Match Files Manually… (Ctrl+Shift+M); no clusters: albums come from folders |

## What is written

| Feature | Status | Notes |
|---|---|---|
| Album, title, track/disc numbers and totals, dates, IDs, label, catalogue no., barcode, ASIN, media, disc subtitle, ISRC, script, language, release type/status/country | Full | `mb_tags.py`, field names and values as the MusicBrainz tag mapping |
| Credited artist names | Full | |
| Standardize artist names (none / variations only / variations and name changes) | Full | default: variations only |
| Always standardize multi-valued artist tags (ARTISTS) | Full | off by default |
| Sort name from the alias matching the credit | Full | |
| Translate artist names (locales, from sort name) | Full | off by default; script exceptions with weighted script detection |
| Translate album / track titles | Full | off by default |
| Various Artists name (`va_name`) | Full | album artist and track artist; default "Various Artists" |
| `compilation=1` on Various Artists releases | Full | removed from other releases (unless the profile preserves it) |
| Convert Unicode punctuation to ASCII | Full | off by default (`text_ascii.py`) |
| Release relationships (release-level producer, engineer, mixer ..., license, ASIN) | Full | on by default |
| Track relationships (composer, lyricist, writer, arranger, conductor, performer:instrument, work, work ID, work languages) | Full | off by default; the work's title, ID, languages (`zxx` for an instrumental performance), comment, ISWCs and the work's own credits |
| Standardized instrument / vocal credits | Full | on by default |
| Genres from MusicBrainz (min usage, max, filter, album artist genres, folksonomy tags, join) | Full | on by default (`genres.musicbrainz`: automatic, at most 5), or as your tagger settings say when the profile follows them (`text_util.py` filter); "only my genres" needs a MusicBrainz login and writes none |
| Ratings (POPM / RATING) | Full | the inspector's Rating row (0-5 stars) is an edit of the preview, written on Apply: ID3 POPM with the rating e-mail (`rating_user_email`, default users@musicbrainz.org; 0-255), Vorbis `RATING:<e-mail>` (0.0-1.0), WMA WM/SharedUserRating (0-99), read back after writing; staged, verified and undoable as every edit. MP4 and APEv2 hold none. Album > Submit Ratings… sends them to MusicBrainz (see "MusicBrainz account features") |
| Discogs data | Full | the built-in Discogs plugin and its settings |
| ReplayGain | Full | Audiophile level, EBU R128 |
| Lyrics | Full | Audiophile level, LRCLIB, synced `.lrc` and embedded plain |
| Work / movement tags (movement, movementnumber, movementtotal, showmovement) | Full | read and written as a file has them (MVNM/MVIN, Vorbis MOVEMENT/MOVEMENTNAME, MP4 ©mvn/©mvi/©mvc/shwm, APEv2), Partial albums too; none are made up from MusicBrainz (only `work`, the recording's work) |
| Parent works and movements built from them | Not planned | the parent-work lookups multiply MusicBrainz requests per track |

## Tag options

| Feature | Status | Notes |
|---|---|---|
| Tag mapping per format | Full | ID3, Vorbis, MP4, APEv2, ASF (credits in TMCL/TIPL and PERFORMER, MVIN, WCOP, WOAR, cpil ...) |
| Preserve these tags | Full | from your tagger settings |
| Clear existing tags | Not planned | wiping every tag loses what no database has (your own tags, comments, ratings); per-field policies instead (preserve, fill, replace, remove, lock) |
| ID3v2 version | Full | `write_id3v23` from your tagger settings or the profile's `tagger.write_id3v23`; without either, each file keeps its own version (a v2.3 tag stays valid v2.3: dates in TYER/TDAT, credits in IPLS; new tags are v2.4). Applied when a file is written |
| ID3v2.3 join character | Full | `id3v23_join_with`, `/` by default |
| Write ID3v1 | Full | `write_id3v1`: written or removed when the file is written; without a setting, an existing ID3v1 is updated and none is added |
| Remove APEv2 from MP3, ID3 from FLAC | Full | `remove_ape_from_mp3`, `remove_id3_from_flac`, off by default; undo puts them back |
| Remove APEv2 from AAC/AC3 | Not planned | TagMender does not tag raw AAC/AC3 streams |
| ID3v2 text encoding | Full | `id3v2_encoding` from your tagger settings: UTF-8 (UTF-16 in an ID3v2.3 tag), UTF-16, or ISO-8859-1 with what Latin-1 lacks simplified; every text frame of a written file, numbers and pictures in Latin-1; a frame TagMender did not write whose text Latin-1 cannot hold keeps its encoding. The preview says "text in UTF-16 / Latin-1" only when a frame the file has changes encoding. Not a profile `tagger` entry |
| Fix missing FLAC seek points | Full | `fix_missing_seekpoints_flac`, off by default: an empty seek table removed; undo puts it back |
| WAV RIFF INFO | Full | written beside the ID3 tag |
| Preserve timestamps | Full | follows your tagger settings unless the profile says |

## Cover art

| Feature | Status | Notes |
|---|---|---|
| Cover sources | Partial | the Cover Art Archive first, then Discogs and CoverUp; order per profile (`artwork.sources`). `ca_providers` in your tagger settings decides the Cover Art Archive's release / release group order and whether each is used, each asked on its own (one failing does not stop the next); an unreadable value is the default list, never "every provider off". URL relationships, Amazon and plugin providers have no TagMender source |
| Cover Art Archive image size, approved only, restrict types | Full | `caa_image_size` (a smaller thumbnail when that one is missing; a larger one, up to the full image, when the one at that size is below the profile's `artwork.min_px` on its short side, rather than enlarging it), `caa_approved_only`, `caa_image_types` / `caa_image_types_to_omit` / `caa_restrict_image_types`; only a front is taken (TagMender saves no back cover). Without tagger settings: front, 1200 px |
| Embed images into tags / save as separate files | Partial | TagMender's own profile settings (`artwork.embed_front`, `artwork.files`: both on); the file is named by the `cover_image_filename` script (`cover.jpg` by default; a file name only, no folders); covers are not embedded into WavPack or WMA |
| Embed only one front | Full | always one front |
| Overwrite existing images | Full | `replace: larger_only` replaces only small covers, backed up |
| Resize / convert / enlarge for tags and files | Partial | embed limit 1200 px, enlarge small; no format conversion setting |
| Keep embedded images | Partial | an embedded cover is kept unless it is small (`replace: larger_only`) |
| Remove images from tags | Not planned | an embedded picture is never removed without one to replace it (a small cover is replaced only by a larger one, backed up and undoable); removing every image would lose scans no source has |
| Artist photo | Full | off by default; when turned on (`artwork.artist.enabled`), saved as `artist.jpg` in the artist folder, and put into the tracks too only with `artwork.artist.embed` |

## Files and naming

| Feature | Status | Notes |
|---|---|---|
| Rename and move files | Partial | journaled and undoable; the profile's operation (in place, copy, move) and naming decide; `rename_in_place` renames the files where they are (in place, nothing moved) |
| Covers and misc folders | Full | profile `subfolders`: pictures and PDF booklets into `covers/`, rip logs and notes into `misc/` (names of your choice), per disc folder or once per album; the front cover (or the picture most like one), cue sheets, playlists, checksum lists and album.nfo/artist.nfo stay beside the audio, and a folder holding more than pictures and papers (Extras/ with a video) is left as it is; only for an identified album; in place or with a move/copy; undoable |
| Run report | Full | `tagmender report` and Edit > Show Report…: needs your decision, could not finish, done; folder links, the issue in words, suggested releases with Apply This Release (a preview first); HTML and CSV export |
| Move additional files | Partial | always on for a moved or copied album, by TagMender's own list (`companions`: logs, cue sheets, images, lyrics ...); cue/playlist references updated (`companions_rewrite_cues`) |
| Delete empty directories | Full | follows your tagger settings unless the profile sets `remove_empty_dirs` |
| Windows-compatible names | Full | follows your tagger settings unless the profile sets `filenames.compat`; plus byte, UTF-16 and path limits, shortening; control characters (a tab) are replaced too |
| Windows long path support | Full | `windows_long_paths`: off, a path below the destination's mount point fits 259 characters, shortened to fit |
| Name collisions | Full | a stable " ~1a2b" suffix (the same file always gets the same one); room for it is kept |
| Replace non-ASCII characters | Full | `ascii_filenames` |
| Replace spaces with underscores | Full | `replace_spaces_with_underscores` |
| Directory separator character | Full | `replace_dir_separator` (`_`): a "/" in a value |
| Windows compatibility replacement characters | Partial | always `_` |
| Overwrite existing files | Not planned | an existing file is never overwritten (a rule of every write: staged, verified, undoable) |
| Naming script language | Full | the tagging script language (`tagscript/`): 82 functions, plus `$words`; naming presets `tagmender`, `standard` and others ([profiles](PROFILES.md#naming-presets)) |
| Tagger scripts (change tags with a script) | Full | TagMender scripts from your tagger settings or a profile's `tagger.list_of_scripts`, run on each track after the Discogs data is applied, with the usual hidden variables; `$delete` removes the tag; names are case-sensitive, but for the fields TagMender writes: `$set(GENRE,Jazz)` sets the genre. A track or disc number a script writes as "N/total" is written as the number and its total |
| Plugins in general | Full | Preferences › Plugins: the built-in Discogs and CoverUp, and plugins you install (docs/PLUGINS.md) |

## The app

Each of these changes the preview only: a chosen release previews the album again, an edit is
one of your edits of the plan, and nothing is written until Apply, staged, verified, journaled
and undoable as every change. Lookups and file reads run off the UI thread.

| Feature | Status | Notes |
|---|---|---|
| Search (releases, recordings, artists) | Full | Album > Change Release > Search MusicBrainz and Discogs… (Ctrl+Shift+F; Ctrl+F stays Find): MusicBrainz releases, recordings (with the release each is on) and artists (Show Releases lists an artist's releases), and Discogs releases, in one sheet (Ctrl+1 … Ctrl+4); Use This Release previews the album again with it; Open in Browser |
| Other versions (release group's releases) | Full | Album > Other Versions… (Ctrl+Shift+L): the MusicBrainz release group and the Discogs master of the release the plan identified, each scored against the files |
| Two versions of an album in one folder | Full | a remaster's files beside its remix's ("101.T.flac" and "101.T (1).flac"), or a set with a disc of each: each file given to the version its length fits, each version tagged from its own release and planned into a folder of its own (shared cover copied to each); undoable; `split_versions` turns it off. MusicBrainz release groups only for the fallback by lengths (a Discogs master's versions are not tried) |
| Choose your pressing | Full | Album > Choose Your Pressing… (Ctrl+Shift+P) for an Album only or Partial album lists only the tied pressings, highlights the details that differ and says what to check on your copy |
| Tags (original and new values, edit, revert, add, remove) | Partial | the inspector's Tags tab: Tag, Original, New for one file or every file; Edit Value… (double-click, F2), Revert to Original, Use Suggested Value, Add Tag…, Remove Tag; each edit shown as yours and kept by a new preview. Not done: copy and paste of tags between files, editing several albums at once |
| Cover art (drop an image) | Partial | the inspector's Artwork tab: the cover now and the new one with their sizes; an image file or link dropped or pasted there becomes the new cover. Front cover only |
| Script editor (naming script, tagger scripts) | Partial | Tools > Script Editor… (Ctrl+Shift+E): the profile's naming script and tagger scripts with highlighting, completion, the help of the function under the cursor, bracket matching, errors underlined at their line and column, and a preview on the selected album's planned tags. Saving writes the profile only. Not done: a list of named naming scripts to choose from, importing or exporting script files |
| Tags from file names | Full | Tools > Tags from File Names… (Ctrl+Shift+G): a pattern and presets, Replace underscores with spaces, a preview per file; the tags become your edits of the preview |
| File info | Partial | Album > Info… (Ctrl+I): one file's format, codec, bitrate, sample rate, bit depth, channels, length, size, every tag as stored (raw keys) and each embedded picture (read only). Not done: an album-level info view |
| Match files to tracks by hand | Partial | Album > Match Files Manually… (Ctrl+Shift+M): the album's files beside the release's tracks, paired automatically by disc and track number, title and length; drag files onto tracks or press Match, Unmatch takes a pair apart, each pair shows the length difference. Use These Matches turns the pairs into your edits of the preview; a file the plan renames or moves takes the name and place of the track it is matched to, and a file left unmatched stays where it is with its own tags. Another release than the plan's can be used only with every file matched and as many tracks as files. No clusters: albums come from folders |
| Look up a CD | Full | File > Look Up CD… (Ctrl+K): the disc's table of contents from the drive (python-discid, optional; the drive is chosen in Preferences > Account), an EAC/XLD rip log or a pasted TOC; Look Up says first exactly what is sent, lists the releases with this disc, exact ID first; Use This Release previews the album again; Submit Disc ID… opens MusicBrainz in the browser. TagMender does not rip CDs: a ripper's log is read instead |
| Browser integration (the tagger buttons on musicbrainz.org) | Full | Preferences > Account, off by default: a server on 127.0.0.1 only (port 8000 or the next free one); the release opens in the search sheet with its ID filled in, searched only when you press Search. Requests from another host or site, and anything but GET, are refused; asks closer than a second apart are answered 429 |
| Player | Full | a bar under the pages, hidden until something plays: Controls > Play/Pause (Space in the album tables), Next, Previous, Stop; seek, speed, volume (saved), mute. Plays the album's files from their folder, read only, with Qt Multimedia (the controls say why when there is no sound output); it lets go of the files before Apply, Undo or Recover touches them |
| Refresh with new settings | Full | Overview: Refresh (Ctrl+R) looks the selected albums up again with the profile as it is now (all of them when none is selected); after a profile is saved the Overview names the albums looked up with its old settings, with a link that refreshes those |
| Apply a setting to every profile | Full | Preferences › Profiles: Apply to All Profiles… on each setting; the profile shown is saved first, and nothing is written if any profile could not take the value |
| Loading, slow and failed look-ups | Full | Search and Change Release show the rows' shape while they load, say when it is slow, offer Stop after 25 seconds, and on a failure say what happened with Try Again; the pulse stands still with Reduce motion |
| Duplicate copies to the Trash | Full | Preferences › File Safety › Duplicate copies go to: the duplicates folder or the Trash of the drive the copy is on (`tagmender dedupe --undo` puts them back) |
| Clear History | Full | History › Clear History…: everything, or what is older than 1, 5 or 10 days; the rules and what they leave alone stay |
| Log viewer and job history | Full | Window > Log (Ctrl+Shift+Y): the log by level, searched as you type, following new lines; Open Log Folder. The Jobs page and Edit > Job Details… (Ctrl+Shift+J): what a job wrote, file by file, with Undo |

## MusicBrainz account features

Each sends only when you choose it for one album, after a sheet lists exactly what is sent and
where; no music file changes. You bring your own MusicBrainz application and AcoustID user key
(Preferences > Account; [providers and keys](PROVIDERS.md), [setup](SETUP.md)).

| Feature | Status | Notes |
|---|---|---|
| Log in to MusicBrainz (OAuth2) | Full | Preferences > Account > Log In… / Log Out…, with your own "Installed application" client ID and secret (musicbrainz.org or metabrainz.org); the browser opens the allow-access page and you paste the code. Tokens kept 0600 in `musicbrainz_login.json`, refreshed when they expire, revoked and deleted on Log Out |
| User collections (add or remove a release) | Full | Album > Collections…: a sheet names the requests that read your collections, you tick the ones that should hold the album's release, then a sheet lists each change before it is sent |
| Submit ratings | Full | Album > Submit Ratings…: your stars from the inspector (else the files' own ratings) for each track with a recording ID, the request shown first |
| Submit AcoustID fingerprints | Full | Album > Submit AcoustIDs…: fpcalc on each file (read only), a sheet lists every track with its recording ID, the length check against MusicBrainz's track length (30 s; a track whose length is not known is sent unchecked, and the sheet says so), format and bitrate; "Send N Fingerprints" sends them; then each submission's status, with Check Again |
| Submit ISRCs, ListenBrainz | Not planned | ListenBrainz is about listening, not tags; ISRCs on a disc need the drive's subchannel data, which TagMender does not read |
