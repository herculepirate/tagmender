"""profiles.py - processing profiles: how TagMender treats a set of folders.

A profile is one JSON file, ~/.config/tagmender/profiles/<NAME>.json. It says which folders
it covers, which providers identify and enrich, how sure TagMender must be before it adds
tags, replaces tags or moves files, how files are named and where they go, and what
happens to albums nothing could identify. Its Ignore/Skip rules sit under "exclusions"
(exclusions.py reads them from there, as before).

Every setting has a default, so a profile file only needs the settings it changes. Files
written by earlier TagMender versions (a name and exclusion rules only) load unchanged and
are upgraded in memory; `migrate` writes the upgrade, keeping a backup.

  load(name) -> Profile         validated, defaults filled in
  validate(dict) -> [problems]  [] when the profile is usable
  save(profile)                 atomic write
  names()                       the profiles on disk
  next_name() / rename / delete  "Profile N" for a new one (at most MAX_PROFILES); a rename
                                carries the profile's references along, a delete keeps a copy
  install_examples()            the shipped examples (Lossy), never overwriting yours
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

import exclusions as ex

# 2: Windows compatibility and Delete empty directories follow the tagger's settings when unset
# 3: new defaults suited to most people (see V2_BEHAVIOUR); a file written before keeps its
#    behaviour: what it left unset is read as the version 2 default
SCHEMA_VERSION = 3

OPERATIONS = ("in_place", "copy", "move")
POLICIES = ("preserve", "fill_missing", "replace", "remove", "lock")
PROVIDERS = ("musicbrainz", "discogs", "acoustid", "web")
PRIMARY = ("discogs", "musicbrainz", "both")          # (older setting, read as "source")
# Sources: which database identifies the release and whose tags win.
#   musicbrainz_only       MusicBrainz identifies; Discogs is not asked, no Discogs tags
#   musicbrainz_preferred  MusicBrainz picks the release when it has the pressing; Discogs
#                          identifies when it cannot, and fills MusicBrainz's gaps
#   discogs_preferred      Discogs picks the release; MusicBrainz identifies when it cannot,
#                          and Discogs's values win over MusicBrainz's
#   discogs_only           Discogs identifies; MusicBrainz is not asked, no MusicBrainz IDs
SOURCES = ("musicbrainz_only", "musicbrainz_preferred", "discogs_preferred", "discogs_only")
SOURCE_MERGE = {"musicbrainz_only": "mb_only", "musicbrainz_preferred": "fill_gaps",
                "discogs_preferred": "override", "discogs_only": "discogs_only"}
SOURCE_WORDS = {"musicbrainz_only": "MusicBrainz only", "musicbrainz_preferred": "MusicBrainz preferred",
                "discogs_preferred": "Discogs preferred", "discogs_only": "Discogs only"}
# the older "primary": "both" never chose a database of its own; the matcher already let a sure
# Discogs pick decide, so it reads as discogs_preferred
PRIMARY_SOURCE = {"discogs": "discogs_preferred", "musicbrainz": "musicbrainz_preferred",
                  "both": "discogs_preferred"}
# an older profile's Discogs Enrichment merge mode that wrote one database's tags only: both
# databases still identified, so it reads as that database preferred (the merge mode itself
# stays, and keeps the other database's tags out: Profile.enrichment)
MERGE_SOURCE = {"mb_only": "musicbrainz_preferred", "discogs_only": "discogs_preferred"}
# merge modes a profile's discogs_enrichment may set in a preferred mode
OWN_MERGE = ("fill_gaps", "override", "combine", "mb_only", "discogs_only")
OCR = ("auto", "on", "off")
ACOUSTID = ("unsure", "always", "off")
WEB_ENGINES = ("none", "mojeek", "marginalia", "tavily")
# web search engines TagMender no longer offers: a profile that lists one still loads, without it
RETIRED_WEB_ENGINES = ("searxng",)
TRADITIONS = ("popular", "western_classical", "indian_classical", "folk")
COMPAT = ("linux", "windows")
UNRESOLVED_ACTIONS = ("leave", "mark", "move")
LOSSY = ("mp3", "opus", "ogg", "oga", "m4a", "aac", "wma", "mpc")
LOSSLESS = ("flac", "wav", "aiff", "aif", "wv", "ape", "alac", "dsf", "dff")
# "formats" may also name a kind, decided by the codec: an .m4a holds AAC (lossy) or ALAC
# (lossless), so "lossless" takes ALAC in an .m4a and "lossy" takes only AAC
FORMAT_KINDS = ("lossless", "lossy")
# matcher thresholds: shipped values are the floor; a profile may only ask for more proof
THRESHOLD_FLOORS = {"auto_min_confidence": 90.0, "auto_min_margin": 8.0,
                    "replace_min_confidence": 90.0, "organize_min_confidence": 95.0}
# Extra-Careful Matching: the thresholds it raises to (never lowers a higher one)
CAREFUL = {"auto_min_confidence": 95.0, "auto_min_margin": 12.0,
           "replace_min_confidence": 95.0, "organize_min_confidence": 98.0}
GENRE_MB = ("auto", "tagger", True, False)
DISCOGS_STYLES = ("own_tag", "genre", "genre_if_none", "both", "tagger")
WEB_GENRES = ("off", "store", "keywords")
DUPLICATE_SUFFIX = ("number", "code")
ON_EXISTING = ("keep_both", "skip", "ask")
ACCEPT = ("exact", "album", "partial")
QUALITY_ACTIONS = ("note", "hold", "move")
DATE_TAGS = ("release", "original", "tagger")
# a destination only written to while its share is mounted: auto - a folder under /media,
# /mnt, /run/media, /srv or /net, or one /etc/fstab names a mount for; always - also any
# other folder, which must then be on a mount of its own (never the system disk)
MOUNT_GUARDS = ("auto", "always")
# a file that cannot be written (read-only, permission denied): the whole album is left
# for later (skip_album), or only that file (skip_file)
ON_UNWRITABLE = ("skip_album", "skip_file")
# Discogs details: the profile's word -> Discogs Enrichment's setting
DISCOGS_DETAILS = {"performers": "performers", "companies": "companies", "identifiers": "identifiers",
                   "marketplace": "marketplace", "notes": "import_notes"}
# Discogs styles: the profile's word -> Discogs Enrichment's style_mode (genre_if_none is
# the plugin's "separate", then the styles fill an empty Genre: engine.styles_into_genre)
STYLE_MODES = {"own_tag": "separate", "genre": "genre", "both": "both", "genre_if_none": "separate"}
VERSION_STYLES = ("paren", "bracket", "tags_only")
WEB_SOURCES = ("deezer", "itunes", "bandcamp", "wikipedia")
# settings that name code to run: this computer's own (app_settings), never a profile's
CODE_KEYS = ("plugins", "plugin_folder", "plugins_folder", "tagger_command")
COMPANIONS_V2 = [".cue", ".log", ".lrc", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".pdf", ".txt",
                 ".m3u", ".m3u8"]

DEFAULTS = {
    "version": SCHEMA_VERSION,
    "name": "",
    "description": "",
    # what the profile covers
    # [{"path": "...", "recursive": true, "watch": false, "destination": ""}]; a source's own
    # destination ("" = the profile's) is where a copy or move files the albums found in it
    "sources": [],
    "exclude_paths": ["*/_unresolved/*", "*/.Trash*/*"],   # globs on the full path
    # folders never looked into, by name (patterns: "_*"); hidden folders are always skipped,
    # and so are TagMender's own unidentified and duplicates folders
    "skip_folders": [".Trash*", ".recycle", "#recycle", "@Recycle", "@eaDir", "$RECYCLE.BIN", "lost+found"],
    # a sibling "(Deluxe Edition)" folder holding only the bonus tracks is read with its album
    "grouping": {"join_deluxe_folders": True},
    "formats": [],                        # only these formats or kinds ([] = every format; FORMAT_KINDS)
    # "alac" and "aac" in formats also take an .m4a or .mp4 holding that codec (version 2: by
    # extension only)
    "formats_by_codec": True,
    # lossy: lossy files into this library instead ("" = the destination); mounted_only: see
    # MOUNT_GUARDS (every destination, the lossy one and unresolved.move_to too)
    "destinations": {"lossy": "", "mounted_only": "auto"},
    "on_unwritable": "skip_album",        # a file that cannot be written: see ON_UNWRITABLE
    # a lossless file that looks converted from lossy: note | hold (for review) | move (to unidentified)
    "quality_action": "note",
    "tradition": "popular",
    # identification
    "providers": {
        "musicbrainz": {"enabled": True, "identify": True},
        "discogs": {"enabled": True, "identify": True},
        "acoustid": {"enabled": True},    # sends audio fingerprints to AcoustID (needs fpcalc and a key)
        # shops: ask Deezer and iTunes what they sell under the album's barcode when the
        # barcode search found nothing sure (a clue and new titles to search, never proof);
        # gnudb: ask gnudb.org (freedb) what it holds for the disc's table of contents when
        # an album is still unsure (a clue and a title to search; needs a registered e-mail);
        # search: a general web search for the codes on the scans, off ([]) by default:
        # engines, tried in order, each handing over to the next when it is not set up, fails,
        # is at its cap or links no release: mojeek (mojeek_key) | marginalia (marginalia_key,
        # else the shared "public" key) | tavily (a paid AI search, only when chosen here; never
        # alone: with no free engine in the list, marginalia is added after it). The older
        # "engine": "mojeek" is a one-item list.
        # monthly_cap: searches a month on each engine (null: its default), or {engine: n}
        # partial: an album MusicBrainz and Discogs do not prove, found on Deezer, iTunes,
        # Bandcamp or Wikipedia and established against the files, gets Partial tags from it
        # (every tag that names no release; never release IDs, label, barcode or catalogue number)
        "web": {"enabled": True, "shops": True, "gnudb": True, "partial": False,
                "sources": list(WEB_SOURCES),
                "search": {"engines": [], "monthly_cap": None}},
    },
    # AcoustID fingerprints in identification: unsure - only albums not identified for sure;
    # always - every album, noting when the fingerprints disagree with a sure pick; off - never.
    # (Writing each track's AcoustID at Audiophile is "audiophile": {"acoustid": ...}.)
    "acoustid": "unsure",
    # which database identifies and whose tags win: musicbrainz_only | musicbrainz_preferred |
    # discogs_preferred | discogs_only (see SOURCES). The older "primary" is read into it.
    "source": "musicbrainz_preferred",
    # OCR of scans (barcodes, catalogue numbers, disc matrix codes): auto - only for folders
    # with scans or a PDF booklet; on - every album (one without scans has its embedded front
    # cover read); off - never. `--ocr` and the GUI's "Read scans" turn it on for one run.
    "ocr": "auto",
    # which albums are tagged: exact (the release is proven) | album (the album is known, the
    # pressing unsure) | partial (some details are certain); Partial always waits for review
    "identification": {"accept": "partial"},
    "singles": {"identify": True},        # a loose track is matched to its recording by its fingerprint
    # ties between pressings nothing else tells apart: preferred_countries "auto" (your
    # tagger's list when followed, else auto_countries: "region" - your region, then
    # Worldwide, then Europe - or a list) | "none" | a list of country codes. The release
    # types and formats: [] = no preference (or your tagger's when followed).
    # extra_careful: raises the thresholds (CAREFUL); asks you about more albums.
    "matching": {"preferred_countries": "auto", "auto_countries": "region",
                 "preferred_release_types": [], "discouraged_release_types": [], "preferred_formats": [],
                 "extra_careful": False},
    # How Discogs data joins MusicBrainz's: exactly as your Picard's Discogs Enrichment plugin
    # does it, with its settings read from Picard.ini (merge mode, styles, marketplace numbers,
    # performers, protected tags, ...) and Picard's "Preserve these tags". Entries here
    # override single plugin settings for this profile, e.g. {"merge_mode": "fill_gaps"}.
    "follow_tagger_settings": False,
    "discogs_enrichment": {},
    # Picard's own settings for what is written from MusicBrainz (standardize_artist_names,
    # translate_artist_names, release_ars, track_ars, va_name, ...; see tagger_settings), read
    # from Picard.ini; entries here override them for this profile, e.g.
    # {"standardize_artist_names": "none"}.
    "tagger": {},
    "field_sources": {},                  # per field, providers in order; overrides the merge
    "thresholds": {
        "auto_min_confidence": 90.0,      # identify (match_core's own setting)
        "auto_min_margin": 8.0,
        "replace_min_confidence": 90.0,   # replace a tag that already has a value
        "organize_min_confidence": 95.0,  # rename, copy or move files
    },
    # what is written: basic | enthusiast | audiophile (see levels.py)
    "tag_level": "enthusiast",
    "level_fields": {"add": [], "drop": []},
    "audiophile": {"replaygain": True, "lyrics": True, "acoustid": True, "rip_info": True, "quality": True},
    "field_policy": {"default": "replace", "fields": {}},   # field -> preserve|fill_missing|replace|remove|lock
    # genres: musicbrainz - "auto" (your tagger's setting when followed, else on), "tagger" (your
    # tagger's setting, else off), true, false; max - at most this many; discogs_styles -
    # own_tag (DISCOGS_STYLE) | genre | genre_if_none (in Genre when MusicBrainz gives none) | both
    # | tagger (as your tagger's Discogs settings say);
    # web - off | store (a shop's own genres) | keywords (also Bandcamp's keywords)
    "genres": {"musicbrainz": "auto", "max": 5, "discogs_styles": "genre_if_none", "web": "store"},
    # Discogs details written as tags of their own: true | false | "audiophile" (only at that level)
    # | "tagger" (as your tagger's Discogs settings say)
    "discogs_details": {"performers": True, "companies": False, "identifiers": "audiophile",
                        "marketplace": False, "notes": False},
    # tags TagMender does not write: removed when clear_other_tags (preserved_tags always kept)
    "cleanup": {"clear_other_tags": False, "preserved_tags": ["comment", "rating", "lyrics", "encodedby"],
                "comments": "keep"},
    # release (this pressing's date) | original (the first release's) | tagger (MusicBrainz:
    # the pressing's; Discogs: as your tagger's Discogs settings say)
    "dates": {"date_tag": "release"},
    # style: the version in the album title "Title (Version)" (paren), "Title [Version]"
    # (bracket) or in its own tags only (tags_only); extra_tags: ALBUMVERSION, RELEASECOMMENT
    "versions": {"style": "paren", "extra_tags": True},
    "loudness": {"replace_existing": False, "album_gain": True, "opus_r128": True},
    # compilations (compilations.py): extra_flags - also ITUNESCOMPILATION and COMP in files with
    # free-form text keys (FLAC, Ogg, APE ...); grouping - GROUPING "Compilation" where empty;
    # single_artist - also flag a single artist's compilation (a best-of). va_name (the album
    # artist of a various-artists release) has no default here: unset, the tagger's own
    # setting is used, as before this group existed
    "compilations": {"extra_flags": False, "grouping": False, "single_artist": False},
    # marks.py: grouping - GROUPING "HD Audio", "Vinyl Rip" or "Tape Rip" where empty
    "marks": {"grouping": True},
    # album artist as the lead artist alone (album_artist.py): lead - a joint credit's first
    # artist, and (bands) a band named after a member filed under that member; file_under -
    # your own names {"credit": "album artist"} (a name mapped to itself is kept as credited)
    "album_artist": {"lead": True, "bands": True, "file_under": {}},      # NAM-1
    # artwork: sources - where covers come from, in order (coverup: CoverUp with all its sources;
    #   caa: MusicBrainz's Cover Art Archive; discogs; web: shops and web image search via
    #   CoverUp; local: the album's own scans); when - "needed" (only for albums missing a cover
    #   in a track or the folder) or "always"; enlarge - a cover smaller than min_px (but at
    #   least 250 px) is enlarged to min_px; generate - when nothing is found: off | template
    #   (CoverUp's offline renderer) | ai (the AI service set in CoverUp) | auto (CoverUp's order);
    #   replace - larger_only: an album's own cover smaller than min_px (embedded or cover.jpg) is
    #   replaced by a larger one found, else by itself enlarged (backed up, undoable; the default,
    #   ART-1) | never
    "artwork": {"enabled": True, "provider": "coverup", "sources": ["caa", "discogs", "coverup"],
                "when": "needed", "enlarge": True, "generate": "off", "embed_front": True, "files": True,
                # ART-1: your own cover is kept unless it is small (under min_px, 500 px)
                "replace": "larger_only", "min_px": 500, "max_bytes": 20_000_000,
                # the longest side of the cover put into each track (0: as found)
                "embed_max_side": 1200,
                # the album artist's photo, at Enthusiast and Audiophile: artist.jpg in the artist
                # folder (file; "" for none) and in each track as an "Artist/performer" picture
                "artist": {"enabled": False, "file": "artist.jpg", "embed": False}},
    # lyrics from LRCLIB: sidecar - a .lrc beside the track (synced lyrics only); embed - in the
    # file; synced - prefer time-synced lyrics (off: the plain text, written into the file)
    "lyrics": {"enabled": False, "synced": True, "embed": True, "sidecar": True},
    "mark_resolved": True,                # TAGMENDER=Resolved on what was identified
    # a release that is one version of its album (MusicBrainz's disambiguation "remaster 2024",
    # Discogs's "Remix 2024", Remastered, Mono, Deluxe Edition) keeps it in the album title,
    # "Title (Version)", when its release group or master holds another version; ALBUMVERSION
    # and RELEASECOMMENT hold it too (engine.keep_version)
    "keep_versions": True,
    # two versions of an album found in one folder (a remaster's files beside its remix's, or
    # a disc of each) are planned into a folder each, tagged from its own release, when every
    # file fits one version's track lengths (engine._versions_split); false: left for review
    "split_versions": True,
    # where it goes
    "operation": "in_place",              # in_place | copy | move
    "destination": "",                    # library root for copy/move
    # editions: "Separate editions in folder names": off | edition ([Vinyl], [WEB], [Tape])
    #   | edition_year ([2019 Vinyl], [2014 CD]); album_editions: the same, added to the Album tag
    # duplicate_suffix: a second file of one name gets " (2)" (number) or " ~1a2b" (code)
    "naming": {"preset": "artist-album-track", "template": "", "editions": "off", "album_editions": "off",
               "duplicate_suffix": "number"},
    "rename_in_place": False,             # in_place: also rename the files where they are
    "on_existing": "keep_both",           # a file of that name already there: keep_both | skip | ask
    "companions": COMPANIONS_V2 + [".nfo", ".accurip", ".md5", ".sfv", ".ffp", ".toc", ".tif", ".tiff", ".heic",
                                   ".sha1", ".sha256", ".crc"],
    "companions_rewrite_cues": True,      # new file names written into cue sheets and playlists
    "remove_empty_dirs": True,
    # folders of their own inside the album folder (tidy.py): covers - every picture and PDF
    # booklet but the front cover (cover.jpg, folder.jpg ... stay beside the music) into
    # covers_name; misc - rip logs, notes and checksums into misc_name (cue sheets and playlists
    # stay beside the audio they name); where - "disc": each disc folder of a set gets its own,
    # "album": one for the whole album, a folder per disc inside it
    # covers: the album's pictures and scan folders gathered in one folder (Artwork); the front
    # cover stays beside the music (tidy.py)
    "subfolders": {"covers": True, "covers_name": "Artwork", "misc": False, "misc_name": "Extras", "where": "album"},
    # max_album_words: the album folder's name cut to this many words (0: the full title)
    # compat windows: names every system and NAS share takes; fat (with windows): a USB stick
    # or car player too - paths of at most 255 characters on the stick
    "filenames": {"compat": "windows", "fat": False, "max_name_bytes": 255, "max_dir_bytes": 255,
                  "max_path_chars": 4096, "max_album_words": 0, "abbreviations": {}},
    "backup_retention_days": 30,
    "allow_unverified_writes": False,     # write tags to formats whose audio data cannot be checked
    "preserve_timestamps": None,          # keep files' modification times (None: as your tagger is set)
    # albums nothing could identify. allow_partial: an album whose release is not proven but
    # which is itself established (same artist, title, track count and lengths) gets the
    # release-independent fields and TAGMENDER=Partial (levels.PARTIAL_FIELDS); off: the
    # Unresolved marker only
    "unresolved": {"action": "mark", "move_to": "", "allow_partial": True},
    "retry": {"after_days": 30, "max_attempts": 6},
    "unattended": False,                  # apply plans without asking
    # recheck_after_update: albums this profile filed are looked up again after an update
    "watch": {"settle_minutes": 10, "interval_seconds": 300, "workers": 1, "recheck_after_update": False},
    "exclusions": [],
}

EXAMPLES = {
    "Lossy": {
        "name": "Lossy",
        "description": "Copies identified lossy files (MP3, Opus, Ogg, AAC) into the lossy library. "
                       "Lossless files (ALAC in .m4a too) are left out, never transcoded; the originals "
                       "stay where they are.",
        "formats": ["lossy"],
        "operation": "copy",
        "destination": "",                # yours to choose: validate says so until it is set
        "unresolved": {"action": "mark", "move_to": "", "allow_partial": False},
    },
}

# What a profile written before schema 3 did with the settings it left unset: these are
# written into it (nested keys one by one) when it is read, so an upgrade never changes what
# it does. The keys are dotted paths into the profile.
V2_BEHAVIOUR = {
    "source": "discogs_preferred",
    "follow_tagger_settings": True,
    "exclude_paths": ["*/2delete/*", "*/_unresolved/*", "*/.Trash*/*"],
    "skip_folders": ["_*", "2delete"],
    "providers.acoustid.enabled": False,
    "providers.web.partial": True,
    "matching.auto_countries": ["CA", "US", "XW", "XE", "GB", "DE"],
    "genres.musicbrainz": "tagger",
    "genres.discogs_styles": "tagger",
    "genres.web": "keywords",
    "discogs_details.performers": "tagger",
    "discogs_details.companies": "tagger",
    "discogs_details.identifiers": "tagger",
    "discogs_details.marketplace": "tagger",
    "discogs_details.notes": "tagger",
    "artwork.sources": ["coverup", "caa", "discogs"],
    "artwork.replace": "larger_only",
    "artwork.artist.enabled": True,
    "artwork.artist.file": "",
    "artwork.artist.embed": True,
    "lyrics.embed": False,
    "naming.preset": "tagmender",
    "naming.duplicate_suffix": "code",
    "companions": COMPANIONS_V2,
    "subfolders.where": "disc",
    "dates.date_tag": "tagger",
    "subfolders.covers": False,                   # (on by default since; such a profile kept it off)
    "subfolders.covers_name": "covers",
    "subfolders.misc_name": "misc",
    "filenames.max_album_words": 5,
    "formats_by_codec": False,
}


ARTWORK_SOURCES = ("coverup", "caa", "discogs", "web", "local")
ARTWORK_GENERATE = ("off", "template", "ai", "auto")


class ProfileError(ValueError):
    pass


def profiles_dir() -> Path:
    return ex.CONFIG_DIR / "profiles"


def path_of(name: str) -> Path:
    return ex.profile_file(name)


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ("abbreviations",):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


# Older names of profile settings, still read (and written back under today's names).
LEGACY_KEYS = {"picard": "tagger", "follow_picard": "follow_tagger_settings"}
LEGACY_PRESETS = {"picard": "standard"}


def rename_legacy(data: dict) -> dict:
    """The profile with older setting names in today's form: the "picard" section is "tagger",
    "follow_picard" is "follow_tagger_settings", naming preset "picard" is "standard"; a web search
    engine no longer offered is left out. A today's name already there wins over the old one.
    Returns a new dict; nothing else changes."""
    data = dict(data or {})
    for old, new in LEGACY_KEYS.items():
        if old in data:
            value = data.pop(old)
            data.setdefault(new, value)
    w = data.get("watch")
    if isinstance(w, dict) and isinstance(w.get("interval_seconds"), (int, float)) and \
            not isinstance(w["interval_seconds"], bool) and w["interval_seconds"] > 86400:
        # (before 0.5.0 any interval was allowed: a longer one is a day now, the longest offered)
        data["watch"] = dict(w, interval_seconds=86400)
    if "source" not in data:
        src = legacy_source(data)
        if src:
            data["source"] = src
            if data.get("primary") in PRIMARY:
                data.pop("primary")
    naming = data.get("naming")
    if isinstance(naming, dict) and naming.get("preset") in LEGACY_PRESETS:
        data["naming"] = dict(naming, preset=LEGACY_PRESETS[naming["preset"]])
    if _lists_retired_engine(data):
        data = _without_retired_engines(data)
    return data


def _web_search_of(data: dict):
    providers = data.get("providers")
    web = providers.get("web") if isinstance(providers, dict) else None
    return web.get("search") if isinstance(web, dict) else None


def _lists_retired_engine(data: dict) -> bool:
    """providers.web.search names an engine TagMender no longer offers (RETIRED_WEB_ENGINES)."""
    search = _web_search_of(data or {})
    if isinstance(search, str):
        return search in RETIRED_WEB_ENGINES
    if not isinstance(search, dict):
        return False
    engines = search.get("engines")
    cap = search.get("monthly_cap")
    return (search.get("engine") in RETIRED_WEB_ENGINES
            or isinstance(engines, list) and any(e in RETIRED_WEB_ENGINES for e in engines)
            or isinstance(cap, dict) and any(k in RETIRED_WEB_ENGINES for k in cap))


def _without_retired_engines(data: dict) -> dict:
    """data with the engines TagMender no longer offers left out of providers.web.search (the
    others keep their order). Only the search setting is copied; nothing else changes."""
    providers = dict(data["providers"])
    web = providers["web"] = dict(providers["web"])
    search = web["search"]
    if isinstance(search, str):
        del web["search"]
    else:
        search = web["search"] = dict(search)
        if search.get("engine") in RETIRED_WEB_ENGINES:
            del search["engine"]
        if isinstance(search.get("engines"), list):
            search["engines"] = [e for e in search["engines"] if e not in RETIRED_WEB_ENGINES]
        if isinstance(search.get("monthly_cap"), dict):
            search["monthly_cap"] = {k: v for k, v in search["monthly_cap"].items()
                                     if k not in RETIRED_WEB_ENGINES}
    return dict(data, providers=providers)


def source_allows(source: str, name: str) -> bool:
    """The Sources setting lets this database ("musicbrainz" or "discogs") identify."""
    return not (name == "musicbrainz" and source == "discogs_only" or
                name == "discogs" and source == "musicbrainz_only")


def legacy_source(data: dict) -> str:
    """The "source" an older profile meant ("" when it said nothing): its Discogs Enrichment
    merge mode when that wrote one database's tags only (mb_only, discogs_only: that database
    preferred, both still identifying), else its "primary" (discogs, musicbrainz; "both"
    reads as discogs_preferred, what the matcher already did)."""
    de = data.get("discogs_enrichment")
    merge = de.get("merge_mode") if isinstance(de, dict) else None
    if merge in MERGE_SOURCE:
        return MERGE_SOURCE[merge]
    return PRIMARY_SOURCE.get(data.get("primary"), "")


def has_legacy(data: dict) -> bool:
    """data still uses an older setting name (see rename_legacy)."""
    data = data or {}
    naming = data.get("naming")
    return any(k in data for k in LEGACY_KEYS) or "primary" in data or (
        isinstance(naming, dict) and naming.get("preset") in LEGACY_PRESETS) or _lists_retired_engine(data)


def keep_v2_behaviour(data: dict) -> dict:
    """A profile file written before schema 3 with every setting it left unset written in as
    version 2 read it (V2_BEHAVIOUR), so it does today what it did then. Settings it sets stay
    as they are; nested settings are filled one key at a time. Returns a new dict."""
    data = copy.deepcopy(rename_legacy(data))
    for dotted, value in V2_BEHAVIOUR.items():
        *parents, last = dotted.split(".")
        d = data
        for k in parents:
            if not isinstance(d.get(k), dict):
                if k in d:              # (a wrong value: left for validate to name)
                    d = None
                    break
                d[k] = {}
            d = d[k]
        if d is not None and last not in d:
            d[last] = copy.deepcopy(value)
    fn = data.get("filenames")
    if isinstance(fn, dict) and "max_album_words" in fn:
        # (version 2 read 0, or anything not a number, as 5 words)
        w = fn["max_album_words"]
        if isinstance(w, bool) or not isinstance(w, int) or w <= 0:
            fn["max_album_words"] = 5
    tg = data.get("tagger") if isinstance(data.get("tagger"), dict) else None
    if tg and tg.get("translate_artist_names") and not data.get("follow_tagger_settings", True):
        tg.setdefault("translation_locales", ["en"])     # (version 2: English, whatever your language)
    return data


def raised_thresholds(data: dict) -> list[str]:
    """Notes on the thresholds of a file written before schema 3 that are below today's floor:
    they are read as the floor (keep_v2_behaviour)."""
    th = (data or {}).get("thresholds")
    if int((data or {}).get("version") or 0) >= 3 or not isinstance(th, dict):
        return []
    return [f"thresholds.{k} {th[k]:g} is read as {floor:g}, the lowest allowed"
            for k, floor in THRESHOLD_FLOORS.items()
            if isinstance(th.get(k), (int, float)) and not isinstance(th.get(k), bool) and th[k] < floor]


def older_behaviour(data: dict) -> dict:
    """A profile written before schema 3 (or 2) with what it left unset read as that version
    read it, so an upgrade never changes what it does. A current one is returned as it is."""
    v = int(data.get("version") or 0)
    if v < 3:
        data = keep_v2_behaviour(data)
        th = data.get("thresholds")
        if isinstance(th, dict):
            for k, floor in THRESHOLD_FLOORS.items():    # (version 2 took 0-100: raised to the floor)
                x = th.get(k)
                if isinstance(x, (int, float)) and not isinstance(x, bool) and x < floor:
                    th[k] = floor
    if v < 2:
        # (a profile saved before these followed Picard (version 1, as every release but one
        # wrote it): unset, they were Windows-safe names and empty folders removed - so they stay,
        # whatever Picard says now)
        fn = data.setdefault("filenames", {})
        if isinstance(fn, dict):
            fn.setdefault("compat", "windows")
        data.setdefault("remove_empty_dirs", True)
    return data


def upgrade(data: dict) -> dict:
    """An older profile in today's form. Version 0 (no "version") held only a name and
    Ignore/Skip rules; nothing it said changes meaning. Older setting names are renamed, and
    what an earlier version left unset is written in as it read it (older_behaviour) - for a
    file and for a dict kept elsewhere that names its version (a saved plan's profile) alike."""
    data = rename_legacy(data)
    v = int(data.get("version") or 0)
    if v > SCHEMA_VERSION:
        raise ProfileError(f"profile {data.get('name')!r} was written by a newer TagMender (version {v})")
    if "version" in data:               # (a dict made in code, with no version, is today's; a file
        data = older_behaviour(data)    #  without one is read as version 0 by raw())
    data["version"] = SCHEMA_VERSION
    return data


def validate(data: dict) -> list[str]:
    """Problems that would stop the profile from working, in plain words ([] = usable)."""
    p = []
    d = _merge(DEFAULTS, rename_legacy(data))
    if not str(d["name"]).strip():
        p.append("name is empty")
    if re.search(r"[/\\\x00]", str(d["name"])):
        p.append("name must not contain / or \\")
    if d["operation"] not in OPERATIONS:
        p.append(f"operation must be one of {', '.join(OPERATIONS)}")
    if d["operation"] in ("copy", "move"):
        if not d["destination"]:
            p.append(f"operation '{d['operation']}' needs a destination")
        elif not os.path.isabs(os.path.expanduser(d["destination"])):
            p.append("destination must be an absolute path")
    if d["source"] not in SOURCES:
        p.append(f"source must be one of {', '.join(SOURCES)}")
    if "primary" in d and d["primary"] not in PRIMARY:
        p.append(f"primary must be one of {', '.join(PRIMARY)} (it is the older form of source)")
    if d.get("ocr", "auto") not in OCR:
        p.append(f"ocr must be one of {', '.join(OCR)}")
    if d.get("acoustid", "unsure") not in ACOUSTID:
        p.append(f"acoustid must be one of {', '.join(ACOUSTID)}")
    if d["tradition"] not in TRADITIONS:
        p.append(f"tradition must be one of {', '.join(TRADITIONS)}")
    for name in d["providers"]:
        if name not in PROVIDERS:
            p.append(f"unknown provider '{name}' (use {', '.join(PROVIDERS)})")
    ident = [n for n in ("musicbrainz", "discogs") if d["providers"].get(n, {}).get("enabled")
             and d["providers"][n].get("identify", True)]
    allowed = [n for n in ident if source_allows(d["source"], n)]
    if ident and not allowed and d["source"] in SOURCES:
        # ("Discogs only" with Discogs turned off: the one database Sources allows is not asked)
        only = "Discogs" if d["source"] == "discogs_only" else "MusicBrainz"
        p.append(f"source is {SOURCE_WORDS[d['source']]} but {only} is not enabled to identify: "
                 "no database identifies music")
    elif not ident and not d["providers"].get("web", {}).get("enabled"):
        p.append("no provider is enabled to identify music")
    search = (d["providers"].get("web") or {}).get("search") or {}
    if isinstance(search, str):                               # "search": "mojeek" for short
        search = {"engine": search}
    if not isinstance(search, dict):
        p.append("providers.web.search must be an object, e.g. {\"engines\": [\"mojeek\"]}")
    else:
        if search.get("engine", "none") not in WEB_ENGINES:
            p.append(f"providers.web.search.engine must be one of {', '.join(WEB_ENGINES)}")
        engines = search.get("engines", [])
        if not isinstance(engines, list) or any(e not in WEB_ENGINES[1:] for e in engines):
            p.append(f"providers.web.search.engines must be a list of {', '.join(WEB_ENGINES[1:])} (tried in order)")
        cap = search.get("monthly_cap")
        caps = cap if isinstance(cap, dict) else {"": cap}
        if (any(k not in WEB_ENGINES[1:] for k in caps if k) or
                any(c is not None and (isinstance(c, bool) or not isinstance(c, int) or c < 0)
                    for c in caps.values())):
            p.append("providers.web.search.monthly_cap must be a whole number of 0 or more, null, "
                     "or {engine: number}")
    for fld, srcs in d["field_sources"].items():
        if not isinstance(srcs, list) or any(s not in PROVIDERS for s in srcs):
            p.append(f"field_sources.{fld} must be a list of {', '.join(PROVIDERS)}")
    pol = d["field_policy"]
    if pol.get("default") not in POLICIES:
        p.append(f"field_policy.default must be one of {', '.join(POLICIES)}")
    for fld, v in (pol.get("fields") or {}).items():
        if v not in POLICIES:
            p.append(f"field_policy.fields.{fld} must be one of {', '.join(POLICIES)}")
    th = d["thresholds"]
    for k, v in th.items():
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= float(v) <= 100:
            p.append(f"thresholds.{k} must be a number from 0 to 100")
        elif k in THRESHOLD_FLOORS and float(v) < THRESHOLD_FLOORS[k]:
            # (lower values tag albums with releases that are not proven: never loosened)
            p.append(f"thresholds.{k} must not be below {THRESHOLD_FLOORS[k]:g} (it may only be raised)")
    if th.get("organize_min_confidence", 100) < th.get("auto_min_confidence", 0):
        p.append("thresholds.organize_min_confidence must not be below auto_min_confidence "
                 "(moving files needs at least as much proof as identifying them)")
    fmts = [str(f).lower().lstrip(".") for f in d["formats"]] if isinstance(d["formats"], list) else None
    if fmts is None:
        p.append("formats must be a list (e.g. [\"flac\", \"mp3\"] or [\"lossless\"])")
    known = set(LOSSY) | set(LOSSLESS) | set(FORMAT_KINDS) | {"mp4"}
    for f in fmts or []:
        if f not in known:
            p.append(f"unknown format '{f}'")
    p += _skip_problems(d)
    p += _new_setting_problems(d)
    for k in CODE_KEYS:
        if k in (data or {}):
            p.append(f"{k} is not a profile setting: a profile never names code to run "
                     "(set it in Preferences > Advanced on this computer)")
    fn = d["filenames"]
    if fn.get("compat") not in COMPAT:
        p.append(f"filenames.compat must be one of {', '.join(COMPAT)}")
    for k in ("max_name_bytes", "max_dir_bytes"):
        if not isinstance(fn.get(k), int) or not 16 <= fn[k] <= 255:
            p.append(f"filenames.{k} must be a whole number from 16 to 255")
    if not isinstance(fn.get("max_path_chars"), int) or fn["max_path_chars"] < 64:
        p.append("filenames.max_path_chars must be a whole number of at least 64")
    words = fn.get("max_album_words")
    if not isinstance(words, int) or isinstance(words, bool) or not 0 <= words <= 20:
        p.append("filenames.max_album_words must be a whole number from 0 (the full title) to 20")
    if d["unresolved"].get("action") not in UNRESOLVED_ACTIONS:
        p.append(f"unresolved.action must be one of {', '.join(UNRESOLVED_ACTIONS)}")
    if not isinstance(d.get("keep_versions", True), bool):
        p.append("keep_versions must be true or false")
    if not isinstance(d.get("split_versions", True), bool):
        p.append("split_versions must be true or false")
    import tidy
    p += tidy.problems(d.get("subfolders"))
    if not isinstance(d["unresolved"].get("allow_partial", True), bool):
        p.append("unresolved.allow_partial must be true or false")
    if d["unresolved"].get("action") == "move" and not d["unresolved"].get("move_to"):
        p.append("unresolved.action 'move' needs unresolved.move_to")
    for i, s in enumerate(d["sources"]):
        if not isinstance(s, dict) or not s.get("path"):
            p.append(f"sources[{i}] needs a path")
        elif not os.path.isabs(os.path.expanduser(s["path"])):
            p.append(f"sources[{i}].path must be absolute")
        elif s.get("watch") and os.path.dirname(os.path.abspath(os.path.expanduser(s["path"]))) == \
                os.path.abspath(os.path.expanduser(s["path"])):
            p.append(f"sources[{i}]: a whole drive ({s['path']}) cannot be watched; watch a folder on it")
        elif not isinstance(s.get("destination", ""), str):
            p.append(f"sources[{i}].destination must be a folder's path, or empty for the profile's destination")
        elif s.get("destination") and not os.path.isabs(os.path.expanduser(s["destination"])):
            p.append(f"sources[{i}].destination must be an absolute path")
    w = d.get("watch") or {}
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: E731
    if not num(w.get("settle_minutes")) or not w["settle_minutes"] >= 0:      # (NaN too)
        p.append("watch.settle_minutes must be a number of minutes, 0 or more")
    if not num(w.get("interval_seconds")) or not 1 <= w["interval_seconds"] <= 86400:
        p.append("watch.interval_seconds must be a number of seconds, 1 to 86400 (a day)")
    if d["preserve_timestamps"] not in (None, True, False):
        p.append("preserve_timestamps must be true, false or null (as your tagger settings say)")
    import levels
    art = d["artwork"] or {}
    bad = [x for x in (art.get("sources") or []) if x not in ARTWORK_SOURCES]
    if bad or not art.get("sources"):
        p.append(f"artwork.sources must be a list of {', '.join(ARTWORK_SOURCES)}")
    if art.get("when", "needed") not in ("needed", "always"):
        p.append("artwork.when must be needed or always")
    ar = art.get("artist") or {}
    if not isinstance(ar, dict) or not isinstance(ar.get("enabled", True), bool) or \
            not isinstance(ar.get("embed", True), bool) or not isinstance(ar.get("file", ""), str) or \
            any(c in (ar.get("file") or "") for c in "/\\") or \
            (ar.get("file") and not ar["file"].lower().endswith((".jpg", ".jpeg"))):
        p.append('artwork.artist must be {"enabled": true|false, "file": "artist.jpg" or "", "embed": true|false}')
    if art.get("replace", "larger_only") not in ("never", "larger_only"):
        p.append("artwork.replace must be never or larger_only")
    if not isinstance(art.get("min_px", 0), int) or isinstance(art.get("min_px"), bool) or art.get("min_px", 0) < 0:
        p.append("artwork.min_px must be a whole number of pixels, 0 or more")
    if art.get("generate", "off") not in ARTWORK_GENERATE:
        p.append(f"artwork.generate must be one of {', '.join(ARTWORK_GENERATE)}")
    import naming as _naming
    if not isinstance((d["naming"] or {}).get("template") or "", str):
        p.append("naming.template must be a naming script (text)")
    for k in ("editions", "album_editions"):
        if (d["naming"] or {}).get(k, "off") not in _naming.EDITIONS:
            p.append(f"naming.{k} must be one of {', '.join(_naming.EDITIONS)}")
    if d["tag_level"] not in levels.LEVELS:
        p.append(f"tag_level must be one of {', '.join(levels.LEVELS)}")
    for k in ("add", "drop"):
        v = (d["level_fields"] or {}).get(k, [])
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            p.append(f"level_fields.{k} must be a list of tag names")
    for k, v in (d["audiophile"] or {}).items():
        if k not in levels.AUDIOPHILE_ITEMS:
            p.append(f"audiophile.{k} is not a measurement (use {', '.join(levels.AUDIOPHILE_ITEMS)})")
        elif not isinstance(v, bool):
            p.append(f"audiophile.{k} must be true or false")
    if not isinstance(d["discogs_enrichment"], dict):
        p.append("discogs_enrichment must be an object of Discogs Enrichment settings")
    else:
        import tagger_settings
        p += tagger_settings.validate(d["discogs_enrichment"])
    if not isinstance(d.get("tagger", {}), dict):
        p.append("tagger must be an object of tagger settings")
    else:
        import tagger_settings
        p += tagger_settings.validate_tag_settings(d.get("tagger") or {})
    for r in d["exclusions"]:
        try:
            ex.Rule.from_dict(r, d["name"] or "profile")
        except ex.RuleError as exc:
            p.append(str(exc))
    return p


def _real(p) -> str:
    return os.path.realpath(os.path.expanduser(str(p)))


def _skip_problems(d: dict) -> list[str]:
    """Skip patterns that would hide everything, or the profile's own folders."""
    import fnmatch
    p = []
    skip, globs = d.get("skip_folders"), d.get("exclude_paths")
    if not isinstance(skip, list) or not all(isinstance(x, str) and x.strip() for x in skip):
        p.append("skip_folders must be a list of folder names (patterns like _* allowed)")
        skip = []
    if not isinstance(globs, list) or not all(isinstance(x, str) and x.strip() for x in globs):
        p.append("exclude_paths must be a list of path patterns")
        globs = []
    for x in skip:
        if x.strip().strip("*") == "" or "/" in x:
            p.append(f"skip_folders: {x!r} would skip every folder (name folders, e.g. _* or Downloads)")
    for g in globs:
        if g.strip().strip("*/") == "":
            p.append(f"exclude_paths: {g!r} would skip everything")
    roots = [s["path"] for s in d.get("sources") or [] if isinstance(s, dict) and s.get("path")]
    roots += filing_roots(d)
    builtin = set(DEFAULTS["exclude_paths"]) | set(V2_BEHAVIOUR["exclude_paths"])
    for root in roots:
        r = _real(root)
        for g in globs:
            if g in builtin:            # (built-in patterns: a folder you name is still looked at)
                continue
            if fnmatch.fnmatchcase(r, g) or fnmatch.fnmatchcase(r + "/x", g):
                p.append(f"exclude_paths: {g!r} matches the profile's own folder {root}")
    return p


def _choice(p, d, dotted, allowed):
    v = d
    for k in dotted.split("."):
        v = v.get(k) if isinstance(v, dict) else None
    if v not in allowed or isinstance(v, bool) and not any(a is v for a in allowed):
        p.append(f"{dotted} must be one of {', '.join(map(str, allowed))}")


def _bool(p, d, dotted):
    v = d
    for k in dotted.split("."):
        v = v.get(k) if isinstance(v, dict) else None
    if not isinstance(v, bool):
        p.append(f"{dotted} must be true or false")


def _new_setting_problems(d: dict) -> list[str]:
    """The settings schema 3 added."""
    p = []
    for k in ("grouping", "destinations", "identification", "singles", "matching", "genres",
              "discogs_details", "cleanup", "dates", "versions", "loudness"):
        if not isinstance(d.get(k), dict):
            p.append(f"{k} must be an object")
            return p
    _bool(p, d, "grouping.join_deluxe_folders")
    _bool(p, d, "formats_by_codec")
    lossy = d["destinations"].get("lossy")
    if not isinstance(lossy, str) or (lossy and not os.path.isabs(os.path.expanduser(lossy))):
        p.append("destinations.lossy must be an absolute path, or empty")
    _choice(p, d, "quality_action", QUALITY_ACTIONS)
    if d.get("quality_action") == "move" and not (d.get("unresolved") or {}).get("move_to"):
        p.append("quality_action 'move' needs unresolved.move_to (where such albums are set aside)")
    _choice(p, d, "identification.accept", ACCEPT)
    _bool(p, d, "singles.identify")
    m = d["matching"]
    for k in ("preferred_countries", "auto_countries"):
        v = m.get(k)
        word = ("auto", "none") if k == "preferred_countries" else ("region",)
        if not (v in word or isinstance(v, list) and all(isinstance(c, str) and re.fullmatch(r"[A-Za-z]{2}", c)
                                                        for c in v)):
            p.append(f"matching.{k} must be {' or '.join(word)}, or a list of two-letter country codes")
    for k in ("preferred_release_types", "discouraged_release_types", "preferred_formats"):
        if not isinstance(m.get(k), list) or not all(isinstance(x, str) for x in m[k]):
            p.append(f"matching.{k} must be a list of names")
    _bool(p, d, "matching.extra_careful")
    g = d["genres"]
    if g.get("musicbrainz") not in ("auto", "tagger") and not isinstance(g.get("musicbrainz"), bool):
        p.append("genres.musicbrainz must be auto, tagger, true or false")
    if not isinstance(g.get("max"), int) or isinstance(g.get("max"), bool) or not 1 <= g["max"] <= 20:
        p.append("genres.max must be a whole number from 1 to 20")
    _choice(p, d, "genres.discogs_styles", DISCOGS_STYLES)
    _choice(p, d, "genres.web", WEB_GENRES)
    for k, v in d["discogs_details"].items():
        if not isinstance(v, bool) and v not in ("audiophile", "tagger"):
            p.append(f"discogs_details.{k} must be true, false, audiophile or tagger")
    _bool(p, d, "cleanup.clear_other_tags")
    aa = d.get("album_artist")
    if not isinstance(aa, dict):
        p.append("album_artist must be an object")
    else:
        for k in ("lead", "bands"):
            _bool(p, d, f"album_artist.{k}")
        fu = aa.get("file_under")
        if not isinstance(fu, dict) or not all(isinstance(k, str) and isinstance(v, str) and 0 < len(k) <= 200
                                               and 0 < len(v) <= 200 for k, v in fu.items()):
            p.append("album_artist.file_under must map names to names (up to 200 characters each)")
    comp = d.get("compilations")
    if not isinstance(comp, dict):
        p.append("compilations must be an object")
    else:
        for k in ("extra_flags", "grouping", "single_artist"):
            _bool(p, d, f"compilations.{k}")
    if not isinstance(d.get("marks"), dict):
        p.append("marks must be an object")
    else:
        _bool(p, d, "marks.grouping")
    _bool(p, d, "watch.recheck_after_update")
    comp = d.get("compilations")
    if isinstance(comp, dict):
        if "va_name" in comp and not (isinstance(comp["va_name"], str) and comp["va_name"].strip()
                                      and len(comp["va_name"]) <= 100):
            p.append("compilations.va_name must be a name of up to 100 characters")
    keep = d["cleanup"].get("preserved_tags")
    if not isinstance(keep, list) or not all(isinstance(x, str) for x in keep):
        p.append("cleanup.preserved_tags must be a list of tag names")
    _choice(p, d, "cleanup.comments", ("keep", "remove"))
    _choice(p, d, "dates.date_tag", DATE_TAGS)
    _choice(p, d, "versions.style", VERSION_STYLES)
    _bool(p, d, "versions.extra_tags")
    for k in ("replace_existing", "album_gain", "opus_r128"):
        _bool(p, d, f"loudness.{k}")
    for k in ("rename_in_place", "companions_rewrite_cues"):
        _bool(p, d, k)
    for k in ("enabled", "synced", "embed", "sidecar"):
        _bool(p, d, f"lyrics.{k}")
    for k, low in (("after_days", 0), ("max_attempts", 1)):
        v = (d.get("retry") or {}).get(k)
        if isinstance(v, bool) or not isinstance(v, int) or not low <= v <= 1000:
            p.append(f"retry.{k} must be a whole number from {low} to 1000")
    # (quality_action "move" on a copy profile holds the album: plan.py)
    _choice(p, d, "on_existing", ON_EXISTING)
    _choice(p, d, "naming.duplicate_suffix", DUPLICATE_SUFFIX)
    src = (d["providers"].get("web") or {}).get("sources", list(WEB_SOURCES))
    if not isinstance(src, list) or any(x not in WEB_SOURCES for x in src):
        p.append(f"providers.web.sources must be a list of {', '.join(WEB_SOURCES)} (tried in order)")
    comp = d.get("companions")
    if not isinstance(comp, list) or not all(isinstance(x, str) and x.startswith(".") for x in comp):
        p.append('companions must be a list of extensions (".cue", ".log", ...)')
    _choice(p, d, "destinations.mounted_only", MOUNT_GUARDS)
    _choice(p, d, "on_unwritable", ON_UNWRITABLE)
    _bool(p, d, "filenames.fat")
    if d["filenames"].get("fat") and d["filenames"].get("compat") != "windows":
        p.append("filenames.fat needs filenames.compat windows (a USB stick takes only names Windows takes)")
    side = (d.get("artwork") or {}).get("embed_max_side", 1200)
    if isinstance(side, bool) or not isinstance(side, int) or not (side == 0 or 300 <= side <= 10000):
        p.append("artwork.embed_max_side must be 0 (as found) or a number of pixels from 300 to 10000")
    return p


@dataclass
class Profile:
    data: dict = field(default_factory=dict)

    def __getitem__(self, key):
        return self.data[key]

    def get(self, key, default=None):
        return self.data.get(key, default)

    @property
    def name(self):
        return self.data["name"]

    def follows_tagger(self) -> bool:
        """The profile reads your tagger settings (follow_tagger_settings; older "follow_picard")."""
        d = self.data
        return bool(d.get("follow_tagger_settings", d.get("follow_picard", True)))

    def tagger_overrides(self) -> dict:
        """The profile's own tagger settings ("tagger"; older "picard"), with what its own
        settings say about the same things: release countries, types and formats to prefer
        (matching), and MusicBrainz genres (genres.musicbrainz). An entry in "tagger" wins."""
        d = self.data
        over = dict(d.get("tagger") or d.get("picard") or {})
        m = d.get("matching") or {}
        for own, key in (("preferred_release_types", "preferred_release_types"),
                         ("discouraged_release_types", "discouraged_release_types"),
                         ("preferred_formats", "preferred_release_formats")):
            if m.get(own):
                over.setdefault(key, list(m[own]))
        if isinstance(m.get("preferred_countries"), list) and m["preferred_countries"]:
            over.setdefault("preferred_release_countries", [c.upper() for c in m["preferred_countries"]])
        if not self.follows_tagger() and over.get("translate_artist_names"):
            # (translated artist names: into your own language, unless the profile names one)
            over.setdefault("translation_locales", [system_language()])
        g = d.get("genres") or {}
        mb = g.get("musicbrainz", "auto")
        if isinstance(mb, bool) or (mb == "auto" and not self.follows_tagger()):
            over.setdefault("use_genres", True if mb == "auto" else mb)
            if isinstance(g.get("max"), int):
                over.setdefault("max_genres", g["max"])
        return over

    def preferred_countries(self, tagger_countries=()) -> list:
        """The country order that breaks ties between pressings: the profile's own list; for
        "auto", the tagger's list (tagger_countries) when there is one, else auto_countries
        (a list, or "region": your region, then Worldwide, then Europe); [] for "none"."""
        m = self.data.get("matching") or {}
        v = m.get("preferred_countries", "auto")
        if isinstance(v, list) and v:
            return [c.upper() for c in v]
        if v == "none":
            return []
        if tagger_countries:
            return list(tagger_countries)
        auto = m.get("auto_countries", "region")
        if isinstance(auto, list) and auto:
            return [c.upper() for c in auto]
        return region_countries()

    def threshold(self, key: str) -> float:
        """One matcher threshold: the profile's, raised by Extra-Careful Matching (never lowered)."""
        v = float(self.data["thresholds"][key])
        if (self.data.get("matching") or {}).get("extra_careful") and key in CAREFUL:
            v = max(v, CAREFUL[key])
        return v

    # ---- helpers the engine asks --------------------------------------------
    def wants_format(self, path) -> bool:
        """The profile takes this file: its format by extension, or its kind ("lossless",
        "lossy") by its codec - an .m4a or .mp4 holding ALAC is lossless, AAC lossy."""
        fmts = {str(f).lower().lstrip(".") for f in self.data["formats"]}
        if not fmts:
            return True
        ext = Path(path).suffix.lower().lstrip(".")
        if ext in fmts:
            return True
        if not (fmts & set(FORMAT_KINDS) or self.data.get("formats_by_codec", True) and fmts & {"alac", "aac"}):
            return False
        kind = audio_kind(path)
        if kind in fmts:
            return True
        if not self.data.get("formats_by_codec", True) or ext not in ("m4a", "mp4"):
            return False
        return kind == "lossless" and "alac" in fmts or kind == "lossy" and "aac" in fmts

    def folder_skipper(self):
        """skips(name): a folder (by its name) the profile never looks into - hidden ones,
        TagMender's own unidentified folder, the duplicates folder under every name it has had,
        and skip_folders. The folder unidentified albums are moved to is skipped by its real
        path (skip_paths), never by its name: an album folder may have the same name. This
        computer's settings are read once, here (a walk asks for every folder)."""
        import fnmatch
        import app_settings
        own = {"_unresolved"} | app_settings.duplicates_folders()
        pats = [x for x in self.data.get("skip_folders") or [] if isinstance(x, str)]

        def skips(name: str) -> bool:
            return name.startswith(".") or name in own or any(fnmatch.fnmatchcase(name, x) for x in pats)
        return skips

    def skip_paths(self) -> set[str]:
        """Real paths of folders never looked into: where unidentified albums are moved to."""
        move_to = (self.data.get("unresolved") or {}).get("move_to") or ""
        return {os.path.realpath(os.path.expanduser(str(move_to)))} if move_to else set()

    def skips_folder(self, name: str) -> bool:
        """One folder name: see folder_skipper."""
        return self.folder_skipper()(name)

    def excluded_path(self, path, named=()) -> bool:
        """path lies in one of exclude_paths - unless that is where a folder named on the
        command line lies (`tagmender retry .../_unresolved`: what you name is looked at)."""
        import fnmatch
        s = str(path)
        inside = [str(n).rstrip("/") + "/x" for n in named or ()]
        return any(fnmatch.fnmatchcase(s, g) and not any(fnmatch.fnmatchcase(i, g) for i in inside)
                   for g in self.data["exclude_paths"])

    def policy(self, fld: str) -> str:
        """The profile's own policy for the field; else, for a tag Picard is set to preserve
        (Options > Tags > "Preserve these tags"), fill_missing - kept as it is on the file,
        written only where the file has none, as Picard does; else the default."""
        pol = self.data["field_policy"]
        fields = pol.get("fields") or {}
        low = fld.lower()
        if low in fields:
            return fields[low]
        if low == "grouping" and (self.data.get("compilations") or {}).get("grouping"):
            return "fill_missing"          # (compilations.grouping: never replaces a Grouping that is there)
        if low in self.picard_preserved():
            return "fill_missing"
        return pol.get("default", "replace")

    def picard_preserved(self) -> set:
        """Picard's preserved tags, less TagMender's own markers (Picard keeps those so that
        it does not wipe them; TagMender writes them)."""
        if not self.follows_tagger():
            return set()
        import tagger_settings
        return {t for t in tagger_settings.preserved_tags() if not t.startswith("tagmender")}

    def preserve_timestamps(self) -> bool:
        v = self.data.get("preserve_timestamps")
        if v is None:
            if not self.follows_tagger():
                return False
            import tagger_settings
            return tagger_settings.picard_bool("preserve_timestamps")
        return bool(v)

    def source(self) -> str:
        """The profile's Sources setting (SOURCES); an unknown value reads as the default."""
        s = self.data.get("source")
        return s if s in SOURCES else DEFAULTS["source"]

    def enrichment(self) -> dict:
        """Discogs Enrichment settings for this profile (see tagger_settings). The merge mode
        follows the Sources setting (SOURCE_MERGE), whatever your tagger settings say; in a
        preferred mode the profile's own discogs_enrichment.merge_mode (OWN_MERGE: fill_gaps,
        override, combine, or an older profile's mb_only / discogs_only) still fine-tunes it."""
        import tagger_settings
        over = dict(self.data.get("discogs_enrichment") or {})
        for k, v in self.discogs_detail_settings().items():
            over.setdefault(k, v)
        src = self.source()
        own = over.get("merge_mode")
        if src.endswith("_only") or own not in OWN_MERGE:
            over["merge_mode"] = SOURCE_MERGE[src]
        if not self.follows_tagger():
            return tagger_settings.enrichment(over, path=Path("/nonexistent/Picard.ini"))
        return tagger_settings.enrichment(over)

    def identifies_with(self, name: str) -> bool:
        """The Sources setting lets this database identify ("musicbrainz" or "discogs")."""
        return source_allows(self.source(), name)

    def prefers(self) -> str:
        """The database whose pick wins when both are sure: "musicbrainz" or "discogs"."""
        return "musicbrainz" if self.source().startswith("musicbrainz") else "discogs"

    def picard_tags(self) -> dict:
        """Picard's settings for the tags written from MusicBrainz (see tagger_settings)."""
        import tagger_settings
        over = self.tagger_overrides()
        if not self.follows_tagger():
            return tagger_settings.tag_settings(over, path=Path("/nonexistent/Picard.ini"))
        return tagger_settings.tag_settings(over)

    def matcher_cfg(self, base: dict) -> dict:
        """match_core's settings with this profile's identification thresholds."""
        cfg = dict(base)
        for k in ("auto_min_confidence", "auto_min_margin"):
            cfg[k] = self.threshold(k)
        cfg["web_search"] = self.web_search()
        cfg["prefer_source"] = self.prefers()
        return cfg

    def web_search(self) -> dict:
        """{"engines", "monthly_cap"}: the general web search engines this profile tries, in
        order, for the codes on the scans; [] when the web provider is off or none is chosen.
        The older {"engine": name} reads as a one-item list. As written: the matcher adds
        Marginalia after a lone Tavily (match_albums.web_engines)."""
        web = self.data["providers"].get("web") or {}
        search = web.get("search")
        search = {"engine": search} if isinstance(search, str) else search if isinstance(search, dict) else {}
        engines = search.get("engines")
        engines = [engines] if isinstance(engines, str) else engines if isinstance(engines, list) else []
        if not engines and search.get("engine"):
            engines = [search["engine"]]
        engines = [e for e in dict.fromkeys(str(e) for e in engines) if e in WEB_ENGINES[1:]]
        return {"engines": engines if web.get("enabled") else [], "monthly_cap": search.get("monthly_cap")}

    # ---- the schema 3 settings the engine and the planner ask -------------------
    def accepts(self, tier: str) -> bool:
        """identification.accept lets an album of this tier be tagged: "exact" always; "album"
        (pressing unsure) unless only exact releases are accepted; "partial" only when Partial
        is accepted too."""
        order = {"exact": 0, "album": 1, "album_only": 1, "partial": 2}
        want = (self.data.get("identification") or {}).get("accept", "partial")
        return order.get(tier, 2) <= order.get(want, 2)

    def identifies_singles(self) -> bool:
        """singles.identify: a loose track is matched to its recording by its fingerprint."""
        return (self.data.get("singles") or {}).get("identify", True) is not False

    def joins_deluxe(self) -> bool:
        """grouping.join_deluxe_folders: a "(Deluxe Edition)" sibling holding only the bonus
        tracks is read with its album."""
        return (self.data.get("grouping") or {}).get("join_deluxe_folders", True) is not False

    def destination_for(self, files, source_dir=None) -> str:
        """The library an identified album goes to: destinations.lossy when one is set and
        every file of the album is lossy, else the destination of the source folder the album
        was found in, else the profile's destination."""
        lossy = (self.data.get("destinations") or {}).get("lossy") or ""
        if lossy and files and all(audio_kind(f) == "lossy" for f in files):
            return lossy
        return (self.source_destination(source_dir) if source_dir else "") or self.data.get("destination") or ""

    def source_destination(self, folder) -> str:
        """The own destination of the source folder that holds FOLDER (the deepest one), or ""."""
        p = os.path.realpath(os.path.expanduser(str(folder)))
        best, depth = "", -1
        for s in self.data.get("sources") or []:
            if not isinstance(s, dict) or not s.get("path"):
                continue
            root = os.path.realpath(os.path.expanduser(s["path"]))
            if (p == root or p.startswith(root.rstrip(os.sep) + os.sep)) and len(root) > depth:
                best, depth = s.get("destination") or "", len(root)
        return best

    def filing_roots(self) -> list[str]:
        """Every library a copy or move files albums into (see filing_roots)."""
        return filing_roots(self.data)

    def mount_guard(self) -> str:
        """destinations.mounted_only (MOUNT_GUARDS)."""
        v = (self.data.get("destinations") or {}).get("mounted_only", "auto")
        return v if v in MOUNT_GUARDS else "auto"

    def discogs_detail_settings(self) -> dict:
        """Discogs Enrichment's settings for the Discogs details (performers, companies, matrix
        and other codes, marketplace numbers, release notes), its style mode (genres.
        discogs_styles) and whether Date holds the first release's date (dates.date_tag), as
        this profile says. "tagger" and "audiophile" (below Audiophile) say nothing: the
        plugin's own setting (your tagger's when followed) stays."""
        out = {}
        level = self.data.get("tag_level", "enthusiast")
        for own, key in DISCOGS_DETAILS.items():
            v = (self.data.get("discogs_details") or {}).get(own, "tagger")
            if isinstance(v, bool):
                out[key] = v
            elif v == "audiophile":
                out[key] = level == "audiophile"
        mode = (self.data.get("genres") or {}).get("discogs_styles", "genre_if_none")
        if mode in STYLE_MODES:
            out["style_mode"] = STYLE_MODES[mode]
        date = (self.data.get("dates") or {}).get("date_tag", "release")
        if date in ("release", "original"):
            out["prefer_original_date"] = date == "original"
        return out

    def styles_fill_genre(self) -> bool:
        """genres.discogs_styles genre_if_none: Discogs's styles go into Genre when nothing
        else gave the album one."""
        return (self.data.get("genres") or {}).get("discogs_styles") == "genre_if_none"

    def date_holds_original(self) -> bool:
        """dates.date_tag original: Date holds the first release's date (the pressing's own
        stays in Release Date)."""
        return (self.data.get("dates") or {}).get("date_tag") == "original"

    def version_style(self) -> str:
        """versions.style (VERSION_STYLES)."""
        v = (self.data.get("versions") or {}).get("style", "paren")
        return v if v in VERSION_STYLES else "paren"

    def version_tags(self) -> bool:
        """versions.extra_tags: ALBUMVERSION and RELEASECOMMENT are written."""
        return (self.data.get("versions") or {}).get("extra_tags", True) is not False

    def provider_on(self, name: str, role: str = "identify") -> bool:
        p = self.data["providers"].get(name) or {}
        return bool(p.get("enabled")) and bool(p.get(role, True))

    def to_dict(self) -> dict:
        return copy.deepcopy(self.data)


def system_language() -> str:
    """Your language's two letters (LC_ALL / LC_MESSAGES / LANG: de_DE.UTF-8 -> de); "en"
    when unknown."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        m = re.match(r"^([a-z]{2})(?:_|\.|$)", os.environ.get(var) or "")
        if m:
            return m.group(1)
    return "en"


def region_countries() -> list:
    """Your region (from the system's language setting, LC_ALL / LC_MESSAGES / LANG: en_CA ->
    CA), then Worldwide (XW) and Europe (XE); Worldwide, Europe and the US when unknown."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        m = re.match(r"^[a-z]{2,3}_([A-Z]{2})", os.environ.get(var) or "")
        if m:
            return list(dict.fromkeys([m.group(1), "XW", "XE"]))
    return ["XW", "XE", "US"]


def audio_kind(path) -> str:
    """ "lossless" or "lossy" by what the file holds; an .m4a / .mp4 is read for its codec
    (ALAC: lossless; AAC: lossy). "" for anything else."""
    ext = Path(path).suffix.lower().lstrip(".")
    if ext in ("m4a", "mp4", "alac"):
        try:
            from mutagen.mp4 import MP4
            codec = str(getattr(MP4(str(path)).info, "codec", "") or "")
        except Exception:
            return "lossless" if ext == "alac" else "lossy" if ext == "m4a" else ""
        return "lossless" if codec.startswith("alac") else "lossy"
    if ext in LOSSLESS:
        return "lossless"
    if ext in LOSSY:
        return "lossy"
    return ""


def threshold(profile, key: str) -> float:
    """One threshold of a Profile or of a profile's data (plans keep the data)."""
    if isinstance(profile, Profile):
        return profile.threshold(key)
    return Profile(profile).threshold(key)


def _picard_defaults(base: dict) -> dict:
    """The defaults that follow Picard's own settings when a profile does not set them:
    Windows compatibility (file names), Delete empty directories."""
    import tagger_settings
    out = copy.deepcopy(base)
    out.setdefault("filenames", {})["compat"] = \
        "windows" if tagger_settings.picard_bool("windows_compatibility", True) else "linux"
    out["remove_empty_dirs"] = tagger_settings.picard_bool("delete_empty_dirs", True)
    return out


def from_dict(data: dict, *, check=True, sv=None) -> Profile:
    """The Profile of these settings. ProfileError when they do not work - also for a file
    with a section of the wrong kind ("unresolved": [], "version": "two"), which a caller
    catching ProfileError (the watcher, the CLI) skips or reports instead of crashing."""
    try:
        return _from_dict(data, check=check, sv=sv)
    except ProfileError:
        raise
    except (TypeError, ValueError, AttributeError) as exc:
        name = data.get("name") if isinstance(data, dict) else None
        raise ProfileError(f"profile {name!r} is malformed: {type(exc).__name__}: {exc}") from None


def _from_dict(data: dict, *, check=True, sv=None) -> Profile:
    # (the shared settings in place of those it does not state or keep its own of: a whole
    # profile, such as a plan's, states every one and stays exactly as it is)
    data = with_shared(upgrade(data), sv)          # (SV: these shared values instead - {} for none)
    base = _picard_defaults(DEFAULTS) if data.get("follow_tagger_settings",
                                                  DEFAULTS["follow_tagger_settings"]) else DEFAULTS
    full = _merge(base, data)
    if check:
        problems = validate(full)
        if problems:
            raise ProfileError(f"profile {full.get('name')!r}: " + "; ".join(problems))
    return Profile(full)


def names() -> list[str]:
    d = profiles_dir()
    return sorted(p.stem for p in d.glob("*.json")) if d.is_dir() else []


# how many profiles one computer keeps, and the name a new or copied one starts with
MAX_PROFILES = 15
NEW_NAME = "Profile {n}"


def filing_roots(d: dict) -> list[str]:
    """The libraries profile data D files albums into: its destination, each source folder's
    own destination and the lossy library (each once, as written; never the unresolved folder)."""
    out = [d.get("destination") or ""]
    out += [s.get("destination") or "" for s in d.get("sources") or [] if isinstance(s, dict)]
    out.append((d.get("destinations") or {}).get("lossy") or "" if isinstance(d.get("destinations"), dict) else "")
    return [x for x in dict.fromkeys(out) if x and isinstance(x, str)]


def next_name() -> str:
    """The first free "Profile N" (1 to MAX_PROFILES); "" when MAX_PROFILES profiles exist."""
    have = {n.casefold() for n in names()}
    if len(have) >= MAX_PROFILES:
        return ""
    return next(NEW_NAME.format(n=i) for i in range(1, MAX_PROFILES + 2)
                if NEW_NAME.format(n=i).casefold() not in have)


def name_problem(name: str, old: str = "") -> str:
    """Why NAME cannot be a profile's (new) name ("" when it can)."""
    name = name.strip()
    if not name:
        return "a profile needs a name"
    if name in (".", "..") or "/" in name or "\\" in name or name.startswith("."):
        return "a profile's name cannot start with a dot or hold / or \\"
    if name.casefold() == "default" or name.casefold() == "global":
        return f"{name!r} is a name TagMender uses itself"
    if name.casefold() != old.casefold() and name.casefold() in {n.casefold() for n in names()}:
        return f"a profile named {name!r} already exists"
    return ""


def rename(old: str, new: str) -> Path:
    """Rename the profile OLD to NEW: its file, the name inside it, and what refers to it
    (this computer's default profile, plans a watched folder left waiting)."""
    new = new.strip()
    problem = name_problem(new, old)
    if problem:
        raise ProfileError(problem)
    src = path_of(old)
    data = json.loads(src.read_text(encoding="utf-8"))
    data["name"] = new
    dst = path_of(new)
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, dst)
    if dst != src:
        src.unlink()
    _rename_references(old, new)
    return dst


