# Roadmap

Planned features, roughly in the order they are expected. Each stands on its own, and none of
them changes how TagMender works today.

1. **One record per album of what was tried.** A single table holding, per album, the sources
   asked and what they answered, the evidence, the versions involved and whether a retry is
   worth it (today this is spread over the plan, the journal and the watcher's state).
   `tagmender unresolved` and the app's Needs attention page would read it.
2. **Lyrics as a planned step.** Lyrics (embedded and/or `.lrc`) become part of the plan, with
   the same preview and undo as every other change, instead of a separate run.
3. **Several workers.** `watch.workers` above 1: identifying albums waits on the network, so
   several can be looked up at once; writes stay one at a time per folder.
4. **Indian classical conventions.** Raga, tala and form fields for Hindustani and Carnatic
   albums, filled only from sourced data.
5. **Pause and resume** in the app (between albums, where a run already stops cleanly).
6. **An optional local API** over a Unix socket (plan, apply, jobs, progress), using the same
   engine.
7. **Flatpak folder access**: choosing between document portals and direct access to the
   library and inbox folders, and how the watcher runs in the background.
8. **Optional local language model** (for example through Ollama on your own machine): explain
   why an album was left unresolved and suggest rules or profile changes, which go through the
   planner like any other change. Never a source of metadata.
