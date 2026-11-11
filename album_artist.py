"""album_artist.py - the profile's "album_artist" group: the album artist (and the folder it
names) as the lead artist alone, while the Artist tag keeps the whole credit.

  lead         on: a joint credit's first artist is the album artist ("Thomas Strønen & Time Is
               a Blind Guide" -> "Thomas Strønen"), and (bands) a band named after one of its
               members is filed under that member ("Steve Morse Band" -> "Steve Morse").
               On by default (NAM-1); off: the album artist is the release's credit.
  bands        with lead: a band whose name is a member's own name followed by more words
               ("Pat Metheny Group", "The Jimi Hendrix Experience", "Tom Petty and the
               Heartbreakers", "Glenn Miller and His Orchestra") is filed under that member.
               MusicBrainz decides: the band's members (its "member of band" relationships)
               must hold a person whose name the band's name starts with; when MusicBrainz
               lists none (or the album came from Discogs alone), the members Discogs lists
               for the band's Discogs artist do ("Jason Bieler and The Baron Von Bielski
               Orchestra": Jason Bieler). So "Deep Purple"
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
# a database not asked at all (Discogs off, no token): no answer - never "no members" (NAM-8)
NOT_ASKED = ()

# the most words after the member's name ("and His Orchestra", "& The Silver Bullet Band",
# "and The Baron Von Bielski Orchestra")
MAX_TAIL_WORDS = 6


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
        # NAM-8: a duo or trio named after its equal members ("Jimmy Page & Robert Plant",
        # MusicBrainz's group of the two): the rest of the name is the other members' names alone
        rest = [x for x in re.split(r"\s*(?:\band\b|,)\s*", b[len(name):]) if x.strip()]
        others = {_no_the(fold(o[0])) for o in members if fold(o[0]) != name}
        if rest and all(_no_the(x.strip()) in others for x in rest):
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


def choose(t: dict, profile, members_of=None, discogs_members_of=None, find_ids=None):
    """(name, sort name, MusicBrainz ID, Discogs ID) of the album artist for tags t, or None to
    keep the credit. members_of(mbid) -> [(name, sort, id), ...] of a band (None: unknown);
    discogs_members_of(discogs id) -> [(name, "", "", discogs id), ...], asked when MusicBrainz
    names no leader (or the band has no MusicBrainz ID). find_ids(name, mbid) -> (mbid, discogs
    id): the band's IDs when the credit lacks them or its own lead to no leader - the Discogs
    artist its MusicBrainz entry links to, or (no ID at all: a shop's credit) the one MusicBrainz
    artist of exactly that name - so the rule holds whichever source the album was tagged from."""
    c = settings(profile)
    if not c.get("lead") or compilations.is_various(t):
        return None
    credit = _first(t, "albumartist")
    if not credit:
        return None
    mine = {fold(k): str(v).strip() for k, v in (c.get("file_under") or {}).items()
            if str(k).strip() and str(v).strip()}
    # (a credit already written one value per artist; a Discogs release gives its credit as one
    # text, and its artists separately: the engine passes those along unwritten. A comma or "&"
    # in the text alone proves nothing: "Earth, Wind & Fire")
    values = _list(t, "albumartist")
    split = len(values) >= 2
    names = _list(t, "albumartists") or _list(t, "~discogs_albumartists") or (values if split else [])
    ids = _list(t, "musicbrainz_albumartistid")

    def changed(name):
        """NAME is not already the whole album artist."""
        return split or fold(name) != fold(credit)

    # (the Discogs ID: a Partial album's tag, or the credited Discogs artists the engine passes
    # along unwritten for a release tagged from Discogs)
    sorts = _list(t, "~albumartists_sort")
    dg = _list(t, "discogs_albumartist_id") or _list(t, "~discogs_albumartist_id")

    def mapped(name):
        """Your own name for NAME: (name, keep) - keep when you mapped it to itself."""
        if fold(name) in mine:
            to = mine[fold(name)]
            return to, fold(to) == fold(name)
        return None, False

    to, keep = mapped(credit) if not split else (None, False)    # (of a split credit: its lead, below)
    if keep:
        return None
    if to:
        return _found(to, ids, members_of)
    # NAM-1: a band credited after its members, Discogs's "Thomas Strønen / Iain Ballamy : Food",
    # is the band ("Food"), not its first member: the " : " puts the members before the act
    band_at = -1
    if len(names) >= 2 and " : " in credit:
        after = fold(credit.rsplit(" : ", 1)[1])
        band_at = next((k for k, n in enumerate(names) if fold(n) == after), -1)
    if band_at >= 0:
        aligned = lambda xs: xs[band_at] if len(xs) == len(names) else ""   # noqa: E731
        lead = (names[band_at], aligned(sorts), aligned(ids), aligned(dg))
    elif len(names) >= 2:                               # a joint credit: its first artist ...
        # (your own name for its first artist goes first: file_under over the rules)
        equals = None if mapped(names[0])[0] else _equals(names, ids, dg, members_of, discogs_members_of)
        if equals is True:
            return None              # ... NAM-8: unless a collaboration of equals - the whole credit
        if equals == "unknown":
            return UNKNOWN
        aligned = lambda xs: xs[0] if len(xs) == len(names) else ""   # noqa: E731
        lead = (names[0], aligned(sorts), aligned(ids), aligned(dg))
    else:
        lead = (names[0] if names else credit, sorts[0] if len(sorts) == 1 else _first(t, "albumartistsort"),
                ids[0] if len(ids) == 1 else "", dg[0] if len(dg) == 1 else "")
    to, keep = mapped(lead[0])
    if keep:
        return lead if changed(lead[0]) else None
    if to:
        return _found(to, [lead[2]] if lead[2] else [], members_of)
    if c.get("bands", True):
        # (MusicBrainz first; Discogs when MusicBrainz names no such member or has no ID for the
        # band. Unknown - the credit kept as the files hold it - only when no database that
        # was asked could answer: MusicBrainz's answer stands when Discogs cannot be reached)
        def ask_both(mbid, dgid):
            hit, unknown = None, {}
            for which, some_id, ask in (("mb", mbid, members_of), ("dg", dgid, discogs_members_of)):
                if hit or not some_id or not ask:
                    continue
                members = ask(some_id)
                unknown[which] = members is None
                hit = leader_of(lead[0], members) if members else None
            return hit, unknown
        hit, unknown = ask_both(lead[2], lead[3])
        if not hit and find_ids and not (unknown.get("mb") or unknown.get("dg")) \
                and len(_no_the(fold(lead[0])).split()) >= 2:      # (a member's name and more)
            # (no leader by the credit's own IDs, or none: the band's other database, or the band
            # found by its name; a leader found there stands, nothing found keeps the credit)
            found = find_ids(lead[0], lead[2]) or ("", "")
            more = (found[0] if found[0] and found[0] != lead[2] else "",
                    found[1] if found[1] and found[1] != lead[3] else "")
            if any(more):
                hit, _more_unknown = ask_both(*more)
        # (MusicBrainz not reached, or the band known only to Discogs and it not reached)
        unknown = unknown.get("mb") or ("mb" not in unknown and unknown.get("dg"))
        if hit:
            lead = (hit[0], hit[1] or "", hit[2] or "", hit[3] if len(hit) > 3 else "")
        elif unknown:
            return UNKNOWN
    return lead if changed(lead[0]) else None


def _equals(names, ids, dg, members_of=None, discogs_members_of=None):
    """NAM-8: a joint credit is a collaboration of equals (True: Calexico and Iron & Wine, two
    bands; Paolo Fresu & Omar Sosa, two musicians) unless its first artist is a member of another
    act it names - a leader credited with their own band (False: Thomas Strønen & Time Is A Blind
    Guide, Strønen in the band). The other acts' members come from MusicBrainz (its "member of
    band" relationships), else from Discogs. None when no database can be asked for any of them
    (no IDs: the first artist leads, as before); "unknown" when one could not be reached."""
    lead = fold(names[0])
    # (and by its IDs: a name credited otherwise on the sleeve, Discogs's "L. Person" for "Lead Person")
    lead_ids = {str(x) for x in ((ids[0] if len(ids) == len(names) else ""),
                                 (dg[0] if len(dg) == len(names) else ""))} - {""}
    asked = False
    for k in range(1, len(names)):
        mbid = ids[k] if len(ids) == len(names) else ""
        dgid = dg[k] if len(dg) == len(names) else ""
        members = None
        for some_id, ask in ((mbid, members_of), (dgid, discogs_members_of)):
            if some_id and ask:
                got = ask(some_id)
                if got is None:
                    return "unknown"
                if isinstance(got, tuple) and not got:
                    continue                             # (NOT_ASKED: no answer either way)
                members = list(got)
                asked = True
                if members:
                    break
        if members and any(fold(m[0]) == lead or (lead_ids & {str(x) for x in m[2:4] if x}) for m in members):
            return False                                 # (the first artist leads this band)
    return True if asked else None


def _found(name, ids, members_of):
    """Your own album artist NAME, with its sort name and ID when the credited band lists a
    member of that name."""
    if len(ids) == 1 and members_of:
        for m in members_of(ids[0]) or ():
            if fold(m[0]) == fold(name):
                return (name, m[1] or "", m[2] or "", "")
    return (name, "", "", "")


def apply(targets: list, profile, members_of=None, discogs_members_of=None, find_ids=None) -> list:
    """Every track's tags with the album artist chosen (choose, from the first track's: the
    album artist is the album's)."""
    if not targets or not enabled(profile):
        return targets
    lead = choose(targets[0], profile, members_of, discogs_members_of, find_ids)
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
        t.pop("~discogs_albumartist_id", None)          # (the whole credit's: not the lead's)
        t.pop("~discogs_albumartists", None)
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


MB_ARTIST = "https://musicbrainz.org/ws/2/artist/{0}?inc=artist-rels+url-rels&fmt=json"


def linked_discogs(http, mbid, log=None) -> str:
    """The Discogs artist ID MusicBrainz's entry for MBID links to ("" when none or not read)."""
    try:
        det = http.get_json(MB_ARTIST.format(mbid), "mb")
    except Exception as exc:
        if log:
            log.info("album artist: links of %s not read: %s", mbid, exc)
        return ""
    for r in (det or {}).get("relations") or [] if isinstance(det, dict) else []:
        m = re.search(r"discogs\.com/artist/(\d+)", str((r.get("url") or {}).get("resource") or ""))
        if r.get("type") == "discogs" and m:
            return m.group(1)
    return ""


def artist_named(http, name, log=None) -> str:
    """The MusicBrainz ID of the one artist named exactly NAME (case, accents and "&"/"and"
    aside); "" when there is none, several, or MusicBrainz could not be asked."""
    import urllib.parse
    q = urllib.parse.quote(f'artist:"{name}"')
    try:
        res = http.get_json(f"https://musicbrainz.org/ws/2/artist?query={q}&limit=10&fmt=json", "mb")
    except Exception as exc:
        if log:
            log.info("album artist: %s not searched: %s", name, exc)
        return ""
    same = [a.get("id") for a in (res or {}).get("artists") or [] if isinstance(res, dict)
            and fold(a.get("name")) == fold(name) and a.get("id")]
    return same[0] if len(set(same)) == 1 else ""


def band_members(http, mbid, log=None):
    """[(name, sort name, MusicBrainz ID)] of the people MusicBrainz lists as members of the
    artist MBID (none for a person); None when MusicBrainz could not be asked (the album artist
    is then left as credited)."""
    try:
        det = http.get_json(MB_ARTIST.format(mbid), "mb")     # (one request: linked_discogs reads it too)
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


def discogs_members(http, dgid, log=None):
    """[(name, "", "", Discogs ID)] of the members Discogs lists for its artist DGID (none for a
    person or a band without them; Discogs' "(2)" name suffix dropped); None when Discogs could
    not be asked."""
    try:
        det = http.get_json(f"https://api.discogs.com/artists/{int(dgid)}", "discogs")
    except (TypeError, ValueError):
        return []
    except Exception as exc:                             # (an outage or no key: the credit stays)
        if log:
            log.info("album artist: Discogs members of %s not read: %s", dgid, exc)
        return None
    out = []
    for m in (det or {}).get("members") or [] if isinstance(det, dict) else []:
        name = re.sub(r"\s*\(\d+\)$", "", str(m.get("name") or "")).strip()
        if name:
            out.append((name, "", "", str(m.get("id") or "")))
    return out
