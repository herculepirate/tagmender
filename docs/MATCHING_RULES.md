# Matching rules

The decisions TagMender makes when it identifies an album, chooses its release (pressing), tags
it and files it. Every rule has an ID. Code that applies a rule cites the ID in a comment
(`# BAR-1`); its tests carry the ID in their class or test name; the reason shown in the app
may name it. When the code and this file disagree, this file is the intended behaviour and the
code is the bug. A case no rule covers is asked, then added here.

Status: **in code** (implemented, with the test named), **to verify** (believed implemented,
no test traced yet).

Terms: *album* = the work (artist, title, tracks); *pressing* = one release of it (label,
catalogue number, barcode, country, media). *Album known* = tagged with what every candidate
pressing shares (album, artists, titles, original date), without pressing details (release IDs,
label, catalogue number, barcode, media, country).

## Precedence

When rules point different ways, the earlier group wins:

1. Barcode (BAR)
2. Other evidence read off your copy (EVI), the edition your folder names (EDN), box sets (BOX)
3. What the audio and folder say about the media (MED)
4. Your earlier choice (TAG)
5. Source preference (SRC), then country order and ties (TIE)

## Barcode

| ID | Rule | Status |
|---|---|---|
| BAR-1 | A barcode from your copy that matches a release makes that the release, even when the audio could not come from its media (a CD barcode on 24/96 files is still that CD). | in code (`classify`) |
| BAR-1b | A release proven by your scans (its barcode or catalogue number printed on them, nothing on them against it) is filed even below organize_min_confidence when some track lengths differ (a vinyl rip's run-in and gaps), at a score of at least 90 (replace_min_confidence), when the files are the release's tracks (none missing) and at least half their lengths agree. Not with Extra-careful matching. | in code (`plan._files_on_evidence`); tests `BAR1_*` |
| BAR-1a | When your barcode is on several releases (a hybrid SACD listed once as SACD and once as CD), the one whose media can hold your audio is taken. | in code (`classify`, `_yields_to_audio`) |
| BAR-2 | Your barcode on one database's unsure pick beats the other database's sure pick that does not carry it, whatever the Sources preference: the barcode's release is taken. When two entries of that database share the barcode, HF-2 applies. When both picks carry the same barcode, the sure one stands. | in code (`_proof_left_unsure`, `_barcode_taken`); tests `BAR2_*`, `SurePickNotDisputed` |
| BAR-3 | Two barcodes of yours naming two releases: the barcode read on the outer packaging (back cover, box, slipcase, o-card) wins over one on a disc, booklet or inlay. When that cannot be told, HF-2 applies. | in code (`barcode_on_outer`, `_outer_barcode_pick`); tests `BAR3_*` |
| BAR-4 | A barcode on your scans that is on no release in either database, nor sold in the shops (a shop sticker), is ignored. One that was not searched, or whose search failed, still says another pressing. | in code |
| BAR-5 | A barcode printed on your scans beats a different barcode in your files' tags. | in code (`score_candidate`, scans' barcode as proof, tags' alone weak); tests `BAR5_*` |
| BAR-6 | When your own barcode (or catalogue number) proves the release and the release holds exactly as many audio tracks as your folder, a larger track total in your files' tags (counting a DVD in the set, or another edition) never holds the album back: it is told in the notes. | in code (`match_albums._copy_disagrees`); tests `BAR6_*` |
| BAR-7 | Your copy's own barcode on the release (from your files as ripped, your scans, a cue sheet or booklet; never one another tagger wrote), with every track there, artist and title agreeing and the album itself fitting as BAR-8's (half its lengths or half its track titles), decides the release whatever the track lengths say (a remaster, lengths printed otherwise): neither the lengths nor the score they lower hold it back, as an agreeing catalogue number already excuses them. A barcode on a release none of whose tracks fit by length or title (half of neither) is another album's and decides nothing. | in code (`classify`); tests `BAR7_*` |
| BAR-8 | Your copy's own number proves the release, so nothing else about names holds it back: with your barcode on it, every track there, the artist agreeing and the album itself fitting (half its lengths or half its track titles), a title your tags write otherwise (Guru Guru - UFO, tagged "UFO/ Hinten" by another tagger) and a version your folder does not name (Live & Unreleased) are told in the notes; and a catalogue number printed on your copy (a scan, a PDF booklet - never only typed in notes or a saved page, and whole, never inside a longer one, never the start of one printed on - "SICP 10151~4", "ECM 1064/65", "SRCS-1234-5" - and never joined across a line break; a number the release itself lists with its slash, "2230/31", is still found as "2230 31" or "2230/31") is your own number, as one in your tags or folder name is (Makarski & Jarrett - Bach, ECM 476 4582: 89.6). | in code (`classify`, `catno_printed`); tests `BAR8_*` |
| CAT-1 | An SACD's audio format written in a folder name or album tag ("DSD64", "DST64", "DSD128", "DXD", "PCM", "MCH", "ISO") is never a catalogue number: it is not searched, and never "on none of these pressings" (Blood, Sweat & Tears: "[SACD DSD64 2ch Rip 24bit 352khz]" held five albums back). | in code (`match_core._NOT_CATNO_WORD`); tests `CAT1_*` |

## Evidence from your copy

| ID | Rule | Status |
|---|---|---|
| EVI-1 | Weight of evidence, strongest first: barcode > catalogue number > disc ID / matrix (runout) > label or country printed on the scans > folder name > country order. | in code (`CLUE_WEIGHTS`); tests `EVI1_*` |
| EVI-2 | Evidence shared by every candidate (a catalogue number on all editions) tells nothing apart; so does a release country (or media) in your tags that none of the tied pressings has (another tagger's GB on an album out only in DE and RU): it never outweighs your disc ID or stops the country order. A download entry that lists no catalogue number is unknown, not different - also when your scans (a CD sleeve bundled with a download) print another pressing's number. | in code (`_weigh`, `choose_pressing`); tests `ScansNumberAgainstAnUnlistedOne`, `EVI2_*` |
| EVI-3 | Text read off the scans (for example "Made in EU") beats the country order. | in code |
| EVI-4 | Poor OCR (few words of artist or title read) never lowers the confidence: scans only add evidence. | in code (`score_candidate`, OCR only adds); test `EVI4_*` |
| EVI-5 | When your own barcode is on every tied pressing, what your audio says against them all alike ("hi-res audio cannot come from this CD": a 24/96 download carrying the CD's UPC) tells none of them apart and does not stop the tie-break (BAR-1: the barcode names the product whatever the audio). Without your barcode on them all, it stands (an unlisted download: Album only). | in code (`choose_pressing`, `_MEDIA_AGAINST`); tests `EVI5_*` |
| EVI-6 | When your evidence (a catalogue number on your scans, ...) is on some tied pressings only and does not single one out, the tie-breaks (one pressing listed twice, the country order, the original date) choose among those, never among the others: Raising Sand's scans name Rounder 1166190752, on the US (listed twice) and ZA entries, not the CA one. | in code (`_choose_pressing`, `_country_listed_twice`); tests `EVI5_EVI6_*` |
| TOC-1 | Your disc's table of contents (from the rip log or cue sheet) fits your files to the sector on every disc, and MusicBrainz has each of those disc IDs attached to the release: the release is proven though the lengths it lists differ (typed or rounded) and though the folder names the artist otherwise (credited as the database does). Every disc of your files needs its TOC, and each disc ID must be on that disc of the release. Its title must agree with yours (and, when the artist is credited otherwise, its track titles too), three tracks or more, the track counts the same; a fuzzy disc match or a log of another rip is no proof. Filed on it below organize_min_confidence when it is clear of the runner-up (not when pressings sharing the disc ID tie and the country order chose), never with Extra-careful matching. | in code (`match_core.toc_lengths`, `classify`, `plan._files_on_evidence`); tests `TOC1_*` |
| LEN-1 | A release that lists EVERY track a few seconds short of your files (none more than 1 s longer or 8 s shorter, most of them short at all, four tracks or more) agrees on lengths: times copied off a sleeve. Another edit or version is not short on every track. | in code (`match_core.sleeve_times`); tests `LEN1_*` |
| FIL-1 | An exact release is filed below organize_min_confidence when your copy's own barcode or catalogue number (its tags, the folder name; not another tagger's) is on it, or your disc ID chose it over the other pressings, and every track length agrees - unless the pressing itself was a guess (country order, original pressing). The score is low then only for a folder title saying more than the database's, or for the higher-scoring pressings the disc ID ruled out. | in code (`plan._files_on_evidence`, `classify` own_number / guessed_pick); tests `FIL1_*` |
| FIL-2 | An exact pick your own barcode or catalogue number proves (the folder name's, the tags', the scans'), every track there and nothing against it, is filed when the release lists no track lengths at all: nothing disagrees, and its score is low only for the lengths it cannot compare (Mark Nauseef - Sura, JICK-89728: 76.9). Not when the pressing was a guess among several. | in code (`summarize` "number_proven", `plan._files_on_evidence`); tests `FIL2_*` |
| TIE-4 | An unofficial release (bootleg, CD-R) that nothing of your copy backs never wins a tie against an official release that fits as well: a bootleg copies the official tracks and lengths. Throughout the tie, too: such an unofficial pressing never takes part in a tie-break (its copied lengths, its country) while an official one is tied. | in code (`match_core.classify`); tests `TIE4_*` |
| TIE-5 | A runner-up your copy rules out never holds the pick back: a bootleg TIE-4 set aside, or a vinyl or download release against a CD rip (whole CD sectors, a rip log), unless your barcode or catalogue number is on it, or the pick is ruled out as well. It is told in the notes. | in code (`classify`); tests `TIE5_*` |
| TIE-6 | One disc entered several times in MusicBrainz, its label and catalogue number written otherwise ("Atlantic" / "Megaforce Worldwide", "7818252" / "7567818252": King's X - Out Of The Silent Planet), is one pressing when every entry carries your barcode and your disc's exact disc ID, of one country, format and year (or undated: a reissue years later reusing both is not merged): the entry of your folder's year, then the fuller one, is used. | in code (`same_pressing_listed_twice`); tests `TIE6_*` |
| BOX-2 | One disc of a set in a folder of its own, matched to its disc of the release, is tagged from that disc's tracks (disc N of the set). | in code (`engine._identified`); tests `BOX2_*` |
| BOX-3 | One disc of a set in a folder of its own, when the release lists no lengths for that disc (Elvis Presley - FTD The Wonderful World of Christmas, "CD 1" ripped alone): your copy's own barcode is the release's, exactly one disc of it has your files' track count and every one of your titles in order, and any length the release does give agrees within 3 s. It is then tagged and filed as that disc of the set (BOX-2). A barcode another tagger wrote is no such proof. | in code (`match_core._box_disc`); tests `BOX3_*` |
| NAM-4 | A folder name never keeps a bracket left open (the folder album name's word limit cut through it): the opener and the words after it, up to the next bracket, are dropped. | in code (`filenames.balance_brackets`); tests `NAM4_*` |
| NAM-5 | On a share that sees one name in two letter cases (SMB/CIFS), an album folder already there whose name differs only in case is used as it is spelled on disk: the files stay in it, never "taken" by themselves and given a suffixed second name (10cc - "The Very Best Of 10cc" against the script's "The Very Best of 10cc"). | in code (`plan.Planner._spelled`); tests `NAM5_*` |
| NAM-6 | A folder the plan would create that differs from one already on disk only in its kind of quote mark ("King’s X" from MusicBrainz, "King's X" from Discogs) is the folder on disk: the album is filed there, as it is spelled. Within one plan, the first spelling is used for the rest. | in code (`plan._folder_key`, `Planner._spelled`); tests `NAM6_*` |
| NAM-7 | The top folder the naming script files an album in (the artist's) is the one the library already keeps under another spelling of that name, when the spellings differ only in "and", "&" or "+", commas, quote marks, letter case, or a full stop closing a word of two or more letters ("Mr. Mister" and "Mr Mister"; "Blood, Sweat & Tears" from MusicBrainz, "Blood, Sweat And Tears" from Discogs, "Blood Sweat & Tears" as a sleeve credits it). Any other full stop separates like a space: initials stay initials ("M.I.A." is "M.I.A" or "M I A", never "MIA" or "MIA."; "t.A.T.u." is not "Tatu"; "R.E.M." is not "REM"). Of several such folders, the one holding the most entries is used, so the albums gather in one folder. Album folders are never folded so. Tags keep each database's spelling. | in code (`plan._loose_key`, `Planner._usual_folder`); tests `NAM7_*` |
| NAM-8 | A collaboration keeps its whole credit as album artist (and folder): NAM-1 cuts a joint credit to its first artist only when its join names a lead ("feat.", "featuring", "ft.", "with", "presents", "introducing": "Robin Trower featuring Sari Schorr" is "Robin Trower", whatever the member lists say), when a database lists that artist as a member of another act the credit names - a leader with their own band ("Thomas Strønen & Time Is A Blind Guide") - or when the first artist is a person (MusicBrainz's type) and another act is a group no database lists members for; two independent acts or musicians ("Calexico and Iron & Wine", "Jon Balke, Per Jørgensen, Audun Kleive") and a duo named after its members alone ("Jimmy Page & Robert Plant", a MusicBrainz group) stay as credited. Equal joins ("&", "and", ",", "/", "x", "vs") keep the rule above. With no IDs to ask by (or a database not consulted, or MusicBrainz knowing no such artist), the first artist, as before; one database's definitive answer (MusicBrainz: a person, or a band's members) stands though the other is out of reach; only when no database answered for an artist the decision depends on does the album artist your files hold stay; your own "file under" name for the first artist goes first. Partners before a guest ("Pat Metheny & Lyle Mays with Nana Vasconcelos": the lead join after an equal one) keep the whole credit - never cut to the first partner. | in code (`album_artist._equals`, `leader_of`); tests `NAM8_*` |

## Album known or full pressing

| ID | Rule | Status |
|---|---|---|
| ALB-1 | Every track title matches (your shortened title, the opening words of the release's, counts) and at most one track length is off: the best pressing is tagged in full, even up to 5 points under the confidence cut-off. | in code (`_one_length_off`); tests `ALB1_*` |
| ALB-3 | An album whose pressing alone is open (a runner-up of the same album within the margin) is tagged as Album only when the pick has the lengths an automatic pick needs (auto_min_duration_frac, 80%), not only when every length agrees (Egberto Gismonti - Corações Futuristas: 7 of 8, two EMI CDs whose numbers nothing of your copy shows). | in code (`album_only`); tests `ALB3_*` |
| ALB-2 | Your files have more tracks than the release (a bonus or hidden track): the pressing that lists them all; if none does, a person decides. | in code; test `ALB2_*` |
| ALB-3 | Album known writes the album's original release date. | in code; test `ALB3_*` |

## Media and audio

| ID | Rule | Status |
|---|---|---|
| MED-1 | 24/96 or 24/192 files, a download is listed, and no SACD, DVD-Audio or Blu-ray is: the download. | in code |
| MED-2 | 24/96 files, only CD and vinyl listed, nothing says vinyl: album known. | in code; test `MED2_*` |
| MED-3 | 24/88.2 or 24/176.4 (DSD64 converted), DSD files (.dsf/.dff) or an SACD area tag, with an SACD listed: the SACD, over a download. | in code (`audio_evidence` "sacd", `sacd_media`, `file_clues`); tests `MED3_*` |
| MED-4 | 24/96 or 24/192 files when both a disc that can hold them (SACD, DVD-Audio, Blu-ray) and a download are listed, with nothing else of yours: album known, every shared tag, Grouping = HD Audio. | in code (`choose_pressing`); tests `MED4_*` |
| MED-5 | Vinyl only with evidence (folder name, your tags, a number of yours, lengths only vinyl fits). Between vinyl pressings: evidence from your scans, then the country order. | in code (0.8.20) |
| MED-6 | Cassette only with evidence (folder name "[Cassette]", "tape rip"); Grouping = Tape Rip. | in code (`marks.of_album`); tests `MED6_*` |
| MED-7 | No vinyl or cassette evidence and a CD fits: the CD. | in code (0.8.20) |
| MED-8 | Grouping: HD Audio for lossless above 16 bits or 44.1 kHz (DSD included); Vinyl Rip; Tape Rip. Written only when Grouping is empty. | in code (`marks.py`) |
| MED-9 | Lossy files (MP3, AAC, Opus): the same rules, without the audio as a clue; no Grouping mark. | in code |
| MED-10 | Lossless files (FLAC, ALAC, WAV) are never a download sold only as MP3, AAC or another lossy codec: such downloads leave a tie, and a download whose format names your files' kind agrees. A CD-R lists no moulded matrix: a pressed matrix read on your disc that another tied pressing lists counts against it. "Made in the EU" on your scans agrees with a UK, German or other European pressing. | in code (`file_clues`, `choose_pressing`, `pressing_clues`); tests `MED10_*` |
| MED-11 | A DVD, Blu-ray or VHS in a set (its film or videos, not audio) is no disc of a CD rip: the disc count compared is that of the discs with audio tracks (Popol Vuh - Kailash: 2 CDs and a DVD, ripped as 2 CDs). | in code (`match_albums.discogs_candidate`); tests `MED11_*` |
| MED-12 | A release that is video only (DVD, Blu-ray, HD DVD, VHS, LaserDisc, UMD; not DVD-Audio or Blu-ray Audio) is never the source of a CD rip: tracks of whole CD sectors or a CD rip log argue against it, and a person decides (AC/DC - Live At Donington: the concert DVD, its songs listed as chapters without lengths). Left alone: audio not known to come off a CD (a restoration of the film's sound), a folder named DVD or Blu-ray, and tracks of whole sectors that are also whole PAL video frames (1764 samples) with no rip log: a DVD's sound cut at its chapters. | in code (`match_albums.discogs_candidate`, `match_core.file_clues`); tests `MED12_*` |
| MED-13 | Hi-res audio the folder's name ("SACD", "DSD64") or an SACD ripping tool's log in the folder (SACD-Ripper, sacd_extract, ISO2DSD: it stays when the folder is renamed) says came off an SACD is an SACD rip, also when converted to 24/96: Discogs is searched for the SACD first (it files an SACD under its own format, never CD), the SACDs found are fetched however other entries rank, and an SACD release gets the format clue. | in code (`match_albums.sacd_marks`, `discogs_search`, `match_core.file_clues`); tests `MED13_*` |

## Editions, box sets, singles

| ID | Rule | Status |
|---|---|---|
| EDN-1 | An edition year in the folder name ("[2015 Remaster]") picks that edition unless evidence from your copy says otherwise. | in code |
| BOX-1 | Discs that together match a box set are tagged as the box set, never as the standalone release of one disc. | in code; test `BOX1_*` |
| SNG-1 | A folder of one track or a short EP is matched to the single or EP whose track list it is, never to the album holding the track. | in code; test `SNG1_*` |
| BOOT-1 | An unofficial release (bootleg) is tagged in full from that release, marked with release status bootleg. | in code (MusicBrainz status; Discogs "Unofficial Release" in `discogs_mapping`); test `BOOT1_*` |

## Edition suffix (folder name and Album tag)

| ID | Rule | Status |
|---|---|---|
| ED-1 | An SACD, DVD-Audio or Blu-ray is always marked with the pressing's year: "Fascinoma [1999 SACD]". | in code (`naming.edition`); tests `ED_*` |
| ED-2 | A vinyl or tape rip is always marked with the pressing's year: "[1977 Vinyl Rip]", "[1984 Tape Rip]". | in code; tests `ED_*` |
| ED-3 | A CD or download is marked only when it is newer than the album's original release: "[2015 Remaster]", or "[2015 Reissue]" when the release calls itself a reissue and not a remaster. The original CD or download has no suffix (no plain "[WEB]"). | in code; tests `ED_*` |
| ED-4 | The suffix is written in the Album tag as well as the folder name, once. A naming script with its own edition lines gets TagMender's edition in their place. | in code (`naming.with_editions`); tests `ED_ScriptWithItsOwnEditionLines` |
| ED-5 | Hi-res audio (lossless above 16 bits or 44.1 kHz) on a CD-type or download edition adds HD: an original hi-res download or an album known gets "[HD]"; a hi-res remaster or reissue gets "[2015 Remaster HD]". SACD, DVD-Audio, Blu-ray ("[1999 SACD]") and vinyl or tape rips ("[1977 Vinyl Rip]") never add HD. Grouping = HD Audio as MED-8. | in code (`naming.edition`, `plan` ~edition_hd); tests `ED5_*` |

## Ties

| ID | Rule | Status |
|---|---|---|
| TIE-1 | Pressings tie and nothing of yours tells them apart: the country order picks one and it is tagged in full (not album known). Only when the country order, the original date and your preferred release types and formats all leave a tie, or when your copy's kind (CD, download, vinyl) is not among the tied pressings, is it album known. | in code (`classify`, `combine`); tests `TIE_*` |
| TIE-2 | The country order comes before the original release date; the date breaks a tie only within one country. All other evidence is weighed before either (the evidence pass). | in code (`prefer_country`, `choose_pressing`, `evidence_pass`); tests `TIE_*` |
| TIE-3 | Discogs's "UK" and "EU" are GB and XE for the country order and for "Made in ..." on your scans. | in code (`country_code`); tests `TIE3_*` |

## Earlier tags

| ID | Rule | Status |
|---|---|---|
| TAG-1 | Files TagMender tagged earlier keep their release unless evidence from your copy points to another; a better score alone does not change it. | in code |
| TAG-2 | Release IDs written by another tagger or a shop are ignored: the release is decided afresh. | in code |

## Sources

| ID | Rule | Status |
|---|---|---|
| SRC-1 | The preferred database's sure pick stands against the other's unsure pick, except where BAR-2 applies. | in code (`_proof_left_unsure`) |
| SRC-2 | An album only Discogs knows is tagged from Discogs in full; MusicBrainz IDs stay empty and are never invented. | in code |
| WEB-1 | When neither database establishes the album, the web is searched next, also with Unresolved set to "leave". With "leave" what it finds is held: shown in the preview (HELD, unticked) and written only when you tick it and apply; a watched folder never applies it, and it keeps the Unresolved retry schedule. | in code (`engine` web step, `plan.held`); tests `WEB1_*` |
| GNU-1 | A gnudb entry that is gnudb's exact match for your CD's table of contents (one disc; same track count, every track length agrees; at least 80% of your files' track titles agree in order) is a web album: Partial tags from it (album, artists, track titles, genre, year), never a release ID, label, barcode, catalogue number or media. A fuzzy gnudb match stays a clue only. | in code (`engine.gnudb_candidates`); tests `GNU1_*` |
| WEB-2 | The last search, when nothing else established the album (not even album known): its artist and title searched once on the web with the profile's search engines (none set up: skipped). Only links are kept - MusicBrainz and Discogs release pages (3 of each at most, only from a database that answered for this album), fetched and scored like any other match, and Bandcamp album pages for WEB-1's Partial tags; nothing is taken from a result's own text. | in code (`match_albums.title_web_step`, `engine._web`); tests `WEB2_*` |

## Names and fields

| ID | Rule | Status |
|---|---|---|
| NAM-1 | Album Artist is the lead artist only ("Thomas Strønen & Time Is A Blind Guide" -> "Thomas Strønen"; a band named after a member -> that member; a band credited after its members, "Thomas Strønen / Iain Ballamy : Food" -> the band, "Food"). The Artist tag keeps the full credit. The folder follows the Album Artist. | in code (`album_artist.py`, on by default); test `NAM1_*` |
| NAM-2 | Compilations and classical albums are filed as the profile's naming script says. | in code (naming script) |
| NAM-3 | Artist names and track titles are written as the database spells them, not as your files do. | in code; test `NAM3_*` |
| GEN-1 | Genre is filled as the profile's genre settings say. | in code |

## Fields and dates

| ID | Rule | Status |
|---|---|---|
| SRC-3 | Fields follow Sources: Discogs only or MusicBrainz only take that database's fields alone. With one preferred, its fields are written first and fields only the other database has are added (MusicBrainz preferred: MusicBrainz tags plus what only Discogs has; Discogs preferred: the reverse). | in code (`enrich`, `SOURCE_MERGE`); test `SRC3_*` |
| DAT-1 | Date holds the pressing's own date; Original Date the album's first release (a 2015 remaster of a 1987 album: Date 2015, Original Date 1987). | in code; test `DAT1_*` |
| DAT-2 | Dates are as precise as the database gives them: 2015-03-23, else 2015-03, else 2015. | in code; test `DAT2_*` |
| CLS-1 | Classical: Composer, Work and Movement (name and number) tags are written where the database gives them; titles as the release has them. | in code (`mb_tags._work`, `_movement`); tests `CLS1_*` |
| ART-1 | Your own cover (scan or embedded art) is kept; a downloaded front is used only when you have none or yours is small (under about 500 px). | in code (default `artwork.replace` larger_only, 500 px); tests `ART1_*` |

## Discs in separate folders

| ID | Rule | Status |
|---|---|---|
| DSC-1 | Discs of one release in separate folders ("Album CD1", "Album CD2") are matched as one release and filed as one album. A watched folder waits for the missing disc before filing: every disc your files' tags count, or, when only the release gives a count (it may hold a DVD no audio folder brings), a gap below the highest disc there. | in code (`engine.sibling_disc`, `_disc_sets`, `watcher.missing_discs`); tests `DSC1_*` |
| DSC-2 | The disc folders of one set ("CD1" ... "CD4" in "Holy Diver [2022 RM DLX 4CD]") are one album even when each disc is tagged with an album title of its own (the remix, the live show, the outtakes): every track lies in a disc folder of the set's folder, each disc folder is tagged as one album with one disc number, all by one album artist, and no two disc folders name the same disc number. Two artists' albums, two folders claiming one disc, a disc without a disc number, an untagged disc, or a folder with tracks of its own beside a disc folder stay apart. Such a set is searched by the words its discs' titles begin with ("Holy Diver"), else by its folder's title. | in code (`engine._one_box`, `match_albums._set_title`); tests `DSC2_*` |
| TRK-1 | A work Discogs lists with its movements under it is one track of the disc when the work has its own length and none of its movements has one (if any movement has a length, the work and every movement stay tracks as listed, never half merged): an index entry with sub-tracks ("Mirror Image" 11:09 over 7a-7d), or a track followed by rows numbered as its movements ("Symphony For The Devil" 7:49, then 7.I-7.III). A movement is numbered by a letter or roman numeral ("7a", "7.I", "7-II"), never by a number ("2-2", "1.7": a disc's track); sub-tracks numbered otherwise stay tracks. Matched and tagged as that one track (its title and length). Movements listing their own lengths stay tracks as listed; "10" is never a movement of "1", nor "7.II" of "7.I". | in code (`match_albums.one_track_movements`, used by every Discogs tracklist reader and `engine.discogs_parts`); tests `TRK1_*` |
| TRK-2 | Your files are the release's own tracks in another order (an SACD rip numbered otherwise: BS&T - Child Is Father To The Man, 001 is the release's track 11): when, on one disc, every file's title is that of exactly one track and its length agrees within 3 s, each file is compared with, and tagged and numbered as, its own track - also for a release you choose. Only a running order of its own: at least half the tracks (3 or more) moved, counted as all but the longest run of files already in the release's order (one bonus track put first moves one track); a few tracks moved (bonus tracks in another order, two swapped) is another edition's order, decided by the album's other pressings as before. Not when a file has no title, a title fits two tracks, or a length disagrees. At one score, a release in the files' order goes first; and a release in the files' order within the tie margin keeps any reordered one out of the tie (no country order or date picks it, nor does it hold the pick back as runner-up) unless your barcode or catalogue number, a disc TOC or the files' own source link is on the reordered one; the release in the files' order must fit every track (none missing). | in code (`match_core.order_view`, `score_candidate`; exported as slots by `summarize`); tests `TRK2_*` |
| TRK-3 | A Discogs tracklist numbered by its medium ("CD-1" ... "CD-11", "DVD") is one disc per medium when each medium named is one disc (listed in the formats with quantity 1, CD + DVD; or, with no formats listed, two media or more named), the headings between being sections of that disc ("Bonus Track": Mogwai - Mr. Beast's track 11 stays 11 of disc 1, not 1 of a second disc); numbers restarting ("CD-1, CD-2, CD-1") and a medium of several discs (a 2-CD set numbered "CD-1" ... "CD-12" across its "CD 1" / "CD 2" headings) are read by the other conventions. The parts of a work listed with no position of its own carry their own position for their disc when it names one with a hyphen ("2-3" ... "2-6": Spock's Beard - Live's suite is on disc 2, not disc 1); a movement's "3.1" or "2a" names no disc. | in code (`discogs_mapping._split_by_medium`, `parse_discs`; `match_albums.flatten_tracklist`, `_HYPHEN_DISC`, `engine._dg_tracks`, `_flatten_raw`); tests `TRK3_*` |
| TRK-4 | MusicBrainz and Discogs listing the same tracks of one release split into discs otherwise (Robin Trower - The Studio Albums 1973-1983: MusicBrainz 8 + 10 on discs 4 and 5, Discogs 9 + 9) number every file by ONE layout, never disc from one and track from the other: the layout most of your files' own disc and track numbers agree with (and more of them than the other's), else MusicBrainz's. A file moved to another disc takes that disc's own MusicBrainz fields (disc title, media, disc ID); its recording and track IDs stay its own. The plan notes it. Only the same discs (2 or more, each numbered from 1) split otherwise: a DVD numbered first on one database, a set listed as one disc on the other, or a hidden track 0 is merged as before. | in code (`engine.settle_disc_layout`, `Engine._identified`); tests `TRK4_*` |
| WEB-2 | A Wikipedia track listing is read link by link ("[[Night Stalkers (song)|Night Stalkers]]" is "Night Stalkers", never "[[Night Stalkers (song)"), templates keep their text, nested ones too ({{nowrap|{{lang|fr|Chanson}} d'amour}} is "Chanson d'amour"; {{nowrap|X}} and {{lang|fr|Titre}} their last part, {{abbr|No.|Number}}, {{nihongo|...}} and {{ruby|...}} their first; notes and citations go, {{r|...}} only by that exact name). A section numbered from 1 is a disc of its own - except an LP's even side ("Side two" numbered from 1 again: the disc runs on, numbered on) and a disc whose headline names an edition ("Deluxe edition bonus disc", "Bonus disc", "reissue"): another edition's extra disc, so the standard edition without it is a candidate too; one numbered on from a disc continues it ("Side two" 6-10, no headline) - unless its headline names an edition ("Digital edition bonus tracks", "EMP & Target exclusive CD bonus tracks", both 13-14) or another section took those numbers: then it is that edition's bonus tracks on the disc whose numbering it continues ("Disc one bonus tracks" after disc two goes on disc one), its own candidate (standard, standard + each section; sections running on, 13-14 then 15-16, together; identical sections once) - never discs 2 and 3. | in code (`web_tag._wiki_blocks`, `_wiki_editions`, `wikipedia_editions`); tests `WEB2_*` |

## Your own choices

| ID | Rule | Status |
|---|---|---|
| CHG-1 | A release you chose with Change Release stands through later look-ups, re-checks and updates, unless a barcode or catalogue number of your copy points to another release. | in code (`_kept_choice`); tests `CHG1_*` |

## Working without you

| ID | Rule | Status |
|---|---|---|
| HF-1 | A watched folder applies full pressings and album known results on its own (tag, rename, move into the library). Only Unresolved albums wait. | in code (watcher `unattended`); test `HF1_*` |
| HF-2 | A genuine dispute (two pieces of your own evidence naming different releases, or two entries sharing your barcode) takes the Sources-preferred database's pick in full, rather than waiting. This overrides the "a person decides" cases of BAR-2 and BAR-3. | in code (`_disputed`); test `HF2_*` |
| HF-3 | An Unresolved album in a watched folder is looked up again every 10 minutes for its first day, then daily for the rest of the week, then only when you ask. New albums always go before retries. Loose tracks right in a watched folder (looking at them again reads the whole folder, the library inside it too) keep the profile's slow retry schedule. | in code (`watcher.unresolved_next_try`, new albums first); tests `test_v0821_retry_schedule` |
| HF-4 | When the preferred database is down or rate-limited, the album is held and retried; it is never decided with the other database alone. | in code (`engine._preferred_down`); tests `HF4_*` |
| HF-5 | Partial albums (tracks missing) are tagged and filed as that release; numbering from the release (also when your files' numbers run on over the discs: disc 2 as 19-36), gaps kept, "incomplete" noted in the job report and the Overview only, no tag. | in code (no TAGMENDER_MISSING tag; engine notes); tests `HF5_*` |
| HF-6 | Albums already filed are not looked up again after an update until the accuracy check on your known albums passes (the re-check option stays off). | in code (off by default) |
| ACC-1 | Accuracy is measured against the databases: an album counts as right when its release is proven in the database (exact) with a TagMender score of 100. The re-check of filed albums (HF-6) is turned on only once that holds on your library. | not code: how accuracy is measured; decides when HF-6 may be turned on |
| HF-7 | Not sure even of the album (artist matches, title partly, a few tracks agree): Unresolved and untouched; retried per HF-3. | in code |
| HF-8 | An album a watched folder left Unresolved is looked up once more after each TagMender update, whatever its HF-3 schedule (not with Retry set to Never); its HF-3 week is not restarted. It is listed under Needs attention also when Unresolved is set to "leave" and nothing was written to its files: with no folder chosen there, the page lists every such album, keeps the badge, and a notification tells of new ones while the app is open. `tagmender unresolved` and `tagmender retry` find them too. | in code (`watcher.version_since`, `watcher.unresolved_records`, `gui/attention.py`); tests `test_v0826_needs_attention`, `test_v0821_retry_schedule` (HF-8) |
| OCR-1 | The investigation of the scans (every scan, every zone, before an album is left Unresolved) keeps to its time budget within a scan too: no reading starts after it, and tesseract is given only the time left. | in code (`match_albums.ocr_deep`, `ocr_located_codes`); tests `OCR1_*` |
| MOV-1 | An album moved or copied takes EVERY file of its own folders with it, whatever its extension (an .auCDtect report, a .toc), so the folder it leaves is empty and removed. A folder shared with another album's music, and an artist folder, keep their own files; there only the profile's companion types that belong to the album's tracks go. | in code (`plan._companions`); tests `MOV1_*` |
| MOV-2 | A second copy of one of the album's tracks (a file of the album's group in the album's folder, not planned, its length within 0.05 s of a planned track's and - FLAC on both - the same audio MD5, else the same title or a file name beginning the other's) is no other album's music: the folder's scans, log and cover still go with the album, and the copy, with its own .cue, .lrc ..., is left where it is, with a note (Allan Holdsworth - Metal Fatigue: "005.TUILMOMF.flac" beside "005.TU.flac" kept the scans and log behind). | in code (`plan.second_copies`, `plan._companions`); tests `MOV2_*` |
| MOV-3 | An album folder you named (planned on its own, or a watched album re-checked) is the album's own folder when its name holds the album's title (without the naming script's edition marks: "3 [HD]" is "3") as a whole word or words and - for a one-word title ("3", "Music", "Live") - a year right beside it ("2016 - 3 [SACD DSD64 24bit 352khz]", "Music (2000)") or the album's artist ("Blood, Sweat & Tears - 3"); never "Music", "Music 2020" or "Rips 2019 Vol 3": MOV-1 applies and every file goes with the album, its .md5 and .sfv too, so the folder is left empty. A named folder of another name ("Downloads") keeps every file not of the companion types, as a watched or library folder always does. | in code (`plan._named_after_album`, `Planner.fixed_roots`); tests `MOV3_*` |
| MOV-4 | A second copy of a release already filed - the same release, bringing no disc the filed copy lacks (disc numbers compared as integers, "01" is 1; non-numeric ones ignored) - is planned as an album tagged where it is (a copy profile: not copied), with a note naming the folder the other copy is in, never put beside it under "~xxxx" names: another copy of this plan (two rips of Robin Trower - Compendium 1987-2013 in "...1987 - 2013" and "...1987–2013") or one already on disk there, outside the plan (a new rip arriving in a watched folder while the library has it). Disc numbers come from the albums' tags; one disc of a set in its own folder brings other discs and still joins its set's folder (BOX-2), as does a full set arriving where the library has disc 1 alone. An album of this plan claims its folder only once its placement is decided (an album blocked, skipped or not moved claims none); a copy held by another album's plan claim says the other copy is "planned to be filed in" that folder, since it is filed only when that plan is carried out. | in code (`Planner._own_album_dir`, `_same_release_there`, `_prop_discs`, `Planner.album`); tests `MOV4_*` |
| APL-1 | Apply asked while albums are looked up runs beside the look-up (the network and the disks at work at once), unless an album to apply shares a folder with one still to be looked up: then the look-up stops first. Each album's row says Done as soon as its files are; a look-up never replaces the row of an album applied, nor brings back one taken out. | in code (`gui/overview.py` apply_alongside); tests `APL1_*` |
| EDT-1 | While a look-up runs, an album it is not looking up can be edited (cover, tags); one it still has in hand waits, and you are told. Clear Done takes Done albums out of the list while work runs (CLR-1). | in code (`Overview.album_busy`, `_take_out`); tests `EDT1_*`, `CLR1_*` |

## Mixed folders

| ID | Rule | Status |
|---|---|---|
| MIX-1 | Two albums or versions (stereo and 5.1) in one folder: if one release in the databases holds both (one catalogue number, e.g. disc 1 stereo and disc 2 5.1), they are filed as that one album, with disc number and disc subtitle tagged. If they are different releases, each is split and filed in its own folder. If neither is known on its own, a release holding both versions is looked for first: that is the answer when found. Disc number and disc subtitle are always tagged; an inferred disc subtitle names format and layout ("SACD Stereo", "Blu-ray 5.1", "CD"). | in code (`engine.version_subtitles`); tests `MIX1_*` |

## Duplicates

| ID | Rule | Status |
|---|---|---|
| DUP-1 | A second copy of a release you already have filed is filed beside it with the profile's duplicate suffix; nothing is deleted. | in code |
| DUP-2 | Another pressing of an album you have is filed beside it, its edition in the folder name (the naming script). | in code |

## Unresolved

| ID | Rule | Status |
|---|---|---|
| UNR-1 | Nothing fits well enough even for album known (another artist, another track count, no results): nothing is changed and the album is marked Unresolved, for Change Release. | in code |
