# Release notes

## 0.8.45

**A second run confirms the first (REP-1)**
- An album TagMender tagged before is planned again on what your copy showed before it was
  tagged: the catalogue number, country, date and edition TagMender wrote - and its own folder
  name - are no longer read back as new evidence, so a second run keeps the same pressing (or
  corrects it the way the first run should have) instead of switching to another one. Your own
  later edits still count, and values another tagger wrote keep their weaker standing.

**Your barcode over a club edition (BAR-9)**
- A MusicBrainz release without a barcode (a record club's edition) gives way when the Discogs
  release it links to carries another barcode and Discogs has the pressing with yours: a disc ID
  proves the master, not which pressing of it you own.

**Safer matching**
- A release listing no track lengths is no longer taken on its name alone: half its titles must
  be your files' (LEN-2).
- A single disc whose media tag says "Stereo" is not a version folder; a lone multichannel or
  5.1 disc still is (MIX-2).
- "[Remastered] [1999 Remaster]" says it once: "[1999 Remaster]" (ED-6).

**AppImage**
- Scan OCR (tesseract, with English, Japanese and Russian) and barcode reading (zbar) are
  bundled, built for glibc 2.28 and newer: no system packages needed.

## 0.8.44

**Check what was written: `tagmender check`**
- `tagmender check JOB|PLAN|PATH` reads the tags a job wrote, a plan would leave, or the albums in
  a folder, and lists what looks wrong: a needed tag missing, track numbers repeated or skipped, a
  guest named in the album artist, an exact album without a release ID, an album-only one carrying
  a pressing's details, files left behind in a moved album's old folder. It changes nothing.
  `--json` and `--csv PATH` for scripts and spreadsheets.

## 0.8.43

**Featured guests also in a split credit (NAM-8)**
- A profile that writes a joint credit one value per artist ("Robin Trower; Jack Bruce") still
  files "Robin Trower featuring Jack Bruce" under its lead: the database's credit text, with its
  joins, is passed along unwritten.

## 0.8.42

**Box sets numbered one way (TRK-4)**
- When MusicBrainz and Discogs split the same tracks into discs differently, every file is
  numbered by the one layout your files' own disc and track numbers agree with - never the disc
  from one database and the track from the other.

**Featured guests and lead artists (NAM-8)**
- "Artist featuring Guest", "Artist with Guest", "Artist presents ...": the album is filed under
  its lead artist. Partners credited before a guest keep the whole credit; a database that does not
  answer no longer throws away the other's answer.

**Safer evidence**
- A barcode decides only for an album that fits it - half its lengths or its titles, or nothing
  to compare (BAR-7); lengths still never stop a barcode when the titles agree.
- A catalogue number on your scans counts only when it stands alone - never as the start of a
  longer printed number such as "SICP 10151~4" or "ECM 1064/65" (BAR-8).
- A release listing your tracks in your files' own order wins a tie against one in another order;
  one track moved is counted as one (TRK-2).
- A 2-CD Discogs release numbered "CD-1" ... "CD-12" stays two discs (TRK-3); a work's movements
  are merged only when none has a length of its own (TRK-1).

**Folders and copies**
- "M.I.A." and "MIA." are two artists; "Mr. Big" and "Mr Big" are still one folder (NAM-7).
- A second copy is held apart only when it brings no disc the filed copy lacks; disc numbers
  "01" and "1" are the same (MOV-4).

**Wikipedia**
- Bonus sections go on the disc they continue, a deluxe edition's bonus disc leaves a standard
  edition beside it, nested and display templates read right (WEB-2).

**AppImage**
- fpcalc (AcoustID fingerprints) is bundled: Chromaprint's own static 1.5.1 release.

## 0.8.41

**Track numbers from Discogs read right (TRK-3)**
- A release numbered by its medium ("CD-1" ... "CD-11", "DVD") keeps its bonus track's number
  (11, not 1 of a second disc), and the parts of a work listed under a heading without a number
  of its own ("2-3" ... "2-6") stay on their own disc.

**A second copy stays apart (MOV-4)**
- A second copy of an album already in the library - two rips of one release, or a new rip of one
  you have - is tagged where it is, with a note, instead of being put beside the first under
  "~xxxx" names.

## 0.8.40

**Collaborations keep every name (NAM-8)**
- An album credited to equals - two bands ("Calexico and Iron & Wine"), several musicians ("Jon
  Balke, Per Jørgensen, Audun Kleive"), a duo named after its members ("Jimmy Page & Robert
  Plant") - keeps the whole credit as album artist and folder. Only a leader credited with their
  own band ("Thomas Strønen & Time Is A Blind Guide") is filed under the leader, as before.

**Wikipedia track listings read right (WEB-2)**
- Linked titles are written as their text (never "[[Night Stalkers (song)"), and an article's
  bonus-track sections are other editions of the album, not discs 2 and 3; an LP's "Side two"
  still continues the album.

## 0.8.39

**Your own number decides over names (BAR-8)**
- When your barcode proves the release (and the album fits: half its lengths or track titles),
  a title another tagger wrote otherwise ("UFO/ Hinten") or a version your folder does not name
  (a live album) no longer holds it back.
- A catalogue number printed on your scans or booklet counts as your own number, as one in your
  folder name does (never one only typed in notes or a saved page).

**One disc entered several times (TIE-6)**
- MusicBrainz entries of one CD with your barcode and your disc's exact disc ID, whose label and
  catalogue number are only written differently, are one pressing: the album is tagged in full
  instead of with album-level tags only.

## 0.8.38

**Nothing left behind, also for short titles (MOV-3)**
- An album folder named "2016 - 3 [SACD ...]" is recognised as album "3"'s own folder although
  the naming script tags it "3 [HD]": its checksum files go with the album. A folder whose name
  merely contains the word ("Music 2020", "Vol 3") keeps its other files.

## 0.8.37

**Pressings your barcode and scans name (EVI-5, EVI-6, TIE-4)**
- A hi-res download carrying the CD's barcode is tagged as that CD pressing (your barcode names
  the release): what the audio says against every CD alike no longer stops the choice.
- When your scans' catalogue number is on some of the tied pressings only, your country order
  chooses among those, never the others.
- A bootleg never wins a tie-break against an official release that fits as well, also when it
  lists its lengths more exactly; one you chose yourself stays.

**Filed on your own number (FIL-2)**
- An album your folder's, tags' or scans' catalogue number or barcode proves is filed even when
  the release lists no track lengths (its score is low only for the lengths it cannot compare).

**Nothing left behind (MOV-3)**
- An album folder you open on its own (or that the watcher re-checks) now gives up every file,
  its .md5 and .sfv too, when the folder is named after the album; a folder of another name keeps
  its other files.

## 0.8.36

**A work's movements are one track (TRK-1)**
- A suite Discogs lists with its movements under it ("Symphony For The Devil" 7:49, then 7.I,
  7.II, 7.III without lengths) is matched and tagged as the one track it is on the disc. Before,
  each movement counted as a track and the album was held for "track count differs".

**Your files in another running order (TRK-2)**
- When your files are the release's own tracks numbered in another order (an SACD rip whose 001
  is the release's track 11), each file is matched, tagged and numbered as its own track, by its
  title and length. A few tracks moved (bonus tracks in another order) still means another
  edition, as before.

## 0.8.35

**SACD rips are found as SACDs (MED-13)**
- Hi-res files ripped from an SACD (converted to 24/96) are recognised by "SACD" or "DSD64" in the
  folder's name or by the SACD ripping tool's log in the folder, which stays when the folder is
  renamed. Discogs is then searched for the SACD itself (it files SACDs under their own format,
  not CD), and an SACD found is always looked at, however many LPs of the album's year rank first.

## 0.8.34

**Your barcode decides (BAR-6, BAR-7)**
- An album whose own barcode is on the release is no longer held back by track lengths that
  disagree (a remaster, lengths printed otherwise) or by the lower score they cause.
- A track total in your files' tags that counts something else (the DVD in the set, another
  edition) no longer holds back an album your barcode proves, when the release holds exactly your
  folder's tracks.

**Runner-ups your copy rules out (TIE-5)**
- A bootleg already set aside, or a vinyl or download release when your files are a CD rip
  (whole CD sectors, a rip log), no longer keeps the CD you have in review.
- An album whose pressing alone is open is tagged as Album only when most of its track lengths
  agree, as an automatic pick needs, not only when every one does (ALB-3).

