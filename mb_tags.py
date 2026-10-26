"""mb_tags.py - the tags of a MusicBrainz release, the way Picard writes them, without Picard.

release_tags(rel, settings=None) -> [{field: [values]}, ...]   one entry per audio track, in
the order the matcher scored them (match_albums.mb_candidate): video media (DVD, Blu-ray, ...)
and video recordings of enhanced CDs are left out, so entry n belongs to the album's n-th
audio file.

The release is fetched with inc_for(settings) (Picard's own includes, picard/album.py).

What is written follows Picard 3.0.2 (picard/mbjson.py, picard/album.py), and the settings
are Picard's own, read from Picard.ini (picard_config.tag_settings):
  - artist names as credited, or standardized: "none", "variations" (Picard 3's default: the
    artist's name unless the credit is one of the artist's former names) or "all"; sort
    names from the alias that matches the credited name; translated artist names
    (translation_locales, translate_from_sortname), album and track titles;
  - release, release-group and URL relationships (release_ars, on by default): producer,
    engineer, mixer, composer ... credited on the release, license, ASIN; track and work
    relationships (track_ars): performer:<instrument>, composer, lyricist, writer, arranger,
    conductor, remixer, work, MusicBrainz work ID, the works' languages, with standardized
    or credited instrument and vocal names (standardize_instruments, standardize_vocals);
  - the Various Artists name (va_name) and compilation=1 on Various Artists releases;
    Unicode punctuation as ASCII (convert_punctuation); the release country from the
    preferred release countries;
  - the track title (not the recording's), sort names, release type and status in lower
    case, release-group first release date as originaldate, label and catalogue number per
    label, the text representation's script, every MusicBrainz ID.
Picard's hidden variables (~...) are not written. Translation script exceptions (a setting
Picard keeps in a form TagMender cannot read) are not applied.

Parts are ports of Picard's code (GPL-2.0-or-later), credited where they are.
"""
from __future__ import annotations

import re
import unicodedata

VIDEO_FORMATS = ("dvd-video", "blu-ray", "dvd", "vhs", "hd-dvd", "vcd", "svcd")
VARIOUS_ARTISTS_ID = "89ad4ac3-39f7-470e-963a-56509c546377"
ALIAS_TYPE_ARTIST_NAME_ID = "894afba6-2816-3c24-8072-eadb66bd04bc"
ALIAS_TYPE_LEGAL_NAME_ID = "d4dcd0c0-b341-3612-a332-c0ce797b25cf"
ALIAS_TYPE_RECORDING_NAME_ID = "5d564c8f-97de-3572-94bb-7f40ad661499"

BASE_INC = ("aliases", "artist-credits", "artists", "isrcs", "labels", "media", "recordings",
            "release-groups", "url-rels")
RELEASE_INC = ("artist-rels", "recording-rels", "label-rels", "release-group-level-rels", "release-rels",
               "series-rels", "url-rels", "work-rels")
TRACK_INC = ("recording-level-rels", "work-level-rels")
INC = "+".join(BASE_INC)          # (without relationships)


def inc_for(settings: dict | None = None) -> str:
    """The release's includes, as Picard asks for them with these settings."""
    s = _settings(settings)
    inc = list(BASE_INC)
    if s["use_genres"] and not s["only_my_genres"]:
        inc += ["tags", "genres"]           # (your own genres need a MusicBrainz login: not asked for)
    if s["release_ars"] or s["track_ars"]:
        inc += [x for x in RELEASE_INC if x not in inc]
        if s["track_ars"]:
            inc += list(TRACK_INC)
    return "+".join(inc)


def _settings(settings):
    if settings is None:
        import picard_config
        return picard_config.tag_settings()
    import picard_config
    out = dict(picard_config.TAG_DEFAULTS)
    out.update(settings)
    return out


