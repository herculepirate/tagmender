# -*- coding: utf-8 -*-
# CoverUp: MusicBrainz Picard plugin. GPL-2.0-or-later.
"""MusicBrainz release lookup used to complete the query (Discogs link, release group, media count)."""

from __future__ import annotations

import logging
import re

from ..model import Query
from ..net import HttpClient, HttpError

log = logging.getLogger("coverup.providers.musicbrainz")

MB_RELEASE = "https://musicbrainz.org/ws/2/release/{mbid}?inc=url-rels+release-groups+artist-credits+media+labels&fmt=json"
_DISCOGS_RELEASE = re.compile(r"discogs\.com/(?:[a-z]{2}/)?release/(\d+)", re.IGNORECASE)
_MBID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def is_mbid(value: str) -> bool:
    return bool(value and _MBID.match(value.strip().lower()))


def discogs_id_from_relations(release: dict) -> int | None:
    for rel in release.get("relations") or []:
        if rel.get("target-type") == "url" and rel.get("type") == "discogs":
            m = _DISCOGS_RELEASE.search((rel.get("url") or {}).get("resource", ""))
            if m:
                return int(m.group(1))
    return None


def enrich_query(http: HttpClient, q: Query) -> Query:
    """Fill missing identifiers from the MusicBrainz release. Never raises (logs and returns q)."""
    if not is_mbid(q.release_mbid):
        return q
    try:
        rel = http.get_json(MB_RELEASE.format(mbid=q.release_mbid))
    except HttpError as exc:
        log.warning("MusicBrainz lookup failed: %s", exc)
        return q
    apply_release_json(q, rel)
    return q


def apply_release_json(q: Query, rel: dict) -> None:
    if q.discogs_release_id is None:
        q.discogs_release_id = discogs_id_from_relations(rel)
    rg = rel.get("release-group") or {}
    if not q.rg_mbid and rg.get("id"):
        q.rg_mbid = rg["id"]
    credits = rel.get("artist-credit") or []
    if not q.artist_mbid and credits and (credits[0].get("artist") or {}).get("id"):
        q.artist_mbid = credits[0]["artist"]["id"]
    if not q.artist and credits:
        q.artist = "".join((c.get("name") or "") + (c.get("joinphrase") or "") for c in credits)
    if not q.album and rel.get("title"):
        q.album = rel["title"]
    media = rel.get("media") or []
    if media:
        q.medium_count = max(q.medium_count or 1, len(media))
        if not q.format:
            q.format = media[0].get("format") or ""
        if not q.track_count:
            q.track_count = sum(int(m.get("track-count") or 0) for m in media)
    if not q.barcode and rel.get("barcode"):
        q.barcode = rel["barcode"]
    labels = rel.get("label-info") or []
    if labels:
        if not q.catalog_number and labels[0].get("catalog-number"):
            q.catalog_number = labels[0]["catalog-number"]
        if not q.label and (labels[0].get("label") or {}).get("name"):
            q.label = labels[0]["label"]["name"]
    if not q.year and rel.get("date"):
        try:
            q.year = int(rel["date"][:4])
        except ValueError:
            pass
    if not q.comment and rel.get("disambiguation"):
        q.comment = rel["disambiguation"]
    if not q.country and rel.get("country"):
        q.country = rel["country"]
    log.info(
        "Identity: release=%s rg=%s artist=%s discogs=%s media=%s barcode=%s catno=%s",
        q.release_mbid, q.rg_mbid, q.artist_mbid, q.discogs_release_id, q.medium_count, q.barcode or "-", q.catalog_number or "-",
    )
