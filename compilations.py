"""compilations.py - the profile's "compilations" group: how a various-artists release (and,
when asked, a single artist's compilation) is marked in the tags.

  va_name        the name written as album artist (and its sort name) of a various-artists
                 release, whichever database identified it. Unset: the tagger's own setting
                 (tagger_settings va_name, "Various Artists"), as before this group existed.
  extra_flags    also ITUNESCOMPILATION=1 and COMP=1, in files with free-form text keys only
                 (Vorbis comments: FLAC, Ogg, Opus; APEv2: APE, WavPack, Musepack ...). ID3, MP4
                 and WMA have the compilation flag of their own (TCMP, cpil, WM/IsCompilation),
                 which iTunes reads: no odd custom frames are added there.
  grouping       GROUPING = "Compilation" where the file's Grouping is empty (never replaces:
                 Profile.policy makes the field fill-only while this is on)
  single_artist  also flag a single artist's compilation (a best-of), without renaming its
                 album artist

engine.own_tag_choices applies this to every identified album's targets, whichever source
identified it; levels.restrict keeps the fields these options write.
"""
from __future__ import annotations

from pathlib import Path

VARIOUS_ARTISTS_MBID = "89ad4ac3-39f7-470e-963a-56509c546377"     # (mb_tags.VARIOUS_ARTISTS_ID)
VARIOUS_ARTISTS_DISCOGS_ID = "194"                                 # (discogs_mapping._VARIOUS_ARTISTS_DISCOGS_ID)
VA_NAMES = ("Various Artists", "Various", "VA")
# an album artist spelled like this is a various-artists release (compared case-insensitively)
VA_SPELLINGS = {"various artists", "various", "va", "v.a.", "v/a"}
# files whose tags have free-form text keys of any name
FREE_FORM = {".flac", ".ogg", ".oga", ".opus", ".spx", ".wv", ".ape", ".mpc", ".mp+", ".ofr", ".ofs", ".tak"}
EXTRA_FLAGS = ("itunescompilation", "comp")
# hidden fields the sources set (never written): the Discogs release's artist is Various (id 194),
# or its format says Compilation
HIDDEN_VA, HIDDEN_COMPILATION = "~discogs_various", "~discogs_compilation"


def settings(profile) -> dict:
    c = profile.get("compilations") if hasattr(profile, "get") else None
    return c if isinstance(c, dict) else {}


def written_fields(profile) -> set:
    """The fields the profile's compilation options write (levels.restrict keeps them at the
    Basic level and in Partial albums)."""
    c = settings(profile)
    out = set()
    if c.get("extra_flags"):
        out |= set(EXTRA_FLAGS)
    if c.get("grouping"):
        out.add("grouping")
    if out or c.get("single_artist"):
        out.add("compilation")
    return out


def _list(v) -> list:
    return v if isinstance(v, list) else [v] if v else []


def _first(t, key) -> str:
    v = t.get(key)
    v = v if isinstance(v, list) else [v] if v else []
    return str(v[0]) if v else ""


def is_various(t: dict) -> bool:
    """The tags are those of a various-artists release: MusicBrainz's Various Artists, Discogs's
    artist 194 (hidden field set from the release, or its ID carried in the tags), or an album
    artist spelled Various Artists / Various / VA / V.A. / V/A.
    An ID decides over a name, and MusicBrainz's over Discogs's: a DJ mix MusicBrainz credits to
    the DJ is not various because Discogs lists it under Various; a band named "V.A." with its
    own ID is not various. The spelling counts only when no database names the artist."""
    mb = [str(x).strip().lower() for x in _list(t.get("musicbrainz_albumartistid")) if str(x).strip()]
    if mb:
        return VARIOUS_ARTISTS_MBID in mb
    dg = [str(x).strip() for x in _list(t.get("discogs_albumartist_id")) if str(x).strip()]
    if dg:
        return VARIOUS_ARTISTS_DISCOGS_ID in dg
    hint = _first(t, HIDDEN_VA)
    if hint in ("0", "1"):                          # (the Discogs release's own artists)
        return hint == "1"
    return _first(t, "albumartist").strip().casefold() in VA_SPELLINGS


def is_single_compilation(t: dict) -> bool:
    """A compilation by one artist (a best-of): MusicBrainz's secondary type Compilation, or
    Discogs's format description Compilation."""
    if _first(t, HIDDEN_COMPILATION) == "1":
        return True
    for k in ("~secondaryreleasetype", "releasetype"):
        v = t.get(k) or []
        if "compilation" in [str(x).strip().casefold() for x in (v if isinstance(v, list) else [v])]:
            return True
    return False


def discogs_hints(det) -> dict:
    """The hidden fields for a Discogs release document (artist id 194, "Compilation" among a
    format's descriptions)."""
    out = {}
    if not isinstance(det, dict):
        return out
    ids = [str(a.get("id") or "") for a in det.get("artists") or [] if isinstance(a, dict)]
    if any(ids):                                    # (artists named: "1" for Various, else "0")
        out[HIDDEN_VA] = ["1" if VARIOUS_ARTISTS_DISCOGS_ID in ids else "0"]
    for f in det.get("formats") or []:
        if isinstance(f, dict) and "compilation" in [str(x).strip().casefold() for x in f.get("descriptions") or []]:
            out[HIDDEN_COMPILATION] = ["1"]
    return out


def apply(t: dict, profile, path=None) -> dict:
    """One track's tags with the profile's compilation choices applied; path is the file they
    are for (the extra flags depend on its format)."""
    c = settings(profile)
    va = is_various(t)
    single = bool(c.get("single_artist")) and not va and is_single_compilation(t)
    if not (va or single):
        rm = [str(x).lower() for x in t.get("~remove") or []]
        if "compilation" in rm and path and Path(str(path)).suffix.lower() in FREE_FORM:
            # (not a compilation: the extra flags go with COMPILATION, so the file's flags agree)
            t = dict(t)
            t["~remove"] = list(t["~remove"]) + [k for k in EXTRA_FLAGS if k not in rm]
        return t
    t = dict(t)
    name = c.get("va_name")
    if va and isinstance(name, str) and name.strip():
        t["albumartist"] = t["albumartistsort"] = [name.strip()]
    t["compilation"] = ["1"]
    if t.get("~remove"):
        t["~remove"] = [x for x in t["~remove"] if str(x).lower() not in ("compilation",) + EXTRA_FLAGS]
        if not t["~remove"]:
            del t["~remove"]
    if c.get("extra_flags") and path and Path(str(path)).suffix.lower() in FREE_FORM:
        for k in EXTRA_FLAGS:
            t[k] = ["1"]
    if c.get("grouping") and not t.get("grouping"):
        t["grouping"] = ["Compilation"]
    return t