# ---- names: standardize, translate, sort (picard/mbjson.py) ----------------------------
def _linear(parts):
    total = sum(w for _v, w in parts)
    return sum(v * w for v, w in parts) / total if total else 0.0


def _locales_from_aliases(aliases):
    locales = {}
    for alias in aliases or []:
        if not alias.get("primary"):
            continue
        full = alias.get("locale")
        if not full:
            continue
        root = full.split("_")[0]
        full_parts, root_parts = [(0.8, 5)], [(0.4 if "_" in full else 0.8, 5)]
        tid = alias.get("type-id")
        score = 0.8 if tid in (ALIAS_TYPE_ARTIST_NAME_ID, ALIAS_TYPE_RECORDING_NAME_ID) else \
            0.5 if tid == ALIAS_TYPE_LEGAL_NAME_ID else 0.0
        full_parts.append((score, 5))
        root_parts.append((score, 5))
        for loc, parts in ((full, full_parts), (root, root_parts)):
            comb = _linear(parts)
            if loc not in locales or comb > locales[loc][0]:
                locales[loc] = (comb, (alias.get("name") or "", alias.get("sort-name") or ""))
    return locales


def _localized_alias(aliases, preferred):
    if not aliases:
        return None
    locales = _locales_from_aliases(aliases)
    for loc in preferred:
        if loc in locales:
            return locales[loc][1]
    for loc in (x.split("_")[0] for x in preferred if "_" in x):
        if loc in locales:
            return locales[loc][1]
    return None


def _reverse_sortname(sortname: str) -> str:
    chunks = [a.strip() for a in sortname.split(",")]
    if len(chunks) == 2:
        return "%s %s" % (chunks[1], chunks[0])
    if len(chunks) == 3:
        return "%s %s %s" % (chunks[2], chunks[1], chunks[0])
    if len(chunks) == 4:
        return "%s %s, %s %s" % (chunks[1], chunks[0], chunks[3], chunks[2])
    return sortname.strip()


def translate_from_sortname(name: str, sortname: str) -> str:
    """'Translate' a name in a non-Latin script by reversing its (Latin) sort name."""
    for c in name:
        if unicodedata.category(c)[0] == "L" and "LATIN" not in unicodedata.name(c, ""):
            for sep in (" & ", "; ", " and ", " vs. ", " with ", " y "):
                if sep in sortname:
                    return sep.join(map(_reverse_sortname, sortname.split(sep)))
            return _reverse_sortname(sortname)
    return name


def _translate_artist(artist: dict, s) -> tuple[str, str]:
    """(name, sort name) of the artist, translated when that is asked for."""
    name, sort = artist.get("name") or "", artist.get("sort-name") or ""
    if s["translate_artist_names"]:
        alias = _localized_alias(artist.get("aliases"), s["translation_locales"])
        if alias:
            return alias
        if s["translate_from_sortname"]:
            return translate_from_sortname(name, sort), sort
    return name, sort


def should_standardize(mode: str, credited: str, artist: dict) -> bool:
    """Whether the artist's own name replaces the credited one: never ("none"), always
    ("all"), or ("variations") unless the credit is one of the artist's ended names - a
    name change, not a spelling variation."""
    if mode == "none":
        return False
    if mode == "all":
        return True
    for alias in artist.get("aliases") or []:
        if alias.get("type-id", None) in (ALIAS_TYPE_ARTIST_NAME_ID, None) and alias.get("ended", False) \
                and credited in (alias.get("name"), alias.get("sort-name")):
            return False
    return True


def _sort_from_aliases(artist: dict, name: str, s) -> str:
    """The sort name of the alias matching the credited name, else the artist's own."""
    cands = [a for a in artist.get("aliases") or [] if a.get("name") == name and a.get("sort-name")]
    if cands:
        if s["translate_artist_names"]:
            alias = _localized_alias(cands, s["translation_locales"])
            if alias:
                return alias[1]
        return cands[0]["sort-name"]
    return artist.get("sort-name") or name


