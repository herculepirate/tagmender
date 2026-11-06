# -*- coding: utf-8 -*-
# CoverUp: MusicBrainz Picard plugin. GPL-2.0-or-later.
"""Fanart.tv: release-group covers and numbered disc art (tier B), plus artist images and logos."""

from __future__ import annotations

import urllib.parse

from ..model import ArtType, Candidate, Query, Tier
from ..net import HttpError
from .base import Provider
from .musicbrainz import is_mbid

API = "https://webservice.fanart.tv/v3/music"

# fanart.tv enforces fixed sizes per image type
SIZES = {
    "albumcover": (1000, 1000),
    "cdart": (1000, 1000),
    "artistthumb": (1000, 1000),
    "hdmusiclogo": (800, 310),
    "musiclogo": (400, 155),
    "artistbackground": (1920, 1080),
    "musicbanner": (1000, 185),
}

ARTIST_KINDS = {
    "artistthumb": ArtType.ARTIST_THUMB,
    "hdmusiclogo": ArtType.ARTIST_LOGO,
    "musiclogo": ArtType.ARTIST_LOGO,
    "artistbackground": ArtType.ARTIST_BACKGROUND,
    "musicbanner": ArtType.ARTIST_BANNER,
}


def preview_url(url: str) -> str:
    return url.replace("assets.fanart.tv/fanart/", "assets.fanart.tv/preview/")


def _likes(item: dict) -> int:
    try:
        return int(item.get("likes") or 0)
    except (TypeError, ValueError):
        return 0


def _cand(kind: str, item: dict, art: ArtType, tier: Tier) -> Candidate:
    w, h = SIZES.get(kind, (0, 0))
    c = Candidate(
        provider="fanart",
        provider_id=str(item.get("id") or item.get("url")),
        url=item.get("url", ""),
        art_type=art,
        tier=tier,
        width=w,
        height=h,
        dims_known=bool(w),
        thumb_url=preview_url(item.get("url", "")),
        likes=_likes(item),
        attribution="fanart.tv",
        comment=kind,
    )
    c.extra["fanart_kind"] = kind
    if item.get("lang"):
        c.extra["lang"] = item["lang"]
    return c


def parse_album(payload: dict, rg_mbid: str) -> list[Candidate]:
    out: list[Candidate] = []
    albums = payload.get("albums") or {}
    album = albums.get(rg_mbid) or (next(iter(albums.values())) if len(albums) == 1 else {})
    for item in album.get("albumcover") or []:
        out.append(_cand("albumcover", item, ArtType.FRONT, Tier.B))
    for item in album.get("cdart") or []:
        c = _cand("cdart", item, ArtType.DISC, Tier.B)
        try:
            c.disc_number = int(item.get("disc") or 0) or None
        except (TypeError, ValueError):
            c.disc_number = None
        try:
            size = int(item.get("size") or 0)
            if size:
                c.width = c.height = size
        except (TypeError, ValueError):
            pass
        out.append(c)
    return out


def parse_artist(payload: dict) -> list[Candidate]:
    out: list[Candidate] = []
    for kind, art in ARTIST_KINDS.items():
        for item in payload.get(kind) or []:
            c = _cand(kind, item, art, Tier.B)
            if kind == "hdmusiclogo":
                c.type_confidence = 1.0
            elif kind == "musiclogo":
                c.type_confidence = 0.8
            out.append(c)
    return out


class FanartTv(Provider):
    ID = "fanart"
    NAME = "Fanart.tv"
    NEEDS_KEY = True

    def available(self) -> tuple[bool, str]:
        if not self.settings["fanart_api_key"]:
            return False, "Fanart.tv needs a project API key (Preferences › Keys)"
        return True, ""

    def _url(self, path: str) -> str:
        params = {"api_key": self.settings["fanart_api_key"]}
        if self.settings["fanart_client_key"]:
            params["client_key"] = self.settings["fanart_client_key"]
        return f"{API}/{path}?{urllib.parse.urlencode(params)}"

    def _get(self, path: str) -> dict:
        try:
            return self.http.get_json(self._url(path)) or {}
        except HttpError as exc:
            if exc.status == 404:
                return {}
            raise

    def search(self, q: Query) -> list[Candidate]:
        out: list[Candidate] = []
        if is_mbid(q.rg_mbid):
            out += parse_album(self._get(f"albums/{q.rg_mbid}"), q.rg_mbid)
        if is_mbid(q.artist_mbid) and self.wants_artist_art(q):
            out += parse_artist(self._get(q.artist_mbid))
        return out
