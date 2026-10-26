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

PLAN_VERSION = 1
AUDIO_EXT = ex.AUDIO_EXT
# an album folder holding any of these has its cover already (as CoverUp's writer sees it)
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff", ".bmp", ".jfif")
CUE_LIKE = {".cue", ".m3u", ".m3u8"}
EMBEDDABLE = {".flac", ".mp3", ".m4a", ".mp4", ".aiff", ".aif", ".wav", ".ogg", ".oga", ".opus"}


@dataclass
class Proposal:
    """One album (or single file) as identification left it."""
    source_dir: str
    files: list                         # paths, in track order
    targets: list = field(default_factory=list)   # per file: {field: [values]} (empty when unresolved)
    status: str = "identified"          # identified | unresolved
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
    artist_photo: dict | None = None    # {"image": staged file, "source", "artist"} (Enthusiast and up)


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


def diff_tags(before: dict, target: dict, profile, confidence: float, extra_removals=()):
    """(changes {field: [..] | None}, rows [{field, before, after, action}])."""
    changes, rows = {}, []
    replace_ok = confidence >= float(profile["thresholds"]["replace_min_confidence"])
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


def _other_release_there(album_dir: Path, me: frozenset, ignore: set) -> bool:
    """album_dir already holds music explicitly tagged as a different release: files that
    carry release IDs and share none with this album. Files of this plan (moving away or
    this album's own, under any name) and untagged files never count."""
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


MOUNT_PARENTS = ("/media", "/mnt", "/run/media")


def mount_point(folder) -> str:
    """The mount point folder lies on ("/" when on the system disk)."""
    p = Path(os.path.realpath(os.path.expanduser(str(folder))))
    while p != p.parent and not os.path.ismount(p):
        p = p.parent
    return str(p)


def mounted(folder) -> bool:
    """False for a folder under /media, /mnt or /run/media when nothing is mounted between
    it and that parent: the share it belongs to is not mounted, and anything written there
    would land on the system disk."""
    real = os.path.realpath(os.path.expanduser(str(folder)))
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


SHARED_FOLDER_NOTE = "folder holds other music too; only each track's own files (lyrics ...) go with it"


def _companions(src_dir: Path, moved: dict, profile, shallow=False, tracks_only=False, artist="",
                single=False):
    """([(companion, destination)], why not): the non-audio files that go with an album
    that moves or is copied, as Picard's "move additional files" and foobar2000's "move
    entire source folder content" do:
      - in each folder holding this album's music (the album folder, its disc folders):
        the files whose extension is in the profile's "companions";
      - in folders with no music anywhere inside (Scans, covers, artwork ...): everything;
      - nothing from a folder holding music that is not moving with this album (a mixed
        folder, a bonus album in a subfolder), nor from below it.
    Each goes to the same place relative to where the music lands. Hidden files and
    symbolic links stay. moved: {source file: destination file}."""
    exts = {e.lower() for e in profile["companions"]}
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
    targets = {os.path.realpath(str(x)) for x in dmap.values()}

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
                               single=single)[0], \
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
            if d in music_below and p.suffix.lower() not in exts:
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


def _spelled_names(raw: dict, changes: dict) -> dict:
    """{field: its spelling} of the fields written whose name is not in lower case (a custom
    tag a tagger script made, "MyTag"): Picard writes such a tag as it is spelled."""
    return {tagio.field_name(k): k for k in raw if tagio.field_name(k) != k and tagio.field_name(k) in changes}


def _linked_tracks(entry) -> list:
    """Conflicts for the album's tracks that are symbolic links (a library of links into a
    download folder) and would be moved or written: moving one leaves a copy where the link
    was, and TagMender never writes through a link."""
    out = []
    for fe in entry["files"]:
        s_ = Path(fe.get("copy_of") or fe["src"])
        if s_.is_symlink() and (fe["op"] == "move" or (fe["op"] == "tag" and fe["src"] == str(s_))):
            out.append(f"{s_.name} is a symbolic link (to {os.readlink(s_)}): TagMender does not move or "
                       "write through links - replace it with the file, or use a copy profile")
    return out


