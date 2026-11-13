#!/usr/bin/env python3
"""web_tag.py - the last resort for albums MusicBrainz and Discogs do not have.

Searches the web for the album, accepts a result only on the same proof the
matcher asks of MusicBrainz and Discogs (artist, title, track count and track
lengths), and writes the essential tags from it:

  album, album artist, artist and contributing artists per track, track titles,
  track and disc numbers, date, label, genre, and the MusicBrainz artist IDs of
  the artists when MusicBrainz knows them without doubt; the barcode and media
  only when they are true of your copy (a shop's UPC and "Digital Media" are not
  written to a CD or vinyl rip unless the album's own barcode agrees);
  TAGMENDER=Resolved, TAGMENDER_REASON (where the tags came from) and
  TAGMENDER_SOURCE (the page).

With --apply, an album no source proves keeps its tags and gets only
TAGMENDER=Unresolved with TAGMENDER_REASON (why, e.g. the near miss). An album
whose pressings tie (album only: the album known, the pressing not) gets the
album's tags only and TAGMENDER=Partial.

A Discogs pressing the matcher proved (or you approved) that MusicBrainz does not
have is tagged from that release only, never from a shop's listing of the album.

Sources, in order: Deezer and Apple iTunes (public catalogue APIs, no key),
Bandcamp album pages found by Bandcamp's own search (the album data they publish
for search engines), and Wikipedia. No search engine's page is scraped.

Dry run by default; --apply writes; --undo <run> restores every file exactly.

  python3 web_tag.py --matches-db ~/Downloads/matching_music/match_state.db
  python3 web_tag.py --matches-db ~/Downloads/matching_music/match_state.db --apply
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
import levels as _levels  # noqa: E402
import reasons  # noqa: E402

from version import VERSION as _VERSION  # noqa: E402
USER_AGENT = f"TagMender/{_VERSION} ( https://github.com/herculepirate/tagmender )"   # MusicBrainz asks for app/version ( contact )
q = urllib.parse.quote


# --------------------------------------------------------------------------
# Sources. Each returns candidates in the matcher's form, so the matcher's own
# scoring decides; "page" is the address shown in TAGMENDER_SOURCE.
# --------------------------------------------------------------------------
def _cand(source, cid, url, artist, title, date, label="", barcode="", genre="", tracks=(), cover="",
          digital=False):
    """Every web candidate is scored as a digital release (formats), as it always was.
    digital: the source sells or streams only the download (Deezer, iTunes, a Bandcamp
    album page), so "Digital Media" and its UPC may be written; an encyclopedia article
    is not about one format, so it names neither (digital_only stays False)."""
    year = int(str(date)[:4]) if str(date or "")[:4].isdigit() else None
    discs = max([t.get("disc") or 1 for t in tracks] or [1])
    return {"source": source, "id": str(cid), "url": url, "artist": artist or "", "artist_alt": "",
            "title": title or "", "year": year, "date": str(date or ""), "country": "", "label": label or "",
            "catnos": set(), "barcodes": {mc.norm_code(barcode)} if barcode else set(), "disc_count": discs,
            "tracks": list(tracks), "formats": {"digital"}, "digital_only": bool(digital),
            "genre": genre_list(genre), "cover": cover or ""}


def _count_off(n_tracks, count):
    """True when a hit's track count is known and differs from the files' by more than 3
    (engine._established refuses such an album, so its details need not be fetched)."""
    try:
        count = int(count or 0)
    except (TypeError, ValueError):
        return False
    return bool(n_tracks) and count > 0 and abs(count - n_tracks) > 3


def deezer(http, artist, title, n_tracks=None):
    query = 'artist:"%s" album:"%s"' % (artist, title)
    data = http.get_json(f"https://api.deezer.com/search/album?q={q(query)}&limit=10", "deezer")
    hits = (data or {}).get("data") or []
    if not hits:
        data = http.get_json(f"https://api.deezer.com/search/album?q={q(artist + ' ' + title)}&limit=10", "deezer")
        hits = (data or {}).get("data") or []
    out = []
    for h in hits[:4]:
        if _count_off(n_tracks, h.get("nb_tracks")):
            continue
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
                  al.get("label"), al.get("upc"), genres, tracks, al.get("cover_xl"), digital=True)
        c["contributors"] = [x.get("name") for x in al.get("contributors") or [] if x.get("name")]
        out.append(c)
    return out


def itunes(http, artist, title, n_tracks=None):
    data = http.get_json(f"https://itunes.apple.com/search?term={q(artist + ' ' + title)}&entity=album&limit=10",
                         "itunes")
    out = []
    for h in ((data or {}).get("results") or [])[:4]:
        cid = h.get("collectionId")
        if not cid or _count_off(n_tracks, h.get("trackCount")):
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
                         tracks, art, digital=True))
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
        genre = [str(k) for k in d.get("keywords") or []] if isinstance(d.get("keywords"), list) else []
        image = d.get("image")
        image = image[0] if isinstance(image, list) and image else image
        return _cand("bandcamp", url, url, (d.get("byArtist") or {}).get("name"), d.get("name"),
                     str(d.get("datePublished") or "")[:30], pub.get("name") if isinstance(pub, dict) else "",
                     "", genre, tracks, image or "", digital=True)
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


def _wiki_fields(listing):
    """WEB-3: the titleN/lengthN fields of a {{Track listing}} as [(key, number, value)]: a field
    runs to the next "|" or line end outside [[links]] and {{templates}}, nested ones too
    ("{{nowrap|{{lang|fr|Chanson}} d'amour}}")."""
    parts, depth, cur, i = [], 0, [], 0
    while i < len(listing):
        two = listing[i:i + 2]
        if two in ("{{", "[["):
            depth += 1
            cur.append(two)
            i += 2
            continue
        if two in ("}}", "]]") and depth:
            depth -= 1
            cur.append(two)
            i += 2
            continue
        c = listing[i]
        if depth == 0 and c in "|\n":
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    parts.append("".join(cur))
    out = []
    for part in parts:
        m = re.match(r"\s*(title|length)(\d+)\s*=(.*)\Z", part, re.S | re.I)
        if m:
            out.append((m.group(1), m.group(2), m.group(3).strip()))
    return out


# notes and citations: gone with their text. "r" is the template named exactly "r" ({{r|a}}),
# never {{ruby|...}}
_NOTE_TEMPLATES = re.compile(r"^\s*(?:efn|refn|ref|sfnp?|rp?(?=\s*(?:\||$))|cite\b|citation|note|nb|#tag)", re.I)
# templates shown as their first unnamed part: {{abbr|No.|Number}} "No.", {{nihongo|English|日本|
# romaji}} "English", {{ruby|雪|ゆき}} "雪"; the others ({{nowrap|X}}, {{lang|fr|Titre}}) as their last
_FIRST_ARG_TEMPLATES = {"abbr", "abbrlink", "nihongo", "nihongo2", "nihongo3", "nihongo foot", "ruby", "ruby-ja",
                        "tooltip", "hover title", "h:title"}


def _unwrap_templates(v):
    """WEB-3: {{nowrap|X}}, {{lang|fr|Titre}}, {{sup|b}} keep their text (the last unnamed part;
    {{abbr|No.|Number}}, {{nihongo|...}}, {{ruby|...}} the first); notes and citations
    ({{efn|...}}, {{cite web|...}}, {{r|...}}) go. Innermost first."""
    def one(m):
        parts = m.group(1).split("|")
        if _NOTE_TEMPLATES.match(parts[0]):
            return ""
        args = [x for x in parts[1:] if "=" not in x]
        if not args:
            return ""
        return args[0] if " ".join(parts[0].split()).casefold() in _FIRST_ARG_TEMPLATES else args[-1]
    for _ in range(5):
        v2 = re.sub(r"\{\{([^{}]*)\}\}", one, v)
        if v2 == v:
            break
        v = v2
    return v


def _wiki_blocks(text):
    """WEB-3: the {{Track listing}} blocks of an article as [(first number, [(num, title, secs)])]."""
    out = []
    for listing in re.findall(r"\{\{\s*Track ?listing(.*?)\n\}\}", text, re.S | re.I):
        rows = {}
        for k, n, v in _wiki_fields(listing):
            v = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>|<!--.*?-->", "", v, flags=re.S)
            v = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", v)       # (a link: its text)
            v = _unwrap_templates(v)
            rows.setdefault(int(n), {})[k.lower()] = v.strip()
        tracks = []
        for n in sorted(rows):
            ln = rows[n].get("length", "")
            m = re.fullmatch(r"(?:(\d+):)?(\d+):(\d{2})", ln)
            secs = (int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))) if m else 0
            tracks.append((n, rows[n].get("title", "").strip('"').strip(), float(secs)))
        if tracks:
            head = re.search(r"\|\s*headline\s*=\s*([^\n]*)", listing, re.I)
            out.append((tracks[0][0], tracks, head.group(1) if head else ""))
    return out


# a section that is another edition's, by its headline ("Digital edition bonus tracks", "Japanese
# bonus track", "Deluxe edition", "EMP & Target exclusive CD"); "Side two", "Part II" run on
_EDITION_HEAD = re.compile(r"bonus|edition|deluxe|reissue|remaster|exclusive|digital|japan|limited|"
                           r"special|expanded|anniversary|itunes|amazon|target|walmart|vinyl\b|cd\b|lp\b", re.I)


# a block numbered from 1 that is an edition's extra disc, by its headline ("Deluxe edition bonus
# disc", "Bonus disc", "2010 reissue"); never a medium's own name ("CD 2", "Disc two")
_EXTRA_DISC_HEAD = re.compile(r"bonus|edition|deluxe|reissue|expanded|anniversary|limited|special|exclusive", re.I)
# an LP side's headline: "Side one", "Side B", "Side 2" - even sides run on from the side before
_SIDE_HEAD = re.compile(r"^\W*side\s+(one|two|three|four|five|six|seven|eight|[a-h]|[1-8])\b", re.I)
_SIDES = {w: k for k, w in enumerate("one two three four five six seven eight".split(), 1)} | \
    {c: k for k, c in enumerate("abcdefgh", 1)} | {str(k): k for k in range(1, 9)}


def _link_text(s):
    return re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", s or "")


def _wiki_editions(blocks):
    """WEB-3: the editions an article's blocks make, each [(disc, num, title, secs)]. A block
    numbered from 1 is a disc of its own ("Disc one", "Disc two") - unless it is an LP's even
    side ("Side two" numbered from 1 again: the disc runs on, numbered on), or its headline names
    an edition ("Deluxe edition bonus disc"): that disc is another edition's, so the standard
    edition without it is a candidate too. A block numbered on from where a disc ends continues
    it ("Side two" 6-10, a section without a headline) - unless its headline names an edition, or
    another section already took those numbers: then it is that edition's bonus tracks on that
    disc (the disc whose numbers it continues: "Disc one bonus tracks" after disc two), its own
    candidate ("Digital edition bonus tracks", "EMP & Target exclusive CD bonus tracks", both
    13-14), never stacked on the others; sections running on from one another (13-14 then 15-16)
    together."""
    discs, sections = [], []                     # discs: [tracks, extra]; sections: (disc index, tracks)

    def end(k):
        return max(n for n, _t, _s in discs[k][0])
    for block in blocks:
        first, tracks = block[0], list(block[1])
        head = _link_text(block[2] if len(block) > 2 else "")
        side = _SIDE_HEAD.match(head)
        if first == 1 and discs and side and _SIDES[side.group(1).casefold()] % 2 == 0:
            e = end(len(discs) - 1)
            discs[-1][0].extend((n + e, t, s) for n, t, s in tracks)     # ("Side two" from 1: runs on)
            continue
        if first == 1 or not discs:
            extra = bool(discs) and (bool(_EXTRA_DISC_HEAD.search(head)) or discs[-1][1])
            discs.append([tracks, extra])
            continue
        at = next((k for k in range(len(discs) - 1, -1, -1) if first == end(k) + 1), None)
        if at == len(discs) - 1 and not _EDITION_HEAD.search(head) and not sections:
            discs[-1][0].extend(tracks)                  # ("Side two": the album runs on)
        else:
            sections.append((at, tracks))
    if not discs:
        return []
    order = [k for k in range(len(discs)) if not discs[k][1]] + [k for k in range(len(discs)) if discs[k][1]]
    number = {k: d for d, k in enumerate(order, 1)}           # (an edition's extra discs after the others)

    def lay(ks):
        return [(number[k], n, t, s) for k in ks for n, t, s in discs[k][0]]
    base = lay([k for k in order if not discs[k][1]])
    whole = lay(order)

    def end_of(edition, disc):
        return max((n for d, n, _t, _s in edition if d == disc), default=0)
    editions, seen = [], set()

    def add(edition):
        edition = sorted(edition, key=lambda x: (x[0], x[1]))
        if edition and tuple(edition) not in seen:     # (two sections of the same tracks: one)
            seen.add(tuple(edition))
            editions.append(edition)
    add(base)
    if whole != base:
        add(whole)                                       # (with the edition's extra disc)
    prev = prev_disc = None
    for at, tracks in sections:
        if prev is not None and tracks[0][0] == end_of(prev, prev_disc) + 1:
            prev = prev + [(prev_disc, n, t, s) for n, t, s in tracks]   # (runs on from the section before it)
        elif at is not None:
            prev_disc = number[at]
            prev = (whole if discs[at][1] else base) + [(prev_disc, n, t, s) for n, t, s in tracks]
        else:
            continue                                     # (numbered otherwise: not placed)
        add(prev)
    return editions


def wikipedia_album(http, page_title):
    """An album article: its infobox and its standard edition's track listing (titles and
    lengths); None without an album infobox."""
    eds = wikipedia_editions(http, page_title)
    return eds[0] if eds else None


def wikipedia_editions(http, page_title):
    """An album article's editions (WEB-3): its infobox and each edition's track listing (titles
    and lengths), one candidate per edition."""
    data = http.get_json("https://en.wikipedia.org/w/api.php?action=parse&prop=wikitext&format=json&redirects=1"
                         f"&page={q(page_title)}", "wikipedia") or {}
    text = ((data.get("parse") or {}).get("wikitext") or {}).get("*", "")
    box = re.search(r"\{\{\s*Infobox album(.*?)\n\}\}", text, re.S | re.I)
    if not box:
        return []
    box = box.group(0)
    name = _wiki_value(box, "name") or page_title
    released = _wiki_value(box, "released")
    m = re.search(r"\d{4}-\d{2}-\d{2}|\d{4}", released)
    url = "https://en.wikipedia.org/wiki/" + q(((data.get("parse") or {}).get("title") or page_title).replace(" ", "_"))
    out = []
    for k, edition in enumerate(_wiki_editions(_wiki_blocks(text))):
        tracks = [{"disc": d, "num": n, "title": t, "secs": s, "artist": ""} for d, n, t, s in edition]
        out.append(_cand("wikipedia", url + (f"#edition-{k}" if k else ""), url, _wiki_value(box, "artist"), name,
                         m.group(0) if m else "", _wiki_value(box, "label").split(",")[0].strip(), "",
                         [g for g in re.split(r"\s*[,;\u2022\n]\s*", _wiki_value(box, "genre")) if g], tracks))
    return out


def wikipedia(http, artist, title):
    data = http.get_json("https://en.wikipedia.org/w/api.php?action=query&list=search&format=json&srlimit=5"
                         f"&srsearch={q(f'{title} {artist} album')}", "wikipedia") or {}
    out = []
    for hit in ((data.get("query") or {}).get("search") or [])[:3]:
        out += [c for c in wikipedia_editions(http, hit.get("title", "")) if c and c["tracks"]]
    return out


SOURCES = (("Deezer", deezer), ("iTunes", itunes), ("Bandcamp", bandcamp), ("Wikipedia", wikipedia))
_COUNTED = (deezer, itunes)                    # the sources that can skip hits by track count


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


def _each_once(names) -> list:
    """names with each artist once ("A & A" is one artist credited twice)."""
    out = []
    for n in names:
        if mc.fold(n) not in {mc.fold(x) for x in out}:
            out.append(n)
    return out


_CREDIT_SPLIT = r"\s*(?:,|&| and )\s*"


def album_artists(http, cand, cache) -> list:
    """The album's artists from the web credit: "A & B" is two artists, but a band whose
    name holds the separators ("Emerson, Lake & Palmer") is one - when MusicBrainz knows the
    whole credit as one artist of this album, it is not split. Kept in cand["album_artists"]."""
    credit = cand.get("artist") or ""
    parts = _each_once([a.strip() for a in re.split(_CREDIT_SPLIT, credit) if a.strip()])
    if len(parts) > 1 and http is not None and mb_artist_id(http, credit, cache, cand.get("title") or "",
                                                            cand.get("year")):
        parts = [credit]
    cand["album_artists"] = parts or [credit]
    return cand["album_artists"]


def genre_list(value) -> list:
    """Genres as separate values, whatever the source gave: a list, or text joined with
    "; " (as older candidates kept them)."""
    vals = value if isinstance(value, (list, tuple)) else str(value or "").split(";")
    out = []
    for v in vals:
        v = str(v or "").strip()
        if v and v.casefold() not in {x.casefold() for x in out}:
            out.append(v)
    return out


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
        images, _key = ma.list_images(base, max_images)
        # only the readings made the current way (OCR-3: page_kind): older ones ("text:",
        # "text-eng+rus+jpn:", greyscale, every pack at once) are not this album's text
        kinds = (ma.page_kind("text"), ma.page_kind("spines"))
        rows = []
        for p in images:                              # cached per scan (match_albums.each_scan)
            rows += db.execute(f"SELECT key, value FROM ocr_cache WHERE key IN ({','.join('?' * len(kinds))})",
                               [f"{k}:{ma.scan_key(p)}" for k in kinds]).fetchall()
    except Exception:
        return ""
    texts = (json.loads(v) for k, v in rows if v)
    return "\n".join(t for t in texts if isinstance(t, str) and t)


def album_text(db, base):
    """The album's own printed text, as the matcher compares it: the documents in the
    folder (notes, rip logs, cue sheets, PDF text) and what it read from the scans."""
    try:
        docs = ma.documents_text(base, ocr=False, db=db)
    except Exception:
        docs = ""
    return (docs + "\n" + cached_ocr(db, base)).strip()


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


def discogs_media(det):
    """The release's format as Picard writes MEDIA: "CD", '12" Vinyl', "Cassette",
    "Digital Media" (a Discogs "File"); the first format of a release that is a medium names
    it (never its "Box Set" or "All Media" entry)."""
    fmts = [f for f in det.get("formats") or [] if str((f or {}).get("name") or "").strip().casefold()
            not in ("box set", "all media")]
    fmt = (fmts or [{}])[0] or {}
    name = str(fmt.get("name") or "")
    if name == "File":
        return "Digital Media"
    if name == "Vinyl":
        size = next((d for d in fmt.get("descriptions") or [] if re.fullmatch(r'\d+"', d)), "")
        return f"{size} Vinyl".strip()
    return name or "Digital Media"


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
             genre=genre_list((det.get("styles") or []) + (det.get("genres") or [])),
             cover=((det.get("images") or [{}])[0] or {}).get("uri", ""), discogs_release=str(release_id),
             media=discogs_media(det), catno_text="; ".join(dict.fromkeys(
                 str(lb.get("catno") or "").strip() for lb in det.get("labels") or []
                 if str(lb.get("catno") or "").strip().lower() not in ("", "none"))))
    for i, tr in enumerate(c["tracks"]):
        tr.setdefault("num", i + 1)
    return [c]


def pressing_matched(http, source, release_id):
    """One release the matcher proved the album by (MusicBrainz or Discogs), as a source."""
    if source == "discogs":
        return discogs_matched(http, release_id)
    rel = http.get_json(f"https://musicbrainz.org/ws/2/release/{release_id}"
                        f"?inc=recordings+artist-credits+labels+url-rels&fmt=json", "mb")
    if not rel:
        return []
    c = ma.mb_candidate(rel)
    counts = {}
    for tr in c["tracks"]:
        counts[tr["disc"]] = counts.get(tr["disc"], 0) + 1
        tr["num"] = counts[tr["disc"]]
    c.update(genre="", cover="", media="")
    return [c]


def album_only_source(http, album, cfg, ocr, matched):
    """(source name, scored best, why) for an album the matcher is sure of when only its
    pressing is not: the best of the tied pressings, its tracks one to one with the files.
    Its pressing fields are dropped later (album_only_tags)."""
    info = (matched or {}).get("album_only") or {}
    best = (matched or {}).get("best") or {}
    if not info.get("tied") or not best.get("id"):
        return None, None, []
    kinds = {k for t in info["tied"] for k in t.get("formats") or ()}
    if info.get("source") and kinds and info["source"] not in kinds:
        # none of them is what your copy is (a CD rip tied between vinyl pressings)
        return None, None, [f"your copy is {info['source']}; the tied pressings are {', '.join(sorted(kinds))}"]
    cands = pressing_matched(http, best.get("source"), best["id"])
    if not cands or len(cands[0]["tracks"]) != len(album["tracks"]):
        return None, None, ["the tied pressing's tracks do not line up with the files"]
    s = mc.score_candidate(album, cands[0], cfg, ocr)
    if not s.get("tracks_ok") or (s.get("dur_frac") or 0) < 1.0:
        return None, s, ["the tied pressing's track lengths do not all agree with the files"]
    return f"album only ({len(info['tied'])} pressings fit)", s, []


def album_only_tags(tags, info):
    """Each file's tags kept to what every tied pressing shares (mc.album_only_tags), then to
    the album's fields only (levels.album_field: no label, media or country, which are a
    pressing's, and none of the Discogs plugin's pressing fields); the date is the earliest
    pressing's when they differ."""
    tied = info.get("tied") or []
    years = sorted(t["year"] for t in tied if t.get("year"))
    # marked Partial, as the engine marks an album-only album (plan._album_only_reason): the
    # album is known, its pressing is not, so it is never counted as Resolved
    reason = reasons.encode("album known, pressing not established: pressing unknown "
                            f"({len(tied)} pressings fit equally)", ["ambiguous_release"])
    out = []
    for t in tags:
        t = dict(t, originaldate=str(years[0]) if years else "")
        t = mc.album_only_tags(t, tied)
        if years and len(set(years)) == 1:
            t["date"] = str(years[0])               # the year they share, not one pressing's day
        t.pop("originaldate", None)
        t.update(tagmender="Partial", tagmender_reason=reason, tagmender_source="")
        # only the album's fields (levels.album_field, as engine._album_only): the Discogs
        # plugin's fields of one pressing (discogs_identifier_*, discogs_status ...) never
        own = ("tagmender", "tagmender_reason", "tagmender_source")
        out.append({k: v for k, v in t.items() if v not in ("", None, [])
                    and (k in own or k.startswith("~") or _levels.album_field(k))})
    return out


