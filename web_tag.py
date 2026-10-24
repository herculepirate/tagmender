#!/usr/bin/env python3
"""web_tag.py - the last resort for albums MusicBrainz and Discogs do not have.

Searches the web for the album, accepts a result only on the same proof the
matcher asks of MusicBrainz and Discogs (artist, title, track count and track
lengths), and writes the essential tags from it:

  album, album artist, artist and contributing artists per track, track titles,
  track and disc numbers, date, label, barcode, genre, and the MusicBrainz
  artist IDs of the artists when MusicBrainz knows them without doubt;
  TAGMENDER=Unresolved, TAGMENDER_REASON (why) and TAGMENDER_SOURCE (the page).

Sources, in order: Deezer and Apple iTunes (public catalogue APIs, no key),
Bandcamp album pages (the album data they publish for search engines), and a
DuckDuckGo search used only to find a Bandcamp page the direct search missed.

Dry run by default; --apply writes; --undo <run> restores every file exactly.

  python3 web_tag.py --matches-db ~/Downloads/matching_extreme/match_state.db
  python3 web_tag.py --matches-db ~/Downloads/matching_extreme/match_state.db --apply
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import match_core as mc  # noqa: E402
import match_albums as ma  # noqa: E402
import tag_albums as ta  # noqa: E402
import exclusions  # noqa: E402

USER_AGENT = "TagMender/0.1 +https://github.com/herculepirate/tagmender"
q = urllib.parse.quote


# --------------------------------------------------------------------------
# Sources. Each returns candidates in the matcher's form, so the matcher's own
# scoring decides; "page" is the address shown in TAGMENDER_SOURCE.
# --------------------------------------------------------------------------
def _cand(source, cid, url, artist, title, date, label="", barcode="", genre="", tracks=(), cover=""):
    year = int(str(date)[:4]) if str(date or "")[:4].isdigit() else None
    discs = max([t.get("disc") or 1 for t in tracks] or [1])
    return {"source": source, "id": str(cid), "url": url, "artist": artist or "", "artist_alt": "",
            "title": title or "", "year": year, "date": str(date or ""), "country": "", "label": label or "",
            "catnos": set(), "barcodes": {mc.norm_code(barcode)} if barcode else set(), "disc_count": discs,
            "tracks": list(tracks), "formats": {"digital"}, "genre": genre or "", "cover": cover or ""}


def deezer(http, artist, title):
    query = 'artist:"%s" album:"%s"' % (artist, title)
    data = http.get_json(f"https://api.deezer.com/search/album?q={q(query)}&limit=10", "deezer")
    hits = (data or {}).get("data") or []
    if not hits:
        data = http.get_json(f"https://api.deezer.com/search/album?q={q(artist + ' ' + title)}&limit=10", "deezer")
        hits = (data or {}).get("data") or []
    out = []
    for h in hits[:4]:
        al = http.get_json(f"https://api.deezer.com/album/{h['id']}", "deezer") or {}
        tr = http.get_json(f"https://api.deezer.com/album/{h['id']}/tracks?limit=300", "deezer") or {}
        if not al or al.get("error"):
            continue
        tracks = [{"disc": t.get("disk_number") or 1, "num": t.get("track_position"), "title": t.get("title") or "",
                   "secs": float(t.get("duration") or 0), "artist": (t.get("artist") or {}).get("name", "")}
                  for t in (tr.get("data") or [])]
        genres = [g.get("name") for g in (al.get("genres") or {}).get("data") or [] if g.get("name")]
        c = _cand("deezer", al["id"], al.get("link") or f"https://www.deezer.com/album/{al['id']}",
                  (al.get("artist") or {}).get("name"), al.get("title"), al.get("release_date"),
                  al.get("label"), al.get("upc"), "; ".join(genres), tracks, al.get("cover_xl"))
        c["contributors"] = [x.get("name") for x in al.get("contributors") or [] if x.get("name")]
        out.append(c)
    return out


def itunes(http, artist, title):
    data = http.get_json(f"https://itunes.apple.com/search?term={q(artist + ' ' + title)}&entity=album&limit=10",
                         "itunes")
    out = []
    for h in ((data or {}).get("results") or [])[:4]:
        cid = h.get("collectionId")
        if not cid:
            continue
        look = http.get_json(f"https://itunes.apple.com/lookup?id={cid}&entity=song", "itunes") or {}
        songs = [r for r in look.get("results") or [] if r.get("wrapperType") == "track"]
        songs.sort(key=lambda r: (r.get("discNumber") or 1, r.get("trackNumber") or 0))
        tracks = [{"disc": r.get("discNumber") or 1, "num": r.get("trackNumber"), "title": r.get("trackName") or "",
                   "secs": (r.get("trackTimeMillis") or 0) / 1000.0, "artist": r.get("artistName") or ""}
                  for r in songs]
        label = re.sub(r"^(?:℗|\(P\)|©)?\s*\d{4}\s*", "", str(h.get("copyright") or "")).strip()
        art = str(h.get("artworkUrl100") or "").replace("100x100bb", "3000x3000bb")
        out.append(_cand("itunes", cid, h.get("collectionViewUrl") or "", h.get("artistName"), h.get("collectionName"),
                         str(h.get("releaseDate") or "")[:10], label, "", h.get("primaryGenreName") or "",
                         tracks, art))
    return out


_ISO_DUR = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?")


def _iso_secs(v):
    m = _ISO_DUR.fullmatch(str(v or ""))
    if not m:
        return 0.0
    d, h, mi, s = m.groups()
    return int(d or 0) * 86400 + int(h or 0) * 3600 + int(mi or 0) * 60 + float(s or 0)


def bandcamp_album(http, url):
    """The album data a Bandcamp album page publishes (schema.org MusicAlbum)."""
    page = http.get_text(url, "bandcamp") or ""
    for block in re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S):
        try:
            d = json.loads(html.unescape(block))
        except ValueError:
            continue
        if d.get("@type") not in ("MusicAlbum", ["MusicAlbum"]):
            continue
        items = ((d.get("track") or {}).get("itemListElement")) or []
        tracks = []
        for it in sorted(items, key=lambda x: x.get("position") or 0):
            item = it.get("item") or {}
            tracks.append({"disc": 1, "num": it.get("position"), "title": item.get("name") or "",
                           "secs": _iso_secs(item.get("duration")),
                           "artist": (item.get("byArtist") or {}).get("name", "")})
        pub = d.get("publisher") or {}
        genre = ", ".join(d.get("keywords") or []) if isinstance(d.get("keywords"), list) else ""
        image = d.get("image")
        image = image[0] if isinstance(image, list) and image else image
        return _cand("bandcamp", url, url, (d.get("byArtist") or {}).get("name"), d.get("name"),
                     str(d.get("datePublished") or "")[:30], pub.get("name") if isinstance(pub, dict) else "",
                     "", genre, tracks, image or "")
    return None


def _date(text):
    """'21 Oct 2022 00:00:00 GMT' -> '2022-10-21'."""
    try:
        return time.strftime("%Y-%m-%d", time.strptime(text[:11], "%d %b %Y"))
    except ValueError:
        return text[:10]


def bandcamp(http, artist, title):
    page = http.get_text(f"https://bandcamp.com/search?q={q(artist + ' ' + title)}&item_type=a", "bandcamp") or ""
    urls = []
    for u in re.findall(r'<div class="itemurl">\s*<a href="([^"?]+)', page):
        if "/album/" in u and u not in urls:
            urls.append(u)
    if not urls:                              # a search engine finds what Bandcamp's own search misses
        ddg = http.get_text("https://html.duckduckgo.com/html/?q=" + q(f"{artist} {title} site:bandcamp.com"),
                            "duckduckgo") or ""
        for u in re.findall(r'uddg=([^&"]+)', ddg):
            u = urllib.parse.unquote(u).split("?")[0]
            if re.match(r"https?://[^/]+\.bandcamp\.com/album/", u) and u not in urls:
                urls.append(u)
    out = []
    for u in urls[:3]:
        c = bandcamp_album(http, u)
        if c:
            if c["date"] and not re.match(r"\d{4}-", c["date"]):
                c["date"] = _date(c["date"])
                c["year"] = int(c["date"][:4]) if c["date"][:4].isdigit() else c["year"]
            out.append(c)
    return out


def _wiki_value(box, field):
    """A field of a Wikipedia infobox, without links, references and markup."""
    m = re.search(r"\|\s*" + field + r"\s*=\s*(.*?)(?=\n\s*\||\n\}\})", box, re.S | re.I)
    if not m:
        return ""
    v = m.group(1)
    v = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>|<!--.*?-->", "", v, flags=re.S)
    v = re.sub(r"\{\{(?:start date|release date|dts)\|(\d{4})\|(\d{1,2})\|(\d{1,2})[^}]*\}\}",
               lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", v, flags=re.I)
    v = re.sub(r"\{\{(?:start date|release date)\|(\d{4})[^}]*\}\}", r"\1", v, flags=re.I)
    v = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", v)
    v = re.sub(r"\{\{(?:hlist|flatlist|ubl|unbulleted list)\|", "", v, flags=re.I)
    v = re.sub(r"\{\{[^{}]*\}\}", "", v)
    v = re.sub(r"<br\s*/?>|\n\*", ", ", v)
    v = re.sub(r"'{2,3}|[{}]|\*", "", v)
    return re.sub(r"\s+", " ", v).strip(" ,|")


def wikipedia_album(http, page_title):
    """An album article: its infobox and track listing (titles and lengths)."""
    data = http.get_json("https://en.wikipedia.org/w/api.php?action=parse&prop=wikitext&format=json&redirects=1"
                         f"&page={q(page_title)}", "wikipedia") or {}
    text = ((data.get("parse") or {}).get("wikitext") or {}).get("*", "")
    box = re.search(r"\{\{\s*Infobox album(.*?)\n\}\}", text, re.S | re.I)
    if not box:
        return None
    box = box.group(0)
    tracks = []
    for disc, listing in enumerate(re.findall(r"\{\{\s*Track ?listing(.*?)\n\}\}", text, re.S | re.I), 1):
        rows = {}
        for k, n, v in re.findall(r"\|\s*(title|length)(\d+)\s*=\s*([^\n|]*)", listing, re.I):
            rows.setdefault(int(n), {})[k.lower()] = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", v).strip()
        for n in sorted(rows):
            ln = rows[n].get("length", "")
            m = re.fullmatch(r"(?:(\d+):)?(\d+):(\d{2})", ln)
            secs = (int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))) if m else 0
            tracks.append({"disc": disc, "num": n, "title": rows[n].get("title", "").strip('"'),
                           "secs": float(secs), "artist": ""})
    name = _wiki_value(box, "name") or page_title
    released = _wiki_value(box, "released")
    m = re.search(r"\d{4}-\d{2}-\d{2}|\d{4}", released)
    url = "https://en.wikipedia.org/wiki/" + q(((data.get("parse") or {}).get("title") or page_title).replace(" ", "_"))
    return _cand("wikipedia", url, url, _wiki_value(box, "artist"), name, m.group(0) if m else "",
                 _wiki_value(box, "label").split(",")[0].strip(), "", _wiki_value(box, "genre"), tracks)


def wikipedia(http, artist, title):
    data = http.get_json("https://en.wikipedia.org/w/api.php?action=query&list=search&format=json&srlimit=5"
                         f"&srsearch={q(f'{title} {artist} album')}", "wikipedia") or {}
    out = []
    for hit in ((data.get("query") or {}).get("search") or [])[:3]:
        c = wikipedia_album(http, hit.get("title", ""))
        if c and c["tracks"]:
            out.append(c)
    return out


SOURCES = (("Deezer", deezer), ("iTunes", itunes), ("Bandcamp", bandcamp), ("Wikipedia", wikipedia))


def wikidata_artist_ids(http, name, cache):
    """(MusicBrainz ID, Discogs ID) Wikidata records for the one musician or band
    of that name (properties P434 and P1953); empty when the name is shared."""
    key = ("wikidata", mc.fold(name))
    if key in cache:
        return cache[key]
    data = http.get_json("https://www.wikidata.org/w/api.php?action=wbsearchentities&format=json&language=en"
                         f"&type=item&limit=10&search={q(name)}", "wikidata") or {}
    hits = [h for h in data.get("search") or [] if mc.fold(h.get("label")) == mc.fold(name)]
    found = []
    if hits:
        ids = "|".join(h["id"] for h in hits[:10])
        ent = http.get_json("https://www.wikidata.org/w/api.php?action=wbgetentities&format=json&props=claims"
                            f"&ids={q(ids)}", "wikidata") or {}
        for e in (ent.get("entities") or {}).values():
            claims = e.get("claims") or {}

            def val(prop):
                for c in claims.get(prop) or []:
                    v = ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")
                    if isinstance(v, str):
                        return v
                return ""
            mb, dg = val("P434"), val("P1953")
            if mb or dg:
                found.append((mb, dg))
    cache[key] = found[0] if len(found) == 1 else ("", "")
    return cache[key]


# --------------------------------------------------------------------------
# Artist IDs
# --------------------------------------------------------------------------
def _titles_match(album_title, titles):
    want = mc.fold(mc.search_title(album_title))
    return any(mc.title_sim(album_title, x) >= 0.85 or (want and want in mc.fold(x)) for x in titles if x)


def _year_of(date):
    return int(str(date)[:4]) if str(date or "")[:4].isdigit() else None


def mb_artist_id(http, name, cache, album_title="", year=None):
    """The MusicBrainz artist ID for a name, decided by the artist + album pair:

    1. the artist credited on a MusicBrainz album of this title (release group),
       of this year when several share the title;
    2. else the only MusicBrainz artist with that name (or alias);
    3. else, among same-name artists ("Rapture"), the one with an album of this
       title, then of this year.
    Empty when that still leaves more than one."""
    key = mc.fold(name)
    if not key:
        return ""
    ck = ("mb", key, album_title, year)
    if ck in cache:
        return cache[ck]

    def credited(groups):
        ids = set()
        for g in groups:
            for c in g.get("artist-credit") or []:
                a = c.get("artist") or {}
                names = [c.get("name", ""), a.get("name", ""), a.get("sort-name", "")]
                if a.get("id") and any(mc.fold(n) == key for n in names):
                    ids.add(a["id"])
        return ids

    found = ""
    if album_title:
        query = 'releasegroup:"%s" AND artist:"%s"' % (mc.search_title(album_title).replace('"', " "),
                                                        name.replace('"', " "))
        data = http.get_json(f"https://musicbrainz.org/ws/2/release-group/?query={q(query)}&fmt=json&limit=25",
                             "mb") or {}
        groups = [g for g in data.get("release-groups") or [] if _titles_match(album_title, [g.get("title")])]
        ids = credited(groups)
        if len(ids) > 1 and year:
            ids = credited([g for g in groups if _year_of(g.get("first-release-date")) == year]) or ids
        if len(ids) == 1:
            found = next(iter(ids))
    if not found:
        query = 'artist:"%s"' % name.replace('"', " ")
        data = http.get_json(f"https://musicbrainz.org/ws/2/artist/?query={q(query)}&fmt=json&limit=10", "mb") or {}
        exact = []
        for a in data.get("artists") or []:
            names = [a.get("name", ""), a.get("sort-name", "")] + [x.get("name", "") for x in a.get("aliases") or []]
            if any(mc.fold(n) == key for n in names):
                exact.append(a["id"])
        if len(exact) == 1:
            found = exact[0]
        elif len(exact) > 1 and album_title:
            fits = {}
            for aid in exact[:6]:
                rgs = http.get_json(f"https://musicbrainz.org/ws/2/release-group?artist={aid}&limit=100&fmt=json",
                                    "mb") or {}
                same = [g for g in rgs.get("release-groups") or [] if _titles_match(album_title, [g.get("title")])]
                if same:
                    fits[aid] = same
            if len(fits) > 1 and year:
                fits = {a: g for a, g in fits.items() if any(_year_of(x.get("first-release-date")) == year for x in g)}
            if len(fits) == 1:
                found = next(iter(fits))
    cache[ck] = found
    return found


def discogs_artist_id(http, name, cache, album_title="", token=""):
    """(Discogs artist ID, MusicBrainz ID linked to it) for a name MusicBrainz
    could not settle. Same-name Discogs artists ("Rapture (2)") are told apart
    by their releases. The MusicBrainz ID comes from MusicBrainz's own link to
    that Discogs artist page, as the Discogs Enrichment plugin finds it."""
    key = ("discogs", mc.fold(name), album_title)
    if key in cache:
        return cache[key]
    found = ("", "")
    pick = None
    if token and album_title:
        # the artist credited on a Discogs release of this album (artist + album pair)
        rel = http.get_json(f"https://api.discogs.com/database/search?type=release&artist={q(name)}"
                            f"&release_title={q(album_title)}&per_page=5", "discogs") or {}
        for r in (rel.get("results") or [])[:3]:
            det = http.get_json(f"https://api.discogs.com/releases/{r['id']}", "discogs") or {}
            hits = {str(a["id"]) for a in det.get("artists") or []
                    if a.get("id") and mc.fold(re.sub(r"\s*\(\d+\)$", "", a.get("name") or "")) == mc.fold(name)}
            if len(hits) == 1:
                pick = {"id": next(iter(hits))}
                break
    if token and not pick:
        data = http.get_json(f"https://api.discogs.com/database/search?type=artist&q={q(name)}&per_page=15",
                             "discogs") or {}
        exact = [r for r in data.get("results") or []
                 if mc.fold(re.sub(r"\s*\(\d+\)$", "", r.get("title") or "")) == mc.fold(name)]
        pick = exact[0] if len(exact) == 1 else None
        if len(exact) > 1 and album_title:
            fits = []
            for r in exact[:6]:
                rel = http.get_json(f"https://api.discogs.com/artists/{r['id']}/releases?per_page=100", "discogs") or {}
                if _titles_match(album_title, [x.get("title") for x in rel.get("releases") or []]):
                    fits.append(r)
            pick = fits[0] if len(fits) == 1 else None
    if pick:
        did = str(pick["id"])
        link = http.get_json("https://musicbrainz.org/ws/2/url?resource=" + q(f"https://www.discogs.com/artist/{did}")
                             + "&inc=artist-rels&fmt=json", "mb") or {}
        mbid = next((r.get("artist", {}).get("id", "") for r in link.get("relations") or []
                     if r.get("artist")), "")
        found = (did, mbid)
    cache[key] = found
    return found


def artist_ids(http, name, cache, album_title="", token="", year=None):
    """(MusicBrainz ID, Discogs ID) for one artist name, from the artist + album
    pair: MusicBrainz first, then Wikidata's record of the artist, then Discogs
    (whose artist page MusicBrainz may link to)."""
    mbid = mb_artist_id(http, name, cache, album_title, year)
    if mbid:
        return mbid, ""
    wmb, wdg = wikidata_artist_ids(http, name, cache)
    if wmb:
        return wmb, ""
    did, linked = discogs_artist_id(http, name, cache, album_title, token)
    return linked, did or wdg


_FEAT = re.compile(r"\s*[\(\[]?\s*(?:feat\.?|featuring|ft\.)\s+([^\)\]]+)[\)\]]?\s*$", re.I)


def track_artists(album_artist, track):
    """The artists of one track: its own credit, plus anyone 'feat.' in the title."""
    names = [n.strip() for n in re.split(r"\s*(?:,|&| and | x | with )\s*", track.get("artist") or album_artist)
             if n.strip()]
    m = _FEAT.search(track.get("title") or "")
    if m:
        names += [n.strip() for n in re.split(r"\s*(?:,|&| and )\s*", m.group(1)) if n.strip()]
    out = []
    for n in names or [album_artist]:
        if n and mc.fold(n) not in {mc.fold(x) for x in out}:
            out.append(n)
    return out


# --------------------------------------------------------------------------
# Finding and writing
# --------------------------------------------------------------------------
def cached_ocr(db, base, max_images=15):
    """The text the matcher already read from the album's scans (page and spines)."""
    try:
        _images, key = ma.list_images(base, max_images)
        rows = db.execute("SELECT key, value FROM ocr_cache WHERE key LIKE ?", (f"%:{key}",)).fetchall()
    except Exception:
        return ""
    return "\n".join(json.loads(v) for k, v in rows if k.startswith(("text", "spines")) and v)


