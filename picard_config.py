"""picard_config.py - your Picard and Discogs Enrichment settings, so TagMender writes what
your Picard would.

TagMender applies Discogs data with the Discogs Enrichment plugin's own code. The plugin's
behaviour depends on its settings (Options > Plugins > Discogs Enrichment), which Picard keeps
in Picard.ini under [plugin.dd789f69-...]. This module reads them, together with the few
Picard settings that decide what ends up in a file:

  enrichment(profile_overrides) -> dict    every plugin setting: Picard.ini first, then the
                                           plugin's own defaults; a profile's
                                           "discogs_enrichment" entries win over both
  preserved_tags()               -> set    Options > Tags > "Preserve these tags" (lower case)
  tag_settings(profile_overrides) -> dict  Picard's own settings that decide the tags written
                                           from MusicBrainz (Options > Metadata, Releases):
                                           Picard.ini first, else the defaults of the Picard
                                           version that wrote it; a profile's "picard" entries
                                           win over both
  source()                       -> str    where the settings came from (for `doctor` and plans)

Picard.ini is found at $TAGMENDER_PICARD_INI, else the Flatpak location, else
~/.config/MusicBrainz/Picard.ini. It is only ever read. The plugin token is never returned.
"""
from __future__ import annotations

import configparser
import importlib.util
import os
from pathlib import Path

DISCOGS_PLUGIN_UUID = "dd789f69-332f-498a-af94-d8e3273cc435"
CANDIDATES = ("~/.var/app/org.musicbrainz.Picard/config/MusicBrainz/Picard.ini",
              "~/.config/MusicBrainz/Picard.ini")

# The plugin's settings and their defaults (discogs_runtime.DEFAULTS), used when the plugin's
# own copy cannot be read. Only what changes the tags written is listed.
FALLBACK_DEFAULTS = {
    "merge_mode": "combine",
    "style_mode": "separate",
    "protected_tags": "",
    "marketplace": False,
    "performers": True,
    "companies": True,
    "identifiers": True,
    "import_notes": False,
    "prefer_original_date": False,
    "split_collaborations": True,
    "track_tags": True,
}
CHOICES = {
    "merge_mode": ("mb_only", "fill_gaps", "override", "combine", "discogs_only"),
    "style_mode": ("separate", "genre", "both"),
}
BOOLS = tuple(k for k, v in FALLBACK_DEFAULTS.items() if isinstance(v, bool))

_cache: dict = {}


def ini_path() -> Path | None:
    env = os.environ.get("TAGMENDER_PICARD_INI")
    if env:
        p = Path(env).expanduser()
        return p if p.exists() else None
    for c in CANDIDATES:
        p = Path(c).expanduser()
        if p.exists():
            return p
    return None


def _read(path: Path | None) -> configparser.RawConfigParser | None:
    if path is None:
        return None
    try:
        key = (str(path), path.stat().st_mtime_ns)
    except OSError:
        return None
    if key not in _cache:
        cp = configparser.RawConfigParser(strict=False, interpolation=None)
        cp.optionxform = str
        try:
            cp.read(path, encoding="utf-8")
        except (configparser.Error, OSError, UnicodeDecodeError):
            cp = None
        _cache.clear()
        _cache[key] = cp
    return _cache[key]


def plugin_defaults() -> dict:
    """The plugin's own defaults, from its discogs_runtime.py when it can be found."""
    out = dict(FALLBACK_DEFAULTS)
    try:
        import companions
        f = companions.discogs_dir() / "discogs_runtime.py"
        spec = importlib.util.spec_from_file_location("_tm_discogs_runtime", f)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for k in FALLBACK_DEFAULTS:
            if k in mod.DEFAULTS:
                out[k] = mod.DEFAULTS[k]
    except Exception:
        pass
    return out


def _value(key, raw):
    if key in BOOLS:
        return str(raw).strip().lower() in ("true", "1", "yes", "on")
    return _qt_text(str(raw)).strip()


def from_ini(path: Path | None = None) -> dict:
    """The plugin settings Picard.ini holds (only those it holds)."""
    cp = _read(path if path is not None else ini_path())
    sec = f"plugin.{DISCOGS_PLUGIN_UUID}"
    if cp is None or not cp.has_section(sec):
        return {}
    return {k: _value(k, cp.get(sec, k)) for k in FALLBACK_DEFAULTS if cp.has_option(sec, k)}