def credit(ac, settings=None) -> tuple[str, str, list, list]:
    """(artist phrase, sort phrase, names, ids) of an artist-credit list, as Picard builds
    them (artist_credit_from_node)."""
    c = credit_info(ac, settings)
    return c["name"], c["sort"], c["names"], c["ids"]


def credit_info(ac, settings=None) -> dict:
    """Picard's ArtistCreditInfo of an artist-credit list: name, sort, names, sort_names,
    countries ("XX" unknown), comments (disambiguations) - and the IDs."""
    s = _settings(settings)
    phrase, sort, names, ids, sorts, countries, comments = [], [], [], [], [], [], []
    for c in ac or []:
        if not isinstance(c, dict):
            continue
        a = c.get("artist") or {}
        credited = c.get("name") or a.get("name") or ""
        use_credited = not should_standardize(s["standardize_artist_names"], credited, a) if a.get("name") \
            else True
        tname, tsort = _translate_artist(a, s) if a.get("name") else (credited, a.get("sort-name") or credited)
        translated = bool(a.get("name")) and tname != a.get("name")
        if not translated and use_credited and c.get("name"):
            name, sname = credited, _sort_from_aliases(a, credited, s) if a.get("name") else credited
        else:
            name, sname = tname, tsort
        jp = c.get("joinphrase") or ""
        phrase.append(name + jp)
        sort.append((sname or "") + jp)
        multi = s["always_standardize_multivalue_artist"] and a.get("name")
        names.append(tname if multi else name)
        sorts.append((tsort if multi else sname) or "")
        if a.get("id"):
            ids.append(a["id"])
            countries.append(a.get("country") or "XX")
            comments.append(a.get("disambiguation", ""))
    return {"name": "".join(phrase), "sort": "".join(sort), "names": names, "sort_names": sorts, "ids": ids,
            "countries": countries, "comments": comments}


def _credit_tags(ac, s, release: bool) -> dict:
    """artist_credit_to_metadata: the tags (and Picard's hidden variables) of a credit."""
    c = credit_info(ac, s)
    p = "~albumartists" if release else "~artists"
    one = c["comments"][0] if len(c["comments"]) == 1 else ""
    if release:
        out = {"musicbrainz_albumartistid": c["ids"], "albumartist": [c["name"]], "albumartists": c["names"],
               "albumartistsort": [c["sort"]], "~albumartists": c["names"], "~albumartistcomment": [one]}
    else:
        out = {"musicbrainz_artistid": c["ids"], "artist": [c["name"]], "artistsort": [c["sort"]],
               "artists": c["names"], "~artistcomment": [one]}
    out.update({p + "_sort": c["sort_names"], p + "_countries": c["countries"], p + "_comments": c["comments"]})
    return out


# ---- relationships (picard/mbjson.py _relations_to_metadata) --------------------------
ARTIST_REL_TYPES = {
    "arranger": "arranger", "audio": "engineer", "chorus master": "performer:chorus master",
    "composer": "composer", "concertmaster": "performer:concertmaster", "conductor": "conductor",
    "engineer": "engineer", "instrument arranger": "arranger", "librettist": "lyricist",
    "live sound": "engineer", "lyricist": "lyricist", "mix-DJ": "djmixer", "mix": "mixer",
    "orchestrator": "arranger", "performing orchestra": "performer:orchestra", "producer": "producer",
    "remixer": "remixer", "sound": "engineer", "audio director": "director", "video director": "director",
    "vocal arranger": "arranger", "writer": "writer",
}
_PREFIX_ATTRS = {"guest", "additional", "minor", "solo"}
_BLANK_SPECIAL = {"instrument": "instruments", "vocal": "vocals"}


def _add(m: dict, key: str, value):
    if value in (None, ""):
        return
    vals = m.setdefault(key, [])
    if value not in vals:
        vals.append(value)


