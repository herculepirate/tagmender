"""album_artist.py - the profile's "album_artist" group: the album artist (and the folder it
names) as the lead artist alone, while the Artist tag keeps the whole credit.

  lead         on: a joint credit's first artist is the album artist ("Thomas Strønen & Time Is
               a Blind Guide" -> "Thomas Strønen"), and (bands) a band named after one of its
               members is filed under that member ("Steve Morse Band" -> "Steve Morse").
               Off (default): the album artist is the release's credit, as before.
  bands        with lead: a band whose name is a member's own name followed by more words
               ("Pat Metheny Group", "The Jimi Hendrix Experience", "Tom Petty and the
               Heartbreakers", "Glenn Miller and His Orchestra") is filed under that member.
               MusicBrainz decides: the band's members (its "member of band" relationships)
               must hold a person whose name the band's name starts with. So "Deep Purple"
               (Steve Morse a member) or "The Marshall Tucker Band" (no member of that name)
               stay as they are.
  file_under   with lead: your own names, {"credit": "album artist"}, over the rules above:
               "Dregs": "Dixie Dregs" files one under another; a name mapped to itself
               ("Ben Folds Five": "Ben Folds Five") is kept as credited.

The album artist's sort name and MusicBrainz ID follow the lead artist when they are known (the
credit's own, or the member's from MusicBrainz); an unknown one is taken off rather than left
naming the whole credit. Various-artists releases are never changed. engine.Engine applies it
to every identified album's targets after own_tag_choices.
"""
from __future__ import annotations

import re
import unicodedata

import compilations

# choose(): MusicBrainz could not be asked for a band's members - the album artist the files
# already hold stays (a re-run during an outage neither renames nor moves a settled album)
UNKNOWN = ("",)

# the most words after the member's name ("and His Orchestra", "& The Silver Bullet Band")
MAX_TAIL_WORDS = 5


def settings(profile) -> dict:
    c = profile.get("album_artist") if hasattr(profile, "get") else None
    return c if isinstance(c, dict) else {}


def enabled(profile) -> bool:
    return bool(settings(profile).get("lead"))


def fold(s) -> str:
    """A name compared without case, accents, punctuation or "&"/"and" differences."""
    s = str(s or "").replace("ø", "o").replace("Ø", "O").replace("æ", "ae").replace("Æ", "AE")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().casefold()
    s = s.replace("&", " and ").replace("’", "'")
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", s).split())


def _no_the(s: str) -> str:
    return s[4:] if s.startswith("the ") else s


def leader_of(band: str, members) -> tuple | None:
    """The member (name, sort name, MusicBrainz ID) a band is named after: the band's name is
    that member's whole name followed by up to MAX_TAIL_WORDS words ("Pat Metheny Group"); of
    several, the longest name. None when no member's name starts it."""
    b = _no_the(fold(band))
    best = None
    for m in members or ():
        name = fold(m[0])
        if not name or not b.startswith(name + " "):
            continue
        if len(b[len(name):].split()) > MAX_TAIL_WORDS:
            continue
        if best is None or len(name) > len(fold(best[0])):
            best = m
    return best


def _first(t, key) -> str:
    v = t.get(key)
    v = v if isinstance(v, list) else [v] if v else []
    return str(v[0]) if v else ""


def _list(t, key) -> list:
    v = t.get(key)
    return [str(x) for x in (v if isinstance(v, list) else [v] if v else [])]


