"""plan.py - what TagMender is about to do, decided before anything is touched.

build(proposals, profile) turns identified (or unresolved) albums into a Plan: for every
file the tags before and after (added / changed / kept / removed / locked), the source
and destination path, the companion files that go along, artwork, the evidence and
confidence behind it, conflicts, and the albums the rules exclude (labelled Ignored or
Skipped, never Unresolved). The same Plan is shown by `tagmender plan`, the GUI, and
written as JSON with --output; `tagmender apply PLAN` executes exactly that plan.

Planning reads files and providers but never changes the music.

What decides what:
  - the profile's rules (exclusions) first, and again with the identified release;
  - field policy per field: preserve | fill_missing | replace | remove | lock;
    an existing value is only replaced at >= thresholds.replace_min_confidence;
  - files are renamed, copied or moved only at >= thresholds.organize_min_confidence;
    below it a move/copy profile tags in place (move) or does nothing (copy), and says so;
  - unresolved albums: unresolved.action = leave | mark (TAGMENDER=Unresolved and the
    reason codes) | move (to unresolved.move_to);
  - Partial albums (identification tier "partial": the album established, no release proven)
    are tagged in place with release-independent fields and TAGMENDER=Partial + the reason;
    never renamed, moved or copied;
  - album-only albums (tier "album_only": the album known, its pressing tied) get the album's
    fields and TAGMENDER=Partial too, with "ambiguous_release: album known, pressing not
    established ..." (never Resolved: no release is proven); organised as an exact album is;
  - a value equal to what the file has is not written again (repeat runs rewrite nothing).
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

import exclusions as ex
import filenames as fnm
import naming
import payload
import reasons
import tagio
import tidy

PLAN_VERSION = 1
AUDIO_EXT = ex.AUDIO_EXT
# an album folder holding any of these has its cover already (as CoverUp's writer sees it)
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff", ".bmp", ".jfif")
CUE_LIKE = {".cue", ".m3u", ".m3u8"}
EMBEDDABLE = {".flac", ".mp3", ".m4a", ".mp4", ".aiff", ".aif", ".wav", ".ogg", ".oga", ".opus"}


def _shop_page(values):
    """A TAGMENDER_SOURCE that is a web shop's or encyclopedia's page (Deezer, iTunes,
    Bandcamp, Wikipedia...), not a database's (Discogs, MusicBrainz) release page."""
    url = str((values or [""])[0] or "").casefold()
    if not url.startswith(("http://", "https://")):
        return False
    host = url.split("/")[2] if url.count("/") >= 2 else ""
    return not any(host == d or host.endswith("." + d) for d in ("discogs.com", "musicbrainz.org"))


@dataclass
class Proposal:
    """One album (or single file) as identification left it."""
    source_dir: str
    files: list                         # paths, in track order
    targets: list = field(default_factory=list)   # per file: {field: [values]} (empty when unresolved)
    status: str = "identified"          # identified | unresolved
    # how far identification got: exact (the release is proven) | album_only (the album, its
    # pressing tied) | recording (a single file's recording) | partial (the album established,
    # no release proven: release-independent fields only, TAGMENDER=Partial) | unresolved
    tier: str = ""
    provider: str = ""                  # musicbrainz | discogs | web | ...
    release_id: str = ""
    url: str = ""
    confidence: float = 0.0             # the matcher's score (0-100); a heuristic, not a probability
    margin: float | None = None
    why: list = field(default_factory=list)          # reasons it is not more certain
    evidence: list = field(default_factory=list)     # what agreed
    reason: str = ""                    # unresolved: in words
    reason_codes: list = field(default_factory=list)
    provenance: dict = field(default_factory=dict)   # field -> provider
    provider_errors: dict = field(default_factory=dict)   # provider -> error (recorded, not fatal)
    release: dict = field(default_factory=dict)      # identified album/albumartist/genre, for rules
    artwork: dict | None = None         # {"front": staged file, "source": ..., "width": .., "height": ..}
    sidecars: dict = field(default_factory=dict)     # audio file -> text of a new .lrc next to it
    notes: list = field(default_factory=list)        # for the album's plan notes (e.g. which files are which)
    artist_photo: dict | None = None    # {"image": staged file, "source", "artist"} (Enthusiast and up)
    # the releases the matcher scored best, for the run report's suggestions (engine.candidates_of):
    # [{"source", "id", "url", "ref", "score", "title", "lengths"}]
    candidates: list = field(default_factory=list)
    # (engine.scans_of; only "printed" is acted on: _files_on_evidence): what the album's scans showed about the chosen
    # release, {"state": proven|agrees|differs|unread, "found": [kinds], "against": [kinds]}
    # (None: no scans, or OCR did not run), and how the release was found: "" (plain search),
    # disc_id, link, scan_search, barcode_lookup or fingerprint (None: not known)
    scans: dict | None = None
    via: str | None = None
    # what lets an album be filed below organize_min_confidence (_files_on_evidence):
    # all_lengths: the release holds the files' tracks and every length agrees (exact tier);
    # no_lengths_tie: artist, title and tracks match, only entries of this album tie, and they
    # list no lengths to score (album_only tier)
    all_lengths: bool = False
    lengths_missing: bool = False       # exact tier, and the release lists no track lengths to compare
    most_lengths: bool = False          # BAR-1b: the files are the release's tracks, half their lengths agree at least
    toc_proven: bool = False            # TOC-1: your disc's TOC is this release's (exact disc ID), its title agreeing
    own_number: bool = False            # FIL-1: your copy's own barcode or catalogue number proves the release
    guessed_pick: bool = False          # FIL-1: the pressing was picked by country order / original date (a guess)
    no_lengths_tie: bool = False
    # known_from_tags: no release fits; the files' own tags name the album (engine._known_from_own_tags)
    known_from_tags: bool = False
    # WEB-1: why the album is planned but held (unresolved.action "leave": a web album found
    # is shown, unticked, and applied only when you tick it); "" when it is not held
    held: str = ""


def held(entry: dict) -> bool:
    """WEB-1: the album is held - planned, but applied only once you ticked it (held_ok)."""
    return bool(entry.get("held")) and not entry.get("held_ok")


def _fp(path):
    st = os.stat(path)
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "ino": st.st_ino, "ctime_ns": st.st_ctime_ns}


def has_work(entry: dict) -> bool:
    """The album has something to do: a file to write, move or copy, a companion, a new
    cover file or cover to embed, or a lyrics file."""
    art = entry.get("artwork") or {}
    return any(fe["op"] != "none" for fe in entry.get("files") or []) or bool(entry.get("companions")) \
        or bool(entry.get("sidecars")) or bool(art.get("file")) or bool(art.get("embed_into")) \
        or bool((entry.get("artist_photo") or {}).get("embed_into"))


def _vals(v):
    if v is None:
        return []
    return [str(x) for x in (v if isinstance(v, list) else [v]) if str(x) != ""]


def _riff_fields(vals: dict, unset=()) -> dict:
    """A WAV's RIFF INFO as Picard's WAVFile._save writes it from the file's fields: every
    comment field - a described or other-language one too - goes to ICMT, the last one
    written winning (the file's own first, then the script's)."""
    out = {k: list(vals[k]) for k in tagio.RIFF_INFO if vals.get(k) and k not in unset}
    comments = [k for k in vals if (k == "comment" or k.startswith("comment:")) and _vals(vals.get(k))
                and k not in unset]
    if comments:
        out["comment"] = _vals(vals[comments[-1]])
    return out


def _plain_number(v) -> str:
    """A track or disc count without its leading zeros ("03" -> "3"); anything else as it is."""
    t = str(v).strip()
    return str(int(t)) if t.isdigit() and t.isascii() else str(v)


def _threshold(profile, key) -> float:
    """A profile's threshold, raised by Extra-Careful Matching (profiles.threshold)."""
    import profiles
    return profiles.threshold(profile, key)


def _organize_floor_words(profile) -> str:
    """The organize_min_confidence the plan used, for a note (raised by Extra-Careful Matching)."""
    used = _threshold(profile, "organize_min_confidence")
    raised = used > float(profile["thresholds"]["organize_min_confidence"])
    return f"{used:g}" + (" (Extra-careful matching)" if raised else "")


def _files_on_evidence(prop, profile, conf) -> str:
    """Why an album below organize_min_confidence is filed all the same, or "": the score is a
    heuristic, and what is known about the copy can outweigh it. Never with Extra-Careful
    Matching (it asks for more proof, not less)."""
    if (profile.get("matching") or {}).get("extra_careful"):
        return ""
    tier = tier_of(prop)
    if tier == "album_only" and getattr(prop, "no_lengths_tie", False):
        return "artist, title and tracks match, and only entries of this album tie (they list no track lengths)"
    if tier == "album_only" and getattr(prop, "known_from_tags", False):
        return "your files name the album, and the databases list this artist's album"
    lengths = getattr(prop, "all_lengths", False)
    if tier == "exact" and lengths and conf >= 80.0 and (getattr(prop, "scans", None) or {}).get("printed"):
        # (a low score here is a folder name that does not say the artist, not doubt about the release)
        return "your scans show its barcode or catalogue number and every track length agrees"
    guessed = bool(getattr(prop, "guessed_pick", False))
    if tier == "exact" and getattr(prop, "toc_proven", False) and not guessed:
        # TOC-1: the disc ID is a hash of your disc's whole TOC, and it fits your files to the
        # sector: the score is low only for an artist credited otherwise and lengths the
        # database lists wrong (Food - Food, filed as Iain Bellamy). Not when pressings sharing
        # the disc ID tied and the country order chose (no margin)
        return "your disc's table of contents is this release's (exact disc ID) and its title agrees"
    if tier == "exact" and getattr(prop, "own_number", False) and lengths and not guessed:
        # FIL-1: your copy's own catalogue number or barcode (its tags, the folder name) is on
        # the release and every track length agrees: the score is low only for a folder title
        # that says more than the database's (ABBA - GOLD. 40th Anniversary Edition (2014,
        # Remastered, Deluxe Edition, 3CD, Polar, 060253740130): 87.4)
        return "your copy's own catalogue number or barcode is on it and every track length agrees"
    if tier == "exact" and lengths and not guessed and \
            any("pressing chosen by disc ID" in str(e) for e in getattr(prop, "evidence", None) or ()):
        # FIL-1: the disc ID chose this pressing over the others and every length agrees (a
        # negative margin is the higher-scoring other pressings it ruled out: Allan Holdsworth -
        # All Night Wrong, 94.7)
        return "your disc ID chose this pressing and every track length agrees"
    sc = getattr(prop, "scans", None) or {}
    if tier == "exact" and conf >= max(90.0, _threshold(profile, "replace_min_confidence")) and \
            sc.get("printed") and sc.get("state") == "proven" and not sc.get("against") and \
            (lengths or getattr(prop, "most_lengths", False)):
        # BAR-1: your scans print its barcode or catalogue number and show nothing against it:
        # the release is proven though some lengths differ (a vinyl rip's run-in and gaps:
        # Pallas - The Sentinel [VinylRip], 4 of 6 lengths, 93.6) - at a score its tags are
        # replaced at (replace_min_confidence, 90 at least): a weaker one is not filed on them
        return "your scans show its barcode or catalogue number and nothing against it"
    if tier == "exact" and (lengths or getattr(prop, "lengths_missing", False)) and \
            conf >= max(90.0, _threshold(profile, "auto_min_confidence")) and prop.margin is not None and \
            prop.margin >= _threshold(profile, "auto_min_margin"):
        return ("every track length agrees" if lengths else "the release does not list track lengths to compare") + \
            " and no other release is close"
    return ""


def diff_tags(before: dict, target: dict, profile, confidence: float, extra_removals=()):
    """(changes {field: [..] | None}, rows [{field, before, after, action}])."""
    changes, rows = {}, []
    replace_ok = confidence >= _threshold(profile, "replace_min_confidence")
    fields = list(dict.fromkeys(list(target) + list(extra_removals)))
    for f in fields:
        if f.startswith("~"):
            continue
        b, a = _vals(before.get(f)), _vals(target.get(f))
        pol = profile.policy(f)
        if f in extra_removals and f not in target:
            if not (replace_ok or f.startswith("tagmender") or pol == "remove"):
                if b:                             # (a match too uncertain to replace a tag deletes none either)
                    rows.append({"field": f, "before": b, "after": b,
                                 "action": "kept (confidence below replace threshold)"})
                continue
            pol = "remove"
        if pol == "lock":
            if a != b:
                rows.append({"field": f, "before": b, "after": b, "action": "locked"})
            continue
        if pol == "remove":
            if b:
                changes[f] = None
                rows.append({"field": f, "before": b, "after": [], "action": "removed"})
            continue
        if pol == "preserve":
            if a != b and b:
                rows.append({"field": f, "before": b, "after": b, "action": "preserved"})
            continue
        if not a or a == b:
            continue
        if not b:
            changes[f] = a
            rows.append({"field": f, "before": b, "after": a, "action": "added"})
        elif pol == "fill_missing":
            rows.append({"field": f, "before": b, "after": b, "action": "preserved"})
        elif replace_ok or f.startswith("tagmender"):
            changes[f] = a
            rows.append({"field": f, "before": b, "after": a, "action": "changed"})
        else:
            rows.append({"field": f, "before": b, "after": b, "action": "kept (confidence below replace threshold)"})
    # policy "remove" for fields the target does not mention
    for f, pol in (profile["field_policy"].get("fields") or {}).items():
        if pol == "remove" and f not in fields and _vals(before.get(f)):
            changes[f] = None
            rows.append({"field": f, "before": _vals(before.get(f)), "after": [], "action": "removed"})
    return changes, rows


# the release IDs a file may already carry, and the database each names
RELEASE_ID_WORDS = {"musicbrainz_albumid": "MusicBrainz release ID", "discogs_release_id": "Discogs release ID"}
RELEASE_ID_DB = {"musicbrainz_albumid": "MusicBrainz", "discogs_release_id": "Discogs"}


def _release_id_notes(rows) -> list:
    """The release IDs already in a file that this plan replaces or removes, in words, set on
    their rows ("note") and returned for the album's notes: what the files said is never
    changed out of sight (the Sources setting may replace or remove the other database's ID)."""
    notes = []
    for r in rows:
        words = RELEASE_ID_WORDS.get(r.get("field"))
        if not words or not r.get("before") or r.get("action") not in ("changed", "removed"):
            continue
        old = "; ".join(r["before"])
        # (the same, for the app to put in your language: note_kind, note_db, note_id)
        r.update(note_kind="replaced" if r["action"] == "changed" else "removed",
                 note_db=RELEASE_ID_DB[r["field"]], note_id=old)
        if r["action"] == "changed":
            r["note"] = f"replaces your {words} {old}"
            notes.append(f"{r['note']} with {'; '.join(r['after'])}")
        else:
            r["note"] = f"removes your {words} {old}"
            notes.append(r["note"])
    return notes


def _release_ids(prop) -> frozenset:
    """The release IDs this album will carry (MusicBrainz and/or Discogs), else its title."""
    ids = set()
    for t in prop.targets or []:
        ids |= _ids_of(t)
    if not ids and prop.release_id:
        ids.add(f"id:{prop.release_id}")
    if not ids:
        ids.add("title:" + str(((prop.release or {}).get("album") or [""])[0]).casefold())
    return frozenset(ids)


def _ids_of(tags: dict) -> set:
    out = set()
    for k, pfx in (("musicbrainz_albumid", "musicbrainz"), ("discogs_release_id", "discogs")):
        for v in tags.get(k) or []:
            out.add(f"{pfx}:{v}")
    return out


def _other_release_there(album_dir: Path, me: frozenset, ignore: set, digests=None) -> bool:
    """album_dir already holds music explicitly tagged as a different release: files that
    carry release IDs and share none with this album. Files of this plan (moving away or
    this album's own, under any name) and untagged files never count. digests: optional
    callable giving the audio digests of this album's own files, called only once a file
    tagged as another release is found."""
    if not album_dir.is_dir():
        return False
    for dirpath, dirs, names in os.walk(album_dir):
        dirs[:] = [d for d in dirs if _DISC_NAME.match(d)]
        for n in names:
            p = os.path.join(dirpath, n)
            if Path(n).suffix.lower() not in AUDIO_EXT or n.startswith("."):
                continue
            try:
                st = os.stat(p)
            except OSError:
                continue
            if p in ignore or (st.st_dev, st.st_ino) in ignore:
                continue
            try:
                ids = _ids_of(tagio.read(p))
            except Exception:
                continue
            if ids and not (ids & me):
                try:
                    if digests is not None:
                        ignore = ignore | digests()
                        digests = None
                    if ("digest", payload.digest(p)) in ignore:
                        continue                         # an earlier copy of this album's own audio
                except Exception:
                    pass
                return True
    return False


def db_text(s) -> str:
    """s as the state database can store it (see executor.db_text)."""
    s = str(s)
    try:
        s.encode("utf-8")
        return s
    except UnicodeEncodeError:
        return "bytes:" + os.fsencode(s).hex()


def db_path(s: str) -> str:
    if s.startswith("bytes:"):
        try:
            return os.fsdecode(bytes.fromhex(s[6:]))
        except ValueError:
            pass
    return s


def copies_of(src, under=None) -> list:
    """Where earlier jobs copied this file (newest first), from the state database; with
    `under`, only the copies inside that folder (this profile's destination: another copy
    profile's library, or the one this profile copied to before its destination changed,
    is not this one's)."""
    import sqlite3
    try:
        db = sqlite3.connect(ex.STATE_DIR / "state.db", timeout=30)
        try:
            rows = db.execute("SELECT dst FROM copies WHERE src=? ORDER BY ts DESC",
                              (db_text(src),)).fetchall()
        finally:
            db.close()
    except sqlite3.Error:
        return []
    out = [db_path(r[0]) for r in rows]
    if under is not None:
        root = os.path.realpath(os.path.expanduser(str(under)))
        out = [d for d in out if os.path.realpath(d).startswith(root.rstrip(os.sep) + os.sep)]
    return out


def _legacy_names(tags: dict) -> dict | None:
    """The tags as TagMender before 72671d5 named folders with them: an album artist with
    no ALBUMARTISTS tag split at ",", "&" and " and " (so "Simon & Garfunkel" was filed
    under "Garfunkel & Simon"). None when that gives the same name."""
    if tags.get("albumartists"):
        return None
    aa = ((tags.get("albumartist") or tags.get("artist") or [""])[0]) or ""
    parts = [a.strip() for a in re.split(r"\s*(?:,|&| and )\s*", aa) if a.strip()]
    if len(parts) < 2 or sorted(dict.fromkeys(parts), key=str.casefold) == [aa]:
        return None
    return dict(tags, albumartists=parts)


def _same_bytes(there: Path, c: Path, pairs: dict, profile) -> bool:
    """there already holds what c would become at that place (itself, or its rewrite)."""
    try:
        have = there.read_bytes()
    except OSError:
        return False
    if c.suffix.lower() in CUE_LIKE:
        rel = {os.path.relpath(s_, c.parent): os.path.relpath(d_, there.parent) for s_, d_ in pairs.items()}
        rel.update({str(s_): str(d_) for s_, d_ in pairs.items()})
        rw = _cue_rewrites(c, rel)
        if rw and rw[0] is not None:
            return have == rw[0].encode(rw[1])
    try:
        return have == c.read_bytes()
    except OSError:
        return False


def _fit_name(name: str, limits: dict) -> str:
    """A companion's own name, made safe for the destination (Windows-forbidden characters)
    and short enough to take a collision suffix."""
    compat = limits.get("compat", "windows")
    stem, ext = os.path.splitext(name)
    stem = fnm.safe_component(stem, compat, file_stem=True) if stem else stem
    limit = int(limits.get("max_name_bytes", 255)) - fnm.SUFFIX_LEN - len(ext.encode("utf-8", "surrogateescape"))
    if len(stem.encode("utf-8", "surrogateescape")) > limit:
        stem = fnm.truncate_words(stem, limit, compat)
    return stem + ext


