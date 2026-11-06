"""app_settings.py - settings for this computer, shared by the app, the CLI and the watcher.

A profile says how one kind of library is handled; these say how TagMender behaves on this
machine, whatever profile runs: undo backups, where duplicate copies go, the network, logs,
appearance, notifications. They live in one JSON file, ~/.config/tagmender/settings.json
(gui.json keeps only what the window itself remembers: sizes, the last tab).

Two sets of defaults:
  DEFAULTS            what a new install starts with (suits most people)
  EXISTING_INSTALL    what an install that already ran an earlier TagMender starts with:
                      the behaviour it had, so an upgrade changes nothing by itself
The file is written the first time the settings are read, so that choice is made once.

Some settings name code to run (the plugin folder, another tagger's command). They are
read from this file only, never from a profile: a shared profile must never carry code.

  load() -> dict            every setting, defaults filled in
  get("backups.keep_days")  one setting by its dotted name
  validate(dict) -> [str]   problems in plain words ([] = usable)
  save(dict)                atomic write (after validate)
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
from pathlib import Path

import exclusions as ex

VERSION = 1
FILE_NAME = "settings.json"

# NAS and desktop recycle bins and indexes: never music to tag
RECYCLE_BINS = [".Trash*", ".recycle", "#recycle", "@Recycle", "@eaDir", "$RECYCLE.BIN", "lost+found"]

DEFAULTS = {
    "version": VERSION,
    # Files and Backups
    "backups": {
        "keep_days": 30,              # 7 | 30 | 90 | 0 (forever); Undo works for this long
        "auto_clean": True,           # delete backups older than keep_days after a run
        "location": "",               # "" = the state folder (~/.local/state/tagmender)
    },
    "duplicates": {
        "dispose": "folder",          # folder | trash; nothing is ever deleted outright
        "folder_name": "_Duplicates",
        # names the duplicates folder had before (still skipped: copies already moved there)
        "previous_names": [],
        "keep": "best",               # best (quality; newest when equal) | newest | oldest
    },
    "permissions": {"new_folders": "parent"},     # parent | system (umask)
    # tools that walk a whole library without a profile (dedupe, audits) skip these folders
    "skip_folders": list(RECYCLE_BINS),
    # General
    "library_folder": "",             # "" = the system Music folder
    "default_profile": "last",        # last | builtin | a profile's name
    "last_profile": "",               # the profile last chosen in the app's toolbar (default_profile last)
    # a folder added (dropped, Add Folder): look its albums up at once, or list them first and
    # look up the ones you choose (Look Up)
    "add_folder": "list",             # list | look_up
    "appearance": {"accent": "teal", "text_size": "default"},
    "player": {"skip_seconds": 10},
    # Notifications (only while the window is in the background)
    "notifications": {"finished": True, "watch": True},
    # Watched Folders
    "media_server": {"rescan": "none"},           # none | navidrome | lms | jellyfin | plex
    # Network and Privacy
    "network": {
        "offline": False,
        # a saved lookup older than this is asked again when its album is processed again
        # (never in a background sweep); 0 = never
        "refresh_days": 30,
        "store_country": "auto",                  # auto (your region, else US) | a country code
        "wikipedia_language": "auto",             # auto (the app's language, then English) | a code
    },
    "ocr": {"languages": [], "effort": "standard"},   # [] = English plus your language; quick|standard|thorough
    # Advanced
    "logs": {"detailed": False, "max_mb": 10},
    "diagnostics": {"hide_names": True},
    # enabled: the installed plugins switched on (plugins.py); builtin_off: the plugins that come
    # with TagMender (Discogs, CoverUp) switched off; settings: {plugin id: {key: value}}
    "plugins": {"enabled": [], "builtin_off": [], "settings": {}},
    "tagger_command": "",             # "" = found on PATH, then the Flatpak
    # one-time notices already shown (a new install has nothing to be told about)
    "notices": {"builtin_defaults": True},
    # what this machine started as when the file was first written: "new", or "earlier" (an
    # install that ran an earlier TagMender); settings the file leaves out are read as that
    "started_as": "new",
}

# An install that ran an earlier TagMender keeps how it behaved (see existing_install()).
EXISTING_INSTALL = {
    "backups": {"auto_clean": False},
    "duplicates": {"folder_name": "2delete", "keep": "newest"},
    "skip_folders": ["_*", "2delete"],
    "network": {"refresh_days": 0},
    "ocr": {"languages": ["eng", "rus", "jpn"]},
    "logs": {"max_mb": 100},
    "diagnostics": {"hide_names": False},
    "player": {"skip_seconds": 5},
    "notices": {"builtin_defaults": False},     # the built-in settings changed: say so once
    "started_as": "earlier",
}

CHOICES = {
    "duplicates.dispose": ("folder", "trash"),
    "duplicates.keep": ("best", "newest", "oldest"),
    "permissions.new_folders": ("parent", "system"),
    "appearance.accent": ("teal", "blue", "graphite", "system"),
    "appearance.text_size": ("small", "default", "large", "larger"),
    "media_server.rescan": ("none", "navidrome", "lms", "jellyfin", "plex"),
    "add_folder": ("look_up", "list"),
    "ocr.effort": ("quick", "standard", "thorough"),
}
KEEP_DAYS = (0, 7, 30, 90)
# a settings file that could not be read: nothing is cleaned up or moved by guesswork
CAUTIOUS = {"backups": {"auto_clean": False, "keep_days": 0}}
REFRESH_DAYS = (0, 7, 30, 90)
LOG_MB = (10, 50, 100)
SKIP_SECONDS = (5, 10, 15)
NETWORK_FS = ("nfs", "nfs4", "cifs", "smb3", "smbfs", "fuse", "fuse.sshfs", "fuse.rclone", "9p", "afs")


def path() -> Path:
    return ex.CONFIG_DIR / FILE_NAME


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def existing_install() -> bool:
    """An earlier TagMender ran here: its state database, a profile or the window's settings."""
    return ((ex.STATE_DIR / "state.db").exists() or (ex.CONFIG_DIR / "gui.json").exists()
            or any((ex.CONFIG_DIR / "profiles").glob("*.json")))