def chosen_discogs(matched):
    """The Discogs release ID the matcher is sure of (or you approved) when MusicBrainz has
    no twin of it, else "". Only web_tag.py's own run meets these: the engine tags such an
    album from the Discogs release itself and never asks the web."""
    best = (matched or {}).get("best") or {}
    if (matched or {}).get("status") == "auto" and best.get("source") == "discogs" and best.get("id"):
        return str(best["id"])
    return ""


def discogs_source(http, album, cfg, ocr, release_id):
    """(source name, scored best, why) for the Discogs pressing the matcher chose: tagged
    from it or not at all, never from a shop's listing of the album (King Diamond - Deadly
    Lullabyes Live, Metal Blade 3984-14499-2, was tagged from iTunes). Its tracks only have
    to line up one to one: the score is kept as information, not asked again."""
    try:
        cands = discogs_matched(http, release_id)
    except Exception as exc:
        print(f"    Discogs lookup failed: {exc}", file=sys.stderr)
        return None, None, []
    if not cands:
        return None, None, []
    scored = [mc.score_candidate(album, c, cfg, ocr) for c in cands]
    res = mc.classify(scored, cfg)
    if res["status"] == "auto" or len(cands[0]["tracks"]) == len(album["tracks"]):
        return "Discogs", (res["best"] if res["status"] == "auto" else scored[0]), res["why"]
    return None, res.get("best"), res["why"]