def same_entry(a, b) -> bool:
    """a and b are one name of one file - one directory entry - however the paths spell it
    (through a relative or symlinked folder, or in another letter case or Unicode form on a
    filesystem that treats them alike); False for two hard links of one file, and for two
    different files."""
    a, b = Path(a), Path(b)
    try:
        if not os.path.samefile(a, b) or not os.path.samefile(a.parent, b.parent):
            return False                                     # two files, or two folders: two names
    except OSError:
        return False
    if a.name == b.name:
        return True
    if fnm.fold(a.name) != fnm.fold(b.name):
        return False                                         # two names in one folder: hard links
    try:                       # both spellings listed: two hard links on a case-sensitive disk
        names = os.listdir(a.parent)
    except OSError:
        return True
    return not (a.name in names and b.name in names)


_one_name = same_entry


MOUNT_PARENTS = ("/media", "/mnt", "/run/media", "/srv", "/net")
FSTAB = "/etc/fstab"
# fstab entries that are never a share a library lives on
_NOT_SHARES = {"swap", "proc", "sysfs", "devpts", "tmpfs", "devtmpfs", "none"}


def mount_point(folder) -> str:
    """The mount point folder lies on ("/" when on the system disk)."""
    p = Path(os.path.realpath(os.path.expanduser(str(folder))))
    while p != p.parent and not os.path.ismount(p):
        p = p.parent
    return str(p)


def fstab_mounts(path=None) -> list[str]:
    """The mount points /etc/fstab names for disks and shares (not / itself, swap or memory
    filesystems), longest first."""
    try:
        lines = Path(path or FSTAB).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        parts = ln.split("#", 1)[0].split()
        if len(parts) >= 3 and parts[1].startswith("/") and parts[1] != "/" and parts[2] not in _NOT_SHARES:
            out.append(os.path.normpath(parts[1].replace("\\040", " ")))
    return sorted(set(out), key=len, reverse=True)


def mounted(folder, guard="auto") -> bool:
    """False when the share a folder belongs to is not mounted, so anything written there
    would land on the system disk: a folder below a mount point /etc/fstab names that is not
    mounted now; a folder under /media, /mnt, /run/media, /srv or /net with nothing mounted between it
    and that parent; with guard "always" (destinations.mounted_only), any folder that is on
    the system disk's own filesystem."""
    real = os.path.realpath(os.path.expanduser(str(folder)))
    for m in fstab_mounts():
        if real == m or real.startswith(m.rstrip(os.sep) + os.sep):
            if not os.path.ismount(m):
                return False
            break
    if guard == "always" and mount_point(real) == os.sep:
        return False
    parent = next((m for m in MOUNT_PARENTS if real.startswith(m + os.sep)), None)
    if parent is None:
        return True
    p = Path(real)
    while str(p) != parent and p != p.parent:
        if os.path.ismount(p):
            return True
        p = p.parent
    return False


_DISC_NAME = re.compile(r"^(?:cd|disc|disk|dvd|side)\s*[-_.]?\s*(?:\d+|[a-h])\b", re.I)
MAX_TREE = 5000                           # entries looked at below one album folder


SECOND_NAMES = 20                         # " (2)" ... " (21)": a second file of one name

SHARED_FOLDER_NOTE = "folder holds other music too; only each track's own files (lyrics ...) go with it"


def _companions(src_dir: Path, moved: dict, profile, shallow=False, tracks_only=False, artist="",
                single=False, extra=(), everything=False):
    """([(companion, destination)], why not): the non-audio files that go with an album
    that moves or is copied, as Picard's "move additional files" and foobar2000's "move
    entire source folder content" do:
      - in each folder holding this album's music (the album folder, its disc folders):
        the files whose extension is in the profile's "companions";
      - in folders with no music anywhere inside (Scans, covers, artwork ...): everything;
      - nothing from a folder holding music that is not moving with this album (a mixed
        folder, a bonus album in a subfolder), nor from below it.
    Each goes to the same place relative to where the music lands. Hidden files and
    symbolic links stay. moved: {source file: destination file}. extra: more extensions that
    go from the music's folders (the covers and misc folders' pictures and papers).
    everything: MOV-1 - an identified album whose folder is no watched or library root takes
    every file of its own folders, whatever its extension (else only the companion types: a
    downloads folder of loose tracks keeps its other files)."""
    exts = {e.lower() for e in profile["companions"]} | set(extra)
    moved = {Path(k): Path(v) for k, v in moved.items()}
    dmap = {}
    for s_, d_ in moved.items():
        dmap.setdefault(s_.parent, set()).add(d_.parent)
    for k, v in list(dmap.items()):
        if len(v) > 1:                     # a flat disc set going into "Disc 01", "Disc 02": its album folder
            parents = {x.parent for x in v}
            if len(parents) == 1 and all(_DISC_NAME.match(x.name) for x in v):
                dmap[k] = parents
            else:
                return [], "files of one folder go to different folders; companion files stay where they are"
    dmap = {k: next(iter(v)) for k, v in dmap.items()}
    if src_dir in dmap and _DISC_NAME.match(dmap[src_dir].name) and \
            any(v != dmap[src_dir] and v.parent == dmap[src_dir].parent for v in dmap.values()):
        dmap[src_dir] = dmap[src_dir].parent        # disc 1 in the album folder, disc 2 inside it
    if src_dir not in dmap:
        top = Path(os.path.commonpath([str(d) for d in dmap.values()]))
        if any(d != top and d.parent != top for d in dmap.values()):
            return [], "the album's folders go to different places; companion files stay where they are"
        dmap[src_dir] = top

    def audio_in(d):
        try:
            return [p for p in d.iterdir() if p.suffix.lower() in AUDIO_EXT and p.is_file()
                    and not p.name.startswith(".")]
        except OSError:
            return []
    if any(p not in moved for p in audio_in(src_dir)):
        # a shared folder (singles, a folder split into two albums): its own files stay, but each
        # moving track still takes its own "01 a.lrc" - else its lyrics are left behind, orphaned
        own = []
        for s_, d_ in sorted(moved.items()):
            for p in sorted(s_.parent.glob(glob.escape(s_.stem) + ".*")):
                if p != s_ and p.stem == s_.stem and not p.name.startswith(".") and not p.is_symlink() \
                        and p.is_file() and p.suffix.lower() not in AUDIO_EXT and p.suffix.lower() in exts:
                    own.append((p, d_.with_name(d_.stem + p.suffix)))
        return own, SHARED_FOLDER_NOTE
    tree, seen, foreign = [], 0, []
    # (an album tidied where it is - into its covers and misc folders - moves into no folder)
    targets = set() if all(s_ == d_ for s_, d_ in moved.items()) else \
        {os.path.realpath(str(x)) for x in dmap.values()}

    def overlaps(d):                       # a folder the album is moving into, or one holding it
        r = os.path.realpath(str(d))
        return any(t == r or t.startswith(r + os.sep) for t in targets)
    for dirpath, dirs, names in os.walk(src_dir):
        d = Path(dirpath)
        dirs[:] = sorted(x for x in dirs if not x.startswith(".") and not (d / x).is_symlink()
                         and not overlaps(d / x))
        if shallow:
            dirs[:] = []
        here = [d / n for n in names if not n.startswith(".")]
        if d != src_dir and any(p.suffix.lower() in AUDIO_EXT and p not in moved for p in here):
            dirs[:] = []                                   # another album's folder: left alone
            foreign.append(d)
            continue
        seen += len(here) + len(dirs)
        if seen > MAX_TREE:
            return _companions(src_dir, moved, profile, shallow=True, tracks_only=tracks_only, artist=artist,
                               single=single, extra=extra, everything=everything)[0], \
                "album folder holds too many files to look through; only its own companion files go"
        tree.append((d, here))
    music_below = set()
    for d, here in [(f_, [f_ / "x.flac"]) for f_ in foreign] + tree:
        if any(p.suffix.lower() in AUDIO_EXT for p in here):
            q = d
            while True:
                music_below.add(q)
                if q == src_dir or q == q.parent:
                    break
                q = q.parent
    by_stem = {(s_.parent, s_.stem): d_ for s_, d_ in moved.items()}
    ours = {s_.parent for s_ in moved}
    # an artist folder (one holding the artist's other albums below it, or named after the album
    # artist - not a self-titled album's own folder, "Band/Band/") with this album's tracks in it:
    #  - its own art (artist.jpg, fanart, logo, backdrops, extrafanart/ ...) and the artist's
    #    photo never go with an album;
    #  - a loose single (one track) beside the artist's albums takes nothing of the folder's
    #    own (only its own .lrc and the like): one album folder beside it is not enough to
    #    tell an artist folder from a release with, say, its remixes in a folder below.
    import artwork as _artwork
    compat_ = (profile.get("filenames") or {}).get("compat", "windows")
    discs_here = any(q.parent == src_dir and _DISC_NAME.match(q.name) for q in ours)
    loose = [s_ for s_ in moved if s_.parent == src_dir]
    others = {f_.relative_to(src_dir).parts[0] for f_ in foreign if src_dir in f_.parents}
    others = {x for x in others if not _DISC_NAME.match(x) and not _artwork._BONUS.match(x)}
    named = _artwork.named_after(src_dir, artist, compat_) and not _artwork.named_after(src_dir.parent, artist, compat_)
    # the artist's own art (artist.jpg, fanart, logo ...: never an album's) stays wherever other
    # music is below, or in a folder named after the artist; a folder.jpg stays only in what is
    # surely an artist folder - holding two or more other albums, or named after the artist and
    # holding another - else it is this album's cover (an album folder with a bonus or live
    # album inside it, an artist's only album ripped flat)
    sure = len(others) >= 2 or (named and bool(others))
    src_shared = len(loose) == 1 and not discs_here and sure
    keep = set()
    if sure:
        keep = _artwork.artist_folder_keeps(src_dir, single=loose if single else ())
    elif others or named:
        keep = {x for x in _artwork.artist_folder_keeps(src_dir) if x.stem.lower() not in _artwork.SHARED_STEMS}
    artist_folder = sure or bool(others) or named              # (its extrafanart/ ... stay too)

    def shared_ok(d):
        """With another album's music in the tree (disc 2 tagged as another title, a bonus
        album), only the folders holding this album's own music, and what is inside them,
        are this album's; the shared folder's own files (cover, Scans) stay."""
        q = d
        while True:
            if q in ours and q != src_dir:
                return True
            if q == src_dir:              # (the album's own folder: its music in it, or in its disc folders)
                return (src_dir in ours or discs_here) and not src_shared
            q = q.parent
    def whole(d):
        """MOV-1: a folder of this album alone (its music in it or below, no other album's,
        not an artist folder): EVERY file in it goes with the album - not only the profile's
        companion extensions - so the folder it leaves is empty (Popol Vuh - Sing, for Song
        Drives Away the Wolves: its .auCDtect report was left behind)."""
        return everything and not tracks_only and not foreign and not (artist_folder and d == src_dir) and \
            shared_ok(d) and (d in ours or d != src_dir)
    out = []
    for d, here in tree:
        if artist_folder and d != src_dir and d.relative_to(src_dir).parts[0].lower() in _artwork.ARTIST_ART_DIRS:
            continue                                        # the artist's extrafanart/ ...
        anchor = d
        while anchor not in dmap:
            anchor = anchor.parent
        compat = (profile.get("filenames") or {}).get("compat", "windows")
        dest_dir = dmap[anchor].joinpath(*[fnm.safe_component(x, compat) for x in d.relative_to(anchor).parts])
        for p in sorted(here):
            if p.is_symlink() or not p.is_file() or p.suffix.lower() in AUDIO_EXT:
                continue
            if d in music_below and p.suffix.lower() not in exts and not whole(d):
                continue
            track = by_stem.get((d, p.stem))                # "01 a.lrc" goes with "01 a.flac"
            if tracks_only and not track:
                continue                                    # a library root's own files stay
            if foreign and not track and not shared_ok(d):
                continue                                    # the album folder is shared with another album
            if d == src_dir and p in keep:
                continue                                    # the artist folder's own photo and art
            out.append((p, track.with_name(track.stem + p.suffix) if track else dest_dir / p.name))
    return out, ""


def _from_album_folder(comps, names, compat) -> list:
    """The naming's path components from the album's own folder down: ["Artist", "1994 - Album
    (Remaster 2024)", "01 Title"] -> the last two (a version split beside the folder it was
    in). The album's folder is the last one named after the album (a disc folder below it
    aside); else the file's own folder."""
    album = str((names.get("album") or [""])[0])
    want = fnm.fold(fnm.safe_component(album, compat))[:12] if album else ""
    last = len(comps) - 2
    if last >= 1 and _DISC_NAME.match(comps[last]):
        last -= 1
    for i in range(last, -1, -1):
        if want and want in fnm.fold(comps[i]):
            return list(comps[i:])
    return list(comps[max(last, 0):])


def _names_in(path: Path, files) -> bool:
    """Whether a text file (a cue sheet, a playlist, a rip log) names any of these files."""
    try:
        data = path.read_bytes()[:2_000_000]
    except OSError:
        return False
    text = data.decode("utf-8", "replace")
    alt = data.decode("latin-1")
    return any(Path(f).name in text or Path(f).name in alt for f in files)


TEXT_SIDES = CUE_LIKE | {".log", ".txt", ".accurip", ".md5", ".ffp", ".sfv"}


def _split_companions(vsplit, pairs: dict, profile, op, extra=()):
    """([(companion, destination)], note, {companion: operation}) of one version of a split
    (engine._versions_split): the non-audio files of the folder it was found in, as
    _companions finds them, sorted out between the versions -
      - in a folder holding only this version's music (its disc folder), or named after
        one of its tracks ("01 a.lrc"): this version's, moved with it;
      - in a folder holding only the other version's music: left for that one;
      - in a folder holding both, a cue sheet, playlist or rip log naming only this
        version's files is this version's; one naming both stays where it is;
      - the rest (the set's cover, Scans/, a log naming no file) is shared: copied to each
        version, moved with the last (so the folder empties).
    Each goes to the same place relative to where this version's music lands."""
    exts = {e.lower() for e in profile["companions"]} | set(extra)
    top = Path(vsplit["of"])
    mine = {Path(k) for k in pairs}
    others = {Path(f) for f in vsplit.get("others") or []}
    last = int(vsplit.get("index", 0)) == int(vsplit.get("count", 1)) - 1
    shared_op = op if (last and op == "move") else "copy"
    dsts = [Path(v) for v in pairs.values()]
    album_dir = Path(os.path.commonpath([str(d.parent) for d in dsts]))
    if _DISC_NAME.match(album_dir.name):
        album_dir = album_dir.parent
    mine_dirs = {f.parent for f in mine} | {Path(d) for d in vsplit.get("own_dirs") or []}
    their_dirs = {f.parent for f in others}
    by_stem = {(Path(k).parent, Path(k).stem): Path(v) for k, v in pairs.items()}
    their_stems = {(f.parent, f.stem) for f in others}
    compat = (profile.get("filenames") or {}).get("compat", "windows")
    # (a version from several disc folders: each disc's own log, cue and cover go to the disc
    # folder its tracks go to, not all to the album's)
    dmap = {}
    for k, v in pairs.items():
        dmap.setdefault(Path(k).parent, set()).add(Path(v).parent)
    dmap = {d: next(iter(ds)) for d, ds in dmap.items() if len(ds) == 1} if len(dmap) > 1 else {}

    def owner(d):
        """mine | theirs | both | shared: whose music the folder (or the nearest above it) holds."""
        q = d
        while True:
            m, t = q in mine_dirs, q in their_dirs and q not in {Path(x) for x in vsplit.get("own_dirs") or []}
            if m or t:
                return "both" if (m and t) else "mine" if m else "theirs"
            if q == top or q == q.parent:
                return "shared"
            q = q.parent

    def anchor(d):
        q = d
        while q not in mine_dirs and q != top and q != q.parent:
            q = q.parent
        return q
    out, how, notes, own_dst = [], {}, [], set()
    for dirpath, dirs, names in os.walk(top):
        d = Path(dirpath)
        dirs[:] = sorted(x for x in dirs if not x.startswith(".") and not (d / x).is_symlink())
        here = [d / n for n in names if not n.startswith(".")]
        music = [x for x in here if x.suffix.lower() in AUDIO_EXT]
        if any(x not in mine and x not in others for x in music):
            if d != top:
                dirs[:] = []                              # another album's folder: left alone
            continue
        kind = owner(d)
        if kind == "theirs":
            dirs[:] = []
            continue
        for c in sorted(here):
            if c.is_symlink() or not c.is_file() or c.suffix.lower() in AUDIO_EXT:
                continue
            if music and c.suffix.lower() not in exts:
                continue
            track = by_stem.get((d, c.stem))
            if track is not None:                          # "01 a.lrc" goes with "01 a.flac"
                out.append((c, track.with_name(track.stem + c.suffix)))
                own_dst.add(out[-1][1])
                continue
            if (d, c.stem) in their_stems:
                continue
            ours = kind == "mine"
            if kind == "both" and c.suffix.lower() in TEXT_SIDES:
                a, b = _names_in(c, mine), _names_in(c, others)
                if a and b:
                    notes.append(f"{c.name} names files of both versions: it stays where it is")
                    continue
                if b:
                    continue
                ours = a
            base = anchor(d)
            home = dmap.get(base, album_dir)
            want = home.joinpath(*[fnm.safe_component(x, compat) for x in d.relative_to(base).parts]) / c.name \
                if base != d else home / c.name
            if not ours and want in own_dst:
                continue                                   # (this version's own file of that name goes there)
            out.append((c, want))
            if ours:
                own_dst.add(want)
            else:
                how[c] = shared_op
    out = [(c, w) for c, w in out if c not in how or w not in own_dst]
    return out, "; ".join(notes), how


def _split_together(albums):
    """The versions of one split go together or not at all: when one cannot be planned (a
    conflict, skipped), or two would go to the same folder, none is split."""
    by = {}
    for a in albums:
        if a.get("split"):
            by.setdefault(a["split"]["of"], []).append(a)
    for of, group in by.items():
        folders = [fnm.fold(a["split"].get("folder") or "") for a in group]
        why = ""
        if len(group) < max(int(a["split"].get("count") or 1) for a in group):
            why = "a version of this album is not in this plan"
        elif any(a["status"] not in ("ready", "unchanged") for a in group):
            bad = next(a for a in group if a["status"] not in ("ready", "unchanged"))
            why = (f"version {bad['split']['index'] + 1} cannot be split: "
                   + ("; ".join(bad.get("conflicts") or []) or bad.get("label") or bad["status"]))
        elif len(set(folders)) < len(folders):
            why = "two versions would go to the same folder"
        if not why:
            # (the files each version's run puts in the folder are planned too: the version
            # that stays is renamed onto names the other one held, and that one, run after,
            # must not take them for tracks added since the plan was made)
            for a in group:
                a["split_with"] = list(dict.fromkeys(list(a.get("split_with") or []) + [
                    fe["dst"] for b in group if b is not a for fe in b["files"] if fe.get("dst")]))
            stays = any(os.path.realpath(a["split"].get("folder") or "") == os.path.realpath(of) for a in group)
            if stays:
                # (one version stays in the folder, named as it already is: what the versions
                # share stays there too, copied to the others - never moved away from it)
                for a in group:
                    for c in a["companions"]:
                        if c["op"] == "move" and c.get("shared"):
                            c["op"] = "copy"
            continue
        for a in group:
            if a["status"] in ("ready", "unchanged"):
                a["status"] = "blocked"
                a["conflicts"].append(f"the versions in {of} are split together or not at all: {why}")


def _album_artist(prop) -> tuple[str, bool]:
    """(the album artist, whether the release is a single): from the release matched, else
    (an unresolved album) from the tracks' own tags."""
    t = prop.targets[0] if prop.targets else {}
    who = (t.get("albumartist") or t.get("artist") or [""])[0] if t else ""
    if not who:
        from engine import _first
        for f in prop.files[:3]:
            m = ex.read_meta(Path(f))
            who = (_first(m, "albumartist") or _first(m, "artist") or "").strip()
            if who:
                break
    single = "single" in [str(x).lower() for x in (t.get("releasetype") or [])]
    return who or "", single