_DM = None


def _plugin():
    """The Discogs Enrichment plugin's mapping code (credits, discs) when available."""
    global _DM
    if _DM is None:
        try:
            d = Path(ma.DEFAULT_PLUGIN_DIR).expanduser()
            sys.path.insert(0, str(d))
            import discogs_mapping
            _DM = discogs_mapping
        except Exception:
            _DM = False
    return _DM or None


def discogs_matched(http, release_id):
    """The Discogs release the matcher already proved, as a source to tag from
    (MusicBrainz has no release for it, so Picard cannot tag it)."""
    dm = _plugin()
    det = http.get_json(f"https://api.discogs.com/releases/{release_id}", "discogs")
    if not det or dm is None:
        return []
    c = ma.discogs_candidate(det, dm)
    c.update(url=det.get("uri") or f"https://www.discogs.com/release/{release_id}",
             date=str(det.get("released") or det.get("year") or ""),
             genre="; ".join((det.get("styles") or []) + (det.get("genres") or [])),
             cover=((det.get("images") or [{}])[0] or {}).get("uri", ""), discogs_release=str(release_id))
    for i, tr in enumerate(c["tracks"]):
        tr.setdefault("num", i + 1)
    return [c]


def find(http, album, cfg, ocr="", matched=None):
    """(source name, scored best) of the first source whose album passes the
    matcher's proof, else (None, best near miss or None)."""
    artist = mc.artist_parts(album["artist"])[0]
    title = mc.search_title(album["title"])
    near = None
    linked = [u for u in album.get("tag_urls") or [] if ".bandcamp.com/album/" in u]
    sources = list(SOURCES)
    best = (matched or {}).get("best") or {}
    if (matched or {}).get("status") == "auto" and best.get("source") == "discogs":
        sources.insert(0, ("Discogs", lambda http, a, t: discogs_matched(http, best["id"])))
    if linked:                                    # the files' own tags name their Bandcamp page
        sources.insert(0, ("Bandcamp (linked in the tags)",
                           lambda http, a, t: [c for c in (bandcamp_album(http, u) for u in linked[:2]) if c]))
    for name, fetch in sources:
        try:
            cands = fetch(http, artist, title)
        except Exception as exc:                               # one source down: try the next
            print(f"    {name} lookup failed: {exc}", file=sys.stderr)
            continue
        if not cands:
            continue
        scored = [mc.score_candidate(album, c, cfg, ocr) for c in cands]
        res = mc.classify(scored, cfg)
        if res["status"] == "auto":
            return name, res["best"], res["why"]
        if res.get("best") and (near is None or res["best"]["confidence"] > near[1]["confidence"]):
            near = (name, res["best"], res["why"])
    return None, (near[1] if near else None), (near[2] if near else [])