def find(http, album, cfg, ocr="", sources=None):
    """(source name, scored best, why) of the first web source whose album passes the
    matcher's proof, else (None, best near miss or None, why). sources: as album_candidates."""
    artist = mc.artist_parts(album["artist"])[0]
    title = mc.search_title(album["title"])
    near = None
    linked = [u for u in album.get("tag_urls") or [] if ".bandcamp.com/album/" in u]
    sources = chosen_sources(sources)
    if linked and any(n == "Bandcamp" for n, _f in sources):                                    # the files' own tags name their Bandcamp page
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


def chosen_sources(names=None):
    """SOURCES limited to the profile's providers.web.sources, in its order (None: all)."""
    if names is None:
        return list(SOURCES)
    by_key = {n.lower(): (n, f) for n, f in SOURCES}
    return [by_key[k] for k in dict.fromkeys(str(x).lower() for x in names) if k in by_key]


def album_candidates(http, album, log=None, sources=None, n_tracks=None, failed=None):
    """[(source name, candidate)] of the web sources for the album (the Bandcamp page the
    files' tags link first, when Bandcamp is one of them), in source order. sources: the
    profile's providers.web.sources (None: every one). n_tracks: skip the Deezer and iTunes
    hits whose track count is known and more than 3 off (only a caller that refuses such
    albums passes it). A source that cannot be reached is left out (and logged): the
    others still count. failed: a list the names of those sources are added to."""
    artist = mc.artist_parts(album["artist"])[0]
    title = mc.search_title(album["title"])
    linked = [u for u in album.get("tag_urls") or [] if ".bandcamp.com/album/" in u]
    sources = chosen_sources(sources)
    if linked and any(n == "Bandcamp" for n, _f in sources):
        sources.insert(0, ("Bandcamp", lambda http, a, t: [c for c in (bandcamp_album(http, u)
                                                                        for u in linked[:2]) if c]))
    out = []
    for name, fetch in sources:
        try:
            cands = (fetch(http, artist, title, n_tracks=n_tracks) if n_tracks and fetch in _COUNTED
                     else fetch(http, artist, title)) or []
        except Exception as exc:                               # one source down: the others count
            if log:
                log.warning("%s lookup failed: %s", name, exc, extra={"provider": "web"})
            if failed is not None:
                failed.append(name)
            continue
        out += [(name, c) for c in cands]
    return out