**One artist folder (NAM-7)**
- "Blood, Sweat & Tears", "Blood, Sweat And Tears" and "Blood Sweat & Tears": albums are filed
  into the artist folder the library already keeps under one of these spellings (the one holding
  the most), instead of a folder for each database's spelling.

## 0.8.33

**One artist folder, whatever the apostrophe (NAM-6)**
- MusicBrainz writes "King’s X", Discogs "King's X": albums are now filed into the folder already
  on disk instead of a second, look-alike artist folder beside it.

**One disc of a set the database lists without lengths (BOX-3)**
- A disc ripped alone ("CD 1" of a 2CD set) whose release has no track lengths is tagged and filed
  as that disc of the set when your own barcode is the release's and every track title of exactly
  one disc is yours, in order. Before, it was left Unresolved for its track count.

## 0.8.32

**Box sets whose discs carry their own titles (DSC-2)**
- A set filed as "CD1" ... "CD4" folders, each disc tagged with a title of its own (the remix,
  the live show, the outtakes), is now matched and filed as one release. Before, each disc was
  an album of its own and one could be filed away from the others.

**Network shares that ignore letter case (NAM-5)**
- On an SMB share, a folder the naming script spells "Best of" where "Best Of" already exists is
  used as it is spelled on disk, instead of every track getting a "~1a2b" name beside itself.

**Leftover folders (MOV-2)**
- A second copy of one of the album's tracks (the same audio under another name) no longer keeps
  the album's scans, log and cover behind in the old folder: they move with the album, and the
  copy stays where it is with its own sidecar files.

**Fewer wrong picks**
- A concert DVD or Blu-ray release (songs as chapters, no audio disc) is no longer chosen for a
  CD rip: tracks of whole CD sectors or a CD rip log argue against it (MED-12).
- An SACD's audio format in the album title ("DSD64", "DST64", "DXD", "2ch") is no longer read as
  a catalogue number (CAT-1).

## 0.8.31

**Albums your copy proves are filed (FIL-1)**
- An album whose own catalogue number or barcode is on the release, or whose disc ID chose the
  pressing, with every track length agreeing, is now filed into the library even when its score
  is under the profile's threshold (a long folder title, other pressings the disc ID ruled out).
  A pressing picked only by your country order is never filed this way.

**One disc of a set in its own folder (BOX-2)**
- A folder holding one disc of a 2-CD set ("Volume One", "Volume Two" apart) is tagged with that
  disc's tracks and filed as disc 1 or 2 of the set, instead of being left Unresolved.

**Bootlegs and folder names**
- An unofficial release (a bootleg CD-R) no longer wins a tie against the official one unless
  your copy points to it (TIE-4).
- Folder names never keep a bracket the five-word limit cut open (NAM-4): "Gold (Greatest Hits)
  [40th Anniversary [2014 Reissue]" is now "Gold (Greatest Hits) [2014 Reissue]". Albums already
  filed under such a name are renamed the next time they are applied.

**Faster look-ups**
- After MusicBrainz says it is busy, TagMender returns to full speed much sooner (SPD-1).

## 0.8.30

**Every file moves with the album (MOV-1)**
- An identified album that is moved or copied now takes every file in its own folders with it,
  whatever the file type (an .auCDtect report, a .toc, an .nfo), so the folder it leaves is empty
  and removed. Folders shared with other music, artist folders and loose tracks in a watched or
  downloads folder keep their own files, as before.

**Apply while albums are looked up (APL-1)**
- Apply during a Look Up no longer stops the look-up: the ready albums are applied beside it, so
  the disks and the databases work at once. Only when an album to apply shares a folder with one
  still being looked up does the look-up stop first.
- Clear Done works while a look-up or Apply runs, and a cover or tag can be edited for any album
  the running work is not busy with (before, the choice was silently dropped).

**Better matching**
- Sleeve times (LEN-1): a release that lists every track a few seconds short of your files is
  taken as agreeing on lengths.
- A DVD in a CD set (MED-11) no longer counts as a missing disc of your CD rip.

## 0.8.29

**Done as soon as it is done**
- While Apply runs, each album's row in the Overview turns to Done (or Failed) the moment its
  last file is tagged and moved, instead of staying Ready until the whole job ends.

**Build**
- A timing test that failed on a busy build machine (the 0.8.28 packages were not published)
  no longer depends on a single run's time.

## 0.8.28

**Your rip log's disc ID proves the album (TOC-1)**
- When your rip log's table of contents fits your files exactly and MusicBrainz has that disc ID
  on the release, the album is identified and filed even if the database lists slightly
  different track lengths, or your folder names the artist differently (a band member instead
  of the band). The album title must still agree, and the track titles too when the artist differs.

**The scan investigation keeps to its time limit (OCR-1)**
- The last look at every scan before an album is left Unresolved now stops at its time limit
  inside a scan too. Grainy photographs read sideways could keep it going far longer.

## 0.8.27

**Albums the watched folders could not identify are never out of sight (HF-8)**
- Needs attention now lists the albums your watched folders left Unresolved, also when the
  profile's Unresolved setting is "leave" and nothing was written to their files. With no folder
  chosen there, it lists them wherever they are, the sidebar badge counts them, and a
  notification tells you when there are new ones while TagMender is open.
- After each TagMender update, every album a watched folder left Unresolved is looked up once
  more, whatever its retry schedule (not when Retry is set to Never). New albums still go first.
- `tagmender unresolved` and `tagmender retry` find these albums too.

## 0.8.26

**Barcodes close to a dark edge are read**
- A barcode whose white label ends a few bar widths after the bars (a dark spine or picture
  right beside it) is now decoded from the scan. Before, it was missed, the scans could not prove
  the release, and an album whose sleeve track lengths differ from the disc was tagged in place
  instead of filed.
- Scans where an earlier version decoded no barcode are decoded once more; barcodes already
  decoded are kept.

## 0.8.25

**A missing track on a set numbered straight through (HF-5)**
- A 2-CD set whose second disc is numbered 19-36 instead of 1-18, with a track missing from the
  first disc, is now tagged as its release (each file as its own track, the gap noted) instead
  of being left Unresolved.

## 0.8.24

**Web search, the last resort (WEB-2)**
- With a web search engine set up for a profile (Mojeek, Marginalia or Tavily), an album that
  MusicBrainz, Discogs, your scans, the shops and gnudb could not establish is searched once by
  its artist and title before it is left Unresolved.
- Only links in the results are used: MusicBrainz and Discogs release pages are fetched and
  checked against your files like any other match, and Bandcamp album pages can give Partial
  tags. Nothing is tagged from the text of a search result.
- Marginalia's shared "public" key often reaches its daily limit; a key of your own is more
  reliable.
- Fixed: a band whose name ends in "X" (Brand X, Malcolm X) was searched as "Brand" on the
  web sources.

**Albums proven by your scans**
- A release whose barcode or catalogue number is printed on your scans, with nothing on them
  against it, is now filed at a score of 90 or more even when some track lengths differ, as a
  vinyl rip's do - at least half of them must agree (BAR-1b).
- A CD sleeve that came with a download no longer makes the download "another pressing": a
  download's entry without a catalogue number is unknown, not different (EVI-2).
- The scans' tooltip in the preview now points to Album › Change Release; Needs attention lists
  only albums already marked Unresolved.

## 0.8.23

**The web is searched next, also with Unresolved set to "leave" (WEB-1)**
- When neither MusicBrainz nor Discogs establishes an album, TagMender now always searches the
  web next. With Unresolved set to "leave", what it finds is held: the album shows as HELD in the
  preview, unticked, and nothing is written unless you tick it and apply. A watched folder never
  applies a held album and keeps looking it up on the Unresolved schedule.

**gnudb can name an album no database lists (GNU-1)**
- When gnudb holds an exact match for your CD's table of contents - the same track count, every
  track length agreeing and your track titles agreeing - the album gets Partial tags from it:
  album, artists, track titles, genre and year, with the gnudb page as its source. No release ID,
  label, barcode, catalogue number or media is written from it. A near match stays a clue only.

**Your disc ID is no longer outvoted by an old country tag (EVI-2)**
- A release country in your files' tags that none of the matching pressings has (written by
  another tagger: "GB" on an album released only in Germany and Russia) counted against every
  pressing alike, so even your exact disc ID could not choose one and the album stayed Album
  only. It now tells nothing apart: the disc ID decides, or else your country order.

**More pressings told apart**
- Discogs's "UK" pressings now count as GB in your country order; before, a UK pressing was
  never picked by it.