def tags_for(album, cand, http, artist_cache, source_name, token=""):
    """The tags to write to each file, in file order."""
    alb_artists = [a.strip() for a in re.split(r"\s*(?:,|&| and )\s*", cand["artist"]) if a.strip()] or [cand["artist"]]
    alb = [artist_ids(http, a, artist_cache, cand["title"], token, cand.get("year")) for a in alb_artists]
    alb_ids = [m for m, _d in alb]
    total_discs = max([t.get("disc") or 1 for t in cand["tracks"]] or [1])
    per_disc = {}
    for t in cand["tracks"]:
        per_disc[t.get("disc") or 1] = per_disc.get(t.get("disc") or 1, 0) + 1
    common = {
        "album": cand["title"], "albumartist": cand["artist"], "date": cand["date"] or (str(cand["year"] or "")),
        "label": cand["label"], "barcode": next(iter(cand["barcodes"]), ""), "genre": cand.get("genre", ""),
        "totaldiscs": str(total_discs), "media": "Digital Media",
        "musicbrainz_albumartistid": [i for i in alb_ids if i],
        "discogs_albumartist_id": [d for _m, d in alb if d],
        "tagmender": "Resolved",
        "tagmender_reason": (f"not on MusicBrainz; tags from the Discogs release" if source_name == "Discogs"
                             else f"web_fallback_only: not on MusicBrainz or Discogs; tags from {source_name}"),
        "discogs_release_id": cand.get("discogs_release", ""),
        "tagmender_source": cand["url"],
    }
    out = []
    for t, c in zip(album["tracks"], cand["tracks"]):
        names = track_artists(cand["artist"], c)
        disc = c.get("disc") or 1
        ids = [artist_ids(http, n, artist_cache, cand["title"], token, cand.get("year")) for n in names]
        out.append(dict(common, title=c["title"], artist=" & ".join(names) if len(names) > 1 else names[0],
                        artists=names, musicbrainz_artistid=[m for m, _d in ids if m],
                        discogs_artist_id=[d for _m, d in ids if d],
                        tracknumber=str(c.get("num") or ""), totaltracks=str(per_disc.get(disc, len(cand["tracks"]))),
                        discnumber=str(disc)))
    return out


