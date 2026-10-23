# TagMender: assessment and requirements checklist

Assessed against *Develop Tagmender: automated music tagging and organization for Linux*.
Status: **done**, **partial**, **deferred** or **blocked**. "Verified" means covered by the
automated tests in `tests/` (295 tests, all passing at the time of writing). It also covers
real runs on the ambient library (~94 albums tagged) and the extreme library (~490 albums matched).

## Where the build stands

TagMender is a set of command-line Python tools. They share a matcher (`match_core.py`),
a rules engine (`exclusions.py`), and SQLite state and caches. One command (`tagmender.py`)
fronts them all:

| Tool | Does |
|---|---|
| `audit_untagged.py` | lists untagged and poorly tagged albums |
| `match_albums.py` + `match_core.py` | identify albums (MusicBrainz, Discogs, disc IDs, CTDB, AcoustID, ISRC, scans/OCR, links in tags) |
| `tag_albums.py` | writes tags and files folders by driving Picard (`--undo`, Resolved/Unresolved marks) |
| `web_tag.py` | albums no database has: Discogs-only, Wikipedia/Wikidata, iTunes, web; artist IDs |
| `coverup_batch.py` | artwork through the CoverUp pipeline, with no Picard GUI |
| `enrich.py` | ReplayGain, genres, lyrics (LRCLIB), quality/transcode check |
| `inbox.py` | watched inbox: identify → tag → file |
| `dedupe_twins.py`, `dupes_fp.py` | duplicates (move to `2delete/`, undo) |
| `mb_seed.py`, `learn_rules.py`, `health_report.py` | MB add-release seeds, learned aliases, library health |

How the companion projects are used:

- **picard-discogs-enrichment** is a Picard plugin. `discogs_mapping.py` and `discogs_client.py`
  load without Picard. TagMender loads them directly (`match_albums.load_plugin`) and needs no copy.
- **picard-coverup** is a Picard plugin with a Picard-free `pipeline.py`. TagMender runs it
  headless through `coverup_loader.py`, using the PIL ports `coverup_pil_imaging.py` and
  `coverup_pil_render.py` in place of its Qt imaging. The plugin itself is unchanged.

Gaps against the spec:

- No single engine or planner object: each tool plans and executes on its own.
- Tag writing for MusicBrainz releases goes through Picard, a subprocess with Picard closed.
- No GUI, no packaging, and no rotating log.

## Checklist

### Architecture (§4)
| Item | Status |
|---|---|
| Shared local engine for GUI/CLI/watcher/API | partial: shared matcher and rules engine; no common planner/executor yet |
| Works without cloud, Picard GUI, HTTP server | partial: identification, web tagging, artwork and enrichment need no Picard; MB tag writing still uses Picard |
| Typed models, validated config, migrations, lockfile | partial: rules are validated; no lockfile or config schema yet |
| Optional API (Unix socket) | deferred |

### Providers (§5, §16)
| Item | Status |
|---|---|
| MusicBrainz identification | done, verified |
| Discogs identification without an MB ID | done, verified (`web_tag` discogs-only test) |
| Both queried and reconciled; clue weighting | done, verified (`choose_pressing`, clue weights) |
| Field-level source priority and provenance | partial: Discogs fills labels, genres and artist IDs; no per-field settings |
| Missing credentials vs no match | partial: no Discogs token is reported; not a full error taxonomy |
| Rate limiting, caching, Retry-After | partial: per-host throttling and SQLite cache; Retry-After is not read everywhere |
| No automatic edits or fingerprint submission | done: `mb_seed` and disc-ID CSVs are for the user to submit |

### Identification (§6)
| Item | Status |
|---|---|
| Tags, filenames, folders, durations, disc structure | done, verified |
| Fingerprints (AcoustID), disc TOC (MB, CTDB), ISRC, barcode, catno, OCR | done, verified |
| Explainable evidence (`why`, clues) | done |
| Singletons, compilations, featured artists | partial: album-centred |
| Per-field rules (preserve/fill/replace/remove/lock) | deferred |

