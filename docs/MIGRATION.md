# Upgrading from 0.1 (the batch tools)

Nothing you use today stops working. The batch tools keep their options, output folders and undo.

## Settings and data
- **Profiles**: rules-only profile files (`~/.config/tagmender/profiles/NAME.json` with `name` and
  `exclusions`) load as they are, with every other setting at its default. To write them in
  the new form: `tagmender profiles migrate` (keeps `NAME.json.bak-<time>`).
- **Rules** (`exclusions.json`) and the state database (`~/.local/state/tagmender/state.db`):
  unchanged; new tables (`jobs`, `job_albums`, `processed`) are added on first use.
- **Discogs token**: `DISCOGS_TOKEN` still works. `tagmender setup` can store it in
  `~/.config/tagmender/discogs_token` (mode 600), so the systemd service needs no `EnvironmentFile`.
- **AcoustID key**: `~/.config/tagmender/acoustid_key` is read as before.
- **Plugin clones**: still found in `~/Documents/Github/`; `TAGMENDER_DISCOGS_PLUGIN` and
  `TAGMENDER_COVERUP_DIR` override; installed packages carry their own copies.

## Behaviour that changed
- `TAGMENDER_REASON` now starts with reason codes: `incomplete_album: review: track count
  differs`. Values written before are still read (codes are worked out from the words).
  `tag_albums.py --mark-unresolved` rewrites an album only when its reason actually changed.
- Web-tagged albums carry `web_fallback_only:` at the start of `TAGMENDER_REASON` (they stay
  `TAGMENDER=Resolved`).
- The matcher's User-Agent is now `TagMender/0.2.0 ( https://github.com/herculepirate/tagmender )`
  (MusicBrainz's requested format).
- The matcher's code changed (provider switches, typed provider errors, mixed folders), so its
  version stamp changed: `match_loop.sh` re-evaluates saved matches (lookups come from the cache),
  and `tag_albums.py` treats matches made before as older until then (`--allow-old-matches`).

## Moving to the engine (optional)
`tagmender plan PATH --profile NAME` does what `match_albums` + `tag_albums`/`web_tag` +
`coverup_batch` did, in one plan you can read before anything changes, without Picard:

| 0.1 | 0.2 |
|---|---|
| `match_albums.py` + `tag_albums.py --apply` | `tagmender run PATH --profile NAME` (or `plan` then `apply`) |
| `web_tag.py --apply` | included (provider `web`, last resort) |
| `coverup_batch.py --apply` | included (`artwork` in the profile); the batch tool remains for back covers, artist photos and generated covers |
| `inbox.py` + `tagmender-inbox.service` | a profile with a watched source + `tagmender-watch.service` |
| `--undo <run>` per tool | `tagmender undo JOB` |

To keep folders exactly as Picard's naming script files them, use the `tagmender` naming preset
(the default), which reproduces the scheme `web_tag.py` already used.