# a title that names one version of the album ("Remix 2024", "Deluxe Edition", "Live"): its
# date is that version's, not the album's first release
_VERSION_MARK = re.compile(r"\b(remaster(ed)?|re-?mix(ed)?|rmx|deluxe|edition|reissue|re-?release|expanded|"
                           r"anniversary|version|live|mono|stereo|bonus|redux|re-?recorded)\b", re.I)


def version_title(title) -> bool:
    """The title names one version of the album ("... (Remix 2024)")."""
    return bool(_VERSION_MARK.search(str(title or "")))


def first_release_year(album, cand):
    """The year of the album's first release when the web source is sure of it, else None:
    an encyclopedia article's release date (it is about the album, not a pressing), or a
    shop's date when its title names no version of the album and the year agrees with your
    files' or folder's own year (a shop lists a reissue under the day it went on sale)."""
    year = cand.get("year")
    if not year:
        return None
    if cand.get("source") == "wikipedia":
        return year
    if version_title(cand.get("title")):
        return None
    return year if year == album.get("year") else None


def album_tags_for(album, cand, pairs, http, artist_cache, token="", numbers=True):
    """A web album's tags for each file (in file order) that name no release: album, album
    artist, artists and their MusicBrainz / Discogs artist IDs (artist_ids: only when known
    without doubt), track titles, genre, the album's first release date when the source is
    sure of it (first_release_year), and track and disc numbers when numbers (the source's
    order fits the files). Never a release ID, label, barcode, catalogue number, country,
    media or a pressing's date. pairs: [(file index, track index)]."""
    alb_artists = album_artists(http, cand, artist_cache)
    alb = [artist_ids(http, a, artist_cache, cand["title"], token, cand.get("year")) for a in alb_artists]
    first = first_release_year(album, cand)
    per_disc = {}
    for t in cand["tracks"]:
        per_disc[t.get("disc") or 1] = per_disc.get(t.get("disc") or 1, 0) + 1
    common = {"album": cand["title"], "albumartist": cand["artist"], "genre": genre_list(cand.get("genre")),
              "musicbrainz_albumartistid": [m for m, _d in alb if m],
              "discogs_albumartist_id": [d for _m, d in alb if d]}
    if first:
        date = cand.get("date") if cand.get("source") == "wikipedia" and str(cand.get("date") or "")[:4] == str(first) \
            else str(first)
        common.update(date=date, originaldate=date, originalyear=str(first))
    if numbers:
        common["totaldiscs"] = str(max(per_disc) if per_disc else 1)
    out = [dict(common) for _ in album["tracks"]]
    for i, j in pairs:
        c = cand["tracks"][j]
        names = track_artists(cand["artist"], c)
        ids = [artist_ids(http, n, artist_cache, cand["title"], token, cand.get("year")) for n in names]
        out[i].update(title=c["title"], artist=" & ".join(names) if len(names) > 1 else names[0], artists=names,
                      musicbrainz_artistid=[m for m, _d in ids if m], discogs_artist_id=[d for _m, d in ids if d])
        if numbers:
            disc = c.get("disc") or 1
            out[i].update(tracknumber=str(c.get("num") or j + 1), discnumber=str(disc),
                          totaltracks=str(per_disc.get(disc, len(cand["tracks"]))))
    return out


