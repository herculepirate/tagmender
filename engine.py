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
import os
import queue
import re
import sqlite3
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import companions
import album_artist
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
        if d.parent != folder or not is_disc_dir(d):
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
        numbers.extend(nums)
    return len(artists) == 1 and len(numbers) == len(set(numbers))


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


def _without_version_marks(title) -> str:
    """The album title without the closing brackets that only mark its version or edition."""
    t = str(title or "")
    while True:
        m = re.search(r"\s*[\(\[\{]([^\)\]\}]*)[\)\]\}]\s*$", t)
        words = re.findall(r"[a-z0-9.-]+", m.group(1).casefold()) if m else []
        marks = [w for w in words if _MARK_WORD.fullmatch(w.strip(".-"))]
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
    return (list(best.get("notes") or []) + list(out.get("shop_notes") or []) + list(out.get("gnudb_notes") or [])
            + list(out.get("acoustid_notes") or []))


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
        watched folder: its tracks are never even opened)."""
        path = Path(os.path.abspath(Path(path).expanduser()))  # plans and journals hold absolute paths
        if path.is_file():
            return [Group(path.parent, [path], [], single=True)]
        found = {}
        skip = {os.path.realpath(str(x)) for x in skip}
        if hasattr(self.profile, "skip_paths"):          # (where unidentified albums are moved to)
            skip |= self.profile.skip_paths()
        skips = self.profile.folder_skipper() if hasattr(self.profile, "folder_skipper") else _skips_folder
        real_of = {}                                     # (each folder's real path, resolved once)

        def skipped(dirpath, d):
            if not skip:
                return False
            child = os.path.join(dirpath, d)
            if os.path.islink(child):
                real = os.path.realpath(child)
            else:                                        # (its folder's real path and its own name)
                top = real_of.get(dirpath)
                if top is None:
                    top = real_of[dirpath] = os.path.realpath(dirpath)
                real = os.path.join(top, d)
            real_of[child] = real
            return real in skip
        for dirpath, dirs, names in os.walk(path):
            # (the profile's Skip These Folders, hidden folders, TagMender's own folders)
            dirs[:] = sorted(d for d in dirs if not skips(d) and not skipped(dirpath, d))
            d = Path(dirpath)
            audio = sorted(d / n for n in names if Path(n).suffix.lower() in ex.AUDIO_EXT and not n.startswith("."))
            if audio:
                found[d] = audio
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
                sets[d.parent] = version_dirs(d.parent, metas, skips)
            return d in sets[d.parent]
        disc = {d: d != path and (is_disc_dir(d) or version_of(d)) for d in found}
        owner_of = {d: (d.parent if disc[d] else d) for d in found}
        for d in found:          # ("Album/Disc 02/5.1/": versions filed inside a disc folder)
            if disc[d] and version_of(d) and owner_of[d] != path and is_disc_dir(owner_of[d]):
                owner_of[d] = owner_of[d].parent
        own_key = {}
        for d in found:
            own_key.setdefault(owner_of[d], []).extend(found[d])
        own_key = {o: set(album_keys(fs, o, metas).values()) for o, fs in own_key.items()}
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
            if self.profile.excluded_path(folder, named=[path]):
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
            elif len(by_album) > 1 and _one_box(folder, files, keys, metas):
                by_album = {tagged[0]: files}                                  # DSC-2
            if len(by_album) == 1:
                groups.append(Group(folder, files, [], single=len(files) == 1))
                continue
            for key, members in by_album.items():            # a mixed folder: one group per album
                others = [f for f in files if f not in members]
                if key[0] == "folder":                       # untagged leftovers: each on its own
                    for m in members:
                        groups.append(Group(folder, [m], [f for f in files if f != m], single=True))
                else:
                    groups.append(Group(folder, members, others, single=len(members) == 1))
        groups = _disc_sets(groups, metas)               # DSC-1
        if hasattr(self.profile, "joins_deluxe") and not self.profile.joins_deluxe():
            return groups
        return _with_deluxe(self.profile, groups, path, skip)

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
        choice = self._choice_for(g, getattr(self, "_alone", True))
        ref = choice.get("ref") if isinstance(choice, dict) else choice
        self._last_out = None
        prop = self._forced(self._chosen_files(g, choice), ref) if ref else self._identify(g)
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

    def identify_all(self, g: Group) -> list:
        """identify, as a list of albums: one, or each disc of a set whose discs are
        different releases (_per_disc), each its own album."""
        prop = self.identify(g)
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
        mixed = mixed_versions(g.files)
        where = stage[0] if stage else g.folder         # DSC-1 (a set side by side: its stage)
        row = {"FullPath": str(where), "AlbumArtistFolder": artist_folder, "AlbumFolder": album_folder,
               "Status": "untagged"}
        outside = [] if stage else self._outside(g)
        args = argparse.Namespace(ocr=self.ocr, max_images=getattr(self, "ocr_images", 15), process_anyway=False,
                                  use_mb=self.use_mb, rules=self.rules,
                                  use_discogs=self.use_discogs, provider_errors=errors, exclude_files=outside,
                                  use_shops=self.profile.provider_on("web", "shops"),
                                  use_gnudb=self.profile.provider_on("web", "gnudb"),
                                  acoustid=ma.acoustid_mode(self.profile.get("acoustid", "unsure")))
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
        self._last_out = out                         # (the matcher's answer: _versions_split's candidates)
        held = self._preferred_down(errors, g.folder, [str(f) for f in g.files])
        if held is not None:
            return held
        if mixed and not _holds_every_file(out, len(g.files)):
            return self._split_or_mixed(g, mixed, errors)
        album = ma.read_album(where, artist_folder, album_folder, exclude={str(x) for x in outside})
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
                return self._one_release(self._identified(out, album, files, base), out, album)
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
        if out and out.get("album_only") and out.get("best") and self._accepts("album") and \
                not _provider_failed(errors):
            # the album is certain, only its pressing is not: tag what every pressing shares
            # (never while MusicBrainz or Discogs failed: once both answer, the pressing may be proven)
            try:
                return self._album_only(self._identified(out, album, files, base, versions=False),
                                        out["album_only"])
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
        pairs, how = version_sides(pairs, info)
        guided = bool(how)
        halves = [[a for a, _b in pairs], [b for _a, b in pairs]]
        props, outs = [], []
        for fs in halves:
            props.append(self._version_alone(g, fs))
            outs.append(getattr(self, "_last_out", None))
        why = _not_own(props)
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

    def _forced(self, g: Group, ref: str) -> planmod.Proposal:
        """The release the user chose for this album (Picard's "Other versions", or a pasted
        MusicBrainz or Discogs link): no search; the matcher's score of it is shown, and the
        tags are written as for any match - when its tracks line up with the files."""
        kind, rid = parse_release_ref(ref)
        outside = self._outside(g)
        album = ma.read_album(g.folder, g.folder.parent.name, g.folder.name, exclude={str(x) for x in outside})
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
        best = dict(cand, confidence=100.0, notes=list(sc.get("notes") or []))
        best.update(_slots_of(album, cand, sc))
        if kind == "mb":
            best["mb_id"] = cand["id"]
        else:
            best["discogs_id"] = cand["id"]
        why = [f"the release you chose ({ref}); the matcher scores it {sc.get('confidence', 0):.0f}"]
        fits = sc.get("tracks_ok") or (best.get("slots") and mc.score_candidate(
            album, mc.slot_view(album, cand) or cand, self.cfg).get("tracks_ok"))
        if best.get("slots") and best.get("missing"):
            why.append(f"tracks {mc.missing_text(best['missing'])} of {best['release_tracks']} are not in the folder")
        if not fits:
            why.append("its track list does not fit the files well: check the preview")
        out = {"status": "auto", "best": best, "why": why, "margin": None}
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

    def _release(self, best):
        return ma.release_fields(best)

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
            if rel:
                tags_mb = mb_tags.release_tags(rel, ps)
        if not (mbid and self.use_mb):
            ps = self.profile.picard_tags()
        es = self.profile.enrichment()
        dparts = None
        if dgid and self.dm is not None and (self.use_discogs or self.token):
            try:
                det = self.http.get_json(f"https://api.discogs.com/releases/{dgid}", "discogs")
            except ma.AuthError:
                if mb_down is None:
                    raise
                raise mb_down from None         # (Discogs refused too: tried again later)
            if det:
                dparts = discogs_parts(det, self.dm, self.http, es)
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
                tags_mb = [whole[i] for i, (p, _k) in enumerate(keys) if int(p) == box] \
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
            tags_mb = [tags_mb[k] for k in slots] if tags_mb and len(tags_mb) == full else None
            if dparts and dparts[1] is not None:
                dparts = (dparts[0], [dparts[1][k] for k in slots] if len(dparts[1]) == full else None, dparts[2])
        if (tags_mb and not slots and len(tags_mb) != n and rel and _has_htoa(album)
                and any(m.get("pregap") for m in rel.get("media") or ())):
            # a rip with the hidden track before track 1 as a file numbered 0: the release's
            # pregap track is that file's (match_core.with_pregap matched it)
            with_gap = mb_tags.release_tags(rel, ps, pregap=True)
            tags_mb = with_gap if len(with_gap) == n else tags_mb
        tags_mb = tags_mb if tags_mb and len(tags_mb) == n else None
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
                    dg_i = _as_tags({**album_f, **(track_i or {})})
                    merged = enrich(merged, album_f, track_i, es, dcount, self.dm)
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
            most_lengths=bool(best.get("most_lengths")), toc_proven=bool(best.get("toc_proven")),
            own_number=bool(best.get("own_number")), guessed_pick=bool(best.get("guessed_pick")),
            number_proven=bool(best.get("number_proven")))
        # what each database was sure of (follow_source: only a sure pick of another release
        # says your files' own ID of it is not this pressing's)
        prop.sure_picks = dict(out.get("sure_picks") or {})
        if layout_note:
            prop.notes = list(getattr(prop, "notes", None) or []) + [layout_note]
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

    def _album_only(self, p, info):
        """An identified album whose pressing is unknown: no release IDs, catalogue number or
        barcode; label, country, media and date only when every tied pressing agrees."""
        import levels
        tied = info.get("tied") or []
        years = {t.get("year") for t in tied}
        same_recordings = self._tied_recordings_agree(tied, p.targets)
        targets = []
        for t in p.targets:
            t = mc.album_only_tags(t, tied, same_recordings, other_kind=bool(info.get("other_kind")))
            # the year every tied pressing shares, else the album's first release: never the
            # day of one pressing
            # (and not the year of a pressing your scans contradict)
            if info.get("edition"):
                # EDN-2: your copy names another label's edition: neither the pick's label nor
                # its country is yours
                t.pop("label", None)
                t.pop("releasecountry", None)
            if info.get("edition") or info.get("doubt"):
                # (an "original" date that is only the other pressing's own - a Discogs release
                # without a master - is no date of yours either)
                first = t.get("originaldate") or t.get("originalyear")
                first = (first[0] if isinstance(first, list) and first else first) or ""
                if info.get("pick_year") and str(first)[:4] == str(info["pick_year"]):
                    for k in ("originaldate", "originalyear"):
                        t.pop(k, None)
            if len(years) == 1 and None not in years and "" not in years and not info.get("scans") \
                    and not info.get("edition") and not info.get("doubt"):
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
                 else f"{len(tied)} pressings fit equally")
        p.why = list(p.why) + [f"pressing unknown ({doubt}): album tags only; "
                               "release IDs, catalogue number, barcode and every field of one pressing left empty"] \
            + isrc_note
        return p

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
            back = self._restore_own(p.files, clash, facts, pick, self._pick_side(p, view))
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

        compared = False
        for m in (rel or {}).get("media") or ():
            mine = copy_tocs.get(str(m.get("position") or ""))
            theirs = [d for d in m.get("discs") or () if d.get("offsets") and d.get("sectors")]
            if not mine or not theirs:
                continue
            nums = mine.split()
            own = lengths(nums[3:], nums[2])
            if any(len(own) == len(o) and all(abs(a - b) <= 1 for a, b in zip(own, o))
                   for o in (lengths(d["offsets"], d["sectors"]) for d in theirs)):
                return ""                    # (one of your discs is one of its discs: the same disc)
            compared = True
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
                if k in have and any(_pressings_value(k, before[k], x) for x in facts if not x.get("unknown")):
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

    def _restore_own(self, files, clash, facts, pick, side) -> dict:
        """TAG-3 / REP-1: {file: {field: value before TagMender}} for the pressing fields
        TagMender's own earlier job wrote of the pressing that clashes, still holding what it left
        - an album found album only takes them back ([] when the field was empty before: removed).
        Only that pressing's: a release ID in the clash (with its release track IDs or Discogs
        fields), or a barcode, catalogue number, label, country, media or ASIN that is its own and
        not the pick's. Never the pick's or a twin's: what TagMender wrote of the release it picks
        again stays (a second plan writes nothing to it)."""
        import tagio
        writes = ma.tagmender_pressing_writes(files)
        known = [x for x in facts if not x.get("unknown")]
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
            mb_lost = losing_id("musicbrainz_albumid", (w.get("musicbrainz_albumid") or ((), ()))[1])
            dg_lost = losing_id("discogs_release_id", (w.get("discogs_release_id") or ((), ()))[1])
            back = {}
            for k, (b, a) in w.items():
                if tuple(b) == tuple(a) or not ma._same_values(now.get(k) or [], a):
                    continue
                if k == "musicbrainz_albumid" or k == "musicbrainz_trackid":
                    ok = mb_lost
                elif k.startswith("discogs_"):
                    ok = dg_lost
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
        prove it whatever the lengths say (BAR-1, BAR-7). A catalogue number on the scans, your
        own number (own_number) or your disc ID (TOC-1, the disc ID choosing it) only as FIL-1
        has them, every track length agreeing - and a number only when it is
        not on the other release too (one every pressing carries proves none of them)."""
        if (self.profile.get("matching") or {}).get("extra_careful") or getattr(p, "guessed_pick", False):
            return ""
        sc = (out or {}).get("scans") or {}
        clean = bool(sc.get("printed") and sc.get("state") == "proven" and not sc.get("against"))
        found = set(sc.get("found") or ())
        if clean and "barcode" in found:
            return "your scans show its barcode"
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
        rg_id = (rel.get("release-group") or {}).get("id")
        if not rg_id:
            notes.append("the MusicBrainz release has no release group")
            return None
        group = _mb_group(self.http, rg_id)
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
                                evidence=list(sc.get("notes") or []), reason=words,
                                reason_codes=["web_fallback_only"],
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
        if prop.status == "identified":
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
        groups = self.albums_in(path)
        per_folder = {}
        for g in groups:
            k = os.path.realpath(str(g.folder))
            per_folder[k] = per_folder.get(k, 0) + 1
        if only is not None:
            groups = [g for g in groups if group_key(g.folder, g.files) in only]
        out = []
        self.reached = set()                      # the group_key()s looked up (a run cancelled: not all)
        for i, g in enumerate(groups):
            if cancel and cancel():
                break
            if progress:
                progress(i, len(groups), str(g.folder))
            # (the album as found and listed: every disc or version it is planned as keeps it,
            # so a later Look Up or Refresh of those rows asks for this album again)
            key = group_key(g.folder, g.files)
            try:
                self._alone = per_folder[os.path.realpath(str(g.folder))] == 1     # (a folder's only album?)
                for p in self.identify_all(g):           # (each disc of _per_disc as an album)
                    self.add_artwork(p)
                    self.apply_level(p)
                    p.group_key = key
                    out.append(p)
            except Exception as exc:                          # one bad album never stops the run
                self.log.exception("identifying %s failed", g.folder)
                p = planmod.Proposal(source_dir=str(g.folder), files=[str(f) for f in g.files],
                                     status="unresolved", reason=f"error: {exc}", reason_codes=["other"])
                p.group_key = key
                out.append(p)
            self.reached.add(key)
        return out

    def listing(self, path, within=None, meta=None) -> dict:
        """The albums PATH holds, not looked up yet (List first): a plan whose albums are
        "listed" - nothing is asked of any database and nothing can be applied. within: only
        the albums inside these folders (several folders dropped at once, listed under the folder
        that holds them). meta: a dict kept by the caller, in which the files' metadata is
        remembered for the next listing or look up (ex.meta_memo; what this listing did not
        read again is dropped from it)."""
        roots = [os.path.realpath(str(w)) for w in within or ()]
        albums = []
        with ex.meta_memo(meta, True) if meta is not None else contextlib.nullcontext():
            groups = self.albums_in(path)
        for g in groups:
            here = os.path.realpath(str(g.folder)) if roots else ""
            if roots and not any(here == r or here.startswith(r.rstrip(os.sep) + os.sep) for r in roots):
                continue
            files = sorted(str(f) for f in g.files)
            albums.append({"source_dir": str(g.folder), "status": "listed", "label": "", "rule": None,
                           "identification": {"status": "listed"}, "release": {}, "files": [],
                           "companions": [], "artwork": None, "conflicts": [], "skipped": [], "notes": [],
                           "album_files": files, "key": group_key(g.folder, g.files)})
        return {"job": self.job, "profile": getattr(self.profile, "name", ""),
                "operation": self.profile["operation"], "listed": True, "albums": albums, "provider_notes": {}}

    def plan(self, path, progress=None, cancel=None, only=None, meta=None) -> dict:
        """meta: a dict kept by the caller (see listing): files whose metadata it remembers, unchanged,
        are not opened again to find the albums (a plan of every album drops what it did not read)."""
        with ex.meta_memo(meta, only is None) if meta is not None else contextlib.nullcontext():
            props = self.proposals(path, progress, cancel, **({"only": only} if only is not None else {}))
        # (your edits from the app's inspector, kept as you typed them: plan.apply_edits)
        p = planmod.build(props, self.profile, self.job, extra_roots=[Path(os.path.abspath(Path(path).expanduser()))],
                          edits=getattr(self, "edits", None))
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
        scan = self.albums_in

        def albums_in(p, skip=()):                       # (the same folder: its albums as found once)
            k = (str(p), tuple(sorted(str(x) for x in skip)))
            if k not in found:
                found[k] = scan(p, skip)
            return list(found[k])
        self.albums_in = albums_in

        def notes():
            return {**outage_notes(getattr(self, "http", None)), **getattr(self, "run_notes", {}), **self.notes}

        def planner():
            return planmod.Planner(self.profile, self.job, extra_roots=[root], edits=getattr(self, "edits", None))
        done = []                                        # (key, its proposals)
        try:
            for i, k in enumerate(keys):
                if cancel and cancel():
                    break
                with ex.meta_memo(meta, False) if meta is not None else contextlib.nullcontext():
                    props = self.proposals(path, (lambda _i, _n, what, i=i: progress(i, what)) if progress else None,
                                           cancel, only={k})
                if k not in getattr(self, "reached", ()) and cancel and cancel():
                    break                                # (cancelled before it: its rows stay)
                for q in props:                          # (as plan.build: absolute paths)
                    q.source_dir = os.path.abspath(os.path.expanduser(str(q.source_dir)))
                    q.files = [os.path.abspath(os.path.expanduser(str(f))) for f in q.files]
                out, (props,) = _build_despite_gone(planner, [props], getattr(self, "log", None))   # (shown at once; replaced below)
                done.append((k, props))
                with_keys(out["albums"], props)
                out["provider_notes"] = notes()
                emit(k, out)
        finally:
            if prev is not None:
                self.albums_in = prev
            else:
                self.__dict__.pop("albums_in", None)     # (the class's own again)
        if len(done) > 1:
            whole, groups = _build_despite_gone(planner, [ps for _k, ps in done], getattr(self, "log", None))
            allp = [q for g in groups for q in g]
            with_keys(whole["albums"], allp)
            whole["provider_notes"] = notes()
            for k, ps in done:
                emit(k, dict(whole, albums=[a for a in whole["albums"] if a.get("key") == k]))
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

        def planner(e):
            return planmod.Planner(e.profile, e.job, extra_roots=[root], edits=getattr(e, "edits", None))

        def notes(e):
            return {**getattr(e, "run_notes", {}), **e.notes}     # (outages: the clones' shared state, below)

        def worker(clone):
            try:
                e = engine_for_thread(self.profile, http=clone, log=self.log, job=self.job, **self._made_with)
                for name in ("edits", "force"):
                    if name in self.__dict__:
                        setattr(e, name, self.__dict__[name])
                scan = e.albums_in

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
                    with ex.meta_memo(meta, False) if meta is not None else contextlib.nullcontext():
                        props = e.proposals(path, lambda _i, _n, what, i=i: results.put(("progress", i, what)),
                                            cancel, only={k})
                    if k not in e.reached and cancel and cancel():
                        break                            # (cancelled before it: its rows stay)
                    for q in props:
                        q.source_dir = os.path.abspath(os.path.expanduser(str(q.source_dir)))
                        q.files = [os.path.abspath(os.path.expanduser(str(f))) for f in q.files]
                    out, (props,) = _build_despite_gone(lambda: planner(e), [props], e.log)
                    with_keys(out["albums"], props)
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
            whole, groups = _build_despite_gone(lambda: planner(self), [done[i][1] for i in sorted(done)],
                                                getattr(self, "log", None))
            allp = [q for g in groups for q in g]
            with_keys(whole["albums"], allp)
            whole["provider_notes"] = {**outage_notes(getattr(self, "http", None)), **notes(self), **told}
            for i in sorted(done):
                k = done[i][0]
                emit(k, dict(whole, albums=[a for a in whole["albums"] if a.get("key") == k]))
        return len(done)