def _parse_attributes(attrs, reltype, attr_credits) -> str:
    prefixes, nouns = [], []
    for attr in attrs:
        attr = attr_credits.get(attr, attr)
        (prefixes if attr in _PREFIX_ATTRS else nouns).append(attr)
    if len(nouns) > 1:
        result = "%s and %s" % (", ".join(nouns[:-1]), nouns[-1])
    elif nouns:
        result = nouns[0]
    else:
        result = _BLANK_SPECIAL.get(reltype, "")
    return " ".join([" ".join(prefixes), result]).strip()


def _rel_artist(rel, m, s, instrumental, per_medium):
    artist = rel.get("artist") or {}
    if not artist.get("name"):
        return
    name, sort = _translate_artist(artist, s)
    if name == artist.get("name") and rel.get("target-credit"):
        credited = rel["target-credit"]
        if credited != artist["name"] and not should_standardize(s["standardize_artist_names"], credited, artist):
            name, sort = credited, _sort_from_aliases(artist, credited, s)
    reltype = rel.get("type") or ""
    attrs = tuple(rel.get("attributes") or ())
    if reltype in ("vocal", "instrument", "performer"):
        credits = rel.get("attribute-credits") or {} if (
            (reltype == "instrument" and not s["standardize_instruments"]) or
            (reltype == "vocal" and not s["standardize_vocals"])) else {}
        key = ("performer:" + _parse_attributes(attrs, reltype, credits)).rstrip(":")   # (Picard's "performer:" is "performer")
    elif reltype == "mix-DJ" and attrs:
        for attr in attrs:
            if attr.startswith("medium"):
                try:
                    _add(per_medium.setdefault(int(attr.split()[1]), {}), "djmixer", name)
                except (ValueError, IndexError):
                    pass
        return
    elif reltype in ARTIST_REL_TYPES:
        key = ARTIST_REL_TYPES[reltype]
    else:
        return
    if instrumental and key == "lyricist":
        return
    _add(m, key, name)
    if key == "composer":
        _add(m, "composersort", sort)
        _add(m, "musicbrainz_composerid", artist.get("id"))
    elif key == "lyricist":
        _add(m, "~lyricistsort", sort)
    elif key == "writer":
        _add(m, "~writersort", sort)


def _work(work, m, s, instrumental, per_medium):
    _add(m, "musicbrainz_workid", work.get("id"))
    if instrumental:
        _add(m, "language", "zxx")
    elif work.get("languages"):
        for lang in work["languages"]:
            _add(m, "language", lang)
    elif work.get("language"):
        _add(m, "language", work["language"])
    if work.get("title"):
        _add(m, "work", work["title"])
    if "disambiguation" in work:
        _add(m, "~workcomment", work["disambiguation"])
    if work.get("relations"):
        relations(work["relations"], m, s, instrumental, per_medium, entity="work")
    for iswc in work.get("iswcs") or []:
        if iswc:
            m.setdefault("~iswc", []).append(iswc)


_AMAZON = re.compile(r"^https?://(?:www.)?(?P<host>.*?)(?::[0-9]+)?/.*/(?P<asin>[0-9B][0-9A-Z]{9})(?:[^0-9A-Z]|$)")


