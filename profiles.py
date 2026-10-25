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

SCHEMA_VERSION = 1

OPERATIONS = ("in_place", "copy", "move")
POLICIES = ("preserve", "fill_missing", "replace", "remove", "lock")
PROVIDERS = ("musicbrainz", "discogs", "acoustid", "web")
PRIMARY = ("discogs", "musicbrainz", "both")
TRADITIONS = ("popular", "western_classical", "indian_classical", "folk")
COMPAT = ("linux", "windows")
UNRESOLVED_ACTIONS = ("leave", "mark", "move")
LOSSY = ("mp3", "opus", "ogg", "oga", "m4a", "aac", "wma", "mpc")
LOSSLESS = ("flac", "wav", "aiff", "aif", "wv", "ape", "alac", "dsf", "dff")

DEFAULTS = {
    "version": SCHEMA_VERSION,
    "name": "",
    "description": "",
    # what the profile covers
    "sources": [],                        # [{"path": "...", "recursive": true, "watch": false}]
    "exclude_paths": ["*/2delete/*", "*/_unresolved/*", "*/.Trash*/*"],   # globs on the full path
    "formats": [],                        # only these formats ([] = every format)
    "tradition": "popular",
    # identification
    "providers": {
        "musicbrainz": {"enabled": True, "identify": True},
        "discogs": {"enabled": True, "identify": True},
        "acoustid": {"enabled": False},
        "web": {"enabled": True},
    },
    "primary": "discogs",                 # who decides the pressing when both have it
    # How Discogs data joins MusicBrainz's: exactly as your Picard's Discogs Enrichment plugin
    # does it, with its settings read from Picard.ini (merge mode, styles, marketplace numbers,
    # performers, protected tags, ...) and Picard's "Preserve these tags". Entries here
    # override single plugin settings for this profile, e.g. {"merge_mode": "fill_gaps"}.
    "follow_picard": True,
    "discogs_enrichment": {},
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
    "artwork": {"enabled": True, "provider": "coverup", "embed_front": True, "files": True,
                "replace": "larger_only", "min_px": 500, "max_bytes": 20_000_000},
    "lyrics": {"enabled": False, "synced": True, "embed": False, "sidecar": True},
    "mark_resolved": True,                # TAGMENDER=Resolved on what was identified
    # where it goes
    "operation": "in_place",              # in_place | copy | move
    "destination": "",                    # library root for copy/move
    # editions: "Separate editions in folder names": off | edition ([Vinyl], [WEB], [Tape])
    #   | edition_year ([2019 Vinyl], [2014 CD]); album_editions: the same, added to the Album tag
    "naming": {"preset": "tagmender", "template": "", "editions": "off", "album_editions": "off"},
    "companions": [".cue", ".log", ".lrc", ".jpg", ".jpeg", ".png", ".pdf", ".txt", ".m3u", ".m3u8"],
    "remove_empty_dirs": True,
    "filenames": {"compat": "windows", "max_name_bytes": 255, "max_dir_bytes": 255, "max_path_chars": 4096,
                  "max_album_words": 5, "abbreviations": {}},
    "backup_retention_days": 30,
    "allow_unverified_writes": False,
    "preserve_timestamps": None,          # keep files' modification times (None: as Picard is set)     # write tags to formats whose audio data cannot be checked
    # albums nothing could identify
    "unresolved": {"action": "mark", "move_to": "", "allow_partial": False},
    "retry": {"after_days": 30, "max_attempts": 6},
    "unattended": False,                  # apply plans without asking
    "watch": {"settle_minutes": 10, "interval_seconds": 300, "workers": 1},
    "exclusions": [],
}

EXAMPLES = {
    "Lossy": {
        "name": "Lossy",
        "description": "Copies identified lossy files (MP3, Opus, Ogg, AAC/M4A) into the lossy library. "
                       "Lossless files are left out, never transcoded; the originals stay where they are.",
        "formats": list(LOSSY),
        "operation": "copy",
        "destination": "/media/media1/music/lossy",
        "unresolved": {"action": "mark", "move_to": "", "allow_partial": False},
    },
}


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