_PHYSICAL = {"cd", "vinyl", "cassette"}


def physical_copy(album):
    """Your copy is a CD, vinyl or tape: its folder name, rip log, cue sheet or CD sectors
    say so, or the folder holds scans of the packaging and the audio is not a download's."""
    source = ma.copy_source(album)
    return source in _PHYSICAL or (source != "digital" and bool((album.get("scans") or {}).get("physical")))


def web_media(album, cand):
    """MEDIA for the tags: the release's own (a Discogs or MusicBrainz pressing), "Digital
    Media" when the source is a download shop AND nothing about your copy says CD or vinyl
    (its folder name, rip log, cue sheet, CD sectors or scans), else "" (left unset)."""
    if cand.get("media"):
        return cand["media"]
    if cand.get("digital_only") and not physical_copy(album):
        return "Digital Media"
    return ""


def own_barcodes(album, text=""):
    """The barcodes your copy carries: in the files' tags, and read in full from its scans
    or documents (text), never one completed from a partial reading."""
    printed = mc.codes_from_ocr_text(text)["barcodes"] if text else []
    return ({mc.barcode_key(b) for b in album.get("barcodes") or ()}
            | {mc.barcode_key(b) for b in mc.barcodes_read_whole(text, printed)})


def web_barcode(album, cand, text=""):
    """The barcode to write: the release's, unless it is a download shop's UPC and your copy
    is not that download (a CD or vinyl rip, or files or scans that show another barcode).
    The shop's UPC then stands only when the album's own barcode agrees: in the tags, or
    printed in its scans or documents (text)."""
    code = next(iter(sorted(cand.get("barcodes") or ())), "")
    if not code or not cand.get("digital_only"):
        return code
    own = own_barcodes(album, text)
    if mc.barcode_key(code) in own or (text and mc.barcode_printed(text, code)):
        return code
    if own or physical_copy(album):
        return ""
    return code


