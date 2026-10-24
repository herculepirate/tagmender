"""mb_tags.py - the tags of a MusicBrainz release, the way Picard writes them, without Picard.

release_tags(rel) -> [{field: [values]}, ...]   one entry per audio track, in the order the
matcher scored them (match_albums.mb_candidate): video media (DVD, Blu-ray, ...) and
video recordings of enhanced CDs are left out, so entry n belongs to the album's n-th
audio file.

The release must be fetched with
  ?inc=recordings+artist-credits+labels+release-groups+media+isrcs+url-rels

Field names and values follow Picard's defaults (picard/mbjson.py): the track title (not
the recording's), credited artist names with their join phrases, sort names, release
type and status in lower case, release-group first release date as originaldate, label
and catalogue number per label, the text representation's script and language, and
every MusicBrainz ID. Standardised artist names, translations and the "Classical
Extras"-style work tags are Picard options/plugins TagMender does not reproduce.
"""
from __future__ import annotations

VIDEO_FORMATS = ("dvd-video", "blu-ray", "dvd", "vhs", "hd-dvd", "vcd", "svcd")
INC = "recordings+artist-credits+labels+release-groups+media+isrcs+url-rels"


def credit(ac) -> tuple[str, str, list, list]:
    """(credited phrase, sort phrase, names, ids) of an artist-credit list."""
    phrase, sort, names, ids = [], [], [], []
    for c in ac or []:
        if not isinstance(c, dict):
            continue
        a = c.get("artist") or {}
        name = c.get("name") or a.get("name") or ""
        phrase.append(name + (c.get("joinphrase") or ""))
        sort.append((a.get("sort-name") or name) + (c.get("joinphrase") or ""))
        names.append(name)
        if a.get("id"):
            ids.append(a["id"])
    return "".join(phrase), "".join(sort), names, ids


def album_tags(rel) -> dict:
    t = {}
    phrase, sort, names, ids = credit(rel.get("artist-credit"))
    rg = rel.get("release-group") or {}
    t["album"] = [rel.get("title", "")]
    t["albumartist"] = [phrase]
    t["albumartistsort"] = [sort]
    t["albumartists"] = names
    t["musicbrainz_albumartistid"] = ids
    t["musicbrainz_albumid"] = [rel["id"]]
    if rg.get("id"):
        t["musicbrainz_releasegroupid"] = [rg["id"]]
    if rel.get("date"):
        t["date"] = [rel["date"]]
    if rg.get("first-release-date"):
        t["originaldate"] = [rg["first-release-date"]]
        t["originalyear"] = [rg["first-release-date"][:4]]
    if rel.get("country"):
        t["releasecountry"] = [rel["country"]]
    if rel.get("status"):
        t["releasestatus"] = [str(rel["status"]).lower()]
    types = ([rg["primary-type"]] if rg.get("primary-type") else []) + list(rg.get("secondary-types") or [])
    if types:
        t["releasetype"] = [x.lower() for x in types]
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
    if tr.get("script"):
        t["script"] = [tr["script"]]
    if tr.get("language"):
        t["language"] = [tr["language"]]
    return {k: v for k, v in t.items() if v and any(v)}


def audio_media(rel):
    return [m for m in rel.get("media") or [] if str(m.get("format") or "").lower() not in VIDEO_FORMATS]


def release_tags(rel) -> list[dict]:
    common = album_tags(rel)
    media = audio_media(rel)
    total_discs = len([m for m in media if m.get("tracks")]) or len(media) or 1
    out = []
    for disc, m in enumerate(media, 1):
        tracks = [t for t in m.get("tracks") or [] if not (t.get("recording") or {}).get("video")]
        for t in tracks:
            rec = t.get("recording") or {}
            phrase, sort, names, ids = credit(t.get("artist-credit") or rec.get("artist-credit"))
            d = dict(common)
            d.update({
                "title": [t.get("title") or rec.get("title") or ""],
                "artist": [phrase or common["albumartist"][0]],
                "artistsort": [sort or common.get("albumartistsort", [""])[0]],
                "artists": names or common.get("albumartists", []),
                "musicbrainz_artistid": ids or common.get("musicbrainz_albumartistid", []),
                "musicbrainz_recordingid": [rec["id"]] if rec.get("id") else [],
                "musicbrainz_trackid": [t["id"]] if t.get("id") else [],
                "tracknumber": [str(t.get("position") or len([x for x in out if x["discnumber"] == [str(disc)]]) + 1)],
                "totaltracks": [str(len(tracks))],
                "discnumber": [str(disc)],
                "totaldiscs": [str(total_discs)],
            })
            if m.get("format"):
                d["media"] = [m["format"]]
            if m.get("title"):
                d["discsubtitle"] = [m["title"]]
            if rec.get("isrcs"):
                d["isrc"] = list(rec["isrcs"])
            out.append({k: v for k, v in d.items() if v and any(v)})
    return out