- "Made in the EU" on your scans no longer counts against a UK or German pressing.
- The matrix printed round a disc's hub is read on more scans (a wider ring), and a CD-R entry
  is not taken for your pressed disc when another tied pressing lists that matrix. Disc scans
  are read again once for this.
- Lossless files are never a download sold only as MP3: of a FLAC and an MP3 entry of one
  download, the FLAC one is picked (MED-10).

## 0.8.22

**Folder names that did not change**
- An album proven by your own copy - your scans' barcode or catalogue number, every track
  length agreeing - now has its tags replaced in full even on a low score, as it was already
  filed. Its old album title ("Mercurial Balm [ECM 2269]") no longer survives into the new
  folder name ("2012 - Mercurial Balm [HD]").
- "[ECM 24-96]", "[24/192]" and other bit-depth and sample-rate notes in a folder name or album
  title are no longer taken for a catalogue number. Taken for one, it counted against every
  pressing and left the album Unresolved (Quiet Inlet).
- A band credited after its members ("Thomas Strønen / Iain Ballamy : Food") is the album
  artist, not its first member, so the band's albums stay in one folder (NAM-1).

**Faster checks**
- The test suite runs in a few minutes and never reaches the network; packages are built while
  the tests run, and published only when every test passes.

## 0.8.21

**Matching rules, written down**
- docs/MATCHING_RULES.md lists every rule TagMender follows to choose a release, tag it and file
  it, each with an ID the code and tests refer to. Everything below follows it.

**Your barcode is the release (BAR-2, BAR-3, BAR-5)**
- Your barcode on one database's pick wins over the other database's sure pick, whichever you
  prefer in Sources. When both picks carry it, the sure one is taken (an SACD whose CD-listed
  twin cannot hold your 24-bit audio).
- Two barcodes of yours naming two releases: the one on the back cover, box or slipcase wins.
- A barcode decoded off your scans beats a different one in your files' tags.

