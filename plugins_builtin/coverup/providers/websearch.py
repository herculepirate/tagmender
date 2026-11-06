# -*- coding: utf-8 -*-
# CoverUp: MusicBrainz Picard plugin. GPL-2.0-or-later.
"""Web image search with the user's own API key (Brave Search or Google Programmable Search).

Search engines' result pages are never scraped. Results are unanchored (tier C): they only win when no
release-anchored art exists, and then only through visual agreement with other sources.
"""

from __future__ import annotations

import urllib.parse

from ..model import ArtType, Candidate, Query, Tier
from .base import Provider

BRAVE = "https://api.search.brave.com/res/v1/images/search?{q}"
GOOGLE = "https://www.googleapis.com/customsearch/v1?{q}"


def parse_brave(payload: dict) -> list[Candidate]:
    out = []
    for i, r in enumerate((payload or {}).get("results") or []):
        props = r.get("properties") or {}
        url = props.get("url") or ""
        if not url:
            continue
        w, h = int(props.get("width") or 0), int(props.get("height") or 0)
        out.append(
            Candidate(
                "websearch", url, url, ArtType.FRONT, Tier.C,
                width=w, height=h, dims_known=bool(w and h),
                thumb_url=(r.get("thumbnail") or {}).get("src") or url,
                type_confidence=0.6, attribution=r.get("source") or r.get("url") or "web", comment=r.get("title") or "", page=i,
            )
        )
    return out


def parse_google(payload: dict) -> list[Candidate]:
    out = []
    for i, item in enumerate((payload or {}).get("items") or []):
        img = item.get("image") or {}
        url = item.get("link") or ""
        if not url:
            continue
        w, h = int(img.get("width") or 0), int(img.get("height") or 0)
        out.append(
            Candidate(
                "websearch", url, url, ArtType.FRONT, Tier.C,
                width=w, height=h, dims_known=bool(w and h),
                thumb_url=img.get("thumbnailLink") or url,
                type_confidence=0.6, attribution=item.get("displayLink") or "web", comment=item.get("title") or "", page=i,
            )
        )
    return out


class WebSearch(Provider):
    ID = "websearch"
    NAME = "Web image search"
    NEEDS_KEY = True

    def available(self) -> tuple[bool, str]:
        if not self.settings["websearch_api_key"]:
            return False, "Web image search needs your own API key (Preferences › Keys)"
        if self.settings["websearch_engine"] == "google" and not self.settings["websearch_google_cx"]:
            return False, "Google image search needs a search engine ID (cx)"
        return True, ""

    def search(self, q: Query) -> list[Candidate]:
        term = f"{q.artist} {q.album} album cover".strip()
        if self.settings["websearch_engine"] == "google":
            params = {
                "key": self.settings["websearch_api_key"], "cx": self.settings["websearch_google_cx"],
                "q": term, "searchType": "image", "num": "10", "imgSize": "large",
            }
            return parse_google(self.http.get_json(GOOGLE.format(q=urllib.parse.urlencode(params))))[: self.limit]
        params = {"q": term, "count": str(min(20, self.limit)), "safesearch": "strict"}
        headers = {"X-Subscription-Token": self.settings["websearch_api_key"]}
        return parse_brave(self.http.get_json(BRAVE.format(q=urllib.parse.urlencode(params)), headers))[: self.limit]
