# Continuation notes

Read `docs/ASSESSMENT.md` first. The steps below run in order. Each one stays usable on
its own, and nothing that works today should be rewritten.

## Next steps

1. **Profiles beyond rules.** Grow `~/.config/tagmender/profiles/NAME.json` into a
   validated profile with these settings:
   - sources and exclusions;
   - provider preferences;
   - thresholds;
   - naming;
   - copy or move, and the destination;
   - unresolved behaviour.

   Ship the example `Lossy` profile: copy lossy files to `/media/media1/music/lossy`, with
   no transcoding. Add `tagmender profiles list|show|check`.
2. **Planner and executor.** Add one `plan.py` that turns a decision (match, web tag,
   artwork) into a JSON plan of tag diffs, path moves and companion files. Add one
   executor with a durable journal for cross-filesystem copy → verify → commit → remove,
   and recovery on start. Port `tag_albums` and `web_tag` onto it. Add
   `tagmender plan|apply|undo|jobs`.
3. **Picard-free MB tag writing.** Map MB release JSON to tags in-process, the way
   `web_tag` already does for Discogs, so Picard becomes optional.
4. **Logging.** Rotate at 100 MB with 3+ backups. Redact `token=`, `key=` and
   `Authorization`. Add job IDs and `tagmender doctor` with a diagnostics export.
5. **Reason codes.** Normalise `TAGMENDER_REASON` to the spec codes (`no_candidate`,
   `ambiguous_release`, `discogs_unavailable`, ...), with the free text kept alongside.
6. **GUI.** Use PySide6, built over the planner. Screens: Overview, Jobs, Watched folders,
   Profiles (including the exclusion rule editor and tester, already available as
   `exclusions.load_rules/save_rule/evaluate`), Needs attention, History.
7. **Packaging.** Build an AppImage first, then a .deb. Test the built artifacts, not just
   their configurations.
8. Format capability matrix, per-field preserve/fill/replace/lock rules, the classical and
   Indian conventions, the optional API, and the local AI assistant.

## Conventions to keep

- Work in dry run unless `--apply` is given. Every write has an undo. Deletions are moves to `2delete/`.
- Never loosen thresholds silently.
- Never print or store the Discogs token or AcoustID key; they come from env or files.
- Tests use generated audio and recorded responses, with an isolated
  `XDG_CONFIG_HOME`/`XDG_STATE_HOME` so they never touch real settings.
- The public repo is authored as herculepirate.