def choose(t: dict, profile, members_of=None):
    """(name, sort name, MusicBrainz ID, Discogs ID) of the album artist for tags t, or None to
    keep the credit. members_of(mbid) -> [(name, sort, id), ...] of a band (None: unknown)."""
    c = settings(profile)
    if not c.get("lead") or compilations.is_various(t):
        return None
    credit = _first(t, "albumartist")
    if not credit:
        return None
    mine = {fold(k): str(v).strip() for k, v in (c.get("file_under") or {}).items()
            if str(k).strip() and str(v).strip()}
    names, ids = _list(t, "albumartists"), _list(t, "musicbrainz_albumartistid")
    sorts, dg = _list(t, "~albumartists_sort"), _list(t, "discogs_albumartist_id")

    def mapped(name):
        """Your own name for NAME: (name, keep) - keep when you mapped it to itself."""
        if fold(name) in mine:
            to = mine[fold(name)]
            return to, fold(to) == fold(name)
        return None, False

    to, keep = mapped(credit)
    if keep:
        return None
    if to:
        return _found(to, ids, members_of)
    if len(names) >= 2:                                  # a joint credit: its first artist
        aligned = lambda xs: xs[0] if len(xs) == len(names) else ""   # noqa: E731
        lead = (names[0], aligned(sorts), aligned(ids), aligned(dg))
    else:
        lead = (names[0] if names else credit, sorts[0] if len(sorts) == 1 else _first(t, "albumartistsort"),
                ids[0] if len(ids) == 1 else "", dg[0] if len(dg) == 1 else "")
    to, keep = mapped(lead[0])
    if keep:
        return lead if fold(lead[0]) != fold(credit) else None
    if to:
        return _found(to, [lead[2]] if lead[2] else [], members_of)
    if c.get("bands", True) and lead[2] and members_of:
        members = members_of(lead[2])
        if members is None:
            return UNKNOWN
        hit = leader_of(lead[0], members) if members else None
        if hit:
            lead = (hit[0], hit[1] or "", hit[2] or "", "")
    return lead if fold(lead[0]) != fold(credit) else None


def _found(name, ids, members_of):
    """Your own album artist NAME, with its sort name and ID when the credited band lists a
    member of that name."""
    if len(ids) == 1 and members_of:
        for m in members_of(ids[0]) or ():
            if fold(m[0]) == fold(name):
                return (name, m[1] or "", m[2] or "", "")
    return (name, "", "", "")


def apply(targets: list, profile, members_of=None) -> list:
    """Every track's tags with the album artist chosen (choose, from the first track's: the
    album artist is the album's)."""
    if not targets or not enabled(profile):
        return targets
    lead = choose(targets[0], profile, members_of)
    if lead == UNKNOWN:
        keys = ("albumartist", "albumartists", "albumartistsort", "musicbrainz_albumartistid",
                "discogs_albumartist_id", "~albumartists", "~albumartists_sort")
        return [{k: v for k, v in t.items() if k not in keys} for t in targets]
    if not lead:
        return targets
    name, sort, mbid, dgid = lead
    out = []
    for t in targets:
        t = dict(t)
        remove = [str(x) for x in t.get("~remove") or []]
        t["albumartist"] = t["albumartists"] = t["~albumartists"] = [name]
        for key, value in (("albumartistsort", sort), ("musicbrainz_albumartistid", mbid),
                           ("discogs_albumartist_id", dgid)):
            if value:
                t[key] = [value]
            elif key in t or key != "discogs_albumartist_id":
                t.pop(key, None)                         # (unknown: never the whole credit's)
                if key not in remove:
                    remove.append(key)
        if sort:
            t["~albumartists_sort"] = [sort]
        else:
            t.pop("~albumartists_sort", None)
        if remove:
            t["~remove"] = remove
        out.append(t)
    return out


def band_members(http, mbid, log=None):
    """[(name, sort name, MusicBrainz ID)] of the people MusicBrainz lists as members of the
    artist MBID (none for a person); None when MusicBrainz could not be asked (the album artist
    is then left as credited)."""
    try:
        det = http.get_json(f"https://musicbrainz.org/ws/2/artist/{mbid}?inc=artist-rels&fmt=json", "mb")
    except Exception as exc:                             # (an outage: the credit stays)
        if log:
            log.info("album artist: members of %s not read: %s", mbid, exc)
        return None
    if not isinstance(det, dict) or det.get("type") == "Person":
        return []
    out = []
    for r in det.get("relations") or []:
        a = r.get("artist") or {}
        if r.get("type") == "member of band" and r.get("direction") == "backward" and a.get("name"):
            out.append((a["name"], a.get("sort-name") or "", a.get("id") or ""))
    return out
