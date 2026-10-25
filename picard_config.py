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
    return str(raw).strip()


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
    return {x.strip().lower() for x in raw.replace("\n", ",").split(",") if x.strip()}


def picard_bool(name: str, default: bool = False, path: Path | None = None) -> bool:
    """One of Picard's own true/false settings ([setting] in Picard.ini)."""
    cp = _read(path if path is not None else ini_path())
    if cp is None or not cp.has_option("setting", name):
        return default
    return cp.get("setting", name).strip().lower() == "true"


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