def _first_values(raw: dict, changes: dict) -> dict:
    """{field: [its first value]} of the ID3v1 fields written with several values (an
    ID3v2.3 tag joins them; its ID3v1 tag takes the first, as Picard's)."""
    return {k: [str(raw[k][0])] for k in ("title", "artist", "album", "genre")
            if changes.get(k) and len(raw.get(k) or []) > 1}


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
                table[o_s.casefold()] = new_s
                if o_s != old_n:
                    by_bytes.add(o_s.casefold())      # (named by the bytes on disk)
                o, n = os.path.splitext(o_s), os.path.splitext(new_s)
                if o[1].lower() in AUDIO_EXT:
                    stems.setdefault(o[0].casefold(), set()).add(n[0])
    if not table:
        return None

    def swap(name):
        key = name.replace("\\", "/")
        while key.startswith("./"):
            key = key[2:]
        hit = table.get(key.casefold())
        if hit is not None:
            swapped.append((key.casefold() in by_bytes, hit))
        if hit is None:          # EAC's cue names the rip's WAV files: "01 a.wav" for "01 a.flac"
            stem, ext = os.path.splitext(key)
            got = stems.get(stem.casefold()) if ext.lower() in AUDIO_EXT else None
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
    def __init__(self, profile, job_id=None, rules=None, overrides=None, extra_roots=()):
        self.profile = profile
        self.extra_roots = [os.path.realpath(os.path.expanduser(str(r))) for r in extra_roots]
        self.extra_roots = [os.path.dirname(r) if os.path.isfile(r) else r for r in self.extra_roots]
        self.job = job_id or time.strftime("%Y%m%d-%H%M%S-") + os.urandom(2).hex()
        self.rules = ex.load_rules(profile.name if profile.name != "default" else None) if rules is None else rules
        self.overrides = ex.State().overrides() if overrides is None else overrides
        self.claimed = {}          # folded destination path -> source (collisions inside the plan)
        self.created_dirs = set()
        self._dirs = {}
        self._inodes = {}
        self._album_dirs = {}
        self._planned = set()
        self._resident = []

    def _picard(self) -> dict:
        """Picard's settings this profile follows (picard_config.tag_settings), once per plan."""
        if not hasattr(self, "_picard_set"):
            get = getattr(self.profile, "picard_tags", None)
            self._picard_set = get() if get else {}
        return self._picard_set

    def _log(self):
        import logging
        return logging.getLogger("tagmender")

    def _scripts(self) -> list:
        import picard_config
        return picard_config.tagger_scripts(self._picard())

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
        tags = {**{spell.get(k, k): list(v) for k, v in before.items() if not k.startswith("~") and k not in gone_},
                **{k: list(v) if isinstance(v, list) else [v] for k, v in target.items() if not k.startswith("~")}}
        out, gone = _engine.run_tagger_scripts(self._scripts(), tags, hidden, self._log(), strip=False)
        back = {v: k for k, v in spell.items()}
        out = {back.get(k, k): v for k, v in out.items()}
        # ($set(Title,X), $set(GENRE,Jazz) - a field TagMender writes, spelled otherwise: that
        # field, the script's value winning; Picard, case-sensitive, would add a TXXX:Title -
        # docs/PICARD_COVERAGE.md)
        known = {k for k in list(tags) + list(target) if k == k.lower()}
        for k in [k for k in out if k != k.lower() and k.lower() in known and k not in tags]:
            out[k.lower()] = out.pop(k)
        tags = {back.get(k, k): v for k, v in tags.items()}
        # ($delete(custom) is not the file's "CUSTOM": left as it is, as Picard leaves it)
        gone = [back.get(g, g) for g in gone if not (g.lower() in spell and g != spell[g.lower()])]
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
        kept = _levels.restrict(new, self.profile)
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
        return lim

    def _file_settings(self) -> dict:
        """Picard's settings for what saving a file does beside its fields (ID3v2 version,
        ID3v1, ...; see tagio.file_options)."""
        return {k: self._picard().get(k) for k in tagio.FILE_SETTINGS}

    # ---- the plan for one album -------------------------------------------
    def album(self, prop: Proposal) -> dict:
        self._win_album = str(prop.source_dir)
        p = self.profile
        files = [Path(f) for f in prop.files]
        entry = {"source_dir": str(prop.source_dir), "status": "ready", "label": "", "rule": None,
                 "identification": {k: getattr(prop, k) for k in
                                    ("status", "provider", "release_id", "url", "confidence", "margin", "why",
                                     "evidence", "reason_codes", "provenance", "provider_errors")},
                 "release": prop.release, "files": [], "companions": [], "artwork": None,
                 "conflicts": [], "skipped": [], "notes": [],
                 # (every file of the album as found: for a release the user chooses for it)
                 "album_files": sorted({str(x) for x in (getattr(prop, "group_files", None) or prop.files or [])})}
        if prop.status == "unresolved":
            entry["identification"]["reason"] = reasons.encode(prop.reason, prop.reason_codes or None)
        # 1. rules, with what the files say and what identification found
        dec = ex.evaluate(files, self.rules, release=prop.release or None, overrides=self.overrides)
        if dec.excluded:
            whole = dec.summary()
            if whole is not None:
                entry.update(status="excluded", label=whole.label,
                             rule={"id": whole.rule.id, "name": whole.rule.name, "action": whole.action,
                                   "matched": whole.where},
                             excluded_files=[str(f) for f in files])     # (for "process anyway")
                return entry
            for f, e in dec.excluded.items():
                entry["skipped"].append({"file": str(f), "label": e.label, "rule": e.rule.id, "matched": e.where})
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
        organize_ok = identified and conf >= float(p["thresholds"]["organize_min_confidence"])
        op = p["operation"]
        if not identified:
            ua = p["unresolved"]["action"]
            entry["status"] = "unresolved"
            entry["label"] = "Unresolved"
            if ua == "leave" or op == "copy":
                for f in kept:
                    entry["files"].append({"src": str(f), "dst": str(f), "op": "none", "changes": {}, "diff": [],
                                           "fingerprint": _fp(f)})
                return entry
            op = "move" if ua == "move" else "in_place"
        elif op in ("copy", "move") and not organize_ok:
            entry["notes"].append(f"confidence {conf:.1f} is below organize_min_confidence "
                                  f"{p['thresholds']['organize_min_confidence']}: "
                                  + ("not copied" if op == "copy" else "tagged in place, not moved"))
            if op == "copy":
                entry["status"] = "skipped"
                entry["label"] = "Skipped"
                return entry
            op = "in_place"
        if identified and self._scripts():
            # Picard's tagger scripts, on each file (its own tags overlaid with the release's
            # every field, whatever the level then writes); before anything is planned from them
            full = getattr(prop, "full_targets", None)
            full = {Path(f): t for f, t in zip(files, full)} if full and len(full) == len(files) else targets
            import engine as _engine
            try:
                for f in kept:
                    try:
                        before_s = tagio.read(f)
                    except tagio.Unsupported:
                        continue
                    targets[f] = self._scripted(f, before_s, dict(full.get(f) or targets.get(f) or {}))
            except _engine.TaggerScriptError as exc:
                entry["conflicts"].append(f"a tagger script failed on {f.name}: {exc}")
                entry["status"] = "blocked"
                return entry
        # 3. destination root (never created: an unmounted share must not fill the local disk)
        dest_root = None
        if op in ("copy", "move"):
            dest_root = Path(p["unresolved"]["move_to"] if not identified else p["destination"]).expanduser()
            if not dest_root.is_dir():
                entry["status"] = "blocked"
                entry["conflicts"].append(f"destination {dest_root} is not available (unmounted?); "
                                          "TagMender never creates it")
                return entry
            if not mounted(dest_root):
                entry["status"] = "blocked"
                entry["conflicts"].append(f"destination {dest_root} is on the system disk, not on the share "
                                          "mounted there (unmounted?)")
                return entry
            entry["dest_root"] = {"path": str(dest_root), "dev": os.stat(dest_root).st_dev,
                                  "mount": mount_point(dest_root)}
        template = naming.template_for(p["naming"])
        album_dirs = {}
        if op == "copy" and identified:
            album_dirs["prior"] = self._prior_album_dir(kept, dest_root)
        words = int(p["filenames"].get("max_album_words") or 5)
        if not (p["naming"] or {}).get("template", "").strip() and words != 5:
            template = template.replace("$words(%album%,5)", f"$words(%album%,{words})") \
                .replace("$set(_maxwords,5)", f"$set(_maxwords,{words})")
        nm = p["naming"] or {}
        template = naming.with_editions(template, nm.get("editions", "off"))
        # the release's first disc says which edition it is, for every file of it (its media and
        # its date as they will be written): a CD with a 7" bonus disc, or a disc from a later
        # repress, stays in one folder
        folder_mode, album_mode = nm.get("editions", "off"), nm.get("album_editions", "off")
        rel = {}
        if identified and (folder_mode != "off" or album_mode != "off") and kept:
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
        win_split, split = {}, False
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
            # (the file as it will be: without what the release or a script deletes)
            gone_ed = {x for x in (target.get("~remove") or [])
                       if x not in target and p.policy(x) not in ("preserve", "lock", "fill_missing")}
            tag_ed = naming.edition({**{k: v for k, v in before.items() if k not in gone_ed}, **target, **rel_tags},
                                    album_mode) if album_mode != "off" else ""
            if identified and tag_ed and (target.get("album") or [""])[0]:
                album = str(target["album"][0])
                if not album.endswith(f" [{tag_ed}]"):      # "I" -> "I [2019 Vinyl]", once
                    target["album"] = [f"{album} [{tag_ed}]"] + list(target["album"][1:])
            if identified and p["mark_resolved"]:
                target.setdefault("tagmender", ["Resolved"])
                if not before.get("tagmender_reason"):
                    target.setdefault("tagmender_reason", [])
            if not identified and p["unresolved"]["action"] in ("mark", "move"):
                target = {"tagmender": ["Unresolved"], "tagmender_reason": [entry["identification"]["reason"]]}
            removals = ["tagmender_reason"] if identified and before.get("tagmender_reason") and \
                str((before.get("tagmender") or [""])[0]).lower() == "unresolved" else []
            # what Picard would delete (compilation on a release not by Various Artists, what a
            # tagger script $delete-s) - never a field the profile preserves or locks
            removals += [tagio.canonical(f, x) for x in (target.pop("~remove", None) or [])
                         if x not in target and p.policy(x) not in ("preserve", "lock", "fill_missing")
                         and x != "itunes_cddb_1"]      # (Picard's $delete leaves iTunes' own COMM there)
            # Picard's hidden variables (%_multiartist%, what a tagger script $set): for the file
            # naming script only, never written
            hidden = {"_" + k[1:]: v for k, v in {**tagio.file_vars(f), **target}.items()
                      if k.startswith("~") and len(k) > 1 and k != "~remove"}
            target = {k: v for k, v in target.items() if not k.startswith("~")}
            # what the format has a place for, as it will hold it (an ID3v2.3 tag joins values ...)
            whole = target                # (the file's fields as Picard has them for naming, held or not)
            raw = {k: v for k, v in target.items() if tagio.holds(f, k)}
            for num_, tot_ in tagio.ID3_PAIRS:          # (a number removed: its total has no place either)
                if num_ in removals and raw.get(tot_) and num_ not in raw:
                    raw[num_] = None
            fmt = tagio.file_options(f, self._file_settings())       # (Picard: ID3v2 version, ID3v1, ...)
            target = tagio.stored(f, raw, fmt)
            if raw.get("comment") and not target.get("comment") and before.get("comment") and \
                    p.policy("comment") not in ("preserve", "lock", "fill_missing"):
                # (a Vorbis/APEv2 comment "text (desc)" is the described comment: the file's
                # plain comment it replaces goes, as Picard replaces it)
                removals.append("comment")
            changes, rows = diff_tags(before, target, p, conf if identified else 100.0, removals)
            as_is = tagio.fragmented(f)
            named = changes               # (named as its album's other files are, whatever it holds)
            if as_is:                     # (moved or copied with its album as it is, never written to)
                changes, rows = {}, []
                entry["notes"].append(f"{f.name}: a fragmented MP4 - never written to (its audio would no longer "
                                      "play); it goes with its album as it is")
            fe = {"src": str(f), "dst": str(f), "op": "tag" if changes else "none", "changes": changes,
                  "diff": rows, "fingerprint": _fp(f)}
            if as_is:
                fe["as_is"] = True
            if changes and fmt:
                fe["format"] = fmt
            spell = _spelled_names(raw, changes)
            if spell:
                fe["names"] = spell
            if _first_values(raw, changes):
                fe["first"] = _first_values(raw, changes)
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
                fe["riff"] = {k: list(merged[k]) for k in tagio.RIFF_INFO if merged.get(k) and k not in unset}
            if op in ("copy", "move"):
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
                    for k_, sp_ in tagio.spellings(f).items():     # (the file's own TXXX:EDITION as %EDITION%)
                        if k_ in names and k_ not in whole:
                            names[sp_] = names.pop(k_)
                    if folder_mode != "off":
                        # (for the name only: a Picard script working the edition out itself
                        # from %media% and %date% gets the release's first-disc values for every
                        # file; and the album goes in without the edition the Album tag carries -
                        # the folder adds its own)
                        if "$set(_edkind," in template:
                            names.update(rel)
                        al = str((names.get("album") or [""])[0])
                        if tag_ed and al.endswith(f" [{tag_ed}]"):
                            names["album"] = [al[:-len(f" [{tag_ed}]")]]
                    try:
                        comps = naming.render(template, names, self._picard(), p["filenames"].get("compat", "windows"),
                                              own=f.stem)
                    except naming.TemplateError as exc:          # (a custom script that fails here)
                        comps, notes = None, [f"the naming script failed: {exc}"]
                    dst = None
                    if comps is not None:
                        dst, notes = fnm.make_path(dest_root, comps, f.suffix.lower(), self._name_limits(dest_root))
                    made = dst
                    if dst is not None:
                        dst = self._spelled(dst, dest_root)
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
                        fe["riff"] = {k: list(now2[k]) for k in tagio.RIFF_INFO
                                      if now2.get(k) and changes2.get(k, "") is not None}
                    fe.update(src=str(dst), dst=str(dst), op="tag" if changes2 else "none", changes=changes2,
                              diff=rows2, fingerprint=_fp(dst), copy_of=str(f))
                    entry["files"].append(fe)
                    continue
                dst = self._unique(dst, f, entry)
                if dst is None:
                    continue
                fe["dst"] = str(dst)
                if Path(dst) == f or (Path(dst).name == f.name and same_entry(dst, f)):
                    fe["dst"] = str(f)                   # already where the template puts it
                    fe["op"] = "tag" if changes else "none"
                else:
                    fe["op"] = op
                    if Path(dst).exists() and os.path.samefile(dst, f) and not _one_name(dst, f):
                        fe["linked"] = True              # the destination is a hard link to this file
                        if op == "copy" and not changes:
                            fe["op"] = "none"            # already there
            entry["files"].append(fe)
        entry["conflicts"] += _linked_tracks(entry)
        entry["conflicts"] = list(dict.fromkeys(entry["conflicts"]))
        if split:
            # Windows path shortening cut the album's folders differently for its tracks (each
            # by its own path's length: discs with names of different lengths): planned again
            # with the most shortened folders for all of them - one album, one folder
            forced = self.__dict__.setdefault("_win_dirs", {})
            key = str(prop.source_dir)
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
                                      "abbreviations) or set picard.windows_long_paths")
        if entry["conflicts"]:
            entry["status"] = "blocked"
            return entry
        # 4. companion files go with an album that moves or is copied
        moved = [fe for fe in entry["files"] if fe["op"] in ("copy", "move")]
        if moved:
            # every file of the album, those already in place too: their folder is the album's
            pairs = {fe.get("copy_of", fe["src"]): fe["dst"] for fe in entry["files"]}
            src_root = Path(prop.source_dir)
            real_src = os.path.realpath(src_root)
            at_root = real_src in self.library_roots() or (real_src in self.roots() and len(pairs) == 1)
            who, single = _album_artist(prop)
            sort = [x for x in ((prop.targets[0] if prop.targets else {}).get("albumartistsort") or []) if x != who]
            comps, why_not = _companions(src_root, pairs, p, shallow=at_root, tracks_only=at_root,
                                         artist=[who] + sort,
                                         single=single)
            if why_not:
                entry["notes"].append(why_not)
            for c, want in comps:
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
                ce = {"src": str(c), "dst": str(dst), "op": moved[0]["op"] if dst != c else "rewrite",
                      "fingerprint": _fp(c)}
                if why_not == SHARED_FOLDER_NOTE:
                    ce["own"] = True          # (a track's own file, from a folder shared with other music)
                if c.suffix.lower() in CUE_LIKE:
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
            has_image = folder is None or any(d.exists() and any(x.suffix.lower() in IMAGE_EXT for x in d.iterdir())
                                              for d in looks)
            has_image = has_image or any(Path(c["dst"]).parent in looks and Path(c["dst"]).suffix.lower() in
                                         IMAGE_EXT for c in entry["companions"])
            for fe in entry["files"]:
                if fe["dst"] in embed and fe["op"] == "none":   # only the cover is new
                    fe["op"] = "tag" if fe["dst"] == fe["src"] else op
            cover = None if (has_image or not p["artwork"]["files"]) else folder / "cover.jpg"
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
            rf = rep.get("file")
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
                    emb = str(_artwork.embed_copy(Path(art["front"]), ex.STATE_DIR / "staging" / self.job))
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
        linked = _linked_tracks(entry)                 # (a linked track that gets only a cover or photo)
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

    def _release(self, entry, prop):
        """A blocked album gives up what it claimed in this plan: its files' names, its cover
        file, the artist folder's artist.jpg (another album of the artist writes it then)."""
        me = f"artwork:{prop.source_dir}:{sorted(_release_ids(prop))}:{sorted(map(str, prop.files))}"
        mine = {str(x) for x in prop.files} | {fe["src"] for fe in entry["files"]} | {me}
        photos = {fnm.fold(sc["dst"]) for sc in entry.get("sidecars") or [] if sc.get("kind") == "artist"}
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
                cands += [d, d.with_name(d.stem + fnm.collision_suffix(str(src)) + d.suffix)]
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
            ignore = self._planned | self._digests_of(own_files)
            clash = not shared and ((owner is not None and not (owner & me)) or
                                    (owner is None and _other_release_there(album_dir, me, ignore)))
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

    def _unique(self, dst: Path, src: Path, entry):
        """dst, or dst with a stable suffix when another file has (or will have) that name;
        None (with a conflict noted) when even that is taken."""
        # (the suffixed name an earlier run gave this very file, when it was moved: kept - its
        # suffix came from where it was then, and another would rename it on every run)
        own = src if src.parent == dst.parent and src.suffix == dst.suffix and \
            re.fullmatch(re.escape(dst.stem) + r" ~[0-9a-f]{4}", src.stem) else None
        for attempt in range(2):
            cand = dst if attempt == 0 else (own or dst.with_name(
                dst.stem + fnm.collision_suffix(str(src)) + dst.suffix))
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
        counts = {}
        for a in albums:
            counts[a["status"]] = counts.get(a["status"], 0) + 1
        return {"version": PLAN_VERSION, "job": self.job, "created": time.time(), "profile": self.profile.name,
                "profile_data": self.profile.to_dict(), "operation": self.profile["operation"],
                "roots": self.roots(), "library_roots": self.library_roots(), "albums": albums, "summary": counts,
                "discogs_settings": _enrichment_summary(self.profile),
                "preserve_timestamps": self.profile.preserve_timestamps()}

    def library_roots(self):
        """The profile's own folders (not a folder named on the command line)."""
        p = self.profile
        r = [os.path.realpath(os.path.expanduser(s["path"])) for s in p["sources"]]
        for d in (p["destination"], p["unresolved"].get("move_to")):
            if d:
                r.append(os.path.realpath(os.path.expanduser(d)))
        return r

    def roots(self):
        p = self.profile
        r = [os.path.realpath(os.path.expanduser(s["path"])) for s in p["sources"]]
        for d in (p["destination"], p["unresolved"].get("move_to")):
            if d:
                r.append(os.path.realpath(os.path.expanduser(d)))
        return list(dict.fromkeys(r + self.extra_roots))


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
    if plan.get("version") != PLAN_VERSION:
        raise ValueError(f"plan version {plan.get('version')} is not {PLAN_VERSION}")
    return plan


