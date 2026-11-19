# Release notes

## 0.8.84

**Ticks follow your highlight**
- Highlighting an album, in either pane, ticks it; when the highlight goes, so does the tick it gave. A box you tick or untick yourself (or Leave Out of / Include in Apply) stays as you set it. Apply carries out exactly the ticked albums (APL-2).
- Albums TagMender waits for your tick on are never ticked by a click: an Unresolved album, a held one, one whose filing is offered, and a release you chose shown to be checked first. An album only shown (after a preview or a look-up) is not highlighted by you and is not ticked.

**What you tick is filed**
- An album below the profile's score for filing now starts unticked even when it has tags to write; ticking it is the ask, and it is tagged and moved into the library. Before, it started ticked and Apply only tagged it where it was (MOV-6).

**Look-ups**
- Albums you add or look up while a look-up runs join it as soon as a worker is free (up to three at once), instead of waiting for it to end. Albums from another folder, with other sources or profile, or in a folder Apply is working on still wait, and the status bar says why (LKP-1).
- Two folders of one release added one by one (an SACD's layers beside its CD rip) are looked up together and filed as one album, as when their parent folder is looked up (DSC-10/DSC-11).

**Also**
- Tests run on their own never write into your settings or History.

## 0.8.83

- An album is no longer called "Mono" because one disc of its box holds a few mono tracks. Discogs's "Mono" names the version only when nothing else in the release says Stereo, Quadraphonic or Multichannel (ED-9). A deluxe SACD set whose bonus CD mixes mono demos with stereo live takes was filed "[Remastered, Mono]"; it is "[Remastered]".

## 0.8.82

- The Cover pane shows the cover you choose. A cover chosen for the album shown (Choose Cover…, a found cover, an image file or link) was taken, and Apply would have written it, but the pane beside the tags kept showing the cover it had read before. It now shows your image at once, its size and "your image" with it.

## 0.8.81

- Any album can be taken out of the list from either pane. Delete (or Backspace) in Your files did nothing; it now takes out the albums its selected folders and files hold, as it does in Albums as they will be, ticked or not. Escape there lets go of the selection.
- Edit › Remove from List does the same from the menu, for the pane you selected in. Nothing on disk changes.

## 0.8.80

**Multi-disc sets: your copy's own proof decides**
- Checked on 67 multi-disc sets against their evidence (disc IDs from the rip logs, barcodes, track lengths): 9 were wrong in 0.8.78, none now; 50 are matched, 17 held with a stated reason.
- What your copy proves wins over quirks of the database listings: a release ID already in your tags is always a candidate (and a broken database answer is asked again); a box's video discs no longer hold its CDs; a disc marker ("CD10") is never taken for a catalogue number; a set's discs crediting their own performers stay one set; your scanned barcode decides; another edition's scans in their own dated folder are not your copy's print.
- Boxes named as such are matched as the box when their disc IDs or rip logs prove it (13-CD and 4-CD sets of separate albums included); separate releases are never joined, and a disc that only its track lengths place in a box holds the album for your decision, its note saying where each disc's ID really lies. Ticked, it is written disc by disc ("disc 2 of 13") and recorded as your choice.
- Track lengths alone never prove a disc or a pressing; two pressings that fit equally are proposed, not written; nothing below your profile's confidence threshold is written unasked.
- Tracks the release puts on another disc move into that disc's folder (with its logs when a whole disc moves).

## 0.8.79

- The status bar never draws two texts over each other: applying beside a look-up said its progress over the album counts ("Beside the look-up: ..." on top of "1 album ready ..."). Its progress is now said where an Apply's always is, the counts step aside while any message shows, and they stay off other pages.

## 0.8.78

**Box sets: never two releases made one**
- 0.8.76's box-set matching could join folders that are separate releases. Fixed, and checked by an independent review: a discography folder whose name gives a CD count ("Discography 1990-1999 (3CD)") stays separate albums; a box with a stray single beside its discs is the box plus the single; standalone albums whose disc IDs are also on a box stay standalone unless the folder names the box or says it is a set; a dated artist folder ("Albums 1979-1981") is not taken for a box titled by its years.
- A single CD whose disc ID is on a box is matched to its own release when that release fits, with the note "also issued as disc 1 of '<box>'". When its own release cannot be found, the box's disc is proposed, unticked: "Its disc ID is on '<box>' disc 1 of 3 only; your tags call it '<title>'. Tick to tag it as disc 1 of '<box>'." A disc tagged with the box's own title ("Heart (Disc 1)", "Heart [CD1]") is the box's disc as before (DSC-12a).
- Joy Division - 1977-1980 (the 1991 Japanese 5-CD box) is now one album: a short disc whose exact disc ID is on the box belongs to it, even when it is also a single (DSC-16).
- Files placed across a set's discs never swap a studio and a live version of the same song; a title on two discs is placed only by its IDs or a clearly different length (TRK-8). Large boxes are matched much faster (a 26-disc box: 8 s to under 0.5 s per candidate).

## 0.8.77

**Applying, at a glance**
- An album you applied is unmistakable: a filled green check in place of its checkbox, "Applied · moved" (or "Applied" when tagged in place) and a light green wash over its row; its tooltip names the folder it now lives in. While it is being applied, its row says "Applying…".
- A progress bar at the bottom of the window, in TagMender's colours, while you apply or save tags: "Applying 3 of 12 albums · 214 of 1,030 files", then "Applied 12 albums" (failures in red) for a few seconds; Cancel shows "Stopped after N of M".
- Clear Done is back on the toolbar, beside Look Up (and in Edit > Clear Done Albums).

**What you ask for is filed (MOV-6)**
- Any album with tags to write can be applied when you tick it, whatever its score: an album found only on the web (Partial) or one scoring below your profile's threshold is tagged and filed into your library layout like any other, keeping its mark. Unticked, nothing changes from before; watched folders still file only what they are sure of.
- An album not filed explains why, in the Inspector and above its tags, with a "Move into Library Anyway" button ("Copy …" on a copy profile); a greyed Apply says why too.
- An Unresolved album is never filed by a tick: choose "Use <the closest release> and File It…" to see that release's tags first, then tick and apply. Album > Change Release > Undo Release Choice takes a choice back. A release you choose is kept as your choice on later look-ups; automatic picks are never recorded as yours.

## 0.8.76

**Box sets and other sets of disc folders**
- A folder that says it is a set - a CD count ("14 CD", "5CD"), "Box Set", "Complete ... Collection" - holding its discs in numbered folders ("CD01 Cold Spring Harbor" ... "CD15 Collected Additional Masters") is now one album, matched as that set, even when each disc is tagged as an album of its own with no disc numbers; a two-disc album inside a box keeps its own disc numbering (DSC-13).
- Disc folders side by side whose disc IDs (read from their rip logs) are all on one release are that release, also when the folder does not say it is a set and with more than eight discs (DSC-14).
- Once a set's release is known, every file is placed on its own track anywhere on the release's discs, by its IDs or by title, length and position - discs in another order or mislabelled no longer go wrong (TRK-8).
- Disc words and years in album titles ("My Lives [Disc 1]", "(Disc 3) 1989-1999", "[Disk 4]") no longer split a set into albums (DSC-15).
- Example: Billy Joel - The Complete Albums Collection (2011, 15 CDs) is one album with its 15 discs, not 15 albums matched to other releases.

## 0.8.75

**Look-ups that stopped with "maximum recursion depth exceeded"**
- Looking up again an album that was applied (moved away) or changed since it was listed - Refresh with done albums still in the list - no longer stops the whole look-up. An album changed since it was listed is looked up as it is now; one moved away stays as it was, with the note "changed since it was listed: add its folder again". A row never takes another album's files.
- An error while looking up one album now stays that album's: it is shown as "could not be looked up" with nothing to apply, and the look-up goes on with the others.
- The error window shows where the error happened, and the full details go to the log.

## 0.8.74

**Albums the external tagger saves only in part**
- TagMender now waits for the external tagger to finish saving before it checks an album. An album it saved only in part is put back whole and every file is marked Unresolved where it actually is; the other discs of the same set go back with it, also when a disc was not saved at all. Separate copies of a release are never tied together.
- Nothing is touched while the tagger is still running. Albums it moved without saving are put back, and a music video it skips no longer counts against an album; the video moves with the album.
- Cover images the tagger saved into folders it created are cleaned up; folders that were there before are never removed.
- The undo record is written safely: closing the terminal or a crash mid-run still leaves a run that Undo puts back, even when something could not be moved back. A run started with nohup keeps going when its terminal closes.

## 0.8.73

**Classical music**
- TagMender now recognises a classical album by itself - Discogs's genre Classical, a classical MusicBrainz genre, MusicBrainz's "Composer; performers" credit, a composer who performs on none of the tracks; classical labels and catalogue numbers (BWV, Op.) only count together with something stronger. The album's Why tab says why. Neo-classical, classical crossover, symphonic metal, rock operas and non-western classical traditions are not taken for it (CLS-2).
- A classical album is tagged in full: Work is the whole work with its year ("Symphony no. 4 in D minor, op. 120 (1841)"), Movement Name keeps its numeral ("I. Andante con moto - Allegro di molto"), with Movement, Movement Total and Show Movement; Composer (at Basic too), Conductor, soloists, ensembles and choirs from the recordings' and works' credits; Artist is who performs, Album Artist the release's whole credit, names in Latin script; librettists and text writers as lyricists (CLS-3, CLS-4).
- A film score or other album whose credited artist composed and performs it keeps that artist ("Hans Zimmer"); on a mixed album this is decided track by track.
- Work fields that Classical Extras already wrote in your files are kept and only filled where empty (CLS-5).
- A new example profile, Classical (`tagmender profiles examples`), for a classical library: every album read as classical, names as the release credits them, and folders named by the release's credit as written ("Schumann; Musica Viva, Alexander Rudin/2004 - ..."). Your own profiles are not changed.

## 0.8.72

**Edit tags without a look-up**
- Add a folder and edit its tags straight away in the Tags pane - an album, a disc, some tracks or one file; values that differ show as "different in N files" (or "in 1 of 2 files" when only some files have the tag), and setting a value for all asks first. File › Save Tags (Ctrl+S) writes only your edits, as one job you can undo; nothing is looked up, renamed or moved. A look-up you ask for later follows the usual process (EDT-2).
- The Tags pane always shows the tags: the album's first, then the tracks'; changes are marked. "Changes only" is gone.
- Tags from File and Folder Names: one pattern reads the whole path below a folder you choose (`%albumartist%/%date% - %album% [%catalognumber%]/%discnumber%-%tracknumber% - %title%`), with a guessed pattern and a live preview.

**Vinyl rips**
- Each rip of a record is its own album, never merged with another rip. Its folder names the rip - "[Vinyl Rip PBTHAL SRM-1-1184]", the ripper (and his transfer name, "Ultimate Master") when its notes confirm it, the catalogue number - and its comment gets one line below your own text ("Vinyl rip: PBTHAL · 24-bit/96 kHz · ripped 2012 · equipment"). Two copies of a release are compared side by side (dynamic range, clipping); TagMender never picks or removes one. A folder holding two rips mixed together is split into two copies when the evidence is clear, else listed. Three profile settings turn each part off (VIN-1 to VIN-7).
- Vinyl catalogue numbers in folder names are now read (SRM-1-1184, 25·3P-378, 832-464-1 Q-1, …), and a number in another tagger's album title no longer counts as yours.
- **Once:** vinyl albums TagMender filed before are renamed on their next apply ("[1977 Vinyl Rip]" → "[1977 Vinyl Rip SRM-1-1184]"); turn "Keep each vinyl rip in its own folder" off to keep the old names.

**Discs of a set arriving apart**
- A lone disc of a set is filed as the whole release (disc 1 of 2, in "Disc 01") and listed as "disc 2 missing". The missing disc, arriving later and matched exactly, joins the album you have automatically - found by its release IDs, also after you renamed it - and the album's disc count is fixed in the same job. Another pressing, or a second copy of a disc you have, is kept apart for your decision. In a watched folder a disc waits at most a day for its set (DSC-12).

**Multi-disc accuracy (checked on 35 held-out albums: 22 right → 33 right, 2 held, 0 wrong)**
- Boxes with DVD-Audio, Blu-ray or download extras are matched by their CDs (King Crimson - Larks' Tongues complete recordings); a plain CD still beats such a box edition.
- A lone disc is chosen by its track lengths when discs share titles (Nektar); a CD of a CD + Blu-ray set gets the CD's media, not the Blu-ray's (Gojira).
- An SACD ISO's stereo and multichannel areas are one album, each layer with its own media (Kodo); the catalogue number in an SACD extraction log counts as yours.
- Your box's barcode on the scans proves it despite the replica sleeves' old numbers (Mahavishnu); a title such as "Made in Japan" is never a country (Deep Purple); a catalogue number inside a longer one agrees (Enigma); a misread barcode beside the release's own printed number is ignored (Alan Parsons).
- When TagMender writes track numbers it writes the matching total too, even where your Picard settings preserve the total.
- Sibling folders of one release named differently ("The Album", "The Story") are joined; artist folders differing only by accents are one ("Kodō" files into "Kodo").

**Album artist**
- A credit of persons only, credited as equals ("Yelena Eckemoff, Arild Andersen, Thomas Strønen"), is filed under the first-credited (NAM-8); bands, duos that are one act, classical credits and split releases ("A / B") keep the whole credit. Such albums move on their next apply.

**Known limits**
- Hemispheres (Fran Solo, US SRM-1-3743) stays held: its only US entry lists the suite differently; confirm it with Change Release.
- The window still pauses about 0.4 s while albums are added during look-ups.

## 0.8.71

**A simpler main window**
- The Overview is one window now: your files on the left, the albums as they will be on the right
  (each album one row with its status and score; open it for its discs and tracks, each with the
  folder or file it comes from), and below them the tag changes, why this release was chosen, and
  the cover. The old album table, the page title, the Sources bar and the Library folder bar are
  gone: Sources is part of the profile (right-click › Look Up ▸ for one look-up), the library
  folder is under File.
- The toolbar holds Add Folder, Look Up, a filter (All / Ready / Needs Attention, with counts),
  the profile, search, the Inspector button and one Apply button that says how many albums it
  applies.
- The Inspector button shows or hides the bottom pane; your choice is remembered.

**Profiles by selection, in colour**
- Select folders or albums and choose a profile in the toolbar (it says "Mixed" when they differ)
  or with right-click › Profile ▸: those albums are looked up again with it.
- Every profile has a colour, and its albums' rows are tinted with it, a coloured dot before the
  profile's name. LossyLess profiles are green and Extreme profiles orange from their names; the
  others get a colour of their own. Choose another in the profile's settings.

**A pressing chosen by a tie-break is suggested, not written**
- When nothing of your copy tells pressings apart (barcode, catalogue number, scans, rip logs, disc
  IDs), the pressing your country order, the original release date or your Sources preference
  would pick is no longer written as if proven. The album is tagged with what all those pressings
  share (album known), and the pick is suggested: Album › Use Suggested Pressing (also in the
  album's menu, the Why This Release tab and Needs attention) tags it in full. Watched folders
  never write a suggested pressing's barcode or catalogue number (TIE-1). Albums tagged by earlier
  versions keep their pressing.

**One release in two folders is one album**
- Two album folders of the same release, each proven on its own (Billy Joel - Piano Man 50th
  Anniversary: the SACD's CD layer and the bonus CD in one folder, its SACD layers from the ISO in
  another) are now one album: one row, each disc and layer from its folder, filed into one album
  folder with the box's scans at its top (DSC-11). Folders that hold the same disc twice stay
  apart. Read again after filing, it is still one album.
- Its evidence says "disc 1's SACD stereo layer" instead of the release's disc position, and the
  "HD Audio" mark goes only on the high-resolution copies.

## 0.8.70

**Folders named "Artist - Album" in a folder that is not the artist's**
- An untagged album in a flat folder ("Music/Radiohead - OK Computer") was looked up with the
  folder above it ("Music") as its artist, and was left unresolved. The folder name is now read as
  artist and album too, and the album is looked up both ways (FLD-2). Beside other "Artist - Album"
  folders it can be tagged as usual; elsewhere what the folder name finds is proposed for you to
  confirm, since the folder above may be the artist's own ("Radiohead/Live - At Wembley").
- "Radiohead/Radiohead - OK Computer" is looked up as "OK Computer", and "Artist - 1997 - Album"
  gives the year.

**The command line says where it is**
- While a plan runs, `tagmender plan` and `run` say on stderr which album is being looked up
  ("Looking up 2/7: Music/Radiohead - OK Computer (MusicBrainz)") and when a database asks
  TagMender to wait. Standard output is unchanged.
- `tagmender doctor` no longer fails without a Discogs token: every key is optional (MusicBrainz
  alone identifies albums). It warns, says what you lose without it and where to add it, and exits
  0 unless something is really broken.

**Licences and releases**
- The AppImage and the Flatpak now carry TagMender's licence, the third-party notices and the
  licence texts of everything they bundle (fpcalc, Tesseract, Leptonica, zbar, their libraries).
  The notices list every bundled component with its version and licence. Because the Japanese
  reading library (pykakasi) is GPL-3.0-or-later, the AppImage as a whole is distributed under
  GPL-3.0-or-later; TagMender's own code stays GPL-2.0-or-later.
- Each version now gets a permanent release of its own (vX.Y.Z) beside the rolling "latest"
  build, so an older version can be downloaded again.

## 0.8.69

**Albums show up at once**
- Each album's row appears as soon as its own folder is read, instead of after every folder you
  dropped. Dropping a few folders reads only those folders, not the folder that holds them: two
  albums dropped from a download folder of 1,400 albums are read in well under a second.
- A folder dropped while a look-up, a Preview or a listing runs gets its row within a second; it
  is looked up after the work running now. A folder dropped during a refile, or into folders
  being added, waits for that work as before.
- Tags read once are remembered for the whole session, for a library of up to 500,000 files
  (about 1 KB each). Going over an unchanged library again opens no file. Pictures and DJ-program
  data stored in the tags no longer stop a track from being remembered.
- A look-up reads only the albums it looks up, not its whole folder again.
- The list sorts and fits its columns without holding the window: the preview of 2,000 albums
  opens in a tenth of a second (3 seconds before).
- When MusicBrainz or Discogs asks TagMender to wait, the status bar says so, with a countdown.
- If work starts while Apply is asking you to confirm, nothing is applied; press Apply again when
  it ends.

**A new preview: your folders, the albums as they will be, the tag changes and the cover**
- Under the album list, four linked panes show your folders and files with where each goes, each
  album as release → discs → tracks, the tag changes for whatever you select (a file, a disc, a
  folder or the album), and the cover art. Selecting in one pane selects its counterpart in the
  others. Each pane folds away; what you folded and the pane sizes are remembered. See the user
  guide, "The preview's panes".

**Optional AI assistance with your own key**
- With your own OpenAI, Anthropic (Claude) or OpenAI-compatible key (one is enough), a profile can
  let AI read rip notes and scans in any language, suggest searches, look for the release's page
  and make covers. It is off until you turn it on; audio is never sent; each provider has a
  monthly cap. What AI finds is a clue, never proof: it never makes an album more certain, only
  suggests one of two equal pressings for you to choose and lists the releases its searches find
  as alternatives (AI-1). Set it up in Preferences › Keys; see the user guide, "AI assistance".

**Where the time goes**
- The log has one "timing" line per album: reading files, tags, scans, MusicBrainz, Discogs, the
  web, waits a database asked for, and AI. `tools/perf_check.py` checks these timings before each
  release.

**Known limits**
- While several look-ups run, the window can still pause for up to about half a second when
  folders are added (the release target is a tenth of a second). Moving look-ups out of the
  window's process is planned for the next release.
- AI is turned on per profile in the profile's file (`"ai": {"enabled": true}`); a switch in the
  profile editor comes later.
- A folder whose own tracks are one album, or with no tracks and two or more numbered folders, is
  still read whole when you drop a folder inside it.

## 0.8.68

**A multi-disc set is one album, whatever its folders are called**
- Sibling folders that are the discs of one release ("Disc 1 - The Original Album",
  "Disc 2 - Live At ...", "CD1", "CD2") are now matched together and filed as one album, its
  discs in "Disc 01", "Disc 02" ... under one folder. Before, folders titled differently were
  matched one by one and could be filed apart or as the wrong release. A disc's sure match
  claims the sibling folders that fit its other discs; otherwise the whole set is searched for.
  The joined album must reach the filing confidence as one album (DSC-10).
- A watched folder waits while a sibling disc is still being copied in.
- Volume folders ("Volume I", "Volume II") of a parent folder whose name says it holds them
  ("Greatest Hits Volume I & II") are one album of 2 discs, never two albums numbered 1 (DSC-9).

**Hybrid SACDs: one disc, whatever layers are listed**
- MusicBrainz lists a hybrid SACD's CD layer, its stereo SACD layer and its multichannel SACD
  layer as media of their own. They are now one disc: a rip of two hybrid SACDs is discs 1 and 2
  of 2, never "1 and 3 of 4" (MED-22). This also holds when the CD layer carries bonus tracks
  the SACD layers lack (MED-22b).
- A rip of an SACD layer is tagged as the disc it is (disc 1 of 2, its own layer as the medium)
  and filed inside the same album as "Disc 01 (SACD Stereo)" or "Disc 01 (SACD Multichannel)",
  beside the CD layer's "Disc 01". Albums already filed this way are read back as they are; an
  SACD-layer rip filed earlier in a plain "Disc 01" is moved once into its layer folder
  (MED-22c).
- Two version folders ("2.0", "5.1") whose files would get the same names are held for you
  instead of being filed side by side with renamed files.

**Folder names for SACD, DVD-Audio and Blu-ray**
- Profiles still holding the edition lines TagMender's scripts carried before 0.8.12 named every
  SACD, DVD-Audio and Blu-ray "CD" ("[2024 CD]" for an SACD rip) and could repeat an edition
  already in the album title ("[2024 SACD] [2024 CD]"). Those exact lines are brought up to date
  when the profile is read, with a backup of the old profile beside it (`.bak`); a line you
  changed yourself is left alone (ED-8). A CD + Blu-ray edition is now named "[Blu-ray]".

**The right Discogs entry, and only that one**
- A Discogs release that MusicBrainz links is used only when it has the same audio discs: a
  3-CD edition no longer takes the fields of the 1-CD bonus EP it links (TAG-4). A Discogs ID
  TagMender wrote earlier that disc shapes prove to be another pressing is removed.
- A Discogs track list whose DVD repeats a suite as an index ("DVD-2.1", "DVD-2.2") no longer
  puts those parts on the CD before it (TRK-6).

**Known limits**
- A hand-written naming script that removes the album's edition another way can still name an
  SACD album "[2024 SACD] [2024 SACD]" (no worse than before).
- Sibling folders named without a number or disc word are not joined; separate albums whose
  folders hold every disc of a box one of them is found only in may be joined as the box.
- In one plan, an SACD-layer copy that also holds a plain disc, planned before the CD layer's
  copy, puts its cover and log in the album folder, and the CD copy's arrive beside them with a
  "~" name. Nothing is lost or overwritten.

## 0.8.67

**Vinyl rips the ripper names**
- A rip whose album tags end in "[Vinyl Rip ...]", or whose notes beside the music say so ("Vinyl
  24bit/96khz rip", "ripped from the original vinyl", "needle drop", "Source: Vinyl"), is now
  matched as a vinyl rip even when the folder name says nothing. Before, its hi-res audio was
  taken for a download. A mention of vinyl is not enough ("also released on vinyl", "the 7"
  was not ripped"), and a rip log that fits the files, or tracks that are whole CD sectors,
  still says CD (MED-21).
- From a set of the album's records and its CD, a vinyl rip is tagged with the records' tracks,
  as a CD rip of such a set is with the CD's. Where the records are listed without lengths, the
  CD's lengths in the same set stand in for them, title by title (MED-21).
- A Discogs "Box Set" or "All Media" entry is no longer counted as a disc: 2 LPs and a CD are
  3 discs, not 4.

**Your folder's number decides the pressing**
- When your folder (or rip notes) names a number that only one release of the album carries,
  and that release's Discogs track list is too sparse to compare (no lengths, or tracks left
  out), it is now chosen, with its tracks taken from a fuller listing of the same album. Before,
  the fuller listing of another pressing won on its lengths (BAR-13). Another release carrying
  the same number, unless your copy rules it out (another barcode, a release date after your
  rip log, a format your files cannot be), leaves the choice to you.

**Steadier checks**
- Two test files no longer run another file's tests a second time, and a timing test reads the
  requests' own times, so the suite gives the same count and result on any machine.

## 0.8.66

**Disc photos read**
- A close-up photo of a disc's hub is now read ring by ring: the text etched or printed round
  it is found and read whichever way it runs, mirrored too (a disc photographed from the
  label side), light on dark or dark on light, on photos as small as 400 px. Covers and
  printed disc faces are recognised and skipped. Every disc photo is read again once (OCR-7).
- A matrix read off the disc ring with a misread letter or two still counts; its digits must
  be read exactly (OCR-8).
- A plant's or country's line alone ("MADE IN GERMANY BY PDO", a side number beside it) no
  longer counts as your disc's matrix, read exactly or not: every disc from that plant
  carries it (OCR-8).

**Twofers and "Original Album Classics" boxes**
- A folder named after the albums it holds ("1992 - The Stranger & An Innocent Man", "Streetlife
  Serenade + 52nd Street") is now planned as one album, the set they were issued in, its discs
  numbered as the release lists them - no longer each album matched to its standalone CD
  (DSC-6).
- A folder of disc folders numbered "Disc 1" to "Disc N" is one set whatever album each disc
  is tagged as, as the discs of an "Original Album Classics" box are (DSC-7).
- A barcode or catalogue number tagged on one disc of such a set only (an earlier run's
  standalone CD) no longer counts against the set (DSC-8), and a set whose one disc lists its
  track times off the sleeve is matched disc by disc (LEN-1).

**The right pressing, more often**
- When your disc shows the matrix of one release, the other releases with the same catalogue
  number or barcode that list a different matrix are left out, however well their track
  lengths fit. Your barcode or disc ID on one of them still wins (EVI-9).
- A catalogue number printed on your scans that a release found by title carries is now
  searched, so every pressing sharing it is compared (CAT-6).
- "Made in West Germany" on your booklet no longer counts against a release whose notes say
  its booklet was made there (CTY-2).

## 0.8.65

**The right pressing, more often**
- Your disc ID now picks the MusicBrainz entry that carries it when that entry lists no
  barcode, links only Discogs releases carrying yours, and Discogs's own pick is one of them,
  over another entry with your barcode (BAR-12).
- A CD whose MusicBrainz release also lists a download bonus track ("Digital Media") is
  disc 1 of 1, not 1 of 2, when your files do not hold that track (MED-20).
- A rip log proven to be of your files (its disc's table of contents, or a CUETools report
  confirming it) and dated in one year now rules out pressings first released in a later
  year, when one not later fits as well and carries what your copy proves (its barcode, disc
  ID or catalogue number): an EAC log from 2005 is never the 2012 reissue sharing the 2002
  CD's barcode. It only chooses: no album is held for the date alone, and a log dated
  before the album existed is ignored (DAT-3).
- Beside a Discogs pick, the MusicBrainz entry that links it now comes along before one of the
  same barcode that links none, and one your rip log rules out comes last (CPT-2).

**Box sets in album-named folders**
- A box set's folder that names its CD count and holds each disc in a folder named as its own
  album (Kenso - Complete Box, Roger Waters - The Collection) is now planned as one album, the
  box, with each disc numbered in the set (DSC-5). Discs of a box still being copied in wait
  until the box is complete.

## 0.8.64

**Move an album into your library when you ask (MOV-5)**
- An album TagMender is sure of, but whose score is below the profile's floor for moving
  files, is still tagged where it is. It no longer reads "Up to date": its Changes column says
  "not moved (score 92 < 95)".
- Album > Move into the Library Layout Anyway (also on the row's menu) looks that album up
  again and plans its move, so you can apply it. On the command line, plan and run take
  --file-below-score. Watched folders and unattended profiles never move such an album.

**Filed on your own catalogue number (FIL-3)**
- A Discogs pick whose track lengths were typed off the sleeve is filed on your folder's
  catalogue number when MusicBrainz's entry of the same pressing agrees on every length
  (Soft Machine - Fifth, Sony BMG 82876872902).

**The right pressing, more often**
- Matrix codes printed round the hub of a close-up disc photo are read, and two Discogs
  entries pressed at different plants are no longer taken for one entry listed twice
  (EVI-8, OCR-6).
- A disc ID found only by a near match in the CUETools database is no longer counted as
  proof of a Discogs release; a Discogs release linked by the MusicBrainz release carrying
  your exact disc ID is (TOC-2). A barcode another tagger wrote no longer outweighs your
  exact disc ID or your folder's catalogue number (BAR-5).
- Folder catalogue numbers such as "RHEADCDX 1" are read (CAT-3).
- A catalogue number with another label's letters in front ("TFCK 87128") is no longer
  taken for yours ("CK 87128", named by your rip logs), which had left a US box set with no
  release that fit (CAT-4).
- When the catalogue number your folder or rip logs name is on no release, a catalogue
  number printed on your scans now picks the pressing, as your barcode does (CAT-5).
- Your exact disc ID on MusicBrainz's entry no longer overrules Discogs's pick of another
  pressing when your folder name or scans speak against MusicBrainz's entry: a disc ID that
  several pressings share proves the disc, not the pressing (TOC-3).

**Boxes**
- A Discogs box whose CDs are numbered "1-01" to "4-14" beside a headed DVD counts its discs
  right (MED-18).
- A CD box with its own LP, ripped as its CDs, is tagged from its CDs instead of being left
  unresolved (MED-19).

## 0.8.63

**A box ripped as its CDs counts its CDs (MED-17)**
- A box of CDs with video DVDs or Blu-rays, ripped as its CDs, now gets a disc total of its
  audio discs: 3 CDs and a DVD are discs 1 to 3 of 3, not of 4. A Blu-ray Audio or DVD-Audio
  still counts, and so does a video disc whose audio you ripped.
- Each CD keeps its own disc number. A video disc that comes before or between the CDs in the
  box stays in the count, so no disc number is ever higher than the total.
- Albums tagged before with the larger total are given the new one the next time they are
  planned. A single CD that came with a DVD is now one disc, so its "Disc 01" folder is no
  longer used.

## 0.8.62

**Credits, more precisely (CRD-1)**
- A credit whose track list is only partly right on Discogs (it names tracks the release does
  not have) is now kept on the tracks it does name, rather than left off altogether. A range
  that starts on a part of a work ("3a to 3k") stays within that work.
- A credit for a video disc (Blu-ray or DVD authoring, a film or video director, editor or
  producer) is no longer written on the files of a rip that has none of that disc. A credit that
  also names an audio disc ("Mastered By [CD & DVD]") or a musical role ("Music By [Film]")
  stays on every track.
- Where MusicBrainz names a track's composers, lyricists or writers, those are written, and a
  Discogs credit for the same track is added beside them under Combine, not instead of them.

**Faster in a large flat folder**
- Looking for an album's "(Deluxe Edition)" folder beside it now reads only the folder names,
  instead of checking every entry of the folder above. In a library of many album folders side
  by side, that was done once for each album.

## 0.8.61

**Credits that no longer belong are taken off (REP-2)**
- When an earlier TagMender job wrote a credit on tracks it does not belong to (before credits
  were limited to their own tracks in 0.8.60), planning the album again now takes it off, or
  puts back what the file held before.
- Only a value TagMender itself wrote, still exactly as written, is taken off. Your own edits,
  another tagger's values and fields you preserve or lock stay. Nothing is taken off when the
  credits were not read in full this time (credits switched off, or a release whose tracks did
  not line up), or when the album is now matched to another release.
- The plan lists what is taken off, and undo puts it back.

## 0.8.60

**Credits go on the tracks they are for (CRD-1)**
- Discogs often limits a credit to some tracks ("Clare Torry - composer, tracks CD1-5, CD2-5,
  CD3-4"). Such a credit is now written only on those tracks, not on every track of the album.
  Position lists and ranges ("A1 to A3", "1-3, 5") are read; a work listed by its movements
  covers them. A credit that names only discs you did not rip (a Blu-ray's chapters) is written
  on no file, and one whose track list cannot be matched is left off rather than spread.
- Credits for the whole release stay on every track, as before.
- Where MusicBrainz names a track's composers, they are no longer replaced by a Discogs credit
  meant for other tracks.

**Plainer words**
- Covers made by an image generation service are now called generated covers, and the setup
  texts and guides describe your tagger settings without naming another program.

## 0.8.59

**The right pressing of a box when two share a barcode (MED-16, CPT-1)**
- Blu-ray chapters that Discogs numbers "BR-1", "BR-2" are no longer counted as tracks of your
  CD rip, when the release lists a Blu-ray. Before, a box numbered that way looked like it had
  extra tracks, and another country's pressing with the same barcode was picked instead. A
  Blu-ray Audio keeps its tracks.
- The MusicBrainz entry written beside a Discogs pick is the one that links that pick. An entry
  that links another pressing comes after it, even when it carries your disc ID, and the
  evidence now says so.

## 0.8.58

**A set whose discs are different releases is stable after it is tagged (IDM-1)**
- A folder holding several releases as disc folders ("Disc 1", "Disc 2", "Disc 3", each its own
  volume) is tagged disc by disc, in place. Planning it again now reads each disc from its own
  folder, as the first plan did. Before, the second plan read every disc with the scans of the
  whole set, so a barcode from one volume's scans could match the other discs to that volume
  (and hold them), or move a disc to a new folder.
- Choosing a release by hand for one disc of such a set now works.

## 0.8.57

**A box of CDs and Blu-rays, ripped as its CDs, is matched (MED-16, TRK-5)**
- In a box set that also holds video Blu-rays or DVDs (TV footage, a film), the video discs'
  chapters, listed as "BD1-3" or "DVD2-1", no longer count as tracks of your CD rip, and no longer
  take the CDs' disc numbers 1, 2, 3. A Blu-ray Audio or DVD-Audio keeps its tracks.
- A work listed in parts ("Esther's Nose Job": "3a" to "3e") with no lengths anywhere is one track
  when your files hold one file per work on every disc. It is then matched and tagged as that one
  track, with the work's title. When your files are the parts, they stay tracks.
- Choosing such a release by hand (`--release`) now works too.

**Planning again after a job plans nothing new (IDM-1)**
- Scans that a job moved into the "covers" folder are no longer taken for front covers. They are
  read in the same order as before, so a barcode found the first time is found again.
- A release listed on MusicBrainz with more discs than you ripped (a CD and its DVD-Audio) is
  tagged the same way whichever database's pick leads. Before, the second plan could switch to
  other titles and another folder name.
- An album tagged where it is, with a file renamed, takes its disc folders' rip logs and scans in
  the same plan, rather than leaving them for the next one.

## 0.8.56

**A crash while tidying up after a move is recovered (DIR-2)**
- When a move empties a folder, TagMender removes it, and the empty folder trees inside it,
  afterwards. Each folder is now noted in the job's journal before it goes. If the program or
  the computer stops part-way through, recovery puts the album back with every folder it had
  removed (undo already did). Folders you have since put files in are left as they are, and a
  box set's other disc folders and the box folder stay untouched.
- Not tested: a crash on a network share, and a crash during undo.

**Scans are only "all read" when they were (SCN-1)**
- The scan limit (8 images at "quick" OCR) is counted on your folder's distinct images. An exact
  copy of a scan ("scan 1 (copy).jpg") is no scan left unread, and a scan past the limit is no
  longer taken as read. The plan now shows how many images were found, how many were copies, how
  many were distinct, and how many were read.
- A scan whose reading failed is no longer stored as a blank page. Blank pages stored that way
  by earlier versions are read again once.
- "Investigated" says how many scans it read again and why it stopped, instead of "every scan".

## 0.8.55

**A folder's year is the album's year (YR-1)**
- A folder named by the album's original year ("1988 - Zodiaque") no longer counts against a
  reissue of that album: the reissue agrees through the album's first release (MusicBrainz's
  release group, Discogs's master). A pressing year named in the folder ("[2004 RM]") is still
  compared with the pressing's own year.

**What TagMender wrote does not vouch for itself (PRV-1)**
- The shop page TagMender's own web search found (TAGMENDER_SOURCE) is no longer read back as a
  link your files came with, and ISRCs it wrote are read as they were before its job.

**Empty folders left behind by a move (DIR-1)**
- A move's clean-up also removes empty folder trees inside the folder it empties (a download's
  empty "Disc 2/Live albums/..."). Any file in them, a hidden one too, keeps them; undo makes them
  again.

**Releasing**
- The release checks every contributor follows are in docs/RELEASING.md.

## 0.8.54

**Both databases' entries of your release in one run (CPT-1)**
- When Discogs's pick is taken over a disputed MusicBrainz one (Sources prefers Discogs), the
  MusicBrainz entry of that same pressing - the one linking it - is written beside it at once,
  not on a later run.
- When MusicBrainz is sure and Discogs is not, Discogs's entry of the same release comes along
  only when it is supported: MusicBrainz links it, or it is the one download of that release in
  your files' own format (the FLAC entry, never an MP3-only one).

**The release your barcode finds is always compared (RET-1)**
- Releases a search by your own barcode or catalogue number finds are always looked at, however
  their titles rank - a retail CD was missed while a club edition of the same barcode was taken.

**The barcode written is a barcode (BAR-11)**
- Where Discogs lists the number printed under the bars (a catalogue number) before the real
  barcode, the real barcode is written.

**Club editions (CLB-1)**
- A club edition whose own mark ("CRC") is not on your back cover - read well enough that its
  barcode was decoded and its text read - gives way to the release carrying your barcode with
  nothing against it. A scan that is missing or was not read decides nothing.

**Release checks**
- A release now counts as verified only after GitHub's tests, packaging and publication pass for
  the tested commit and the published AppImage is checked.

## 0.8.53

**Build checks fixed**
- Three artist-photo tests picked "the moved album's track" by its old file name. Since files are
  renamed ("01 p.flac" -> "01.P.flac"), the other album's tracks could be picked instead, depending
  on the order the build machine lists files - the cause of the failing builds of 0.8.48, 0.8.51 and
  0.8.52. They now pick the moved album by its folder. TagMender's behaviour is unchanged.

## 0.8.52

**A country in the folder name counts again when the title shares its word (FLD-1)**
- A folder bracket is ignored as the release's own title only when the title itself has that same
  bracket ("The Sessions Box Set (United Kingdom & Ireland 2022)"). "Made in Japan (Japan)" or
  "Live in Germany [Germany]" again say where the copy was pressed.

**The run report says "taken back" only when something was**
- An album that could not be identified no longer reads "what an earlier run wrote for it was
  taken back" when nothing was: only when an earlier run's tags really went back.

**Catalogue numbers in three groups are read from folder names (CAT-2)**
- "[A&M_75021 5350 2]", "75021-5350-2" or "31454 0123-2" (A&M, PolyGram and Universal US CDs)
  now count as the catalogue number the folder names; an underscore stands for a space.

**A rip group's name is no release country (CTY-1)**
- A release country that is no country but the same word the files carry as their label or
  comment ("PMEDIA") is removed when the album is tagged and its release names no country.
  Real country names and codes are never touched.

**Matching rules**
- The Wikipedia track-listing rule is now WEB-3 (WEB-2 named two rules).

## 0.8.51

**The run report says what happened to an album held for you**
- An album held for your choice (not identified) whose job went through - an earlier run's tags
  for a release TagMender withdrew taken back - is listed as "Unresolved", with what was taken
  back, never as "Failed".

## 0.8.50

**TagMender's earlier choice is history, not evidence (TAG-1)**
- A pressing TagMender chose before keeps its standing only while what decided it is still found
  on your copy. A deciding code your scans no longer show (an OCR misreading) no longer holds the
  album to that pressing: it is weighed afresh, and tagged only as far as your copy now proves -
  album tags only when nothing decides, with the earlier pressing's tags taken back. A clue that
  simply could not be checked this time (scans not read, a database down) keeps the choice.
- A catalogue number of digits only counts when printed as that number, not inside a longer run
  of digits the OCR produced from noise.
- A folder or album title naming an instrumental or karaoke version that no release names is held
  for you to choose, never tagged as the vocal album; what an earlier run wrote for it is taken back.

**Disc numbers in one layout (TRK-4b)**
- A set one database lists as one run (a 2LP's A1..D2) and the other disc by disc is numbered in
  one layout - the one your files follow - never "disc 2, track 4 of 6". Disc numbers an earlier
  run added are taken back; your own stay.

**Barcode digits (OCR-5)**
- Digit groups are joined into a barcode only when they sit on one line of one reading: no more
  barcodes made up from two separate readings. Disc rings and barcode digits stay read in grey on
  purpose: reading them in colour lost real codes and added false ones on real scans (OCR-4).

## 0.8.49

**What your copy prints decides (TAG-3)**
- A matrix / runout read whole on your copy's scans (a vinyl label, a disc) that the release - or
  its Discogs twin - lists, and no other pressing, proves that release: its tags replace a
  pressing your files named before. Never a matrix typed in notes, part of one, or one another
  pressing lists too.
- For a vinyl rip (its lengths are never compared): a catalogue number printed on your copy that
  only one pressing carries proves it.
- A release ID your files hold whose release lists another number than the one printed on your
  copy is removed, also when the pressing itself stays unproven (album tags only).
- Discogs releases that MusicBrainz's candidates link are looked up too when the result is unsure
  or your files name another release: a pressing Discogs's search missed can now be found.

## 0.8.48

**Your scans are read properly again (OCR-3)**
- Covers are read in colour at their own resolution: print in a single colour channel (a blue
  vinyl label's catalogue number and matrix) was lost when scans were turned grey first.
- Each scan is read in its own script: Latin covers with the Latin languages only, Japanese or
  Cyrillic ones with their own language plus English - no more stray kana or Cyrillic in what is
  read off a Latin sleeve. Every scan is read once more.

**Box sets (DSC-3, DSC-4, MED-14, MED-15)**
- Disc folders of a box written as "CD02-03 <title>/Part 1" and "Part 2" are discs 2 and 3 of the
  box: the whole box is identified and filed as one album.
- A track total every disc of a set repeats (74 on each of 10 discs) is the album's total.
- "[... 10CD Box]" in every disc's album tag counts as a CD rip, unless the audio says otherwise.
- Each disc of a Discogs release that mixes formats gets its own media (a CD box with Blu-rays
  is no longer named or tagged "Blu-ray").

**Bootlegs (R9, TIE-4, ED-7)**
- A bootleg MusicBrainz or Discogs lists is tagged as far as your copy proves it: its own
  barcode, catalogue number, matrix or disc ID makes it the release (with a note that it is
  unofficial); without such proof only the album's own tags are written. An official release
  carrying the same barcode or catalogue number always wins over a counterfeit.
- A bootleg is not named "Remaster" or "Reissue".

**Names and titles (NAM-9, NAM-10, NAM-11)**
- Capital initials in your tags ("NHØP") are matched to the credited name they spell.
- A sort name always comes from the same source as the name it sorts.
- With title translation on, a title given in two languages ("「森の組曲」 = "Forest" Suite") is
  written by its Latin side.

**More**
- A folder name bracket that is part of the release's own title is no country or label evidence
  (FLD-1).
- An earlier wrong pick of TagMender's own is corrected in one run (TAG-3).
- Barcodes are written as their digits, the form Discogs and MusicBrainz searches find (BAR-10).

## 0.8.47

**One release per file (TAG-3)**
- Your files no longer end up naming two pressings. A MusicBrainz or Discogs release ID already in
  your files that names another pressing than the one TagMender identifies is corrected - but only
  on evidence that they are two pressings: a different barcode, catalogue numbers that do not
  agree, another format (CD, vinyl, download), MusicBrainz linking other Discogs entries, or a
  MusicBrainz disc ID that fits no disc of your copy (read from its rip log or cue sheet).
  Missing data or a database outage never removes or replaces an ID; IDs of the same pressing in
  the two databases stay as they are.
- When your copy proves the new release (the barcode on your scans, or your own number or disc
  with every track length agreeing), its tags replace the old pressing's - date, media, country,
  label and catalogue number included - even below the replace threshold. When nothing proves it
  and the score is below that threshold, only the album's own tags are written (album only), and
  pressing tags TagMender itself wrote earlier for the other pressing are taken back.
- The Sources setting ("MusicBrainz/Discogs preferred") chooses whose release is written; it
  removes the other database's ID only by the same evidence.
- A second plan of an album keeps IDs a later TagMender run left unchanged as TagMender's own
  (REP-1), instead of reading them as another tagger's.

**What your scans say (MED-10, EVI-7)**
- "Made in Holland", "England", "Finland", "Poland", "Switzerland" and "Ireland" are read as
  countries again, also when more text follows on the next line.
- "Made in the EU" agrees with a UK, German or other European pressing as much as with one listed
  as "Europe": it no longer picks one of two European pressings over the other.
- A label named as the album's artist is no clue: the artist's name is printed on every sleeve.

## 0.8.46

**The edition your copy names (EDN-2)**
- A folder naming its edition by year and label ("(2017 AP ISO)", "[2013 MFSL]") or an SACD
  rip log naming its disc image after the label keeps a pressing of another label and year
  from being tagged as yours: the album is tagged without that pressing's label, country and
  date. A pick on the label you name stays.

**Versions tagged apart are one album (MIX-3)**
- A disc's stereo and multichannel folders whose tags differ only in a format mark ("[SACD 2ch
  DSD64]" beside "[HD]") are planned together again, with the set's name, scans and log.

**No barcode from a hex dump (OCR-2)**
- A ripping tool's hex dump in its log is no longer read as a barcode.

**Your catalogue number and the track lengths (LEN-3)**
- A catalogue number printed on your copy excuses track lengths that disagree whether half or
  most of them do; one only typed in notes, or part of a longer printed number, never does.

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
- **Make Covers…** makes covers from four presets (Faithful, Creative, Art, Era) with your own
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