**Evidence (EVI-1, EVI-2, EVI-4)**
- A catalogue number counts for more than a disc ID or matrix code.
- A catalogue number on your scans no longer tells a pressing apart from a download entry that
  lists none (its booklet shows the disc's number).
- Scans that read badly never lower the score; they only add evidence.

**Nothing identified waits (TIE-1, TIE-2, HF-2, ALB-1)**
- When nothing of yours tells pressings apart, your country order picks one (the original date
  only within a country) and it is tagged in full. "Album known" is left for a copy whose kind
  (CD, download, vinyl) is not among them.
- When two pieces of your own evidence disagree, your preferred database's pick is taken.
- Every track title matching with at most one track length off is tagged as that release.

**Your choices stand (TAG-1, CHG-1)**
- A release you chose, or TagMender tagged, stays unless a barcode or catalogue number of your
  copy names another; a better score alone does not move it.

**SACD and hi-res (MED-3, MED-4)**
- 88.2 or 176.4 kHz audio, DSD files and SACD area tags point to the SACD.
- 96 or 192 kHz audio, with both an SACD or Blu-ray and a download listed and nothing else
  deciding, is filed as "album known".

**Edition suffixes (ED-1 to ED-5)**
- SACD and vinyl or tape rips are always marked with the pressing's year: "[1999 SACD]",
  "[1977 Vinyl Rip]". A CD or download only as a newer remaster or reissue: "[2015 Remaster]".
  The original CD or download gets none, so no more plain "[WEB]".
- Hi-res audio adds HD: "[HD]", "[2015 Remaster HD]".
- The same text in the folder name, the Album tag and the exported tagger script; naming
  scripts with their own edition lines get it too.

**Watched folders (HF-3, HF-4, DSC-1)**
- An Unresolved album is looked up again every 10 minutes on its first day, then daily for a
  week. New albums always go first.
- When your preferred database is down, the album waits and is retried until it answers, never
  decided from the other one alone.
- Disc folders of one release ("Album CD1", "Album CD2") are joined into one album, and a
  watched folder waits for a missing disc.

**Tags (NAM-1, BOOT-1, CLS-1, MIX-1, HF-5, MED-6, ART-1)**
- The album artist is the lead artist by default ("A & Band" is filed under A). This also
  applies to existing profiles that never set it; switch it off in the profile to keep the full
  credit.
- A Discogs bootleg is marked with release status bootleg.
- Classical: Movement, movement number and total from MusicBrainz.
- Two versions of one release in a folder get a disc subtitle such as "SACD Stereo".
- Missing tracks are noted in the report, no longer as a TAGMENDER_MISSING tag.
- Grouping says Tape Rip for a cassette rip.
- A cover of yours under 500 px is replaced by a larger one, also in existing profiles that
  never set Artwork > Replace; set it to Never to keep every cover as it is.

**Fixes**
- An artist folder you opened inside your library is removed once a move empties it.
- Clear Done is a real button below the list, not flat text that was easy to miss.

## 0.8.20

**No vinyl or cassette without evidence**
- When a CD of the album is among pressings that tie, and nothing about your copy points to vinyl
  or cassette, those are set aside and the copy is taken for a CD. They stay listed in Change
  Release. A folder name, your tags, a number of yours, or lengths only they fit keep them in.

**One TagMender**
- Starting TagMender again brings the open window forward instead of opening another, which also
  stopped watched folders being checked.

**Filing without asking**
- An album a database knows but cannot be matched to (a promo, a sampler), whose files already
  name their artist and album, is filed as "album known". No database field is written.
- An album whose scans name another pressing, with nothing of yours on the one picked (no barcode,
  catalogue number or disc ID), is tagged as "album known" rather than as that pressing.
- Watched folders can look up filed albums again after an update (off unless you tick it): a few
  at a time, only albums unchanged since they were filed, and only a proven release is applied.
  Profiles that copy are left out, as a copy would file the album twice.

**HD audio and vinyl rips**
- Grouping now says **HD Audio** for lossless audio above 16 bits or 44.1 kHz, DSD included, or
  **Vinyl Rip** for a vinyl rip, so a player can filter on it. Only when Grouping is empty; it can
  be switched off in the profile.

## 0.8.19

**Your barcode is the release**
- A barcode read off your copy now decides the release, whatever the audio was ripped to: a hi-res
  rip of a disc, or tracks that are not whole CD sectors, no longer send it for review or lose it
  to a listing of another format.
- When several tied pressings carry that barcode and nothing else tells them apart, the audio
  chooses between them: a hi-res rip goes to the hybrid SACD rather than the CD with the same
  barcode.

**Look-ups**
- A database answering "temporarily unavailable" no longer ends the look-up of an album when the
  window was started from a terminal or program that has since closed.

**Overview**
- Apply carries out only the rows you highlighted, even when it is just one, however many others
  are Ready. With nothing highlighted (Escape clears it), Apply carries out every ticked album.
  The first row, highlighted by itself after a look-up, does not count.

**Rip logs**
- A remixer or other words in a ripped track's file name, as a rip log lists it, are no longer
  read as a catalogue number. *MC 900 Ft. Jesus* blocked the right release and brought in unrelated
  cassettes.

**Album artist: lead artist only**
- A joint credit from Discogs now follows the setting: *Thomas Strønen, Time Is A Blind Guide* is
  filed as **Thomas Strønen**, and Artist keeps the whole credit. It only worked for MusicBrainz
  before, so one artist's albums could end up in two folders. A name that merely has a comma in
  it, such as *Earth, Wind & Fire*, is left alone.
- A credit written as one value per artist follows it too: *Jon Hassell / Farafina* gave both names
  as the album artist.
- Albums already filed move only when you look them up again and apply.

**Folder names**
- With "Replace directory separators with" left empty, a " / " in a name no longer leaves two
  spaces behind.

**Pressings of another media**
- An album whose only tied listings are of another media than your copy (a download listed only
  on vinyl and CD, a CD rip listed only on vinyl) no longer stays Unresolved. It is tagged as
  "album known", with no media, track numbering, release IDs, catalogue number or barcode, and it
  is filed.

## 0.8.18

**Overview**
- A new Clear Done button takes every album marked Done out of the list at once. Their folders and
  files are not touched, and the albums stay out of the list unless you add those folders again.

**Pressings nothing can prove**
- When Discogs ties between several pressings and MusicBrainz lists only one, that one is no longer
  taken as certain. Unless something about your copy proves it, the album is tagged as "album
  known" with no pressing details, as for any other pressing that cannot be told apart. With
  MusicBrainz preferred in Sources, its pick still decides.

**Reading scans and logs**
- "CDDA 100%" in an audio checker's log is no longer taken for a catalogue number and searched for.
- A barcode on your scans no longer shows as ✗ when the chosen release lists no barcode at all.

**Builds**
- The Flatpak build frees disk space on the build machine first and retries when a download server
  has a brief outage.

## 0.8.17

**Overview: several albums looked up at once**
- A look-up now works on up to three albums at the same time, and each shows "Looking up…" in the
  list. Albums added from different folders run side by side.
- The limits the databases set are shared between them, so MusicBrainz and Discogs get no more
  requests per second than before. Albums waiting mostly on those two gain least; reading files,
  scans and artwork gain most.

**Track lengths no longer needed to match**
- A release that lists no track lengths no longer holds an album back or costs it points.
- Lengths that clearly disagree still send the album to review. So does a pick without lengths when
  the other database has a release whose lengths do match.
- Extra-careful matching still asks for track lengths.

**Report: fewer questions, better choices**
- An album proven some other way, by a barcode on your scans for example, is no longer asked about
  because its release lists no track lengths.
- When every clue has been tried and the pressing still cannot be told, the album is tagged with
  its original date and no pressing's details, and nothing is asked. Before, it asked you to choose.
- Releases your folder name rules out are no longer offered: a folder marked vinyl is not offered
  a CD or a cassette. If none fits, they are all shown.
- Each release offered now starts with its format, country and catalogue number, and your preferred
  database comes first.

**Reading scans**
- Catalogue numbers printed with spaces, such as EMI's "CDP 7 91186 2", are now recognised.

**Look-ups**
- A file that cannot be read for a moment, on a network share for example, now affects only its own
  album. Before, it stopped the whole look-up.

**Watched folders**
- A barcode or catalogue number found on your scans, with every track length agreeing, now files an
  exact match even on a score from 80. The score only looks low when a folder name does not say the
  artist. Extra-careful matching keeps the higher score.
- Fixed: when the terminal or program that started TagMender had gone, a routine "waiting" message
  from a busy database stopped the album's look-up with "Broken pipe".
- Fixed: the rule added in 0.8.16 for filing an exact match scoring at least 90 never took effect,
  so those albums were identified again on every check and stayed in the folder. They are now filed,
  as are exact matches of a release that lists no track lengths.
- When a repeat check finds nothing new and an album still cannot be filed, the log says why.

**Artwork**
- Cover images are written whole and then put in place, so albums looked up together cannot leave a
  half-written file.

## 0.8.16

**Overview: add albums at any time**
- A folder dropped on a list now joins it. Before, dropping one when nothing was running replaced
  the whole list. Add Folder does the same.
- Drop more albums while a look-up runs: each one is in the list at once, marked Waiting, and is
  looked up next. A first folder is listed, then looked up album by album, so there is always a
  list to add to.
- Music dropped on the cover panel is added too, instead of being refused.
- Adding a folder no longer makes the albums already listed read their files again.

**Apply**
- Albums added from different folders can all be applied. Before, only the folder added last was
  allowed and the others failed with "outside the folders this profile may change".
- The report offers other releases only where choosing one could help. An album identified for
  certain that failed while being written no longer shows a list of releases.

**Fewer albums left behind**
- An exact match scoring at least 90 is filed when every track length agrees and no other release
  is close. The preview says why. Before, it needed a score of 95 whatever the evidence.
- When artist, title and tracks all match and the only doubt is between entries of that album that
  list no track lengths, the album is tagged with what they share (no release IDs) and filed.
- Extra-careful matching still asks for the higher score in both cases.

**Watched folders**
- With a move profile, an album still in the watched folder after it was handled is looked at on
  every check until it is filed. Before, one held back from moving was never looked at again.
  Unresolved albums keep the schedule set in the profile.
- A check that finds nothing new adds no job and no notification.
- An unresolved album is no longer listed as a job that was done.

## 0.8.15

**Watched folders**
- Process Folder Again now also handles albums you undid. Before, an album whose job you
  undid was never looked at again: it stayed in the watched folder for good, even once a newer
  version would identify it correctly. (The watcher still never redoes an undone album by
  itself; only when you ask.)
- Right-click an album under Recently › Process Again: that one album is identified and
  handled again at once. An album you undid asks first, and its row says how to bring it back.

**Report**
- An album held back because no release fits surely enough (confidence under the threshold)
  now says so: "no release fits surely enough". Before, it said "the artist or title agree only
  partly", even when both agreed in full. Albums looked at before are shown the right way too.
  Needs attention calls it "Not sure enough".

## 0.8.14

**Matching**
- Two discs ripped into one folder without disc tags, each disc's files with its own album
  tag ("Title CD 1" and "Title CD 2", as EAC writes them): each file is now on the disc its
  album tag names. Before, the two discs' tracks 1, 1, 2, 2 ... were taken in name order and
  half the files were given another track's IDs.
- When files share their disc and track number and nothing says which disc each is on, the
  album is left for you to choose instead of being tagged: which file is which track would be
  a guess. A catalogue number no longer lets such an album through.

- A release that plays a piece twice (a suite on one CD: "Gnomus" at track 2 and again at
  track 18, the same length) keeps both: files whose audio differs are two tracks. Before, the
  second was taken for a copy, left behind with the album's cue, log and scans, and the rest moved.
- Lead artist only, with "a band named after a member": now for albums tagged from MusicBrainz
  or a shop page too. When MusicBrainz lists no members, the Discogs artist its entry links to is
  asked; a credit without IDs is found on MusicBrainz by its exact name (only when one artist
  has that name). The band must still list that member.

**Window**
- Every dropdown shows its whole list, below the box or above it near the screen's edge; no
  more lists cut off with scroll arrows.
- Search for Release always shows the Format column (CD, Vinyl, File ...).
- Right-click an album that was looked up: Open on MusicBrainz, Open on Discogs (or the page it
  was found on).
- A folder dropped while Apply runs is in the list at once (read alongside Apply), waiting; it
  is looked up when Apply ends. Before, it appeared only after Apply. (A folder Apply is moving
  albums out of or into is still read once Apply has finished.)
- Use This Release (Find Release, Choose Your Pressing) looks up only the album you chose it
  for; before, every album in the list was looked up again.

## 0.8.13

A quality release: fixes found by testing every part of TagMender again.

**Please note: one change in behaviour**
- An album whose folder is a link to a folder outside your profile's folders is no longer
  moved: its files would have been taken out of that outside folder. The preview says why. Add
  that folder to the profile (or drop it on the window) to file it; copying still works.

**Files and undo**
- TrueAudio (.tta) files can be tagged: every write was refused by the audio check. Jobs made by
  earlier versions on .tta files that already had a tag can be undone again.
- When the journal cannot be written for a moment (a full or failing disk), each change already
  made is still put back; before, a file could keep its new tags or a copy stay behind.
- Undo goes on when a folder cannot be made again (read-only or full disk): it says so, and
  running Undo again finishes it.
- Undo of a file whose name is near the length limit works on Python 3.10 and 3.11.
- A custom tag the format cannot store (letters other than ASCII in FLAC, Ogg and Opus,
  `TAG` in WavPack, and so on) is left out of the plan instead of the album
  failing at Apply.
- Cue sheets with file names without quotes, and cue sheets and playlists that write accented
  names in the other Unicode form (as a Mac does), are rewritten when the files are renamed.
- A plugin calling `sys.exit` is switched off instead of ending TagMender; a plugin's empty
  value is no value instead of the text "None".

**Look-ups and settings**
- A server's "try again later" in any form (a date, a fraction) is waited out (at most five
  minutes) instead of the album failing; your AcoustID key is no longer stored in the cache.
- One damaged profile file no longer stops the watcher or the command line: it is reported
  and the other profiles carry on. A damaged value in your tagger's settings file is read as no value.
- `tagmender scan`, `plan`, `run`, `unresolved` and `retry` with a folder that is not there, and
  `tagmender jobs` with an unknown job, end with an error (exit code 1) instead of "nothing".

**Watched folders**
- A file dated in the future (copied from a device with a wrong clock) no longer holds its album
  back as "still changing".
- The timer stays on the clock on a summer-time change day, and starts again at midnight
  (every 9 hours: 00:00, 09:00, 18:00).

**Window**
- An Apply cancelled before its first album says nothing was changed (it offered an Undo).
- Jobs: Filed into lists only the albums the job finished.

