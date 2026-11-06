# -*- coding: utf-8 -*-
# CoverUp: MusicBrainz Picard plugin. GPL-2.0-or-later.
"""Disc-number matching heuristics (design section 5.4)."""

from __future__ import annotations

import re

from ..model import ArtType, Candidate

# "Disc 2", "disk2", "CD 3", "CD2", "Disc two" (words one..ten), "Side B" is handled separately.
_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_NUM = re.compile(r"\b(?:disc|disk|cd|dvd|lp)\s*[-_#.:]?\s*(\d{1,2})\b", re.IGNORECASE)
_WORD = re.compile(r"\b(?:disc|disk|cd|dvd|lp)\s*[-_#.:]?\s*(" + "|".join(_WORDS) + r")\b", re.IGNORECASE)
_SIDE = re.compile(r"\bside\s*([a-d])\b", re.IGNORECASE)


def parse_disc_number(text: str | None) -> int | None:
    """Extract a disc number from free text such as a CAA comment. Returns None if absent."""
    if not text:
        return None
    m = _NUM.search(text)
    if m:
        n = int(m.group(1))
        return n if n > 0 else None
    m = _WORD.search(text)
    if m:
        return _WORDS[m.group(1).lower()]
    return None


def parse_vinyl_side(text: str | None) -> str | None:
    """Return 'A'-'D' for text like 'Side B', else None. Sides map to discs by the caller."""
    if not text:
        return None
    m = _SIDE.search(text)
    return m.group(1).upper() if m else None


def assign_disc_numbers(candidates: list[Candidate], total_discs: int) -> list[Candidate]:
    """Fill ``disc_number`` on disc candidates that lack one, using their comment text.

    Candidates whose number cannot be determined are left as ``None`` so the UI can ask the user.
    Numbers above ``total_discs`` are discarded (likely a different release's disc).
    """
    for c in candidates:
        if c.art_type != ArtType.DISC:
            continue
        if c.disc_number is None:
            c.disc_number = parse_disc_number(c.comment)
        if c.disc_number is not None and not (1 <= c.disc_number <= max(1, total_discs)):
            c.disc_number = None
    return candidates


def disc_for_track(discnumber_tag: str | None) -> int:
    """Disc number of a track from its ``discnumber`` tag (``'2'`` or ``'2/3'``); missing means disc 1."""
    if not discnumber_tag:
        return 1
    m = re.match(r"\s*(\d+)", str(discnumber_tag))
    n = int(m.group(1)) if m else 1
    return n if n > 0 else 1
