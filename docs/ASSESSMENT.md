# TagMender: requirements checklist

Assessed against *Develop Tagmender: automated music tagging and organization for Linux* (prompt 1)
and *Ignore and skip rules* (prompt 2). Status: **done**, **partial**, **deferred** or **blocked**.
"Verified" means covered by the automated tests (`./run_tests.sh`: 414+ tests, all passing on
CPython 3.13.17 and on the AppImage's bundled CPython 3.12.13, 2026-10-24) or by the package
tests recorded in `docs/RELEASE_NOTES.md`.

## Where the build stands (0.2.0)

One engine (`engine.py`) now sits behind the CLI (`tagmender`), the watcher (`tagmender watch`) and
the desktop app (`tagmender gui`):

```
path ─► engine.albums_in ─► engine.identify ─► plan.build ─► plan.preview (CLI, GUI)
         (albums, discs,     (matcher: MB +     (rules, field      │
          mixed folders,      Discogs, OCR,      policies, naming,  ▼
          singles)            fingerprints,      filename limits, executor.Executor ─► journal, undo,
                              web; tags from     companions, art)  (staged, verified,     recover
                              mb_tags / Discogs                     never overwrites)
                              Enrichment code)
```

The batch tools from 0.1 (`match_albums`, `tag_albums`, `web_tag`, `coverup_batch`, `enrich`,
`inbox`, ...) are unchanged in behaviour and still run (`tagmender match|tag|web|...`). Picard is
no longer needed to write MusicBrainz tags: `tagio` uses Picard's own tag mappings.

## Checklist

### Start and companions (§1)
| Item | Status |
|---|---|
| Existing build inspected, run and tested before changes | done (296/307 passing at b9664b8; the 11 failures were a real headless bug, fixed first) |
| Coverup interface determined | done: Picard plugin with a Picard-free `pipeline.py`; its `creative/render.py` needs PyQt6 (stand-in registered when Qt is absent) |
| Discogs Enrichment interface determined | done: Picard plugin; `discogs_mapping.py`/`discogs_client.py` load without Picard and are used directly |
| Unrelated work preserved; no rewrite | done: every 0.1 tool and test kept |

### Architecture (§4)
| Item | Status |
|---|---|
| One engine for GUI, CLI, watcher | done, verified (`test_cli_work`, `test_gui`) |
| Independent of cloud, Picard GUI, HTTP server | done (Picard only optional, for `tagmender tag`) |
| Clear boundaries | done: discovery (`engine.albums_in`), providers (`match_albums`, `mb_tags`, plugin), fingerprints, scoring (`match_core`), reconciliation (`engine._identified`), profiles, naming (`naming`, `filenames`), planning (`plan`), execution (`executor`), artwork (`artwork`), jobs/watching (`executor`, `watcher`), adapters (`tagmender.py`, `gui.py`) |
| Typed models, validated config, migrations, locking | partial: dataclasses (`Proposal`, `Rule`, `Profile`), validated profiles with schema version + migration; AppImage dependency lock; no lock for the system-Python install (Debian packages) |
| Optional API (Unix socket) | deferred |

### Providers (§5, §16)
| Item | Status |
|---|---|
| MusicBrainz identification | done, verified |
| Discogs identification without MB ID; never invented MB IDs | done, verified (`test_discogs_only_without_any_musicbrainz_id`) |
| Both reconciled; same pressing only | done, verified (MB base + Discogs fill only for linked/twin pressings; disagreeing pressings never merged) |
| Field-level source preferences and provenance | done, verified (`field_sources`, `Proposal.provenance`) |
| Confidence / coverage / completeness / errors kept apart | done: `confidence`, `provider_errors`, field diffs, reason codes |
| Missing credentials vs no match vs outage vs throttling vs invalid response | done, verified (`ProviderUnavailable.kind`, `missing_credentials`) |
| Rate limits, Retry-After, backoff with jitter, caching | done: MB ≈1 req/s, Discogs ≈54/min, AcoustID ≈1/s; see `docs/PROVIDERS.md` |
| No automatic edits or fingerprint submissions | done |

### Identification (§6)
| Item | Status |
|---|---|
| Tags, file/folder names, durations, discs, fingerprints, disc IDs, barcodes, catalogue numbers, OCR | done, verified (0.1 matcher) |
| Mixed folders split by album; disc folders joined | done, verified |
| Single tracks: recording by fingerprint, no edition claimed | done, verified |
| Explainable evidence; scores not called probabilities | done (preview shows "score", agreed evidence, doubts) |
| Thresholds: identify / replace / organise | done, verified |
| Per-field preserve / fill missing / replace / remove / lock | done, verified |
| Compilations, live, remixes, bonus tracks | partial: matcher handles credits, editions and track counts; no dedicated singles-on-compilations logic |

### Web discovery and unresolved (§7)
| Item | Status |
|---|---|
| Web fallback (Deezer, iTunes, Bandcamp, Wikipedia; DuckDuckGo HTML for Bandcamp pages) | done (0.1 `web_tag`, now also in the engine) |
| Search the web in the system browser | done (GUI) |
| `TAGMENDER=Unresolved`, `TAGMENDER_REASON` with codes | done, verified (`reasons.py`; old word-only values still read) |
| Provider errors recorded without unresolving an identified album | done, verified |
| Structured attempt details (provider outcomes, evidence, timestamps, retry eligibility) | partial: in the plan JSON, job journal and watcher state; not yet one per-album record |

### Formats (§8)
| Item | Status |
|---|---|
| Capability matrix | done (`docs/FORMATS.md`, `tagmender formats`) |
| Write + read-back + audio unchanged | done, verified for MP3, FLAC, AAC/ALAC M4A, Vorbis, Opus, WAV, AIFF, WavPack, WMA |
| APE, Musepack, DSF, DFF, TTA | partial: mapped and audio-verifiable, not tested here (no encoder) |
| Detection from contents | done (`payload._sniff`, mutagen) |
| Never transcode; unknown tags and art preserved | done, verified |

### Metadata and traditions (§9)
| Item | Status |
|---|---|
| Core, sort, IDs, label, catno, barcode, ISRC, release type/status/country, script/language | done, verified (`mb_tags`, plugin) |
| Discogs credits/roles, styles, notes, identifiers | done (plugin's own extraction) |
| Lyrics (plain/synced, LRCLIB, source recorded) | done in `enrich.py` (0.1); not yet a planner step |
| Classical / Indian classical / folk conventions | deferred: `tradition` setting and a `classical` naming preset exist; no work/movement or raga/tala model yet |

### Profiles and watching (§10)
| Item | Status |
|---|---|
| Profiles (all listed settings) via CLI and GUI | done, verified |
| Lossy example to `/media/media1/music/lossy`, copy, no transcoding | done, verified |
| Watching: initial scan, settle, partial downloads, own-writes loop, overlap precedence, restart recovery, retry backoff, reconciliation, unmounted roots | done, verified |
| User service, foreground daemon, low priority | done (`contrib/tagmender-watch.service`, `tagmender watch --nice`) |
| CPU/IO limits | partial: nice/ionice; one worker (no parallel workers yet) |

### Naming, operations, recovery (§11, §12)
| Item | Status |
|---|---|
| Template language (documented Picard subset) and presets | done, verified |
| In place / copy / move; album, disc, singleton layouts | done, verified |
| Companion files; cue/playlist references updated | done, verified |
| Preview identical in CLI and GUI | done (`plan.preview`) |
| Staged, verified, never-overwrite execution; cross-filesystem copy-verify-commit-remove | done, verified (incl. real tmpfs↔ext4 move) |
| Crash recovery, album rollback, undo, retention | done, verified |
| Path safety (`..`, symlinks, roots), missing mounts, disk space, concurrent writers | done, verified |
| Filename limits, abbreviations, word-aware shortening, stable suffixes, case-insensitive collisions | done, verified |

### CoverUp (§13)
| Item | Status |
|---|---|
| CoverUp's own pipeline headless, staged, final write by TagMender | done, verified (offline, local source) |
| Provider abstraction and fallbacks (CAA, Discogs) | done (fallbacks not exercised here: no network) |
| Shared setup | partial: `tagmender setup` stores CoverUp's keys; other CoverUp options still come from Picard.ini |
| Artwork failure never blocks tagging | done, verified |

### GUI and CLI (§14)
| Item | Status |
|---|---|
| Single CLI with JSON, exit codes, help, secret-safe entry | done, verified |
| GUI: Overview, Jobs, Watched folders, Profiles (+ rules editor/tester), Needs attention, History, Settings | done, verified offscreen |
| Off-UI-thread work, cancel, search, drag-and-drop | done |
| Accessibility | partial: keyboard shortcuts, accessible names, symbol+word status, theme-following colours; not tested with a screen reader |
| Pause/resume | deferred (cancel only) |

### Optional local AI (§15)
| Item | Status |
|---|---|
| Local AI assistant | deferred (nothing depends on it) |

### Logging and diagnostics (§16)
| Item | Status |
|---|---|
| 104,857,600-byte rotation, 3 backups, multi-process safe, redaction, job IDs | done, verified |
| Diagnostics export, optional anonymisation | done, verified |

### Packaging (§17)
| Item | Status |
|---|---|
| AppImage | done: built and run here (CLI, GUI start, whole test suite on its Python) |
| .deb | done: built, installed and run here (dependencies satisfied from pip; apt was unreachable) |
| Flatpak, RPM | deferred |
| Desktop entry, icon, metainfo, checksums, release notes | done |

## Prompt 2: Ignore and skip rules
| Item | Status |
|---|---|
| Global and per-profile rules, GUI and CLI | done, verified |
| Literal, case-insensitive default; glob/regex only when chosen | done, verified |
| Fields: album, title, artist, albumartist, genre, any tag, filename, folder, path, format | done |
| Operators, AND/OR (nested), NOT | done, verified |
| Ignore (persistent) vs Skip (this job) | done, verified |
| File or identified-album target; mixed folders | done, verified |
| Evaluated before lookups, again after identification, again before writing | done, verified (`test_rule_added_after_planning_cancels_pending_operations`) |
| No tag/art/lyrics/rename/copy/move for excluded items | done, verified |
| Shown as Ignored/Skipped (never Unresolved) in previews, results, history | done, verified (CLI, GUI, watcher) |
| Watched folders: no re-queue until files, rules or override change | done, verified |
| Process anyway, enable/disable, rule tester | done (CLI and GUI) |

## Release-candidate demonstrations (§18)
| # | Demonstration | Status |
|---|---|---|
| 1 | single-file identification and verified tag writing | done (fingerprint singles; verified writes) — with recorded responses |
| 2 | Discogs tagging when MusicBrainz has no entry | done, verified |
| 3 | album processing with previewed organisation | done, verified |
| 4 | unattended processing under a saved profile | done, verified (watcher) |
| 5 | useful unresolved outcomes | done, verified |
| 6 | recovery from a deliberately interrupted operation | done, verified |
| 7 | shared GUI/CLI behaviour | done, verified |
| 8 | install and launch from a built package | done (.deb and AppImage) |

Not demonstrated here: live MusicBrainz/Discogs/AcoustID traffic (the sandbox cannot reach
them; provider paths are tested with recorded-style responses). Run `tagmender plan` on a few of
your albums first.