**Installing from source**
- PySide6 6.12 crashes on Python 3.10 and 3.11 (often when a window closes). `tagmender doctor`
  warns about it; install `'PySide6-Essentials<6.12'` there, or use the AppImage. Python 3.12
  and later are not affected.

## 0.8.12

**One release in several versions (SACD stereo and multichannel)**
- Folders named by channel layout under one release folder (`2.0` and `5.0`, `Stereo` and
  `Multichannel`, `Mono` and `Stereo`) are one album. Each version is matched to its own disc of
  the release and tagged and filed as that disc: a Hybrid SACD's stereo layer as disc 2 of 5 in
  `Disc 02`, its multichannel layer as disc 3 in `Disc 03`. Before, each version was a separate
  album, both went into one folder and one set of files got `~` suffixes.
- A version is mapped to an SACD layer only with evidence: an `SACD_AREA` tag, DSD files, or
  high-resolution audio. A 44.1 kHz/16-bit rip is not taken for the SACD layer.
- When the release does not say which disc is which version, the album is left for you to choose,
  with the reason, instead of both versions being filed together.
- The same files filed this way and looked up again are recognised as they were filed; nothing
  is rewritten.
- Only folders that are really versions count: at least two sibling folders with different
  layouts, all of one album, with no other music beside them. An artist or album called *Mono* or
  *Stereo* is read as before.

**DVD-Audio, DVD-Video and Blu-ray audio rips**
- Version folders such as `Stereo 24-96` and `5.1 24-96`, `DVD-A 2.0` and `DVD-A 5.1`,
  `BD Stereo` and `BD Atmos` are one album too. Stereo, 5.1, Quad, Atmos (its own version, not
  5.1) and 7.1 are told apart; codec and rate words in the name (DTS, MLP, TrueHD, LPCM, 24-96,
  48kHz) are allowed.
- A DVD or Blu-ray holds both mixes on one disc: when the release lists each mix (titles such as
  "(5.1 Mix)"), each folder is matched to its own tracks; when it lists the tracks once, both
  versions share the disc number. Versions that share a disc are filed into a subfolder each,
  `Disc 02/Stereo/` and `Disc 02/5.1/` (or `<album>/Stereo/` without disc folders), whatever your
  naming script; never with `~` suffixes.
- What says which disc a version came from: a disc word in its folder name (DVD, DVD-A, BD,
  Blu-ray), the media tag of its files, an SACD_AREA tag or DSD files. The sample rate alone never
  proves it: such a match is shown for you to check, never as certain. A single folder named
  `Stereo 24-96` is matched as an ordinary album.

**The edition in folder names says SACD, DVD-A and Blu-ray**
- With the built-in naming (and *Separate editions in folder names* on, or the All music, Various
  artists and Classical presets), an album filed from an SACD layer is `[SACD]`, from a DVD-Audio
  `[DVD-A]`, from a Blu-ray `[Blu-ray]`; before, each was named as a CD. The Piano Man set above
  becomes `1973 - Piano Man [2024 SACD]`.
- Albums of those media already in your library move to the new folder name the next time they
  are filed (and their Album tag changes when *Edition in the album title* is on). A profile with
  its own copy of the naming script keeps naming as before until you choose the preset again or
  copy its edition line.

**The release folder's name is read for its versions**
- The catalogue number, country, year and format written in the release folder's name count for
  the version folders inside it, and a catalogue number range is understood: `SICP 10151-4` and
  `SICP 10151~4` mean SICP 10151 to 10154. A European format suffix (`COL 474161-2`) is not taken
  for a range.
- A release the folder name contradicts (another year, a download where the folder says CD or
  SACD) is no longer reported as certain.

**Watched folders: never the library**
- A profile's library (or a folder inside it) is no longer looked at as a watched folder. Watched,
  every album already filed there was treated as a new drop, and a pass over a large library could
  run so long that the drop folders after it were never reached. Watch a Folder now refuses a
  library, and one already on the list shows *No: it is the library* under Available and is skipped.

**Watched folders: when you say, or on the clock**
- **Check Selected** looks in the selected watched folder now (also *Check This Folder Now* in its
  right-click menu and Tools › Watched Folders); **Check All Now** looks in every one.
- The timer runs on the clock for all watched folders: every 10 minutes is 12:00, 12:10, 12:20,
  not 10 minutes after TagMender started. The page shows when the next check is; untick *Keep
  watching while TagMender is open* to look only when you ask. The background service keeps the
  same times.
- The list under the folders is now *Recently in watched folders*: albums applied on the Overview
  are on the Jobs page.

**Jobs: which albums, and where they went**
- Each job shows its albums (artist and album as filed) and the folder they were filed into, with
  **Open Folder** (also in the right-click menu). For an undone job it opens where the albums are
  back.
- The release folder's artwork, scans and notes now move with an album made of version folders
  (they were left behind beside `2.0` and `5.0`).

**Editing a disc number moves the files**
- An edit in the inspector of a field your naming uses (disc number, total discs) now previews the
  album again, so Apply files it where the edit says (a `Disc 02` folder). Undoing the edit does
  the same.

## 0.8.11

**Overview**
- The Profile column is always shown, for one album as for many.
- Drop folders while a look-up or Apply is running, or choose them with Add Folder (now available
  during a run). They wait, and when the run ends their albums are added below the ones already
  listed and looked up with the same sources. Albums already looked up keep their results. Cancel
  discards folders still waiting; after a failed look-up they are listed but not looked up.
- An album already applied can take another profile: choose it in the album's Profile cell or
  menu. TagMender reads the album where Apply put it, looks it up again with that profile, and
  the next Apply files it the new profile's way, as a job of its own that Jobs can undo.
- Drag a rectangle across a list to select the rows it covers, as in a file manager: from a row
  or from the empty space below the rows, with Ctrl to add to the selection. It works on every
  list that can select several rows. Escape clears the Overview's selection.
- Delete (or Backspace) and Remove from List take the selected albums out of the Overview list
  again, also after an Apply of every album. Nothing on disk changes. Delete did nothing because
  Remove Tag in the menus used the same key, and after a full Apply the list had no plan behind
  its rows.
- Scores are tinted again: a light wash for Excellent and Good, and a stronger tint with bold
  digits for Worth a look and Needs investigation, so a doubtful score stands out by weight as
  well as colour.

**Lists that behave like a folder**
- Copy copies every selected row, one per line (folder paths where the row has one).
- Needs attention: Retry and Open Folder act on every selected album; several are listed
  together on the Overview.
- The Menu key and Shift+F10 open a list's menu on the current row, wherever the pointer is.
- Lists whose actions take one row at a time (Watched folders, Waiting for your approval,
  Recently, Ignore and skip rules) now select one row, so an action never quietly applies to
  only one of several selected rows.

**Watched folders**
- A **Check Now** button under the folders looks in every watched folder at once instead of
  waiting for the next check. Music still being copied waits until it has settled, as before.
- The lists on Watched folders and Jobs are resized by dragging the line between them; the heights
  are kept for the next start.

**Find Covers…**
- Right-click a picture (or press the Menu key): open your own pictures in their default app or
  show their folder; open a copy of a cover stored in the tracks; open a found cover in the
  browser or copy its link.

**Profiles**
- The profile section **Source** is now **Files Read**: it sets which audio files are read. The
  databases a profile asks are under Identification. (The Overview's Source column, which says
  how each album was matched, is unchanged.)

## 0.8.10

- The packaged app (.deb, AppImage, Flatpak) shows the TagMender logo beside each page's title
  again: the logo file was not in the packages, only in the source. The scan-reading order that
  comes with TagMender (`assets/ocr_order.json`) was left out the same way and is now included.
- The logo and the green-to-red line are on every screen's title: Preferences (Profiles and Setup
  among its tabs since 0.8.9) and the dialogs with a page title, not only the main window's pages.
- Preferences opens wide enough to show all nine tabs (since 0.8.9 the last ones were cut off).

**Overview: what matters first**
- "N albums ready to apply" in bold under the list, the other counts beside it in grey.
- Colour only where it asks for you: a status is coloured only when it stops Apply (Blocked,
  Failed, Changed since preview) - its icon still says the rest; the Score cell is tinted only
  when it is worth a look or needs investigation, and says its band to a screen reader.
- No subtitle under the title; Sources and Library folder are quiet labels; Look Up and Refresh
  are plain text buttons, Apply in the toolbar the one prominent one. The 0.8.9 Refresh button
  made the page too wide for a 960-point window; it fits again.