def enrichment(overrides: dict | None = None, path: Path | None = None) -> dict:
    s = plugin_defaults()
    s.update(from_ini(path))
    for k, v in (overrides or {}).items():
        if k in FALLBACK_DEFAULTS:
            s[k] = _value(k, v) if isinstance(v, str) and k in BOOLS else v
    for k, allowed in CHOICES.items():
        if s.get(k) not in allowed:
            s[k] = FALLBACK_DEFAULTS[k]
    return s


def preserved_tags(path: Path | None = None) -> set:
    """Tags Picard keeps as they are on the file, lower case. Empty when Picard.ini is absent."""
    cp = _read(path if path is not None else ini_path())
    if cp is None:
        return set()
    raw = cp.get("setting", "preserved_tags", fallback="")
    if raw.strip().startswith("@") or '"' in raw:            # (one tag: Qt's binary form; or quoted)
        return {x.lower() for x in _qt_list(raw)}
    return {x.strip().lower() for x in raw.replace("\n", ",").split(",") if x.strip()}


def picard_bool(name: str, default: bool = False, path: Path | None = None) -> bool:
    """One of Picard's own true/false settings ([setting] in Picard.ini)."""
    cp = _read(path if path is not None else ini_path())
    if cp is None or not cp.has_option("setting", name):
        return default
    return cp.get("setting", name).strip().lower() == "true"


# Picard's settings that change the tags written from MusicBrainz, with Picard 3's defaults
# (picard/options.py). A Picard 2 Picard.ini without a setting means Picard 2's default.
TAG_DEFAULTS = {
    "standardize_artist_names": "variations",     # none | variations | all
    "always_standardize_multivalue_artist": False,
    "translate_artist_names": False,
    "translation_locales": ["en"],
    "translate_from_sortname": False,
    "translate_album_titles": False,
    "translate_track_titles": False,
    "release_ars": True,
    "track_ars": False,
    "standardize_instruments": True,
    "standardize_vocals": True,
    "va_name": "Various Artists",
    "convert_punctuation": False,
    "preferred_release_countries": [],
    "use_genres": False,                          # Options > Metadata > Genres
    "only_my_genres": False,
    "artists_genres": False,
    "folksonomy_tags": False,
    "max_genres": 5,
    "min_genre_usage": 90,
    "genres_filter": "-seen live\n-favorites\n-fixme\n-owned",
    "join_genres": "",
    "enable_tagger_scripts": False,               # Options > Scripting
    "list_of_scripts": [],                        # [(position, name, enabled, script text)]
}
PICARD2_DEFAULTS = {"standardize_artist_names": "none"}      # (Picard 2: "standardize_artists", off)
STANDARDIZE = ("none", "variations", "all")
_LISTS = tuple(k for k, v in TAG_DEFAULTS.items() if isinstance(v, list))
_TAG_BOOLS = tuple(k for k, v in TAG_DEFAULTS.items() if isinstance(v, bool))
_TAG_INTS = tuple(k for k, v in TAG_DEFAULTS.items() if isinstance(v, int) and not isinstance(v, bool))


def _qt_unescape(raw: str) -> list:
    """The strings of one value as Qt's QSettings writes it to an ini file (IniFormat): items
    split by top-level commas, "..." quoting, \\n \\t \\0 \\xHHHH escapes; an item
    "@Variant(...)" is a value in Qt's binary form (see _qt_variant)."""
    out, cur, quoted, i, n, any_text = [], [], False, 0, len(raw), False
    raw = raw.strip()
    n = len(raw)
    while i < n:
        c = raw[i]
        if c == '"':
            quoted = not quoted
            any_text = True
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            e = raw[i + 1]
            if e == "x":
                j = i + 2
                while j < n and j < i + 6 and raw[j] in "0123456789abcdefABCDEF":
                    j += 1
                cur.append(chr(int(raw[i + 2:j] or "0", 16)))
                i = j
                continue
            cur.append({"n": "\n", "t": "\t", "r": "\r", "0": "\0", "a": "\a", "b": "\b", "f": "\f",
                        "v": "\v"}.get(e, e))
            i += 2
            continue
        if c == "," and not quoted:
            out.append("".join(cur).strip() if not any_text else "".join(cur))
            cur, any_text = [], False
            i += 1
            while i < n and raw[i] == " ":
                i += 1
            continue
        cur.append(c)
        i += 1
    out.append("".join(cur))
    return out