def delete(name: str) -> Path:
    """Remove the profile NAME. A copy is kept in profiles/.deleted/ (put it back by moving it
    to profiles/); the job history keeps the name it ran under."""
    src = path_of(name)
    if not src.exists():
        raise ProfileError(f"no profile named {name!r}")
    keep = profiles_dir() / ".deleted" / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    keep.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, keep)
    _rename_references(name, None)
    return keep


def _rename_references(old: str, new: str | None):
    """What names a profile, moved to NEW (None: the profile is gone)."""
    try:
        import app_settings
        if app_settings.get("default_profile") == old:
            app_settings.set_value("default_profile", new or "last")
        if app_settings.get("last_profile") == old:
            app_settings.set_value("last_profile", new or "")
    except Exception:                       # (an unreadable settings file stays as it is)
        pass
    if new is None:
        return
    try:
        import watcher
        for f in watcher.plans_dir().glob("*.json"):
            plan = json.loads(f.read_text(encoding="utf-8"))
            if plan.get("profile") == old:
                plan["profile"] = new
                tmp = f.with_name(f".{f.name}.tmp")
                tmp.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, f)
    except (OSError, ValueError, ImportError):
        pass


# ---- settings shared by all profiles ----------------------------------------------------------
# Matters of taste and of your system rather than of one job: one value for every profile,
# kept in shared_settings.json ({dotted path: value}; a path not there has its default). A
# profile keeps its own value only for the paths in its "own_settings" (Only This Profile);
# a file without that list counts a shared setting as its own when it states a value that
# differs from the shared one (a profile from before 0.8.6, or written by hand). Resolved
# when a profile file is read (raw), so the engine, watcher and CLI see one ordinary profile.
SHARED_SETTINGS = (
    "compilations.va_name", "compilations.extra_flags", "compilations.grouping", "compilations.single_artist",
    "album_artist",
    "tagger.standardize_artist_names", "tagger.translate_artist_names", "tagger.convert_punctuation",
    "tagger.standardize_instruments", "tagger.release_ars", "tagger.track_ars",
    "genres.musicbrainz", "genres.max", "genres.discogs_styles", "genres.web",
    "matching.preferred_countries", "matching.preferred_release_types", "matching.discouraged_release_types",
    "matching.preferred_formats",
    "tagger.write_id3v23", "tagger.write_id3v1", "tagger.remove_id3_from_flac", "tagger.remove_ape_from_mp3",
    "cleanup.comments",
    "filenames.fat", "tagger.ascii_filenames", "tagger.replace_spaces_with_underscores", "filenames.max_name_bytes",
    "subfolders.covers_name", "subfolders.misc_name",
    "artwork.sources", "artwork.min_px", "artwork.enlarge", "artwork.generate",
    "providers.web.search", "providers.web.gnudb", "providers.web.shops", "providers.web.sources",
    "on_unwritable", "destinations.mounted_only", "allow_unverified_writes", "preserve_timestamps",
    "exclude_paths", "skip_folders", "grouping.join_deluxe_folders", "retry", "mark_resolved",
    "lyrics.sidecar", "lyrics.embed", "lyrics.synced", "loudness",
)
OWN_KEY = "own_settings"
_MISSING = object()


