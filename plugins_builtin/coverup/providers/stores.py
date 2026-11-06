# -*- coding: utf-8 -*-
# CoverUp: MusicBrainz Picard plugin. GPL-2.0-or-later.
"""Search-based front-cover sources: iTunes, Deezer, Last.fm (tier C, matched by artist/title)."""

from __future__ import annotations

import re
import urllib.parse

from ..model import ArtType, Candidate, Query, Tier
from ..net import HttpError
from .base import Provider, matches, similarity
from .musicbrainz import is_mbid


class ITunes(Provider):
    ID = "itunes"
    NAME = "iTunes"
    SEARCH = "https://itunes.apple.com/search?{q}"

    def search(self, q: Query) -> list[Candidate]:
        if not (q.artist or q.album):
            return []
        url = self.SEARCH.format(q=urllib.parse.urlencode({"term": f"{q.artist} {q.album}", "entity": "album", "limit": 15}))
        return parse_itunes(self.http.get_json(url), q)[: self.limit]


def parse_itunes(payload: dict, q: Query) -> list[Candidate]:
    out: list[Candidate] = []
    for r in (payload or {}).get("results") or []:
        art100 = r.get("artworkUrl100") or ""
        conf = matches(q, r.get("artistName", ""), r.get("collectionName", ""))
        if not art100 or not conf:
            continue
        big = re.sub(r"/\d+x\d+bb\.(jpg|png)$", r"/3000x3000bb.\1", art100)
        thumb = re.sub(r"/\d+x\d+bb\.(jpg|png)$", r"/250x250bb.\1", art100)
        c = Candidate("itunes", str(r.get("collectionId")), big, ArtType.FRONT, Tier.C, thumb_url=thumb, attribution="Apple Music")
        c.type_confidence = conf
        c.comment = f"{r.get('artistName')} - {r.get('collectionName')}"
        out.append(c)
    return out


class Deezer(Provider):
    ID = "deezer"
    NAME = "Deezer"
    SEARCH = "https://api.deezer.com/search/album?{q}"
    ARTIST_SEARCH = "https://api.deezer.com/search/artist?{q}"

    def search(self, q: Query) -> list[Candidate]:
        out: list[Candidate] = []
        if q.album:
            query = f'artist:"{q.artist}" album:"{q.album}"' if q.artist else q.album
            url = self.SEARCH.format(q=urllib.parse.urlencode({"q": query, "limit": 15}))
            out += parse_deezer(self.http.get_json(url), q)[: self.limit]
        if q.artist and self.wants_artist_art(q):
            try:
                url = self.ARTIST_SEARCH.format(q=urllib.parse.urlencode({"q": q.artist, "limit": 5}))
                out += parse_deezer_artist(self.http.get_json(url), q)
            except HttpError as exc:
                self.log.warning("Deezer artist search failed: %s", exc)
        return out


def parse_deezer_artist(payload: dict, q: Query) -> list[Candidate]:
    """Deezer's artist photo (1000 x 1000) when a hit has the same name (tier C: matched by name only)."""
    for r in (payload or {}).get("data") or []:
        name = r.get("name", "")
        sim = similarity(q.artist, name)
        url = r.get("picture_xl") or ""
        # artists without a photo get a grey placeholder whose address has an empty image hash
        if sim < 0.95 or not url or "/artist//" in url:
            continue
        c = Candidate(
            "deezer", f"artist:{r.get('id')}", url, ArtType.ARTIST_THUMB, Tier.C,
            width=1000, height=1000, dims_known=True, thumb_url=r.get("picture_medium") or url, attribution="Deezer",
        )
        c.type_confidence = sim * 0.9
        c.likes = int(r.get("nb_fan") or 0)
        c.comment = f"Deezer artist: {name}"
        return [c]
    return []


def parse_deezer(payload: dict, q: Query) -> list[Candidate]:
    out: list[Candidate] = []
    for r in (payload or {}).get("data") or []:
        artist = (r.get("artist") or {}).get("name", "")
        conf = matches(q, artist, r.get("title", ""))
        if not r.get("cover_xl") or not conf:
            continue
        c = Candidate(
            "deezer", str(r.get("id")), r["cover_xl"], ArtType.FRONT, Tier.C,
            width=1000, height=1000, dims_known=True,
            thumb_url=r.get("cover_medium") or r["cover_xl"], attribution="Deezer",
        )
        c.type_confidence = conf
        c.comment = f"{artist} - {r.get('title')}"
        out.append(c)
    return out


class LastFm(Provider):
    ID = "lastfm"
    NAME = "Last.fm"
    NEEDS_KEY = True
    API = "https://ws.audioscrobbler.com/2.0/?{q}"

    def available(self) -> tuple[bool, str]:
        if not self.settings["lastfm_api_key"]:
            return False, "Last.fm needs an API key (Preferences › Keys)"
        return True, ""

    def search(self, q: Query) -> list[Candidate]:
        params = {"method": "album.getinfo", "api_key": self.settings["lastfm_api_key"], "format": "json", "autocorrect": "1"}
        if is_mbid(q.release_mbid):
            params["mbid"] = q.release_mbid
        else:
            params.update({"artist": q.artist, "album": q.album})
        try:
            payload = self.http.get_json(self.API.format(q=urllib.parse.urlencode(params)))
        except HttpError as exc:
            if exc.status in (400, 404):
                return []
            raise
        if "mbid" in params and (payload or {}).get("error"):
            params.pop("mbid")
            params.update({"artist": q.artist, "album": q.album})
            payload = self.http.get_json(self.API.format(q=urllib.parse.urlencode(params)))
        return parse_lastfm(payload, q)


def parse_lastfm(payload: dict, q: Query) -> list[Candidate]:
    album = (payload or {}).get("album") or {}
    if not album or not matches(q, album.get("artist", ""), album.get("name", ""), threshold=0.75):
        return []
    images = {i.get("size"): i.get("#text") for i in album.get("image") or [] if i.get("#text")}
    url = images.get("mega") or images.get("extralarge") or images.get("large")
    if not url:
        return []
    original = re.sub(r"/u/\d+x\d+/", "/u/", url)  # Last.fm serves the original upload at /u/<hash>
    thumb = images.get("large") or url
    return [Candidate("lastfm", original, original, ArtType.FRONT, Tier.C, thumb_url=thumb, attribution="Last.fm")]