# Picard's names for these tags in each format
_ID3 = {"album": "TALB", "albumartist": "TPE2", "artist": "TPE1", "title": "TIT2", "date": "TDRC",
        "label": "TPUB", "genre": "TCON", "media": "TMED"}
_ID3_TXXX = {"barcode": "BARCODE", "artists": "ARTISTS", "musicbrainz_artistid": "MusicBrainz Artist Id",
             "discogs_artist_id": "DISCOGS_ARTIST_ID", "discogs_albumartist_id": "DISCOGS_ALBUMARTIST_ID",
             "discogs_release_id": "DISCOGS_RELEASE_ID",
             "musicbrainz_albumartistid": "MusicBrainz Album Artist Id", "tagmender": "TAGMENDER",
             "tagmender_reason": "TAGMENDER_REASON", "tagmender_source": "TAGMENDER_SOURCE"}
_MP4 = {"album": "\xa9alb", "albumartist": "aART", "artist": "\xa9ART", "title": "\xa9nam", "date": "\xa9day",
        "genre": "\xa9gen"}
_MP4_FREE = {"label": "LABEL", "barcode": "BARCODE", "media": "MEDIA", "artists": "ARTISTS",
             "discogs_artist_id": "DISCOGS_ARTIST_ID", "discogs_albumartist_id": "DISCOGS_ALBUMARTIST_ID",
             "discogs_release_id": "DISCOGS_RELEASE_ID",
             "musicbrainz_artistid": "MusicBrainz Artist Id",
             "musicbrainz_albumartistid": "MusicBrainz Album Artist Id", "tagmender": "TAGMENDER",
             "tagmender_reason": "TAGMENDER_REASON", "tagmender_source": "TAGMENDER_SOURCE"}


