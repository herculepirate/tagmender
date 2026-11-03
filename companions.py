"""companions.py - where TagMender finds the Discogs Enrichment and CoverUp code.

Both are Picard plugins whose Picard-free modules TagMender loads directly (no copy, no
changes to the plugins). The first location that holds them wins:

  1. $TAGMENDER_DISCOGS_PLUGIN / $TAGMENDER_COVERUP_DIR
  2. the plugin folder set on this computer (app_settings "plugins.folder": a folder holding
     picard-discogs-enrichment/ and picard-coverup/, Preferences > Advanced; never a profile)
  3. the copies bundled with an installed TagMender package (<install>/vendor/...)
  4. working copies next to a TagMender run from its source (../picard-discogs-enrichment, ...)

A --plugin-dir / --coverup-dir option on a tool still overrides all of these.
"""
from __future__ import annotations

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENDOR = HERE / "vendor"

DISCOGS = ("TAGMENDER_DISCOGS_PLUGIN", str(HERE.parent / "picard-discogs-enrichment"),
           "picard-discogs-enrichment", "discogs_mapping.py")
COVERUP = ("TAGMENDER_COVERUP_DIR", str(HERE.parent / "picard-coverup"), "picard-coverup", "pipeline.py")


def _settings_folder() -> str:
    try:
        import app_settings
        return str(app_settings.get("plugins.folder") or "")
    except Exception:              # (unreadable settings: as if none were set)
        return ""


def _find(spec) -> Path:
    env, home, vendored, marker = spec
    own = _settings_folder()
    candidates = [os.environ.get(env, ""), own and str(Path(own).expanduser() / vendored), own,
                  str(VENDOR / vendored), home]
    for c in candidates:
        if c and (Path(c).expanduser() / marker).exists():
            return Path(c).expanduser()
    return Path(os.environ.get(env) or home).expanduser()      # the place to put it (named in errors)


def discogs_dir() -> Path:
    return _find(DISCOGS)


def coverup_dir() -> Path:
    return _find(COVERUP)


def version_of(path: Path) -> str:
    """The plugin's version from its MANIFEST.toml, and the git commit when it is a clone."""
    out = []
    try:
        for line in (Path(path) / "MANIFEST.toml").read_text(encoding="utf-8").splitlines():
            if line.startswith("version"):
                out.append(line.split("=", 1)[1].strip().strip('"'))
    except OSError:
        pass
    head = Path(path) / ".git" / "HEAD"
    try:
        ref = head.read_text().strip()
        if ref.startswith("ref:"):
            ref = (Path(path) / ".git" / ref[5:]).read_text().strip()
        out.append(ref[:8])
    except OSError:
        stamp = Path(path) / "VENDORED_COMMIT"
        if stamp.exists():
            out.append(stamp.read_text().strip()[:8])
    return " ".join(out) or "unknown"
