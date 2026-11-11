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

## Evidence from your copy

| ID | Rule | Status |
|---|---|---|
| EVI-1 | Weight of evidence, strongest first: barcode > catalogue number > disc ID / matrix (runout) > label or country printed on the scans > folder name > country order. | in code (`CLUE_WEIGHTS`); tests `EVI1_*` |
| EVI-2 | Evidence shared by every candidate (a catalogue number on all editions) tells nothing apart; so does a release country (or media) in your tags that none of the tied pressings has (another tagger's GB on an album out only in DE and RU): it never outweighs your disc ID or stops the country order. A download entry that lists no catalogue number is unknown, not different - also when your scans (a CD sleeve bundled with a download) print another pressing's number. | in code (`_weigh`, `choose_pressing`); tests `ScansNumberAgainstAnUnlistedOne`, `EVI2_*` |
| EVI-3 | Text read off the scans (for example "Made in EU") beats the country order. | in code |
| EVI-4 | Poor OCR (few words of artist or title read) never lowers the confidence: scans only add evidence. | in code (`score_candidate`, OCR only adds); test `EVI4_*` |

## Album known or full pressing

| ID | Rule | Status |
|---|---|---|
| ALB-1 | Every track title matches (your shortened title, the opening words of the release's, counts) and at most one track length is off: the best pressing is tagged in full, even up to 5 points under the confidence cut-off. | in code (`_one_length_off`); tests `ALB1_*` |
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