### Web discovery and unresolved outcomes (§7)
| Item | Status |
|---|---|
| Web fallback (Wikipedia/Wikidata, iTunes, DuckDuckGo HTML) | done, verified with recorded responses |
| `TAGMENDER=Resolved/Unresolved`, `TAGMENDER_REASON` | done |
| Reason codes as listed in the spec | partial: free-text reasons; codes not yet normalised |
| Artist disambiguation (MB album+year, Wikidata, Discogs) | done, verified |

### Formats (§8)
| Item | Status |
|---|---|
| FLAC, MP3, M4A, Ogg, Opus read/write (mutagen) | done, verified in tests |
| Capability matrix document | deferred |
| Audio never changed; unknown tags kept | done: undo restores files byte-identical (tested) |

### Metadata and traditions (§9)
| Item | Status |
|---|---|
| Core fields, IDs, label, catno, barcode, ISRC | done (through Picard + Discogs) |
| Classical / Indian / folk conventions | deferred (Picard relationship options documented) |
| Lyrics: plain and synced, source recorded, none for instrumentals | done (LRCLIB) |
| Transliteration (Cyrillic, Greek, Japanese) for matching | done; tags keep original scripts |

### Profiles and watching (§10)
| Item | Status |
|---|---|
| Profiles via CLI | partial: profiles hold exclusion rules only |
| Exclusion rules (Ignore/Skip, file/album, AND/OR, literal by default) | **done, verified** |
| Watched inbox, stability, no re-queue of unchanged ignored items | done, verified |
| Example Lossy profile (copy to `/media/media1/music/lossy`) | deferred |
| User service | done (`contrib/tagmender-inbox.service`) |
| Mount verification, worker limits, restart recovery | partial |

### Naming, operations, recovery (§11, §12)
| Item | Status |
|---|---|
| Picard naming; ≤5-word album folders | done |
| Dry run by default, `--apply` to write | done |
| Undo per run (tags, moves, artwork) | done, verified |
| Unified operation plan and preview shown by GUI and CLI | partial: per-tool dry runs and CSVs |
| Journaled cross-filesystem moves | partial: copy-verify-remove in places; no general journal |
| Filename shortening with byte limits and collisions | partial: word limit and collision checks |

### CoverUp (§13)
| Item | Status |
|---|---|
| Headless CoverUp pipeline, plan/apply/undo | done, verified (50 tests) |
| Artwork failure never blocks tagging | done |
| Shared setup wizard | deferred |

### GUI and CLI (§14)
| Item | Status |
|---|---|
| Single `tagmender` command | partial: `rules` subcommands plus pass-through to every tool |
| `--json`, stable exit codes | partial: `rules` commands |
| GUI | **not started** |

### Reliability and logging (§16)
| Item | Status |
|---|---|
| XDG config/state locations | done for rules and state; older tools keep their own output folders |
| Secrets kept out of logs and arguments | done (tokens are read from env or files only) |
| Rotating logs (100 MB × 3+) with redaction | deferred |
| One bad album never stops a run | done (per-album isolation in enrich, match, coverup) |

### Packaging (§17)
| Item | Status |
|---|---|
| AppImage / .deb / Flatpak | deferred; none has been built, so none is claimed |

## Release-candidate demonstrations (§18)

| # | Demonstration | Status |
|---|---|---|
| 1 | single-file identification and verified writing | partial (album-centred) |
| 2 | Discogs tagging without MB | done (web_tag) |
| 3 | album processing with previewed organization | done (dry runs) |
| 4 | unattended processing under a saved profile | partial (inbox; profiles are rules-only) |
| 5 | useful unresolved outcomes | done |
| 6 | recovery from an interrupted operation | partial (undo; no crash journal) |
| 7 | shared GUI/CLI behaviour | blocked on the GUI |
| 8 | install from a built package | deferred |
