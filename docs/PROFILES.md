# Profiles, naming and examples

A profile lives in `~/.config/tagmender/profiles/NAME.json` and holds only what differs from the
defaults. `tagmender profiles show NAME --full` prints every setting.

```bash
tagmender profiles examples                      # installs Lossy
tagmender profiles new Inbox --from Lossy --set operation=move --set destination=/media/unraidmedia2/music
tagmender profiles set Inbox 'sources=[{"path": "/home/hp/Music/Inbox", "watch": true}]' unattended=true
tagmender profiles check
```

More examples are in `contrib/profiles/` (copy one into `~/.config/tagmender/profiles/`).

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `sources` | `[]` | `[{"path", "recursive", "watch"}]`; the deepest profile wins where roots overlap |
| `exclude_paths` | 2delete, _unresolved, .Trash | globs on the full path, never processed |
| `formats` | every format | e.g. `["mp3","opus","m4a"]`; others are Skipped, never converted |
| `providers` | MB, Discogs, web on; AcoustID off | `{"discogs": {"enabled": true, "identify": true}}` |
| `primary` | `discogs` | which provider's tags form the base when MusicBrainz and Discogs hold the *same* pressing (`discogs`, `musicbrainz`, `both`). Which pressing it is, is the matcher's call: evidence about your copy first (folder catalogue number, barcode, scans, disc ID), Discogs ahead of MusicBrainz, then your preferred-country order |
| `field_sources` | genre, label, catalogue number: Discogs first | per field, providers in order |
| `thresholds` | identify 90 (margin 8), replace 90, organise 95 | matcher scores 0-100 (heuristic, not probability) |
| `field_policy` | replace | `{"default": "replace", "fields": {"album": "lock", "comment": "remove"}}` |
| `artwork` | CoverUp, embed front, cover.jpg only into folders without an image | |
| `operation` | `in_place` | `in_place`, `copy`, `move` |
| `destination` | — | must exist (never created); required for copy/move |
| `naming` | preset `tagmender` | or `{"template": "..."}` |
| `companions` | .cue .log .lrc .jpg .png .pdf .txt .m3u | go along with a moved/copied album |
| `filenames` | windows-compatible, 255 bytes per name, 4096 per path, 5-word album folders | `abbreviations`: `{"Volume": "Vol."}` |
| `unresolved` | `mark` | `leave`, `mark` (TAGMENDER tags), `move` (to `move_to`) |
| `retry` | after 30 days, 6 attempts | watched folders |
| `unattended` | false | apply without asking (watcher) |
| `watch` | settle 10 min, every 300 s | |
| `backup_retention_days` | 30 | `tagmender jobs prune` |
| `exclusions` | `[]` | this profile's Ignore/Skip rules, added to the global ones |

## Naming presets

| Preset | Gives |
|---|---|
| `tagmender` | `Robert Rich & Steve Roach/1992 - Soma The Twentieth Anniversary Edition/03.TTE.flac`, multi-disc `.../Disc 02/203.TTE.flac` (TagMender's existing scheme) |
| `picard` | Picard's default: `Album Artist/Album/03 Title.flac` |
| `artist-album-track` | `Album Artist/1992 - Album/1-03 - Title.flac` |
| `classical` | `Composer/1992 - Album/03 - Title.flac` |
| `singleton` | `Artist/Singles/Artist - Title.flac` |

## Template language

A subset of Picard's scripting, documented exactly (anything else is an error when the profile is
checked):

`%field%` (several values joined with "; "), `%_year%` (original year, else release year),
`%_albumartists_sorted%`, `%_multiartist%`; `$if`, `$if2`, `$not`, `$and`, `$or`, `$eq`, `$ne`,
`$gt`, `$lt`, `$gte`, `$lte` (numeric when both sides are numbers), `$num`, `$left`, `$right`,
`$lower`, `$upper`, `$title`, `$trim`, `$replace`, `$firstalphachar`, `$truncate`, `$year`,
`$join`, `$getmulti`, `$lenmulti`, `$noop`, and TagMender's `$words(text,n)` and `$initials(text)`.
`/` separates folders; a `/` inside a value becomes `_`. Not supported: `$set`, `$unset`,
`$copy`, `$rsearch`, `$rreplace`, plugin functions.