def _qt_variant(data: str):
    """A list of strings (or a string) from Qt's binary QVariant form, as Qt 5 and 6 write
    it for a list of one string; None when it is something else."""
    import struct
    b = data.encode("latin-1", "replace")

    def parse(qt5):
        pos = 0

        def u32():
            nonlocal pos
            v = struct.unpack(">I", b[pos:pos + 4])[0]
            pos += 4
            return v

        def qstring():
            nonlocal pos
            ln = u32()
            if ln == 0xFFFFFFFF:
                return ""
            v = b[pos:pos + ln].decode("utf-16-be")
            pos += ln
            return v

        def variant():
            nonlocal pos
            t = u32()
            if qt5:
                pos += 1                                       # (the "is null" byte of Qt 5)
            if t == 10:                                        # QString
                return qstring()
            if t in (9, 11):                                   # QVariantList, QStringList
                return [variant() if t == 9 else qstring() for _ in range(u32())]
            if t == 1:                                         # bool
                v = b[pos] != 0
                pos += 1
                return v
            if t in (2, 3):                                    # int, uint
                v = struct.unpack(">i" if t == 2 else ">I", b[pos:pos + 4])[0]
                pos += 4
                return v
            if t in (4, 5):                                    # qlonglong, qulonglong
                v = struct.unpack(">q" if t == 4 else ">Q", b[pos:pos + 8])[0]
                pos += 8
                return v
            if t == 127:                                       # a user type: PyQt's Python object
                ln = u32()
                name = b[pos:pos + ln]
                pos += ln
                if not name.startswith(b"PyQt_PyObject"):
                    raise ValueError(name)
                ln = u32()
                v = _plain_unpickle(b[pos:pos + ln])
                pos += ln
                return list(v) if isinstance(v, tuple) else v
            raise ValueError(t)
        v = variant()
        if pos != len(b):
            raise ValueError("left over")
        return v
    for qt5 in (False, True):
        try:
            return parse(qt5)
        except (ValueError, struct.error, UnicodeDecodeError):
            continue
    return None


def _plain_unpickle(data: bytes):
    """Plain data (tuples, lists, strings, numbers, booleans) from a pickle, as PyQt stores a
    Python value in an ini file - never any object: no class or function is ever looked up,
    so nothing in the file can run."""
    import io
    import pickle

    class Plain(pickle.Unpickler):
        def find_class(self, module, name):
            raise pickle.UnpicklingError(f"not plain data: {module}.{name}")
    try:
        v = Plain(io.BytesIO(data)).load()
    except Exception as exc:
        raise ValueError(str(exc)) from None

    def plain(x):
        if isinstance(x, (str, int, float, bool)) or x is None:
            return True
        if isinstance(x, (list, tuple)):
            return all(plain(y) for y in x)
        return False
    if not plain(v):
        raise ValueError("not plain data")
    return v


def _qt_value(raw: str):
    """A string, or a list of strings, from an ini value as Qt wrote it."""
    items = _qt_unescape(raw)
    vals = []
    for it in items:
        if it.startswith("@Variant(") and it.endswith(")"):
            v = _qt_variant(it[9:-1])
            if isinstance(v, list):
                vals.extend(str(x) for x in v)
                continue
            if isinstance(v, str):
                vals.append(v)
                continue
            continue
        if it.startswith("@Invalid("):
            continue
        vals.append(it)
    return vals if len(items) > 1 or (items and items[0].startswith("@Variant(")) else (vals[0] if vals else "")


def _scripts(raw: str) -> list:
    """Picard's tagger scripts as it keeps them: [(position, name, enabled, text)]."""
    items = _qt_unescape(raw)
    out = []
    for it in items:
        v = _qt_variant(it[9:-1]) if it.startswith("@Variant(") and it.endswith(")") else None
        if isinstance(v, list):
            for x in v if v and isinstance(v[0], list) else [v]:
                if isinstance(x, list) and len(x) == 4:
                    out.append([int(x[0]) if isinstance(x[0], int) else 0, str(x[1]), bool(x[2]), str(x[3])])
    return out


def tagger_scripts(settings: dict) -> list:
    """The texts of the tagger scripts that run: enabled and not empty, in their order (none
    unless "Enable tagger scripts" is on). A profile may list plain texts."""
    if not settings.get("enable_tagger_scripts"):
        return []
    out = []
    for x in settings.get("list_of_scripts") or []:
        if isinstance(x, str):
            out.append((0, x))
        elif isinstance(x, (list, tuple)) and len(x) == 4 and x[2] and x[3]:
            out.append((x[0] if isinstance(x[0], int) else 0, str(x[3])))
    return [t for _p, t in out if t.strip()]


def _qt_list(raw: str) -> list:
    v = _qt_value(raw)
    return [x.strip() for x in (v if isinstance(v, list) else [v]) if str(x).strip()]


def _qt_text(raw: str) -> str:
    v = _qt_value(raw)
    return ", ".join(v) if isinstance(v, list) else v