def _holds_other_music(folder: Path, entry, group=None) -> bool:
    """folder - or any folder below it - holds, or will hold, music that is not this album's
    (an artist folder with a loose single and the artist's albums: its folder.jpg is no one
    album's). The same rule the artwork step used to choose the album's covers
    (artwork._other_music). group: every file of the album as found (its second copies too)."""
    import artwork as _artwork
    ours = {fe["src"] for fe in entry["files"]} | {fe["dst"] for fe in entry["files"]} \
        | {s_.get("file") for s_ in entry.get("skipped") or [] if s_.get("file")} | {str(g) for g in group or []}
    return _artwork._other_music(Path(folder), sorted(ours))


def _pulled_small_front(entry, art, looks, p):
    """The front cover the covers folder rule brings beside the music from a folder of scans
    ("covers/Artwork/front.jpg" -> "front.jpg") when it is smaller than artwork.min_px and the
    cover found is larger: its path, for the larger cover to replace it as it moves (as a small
    cover.jpg already beside the music is), else None. Without this, the small one would be
    replaced only by the next plan, so planning again would not be a no-op."""
    a = p["artwork"]
    low = int(a.get("min_px") or 0)
    if a.get("replace", "larger_only") != "larger_only" or low <= 0 or not a.get("files", True):
        return None
    import artwork as _artwork
    homes = {Path(d) for d in looks if d is not None}
    for c in entry["companions"]:
        src, dst = Path(c["src"]), Path(c["dst"])
        if c["op"] not in ("move", "copy") or c.get("tidy") or c.get("rewrite") or \
                dst.parent not in homes or src.parent in homes or dst.suffix.lower() not in IMAGE_EXT:
            continue
        try:
            old, new = _artwork._size_of(src.read_bytes()), _artwork._size_of(Path(art["front"]).read_bytes())
        except OSError:
            return None
        if old and new and min(old) < low and min(new) > min(old):
            return str(src)
        return None
    return None


def _spelled_names(raw: dict, changes: dict) -> dict:
    """{field: its spelling} of the fields written whose name is not in lower case (a custom
    tag a tagger script made, "MyTag"): Picard writes such a tag as it is spelled."""
    return {tagio.field_name(k): k for k in raw if tagio.field_name(k) != k and tagio.field_name(k) in changes}


def _linked_tracks(entry, roots=None) -> list:
    """Conflicts for the album's tracks that are symbolic links (a library of links into a
    download folder) and would be moved or written: moving one leaves a copy where the link
    was, and TagMender never writes through a link. roots: the plan's folders - a track in a
    folder that is a link to a place outside them would be written or removed there, which
    the executor refuses (as for a destination): said here, before Apply."""
    out = []
    for fe in entry["files"]:
        s_ = Path(fe.get("copy_of") or fe["src"])
        writes = fe["op"] == "move" or (fe["op"] == "tag" and fe["src"] == str(s_))
        if s_.is_symlink() and writes:
            out.append(f"{s_.name} is a symbolic link (to {os.readlink(s_)}): TagMender does not move or "
                       "write through links - replace it with the file, or use a copy profile")
        elif writes and roots:
            real = os.path.realpath(str(s_.parent))
            if not any(real == r or real.startswith(r.rstrip(os.sep) + os.sep) for r in roots):
                out.append(f"{s_.name} is in {s_.parent}, which leads to {real}, outside the folders this "
                           "profile may change: TagMender does not move or write files there - add that "
                           "folder, or use a copy profile")
    return out


def _first_values(raw: dict, changes: dict) -> dict:
    """{field: [its first value]} of the ID3v1 fields written with several values (an
    ID3v2.3 tag joins them; its ID3v1 tag takes the first, as Picard's)."""
    return {k: [str(raw[k][0])] for k in ("title", "artist", "album", "genre")
            if changes.get(k) and len(raw.get(k) or []) > 1}


def _sheet_key(name: str) -> str:
    """A file name as a cue sheet or playlist entry is matched: letter case and Unicode form
    aside (a sheet written on a Mac names "Café" composed, the files copied from it spell it
    decomposed - or the other way)."""
    return unicodedata.normalize("NFC", name).casefold()