def tags_for(album, cand, http, artist_cache, source_name, token="", text=""):
    """The tags to write to each file, in file order. text: the album's scans and
    documents (album_text), which may show the barcode."""
    alb_artists = album_artists(http, cand, artist_cache)
    alb = [artist_ids(http, a, artist_cache, cand["title"], token, cand.get("year")) for a in alb_artists]
    alb_ids = [m for m, _d in alb]
    total_discs = max([t.get("disc") or 1 for t in cand["tracks"]] or [1])
    per_disc = {}
    for t in cand["tracks"]:
        per_disc[t.get("disc") or 1] = per_disc.get(t.get("disc") or 1, 0) + 1
    common = {
        "album": cand["title"], "albumartist": cand["artist"], "date": cand["date"] or (str(cand["year"] or "")),
        "label": cand["label"], "barcode": web_barcode(album, cand, text), "genre": genre_list(cand.get("genre")),
        "totaldiscs": str(total_discs), "media": web_media(album, cand),
        "musicbrainz_albumartistid": [i for i in alb_ids if i],
        "discogs_albumartist_id": [d for _m, d in alb if d],
        "tagmender": "Resolved",
        "tagmender_reason": (f"not on MusicBrainz; tags from the Discogs release" if source_name == "Discogs"
                             else f"web_fallback_only: not on MusicBrainz or Discogs; tags from {source_name}"),
        "discogs_release_id": cand.get("discogs_release", ""),
        "tagmender_source": cand["url"],
    }
    if source_name == "Discogs":                 # the pressing itself: its number and country
        common.update(catalognumber=cand.get("catno_text") or "",
                      releasecountry=mc.country_code(cand.get("country")) or "")
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
             "discogs_release_id": "DISCOGS_RELEASE_ID", "catalognumber": "CATALOGNUMBER",
             "releasecountry": "MusicBrainz Album Release Country",
             "musicbrainz_albumartistid": "MusicBrainz Album Artist Id", "tagmender": "TAGMENDER",
             "tagmender_reason": "TAGMENDER_REASON", "tagmender_source": "TAGMENDER_SOURCE"}