def write_tags(path, tags):
    import mutagen
    from mutagen.id3 import ID3, TXXX, TRCK, TPOS, Frames
    from mutagen.mp4 import MP4, MP4FreeForm
    path = Path(path)
    f = mutagen.File(path)
    if f is None:
        raise ValueError("not an audio file mutagen can write")
    vals = {k: ([str(x) for x in v] if isinstance(v, list) else [str(v)]) for k, v in tags.items()}
    vals = {k: v for k, v in vals.items() if any(x for x in v)}
    if isinstance(f, MP4):
        if f.tags is None:
            f.add_tags()
        for k, key in _MP4.items():
            if k in vals:
                f.tags[key] = vals[k]
        for k, name in _MP4_FREE.items():
            if k in vals:
                f.tags[f"----:com.apple.iTunes:{name}"] = [MP4FreeForm(x.encode()) for x in vals[k]]
        if vals.get("tracknumber", [""])[0].isdigit():
            f.tags["trkn"] = [(int(vals["tracknumber"][0]), int((vals.get("totaltracks") or ["0"])[0] or 0))]
        if vals.get("discnumber", [""])[0].isdigit():
            f.tags["disk"] = [(int(vals["discnumber"][0]), int((vals.get("totaldiscs") or ["0"])[0] or 0))]
    elif (f.tags is not None and isinstance(f.tags, ID3)) or path.suffix.lower() == ".mp3":
        if f.tags is None:
            f.add_tags()
        for k, fid in _ID3.items():
            if k in vals:
                f.tags.setall(fid, [Frames[fid](encoding=3, text=vals[k])])
        for k, desc in _ID3_TXXX.items():
            if k in vals:
                f.tags.delall(f"TXXX:{desc}")
                f.tags.add(TXXX(encoding=3, desc=desc, text=vals[k]))
        if "tracknumber" in vals:
            f.tags.setall("TRCK", [TRCK(encoding=3, text=[f"{vals['tracknumber'][0]}/{vals.get('totaltracks', [''])[0]}"
                                                          .rstrip("/")])])
        if "discnumber" in vals:
            f.tags.setall("TPOS", [TPOS(encoding=3, text=[f"{vals['discnumber'][0]}/{vals.get('totaldiscs', [''])[0]}"
                                                          .rstrip("/")])])
    else:
        if f.tags is None:
            f.add_tags()
        for k, v in vals.items():
            f.tags[k.upper()] = v
    f.save()


