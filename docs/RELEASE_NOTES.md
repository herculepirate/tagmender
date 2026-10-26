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
punctuation as ASCII, the preferred release countries, and MusicBrainz genres (when Picard's
"Use genres from MusicBrainz" is on: its filter, minimum usage, maximum, album artist genres,
folksonomy tags and join character). `tests/test_picard_parity.py`
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

**Naming and tagger scripts run on Picard's own script engine** (copied from Picard 3.0.2):
every one of its 82 functions, with Picard's semantics, so a script works here exactly as in
Picard. Picard's tagger scripts (Options > Scripting) run on each file after Discogs
Enrichment, as Picard runs them - on the file's own tags overlaid with the release's, so a
script reading %genre% sees the file's genre, and with the file's variables (%_extension%,
%_format%, %_bitrate% ...) - with Picard's hidden variables (%_releasecomment%,
%_absolutetracknumber%, %_primaryreleasetype%, %_artists_sort% ...); a tag a script
`$delete`s is removed, and so is a compilation flag on a release not by Various Artists, as
Picard does (never a field the profile preserves, a field the tagging level leaves alone, or
on a match below the replace threshold). A profile can list its own scripts
(`picard.enable_tagger_scripts`, `picard.list_of_scripts`). The naming script sees Picard's
hidden variables too, and what a tagger script `$set`s: with the `picard` preset, every track
of an album with one guest-artist track is now named "Artist - Title", as Picard names it
(files named before are renamed once). A custom naming script that fails on an album makes
that album a conflict; one that does not parse is refused when the profile is saved.