_MP4 = {"album": "\xa9alb", "albumartist": "aART", "artist": "\xa9ART", "title": "\xa9nam", "date": "\xa9day",
        "genre": "\xa9gen"}
_MP4_FREE = {"label": "LABEL", "barcode": "BARCODE", "media": "MEDIA", "artists": "ARTISTS",
             "discogs_artist_id": "DISCOGS_ARTIST_ID", "discogs_albumartist_id": "DISCOGS_ALBUMARTIST_ID",
             "discogs_release_id": "DISCOGS_RELEASE_ID", "catalognumber": "CATALOGNUMBER",
             "releasecountry": "MusicBrainz Album Release Country",
             "musicbrainz_artistid": "MusicBrainz Artist Id",
             "musicbrainz_albumartistid": "MusicBrainz Album Artist Id", "tagmender": "TAGMENDER",
             "tagmender_reason": "TAGMENDER_REASON", "tagmender_source": "TAGMENDER_SOURCE"}


def write_tags(path, tags):
    import mutagen
    from mutagen.id3 import ID3, TXXX, TRCK, TPOS, Frames
    from mutagen.mp4 import MP4, MP4FreeForm
    path = Path(path)
    import tagio
    if tagio.fragmented(path):
        raise ValueError(f"{path.name} is a fragmented MP4: TagMender does not write to it")
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
        tagio.save_id3(f, f.tags, keep_date="date" not in vals)      # (in the tag's own version: v2.3 stays v2.3)
        return
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
    """Where Picard's naming script would file the album: <library>/<album artists A to Z,
    joined by &>/<original year> - <album, at most 5 words>[ [<reissue year> <media>]].

    The artist folder already in the library is used when it is the same name written
    another way (your Picard standardises "Voïvod" to Voivod; Discogs credits Voïvod), and
    the year is the album's original (the folder's own year) with a later pressing's year
    as the edition, like your script's "[2019 Vinyl]"."""
    library = Path(library) if library else Path(base).parent.parent
    artists = sorted(cand.get("album_artists") or
                     _each_once([a.strip() for a in re.split(_CREDIT_SPLIT, cand["artist"]) if a.strip()]),
                     key=str.casefold) or [cand["artist"]]
    artist_dir = _safe(" & ".join(artists))
    try:
        same = [d.name for d in library.iterdir() if d.is_dir() and mc.fold(d.name) == mc.fold(artist_dir)]
    except OSError:
        same = []
    if same:
        # several spellings in the library (Voïvod and Voivod): the one most albums are in,
        # which is where Picard files this artist
        def albums_in(name):
            try:
                return sum(1 for d in (library / name).iterdir() if d.is_dir())
            except OSError:
                return 0
        artist_dir = max(same, key=lambda n: (albums_in(n), n == Path(base).parent.name))
    pressing = str(cand.get("date") or cand.get("year") or "")[:4]
    m = re.match(r"(\d{4})\b", Path(base).name)
    original = m.group(1) if m and (not pressing.isdigit() or m.group(1) <= pressing) else pressing
    album = _five_words(cand["title"])
    name = f"{original} - {album}" if original else album
    if original and pressing.isdigit() and pressing > original:
        media = str(cand.get("media") or "")
        kind = ("Vinyl" if "vinyl" in media.lower() else "WEB" if media in ("Digital Media", "File")
                else "CD" if "cd" in media.lower() else media.split()[-1] if media else "")
        name += f" [{pressing} {kind}]" if kind else f" [{pressing}]"
    dest = library / artist_dir / _safe(name)
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