def shared_file() -> Path:
    return ex.CONFIG_DIR / "shared_settings.json"


def _get(d, path):
    for k in path.split("."):
        if not isinstance(d, dict) or k not in d:
            return _MISSING
        d = d[k]
    return d


def _put(d, path, value):
    *head, last = path.split(".")
    for k in head:
        if not isinstance(d.get(k), dict):
            d[k] = {}
        d = d[k]
    d[last] = copy.deepcopy(value)


def _drop(d, path):
    *head, last = path.split(".")
    for k in head:
        d = d.get(k) if isinstance(d, dict) else None
        if not isinstance(d, dict):
            return
    d.pop(last, None)


def is_shared(path: str) -> bool:
    """PATH (dotted) is a shared setting or inside one."""
    return shared_root(path) is not None


def shared_root(path: str):
    """The shared setting PATH is or is inside ("album_artist" for "album_artist.lead"), or None."""
    for p in SHARED_SETTINGS:
        if path == p or path.startswith(p + "."):
            return p
    return None


_shared_cache = [None, None]                    # (file stamp, values)


def shared_values() -> dict:
    """{path: value} of the shared settings set (the others have their defaults)."""
    f = shared_file()
    try:
        st = f.stat()
        stamp = (st.st_mtime_ns, st.st_size, st.st_ino, str(f))
    except OSError:
        return {}
    if _shared_cache[0] != stamp:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        _shared_cache[:] = [stamp, {k: v for k, v in (data.get("values") or {}).items()
                                    if k in SHARED_SETTINGS} if isinstance(data, dict) else {}]
    return copy.deepcopy(_shared_cache[1])


