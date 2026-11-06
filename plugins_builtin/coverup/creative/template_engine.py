# -*- coding: utf-8 -*-
# CoverUp: MusicBrainz Picard plugin. GPL-2.0-or-later.
"""Style templates for generated art: loading, placeholders, era and genre cues. Pure Python."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # Picard 3 bundles tomli on Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

from ..model import Query

log = logging.getLogger("coverup.creative")

BUILTIN_DIR = Path(__file__).resolve().parent / "templates"
CUSTOM = "custom"
DEFAULT_NEGATIVE = "text, letters, words, typography, watermark, logo, signature, frame, border, low quality, blurry"


@dataclass
class Template:
    name: str
    title: str
    prompt: str
    negative: str = DEFAULT_NEGATIVE
    procedural: str = "gradient"
    palette: list[str] = field(default_factory=lambda: ["#222222", "#888888", "#dddddd", "#c0392b"])
    overlay_logo: bool = True
    overlay_title: bool = True


def load_templates(*folders: Path | str | None) -> dict[str, Template]:
    """Built-in templates, then any *.toml in ``folders`` (later folders override earlier ones)."""
    out: dict[str, Template] = {}
    for folder in (BUILTIN_DIR, *[f for f in folders if f]):
        folder = Path(folder)
        if not folder.is_dir():
            continue
        for p in sorted(folder.glob("*.toml")):
            try:
                data = tomllib.loads(p.read_text(encoding="utf-8"))
                out[p.stem] = Template(
                    name=p.stem,
                    title=str(data.get("title") or p.stem.title()),
                    prompt=str(data["prompt"]),
                    negative=str(data.get("negative") or DEFAULT_NEGATIVE),
                    procedural=str(data.get("procedural") or "gradient"),
                    palette=[str(c) for c in data.get("palette") or []] or Template("", "", "").palette,
                    overlay_logo=bool(data.get("overlay_logo", True)),
                    overlay_title=bool(data.get("overlay_title", True)),
                )
            except (OSError, ValueError, KeyError) as exc:
                log.warning("Ignoring template %s: %s", p.name, exc)
    return out


def custom_template(prompt: str) -> Template:
    return Template(name=CUSTOM, title="Custom", prompt=prompt, procedural="gradient")


ERAS = [
    (1950, "1950s jazz-era record sleeve aesthetic,"),
    (1960, "1960s psychedelic poster aesthetic,"),
    (1970, "1970s gatefold album art aesthetic, analogue film colours,"),
    (1980, "1980s airbrush and neon aesthetic,"),
    (1990, "1990s photographic grunge and early digital aesthetic,"),
    (2000, "2000s glossy digital design aesthetic,"),
    (2010, "2010s minimalist modern design aesthetic,"),
    (2020, "contemporary 2020s design aesthetic,"),
]


def era_cue(year: int | None) -> str:
    if not year:
        return ""
    cue = ""
    for start, text in ERAS:
        if year >= start:
            cue = text
    if year < 1950:
        cue = "classic early 20th-century print aesthetic,"
    return cue


_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


def build_prompt(t: Template, q: Query, match_era: bool = True, match_genre: bool = True) -> str:
    values = {
        "album": q.album or "Untitled",
        "artist": q.artist or "Unknown artist",
        "year": str(q.year or ""),
        "genre": (f"{q.genre} music," if q.genre else "") if match_genre else "",
        "era": era_cue(q.year) if match_era else "",
    }
    text = _PLACEHOLDER.sub(lambda m: values.get(m.group(1), ""), t.prompt)
    return " ".join(text.replace(" ,", ",").split())