def _cue_rewrites(path: Path, renames: dict):
    """New text of a cue sheet or playlist whose entries name files being renamed or moved,
    or None. renames: {old path relative to the sheet's folder: new path relative to where
    the sheet goes}. Every entry is replaced in one pass (so 01->02 and 02->03 never chain),
    as a quoted FILE name or as a whole playlist line; "\\" separators count as "/"."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    encs = ("utf-16",) if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8", "cp1252", "latin-1")
    text = None
    for enc in encs:
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return None
    def as_sheet(name):
        """The ways the sheet may spell a file name: as text, and - for a name that is not
        valid UTF-8 on disk (an old Latin-1 rip: "H\\udce9lls") - its bytes read the way the
        sheet is read ("Hélls"), which is how a playlist made beside such files names them."""
        out = [] if any("\udc80" <= ch <= "\udcff" for ch in name) else [name]
        try:
            b = os.fsencode(name).decode(enc)
        except (UnicodeDecodeError, UnicodeEncodeError):
            b = None
        if b and b not in out:
            out.append(b)
        return out

    table, stems, by_bytes = {}, {}, set()
    for old, new in renames.items():
        old_n, new_n = old.replace("\\", "/"), new.replace("\\", "/")
        if old_n != new_n:
            new_s = (as_sheet(new_n) or [new_n])[0]
            for o_s in as_sheet(old_n):
                table[_sheet_key(o_s)] = new_s
                if o_s != old_n:
                    by_bytes.add(_sheet_key(o_s))      # (named by the bytes on disk)
                o, n = os.path.splitext(o_s), os.path.splitext(new_s)
                if o[1].lower() in AUDIO_EXT:
                    stems.setdefault(_sheet_key(o[0]), set()).add(n[0])
    if not table:
        return None

    def swap(name):
        key = name.replace("\\", "/")
        while key.startswith("./"):
            key = key[2:]
        hit = table.get(_sheet_key(key))
        if hit is not None:
            swapped.append((_sheet_key(key) in by_bytes, hit))
        if hit is None:          # EAC's cue names the rip's WAV files: "01 a.wav" for "01 a.flac"
            stem, ext = os.path.splitext(key)
            got = stems.get(_sheet_key(stem)) if ext.lower() in AUDIO_EXT else None
            if got and len(got) == 1:
                hit = next(iter(got)) + ext              # the cue's own extension, as it had it
        return hit

    swapped = []
    bom = text.startswith("\ufeff")
    if bom:
        text = text[1:]

    def line(ln):
        m = re.match(r'^(\s*FILE\s+")([^"]+)(".*)$', ln, re.I)
        if m and swap(m.group(2)):
            return m.group(1) + swap(m.group(2)) + m.group(3)
        # (a name without spaces may stand unquoted - FILE 01.flac WAVE -, as the matcher reads
        # it too; a new name with spaces gets its quotes)
        m = re.match(r'^(\s*FILE\s+)([^"\s]+)(\s+\S+\s*)$', ln, re.I)
        if m and swap(m.group(2)):
            new_name = swap(m.group(2))
            return m.group(1) + (f'"{new_name}"' if re.search(r"\s", new_name) else new_name) + m.group(3)
        body = ln.rstrip("\r")
        if body.strip() and not body.lstrip().startswith("#") and swap(body.strip()):
            return ln[:len(ln) - len(ln.lstrip())] + swap(body.strip()) + ln[len(body):]
        return ln
    new = "\n".join(line(ln) for ln in text.split("\n"))
    if new == text:
        return None
    if bom:
        new = "\ufeff" + new
    if enc not in ("utf-8", "utf-16") and any(b for b, _h in swapped) and \
            any(not h.isascii() for _b, h in swapped):
        # (a sheet that named its files by their Latin-1 bytes on disk: the new names are
        # UTF-8 on disk, so the sheet is written in UTF-8 - its entries name them byte for byte)
        enc = "utf-8"
    try:
        new.encode(enc)
    except UnicodeEncodeError:
        return None, enc                                # the new names cannot be written in its encoding
    return new, enc


class Planner:
    def __init__(self, profile, job_id=None, rules=None, overrides=None, extra_roots=(), edits=None):
        self.profile = profile
        self.edits = edits or {}   # your edits from the app: {album folder: [edit]} (apply_edits)
        self.extra_roots = [os.path.realpath(os.path.expanduser(str(r))) for r in extra_roots]
        self.extra_roots = [os.path.dirname(r) if os.path.isfile(r) else r for r in self.extra_roots]
        self.job = job_id or time.strftime("%Y%m%d-%H%M%S-") + os.urandom(2).hex()
        self.rules = ex.load_rules(profile.name if profile.name != "default" else "") if rules is None else rules
        self.overrides = ex.State().overrides() if overrides is None else overrides
        self.claimed = {}          # folded destination path -> source (collisions inside the plan)
        self.created_dirs = set()
        self._dirs = {}
        self._inodes = {}
        self._album_dirs = {}
        self._planned = set()
        self._resident = []
        self._split_roots = set()  # folders a split's versions are made in, beside their folder

    def _picard(self) -> dict:
        """Picard's settings this profile follows (tagger_settings.tag_settings), once per plan."""
        if not hasattr(self, "_picard_set"):
            get = getattr(self.profile, "picard_tags", None)
            self._picard_set = get() if get else {}
        return self._picard_set

    def _log(self):
        import logging
        return logging.getLogger("tagmender")

    def _scripts(self) -> list:
        import tagger_settings
        return tagger_settings.tagger_scripts(self._picard())

    def _scripted(self, f: Path, before: dict, target: dict) -> dict:
        """The file's target after Picard's tagger scripts, run as Picard runs them on a file
        (Track.update_file_metadata, "clear existing tags" off): on the file's own tags
        overlaid with the release's, with Picard's hidden variables and the file's (%_extension%,
        %_format% ...). What a script $delete-s goes to "~remove"; the variables it $set-s
        stay for the naming script. Fields the profile's level leaves alone stay as they are."""
        import engine as _engine
        import levels as _levels
        hidden = {k: list(v) for k, v in target.items() if k.startswith("~") and k != "~remove"}
        hidden = {**tagio.file_vars(f), **hidden}
        gone_ = set(target.get("~remove") or [])        # (what the release deletes: no script sees it)
        # (a file's own ID3 user tag as it is spelled - "CUSTOM": Picard's names are case-sensitive)
        spell = {k: v for k, v in tagio.spellings(f).items() if k not in target}
        # (and its user tags spelled like a field it keeps elsewhere - TXXX:TOTALTRACKS beside TRCK,
        # read as "~custom:TOTALTRACKS" - by their own names: %TOTALTRACKS%, $delete(TOTALTRACKS))
        cust = {k[8:]: list(v) for k, v in before.items()
                if k.startswith("~custom:") and k[8:] != k[8:].lower() and k not in gone_}
        tags = {**{spell.get(k, k): list(v) for k, v in before.items() if not k.startswith("~") and k not in gone_},
                **cust,
                **{k: list(v) if isinstance(v, list) else [v] for k, v in target.items() if not k.startswith("~")}}
        # (what the scripts were given: the script editor's preview runs them on this, not again
        # on what they made)
        self.__dict__.setdefault("_unscripted", {})[str(f)] = {k: [str(x) for x in v] for k, v in tags.items()}
        out, gone = _engine.run_tagger_scripts(self._scripts(), tags, hidden, self._log(), strip=False)
        import plugins
        if plugins.has_tag_processors():          # (then the plugins' tag changes: shown in the plan as any)
            was = set(out)
            out = plugins.process_tags(out, {"file": str(f),   # (the album's folder: CD1/ CD2/ in it too)
                                             "album_folder": str(getattr(self, "_album_dir", None) or Path(f).parent),
                                             "profile": getattr(self.profile, "name", "")})
            gone = list(gone) + [k for k in was if k not in out and k not in gone]
        back = {v: k for k, v in spell.items()}
        out = {back.get(k, k): v for k, v in out.items()}
        # ($set(Title,X), $set(GENRE,Jazz) - a field TagMender writes, spelled otherwise: that
        # field, the script's value winning; Picard, case-sensitive, would add a TXXX:Title -
        # docs/FEATURES.md)
        known = {k for k in target if k == k.lower()}     # (the release's fields - not a custom tag of the file's)
        for k in [k for k in out if k != k.lower() and k.lower() in known and k not in tags]:
            v_ = out.pop(k)
            if out.get(k.lower()) == tags.get(k.lower()):      # (the script set "title" itself too: that)
                out[k.lower()] = v_
        tags = {back.get(k, k): v for k, v in tags.items()}
        # ($delete(custom) is not the file's "CUSTOM": left as it is, as Picard leaves it)
        gone = [back.get(g, g) for g in gone if not (g.lower() in spell and g != spell[g.lower()])]
        cmap = {n: "~custom:" + n for n in cust}
        out = {cmap.get(k, k): v for k, v in out.items()}
        tags = {cmap.get(k, k): v for k, v in tags.items()}
        gone = [cmap.get(g, g) for g in gone]
        # (what a script $unset: not deleted from the file, but gone from what Picard writes into a
        # WAV's RIFF INFO, which it makes from the track's fields)
        self.__dict__.setdefault("_unset", {})[str(f)] = {k for k in tags if k not in out and k not in gone}
        # (the release's fields, and what a script changed: never the file's other fields as
        # they are - a copy's own edits stay the copy's)
        new = {k: v for k, v in out.items() if v and (k in target or tags.get(k) != v)}
        new.update({k: v for k, v in hidden.items() if v})
        remove = list(target.get("~remove") or []) + [g for g in gone if g not in new]
        if remove:
            new["~remove"] = list(dict.fromkeys(remove))
        # (a field the level does not write: never written, whatever a script made of it)
        kept = _levels.restrict(new, self.profile, tier=getattr(self, "_album_tier", ""))
        for k in list(new):
            if k not in kept and not k.startswith("~"):
                del new[k]
        if new.get("~remove"):
            new["~remove"] = list(kept.get("~remove") or [])
        return new

    def _name_limits(self, dest_root) -> dict:
        """The profile's file name limits, with Picard's Windows path shortening when Windows
        compatibility is on and Picard's "Windows long path support" off (its default off
        Windows): the room the destination takes below its mount point reserved, as Picard
        reserves it (a drive "X:\\" and the separator)."""
        lim = dict(self.profile["filenames"])
        if lim.get("compat", "windows") == "windows" and not self._picard().get("windows_long_paths"):
            base = os.path.abspath(str(dest_root))
            mount = base                                  # (as Picard finds it: on the path as given)
            while mount and not os.path.ismount(mount) and os.path.dirname(mount) != mount:
                mount = os.path.dirname(mount)
            mount = mount or os.sep
            rel_to = os.path.dirname(base) if mount == os.sep else mount
            lim["win_reserved"] = len(base) - len(rel_to) + 3 + 1
            forced = (self.__dict__.get("_win_dirs") or {}).get(self.__dict__.get("_win_album", ""))
            if forced:
                lim["win_dirs"] = forced
        if lim.get("fat"):
            # (a USB stick or car player: at most 255 characters below the stick's own folder)
            mount = mount_point(dest_root)
            lim["max_path_chars"] = min(int(lim.get("max_path_chars", 4096)), len(mount.rstrip(os.sep)) + 255)
        return lim

    def _file_settings(self) -> dict:
        """Picard's settings for what saving a file does beside its fields (ID3v2 version,
        ID3v1, the ID3 text encoding ...; see tagio.file_options)."""
        if not hasattr(self, "_file_set"):
            import tagger_options
            self._file_set = {k: self._picard().get(k) for k in tagio.FILE_SETTINGS}
            if self._file_set.get("id3v2_encoding") is None:     # (not one of tagger_settings's)
                self._file_set["id3v2_encoding"] = tagger_options.settings(self.profile)["id3v2_encoding"]
        return self._file_set

    # ---- the plan for one album -------------------------------------------
    def album(self, prop: Proposal) -> dict:
        """The plan for one album. Its files are opened once for all the reads below (tagio.reading)."""
        with tagio.reading(), ex.meta_memo():
            return self._album(prop)

    def _album(self, prop: Proposal) -> dict:
        self._win_album = f"{prop.source_dir}#{sorted(map(str, prop.files))}"   # (one of several albums of a folder)
        p = self.profile
        files = [Path(f) for f in prop.files]
        entry = {"source_dir": str(prop.source_dir), "status": "ready", "label": "", "rule": None,
                 "identification": dict({k: getattr(prop, k) for k in
                                         ("status", "provider", "release_id", "url", "confidence", "margin", "why",
                                          "evidence", "reason_codes", "provenance", "provider_errors")},
                                        tier=tier_of(prop), candidates=list(getattr(prop, "candidates", None) or [])),
                 "release": prop.release, "files": [], "companions": [], "artwork": None,
                 "conflicts": [], "skipped": [], "notes": [],
                 # (every file of the album as found: for a release the user chooses for it)
                 "album_files": sorted({str(x) for x in (getattr(prop, "group_files", None) or prop.files or [])})}
        entry["notes"].extend(getattr(prop, "notes", None) or [])
        if getattr(prop, "held", "") and prop.status == "identified":     # WEB-1
            entry["held"] = prop.held
            entry["notes"].insert(0, prop.held)
        if getattr(prop, "scans", None):
            entry["identification"]["scans"] = dict(prop.scans)
        if getattr(prop, "via", None) is not None:
            entry["identification"]["via"] = prop.via
        partial = prop.status == "identified" and tier_of(prop) == "partial"
        # Album only (the album known, its pressing tied) is, like Partial, a release not proven
        # (levels.ALBUM_TIERS): marked TAGMENDER=Partial too, its reason "ambiguous_release:
        # album known, pressing not established ..." - never Resolved, so a later run, the
        # watcher and `tagmender unresolved` try it again. It is organised as before.
        album_only = prop.status == "identified" and tier_of(prop) == "album_only"
        self._album_tier = tier_of(prop)
        import levels as _levels
        # (Partial or album only: the pressing is not proven, so neither is the files' own media
        # tag - no edition is read from it)
        unproven = prop.status == "identified" and self._album_tier in _levels.ALBUM_TIERS
        if prop.status == "unresolved" or partial:
            entry["identification"]["reason"] = reasons.encode(prop.reason, prop.reason_codes or None)
        elif album_only:
            entry["identification"]["reason"] = _album_only_reason(prop)
        # 1. rules, with what the files say and what identification found
        dec = ex.evaluate(files, self.rules, release=prop.release or None, overrides=self.overrides)
        if dec.excluded:
            whole = dec.summary()
            if whole is not None:
                # (labelled as what it is: a rule left it out, not an album nobody found)
                entry["identification"]["tier"] = "ignored"
                entry.update(status="excluded", label=whole.label,
                             rule={"id": whole.rule.id, "name": whole.rule.name, "action": whole.action,
                                   "matched": whole.where},
                             excluded_files=[str(f) for f in files])     # (for "process anyway")
                return entry
            for f, e in dec.excluded.items():
                entry["skipped"].append({"file": str(f), "label": e.label, "rule": e.rule.id, "matched": e.where})
        if entry["identification"]["tier"] == "ignored":
            # (identification said a rule leaves it out, but these rules, on what the plan knows of
            # the release, do not: it is planned as the unresolved album it is, and labelled so)
            entry["identification"]["tier"] = "unresolved"
        kept = [f for f in files if f not in dec.excluded]
        # 2. profile filters
        for f in list(kept):
            if not p.wants_format(f):
                entry["skipped"].append({"file": str(f), "label": "Skipped", "rule": "profile formats",
                                         "matched": f.suffix.lower()})
                kept.remove(f)
            elif p.excluded_path(f, named=self.extra_roots):
                entry["skipped"].append({"file": str(f), "label": "Skipped", "rule": "profile exclude_paths",
                                         "matched": str(f)})
                kept.remove(f)
        if not kept:
            entry["status"] = "skipped"
            entry["label"] = "Skipped"
            return entry
        targets = {Path(f): t for f, t in zip(files, prop.targets or [{}] * len(files))}
        identified = prop.status == "identified"
        conf = float(prop.confidence or 0)
        organize_ok = identified and not partial and conf >= _threshold(p, "organize_min_confidence")
        op = p["operation"]
        evidence = _files_on_evidence(prop, p, conf) if identified and not partial and not organize_ok else ""
        if evidence:
            organize_ok = True
            if op in ("copy", "move"):
                entry["notes"].append(f"confidence {conf:.1f} is below organize_min_confidence "
                                      f"{_organize_floor_words(p)}; filed because {evidence}")
            # (its album tags are written, not only added: an exact release proven by your copy -
            # its scans' barcode or catalogue number, every track length - is THE release, BAR-1/
            # EVI-1, so its title replaces an old one such as "Album [ECM 2269]", and the folder
            # is named from it; an album-only one only when its entries tie for want of lengths)
            if getattr(prop, "no_lengths_tie", False) or tier_of(prop) == "exact":
                conf = max(conf, _threshold(p, "replace_min_confidence"))
        # one version of an album found in one folder with another (engine._versions_split,
        # _discs_split): into a folder of its own
        vsplit = getattr(prop, "split", None) if identified else None
        flat = False
        if vsplit:
            entry["split"] = {k: vsplit[k] for k in ("of", "index", "count")}
            entry["split_with"] = list(vsplit.get("others") or [])
        if not identified:
            ua = "move" if getattr(prop, "quality_move", False) else p["unresolved"]["action"]
            entry["status"] = "unresolved"
            entry["label"] = "Unresolved"
            if ua == "leave" or op == "copy":
                for f in kept:
                    entry["files"].append({"src": str(f), "dst": str(f), "op": "none", "changes": {}, "diff": [],
                                           "fingerprint": _fp(f)})
                return entry
            op = "move" if ua == "move" else "in_place"
        elif vsplit:
            op, flat = self._split_op(entry, op, partial, organize_ok, conf)
            if op is None:
                entry["status"] = "skipped"
                entry["label"] = "Skipped"
                return entry
        elif op in ("copy", "move") and partial:
            # (a Partial album is not proven enough to file: its name and folder could be another
            # pressing's)
            entry["notes"].append("Partial identification (no release proven): "
                                  + ("not copied" if op == "copy" else "tagged in place, not moved"))
            if op == "copy":
                entry["status"] = "skipped"
                entry["label"] = "Skipped"
                return entry
            op = "in_place"
        elif op in ("copy", "move") and getattr(prop, "disc_folder", None):
            # one disc of a set whose discs are different releases (engine._per_disc): the set's
            # cover, rip log, cue sheet and scans sit beside the disc folders, and belong to the
            # whole set. Filing one disc would leave them behind, the cue pointing at nothing
            entry["notes"].append("one disc of a set whose discs are different releases: "
                                  + ("not copied" if op == "copy" else "tagged in place, not moved")
                                  + "; split the set into a folder per release first (its cover, log, "
                                    "cue sheet and scans are the whole set's)")
            if op == "copy":
                entry["status"] = "skipped"
                entry["label"] = "Skipped"
                return entry
            op = "in_place"
        elif op in ("copy", "move") and not organize_ok:
            entry["notes"].append(f"confidence {conf:.1f} is below organize_min_confidence "
                                  f"{_organize_floor_words(p)}: "
                                  + ("not copied" if op == "copy" else "tagged in place, not moved"))
            if op == "copy":
                entry["status"] = "skipped"
                entry["label"] = "Skipped"
                return entry
            op = "in_place"
        import plugins
        if identified and (self._scripts() or plugins.has_tag_processors()):
            # Picard's tagger scripts (and the plugins' tag changes), on each file (its own tags overlaid with the release's
            # every field, whatever the level then writes); before anything is planned from them
            full = getattr(prop, "full_targets", None)
            full = {Path(f): t for f, t in zip(files, full)} if full and len(full) == len(files) else targets
            import engine as _engine
            # (what each file is given, copied first: full may be targets itself, which this fills)
            given = {f: dict(full.get(f) or targets.get(f) or {}) for f in kept}
            self._album_dir = getattr(prop, "source_dir", None)
            was_working = plugins.working()
            try:
                for _round in (1, 2):
                    for f in kept:
                        try:
                            before_s = tagio.read(f)
                        except tagio.Unsupported:
                            continue
                        targets[f] = self._scripted(f, before_s, dict(given.get(f) or {}))
                    failed = plugins.broken_since(was_working)
                    if _round == 2:
                        late = [pid for pid, _w in failed if pid not in noted]
                        if late:                  # (another plugin failed in the second pass too)
                            entry["conflicts"].append("the plugin " + ", ".join(late) + " failed while this "
                                                      "album was planned and is switched off; preview it again")
                            entry["status"] = "blocked"
                            return entry
                        break
                    if not failed:
                        break
                    noted = {pid for pid, _w in failed}
                    # (a plugin failed part-way: it is off now, so every file again without it -
                    # never an album with its changes on some tracks only - and the plan says so)
                    entry["notes"].append("the plugin " + ", ".join(dict.fromkeys(pid for pid, _w in failed)) +
                                          " failed and is switched off: its tag changes are left out ("
                                          + failed[0][1] + ")")
            except _engine.TaggerScriptError as exc:
                entry["conflicts"].append(f"a tagger script failed on {f.name}: {exc}")
                entry["status"] = "blocked"
                return entry
            finally:
                self._album_dir = None                       # (this album's only)
        # 3. destination root (never created: an unmounted share must not fill the local disk)
        dest_root = None
        if flat:
            # (beside the folder it was found in: no destination, nothing filed elsewhere)
            dest_root = Path(vsplit["parent"])
            self._split_roots.add(os.path.realpath(str(dest_root)))
        elif op in ("copy", "move"):
            # (the lossy library for an album of lossy files only, else its source folder's own library)
            dest = p.destination_for(kept, prop.source_dir) if identified and hasattr(p, "destination_for") \
                else p["destination"]
            dest_root = Path(p["unresolved"]["move_to"] if not identified else dest).expanduser()
            if not dest_root.is_dir():
                entry["status"] = "blocked"
                entry["conflicts"].append(f"destination {dest_root} is not available (unmounted?); "
                                          "TagMender never creates it")
                return entry
            guard = p.mount_guard() if hasattr(p, "mount_guard") else "auto"
            if not mounted(dest_root, guard):
                entry["status"] = "blocked"
                entry["conflicts"].append(f"destination {dest_root} is on the system disk, not on the share "
                                          "mounted there (unmounted?)")
                return entry
            entry["dest_root"] = {"path": str(dest_root), "dev": os.stat(dest_root).st_dev,
                                  "mount": mount_point(dest_root), "guard": guard}
        template = naming.effective_template(p["naming"], p["filenames"])
        # rename_in_place: an album tagged where it is gets its files' names from the naming
        # script too (only the file names: its folders stay as they are), at the rename level
        renaming = op == "in_place" and identified and organize_ok and bool(p.get("rename_in_place")) and \
            not vsplit and not flat
        # your edits of fields the naming script reads (a disc number, the album's title): the
        # files are named with your values (apply_edits writes them after); recorded, so an edit
        # made later on the preview shown is known to need a new preview (edit_renames)
        naming_edit = {}
        if (op in ("copy", "move") or renaming) and identified:
            entry["naming_fields"] = sorted(template_fields(template))
            naming_edit = _naming_edit(edit_for(self.edits, prop.source_dir, [str(f) for f in files]),
                                       entry["naming_fields"])
            if naming_edit:
                entry["naming_edit"] = naming_edit
        album_dirs = {}
        if op == "copy" and identified:
            album_dirs["prior"] = self._prior_album_dir(kept, dest_root)
        nm = p["naming"] or {}
        # the release's first disc says which edition it is, for every file of it (its media and
        # its date as they will be written): a CD with a 7" bonus disc, or a disc from a later
        # repress, stays in one folder
        folder_mode, album_mode = nm.get("editions", "off"), nm.get("album_editions", "off")
        rel = {}
        if identified and not unproven and (folder_mode != "off" or album_mode != "off") and kept:
            def _order(f):
                t = targets.get(f) or {}
                d = str((t.get("discnumber") or ["1"])[0]).split("/")[0]
                n = str((t.get("tracknumber") or ["1"])[0]).split("/")[0]
                return (int(d) if d.isdigit() else 1, int(n) if n.isdigit() else 1, str(f))
            has_media = [f for f in kept if ((targets.get(f) or {}).get("media") or [""])[0]]
            f0 = min(has_media or kept, key=_order)       # (the first disc that says what it is)
            try:
                b0 = tagio.read(f0)
                t0 = dict(targets.get(f0) or {})
                gone0 = [x for x in (t0.get("~remove") or [])
                         if x not in t0 and p.policy(x) not in ("preserve", "lock", "fill_missing")]
                ch0, _r0 = diff_tags(b0, t0, p, conf, gone0)
                m0 = {**b0, **{k: v for k, v in ch0.items() if v is not None}}
                for k, v in ch0.items():
                    if v is None:
                        m0.pop(k, None)
                rel = {k: list(m0[k]) for k in ("media", "date", "originaldate", "originalyear") if m0.get(k)}
            except Exception:
                rel = {}
        rel_tags = {f"~edition_{k}": v for k, v in rel.items()}
        # ED-5: hi-res audio adds HD to a CD-type or download edition, and is the only edition
        # an album known (its pressing unproven) shows
        if identified and (folder_mode != "off" or album_mode != "off") and kept:
            import marks
            if any(marks.is_hd(f) for f in kept[:3]):
                rel_tags["~edition_hd"] = ["1"]
            if unproven:
                rel_tags["~edition_unproven"] = ["1"]
        # a Partial album whose files already name a release (Picard's or TagMender's tags):
        # only what they lack is filled; their date, album, titles and mark stay as they are
        held = partial and _names_release(kept)
        # (HF-5: tracks missing are noted by the engine, in the report only; no tag)
        if held:
            entry["notes"].append("Partial identification on files that already name a release: only empty "
                                  "fields filled, their release tags and TagMender mark left as they are")
        win_split, split = {}, False
        apart = self._versions_apart(kept, targets, template, rel_tags) \
            if identified and op in ("copy", "move") and not flat and not vsplit else False
        if apart:
            entry["notes"].append("versions of one disc (" + ", ".join(apart) + ") would be named alike: each "
                                  "goes into a folder of its own, named after it, inside the album's (or its disc's) folder")
        naming_with = plugins.working()                   # (a $function of a plugin failing while naming)
        for f in kept:
            try:
                before = tagio.read(f)
            except tagio.Unsupported as exc:
                entry["skipped"].append({"file": str(f), "label": "Skipped", "rule": "unsupported_format",
                                         "matched": str(exc)})
                continue
            try:
                ino = (os.stat(f).st_dev, os.stat(f).st_ino)
            except OSError:
                ino = None
            if ino and self._inodes.get(ino, str(f)) != str(f):
                entry["skipped"].append({"file": str(f), "label": "Skipped", "rule": "same_file",
                                         "matched": f"the same file as {self._inodes[ino]} (a hard link); "
                                                    "left where it is"})
                continue
            if ino:
                self._inodes[ino] = str(f)
            target = dict(targets.get(f) or {})
            # (what a script did to a user tag spelled like a field kept elsewhere: by itself)
            cust_set = {k: target.pop(k) for k in [k for k in target if k.startswith("~custom:")]}
            cust_rm = [x for x in (target.get("~remove") or []) if x.startswith("~custom:")]
            if cust_rm:
                target["~remove"] = [x for x in target["~remove"] if not x.startswith("~custom:")]
            # (the file as it will be: without what the release or a script deletes)
            gone_ed = {x for x in (target.get("~remove") or [])
                       if x not in target and p.policy(x) not in ("preserve", "lock", "fill_missing")}
            tag_ed = naming.edition({**{k: v for k, v in before.items() if k not in gone_ed}, **target, **rel_tags},
                                    album_mode) if album_mode != "off" else ""
            # (never on a Partial or album-only album: its edition would be read from an unproven
            # media tag)
            if identified and (not unproven or tag_ed == "HD") and tag_ed and (target.get("album") or [""])[0]:
                album = str(target["album"][0])
                if not album.endswith(f" [{tag_ed}]"):      # "I" -> "I [2019 Vinyl]", once
                    target["album"] = [f"{album} [{tag_ed}]"] + list(target["album"][1:])
            if held:
                target = {k: v for k, v in target.items()
                          if (k.startswith("~") and k != "~remove") or not before.get(k)}
                target.pop("tagmender", None)
                target.pop("tagmender_reason", None)
            elif partial:
                # the date is the album's first release, filled only where the file has none: a
                # date the file already carries may be its own pressing's (a 2009 reissue)
                if before.get("date"):
                    target.pop("date", None)
                # (always: what tells you, Picard and a later run that no release was proven)
                target["tagmender"] = ["Partial"]
                target["tagmender_reason"] = [entry["identification"]["reason"]]
            elif album_only:
                target["tagmender"] = ["Partial"]          # (its release not proven: see above)
                target["tagmender_reason"] = [entry["identification"]["reason"]]
            elif identified and p["mark_resolved"]:
                target.setdefault("tagmender", ["Resolved"])
                if not before.get("tagmender_reason"):
                    target.setdefault("tagmender_reason", [])
            if not identified and p["unresolved"]["action"] in ("mark", "move"):
                target = {"tagmender": ["Unresolved"], "tagmender_reason": [entry["identification"]["reason"]]}
            removals = ["tagmender_reason"] if identified and not (partial or album_only) and \
                before.get("tagmender_reason") and \
                str((before.get("tagmender") or [""])[0]).lower() in ("unresolved", "partial") else []
            # an earlier run's gap list and web page, once the album as identified now carries
            # neither (the folder is complete again, or a release was proven since)
            # (the page only when it is a shop's or encyclopedia's and a release is proven now:
            # a Discogs or MusicBrainz page the files name stays, as does any on a Partial album)
            if identified and not held:
                exact = not (partial or album_only or unproven)
                removals += [k for k in ("tagmender_missing", "tagmender_source")
                             if before.get(k) and not target.get(k) and p.policy(k) not in ("preserve", "lock")
                             and (k != "tagmender_source" or (exact and _shop_page(before[k])))]
            # what Picard would delete (compilation on a release not by Various Artists, what a
            # tagger script $delete-s) - never a field the profile preserves or locks
            removals += [tagio.canonical(f, x) for x in (target.pop("~remove", None) or [])
                         if x not in target and p.policy(x) not in ("preserve", "lock", "fill_missing")
                         and x != "itunes_cddb_1"]      # (Picard's $delete leaves iTunes' own COMM there)
            # Picard's hidden variables (%_multiartist%, what a tagger script $set): for the file
            # naming script only, never written
            hidden = {"_" + k[1:]: v for k, v in {**tagio.file_vars(f), **target}.items()
                      if k.startswith("~") and len(k) > 1 and k != "~remove"}
            mb_length = target.get("~length")   # (the release's track length: Submit AcoustIDs checks by it)
            target = {k: v for k, v in target.items() if not k.startswith("~")}
            # what the format has a place for, as it will hold it (an ID3v2.3 tag joins values ...)
            whole = target                # (the file's fields as Picard has them for naming, held or not)
            raw = {k: v for k, v in target.items() if tagio.holds(f, k)}
            # (a total kept in its number's field - "5/9", trkn (5, 9) - which Picard makes from
            # the track's fields: a number removed or unset takes no total; a total unset leaves
            # the number alone in it. What is written only: the file is named from its fields
            # as Picard names it, below)
            removals_named = list(removals)
            unset_ = self.__dict__.get("_unset", {}).get(str(f), set())
            # (Vorbis and APEv2 keep a described comment in COMMENT as "text (desc)", which Picard
            # writes whole from the track's comments: a plain comment a script unset goes)
            if "comment" in unset_ and "comment" not in raw and before.get("comment") and \
                    (any(k_.startswith("comment:") and raw[k_] for k_ in raw) or
                     any(k_.startswith("comment:") and v_ for k_, v_ in before.items())) and \
                    tagio.family_of(f) in ("vorbis", "apev2") and \
                    p.policy("comment") not in ("preserve", "lock", "fill_missing") and "comment" not in removals:
                removals.append("comment")
            for num_, tot_ in tagio.combined_pairs(f):
                if (num_ in removals or num_ in unset_) and num_ not in raw:
                    raw.pop(tot_, None)
                elif p.policy(num_) in ("preserve", "lock") and not before.get(num_) and \
                        p.policy(tot_) not in ("preserve", "lock") and not before.get(tot_):
                    raw.pop(tot_, None)          # (no number of its own, kept so: no place for a total)
                elif tot_ in unset_ and tot_ not in raw and tot_ not in removals and before.get(tot_) and \
                        p.policy(tot_) not in ("preserve", "lock", "fill_missing"):
                    removals.append(tot_)                    # (a total Picard preserves stays)
            fmt = tagio.file_options(f, self._file_settings())       # (Picard: ID3v2 version, ID3v1, ...)
            target = tagio.stored(f, raw, fmt)
            for k_, v_ in list(target.items()):   # (a value the format holds as none - an M4A total 0: the
                if v_ is None and raw.get(k_) and before.get(k_) and k_ not in removals and \
                        p.policy(k_) not in ("preserve", "lock", "fill_missing"):              # file's goes)
                    removals.append(k_)
                    target.pop(k_)
            if raw.get("comment") and not target.get("comment") and before.get("comment") and \
                    p.policy("comment") not in ("preserve", "lock", "fill_missing"):
                # (a Vorbis/APEv2 comment "text (desc)" is the described comment: the file's
                # plain comment it replaces goes, as Picard replaces it)
                removals.append("comment")
            # (a Vorbis total only inside "5/9" is none, as Picard reads it; one under one of its two
            # names is written under both when the file is written, as Picard saves it)
            v_inside, v_one = tagio.vorbis_totals(f)
            before_d = {k_: v_ for k_, v_ in before.items() if k_ not in v_inside}
            for num_, tot_ in tagio.NUMBER_PAIRS:      # (and the number is "5/9" as it stands: rewritten)
                if tot_ in v_inside and before.get(num_) and before.get(tot_):
                    before_d[num_] = [f"{before[num_][0]}/{before[tot_][0]}"]
            changes, rows = diff_tags(before_d, target, p, conf if identified else 100.0, removals)
            import levels as _lv
            if identified and float(prop.confidence or 0) >= _threshold(p, "replace_min_confidence") \
                    and _lv.level(p) != "basic":
                join_ = tagio.v23_join(f, fmt) if cust_set else None
                for k_, v_ in cust_set.items():
                    if join_ and len(_vals(v_)) > 1:         # (an ID3v2.3 TXXX: its values joined, as written)
                        v_ = [join_.join(_vals(v_))]
                    if _vals(v_) and _vals(v_) != _vals(before.get(k_)):
                        changes[k_] = _vals(v_)
                        rows.append({"field": k_[8:], "before": _vals(before.get(k_)), "after": _vals(v_),
                                     "action": "changed by a tagger script (a user tag)"})
                for k_ in cust_rm:
                    if _vals(before.get(k_)) and k_ not in cust_set:
                        changes[k_] = None
                        rows.append({"field": k_[8:], "before": _vals(before.get(k_)), "after": [],
                                     "action": "removed by a tagger script (a user tag)"})
            riff_copied = []
            if changes:                   # (a WAV's fields kept only in its RIFF INFO: into ID3, as Picard)
                for k_ in sorted(tagio.riff_only(f)):
                    if k_ not in changes and k_ not in removals and before.get(k_) and p.policy(k_) != "lock" and \
                            _lv.restrict({k_: before[k_]}, p).get(k_) and tagio.holds(f, k_):
                        # (as the frame holds it - a date that is no date, a genre "(17)", may read
                        # back otherwise: copied as it comes, never failing the album)
                        changes[k_] = _vals(tagio.stored(f, {k_: before[k_]}, fmt).get(k_)) or list(before[k_])
                        riff_copied.append(k_)
                        rows.append({"field": k_, "before": list(before[k_]), "after": list(before[k_]),
                                     "action": "written into the ID3 tag (the file had it in its RIFF INFO only), "
                                               "as the MusicBrainz tag mapping writes it"})
            if changes and "lyrics" not in changes and before.get("lyrics") and p.policy("lyrics") != "lock" and \
                    "lyrics" not in removals and tagio.unsynced_lyrics_only(f):
                changes["lyrics"] = list(before["lyrics"])     # (Picard writes them as LYRICS on saving)
                rows.append({"field": "lyrics", "before": list(before["lyrics"]), "after": list(before["lyrics"]),
                             "action": "written as LYRICS (the file had them as UNSYNCEDLYRICS), "
                                       "as the MusicBrainz tag mapping writes them"})
            if changes:                   # (only when the file is written anyway)
                for tot_ in ("totaltracks", "totaldiscs"):
                    # (a total the file has under both names, or not Vorbis - ID3 "1/03" -: on a
                    # Partial or album-only album its "03" is written plain too, as below)
                    if tot_ in v_one or tot_ in changes or tot_ in removals or tot_ in unset_ or \
                            not before.get(tot_) or not unproven or not tagio.holds(f, tot_) or \
                            p.policy(tot_) in ("preserve", "fill_missing", "lock"):
                        continue
                    after_ = [_plain_number(v_) for v_ in before[tot_]]
                    if after_ != list(before[tot_]):
                        changes[tot_] = after_
                        rows.append({"field": tot_, "before": list(before[tot_]), "after": after_,
                                     "action": "leading zero dropped, as an exact match writes it"})
                for tot_ in v_one:
                    # (not one a script unset - Picard then has no total to write - unless preserved)
                    if tot_ not in changes and before.get(tot_) and tot_ not in removals and \
                            p.policy(tot_) != "lock" and \
                            (tot_ not in unset_ or p.policy(tot_) in ("preserve", "fill_missing")):
                        # (its value as it is; on a Partial or album-only album, which writes no
                        # total of its own, the file's "03" is written plain, "3", as an exact match
                        # writes the release's - TagMender has no option to pad numbers - unless
                        # the profile, or Picard's preserved tags, keep the field as it is)
                        plain = unproven and p.policy(tot_) not in ("preserve", "fill_missing")
                        after_ = [_plain_number(v_) for v_ in before[tot_]] if plain else list(before[tot_])
                        changes[tot_] = after_
                        names_ = "TOTALDISCS and DISCTOTAL" if tot_ == "totaldiscs" else "TOTALTRACKS and TRACKTOTAL"
                        rows.append({"field": tot_, "before": list(before[tot_]), "after": after_,
                                     "action": f"written under both names ({names_}), as the MusicBrainz "
                                               "tag mapping writes it"
                                               + (", leading zero dropped" if after_ != list(before[tot_]) else "")})
            as_is = tagio.fragmented(f)
            named = changes               # (named as its album's other files are, whatever it holds)
            if as_is:                     # (moved or copied with its album as it is, never written to)
                changes, rows = {}, []
                entry["notes"].append(f"{f.name}: a fragmented MP4 - never written to (its audio would no longer "
                                      "play); it goes with its album as it is")
            for note in _release_id_notes(rows):
                if note not in entry["notes"]:
                    entry["notes"].append(note)
            fe = {"src": str(f), "dst": str(f), "op": "tag" if changes else "none", "changes": changes,
                  "diff": rows, "fingerprint": _fp(f)}
            if mb_length:
                fe["length"] = mb_length
            if str(f) in self.__dict__.get("_unscripted", {}):
                fe["unscripted"] = self._unscripted[str(f)]
            if riff_copied:
                fe["lenient"] = riff_copied
            if as_is:
                fe["as_is"] = True
            elif tagio.opus_pictures_need_fix(f):
                # (a picture an earlier TagMender wrote with a size and no depth: libopusfile
                # players show no cover - its size cleared, as Picard writes Opus pictures)
                fe["fix_pictures"] = True
                fe["op"] = "tag"
                fe["diff"] = list(fe["diff"]) + [{"field": "picture", "before": ["size without depth"],
                                                  "after": ["no size (Opus players show it)"], "action": "repaired"}]
            if changes and fmt:
                fe["format"] = fmt
            spell = _spelled_names(raw, changes)
            if spell:
                fe["names"] = spell
            if _first_values(raw, changes):
                fe["first"] = _first_values(raw, changes)
            if changes.get("license") and raw.get("license"):
                fe["license_count"] = len(raw["license"])      # (one URL: WCOP; several: TXXX:LICENSE)
            kept_ = sorted(t_ for _n, t_ in tagio.NUMBER_PAIRS if t_ in unset_ and t_ not in changes
                           and p.policy(t_) not in ("preserve", "lock", "fill_missing"))
            if changes and kept_ and not tagio.combined_pairs(f):
                fe["keep"] = kept_                   # (a total a script unset: as the file has it)
            # the file's tags after the write, as values (not as an ID3v2.3 tag joins them: a
            # field the file already holds as the target says is named "A & B", not "A_B")
            merged = {k: (raw[k] if k in raw and _vals(target.get(k)) == _vals(v) else v) for k, v in before.items()}
            merged.update({k: (raw.get(k) or v) for k, v in named.items() if v is not None})
            for k, v in named.items():
                if v is None:
                    merged.pop(k, None)
            if changes and f.suffix.lower() == ".wav":
                # RIFF INFO: every field the file holds, the values as they are (Picard's
                # WAVFile._save writes it from all of the file's metadata)
                unset = self.__dict__.get("_unset", {}).get(str(f), set())
                fe["riff"] = _riff_fields(merged, unset)
            if op in ("copy", "move") or renaming:
                if not identified:      # as it is, with its folders below the profile's source
                    src_root = Path(prop.source_dir)
                    try:
                        rel = f.relative_to(src_root)
                    except ValueError:
                        rel = Path(f.name)
                    real_hold = os.path.realpath(dest_root)
                    if os.path.realpath(str(src_root)).startswith(real_hold.rstrip(os.sep) + os.sep):
                        dst = f                         # already in the holding folder: stays
                    else:
                        compat = p["filenames"].get("compat", "windows")
                        below = Path(*[fnm.safe_component(x, compat) for x in self._below_source(src_root).parts]) \
                            if self._below_source(src_root).parts else Path()
                        dst = dest_root / below / Path(*[fnm.safe_component(x, compat) for x in rel.parts[:-1]]) \
                            / _fit_name(rel.name, p["filenames"])
                    notes = []
                else:
                    # (Picard names a file from all of its fields - those its format has no place
                    # for, or keeps otherwise (a WMA's total tracks, an M4A's "A1"), too)
                    unheld = {k: v for k, v in whole.items() if v and (not tagio.holds(f, k) or
                                                                        (k in raw and k not in target))}
                    names = {**hidden, **merged, **unheld, **rel_tags}
                    # (numbers and totals as Picard names a file: the file's own fields with
                    # the track's over them, less what a script deleted - not as written)
                    inside_ = tagio.vorbis_totals(f)[0]     # (a Vorbis "5/9": no total, as Picard reads it)
                    for num_, tot_ in tagio.ID3_PAIRS:
                        for k_ in (num_, tot_):
                            pol_ = p.policy(k_)
                            v_ = whole.get(k_) if whole.get(k_) else (None if k_ in inside_ else before.get(k_))
                            if pol_ in ("preserve", "lock") or (pol_ == "fill_missing" and before.get(k_)):
                                v_ = before.get(k_)          # (kept as the file has it)
                            elif k_ in removals_named and not whole.get(k_):
                                v_ = None
                            if v_:
                                names[k_] = v_
                            else:
                                names.pop(k_, None)
                    for k_, sp_ in tagio.spellings(f).items():     # (the file's own TXXX:EDITION as %EDITION%)
                        if k_ in names and k_ not in whole:
                            names[sp_] = names.pop(k_)
                    for k_, v_ in before.items():                    # (its TXXX:TOTALTRACKS as %TOTALTRACKS%)
                        if k_.startswith("~custom:") and k_[8:] != k_[8:].lower() and k_ not in cust_rm:
                            names[k_[8:]] = _vals(cust_set.get(k_, v_))
                    for rk_ in raw:                     # (a script's $set(MyTag,...) as %MyTag%, not %mytag%,
                        lk_ = tagio.field_name(rk_)     # as Picard's case-sensitive names have it)
                        if rk_ != lk_ and lk_ in names and lk_ not in before and lk_ not in whole:
                            names[rk_] = names.pop(lk_)
                    if folder_mode != "off":
                        # (for the name only: a Picard script working the edition out itself
                        # from %media% and %date% gets the release's first-disc values for every
                        # file; and the album goes in without the edition the Album tag carries -
                        # the folder adds its own)
                        if "$set(_edkind," in template:
                            names.update(rel)
                        if unproven:                      # (the files' own media tag is not proven)
                            names.pop("media", None)
                        al = str((names.get("album") or [""])[0])
                        if tag_ed and al.endswith(f" [{tag_ed}]"):
                            names["album"] = [al[:-len(f" [{tag_ed}]")]]
                    for k_, v_ in _edited_names(naming_edit, f).items():
                        if v_ is None:
                            names.pop(k_, None)
                        elif v_ != KEEP:
                            names[k_] = _vals(v_)
                        elif before.get(k_):
                            names[k_] = before[k_]
                    try:
                        comps = naming.render(template, names, self._picard(), p["filenames"].get("compat", "windows"),
                                              own=f.stem)
                    except naming.TemplateError as exc:          # (a custom script that fails here)
                        comps, notes = None, [f"the naming script failed: {exc}"]
                    dst = None
                    if comps is not None and flat:            # (beside the folder: from the album's own folder down)
                        comps = _from_album_folder(comps, names, p["filenames"].get("compat", "windows"))
                    if comps is not None and renaming:      # (the file's own name only, in its folder)
                        dst, notes = fnm.make_path(f.parent, comps[-1:], f.suffix.lower(), self._name_limits(f.parent))
                        comps = None
                    elif comps is not None:
                        dst, notes = fnm.make_path(dest_root, comps, f.suffix.lower(), self._name_limits(dest_root))
                    made = dst
                    if dst is not None and not renaming:
                        dst = self._spelled(dst, dest_root)
                        if not vsplit:                   # (a split's folder already there is a conflict)
                            dst = self._own_album_dir(dst, prop, kept, album_dirs)
                        # (Windows path shortening cuts each track's folders by its own path's
                        # length: one folder of the album must never become two)
                        try:
                            got = made.parent.relative_to(dest_root).parts
                        except ValueError:
                            got = None
                        if got is not None and len(got) == len(comps) - 1 and \
                                "win_reserved" in self._name_limits(dest_root):
                            compat_ = p["filenames"].get("compat", "windows")
                            keys = [fnm.safe_component(c, compat_) for c in comps[:-1]]
                            for i in range(len(got)):
                                k = tuple(keys[:i + 1])
                                was = win_split.setdefault(k, got[:i + 1])
                                if was != got[:i + 1]:
                                    split = True        # (the most shortened wins, for every track)
                                    if sum(map(len, got[:i + 1])) < sum(map(len, was)):
                                        win_split[k] = got[:i + 1]
                    ver_ = _vals((targets.get(f) or {}).get("~version")) if apart else []
                    if dst is not None and not renaming and ver_:
                        # (a version of a disc another version of the album shares: in a folder of
                        # its own inside the one the naming made - "Disc 02/5.1/201.T.flac")
                        dst = dst.parent / fnm.safe_component(str(ver_[0]), p["filenames"].get("compat", "windows")) \
                            / dst.name
                entry["notes"] += notes
                if dst is None:
                    entry["conflicts"].append(f"{f.name}: " + (notes[-1] if notes else "no destination path"))
                    continue
                earlier = None
                if op == "copy":
                    legacy = None
                    old_tags = _legacy_names(merged) if identified else None
                    if old_tags is not None:
                        try:
                            legacy, _n = fnm.make_path(
                                dest_root, naming.render(template, {**hidden, **old_tags, **rel_tags}, self._picard(),
                                                         p["filenames"].get("compat", "windows"), own=f.stem),
                                f.suffix.lower(), self._name_limits(dest_root))
                        except naming.TemplateError:
                            legacy = None                 # (the old name cannot be made: no earlier copy by it)
                    # (and the name a version before Picard's Windows path shortening gave it)
                    plain = fnm.make_path(dest_root, comps, f.suffix.lower(), p["filenames"])[0] \
                        if comps is not None and "win_reserved" in self._name_limits(dest_root) else None
                    earlier = self._earlier_copy(dst, f, dest_root, [legacy, plain])
                if earlier is not None:
                    # copied by an earlier run: bring the copy's tags up to date, nothing else
                    dst = earlier
                    self.claimed[fnm.fold(dst)] = str(f)
                    have = tagio.read(dst)
                    fmt2 = tagio.file_options(dst, self._file_settings())    # (as the copy holds them)
                    changes2, rows2 = diff_tags(have, tagio.stored(dst, raw, fmt2), p,
                                                conf if identified else 100.0, removals)
                    fe.pop("as_is", None)
                    if tagio.fragmented(dst):            # (the copy as it is: never written to)
                        changes2, rows2 = {}, []
                        fe["as_is"] = True
                    fe.pop("format", None)
                    if changes2 and fmt2:
                        fe["format"] = fmt2
                    fe.pop("names", None)
                    if _spelled_names(raw, changes2):
                        fe["names"] = _spelled_names(raw, changes2)
                    fe.pop("first", None)
                    if _first_values(raw, changes2):
                        fe["first"] = _first_values(raw, changes2)
                    fe.pop("riff", None)
                    if changes2 and dst.suffix.lower() == ".wav":       # (the copy's own INFO, as for any file)
                        now2 = {k: v for k, v in have.items() if not k.startswith("~")}
                        now2.update({k: (raw.get(k) or v) for k, v in changes2.items() if v is not None})
                        fe["riff"] = _riff_fields(now2, {k for k, v in changes2.items() if v is None})
                    fe.pop("fix_pictures", None)
                    if not fe.get("as_is") and tagio.opus_pictures_need_fix(dst):     # (the copy's own cover)
                        fe["fix_pictures"] = True
                        rows2 = list(rows2) + [{"field": "picture", "before": ["size without depth"],
                                                "after": ["no size (Opus players show it)"], "action": "repaired"}]
                    fe.update(src=str(dst), dst=str(dst), op="tag" if (changes2 or fe.get("fix_pictures")) else "none",
                              changes=changes2, diff=rows2, fingerprint=_fp(dst), copy_of=str(f))
                    entry["files"].append(fe)
                    continue
                dst = self._unique(dst, f, entry)
                if dst is None:
                    continue
                fe["dst"] = str(dst)
                if Path(dst) == f or (Path(dst).name == f.name and same_entry(dst, f)):
                    fe["dst"] = str(f)                   # already where the template puts it
                    fe["op"] = "tag" if (changes or fe.get("fix_pictures")) else "none"
                else:
                    fe["op"] = "move" if renaming else op
                    if Path(dst).exists() and os.path.samefile(dst, f) and not _one_name(dst, f):
                        fe["linked"] = True              # the destination is a hard link to this file
                        if op == "copy" and not changes and not fe.get("fix_pictures"):
                            fe["op"] = "none"            # already there
            entry["files"].append(fe)
        entry["conflicts"] += _linked_tracks(entry, self.roots() + sorted(self._split_roots))
        if identified and getattr(prop, "suspect_lossy", "") and (p.get("quality_action") == "hold" or
                                                                  p.get("quality_action") == "move" and op == "copy"):
            entry["conflicts"].append(f"held for review: the lossless files look converted from lossy "
                                      f"({prop.suspect_lossy})")
        if self._unwritable(entry) and not entry["files"] and not entry["conflicts"]:
            entry["status"] = "skipped"
            entry["label"] = "Skipped"
            return entry
        if vsplit:
            self._split_folder(entry, vsplit)
        entry["conflicts"] = list(dict.fromkeys(entry["conflicts"]))
        if split:
            # Windows path shortening cut the album's folders differently for its tracks (each
            # by its own path's length: discs with names of different lengths): planned again
            # with the most shortened folders for all of them - one album, one folder
            forced = self.__dict__.setdefault("_win_dirs", {})
            key = self._win_album
            norm = {}                       # (each folder's shortened parent: the one chosen for it)
            for k_ in sorted(win_split, key=len):
                v_ = tuple(win_split[k_])
                norm[k_] = (norm[k_[:-1]] + v_[-1:]) if k_[:-1] in norm else v_
            tries = self.__dict__.setdefault("_win_tries", {})
            if tries.get(key, 0) < 2 and forced.get(key) != norm:
                tries[key] = tries.get(key, 0) + 1
                forced[key] = norm
                mine = {str(x) for x in files}
                for k_ in [k_ for k_, v_ in self.claimed.items() if v_ in mine]:
                    del self.claimed[k_]
                return self.album(prop)
            entry["conflicts"].append("the album's folders are too long for Windows' path length and could not be "
                                      "shortened alike for all its tracks; shorten the names (profile "
                                      "abbreviations) or set tagger.windows_long_paths")
        late = plugins.broken_since(naming_with)
        if late:                                  # (some tracks named with its function, the rest without)
            entry["conflicts"].append("the plugin " + ", ".join(dict.fromkeys(pid for pid, _w in late)) +
                                      " failed while this album's files were named and is switched off; "
                                      "preview it again for names made without it (" + late[0][1] + ")")
        if entry["conflicts"]:
            entry["status"] = "blocked"
            return entry
        # 4. companion files go with an album that moves or is copied
        # (and, with the profile's covers and misc folders, the album's pictures and papers go
        # into them - also for an album tagged where it is: its own folder is tidied)
        moved = [fe for fe in entry["files"] if fe["op"] in ("copy", "move")]
        # (only a proven album's folder is tidied: one nothing identified may not be an album at
        # all - a downloads folder of loose tracks beside your own photos and notes)
        sub = tidy.settings(p) if identified else None
        tidy_here = bool(sub) and not moved and self._tidies_here(entry)
        if moved or tidy_here:
            # every file of the album, those already in place too: their folder is the album's
            pairs = {fe.get("copy_of", fe["src"]): fe["dst"] for fe in entry["files"]}
            src_root = Path(prop.source_dir)
            real_src = os.path.realpath(src_root)
            at_root = real_src in self.library_roots() or (real_src in self.roots() and len(pairs) == 1)
            who, single = _album_artist(prop)
            sort = [x for x in ((prop.targets[0] if prop.targets else {}).get("albumartistsort") or []) if x != who]
            how = {}
            if vsplit:              # (each version takes its own; what both share is copied to each)
                comps, why_not, how = _split_companions(vsplit, pairs, p, moved[0]["op"],
                                                        extra=tidy.extensions(sub))
                own_from = {c for c, _w in comps} if why_not == SHARED_FOLDER_NOTE else set()
            else:
                whole = bool(identified) and real_src not in self.roots() and real_src not in self.library_roots()
                comps, why_not = _companions(src_root, pairs, p, shallow=at_root, tracks_only=at_root,
                                             artist=[who] + sort,
                                             single=single, extra=tidy.extensions(sub), everything=whole)
                own_from = {c for c, _w in comps} if why_not == SHARED_FOLDER_NOTE else set()
                # DSC-1: a set joined from disc folders side by side ("Album CD1", "Album CD2"):
                # each other disc folder's scans, logs and cue sheets go with its music
                others = sorted({Path(f).parent for f in pairs if Path(f).parent != src_root
                                 and src_root not in Path(f).parents})
                for od in others:
                    if os.path.realpath(od) in self.roots():
                        continue                     # (never a watched or library folder itself)
                    mine = {f: d for f, d in pairs.items() if Path(f).parent == od}
                    more, why2 = _companions(od, mine, p, artist=[who] + sort, single=single,
                                             extra=tidy.extensions(sub), everything=bool(identified))
                    comps += [c for c in more if c not in comps]
                    if why2 == SHARED_FOLDER_NOTE:       # (that folder's own: told, and marked one by one)
                        own_from |= {c for c, _w in more}
                    elif why2:
                        why_not = why_not or why2
            if why_not and not tidy_here:
                entry["notes"].append(why_not)
            base_op = moved[0]["op"] if moved else "move"
            album_dir, homes, fronts = self._tidy_homes(entry, prop) if sub else (None, (), ())
            keep = tidy.likely_fronts([w for _c, w in comps], album_dir, homes, fronts) if sub else set()
            fronted = tidy.fronts_at([w for _c, w in comps], album_dir, homes, fronts, keep) if sub else None
            for c, want in comps:
                kind = ""
                if sub:
                    tidied, kind = tidy.target(want, album_dir, homes, sub, fronts, fronted, src=c, keep=keep)
                    bad = tidy.blocked(tidied, album_dir) if kind else None
                    if bad is not None:
                        # (a file or link already holds the folder's name: the file stays put
                        # rather than fail the album at apply)
                        entry["notes"].append(f"{c.name} is not moved into the {kind} folder: {bad.name} "
                                              f"there is a file or link, not a folder")
                        kind = ""
                    else:
                        want = tidied
                if tidy_here and want == c:
                    continue                                  # (already in its place)
                if want != c and want.exists() and os.path.samefile(want, c) and not _one_name(want, c):
                    entry["notes"].append(f"{c.name} is already at {want} (a hard link); left as it is")
                    continue
                if want != c:
                    want = want.with_name(_fit_name(want.name, p["filenames"]))
                if want != c and want.exists() and not same_entry(want, c) and _same_bytes(want, c, pairs, p):
                    continue                                  # copied there by an earlier run
                dst = c if want == c else self._unique(want, c, entry)
                if dst is None:
                    continue
                ce = {"src": str(c), "dst": str(dst), "op": how.get(c, base_op) if dst != c else "rewrite",
                      "fingerprint": _fp(c)}
                if kind:
                    ce["tidy"] = kind         # (into the album's covers or misc folder)
                if c in own_from:              # (a track's own file, from a folder shared with other music)
                    ce["own"] = True
                if c in how:
                    ce["shared"] = True       # (a split's file both versions share: copied to each)
                if c.suffix.lower() in CUE_LIKE and p.get("companions_rewrite_cues", True) is False:
                    entry["notes"].append(f"{c.name}: kept as it is (cue sheets and playlists are not rewritten); "
                                          "it may name the old file names")
                elif c.suffix.lower() in CUE_LIKE:
                    rel = {os.path.relpath(s_, c.parent): os.path.relpath(d_, dst.parent)
                           for s_, d_ in pairs.items()}
                    rel.update({str(s_): str(d_) for s_, d_ in pairs.items()})       # absolute entries
                    rw = _cue_rewrites(c, rel)
                    if rw and rw[0] is None:
                        entry["notes"].append(f"{c.name}: the new file names cannot be written in its "
                                              f"{rw[1]} encoding; it keeps the old names")
                    elif rw:
                        ce["rewrite"] = {"text": rw[0], "encoding": rw[1]}
                if ce["op"] == "rewrite" and not ce.get("rewrite"):
                    continue                                  # stays where it is, unchanged
                entry["companions"].append(ce)
        # 5. artwork (staged by the artwork provider; never replaces a picture already embedded)
        art = prop.artwork if p["artwork"]["enabled"] else None
        if art and art.get("front") and Path(art["front"]).exists():
            rep = art.get("replace") or {}
            smaller = set(rep.get("embedded") or [])      # tracks whose own cover is too small
            import artwork as _artwork
            embed = [fe["dst"] for fe in entry["files"]
                     if p["artwork"]["embed_front"] and Path(fe["src"]).suffix.lower() in EMBEDDABLE
                     and not fe.get("as_is") and (fe["src"] in smaller or not _artwork.has_front(fe["src"]))]
            embed_replace = [fe["dst"] for fe in entry["files"] if fe["dst"] in embed and fe["src"] in smaller]
            # the album's own folder: for a disc set in "Disc 01", "Disc 02" ... that is the folder
            # holding the disc folders, not the first disc's
            folder = (Path(os.path.commonpath([str(Path(fe["dst"]).parent) for fe in entry["files"]]))
                      if entry["files"] else (None if op == "copy" else Path(prop.source_dir)))
            looks = [folder]
            if folder is not None and entry["files"]:
                from engine import is_disc_dir
                if is_disc_dir(folder):           # one disc of a set ("Album/Disc 01"): the album's folder,
                    looks = [folder, folder.parent]   # where the set's cover is looked for too
                    folder = folder.parent
            # (a picture moving into the covers folder is no longer the album folder's)
            away = {c["src"] for c in entry["companions"] if c["op"] == "move" and c.get("tidy")}
            has_image = folder is None or any(d.exists() and any(x.suffix.lower() in IMAGE_EXT and str(x) not in away
                                                                 for x in d.iterdir())
                                              for d in looks)
            has_image = has_image or any(Path(c["dst"]).parent in looks and Path(c["dst"]).suffix.lower() in
                                         IMAGE_EXT for c in entry["companions"])
            for fe in entry["files"]:
                if fe["dst"] in embed and fe["op"] == "none":   # only the cover is new
                    fe["op"] = "tag" if fe["dst"] == fe["src"] else op
            # (named by Picard's cover_image_filename: cover.jpg unless your Picard says otherwise)
            cover = None if (has_image or not p["artwork"]["files"]) else \
                folder / _artwork.cover_file_name(p, (prop.targets or [{}])[0])
            group = getattr(prop, "group_files", None)
            if cover is not None and _holds_other_music(folder, entry, group):
                cover = None                  # a folder shared with other music: its cover is no one album's
            if cover is not None:
                key = fnm.fold(cover)
                me = f"artwork:{prop.source_dir}:{sorted(_release_ids(prop))}:{sorted(map(str, prop.files))}"
                if self.claimed.get(key) not in (None, me):
                    cover = None                              # another album's file goes there
                else:
                    self.claimed[key] = me
            replaced = None                   # the album's small cover file, now the larger one
            rf = rep.get("file") or _pulled_small_front(entry, art, looks, p)
            if rf and Path(rf).exists() and not _holds_other_music(Path(rf).parent, entry, group):
                try:
                    import artwork as _artwork
                    new = _artwork.as_format(Path(art["front"]), Path(rf).suffix,
                                             ex.STATE_DIR / "staging" / self.job)
                    rw = {"from": str(new), "sha256": hashlib.sha256(new.read_bytes()).hexdigest(),
                          "cover": True}
                    ce = next((c for c in entry["companions"] if c["src"] == rf), None)
                    stays = bool(entry["files"]) and all(fe["dst"] == fe["src"] for fe in entry["files"])
                    if ce is None and stays:              # tagged where it is: the cover beside it
                        ce = {"src": rf, "dst": rf, "op": "rewrite", "fingerprint": _fp(Path(rf))}
                        entry["companions"].append(ce)
                    if ce is None:                        # (copied or moved without it: the source's is not touched)
                        entry["notes"].append(f"{Path(rf).name} stays as it is: it does not go with the album")
                    elif not ce.get("rewrite"):
                        ce["rewrite"] = rw
                        replaced = ce["dst"]
                except Exception as exc:
                    entry["notes"].append(f"{Path(rf).name}: not replaced ({exc})")
            emb = art.get("embed")
            if not emb and embed:                  # (a provider result without its embedding copy)
                try:
                    import artwork as _artwork
                    emb = str(_artwork.embed_copy(Path(art["front"]), ex.STATE_DIR / "staging" / self.job,
                                                  _artwork.profile_embed_side(p["artwork"])))
                except Exception:
                    emb = None
            entry["artwork"] = {"front": art["front"], "embed": emb or art["front"],
                                "source": art.get("source", ""), "why": art.get("why", ""),
                                "embed_into": embed, "file": str(cover) if cover else None,
                                "embed_replace": embed_replace, "replaced_file": replaced,
                                "width": art.get("width"), "height": art.get("height")}
        # 6. lyrics files (.lrc) next to the tracks; never over a file that is already there
        entry["sidecars"] = []
        if identified:
            for fe in entry["files"]:
                text = (prop.sidecars or {}).get(fe["src"])
                dst = Path(fe["dst"]).with_suffix(".lrc")
                if not text or dst.exists() or any(c["dst"] == str(dst) for c in entry["companions"]):
                    continue
                entry["sidecars"].append({"src": fe["src"], "dst": str(dst), "text": text})
        # 7. the artist's photo (Enthusiast and up): into the tracks, as a picture of its own type
        # ("Artist/performer", never in place of another), and as artist.jpg in the artist folder
        # - the album folder's parent, when that folder is named after the album artist and lies
        # inside one of the profile's folders (never a library root, never over a file there)
        entry["artist_photo"] = self._artist_photo(prop, entry, op) if identified else None
        linked = _linked_tracks(entry, self.roots() + sorted(self._split_roots))   # (a linked track that gets only a cover or photo)
        if linked:
            entry["conflicts"] += linked
            entry["status"] = "blocked"
            self._release(entry, prop)                 # (its cover and artist.jpg: another album's to take)
            return entry
        if all(fe["op"] == "none" for fe in entry["files"]) and not entry["companions"] and \
                not entry["sidecars"] and \
                not (entry["artwork"] and (entry["artwork"]["embed_into"] or entry["artwork"]["file"])):
            entry["status"] = "unchanged" if entry["status"] == "ready" else entry["status"]
        return entry

    def _tidies_here(self, entry) -> bool:
        """An album that stays where it is gets its covers and misc folders there: tagged in
        place (an in-place profile), or already filed in a move profile's destination - never
        a copy profile's source, nor an album a move profile leaves in its inbox."""
        files = entry["files"]
        if not files or any(fe["dst"] != fe["src"] or fe.get("copy_of") for fe in files):
            return False
        p = self.profile
        if p["operation"] == "in_place":
            return True
        if p["operation"] != "move":
            return False
        # (any library the profile files into: the main one, a source folder's own, the lossy one)
        import profiles
        roots = profiles.filing_roots(getattr(p, "data", p))
        dests = [os.path.realpath(os.path.expanduser(str(r))).rstrip(os.sep) + os.sep for r in roots]
        return any(all(os.path.realpath(fe["src"]).startswith(d) for fe in files) for d in dests)

    def _tidy_homes(self, entry, prop):
        """(the album's folder, the folders its music lands in, the cover file's name) where its
        covers and misc folders go (tidy.target)."""
        from engine import is_disc_dir
        homes = {Path(fe["dst"]).parent for fe in entry["files"]}
        album = Path(os.path.commonpath([str(h) for h in homes]))
        if is_disc_dir(album):                # (one disc of a set, or "Album/CD1" alone: the album's folder)
            album = album.parent
        fronts = ()
        try:
            import artwork as _artwork
            fronts = (Path(_artwork.cover_file_name(self.profile, (prop.targets or [{}])[0])).stem,)
        except Exception:
            pass
        return album, homes, fronts

    def _split_op(self, entry, op, partial, organize_ok, conf):
        """(operation, beside) of one version of a split (engine._versions_split): a move or copy
        profile files it by the naming, as any album; in place - or when it is not proven
        enough to file - it moves into a folder of its own beside the folder it was found in,
        named as the naming names the album's folder (beside). (None, False): not copied."""
        p = self.profile
        why = ("Partial identification (no release proven)" if partial else
               f"confidence {conf:.1f} is below organize_min_confidence {_organize_floor_words(p)}"
               if not organize_ok else "")
        if op == "copy":
            if why:
                entry["notes"].append(f"{why}: not copied")
                return None, False
            return "copy", False
        if op == "move" and not why:
            return "move", False
        if op == "move":
            entry["notes"].append(f"{why}: not filed into the destination; split into a folder beside the "
                                  "one it was in")
        return "move", True

    def _split_folder(self, entry, vsplit):
        """The folder one version of a split goes to (entry["split"]["folder"]); a conflict when
        that folder is there already (another album's, or one made by hand) - never merged into."""
        dsts = [Path(fe["dst"]) for fe in entry["files"]]
        if not dsts:
            return
        folder = Path(os.path.commonpath([str(d.parent) for d in dsts]))
        if _DISC_NAME.match(folder.name) and folder != Path(vsplit["of"]):
            folder = folder.parent
        entry["split"]["folder"] = str(folder)
        real = os.path.realpath(str(folder))
        if real == os.path.realpath(vsplit["of"]):
            return                                        # (named as this version already: it stays)
        if os.path.lexists(folder) and not any(fe.get("copy_of") for fe in entry["files"]):
            entry["conflicts"].append(f"{folder} is there already: the versions are not split into it "
                                      "(rename or move that folder first)")

    def _release(self, entry, prop):
        """A blocked album gives up what it claimed in this plan: its files' names, its cover
        file, the artist folder's artist.jpg (another album of the artist writes it then)."""
        me = f"artwork:{prop.source_dir}:{sorted(_release_ids(prop))}:{sorted(map(str, prop.files))}"
        mine = {str(x) for x in prop.files} | {fe["src"] for fe in entry["files"]} | {me}
        photos = {fnm.fold(sc["dst"]) for sc in entry.get("sidecars") or [] if sc.get("kind") == "artist"} | \
            {fnm.fold(c["src"]) for c in entry.get("companions") or [] if (c.get("rewrite") or {}).get("artist")}
        for k in [k for k, v in self.claimed.items() if v in mine or (k in photos and v == "artist-photo")]:
            del self.claimed[k]
        claims = self.__dict__.get("artist_dirs") or {}
        for dk in list(claims):
            c = claims[dk]
            c["entries"] = [e for e in c["entries"] if e is not entry]
            if not c["entries"]:
                del claims[dk]
            elif c["sidecar"] is not None and c["sidecar"] in (entry.get("sidecars") or []):
                c["sidecar"] = None

    def _artist_photo(self, prop, entry, op):
        import artwork as _artwork
        ap = getattr(prop, "artist_photo", None)
        want = _artwork.artist_settings(self.profile) if ap else None
        if not ap or not want or not entry["files"] or not Path(ap.get("image") or "").exists():
            return None
        album = Path(os.path.commonpath([str(Path(fe["dst"]).parent) for fe in entry["files"]]))
        title = ((prop.targets[0].get("album") or [""])[0]) if prop.targets else ""
        artist_dir = _artwork.artist_dir_for(album, [ap.get("artist", "")] + list(ap.get("artist_sort") or []),
                                             self.roots(),
                                             self.profile["filenames"]["compat"], title)
        staging = ex.STATE_DIR / "staging" / self.job
        image, source = ap["image"], ap.get("source", "")
        _who, single = _album_artist(prop)
        mine = [Path(fe["src"]) for fe in entry["files"]] if single else ()
        there = _artwork.artist_folder_photo(artist_dir) if artist_dir is not None else None
        download = None                   # (TagMender's download there, which the user's own photo replaces)
        if there is not None and _artwork.written_by_us(there) and ap.get("own"):
            download, there = there, None # (a download TagMender wrote: the user's own photo wins)
        has_image = artist_dir is not None and (there is not None or download is not None or
                                                _artwork.artist_folder_has_photo(artist_dir, mine))
        if there is None and has_image and not ap.get("own"):
            return None                   # (the artist folder has its image - a folder.jpg - and this is a download)
        claims = self.__dict__.setdefault("artist_dirs", {})
        dkey = fnm.fold(artist_dir) if artist_dir is not None else None
        if there is not None:
            # the artist folder (where the album is, or goes) has its photo - the user's own, or
            # one corrected by hand: that one goes into the tracks, never a second one
            try:
                image = str(_artwork._stage(there.read_bytes(), staging)[0])
                data = Path(image).read_bytes()          # (as staged: a PNG made JPEG, the same every run)
                source = f"the artist folder's {there.name}"
            except Exception:
                return None
        elif dkey is not None and dkey in claims and (claims[dkey]["own"] or not ap.get("own")):
            # another album of this folder, this run: its photo
            c = claims[dkey]
            image, source, data = c["image"], c["source"], c["data"]
        else:
            try:                          # (a copy of its own: the photo planned is the photo written)
                image = str(_artwork._stage(Path(image).read_bytes(), staging)[0])
                data = Path(image).read_bytes()
            except Exception:
                return None
        theirs = there is not None and not _artwork.written_by_us(there)
        own_here = theirs or (there is None and (claims[dkey]["own"] if dkey in claims and
                                                 (claims[dkey]["own"] or not ap.get("own")) else bool(ap.get("own"))))
        if dkey is not None:
            c = claims.get(dkey)
            if c is not None and not c["own"] and own_here:
                # the user's own photo came with a later album: it takes over from the download an
                # earlier album of this folder claimed (that album's tracks and the artist.jpg)
                try:
                    emb_own = str(_artwork.embed_copy(Path(image), staging, _artwork.ARTIST_EMBED_MAX_SIDE))
                except Exception:
                    emb_own = None
                for e in c["entries"]:
                    if e.get("artist_photo") and emb_own:
                        e["artist_photo"].update(image=image, embed=emb_own, source=source,
                                                 mark=_artwork.artist_mark(_artwork.photo_identity(data)))
                if c["sidecar"] is not None:
                    c["sidecar"].update({"from": image, "sha256": hashlib.sha256(data).hexdigest()})
                    c["sidecar"].pop("download", None)
                c.update(image=image, source=source, data=data, own=True)
            elif c is None:
                c = claims[dkey] = {"image": image, "source": source, "data": data, "own": own_here,
                                    "entries": [], "sidecar": None}
            c["entries"].append(entry)
        authoritative = own_here
        # the photo's identity - of the artist folder's file, as it is or will be - goes into the
        # description of the picture embedded: a corrected artist.jpg replaces it on a later run
        ident = _artwork.photo_identity(data)
        into, replace = [], []
        if want["embed"]:
            fronts = set((entry.get("artwork") or {}).get("embed_into") or [])
            for fe in entry["files"]:
                ext_ = Path(fe["src"]).suffix.lower()
                if ext_ not in EMBEDDABLE or ext_ in (".m4a", ".mp4") or fe.get("as_is"):
                    continue
                # beside a front cover only (one the track has, or gets now): an artist photo is
                # never a track's only picture - players would show it as the cover
                if not (fe["dst"] in fronts or _artwork.has_front(fe["src"])):
                    continue
                marks = _artwork.our_artist_marks(fe["src"])
                types8 = [t for t, _d in _artwork._pictures(fe["src"])].count(8)
                # replaced only from the artist folder's own photo (the user's word: never by a
                # download), and only where every artist picture in the track is TagMender's
                if authoritative and marks and len(marks) == types8 and any(m != ident for m in marks):
                    into.append(fe["dst"])                 # TagMender's own, from another photo
                    replace.append(fe["dst"])
                elif not _artwork.has_artist_picture(fe["src"]):
                    into.append(fe["dst"])
            for fe in entry["files"]:
                if fe["dst"] in into and fe["op"] == "none":       # only the artist photo is new
                    fe["op"] = "tag" if fe["dst"] == fe["src"] else op
        try:
            emb = str(_artwork.embed_copy(Path(image), staging, _artwork.ARTIST_EMBED_MAX_SIDE))
        except Exception:
            emb, into = None, []
        if want["file"] and artist_dir is not None and not has_image and \
                not os.path.lexists(artist_dir / want["file"]):
            target = artist_dir / want["file"]
            key = fnm.fold(target)
            if self.claimed.get(key) is None and not any(sc["dst"] == str(target) for sc in entry["sidecars"]):
                self.claimed[key] = "artist-photo"
                data = Path(image).read_bytes()
                sc_ = {"src": entry["files"][0]["src"], "dst": str(target), "from": image,
                       "sha256": hashlib.sha256(data).hexdigest(), "kind": "artist",
                       **({} if own_here else {"download": True})}
                entry["sidecars"].append(sc_)
                if dkey is not None and claims.get(dkey) is not None and claims[dkey]["sidecar"] is None:
                    claims[dkey]["sidecar"] = sc_
        if download is not None and own_here and want["file"]:
            # the artist folder's artist.jpg is TagMender's download: the user's own photo, which
            # this album brings, takes its place (the download is kept for undo)
            key = fnm.fold(download)
            if self.claimed.get(key) is None and not any(c["src"] == str(download) for c in entry["companions"]):
                self.claimed[key] = "artist-photo"
                try:
                    new = Path(image) if download.suffix.lower() in (".jpg", ".jpeg") else \
                        _artwork.as_format(Path(image), download.suffix, staging)
                    entry["companions"].append(
                        {"src": str(download), "dst": str(download), "op": "rewrite", "fingerprint": _fp(download),
                         "rewrite": {"from": str(new), "sha256": hashlib.sha256(new.read_bytes()).hexdigest(),
                                     "artist": True}})
                except Exception as exc:
                    entry["notes"].append(f"{download.name}: not replaced by your artist photo ({exc})")
        if not into and not any(sc.get("kind") == "artist" for sc in entry["sidecars"]) and \
                not any((c.get("rewrite") or {}).get("artist") for c in entry["companions"]):
            return None
        return {"image": image, "embed": emb, "embed_into": into, "source": source,
                "artist": ap.get("artist", ""), "mark": _artwork.artist_mark(ident),
                "replace": [x for x in replace if x in into]}

    def _earlier_copy(self, dst: Path, src: Path, dest_root=None, legacy=None):
        """The copy an earlier run made of src into this destination: where TagMender
        recorded it, or at the template's name, or at the suffixed name it was given when the
        plain one was taken, or at the name an older TagMender gave it (legacy). None when
        there is none."""
        want = payload.digest(src)
        if want is None:
            return None
        cands = [Path(p) for p in copies_of(src, dest_root)]
        for d in [dst] + (list(legacy) if isinstance(legacy, (list, tuple)) else [legacy]):
            if d is not None:
                cands += [d, d.with_name(d.stem + fnm.collision_suffix(str(src)) + d.suffix)] + \
                    [d.with_name(f"{d.stem} ({n}){d.suffix}") for n in range(2, 2 + SECOND_NAMES)]
        for cand in cands:
            try:
                if not cand.exists() or os.path.samefile(cand, src) or fnm.fold(cand) in self.claimed:
                    continue
            except OSError:
                continue
            if payload.digest(cand) == want:
                return cand
        return None

    def _prior_album_dir(self, files, dest_root=None) -> Path | None:
        """The album folder an earlier run of a copy profile put this album's copies in (as
        recorded), when they are still there: the album stays there - no second copy when a
        folder clash went away or the template now gives another path."""
        found = {}
        for f in files:
            want = None
            for p in copies_of(f, dest_root):
                p = Path(p)
                if not p.exists():
                    continue
                want = want or payload.digest(f)
                if want is not None and payload.digest(p) == want:
                    d = p.parent.parent if _DISC_NAME.match(p.parent.name) else p.parent
                    found[d] = found.get(d, 0) + 1
                    break
        return max(found, key=found.get) if found else None

    def _versions_apart(self, kept, targets, template, rel_tags) -> list:
        """The versions (%_version%: "Stereo", "5.1" ...) of an album that share a disc (a DVD's or
        Blu-ray's mixes) or whose files the naming script names alike - each is then put in a
        folder of its own, named after it (so a later run knows each version by its folder).
        [] when they are discs of their own (an SACD's layers) and named apart."""
        compat = self.profile["filenames"].get("compat", "windows")
        by_name, seen, by_disc = {}, [], {}
        for f in kept:
            t = targets.get(f) or {}
            ver = _vals(t.get("~version"))
            if not ver:
                continue
            seen.append(str(ver[0]))
            # (two versions on one disc - a DVD's or Blu-ray's mixes: apart too, whatever their
            # names, so a later run knows each version again by its folder)
            by_disc.setdefault(str((_vals(t.get("discnumber")) or ["1"])[0]), set()).add(fnm.fold(str(ver[0])))
            names = {**{"_" + k[1:]: v for k, v in t.items() if k.startswith("~") and k != "~remove"},
                     **{k: v for k, v in t.items() if not k.startswith("~")}, **rel_tags}
            try:
                comps = naming.render(template, names, self._picard(), compat, own=Path(f).stem)
            except Exception:
                continue
            by_name.setdefault(tuple(fnm.fold(c) for c in comps), set()).add(fnm.fold(str(ver[0])))
        if not any(len(v) > 1 for v in list(by_name.values()) + list(by_disc.values())):
            return []
        return list(dict.fromkeys(seen))

    def _own_album_dir(self, dst: Path, prop, own_files, cache: dict) -> Path:
        """dst, moved into an album folder of its own when the template gives this release
        the same folder as a different release (in this plan, or already on disk): "Now
        That's What I Call Music! 47" and "... 48" shortened to five words would share one."""
        album_dir = dst.parent.parent if _DISC_NAME.match(dst.parent.name) else dst.parent
        if "map" not in cache and cache.get("prior") is not None:
            cache["map"] = (album_dir, cache["prior"])
        if "map" not in cache:
            me = _release_ids(prop)
            key = fnm.fold(album_dir)
            owner = self._album_dirs.get(key)
            if owner is None:                          # an album already sitting there, in this plan
                owner = next((ids for d, ids, src in self._resident if d == key and src != prop.source_dir), None)
            # a single's folder (Artist/, Artist/Singles) is shared by design; a folder named
            # after the release is an album folder, also for a one-track release
            title = str(((prop.targets or [{}])[0].get("album") or [""])[0])
            as_named = fnm.safe_component(title, self.profile["filenames"].get("compat", "windows")) if title else ""
            shared = len(prop.files) == 1 and not (as_named and fnm.fold(as_named)[:12] in fnm.fold(album_dir.name))
            ignore = self._planned
            clash = not shared and ((owner is not None and not (owner & me)) or
                                    (owner is None and _other_release_there(album_dir, me, ignore,
                                                              lambda: self._digests_of(own_files))))
            new = album_dir
            if clash:
                tag = fnm.collision_suffix(" ".join(sorted(me)))
                limits = self.profile["filenames"]
                room = int(limits.get("max_dir_bytes", 255)) - len(tag.encode("utf-8", "surrogateescape"))
                base = album_dir.name
                if len(base.encode("utf-8", "surrogateescape")) > room:
                    base = fnm.truncate_words(base, room, limits.get("compat", "windows"))
                new = album_dir.with_name(base + tag)
            if not shared:
                self._album_dirs[fnm.fold(new)] = me
            cache["map"] = (album_dir, new)
        old, new = cache["map"]
        try:
            return new / dst.relative_to(old)
        except ValueError:
            return dst

    def _digests_of(self, files) -> set:
        """Audio digests of these files: an earlier copy of them is not another release."""
        out = set()
        for f in files:
            d = self._digest_cache.get(str(f)) if hasattr(self, "_digest_cache") else None
            if d is None:
                try:
                    d = payload.digest(f)
                except Exception:
                    d = None
                self._digest_cache = getattr(self, "_digest_cache", {})
                self._digest_cache[str(f)] = d
            if d:
                out.add(("digest", d))
        return out

    def _below_source(self, folder: Path) -> Path:
        """The album folder's path below the deepest profile source holding it
        ("Artist/Album"; nothing for loose tracks at the top of a source), else its last two
        folders."""
        real = os.path.realpath(str(folder))
        best = None
        for s_ in self.profile["sources"]:
            r = os.path.realpath(os.path.expanduser(s_["path"]))
            if (real == r or real.startswith(r.rstrip(os.sep) + os.sep)) and (best is None or len(r) > len(best)):
                best = r
        if best:
            return Path(os.path.relpath(real, best))
        return Path(folder.parent.name) / folder.name

    def _spelled(self, path: Path, root: Path) -> Path:
        """path, with each folder below root spelled as it already is on disk when the only
        difference is the Unicode form (and, with Windows-compatible names, letter case):
        never a second, look-alike "Beyoncé" folder."""
        windows = self.profile["filenames"].get("compat") == "windows"
        cur = Path(root)
        rel = Path(path).relative_to(root)
        parts = list(rel.parts)
        for k, comp in enumerate(parts[:-1]):
            if (cur / comp).is_dir():
                cur = cur / comp
                continue
            listing = self._listing(cur)
            key = unicodedata.normalize("NFC", comp)
            key = key.casefold() if windows else key
            if key not in listing:
                listing[key] = comp                      # later albums of this plan use this spelling
                cur = cur / comp
                continue
            cur = cur / listing[key]
        return cur / parts[-1]

    def _listing(self, d: Path) -> dict:
        if d not in self._dirs:
            windows = self.profile["filenames"].get("compat") == "windows"
            out = {}
            try:
                for name in os.listdir(d):
                    k = unicodedata.normalize("NFC", name)
                    out.setdefault(k.casefold() if windows else k, name)
            except OSError:
                pass
            self._dirs[d] = out
        return self._dirs[d]

    def _taken_alike(self, cand: Path, src: Path) -> bool:
        """With Windows-compatible names, a file whose name differs only in case or Unicode
        form is taken too (Windows and SMB see one name)."""
        if self.profile["filenames"].get("compat") != "windows" or not cand.parent.is_dir():
            return False
        k = unicodedata.normalize("NFC", cand.name).casefold()
        for name in os.listdir(cand.parent):
            if unicodedata.normalize("NFC", name).casefold() == k:
                other = cand.parent / name
                return not (src.exists() and os.path.samefile(other, src) and fnm.fold(other) == fnm.fold(src))
        return False

    def _unwritable(self, entry):
        """Files whose folder cannot be written (a read-only share, no permission): the album
        is left for later with a conflict (on_unwritable skip_album), or only those files are
        left out (skip_file). A copy only reads its source. True when there were any."""
        bad = [fe for fe in entry["files"] if fe["op"] in ("tag", "move") and
               not os.access(Path(fe["src"]).parent, os.W_OK | os.X_OK)]
        if not bad:
            return False
        if self.profile.get("on_unwritable", "skip_album") == "skip_file":
            for fe in bad:
                entry["files"].remove(fe)
                entry["skipped"].append({"file": fe["src"], "label": "Skipped", "rule": "cannot be written",
                                         "matched": str(Path(fe["src"]).parent)})
            entry["notes"].append(f"{len(bad)} file(s) left out: their folder cannot be written (read-only?)")
        else:
            entry["conflicts"].append(f"{Path(bad[0]['src']).parent} cannot be written (read-only, or no "
                                      "permission): the album is left as it is")
        return True

    def _second_names(self, dst: Path, src: Path) -> list:
        """The names a second file of dst's name may take, in order (naming.duplicate_suffix):
        number - " (2)", " (3)" ... (the first free one each time the plan is made: the same
        while the folder stays as it is); code - " ~1a2b", the same for this file always."""
        if ((self.profile.get("naming") or {}).get("duplicate_suffix") or "number") == "code":
            return [dst.with_name(dst.stem + fnm.collision_suffix(str(src)) + dst.suffix)]
        return [dst.with_name(f"{dst.stem} ({n}){dst.suffix}") for n in range(2, 2 + SECOND_NAMES)]

    def _unique(self, dst: Path, src: Path, entry):
        """dst, or dst with a suffix when another file has (or will have) that name; None
        when it is not planned: a conflict noted when even the suffixed names are taken, or
        when on_existing is "ask" (an unattended profile keeps both instead); the file left
        out, noted, when on_existing is "skip" and a different file is already there."""
        # (the suffixed name an earlier run gave this very file, when it was moved: kept - its
        # suffix came from where it was then, and another would rename it on every run)
        own = src if src.parent == dst.parent and src.suffix == dst.suffix and \
            re.fullmatch(re.escape(dst.stem) + r"( ~[0-9a-f]{4}| \([0-9]{1,3}\))", src.stem) else None
        on_existing = self.profile.get("on_existing", "keep_both")
        if on_existing == "ask" and self.profile.get("unattended"):
            on_existing = "keep_both"                # (nobody is there to ask)
        for attempt, cand in enumerate([dst] + ([own] if own else self._second_names(dst, src))):
            key = fnm.fold(cand)
            other = self.claimed.get(key)
            same_file = src.exists() and cand.exists() and os.path.samefile(cand, src)
            # this file, spelled differently, or a hard link to it already there (an *arr import)
            taken_on_disk = (cand.exists() and not same_file) or \
                (not cand.exists() and self._taken_alike(cand, src))
            if other in (None, str(src)) and not taken_on_disk:
                self.claimed[key] = str(src)
                if attempt:
                    entry["notes"].append(f"{dst.name} is taken; using {cand.name}")
                return cand
            if attempt == 0 and taken_on_disk and other in (None, str(src)) and on_existing != "keep_both":
                if on_existing == "skip":
                    entry["notes"].append(f"{dst} is already there (a different file); {src.name} is left out")
                    entry["skipped"].append({"file": str(src), "label": "Skipped", "rule": "already there",
                                             "matched": str(dst)})
                else:
                    entry["conflicts"].append(f"{dst} is already there (a different file): choose which to keep")
                return None
        entry["conflicts"].append(f"{dst} already exists (a different file) and so does the suffixed name")
        return None

    def build(self, proposals) -> dict:
        # every file this plan starts from (and its inode): never "another release" in a folder
        for pr in proposals:
            for f in pr.files:
                self._planned.add(str(f))
                try:
                    st = os.stat(f)
                    self._planned.add((st.st_dev, st.st_ino))
                except OSError:
                    pass
        for pr in proposals:
            if pr.status == "identified" and len(pr.files) > 1:
                self._resident.append((fnm.fold(pr.source_dir), _release_ids(pr), pr.source_dir))
        albums = [self.album(pr) for pr in proposals]
        _split_together(albums)
        for a in albums:                                  # (your edits: kept by a new preview)
            e = edit_for(self.edits, a["source_dir"], [fe["src"] for fe in a["files"]] or a.get("album_files"))
            if e:
                apply_edits(a, e, self.profile, self._file_settings())
        counts = {}
        for a in albums:
            counts[a["status"]] = counts.get(a["status"], 0) + 1
        return {"version": PLAN_VERSION, "job": self.job, "created": time.time(), "profile": self.profile.name,
                "profile_data": self.profile.to_dict(), "operation": self.profile["operation"],
                "roots": list(dict.fromkeys(self.roots() + sorted(self._split_roots))),
                "library_roots": self.library_roots(), "albums": albums, "summary": counts,
                "discogs_settings": _enrichment_summary(self.profile),
                "preserve_timestamps": self.profile.preserve_timestamps()}

    def library_roots(self):
        """The profile's own folders (not a folder named on the command line)."""
        import profiles
        p = self.profile
        r = [os.path.realpath(os.path.expanduser(s["path"])) for s in p["sources"]]
        for d in profiles.filing_roots(getattr(p, "data", p)) + [p["unresolved"].get("move_to")]:
            if d:
                r.append(os.path.realpath(os.path.expanduser(d)))
        return r

    def roots(self):
        import profiles
        p = self.profile
        r = [os.path.realpath(os.path.expanduser(s["path"])) for s in p["sources"]]
        for d in profiles.filing_roots(getattr(p, "data", p)) + [p["unresolved"].get("move_to")]:
            if d:
                r.append(os.path.realpath(os.path.expanduser(d)))
        return list(dict.fromkeys(r + self.extra_roots))