def _base_defaults() -> dict:
    """The defaults as a profile of today has them (no shared values)."""
    return Profile(_merge(DEFAULTS, {"name": "_", "version": SCHEMA_VERSION})).data


def own_of(data: dict, sv=None) -> set:
    """The shared settings a profile (in today's form) keeps its own value of: those its
    own_settings names, and those it states a value of unlike the shared one (or, when that is
    not set, the default)."""
    sv = shared_values() if sv is None else sv
    own = {p for p in data.get(OWN_KEY) or [] if p in SHARED_SETTINGS} \
        if isinstance(data.get(OWN_KEY), list) else set()
    base = None
    for p in SHARED_SETTINGS:
        v = _get(data, p)
        if v is _MISSING or p in own:
            continue
        if p in sv:
            if v != sv[p]:
                own.add(p)
            continue
        if base is None:
            base = _base_defaults()
        if v != _get(base, p):
            own.add(p)
    return own


def with_shared(data: dict, sv=None) -> dict:
    """DATA (a profile in today's form) with the shared values filled in where it states none.
    What it states stays as it is - a whole profile (a plan's) is unchanged - and a profile file
    states only the shared settings it keeps its own of (minimal, _stamp_profiles)."""
    sv = shared_values() if sv is None else sv
    out = copy.deepcopy(data)
    own = set(out.get(OWN_KEY) or []) if isinstance(out.get(OWN_KEY), list) else set()
    for p, v in sv.items():
        if p not in own and _get(out, p) is _MISSING:   # (its own, unset: Automatic, not the shared one)
            _put(out, p, v)
    return out


