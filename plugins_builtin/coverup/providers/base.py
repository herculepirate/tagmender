# -*- coding: utf-8 -*-
# CoverUp: MusicBrainz Picard plugin. GPL-2.0-or-later.
"""Artwork provider base class and shared helpers. Providers are blocking and run in worker threads."""

from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from typing import TYPE_CHECKING

from ..model import Candidate, Query

if TYPE_CHECKING:
    from ..net import HttpClient
    from ..settings import Settings


class Provider:
    ID = "base"
    NAME = "Base"
    NEEDS_KEY = False

    def __init__(self, http: "HttpClient", settings: "Settings"):
        self.http = http
        self.settings = settings
        self.log = logging.getLogger(f"coverup.providers.{self.ID}")
        self.limit = int(settings.get("max_candidates_per_provider", 12) or 12)

    def available(self) -> tuple[bool, str]:
        """(usable, reason). Providers that need a missing key return False with a message."""
        return True, ""

    def search(self, q: Query) -> list[Candidate]:
        raise NotImplementedError

    def wants_artist_art(self, q: Query) -> bool:
        """Artist images are on, and this is an album by one artist (not a compilation)."""
        return bool(self.settings.get("artist_art")) and not is_various(q.artist) and bool(q.artist or q.artist_mbid)


_PAREN = re.compile(r"[\(\[].*?[\)\]]")
_NONWORD = re.compile(r"[^\w\s]+", re.UNICODE)
_EDITION = re.compile(
    r"\b(deluxe|remaster(ed)?|edition|expanded|anniversary|bonus|version|explicit|clean|mono|stereo)\b", re.IGNORECASE
)


def is_various(artist: str) -> bool:
    return normalize(artist) in ("various artists", "various", "va")


def normalize(text: str) -> str:
    """Lower-case, strip accents, bracketed parts, punctuation and edition words."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).lower()
    t = t.replace("&", " and ")
    t = _PAREN.sub(" ", t)
    t = _EDITION.sub(" ", t)
    t = _NONWORD.sub(" ", t)
    t = re.sub(r"^the\s+", "", t.strip())
    return " ".join(t.split())


def similarity(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def matches(q: Query, artist: str, album: str, threshold: float = 0.82) -> float:
    """Confidence 0..1 that a search hit is the queried album; 0 when below threshold."""
    sa = similarity(q.artist, artist) if q.artist else 0.5
    if q.artist and normalize(q.artist) in ("various artists", "various"):
        sa = 1.0 if normalize(artist) in ("various artists", "various") else 0.6
    sb = similarity(q.album, album)
    score = 0.4 * sa + 0.6 * sb
    return score if (sb >= threshold and sa >= threshold - 0.1) else 0.0