def _enrichment_summary(profile) -> str:
    """One line saying how Discogs data is applied, and where that came from."""
    try:
        import picard_config
        s = profile.enrichment()
        src = (picard_config.source() if profile.get("follow_picard", True)
               else "the plugin's defaults (follow_picard is off)")
        return (f"Discogs Enrichment: merge {s['merge_mode']}, styles {s['style_mode']}, "
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
        head = f"\n[{a['status'].upper()}] {a['source_dir']}"
        if a["status"] == "excluded":
            head += f"\n    {a['label']} by rule '{a['rule']['name']}' (matched {a['rule']['matched']})"
        elif ident.get("status") == "identified":
            head += (f"\n    {ident['provider']} {ident['release_id']}  score {ident['confidence']:.1f}"
                     + (f"  margin {ident['margin']:.1f}" if ident.get("margin") is not None else ""))
            if ident.get("evidence"):
                head += "\n    agreed: " + "; ".join(ident["evidence"][:6])
            if ident.get("why"):
                head += "\n    doubts: " + "; ".join(ident["why"][:4])
        else:
            codes, words = reasons.parse(ident.get("reason", ""))
            head += f"\n    Unresolved: {', '.join(codes)} — {words}"
        if ident.get("provider_errors"):
            head += "\n    provider problems (recorded): " + "; ".join(f"{k}: {v}" for k, v in
                                                                      ident["provider_errors"].items())
        out.append(head)
        for c in a["conflicts"]:
            out.append(f"    CONFLICT: {c}")
        for n in a["notes"]:
            out.append(f"    note: {n}")
        for s in a["skipped"]:
            out.append(f"    {s['label']}: {Path(s['file']).name} ({s['rule']}: {s['matched']})")
        for fe in a["files"]:
            if fe["op"] == "none" and not verbose:
                continue
            move = f"  ->  {fe['dst']}" if fe["dst"] != fe["src"] else ""
            out.append(f"    {fe['op']:<5} {Path(fe['src']).name}{move}")
            if fe.get("format"):
                out.append(f"           saved    {format_note(fe['format'])} (Picard's Options > Tags)")
            for r in fe["diff"]:
                if r["action"] in ("added", "changed", "removed") or verbose:
                    b = "; ".join(r["before"]) or "—"
                    af = "; ".join(r["after"]) or "—"
                    out.append(f"           {r['action']:<9} {r['field']}: {b[:50]} => {af[:50]}")
        for c in a["companions"]:
            note = "" if not c.get("rewrite") else \
                "  (replaced by the larger cover; the old one is kept for undo)" if c["rewrite"].get("cover") \
                else "  (TagMender's downloaded artist photo replaced by yours; kept for undo)" \
                if c["rewrite"].get("artist") else "  (file references updated)"
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


def as_dict(p: Proposal) -> dict:
    return asdict(p)