def _write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    if path == shared_file():
        _shared_cache[:] = [None, None]


def is_marked(data: dict) -> bool:
    """DATA (a profile's file) says which shared settings it keeps its own of (own_settings):
    written since the shared settings exist. One that does not (from before them, or put back
    from elsewhere) works as it always did (raw), and is stamped at the app's next start."""
    return isinstance(data.get(OWN_KEY), list)


def _stamp_profiles(sv, *, marked=False):
    """Each profile file not yet marked (is_marked) given its own shared settings (own_settings)
    and no others, so a shared value changed later reaches every profile that does not keep its
    own. What each profile does stays as it was: a setting whose value with SV (the shared
    values until now) would differ from what it had becomes its own. Nothing else in the file
    changes - what it leaves to the tagger's settings still follows them. The file as it was is
    kept once as NAME.json.bak-shared. MARKED: the marked ones too (the first time: adopt_shared)."""
    for name in names():
        path = path_of(name)
        try:
            data = raw_file(name)
        except ProfileError:
            continue
        if is_marked(data) and not marked:
            continue
        try:
            out = _stamped(data, sv)
            if out == upgrade(copy.deepcopy(data)):
                continue                                # (nothing to change)
        except (ProfileError, TypeError, ValueError, AttributeError):
            continue                                    # (one that cannot be read is left as it is)
        backup = path.with_name(f"{path.name}.bak-shared")
        if not backup.exists():
            shutil.copy2(path, backup)
        _write_json(path, out)