**Look and feel**
- One type scale: page titles 22, section headings 15 (a profile's sections were as large as the
  page's title), text 13, small print 12 (was 11).
- Buttons, fields and pop-ups have an edge you can see (3:1 against the window, light and dark),
  and a visible focus ring when you move with the keyboard.
- Spacing on one scale (2, 4, 8, 12, 16, 24).
- Shorter page subtitles (Setup's was 37 words).
- A setting that cannot be saved is named as on screen ("Duplicate copies folder must be a plain
  folder name, such as 2delete"), not by its internal key.

**Loading**
- Search and Change Release show the rows' shape while the databases answer (a soft pulse; still
  with Reduce motion or the desktop's animations off), say so when it is slow, offer Stop when it
  takes much longer, and on a failure say what happened with Try Again - never a blank list.

## 0.8.9

**Discogs and CoverUp are built-in plugins**
- TagMender now carries the Discogs and CoverUp code itself (`plugins_builtin/`): nothing else to
  download or clone, from a package or from source.
- Both are listed in Preferences › Plugins, switched on, each with its own switch (they can be
  switched off, not removed). The change applies from the next look-up. With Discogs off, albums
  are identified with MusicBrainz only; with CoverUp off, covers come from the Cover Art Archive
  and Discogs, and covers already there are kept. `tagmender plugins disable discogs` does the same
  on the command line, and `tagmender doctor` says when one is off. With Discogs off,
  `tagmender inbox` and `tagmender match` stop and say so; `tagmender web` and `tagmender enrich`
  leave Discogs out.
- Preferences › Advanced › Code folder is gone; a folder set there before is no longer read.
- With Discogs off, CoverUp asks Discogs nothing either, and band members are not looked up there;
  with CoverUp off, no artist photo is looked for and Find Covers… does not search online.
- What CoverUp sends to cover sites now names TagMender.

**Preferences**
- Profiles and Setup are now tabs of Preferences (Ctrl+,), not pages of the main window's
  sidebar. View › Profiles and View › Setup, File › New Profile… and Help › Set Up TagMender…
  open them there, and the first start opens Setup there. Closing Preferences asks about a
  profile not saved.
- **Apply to All Profiles…** on every setting of a profile (in the ⋯ menu beside it, or the All
  profiles ▾ menu of a shared one): that setting, as shown, goes into every profile, also one
  that kept its own value. Only that setting changes in them; nothing is written if any profile
  could not take it, and the profile shown is saved first.
- File Safety › **Duplicate copies go to**: the duplicates folder, or **the Trash** (the Trash of
  the drive the copy is on, as a file manager does; `tagmender dedupe --undo` still puts them back).
- The duplicates folder can be given as its path at the top of a library
  (`/music/2delete` is taken as `2delete`) instead of being refused.

**Overview**
- **Refresh** (beside Look Up, Ctrl+R, and Refresh with Current Settings when you right-click
  albums) looks the selected albums up again with the profile as it is now, every album when none
  is selected. After you change and save a profile, the Overview says how many albums were looked up
  with its old settings, with a link that refreshes just those.
- An album looked up again with the same settings is no longer asked of the databases twice (the
  check compared the profile in memory, not its settings).

**Album artist**
- *Lead artist only* now files a band named after a member under that member also for an album
  tagged from Discogs alone ("Jason Bieler and The Baron Von Bielski Orchestra" → Jason Bieler): the
  band's members were asked of Discogs only when the files already held its Discogs artist ID.

**Plugin API**
- A script function given the wrong number of arguments is the script's error ("Wrong number of
  arguments"), no longer switching the whole plugin off; one that returns something other than
  text is switched off instead of stopping the album.
- A tag processor that fails part-way through an album: the album is planned again without it and
  the plan says so, never with the change on some tracks only; a script function failing while an
  album's files are named holds that album back with a note, never with mixed names. `album_folder` is the album's folder
  also for albums in CD1/CD2 folders.
- `requires = ["pkg.sub"]` with `pkg` missing no longer stops every plugin from loading;
  `plugins check` takes a plugin in several files; a damaged zip is refused with a message.
- Python 3.10: plugins work with the `tomli` module, and a plugin switched on that cannot be read
  says why. `tagmender profiles set/check` know plugins' script functions. `--no-plugins` and
  `--profile` in either order.
- Preferences › Plugins › Install from Folder…; installing a newer copy keeps a plugin on;
  switching a failed plugin off and on tries it again; a secret setting can be forgotten.
- `plugins new` names its example function after the plugin and quotes its name; `plugins check`
  refuses the built-in plugins' ids; a switch setting's default must be true or false.
- docs/PLUGINS.md: the Python requirement, every manifest field, the script function and tag
  processor contracts, settings and secrets, and what happens when a plugin fails.

**History**
- **Clear History…**: everything, or what is older than 1, 5 or 10 days. Only the list goes; the
  rules, and what they leave alone, stay.

**Window**
- A new session always opens on the Overview.
- The inspector can be dragged wider (up to 960 points instead of 480), so long tag values show
  whole; the Tag column widens with it.

## 0.8.8

- *Start from a preset* lets you pick one preset only (all three could be selected at once).
- Overview: the columns fit the list instead of running past its edge. The widest (album
  folder, changes, profile) give way first, cut and shown whole when pointed at, and Why, Score,
  Scans and Source make room in that order as the window narrows, so the Score stays in view
  at common window sizes. The columns are fitted again when the inspector opens or closes.
- The README's screenshots show this version.

## 0.8.7

Fixes to the 0.8.6 changes.

**Settings shared by all profiles**
- A profile that follows the tagger's settings for Windows-safe names and empty folders keeps
  following them when the shared settings are first set up.
- A shared change that would stop another profile from working (for example the pictures
  folder given the name another profile uses for logs) is refused and that profile named.
- *Start from a preset* changes only the profile it is chosen in: a shared setting it changes
  becomes that profile's own.
- A profile file put back from elsewhere (a backup, another computer, the deleted profiles
  folder) works as it always did; it is never given the shared values silently, whichever
  way it is opened or saved. An own setting left as *Automatic* stays Automatic.
- `tagmender profiles migrate` upgrades an older file again once the shared settings exist.

**Overview**
- *Apply This Album* during a look-up applies only the albums you chose, not one the stopped
  look-up brings in meanwhile (another album of the same folder included).
- Dragging the inspector's edge or the window no longer counts as you sizing the columns: only
  a drag on the column headers keeps a width.

## 0.8.6

**Settings shared by all profiles**
- Matters of taste and of your system are now set once for every profile: various artists
  and compilations, album artist, artist names and credits, genres, preferred releases, MP3
  tag versions and stray tags, comments, file-name safety, picture and log folder names, cover
  sources and sizes, web lookups, file safety, skipped folders, retries, the TagMender notes,
  lyrics and loudness. Each such setting says *All profiles ▾* in the profile editor; choose
  *Only This Profile* to keep a different value in one profile.
- The first time the app starts, a setting your profiles all hold the same value of becomes
  that shared value; one that differs stays as it was in each profile (marked *This profile
  only*). Nothing any profile does changes.

**Profiles**
- The tagging level (Basic, Enthusiast, Audiophile) is the first setting of a profile, and
  only the settings that matter at that level are shown.
- A *Search settings* field above the profile list.

**Overview**
- Apply while albums are still being looked up: the look-up stops after the album it is on,
  Apply asks as usual, and the albums not reached are looked up afterwards.
- The Score is coloured by how far the match can be trusted, from the score *and* its
  evidence: green (excellent: the pressing is proven, or an almost perfect fit), blue (good),
  amber (worth a look: the album is known but not its pressing, or a weaker fit), red (needs
  investigation: unresolved, a conflict, or scans naming another pressing).
- Every column can be resized and keeps the width you give it; a cut cell shows its whole
  text (the whole Why) when pointed at.
- Read scans and Choose… are gone from the folder line: the profile's *Read scans and
  booklets* decides, and Add Folder or a drop chooses the folder.

**Album artist**
- A band named after a member is also filed under that member when Discogs lists the member
  and MusicBrainz does not (or the album came from Discogs alone): *Jason Bieler and The Baron
  Von Bielski Orchestra* → **Jason Bieler**. The band's name may run six words past the
  member's.

**Look (closer to a Mac app)**
- Each page's title carries the TagMender logo with a thin rule in its green and red, and the
  accent colour is the logo's green. Preferences and dialogs keep a plain title.
- The sidebar's selection is a soft green tint with the page's name in bold, grey when the
  sidebar is not focused.
- Empty lists say so (Jobs, History, Watched folders), with what fills them.
- The window's title names the page shown ("Overview — TagMender").
- Setup's sections are inset cards, as in Preferences and Profiles.

## 0.8.5

- The profile chosen in the toolbar is remembered: the app starts with the one you used last
  (Preferences' default profile "last", as it always said). A renamed profile is followed.
- Folders whose albums you took out of the list (Remove from List) can be dropped again: they
  are listed again instead of "Already in the preview".

## 0.8.4

**Album artist: lead artist only** (a profile option, Tags › Album artist; off by default)
- The album artist, and the folder it names, can be the lead artist alone, while Artist keeps
  the whole credit: *Thomas Strønen & Time Is a Blind Guide* is filed as **Thomas Strønen**.
- *Bands named after a member*: Steve Morse Band, Pat Metheny Group, The Jimi Hendrix
  Experience, Glenn Miller and His Orchestra, Tom Petty and the Heartbreakers… are filed under
  that member when MusicBrainz lists them as a member. Deep Purple stays Deep Purple, and so
  do bands not named after a member (Electric Light Orchestra, The Marshall Tucker Band).
- *Your own album artists*: one per line, `Credit = Album artist`; `Ben Folds Five = Ben Folds
  Five` keeps one as credited.
- The sort name and MusicBrainz ID follow the new album artist. When MusicBrainz cannot be
  asked, an album already filed stays where it is.

**Overview**
- Look Up while another look-up runs: the albums are queued and looked up next (Cancel
  clears the queue).
- Each album's row shows its result as soon as it is matched, with *Waiting…* and *Looking
  up…* on the others, instead of all at the end.
- Apply shows the album and the file being written ("album 2 of 5 … file 3 of 12"), with
  *Applying…* on its row.
- The progress count beside the bar is readable again.
- The Changes column says what Apply will do, before it is done: "10 tracks, will move",
  "15 tracks, stay in their folder".
- A **Scans** column says what your scans showed about the release: "✓ Barcode, Cat. no."
  (the pressing is proven), "~ Label" (agrees, proves nothing), "✗ Cat. no. differs" (they name
  another pressing), "— Nothing read". Never a percentage: a score is not proof.
- **Source** says how the release was found when not by a plain search: "MusicBrainz · disc
  ID", "· your files' link", "· web search of your scans", "· barcode lookup"; albums tagged
  from store pages say "Web".

**Watched folders**
- *Recently* has a **Match** column: how sure the identification was ("Exact · ✓ Barcode",
  "Album only", "Partial", "Unresolved"), for albums handled from this version on.
- "Waiting for approval" stands out; Review… (right-click, or double-click) chooses that album
  in the list of plans waiting for approval and shows its changes, to apply there.
- Choosing another release for one album (Change Release, or the report's Apply This Release
  for an album already listed) looks up that album alone; the other albums listed stay as they
  are, instead of the whole list being looked up again.

## 0.8.3

**A profile per album**
- With several albums in the Overview, each row has a Profile drop-down: *As Above* (the
  profile chosen in the toolbar) or one of your own for that album. Several selected:
  right-click › Profile. Look Up plans each album with its profile, and Apply runs one job per
  profile; an album whose profile changed after it was looked up is looked up again.
- Albums left out of one Apply can be applied later (before, the second Apply was refused as
  "already run").

**Fewer wrong pressings**
- A catalogue number written by another tagger now counts like its barcode: it fits the album
  but proves no pressing (printed on your scans, it is your copy's own again).
- Audio fingerprints identify the album, never the pressing: they no longer decide between
  pressings, between MusicBrainz's and Discogs's picks, over your folder's catalogue number, or
  for a bootleg.
- Another tagger's release IDs no longer pick between MusicBrainz's and Discogs's pressings,
  and a barcode from notes or another tagger no longer silences the folder name's year or makes
  a pick by track lengths automatic.
- Albums whose only number came from notes or another tagger have their scans read for a
  number of their own.

**Your files**
- A track saved by another program while TagMender wrote its hard-linked copy is put back as
  it was, that program's edit kept (before, the new copy could be left behind unrecorded).
- A cover or log removed after the preview gives a plain message.

**Watched folders**
- A profile that uses only the web retries soon when no web source answers, instead of waiting
  days as if nothing were found.
- A profile saved with an interval over a day (older versions allowed it) is watched again,
  daily.

## 0.8.2

Bug fixes.

**Your files**
- If another program saves a track while TagMender is writing it, the album is put back and
  that program's edit is kept (before, it could be lost without a word).
- Read-only tracks are tagged and stay read-only (before, Apply always failed on them); undo
  works on them too.
- Applying one version of a folder that holds two no longer takes their shared cover and log
  away from the other: apply both together.
- A crash while a cue sheet's name changed only in letter case no longer leaves the rewritten
  cue behind after recovery.

**Watched folders**
- An album you undid is not filed again just because the folder is watched by another profile.
- Approving a waiting plan of several discs or versions remembers the one that failed, so it is
  tried again.
- Process Folder Again works for folders written with ~ (your home folder).
- Saving a watched folder's settings without changing them no longer changes its timings.

**Look-ups**
- A profile's ignore and skip rules are never another profile's (the watcher and a Look Up
  running at the same time, or a profile chosen on the command line).
- When no web source answers, the album says so ("Web source unreachable") instead of "Not
  found", and a Look Up asks again. Albums a database could not be asked about are looked up
  again too, not skipped as done.
- Refresh, Look Up and a new library folder no longer drop the rows of a set split per disc or a
  folder split into versions; a cancelled Look Up keeps the albums it had not reached.
- Needs attention never shows the previous folder's albums.
- Apply is no longer refused after Make It the Profile's.
- Some phrases are now translatable.

## 0.8.1

**Faster**
- MusicBrainz and Discogs are searched at the same time, and scans are read while they are
  searched (one album's look-up takes noticeably less time; MusicBrainz still gets at most one
  request a second, now shared by every part of TagMender).
- The watched folders: tags of files that did not change are not read again on every check, and
  each file is looked at once per check instead of three times.
- Look Up after listing a folder reads only the albums you chose.
- Audiophile loudness and dynamic range are measured four tracks at a time, each track read once.

**Surer about the pressing** (only stricter, except hidden tracks below)
- A MusicBrainz or Discogs release link another tagger wrote into the tags is a lead, not proof.
  Nor are that tagger's country, date or label.
- When one database is sure but your barcode or disc ID is on a different pressing in the other,
  the album waits for your review.
- A blank field in one database entry no longer picks the other pressing.
- A cue sheet from another rip no longer proves anything; your own rip's cue still does (gaps
  appended or prepended, with a hidden track or without).
- A hidden track before track 1 (a "00" file) is the disc's pregap: with the disc ID or the rip
  log proving the release, the album is tagged automatically, the hidden track included; on track
  lengths alone it waits for your review.
- Label lines in Russian notes ("Лейбл:", "Издатель (лейбл):") are searched too.

## 0.8.0

**Surer about the pressing.** Each change below sends an album to review or tries it again later
rather than picking a pressing on weak evidence.
- When MusicBrainz is busy while the disc ID or the files' own release links are checked, an album
  nothing else proves is tried again later instead of being picked from weaker clues. The release
  your files already link to is kept when that lookup fails.
- A barcode only typed in a notes, .nfo or saved web page counts as a clue, not as proof. A
  barcode read off a scan, a booklet PDF or a cue sheet still is proof.
- ISRCs identify recordings, which every pressing shares: they no longer decide the pressing.
  Nor do audio fingerprints, nor a disc ID that only roughly fits.
- When MusicBrainz links its release to a different Discogs release, the two are no longer taken
  for the same pressing.
- Tags another tagger wrote are not proof. When they name a release the folder name rules out,
  the album waits for your review with both named. Tags TagMender wrote itself (it knows from its
  job history, also after it moved the album) still count.
- Expect more albums for review when you re-run over a library another tool tagged.

**Faster**
- While MusicBrainz or Discogs is down, a run stops asking for five minutes instead of waiting out
  every request; the albums are tried again later.
- Moving FLACs to a share: the cover no longer makes TagMender rewrite each file a second time
  (about half the data written for a typical album).
- Scans, logs and cue sheets moved within one disk are renamed instead of copied and checked.

## 0.7.1

Faster, with the same results: nothing about what is identified or written changes.

- **Slow or unreachable web sources no longer stall a run.** Deezer, iTunes, Bandcamp and
  Wikipedia are asked once or twice with a short wait, and left alone for 30 minutes after
  failing again and again (before: six tries of up to a minute each, for every album). When one
  of them did not answer, no web tags are taken from the others for that album; it is tried
  again later.
- **Each website is paced on its own.** MusicBrainz keeps its one request a second, and slowing
  down after MusicBrainz says it is busy no longer slows the others; it comes back to normal
  after a while. Deezer's "too many requests" replies are no longer kept as "no results".
- **No pointless wait after the last try** of a request that failed.
- **Web results whose track count rules them out** are not fetched in detail.
- **Preview of a move** no longer reads every audio file in full when nothing at the destination
  needs it (about twice as fast to plan).
- **Apply** reads each moved file twice instead of three times; the audio check before the file
  goes into place stays.
- **Each file's tags are read once per album** instead of up to eleven times: much less reading
  over network shares.

## 0.7.0

**Compilations** (a new group in the profile's Tags section)
- *Various artists name*: Various Artists, Various, VA or your own, written as the album artist
  of a various-artists release whichever database identified it. *Automatic* (the default)
  keeps what TagMender wrote before.
- Various-artists albums identified from Discogs or the web now get the compilation flag too,
  not only those from MusicBrainz. Track artists stay as credited.
- A database's own artist ID decides: a DJ mix credited to the DJ, or a band called "V.A.", is
  not taken for a compilation.
- *Also write ITUNESCOMPILATION and COMP* (off by default): for FLAC, Ogg and APE files, for
  players that look for these. MP3, M4A and WMA keep their own compilation flag.
- *Set Grouping to Compilation* (off by default): only where Grouping is empty; yours is never
  replaced.
- *Also flag single-artist compilations* (off by default): best-ofs get the compilation flag,
  their artist unchanged.
- Existing profiles behave as before until you change these.

## 0.6.0

**Library folder on the Overview**
- Beside Sources, *Library folder* shows where copied or moved albums go. Choose another folder
  for this preview and Apply only (marked ↺); albums already looked up are looked up again for
  it, a release you chose is kept. *Make It the Profile's* saves it to the profile.
- It goes back to the profile's folder when you open another folder or choose another profile.
  Apply asks you to preview again when the preview was made for another folder.
- Not offered for a profile that fixes files in place.

**Profiles no longer list folders**
- The profile editor no longer has *Folders this profile covers* and *Include subfolders*: any
  folder can be previewed with any profile, and the Watched folders page is where a folder is
  paired with a profile. *Ignore and Skip Rules…* is in the Source section.
- Each watched folder has its own *Include subfolders*. Off, only albums directly in the folder
  are filed.
- Your profiles keep the folders they had: watching and the profile chosen for a folder work
  as before.

## 0.5.0

**Watched folders**
- *File albums without asking* now files only the albums it is sure of (Ready). An album it is
  not sure of (Unresolved, Partial, two pressings that fit) stays where it is and waits under
  *Waiting for your approval*.
- A folder given another profile is processed again with it: an album tagged in place before
  is filed into the new profile's library.
- *Process Folder Again…* (right-click a watched folder, or Tools › Watched Folders) looks at
  every album there again, as its profile says now. Albums you undid, and albums a rule
  ignores, are left as they are.
- *Look every* offers 1, 2, 5, 10, 15 and 30 minutes, and 1, 2, 3, 5, 9, 12 and 24 hours.

**Looking up**
- While albums are looked up, the line under the list says which one of how many, and the
  toolbar bar shows the count.
- An album already looked up the same way (profile, sources, Read scans) is not looked up
  again; you are asked first. Refresh (Ctrl+R) looks up the selected albums again.
- What your copy itself proves (its disc ID, a barcode from the tags or a scan, ISRCs) now
  outranks a country or catalogue number in the folder name, which can be wrong. A release your
  copy cannot be (a 12" when the folder has a CD rip log) is ranked after the others.

**Overview**
- With two or more rows highlighted, Apply applies only those.
- Selecting rows by dragging the mouse no longer stalls on each album it passes over.
- *Needs attention* follows the folder you previewed last, unless you chose one there.

## 0.4.1

- Dropping several folders at once lists every album in them together; nothing is looked up
  until you select albums and press Look Up (or Preview for all). Before, only the first folder
  was taken and the others had to be dropped again one at a time.

## 0.4.0

**Simpler profiles**
- A profile shows about half as many settings: related ones are now one choice (what happens to
  files and renaming, artist photos, the cover in each track, Audiophile measurements, Discogs
  details, credits, pictures and logs folders), rarely changed ones appear only once changed, and
  expert ones are under Advanced.
- No more settings that contradict each other: lyrics are one choice; Sources has five values
  (including Web only) instead of two extra switches; a setting that does nothing as things stand
  is greyed out and says why (at the Basic level, with Discogs not asked, with covers off...).
- Watching settings are set on the Watched folders page only.
- Your saved profiles keep exactly the settings they had.

**Looking up**
- The Sources beside Look Up list every choice (the profile's, MusicBrainz or Discogs only or
  preferred, the web only). Another choice is for that look-up only, marked ↺, and goes back to the
  profile's for the next folder; *Make It the Profile's* keeps it.

**Help**
- A [quick start](QUICK_START.md) for new users; Help (F1) opens it.
- Preferences › Advanced names the Discogs Enrichment and CoverUp code folder as such;
  Preferences › Plugins shows where plugins are kept.

## 0.3.0

**Adding music**
- A folder you add is listed first, marked *Not looked up*. Choose the sources beside **Look Up**
  (the profile's, MusicBrainz only, Discogs only, or the web only) and look up the albums you
  select, or all of them. Preferences › General can go back to looking up at once.
- Apply only the albums you want: each has a tick box, and several can be selected for Apply
  Selected Albums…, Leave Out, Include or Remove from List. Albums left out stay to apply later.
- Each source folder of a profile can file its albums into its own library folder (Moves to), so
  one profile can send `/inbox/ambient` to `/music/ambient` and `/inbox/extreme` to `/music/extreme`.

**Covers**
- **Find Covers…** (Album › Artwork) shows your own pictures - including those in `Artwork/` and
  `Scans/` folders - and every cover found online, with its size and source, to pick one by eye.
- **Make AI Covers…** makes covers from four presets (Faithful, Creative, Art, Era) with your own
  image service; their lettering is checked, and a real cover found later replaces them.
- The album's own cover is also found in its picture folders, the largest front winning.
- New profiles gather an album's pictures and scans into one `Artwork` folder.

**Faster scans**
- Scans are read in the order that pays off - backs, discs and obis first, booklet pages last -
  learned from your own albums; `tagmender ocr stats --export` shares the counts (names and
  numbers only). Reading stops once a scan settles the release, codes are read in English only,
  scans are read in parallel, and misread codes are no longer searched. On albums with scans,
  identifying took about half the time.

**Profiles and settings**
- Profiles can be renamed, deleted and duplicated (new ones are named Profile 1, 2... up to 15);
  their settings sections fold; folders are chosen with Choose Folder….
- Every setting has a plain description; cover sources can each be ticked and ordered.
- Plugins: Preferences › Plugins installs plugins that add script functions and tag changes
  (see [PLUGINS.md](PLUGINS.md)).
- A guide to the naming and tagger script language: [SCRIPTING.md](SCRIPTING.md).
- The window opens at a size that fits your screen, with every control and the status bar in view.

## 0.2.0

The first public release.

- Identifies each album against MusicBrainz and Discogs, down to the pressing, from its tags, file
  and folder names, track lengths, disc IDs, barcodes and catalogue numbers read from scans, and
  AcoustID fingerprints. A release is chosen automatically only when the evidence proves it.
- Writes standard tags at three levels (Basic, Enthusiast, Audiophile), adds cover art and artist
  photos, and names and files albums with presets or your own naming script.
- Every change is planned and shown first, then staged, verified, journaled and undoable.
- Profiles for each library or inbox, watched folders, duplicate handling, and an optional
  MusicBrainz login for collections and ratings.
- A desktop app and a command line (`tagmender`); packaged as an AppImage, a .deb and a Flatpak.

See [FEATURES.md](FEATURES.md) for everything TagMender does, and [SETUP.md](SETUP.md) to get
started.
