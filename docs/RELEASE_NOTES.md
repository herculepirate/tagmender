# Release notes

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