def _stamped(data: dict, sv: dict) -> dict:
    """DATA (a profile file) marked against the shared values SV (_stamp_profiles); what a
    marked one lists stays its own."""
    up = upgrade(copy.deepcopy(data))
    chosen = [p for p in up.get(OWN_KEY) or [] if p in SHARED_SETTINGS] if is_marked(up) else []
    # (as it works now: an unmarked one with no shared value; a marked one with SV - stamped
    # again, as when the first time was stopped half-way, it stays as it is)
    was = from_dict(up, check=False, sv=sv if is_marked(up) else {}).data
    out = copy.deepcopy(up)
    for p in SHARED_SETTINGS:
        _drop(out, p)
    now = from_dict(out, check=False, sv=sv).data
    own = sorted(set(chosen) | {p for p in SHARED_SETTINGS if _get(was, p) != _get(now, p)})
    for p in own:
        v = _get(was, p)
        if v is not _MISSING:                           # (unset - Automatic: kept unset, as its own)
            _put(out, p, v)
    for k in [k for k, v in out.items() if v == {} and isinstance(DEFAULTS.get(k), dict)]:
        out.pop(k)
    out[OWN_KEY] = own
    return out


def _with_changes(sv: dict, changes: dict) -> dict:
    sv = copy.deepcopy(sv)
    base = None
    for p, v in changes.items():
        if v is _MISSING:
            sv.pop(p, None)
            continue
        if isinstance(v, dict):                       # (a group, such as loudness: kept whole)
            base = _base_defaults() if base is None else base
            d = _get(base, p)
            v = _merge(d, v) if isinstance(d, dict) else v
        sv[p] = copy.deepcopy(v)
    return sv


