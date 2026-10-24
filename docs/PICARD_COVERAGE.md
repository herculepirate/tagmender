# Picard feature coverage

**Full** = equivalent, tested. **Partial** = some of it. **Plugin** = provided by a Picard plugin
TagMender reuses. **Excluded** = deliberately not done.

| Picard feature | TagMender | Notes |
|---|---|---|
| Album lookup by tags / file names / folder | Full | matcher (plus catalogue numbers, OCR, rip logs, cue sheets, links in tags) |
| Scan (AcoustID fingerprint) | Full | albums still unsure; single tracks at recording level |
| Disc ID lookup | Full | from track lengths, EAC/XLD logs, cue sheets; MusicBrainz and CTDB |
| Preferred release countries / formats | Partial | Picard's country list is read; formats from folder names |
| Tag mapping per format | Full | ID3, Vorbis, MP4, APEv2, ASF as in Picard's source |
| Clear existing tags / preserve tags | Partial | TagMender never clears; per-field policies (preserve, fill, replace, remove, lock) |
| Standardise artist names, translate names | Excluded | credited names as on the release (Picard's default) |
| Use release / track relationships (composer, work, performers) | Partial | not fetched yet (`docs/CONTINUATION.md` step 4); Discogs credits via the plugin |
| Original release date | Full | release-group first release date; Discogs master year via the plugin |
| Release type, status, country, script, language | Full | |
| Cover art (CAA, local files, embed, save) | Full | via CoverUp (plugin), CAA fallback; never replaces an embedded picture |
| File naming script | Partial | documented subset (`docs/PROFILES.md`); no $set/$unset or plugin functions |
| Windows-compatible file names | Full | plus byte/UTF-16 limits and shortening |
| Move files / delete empty folders | Full | journaled, undoable |
| Move additional files (cue, log, scans) | Full | cue/playlist references updated |
| Save tags only, no rename | Full | `operation: in_place` |
| Ratings, user collections, submissions | Excluded | nothing is submitted to MusicBrainz or AcoustID |
| Discogs Enrichment plugin | Plugin | its own extraction code |
| CoverUp plugin | Plugin | its own pipeline, headless |
| ReplayGain | Partial | `enrich.py` (EBU R128); existing values always preserved |
| Lyrics | Partial | `enrich.py` (LRCLIB, `.lrc`), not yet a planned step |
| Classical Extras plugin (works, movements) | Excluded for now | see continuation step 4 |
| Interactive cluster/match UI | Excluded | TagMender previews decisions instead of drag-and-drop matching |