# albums looked up at once: enough to use the waiting on scans, images and files, few enough
# for a home machine and a network share (the providers' pace is shared and does not change)
LOOKUP_WORKERS = 3
_MAKING = threading.Lock()


def engine_for_thread(*args, **kw) -> "Engine":
    """An Engine made while other threads make theirs (loading its plugin is not thread-safe)."""
    with _MAKING:
        return Engine(*args, **kw)


def group_key(folder, files) -> str:
    """One album of a folder, as List first names it: its folder and its files."""
    return "|".join([os.path.realpath(str(folder))] + sorted(os.path.realpath(str(f)) for f in files))


def _build_despite_gone(planner, groups, log=None) -> tuple:
    """(the plan of these albums, the albums as planned). groups: each album's proposals. An album
    a file of which cannot be read while it is planned (a network share that lost it for a moment, a
    folder moved away) becomes a row saying so, as one that failed being identified does
    (Engine.proposals: one bad album never stops the run); the others are planned."""
    groups = [list(g) for g in groups]
    for _try in range(sum(len(q.files) + 1 for g in groups for q in g)):
        try:
            return planner().build([q for g in groups for q in g]), groups
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
    return planner().build([q for g in groups for q in g]), groups


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
        return bool(facts.get("label")) and any(mc.fold(v) == mc.fold(facts["label"]) for v in vals)
    if field == "releasecountry":
        return bool(facts.get("country")) and any(mc.country_code(v) == mc.country_code(facts["country"])
                                                  for v in vals)
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