def shared_problems(changes: dict, *, but=None) -> list[str]:
    """What the shared CHANGES ({path: value}) would stop working: each other profile (BUT, the
    one being saved, is checked on its own) that loads now and would not with them."""
    for p in changes:
        if p not in SHARED_SETTINGS:
            raise ProfileError(f"{p!r} is not a setting shared by all profiles")
    sv = _with_changes(shared_values(), changes)
    out = []
    for name in names():
        if name == but:
            continue
        try:
            data = raw(name)
            from_dict(data)
        except ProfileError:
            continue                                    # (not working now: not this change's doing)
        try:
            from_dict(data, sv=sv)
        except ProfileError as exc:
            out.append(str(exc))
    return out


def set_shared(changes: dict):
    """Set shared settings for every profile: {path: value} (_MISSING: back to the default).
    Profiles not yet stating their own are stamped first, as they were."""
    for p in changes:
        if p not in SHARED_SETTINGS:
            raise ProfileError(f"{p!r} is not a setting shared by all profiles")
    sv = shared_values()
    _stamp_profiles(sv)
    _write_json(shared_file(), {"version": 1, "values": _with_changes(sv, changes)})


def adopt_shared() -> bool:
    """Once (no shared_settings.json yet): a setting two or more profiles all hold the same
    value of, unlike the default, becomes that shared value; every profile is stamped with its
    own (what differs keeps differing). Nothing any profile does changes. True when written.
    At each later start, a profile file put back unmarked (is_marked) is stamped."""
    if shared_file().exists():
        try:
            unfinished = json.loads(shared_file().read_text(encoding="utf-8")).get("stamping")
        except (OSError, ValueError, AttributeError):
            unfinished = False
        sv = shared_values()
        _stamp_profiles(sv, marked=bool(unfinished))  # (the first time stopped half-way: finished)
        if unfinished:
            _write_json(shared_file(), {"version": 1, "values": sv})
        return False
    fulls = []
    for name in names():
        try:
            fulls.append(from_dict(raw_file(name), check=False, sv={}).data)
        except ProfileError:
            continue
    sv = {}
    if len(fulls) >= 2:
        base = _base_defaults()
        for p in SHARED_SETTINGS:
            vals = [_get(f, p) for f in fulls]
            first = vals[0]
            if first is not _MISSING and all(v == first for v in vals) and first != _get(base, p):
                sv[p] = first
    # (marked ones too: one saved from the editor before now states its values; one all agree
    # on follows the shared value unless chosen Only This Profile)
    # (the shared values written first, the stamping flagged until done: stopped in between,
    # no profile loses a value - each still states it, or is stamped when read - and the next
    # start finishes it)
    _write_json(shared_file(), {"version": 1, "values": sv, "stamping": True})
    _stamp_profiles(sv, marked=True)
    _write_json(shared_file(), {"version": 1, "values": sv})
    return True