# what says a file was tagged from one release already
RELEASE_IDENTITY = ("musicbrainz_albumid", "discogs_release_id")


def _names_release(files) -> bool:
    """Some of the files carry a release ID or TagMender's Resolved mark."""
    for f in files:
        try:
            t = tagio.read(f)
        except Exception:
            continue
        if any((t.get(k) or [""])[0] for k in RELEASE_IDENTITY) or \
                str((t.get("tagmender") or [""])[0]).lower() == "resolved":
            return True
    return False


TIERS = ("exact", "album_only", "recording", "partial", "unresolved", "ignored")
TIER_WORDS = {"exact": "exact release", "album_only": "album only (pressing unknown)",
              "recording": "recording only", "partial": "Partial (album, no release)", "unresolved": "Unresolved",
              # (one tier for both actions: the rule's own label, Ignored or Skipped, says which)
              "ignored": "excluded by a rule"}


def _album_only_reason(prop) -> str:
    """TAGMENDER_REASON of an album-only album: the album is known, its pressing is not."""
    tied = next((w for w in prop.why or [] if str(w).startswith("pressing unknown")), "")
    words = "album known, pressing not established" + (f": {tied.split(':', 1)[0]}" if tied else "")
    return reasons.encode(words, ["ambiguous_release"])


def tier_of(prop) -> str:
    """How far identification got (Proposal.tier; an older proposal without one: from its status)."""
    t = getattr(prop, "tier", "") or ""
    if t in TIERS:
        return t
    return "unresolved" if prop.status == "unresolved" else "exact"