def keep_days_choice(days) -> int:
    """The Keep Undo choice for the retentions profiles had: never shorter than the longest,
    so an upgrade never shortens Undo (0 = for ever, also for anything above 90 days)."""
    if 0 in days:
        return 0
    longest = max(days)
    return next((k for k in KEEP_DAYS if k and k >= longest), 0)


def first_settings() -> dict:
    """The settings a machine starts with: DEFAULTS, or an existing install's behaviour, with
    its undo backups kept as long as its longest-keeping profile kept them."""
    if not existing_install():
        return copy.deepcopy(DEFAULTS)
    out = _merge(DEFAULTS, EXISTING_INSTALL)
    days = []
    for f in sorted((ex.CONFIG_DIR / "profiles").glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8")).get("backup_retention_days", 30)
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(d, int) and not isinstance(d, bool):
            days.append(d)
    if days:
        out["backups"]["keep_days"] = keep_days_choice(days)
    return out


def _read():
    """(data, problem): the settings file as written. data is None when there is no file;
    problem says why a file that is there could not be read ("" when it read cleanly)."""
    f = path()
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, ""
    except (OSError, ValueError) as exc:
        return {}, f"{f} could not be read ({type(exc).__name__}: {exc})"
    if not isinstance(data, dict):
        return {}, f"{f} does not hold a JSON object"
    return data, ""


def read_problem() -> str:
    """Why the settings file that is there could not be read; "" when it reads cleanly or
    is not there yet."""
    return _read()[1]


def _missing(base, data) -> bool:
    """data leaves out a setting base has (a hand-written file)."""
    return any(k not in data or isinstance(v, dict) and isinstance(data[k], dict) and _missing(v, data[k])
               for k, v in base.items())


_warned = set()


def load() -> dict:
    """Every setting, defaults filled in. The first read writes the file (DEFAULTS or the
    existing install's), so that choice is never made again. Settings a hand-written file
    leaves out are this machine's first settings, not a new install's. A file that cannot be
    read is left as it is and reads as the first settings with nothing cleaned up (CAUTIOUS);
    the app refuses to save over it."""
    data, problem = _read()
    if data is None:
        data = first_settings()
        try:
            save(data)
        except OSError:
            pass
        return _merge(DEFAULTS, data)
    if problem:
        if problem not in _warned:
            _warned.add(problem)
            logging.getLogger("tagmender").warning("settings: %s; using cautious settings until it is fixed",
                                                   problem)
        return _merge(first_settings(), CAUTIOUS)
    if _missing(DEFAULTS, data):
        # (what the machine started as, when the file says so; else, a hand-written file,
        # what it would start as now)
        base = copy.deepcopy(DEFAULTS) if data.get("started_as") == "new" else first_settings()
        return _merge(base, data)
    return _merge(DEFAULTS, data)


def stored(name: str, default=None):
    """One setting as the file says it, with nothing filled in (default when the file leaves
    it out or cannot be read)."""
    data, problem = _read()
    v = _value(data or {}, name) if not problem else None
    return default if v is None else v


def get(name: str, default=None):
    """One setting by its dotted name ("backups.keep_days")."""
    v = load()
    for part in name.split("."):
        if not isinstance(v, dict) or part not in v:
            return default
        v = v[part]
    return v


def _value(d, name):
    v = d
    for part in name.split("."):
        v = v.get(part) if isinstance(v, dict) else None
    return v


def _mount_of(p: str) -> tuple[str, str]:
    """(mount point, filesystem type) holding the folder p; ("", "") when unknown."""
    best = ("", "")
    try:
        lines = Path("/proc/mounts").read_text().splitlines()
    except OSError:
        return best
    for ln in lines:
        parts = ln.split()
        if len(parts) < 3:
            continue
        mnt = parts[1].replace("\\040", " ")
        if (p == mnt or p.startswith(mnt.rstrip("/") + "/")) and len(mnt) >= len(best[0]):
            best = (mnt, parts[2])
    return best


def library_folders() -> list[str]:
    """Every profile's source and destination folders (real paths)."""
    import profiles
    out = []
    for n in profiles.names():
        try:
            d = profiles.raw(n)
        except profiles.ProfileError:
            continue
        for s in d.get("sources") or []:
            if isinstance(s, dict) and s.get("path"):
                out.append(s["path"])
        out += profiles.filing_roots(d)
    return [os.path.realpath(os.path.expanduser(str(x))) for x in out]


def backup_location_problems(location: str, libraries=None) -> list[str]:
    """A backup folder must be a local folder that is there now, outside every library:
    a backup on the library's own share is lost with it."""
    if not location:
        return []
    p = os.path.realpath(os.path.expanduser(location))
    if not os.path.isabs(os.path.expanduser(location)):
        return ["backups.location must be an absolute path"]
    out = []
    for lib in libraries if libraries is not None else library_folders():
        if p == lib or p.startswith(lib.rstrip("/") + "/") or lib.startswith(p.rstrip("/") + "/"):
            out.append(f"backups.location must not be inside or around a library folder ({lib})")
            break
    probe = p
    while not os.path.isdir(probe) and os.path.dirname(probe) != probe:
        probe = os.path.dirname(probe)
    mnt, fs = _mount_of(probe)
    if fs and (fs in NETWORK_FS or fs.startswith("fuse.")):
        out.append(f"backups.location is on a network share ({fs}); choose a folder on this computer")
    for parent in ("/media/", "/mnt/", "/run/media/", "/srv/", "/net/"):
        if p.startswith(parent) and mnt in ("", "/"):
            out.append(f"backups.location: nothing is mounted there now ({location})")
            break
    return out


def _plain_name(name) -> bool:
    return bool(name.strip()) and name == name.strip() and not any(c in name for c in "/\\\x00") \
        and name not in (".", "..")


def validate(data: dict, libraries=None) -> list[str]:
    """Problems that would stop these settings from working, in plain words ([] = usable)."""
    d = _merge(DEFAULTS, data or {})
    p = []
    for name, allowed in CHOICES.items():
        if _value(d, name) not in allowed:
            p.append(f"{name} must be one of {', '.join(allowed)}")
    for name, allowed in (("backups.keep_days", KEEP_DAYS), ("network.refresh_days", REFRESH_DAYS),
                          ("logs.max_mb", LOG_MB), ("player.skip_seconds", SKIP_SECONDS)):
        v = _value(d, name)
        if isinstance(v, bool) or v not in allowed:
            p.append(f"{name} must be one of {', '.join(map(str, allowed))}")
    for name in ("backups.auto_clean", "network.offline", "logs.detailed", "diagnostics.hide_names",
                 "notifications.finished", "notifications.watch"):
        if not isinstance(_value(d, name), bool):
            p.append(f"{name} must be true or false")
    name = str(_value(d, "duplicates.folder_name") or "")
    if not _plain_name(name):
        p.append("duplicates.folder_name must be a plain folder name, such as 2delete (no slashes)")
    was = _value(d, "duplicates.previous_names")
    if not isinstance(was, list) or not all(isinstance(x, str) and _plain_name(x) for x in was):
        p.append("duplicates.previous_names must be a list of folder names")
    skip = d.get("skip_folders")
    if not isinstance(skip, list) or not all(isinstance(x, str) and x.strip() for x in skip):
        p.append("skip_folders must be a list of folder names")
    elif any(x.strip() in ("*", "**") for x in skip):
        p.append("skip_folders must not hold a pattern matching every folder (*)")
    langs = _value(d, "ocr.languages")
    if not isinstance(langs, list) or not all(isinstance(x, str) and re.fullmatch(r"[A-Za-z]{3}(_[A-Za-z]+)?", x)
                                              for x in langs):
        p.append("ocr.languages must be a list of language codes (eng, deu, ...), or [] for automatic")
    v = _value(d, "library_folder")
    if not isinstance(v, str) or (v and not os.path.isabs(os.path.expanduser(v))):
        p.append("library_folder must be an absolute path, or empty")
    for name in ("plugins.enabled", "plugins.builtin_off"):
        on = _value(d, name)
        if not isinstance(on, list) or not all(isinstance(x, str) for x in on):
            p.append(f"{name} must be a list of plugin ids")
    if not isinstance(_value(d, "plugins.settings"), dict):
        p.append("plugins.settings must be an object: {plugin id: {setting: value}}")
    if not isinstance(d.get("tagger_command"), str):
        p.append("tagger_command must be text")
    if not isinstance(d.get("default_profile"), str) or not d["default_profile"].strip():
        p.append("default_profile must be last, builtin or a profile's name")
    loc = _value(d, "backups.location")
    if not isinstance(loc, str):
        p.append("backups.location must be a folder, or empty")
    else:
        p += backup_location_problems(loc, libraries)
    return p


class Unreadable(ValueError):
    """The settings file is there but could not be read: it is not saved over."""


def save(data: dict):
    """Write every setting, not only those that differ from DEFAULTS, so that a later change
    of DEFAULTS never changes a choice already made. A file that is there but cannot be read
    is never replaced (Unreadable): fix it, or remove it to start again."""
    problem = read_problem()
    if problem:
        raise Unreadable(f"{problem}. Fix it, or remove it to start again; it was not replaced")
    data = _merge(DEFAULTS, data)
    data["version"] = VERSION
    f = path()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f".{f.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, f)
    return f


def set_value(name: str, value):
    """Change one setting by its dotted name and save (after validate). Only problems the
    change brings are refused: a stored value that stopped working (a plugin folder since
    removed) never blocks changing another setting. A new duplicates folder name keeps the
    old one in duplicates.previous_names, so copies already moved there stay skipped."""
    data = load()
    before = set(validate(data))
    old = _value(data, name)
    d = data
    parts = name.split(".")
    for part in parts[:-1]:
        d = d.setdefault(part, {})
    d[parts[-1]] = value
    if name == "duplicates.folder_name" and isinstance(old, str) and old and old != value:
        was = data["duplicates"].setdefault("previous_names", [])
        if isinstance(was, list) and old not in was:
            was.append(old)
    problems = [x for x in validate(data) if x not in before or x.startswith(name)]
    if problems:
        raise ValueError("; ".join(problems))
    save(data)


# ---- what other modules ask -------------------------------------------------
# the system language (LANG's two letters) -> its Tesseract language pack
TESSERACT_LANGS = {"de": "deu", "fr": "fra", "es": "spa", "it": "ita", "pt": "por", "nl": "nld", "pl": "pol",
                   "ru": "rus", "uk": "ukr", "cs": "ces", "sk": "slk", "hu": "hun", "ro": "ron", "sv": "swe",
                   "da": "dan", "nb": "nor", "nn": "nor", "no": "nor", "fi": "fin", "el": "ell", "tr": "tur",
                   "ja": "jpn", "ko": "kor", "zh": "chi_sim", "bg": "bul", "hr": "hrv", "sr": "srp",
                   "sl": "slv", "et": "est", "lv": "lav", "lt": "lit", "he": "heb", "ar": "ara"}
# how many scans of an album are read (ocr.effort)
OCR_IMAGES = {"quick": 8, "standard": 15, "thorough": 40}


def ocr_languages(installed, data=None) -> str:
    """The Tesseract languages scans are read in ("eng+deu"): ocr.languages, or for []
    English plus the system language; only packs installed (installed: their codes)."""
    data = load() if data is None else data
    want = list(_value(data, "ocr.languages") or [])
    if not want:
        want = ["eng"]
        for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
            code = (os.environ.get(var) or "")[:2].lower()
            if code.isalpha() and len(code) == 2:
                if code in TESSERACT_LANGS:
                    want.append(TESSERACT_LANGS[code])
                break
    have = set(installed or ())
    return "+".join(x for x in dict.fromkeys(want) if x in have) or "eng"


def ocr_images(data=None) -> int:
    """How many scans of an album OCR reads (ocr.effort)."""
    data = load() if data is None else data
    return OCR_IMAGES.get(_value(data, "ocr.effort"), OCR_IMAGES["standard"])

def duplicates_folder() -> str:
    """The folder name duplicate copies are moved into (below the library's root)."""
    return str(get("duplicates.folder_name") or DEFAULTS["duplicates"]["folder_name"])


def duplicates_folders(data=None) -> set[str]:
    """Every name the duplicates folder has had (the current one and previous_names): all
    are skipped by the walks."""
    data = load() if data is None else data
    dup = data.get("duplicates") or {}
    names = {str(dup.get("folder_name") or DEFAULTS["duplicates"]["folder_name"])}
    # (and the built-in names, new and earlier: copies moved there before stay skipped)
    names.update({DEFAULTS["duplicates"]["folder_name"], EXISTING_INSTALL["duplicates"]["folder_name"]})
    names.update(x for x in dup.get("previous_names") or [] if isinstance(x, str) and x)
    return names


def tool_skipper():
    """skips(name): a folder a whole-library tool never looks into - hidden ones, the
    duplicates folder, TagMender's own unidentified folder and the skip_folders patterns.
    The settings are read once, here."""
    import fnmatch
    data = load()
    own = duplicates_folders(data) | {"_unresolved"}
    pats = [p for p in data.get("skip_folders") or [] if isinstance(p, str)]

    def skips(name: str) -> bool:
        return name.startswith(".") or name in own or any(fnmatch.fnmatchcase(name, p) for p in pats)
    return skips


def seen_notice(key: str) -> bool:
    """A one-time notice was already shown; marks it shown (returns False the first time)."""
    if get(f"notices.{key}") or read_problem():
        return True
    try:
        data = load()
        data.setdefault("notices", {})[key] = True
        save(data)
    except (OSError, ValueError):
        pass
    return False