def relations(rels, m: dict, settings=None, instrumental=False, per_medium=None, entity=None) -> dict:
    """Add the tags of a relationship list to m. Returns {medium number: tags} for credits
    on one medium only (a DJ mix of disc 2)."""
    s = _settings(settings)
    per_medium = {} if per_medium is None else per_medium
    series_cleared = False
    for rel in rels or []:
        target = rel.get("target-type")
        if target == "artist":
            _rel_artist(rel, m, s, instrumental, per_medium)
        elif target == "work" and rel.get("type") == "performance" and rel.get("work"):
            attrs = tuple(rel.get("attributes") or ())
            for a in attrs:
                _add(m, "~performance_attributes", a)
            _work(rel["work"], m, s, "instrumental" in attrs, per_medium)
        elif target == "series" and rel.get("type") == "part of" and rel.get("series"):
            pre = f"~{entity}_" if entity else "~"
            if not series_cleared:            # (once per list: picard's metadata_was_cleared)
                for x in ("series", "seriesid", "seriescomment", "seriesnumber"):
                    m.pop(pre + x, None)
                series_cleared = True
            sr = rel["series"]
            for x, v in (("series", sr.get("name")), ("seriesid", sr.get("id")),
                         ("seriescomment", sr.get("disambiguation")),
                         ("seriesnumber", (rel.get("attribute-values") or {}).get("number", ""))):
                if v or v == 0:
                    m.setdefault(pre + x, []).append(str(v))
        elif target == "label" and rel.get("type") == "broadcast" and "begin" in rel:
            m["~broadcast_date"] = [rel["begin"]]
        elif target == "url":
            res = (rel.get("url") or {}).get("resource") or ""
            if rel.get("type") == "amazon asin" and "asin" not in m:
                hit = _AMAZON.match(res)
                if hit:
                    m["asin"] = [hit.group("asin")]
            elif rel.get("type") == "license" and res:
                m.setdefault("license", []).append(res)
    return per_medium


# ---- the release ----------------------------------------------------------------------
def _asciipunct(v: str) -> str:
    import picard_text
    return picard_text.asciipunct(v)


def album_tags(rel, settings=None, per_medium=None) -> dict:
    s = _settings(settings)
    t = {}
    rg = rel.get("release-group") or {}
    # (release_group_to_metadata, then release_to_metadata: picard/album.py's order)
    if rg.get("id"):
        t["musicbrainz_releasegroupid"] = [rg["id"]]
    for k, name in (("disambiguation", "~releasegroupcomment"), ("first-release-date", "~releasegroup_firstreleasedate"),
                    ("title", "~releasegroup")):
        if rg.get(k):
            t[name] = [rg[k]]
    if rg.get("primary-type"):
        t["~primaryreleasetype"] = [str(rg["primary-type"]).lower()]
    for x in rg.get("secondary-types") or []:
        _add(t, "~secondaryreleasetype", str(x).lower())
    t["album"] = [rel.get("title", "")]
    if s["translate_album_titles"]:
        alias = _localized_alias(rel.get("aliases"), s["translation_locales"])
        if alias:
            t["album"] = [alias[0]]
    ids = []
    if rel.get("artist-credit"):
        ct = _credit_tags(rel["artist-credit"], s, release=True)
        ids = ct["musicbrainz_albumartistid"]
        t.update(ct)
    t["musicbrainz_albumid"] = [rel["id"]]
    for k, name in (("annotation", "~releaseannotation"), ("disambiguation", "~releasecomment")):
        if rel.get(k):
            t[name] = [rel[k]]
    if rel.get("date"):
        t["date"] = [rel["date"]]
    if rg.get("first-release-date"):
        t["originaldate"] = [rg["first-release-date"]]
        t["originalyear"] = [rg["first-release-date"][:4]]
    countries = sorted(c for c in ((((ev.get("area") or {}).get("iso-3166-1-codes") or [None])[0])
                                   for ev in rel.get("release-events") or [] if isinstance(ev, dict)) if c)
    t["~releasecountries"] = countries
    pick = next((c for c in s["preferred_release_countries"] if c in countries), None)
    if pick or rel.get("country"):
        t["releasecountry"] = [pick or rel["country"]]
    if rel.get("status"):
        t["releasestatus"] = [str(rel["status"]).lower()]
    types = ([rg["primary-type"]] if rg.get("primary-type") else []) + list(rg.get("secondary-types") or [])
    if types:
        t["releasetype"] = list(dict.fromkeys(x.lower() for x in types))
    if rel.get("barcode"):
        t["barcode"] = [rel["barcode"]]
    if rel.get("asin"):
        t["asin"] = [rel["asin"]]
    labels = [li for li in rel.get("label-info") or [] if li]
    lnames = list(dict.fromkeys((li.get("label") or {}).get("name") for li in labels if (li.get("label") or {}).get("name")))
    catnos = list(dict.fromkeys(li["catalog-number"] for li in labels if li.get("catalog-number")))
    if lnames:
        t["label"] = lnames
    if catnos:
        t["catalognumber"] = catnos
    tr = rel.get("text-representation") or {}
    if tr.get("language"):
        t["~releaselanguage"] = [tr["language"]]
    if tr.get("script"):
        t["script"] = [tr["script"]]
    if s["release_ars"]:
        # the release group's first, then the release's (picard/album.py reads the release group
        # first); credits on one medium only (a DJ mix of disc 2) come from the release
        per_medium = {} if per_medium is None else per_medium
        relations(rg.get("relations"), t, s, per_medium={}, entity="releasegroup")
        relations(rel.get("relations"), t, s, per_medium=per_medium, entity="release")
    if VARIOUS_ARTISTS_ID in ids[:1] and len(ids) == 1:
        t["albumartist"] = t["albumartistsort"] = [s["va_name"]]
    return _kept(t)