def mixed_versions(files, versions=False) -> dict:
    """{disc folder: [[file, file], ...]} when one folder holds two versions of the album:
    at least two titles twice in one format and on one disc as the tags number them (the
    same title, or the same file name apart from its number and a copy's " (1)"), those
    pairs at least 80% of its files, and most pairs not the same audio (lengths more than
    match_albums._SAME_AUDIO_S apart, checksums different) - a track saved twice is one copy
    (match_albums drops it). {} otherwise.
    versions: a version's mark in a track title aside ("Twilight" and "Twilight (Remix
    2024)" are one title). An EP or single often holds a track and its remix, or a mono
    and stereo mix, so that reading is only asked once the folder fits no release."""
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
            k = ma._track_key(Path(re.sub(r"\s*\(\d+\)$", "", f.stem) + f.suffix),
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


def version_sides(pairs, info) -> tuple:
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
    tests = (("copy names", lambda f: bool(_COPY_SUFFIX.search(Path(f).stem))),
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


def _release_words(p) -> str:
    """One disc's release, in words: "MusicBrainz release <id>", "Deezer \"<title>\"" ..."""
    album = ((p.targets or [{}])[0].get("album") or [""])[0]
    if str(p.provider).startswith("web:"):
        return f"{p.provider[4:]} \"{album}\""
    name = {"musicbrainz": "MusicBrainz", "discogs": "Discogs"}.get(str(p.provider).split("+")[0], p.provider)
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


def _mb_group(http, rg_id):
    """Every release of a MusicBrainz release group, with each one's tracks (recording, title,
    artist folded; name and length in seconds), disc layout, title and credit (folded): {"ids", "pressings", "whole"} - whole when the one page (100) read
    holds them all; None when it could not be read."""
    data = http.get_json(f"https://musicbrainz.org/ws/2/release?release-group={rg_id}"
                         "&inc=recordings+artist-credits+media&limit=100&fmt=json", "mb")
    if not data or not isinstance(data.get("releases"), list):
        return None
    pressings = []
    for r in data["releases"]:
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
    return sorted({fe.get("copy_of") or fe["src"] for fe in a.get("files") or []} |
                  {x.get("file") for x in a.get("skipped") or [] if x.get("file")} |
                  set(a.get("album_files") or []) | set(a.get("excluded_files") or []))


def parse_release_ref(ref: str):
    """("mb", MBID) or ("discogs", "123") for a MusicBrainz or Discogs release link or ID
    ("https://musicbrainz.org/release/<mbid>", a bare MBID, "https://www.discogs.com/release/123-...",
    "discogs:123", "[r123]"); (None, "") otherwise."""
    ref = str(ref or "").strip()
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
                out.extend(subs)
                continue
        d = dict(e)
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


def discogs_parts(det, dm, http=None, s=None):
    """(album fields, [per-track fields], disc count) of a Discogs release, as the plugin
    builds them: extract_discogs_fields shaped by the settings, then the master's original
    year (and date = original date when prefer_original_date is on). Per-track fields are in
    the matcher's track order, each with its disc fields; None when the tracklist does not
    line up with the matcher's."""
    import tagger_settings
    s = s or tagger_settings.enrichment()
    det = discogs_without_repeats(det, dm)
    det = ma.discogs_one_track_movements(det)                # TRK-1: the plugin writes one track too
    album = shape_fields(dm.extract_discogs_fields(det), s, dm)
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
    seen, tracks = {}, []
    for entry, disc in zip(raw, discs):
        seen[disc] = seen.get(disc, 0) + 1
        t = dict(dm.extract_disc_fields({"number": disc}, total_discs))
        # numbered within each disc: the plugin's automatic path numbers through the whole
        # release, which would give disc 2's first track number 11
        t.update(dm.extract_track_fields(entry, seen[disc], per_disc[disc]))
        t["discnumber"], t["totaldiscs"] = str(disc), str(total_discs)
        tracks.append(t)
    return album, tracks, total_discs


_POSITION = ("discnumber", "totaldiscs", "tracknumber", "totaltracks")
_MEDIUM_KEYS = ("discsubtitle", "media", "musicbrainz_discid")


def _num(v) -> int:
    s = str((v if not isinstance(v, list) else (v or [""])[0]) or "").partition("/")[0].strip()
    return int(s) if s.isdigit() else 0


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
        merged = dict(album)
        merged.update(t)
        merged.setdefault("artist", album.get("albumartist", ""))
        out.append(_as_tags(merged))
    return out


TRACK_ONLY = ("discnumber", "totaldiscs", "disctotal", "discsubtitle")


def enrich(mb: dict, album: dict, track: dict | None, s: dict, disc_count: int, dm) -> dict:
    """One file's MusicBrainz tags with the Discogs release applied, as the plugin applies
    it in Picard (_apply_discogs_fields, then the per-track fields through the merge mode,
    "Discogs only" clearing, then artist credits settled)."""
    meta = Meta(mb)
    mode = s["merge_mode"] or "combine"
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