def build(proposals, profile, job_id=None, **kw) -> dict:
    for prop in proposals:          # absolute paths: undo and recover may run from another folder
        prop.source_dir = os.path.abspath(os.path.expanduser(str(prop.source_dir)))
        prop.files = [os.path.abspath(os.path.expanduser(str(f))) for f in prop.files]
    return Planner(profile, job_id, **kw).build(proposals)


def save(plan: dict, path) -> Path:
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp")
    # ASCII: a file name that is not valid UTF-8 (surrogate escapes) comes back exactly
    tmp.write_text(json.dumps(plan, indent=1, ensure_ascii=True, default=str), encoding="ascii")
    os.replace(tmp, path)
    return path


def load(path) -> dict:
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise ValueError(f"{Path(path).name} is not a plan")
    if plan.get("version") != PLAN_VERSION:
        raise ValueError(f"plan version {plan.get('version')} is not {PLAN_VERSION}")
    return plan


def _enrichment_summary(profile) -> str:
    """One line saying how Discogs data is applied, and where that came from."""
    try:
        import tagger_settings
        s = profile.enrichment()
        src = (tagger_settings.source_label() if profile.follows_tagger()
               else "the plugin's defaults (follow_tagger_settings is off)")
        import profiles as _pf
        merge_from = ("from Sources" if s["merge_mode"] == _pf.SOURCE_MERGE.get(profile.source())
                      else "this profile's discogs_enrichment")
        return (f"Sources: {_pf.SOURCE_WORDS.get(profile.source(), profile.source())}; "
                f"Discogs Enrichment: merge {s['merge_mode']} ({merge_from}), styles {s['style_mode']}, "
                f"marketplace {'on' if s['marketplace'] else 'off'}, "
                f"original date in Date {'on' if s['prefer_original_date'] else 'off'}  (from {src})")
    except Exception:
        return ""


