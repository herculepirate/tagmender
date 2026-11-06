# -*- coding: utf-8 -*-
# CoverUp: MusicBrainz Picard plugin. GPL-2.0-or-later.
"""TheAudioDB: release-group album art (front, back, CD art) and artist images (tier B)."""

from __future__ import annotations

import urllib.parse

from ..model import ArtType, Candidate, Query, Tier
from ..net import HttpError
from .base import Provider
from .musicbrainz import is_mbid

API = "https://www.theaudiodb.com/api/v1/json/{key}/{endpoint}?{query}"

ALBUM_FIELDS = (
    ("strAlbumThumbHQ", ArtType.FRONT, 1.0),
    ("strAlbumThumb", ArtType.FRONT, 1.0),
    ("strAlbumThumbBack", ArtType.BACK, 1.0),
    ("strAlbumCDart", ArtType.DISC, 1.0),
    ("strAlbumSpine", ArtType.BOOKLET, 0.6),
)
ARTIST_FIELDS = (
    ("strArtistThumb", ArtType.ARTIST_THUMB),
    ("strArtistLogo", ArtType.ARTIST_LOGO),
    ("strArtistFanart", ArtType.ARTIST_BACKGROUND),
    ("strArtistBanner", ArtType.ARTIST_BANNER),
)


def _c(url: str, art: ArtType, field: str, conf: float = 1.0) -> Candidate:
    return Candidate(
        provider="audiodb",
        provider_id=url,
        url=url,
        art_type=art,
        tier=Tier.B,
        thumb_url=url.rstrip("/") + "/preview",
        type_confidence=conf,
        attribution="TheAudioDB",
        comment=field,
    )


def parse_album(payload: dict, medium_count: int) -> list[Candidate]:
    out: list[Candidate] = []
    albums = (payload or {}).get("album") or []
    if not albums:
        return out
    a = albums[0]
    seen = set()
    for field, art, conf in ALBUM_FIELDS:
        url = a.get(field)
        if url and url not in seen:
            seen.add(url)
            c = _c(url, art, field, conf)
            if art == ArtType.DISC:
                # TheAudioDB has one CD art per album: only certain for single-disc releases
                c.disc_number = 1 if medium_count <= 1 else None
            out.append(c)
    return out


def parse_artist(payload: dict) -> list[Candidate]:
    out: list[Candidate] = []
    artists = (payload or {}).get("artists") or []
    if not artists:
        return out
    a = artists[0]
    for field, art in ARTIST_FIELDS:
        if a.get(field):
            out.append(_c(a[field], art, field))
    return out


class TheAudioDB(Provider):
    ID = "audiodb"
    NAME = "TheAudioDB"

    def _get(self, endpoint: str, **params: str) -> dict:
        key = self.settings["audiodb_api_key"] or "123"
        url = API.format(key=urllib.parse.quote(str(key)), endpoint=endpoint, query=urllib.parse.urlencode(params))
        try:
            return self.http.get_json(url) or {}
        except HttpError as exc:
            if exc.status == 404:
                return {}
            raise

    def search(self, q: Query) -> list[Candidate]:
        out: list[Candidate] = []
        if is_mbid(q.rg_mbid):
            out += parse_album(self._get("album-mb.php", i=q.rg_mbid), q.medium_count)
        if is_mbid(q.artist_mbid) and self.wants_artist_art(q):
            out += parse_artist(self._get("artist-mb.php", i=q.artist_mbid))
        return out