def carries_release(album_dir):
    """True when any audio file in the folder already names a release (a MusicBrainz or
    Discogs release ID): such an album keeps its tags; only a re-tag you approved replaces them."""
    import audit_untagged
    for p in sorted(Path(album_dir).rglob("*")):
        if p.is_file() and p.suffix.lower() in ma.AUDIO_EXT:
            t, _len = audit_untagged.read_tags(p)
            if t.get("mb") or t.get("dg"):
                return True
    return False


def unresolved(matches_db, only=None, include_review=False, include_discogs=True, include_album_only=True):
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
        # the album is certain and only its pressing is not: album tags, no release IDs
        album_only = include_album_only and o.get("status") != "auto" and bool(o.get("album_only"))
        if ((o.get("status") in wanted or discogs_only or album_only) and Path(path).is_dir()
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
    ap.add_argument("--untagged-only", action="store_true",
                    help="only albums whose files carry no release ID yet (retag_approved --new)")
    ap.add_argument("--keep-folders", action="store_true",
                    help="tag in place; do not file albums into artist folders")
    args = ap.parse_args(argv)
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.undo:
        return undo(out_dir, args.undo)

    cfg = dict(mc.DEFAULT_CFG)
    import companions
    token = ma._discogs_token() if companions.discogs_on() else ""
    if not companions.discogs_on():      # (the Discogs plugin off: Discogs is asked nothing)
        print("Note: the Discogs plugin is switched off (Preferences › Plugins): albums matched on Discogs "
              "are left out, and so are Discogs artist IDs.", file=sys.stderr)
    elif not token:                      # (said once; Discogs artist IDs are then left out)
        import requirements
        print(f"Note: {requirements.notice('discogs', 'cli', requirements.NOT_SET)}", file=sys.stderr)
    db = sqlite3.connect(Path(args.matches_db).expanduser(), timeout=60)
    http = ma.Http(db, cfg, "")
    ma.USER_AGENT = USER_AGENT
    albums = unresolved(args.matches_db, args.only, args.include_review,
                        not args.no_discogs and companions.discogs_on())
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
        album_only_pick = bool(o.get("album_only")) and o.get("status") != "auto"
        if (args.untagged_only or album_only_pick) and carries_release(base):
            # already tagged with a release: kept (Cynic, Nile, Ulcerate... were overwritten
            # with album-only tags on top of their release IDs)
            print(f"  =   {base.parent.name} | {base.name}: already tagged with a release; kept")
            report.append({"album": o["path"], "source": "", "page": "", "matched": "", "confidence": "",
                           "result": "already tagged with a release; kept"})
            continue
        chosen = o.get("status") == "auto" and (o.get("best") or {}).get("source") == "discogs"
        # a chosen Discogs pressing replaces the tags: read the album as the matcher did, blind to
        # the catalogue number and barcode of the release the files carry now (King Diamond -
        # Deadly Lullabyes Live: "barcode differs" was the Massacre CD's barcode in the tags)
        album = ma.read_album(base, o.get("artist_folder") or base.parent.name, o.get("album_folder") or base.name,
                              exclude=pre.excluded, blind=chosen)
        if not album["tracks"]:
            continue
        text = album_text(db, base)
        if o.get("album_only") and o.get("status") != "auto":
            source, best, why = album_only_source(http, album, cfg, text, o)
        elif chosen_discogs(o):
            source, best, why = discogs_source(http, album, cfg, text, chosen_discogs(o))
        else:
            source, best, why = find(http, album, cfg, text)
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
                import tagio
                files = [Path(t["path"]) for t in album["tracks"] if not tagio.fragmented(t["path"])]
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
                                       "artist": [c.get("artist") or ""], "genre": genre_list(c.get("genre")) or [""]},
                              process_anyway=args.process_anyway)
        if dec.excluded:                                 # what the web found is excluded: no writes at all
            whole = dec.summary() or next(iter(dec.excluded.values()))
            row["result"] = whole.reason()
            print(f"  ×   {base.parent.name} | {base.name}: {whole.reason()}")
            report.append(row)
            continue
        import tagio
        frag = [Path(t["path"]).name for t in album["tracks"] if tagio.fragmented(t["path"])]
        if frag:                                         # (never written to: its audio would break)
            row["result"] = f"{frag[0]} is a fragmented MP4: the album is left for you"
            print(f"  ×   {base.parent.name} | {base.name}: {row['result']}")
            report.append(row)
            continue
        tags = tags_for(album, best["cand"], http, cache, source, token, text)
        album_only = source.startswith("album only")
        if album_only:
            tags = album_only_tags(tags, o["album_only"])
        # an album-only album stays where it is: its folder year is not a pressing's
        filed = dict(best["cand"], media=tags[0].get("media", "") if tags else "")   # the media written
        dest = target_folder(base, filed, args.library) if not (args.keep_folders or album_only) else base
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