def picard_version(path: Path | None = None) -> tuple:
    """The version of Picard that last wrote Picard.ini, e.g. (3, 0, 2); () unknown."""
    import re as _re
    cp = _read(path if path is not None else ini_path())
    raw = cp.get("application", "version", fallback="") if cp is not None else ""
    return tuple(int(x) for x in _re.findall(r"\d+", raw)[:3])


def tag_settings(overrides: dict | None = None, path: Path | None = None) -> dict:
    p = path if path is not None else ini_path()
    cp = _read(p)
    s = {k: (list(v) if isinstance(v, list) else v) for k, v in TAG_DEFAULTS.items()}
    if cp is not None:
        ver = picard_version(p)
        if ver and ver[0] < 3:
            s.update(PICARD2_DEFAULTS)
        get = (lambda k: cp.get("setting", k)) if cp.has_section("setting") else None
        if get is not None:
            if cp.has_option("setting", "standardize_artists"):          # Picard 2
                s["standardize_artist_names"] = "all" if get("standardize_artists").strip().lower() == "true" \
                    else "none"
            if cp.has_option("setting", "artist_locales") and not cp.has_option("setting", "translation_locales"):
                s["translation_locales"] = _qt_list(get("artist_locales")) or s["translation_locales"]
            if cp.has_option("setting", "standardize_instruments") and \
                    not cp.has_option("setting", "standardize_vocals"):     # (Picard 2: one setting for both)
                s["standardize_vocals"] = get("standardize_instruments").strip().lower() == "true"
            for k in TAG_DEFAULTS:
                if not cp.has_option("setting", k):
                    continue
                raw = get(k)
                if k == "list_of_scripts":
                    s[k] = _scripts(raw)
                    continue
                if k in _TAG_BOOLS:
                    s[k] = raw.strip().lower() == "true"
                elif k in _TAG_INTS:
                    try:
                        s[k] = int(raw.strip())
                    except ValueError:
                        pass
                elif k in _LISTS:
                    s[k] = _qt_list(raw)
                else:
                    s[k] = _qt_text(raw)
    for k, v in (overrides or {}).items():
        if k in TAG_DEFAULTS:
            s[k] = v
    if s["standardize_artist_names"] not in STANDARDIZE:
        s["standardize_artist_names"] = TAG_DEFAULTS["standardize_artist_names"]
    return s


def validate_tag_settings(overrides: dict) -> list[str]:
    p = []
    for k, v in (overrides or {}).items():
        if k not in TAG_DEFAULTS:
            p.append(f"picard.{k} is not a Picard setting TagMender follows (use {', '.join(TAG_DEFAULTS)})")
        elif k == "standardize_artist_names" and v not in STANDARDIZE:
            p.append(f"picard.standardize_artist_names must be one of {', '.join(STANDARDIZE)}")
        elif k in _TAG_BOOLS and not isinstance(v, bool):
            p.append(f"picard.{k} must be true or false")
        elif k == "list_of_scripts" and not (isinstance(v, list) and all(
                isinstance(x, str) or (isinstance(x, list) and len(x) == 4 and isinstance(x[3], str)) for x in v)):
            p.append("picard.list_of_scripts must be a list of scripts: texts, or [position, name, enabled, text]")
        elif k in _LISTS and k != "list_of_scripts" and not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
            p.append(f"picard.{k} must be a list of strings")
        elif k == "va_name" and not (isinstance(v, str) and v.strip()):
            p.append("picard.va_name must be a name")
        elif k in _TAG_INTS and not (isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 100):
            p.append(f"picard.{k} must be a number from 0 to 100")
        elif k in ("genres_filter", "join_genres") and not isinstance(v, str):
            p.append(f"picard.{k} must be text")
    return p


def source(path: Path | None = None) -> str:
    p = path if path is not None else ini_path()
    return str(p) if p is not None and from_ini(p) else "the plugin's defaults (no Picard.ini settings found)"


def validate(overrides: dict) -> list[str]:
    p = []
    for k, v in (overrides or {}).items():
        if k not in FALLBACK_DEFAULTS:
            p.append(f"discogs_enrichment.{k} is not a Discogs Enrichment setting "
                     f"(use {', '.join(FALLBACK_DEFAULTS)})")
        elif k in CHOICES and v not in CHOICES[k]:
            p.append(f"discogs_enrichment.{k} must be one of {', '.join(CHOICES[k])}")
        elif k in BOOLS and not isinstance(v, bool):
            p.append(f"discogs_enrichment.{k} must be true or false")
    return p