**Picard's Options > Tags settings for saving files**: the ID3v2 version (v2.3 or v2.4), the
ID3v2.3 join character, ID3v1 tags written or removed, APEv2 tags removed from MP3 and ID3 tags
from FLAC, an empty FLAC seek table removed - read from Picard.ini or set in the profile (`picard.write_id3v23` ...; the profile
editor has the ID3 choices). Without them each file keeps its ID3 version and ID3v1 tag, as
before. They apply when a file is written, and the preview says so. Undo now puts back an
MP3's own ID3v1 and APEv2 tags and a FLAC file's ID3 tags exactly; before, undo rebuilt an
ID3v1 tag from the restored ID3v2 tag. A WAV file's RIFF INFO now carries every field the file
holds, as Picard writes it, not only the fields that changed. Picard's Options > File Naming
are followed too: non-ASCII characters replaced, spaces made underscores, and the character
that stands for a "/" in a value (checked against Picard's own code).

**Choose the release yourself**, as Picard's "Other versions": `tagmender plan ALBUM --release
LINK` (or `run`), or "Use another release…" in the app's preview, with a MusicBrainz or Discogs
release link or ID. No search is made; the preview says how the matcher scores the release, and
one whose tracks do not fit the files is left unresolved rather than forced.

**Artist folders (twelfth review).** The artist's own art stays in the artist folder whatever
it is called: artist.jpg, fanart, fanart1, backdrop2, logo, banner ... and extrafanart/; a
folder.jpg beside an album's own cover.jpg is the artist's. A disc set ripped into the artist
folder (CD1, CD2) counts as an album there: its folder.jpg is its cover, never the artist photo.
A single in a folder with only one album below it (a release with its remixes) takes its cover
and log with it. An artist.jpg that is a symbolic link is the artist's photo (never written
over: the album was failing). The artist folder's own photo is used without a download wherever
the album is - flat in the artist folder, or being moved out of it (a moved album takes it
along) - and the tracks' picture follows a corrected one also with "embed only"; a picture of
the user's own beside TagMender's is never touched, and a download never replaces a picture
made from the user's photo.

**The artist photo: into the tracks by default; artist.jpg only when asked.** At Enthusiast
and Audiophile the artist's photo still goes into each track by default, but it is no longer
saved as artist.jpg in the artist folder unless the profile says so (profile editor: "Also save
it as artist.jpg in the artist folder"; `artwork.artist.file: "artist.jpg"`). Your own
artist.jpg is used as before. Saving a profile in the desktop app keeps the photo's own file
name (artist.jpeg, folder.jpg ...).

**A first run on a real library (twenty-second and twenty-third reviews).** A playlist or cue
sheet in Latin-1 naming tracks whose names are Latin-1 bytes is updated when they are renamed
(in UTF-8 when the new names need it, as they are on disk); in a folder shared with other music (Singles) each moving track takes its own .lrc along; an
undo cut short just after it put back a rewritten cover or cue sheet finishes when run again;
a track that is a symbolic link is never moved or written through (the plan says so; a copy
profile copies it); `tagmender recover` now says to plan the album again (naming it), not to
apply the same plan, which a job never runs twice. Turning "Also save it as artist.jpg" on after a run without it saves
your own artist photo there (for a moved album, the one in the folder it came from), not a
download.

**The tools beside the jobs (twenty-seventh review).** ReplayGain and lyrics (`enrich.py`)
and CoverUp's batch covers are written through a staging copy, as jobs are: a crash or power
cut never leaves a track half rewritten. CoverUp's batch keeps each track's tags before it
writes, on disk: a crash loses no cover, and its undo puts the track back byte for byte (an
ID3v2.3 tag stays v2.3). Their undo leaves a track or .lrc edited since alone and says so
(--force puts it back anyway). An exclusions.json that is not valid JSON (a stray comma) is
reported, never replaced with the default rules. `jobs prune` keeps the backups of a job whose
undo was cut short or had problems.

**Twenty-eighth review.** An album whose FLAC/Ogg track numbers hold their total ("5/9", as
ffmpeg leaves them) is tagged again (it failed on every run); enrich and CoverUp's batch never
write through a symbolic link, take TagMender's file lock and check the track is still the one
they copied before it is replaced; enrich keeps an ID3v2.3 tag v2.3 and an undo after a cut-short
run removes the .lrc files it made; learn_rules never writes over a rules.json it cannot read;
the watcher keeps two loose albums you undid undone. A profile saved before this release keeps
Windows-safe names and removes empty folders as before, and one saved now keeps what it says;
only a profile that never says follows Picard's settings.

**Twenty-ninth review.** enrich writes an album's tracks one by one and records each as it
goes: one it cannot write (a link, a track a job holds) is skipped and said so, and its undo
never touches a track it did not write; an .lrc is written through a temp file that is never
left behind, once even for a track in two formats. Profiles saved by the previous release are
read as it read them. A total of 0 never removes a preserved or locked total. Marking albums
unresolved, writing album IDs and enrich keep an MP3's ID3v2.3 tag as it is; web tagging
keeps its version. Undo of a run that wrote through a symbolic link puts back the file it points
to. Opus covers an earlier version wrote are repaired on the next run. One license URL goes
into WCOP (a Wayback Machine link too), as Picard writes it.

**Tags other taggers left (twenty-ninth review, Picard parity).** An M4A with a compilation,
gapless or podcast flag is tagged again (it failed the album, TagMender's own Various Artists
M4As on a second run too). A user tag spelled like a field the file keeps elsewhere
(TXXX:TOTALTRACKS beside TRCK, as ffmpeg leaves a converted FLAC's tags) is read as a tag of
its own, as Picard reads it, instead of failing the album; writing the album artist's sort
drops an old TXXX:ALBUMARTISTSORT, as Picard does. Picard's own TXXX names are written as
Picard spells them. A FLAC or Ogg total inside "5/9" counts as none and one under one name gets
both, as Picard writes them; a preserved total beats a script's $unset. A naming script's
%MyTag% names files alike on every run. $unset(comment) beside a described comment in the file
removes the plain one.

**Thirtieth review.** ffmpeg-converted files get Picard's own frames: the totals in TRCK/TPOS,
the MusicBrainz IDs, release type and dates under Picard's names (a TXXX:TOTALTRACKS or
TXXX:MUSICBRAINZ_ALBUMID beside them is a tag of its own, which a tagger script can read or
`$delete` by its name); removing such a field no longer fails the album. A FLAC or Ogg "5/9"
deleted by a script no longer brings its old total back, and names no disc from it. A
WavPack/APE album whose APEv2 tag is 131 bytes is no longer failed as "audio data differed";
an APEv2 ALBUMARTIST or RELEASETYPE item is updated where it is. UNSYNCEDLYRICS is read as the
lyrics and a WAV's RIFF INFO as its tags where its ID3 tag has none, as Picard reads them.
Marking unresolved, writing album IDs and enrich keep an MP3's ID3v1 tag byte for byte (it was
merged into the ID3v2 tag). The watcher leaves alone albums undone with the previous version.
The Opus cover repair also reaches files already in place, and a Vorbis total under one name
gets the other only when the file is written anyway. TXXX:ALBUMARTISTSORT, :COMPOSERSORT,
:Artists and :Work move to Picard's frames when the file is written; a TXXX beside the field's
own frame no longer hides the field from scripts and naming.

**Picard parity (twenty-eighth review).** Opus covers carry no size, as Picard writes them
(players built on libopusfile showed none); a FLAC or Ogg total is written under both names
whenever its number or total is (TRACKTOTAL alone gets TOTALTRACKS); the "picard" naming preset
is Picard 3's default script (10+ disc sets "01-01"); a preserved total stays when a script
unsets it; $delete(ENCODER) works outside ID3; several licenses go into TXXX:LICENSE in ID3v2.3;
an unset plain comment goes beside a described one; an M4A total 0 is written; number and
picture frames are written in Latin-1, as Picard writes them. docs/PICARD_COVERAGE.md now says
where cover art and the files that go with an album follow TagMender's profile, not Picard.

**More of Picard's settings followed (twenty-seventh review).** Windows compatibility, Delete
empty directories and Preserve timestamps follow Picard's settings (its option profiles too)
unless the profile sets them; the preferred release countries are read from the Picard.ini
TagMender uses (one country, option profiles); a profile option ticked without a value no
longer hides the profiles. After a script removes or unsets a disc number or total, files are
named as Picard names them; an M4A's or WavPack's movement total is kept; a FLAC or Ogg total
not changing is left as it is. Covers embedded into FLAC and Ogg record their size.

**Watched folders and Picard.ini (twenty-sixth review).** "Process anyway" in the app lets
the album's own files through, never the folder they lie in (for loose tracks in a watched
folder, that switched the rules off for everything dropped there later); an album you undid
stays undone when other music is dropped beside it; loose tracks still being copied into a
watched folder hold the others back. Picard 3's enabled option profiles are read, as Picard
applies them over its settings; translation script exceptions apply; "1"/"yes" count as true.
$delete(discnumber) removes it again (a regression); $unset(totaltracks) writes the number
alone; a compilation's genres come from the recordings' artists; a license on the release and
the recording is written twice, as Picard writes it.

**Naming and scripts as Picard 3 (twenty-fourth review).** With Windows compatibility on and
Picard's "Windows long path support" off (its default off Windows), a path below the
destination's mount point is shortened to Windows' 259 characters exactly as Picard shortens
it (`picard.windows_long_paths: true` turns it off). The naming script sees every field of the
file, those its format has no place for too (a WMA's total tracks, an M4A's "A1"), and names
as they are spelled (%_Shelf%, %MyTag%); a "./" folder in it is dropped. Tagger scripts see a
file's own ID3 user tags as they are spelled (%CUSTOM%; $delete(custom) leaves CUSTOM). A
track or disc number a script writes as "N/total" no longer fails the album; M4A track 0 is
written; ID3v2.3 credits keep their order; a WAV's RIFF INFO leaves out what a script unset.
When the shortening would cut an album's folders differently for its discs, all its tracks get
the same (most shortened) folders - one album, one folder; copies made under the longer names
an earlier version gave are found, not made again; a name an earlier run suffixed (" ~1a2b")
stays as it is. Jobs an earlier version ran on M4A files numbered 0 are still undone. A
destination too deep for Windows' path length blocks the album and says why (it used to stop
the whole plan, or move it with longer paths). A script that removes the disc number of a
single-disc release ($unset(discnumber)) no longer fails the album in MP3, M4A, WavPack or WMA
(the lone total is not written, as Picard drops it); a website that is not a URL is not
written into MP3 (as Picard).

**Tagger scripts and naming, closer to Picard (twenty-third review).** In M4A files numbers
are written as Picard writes them (a script's "01" is track 1; a track number that is not a
number, as a vinyl side "A1", is left as the file has it) - such a script no longer fails the
album; show, show sort and podcast URL go into M4A's own atoms. In WavPack and other APEv2
files, a script's "year" is written into the Year item beside the date (it used to wipe the
date and fail the album), and the fields Picard never writes there (gapless, podcast, show ...)
are not written. A custom tag a script makes keeps its spelling in MP3, WAV and M4A
(TXXX:MyTag). With ID3v2.3 and ID3v1, the ID3v1 tag takes the first of several artists or
genres (genre Rock, not none). A naming script that gives no file name ("%albumartist%/%album%/")
keeps each file's own name in that folder, and a long title with few spaces is shortened inside
a word, never down to the track number alone.

**Comments and lyrics as Picard 3 names them (nineteenth and twentieth reviews).** A
comment or lyrics with a description or language (from a tagger script, or already in the
file) is read and written as Picard 3 does: in ID3 the COMM/USLT frame of that language and
description ("comment::ripper", "comment:deu", "lyrics:eng:Verse"; iTunes' own comments as
iTunes writes them), in Vorbis and APEv2 a COMMENT "text (description)" - only that one
replaced, the others kept. Described lyrics count as lyrics the file has. A job made by an
earlier version is still undone without --force, though tags are now read this way.

**Undo, byte for byte (seventeenth and eighteenth reviews).** A job's backup now keeps an
MP3's ID3v2 tag and a FLAC file's whole metadata exactly as they were (a FLAC file's is put
back whole only when its stream info, seek table and cue sheet are as they were - a cue sheet
added since stays), M4A tags with their room, and Ogg and Opus comments in their order and
spelling with their padding: undo gives back the very same file
(before, tags came back with frames reordered, Vorbis names in lower case, padding grown, an
empty seek table or comment block moved or added). Jobs recorded by any earlier version are
still recognised and undone as they were recorded. Backup data is written safely and
checked before use: damaged data is never written into a file. A fragmented MP4 (as some
streaming downloads are) is never written to - by tagging, covers, ReplayGain, lyrics or
CoverUp - since the tool TagMender writes MP4 tags with cannot keep its fragments' audio
offsets; it still goes with its album, as it is, and the plan says so. Known limits: an M4A
whose tag had no room comes back 8 bytes larger (an empty "free" atom), its tags in mutagen's
order; one with no tags at all and the audio first ("fast start") keeps an empty tag; and an
Ogg file whose comments hold text that is not UTF-8 is put back with the same tags but not
byte for byte (an Ogg or Opus file's header pages are otherwise put back as they were laid
out, even when an embedded cover made its comments span many pages). A job recorded by an earlier version that kept an M4A's
whole moov is never put back over a file rewritten since (its audio moved): it says so. Mood and release date can be removed or
changed in every kind of ID3 tag. Tagger scripts see every field of the release at the
Basic level too, never a field the release deletes, keep the whitespace of the file's own
fields, decide the album's edition, and a script that fails on a file stops only that album.
%_format% is Picard's (e.g. "MPEG-1 Layer 3 - ID3v2.4"), and %_multiartist% is set for
albums identified on the web as well.

**Undo, exactly (sixteenth review).** An MP3 with no tags, or only an APEv2 tag, is put back
as it was (before, undo added an empty ID3v2 tag, kept an ID3v1 tag the job added, or lost the
APEv2 tag); an ID3v2.2 tag comes back as v2.2; jobs recorded by earlier versions are still
undone exactly. A mood or release date survives a change of ID3 version. Your own artist
photo, changed after the job that put it in place of a download, is kept by undo and never
holds the album back.

**Artist photos and covers when albums move (fifteenth review).** Your own artist photo now
replaces an artist.jpg TagMender downloaded earlier (the download is kept for undo) - also
when the albums move over several runs, or a copy profile made the copies with a download -
and your artist.png wins over a downloaded artist.jpg beside it. Artist folders named by
sort name ("Bowie, David") are recognised. An album moved out of its artist folder gets a
cover where it lands (the artist's pictures that stay behind are no cover of it). poster.jpg,
art.jpg and thumb.jpg (Plex, Jellyfin) stay in an artist folder like folder.jpg.
`tagmender artist-photos --forget` no longer forgets which artist.jpg files were downloads.

**Artist photo: only artist.jpg goes into the tracks (thirteenth review).** A folder.jpg in an
artist folder can be an album's cover as well as the artist's image (Jellyfin, Kodi), so it is
never embedded any more: it only means the artist has an image (nothing is looked up or
written beside it) unless it is the cover of an album lying in that folder (the same picture
as its cover.jpg, or its only cover), and then it moves with that album. artist.webp/.png/...
count; "R.E.M." folders are recognised; an artist's only album ripped flat into its folder,
right below the library, uses the artist.jpg there. An artist.jpg TagMender downloaded is
never taken for the user's word: a picture made from the user's photo is not replaced by it.
No lookup for an album that gets no front cover (the photo would never go into it), and
cover.webp, .gif and .bmp move with their albums.

**ID3v2.3, credits and Picard.ini (thirteenth review).** What TagMender plans is now what the
file can hold, as it will hold it: in an ID3v2.3 tag several values are joined with "/", the
original date is a year, and a date without its day is a year - so v2.3 albums no longer fail
with "did not read back as written" (several artists or release types, a full original date)
and their plans settle. A v2.3 tag's other dates are kept as they are when only another field
changes, embedding a cover keeps the tag valid v2.3, and undo puts a v2.3 tag back byte for
byte. Instrument names keep their case ("Hammond organ", as Picard). Release-group credits
come before the release's, and discs and track totals are numbered as Picard numbers them (a
CD + DVD + CD release's second CD is disc 3 of 3). WAV files get the RIFF INFO chunk Picard
writes (title, artist, album ... for Explorer and Audacity). Picard.ini values are read as
Qt writes them: a list of one item (one translation locale, one preferred country, one
preserved tag), quoted text, and Picard 2's names. The app frees what each background job
used (a watched-folders window open for days grew).

**Fourteenth review.** A release chosen with "Use another release…" applies to that album only,
never to the folder's other albums or loose tracks (`--release` refuses a folder holding several).
An ID3v2.3 MP3 copied or moved is named from its values ("A & B", not "A_B"); writing one field
keeps the other frames' values as they were stored (several values are joined only for what is
written, as Picard joins them); undo restores a v2.3 tag's several values exactly; two licence
URLs go to TXXX:LICENSE, never a made-up WCOP. A performer credit an earlier TagMender wrote with
a lower-case role ("hammond organ") is replaced, not doubled. WAV RIFF INFO gets the values as
they are ("Rock, Pop", "2001-05"). Undo keeps an M4A freeform atom's type.
Known limitation: when an artist's last album moves out of an artist folder, the folder's own
art (fanart, logo, extrafanart/) stays there; only artist.jpg is made at the destination.

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
- An interrupted album is put back as it was by `tagmender recover`; plan it again to do it.
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