def upgrade(data: dict) -> dict:
    """An older profile file in today's form. Version 0 (no "version") held only a name and
    Ignore/Skip rules; nothing it said changes meaning."""
    data = dict(data or {})
    v = int(data.get("version") or 0)
    if v > SCHEMA_VERSION:
        raise ProfileError(f"profile {data.get('name')!r} was written by a newer TagMender (version {v})")
    data["version"] = SCHEMA_VERSION
    return data


def validate(data: dict) -> list[str]:
    """Problems that would stop the profile from working, in plain words ([] = usable)."""
    p = []
    d = _merge(DEFAULTS, data)
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
    if d["primary"] not in PRIMARY:
        p.append(f"primary must be one of {', '.join(PRIMARY)}")
    if d["tradition"] not in TRADITIONS:
        p.append(f"tradition must be one of {', '.join(TRADITIONS)}")
    for name in d["providers"]:
        if name not in PROVIDERS:
            p.append(f"unknown provider '{name}' (use {', '.join(PROVIDERS)})")
    ident = [n for n in ("musicbrainz", "discogs") if d["providers"].get(n, {}).get("enabled")
             and d["providers"][n].get("identify", True)]
    if not ident and not d["providers"].get("web", {}).get("enabled"):
        p.append("no provider is enabled to identify music")
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
        if not isinstance(v, (int, float)) or not 0 <= float(v) <= 100:
            p.append(f"thresholds.{k} must be a number from 0 to 100")
    if th.get("organize_min_confidence", 100) < th.get("auto_min_confidence", 0):
        p.append("thresholds.organize_min_confidence must not be below auto_min_confidence "
                 "(moving files needs at least as much proof as identifying them)")
    fmts = [str(f).lower().lstrip(".") for f in d["formats"]]
    known = set(LOSSY) | set(LOSSLESS)
    for f in fmts:
        if f not in known:
            p.append(f"unknown format '{f}'")
    fn = d["filenames"]
    if fn.get("compat") not in COMPAT:
        p.append(f"filenames.compat must be one of {', '.join(COMPAT)}")
    for k in ("max_name_bytes", "max_dir_bytes"):
        if not isinstance(fn.get(k), int) or not 16 <= fn[k] <= 255:
            p.append(f"filenames.{k} must be a whole number from 16 to 255")
    if not isinstance(fn.get("max_path_chars"), int) or fn["max_path_chars"] < 64:
        p.append("filenames.max_path_chars must be a whole number of at least 64")
    if d["unresolved"].get("action") not in UNRESOLVED_ACTIONS:
        p.append(f"unresolved.action must be one of {', '.join(UNRESOLVED_ACTIONS)}")
    if d["unresolved"].get("action") == "move" and not d["unresolved"].get("move_to"):
        p.append("unresolved.action 'move' needs unresolved.move_to")
    for i, s in enumerate(d["sources"]):
        if not isinstance(s, dict) or not s.get("path"):
            p.append(f"sources[{i}] needs a path")
        elif not os.path.isabs(os.path.expanduser(s["path"])):
            p.append(f"sources[{i}].path must be absolute")
    if d["preserve_timestamps"] not in (None, True, False):
        p.append("preserve_timestamps must be true, false or null (as Picard is set)")
    import levels
    import naming as _naming
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
        import picard_config
        p += picard_config.validate(d["discogs_enrichment"])
    for r in d["exclusions"]:
        try:
            ex.Rule.from_dict(r, d["name"] or "profile")
        except ex.RuleError as exc:
            p.append(str(exc))
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

    # ---- helpers the engine asks --------------------------------------------
    def wants_format(self, path) -> bool:
        fmts = {f.lower().lstrip(".") for f in self.data["formats"]}
        return not fmts or Path(path).suffix.lower().lstrip(".") in fmts

    def excluded_path(self, path) -> bool:
        import fnmatch
        s = str(path)
        return any(fnmatch.fnmatchcase(s, g) for g in self.data["exclude_paths"])

    def policy(self, fld: str) -> str:
        """The profile's own policy for the field; else, for a tag Picard is set to preserve
        (Options > Tags > "Preserve these tags"), fill_missing - kept as it is on the file,
        written only where the file has none, as Picard does; else the default."""
        pol = self.data["field_policy"]
        fields = pol.get("fields") or {}
        low = fld.lower()
        if low in fields:
            return fields[low]
        if low in self.picard_preserved():
            return "fill_missing"
        return pol.get("default", "replace")

    def picard_preserved(self) -> set:
        """Picard's preserved tags, less TagMender's own markers (Picard keeps those so that
        it does not wipe them; TagMender writes them)."""
        if not self.data.get("follow_picard", True):
            return set()
        import picard_config
        return {t for t in picard_config.preserved_tags() if not t.startswith("tagmender")}

    def preserve_timestamps(self) -> bool:
        v = self.data.get("preserve_timestamps")
        if v is None:
            if not self.data.get("follow_picard", True):
                return False
            import picard_config
            return picard_config.picard_bool("preserve_timestamps")
        return bool(v)

    def enrichment(self) -> dict:
        """Discogs Enrichment settings for this profile (see picard_config)."""
        import picard_config
        over = self.data.get("discogs_enrichment") or {}
        if not self.data.get("follow_picard", True):
            return picard_config.enrichment(over, path=Path("/nonexistent/Picard.ini"))
        return picard_config.enrichment(over)

    def matcher_cfg(self, base: dict) -> dict:
        """match_core's settings with this profile's identification thresholds."""
        cfg = dict(base)
        for k in ("auto_min_confidence", "auto_min_margin"):
            cfg[k] = float(self.data["thresholds"][k])
        return cfg

    def provider_on(self, name: str, role: str = "identify") -> bool:
        p = self.data["providers"].get(name) or {}
        return bool(p.get("enabled")) and bool(p.get(role, True))

    def to_dict(self) -> dict:
        return copy.deepcopy(self.data)


