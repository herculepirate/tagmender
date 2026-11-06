# -*- coding: utf-8 -*-
# CoverUp: MusicBrainz Picard plugin. GPL-2.0-or-later.
"""Local images already in the album folder (and its art sub-folders). Used as candidates and as the
reference for "is the new image better than what I have?"."""

from __future__ import annotations

import re
from pathlib import Path

from ..imageinfo import probe
from ..matching.disc import parse_disc_number
from ..model import ArtType, Candidate, Query, Tier
from .base import Provider

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".gif", ".bmp"}
SUBFOLDERS = ("covers", "cover", "art", "artwork", "artworks", "scans", "booklet", "images")
MAX_LOCAL = 60
SKIP_DIRS = {".coverup-backup"}

_WORDS = re.compile(r"[a-z]+|\d+")
_BACK = {"back", "rear", "backcover"}
_DISC = {"disc", "disk", "cd", "cdart", "vinyl", "medium", "label"}
_BOOKLET = {"booklet", "inlay", "insert", "tray", "inside", "scan", "page", "pages"}
_FRONT = {"cover", "folder", "front", "album", "albumart", "albumartsmall", "frontcover"}


def classify(name: str) -> tuple[ArtType, int | None]:
    """Guess the art type from any word in the file name ('Leonard - Front.jpg', 'scan_back_2.png')."""
    stem = Path(name).stem
    words = _WORDS.findall(stem.lower())
    wordset = set(words)
    if wordset & _BACK:
        return ArtType.BACK, None
    if wordset & _DISC:
        m = re.search(r"(?:disc|disk|cd|cdart|vinyl|medium)\s*[-_ ]?\s*(\d+)", stem, re.IGNORECASE)
        return ArtType.DISC, int(m.group(1)) if m else parse_disc_number(stem)
    if wordset & _BOOKLET:
        return ArtType.BOOKLET, None
    if wordset & _FRONT:
        return ArtType.FRONT, None
    return ArtType.OTHER, None


def scan_folder(folder: str | Path) -> list[Candidate]:
    base = Path(folder)
    out: list[Candidate] = []
    dirs = [base]
    try:  # every sub-folder, not only the usual names: scans are kept under all sorts of names
        dirs += sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".") and p.name not in SKIP_DIRS)
    except OSError:
        pass
    for d in dirs:
        try:
            entries = sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXT)
        except OSError:
            continue
        for p in entries:
            if len(out) >= MAX_LOCAL:
                return out
            art, disc = classify(p.name)
            try:
                data = p.read_bytes()
            except OSError:
                continue
            source_format = p.suffix.lower().lstrip(".")
            info = probe(data[:65536])
            w, h = (info[0], info[1]) if info else (0, 0)
            if not info:  # TIFF and friends: decode once (Qt) and keep a JPEG, so previews stay light
                try:
                    from .. import imaging

                    data = imaging.to_jpeg(data)
                    w, h = imaging.dimensions(data) or (0, 0)
                    info = (w, h, "jpeg") if w else None
                except Exception:  # noqa: BLE001 - an unreadable file is simply not offered
                    continue
            c = Candidate(
                "local", str(p), p.as_uri(), art, Tier.B, width=w, height=h, bytes=len(data), dims_known=bool(info),
                disc_number=disc, thumb_data=data, data=data, attribution="local file", comment=p.name,
                type_confidence=0.9 if art != ArtType.OTHER else 0.2,
            )
            c.extra["local_path"] = str(p)
            c.extra["source_format"] = source_format
            out.append(c)
    return out


class LocalFiles(Provider):
    ID = "local"
    NAME = "Local files"

    def search(self, q: Query) -> list[Candidate]:
        out: list[Candidate] = []
        seen: set[str] = set()
        for d in q.album_dirs:
            for c in scan_folder(d):
                if c.provider_id not in seen:
                    seen.add(c.provider_id)
                    out.append(c)
        return out
