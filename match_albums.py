#!/usr/bin/env python3
"""match_albums.py - phase 2: find the MusicBrainz / Discogs release for albums
that have no IDs yet. READ-ONLY with respect to your music library.

Input : the CSV from audit_untagged.py (Status = untagged by default).
Output: three CSVs in --out-dir
          matches_auto.csv     confident matches (safe to apply in phase 3)
          matches_review.csv   plausible but not certain; files left untouched
          matches_nomatch.csv  nothing plausible found
        plus match_state.db (resumable progress + HTTP cache).

Order of lookup: MusicBrainz first; Discogs only when MusicBrainz is not
already an auto match. Scoring lives in match_core.py.

Setup:
  export DISCOGS_TOKEN=...        # discogs.com/settings/developers
  python3 match_albums.py ~/Downloads/audit.csv --sample 100

Optional OCR (barcodes + printed text on back/disc scans):
  sudo apt install tesseract-ocr libzbar0 python3-pil python3-pyzbar
  ... --ocr
"""
import argparse
import copy
import csv
import json
import os
import random
import re
import shutil
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from http.client import HTTPException as _HTTPException
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import match_core as mc  # noqa: E402
import exclusions  # noqa: E402
import tagio  # noqa: E402
import ocr_order  # noqa: E402
import requirements  # noqa: E402
import companions  # noqa: E402

try:
    import mutagen
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4
except ImportError:
    sys.exit("mutagen is required: pip install mutagen")

def _script_version(plugin_files=()):
    """Changes whenever the matching code changes, including the Discogs Enrichment
    plugin code it uses. Results made by an older version are re-evaluated, so a fix
    reaches albums already marked auto."""
    import hashlib
    h = hashlib.md5()
    for path in [HERE / "match_core.py", HERE / "match_albums.py", *plugin_files]:
        try:
            h.update(Path(path).read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:12]


def plugin_files(dm):
    """The plugin modules the matcher's results depend on."""
    d = Path(dm.__file__).resolve().parent
    return [d / "discogs_mapping.py", d / "discogs_countries.py"]


SCRIPT_VERSION = _script_version()
_PLUGIN_STAMP = ""          # set in main() from the Discogs Enrichment code in use


def result_version():
    """Stored with every result: the matcher's version plus the plugin code's, so an
    update to either re-evaluates every album."""
    return f"{SCRIPT_VERSION}-{_PLUGIN_STAMP}" if _PLUGIN_STAMP else SCRIPT_VERSION

AUDIO_EXT = {".flac", ".mp3", ".m4a", ".mp4", ".ogg", ".opus", ".oga", ".wv",
             ".ape", ".wav", ".aif", ".aiff", ".dsf", ".dff", ".wma", ".mpc"}
from version import VERSION as _VERSION  # noqa: E402
USER_AGENT = f"TagMender/{_VERSION} ( https://github.com/herculepirate/tagmender )"   # MusicBrainz asks for app/version ( contact )
DEFAULT_PLUGIN_DIR = str(companions.discogs_dir())   # the built-in Discogs plugin (or $TAGMENDER_DISCOGS_PLUGIN)


# --------------------------------------------------------------------------
# Reading what the files say
# --------------------------------------------------------------------------
def _txt(v):
    if isinstance(v, (list, tuple)):
        v = v[0] if v else ""
    if isinstance(v, bytes):
        v = v.decode("utf-8", "ignore")
    return str(v).strip()


def _tell(text):
    """A progress line on stderr. Nobody may be reading it (a window started from a terminal or
    another program that has since gone): a look-up never fails for that."""
    try:
        print(text, file=sys.stderr)
    except (OSError, ValueError):
        pass


def _names(v):
    """All values of a multi-valued artist tag, joined. The Discogs Enrichment
    plugin writes a collaboration as ONE NAME PER ARTIST (albumartist =
    ["Max Corbacho", "Bruno Sanfilippo"]); reading only the first value would
    see half the credit."""
    vals = v if isinstance(v, (list, tuple)) else [v]
    out = []
    for x in vals:
        if isinstance(x, bytes):
            x = x.decode("utf-8", "ignore")
        x = str(x).strip()
        if x and x not in out:
            out.append(x)
    return "; ".join(out)


def _int(v):
    m = re.match(r"\s*(\d+)", str(v or ""))
    return int(m.group(1)) if m else 0


_ID_KEYS = ("musicbrainzalbumid", "discogsreleaseid")
_LOSSLESS_EXT = {".flac", ".wav", ".aiff", ".aif", ".wv", ".ape", ".alac"}
CD_FRAME = 588          # a CD sector holds 588 stereo samples at 44.1 kHz
# catalogue number tag names used by Picard, JRiver (LABELNO), foobar2000, Mp3tag and others
_CATNO_KEYS = ("catalognumber", "labelno", "catalogno", "catalog", "catno", "catalog#",
               "cataloguenumber", "catalogue", "catalogueno")
# barcode tag names: Picard's BARCODE, and the UPC / EAN / "EAN/UPN" other taggers and shops
# write (compared without spaces, dashes, underscores or slashes)
_BARCODE_KEYS = ("barcode", "upc", "ean", "eanupn", "eanupc", "upcean", "ean13", "upca", "upccode",
                 "eancode", "barcodenumber")
_ASIN_KEYS = ("asin", "amazonid", "amazonasin")
# An Amazon product ID: "B0..." or, for older CDs and books, an ISBN-10-like number
_ASIN = re.compile(r"(?:B0[0-9A-Z]{8}|\d{9}[\dX])")


def _plain_key(key):
    """A tag name to compare: lower case, letters and digits only ("EAN/UPN" is "eanupn")."""
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _code_tag(t, key, value):
    """A barcode or ASIN tag under any of its names. BARCODE is kept as Picard wrote it;
    UPC/EAN spellings only when their check digit is right; an ASIN that is a whole
    barcode counts as the barcode, one shaped like an Amazon ID is kept as the ASIN only.
    An empty tag never erases a code read before. t["barcode_from"] names the tag a
    barcode came from when it is not BARCODE (a shop's UPC is often the download's code).
    True when the key is one of these names."""
    name = str(key).upper()
    key = _plain_key(key)
    value = str(value or "").strip()
    digits = re.sub(r"[\s\-]", "", value)
    if key == "barcode":
        if value:
            t["barcode"], t["barcode_from"] = value, ""
    elif key in _BARCODE_KEYS:
        if not t["barcode"] and mc.valid_barcode(digits):
            t["barcode"], t["barcode_from"] = digits, name
    elif key in _ASIN_KEYS:
        if mc.valid_barcode(digits):
            if not t["barcode"]:
                t["barcode"], t["barcode_from"] = digits, name
        elif _ASIN.fullmatch(digits.upper()) and not t.get("asin"):
            t["asin"] = digits.upper()
    else:
        return False
    return True


def labels_from_text(text, limit=3):
    """Label names a ripper's or shop's notes write out ("Label: Dobles Music / Tidal",
    "Record label = X", a cue sheet's REM LABEL "X"). Searched with the catalogue number;
    never evidence by themselves."""
    out = []
    found = sorted(list(_LABEL_LINE.finditer(text or "")) + list(_LABEL_CAT_LINE.finditer(text or "")),
                   key=lambda m: m.start())
    for m in found:
        for name in re.split(r"\s+/\s+|\s*;\s*", m.group(1)):
            name = name.strip(" \t\"'.,")
            if 2 <= len(name) <= 60 and re.search(r"[^\W\d_]", name) and name not in out:
                out.append(name)
    return out[:limit]


# "Label:", "Record label =", a cue's REM LABEL "X", and the Russian trackers' "Лейбл:",
# "Издатель (лейбл):"
_LABEL_WORD = r"(?:(?:record[ \t]+)?label|лейбл|издатель(?:[ \t]*\(лейбл\))?)"
_LABEL_LINE = re.compile(r"(?im)^[ \t]*(?:REM[ \t]+)?" + _LABEL_WORD +
                         r"[ \t]*(?:[:=][ \t]*|[ \t]+(?=\"))(.+?)[ \t]*$")
# the label and catalogue number on one line: "Label / Cat#: Century Media / 77587-2",
# "Лейбл / Номер по каталогу: Century Media / 77587-2" (the label is the part before the " / ")
_LABEL_CAT_LINE = re.compile(
    r"(?im)^[ \t]*" + _LABEL_WORD + r"[ \t]*/[ \t]*(?:cat(?:alog(?:ue)?)?\.?[ \t]*(?:#|no\.?|number)"
    r"|номер[ \t]+по[ \t]+каталогу|каталожный[ \t]+номер|кат\.?[ \t]*номер)[ \t]*[:=][ \t]*(.+?)[ \t]+/[ \t]+\S.*$")


def search_labels(tag_label, text=""):
    """Label names to search with the catalogue number: the tags' label (one per ";"),
    then any the notes name."""
    out = [x.strip() for x in re.split(r"\s*;\s*", tag_label or "") if x.strip()]
    return out + [x for x in labels_from_text(text) if x not in out]


_ISRC = re.compile(r"\b([A-Z]{2}[A-Z0-9]{3}\d{7})\b")

# What a shop writes into the files it sells: Bandcamp's "Visit http://band.bandcamp.com"
# comment, a Tidal track URL, an Amazon Music album URL, Qobuz's name. They say where the
# FILES came from (a download), not which release they were tagged as, so a blind run
# keeps them (King Goat - King Goat: whole CD sectors, but bought on Bandcamp).
_DOWNLOAD_MARKS = (("Bandcamp", re.compile(r"\bvisit\s+https?://[\w-]+\.bandcamp\.com", re.I)),
                   ("Tidal", re.compile(r"\btidal\.com/(?:browse/)?track/\d+", re.I)),
                   ("Amazon Music", re.compile(r"\bmusic\.amazon\.[a-z.]+/albums/", re.I)),
                   ("Qobuz", re.compile(r"(?<![a-z0-9])qobuz(?![a-z0-9])", re.I)))


def download_marker(text):
    """The shop a file's comments and URLs name as where it was bought, or ""."""
    return next((shop for shop, rx in _DOWNLOAD_MARKS if rx.search(text or "")), "")


def _source_clues(path, t):
    """ISRC and every comment, URL and store ID in the tags, as one text, for
    source_links() to read (Bandcamp/iTunes/Qobuz/Deezer/Spotify/Beatport pages,
    MusicBrainz and Discogs release links)."""
    try:
        f = tagio.read_only_mutagen_file(path)
    except Exception:
        return
    tags = getattr(f, "tags", None)
    if not tags:
        return
    parts, isrc = [], ""
    for k, v in tags.items():
        key = str(k).lower()
        vals = v if isinstance(v, list) else [v]
        text = " ".join(str(getattr(x, "text", x) if not isinstance(x, bytes) else x.decode("utf-8", "ignore"))
                        for x in vals)
        if "isrc" in key or key.startswith("tsrc"):
            isrc = isrc or text.strip().upper().replace("-", "")
        elif key in ("plid", "cnid"):                               # iTunes album / track IDs
            parts.append(f"itunes:{key}:{text.strip()}")
        elif any(w in key for w in ("comm", "comment", "description", "url", "wxxx", "woaf", "woas", "wors",
                                    "purl", "website", "source", "qobuz", "deezer", "spotify", "beatport",
                                    "bandcamp", "itunes", "encoded", "ripp", "\xa9cmt")):
            parts.append(text)
    t["isrc"] = isrc if _ISRC.fullmatch(isrc or "") else ""
    t["clue_text"] = " ".join(parts)[:4000]
    t["download_marker"] = download_marker(t["clue_text"])
    # A shop's own track ID, as its downloaders write it (SOURCE=Deezer, SOURCEID=1203751422):
    # the shop's album page, which MusicBrainz links to the digital release, follows from it.
    low = {str(k).lower(): v for k, v in tags.items()}
    src = " ".join(str(x) for x in (low.get("source") or [])) if isinstance(low.get("source"), list) else ""
    sid = " ".join(str(x) for x in (low.get("sourceid") or [])) if isinstance(low.get("sourceid"), list) else ""
    if src.strip().lower() == "deezer" and sid.strip().isdigit():
        t["store_track"] = f"deezer:{sid.strip()}"


_LINK_PATTERNS = (
    ("musicbrainz", re.compile(r"musicbrainz\.org/release/([0-9a-f-]{36})")),
    ("discogs", re.compile(r"discogs\.com/(?:[\w-]+/)?release/(\d+)")),
    ("url", re.compile(r"(https?://[\w-]+\.bandcamp\.com/album/[\w-]+)")),
    ("url", re.compile(r"(https?://(?:www\.)?deezer\.com/(?:\w{2}/)?album/\d+)")),
    ("url", re.compile(r"(https?://open\.spotify\.com/album/\w+)")),
    # with or without the country ("/us/"), the album's name and "id" before the number
    ("url", re.compile(r"(https?://(?:music|itunes)\.apple\.com/(?:[a-z]{2}/)?album/(?:[^/?#\s]+/)?(?:id)?\d+)")),
    ("url", re.compile(r"(https?://(?:www\.)?qobuz\.com/[\w/-]*album/[\w/-]+)")),
    ("url", re.compile(r"(https?://(?:www\.)?beatport\.com/release/[\w-]+/\d+)")),
)


def source_links(infos):
    """Release links found in the files' own tags: (MusicBrainz ids, Discogs ids, other URLs)."""
    text = " ".join(i.get("clue_text", "") for i in infos)
    mb, dg, urls = set(), set(), []
    for kind, rx in _LINK_PATTERNS:
        for m in rx.findall(text):
            if kind == "musicbrainz":
                mb.add(m)
            elif kind == "discogs":
                dg.add(m)
            elif m not in urls:
                urls.append(m)
    m = re.search(r"itunes:plid:(\d+)", text)
    if m:
        urls.append(f"https://music.apple.com/album/{m.group(1)}")
    return mb, dg, urls


_APPLE_ALBUM = re.compile(r"(?:music|itunes)\.apple\.com/(?:([a-z]{2})/)?album/(?:[^/?#\s]+/)?(?:id)?(\d+)")


def lookup_forms(url):
    """The forms of a shop link to ask MusicBrainz's /url lookup for. An Apple Music
    album is stored there as https://music.apple.com/<country>/album/<id> (the name
    and query left out), or without the country: both are tried, the link's own
    country first ("us" when it has none)."""
    m = _APPLE_ALBUM.search(url)
    if not m:
        return [url]
    country, aid = m.group(1) or "us", m.group(2)
    return [f"https://music.apple.com/{country}/album/{aid}", f"https://music.apple.com/album/{aid}"]


def deezer_isrc_releases(http, album):
    """Releases MusicBrainz holds for the ISRCs of this album on Deezer (Deezer
    lists every track's ISRC): finds a MusicBrainz release that a title search
    misses, e.g. one listed under another title or script."""
    artist = mc.artist_parts(album["artist"])[0]
    title = mc.search_title(album["title"])
    query = 'artist:"%s" album:"%s"' % (artist, title)
    data = http.get_json(f"https://api.deezer.com/search/album?q={q(query)}&limit=10", "deezer") or {}
    n = len(album["tracks"])        # (limit 10, read as [:3]: web_tag's identical search is a cache hit)
    for hit in (data.get("data") or [])[:3]:
        if int(hit.get("nb_tracks") or 0) != n:
            continue
        tracks = (http.get_json(f"https://api.deezer.com/album/{hit['id']}/tracks?limit=300", "deezer") or {}).get("data") or []
        if len(tracks) != n or not all(abs((t.get("duration") or 0) - a["secs"]) <= 3
                                       for t, a in zip(tracks, album["tracks"])):
            continue                                   # not this album
        common = None
        for t in [tracks[0], tracks[-1]]:
            isrc = t.get("isrc") or (http.get_json(f"https://api.deezer.com/track/{t['id']}", "deezer") or {}).get("isrc")
            if not isrc:
                continue
            d = http.get_json(f"https://musicbrainz.org/ws/2/isrc/{isrc}?inc=releases&fmt=json", "mb") or {}
            rels = {r["id"] for rec in d.get("recordings") or [] for r in rec.get("releases") or [] if r.get("id")}
            common = rels if common is None else common & rels
        if common:
            return common
    return set()


# --------------------------------------------------------------------------
# Shops by barcode: what Deezer and iTunes sell under the album's UPC
# --------------------------------------------------------------------------
DEEZER_NO_DATA = 800          # Deezer's "DataException: no data": it has no album under this code


def deezer_upc_album(http, code):
    """The album Deezer sells under this barcode, or None. Deezer answers every error with
    HTTP 200 and {"error": {"code": ...}}: only "no data" (800) means it has no such album.
    Any other error (4 = over its request limit, 700 = busy, ...) is not an answer: it is
    dropped from the cache, so a later run asks again, and raised so it is noted."""
    url = f"https://api.deezer.com/album/upc:{code}"
    data = http.get_json(url, "deezer-upc") or {}
    error = data.get("error")
    if error:
        code_no = error.get("code") if isinstance(error, dict) else None
        if code_no == DEEZER_NO_DATA:
            return None
        _forget(http, url)
        message = (error.get("message") if isinstance(error, dict) else str(error)) or "error"
        raise ProviderUnavailable("deezer-upc", "rate_limited" if code_no == 4 else "outage",
                                  f"Deezer error {code_no}: {message}")
    if not data.get("title"):
        return None
    tracks = [{"title": t.get("title") or "", "secs": float(t.get("duration") or 0)}
              for t in (data.get("tracks") or {}).get("data") or []]
    return {"shop": "Deezer", "barcode": code, "artist": (data.get("artist") or {}).get("name") or "",
            "title": data["title"], "tracks": tracks}


def _forget(http, url):
    """Drop a cached answer that was not a real answer (as get_json does for a body that is not JSON)."""
    db = getattr(http, "db", None)
    if db is not None:
        db.execute("DELETE FROM http_cache WHERE url=?", (url,))
        db.commit()


def itunes_upc_album(http, code):
    """The album the iTunes Store sells under this barcode, with its songs, or None."""
    data = http.get_json(f"https://itunes.apple.com/lookup?upc={code}&entity=song", "itunes-upc") or {}
    results = data.get("results") or []
    album = next((r for r in results if r.get("wrapperType") == "collection"), None)
    if not album or not album.get("collectionName"):
        return None
    songs = sorted((r for r in results if r.get("wrapperType") == "track"),
                   key=lambda r: (int(r.get("discNumber") or 1), int(r.get("trackNumber") or 0)))
    tracks = [{"title": r.get("trackName") or "", "secs": float(r.get("trackTimeMillis") or 0) / 1000}
              for r in songs]
    return {"shop": "iTunes", "barcode": code, "artist": album.get("artistName") or "",
            "title": _ITUNES_KIND.sub("", album["collectionName"]), "tracks": tracks}


# iTunes names the kind of release in its title ("First Light - EP", "Song - Single");
# MusicBrainz and Discogs do not, so the title is searched without it
_ITUNES_KIND = re.compile(r"\s+-\s+(?:EP|Single)$", re.I)


SHOP_UPC_LOOKUPS = (("Deezer", deezer_upc_album), ("iTunes", itunes_upc_album))


def shop_barcodes(barcodes):
    """The album's barcodes a shop can be asked for: those valid as an EAN-13 or a UPC-A
    (codes are kept without their leading zeros)."""
    return sorted(b for b in barcodes if any(mc.valid_barcode(f) for f in mc.barcode_variants(b)))


def shop_upc_albums(http, barcodes, limit=2):
    """What the shops sell under the album's barcodes: ([shop album], [failure notes]).
    Each barcode is asked as its EAN-13 and its UPC-A (a shop keeps one of them). A shop
    that cannot be reached is noted and the others are still asked."""
    found, failed = [], []
    for code in shop_barcodes(barcodes)[:limit]:
        for shop, lookup in SHOP_UPC_LOOKUPS:
            if shop in failed:
                continue
            for form in [f for f in mc.barcode_variants(code) if mc.valid_barcode(f)]:
                try:
                    hit = lookup(http, form)
                except AuthError:
                    raise
                except Exception:
                    failed.append(shop)
                    break
                if hit:
                    found.append(hit)
                    break
    return found, [f"{shop} barcode lookup failed (offline or unavailable); carried on without it"
                   for shop in failed]


def shop_fits(album, shop, cfg):
    """True when the shop's album has the files' track count and every known length agrees."""
    if len(shop["tracks"]) != len(album["tracks"]):
        return False
    pairs = [(a["secs"], s["secs"]) for a, s in zip(album["tracks"], shop["tracks"]) if s["secs"] > 0]
    return bool(pairs) and all(mc.duration_ok(a, s, cfg) for a, s in pairs)


def shop_note(album, shop, cfg):
    """The shop's hit as a clue: "Deezer: UPC 0724... = Artist - Title (lengths agree)"."""
    fit = ("lengths agree" if shop_fits(album, shop, cfg)
           else f"{len(shop['tracks'])} tracks, does not fit your files")
    return f"{shop['shop']}: UPC {shop['barcode']} = {shop['artist']} - {shop['title']} ({fit})"


def shop_probe(album, shop):
    """The album searched under the shop's artist and title: only the title searches are
    new work (its codes, links and disc IDs were searched already). The shop's UPC is not
    evidence: the files' own barcode is still what candidates are compared with."""
    return dict(album, artist=shop["artist"] or album["artist"], title=shop["title"],
                barcodes=set(), asins=[], catno_search=[], search_labels=[], label_catnos=[],
                toc_releases=set(), link_releases=set(), isrc_releases=set(), tag_release_mb=set(),
                toc_discogs=set(), tag_discogs_links=set(), tag_release_discogs=set())


def _whole(sc):
    """The release a scored candidate was made from: for a match with tracks missing, the
    whole release rather than the view of the tracks you have (scoring that view again would
    lose which tracks are missing)."""
    return (sc.get("whole_view") or {}).get("cand") or sc["cand"]


def _rescored(res, extra, name, album, cfg, text=""):
    """`res` with the candidates in `extra` added, all scored again against the files."""
    cands = [_whole(s) for s in (res or {}).get("scored", [])]
    ids = {str(c["id"]) for c in cands}
    new = [c for c in extra if str(c["id"]) not in ids]
    if not new:
        return res                                    # nothing new: the result stands as it is
    scored = [mc.score_candidate(album, c, cfg, text) for c in cands + new]
    return dict(mc.classify(scored, cfg), scored=scored, source=name)


def _scored_again(res, extra, name, album, cfg, text=""):
    """`res` scored again against the files with the candidates in `extra` added, even when
    none is new (something the files show changed: fingerprints, a barcode). Each match is
    scored as its whole release (_whole), so one with tracks missing keeps them."""
    cands = [_whole(s) for s in (res or {}).get("scored", [])]
    ids = {str(c["id"]) for c in cands}
    cands += [c for c in extra if str(c["id"]) not in ids]
    if not cands:
        return res
    scored = [mc.score_candidate(album, c, cfg, text) for c in cands]
    return dict(mc.classify(scored, cfg), scored=scored, source=name)


def _rescored_all(res, album, cfg, text=""):
    """`res` with every candidate scored again against the files (something about them changed)."""
    if not res or not res.get("scored"):
        return res
    scored = [mc.score_candidate(album, _whole(s), cfg, text) for s in res["scored"]]
    return dict(mc.classify(scored, cfg), scored=scored, source=res.get("source"))


def both_databases_answered(use_mb, use_discogs, provider_errors=None):
    """MusicBrainz and Discogs were both asked and neither failed: only then can a barcode
    be on "no release" (one only the other database lists is not)."""
    return bool(use_mb and use_discogs and not ({"musicbrainz", "discogs"} & set(provider_errors or ())))


def unlisted_scan_barcodes(http, album, results, args=None):
    """The album's barcodes that were read off the scans (not the tags) and that no release
    carries: none of the candidates MusicBrainz and Discogs gave (their barcode searches
    included) and no shop sells anything under it. Empty when a shop could not be asked."""
    tags = {mc.barcode_key(b) for b in album.get("tag_barcodes") or ()}   # your copy's own
    listed = {mc.barcode_key(b) for res in results for sc in (res or {}).get("scored") or ()
              for b in sc["cand"].get("barcodes") or ()}
    # (only the barcodes the databases were searched by: one the scans show beside a different
    # barcode in the tags was not, and is never called unlisted - BAR-4)
    left = sorted(b for b in album.get("barcodes") or () if mc.barcode_key(b) not in listed | tags)
    if not left or (args is not None and not getattr(args, "use_shops", True)):
        return set()
    try:
        shops, failed = shop_upc_albums(http, left)
    except AuthError:
        raise
    except Exception:
        return set()
    if failed:
        return set()                                   # a shop not asked may sell it
    sold = {mc.barcode_key(s.get("barcode") or "") for s in shops} - {""}
    if shops and not sold:
        return set()                                   # sold, but under which barcode is unknown
    return {b for b in left if mc.barcode_key(b) not in sold}


def _search_key(title, artist):
    """What the title searches are made of: the same key means the same requests."""
    return mc.search_title(title).casefold(), mc.artist_parts(artist)[0].casefold()


def shop_upc_search(http, album, cfg, dm, mb_res, dg_res, text, use_mb, use_discogs, provider_errors=None,
                    barcodes=None):
    """When the album's barcode found nothing sure: what Deezer and iTunes sell under it.
    A shop album that fits the files (track count and lengths) under another title or
    artist is searched by that name on MusicBrainz and Discogs, and what comes back is
    scored like any other candidate. Returns (mb_res, dg_res, notes, source_failures).
    MusicBrainz or Discogs unavailable is a source failure, as for the other searches: it
    is returned in `source_failures` (a "no match" then becomes an error, retried later:
    the shop's title may be the only way to the album) and recorded in `provider_errors`;
    with Discogs off, MusicBrainz unavailable is raised, as the first search does.
    `barcodes` (default: all the album's) are the ones the shops are asked for."""
    shops, failed = shop_upc_albums(http, album["barcodes"] if barcodes is None else barcodes)
    source_failures = []
    notes = [shop_note(album, shop, cfg) for shop in shops]
    # the album's own name was searched already: only another spelling is new work
    searched = {_search_key(album["title"], album["artist"])}
    for shop in shops:
        key = _search_key(shop["title"], shop["artist"] or album["artist"])
        if key in searched or not shop_fits(album, shop, cfg):
            continue                                    # nothing new to search
        searched.add(key)
        mb_res, dg_res, more, failures = probe_search(
            http, shop_probe(album, shop), f"{shop['shop']} title", album, cfg, dm, mb_res, dg_res, text,
            use_mb, use_discogs, provider_errors)
        notes += more
        source_failures += failures
    return mb_res, dg_res, notes + failed, source_failures


def probe_search(http, probe, what, album, cfg, dm, mb_res, dg_res, text, use_mb, use_discogs,
                 provider_errors=None):
    """MusicBrainz and Discogs searched under another name (`probe`: a shop's or gnudb's
    title and artist); what comes back is scored against the files (`album`) like any other
    candidate. Returns (mb_res, dg_res, notes, source_failures): a provider unavailable is a
    source failure (recorded in `provider_errors`; MusicBrainz with Discogs off is raised),
    any other error a note. `what` names the title in the messages ("Deezer title")."""
    notes, source_failures = [], []
    extra = {"mb": [], "discogs": []}
    try:
        extra["mb"] = mb_search(http, probe, cfg) if use_mb else []
    except ProviderUnavailable as exc:
        if not use_discogs:
            raise
        source_failures.append(f"MusicBrainz search of the {what} failed ({exc})")
        if provider_errors is not None:
            provider_errors["musicbrainz"] = exc.kind
    except Exception as exc:
        notes.append(f"MusicBrainz search of the {what} failed ({exc})")
    try:
        extra["discogs"] = discogs_search(http, probe, cfg, dm) if use_discogs else []
    except AuthError:
        raise
    except ProviderUnavailable as exc:
        source_failures.append(f"Discogs search of the {what} failed ({exc})")
        if provider_errors is not None:
            provider_errors["discogs"] = exc.kind
    except Exception as exc:
        notes.append(f"Discogs search of the {what} failed ({exc})")
    mb_res = _rescored(mb_res, extra["mb"], "mb", album, cfg, text)
    dg_res = _rescored(dg_res, extra["discogs"], "discogs", album, cfg, text)
    return mb_res, dg_res, notes, source_failures


def shop_step(http, album, cfg, dm, mb_res, dg_res, final, text, args, use_mb, use_discogs, out,
              source_notes, asked):
    """The shops' step of `process`: when the album is still unsure and has barcodes the
    shops were not asked for yet (`asked` holds those that were), ask them and search
    what they sell. Returns (mb_res, dg_res, final); the clues go to out["shop_notes"],
    MusicBrainz or Discogs failing to `source_notes`."""
    # the ripper's notes' barcode is asked too: what a shop sells under it is a clue to
    # search, and it stays out of album["barcodes"] (never your copy's own, R3)
    codes = set(album["barcodes"]) | set(album.get("note_barcodes") or ())
    new = [b for b in shop_barcodes(codes) if mc.barcode_key(b) not in asked]
    if (final["status"] == "auto" or not getattr(args, "use_shops", True) or not (use_mb or use_discogs)
            or not new):
        return mb_res, dg_res, final
    asked.update(mc.barcode_key(b) for b in new)
    failures = []
    try:
        new_mb, new_dg, notes, failures = shop_upc_search(
            http, album, cfg, dm, mb_res, dg_res, text, use_mb, use_discogs,
            getattr(args, "provider_errors", None), barcodes=new)
    except AuthError:
        raise
    except ProviderUnavailable:         # only MusicBrainz with Discogs off gets here: retried later
        raise
    except Exception as exc:
        new_mb, new_dg = mb_res, dg_res
        notes = [f"shop barcode lookup failed ({type(exc).__name__}: {exc}); carried on without it"]
    source_notes.extend(failures)
    if notes:
        out["shop_notes"] = list(out.get("shop_notes") or []) + notes
    if new_mb is mb_res and new_dg is dg_res:
        return mb_res, dg_res, final                   # nothing new found: the result stands
    new_final = _combine(new_mb, new_dg)
    if new_final["status"] != "auto":                  # what the investigation said still holds
        new_final["why"] = list(new_final.get("why") or []) + [
            w for w in final.get("why") or [] if w.startswith("investigated:")]
    return new_mb, new_dg, new_final


def link_releases(http, album):
    """MusicBrainz releases the files' shop pages lead to (Bandcamp, Deezer, Spotify,
    Apple, Qobuz, Beatport: MusicBrainz's /url lookup of the page the files were bought
    from), and the releases holding the files' ISRCs.

    A MusicBrainz or Discogs release link in the tags is not read here: whoever tagged the
    files wrote it (like a release ID tag), so it is a candidate to fetch and score
    (album["tag_release_mb"] / ["tag_release_discogs"]), never proof. A failed /url or /isrc lookup is recorded in album["clue_failures"]
    ("musicbrainz link lookup"), never read as "no release": the album is retried. When an
    ISRC lookup failed, no ISRC evidence is used at all (the releases of fewer codes would
    be a looser match than the releases of all of them)."""
    found = set()
    album["link_releases"] = set()               # (what stands if every lookup below fails)
    album["isrc_releases"] = set()
    failed = []

    def mb_lookup(url):
        try:
            return http.get_json(url, "mb") or {}
        except _CLUE_ERRORS as exc:             # a lead lost to MusicBrainz failing: reported
            failed.append(exc)
            return None
    for ref in sorted(album.get("store_tracks") or ())[:2]:
        shop, tid = ref.split(":", 1)
        if shop == "deezer":
            try:
                track = http.get_json(f"https://api.deezer.com/track/{tid}", "deezer") or {}
            except Exception:
                track = {}
            aid = (track.get("album") or {}).get("id")
            url = f"https://www.deezer.com/album/{aid}" if aid else ""
            if url and url not in (album.get("tag_urls") or []):
                album["tag_urls"] = [url] + list(album.get("tag_urls") or [])
    for link in (album.get("tag_urls") or [])[:4]:
        for url in lookup_forms(link):
            data = mb_lookup("https://musicbrainz.org/ws/2/url?resource=" + q(url) + "&inc=release-rels&fmt=json")
            if data is None:
                continue                         # (this form failed: the next one may answer)
            rids = {(r.get("release") or {}).get("id") for r in data.get("relations") or []} - {None}
            found |= rids
            if rids:
                break
    album["link_releases"] = set(found)
    isrc_rels, isrc_failed = None, False
    for code in [c for c in album.get("isrcs") or [] if c][:3]:
        data = mb_lookup(f"https://musicbrainz.org/ws/2/isrc/{code}?inc=releases&fmt=json")
        if data is None:
            isrc_failed = True
            continue
        rels = {rel["id"] for rec in data.get("recordings") or [] for rel in rec.get("releases") or [] if rel.get("id")}
        if rels:
            isrc_rels = rels if isrc_rels is None else (isrc_rels & rels)
    if isrc_failed:
        isrc_rels = None                         # a partial intersection is looser: none at all
    if failed:
        exc = failed[-1]
        album.setdefault("clue_failures", {})["musicbrainz link lookup"] = (
            _clue_error_text(exc), getattr(exc, "kind", "outage"))
    album["isrc_releases"] = isrc_rels or set()
    return found | (isrc_rels or set())


# "01 - Title", "1-03 Title", "A2. Title", "03. Artist - Title": tracks named by file only
_NAME_NUM = re.compile(r"^(?:(\d)\s*[-.]\s*)?(\d{1,3})\s*(?:[-._)]\s*|\s+)(.+)$")
_NAME_SIDE = re.compile(r"^([A-Ha-h])(\d{1,2})\s*[-._)]?\s+(.+)$")


def names_from_files(infos):
    """Fill track number, disc and title from the file names where the tags have none."""
    parsed = []
    for p, i in infos:
        stem = Path(p).stem
        m, side = _NAME_NUM.match(stem), _NAME_SIDE.match(stem)
        if m:
            parsed.append((p, i, int(m.group(1) or 0), int(m.group(2)), m.group(3).strip()))
        elif side:
            parsed.append((p, i, 0, (ord(side.group(1).upper()) - 64) * 100 + int(side.group(2)), side.group(3).strip()))
        else:
            parsed.append((p, i, 0, 0, stem))
    rests = [r for *_x, r in parsed]
    heads = {r.split(" - ", 1)[0] for r in rests if " - " in r}
    common = next(iter(heads)) if len(heads) == 1 and all(" - " in r for r in rests) and len(rests) > 1 else None
    sides = sorted({n for _p, _i, _d, n, _r in parsed if n >= 100})
    for p, i, disc, num, rest in parsed:
        title = rest.split(" - ", 1)[1] if common else rest
        if re.fullmatch(r"[A-Z0-9-]{1,14}", title) and len(rests) > 1:
            title = ""                                     # "001.ITKWEDTSIM": initials, not a title
        if num >= 100 and sides:
            num = sides.index(num) + 1                     # vinyl sides A1, A2, B1 ... in order
        if not i.get("num") and num:
            i["num"] = num
        elif not i.get("num") and num == 0 and _NAME_NUM.match(Path(p).stem):
            i["num0"] = True                               # "00 - Hidden": numbered 0
        if not i.get("disc") and disc:
            i["disc"] = disc
        if not i.get("title") and title:
            i["title"] = title.replace("_", " ").strip()


_MBID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# a bare MBID in a comment (not part of a link: those are read by source_links), and a
# Discogs release number written out ("Discogs: 1441661", "DISCOGS_RELEASE_ID=1441661")
_BARE_MBID = re.compile(r"(?<![0-9a-zA-Z/=-])(" + _MBID.pattern + r")(?![0-9a-zA-Z-])", re.I)
_BARE_DISCOGS = re.compile(r"\bdiscogs[ _-]?(?:release[ _-]?(?:id)?\s*[:#=]?|(?:id)?\s*[:#=]|r)\s*r?(\d{3,10})\b", re.I)


def _release_id(t, key, value):
    """A release ID tag: the files were tagged before. Kept as a candidate to look up
    and score like any other, never as proof (the tags may be wrong)."""
    t["has_ids"] = True
    value = str(value or "").strip()
    if key == "musicbrainzalbumid" and _MBID.fullmatch(value.lower()):
        t["mb_albumid"] = value.lower()
    elif key == "discogsreleaseid" and value.isdigit():
        t["discogs_releaseid"] = value


# TagMender's own jobs, parsed once per plan file: {job: (plan.json mtime, albums)}
_OWN_JOBS = {}
# the path index built from them: (signature of the job history, {path: (MusicBrainz ids, Discogs ids)})
_OWN_INDEX = [None, {}]
_OWN_LOCK = threading.Lock()


def _own_job_files(job):
    """[(source folder, exact, [(src, dst, op, MusicBrainz ids, Discogs ids)])] for each album
    of a job's plan. The IDs are those the file holds afterwards that TagMender may own: the
    ones it wrote of the release it identified exactly, or (no ID changed) any ID of that
    release the file kept; never a preserved ID of another release. Cached per plan file."""
    path = exclusions.STATE_DIR / "jobs" / str(job) / "plan.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    with _OWN_LOCK:
        hit = _OWN_JOBS.get(job)
        if hit and hit[0] == mtime:
            return hit[1]
    albums = []
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        plan = {}
    for a in plan.get("albums") or ():
        ident = a.get("identification") or {}
        exact = ident.get("tier") == "exact"
        rid = str(ident.get("release_id") or "").lower()
        chosen = frozenset(({rid} | set(_MB_DISCOGS_RE.findall(str(ident.get("url") or "")))) - {""})
        files = []
        for fe in a.get("files") or ():
            if not fe.get("dst"):
                continue
            after = {r.get("field"): str((r.get("after") or [""])[0] or "") for r in fe.get("diff") or ()
                     if r.get("field") in ("musicbrainz_albumid", "discogs_release_id")}
            ids = []
            for field in ("musicbrainz_albumid", "discogs_release_id"):
                if field in after:          # written: only an ID of the release it identified
                    v = after[field].lower() if field == "musicbrainz_albumid" else after[field]
                    ids.append(frozenset({v}) & chosen)
                else:                       # kept: the file's own ID, when it is that release's
                    ids.append(chosen)
            files.append((str(fe.get("src") or fe["dst"]), str(fe["dst"]), fe.get("op"), *ids))
        albums.append((a.get("source_dir"), exact, files))
    with _OWN_LOCK:
        _OWN_JOBS[job] = (mtime, albums)
    return albums


def _own_index():
    """{file path: (MusicBrainz ids, Discogs ids)} TagMender's jobs own, built once per process
    and again only when the job history changed (state.db, its WAL, its job rows)."""
    db_path = exclusions.STATE_DIR / "state.db"
    try:
        st = db_path.stat()
    except OSError:
        return {}
    try:
        wal = os.stat(str(db_path) + "-wal")
        wal = (wal.st_mtime_ns, wal.st_size)
    except OSError:
        wal = None
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
        try:
            # the newest job and album row (job_albums is only ever appended to: its newest
            # rowid is enough), and how many jobs were undone (jobs: one row per job, few)
            sig = (str(db_path), st.st_mtime_ns, st.st_size, wal,
                   db.execute("SELECT MAX(rowid), COUNT(*), SUM(status LIKE 'undone%') FROM jobs").fetchone(),
                   db.execute("SELECT MAX(rowid) FROM job_albums").fetchone())
            with _OWN_LOCK:
                if _OWN_INDEX[0] == sig:
                    return _OWN_INDEX[1]
            rows = db.execute("SELECT a.job, a.album FROM job_albums a JOIN jobs j ON j.job = a.job "
                              "WHERE a.status IN ('done', 'unchanged') AND COALESCE(j.status, '') NOT LIKE 'undone%' "
                              "ORDER BY a.ts").fetchall()
        finally:
            db.close()
    except sqlite3.Error:
        return {}
    done, last = {}, {}
    for i, (job, folder) in enumerate(rows):
        done.setdefault(job, set()).add(folder)
        last[job] = i
    owned = {}
    # Jobs in order: a file a job moved carries what was owned at its old path to the new one
    # (a copy to both); an album identified exactly owns the IDs that job left in its files.
    for job in sorted(done, key=last.get):
        for folder, exact, files in _own_job_files(job):
            if folder not in done[job]:
                continue
            for src, dst, op, mb, dg in files:
                carried = owned.get(src)
                if src != dst and carried is not None:
                    if op != "copy":
                        owned.pop(src, None)
                    owned[dst] = carried
                if exact and (mb or dg):
                    owned[dst] = (mb, dg)
    with _OWN_LOCK:
        _OWN_INDEX[:] = [sig, owned]
    return owned


def tagmender_wrote(files):
    """True when TagMender itself wrote the release IDs these files carry: for every file
    [(path, MusicBrainz id, Discogs id)], a job of TagMender's (not undone) that ended done
    or unchanged identified that release exactly and left that very ID in that file (written,
    or kept as it was), and every later job of TagMender's that moved the file carried it to
    this path. Read from the state database's job history and the jobs' plans (read only).
    Re-processing such an album (a watched folder given a profile that moves it) keeps its
    tags' barcode and the rest as the copy's own: TagMender chose them on evidence then.
    False when any file's IDs are not TagMender's (another tagger, or edited since), or the
    history is gone (pruned jobs, the album moved by hand since)."""
    if not files:
        return False
    owned = _own_index()
    if not owned:
        return False
    for path, mb, dg in files:
        own = owned.get(str(path)) or owned.get(os.path.realpath(str(path)))
        if not own or not (mb or dg) or (mb and str(mb).lower() not in own[0]) or (dg and str(dg) not in own[1]):
            return False
    return True


def tag_release_ids(infos):
    """(MusicBrainz ids, Discogs ids) the files' tags and comments name as their release."""
    mb, dg = set(), set()
    for i in infos:
        if i.get("mb_albumid"):
            mb.add(i["mb_albumid"])
        if i.get("discogs_releaseid"):
            dg.add(i["discogs_releaseid"])
        text = i.get("clue_text") or ""
        mb |= {m.lower() for m in _BARE_MBID.findall(text)}
        dg |= set(_BARE_DISCOGS.findall(text))
    return mb, dg


def read_file(path):
    t = {"title": "", "num": 0, "disc": 0, "album": "", "artist": "", "year": 0,
         "barcode": "", "barcode_from": "", "asin": "", "catno": "", "secs": 0.0,
         "label": "", "country": "", "media": "", "date": "",   # pressing details in the tags
         "rate": 0, "bits": 0, "samples": 0, "lossless": False,  # the audio itself
         "has_ids": False,      # already carries a MusicBrainz or Discogs release ID
         "mb_albumid": "", "discogs_releaseid": "",    # ... and which (a candidate, not evidence)
         "audio_md5": 0,        # FLAC's checksum of the decoded audio (0 = unknown)
         "isrc": "", "clue_text": "",   # ISRC, and comments/URLs/store IDs left by shops and rippers
         "download_marker": "",         # the shop those comments name (kept when blind)
         "tracktotal": 0,               # the album's track count, as the tags have it
         "write_failed_total": 0,       # TagMender's own "write_failed: ... 10 of 12" marker: the 12
         "cdtoc": "",                   # the ripper's CDTOC tag: the whole disc's TOC ("20+96+1937+...")
         "credit": ""}                  # who the files say performed/wrote it: track artist, copyright, composer
    try:
        _source_clues(path, t)
        if path.suffix.lower() in (".m4a", ".mp4"):
            f = MP4(path)
            t["secs"] = f.info.length
            for k, v in (f.tags or {}).items():
                nk = re.sub(r"[\s_\-]", "", (k.decode() if isinstance(k, bytes) else k).lower()
                            .replace("----:com.apple.itunes:", ""))
                if k == "\xa9nam": t["title"] = _txt(v)
                elif k == "\xa9alb": t["album"] = _txt(v)
                elif k == "aART": t["artist"] = _names(v)
                elif k == "\xa9ART" and not t["artist"]: t["artist"] = _txt(v)
                elif k == "\xa9day": t["year"] = _int(v[0] if v else "")
                elif k == "trkn" and v:
                    t["num"], t["tracktotal"] = v[0][0], (v[0][1] if len(v[0]) > 1 else 0)
                    # (trkn (0, N) is the total alone, no number: a file numbered 0 only when
                    # its name says 00 too, names_from_files)
                elif k == "disk" and v: t["disc"] = v[0][0]
                elif _plain_key(nk) in _BARCODE_KEYS + _ASIN_KEYS: _code_tag(t, nk, _txt(v))
                elif nk in _CATNO_KEYS and not t["catno"]: t["catno"] = _txt(v)
                elif nk in _ID_KEYS: _release_id(t, nk, _txt(v))
                if k in ("\xa9ART", "cprt", "\xa9wrt"): t["credit"] += " " + _txt(v)
            return t
        f = tagio.read_only_mutagen_file(path)
        if f is None:
            return t
        t["secs"] = getattr(f.info, "length", 0.0) or 0.0
        t["audio_md5"] = getattr(f.info, "md5_signature", 0) or 0
        t["rate"] = getattr(f.info, "sample_rate", 0) or 0
        t["bits"] = getattr(f.info, "bits_per_sample", 0) or 0
        t["samples"] = getattr(f.info, "total_samples", 0) or 0
        t["channels"] = getattr(f.info, "channels", 0) or 0
        t["lossless"] = path.suffix.lower() in _LOSSLESS_EXT
        if t["lossless"] and not t["samples"] and t["rate"]:
            t["samples"] = round(t["secs"] * t["rate"])        # WAV/AIFF/WavPack: from the length
        tags = f.tags
        if tags is None:
            return t
        if isinstance(tags, ID3) or tags.__class__.__module__.startswith("mutagen.id3"):
            def fr(fid):
                x = tags.getall(fid)
                return _txt([str(s) for s in x[0].text]) if x else ""
            def names(fid):
                x = tags.getall(fid)
                return _names([str(s) for s in x[0].text]) if x else ""
            t.update(title=fr("TIT2"), album=fr("TALB"), artist=names("TPE2") or names("TPE1"),
                     year=_int(fr("TDRC")), num=_int(fr("TRCK")), disc=_int(fr("TPOS")),
                     label=fr("TPUB"), media=fr("TMED"), date=fr("TDRL") or fr("TDRC"),
                     credit=" ".join(fr(x) for x in ("TPE1", "TCOP", "TCOM", "TPE3")))
            for frame in tags.getall("TXXX"):
                nk = re.sub(r"[\s_\-]", "", frame.desc.lower())
                if _plain_key(nk) in _BARCODE_KEYS + _ASIN_KEYS: _code_tag(t, nk, _txt(frame.text))
                elif nk in ("musicbrainzalbumreleasecountry", "releasecountry"): t["country"] = _txt(frame.text)
                elif nk in _CATNO_KEYS and not t["catno"]: t["catno"] = _txt(frame.text)
                elif nk in _ID_KEYS: _release_id(t, nk, _txt(frame.text))
                elif nk == "tagmenderreason": _write_failed(t, _txt(frame.text))
                elif nk == "cdtoc": t["cdtoc"] = _txt(frame.text)
            t["tracktotal"] = _total(fr("TRCK"))
            t["num0"] = _numbered_zero(fr("TRCK"))
            return t
        low = {}
        for k, v in tags.items():
            low.setdefault(re.sub(r"[\s_\-]", "", str(k).lower()), v)
        t["has_ids"] = any(k in low for k in _ID_KEYS)
        for k in _ID_KEYS:
            if k in low:
                _release_id(t, k, _txt(low[k]))
        g = lambda k: _txt(low.get(k, ""))  # noqa: E731
        n = lambda k: _names(low.get(k, ""))  # noqa: E731
        t.update(title=g("title"), album=g("album"), artist=n("albumartist") or n("artist"),
                 year=_int(g("date") or g("year")), num=_int(g("tracknumber")),
                 disc=_int(g("discnumber")), barcode=g("barcode"),
                 catno=next((g(k) for k in _CATNO_KEYS if g(k)), ""),
                 label=g("label") or g("organization") or g("publisher"),
                 country=g("releasecountry"), media=g("media"), date=g("date"),
                 credit=" ".join(g(k) for k in ("artist", "copyright", "composer", "performer", "conductor")))
        t["tracktotal"] = _int(g("tracktotal") or g("totaltracks")) or _total(g("tracknumber"))
        t["num0"] = _numbered_zero(g("tracknumber"))
        _write_failed(t, g("tagmenderreason"))
        t["cdtoc"] = g("cdtoc")
        t["sacd_area"] = g("sacdarea")                 # an SACD rip's area: "Stereo", "Multichannel"
        for k, v in low.items():                     # UPC / EAN / ASIN under their other names
            if k != "barcode" and _plain_key(k) in _BARCODE_KEYS + _ASIN_KEYS:
                _code_tag(t, k, _txt(v))
    except Exception as exc:
        t["_error"] = str(exc)
    return t


def _numbered_zero(number):
    """A track number of 0 written out ("0", "00", "0/12"): the hidden track before track 1
    (HTOA), as Picard numbers a release's pregap track. Not a missing number."""
    return bool(re.fullmatch(r"\s*0+\s*(?:/\s*\d*\s*)?", str(number or "")))


def _total(number):
    """The track count of a "3/12" track number, or 0."""
    m = re.match(r"\s*\d+\s*/\s*(\d+)", str(number or ""))
    return int(m.group(1)) if m else 0


def _write_failed(t, reason):
    """TagMender's own marker on files it could not finish ("write_failed: saved 10 of 12
    files"): the album has 12 tracks, though only some may be in this folder."""
    m = re.search(r"\b(\d+)\s+of\s+(\d+)\b", str(reason or ""))
    if m and "write_failed" in str(reason) and int(m.group(1)) <= int(m.group(2)):
        t["write_failed_total"] = int(m.group(2))


def _natural_key(text):
    """'2 - x' before '10 - x' (plain string order puts 10 and 11 before 2)."""
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", str(text))]


# When the same album sits in a folder twice in different formats, keep one copy.
_FORMAT_PREFERENCE = [".flac", ".wav", ".aiff", ".aif", ".ape", ".wv", ".dsf", ".dff",
                      ".m4a", ".mp4", ".mp3", ".ogg", ".oga", ".opus", ".mpc", ".wma"]


# Copies of the same audio differ in length by hundredths of a second (encoder
# padding); different tracks are practically never this close.
_SAME_AUDIO_S = 0.25


def _same_copy(g1, g2, info):
    """Two file groups hold the same recording when they have the same number
    of tracks, the lengths agree track by track (within _SAME_AUDIO_S) AND the titles - or failing that the file names -
    agree. Lengths alone are not enough: two discs of one 60-minute piece each
    can be equally long."""
    if len(g1) != len(g2) or not g1:
        return False
    if _layouts(g1, info) != _layouts(g2, info):
        return False                     # (stereo and multichannel: two versions, not two copies)
    close = sum(1 for a, b in zip(g1, g2)
                if abs(info[a]["secs"] - info[b]["secs"]) <= _SAME_AUDIO_S)
    if close / len(g1) < 0.9:
        return False

    def name(p):
        t = info[p]["title"].strip().casefold()
        return t or re.sub(r"\W+", " ", p.stem).strip().casefold()
    same = sum(1 for a, b in zip(g1, g2) if name(a) == name(b))
    return same / len(g1) >= 0.8


def _layouts(files, info):
    """The channel layouts of these files: their folder's name ("2.0", "Stereo") and their
    audio's channel count or SACD_AREA tag. Two groups whose layouts differ are two versions."""
    out = {mc.layout_of_channels(info[p].get("channels"), info[p].get("sacd_area")) for p in files}
    out |= {mc.version_layout(p.parent.name) for p in files}
    out.discard("")
    return out


def _drop_duplicate_copies(files, info, album_dir):
    """Folders with the same album twice - FLAC + Opus side by side, or the
    same format in two folders ('16-44/' and '24-96/') - count once. Real
    discs (different tracks) are never merged, whatever their format.
    Keeps the preferred format, then the larger (higher-resolution) copy."""
    groups = {}
    for p in files:
        groups.setdefault((p.parent, p.suffix.lower()), []).append(p)
    for k in groups:
        groups[k].sort(key=lambda p: (info[p]["num"] or 9999, _natural_key(p.name)))
    rank = {k: (not any(info[p]["has_ids"] for p in g),   # the copy already tagged
                _FORMAT_PREFERENCE.index(k[1]) if k[1] in _FORMAT_PREFERENCE else 99,
                -sum(p.stat().st_size for p in g)) for k, g in groups.items()}
    keys = sorted(groups, key=lambda k: _natural_key(f"{k[0]}{k[1]}"))
    removed = []
    for i, k1 in enumerate(keys):
        if k1 in removed:
            continue
        for k2 in keys[i + 1:]:
            if k2 in removed or not _same_copy(groups[k1], groups[k2], info):
                continue
            loser = k2 if rank[k1] <= rank[k2] else k1
            removed.append(loser)
            if loser == k1:
                break
    if not removed:
        return files, []
    gone = {p for k in removed for p in groups[k]}
    label = [f"{(Path(os.path.relpath(k[0], album_dir)).as_posix() + '/') if k[0] != Path(album_dir) else ''}"
             f"*{k[1]}" for k in removed]
    return [p for p in files if p not in gone], label


def _track_key(p, i):
    """What identifies a track for duplicate detection: its title tag, or the
    file name without the leading track number ("001.ACAaM" and "01.ACAAM"
    are the same track saved by two versions of a naming script)."""
    t = i["title"].strip()
    if not t:
        t = re.sub(r"^[\d\s._\-]+", "", p.stem)
    return re.sub(r"\W+", " ", t).strip().casefold()


def _other_disc(a, b, info) -> bool:
    """a and b are on two different discs of a set: both carry a disc tag and the two
    differ, or they sit in two different disc folders ("Disc 1", "Disc 2"). A file without
    a disc tag fits any disc ("01 Song.flac" tagged disc 1 and its MP3 copy untagged)."""
    def num(i):
        try:
            return int(str(i.get("disc") or 0).split("/")[0])
        except ValueError:
            return 0
    da, db = num(info[a]), num(info[b])
    if da and db and da != db:
        return True
    if a.parent == b.parent:
        return False
    if mc.version_layout(a.parent.name) and mc.version_layout(b.parent.name) and \
            _layouts([a], info) != _layouts([b], info):
        return True                                   # ("2.0/" and "5.0/": a version each)
    from engine import is_disc_dir                    # (engine imports this module)
    return is_disc_dir(a.parent) or is_disc_dir(b.parent)


def _other_audio(a, b, info) -> bool:
    """a and b are two FLAC files whose decoded audio has a checksum each, and the two differ:
    two recordings, however alike their titles and lengths (a suite played twice on one CD,
    its "Gnomus" 2:43 both times). A file without one (another format, a FLAC whose encoder
    left it empty) says nothing."""
    ma_, mb_ = info[a].get("audio_md5"), info[b].get("audio_md5")
    return bool(ma_ and mb_ and ma_ != mb_)


def _drop_duplicate_tracks(files, info):
    """The same track twice in one album - same title (or same name apart
    from the number) and the same length (within _SAME_AUDIO_S), in any folder
    of it and in any format - counts once. Two different tracks that merely
    share a title differ in length; the same title on two discs (two disc
    tags, or two disc folders) is another disc's track (a remaster on disc 1,
    its remix on disc 2), never a copy unless the audio itself is the same
    (its checksum, below)."""
    buckets = {}
    for p in files:
        key = _track_key(p, info[p])
        if key:
            buckets.setdefault(key, []).append(p)

    def preference(p):
        # Keep the copy that already carries release IDs (the one Picard
        # saved last), then the preferred format, then the newest file.
        ext = p.suffix.lower()
        st = p.stat()
        return (not info[p]["has_ids"],
                _FORMAT_PREFERENCE.index(ext) if ext in _FORMAT_PREFERENCE else 99,
                -st.st_mtime, -st.st_size, _natural_key(p.name))
    # FLAC files whose decoded audio has the same checksum are the same
    # recording, whatever their names or titles say.
    for p in files:
        md5 = info[p].get("audio_md5")
        if md5:
            buckets.setdefault(f"md5:{md5:x}", []).append(p)
    gone = []
    for key, group in buckets.items():
        group = [p for p in group if p not in gone]
        if len(group) < 2:
            continue
        kept = []
        by_title = not str(key).startswith("md5:")
        for p in sorted(group, key=preference):
            if any(abs(info[p]["secs"] - info[k]["secs"]) <= _SAME_AUDIO_S
                   and not (by_title and (_other_disc(p, k, info) or _other_audio(p, k, info)))
                   for k in kept):
                gone.append(p)
            else:
                kept.append(p)
    if not gone:
        return files, []
    gone_set = set(gone)
    return [p for p in files if p not in gone_set], sorted(p.name for p in gone_set)


BLIND_KEYS = ("barcode", "barcode_from", "asin", "catno", "label", "country", "media", "date", "isrc", "clue_text", "credit",
              "mb_albumid", "discogs_releaseid")


def notes_text(album_dir, limit=20000):
    """Text the ripper or shop left next to the music (.info, .txt, .nfo), in subfolders
    too ("covers/tech.info/tXt.txt"): it often names the performer, the label ("Label:
    Dobles Music / Tidal") and the edition ripped ("Century Media – 8513-2")."""
    out = []
    for dirpath, dirs, names in os.walk(album_dir):
        dirs.sort()
        for n in sorted(names):
            p = Path(dirpath) / n
            try:
                if p.suffix.lower() in (".info", ".txt", ".nfo", ".md") and p.is_file() and p.stat().st_size < limit:
                    out.append(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    return " ".join(out)[:limit]


# A line of the notes that names the edition: "Cat#: QDISS0201CDD", "Catalogue number = X",
# "[b]Исходное издание:[/b] Century Media – 8513-2" (a tracker's "source edition").
_NOTE_CODE_LINE = re.compile(
    r"(?im)^[ \t]*(?:cat(?:alog(?:ue)?)?\.?[ \t]*(?:no\.?|nr\.?|number|num|#)?|catno|"
    r"source(?:[ \t]+(?:edition|release))?|original[ \t]+(?:edition|release)|"
    r"исходное[ \t]+издание|номер[ \t]+по[ \t]+каталогу|каталожный[ \t]+номер)[ \t]*[:=][ \t]*(.+?)[ \t]*$")
_NOTE_BARCODE_LINE = re.compile(r"(?im)^[ \t]*(?:barcode|upc|ean)(?:[ \t]*/[ \t]*(?:upc|ean))?[ \t]*(?:code|#|no\.?)?"
                                r"[ \t]*[:=][ \t]*(\d[\d \t-]{10,18}\d)\b")
_BBCODE = re.compile(r"\[/?[a-z*]+(?:=[^\]]*)?\]", re.I)
_RATE = re.compile(r"^(?:[A-Za-z]+[ -]?)?\d{2}\s*[-/]\s*\d{2,3}(?:[.,]\d)?$")      # "Qobuz 24-96": bits and kHz


# A word of a catalogue number: one with a digit ("8513-2", "QDISS0201CDD"), or a label's
# prefix in capitals ("MASS", "RR"); "Bandcamp", "Japan" or "kbps" end it
_CODE_WORD = re.compile(r"^(?=[^\s]*\d)[\w.\-/]+$|^[A-Z]{1,6}$")
_UNIT = re.compile(r"\d\s*(?:-?bits?|k?hz|kbps|kbit)", re.I)


def _note_code(seg):
    """The catalogue number at the end of one segment of a notes line ("Century Media 8513-2":
    "8513-2"), or "": a year ("CD 2005"), bits and kHz ("16-44", "24bit/96kHz"), a bit rate
    ("Bandcamp 320") or a short plain number is none."""
    words = []
    for w in reversed(seg.split()):
        if not _CODE_WORD.match(w):
            break
        words.insert(0, w)
    while words and not re.search(r"\d", words[0]) and mc._NOT_CATNO_WORD.match(words[0]):
        words.pop(0)                               # "CD 2005", "WEB FLAC 16-44": a format word
    code = " ".join(words)
    digits = re.sub(r"\D", "", code)
    if (not code or len(code) > 24 or len(words) > 4 or len(digits) < 3 or _UNIT.search(code)
            or any(mc._YEAR.match(w) or _RATE.match(w) for w in words)):
        return ""
    if not re.search(r"[A-Za-z]", code) and not re.search(r"\d[-/]\d", code) and len(digits) < 5:
        return ""                                  # "320", "1491": a rate or a count, not a number
    return code


def codes_from_notes(text):
    """Catalogue numbers and barcodes the notes name on a line of their own ("Cat#:",
    "Barcode:", a tracker's "source edition"). Typed by the ripper, like a folder name:
    they count as the folder name's (catalogue number (folder name)), never as a scan's."""
    catnos, barcodes = [], []
    text = _BBCODE.sub("", text or "")
    for m in _NOTE_CODE_LINE.finditer(text):
        for seg in reversed(re.split(r"\s+[–—-]\s+|\s*[,;]\s*|\s+/\s+", m.group(1))):
            code = _note_code(seg.strip(" .\t\"'"))
            if code and code not in catnos:
                catnos.append(code)
                break                              # the edition's number: the rest is its label
    for m in _NOTE_BARCODE_LINE.finditer(text):
        code = re.sub(r"\D", "", m.group(1))
        if mc.valid_barcode(code) and code not in barcodes:
            barcodes.append(code)
    return catnos[:3], barcodes[:2]


# Every document in an album folder may name the release: the ripper's notes and logs,
# the shop's or label's PDF booklet, a Word or HTML page saved with the download.
TEXT_DOC_EXT = {".txt", ".nfo", ".info", ".md", ".log", ".cue", ".m3u", ".m3u8", ".csv", ".json", ".xml",
                ".accurip", ".sfv", ".ffp", ".rtf", ".htm", ".html"}
OFFICE_DOC_EXT = {".docx", ".odt", ".doc", ".pdf"}
DOC_EXT = TEXT_DOC_EXT | OFFICE_DOC_EXT


def album_documents(album_dir, max_bytes=60_000_000):
    """The documents under the album folder (subfolders too), small ones first."""
    found = []
    for dirpath, _d, names in os.walk(album_dir):
        for n in sorted(names):
            p = Path(dirpath) / n
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if p.suffix.lower() in DOC_EXT and 0 < size <= max_bytes:
                found.append((size, str(p).lower(), p))
    return [p for _s, _n, p in sorted(found)]


def _decode(raw):
    """Text in whatever encoding the ripper wrote (EAC logs are UTF-16)."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    if raw.count(b"\x00") > len(raw) // 4:
        return raw.decode("utf-16-le", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _xml_text(xml):
    xml = re.sub(r"<(?:w:p|text:p|w:br|text:line-break|w:tab|text:tab)\b[^>]*>", "\n", xml)
    import html as _html
    return _html.unescape(re.sub(r"<[^>]+>", " ", xml))


def pdf_text(path, ocr=True, max_pages=40):
    """A PDF's text; pages that are only pictures (a scanned booklet) are rendered and
    read like any other scan, barcode digits and spines included."""
    import subprocess
    import tempfile
    text = ""
    if shutil.which("pdftotext"):
        try:
            text = subprocess.run(["pdftotext", "-layout", "-l", str(max_pages), str(path), "-"],
                                  capture_output=True, text=True, timeout=120).stdout
        except Exception:
            text = ""
    else:
        try:
            import pypdf
            text = "\n".join((pg.extract_text() or "") for pg in pypdf.PdfReader(str(path)).pages[:max_pages])
        except Exception:
            text = ""
    pages = text.count("\f") + 1
    if not ocr or len(re.sub(r"\s", "", text)) >= 80 * pages or not shutil.which("pdftoppm"):
        return text
    with tempfile.TemporaryDirectory() as tmp:
        try:
            subprocess.run(["pdftoppm", "-r", "300", "-gray", "-png", "-l", str(max_pages), str(path),
                            str(Path(tmp) / "page")], capture_output=True, timeout=600)
        except Exception:
            return text
        imgs = sorted(str(x) for x in Path(tmp).glob("page*.png"))
        if imgs:
            text += "\n" + ocr_text(imgs) + "\n" + ocr_spines(imgs) + "\n" + ocr_barcode_digits(imgs, 40)
    return text


def document_text(path, ocr=True):
    """The text of one document, whatever its kind."""
    import zipfile
    ext = Path(path).suffix.lower()
    try:
        if ext == ".pdf":
            return pdf_text(path, ocr=ocr)
        if ext in (".docx", ".odt"):
            with zipfile.ZipFile(path) as z:
                part = "word/document.xml" if ext == ".docx" else "content.xml"
                return _xml_text(z.read(part).decode("utf-8", errors="replace"))
        raw = Path(path).read_bytes()[:2_000_000]
        if ext == ".doc":
            import subprocess
            for tool in ("antiword", "catdoc"):
                if shutil.which(tool):
                    return subprocess.run([tool, str(path)], capture_output=True, text=True, timeout=60).stdout
            return " ".join(re.findall(r"[\x20-\x7e]{4,}", raw.decode("latin-1")))
        text = _decode(raw)
        if ext in (".log", ".cue", ".accurip", ".sfv", ".ffp", ".m3u", ".m3u8"):
            # rip logs and cue sheets: only the lines naming the release (a cue's CATALOG
            # is the barcode); drive models and checksums would be searched as codes
            return "\n".join(l for l in text.splitlines()
                              if re.search(r"catalog|barcode|upc|ean|label|^\s*(?:REM\s+)?(?:PERFORMER|TITLE)\b",
                                           l, re.I) and not re.search(r"\.(?:wav|flac|ape|mp3)\b", l, re.I))
        if ext in (".htm", ".html", ".xml"):
            text = _xml_text(re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text))
        elif ext == ".rtf":
            text = re.sub(r"\\'[0-9a-f]{2}|\\[a-z]+-?\d* ?|[{}]", " ", text)
        return text
    except Exception:
        return ""


_URL = re.compile(r"(?:https?://|www\.)\S+|\[/?(?:img|url|hide|spoiler|code|b|i|br|hr)[^\]]*\]", re.I)


def documents_text(album_dir, ocr=True, db=None):
    """Every document in the folder as one text (cached while the files are unchanged)."""
    import hashlib
    docs = album_documents(album_dir)
    if not docs:
        return ""
    key = hashlib.md5(("docs-2|" + "|".join(f"{p}:{p.stat().st_size}:{int(p.stat().st_mtime)}"
                                            for p in docs) + f"|{ocr}").encode()).hexdigest()
    # links and forum markup out: an image host's hash would be read as a barcode
    return _cached(db, "docs", key, lambda: "\n".join(
        f"({p.name})\n{_URL.sub(' ', document_text(p, ocr=ocr))}" for p in docs))


DISC_DIR = re.compile(r"^(?:cd|disc|disk|dvd|side)\s*[-_.]?\s*\d+\b|^\d{1,2}$", re.I)


def album_dirs(top):
    """The album folders under an artist folder, however deep (Rotting Christ/Albums/
    1993 - Thy Mighty Contract): every folder holding audio, a disc folder ("Disc 02",
    "CD1") counting as its parent's. Hidden, "_" and 2delete folders are skipped."""
    top, audio = Path(top), []
    for dirpath, dirs, names in os.walk(top):
        dirs[:] = sorted(d for d in dirs if not d.startswith((".", "_")) and d != "2delete")
        d = Path(dirpath)
        if d != top and any(Path(n).suffix.lower() in AUDIO_EXT and not n.startswith(".") for n in names):
            audio.append(d)
    owners = _owners(top, audio, DISC_DIR)
    merged = {deluxe_sibling(d) for d in owners} - {None}
    return [d for d in owners if d not in merged]       # read with the album it completes


# "2019 - The Heretics (Deluxe Edition)" beside "2019 - The Heretics": the deluxe tracks
# saved apart from the rest of the album
_DELUXE_DIR = r"\s*[\(\[]\s*deluxe(?:\s+edition)?\s*[\)\]]\s*$"


def _audio_files(d):
    try:
        return sorted(p for p in Path(d).iterdir()
                      if p.is_file() and p.suffix.lower() in AUDIO_EXT and not p.name.startswith("."))
    except OSError:
        return []


def deluxe_sibling(album_dir):
    """The sibling folder holding the rest of this album, or None: "<this folder's name>
    (Deluxe Edition)", whose files have the same album artist and the same track total
    (the tags' TRACKTOTAL or TagMender's "write_failed: ... 10 of 12" marker) as these,
    the two folders together holding exactly that many tracks, none twice (Rotting Christ -
    The Heretics: tracks 1-10 here, 11 and 12 in the deluxe folder)."""
    album_dir = Path(album_dir)
    rx = re.compile(re.escape(album_dir.name) + _DELUXE_DIR, re.I)
    try:
        sib = next((d for d in sorted(album_dir.parent.iterdir()) if d.is_dir() and d != album_dir
                    and rx.fullmatch(d.name)), None)
    except OSError:
        return None
    if sib is None:
        return None
    mine, theirs = _audio_files(album_dir), _audio_files(sib)
    if not mine or not theirs:
        return None
    tags = [read_file(p) for p in mine + theirs]
    artists = {mc.fold(t["artist"]) for t in tags}
    totals = {t["tracktotal"] or t["write_failed_total"] for t in tags}
    nums = [t["num"] for t in tags]
    if (len(artists) != 1 or "" in artists or len(totals) != 1 or totals == {0}
            or len(tags) != next(iter(totals)) or 0 in nums or len(set(nums)) != len(nums)
            or any(t["disc"] > 1 for t in tags)):
        return None
    return sib


def vinyl_marks(album_dir, audio_names, sides_count=True):
    """Why the folder looks ripped from vinyl whatever its name says ("[WEB]"), or "": side
    letters on two sides or more in the audio files' names ("A1 Title", "... side B") or the cue
    sheet's (FILE "... side A).wv", TITLE "B1. Eternal Return"), pictures of each side
    ("SUNN200, side A.png": needle-drop screenshots) or a picture named after a needle drop
    (Sunn O))) & Ulver - Terrestrials [WEB]: a 32/192 rip of the LP). sides_count=False: only
    the needle-drop picture, as a tape has sides A and B too."""
    def sides(names, pattern):
        return {m.group(1).upper() for n in names for m in [re.search(pattern, n, re.I)] if m}
    side_word = r"(?<![a-z])side[ _-]*([a-h])(?![a-z0-9])"
    stems = [Path(n).stem for n in audio_names]
    if sides_count and len(sides(stems, r"^([a-h])\d{1,2}(?=[ ._)-])") | sides(stems, side_word)) >= 2:
        return "the audio files are named by vinyl side"
    cue_names, images = [], []
    for dirpath, _d, names in os.walk(album_dir):
        for n in names:
            p = Path(dirpath) / n
            if p.suffix.lower() == ".cue":
                try:
                    cue_names += re.findall(r'(?im)^\s*(?:FILE|TITLE)\s+"([^"]+)"', _read_log(p))
                except OSError:
                    pass
            elif p.suffix.lower() in IMAGE_EXT:
                images.append(p.stem)
    if sides_count and len(sides(cue_names, r"^([a-h])\d{1,2}[.)]?\s") | sides(cue_names, side_word)) >= 2:
        return "the cue sheet numbers the tracks by vinyl side"
    if sides_count and len(sides(images, side_word)) >= 2:
        return "the folder has pictures of each vinyl side"
    if any(re.search(r"needle[ _-]?drop", n, re.I) for n in images):
        return "the folder has a needle-drop picture"
    return ""


def enhanced_marks(album_dir, logs=()):
    """Why the disc ripped was an Enhanced CD (CD-Extra), or "": a CUETools/AccurateRip report
    of its data track, the data folder copied off it ("Data/CDPLUS"), or a rip log whose TOC
    has the data track (Pestilence - Mind Reflections: Metal Mind's 2006 Enhanced CD)."""
    if any(log.get("data_track") for log in logs):
        return "the rip log lists the disc's data track"
    for dirpath, dirs, names in os.walk(album_dir):
        if any(d.casefold() == "cdplus" for d in dirs):
            return "the folder holds the disc's CDPLUS data folder"
        for n in names:
            if n.lower().endswith(".accurip"):
                try:
                    if re.search(r"CD-Extra data track", _read_log(Path(dirpath) / n), re.I):
                        return "the AccurateRip report names the disc's CD-Extra data track"
                except OSError:
                    pass
    return ""


# The original names a checker logged (audiochecker, auCDtect): "Artist - Album - 01 Title.flac"
# is how Bandcamp names its downloads (Hypno5e - Des Deux L'Une Est L'Autre)
_BANDCAMP_NAME = re.compile(r"^\s*(.+?) - (.+?) - \d{2} (?![-.\s]).+\.(?:flac|wav|aiff?|mp3|m4a|alac)\s*$", re.I)


def bandcamp_names(album_dir, artist, n_tracks):
    """True when a checker's log under the folder names most tracks as Bandcamp does, under
    this album's artist."""
    found = 0
    for p in Path(album_dir).rglob("*.log"):
        if not re.search(r"audiochecker|aucdtect", p.name, re.I):
            continue
        try:
            text = _read_log(p)
        except OSError:
            continue
        names = [seg for line in text.splitlines() for seg in re.split(r"-=-|[\\/]|\t", line)]
        found = max(found, sum(1 for seg in names for m in [_BANDCAMP_NAME.match(seg)]
                               if m and mc.fold(m.group(1)) == mc.fold(artist)))
    return found >= 2 and found * 2 >= n_tracks


def _owners(top, audio, disc_re):
    """Each audio folder's album folder: a disc folder belongs to its parent, and so does any
    folder inside a folder that holds audio itself (Rotting Christ - A Dead Poem: its bonus
    disc "1997 - Darkness We Feel (free bonus CD)" sits inside the album's folder)."""
    have = set(audio) | {d.parent for d in audio if disc_re.match(d.name) and d.parent != top}
    owners = []
    for d in audio:
        owner = d.parent if disc_re.match(d.name) and d.parent != top else d
        up = owner.parent
        while up != top and top in up.parents:
            if up in have:
                owner = up
            up = up.parent
        if owner not in owners:
            owners.append(owner)
    return sorted(owners)


_ALBUM_DISC = re.compile(r"^(.*?)[\s\-\u2013\u2014:,_]*[(\[]?\s*(?:cd|disc|disk)\s*[-_.#]?\s*(\d{1,2})"
                         r"(?:\s*(?:of|/)\s*\d{1,2})?\s*[)\]]?\s*$", re.I)


def album_tag_discs(infos) -> tuple:
    """({path: disc}, title) when the files without a disc tag name their disc in their album
    tags ("The Principle Of Doubt (Compilation) CD 1" and "... CD 2"): every one of them does,
    all name the same album, and at least two discs. Else ({}, "")."""
    untagged = [(p, i) for p, i in infos if not i.get("disc")]
    if len(untagged) < 2:
        return {}, ""
    found, bases = {}, set()
    for p, i in untagged:
        m = _ALBUM_DISC.match(i.get("album") or "")
        if not m or not m.group(1).strip() or not int(m.group(2)):
            return {}, ""
        found[p] = int(m.group(2))
        bases.add(m.group(1).strip())
    if len({b.casefold() for b in bases}) != 1 or len(set(found.values())) < 2:
        return {}, ""
    return found, min(bases)


def read_album(album_dir, artist_folder, album_folder, exclude=(), blind=False):
    """blind=True (validate.py): the files were tagged already, so their release IDs,
    ISRCs, label, catalogue number, barcode, country, date and year came FROM a release
    and would only confirm it. They are ignored; what is left is what an untagged copy
    has: names, track lengths, the audio, the disc TOC, the scans and the folder name."""
    files = []
    exclude = {str(x) for x in exclude}
    album_dir = Path(album_dir)
    sibling = deluxe_sibling(album_dir)        # the deluxe tracks saved in a folder of their own
    for top_dir in (album_dir, sibling) if sibling else (album_dir,):
        for dirpath, _d, names in os.walk(top_dir):
            for n in names:
                p = Path(dirpath) / n
                if p.suffix.lower() in AUDIO_EXT and str(p) not in exclude:
                    files.append(p)
    info = {p: read_file(p) for p in files}
    names_from_files([(p, info[p]) for p in files])
    # the release IDs the tags' track total was written with (read by _total_miscounted only).
    # Empty when blind: the IDs are the validation's truth, and a waiver that fires only when
    # the pick is the hidden release would turn an agreeing review into an agreeing auto
    # (a blind run reads the total against the databases' own count of the pick's pressing).
    total_releases = (set(), set()) if blind else tag_release_ids(info.values())
    if blind:
        for i in info.values():
            for k in BLIND_KEYS:
                i[k] = ""
            i["year"] = 0                                  # the folder's year is used instead
    files, dropped_formats = _drop_duplicate_copies(files, info, album_dir)
    files, dropped_tracks = _drop_duplicate_tracks(files, info)
    infos = [(p, info[p]) for p in files]

    # Files without a disc tag take their disc from their folder: "CD1/",
    # "CD2/" ... in natural order. Without this, two disc folders that each
    # start at track 1 interleave (1-1, 2-1, 1-2, 2-2 ...).
    # (the deluxe sibling's tracks continue the album's disc, after its own)
    def rel(p):
        return p.relative_to(album_dir).as_posix() if album_dir in p.parents else "\uffff" + p.name
    folders = sorted({p.parent for p in files if album_dir in p.parents},
                     key=lambda d: _natural_key(d.relative_to(album_dir).as_posix()))
    folder_disc = {d: i + 1 for i, d in enumerate(folders)} if len(folders) > 1 else {}
    # versions of the album in folders of their own ("2.0/", "5.0/": an SACD's stereo and
    # multichannel areas): each folder its own disc, whatever disc tag the files carry (each
    # version's files say disc 1)
    versions = version_folders(infos, folders)
    kinds = {d: version_kind(v[2], [(p, i) for p, i in infos if p.parent == d]) for d, v in versions.items()}
    if len(versions) == 1 and next(iter(kinds.values()))[1] == "audio":
        # one layout-named folder alone ("Stereo 24-96/" of a download): a version of a disc only
        # when its name's disc word, an SACD_AREA tag, DSD or its media tag says which disc
        versions, kinds = {}, {}
    # Files of one folder without disc tags whose album tags name their disc ("Title CD 1" and
    # "Title CD 2", as EAC writes each disc of a set): that disc. Without it, the two discs'
    # tracks 1, 1, 2, 2 ... interleave and are lined up with the wrong tracks of the release.
    tag_disc, tag_title = ({}, "") if versions else album_tag_discs(infos)
    for p, i in infos:
        i["disc_eff"] = folder_disc.get(p.parent, 1) if versions else \
            i["disc"] or folder_disc.get(p.parent) or tag_disc.get(p, 1)
    # (or, filed already as discs of their release, by the layout each disc's tags name)
    if versions:
        found = {(folder_disc.get(d, 1) if folder_disc else 1): v for d, v in versions.items()}
        kinds = {(folder_disc.get(d, 1) if folder_disc else 1): k for d, k in kinds.items()}
    else:
        found = version_discs_by_tags(infos)
        kinds = {d: version_kind(v[2], [(p, i) for p, i in infos if i["disc_eff"] == d]) for d, v in found.items()}
    version_discs = {d: v[0] for d, v in found.items()}
    version_labels = {d: v[1] for d, v in found.items()}
    version_kinds = {d: k[0] for d, k in kinds.items()}
    # (a file numbered 0, "00 - Hidden.flac": the hidden track before track 1, first on its disc)
    infos.sort(key=lambda x: (x[1]["disc_eff"], x[1]["num"] or (0 if x[1].get("num0") else 9999),
                              _natural_key(rel(x[0]))))

    def top(key):
        c = Counter(i[key] for _p, i in infos if i[key])
        return c.most_common(1)[0][0] if c else ""

    m = re.match(r"\s*(\d{4})\s*-\s*(.*)", album_folder)
    year = top("year") or (int(m.group(1)) if m else 0)
    title = tag_title or top("album") or (m.group(2) if m else album_folder)
    n_discs = len({i["disc_eff"] for _p, i in infos}) or 1
    folder_hints = mc.catno_hints(album_folder) + [h for h in mc.catno_hints(title)
                                                   if h not in mc.catno_hints(album_folder)]
    folder_match = set(mc.catno_hints(album_folder, variants=True)) | set(
        mc.catno_hints(title, variants=True))
    # a Japanese set's numbers as a range with "-" ("SICP 10151-4"): the "~" form MusicBrainz
    # and Discogs list it by is the folder's too, and searched as well (never its single numbers:
    # mc.catno_range)
    tilde = [f for h in folder_hints for f in mc.catno_range_forms(h)]
    folder_match |= set(tilde)
    folder_hints += [f for f in tilde if f not in folder_hints]
    # An EAC/XLD log: the copy was ripped from a CD. Only a log whose TOC fits the files
    # counts; one that fits no disc was made of another rip (R10), and its TOC, title and
    # paths say nothing of these files.
    logs, foreign_logs = fitting_logs(infos, rip_logs(album_dir))
    log_toc = {}
    for log in logs:
        if log["toc"] and log["toc"] not in log_toc.values():
            log_toc[len(log_toc) + 1] = log["toc"]
    notes = notes_text(album_dir)
    note_catnos, note_barcodes = codes_from_notes(notes)
    # catalogue numbers the ripper typed: the edition its notes name, the folders its log
    # ripped to, the log's own name. Read as the folder name's (R3). Only a rip log whose TOC
    # fits the files: a checker's log (audiochecker, auCDtect) or one without a TOC may name
    # the downloader's own folders.
    ripped = [log for log in logs if log["toc"]]
    log_codes = [c for log in ripped for c in log["codes"]]
    typed = {mc.norm_code(c) for c in note_catnos + log_codes}
    typed_search = [c for c in note_catnos + log_codes if c not in folder_hints]
    tags_barcodes = {mc.norm_code(i["barcode"]) for _p, i in infos if i["barcode"]}
    headers = [log["header"] for log in ripped if log.get("header")]
    cdtocs = tag_tocs(infos)
    tocs = merge_tocs(infos, log_toc, cue_tocs(album_dir, infos), cdtocs)
    hint, hint_from = _media_hint(album_dir, album_folder, files, infos, top("artist") or artist_folder,
                                  bool(log_toc))
    # The files name a release in their ID tags, and TagMender did not write them: another
    # tagger did (Picard, an older tool, the shop). Their barcode, ISRCs, country, date and
    # label came FROM that release and only confirm it; they never outrank the folder name
    # (match_core: TAGS_BARCODE, classify's "tags name" review). Tags TagMender wrote itself
    # (tagmender_wrote: its job history) keep counting as the copy's own (an album tagged in
    # place, re-processed when its watched folder is given a profile that moves it).
    # A MusicBrainz or Discogs release link in the tags (a comment, a URL tag) names a
    # release the same way: whoever tagged the files wrote it.
    id_files, foreign_ids = [], False
    for p, i in infos:
        link_mb, link_dg, _urls = source_links([i])
        mbs = ({i["mb_albumid"]} if i.get("mb_albumid") else set()) | link_mb
        dgs = ({i["discogs_releaseid"]} if i.get("discogs_releaseid") else set()) | link_dg
        if len(mbs) > 1 or len(dgs) > 1:
            foreign_ids = True             # links to another release than its ID tag: not TagMender's
        if mbs or dgs:
            id_files.append((p, min(mbs) if mbs else "", min(dgs) if dgs else ""))
    by_tagger = bool(id_files) and not blind and (foreign_ids or not tagmender_wrote(id_files))
    tag_mb_links, tag_discogs_links, tag_urls = source_links([i for _p, i in infos])
    tag_release_mb, tag_release_discogs = tag_release_ids([i for _p, i in infos])
    return {
        "tags_by_tagger": by_tagger,
        "artist": top("artist") or artist_folder, "title": title,
        "year": year or None, "discs": n_discs,
        "dropped_formats": dropped_formats, "dropped_tracks": dropped_tracks,
        "tracks": [{"disc": i["disc_eff"], "num": i["num"], "title": i["title"],
                    "secs": i["secs"], "path": str(_p)} for _p, i in infos],
        # the tags' barcode, else one the ripper's notes name ("Barcode: 5013929020160")
        "barcodes": tags_barcodes,
        "tag_barcodes": set(tags_barcodes),
        # a barcode the ripper's notes name ("Barcode: 5013929020160"): typed, perhaps from another
        # edition's page, so never the tags' barcode; the documents step searches it as read print
        "note_barcodes": sorted({mc.norm_code(b) for b in note_barcodes} - tags_barcodes),
        # the tags a barcode came from when it is not Picard's BARCODE (UPC, EAN, ASIN)
        "barcode_sources": sorted({i["barcode_from"] for _p, i in infos if i["barcode"] and i.get("barcode_from")}),
        "asins": sorted({i["asin"] for _p, i in infos if i.get("asin")}),
        # catalogue numbers from the tags, plus any written in the folder name
        # or album title ("[20bit RM DOMO 71063-2]")
        "catnos": ({mc.norm_code(c) for _p, i in infos for c in mc.split_codes(i["catno"])}
                   | {mc.norm_code(h) for h in folder_match} | typed),
        "catnos_from_folder": {mc.norm_code(h) for h in folder_match} | typed,
        # ... and those from the tags (mc.tagger_catno: another tagger's word only)
        "tag_catnos": {mc.norm_code(c) for _p, i in infos for c in mc.split_codes(i["catno"])},
        # ... of which the ripper typed in notes or a log (told apart from the folder's in the notes)
        "catnos_typed": typed - {mc.norm_code(h) for h in folder_match},
        "catnos_from_logs": sorted({mc.norm_code(c) for c in log_codes}),
        "catno_search": sorted({c for _p, i in infos for c in mc.split_codes(i["catno"])} | set(folder_hints)
                               | set(typed_search)),
        "pressing_year": mc.pressing_year(album_folder) or mc.pressing_year(title) or _log_year(album_dir, logs),
        "media_hint": hint, "media_hint_from": hint_from,
        # the album named in the rip logs' headers ("Jesu / Conqueror (Japanese Bonus Disc)"):
        # searched too when the folder's title finds nothing sure (one disc of a box)
        "log_titles": [h for h in headers if mc.fold(h[1]) != mc.fold(title)],
        "foreign_logs": [Path(log["path"]).name for log in foreign_logs],
        # cue sheets whose tracks fit no disc of the files: their CATALOG is typed, not print
        "foreign_cues": foreign_cues(album_dir, infos),
        "enhanced": enhanced_marks(album_dir, logs),
        "tracktotal": _track_total(infos),
        "folder": album_folder, "artist_folder": artist_folder,
        "tag_label": top("label"), "tag_country": top("country"),
        # labels to search with the catalogue number: the tags', then the ripper's notes'
        "search_labels": search_labels(top("label"), notes),
        "tag_media": mc.media_kind(top("media")), "tag_date": top("date"),
        "audio": audio_evidence([i for _p, i in infos]),
        # per disc: the rip log's TOC first (the disc itself, its pregaps and the data track
        # of an Enhanced CD), then a cue sheet's, then the one rebuilt from the tracks' lengths
        "tocs": tocs,
        "rip_log_toc": bool(log_toc),
        # discs whose hidden track before track 1 (a file numbered 0) a rip log's or CDTOC tag's
        # TOC shows in its first pregap: the disc itself has it (match_core.with_pregap)
        "htoa_toc_discs": htoa_toc_discs(infos, list(log_toc.values()) + list(cdtocs.values())),
        "scans": scan_evidence(album_dir),
        "packaging": packaging_evidence(album_dir),
        "isrcs": [i.get("isrc") for _p, i in infos if i.get("isrc")],
        "store_tracks": sorted({i["store_track"] for _p, i in infos if i.get("store_track")}),
        "credit_text": " ".join(sorted({i.get("credit") or "" for _p, i in infos})) + " " + notes,
        # shop pages the files came from: MusicBrainz's /url lookup ties them to a release
        # (link_releases: "source link")
        "tag_mb_links": tag_mb_links, "tag_discogs_links": tag_discogs_links, "tag_urls": tag_urls,
        # release IDs the files were tagged with, and the release links in their tags (another
        # tagger's word, like the ID tags): fetched and scored, not evidence
        "tag_release_mb": tag_release_mb | tag_mb_links,
        "tag_release_discogs": tag_release_discogs | tag_discogs_links,
        "total_releases": total_releases,
        # {disc: "stereo" | "multichannel" | "atmos" | "mono"}: the album's discs are versions of
        # one disc in folders named after their channel layout (match_core.version_view)
        "version_discs": version_discs,
        # {disc: "Stereo" | "5.1" | "Quad" | "Atmos" ...}: each version's name (%_version%)
        "version_labels": version_labels,
        # {disc: "sacd" | "video" | "cd" | ""}: the kind of disc each version's folder name or
        # audio says it was ripped from (_audio_kind); "" when nothing says
        "version_kinds": version_kinds,
        # {disc: "name" | "media" | "tags" | "audio"}: what says so (version_kind) - "audio" (the rate,
        # depth and channels alone) never proves which disc a version is (match_core.version_view)
        "version_kind_by": {d: k[1] for d, k in kinds.items()},
        # {disc: release IDs its files' tags name (MusicBrainz, Discogs)}: a media tag counts as the
        # disc's evidence only when they name the release it is scored against (version_view)
        "version_release_ids": {d: sorted({str(x) for _p, i in infos if i["disc_eff"] == d
                                          for x in (i.get("mb_albumid"), i.get("discogs_releaseid")) if x})
                                for d in kinds},
        # {disc: True} for a disc whose audio is no CD's (an SACD_AREA tag, hi-res or DSD audio,
        # more than two channels, a DVD or Blu-ray word in its folder name)
        "version_hires": {d: version_kinds[d] in ("sacd", "video") or
                          all(_not_cd_audio(p, i) for p, i in infos if i["disc_eff"] == d) for d in version_discs},
    }


def version_folders(infos, folders=()):
    """{folder: (layout, label, disc kind)} (match_core.parse_version) when the album's files are
    versions of one disc in folders named after their channel layout ("2.0/" and "5.0/",
    "Stereo 24-96/" and "DTS 5.1/"; or the album folder itself when it is one and holds them
    all): every folder holding its files is one, each a different version, and no file's
    channel count or SACD_AREA tag says otherwise. {} otherwise. folders: the album's
    subfolders holding files (read_album)."""
    dirs = sorted({p.parent for p, _i in infos})
    if not dirs or (len(dirs) > 1 and set(dirs) != set(folders or ())):
        return {}
    out = {}
    for d in dirs:
        got = mc.parse_version(d.name)
        if not got or any(mc.fold(i.get("album") or "") == mc.fold(d.name) for _p, i in infos):
            return {}                    # (an album called "Stereo" in its own folder)
        lay = got[0]
        mine = [i for p, i in infos if p.parent == d]
        if len({i.get("disc") for i in mine if i.get("disc")}) > 1:
            return {}                    # (a whole set in one version's folder: its discs stand)
        heard = {mc.layout_of_channels(i.get("channels"), i.get("sacd_area")) for i in mine}
        heard.discard("")
        if lay == "mono" and not any(i.get("sacd_area") for i in mine):
            heard.discard("stereo")     # (mono as two identical channels)
        if lay == "atmos":
            heard.discard("multichannel")   # (an immersive mix's bed, as ripped)
        if heard and heard != {lay}:
            return {}                    # (the audio is another layout than the folder's name)
        out[d] = got
    if len({mc.fold(v[1]) for v in out.values()}) != len(out):
        return {}
    return out


def version_discs_by_tags(infos):
    """{disc: (layout, label, "")} when every file of each disc names its layout in its
    SACD_AREA tag ("Stereo", "Multichannel") or its media tag ("Hybrid SACD (SACD layer, 2
    channels)", as TagMender wrote it), a different one each: versions of one disc already
    filed as discs of their release ("Disc 02/", "Disc 03/"). {} otherwise; never from the
    channel count alone."""
    by = {}
    for _p, i in infos:
        lay = mc.layout_of_channels(0, i.get("sacd_area")) or mc.medium_layout(i.get("media"))
        by.setdefault(i["disc_eff"], set()).add(lay)
    if not by or any(len(v) != 1 or "" in v for v in by.values()):
        return {}
    out = {d: next(iter(v)) for d, v in by.items()}
    if len(set(out.values())) != len(out):
        return {}
    return {d: (lay, lay.title(), "") for d, lay in out.items()}


def version_kind(name_kind, files):
    """(kind, what says so) of the disc a version's files were ripped from: the disc word in
    its folder's name ("DVD 5.1", "BD Stereo": "name"), else the media tag every file carries
    ("DVD-Video", "Blu-ray", "Hybrid SACD (SACD layer, ...)" as TagMender wrote it: "media"),
    else the audio (_audio_kind: "tags" for an SACD_AREA tag or DSD, "audio" otherwise)."""
    if name_kind:
        return name_kind, "name"
    media = {mc.medium_type(i.get("media")) for _p, i in files}
    if len(media) == 1 and next(iter(media)):
        return media.pop(), "media"
    kind = _audio_kind(files)
    sure = any(i.get("sacd_area") for _p, i in files) or any(Path(p).suffix.lower() in (".dsf", ".dff")
                                                            for p, _i in files)
    return kind, ("tags" if sure else "audio")


def _audio_kind(files):
    """The kind of disc these files' audio says they were ripped from: "sacd" (an SACD_AREA tag,
    DSD, a 44.1 kHz-family rate past a CD's), "video" (a 48 kHz-family rate with more than a
    CD's depth or channels, or past 48 kHz: a DVD or Blu-ray), "cd" (16-bit/44.1 kHz stereo)
    or "" (48 kHz/16-bit stereo, mixed: nothing says)."""
    if not files:
        return ""
    infos = [i for _p, i in files]
    if any(i.get("sacd_area") for i in infos) or any(Path(p).suffix.lower() in (".dsf", ".dff") for p, _i in files):
        return "sacd"
    rates = {int(i.get("rate") or 0) for i in infos}
    if len(rates) != 1 or not next(iter(rates)):
        return ""
    rate = rates.pop()
    deep = any((i.get("bits") or 0) > 16 for i in infos)
    wide = any((i.get("channels") or 0) > 2 for i in infos)
    if rate % 44100 == 0 and rate > 44100:
        return "sacd"
    if rate % 48000 == 0 and (rate > 48000 or deep or wide):
        return "video"
    if rate == 44100 and not deep and not wide:
        return "cd"
    return ""


def _not_cd_audio(p, i):
    """The file's audio is no CD's: an SACD_AREA tag, a DSD file, a sample rate or depth past a
    CD's, or more than two channels."""
    return bool(i.get("sacd_area") or Path(p).suffix.lower() in (".dsf", ".dff")
                or (i.get("rate") or 0) > 48000 or (i.get("bits") or 0) > 16 or (i.get("channels") or 0) > 2)


def _log_year(album_dir, logs):
    """The edition year a ripper typed into the folder a rip log wrote to ("[2006 - reissue]"),
    from a log without a TOC that a CUETools report proves is of these files (a log with a
    TOC that fits is read for codes, not years). None otherwise; the folder name's year
    always comes first."""
    for log in logs:
        if not log["toc"] and log.get("out_parts") and accurip_confirms(album_dir, log):
            for part in log["out_parts"]:
                y = mc.pressing_year(part)
                if y:
                    return y
    return None


def _media_hint(album_dir, album_folder, files, infos, artist, rip_log):
    """What the copy was made from, and what says so: the folder name first ("[Vinyl Rip]",
    "[2CD]"), then signs the files carry whatever the folder is called (R4). Vinyl signs
    (sides, needle-drop pictures) outrank a "[WEB]" folder; a shop's comments in the tags or
    Bandcamp's original names in a checker's log make a download, unless a rip log that
    fits the files says it came off a CD. A rip log that fits the files outranks the vinyl
    signs too, and sides alone (named "A1 ...", "side A.jpg") make vinyl only of a folder
    named "[WEB]": a tape has sides, and side pictures may be scans of another pressing."""
    hint = mc.media_hint(album_folder)
    if hint in ("", "digital") and not rip_log:
        vinyl = vinyl_marks(album_dir, [p.name for p in files], sides_count=hint == "digital")
        if vinyl:
            return "vinyl", vinyl
    if hint or rip_log:
        return hint, ("folder name" if hint else "")
    shops = Counter(i.get("download_marker") for _p, i in infos if i.get("download_marker"))
    if shops and shops.most_common(1)[0][1] * 2 >= len(infos):
        return "digital", f"{shops.most_common(1)[0][0]} comments or URLs in the tags"
    if bandcamp_names(album_dir, artist, len(infos)):
        return "digital", "Bandcamp's file names in a checker's log"
    return "", ""


def _track_total(infos):
    """The album's track count as the tags have it: TRACKTOTAL ("3/12"), every file of a disc
    agreeing, summed over discs whose numbering starts again (1-10, 1-10: 20), counted once
    when the numbers run on over the discs (1-10, 11-20, each saying 20); else TagMender's
    write_failed marker ("saved 10 of 12 files"), which counts the whole album's files and is
    stamped on each, taken once. 0 when the files do not say, or say it in a way that adds up
    to neither."""
    per_disc = {}
    for _p, i in infos:
        per_disc.setdefault(i.get("disc_eff", 1), []).append(i)
    totals = {d: {i.get("tracktotal") or 0 for i in fs} for d, fs in per_disc.items()}
    if per_disc and all(len(v) == 1 and 0 not in v for v in totals.values()):
        total = {d: next(iter(v)) for d, v in totals.items()}
        nums = {d: [i.get("num") or 0 for i in fs] for d, fs in per_disc.items()}
        discs = sorted(per_disc)
        # One album-wide total over discs numbered from 1 (Voivod - The Wake: 1-8 and 1-11,
        # every file saying 19). Only when the per-disc reading cannot hold: discs of
        # different track counts, each numbered 1..n without a gap. Discs of equal counts
        # (1-5 and 1-5 saying 10) are Picard's per-disc totals with files missing: summed.
        counts = [len(nums[d]) for d in discs]
        if (len(discs) > 1 and len(set(total.values())) == 1 and len(set(counts)) == len(counts)
                and all(sorted(nums[d]) == list(range(1, len(nums[d]) + 1)) for d in discs)
                and sum(counts) == total[discs[0]]):
            return total[discs[0]]
        runs_on = len(discs) > 1 and all(min(nums[b]) > max(nums[a]) for a, b in zip(discs, discs[1:]))
        if runs_on and len(set(total.values())) == 1 and max(max(n) for n in nums.values()) <= total[discs[0]]:
            return total[discs[0]]                 # numbered straight through: the album's total
        if len(discs) == 1 or (not runs_on and all(max(nums[d]) <= total[d] for d in discs)):
            return sum(total.values())
        return 0
    if any(i.get("tracktotal") for _p, i in infos):
        return 0                                   # the tags disagree, or some have none
    return max((i.get("write_failed_total") or 0 for _p, i in infos), default=0)


def copy_source(album):
    """What the files were made from, as far as they tell: "vinyl", "cd" or "digital"
    (the folder name first, then a rip log / cue sheet / disc TOC or whole CD sectors for a
    CD, a shop's track ID or CD-quality tracks that are not CD sectors for a download);
    "" when they do not say (hi-res audio alone may be a vinyl rip or a download)."""
    hint = album.get("media_hint") or ""
    if hint:
        return hint
    audio = album.get("audio") or {}
    if album.get("tocs") or audio.get("cd_frames"):
        return "cd"
    if album.get("store_tracks") or audio.get("not_cd"):
        return "digital"
    return ""


def _sure_if_no_album_only(final, album, cfg):
    """FINAL, or the sure pick it set aside (match_core.combine, Discogs's pressings tied) when
    the album could not be tagged as "album known" after all: that would leave it unresolved. So
    too when the tied pressings are all of another media than your copy: a sure pick stands."""
    if final.get("sure_elsewhere"):
        ao = mc.album_only(final, cfg, tie_source(album, final))
        if not ao or ao.get("other_kind"):
            return final["sure_elsewhere"]
    return final


def tie_source(album, final):
    """What the files were made from, for the tied pressings album_only keeps: copy_source,
    but not "cd" when nothing proves it, as file_clues has it: hi-res audio beside a rip
    log (an earlier CD rip), or whole CD sectors only when a download of the album is whole
    sectors too and no rip log or scans back the CD up (Voivod - Lost Machine - Live)."""
    source = copy_source(album)
    if source != "cd" or album.get("media_hint"):
        return source
    if (album.get("audio") or {}).get("hires"):
        return ""
    backed = album.get("rip_log_toc") or (album.get("scans") or {}).get("physical")
    if not backed and any(mc._sector_aligned_download(s) for s in (final or {}).get("scored") or ()):
        return ""
    return source


def audio_evidence(infos):
    """What the audio itself says about its source.

    hires: more than 16 bits or 48 kHz, so not from a CD (a vinyl rip or a download).
    sacd: every file is DSD, an SACD area, or 88.2/176.4 kHz (an SACD's DSD64 converted: MED-3).
    cd_frames: every lossless track at 44.1 kHz is a whole number of CD sectors
    (588 samples), which a CD rip is and a download almost never is."""
    lossless = [i for i in infos if i.get("lossless") and i.get("rate")]
    hires = bool(infos) and any(i.get("bits", 0) > 16 or i.get("rate", 0) > 48000 for i in infos)
    cd_frames = (len(lossless) >= 2 and len(lossless) == len(infos)
                 and all(i["rate"] == 44100 and i.get("bits", 16) <= 16 and i["samples"]
                         and i["samples"] % CD_FRAME == 0 for i in lossless))
    # lossless CD-quality tracks that are NOT whole sectors: not a CD rip, so a download
    not_cd = (len(lossless) >= 2 and len(lossless) == len(infos) and not hires
              and all(i["rate"] == 44100 and i["samples"] for i in lossless)
              and sum(1 for i in lossless if i["samples"] % CD_FRAME == 0) == 0)
    # MED-3: an SACD's audio - DSD, an SACD_AREA tag, or DSD64 converted to 88.2 or 176.4 kHz
    sacd = bool(infos) and all(i.get("sacd_area") or i.get("rate") in (88200, 176400)
                               or (i.get("rate") or 0) >= 2822400 for i in infos)
    # every file lossless (FLAC, ALAC, WAV...): a download sold only as MP3 is not this copy
    every_lossless = bool(infos) and all(i.get("lossless") for i in infos)
    return {"hires": hires, "cd_frames": cd_frames, "not_cd": not_cd, "sacd": sacd, "lossless": every_lossless}


def disc_tocs(infos):
    """The table of contents of each disc, rebuilt from the tracks' exact
    lengths (as freeDB/MusicBrainz disc IDs are), for CD rips only:
    {disc: "1 N leadout offset1 ... offsetN"} in MusicBrainz's toc form."""
    out = {}
    discs = {}
    for _p, i in infos:
        discs.setdefault(i.get("disc_eff", 1), []).append(i)
    for d, tracks in discs.items():
        if not tracks or len(tracks) > 99:
            continue
        if not all(i.get("lossless") and i.get("rate") == 44100 and i.get("samples")
                   and i["samples"] % CD_FRAME == 0 for i in tracks):
            continue
        offsets, pos = [], 150
        hidden = htoa_file(tracks)
        if hidden is not None:
            # a hidden track before track 1 (a file numbered 0): the first track's pregap
            pos += hidden["samples"] // CD_FRAME
            tracks = [i for i in tracks if i is not hidden]
        for i in tracks:
            offsets.append(pos)
            pos += i["samples"] // CD_FRAME
        out[d] = " ".join(str(x) for x in [1, len(tracks), pos] + offsets)
    return out


def merge_tocs(infos, *sources):
    """One table of contents per disc: those of the rip logs and cue sheets (in the order
    given, the first wins) where they fit a disc, the ones rebuilt from the tracks'
    lengths for the other discs. A log or cue TOC goes to the disc with as many tracks
    (logs and cue sheets do not say which disc they are; of discs with as many, the one
    closest in length), else to a disc that has no TOC yet. One that fits no disc is left
    out: that disc's own TOC stands."""
    counts, sectors, per_disc = {}, {}, {}
    for _p, i in infos:
        d = i.get("disc_eff", 1)
        counts[d] = counts.get(d, 0) + 1
        per_disc.setdefault(d, []).append(i)
        sectors[d] = sectors.get(d, 0) + (i.get("secs") or 0) * 75
    out = dict(disc_tocs(infos))
    taken = set()
    for source in sources:
        for toc in (source or {}).values():
            same = [d for d, t in out.items() if t == toc]
            if same:                                   # the log agrees with the disc's lengths
                taken.update(same)
                continue
            n, leadout = (int(x) for x in toc.split()[1:3])
            free = [d for d in sorted(counts) if d not in taken]
            fits = [d for d in free if counts[d] == n]
            disc = min(fits, key=lambda d: abs(sectors[d] + 150 - leadout)) if fits else None
            if disc is None:
                # a disc whose files fit their own tracks of it, the rest missing (the TOC
                # rebuilt from those files is no disc's)
                disc = next((d for d in free if toc_fits_slots(toc, per_disc[d])), None)
            if disc is None:
                # a disc with a hidden track before track 1 ripped to a file numbered 0: the
                # TOC lists one track fewer (without_htoa)
                disc = next((d for d in free if counts[d] == n + 1 and htoa_file(per_disc[d]) is not None
                             and toc_fits_disc(toc, per_disc[d])), None)
            if disc is None:
                disc = next((d for d in free if d not in out), None)
            if disc is not None:
                out[disc] = toc
                taken.add(disc)
    return out


# An Enhanced CD (CD-Extra) has its data track in a second session, 11400 sectors after
# the audio: MusicBrainz's disc ID covers the audio only, ending 11400 sectors before it.
SESSION_GAP = 11400


def audio_session(tracks):
    """The audio tracks of a log's TOC table [(start, end)] and the sector the audio
    ends at (its lead-out, from 0): an Enhanced CD's last track is its data track."""
    if len(tracks) >= 2 and tracks[-1][0] - tracks[-2][1] - 1 >= SESSION_GAP:
        return tracks[:-1], tracks[-1][0] - SESSION_GAP
    return tracks, tracks[-1][1] + 1


_LOG_TOC_ROW = re.compile(r"^\s*(\d{1,2})\s*\|\s*[\d:.]+\s*\|\s*[\d:.]+\s*\|\s*(\d+)\s*\|\s*(\d+)\s*$", re.M)


def _read_log(path):
    raw = Path(path).read_bytes()[:400000]
    for enc in ("utf-16",) if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", "ignore")


def log_tocs(album_dir):
    """Tables of contents from the rip logs next to the music (EAC, XLD): the
    exact disc that was ripped, whatever the files were converted to since."""
    out = []
    for log in rip_logs(album_dir):
        if log["toc"] and log["toc"] not in out:
            out.append(log["toc"])
    return {i + 1: toc for i, toc in enumerate(out)}


# The disc a log was made of, on its own line near the top: "Jesu / Conqueror (Japanese Bonus
# Disc)" (EAC, XLD; in any language of the program)
_LOG_HEADER = re.compile(r"^(\S[^\r\n:]*?)\s+/\s+(\S[^\r\n]*?)\s*$")
_WIN_PATH = re.compile(r"(?<![A-Za-z])[A-Za-z]:\\[^\r\n\t\"]+")
# where a log names the file it wrote ("     Filename D:\\EAC\\...\\01.wav", "Output file: ..."),
# a ripped file's own extension, and the parts of a path that are the program's
_OUTPUT_LINE = re.compile(r"(?i)\b(?:file\s*name|dateiname|nom\s+de\s+fichier|имя\s+файла|output)\b")
_AUDIO_PATH = re.compile(r"(?i)\.(?:wav|flac|ape|wv|mp3|m4a|aiff?|tak|ogg|opus)\s*$")
_PROGRAM_PART = re.compile(r"(?i)program\s*files|\(x(?:86|64)\)|^x(?:86|64)$|exact\s*audio\s*copy|\.exe$")


def _log_toc(text):
    """The TOC of one log's text in MusicBrainz's form, and whether its disc had a data
    track (an Enhanced CD), or ("", False)."""
    if "TOC" not in text and "toc" not in text:
        return "", False
    rows = [(int(n), int(s), int(e)) for n, s, e in _LOG_TOC_ROW.findall(text)]
    seen, tracks = set(), []
    for n, s, e in rows:                       # the TOC table only (first occurrence of each track)
        if n in seen:
            break
        seen.add(n)
        tracks.append((s, e))
    if not 1 <= len(tracks) <= 99:
        return "", False
    audio, leadout = audio_session(tracks)
    offsets = [s + 150 for s, _e in audio]
    return " ".join(str(x) for x in [1, len(audio), leadout + 150] + offsets), len(audio) < len(tracks)


def _log_header(text):
    """(artist, album) of the disc the log names in its first lines, or None."""
    lines = [l for l in text.splitlines()[:12] if l.strip()]
    for line in lines[:6]:
        m = _LOG_HEADER.match(line.strip())
        if m and not re.search(r"\d{1,2}:\d{2}|\\|https?:", line):
            return m.group(1).strip(), m.group(2).strip()
    return None


def _path_codes(text, name):
    """Catalogue numbers the ripper typed into the folders it ripped to ("D:\\EAC\\... (Prosthetic
    - 7930183432)\\...wav", "...\\2014-Metal Mind [Poland, MASS CD 1491 DG]\\...") and into the
    log's own name ("Candlemass (Metal Mind).log"): written like a folder name, read as one.
    Only the paths of the files ripped count: the encoder's ("C:\\Program Files (x86)\\Exact Audio
    Copy\\Flac\\flac.exe") names the program, and its "(x86)" is no number."""
    parts = [name]
    for m in _WIN_PATH.finditer(text):
        path = m.group(0).strip()
        line = text[text.rfind("\n", 0, m.start()) + 1:m.start()]
        if not (_OUTPUT_LINE.search(line) or _AUDIO_PATH.search(path)):
            continue
        folders = re.split(r"[\\/]", path)[1:]
        if _AUDIO_PATH.search(path):             # (a track's own name is its title: "... (MC 900 Ft. Jesus Mix).wav")
            folders = folders[:-1]
        parts += [x for x in folders if x and not _PROGRAM_PART.search(x)]
    out = []
    for part in parts:
        for c in mc.catno_hints(part):
            if c not in out:
                out.append(c)
    return out


def rip_logs(album_dir):
    """Every rip log under the album folder: {"path", "toc" (MusicBrainz form, "" when it has
    none), "data_track" (an Enhanced CD's), "header" ((artist, album) or None), "codes"
    (catalogue numbers in its output paths and its name), "crcs" (its CRC values), "out_parts"
    (the folders of the files it wrote)}, in natural order."""
    out = []
    for p in sorted(Path(album_dir).rglob("*.log"), key=lambda p: _natural_key(str(p))):
        try:
            text = _read_log(p)
        except OSError:
            continue
        toc, data = _log_toc(text)
        out.append({"path": str(p), "toc": toc, "data_track": data, "header": _log_header(text),
                    "codes": _path_codes(text, p.stem), "crcs": set(re.findall(r"\bCRC\s+([0-9A-F]{8})\b", text)),
                    "out_parts": _output_parts(text)})
    return out


def _output_parts(text):
    """The folders of the files a log says it wrote (its Filename/Output lines), without the
    program's own ("C:\\Lossrip\\Pestilence [2006 - reissue] [CD] Mind Reflections\\...wav")."""
    parts = []
    for m in _WIN_PATH.finditer(text):
        line = text[text.rfind("\n", 0, m.start()) + 1:m.start()]
        if _OUTPUT_LINE.search(line):
            parts += [x for x in re.split(r"[\\/]", m.group(0).strip())[1:-1] if x and not _PROGRAM_PART.search(x)]
    return parts


# A CUETools report's CRC row: "[CRC32] [W/O NULL]" then the LOG column: "CRC32" or "W/O NULL"
# when the log's CRC is that one, the log's own "[XXXXXXXX]" when it differs
_ACCURIP_ROW = re.compile(r"^\s*(?:--|\d{1,2})\s+[\d.,]+\s+\[([0-9A-F]{8})\]\s+\[([0-9A-F]{8})\]"
                          r"[ \t]*(CRC32|W/O NULL|\[[0-9A-F]{8}\])?", re.M)


def accurip_confirms(album_dir, log):
    """A CUETools .accurip report in the folder checked this log's CRC against the files:
    one of the log's CRCs is in the report with the LOG column "CRC32" or "W/O NULL", and no
    row shows a log CRC that differs. A log without a TOC (the old EAC kind) is then still
    proven to be of these files (Pestilence - Mind Reflections: CRC 90E183CD)."""
    if not log.get("crcs"):
        return False
    hit = False
    for p in Path(album_dir).rglob("*.accurip"):
        try:
            text = _read_log(p)
        except OSError:
            continue
        for crc32, wo_null, col in _ACCURIP_ROW.findall(text):
            if col.startswith("["):
                return False
            if (col == "CRC32" and crc32 in log["crcs"]) or (col == "W/O NULL" and wo_null in log["crcs"]):
                hit = True
    return hit


def _toc_lengths(toc):
    """Each track's length in sectors from a TOC string ("1 N leadout off1 ... offN")."""
    nums = [int(x) for x in toc.split()]
    n, leadout, offsets = nums[1], nums[2], nums[3:]
    return [b - a for a, b in zip(offsets, offsets[1:] + [leadout])][:n]


def toc_fits_audio(toc, tracks):
    """Does a rip log's TOC fit these files (one disc, in order)? Each lossless 44.1 kHz
    track must be the TOC's length within one sector (588 samples); any other file within
    0.1 s. A one-file image is compared as a whole. A log that does not fit was made of
    another rip or another pressing (Cynic - Traced in Air: a 2008 CD log beside the 2019
    download), so its disc ID is not your files'."""
    lengths = _toc_lengths(toc)
    if not tracks or not lengths:
        return False
    if len(tracks) == 1 and len(lengths) > 1:
        lengths = [sum(lengths)]
    if len(tracks) != len(lengths):
        return False
    for i, sectors in zip(tracks, lengths):
        if i.get("lossless") and i.get("rate") == 44100 and i.get("samples"):
            if abs(i["samples"] / CD_FRAME - sectors) > 1:
                return False
        elif abs((i.get("secs") or 0) - sectors / 75) > 0.1:
            return False
    return True


def toc_fits_slots(toc, tracks):
    """A TOC with more tracks than these files (some are missing from the folder): does each
    file fit the TOC's track of its own number, as toc_fits_audio compares lengths? Every
    file needs a number of its own within the TOC."""
    lengths = _toc_lengths(toc)
    nums = [i.get("num") or 0 for i in tracks]
    if (not tracks or len(tracks) >= len(lengths) or len(set(nums)) != len(nums)
            or any(n < 1 or n > len(lengths) for n in nums)):
        return False
    for i, n in zip(tracks, nums):
        sectors = lengths[n - 1]
        if i.get("lossless") and i.get("rate") == 44100 and i.get("samples"):
            if abs(i["samples"] / CD_FRAME - sectors) > 1:
                return False
        elif abs((i.get("secs") or 0) - sectors / 75) > 0.1:
            return False
    return True


def htoa_file(tracks):
    """The disc's hidden track before track 1 (HTOA: the first track's pregap, ripped to a
    file of its own numbered 0), or None: the one file numbered 0, first on the disc, every
    other file numbered from 1. (read_album puts a file numbered 0 by its tags or its name,
    "00 - ...", first on its disc, and one without a number last: that is never one.)"""
    zero = [i for i in tracks if not i.get("num")]
    if (len(zero) != 1 or tracks[0] is not zero[0] or len(tracks) < 2
            or any((i.get("num") or 0) < 1 for i in tracks[1:])):
        return None
    return zero[0]


def without_htoa(toc, tracks):
    """The disc's files without its hidden track before track 1 (htoa_file) when that file
    fits in the TOC's first pregap (its length at most the first track's offset less the
    150-sector lead-in): a TOC lists no track 0. Else the files as they are."""
    first = htoa_file(tracks) if toc else None
    if first is None:
        return tracks
    try:
        pregap = (int(toc.split()[3]) - 150) / 75
    except (IndexError, ValueError):
        return tracks
    if pregap <= 0 or (first.get("secs") or 0) > pregap + 0.1:
        return tracks
    return [i for i in tracks if i is not first]


def htoa_toc_discs(infos, tocs):
    """The discs whose hidden track before track 1 (htoa_file) is in the first pregap of one
    of these TOCs (a rip log's, a CDTOC tag's) that fits the disc: the disc was read with that
    pregap, so the track is the disc's own, not just a file as long as a release's pregap."""
    discs = {}
    for _p, i in infos:
        discs.setdefault(i.get("disc_eff", 1), []).append(i)
    return sorted(d for d, tracks in discs.items()
                  if htoa_file(tracks) is not None
                  and any(toc and len(without_htoa(toc, tracks)) < len(tracks) and toc_fits_disc(toc, tracks)
                          for toc in tocs))


def toc_fits_disc(toc, tracks):
    """The TOC is this disc's: every track there (toc_fits_audio), or the files there fit
    their own tracks of it (toc_fits_slots). A hidden track before track 1 ripped to a file
    numbered 0 is left out when it fits the TOC's first pregap (without_htoa)."""
    tracks = without_htoa(toc, tracks)
    return toc_fits_audio(toc, tracks) or toc_fits_slots(toc, tracks)


_CDTOC_TAG = re.compile(r"^\s*([0-9A-Fa-f]{1,2})((?:\+[0-9A-Fa-f]+)+)\s*$")


def cdtoc_tag_toc(value):
    """The CDTOC tag rippers write ("20+96+1937+...+4E6E9": the track count, each track's
    start and the lead-out, in hex sectors with the 150-sector lead-in) in MusicBrainz's
    TOC form ("1 32 321257 150 6455 ..."), or ""."""
    m = _CDTOC_TAG.match(str(value or ""))
    if not m:
        return ""
    n = int(m.group(1), 16)
    nums = [int(x, 16) for x in m.group(2).strip("+").split("+")]
    if not 1 <= n <= 99 or len(nums) != n + 1 or nums != sorted(nums):
        return ""
    return " ".join(str(x) for x in [1, n, nums[-1]] + nums[:-1])


def tag_tocs(infos):
    """The TOCs the files' CDTOC tags hold, one per disc, where it fits that disc's files."""
    discs = {}
    for _p, i in infos:
        discs.setdefault(i.get("disc_eff", 1), []).append(i)
    out = {}
    for d, tracks in sorted(discs.items()):
        tocs = {cdtoc_tag_toc(i.get("cdtoc")) for i in tracks} - {""}
        if len(tocs) == 1 and toc_fits_disc(next(iter(tocs)), tracks):
            out[len(out) + 1] = next(iter(tocs))
    return out


def fitting_logs(infos, logs):
    """The logs whose TOC fits one disc of the files (or that have no TOC to check), and the
    foreign ones: a TOC that fits no disc. A disc whose files fit their own tracks of the
    TOC (the others missing from the folder) fits too."""
    discs = {}
    for _p, i in infos:
        discs.setdefault(i.get("disc_eff", 1), []).append(i)
    kept, foreign = [], []
    for log in logs:
        if not log["toc"] or any(toc_fits_disc(log["toc"], tracks) for tracks in discs.values()):
            kept.append(log)
        else:
            foreign.append(log)
    return kept, foreign


# --------------------------------------------------------------------------
# AcoustID fingerprints (Picard's "Scan"): identify the recordings from the audio
# --------------------------------------------------------------------------
def picard_preferred_countries(ini=None, overrides=None, follow_picard=True):
    """Picard's Options > Metadata > Preferred Releases > countries, when set: read as
    tagger_settings reads Picard's settings (the Picard.ini TagMender uses, one country in Qt's
    binary form, Picard 3's option profiles); ini: that file instead. As the release types and
    formats (picard_release_preferences): a profile's "picard" entries override them, and
    follow_picard False reads no Picard.ini."""
    import tagger_settings
    path = Path("/nonexistent/Picard.ini") if not follow_picard else Path(ini).expanduser() if ini else None
    try:
        s = tagger_settings.tag_settings(overrides or {}, path=path)
    except Exception:
        s = {"preferred_release_countries": (overrides or {}).get("preferred_release_countries") or []}
    return [str(c).strip().upper() for c in s.get("preferred_release_countries") or []
            if re.fullmatch(r"\s*[A-Za-z]{2}\s*", str(c))]


def picard_release_preferences(ini=None, overrides=None, follow_picard=True):
    """Picard's Options > Metadata > Preferred Releases beside the countries: the release
    types to prefer and to discourage and the medium formats to prefer, as match_core's
    settings (cfg.update(...); tagger_settings.matching_preferences reads them, with a
    profile's "picard" entries as overrides). Picard 3's defaults (none) and the overrides when
    unreadable.
    Like the country order, only the very last tie-break between pressings of one album."""
    import tagger_settings
    try:
        return tagger_settings.matching_preferences(overrides, path=Path(ini).expanduser() if ini else None,
                                                  follow_picard=follow_picard)
    except Exception:                                  # (an odd Picard.ini: the overrides still apply)
        return tagger_settings.matching_preferences(overrides, follow_picard=False)


def _discogs_token():
    """DISCOGS_TOKEN from the environment, else ~/.config/tagmender/discogs_token (mode 600)."""
    import credentials
    try:
        return credentials.get("discogs")
    except credentials.CredentialError as exc:
        sys.exit(str(exc))


def acoustid_key():
    """TAGMENDER_ACOUSTID_KEY from the environment, else acoustid_key in TagMender's config folder
    (mode 600). "" when not set up, or when other users can read the file: it is then not sent,
    and requirements' notice says how to fix it."""
    import credentials
    try:
        return credentials.get("acoustid")
    except credentials.CredentialError:
        return ""


def fingerprint_ready():
    return bool(shutil.which("fpcalc") and acoustid_key())


def fingerprint(path, db=None):
    """Chromaprint fingerprint of a file (fpcalc), cached by file size and time."""
    import subprocess
    try:
        st = Path(path).stat()
    except OSError:
        return None
    key = f"{path}|{st.st_size}|{int(st.st_mtime)}"

    def compute():
        try:
            r = subprocess.run(["fpcalc", "-json", "-length", "120", str(path)],
                               capture_output=True, text=True, timeout=120)
            d = json.loads(r.stdout)
            return {"duration": round(d["duration"]), "fingerprint": d["fingerprint"]}
        except Exception:
            return None
    return _cached(db, "fp", key, compute)


ACOUSTID_MODES = ("unsure", "always", "off")


def acoustid_mode(value):
    """The profile's "acoustid": unsure (only albums not identified for sure; the default),
    always (every album: a fingerprint that disagrees with a sure pick is noted) or off."""
    value = str(value or "unsure").strip().lower()
    return value if value in ACOUSTID_MODES else "unsure"


def acoustid_picks(album, n=3):
    """The tracks fingerprinted: [(position in the album, track)], first, middle and last."""
    tracks = [(i, t) for i, t in enumerate(album["tracks"]) if t.get("path")]
    if len(tracks) < 3:
        return tracks
    return [tracks[0], tracks[len(tracks) // 2], tracks[-1]][:n]


def acoustid_matches(http, album, db=None, n=3):
    """What AcoustID says of the picked tracks: [(position, {recording ids}, {release ids})],
    results scored 0.8 or more only; a track without a fingerprint or an answer is left out."""
    key = acoustid_key()
    if not key or not album["tracks"] or not shutil.which("fpcalc"):
        return []
    out = []
    for pos, t in acoustid_picks(album, n):
        fp = fingerprint(t["path"], db)
        if not fp:
            continue
        data = http.get_json(f"https://api.acoustid.org/v2/lookup?client={q(key)}&format=json"
                             f"&meta=recordings+releaseids&duration={fp['duration']}"
                             f"&fingerprint={q(fp['fingerprint'])}", "acoustid")
        recs = [rec for res in (data or {}).get("results", []) if res.get("score", 0) >= 0.8
                for rec in res.get("recordings") or []]
        rels = {rel["id"] for rec in recs for rel in rec.get("releases") or [] if rel.get("id")}
        if recs:
            out.append((pos, {rec["id"] for rec in recs if rec.get("id")}, rels))
    return out


def acoustid_releases(http, album, db=None, n=3):
    """MusicBrainz releases that hold the recordings AcoustID finds for several
    tracks of the album (first, middle and last): the releases common to all of
    them. Empty when fingerprinting is not set up or nothing is found."""
    common = None
    for _pos, _recs, rels in acoustid_matches(http, album, db, n):
        if rels:
            common = rels if common is None else common & rels
    return common or set()


def mb_recordings(http, release_id):
    """The recording ID of each audio track of a MusicBrainz release, in order ([] when unknown)."""
    det = http.get_json(f"https://musicbrainz.org/ws/2/release/{release_id}"
                        f"?inc=recordings+artist-credits+labels+url-rels&fmt=json", "mb") or {}
    return [(t.get("recording") or {}).get("id") or ""
            for m in det.get("media") or [] if str(m.get("format") or "").lower() not in _MB_VIDEO_FORMATS
            for t in m.get("tracks") or [] if not (t.get("recording") or {}).get("video")]


def acoustid_disagreement(http, album, release_id, db=None):
    """With acoustid: always, for an album identified for sure: a note when the fingerprints
    point to other recordings than the pick's MusicBrainz release has at those tracks
    ("" when they agree, or nothing can be compared). Never changes the pick."""
    if not release_id:
        return ""                              # a Discogs-only pick: nothing to compare against
    found = acoustid_matches(http, album, db)
    if not found:
        return ""
    recs = mb_recordings(http, release_id)
    if len(recs) != len(album["tracks"]):
        return ""
    off = [pos + 1 for pos, ids, _rels in found if recs[pos] and recs[pos] not in ids]
    if not off:
        return ""
    many = len(off) > 1
    return (f"AcoustID disagrees: the fingerprint{'s' if many else ''} of track"
            f"{'s' if many else ''} {', '.join(map(str, off))} point{'' if many else 's'} to other recordings "
            f"than the pick's (of {len(found)} fingerprinted)")


def acoustid_unchecked(result, mode):
    """True for a stored sure match that acoustid: always has not fingerprinted yet (it was
    matched by a run without it), when fingerprints can be taken now."""
    return (acoustid_mode(mode) == "always" and result.get("status") == "auto"
            and not result.get("acoustid_checked") and fingerprint_ready())


def mb_discid(toc):
    """The MusicBrainz disc ID of a table of contents ("1 N leadout off1 ... offN")."""
    import base64
    import hashlib
    nums = [int(x) for x in toc.split()]
    first, last, leadout, offsets = nums[0], nums[1], nums[2], nums[3:]
    text = f"{first:02X}{last:02X}{leadout:08X}" + "".join(
        f"{(offsets[i] if i < len(offsets) else 0):08X}" for i in range(99))
    digest = hashlib.sha1(text.encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii").replace("+", ".").replace("/", "_").replace("=", "-")


def ctdb_toc(toc):
    """The same table of contents in CUETools DB form (sectors from 0, colon separated)."""
    nums = [int(x) for x in toc.split()]
    return ":".join(str(x - 150) for x in nums[3:] + [nums[2]])


def toc_releases(http, album):
    """Releases whose disc matches a disc of these files, from the tables of
    contents (audio, rip log or cue sheet):

    - MusicBrainz by disc ID: exact when that disc ID is attached to a release,
      else fuzzy, as Picard's CD lookup does;
    - the CUETools database (CTDB), which links rips to MusicBrainz AND Discogs
      releases, including pressings MusicBrainz has no disc ID for.

    Sets album["toc_discogs"] (Discogs ids from CTDB), album["discids"]
    ([(disc ID, toc, attached in MusicBrainz? None when the lookup failed)]) and album["exact_toc_releases"] (the
    MusicBrainz releases a disc ID of these files is attached to), album["exact_toc_media"]
    ({release: {medium positions carrying that disc ID}}) and, for a lookup that failed,
    album["clue_failures"] ({what: error}); returns MusicBrainz ids."""
    found, discogs, discids, exact_found, media = set(), set(), [], set(), {}
    for toc in (album.get("tocs") or {}).values():
        did = mb_discid(toc)
        failed = False
        try:
            data = http.get_json(_discid_url(did, toc), "mb")
        except _CLUE_ERRORS as exc:          # the other discs, and CTDB, may still answer
            album.setdefault("clue_failures", {})["musicbrainz disc ID"] = (
                _clue_error_text(exc), getattr(exc, "kind", "outage"))
            data, failed = None, True
        # None: the lookup failed, so whether MusicBrainz has this disc ID is unknown
        # (not "no such disc ID": no attach link is offered for it)
        exact = None if failed else bool(data and data.get("id") == did)
        discids.append((did, toc, exact))
        _discid_releases(data, did, exact, found, exact_found, media)
        get_text = getattr(http, "get_text", None)
        if get_text:
            try:
                xml = get_text(f"https://db.cue.tools/lookup2.php?version=3&ctdb=1&metadata=extensive"
                               f"&fuzzy=1&toc={ctdb_toc(toc)}", "ctdb") or ""
            except _CLUE_ERRORS as exc:          # not "CTDB knows no release": the lookup failed
                xml = ""
                album.setdefault("clue_failures", {})["ctdb"] = (_clue_error_text(exc), "outage")
            for tag in re.findall(r"<metadata\b[^>]*>", xml):
                attrs = dict(re.findall(r'(\w+)="([^"]*)"', tag))
                if attrs.get("source") == "musicbrainz" and re.fullmatch(r"[0-9a-f-]{36}", attrs.get("id", "")):
                    found.add(attrs["id"])
                elif attrs.get("source") == "discogs" and attrs.get("id", "").isdigit():
                    discogs.add(attrs["id"])
    album["toc_discogs"] = discogs
    album["discids"] = discids
    album["exact_toc_releases"] = exact_found
    album["exact_toc_media"] = media
    return found


def _discid_url(did, toc):
    return f"https://musicbrainz.org/ws/2/discid/{did}?toc={toc.replace(' ', '+')}&cdstubs=no&fmt=json"


def _discid_releases(data, did, exact, found, exact_found, media):
    """Add the releases of a disc ID answer to found / exact_found / media (toc_releases)."""
    for r in (data or {}).get("releases", []):
        if r.get("id"):
            found.add(r["id"])
            if exact:
                exact_found.add(r["id"])     # this very disc ID is attached to it
                for m in r.get("media") or ():
                    if any(d.get("id") == did for d in m.get("discs") or ()) and m.get("position"):
                        media.setdefault(r["id"], set()).add(int(m["position"]))


def retry_disc_ids(http, album):
    """Ask MusicBrainz once more for the disc IDs whose lookup failed (toc_releases), a single
    try each (no 503 retry ladder: by now the request pace has adapted, and a short burst
    has usually cleared). Only when every failed disc ID answers now are the answers used
    and the failure cleared; True then. False (nothing changed) otherwise."""
    fails = album.get("clue_failures") or {}
    discids = list(album.get("discids") or ())
    if "musicbrainz disc ID" not in fails or not any(e is None for _d, _t, e in discids):
        return False
    found = set(album.get("toc_releases") or ())
    exact_found = set(album.get("exact_toc_releases") or ())
    media = {k: set(v) for k, v in (album.get("exact_toc_media") or {}).items()}
    answered = []
    for did, toc, exact in discids:
        if exact is not None:
            answered.append((did, toc, exact))
            continue
        try:
            if isinstance(http, Http):
                data = http.get_json(_discid_url(did, toc), "mb", attempts=1)
            else:
                data = http.get_json(_discid_url(did, toc), "mb")
        except _CLUE_ERRORS as exc:          # still failing: the album keeps its failure (retried)
            fails["musicbrainz disc ID"] = (_clue_error_text(exc), getattr(exc, "kind", "outage"))
            return False
        exact = bool(data and data.get("id") == did)
        answered.append((did, toc, exact))
        _discid_releases(data, did, exact, found, exact_found, media)
    del fails["musicbrainz disc ID"]
    album.update(discids=answered, toc_releases=found, exact_toc_releases=exact_found, exact_toc_media=media)
    return True


def hard_proof(best):
    """The pick rests on something only your copy's own pressing has: its exact disc ID
    attached in MusicBrainz, the shop page the files link to (MusicBrainz ties it to this
    release: never a MusicBrainz/Discogs release link in the tags, another tagger's word), or a barcode. (Not a barcode only typed
    in notes or written by another tagger: mc.WEAK_BARCODES. Not audio fingerprints: they find
    the recordings, which every pressing of the album shares.)"""
    if not best:
        return False
    clues = best.get("clues") or {}
    return mc._exact_toc(best) or "source link" in clues or mc.barcode_proves(best)


_CLUE_FAILED = {"ctdb": "CUETools DB lookup of the disc failed ({msg}); its MusicBrainz/Discogs links were not used",
                "musicbrainz disc ID": "MusicBrainz disc ID lookup failed ({msg}); the disc ID was not checked",
                "musicbrainz link lookup": "MusicBrainz lookup of the files' links or ISRCs failed ({msg}); "
                                           "their releases were not checked"}
# the lookups whose failure counts as MusicBrainz's (a source note: the album is retried)
MB_CLUES = ("musicbrainz disc ID", "musicbrainz link lookup")


def clue_failure_notes(album, provider_errors=None):
    """A note for each disc lookup that failed (toc_releases' "clue_failures"), so a failure
    is never read as "no release has this disc". CTDB is a clue: recorded as an outage in
    provider_errors, which engine._unresolved does not turn into a reason code; a
    MusicBrainz disc ID or link lookup failure counts as MusicBrainz's, as a failed search
    does (an error MusicBrainz's search already recorded is kept)."""
    notes = []
    for what, (msg, kind) in (album.get("clue_failures") or {}).items():
        notes.append(_CLUE_FAILED.get(what, what + " failed ({msg})").format(msg=msg))
        if provider_errors is None:
            continue
        if what in MB_CLUES:
            provider_errors.setdefault("musicbrainz", kind)
        elif what == "ctdb":
            provider_errors["ctdb"] = "outage"
    return notes


def cue_tocs(album_dir, infos):
    """Tables of contents from cue sheets: track starts from INDEX 01, the end
    from the length of the audio (per-track files are added up in order)."""
    out = []
    secs_by_name = {Path(p).name.casefold(): i["secs"] for p, i in infos if p is not None}
    track_secs = [i["secs"] for _p, i in infos]
    for cue in sorted(Path(album_dir).rglob("*.cue"), key=lambda p: _natural_key(str(p))):
        try:
            text = _read_log(cue)
        except OSError:
            continue
        files, current, starts = [], None, []
        for line in text.splitlines():
            m = re.match(r'\s*FILE\s+"?(.+?)"?\s+(WAVE|FLAC|MP3|AIFF|BINARY)?\s*$', line, re.I)
            if m:
                current = {"name": m.group(1), "tracks": []}
                files.append(current)
                continue
            m = re.match(r"\s*INDEX\s+01\s+(\d+):(\d+):(\d+)", line)
            if m and current is not None:
                current["tracks"].append(int(m.group(1)) * 4500 + int(m.group(2)) * 75 + int(m.group(3)))
        n = sum(len(f["tracks"]) for f in files)
        if not n or n > 99:
            continue
        pos, offsets = 0, []
        ok = True
        for k, f in enumerate(files):
            length = secs_by_name.get(Path(f["name"]).name.casefold())
            if length is None:
                # converted since (names changed): one file per track, in order
                length = track_secs[k] if len(files) == len(track_secs) else None
            if length is None and k < len(files) - 1:
                ok = False
                break
            offsets += [pos + s for s in f["tracks"]]
            if length is None:                         # single-file cue whose file is gone
                ok = False
                break
            pos += round(length * 75)
        if ok and offsets:
            toc = " ".join(str(x) for x in [1, len(offsets), pos + 150] + [o + 150 for o in offsets])
            if toc not in out:
                out.append(toc)
    return {i + 1: toc for i, toc in enumerate(out)}


def cue_fits(cue, infos):
    """Is this cue sheet of these files? True when every file it names is a file of the
    folder (the rip's own cue, an image's), or its tracks' lengths (from its INDEX lines, and
    the length of a file of the folder it names) fit one disc of the files, or it names some
    of their files but gives no length to compare; False when it gives lengths and they fit no
    disc (another edition's cue, a downloaded one); None when there is nothing to compare
    (no audio read, or nothing of it known): unverifiable. A track's length is taken each
    way a rip is split: the gap before the next track (INDEX 00) appended to it, left out,
    or prepended to the next track (the track then starts at its own INDEX 00 too). A hidden
    track before track 1 ripped to a file numbered 0 (htoa_file; the cue lists no track 0)
    is left out of the comparison, and fits when it is as long as track 1's pregap (its
    INDEX 01 less its INDEX 00)."""
    try:
        text = _read_log(cue)
    except OSError:
        return None
    secs_by_name = {Path(p).name.casefold(): i.get("secs") for p, i in infos if p is not None}
    files, current, pending_gap = [], None, None
    for line in text.splitlines():
        m = re.match(r'\s*FILE\s+"?(.+?)"?\s+(WAVE|FLAC|MP3|AIFF|BINARY)?\s*$', line, re.I)
        if m:
            current = {"name": m.group(1), "starts": [], "gaps": []}
            files.append(current)
            continue
        m = re.match(r"\s*INDEX\s+(00|01)\s+(\d+):(\d+):(\d+)", line)
        if m and current is not None:
            at = int(m.group(2)) * 4500 + int(m.group(3)) * 75 + int(m.group(4))
            if m.group(1) == "00":
                pending_gap = (current, at)
            else:
                current["starts"].append(at)
                current["gaps"].append(pending_gap[1] if pending_gap and pending_gap[0] is current else None)
                pending_gap = None
    if not files or not any(f["starts"] for f in files):
        return None
    if all(Path(f["name"]).name.casefold() in secs_by_name for f in files):
        return True                 # it names the folder's own files (an image and its cue)
    # [(lengths in seconds it may have)] per track, [] when unknown
    lengths, named = [], False
    for f in files:
        file_secs = secs_by_name.get(Path(f["name"]).name.casefold())
        named = named or file_secs is not None
        for k, start in enumerate(f["starts"]):
            own = f["gaps"][k]
            if k + 1 < len(f["starts"]):
                nxt = f["starts"][k + 1]
                gap = f["gaps"][k + 1]
                opts = {(nxt - start) / 75}
                if gap is not None:
                    opts.add((gap - start) / 75)
                    if own is not None:
                        opts.add((gap - own) / 75)            # gaps prepended
                lengths.append(opts)
            elif file_secs:
                lengths.append({file_secs - start / 75} | ({file_secs - own / 75} if own is not None else set()))
            else:
                lengths.append(set())
    # track 1's pregap in the cue (seconds), for a hidden track ripped to a file of its own
    first = next((f for f in files if f["starts"]), None)
    pregap = (first["starts"][0] - (first["gaps"][0] or 0)) / 75 if first else 0
    if not any(lengths):
        return True if named else None
    discs = {}
    for _p, i in infos:
        if i.get("secs"):
            discs.setdefault(i.get("disc_eff", 1), []).append(i)
    if not discs:
        return None

    def near(secs, options):
        return not options or any(abs(secs - o) <= 0.5 for o in options)
    for tracks in discs.values():
        hidden = htoa_file(tracks)
        if hidden is not None and len(tracks) == len(lengths) + 1:
            if pregap <= 0 or abs(hidden["secs"] - pregap) > 0.5:
                continue
            tracks = [i for i in tracks if i is not hidden]
        if len(tracks) == len(lengths):
            if all(near(i["secs"], opt) for i, opt in zip(tracks, lengths)):
                return True
        elif len(tracks) < len(lengths):
            nums = [i.get("num") or 0 for i in tracks]
            if (len(set(nums)) == len(nums) and all(1 <= n <= len(lengths) for n in nums)
                    and all(near(i["secs"], lengths[n - 1]) for i, n in zip(tracks, nums))):
                return True
    return False


def foreign_cues(album_dir, infos):
    """The names of the cue sheets in the folder that fit no disc of the files (cue_fits
    False): another edition's or a downloaded cue, whose CATALOG is no print of this copy."""
    return sorted({cue.name for cue in Path(album_dir).rglob("*.cue")
                   if not cue.name.startswith(".") and cue_fits(cue, infos) is False})


# --------------------------------------------------------------------------
# gnudb.org (freedb's successor): what someone once typed in for a disc with this
# table of contents. Free text, often wrong or for another pressing: a clue and a
# new title to search, never proof of a release.
# --------------------------------------------------------------------------
GNUDB_URL = "https://gnudb.gnudb.org/~cddb/cddb.cgi"
GNUDB_NOT_SET = requirements.notice("gnudb", state=requirements.NOT_SET)


def gnudb_problem():
    """Why gnudb lookups are off, or "" when a registered address is set up. A key file
    other users can read says so (and how to fix it: chmod 600), rather than asking for
    an address that is already there."""
    import credentials
    try:
        return "" if gnudb_user() else GNUDB_NOT_SET
    except credentials.CredentialError:
        return requirements.notice("gnudb", state=requirements.UNSAFE)


def _gnudb_ready():
    """True when a registered address is set up (a key file others can read is not)."""
    return not gnudb_problem()


def gnudb_user():
    """The e-mail address registered at gnudb.org: TAGMENDER_GNUDB_USER, else gnudb_user in
    TagMender's config folder (mode 600). "" when not set up. Raises
    credentials.CredentialError for a file other users can read."""
    import credentials
    return credentials.get("gnudb")


def cddb_discid(toc):
    """The CDDB (freedb/gnudb) disc ID of a table of contents ("1 N leadout off1 ... offN",
    frames with the 150-frame lead-in): the digit sum of each track's start second, the
    disc's playing time in seconds and the track count, as eight hex digits."""
    nums = [int(x) for x in toc.split()]
    count, leadout, offsets = nums[1], nums[2], nums[3:]
    digits = sum(sum(int(c) for c in str(off // 75)) for off in offsets)
    seconds = leadout // 75 - offsets[0] // 75
    return f"{((digits % 255) << 24) | (seconds << 8) | count:08x}"


def _gnudb_hello(user):
    """The CDDB "hello" words: the registered address as user + host, then TagMender."""
    name, _at, host = user.partition("@")
    return "+".join(q(x) for x in (name, host or "localhost", "TagMender", _VERSION))


def _gnudb_get(http, cmd, user):
    """One CDDB-over-HTTP command (protocol 6: UTF-8). Cached and spaced under "gnudb"; the
    cache key leaves the address out, so it never sits in the cache next to the answer."""
    url = f"{GNUDB_URL}?cmd={cmd}&hello={_gnudb_hello(user)}&proto=6"
    return http.get_text(url, "gnudb", key=f"{GNUDB_URL}?cmd={cmd}&proto=6") or ""


def _cddb_reply(body):
    """(status code, first line, the lines after it up to the closing ".")."""
    lines = body.replace("\r\n", "\n").split("\n")
    first = lines[0].strip() if lines else ""
    m = re.match(r"(\d{3})\s", first + " ")
    rest = []
    for line in lines[1:]:
        if line.strip() == ".":
            break
        rest.append(line)
    return (int(m.group(1)) if m else 0), first, rest


def _gnudb_answered(http):
    """gnudb gave a real answer (an entry, a match or none): its refusals so far are forgotten."""
    ok = getattr(http, "host_ok", None)
    if ok:
        ok("gnudb")


def _gnudb_refused(http, cmd, first):
    """Anything but an answer (bad hello, server busy): forgotten, so a later run asks again,
    and counted as a failure of the host, so an address gnudb does not accept pauses gnudb
    (three refusals, then 30 minutes) instead of asking for every album."""
    _forget(http, f"{GNUDB_URL}?cmd={cmd}&proto=6")
    failed = getattr(http, "host_failed", None)
    if failed:
        failed("gnudb")
    raise GnudbRefused(first)


def gnudb_matches(http, toc, user, exact=None):
    """[(category, disc ID)] gnudb holds for this table of contents (exact and fuzzy). exact:
    a set the (category, disc ID) of gnudb's exact matches are added to (its reply 200 or
    210; 211 is fuzzy) - gnudb files a disc whose ID another disc has under an ID of its
    own, so the ID alone does not say it."""
    nums = [int(x) for x in toc.split()]
    cmd = "+".join(["cddb", "query", cddb_discid(toc), str(nums[1])] + [str(x) for x in nums[3:]]
                   + [str(nums[2] // 75)])
    code, first, rest = _cddb_reply(_gnudb_get(http, cmd, user))
    if code not in (200, 202, 210, 211):
        _gnudb_refused(http, cmd, first)
    _gnudb_answered(http)
    if code == 202:                                    # no match
        return []
    rows = [first.split(None, 1)[1]] if code == 200 else rest
    out = []
    for row in rows:
        parts = row.split()
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-fA-F]{8}", parts[1]):
            out.append((parts[0].lower(), parts[1].lower()))
    if exact is not None and code in (200, 210):
        exact.update(out)
    return out


def parse_xmcd(text):
    """The fields of an xmcd entry: {"artist", "title", "year", "genre", "tracks": [titles],
    "offsets": [frames], "seconds": disc length}. A field may run over several lines."""
    fields, offsets, seconds, in_offsets = {}, [], 0, False
    for line in text.splitlines():
        if line.startswith("#"):
            body = line[1:].strip()
            if body.lower().startswith("track frame offsets"):
                in_offsets = True
            elif in_offsets and body.isdigit():
                offsets.append(int(body))
            else:
                in_offsets = False
                m = re.match(r"disc length:\s*(\d+)", body, re.I)
                if m:
                    seconds = int(m.group(1))
            continue
        key, eq, value = line.partition("=")
        if eq:
            fields[key.strip().upper()] = fields.get(key.strip().upper(), "") + value
    dtitle = fields.get("DTITLE", "").strip()
    artist, sep, title = dtitle.partition(" / ")
    if not sep:
        artist, title = dtitle, dtitle             # freedb's rule: one name is both
    tracks, i = [], 0
    while f"TTITLE{i}" in fields:
        tracks.append(fields[f"TTITLE{i}"].strip())
        i += 1
    year = fields.get("DYEAR", "").strip()
    return {"artist": artist.strip(), "title": title.strip(), "year": int(year) if year.isdigit() else None,
            "genre": fields.get("DGENRE", "").strip(), "tracks": tracks, "offsets": offsets, "seconds": seconds}


def gnudb_entry(http, category, discid, user):
    """The entry gnudb keeps under category + disc ID, or None."""
    cmd = f"cddb+read+{q(category)}+{discid}"
    code, first, rest = _cddb_reply(_gnudb_get(http, cmd, user))
    if code not in (210, 401):
        _gnudb_refused(http, cmd, first)
    _gnudb_answered(http)
    if code == 401:                                    # no such entry
        return None
    entry = parse_xmcd("\n".join(rest))
    entry.update(category=category, discid=discid)
    return entry


def gnudb_fits(toc, entry, cfg):
    """True when the entry has the disc's track count and every track length agrees (a
    fuzzy match can be another disc that only came close)."""
    nums = [int(x) for x in toc.split()]
    mine = nums[3:] + [nums[2]]
    theirs = entry["offsets"] + [entry["seconds"] * 75]
    if len(entry["offsets"]) != nums[1] or len(entry["tracks"]) != nums[1] or not entry["seconds"]:
        return False
    return all(mc.duration_ok((mine[k + 1] - mine[k]) / 75, (theirs[k + 1] - theirs[k]) / 75, cfg)
               for k in range(nums[1]))          # the last track too: it ends at the disc length


def gnudb_lookup(http, album, cfg, user, discs=2, per_disc=3):
    """The gnudb entries that fit the album's discs (the first two discs, three entries
    each at most): ([entry], [failure notes]). gnudb unreachable is noted, never raised."""
    found, seen = [], set()
    for disc in sorted(album.get("tocs") or {})[:discs]:
        toc = album["tocs"][disc]
        try:
            exact = set()
            rows = gnudb_matches(http, toc, user, exact)
            # (more exact entries than are read: a rival unread may disagree - none is exact)
            crowded = len(exact) > per_disc
            for category, discid in rows[:per_disc]:
                if (category, discid) in seen:
                    continue
                seen.add((category, discid))
                entry = gnudb_entry(http, category, discid, user)
                if entry and entry["title"] and gnudb_fits(toc, entry, cfg):
                    # (exact: gnudb's exact match for this table of contents, not a fuzzy one - GNU-1)
                    found.append(dict(entry, disc=disc, exact=(category, discid) in exact and not crowded))
        except AuthError:
            raise
        except GnudbRefused as exc:                    # it answered, but not with an entry
            if "unknown application" in exc.answer.lower():   # gnudb lists the apps it answers
                return found, ["gnudb refused the lookup: gnudb.org does not accept this version "
                               "of TagMender yet (it answers registered applications only); "
                               "carried on without it"]
            return found, [f"gnudb refused the lookup ({exc.answer}): check the e-mail address "
                           f"registered at gnudb.org (tagmender setup); carried on without it"]
        except Exception:
            return found, ["gnudb lookup failed (offline or unavailable); carried on without it"]
    return found, []


def gnudb_title(entry):
    """The album title without the disc number freedb users put in it ("Title (CD1)")."""
    return re.sub(r"\s*[\(\[]?\b(?:cd|disc|disk)\s*\d+[\)\]]?\s*$", "", entry["title"], flags=re.I).strip() \
        or entry["title"]


def gnudb_probe(album, entry):
    """The album searched under gnudb's artist and title (its year when the files have
    none). Only the title searches are new work; the files' own codes are not resent."""
    probe = dict(album, artist=entry["artist"] or album["artist"], title=gnudb_title(entry),
                 barcodes=set(), asins=[], catno_search=[], search_labels=[], label_catnos=[],
                 toc_releases=set(), link_releases=set(), isrc_releases=set(), tag_release_mb=set(),
                 toc_discogs=set(), tag_discogs_links=set(), tag_release_discogs=set())
    if not album.get("year") and entry.get("year"):
        probe["year"] = entry["year"]
    return probe


def gnudb_disc_tracks(entry, cand):
    """The candidate's tracks on the disc the entry is for (a gnudb entry holds one disc, a
    release every disc); a single-disc release numbered otherwise (one disc of a box) is
    taken whole. [] when the disc's track count is not the entry's: nothing to compare."""
    tracks = cand.get("tracks") or []
    disc = [t for t in tracks if (t.get("disc") or 1) == entry.get("disc", 1)]
    if len(disc) != len(entry["tracks"]) and len({t.get("disc") or 1 for t in tracks}) == 1:
        disc = tracks
    return disc if len(disc) == len(entry["tracks"]) else []


def gnudb_titles_agree(entry, cand):
    """(agreeing, compared): gnudb's track titles that match those of the candidate's disc
    in order."""
    pairs = [(a, b.get("title") or "") for a, b in zip(entry["tracks"], gnudb_disc_tracks(entry, cand)) if a]
    agree = sum(1 for a, b in pairs
                if mc._ratio(mc.fold(mc.strip_edition(a)), mc.fold(mc.strip_edition(b))) >= 0.9)
    return agree, len(pairs)


def gnudb_note(entry, cand=None):
    """The entry as a clue: "gnudb: Artist - Title (1998, Rock): 9/10 track titles agree
    with the pick"."""
    extra = ", ".join(str(x) for x in (entry.get("year"), entry.get("genre") or entry.get("category")) if x)
    note = f"gnudb: {entry['artist']} - {entry['title']}" + (f" ({extra})" if extra else "")
    if cand:
        agree, n = gnudb_titles_agree(entry, cand)
        if n:
            note += f": {agree}/{n} track titles agree with the pick"
    return note


def gnudb_step(http, album, cfg, dm, mb_res, dg_res, final, text, args, use_mb, use_discogs, out,
               source_notes):
    """gnudb's step of `process`: when the album is still unsure and has a table of contents,
    what gnudb holds for its discs. A title or artist that differs from the files' is
    searched on MusicBrainz and Discogs; what comes back is scored against the files like
    any other candidate. The entries are kept in album["gnudb"] (their notes are made once
    the pick is final, by note_gnudb). Not set up (no registered address): skipped."""
    if (final["status"] == "auto" or not getattr(args, "use_gnudb", True) or not album.get("tocs")
            or not (use_mb or use_discogs)):
        return mb_res, dg_res, final
    if not _gnudb_ready():                             # not set up: the run's one note says so
        return mb_res, dg_res, final
    user = gnudb_user()
    entries, failed = gnudb_lookup(http, album, cfg, user)
    album["gnudb"] = entries
    notes = list(failed)
    searched = {_search_key(album["title"], album["artist"])}
    new_mb, new_dg = mb_res, dg_res
    for entry in entries:
        key = _search_key(gnudb_title(entry), entry["artist"] or album["artist"])
        if key in searched:
            continue
        searched.add(key)
        new_mb, new_dg, more, failures = probe_search(
            http, gnudb_probe(album, entry), "gnudb title", album, cfg, dm, new_mb, new_dg, text,
            use_mb, use_discogs, getattr(args, "provider_errors", None))
        notes += more
        source_notes.extend(failures)
    if notes:
        out["gnudb_notes"] = list(out.get("gnudb_notes") or []) + notes
    if new_mb is mb_res and new_dg is dg_res:
        return mb_res, dg_res, final
    return new_mb, new_dg, _combine(new_mb, new_dg)


def note_gnudb(out, album, final):
    """The gnudb entries as clues for whoever reviews the album, compared with the final
    pick's track titles; kept out of the match's own notes and `why` (never proof)."""
    entries = album.get("gnudb") or []
    if not entries:
        return
    out["gnudb"] = list(entries)                 # (GNU-1: the engine's web step may use them)
    cand = ((final or {}).get("best") or {}).get("cand")
    out["gnudb_notes"] = [gnudb_note(e, cand) for e in entries] + list(out.get("gnudb_notes") or [])


# --------------------------------------------------------------------------
# HTTP with rate limiting and a persistent cache
# --------------------------------------------------------------------------
class AuthError(RuntimeError):
    """Discogs rejected the token. Fatal: retrying cannot help."""


class ProviderUnavailable(RuntimeError):
    """A provider could not answer: kind is "rate_limited", "outage" or "invalid_response".
    Recorded per album; another provider may still identify it."""

    def __init__(self, host, kind, detail=""):
        super().__init__(f"{host} {kind.replace('_', ' ')}: {detail}")
        self.host, self.kind = host, kind


# What a disc lookup (a clue) can fail with besides ProviderUnavailable: an HTTP error
# Http re-raises (400, 403, 410), a connection reset or cut-short read, an SSL error while
# reading, an answer that is not UTF-8 or JSON. A failed clue is a note, never the album's
# error; anything else (KeyError, TypeError) is a bug and is raised.
_CLUE_ERRORS = (ProviderUnavailable, OSError, _HTTPException, ValueError)


def _clue_error_text(exc):
    return str(exc) if isinstance(exc, ProviderUnavailable) else f"{type(exc).__name__}: {exc}"


class GnudbRefused(ProviderUnavailable):
    """gnudb answered with a refusal (an address it does not accept, a busy server)
    instead of an entry; `answer` is its status line."""

    def __init__(self, answer):
        self.answer = answer[:80]
        super().__init__("gnudb", "outage", f"gnudb answered {self.answer!r}")


def check_discogs_token(token):
    """One uncached call so a bad token stops the run before any work is done."""
    req = urllib.request.Request(
        "https://api.discogs.com/oauth/identity",
        headers={"User-Agent": USER_AGENT, "Authorization": f"Discogs token={token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8")).get("username", "?")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise AuthError(
                f"Discogs rejected the token (HTTP {e.code}).\n"
                "  - Create one at https://www.discogs.com/settings/developers -> 'Generate new token'\n"
                "  - Paste only the token itself, no quotes or the words 'token=':\n"
                "      export DISCOGS_TOKEN=abcdEFGH...\n"
                "  - The export only lasts for that terminal window.")
        raise
    except (urllib.error.URLError, TimeoutError):
        return None   # offline check inconclusive; real calls will retry


def post_cache_key(url, payload):
    """The cache key of a JSON POST: Http.post_json stores the answer under it, and web_search
    looks it up to know a Tavily search is free (one key, so the two cannot drift apart)."""
    return f"POST {url} {json.dumps(payload, sort_keys=True)}"


# One pace per host for the whole process (share_pace): every Http the app, the watcher and
# the command line make for their look-ups (engine.Engine, the app's search and versions
# sheet, match_albums' own run) takes the last-call times and the per-host locks from here,
# so two look-ups at once (a versions sheet open during a Look Up) never ask a host faster
# than its delay between them. (An Http made on its own keeps its own, as a test's does.)
_SHARED_LAST: dict = {}              # host (SAME_LIMIT name) -> when it was last asked
_SHARED_HOST_LOCKS: dict = {}        # host (SAME_LIMIT name) -> its lock, held across pacing and request
_HOST_LOCKS_GUARD = threading.Lock()


# The longest a Retry-After is waited out between two tries of one request: a server's (or a
# proxy's) hour is not sat through five times over; the request gives up as rate limited instead.
RETRY_AFTER_MAX = 300


def retry_after(headers, now=None):
    """The seconds a 429's or 503's Retry-After asks for (RFC 9110: whole seconds or an HTTP
    date), at most RETRY_AFTER_MAX; 0 when it is missing, unreadable or already past (the
    caller's own back-off then). Never raises: a header in another form is no reason for the
    request to fail as anything but rate limited or unavailable."""
    try:
        value = str((headers.get("Retry-After") if headers is not None else None) or "").strip()
    except Exception:
        return 0
    if not value:
        return 0
    try:
        secs = float(value)
    except ValueError:
        try:
            import email.utils
            when = email.utils.parsedate_to_datetime(value)
            secs = when.timestamp() - (time.time() if now is None else now)
        except (TypeError, ValueError, IndexError, OverflowError):
            return 0
    if secs != secs or secs <= 0:        # (NaN, negative, now)
        return 0
    return min(float(RETRY_AFTER_MAX), secs)


def share_pace(http):
    """`http` (an Http) pacing its requests with every other shared Http of this process:
    their last-call times and per-host locks are the process's (_SHARED_LAST,
    _SHARED_HOST_LOCKS). Its thread clones share them too. Returns http."""
    http.last = _SHARED_LAST
    http._host_locks = _SHARED_HOST_LOCKS
    return http


class Http:
    def __init__(self, db, cfg, discogs_token):
        self.db, self.cfg, self.token = db, cfg, discogs_token
        self.last = {}
        db.execute("CREATE TABLE IF NOT EXISTS http_cache (url TEXT PRIMARY KEY, body TEXT)")
        cols = [r[1] for r in db.execute("PRAGMA table_info(http_cache)")]
        if "ts" not in cols:                              # when each answer was fetched
            db.execute("ALTER TABLE http_cache ADD COLUMN ts REAL DEFAULT 0")
            db.commit()
        # each host's last outcome, kept across runs for `doctor` (never a URL or a token)
        db.execute("CREATE TABLE IF NOT EXISTS provider_health (host TEXT PRIMARY KEY, fails INT, "
                   "last_error TEXT, last_fail REAL, last_ok REAL)")
        db.commit()
        # (answers cached by an older version under a URL holding the AcoustID application key:
        # the key is a credential and never stays in the cache)
        # (looked for first, so an ordinary start never takes the write lock; a DELETE that
        # matched nothing would leave its transaction open and hold every other Engine back)
        old = "url LIKE 'https://api.acoustid.org/%client=%'"
        try:
            if db.execute(f"SELECT 1 FROM http_cache WHERE {old} LIMIT 1").fetchone():
                db.execute(f"DELETE FROM http_cache WHERE {old}")
        except sqlite3.Error:
            pass
        finally:
            if db.in_transaction:
                try:
                    db.commit()
                except sqlite3.Error:
                    db.rollback()
        self.max_age = None
        self.run_failures, self.run_ok, self.run_errors = Counter(), set(), {}
        self._mb_base = max(1.0, float(cfg.get("mb_delay") or 1.0))  # mb_delay recovers toward this
        self._mb_ok = 0                                   # MusicBrainz answers in a row since the last 503

    def _health(self, host, error=None):
        """Record a live call's outcome: error=None for an answer, else what went wrong
        (the network's reason, e.g. an SSL hostname mismatch)."""
        try:
            if error is None:
                getattr(self, "run_ok", set()).add(host)
                self.db.execute("INSERT INTO provider_health (host, fails, last_error, last_fail, last_ok) "
                                "VALUES (?, 0, '', 0, ?) ON CONFLICT(host) DO UPDATE SET fails=0, last_ok=?",
                                (host, time.time(), time.time()))
            else:
                error = str(error)[:200]
                if hasattr(self, "run_failures"):
                    self.run_failures[host] += 1
                    self.run_errors[host] = error
                self.db.execute("INSERT INTO provider_health (host, fails, last_error, last_fail, last_ok) "
                                "VALUES (?, 1, ?, ?, 0) ON CONFLICT(host) DO UPDATE SET fails=fails+1, "
                                "last_error=?, last_fail=?", (host, error, time.time(), error, time.time()))
            self.db.commit()
        except Exception:      # (an old cache file, a locked db) a record never stops a lookup
            pass

    # Query parameters that are credentials, per host: left out of the cache key (cache_key)
    CREDENTIAL_PARAMS = {"acoustid": ("client",)}

    def cache_key(self, url, host):
        """The cache key of a GET: its URL, without a credential the host takes in the query
        (AcoustID's application key, client=), which must never be stored in the cache."""
        names = self.CREDENTIAL_PARAMS.get(host)
        if not names or "?" not in url:
            return url
        base, query = url.split("?", 1)
        kept = [p for p in query.split("&") if p.split("=", 1)[0] not in names]
        return base + ("?" + "&".join(kept) if kept else "")

    def get_json(self, url, host, attempts=None):
        body = self.get_text(url, host, attempts=attempts)
        if body is None:
            return None
        try:
            data = json.loads(body)
        except ValueError:
            self.db.execute("DELETE FROM http_cache WHERE url=?", (self.cache_key(url, host),))
            self.db.commit()
            raise ProviderUnavailable(host, "invalid_response", f"not JSON from {url.split('?')[0]}") from None
        error = data.get("error") if host == "deezer" and isinstance(data, dict) else None
        if error:
            # Deezer answers every error with HTTP 200 (deezer_upc_album): only "no data" (800) is
            # an answer; a quota (4) or busy (700) reply is not kept as "no results"
            code_no = error.get("code") if isinstance(error, dict) else None
            if code_no != DEEZER_NO_DATA:
                self.db.execute("DELETE FROM http_cache WHERE url=?", (self.cache_key(url, host),))
                self.db.commit()
                raise ProviderUnavailable(host, "rate_limited" if code_no == 4 else "outage",
                                          f"Deezer error {code_no}")
        return data

    # Hosts that only add evidence (the web search of an investigation): asked once with a
    # short timeout, and left alone for a while after failing, so an unreachable search
    # engine cannot hold every album up for minutes (6 retries x 60 s per query).
    OPTIONAL_HOSTS = {"mojeek": (1, 20, 3, 1800),        # attempts, timeout s, failures, pause s: the
                                                         # optional web search engines
                      "marginalia": (1, 20, 3, 1800),
                      "tavily": (1, 30, 3, 1800),
                      "deezer-upc": (1, 20, 3, 1800),    # the shops' barcode lookups
                      "itunes-upc": (1, 20, 3, 1800),
                      "gnudb": (1, 20, 3, 1800),         # a clue from a disc's TOC
                      "deezer": (2, 20, 3, 1800),        # the web fallback (and Deezer's track and ISRC leads)
                      "itunes": (1, 20, 3, 1800),
                      "bandcamp": (1, 20, 3, 1800),
                      "wikipedia": (1, 20, 3, 1800),
                      "ctdb": (2, 20, 3, 1800)}          # likewise: CUETools DB's links from the TOC
    # Seconds between requests to a host with its own limit (iTunes: about 20 a minute)
    # (mb and discogs have their own settings, mb_delay and discogs_delay; any other host: 1 s)
    HOST_DELAYS = {"itunes": 3.0, "itunes-upc": 3.0, "gnudb": 2.0, "deezer": 0.2, "deezer-upc": 0.2,
                   "acoustid": 0.4, "wikipedia": 0.5, "ctdb": 1.0, "bandcamp": 1.0}
    # Hosts that are one service under two names: their requests are spaced as one
    SAME_LIMIT = {"itunes-upc": "itunes", "deezer-upc": "deezer"}
    # Hosts that can answer with a refusal (gnudb: an address it does not accept): a reply
    # is not yet a success, so their failures are cleared by the caller (host_ok) once the
    # reply is read as a real answer
    ANSWER_CHECKED = {"gnudb"}

    def host_down(self, host):
        down = getattr(self, "down", None) or {}
        return down.get(host, (0, 0))[1] > time.time()

    def host_ok(self, host):
        """The host gave a real answer: its failures so far are forgotten."""
        (getattr(self, "down", None) or {}).pop(host, None)

    def host_failed(self, host):
        down = getattr(self, "down", None)
        if down is None:
            down = self.down = {}
        _a, _t, max_fail, pause_s = self.OPTIONAL_HOSTS[host]
        fails = down.get(host, (0, 0))[0] + 1
        until = time.time() + pause_s if fails >= max_fail else 0
        down[host] = (0 if until else fails, until)
        if until:
            _tell(f"    {host} failed {max_fail} times in a row (unreachable or refusing), "
                  f"not asked again for {pause_s // 60} minutes")

    def post_json(self, url, payload, host, headers=None):
        """A JSON POST (Tavily), cached by URL and body; never retried (an optional host).
        Credentials go in `headers` only, so they are never part of the cache key."""
        body = json.dumps(payload, sort_keys=True)
        key = post_cache_key(url, payload)
        row = self.db.execute("SELECT body FROM http_cache WHERE url=?", (key,)).fetchone()
        if row:
            return json.loads(row[0])
        if self.host_down(host):
            raise ProviderUnavailable(host, "outage", "skipped: failed repeatedly, trying again later")
        _a, timeout, _f, _p = self.OPTIONAL_HOSTS.get(host, (1, 30, 3, 1800))
        req = urllib.request.Request(url, data=body.encode(), method="POST",
                                     headers=dict({"User-Agent": USER_AGENT, "Content-Type": "application/json",
                                                   "Accept": "application/json"}, **(headers or {})))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = r.read().decode("utf-8")
            data = json.loads(text)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            if host in self.OPTIONAL_HOSTS:
                self.host_failed(host)
            self._health(host, f"{type(exc).__name__}: {getattr(exc, 'reason', '') or exc}")
            raise ProviderUnavailable(host, "outage", f"{type(exc).__name__}") from None
        (getattr(self, "down", None) or {}).pop(host, None)
        self._health(host)
        self.db.execute("INSERT OR REPLACE INTO http_cache (url, body, ts) VALUES (?,?,?)", (key, text, time.time()))
        self.db.commit()
        return data

    # MusicBrainz and Discogs: after a request gave up because the host does not answer
    # (kind "outage": every attempt a network error or a 5xx that is no request to slow
    # down), the host's requests that miss the cache fail at once for BREAKER_S seconds,
    # instead of each sitting through its own 6-try ladder (about 10 minutes); then one real
    # request is the trial. Never after "rate_limited": that is the host asking us to slow
    # down, which the pacing does. A request skipped so is still that provider's outage:
    # the album is an error, retried later, never a no match.
    BREAKER_HOSTS = ("mb", "discogs")
    BREAKER_S = 300

    def _breaker(self):
        """(state, lock): state is {host: [open until, trial request under way]}."""
        lock = self.__dict__.get("_breaker_lock")
        if lock is None:
            lock = self.__dict__.setdefault("_breaker_lock", threading.Lock())
        return self.__dict__.setdefault("_breaker_state", {}), lock

    def _breaker_gate(self, host):
        """Raise ProviderUnavailable while the host's breaker is open; True when this request
        is the one trial after the window (asked once), False for an ordinary request."""
        if host not in self.BREAKER_HOSTS:
            return False
        state, lock = self._breaker()
        with lock:
            st = state.get(host)
            if not st:
                return False
            until, trial = st
            if until > time.time() or trial:          # (another thread is making the trial)
                left = max(0, int(until - time.time()))
                raise ProviderUnavailable(host, "outage", "skipped: it gave up on an earlier request; "
                                          + (f"asked again in {left} s" if left else "a trial request is under way"))
            st[1] = True
            return True

    def _breaker_trip(self, host):
        if host not in self.BREAKER_HOSTS:
            return
        state, lock = self._breaker()
        with lock:
            state[host] = [time.time() + self.BREAKER_S, False]
        _tell(f"    {host} is not answering: its uncached requests are skipped for "
              f"{self.BREAKER_S // 60} minutes, then tried again")

    def _breaker_trial_done(self, host):
        """The trial request finished: closed, unless it gave up again (re-tripped)."""
        state, lock = self._breaker()
        with lock:
            st = state.get(host)
            if st and st[0] <= time.time():
                del state[host]
            elif st:
                st[1] = False

    def get_text(self, url, host, key=None, headers=None, attempts=None):
        """The answer to a GET, cached under `key` (default: the URL; a key lets what must not
        be stored, such as gnudb's registered address, stay out of the cache). `headers` are
        sent as well (an API key), never part of the cache key. attempts: at most this many
        tries (a single-try re-ask) instead of the host's own number."""
        key = key or self.cache_key(url, host)
        self._check_stop(host)
        row = self.db.execute("SELECT body, ts FROM http_cache WHERE url=?", (key,)).fetchone()
        if row and not (self.max_age and time.time() - float(row[1] or 0) > self.max_age):
            return row[0]
        trial = self._breaker_gate(host)
        try:
            return self._fetch(url, host, key, headers, 1 if trial else attempts, trial)
        finally:
            if trial:
                self._breaker_trial_done(host)

    # A clone for one more thread (thread_clone): it shares the pacing (last request times),
    # the down/breaker state and the run's health records with the Http it came from, and has
    # its own SQLite connection to the same cache. A host's live requests are taken one at a
    # time across the clones (_host_lock), so a host's limit is never multiplied.
    def _host_lock(self, host):
        with _HOST_LOCKS_GUARD:
            locks = self.__dict__.setdefault("_host_locks", {})
            return locks.setdefault(self.SAME_LIMIT.get(host, host), threading.RLock())

    def _pause(self, seconds):
        """Sleep, but a clone whose look-up was given up (_stop) wakes at once and raises."""
        stop = self.__dict__.get("_stop")
        if stop is None:
            time.sleep(seconds)
            return
        stop.wait(max(0.0, seconds))

    def _check_stop(self, host):
        stop = self.__dict__.get("_stop")
        if stop is not None and stop.is_set():
            raise ProviderUnavailable(host, "outage", "stopped: the look-up was given up")

    def thread_clone(self):
        """A shallow clone of this Http for one worker thread (see _host_lock), or None when
        this one cannot be cloned safely: its cache is not a file another connection can open,
        or its requests are wrapped on the instance (a test's stand-in). Close it with its
        close_clone() on any thread once the worker is done."""
        if any(k in self.__dict__ for k in ("get_text", "get_json", "post_json", "_fetch")):
            return None
        try:
            files = [r[2] for r in self.db.execute("PRAGMA database_list") if r[1] == "main"]
        except Exception:
            return None
        path = files[0] if files else ""
        if not path:                                   # an in-memory cache: nothing to share
            return None
        if getattr(self.db, "in_transaction", False):  # (its writes would wait for this one)
            return None
        # the state every clone shares, made now (each is created lazily otherwise)
        self._breaker()
        with _HOST_LOCKS_GUARD:
            self.__dict__.setdefault("_host_locks", {})
        with self._breaker()[1]:
            if getattr(self, "down", None) is None:
                self.down = {}
        clone = copy.copy(self)
        # own connection (journal_mode delete: a writer waits for the other, up to 60 s)
        clone.db = sqlite3.connect(path, timeout=60, check_same_thread=False)
        clone._stop = threading.Event()
        return clone

    def close_clone(self):
        try:
            self.db.close()
        except Exception:
            pass

    def _fetch(self, url, host, key, extra, at_most, trial):
        """_fetch_paced, the host's live requests one at a time across this Http's clones (and,
        shared, every Http of the process: share_pace). A clone whose look-up is given up while
        it waits for the host stops waiting."""
        lock = self._host_lock(host)
        if self.__dict__.get("_stop") is None:
            with lock:
                return self._fetch_paced(url, host, key, extra, at_most, trial)
        while not lock.acquire(timeout=0.25):
            self._check_stop(host)
        try:
            return self._fetch_paced(url, host, key, extra, at_most, trial)
        finally:
            lock.release()

    def _fetch_paced(self, url, host, key, extra, at_most, trial):
        """get_text's live request, with the host's retries (at_most: fewer)."""
        attempts, timeout, max_fail, pause_s = self.OPTIONAL_HOSTS.get(host, (6, 60, 0, 0))
        # only the host's full ladder (or the breaker's own trial) giving up shows the host is
        # down; a single-try re-ask (retry_disc_ids) failing once does not open the breaker
        full_ladder = trial or not at_most or at_most >= attempts
        if at_most:
            attempts = max(1, min(attempts, at_most))
        down = getattr(self, "down", None)
        if down is None:
            down = self.down = {}
        if host in self.OPTIONAL_HOSTS and down.get(host, (0, 0))[1] > time.time():
            raise ProviderUnavailable(host, "outage", "skipped: failed repeatedly, trying again later")
        if host == "mb":
            delay = self.cfg["mb_delay"]
        elif host == "discogs":
            delay = self.cfg["discogs_delay"]
        else:
            delay = self.HOST_DELAYS.get(host, 1.0)
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if host == "discogs":
            headers["Authorization"] = f"Discogs token={self.token}"
        headers.update(extra or {})
        last_kind, last_err = "outage", ""
        limit = self.SAME_LIMIT.get(host, host)
        for attempt in range(attempts):
            if attempt:
                self._check_stop(host)                   # (a clone whose look-up was given up)
            wait = delay - (time.time() - self.last.get(limit, 0))
            if wait > 0:
                self._pause(wait)
                self._check_stop(host)
            self.last[limit] = time.time()
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                            timeout=timeout) as r:
                    body = r.read().decode("utf-8")
                if host not in self.ANSWER_CHECKED:
                    down.pop(host, None)
                if host == "mb":                           # a calm spell: the 503 slow-down wears off
                    self._mb_ok = getattr(self, "_mb_ok", 0) + 1
                    if self._mb_ok >= 20:
                        self._mb_ok = 0
                        base = getattr(self, "_mb_base", 1.0)
                        if self.cfg["mb_delay"] > base:
                            self.cfg["mb_delay"] = max(base, self.cfg["mb_delay"] / 1.1)
                self._health(host)
                self.db.execute("INSERT OR REPLACE INTO http_cache (url, body, ts) VALUES (?,?,?)",
                                (key, body, time.time()))
                self.db.commit()
                return body
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    self._health(host)                     # an answer: there is no such thing
                    return None
                if e.code in (401, 403) and host == "discogs":
                    raise AuthError("Discogs rejected the token (HTTP %d)" % e.code)
                if e.code in (429, 500, 502, 503, 504):
                    last_err = f"HTTP {e.code}"
                    last_kind = "rate_limited" if e.code == 429 or (host == "mb" and e.code == 503) else "outage"
                    if host == "mb" and e.code == 503:
                        # MusicBrainz answers 503 when requests come too fast:
                        # slow down for the rest of the run, and retry soon.
                        self.cfg["mb_delay"] = min(3.0, self.cfg["mb_delay"] * 1.2)
                        delay = self.cfg["mb_delay"]
                        self._mb_ok = 0
                        pause = retry_after(e.headers) or 3 * (attempt + 1)
                    else:
                        pause = retry_after(e.headers) or 15 * (attempt + 1)
                    pause += random.uniform(0, 1)                   # jitter: parallel runs do not retry in step
                    if attempt == attempts - 1:
                        # giving up: no sleep now, but the host's next request still waits the
                        # pause out (the pacing wait is delay - (now - last))
                        self.last[limit] = max(self.last.get(limit, 0), time.time() + pause - delay)
                        break
                    _tell(f"    {host} HTTP {e.code}, waiting {pause:.0f}s")
                    self._pause(pause)                      # (a given-up clone: stops at once)
                    continue
                # another refusal (400, 403, 410): no retry, but the failure is recorded
                # so outage_notes and doctor see it and an optional host backs off
                self._health(host, f"HTTP {e.code}")
                if host in self.OPTIONAL_HOSTS:
                    self.host_failed(host)
                raise
            except (urllib.error.URLError, TimeoutError) as e:
                last_err = f"{type(e).__name__}: {getattr(e, 'reason', '') or e}"
                if attempt == attempts - 1:
                    break                                   # giving up: no sleep first
                _tell(f"    {host} network error {e}, retrying")
                self._pause(10 * (attempt + 1) + random.uniform(0, 1))
            except (OSError, _HTTPException, ValueError) as e:
                # a broken answer (connection reset, incomplete read, SSL error, not UTF-8)
                last_err = f"{type(e).__name__}: {e}"
                if host in self.OPTIONAL_HOSTS:
                    self.host_failed(host)
                if trial:                                   # the trial got no answer: open again
                    self._breaker_trip(host)
                self._health(host, last_err)
                raise ProviderUnavailable(host, "outage", f"giving up on {url.split('?')[0]}"
                                          f" ({last_err})") from None
        if host in self.OPTIONAL_HOSTS:
            self.host_failed(host)
        if last_kind == "outage" and full_ladder:           # (never "rate_limited")
            self._breaker_trip(host)
        self._health(host, last_err or last_kind)
        raise ProviderUnavailable(host, last_kind, f"giving up on {url.split('?')[0]}"
                                  + (f" ({last_err})" if last_err else ""))


def outage_notes(http):
    """{host: note} for every host whose every live call failed this run (Http's
    run_failures / run_ok): a host down for the whole run, or a bad hostname, would
    otherwise show only as missing evidence (engine's provider_notes, validate's notes)."""
    fails, ok = getattr(http, "run_failures", None) or {}, getattr(http, "run_ok", None) or set()
    errs = getattr(http, "run_errors", None) or {}
    return {h: f"outage: every {h} call failed this run ({n}x, last: {errs.get(h, '?')})"
            for h, n in fails.items() if n and h not in ok}


def q(s):
    return urllib.parse.quote(str(s))


# --------------------------------------------------------------------------
# MusicBrainz
# --------------------------------------------------------------------------
def mb_credit(rel):
    """The credit as printed on the release ("credited as" names)."""
    return "".join(a.get("name", "") + a.get("joinphrase", "")
                   for a in rel.get("artist-credit", []) if isinstance(a, dict))


def mb_canonical_artists(rel):
    """The artists' own MusicBrainz names, one per artist - the list Picard
    exposes as %_albumartists% and the plugin writes one name per artist."""
    return "; ".join((a.get("artist") or {}).get("name") or a.get("name", "")
                     for a in rel.get("artist-credit", []) if isinstance(a, dict))


_MB_DISCOGS_RE = re.compile(r"discogs\.com/(?:[a-zA-Z-]+/)?release/(\d+)", re.I)
_MB_MASTER_RE = re.compile(r"discogs\.com/(?:[a-zA-Z-]+/)?master/(\d+)", re.I)


def mb_group_master(group):
    """The one Discogs MASTER id a MusicBrainz release group links (its discogs.com/master/N
    URL relationship), when the group's relationships were fetched; "" when none, or several
    (a group linked to two duplicate masters: neither is known to be the album's)."""
    found = {m.group(1) for r in (group or {}).get("relations") or [] if r.get("target-type") in (None, "url")
             for m in [_MB_MASTER_RE.search(str((r.get("url") or {}).get("resource") or ""))] if m}
    return found.pop() if len(found) == 1 else ""


def mb_group_types(group):
    """A release group's primary and secondary types ("Album", "Compilation"), as Picard
    compares them with your preferred release types; [] when the group was not fetched."""
    group = group or {}
    return [str(t).strip() for t in [group.get("primary-type"), *(group.get("secondary-types") or [])]
            if t and str(t).strip()]


def mb_linked_discogs_ids(rel):
    """Every Discogs RELEASE id MusicBrainz links this release to, in the order listed
    (several when Discogs lists the pressing twice). Master links are ignored: a master is
    the work, not a pressing. match_core.same_pressing reads them all: a Discogs release
    not among them is another pressing for MusicBrainz."""
    out = []
    for r in rel.get("relations") or []:
        if r.get("target-type") != "url":
            continue
        m = _MB_DISCOGS_RE.search((r.get("url") or {}).get("resource") or "")
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out


def mb_linked_discogs_id(rel):
    """The (first) Discogs RELEASE id MusicBrainz links this release to, if any - the
    same rule as the plugin's discogs_release_id_from_mb(); mb_linked_discogs_ids has all."""
    ids = mb_linked_discogs_ids(rel)
    return ids[0] if ids else ""


_MB_VIDEO_FORMATS = ("dvd-video", "blu-ray", "dvd", "vhs", "hd-dvd", "vcd", "svcd")


def mb_sort_names(rel):
    """Each artist's MusicBrainz sort name (Latin script, e.g. "Kitaro" for 喜多郎)."""
    return "; ".join((a.get("artist") or {}).get("sort-name", "")
                     for a in rel.get("artist-credit", []) if isinstance(a, dict))


def mb_candidate(rel):
    tracks = []
    audio_media = [m for m in rel.get("media", [])
                   if str(m.get("format") or "").lower() not in _MB_VIDEO_FORMATS]
    kinds = {mc.media_kind(m.get("format")) for m in audio_media} - {""}
    mixed_vinyl = bool(kinds & {"vinyl", "cassette"}) and bool(kinds & {"cd", "digital"})
    disc_titles, pregaps = {}, []
    for mi, media in enumerate(audio_media, 1):
        if media.get("title"):
            disc_titles[mi] = media["title"]       # a box set's discs: "The Legacy", "The New Order"
        box_vinyl = mixed_vinyl and mc.media_kind(media.get("format")) in ("vinyl", "cassette")
        pregap = media.get("pregap")
        if pregap and not (pregap.get("recording") or {}).get("video"):
            # the hidden track before track 1 (HTOA), numbered 0: compared only with a rip
            # that has it as a file numbered 0 (match_core.with_pregap)
            pregaps.append({"disc": mi, "title": pregap.get("title", ""), "num": 0, "pregap": True,
                            "secs": (pregap.get("length") or 0) / 1000.0})
        for t in media.get("tracks", []):
            if (t.get("recording") or {}).get("video"):
                continue                               # an enhanced CD's video, never an audio file
            tracks.append({"disc": mi, "title": t.get("title", ""),
                           "secs": (t.get("length") or 0) / 1000.0})
            if box_vinyl:
                tracks[-1]["box_vinyl"] = True         # a 7" in a CD box (Kreator - Dying Alive)
    infos = rel.get("label-info") or []
    group = rel.get("release-group") or {}
    # each medium's format name as Picard compares it with your preferred formats ("CD",
    # '12" Vinyl'); unknown (none) when a medium names none
    names = [str(m.get("format") or "").strip() for m in audio_media]
    return {
        "source": "mb", "id": rel["id"], "url": f"https://musicbrainz.org/release/{rel['id']}",
        "discogs_id": mb_linked_discogs_id(rel),
        "discogs_ids": mb_linked_discogs_ids(rel),       # every one linked (same_pressing)
        # the album (release group), its types and its Discogs master: what tells pressings of
        # one album from other albums (match_core.same_album, Picard's preferred types/formats)
        "release_group": str(group.get("id") or ""), "release_types": mb_group_types(group),
        "discogs_master": mb_group_master(group),
        "media_formats": names if names and all(names) else [],
        # every medium (a DVD's and Blu-ray's too), its format and its audio tracks ("index": the
        # track's place on its medium): which tracks of it a version folder of yours is
        # (match_core.version_view; the tags are made for those, mb_tags.track_keys)
        "media_info": mb_media_info(rel),
        "artist": mb_credit(rel), "artist_alt": mb_canonical_artists(rel),
        "artist_sort": mb_sort_names(rel),
        "title": rel.get("title", ""),
        "year": int(rel["date"][:4]) if str(rel.get("date") or "")[:4].isdigit() else None,
        "date": str(rel.get("date") or ""),
        "script": (rel.get("text-representation") or {}).get("script") or "",
        "country": rel.get("country", ""),
        "label": "; ".join(sorted({i["label"]["name"] for i in infos if i.get("label")})),
        "catnos": {mc.norm_code(i.get("catalog-number")) for i in infos if i.get("catalog-number")},
        "catno_shown": ", ".join(dict.fromkeys(i["catalog-number"] for i in infos if i.get("catalog-number"))),
        "barcodes": {mc.norm_code(rel["barcode"])} if rel.get("barcode") else set(),
        "disc_count": len([m for m in audio_media if m.get("tracks")]) or len(audio_media) or 1,
        "tracks": tracks, "pregap_tracks": pregaps, "disc_titles": disc_titles, "packaging": mc.packaging_kind(rel.get("packaging")),
        "formats": {k for k in (mc.media_kind(m.get("format")) for m in audio_media) if k},
        "hires_media": mc.hires_media(m.get("format") for m in audio_media),
        "sacd_media": mc.sacd_media(m.get("format") for m in audio_media),        # MED-3
        "enhanced": any("enhanced" in str(m.get("format") or "").casefold() for m in rel.get("media") or []),
        "unofficial": str(rel.get("status") or "").casefold() == "bootleg",       # R9
        # (the release's and its group's genres, when the lookup carried them: what an album
        # rule on the identified release's genre checks)
        "genre": _genre_names(list(rel.get("genres") or []) + list((rel.get("release-group") or {}).get("genres") or [])),
    }


def mb_media_info(rel):
    """[{"position", "format", "tracks": [{"title", "secs", "index"}]}] of every medium of a
    MusicBrainz release, video recordings left out (as mb_tags.track_keys leaves them out)."""
    out = []
    for disc, m in enumerate(rel.get("media") or [], 1):
        tracks = [{"title": t.get("title", ""), "secs": (t.get("length") or 0) / 1000.0, "index": k}
                  for k, t in enumerate(m.get("tracks") or []) if not (t.get("recording") or {}).get("video")]
        out.append({"position": m.get("position") or disc, "format": str(m.get("format") or ""), "tracks": tracks})
    return out


def _genre_names(genres):
    """MusicBrainz genres ([{"name": ...}]) or Discogs ones (strings): their names, once each, in order."""
    out = []
    for g in genres:
        name = str((g.get("name") if isinstance(g, dict) else g) or "").strip()
        if name and name.casefold() not in {x.casefold() for x in out}:
            out.append(name)
    return out


def _mb_track_total(rel):
    return sum(int(m.get("track-count") or 0) for m in rel.get("media") or []
               if str(m.get("format") or "").lower() not in _MB_VIDEO_FORMATS)


def version_priority(album, catnos=(), country="", formats=()):
    """Which versions of an album to fetch first (lower is sooner): your
    catalogue number, then the country and format your folder names."""
    mine = {mc.norm_code(c) for c in album.get("catnos") or ()}
    theirs = {mc.norm_code(c) for c in catnos if c}
    folder = mc.fold(album.get("folder") or "")
    cc = mc.country_code(country)
    named = {c for n, c in mc._COUNTRY_NAMES.items() if len(n) > 2 and f" {n} " in f" {folder} "}
    kinds = {mc.media_kind(f) for f in formats if f}
    want = fetch_kinds(album)
    return (0 if mine & theirs else 1, 0 if cc and cc in named else 1, 0 if want & kinds else 1)


def fetch_kinds(album):
    """The formats your copy points to, to fetch those versions first: what copy_source
    says (folder name, rip log, whole CD sectors, a shop's track ID), else hi-res audio (a
    download, or a vinyl rip), else scans of a physical copy (a CD). Slayer - South Of
    Heaven [RM 2015] is 24/192: its 2015 download is one version among dozens of CDs."""
    kind = copy_source(album)
    hires = (album.get("audio") or {}).get("hires")
    if kind == "cd" and hires and not album.get("media_hint"):
        # a rip log or cue sheet beside hi-res audio is left from an earlier CD rip, as
        # format_against has it (Cynic - Traced in Air: 24-bit files, a 2008 CD log)
        kind = ""
    if kind:
        return {kind}
    if hires:
        return {"digital", "vinyl"}
    return {"cd"} if (album.get("scans") or {}).get("physical") else set()


# Versions of a Discogs master fetched: a few, or more when the album has a barcode or
# catalogue number to tell them apart (each is one request; Discogs allows 60 a minute)
MASTER_VERSIONS = 6
MASTER_VERSIONS_WITH_CODES = 25


def label_catnos(album, limit=4):
    """(label, catalogue number) pairs to search: the album's labels (tags, notes) with its
    catalogue numbers, at most `limit` (each is one more request). Inside a quoted Lucene
    phrase only " and \\ are special, so both become spaces."""
    def clean(x):
        return str(x).replace("\\", " ").replace('"', " ").strip()
    labels = [clean(x) for x in album.get("search_labels") or () if clean(x)][:2]
    # "label_catnos": the numbers to pair with the labels when they differ from the ones
    # searched alone (the documents step searches only a new label with the old numbers)
    catnos = album.get("label_catnos", album.get("catno_search")) or ()
    catnos = [clean(x) for x in catnos if clean(x)][:3]
    return [(label, c) for label in labels for c in catnos][:limit]


def mb_search(http, album, cfg):
    # Inside a quoted Lucene phrase only " and \ are special.
    title = mc.search_title(album["title"]).replace("\\", " ").replace('"', " ")
    first = mc.artist_parts(album["artist"])[0].replace("\\", " ").replace('"', " ")
    short = re.split(r"\s+[-:–]\s+", title)[0].strip() or title
    queries = []
    for b in sorted(album["barcodes"]):
        for form in mc.barcode_variants(b):
            queries.append(f"barcode:{form}")
    for a in album.get("asins") or ():
        queries.append(f"asin:{a}")
    catnos = [c.replace("\\", " ").replace('"', " ") for c in album.get("catno_search", [])[:3]]
    narrow = [f'catno:"{c}" AND artist:"{first}"' for c in catnos]
    queries += narrow
    # The label with the catalogue number: finds the pressing when the artist is credited
    # differently (an alias, Various Artists) and a bare number is shared by other labels.
    # Asked before the bare number, whose hits from other labels would end the search first.
    wide = [f'label:"{label}" AND catno:"{c}"' for label, c in label_catnos(album)]
    wide += [f'catno:"{c}"' for c in catnos]
    queries += wide
    if album.get("pressing_year"):
        queries.append(f'release:"{title}" AND artist:"{first}" AND date:{album["pressing_year"]}*')
    # The format the folder names ("[Vinyl Rip]") searched for itself: the title alone
    # turns up the CDs first (Sunn O))) & Ulver - Terrestrials: only CDs were fetched)
    fmt = {"vinyl": "vinyl", "cd": "cd", "digital": '"digital media"'}.get(album.get("media_hint") or "")
    if fmt:
        queries.append(f'release:"{title}" AND artist:"{first}" AND format:{fmt}')
    # Collaborators are separate artists in MusicBrainz, so search ONE name.
    queries.append(f'release:"{title}" AND artist:"{first}"')
    typeless = mc.drop_release_type(title)
    if typeless != title:                                   # "Garbage EP" is listed as "Garbage"
        queries.append(f'release:"{typeless}" AND artist:"{first}"')
    queries.append(f'release:"{title}"')
    if short != title:
        queries.append(f'release:"{short}" AND artist:"{first}"')
    queries.append(f'"{title}" AND "{first}"')
    n_files = len(album["tracks"])
    seen, hits = set(), []
    # numbers read off scans (narrow_codes_first): a misread one matches some other
    # release when asked alone, so the wider queries go only when the artist's found nothing
    skip_wide = set(wide) if album.get("narrow_codes_first") and narrow else set()
    narrow_found = False
    for query in queries:
        if query in skip_wide and narrow_found:
            continue
        data = http.get_json(f"https://musicbrainz.org/ws/2/release/?query={q(query)}&fmt=json&limit=15", "mb")
        if query in narrow and (data or {}).get("releases"):
            narrow_found = True
        for r in (data or {}).get("releases", []):
            if r["id"] not in seen:
                seen.add(r["id"])
                hits.append(r)
        if len(hits) >= 5 and any(r.get("track-count") == n_files for r in hits):
            break
    same = [r for r in hits if r.get("track-count") == n_files]

    def prio(r):
        return version_priority(album, catnos=[i.get("catalog-number") for i in r.get("label-info") or []],
                                country=r.get("country"), formats=[m.get("format") for m in r.get("media") or []])
    same.sort(key=prio)                                    # stable: search rank among equals
    chosen = (same or hits)[:cfg["mb_detail_fetch"] if same else 2]
    # The pressing your folder names (its catalogue number or country) is fetched
    # whatever its track count: Kreator Endorama (2003 Art Music, AMG 099, Russia) is
    # a 15-track enhanced CD, and must not lose to the 13-track Japanese one unseen.
    have = {r["id"] for r in chosen}
    named = [r for r in hits if r["id"] not in have and prio(r)[:2] != (1, 1)]
    chosen += sorted(named, key=prio)[:4]
    # Releases whose disc ID matches the files are fetched whatever their title
    # (a freeDB-style lookup finds an album the folder names differently).
    have = {r["id"] for r in chosen}
    chosen += [{"id": rid} for rid in sorted(album.get("toc_releases") or ()) if rid not in have][:6]
    have = {r["id"] for r in chosen}
    chosen += [{"id": rid} for rid in sorted((album.get("link_releases") or set()) |
                                             (album.get("isrc_releases") or set())) if rid not in have][:6]
    # The release the files were tagged with before: a candidate scored like the others
    # (its lengths and track count must agree), never taken on the tags' word.
    have = {r["id"] for r in chosen}
    chosen += [{"id": rid} for rid in sorted(album.get("tag_release_mb") or ()) if rid not in have][:4]
    # Every version of the album (its release group): the edition with your track
    # count (bonus tracks, deluxe 2CD) or your pressing may not be among the hits.
    have = {r["id"] for r in chosen}
    rg = next(((r.get("release-group") or {}).get("id") for r in (same or hits)
               if (r.get("release-group") or {}).get("id")), None)
    group = {}
    if rg:
        data = http.get_json(f"https://musicbrainz.org/ws/2/release?release-group={rg}"
                             f"&inc=media+labels&limit=100&fmt=json", "mb")
        group = _digital_count(data, "releases", "release-count",
                               lambda v: [m.get("format") for m in v.get("media") or []])
        versions = [v for v in (data or {}).get("releases", []) if v.get("id") not in have]
        exact = [v for v in versions if _mb_track_total(v) == n_files]
        exact.sort(key=prio)
        chosen += [{"id": v["id"]} for v in exact[:4 if same else 8]]
        have = {r["id"] for r in chosen}
        chosen += [{"id": v["id"]} for v in sorted((v for v in versions if v["id"] not in have
                                                     and prio(v)[:2] != (1, 1)), key=prio)[:4]]
    # Which album each release is a pressing of: the search hits name their release group
    # (with its types), the group's listing its members. A release lookup does not carry
    # it (inc=release-groups would change every cached lookup's address).
    groups = {r["id"]: r["release-group"] for r in hits if (r.get("release-group") or {}).get("id")}
    if rg:
        info = dict(next((g for g in groups.values() if g["id"] == rg), {}), id=rg,
                    relations=_group_relations(http, rg, cfg))
        groups.update({k: info for k, g in groups.items() if g["id"] == rg})
        groups.update({v["id"]: info for v in (data or {}).get("releases", []) if v.get("id")})
    out = []
    for r in chosen:
        det = http.get_json(f"https://musicbrainz.org/ws/2/release/{r['id']}"
                            f"?inc=recordings+artist-credits+labels+url-rels&fmt=json", "mb")
        if det:
            if not det.get("release-group") and groups.get(det["id"]):
                det = dict(det, **{"release-group": groups[det["id"]]})
            out.append(dict(mb_candidate(det), **group.get(det["id"], {})))
    return out


def _group_relations(http, rg, cfg):
    """The release group's URL relationships (its Discogs master link: discogs_master),
    one request per album, not per pressing, and only when something can act on it: Picard's
    preferred formats, between a MusicBrainz and a Discogs pick (match_core.combine,
    same_album). The release types cannot (a Discogs pick has none: its types are unknown,
    and a discouraged MusicBrainz pick leaves one pressing, no choice), so they ask nothing.
    Otherwise only an answer already in the cache is used, so no request is added."""
    url = f"https://musicbrainz.org/ws/2/release-group/{rg}?inc=url-rels&fmt=json"
    try:
        if mc.release_preferences(cfg)["formats"]:
            return (http.get_json(url, "mb") or {}).get("relations") or []
        row = http.db.execute("SELECT body FROM http_cache WHERE url=?", (url,)).fetchone()
        return (json.loads(row[0]) or {}).get("relations") or [] if row and row[0] else []
    except Exception:                                  # a missing link only leaves it unknown
        return []


def _digital_count(data, key, total_key, formats):
    """{id: {"group_digital"/"master_digital": n}} for every entry of a FULL listing of an
    album's versions (a MusicBrainz release group, a Discogs master): how many of them are
    downloads only. Empty when the listing is missing or runs to more than one page, so
    "the only download" is never claimed from part of the list (same_pressing, R7)."""
    entries = (data or {}).get(key) or []
    total = (data or {}).get(total_key)
    if key == "versions":
        pages = (data or {}).get("pagination") or {}
        total = None if (pages.get("pages") or 1) > 1 else pages.get("items", len(entries))
    if not entries or total is None or total > len(entries):
        return {}
    name = "group_digital" if key == "releases" else "master_digital"
    n = sum({mc.media_kind(f) for f in formats(v) if f} - {""} == {"digital"} for v in entries)
    return {str(v.get("id")): {name: n} for v in entries if v.get("id")}


# --------------------------------------------------------------------------
# Discogs
# --------------------------------------------------------------------------
_POS_DISC = re.compile(r"^\s*(?:[A-Za-z]{1,4})?(\d+)\s*[-.]\s*\d+")


def flatten_tracklist(tracklist, dm, parent_position=None):
    """Audio tracks in order. Discogs 'index' entries (a work split into
    movements) carry their audio in nested sub_tracks; headings carry none.
    The plugin's parse_discs() skips index entries, so it cannot be used
    alone for counting.

    A sub-track's own position ("2.1", "2a") numbers it WITHIN its work, not
    a disc, so it takes its parent entry's position for disc numbering."""
    out = []
    for e in tracklist or []:
        if not isinstance(e, dict):
            continue
        kind = str(e.get("type_") or e.get("type") or "track").strip().casefold()
        if kind == "heading":
            continue
        if kind == "index":
            subs = flatten_tracklist(e.get("sub_tracks"), dm,
                                     parent_position=e.get("position") or "")
            if subs:
                # one work: its rows are counted as one track where they list no lengths
                n = len(out)
                out.extend(dict(t, index=t.get("index", n)) for t in subs)
                continue
        pos = parent_position if parent_position is not None else (e.get("position") or "")
        out.append({"position": pos, "title": e.get("title", ""),
                    "secs": dm._parse_duration(e.get("duration"))})
    return out


_VIDEO_POS = re.compile(r"^\s*(?:video(?:\s*clip)?s?|vid|clip|v\d|dvd|bd|blu-?ray|hd-?dvd|vhs|cd-?rom|data|enh(?:anced)?|multimedia|mm)\b",
                        re.I)
_VIDEO_TITLE = re.compile(r"\((?:video|videoclip|video clip|music video|live video|multimedia)[^)]*\)\s*$|"
                          r"^\s*(?:video|videoclip|multimedia|cd-?rom)(?:\s+(?:track|part|section|clip))?\s*[:\-]|"
                          r"\b(?:screensaver|wallpapers?|bonus multimedia|enhanced (?:part|section))\b", re.I)


def drop_video_tracks(tracks, rel):
    """An enhanced CD's videos and data are listed as tracks on Discogs (Kreator -
    Endorama, Art Music AMG 099: 13 songs + 2 videos), but are never audio files. Only
    entries marked as such are dropped: a position like "Video" or "CD-ROM", or a title
    ending "(Video)", and only at the end of a release whose format says Enhanced or
    lists a DVD/CD-ROM part, so a song called "Video Killed the Radio Star" stays."""
    desc = " ".join(" ".join([f.get("name") or ""] + list(f.get("descriptions") or []))
                    for f in rel.get("formats") or []).casefold()
    if not re.search(r"enhanced|dvd|blu-?ray|cd-?rom|multimedia|video", desc):
        return tracks
    out = list(tracks)
    while out and (_VIDEO_POS.match(str(out[-1].get("position") or "")) or _VIDEO_TITLE.search(out[-1]["title"])):
        out.pop()
    # videos marked anywhere by their position ("Video 1") are never audio
    return [t for t in out if not _VIDEO_POS.match(str(t.get("position") or ""))]


# a position on a video disc of a box ("DVD1-3", "BD2-1"): a chapter, never an audio file
_VIDEO_MEDIUM = re.compile(r"^\s*(?:dvd|bd|blu-?ray|hd-?dvd|vhs|video)\s*\d*\s*[-.]?\s*\d", re.I)


# a vinyl or tape side: "A", "B2", "LP-C1", '10"-A1', "MC-B2" (Kreator - Gods Of Violence box)
_SIDE_POS = re.compile(r'(?:(?:LP|MC|Cass(?:ette)?|Tape|Vinyl|\d{1,2}(?:"|\u201d|in(?:ch)?)?)\d*[-.\s])?[A-H]\d{0,2}',
                       re.I)


_NOTES_PACKAGING = re.compile(r"\b(?:packaged|comes|housed|issued|sold)\s+in\s+(?:a\s+|an\s+)?"
                              r"(jewel[- ]?case|digi-?pa(?:c?k)|digi-?book|media-?book)", re.I)


def discogs_packaging(rel):
    """A Discogs entry's packaging: its formats' descriptions, then their free text
    ("Digipak" typed there), then its notes ("Packaged in jewel case"; Sadist - Spellbound:
    Fono's jewel case and 300-copy digipak share every number)."""
    for f in rel.get("formats") or []:
        for d in list(f.get("descriptions") or []):
            if mc.packaging_kind(d):
                return mc.packaging_kind(d)
    for f in rel.get("formats") or []:
        if mc.packaging_kind(f.get("text")):
            return mc.packaging_kind(f.get("text"))
    m = _NOTES_PACKAGING.search(str(rel.get("notes") or ""))
    return mc.packaging_kind(m.group(1)) if m else ""


# Discogs format names that are one physical disc each (qty counts the discs)
_DISC_MEDIA = {"cd", "cdr", "sacd", "dvd", "dvdr", "blu-ray", "blu-ray-r", "hd dvd", "vinyl", "minidisc"}
# a heading that names a disc: "CD 1", "CD2", "Disc Two", "CD 2 - Instrumentals", "Bonus CD"
_DISC_HEADING = re.compile(r"^\s*(?:(?:bonus|extra|live)\s+)?(?:cd|disc|disk|dvd|sacd)(?:\s*-?\s*"
                           r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|[ivx]+)\b|\s*$|\s*[-:(])",
                           re.I)


def _disc_media_total(rel):
    """How many physical discs the formats list (CD qty 1 + CD qty 1 = 2), or 0 when a
    format is not a disc (a file, a box) or names no quantity we can read."""
    total = 0
    for f in rel.get("formats") or []:
        if not isinstance(f, dict):
            return 0
        name = str(f.get("name") or "").strip().casefold()
        if name not in _DISC_MEDIA:
            if name in ("box set", "all media"):
                continue                       # the box holds the discs, it is not one
            return 0
        try:
            total += int(str(f.get("qty") or "1").strip())
        except (TypeError, ValueError):
            return 0
    return total


def discogs_discs(rel, dm):
    """(discs, disc count) of a Discogs release: the plugin's parse_discs and
    release_disc_count, except where heading rows split the release into more discs than
    its formats hold (Feared - Reborn, 2 CDs listed "CD 1 - Album", "SIDE A", "SIDE B",
    "CD 2 - Instrumentals"). Then only a heading that names a disc starts one; the sides
    and parts under it stay on its disc, numbered on through it. Vinyl and tape keep the
    plugin's rule (their sides are real)."""
    discs = dm.parse_discs(rel)
    count = dm.release_disc_count(rel)
    media = _disc_media_total(rel)
    kinds = {mc.media_kind(f.get("name")) for f in rel.get("formats") or [] if isinstance(f, dict)}
    if media < 2 or len(discs) <= media or kinds & {"vinyl", "cassette"}:
        return discs, count
    if any(_POS_DISC.match(str(t.get("position") or "")) for d in discs for t in d["tracks"]):
        return discs, count                     # numbered by position prefixes, not headings
    rows = [r for r in rel.get("tracklist") or [] if isinstance(r, dict)]
    flatten = getattr(dm, "_flatten_index_entries", None)   # a work's movements, as parse_discs
    rows = flatten(rows) if flatten else rows
    out, current = [], None
    for row in rows:
        kind = str(row.get("type_") or "track").strip().casefold()
        title = str(row.get("title") or "").strip()
        if kind == "heading" and _DISC_HEADING.match(title):
            current = {"number": len(out) + 1, "title": title, "tracks": []}
            out.append(current)
        elif kind == "track":
            if current is None:
                current = {"number": 1, "title": "", "tracks": []}
                out.append(current)
            current["tracks"].append(row)
    out = [d for d in out if d["tracks"]]
    if len(out) != media:
        return discs, count                     # the headings do not say which disc is which
    for n, d in enumerate(out, start=1):
        d["number"] = n
    return out, media


def discogs_candidate(rel, dm):
    # The credit exactly as the plugin writes it (format_artist_credit honours
    # Discogs' "credited as" names and join words), plus the canonical names,
    # since files may carry either form.
    artists = dm.format_artist_credit(rel.get("artists"))
    canonical = " & ".join(dm.clean_artist_name(a.get("name")) for a in rel.get("artists", [])
                           if isinstance(a, dict))
    flat = drop_video_tracks(flatten_tracklist(rel.get("tracklist"), dm), rel)
    discs, disc_count = discogs_discs(rel, dm)  # the plugin's rule, sub-headings kept on their disc
    tracks = []
    kinds = {mc.media_kind(f.get("name")) for f in rel.get("formats") or []} - {""}
    mixed_vinyl = bool(kinds & {"vinyl", "cassette"}) and bool(kinds & {"cd", "digital"})
    for t in flat:
        m = _POS_DISC.match(str(t["position"]))
        tracks.append({"disc": int(m.group(1)) if m else 1, "title": t["title"], "secs": t["secs"]})
        if "index" in t:
            tracks[-1]["index"] = t["index"]          # a sub-track of an index track
        if _VIDEO_MEDIUM.match(str(t["position"])):
            tracks[-1]["video"] = True                # on a DVD or other video disc ("DVD1-3")
        if mixed_vinyl and _SIDE_POS.fullmatch(str(t["position"]).strip()):
            tracks[-1]["box_vinyl"] = True        # a 7" in a CD box: side A / B
    if discs and len(discs) > 1 and not any(_POS_DISC.match(str(t["position"])) for t in flat):
        # numbered by heading rows instead of position prefixes
        n = 0
        for d in discs:
            for _ in d["tracks"]:
                if n < len(tracks):
                    tracks[n]["disc"] = d["number"]
                n += 1
    # a box set's discs named by the heading rows before them ("The Legacy", "The New Order")
    disc_titles, heading = {}, ""
    for row in rel.get("tracklist") or []:
        if row.get("type_") == "heading":
            heading = str(row.get("title") or "").strip()
        elif row.get("type_", "track") == "track":
            m = _POS_DISC.match(str(row.get("position") or ""))
            if m and heading and int(m.group(1)) not in disc_titles:
                disc_titles[int(m.group(1))] = heading
    labels = rel.get("labels") or []
    barcodes = {mc.norm_code(i["value"]) for i in rel.get("identifiers", [])
                if i.get("type") == "Barcode" and i.get("value")}
    year = rel.get("year") or 0
    if not year and str(rel.get("released") or "")[:4].isdigit():
        year = int(rel["released"][:4])
    return {
        "source": "discogs", "id": str(rel["id"]),
        "url": rel.get("uri") or f"https://www.discogs.com/release/{rel['id']}",
        "artist": artists or canonical, "artist_alt": canonical,
        "title": rel.get("title", ""), "year": year or None,
        "date": str(rel.get("released") or ""),
        "country": rel.get("country", ""),
        "label": "; ".join(sorted({dm.clean_artist_name(l.get("name")) for l in labels})),
        "catnos": {mc.norm_code(l["catno"]) for l in labels
                   if l.get("catno") and str(l["catno"]).casefold() != "none"},
        "catno_shown": ", ".join(dict.fromkeys(l["catno"] for l in labels
                                               if l.get("catno") and str(l["catno"]).casefold() != "none")),
        "barcodes": barcodes, "disc_count": disc_count, "tracks": tracks, "disc_titles": disc_titles,
        # matrix / runout, SID and label codes: compared with what the disc scans read
        "matrix": [(str(i.get("type")), str(i["value"])) for i in rel.get("identifiers", [])
                   if str(i.get("type") or "").casefold() in mc.MATRIX_KINDS and i.get("value")],
        "packaging": discogs_packaging(rel),
        "formats": {k for k in (mc.media_kind(f.get("name")) for f in rel.get("formats") or []) if k},
        # the album (its master) and its format names, for Picard's preferred formats
        # (match_core.same_album, release_format_score: compared by kind only)
        "master_id": str(rel.get("master_id") or "") if rel.get("master_id") else "",
        "media_formats": discogs_format_names(rel),
        # an SACD's descriptions may name it ("CD" with "SACD", "Hybrid")
        "hires_media": mc.hires_media([f.get("name") for f in rel.get("formats") or []]
                                      + [d for f in rel.get("formats") or [] for d in f.get("descriptions") or []]),
        "sacd_media": mc.sacd_media([f.get("name") for f in rel.get("formats") or []]         # MED-3
                                    + [d for f in rel.get("formats") or [] for d in f.get("descriptions") or []]),
        "enhanced": "enhanced" in _format_words(rel),
        # a download's codecs as its format names them ("File: FLAC", "File: MP3, 320 kbps")
        "file_codecs": {d for f in rel.get("formats") or [] if str(f.get("name") or "").casefold() == "file"
                        for d in (str(x).strip().casefold() for x in f.get("descriptions") or [])
                        if d in mc.LOSSLESS_CODECS or d in mc.LOSSY_CODECS},
        # Discogs marks a bootleg "Unofficial Release" among the format's descriptions (R9:
        # Kreator - Enemy Of God Revisited, the Russian 8382890; BTBAM - Alaska)
        "unofficial": "unofficial release" in _format_words(rel),
        "genre": _genre_names(rel.get("genres") or []),
    }


# Discogs format descriptions that make a medium a sub-format in Picard's names (a CD
# described "HDCD" is MusicBrainz's "HDCD", a vinyl described '7"' its '7" Vinyl')
_DISCOGS_SUB_FORMATS = {"hdcd": "HDCD", "shm-cd": "SHM-CD", "blu-spec cd": "Blu-spec CD", "hybrid": "Hybrid SACD",
                        "enhanced": "Enhanced CD", '7"': '7" Vinyl', '10"': '10" Vinyl', '12"': '12" Vinyl'}


def discogs_format_names(rel):
    """Each Discogs format's name, made a sub-format's when its descriptions say so ('7"' on
    a vinyl, "HDCD" on a CD; "CDr" is "CD-R"), so a sized vinyl or special CD is never read
    as the generic "Vinyl" or "CD" your preferred formats name. [] when a format has no name."""
    out = []
    for f in rel.get("formats") or []:
        name = str(f.get("name") or "").strip()
        if not name:
            return []
        name = {"cdr": "CD-R"}.get(name.casefold(), name)
        subs = [_DISCOGS_SUB_FORMATS[d] for d in (str(x).strip().casefold() for x in f.get("descriptions") or [])
                if d in _DISCOGS_SUB_FORMATS and mc.media_kind(_DISCOGS_SUB_FORMATS[d]) == mc.media_kind(name)]
        out.append(subs[0] if subs else name)
    return out


def _format_words(rel):
    """A Discogs release's format names, descriptions and free text, lower case, one per item."""
    return {str(x).strip().casefold() for f in rel.get("formats") or []
            for x in [f.get("name"), f.get("text"), *(f.get("descriptions") or [])] if x}


def discogs_search(http, album, cfg, dm):
    title = mc.search_title(album["title"])
    first = mc.artist_parts(album["artist"])[0]
    short = re.split(r"\s+[-:–]\s+", title)[0].strip() or title
    searches = []
    for b in sorted(album["barcodes"]):
        for form in mc.barcode_variants(b):
            searches.append(f"barcode={q(form)}")
    catnos = album.get("catno_search", [])[:3]
    narrow = [f"catno={q(c)}&artist={q(first)}" for c in catnos]
    searches += narrow
    # The label with the number before the bare number: a number other labels share
    # fills the results first and the search stops before asking for the label
    wide = [f"label={q(label)}&catno={q(c)}" for label, c in label_catnos(album)]
    wide += [f"catno={q(c)}" for c in catnos]
    searches += wide
    if album.get("pressing_year"):
        searches.append(f"artist={q(first)}&release_title={q(title)}&year={album['pressing_year']}")
    dfmt = {"vinyl": "Vinyl", "cd": "CD", "digital": "File"}.get(album.get("media_hint") or "")
    if dfmt:                                                # the format the folder names, first
        searches.append(f"artist={q(first)}&release_title={q(title)}&format={dfmt}")
    searches.append(f"artist={q(first)}&release_title={q(title)}")
    typeless = mc.drop_release_type(title)
    if typeless != title:                                   # "Garbage EP" is listed as "Garbage"
        searches.append(f"artist={q(first)}&release_title={q(typeless)}")
    # Compilations are filed under "Various" and works under their performers,
    # so also search the title without any artist.
    searches.append(f"release_title={q(title)}")
    searches.append(f"q={q(first + ' ' + title)}")
    if short != title:
        searches.append(f"artist={q(first)}&release_title={q(short)}")
    seen, results = set(), []
    # numbers read off scans: the wider searches only when the artist's found nothing (as mb_search)
    skip_wide = set(wide) if album.get("narrow_codes_first") and narrow else set()
    narrow_found = False
    for s in searches:
        if s in skip_wide and narrow_found:
            continue
        data = http.get_json(f"https://api.discogs.com/database/search?type=release&per_page=15&{s}", "discogs")
        if s in narrow and (data or {}).get("results"):
            narrow_found = True
        for r in (data or {}).get("results", []):
            if r["id"] not in seen:
                seen.add(r["id"])
                results.append(r)
        if len(results) >= 8:
            break

    def pre(r):
        a, t = dm.split_discogs_title(r.get("title", ""))
        s = 0.35 * mc.artist_sim(album["artist"], dm.clean_artist_name(a)) + 0.65 * mc.title_sim(album["title"], t)
        return s + (0.05 if str(r.get("year") or "") == str(album["year"] or "") else 0)

    results.sort(key=pre, reverse=True)
    out = []
    for r in results[:cfg["discogs_detail_fetch"]]:
        if pre(r) < 0.5 and not album["barcodes"] and not album.get("catno_search"):
            continue
        det = http.get_json(f"https://api.discogs.com/releases/{r['id']}", "discogs")
        if det:
            out.append(discogs_candidate(det, dm))
    # Discogs releases the CUETools database links to your disc, or your tags link to; then
    # the release the files were tagged with before (scored like the others, not evidence)
    have = {str(c["id"]) for c in out}
    linked = sorted(set(album.get("toc_discogs") or ()) | set(album.get("tag_discogs_links") or ()))[:4]
    tagged = sorted(set(album.get("tag_release_discogs") or ()) - set(linked))[:4]
    for rid in linked + tagged:
        if rid not in have:
            det = http.get_json(f"https://api.discogs.com/releases/{rid}", "discogs")
            if det:
                out.append(discogs_candidate(det, dm))
    # Every version of the album (its Discogs master) when none of these has your
    # track count or your catalogue number.
    n_files = len(album["tracks"])
    mine = {mc.norm_code(c) for c in album.get("catnos") or ()}
    found_count = any(len(c["tracks"]) == n_files for c in out)
    found_catno = not mine or any(mine & set(c["catnos"]) for c in out)
    hint = album.get("media_hint") or ""
    want = fetch_kinds(album)
    found_format = not want or any(want & set(c.get("formats") or ()) for c in out)
    master = next((r.get("master_id") for r in results if r.get("master_id") and pre(r) >= 0.7), None)
    # a download among them: the master's listing tells whether it is the only one (R7)
    has_download = any(c.get("formats") == {"digital"} for c in out)
    data = None
    if master and (has_download or not (found_count and found_catno and found_format)):
        data = http.get_json(f"https://api.discogs.com/masters/{master}/versions?per_page=100", "discogs")
    if master and not (found_count and found_catno and found_format):
        have = {str(c["id"]) for c in out}
        versions = [v for v in (data or {}).get("versions", []) if str(v.get("id")) not in have]
        def vprio(v):
            return version_priority(album, catnos=[v.get("catno") or ""], country=v.get("country"),
                                    formats=str(v.get("format") or "").split(","))
        versions.sort(key=vprio)
        # With a barcode or catalogue number to compare, look further down the list (a
        # master can have a hundred pressings, and the list rarely shows barcodes). After
        # the first six it stops once one carries your code and no other listed version
        # has your catalogue number, so every pressing that shares it is still compared.
        barcodes = {mc.barcode_key(b) for b in album["barcodes"]} - {""}
        found = False
        for i, v in enumerate(versions[:MASTER_VERSIONS_WITH_CODES if (barcodes or mine) else MASTER_VERSIONS]):
            if found and i >= MASTER_VERSIONS and vprio(v)[0]:
                break
            det = http.get_json(f"https://api.discogs.com/releases/{v['id']}", "discogs")
            if det:
                cand = discogs_candidate(det, dm)
                code = bool((mine & set(cand["catnos"]))
                            or barcodes & {mc.barcode_key(b) for b in cand["barcodes"]})
                if (len(cand["tracks"]) == n_files or code
                        or (hint and hint in (cand.get("formats") or ()))):
                    out.append(cand)
                found = found or code
    counts = _digital_count(data, "versions", None,
                            lambda v: v.get("major_formats") or [v.get("format") or ""])
    return [dict(c, **counts.get(str(c["id"]), {})) for c in out]


# --------------------------------------------------------------------------
# Optional OCR
# --------------------------------------------------------------------------
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"}


OCR_VERSION = "ocr-4"   # bump when the OCR code changes, so cached OCR is redone


# Images that are not scans of the release: covers saved from the web, rip
# screenshots and spectrum/analysis pictures ("tech.info/Au.jpg", "ksnip_...").
_NOT_A_SCAN = re.compile(r"^(cover|folder|front|albumart\w*|thumb\w*|ksnip\S*|screenshot\S*|spectr\S*|au\d*|"
                         r"expose\d*|mv|dr|dynamic\S*|\d{2,4}x\d{2,4}\S*)$", re.I)
_NOT_A_SCAN_DIR = re.compile(r"^(tech[ ._-]?info|info|spectr\S*|analysis|screens?(hots)?)$", re.I)
# Pictures saved from shops and databases, not scanned from your copy: Amazon product
# images ("71-Q6hBQwTL._UF1000,1000_QL80_.jpg", "._AC_SL1500_"), Discogs release images
# ("R-4847696-1388801849-4582.jpg"). Their barcode is some other pressing's (Samael -
# Ceremony Of Opposites: the 2021 MDD reissue's, in a folder ripped in 2005).
_WEB_IMAGE = re.compile(r"\._(?:[A-Z]{2}\d|[A-Z]{2}_)|^R-\d{3,}-\d{9,}(?:-\d+)?$", re.I)


def web_image(path):
    return bool(_WEB_IMAGE.search(Path(path).stem))


_PHYSICAL_NAME = re.compile(r"back|rear|tray|inlay|disc|disk|(?<![a-z])cd\d*(?![a-z])|obi|matrix|spine|booklet|"
                            r"inside|insert|(?<![a-z])label|sticker|case", re.I)


def scan_evidence(album_dir):
    """Does the folder hold scans of a physical copy (a back cover, tray card,
    booklet, disc or obi)? A download comes with a cover image at most, so such
    scans mean the disc was owned. Returns {"physical": bool, "why": str}."""
    try:
        from PIL import Image
    except ImportError:
        Image = None
    for dirpath, dirs, names in os.walk(album_dir):
        dirs[:] = [d for d in dirs if not _NOT_A_SCAN_DIR.match(d)]
        for n in sorted(names):
            p = Path(dirpath) / n
            if p.suffix.lower() not in IMAGE_EXT or p.suffix.lower() == ".gif" or _NOT_A_SCAN.match(p.stem) \
                    or web_image(p):
                continue
            if _PHYSICAL_NAME.search(p.stem):
                return {"physical": True, "why": f"a scan named {p.name}"}
            if Image is not None:
                try:
                    with Image.open(p) as im:
                        w, h = im.size
                except Exception:
                    continue
                if w >= 1000 and 1.15 <= w / max(h, 1) <= 1.5:
                    return {"physical": True, "why": f"a tray-card scan ({p.name})"}
    return {"physical": False, "why": ""}


def packaging_evidence(album_dir):
    """The packaging your scans are named after: "scan-digipak.jpg", "Digi Cover.png",
    "Digibook 01.jpg", "Jewel case back.jpg" (Cynic - Carbon-Based Anatomy: the jewel case
    and digipak pressings share barcode and number; only the scans tell them apart).
    Returns a set of mc.packaging_kind values; empty when the names say nothing. A scan
    named "Inlay" or "Tray" (a jewel case's back card and tray) is a jewel case when no
    scan names another packaging (Sadist - Spellbound)."""
    out, jewel_parts = set(), False
    for dirpath, dirs, names in os.walk(album_dir):
        dirs[:] = [d for d in dirs if not _NOT_A_SCAN_DIR.match(d)]
        for n in names:
            p = Path(dirpath) / n
            if p.suffix.lower() in IMAGE_EXT and not web_image(p):
                k = mc.packaging_kind(p.stem)
                if k:
                    out.add(k)
                elif re.search(r"(?<![a-z])(?:inlay|tray)(?![a-z])", p.stem.casefold()):
                    jewel_parts = True
    return out or ({"jewel"} if jewel_parts else set())


def list_images(base, max_n):
    """Every image under the album folder (subfolders included), most likely
    to carry a barcode or catalogue number first (ocr_order: the roles whose scans paid
    off most, learned). Exact duplicate files (e.g. 'scan 1 (copy).jpg') are scanned once.

    Returns (paths, key): the key identifies exactly these image contents, so
    OCR results can be cached and reused while the images are unchanged."""
    import hashlib
    found = []
    for dirpath, _d, names in os.walk(base):
        for n in sorted(names):
            p = Path(dirpath) / n
            if p.suffix.lower() in IMAGE_EXT and not web_image(p):
                found.append(p)
    found = ocr_order.rank(found, tie=_scan_shape)
    seen, out, digests = set(), [], []
    for p in found:
        try:
            digest = hashlib.md5(p.read_bytes()).hexdigest()
        except OSError:
            continue
        if digest in seen:
            continue
        seen.add(digest)
        out.append(str(p))
        digests.append(digest)
        _DIGESTS[str(p)] = digest
        if max_n and len(out) >= max_n:
            break
    key = hashlib.md5((OCR_VERSION + "|" + "|".join(digests)).encode()).hexdigest()
    return out, key


# each scan's contents (md5), as list_images last read them: the per-scan cache keys
_DIGESTS = {}


def _scan_digest(path):
    import hashlib
    d = _DIGESTS.get(str(path))
    if d is None:
        try:
            d = hashlib.md5(Path(path).read_bytes()).hexdigest()
        except OSError:
            d = "path:" + str(path)                   # not readable: a reading of nothing
        _DIGESTS[str(path)] = d
    return d


def images_key(paths):
    """list_images' key for these scans (in this order)."""
    import hashlib
    return hashlib.md5((OCR_VERSION + "|" + "|".join(_scan_digest(p) for p in paths)).encode()).hexdigest()


def scan_key(path):
    """One scan's cache key: its contents and the OCR version, wherever it is in the list."""
    import hashlib
    return hashlib.md5((OCR_VERSION + "|scan|" + _scan_digest(path)).encode()).hexdigest()


def _scan_shape(p):
    """Within one role: single panels first (a back cover, tray card, obi, sticker or
    disc: up to 1.7 times as wide as tall), then the rest, such as booklet spreads.
    Alcest - Les Voyages De L'âme: 28 booklet spreads named img001-img028 came before the
    back with its sticker barcode, and only the first 15 scans are read."""
    try:
        from PIL import Image
        with Image.open(p) as im:
            w, h = im.size
    except Exception:
        return 2
    return 0 if w <= 1.7 * h else 1


def _cached(db, kind, key, compute):
    """OCR is slow (seconds per image); store its result so re-evaluating an
    album after a script fix does not redo it."""
    if db is None:
        return compute()
    db.execute("CREATE TABLE IF NOT EXISTS ocr_cache (key TEXT PRIMARY KEY, value TEXT)")
    row = db.execute("SELECT value FROM ocr_cache WHERE key=?", (f"{kind}:{key}",)).fetchone()
    if row:
        return json.loads(row[0])
    value = compute()
    db.execute("INSERT OR REPLACE INTO ocr_cache VALUES (?,?)", (f"{kind}:{key}", json.dumps(value)))
    db.commit()
    return value


def ocr_workers():
    """How many tesseract runs at once: a few, leaving one processor free."""
    return max(1, min(4, (os.cpu_count() or 2) - 1))


_POOL = threading.local()
_TESSERACT_SLOTS = None
_TESSERACT_GUARD = threading.Lock()


def _tesseract(*args, **kw):
    """subprocess.run of tesseract, no more at a time in the process than ocr_workers() (albums
    looked up at once each read their scans: their pools must not add up)."""
    import subprocess
    global _TESSERACT_SLOTS
    with _TESSERACT_GUARD:
        if _TESSERACT_SLOTS is None:
            _TESSERACT_SLOTS = threading.BoundedSemaphore(max(1, ocr_workers()))
    with _TESSERACT_SLOTS:
        return subprocess.run(*args, **kw)


def _pmap(fn, items):
    """[fn(x) for x in items], up to ocr_workers() at once, results in the same order.
    Called from inside a worker, it runs in turn (no pool inside a pool)."""
    items = list(items)
    n = ocr_workers()
    if n <= 1 or len(items) <= 1 or getattr(_POOL, "inside", False):
        return [fn(x) for x in items]
    from concurrent.futures import ThreadPoolExecutor

    def run(x):
        _POOL.inside = True
        try:
            return fn(x)
        finally:
            _POOL.inside = False
    with ThreadPoolExecutor(max_workers=min(n, len(items))) as pool:
        return list(pool.map(run, items))


def _tess_env():
    """tesseract's environment: one thread each (several run at once, see _pmap), unless
    you set OMP_THREAD_LIMIT yourself."""
    env = dict(os.environ)
    env.setdefault("OMP_THREAD_LIMIT", "1")
    return env


def new_scan_log():
    """What OCR read on each scan of one album, for ocr_order.record: "read" the scans
    read (not from the cache) this time, "text" each scan's readings, "barcodes" the
    barcodes decoded on each, "no_bars" the scans already decoded without a result."""
    return {"read": set(), "text": {}, "barcodes": {}, "no_bars": set()}


def each_scan(db, kind, images, read, log=None, budget_s=None, ahead=None):
    """read(path) for each scan, cached per scan (its contents and the OCR version, so a
    scan read once is not read again wherever it is in the list), several at once.
    Returns the readings in the order of IMAGES; a scan not started within budget_s
    reads as "". The scans read now (not from the cache) go to log["read"].
    ahead: a _ScanReads that may have read some of them already (the same reading,
    and its own budget for the kind); the others are read here."""
    images = [str(p) for p in images]
    keys = [f"{kind}:{scan_key(p)}" for p in images]
    vals = [None] * len(images)
    if db is not None:
        db.execute("CREATE TABLE IF NOT EXISTS ocr_cache (key TEXT PRIMARY KEY, value TEXT)")
        for i, k in enumerate(keys):
            row = db.execute("SELECT value FROM ocr_cache WHERE key=?", (k,)).fetchone()
            if row:
                vals[i] = json.loads(row[0])
    todo = [i for i, v in enumerate(vals) if v is None]
    started = time.time()

    def one(i):
        if budget_s is not None and time.time() - started > budget_s:
            return None
        try:
            return read(images[i]) or ""
        except Exception:
            return ""
    got = ahead.take(kind, [images[i] for i in todo]) if ahead is not None and todo else {}
    rest = [i for i in todo if images[i] not in got]
    started = time.time()                              # (the budget of what is read here)
    done = dict(zip(rest, _pmap(one, rest)))
    for i in todo:
        v = got[images[i]] if images[i] in got else done[i]
        if v is None:
            vals[i] = ""                               # over the time budget: not read
            continue
        vals[i] = v
        if db is not None:
            db.execute("INSERT OR REPLACE INTO ocr_cache VALUES (?,?)", (keys[i], json.dumps(v)))
        if log is not None:
            log["read"].add(images[i])
    if db is not None and todo:
        db.commit()
    if log is not None:
        for p, v in zip(images, vals):
            if v:
                log["text"].setdefault(p, []).append(v)
    return vals


class _ScanReads:
    """The scans' readings (each_scan's text, spines, barcode digits and disc rings) made on
    a background pool while the databases are searched. Each reading is read the way
    each_scan reads it, so taking it from here gives the same readings - unless a time budget
    runs out under load: the barcode digits' and disc rings' budgets (BUDGETS, as each_scan's
    budget_s) start here when the kind's first reading does, not when each_scan asks, so on a
    busy machine a different set of scans may be left unread ("") than in turn. Only
    each_scan writes the cache and the album's scan log, and only for the scans it uses: a
    reading made ahead and not used (the decoded barcode settled the album: page text of
    three scans) is dropped, as never read.

    Its own read-only connection to the cache (so a scan read before is not read again)."""

    BUDGETS = {"barcode-digits": 120.0, "disc-rings-2": 60.0}

    @classmethod
    def start(cls, db, images, want_digits):
        conn = None
        if db is not None:
            try:
                files = [r[2] for r in db.execute("PRAGMA database_list") if r[1] == "main"]
            except Exception:
                return None
            if not files or not files[0]:
                return None                            # an in-memory cache: read in turn
            try:
                conn = sqlite3.connect(Path(files[0]).resolve().as_uri() + "?mode=ro", uri=True,
                                       timeout=60, check_same_thread=False)
            except sqlite3.Error:
                return None
        return cls(conn, [str(p) for p in images], want_digits)

    def __init__(self, conn, images, want_digits):
        from concurrent.futures import ThreadPoolExecutor
        self.conn, self.lock, self.closed = conn, threading.Lock(), False
        self.started = {}
        self.futures = {}
        langs = ocr_langs()
        text = "text" if langs == "eng" else f"text-{langs}"
        spines = "spines" if langs == "eng" else f"spines-{langs}"
        reads = {text: lambda p: ocr_text([p]), spines: lambda p: ocr_spines([p]),
                 "barcode-digits": lambda p: ocr_barcode_digits([p]),
                 "disc-rings-2": lambda p: ocr_disc_rings([p])}
        # what _process reads for sure first (the first three scans' page text, which is all
        # it reads when the decoded barcode settles the album, every disc), the rest after
        order = [(text, p) for p in images[:3]] + [(spines, p) for p in images[:3]]
        order += [("disc-rings-2", p) for p in images[:40]]
        if want_digits:
            order += [("barcode-digits", p) for p in images[:15]]
        order += [(text, p) for p in images[3:]] + [(spines, p) for p in images[3:]]
        self.pool = ThreadPoolExecutor(max_workers=ocr_workers(), thread_name_prefix="scan-reads")
        for kind, p in order:
            self.futures[(kind, p)] = self.pool.submit(self._one, kind, p, reads[kind])

    def _one(self, kind, path, read):
        with self.lock:
            if self.closed:
                return None
            began = self.started.setdefault(kind, time.time())
            if self.conn is not None:
                try:
                    row = self.conn.execute("SELECT value FROM ocr_cache WHERE key=?",
                                            (f"{kind}:{scan_key(path)}",)).fetchone()
                except sqlite3.Error:
                    row = None
                if row:
                    return json.loads(row[0])
        budget = self.BUDGETS.get(kind)
        if budget is not None and time.time() - began > budget:
            return None
        _POOL.inside = True                            # (no pool inside this pool)
        try:
            return read(path) or ""
        except Exception:
            return ""
        finally:
            _POOL.inside = False

    def take(self, kind, paths):
        """{path: reading (None: over the kind's budget)} for the paths read here."""
        out = {}
        for p in paths:
            fut = self.futures.get((kind, str(p)))
            if fut is None or fut.cancelled():
                continue
            try:
                out[str(p)] = fut.result()
            except Exception:                          # (cancelled meanwhile)
                continue
        return out

    def close(self, wait=False):
        """Readings not started are dropped; one under way finishes on its own (never kept).
        Not waited for (wait=False, the album's normal end): those readings, at most one scan
        per pool worker, may still run while the next album's reads start, briefly using more
        OCR workers than ocr_workers(); they write nothing. abort() waits for them."""
        with self.lock:
            if self.closed:
                return
            self.closed = True
            if self.conn is not None:
                self.conn.close()
        self.pool.shutdown(wait=wait, cancel_futures=True)

    def abort(self):
        """The look-up was given up: nothing left running once this returns."""
        self.close(wait=True)
        self.pool.shutdown(wait=True)


class _DiscogsAhead:
    """discogs_search(http, album, ...) on one worker thread, with a clone of the Http
    (Http.thread_clone: the pacing, down and breaker state shared, its own connection to the
    cache). result() waits for it and returns its candidates or raises what it raised."""

    @classmethod
    def start(cls, http, album, cfg, dm):
        # (an Http: a test's stand-in has no thread_clone and is searched in turn)
        clone = http.thread_clone() if callable(getattr(http, "thread_clone", None)) else None
        return cls(clone, album, cfg, dm) if clone is not None else None

    def __init__(self, clone, album, cfg, dm):
        self.clone, self.value, self.error = clone, None, None
        self.thread = threading.Thread(target=self._run, args=(album, cfg, dm), name="discogs-search", daemon=True)
        self.thread.start()

    def _run(self, album, cfg, dm):
        try:
            self.value = discogs_search(self.clone, album, cfg, dm)
        except BaseException as exc:
            self.error = exc

    def result(self):
        self.thread.join()
        if self.error is not None:
            raise self.error
        return self.value

    def abort(self):
        self.clone._stop.set()                         # its next request is not made
        self.thread.join()

    def close(self):
        self.thread.join()
        self.clone.close_clone()


def decoded_barcodes(db, images, log=None):
    """ocr_barcodes(images), cached for exactly these scans ("barcodes2"), with the scan
    each code was decoded on noted in log["barcodes"]."""
    images = [str(p) for p in images]
    if not images:
        return set()

    def compute():
        codes = sorted(ocr_barcodes(images))           # (albums looked up at once share _DECODED_ON)
        where = {c: [p for p in list(_DECODED_ON.get(c, ())) if p in images] for c in codes}
        return {"codes": codes, "from": {c: min(images.index(p) for p in on) for c, on in where.items() if on}}
    value = _cached(db, "barcodes2", images_key(images), compute)
    if isinstance(value, list):                        # a reading stored as the codes alone
        value = {"codes": value, "from": {}}
    found = set(value.get("codes") or ())
    if log is not None:
        for c, i in (value.get("from") or {}).items():
            if isinstance(i, int) and 0 <= i < len(images):
                log["barcodes"].setdefault(str(images[i]), set()).add(c)
        if not found:
            log["no_bars"].update(str(p) for p in images)
    return found


# the scan each barcode ocr_barcodes returned was decoded on (set as it returns)
_DECODED_ON = {}


def barcode_from_symbol(kind, data):
    """The barcode a decoded symbol stands for, or "" when its check digit fails (noise).
    A UPC-E is kept as the UPC-A it is short for: that is how the databases list it."""
    code = re.sub(r"\D", "", str(data or ""))
    if not mc.valid_barcode(code, kind):
        return ""
    # zbar may hand a UPC-E back already expanded (12 or 13 digits): kept as read
    return mc.upce_to_upca(code) if kind == "UPCE" and len(code) == 8 else code


def _decoded_barcodes(decode, image, symbols):
    found = set()
    for d in decode(image, symbols=symbols):
        code = barcode_from_symbol(d.type, d.data.decode("ascii", "ignore"))
        if code:
            found.add(code)
    return found


def _barcode_variants(im):
    """Progressively more effortful versions of one scan. A barcode is often a
    small part of a big page, slightly blurred, or tiny: enlarge it, sharpen
    it, and try each part of the page on its own."""
    from PIL import Image, ImageFilter, ImageOps
    gray = ImageOps.grayscale(im)
    yield gray
    enhanced = ImageOps.autocontrast(gray).filter(ImageFilter.UnsharpMask(radius=2, percent=180))
    yield enhanced
    w, h = gray.size
    longest = max(w, h)
    for scale in (2, 3):
        if longest * scale <= 6500:
            yield enhanced.resize((w * scale, h * scale), Image.LANCZOS)
    # overlapping tiles (3x3 grid, 50% overlap), each enlarged to ~2400px
    tw, th = w // 2, h // 2
    for gx in range(3):
        for gy in range(3):
            x0, y0 = min(gx * tw // 2 * 1, w - tw), min(gy * th // 2 * 1, h - th)
            tile = enhanced.crop((x0, y0, x0 + tw, y0 + th))
            factor = max(1.0, min(3.0, 2400 / max(tile.size)))
            if factor > 1.0:
                tile = tile.resize((int(tile.size[0] * factor), int(tile.size[1] * factor)), Image.LANCZOS)
            yield tile


def ocr_barcodes(paths, per_image_s=8.0, total_s=40.0, patches_s=60.0):
    """Decode product barcodes from scans. Returns validated codes only.

    Stops at the first image that yields one. Time-boxed (the bar patches of all
    scans in `patches_s`, then whole pages in `total_s`), because an image with no
    readable barcode would otherwise cost the full multi-pass search."""
    try:
        from PIL import Image
        from pyzbar.pyzbar import decode, ZBarSymbol
    except ImportError:
        return set()
    symbols = [ZBarSymbol.EAN13, ZBarSymbol.EAN8, ZBarSymbol.UPCA, ZBarSymbol.UPCE]

    def codes_in(img):
        found = set()
        for rotation in (0, 90, 180, 270):
            v = img if rotation == 0 else img.rotate(rotation, expand=True)
            try:
                found |= _decoded_barcodes(decode, v, symbols)
            except Exception:
                continue
            if found:
                break
        return found

    # First where barcodes ARE: the bar patches of every scan, found cheaply and decoded
    # on their own (Enslaved - Axioma Ethica Odini: nine 7019x5100 scans; the whole-page
    # passes used the time budget up before the 8th, the back of the digipak). Upright or
    # sideways, the bars cut out alone first: zbar fails on a printed frame or a busy
    # picture beside them, which page passes cannot avoid.
    with_bars, started = [], time.time()
    for p in paths:
        if time.time() - started > patches_s:
            break
        try:
            im = Image.open(p)
            im.load()
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
        except Exception:
            continue
        patches = barcode_patches(im)
        if patches:
            with_bars.append(p)
        for g, box, _turned in patches:
            if time.time() - started > patches_s:
                break                              # a scan full of bar-like lines: stop within it too
            for crop in _bar_crops(g, box):
                try:
                    hit = _decoded_barcodes(decode, crop, symbols)
                except Exception:
                    continue
                if hit:
                    return _decoded_on(hit, p)
            x0, y0, x1, y1 = box
            bw, bh = x1 - x0, y1 - y0
            crop = g.crop((int(max(0, x0 - bw * 0.3)), int(max(0, y0 - bh * 0.5)),
                           int(min(g.width, x1 + bw * 0.3)), int(min(g.height, y1 + bh * 0.8))))
            for variant in list(_barcode_variants(crop))[:4]:
                hit = codes_in(variant)
                if hit:
                    return _decoded_on(hit, p)
    # then whole pages, those with bar patches first: within the time box, the likely
    # scans are read before a run of booklet pages uses it up (Alcest - Les Chants De
    # L'aurore: the obi was the tenth of ten scans)
    paths = with_bars + [p for p in paths if p not in with_bars]
    started = time.time()
    for p in paths:
        if time.time() - started > total_s:
            break
        try:
            im = Image.open(p)
            im.load()
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
        except Exception:
            continue
        t_img = time.time()
        small = max(im.size) < 1000          # too few pixels for the heavy passes
        hit = set()
        for n, variant in enumerate(_barcode_variants(im)):
            if n >= 2 and small:
                break
            for rotation in (0, 90, 180, 270):
                if time.time() - t_img > per_image_s:
                    break
                v = variant if rotation == 0 else variant.rotate(rotation, expand=True)
                try:
                    hit |= _decoded_barcodes(decode, v, symbols)
                except Exception:
                    continue
                if hit:
                    break
            if hit or time.time() - t_img > per_image_s:
                break
        if hit:
            return _decoded_on(hit, p)
    return set()


def _decoded_on(codes, path):
    for c in codes:
        _DECODED_ON.setdefault(c, set()).add(str(path))
    return codes


_LANGS = None


def ocr_langs():
    """English, plus Russian and Japanese when their tesseract packs are installed
    (tesseract-ocr-rus, tesseract-ocr-jpn): FONO sleeves, Japanese obi strips."""
    global _LANGS
    if _LANGS is None:
        import subprocess
        try:
            have = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True,
                                  timeout=30).stdout.split()
        except Exception:
            have = []
        _LANGS = "+".join(x for x in ("eng", "rus", "jpn") if x in have) or "eng"
    return _LANGS


def ocr_spines(paths):
    """Text printed along the spines of a CD tray card (catalogue numbers often
    appear only there), which page OCR misses: it runs sideways. Each side strip
    of a landscape scan is cut off, turned upright, enlarged and read."""
    import subprocess
    import tempfile
    try:
        from PIL import Image
    except ImportError:
        return ""
    chunks = []
    for p in paths:
        try:
            im = Image.open(p).convert("L")
        except Exception:
            continue
        w, h = im.size
        if w / max(h, 1) >= 1.8 and w >= 1200:
            # a digipak or booklet spread scanned open: its spine runs down the middle
            # (Cryptopsy - Tome II: "HHR 2018-23" only there). Read the whole spread sideways.
            whole = im.copy()
            whole.thumbnail((3600, 3600))
            for angle in (90, 270):
                try:
                    with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
                        whole.rotate(angle, expand=True).save(tmp.name)
                        r = _tesseract(["tesseract", tmp.name, "stdout", "--psm", "11", "-l", ocr_langs()],
                                           capture_output=True, text=True, timeout=120, env=_tess_env())
                    chunks.append(r.stdout)
                except Exception:
                    continue
            continue
        if not (1.15 <= w / max(h, 1) <= 1.6) or w < 600:
            continue                                   # tray cards are about 151 x 118 mm
        strip_w = max(int(w * 0.07), 30)
        for box in ((0, 0, strip_w, h), (w - strip_w, 0, w, h)):
            strip = im.crop(box)
            scale = max(1.0, min(3.0, 3000 / h))
            if scale > 1.0:
                strip = strip.resize((int(strip.size[0] * scale), int(strip.size[1] * scale)))
            for angle in (90, 270):
                try:
                    with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
                        strip.rotate(angle, expand=True).save(tmp.name)
                        r = _tesseract(["tesseract", tmp.name, "stdout", "--psm", "11", "-l", ocr_langs()],
                                           capture_output=True, text=True, timeout=60, env=_tess_env())
                    chunks.append(r.stdout)
                except Exception:
                    continue
    return "\n".join(chunks)


def barcode_regions(im, max_regions=3):
    """Where barcodes are on a scan: patches of dense vertical bars (strong horizontal
    contrast, weak vertical contrast), found on a small copy. Returns boxes in the
    image's own pixels, largest first. Needs numpy; without it, none."""
    try:
        import numpy as np
        from PIL import ImageFilter
    except ImportError:
        return []
    from collections import deque
    g = im.convert("L")
    W, H = g.size
    s = 1200 / max(W, H, 1)
    a = np.asarray(g.resize((max(1, int(W * s)), max(1, int(H * s)))), dtype=np.float32)
    if a.shape[0] < 3 or a.shape[1] < 3:
        return []
    gx = np.abs(np.diff(a, axis=1))[:-1, :]
    gy = np.abs(np.diff(a, axis=0))[:, :-1]
    energy = Image_from(np.uint8(np.clip(np.clip(gx - gy, 0, None) * 2, 0, 255))).filter(ImageFilter.BoxBlur(6))
    m = np.asarray(energy) > 60
    h, w = m.shape
    seen = np.zeros_like(m, bool)
    found = []
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            if not m[y, x] or seen[y, x]:
                continue
            q, ys, xs = deque([(y, x)]), [], []
            seen[y, x] = True
            while q:
                cy, cx = q.popleft()
                ys.append(cy)
                xs.append(cx)
                for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                    if 0 <= ny < h and 0 <= nx < w and m[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        q.append((ny, nx))
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
            bw, bh = x1 - x0, y1 - y0
            if len(ys) > 150 and bw > 20 and bh > 8 and len(ys) > 0.5 * bw * bh:
                found.append((len(ys), (x0 / s, y0 / s, x1 / s, y1 / s)))
    found = _merge_bar_rows(found)
    found.sort(reverse=True)
    return [b for _n, b in found[:max_regions]]


def _merge_bar_rows(found):
    """Patches side by side on one row joined into one: wide black bars have no edges
    inside, so a big barcode is found as two or three pieces (Lamb of God - Omens, Alcest's
    obi). `found` holds (pixels, box) pairs; the joined patch sums the pixels."""
    found = list(found)
    joined = True
    while joined:
        joined = False
        for i in range(len(found)):
            for j in range(i + 1, len(found)):
                (na, a), (nb, b) = found[i], found[j]
                overlap = min(a[3], b[3]) - max(a[1], b[1])
                gap = max(a[0], b[0]) - min(a[2], b[2])
                if overlap >= 0.5 * min(a[3] - a[1], b[3] - b[1]) and gap <= 0.6 * max(a[3] - a[1], b[3] - b[1]):
                    found[i] = (na + nb, (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])))
                    del found[j]
                    joined = True
                    break
            if joined:
                break
    return found


def barcode_patches(im, max_regions=3):
    """The barcodes on a scan, each with its bars standing upright: (grey image, box,
    turned) triples. A barcode printed sideways, along a tray card's edge (BTBAM - The Great
    Misdirect), is found on the scan turned a quarter."""
    from PIL import Image
    g = im.convert("L")
    out = [(g, b, False) for b in barcode_regions(g, max_regions)]
    turned = g.transpose(Image.ROTATE_90)
    out += [(turned, b, True) for b in barcode_regions(turned, max_regions)]
    return out


def _bars_span(g, box):
    """Where the bars of the barcode found at `box` start and end, as (left, right,
    module, inner left, inner right; see _lone_edge_lines): walked out along the middle rows from the patch until a white gap wider
    than any inside it (the quiet zone round a barcode is 7+ bar widths; inside, gaps
    are at most 4). The patch found may be half the code, or run into a printed frame
    (Candlemass's digipak). None when the patch holds no bars."""
    import numpy as np
    x0, y0, x1, y1 = (int(v) for v in box)
    bw, bh = x1 - x0, y1 - y0
    lo, hi = int(max(0, x0 - 1.5 * bw)), int(min(g.width, x1 + 1.5 * bw))
    top, bottom = int(y0 + bh * 0.3), int(y1 - bh * 0.3)
    if hi - lo < 10 or bottom <= top:
        return None
    profile = np.asarray(g.crop((lo, top, hi, bottom)), dtype=np.float32).mean(axis=0)
    inside = profile[x0 - lo:x1 - lo]
    if len(inside) < 10:
        return None
    p10, p90 = np.percentile(inside, [10, 90])
    if p90 - p10 < 30:
        return None
    dark = profile < (p10 + p90) / 2
    gaps, run = [], 0
    for d in dark[x0 - lo:x1 - lo]:
        if not d:
            run += 1
        elif run:
            gaps.append(run)
            run = 0
    marks = np.nonzero(dark)[0]
    if not gaps or not len(marks):
        return None
    quiet = max(4.0, 1.6 * max(gaps))
    start = int(marks[np.argmin(np.abs(marks - ((x0 + x1) // 2 - lo)))])
    left = right = start
    for i in range(start, len(dark)):
        if dark[i]:
            right = i
        elif i - right > quiet:
            break
    for i in range(start, -1, -1):
        if dark[i]:
            left = i
        elif left - i > quiet:
            break
    module = max(1, min(gaps))
    inner_left, inner_right = _lone_edge_lines(dark, left, right, module)
    return lo + left, lo + right + 1, module, lo + inner_left, lo + inner_right + 1


def _lone_edge_lines(dark, left, right, module):
    """The span without a lone line at either end: a frame drawn a few bar widths from
    the bars is walked into as one more bar. Returns (left, right) inside it; the
    same ends when there is none."""
    def next_run(i, step):
        while left <= i <= right and dark[i]:
            i += step
        gap = 0
        while left <= i <= right and not dark[i]:
            i, gap = i + step, gap + 1
        return i, gap
    i, gap = next_run(left, 1)
    inner_left = i if gap >= 3 * module and i < right else left
    i, gap = next_run(right, -1)
    inner_right = i if gap >= 3 * module and i > inner_left else right
    return inner_left, inner_right


def _bar_crops(g, box):
    """Crops of one barcode for the decoder, closest first, each in a white margin: the
    bars alone (a frame, a sticker's edge or a busy picture beside them stops zbar), then
    slightly narrower and wider spans, as the patch found is seldom exact; small ones
    also enlarged."""
    from PIL import Image, ImageOps
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    spans = []
    found = _bars_span(g, box)
    if found:
        left, right, module, inner_left, inner_right = found
        spans.append((left - 2 * module, right + 2 * module))
        if (inner_left, inner_right) != (left, right):         # without a frame's line
            spans += [(inner_left - module, inner_right + module), (inner_left - module, right + 2 * module),
                      (left - 2 * module, inner_right + module)]
    spans += [(x0 - bw * m, x1 + bw * m) for m in (-0.02, 0.03, 0.1, 0.2, 0.35)]
    for a, b in spans:
        a, b = int(max(0, a)), int(min(g.width, b))
        for inset in (0.1, 0.3):
            c = g.crop((a, int(y0 + bh * inset), b, int(y1 - bh * inset)))
            if c.width < 8 or c.height < 4:
                continue
            c = ImageOps.expand(c, border=max(12, int((b - a) * 0.08)), fill=255)
            yield c
            if c.width < 900:
                yield c.resize((c.width * 2, c.height * 2), Image.LANCZOS)


def Image_from(arr):
    from PIL import Image
    return Image.fromarray(arr)


def _digit_band_reads(g, box):
    """tesseract's readings of the digits under one upright barcode: the patch with its
    surroundings enlarged, and tight bands at the foot of the bars, read as a line and
    as digits only."""
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    crop = g.crop((int(max(0, x0 - bw * 0.15)), int(max(0, y0 - bh * 0.4)),
                   int(min(g.width, x1 + bw * 0.15)), int(min(g.height, y1 + bh * 0.6))))
    crops = [(crop.resize((crop.width * 2, crop.height * 2)), 6, "")]
    # the digits alone: a tight band at the foot of the bars, at the scan's own
    # resolution (Exhorder's Dissonance digipak: enlarged, or with the bars in
    # view, tesseract reads noise; the band alone reads "5 013929 020160")
    for f0, f1 in ((-0.1, 0.3), (-0.05, 0.35), (0.0, 0.3)):
        band = (int(max(0, x0 - bw * 0.15)), int(max(0, y1 + bh * f0)),
                int(min(g.width, x1 + bw * 0.1)), int(min(g.height, y1 + bh * f1)))
        if band[3] - band[1] > 8:
            crops.append((g.crop(band), 7, ""))
            crops.append((g.crop(band), 7, "0123456789"))   # no letters for a smudged digit
    out = []
    for c, psm, only in crops:
        try:
            out.append(_tess(c, psm, only=only))
        except Exception:
            continue
    return out


def ocr_barcode_digits(paths, max_images=15, budget_s=120.0):
    """The digits printed under a barcode, read from the barcode's own patch at full
    resolution (page OCR on a reduced scan misses them: Cryptopsy - Tome II's
    8715392182327). For when no barcode decoder (pyzbar) is installed, or it read none.
    A sideways barcode is read turned upright both ways (its digits may run up or down)."""
    try:
        from PIL import Image
    except ImportError:
        return ""
    chunks, started = [], time.time()
    for p in list(paths)[:max_images]:
        if time.time() - started > budget_s:
            break
        try:
            im = Image.open(p)
            im.load()
        except Exception:
            continue
        for g, box, turned in barcode_patches(im):
            frames = [(g, box)]
            if turned:
                x0, y0, x1, y1 = box
                frames.append((g.transpose(Image.ROTATE_180), (g.width - x1, g.height - y1, g.width - x0, g.height - y0)))
            for frame, fbox in frames:
                # checked before each read too: one scan holds up to six patches, 7 reads each
                if time.time() - started > budget_s:
                    break
                chunks += _digit_band_reads(frame, fbox)
    # the first digit sits apart and is often misread: complete 6+6 digit groups by
    # the check digit
    text = "\n".join(chunks)
    for m in re.finditer(r"(?<!\d)(\d{6})\D{0,3}(\d{6})(?!\d)", text):
        code = mc.complete_ean(m.group(1) + m.group(2))
        if code:
            chunks.append(mc.COMPLETED + code)
    return "\n".join(chunks)


def find_disc(g):
    """A disc on a scan as (centre x, centre y, radius) in the image's own pixels, from
    the extent of everything darker than the scanner's lid; None when the scan is not
    a disc (too little of it, or not round)."""
    try:
        import numpy as np
    except ImportError:
        return None
    s = 800 / max(g.size)
    small = np.asarray(g.resize((max(1, int(g.width * s)), max(1, int(g.height * s)))), dtype=np.float32)
    ys, xs = np.nonzero(small < 235)
    if len(xs) < 1000:
        return None
    x0, x1 = np.percentile(xs, [0.5, 99.5])
    y0, y1 = np.percentile(ys, [0.5, 99.5])
    w, h = x1 - x0, y1 - y0
    if min(w, h) < 0.5 * min(small.shape) or not 0.85 <= w / max(h, 1) <= 1.15:
        return None
    return (x0 + x1) / 2 / s, (y0 + y1) / 2 / s, min(w, h) / 2 / s


def unwrap_ring(g, cx, cy, r0, r1):
    """The ring between radii r0 and r1 laid out flat, outer edge up, so text printed
    round the hub reads left to right; the start is repeated at the end, so a line
    crossing the cut is read whole."""
    import numpy as np
    a = np.asarray(g, dtype=np.uint8)
    n, rows = int(2 * np.pi * r1), int(r1 - r0)
    if n < 50 or rows < 8:
        return None
    th = np.linspace(0, 2 * np.pi, n, endpoint=False)
    th = np.concatenate([th, th[: n // 4]])
    T, R = np.meshgrid(th, np.linspace(r1, r0, rows))
    X = np.clip(cx + R * np.cos(T), 0, a.shape[1] - 1).astype(int)
    Y = np.clip(cy + R * np.sin(T), 0, a.shape[0] - 1).astype(int)
    return Image_from(a[Y, X])


def ocr_disc_rings(paths, max_images=8, budget_s=60.0):
    """The matrix / runout codes etched or printed round a disc's hub (Exhorder's
    Dissonance CD: "QDISS0201CDD-1 15 A00"), which page OCR cannot read because the
    text runs in a circle: the ring is unwrapped from the disc's centre and read flat.
    The rim is read too: catalogue numbers and the maker's line are printed along the
    edge (Lamb of God - Omens: "NB 6570-2"; Alcest's "Made in Japan GQCS-91470").
    Time-boxed (`budget_s` for all discs): a rim strip of a big scan is a slow read."""
    try:
        from PIL import Image
        import numpy  # noqa: F401
    except ImportError:
        return ""
    chunks, discs, started = [], 0, time.time()
    for p in list(paths)[:40]:
        if discs >= max_images or time.time() - started > budget_s:
            break
        try:
            g = Image.open(p).convert("L")
        except Exception:
            continue
        if not 0.8 <= g.width / max(g.height, 1) <= 1.25 or min(g.size) < 800:
            continue
        found = find_disc(g)
        if not found:
            continue
        discs += 1
        cx, cy, r = found
        for strip in _hub_strips(g, cx, cy, r) + _rim_strips(g, cx, cy, r):
            if time.time() - started > budget_s:
                break
            try:
                chunks.append(_tess(strip, 11))
            except Exception:
                continue
    return "\n".join(chunks)


def _hub_strips(g, cx, cy, r):
    """The ring round the hub laid flat, twice, and once for text running the other way."""
    from PIL import Image
    out = []
    # (a band a little further out too: a printed hub line on a wide clamping ring sits there,
    # and the inner bands cut through it - Pallas - wearewhoweare: "398478 @1 MTS5503")
    for f0, f1, flip in ((0.28, 0.40, False), (0.30, 0.38, False), (0.28, 0.40, True), (0.33, 0.42, False)):
        if flip:                                   # text running the other way round
            strip = unwrap_ring(g.transpose(Image.FLIP_LEFT_RIGHT), g.width - cx, cy, r * f0, r * f1)
            strip = strip.rotate(180) if strip is not None else None
        else:
            strip = unwrap_ring(g, cx, cy, r * f0, r * f1)
        if strip is not None:
            out.append(strip)
    return out


def _rim_strips(g, cx, cy, r):
    """The band along a disc's edge laid flat, read as it comes and upside down (text on
    the lower half is often printed to read from inside). A big scan is reduced first, a
    small one's band enlarged, so the letters come out about the size tesseract reads best."""
    from PIL import Image
    scale = min(1.0, 1500 / max(r, 1))
    if scale < 1.0:
        g = g.resize((int(g.width * scale), int(g.height * scale)), Image.LANCZOS)
        cx, cy, r = cx * scale, cy * scale, r * scale
    strip = unwrap_ring(g, cx, cy, r * 0.85, r * 1.02)      # find_disc may find it a little small
    if strip is None:
        return []
    if r < 900:
        strip = strip.resize((strip.width * 2, strip.height * 2), Image.LANCZOS)
    return [strip, strip.rotate(180)]


_CODE_LIKE = re.compile(r"(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9-]{5,}|\d[\d ]{9,}\d")


def _tess(img, psm, langs=None, tsv=False, only=""):
    """tesseract's reading of one image; `only` limits it to those characters."""
    import subprocess
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
        img.save(tmp.name)
        cmd = ["tesseract", tmp.name, "stdout", "--psm", str(psm)] + (["-l", langs] if langs else [])
        cmd += ["-c", "tessedit_char_whitelist=" + only] if only else []
        r = _tesseract(cmd + (["tsv"] if tsv else []), capture_output=True, text=True, timeout=180,
                           env=_tess_env())
    return r.stdout


def ocr_located_codes(im):
    """Codes re-read where they are: tesseract finds the lines on a reduced copy (in
    every orientation), and each line holding something code-like is cut from the
    FULL-resolution scan and read alone. Small print that page OCR mangles reads
    cleanly this way (Exhorder's barcode and spine): locating the text helps, not
    enlarging it."""
    chunks = []
    g = im.convert("L")
    for angle in (0, 90, 270, 180):
        full = g if angle == 0 else g.rotate(angle, expand=True)
        small = full.copy()
        small.thumbnail((2400, 2400))
        sc = full.width / max(small.width, 1)
        try:
            rows = _tess(small, 11, tsv=True).splitlines()[1:]
        except Exception:
            continue
        lines = {}
        for row in rows:
            f = row.split("\t")
            if len(f) < 12 or not f[11].strip():
                continue
            key = (f[2], f[3], f[4])
            x, y, w, h = (int(v) for v in f[6:10])
            box = lines.setdefault(key, [x, y, x + w, y + h, ""])
            box[:4] = [min(box[0], x), min(box[1], y), max(box[2], x + w), max(box[3], y + h)]
            box[4] += " " + f[11]
        boxes = []
        for x0, y0, x1, y1, text in lines.values():
            if not _CODE_LIKE.search(text):
                continue
            hgt = (y1 - y0) * sc
            boxes.append((int(max(0, x0 * sc - hgt)), int(max(0, y0 * sc - hgt * 0.5)),
                          int(min(full.width, x1 * sc + hgt)), int(min(full.height, y1 * sc + hgt * 0.5))))
        chunks += [c for c in _pmap(lambda box: _tess_or_empty(full.crop(box), 7), boxes) if c is not None]
    return "\n".join(chunks)


def _tess_or_empty(img, psm):
    try:
        return _tess(img, psm)
    except Exception:
        return None


def ocr_deep(paths, max_images=80, budget_s=900):
    """Investigation: every scan, every zone. Each image at its full resolution in
    overlapping tiles (page OCR reads a reduced copy), the code-like lines re-read
    where they are, in every orientation, plus the barcode bands and disc rings."""
    try:
        from PIL import Image
    except ImportError:
        return ""
    started, chunks = time.time(), []
    for p in list(paths)[:max_images]:
        if time.time() - started > budget_s:
            chunks.append("(investigation stopped: time budget used)")
            break
        try:
            im = Image.open(p)
            im.load()
            g = im.convert("L")
        except Exception:
            continue
        tile = 1800
        nx, ny = max(1, -(-g.width // tile)), max(1, -(-g.height // tile))
        if nx * ny > 1:
            tw, th = -(-g.width // nx), -(-g.height // ny)
            boxes = [(max(0, i * tw - tw // 8), max(0, j * th - th // 8),
                      min(g.width, (i + 1) * tw + tw // 8), min(g.height, (j + 1) * th + th // 8))
                     for i in range(nx) for j in range(ny)]
            # the tiles hunt codes (Latin letters and digits): English only, which reads
            # them as well as eng+rus+jpn in a third of the time; the page text keeps
            # your languages for the words (label names, titles)
            chunks += [c for c in _pmap(lambda box: _tess_or_empty(g.crop(box), 11), boxes) if c]
        try:
            chunks.append(ocr_located_codes(g))
        except Exception:
            pass
    chunks.append(ocr_barcode_digits(paths, max_images=max_images))
    chunks.append(ocr_disc_rings(paths, max_images=max_images))
    return "\n".join(c for c in chunks if c)


def pdf_deep(base, db=None):
    """Investigation of PDF booklets: their pages rendered and read like scans, in full."""
    import hashlib
    import subprocess
    import tempfile
    pdfs = [p for p in album_documents(base) if p.suffix.lower() == ".pdf"]
    if not pdfs or not shutil.which("pdftoppm"):
        return ""
    key = hashlib.md5("|".join(f"{p}:{p.stat().st_size}" for p in pdfs).encode()).hexdigest()

    def read():
        chunks = []
        for p in pdfs:
            with tempfile.TemporaryDirectory() as tmp:
                try:
                    subprocess.run(["pdftoppm", "-r", "300", "-gray", "-png", "-l", "40", str(p),
                                    str(Path(tmp) / "page")], capture_output=True, timeout=600)
                except Exception:
                    continue
                chunks.append(ocr_deep(sorted(str(x) for x in Path(tmp).glob("page*.png"))))
        return "\n".join(chunks)
    return _cached(db, "pdf-deep", key, read)


_WEB_DISCOGS = re.compile(r"discogs\.com/(?:[a-z]{2}/)?(?:[^/\s\"'<>]+/)?release/(\d+)")
_WEB_MB = re.compile(r"musicbrainz\.org/release/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")


def _setting(env, name):
    """A setting from the environment, else from <name> in TagMender's config folder
    ($XDG_CONFIG_HOME/tagmender, normally ~/.config/tagmender)."""
    value = os.environ.get(env, "").strip()
    if not value:
        f = exclusions.CONFIG_DIR / name
        if f.is_file():
            value = f.read_text().strip()
    return value


# The general web search: off unless a profile chooses engines (providers.web.search.engines,
# an ordered chain, carried into the matcher's cfg as "web_search"; TAGMENDER_WEB_SEARCH,
# comma-separated, for match_albums.py run alone). Free services only by default: no AI search
# engine, no scraping of a search page (DuckDuckGo's HTML answered its CAPTCHA page 480 times
# in 480, 2026-10-31). Tavily, a paid AI search, only when chosen by name, and never alone
# (user decision 2026-10-31): a chain with Tavily and no free engine gets Marginalia after it.
# Each engine has a monthly cap, counted in state.db.
WEB_ENGINES = ("mojeek", "marginalia", "tavily")
WEB_FREE_ENGINES = ("mojeek", "marginalia")      # not AI searches
WEB_CAPS = {"mojeek": 1000, "marginalia": 500, "tavily": 1000}
WEB_CODES_PER_ALBUM = 3
MARGINALIA_PUBLIC_KEY = "public"


def web_chain(ws):
    """The engine names a web_search setting lists, in order, as given: "engines" (a list, or
    one name), else the older single "engine" string; "none" and unknown names dropped,
    each name once."""
    if isinstance(ws, str):
        ws = {"engine": ws}
    if not isinstance(ws, dict):
        return []
    names = ws.get("engines")
    if isinstance(names, str):
        names = [n for n in re.split(r"[,\s]+", names) if n]
    if not names:                                            # the older form: one engine
        names = [ws.get("engine")] if ws.get("engine") else []
    out = []
    for n in names if isinstance(names, (list, tuple)) else ():
        n = str(n or "").strip().casefold()
        if n in WEB_ENGINES and n not in out:
            out.append(n)
    return out


def web_cap(engine, cap):
    """An engine's monthly cap from monthly_cap: null (its default), a number (every engine in
    the chain), or {engine: number} (an engine not named keeps its default)."""
    if isinstance(cap, dict):
        cap = cap.get(engine)
    if cap is None or isinstance(cap, bool):
        return WEB_CAPS[engine]
    try:
        return max(0, int(cap))
    except (TypeError, ValueError):
        return WEB_CAPS[engine]


def web_engines(cfg=None):
    """[(engine, monthly cap), ...]: the chain the matcher's settings choose, tried in order;
    [] for no web search (the default). Marginalia is added after Tavily when no free engine
    is in the chain, so Tavily is never the only one."""
    ws = (cfg or {}).get("web_search")
    if not isinstance(ws, (str, dict)):
        ws = {"engines": os.environ.get("TAGMENDER_WEB_SEARCH", "")}
    chain = web_chain(ws)
    cap = ws.get("monthly_cap") if isinstance(ws, dict) else None
    out = [(e, web_cap(e, cap)) for e in chain]
    if "tavily" in chain and not any(e in WEB_FREE_ENGINES for e in chain):
        # a plain number caps the engines the profile lists; the added one keeps its own
        out.append(("marginalia", web_cap("marginalia", cap) if isinstance(cap, dict)
                    else WEB_CAPS["marginalia"]))
    return out


def web_engine(cfg=None):
    """(engine names joined by ", ", the first one's monthly cap) for the chain the matcher's
    settings choose; ("none", 0) for no web search."""
    chain = web_engines(cfg)
    if not chain:
        return "none", 0
    return ", ".join(e for e, _c in chain), chain[0][1]


def web_engine_setting(engine):
    """(value, problem): the engine's address or key (environment, else its 0600 file in the
    config folder), or "" and why it cannot be used, as one sentence (Setup's notice). Never
    logged."""
    import credentials
    try:
        value = credentials.get(engine)
    except credentials.CredentialError:
        return "", requirements.notice(engine, state=requirements.UNSAFE)
    if engine == "marginalia":
        return value or MARGINALIA_PUBLIC_KEY, ""
    if engine == "tavily":
        m = re.search(r"tavilyApiKey=([\w-]+)", value)          # the MCP URL pasted whole
        value = m.group(1) if m else value
    if not value:
        return "", requirements.notice(engine, state=requirements.NOT_SET)
    return value, ""


def _month(now=None):
    return time.strftime("%Y-%m", time.localtime(now))


def _usage_db():
    exclusions.STATE_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(exclusions.STATE_DIR / "state.db", timeout=60)
    db.execute("CREATE TABLE IF NOT EXISTS web_search_usage (engine TEXT, month TEXT, count INT, "
               "PRIMARY KEY (engine, month))")
    return db


def web_search_count(engine, month=None):
    """Searches sent to the engine this month (or `month`, "YYYY-MM"), over every run."""
    try:
        db = _usage_db()
        try:
            row = db.execute("SELECT count FROM web_search_usage WHERE engine=? AND month=?",
                             (engine, month or _month())).fetchone()
        finally:
            db.close()
    except sqlite3.Error:
        return 0
    return int(row[0]) if row else 0


def _count_web_search(engine):
    try:
        db = _usage_db()
        try:
            db.execute("INSERT INTO web_search_usage VALUES (?, ?, 1) ON CONFLICT(engine, month) "
                       "DO UPDATE SET count=count+1", (engine, _month()))
            db.commit()
        finally:
            db.close()
    except sqlite3.Error:
        pass


def _note_web_answer(engine, linked, now=None):
    """Remember when the engine last answered a search (from the cache too) and whether its
    answer linked a release: doctor says which engine of the chain answered last."""
    try:
        db = _usage_db()
        try:
            db.execute("CREATE TABLE IF NOT EXISTS web_search_answers (engine TEXT PRIMARY KEY, ts REAL, linked INT)")
            db.execute("INSERT OR REPLACE INTO web_search_answers VALUES (?, ?, ?)",
                       (engine, time.time() if now is None else now, int(bool(linked))))
            db.commit()
        finally:
            db.close()
    except sqlite3.Error:
        pass


def web_search_answers():
    """{engine: (time of its last answer, did it link a release)}, over every run."""
    try:
        db = _usage_db()
        try:
            db.execute("CREATE TABLE IF NOT EXISTS web_search_answers (engine TEXT PRIMARY KEY, ts REAL, linked INT)")
            rows = db.execute("SELECT engine, ts, linked FROM web_search_answers").fetchall()
        finally:
            db.close()
    except sqlite3.Error:
        return {}
    return {e: (float(ts or 0), bool(linked)) for e, ts, linked in rows}


def _web_cached(http, key):
    """Will Http answer this search from its cache? Not when the answer is older than
    --recheck-days (Http.max_age): get_text then asks the engine again, and that counts.
    (post_json, Tavily's, keeps an answer whatever its age.)"""
    db = getattr(http, "db", None)
    if db is None:
        return False
    try:
        row = db.execute("SELECT ts FROM http_cache WHERE url=?", (key,)).fetchone()
    except sqlite3.Error:
        return False
    if row is None:
        return False
    max_age = getattr(http, "max_age", None)
    stale = max_age and time.time() - float(row[0] or 0) > max_age
    return not (stale and not key.startswith("POST "))


def _reached_engine(exc):
    """Did a failed search reach the engine (it answered with an HTTP error or a broken
    answer), so that it counts toward the cap? Not when the host was never reached
    (refused, timed out) or Http skipped it (paused after repeated failures)."""
    if isinstance(exc, urllib.error.HTTPError):
        return True
    if isinstance(exc, (urllib.error.URLError, TimeoutError)):
        return False
    text = str(exc)
    return not any(w in text for w in ("skipped:", "URLError", "TimeoutError", "timed out"))


def _web_note(http, notes, text):
    """Said once a run (the first album it happens on), and on stderr."""
    said = getattr(http, "web_notes_said", None)
    if said is None:
        said = set()
        try:
            http.web_notes_said = said
        except AttributeError:
            pass
    if text in said:
        return
    said.add(text)
    _tell(f"    {text}")
    if notes is not None:
        notes.append(text)


def _result_urls(body):
    """The result URLs in an engine's JSON answer (Marginalia: results[].url;
    Mojeek: response.results[].url), one per line."""
    try:
        data = json.loads(body) if isinstance(body, str) else body
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    results = data.get("results")
    if results is None and isinstance(data.get("response"), dict):
        results = data["response"].get("results")
    return "\n".join(str(r.get("url") or "") for r in results or () if isinstance(r, dict))


TAVILY_URL = "https://api.tavily.com/search"


def _web_request(engine, setting, query):
    """(url, cache key, headers) of one search; the cache key never holds the API key."""
    if engine == "mojeek":
        tail = f"fmt=json&q={q(query)}"
        return f"https://www.mojeek.com/search?api_key={q(setting)}&{tail}", f"https://www.mojeek.com/search?{tail}", None
    url = f"https://api2.marginalia-search.com/search?query={q(query)}&count=20"
    return url, url, {"API-Key": setting}


def _web_links(text):
    """Does an engine's answer link a Discogs or MusicBrainz release?"""
    text = urllib.parse.unquote(text or "")
    return bool(_WEB_DISCOGS.search(text) or _WEB_MB.search(text))


def web_search(http, query, cfg=None, notes=None):
    """Web results for a query, as text holding their URLs, from the engines the profile
    chose, in order (none by default: ("", "")). An engine that is not set up, is paused
    after failures, fails, is at its monthly cap or answers without a release link hands
    over to the next. A cached answer is free; a search that reaches an engine counts toward
    that engine's cap (one refused or skipped on the way does not). Returns (text, engine):
    the answer and engine that linked a release, else every answer's URLs and the last
    engine asked ("" when none was)."""
    texts, last = [], ""
    for engine, cap in web_engines(cfg if cfg is not None else getattr(http, "cfg", None)):
        text, asked = _web_search_one(http, engine, cap, query, notes)
        last = engine if asked else last
        if _web_links(text):
            return text, engine
        if text:
            texts.append(text)
    return "\n".join(texts), last


def _web_search_one(http, engine, cap, query, notes=None):
    """(text, asked) from one engine: asked is False when it was not set up or at its cap
    (nothing was tried), True when it was asked, answered from the cache or skipped as
    paused. The cap note is said once a run."""
    setting, problem = web_engine_setting(engine)
    if problem:
        _web_note(http, notes, problem)
        return "", False
    if engine == "tavily":
        body = {"query": query, "max_results": 10}
        key = post_cache_key(TAVILY_URL, body)
    else:
        url, key, headers = _web_request(engine, setting, query)
    fresh = not _web_cached(http, key)
    if fresh:
        down = getattr(http, "host_down", None)
        if down and down(engine):
            return "", True              # paused after repeated failures: nothing is sent or counted
        if web_search_count(engine) >= cap:
            _web_note(http, notes, f"web search ({engine}) is off for the rest of {_month()}: its monthly cap "
                                   f"of {cap} searches is reached (providers.web.search.monthly_cap)")
            return "", False
    try:
        if engine == "tavily":
            if not hasattr(http, "post_json"):
                return "", True
            data = http.post_json(TAVILY_URL, body, "tavily",
                                  headers={"Authorization": f"Bearer {setting}"}) or {}
            text = _result_urls(data)
        elif headers:
            text = _result_urls(http.get_text(url, engine, key=key, headers=headers) or "")
        elif key != url:
            text = _result_urls(http.get_text(url, engine, key=key) or "")
        else:
            text = _result_urls(http.get_text(url, engine) or "")
    except Exception as exc:             # an optional host: noted by Http (doctor), never a failure
        if fresh and _reached_engine(exc):
            _count_web_search(engine)
        return "", True
    if fresh:
        _count_web_search(engine)        # the engine answered (a cached answer is free)
    _note_web_answer(engine, _web_links(text))
    return text, True


def web_release_links(http, codes, artist, limit=WEB_CODES_PER_ALBUM, cfg=None, notes=None):
    """Releases the web ties to codes read from the scans: one search for each code (strongest
    first, at most `limit`), stopping at the first that turns up a Discogs or MusicBrainz
    release page. For a pressing the databases' own search does not turn up. Nothing without
    a web search engine set up."""
    dg, mb = [], []
    if not web_engines(cfg if cfg is not None else getattr(http, "cfg", None)):
        return dg, mb
    for code in list(codes)[:limit]:
        text, _engine = web_search(http, f'"{code}" {artist}'.strip(), cfg, notes)
        text = urllib.parse.unquote(text)
        for rid in _WEB_DISCOGS.findall(text):
            if rid not in dg:
                dg.append(rid)
        for rid in _WEB_MB.findall(text):
            if rid not in mb:
                mb.append(rid)
        if dg or mb:
            break
    return dg[:8], mb[:8]


def code_kind(code):
    """"barcode" for a number of a barcode's length (8, 12, 13 or 14 digits, spaces aside),
    else "catno". A guess, for a code whose origin is not known."""
    k = re.sub(r"[\s]", "", str(code or ""))
    return "barcode" if k.isdigit() and len(k) in (8, 12, 13, 14) else "catno"


def junk_code(code, kind=None):
    """A code not worth a search: under 5 characters, one digit over and over
    ("0000000000000" passes the check digit), a barcode failing its check digit, or
    letters without digits (an OCR fragment). A catalogue number of digits only
    ("9362-47305-2", Warner's) is no barcode: the check digit is not asked of it.
    `kind` is "barcode" or "catno"; guessed from the code (code_kind) when not given."""
    k = re.sub(r"[\s\-./]", "", str(code or ""))
    if len(k) < 5:
        return True
    kind = kind or code_kind(code)
    if k.isdigit():
        return len(set(k)) == 1 or (kind == "barcode" and not mc.valid_barcode(k))
    return kind == "barcode" or not _distinctive(k)


def _digits_printed_as_catno(code, text):
    """A catalogue number of digits only is taken only as printed with its separators
    ("9362-47305-2", "4509 91234 2"): at least 8 digits in two or more groups, read whole.
    A bare run of digits is as likely a phone number or a part of something else."""
    groups = re.findall(r"\d+", str(code or ""))
    return len(groups) >= 2 and sum(map(len, groups)) >= 8 and _read_whole(code, text)


def _read_whole(code, text):
    """Is the catalogue number printed as one in the text (any case and separators, O/0 and
    I/1 aside), not only assembled from letters taken as digits?"""
    chars = re.sub(r"[^A-Za-z0-9]", "", str(code or ""))
    if not chars:
        return False
    cls = {"O": "[O0o]", "0": "[O0o]", "I": "[I1l]", "1": "[I1l]"}
    body = r"[\s\-.]?".join(cls.get(ch.upper(), re.escape(ch)) for ch in chars)
    return re.search(r"(?<![A-Za-z0-9])" + body + r"(?![A-Za-z0-9])", str(text or ""), re.I) is not None


def strong_codes(text, codes, decoded=(), limit=WEB_CODES_PER_ALBUM):
    """The codes worth searching beyond the databases, strongest first: barcodes decoded from
    the bars, then barcodes printed and read whole (not completed by the check digit), then
    catalogue numbers read whole (as printed, O/0 and I/1 aside; not a reading assembled
    from letters taken as digits). Junk codes never; at most `limit`."""
    out = []
    whole = set(mc.barcodes_read_whole(text, codes.get("barcodes") or ()))
    for b in sorted(decoded or ()) + [b for b in codes.get("barcodes") or () if b in whole]:
        if not junk_code(b, "barcode") and mc.barcode_key(b) not in {mc.barcode_key(x) for x in out}:
            out.append(b)
    for c in codes.get("catnos") or ():
        if junk_code(c, "catno") or any(mc.norm_code(c) == mc.norm_code(x) for x in out):
            continue
        digits_only = not re.search(r"[A-Za-z]", str(c))
        if (_digits_printed_as_catno(c, text) if digits_only else _read_whole(c, text)):
            out.append(c)
    return out[:limit]


def code_kinds(codes, decoded=()):
    """{code: "barcode" | "catno"} for the codes codes_from_ocr_text found and the barcodes
    decoded from the bars: what each was read as, for variant_release_links."""
    kinds = {str(c): "catno" for c in codes.get("catnos") or ()}
    kinds.update({str(b): "barcode" for b in list(codes.get("barcodes") or ()) + list(decoded or ())})
    return kinds


def _numbers_as_digits(code):
    """O read for 0 and I for 1 beside a digit: "QDISSO201CDD" -> "QDISS0201CDD"."""
    s = str(code)
    while True:
        t = re.sub(r"(?<=\d)[Oo]|[Oo](?=\d)", "0", s)
        t = re.sub(r"(?<=\d)I|I(?=\d)", "1", t)
        if t == s:
            return s
        s = t


def _words_as_letters(code):
    """0 read for O and 1 for I between letters: "QD1SS0201" -> "QDISS0201"."""
    s = str(code)
    s = re.sub(r"(?<=[A-Za-z])0(?=[A-Za-z])|^0(?=[A-Za-z]{2})|(?<=[A-Za-z]{2})0$", "O", s)
    return re.sub(r"(?<=[A-Za-z])1(?=[A-Za-z])|^1(?=[A-Za-z]{2})|(?<=[A-Za-z]{2})1$", "I", s)


def code_variants(code, limit=5, kind="catno"):
    """Other spellings of a catalogue number to search when it found nothing as read:
    separators removed, or a space, hyphen or dot between its letter and digit groups,
    and (only for a code of letters and digits) O/0 and I/1 told apart by what stands
    beside them. Never the code itself; at most `limit`."""
    s = str(code or "").strip()
    bases = [s]
    if re.search(r"[A-Za-z]", s) and re.search(r"\d", s):
        # the readings with O/0 and I/1 changed first (when they differ), then the code as read
        bases = [b for b in (_numbers_as_digits(s), _words_as_letters(s)) if b != s] + [s]
    forms, dotted = [], []
    for b in bases:
        compact = re.sub(r"[\s\-.]", "", b)
        groups = re.findall(r"[A-Za-z]+|\d+", compact)
        forms += [b, compact] + ([" ".join(groups), "-".join(groups)] if len(groups) > 1 else [])
        dotted += [".".join(groups)] if len(groups) > 1 else []
    out = []
    for f in forms + dotted:
        same_kind = bool(re.search(r"[A-Za-z]", f)) == bool(re.search(r"[A-Za-z]", s))
        if f and f != s and f not in out and same_kind and not junk_code(f, kind):
            out.append(f)
    return out[:limit]


def barcode_spellings(code):
    """A barcode as sleeves and Discogs print it: EAN-13 "5 013929 020160", UPC-A
    "0 16861 84112 7"."""
    k = re.sub(r"\D", "", str(code or ""))
    out = []
    if len(k) == 13:
        out.append(f"{k[0]} {k[1:7]} {k[7:]}")
    if len(k.lstrip("0")) <= 12:
        u = k.lstrip("0").zfill(12)
        out.append(f"{u[0]} {u[1:6]} {u[6:11]} {u[11]}")
    return out


def _carries(cand, code, kind=None):
    """Does a candidate list this barcode or catalogue number? (`kind` as junk_code's)"""
    if (kind or code_kind(code)) == "barcode":
        return mc.barcode_key(code) in {mc.barcode_key(b) for b in cand.get("barcodes") or ()}
    return mc.catnos_agree([code, _numbers_as_digits(code)], cand.get("catnos") or ())


def variant_release_links(http, codes, known, use_mb=True, use_discogs=True, per_search=3,
                          kinds=None, notes=None):
    """Releases found for a code under another spelling: for each code no candidate in
    `known` lists, its variants (code_variants; a barcode spaced as printed) asked of
    MusicBrainz (catno:) and Discogs (catno= / barcode=) until one finds something, then
    Discogs' free-text search for the code (it reads notes and identifiers too). What the
    free search used to cover. `kinds` says which codes are barcodes and which catalogue
    numbers ({code: "barcode" | "catno"}; guessed by code_kind when missing). Answers are
    cached by Http. A database that fails is asked no more in this call (a note in
    `notes`); only a rejected token is raised. Returns (discogs ids, mb ids)."""
    dg, mb = [], []
    failed = set()

    def ask(host, url):
        if host in failed:
            return None
        try:
            return http.get_json(url, host)
        except AuthError:
            raise
        except Exception as exc:
            failed.add(host)
            name = "MusicBrainz" if host == "mb" else "Discogs"
            if notes is not None:
                notes.append(f"{name} search of other spellings of the codes failed ({exc})")
            return None

    def add(into, ids):
        for rid in ids[:per_search]:
            if str(rid) not in into:
                into.append(str(rid))
        return bool(ids)

    def mb_ids(query):
        data = ask("mb", f"https://musicbrainz.org/ws/2/release/?query={q(query)}&fmt=json&limit=15")
        return [r["id"] for r in (data or {}).get("releases", []) if r.get("id")]

    def dg_ids(search):
        data = ask("discogs", f"https://api.discogs.com/database/search?type=release&per_page=15&{search}")
        return [r["id"] for r in (data or {}).get("results", []) if r.get("id")]

    for code in codes:
        kind = (kinds or {}).get(str(code)) or code_kind(code)
        if any(_carries(c, code, kind) for c in known):
            continue
        barcode = kind == "barcode"
        hit = False
        for form in (barcode_spellings(code) if barcode else code_variants(code, kind=kind)):
            if use_mb and not barcode:
                hit = add(mb, mb_ids(f'catno:"{form}"')) or hit
            if use_discogs:
                hit = add(dg, dg_ids(f"{'barcode' if barcode else 'catno'}={q(form)}")) or hit
            if hit:
                break
        if not hit and use_discogs:
            add(dg, dg_ids(f"q={q(code)}"))
    return dg[:8], mb[:8]


def ocr_text(paths):
    """OCR via the tesseract program itself (no pytesseract needed)."""
    import subprocess
    import tempfile
    try:
        from PIL import Image
    except ImportError:
        return ""
    chunks = []
    for p in paths:
        try:
            im = Image.open(p).convert("L")
            im.thumbnail((2400, 2400))
            with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
                im.save(tmp.name)
                r = _tesseract(["tesseract", tmp.name, "stdout", "--psm", "11", "-l", ocr_langs()],
                                   capture_output=True, text=True, timeout=120, env=_tess_env())
            chunks.append(r.stdout)
        except Exception:
            continue
    return "\n".join(chunks)


OCR_MODES = ("auto", "on", "off")


def ocr_mode(value):
    """--ocr or a profile's "ocr" as one of OCR_MODES: True (the --ocr flag) is on,
    False/None/"" off; anything unknown is off."""
    if value is True:
        return "on"
    v = str(value or "off").strip().casefold()
    return v if v in OCR_MODES else "off"


def has_scans(album_dir):
    """Does the folder hold scans to read (images other than a plain cover.jpg or a
    picture saved from a shop) or a PDF booklet? What `ocr: auto` asks."""
    for dirpath, dirs, names in os.walk(album_dir):
        dirs[:] = [d for d in dirs if not _NOT_A_SCAN_DIR.match(d)]
        for n in names:
            p = Path(dirpath) / n
            ext = p.suffix.lower()
            if ext == ".pdf":
                return True
            if ext in IMAGE_EXT and not _NOT_A_SCAN.match(p.stem) and not web_image(p):
                return True
    return False


def ocr_wanted(value, album_dir):
    """Is OCR run for this album? on: always; off: never; auto: when it has scans or a PDF."""
    mode = ocr_mode(value)
    return mode == "on" or (mode == "auto" and has_scans(album_dir))


def embedded_cover(album, max_tracks=4):
    """The front cover embedded in the album's first tracks, as (bytes, key) or (None, "")."""
    import hashlib
    try:
        import artwork
    except Exception:
        return None, ""
    for t in list(album.get("tracks") or ())[:max_tracks]:
        try:
            data = artwork.embedded_front(t.get("path") or "")
        except Exception:
            data = None
        if data:
            return data, hashlib.md5((OCR_VERSION + "|cover|").encode() + data).hexdigest()
    return None, ""


# A long printed number: on a small embedded cover the digits under a barcode are never
# trusted as read (only a barcode zbar decodes whole is), so OCR text loses them.
_LONG_NUMBER = re.compile(r"(?<![\w-])\d(?:[ \t°'’·.\-]{0,2}\d){10,}")


def ocr_cover(data):
    """Text and barcodes on an embedded front cover: {"text", "barcodes"}. The text keeps
    words and codes (catalogue number, label), not long numbers; a barcode counts only when
    decoded whole with a right check digit (a full UPC-A or EAN-13, as on scans)."""
    import tempfile
    try:
        with tempfile.NamedTemporaryFile(suffix=".img") as tmp:
            tmp.write(data)
            tmp.flush()
            text = ocr_text([tmp.name])
            codes = ocr_barcodes([tmp.name])
    except Exception:
        return {"text": "", "barcodes": []}
    return {"text": _LONG_NUMBER.sub(" ", text or ""),
            "barcodes": sorted(c for c in codes or () if len(c) >= 12 and mc.valid_barcode(c))}


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------
def release_fields(c):
    """What the rules check of an identified release (a candidate or its summary): its album,
    artist and, when it names any, genres (no genre is no value, not an empty one)."""
    genre = c.get("genre") or []
    genre = [genre] if isinstance(genre, str) else [g for g in genre if g]
    return {"album": [c.get("title") or ""], "albumartist": [c.get("artist") or ""],
            "artist": [c.get("artist") or ""], **({"genre": genre} if genre else {})}


def summarize(res):
    b = res["best"]
    if not b:
        return None
    c = b["cand"]
    return {"source": c["source"], "id": c["id"], "url": c["url"],
            "artist": c["artist"], "title": c["title"], "year": c["year"],
            "country": c["country"], "label": c["label"],
            "tracks": len(c["tracks"]), "confidence": b["confidence"],
            "artist_sim": b.get("artist_sim", 0), "title_sim": b.get("title_sim", 0),
            # (what lets a sure album be filed below the organize threshold: plan._files_on_evidence)
            "all_lengths": bool(b.get("tracks_ok") and b.get("dur_frac") == 1.0 and (b.get("dur_cov") or 0) >= 1.0
                                and not b.get("conflict") and not b.get("missing")),
            "lengths_missing": bool(b.get("lengths_free") and not b.get("conflict") and not b.get("missing")),
            "notes": b["notes"], "discogs_id": c.get("discogs_id", ""), "genre": list(c.get("genre") or []),
            # tracks missing from the folder: each file's track of the release (0-based, in
            # the release's order), the missing ones {disc: [numbers]}, the release's count
            **({"slots": list(c["slots"]), "missing": {str(d): v for d, v in b["missing"].items()},
                "release_tracks": c.get("full_tracks")} if b.get("missing") else {}),
            # versions of one disc in folders of their own: each its own medium of the release
            # (match_core.version_view), tagged as that medium's tracks - nothing missing
            **({"version_tracks": [list(x) for x in c["version_tracks"]],
                "version_media": {str(d): m for d, m in c["version_media"].items()}}
               if c.get("version_media") and not b.get("missing") else {}),
            "mb_id": res.get("mb_twin") or (c["id"] if c["source"] == "mb" else ""),
            # (Sources "MusicBrainz preferred": Discogs's sure pick of the same pressing)
            **({"discogs_id": res["dg_twin"]} if res.get("dg_twin") and not c.get("discogs_id") else {})}


def run_source(name, finder, album, cfg, ocr_txt=""):
    cands = finder()
    scored = [mc.score_candidate(album, c, cfg, ocr_txt) for c in cands]
    res = mc.classify(scored, cfg)
    res["scored"] = scored
    res["source"] = name
    return res


def _combine(mb_res, dg_res, countries=None, prefs=None):
    """match_core.combine, but always a result: when neither provider gave one (MusicBrainz
    down and Discogs failing too) it is a "no match" whose reasons the provider errors
    in the album's notes complete (the album is then retried, not filed). prefs
    (match_core.release_preferences): Picard's preferred release types and formats."""
    return mc.combine(mb_res, dg_res, countries, prefs) or {
        "status": "nomatch", "best": None, "runner_up": None, "margin": None,
        "why": ["no provider gave a result"]}


def _distinctive(code):
    """A catalogue number OCR cannot make up from a year or a phone number: 5+ characters,
    letters and digits ("omm0132")."""
    k = mc.norm_code(code)
    return len(k) >= 5 and bool(re.search(r"[a-z]", k)) and bool(re.search(r"\d", k))


def scans_barcode_why(final, album, ocr_txt, results):
    """A barcode your scans show (decoded, or printed and read whole) that no candidate
    carries, while the pick carries none: the scans are of another pressing, perhaps one
    the databases do not list, so a person decides (R3). "" when there is none."""
    cand = ((final or {}).get("best") or {}).get("cand") or {}
    if cand.get("barcodes"):
        return ""                                  # its own barcode differs: score_candidate's conflict
    printed = mc.codes_from_ocr_text(ocr_txt or "", limit=10)["barcodes"]
    # the ripper's notes ("Barcode: X") are no scan: their number is not called one
    typed = {mc.barcode_key(b) for b in album.get("note_barcodes") or ()}
    printed = [b for b in printed if mc.barcode_key(b) not in typed]
    seen = sorted(album.get("ocr_barcodes") or ()) + mc.barcodes_read_whole(ocr_txt or "", printed)
    listed = {mc.barcode_key(b) for res in results for s in (res or {}).get("scored") or ()
              for b in s["cand"].get("barcodes") or ()}
    unlisted = {mc.barcode_key(x) for x in album.get("unlisted_barcodes") or ()}
    ignored = ""
    for b in seen:
        if mc.valid_barcode(re.sub(r"\D", "", str(b))) and mc.barcode_key(b) not in listed:
            # BAR-4: one searched and found on no release anywhere is ignored (a sticker); any
            # other barcode of the scans still says another pressing
            if mc.barcode_key(b) in unlisted:
                ignored = ignored or f"scans show barcode {b}, on no release: ignored (BAR-4)"
                continue
            return f"scans show barcode {b}, not on this release"
    return ignored


def notes_barcode_why(final, album):
    """An unofficial pick (R9) whose only barcode agreeing is the one the ripper's notes typed
    ("Barcode: X", perhaps off that bootleg's own page): the notes weigh as the folder name,
    so nothing of your copy says it is yours. "" otherwise."""
    best = (final or {}).get("best") or {}
    cand = best.get("cand") or {}
    if not cand.get("unofficial") or not str(best.get("code") or "").startswith("barcode"):
        return ""
    keys = lambda bs: {mc.barcode_key(b) for b in bs or ()}  # noqa: E731
    mine = keys(album.get("barcodes")) | keys(album.get("ocr_barcodes"))
    typed = keys(album.get("note_barcodes")) - mine
    if keys(cand.get("barcodes")) & mine or not keys(cand.get("barcodes")) & typed:
        return ""
    return ("the release is an unofficial one (a bootleg); only the barcode the ripper's notes "
            "typed says it is yours")


def _total_miscounted(final, album, here, total=0, results=()):
    """The pick lists exactly the tracks here, every length agreeing, and the tags' track
    total is that of the pick's own pressing as written: the writer counted a box's DVD
    chapters or an index track's sub-tracks (Dark Tranquillity 71 for 2 CDs of 21; Intronaut
    12 for 8; Tourniquet 12 for 10), no file is missing. "Its own pressing" is either
    - the release the files' tags name (their IDs, "total_releases", never read in a blind
      run: they are the validation's truth there, and never decide the pick, only whether
      their own total counts), or
    - the other database's entry of the pick's pressing (MusicBrainz's link, the barcode, or
      the catalogue number and country agree) listing exactly that many rows, of which the
      audio tracks are the ones here (_audio_rows): Discogs counts 1998130's DVD chapters
      (71), 1467713's and 15953028's index sub-tracks (12). Rows with lengths beyond the
      pick's are songs the pick's entry may lack: the warning stands."""
    final = final or {}
    best = final.get("best") or {}
    cand = best.get("cand") or {}
    if len(cand.get("tracks") or ()) != here or (best.get("dur_frac") or 0) < 1.0:
        return False
    mb_ids, dg_ids = album.get("total_releases") or (album.get("tag_release_mb"), album.get("tag_release_discogs"))
    mb_ids, dg_ids = set(mb_ids or ()), set(dg_ids or ())
    mine = mb_ids if cand.get("source") == "mb" else dg_ids
    if (len(mine) == 1 and str(cand.get("id")) in mine) or (final.get("mb_twin") and mb_ids == {final["mb_twin"]}):
        return True
    return bool(total) and any(
        c.get("source") != cand.get("source") and len(c.get("tracks") or ()) == total
        and _audio_rows(c) == here and not mc._stated_apart(c, cand)
        and (mc.same_pressing(c, cand) or mc.same_pressing(cand, c))
        for res in results or () for c in (s["cand"] for s in (res or {}).get("scored") or ()))


def _audio_rows(cand):
    """The audio tracks an entry's rows stand for: a video disc's chapters count none, an
    index track's sub-tracks without lengths count as the one track they are on the disc
    (Intronaut - Prehistoricisms: "The Reptilian Brain", 8-I to 8-V). Rows with lengths
    beyond a pick's tracks are songs the folder may lack, so they count each."""
    rows = [t for t in cand.get("tracks") or () if not t.get("video")]
    works = {t["index"] for t in rows if t.get("index") is not None and not t.get("secs")}
    return sum(1 for t in rows if t.get("index") is None or t.get("secs")) + len(works)


def _copy_disagrees(final, album, ocr_txt, results):
    """Reasons an auto pick does not fit your copy, found after scoring: a barcode on your
    scans that no candidate carries (R3), an unofficial pick resting on the notes' barcode
    alone (R3/R9), or fewer tracks here than the tags' own track total (the rest of the
    album is elsewhere, R11)."""
    why = []
    sticker = scans_barcode_why(final, album, ocr_txt, results)
    if sticker.endswith("(BAR-4)") and (final or {}).get("best"):
        # BAR-4: a barcode on your scans that no release anywhere carries (searched on both
        # databases and in the shops) is ignored: told, never a reason to wait
        final["best"]["notes"] = list(final["best"].get("notes") or []) + [sticker]
    elif sticker:
        why.append(sticker)              # (not searched, or a search failed: another pressing, perhaps)
    note = notes_barcode_why(final, album)
    if note:
        why.append(note)
    total, here = album.get("tracktotal") or 0, len(album.get("tracks") or ())
    best = (final or {}).get("best") or {}
    if best.get("missing") and total == len(best["cand"].get("slots") or ()) + sum(
            len(v) for v in best["missing"].values()):
        return why                                     # the missing tracks are known and told
    if total > here and not _total_miscounted(final, album, here, total, results):
        why.append(f"the files' tags say the album has {total} tracks; this folder holds {here}")
    return why


def log_title_step(http, album, cfg, dm, mb_res, dg_res, final, text, use_mb, use_discogs, out, source_notes,
                   provider_errors=None):
    """The album a rip log's header names, searched when it differs from the folder's and
    the result is not sure (Jesu - Sun Down / Sun Rise: the log reads "Jesu / Conqueror
    (Japanese Bonus Disc)", and only Conqueror's disc 2 is this folder, _box_disc). Its name is
    a title to search, never evidence. Returns (mb_res, dg_res, final)."""
    if final.get("status") == "auto" or not (use_mb or use_discogs):
        return mb_res, dg_res, final
    searched = {_search_key(album["title"], album["artist"])}
    for artist, title in album.get("log_titles") or ():
        key = _search_key(title, artist or album["artist"])
        if key in searched:
            continue
        searched.add(key)
        new_mb, new_dg, notes, failures = probe_search(
            http, shop_probe(album, {"artist": artist, "title": title}), "rip log's album title", album, cfg, dm,
            mb_res, dg_res, text, use_mb, use_discogs, provider_errors)
        source_notes.extend(failures)
        out["log_title_searched"] = list(out.get("log_title_searched") or []) + [f"{artist} / {title}"] + notes
        if new_mb is not mb_res or new_dg is not dg_res:
            mb_res, dg_res = new_mb, new_dg
            final = _combine(mb_res, dg_res)
    return mb_res, dg_res, final


# Programs whose window title an OCR of a screenshot reads (iZotope RX, Audacity, Spek ...)
_APP_TITLE = re.compile(r"\b(?:izotope|rx\s*\d+\s*(?:audio\s*)?editor|audacity|spek|adobe\s+audition|sound\s*forge|"
                        r"foobar2000|spectrogram|spectral\s+frequency)\b", re.I)
_FILE_EXT = re.compile(r"\.(?:wav|wv|flac|ape|mp3|m4a|aiff?|dsf|png|jpe?g|tiff?|bmp|cue|log|txt|nfo|pdf)\b", re.I)


def _is_code(name):
    """Whether a file's name is one catalogue number or barcode and nothing else."""
    codes = mc.codes_from_ocr_text(name)
    return any(mc.norm_code(c) == mc.norm_code(name) for c in codes["catnos"] + codes["barcodes"])


# Documents someone TYPED (a ripper's notes, an .nfo, a saved web page): a barcode found only
# in them is perhaps copied from another edition's page, so it never proves the pressing
# (match_core.typed_only_barcode). Rip logs, cue sheets (CATALOG: the disc's own MCN), PDF
# and Word booklets are the copy's own print.
TYPED_DOC_EXT = {".txt", ".nfo", ".info", ".md", ".htm", ".html", ".xml", ".json", ".csv", ".rtf"}


def split_documents(docs_txt, foreign=()):
    """(typed, printed): documents_text's sections by kind of document (TYPED_DOC_EXT).
    foreign: the names of cue sheets that fit no disc of the files (foreign_cues): their
    CATALOG is not this copy's, so they go with the typed text (a cue sheet that cannot be
    checked stays printed)."""
    typed, printed = [], []
    foreign = set(foreign or ())
    parts = re.split(r"(?m)^\(([^\n]*)\)\n", docs_txt or "")
    printed.append(parts[0])
    for name, body in zip(parts[1::2], parts[2::2]):
        (typed if Path(name).suffix.lower() in TYPED_DOC_EXT or name in foreign else printed).append(body)
    return "\n".join(typed), "\n".join(printed)


def drop_file_name_lines(text, album_dir):
    """The text without its lines that are a file's name: a document's "(name.cue)" heading,
    a screenshot's title bar ("Terrestrials (2014, SUNN200, side A).wv - iZotope RX 7") or
    any line naming a file of the folder (or one its cue sheet names)."""
    stems = set()
    for dirpath, _d, files in os.walk(album_dir):
        for n in files:
            stems.add(Path(n).stem)
            if n.lower().endswith(".cue"):
                try:
                    stems.update(Path(f).stem for f in
                                 re.findall(r'(?im)^\s*FILE\s+"([^"]+)"', _read_log(Path(dirpath) / n)))
                except OSError:
                    pass
    # only names holding a code ("sunn200", a long number): a track's name read off the
    # back cover ("01 acid mist tomorrow") is print, and stays. A name that is the code alone
    # ("QDISS0201CDD.jpg", "5013929020160.jpg") is the print the scan shows: it stays too.
    names = {mc.fold(n) for n in stems if not _is_code(n)}
    names = {n for n in names if len(n) >= 8 and re.search(r"[a-z]+\d|\d[a-z]+|\d{5,}", n)}
    kept = []
    for line in str(text or "").splitlines():
        low = mc.fold(line)
        if _APP_TITLE.search(line) or _FILE_EXT.search(line) or any(n in low for n in names):
            continue
        kept.append(line)
    return "\n".join(kept)


def _needs_investigation(final, album, ocr_txt, base):
    if not any(Path(d).suffix.lower() in IMAGE_EXT | DOC_EXT for _r, _d, fs in os.walk(base) for d in fs):
        return False
    if final.get("status") != "auto":
        return True
    best = final.get("best") or {}
    cand = best.get("cand") or {}
    have = {mc.barcode_key(b) for b in cand.get("barcodes") or ()}
    codes = mc.codes_from_ocr_text(ocr_txt)
    # the barcodes printed on the scans, and those decoded from them (cached by ocr_barcodes:
    # Kreator - Enemy Of God Revisited, 693723019529, while the pick had none)
    read = {mc.barcode_key(b) for b in list(codes["barcodes"]) + sorted(album.get("ocr_barcodes") or ())}
    if read and not (read & have):
        return True                                # the scans name another pressing
    if best.get("code") and best["code"] not in mc.WEAK_BARCODES:
        return False                               # a number of yours is on the pick already
    # catalogue numbers read on the scans (distinctive ones: letters and digits, as
    # score_candidate counts them) or typed in the rip log's paths, on the pick not at all
    theirs = cand.get("catnos") or ()
    mine = [c for c in codes["catnos"] if _distinctive(c)] + list(album.get("catnos_from_logs") or ())
    return bool(mine and theirs and not mc.catnos_agree(mine, theirs))


def _joined(readings):
    return "\n".join(r for r in readings if r)


def _barcode_settles(final, album):
    """A barcode decoded off the scans found the release the matcher is sure of: it is
    sure ("auto") and its pick carries that barcode."""
    decoded = {mc.barcode_key(b) for b in album.get("ocr_barcodes") or ()} - {""}
    if not decoded or final.get("status") != "auto":
        return False
    cand = (final.get("best") or {}).get("cand") or {}
    return bool(decoded & {mc.barcode_key(b) for b in cand.get("barcodes") or ()})


def plausible_scan_catno(code):
    """A catalogue number OCR read off a scan that may be one, not a misreading ("ZB 44",
    "Aq30" on a sleeve's artwork): the shape codes_from_ocr_text reads and no junk_code
    (5+ characters, letters and digits: "AMG 099", "NB 6570-2", "FO40CD" stay). Codes
    from tags, rip logs and file names are not asked this."""
    return not junk_code(code, "catno")


def scan_catnos(codes, kept_text=""):
    """codes["catnos"] without the misreadings (plausible_scan_catno), except those KEPT_TEXT
    (the folder's documents: rip logs, notes) names as well."""
    kept = {mc.norm_code(c) for c in mc.codes_from_ocr_text(kept_text, limit=50)["catnos"]} if kept_text else set()
    return [c for c in codes if mc.norm_code(c) in kept or plausible_scan_catno(c)]


INVESTIGATE_FIRST = 6        # scans the investigation reads first, in ocr_order's order
INVESTIGATE_BUDGET_S = 900


def investigate(http, base, album, cfg, dm, mb_res, dg_res, final, ocr_txt, out, notes, use_mb, use_discogs, db):
    """Read everything again, harder, and search every code found everywhere. In stages:
    the first INVESTIGATE_FIRST scans (those most likely to carry a code), their codes
    searched; the rest only when that leaves the album unsure. One time budget for all."""
    images, _key = list_images(base, 80)
    log = album.setdefault("scan_log", new_scan_log())
    stages = [images[:INVESTIGATE_FIRST], images[INVESTIGATE_FIRST:]] if images else [[]]
    started = time.time()
    state = {"deep": "", "tried": ({mc.barcode_key(b) for b in album["barcodes"]}
                                   | {mc.norm_code(c) for c in album.get("catno_search", [])}),
             "asked": set(), "titled": False, "codes": [], "strong": [], "dg_ids": [], "mb_ids": [],
             "found": 0, "kept_text": "", "rings": not album.get("ring_text")}
    text = ocr_txt
    for n, stage in enumerate(stages):
        if n and not stage:
            break
        left = INVESTIGATE_BUDGET_S - (time.time() - started)
        deep = _joined(each_scan(db, "deep", stage, lambda p: ocr_deep([p]), log, budget_s=left))
        if stage and time.time() - started > INVESTIGATE_BUDGET_S:
            deep += "\n(investigation stopped: time budget used)"
        if stage and state["rings"]:
            # the disc rings on their own as well: a matrix that clearly differs is told from them only
            rings = _joined(each_scan(db, "disc-rings-2", stage[:40], lambda p: ocr_disc_rings([p]), log,
                                      budget_s=60.0))
            if rings:
                album["ring_text"] = _joined([album.get("ring_text") or "", rings])
        if stage and not album.get("ocr_barcodes"):
            # the bars decoded on every scan, not only the first ones the first pass read; a
            # decoded code is in the text whole, so it is searched and counts as read. Scans
            # the first pass decoded without a result are not decoded again (~100 s)
            todo = [p for p in stage if p not in log["no_bars"]]
            decoded = decoded_barcodes(db, todo, log) if todo else set()
            if decoded:
                album["ocr_barcodes"] = decoded
                deep = deep + "\n" + "\n".join(sorted(decoded))
        if n == 0:
            deep = deep + "\n" + pdf_deep(base, db)
        state["deep"] = (state["deep"] + "\n" + deep).strip()
        # file names and screenshot title bars are no print, read deep as in _process (R12)
        text = drop_file_name_lines(ocr_txt + "\n" + state["deep"], base).strip()
        mb_res, dg_res, new_final = _investigate_codes(http, base, album, cfg, dm, mb_res, dg_res, final,
                                                       text, state, notes, use_mb, use_discogs)
        if n + 1 < len(stages) and stages[n + 1] and not _needs_investigation(new_final, album, text, base):
            break                                      # sure now: the other scans are not read
    engine = web_engine(cfg)[0]
    web_codes = state["codes"]
    out["investigated"] = {"codes": web_codes, "strong": state["strong"], "web_engine": engine,
                           "web_discogs": state["dg_ids"], "web_mb": state["mb_ids"],
                           "found": state["found"]}
    where = "MusicBrainz, Discogs" + (f" and the web ({engine})" if engine != "none" else
                                      " (other spellings too)")
    if new_final.get("best"):
        new_final["best"]["notes"] = new_final["best"]["notes"] + [
            "investigated: every scan read again in full; codes "
            + (", ".join(web_codes[:6]) or "none") + f" searched on {where}"]
    if new_final.get("status") != "auto":
        new_final["why"] = list(new_final.get("why") or []) + [
            "investigated: " + (f"codes {', '.join(web_codes[:6])} searched on {where}"
                                if web_codes else "no catalogue number or barcode readable on any scan")]
    return mb_res, dg_res, new_final, text


def _investigate_codes(http, base, album, cfg, dm, mb_res, dg_res, final, text, state, notes, use_mb,
                       use_discogs):
    """One stage of investigate: the codes in TEXT not searched yet, searched on MusicBrainz
    and Discogs (other spellings too) and the web; every candidate scored again with TEXT."""
    codes = mc.codes_from_ocr_text(text, limit=10)
    if not state["kept_text"]:
        state["kept_text"] = documents_text(base, ocr=False) or " "
    # a misreading ("ZB 44") is not searched; the documents' numbers are, as typed
    codes["catnos"] = scan_catnos(codes["catnos"], state["kept_text"])
    tried = state["tried"]
    new_b = [b for b in codes["barcodes"] if mc.barcode_key(b) not in tried]
    new_c = [c for c in codes["catnos"] if mc.norm_code(c) not in tried]
    tried.update(mc.barcode_key(b) for b in new_b)
    tried.update(mc.norm_code(c) for c in new_c)
    found = {"mb": [], "discogs": []}
    probe = dict(album, barcodes=set(new_b), catno_search=new_c, narrow_codes_first=True)
    if new_b or new_c:
        if use_mb:
            found["mb"] = mb_search(http, probe, cfg)
        if use_discogs:
            try:
                found["discogs"] = discogs_search(http, probe, cfg, dm)
            except AuthError:
                raise
            except Exception as exc:
                notes.append(f"Discogs search of codes from the investigation failed ({exc})")
    for c in codes["barcodes"] + codes["catnos"]:
        if c not in state["codes"]:
            state["codes"].append(c)
    first = mc.artist_parts(album["artist"])[0]
    # the strongest codes (at most 3, never junk) that no candidate lists: first their other
    # spellings on MusicBrainz and Discogs (free, cached), then the web when an engine is set up
    strong = strong_codes(text, codes, album.get("ocr_barcodes"))
    state["strong"] = strong
    kinds = code_kinds(codes, album.get("ocr_barcodes"))
    known = (found["mb"] + found["discogs"] + [s["cand"] for s in (mb_res or {}).get("scored", [])]
             + [s["cand"] for s in (dg_res or {}).get("scored", [])])
    # each code asked once, whichever stage read it
    missing = [c for c in strong if not any(_carries(k, c, kinds.get(c)) for k in known)
               and c not in state["asked"]]
    state["asked"].update(missing)
    dg_ids, mb_ids = (variant_release_links(http, missing, known, use_mb, use_discogs, kinds=kinds, notes=notes)
                      if missing else ([], []))
    engine = web_engine(cfg)[0]
    if engine != "none" and not dg_ids and not mb_ids:
        # the web: pages naming the codes, linking to the release on Discogs or MusicBrainz
        dg_ids, mb_ids = web_release_links(http, missing, first, cfg=cfg, notes=notes) if missing else ([], [])
        if not dg_ids and not mb_ids and not strong and not state["titled"]:
            # no code to tie it to: the album's own name on the web, once
            state["titled"] = True
            title = mc.search_title(album["title"])
            dg_ids, mb_ids = web_release_links(http, [f"{title}\" \"{first}"], "", limit=1, cfg=cfg, notes=notes)
    state["dg_ids"] += [x for x in dg_ids if x not in state["dg_ids"]]
    state["mb_ids"] += [x for x in mb_ids if x not in state["mb_ids"]]
    have = {str(c["id"]) for c in found["discogs"]} | {str(s["cand"]["id"]) for s in (dg_res or {}).get("scored", [])}
    for rid in dg_ids:
        if use_discogs and rid not in have:
            det = http.get_json(f"https://api.discogs.com/releases/{rid}", "discogs")
            if det:
                found["discogs"].append(discogs_candidate(det, dm))
    have = {c["id"] for c in found["mb"]} | {s["cand"]["id"] for s in (mb_res or {}).get("scored", [])}
    for rid in mb_ids:
        if use_mb and rid not in have:
            det = http.get_json(f"https://musicbrainz.org/ws/2/release/{rid}"
                                f"?inc=recordings+artist-credits+labels+url-rels&fmt=json", "mb")
            if det:
                found["mb"].append(mb_candidate(det))
    # (a barcode only typed in the notes or a saved page is searched, never your copy's own)
    sure = [b for b in mc.barcodes_read_whole(text, new_b) if not mc.typed_only_barcode(album, b)]
    if sure and not album["barcodes"]:
        album["barcodes"] = set(sure)
    state["found"] += len(found["mb"]) + len(found["discogs"])
    mb_res = _scored_again(mb_res, found["mb"], "mb", album, cfg, text)
    dg_res = _scored_again(dg_res, found["discogs"], "discogs", album, cfg, text)
    return mb_res, dg_res, mc.combine(mb_res, dg_res) or final


def process(row, http, cfg, dm, args):
    """Decide one album. Every kind of evidence is tried first with the country
    preference switched off: it is the LAST resort, and a tie it settled early
    would skip the steps that can really tell the pressings apart (the scans,
    fingerprints, ISRCs)."""
    full_cfg = cfg
    # (evidence_pass: a pick by date or entry is held back for the country order: TIE-2)
    cfg = dict(cfg, preferred_countries=[], evidence_pass=bool(full_cfg.get("preferred_countries")))
    out = _process(row, http, cfg, dm, args, full_cfg)
    return out


# The Discogs search runs on a worker thread while MusicBrainz is searched (each host on one
# thread, its pacing shared: Http.thread_clone), and the scans are read while the databases
# are searched (_ScanReads). Only the order of the work changes, never the result; False
# runs everything in turn (tests compare the two).
CONCURRENT_SOURCES = True
CONCURRENT_SCANS = True


def _process(row, http, cfg, dm, args, full_cfg):
    started = []                    # the worker threads and pools this album started
    try:
        return _process_album(row, http, cfg, dm, args, full_cfg, started)
    except BaseException:
        for w in started:           # given up (an error, a cancel): nothing left running
            w.abort()
        raise
    finally:
        for w in started:
            w.close()


def _process_album(row, http, cfg, dm, args, full_cfg, started):
    base = Path(row["FullPath"])
    if not base.is_dir():
        # Moved or renamed since the audit (Picard moves a folder when it tags the album).
        # Not a "no match": nobody looked it up. Its new folder is picked up by a fresh audit.
        return {"path": row["FullPath"], "artist_folder": row["AlbumArtistFolder"],
                "album_folder": row["AlbumFolder"], "tag_artist": "", "tag_title": "", "tag_year": None,
                "tracks": 0, "status": "moved", "best": None, "alts": [], "version": result_version(),
                "why": ["folder no longer exists (moved or renamed, e.g. by the tagger after tagging); "
                        "run audit_untagged.py again to pick up its new folder"]}
    # Ignore/Skip rules, as early as the files' own tags and names allow: no
    # lookups, fingerprints or OCR for what is excluded anyway.
    anyway = getattr(args, "process_anyway", False)
    # (the engine hands its profile's rules over; the standalone tool reads them here)
    rules = getattr(args, "rules", None)
    rules = exclusions.load_rules() if rules is None else rules
    dec = exclusions.gate(base, "match", rules, process_anyway=anyway)
    if dec.all_excluded:
        whole = dec.summary()
        return {"path": row["FullPath"], "artist_folder": row["AlbumArtistFolder"],
                "album_folder": row["AlbumFolder"], "tag_artist": "", "tag_title": "", "tag_year": None,
                "tracks": len(dec.excluded), "status": "ignored" if whole.action == "ignore" else "skipped",
                "best": None, "alts": [], "why": [whole.reason()], "excluded_by": whole.rule.id,
                "rules_hash": exclusions.rules_hash(rules), "version": result_version(), "checked": round(time.time())}
    # a mixed folder is matched one album at a time: the engine names the other files
    others = {str(x) for x in getattr(args, "exclude_files", ()) or ()}
    album = read_album(base, row["AlbumArtistFolder"], row["AlbumFolder"],
                       exclude={str(x) for x in dec.excluded} | others, blind=getattr(args, "blind", False))
    out = {"path": row["FullPath"], "artist_folder": row["AlbumArtistFolder"],
           "album_folder": row["AlbumFolder"], "tag_artist": album["artist"],
           "tag_title": album["title"], "tag_year": album["year"],
           "tracks": len(album["tracks"])}
    if dec.excluded:                                     # a mixed folder: the rest is still matched
        out["excluded_files"] = [[p.name, e.label, e.rule.name] for p, e in dec.excluded.items()]
    if not album["tracks"]:
        out.update(status="nomatch", why=["no audio files"], best=None, alts=[])
        return out
    if album.get("dropped_formats"):
        out["dropped_formats"] = album["dropped_formats"]
    if album.get("dropped_tracks"):
        out["dropped_tracks"] = album["dropped_tracks"]

    images, img_key = [], ""
    scans_ahead = None
    db = getattr(http, "db", None)
    scan_log = album["scan_log"] = new_scan_log()     # what each scan gave (ocr_order.record)
    # --ocr / the profile's "ocr": on, off, or auto (only folders with scans or a PDF)
    use_ocr = ocr_wanted(getattr(args, "ocr", False), base)
    if use_ocr:
        images, img_key = list_images(base, getattr(args, "max_images", 15))
        if images:
            found = decoded_barcodes(db, images, scan_log)
            album["ocr_barcodes"] = found               # decoded off the scans, whatever the tags say
            if found and not album["barcodes"]:
                album["barcodes"] = found
                out["barcodes_from_ocr"] = ";".join(sorted(found))
            if CONCURRENT_SCANS:
                # read while the databases are searched; what each_scan reads below is taken
                # from here (the scans _process would read, in the order it needs them)
                scans_ahead = _ScanReads.start(db, images, not album["barcodes"])
                if scans_ahead is not None:
                    started.append(scans_ahead)
        elif not getattr(args, "blind", False) and ocr_mode(getattr(args, "ocr", False)) == "on":
            # No scans: the front cover embedded in the files is read instead, for a
            # catalogue number or label (a clue and codes to search). Only with OCR on
            # (auto runs for a folder with only a PDF, whose cover came from anywhere), and
            # never when blind: a tagged album's embedded cover came with the release it was
            # tagged as.
            cover_bytes, cover_key = embedded_cover(album)
            if cover_bytes:
                # cached per language set, as the scans: a new language pack reads it again
                kind = "cover" if ocr_langs() == "eng" else f"cover-{ocr_langs()}"
                cover = _cached(db, kind, cover_key, lambda: ocr_cover(cover_bytes))
                album["cover_text"] = cover.get("text") or ""
                found = set(cover.get("barcodes") or ())
                if found and not album["barcodes"]:
                    album["barcodes"] = found          # decoded whole: read, as from a scan
                    out["barcodes_from_ocr"] = ";".join(sorted(found))
                if album["cover_text"].strip():
                    out["cover_read"] = True

    source_notes = []
    # A clue host's failure (CTDB): a note in `why`, never what turns a no match into an
    # error (engine.CLUE_HOSTS); a MusicBrainz disc ID failure counts as MusicBrainz's.
    clue_notes = []
    provider_errors = getattr(args, "provider_errors", None)
    if album.get("tocs"):
        try:
            album["toc_releases"] = toc_releases(http, album)
        except Exception as exc:          # a clue: its failure never costs the album its match
            album["toc_releases"] = set()
            album.setdefault("clue_failures", {})["disc lookup"] = (f"{type(exc).__name__}: {exc}", "outage")
    if album.get("tag_urls") or album.get("isrcs") or album.get("store_tracks"):
        try:
            link_releases(http, album)
        except Exception as exc:          # (link_releases catches the lookups' own failures)
            # (the tags' own release links are candidates, tag_release_mb: never proof)
            album.setdefault("link_releases", set())
            album["isrc_releases"] = set()
            album.setdefault("clue_failures", {})["musicbrainz link lookup"] = (
                f"{type(exc).__name__}: {exc}", "outage")
    use_mb = getattr(args, "use_mb", True)
    use_discogs = getattr(args, "use_discogs", True) and dm is not None
    # Discogs searched on a worker while MusicBrainz is (its result used below, where the
    # search ran before: the notes and errors in the same order)
    dg_ahead = _DiscogsAhead.start(http, album, cfg, dm) if (CONCURRENT_SOURCES and use_mb and use_discogs) else None
    if dg_ahead is not None:
        started.append(dg_ahead)
    if use_mb:
        try:
            mb_res = run_source("mb", lambda: mb_search(http, album, cfg), album, cfg)
        except ProviderUnavailable as exc:             # Discogs may still identify the album
            if not use_discogs:
                raise
            mb_res = None
            source_notes.append(f"MusicBrainz lookup failed ({exc}); result is Discogs-only")
            if provider_errors is not None:
                provider_errors["musicbrainz"] = exc.kind
        # the disc ID lookup failed (a 503 burst): asked once more now; when it answers, the
        # MusicBrainz candidates are scored again with it (what was fetched is cached)
        if mb_res is not None and retry_disc_ids(http, album):
            if album["toc_releases"]:
                out["toc_releases"] = len(album["toc_releases"])
            try:
                mb_res = run_source("mb", lambda: mb_search(http, album, cfg), album, cfg)
            except ProviderUnavailable as exc:
                if not use_discogs:
                    raise
                mb_res = None
                source_notes.append(f"MusicBrainz lookup failed ({exc}); result is Discogs-only")
                if provider_errors is not None:
                    provider_errors["musicbrainz"] = exc.kind
    else:
        mb_res = None
    # each failed disc or link lookup: a note (a MusicBrainz one is a source note, the
    # album retried; CTDB's a clue note), ahead of the searches' own notes
    mb_clue_notes = []
    for what, note in zip(album.get("clue_failures") or {}, clue_failure_notes(album, provider_errors)):
        (mb_clue_notes if what in MB_CLUES else clue_notes).append(note)
    source_notes[:0] = mb_clue_notes
    dg_res = None
    # Discogs is asked too, also when MusicBrainz is sure: your pressing may be listed
    # only there (Kreator Endorama, Art Music AMG 099), and its catalogue number then
    # outranks a MusicBrainz release that contradicts the folder name.
    if use_discogs:
        try:
            dg_res = run_source("discogs", dg_ahead.result if dg_ahead is not None
                                else lambda: discogs_search(http, album, cfg, dm), album, cfg)
        except AuthError:
            raise
        except Exception as exc:     # keep what MusicBrainz found
            source_notes.append(f"Discogs lookup failed ({type(exc).__name__}: {exc}); "
                                "result is MusicBrainz-only")
            if provider_errors is not None:
                provider_errors["discogs"] = getattr(exc, "kind", "outage")
    final = _combine(mb_res, dg_res)

    ocr_txt = ""
    # Every document in the folder (notes, logs, PDF booklets, Word/HTML pages) is read
    # with the scans: the codes in it are searched and compared like printed ones.
    docs_txt = documents_text(base, ocr=use_ocr, db=db)
    if docs_txt.strip():
        out["documents_read"] = len(album_documents(base))
    # The scans are read for EVERY album that has them, also when a database is already
    # sure: the back and credits (a catalogue number, "Made in", the label's address)
    # tell which pressing your copy is, which a title match cannot.
    cover_txt = album.get("cover_text") or ""
    if (use_ocr and images) or docs_txt.strip() or cover_txt.strip():
        scans = images if use_ocr else []
        # A barcode decoded off the scans that already found a release the matcher is sure
        # of (and that carries it): the page text of the first three scans is enough, the
        # rest are not read. Otherwise every scan is, as always.
        page_scans = scans[:3] if _barcode_settles(final, album) else scans
        if len(page_scans) < len(scans):
            out["scans_read"] = f"{len(page_scans)} of {len(scans)} (the decoded barcode found the release)"
        # cached per scan and language set: installing a language pack reads the scans again
        kind = "text" if ocr_langs() == "eng" else f"text-{ocr_langs()}"
        text = _joined(each_scan(db, kind, page_scans, lambda p: ocr_text([p]), scan_log, ahead=scans_ahead))
        # a file's name (a document's heading, a screenshot's title bar) is not print on the
        # copy: "SUNN200" in an RX window's title is no "catalogue number (OCR)" (R12)
        # which barcodes were only typed (notes, .nfo, saved pages) and which printed on the
        # copy (scans, booklets, a cue's CATALOG): score_candidate weighs them apart (Q3)
        typed_docs, printed_docs = split_documents(docs_txt, album.get("foreign_cues"))
        album["typed_text"] = typed_docs
        album["printed_text"] = printed_docs + "\n" + text
        text = drop_file_name_lines(docs_txt + "\n" + text, base).strip()
        # spreads are read sideways too
        spines = _joined(each_scan(db, "spines" if ocr_langs() == "eng" else f"spines-{ocr_langs()}",
                                   page_scans, lambda p: ocr_spines([p]), scan_log, ahead=scans_ahead))
        if spines.strip():
            text = text + "\n" + spines
            album["printed_text"] += "\n" + spines
        if not album["barcodes"] and scans:
            # the digit band read alone, the first digit completed (15 scans, 2 minutes)
            digits = _joined(each_scan(db, "barcode-digits", scans[:15], lambda p: ocr_barcode_digits([p]),
                                       scan_log, budget_s=120.0, ahead=scans_ahead))
            if digits.strip():
                text = text + "\n" + digits
                album["printed_text"] += "\n" + digits
        # every disc read, also when the barcode settled it: a matrix that differs is told only here
        rings = _joined(each_scan(db, "disc-rings-2", scans[:40], lambda p: ocr_disc_rings([p]), scan_log,
                                  budget_s=60.0, ahead=scans_ahead))
        if scans_ahead is not None:
            scans_ahead.close()                        # what was read ahead and not needed: dropped
        if rings.strip():
            text = text + "\n" + rings
            album["printed_text"] += "\n" + rings
            album["ring_text"] = rings                 # matrix codes: a conflict is told only here
        if text.strip() or cover_txt.strip():
            # Barcodes and catalogue numbers printed on the scans are SEARCHED, not only
            # compared: the release they identify may not be among what the title found.
            # The embedded cover's codes are searched too, but its text is not scored as
            # scans (it may show another pressing's cover): only cover_clue weighs it.
            codes = mc.codes_from_ocr_text((text + "\n" + cover_txt).strip())
            known_b = {mc.barcode_key(b) for b in album["barcodes"]}
            known_c = {mc.norm_code(c) for c in album.get("catno_search", [])}
            new_b = [b for b in codes["barcodes"] if mc.barcode_key(b) not in known_b]
            # a misreading ("ZB 44") is not searched; the documents' numbers are, as typed
            new_c = [c for c in scan_catnos(codes["catnos"], docs_txt) if mc.norm_code(c) not in known_c]
            extra = {"mb": [], "discogs": []}
            # a label the documents name ("Label: X") is searched with the catalogue numbers
            labels = album.get("search_labels") or []
            # new: not among the two labels the first search paired (label_catnos); the notes
            # in subfolders name labels too, after the tags' own
            new_l = [x for x in labels_from_text(text) if x not in labels[:2]]
            labels = [x for x in labels if x not in new_l]
            if new_l and not new_c and not album.get("catno_search"):
                new_l = []                               # nothing to search the label with
            if new_b or new_c or new_l:
                if new_b or new_c:
                    out["codes_from_ocr"] = ";".join(new_b + new_c)
                # Only new work: the new codes, and the labels paired with them. A label only
                # the documents name goes FIRST (label_catnos keeps two labels), and with no new
                # number it is searched alone with the album's numbers; the album's ASINs and
                # numbers alone were searched already.
                # each number with the artist first; the label with it and the number alone
                # only when that finds nothing (narrow_codes_first)
                probe = dict(album, barcodes=set(new_b), catno_search=new_c, asins=[],
                             search_labels=(new_l + labels) if new_c else new_l, narrow_codes_first=True)
                if not new_c:
                    probe["label_catnos"] = album.get("catno_search", [])
                try:
                    extra["mb"] = mb_search(http, probe, cfg) if use_mb else []
                except ProviderUnavailable as exc:      # as for the title search: retried later
                    if not use_discogs:
                        raise
                    source_notes.append(f"MusicBrainz lookup of codes read from scans failed ({exc})")
                    if provider_errors is not None:
                        provider_errors["musicbrainz"] = exc.kind
                try:
                    extra["discogs"] = discogs_search(http, probe, cfg, dm) if use_discogs else []
                except AuthError:
                    raise
                except Exception as exc:
                    source_notes.append(f"Discogs lookup of codes read from scans failed ({exc})")
                # a barcode the ripper's notes typed is searched, never your copy's own (R3): it
                # may be copied from another edition's page; decoded off the scans, it is
                # (a saved web page or .nfo typing it is no more your copy's than the notes)
                sure = [b for b in mc.barcodes_read_whole(text, new_b)
                        if not mc.typed_only_barcode(album, b)]
                if not album["barcodes"] and sure:
                    album["barcodes"] = set(sure)        # as for barcodes decoded from the scans

            seen_ids = _cand_ids(mb_res, dg_res)
            mb_res = _scored_again(mb_res, extra["mb"], "mb", album, cfg, text)
            dg_res = _scored_again(dg_res, extra["discogs"], "discogs", album, cfg, text)
            _note_found(album, seen_ids, (mb_res, dg_res), "scan_search")
            final = _combine(mb_res, dg_res)
            ocr_txt = text

    # Still unsure with a barcode (tags, scans, documents): the album the shops sell under
    # it, searched by the shop's own title and artist. The shop agreeing is a clue, never
    # barcode proof for a release that does not list the barcode itself.
    shops_asked = set()
    seen_ids = _cand_ids(mb_res, dg_res)
    mb_res, dg_res, final = shop_step(http, album, cfg, dm, mb_res, dg_res, final, ocr_txt, args, use_mb,
                                      use_discogs, out, source_notes, shops_asked)
    _note_found(album, seen_ids, (mb_res, dg_res), "barcode_lookup")
    # a barcode on the scans that no release anywhere carries: a shop's sticker, perhaps
    # (match_core._shop_sticker); each candidate is scored again with it known
    if (final["status"] != "auto" and not album.get("unlisted_barcodes")
            and both_databases_answered(use_mb, use_discogs, provider_errors)):
        unlisted = unlisted_scan_barcodes(http, album, (mb_res, dg_res), args)
        if unlisted:
            album["unlisted_barcodes"] = unlisted
            mb_res, dg_res = (_rescored_all(r, album, cfg, ocr_txt) for r in (mb_res, dg_res))
            final = _combine(mb_res, dg_res)

    # Still unsure with a table of contents: what gnudb (freedb) holds for the disc. Its
    # title and artist are searched; never proof (typed in by anyone, for any pressing).
    mb_res, dg_res, final = gnudb_step(http, album, cfg, dm, mb_res, dg_res, final, ocr_txt, args, use_mb,
                                       use_discogs, out, source_notes)

    # Still unsure with a rip log naming another album ("Jesu / Conqueror (Japanese Bonus
    # Disc)"): that album searched too, so one disc of a set can be matched as that disc
    mb_res, dg_res, final = log_title_step(http, album, cfg, dm, mb_res, dg_res, final, ocr_txt, use_mb,
                                           use_discogs, out, source_notes, provider_errors)

    # Still unsure: fingerprint a few tracks (AcoustID) and add the releases that
    # hold those recordings; they also prove the album when its title differs.
    # (profile "acoustid": unsure - this; always - sure albums are checked below; off - never)
    fp_mode = acoustid_mode(getattr(args, "acoustid", "unsure"))
    if final["status"] != "auto" and fp_mode != "off" and use_mb and fingerprint_ready():
        try:
            rels = acoustid_releases(http, album, db)
        except Exception as exc:
            rels = set()
            source_notes.append(f"fingerprint lookup failed ({exc})")
        if rels:
            album["fp_releases"] = rels
            out["fingerprint_releases"] = len(rels)
            have = {s["cand"]["id"] for s in (mb_res or {}).get("scored", [])}
            new = []
            for rid in sorted(rels - have)[:6]:
                det = http.get_json(f"https://musicbrainz.org/ws/2/release/{rid}"
                                    f"?inc=recordings+artist-credits+labels+url-rels&fmt=json", "mb")
                if det:
                    new.append(mb_candidate(det))
            if (mb_res or {}).get("scored") or new:
                seen_ids = _cand_ids(mb_res, dg_res)
                mb_res = _scored_again(mb_res, new, "mb", album, cfg, ocr_txt)
                _note_found(album, seen_ids, (mb_res, dg_res), "fingerprint")
                final = _combine(mb_res, dg_res)
    # Still unsure: the album on Deezer, through its ISRCs, to the MusicBrainz release
    if final["status"] != "auto" and use_mb and not album.get("isrcs"):
        try:
            rels = deezer_isrc_releases(http, album)
        except Exception:
            rels = set()
        if rels:
            album["isrc_releases"] = rels
            out["isrc_releases"] = len(rels)
            have = {s["cand"]["id"] for s in (mb_res or {}).get("scored", [])}
            new = []
            for rid in sorted(rels - have)[:6]:
                det = http.get_json(f"https://musicbrainz.org/ws/2/release/{rid}"
                                    f"?inc=recordings+artist-credits+labels+url-rels&fmt=json", "mb")
                if det:
                    new.append(mb_candidate(det))
            if (mb_res or {}).get("scored") or new:
                mb_res = _scored_again(mb_res, new, "mb", album, cfg, ocr_txt)
                final = _combine(mb_res, dg_res)
    if album.get("discids"):
        out["discids"] = album["discids"]

    # Still unsure, or the scans show a barcode the pick does not carry: investigate.
    # Every scan (not only the first few), every zone at full resolution and every
    # orientation; every code found is searched on MusicBrainz and Discogs (other spellings
    # too) and, with a web search engine set up, the strongest three on the web.
    # An album with scans goes to a person only after all of that has failed.
    if use_ocr and getattr(args, "investigate", True) and _needs_investigation(final, album, ocr_txt, base):
        seen_ids = _cand_ids(mb_res, dg_res)
        try:
            mb_res, dg_res, final, ocr_txt = investigate(
                http, base, album, cfg, dm, mb_res, dg_res, final, ocr_txt, out, source_notes,
                use_mb, use_discogs, db)
        except AuthError:
            raise
        except Exception as exc:
            source_notes.append(f"investigation of the scans failed ({type(exc).__name__}: {exc})")
        _note_found(album, seen_ids, (mb_res, dg_res), "scan_search")
        # a barcode only the deep reading found is asked of the shops too
        seen_ids = _cand_ids(mb_res, dg_res)
        mb_res, dg_res, final = shop_step(http, album, cfg, dm, mb_res, dg_res, final, ocr_txt, args, use_mb,
                                          use_discogs, out, source_notes, shops_asked)
        _note_found(album, seen_ids, (mb_res, dg_res), "barcode_lookup")

    # Last resort: the country preference, between pressings nothing else told apart
    if final["status"] != "auto" and full_cfg.get("preferred_countries"):
        again = []
        for res in (mb_res, dg_res):
            if res and res.get("scored"):
                res = dict(mc.classify(res["scored"], full_cfg), scored=res["scored"], source=res.get("source"))
            again.append(res)
        mb_res, dg_res = again
        final = _combine(mb_res, dg_res)
    # Both databases sure of different pressings, and nothing about your copy tells them
    # apart: your country order decides between them as it does within one database, then
    # Picard's preferred release types and formats (only for one album, and a guess).
    if full_cfg.get("preferred_countries"):
        final = _combine(mb_res, dg_res, full_cfg.get("preferred_countries"), mc.release_preferences(full_cfg))

    # The rules again, with what identification found (a release titled "[no vox]")
    if final.get("best") and not anyway:
        c = final["best"]["cand"]
        release = release_fields(c)
        kept = [Path(t["path"]) for t in album["tracks"] if t.get("path")]
        dec2 = exclusions.gate(kept, "match", rules, release=release) if kept else None
        if dec2 is not None and dec2.all_excluded:
            whole = dec2.summary()
            out.update(status="ignored" if whole.action == "ignore" else "skipped", why=[whole.reason()],
                       best=summarize(final), alts=[], excluded_by=whole.rule.id,
                       rules_hash=exclusions.rules_hash(rules), version=result_version(), checked=round(time.time()))
            return out

    final = _sure_if_no_album_only(final, album, cfg)
    status = final["status"]
    why = list(final["why"]) + source_notes + clue_notes
    if (status == "auto" and not album.get("unlisted_barcodes")
            and scans_barcode_why(final, album, ocr_txt, (mb_res, dg_res))
            and both_databases_answered(use_mb, use_discogs, provider_errors)):
        # BAR-4: a barcode on the scans no candidate carries is searched (databases, shops)
        # before it is ignored as a sticker or kept as another pressing's
        album["unlisted_barcodes"] = unlisted_scan_barcodes(http, album, (mb_res, dg_res), args)
    disagrees = _copy_disagrees(final, album, ocr_txt, (mb_res, dg_res)) if status == "auto" else []
    if disagrees:
        status = "review"
        why += disagrees
    if album.get("foreign_logs"):
        out["foreign_logs"] = album["foreign_logs"]
    if album.get("media_hint_from") not in (None, "", "folder name"):
        out["media_hint"] = f"{album['media_hint']} ({album['media_hint_from']})"
    if source_notes and status == "nomatch":
        status = "error"      # not a real "no match": must be retried, not filed
    # (a disc lookup that failed as a whole, "disc lookup", lost the MusicBrainz disc ID too)
    lost = [what for what in MB_CLUES + ("disc lookup",) if what in (album.get("clue_failures") or {})
            and (what != "musicbrainz disc ID" or album.get("tocs"))]
    if status == "auto" and lost and not hard_proof(final.get("best")):
        # MusicBrainz could not be asked for your copy's disc ID (or its links/ISRCs), which
        # may have pointed at another pressing, and nothing else proves this one: retried
        # once MusicBrainz answers, never filed on the weaker clues
        status = "error"
        why.append(f"{' and '.join(lost).replace('musicbrainz', 'MusicBrainz')} failed and nothing "
                   "else proves this pressing (exact disc ID, the files' shop link, barcode): "
                   "tried again later")
    if row.get("Status") == "partial" and status == "auto":
        status = "review"
        why.append("album is partially tagged already")
    alts = []
    for res in (mb_res, dg_res):
        if res and res.get("scored"):
            for s in sorted(res["scored"], key=lambda s: -s["confidence"])[:3]:
                c = s["cand"]
                alts.append(f"{c['source']}:{c['id']} {s['confidence']} {c['artist']} - {c['title']} ({c['year']})")
    # every candidate scored, not only the three each "alts" shows: a Partial album
    # (engine._partial) must see each one that fits about as well, not guess at a fourth
    pool = []
    for res in (mb_res, dg_res):
        for s in sorted((res or {}).get("scored") or [], key=lambda s: -s["confidence"]):
            e = f"{s['cand']['source']}:{s['cand']['id']} {s['confidence']}"
            if e not in pool:
                pool.append(e)
    # each database's own sure pick (engine.follow_source: whether the other database's
    # release ID in your files is contradicted, or only not confirmed)
    sure_picks = {name: str(res["best"]["cand"]["id"]) for name, res in (("mb", mb_res), ("discogs", dg_res))
                  if res and res.get("status") == "auto" and res.get("best")}
    out.update(status=status, why=why, best=summarize(final), alts=alts, pool=pool, choices=release_choices((mb_res, dg_res)),
               sure_picks=sure_picks,
               margin=final.get("margin"), version=result_version(), checked=round(time.time()))
    note_barcode_sources(out, album)
    note_gnudb(out, album, final)
    # (reporting only: what the scans showed about the pick, and how it was found)
    if final.get("best"):
        report = scans_report(final["best"], album, ocr_txt, (mb_res, dg_res),
                              bool(use_ocr and (images or _has_pdf(base))))
        if report:
            out["scans"] = report
        out["via"] = found_via(final["best"], album)
    if status == "auto" and getattr(args, "learn_scan_order", True):
        learn_scan_order(scan_log, final)
    mb_pick = (out.get("best") or {}).get("mb_id")
    if status == "auto" and fp_mode == "always" and fingerprint_ready():
        # acoustid: always - a sure album is fingerprinted too; a disagreement is noted for
        # whoever reviews it, and the pick stands (the fingerprint is one more clue).
        # "acoustid_checked" marks it done (or nothing to compare: a pick without
        # MusicBrainz), so a later --acoustid always run does not redo it; a failed check
        # is left unmarked and tried again.
        note, checked = "", True
        if use_mb and mb_pick:
            try:
                note = acoustid_disagreement(http, album, mb_pick, db)
            except Exception as exc:
                note, checked = f"AcoustID check failed ({exc}); carried on without it", False
        if note:
            out["acoustid_notes"] = [note]
        out["acoustid_checked"] = checked
    if status != "auto":
        ao = mc.album_only(final, cfg, tie_source(album, final))
        if ao:
            out["album_only"] = ao              # the album is certain, its pressing is not
    else:
        ao = mc.scans_name_another(final, out.get("scans"))
        if ao:                                  # (sure of a pressing your scans contradict)
            out["album_only"], out["status"] = ao, "review"
    return out


# --- Reporting only: what the scans showed about the pick, and how it was found. Nothing
# here is read by scoring or classification; a score is a heuristic, never proof.

def _cand_ids(*results):
    """"source:id" of every candidate scored in these results."""
    return {f"{s['cand']['source']}:{s['cand']['id']}" for res in results
            for s in (res or {}).get("scored") or ()}


def _note_found(album, before, results, via):
    """The candidates RESULTS hold that BEFORE did not: found by the step VIA ("scan_search",
    "barcode_lookup", "fingerprint"), kept in album["found_via"] (the first step wins)."""
    found = album.setdefault("found_via", {})
    for key in _cand_ids(*results) - set(before):
        found.setdefault(key, via)


def _has_pdf(base):
    return any(p.suffix.lower() == ".pdf" for p in album_documents(base))


# a clue pressing_clues found on the scans -> the kind of thing read
_SCAN_CLUE_KINDS = {"barcode": "barcode", "catalogue number": "catalogue_number", "label": "label",
                    "country": "country", "matrix": "matrix", "sid code": "matrix", "label code": "matrix",
                    "packaging": "format"}
# a clue against that pressing_clues / file_clues took from the scans -> its kind
_SCAN_AGAINST_KINDS = (("scans say made in ", "country"), ("disc ring reads ", "matrix"),
                       ("your scans show a ", "format"))
SCAN_STATES = ("proven", "agrees", "differs", "unread")


def scans_report(best, album, ocr_txt, results, had_scans):
    """What the album's scans (and the barcodes decoded off them) showed about BEST, the
    scored pick: {"state", "found", "against"}, or None when the folder has no scans or
    OCR did not run. state: "proven" (a barcode or catalogue number read on the scans is on
    the pick), "agrees" (only weaker clues: label, country, matrix, packaging), "differs"
    (the scans name another pressing), "unread" (nothing usable about the pick was read).
    "printed": True when the barcode or catalogue number on the pick is printed on your copy, not
    only typed in a note (plan._files_on_evidence, the one thing that reads this report)."""
    if not had_scans or not best:
        return None
    cand = best.get("cand") or {}
    clues = best.get("clues") or {}
    found, against = [], []

    def add(lst, kind):
        if kind not in lst:
            lst.append(kind)

    for key, kind in _SCAN_CLUE_KINDS.items():
        if key in clues:
            add(found, kind)
    code = str(best.get("code") or "")
    if code == "barcode (OCR)":
        add(found, "barcode")
    elif code == "catalogue number (OCR)":
        add(found, "catalogue_number")
    have = {mc.barcode_key(b) for b in cand.get("barcodes") or ()} - {""}
    # the barcodes read on the scans: decoded, or printed and read whole (not the notes'
    # typed ones, nor a sticker no release carries)
    printed = mc.codes_from_ocr_text(ocr_txt or "", limit=10)["barcodes"]
    typed = {mc.barcode_key(b) for b in album.get("note_barcodes") or ()}
    printed = [b for b in printed if mc.barcode_key(b) not in typed and not mc.typed_only_barcode(album, b)]
    stickers = {mc.barcode_key(b) for b in album.get("unlisted_barcodes") or ()}
    read = {mc.barcode_key(b) for b in sorted(album.get("ocr_barcodes") or ())
            + mc.barcodes_read_whole(ocr_txt or "", printed)} - {""} - stickers
    if read & have:
        add(found, "barcode")
    elif read and have:                           # (a release that lists no barcode contradicts none)
        add(against, "barcode")
    # a catalogue number read on the scans that another scored pressing carries, not this one
    if "catalogue_number" not in found and ocr_txt:
        mine = [c for c in scan_catnos(mc.codes_from_ocr_text(ocr_txt)["catnos"]) if _distinctive(c)]
        theirs = cand.get("catnos") or ()
        others = [s["cand"].get("catnos") or () for res in results for s in (res or {}).get("scored") or ()
                  if s.get("cand") is not cand and str(s["cand"].get("id")) != str(cand.get("id"))]
        if any(not mc.catnos_agree([c], theirs) and any(mc.catnos_agree([c], o) for o in others) for c in mine):
            add(against, "catalogue_number")
    for words in best.get("clues_against") or ():
        for prefix, kind in _SCAN_AGAINST_KINDS:
            if str(words).startswith(prefix):
                add(against, kind)
    numbers = {"barcode", "catalogue_number"}
    if set(found) & numbers and not set(against) & numbers:
        state = "proven"
    elif against:
        state = "differs"
    elif found:
        state = "agrees"
    else:
        state = "unread"
    out = {"state": state, "found": found, "against": against}
    on_copy = mc.codes_from_ocr_text(album.get("printed_text") or "", limit=50)["catnos"]
    if state == "proven" and (read & have or any(mc.catnos_agree([c], cand.get("catnos") or ()) for c in on_copy)):
        out["printed"] = True
    return out


def found_via(best, album):
    """How BEST was found, when not by the plain search: "disc_id" (the disc ID / TOC),
    "link" (the files' shop or source link), "scan_search" (numbers read on the scans,
    searched), "barcode_lookup" (a shop's barcode lookup), "fingerprint" (AcoustID); ""
    otherwise. Reporting only."""
    if not best:
        return ""
    clues = best.get("clues") or {}
    if "disc TOC" in clues:
        return "disc_id"
    if "source link" in clues:
        return "link"
    cand = best.get("cand") or {}
    return (album.get("found_via") or {}).get(f"{cand.get('source')}:{cand.get('id')}", "")


def scans_confirmed(log, cand):
    """The scans of LOG whose own reading holds a code CAND carries: a barcode (decoded off
    that scan, or printed on it and read whole), a distinctive catalogue number, a matrix."""
    barcodes = [b for b in cand.get("barcodes") or () if mc.barcode_key(b)]
    keys = {mc.barcode_key(b) for b in barcodes}
    catnos = [c for c in cand.get("catnos") or () if _distinctive(c)]
    matrices = mc.cand_matrices(cand)
    own = {"".join(mc.matrix_tokens(c)) for c in cand.get("catnos") or ()} | keys
    hits = []
    for p in sorted(set(log.get("text") or {}) | set(log.get("barcodes") or {})):
        text = "\n".join(log.get("text", {}).get(p) or ())
        hay = mc.norm_code(text)
        if ({mc.barcode_key(b) for b in log.get("barcodes", {}).get(p) or ()} & keys
                or any(mc.barcode_printed(text, b) for b in barcodes)
                or any(mc.norm_code(c) in hay for c in catnos)
                or any(mc.matrix_read(text, v, own) for _k, v in matrices)):
            hits.append(p)
    return hits


def learn_scan_order(log, final):
    """After a sure identification: count, per role (ocr_order), the scans OCR read this
    time and those whose code the identified release carries. Only role counts are kept."""
    cand = (final.get("best") or {}).get("cand")
    if not log or not log.get("read") or not cand:
        return
    try:
        ocr_order.record(sorted(log["read"]), scans_confirmed(log, cand))
    except OSError:
        pass


COLS = ["Status", "Confidence", "Source", "ID", "URL", "MatchedArtist", "MatchedTitle",
        "MatchedYear", "MatchedCountry", "MatchedLabel", "Margin", "Evidence",
        "WhyNotAuto", "Alternatives", "TagArtist", "TagAlbum", "TagYear", "Tracks",
        "AlbumArtistFolder", "AlbumFolder", "FullPath", "BarcodeFromOCR", "CodesFromOCR", "LinkedDiscogsID",
        "Notes"]


def barcode_source_note(album):
    """Words saying the barcode came from a UPC / EAN / ASIN tag, not BARCODE ("" when not)."""
    if not album.get("barcode_sources"):
        return ""
    return f"barcode read from the {' / '.join(album['barcode_sources'])} tag, not BARCODE"


def release_choices(results):
    """Every release scored, as a person would be offered it (engine.candidates_of): "against_folder"
    when the folder name rules it out, by the test that keeps it from being picked (match_core.classify).
    Beside "alts", which says what came close whatever it is: rivals are weighed on that."""
    out = []
    for res in results:
        for s in (res or {}).get("scored") or ():
            c = s["cand"]
            year = f" ({c['year']})" if c.get("year") else ""
            out.append({"source": c["source"], "id": str(c["id"]), "score": s["confidence"],
                        "title": f"{c['artist']} - {c['title']}{year}", "details": mc.release_details(c),
                        "against_folder": any(str(x).startswith("folder name says")
                                              for x in s.get("clues_against") or ())})
    return out


def note_barcode_sources(out, album):
    """Say where the barcode came from without making it a doubt: a shop's UPC is weighed like
    Picard's BARCODE but is often the download's code. The words go with the evidence and the
    notes, never into `why`, which is read as the reason an album is not identified."""
    words = barcode_source_note(album)
    if not words:
        return
    out["barcode_sources"] = list(album["barcode_sources"])
    if out.get("best"):
        out["best"] = dict(out["best"], notes=list(out["best"].get("notes") or []) + [words])


def to_row(o):
    b = o.get("best") or {}
    return {"Status": o["status"], "Confidence": b.get("confidence", ""),
            "Source": b.get("source", ""), "ID": b.get("id", ""), "URL": b.get("url", ""),
            "MatchedArtist": b.get("artist", ""), "MatchedTitle": b.get("title", ""),
            "MatchedYear": b.get("year", ""), "MatchedCountry": b.get("country", ""),
            "MatchedLabel": b.get("label", ""), "Margin": o.get("margin", ""),
            "Evidence": " | ".join(b.get("notes", [])), "WhyNotAuto": " | ".join(o.get("why", [])),
            "Alternatives": " || ".join(o.get("alts", [])),
            "TagArtist": o["tag_artist"], "TagAlbum": o["tag_title"], "TagYear": o["tag_year"],
            "Tracks": o["tracks"], "AlbumArtistFolder": o["artist_folder"],
            "AlbumFolder": o["album_folder"], "FullPath": o["path"],
            "BarcodeFromOCR": o.get("barcodes_from_ocr", ""),
            "CodesFromOCR": o.get("codes_from_ocr", ""),
            "LinkedDiscogsID": b.get("discogs_id", ""),
            "Notes": " | ".join(filter(None, [
                barcode_source_note(o),
                " | ".join(o.get("shop_notes") or []),
                " | ".join(o.get("gnudb_notes") or []),
                " | ".join(o.get("acoustid_notes") or []),
                ("duplicate copies ignored: " + ", ".join(o["dropped_formats"]))
                if o.get("dropped_formats") else "",
                (f"{len(o['dropped_tracks'])} duplicate track file(s) ignored, e.g. "
                 + ", ".join(o["dropped_tracks"][:3]))
                if o.get("dropped_tracks") else ""]))}


def export(db, out_dir):
    out_dir = Path(out_dir)
    rows = {"auto": [], "review": [], "nomatch": [], "moved": [], "excluded": []}
    for (blob,) in db.execute("SELECT result FROM results"):
        o = json.loads(blob)
        if o["status"] in ("ignored", "skipped"):
            r = to_row(o)
            r["Status"] = "Ignored" if o["status"] == "ignored" else "Skipped"
            rows["excluded"].append(r)
        elif o["status"] in rows:
            rows[o["status"]].append(to_row(o))
    # Discs MusicBrainz has no disc ID for: one link each to attach it (you check
    # the release on the MusicBrainz page and confirm; nothing is sent from here).
    subs = []
    for (blob,) in db.execute("SELECT result FROM results ORDER BY path"):
        o = json.loads(blob)
        b = o.get("best") or {}
        for did, toc, exact in o.get("discids") or []:
            if exact is not False:          # attached, or the lookup failed (unknown)
                continue
            n = toc.split()[1]
            subs.append({"Album": o["path"], "DiscID": did, "Tracks": n,
                         "MatchedRelease": (f"https://musicbrainz.org/release/{b['mb_id']}" if o["status"] == "auto"
                                            and b.get("mb_id") else ""),
                         "AttachLink": f"https://musicbrainz.org/cdtoc/attach?id={did}&tracks={n}"
                                       f"&toc={toc.replace(' ', '+')}"})
    with open(out_dir / "discid_submissions.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["Album", "DiscID", "Tracks", "MatchedRelease", "AttachLink"])
        w.writeheader()
        w.writerows(subs)
    for status, items in rows.items():
        key = (lambda r: -float(r["Confidence"] or 0))
        with open(out_dir / f"matches_{status}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLS)
            w.writeheader()
            w.writerows(sorted(items, key=key))
    return {k: len(v) for k, v in rows.items()}


def load_plugin(plugin_dir):
    d = Path(plugin_dir).expanduser()
    if not (d / "discogs_mapping.py").exists():
        sys.exit(f"discogs_mapping.py not found in {d}: reinstall TagMender, or name a copy with --plugin-dir")
    sys.path.insert(0, str(d))
    import discogs_mapping
    return discogs_mapping


def ocr_problems():
    """What OCR is missing to work (Pillow, pyzbar/zbar, tesseract), as lines saying how to
    install each; [] when it is ready."""
    import shutil
    problems = []
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        problems.append("Pillow missing        -> sudo apt install python3-pil")
    try:
        from pyzbar.pyzbar import decode  # noqa: F401
    except ImportError as exc:
        # pyzbar raises ImportError both when it is not installed and when it
        # is installed but cannot find the zbar library itself.
        if "zbar" in str(exc).lower() and "librar" in str(exc).lower():
            problems.append(f"zbar library missing ({exc}) -> sudo apt install libzbar0")
        else:
            problems.append("pyzbar missing        -> sudo apt install python3-pyzbar")
    except Exception as exc:
        problems.append(f"pyzbar failed to load ({exc}) -> sudo apt install libzbar0 python3-pyzbar")
    if not shutil.which("tesseract"):
        problems.append("tesseract binary missing -> sudo apt install tesseract-ocr")
    return problems


def check_ocr_ready():
    """Fail loudly if --ocr was asked for but cannot actually work."""
    problems = ocr_problems()
    if problems:
        sys.exit("--ocr was requested but OCR is not ready:\n  " + "\n  ".join(problems)
                 + "\n(Or in a venv made with --system-site-packages: pip install pillow pyzbar)")


def arg_parser():
    """The command line main() reads (a function of its own so tests can parse with it)."""
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audit_csv")
    ap.add_argument("--out-dir", default="~/Downloads/matching")
    ap.add_argument("--plugin-dir", default=DEFAULT_PLUGIN_DIR)
    ap.add_argument("--include-partial", action="store_true",
                    help="also look up partially tagged albums (never auto-applied)")
    ap.add_argument("--sample", type=int, help="random sample of N albums (seeded)")
    ap.add_argument("--limit", type=int, help="first N albums only")
    ap.add_argument("--artist", help="only artist folders containing this text")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--ocr", action="store_true", help="barcode + text OCR on scan images")
    ap.add_argument("--max-images", type=int, default=15,
                    help="most images to OCR per album, back/disc-named first "
                         "(default 15; 0 = no limit)")
    ap.add_argument("--no-investigate", dest="investigate", action="store_false",
                    help="do not re-read every scan in full and search the codes found on the web "
                         "when an album is still unsure (on by default)")
    ap.add_argument("--no-shops", dest="use_shops", action="store_false",
                    help="do not ask Deezer and iTunes what they sell under the album's barcode "
                         "when the barcode search found nothing sure (on by default)")
    ap.add_argument("--no-gnudb", dest="use_gnudb", action="store_false",
                    help="do not ask gnudb.org (freedb) what it holds for the disc's table of contents "
                         "when an album is still unsure (on by default; needs a registered e-mail address)")
    ap.add_argument("--acoustid", choices=ACOUSTID_MODES, default="unsure",
                    help="fingerprint (AcoustID): unsure - only albums not identified for sure (default); "
                         "always - every album, noting when the fingerprints disagree with a sure pick "
                         "(albums matched by earlier runs are checked too); off")
    ap.add_argument("--redo", action="store_true", help="ignore saved results")
    ap.add_argument("--retry-review", action="store_true",
                    help="re-evaluate albums previously filed as review (cheap: lookups "
                         "come from the cache)")
    ap.add_argument("--process-anyway", action="store_true",
                    help="ignore the Ignore/Skip rules for this run (exclusions.json, profiles)")
    ap.add_argument("--recheck-days", type=float,
                    help="look up again, with fresh data, the review/nomatch albums last checked more than "
                         "this many days ago (MusicBrainz and Discogs grow daily); e.g. 30")
    ap.add_argument("--retry-nomatch", action="store_true",
                    help="look up again the albums previously filed as nomatch "
                         "(use after a script fix); matched/review results are kept")
    ap.add_argument("--keep-old-results", action="store_true",
                    help="do not re-evaluate results made by an older version of the scripts")
    ap.add_argument("--config", help="JSON file overriding thresholds/weights")
    return ap


def main():
    args = arg_parser().parse_args()

    if args.ocr:
        check_ocr_ready()
    cfg = dict(mc.DEFAULT_CFG)
    picard_countries = picard_preferred_countries()
    if picard_countries:
        cfg["preferred_countries"] = picard_countries
    cfg.update(picard_release_preferences())
    try:                                           # rules learned from your library (learn_rules.py)
        cfg["artist_aliases"] = json.loads((exclusions.CONFIG_DIR / "rules.json")
                                           .read_text()).get("artist_aliases") or {}
    except (OSError, ValueError):
        pass
    if args.config:
        cfg.update(json.load(open(Path(args.config).expanduser())))
    for need in ("fpcalc", "acoustid"):
        if requirements.problem(need, "cli"):
            print(f"Note: {requirements.problem(need, 'cli')}", file=sys.stderr)
    if args.use_gnudb and gnudb_problem():
        print(f"Note: {gnudb_problem()}", file=sys.stderr)
    if not companions.discogs_on():
        sys.exit("This tool identifies with Discogs. The Discogs plugin is switched off (Preferences › Plugins, or: tagmender plugins enable discogs).")
    token = _discogs_token()
    if not token:
        sys.exit("This tool needs a Discogs token. " + requirements.notice("discogs", "cli", requirements.NOT_SET))

    dm = load_plugin(args.plugin_dir)
    global _PLUGIN_STAMP
    _PLUGIN_STAMP = _script_version(plugin_files(dm))[:6]
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    import fcntl
    lock = open(out_dir / "match.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit(f"Another match_albums.py is already working in {out_dir} "
                 "(is auto_ambient.sh running?). Stop it first, or use a different --out-dir.")
    db = sqlite3.connect(out_dir / "match_state.db")
    db.execute("CREATE TABLE IF NOT EXISTS results (path TEXT PRIMARY KEY, result TEXT)")
    if args.redo:
        db.execute("DELETE FROM results")
    else:
        pass  # --retry-* are handled below without deleting anything
    try:
        who = check_discogs_token(token)
    except AuthError as exc:
        sys.exit(f"\n{exc}")
    if who:
        print(f"Discogs token OK (user: {who})")
    # Earlier versions saved failed lookups as final results; drop them so
    # those albums are looked up again.
    stale = [p for (p, b) in db.execute("SELECT path, result FROM results")
             if json.loads(b).get("status") == "error"
             or any(str(w).startswith("error:") for w in json.loads(b).get("why", []))]
    for p in stale:
        db.execute("DELETE FROM results WHERE path=?", (p,))
    db.commit()
    if stale:
        print(f"Re-queued {len(stale)} albums whose earlier lookup had failed")
    # Albums to decide again. Their old results are NOT deleted first: they stay
    # in the CSVs until the new decision replaces them, so an interrupted run
    # never leaves the review/nomatch lists half empty.
    #   - results from an older version of the scripts (including auto matches),
    #     unless --keep-old-results; lookups and OCR come from the caches;
    #   - with --retry-nomatch / --retry-review, results with that status.
    redo = {}
    current_rules = exclusions.rules_hash(exclusions.load_rules())
    retry = {s for s, on in (("nomatch", args.retry_nomatch), ("review", args.retry_review)) if on}
    for (p, b) in db.execute("SELECT path, result FROM results"):
        o = json.loads(b)
        if not args.keep_old_results and o.get("version") != result_version():
            redo[p] = o["status"]
        elif o["status"] in retry:
            redo[p] = o["status"]
        elif acoustid_unchecked(o, args.acoustid):
            redo[p] = o["status"]                    # matched before the fingerprint check: check it
        elif (args.recheck_days and o["status"] in ("review", "nomatch")
              and time.time() - float(o.get("checked") or 0) > args.recheck_days * 86400):
            redo[p] = o["status"]
        elif o["status"] == "moved" and Path(p).is_dir():
            redo[p] = o["status"]                    # the folder is back: match it
        elif o["status"] == "skipped" or args.process_anyway:
            redo[p] = o["status"]                    # a skip lasts one job only
        elif o["status"] == "ignored" and o.get("rules_hash") != current_rules:
            redo[p] = o["status"]                    # the rules changed: does it still apply?
    if redo:
        print(f"Re-evaluating {len(redo)} albums ("
              + ", ".join(f"{n} {s}" for s, n in sorted(Counter(redo.values()).items())) + ")")
    http = share_pace(Http(db, cfg, token))
    if args.recheck_days:
        http.max_age = args.recheck_days * 86400        # older lookups are fetched again

    wanted = {"untagged"} | ({"partial"} if args.include_partial else set())
    rows = [r for r in csv.DictReader(open(Path(args.audit_csv).expanduser(), encoding="utf-8"))
            if r["Status"] in wanted]
    if args.artist:
        rows = [r for r in rows if args.artist.lower() in r["AlbumArtistFolder"].lower()]
    if args.sample:
        random.Random(args.seed).shuffle(rows)
        rows = rows[:args.sample]
    if args.limit:
        rows = rows[:args.limit]

    # The loop runner stops a round with SIGTERM when the scripts change, so
    # the next round uses the fix right away. Treat it like Ctrl+C: the album
    # in progress is simply redone next time; finished ones are already saved.
    import signal

    def _stop(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _stop)

    done = {p for (p,) in db.execute("SELECT path FROM results")} - set(redo)
    todo = [r for r in rows if r["FullPath"] not in done]
    # Re-evaluations first (nomatch, then review, then auto), new albums after,
    # so the effect of a fix shows up in the CSVs quickly.
    order = {"nomatch": 0, "review": 1, "auto": 2}
    todo.sort(key=lambda r: order.get(redo.get(r["FullPath"]), 3))
    print(f"{len(rows)} albums selected, {len(rows) - len(todo)} already done, {len(todo)} to go")
    t0 = time.time()
    try:
        for i, row in enumerate(todo, 1):
            try:
                o = process(row, http, cfg, dm, args)
            except KeyboardInterrupt:
                raise
            except AuthError as exc:
                print(f"\nSTOPPED: {exc}\nFix the token and run the same command again; "
                      "finished albums are kept.")
                break
            except Exception as exc:
                # Not filed: it will be retried on the next run.
                print(f"[{i}/{len(todo)}] ERROR (will retry next run) "
                      f"{row['AlbumArtistFolder']} | {row['AlbumFolder']}: "
                      f"{type(exc).__name__}: {exc}")
                continue
            if o["status"] == "error":
                print(f"[{i}/{len(todo)}] LOOKUP FAILED (will retry next run) "
                      f"{o['artist_folder']} | {o['album_folder']}: {'; '.join(o['why'])}")
                continue
            db.execute("INSERT OR REPLACE INTO results VALUES (?,?)",
                       (o["path"], json.dumps(o, default=list)))
            db.commit()
            b = o.get("best") or {}
            print(f"[{i}/{len(todo)}] {o['status']:7s} {str(b.get('confidence', '')):>5} "
                  f"{o['artist_folder']} | {o['album_folder']}"
                  f"{'  <- ' + b['source'] + ':' + b['id'] if b and o['status'] == 'auto' else ''}")
            if i % 25 == 0:
                export(db, out_dir)
    except KeyboardInterrupt:
        print("\nInterrupted - progress is saved; run the same command to resume.")
    counts = export(db, out_dir)
    print(f"\nTotals so far: {counts}\nFiles in {out_dir}: matches_auto/review/nomatch/moved.csv "
          f"({(time.time() - t0) / 60:.1f} min this run)")


if __name__ == "__main__":
    main()