# --------------------------------------------------------------------------
# preview text (the CLI and GUI show the same thing)
# --------------------------------------------------------------------------
def format_note(fmt: dict) -> str:
    """What saving a file does beside its fields (tagio.file_options), in words."""
    out = []
    if fmt.get("v2"):
        out.append(f"as ID3v2.{fmt['v2']}")
    if fmt.get("join"):
        out.append(f"several values joined with \"{fmt['join']}\"")
    if fmt.get("v1") == 2:
        out.append("an ID3v1 tag added")
    elif fmt.get("v1") == 0:
        out.append("its ID3v1 tag removed")
    if fmt.get("enc") in (0, 1) and fmt.get("enc_change"):    # (only when a frame changes encoding)
        out.append("text in " + ("UTF-16" if fmt["enc"] == 1 else "Latin-1 (ISO-8859-1)"))
    if fmt.get("strip_ape"):
        out.append("its APEv2 tag removed")
    if fmt.get("strip_id3"):
        out.append("its ID3 tags removed")
    if fmt.get("fix_seektable"):
        out.append("its empty seek table removed")
    return ", ".join(out)


def preview(plan: dict, verbose=False) -> str:
    out = [f"Plan {plan['job']}  profile {plan['profile']}  ({plan['operation']})"]
    if plan.get("discogs_settings"):
        out.append(plan["discogs_settings"])
    for a in plan["albums"]:
        ident = a["identification"]
        shown = "HELD" if a["status"] == "ready" and held(a) else a["status"].upper()   # WEB-1
        head = f"\n[{shown}] {a['source_dir']}"
        if a["status"] == "excluded":
            head += f"\n    {a['label']} by rule '{a['rule']['name']}' (matched {a['rule']['matched']})"
        elif ident.get("status") == "identified":
            tier = ident.get("tier") or "exact"
            head += (f"\n    {ident['provider']} {ident['release_id'] or '(no release)'}  score {ident['confidence']:.1f}"
                     + (f"  margin {ident['margin']:.1f}" if ident.get("margin") is not None else "")
                     + f"  [{TIER_WORDS.get(tier, tier)}]")
            if tier in ("partial", "album_only"):
                codes, words = reasons.parse(ident.get("reason", ""))
                head += (f"\n    {'Partial' if tier == 'partial' else 'Album only (marked Partial)'}: "
                         f"{', '.join(codes)} — {words}")
            if ident.get("evidence"):
                head += "\n    agreed: " + "; ".join(ident["evidence"][:6])
            if ident.get("why"):
                head += "\n    doubts: " + "; ".join(ident["why"][:4])
        else:
            codes, words = reasons.parse(ident.get("reason", ""))
            head += f"\n    Unresolved: {', '.join(codes)} — {words}"
        if a.get("split"):
            sp = a["split"]
            head += (f"\n    split: version {sp['index'] + 1} of {sp['count']} found in {sp['of']}"
                     + (f", into {sp['folder']}" if sp.get("folder") else ""))
        if ident.get("provider_errors"):
            head += "\n    provider problems (recorded): " + "; ".join(f"{k}: {v}" for k, v in
                                                                      ident["provider_errors"].items())
        out.append(head)
        for c in a.get("conflicts") or []:
            out.append(f"    CONFLICT: {c}")
        for n in a.get("notes") or []:
            out.append(f"    note: {n}")
        for s in a.get("skipped") or []:
            out.append(f"    {s['label']}: {Path(s['file']).name} ({s['rule']}: {s['matched']})")
        for fe in a["files"]:
            if fe["op"] == "none" and not verbose:
                continue
            move = f"  ->  {fe['dst']}" if fe["dst"] != fe["src"] else ""
            out.append(f"    {fe['op']:<5} {Path(fe['src']).name}{move}")
            if fe.get("format"):
                out.append(f"           saved    {format_note(fe['format'])} (your tagger settings' tag options)")
            for r in fe["diff"]:
                if r["action"] in ("added", "changed", "removed") or verbose:
                    b = "; ".join(r["before"]) or "—"
                    af = "; ".join(r["after"]) or "—"
                    out.append(f"           {r['action']:<9} {r['field']}: {b[:50]} => {af[:50]}"
                               + (f"  ({r['note']})" if r.get("note") else ""))
        for c in a.get("companions") or []:
            note = "" if not c.get("rewrite") else \
                "  (replaced by the larger cover; the old one is kept for undo)" if c["rewrite"].get("cover") \
                else "  (TagMender's downloaded artist photo replaced by yours; kept for undo)" \
                if c["rewrite"].get("artist") else "  (file references updated)"
            if c.get("tidy") and not note:
                note = "  (into the covers folder)" if c["tidy"] == "covers" else "  (into the misc folder)"
            out.append(f"    {c['op']:<5} {Path(c['src']).name}  ->  {c['dst']}{note}")
        if a.get("artwork"):
            art = a["artwork"]
            if art["embed_into"]:
                rep = len(art.get("embed_replace") or [])
                out.append(f"    art   front cover embedded into {len(art['embed_into'])} file(s) from {art['source']}"
                           + (f" ({art.get('width')}x{art.get('height')}; replaces a smaller cover in {rep})"
                              if rep else ""))
            if art["file"]:
                out.append(f"    art   {art['file']}")
        if a.get("artist_photo") and a["artist_photo"]["embed_into"]:
            ap = a["artist_photo"]
            out.append(f"    art   artist photo of {ap['artist']} embedded into {len(ap['embed_into'])} file(s) "
                       f"from {ap['source']}"
                       + (f" (in place of the one TagMender embedded before in {len(ap['replace'])})"
                          if ap.get("replace") else ""))
        for sc in a.get("sidecars") or []:
            if sc.get("kind") == "artist":
                out.append(f"    art   {sc['dst']}  (artist photo, new file)")
            else:
                out.append(f"    lyric {Path(sc['dst']).name}  (time-synced lyrics, new file)")
    s = plan["summary"]
    out.append("\n" + ", ".join(f"{v} {k}" for k, v in sorted(s.items())))
    return "\n".join(out)


