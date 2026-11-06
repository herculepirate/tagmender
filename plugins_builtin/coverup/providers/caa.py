# -*- coding: utf-8 -*-
# CoverUp: MusicBrainz Picard plugin. GPL-2.0-or-later.
"""Cover Art Archive: typed images for the exact release (tier A) and its release group (tier B)."""

from __future__ import annotations

from ..model import ArtType, Candidate, Query, Tier
from ..net import HttpError
from .base import Provider
from .musicbrainz import is_mbid

RELEASE_URL = "https://coverartarchive.org/release/{mbid}"
RG_URL = "https://coverartarchive.org/release-group/{mbid}"

TYPE_MAP = {
    "front": ArtType.FRONT,
    "back": ArtType.BACK,
    "medium": ArtType.DISC,
    "booklet": ArtType.BOOKLET,
    "tray": ArtType.BOOKLET,
    "liner": ArtType.BOOKLET,
    "spine": ArtType.BOOKLET,
    "obi": ArtType.BOOKLET,
    "poster": ArtType.BOOKLET,
    "sticker": ArtType.BOOKLET,
}


def parse_images(payload: dict, tier: Tier) -> list[Candidate]:
    out: list[Candidate] = []
    booklet_page = 0
    for img in payload.get("images") or []:
        url = (img.get("image") or "").replace("http://", "https://", 1)
        if not url:
            continue
        types = [t.lower() for t in img.get("types") or []]
        if img.get("front"):
            art = ArtType.FRONT
        elif img.get("back"):
            art = ArtType.BACK
        else:
            art = next((TYPE_MAP[t] for t in types if t in TYPE_MAP), ArtType.OTHER)
        thumbs = img.get("thumbnails") or {}
        thumb = thumbs.get("250") or thumbs.get("small") or thumbs.get("500") or url
        cand = Candidate(
            provider="caa",
            provider_id=str(img.get("id", url)),
            url=url,
            art_type=art,
            tier=tier,
            thumb_url=thumb,
            comment=img.get("comment") or "",
            type_confidence=1.0 if types else 0.6,
            attribution="Cover Art Archive",
        )
        if art == ArtType.BOOKLET:
            booklet_page += 1
            cand.page = booklet_page
        # smaller renditions, tried if the original (often a huge PNG) cannot be downloaded
        cand.extra["fallback_urls"] = [u.replace("http://", "https://", 1) for k in ("1200", "large", "500") if (u := thumbs.get(k))]
        cand.extra["caa_types"] = types
        cand.extra["approved"] = bool(img.get("approved", True))
        out.append(cand)
    return out


class CoverArtArchive(Provider):
    ID = "caa"
    NAME = "Cover Art Archive"

    def search(self, q: Query) -> list[Candidate]:
        out: list[Candidate] = []
        seen: set[str] = set()
        if is_mbid(q.release_mbid):
            out += self._fetch(RELEASE_URL.format(mbid=q.release_mbid), Tier.A, seen)
        if is_mbid(q.rg_mbid):
            out += self._fetch(RG_URL.format(mbid=q.rg_mbid), Tier.B, seen)
        return out

    def _fetch(self, url: str, tier: Tier, seen: set[str]) -> list[Candidate]:
        try:
            payload = self.http.get_json(url)
        except HttpError as exc:
            if exc.status == 404:
                self.log.info("No Cover Art Archive images at %s", url)
                return []
            raise
        cands = [c for c in parse_images(payload, tier) if c.provider_id not in seen]
        seen.update(c.provider_id for c in cands)
        return cands