def _kept(m: dict) -> dict:
    """As Picard's Metadata keeps values: a single empty value is no value; several are kept."""
    return {k: v for k, v in m.items() if v and (len(v) > 1 or v[0] not in ("", None))}


def format_time(ms) -> str:
    """picard/util format_time: "m:ss", or "h:mm:ss" from an hour."""
    secs = round(float(ms) / 1000)
    if secs < 3600:
        return "%d:%02d" % divmod(secs, 60)
    h, rest = divmod(secs, 3600)
    return "%d:%02d:%02d" % ((h,) + divmod(rest, 60))


def recording_tags(rec: dict, settings=None, title=None) -> dict:
    """The tags of a recording (picard/mbjson.py recording_to_metadata): its artist credit,
    relationships (with track_ars), ISRCs and title - the track's title (title) when given."""
    s = _settings(settings)
    m = {}
    if rec.get("id"):
        m["musicbrainz_recordingid"] = [rec["id"]]
    for k, name in (("disambiguation", "~recordingcomment"), ("first-release-date", "~recording_firstreleasedate")):
        if rec.get(k):
            m[name] = [rec[k]]
    if rec.get("artist-credit"):
        m.update(_credit_tags(rec["artist-credit"], s, release=False))
    if rec.get("relations"):
        relations(rec["relations"], m, s, entity="recording")
    if rec.get("isrcs"):
        m["isrc"] = list(rec["isrcs"])
    if rec.get("video"):
        m["~video"] = ["1"]
    rtitle = rec.get("title")
    if s["translate_track_titles"]:
        alias = _localized_alias(rec.get("aliases"), s["translation_locales"])
        if alias:
            rtitle = alias[0]
            title = alias[0] if title else title
    m["title"] = [title or rtitle or ""]
    if rtitle:
        m["~recordingtitle"] = [rtitle]
    if rec.get("length"):
        m["~length"] = [format_time(rec["length"])]
    return _kept(m)


def track_tags(t: dict, settings=None) -> dict:
    """The tags of a release's track (picard/mbjson.py track_to_metadata): its recording's,
    then the track's own title, position, ID and artist credit."""
    s = _settings(settings)
    m = recording_tags(t.get("recording") or {}, s, t.get("title"))
    if t.get("id"):
        m["musicbrainz_trackid"] = [t["id"]]
    if t.get("number") or t.get("number") == 0:
        m["~musicbrainz_tracknumber"] = [str(t["number"])]
    if t.get("position") is not None and t.get("position") != "":
        m["tracknumber"] = [str(t["position"])]
    if t.get("length"):
        m["~length"] = [format_time(t["length"])]
    if t.get("artist-credit"):
        m.update(_credit_tags(t["artist-credit"], s, release=False))
    return _kept(m)