# ---- your own edits (the inspector's Tags and Artwork tabs) ----------------------------------
# An edit of one album, as the app keeps it (JSON): {"files": [the album's files], "tags": {field:
# value}, "file_tags": {file: {field: value}}, "cover": {"image": a staged image, "source": where
# it came from, "width", "height"} | None}. A value is a list (the values, in order), None (the
# tag removed) or KEEP (the file's own value kept: "Revert to Original"). A file's own edit wins
# over the album's. The planner applies the edits after it planned the album (Planner(edits=...),
# engine.Engine.edits), so a new preview keeps them; the app applies them to the plan it shows
# (apply_edits), and Apply writes them as any planned change - staged, verified, journaled and
# undoable. What you typed wins over the profile's field policy and the match's confidence.
KEEP = {"keep": True}
EDITABLE = ("ready", "unchanged", "unresolved")      # (an album blocked or excluded is never written)
BY_YOU = "you"                                       # a row's "by": the value is your edit
_AUTO = ("changes", "diff", "op", "format", "names", "first", "lenient")


def edit_for(edits, source_dir, files):
    """The edit of this album from edits {realpath(album folder): [edit, ...]}: the one naming
    the most of its files (a folder may hold several albums), else None."""
    got = (edits or {}).get(os.path.realpath(str(source_dir)))
    if isinstance(got, dict):
        got = [got]
    mine = {os.path.realpath(str(f)) for f in files or []}
    best, most = None, 0
    for e in got or []:
        if not isinstance(e, dict):
            continue
        n = len(mine & {os.path.realpath(str(f)) for f in e.get("files") or []})
        if n > most or (not mine and best is None):
            best, most = e, n
    return best


def template_fields(template) -> set:
    """The tags a naming script reads (%discnumber%, %album% ...), lower case; never its own
    variables (%_edition%) or Picard's hidden ones."""
    return {m.lower() for m in re.findall(r"%([A-Za-z0-9][A-Za-z0-9_:]*)%", str(template or ""))}


def _naming_edit(edit, fields) -> dict:
    """The part of an edit the naming reads: {"tags": {field: value}, "file_tags": {realpath:
    {field: value}}} for the fields in fields; {} when it touches none."""
    if edit_empty(edit):
        return {}
    want = {str(f).lower() for f in fields or ()}

    def mine(d):
        return {k: v for k, v in (d or {}).items() if str(k).lower() in want}
    tags = mine(edit.get("tags"))
    per = {os.path.realpath(str(f)): mine(v) for f, v in (edit.get("file_tags") or {}).items()}
    per = {f: v for f, v in per.items() if v}
    return {"tags": tags, "file_tags": per} if tags or per else {}


def _edited_names(naming_edit, f) -> dict:
    """{field: value} of naming_edit for file f (its own edit over the album's)."""
    if not naming_edit:
        return {}
    return {str(k).lower(): v for k, v in dict(naming_edit.get("tags") or {}, **(
        (naming_edit.get("file_tags") or {}).get(os.path.realpath(str(f))) or {})).items()}


def edit_renames(entry, edit) -> bool:
    """Whether an edit of this album changes what its files are named after (a field its
    naming script reads, where the files are moved, copied or renamed) from what the plan
    named them with: apply_edits cannot name them again on the preview shown - a new preview
    (Planner(edits=...), engine.Engine.edits) does."""
    fields = entry.get("naming_fields")
    if not fields:
        return False
    return _naming_edit(edit, fields) != (entry.get("naming_edit") or {})


def _note_renames(entry, edit):
    """entry["renames"]: why this preview's names are no longer what your edits make them (a
    disc number, title ... the naming reads, edited or reverted since the album was planned):
    apply_edits cannot name files again - preview again, with the edits, to rename them."""
    entry.pop("renames", None)
    if not edit_renames(entry, edit):
        return
    now, was = _naming_edit(edit, entry["naming_fields"]), entry.get("naming_edit") or {}
    fields = set()
    for d in (now, was):
        fields |= {str(k).lower() for k in d.get("tags") or {}}
        fields |= {str(k).lower() for v in (d.get("file_tags") or {}).values() for k in v}
    entry["renames"] = (f"your edit of {', '.join(sorted(fields))} changes where the files go: "
                        "preview again to name them with it")


def edit_empty(edit) -> bool:
    return not edit or not (edit.get("tags") or any((edit.get("file_tags") or {}).values()) or edit.get("cover"))


def file_settings(profile) -> dict:
    """Picard's settings for saving a file (the ID3v2 version, ID3v1 ...), as the planner reads them."""
    return Planner(profile, rules=[], overrides=set())._file_settings()


def _restore(entry):
    """The album as the planner planned it, before any edit (the edits are applied to that)."""
    for fe in entry.get("files") or []:
        auto = fe.pop("auto", None)
        if auto is None:
            continue
        for k in _AUTO:
            if auto.get(k) is None:
                fe.pop(k, None)
            else:
                fe[k] = auto[k]
    for fe in entry.get("files") or []:
        if "auto_dst" in fe:                          # (a match moved it: where the planner put it)
            fe["dst"] = fe.pop("auto_dst")
    if "auto_artwork" in entry:
        entry["artwork"] = entry.pop("auto_artwork")
    for k in ("artist_photo", "sidecars"):
        if f"auto_{k}" in entry:
            entry[k] = entry.pop(f"auto_{k}")
    for ce in entry.get("companions") or []:
        if "auto_rewrite" in ce:
            ce["rewrite"] = ce.pop("auto_rewrite")
    if "auto_status" in entry:
        entry["status"] = entry.pop("auto_status")
    entry.pop("edited", None)


def _snapshot(entry):
    import copy
    for fe in entry.get("files") or []:
        fe["auto"] = copy.deepcopy({k: fe.get(k) for k in _AUTO})
    entry["auto_artwork"] = copy.deepcopy(entry.get("artwork"))
    entry["auto_status"] = entry.get("status")


def _edit_file(fe, wanted: dict, fmt: dict | None):
    """fe's changes and rows with your values for the fields in wanted."""
    src = fe["src"]
    try:
        before = tagio.read(src)
    except Exception:
        before = {}
    changes, rows = dict(fe.get("changes") or {}), list(fe.get("diff") or [])
    for field_, val in wanted.items():
        if not field_ or field_.startswith("~"):
            continue
        key = tagio.canonical(src, tagio.field_name(field_))
        if val is not None and val != KEEP and not tagio.holds(src, key):
            continue                                  # (no place for it in this kind of file)
        had = _vals(before.get(key))
        if key in ("title", "artist", "album", "genre") and fe.get("first"):
            fe["first"].pop(key, None)                    # (the planner's ID3v1 value: yours replaces it)
            if not fe["first"]:
                fe.pop("first")
        names = {key, field_, tagio.field_name(field_)}
        rows = [r for r in rows if r["field"] not in names]
        for k in names:
            changes.pop(k, None)
        if val == KEEP:
            if had:
                rows.append({"field": key, "before": had, "after": had, "action": "kept (your edit)", "by": BY_YOU})
            continue
        if val is None:
            if had:
                changes[key] = None
                rows.append({"field": key, "before": had, "after": [], "action": "removed", "by": BY_YOU})
            continue
        held = tagio.stored(src, {field_: _vals(val)}, fmt)
        new = _vals(held.get(key, next(iter(held.values()), None)) if held else _vals(val))
        if new == had:
            rows.append({"field": key, "before": had, "after": had, "action": "kept (your edit)", "by": BY_YOU})
            continue
        for k, v in (held or {key: new}).items():
            changes[k] = _vals(v) or None
        rows.append({"field": key, "before": had, "after": new, "action": "changed" if had else "added",
                     "by": BY_YOU})
        if field_ != tagio.field_name(field_):          # (a custom tag spelled "MyTag": written so)
            fe["names"] = dict(fe.get("names") or {}, **{tagio.field_name(field_): field_})
        if key in ("title", "artist", "album", "genre") and len(_vals(val)) > 1:
            fe["first"] = dict(fe.get("first") or {}, **{key: [str(_vals(val)[0])]})
    fe["changes"], fe["diff"] = changes, rows
    if changes and fmt and not fe.get("format"):
        fe["format"] = fmt


MATCH = "match"                                      # an edit's pairs (gui.manual_match.MATCH_KEY)


def _number(v) -> int:
    """A tag's track or disc number ("3", ["3/12"]) as a number; 0 when it has none."""
    m = re.match(r"\s*(\d+)", (_vals(v) or [""])[0])
    return int(m.group(1)) if m else 0


def _position(changes: dict, before: dict) -> tuple:
    """(disc, track number) a file will have: the changes' value, else the file's own."""
    def pick(key):
        return _number(changes[key]) if key in (changes or {}) else _number(before.get(key))
    return (pick("discnumber") or 1, pick("tracknumber"))


def _place_by_match(entry, match: dict):
    """Files named and placed after the tracks your match gave them (Album > Match Files
    Manually): a file matched to another track than the plan gave it takes the new name the plan
    gave the file that had that track (with its own extension); a file left unmatched stays where
    it is (written in place, or left alone when the album is copied). Only where the plan renames
    or moves files; the covers and photos to embed, and the lyrics files, follow the files (a
    lyrics file of a track the file is no longer is not written)."""
    unmatched = {os.path.realpath(str(p)) for p in match.get("unmatched") or []}
    files = [fe for fe in entry.get("files") or [] if not fe.get("as_is")]
    pos, planned_at = {}, {}
    for fe in files:
        auto = fe.get("auto") or {}
        try:
            before = tagio.read(fe["src"])
        except Exception:
            before = {}
        was = _position(auto.get("changes") or {}, before)
        pos[fe["src"]] = (was, _position(fe.get("changes") or {}, before))
        planned_at.setdefault(was, fe["dst"])
    moved, taken = {}, set()
    later = []
    for fe in files:
        src, old = fe["src"], fe["dst"]
        if os.path.realpath(src) in unmatched:
            new = src
            if fe["op"] == "copy":                    # (a copy is never written over the original)
                fe["op"], fe["changes"], fe["diff"] = "none", {}, []
            else:
                fe["op"] = "tag" if fe.get("changes") else "none"
        elif old == src or pos[src][0] == pos[src][1]:
            new = old                                 # (named as planned: in place, or its own track)
        else:
            new = planned_at.get(pos[src][1])
            if new is None:                           # (a track no file of the plan had: after the others)
                later.append(fe)
                continue
            new = str(Path(new).with_suffix(Path(src).suffix))
        if new != old:
            fe["auto_dst"] = old
            fe["dst"] = new
            moved[old] = new
        taken.add(new)
    for fe in later:                                  # (its own planned name, unless another file has it now)
        old = fe["dst"]
        if old in taken:
            fe["auto_dst"] = old
            fe["dst"] = str(Path(old).parent / Path(fe["src"]).name)
            moved[old] = fe["dst"]
        taken.add(fe["dst"])
    if not moved:
        return
    import copy
    for k in ("artist_photo", "sidecars"):           # (as planned, for _restore: the match moved the files)
        if k in entry and f"auto_{k}" not in entry:
            entry[f"auto_{k}"] = copy.deepcopy(entry[k])
    for key in ("artwork", "artist_photo"):
        art = entry.get(key) or {}
        for lst in ("embed_into", "embed_replace"):
            if art.get(lst):
                art[lst] = [moved.get(d, d) for d in art[lst]]
    if entry.get("sidecars"):
        changed = {fe["src"] for fe in files if fe.get("auto_dst") is not None and
                   (os.path.realpath(fe["src"]) in unmatched or pos[fe["src"]][0] != pos[fe["src"]][1])}
        entry["sidecars"] = [sc for sc in entry["sidecars"] if sc.get("src") not in changed]


def _embed_front(profile) -> bool:
    try:
        return bool(profile["artwork"]["embed_front"])
    except Exception:
        return True


def _edit_cover(entry, cover: dict, profile):
    """Your cover in place of the new one: embedded into every track (in place of the cover
    each has), and in the cover file the plan writes or rewrites."""
    import artwork as _artwork
    image = Path(cover["image"])
    stage = ex.STATE_DIR / "staging" / "your-covers"
    emb = str(_artwork.embed_copy(image, stage))
    files = [fe for fe in entry.get("files") or [] if not fe.get("as_is")]
    into = [fe["dst"] for fe in files if _embed_front(profile) and Path(fe["src"]).suffix.lower() in EMBEDDABLE
            and (fe["op"] != "none" or fe["dst"] == fe["src"])]
    old = entry.get("artwork") or {}
    replace = [fe["dst"] for fe in files if fe["dst"] in into and _artwork.has_front(fe["src"])]
    for fe in files:
        if fe["dst"] in into and fe["op"] == "none":
            fe["op"] = "tag"
    replaced = old.get("replaced_file")
    for ce in entry.get("companions") or []:
        if replaced and ce.get("dst") == replaced and (ce.get("rewrite") or {}).get("cover"):
            new = _artwork.as_format(image, Path(ce["src"]).suffix, stage)
            ce["auto_rewrite"] = ce["rewrite"]
            ce["rewrite"] = dict(ce["rewrite"], **{"from": str(new),
                                                   "sha256": hashlib.sha256(new.read_bytes()).hexdigest()})
    entry["artwork"] = {"front": str(image), "embed": emb, "source": cover.get("source") or "Your image",
                        "why": "your choice", "embed_into": into, "file": old.get("file"),
                        "embed_replace": replace, "replaced_file": replaced,
                        "width": cover.get("width"), "height": cover.get("height"), "by": BY_YOU}


def apply_edits(entry: dict, edit: dict | None, profile=None, settings=None) -> dict:
    """entry (an album of a plan) with your edits applied, in place, from what the planner
    planned (applied again, an edit replaces the last one; none: the album as planned).
    Reads the files' tags (never writes them). An album that is not to be written (blocked,
    excluded, done) is left as it is."""
    _restore(entry)
    _note_renames(entry, edit)
    if edit_empty(edit) or entry.get("status") not in EDITABLE:
        return entry
    _snapshot(entry)
    album_tags = dict(edit.get("tags") or {})
    per_file = {os.path.realpath(k): v for k, v in (edit.get("file_tags") or {}).items()}
    for fe in entry.get("files") or []:
        wanted = dict(album_tags, **(per_file.get(os.path.realpath(fe["src"])) or {}))
        if not wanted or fe.get("as_is"):
            continue
        fmt = fe.get("format")
        if fmt is None and settings is not None:
            try:
                fmt = tagio.file_options(fe["src"], settings)
            except Exception:
                fmt = None
        _edit_file(fe, wanted, fmt)
        if "~rating" in wanted and getattr(tagio, "WRITES_RATING", False):
            import mb_account                          # (your stars: tagio.write saves and reads them back)
            mb_account.plan_rating(fe, wanted["~rating"])
            if "~rating" in fe["changes"]:            # (tagio.read has no "~rating": checked in tagio.write)
                fe["lenient"] = list(dict.fromkeys(list(fe.get("lenient") or []) + ["~rating"]))
        if fe["changes"] and fe["op"] == "none" and fe["dst"] == fe["src"]:
            fe["op"] = "tag"
        auto = fe["auto"]
        if not fe["changes"] and fe["op"] == "tag" and auto.get("changes") and not fe.get("fix_pictures"):
            fe["op"] = "none"                          # (only your edit was to be written, and is undone)
    if isinstance(edit.get(MATCH), dict) and edit[MATCH].get("pairs") is not None:
        _place_by_match(entry, edit[MATCH])
    if edit.get("cover") and edit["cover"].get("image") and Path(edit["cover"]["image"]).exists():
        _edit_cover(entry, edit["cover"], profile)
    embeds = set((entry.get("artwork") or {}).get("embed_into") or []) | \
        set((entry.get("artist_photo") or {}).get("embed_into") or [])
    for fe in entry.get("files") or []:              # (a cover or photo still to embed keeps its track's write)
        if fe["op"] == "none" and fe["dst"] in embeds:
            fe["op"] = "tag"
    entry["edited"] = {"tags": album_tags, "file_tags": edit.get("file_tags") or {},
                       "cover": edit.get("cover")}
    if entry["status"] == "unchanged" and has_work(entry):
        entry["status"] = "ready"
    elif entry["status"] == "ready" and not has_work(entry):
        entry["status"] = "unchanged"
    return entry


def recount(plan: dict) -> dict:
    """plan's summary counted again (after an edit made an album ready or unchanged)."""
    counts = {}
    for a in plan.get("albums") or []:
        counts[a["status"]] = counts.get(a["status"], 0) + 1
    plan["summary"] = counts
    return plan


def as_dict(p: Proposal) -> dict:
    return asdict(p)