def from_dict(data: dict, *, check=True) -> Profile:
    data = upgrade(data)
    full = _merge(DEFAULTS, data)
    if check:
        problems = validate(full)
        if problems:
            raise ProfileError(f"profile {full.get('name')!r}: " + "; ".join(problems))
    return Profile(full)


def names() -> list[str]:
    d = profiles_dir()
    return sorted(p.stem for p in d.glob("*.json")) if d.is_dir() else []


def raw(name: str) -> dict:
    path = path_of(name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ProfileError(f"no profile named {name!r} ({path})") from None
    except ValueError as exc:
        raise ProfileError(f"profile {name!r} is not valid JSON: {exc}") from None
    data.setdefault("name", name)
    return data


def load(name: str) -> Profile:
    return from_dict(raw(name))


def default() -> Profile:
    """The settings used when no profile is chosen: in place, nothing moved."""
    return from_dict({"name": "default", "description": "In-place tagging with the built-in settings."})


def minimal(profile: Profile) -> dict:
    """Only what differs from the defaults (what is written to disk)."""
    def diff(full, base):
        out = {}
        for k, v in full.items():
            if k in ("name", "version"):
                continue
            if isinstance(v, dict) and isinstance(base.get(k), dict) and k != "abbreviations":
                sub = diff(v, base[k])
                if sub:
                    out[k] = sub
            elif base.get(k) != v:
                out[k] = v
        return out
    return {"version": SCHEMA_VERSION, "name": profile.name, **diff(profile.data, DEFAULTS)}


def save(profile: Profile, *, full=False):
    problems = validate(profile.data)
    if problems:
        raise ProfileError("; ".join(problems))
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
    data = raw(name)
    if int(data.get("version") or 0) == SCHEMA_VERSION:
        return False, None
    path = path_of(name)
    backup = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, backup)
    prof = from_dict(data)
    save(prof)
    return True, backup


def install_examples(overwrite=False) -> list[str]:
    done = []
    for name, data in EXAMPLES.items():
        if path_of(name).exists() and not overwrite:
            continue
        save(from_dict(data))
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