def raw_file(name: str) -> dict:
    """A profile's file as it is (older names read), without the shared settings resolved."""
    path = path_of(name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ProfileError(f"no profile named {name!r} ({path})") from None
    except ValueError as exc:
        raise ProfileError(f"profile {name!r} is not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise ProfileError(f"profile {name!r} is not a JSON object")
    data.setdefault("name", name)
    try:
        return older_behaviour(data)
    except ProfileError:
        raise
    except (TypeError, ValueError, AttributeError) as exc:     # ("version": "two")
        raise ProfileError(f"profile {name!r} is malformed: {type(exc).__name__}: {exc}") from None


def raw(name: str) -> dict:
    """A profile's file (from_dict fills in the shared settings it does not state). One not
    marked (is_marked: from before the shared settings, or put back from elsewhere) comes as it
    would be stamped: it keeps its own of each it would otherwise have taken, so whatever reads
    or saves it - the app, the CLI, the watcher - finds it doing what it always did."""
    data = raw_file(name)
    if not is_marked(data) and shared_file().exists():
        try:
            data = _stamped(data, shared_values())
        except (TypeError, ValueError, AttributeError):
            pass                                        # (malformed: from_dict says what is wrong)
    return data


def load(name: str) -> Profile:
    return from_dict(raw(name))


BUILTIN = {"name": "default", "description": "In-place tagging with the built-in settings."}
PREVIOUS_BUILTIN_NAME = "Built-in (previous)"


def default() -> Profile:
    """The settings used when no profile is chosen: in place, nothing moved."""
    return from_dict(dict(BUILTIN))


def previous_builtin() -> Profile:
    """The built-in settings as TagMender before schema 3 had them (in place, Discogs
    preferred, short names...), as a profile to save under PREVIOUS_BUILTIN_NAME."""
    data = keep_v2_behaviour(dict(BUILTIN, version=2))
    data.update(name=PREVIOUS_BUILTIN_NAME,
                description="The built-in settings of earlier TagMender versions.")
    # (file-name safety and empty folders came from the tagger's settings, as they still do)
    return from_dict(data)


def load_or_builtin(name: str | None, note=None) -> Profile:
    """The profile NAME; the built-in settings for none, or for "default" when no file of that
    name exists. note(text): told once, on a machine that ran an earlier TagMender, that the
    built-in settings changed (builtin_changed_note)."""
    if name and not (name == "default" and not path_of(name).exists()):
        return load(name)
    if note is not None:
        text = builtin_changed_note()
        if text:
            note(text)
    return default()


def save_previous_builtin() -> Path:
    """Save the earlier built-in settings as the profile PREVIOUS_BUILTIN_NAME (never over one)."""
    if path_of(PREVIOUS_BUILTIN_NAME).exists():
        raise ProfileError(f"a profile named {PREVIOUS_BUILTIN_NAME!r} already exists")
    return save(previous_builtin())


def builtin_changed_note() -> str:
    """Once on a machine that ran an earlier TagMender (app_settings.EXISTING_INSTALL), the
    note that the built-in settings changed; "" every other time and on a new install.
    The app offers to save the old ones (previous_builtin); the CLI and watcher print this."""
    import app_settings
    if path_of(PREVIOUS_BUILTIN_NAME).exists() or app_settings.seen_notice("builtin_defaults"):
        return ""
    return ("Note: the built-in settings (used when no profile is chosen) have new defaults: "
            "MusicBrainz preferred, \"Artist/Year - Album/01 - Title\" names, no artist photos, "
            "own covers kept, and folders whose names start with _ are no longer skipped (only "
            "recycle bins and TagMender's own folders are). To keep the old ones: "
            "tagmender profiles save-previous-builtin")


def minimal(profile: Profile, *, sv=None, mark=False) -> dict:
    """Only what differs from the defaults (what is written to disk)."""
    def diff(full, base, keep_none=False):
        out = {}
        for k, v in full.items():
            if k in ("name", "version"):
                continue
            if isinstance(v, dict) and isinstance(base.get(k), dict) and k != "abbreviations":
                sub = diff(v, base[k], keep_none=k == "tagger")
                if sub:
                    out[k] = sub
            elif base.get(k) != v or (keep_none and k not in base):
                out[k] = v          # (picard: a setting null says "as each file has it", not "as Picard")
        return out
    data = {k: v for k, v in profile.data.items() if k != OWN_KEY}
    out = {"version": SCHEMA_VERSION, "name": profile.name, **diff(data, DEFAULTS)}
    # (a profile saved keeps its file names and empty-folder setting whatever Picard says later;
    # only one that never says follows Picard's)
    out.setdefault("filenames", {})["compat"] = profile.data["filenames"]["compat"]
    out["remove_empty_dirs"] = profile.data["remove_empty_dirs"]
    # the shared settings: only those it keeps its own value of (Only This Profile, or a value
    # unlike the shared one, which it keeps rather than lose)
    own = own_of(profile.data, shared_values() if sv is None else sv)
    for p in SHARED_SETTINGS:
        _drop(out, p)
    for p in sorted(own):
        v = _get(data, p)
        if v is not _MISSING:
            _put(out, p, v)
    for k in [k for k, v in out.items() if v == {} and isinstance(DEFAULTS.get(k), dict)]:
        out.pop(k)
    # (before the shared settings exist, only a profile saying which it keeps - the editor's - is
    # marked: its Only This Profile is kept; others are stamped as they worked when they come)
    if mark or shared_file().exists():
        out[OWN_KEY] = sorted(own)
    elif is_marked(profile.data):                    # (only what was chosen: a value all profiles
        out[OWN_KEY] = sorted(p for p in profile.data[OWN_KEY]   #  agree on is shared when they are)
                              if p in SHARED_SETTINGS)
    return out


def check_template(d: dict) -> list[str]:
    """A naming script that does not parse (checked when a profile is saved, and by `profiles
    check`; a profile holding one still loads - a plan names its albums' conflicts)."""
    import naming as _naming
    tpl = ((d.get("naming") or {}).get("template") or "") if isinstance(d.get("naming"), dict) else ""
    if not isinstance(tpl, str) or not tpl.strip():
        return []
    return [f"naming.template: {x}" for x in _naming.check(tpl)]


def save(profile: Profile, *, full=False):
    problems = validate(profile.data) + check_template(profile.data)
    if problems:
        raise ProfileError("; ".join(problems))
    return _write(profile, full=full)


def _write(profile: Profile, *, full=False):
    """Write PROFILE's file as it is (save() checks it first)."""
    path = path_of(profile.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = profile.to_dict() if full else minimal(profile)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def migrate(name: str) -> tuple[bool, Path | None]:
    """Write an older profile file in today's form, keeping the old one as NAME.json.bak-<time>.
    Returns (changed, backup path)."""
    orig = raw_file(name)                 # (the file as written: raw() gives it in today's form)
    if int(orig.get("version") or 0) == SCHEMA_VERSION and not has_legacy(orig):
        return False, None
    prof = from_dict(raw(name))              # (checked first: a profile that cannot be used is left alone)
    problems = check_template(prof.data)
    if problems:
        raise ProfileError(f"profile {name!r}: " + "; ".join(problems))
    path = path_of(name)
    backup = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, backup)
    save(prof)
    return True, backup


def install_examples(overwrite=False) -> list[str]:
    """Write the shipped examples. They leave their library folder for you to choose, so they
    are written without the checks save() makes; `profiles check` names what is missing and
    nothing runs with them until it is set."""
    done = []
    for name, data in EXAMPLES.items():
        if path_of(name).exists() and not overwrite:
            continue
        _write(from_dict(data, check=False))
        done.append(name)
    return done


def for_path(path) -> Profile | None:
    """The profile whose source folder holds PATH; the deepest source wins when roots
    overlap (a profile for /music/ambient beats one for /music)."""
    p = os.path.realpath(os.path.expanduser(str(path)))
    best, depth = None, -1
    for n in names():
        try:
            prof = load(n)
        except ProfileError:
            continue
        for s in prof["sources"]:
            root = os.path.realpath(os.path.expanduser(s["path"]))
            if (p == root or p.startswith(root.rstrip(os.sep) + os.sep)) and len(root) > depth:
                if s.get("recursive", True) or os.path.dirname(p) == root or p == root:
                    best, depth = prof, len(root)
    return best