def _artist_nodes(ac) -> dict:
    return {c["artist"]["id"]: c["artist"] for c in ac or []
            if isinstance(c, dict) and isinstance(c.get("artist"), dict) and c["artist"].get("id")}


def _with_nodes(ac, nodes: dict):
    """The artist credit with its artists' nodes taken from nodes where the IDs match."""
    if not ac or not nodes:
        return ac
    out = []
    for c in ac:
        if isinstance(c, dict) and isinstance(c.get("artist"), dict) and c["artist"].get("id") in nodes:
            c = dict(c, artist=nodes[c["artist"]["id"]])
        out.append(c)
    return out


# ---- genres (picard/track.py _convert_folksonomy_tags_to_genre) -----------------------
def _counts(node, s):
    from collections import Counter
    c = Counter()
    for g in (node or {}).get("tags" if s["folksonomy_tags"] else "genres") or []:
        if isinstance(g, dict) and g.get("name"):
            c[g["name"]] += int(g.get("count") or 0)
    return c


def genres(track_nodes, rel, s, artist_nodes=()) -> list:
    """The GENRE values Picard writes for one track: the recording's, the release's and the
    release group's genres (or folksonomy tags) together - else, with "use album artist
    genres", the artists' - filtered, at least min_genre_usage % of the top one, the
    max_genres most used, title-cased and sorted (joined when asked)."""
    import picard_util
    if not s["use_genres"] or s["only_my_genres"]:
        return []
    c = _counts(track_nodes, s) + _counts(rel, s) + _counts(rel.get("release-group"), s)
    if not c and s["artists_genres"]:
        for a in artist_nodes:
            c += _counts(a, s)
    limit = s["max_genres"]
    if limit is not None and limit < 1:
        return []
    c = +c
    if not c:
        return []
    c = picard_util.TagGenreFilter(s["genres_filter"] or "").filter(c, minusage=s["min_genre_usage"])
    top = sorted(c.items(), key=lambda x: (-x[1], x[0]))[:limit]
    out = sorted(picard_util.titlecase(name) for name, _n in top)
    return [s["join_genres"].join(out)] if s["join_genres"] and out else out


def audio_media(rel):
    return [m for m in rel.get("media") or [] if str(m.get("format") or "").lower() not in VIDEO_FORMATS]


_TRACK_SETS = {"title", "artist", "artistsort", "artists", "musicbrainz_artistid", "tracknumber",
               "musicbrainz_recordingid", "musicbrainz_trackid", "isrc", "~artists_sort", "~artists_countries",
               "~artists_comments", "~artistcomment", "~recordingcomment", "~recording_firstreleasedate",
               "~recordingtitle", "~video", "~length", "~musicbrainz_tracknumber"}
DATA_TRACK_TITLE, SILENCE_TRACK_TITLE = "[data track]", "[silence]"


