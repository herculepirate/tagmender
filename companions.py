"""companions.py - where TagMender finds the code of its two built-in plugins, Discogs and
CoverUp (plugins.BUILTIN), and whether each is switched on.

Their code comes with TagMender, in plugins_builtin/discogs and plugins_builtin/coverup. For
development, $TAGMENDER_DISCOGS_PLUGIN / $TAGMENDER_COVERUP_DIR name another copy to use instead,
and a --plugin-dir / --coverup-dir option on a tool overrides both.
"""
from __future__ import annotations

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILTIN = HERE / "plugins_builtin"

DISCOGS = ("TAGMENDER_DISCOGS_PLUGIN", BUILTIN / "discogs", "discogs_mapping.py")
COVERUP = ("TAGMENDER_COVERUP_DIR", BUILTIN / "coverup", "pipeline.py")


def _find(spec) -> Path:
    env, builtin, marker = spec
    other = os.environ.get(env, "")
    if other and (Path(other).expanduser() / marker).exists():
        return Path(other).expanduser()
    return builtin


def discogs_dir() -> Path:
    return _find(DISCOGS)


def coverup_dir() -> Path:
    return _find(COVERUP)


def discogs_on() -> bool:
    """The Discogs plugin is switched on (Preferences > Plugins). Off: Discogs is not asked at all."""
    import plugins
    return plugins.builtin_on("discogs")


def coverup_on() -> bool:
    """The CoverUp plugin is switched on. Off: no cover is looked for through CoverUp."""
    import plugins
    return plugins.builtin_on("coverup")


def version_of(path: Path) -> str:
    """The built-in plugin's version when PATH is its own folder; else the git commit of a
    development copy."""
    import plugins
    path = Path(path)
    for pid, m in plugins.BUILTIN.items():
        if path.resolve() == (BUILTIN / pid).resolve():
            return m["version"]
    head = path / ".git" / "HEAD"
    try:
        ref = head.read_text().strip()
        if ref.startswith("ref:"):
            ref = (path / ".git" / ref[5:]).read_text().strip()
        return ref[:8]
    except OSError:
        return "unknown"
