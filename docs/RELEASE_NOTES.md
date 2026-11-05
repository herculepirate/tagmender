# Release notes

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