_UNSAFE = re.compile(r'[\\/:*?"<>|]')


def _safe(name):
    """A file or folder name as Picard writes it with Windows compatibility on."""
    return _UNSAFE.sub("_", str(name or "")).strip().rstrip(".") or "_"


def _five_words(title):
    return re.sub(r"^((?:\S+\s+){4}\S+)\s.*$", r"\1", str(title or "").strip())


def target_folder(base, cand, library=None):
    """Where Picard's naming script would file the album: <library>/<album
    artists A to Z, joined by &>/<year> - <album, at most 5 words>."""
    library = Path(library) if library else Path(base).parent.parent
    artists = sorted({a.strip() for a in re.split(r"\s*(?:,|&| and )\s*", cand["artist"]) if a.strip()},
                     key=str.casefold) or [cand["artist"]]
    year = str(cand.get("date") or cand.get("year") or "")[:4]
    album = _five_words(cand["title"])
    dest = library / _safe(" & ".join(artists)) / _safe(f"{year} - {album}" if year else album)
    if dest.exists() and dest.resolve() != Path(base).resolve():
        return Path(base)                      # never merge into another album's folder
    return dest


def file_name(tags, suffix):
    """'01.ITKWEDTSIM.flac', or 'Disc 02/203.X.flac' on multi-disc albums, as the naming script does."""
    initials = "".join(w[0] for w in str(tags.get("title") or "").split() if w[:1].isalpha()).upper()
    num = int(tags.get("tracknumber") or 0)
    disc = int(tags.get("discnumber") or 1)
    if int(tags.get("totaldiscs") or 1) > 1:
        return f"Disc {disc:02d}/{disc * 100 + num:03d}.{_safe(initials)}{suffix}"
    return f"{num:02d}.{_safe(initials)}{suffix}"


