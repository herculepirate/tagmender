"""engine.py - one processing engine for the CLI, the watcher, the GUI and the API.

  eng = Engine(profile)
  eng.albums_in(path)        -> [Group]          what is in a path: albums, disc sets, mixed
                                                  folders split by album, single files
  eng.identify(group)        -> plan.Proposal    MusicBrainz and Discogs (the matcher's own
                                                  evidence and scoring), fingerprints for
                                                  single tracks, the web as a last resort
  eng.plan(path)             -> plan dict        all of the above, then plan.build()
  executor.Executor(plan).run()                  the same executor every interface uses

Identification never depends on Picard. Tags come from the release itself:
  - MusicBrainz: mb_tags (Picard's mapping);
  - Discogs: the Discogs Enrichment plugin's own extraction code (discogs_mapping), so a
    Discogs-only album is tagged exactly as the plugin would tag it, with no MusicBrainz
    ID required or invented;
  - both, when they hold the same pressing (linked, or the matcher's "twin"): the
    MusicBrainz tags are the base and the Discogs release is applied to them the way the
    plugin applies it inside Picard (enrich(): its merge mode, style mode, field filters,
    protected tags, original date, track tags and artist-credit settling), with your
    plugin settings read from Picard.ini (tagger_settings). A profile's field_sources, if
    set, have the last word on named fields. Releases that are not the same pressing are
    never mixed.

Missing credentials, an outage or throttling at one provider is recorded on the album
(provider_errors) and does not make it unresolved when the other provider identified it.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import heapq
import logging
import os
import queue
import re
import sqlite3
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import companions
import album_artist
import classical
import classical as classical_mod          # (discogs_parts's own "classical" is its flag)
import compilations
import marks
import credentials
import requirements as rq
import exclusions as ex
import logsetup
import match_albums as ma
import match_core as mc
import mb_tags
import tagger_settings
import tagio
import plan as planmod
import reasons
import stagetimes

DISC_DIR = re.compile(r"^(?:cd|disc|disk|dvd|side)\s*[-_.]?\s*(?:\d+|one|two|three|four|five|six|seven|eight|"
                      r"nine|ten|[a-h])\b|^\d{1,2}$", re.I)
_NUMBER_ONLY = re.compile(r"^\d{1,2}$")


_DISC_WORD = r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)"
# "(Disc 1)", "[CD 2 of 3]", "(Disc One)", "(Disc 2: Live)" anywhere in the title, and an
# unbracketed "- Disc 2" / "CD1" at its end
_DISC_SUFFIX = re.compile(r"\s*[\(\[\{]\s*(?:disc|disk|cd|dvd)\s*" + _DISC_WORD +
                          r"(?:\s*(?:of|/)\s*\d+)?(?:\s*[:\-\u2013]\s*[^\)\]\}]*)?\s*[\)\]\}]"
                          r"|\s*(?:[-:,\u2013]\s*)?\b(?:disc|disk|cd|dvd)\s*" + _DISC_WORD +
                          r"(?:\s*(?:of|/)\s*\d+)?\s*$", re.I)


def _first(meta, k):
    return ((meta.get(k) or [""])[0] or "").strip()


def _album_title(meta) -> str:
    album = _first(meta, "album").casefold()
    return re.sub(r"\s+", " ", _DISC_SUFFIX.sub("", album)).strip() or album


def _one_compilation(files, metas, title, folder) -> bool:
    """Tracks sharing an album title, by several artists, with no album artist: one
    compilation (a freedb/EAC rip of a Various Artists CD) when they say so, when their
    folder is named after the album, or when their track numbers are a set of one album's."""
    if any(_first(metas[f], "compilation").lower() in ("1", "true", "yes") for f in files):
        return True
    fold = lambda s: re.sub(r"[\W_]+", " ", s.casefold()).strip()  # noqa: E731
    t = fold(title)
    named = re.compile(r"(?:^| )" + re.escape(t) + r"(?: |$)") if t else None
    if named and any(named.search(fold(d.name)) for d in {folder, *{f.parent for f in files}}):
        return True                            # "1999 - Hits 1999"; not "Demos" for "Demo"
    nums, totals = [], set()
    for f in files:
        n, _, tot = _first(metas[f], "tracknumber").partition("/")
        if not n.strip().isdigit():
            return False
        nums.append((_first(metas[f], "discnumber").partition("/")[0].strip(), int(n)))
        totals.add(tot.strip() or _first(metas[f], "totaltracks") or _first(metas[f], "tracktotal"))
    return len(set(nums)) == len(nums) and len(totals) == 1 and "" not in totals


def album_keys(files, folder, metas=None) -> dict:
    """{file: which album it belongs to} for the audio files of one folder (with its disc
    folders): exclusions.album_key, with the disc left out of the title ("Album (Disc 1)"
    and "Album (Disc Two)" are one album). Within one title, tracks without an album artist
    join the album artist the others have; with no album artist at all, tracks by different
    artists are one compilation only when they look like one (_one_compilation) - otherwise
    "Greatest Hits" by Queen and by ABBA stay two albums."""
    metas = {f: (metas or {}).get(f) or ex.read_meta(f) for f in files}
    out, by_title = {}, {}
    for f in files:
        k = ex.album_key(metas[f])
        if k[0] != "tags":
            out[f] = k
        else:
            by_title.setdefault(_album_title(metas[f]), []).append(f)
    for title, fs in by_title.items():
        aa = {f: _first(metas[f], "albumartist").casefold() for f in fs}
        ar = {f: _first(metas[f], "artist").casefold() for f in fs}
        aas = {v for v in aa.values() if v}
        if aas:
            for f in fs:
                who = aa[f] or (next(iter(aas)) if len(aas) == 1 else ar[f])
                out[f] = ("tags", who, title)
            continue
        artists = set(ar.values())
        if len(artists) > 1 and not _one_compilation(fs, metas, title, Path(folder)):
            for f in fs:
                out[f] = ("tags", ar[f], title)
        else:
            who = next(iter(artists)) if len(artists) == 1 else ""
            for f in fs:
                out[f] = ("tags", who, title)
    return out


def is_disc_dir(d: Path) -> bool:
    """A disc folder ("CD1", "Disc 2", "Disc One", "Side A"). A folder named only by a
    number counts when its siblings are numbered 1, 2, ... too: "Adele/19" and "Adele/21"
    are albums, "Box/1" and "Box/2" are discs."""
    if not DISC_DIR.match(d.name) or mc.parse_version(d.name):
        return False                      # ("DVD 2.0", "DVD 5.1": a version's folder, not "DVD 2")
    if not _NUMBER_ONLY.match(d.name):
        return True
    try:
        nums = sorted(int(x.name) for x in d.parent.iterdir() if x.is_dir() and _NUMBER_ONLY.match(x.name))
    except OSError:
        return False
    return len(nums) >= 2 and nums[0] == 1 and nums == list(range(1, len(nums) + 1))


# DSC-1: a disc of a set in a folder of its own beside the others, named after the set:
# "Album CD1", "Album - Disc 2", "Album [CD 1]", "Album (Disc 2)"
_SIBLING_DISC = re.compile(r"^(?P<stem>.*?[^\s._\-\[\(])[\s._\-]*[\[\(]?\s*(?:cd|disc|disk)\s*[-_.]?\s*"
                           r"(?P<n>\d{1,2})\s*[\]\)]?$", re.I)


def sibling_disc(d: Path):
    """DSC-1: (the set's name, disc number) of a disc folder named after its set ("Album CD1"
    -> ("Album", 1)), else None. A folder named as a disc alone ("CD1", "Disc 2") is a disc
    folder of the album folder holding it (is_disc_dir), not this."""
    d = Path(d)
    if is_disc_dir(d):
        return None
    m = _SIBLING_DISC.match(d.name)
    if not m or int(m["n"]) < 1:
        return None
    return m["stem"].strip(), int(m["n"])


def _format_word(fmt) -> str:
    """MIX-1: a database medium's format as a disc subtitle names it: "SACD", "CD", "Blu-ray",
    "DVD-Audio", "DVD"; else the format as given ("Digital Media")."""
    f = str(fmt or "").strip()
    kind = mc.medium_type(f)
    if kind == "sacd":
        return "SACD"
    if kind == "cd":
        return "CD"
    low = f.casefold()
    if "blu" in low:
        return "Blu-ray"
    if "dvd-audio" in low or "dvd audio" in low:
        return "DVD-Audio"
    if "dvd" in low:
        return "DVD"
    return f


_CHANNEL_WORDS = {1: "Mono", 2: "Stereo", 3: "3.0", 4: "Quad", 5: "5.0", 6: "5.1", 7: "6.1", 8: "7.1"}


def version_subtitles(targets, files) -> bool:
    """MIX-1: two versions of the album in one folder matched as one release whose media carry
    no title (an SACD's stereo and 5.1 areas as discs 1 and 2): each such disc's subtitle is
    inferred from its format and its audio's layout - "SACD Stereo", "SACD 5.1", "Blu-ray 5.1",
    "CD" (a CD names no layout). Only when the discs' layouts or formats differ (a plain 2CD set
    is no two versions); a disc subtitle the database gives is never replaced. True when one
    was written."""
    if not targets or len(targets) != len(files):
        return False
    discs = {}
    for t, f in zip(targets, files):
        d = str((t.get("discnumber") or [""])[0]).partition("/")[0].strip()
        if not d:
            return False
        discs.setdefault(d, []).append((t, f))
    if len(discs) < 2:
        return False
    # (versions: two discs of the release holding the same tracks - not a CD and its bonus disc)
    lists = [tuple(mc.fold(str((t.get("title") or [""])[0])) for t, _f in m) for m in discs.values()]
    if len(set(lists)) == len(lists):
        return False
    words = {}
    for d, members in discs.items():
        fmts = {_format_word((t.get("media") or [""])[0]) for t, _f in members}
        lays = set()
        for _t, f in members:
            try:
                info = ma.read_file(Path(f))
            except Exception:
                return False
            area = mc.layout_of_channels(0, info.get("sacd_area"))
            n = int(info.get("channels") or 0)
            lays.add("Stereo" if area == "stereo" else
                     _CHANNEL_WORDS.get(n) or ("Multichannel" if area == "multichannel" else ""))
        if len(fmts) != 1 or len(lays) != 1 or "" in fmts or "" in lays:
            return False
        words[d] = (fmts.pop(), lays.pop())
    if len(set(words.values())) < 2:
        return False                                       # (the same disc twice over: no versions)
    wrote = False
    for d, (fmt, lay) in words.items():
        if any(t.get("discsubtitle") for t, _f in discs[d]):
            continue                                       # (the database's own title stays)
        text = fmt if fmt == "CD" else f"{fmt} {lay}"
        for t, _f in discs[d]:
            t["discsubtitle"] = [text]
        wrote = True
    return wrote


def _disc_sets(groups, metas) -> list:
    """DSC-1: sibling folders of one release ("Album CD1", "Album CD2": one parent, one set
    name, different disc numbers) whose tags name the same album are one album: their files
    join the first disc's group (Group.discs lists the folders) and the others are no group of
    their own. A mixed folder, or two folders naming the same disc, is left as it is."""
    sets = {}
    for g in groups:
        sd = None if g.others else sibling_disc(g.folder)
        if sd is None:
            continue
        keys = set(album_keys(g.files, g.folder, metas).values())
        if len(keys) != 1 or next(iter(keys))[0] != "tags":
            continue
        sets.setdefault((g.folder.parent, mc.fold(sd[0]), next(iter(keys))), []).append((sd[1], g))
    joined = set()
    for members in sets.values():
        if len({n for n, _g in members}) != len(members):
            continue                                     # (two folders say the same disc)
        members.sort(key=lambda m: m[0])
        first = members[0][1]
        first.discs = [g.folder for _n, g in members]
        if len(members) > 1:
            first.files = [f for _n, g in members for f in g.files]
            first.single = False
            joined |= {id(g) for _n, g in members[1:]}
    return [g for g in groups if id(g) not in joined]


# DSC-3: a disc folder holding two discs of a set ("CD02-03 1973 Out-Takes") and its
# parts ("Part 1", "Part 2"): the range's discs in order
# (written tight, "CD02-03" / "CD2-3": never "CD1 - 12 Inch Mixes" - no spaces around its
# dash, no "12 inch", at most ten discs: box_part_disc)
_DISC_RANGE = re.compile(r"^(?:cd|disc|disk)\s*[-_.]?\s*(?P<a>\d{1,2})[-–](?P<b>\d{1,2})(?!\d)"
                         r"(?!\s*(?:inch|in\b|\"|''|'))", re.I)
_PART_DIR = re.compile(r"^(?:part|pt\.?)\s*[-_.]?\s*(?P<n>\d{1,2})$", re.I)


def box_part_disc(d: Path):
    """DSC-3: the set's disc number a part folder of a disc-range folder holds ("CD02-03 .../
    Part 2" -> 3), else None: the range counts up (b > a) and the part lies within it."""
    d = Path(d)
    m, r = _PART_DIR.match(d.name), _DISC_RANGE.match(d.parent.name)
    if not m or not r:
        return None
    a, b, n = int(r["a"]), int(r["b"]), int(m["n"])
    if not (1 <= a < b <= a + 9 and 1 <= n <= b - a + 1):
        return None
    return a + n - 1


def _box_disc_dir(d: Path, folder: Path) -> bool:
    """DSC-2 / DSC-3: d is one disc's folder of the set in folder - a disc folder of it
    ("CD1", "CD01 1974 First Album"), or a part of a disc-range folder of it ("CD02-03 .../Part 1")."""
    if d.parent == folder:
        return is_disc_dir(d)
    return d.parent.parent == folder and box_part_disc(d) is not None and is_disc_dir(d.parent)


def _one_box(folder, files, keys, metas) -> bool:
    """DSC-2: a set's disc folders ("CD1" ... "CD4" in "Holy Diver [2022 RM DLX 4CD]") whose
    discs each carry their own album title ("Holy Diver [2022 Barresi RMX]", "... Live In
    Fresno", "... Outtakes, Singles & B-Sides") are still one release: every file lies in a
    disc folder of FOLDER, each disc folder is tagged as one album, all by one same album
    artist, and no two disc folders' tags name the same disc number. A folder with tracks of
    its own beside them, an untagged disc, or two artists' albums are left as they are."""
    by_dir = {}
    for f in files:
        d = Path(f).parent
        if not _box_disc_dir(d, folder):
            return False
        by_dir.setdefault(d, []).append(f)
    if len(by_dir) < 2:
        return False
    artists, numbers = set(), []
    for d, fs in by_dir.items():
        ks = {keys[f] for f in fs}
        if len(ks) != 1:
            return False                                   # (a disc folder of two albums)
        k = next(iter(ks))
        if k[0] != "tags" or not k[1]:
            return False
        artists.add(k[1])
        nums = {_first(metas.get(f) or {}, "discnumber").partition("/")[0].strip().lstrip("0") for f in fs} - {""}
        if len(nums) != 1:
            return False                                   # (no disc number: nothing says it is a disc of one set)
        part = box_part_disc(d) if d.parent != folder else None
        if part is not None and nums != {str(part)}:
            return False                                   # (DSC-3: "CD02-03/Part 2" tagged as another disc than 3)
        numbers.extend(nums)
    return len(artists) == 1 and len(numbers) == len(set(numbers))


# DSC-5: the CD count a box set's folder names ("Complete Box 13CD+DVD", "(2011, 7CD+DVD, Sony,
# ...)", "[16 CD]", "4xSACD"): a number written right before "CD" - never a catalogue number's
# digits ("88697763332-4"), never the disc word of a disc folder ("CD4-5")
_BOX_CD_COUNT = re.compile(r"(?<![\w.\-])(\d{1,3})\s*[x×]?\s*(?:shm-?|blu-?spec\s*|hq|uhq|sa)?cds?(?![a-z0-9])",
                           re.I)


def box_cd_count(name) -> int:
    """DSC-5: the number of CDs a folder's name says it holds ("13CD+DVD" -> 13), when it names
    one count only and at least two CDs; else 0."""
    counts = {int(m[1]) for m in _BOX_CD_COUNT.finditer(str(name or ""))}
    return counts.pop() if len(counts) == 1 and min(counts) >= 2 else 0


def _disc_tag(meta) -> tuple:
    """(disc number, disc total) a file's tags give, as text without leading zeros ("" for none):
    "4/7" and "04" with a total tag of "7" are both ("4", "7")."""
    num, _s, total = _first(meta, "discnumber").partition("/")
    total = total.strip() or _first(meta, "totaldiscs") or _first(meta, "disctotal")
    return num.strip().lstrip("0"), total.strip().lstrip("0")


def numbered_box(box: Path, found: dict, metas: dict) -> bool:
    """DSC-5 (BOX-1): a box set's folder whose discs sit in folders of its own named as albums
    ("01. (1980) - Kenso (2012) - KIZC 168"; "CD3. 1992. Amused to Death (88697763332-3)"),
    some holding two discs in disc folders ("CD4-5. 2005. Ca Ira (2CD, ...)/CD1" and "/CD2"), is
    one album - the box - when every sign of it agrees: the folder's name gives its CD count N
    (box_cd_count) and it has no tracks of its own; each of its audio folders is a sub-folder
    of it, or a disc folder of one (a sub-folder holding disc folders has no tracks beside
    them); each such folder's files are tagged as one album with one disc number, by one album
    artist all through; the disc numbers are 1 .. N, each once, rising in the folders' order;
    and no file's disc total says fewer than N discs (a "1 of 1" standalone album). An artist's
    folder or a "Compilations" folder of albums each tagged disc 1, a box whose discs' tags
    leave a gap or count otherwise, two artists' albums - stay apart as before."""
    got = _box_discs(box, found, metas)
    return bool(got) and got[1] == list(range(1, got[0] + 1))


# DSC-6: the words a twofer's folder joins its albums' titles with ("The Stranger & An Innocent
# Man", "Album One / Album Two", "One + Two", "One and Two")
_TITLE_JOIN = re.compile(r"\s+(?:&|\+|/|and|und|et|y)\s+|\s*/\s*", re.I)
# a year or bracketed part around the title in a folder's name: "1992 - ", " (2CD)", " [Columbia]"
_NAME_EXTRAS = re.compile(r"^\s*(?:19|20)\d\d\s*[-.–]\s*|\s*[\(\[\{][^\)\]\}]*[\)\]\}]")


def _title_fold(text) -> str:
    return re.sub(r"[\W_]+", " ", str(text or "").casefold()).strip()


def numbered_disc_set(folder: Path, files, keys, metas=None) -> bool:
    """DSC-7: a folder whose tracks all lie in disc folders of its own numbered 1 to N ("Disc 1
    1974 - Streetlife Serenade", "Disc 02", "Disc 3 - 52nd Street (Original Album Classics #2)"),
    each number once, N two or more, by one artist, is one set - its discs - whatever album each
    disc is tagged as (an "Original Album Classics" box: each disc its own album's title). A
    folder with tracks of its own, a gap or a repeat in the numbers, two artists, or a folder
    not named as a disc stays as before."""
    dirs = {f.parent for f in files}
    if len(dirs) < 2 or any(d.parent != folder or not is_disc_dir(d) for d in dirs):
        return False
    numbers = []
    for d in dirs:
        m = _LEAD_NUMBER.match(d.name)
        if not m:
            return False
        numbers.append(int(m[1]))
    if sorted(numbers) != list(range(1, len(dirs) + 1)):
        return False
    artists = {k[1] for k in keys.values() if k[0] == "tags"}
    if not (all(k[0] == "tags" for k in keys.values()) and len(artists) == 1 and all(artists)):
        return False
    # (and its discs name the set: half of them at least carry the set folder's title in brackets
    # in their folder's name or album tag - "52nd Street (Original Album Classics #2)" - or as
    # their album tag itself; "Drops 1" and "Drops 2" in "Drops (2001-2004)/Disc 1", "/Disc 2"
    # are a series' volumes, each its own release, and "CD1", "CD2" of an artist's folder two albums)
    name = _title_fold(_bare_name(folder.name))
    if not name:
        return False
    metas = metas or {}

    def names_set(d):
        tags = {_first(metas.get(f) or ex.read_meta(f), "album") for f in files if f.parent == d}
        for text in [d.name, *tags]:
            if any(_title_fold(b) == name for b in re.findall(r"[\(\[\{]([^\)\]\}]*)[\)\]\}]", text)):
                return True
        return any(_title_fold(t) == name for t in tags)
    return 2 * sum(1 for d in dirs if names_set(d)) >= len(dirs)


# DSC-10: at most this many sibling folders are tried as one set, and at most this many whole-set
# or leading-run searches are made for one (each costs lookups)
SET_TRIAL_MAX_DISCS = 8
SET_TRIAL_MAX_SEARCHES = 3
# DSC-10: what an artist's folder may say besides the artist's name ("Band - Discography [FLAC]")
_ARTIST_FOLDER_WORDS = {"discography", "albums", "album", "collection", "studio", "complete", "works",
                        "the", "flac", "mp3", "lossless", "cd", "cds", "music", "and", "other", "others",
                        "live", "singles", "eps", "ep", "compilations", "box", "sets", "rips"}


def _artist_folder(folder: Path, artist: str) -> bool:
    """DSC-10: FOLDER is named as ARTIST's folder ("Billy Joel", "Billy Joel - Discography
    [FLAC]"): what it holds are the artist's albums, never one set's discs."""
    def fold(t):                                        # ("The Beatles" and "Beatles", "Motörhead" and "Motorhead")
        t = _title_fold(unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode())
        return t[4:] if t.startswith("the ") else t
    name = fold(re.sub(r"[\(\[\{][^\)\]\}]*[\)\]\}]", " ", folder.name))
    who = fold(artist)
    if not who or not name.startswith(who):
        return False
    rest = [w for w in name[len(who):].split() if not re.fullmatch(r"(?:19|20)\d\d", w)]
    return all(w in _ARTIST_FOLDER_WORDS for w in rest)


def release_set_shape(folder: Path, dirs, files, keys, metas, found) -> str:
    """DSC-10: what FOLDER's sibling audio folders DIRS are to a set matched by its release:
    "discs" - the shape DSC-7 has without its "discs name the set" guard (disc folders "Disc 1
    - Title" ... numbered 1 to k, each once, no disc tag but each folder's own): a whole-set
    search may be tried; "numbered" - each folder's name begins with a number or a disc word
    ("01. Title", "CD3 Bonus"), each number once, in any order: only a release one of them is
    matched to may claim the others (its disc, not the folder's number, is each one's); "" -
    no set (an artist's folder, two artists, Various Artists, tracks in FOLDER itself, a folder
    of two albums or partly tagged, more than SET_TRIAL_MAX_DISCS folders)."""
    if folder in found or not 2 <= len(dirs) <= SET_TRIAL_MAX_DISCS:
        return ""
    if any(d.parent != folder or mc.version_layout(d.name) for d in dirs):
        return ""
    if any(folder in d.parents and d not in dirs for d in found):
        return ""                                       # (audio deeper down: not one level of discs)
    if any(k[0] != "tags" or not k[1] for k in keys.values()):
        return ""
    artists = {k[1] for k in keys.values()}
    if len(artists) != 1 or next(iter(artists)) in ("various artists", "various", "va"):
        return ""
    artist = next(iter(artists))
    if _artist_folder(folder, artist):
        return ""
    numbers = {}
    for d in dirs:
        fs = [f for f in files if Path(f).parent == d]
        if not fs or len({keys[f] for f in fs}) != 1:
            return ""                                   # (a folder of two albums)
        m = _LEAD_NUMBER.match(d.name)
        if not m:
            return ""
        numbers[d] = int(m[1])
    if len(set(numbers.values())) != len(dirs):
        return ""
    discs = all(is_disc_dir(d) for d in dirs) and sorted(numbers.values()) == list(range(1, len(dirs) + 1))
    if discs:
        for d in dirs:
            tags = {_disc_tag(metas.get(f) or {})[0] for f in files if Path(f).parent == d}
            if not tags <= {"", str(numbers[d])}:
                discs = False                           # (a disc tag against the folder's number)
    return "discs" if discs else "numbered"


def _left_out(profile, box: Path, dirs, path) -> bool:
    """Engine._box_part_left_out for a profile: a folder of the set your profile excludes, or
    one the rules' Ignore remembered."""
    parts = set(dirs) | {box / d.relative_to(box).parts[0] for d in dirs}
    if any(profile.excluded_path(d, named=[path] if path else ()) for d in parts):
        return True
    try:
        st = ex.State()
        try:
            return any(st.ignored_item(d) for d in parts)
        finally:
            st.db.close()
    except Exception:
        return True                                  # (the ignores cannot be read: no box)


def _release_set_groups(eng, groups, found, metas, path) -> list:
    """DSC-10: the albums of sibling folders of one parent that may be one set by its release
    (release_set_shape) stay the albums they are - listed, watched and remembered each as
    before - and each knows the others (Group.siblings): Engine.identify_all plans them each
    alone and then joins those one release proves one (Engine._release_set). Only below the
    folder planned (a plan of one disc's folder plans it alone); a folder you excluded or
    ignored keeps the set apart."""
    by_parent = {}
    for g in groups:
        dirs = {Path(f).parent for f in g.files}
        if len(dirs) != 1 or g.discs or g.box:
            continue
        d = next(iter(dirs))
        if d.parent == d or not (d.parent == path or path in d.parent.parents):
            continue
        by_parent.setdefault(d.parent, []).append((d, g))
    for parent, members in by_parent.items():
        dirs = [d for d, _g in members]
        if len(set(dirs)) != len(dirs):
            continue                                 # (a folder of two albums)
        files = [f for _d, g in members for f in g.files]
        if set(files) != {f for d in found if d.parent == parent for f in found[d]}:
            continue                                 # (a file of the set no album of these holds)
        keys = album_keys(files, parent, metas)
        shape = release_set_shape(parent, dirs, files, keys, metas, found)
        if not shape or _left_out(eng.profile, parent, dirs, path):
            continue
        # (the parts in their folders' order: by the number each name begins with)
        parts = [g for _d, g in sorted(members, key=lambda m: _lead_key(m[0].name))]
        for g in parts:
            g.siblings, g.set_folder, g.set_shape = parts, parent, shape
    return groups


def one_release_names(a, b) -> bool:
    """DSC-11: two sibling folders' names may be one release's - their titles (without the year
    in front and the brackets: "1973 - Piano Man 50th Anniversary Deluxe Edition [2024 SACD]")
    the same, or the shorter the longer's first words, half of them at least ("Piano Man 50th
    Anniversary Deluxe [2024 CD]"). Only such folders are read and looked up together."""
    x, y = _title_fold(_bare_name(a)).split(), _title_fold(_bare_name(b)).split()
    if not x or not y:
        return False
    if len(x) > len(y):
        x, y = y, x
    return y[:len(x)] == x and 2 * len(x) >= len(y)


_ALBUM_FOLDER_NAME = re.compile(r"^\s*(?:19|20)\d\d\s+-\s+\S")


def titled_disc_set(parent_name, names, parent_audio=False) -> bool:
    """DSC-11: a folder named as an album ("2016 - The Fall Of A Rebel Angel (Deluxe Edition)
    (... 570 934-4)": a year, " - ", the title), with no music of its own, holding 2 to
    SET_TRIAL_MAX_DISCS album-like sub-folders named after their discs' own titles ("The Album",
    "The Story"): those may be one release's discs, whatever their names - read and looked up
    together, and joined only when each matches one release's different discs for sure (as any
    of DSC-11's)."""
    return (not parent_audio and bool(_ALBUM_FOLDER_NAME.match(str(parent_name or "")))
            and 2 <= len(names) <= SET_TRIAL_MAX_DISCS)


def one_release_pairs(names) -> list:
    """DSC-11: the pairs of NAMES (folder names, in one parent) one_release_names takes - found
    by each name's leading words, never by trying every pair (a library's root of thousands of
    artist folders is asked too)."""
    words = {n: tuple(_title_fold(_bare_name(n)).split()) for n in names}
    by = {}
    for n, w in words.items():
        if w:
            by.setdefault(w, []).append(n)
    out = []
    for n, w in words.items():
        for k in range((len(w) + 1) // 2, len(w) + 1):
            for m in by.get(w[:k], ()):
                if m != n and (k < len(w) or n < m):
                    out.append((m, n))
    return out


def _one_release_groups(eng, groups, path) -> list:
    """DSC-11: album folders side by side in one parent (any parent, an artist's folder too),
    whose names may be one release's (one_release_names), each an album of its own folder:
    they stay the albums they are - listed, watched and remembered each as before - and each
    knows the others (Group.siblings, set_shape "albums"): Engine.identify_all plans them
    each alone and then joins those each matched for sure to one release, its different discs
    or layers (Engine._release_folders). Never a folder of two albums, a set of disc folders
    (DSC-1), a folder joined with its deluxe sibling, DSC-10's sets, a folder excluded or
    ignored; only below the folder planned."""
    per_folder = {}
    for g in groups:
        per_folder[g.folder] = per_folder.get(g.folder, 0) + 1
    by_parent = {}
    for g in groups:
        d = g.folder
        if g.siblings or g.discs or g.box or per_folder[d] != 1 or d.parent == d:
            continue
        if not (d.parent == path or path in d.parent.parents):
            continue
        by_parent.setdefault(d.parent, []).append(g)
    for parent, members in by_parent.items():
        # (most folders' names pair with none: asked first, before each file of them is looked at)
        # (DSC-11: or the discs of an album folder named by their own titles, the folder holding
        # no music of its own and nothing but them - titled_disc_set)
        titled = not one_release_pairs([g.folder.name for g in members]) and titled_disc_set(
            parent.name, members, parent_audio=parent in per_folder) and not any(
            g.folder.parent == parent and g not in members for g in groups)
        if len(members) < 2 or not (titled or one_release_pairs([g.folder.name for g in members])):
            continue
        members = [g for g in members                # (a deluxe sibling's files joined in: never)
                   if all(Path(f).parent == g.folder or g.folder in Path(f).parents for f in g.files)]
        if len(members) < 2:
            continue
        up = list(range(len(members)))

        def top(i):
            while up[i] != i:
                up[i] = up[up[i]]
                i = up[i]
            return i
        at = {g.folder.name: i for i, g in enumerate(members)}
        for a, b in (one_release_pairs(list(at)) if not titled else
                     [(members[0].folder.name, g.folder.name) for g in members[1:]]):
            up[top(at[b])] = top(at[a])
        clusters = {}
        for i, g in enumerate(members):
            clusters.setdefault(top(i), []).append(g)
        for parts in clusters.values():
            if len(parts) > SET_TRIAL_MAX_DISCS:
                (eng._logger() if hasattr(eng, "_logger") else logging.getLogger(__name__)).info(
                    "%s: %d album folders whose names may be one release's: more than %d, each planned alone",
                    parent, len(parts), SET_TRIAL_MAX_DISCS)
            if not 2 <= len(parts) <= SET_TRIAL_MAX_DISCS:
                continue
            parts = sorted(parts, key=lambda g: str(g.folder))
            if _left_out(eng.profile, parent, [g.folder for g in parts], path):
                continue
            for g in parts:
                g.siblings, g.set_folder, g.set_shape = parts, parent, "albums"
    return groups


def _bare_name(name) -> str:
    """A folder name or title without its year in front and its brackets ("1992 - Title (2CD)
    [Label]" -> "Title")."""
    name = str(name or "")
    while True:
        cut = _NAME_EXTRAS.sub("", name, count=1)
        if cut == name:
            return name
        name = cut


def titled_box(box: Path, found: dict, metas: dict) -> bool:
    """DSC-6: a folder holding two or more albums' folders that it is named after, joined ("1992 -
    The Stranger & An Innocent Man" holding "1977 - The Stranger" and "1983 - An Innocent Man"),
    is one album - the set they were issued in (a twofer, a "2 albums on 2 CDs" box) - when every
    sign agrees: the folder has no tracks of its own; each of its audio folders is a sub-folder
    of it; each holds one album by its tags, all by one album artist, each a different title;
    and its name, its year and brackets aside, is exactly those titles joined by "&", "+", "/" or
    "and" (each once, in any order). Its discs are numbered as the release found lists them (the
    folders' order is no disc order: the discs of a twofer may be packed the other way round).
    An artist's folder ("Billy Joel") or a folder named otherwise stays apart, as before."""
    if box in found or not _TITLE_JOIN.search(_bare_name(box.name)):
        return False                                    # (the name first: no titles joined, no twofer)
    dirs = [d for d in found if box in d.parents]
    if len(dirs) < 2 or any(d.parent != box for d in dirs):
        return False
    files = [f for d in dirs for f in found[d]]
    keys = album_keys(files, box, metas)
    if any(k[0] != "tags" or not k[1] for k in keys.values()) or len({k[1] for k in keys.values()}) != 1:
        return False                                    # (untagged, or two artists' albums)
    titles = []
    for d in dirs:
        own = {keys[f] for f in found[d]}
        if len(own) != 1:
            return False                                # (a folder of two albums)
        # (an edition in brackets aside: "Streetlife Serenade [1986 Reissue]", as a tagger named it)
        titles.append(_title_fold(_bare_name(next(iter(own))[2])))
    if len(set(titles)) != len(titles) or not all(titles):
        return False
    parts = [_title_fold(p) for p in _TITLE_JOIN.split(_bare_name(box.name)) if _title_fold(p)]
    return sorted(parts) == sorted(titles)




volume_number = ma.volume_number          # DSC-9 (match_albums: read_album numbers the discs by it)
_names_volumes = ma.names_volumes


# DSC-9: a catalogue number that names none ("none", Discogs's for a release without one)
_NO_CODE = {"", "none", "n/a", "na", "nocat", "-", "0", "unknown"}


def volume_set(box: Path, found: dict, metas: dict) -> bool:
    """DSC-9: a folder holding a set's volumes in folders of their own ("Volume 1", "Volume 2"),
    each tagged as an album of its own ("Greatest Hits MFSL I", "... II"), is one album - the
    set - when every sign agrees: its name says it holds k volumes ("Volume I & II", "Vol. 1-2",
    "2 Hybrid SACD", "2CD": every count or range in it saying k); it has no tracks of its own;
    each of its audio folders is a sub-folder of it named as a volume, numbered 1 to k, each
    once; each holds one album by its tags with one disc tag, all by one album artist; the
    volumes are not shown to be separate releases (a series pack "Vol. 1-2" of two releases:
    their barcodes or catalogue numbers differ, or their years do, with no code shared); and
    their disc tags, where they are one numbering of a set (every volume tagged, one total),
    number them as the volumes (that total k or none) - or say disc 1 on every volume, each
    numbered from track 1. Disc tags that are no one numbering (half tagged, totals differing:
    "1/2" beside "3/4") say nothing; the volume number is the disc (match_albums.read_album).
    A series' volumes in a folder named otherwise stay apart."""
    if box in found or not ma.named_volume_count(box.name):
        return False                                    # (the name first: cheap, and most folders fail it)
    dirs = [d for d in found if box in d.parents]
    k = len(dirs)
    if k < 2 or any(d.parent != box for d in dirs):
        return False
    if sorted(volume_number(d) for d in dirs) != list(range(1, k + 1)) or not _names_volumes(box.name, k):
        return False
    files = [f for d in dirs for f in found[d]]
    keys = album_keys(files, box, metas)
    if any(k_[0] != "tags" or not k_[1] for k_ in keys.values()) or len({k_[1] for k_ in keys.values()}) != 1:
        return False                                    # (untagged, or two artists' albums)
    discs, totals, years, barcodes, catnos, restart, stale = {}, set(), [], [], [], {}, {}
    for d in dirs:
        if len({keys[f] for f in found[d]}) != 1:
            return False                                # (a folder of two albums)
        ms = [metas.get(f) or {} for f in found[d]]
        nums = {_disc_tag(m)[0] for m in ms}
        if len(nums) != 1:
            return False                                # (a folder of two discs, or partly tagged)
        discs[d] = next(iter(nums))
        totals |= {_disc_tag(m)[1] for m in ms}
        years.append({_first(m, "date")[:4] for m in ms} - {""})
        barcodes.append({mc.norm_code(_first(m, "barcode")) for m in ms} - {""})
        catnos.append({mc.norm_code(_first(m, "catalognumber")) for m in ms} - _NO_CODE)
        stale[d] = all(_first(m, "tagmender") for m in ms)     # (an earlier job's tags: its disc numbers too)
        restart[d] = "1" in {_first(m, "tracknumber").partition("/")[0].strip().lstrip("0") or "0" for m in ms}

    def apart(codes):                                   # (every volume carries one, and no two share)
        return all(codes) and not set.intersection(*codes)
    # (barcodes first, when every volume has one; else catalogue numbers - "none" is no number)
    codes = barcodes if all(barcodes) else catnos      # (a barcode on some volumes only tells nothing apart)
    if apart(codes):
        return False                                    # (each volume its own release)
    shared = all(codes) and set.intersection(*codes)
    if not shared and all(len(y) == 1 for y in years) and len({next(iter(y)) for y in years}) > 1:
        return False                                    # (released years apart: a series' volumes)
    # (a total counting other than the set - "1 of 3" on a volume of two - is another release's,
    # unless an earlier job of TagMender's wrote it: "3 of 4" from a release listing each disc twice)
    for d in dirs:
        ts = {_disc_tag(metas.get(f) or {})[1] for f in found[d]} - {"", "1", str(k)}
        if ts and not stale[d]:
            return False
    if all(discs.values()) and len(totals) == 1:        # (one numbering of a set: it must be this one)
        if len(set(discs.values())) == 1:
            return discs[dirs[0]] == "1" and all(restart.values())
        total = next(iter(totals))
        return total in ("", str(k)) and all(v == str(volume_number(d)) for d, v in discs.items())
    return True


# DSC-5: the number a box's folder begins with, the order its discs are in: "01. (1980) - ...",
# "CD3. 1992. ...", "CD02-03 1973 ..." (2), "CD-1 (KZ 2)", "Part 1"
_LEAD_NUMBER = re.compile(r"^(?:(?:cd|disc|disk|part|pt\.?)\s*[-_.]?\s*)?(\d{1,3})(?!\d)", re.I)


def _lead_key(name):
    """DSC-5: a folder's place among its siblings - by the number its name begins with (any word
    of a disc or part before it: "01. Alpha" and "CD02-03 Beta" are 1 and 2), else by its name."""
    m = _LEAD_NUMBER.match(name)
    return (0, int(m[1]), []) if m else (1, 0, ma._natural_key(name))


def _box_discs(box: Path, found: dict, metas: dict):
    """DSC-5: (N, the disc numbers of box's audio folders in their order, whether every file
    gives a disc total) for numbered_box and box_still_arriving, when every other sign of a box
    agrees; else None."""
    n = box_cd_count(box.name)
    if n < 2 or box in found:
        return None
    dirs = [d for d in found if box in d.parents]
    if not dirs:
        return None
    for d in dirs:
        depth = len(d.relative_to(box).parts)
        if depth > 2 or (depth == 2 and (not (is_disc_dir(d) or box_part_disc(d) is not None) or d.parent in found)):
            return None                                   # (DSC-3's "Part 1" of a range folder is a disc folder too)
    dirs.sort(key=lambda d: [_lead_key(x) for x in d.relative_to(box).parts])
    files = [f for d in dirs for f in found[d]]
    keys = album_keys(files, box, metas)
    if any(k[0] != "tags" or not k[1] for k in keys.values()) or len({k[1] for k in keys.values()}) != 1:
        return None                                       # (untagged, or two artists' albums)
    numbers = []
    for d in dirs:
        if len({keys[f] for f in found[d]}) != 1:
            return None                                   # (a folder of two albums)
        tags = {_disc_tag(metas.get(f) or {}) for f in found[d]}
        nums = {t[0] for t in tags}
        if len(nums) != 1 or not next(iter(nums)).isdigit():
            return None                                   # (no disc number, or two of them)
        if any(t[1].isdigit() and int(t[1]) < n for t in tags):
            return None                                   # (its own tags count fewer discs)
        numbers.append(int(next(iter(nums))))
    return n, numbers, all(t[1].isdigit() for f in files for t in [_disc_tag(metas.get(f) or {})])


def box_still_arriving(folder, skips=None, left_out=None) -> str:
    """DSC-5 / DSC-1: why one folder of a box set being copied in waits (the watcher): the
    folder above it (or the one above that) names its CD count N, its audio folders are its
    discs as numbered_box has them, numbered 1 .. k rising in the folders' order with k below N,
    and every file's disc total is N or more - "discs 3, 4 of 4 are not here yet". "" for any
    other folder (an artist's or a "Compilations" folder of albums: their totals count fewer
    discs, or none). left_out(box, folders): a folder of it you excluded or ignored
    (Engine._box_part_left_out) - such a box is never one album, so nothing of it waits."""
    folder = Path(folder)
    skips = skips or _skips_folder
    for box in (folder.parent, folder.parent.parent):
        if not box_cd_count(box.name):
            continue
        found = {}
        try:
            for dirpath, dirs, names in os.walk(box):
                dirs[:] = sorted(x for x in dirs if not skips(x))
                fs = sorted(Path(dirpath) / x for x in names
                            if Path(x).suffix.lower() in ex.AUDIO_EXT and not x.startswith("."))
                if fs:
                    found[Path(dirpath)] = fs
        except OSError:
            return ""
        got = _box_discs(box, found, {f: ex.read_meta(f) for fs in found.values() for f in fs})
        if not got:
            continue
        if left_out is not None and left_out(box, list(found)):
            return ""
        n, numbers, totals = got
        if totals and numbers and numbers == list(range(1, len(numbers) + 1)) and len(numbers) < n:
            gone = list(range(len(numbers) + 1, n + 1))
            words = ", ".join(map(str, gone))
            return f"disc{'s' if len(gone) > 1 else ''} {words} of {n} {'are' if len(gone) > 1 else 'is'} not here yet"
    return _named_set_arriving(folder, skips, left_out)                 # DSC-6, DSC-7


def _named_set_arriving(folder: Path, skips, left_out=None) -> str:
    """DSC-6 / DSC-7: why a folder of a set still being copied in waits. DSC-6: its folder is one
    of the albums its parent is named after ("1977 - The Stranger" in "1992 - The Stranger & An
    Innocent Man"), the parent has no tracks of its own and holds fewer albums' folders than its
    name joins - "An Innocent Man is not here yet". DSC-7: it is "Disc k" of a folder of disc
    folders numbered 1, 2 ... with no gap, and its files' disc totals count more discs than are
    here - "discs 4, 5 of 5 are not here yet". "" otherwise (nothing says how many are coming)."""
    # (the watcher asks about the set's folder, a DSC-7 group's: its highest-numbered disc folder)
    try:
        inner = [(int(m[1]), x) for x in folder.iterdir() if x.is_dir() and is_disc_dir(x)
                 for m in [_LEAD_NUMBER.match(x.name)] if m]
    except OSError:
        inner = []
    if inner:
        return _named_set_arriving(max(inner)[1], skips, left_out)
    box = folder.parent

    def has_audio(d):
        try:
            return any(f.suffix.lower() in ex.AUDIO_EXT for f in d.iterdir() if f.is_file())
        except OSError:
            return False
    try:
        # (folders holding audio only: "Scans/" or "Artwork/" beside the albums is none of them)
        subs = sorted(x for x in box.iterdir() if x.is_dir() and not skips(x.name) and has_audio(x))
        if any(x.is_file() and x.suffix.lower() in ex.AUDIO_EXT for x in box.iterdir()):
            return ""
    except OSError:
        return ""
    if left_out is not None and left_out(box, subs):
        return ""
    parts = [_title_fold(p) for p in _TITLE_JOIN.split(_bare_name(box.name)) if _title_fold(p)]
    mine = _title_fold(_bare_name(folder.name))
    if len(parts) > 1 and mine in parts:
        have = {_title_fold(_bare_name(x.name)) for x in subs}
        if have <= set(parts) and len(have) < len(parts):
            gone = [p for p in _TITLE_JOIN.split(_bare_name(box.name)) if _title_fold(p) and _title_fold(p) not in have]
            return f"{' and '.join(x.strip() for x in gone)} {'are' if len(gone) > 1 else 'is'} not here yet"
    # DSC-9: "Volume 1" of a folder naming k volumes, fewer of them here - "volume 2 of 2 is not here yet"
    k, n = ma.named_volume_count(box.name), ma.volume_number(folder)
    if k >= 2 and 1 <= n <= k:
        have = sorted(ma.volume_number(x) for x in subs)
        coded = False
        try:
            coded = any(_first(ex.read_meta(f), "barcode") for x in subs for f in x.iterdir()
                        if f.suffix.lower() in ex.AUDIO_EXT)
        except OSError:
            pass
        # (a volume carrying a barcode is a release of its own - a series pack's -: nothing to wait for)
        if not coded and 0 not in have and have == list(range(1, len(have) + 1)) and len(have) < k:
            gone = list(range(len(have) + 1, k + 1))
            return f"volume{'s' if len(gone) > 1 else ''} {', '.join(map(str, gone))} of {k} " \
                   f"{'are' if len(gone) > 1 else 'is'} not here yet"
    m = _LEAD_NUMBER.match(folder.name)
    if not (m and is_disc_dir(folder)):
        return ""
    numbers = sorted(int(x[1]) for x in (_LEAD_NUMBER.match(s.name) for s in subs if is_disc_dir(s)) if x)
    if numbers != list(range(1, len(numbers) + 1)):
        return ""
    # (its files tagged as its own disc k: a range folder's "CD1" holding disc 3 of 4 is no set)
    totals, own = set(), set()
    for f in folder.iterdir():
        if f.suffix.lower() in ex.AUDIO_EXT:
            num, t = _disc_tag(ex.read_meta(f))
            own.add(num)
            if t.isdigit():
                totals.add(int(t))
    if own != {str(int(m[1]))}:
        return ""
    if len(totals) == 1 and next(iter(totals)) > len(numbers):
        n = next(iter(totals))
        gone = list(range(len(numbers) + 1, n + 1))
        return f"disc{'s' if len(gone) > 1 else ''} {', '.join(map(str, gone))} of {n} " \
               f"{'are' if len(gone) > 1 else 'is'} not here yet"
    return ""


def version_dirs(parent: Path, metas=None, skips=None) -> list:
    """The folders of parent that hold versions of one disc of one album, each named after
    its channel layout ("2.0/" and "5.0/", "Stereo/" and "Multichannel/": an SACD's two areas,
    a DVD-Audio's mixes) - they belong to parent, as disc folders do, and parent's name names
    the release. [] unless two or more such folders, each a different layout, hold all the
    audio below parent (none in parent itself, none in another folder), and every file is
    tagged as one same album that is not its folder's name (an album called "Stereo").
    metas: {file: tags} already read (albums_in); skips: the folders never looked into (a
    profile's folder_skipper; hidden and TagMender's own ones without it)."""
    parent = Path(parent)
    skips = skips or _skips_folder
    try:                     # (two or more layout-named folders first: never a library walked for one)
        named = [x for x in parent.iterdir() if x.is_dir() and not skips(x.name) and mc.version_label(x.name)]
    except OSError:
        return []
    if len(named) < 2:
        return []
    audio = {}
    try:
        for dirpath, dirs, names in os.walk(parent):
            dirs[:] = [x for x in dirs if not skips(x)]
            fs = sorted(Path(dirpath) / n for n in names
                        if Path(n).suffix.lower() in ex.AUDIO_EXT and not n.startswith("."))
            if fs:
                audio[Path(dirpath)] = fs
    except OSError:
        return []
    lays = [mc.fold(mc.version_label(d.name)) if d.parent == parent else "" for d in audio]
    if len(audio) < 2 or "" in lays or len(set(lays)) != len(lays):
        return []
    metas = dict(metas or {})
    files = [f for fs in audio.values() for f in fs]
    for f in files:
        if f not in metas:
            metas[f] = ex.read_meta(f)
    keys = set(album_keys(files, parent, metas).values())
    if not _one_album_titled(keys):
        return []
    if any(mc.fold(_first(metas[f], "album")) == mc.fold(f.parent.name) for f in files):
        return []
    return sorted(audio)


def _one_album_titled(keys) -> bool:
    """album_keys' keys are one album tagged by one artist: one key, or (versions of one disc
    tagged apart) titles that differ only in a closing mark of their version or edition
    ("Child Is Father To The Man [SACD 2ch DSD64]" beside "Child Is Father to the Man [HD]")."""
    keys = set(keys)
    if not keys or any(k[0] != "tags" for k in keys):
        return False
    if len(keys) == 1:
        return True
    bare = {_without_version_marks(k[2]) for k in keys}
    return len({k[1] for k in keys}) == 1 and len(bare) == 1 and "" not in bare


# MIX-3: the words a closing mark of a version or edition is made of - format, layout, audio
# and edition year ("[SACD 2ch DSD64 24bit 352khz]", "[HD]", "[2015 Remaster]", "[1980 Vinyl Rip]" another
# run wrote); never a title's
# own words ("[Live]", "(Volume 1)") nor "Mono" / "Stereo" (an album may be named after its mix);
# a catalogue number may stand beside them
_MARK_WORD = re.compile(
    r"sacd|hybrid|dsd\d*|dst\d*|dxd|pcm|mch|iso|flac|hd|hi-?res|uhd|\d+ch|[2-9]\.[01]|multi-?channel|"
    r"surround|\d+-?bit|\d+(?:\.\d+)?-?k?hz|24-\d+|\d+-24|remaster(?:ed)?|rm|(?:19|20)\d\d|rip|disc|layer|vinyl|lp|cd|reissue")


# (the disc or format words beside which "Stereo" / "Mono" names a rip's layer: _without_version_marks)
_LAYER_WORD = re.compile(r"sacd|hybrid|dsd\d*|dst\d*|dxd|mch|iso|rip|\d+ch|[2-9]\.[01]|multi-?channel|surround|layer")


def _without_version_marks(title) -> str:
    """The album title without the closing brackets that only mark its version or edition."""
    t = str(title or "")
    while True:
        m = re.search(r"\s*[\(\[\{]([^\)\]\}]*)[\)\]\}]\s*$", t)
        words = re.findall(r"[a-z0-9.-]+", m.group(1).casefold()) if m else []
        marks = [w for w in words if _MARK_WORD.fullmatch(w.strip(".-"))]
        if any(_LAYER_WORD.fullmatch(w.strip(".-")) for w in marks):
            # (MED-22c: "Stereo" / "Mono" beside a disc or format word is the rip's layer, not the
            # album's name: "Kodo One Earth Tour Special [Stereo DSD Rip]" beside "... [Multichannel
            # 5.1]"; alone, "(Mono)", or beside a year or remaster only - "(Mono 2009 Remaster)"
            # and "(Stereo 2009 Remaster)" are two albums - it stays the title's)
            marks += [w for w in words if w in ("stereo", "mono")]
        # (beside those words, only a catalogue number's parts: "[SACD AFZ5 198 2ch DSD64]")
        if not marks or not all(w in marks or re.search(r"\d", w) for w in words):
            return mc.fold(t)
        t = t[:m.start()]


def is_version_dir(d: Path, metas=None, skips=None) -> bool:
    """A version folder of the folder above it (version_dirs; skips: the profile's folder_skipper)."""
    d = Path(d)
    return bool(mc.version_layout(d.name)) and d in version_dirs(d.parent, metas, skips)


def set_dir(d: Path, skips=None) -> bool:
    """A disc folder or a version folder (is_disc_dir, is_version_dir): one part of the album
    in the folder above it."""
    return is_disc_dir(d) or is_version_dir(d, skips=skips)


@dataclass
class Group:
    folder: Path
    files: list = field(default_factory=list)
    others: list = field(default_factory=list)     # files in the same folder that belong elsewhere
    single: bool = False
    # DSC-1: the disc folders of a set filed side by side ("Album CD1", "Album CD2"), in disc
    # order - folder is the first; one such folder alone names a set too. [] for any other album.
    discs: list = field(default_factory=list)
    # DSC-5: a box set's folder whose discs sit in folders of its own named as albums (numbered_box)
    box: bool = False
    # DSC-10: the albums of sibling folders that may be one set by its release, this one among
    # them (each planned alone first; Engine._release_set joins those a release proves one), the
    # folder holding them and their shape (release_set_shape). [] / None / "" otherwise.
    siblings: list = field(default_factory=list, compare=False, repr=False)
    set_folder: Path | None = field(default=None, compare=False)
    set_shape: str = ""
    # MED-22c: why a copy of a disc's layers, read apart from the CD layer's, is held ("" for none)
    layer_hold: str = field(default="", compare=False)


def _slots_of(album, cand, sc):
    """For a release you chose with more tracks than the files: each file's own track of it
    (match_core.slot_view, as the matcher's missing-track match has them), so the files are
    tagged as those tracks and the rest noted as missing. {} when the files are not fewer or
    have no track numbers to place them by."""
    # (TRK-2: or the files are its tracks in another order - each file's own track, none missing)
    view = sc["cand"] if (sc["cand"].get("slots") is not None
                          and (sc.get("missing") or sc["cand"].get("reordered"))) else mc.slot_view(album, cand)
    if not view or not (view.get("missing") or view.get("reordered")):
        return {}
    return {"slots": list(view["slots"]), "missing": dict(view["missing"]),
            "release_tracks": view.get("full_tracks") or len(cand.get("tracks") or [])}


def _has_htoa(album):
    """A disc of the album has its hidden track before track 1 as a file numbered 0
    (match_albums.htoa_file)."""
    discs = {}
    for t in album.get("tracks") or ():
        discs.setdefault(t.get("disc") or 1, []).append(t)
    return any(ma.htoa_file(tracks) is not None for tracks in discs.values())


def _first_text(v):
    """A tag's first value as text ("" for none): ["Title"] or "Title"."""
    if isinstance(v, (list, tuple)):
        return str(v[0]) if v else ""
    return str(v or "")


def _missing_of(best):
    """The tracks of the release missing from the folder, as TAGMENDER_MISSING has them
    ("2, 4, 5, 7-10, 14"; "1:3, 2:5" on several discs), or "" when none are."""
    missing = {int(d): v for d, v in (best.get("missing") or {}).items() if v}
    return mc.missing_text(missing) if best.get("slots") and missing else ""


def evidence_of(out, best):
    """What agreed, then what the shops sell under the barcode, what gnudb holds for the disc
    and an AcoustID disagreement: clues for whoever reviews the album, kept out of the
    match's own notes so nothing reads them as proof."""
    out = out or {}
    # (FLD-2: the artist and album read from the folder name, when the lookup used that reading)
    return (list(best.get("notes") or []) + list(out.get("reading_notes") or [])
            + list(out.get("shop_notes") or []) + list(out.get("gnudb_notes") or [])
            + list(out.get("acoustid_notes") or []) + list(out.get("ai_notes") or []))


def scans_of(prop, out):
    """Reporting only: the matcher's account of the album's scans and of how its pick was
    found (match_albums.scans_report / found_via), on PROP when PROP is about that pick: an
    exact release it names, its album only or Partial tags from the matcher's candidates,
    or the pick an unresolved album was left with for review. The planner reads one thing of it:
    plan._files_on_evidence, whether the pick's own number is printed on your copy."""
    out = out or {}
    best = out.get("best") or {}
    if not best or ("scans" not in out and "via" not in out):
        return
    tier = planmod.tier_of(prop)
    if tier in ("ignored", "recording") or str(prop.provider or "").startswith(("web", "acoustid")):
        return
    if tier == "exact" and str(prop.release_id or "") not in {
            str(best.get(k) or "") for k in ("id", "mb_id", "discogs_id")} - {""}:
        return                     # another release than the matcher's pick (a version split ...)
    if out.get("scans"):
        prop.scans = dict(out["scans"])
    prop.via = str(out.get("via") or "")


def _with_deluxe(profile, groups, path, skip=()) -> list[Group]:
    """A "<title> (Deluxe Edition)" folder that completes an album (match_albums.deluxe_sibling,
    R11: Rotting Christ - The Heretics, tracks 11 and 12 saved apart) is one album with it, as
    the matcher reads it: its files join the album's group, and it is no group of its own.
    A sibling that is a mixed folder, or that the scan left out (skipped, or excluded by
    the profile), is left as it is; one outside the path scanned (only the main folder
    named) is read from its folder. skip: real paths, as albums_in has them."""
    folders = {g.folder for g in groups}
    whole = {g.folder: g for g in groups if not g.others}
    merged = set()
    for g in list(whole.values()):
        sib = ma.deluxe_sibling(g.folder)
        if sib is None or sib in merged or (sib in folders and sib not in whole):
            continue                                     # a mixed folder there: left as it is
        if sib not in folders and not _may_read(profile, sib, path, skip):
            continue                                     # left out of the scan: not ours to tag
        theirs = whole[sib].files if sib in whole else ma._audio_files(sib)
        g.files = g.files + [f for f in theirs if f not in g.files]
        g.single = False
        merged.add(sib)
    return [g for g in groups if g.folder not in merged]


def _may_read(profile, folder, path, skip) -> bool:
    """Whether a folder the scan did not group may join an album: only one outside the
    path scanned, neither skipped nor excluded by the profile."""
    folder = Path(folder)
    if folder == path or path in folder.parents:
        return False                                 # under the scan, yet no group: left out on purpose
    real = os.path.realpath(str(folder))
    if any(real == x or real.startswith(x.rstrip("/") + "/") for x in skip):
        return False
    return not profile.excluded_path(folder, named=[path])


class _Scan:
    """How albums_in reads one folder tree: the folders it never looks into (skip: real paths,
    the profile's skip_paths too; skips: the profile's Skip These Folders, hidden folders,
    TagMender's own) and each folder's real path, resolved once."""

    def __init__(self, eng, skip=()):
        prof = eng.profile
        self.skip = {os.path.realpath(str(x)) for x in skip}
        if hasattr(prof, "skip_paths"):              # (where unidentified albums are moved to)
            self.skip |= prof.skip_paths()
        self.skips = prof.folder_skipper() if hasattr(prof, "folder_skipper") else _skips_folder
        self.real_of = {}

    def skipped(self, dirpath, d) -> bool:
        if not self.skip:
            return False
        child = os.path.join(dirpath, d)
        if os.path.islink(child):
            real = os.path.realpath(child)
        else:                                        # (its folder's real path and its own name)
            top = self.real_of.get(dirpath)
            if top is None:
                top = self.real_of[dirpath] = os.path.realpath(dirpath)
            real = os.path.join(top, d)
        self.real_of[child] = real
        return real in self.skip

    def read(self, dirpath, dirs, names) -> list:
        """One folder of os.walk as albums_in reads it: DIRS narrowed (in place) to the folders it
        looks into, in order; the folder's audio files, in order."""
        dirs[:] = sorted(d for d in dirs if not self.skips(d) and not self.skipped(dirpath, d))
        d = Path(dirpath)
        return sorted(d / n for n in names if Path(n).suffix.lower() in ex.AUDIO_EXT and not n.startswith("."))

    def listed(self, folder: Path):
        """(its folders looked into, its audio files, every name in it) of one folder, as the
        walk reads it; None when it cannot be read."""
        for dirpath, dirs, names in os.walk(folder):
            every = list(dirs) + list(names)
            return dirs, self.read(dirpath, dirs, names), every
        return None


# ---- PRF-3: a scan read piece by piece ----------------------------------------------------------
# albums_in groups the audio folders below a path by what their names and tags say of each
# other (_group_unit). What can reach past one sub-folder of a folder P (with everything above P
# read the same way):
#   - P's name: a box naming its CD count (DSC-5), a volume set (DSC-9), a twofer (DSC-6), a disc
#     range (DSC-3) - P is then one piece with everything below it;
#   - P's own tracks, and its sub-folders that may be its discs or versions (disc, version, disc-
#     range, range-part names): they may be P's album - "P's core", read first; and any album
#     below P whose tags are P's one album is P's too (the folder above an album tagged alike) -
#     so when P's core is one tagged album (own_key), P is one piece;
#   - sub-folders named as discs of one set side by side (DSC-1, sibling_disc stems) and an
#     album beside its "(Deluxe Edition)" folder (match_albums.deluxe_named): read together;
#   - two or more sub-folders whose names begin with a number, with no tracks in P (DSC-10: a
#     set by its release needs every audio folder of P - not bounded: P is one piece).
# Every other sub-folder is a piece of its own, read the same way in turn; its albums are the
# whole scan's, and come out as soon as no piece read later can hold an album before them.
_CORE = "\0core"


def _name_blocks(d: Path) -> bool:
    """P's name makes P one album of its sub-folders: a box, volumes, a twofer, a disc range."""
    return bool(box_cd_count(d.name) or ma.named_volume_count(d.name) or _DISC_RANGE.match(d.name)
                or _TITLE_JOIN.search(_bare_name(d.name)))


def _disc_like(d: Path, c: str) -> bool:
    """Sub-folder C of D may be one of D's discs or versions (its owner D)."""
    sub = d / c
    return bool(mc.version_layout(c) or _DISC_RANGE.match(c) or is_disc_dir(sub) or box_part_disc(sub) is not None)


def _walk_into(found: dict, top: Path, sc: _Scan) -> None:
    for dirpath, dirs, names in os.walk(top):
        audio = sc.read(dirpath, dirs, names)
        if audio:
            found[Path(dirpath)] = audio


def _unit_of(eng, path, sc, own, roots) -> list[Group]:
    """The albums of OWN's tracks (folder, its audio files) and every folder below ROOTS, together."""
    found = {}
    if own is not None and own[1]:
        found[own[0]] = own[1]
    for r in sorted(roots):
        _walk_into(found, r, sc)
    return _group_unit(eng, path, found, sc) if found else []


def _album_like(sc, folder: Path) -> bool:
    """DSC-11: FOLDER is shaped as an album's own folder, not an artist's: it holds music at its
    top or in disc folders ("Disc 02/", "CD1/") - never a folder of album folders ("Neil Young
    [FLAC]/1970 - Album/"), whatever its name says."""
    got = sc.listed(folder)
    if not got:
        return False
    dirs, audio, _names = got
    return bool(audio) or any(_disc_like(folder, x) for x in dirs)


def _one_release_units(eng, sc, d: Path, names) -> list:
    """DSC-11: the sub-folders of D read together because a release may join them
    (one_release_pairs): album-like folders only (_album_like), never in a watched folder, a
    library or the profile's own folders themselves (their artist folders: "Neil Young" and
    "Neil Young & Crazy Horse" are two artists), clusters of SET_TRIAL_MAX_DISCS at most (a
    larger one is read as before, and logged)."""
    pairs = one_release_pairs(names)                 # (first: most folders' names pair with none)
    if not pairs and _ALBUM_FOLDER_NAME.match(d.name) and 2 <= len(names) <= SET_TRIAL_MAX_DISCS:
        # (DSC-11: the discs of an album folder named by their own titles - titled_disc_set; its
        # name and size asked first, so an artist's folder at a library root lists nothing more)
        got = sc.listed(d)
        if got and not got[1]:
            kids = sorted(n for n in names if _album_like(sc, d / n))
            if titled_disc_set(d.name, kids):
                pairs = [(kids[0], k) for k in kids[1:]]
    if not pairs:
        return []
    try:
        import profiles
        prof = eng.profile
        roots = {os.path.realpath(os.path.expanduser(str(r))) for r in
                 profiles.filing_roots(getattr(prof, "data", prof)) if r}
        roots |= {os.path.realpath(os.path.expanduser(str(x.get("path"))))
                  for x in (prof.get("sources") or []) if isinstance(x, dict) and x.get("path")}
    except Exception:
        roots = set()
    if os.path.realpath(str(d)) in roots:
        return []
    like = {}

    def ok(c):
        if c not in like:
            like[c] = _album_like(sc, d / c)
        return like[c]
    up = {}

    def top(c):
        up.setdefault(c, c)
        while up[c] != c:
            up[c] = up[up[c]]
            c = up[c]
        return c
    for a, b in pairs:
        if ok(a) and ok(b):
            up[top(b)] = top(a)
    clusters = {}
    for c in up:
        clusters.setdefault(top(c), []).append(c)
    out = []
    for ms in clusters.values():
        if len(ms) > SET_TRIAL_MAX_DISCS:
            (eng._logger() if hasattr(eng, "_logger") else logging.getLogger(__name__)).info(
                "%s: %d folders whose names may be one release's (%s ...): more than %d, each read as before",
                d, len(ms), ", ".join(sorted(ms)[:3]), SET_TRIAL_MAX_DISCS)
            continue
        if len(ms) > 1:
            out.append(sorted(ms))
    return out


def _plan_folder(eng, path, sc, d: Path, got):
    """PRF-3: how folder D (its listing GOT) is read. None: as one piece with everything below
    it. Else (its core's albums, its core sub-folders, [(first sub-folder, [the sub-folders read
    together])] in order - one alone is planned in turn)."""
    dirs, audio, _names = got
    kids = [c for c in dirs if not os.path.islink(os.path.join(d, c))]   # (a linked folder: never walked)
    if not kids or _name_blocks(d):
        return None
    if not audio and sum(1 for c in kids if _LEAD_NUMBER.match(c)) >= 2:
        return None                                  # (DSC-10 may join them: not bounded)
    up = {c: c for c in kids}
    up[_CORE] = _CORE

    def top(c):
        while up[c] != c:
            up[c] = up[up[c]]
            c = up[c]
        return c

    def join(a, b):
        a, b = top(a), top(b)
        if a != b:
            if b == _CORE:
                a, b = b, a
            up[b] = a
    stems, discs = {}, set()
    for c in kids:
        if _disc_like(d, c):
            discs.add(c)
            join(_CORE, c)
            continue
        sd = sibling_disc(d / c)
        if sd is not None:
            s = mc.fold(sd[0])
            if s in stems:
                join(stems[s], c)                    # (DSC-1)
            else:
                stems[s] = c
    for n in kids:                                   # (deluxe_sibling's own match: re.I)
        if "deluxe" in n.casefold():
            for c in kids:
                if c != n and ma.deluxe_named(c, n):
                    join(c, n)
    # DSC-11: album folders whose names may be one release's are read together (a release may
    # join them: _one_release_groups)
    for ms in _one_release_units(eng, sc, d, [c for c in kids if c not in discs]):
        for c in ms[1:]:
            join(ms[0], c)
    members = {}
    for c in kids:
        members.setdefault(top(c), []).append(c)
    core = members.pop(_CORE, [])
    core_groups = []
    if audio or core:
        keys = {}
        found = {d: audio} if audio else {}
        for c in core:
            _walk_into(found, d / c, sc)
        core_groups = _group_unit(eng, path, found, sc, keys) if found else []
        mine = keys.get(d) or set()
        if len(mine) == 1 and next(iter(mine))[0] == "tags":
            return None                              # (an album below tagged as D's own is D's)
    parts = sorted((ms[0], ms) for ms in members.values())
    return core_groups, set(core), parts


def _scan_from(eng, path, sc, d: Path, got=None):
    """PRF-3: the albums below D (PATH, or a folder planned on its own), in albums_in's order,
    each as soon as nothing read later can come before it."""
    got = got if got is not None else sc.listed(d)
    if got is None:
        return
    with ex.meta_memo():                             # (D's core read once, also when D is one piece)
        plan = _plan_folder(eng, path, sc, d, got)
        whole = _unit_of(eng, path, sc, (d, got[1]), [d / c for c in got[0]
                                                      if not os.path.islink(os.path.join(d, c))]) \
            if plan is None else None
    if plan is None:
        yield from whole
        return
    core_groups, _core, parts = plan
    pending, n = [], 0
    for g in core_groups:
        heapq.heappush(pending, (g.folder, n, g))
        n += 1
    for first, ms in parts:
        here = d / first
        while pending and pending[0][0] < here:
            yield heapq.heappop(pending)[2]
        if len(ms) == 1:
            yield from _scan_from(eng, path, sc, here)
            continue
        with ex.meta_memo():
            gs = _unit_of(eng, path, sc, None, [d / c for c in ms])
        for g in gs:
            heapq.heappush(pending, (g.folder, n, g))
            n += 1
    while pending:
        yield heapq.heappop(pending)[2]


def _within_pieces(eng, path: Path, within, sc: _Scan):
    """PRF-3: what to read for the albums inside the folders WITHIN, as a scan of PATH has them:
    [(kind, folder, sub-folders)] - "all": everything below the folder (one planned on its own,
    or the piece holding the folder dropped), "core": a folder's core, "unit": sub-folders read
    together. Only the folders on the way down
    are listed, and the core of each (its own tracks, its disc folders) read. None: the whole
    scan (a folder holding PATH). A folder outside PATH or never looked into holds none."""
    rp = os.path.realpath(str(path)).rstrip(os.sep)
    plans, out = {}, []
    for w in within:
        rw = os.path.realpath(str(w))
        if rp == rw.rstrip(os.sep) or rp.startswith(rw.rstrip(os.sep) + os.sep):
            return None
        if not rw.startswith(rp + os.sep):
            continue
        d, piece = path, None
        for part in rw[len(rp) + 1:].split(os.sep):
            if d not in plans:
                got = sc.listed(d)
                with ex.meta_memo():
                    plans[d] = (got, _plan_folder(eng, path, sc, d, got) if got is not None else None)
            got, plan = plans[d]
            if got is None:
                break
            if plan is None:
                piece = ("all", d, ())                   # (the piece holding it: all below d)
                break
            core_groups, core, parts = plan
            if part not in got[0] or os.path.islink(d / part):
                break                                # (never looked into: no album of it)
            if part in core:
                piece = ("core", d, ())
                break
            ms = next(ms for _f, ms in parts if part in ms)
            if len(ms) > 1:
                piece = ("unit", d, tuple(ms))
                break
            d = d / part
        else:
            piece = ("all", d, ())
        if piece is not None and piece not in out:
            out.append(piece)
    whole = [p[1] for p in out if p[0] == "all"]
    keep = [p for p in out if not any(w == p[1] and p[0] != "all" or w in p[1].parents for w in whole)]
    return keep, plans


def _albums_iter(eng, path, skip=(), within=None, walked=None):
    """Engine.albums_iter. walked: a list given is told each folder read whole (the scan's own
    path, or the folders read for WITHIN)."""
    path = Path(os.path.abspath(Path(path).expanduser()))  # plans and journals hold absolute paths
    getattr(eng, "__dict__", {}).pop("_set_memo", None)   # (DSC-10: the sets as this scan finds them)
    if path.is_file():
        if walked is not None:
            walked.append(path)
        yield Group(path.parent, [path], [], single=True)
        return
    sc = _Scan(eng, skip)
    got = None if within is None else _within_pieces(eng, path, within, sc)
    if got is None:
        if walked is not None:
            walked.append(path)
        yield from _scan_from(eng, path, sc, path)
        return
    pieces, plans = got
    out = []
    for kind, d, ms in pieces:
        if kind == "all":
            if walked is not None:
                walked.append(d)
            out += list(_scan_from(eng, path, sc, d, plans.get(d, (None,))[0]))
        elif kind == "core":
            if walked is not None:
                walked.extend(d / c for c in plans[d][1][1])
            out += plans[d][1][0]
        else:
            if walked is not None:
                walked.extend(d / c for c in ms)
            with ex.meta_memo():
                out += _unit_of(eng, path, sc, None, [d / c for c in ms])
    out.sort(key=lambda g: g.folder)                 # (albums_in's order: by album folder; stable)
    yield from out


def _albums_whole(eng, path, skip=()) -> list[Group]:
    """albums_in as one unit: every folder below PATH read first, then grouped at once (the
    scan before PRF-3; what its units must add up to)."""
    path = Path(os.path.abspath(Path(path).expanduser()))
    getattr(eng, "__dict__", {}).pop("_set_memo", None)
    if path.is_file():
        return [Group(path.parent, [path], [], single=True)]
    sc, found = _Scan(eng, skip), {}
    for dirpath, dirs, names in os.walk(path):
        audio = sc.read(dirpath, dirs, names)
        if audio:
            found[Path(dirpath)] = audio
    return _group_unit(eng, path, found, sc) if found else []


def _albums_of_keys(eng, path, keys, skip=()) -> list[Group]:
    """PRF-4: the albums of a scan of PATH that a look-up of KEYS (group_key()s, as List first
    listed them) needs: those of the folders holding them only (albums_iter within), each read
    again now - nothing is taken from the listing, so a folder changed since is read as it is.
    The whole scan when a key is not among them (an album gone or changed since it was listed)."""
    keys = {k for k in keys if k}
    folders = sorted({k.split("|", 1)[0] for k in keys})
    if keys and not Path(os.path.abspath(Path(path).expanduser())).is_file():
        groups = list(_albums_iter(eng, path, skip, within=folders))
        if keys <= {group_key(g.folder, g.files) for g in groups}:
            return groups
    # (the engine's own whole scan, never eng.albums_in: in plan_each that is the look-up's
    # stand-in, which reads through this function - a key not found asked it again and again,
    # "maximum recursion depth exceeded" one album at a time, a deadlock with several at once)
    return list(_albums_iter(eng, path, skip))


def _group_unit(eng, path: Path, found: dict, sc: "_Scan", keys_out=None) -> list[Group]:
    """The albums of FOUND ({folder: its audio files}, in the order the scan read them): one
    piece of a scan of PATH (_scan_from), or all its folders at once (_albums_whole). keys_out:
    a dict given is told each album folder's album keys (own_key: _plan_folder)."""
    # each audio folder's album folder: a disc folder belongs to its parent; a folder
    # inside an album's folder belongs to that album (a bonus disc filed inside it, as
    # match_albums._owners has it) only when both are tagged as that same album -
    # otherwise it is an album of its own
    metas = {f: ex.read_meta(f) for fs in found.values() for f in fs}      # (each file opened once)
    # (a version folder, "2.0/" beside "5.0/", too: the set's folder names the release)
    sets = {}

    def version_of(d):
        if not mc.version_layout(d.name):
            return False
        if d.parent not in sets:
            sets[d.parent] = version_dirs(d.parent, metas, sc.skips)
        return d in sets[d.parent]
    # (DSC-3: "CD02-03 .../Part 1" too - a part of a disc-range folder is one of its discs)
    disc = {d: d != path and (is_disc_dir(d) or version_of(d) or box_part_disc(d) is not None) for d in found}
    owner_of = {d: (d.parent if disc[d] else d) for d in found}
    for d in found:          # ("Album/Disc 02/5.1/": versions filed inside a disc folder)
        if disc[d] and version_of(d) and owner_of[d] != path and is_disc_dir(owner_of[d]):
            owner_of[d] = owner_of[d].parent
    # DSC-3: the box holding the disc-range folders ("Box (16CD)/CD02-03 .../Part 1"
    # beside "CD01 1974 First Album"): its parts are its discs when the whole set is one
    # box by DSC-2 (one album artist, each disc's own disc number, the parts' agreeing with
    # the range) - else each range folder stays an album of its own
    boxes = {owner_of[d].parent for d in found if box_part_disc(d) is not None and owner_of[d] == d.parent}
    for box in sorted(boxes):
        if box != path and path not in box.parents:
            continue                                 # (only the range folder was asked for)
        parts = [d for d in found if owner_of[d] == box or
                 (box_part_disc(d) is not None and owner_of[d] == d.parent and d.parent.parent == box)]
        fs = [f for d in parts for f in found[d]]
        if _one_box(box, fs, album_keys(fs, box, metas), metas):
            for d in parts:
                owner_of[d] = box
    # DSC-5 (BOX-1): a box set's folder naming its CD count, its discs in folders named as
    # albums ("01. (1980) - Kenso ...", "CD4-5. 2005. Ca Ira (2CD, ...)/CD1"), each tagged as
    # its disc of the box: the box is one album (numbered_box), never each folder an album of
    # its own matched to the standalone release of that disc - only when the box folder
    # itself is planned (or a folder above it)
    whole_boxes = set()
    near = {a for d in found for a in (d.parent, d.parent.parent)
            if (a == path or path in a.parents) and box_cd_count(a.name)}
    # DSC-6: a twofer's folder named after the albums it holds, joined ("The Stranger & An
    # Innocent Man"): the set they were issued in, numbered as the release found lists it
    # DSC-9: a folder of a set's volumes ("Volume 1", "Volume 2") that its name says it holds
    # (each parent once: a library's every album folder asks of its artist's folder)
    parents = {d.parent for d in found if (d.parent == path or path in d.parent.parents) and d.parent != d}
    named = {b for b in parents if titled_box(b, found, metas) or volume_set(b, found, metas)}
    for box in sorted(near | named, key=lambda a: len(a.parts)):
        if any(b in box.parents for b in whole_boxes) or not (
                numbered_box(box, found, metas) or box in named):
            continue
        if eng._box_part_left_out(box, [d for d in found if box in d.parents], path):
            continue                                 # (a disc you excluded or ignored: no box)
        whole_boxes.add(box)
        for d in found:
            if box in d.parents:
                owner_of[d] = box
    own_key = {}
    for d in found:
        own_key.setdefault(owner_of[d], []).extend(found[d])
    own_key = {o: set(album_keys(fs, o, metas).values()) for o, fs in own_key.items()}
    if keys_out is not None:
        keys_out.update(own_key)
    folders = {}
    for d in sorted(found):
        owner = owner_of[d]
        keys = own_key.get(owner, set())
        up = owner.parent
        while up == path or path in up.parents:      # (the named folder itself too: "run Album")
            if up in own_key and len(keys) == 1 and next(iter(keys))[0] == "tags" and own_key[up] == keys:
                owner = up
            up = up.parent
        folders.setdefault(owner, []).extend(found[d])
    groups = []
    for folder in sorted(folders):
        files = folders[folder]
        if eng.profile.excluded_path(folder, named=[path]):
            continue
        by_album = {}
        keys = album_keys(files, folder, metas)
        for f in files:
            by_album.setdefault(keys[f], []).append(f)
        tagged = [k for k in by_album if k[0] == "tags"]
        spans_discs = len({f.parent for f in files}) > 1
        if len(by_album) > 1 and spans_discs and len(tagged) <= 1:
            by_album = {(tagged or [("folder", str(folder))])[0]: files}   # one disc set, partly tagged
        elif len(by_album) > 1 and sets.get(folder) and _one_album_titled(by_album):
            by_album = {tagged[0]: files}            # versions of one disc tagged apart ("[HD]")
        elif len(by_album) > 1 and (folder in whole_boxes or _one_box(folder, files, keys, metas)):
            by_album = {tagged[0]: files}                                  # DSC-2, DSC-5
        elif len(by_album) > 1 and spans_discs and numbered_disc_set(folder, files, keys, metas) \
                and not eng._box_part_left_out(folder, sorted({f.parent for f in files}), path):
            by_album = {tagged[0]: files}                                  # DSC-7
        copies = _layer_copies(files, metas) if len(by_album) == 1 and folder not in whole_boxes else None
        if copies:
            # MED-22c: the CD layer's copy and SACD layers' copies of a disc filed side by side
            # ("Disc 01", "Disc 01 (SACD Stereo)"): each copy an album of its own, as filed
            mine = []
            for members, hold in copies:
                mine.append(Group(folder, members, [f for f in files if f not in members],
                                  single=len(members) == 1, layer_hold=hold))
            groups += mine
            if 2 <= len(mine) <= SET_TRIAL_MAX_DISCS:
                # DSC-11: the copies of one release's discs and layers in one album folder (as a
                # move filed them) are one album again when each is matched for sure to it, no disc
                # or layer held twice (Engine._release_folders); else each stays its own album
                for g in mine:
                    g.siblings, g.set_folder, g.set_shape = mine, folder, "albums"
            continue
        if len(by_album) == 1:
            groups.append(Group(folder, files, [], single=len(files) == 1, box=folder in whole_boxes))
            continue
        for key, members in by_album.items():            # a mixed folder: one group per album
            others = [f for f in files if f not in members]
            if key[0] == "folder":                       # untagged leftovers: each on its own
                for m in members:
                    groups.append(Group(folder, [m], [f for f in files if f != m], single=True))
            else:
                groups.append(Group(folder, members, others, single=len(members) == 1))
    groups = _release_set_groups(eng, groups, found, metas, path)    # DSC-10
    groups = _disc_sets(groups, metas)               # DSC-1
    if not (hasattr(eng.profile, "joins_deluxe") and not eng.profile.joins_deluxe()):
        groups = _with_deluxe(eng.profile, groups, path, sc.skip)
    return _one_release_groups(eng, groups, path)    # DSC-11


# notes told once for the run, never as each album's provider error (OCR, gnudb, fingerprints
# and the cover sources' keys are not providers an album needs)
RUN_NOTES = ("ocr", "gnudb", "acoustid", "fanart", "audiodb", "lastfm")


def ocr_note(mode, problems) -> str:
    """Why OCR is off for this run: the Setup notice of what is missing (Tesseract, pyzbar), else
    the problems found, as match_albums.ocr_problems says them."""
    told = [n for n in (rq.problem("tesseract"), rq.problem("pyzbar")) if n]
    return f"OCR is {mode}, but scans are not read. " + (" ".join(told) if told else "; ".join(problems))


def _skips_folder(name: str) -> bool:
    """A folder never looked into without a profile's own list: hidden ones and TagMender's own."""
    return name.startswith(".") or name == "_unresolved"


def web_genres(profile, cand) -> list:
    """A web album's genres as the profile's genres.web says: off - none; store - a shop's
    own genres (never Bandcamp's keywords, which name places and moods too); keywords - also
    Bandcamp's keywords."""
    import web_tag
    mode = ((profile.get("genres") if hasattr(profile, "get") else None) or {}).get("web", "store")
    if mode == "off" or (mode != "keywords" and cand.get("source") == "bandcamp"):
        return []
    return web_tag.genre_list(cand.get("genre"))


def gnudb_candidates(out, album) -> list:
    """GNU-1: [("gnudb", candidate)] - the gnudb entries the matcher found for a one-disc
    album, as web albums (engine._web): only an entry gnudb gives as an exact match for your
    disc's table of contents (not a fuzzy one), with your disc's track count, every track length agreeing with the rip's
    table of contents (match_albums.gnudb_fits), artist, title and a title for every track.
    Its track lengths are the entry's own; its page is gnudb.org's. Free text someone once
    typed in: never a release, so only Partial tags come of it (no release IDs, label,
    barcode, catalogue number or media)."""
    tocs = (album or {}).get("tocs") or {}
    tracks_in = (album or {}).get("tracks") or []
    # (one disc: one table of contents, and no file of another disc)
    if len(tocs) != 1 or len({t.get("disc") or 1 for t in tracks_in}) > 1:
        return []
    only = next(iter(tocs))
    import web_tag
    found = []
    for e in (out or {}).get("gnudb") or []:
        offsets = list(e.get("offsets") or [])
        if not (e.get("exact") and e.get("artist") and e.get("title") and offsets and e.get("seconds")
                and len(e.get("tracks") or []) == len(offsets) == len(tracks_in) and all(e["tracks"])
                and e.get("disc", only) == only):
            continue
        ends = offsets[1:] + [int(e["seconds"]) * 75]
        tracks = [{"title": t, "secs": (b - a) / 75, "num": k + 1, "disc": 1}
                  for k, (t, a, b) in enumerate(zip(e["tracks"], offsets, ends))]
        cat, discid = str(e.get("category") or ""), str(e.get("discid") or "")
        cand = web_tag._cand("gnudb", f"{cat}/{discid}",
                             f"https://gnudb.org/cd/{cat[:2]}{discid}" if cat and discid else "",
                             e["artist"], ma.gnudb_title(e), e.get("year") or "", genre=e.get("genre") or "",
                             tracks=tracks)
        cand["formats"] = {"cd"}             # (a CD's table of contents; never written as media)
        found.append(("gnudb", cand))
    return found


def _gnudb_titles_miss(album, cand, sc) -> str:
    """GNU-1: why a gnudb album's track titles do not confirm it ("" when they do): it must
    fit by the track lengths, and of your files that have a title, at least 80% must agree
    with its track's title, in order (a disc ID and lengths alone fit another album now and
    then; untitled files: the lengths decide)."""
    if sc.get("mode") != "lengths":
        return "gnudb's entry fits by its titles only, not its lengths"
    files, ct = album.get("tracks") or [], cand.get("tracks") or []
    titled = [(i, j) for i, j in sc.get("map") or [] if files[i].get("title")]
    agree = sum(1 for i, j in titled if mc.track_titles_agree(files[i]["title"], ct[j].get("title")))
    if titled and agree < 0.8 * len(titled):
        return f"only {agree} of gnudb's {len(titled)} track titles agree with your files'"
    return ""


def _refresh_days() -> int:
    """This computer's network.refresh_days (0: saved lookups are kept for ever)."""
    try:
        import app_settings
        v = app_settings.get("network.refresh_days", 0)
    except Exception:
        return 0
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0


def styles_into_genre(t: dict, most: int = 5) -> dict:
    """Discogs's styles as the Genre of a track nothing else gave one (genres.discogs_styles
    genre_if_none); DISCOGS_STYLE stays as it is."""
    if not [g for g in t.get("genre") or [] if g] and [s for s in t.get("discogs_style") or [] if s]:
        t["genre"] = [s for s in t["discogs_style"] if s][:max(1, int(most or 5))]
    return t


# Why a listing of the album could not be matched to your files (engine._known_from_own_tags)
_NOT_COMPARABLE = re.compile(r"durations unavailable$|only \d+% of durations agree$|"
                             r"title similarity [\d.]+ < [\d.]+$|confidence [\d.]+ < [\d.]+$")


def own_tag_choices(t: dict, profile, path=None) -> dict:
    """One track's tags with the profile's own tag choices applied, whichever database gave
    them: Discogs styles into an empty Genre (genres.discogs_styles genre_if_none), Date
    holding the first release's date (dates.date_tag original; Original Date holds it too),
    the files' comments taken off (cleanup.comments remove), and the compilations group
    (compilations.apply: the various-artists name, the compilation flag and its extras; path
    is the file the tags are for)."""
    t = compilations.apply(dict(t), profile, path)
    if getattr(profile, "styles_fill_genre", None) and profile.styles_fill_genre():
        styles_into_genre(t, (profile.get("genres") or {}).get("max", 5))
    if getattr(profile, "date_holds_original", None) and profile.date_holds_original():
        first = [d for d in t.get("originaldate") or [] if d]
        if first:
            t["date"] = [first[0]]
    if ((profile.get("cleanup") if hasattr(profile, "get") else None) or {}).get("comments") == "remove" \
            and not t.get("comment"):
        # (cleanup.comments remove: a ripper's or a shop's comment is taken off)
        t["~remove"] = list(dict.fromkeys(list(t.get("~remove") or []) + ["comment"]))
    return t


class Engine:
    lookup_workers = 1      # albums plan_each looks up at once (Overview sets LOOKUP_WORKERS)

    def __init__(self, profile, *, http=None, dm=None, ocr=None, log=None, job=None, cache=None):
        self.profile = profile
        self._made_with = {"dm": dm, "ocr": ocr, "cache": cache}     # (a worker's Engine: _plan_each_at_once)
        # the profile's own Ignore/Skip rules, handed to the matcher with each album (never
        # through the process environment: the GUI and its watcher run several engines at once,
        # each for its own profile)
        self.rules = ex.load_rules(profile.name if profile.name != "default" else "")   # ("": never $TAGMENDER_PROFILE)
        self.job = job or logsetup.new_job_id()
        self.log = log or logsetup.setup("engine", job=self.job)
        # OCR of scans: True (--ocr, the GUI's "Read scans") forces it on; otherwise the
        # profile's "ocr": auto (folders with scans or a PDF), on or off
        self.ocr = "on" if ocr is True else ma.ocr_mode(profile.get("ocr", "auto") if ocr is None else ocr)
        self.notes = {}                           # provider -> why it is off for this run
        self.run_notes = {}                       # provider -> what happened during the run (said once)
        if self.ocr != "off":
            # OCR that cannot work is turned off: run anyway, each scan's empty reading would
            # be cached and the album never read again once tesseract is installed. The note
            # tells the user (the CLI prints it, the GUI shows it with the plan).
            problems = ma.ocr_problems()
            if problems:
                self.log.info("OCR is %s, but it is not ready, so scans are not read: %s",
                              self.ocr, "; ".join(problems))
                self.notes["ocr"] = ocr_note(self.ocr, problems)
                self.ocr = "off"
        self.ocr_images = 15
        # optional AI (AI-1): None unless this profile turns it on, a provider is set up with
        # your own key and its first-use sheet was accepted; it only adds clues to unsure albums
        try:
            import ai_assist
            self.ai = ai_assist.for_profile(profile)
        except Exception as exc:                          # (AI never stops a run)
            self.log.warning("AI assistance is off for this run: %s", exc)
            self.ai = None
        if self.ocr != "off":
            self._ocr_settings()
        cfg = profile.matcher_cfg(dict(mc.DEFAULT_CFG))
        # the tagger's preferred countries, release types and formats, read alike: the profile's
        # "tagger" entries and its own "matching" lists override them; follow_tagger_settings
        # off: no tagger file is read. The country order is the profile's (Profile.
        # preferred_countries): its own list, else the tagger's, else its automatic one - only
        # "none" leaves it empty, which turns the last tie-break between pressings off
        over, follow = profile.tagger_overrides(), profile.follows_tagger()
        countries = ma.picard_preferred_countries(overrides=over, follow_picard=follow)
        cfg["preferred_countries"] = profile.preferred_countries(countries) \
            if hasattr(profile, "preferred_countries") else countries or cfg["preferred_countries"]
        cfg.update(ma.picard_release_preferences(overrides=over, follow_picard=follow))
        try:
            import json
            cfg["artist_aliases"] = json.loads((ex.CONFIG_DIR / "rules.json").read_text()).get("artist_aliases") or {}
        except (OSError, ValueError):
            pass
        cfg["lengths_required"] = bool((self.profile.get("matching") or {}).get("extra_careful"))
        self.cfg = cfg
        gnudb_off = profile.provider_on("web", "gnudb") and ma.gnudb_problem()
        if gnudb_off:
            # not an error: the albums are identified without it, and the CLI/GUI tell it once
            self.notes["gnudb"] = gnudb_off
        self._note_fingerprints(ma.acoustid_mode(profile.get("acoustid")))
        # the Sources setting: a database it leaves out is not asked to identify at all
        self.source = profile.source()
        self.use_mb = profile.provider_on("musicbrainz") and profile.identifies_with("musicbrainz")
        # (the Discogs plugin switched off: as if the profile left Discogs out; not an album's error)
        self.use_discogs = (companions.discogs_on() and profile.provider_on("discogs")
                            and profile.identifies_with("discogs"))
        token = ""
        if self.use_discogs:
            try:
                token = credentials.get("discogs")
            except credentials.CredentialError:
                self.notes["discogs"] = "missing_credentials: " + rq.notice("discogs", state=rq.UNSAFE)
            if not token and "discogs" not in self.notes:
                self.notes["discogs"] = "missing_credentials: " + rq.notice("discogs", state=rq.NOT_SET)
            if not token:
                self.use_discogs = False
        self.token = token
        if dm is None and (self.use_discogs or token):
            try:
                dm = ma.load_plugin(str(companions.discogs_dir()))
            except SystemExit as exc:
                self.notes["discogs"] = f"Discogs plugin code not found: {exc}"
                self.use_discogs = False
        self.dm = dm
        if http is None:
            ex.STATE_DIR.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(cache or (ex.STATE_DIR / "cache.sqlite"), timeout=60)
            # (one pace per host for the whole process: the app's searches and versions sheet,
            # the watcher in the app, every Engine; match_albums.share_pace)
            http = ma.share_pace(ma.Http(db, cfg, token))
            days = _refresh_days()
            if days:
                # (a saved lookup older than this is asked again when its album is processed
                # again; never a background sweep)
                http.max_age = days * 86400
        self.http = http
        self._art = None

    def _note_mb_down(self, exc=None):
        """One note for the run when MusicBrainz is busy or not answering: what TagMender does
        meanwhile (carries on with Discogs when it can; else the albums are tried again later).
        Kept apart from self.notes, which become every later album's provider errors."""
        if "musicbrainz" in self.__dict__.setdefault("run_notes", {}):
            return
        kind = getattr(exc, "kind", "outage")
        busy = "busy" if kind == "rate_limited" else "not answering"
        if self.use_discogs and self._preferred() == "musicbrainz":     # HF-4
            self.run_notes["musicbrainz"] = (
                f"{kind}: MusicBrainz is {busy}. MusicBrainz is preferred in Sources: albums are held "
                "and tried again later, not decided from Discogs alone.")
        elif self.use_discogs:
            self.run_notes["musicbrainz"] = (
                f"{kind}: MusicBrainz is {busy}. TagMender carries on with Discogs: albums Discogs proves are "
                "tagged from it; those only MusicBrainz can prove are left to try again later.")
        else:
            self.run_notes["musicbrainz"] = (
                f"{kind}: MusicBrainz is {busy}. Albums it would identify are left to try again later "
                "(with a Discogs token, TagMender carries on with Discogs meanwhile).")

    def _ocr_settings(self):
        """This computer's scan settings: which languages scans are read in (app_settings
        ocr.languages: automatic is English plus your language, of the packs installed) and
        how many scans an album has read (ocr.effort)."""
        import app_settings
        import subprocess
        try:
            have = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True,
                                  timeout=30).stdout.split()
        except Exception:
            have = []
        try:
            data = app_settings.load()
        except Exception:                             # (unreadable settings: the defaults)
            data = app_settings.DEFAULTS
        ma._LANGS = app_settings.ocr_languages(have, data)
        self.ocr_images = app_settings.ocr_images(data)

    def _note_fingerprints(self, mode):
        """One note for the run when fingerprints are wanted (always, or for unsure albums: the
        default) but fpcalc or the AcoustID application key is missing: which one, and where to set
        it. The albums carry on without fingerprints."""
        if mode == "off" or ma.fingerprint_ready():
            return
        lead = ("acoustid: always is set, but fingerprints are off (they need fpcalc and an AcoustID "
                "application key)." if mode == "always" else
                "Albums not identified for sure are not fingerprinted.")
        self.notes["acoustid"] = " ".join([lead] + [n for n in (rq.problem("fpcalc"), rq.problem("acoustid")) if n])

    def _note_art_keys(self):
        """One note for the run per cover source CoverUp would use that has no key (fanart.tv,
        TheAudioDB, Last.fm): said once, never as an album's error."""
        import artwork
        for cu in self._art or ():
            try:                          # a note: never a reason to lose the album's cover
                missing = cu.missing_keys() if isinstance(cu, artwork.CoverUp) else ()
            except Exception:
                missing = ()
            for name in missing:
                self.notes.setdefault(name, rq.problem(name) or rq.notice(name, state=rq.NOT_SET))

    # ---- what is in a path ------------------------------------------------
    def _web_sources(self):
        """The profile's web fallback sources (providers.web.sources), in its order."""
        web = ((self.profile.get("providers") or {}).get("web") or {}) if hasattr(self.profile, "get") else {}
        src = web.get("sources")
        return list(src) if isinstance(src, list) else None

    def albums_in(self, path, skip=()) -> list[Group]:
        """The albums below path; skip: folders not looked into at all (a library inside a
        watched folder: its tracks are never even opened). Every album of albums_iter."""
        return list(_albums_iter(self, path, skip))

    def albums_iter(self, path, skip=(), within=None):
        """albums_in's albums one at a time, in its order, each as soon as the folders it needs
        are read (PRF-3: _scan_from). within: only the folders these albums need are read -
        the albums inside these folders come out as albums_in(path) has them, beside the other
        albums of the folders read with them (a disc folder's album folder, a set's siblings)."""
        return _albums_iter(self, path, skip, within)

    def _logger(self):
        """The engine's log - or, for an engine made without one (a test's, by __new__), the
        module's logger: its look-ups still plan, their timing and failure lines logged."""
        return getattr(self, "log", None) or logging.getLogger("tagmender.engine")

    def _own_scan(self) -> bool:
        """albums_in is the engine's own (not a stand-in put in its place: plan_each's, a test's)."""
        return "albums_in" not in getattr(self, "__dict__", {}) and getattr(type(self), "albums_in", None) is _ALBUMS_IN

    def _box_part_left_out(self, box: Path, dirs, path) -> bool:
        """DSC-5: a folder of the box (one of its sub-folders, or a disc folder in one) that the
        profile's exclude_paths leave out, or that the rules' Ignore remembered (the watcher's
        ignored folders): the box is not one album - its other folders stay as they were, and
        what you left out is never tagged or filed with them."""
        return _left_out(self.profile, box, dirs, path)

    def _skipper(self):
        """The profile's Skip These Folders (its folder_skipper), as albums_in reads folders."""
        return self.profile.folder_skipper() if hasattr(self.profile, "folder_skipper") else _skips_folder

    def _accepts(self, tier: str) -> bool:
        """identification.accept lets an album of this tier be tagged (Profile.accepts)."""
        return self.profile.accepts(tier) if hasattr(self.profile, "accepts") else True

    def _outside(self, g: Group) -> list:
        """Audio files below the group's folder that are not the group's own: a loose track's
        neighbours in a library root, another album nested inside this one, folders the
        grouping skips - and, when deluxe folders are not joined (grouping.join_deluxe_folders
        off), the "(Deluxe Edition)" folder next to it, which the matcher would read with it.
        Identification and the plan never see them."""
        mine = {str(f) for f in g.files}
        tops = [g.folder]
        if hasattr(self.profile, "joins_deluxe") and not self.profile.joins_deluxe():
            sib = ma.deluxe_sibling(g.folder)
            if sib:
                tops.append(Path(sib))
        out = []
        for top in tops:
            for dirpath, _dirs, names in os.walk(top):
                for n in names:
                    p = os.path.join(dirpath, n)
                    if Path(n).suffix.lower() in ex.AUDIO_EXT and p not in mine:
                        out.append(Path(p))
        return out

    # ---- identification ------------------------------------------------------
    def forced_for(self, g: Group, alone: bool = True):
        """The release the user chose for this album: force maps an album folder to a link (for
        the folder's only album), or to {"ref": link, "files": [the album's files]} - or a list
        of those, one per album of a folder holding several. A choice is this album's when it
        names files of it (the album's files as the plan listed them: its second copies, or
        its copies of a copy profile, may be missing; the most files in common wins)."""
        c = self._choice_for(g, alone)
        return c.get("ref") if isinstance(c, dict) else c

    def _choice_for(self, g: Group, alone: bool = True):
        """forced_for's choice as it was given: a link, or the dict naming the album's files."""
        v = (getattr(self, "force", None) or {}).get(os.path.realpath(str(g.folder)))
        if isinstance(v, (dict, list)):
            mine = {os.path.realpath(str(f)) for f in g.files}
            best, most = None, 0
            for c in ([v] if isinstance(v, dict) else v):
                if not isinstance(c, dict) or not c.get("ref"):
                    continue
                n = len(mine & {os.path.realpath(str(f)) for f in c.get("files") or []})
                if n > most:
                    best, most = c, n
            return best
        return v if v and alone else None

    @staticmethod
    def _chosen_files(g: Group, choice) -> Group:
        """The album the choice is for: one version of a folder holding two ("only" set, the
        report's Apply This Release on a split's version) is its own files, the other version
        left out; any other choice is for the whole album (its second copies included)."""
        if not (isinstance(choice, dict) and choice.get("only")):
            return g
        named = {os.path.realpath(str(f)) for f in choice.get("files") or []}
        fs = [f for f in g.files if os.path.realpath(str(f)) in named]
        return Group(g.folder, fs) if fs else g

    def identify(self, g: Group) -> planmod.Proposal:
        disc = self._tagged_disc(g)
        # (a release chosen for one disc of it: its row is the disc folder's - IDM-1, _per_disc)
        own = Group(disc, list(g.files)) if disc is not None and self._choice_for(
            Group(disc, list(g.files)), getattr(self, "_alone", True)) else g
        choice = self._choice_for(own, getattr(self, "_alone", True))
        ref = choice.get("ref") if isinstance(choice, dict) else choice
        self._last_out = None
        if ref:
            prop = self._forced(self._chosen_files(own, choice), ref)
            if own is not g:
                prop.disc_folder = disc
        elif disc is not None:
            # IDM-1: one disc of a set TagMender tagged disc by disc (_per_disc), each its own
            # release: identified alone, from its own folder, as that job did - the set's other
            # discs' scans are not its proof
            was, self._disc_alone = getattr(self, "_disc_alone", False), True
            try:
                prop = self._identify(Group(disc, list(g.files)),
                                      names=(g.folder.parent.name, one_disc_name(g.folder.name)))
            finally:
                self._disc_alone = was
            prop.disc_folder = disc
        else:
            prop = self._identify(g)
        # the releases the matcher scored best, for the run report (one album as one folder only:
        # a set of discs or a split was matched disc by disc, version by version)
        whole = not ref and not getattr(prop, "parts", None) and not getattr(prop, "split", None) \
            and not str(prop.reason or "").startswith(MIXED_WORDS)
        if whole and not getattr(prop, "candidates", None):
            source = getattr(self, "source", "") or ""
            prop.candidates = candidates_of(self._last_out, prefer=("mb" if source == "musicbrainz_preferred" else
                                                                    "discogs" if source == "discogs_preferred" else ""))
        if whole:
            scans_of(prop, self._last_out)     # (reporting only)
        # (a set whose discs are different releases: each disc is its own album, _per_disc)
        for p in [prop] + list(getattr(prop, "parts", None) or []):
            self._finish(p, g)
        return prop

    # ---- DSC-10: sibling folders of one set, joined by its release ----------------------
    def _set_member(self, g: Group) -> list:
        """DSC-10: what album g of sibling folders is planned as: the set's albums are worked
        out once per scan (_release_set, for whichever of them is asked first) - g's own plan as
        before, or the joined album of the release holding it (the same Proposal for each of
        its folders: its group_key is its first folder's, Proposal.set_key). A failure in
        identifying g alone is g's own, as before; its siblings are planned without it."""
        memo = self.__dict__.setdefault("_set_memo", {})
        sk = tuple(group_key(q.folder, q.files) for q in g.siblings)
        if sk not in memo:
            memo[sk] = self._release_set(g)
        got = memo[sk]
        i = sk.index(group_key(g.folder, g.files))
        if i in got["members"]:
            return list(got["joined"])
        own = got["own"].get(i)
        if isinstance(own, BaseException):
            raise own
        if own is None:                                  # (not reached: identified alone now)
            was, self._in_set = getattr(self, "_in_set", False), True
            try:
                return self.identify_all(g)
            finally:
                self._in_set = was
        return list(own)

    def _release_set(self, g: Group) -> dict:
        """DSC-10: the albums of g's sibling folders, each planned alone first, as before (a
        failure is that album's own); then (A) a release one of them is matched to for sure
        claims the siblings holding all its other discs (_claim), else (B) disc folders
        numbered 1 to k are searched for as a set and as its leading discs (_set_search). The
        folders proven one release are one album of it. {"own": {index: [Proposal] or the
        exception}, "members": indexes joined, "joined": [the joined album's Proposal]}."""
        parts, folder = list(g.siblings), g.set_folder
        was_alone = getattr(self, "_alone", True)
        count = {}
        for part in parts:
            k = os.path.realpath(str(part.folder))
            count[k] = count.get(k, 0) + 1

        def alone(part):
            return count[os.path.realpath(str(part.folder))] == 1
        out = {"own": {}, "members": set(), "joined": []}
        albums = g.set_shape == "albums"
        # (DSC-11: a release you chose for the joined row - kept under its first folder - is each
        # covered folder's own choice: each planned alone with it, then joined as usual)
        chosen = set() if albums else self._chosen_join(folder, parts)
        was_force = getattr(self, "force", None)
        if albums:
            self.force = self._albums_chosen(parts)
        was_in, self._in_set = getattr(self, "_in_set", False), True
        try:
            for i, part in enumerate(parts):
                if i in chosen:
                    continue
                self._alone = alone(part)
                try:
                    out["own"][i] = self.identify_all(part)
                except Exception as exc:                 # (that album's own failure, as before)
                    out["own"][i] = exc
            if chosen:                                   # (you chose a release for some of them together)
                files = [f for i in sorted(chosen) for f in parts[i].files]
                joined = Group(folder, files, [f for q in parts for f in q.files if f not in files])
                self._alone = True
                try:
                    props = self.identify_all(joined)
                except Exception as exc:
                    for i in chosen:
                        out["own"][i] = exc
                    return out
                lead = parts[min(chosen)]
                for p in props:
                    p.set_key, p.set_members = group_key(lead.folder, lead.files), [parts[i] for i in sorted(chosen)]
                out["members"], out["joined"] = set(chosen), props
                return out
            per = [(i, parts[i], ps) for i, ps in sorted(out["own"].items()) if isinstance(ps, list)]
            if g.set_shape == "albums":                  # DSC-11: album folders of one release
                return self._release_folders(folder, parts, per, out)
            if any(self._choice_for(part, alone(part)) for _i, part, _ps in per):
                return out                               # (a release you chose for one of them)
            got = self._claim(folder, parts, per)
            if got is None and g.set_shape == "discs" and len(per) == len(parts):
                got = self._set_search(folder, parts, per)
        finally:
            self._in_set, self._alone = was_in, was_alone
            if albums:
                self.force = was_force
        if got is None:
            return out
        prop, mapping, how = got
        names = ", ".join(f"'{d.name}' -> disc {n}" for d, n in sorted(mapping.items(), key=lambda x: x[1]))
        note = f"one release, {len(mapping)} discs: {names} ({_release_words(prop)}{how})"
        members = {i for i, part, _ps in per if {Path(f).parent for f in part.files} <= set(mapping)}
        left = []
        for i, part, ps in per:
            if i in members:
                continue
            words = f"'{Path(part.files[0]).parent.name}' fits no disc of it: planned alone"
            left.append(words)
            for p in ps:
                p.notes = list(p.notes or []) + [words]
        prop.notes = list(prop.notes or []) + [note] + left
        lead = parts[min(members)]
        prop.set_key, prop.set_members = group_key(lead.folder, lead.files), [parts[i] for i in sorted(members)]
        out["members"], out["joined"] = members, [prop]
        self.log.info("%s: %s", folder, "; ".join([note] + left))
        return out

    def _albums_chosen(self, parts):
        """DSC-11: the choices (force) with a release you chose for a joined row of album folders
        (kept under its first folder, naming every file of it) given to each folder it covers as
        that folder's own - each album then planned alone with it (exact: _sure) and joined as
        usual. A choice covering one folder only stays as it was. A new dict; force unchanged."""
        force = dict(getattr(self, "force", None) or {})
        mine = [{os.path.realpath(str(f)) for f in q.files} for q in parts]
        given = {}
        for v in list(force.values()):
            for c in ([v] if isinstance(v, dict) else v if isinstance(v, list) else []):
                if not isinstance(c, dict) or not c.get("ref") or c.get("only"):
                    continue
                named = {os.path.realpath(str(f)) for f in c.get("files") or []}
                covered = [i for i, m in enumerate(mine) if m and m <= named]
                if len(covered) < 2:
                    continue
                for i in covered:
                    k = os.path.realpath(str(parts[i].folder))
                    given.setdefault(k, []).append({"ref": c["ref"], "files": [str(f) for f in parts[i].files]})
        for k, cs in given.items():
            v = force.get(k)
            # (a folder's other albums keep their own choices; its covered albums take the joined one)
            covered = {os.path.realpath(f) for c in cs for f in c["files"]}
            rest = [c for c in ([v] if isinstance(v, dict) else v if isinstance(v, list) else [])
                    if isinstance(c, dict) and not {os.path.realpath(str(f)) for f in c.get("files") or []} <= covered]
            force[k] = cs + rest
        return force

    def _chosen_join(self, folder: Path, parts) -> set:
        """DSC-10: the parts a release you chose for several of them together names (the
        joined row's Change Release): their indexes, else none."""
        v = (getattr(self, "force", None) or {}).get(os.path.realpath(str(folder)))
        for c in ([v] if isinstance(v, dict) else v if isinstance(v, list) else []):
            if not isinstance(c, dict) or not c.get("ref"):
                continue
            named = {os.path.realpath(str(f)) for f in c.get("files") or []}
            covered = {i for i, q in enumerate(parts) if {os.path.realpath(str(f)) for f in q.files} <= named}
            if len(covered) >= 2:
                return covered
        return set()

    def _release_cand(self, kind, rid):
        """A MusicBrainz ("mb") or Discogs release as the matcher's candidate (its tracks, disc
        by disc), from the cache the match itself read it into; None when it cannot be had."""
        try:
            if kind == "mb":
                det = self.http.get_json(f"https://musicbrainz.org/ws/2/release/{rid}"
                                         f"?inc=recordings+artist-credits+labels+url-rels+release-groups&fmt=json", "mb")
                cand = ma.mb_candidate(det) if det else None
                if cand and not cand.get("layers"):
                    # (its tracks' and recordings' IDs, each on its disc: a file tagged with one
                    # is that track - an ID on two discs says nothing)
                    ids = {}
                    audio = [m for m in det.get("media") or []
                             if str(m.get("format") or "").lower() not in ma._MB_VIDEO_FORMATS]
                    for n, m in enumerate(audio, 1):
                        for t in m.get("tracks") or []:
                            for x in (t.get("id"), (t.get("recording") or {}).get("id")):
                                if x:
                                    ids.setdefault(str(x).lower(), set()).add(n)
                    cand["track_ids"] = {x: next(iter(v)) for x, v in ids.items() if len(v) == 1}
                return cand
            det = self.http.get_json(f"https://api.discogs.com/releases/{rid}", "discogs")
            if not det or self.dm is None:
                return None
            cand = ma.discogs_candidate(det, self.dm)
            # (its audio discs as its formats name them: a DVD-Video listed "2-1" ... is none, MED-11)
            cand["audio_shape"], cand["shape_unsure"] = _discogs_shape(det, cand)
            return cand
        except Exception as exc:                           # (no claim then: each folder as planned)
            self.log.warning("release %s %s for a set's claim: %s", kind, rid, exc)
            return None

    @staticmethod
    def _release_of(p):
        """("mb" | "discogs", release ID) a Proposal names, or None."""
        prov = str(getattr(p, "provider", "") or "")
        kind = "mb" if prov.startswith("musicbrainz") else "discogs" if prov.startswith("discogs") else None
        return (kind, str(p.release_id)) if kind and p.release_id else None

    def _sure(self, p) -> bool:
        """DSC-10: a release proven for an album at the profile's auto threshold at least."""
        return p.status == "identified" and planmod.tier_of(p) == "exact" and \
            float(p.confidence or 0) >= planmod._threshold(self.profile, "auto_min_confidence")

    def _suggested_set(self, p) -> bool:
        """DSC-10 / TIE-1: the album is album known only because its pressings tie and a tie-break
        suggested one (Proposal.suggested), at the profile's auto threshold at least: as sure of
        the album as _sure, the pressing proposed."""
        sg = getattr(p, "suggested", None) or {}
        return p.status == "identified" and planmod.tier_of(p) == "album_only" and bool(sg.get("id")) and \
            float(p.confidence or 0) >= planmod._threshold(self.profile, "auto_min_confidence")

    @staticmethod
    def _suggested_key(p):
        """("mb" | "discogs", ID) of the release suggested for p (TIE-1), or None."""
        sg = getattr(p, "suggested", None) or {}
        if not sg.get("id"):
            return None
        return ("mb" if sg.get("source") in ("mb", "musicbrainz") else "discogs", str(sg["id"]))

    def _same_release(self, key, q, cand) -> bool:
        """DSC-10: Proposal q's release is the release key (cand): _same_key."""
        other = self._release_of(q)
        return other is not None and self._same_key(key, other, cand)

    def _same_key(self, key, other, cand) -> bool:
        """DSC-10: release other ("mb" | "discogs", ID) is release key (cand): the same ID, or the
        same pressing on the other database - linked to it, with as many audio discs and tracks
        (a one-disc release that links the box it came in is no twin of the box)."""
        if other == key:
            return True
        if key[0] == "mb" and other[0] == "discogs":
            twin = self._release_cand(*other)
            linked = other[1] in {str(x) for x in cand.get("discogs_ids") or ()} | {str(cand.get("discogs_id") or "")}
        elif key[0] == "discogs" and other[0] == "mb":
            twin = self._release_cand(*other)
            linked = bool(twin) and key[1] in {str(x) for x in twin.get("discogs_ids") or ()} | \
                {str(twin.get("discogs_id") or "")}
        else:
            return False
        return linked and bool(twin) and _disc_shape(twin) == _disc_shape(cand)

    def _discogs_twin(self, best, rel, dgid) -> str:
        """TAG-4: of the Discogs releases a MusicBrainz release links (best's discogs_ids, dgid
        first), the first that is the same pressing by its discs: as many audio discs with as
        many tracks (in all within a tenth: Discogs lists headings and index tracks its own way)
        - or its CDs only, the MusicBrainz release's leading discs track for track, when every
        other disc of it is no CD (a DVD-Audio Discogs lists apart, IDM-1). "" when none is (a
        3-CD edition linking its 1-CD bonus EP). The MusicBrainz release's discs are rel's
        (fetched), else the match's own when it is the whole release (not one disc of it,
        BOX-2). Unknown shapes keep dgid."""
        mine, fmts = [], []
        if rel:
            try:
                c = ma.mb_candidate(rel)
                mine, fmts = _disc_shape(c), list(c.get("media_formats") or [])
            except Exception:
                mine = []
        elif not best.get("box_disc") and not best.get("slots") and isinstance(best.get("tracks"), list):
            mine, fmts = _disc_shape(best), list(best.get("media_formats") or [])
        if not mine:
            return dgid
        ids = [str(dgid)] + [str(x) for x in best.get("discogs_ids") or () if str(x) != str(dgid)]
        for x in ids:
            try:
                det = self.http.get_json(f"https://api.discogs.com/releases/{x}", "discogs")
                theirs, _video = _discogs_shape(det, ma.discogs_candidate(det, self.dm)) if det else ([], False)
            except ma.AuthError:
                raise
            except Exception:
                return dgid                              # (cannot be read now: as before)
            if not theirs:
                return dgid
            # (disc for disc the same tracks - a tenth's leeway only where Discogs lists headings or
            # index tracks its own way: 9 tracks and the 10 of a bonus edition are two releases)
            loose = any(str(t.get("type_") or "track") in ("heading", "index") for t in det.get("tracklist") or [])
            same = theirs == mine or (loose and len(theirs) == len(mine)
                                      and abs(sum(theirs) - sum(mine)) <= max(1, sum(mine) // 10))
            cds = len(theirs) < len(mine) and theirs == mine[:len(theirs)] and len(fmts) == len(mine) and \
                all(mc.medium_type(f) != "cd" for f in fmts[len(theirs):])
            if same or cds:
                return x
            self.log.info("Discogs %s linked by MusicBrainz %s is another release: %d disc(s) of %d track(s), "
                          "not %d of %d", x, best.get("mb_id") or best.get("id"), len(theirs), sum(theirs),
                          len(mine), sum(mine))
        return ""

    def _stale_other_ids(self, prop):
        """TAG-4: a release ID of the other database your files carry, where the release written
        has no twin there (none of that database's IDs is written): taken off when TagMender's own
        earlier job wrote it (REP-1: the value it left, still there), or when its release is shown
        to be another pressing - another number of audio discs than the release written, or
        TAG-3's evidence (_another_pressing). Another tagger's ID nothing shows to be another
        pressing stays. The plan says what is taken off and why."""
        import tagio
        if prop.status != "identified" or planmod.tier_of(prop) != "exact" or not prop.targets:
            return
        has = {k for t in prop.targets for k in ("musicbrainz_albumid", "discogs_release_id") if t.get(k)}
        if has == {"musicbrainz_albumid"}:
            field, kind, name = "discogs_release_id", "discogs", "Discogs"
        elif has == {"discogs_release_id"}:
            field, kind, name = "musicbrainz_albumid", "mb", "MusicBrainz"
        else:
            return
        if str(getattr(self, "source", "") or "").endswith("_only"):
            return                                       # (follow_source takes them all off)
        asks = bool(getattr(self, "use_discogs" if kind == "discogs" else "use_mb", False)) and \
            not (prop.provider_errors or {}).get("discogs" if kind == "discogs" else "musicbrainz")
        totals = {str((t.get("totaldiscs") or [""])[0]).strip() for t in prop.targets} - {""}
        mine = int(next(iter(totals))) if len(totals) == 1 and next(iter(totals)).isdigit() else 0
        try:
            owned = ma._own_index()
        except Exception:
            owned = {}
        why_of = {}

        def why(v, f):
            if v not in why_of:
                own = owned.get(str(f)) or owned.get(os.path.realpath(str(f))) or (frozenset(), frozenset())
                ours = {str(x).lower() for x in own[0 if kind == "mb" else 1]}
                if str(v).lower() in ours:
                    why_of[v] = "TagMender's earlier job wrote it"
                else:
                    shape, unsure = [], False
                    try:
                        c = self._release_cand(kind, v) if asks else None     # (never a database left out)
                        shape = _disc_shape(c) if c else []
                        unsure = bool(c and c.get("shape_unsure"))
                    except Exception:
                        shape = []
                    # (never by the count alone while it is unsure - a DVD or Blu-ray in its formats, or
                    # formats counting other discs than its tracklist: TAG-3's evidence then)
                    if shape and mine and len(shape) != mine and not unsure:
                        why_of[v] = f"another pressing: {len(shape)} disc(s), not {mine}"
                    else:
                        try:
                            ev = self._another_pressing(prop, getattr(prop, "release_view", None), field, v)[0] \
                                if asks else ""
                        except Exception:
                            ev = ""
                        why_of[v] = f"another pressing: {ev}" if ev else ""
            return why_of[v]
        told = {}
        for f, t in zip(prop.files, prop.targets):
            if t.get(field) or field in (t.get("~remove") or ()):
                continue                                 # (written, or taken off already: follow_source, TAG-3)
            try:
                had = [str(v) for v in tagio.read(f).get(field) or [] if str(v)]
            except Exception:
                continue
            if had and all(why(v, f) for v in had):
                t["~remove"] = list(dict.fromkeys(list(t.get("~remove") or []) + [field]))
                for v in had:
                    told.setdefault(v, why(v, f))
        for v, w in told.items():
            note = f"takes off your {name} release ID {v} ({w}): the release written has none there"
            prop.notes = list(prop.notes or []) + [note]

    @staticmethod
    def _foreign_ids(files) -> set:
        """DSC-10: the releases your files name by their own release IDs ("mb" | "discogs", ID)
        that another tagger wrote: a file TagMender tagged (its TAGMENDER mark) says only what an
        earlier job of TagMender's chose, which is no proof (REP-1)."""
        out = set()
        for f in files:
            m = ex.read_meta(f)
            if _first(m, "tagmender"):
                continue
            for kind, tags in (("mb", ("musicbrainz_albumid",)), ("discogs", ("discogs_release_id", "discogs_releaseid"))):
                for t in tags:
                    v = _first(m, t).strip()
                    if v:
                        out.add((kind, v.lower() if kind == "mb" else v))
        return out

    @staticmethod
    def _own_proof(p, against="mb") -> bool:
        """DSC-10: an album's match rests on its own copy: its barcode or catalogue number is on
        the release it was matched to - or its disc's table of contents is, when the release
        it is weighed against (against: "mb" | "discogs") is one that would list that disc ID
        too (MusicBrainz's; Discogs lists none, so a disc of a box pressed as the standalone CD
        is no proof against the box)."""
        return p.status == "identified" and bool(
            getattr(p, "own_number", False) or getattr(p, "number_proven", False)
            or (against == "mb" and getattr(p, "toc_proven", False)))

    def _fits_discs(self, folder: Path, files, cand, tight=False) -> list:
        """DSC-10: the discs of the release cand whose every track one file of folder is, in
        order: as many tracks as files, each title agreeing and each length within the
        matcher's tolerance (a track listing no length fits none). tight: each length within
        TIGHT_S - as a disc's table of contents measures it, the same master and pressing."""
        mine = {str(f) for f in files}
        ids = cand.get("track_ids") or {}
        if ids:
            # (a file's own track or recording ID on the release decides its disc outright)
            hit = []
            for f in files:
                m = ex.read_meta(f)
                own = {str(v).lower() for k in ("musicbrainz_trackid", "musicbrainz_releasetrackid",
                                                "musicbrainz_recordingid") for v in m.get(k) or []}
                hit.append({ids[x] for x in own if x in ids})
            if any(hit):
                ds = set().union(*hit)
                if len(ds) != 1 or not all(hit):
                    return []                            # (its files on two discs, or some on none)
                d = next(iter(ds))
                count = sum(1 for t in cand.get("tracks") or [] if int(t.get("disc") or 1) == d)
                return [d] if count == len(mine) else []
        album = ma.read_album(folder, folder.parent.name, folder.name)
        tracks = [t for t in album.get("tracks") or [] if str(t.get("path")) in mine]
        if len(tracks) != len(mine):
            return []
        if len(tracks) < self.FIT_MIN_TRACKS and sum(float(t.get("secs") or 0) for t in tracks) < self.FIT_MIN_SECONDS:
            return []                                    # (a short disc of a track or two: any interview fits)
        tol_s, tol_pct = float(self.cfg.get("duration_tol_s", 3.0)), float(self.cfg.get("duration_tol_pct", 0.015))
        if tight:
            tol_s, tol_pct = self.TIGHT_S, 0.0
        by_disc = {}
        for t in cand.get("tracks") or []:
            by_disc.setdefault(int(t.get("disc") or 1), []).append(t)
        out = []
        for d, ct in sorted(by_disc.items()):
            if len(ct) != len(tracks):
                continue
            if all(mc.track_titles_agree(a.get("title"), b.get("title")) and float(b.get("secs") or 0) > 0
                   and float(a.get("secs") or 0) > 0
                   and abs(float(a["secs"]) - float(b["secs"])) <= max(tol_s, tol_pct * float(b["secs"]))
                   for a, b in zip(tracks, ct)):
                out.append(d)
        return out

    TIGHT_S = 1.0          # (DSC-10: lengths as one disc's table of contents gives them, to the second)
    FIT_MIN_TRACKS = 3     # (DSC-10: a disc fits by its titles and lengths with this many tracks,
    FIT_MIN_SECONDS = 600  # or this long: never a one-track 11 s "Interview")

    @staticmethod
    def _complete(p) -> bool:
        """DSC-10: an album matched to a release it holds whole (no track of it missing)."""
        return p.status == "identified" and "incomplete_album" not in (p.reason_codes or [])

    def _claim(self, folder: Path, parts, per):
        """DSC-10 (A): a folder matched for sure (_sure) to a release of more audio discs than it
        holds claims its sibling folders when they hold every other disc of it, each fitting
        one disc: its own match is that release (or its twin), or its files' track or recording
        IDs are that disc's, or else its every title and length in order (_fits_discs; three
        tracks or ten minutes at least). A sibling stays apart when its files name another
        release by IDs another tagger wrote (_foreign_ids), or its own match is another release
        resting on its own copy: its barcode or catalogue number - or its disc ID, unless its
        every length is this disc's to the second (TIGHT_S). A folder fitting two discs, two
        fitting one, a disc of the release no folder holds: no claim. The release claiming the
        most folders wins; two claiming as many, neither. The joined album must itself be proven
        at the auto threshold. Then (its Proposal, {folder: disc}, "") - or None. Folders that
        are all discs of one release with discs not here yet wait for them (_set_arriving)."""
        claims, seen = [], set()
        dir_of = {i: next(iter({Path(f).parent for f in part.files})) for i, part, _ps in per}
        for i, part, ps in per:
            if len(ps) != 1 or not self._sure(ps[0]):
                continue
            p = ps[0]
            key = self._release_of(p)
            own = {str((t.get("discnumber") or [""])[0]).partition("/")[0].strip() for t in p.targets or []}
            totals = {str((t.get("totaldiscs") or [""])[0]).strip() for t in p.targets or []}
            if key is None or key in seen or len(own) != 1 or not next(iter(own)).isdigit():
                continue
            total = int(next(iter(totals))) if len(totals) == 1 and next(iter(totals)).isdigit() else 0
            if not 2 <= total <= len(parts):
                continue                                 # (no release of more discs, or more than are here)
            seen.add(key)
            cand = self._release_cand(*key)
            discs = set(range(1, len(_disc_shape(cand)) + 1)) if cand else set()
            if len(discs) < 2:
                continue
            mapping, fits = {dir_of[i]: int(next(iter(own)))}, {}
            for j, other, ops in per:
                if j == i:
                    continue
                d = dir_of[j]
                if any(not self._same_key(key, x, cand) for x in self._foreign_ids(other.files)):
                    continue                             # (another tagger's IDs name another release)
                proof = [q for q in ops if self._own_proof(q, key[0]) and not self._same_release(key, q, cand)]
                if any(q.own_number or q.number_proven for q in proof):
                    continue                             # (its own barcode or catalogue number: another release)
                if proof and len(self._fits_discs(d, other.files, cand, tight=True)) != 1:
                    continue                             # (its disc ID proves another release, and its
                                                         # lengths are not this one's to the second)
                    # (its own match is this release - or its twin on the other database: its disc)
                same = [q for q in ops if self._sure(q) and self._same_release(key, q, cand)]
                theirs = {str((t.get("discnumber") or [""])[0]).partition("/")[0].strip()
                          for q in same for t in q.targets or []}
                if len(ops) == 1 and same and len(theirs) == 1 and next(iter(theirs)).isdigit():
                    found = [int(next(iter(theirs)))]
                else:
                    found = self._fits_discs(d, other.files, cand)
                found = [n for n in found if n not in mapping.values()]
                if len(found) == 1:
                    fits[d] = found[0]
            taken = list(fits.values())
            mapping.update({d: n for d, n in fits.items() if taken.count(n) == 1})
            if set(mapping.values()) == discs and len(mapping) == len(discs):
                claims.append((len(mapping), key, p, mapping))
        self._set_arriving(parts, per)
        if not claims:
            return None
        claims.sort(key=lambda c: -c[0])
        if len(claims) > 1 and claims[1][0] == claims[0][0]:
            self.log.info("%s: %s and %s each claim %d folders: each planned alone", folder,
                          claims[0][1], claims[1][1], claims[0][0])
            return None
        _n, (kind, rid), claimer, mapping = claims[0]
        every = [f for q in parts for f in q.files]
        files = [f for d, _n2 in sorted(mapping.items(), key=lambda x: x[1]) for f in every if Path(f).parent == d]
        joined = Group(folder, files, [f for f in every if f not in files])
        ref = f"https://musicbrainz.org/release/{rid}" if kind == "mb" else f"https://www.discogs.com/release/{rid}"
        prop = self._forced(joined, ref, discs=mapping, claimed=claimer, release=(kind, rid))
        # (proven as one album at the auto threshold, as any album: never on weaker evidence than
        # its folders' own plans had)
        if not self._sure(prop) or not _discs_as(prop, mapping) or not self._complete(prop):
            self.log.info("%s: the claim of %s is not proven as one album: %s %s", folder, ref,
                          prop.reason, f"{prop.confidence:.1f}")
            return None
        self._finish(prop, joined)
        return prop, mapping, ""

    def _set_arriving(self, parts, per):
        """DSC-10: every folder here matched for sure, each alone, to its own disc of one same
        release of more discs than are here ("Disc 2", "Disc 3" each found as theirs of a 3-CD
        set): a set still being copied in. Each album says which discs are not here yet
        (.awaits): the watcher waits for them; a plan plans each folder alone meanwhile. Nothing
        is fetched for it."""
        if len(per) != len(parts) or any(len(ps) != 1 or not self._sure(ps[0]) for _i, _p, ps in per):
            return
        keys = {self._release_of(ps[0]) for _i, _p, ps in per}
        discs, totals = [], set()
        for _i, _p, ps in per:
            t = ps[0].targets or [{}]
            discs.append(str((t[0].get("discnumber") or [""])[0]).partition("/")[0].strip())
            totals.add(str((t[0].get("totaldiscs") or [""])[0]).strip())
        if len(keys) != 1 or None in keys or len(totals) != 1 or not all(d.isdigit() for d in discs) or \
                len(set(discs)) != len(discs) or not next(iter(totals)).isdigit():
            return
        n = int(next(iter(totals)))
        gone = [d for d in range(1, n + 1) if str(d) not in discs]
        if not gone or len(gone) + len(discs) != n:
            return
        words = f"disc{'s' if len(gone) > 1 else ''} {', '.join(map(str, gone))} of {n} " \
                f"{'are' if len(gone) > 1 else 'is'} not here yet"
        for _i, _p, ps in per:
            ps[0].awaits = words

    def _set_search(self, folder: Path, parts, per):
        """DSC-10 (B): disc folders numbered 1 to k (release_set_shape "discs") searched for as
        one set, then as their leading discs 1 to j (j from k-1 down to 2), at most
        SET_TRIAL_MAX_SEARCHES searches: accepted only when a release is proven for them at the
        auto threshold, holding exactly those discs (its audio discs, MED-17), every file its
        own track of the disc its folder numbers, none missing - and no disc's own match
        resting on its own copy is another release. Never for folders each matched for sure to
        a release of its own held whole (a series' volumes), nor for folders their own codes or
        years show to be separate releases (as DSC-9). Then (Proposal, {folder: disc}, how) - or None."""
        if all(len(ps) == 1 and self._sure(ps[0]) and self._complete(ps[0]) for _i, _p, ps in per) and \
                len({self._release_of(ps[0]) for _i, _p, ps in per}) == len(per):
            return None                                  # (each a release of its own, held whole)
        if _separate_releases(parts):
            return None
        every = [f for q in parts for f in q.files]
        order = sorted({Path(f).parent for f in every}, key=lambda d: _lead_key(d.name))
        k = len(order)
        runs = ([k] + list(range(k - 1, 1, -1)))[:SET_TRIAL_MAX_SEARCHES]
        for j in runs:
            dirs = order[:j]
            files = [f for d in dirs for f in every if Path(f).parent == d]
            trial = Group(folder, files, [f for f in every if f not in files])
            prop = self._set_trial_identify(trial)
            mapping = {d: n for n, d in enumerate(dirs, 1)}
            # (TIE-1: a set whose pressings tie, one only suggested, is the set all the same - album
            # known with the set's suggestion; never left to its discs, each the same guess alone)
            if not (self._sure(prop) or self._suggested_set(prop)) or getattr(prop, "parts", None) \
                    or not self._complete(prop):
                continue
            if len(prop.targets or []) != len(files) or not _discs_as(prop, mapping, total=j):
                continue
            key = self._release_of(prop) or self._suggested_key(prop)
            foreign = {x for q in parts if {Path(f).parent for f in q.files} <= set(dirs)
                       for x in self._foreign_ids(q.files)}
            if foreign and any(not self._same_key(key, x, self._release_cand(*key) or {}) for x in foreign):
                continue                                 # (another tagger's IDs name another release)
            if not self._own_proof(prop, key[0]) and any(
                    self._own_proof(q, key[0]) and self._release_of(q) != key
                    for _i, part, ps in per if {Path(f).parent for f in part.files} <= set(dirs) for q in ps):
                continue                                 # (a disc's own copy proves another release)
            return prop, mapping, ("; found searching for the set" if j == k else
                                   f"; found searching for discs 1-{j}")
        return None

    def _set_trial_identify(self, g: Group):
        """DSC-10 (B): one search for a set's discs together (_set_search): the matcher's answer
        only - no album-only, Partial, web, fingerprint or disc-by-disc fallback."""
        was, self._trialling = getattr(self, "_trialling", False), True
        alone, self._alone = getattr(self, "_alone", True), True
        try:
            return self.identify(g)
        finally:
            self._trialling, self._alone = was, alone

    @staticmethod
    def _slots(p) -> set:
        """DSC-11: the discs and layers of its release album p's files take: {(disc number,
        layer)}, the layer "disc" for a plain disc or a hybrid SACD's CD layer (one copy of the
        disc's CD audio), "stereo" / "multichannel" / "sacd" for an SACD layer (MED-22)."""
        full = getattr(p, "full_targets", None)
        ts = full if full and len(full) == len(p.targets or []) else p.targets or []
        out = set()
        for t in ts:
            disc = str((t.get("discnumber") or ["1"])[0]).partition("/")[0].strip() or "1"
            kind = mb_tags._layer_kind(str((t.get("media") or [""])[0]))[1]
            lay = str((t.get("~layer") or [""])[0]).casefold()
            if not kind and lay:
                kind = "stereo" if "stereo" in lay else "multichannel" if "multi" in lay else "sacd"
            out.add((disc, kind if kind and kind != "cd" else "disc"))
        return out

    @staticmethod
    def _slots_overlap(a: set, b: set) -> bool:
        """DSC-11: two copies hold one disc or layer alike - the same disc's same layer, or an
        SACD layer naming no channels beside either of that disc's SACD layers."""
        for d, k in a:
            for d2, k2 in b:
                if d == d2 and (k == k2 or ("sacd" in (k, k2) and "disc" not in (k, k2))):
                    return True
        return False

    @staticmethod
    def _vinyl_rips(members) -> list:
        """VIN-1: [(resolution, ripper)] of each member folder holding vinyl (its files' media) -
        a CD beside them (a CD and LP box ripped side by side) is no rip and is not compared: the
        audio's own bits and rate (vinylrip.resolution_of) and the ripper its name, notes or
        comment clearly name, the notes of the folders' common parent read with each ("" where
        none is named: it matches any)."""
        import vinylrip
        out = []
        parent_notes = {}
        for _i, part, p in members:
            media = [m for t in p.targets or () for m in t.get("media") or ()]
            if not media or not any(mc.media_kind(m) == "vinyl" for m in media):
                continue
            up = Path(part.folder).parent
            if up not in parent_notes:
                parent_notes[up] = vinylrip.notes_here(up)
            d = vinylrip.describe(part.folder, p.files, notes=vinylrip.notes_of(part.folder),
                                  parent_notes=parent_notes[up])
            out.append((vinylrip.resolution_name(d["resolution"]), d["ripper"]))
        return out

    @staticmethod
    def _other_rips(rips) -> bool:
        """VIN-1: two of these rips are different ones: another resolution, or two rippers named
        and different (one named and one not: unknown, never a reason to keep them apart)."""
        res = {r for r, _w in rips if r}
        who = {w.casefold() for _r, w in rips if w}
        return len(res) > 1 or len(who) > 1

    def _release_folders(self, folder: Path, parts, per, out) -> dict:
        """DSC-11: album folders side by side (_one_release_groups), each planned alone first, as
        before. Those each matched for sure on their own evidence (_sure: exact at the auto
        threshold - never album known, a TIE-1 suggestion nor a pressing picked by country
        order) to one release - or its twin on the other database, the same pressing (_same_key)
        - whose files take different discs or layers of it (_slots: a hybrid SACD's CD layer
        and its stereo and multichannel layers are three; any disc or layer taken twice is two
        copies of it: they stay apart) are one album of it: one Proposal, its files and targets
        each folder's own as planned alone (their discs, layers and the release's audio disc
        total), its group_key the first folder's (set_key), its folders remembered
        (set_members, one_release_folders: plan.Planner files their own covers, scans and notes
        at the album's root). Else each folder keeps its own plan. {"own", "members", "joined"}
        as _release_set."""
        sure = []
        for i, part, ps in per:
            if len(ps) != 1:
                continue
            p = ps[0]
            if not self._sure(p) or getattr(p, "guessed_pick", False) or getattr(p, "suggested", None) \
                    or getattr(p, "disc_folder", None) is not None or getattr(p, "split", None) \
                    or getattr(p, "suspect_lossy", "") or getattr(p, "parts", None) \
                    or getattr(p, "awaits", "") or len(p.targets or []) != len(p.files or []) or not p.files:
                continue
            if self._release_of(p) is None:
                continue
            sure.append((i, part, p))
        kinds = []                                       # [(release key, its candidate or None, [(i, part, p)])]
        for i, part, p in sure:
            key = self._release_of(p)
            for k in kinds:
                if key == k[0]:
                    k[2].append((i, part, p))
                    break
                if key[0] != k[0][0]:
                    if k[1] is None:
                        k[1] = self._release_cand(*k[0]) or {}
                    if self._same_key(k[0], key, k[1]):
                        k[2].append((i, part, p))
                        break
            else:
                kinds.append([key, None, [(i, part, p)]])
        kinds = sorted((k for k in kinds if len(k[2]) >= 2), key=lambda k: -len(k[2]))
        if not kinds:
            return out
        if len(kinds) > 1 and len(kinds[1][2]) == len(kinds[0][2]):
            self.log.info("%s: two releases each hold as many of its album folders: each planned alone", folder)
            return out
        key, _cand, members = kinds[0]
        # (no disc or layer taken twice: two copies of one disc stay apart, never joined)
        slots = [self._slots(p) for _i, _part, p in members]
        for a in range(len(slots)):
            for b in range(a + 1, len(slots)):
                if self._slots_overlap(slots[a], slots[b]):
                    self.log.info("%s: '%s' and '%s' both hold a disc of %s: two copies, each planned alone", folder,
                                  members[a][1].folder.name, members[b][1].folder.name, key[1])
                    return out
        # VIN-1: rips of a record are copies, never layers: vinyl rips at another resolution or by
        # another ripper stay apart even where they hold different discs (side 1-2 by one ripper,
        # 3-4 by another are two partial rips, not one copy)
        rips = self._vinyl_rips(members)
        if len(rips) > 1 and self._other_rips(rips):
            self.log.info("%s: its album folders are different rips of a record of %s (%s): each planned alone",
                          folder, key[1], " / ".join(sorted({" ".join(x for x in r if x) for r in rips})))
            return out
        totals = {str((t.get("totaldiscs") or [""])[0]).strip() for _i, _part, p in members for t in p.targets}
        if len(totals) != 1:
            self.log.info("%s: its album folders count %s discs of %s: each planned alone", folder,
                          " / ".join(sorted(totals)), key[1])
            return out
        members.sort(key=lambda m: m[0])
        lead_i, lead_part, lead = members[0]
        joined = copy.copy(lead)

        def every(attr):
            return [x for _i, _part, p in members for x in (getattr(p, attr, None) or [])]

        def once(attr):
            return list(dict.fromkeys(every(attr)))
        joined.files = every("files")
        joined.targets = every("targets")
        fulls = [getattr(p, "full_targets", None) for _i, _part, p in members]
        if all(f and len(f) == len(p.files) for f, (_i, _part, p) in zip(fulls, members)):
            joined.full_targets = [t for f in fulls for t in f]
        joined.group_files = list(dict.fromkeys(str(f) for _i, part, p in members
                                                for f in (getattr(p, "group_files", None) or part.files)))
        for attr in ("sidecars", "restore", "provider_errors"):
            merged = {}
            for _i, _part, p in members:
                merged.update(getattr(p, attr, None) or {})
            setattr(joined, attr, merged)
        for attr in ("evidence", "why", "reason_codes", "release_proven_over"):
            setattr(joined, attr, once(attr))
        cr = [getattr(p, "credits_read", None) or {} for _i, _part, p in members]
        if all(cr):
            # (REP-2: credits taken back only where every folder's database read them whole)
            joined.credits_read = {k: (all(c.get(k) for c in cr) if k != "protected" else
                                       sorted({x for c in cr for x in c.get(k) or ()})) for k in cr[0]}
        names = ", ".join(f"'{part.folder.name}' -> " + ", ".join(
            f"disc {d}" + ("" if k == "disc" else f" ({k} layer)")
            for d, k in sorted(self._slots(p), key=lambda s: (int(s[0]) if s[0].isdigit() else 0, s[1])))
            for _i, part, p in members)
        note = f"one release, {len(members)} album folders: {names} ({_release_words(lead)})"
        joined.notes = [note] + once("notes")
        joined.set_key = group_key(lead_part.folder, lead_part.files)
        joined.set_members = [part for _i, part, _p in members]
        joined.one_release_folders = [str(part.folder) for _i, part, _p in members]
        # (each copy's own files: its own mark, apply_level - one folder may hold two copies, MED-22c)
        joined.one_release_files = [[str(f) for f in p.files] for _i, _part, p in members]
        out["members"], out["joined"] = {i for i, _part, _p in members}, [joined]
        self.log.info("%s: %s", folder, note)
        return out

    def _tagged_disc(self, g: Group):
        """IDM-1: the disc folder of a set (its folder's subfolder) holding all of this album's
        files, when every disc of the set was tagged by a job of TagMender's as a release of its
        own (_per_disc: each disc's files one release's IDs, the discs' different - REP-1's
        ownership index) and albums_in grouped the set by those tags: None for any other album
        (a box whose discs another tagger titled as albums of their own is split as before)."""
        if not g.others or g.discs:
            return None
        parents = {Path(f).parent for f in g.files}
        if len(parents) != 1:
            return None
        d = next(iter(parents))
        skips = self._skipper()
        if d.parent != g.folder or not set_dir(d, skips):
            return None
        if any(Path(f).parent == d or Path(f).parent.parent != g.folder or not set_dir(Path(f).parent, skips)
               for f in g.others):
            return None
        try:
            owned = ma._own_index()
        except Exception:
            return None
        ids = {}
        for f in list(g.files) + list(g.others):
            own = owned.get(str(f)) or owned.get(os.path.realpath(str(f)))
            key = frozenset(str(x).lower() for part in (own or ()) for x in part)
            if not key:
                return None                              # (not TagMender's own write)
            ids.setdefault(Path(f).parent, set()).add(key)
        if any(len(v) != 1 for v in ids.values()):
            return None                                  # (a disc of two releases)
        keys = [next(iter(v)) for v in ids.values()]
        return d if len(set(keys)) == len(keys) else None

    def identify_all(self, g: Group) -> list:
        """identify, as a list of albums: one, or each disc of a set whose discs are
        different releases (_per_disc), each its own album. DSC-10: an album of sibling
        folders (Group.siblings) is planned as _release_set has it - alone, as before, or as
        the one album of the release that holds it and its siblings."""
        if len(getattr(g, "siblings", None) or ()) > 1 and not getattr(self, "_in_set", False) and \
                not getattr(self, "_set_alone", False):           # (the watcher: watcher.siblings_settled)
            return self._set_member(g)
        prop = self.identify(g)
        if getattr(g, "layer_hold", "") and prop.status != "identified":
            prop.reason = g.layer_hold + (f" ({prop.reason})" if prop.reason else "")   # (MED-22c)
        return [prop] + list(getattr(prop, "parts", None) or [])

    def _finish(self, prop, g: Group):
        """What every identified album gets after identification."""
        # Picard's %_multiartist% (the tracks are by more than one artist): MusicBrainz's comes
        # with the release (mb_tags, before any plugin); else from the tracks' artists
        if prop.status == "identified" and prop.targets and not str(prop.provider or "").startswith("musicbrainz") \
                and len({"; ".join(map(str, t.get("artist") or [])) for t in prop.targets}) > 1:
            for t in prop.targets:
                t.setdefault("~multiartist", ["1"])
        if prop.status == "identified" and prop.targets:
            other = "musicbrainz" if self.source.startswith("discogs") else "discogs"
            follow_source(prop, self.source, asked=(self.use_mb if other == "musicbrainz" else self.use_discogs)
                          and not (prop.provider_errors or {}).get(other), sure=getattr(prop, "sure_picks", None),
                          differs=lambda f, v: bool(self._another_pressing(
                              prop, getattr(prop, "release_view", None), f, v)[0]))
            self._stale_other_ids(prop)                  # TAG-4
        # every audio file of the album as found (its second copies too: "01 a.flac" and
        # "01 - a.mp3"), for telling its covers from another album's (not saved in plans)
        disc = getattr(prop, "disc_folder", None)          # (one disc of a set, as its own album)
        prop.group_files = [str(f) for f in g.files if disc is None or Path(f).parent == disc]
        if getattr(prop, "split", None) and disc is None:    # (one version of a folder holding two)
            prop.group_files = [str(f) for f in prop.files]

    def _identify(self, g: Group, names=None) -> planmod.Proposal:
        """names: (artist folder, album folder) the album is read with, when they are not
        its folder's own (one disc of a set, identified alone: _per_disc). Its files' tags
        and rule metadata are read once (ex.meta_memo, tagio.reading)."""
        with ex.meta_memo(), tagio.reading():
            if names is None and len(getattr(g, "discs", None) or ()) > 1:
                return self._identify_set(g)
            return self._identify_album(g, names)

    def _identify_set(self, g: Group) -> planmod.Proposal:
        """DSC-1: the discs of a set in folders side by side ("Album CD1", "Album CD2"),
        matched as one release: the matcher reads a temporary folder named after the set,
        holding each disc's folder as links to its files (its scans and logs too), so they
        are discs 1, 2 ... of one album; what it says of the files is said of the real ones."""
        import shutil
        import tempfile
        stem = (sibling_disc(g.discs[0]) or (g.folder.name, 1))[0]
        tmp = Path(tempfile.mkdtemp(prefix="tagmender-discs-"))
        top, real_of = tmp / stem, {}
        mine, skips = {str(f) for f in g.files}, self._skipper()
        try:
            for d in g.discs:
                for dirpath, dirs, names in os.walk(d):
                    dirs[:] = sorted(x for x in dirs if not skips(x))
                    for n in names:
                        src = Path(dirpath) / n
                        if src.suffix.lower() in ex.AUDIO_EXT and str(src) not in mine:
                            continue                     # (not this album's: never read with it)
                        dst = top / Path(d).name / src.relative_to(d)
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        os.symlink(src, dst)
                        real_of[str(dst)] = str(src)
            return self._identify_album(g, (g.folder.parent.name, stem), stage=(top, real_of))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _identify_album(self, g: Group, names=None, stage=None) -> planmod.Proposal:
        """stage: (folder, {link: real file}) the matcher reads instead of the album's folder
        (a set of discs side by side: _identify_set)."""
        if names is None and is_version_dir(g.folder, skips=self._skipper()):
            # a version folder named alone ("2.0/" of an SACD's set): the set's folder names
            # the release, as for one disc of a set (_per_disc)
            names = (g.folder.parent.parent.name, one_disc_name(g.folder.parent.name))
        artist_folder, album_folder = names or (g.folder.parent.name, g.folder.name)
        self._last_out = None
        # OCR and gnudb are not providers an album needs: their notes are told once for the
        # run, not as each album's error
        errors = {k: v.split(":")[0] for k, v in self.notes.items() if k not in RUN_NOTES}
        # the rules first, on what the files themselves say: an excluded album gets no lookup,
        # fingerprint or web search at all (the planner labels it Ignored/Skipped)
        if ex.evaluate(g.files, self.rules, overrides=ex.State().overrides()).all_excluded:
            return planmod.Proposal(source_dir=str(g.folder), files=[str(f) for f in g.files],
                                    status="unresolved", reason="excluded by a rule", reason_codes=["other"],
                                    tier="ignored")
        # one disc folder holding two versions of the album ("101.T.flac" and "101.T (1).flac"):
        # never tagged as one release, nor as one album with half its files dropped as copies -
        # unless one release proven exactly holds every file (a 2CD edition ripped into one
        # folder, its second disc's other takes under the same titles): the matcher is asked first
        vinylish = _vinylish(g)
        mixed = mixed_versions(g.files, rips=vinylish)
        # VIN-7: two rips of one record in one folder, told apart by their own tags (not by names) -
        # a vinyl rip's folder only (a CD set's discs numbered 1..n each, untagged, are no rips)
        rips = rip_sets(g.files) if not mixed and not stage and vinylish else []
        where = stage[0] if stage else g.folder         # DSC-1 (a set side by side: its stage)
        # (FLD-2: the folder the album's name belongs to - a disc's or version's folder is read with
        # its set's name, a set side by side through a stage: the folders beside that one tell
        # whether the layout is flat)
        layout = next((p for p in (g.folder, *g.folder.parents) if p.parent != p and p.parent.name == artist_folder),
                      g.folder)
        row = {"FullPath": str(where), "AlbumArtistFolder": artist_folder, "AlbumFolder": album_folder,
               "Status": "untagged", "LayoutDir": str(layout)}
        outside = [] if stage else self._outside(g)
        args = argparse.Namespace(ocr=self.ocr, max_images=getattr(self, "ocr_images", 15), process_anyway=False,
                                  use_mb=self.use_mb, rules=self.rules,
                                  use_discogs=self.use_discogs, provider_errors=errors, exclude_files=outside,
                                  use_shops=self.profile.provider_on("web", "shops"),
                                  use_gnudb=self.profile.provider_on("web", "gnudb"),
                                  ai=getattr(self, "ai", None),
                                  acoustid="off" if getattr(self, "_trialling", False) else
                                  ma.acoustid_mode(self.profile.get("acoustid", "unsure")))
        out = None
        if self.use_mb or self.use_discogs:
            try:
                try:
                    out = ma.process(row, self.http, self.cfg, self.dm, args)
                except ma.AuthError as exc:                  # token rejected: carry on without Discogs
                    errors["discogs"] = "missing_credentials"
                    self.notes["discogs"] = f"missing_credentials: Discogs rejected the token ({exc})"
                    self.log.error("Discogs rejected the token: %s", exc, extra={"provider": "discogs"})
                    self.use_discogs = args.use_discogs = False
                    self.token = ""                          # (not offered again this run)
                    out = ma.process(row, self.http, self.cfg, self.dm, args) if self.use_mb else None
            except ma.ProviderUnavailable as exc:
                errors[{"mb": "musicbrainz"}.get(exc.host, exc.host)] = exc.kind
                self.log.warning("%s", exc, extra={"provider": exc.host})
        if errors.get("musicbrainz") in ("outage", "rate_limited", "invalid_response"):
            self._note_mb_down(argparse.Namespace(kind=errors["musicbrainz"]))
        if out and out.get("ai"):                    # (AI-1: counted in the planner's log)
            self.log.info("AI for %s: %d requests, %d answers from the cache", g.folder,
                          out["ai"]["calls"], out["ai"]["cache_hits"], extra={"provider": "ai"})
        self._last_out = out                         # (the matcher's answer: _versions_split's candidates)
        held = self._preferred_down(errors, g.folder, [str(f) for f in g.files])
        if held is not None:
            return held
        trialling = getattr(self, "_trialling", False)      # DSC-10: a whole-set search, nothing more
        if mixed and not _holds_every_file(out, len(g.files)):
            if trialling:
                return planmod.Proposal(source_dir=str(g.folder), files=[str(f) for f in g.files],
                                        status="unresolved", tier="unresolved", reason_codes=["other"],
                                        reason="set search: no one release holds every file")
            return self._split_or_mixed(g, mixed, errors)
        if rips and not trialling and not getattr(self, "_disc_alone", False) and \
                not _holds_every_file(out, len(g.files)):
            return self._rips_or_held(g, rips, errors)
        album = ma.read_album(where, artist_folder, album_folder, exclude={str(x) for x in outside})
        # FLD-2: the reading of the folder name the matcher's answer rests on ("Artist - Album" of a
        # flat layout): the plan, tags and naming read the album as it did
        album = ma.with_reading(album, (out or {}).get("folder_reading"))
        if stage:                                        # (the real files, not the stage's links)
            for t in album["tracks"]:
                if t.get("path"):
                    t["path"] = stage[1].get(str(t["path"]), t["path"])
        mine = {str(f) for f in g.files}
        files = [t["path"] for t in album["tracks"] if t.get("path") in mine] or [str(f) for f in g.files]
        base = dict(source_dir=str(g.folder), files=files, provider_errors=errors)
        if out and out.get("status") in ("ignored", "skipped"):   # a rule matched the identified release
            return self._unresolved(out, files, base, errors)
        if out and out.get("status") == "auto" and out.get("best"):
            prov = {"mb": "musicbrainz"}.get(out["best"]["source"], out["best"]["source"])
            try:
                p = self._identified(out, album, files, base)
                withdrawn = self._own_guess_withdrawn(p, out)
                guess = (out["best"] or {}).get("tie_guess")
                if withdrawn is not None and guess and withdrawn.status == "identified":
                    # (TAG-1's album only, its pick a tie-break's: still yours to confirm - TIE-1)
                    withdrawn.suggested = suggestion_of(guess, out["best"])
                if withdrawn is None:
                    # TIE-1: a pressing only a tie-break chose is proposed; the album is album known
                    withdrawn = self._tie_suggested(p, out, album)
                return withdrawn if withdrawn is not None else self._one_release(p, out, album)
            except ma.ProviderUnavailable as exc:             # fetching the release failed: retry later
                self.log.warning("tags for %s: %s", g.folder, exc)
                errors[prov] = exc.kind
                code = exc.kind if exc.kind == "rate_limited" else f"{prov}_unavailable"
                return planmod.Proposal(**base, status="unresolved", reason_codes=[code],
                                        reason=f"identified, but the release could not be fetched: {exc}")
            except Exception as exc:                          # the release does not fit the files after all
                self.log.warning("tags for %s: %s", g.folder, exc)
                return planmod.Proposal(**base, status="unresolved", reason_codes=["incomplete_album"],
                                        reason=f"matched, but the release's tracks do not line up with the files: {exc}")
        if trialling:
            # (DSC-10: the set is one release only when one is proven for it; no album-only,
            # Partial, web, fingerprint or disc-by-disc answer is looked for)
            return planmod.Proposal(**base, status="unresolved", tier="unresolved", reason_codes=["other"],
                                    reason="set search: no release proven for the whole set")
        cv = ((out or {}).get("best") or {}).get("copy_version") if out and out.get("status") != "auto" else ""
        if cv:
            # your copy names a version (an instrumental album) no release found is: held, never
            # tagged as the vocal album's release nor its album-only or Partial tags
            held = planmod.Proposal(**base, status="unresolved", reason_codes=["conflicting_sources"],
                                    reason=f"your folder or album title names the {cv} version, and no release "
                                           f"found is it (the best fit, {out['best'].get('id')}, is not): "
                                           "choose the release with Album > Change Release",
                                    why=list(out.get("why") or []))
            # (TagMender's own earlier pick of that release is withdrawn: what its job wrote of the
            # pressing goes back to what the files held before it)
            held.restore = self._withdraw_own(held.files, out, pick=None, title=True, errors=errors)
            if held.restore:
                held.notes = list(held.notes or []) + [
                    "what TagMender's earlier job wrote of that release is taken back (TAG-1)"]
            return held
        if out and out.get("reading_capped"):
            # FLD-2: found only under the folder name's "Artist - Album" reading, and the folder
            # above is no flat layout (it may be the artist's own folder): the release is proposed
            # for you to confirm - no album-known, Partial or web tags are written on that reading
            return self._unresolved(out, files, base, errors)
        if out and out.get("album_only") and out.get("best") and self._accepts("album") and \
                not _provider_failed(errors):
            # the album is certain, only its pressing is not: tag what every pressing shares
            # (never while MusicBrainz or Discogs failed: once both answer, the pressing may be proven)
            try:
                ao = self._album_only(self._identified(out, album, files, base, versions=False), out["album_only"])
                if out.get("own_disproved"):
                    # (TagMender's own earlier pick lost its standing: its writes go, TAG-1)
                    ao.restore = self._withdraw_own(ao.files, out, errors=errors)
                return ao
            except Exception as exc:
                self.log.warning("album-only tags for %s: %s", g.folder, exc)
        alone = getattr(self, "_disc_alone", False)       # (one disc of a set: _per_disc)
        if g.single and not alone and len(files) == 1 and self.use_mb and ma.fingerprint_ready() and \
                ma.acoustid_mode(self.profile.get("acoustid", "unsure")) != "off" and \
                (not hasattr(self.profile, "identifies_singles") or self.profile.identifies_singles()):
            p = self._single(Path(files[0]), base)
            if p:
                return p
        # (unresolved.action "leave" writes nothing on an album whose release is not proven
        # unless you tick it: WEB-1 below)
        ur = self.profile["unresolved"]
        partial_ok = ur.get("allow_partial", True) and ur.get("action") != "leave" and \
            not _provider_failed(errors) and self._accepts("partial")
        # WEB-1: with unresolved.action "leave" the web is still searched; what it establishes
        # is planned but held - shown in the preview, unticked, never applied unless you tick
        # it (a watched folder never applies it). Never for one disc or version of a set (a
        # held part must not let the set's other parts be filed)
        hold = (not partial_ok and ur.get("action") == "leave" and ur.get("allow_partial", True)
                and not _provider_failed(errors) and self._accepts("partial") and not alone)
        if (partial_ok or hold) and not alone and out and out.get("status") in PARTIAL_FROM and out.get("best"):
            # the album is established though its release is not: what every pressing shares
            # (never while MusicBrainz or Discogs failed: the album is retried, it may be matched
            # exactly once both answer)
            try:
                p = self._partial(out, album, files, base)
            except ma.ProviderUnavailable as exc:         # retried later, as the auto path is
                self.log.warning("partial tags for %s: %s", g.folder, exc)
                errors[{"mb": "musicbrainz"}.get(exc.host, exc.host)] = exc.kind
                p = None
            except Exception as exc:                      # never more than the fallback it is
                self.log.warning("partial tags for %s failed: %s", g.folder, exc)
                p = None
            if p and partial_ok:
                if (out or {}).get("own_disproved"):
                    # (Partial on files that name TagMender's withdrawn pick: its writes go, not kept)
                    p.restore = self._withdraw_own(p.files, out, title=True, errors=errors)
                return p
            if p:
                # (WEB-1: a database established the album: "leave" leaves it as it is, and
                # the web, which would only rival that album, is not asked)
                hold = False
        # the album on the web only (MusicBrainz and Discogs established nothing): Partial. (A
        # web-only EP holding a track and its remix is matched here, before the folder is
        # called two versions mixed, below)
        if (partial_ok or hold) and self.web_partial_on() and album["tracks"]:
            p = self._web(out, album, files, base, by_lengths=alone)
            if p:
                if hold:
                    p.held = (f"held: found on {p.provider.split(':', 1)[-1]}, but Unresolved is set to "
                              f"\"leave\" in this profile: nothing is written unless you tick it and apply")
                return p
        # titles twice, once with a version's mark ("Twilight" and "Twilight (Remix 2024)"),
        # in a folder no release and no web album fits: two versions of the album rather than
        # one EP's track and its remix (that EP was matched above)
        mixed = mixed_versions(g.files, versions=True)
        if mixed:
            return self._split_or_mixed(g, mixed, errors)
        # a set whose discs are different releases of the album (a remaster and its remix):
        # each disc identified as its own release - and, when they are versions of one album,
        # each planned into a folder of its own (split_versions)
        if not alone and not names:
            self._disc_outs = []
            self._set_note = ""
            p = self._per_disc(g, errors)
            if self.split_on() and not (p and p.tier in ("", "exact") and
                                        all(q.tier in ("", "exact") for q in p.parts or [])):
                # (no disc proven exactly alone: the album's release group's versions, by the lengths)
                was, self._splitting = getattr(self, "_splitting", False), True   # (as _split_or_mixed)
                try:
                    q = self._discs_by_group(g, [out] + list(self._disc_outs), errors)
                finally:
                    self._splitting = was
                if q:
                    return q
            if p:
                return self._discs_split(g, p)
            # (the discs, read alone, left the matcher's last answer theirs: the album's own
            # answer is what its proposal, candidates and scans report are of)
            self._last_out = out
        known = self._known_from_own_tags(out, files, base) if partial_ok and not alone else None
        if known:
            return known
        prop = self._unresolved(out, files, base, errors)
        if (out or {}).get("own_disproved"):
            # (TagMender's own earlier pick lost its standing and nothing decides now: its writes go)
            prop.restore = self._withdraw_own(prop.files, out, pick=None, title=True, errors=errors)
        note = getattr(self, "_set_note", "") if not alone and not names else ""
        if note:
            prop.why = list(prop.why or []) + [note]
            prop.notes = list(prop.notes or []) + [note]
            prop.reason = f"{prop.reason}; {note}" if prop.reason else note
        return prop

    def _preferred(self) -> str:
        """HF-4: the database the Sources setting prefers ("musicbrainz" / "discogs"), when the
        other one is asked too; "" when there is no preference (a *_only source, or a matcher
        order with no preferred database)."""
        if not str(getattr(self, "source", "") or "").endswith("_preferred"):
            return ""
        pref = (getattr(self, "cfg", None) or {}).get("prefer_source", "")
        return pref if pref in ("musicbrainz", "discogs") else ""

    def _preferred_down(self, errors, folder, files):
        """HF-4: the Sources-preferred database is down, rate-limited or not answering for this
        album: it is held (Unresolved with that database's error, so the watcher retries it on
        PROVIDER_RETRY), never decided from the other database alone. None: not held."""
        pref = self._preferred()
        kind = (errors or {}).get(pref) if pref else None
        if kind not in ("outage", "rate_limited", "invalid_response"):
            return None
        if not (self.use_mb if pref == "musicbrainz" else self.use_discogs):
            return None
        name, other = (("MusicBrainz", "Discogs") if pref == "musicbrainz" else ("Discogs", "MusicBrainz"))
        code = "rate_limited" if kind == "rate_limited" else f"{pref}_unavailable"
        words = (f"{name} (preferred in Sources) did not answer ({kind.replace('_', ' ')}): held and "
                 f"tried again later, not decided from {other} alone")
        return planmod.Proposal(source_dir=str(folder), files=list(files), provider_errors=errors,
                                status="unresolved", tier="unresolved", reason=words, reason_codes=[code],
                                why=[words])

    def _per_disc(self, g: Group, errors):
        """The album's discs, each identified alone as a one-disc release, when the whole
        set fits no release: the first disc's Proposal, the others in its .parts (each disc
        folder is then planned as an album of its own, tagged from its own release: its
        title, its disc and track numbers). Only when every disc folder proves its own
        release, as strongly as a whole album must: a MusicBrainz or Discogs release
        matched for sure, or a web album whose track lengths agree (a Partial album by
        titles, or from MusicBrainz's or Discogs's album, names no version), and no two
        discs the same release. None otherwise (the set stays as it was found)."""
        discs = sorted({Path(f).parent for f in g.files}, key=lambda d: ma._natural_key(d.name))
        if len(discs) < 2 or any(d.parent != g.folder or not set_dir(d, self._skipper()) for d in discs):
            return None
        props = []
        self._disc_alone = True
        try:
            for d in discs:
                sub = Group(d, [f for f in g.files if Path(f).parent == d])
                # (the set's folder names for the disc: "[2CD]" said of the set, not of one disc)
                p = self._identify(sub, names=(g.folder.parent.name, one_disc_name(g.folder.name)))
                self.__dict__.setdefault("_disc_outs", []).append(getattr(self, "_last_out", None))
                own = p.status == "identified" and (p.tier in ("", "exact") or
                                                    (p.tier == "partial" and str(p.provider).startswith("web:")))
                if not own:
                    self.log.info("discs of %s not identified alone: %s %s", g.folder, d.name,
                                  p.reason or "; ".join(p.why or []))
                    return None
                p.disc_folder = d
                props.append(p)
        finally:
            self._disc_alone = False
        keys = [(p.provider, p.release_id or p.url or _release_words(p)) for p in props]
        if len(set(keys)) < len(keys):
            if all(is_version_dir(d, skips=self._skipper()) for d in discs):
                # versions of one disc ("2.0/", "5.0/") that each fit the same release, whose
                # media do not say which is which (match_core.version_view): never tagged as
                # one disc twice, into one folder
                self._set_note = (f"the version folders {', '.join(d.name for d in discs)} each fit "
                                  f"{_release_words(props[0])}, whose media do not tell which version each is "
                                  "(no medium names its channel layout): left for you to choose a release for "
                                  "each folder, or to file them apart")
            return None
        names = "; ".join(f"{d.name}: {_release_words(p)}" for d, p in zip(discs, props))
        note = f"the discs of this set are different releases of the album, each tagged from its own ({names})"
        for p in props:
            p.why = [note] + list(p.why or [])
            p.notes = list(p.notes or []) + [note]
            p.provider_errors = dict(errors, **(p.provider_errors or {}))
        first = props[0]
        first.parts = props[1:]
        return first

    def _mixed(self, g, mixed, errors, why=""):
        """The unresolved album of a folder with two versions of the album mixed in it
        (mixed_versions), and - when the web knows both versions and the files' lengths tell
        them apart - which files are which version, as plan notes. Nothing is split or moved.
        why: why TagMender does not split it itself (_versions_split)."""
        n = sum(len(v) for v in mixed.values())
        words = (f"{MIXED_WORDS} in this folder ({n} titles twice): "
                 "split them into separate folders")
        if why:
            words += f" (not split for you: {why})"
        files = [str(f) for f in g.files]
        notes = []
        if self.profile.provider_on("web", "enabled"):
            try:
                import web_tag
                album = ma.read_album(g.folder, g.folder.parent.name, g.folder.name,
                                      exclude={str(x) for x in self._outside(g)})
                found = web_tag.album_candidates(self.http, album, self.log, self._web_sources()) \
                    if album["tracks"] else []
                notes = versions_apart(mixed, found, self.cfg)
            except Exception as exc:                  # (the notes only help; the album stays unresolved)
                self.log.warning("versions of %s: %s", g.folder, exc)
        return planmod.Proposal(source_dir=str(g.folder), files=files, status="unresolved", tier="unresolved",
                                reason=words, reason_codes=["other"], why=[words], notes=notes,
                                provider_errors=errors)

    # ---- two versions of an album in one folder: a split into a folder per version ------
    def split_on(self) -> bool:
        """split_versions (on by default): two versions of an album found in one folder are
        planned into a folder each."""
        return self.profile.get("split_versions", True) is not False

    def _split_or_mixed(self, g, mixed, errors):
        """The versions of a folder holding two (mixed_versions), each as its own album
        (_versions_split), else the folder unresolved (_mixed) with why it is not split."""
        why = ""
        if self.split_on():
            # (each version keeps its mark in its title even with keep_versions off: the two
            # folders and albums would not be told apart otherwise)
            was, self._splitting = getattr(self, "_splitting", False), True
            try:
                p, why = self._versions_split(g, mixed, errors)
            except ma.ProviderUnavailable as exc:      # (retried later, as any lookup)
                errors[{"mb": "musicbrainz"}.get(exc.host, exc.host)] = exc.kind
                p, why = None, f"a lookup failed ({exc})"
            finally:
                self._splitting = was
            if p:
                return p
        if mc.media_hint(g.folder.name) == "vinyl":
            # VIN-7: a vinyl rip's folder holding two copies of each track - two rips, not
            # versions: held and listed so (Needs Attention, "two rips in one folder")
            pairs = [pr for prs in mixed.values() for pr in prs]
            sides = [sorted({Path(a) for a, b in pairs if not _RIP_COPY_SUFFIX.search(Path(a).stem)} |
                            {Path(b) for a, b in pairs if not _RIP_COPY_SUFFIX.search(Path(b).stem)}),
                     sorted({Path(a) for a, b in pairs if _RIP_COPY_SUFFIX.search(Path(a).stem)} |
                            {Path(b) for a, b in pairs if _RIP_COPY_SUFFIX.search(Path(b).stem)})]
            if all(sides):
                return self._rips_held(g, sides, why, errors)
        return self._mixed(g, mixed, errors, why)

    def _version_alone(self, g, files):
        """files (one version of the album in g's folder) identified as an album of their own:
        the folder's other files left out, as one disc of a set is (_per_disc)."""
        sub = Group(g.folder, sorted(files, key=lambda f: ma._natural_key(str(f))))
        was = getattr(self, "_disc_alone", False)
        self._disc_alone = True
        try:
            return self._identify(sub, names=(g.folder.parent.name, one_disc_name(g.folder.name)))
        finally:
            self._disc_alone = was

    def _versions_split(self, g, mixed, errors):
        """(Proposal, "") of a folder holding two versions of the album (mixed_versions): the
        first version's album, the other in its .parts, each with .split (plan.Planner puts
        each into a folder of its own) - or (None, why not). The candidates are the releases
        each half of the folder is matched with (a copy's " (1)", the titles' version marks or
        the album tags make the halves) or, when the matcher proves no two releases that way,
        the releases of the album's MusicBrainz release group that name a version ("remaster
        2024", "remix 2024"). Every file must fit one candidate's track lengths (assign_versions),
        each version must then be proven as an album of its own, the two different releases
        with different titles."""
        pairs = [pr for prs in mixed.values() for pr in prs]
        paired = {f for pr in pairs for f in pr}
        loose = [Path(f) for f in g.files if Path(f) not in paired]
        info = {}
        for f in [Path(x) for x in g.files]:
            try:
                info[f] = ma.read_file(f)
            except Exception as exc:
                return None, f"{f.name} cannot be read ({exc})"
        vinylish = _vinylish(g)
        pairs, how = version_sides(pairs, info, _RIP_COPY_SUFFIX if vinylish else None)
        guided = bool(how)
        halves = [[a for a, _b in pairs], [b for _a, b in pairs]]
        props, outs = [], []
        for fs in halves:
            props.append(self._version_alone(g, fs))
            outs.append(getattr(self, "_last_out", None))
        why = _not_own(props)
        if why.startswith("both halves fit the same release") and not loose and vinylish and \
                (how == "copy names" or sorted(map(sorted, halves)) == sorted(map(sorted, rip_sets(g.files)))):
            if not one_rip_each(halves):
                # (the copy marks fall on files of both rips: never split on them)
                return None, ("the copy marks in the names do not divide the files into two rips (each set's "
                              "files would be of both)")
            # VIN-7: two rips of one record saved into one folder ("01.AFTK.flac", "01.AFTK (1).flac")
            got, why2 = self._rips_split(g, props, halves, errors)
            if got is not None:
                return got, ""
            why = why2 or why
        if not why:
            got, unsure, _cost = assign_versions(pairs, loose, info, [p.release_secs for p in props], self.cfg, guided)
            if unsure:
                why = (f"their lengths do not tell these apart: {_names(unsure)} "
                       f"({_release_words(props[0])} or {_release_words(props[1])})")
            else:
                if [sorted(x) for x in got] != [sorted(x) for x in halves]:
                    # (the lengths say otherwise: the files identified again, as they are)
                    keys = [_release_key(p) for p in props]
                    props = [self._version_alone(g, fs) for fs in got]
                    if _not_own(props) or [_release_key(p) for p in props] != keys:
                        return None, "the files' lengths and the releases they fit do not settle"
                return self._split_done(g, props, got, errors)
        # the album's release group: its releases that name a version, against the files' lengths
        found, why2 = self._group_split(g, pairs, loose, info, guided, outs)
        if found:
            return self._split_done(g, *found, errors)
        return None, why2 or why

    def _split_done(self, g, props, groups, errors):
        """(Proposal, "") of the split, or (None, why) when the versions' titles are the same."""
        titles = [mc.fold(((p.targets or [{}])[0].get("album") or [""])[0]) for p in props]
        if titles[0] == titles[1]:
            return None, (f"{_release_words(props[0])} and {_release_words(props[1])} have the same title: "
                          "their folders would not tell them apart")
        lines = "; ".join(f"{_release_words(p)}: {_names(sorted(fs, key=lambda f: ma._natural_key(str(f))))}"
                          for p, fs in zip(props, groups))
        note = (f"{len(props)} versions of this album were in one folder ({lines}): "
                "each goes to a folder of its own, tagged from its own release")
        return self._as_split(g, props, note, errors), ""

    def _group_split(self, g, pairs, loose, info, guided, outs):
        """((props, groups), "") from the releases of the MusicBrainz release group the matcher
        found the halves in that name a version, the two that the files' lengths fit (each
        version then tagged from its release, as one chosen for it) - or (None, why not). Two
        releases of one version fitting alike (a CD and its download) settle nothing."""
        mbids = [((o or {}).get("best") or {}).get("mb_id") for o in outs]
        mbid = next((m for m in mbids if m), "")
        if not (mbid and self.use_mb):
            return None, ""
        rel = self.http.get_json(f"https://musicbrainz.org/ws/2/release/{mbid}?inc=release-groups&fmt=json", "mb")
        rg = ((rel or {}).get("release-group") or {}).get("id")
        if not rg:
            return None, ""
        data = self.http.get_json(f"https://musicbrainz.org/ws/2/release?release-group={rg}"
                                  "&inc=recordings+artist-credits+media&limit=100&fmt=json", "mb")
        cands = []
        for r in (data or {}).get("releases") or []:
            key = _mb_version(r)[0]
            secs = release_secs(r)
            if key and len(secs) >= len(pairs) and all(x for _t, x in secs):
                cands.append((key, r, secs))
        tries = []
        for i, (k1, r1, s1) in enumerate(cands):
            for k2, r2, s2 in cands[i + 1:]:
                if k1 == k2:
                    continue
                got, unsure, cost = assign_versions(pairs, loose, info, [s1, s2], self.cfg, guided)
                # (each version's files its release's every track: a release with more is
                # another edition - a deluxe set holding both - not this version)
                if not unsure and len(got[0]) == len(s1) and len(got[1]) == len(s2):
                    tries.append((cost, (r1, r2), got))
        if not tries:
            return None, (f"the files' lengths fit no two versions of the album's release group "
                          f"({len(cands)} releases name a version)") if cands else ""
        tries.sort(key=lambda x: x[0])
        if len(tries) > 1 and tries[1][0] - tries[0][0] <= 1.0:
            a, b = tries[0][1], tries[1][1]
            return None, (f"releases of the release group fit alike ({', '.join(r['id'] for r in a)} and "
                          f"{', '.join(r['id'] for r in b)})")
        _cost, (r1, r2), got = tries[0]
        props = [self._forced(Group(g.folder, sorted(fs, key=lambda f: ma._natural_key(str(f)))),
                              f"https://musicbrainz.org/release/{r['id']}") for r, fs in ((r1, got[0]), (r2, got[1]))]
        if any(p.status != "identified" for p in props):
            bad = next(p for p in props if p.status != "identified")
            return None, bad.reason or "a version's release does not fit its files"
        for p in props:
            p.why = [w for w in p.why if not str(w).startswith("the release you chose")]
            p.why.insert(0, "the release of the album's release group whose track lengths these files fit")
        return (props, got), ""


    def _discs_split(self, g, first):
        """A set whose discs are different releases (_per_disc), when they are versions of one
        album (one MusicBrainz release group, one Discogs master or, from the web, one title
        without its version) and split_versions is on: each disc planned into a folder of its
        own, as _versions_split does; else the discs as _per_disc left them (tagged in place)."""
        props = [first] + list(first.parts or [])
        if not self.split_on() or len(props) < 2:
            return first

        def one(key):
            vals = [tuple((p.targets or [{}])[0].get(key) or []) for p in props]
            return all(vals) and len(set(vals)) == 1, all(vals)
        same, known = one("musicbrainz_releasegroupid")
        if not known:
            same, known = one("discogs_master_id")
        if not known:
            bases = {mc.fold(mc.drop_release_type(web_album_title(((p.targets or [{}])[0].get("album") or [""])[0])))
                     for p in props}
            same = len(bases) == 1
        titles = [mc.fold(((p.targets or [{}])[0].get("album") or [""])[0]) for p in props]
        if not same or len(set(titles)) < len(titles):
            return first
        for p in props:                             # (_per_disc's own note says what each disc is)
            p.why = [w for w in p.why if not str(w).startswith("the discs of this set are different releases")]
            p.notes = [w for w in p.notes if not str(w).startswith("the discs of this set are different releases")]
        lines = "; ".join(f"{p.disc_folder.name}: {_release_words(p)}" for p in props)
        note = (f"{len(props)} versions of this album were in one folder, a disc each ({lines}): "
                "each goes to a folder of its own, tagged from its own release")
        first.parts = []
        return self._as_split(g, props, note, {}, own_dirs=True)

    def _rips_split(self, g, props, groups, errors):
        """VIN-7: (Proposal, "") splitting one folder holding two rips of one record - each set
        of files identified alone (_version_alone) and proven on its own (exact at the auto
        threshold, every track of the release there and every length agreeing), each a record's (its media vinyl) - into a copy each,
        filed apart as any two copies (their folders named by their rips, VIN-3); or (None, why
        not): the folder is then held, named in Needs Attention ("two rips in one folder")."""
        if self.profile.get("vinyl_rip_folders", True) is False:
            return None, "the profile keeps vinyl rips as any album (vinyl_rip_folders off)"
        same = same_audio_lengths(groups)
        if same:
            # (a half whose every length is the other's to the sample is no other rip: a transcode
            # of the same rip, or the same rip twice - never split, never two copies)
            return None, same
        for p in props:
            if p.status != "identified" or (p.tier or "exact") != "exact" or not self._complete(p) \
                    or len(p.targets or []) != len(p.files or []) or not self._sure(p) \
                    or not getattr(p, "all_lengths", False):
                return None, (f"{len(p.files)} of its files ({_names(p.files[:3])}...) are not a whole copy of "
                              "the record proven on its own")
            media = [m for t in p.targets or () for m in t.get("media") or ()]
            if not media or not all(mc.media_kind(m) == "vinyl" for m in media):
                return None, "the files are no record's rips"
        # (the copy without a copy's mark in its names, "x.flac" beside "x (1).flac", is the first)
        order = sorted(range(len(props)), key=lambda k: sum(bool(_RIP_COPY_SUFFIX.search(Path(f).stem))
                                                          for f in groups[k]))
        props, groups = [props[k] for k in order], [groups[k] for k in order]
        lines = "; ".join(f"{_names(sorted(fs, key=lambda f: ma._natural_key(str(f))))}" for fs in groups)
        note = (f"two rips of this record were in one folder ({lines}): each goes to a folder of its own, "
                "a copy of its own (VIN-7)")
        first = self._as_split(g, props, note, errors)
        import tagio
        for p in props:
            p.split["rips"] = True
            # (what each rip says of itself - its comment: plan._rips_apart gives a scan folder
            # to the rip whose words are on its scans)
            try:
                p.split["words"] = "\n".join(str(x) for x in tagio.read(p.files[0]).get("comment") or ())
            except Exception:
                p.split["words"] = ""
        return first, ""

    def _rips_held(self, g, groups, why, errors):
        """VIN-7: the folder holding two rips that are not split for you, held (unresolved) with
        both sets of files named - Needs Attention lists it as "two rips in one folder"."""
        lines = " and ".join(f"[{_names(sorted(fs, key=lambda f: ma._natural_key(str(f))))}]" for fs in groups)
        words = f"{RIPS_WORDS}: {lines}; sort them into a folder each" + (f" (not split for you: {why})" if why else "")
        return planmod.Proposal(source_dir=str(g.folder), files=[str(f) for f in g.files], status="unresolved",
                                tier="unresolved", reason=words, reason_codes=["two_rips"], why=[words],
                                provider_errors=errors)

    def _rips_or_held(self, g, groups, errors):
        """VIN-7: a folder holding two rips told apart by their tags (rip_sets): each set
        identified alone, split as _rips_split does - else held and listed (_rips_held)."""
        was, self._splitting = getattr(self, "_splitting", False), True
        try:
            props = [self._version_alone(g, fs) for fs in groups]
        except ma.ProviderUnavailable as exc:
            errors[{"mb": "musicbrainz"}.get(exc.host, exc.host)] = exc.kind
            return self._rips_held(g, groups, f"a lookup failed ({exc})", errors)
        finally:
            self._splitting = was
        got, why = self._rips_split(g, props, groups, errors) if self.split_on() else (None, "split_versions is off")
        return got if got is not None else self._rips_held(g, groups, why, errors)

    def _discs_by_group(self, g, outs, errors):
        """A two-disc set whose discs are two versions of the album (a remaster on disc 1, its
        remix on disc 2), told apart by the releases of the album's MusicBrainz release group
        that name a version (_group_split, the discs' tracks paired by title): each disc
        split into a folder of its own, tagged from the release its lengths fit. None when the
        discs are not paired title by title, or their lengths do not tell the versions apart."""
        discs = sorted({Path(f).parent for f in g.files}, key=lambda d: ma._natural_key(d.name))
        if len(discs) != 2 or any(d.parent != g.folder or not set_dir(d, self._skipper()) for d in discs):
            return None
        by = [sorted((Path(f) for f in g.files if Path(f).parent == d), key=lambda f: ma._natural_key(f.name))
              for d in discs]
        if len(by[0]) != len(by[1]) or len(by[0]) < 2:
            return None
        info = {}
        try:
            for f in by[0] + by[1]:
                info[f] = ma.read_file(f)
        except Exception:
            return None
        if not all(mc.track_titles_agree(web_album_title(info[a]["title"]), web_album_title(info[b]["title"]))
                   for a, b in zip(*by)):
            return None
        try:
            found, why = self._group_split(g, list(zip(*by)), [], info, True, outs)
        except ma.ProviderUnavailable:
            return None
        if not found:
            if why:
                self.log.info("discs of %s not split by the release group: %s", g.folder, why)
            return None
        props, got = found
        if sorted(got[0]) == sorted(by[1]):            # (the release group lists disc 2's version first)
            props, got = props[::-1], got[::-1]
        if sorted(got[0]) != sorted(by[0]):           # (each disc one version, as the lengths say)
            return None
        for p, d in zip(props, discs):
            p.disc_folder = d
        lines = "; ".join(f"{d.name}: {_release_words(p)}" for d, p in zip(discs, props))
        titles = [mc.fold(((p.targets or [{}])[0].get("album") or [""])[0]) for p in props]
        if titles[0] == titles[1]:
            return None
        note = (f"{len(props)} versions of this album were in one folder, a disc each ({lines}): "
                "each goes to a folder of its own, tagged from its own release")
        return self._as_split(g, props, note, errors, own_dirs=True)

    def _as_split(self, g, props, note, errors, own_dirs=False):
        """The versions as albums of one split: the first, the others in its .parts. own_dirs:
        each version's own disc folder (its rip log, cue sheet and scans are that version's)."""
        everyone = [list(map(str, p.files)) for p in props]
        for k, p in enumerate(props):
            p.split = {"of": str(g.folder), "parent": str(g.folder.parent), "index": k, "count": len(props),
                       "others": [f for j, fs in enumerate(everyone) if j != k for f in fs],
                       "own_dirs": [str(p.disc_folder)] if own_dirs and getattr(p, "disc_folder", None) else [],
                       "why": note}
            p.why = [note] + list(p.why or [])
            p.notes = [note] + list(p.notes or [])
            p.provider_errors = dict(errors, **(p.provider_errors or {}))
        first = props[0]
        first.parts = props[1:]
        return first

    def web_partial_on(self) -> bool:
        """providers.web.partial (on by default with the web provider): an album only the
        web knows gets Partial tags from it."""
        return self.profile.provider_on("web", "partial")

    def _forced(self, g: Group, ref: str, discs=None, claimed=None, release=None) -> planmod.Proposal:
        """The release the user chose for this album (Picard's "Other versions", or a pasted
        MusicBrainz or Discogs link): no search; the matcher's score of it is shown, and the
        tags are written as for any match - when its tracks line up with the files.
        DSC-10: discs ({folder: the release's disc}) and claimed (the Proposal of the disc whose
        own match found the release): sibling folders the release claimed by their fit, each
        read as its disc of the release, the whole scored as one album (its margin the claiming disc's). release: ("mb" |
        "discogs", ID) when known already (ref is then only shown)."""
        kind, rid = release or parse_release_ref(ref)
        outside = self._outside(g)
        album = ma.read_album(g.folder, g.folder.parent.name, g.folder.name, exclude={str(x) for x in outside})
        if discs:
            album = _renumbered(album, discs)
        mine = {str(f) for f in g.files}
        files = [t["path"] for t in album["tracks"] if t.get("path") in mine] or [str(f) for f in g.files]
        base = dict(source_dir=str(g.folder), files=files, provider_errors={})
        if kind is None:
            return planmod.Proposal(**base, status="unresolved", reason_codes=["other"],
                                    reason=f"not a MusicBrainz or Discogs release: {ref}")
        try:
            if kind == "mb":
                det = self.http.get_json(f"https://musicbrainz.org/ws/2/release/{rid}"
                                         f"?inc=recordings+artist-credits+labels+url-rels+release-groups&fmt=json", "mb")
                cand = ma.mb_candidate(det) if det else None
            else:
                det = self.http.get_json(f"https://api.discogs.com/releases/{rid}", "discogs")
                cand = ma.discogs_candidate(det, self.dm) if det and self.dm is not None else None
        except ma.ProviderUnavailable as exc:
            return planmod.Proposal(**base, status="unresolved", reason_codes=[exc.kind if exc.kind == "rate_limited"
                                                                               else "other"],
                                    reason=f"the release you chose could not be fetched: {exc}")
        if not cand:
            return planmod.Proposal(**base, status="unresolved", reason_codes=["other"],
                                    reason=f"the release you chose was not found: {ref}")
        sc = mc.score_candidate(album, cand, self.cfg)
        if album.get("folder_reading"):
            # (FLD-2: the release you chose, scored under the folder name's "Artist - Album" reading
            # too - a flat layout's folder above is no artist: the reading that agrees better)
            alt = ma.with_reading(album, album["folder_reading"])
            s2 = mc.score_candidate(alt, cand, self.cfg)
            if float(s2.get("confidence") or 0) > float(sc.get("confidence") or 0):
                album, sc = alt, s2
        if claimed is not None:
            # (DSC-10: a set's title is its folder's, or the title one of its discs is tagged with -
            # "The Stranger" on the disc of the album in "The Stranger Legacy Edition (with BestBuy
            # Bonus Disc)": scored as the title that agrees best)
            for t in sorted({_first(ex.read_meta(f), "album") for f in g.files} - {"", album.get("title")}):
                alt = dict(album, title=t)
                s2 = mc.score_candidate(alt, cand, self.cfg)
                if float(s2.get("confidence") or 0) > float(sc.get("confidence") or 0):
                    album, sc = alt, s2
        if (sc.get("cand") or {}).get("version_media"):
            # (MED-22c: a copy of an SACD's layers, chosen by hand - scored, and tagged, as the
            # layers' own tracks of the release, as the matcher's own pick is: version_view)
            cand = sc["cand"]
        if kind != "mb":
            # BAR-13: a pressing your code names, chosen by hand, listing too few tracks to tag -
            # tagged from the full entry of its master, as when the matcher picks it
            view = self._code_twin_view(album, cand, sc)
            if view:
                sc, cand = view, view["cand"]
        # (DSC-10: a claimed set is scored as one album, as any match: never the claiming disc's score)
        best = dict(cand, confidence=float(sc.get("confidence") or 0) if claimed is not None else 100.0,
                    notes=list(sc.get("notes") or []))
        if (sc.get("cand") or {}).get("whole_works"):
            best["whole_works"] = True               # TRK-5: tagged one track per work, as scored
        else:                                        # (one per work: no file missing, no slots)
            best.update(_slots_of(album, cand, sc))
        if kind == "mb":
            best["mb_id"] = cand["id"]
        else:
            best["discogs_id"] = cand["id"]
            twin = release_twin(ref)
            twin_note = ""
            if twin:
                # (TIE-1: the suggested Discogs pressing confirmed with MusicBrainz's entry of it -
                # tagged from both, as the matcher's own pick would have been - only when that
                # MusicBrainz release is this pressing: it links it, or barcode / catalogue number
                # and country agree; never two pressings paired)
                if self._is_twin(twin[1], cand):
                    best["mb_id"] = twin[1]
                else:
                    twin_note = (f"MusicBrainz release {twin[1]} is not this Discogs pressing (or could not be "
                                 "read): tagged from Discogs alone")
        why = [f"the release you chose ({ref}); the matcher scores it {sc.get('confidence', 0):.0f}"] \
            if claimed is None else []
        if kind != "mb" and twin_note:
            why.append(twin_note)
        fits = sc.get("tracks_ok") or (best.get("slots") and mc.score_candidate(
            album, mc.slot_view(album, cand) or cand, self.cfg).get("tracks_ok"))
        if best.get("slots") and best.get("missing"):
            why.append(f"tracks {mc.missing_text(best['missing'])} of {best['release_tracks']} are not in the folder")
        if not fits:
            why.append("its track list does not fit the files well: check the preview")
        if claimed is not None:
            best["all_lengths"] = bool(mc.lengths_all_agree(sc))
        out = {"status": "auto", "best": best, "why": why,
               "margin": getattr(claimed, "margin", None) if claimed is not None else None}
        try:
            prop = self._identified(out, album, files, base)
        except ma.ProviderUnavailable as exc:
            return planmod.Proposal(**base, status="unresolved", reason_codes=["other"],
                                    reason=f"the release you chose could not be fetched: {exc}")
        except Exception as exc:
            return planmod.Proposal(**base, status="unresolved", reason_codes=["incomplete_album"],
                                    reason=f"the release you chose does not fit the files: {exc}")
        prop.why = why + [w for w in prop.why if w not in why]
        return prop

    def _is_twin(self, mbid, dg_cand) -> bool:
        """TIE-1 / M4: MusicBrainz release MBID is the Discogs candidate's own pressing
        (match_core.same_pressing: it links it, or its barcode or catalogue number and country
        agree). False when it cannot be read."""
        try:
            det = self.http.get_json(f"https://musicbrainz.org/ws/2/release/{mbid}"
                                     f"?inc=recordings+artist-credits+labels+url-rels+release-groups&fmt=json", "mb")
        except ma.ProviderUnavailable:
            return False
        mb = ma.mb_candidate(det) if det else None
        return bool(mb) and bool(mc.same_pressing(mb, dg_cand))

    CODE_TWIN_VERSIONS = 20         # (BAR-13, a hand-chosen release: its master's versions read at most)

    def _code_twin_view(self, album, cand, sc):
        """BAR-13 for a release you chose (Engine._forced): the Discogs entry carrying your copy's
        own code whose track list is too sparse to tag, scored with the tracks of a full entry of
        its master as match_core.code_twins scores it when the matcher finds it; None otherwise
        (the chosen release then stands as it is)."""
        n = len(album.get("tracks") or ())
        tracks = cand.get("tracks") or ()
        if sc.get("code") not in mc.OWN_CODES or not cand.get("master_id") or self.dm is None or \
                not (len(tracks) < n or (len(tracks) == n and not sc.get("dur_cov"))):
            return None
        try:
            ids = _discogs_versions(self.http, cand["master_id"]) or set()
            others = []
            for vid in sorted(ids - {str(cand["id"])})[:self.CODE_TWIN_VERSIONS]:
                det = self.http.get_json(f"https://api.discogs.com/releases/{vid}", "discogs")
                if det:
                    others.append(mc.score_candidate(album, ma.discogs_candidate(det, self.dm), self.cfg))
        except ma.ProviderUnavailable:
            return None
        view = next((s for s in mc.code_twins(album, [sc] + others, self.cfg)
                     if str(s["cand"].get("id")) == str(cand["id"])), None)
        return view if view is not None and view["cand"].get("tracks_of") else None

    def _release(self, best):
        return ma.release_fields(best)

    @staticmethod
    def _twin_slots(album, rel, tags_mb, best, n, ps=None):
        """IDM-1: the MusicBrainz entry of a Discogs pick's pressing (its "mb_twin") listing
        more tracks than your files (King Crimson - Islands, 40th Anniversary: the CD and its
        DVD-Audio, 49 tracks, of which your 12 files are the CD): each file tagged as its own
        track of it, as when MusicBrainz's pick leads (match_core.slot_view). Otherwise TAGS_MB
        unchanged: the twin's tags were dropped and the album re-tagged from Discogs alone, so
        planning the same files again after their first job gave other tags."""
        if not tags_mb or len(tags_mb) == n or best.get("source") != "discogs" or best.get("slots") \
                or best.get("version_tracks") or best.get("box_disc"):
            return tags_mb
        cand = ma.mb_candidate(rel)
        if len(cand.get("tracks") or ()) != len(tags_mb):
            return tags_mb
        view = mc.slot_view(album, cand)
        if not view or not view.get("missing") or len(view.get("slots") or ()) != n:
            return tags_mb
        digital = _unheld_digital_media(rel, view["slots"])     # (MED-20, as when MusicBrainz leads)
        if digital:
            again = mb_tags.release_tags(rel, ps, uncounted=digital)
            tags_mb = again if len(again) == len(tags_mb) else tags_mb
        return [tags_mb[k] for k in view["slots"]]

    def _identified(self, out, album, files, base, versions=True):
        """versions: the release's version kept visible (keep_version); never for an album
        whose pressings tie (album only: which version is not known)."""
        best = out["best"]
        rel = det = None
        mbid = best.get("mb_id") or ""
        dgid = best.get("discogs_id") or (best["id"] if best["source"] == "discogs" else "")
        tags_mb = tags_dg = None
        mb_down = None
        if mbid and self.use_mb:
            ps = self.profile.picard_tags()
            try:
                rel = self.http.get_json(f"https://musicbrainz.org/ws/2/release/{mbid}?inc={mb_tags.inc_for(ps)}"
                                         "&fmt=json", "mb")
            except ma.ProviderUnavailable as exc:
                # MusicBrainz busy or down: Discogs's own release, when the match names one and
                # Discogs can be read, tags the album; else it is tried again later
                # HF-4: never from Discogs alone while MusicBrainz is the preferred database
                if not (dgid and self.dm is not None and (self.use_discogs or self.token)) or \
                        self._preferred() == "musicbrainz":
                    raise
                mb_down, rel = exc, None
                self._note_mb_down(exc)
            full_rel = rel
            if rel and not best.get("version_tracks"):
                # MED-22: a hybrid SACD's layers listed as media of their own are one disc: your
                # copy's layer of each, numbered as the discs are (2 hybrid SACDs listed as 4
                # media: discs 1 and 2 of 2). A version view keeps every medium (its folders are
                # the layers: match_core.version_view)
                rel = mb_tags.one_layer(rel, _layer_prefer(album, rel, best.get("box_disc")))
            if rel:
                tags_mb = mb_tags.release_tags(rel, ps)
                tags_mb = self._twin_slots(album, rel, tags_mb, best, len(files), ps)
        if not (mbid and self.use_mb):
            ps = self.profile.picard_tags()
        if dgid and best.get("source") == "mb" and self.dm is not None and (self.use_discogs or self.token):
            # (the Discogs release MusicBrainz links is the same pressing only with the same discs:
            # a 3-CD edition linking its 1-CD bonus EP is no twin - none of the EP's fields)
            dgid = self._discogs_twin(best, rel, dgid)
        es = self.profile.enrichment()
        dparts = None
        cls = None                        # CLS-2: (classical, reasons), once both releases are read
        if dgid and self.dm is not None and (self.use_discogs or self.token):
            try:
                det = self.http.get_json(f"https://api.discogs.com/releases/{dgid}", "discogs")
            except ma.AuthError:
                if mb_down is None:
                    raise
                raise mb_down from None         # (Discogs refused too: tried again later)
            cls = self._classical(album, files, rel, det)
            if det:
                dparts = discogs_parts(det, self.dm, self.http, es, whole_works=bool(best.get("whole_works")),
                                       classical=cls[0])
            if dparts and best.get("tracks_of") and best.get("source") == "discogs" and \
                    (dparts[1] is None or len(dparts[1]) != len(files)):
                # BAR-13: the pressing your code names lists too few of its tracks (Discogs 16262491,
                # 131 of a box's 139, no lengths): each file is tagged as its track of the full entry
                # of the same album the matcher compared (match_core.code_twins), the release's own
                # fields (IDs, label, number, barcode, date, country, notes, its discs' format)
                # staying its own
                full = self.http.get_json(f"https://api.discogs.com/releases/{best['tracks_of']}", "discogs")
                fparts = discogs_parts(det, self.dm, self.http, es, whole_works=bool(best.get("whole_works")),
                                       tracks_det=full, classical=cls[0]) if full else None
                if fparts and fparts[1] is not None and len(fparts[1]) == len(files) and fparts[2] == dparts[2]:
                    dparts = fparts
        if cls is None:
            cls = self._classical(album, files, rel, None)
        if not cls[0] and rel and tags_mb is not None and not ps.get("track_ars") and \
                classical.composer_first_form(rel):
            # CLS-2: MusicBrainz's "Composer; performers" credit, with nothing else to decide by:
            # the recordings' relationships say whether its first artist composed the works (the
            # lookup CLS-3 makes for a classical album: asked once, cached)
            cls = self._classical_probe(album, files, mbid, rel, det, ps)
        if cls[0] and rel and tags_mb is not None:
            # CLS-3: a classical album's MusicBrainz release with its recordings' and works'
            # relationships (whatever the profile's Credits setting) and its works' parents,
            # tagged again as CLS-4 writes it (an album that is not classical asks nothing more)
            full_rel, rel, ps = self._classical_release(mbid, full_rel, ps, album, best)
            # (the verdict on the release with its recordings' relationships: whether the album
            # is classical by its genres alone, for CLS-4's credited composer-performers)
            memo = self._classical_cache()
            key = (_files_key(files), "full:" + str(full_rel.get("id") or ""), str((det or {}).get("id") or ""))
            if key not in memo:
                memo[key] = classical.is_classical(self.profile, full_rel, det, album)
            if memo[key].genre_only:
                ps["classical_genre_only"] = True
            tags_mb = mb_tags.release_tags(rel, ps)
            tags_mb = self._twin_slots(album, rel, tags_mb, best, len(files), ps)
        # MED-19: which Discogs tracks are a vinyl record's in a CD box (never written: a marker)
        dg_vinyl = [bool(t.pop("~box_vinyl", None)) for t in dparts[1]] \
            if dparts and dparts[1] is not None else []
        n = len(files)
        slots, missing = best.get("slots"), _missing_of(best)
        vtracks = best.get("version_tracks")
        box = best.get("box_disc")
        if box and not vtracks and not slots:
            # BOX-2: one disc of a set in a folder of its own (Allan Holdsworth - Against the
            # Clock, "Volume One Guitar" and "Volume Two Synthaxe" apart): that disc's tracks,
            # numbered as the set numbers them (disc 1 of 2), tag its files. The disc number is
            # the matched database's: the other database's tracks only where its discs are the
            # same in number (a DVD listed first on one of them numbers the CDs otherwise)
            box, src = int(box), best.get("source")
            mb_media = len([m for m in (rel or {}).get("media") or []])
            same = bool(rel and dparts and dparts[2] and mb_media == int(dparts[2]))
            if rel and (src == "mb" or same):
                whole = mb_tags.release_tags(rel, ps)
                keys = mb_tags.track_keys(rel)
                at = _box_medium(rel, box, src)
                tags_mb = [whole[i] for i, (p, _k) in enumerate(keys) if int(p) == at] \
                    if len(keys) == len(whole) else None
            elif rel:
                tags_mb = None
            if dparts and dparts[1] is not None:
                dparts = (dparts[0], [t for t in dparts[1] if str(t.get("discnumber")) == str(box)]
                          if (src == "discogs" or same) else None, dparts[2])
        if vtracks and rel:
            # versions of one disc in folders of their own: each file its own track of the
            # medium its version is (an SACD's layer, a DVD's or Blu-ray's mix: match_core.
            # version_view) - a DVD's or Blu-ray's tracks included, as audio files
            want = {int(p) for p, _k in vtracks}
            whole = mb_tags.release_tags(rel, ps, media=want)
            at = {(int(p), int(k)): i for i, (p, k) in enumerate(mb_tags.track_keys(rel, media=want))}
            try:
                tags_mb = [copy.deepcopy(whole[at[(int(p), int(k))]]) for p, k in vtracks]
            except (KeyError, IndexError):
                tags_mb = None
            # (Discogs lists a release's media apart from MusicBrainz: its tracks are not these)
            dparts = (dparts[0], None, dparts[2]) if dparts else None
            labels = album.get("version_labels") or {}
            if tags_mb and len(tags_mb) == n == len(album.get("tracks") or ()):
                for t, tr in zip(tags_mb, album["tracks"]):
                    if labels.get(tr.get("disc") or 1):
                        t["~version"] = [labels[tr.get("disc") or 1]]
        elif slots:
            # tracks missing from the folder: each file takes its own track of the release
            # (its number, the release's totals), the others are left out
            full = best.get("release_tracks") or 0
            if tags_mb and rel and len(tags_mb) == full:
                # MED-20: the release's download at its end, none of whose tracks are yours, is no
                # disc of your copy: not counted
                digital = _unheld_digital_media(rel, slots)
                if digital:
                    again = mb_tags.release_tags(rel, ps, uncounted=digital)
                    tags_mb = again if len(again) == full else tags_mb
            tags_mb = [tags_mb[k] for k in slots] if tags_mb and len(tags_mb) == full else None
            if dparts and dparts[1] is not None:
                # IDM-1: Discogs listing just your files' tracks where they are the release's first
                # ones (King Crimson - Islands: its 12 CD tracks, MusicBrainz's CD + DVD-Audio 49) -
                # its tracks are yours as they are, as when Discogs's pick leads (Engine._twin_slots):
                # never one merge after the first job and another after the next
                # (and every title agrees: Discogs leaving out one track of the middle is no fit)
                # (tags_mb: already your files' own tracks, in their order)
                lead = list(slots) == list(range(n)) and len(dparts[1]) == n and bool(tags_mb) and \
                    len(tags_mb) == n and all(mc.title_sim(_first_text(mt.get("title")), str(dt.get("title") or "")) >= 0.6
                                              for mt, dt in zip(tags_mb, dparts[1]))
                dparts = (dparts[0], [dparts[1][k] for k in slots] if len(dparts[1]) == full else
                          (dparts[1] if lead else None), dparts[2])
        cd_copy = ma.copy_source(album) in ("cd", "digital") or \
            (ma.copy_source(album) == "" and not (album.get("audio") or {}).get("hires"))
        # MED-21: the other way round, the record ripped and not the CD (M83 - Saturdays = Youth,
        # Discogs 1572883: 2 LPs and the CD of the album, your files a rip of the LPs)
        vinyl_copy = ma.copy_source(album) == "vinyl"
        if not slots and not vtracks and not box and (cd_copy or vinyl_copy):
            # MED-19: a CD box with a vinyl record of its own (Biffy Clyro - Only Revolutions, the
            # Limited Collectors Edition: CD, sampler CD, 12" LP, DVD; Kreator - Dying Alive: 3 CDs
            # and a 7"): your CDs were ripped, the record was not. The matcher compared the CDs
            # alone (match_core.score_candidate); the tags are theirs too. The record still counts
            # among the discs (an audio disc, MED-17)
            if tags_mb and rel and len(tags_mb) != n and cd_copy:
                cut = _without_box_vinyl(rel, ps)
                if cut is not None and len(cut) == n:
                    tags_mb = cut
            if tags_mb and rel and len(tags_mb) != n and cd_copy and not (album.get("audio") or {}).get("hires"):
                # MED-23: the box's DVD-Audio / Blu-ray Audio and its downloads after the last disc
                # are not in your CD rip (the matcher compared the CDs alone): each CD keeps its
                # medium position, the DVD-Audio still counted among the discs (an audio disc,
                # MED-17), the downloads not (MED-20)
                cut = _without_box_extra(rel, ps)
                if cut is not None and len(cut) == n:
                    tags_mb = cut
            if dparts and dparts[1] is not None and len(dparts[1]) != n and any(dg_vinyl) \
                    and len(dg_vinyl) == len(dparts[1]):
                kept = [t for t, v in zip(dparts[1], dg_vinyl) if v == vinyl_copy]
                if len(kept) == n:
                    # (each CD numbered on its own, the record's sides no longer before it; a
                    # record's tracks likewise, the CD no longer after them)
                    count = {}
                    for t in kept:
                        count[t.get("discnumber")] = count.get(t.get("discnumber"), 0) + 1
                    seen = {}
                    for t in kept:
                        d = t.get("discnumber")
                        seen[d] = seen.get(d, 0) + 1
                        t["tracknumber"], t["totaltracks"] = str(seen[d]), str(count[d])
                    dparts = (dparts[0], kept, dparts[2])
        if (tags_mb and not slots and len(tags_mb) != n and rel and _has_htoa(album)
                and any(m.get("pregap") for m in rel.get("media") or ())):
            # a rip with the hidden track before track 1 as a file numbered 0: the release's
            # pregap track is that file's (match_core.with_pregap matched it)
            with_gap = mb_tags.release_tags(rel, ps, pregap=True)
            tags_mb = with_gap if len(with_gap) == n else tags_mb
        tags_mb = tags_mb if tags_mb and len(tags_mb) == n else None
        if tags_mb and rel and mbid and self.use_mb:
            # MED-22c: a copy of a disc's SACD layers (beside its CD layer's copy) numbered by the
            # physical disc and filed in a folder of that layer's own
            tags_mb = _layer_copy(full_rel, tags_mb, vtracks)
            tags_mb = _area_copy(full_rel, tags_mb, vtracks, album)
        if dparts and (dparts[1] is None or len(dparts[1]) != n):
            dparts = None if not tags_mb else (dparts[0], None, dparts[2])
        if tags_mb and dparts and es["merge_mode"] == "mb_only":
            dparts = None
        layout_note = ""
        if tags_mb and dparts and dparts[1] is not None and not vtracks:
            # TRK-4: the two databases split the same tracks into discs otherwise
            tags_mb, dg_tracks, layout_note = settle_disc_layout(tags_mb, dparts[1], album.get("tracks") or [])
            dparts = (dparts[0], dg_tracks, dparts[2])
        if not tags_mb and dparts and dparts[1] is not None:
            tags_dg = discogs_only_tags(*dparts[:2])
        if not tags_mb and not tags_dg:
            if mb_down is not None:
                raise mb_down                     # (Discogs could not tag it either: tried again later)
            raise RuntimeError("the matched release's tracks could not be read")
        order = ["musicbrainz"] + (["discogs"] if dparts else []) if tags_mb else ["discogs"]
        targets, prov = [], {}
        for i in range(n):
            dg_i = None
            if tags_mb:
                merged = {k: v for k, v in tags_mb[i].items() if not k.startswith("~") and v}
                where = dict.fromkeys(merged, "musicbrainz")
                if dparts:
                    album_f, tracks_f, dcount = dparts
                    track_i = tracks_f[i] if tracks_f else None
                    album_i = with_track_credits(album_f, track_i)     # CRD-1: its own credits
                    dg_i = _as_tags({**album_i, **(track_i or {})})
                    merged = enrich(merged, album_f, track_i, es, dcount, self.dm)   # (it adds them)
                    if cls[0] and any(tags_mb[i].get(k) for k in classical.CE_FIELDS):
                        # CLS-4: Discogs's work (an index entry's title) only where MusicBrainz's
                        # works give none: MusicBrainz's work fields as a set, never mixed with
                        # Discogs's - unless your field sources name Discogs for the field
                        for k in classical.CE_FIELDS:
                            if "discogs" in (self.profile["field_sources"] or {}).get(k, ()):
                                continue
                            if tags_mb[i].get(k):
                                merged[k] = tags_mb[i][k]
                            else:
                                merged.pop(k, None)
                    where = {k: ("discogs" if dg_i.get(k) == v else "musicbrainz") for k, v in merged.items()}
            else:
                dg_i = tags_dg[i]
                merged = {k: v for k, v in dg_i.items() if not k.startswith("~") and v}
                where = dict.fromkeys(merged, "discogs")
            src = {"musicbrainz": tags_mb[i] if tags_mb else {}, "discogs": dg_i or {}}
            for fld, prefs in self.profile["field_sources"].items():   # a profile's explicit choice
                for name in prefs:
                    v = src.get(name, {}).get(fld)
                    if v:
                        merged[fld], where[fld] = v, name
                        break
            merged, where = sorts_follow_names(merged, where, src)
            mb_media = (tags_mb[i].get("media") or [""])[0] if tags_mb else ""
            if tags_mb and tags_mb[i].get("~layer") and mb_tags._layer_kind(mb_media)[1] not in ("", "cd") \
                    and "media" not in (self.profile["field_sources"] or {}):
                # MED-22: an SACD layer's copy is written its own layer's media ("Hybrid SACD (SACD
                # layer, 2 channels)"), never the release's format the Discogs plugin writes for the
                # disc ("SACD, Hybrid, Multichannel, Album" on the stereo layer: Kodo - One Earth Tour
                # Special)
                merged["media"], where["media"] = tags_mb[i]["media"], "musicbrainz"
            merged = latin_titles(merged, self.profile.picard_tags())
            # HF-5: a partial album's missing tracks are no tag of their own (no TAGMENDER_MISSING):
            # "incomplete" is told in the proposal's notes (the plan's and the job report's) only
            if not self.profile["mark_resolved"]:
                merged.pop("tagmender", None)
            hidden = {k: v for k, v in (tags_mb[i] if tags_mb else {}).items() if k.startswith("~")}
            hidden.update(compilations.discogs_hints(det if dparts else None))   # (Various / Compilation on Discogs)
            if tags_mb and rel and rel.get("artist-credit"):
                # NAM-8: the credit as one text, its joins ("featuring", "&") kept for album_artist
                # when the plugin writes the credit one value per artist
                hidden["~albumartist_credit"] = [mb_tags.credit_info(rel["artist-credit"], ps)["name"]]
            if dparts and det:                     # (the credited Discogs artists: album_artist asks a
                dg_ids = [str(a["id"]) for a in det.get("artists") or []   # band's members by them)
                          if isinstance(a, dict) and a.get("id")]
                if dg_ids:
                    hidden["~discogs_albumartist_id"] = dg_ids
                hidden.update(self._discogs_joint_names(det))
            if cls[0]:
                hidden["~classical"] = ["1"]      # CLS-2: for album_artist, levels and the naming script
            remove = list(hidden.pop("~remove", []))
            if remove:
                merged["~remove"] = list(dict.fromkeys(remove))
            # Picard's hidden variables (%_multiartist%, %_releasecomment% ...): for the tagger
            # scripts and the file naming script only - never written (every "~" field is
            # skipped). The tagger scripts run in the plan, on each file's own tags with these
            # (as Picard's Track.update_file_metadata)
            for k, v in hidden.items():
                if k not in merged and v not in (None, "", []):
                    merged[k] = v
            targets.append({k: (v if isinstance(v, list) else [v]) for k, v in merged.items()})
            prov.update(where)

        shown = ""                 # (None: unsure, the release group could not be read)
        if versions and (self.profile.get("keep_versions", True) or getattr(self, "_splitting", False)):
            shown = keep_version(self.http, targets, album, files, rel if tags_mb else None,
                                 det if dparts else None, **version_choices(self.profile))
        if versions and shown == "":
            # (an earlier run's version, once this match keeps none: the title is plain again)
            for t in targets:
                t["~remove"] = list(dict.fromkeys(list(t.get("~remove") or []) + ["albumversion", "releasecomment"]))
        provider = "musicbrainz" if order[0] == "musicbrainz" else "discogs"
        # the missing tracks are information, not a block
        codes = ["incomplete_album"] if missing else []
        if version_subtitles(targets, files):           # MIX-1
            prov.setdefault("discsubtitle", "tagmender")
        prop = planmod.Proposal(
            **base, targets=targets, status="identified", reason_codes=codes, provider=provider + (
                "+discogs" if len(order) == 2 and order[1] == "discogs" else
                "+musicbrainz" if len(order) == 2 else ""),
            release_id=mbid if provider == "musicbrainz" else dgid, url=best.get("url", ""),
            confidence=float(best.get("confidence") or 0), margin=out.get("margin"),
            why=list(out.get("why") or []), evidence=evidence_of(out, best),
            provenance=prov, release=self._release(best), tier="exact",
            all_lengths=bool(best.get("all_lengths")), lengths_missing=bool(best.get("lengths_missing")),
            twin_lengths=bool(best.get("twin_lengths")),
            most_lengths=bool(best.get("most_lengths")), toc_proven=bool(best.get("toc_proven")),
            own_number=bool(best.get("own_number")), guessed_pick=bool(best.get("guessed_pick")),
            number_proven=bool(best.get("number_proven")))
        if cls[0]:
            prop.why = list(prop.why or []) + [classical.why_line(cls[1])]
        # REP-2: whose credits were read whole and placed track by track - MusicBrainz's with the
        # release's and the tracks' relationships both asked for (Credits "Both"), Discogs's with
        # its tracks lined up with yours (CRD-1) and the release listing its credits; what the
        # profile drops (Discogs performers off, protected tags) is named so a plan leaves it
        prop.credits_read = credits_read(tags_mb, ps, dparts, det if dparts else None, es)
        # DSC-12 / MED-17: the release's video discs (MusicBrainz's medium positions) - never a
        # disc "missing" from an audio rip (Gojira - The Flesh Alive: the Blu-ray before the CD)
        whole_rel = locals().get("full_rel") or rel if tags_mb else None
        prop.video_discs = sorted({int(m.get("position") or k) for k, m in enumerate((whole_rel or {}).get("media") or [], 1)
                                   if str(m.get("format") or "").casefold() in mb_tags.VIDEO_FORMATS})
        # what each database was sure of (follow_source: only a sure pick of another release
        # says your files' own ID of it is not this pressing's)
        prop.sure_picks = dict(out.get("sure_picks") or {})
        if layout_note:
            prop.notes = list(getattr(prop, "notes", None) or []) + [layout_note]
            if "one run" in layout_note:
                # TRK-4b: the disc numbers an earlier TagMender job added beside one run ("disc 2"
                # with "track 4 of 6") are taken back - at any score: TagMender corrects its own
                # write (REP-1's history: written by its job, unchanged since); never yours
                back = _own_disc_writes(prop.files)
                if back:
                    prop.restore = {**(getattr(prop, "restore", None) or {}), **back}
        if missing:
            # HF-5: the gaps go to the album's notes (plan, job report: report._gaps reads this
            # wording), never into the files
            prop.notes = list(getattr(prop, "notes", None) or []) + [
                f"tracks {missing} of the release are not in this folder: each file is tagged as its own "
                "track, with the release's totals"]
        if mb_down is not None:
            words = (f"MusicBrainz did not answer ({mb_down.kind.replace('_', ' ')}): tagged from Discogs alone; "
                     "its MusicBrainz IDs are added when the album is processed again")
            prop.why = list(prop.why or []) + [words]
            prop.notes = list(getattr(prop, "notes", None) or []) + [words]
            prop.provider_errors = dict(prop.provider_errors or {}, musicbrainz=mb_down.kind)
        # (the release's track lengths: which files of a folder holding two versions are its own)
        prop.release_secs = release_secs(rel if tags_mb else None, det if dparts else None)
        return prop

    def _classical(self, album, files, rel, det) -> tuple:
        """CLS-2: classical.is_classical of this album, asked once per album and release."""
        memo = self._classical_cache()
        key = (_files_key(files), (rel or {}).get("id") or "", str((det or {}).get("id") or ""))
        if key not in memo:
            memo[key] = classical.is_classical(self.profile, rel, det, album)
        return memo[key]

    def _classical_probe(self, album, files, mbid, rel, det, ps) -> tuple:
        """CLS-2: is_classical again with the release's recordings' and works' relationships."""
        memo = self._classical_cache()
        key = (_files_key(files), "probe:" + str(mbid), str((det or {}).get("id") or ""))
        if key not in memo:
            # (MusicBrainz unreachable: raised - the album is tried again later, never planned
            # as not classical for an answer not had; nothing remembered)
            got = self.http.get_json(f"https://musicbrainz.org/ws/2/release/{mbid}"
                                     f"?inc={mb_tags.inc_for(dict(ps, track_ars=True))}&fmt=json", "mb")
            memo[key] = classical.is_classical(self.profile, got, det, album) \
                if isinstance(got, dict) and got.get("media") else self._classical(album, files, rel, det)
        return memo[key]

    CLASSICAL_MEMO_MAX = 512

    def _classical_cache(self) -> dict:
        """CLS-2's answers this planning run (Engine.plan clears it; bounded for a watcher)."""
        memo = self.__dict__.setdefault("_classical_memo", {})
        if len(memo) > self.CLASSICAL_MEMO_MAX:
            memo.clear()
        return memo

    def _classical_release(self, mbid, full_rel, ps, album, best):
        """CLS-3: (whole release, release as tagged, settings) of a classical album: the release
        looked up with the recordings' and works' relationships when the profile's Credits
        setting left them out (the lookup a profile with them on makes: one cache entry), and
        the parent works of its works (classical.work_lookups); the settings write CLS-4's
        fields (classical=True). MusicBrainz unreachable: raised, the album tried again later."""
        asked_before = bool(ps.get("track_ars"))
        ps = dict(ps, track_ars=True, classical=True)
        rel = full_rel
        if not asked_before:
            got = self.http.get_json(f"https://musicbrainz.org/ws/2/release/{mbid}?inc={mb_tags.inc_for(ps)}"
                                     "&fmt=json", "mb")
            rel = got if isinstance(got, dict) and got.get("media") else full_rel
        works, asked, capped = classical.work_lookups(self.http, rel, self.log)
        self.log.info("classical: %s - %d parent work(s) read in %d lookup(s)", mbid, len(works), asked)
        ps["~works"] = works
        if capped:
            # (more parent works than one album may ask for: no album holds work fields some of
            # its tracks would have from a parent read and others from one not read)
            ps["~works_capped"] = True
        full = rel
        if not best.get("version_tracks"):
            rel = mb_tags.one_layer(rel, _layer_prefer(album, rel, best.get("box_disc")))
        return full, rel, ps

    def _album_only(self, p, info):
        """An identified album whose pressing is unknown: no release IDs, catalogue number or
        barcode; label, country, media and date only when every tied pressing agrees."""
        import levels
        tied = info.get("tied") or []
        years = {t.get("year") for t in tied}
        same_recordings = self._tied_recordings_agree(tied, p.targets)
        targets = []
        for t in p.targets:
            t = mc.album_only_tags(t, tied, same_recordings, other_kind=bool(info.get("other_kind")),
                                   media_held=bool(info.get("media")))               # (MED-2)
            # the year every tied pressing shares, else the album's first release: never the
            # day of one pressing
            # (and not the year of a pressing your scans contradict)
            if info.get("edition"):
                # EDN-2: your copy names another label's edition: neither the pick's label nor
                # its country is yours
                t.pop("label", None)
                t.pop("releasecountry", None)
            if info.get("edition") or info.get("doubt") or info.get("media"):
                # (an "original" date that is only the other pressing's own - a Discogs release
                # without a master - is no date of yours either; MED-2: nor the one pressing's
                # your audio is not)
                first = t.get("originaldate") or t.get("originalyear")
                first = (first[0] if isinstance(first, list) and first else first) or ""
                pick_year = info.get("pick_year") or (
                    next(iter(years)) if info.get("media") and len(years) == 1 else None)
                if pick_year and str(first)[:4] == str(pick_year):
                    for k in ("originaldate", "originalyear"):
                        t.pop(k, None)
            if len(years) == 1 and None not in years and "" not in years and not info.get("scans") \
                    and not info.get("edition") and not info.get("doubt") and not info.get("media"):
                t["date"] = [str(next(iter(years)))]
            else:
                first = t.get("originaldate") or t.get("originalyear")
                if first:
                    t["date"] = list(first) if isinstance(first, list) else [first]
                else:
                    t.pop("date", None)
            # only fields of the album, as a Partial album's (levels.album_field): the Discogs
            # plugin writes an open-ended set of one pressing's fields (discogs_identifier_*,
            # discogs_status, discogs_date_added ...), and the release's status and script are
            # its own. (Picard's hidden variables of the one pressing - its comment, catalogue
            # number ... - are no album-only facts: only those of the album's tracks stay.)
            targets.append({k: v for k, v in t.items()
                            if (k in ALBUM_ONLY_HIDDEN if k.startswith("~") else levels.album_field(k))})
        isrc_note = []
        p.targets = _drop_isrcs(targets, p.source_dir, isrc_note)
        p.release_id, p.url, p.tier = "", "", "album_only"
        p.no_lengths_tie = bool(info.get("no_lengths"))
        kept = {k for t in p.targets for k in t}
        p.provenance = {k: v for k, v in p.provenance.items() if k in kept}
        doubt = (info["doubt"] if info.get("doubt")
                 else "your scans name another pressing" if info.get("scans")
                 else info["edition"] if info.get("edition")
                 # (MED-2: your audio is none of the pressings listed - a hi-res download of an
                 # album listed only on CD and vinyl)
                 else f"{info['media']}, and no pressing listed is of your audio's kind" if info.get("media") and len(tied) == 1
                 else f"{len(tied)} pressings fit equally")
        p.why = list(p.why) + [f"pressing unknown ({doubt}): album tags only; "
                               "release IDs, catalogue number, barcode and every field of one pressing left empty"] \
            + isrc_note
        return p

    def _own_guess_withdrawn(self, p, out):
        """TAG-1: TagMender's own earlier pick lost its standing - the clue that decided it is no
        longer found on your copy (match_core._still_decides) - and what was chosen in its place is
        itself a guess (the country order, the original date): album only, as far as the
        evidence goes, with what TagMender's job wrote of that pressing taken back. None when
        that is not so (a fresh album keeps TIE-1; a pick your copy proves stays exact)."""
        why = str((out or {}).get("own_disproved") or "") or self._withdrawn_before(p.files)
        best = (out or {}).get("best") or {}
        # (a guess: the pressing chosen by the country order or the original date - within one
        # database, guessed_pick; between the two databases' sure picks, combine's TIE-1 notes)
        guess = best.get("guessed_pick") or any(re.search(r"by your (?:preferred )?country order|original pressing",
                                                          str(e)) for e in list(p.evidence or ()) + list(best.get("notes") or ()))
        if not why or p.status != "identified" or planmod.tier_of(p) != "exact" or not guess:
            return None
        view = (out or {}).get("release_ids") or {}
        clue = re.sub(r"\s*\(not on [^)]*\)\s*$", "", why[len("pressing chosen by "):]) \
            if why.startswith("pressing chosen by ") else why
        doubt = (f"TagMender chose this album's pressing earlier by {clue}, which your copy no longer shows, "
                 "and nothing of your copy decides between the pressings now (TAG-1)")
        pick = view.get("pick") or {}
        pick_info = {k: pick.get(k) for k in ("source", "id", "label", "country", "formats", "year", "layout")}
        own = [x for f, ids in (view.get("held") or {}).items() for x in
               ((view.get("cands") or {}).get(f) or {}).values() if ma.norm_release_id(f, x.get("id")) in ids]
        tied = [pick_info] + [{k: x.get(k) for k in pick_info} for x in own]
        p = self._album_only(p, {"tied": tied, "doubt": doubt, "pick_year": pick.get("year")})
        # (the pick is a guess: none of its values is proven, so none keeps a value TagMender wrote
        # of the pressing it withdraws - only the album's original date stays)
        first = {str(v)[:4] for t in p.targets for k in ("originaldate", "originalyear") for v in t.get(k) or ()}
        p.restore = self._withdraw_own(p.files, out, first=first, errors=p.provider_errors)
        p.notes = list(getattr(p, "notes", None) or []) + [
            doubt + ": only the album's own tags are written"
            + ("; what TagMender's earlier job wrote of that pressing is taken back" if p.restore else "")]
        return p

    def _tie_suggested(self, p, out, album=None):
        """TIE-1 (decided 2026-11-17): the matcher's pick among pressings of the album was made by
        a tie-break alone (your country order, the original release date, your preferred release
        types or formats: match_core.tie_guess), every clue of your copy having been tried. The
        album is tagged as album known - what every tied pressing shares, never a release ID,
        catalogue number or barcode - and the pick is proposed (Proposal.suggested): you confirm it
        by choosing that release (Album > Use Suggested Pressing, Change Release, or `tagmender
        plan PATH --release REF`), which tags it in full as your choice. A profile that accepts
        exact releases only leaves the album unresolved, the suggestion named. None when the pick
        rests on your copy's evidence.
        TAG-3 / REP-1 as on any album only (_one_release): what TagMender's own earlier job wrote of
        a pressing your files' release IDs name, other than the pick, is taken back, and a release
        ID your copy's printed number contradicts goes."""
        guess = ((out or {}).get("best") or {}).get("tie_guess")
        if not guess or p.status != "identified" or planmod.tier_of(p) != "exact":
            return None
        suggested = suggestion_of(guess, (out or {}).get("best") or {})
        words = suggested["why"]
        if not self._accepts("album"):
            # (the reason starts with the release, in the form Needs attention reads: never cut off)
            held = planmod.Proposal(source_dir=p.source_dir, files=p.files, provider_errors=p.provider_errors,
                                    status="unresolved", reason_codes=["ambiguous_release"],
                                    reason=f"pressing not established: {reasons.SUGGESTED_WORDS}{suggested['ref']}; "
                                           f"{words}", why=list(p.why) + [words],
                                    candidates=list(getattr(p, "candidates", None) or []))
            held.suggested = suggested
            return held
        # (TAG-3: your files' release IDs naming another pressing than the pick, as _one_release
        # finds them - worked out while the pick is still the exact release)
        view = (out or {}).get("release_ids") or {}
        clash, facts, pick, side = {}, [], {}, {}
        if view.get("pick") and p.targets:
            view = dict(view, copy_tocs={str(d): t for d, t in ((album or {}).get("tocs_read") or {}).items()})
            p.release_view = view
            clash, facts = self._clashing(p, view, (album or {}).get("file_release_ids") or {})
            pick = view["pick"]
            side = self._pick_side(p, view) if clash else {}
        keys = ("source", "id", "label", "country", "formats", "year", "layout")
        tied = [{k: x.get(k) for k in keys} for x in [guess["pick"]] + list(guess.get("others") or [])]
        p = self._album_only(p, {"tied": tied})
        p.suggested = suggested
        p.why = list(p.why) + [words]
        p.notes = list(getattr(p, "notes", None) or []) + [words]
        if clash:
            for x in facts:
                x["shares_number"] = bool(_catnos_agree(pick, x)
                                          or set(pick.get("barcodes") or ()) & set(x.get("barcodes") or ()))
            first = {str(v)[:4] for t in p.targets for k in ("originaldate", "originalyear") for v in t.get(k) or ()}
            back = {} if _provider_failed(p.provider_errors or {}) else \
                self._restore_own(p.files, clash, facts, pick, side, first)
            gone = self._copy_contradicts(p, out, clash, facts, pick, side)
            for f, fields in gone.items():
                back.setdefault(f, {}).update({k: [] for k in fields if k not in back.get(f, {})})
            if back:
                p.restore = back
                p.notes = list(p.notes) + ["what TagMender's earlier job wrote of another pressing your files' "
                                           "release IDs name is taken back (TAG-3)"]
        return p

    @staticmethod
    def _withdrawn_before(files) -> str:
        """REP-1 / TAG-1: the reason an earlier plan of TagMender's withdrew its own pick of this
        album - its TAGMENDER_REASON, written with the album only it made ("TagMender chose this
        album's pressing earlier by ..., which your copy no longer shows") - while the files are
        still marked Partial: a later guess stays album only, as the first plan made it, until
        your copy decides. "" otherwise."""
        import tagio
        for f in files[:1]:
            try:
                t = tagio.read(f)
            except Exception:
                return ""
            if str((t.get("tagmender") or [""])[0]).casefold() != "partial":
                return ""
            m = re.search(r"TagMender chose this album's pressing earlier by (.+?), which your copy no longer shows",
                          " ".join(map(str, t.get("tagmender_reason") or ())))
            return f"pressing chosen by {m.group(1)}" if m else ""
        return ""

    def _coded(self, facts) -> list:
        """facts with each release's country as the Discogs plugin codes it (country_code)."""
        conv = getattr(self.dm, "country_code", None) if self.dm is not None else None
        out = []
        for x in facts:
            x = dict(x)
            if conv and x.get("country") and not x.get("country_code"):
                try:
                    x["country_code"] = conv(x["country"]) or ""
                except Exception:
                    pass
            out.append(x)
        return out

    def _withdraw_own(self, files, out, pick=None, side=None, first=(), title=False, errors=None) -> dict:
        """TAG-1 / REP-1: {file: {field: value before}} taking back what TagMender's own earlier job
        wrote of the pressing your files' release IDs name, when that pick is withdrawn - as
        _restore_own takes it back (only what its job wrote and nobody changed since; your field
        policy's preserved and locked fields stay). pick / side: the release decided now, whose
        own values stay (none when the album is held)."""
        if _provider_failed(errors or {}):
            return {}                    # (a database out: nothing is taken back until both answer)
        view = (out or {}).get("release_ids") or {}
        side = side or {f: set() for f in ma.RELEASE_ID_FIELDS}
        clash, facts = {}, []
        for f, ids in (view.get("held") or {}).items():
            for x in ids:
                if x in side.get(f, ()):
                    continue
                clash.setdefault(f, {})[x] = "withdrawn"
                info = ((view.get("cands") or {}).get(f) or {}).get(x)
                if not info and ma.norm_release_id(f, (view.get("pick") or {}).get("id")) == x:
                    info = view["pick"]
                info = info or self._release_facts(f, x)
                if info:
                    facts.append(info)
        if not clash:
            return {}
        return self._restore_own(files, clash, facts, pick or {}, side, first, title)

    def _one_release(self, p, out, album):
        """TAG-3: one release per file. The files may already hold a release ID (either
        database's) of another pressing than the pick; below replace_min_confidence the plan
        keeps existing values and only fills empty fields, which would leave them naming two
        pressings (Robin Trower - Back It Up: MusicBrainz's 1983 release kept beside the 1991 US
        CD your scans' barcode proves). Which IDs clash: _clashing (positive differences only).
        - your copy proves the pick (_copy_proves): its tags replace the old pressing's from the
          start (release_proven_over: the plan writes it at the replace threshold), and what of
          the old pressing the pick has no value for is removed (_losing_fields);
        - nothing proves it and its score is below the replace threshold: album only (the
          album's own fields; the release tags your files hold stay - but what TagMender's own
          earlier job wrote of a pressing is taken back, REP-1: restore);
        - nothing proves it, its score at the threshold: replaced as ever, the old pressing's
          fields removed with it.
        No answer about a release (lookup failed, its database off, an old result without the
        matcher's view) is no clash: missing information never removes or demotes anything."""
        if p.status != "identified" or planmod.tier_of(p) != "exact" or not p.targets:
            return p
        view = (out or {}).get("release_ids") or {}
        if view:
            # (your copy's own table of contents, each disc's that fits its files to the sector:
            # a release whose disc IDs none of them fit is another pressing - _disc_ids_differ)
            # ({disc: TOC} read from a rip log or cue sheet only: match_albums.tocs_read)
            view = dict(view, copy_tocs={str(d): t for d, t in ((album or {}).get("tocs_read") or {}).items()})
        p.release_view = view                 # (follow_source asks the same: _another_pressing)
        if not view.get("pick"):
            return p
        clash, facts = self._clashing(p, view, (album or {}).get("file_release_ids") or {})
        if not clash:
            return p
        names = " and ".join(planmod.RELEASE_ID_WORDS[f] for f in clash)
        ids = "; ".join(f"{x}: {why}" for v in clash.values() for x, why in v.items())
        pick = view["pick"]
        for x in facts:
            x["shares_number"] = bool(_catnos_agree(pick, x)
                                      or set(pick["barcodes"]) & set(x.get("barcodes") or ()))
        proof = self._copy_proves(p, out, facts)
        if not proof and _own_ids_clash(p.files, clash) and not getattr(p, "guessed_pick", False):
            # (not when the pick is itself a guess - the country order, the original date - as
            # after TagMender's own pick lost its standing, its deciding clue no longer found: then
            # the pick is no more proven than the one before it)
            # TAG-3 + TAG-1: the IDs that clash are TagMender's own earlier pick, and the matcher
            # left that release only because your copy points elsewhere (TAG-1 keeps it on a
            # better score alone): correcting its own pick is no demotion - one run writes the
            # release, and a second finds nothing to do (BS&T 1968 [SACD]: the vinyl IDs a batch
            # wrote, the AP SACD your scans' catalogue number names)
            proof = "the release IDs your files hold are TagMender's own earlier pick, and your copy points to this release (TAG-1)"
        replace = planmod._threshold(self.profile, "replace_min_confidence")
        conf = float(p.confidence or 0)
        if not proof and conf < replace:
            doubt = (f"your files' {names} names another pressing ({ids}), and nothing on your copy proves "
                     f"this one over it (score {conf:.1f} below the replace threshold {replace:g}; TAG-3)")
            if not self._accepts("album"):
                return planmod.Proposal(source_dir=p.source_dir, files=p.files, provider_errors=p.provider_errors,
                                        status="unresolved", reason_codes=["ambiguous_release"],
                                        reason=f"pressing not established: {doubt}", why=list(p.why) + [doubt],
                                        candidates=list(getattr(p, "candidates", None) or []))
            pick_info = {k: pick.get(k) for k in ("source", "id", "label", "country", "formats", "year", "layout")}
            tied = [pick_info] + [{k: x.get(k) for k in pick_info} for x in facts]
            # (the pick's own date is not the album's: an "original" date that is only the pick's -
            # a Discogs release without a master - is left out with it, as EDN-2's)
            p = self._album_only(p, {"tied": tied, "doubt": doubt, "pick_year": pick.get("year")})
            first = {str(v)[:4] for t in p.targets for k in ("originaldate", "originalyear") for v in t.get(k) or ()}
            back = {} if _provider_failed(p.provider_errors or {}) else \
                self._restore_own(p.files, clash, facts, pick, self._pick_side(p, view), first)
            gone = self._copy_contradicts(p, out, clash, facts, pick, self._pick_side(p, view))
            for f, fields in gone.items():
                back.setdefault(f, {}).update({k: [] for k in fields if k not in back.get(f, {})})
            if back:
                p.restore = back
            p.notes = list(getattr(p, "notes", None) or []) + [
                doubt + ": only the album's own tags are written; the release tags your files hold stay"
                + (", but what TagMender's earlier job wrote of a pressing is taken back" if back else "")]
            return p
        gone, kept = self._losing_fields(p, clash, facts)
        if proof:
            p.release_proven_over = sorted(clash)
        words = f"your files' {names} names another pressing ({ids}); " + (f"{proof}, so " if proof else "") + \
            "this release's tags replace that pressing's"
        if gone:
            words += "; removed where this release has none: " + ", ".join(gone)
        if kept:
            words += "; kept by your field policy: " + ", ".join(kept)
        p.notes = list(getattr(p, "notes", None) or []) + [words + " (TAG-3)"]
        return p

    def _clashing(self, p, view, held):
        """TAG-3: ({field: {id: why}}, [facts of each]) - your files' release IDs that name
        another pressing than the pick. On the database whose ID the release writes (the pick's
        own, or its twin there): any other ID. On the other database: only when that release (as
        scored, else looked up by its ID) differs from the pick by evidence (_pressings_differ);
        a release nothing is known of (lookup failed, database off) is no clash."""
        clash, facts = {}, []
        for f in ma.RELEASE_ID_FIELDS:
            for x in sorted({ma.norm_release_id(f, v) for v in held.get(f) or ()} - {""}):
                why, info = self._another_pressing(p, view, f, x)
                if why:
                    clash.setdefault(f, {})[x] = why
                    facts.append(dict(info) if info else {"source": "mb" if f == "musicbrainz_albumid" else "discogs",
                                                          "id": x, "unknown": True})
        return clash, facts

    def _another_pressing(self, p, view, field, rid):
        """TAG-3 (and follow_source): (why, facts) when the release ID rid of field names another
        pressing than p's pick; ("", facts or None) when nothing shows it does. On the database
        whose ID p writes (the pick's own or its twin there): any other ID. On the other: the
        release as scored, else looked up, compared on positive differences (_pressings_differ);
        an ID nothing is known of is not another pressing."""
        if not (view or {}).get("pick"):
            return "", None
        pick = view["pick"]
        written = {f: {ma.norm_release_id(f, v) for t in p.targets for v in t.get(f) or ()} - {""}
                   for f in ma.RELEASE_ID_FIELDS}
        side = self._pick_side(p, view)
        x = ma.norm_release_id(field, rid)
        if not x or x in side[field]:
            return "", None
        info = ((view.get("cands") or {}).get(field) or {}).get(x) or self._release_facts(field, x)
        if info and ma.norm_release_id(field, info.get("id")) in side[field]:
            return "", info                  # (an old ID merged into the pick's, or its twin's)
        own = {"mb": "musicbrainz_albumid"}.get(pick["source"], "discogs_release_id")
        if written[field] and field == own:
            return "another release than the one written", info
        # (the other database: its ID written is one twin of the pick's among those that may list
        # the pressing twice - another of them is the same release; anything else only by evidence)
        discs = (lambda: self._disc_ids_differ(x, view.get("copy_tocs"))) if field == "musicbrainz_albumid" else None
        return ("; ".join(_pressings_differ(pick, info, side, view.get("mb_links") or {}, discs))
                if info else ""), info

    def _disc_ids_differ(self, rid, copy_tocs) -> str:
        """TAG-3: the MusicBrainz release rid lists disc IDs for a medium, and the table of
        contents of your copy's disc of that number - read from a rip log or a cue sheet
        (match_albums.tocs_read: never one rebuilt from the files' lengths, which always fits
        them and may lack the pregaps) - fits none of them, each track within a sector as
        toc_fits_audio compares: your disc is not that release's (Robin Trower - Back It Up:
        MusicBrainz's 1983 entry, its one disc ID 80 to 582 sectors off the copy's). Medium by
        medium: a medium that lists no disc ID, or a disc of yours with no TOC read, says
        nothing. "" too when MusicBrainz did not answer (no evidence)."""
        if not copy_tocs or not self.use_mb:
            return ""
        try:
            rel = self.http.get_json(f"https://musicbrainz.org/ws/2/release/{rid}?inc=discids+media&fmt=json", "mb")
        except Exception as exc:
            self.log.info("TAG-3: disc IDs of %s not read: %s", rid, exc)
            return ""

        def lengths(offsets, leadout):
            offsets = [int(x) for x in offsets]
            return [b - a for a, b in zip(offsets, offsets[1:] + [int(leadout)])]

        # MED-22: a hybrid SACD's CD layer is the disc of that number (its SACD layers, listed as
        # media of their own, number no disc: the CD layers at positions 1 and 3 are discs 1 and 2)
        rel = mb_tags.one_layer(rel, "cd") if rel else rel
        media = [(m, [lengths(d["offsets"], d["sectors"]) for d in m.get("discs") or ()
                      if d.get("offsets") and d.get("sectors")]) for m in (rel or {}).get("media") or ()]
        own = {str(k): lengths(t.split()[3:], t.split()[2]) for k, t in copy_tocs.items()}
        # any disc of yours fitting any of its disc IDs, on whichever medium: the same release
        # (a DVD listed first numbers its CDs 2 and 3; one disc of a set in a folder of its own)
        if any(len(o) == len(t) and all(abs(a - b) <= 1 for a, b in zip(o, t))
               for o in own.values() for _m, theirs in media for t in theirs):
            return ""

        def cd_family(fmt):
            # (CD, Hybrid SACD, Enhanced CD, CD-R; a medium of no format named that carries a disc
            # ID is a CD too - MusicBrainz attaches disc IDs to CD media only: Back It Up's 1983)
            f = str(fmt or "").casefold().strip()
            return not f or (mc.media_kind(fmt) == "cd" and ("sacd" not in f or "hybrid" in f))

        # compared only against the CD medium numbered as your disc, a disc ID of as many tracks
        # as your disc's TOC: anything else leaves the medium your disc is unknown (no evidence)
        # (and only where its CDs are numbered as yours: the n-th CD medium at position n - a DVD
        # listed first shifts them, and its CD 1 may then be your disc 2's number)
        cds = [m for m, _t in sorted(media, key=lambda x: int(x[0].get("position") or 0))
               if cd_family(m.get("format"))]
        nth = {str(m.get("position") or ""): k for k, m in enumerate(cds, 1)}
        compared = any(cd_family(m.get("format")) and str(m.get("position") or "") in own and
                       nth.get(str(m.get("position"))) == int(m.get("position") or 0) and
                       any(len(t) == len(own[str(m["position"])]) for t in theirs)
                       for m, theirs in media)
        return f"none of its disc IDs fits your disc's table of contents (from its rip log or cue sheet)" \
            if compared else ""

    @staticmethod
    def _pick_side(p, view) -> dict:
        """TAG-3: {field: the IDs that name the pick's own release}: the pick's, those written,
        its twins on the other database (mb_twin / dg_twin, and every scored entry of its pressing)."""
        pick = view["pick"]
        side = {f: {ma.norm_release_id(f, v) for t in p.targets for v in t.get(f) or ()}
                | set((view.get("twins") or {}).get(f) or ()) for f in ma.RELEASE_ID_FIELDS}
        side["musicbrainz_albumid"].add(view.get("mb_twin") or "")
        side["discogs_release_id"].add(view.get("dg_twin") or "")
        side[{"mb": "musicbrainz_albumid"}.get(pick["source"], "discogs_release_id")].add(pick["id"])
        return {f: v - {""} for f, v in side.items()}

    def _release_facts(self, field, rid):
        """TAG-3: ma.release_facts of a release your files' ID names that the matcher did not
        score, looked up through the providers' clients (their cache and throttling); None when
        its database is off or did not answer (no evidence)."""
        try:
            if field == "musicbrainz_albumid":
                if not self.use_mb:
                    return None
                det = self.http.get_json(f"https://musicbrainz.org/ws/2/release/{rid}"
                                         "?inc=recordings+artist-credits+labels+url-rels&fmt=json", "mb")
                return ma.release_facts(ma.mb_candidate(det)) if det else None
            if self.dm is None or not (self.use_discogs or self.token) or not str(rid).isdigit():
                return None
            det = self.http.get_json(f"https://api.discogs.com/releases/{rid}", "discogs")
            return ma.release_facts(ma.discogs_candidate(det, self.dm)) if det else None
        except Exception as exc:                    # (an outage, a rejected token: nothing known)
            self.log.info("TAG-3: release %s %s not read: %s", field, rid, exc)
            return None

    def _losing_fields(self, p, clash, facts):
        """TAG-3: the fields of the pressing that lost (removed, gone), and those your field
        policy keeps (kept): its database's fields of one release (release IDs, release track
        IDs, the Discogs plugin's fields of one release), and a barcode, catalogue number, label,
        country, media or date that is that pressing's own - only where the pick writes none
        (the pick's own values replace the rest)."""
        import tagio
        dbs = {"musicbrainz" if f == "musicbrainz_albumid" else "discogs" for f in clash}
        gone_all, kept_all = [], []
        for i, t in enumerate(p.targets):
            try:
                before = tagio.read(p.files[i]) if i < len(p.files) else {}
            except Exception:
                before = {}
            have = [k for k, v in before.items() if v and k not in t]
            out = []
            for db in dbs:      # (other_source_tags: the fields of the database the source setting does not name)
                out += other_source_tags("discogs_only" if db == "musicbrainz" else "musicbrainz_only", have, True)
            first = {str(v)[:4] for k in ("originaldate", "originalyear") for v in
                     list(t.get(k) or []) + list(before.get(k) or [])}
            for k in ("barcode", "catalognumber", "label", "releasecountry", "media", "date"):
                if k == "date" and {str(v)[:4] for v in before.get(k) or ()} & first:
                    continue                 # (the album's original date: no pressing's own)
                if k in have and any(_pressings_value(k, before[k], x) for x in self._coded(facts)
                                     if not x.get("unknown")):
                    out.append(k)
            out = list(dict.fromkeys(out))
            pol = getattr(self.profile, "policy", None)
            kept = [k for k in out if pol and pol(k) in ("preserve", "lock", "fill_missing")]
            gone = [k for k in out if k not in kept]
            if out:
                t["~remove"] = list(dict.fromkeys(list(t.get("~remove") or []) + out))
            gone_all += gone
            kept_all += kept
        return list(dict.fromkeys(gone_all)), list(dict.fromkeys(kept_all))

    def _copy_contradicts(self, p, out, clash, facts, pick, side) -> dict:
        """TAG-3: {file: [fields]} to remove on an album only: the release IDs your files hold
        whose release your copy's own printed evidence contradicts - the catalogue number or
        barcode read on your scans is the pick's (scans proven, nothing against) and that release
        lists another - with that pressing's fields (its database's fields of one release, a
        barcode, catalogue number, label, country, media, date that is its own and not the
        pick's). Never kept beside your copy's evidence, proven pick or not; never the pick's own
        (the Discogs fields a TagMender job wrote of the pick's twin stay)."""
        import tagio
        sc = (out or {}).get("scans") or {}
        found = set(sc.get("found") or ())
        # (the bar _copy_proves sets: printed on your copy, not only read somewhere - a fuzzy
        # read or a typed note deletes nothing)
        if sc.get("state") != "proven" or sc.get("against") or not sc.get("printed") or \
                not found & {"catalogue_number", "barcode"}:
            return {}
        printed = ((out or {}).get("release_ids") or {}).get("on_copy") or {}
        bad = {}
        for x in self._coded(facts):
            if x.get("unknown") or x.get("shares_number"):
                continue                     # (a twin sharing the barcode, the number written otherwise)
            if mc.catnos_agree(printed.get("catnos") or (), x.get("catnos") or ()) or \
                    set(printed.get("barcodes") or ()) & set(x.get("barcodes") or ()) or \
                    _printed_on_copy(x, ((out or {}).get("release_ids") or {}).get("printed")):
                continue                     # (your copy prints its number too: nothing contradicts it)
            num = "catalogue_number" in found and pick.get("catnos") and x.get("catnos") and \
                not _catnos_agree(pick, x)
            bar = "barcode" in found and pick.get("barcodes") and x.get("barcodes") and \
                not set(pick["barcodes"]) & set(x["barcodes"])
            if num or bar:
                f = "musicbrainz_albumid" if x.get("source") == "mb" else "discogs_release_id"
                if x.get("id") in (clash.get(f) or {}):
                    bad.setdefault(f, []).append(x)
        if not bad:
            return {}
        out_ = {}
        for f in p.files:
            try:
                before = tagio.read(f)
            except Exception:
                continue
            gone = []
            for field, xs in bad.items():
                if not before.get(field):
                    continue
                gone.append(field)
                if field == "musicbrainz_albumid":
                    gone += [k for k in ("musicbrainz_trackid",) if before.get(k)]
                else:
                    # (the Discogs fields of that release only: those of another one the file's
                    # discogs_uri names - the pick's twin's - stay)
                    uri = {m for v in before.get("discogs_uri") or () for m in re.findall(r"/release/(\d+)", str(v))}
                    if not uri or uri & {x["id"] for x in xs}:
                        gone += other_source_tags("musicbrainz_only", [k for k, v in before.items() if v], True)
                for k in ("barcode", "catalognumber", "label", "releasecountry", "media", "date"):
                    if before.get(k) and any(_pressings_value(k, before[k], x) for x in xs) and \
                            not _pressings_value(k, before[k], pick):
                        gone.append(k)
            pol = getattr(self.profile, "policy", None)
            gone = [k for k in dict.fromkeys(gone) if not (pol and pol(k) in ("preserve", "lock"))]
            if gone:
                out_[str(f)] = gone
        return out_

    def _restore_own(self, files, clash, facts, pick, side, first=(), title=False) -> dict:
        """TAG-3 / REP-1: {file: {field: value before TagMender}} for the pressing fields
        TagMender's own earlier job wrote of the pressing that clashes, still holding what it left
        - an album found album only takes them back ([] when the field was empty before: removed).
        Only that pressing's: a release ID in the clash (with its release track IDs or Discogs
        fields), or a barcode, catalogue number, label, country, media or ASIN that is its own and
        not the pick's. Never the pick's or a twin's: what TagMender wrote of the release it picks
        again stays (a second plan writes nothing to it)."""
        import tagio
        writes = ma.tagmender_pressing_writes(files, title=title) if title else ma.tagmender_pressing_writes(files)
        known = self._coded([x for x in facts if not x.get("unknown")])
        ids = {f: set(v) for f, v in clash.items()}

        def losing_id(field, vals):
            return any(ma.norm_release_id(field, v) in ids.get(field, ()) and
                       ma.norm_release_id(field, v) not in side.get(field, ()) for v in vals or ())
        out = {}
        for f in files:
            w = writes.get(str(f)) or {}
            if not w:
                continue
            try:
                now = tagio.read(f)
            except Exception:
                continue
            def wrote(field):
                b_, a_ = w.get(field) or ((), ())
                return a_ if tuple(b_) != tuple(a_) else ()     # (kept as it was: not this job's)
            mb_lost = losing_id("musicbrainz_albumid", wrote("musicbrainz_albumid"))
            # (the Discogs fields a job wrote are those of the release its discogs_uri names - the
            # pick's twin's, beside another tagger's Discogs ID it kept, are not the losing one's)
            uri = [m for v in wrote("discogs_uri") for m in re.findall(r"/release/(\d+)", str(v))]
            dg_lost = losing_id("discogs_release_id", wrote("discogs_release_id")) if not uri else \
                losing_id("discogs_release_id", uri)
            back = {}
            for k, (b, a) in w.items():
                if tuple(b) == tuple(a) or not ma._same_values(now.get(k) or [], a):
                    continue
                if k == "musicbrainz_albumid" or k == "musicbrainz_trackid":
                    ok = mb_lost
                elif k.startswith("discogs_"):
                    ok = dg_lost
                elif k in ("tagmender", "tagmender_reason"):
                    # (title: an album held - the mark TagMender's job wrote for the pick it withdraws)
                    ok = bool(title) and (mb_lost or dg_lost)
                elif k == "album":
                    # (title: an album held, its pick withdrawn - the title TagMender's job wrote for
                    # it, "Rust in Peace [1990 Vinyl Rip]", goes back to the files' own)
                    ok = bool(title) and (mb_lost or dg_lost)
                elif k == "date":
                    # (the losing pressing's own date - its year, not the pick's nor the album's
                    # original year (first): taken back as before the job; one empty before is
                    # removed, and the album only's own date rule fills it - plan._album)
                    ok = (any(_pressings_value(k, a, x) for x in known) and not _pressings_value(k, a, pick)
                          and not {str(v)[:4] for v in a} & set(first or ()))
                else:
                    ok = any(_pressings_value(k, a, x) for x in known) and not _pressings_value(k, a, pick)
                if ok:
                    back[k] = list(b)
            if back:
                out[str(f)] = back
        return out

    def _copy_proves(self, p, out, clashing) -> str:
        """TAG-3: what of your copy proves the pick over the release your files' ID names, or
        "". Never with Extra-Careful Matching, never a pick a guess made (country order,
        original date: TIE-1). Your scans printing its barcode, proven with nothing against it,
        prove it whatever the lengths say (BAR-1, BAR-7); so does a matrix read off your copy that
        the pick or its twin lists (R9), and, for a vinyl rip (its lengths never compared), its
        catalogue number printed on your copy when no other scored pressing carries it. A
        catalogue number on the scans, your own number (own_number) or your disc ID (TOC-1, the
        disc ID choosing it) otherwise only as FIL-1 has them, every track length agreeing - and
        a number only when it is not on the other release too (one every pressing carries
        proves none of them)."""
        if (self.profile.get("matching") or {}).get("extra_careful") or getattr(p, "guessed_pick", False):
            return ""
        sc = (out or {}).get("scans") or {}
        clean = bool(sc.get("printed") and sc.get("state") == "proven" and not sc.get("against"))
        found = set(sc.get("found") or ())
        best = (out or {}).get("best") or {}
        view = (out or {}).get("release_ids") or {}
        if clean and "barcode" in found:
            return "your scans show its barcode"
        own_matrix = set(view.get("matrix") or ()) - {k for i in clashing for k in i.get("matrices") or ()}
        # (a release your files name that could not be looked up may list the same matrix: no proof)
        if own_matrix and not any(i.get("unknown") for i in clashing) and sc.get("state") != "differs" and not sc.get("against"):
            # (R9: a matrix of the pick or its twin read WHOLE on your copy's print - its label, its
            # runout; never notes typed by someone - that neither the release your files name nor
            # any other pressing scored lists, with nothing on your scans against the pick, names
            # the pressing itself; lengths say nothing more)
            return "your copy's matrix is this release's"
        if best.get("vinyl_rip") and sc.get("state") == "proven" and not sc.get("against") and \
                "catalogue_number" in found and (sc.get("printed") or best.get("catno_on_copy")) and \
                not view.get("number_rivals") and not any(i.get("unknown") or i.get("shares_number")
                                                          for i in clashing):
            # (a vinyl rip's lengths are never compared, so FIL-1's every length cannot be asked: its
            # catalogue number printed on your copy, on the pick and on no other pressing scored)
            return "your copy's catalogue number is printed on it and on no other pressing"
        if not getattr(p, "all_lengths", False):
            return ""
        # (a release the matcher did not score: whether it carries the number is not known)
        shared = any(i.get("unknown") or i.get("shares_number") for i in clashing)
        if clean and "catalogue_number" in found and not shared:
            return "your scans show its catalogue number and every track length agrees"
        if (getattr(p, "own_number", False) or getattr(p, "number_proven", False)) and not shared:
            return "your copy's own catalogue number or barcode is on it and every track length agrees"
        if getattr(p, "toc_proven", False) or \
                any("pressing chosen by disc ID" in str(e) for e in getattr(p, "evidence", None) or ()):
            return "your disc ID is this release's and every track length agrees"
        return ""

    def _tied_recordings_agree(self, tied, targets):
        """Every tied pressing is a release of the MusicBrainz release group the tags name,
        with the same recording on each track: only then are the pick's recording IDs, ISRCs
        and recording credits the album's (a Discogs pressing says nothing of recordings)."""
        if not tied or any(t.get("source") != "mb" for t in tied):
            return False
        groups = {g for t in targets for g in ([t.get("musicbrainz_releasegroupid")]
                                               if isinstance(t.get("musicbrainz_releasegroupid"), str)
                                               else t.get("musicbrainz_releasegroupid") or [])}
        if len(groups) != 1 or not self.use_mb:
            return False
        group = _mb_group(self.http, next(iter(groups)))
        if not group:
            return False
        by_id = {g["id"]: [x["recording"] for x in g["tracks"]] for g in group["pressings"]}
        recs = [by_id.get(t.get("id")) for t in tied]
        return all(r and all(r) for r in recs) and len({tuple(r) for r in recs}) == 1

    def _single(self, path, base):
        """A standalone track: its recording, from the fingerprint. This proves the recording,
        not which album edition the file came from, so no album fields are written - but for
        the album of a recording on one release group only (_single_group), and Picard's
        standalone recordings name (nat_name) for a recording on no release at all, as Picard
        files such a recording - when the file has no album of its own (_own_album)."""
        fp = ma.fingerprint(str(path), self.http.db)
        if not fp:
            return None
        key = ma.acoustid_key()
        data = self.http.get_json(f"https://api.acoustid.org/v2/lookup?client={ma.q(key)}&format=json"
                                  f"&meta=recordings&duration={fp['duration']}"
                                  f"&fingerprint={ma.q(fp['fingerprint'])}", "acoustid")
        results = sorted((data or {}).get("results") or [], key=lambda r: -r.get("score", 0))
        if not results or results[0].get("score", 0) < 0.9 or not results[0].get("recordings"):
            return None
        top, rec = results[0], results[0]["recordings"][0]
        if len(results) > 1 and results[1].get("score", 0) > top["score"] - 0.05 and \
                (results[1].get("recordings") or [{}])[0].get("id") != rec.get("id"):
            return None
        secs = float(fp.get("duration") or 0)
        if rec.get("duration") and secs and abs(float(rec["duration"]) - secs) > max(10.0, 0.1 * secs):
            # REC-1: the recording is not as long as your file (a disc image, a medley, a file
            # holding more than it): the fingerprint matched its start only
            return None
        if _cue_tracks_of(path) > 1 and not (rec.get("duration") and secs):
            # REC-1: a file its cue sheet splits into tracks is one recording only where the
            # recording is as long as the whole file (Dr. Jeffrey Thompson - Delta Sync Sleep
            # System, Part 1: one 60-min track, 3642.9 s, the file 3640.3 s, a box disc's cue
            # indexing it at 30:20); with no length to compare it is held
            return None
        names = [a.get("name", "") for a in rec.get("artists") or []]
        phrase = "".join(a.get("name", "") + (a.get("joinphrase") or "") for a in rec.get("artists") or [])
        t = {"title": [rec.get("title", "")], "artist": [phrase] if phrase else [], "artists": names,
             "musicbrainz_recordingid": [rec["id"]], "acoustid_id": [top["id"]],
             "musicbrainz_artistid": [a["id"] for a in rec.get("artists") or [] if a.get("id")]}
        why = ["recording only: the album edition is not established"]
        try:
            group = self._single_group(rec["id"])
        except Exception as exc:                          # (the recording alone still stands)
            self.log.warning("release group of recording %s: %s", rec["id"], exc)
            group = None
        standalone = bool(group and group.pop("~standalone", None))
        if standalone and _own_album(path):
            # the file names an album of its own (a self-released EP MusicBrainz lacks ...):
            # kept, never replaced with the standalone recordings name
            why = ["recording only: a standalone recording on MusicBrainz; the file's own album is kept"]
        elif standalone:
            t.update(group)
            why = ["a standalone recording (on no MusicBrainz release): the album is the standalone "
                   f"recordings name of your tagger settings, \"{group['album'][0]}\""]
        elif group:
            t.update(group)
            why = ["the recording is on one release group only: its album, date and genre; "
                   "the edition is not established"]
        return planmod.Proposal(**base, targets=[{k: v for k, v in t.items() if v}], status="identified",
                                provider="acoustid", release_id=rec["id"],
                                url=f"https://musicbrainz.org/recording/{rec['id']}",
                                confidence=round(top["score"] * 100, 1), evidence=["fingerprint"],
                                why=why, tier="recording",
                                release={"title": [rec.get("title", "")], "artist": [phrase]})

    def _single_group(self, rec_id) -> dict:
        """Album, date, genre and release-group ID of a recording found on exactly one
        MusicBrainz release group ({} when it is on several: a compilation, a live album and
        the studio album are all "the album" of a single file otherwise). A recording on no
        release at all: {"album": [Picard's nat_name], "~standalone": ["1"]}."""
        if not self.use_mb:
            return {}
        # (a browse, not a lookup: a lookup lists 25 releases at most, and a hit single's first
        # 25 may all be its album's)
        data = self.http.get_json(f"https://musicbrainz.org/ws/2/release?recording={rec_id}"
                                  "&inc=release-groups&limit=100&fmt=json", "mb") or {}
        rels = data.get("releases")
        count = data.get("release-count")
        if not isinstance(rels, list) or not isinstance(count, int) or count > len(rels):
            return {}                                     # (not all of them read)
        if count == 0 and not rels:
            # a standalone recording (on no release): Picard's standalone recordings album
            import tagger_options
            return {"album": [tagger_options.settings(self.profile)["nat_name"]], "~standalone": ["1"]}
        groups = {(r.get("release-group") or {}).get("id") for r in rels}
        if len(groups) != 1 or None in groups or "" in groups:
            return {}
        rg_id = groups.pop()
        rg = self.http.get_json(f"https://musicbrainz.org/ws/2/release-group/{rg_id}?inc=genres+tags&fmt=json",
                                "mb") or {}
        if not rg.get("title"):
            return {}
        ps = mb_tags._settings(self.profile.picard_tags())
        first = str(rg.get("first-release-date") or "")
        got = {"album": [rg["title"]], "date": [first] if first else [],
               "genre": mb_tags.genres(None, {"release-group": rg}, ps), "musicbrainz_releasegroupid": [rg_id]}
        return {k: v for k, v in got.items() if v}

    # ---- Partial: the album, not its release -------------------------------
    def _partial(self, out, album, files, base):
        """The album is established (see _established) but no release is proven: the
        release-independent fields (PARTIAL_FIELDS in levels.py) from the MusicBrainz release
        group and its recordings and the Discogs master. A field the candidate pressings, or
        MusicBrainz and Discogs, disagree on is left out; nothing that names one pressing is
        written.

        The album's anchor is the first candidate of the matcher's pool (every candidate it
        scored, best score first) that is established against the files - not necessarily the
        matcher's pick, which is chosen for the release. Every other candidate within
        auto_min_margin of the anchor (above it too) must then be shown to be a pressing of the
        same album: in its release group or master, in the master MusicBrainz links that group
        to, or the same album entered twice (same title, credit and tracks). None otherwise.
        A result saved without a pool (an older matcher) is checked as before: the matcher's
        pick is the anchor, and a full list of three alternatives counts against it."""
        best = out["best"]
        n = len(files)
        margin = self.cfg["auto_min_margin"]
        notes = []
        es = self.profile.enrichment()
        recs = {}                                        # (source, id) -> its record, read once

        def no(why):
            self.log.info("no partial tags for %s: %s", base["source_dir"], why)
            return None
        best_key = (best.get("source"), str(best.get("id")))
        pool = _pool(out, best)
        if pool is None:
            hidden = _alternatives_hidden(out, best, margin)
            if hidden:
                # (the matcher listed 3 per provider: the 4th, not seen, may be another album as good)
                return no(f"more {' and '.join(hidden)} candidates fit about as well than are listed")
            tries = [(best_key[0], best_key[1], float(best.get("confidence") or 0))]
            others = [(s, i, float(c)) for s, i, c in _alternatives(out, best, margin, scores=True)]
        else:
            tries, others = pool, pool
        anchor, first_miss = None, {}
        for src, rid, score in tries[:_ANCHOR_TRIES]:
            if not self._can_read(src):
                continue
            got = self._anchor(src, rid, score, album, recs)
            if got.get("miss"):
                first_miss.setdefault((src, rid), got["miss"])
                continue
            anchor = got
            break
        if anchor is None:
            why = first_miss.get(best_key) or next(iter(first_miss.values()), "") or "album not established"
            if pool is None:
                return no(why)
            return no(f"no candidate within the margin is established: {why}")
        mb = dg = None
        mbid = dgid = ""
        if anchor["src"] == "mb":
            mbid = anchor["id"]
            if anchor.get("same"):
                # (matched through another track layout of its group: its own Discogs twin is a
                # pressing of the layout that did not fit)
                dgid = ""
            elif best_key == ("mb", mbid) and best.get("discogs_id"):
                dgid = str(best["discogs_id"])
            else:
                dgid = ma.mb_linked_discogs_id(anchor["rec"])
        else:
            dgid = anchor["id"]
            if best_key == ("discogs", dgid) and best.get("mb_id"):
                mbid = str(best["mb_id"])
        # MusicBrainz first (its tags are the base), then the Discogs master
        if mbid and self.use_mb:
            if anchor["src"] == "mb":
                rel, sc = anchor["rec"], anchor["sc"]
            else:                                         # the Discogs anchor's twin
                rel = self._record("mb", mbid, recs)
                sc, miss = self._established(album, ma.mb_candidate(rel), "MusicBrainz") if rel else (None, "")
                if not rel:
                    notes.append("the MusicBrainz release could not be read")
                elif miss:                                # (the same pressing: not this album on one side)
                    return no("; ".join(notes + [miss]))
            if rel and sc:
                mb = self._partial_mb(rel, sc, album, n, notes, anchor.get("same"))
        mb_only = es["merge_mode"] == "mb_only"
        if dgid and self.dm is not None and (self.use_discogs or self.token) and not (mb and mb_only):
            if anchor["src"] == "discogs":
                det, sc = anchor["rec"], anchor["sc"]
            else:                                         # the MusicBrainz anchor's twin
                det = self._record("discogs", dgid, recs)
                sc, miss = self._established(album, ma.discogs_candidate(det, self.dm), "Discogs") if det \
                    else (None, "")
                if not det:
                    notes.append("the Discogs release could not be read")
                elif miss:
                    return no("; ".join(notes + [miss]))
            if det and sc:
                dg = self._partial_discogs(det, sc, album, n, notes, need_album=not mb)
        # the album's release group and master, and the master MusicBrainz links that group to
        rgs, masters, linked = set(), set(), ""
        rel_mb = (anchor["rec"] if anchor["src"] == "mb" else recs.get(("mb", mbid))) if mbid else None
        rg_id = ((rel_mb or {}).get("release-group") or {}).get("id") or ""
        if rg_id:
            rgs.add(rg_id)
            linked = self._rg_master(rg_id) if self.use_mb else ""
            if linked:
                masters.add(linked)
        det_dg = anchor["rec"] if anchor["src"] == "discogs" else recs.get(("discogs", dgid))
        if det_dg and det_dg.get("master_id"):
            own = str(det_dg["master_id"])
            if linked and linked != own:
                # (MusicBrainz links the group to another master than the twin's own: only the
                # twin's, which Discogs itself names, places the other candidates)
                notes.append(f"the release group links to Discogs master {linked}, the Discogs release is of {own}")
                masters.discard(linked)
                linked = ""
            masters.add(own)
        if linked and not dg and not dgid and mb and self.dm is not None and (self.use_discogs or self.token) \
                and not mb_only:
            # (no Discogs twin: the master the release group links to gives its album facts)
            master = self.http.get_json(f"https://api.discogs.com/masters/{linked}", "discogs")
            if master:
                dg = {"album": self._master_album(linked, master), "targets": [], "score": mb["score"]}
        # every other candidate about as good: a pressing of this album, or the album is refused
        twins = {("mb", str(mbid)), ("discogs", str(dgid)), (anchor["src"], anchor["id"]),
                 (anchor["src"], str(anchor["rec"].get("id")))}
        rivals = [(s, i, c) for s, i, c in others if (s, i) not in twins and anchor["score"] - c < margin]
        group_ids = set((mb or {}).get("group_ids") or ())
        versions = set((dg or {}).get("versions") or ())
        drop, unseen = set(), []
        for src, rid, score in rivals:
            how = self._same_album(src, rid, anchor, rgs, masters, group_ids, versions, recs)
            if how == "unseen":
                unseen.append((src, rid))
            elif how.startswith("another"):
                return no("; ".join(notes + [f"{how} ({rid}, {score}) fits about as well"]))
            elif how == "twice":
                drop.add(src)
        if unseen:
            return no("; ".join(notes + [f"{len(unseen)} other candidate(s) fit about as well and are not "
                                         "shown to be this album's pressings"]))
        if not mb and not (dg and dg["targets"]):
            return no("; ".join(notes) or "album not established")
        targets = mb["targets"] if mb else dg["targets"]
        prov = dict.fromkeys({k for t in targets for k in t}, "musicbrainz" if mb else "discogs")
        if mb and dg:
            for t in targets:
                for k, v in dg["album"].items():
                    if k not in t:
                        t[k], prov[k] = v, "discogs"
            mb_year = {str((t.get("originalyear") or [""])[0]) for t in targets} - {""}
            dg_year = str((dg["album"].get("originalyear") or [""])[0])
            if dg_year and mb_year and mb_year != {dg_year}:
                notes.append(f"MusicBrainz and Discogs disagree on the original year ({', '.join(sorted(mb_year))} "
                             f"vs {dg_year}): left out")
                for t in targets:
                    for k in ("originaldate", "originalyear", "date"):
                        t.pop(k, None)
        if drop:
            # (the album is in MusicBrainz or Discogs twice: neither group's ID is its only one,
            # and the other group's recordings were not compared)
            gone = (["musicbrainz_releasegroupid", "~releasegroup", "musicbrainz_recordingid", "isrc"]
                    if "mb" in drop else []) + (["discogs_master_id"] if "discogs" in drop else [])
            notes.append("the album is listed twice (another release group or master with the same tracks): "
                         + ", ".join(g for g in gone if not g.startswith("~")) + " left out")
            for t in targets:
                for k in gone:
                    t.pop(k, None)
        self._settle_titles(targets, album.get("tracks") or [], mb, dg, others, recs, notes)
        for t in targets:                     # the album's first release, never a pressing's own date
            first = t.get("originaldate") or t.get("originalyear")
            if first:
                t["date"] = list(first)
        targets = [{k: v for k, v in t.items() if v and (_partial_kept(k))} for t in targets]
        if not any(t.get("album") for t in targets):
            return None
        targets = _drop_isrcs(targets, base["source_dir"], notes)
        missing = "; ".join(w for w in out.get("why") or [] if w) or "no release proven"
        words = f"album established, release not proven: {missing}"
        codes = [c for c in reasons.classify(missing) if c != "other"] or ["other"]
        sc = (mb or dg)["score"]
        why = [words] + notes
        return planmod.Proposal(
            **base, targets=targets, status="identified", tier="partial",
            provider="+".join(x for x in ("musicbrainz" if mb else "", "discogs" if dg else "") if x),
            release_id="", url="", confidence=float(sc.get("confidence") or 0), margin=out.get("margin"),
            why=why, evidence=evidence_of(out, best), reason=words, reason_codes=codes,
            provenance={k: v for k, v in prov.items() if not k.startswith("~") and any(k in t for t in targets)},
            release=self._release(best))

    def _settle_titles(self, targets, files, mb, dg, pool, recs, notes):
        """files: the album's tracks as read (album["tracks"], in file order).

        A track title MusicBrainz and Discogs spell differently (BTBAM - Automata I & II:
        Discogs misspells "Condemned To The Gallows" "Condemed") is your file's own where yours
        is either one's title, folded (mc.fold: case, accents, punctuation aside), as your file
        spells it; else MusicBrainz's. With Discogs's titles only (no MusicBrainz release
        established), MusicBrainz's releases of the matcher's pool are asked, only where a
        file's title and Discogs's are one title spelt differently (mc.track_titles_agree): one
        that has your file's title (folded) shows Discogs's spelling wrong, and yours is kept.
        Such a release is only a candidate, so its own spelling is never written."""
        if mb:
            mbt = {i: t["title"][0] for i, t in enumerate(targets) if t.get("title")}
            dgt = (dg or {}).get("titles") or {}
        elif dg:
            dgt = {i: t["title"][0] for i, t in enumerate(targets) if t.get("title")}
            mbt = self._pool_mb_titles(files, dgt, pool, recs)
        else:
            return
        yours = theirs = 0
        for i, t in enumerate(targets):
            m, d = mbt.get(i), dgt.get(i)
            if not (m and d and t.get("title")) or mc.fold(m) == mc.fold(d):
                continue
            own = str(files[i].get("title") or "").strip() if i < len(files) else ""
            if own and mc.fold(own) in (mc.fold(m), mc.fold(d)):
                new = own                                # (one source's title, as your files spell it)
            elif mb:
                new = m
            else:
                continue
            if t["title"] != [new]:
                t["title"] = [new]
                yours, theirs = (yours + 1, theirs) if new == own else (yours, theirs + 1)
        if yours:
            notes.append(f"MusicBrainz and Discogs spell {yours} track title(s) differently: your files' own kept"
                         + ("" if mb else " (as a MusicBrainz release among the candidates has it)"))
        if theirs:
            notes.append(f"MusicBrainz and Discogs spell {theirs} track title(s) differently: MusicBrainz's used")

    def _pool_mb_titles(self, files, dgt, pool, recs) -> dict:
        """{file i: a title on one of the pool's MusicBrainz releases that is your file's own
        title (folded): only the proof that Discogs's spelling is wrong}, where your title and Discogs's are one title spelt differently. The
        releases are read as the matcher read them (its cached lookups), at most six (a
        two-part album's pool can list several releases of the other part first)."""
        odd = [i for i, d in dgt.items() if i < len(files) and files[i].get("title")
               and mc.fold(files[i]["title"]) != mc.fold(d) and mc.track_titles_agree(files[i]["title"], d)]
        if not odd or not self.use_mb:
            return {}
        names = set()
        for _src, rid, _score in [e for e in pool or () if e[0] == "mb"][:6]:
            if all(mc.fold(files[i]["title"]) in {mc.fold(x) for x in names} for i in odd):
                break                                    # (each one found)
            try:
                rel = recs.get(("mb", str(rid))) or self.http.get_json(
                    f"https://musicbrainz.org/ws/2/release/{rid}?inc=recordings+artist-credits+labels+url-rels"
                    "&fmt=json", "mb")
            except Exception:                            # (only a check: your title is then written over as before)
                continue
            names |= {str(tr.get("title") or "") for m in (rel or {}).get("media") or [] for tr in m.get("tracks") or []}
        folded = {mc.fold(x): x for x in names if x}
        return {i: folded[mc.fold(files[i]["title"])] for i in odd if mc.fold(files[i]["title"]) in folded}

    def _can_read(self, src) -> bool:
        if src == "mb":
            return bool(self.use_mb)
        return src == "discogs" and self.dm is not None and bool(self.use_discogs or self.token)

    def _record(self, src, rid, recs):
        """A candidate's MusicBrainz release (with its release group) or Discogs release, read
        once per album (None when it cannot be read)."""
        key = (src, str(rid))
        if key not in recs:
            if src == "mb":
                ps = self.profile.picard_tags()
                recs[key] = self.http.get_json(f"https://musicbrainz.org/ws/2/release/{rid}"
                                               f"?inc={mb_tags.inc_for(ps)}&fmt=json", "mb")
            else:
                recs[key] = self.http.get_json(f"https://api.discogs.com/releases/{rid}", "discogs")
        return recs[key]

    def _anchor(self, src, rid, score, album, recs) -> dict:
        """One pool candidate tried as the album's anchor: {"src", "id", "score", "rec", "sc"},
        or {"miss": why it is not the files' album}. A MusicBrainz release that fails on its
        tracks only (artist and title established) is tried through its release group's other
        track layouts, smallest first: the files may be the 10-track album plus 2 bonus tracks
        that one 12-track pressing orders otherwise."""
        kind = "MusicBrainz" if src == "mb" else "Discogs"
        rec = self._record(src, rid, recs)
        if not rec:
            return {"miss": f"the {kind} release could not be read"}
        cand = ma.mb_candidate(rec) if src == "mb" else ma.discogs_candidate(rec, self.dm)
        sc, miss = self._established(album, cand, kind)
        if not miss:
            return {"src": src, "id": str(rid), "score": score, "rec": rec, "sc": sc}
        if src != "mb" or sc.get("gate") == "album":
            return {"miss": miss}
        rg_id = (rec.get("release-group") or {}).get("id")
        group = _mb_group(self.http, rg_id) if rg_id else None
        if not group or not group["whole"]:
            return {"miss": miss}
        counts = sorted({len(p["tracks"]) for p in group["pressings"]} - {len(cand["tracks"])})
        for k in counts:
            same = [p for p in group["pressings"] if len(p["tracks"]) == k]
            maps = {tuple(mc.track_map(album, {"tracks": [{"title": t["name"], "secs": t["secs"]}
                                                          for t in p["tracks"]]}, self.cfg)) for p in same}
            if len(maps) != 1:
                continue                                 # (its pressings would map the files otherwise)
            ref = self._record("mb", same[0]["id"], recs)
            if not ref:
                continue
            sc2, miss2 = self._established(album, ma.mb_candidate(ref), kind)
            if not miss2:
                sc2["layout_of"] = len(cand["tracks"])
                return {"src": "mb", "id": str(rid), "score": score, "rec": ref, "sc": sc2,
                        "same": {p["id"] for p in same}}
        return {"miss": miss}

    def _rg_master(self, rg_id) -> str:
        """The one Discogs master MusicBrainz links this release group to ("" when none, or
        several)."""
        data = self.http.get_json(f"https://musicbrainz.org/ws/2/release-group/{rg_id}?inc=url-rels&fmt=json",
                                  "mb") or {}
        return ma.mb_group_master(data)               # (one reading of the link with mb_candidate's)

    def _same_album(self, src, rid, anchor, rgs, masters, group_ids, versions, recs) -> str:
        """What another candidate about as good is: "same" (a pressing of the album: its own
        release group or master is the album's, or the one MusicBrainz links it to), "twice"
        (another group or master, but the same album: title, credit and every track the
        anchor's), "unseen" (it could not be read), or "another ... album" (anything else)."""
        kind = "MusicBrainz" if src == "mb" else "Discogs"
        if (src == "mb" and rid in group_ids) or (src == "discogs" and rid in versions):
            return "same"
        if not self._can_read(src):
            return "unseen"
        rec = self._record(src, rid, recs)
        if not rec:
            return "unseen"
        if src == "mb":
            rg = (rec.get("release-group") or {}).get("id") or ""
            if rg and (rg in rgs or (masters and self._rg_master(rg) in masters)):
                return "same"
            cand = ma.mb_candidate(rec)
            title = (rec.get("release-group") or {}).get("title") or cand["title"]
        else:
            mid = str(rec.get("master_id") or "")
            if mid and mid in masters:
                return "same"
            cand = ma.discogs_candidate(rec, self.dm)
            title = cand["title"]
        mine = anchor["sc"]["cand"]
        album_title = (((anchor["rec"].get("release-group") or {}).get("title") if anchor["src"] == "mb" else "")
                       or mine["title"])
        if mc.fold(title) == mc.fold(album_title) and mc.fold(cand["artist"]) == mc.fold(mine["artist"]) \
                and _same_tracks(cand["tracks"], mine["tracks"], self.cfg):
            return "twice"
        return f"another {kind} album fits about as well: {cand['artist']} - {cand['title']}"

    def _established(self, album, cand, kind):
        """Why `cand` is not the files' album ("" when it is). Established means, rescored
        against the files: artist and title at least as similar as an automatic pick needs
        (auto_min_artist / auto_min_title), a credit that leaves out none of the artists your
        tags name, and the tracks: the same count, or at most 3 more or fewer with every track
        of the shorter list in the in-order map (mc.track_map: by titles where both have one,
        and only one way of lining them up), and at least auto_min_duration_frac of the
        (mapped) track lengths agreeing. With other counts, titled files need at least
        auto_min_duration_frac of the mapped titles agreeing, and files that lack more than
        one of the release's tracks need every mapped title agreeing (untitled: refused). Partial only, a
        "titles" match: every mapped title agrees in order, at least 5 tracks, half the
        lengths and the whole running time within 2% (restored or remastered audio; no
        recording IDs are then written). The pressing may still be unknown or contradicted
        (a barcode of another pressing does not matter here). sc["map"] is the
        [(file_i, track_j)] map, sc["mode"] "lengths" or "titles"."""
        sc = mc.score_candidate(album, cand, self.cfg)
        c = self.cfg
        if sc["artist_sim"] < c["auto_min_artist"] or sc["title_sim"] < c["auto_min_title"]:
            return dict(sc, gate="album"), f"the {kind} release's artist or title agree only partly"
        if sc.get("lacks"):               # (tagging from it would drop an artist your tags name)
            return dict(sc, gate="album"), f"the {kind} credit lacks {', '.join(sc['lacks'])}"
        files, ct = album.get("tracks") or [], (sc.get("cand") or cand)["tracks"]
        nf, nc = len(files), len(ct)
        if sc.get("tracks_ok"):
            pairs = [(i, i) for i in range(nf)]
            frac = sc.get("dur_frac")
        else:
            pairs = mc.track_map(album, sc.get("cand") or cand, c) if nf and nc and abs(nf - nc) <= 3 else []
            if not pairs or len(pairs) < min(nf, nc):
                # (a track of the shorter list in no pair, or in more than one way of lining
                # the files up with the tracks)
                return dict(sc, gate="tracks"), f"the {kind} release's track count differs from the files"
            titled = [(i, j) for i, j in pairs if files[i].get("title")]
            agree = sum(1 for i, j in titled if mc.track_titles_agree(files[i]["title"], ct[j].get("title")))
            if abs(nf - nc) > 1 and agree < len(pairs):
                # (files that lack more than one of its tracks, or more than one file not on
                # it: the album only when every file's title says which track it is - lengths
                # alone fit too many albums)
                return dict(sc, gate="tracks"), (f"the {kind} release has {nc - nf} tracks your files lack"
                                                 if nf < nc else
                                                 f"{nf - nc} of your files are not on the {kind} release")
            if nf != nc and titled and agree < c["auto_min_duration_frac"] * len(titled):
                # (of the mapped files that have a title, most must agree with the track's)
                return dict(sc, gate="tracks"), f"too few of the {kind} release's track titles agree with the files'"
            known = [(i, j) for i, j in pairs if ct[j]["secs"] > 0]
            frac = (sum(1 for i, j in known if mc.duration_ok(files[i]["secs"], ct[j]["secs"], c)) / len(known)
                    if known and len(known) / len(pairs) >= c["min_duration_coverage"] else None)
        sc = dict(sc, map=pairs, mode="lengths", gate="")
        if frac is None or frac < c["auto_min_duration_frac"]:
            if _titles_match(files, ct, pairs, c):
                return dict(sc, mode="titles"), ""
            return dict(sc, gate="tracks"), f"too few of the {kind} release's track lengths agree"
        return sc, ""

    def _partial_mb(self, rel, sc, album, n, notes, same_ids=None):
        """The album's tags from an established MusicBrainz release `rel` (sc: its
        _established score, with the files' map): the release group's and the recordings'
        facts, never the release's own. {"targets" (one per file, in file order), "score",
        "group_ids"}, or None."""
        ps = self.profile.picard_tags()
        layer = "cd"
        if not sc["cand"].get("version_tracks"):
            prefer = _layer_prefer(album, rel, sc["cand"].get("box_disc"))
            rel, layer = mb_tags.one_layer(rel, prefer), _uniform_layer(prefer)       # (MED-22)
        rg_id = (rel.get("release-group") or {}).get("id")
        if not rg_id:
            notes.append("the MusicBrainz release has no release group")
            return None
        group = _mb_group(self.http, rg_id, layer)
        pairs = list(sc.get("map") or [(i, i) for i in range(n)])
        n_cand = len(sc["cand"]["tracks"])
        # the release without what is its own: relationships (its mastering, its studio), label,
        # barcode, country, dates, status, script, its own genres; the release group's and
        # recordings' stay
        bare = {k: v for k, v in rel.items() if k not in _MB_PRESSING_KEYS}
        rg = rel.get("release-group") or {}
        same = [p for p in (group or {}).get("pressings") or [] if len(p["tracks"]) == n_cand
                and (not same_ids or p["id"] in same_ids)]
        whole = bool(group and group["whole"] and same)
        # the album's title is the release group's ("X", never one pressing's "X (Deluxe Edition)")
        if rg.get("title"):
            bare["title"] = rg["title"]
        elif not (whole and len({p["title"] for p in same}) == 1):
            notes.append("the release group has no title and its pressings' titles differ: no album")
            return None
        rt = mb_tags.release_tags(bare, ps)
        tracks = _mb_audio_tracks(rel)
        if len(rt) != n_cand or len(tracks) != n_cand or any(j >= n_cand for _i, j in pairs):
            notes.append("the MusicBrainz release's tracks do not line up with the files")
            return None
        # the album artist: the release group's credit, or one every pressing shares
        credit = mc.fold(ma.mb_credit(rel))
        if not ((rg.get("artist-credit") and mc.fold(ma.mb_credit(rg)) == credit)
                or (whole and {p["credit"] for p in same} == {credit})):
            notes.append("the pressings' album artist credits are not shown to agree: album artist left out")
            for t in rt:
                for k in _ALBUM_ARTIST_KEYS:
                    t.pop(k, None)
        if not whole:
            notes.append("the release group's pressings could not all be read: no recording IDs")
        # track and disc numbers only when they are the album's, not one pressing's: how a
        # pressing splits its tracks into discs is its own (2 LPs, 1 CD), and so is a DVD beside
        # the CD (release_tags counts every medium in totaldiscs and numbers them all)
        files_discs = len({t.get("disc") or 1 for t in album.get("tracks") or []})
        audio = len(mb_tags.audio_media(rel))
        if len(rel.get("media") or []) != audio:
            drop = "the release has a video or other non-audio disc, which is its own"
        elif whole and len({p["layout"] for p in same}) > 1:
            drop = "the pressings number their discs differently"
        elif whole and files_discs > 1 and files_discs != audio:
            drop = f"your files are split into {files_discs} discs, the pressings into {audio}"
        elif not whole and (audio != 1 or files_discs > 1):
            drop = "the disc split of the other pressings is not known"
        elif n != n_cand and (audio != 1 or files_discs > 1):
            drop = "your files and the album's releases have other tracks, on more than one disc"
        else:
            drop = ""
        if drop:
            notes.append(f"{drop}: track and disc numbers left out")
            for t in rt:
                for k in ("tracknumber", "totaltracks", "discnumber", "totaldiscs"):
                    t.pop(k, None)
        titles = sc.get("mode") == "titles"
        for j, t in enumerate(rt):
            rec = tracks[j].get("recording") or {}
            agree = lambda key: whole and len({p["tracks"][j][key] for p in same}) == 1   # noqa: E731
            if not agree("recording"):
                # (another pressing has another recording here: its credits, works and ISRCs may not
                # be this one's)
                for k in mb_tags.recording_tags(rec, ps):
                    if k not in _TRACK_NAMES:
                        t.pop(k, None)
            if whole and not agree("title"):
                t.pop("title", None)
            if whole and not agree("artist"):
                for k in ("artist", "artists", "artistsort", "musicbrainz_artistid"):
                    t.pop(k, None)
            if titles:
                # (matched by titles, not lengths: the audio is not shown to be these recordings)
                for k in list(t):
                    if k in _RECORDING_CLAIMS or k.startswith("performer:"):
                        t.pop(k, None)
        if whole and any(len({p["tracks"][j]["title"] for p in same}) > 1 for j in range(n_cand)):
            notes.append("the pressings disagree on some track titles: those left out")
        if titles:
            notes.append("track lengths differ from every release (restored or remastered audio?): "
                         "matched by titles; no recording IDs")
        # each file its own track: the n-th file is the n-th track only where the map says so
        album_level = {k: v for k, v in rt[0].items() if k in _ALBUM_LEVEL and all(t.get(k) == v for t in rt)}
        to = dict(pairs)
        targets, alone = [], 0
        for i in range(n):
            j = to.get(i)
            if j is None:
                targets.append(dict(album_level))
                alone += 1
                continue
            t = dict(rt[j])
            if i != j:
                t.pop("tracknumber", None)
            if n != n_cand:
                for k in ("totaltracks", "~totalalbumtracks", "~absolutetracknumber"):
                    t.pop(k, None)
            targets.append(t)
        if alone:
            notes.append(f"{alone} of your files are not on the album's releases: album fields only")
        elif n != n_cand:
            notes.append(f"the album's releases have {n_cand - n} track(s) your files lack")
        if sc.get("layout_of"):
            notes.append(f"tracks matched to the release group's {n_cand}-track pressings")
        return {"targets": targets, "score": sc, "group_ids": (group or {}).get("ids") or set()}

    def _master_album(self, mid, master) -> dict:
        """A Discogs master's own album facts: its ID, artists, genres, styles, original year."""
        dm, es = self.dm, self.profile.enrichment()
        master = discogs_without_repeats(master, dm)
        ids = [str(a["id"]) for a in master.get("artists") or [] if isinstance(a, dict) and a.get("id")]
        alb = {"discogs_master_id": str(mid), "discogs_albumartist_id": ids}
        if master.get("genres"):
            alb["genre"] = dm.dedupe(master["genres"])
        if master.get("styles"):
            alb["discogs_style"] = dm.dedupe(master["styles"])
        alb = dm.apply_style_mode(alb, es["style_mode"] or "separate")
        alb.update(dm.extract_original_year(master))
        return _as_tags(alb)

    def _discogs_joint_names(self, entry) -> dict:
        """The names of a Discogs entry's joint credit, unwritten, for album_artist (Discogs gives
        the credit as one text): {} for a single artist."""
        names = [n for a in entry.get("artists") or [] if isinstance(a, dict)
                 for n in [self.dm.clean_artist_name(a.get("anv") or a.get("name"))] if n]
        return {"~discogs_albumartists": names} if len(names) >= 2 else {}

    def _partial_discogs(self, det, sc, album, n, notes, need_album):
        """The album's tags from an established Discogs release `det`: its master's facts and
        the master's titles where the release has the same one in that place. Nothing of the
        release itself (its ID, labels, catalogue numbers, barcode, identifiers ...)."""
        dm = self.dm
        mid = det.get("master_id")
        master = self.http.get_json(f"https://api.discogs.com/masters/{mid}", "discogs") if mid else None
        if not master:
            notes.append("the Discogs release has no master (its only version: no pressing-free facts)")
            return None
        versions = _discogs_versions(self.http, mid)
        det, master = discogs_without_repeats(det, dm), discogs_without_repeats(master, dm)
        ids = [str(a["id"]) for a in master.get("artists") or [] if isinstance(a, dict) and a.get("id")]
        alb = self._master_album(mid, master)
        mine = ma.drop_video_tracks(_dg_tracks(det.get("tracklist")), det)
        pairs = list(sc.get("map") or [(i, i) for i in range(n)])
        # each file's title on the established release (to compare with MusicBrainz's: _settle_titles)
        titles = {i: mine[j]["title"] for i, j in pairs if j < len(mine) and mine[j].get("title")}
        if not need_album:
            return {"album": alb, "targets": [], "score": sc, "versions": versions or set(), "titles": titles}
        credit = dm.format_artist_credit(master.get("artists"))
        # the master's tracklist is its main release's: a title is kept only where the release
        # established against the files has the same one in that place
        flat = ma.drop_video_tracks(_dg_tracks(master.get("tracklist")), master)
        if not credit or not master.get("title") or len(mine) != len(sc["cand"]["tracks"]) \
                or any(j >= len(mine) for _i, j in pairs):
            notes.append("the Discogs master's tracks do not line up with the files")
            return None
        own = any(tr["artists"] for tr in flat)          # (a split album's or a compilation's credits)
        # track and disc numbers: only when the master's one version is this one-disc release
        # (how another version splits its tracks into discs, 2 LPs or 1 CD, is not known)
        one_disc = (ma.discogs_discs(det, dm)[1] or 1) == 1 and versions == {str(det.get("id"))}
        to_mine = dict(pairs)
        to_flat = dict(_lcs(mine, flat, lambda a, b: mc.fold(a["title"]) == mc.fold(b["title"])))
        targets, differ, alone = [], 0, 0
        for i in range(n):
            t = dict(alb, albumartist=[credit], album=[master["title"]], **self._discogs_joint_names(master))
            j = to_mine.get(i)
            k = to_flat.get(j) if j is not None else None
            if k is None:
                if j is None:
                    alone += 1
                else:
                    differ += 1
                targets.append({key: v for key, v in t.items() if v})
                continue
            tr = flat[k]
            t.update(title=[tr["title"]], discogs_artist_id=ids)
            if tr["artists"]:
                t["artist"] = [dm.format_artist_credit(tr["artists"])]
                t["discogs_artist_id"] = [str(x["id"]) for x in tr["artists"] if isinstance(x, dict) and x.get("id")]
            elif not own:
                t["artist"] = [credit]
            if one_disc:
                t.update(discnumber=["1"], totaldiscs=["1"])
                if i == k:
                    t["tracknumber"] = [str(k + 1)]
                if n == len(flat):
                    t["totaltracks"] = [str(n)]
            targets.append({key: v for key, v in t.items() if v})
        # (the release's tracks the master lacks are its bonus tracks: album fields only)
        bonus = min(differ, max(0, len(mine) - len(flat)))
        differ, alone = differ - bonus, alone + bonus
        if differ:
            notes.append(f"the Discogs master and the release disagree on {differ} track title(s): those left out")
        if alone:
            notes.append(f"{alone} of your files are not on the Discogs master: album fields only")
        if not one_disc:
            notes.append("the Discogs master's other versions may split the discs otherwise: "
                         "track and disc numbers left out")
        if sc.get("mode") == "titles":
            notes.append("track lengths differ from every release (restored or remastered audio?): matched by titles")
        return {"album": alb, "targets": targets, "score": sc, "versions": versions or set(), "titles": titles}

    def _web(self, out, album, files, base, by_lengths=False):
        """Partial from the web: an album neither MusicBrainz nor Discogs proves, found on a
        web source (Deezer, iTunes, Bandcamp, Wikipedia: web_tag.album_candidates) and
        established against the files as a database's album would be (_established: artist
        and title, and the tracks by their lengths, or by their titles in order). Written as
        tier Partial with every tag that names no release (web_tag.album_tags_for), its
        source page in TAGMENDER_SOURCE; never a release ID, barcode, catalogue number,
        label, country, media or a pressing's date (a shop's album ID is no release's ID).
        None when no web album is established, or two different ones are."""
        import web_tag
        failed = []
        # GNU-1: a gnudb entry for your disc's exact disc ID, whose every track length agrees
        # with the rip's table of contents, is a web album like a shop's (established below
        # the same way: artist, title, lengths; its track titles must agree with the files')
        disc = gnudb_candidates(out, album)
        # WEB-2: Bandcamp album pages the web search of the album's name linked (match_albums.
        # title_web_step), read as the Bandcamp source reads one, established the same way
        if (out or {}).get("web_bandcamp") and any(n == "Bandcamp" for n, _f in
                                                    web_tag.chosen_sources(self._web_sources())):
            album = dict(album, tag_urls=list(album.get("tag_urls") or []) +
                         [u for u in out["web_bandcamp"] if u not in (album.get("tag_urls") or [])])
        try:
            found = disc + web_tag.album_candidates(self.http, album, self.log, self._web_sources(),
                                                    n_tracks=len(album.get("tracks") or ()), failed=failed)
        except Exception as exc:
            self.log.warning("web lookup failed: %s", exc, extra={"provider": "web"})
            base.setdefault("provider_errors", {})["web"] = "outage"   # (not "no match": nobody answered)
            return None
        if failed:
            # a source that did not answer might have held a rival that makes the others
            # refuse: no web tags from the rest this time. When none of them answered it is an
            # outage, not "no candidate" (the album is tried again later: watcher, Overview);
            # one source down for good must not make every album an outage (0.8.2)
            self.log.info("no web tags for %s: %s did not answer", base["source_dir"], ", ".join(failed))
            if len(set(failed)) >= len(web_tag.chosen_sources(self._web_sources())):
                # (not one web source answered: an outage, not "no match")
                base.setdefault("provider_errors", {})["web"] = "outage"
            return None
        pick, why = self._web_album(album, found)
        if pick and by_lengths and pick[2].get("mode") != "lengths":
            pick, why = None, f"{pick[0]} \"{pick[1].get('title')}\" fits by its titles only, not its lengths"
        if not pick:
            if why:
                self.log.info("no web tags for %s: %s", base["source_dir"], why)
            return None
        name, cand, sc = pick
        cand = dict(cand, genre=web_genres(self.profile, cand))
        pairs = list(sc.get("map") or [])
        # the source's track and disc numbers only when its order is the files' own
        numbers = len(pairs) == len(album["tracks"]) == len(cand["tracks"]) and all(i == j for i, j in pairs)
        tags = web_tag.album_tags_for(album, cand, pairs, self.http, {}, self.token, numbers)
        if sc.get("base_title"):          # (two versions fit: the album's own titles, no version's)
            for t in tags:
                t["album"] = sc["base_title"]
                if t.get("title"):
                    t["title"] = web_album_title(t["title"])
        # (two versions fit alike: no page or cover of one of them either; the notes name both)
        page = "" if sc.get("base_title") else cand.get("url", "")
        targets = []
        for t in tags:
            t = {k: (v if isinstance(v, list) else [v]) for k, v in t.items() if v not in ("", [], None)}
            if page:
                t["tagmender_source"] = [page]
            targets.append(t)
        how = ("track lengths" if sc.get("mode") == "lengths"
               else "track titles, in order (the lengths differ: another master?)")
        words = (f"not on MusicBrainz or Discogs: album tags from {name} (artist, title and {len(pairs)} "
                 f"{how} agree); no release IDs, label, barcode, catalogue number or media")
        notes = [words] + list(why or [])
        if not numbers:
            notes.append(f"{name}'s track order does not fit the files: track and disc numbers left out")
        if not any(t.get("date") for t in targets):
            notes.append(f"{name}'s date is of one version of the album, not its first release: left out")
        prop = planmod.Proposal(**base, targets=targets, status="identified", tier="partial",
                                provider=f"web:{name}", release_id="", url=page,
                                confidence=float(sc.get("confidence") or 0), why=notes,
                                # (FLD-2: the artist and album read from the folder name, when they were)
                                evidence=list(sc.get("notes") or []) + list((out or {}).get("reading_notes") or []),
                                reason=words, reason_codes=["web_fallback_only"],
                                provenance={k: "web" for t in targets for k in t if not k.startswith("~")},
                                release={"album": [targets[0].get("album", [""])[0]] if targets else [],
                                         "albumartist": [cand.get("artist", "")],
                                         "genre": web_tag.genre_list(cand.get("genre")) or [""]})
        prop.web_cover = (cand.get("cover") or "") if page else ""
        prop.release_secs = [(t.get("title") or "", float(t.get("secs") or 0)) for t in cand.get("tracks") or []]
        return prop

    def _web_album(self, album, found):
        """((source name, candidate, _established score), notes) of the web album the files
        are, or (None, why). The best established candidate wins: by lengths before by
        titles, then by score. Another established candidate about as good (within
        auto_min_margin) must be the same album: when only its version differs ("... (Remix
        2024)"), the album's title without the version is written (base_title); when it is
        another album, nothing is (a release type aside: "... - EP" is the album too)."""
        ok = []
        near = ""
        for name, c in found:
            if not c.get("tracks"):
                continue
            sc, miss = self._established(album, c, name)
            if not miss and c.get("source") == "gnudb":          # GNU-1
                miss = _gnudb_titles_miss(album, c, sc)
            if miss:
                near = near or f"{name} \"{c.get('title', '')}\": {miss}"
                continue
            ok.append((name, c, sc))
        if not ok:
            return None, near
        ok.sort(key=lambda x: (x[2].get("mode") != "lengths", -float(x[2].get("confidence") or 0)))
        name, top, sc = ok[0]
        margin = float(self.cfg["auto_min_margin"])
        rivals = [(n, c, s) for n, c, s in ok[1:] if s.get("mode") == sc.get("mode")
                  and float(sc.get("confidence") or 0) - float(s.get("confidence") or 0) < margin
                  and mc.fold(c.get("title")) != mc.fold(top.get("title"))]
        if not rivals:
            return ok[0], []
        base = web_album_title(top.get("title"))

        def same(title):           # (the same album: "Until Eternity Ends - EP" is "Until Eternity Ends")
            return mc.fold(mc.drop_release_type(web_album_title(title))) == mc.fold(mc.drop_release_type(base))
        if not all(same(c.get("title")) for _n, c, _s in rivals):
            other = rivals[0]
            return None, f"{other[0]} \"{other[1].get('title', '')}\" fits about as well as \"{top.get('title')}\""
        titles = ", ".join(f"\"{c.get('title')}\"" + (f" {c['url']}" if c.get("url") else "")
                           for c in [top] + [c for _n, c, _s in rivals])
        return (name, top, dict(sc, base_title=base)), [f"versions of the album fit about as well ({titles}): "
                                                         f"the album's own title \"{base}\" written"]

    def _known_from_own_tags(self, out, files, base):
        """Album known from your own tags, with no database field written: a database lists
        this artist's album but not in a form that can be matched (a promo, a sampler, a
        pressing with other track lengths). Every file must already name its artist and album,
        and the best listing must be of that artist and album. None otherwise."""
        best = (out or {}).get("best") or {}
        why = (out or {}).get("why") or []
        # (only a listing that cannot be compared: anything else in the way stays as it was,
        # a look-up that failed, another tagger's release, versions in one folder)
        if not (self._accepts("album") and len(files) >= 2 and why and all(_NOT_COMPARABLE.match(w) for w in why)
                and best.get("artist_sim", 0) >= mc.DEFAULT_CFG["auto_min_artist"]
                and best.get("title_sim", 0) >= 0.75):
            return None
        for f in files:
            try:
                t = tagio.read(f)
            except Exception:
                return None
            if not (t.get("album") and (t.get("albumartist") or t.get("artist"))):
                return None
        return planmod.Proposal(**base, status="identified", tier="album_only", targets=[{} for _ in files],
                                known_from_tags=True, confidence=float(best.get("confidence") or 0), evidence=evidence_of(out, best),
                                reason="album known from your tags",
                                why=list((out or {}).get("why") or [])
                                + ["album known from your own tags: no release fits, and no database field is written"])

    def _unresolved(self, out, files, base, errors):
        status = (out or {}).get("status") or "nomatch"
        why = list((out or {}).get("why") or [])
        codes = []
        for prov, kind in errors.items():
            if prov in CLUE_HOSTS:
                continue              # a clue's lookup failing is a note, not why the album is unresolved
            if kind in ("rate_limited", "missing_credentials"):
                codes.append(kind)
            elif kind in ("outage", "invalid_response"):
                codes.append(f"{prov}_unavailable")
        if out is None:                                      # nobody was asked: not a "no match"
            words = "no provider could be asked (" + ", ".join(f"{k}: {v}" for k, v in errors.items()) + ")"
            codes = codes or ["other"]
        else:
            words = "; ".join(why) or ("no candidates found" if status == "nomatch" else status)
            if status == "review":
                words = "review: " + words
            # (an outage is no evidence that no source lists the album: never "no candidate" then)
            down = any(c == "rate_limited" or c.endswith("_unavailable") for c in codes)
            codes += [c for c in reasons.classify(words) if (c != "other" or not codes) and
                      not (c == "no_candidate" and down)]
        best = (out or {}).get("best") or {}
        if status in ("ignored", "skipped"):                   # the rules: the planner labels it
            codes = ["other"]
        return planmod.Proposal(**base, status="unresolved", reason=words, reason_codes=list(dict.fromkeys(codes)),
                                tier="ignored" if status in ("ignored", "skipped") else "unresolved",
                                provider=best.get("source", ""), release_id=best.get("id", ""),
                                url=best.get("url", ""), confidence=float(best.get("confidence") or 0),
                                why=why, evidence=evidence_of(out, best),
                                release=self._release(best) if best else {})

    # ---- artwork -------------------------------------------------------------
    def add_artwork(self, prop: planmod.Proposal):
        """Fetch the front cover - and at Enthusiast and Audiophile the artist's photo - into
        staging (the executor writes them). Never raises."""
        self._add_front(prop)
        try:
            self._add_artist_photo(prop)
        except Exception as exc:                          # an artist photo never stops the album
            self.log.warning("artist photo for %s: %s", prop.source_dir, exc)

    def _add_artist_photo(self, prop: planmod.Proposal):
        import artwork
        from plan import EMBEDDABLE
        want = artwork.artist_settings(self.profile)
        if want is None or prop.status != "identified" or not prop.targets:
            return
        t = prop.targets[0]
        who = (t.get("albumartist") or t.get("artist") or [""])[0]
        mbids = "; ".join(t.get("musicbrainz_albumartistid") or [])
        if not who or who.casefold() in ("various artists", "various", "va") or \
                (t.get("compilation") or [""])[0] == "1" or artwork.is_collaboration(mbids, who):
            return                        # (a compilation, or "A & B": no one artist's photo stands for it)
        folder, files, _ours = self._art_view(prop)
        op = self.profile["operation"]
        # looked up only while something lacks it: a track (one TagMender can embed into) without
        # an artist picture, or - where the album stays - an artist folder that can take a photo
        # (a track that has a front cover or gets one now: an artist photo never goes into a track
        # without one - the plan's rule)
        gets_front = bool(getattr(prop, "artwork", None))
        lacking = want["embed"] and any(Path(f).suffix.lower() in EMBEDDABLE and
                                        Path(f).suffix.lower() not in (".m4a", ".mp4") and
                                        not artwork.has_artist_picture(f) and
                                        (gets_front or artwork.has_front(f)) for f in files)
        artist_dir = None
        dest = self.profile.destination_for(files) if hasattr(self.profile, "destination_for") \
            else self.profile["destination"]
        real_src = os.path.realpath(str(prop.source_dir))
        # the album stays where it is judged: in place, a copy profile's copies, or an album a
        # move profile already filed (a library organised into itself)
        stays = op == "in_place" or str(folder) != str(prop.source_dir) or \
            (op == "move" and bool(dest) and real_src.startswith(os.path.realpath(os.path.expanduser(dest)) + os.sep))
        roots = [s_["path"] for s_ in self.profile["sources"]] + ([dest] if dest else [])
        compat = self.profile["filenames"]["compat"]
        title = (t.get("album") or [""])[0]
        names = [who] + [x for x in t.get("albumartistsort") or [] if x and x != who]   # ("Bowie, David/")
        if stays:
            artist_dir = artwork.artist_dir_for(folder, names, roots, compat, title)
        # the artist folder the album is in now: where it stays (its copies' for a copy
        # profile), or where a move takes it from; with no artist.jpg there, the source's own
        src_dir = artwork.artist_dir_for(Path(prop.source_dir), names, roots, compat, title)
        home = artist_dir if artist_dir is not None else src_dir
        if home is not None and src_dir is not None and home != src_dir:
            here, there = artwork.artist_folder_photo(home), artwork.artist_folder_photo(src_dir)
            # (the copies' artist folder has none, or only TagMender's download: the user's own
            # photo in the source's artist folder is the one)
            if there is not None and (here is None or (artwork.written_by_us(here) and not artwork.written_by_us(there))):
                home = src_dir
        single = "single" in [x.lower() for x in t.get("releasetype") or []]
        mine = [Path(f) for f in prop.files] if single else ()
        photo = artwork.artist_folder_photo(home) if home is not None else None
        if photo is None and home is not None and artwork.artist_folder_has_photo(home, mine):
            return                        # (a folder.jpg of Jellyfin's or Kodi's: the artist has an image)
        mbid1 = mbids.split(";")[0].strip()
        if photo is not None and not artwork.written_by_us(photo):
            artwork.remember_own_photo(mbid1, photo)
        # artist.jpg asked for where the album now is, and none there: the copies' artist folder
        # (the source's own photo goes there), or a moved album's (the user's own photo, from
        # the folder it was moved away from - not a download)
        needs_file = bool(want["file"]) and artist_dir is not None and \
            artwork.artist_folder_photo(artist_dir) is None and not artwork.artist_folder_has_photo(artist_dir, mine)
        if photo is None and needs_file:
            photo = artwork.own_photo_for(mbid1)
        if photo is not None:
            # the artist folder's own photo (the user's, or one corrected by hand) is the artist's
            # photo: no download; the tracks' picture follows it (TagMender's own, from an older
            # photo, is replaced), and a moved album takes it along
            try:
                path, w, h = artwork._stage(photo.read_bytes(), ex.STATE_DIR / "staging" / self.job)
                ident = artwork.photo_identity(Path(path).read_bytes())
            except Exception:
                return
            stale = want["embed"] and any(m != ident for f in files for m in artwork.our_artist_marks(f))
            if lacking or stale or not stays or needs_file:
                prop.artist_photo = {"image": str(path), "width": w, "height": h, "artist": who,
                                     "artist_sort": names[1:], "own": not artwork.written_by_us(photo),
                                     "source": f"the artist folder's {photo.name}"}
            return
        if want["file"] and (artist_dir is not None or not stays):
            lacking = True                # (an artist folder that can take a photo; or moved or copied:
            #                               where it lands is the plan's, looked up and kept below)
        if not lacking:
            return
        artist_dir = home
        mbid = mbids.split(";")[0].strip()
        # kept across runs by MusicBrainz artist ID; without one, by the artist's folder (two
        # artists may share a name, never a folder) - or for this run only, by name
        key = mbid or (f"dir:{os.path.realpath(str(artist_dir))}" if artist_dir is not None else who.casefold())
        lasting = bool(mbid) or artist_dir is not None
        cache = self.__dict__.setdefault("_artists", {})
        by_dir = self.__dict__.setdefault("_artist_dirs", {})
        real_dir = os.path.realpath(str(artist_dir)) if artist_dir is not None else None
        if key not in cache and real_dir in by_dir:
            cache[key] = by_dir[real_dir]           # another album of this artist folder, this run
        if key not in cache:
            known, got = artwork.cached_artist_photo(key) if lasting else (False, None)
            if not known and companions.coverup_on():         # (CoverUp off: no photo looked for)
                cu = next((p for p in (self._art or []) if isinstance(p, artwork.CoverUp)), None)
                if cu is None:
                    cu = self.__dict__.get("_artist_cu")
                    if cu is None:
                        cu = artwork.CoverUp(ex.STATE_DIR / "staging" / self.job)
                        self._artist_cu = cu
                ids = {"release_mbid": (t.get("musicbrainz_albumid") or [""])[0], "artist_mbid": mbids,
                       "discogs_release_id": (t.get("discogs_release_id") or [""])[0],
               # (a Partial album: the Discogs master's cover, as the release group's at the CAA)
               "discogs_master_id": (t.get("discogs_master_id") or [""])[0]}
                got = cu.artist_photo(prop.source_dir, ids, who, files=prop.files)
                if got and got.get("note"):
                    prop.why = list(prop.why) + [f"no artist photo: {got['note']}"]
                # "none found" is kept (asked again after 30 days); a failed search is not
                if lasting and ((got and got.get("image")) or (got or {}).get("note") == "no artist photo found"):
                    got = artwork.remember_artist_photo(key, got if got and got.get("image") else None, who)
                if lasting and not (got and got.get("image")):
                    got = artwork.stale_artist_photo(key) or got     # (the search failed: the kept one serves)
            cache[key] = got if got and got.get("image") else None
            if real_dir is not None and cache[key]:
                by_dir.setdefault(real_dir, cache[key])
        if cache[key]:
            prop.artist_photo = dict(cache[key], artist=who, artist_sort=names[1:])

    def _add_front(self, prop: planmod.Proposal):
        if not self.profile["artwork"]["enabled"] or prop.status != "identified" or not prop.targets:
            return
        t = prop.targets[0]
        ids = {"release_mbid": (t.get("musicbrainz_albumid") or [""])[0],
               "rg_mbid": (t.get("musicbrainz_releasegroupid") or [""])[0],
               "artist_mbid": "; ".join(t.get("musicbrainz_albumartistid") or []),
               "discogs_release_id": (t.get("discogs_release_id") or [""])[0],
               # (a Partial album: the Discogs master's cover, as the release group's at the CAA)
               "discogs_master_id": (t.get("discogs_master_id") or [""])[0],
               # (a Partial album from the web: the cover its web album shows)
               "web_cover": getattr(prop, "web_cover", "") or ""}
        if not any(ids.values()):
            return
        try:
            import artwork
            folder, files, ours = self._art_view(prop)
            small = artwork.small_covers(folder, files, self.profile, ours=ours)
            # (the album stays where it is judged: tagged in place, or a copy profile's copies)
            stays = self.profile["operation"] == "in_place" or str(folder) != str(prop.source_dir)
            if not artwork.needs_art(folder, files, self.profile, small=small, stays=stays):
                return                        # every track and the folder have a cover: nothing to look for
            if self._art is None:
                self._art = artwork.chain(self.profile, ex.STATE_DIR / "staging" / self.job, self.http,
                                          self.token, self.log)
                self._note_art_keys()
            chain = self._art
            if ids["web_cover"]:
                chain = list(self._art) + [_web_cover_provider(self._art[-1] if self._art else None,
                                                               ex.STATE_DIR / "staging" / self.job)]
            # the album's own best cover: used as it is when large enough (no lookup), enlarged when
            # nothing larger is found; and never a generated cover over it
            own = artwork.own_cover(small, ex.STATE_DIR / "staging" / self.job) if small["owns"] else None
            got = artwork.find_front(chain, prop.source_dir, ids, (t.get("albumartist") or [""])[0],
                                     (t.get("album") or [""])[0], self.log,
                                     files=getattr(prop, "group_files", None) or prop.files, own=own,
                                     keep=bool(small["owns"]) or artwork.folder_cover(folder) is not None)
            if got and got.get("front") and artwork.has_small(small):
                # what the larger cover replaces (artwork.replace "larger_only"); backed up, undoable
                got["replace"] = {"file": str(small["file"][0]) if small.get("file") else None,
                                  "embedded": sorted(small.get("embedded") or {})}
        except Exception as exc:
            got = {"note": f"artwork: {exc}"}
        if got and got.get("front"):
            prop.artwork = got
        elif got and got.get("note"):
            prop.why = list(prop.why) + [f"no cover: {got['note']}"]

    def _art_view(self, prop):
        """Where the album's covers are judged: (folder, the tracks TagMender writes, every file
        of the album as found). For a copy profile whose copies exist already, the copies (what
        the library has; the source is never changed); else the album itself."""
        own = list(getattr(prop, "group_files", None) or prop.files)
        dest = self.profile.destination_for(prop.files) if hasattr(self.profile, "destination_for") \
            else self.profile["destination"]
        if self.profile["operation"] != "copy" or not dest:
            return prop.source_dir, list(prop.files), own
        copies = []
        for f in prop.files:                  # (files never copied - another format, skipped - do not count)
            c = [x for x in planmod.copies_of(f, under=dest) if Path(x).is_file()]
            if c:
                copies.append(Path(c[0]))
        if not copies:
            return prop.source_dir, list(prop.files), own
        folder = Path(os.path.commonpath([str(c.parent) for c in copies]))
        if is_disc_dir(folder):
            folder = folder.parent
        return folder, copies, copies

    # ---- everything ---------------------------------------------------------
    def apply_level(self, prop: planmod.Proposal):
        """Basic, Enthusiast or Audiophile (levels.py): the Audiophile measurements are added
        (to Partial albums too), then each file's tags are cut to what the profile's level
        writes (a Partial album's to the level's PARTIAL_FIELDS)."""
        import levels
        if levels.items(self.profile) and prop.status == "identified":
            if getattr(self, "_analysis", None) is None:
                self._analysis = levels.Cache(ex.STATE_DIR / "analysis.db")
            try:
                prop.why = list(prop.why or []) + levels.audiophile(prop, self.profile, self.http,
                                                                     self._analysis, self.log)
            except Exception as exc:                          # measurements never block tagging
                self.log.warning("audiophile measurements for %s failed: %s", prop.source_dir, exc)
                prop.why = list(prop.why or []) + [f"audiophile measurements failed: {exc}"]
        # (the release's every field, for the tagger scripts the plan runs: Picard's scripts see
        # them all, whatever the level then writes)
        if prop.status == "identified" and getattr(prop, "suspect_lossy", "") and \
                self.profile.get("quality_action") == "move" and self.profile.get("operation") != "copy":
            # (on a copy profile nothing is moved: "move" holds the album, as "hold" does - plan)
            # (a lossless album that looks converted from lossy: set aside, untagged)
            why = f"looks converted from lossy ({prop.suspect_lossy}); moved aside for you to check"
            prop.status, prop.tier, prop.reason, prop.reason_codes = "unresolved", "unresolved", why, ["other"]
            prop.targets = [{} for _ in prop.files]
            prop.quality_move = True
        if prop.status == "identified":
            files = list(prop.files or [])
            prop.targets = [own_tag_choices(t, self.profile, files[i] if len(files) == len(prop.targets or []) else None)
                            for i, t in enumerate(prop.targets or [])]
            if album_artist.enabled(self.profile):
                # NAM-1 (the lead artist alone as album artist: album_artist.py; a band's members
                # once each). A stand-in engine without the band look-ups (or without http) still
                # takes the lead artist from the credit, with no MusicBrainz band questions.
                prop.targets = album_artist.apply(prop.targets, self.profile,
                                                  getattr(self, "_band_members", None),
                                                  getattr(self, "_discogs_band_members", None),
                                                  getattr(self, "_band_ids", None))
        prop.full_targets = [dict(t) for t in prop.targets or []]
        prop.targets = [levels.restrict(t, self.profile, tier=prop.tier) for t in prop.targets or []]
        if prop.status == "identified" and getattr(prop, "one_release_folders", None) and \
                len(prop.files or []) == len(prop.targets or []):
            # DSC-11: each album folder of the joined album takes the mark of its own copy (its
            # SACD layers' rip "HD Audio", the CD layer's rip none), as planned alone
            files, out = [str(f) for f in prop.files], list(prop.targets)
            for top, mine in zip(prop.one_release_folders, getattr(prop, "one_release_files", None) or ()):
                mine = set(map(str, mine))
                at = [i for i, f in enumerate(files) if f in mine]
                got = marks.apply([out[i] for i in at], [files[i] for i in at], top, self.profile,
                                  [prop.full_targets[i] for i in at])
                for i, t in zip(at, got):
                    out[i] = t
            prop.targets = out
        elif prop.status == "identified":
            prop.targets = marks.apply(prop.targets, prop.files, prop.source_dir, self.profile,
                                       prop.full_targets)

    def _band_members(self, mbid):
        """album_artist.band_members, asked once per artist in this run."""
        memo = self.__dict__.setdefault("_members_memo", {})
        if mbid not in memo:
            # (Sources without MusicBrainz: never asked - SRC-1)
            asks = self.http is not None and getattr(self, "use_mb", True)
            memo[mbid] = album_artist.band_members(self.http, mbid, self.log) if asks else None
        return memo[mbid]

    def _discogs_band_members(self, dgid):
        """album_artist.discogs_members, asked once per artist in this run (only when the
        profile asks Discogs)."""
        memo = self.__dict__.setdefault("_dg_members_memo", {})
        if dgid not in memo:
            asks = self.http is not None and companions.discogs_on() and \
                bool(((self.profile.get("providers") or {}).get("discogs") or {}).get("enabled", True))
            memo[dgid] = album_artist.discogs_members(self.http, dgid, self.log) if asks else album_artist.NOT_ASKED
        return memo[dgid]

    def _band_ids(self, name, mbid):
        """album_artist.choose's find_ids: (MusicBrainz ID, Discogs ID) of the band NAME - its
        MusicBrainz entry (MBID, or the one artist of that name) and the Discogs artist it links
        to - asked once per band in this run."""
        memo = self.__dict__.setdefault("_band_ids_memo", {})
        key = (album_artist.fold(name), mbid or "")
        if key not in memo:
            asks = self.http is not None and getattr(self, "use_mb", True)    # (SRC-1: as above)
            found = mbid or (album_artist.artist_named(self.http, name, self.log) if asks else "")
            memo[key] = (found, album_artist.linked_discogs(self.http, found, self.log) if found and asks else "")
        return memo[key]

    def proposals(self, path, progress=None, cancel=None, only=None):
        """The albums in PATH, each identified; only: the group_key()s of the albums to look up
        (List first's Look Up), the others left out."""
        start = time.monotonic()
        with stagetimes.album() as secs:
            # (PRF-4: a look-up of some albums reads only the folders holding them)
            groups = _albums_of_keys(self, path, only) if only is not None and self._own_scan() else self.albums_in(path)
        took = time.monotonic() - start
        log = self._logger()
        if took >= 0.1:                           # (PRF-1: a scan the look-up waited for; plan_each's later ones are remembered)
            log.info("timing scan %s: %d albums in %.1f s (tags %.1f s)", path, len(groups), took,
                          secs.get("tags", 0.0))
        per_folder = {}
        for g in groups:
            k = os.path.realpath(str(g.folder))
            per_folder[k] = per_folder.get(k, 0) + 1
        if only is not None:
            # (DSC-10: an album of sibling folders is looked up with its siblings, each planned
            # under its own key - a joined set's row looked up again when it no longer joins
            # gives every folder's row back)
            asked = {id(g) for g in groups if group_key(g.folder, g.files) in only}
            # (an album changed since it was listed - a track added, one renamed: its key is no
            # album's now. Its folder's album that no row of this look-up is listed as - when
            # exactly one is left - is looked up in its place, under the key its row was listed
            # with; an album another row is listed as is that row's, never taken. plan_each tells
            # the keys of all its rows: _listed_keys, and the app every row of its list's)
            have = {group_key(g.folder, g.files) for g in groups}
            claimed = set(only) | set(getattr(self, "_listed_keys", None) or ())
            self.kept_rows = {}
            changed = {}
            for k in only:
                if k and k not in have:
                    changed.setdefault(k.split("|", 1)[0], []).append(k)
            listed_as = {}
            for folder, stale in changed.items():
                free = [g for g in groups if os.path.realpath(str(g.folder)) == folder
                        and group_key(g.folder, g.files) not in claimed]
                if len(stale) == 1 and len(free) == 1:
                    asked.add(id(free[0]))
                    listed_as[id(free[0])] = stale[0]
                elif os.path.isdir(folder):
                    # (which album is its row's cannot be told: the row stays as listed, saying
                    # so - never left out of the plan, which takes it off the list)
                    self.kept_rows.update({k: CHANGED_SINCE_LISTED for k in stale})
            asked |= {id(m) for g in groups if id(g) in asked for m in getattr(g, "siblings", None) or ()}
            groups = [g for g in groups if id(g) in asked]
        out, told = [], set()
        self.reached = set()                      # the group_key()s looked up (a run cancelled: not all)
        for i, g in enumerate(groups):
            if cancel and cancel():
                break
            if progress:
                progress(i, len(groups), str(g.folder))
            # (the album as found and listed: every disc or version it is planned as keeps it,
            # so a later Look Up or Refresh of those rows asks for this album again)
            key = group_key(g.folder, g.files)
            if only is not None:
                key = listed_as.get(id(g), key)
            start = time.monotonic()
            try:
                # PRF-1: where the album's time went, one line in the log (stagetimes)
                with stagetimes.album() as secs:
                    try:
                        self._alone = per_folder[os.path.realpath(str(g.folder))] == 1   # (a folder's only album?)
                        for p in self.identify_all(g):           # (each disc of _per_disc as an album)
                            if id(p) in told:
                                continue                         # (DSC-10: a set's one album, found by each of its folders)
                            told.add(id(p))
                            self.add_artwork(p)
                            self.apply_level(p)
                            # (DSC-10: a set's album is its first folder's, whichever folder found it)
                            p.group_key = getattr(p, "set_key", None) or key
                            out.append(p)
                    finally:
                        log.info("%s", stagetimes.line(g.folder, time.monotonic() - start, secs))
            except Exception as exc:                          # one bad album never stops the run
                log.exception("identifying %s failed", g.folder)
                # (nothing of it is written, moved or marked: an Unresolved row saying so)
                p = planmod.Proposal(source_dir=str(g.folder), files=[], status="unresolved",
                                     reason=f"could not be looked up: {_short(exc)} (see log)",
                                     reason_codes=["lookup_failed"])
                p.group_key = key
                p.group_files = [str(f) for f in g.files]
                p.failed_row = True
                out.append(p)
            self.reached.add(key)
        return out

    def listing(self, path, within=None, meta=None, on_album=None) -> dict:
        """The albums PATH holds, not looked up yet (List first): a plan whose albums are
        "listed" - nothing is asked of any database and nothing can be applied. within: only
        the albums inside these folders (several folders dropped at once, listed under the folder
        that holds them) - only the folders they need are read (PRF-3). meta: a dict kept by the
        caller, in which the files' metadata is remembered for the next listing or look up
        (ex.meta_memo; what this listing did not read again, of the folders it read whole, is
        dropped from it). on_album(entry): told each album as soon as its folders are read
        (PRF-3), the same dict, in the same order, as the plan's "albums" list holds."""
        roots = [os.path.realpath(str(w)) for w in within or ()]
        albums, walked = [], []
        if self._own_scan():
            scan = _albums_iter(self, path, (), within if roots else None, walked)
        else:                                            # (a stand-in scan: its albums, all at once)
            def stand_in():                              # (run inside the meta_memo block below)
                yield from self.albums_in(path)
            scan = stand_in()
            walked.append(Path(os.path.abspath(Path(path).expanduser())))
        with ex.meta_memo(meta, walked) if meta is not None else contextlib.nullcontext():
            for g in scan:
                here = os.path.realpath(str(g.folder)) if roots else ""
                if roots and not any(here == r or here.startswith(r.rstrip(os.sep) + os.sep) for r in roots):
                    continue
                files = sorted(str(f) for f in g.files)
                entry = {"source_dir": str(g.folder), "status": "listed", "label": "", "rule": None,
                         "identification": {"status": "listed"}, "release": {}, "files": [],
                         "companions": [], "artwork": None, "conflicts": [], "skipped": [], "notes": [],
                         "album_files": files, "key": group_key(g.folder, g.files)}
                albums.append(entry)
                if on_album is not None:
                    on_album(entry)
        return {"job": self.job, "profile": getattr(self.profile, "name", ""),
                "operation": self.profile["operation"], "listed": True, "albums": albums, "provider_notes": {}}

    def plan(self, path, progress=None, cancel=None, only=None, meta=None) -> dict:
        """meta: a dict kept by the caller (see listing): files whose metadata it remembers, unchanged,
        are not opened again to find the albums (a plan of every album drops what it did not read)."""
        whole = [Path(os.path.abspath(Path(path).expanduser()))] if only is None else False
        self.__dict__.pop("_classical_memo", None)          # (CLS-2: asked anew each planning run)
        with ex.meta_memo(meta, whole) if meta is not None else contextlib.nullcontext():
            props = self.proposals(path, progress, cancel, **({"only": only} if only is not None else {}))
        # (your edits from the app's inspector, kept as you typed them: plan.apply_edits)
        p = planmod.build(props, self.profile, self.job, extra_roots=[Path(os.path.abspath(Path(path).expanduser()))],
                          edits=getattr(self, "edits", None), file_anyway=getattr(self, "file_anyway", None),
                          seed_claims=getattr(self, "seed_claims", None),
                          offer_file_anyway=getattr(self, "offer_file_anyway", False))
        with_keys(p["albums"], props)
        # (not kept in self.notes: those become every later album's provider errors)
        p["provider_notes"] = {**outage_notes(getattr(self, "http", None)), **getattr(self, "run_notes", {}), **self.notes}
        return p

    def plan_each(self, path, keys, emit, progress=None, cancel=None, meta=None) -> int:
        """plan(PATH, only=KEYS) made an album at a time, KEYS in order: emit(key, plan) as soon
        as one album is identified (a folder split per version, or a set planned per disc, comes
        as several rows of one plan), then - the albums known together - emit(key, plan) again
        for each from ONE plan of them all, which is what Apply uses: exactly Engine.plan's (an
        album already in its place keeps it; a newcomer planned first never takes it). The
        folder is scanned once. progress(i, folder): album i (0-based) is being looked up.
        Cancelled: the albums not reached are not emitted; those reached are planned together.
        Returns how many albums were looked up.
        lookup_workers > 1: several albums at once (_plan_each_at_once), the same results."""
        clones = self._thread_clones(min(self.lookup_workers, len(keys)))
        if clones:
            return self._plan_each_at_once(path, keys, emit, clones, progress, cancel, meta)
        root = Path(os.path.abspath(Path(path).expanduser()))
        found = {}
        prev = self.__dict__.get("albums_in")
        # (PRF-4: only the folders holding KEYS are read, once for them all)
        scan = (lambda p, skip=(): _albums_of_keys(self, p, keys, skip)) if self._own_scan() else self.albums_in

        def albums_in(p, skip=()):                       # (the same folder: its albums as found once)
            k = (str(p), tuple(sorted(str(x) for x in skip)))
            if k not in found:
                found[k] = scan(p, skip)
            return list(found[k])
        self.albums_in = albums_in

        def notes():
            return {**outage_notes(getattr(self, "http", None)), **getattr(self, "run_notes", {}), **self.notes}

        def planner():
            return planmod.Planner(self.profile, self.job, extra_roots=[root], edits=getattr(self, "edits", None),
                                   file_anyway=getattr(self, "file_anyway", None),
                                   seed_claims=getattr(self, "seed_claims", None),
                          offer_file_anyway=getattr(self, "offer_file_anyway", False))
        done, kept = [], {}                              # (key, its proposals); rows kept as listed
        # (proposals: a changed album never takes another row's - this look-up's or, from the
        # app, any row of its list: listed_keys)
        self._listed_keys = set(keys) | set(getattr(self, "listed_keys", None) or ())
        try:
            for i, k in enumerate(keys):
                if cancel and cancel():
                    break
                try:
                    with ex.meta_memo(meta, False) if meta is not None else contextlib.nullcontext():
                        props = self.proposals(path, (lambda _i, _n, what, i=i: progress(i, what)) if progress else None,
                                               cancel, only={k})
                except Exception as exc:                 # (one album's internal error: its row says so)
                    self.reached = set() if cancel and cancel() else {k}
                    props = _failed_album(k, exc, self._logger())
                if k not in getattr(self, "reached", ()) and cancel and cancel():
                    break                                # (cancelled before it: its rows stay)
                for q in props:                          # (as plan.build: absolute paths)
                    q.source_dir = os.path.abspath(os.path.expanduser(str(q.source_dir)))
                    q.files = [os.path.abspath(os.path.expanduser(str(f))) for f in q.files]
                out, (props,) = _build_despite_gone(planner, [props], getattr(self, "log", None))   # (shown at once; replaced below)
                done.append((k, props))
                with_keys(out["albums"], props)
                _keep_row(self, k, props, out, kept)
                out["provider_notes"] = notes()
                emit(k, out)
        finally:
            self.__dict__.pop("_listed_keys", None)
            if prev is not None:
                self.albums_in = prev
            else:
                self.__dict__.pop("albums_in", None)     # (the class's own again)
        if len(done) > 1:
            whole, groups = _build_despite_gone(planner, _once_each([ps for _k, ps in done]), getattr(self, "log", None))
            allp = [q for g in groups for q in g]
            with_keys(whole["albums"], allp)
            whole["provider_notes"] = notes()
            for k, ps in done:
                emit(k, dict(whole, albums=[a for a in whole["albums"] if a.get("key") == k] + kept.get(k, [])))
        return len(done)


    def _thread_clones(self, n) -> list:
        """n connections for n workers, or [] to work an album at a time (one album, or a stand-in
        Http that cannot be cloned). Made here: the base connection belongs to this thread."""
        if n < 2:
            return []
        make = getattr(self.http, "thread_clone", None)
        out = []
        for _i in range(n):
            c = make() if make else None
            if c is None:
                for x in out:
                    x.close_clone()
                return []
            out.append(c)
        return out

    def _plan_each_at_once(self, path, keys, emit, clones, progress, cancel, meta) -> int:
        """plan_each with len(clones) albums looked up at the same time. An album's work belongs
        to one worker with its own Engine (an Engine holds the album it is on) and connection;
        the providers' pace and back-off are shared by the clones, so the requests per second
        do not change. Only this thread reports, and the plan of them all is made once, in KEYS
        order, as plan_each's."""
        root = Path(os.path.abspath(Path(path).expanduser()))
        found, found_lock = {}, threading.Lock()
        tasks, results = queue.SimpleQueue(), queue.SimpleQueue()
        for item in enumerate(keys):
            tasks.put(item)
        failed = threading.Event()
        kept = {}                                        # (rows kept as listed: _keep_row)

        def planner(e):
            return planmod.Planner(e.profile, e.job, extra_roots=[root], edits=getattr(e, "edits", None),
                                   file_anyway=getattr(e, "file_anyway", None),
                                   seed_claims=getattr(e, "seed_claims", None),
                                   offer_file_anyway=getattr(e, "offer_file_anyway", False))

        def notes(e):
            return {**getattr(e, "run_notes", {}), **e.notes}     # (outages: the clones' shared state, below)

        def worker(clone):
            try:
                e = engine_for_thread(self.profile, http=clone, log=self.log, job=self.job, **self._made_with)
                for name in ("edits", "force", "file_anyway", "seed_claims", "offer_file_anyway"):
                    if name in self.__dict__:
                        setattr(e, name, self.__dict__[name])
                e._listed_keys = set(keys) | set(getattr(self, "listed_keys", None) or ())    # (as plan_each)
                # (PRF-4: only the folders holding KEYS are read, once for them all)
                scan = (lambda p, skip=(), e=e: _albums_of_keys(e, p, keys, skip)) if e._own_scan() else e.albums_in

                def albums_in(p, skip=()):               # (the folder is scanned once, for them all)
                    k = (str(p), tuple(sorted(str(x) for x in skip)))
                    with found_lock:
                        if k not in found:
                            found[k] = scan(p, skip)
                        return list(found[k])
                e.albums_in = albums_in
                while not failed.is_set() and not (cancel and cancel()):
                    try:
                        i, k = tasks.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        with ex.meta_memo(meta, False) if meta is not None else contextlib.nullcontext():
                            props = e.proposals(path, lambda _i, _n, what, i=i: results.put(("progress", i, what)),
                                                cancel, only={k})
                    except Exception as exc:             # (one album's internal error: its row says so)
                        e.reached = set() if cancel and cancel() else {k}
                        props = _failed_album(k, exc, e._logger())
                    if k not in e.reached and cancel and cancel():
                        break                            # (cancelled before it: its rows stay)
                    for q in props:
                        q.source_dir = os.path.abspath(os.path.expanduser(str(q.source_dir)))
                        q.files = [os.path.abspath(os.path.expanduser(str(f))) for f in q.files]
                    out, (props,) = _build_despite_gone(lambda: planner(e), [props], e.log)
                    with_keys(out["albums"], props)
                    _keep_row(e, k, props, out, kept)
                    out["provider_notes"] = notes(e)
                    results.put(("done", i, k, props, out))
            except BaseException as exc:                 # (reported, and raised, by the coordinator)
                failed.set()
                results.put(("failed", exc))
            finally:
                clone.close_clone()
                results.put(("ended",))
        threads = [threading.Thread(target=worker, args=(c,), name=f"look-up-{n}", daemon=True)
                   for n, c in enumerate(clones)]
        for t in threads:
            t.start()
        done, told, error, live = {}, {}, None, len(threads)
        while live:
            msg = results.get()
            if msg[0] == "ended":
                live -= 1
            elif msg[0] == "failed":
                error = error or msg[1]
            elif msg[0] == "progress":
                if progress:
                    progress(msg[1], msg[2])
            else:
                _kind, i, k, props, out = msg
                done[i] = (k, props)
                told.update(out["provider_notes"])       # (a note one worker's album gave stays shown)
                out["provider_notes"] = {**outage_notes(getattr(self, "http", None)), **told}
                emit(k, out)
        for t in threads:
            t.join()
        if error is not None:
            raise error
        self.reached = {k for k, _p in done.values()}
        if len(done) > 1:
            whole, groups = _build_despite_gone(lambda: planner(self), _once_each([done[i][1] for i in sorted(done)]),
                                                getattr(self, "log", None))
            allp = [q for g in groups for q in g]
            with_keys(whole["albums"], allp)
            whole["provider_notes"] = {**outage_notes(getattr(self, "http", None)), **notes(self), **told}
            for i in sorted(done):
                k = done[i][0]
                emit(k, dict(whole, albums=[a for a in whole["albums"] if a.get("key") == k] + kept.get(k, [])))
        return len(done)


# albums looked up at once: enough to use the waiting on scans, images and files, few enough
# for a home machine and a network share (the providers' pace is shared and does not change)
LOOKUP_WORKERS = 3
_ALBUMS_IN = Engine.albums_in                  # (the engine's own scan: Engine._own_scan)
_MAKING = threading.Lock()


def engine_for_thread(*args, **kw) -> "Engine":
    """An Engine made while other threads make theirs (loading its plugin is not thread-safe)."""
    with _MAKING:
        return Engine(*args, **kw)


def _once_each(groups) -> list:
    """Each album's proposals once (DSC-10: a set's one album is what each of its folders'
    look-ups found - in a plan of them all, it is planned once)."""
    seen, out = set(), []
    for ps in groups:
        mine = []
        for q in ps:
            k = (os.path.realpath(str(q.source_dir)), tuple(sorted(os.path.realpath(str(f)) for f in q.files)))
            if q.files and k in seen:
                continue
            seen.add(k)
            mine.append(q)
        out.append(mine)
    return out


def group_key(folder, files) -> str:
    """One album of a folder, as List first names it: its folder and its files."""
    return "|".join([os.path.realpath(str(folder))] + sorted(os.path.realpath(str(f)) for f in files))


LOOKUP_FAILED = "could not be looked up: internal error (see log)"
FOLDER_UNREAD = "could not be looked up: folder could not be read (see log)"
CHANGED_SINCE_LISTED = "changed since it was listed: add its folder again"


def _short(exc, most=120) -> str:
    """An error in a few words for a row's reason (the log has it whole)."""
    words = (str(exc).strip().splitlines() or [""])[0].strip() or type(exc).__name__
    return words if len(words) <= most else words[:most - 1].rstrip() + "…"


def _keep_row(e, key, props, out, kept):
    """A row whose album changed since it was listed, when which of its folder's albums is its
    own cannot be told (Engine.proposals' kept_rows): it stays in OUT as listed, saying so -
    nothing of it can be applied, and it is not taken off the list."""
    note = (getattr(e, "kept_rows", None) or {}).get(key)
    if not note or props:
        return
    parts = key.split("|")
    row = {"source_dir": parts[0], "status": "listed", "label": "", "rule": None,
           "identification": {"status": "listed", "reason": note}, "release": {}, "files": [],
           "companions": [], "artwork": None, "conflicts": [], "skipped": [], "notes": [note],
           "album_files": parts[1:], "key": key, "changed_since_listed": True}
    out["albums"] = list(out.get("albums") or []) + [row]
    kept[key] = [row]


def _failed_album(key, exc, log=None, props=None) -> list:
    """The album of KEY (or the proposals PROPS of one album), after an error while it was
    looked up or planned: a row saying so, Unresolved - "could not be looked up: internal error
    (see log)", or "... folder could not be read" when its folder could not be read - the error
    logged with its traceback. Planned with no files: nothing of it is written, moved or marked,
    and Apply has nothing to do for it; its row's key and files are kept (with_keys)."""
    why = FOLDER_UNREAD if isinstance(exc, OSError) else LOOKUP_FAILED
    if log is not None:
        log.error("looking up %s failed (%s): %s: %s", (key or "").split("|", 1)[0] or
                  (str(props[0].source_dir) if props else "?"), why, type(exc).__name__, exc, exc_info=exc)
    if not props:
        parts = (key or "").split("|")
        props = [planmod.Proposal(source_dir=parts[0], files=parts[1:], status="unresolved", reason="")]
    out = []
    for q in props:
        p = planmod.Proposal(source_dir=str(q.source_dir), files=[], status="unresolved", reason=why,
                             reason_codes=["lookup_failed"])
        p.group_key = getattr(q, "group_key", None) or key
        p.group_files = [str(f) for f in getattr(q, "group_files", None) or q.files]
        p.failed_row = True
        out.append(p)
    return out


def _culprit(planner, groups):
    """The album (its index in GROUPS) whose planning raised: one that fails planned alone, else
    the last of the shortest run of them, from the first, that fails planned together (found
    by halving: a few plans, not one per album). None when none is found."""
    if len(groups) == 1:
        return 0
    for i, g in enumerate(groups):
        try:
            planner().build(list(g))
        except Exception:
            return i

    def fails(n):
        try:
            planner().build([q for g in groups[:n] for q in g])
            return False
        except Exception:
            return True
    lo, hi = 2, len(groups)
    if not fails(hi):
        return None
    while lo < hi:
        mid = (lo + hi) // 2
        if fails(mid):
            hi = mid
        else:
            lo = mid + 1
    return hi - 1


def _build_despite_gone(planner, groups, log=None) -> tuple:
    """(the plan of these albums, the albums as planned). groups: each album's proposals. An album
    a file of which cannot be read while it is planned (a network share that lost it for a moment, a
    folder moved away) becomes a row saying so, as one that failed being identified does
    (Engine.proposals: one bad album never stops the run); the others are planned."""
    groups = [list(g) for g in groups]
    for _try in range(sum(len(q.files) + 1 for g in groups for q in g)):
        try:
            return _unresolved_rows(planner().build([q for g in groups for q in g]), groups), groups
        except OSError as exc:
            where = str(exc.filename or "")
            spots = [(g, i) for g in groups for i in range(len(g)) if g[i].files]
            inside = [(g, i) for g, i in spots if where and (where == str(g[i].source_dir)
                      or where.startswith(str(g[i].source_dir) + os.sep))]
            hit = next(((g, i) for g, i in spots if where in map(str, g[i].files)), None) or \
                max(inside, key=lambda x: len(str(x[0][x[1]].source_dir)), default=None)
            if hit is None:
                raise
            g, i = hit
            q = g[i]
            if log:
                log.warning("planning %s failed: %s", q.source_dir, exc)
            # (no files: nothing of it is tagged, moved or marked on what was seen of it for a moment)
            p = planmod.Proposal(source_dir=str(q.source_dir), files=[], status="unresolved",
                                 reason=f"error: {exc}", reason_codes=["other"])
            p.group_key = getattr(q, "group_key", None)
            p.group_files = [str(f) for f in getattr(q, "group_files", None) or q.files]      # (its row's key: with_keys)
            g[i] = p
        except Exception as exc:
            # (an internal error planning one album: that album's rows say so, the others are
            # planned. Out of memory: never planned again to find which - only one album's is)
            if isinstance(exc, MemoryError) and len(groups) > 1:
                raise
            bad = _culprit(planner, groups)
            if bad is None or all(getattr(q, "failed_row", False) for q in groups[bad]):
                raise
            groups[bad] = _failed_album(None, exc, log, props=groups[bad])
    return _unresolved_rows(planner().build([q for g in groups for q in g]), groups), groups


def _unresolved_rows(plan, groups=()) -> dict:
    """An album that could not be looked up (reason code lookup_failed: _failed_album, Engine.
    proposals' error), planned with no files, is Unresolved saying why - not Skipped, as an
    album with nothing to do is. Told by its code, whatever the length of its words."""
    import reasons
    for a in plan.get("albums") or []:
        ident = a.get("identification") or {}
        codes = set(ident.get("reason_codes") or ()) | set(reasons.parse(ident.get("reason") or "")[0])
        if a.get("status") == "skipped" and not a.get("files") and "lookup_failed" in codes:
            a["status"], a["label"] = "unresolved", "Unresolved"
    return plan


def with_keys(entries, props):
    """Each plan entry's "key": the group_key() of the album it was found as (Engine.proposals'
    group_key), also for a set planned as a row per disc or a folder split per version - whose
    own folder and files are not the album's. Overview's Look Up and Refresh send these keys."""
    of = {}
    for q in props:
        k = getattr(q, "group_key", None)
        if k:
            for f in list(q.files or []) + list(getattr(q, "group_files", None) or []):
                of.setdefault(str(f), k)
            if not q.files:                              # (planned with none: _build_despite_gone)
                of.setdefault(str(q.source_dir), k)
    for a in entries:
        if a.get("key"):
            continue
        k = next((of[str(f)] for f in list(a.get("album_files") or []) +
                  [fe.get("src") for fe in a.get("files") or [] if isinstance(fe, dict)] + [a.get("source_dir")]
                  if str(f) in of), None)
        if k:
            a["key"] = k


# A review or a "no match" may still have an established album (an error is retried instead)
PARTIAL_FROM = ("review", "nomatch")
# a MusicBrainz release's own data, left out before its tags are made for a Partial album
_MB_PRESSING_KEYS = ("relations", "barcode", "asin", "label-info", "country", "release-events", "status",
                     "text-representation", "date", "disambiguation", "annotation", "packaging",
                     "genres", "tags", "user-genres", "user-tags")
# a release's album artist fields (left out when the pressings' credits are not shown to agree)
_ALBUM_ARTIST_KEYS = ("musicbrainz_albumartistid", "albumartist", "albumartists", "albumartistsort",
                      "~albumartists", "~albumartistcomment", "~albumartists_sort", "~albumartists_countries",
                      "~albumartists_comments")
# a track's names (kept when its recording is not the same on every pressing; compared themselves)
_TRACK_NAMES = {"title", "artist", "artists", "artistsort", "musicbrainz_artistid", "~recordingtitle",
                "~artists_sort", "~artists_countries", "~artists_comments", "~artistcomment"}
_ALT = re.compile(r"^(mb|discogs):(\S+) ([\d.]+)(?:\s|$)")
# pool candidates tried, best score first, as the album's anchor (each one is a lookup)
_ANCHOR_TRIES = 20
# a file the album's releases do not have (a bonus track): only what every track of it shares
_ALBUM_LEVEL = {"album", "albumartist", "albumartists", "albumartistsort", "musicbrainz_albumartistid",
                "musicbrainz_releasegroupid", "originaldate", "originalyear", "date", "genre", "releasetype",
                "compilation", "~releasegroup", "~primaryreleasetype", "~secondaryreleasetype",
                "~releasegroup_firstreleasedate", "~albumartists", "~albumartistcomment"}
# what claims one recording (left out of a match by titles: the audio is not shown to be it)
_RECORDING_CLAIMS = {"musicbrainz_recordingid", "isrc", "engineer", "mixer", "producer", "remixer",
                     "~recordingcomment", "~recording_firstreleasedate"}


# a folder of restored or remastered audio ("[Restored]", "[RM]", "Remastered", "RMX"): another
# master than the release's, so its ISRCs (which name one master's recording) are not the files'
_RESTORED = re.compile(r"[\[(](?:restored|rm)[\])]|remaster|\brmx\b", re.I)


# The Sources setting and the IDs already in the files (engine.Engine.identify).
# What names one MusicBrainz release (its tracks and recordings as listed on it); the artist
# IDs name artists, whatever the release.
# (musicbrainz_trackid is the release track's ID, as tagio and Picard name it)
# (a preferred mode removes only these; the release group and recordings hold for every
# pressing of the album, and the disc ID is your copy's own, measured from it)
MB_RELEASE_TAGS = ("musicbrainz_albumid", "musicbrainz_trackid")
# never removed: what your CD itself measures, whichever database the tags come from
OWN_COPY_TAGS = ("musicbrainz_discid",)
# Discogs fields that are not one pressing's: its master, artists and styles
DG_ALBUM_TAGS = ("discogs_master_id", "discogs_artist_id", "discogs_albumartist_id", "discogs_style")


def _pressings_differ(pick, other, side, mb_links, discs=None) -> list:
    """TAG-3: what shows two releases of two databases are two pressings ([] when nothing
    does). A link between them (MusicBrainz's release linking the Discogs one, or the pick
    linking it) makes them one. Else: both list barcodes and share none; both list catalogue
    numbers and none agrees; both name their format and the families differ (CD, vinyl,
    digital, cassette); a MusicBrainz release linking only other Discogs releases than this
    one's; a Discogs release another MusicBrainz release than this one links; a MusicBrainz
    release whose disc IDs none fits your copy's own TOC (discs: Engine._disc_ids_differ). What either
    lacks is no evidence; a country alone is none (a CD made in the EU sold in the UK)."""
    if other.get("source") == "mb" and set(other.get("links") or ()) & side["discogs_release_id"]:
        return []
    if other.get("source") == "discogs" and (other.get("id") in (pick.get("links") or ()) or
                                             set(mb_links.get(other.get("id")) or ()) & side["musicbrainz_albumid"]):
        return []
    why = []
    a, b = set(pick.get("barcodes") or ()), set(other.get("barcodes") or ())
    if a and b and not a & b:
        why.append(f"barcode {', '.join(sorted(b))}, not {', '.join(sorted(a))}")
    if pick.get("catnos") and other.get("catnos") and not _catnos_agree(pick, other):
        why.append(f"catalogue number {', '.join(other['catnos']).upper()}, not {', '.join(pick['catnos']).upper()}")
    a, b = set(pick.get("formats") or ()), set(other.get("formats") or ())
    if a and b and not a & b:
        why.append(f"{'/'.join(sorted(b))}, not {'/'.join(sorted(a))}")
    if other.get("source") == "mb" and other.get("links"):
        why.append(f"MusicBrainz links it to Discogs {', '.join(other['links'])}")
    if other.get("source") == "discogs":
        by = [m for m in mb_links.get(other.get("id")) or () if m not in side["musicbrainz_albumid"]]
        if by:
            why.append(f"MusicBrainz's {', '.join(by)} links it")
    if not why and discs is not None and other.get("source") == "mb":
        # (asked last, and only then: it may cost a lookup - its disc IDs against your copy's TOC)
        w = discs()
        if w:
            why.append(w)
    return why


def _printed_on_copy(facts, text) -> bool:
    """TAG-3: one of this release's own numbers stands in your copy's print - a distinctive
    catalogue number by containment in the folded text, however spaced ("20 21276-9"), as the
    pick's are matched (pressing_clues); a barcode printed as one number (barcode_printed)."""
    if not text:
        return False
    hay = mc.norm_code(text)
    for c in facts.get("catnos") or ():
        digits = re.sub(r"\D", "", c)
        if ((len(c) >= 4 and re.search(r"[a-z]", c) and digits) or len(digits) >= 6) and c in hay:
            return True
    return any(mc.barcode_printed(text, b) for b in facts.get("barcodes") or ())


def _catnos_agree(a, b) -> bool:
    """TAG-3: two releases' catalogue numbers agree - as written (a range "SICP 10151~4" is
    "SICP 10151-4", catnos_agree) or normalised."""
    return bool(mc.catnos_agree(a.get("catnos_raw") or (), b.get("catnos_raw") or ())
                or mc.catnos_agree(a.get("catnos") or (), b.get("catnos") or ()))


def _pressings_value(field, values, facts) -> bool:
    """TAG-3: the file's value of a generic field is the losing pressing's own (its barcode,
    catalogue number, label, country, media family or year)."""
    vals = [str(v) for v in values or () if str(v).strip()]
    if not vals:
        return False
    if field == "barcode":
        return any(mc.barcode_key(v) in set(facts.get("barcodes") or ()) for v in vals)
    if field == "catalognumber":
        return bool(mc.catnos_agree(vals, facts.get("catnos_raw") or ())
                    or mc.catnos_agree([mc.norm_code(v) for v in vals], facts.get("catnos") or ()))
    if field == "label":
        # (a database's labels joined as one: "Mirage Records; Universal")
        theirs = {mc.fold(x) for x in re.split(r"\s*;\s*", str(facts.get("label") or "")) if x.strip()}
        return bool(theirs) and (all(mc.fold(v) in theirs for v in vals)
                                 or any(mc.fold(v) == mc.fold(facts["label"]) for v in vals))
    if field == "releasecountry":
        # (the code the Discogs plugin gives the country - "Egypt" is EG - where it gave one)
        code = facts.get("country_code") or mc.country_code(facts.get("country"))
        return bool(code) and any(mc.country_code(v) == code for v in vals)
    if field == "media":
        return bool(facts.get("formats")) and all(mc.media_kind(v) in facts["formats"] for v in vals)
    if field == "date":
        return bool(facts.get("year")) and any(str(v)[:4] == str(facts["year"]) for v in vals)
    return False


def other_source_tags(source: str, keys, release_only: bool) -> list:
    """The fields among keys that belong to the database the Sources setting does not prefer
    (MusicBrainz's under discogs_*, Discogs's under musicbrainz_*): every one of them, or, with
    release_only, only those that describe one release of it."""
    if source.startswith("discogs"):
        return [k for k in keys if k.startswith("musicbrainz_") and k not in OWN_COPY_TAGS
                and (not release_only or k in MB_RELEASE_TAGS)]
    return [k for k in keys if k.startswith("discogs_") and (not release_only or k not in DG_ALBUM_TAGS)]


def follow_source(prop, source: str, asked: bool = True, sure=None, differs=None):
    """The identified album's tags as the Sources setting has them, against the IDs already in
    its files. Those IDs helped find the release (the matcher scores the releases they name),
    but what the other database's IDs say is never a conflict:
    - *_only: none of the other database's tags is written; the files' own are removed;
    - *_preferred: a file whose other-database release ID differs from the one written (or
      none is written: that database has no entry of the chosen pressing) loses that release's
      fields; one written in its place replaces it (only for an exact release: an album-only
      or Partial album proves no release to replace it with).
    A preferred mode removes nothing when the other database was not asked or failed (asked
    False), or was not sure of a release other than the one written (sure: {"mb" |
    "discogs": the release ID each database was sure of}, as the matcher found them): its
    silence or doubt then says nothing against the release ID your files already carry.
    The disc ID (OWN_COPY_TAGS) is never removed: it is your CD's own.
    A preferred mode removes the other database's release only when it is another pressing by
    evidence (differs(field, value): Engine._another_pressing, TAG-3's positive differences): a
    release ID of the same pressing, or one nothing is known of (no differs given, data missing,
    an outage), stays - the Sources setting chooses whose release is written; it deletes no ID
    of your pressing.
    Removals are planned like any other change (shown in the preview, undoable)."""
    import tagio
    only = source.endswith("_only")
    id_key = "musicbrainz_albumid" if source.startswith("discogs") else "discogs_release_id"
    sure_other = str((sure or {}).get("mb" if source.startswith("discogs") else "discogs") or "")
    for i, t in enumerate(prop.targets):
        for k in other_source_tags(source, list(t), release_only=False) if only else ():
            t.pop(k, None)
        try:
            before = tagio.read(prop.files[i]) if i < len(prop.files) else {}
        except Exception:
            before = {}
        had = [str(v) for v in before.get(id_key) or [] if str(v)]
        written = [str(v) for v in t.get(id_key) or []]
        if not only and (not asked or not had or had == written
                         or (getattr(prop, "tier", "") or "exact") != "exact"
                         or not sure_other or sure_other in written):
            continue
        if not only and not (differs and any(differs(id_key, v) for v in had)):
            continue                         # (no evidence of two pressings: kept)
        gone = [k for k in other_source_tags(source, [k for k, v in before.items() if v], release_only=not only)
                if k not in t]
        if gone:
            t["~remove"] = list(dict.fromkeys(list(t.get("~remove") or []) + gone))
    return prop


def _holds_every_file(out, n) -> bool:
    """The matcher proved one release exactly whose tracks are as many as the folder's files
    (none missing): a folder whose titles come twice is that release, not two versions mixed."""
    best = (out or {}).get("best") or {}
    return bool((out or {}).get("status") == "auto" and best and not best.get("missing")
                and best.get("tracks") == n)


def _vinylish(g) -> bool:
    """VIN-7: G's folder is a vinyl rip's - its name says so, or its first file's media tag is vinyl."""
    if mc.media_hint(Path(str(g.folder)).name) == "vinyl":
        return True
    try:
        import tagio
        return any(mc.media_kind(m) == "vinyl" for m in tagio.read(g.files[0]).get("media") or ())
    except Exception:
        return False


def rip_signature(f) -> tuple:
    """VIN-7: what one rip wrote on its files: (comment, date, encoder, bits, rate)."""
    import mutagen
    import tagio
    t = tagio.read(f)
    try:
        info = mutagen.File(str(f)).info
        res = (getattr(info, "bits_per_sample", 0), getattr(info, "sample_rate", 0))
    except Exception:
        res = (0, 0)
    return (str((t.get("comment") or [""])[0]).strip(), str((t.get("date") or [""])[0]).strip(),
            str((t.get("encoder") or [""])[0]).strip(), *res)


def one_rip_each(halves) -> bool:
    """VIN-7: each half of a folder's files is one rip throughout (one signature, rip_signature)
    and the two are not the same: the copy marks fell on one rip's files only."""
    try:
        sigs = [{rip_signature(f) for f in h} for h in halves]
    except Exception:
        return False
    return all(len(s) == 1 for s in sigs) and sigs[0] != sigs[1]


def same_audio_lengths(groups) -> str:
    """VIN-7: why two sets are no two rips - every track's length the other's to the sample (a
    transcode of one rip: "a lower-resolution copy of the same rip"; or the same rip twice) - or ""."""
    import mutagen
    def by_num(fs):
        out = {}
        for f in fs:
            try:
                out[int(str(ma.read_file(Path(f)).get("num") or 0))] = Path(f)
            except Exception:
                continue
        return out
    a, b = by_num(groups[0]), by_num(groups[1])
    common = sorted(set(a) & set(b))
    if not common or len(common) < max(len(a), len(b)):
        return ""
    lower = False
    for n in common:
        try:
            ia, ib = mutagen.File(str(a[n])).info, mutagen.File(str(b[n])).info
        except Exception:
            return ""
        if abs(float(ia.length) - float(ib.length)) > 0.0005:
            return ""
        if (getattr(ia, "sample_rate", 0), getattr(ia, "bits_per_sample", 0)) != \
                (getattr(ib, "sample_rate", 0), getattr(ib, "bits_per_sample", 0)):
            lower = True
    return ("a lower-resolution copy of the same rip (every length the other's to the sample)" if lower else
            "the same rip twice (every length the other's to the sample)")


RIPS_WORDS = "two rips of one record are mixed in this folder"      # VIN-7 (Needs Attention: "two rips in one folder")


def rip_sets(files) -> list:
    """VIN-7: [set, set] when one folder's files are two rips of one record mixed together, told
    apart by their own tags rather than their names (a 4-track rip beside an 8-track one: the
    suite as one track): one format, one folder, track numbers taken twice on one disc, and the
    files falling into exactly two sets by what each rip wrote (comment, date, encoder, the
    audio's bits and rate) - each set numbered 1..n once, three files or more. [] otherwise:
    never on weaker evidence. (Track numbers first, from the file names when the tags are not
    read yet: a folder whose numbers are each taken once costs nothing more.)"""
    fs = [Path(f) for f in files]
    if len(fs) < 6 or len({f.parent for f in fs}) != 1 or len({f.suffix.lower() for f in fs}) != 1:
        return []
    lead = [re.match(r"^\s*(\d{1,3})\b", f.stem) for f in fs]
    if all(lead) and len({int(m.group(1)) for m in lead}) == len(fs):
        return []                                    # (no number taken twice by the names)
    import mutagen
    import tagio
    sig, nums = {}, {}
    for f in fs:
        try:
            i = ma.read_file(f)
            n = int(str(i.get("num") or "0"))
        except Exception:
            return []
        if not n:
            return []
        nums[f] = n
    if len(set(nums.values())) == len(fs):
        return []                                    # (no track number taken twice: one rip)
    for f in fs:
        try:
            t = tagio.read(f)
            info = mutagen.File(str(f)).info
            res = (getattr(info, "bits_per_sample", 0), getattr(info, "sample_rate", 0))
        except Exception:
            return []
        key = (str((t.get("comment") or [""])[0]).strip(), str((t.get("date") or [""])[0]).strip(),
               str((t.get("encoder") or [""])[0]).strip(), res, str((t.get("discnumber") or [""])[0]))
        sig.setdefault(key, []).append(f)
    if len(sig) != 2 or len({k[4] for k in sig}) != 1:
        return []                                    # (not two sets; two discs of one set)
    groups = sorted(sig.values(), key=lambda g: (-len(g), str(min(g))))
    for g in groups:
        if len(g) < 3 or sorted(nums[f] for f in g) != list(range(1, len(g) + 1)):
            return []
    return [sorted(g, key=lambda f: ma._natural_key(str(f))) for g in groups]


def mixed_versions(files, versions=False, rips=False) -> dict:
    """{disc folder: [[file, file], ...]} when one folder holds two versions of the album:
    at least two titles twice in one format and on one disc as the tags number them (the
    same title, or the same file name apart from its number and a copy's " (1)"), those
    pairs at least 80% of its files, and most pairs not the same audio (lengths more than
    match_albums._SAME_AUDIO_S apart, checksums different) - a track saved twice is one copy
    (match_albums drops it). {} otherwise.
    versions: a version's mark in a track title aside ("Twilight" and "Twilight (Remix
    2024)" are one title). An EP or single often holds a track and its remix, or a mono
    and stereo mix, so that reading is only asked once the folder fits no release.
    rips: a vinyl rip's folder (VIN-7) - a copy's "x_2" pairs too, not only its "x (1)"."""
    by_folder = {}
    for f in files:
        by_folder.setdefault((Path(f).parent, Path(f).suffix.lower()), []).append(Path(f))
    out = {}
    for (folder, _ext), fs in by_folder.items():
        if len(fs) < 4:
            continue
        info = {}
        for f in fs:
            try:
                info[f] = ma.read_file(f)
            except Exception:
                info[f] = None
        if any(i is None for i in info.values()):
            continue
        keys = {}
        for f in fs:
            # (a version's own title, "Twilight (Remix 2024)", is the title of the album's track)
            title = info[f]["title"] or ""
            k = ma._track_key(Path(re.sub(r"\s*\(\d+\)$|_[2-9]$" if rips else r"\s*\(\d+\)$", "", f.stem) + f.suffix),
                              dict(info[f], title=web_album_title(title) if versions and title else title))
            if k:
                keys.setdefault(k, []).append(f)
        # (the same title on disc 1 and disc 2, as the tags number them, is a two-disc set's)
        pairs = [sorted(v) for v in keys.values() if len(v) == 2 and info[v[0]]["disc"] == info[v[1]]["disc"]]
        if len(pairs) < 2 or 2 * len(pairs) < 0.8 * len(fs):
            continue

        def apart(a, b):
            ia, ib = info[a], info[b]
            if ia.get("audio_md5") and ia.get("audio_md5") == ib.get("audio_md5"):
                return False
            return abs(ia["secs"] - ib["secs"]) > ma._SAME_AUDIO_S
        if 2 * sum(1 for a, b in pairs if apart(a, b)) >= len(pairs):
            out[folder] = sorted(pairs, key=lambda pr: (info[pr[0]]["num"] or 9999, str(pr[0])))
    return out


def _names(fs) -> str:
    return ", ".join(Path(f).name for f in fs)


_COPY_SUFFIX = re.compile(r"\s\(\d+\)$")
_RIP_COPY_SUFFIX = re.compile(r"(?:\s\(\d+\)|_[2-9])$")      # VIN-7 only: a copy's "x (1)" or "x_2"


def version_sides(pairs, info, suffix=None) -> tuple:
    """([(file, file)], how): each pair of a folder holding two versions (mixed_versions)
    in the same order - the first of every pair one version, the second the other - as the
    files themselves suggest: a copy's " (1)" on one of each pair, the version marks in
    their titles ("Twilight (Remix 2024)"), their album tags, or the releases they are
    tagged with. Only a first guess: assign_versions decides by the lengths. how: what the
    order was read from ("" when nothing told: the pairs as sorted)."""
    def by(key):
        keys = [(key(a), key(b)) for a, b in pairs]
        if any(x == y for x, y in keys):
            return None
        first = keys[0][0]
        out = []
        for (a, b), (x, y) in zip(pairs, keys):
            if x == first:
                out.append((a, b))
            elif y == first:
                out.append((b, a))
            else:
                return None
        return out
    suffix = suffix or _COPY_SUFFIX
    tests = (("copy names", lambda f: bool(suffix.search(Path(f).stem))),
             ("title marks", lambda f: _title_marks(info[f].get("title"))),
             ("album tags", lambda f: mc.fold(info[f].get("album"))),
             ("release IDs", lambda f: (info[f].get("mb_albumid") or "", info[f].get("discogs_releaseid") or "")))
    for how, key in tests:
        got = by(key)
        if got:
            return got, how
    return [tuple(pr) for pr in pairs], ""


def _track_secs(tracks, title) -> list:
    """The lengths of a release's tracks (tracks: [(title, seconds)]) titled as title, a
    version's mark aside."""
    want = web_album_title(title)
    return [secs for t, secs in tracks if secs and mc.track_titles_agree(want, web_album_title(t))]


def _release_key(p):
    return (p.provider, p.release_id or p.url or _release_words(p))


def _not_own(props) -> str:
    """Why these versions' albums (_version_alone) are no split's: one proves no release of its
    own (a release matched for sure, or a web album by its lengths), lists no track lengths, or
    both are the same release; "" when they are."""
    for p in props:
        own = p.status == "identified" and (p.tier in ("", "exact") or
                                            (p.tier == "partial" and str(p.provider).startswith("web:")))
        if not own:
            return (f"{len(p.files)} of its files ({_names(p.files[:3])}...) prove no release of their own: "
                    + (p.reason or "; ".join(p.why or []) or "no match"))
        if not getattr(p, "release_secs", None):
            return f"{_release_words(p)} lists no track lengths"
    if _release_key(props[0]) == _release_key(props[1]):
        return f"both halves fit the same release ({_release_words(props[0])})"
    return ""


def assign_versions(pairs, loose, info, versions, cfg, guided=False) -> tuple:
    """([files of version 1, files of version 2], unsure, cost): which version each file of a
    folder holding two is, by its length against each version's tracks of its title
    (versions: two releases' [(title, seconds)]). A pair goes one way round when that fits
    clearly better (by more than 1 s in all) and each file then fits its own version's
    length. guided (the pairs are in the order the files suggest, version_sides): a pair whose
    lengths are too close to tell goes the way every pair told apart went - when at least two
    (and a quarter of them) were, all the same way round - and each file still fits its own
    version's length. A file with no twin goes to the one version it fits (or fits clearly
    better). unsure: the files that are not told apart; cost: how far, in seconds, the files'
    lengths are from their versions' in all."""
    got, unsure, cost = [[], []], [], 0.0

    def title(f):
        return info[f].get("title") or re.sub(r"^[\d\s._\-]+", "", _COPY_SUFFIX.sub("", Path(f).stem))

    def near(f, k):
        cands = _track_secs(versions[k], title(f))
        num = int(info[f].get("num") or 0)
        if not cands and 0 < num <= len(versions[k]) and versions[k][num - 1][1]:
            cands = [versions[k][num - 1][1]]    # (its title spelled otherwise there: its track number's)
        return min(cands, key=lambda x: abs(x - info[f]["secs"])) if cands else None

    def ok(f, x):
        return mc.duration_ok(info[f]["secs"], x, cfg)
    told, close = [], []
    for a, b in pairs:
        xa, xb, ya, yb = near(a, 0), near(b, 1), near(a, 1), near(b, 0)
        if None in (xa, xb, ya, yb):
            unsure += [a, b]
            continue
        ab = abs(info[a]["secs"] - xa) + abs(info[b]["secs"] - xb)
        ba = abs(info[a]["secs"] - ya) + abs(info[b]["secs"] - yb)
        straight = ab < ba
        one, two, x1, x2 = (a, b, xa, xb) if straight else (b, a, yb, ya)
        if abs(ab - ba) > 1.0 and ok(one, x1) and ok(two, x2):
            got[0].append(one)
            got[1].append(two)
            told.append(straight)
            cost += min(ab, ba)
        else:
            close.append((a, b, xa, xb, ya, yb, ab, ba))
    way = set(told)
    if close and guided and len(way) == 1 and len(told) >= max(2, len(pairs) / 4):
        straight = way.pop()
        for a, b, xa, xb, ya, yb, ab, ba in close:
            one, two, x1, x2 = (a, b, xa, xb) if straight else (b, a, yb, ya)
            if ok(one, x1) and ok(two, x2):
                got[0].append(one)
                got[1].append(two)
                cost += ab if straight else ba
            else:
                unsure += [a, b]
    else:
        for a, b, *_rest in close:
            unsure += [a, b]
    for f in loose:
        x = [near(f, 0), near(f, 1)]
        fits = [k for k in (0, 1) if x[k] is not None and ok(f, x[k])]
        if len(fits) == 2 and abs(abs(info[f]["secs"] - x[0]) - abs(info[f]["secs"] - x[1])) > 1.0:
            fits = [min(fits, key=lambda k: abs(info[f]["secs"] - x[k]))]
        if len(fits) == 1:
            got[fits[0]].append(f)
            cost += abs(info[f]["secs"] - x[fits[0]])
        else:
            unsure.append(f)
    return got, unsure, cost


def versions_apart(mixed, found, cfg) -> list:
    """Plan notes: which files of a mixed folder (mixed_versions) are which of two web
    versions of the album (found: [(source name, candidate)]), where their lengths tell them
    apart. The two versions are the pair of candidates the files' lengths fit best; a pair of
    files is told apart only when one way round fits clearly better (by more than 1 s in all)
    and each file then fits its own version's track. [] when no two versions fit."""
    import match_core as _mc
    pairs = [pr for prs in mixed.values() for pr in prs]
    secs = {f: ma.read_file(f)["secs"] for pr in pairs for f in pr}
    titles = {pr[0]: (ma.read_file(pr[0])["title"] or re.sub(r"^[\d\s._\-]+", "", pr[0].stem)) for pr in pairs}

    def lengths(c):
        """Each pair's track length on candidate c (None where c lacks its title)."""
        out = []
        for a, _b in pairs:
            t = next((t for t in c["tracks"] if t.get("secs") and _mc.track_titles_agree(
                web_album_title(titles[a]), web_album_title(t.get("title")))), None)
            out.append(t["secs"] if t else None)
        return out
    cands = [(n, c, lengths(c)) for n, c in found if c.get("tracks")]
    cands = [x for x in cands if sum(1 for v in x[2] if v) >= 0.8 * len(pairs)]
    best = None
    for i, (n1, c1, l1) in enumerate(cands):
        for n2, c2, l2 in cands[i + 1:]:
            if _mc.fold(c1.get("title")) == _mc.fold(c2.get("title")):
                continue
            cost = 0.0
            for (a, b), x, y in zip(pairs, l1, l2):
                if x and y:
                    cost += min(abs(secs[a] - x) + abs(secs[b] - y), abs(secs[a] - y) + abs(secs[b] - x))
            if best is None or cost < best[0]:
                best = (cost, (n1, c1, l1), (n2, c2, l2))
    if best is None:
        return []
    (n1, c1, l1), (n2, c2, l2) = best[1], best[2]
    first, second, unsure = [], [], []
    for (a, b), x, y in zip(pairs, l1, l2):
        if not (x and y):
            unsure += [a, b]
            continue
        ab, ba = abs(secs[a] - x) + abs(secs[b] - y), abs(secs[a] - y) + abs(secs[b] - x)
        one, two = (a, b) if ab < ba else (b, a)
        if abs(ab - ba) > 1.0 and _mc.duration_ok(secs[one], x, cfg) and _mc.duration_ok(secs[two], y, cfg):
            first.append(one)
            second.append(two)
        else:
            unsure += [a, b]
    if not first:
        return []

    def names(fs):
        return ", ".join(f.name for f in fs)
    notes = [f"the files' lengths fit {n1} \"{c1.get('title')}\" ({c1.get('url')}): {names(first)}",
             f"the files' lengths fit {n2} \"{c2.get('title')}\" ({c2.get('url')}): {names(second)}"]
    if unsure:
        notes.append(f"their lengths do not tell these apart: {names(unsure)}")
    return notes


def one_disc_name(folder_name: str) -> str:
    """A set's folder name as one disc of it reads it: "[2024 RM + RMX 2CD]" -> "[2024 RM +
    RMX CD]" (how many discs is the set's, not the disc's)."""
    return re.sub(r"\b\d+\s*[x×]?\s*(CD|LP|SACD|DVD)s?\b", r"\1", folder_name, flags=re.I)


def _renumbered(album: dict, discs: dict) -> dict:
    """DSC-10: the album read_album gave, each track on the disc its folder is of the release
    ({folder: disc}), not the folders' order; its tables of contents follow their discs."""
    moved = {}
    for t in album.get("tracks") or []:
        d = discs.get(Path(str(t.get("path") or "")).parent)
        if d is None:
            continue
        if moved.setdefault(t.get("disc"), d) != d:
            return album                                 # (one disc read from two folders: as it was)
    out = dict(album)
    out["tracks"] = sorted((dict(t, disc=moved.get(t.get("disc"), t.get("disc"))) for t in album.get("tracks") or []),
                           key=lambda t: (t["disc"] or 0, t.get("num") or 0))
    if isinstance(album.get("tocs"), dict):
        out["tocs"] = {moved.get(d, d): v for d, v in album["tocs"].items()}
    out["discs"] = len({t["disc"] for t in out["tracks"]}) or 1
    return out


def _separate_releases(parts) -> bool:
    """DSC-10 (as DSC-9): sibling folders shown to be separate releases by their own tags (an
    earlier job of TagMender's aside): each carries a barcode (or, none carrying one, a
    catalogue number) and no two share one - or, sharing no code, their years differ."""
    barcodes, catnos, years = [], [], []
    for q in parts:
        ms = [m for m in (ex.read_meta(f) for f in q.files) if not _first(m, "tagmender")]
        barcodes.append({mc.norm_code(_first(m, "barcode")) for m in ms} - {""})
        catnos.append({mc.norm_code(_first(m, "catalognumber")) for m in ms} - _NO_CODE)
        years.append({_first(m, "date")[:4] for m in ms} - {""})
    codes = barcodes if any(barcodes) else catnos
    if all(codes) and not any(a & b for x, a in enumerate(codes) for b in codes[x + 1:]):
        return True
    shared = any(a & b for x, a in enumerate(codes) for b in codes[x + 1:])
    return not shared and all(len(y) == 1 for y in years) and len({next(iter(y)) for y in years}) > 1


_VIDEO_MEDIUM = re.compile(r"\b(?:dvd|blu-?ray|hd-?dvd|vhs|laserdisc|umd)\b", re.I)


def _discogs_media(det) -> list:
    """TAG-4 (as MED-11): each disc of a Discogs release in order, as its format names it -
    "video" for a DVD-Video, Blu-ray or VHS (a DVD-Audio, an audio Blu-ray are audio), else
    "audio" - from its formats list and their quantities; [] when it gives none."""
    out = []
    for f in (det or {}).get("formats") or []:
        if str(f.get("name") or "").strip().casefold() in ("box set", "all media"):
            continue                                     # (a container, no disc of its own)
        try:
            qty = max(1, int(str(f.get("qty") or "1").strip() or 1))
        except ValueError:
            qty = 1
        words = " ".join([str(f.get("name") or "")] + [str(x) for x in f.get("descriptions") or []])
        video = bool(_VIDEO_MEDIUM.search(words)) and not re.search(r"dvd-?audio|blu-?ray audio|pure audio", words, re.I)
        out += ["video" if video else "audio"] * qty
    return out


def _discogs_shape(det, cand) -> tuple:
    """TAG-4: (a Discogs release's audio discs as their track counts in order, whether that
    count is unsure). A disc its formats name as video ("2-1" ... on the DVD of a CD and
    DVD-Video) is no audio disc - counted by the formats only when they list as many discs as
    the tracklist has. Unsure: the formats name a DVD, Blu-ray, VHS or the like (video or audio:
    MusicBrainz may count it otherwise - its "Blu-ray" is video to it), or list another number
    of discs than the tracklist; its disc count alone then shows no other pressing."""
    shape, media = _disc_shape(cand), _discogs_media(det)
    unsure = len(media) != len(shape) or any(
        _VIDEO_MEDIUM.search(" ".join([str(f.get("name") or "")] + [str(x) for x in f.get("descriptions") or []]))
        for f in (det or {}).get("formats") or [])
    if len(media) == len(shape):
        shape = [n for n, m in zip(shape, media) if m == "audio"]
    return shape, unsure


def _disc_shape(cand) -> list:
    """DSC-10: a release candidate's audio discs, as their track counts in order (a track
    marked video, on a DVD of the set, counts on none; a Discogs release's as its formats name
    them, _discogs_shape, when _release_cand read it)."""
    if cand.get("audio_shape") is not None:
        return list(cand["audio_shape"])
    count = {}
    for t in cand.get("tracks") or []:
        if t.get("video"):
            continue
        d = int(t.get("disc") or 1)
        count[d] = count.get(d, 0) + 1
    return [count[d] for d in sorted(count)]


def _discs_as(prop, discs: dict, total=None) -> bool:
    """DSC-10: every file of prop is tagged as the disc its folder is ({folder: disc}), each
    file its own track (and, total given, the release's audio discs are that many)."""
    seen = set()
    for f, t in zip(prop.files or [], prop.targets or []):
        num = str((t.get("discnumber") or [""])[0]).partition("/")[0].strip().lstrip("0")
        if num != str(discs.get(Path(f).parent, "")):
            return False
        if total is not None and str((t.get("totaldiscs") or [""])[0]).strip().lstrip("0") != str(total):
            return False
        track = (num, str((t.get("tracknumber") or [""])[0]).partition("/")[0].strip().lstrip("0"))
        if track in seen:
            return False
        seen.add(track)
    return len(prop.files or []) == len(prop.targets or []) and bool(seen)


def _release_words(p) -> str:
    """One disc's release, in words: "MusicBrainz release <id>", "Deezer \"<title>\"" ..."""
    album = ((p.targets or [{}])[0].get("album") or [""])[0]
    if str(p.provider).startswith("web:"):
        return f"{p.provider[4:]} \"{album}\""
    name = {"musicbrainz": "MusicBrainz", "discogs": "Discogs"}.get(str(p.provider).split("+")[0], p.provider)
    sg = getattr(p, "suggested", None) or {}
    if not p.release_id and sg.get("id"):                 # (TIE-1: album known, a pressing suggested)
        return f"album known \"{album}\", {name} release {sg['id']} suggested"
    return f"{name} release {p.release_id} \"{album}\""


def _web_cover_provider(like, staging):
    """artwork.WebCover with the limits of the profile's other cover sources (like)."""
    import artwork
    p = artwork.WebCover(staging)
    for k in ("limit", "min_px", "enlarge", "generate_mode"):
        if hasattr(like, k):
            setattr(p, k, getattr(like, k))
    return p


def web_album_title(title) -> str:
    """A web album's title without what names one version of it: "Purgatory Afterglow
    (Remaster 2024)" and "... (Remix 2024)" -> "Purgatory Afterglow"; "Until Eternity Ends -
    EP (Remix 2024)" -> "Until Eternity Ends - EP"."""
    import web_tag
    t = str(title or "")
    out = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", lambda m: "" if web_tag.version_title(m.group(0)) else m.group(0), t)
    out = re.sub(r"\s+-\s+[^-]*$", lambda m: "" if web_tag.version_title(m.group(0)) else m.group(0), out)
    return out.strip() or t.strip()


# Discogs format descriptions that name a version of the album (its sound, its track list)
DISCOGS_VERSIONS = {"remastered": "Remastered", "deluxe edition": "Deluxe Edition", "mono": "Mono",
                    "remixed": "Remixed", "special edition": "Special Edition"}


# what names a version of an album's sound or track list in a disambiguation, a title's
# brackets or a format's text ("remaster 2024", "Remix 2024", "deluxe edition", "mono"); a
# pressing's words ("Japanese edition", "bonus tracks", "limited edition of 500") do not
_VERSION_NAME = re.compile(r"\b(re-?master(ed)?|re-?mix(ed)?|rmx|mono|deluxe|expanded|anniversary|redux|"
                           r"re-?recorded|instrumentals?|acoustic|unplugged|demos?)\b", re.I)


def _version_parts(text) -> list:
    """The parts of a disambiguation or format text (split at commas and semicolons) that
    name a version: "Japanese edition, 2009 remaster, SHM-CD" -> ["2009 remaster"]."""
    return [p.strip() for p in re.split(r"[,;]", str(text or "")) if p.strip() and _VERSION_NAME.search(p)]


def _version_key(text) -> str:
    """The version a release's disambiguation, title mark or format text names, folded
    ("remaster 2024"; its pressing's words left out), or "" when it names none."""
    return mc.fold(" ".join(_version_parts(text)))


def _title_marks(title) -> str:
    """The version marks in a release title's brackets, folded ("Album (Remix 2024)": "remix 2024")."""
    return " ".join(k for k in (_version_key(m) for m in re.findall(r"[\(\[]([^\)\]]*)[\)\]]", str(title or ""))) if k)


def _mb_version(rel) -> tuple:
    """(key, text) of a MusicBrainz release's version: its disambiguation when it names one
    ("remaster 2024"), else the version marks in its title; ("", "") for none."""
    parts = _version_parts(rel.get("disambiguation"))
    if parts:
        return mc.fold(" ".join(parts)), ", ".join(parts)
    return _title_marks(rel.get("title")), ""


def _dg_format_key(fmt_text, title="") -> str:
    """A Discogs version's key from its format text ("CD, Album, Remastered" in a master's
    version list; the release's descriptions and free text) and its title."""
    parts = [x.strip() for x in str(fmt_text or "").split(",")]
    words = sorted({mc.fold(x) for x in parts if mc.fold(x) in DISCOGS_VERSIONS or _version_key(x)})
    return " ".join(words + ([_title_marks(title)] if _title_marks(title) else []))


def _named_version(part) -> bool:
    """A format text's part that is a version's own name ("Remix 2024", "2009 Remaster"),
    not a description as common as "Remastered" alone."""
    return mc.fold(part) not in DISCOGS_VERSIONS and bool(re.search(r"\d|re-?mix|rmx", part, re.I))


def _dg_version(det, text_only=False) -> tuple:
    """(key, text) of a Discogs release's version: the part of its format's free text naming
    a version ("Remix 2024"; "Digipak, 2009 Remaster" shows "2009 Remaster"), else (not
    text_only) the descriptions that do ("Remastered", "Mono", "Deluxe Edition"). text_only
    (a MusicBrainz release is tagged): only a version's own name counts, not "Remastered"."""
    fmts = det.get("formats") or []
    texts = [str(f.get("text") or "").strip() for f in fmts]
    descs = [str(d) for f in fmts for d in f.get("descriptions") or []]
    key = _dg_format_key(", ".join(descs + texts), det.get("title"))
    parts = [p for t in texts for p in _version_parts(t) if not text_only or _named_version(p)]
    shown = ", ".join(dict.fromkeys(parts))
    if not shown and not text_only:
        shown = ", ".join(dict.fromkeys(DISCOGS_VERSIONS[mc.fold(d)] for d in descs if mc.fold(d) in DISCOGS_VERSIONS))
    return (key, shown) if shown else ("", "")


def _mb_other_versions(http, rel, key) -> bool:
    """Another release of the MusicBrainz release group names another version (Edge Of
    Sanity's Remix 2024 beside its Remaster 2024). A plain release beside a remaster does
    not count: nearly every remastered album has one, and its title needs no mark."""
    rg = (rel.get("release-group") or {}).get("id")
    if not rg:
        return False
    # (the URL the matcher reads the group with: cached)
    data = http.get_json(f"https://musicbrainz.org/ws/2/release?release-group={rg}"
                         "&inc=media+labels&limit=100&fmt=json", "mb")
    if data is None:
        raise ma.ProviderUnavailable("musicbrainz", "invalid_response", "the release group could not be read")
    return any(_mb_version(r)[0] not in ("", key) for r in (data or {}).get("releases") or []
               if r.get("id") != rel.get("id"))


def _dg_other_versions(http, det, key) -> bool:
    """Another version of the Discogs master names another version (a plain one does not
    count, as for _mb_other_versions)."""
    mid = det.get("master_id")
    if not mid:
        return False
    data = http.get_json(f"https://api.discogs.com/masters/{mid}/versions?per_page=100", "discogs")
    if data is None:
        raise ma.ProviderUnavailable("discogs", "invalid_response", "the master's versions could not be read")
    return any(_dg_format_key(v.get("format"), v.get("title")) not in ("", key)
               for v in (data or {}).get("versions") or [] if str(v.get("id")) != str(det.get("id")))


def _shown_version(text, own_titles) -> str:
    """The version as your files spell it where they carry it ("Remaster 2024" from
    "Nothing But Death Remains (Remaster 2024)"), else the database's text, its words
    capitalised ("remaster 2024": "Remaster 2024")."""
    key = mc.fold(text)
    for t in own_titles:
        for m in re.findall(r"[\(\[]([^\)\]]*)[\)\]]", str(t or "")):
            if mc.fold(m) == key:
                return m.strip()
    return " ".join(w[:1].upper() + w[1:] for w in str(text).split())


def version_choices(profile) -> dict:
    """keep_version's style and tags as the profile's versions settings say."""
    if not hasattr(profile, "version_style"):
        return {}
    return {"style": profile.version_style(), "tags": profile.version_tags()}


def keep_version(http, targets, album, files, rel=None, det=None, style="paren", tags=True):
    """A release that is one version of its album (MusicBrainz's disambiguation "remaster
    2024", a title marked so, Discogs's format text "Remix 2024" or descriptions Remastered,
    Mono, Deluxe Edition; only the words naming the version, not the pressing's) keeps it
    visible when its release group (master) holds a release naming another version (a plain
    one does not count): the album is "Title (Version)" unless its title names a version
    already, ALBUMVERSION holds the version and
    RELEASECOMMENT MusicBrainz's disambiguation, so a media server shows two albums
    (Edge Of Sanity - Nothing But Death Remains, its Remaster 2024 and its Remix 2024, both
    titled plainly). A track title the files mark with that version keeps the mark where the
    release's title lacks it. Changes targets in place; returns the version shown, "" when the
    release keeps none, or None when the group (master) could not be read: unsure, the files
    keep the version marks they have (_kept_own_version). style: the version in the album
    title "(Version)" (paren), "[Version]" (bracket), or not in it (tags_only); tags: ALBUMVERSION
    and RELEASECOMMENT written (the version is always kept for naming, ~albumversion)."""
    key = text = comment = ""
    try:
        if rel:
            key, text = _mb_version(rel)
            if key and not _mb_other_versions(http, rel, key):
                key = ""
            comment = str(rel.get("disambiguation") or "").strip() if key else ""
        if not key and det:
            # (a MusicBrainz release its editors gave no version: only Discogs's own words for
            # one, "Remix 2024", not a description as common as Remastered)
            key, text = _dg_version(det, text_only=bool(rel))
            if key and not _dg_other_versions(http, det, key):
                key = ""
    except ma.ProviderUnavailable:
        _kept_own_version(targets, album, files)    # (the group could not be read: as the files have it)
        return None
    if not key or not text:
        return ""
    by_path = {t.get("path"): t for t in album.get("tracks") or []}
    own = [by_path.get(f) or {} for f in files]
    shown = _shown_version(text, [t.get("album") for t in own] + [album.get("folder") or ""])
    mark = mc.fold(shown)
    for i, t in enumerate(targets):
        name = (t.get("album") or [""])[0]
        # (a title that names its version already, "Master Of Puppets (Remastered)", gets no second)
        if name and mark not in mc.fold(name) and not _title_marks(name) and style != "tags_only":
            t["album"] = [f"{name} [{shown}]" if style == "bracket" else f"{name} ({shown})"] + list(t["album"][1:])
        if tags:
            t["albumversion"] = [shown]
        t["~albumversion"] = [shown]        # (for naming at every level: Basic writes no ALBUMVERSION)
        if comment and tags:
            t["releasecomment"] = [comment]
        mine = own[i].get("title") if i < len(own) else ""
        title = (t.get("title") or [""])[0]
        if title and mine and mark in mc.fold(mine) and mark not in mc.fold(title):
            spelled = _shown_version(shown, [mine])
            t["title"] = [f"{title} ({spelled})"] + list(t["title"][1:])
    return shown


def _marked_as(own, plain):
    """own ("Album (Remaster 2024)") when it is plain with a version mark added, else None."""
    m = re.match(r"^(.*?)\s*[\(\[]([^\)\]]*)[\)\]]\s*$", str(own or ""))
    if m and plain and mc.fold(m.group(1)) == mc.fold(plain) and _version_key(m.group(2)):
        return str(own)
    return None


def _kept_own_version(targets, album, files):
    """Whether the release is one version of several is not known (its group could not be
    read): an album or track title the files mark with a version ("Album (Remaster 2024)")
    keeps that mark, so a lookup failing never merges two versions an earlier run kept
    apart (ALBUMVERSION and RELEASECOMMENT stay as the files have them)."""
    by_path = {t.get("path"): t for t in album.get("tracks") or []}
    for i, t in enumerate(targets):
        own = (by_path.get(files[i]) or {}) if i < len(files) else {}
        # (REP-1: the album title as the files hold it, not as read before TagMender tagged them)
        mine_album = own.get("album") or album.get("title_written") or album.get("title")
        for field, mine in (("album", mine_album), ("title", own.get("title"))):
            plain = (t.get(field) or [""])[0]
            kept = _marked_as(mine, plain)
            if kept:
                t[field] = [kept] + list(t[field][1:])


def restored_folder(folder) -> bool:
    """The album folder (or, for one disc of a set, the folder above it) says its audio was
    restored or remastered."""
    d = Path(str(folder))
    names = [d.name] + ([d.parent.name] if set_dir(d) else [])
    return any(_RESTORED.search(n) for n in names)


def _own_album(path) -> bool:
    """The file has an album tag of its own (a standalone recording's album is then left as
    the file has it)."""
    import tagio
    try:
        return any(str(v).strip() for v in tagio.read(path).get("album") or [])
    except Exception:
        return True                     # (unread: never risk replacing an album it may have)


def _drop_isrcs(targets, folder, why) -> list:
    """targets without ISRCs when the folder is restored or remastered audio (recording IDs
    stay: the recording is the same performance); says so in why."""
    if not restored_folder(folder) or not any(t.get("isrc") for t in targets):
        return targets
    why.append("restored or remastered audio (folder name): ISRCs left out, they name another master")
    return [{k: v for k, v in t.items() if k != "isrc"} for t in targets]


def _partial_kept(k: str) -> bool:
    """A field a Partial album may carry: levels.PARTIAL_FIELDS (the widest level), or one of
    Picard's hidden variables that names no pressing."""
    import levels
    if k.startswith("~"):
        return k in ALBUM_ONLY_HIDDEN and k != "~remove"       # (a Partial album deletes nothing)
    return levels.album_field(k) and k not in levels.ALWAYS


def _alternatives(out, best, margin, scores=False) -> list:
    """(source, id) - with scores, (source, id, score) - of the other candidates the matcher
    scored within `margin` points of the best (from its "alts" list)."""
    found = []
    top = float(best.get("confidence") or 0)
    for a in out.get("alts") or []:
        m = _ALT.match(str(a))
        if m and top - float(m.group(3)) < margin and m.group(2) != str(best.get("id")):
            found.append((m.group(1), m.group(2), float(m.group(3))) if scores else (m.group(1), m.group(2)))
    return found


MIXED_WORDS = "two versions of the album are mixed"     # (the reason of a folder holding two: _mixed)
_ALT_WORDS = re.compile(r"^(mb|discogs):(\S+) ([\d.]+)\s*(.*)$")


def release_url(source, rid) -> str:
    """The page of a MusicBrainz or Discogs release."""
    return (f"https://musicbrainz.org/release/{rid}" if source in ("mb", "musicbrainz")
            else f"https://www.discogs.com/release/{rid}")


def suggestion_of(guess, best) -> dict:
    """TIE-1: the plan's account of a pressing a tie-break proposed (match_core.tie_guess):
    {"source", "id", "url", "ref", "label", "country", "formats", "year", "catnos", "barcodes",
    "by" (the tie-break, in words), "why" (the sentence the plan and the GUI show), "others"
    (the tied pressings it was chosen over, each with the same facts)}. "ref" is what forcing the
    release takes (Change Release, `tagmender plan PATH --release REF`): the pick's own release;
    a Discogs pick that MusicBrainz lists as the same pressing (its twin) is the pair
    "discogs:<ID>+<MBID>" (release_twin), so its tags come from both databases, as the matcher's
    pick would."""
    pick = dict(guess.get("pick") or {})
    others = [dict(x) for x in guess.get("others") or ()]
    for x in [pick] + others:
        x["source"] = "musicbrainz" if x.get("source") == "mb" else x.get("source") or ""
        x["url"] = x.get("url") or release_url(x["source"], x.get("id"))
    if pick.get("source") == "discogs" and (best or {}).get("mb_id"):
        pick["ref"] = f"discogs:{pick.get('id')}+{best['mb_id']}"
    by = re.sub(r";\s*nothing about your copy tells the pressings apart\s*$", "", str(guess.get("by") or ""))
    n = 1 + len(others)
    pick["by"] = by
    pick["why"] = (f"pressing suggested ({by}), not proven: nothing of your copy tells these {n} pressings apart; confirm "
                   "it to write its barcode, catalogue number and label (Album > Use Suggested Pressing, or "
                   f"tagmender plan PATH --release {pick.get('ref')}) (TIE-1)")
    pick["others"] = others
    return pick


def candidates_of(out, limit=5, prefer="") -> list:
    """The releases the matcher scored best for an album, best first (its pick, then its
    "alts"), as the run report suggests them: [{"source": "musicbrainz" | "discogs", "id",
    "url", "ref" (what plan --release takes), "score", "title", "lengths"}]. "lengths" is
    False when the pick's release lists no track lengths, None when not known; "details" its
    format, country and catalogue number. A release the folder name rules out is not offered,
    unless none fits. prefer ("mb" or "discogs"): that database's releases first, after the pick."""
    if not isinstance(out, dict):
        return []
    found, seen = [], set()

    choices = [c for c in out.get("choices") or () if isinstance(c, dict)]
    details = {(c["source"], c["id"]): c.get("details") or "" for c in choices}

    def add(src, rid, score, title, lengths=None):
        key = (src, str(rid))
        if not rid or key in seen or src not in ("mb", "discogs"):
            return
        seen.add(key)
        found.append({"source": "musicbrainz" if src == "mb" else "discogs", "id": str(rid),
                      "url": release_url(src, rid), "ref": str(rid) if src == "mb" else f"discogs:{rid}",
                      "score": round(float(score or 0), 1), "title": title, "lengths": lengths,
                      "details": details.get(key, "")})
    best = out.get("best") or {}
    if best.get("id"):
        title = " - ".join(x for x in (best.get("artist"), best.get("title")) if x) + \
            (f" ({best['year']})" if best.get("year") else "")
        notes = " ".join(map(str, best.get("notes") or []))
        add(best.get("source"), best.get("id"), best.get("confidence"), title,
            False if "durations unavailable" in notes else None)
    alts = []
    if choices:                                      # (a result saved by an older matcher has none)
        offered = [c for c in choices if not c.get("against_folder")] or choices
        alts = [(float(c.get("score") or 0), c["source"], c["id"], c.get("title") or "") for c in offered]
    for a in (out.get("alts") or []) if not choices else ():
        m = _ALT_WORDS.match(str(a))
        if m:
            alts.append((float(m.group(3)), m.group(1), m.group(2), m.group(4).strip()))
    for score, src, rid, title in sorted(alts, key=lambda x: (x[1] != prefer if prefer else False, -x[0])):
        add(src, rid, score, title)
    return found[:limit]


def _pool(out, best):
    """[(source, id, score)] of every candidate the matcher scored (its "pool"), best score
    first, the pick among them; None for a result saved without one (an older matcher)."""
    if not isinstance(out.get("pool"), list):
        return None
    got = {}
    for e in out["pool"]:
        m = _ALT.match(str(e))
        if m:
            key = (m.group(1), m.group(2))
            got[key] = max(got.get(key, 0.0), float(m.group(3)))
    key = (best.get("source"), str(best.get("id")))
    if key[0] in ("mb", "discogs") and key not in got:
        got[key] = float(best.get("confidence") or 0)
    return sorted(((s, i, c) for (s, i), c in got.items()), key=lambda x: -x[2])


def _lcs(a, b, eq) -> list:
    """The longest in-order run of (i, j) with eq(a[i], b[j])."""
    n, m = len(a), len(b)
    t = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            t[i][j] = t[i + 1][j + 1] + 1 if eq(a[i], b[j]) else max(t[i + 1][j], t[i][j + 1])
    out, i, j = [], 0, 0
    while i < n and j < m:
        if eq(a[i], b[j]) and t[i][j] == t[i + 1][j + 1] + 1:
            out.append((i, j))
            i, j = i + 1, j + 1
        elif t[i + 1][j] >= t[i][j + 1]:
            i += 1
        else:
            j += 1
    return out


def _titles_match(files, ct, pairs, cfg) -> bool:
    """A Partial match by titles (restored or remastered audio whose lengths moved): the map
    covers the shorter list, every mapped title agrees, at least 5 tracks, at least half the
    mapped lengths agree and the whole mapped running time is within 2%."""
    if len(pairs) < 5 or len(pairs) < min(len(files), len(ct)) or not all(files[i].get("title") for i, _j in pairs):
        return False
    if not all(mc.track_titles_agree(files[i]["title"], ct[j].get("title")) for i, j in pairs):
        return False
    if not all((ct[j].get("secs") or 0) > 0 for _i, j in pairs):
        return False
    ok = sum(1 for i, j in pairs if mc.duration_ok(files[i]["secs"], ct[j]["secs"], cfg))
    mine, theirs = sum(files[i]["secs"] for i, _j in pairs), sum(ct[j]["secs"] for _i, j in pairs)
    return ok / len(pairs) >= 0.5 and theirs > 0 and abs(mine - theirs) <= 0.02 * theirs


def _same_tracks(a, b, cfg) -> bool:
    """Two releases' track lists are the same album's: the same count, every title the same
    (folded) and every length within the matcher's tolerance."""
    return len(a) == len(b) and bool(a) and all(
        mc.fold(x.get("title")) == mc.fold(y.get("title")) and (x.get("secs") or 0) > 0 and (y.get("secs") or 0) > 0
        and mc.duration_ok(x["secs"], y["secs"], cfg) for x, y in zip(a, b))


def _alternatives_hidden(out, best, margin) -> list:
    """The providers whose list in the matcher's "alts" is full (it keeps 3 each) with its last
    one still within `margin` points of the best: a candidate after it is not seen."""
    top = float(best.get("confidence") or 0)
    by = {}
    for a in out.get("alts") or []:
        m = _ALT.match(str(a))
        if m:
            by.setdefault(m.group(1), []).append(float(m.group(3)))
    names = {"mb": "MusicBrainz", "discogs": "Discogs"}
    return [names.get(src, src) for src, scores in by.items() if len(scores) >= 3 and top - min(scores) < margin]


def _provider_failed(errors) -> bool:
    """MusicBrainz or Discogs could not answer for this album (not a clue's lookup): it is
    retried (watcher.py's provider_error), not given Partial tags. A missing Discogs token is
    not this: retrying changes nothing until you add one, and Discogs then counts as off."""
    return any(k not in CLUE_HOSTS and v in ("outage", "rate_limited", "invalid_response")
               for k, v in (errors or {}).items())


def _dg_tracks(tracklist, parent_position=None) -> list:
    """A Discogs tracklist's audio entries in ma.flatten_tracklist's order, with each one's own
    artists (a split album's or a compilation's)."""
    out = []
    if parent_position is None:
        tracklist = ma.one_track_movements(tracklist)        # TRK-1
    for e in tracklist or []:
        if not isinstance(e, dict):
            continue
        kind = str(e.get("type_") or e.get("type") or "track").strip().casefold()
        if kind == "heading":
            continue
        if kind == "index":
            subs = _dg_tracks(e.get("sub_tracks"), parent_position=e.get("position") or "")
            if subs:
                out.extend(subs)
                continue
        pos = parent_position if parent_position is not None else (e.get("position") or "")
        if parent_position == "" and ma._HYPHEN_DISC.match(str(e.get("position") or "")):
            pos = str(e.get("position"))                   # (TRK-3: "2-3" of a work with no position)
        out.append({"position": pos, "title": e.get("title", ""), "artists": e.get("artists") or [],
                    "duration": e.get("duration") or ""})
    return out


def _duration_secs(text) -> float:
    """A Discogs duration ("2:33", "1:02:10") in seconds; 0 when it is none."""
    try:
        parts = [int(x) for x in str(text or "").strip().split(":")]
    except ValueError:
        return 0.0
    secs = 0
    for x in parts:
        secs = secs * 60 + x
    return float(secs) if len(parts) >= 2 else 0.0


def release_secs(rel=None, det=None) -> list:
    """[(title, seconds)] of a release's audio tracks, MusicBrainz's (rel) or else Discogs's
    (det); a length not listed is 0."""
    if rel:
        return [(t.get("title") or (t.get("recording") or {}).get("title") or "",
                 float(t.get("length") or (t.get("recording") or {}).get("length") or 0) / 1000.0)
                for t in _mb_audio_tracks(rel)]
    if det:
        return [(t["title"], _duration_secs(t.get("duration"))) for t in _dg_tracks(det.get("tracklist"))]
    return []


def _mb_audio_tracks(rel) -> list:
    """A release's audio tracks in the order mb_tags.release_tags writes them."""
    return [t for m in mb_tags.audio_media(rel) for t in m.get("tracks") or []
            if not (t.get("recording") or {}).get("video")]


def _mb_group(http, rg_id, layer="cd"):
    """Every release of a MusicBrainz release group, with each one's tracks (recording, title,
    artist folded; name and length in seconds), disc layout, title and credit (folded): {"ids", "pressings", "whole"} - whole when the one page (100) read
    holds them all; None when it could not be read. layer: which layer of a hybrid SACD listed
    as media of their own each pressing's disc is (MED-22, mb_tags.one_layer)."""
    data = http.get_json(f"https://musicbrainz.org/ws/2/release?release-group={rg_id}"
                         "&inc=recordings+artist-credits+media&limit=100&fmt=json", "mb")
    if not data or not isinstance(data.get("releases"), list):
        return None
    pressings = []
    for r in data["releases"]:
        r = mb_tags.one_layer(r, layer)  # (MED-22: a hybrid SACD's layers one disc, the pick's layer)
        tracks = [{"recording": (t.get("recording") or {}).get("id") or "",
                   "title": mc.fold(t.get("title") or ""), "artist": mc.fold(ma.mb_credit(t) or ma.mb_credit(r)),
                   "name": t.get("title") or "", "secs": (t.get("length") or 0) / 1000.0}
                  for t in _mb_audio_tracks(r)]
        # (the tracks of each audio disc, and every medium: a DVD beside the CD changes the numbers)
        layout = (tuple(len([t for t in m.get("tracks") or [] if not (t.get("recording") or {}).get("video")])
                        for m in mb_tags.audio_media(r)), len(r.get("media") or []))
        pressings.append({"id": r.get("id"), "tracks": tracks, "layout": layout,
                          "title": mc.fold(r.get("title") or ""), "credit": mc.fold(ma.mb_credit(r))})
    count = data.get("release-count")
    whole = isinstance(count, int) and count <= len(data["releases"])
    return {"ids": {p["id"] for p in pressings}, "pressings": pressings, "whole": whole}


def _discogs_versions(http, master_id):
    """The release IDs of a Discogs master's versions (None when not all were read: one page of 100)."""
    data = http.get_json(f"https://api.discogs.com/masters/{master_id}/versions?per_page=100", "discogs")
    if not data or not isinstance(data.get("versions"), list):
        return None
    if int((data.get("pagination") or {}).get("pages") or 1) > 1:
        return None
    return {str(v.get("id")) for v in data["versions"]}


# Hosts that only add clues: their failures are told once for the run, never an album's
# reason code ("ctdb_unavailable" would send an album to retry for a missing clue)
CLUE_HOSTS = ("ocr", "gnudb", "acoustid", "ctdb")


outage_notes = ma.outage_notes      # {host: note} for hosts whose every call failed this run


ALBUM_ONLY_HIDDEN = {"~remove", "~multiartist", "~absolutetracknumber", "~totalalbumtracks", "~albumartists",
                     "~albumartist_credit",
                     "~albumartistcomment", "~artists", "~artistcomment", "~artists_sort", "~artists_comments",
                     "~artists_countries", "~recordingtitle", "~recordingcomment", "~length", "~video", "~datatrack",
                     "~discpregap", "~silence", "~releasegroup", "~releasegroupcomment",
                     "~releasegroup_firstreleasedate", "~primaryreleasetype", "~secondaryreleasetype",
                     "~recording_firstreleasedate", "~iswc", "~workcomment", "~lyricistsort", "~writersort",
                     "~performance_attributes", "~discogs_albumartist_id", "~discogs_albumartists"}


class TaggerScriptError(Exception):
    """A tagger script that fails on a file (a function given the wrong arguments ...)."""


def run_tagger_scripts(scripts, tags: dict, hidden: dict, log=None, strip=True):
    """(tags, removed): the tags after Picard's tagger scripts, each in turn, with Picard's
    hidden variables (%_releasecomment%, %_totalalbumtracks% ...) to read. strip: whitespace
    stripped after each script, as Picard does on a track (picard/album.py) - not on a file
    (Track.update_file_metadata). hidden is updated with the hidden variables as the scripts
    leave them ($set(_shelf,...)), which the file naming script reads in Picard. A script
    that does not parse is skipped, as Picard skips it; one that fails while it runs raises
    TaggerScriptError."""
    import tagscript as ps
    m = ps.Metadata()
    for k, v in list(hidden.items()) + list(tags.items()):
        m[k] = v if isinstance(v, list) else [v]
    for text in scripts:
        try:
            ps.ScriptParser().eval(text, m)
        except ps.ScriptError as exc:
            if log is not None:
                log.warning("tagger script failed: %s", exc)
        except (TypeError, ValueError, OverflowError, MemoryError, RecursionError, IndexError, KeyError,
                ZeroDivisionError, AttributeError) as exc:
            raise TaggerScriptError(f"{type(exc).__name__}: {exc}") from None
        if strip:
            for k, vals in list(m.rawitems()):
                m[k] = [str(x).strip() for x in vals]
    out = {k: list(v) for k, v in m.rawitems() if not k.startswith("~")}
    gone = sorted(k for k in m.deleted_tags if not k.startswith("~") and k not in out)
    hidden.clear()
    hidden.update({k: list(v) for k, v in m.rawitems() if k.startswith("~") and k != "~remove"})
    return out, gone


_MBID = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.IGNORECASE)


def plan_entry_files(a: dict) -> list:
    """The album's own files, from its plan entry (for a release the user chooses for it): a
    copy profile's earlier copy names the file it was copied from."""
    return sorted({fe.get("copy_of") or fe["src"] for fe in a.get("files") or [] if not fe.get("joined")} |
                  {x.get("file") for x in a.get("skipped") or [] if x.get("file")} |
                  set(a.get("album_files") or []) | set(a.get("excluded_files") or []))


def release_twin(ref: str):
    """TIE-1: ("mb", MBID) of MusicBrainz's entry of the same pressing a Discogs ref pairs with it
    ("discogs:123+<mbid>": the matcher's pick with its twin, as Use Suggested Pressing confirms it),
    else None."""
    ref = str(ref or "")
    if "+" not in ref:
        return None
    first, second = (x.strip() for x in ref.split("+", 1))
    one, two = parse_release_ref(first), parse_release_ref(second)
    return two if one[0] == "discogs" and two[0] == "mb" else None


def parse_release_ref(ref: str):
    """("mb", MBID) or ("discogs", "123") for a MusicBrainz or Discogs release link or ID
    ("https://musicbrainz.org/release/<mbid>", a bare MBID, "https://www.discogs.com/release/123-...",
    "discogs:123", "[r123]"); (None, "") otherwise. A pair "discogs:123+<mbid>" (TIE-1's suggestion of
    a Discogs pressing and MusicBrainz's entry of it: release_twin) is its first release."""
    ref = str(ref or "").strip()
    if "+" in ref:
        ref = ref.split("+", 1)[0].strip()
    m = re.search(r"musicbrainz\.org/release/" + _MBID.pattern, ref, re.IGNORECASE) or \
        (re.fullmatch(_MBID.pattern, ref, re.IGNORECASE) and re.match(_MBID.pattern, ref, re.IGNORECASE))
    if m:
        return "mb", m.group(1).lower()
    m = re.search(r"discogs\.com/(?:[^/?#]+/)?release/(\d+)", ref, re.IGNORECASE) or \
        re.fullmatch(r"(?:discogs:|\[?r)(\d+)\]?", ref, re.IGNORECASE)
    if m:
        return "discogs", m.group(1)
    return None, ""


# --------------------------------------------------------------------------
# Discogs release -> tags, with the Discogs Enrichment plugin's own code
# --------------------------------------------------------------------------
def _flatten_raw(tracklist, parent_position=None):
    """match_albums.flatten_tracklist, keeping each entry's own fields (artists...)."""
    out = []
    if parent_position is None:
        tracklist = ma.one_track_movements(tracklist)        # TRK-1
    for e in tracklist or []:
        if not isinstance(e, dict):
            continue
        kind = str(e.get("type_") or e.get("type") or "track").strip().casefold()
        if kind == "heading":
            continue
        if kind == "index":
            subs = _flatten_raw(e.get("sub_tracks"), parent_position=e.get("position") or "")
            if subs:
                for k, sub in enumerate(subs, 1):     # (CLS-4: the work its movements are of)
                    sub.setdefault("~index_work", (str(e.get("title") or "").strip(), k, len(subs)))
                out.extend(subs)
                continue
        d = dict(e)
        if parent_position is not None:
            d["~own_position"] = e.get("position") or ""   # (a movement's own: CRD-1 credits can name it)
        d["position"] = parent_position if parent_position is not None else (e.get("position") or "")
        if parent_position == "" and ma._HYPHEN_DISC.match(str(e.get("position") or "")):
            d["position"] = str(e.get("position"))         # (TRK-3)
        out.append(d)
    return out


class Meta:
    """The little of Picard's Metadata the plugin's merge code uses, over a tag dict."""

    def __init__(self, tags: dict):
        self.d = {k: [str(x) for x in (v if isinstance(v, list) else [v])] for k, v in tags.items()}

    def getall(self, k):
        return list(self.d.get(k, []))

    def get(self, k, default=""):
        v = self.d.get(k)
        return v[0] if v else default

    def __setitem__(self, k, v):
        self.d[k] = [str(x) for x in v] if isinstance(v, (list, tuple)) else [str(v)]

    def __contains__(self, k):
        return k in self.d

    def delete(self, k):
        self.d.pop(k, None)

    def keys(self):
        return list(self.d)


def shape_fields(fields: dict, s: dict, dm) -> dict:
    """The plugin's _shape_fields: style routing, then the field groups switched off and the
    protected tags dropped; notes only when imported."""
    fields = dm.apply_style_mode(dict(fields), s["style_mode"] or "separate")
    fields = dm.filter_fields(fields, marketplace=bool(s["marketplace"]), performers=bool(s["performers"]),
                              companies=bool(s["companies"]), identifiers=bool(s["identifiers"]),
                              protected=dm.parse_protected_tags(s["protected_tags"]))
    if not s["import_notes"]:
        fields.pop("discogs_notes", None)
    return fields


def _dg_artist_key(entry, dm) -> str:
    """A Discogs artist's name compared without case or spacing (and without Discogs'
    "(2)" suffix)."""
    name = dm.clean_artist_name(entry.get("name")) if dm is not None else entry.get("name")
    return " ".join(unicodedata.normalize("NFKC", str(name or "")).casefold().split())


def _discogs_repeat(kept, entry, dm) -> bool:
    """Whether a Discogs artist entry names an artist already in `kept`: the same artist ID,
    or (where either has no ID) the same name. Same-named artists with their own IDs stay."""
    for k in kept:
        if entry.get("id") and k.get("id"):
            if str(entry["id"]) == str(k["id"]):
                return True
        elif _dg_artist_key(entry, dm) and _dg_artist_key(entry, dm) == _dg_artist_key(k, dm):
            return True
    return False


def dedupe_discogs_artists(artists, dm=None) -> list:
    """A Discogs credit with each artist once. Discogs credits one artist twice for a name in
    two scripts ("Mekong Delta = メコン・デルタ", both entries the same artist): the first
    credit is kept, with the join that followed the repeat."""
    kept = []
    for entry in artists or []:
        if not isinstance(entry, dict):
            continue
        if _discogs_repeat(kept, entry, dm):
            if kept:
                kept[-1] = dict(kept[-1], join=entry.get("join") or "")
            continue
        kept.append(entry)
    return kept


def _dedupe_tracklist(tracklist, dm):
    out = []
    for e in tracklist or []:
        if isinstance(e, dict) and (e.get("artists") or e.get("sub_tracks")):
            e = dict(e)
            if e.get("artists"):
                e["artists"] = dedupe_discogs_artists(e["artists"], dm)
            if e.get("sub_tracks"):
                e["sub_tracks"] = _dedupe_tracklist(e["sub_tracks"], dm)
        out.append(e)
    return out


def discogs_without_repeats(doc, dm=None):
    """A Discogs release or master (a copy) whose release and track credits name each artist
    once (dedupe_discogs_artists)."""
    if not isinstance(doc, dict):
        return doc
    out = dict(doc)
    if doc.get("artists"):
        out["artists"] = dedupe_discogs_artists(doc["artists"], dm)
        if len(out["artists"]) < len(doc["artists"]) and doc.get("artists_sort") and dm is not None:
            # Discogs' sort credit repeats the artist too ("Mekong Delta = Mekong Delta")
            out["artists_sort"] = dm.format_artist_credit([dict(a, anv="") for a in out["artists"]])
    if doc.get("tracklist"):
        out["tracklist"] = _dedupe_tracklist(doc["tracklist"], dm)
    return out


_BARCODE_TEXT = re.compile(r"[0-9][0-9 .\-]*[0-9]")


def barcode_digits(values):
    """BAR-10: a barcode is written as its digits ("72435-20811-27", "7 24352 08112 7" ->
    "724352081127"), the form a scanner reads and Discogs's and MusicBrainz's barcode searches
    find exactly (the spaced form matches loosely). Only a value of digits, spaces, dashes and
    dots whose digits make an EAN-8, UPC-A, EAN-13 or GTIN-14 length is rewritten; anything else
    ("none", a note) stays as it is. Repeats are written once."""
    one = isinstance(values, str)
    out = []
    for v in ([values] if one else list(values or [])):
        v = str(v).strip()
        digits = re.sub(r"\D", "", v)
        if _BARCODE_TEXT.fullmatch(v) and len(digits) in (8, 12, 13, 14):
            v = digits
        if v and v not in out:
            out.append(v)
    return (out[0] if out else "") if one and len(out) <= 1 else out


def _credit_scope(det, dm):
    """CRD-1: (the release's album-wide credits, [(credit, the normalized positions it names)]
    for its credits that name their tracks). A credit whose track list cannot be matched to the
    release's positions is left out - never spread to every track. None where the plugin cannot
    read track lists (it then writes its credits as it always has)."""
    if not hasattr(dm, "split_scoped_credits") or not isinstance(det, dict):
        return None
    wide, scoped = dm.split_scoped_credits(det.get("extraartists") or [])
    positions = dm.release_positions(det.get("tracklist"))
    named, video = [], []
    for credit in scoped:
        if not str(credit.get("tracks") or "").strip():
            # CRD-1b: a role naming a video disc, no tracks listed: that disc's tracks only
            kind = dm.video_disc_of_role(credit.get("role")) if hasattr(dm, "video_disc_of_role") else ""
            if kind:
                video.append((credit, kind))
            continue
        names = dm.credit_track_positions(credit.get("tracks"), positions)
        if names:
            named.append((credit, set(names)))
    return wide, named, video


# CRD-1b: the Discogs format names of each kind of video disc a role can name
_VIDEO_DISCS = {"blu-ray": ("blu-ray", "blu-ray-r", "bd"),
                "dvd": ("dvd", "dvdr", "dvd-r", "hd dvd", "hd dvd-r")}


_VIDEO_POS_KIND = re.compile(r"^\s*(?:(bd|blu-?ray|br)|((?:hd-?)?dvd))", re.I)


def _video_kind_of(medium, position="", det=None) -> set:
    """CRD-1b: the kinds of video disc a track is on: {"blu-ray", "video"}, {"dvd", "video"},
    {"video"} or none - from its position where that names a video disc of the release
    ("BD1-3", "DVD2-1", "BR-4": MED-16), else from its medium ("Blu-ray, Album, Stereo")."""
    if position and isinstance(det, dict) and ma._video_medium(position, det):
        m = _VIDEO_POS_KIND.match(str(position))
        return {"blu-ray" if m and m.group(1) else "dvd" if m and m.group(2) else "video", "video"}
    name = mc.fold(str(medium or "").split(",")[0])
    for kind, names in _VIDEO_DISCS.items():
        if name in {mc.fold(n) for n in names}:
            return {kind, "video"}
    return {"video"} if name in ("vhs", "laserdisc", "umd", "betamax", "video", "cdv", "vcd", "svcd") else set()


def _track_credits(entry, scope, s, dm, medium="", det=None) -> dict:
    """CRD-1: the credits naming this Discogs track (its position, its position within its
    work, or one of its movements': "CD6-3a" names the work "CD6-3"), and CRD-1b the video
    roles of its own disc's kind (_video_kind_of: its position on DET, or MEDIUM, the track's
    format "Blu-ray, ..."), each tag with the album-wide credits of that tag beside them, shaped
    as the album's fields are. {} when none names it."""
    if not scope or not (scope[1] or (len(scope) > 2 and scope[2])):
        return {}
    keys = {dm.normalize_position(entry.get(k)) for k in ("position", "~own_position")} - {""}
    mine = [c for c, names in scope[1]
            if keys & names or any(ma._movement_of(n, k) for n in names for k in keys)] if keys else []
    kinds = _video_kind_of(medium, entry.get("position"), det)
    mine += [c for c, kind in (scope[2] if len(scope) > 2 else ()) if kind in kinds]
    if not mine:
        return {}
    combined = dm.extract_credits(list(scope[0]) + mine)
    own = {k: combined[k] for k in dm.extract_credits(mine) if k in combined}
    return shape_fields(own, s, dm)


def credits_read(tags_mb, ps, dparts, det, es) -> dict:
    """REP-2: {"musicbrainz": bool, "discogs": bool, "discogs_performers": bool, "protected": [..]}:
    whether each database's credits were read whole and placed per track for these targets
    (a plan takes back only credits a database read so no longer gives a file)."""
    ps, es = ps or {}, es or {}
    mb = bool(tags_mb) and bool(ps.get("release_ars")) and bool(ps.get("track_ars"))
    dg = bool(dparts) and dparts[1] is not None and isinstance(det, dict) and bool(det.get("extraartists"))
    protected = sorted(p.lower() for p in re.split(r"[,\s]+", str(es.get("protected_tags") or "")) if p)
    return {"musicbrainz": mb, "discogs": dg, "discogs_performers": bool(es.get("performers", True)),
            "protected": protected}


def with_track_credits(album: dict, track) -> dict:
    """CRD-1: a Discogs release's album fields with one track's own scoped credits (from
    discogs_parts) in them - the fields that track is tagged with."""
    extra = (track or {}).get("~credits") if isinstance(track, dict) else None
    return {**album, **extra} if extra else album


def _unheld_digital_media(rel, slots):
    """MED-20: positions of a MusicBrainz release's download media ("Digital Media") that come
    after its last physical medium, when the release has physical media and none of the
    tracks in SLOTS (the release's entries your files are) is on them; else an empty set."""
    media = rel.get("media") or []
    kinds = {int(m.get("position") or d): mc.media_kind(m.get("format")) for d, m in enumerate(media, 1)}
    video = {int(m.get("position") or d) for d, m in enumerate(media, 1)
             if str(m.get("format") or "").casefold() in mb_tags.VIDEO_FORMATS}
    # (the physical audio discs: a video disc after them is MED-17's, left out of the count too)
    physical = [p for p, k in kinds.items() if k != "digital" and p not in video]
    digital = {p for p, k in kinds.items() if k == "digital"}
    if not physical or not digital or any(p < max(physical) for p in digital):
        return set()
    keys = mb_tags.track_keys(rel)
    held = {keys[k][0] for k in slots if k < len(keys)}
    return set() if held & digital else digital


def _disc_layer(album, tracks) -> str:
    """MED-22: which layer of a hybrid SACD one disc of your copy is: "cd" for CD-quality audio
    (16-bit/44.1 kHz: only the CD layer holds that), and for a disc whose files mix the two;
    else the SACD layer of its files' channel layout ("multichannel" when a file has more than
    two channels or names its multichannel area, else "stereo"). Files that carry no audio
    facts: the copy's (album "audio")."""
    facts = [t.get("audio") for t in tracks if t.get("audio")]
    if facts and len(facts) == len(tracks):
        hi = [(a.get("bits") or 0) > 16 or (a.get("rate") or 0) > 48000 or bool(a.get("sacd_area")) for a in facts]
        if not all(hi):
            return "cd"
    else:
        audio = album.get("audio") or {}
        if not (audio.get("hires") or audio.get("sacd")):
            return "cd"
        facts = tracks
    lays = {mc.layout_of_channels(a.get("channels"), a.get("sacd_area")) for a in facts}
    return "multichannel" if "multichannel" in lays else "stereo"


def _layer_prefer(album, rel, box=None) -> dict:
    """MED-22: {disc of the release, as mb_tags.one_layer numbers them: the layer your copy's
    disc is (_disc_layer)}. Disc by disc where your files' discs are the release's audio discs
    (your disc n its n-th): a rip of disc 1's CD layer and disc 2's SACD layer takes each from
    its own. Otherwise (one folder of every disc, no disc tags) every disc takes your copy's
    layer as a whole; one disc of the set in a folder of its own (box: its number among the
    audio discs) likewise."""
    groups = mb_tags.layer_groups(rel)
    audio = [k for k, g in enumerate(groups, 1)
             if str(g[0].get("format") or "").casefold() not in mb_tags.VIDEO_FORMATS]
    tracks = album.get("tracks") or []
    discs = sorted({int(t.get("disc") or 1) for t in tracks})
    whole = _disc_layer(album, tracks)
    out = {k: whole for k in audio}
    if not box and discs == list(range(1, len(audio) + 1)):
        for d in discs:
            out[audio[d - 1]] = _disc_layer(album, [t for t in tracks if int(t.get("disc") or 1) == d])
    return out


_LAYER_FOLDER = re.compile(r"\((SACD(?: Stereo| Multichannel)?)\)\s*$", re.I)


def _layer_copies(files, metas):
    """MED-22c: an album folder holding a disc's CD-layer copy beside copies of its SACD layers
    ("Disc 01", "Disc 01 (SACD Stereo)", "Disc 01 (SACD Multichannel)", "Disc 02"; or, as an
    earlier job filed them, "Disc 01", "Disc 02" and "Disc 03" the SACD layers, "Disc 04"): [the
    files of each copy] - the CD layer's with the discs of no layer (the bonus CD) - when a
    disc is there twice: an SACD layer's files numbered as a disc the others hold, or with that
    disc's titles. Each file's layer from its media tag (as TagMender wrote it), its SACD_AREA
    tag, else its folder's "(SACD Stereo)". None for any other album (version folders "2.0/"
    and "5.1/", a copy of disc 1's CD layer and disc 2's SACD layer)."""
    bucket = {}
    for f in files:
        m = metas.get(f) or {}
        label = mb_tags.layer_label((m.get("media") or [""])[0])
        if not label:
            lay = mc.layout_of_channels(0, (m.get("sacd_area") or [""])[0])
            label = {"stereo": "SACD Stereo", "multichannel": "SACD Multichannel"}.get(lay, "")
        if not label:
            got = _LAYER_FOLDER.search(Path(f).parent.name)
            label = got.group(1) if got else ""
        bucket.setdefault(label.casefold(), []).append(f)
    if len(bucket) < 2 or "" not in bucket:
        return None
    where = {}
    for k, fs in bucket.items():
        for f in fs:
            where.setdefault(Path(f).parent, set()).add(k)
    if any(len(v) > 1 for v in where.values()) or any(mc.parse_version(d.name) for d in where):
        return None                              # (a folder of two layers; version folders: one album)

    def discs(fs):
        """{disc number: (its titles, whether a media tag names a hybrid SACD's layer)}"""
        out = {}
        for f in fs:
            m = metas.get(f) or {}
            try:
                n = int(str((m.get("discnumber") or ["1"])[0]).partition("/")[0] or 1)
            except ValueError:
                n = 1
            fam, kind = mb_tags._layer_kind((m.get("media") or [""])[0])
            got = out.setdefault(n, (set(), [False]))
            got[0].add(mc.fold((m.get("title") or [Path(f).stem])[0]))
            got[1][0] = got[1][0] or (bool(kind) and "hybrid" in fam) or Path(f).parent in unsure
        return out
    # MED-22c: a folder of no layer whose audio is no CD's (hi-res, SACD_AREA naming none) is a
    # layer nothing tells: held on its own, said why - never taken for the CD layer's copy
    unsure = {d for d in where if "" in where[d] and _hires_folder([f for f in bucket[""] if Path(f).parent == d])}
    cd = discs(bucket[""])
    # (the same titles only where a media tag names a hybrid SACD's layer, or the folder of no
    # layer is hi-res: a CD and an SACD of one album, two discs of their own, are never one disc
    # twice)
    twice = any(n in cd or (len(t) > 1 and any(t == c[0] and (hy[0] or c[1][0]) for c in cd.values()))
                for k, fs in bucket.items() if k for n, (t, hy) in discs(fs).items())
    if not twice:
        return None
    out = [(bucket[k], "") for k in sorted(bucket) if k]
    plain = [f for f in bucket[""] if Path(f).parent not in unsure]
    lost = "which SACD layer this disc is cannot be told (no SACD area or media tag names it)"
    out += [([f for f in bucket[""] if Path(f).parent == d], lost) for d in sorted(unsure)]
    if plain:
        out.insert(0, (plain, ""))
    return out


def _hires_folder(files) -> bool:
    """Every one of these files holds audio no CD holds (past 16-bit or 48 kHz)."""
    import mutagen
    if not files:
        return False
    for f in files:
        try:
            info = mutagen.File(f).info
        except Exception:
            return False
        if not ((getattr(info, "bits_per_sample", 0) or 0) > 16 or (getattr(info, "sample_rate", 0) or 0) > 48000):
            return False
    return True


def _layer_copy(rel, tags, vtracks=None) -> list:
    """MED-22c (decided 2026-11-16): a copy of a hybrid SACD's SACD layers - one disc's stereo or
    multichannel layer, or both of them in one copy - is numbered by the physical disc as MED-22
    counts them (Billy Joel - Piano Man 50th, CD layer, stereo and multichannel SACD layers of
    disc 1 and a bonus CD: its layers' copy is disc 1 of 2, never discs 2 and 3 of 4), its media
    and tracks its own layer's, and each of its files carries "~layer" ("SACD Stereo", "SACD
    Multichannel", "SACD": mb_tags.layer_label) - the planner files it in "Disc 01 (SACD
    Stereo)" beside the CD layer's "Disc 01". Version folders (VTRACKS: [[medium position, track
    index]], match_core.version_view) are numbered here; other copies were numbered by
    mb_tags.one_layer already. Only an SACD layer of a disc listed as two layers or more; the
    label follows the layer, whatever else the copy holds (a copy of every disc in one SACD
    layer too: a CD layer's copy arriving later is filed beside it, in the plain "Disc 01").
    TAGS unchanged otherwise."""
    groups = mb_tags.layer_groups(rel or {})
    if not tags or not any(len(g) > 1 for g in groups):
        return tags
    media = rel.get("media") or []
    at = {int(m.get("position") or k): m for k, m in enumerate(media, 1)}
    disc_of = {id(m): k for k, g in enumerate(groups, 1) for m in g}
    joined = {id(m) for g in groups if len(g) > 1 for m in g}
    one = mb_tags.one_layer(rel)
    out = [dict(t) for t in tags]
    if vtracks:
        if len(vtracks) != len(out) or any(int(p) not in at for p, _k in vtracks):
            return tags
        mine = [at[int(p)] for p, _k in vtracks]
        total = str(mb_tags.disc_total(one, media={disc_of[id(m)] for m in mine}))
        for t, m in zip(out, mine):
            t["discnumber"], t["totaldiscs"] = [str(disc_of[id(m)])], [total]
    else:
        mine = []
        for t in out:
            try:
                disc = int(str((t.get("discnumber") or ["1"])[0]).partition("/")[0])
            except ValueError:
                return tags
            fmt = str((t.get("media") or [""])[0])
            g = groups[disc - 1] if 0 < disc <= len(groups) else []
            m = next((x for x in g if x.get("format") == fmt), None)
            if m is None:
                return tags
            mine.append(m)
    for t, m in zip(out, mine):
        label = mb_tags.layer_label(m.get("format")) if id(m) in joined else ""
        if label:
            t["~layer"] = [label]
    return out


def _area_copy(rel, tags, vtracks, album) -> list:
    """MED-22c: version folders of an SACD the release lists as one disc naming no layer
    ("Hybrid SACD": Kodo - One Earth Tour Special, SICL-10001, ripped from its ISO to "Stereo/"
    and "Multichannel/") are that disc's stereo and multichannel areas: each file carries
    "~layer" ("SACD Stereo", "SACD Multichannel") from its folder's layout, so they are filed as
    "Disc 01 (SACD Stereo)" and "Disc 01 (SACD Multichannel)" in the one album folder. TAGS
    unchanged otherwise (a layer _layer_copy labelled, a DVD's mixes, no version folders)."""
    if not tags or not vtracks or len(vtracks) != len(tags) or any(t.get("~layer") for t in tags):
        return tags
    at = {int(m.get("position") or k): m for k, m in enumerate((rel or {}).get("media") or [], 1)}
    lays = album.get("version_discs") or {}
    files = album.get("tracks") or []
    if len(files) != len(tags):
        return tags
    out = []
    for t, (p, _k), f in zip(tags, vtracks, files):
        fmt = (at.get(int(p)) or {}).get("format")
        lay = lays.get(f.get("disc") or 1)
        if mc.medium_type(fmt) != "sacd" or mc.medium_layout(fmt) or lay not in ("stereo", "multichannel"):
            return tags
        # (its layer's own media name, as MusicBrainz names a layer listed apart: "Hybrid SACD
        # (SACD layer, 2 channels)", "SACD (multichannel)")
        area = "2 channels" if lay == "stereo" else "multichannel"
        media = f"{fmt} (SACD layer, {area})" if "hybrid" in str(fmt).casefold() else f"{fmt} ({area})"
        out.append(dict(t, media=[media], **{"~layer": ["SACD Stereo" if lay == "stereo" else "SACD Multichannel"]}))
    return out


def _uniform_layer(prefer) -> str:
    """MED-22: the one layer a {disc: layer} map names for every disc ("cd" when they differ)."""
    kinds = set((prefer or {}).values())
    return kinds.pop() if len(kinds) == 1 else "cd"


def _box_medium(rel, box, src) -> int:
    """BOX-2: the MusicBrainz medium position of a lone disc: a MusicBrainz pick numbers its discs
    as audio discs only (_audio_position); a Discogs pick as its track positions do, a video disc
    listed first counted - the same as the medium position (the media counts agree: "same")."""
    return _audio_position(rel, box) if src == "mb" else box


def _audio_position(rel, n) -> int:
    """BOX-2: the medium position of the release's n-th audio disc (the candidate's disc n): a
    video disc listed first is no disc of the candidate's (DVD-Video, CD, CD: disc 2 is medium
    3). n itself when the release has no such disc."""
    audio = [m.get("position") or k for k, m in enumerate(rel.get("media") or [], 1)
             if str(m.get("format") or "").casefold() not in mb_tags.VIDEO_FORMATS]
    return int(audio[n - 1]) if 0 < n <= len(audio) else n


def _without_box_vinyl(rel, ps):
    """MED-19: release_tags of a MusicBrainz release mixing CDs (or a download) with vinyl or
    tape, without the vinyl's and tape's media; None when it mixes none."""
    kinds = {m.get("position"): mc.media_kind(m.get("format")) for m in rel.get("media") or ()}
    if not (set(kinds.values()) & {"vinyl", "cassette"} and set(kinds.values()) & {"cd", "digital"}):
        return None
    drop = {int(p) for p, k in kinds.items() if p and k in ("vinyl", "cassette")}
    return mb_tags.release_tags(rel, ps, without=drop)


def _cue_counts(text) -> dict:
    """{file name named by a FILE line (case-folded): its TRACK ... AUDIO count} of a cue sheet."""
    counts, current = {}, None
    for line in str(text or "").splitlines():
        m = re.match(r'\s*FILE\s+"?(.+?)"?(?:\s+(?:WAVE|FLAC|MP3|AIFF|BINARY|MOTOROLA))?\s*$', line, re.I)
        if m:
            current = Path(m.group(1).strip().replace("\\", "/")).name.casefold()
            counts.setdefault(current, 0)
            continue
        if current is not None and re.match(r"\s*TRACK\s+\d+\s+AUDIO", line, re.I):
            counts[current] += 1
    return counts


def _cue_tracks_of(path) -> int:
    """REC-1: how many tracks a cue sheet gives this one file: a cue in its folder whose FILE line
    names it, or one named as the file ("x.cue", "x.flac.cue") naming one file only (the WAV it
    was ripped to, converted since); or the cue sheet the file carries itself (a FLAC's CUESHEET
    block or CUESHEET tag). 0 when no cue sheet speaks of it."""
    path = Path(path)
    most = 0
    try:
        cues = [c for c in path.parent.iterdir() if c.suffix.casefold() == ".cue" and c.is_file()]
    except OSError:
        cues = []
    for cue in cues:
        try:
            counts = _cue_counts(ma._read_log(cue))
        except OSError:
            continue
        mine = counts.get(path.name.casefold())
        stem = cue.stem
        if Path(stem).suffix.casefold() in ex.AUDIO_EXT:
            stem = Path(stem).stem                        # ("CDImage.flac.cue": the image's own)
        if mine is None and stem.casefold() == path.stem.casefold() and len(counts) == 1:
            # (the image converted since - "x.wav" named, "x.flac" here - the cue named as it is)
            mine = next(iter(counts.values()))
        most = max(most, mine or 0)
    if path.suffix.casefold() == ".flac":
        try:
            from mutagen.flac import FLAC
            f = FLAC(str(path))
            sheet = getattr(f, "cuesheet", None)
            if sheet is not None:
                # (the lead-out is a track of its own there, numbered 170 on a CD)
                most = max(most, len([t for t in sheet.tracks if 0 < int(t.track_number) < 100
                                      and getattr(t, "type", 0) == 0]))
            for text in (f.tags or {}).get("cuesheet", []) if f.tags else []:
                counts = _cue_counts(text)
                most = max(most, max(counts.values()) if counts else 0)
        except Exception:
            pass
    return most


def _without_box_extra(rel, ps):
    """MED-23: release_tags of a MusicBrainz CD box without its DVD-Audio / Blu-ray Audio media
    and its downloads after the last physical disc (those not counted in the total, MED-20);
    None when it has neither (match_albums.mb_candidate marks the same media)."""
    audio = mb_tags.audio_media(rel)
    pos = [int(m.get("position") or d) for d, m in enumerate(audio, 1)]
    kinds = [mc.media_kind(m.get("format")) for m in audio]
    if not any(k == "cd" and not mc.hires_media([m.get("format")]) for k, m in zip(kinds, audio)):
        return None
    physical = [p for p, k in zip(pos, kinds) if k != "digital"]
    download = {p for p, k in zip(pos, kinds) if k == "digital" and physical and p > max(physical)}
    hires = {p for p, k, m in zip(pos, kinds, audio)
             if k != "cd" and ma._DVD_AUDIO.search(str(m.get("format") or ""))}
    if not download and not hires:
        return None
    return mb_tags.release_tags(rel, ps, without=download | hires, uncounted=download or None)


def discogs_parts(det, dm, http=None, s=None, whole_works=False, tracks_det=None, classical=False):
    """(album fields, [per-track fields], disc count) of a Discogs release, as the plugin
    builds them: extract_discogs_fields shaped by the settings, then the master's original
    year (and date = original date when prefer_original_date is on). Per-track fields are in
    the matcher's track order, each with its disc fields; None when the tracklist does not
    line up with the matcher's. WHOLE_WORKS: the matcher read each work listed without lengths
    as one track (TRK-5: your files are one per work), so it is tagged so. TRACKS_DET (BAR-13):
    the tracks are that entry's - the full entry of the same album, DET listing too few of its
    own -, the album's fields and each disc's format DET's."""
    import tagger_settings
    s = s or tagger_settings.enrichment()
    det = discogs_without_repeats(det, dm)
    own = det
    if tracks_det is not None:
        det = discogs_without_repeats(tracks_det, dm)
    scope = _credit_scope(det, dm)              # (CRD-1: from the tracklist as listed, movements too)
    det = ma.discogs_one_track_movements(det, True) if whole_works else \
        ma.discogs_one_track_movements(det)                  # TRK-1: the plugin writes one track too
    album = shape_fields(dm.extract_discogs_fields(own if tracks_det is not None else det), s, dm)
    if classical:
        album = classical_mod.discogs_roles(album)            # CLS-4: librettists are lyricists
    if album.get("barcode"):
        album["barcode"] = barcode_digits(album["barcode"])
    if det.get("master_id") and http is not None:
        try:
            master = http.get_json(f"https://api.discogs.com/masters/{det['master_id']}", "discogs")
            if master:
                album.update(dm.extract_original_year(master))
        except Exception:
            pass
    if s["prefer_original_date"]:
        original = album.get("originaldate") or album.get("originalyear")
        if original:
            album["date"] = original
    raw = ma.drop_video_tracks(_flatten_raw(det.get("tracklist")), det)
    cand = ma.discogs_candidate(det, dm)
    discs = [t["disc"] for t in cand["tracks"]]
    total_discs = cand["disc_count"] or max(discs or [1])
    if len(discs) != len(raw):
        return album, None, total_discs
    per_disc = {}
    for d in discs:
        per_disc[d] = per_disc.get(d, 0) + 1
    vinyl = [bool(c.get("box_vinyl")) for c in cand["tracks"]] if len(cand["tracks"]) == len(discs) \
        else [False] * len(discs)
    # MED-15: each track's media is its own disc's format, never every format of the release
    # joined ("CD, ...; Blu-ray, ...; Box Set": a box's Blu-ray named every CD of it Blu-ray)
    media = discogs_track_media(own, raw, discs)
    # (CRD-1b: a release of one format: every track is on it)
    carriers = [str(f.get("name") or "") for f in own.get("formats") or []
                if isinstance(f, dict) and str(f.get("name") or "").strip().casefold() not in _NO_CARRIER]
    seen, tracks = {}, []
    for i, (entry, disc) in enumerate(zip(raw, discs)):
        seen[disc] = seen.get(disc, 0) + 1
        t = dict(dm.extract_disc_fields({"number": disc}, total_discs))
        # numbered within each disc: the plugin's automatic path numbers through the whole
        # release, which would give disc 2's first track number 11
        t.update(dm.extract_track_fields(entry, seen[disc], per_disc[disc]))
        t["discnumber"], t["totaldiscs"] = str(disc), str(total_discs)
        medium = media[i] if media is not None else (carriers[0] if len(set(carriers)) == 1 else "")
        if vinyl[i]:
            t["~box_vinyl"] = True          # MED-19: an LP or 7" of a CD box (engine._identified)
        credits = _track_credits(entry, scope, s, dm, medium, det)
        if credits:
            t["~credits"] = classical_mod.discogs_roles(credits) if classical else credits
        if classical and entry.get("~index_work"):
            _index_work(t, *entry["~index_work"])
        tracks.append(t)
    if media is not None:
        album.pop("media", None)
        for t, m in zip(tracks, media):
            if m:
                t["media"] = m
    return album, tracks, total_discs


def _files_key(files) -> tuple:
    """The files as CLS-2's memo knows them: path, size and change time (a file retagged since is
    read anew)."""
    out = []
    for f in files or ():
        try:
            st = os.stat(f)
            out.append((str(f), st.st_size, st.st_mtime_ns))
        except OSError:
            out.append((str(f), -1, -1))
    return tuple(out)


def _index_work(t, work, at, count):
    """CLS-4: a classical Discogs movement listed under its work (an "index" entry whose
    sub-tracks are the movements): Work the index's title, Movement Name the movement's title
    as listed, Movement its place under the work, Movement Total the work's movements, Show
    Movement 1 - and the title "Work: Movement" (MusicBrainz's classical style), since
    Discogs's title names the movement alone ("I. Prelude")."""
    title = str(t.get("title") or "").strip()
    if not work:
        return
    t["work"] = work
    if title:
        t["movement"] = title
        t["showmovement"] = "1"
        if not title.casefold().startswith(work.casefold()):
            t["title"] = f"{work}: {title}"
    t["movementnumber"], t["movementtotal"] = str(at), str(count)


# Discogs formats that are no medium of their own (the box, "All Media")
_NO_CARRIER = {"box set", "all media"}
_POSITION_KIND = re.compile(r"^\s*([A-Za-z][A-Za-z\- ]*?)\s*(\d+)\s*[-.:]", re.I)
# (MED-15: "CD-1", "BD-12": a carrier of the release named with the track's number alone)
_POSITION_CARRIER = re.compile(r"^\s*([A-Za-z][A-Za-z ]*?)\s*-\s*\d+[a-z]?\s*$", re.I)


def discogs_track_media(det, raw, discs):
    """MED-15: the media of each track's disc, as the Discogs plugin writes a format ("CD,
    Album, Remastered"), or None when the release has one format only (the plugin's own value
    stands). A track whose position names its carrier ("CD3-1", "Blu-ray2-4") is that
    carrier's disc of that number; otherwise discs are the release's media in order, each
    format counted by its quantity. A track no medium can be found for gets none ("")."""
    entries = []
    for f in det.get("formats") or []:
        name = str(f.get("name") or "").strip()
        if not name or name.casefold() in _NO_CARRIER:
            continue
        q = str(f.get("qty") or "1").strip()
        q = int(q) if q.isdigit() and int(q) > 0 and name != "File" else 1   # (a download's qty counts files)
        text = ", ".join(x for x in [name, *(str(d) for d in f.get("descriptions") or [])] if x)
        entries += [(name, text)] * q
    if len({t for _n, t in entries}) <= 1:
        return None
    by_kind = {}
    for name, text in entries:
        by_kind.setdefault(mc.fold(name), []).append(text)
    out = []
    for entry, disc in zip(raw, discs):
        m = _POSITION_KIND.match(str((entry or {}).get("position") or ""))
        kind = [v for k, v in by_kind.items() if m and (k == mc.fold(m.group(1)) or k.startswith(mc.fold(m.group(1))))]
        bare = None if m else _POSITION_CARRIER.match(str((entry or {}).get("position") or ""))
        alone = [v for k, v in by_kind.items() if bare and k == mc.fold(bare.group(1))]
        if m and kind:
            n = int(m.group(2))
            out.append(kind[0][n - 1] if 1 <= n <= len(kind[0]) else "")
        elif alone and len(alone[0]) == 1:
            # (a carrier named with its track alone, "CD-1" beside "BD-1": the release's one CD -
            # Gojira - The Flesh Alive, Discogs 3663285, never the Blu-ray listed first)
            out.append(alone[0][0])
        else:
            out.append(entries[disc - 1][1] if disc and 1 <= disc <= len(entries) else "")
    return out


_POSITION = ("discnumber", "totaldiscs", "tracknumber", "totaltracks")
_MEDIUM_KEYS = ("discsubtitle", "media", "musicbrainz_discid")


def _num(v) -> int:
    s = str((v if not isinstance(v, list) else (v or [""])[0]) or "").partition("/")[0].strip()
    return int(s) if s.isdigit() else 0


def _own_disc_writes(files) -> dict:
    """{path: {"discnumber": before, "totaldiscs": before}} for the files whose disc number or
    total a TagMender job CHANGED (from empty, or from "1" to "2" - Palms) and nobody changed
    since (REP-1's history, match_albums.tagmender_originals): back to the value before that
    job. Files whose disc fields the job left as they were are not touched; any of the album's
    disc fields changed since the job -> {} (yours)."""
    try:
        _folder, orig, _foreign = ma.tagmender_originals([str(f) for f in files])
    except Exception:
        return {}
    out = {}
    for f in files:
        fields = (orig or {}).get(str(f)) or {}
        try:
            now = tagio.read(str(f))
        except Exception:
            return {}
        back = {}
        for k in ("discnumber", "totaldiscs"):
            if k in fields:
                before, left = fields[k]
                b = [str(x) for x in (before or []) if str(x).strip()]
                l_ = [str(x) for x in (left or []) if str(x).strip()]
                if [str(x) for x in (now.get(k) or []) if str(x).strip()] != l_:
                    return {}                  # (you changed the album's disc numbers since: yours)
                if b != l_:
                    back[k] = b                # (the value before TagMender's job: "1", or none)
        if back:
            out[str(f)] = back
    return out


def _one_run_vs_discs(mb: list, dg: list) -> str:
    """TRK-4b: the same tracks in the same order, one database numbering them as one run on one
    disc (1..n: Discogs's A1..D2 of a 2LP, as the plugin numbers them) and the other split into
    discs each numbered from 1 (MusicBrainz's 6 + 5). "dg" when Discogs is the one run, "mb"
    when MusicBrainz is; "" otherwise."""
    if len(mb) != len(dg) or len(mb) < 2:
        return ""

    def run(lay):
        return len({d for d, _k in lay}) == 1 and [k for _d, k in lay] == list(range(1, len(lay) + 1))

    def split(lay):
        discs = sorted({d for d, _k in lay})
        return len(discs) >= 2 and all([k for x, k in lay if x == d] == list(range(1, sum(1 for x, _k in lay if x == d) + 1))
                                        for d in discs)
    if run(dg) and split(mb):
        return "dg"
    if run(mb) and split(dg):
        return "mb"
    return ""


def _settle_one_run(tags_mb: list, tracks_dg: list, files: list, which: str):
    """TRK-4b: number every file of one layout, never disc from one database and track from the
    other (Deftones - Palms 2LP: disc 2 with tracks 4-6 of 6; Tame Impala - Innerspeaker 2LP:
    disc 2 track 7 of 11). The files' own numbers decide: numbered as one run (1..n, no disc
    or all disc 1) -> one run, and no disc number or disc total the files do not have; numbered
    disc by disc -> the split layout; else the split layout (MusicBrainz's discs, as before).
    Existing numbers are never turned into a layout neither database nor the files have."""
    n = len(tags_mb)
    own = [((f.get("disc") or 1), f.get("num") or 0) for f in files] if len(files) == n else []
    numbered = [x for x in own if x[1]]
    as_run = bool(own) and len(numbered) == n and len({d for d, _k in own}) == 1 and \
        [k for _d, k in own] == list(range(1, n + 1))
    split_tags = tags_mb if which == "dg" else tracks_dg
    if as_run:
        # (disc numbers the files - as they were before TagMender, REP-1 - never had go: an
        # earlier run's "disc 2 of 2" beside "track 4 of 6"; your own disc numbers stay)
        no_discs = all(not f.get("disc_tag", f.get("disc")) for f in files)
        out_mb, out_dg = [], []
        for i, (t, d) in enumerate(zip(tags_mb, tracks_dg)):
            t, d = dict(t), dict(d)
            for x in (t, d):
                x.pop("discnumber", None)
                x.pop("totaldiscs", None)
                x["tracknumber"] = [str(i + 1)] if x is t else str(i + 1)
                x["totaltracks"] = [str(n)] if x is t else str(n)
            if no_discs:
                t["~remove"] = list(dict.fromkeys(list(t.get("~remove") or []) + ["discnumber", "totaldiscs"]))
            out_mb.append(t)
            out_dg.append(d)
        return out_mb, out_dg, ("MusicBrainz and Discogs number the discs otherwise: numbered as one run, as your "
                                "files are (TRK-4b)")
    # the split layout in both (the merge then writes one layout)
    src = split_tags
    out_mb, out_dg = [], []
    for t, d, s in zip(tags_mb, tracks_dg, src):
        t, d = dict(t), dict(d)
        for k in _POSITION:
            v = s.get(k)
            if v:
                t[k] = v if isinstance(v, list) else [str(v)]
                d[k] = (v[0] if isinstance(v, list) else str(v))
        out_mb.append(t)
        out_dg.append(d)
    return out_mb, out_dg, ("MusicBrainz and Discogs number the discs otherwise: numbered disc by disc"
                            + (", as your files are" if numbered else "") + " (TRK-4b)")


def settle_disc_layout(tags_mb: list, tracks_dg: list, files: list):
    """TRK-4: MusicBrainz's and Discogs's tracks of one release, the same in number and
    order, split into discs otherwise (Robin Trower - The Studio Albums 1973-1983:
    MusicBrainz 8 + 10 on discs 4 and 5, Discogs 9 + 9). The plugin's merge takes the disc
    number from MusicBrainz and the track number from Discogs - a file of neither's layout
    (disc 5, track 9). The layout the files' own disc and track numbers agree with numbers
    every file, in both (most of the files' numbers, and more of them than the other's);
    else MusicBrainz's. A file moved to
    another disc takes that disc's own fields (its title, media) from MusicBrainz.
    (tags_mb, tracks_dg, note); note "" when the layouts are the same."""
    pos = lambda t: (_num(t.get("discnumber")) or 1, _num(t.get("tracknumber")))   # noqa: E731
    mb, dg = [pos(t) for t in tags_mb], [pos(t) for t in tracks_dg]
    if mb == dg:
        return tags_mb, tracks_dg, ""
    count = lambda lay: {d: sum(1 for x, _k in lay if x == d) for d, _k in lay}   # noqa: E731
    cm, cd = count(mb), count(dg)
    one = _one_run_vs_discs(mb, dg)
    if one:
        return _settle_one_run(tags_mb, tracks_dg, files, one)
    if set(cm) != set(cd) or len(cm) < 2 or any(min(k for x, k in lay if x == d) != 1
                                                for lay, c in ((mb, cm), (dg, cd)) for d in c):
        # (not the same discs split otherwise: a DVD numbered first on one of them, a set listed
        # as one disc, a hidden track 0 - the plugin's merge as before)
        return tags_mb, tracks_dg, ""
    own = [((f.get("disc") or 1), f.get("num") or 0) for f in files] if len(files) == len(mb) else []
    numbered = [i for i, (_d, k) in enumerate(own) if k]
    agree = lambda lay: sum(1 for i in numbered if own[i] == lay[i])   # noqa: E731
    # (most of the files: one file already numbered otherwise - an earlier run's - does not
    # outvote the rest)
    fits = lambda lay, other: 2 * agree(lay) > len(numbered) and agree(lay) > agree(other)   # noqa: E731
    follow = "discogs" if fits(dg, mb) else "musicbrainz"
    if follow == "discogs":
        medium = {}
        for t in tags_mb:
            medium.setdefault(_num(t.get("discnumber")) or 1, {k: t[k] for k in _MEDIUM_KEYS if k in t})
        out = []
        for t, d in zip(tags_mb, tracks_dg):
            t = dict(t)
            moved = pos(t)[0] != pos(d)[0]
            for k in _POSITION:
                if d.get(k):
                    t[k] = d[k] if isinstance(d[k], list) else [str(d[k])]
            if moved and pos(d)[0] in medium:
                for k in _MEDIUM_KEYS:
                    t.pop(k, None)
                t.update(medium[pos(d)[0]])
            out.append(t)
        return out, tracks_dg, ("MusicBrainz and Discogs split the discs differently: numbered as Discogs "
                                "and your files number them (TRK-4)")
    out = []
    for t, d in zip(tags_mb, tracks_dg):
        d = dict(d)
        for k in _POSITION:
            if t.get(k):
                d[k] = (t[k] or [""])[0] if isinstance(t[k], list) else str(t[k])
        out.append(d)
    return tags_mb, out, ("MusicBrainz and Discogs split the discs differently: numbered as MusicBrainz"
                          + (" and your files number them" if fits(mb, dg) else "") + " (TRK-4)")


def _own_ids_clash(files, clash) -> bool:
    """Every clashing release ID on every file is one a job of TagMender's wrote (REP-1's
    ownership index: written, or kept by a later job, never undone)."""
    if not files or not clash:
        return False
    try:
        owned = ma._own_index()
    except Exception:
        return False
    if not owned:
        return False
    for path in files:
        own = owned.get(str(path)) or owned.get(os.path.realpath(str(path)))
        if not own:
            return False
        for field, ids in clash.items():
            have = own[0] if field == "musicbrainz_albumid" else own[1]
            if any(str(x).lower() not in {str(h).lower() for h in have} for x in ids):
                return False
    return True


_NAME_SORTS = (("albumartist", "albumartistsort"), ("artist", "artistsort"))


def sorts_follow_names(merged: dict, where: dict, src: dict):
    """NAM-10: a sort name sorts the name beside it. When the two databases name the artist
    differently and the name written is one database's, its sort name is that database's too
    (or none): Tara Hugo Sings Philip Glass - MusicBrainz's "Philip Glass" beside Discogs's sort
    "Tara Hugo Sings Philip Glass" was a sort name of another credit. Where both name the artist
    alike nothing changes."""
    for name, sort in _NAME_SORTS:
        if name not in merged or sort not in merged:
            continue
        a, b = (src.get("musicbrainz") or {}).get(name), (src.get("discogs") or {}).get(name)
        if not a or not b or _vals_eq(a, b):
            continue
        own = where.get(name)
        if own not in src or where.get(sort) == own:
            continue
        theirs = (src.get(own) or {}).get(sort)
        if theirs:
            merged[sort], where[sort] = theirs, own
        else:
            merged.pop(sort, None)
            where.pop(sort, None)
    return merged, where


def _latin_share(text) -> float:
    letters = [c for c in str(text) if c.isalpha()]
    return (sum(1 for c in letters if ord(c) < 0x250) / len(letters)) if letters else 0.0


def latin_side(value):
    """NAM-11: "「森の組曲」 = "Forest" Suite" -> '"Forest" Suite': of a title the database gives in
    two languages joined by " = " (Discogs's way), the side in Latin script; a title wholly in
    Latin script, or one without a Latin side, stays as it is."""
    one = isinstance(value, str)
    out = []
    for v in ([value] if one else list(value or [])):
        v = str(v)
        if _latin_share(v) < 1.0 and " = " in v:          # (any letter not Latin: "森の組曲")
            sides = [x.strip() for x in v.split(" = ")]
            latin = next((x for x in sides if x and _latin_share(x) >= 0.5), None)
            v = latin or v
        out.append(v)
    return out[0] if one else out


def latin_titles(merged: dict, s: dict) -> dict:
    """NAM-11: with album / track title translation asked for (the profile's tagger settings: an
    English alias where MusicBrainz has one), a two-language title from either database is
    written by its Latin side too."""
    for key, flag in (("album", "translate_album_titles"), ("title", "translate_track_titles")):
        if (s or {}).get(flag) and merged.get(key):
            merged[key] = latin_side(merged[key])
    return merged


def _vals_eq(a, b) -> bool:
    as_list = lambda v: [str(x) for x in (v if isinstance(v, list) else [v])]   # noqa: E731
    return as_list(a) == as_list(b)


def _as_tags(d: dict) -> dict:
    return {k: (v if isinstance(v, list) else [str(v)]) for k, v in d.items()
            if not str(k).startswith("~") and v not in ("", None, [])}


def discogs_tags(det, dm, http=None, s=None) -> list[dict]:
    """A release tagged from Discogs alone (no MusicBrainz release): every field from Discogs,
    shaped by the plugin's settings."""
    album, tracks, _ = discogs_parts(det, dm, http, s)
    return discogs_only_tags(album, tracks)


def discogs_only_tags(album: dict, tracks) -> list[dict]:
    if tracks is None:
        return []
    out = []
    for t in tracks:
        merged = dict(with_track_credits(album, t))          # CRD-1: the track's own credits
        merged.update(t)
        merged.setdefault("artist", album.get("albumartist", ""))
        out.append(_as_tags(merged))
    return out


TRACK_ONLY = ("discnumber", "totaldiscs", "disctotal", "discsubtitle")
# CRD-1c: the credits MusicBrainz gives of a track's work
_WORK_CREDITS = ("composer", "lyricist", "writer")


def enrich(mb: dict, album: dict, track: dict | None, s: dict, disc_count: int, dm) -> dict:
    """One file's MusicBrainz tags with the Discogs release applied, as the plugin applies
    it in Picard (_apply_discogs_fields, then the per-track fields through the merge mode,
    "Discogs only" clearing, then artist credits settled)."""
    meta = Meta(mb)
    mode = s["merge_mode"] or "combine"
    # CRD-1c: the track's own scoped credits (discogs_parts) go in with the album's fields; a
    # work credit (composer, lyricist, writer) Discogs gives for some tracks only (none
    # album-wide) is no Discogs value of the album's field, so it never replaces MusicBrainz's
    # value of that track (MusicBrainz's are its work's): Override fills only where MusicBrainz
    # gives none, as on the album's other tracks (Pink Floyd - The Dark Side Of The Moon
    # Immersion: Clare Torry, "Composed By [Vocals]" of "The Great Gig In The Sky", had taken
    # MusicBrainz's Richard Wright and Clare Torry off those three tracks)
    own = (track or {}).get("~credits") if isinstance(track, dict) else None
    scoped_only = {k for k in (own or {}) if k not in album and k in _WORK_CREDITS}
    album = with_track_credits(album, track)
    if s["split_collaborations"]:                      # done by the plugin on MusicBrainz's data
        try:
            dm.settle_artist_credits(meta, dm.snapshot_artist_credits(meta), (), True, None)
        except Exception:
            pass
    if mode == "mb_only":
        return meta.d
    names_before = dm.snapshot_artist_credits(meta)
    boxset = dm.is_probable_boxset(meta.getall("album"), album.get("album"), disc_count)
    for f, v in album.items():
        if dm.is_supplementary_field(f):
            meta[f] = v
            continue
        if f == "album":
            for tf, tv in dm.resolve_album_and_subtitle(meta.getall("album"), v,
                                                        meta.getall("discsubtitle"), mode).items():
                meta[tf] = tv
            continue
        eff = "fill_gaps" if (boxset and f in dm.PROTECTED_IN_BOXSET) else mode
        if eff == "override" and f in scoped_only:
            eff = "fill_gaps"
        r = dm.resolve_field_value(meta.getall(f), v, eff, field=f)
        if r is not None:
            meta[f] = r
    per = {}
    if s["track_tags"] and track:
        # the automatic path writes track identity only; disc numbers come from MusicBrainz
        per = {k: v for k, v in track.items() if k not in TRACK_ONLY}
        for f, v in per.items():
            if f.startswith("~"):
                continue
            r = dm.resolve_field_value(meta.getall(f), v, mode, field=f)
            if r is not None:
                meta[f] = r
    supplied = set(album) | set(per)
    if mode == "discogs_only":
        for name in dm.tags_to_clear(meta.keys(), supplied, dm.parse_protected_tags(s["protected_tags"])):
            meta.delete(name)
    try:
        dm.settle_artist_credits(meta, names_before, supplied, mode != "discogs_only", None)
    except Exception:
        pass
    return meta.d