def release_tags(rel, settings=None) -> list[dict]:
    s = _settings(settings)
    per_medium = {}
    common = album_tags(rel, s, per_medium)
    va = common.get("musicbrainz_albumartistid") == [VARIOUS_ARTISTS_ID]
    # the release's artist nodes carry what the tracks' may not (their aliases): copied to
    # wherever the artist IDs match, as Picard does (album.py _copy_artist_nodes)
    release_artists = _artist_nodes(rel.get("artist-credit"))
    # numbered as Picard numbers them: the medium's own position among all media (a CD + DVD +
    # CD release's second CD is disc 3 of 3), its own track count, every track counted in
    # %_absolutetracknumber% - only the audio tracks are written (the n-th entry is the
    # album's n-th audio file)
    all_media = rel.get("media") or []
    total_discs = len(all_media) or 1
    audio = {id(m) for m in audio_media(rel)}
    absolute, out, artists = 0, [], set()
    for disc, m in enumerate(all_media, 1):
        loaded = ([("pregap", m["pregap"])] if m.get("pregap") else []) + \
            [("track", t) for t in m.get("tracks") or []] + [("data", t) for t in m.get("data-tracks") or []]
        for kind, t in loaded:
            absolute += 1
            rec = t.get("recording") or {}
            t_ac = _with_nodes(t.get("artist-credit"), release_artists)
            r_ac = _with_nodes(_with_nodes(rec.get("artist-credit"), release_artists), _artist_nodes(t_ac))
            tt = track_tags(dict(t, **{"artist-credit": t_ac, "recording": dict(rec, **{"artist-credit": r_ac})}), s)
            # (Picard counts the artist as Track._customize_metadata leaves it: the Various
            # Artists name, punctuation converted)
            who = (tt.get("artist") or common.get("albumartist") or [""])[0]
            if (tt.get("musicbrainz_artistid") or []) == [VARIOUS_ARTISTS_ID]:
                who = s["va_name"]
            artists.add(_asciipunct(who) if s["convert_punctuation"] and isinstance(who, str) else who)
            if kind != "track" or id(m) not in audio or rec.get("video"):
                continue                      # (a pregap, data or video track: no audio file of the album's)
            d = {k: list(v) for k, v in common.items()}
            for k, v in (per_medium.get(m.get("position") or disc) or {}).items():
                for x in v:
                    _add(d, k, x)
            for k, v in tt.items():
                if k in _TRACK_SETS:
                    d[k] = v
                else:
                    for x in v:
                        _add(d, k, x)
            d.setdefault("artist", common.get("albumartist", [""]))
            d.setdefault("artistsort", common.get("albumartistsort", [""]))
            d.setdefault("artists", common.get("albumartists", []))
            d.setdefault("musicbrainz_artistid", common.get("musicbrainz_albumartistid", []))
            if not d.get("tracknumber"):
                d["tracknumber"] = [str(len([x for x in out if x["discnumber"] == [str(m.get("position") or disc)]]) + 1)]
            count = m.get("track-count")
            count = (int(count) + (1 if m.get("pregap") else 0)) if isinstance(count, int) else \
                len(m.get("tracks") or [])
            d.update({"totaltracks": [str(count)], "discnumber": [str(m.get("position") or disc)],
                      "totaldiscs": [str(total_discs)], "~absolutetracknumber": [str(absolute)]})
            if m.get("pregap"):
                d["~discpregap"] = ["1"]
            if s["use_genres"]:
                who = [c["artist"] for c in (t_ac or r_ac or []) if isinstance(c, dict) and c.get("artist")] \
                    if va else list(release_artists.values())
                g = genres(rec, rel, s, who)
                if g:
                    d["genre"] = g
            if d["musicbrainz_artistid"] == [VARIOUS_ARTISTS_ID]:
                d["artist"] = d["artistsort"] = [s["va_name"]]
            if (d.get("title") or [""])[0] == DATA_TRACK_TITLE:
                d["~datatrack"] = ["1"]
            if (d.get("title") or [""])[0] == SILENCE_TRACK_TITLE:
                d["~silence"] = ["1"]
            if va:
                d["compilation"] = ["1"]
            else:
                d["~remove"] = ["compilation"]          # (Picard deletes it on any other release)
            if m.get("format"):
                d["media"] = [m["format"]]
            if m.get("title"):
                d["discsubtitle"] = [m["title"]]
            out.append(d)
    for d in out:
        d["~totalalbumtracks"] = [str(absolute)]
        if len(artists) > 1:
            d["~multiartist"] = ["1"]
    if s["convert_punctuation"]:
        out = [{k: [_asciipunct(x) if isinstance(x, str) and not k.startswith(("musicbrainz_", "~remove")) else x
                    for x in v] for k, v in d.items()} for d in out]
    return [_kept(d) for d in out]