def save_cover(album_dir, url, http):
    """cover.jpg from the source, only when the folder has no image at all."""
    if not url or any(p.suffix.lower() in ma.IMAGE_EXT for p in Path(album_dir).rglob("*")):
        return None
    import urllib.request
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}),
                                    timeout=60) as r:
            data = r.read()
    except Exception:
        return None
    if not data[:3] == b"\xff\xd8\xff":
        return None
    dest = Path(album_dir) / "cover.jpg"
    dest.write_bytes(data)
    return str(dest)


def undo(out_dir, run_id):
    """Restore every file's tags; remove the cover.jpg files this run downloaded."""
    journal = json.loads((out_dir / f"run-{run_id}" / "journal.json").read_text())
    for entry in journal:
        for created in entry.get("created", []):
            Path(created).unlink(missing_ok=True)
    return ta.undo(out_dir, run_id)


def unresolved(matches_db, only=None, include_review=False, include_discogs=True):
    """Albums Picard cannot tag: no match, review (if asked), and confident
    matches found on Discogs only (MusicBrainz has no release for Picard to use)."""
    db = sqlite3.connect(Path(matches_db).expanduser())
    out = []
    wanted = {"nomatch"} | ({"review"} if include_review else set())
    for path, blob in db.execute("SELECT path, result FROM results ORDER BY path"):
        o = json.loads(blob)
        discogs_only = (include_discogs and o.get("status") == "auto"
                        and (o.get("best") or {}).get("source") == "discogs"
                        and not (o.get("best") or {}).get("mb_id"))   # else Picard tags its MB twin
        if ((o.get("status") in wanted or discogs_only) and Path(path).is_dir()
                and (not only or only.casefold() in path.casefold())):
            out.append(o)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matches-db", default="~/Downloads/matching/match_state.db")
    ap.add_argument("--out-dir", default="~/Downloads/tagging")
    ap.add_argument("--include-review", action="store_true",
                    help="also albums left for review (not only those with no match)")
    ap.add_argument("--only", help="only folders whose path contains this text")
    ap.add_argument("--apply", action="store_true", help="write the tags (default: dry run)")
    ap.add_argument("--process-anyway", action="store_true",
                    help="also albums an Ignore/Skip rule excludes (this run only)")
    ap.add_argument("--undo", help="restore a run")
    ap.add_argument("--no-cover", action="store_true", help="do not save cover.jpg into folders without images")
    ap.add_argument("--library", help="library folder to file albums into (default: the one they are in)")
    ap.add_argument("--no-discogs", action="store_true",
                    help="skip albums matched on Discogs only (by default they are tagged from that release)")
    ap.add_argument("--keep-folders", action="store_true",
                    help="tag in place; do not file albums under their artist like Picard does")
    args = ap.parse_args(argv)
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.undo:
        return undo(out_dir, args.undo)

    cfg = dict(mc.DEFAULT_CFG)
    token = ma._discogs_token()
    db = sqlite3.connect(Path(args.matches_db).expanduser(), timeout=60)
    http = ma.Http(db, cfg, "")
    ma.USER_AGENT = USER_AGENT
    albums = unresolved(args.matches_db, args.only, args.include_review, not args.no_discogs)
    rules = exclusions.load_rules()
    print(f"{len(albums)} album(s) without a MusicBrainz/Discogs match")
    run_dir = blob_dir = None
    journal, report, cache = [], [], {}
    if args.apply:
        run_id = time.strftime("%Y%m%d-%H%M%S")
        run_dir, n = out_dir / f"run-web-{run_id}", 1
        while run_dir.exists():
            n += 1
            run_dir = out_dir / f"run-web-{run_id}-{n}"
        blob_dir = run_dir / "blobs"
        blob_dir.mkdir(parents=True)
    for o in albums:
        base = Path(o["path"])
        pre = exclusions.gate(base, "web", rules, process_anyway=args.process_anyway)
        if pre.all_excluded:
            print(f"  ×   {base.parent.name} | {base.name}: {pre.summary().reason()}")
            report.append({"album": o["path"], "source": "", "page": "", "matched": "", "confidence": "",
                           "result": pre.summary().reason()})
            continue
        album = ma.read_album(base, o.get("artist_folder") or base.parent.name, o.get("album_folder") or base.name,
                              exclude=pre.excluded)
        if not album["tracks"]:
            continue
        source, best, why = find(http, album, cfg, cached_ocr(db, base), o)
        row = {"album": o["path"], "source": source or "", "page": (best or {}).get("cand", {}).get("url", ""),
               "matched": f"{best['cand']['artist']} - {best['cand']['title']} ({best['cand']['year']})" if best else "",
               "confidence": best["confidence"] if best else "", "result": ""}
        if not source:
            row["result"] = "not found" if not best else "near miss: " + "; ".join(why)[:200]
            print(f"  -   {base.parent.name} | {base.name}: {row['result'][:100]}")
            if args.apply:                         # say so in the files, for whoever looks next
                reason = ("not on MusicBrainz, Discogs, Deezer, iTunes or Bandcamp" if not best else
                          f"not on MusicBrainz or Discogs; {row['source'] or 'web'} near miss "
                          f"{row['page']}: {'; '.join(why)}")[:250]
                files = [Path(t["path"]) for t in album["tracks"]]
                recs = [{"path": str(p), "new_path": str(p), "key": "", "backup": ta.backup_tags(p, blob_dir)}
                        for p in files]
                journal.append({"album": str(base), "mbid": None, "files": recs, "extras": [], "moves": []})
                with open(run_dir / "journal.json", "w") as fh:
                    json.dump(journal, fh)
                for p in files:
                    ta.write_tagmender(p, "Unresolved", reason)
            report.append(row)
            continue
        c = best["cand"]
        dec = exclusions.gate([Path(t["path"]) for t in album["tracks"]], "web", rules,
                              release={"album": [c.get("title") or ""], "albumartist": [c.get("artist") or ""],
                                       "artist": [c.get("artist") or ""], "genre": [c.get("genre") or ""]},
                              process_anyway=args.process_anyway)
        if dec.excluded:                                 # what the web found is excluded: no writes at all
            whole = dec.summary() or next(iter(dec.excluded.values()))
            row["result"] = whole.reason()
            print(f"  ×   {base.parent.name} | {base.name}: {whole.reason()}")
            report.append(row)
            continue
        tags = tags_for(album, best["cand"], http, cache, source, token)
        dest = target_folder(base, best["cand"], args.library) if not args.keep_folders else base
        row["result"] = "would tag" if not args.apply else "tagged"
        if dest != base:
            row["result"] += f", move to {dest}"
        print(f"  {'+' if args.apply else '?'}   {base.parent.name} | {base.name}  <- {source}: {row['matched']}"
              + (f"\n        -> {dest}" if dest != base else ""))
        if args.apply:
            files = [Path(t["path"]) for t in album["tracks"]]
            recs = [{"path": str(p), "new_path": str(p), "key": "", "backup": ta.backup_tags(p, blob_dir)}
                    for p in files]
            entry = {"album": str(base), "mbid": None, "files": recs, "extras": [], "moves": []}
            journal.append(entry)
            with open(run_dir / "journal.json", "w") as fh:
                json.dump(journal, fh)
            for p, tg in zip(files, tags):
                write_tags(p, tg)
            if dest != base:                                   # filed like Picard files it
                entry["extras"] = ta.extra_files(base)
                try:
                    for rec, tg in zip(recs, tags):
                        new = dest / file_name(tg, Path(rec["path"]).suffix)
                        new.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(rec["path"], new)
                        rec["new_path"] = str(new)
                        with open(run_dir / "journal.json", "w") as fh:
                            json.dump(journal, fh)
                    entry["moves"].extend(ta.tidy_artwork(str(base), str(dest), entry["extras"]))
                    ta._remove_empty(base)
                except OSError as exc:
                    print(f"    could not move the files: {exc}")
                with open(run_dir / "journal.json", "w") as fh:
                    json.dump(journal, fh)
            if not args.no_cover:
                cover = save_cover(dest if Path(dest).is_dir() else base, best["cand"].get("cover"), http)
                if cover:
                    entry["created"] = [cover]
                    with open(run_dir / "journal.json", "w") as fh:
                        json.dump(journal, fh)
        report.append(row)
    path = (run_dir or out_dir) / "web_tag_plan.csv" if not args.apply else run_dir / "results.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["album", "source", "page", "matched", "confidence", "result"])
        w.writeheader()
        w.writerows(report)
    found = sum(1 for r in report if r["source"])
    print(f"\n{found} of {len(report)} found on the web. Report: {path}")
    if args.apply:
        print(f"Undo: python3 web_tag.py --undo {run_dir.name[len('run-'):]}")
        if found:
            ta._navidrome()
    else:
        print("Dry run, nothing changed. Run again with --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
